from __future__ import annotations

import ast
import hashlib
import io
import json
import math
import re
import subprocess
import sys
import tempfile
import textwrap
import token
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.archive.alpha1.merlo.native_differential import MIRInterpreter
from research.archive.alpha1.merlo.native_hir import compile_native_hir, lower_native_hir_to_performance
from tools.benchmarks.merlo.performance_opt import optimize_mir
from research.archive.alpha1.merlo.semantic_surface import SemanticSurfaceError, compile_semantic_surface


CORPUS_SCHEMA = "merlo.surface-0.2.corpus.v1"
PROTOCOL_SCHEMA = "merlo.surface-0.2.protocol.v1"
CATEGORIES = (
    "numeric",
    "strings",
    "collections",
    "records_data",
    "parsing",
    "business_logic",
    "error_handling",
    "file_data_transformations",
    "algorithms",
    "small_utilities",
)
DEFAULT_ROOT = Path(__file__).resolve().parents[4] / "tools" / "benchmarks" / "merlo" / "benchmarks" / "surface_0_2"
_MERLO_TOKEN = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|\"(?:\\.|[^\"\\])*\"|"
    r"'(?:\\.|[^'\\])*'|==|!=|<=|>=|->|=>|\?\?|<<|>>|\+=|-=|\*=|/=|"
    r"[()\[\]{},:.?+*/%<>=|&^~-])"
)


class SurfaceChallengeError(ValueError):
    pass


@dataclass(frozen=True)
class SurfaceChallengeCase:
    id: str
    category: str
    repository: str
    repository_url: str
    commit: str
    license: str
    license_file: str
    path: str
    qualname: str
    start_line: int
    end_line: int
    python_source: str
    source_sha256: str
    selection_rationale: str
    merlo_source: str | None = None


@dataclass(frozen=True)
class SurfaceChallengeCorpus:
    cases: tuple[SurfaceChallengeCase, ...]
    corpus_sha256: str
    protocol_sha256: str
    root: Path


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _case_payload(case: SurfaceChallengeCase) -> dict[str, Any]:
    return {
        key: value
        for key, value in case.__dict__.items()
        if key != "merlo_source"
    }


def _strip_python_docstring(tokens: list[tokenize.TokenInfo]) -> list[tokenize.TokenInfo]:
    significant = [
        index
        for index, item in enumerate(tokens)
        if item.type not in {
            tokenize.ENCODING,
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.COMMENT,
            tokenize.ENDMARKER,
        }
    ]
    if not significant:
        return tokens
    colon = next(
        (
            index
            for index in significant
            if tokens[index].type == token.OP and tokens[index].string == ":"
        ),
        None,
    )
    if colon is None:
        return tokens
    for index in range(colon + 1, len(tokens)):
        item = tokens[index]
        if item.type in {
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.COMMENT,
        }:
            continue
        if item.type == token.STRING:
            return tokens[:index] + tokens[index + 1 :]
        break
    return tokens


def significant_tokens(source: str, *, language: str) -> tuple[str, ...]:
    if language == "merlo":
        return tuple(_MERLO_TOKEN.findall(source))
    if language != "python":
        raise ValueError(f"unsupported token language: {language}")
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (IndentationError, tokenize.TokenError) as exc:
        raise SurfaceChallengeError(f"invalid Python source: {exc}") from exc
    tokens = _strip_python_docstring(tokens)
    ignored = {
        tokenize.ENCODING,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.COMMENT,
        tokenize.ENDMARKER,
    }
    return tuple(item.string for item in tokens if item.type not in ignored)


def quantile(values: Sequence[float], q: float) -> float:
    if not values:
        raise ValueError("quantile requires samples")
    if not 0 < q <= 1:
        raise ValueError("quantile must be in (0, 1]")
    ordered = sorted(float(value) for value in values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _protocol_hash(root: Path) -> str:
    path = root / "protocol.json"
    if not path.is_file():
        raise SurfaceChallengeError("missing protocol lock")
    try:
        protocol = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SurfaceChallengeError(f"invalid protocol lock: {exc}") from exc
    if protocol.get("schema") != PROTOCOL_SCHEMA:
        raise SurfaceChallengeError("unsupported protocol schema")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_locked_corpus(corpus: SurfaceChallengeCorpus) -> None:
    if len(corpus.cases) != 100:
        raise SurfaceChallengeError("corpus must contain exactly 100 cases")
    identifiers = [case.id for case in corpus.cases]
    if len(set(identifiers)) != len(identifiers):
        raise SurfaceChallengeError("duplicate corpus case identity")
    counts = {
        category: sum(case.category == category for case in corpus.cases)
        for category in CATEGORIES
    }
    if counts != {category: 10 for category in CATEGORIES}:
        raise SurfaceChallengeError(f"category distribution mismatch: {counts}")
    expected_protocol = _protocol_hash(corpus.root)
    if corpus.protocol_sha256 != expected_protocol:
        raise SurfaceChallengeError("protocol hash mismatch")
    for case in corpus.cases:
        if case.merlo_source is not None:
            raise SurfaceChallengeError(f"{case.id}: freeze contains a Merlo translation")
        if len(case.commit) != 40 or not re.fullmatch(r"[0-9a-f]{40}", case.commit):
            raise SurfaceChallengeError(f"{case.id}: invalid immutable revision")
        if not case.python_source.endswith("\n"):
            raise SurfaceChallengeError(f"{case.id}: source is not newline terminated")
        if hashlib.sha256(case.python_source.encode()).hexdigest() != case.source_sha256:
            raise SurfaceChallengeError(f"{case.id}: source hash mismatch")
        if any(part in {"tests", "test", "benchmarks", "benchmark"} for part in Path(case.path).parts):
            raise SurfaceChallengeError(f"{case.id}: non-production source path")
        if not 3 <= case.end_line - case.start_line + 1 <= 40:
            raise SurfaceChallengeError(f"{case.id}: source span outside preregistered size")
        if not (corpus.root / "licenses" / case.license_file).is_file():
            raise SurfaceChallengeError(f"{case.id}: missing license notice")
    unsigned = {
        "schema": CORPUS_SCHEMA,
        "protocol_sha256": corpus.protocol_sha256,
        "cases": [_case_payload(case) for case in corpus.cases],
    }
    if hashlib.sha256(_canonical(unsigned)).hexdigest() != corpus.corpus_sha256:
        raise SurfaceChallengeError("corpus hash mismatch")


def load_locked_corpus(path: str | Path | None = None) -> SurfaceChallengeCorpus:
    root = DEFAULT_ROOT if path is None else Path(path)
    if root.is_file():
        corpus_path = root
        root = root.parent
    else:
        corpus_path = root / "corpus.json"
    try:
        payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SurfaceChallengeError(f"invalid corpus lock: {exc}") from exc
    if payload.get("schema") != CORPUS_SCHEMA or not isinstance(payload.get("cases"), list):
        raise SurfaceChallengeError("unsupported corpus schema")
    try:
        cases = tuple(SurfaceChallengeCase(**item) for item in payload["cases"])
        corpus = SurfaceChallengeCorpus(
            cases,
            str(payload["corpus_sha256"]),
            str(payload["protocol_sha256"]),
            root,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SurfaceChallengeError("malformed corpus lock") from exc
    validate_locked_corpus(corpus)
    return corpus

def measure_surface_compression(
    corpus: SurfaceChallengeCorpus,
    translations: Mapping[str, str],
) -> dict[str, Any]:
    """Measure only preregistered lexical and annotation claims.

    Semantic execution and compiler-equivalence gates are deliberately separate;
    compact text cannot count as semantic evidence.
    """

    validate_locked_corpus(corpus)
    expected_ids = {case.id for case in corpus.cases}
    actual_ids = set(translations)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        raise SurfaceChallengeError(
            "translation identities mismatch: "
            f"missing={missing} extra={extra}"
        )
    ratios: list[float] = []
    observations: list[dict[str, Any]] = []
    zero_annotation_private = 0
    forbidden = {
        "any_dynamic": 0,
        "lifetime_annotations": 0,
        "manual_memory": 0,
    }
    for case in corpus.cases:
        source = translations[case.id]
        if not isinstance(source, str) or not source.strip():
            raise SurfaceChallengeError(
                f"{case.id}: translation must be non-empty text"
            )
        python_count = len(
            significant_tokens(case.python_source, language="python")
        )
        merlo_tokens = significant_tokens(source, language="merlo")
        merlo_count = len(merlo_tokens)
        ratio = merlo_count / python_count
        ratios.append(ratio)
        signature = next(
            (
                line.strip()
                for line in source.splitlines()
                if line.strip()
            ),
            "",
        )
        parameter_text = (
            signature.split("(", 1)[1].split(")", 1)[0]
            if "(" in signature and ")" in signature
            else ""
        )
        zero_annotation = (
            "export " not in signature
            and ":" not in parameter_text
            and "->" not in signature
        )
        zero_annotation_private += int(zero_annotation)
        any_dynamic = sum(token_value == "Any" for token_value in merlo_tokens)
        lifetime_annotations = len(
            re.findall(r"&\s*'[A-Za-z_]\w*", source)
        )
        manual_memory = len(
            re.findall(
                r"\b(?:malloc|calloc|realloc|free|delete|new_unchecked)\b",
                source,
            )
        )
        forbidden["any_dynamic"] += any_dynamic
        forbidden["lifetime_annotations"] += lifetime_annotations
        forbidden["manual_memory"] += manual_memory
        observations.append(
            {
                "id": case.id,
                "category": case.category,
                "python_tokens": python_count,
                "merlo_tokens": merlo_count,
                "ratio": ratio,
                "zero_annotation_private": zero_annotation,
            }
        )
    median = quantile(ratios, 0.50)
    p75 = quantile(ratios, 0.75)
    longer_fraction = sum(value > 1.0 for value in ratios) / len(ratios)
    zero_fraction = zero_annotation_private / len(corpus.cases)
    gates = {
        "median_ratio_at_most_0_70": median <= 0.70,
        "p75_ratio_at_most_0_85": p75 <= 0.85,
        "merlo_longer_fraction_at_most_0_15": longer_fraction <= 0.15,
        "zero_annotation_private_at_least_0_90": zero_fraction >= 0.90,
        "any_dynamic_zero": forbidden["any_dynamic"] == 0,
        "lifetime_annotations_zero": forbidden["lifetime_annotations"] == 0,
        "manual_memory_zero": forbidden["manual_memory"] == 0,
    }
    return {
        "corpus_count": len(corpus.cases),
        "corpus_sha256": corpus.corpus_sha256,
        "protocol_sha256": corpus.protocol_sha256,
        "category_counts": {
            category: sum(case.category == category for case in corpus.cases)
            for category in CATEGORIES
        },
        "ratios": {
            "median": median,
            "p75": p75,
            "merlo_longer_fraction": longer_fraction,
        },
        "zero_annotation_private_fraction": zero_fraction,
        "forbidden": forbidden,
        "observations": observations,
        "gates": gates,
        "passed": all(gates.values()),
    }


def _translation_file(root: Path) -> Path:
    path = root / "translations.json"
    if not path.is_file():
        raise SurfaceChallengeError(f"missing translation lock: {path}")
    return path


def _load_translation_lock(
    root: Path,
    corpus: SurfaceChallengeCorpus,
) -> tuple[dict[str, dict[str, Any]], str]:
    path = _translation_file(root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SurfaceChallengeError(f"invalid translation lock: {exc}") from exc
    if not isinstance(payload, dict):
        raise SurfaceChallengeError("translation lock must be a JSON object")
    if payload.get("schema") != "merlo.surface-0.2.translations.v1":
        raise SurfaceChallengeError("unsupported translation schema")
    if payload.get("corpus_sha256") != corpus.corpus_sha256:
        raise SurfaceChallengeError("translation corpus hash mismatch")
    if payload.get("protocol_sha256") != corpus.protocol_sha256:
        raise SurfaceChallengeError("translation protocol hash mismatch")
    translations = payload.get("translations")
    if not isinstance(translations, dict):
        raise SurfaceChallengeError("translation lock must contain a mapping")
    expected_ids = {case.id for case in corpus.cases}
    actual_ids = set(translations)
    if actual_ids != expected_ids:
        raise SurfaceChallengeError(
            "translation identities mismatch: "
            f"missing={sorted(expected_ids - actual_ids)} "
            f"extra={sorted(actual_ids - expected_ids)}"
        )
    result: dict[str, dict[str, Any]] = {}
    for case in corpus.cases:
        item = translations[case.id]
        if not isinstance(item, dict):
            raise SurfaceChallengeError(f"{case.id}: translation record is not an object")
        for field in ("merlo_source", "manual_canonical_source", "fixture", "expected"):
            if field not in item:
                raise SurfaceChallengeError(f"{case.id}: missing translation field {field}")
        if not isinstance(item["merlo_source"], str) or not item["merlo_source"].strip():
            raise SurfaceChallengeError(f"{case.id}: empty Merlo translation")
        if not isinstance(item["manual_canonical_source"], str) or not item[
            "manual_canonical_source"
        ].strip():
            raise SurfaceChallengeError(f"{case.id}: empty canonical contract")
        if not isinstance(item["fixture"], dict) or not isinstance(item["expected"], dict):
            raise SurfaceChallengeError(f"{case.id}: malformed fixture or expected result")
        result[case.id] = item
    return result, hashlib.sha256(path.read_bytes()).hexdigest()


def _python_ast(source: str) -> ast.AST | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _merlo_ast(source: str) -> ast.AST:
    converted = re.sub(r"(?m)^(\s*)fn\s+", r"\1def ", source)
    converted = re.sub(r"(?m)^\s*(?:let|var)\s+", lambda match: match.group(0).replace("let ", "").replace("var ", ""), converted)
    converted = re.sub(
        r"\b([A-Za-z_]\w*)\s+in\s+(-?\d+)\.\.(-?\d+)",
        r"\1 in range(\2, \3)",
        converted,
    )
    converted = re.sub(r"\btrue\b", "True", converted)
    converted = re.sub(r"\bfalse\b", "False", converted)
    try:
        return ast.parse(converted)
    except SyntaxError as exc:
        raise SurfaceChallengeError(f"invalid Merlo translation: {exc}") from exc


def _semantic_constructs(tree: ast.AST | None) -> dict[str, int]:
    counts = {
        "branch": 0,
        "loop": 0,
        "call": 0,
        "binding": 0,
        "error-propagation": 0,
        "collection-transform": 0,
        "match": 0,
    }
    if tree is None:
        return counts
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.IfExp)):
            counts["branch"] += 1
        elif isinstance(node, (ast.For, ast.While)):
            counts["loop"] += 1
        elif isinstance(node, ast.Call):
            counts["call"] += 1
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            counts["binding"] += 1
        elif isinstance(node, (ast.Try, ast.Raise, ast.Assert, ast.With)):
            counts["error-propagation"] += 1
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            counts["collection-transform"] += 1
        elif isinstance(node, getattr(ast, "Match", ast.If)):
            counts["match"] += 1
    return counts


_CATEGORY_BLOCKERS = {
    "numeric": "numeric helpers require repository globals and non-UInt64 scalar inputs",
    "strings": "string helpers require Text/string runtime semantics",
    "collections": "collection helpers require list/iterator runtime semantics",
    "records_data": "record helpers require package record and metadata adapters",
    "parsing": "parsers require Text and parser-state semantics",
    "business_logic": "business helpers require package URL/configuration adapters",
    "error_handling": "error helpers require exception and context-manager semantics",
    "file_data_transformations": "file helpers require filesystem/stream effect adapters",
    "algorithms": "algorithm helpers require collection and callable adapters",
    "small_utilities": "utility helpers require their original Python object protocols",
}


def _semantic_blocker(category: str) -> str:
    return _CATEGORY_BLOCKERS.get(category, "locked semantics are not expressible by this adapter")

def _construct_total(constructs: Mapping[str, int]) -> int:
    return sum(int(value) for value in constructs.values())


def _run_python_fixture(
    case_dir: Path,
    case_id: str,
    python_source: str,
    qualname: str,
    fixture: Mapping[str, Any],
) -> dict[str, Any]:
    arguments = fixture.get("arguments")
    if not isinstance(arguments, list):
        raise SurfaceChallengeError(f"{case_id}: fixture arguments are missing")
    source_literal = json.dumps(textwrap.dedent(python_source))
    qualname_literal = json.dumps(qualname)
    script = (
        "import json, sys, textwrap\n"
        f"source = json.loads({source_literal!r})\n"
        f"qualname = json.loads({qualname_literal!r})\n"
        "payload = json.load(sys.stdin)\n"
        "namespace = {'__name__': '__surface_fixture__'}\n"
        "exec(textwrap.dedent(source), namespace)\n"
        "target = namespace[qualname.split('.')[-1]]\n"
        "try:\n"
        "    value = target(*payload['arguments'], **payload.get('kwargs', {}))\n"
        "    observation = {'status': 'OK', 'return_value': value,\n"
        "                   'error_kind': None, 'effect_trace': []}\n"
        "except BaseException as exc:\n"
        "    observation = {'status': 'ERROR', 'return_value': None,\n"
        "                   'error_kind': type(exc).__name__, 'effect_trace': []}\n"
        "def _json_default(value):\n"
        "    if callable(value):\n"
        "        return {'kind': 'callable', 'name': getattr(value, '__qualname__', type(value).__name__)}\n"
        "    return {'kind': 'object', 'type': type(value).__name__}\n"
        "print(json.dumps(observation, default=_json_default, sort_keys=True))\n"
    )
    script_path = case_dir / "python_arm.py"
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        (sys.executable, str(script_path)),
        input=json.dumps(dict(fixture)),
        capture_output=True,
        text=True,
        cwd=case_dir,
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        raise SurfaceChallengeError(
            f"{case_id}: Python arm failed: {completed.stderr.strip()}"
        )
    try:
        observation = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SurfaceChallengeError(f"{case_id}: invalid Python arm output") from exc
    if not isinstance(observation, dict):
        raise SurfaceChallengeError(f"{case_id}: Python arm output is not an object")
    return observation


def _mir_equivalence_payload(mir: Any) -> Any:
    payload = json.loads(mir.to_json())
    if isinstance(payload, dict):
        payload.pop("source_sha256", None)
    return payload

def _run_merlo_translation(
    case_id: str,
    case_dir: Path,
    source: str,
    manual_canonical_source: str,
    fixture: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_path = case_dir / "translation.mlo"
    canonical_path = case_dir / "manual_canonical.mlo"
    source_path.write_text(source, encoding="utf-8")
    canonical_path.write_text(manual_canonical_source, encoding="utf-8")
    compilation = compile_semantic_surface(
        source,
        path=str(source_path),
        entry_function="main",
    )
    canonical_source = compilation.elaborated.canonical_source
    canonical_hash = hashlib.sha256(canonical_source.encode("utf-8")).hexdigest()
    manual_hash = hashlib.sha256(
        manual_canonical_source.encode("utf-8")
    ).hexdigest()
    canonical_equal = canonical_source == manual_canonical_source
    if not canonical_equal:
        raise SurfaceChallengeError(
            f"{case_id}: canonical contract mismatch "
            f"expected={manual_hash} actual={canonical_hash}"
        )
    manual_hir = compile_native_hir(
        manual_canonical_source,
        path=str(source_path),
        entry_function="main",
    )
    manual_mir = lower_native_hir_to_performance(manual_hir)
    manual_optimized, _ = optimize_mir(manual_mir)
    arguments = fixture.get("arguments")
    if not isinstance(arguments, list):
        raise SurfaceChallengeError(f"{case_id}: fixture arguments are missing")
    observation = MIRInterpreter(compilation.mir).run(arguments).to_dict()
    optimized = MIRInterpreter(compilation.optimized_mir).run(arguments).to_dict()
    optimized_mir_equal = (
        _mir_equivalence_payload(compilation.optimized_mir)
        == _mir_equivalence_payload(manual_optimized)
    )
    fusion_evidence = any(
        snapshot.statistics.loops_fused > 0
        for snapshot in compilation.optimization_snapshots
    )
    return (
        observation,
        {
            "canonical_source_sha256": canonical_hash,
            "manual_canonical_sha256": manual_hash,
            "canonical_equal": canonical_equal,
            "optimized_mir_sha256": hashlib.sha256(
                compilation.optimized_mir.to_json().encode("utf-8")
            ).hexdigest(),
            "manual_optimized_mir_sha256": hashlib.sha256(
                manual_optimized.to_json().encode("utf-8")
            ).hexdigest(),
            "optimized_mir_equal": optimized_mir_equal,
            "fusion_evidence": fusion_evidence,
            "optimization_passes": [
                snapshot.statistics.to_dict()
                for snapshot in compilation.optimization_snapshots
            ],
            "optimized_observation": optimized,
        },
    )


def _persist_surface_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SurfaceChallengeError(f"invalid existing report: {exc}") from exc
        if (
            previous.get("corpus_sha256") != report.get("corpus_sha256")
            or previous.get("protocol_sha256") != report.get("protocol_sha256")
        ):
            raise SurfaceChallengeError(
                "refusing to overwrite report with different corpus/protocol hashes"
            )
    path.write_text(payload, encoding="utf-8")


def run_surface_challenge(
    root: str | Path = ".",
    *,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Execute every locked Surface 0.2 case and return its evidence report."""
    candidate = Path(root)
    if (candidate / "benchmarks" / "surface_0_2").is_dir():
        challenge_root = candidate / "benchmarks" / "surface_0_2"
    elif candidate.name == "surface_0_2" and candidate.is_dir():
        challenge_root = candidate
    else:
        challenge_root = candidate
    corpus = load_locked_corpus(challenge_root)
    translations, translation_sha256 = _load_translation_lock(challenge_root, corpus)
    translation_sources = {
        case_id: item["merlo_source"]
        for case_id, item in translations.items()
    }
    compression = measure_surface_compression(corpus, translation_sources)
    failures: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    python_construct_totals: list[int] = []
    merlo_construct_totals: list[int] = []
    with tempfile.TemporaryDirectory(prefix="merlo-surface-0.2-") as workspace:
        workspace_path = Path(workspace)
        for case in corpus.cases:
            item = translations[case.id]
            fixture = item["fixture"]
            record: dict[str, Any] = {
                "id": case.id,
                "category": case.category,
                "repository": case.repository,
                "repository_url": case.repository_url,
                "commit": case.commit,
                "license": case.license,
                "license_file": case.license_file,
                "path": case.path,
                "qualname": case.qualname,
                "start_line": case.start_line,
                "end_line": case.end_line,
                "source_sha256": case.source_sha256,
                "semantic_family": item.get("semantic_family"),
                "blocker": _semantic_blocker(case.category),
                "executed": False,
                "python_executed": False,
                "merlo_executed": False,
                "result_equal": False,
                "canonical_equal": False,
                "optimized_mir_equal": False,
                "fusion": case.category == "file_data_transformations",
                "diagnostics": [],
            }
            case_dir = workspace_path / case.id
            case_dir.mkdir()
            try:
                python_observation = _run_python_fixture(
                    case_dir,
                    case.id,
                    case.python_source,
                    case.qualname,
                    fixture,
                )
                record["python_executed"] = True
                merlo_observation, equivalence = _run_merlo_translation(
                    case.id,
                    case_dir,
                    item["merlo_source"],
                    item["manual_canonical_source"],
                    fixture,
                )
                record["fusion"] = bool(equivalence["fusion_evidence"])
                record["merlo_executed"] = True
                record["python_observation"] = python_observation
                record["merlo_observation"] = merlo_observation
                record["optimized_observation"] = equivalence.pop(
                    "optimized_observation"
                )
                record.update(equivalence)
                expected = item["expected"]
                if expected.get("derive_from_python") is True:
                    expected = {
                        "status": python_observation.get("status"),
                        "return_value": python_observation.get("return_value"),
                        "error_kind": python_observation.get("error_kind"),
                        "effect_trace": python_observation.get("effect_trace", []),
                    }
                expected_return = expected.get("return_value")
                expected_status = expected.get("status")
                expected_error = expected.get("error_kind")
                expected_effects = expected.get("effect_trace", [])
                record["expected"] = expected
                record["result_equal"] = (
                    python_observation.get("status")
                    == merlo_observation.get("status")
                    == expected_status
                    and python_observation.get("return_value")
                    == merlo_observation.get("return_value")
                    == expected_return
                    and python_observation.get("error_kind")
                    == merlo_observation.get("error_kind")
                    == expected_error
                    and python_observation.get("effect_trace")
                    == merlo_observation.get("effect_trace")
                    == expected_effects
                )
                record["executed"] = True
                if not record["result_equal"]:
                    raise SurfaceChallengeError(
                        f"{case.id}: observable result mismatch; "
                        f"locked Python qualname {case.qualname!r} produced "
                        f"{python_observation.get('status')}/"
                        f"{python_observation.get('error_kind')}, while the "
                        f"Surface family produced {merlo_observation.get('status')}/"
                        f"{merlo_observation.get('error_kind')}; "
                        f"blocker={_semantic_blocker(case.category)}"
                    )
            except (
                OSError,
                subprocess.SubprocessError,
                SemanticSurfaceError,
                SurfaceChallengeError,
                ValueError,
            ) as exc:
                message = str(exc)
                record["diagnostics"].append(message)
                failures.append({"id": case.id, "diagnostic": message})
            python_tree = _python_ast(case.python_source)
            try:
                merlo_tree = _merlo_ast(item["manual_canonical_source"])
            except SurfaceChallengeError as exc:
                record["diagnostics"].append(str(exc))
                failures.append({"id": case.id, "diagnostic": str(exc)})
                merlo_tree = None
            python_constructs = _semantic_constructs(python_tree)
            merlo_constructs = _semantic_constructs(merlo_tree)
            record["python_constructs"] = python_constructs
            record["merlo_constructs"] = merlo_constructs
            python_construct_totals.append(_construct_total(python_constructs))
            merlo_construct_totals.append(_construct_total(merlo_constructs))
            records.append(record)
    semantic_gate = all(
        item["python_executed"] and item["merlo_executed"] and item["result_equal"]
        for item in records
    )
    canonical_gate = all(item["canonical_equal"] for item in records)
    optimized_gate = all(item["optimized_mir_equal"] for item in records)
    fusion_gate = all(
        item["fusion"] for item in records if item["category"] == "file_data_transformations"
    )
    construct_ratios = [
        float("inf") if python == 0 and merlo > 0
        else 1.0 if python == 0
        else merlo / python
        for python, merlo in zip(
            python_construct_totals, merlo_construct_totals, strict=True
        )
    ]
    zero_construct_regression = any(
        python == 0 and merlo > 0
        for python, merlo in zip(
            python_construct_totals, merlo_construct_totals, strict=True
        )
    )
    construct_median = quantile(construct_ratios, 0.50)
    construct_p75 = quantile(construct_ratios, 0.75)
    gates = {
        **compression["gates"],
        "exact_case_accounting": [item["id"] for item in records]
        == [case.id for case in corpus.cases],
        "all_case_observations": len(records) == len(corpus.cases),
        "no_failed_case_ids": not failures,
        "python_and_merlo_executed": semantic_gate,
        "observable_result_equality": semantic_gate,
        "canonical_ast_equality": canonical_gate,
        "optimized_mir_equality": optimized_gate,
        "canonical_hash_equality": canonical_gate,
        "zero_construct_regressions_absent": not zero_construct_regression,
        "optimized_mir_hash_equality": optimized_gate,
        "pipeline_fusion": fusion_gate,
        "normalized_construct_median_no_worse": construct_median <= 1.0,
        "normalized_construct_p75_no_worse": construct_p75 <= 1.0,
        "repository_count": len({case.repository for case in corpus.cases}) == 5,
    }
    report = {
        "status": (
            "MERLO_SURFACE_0_2_SUPPORTED"
            if not failures and all(gates.values())
            else "MERLO_SURFACE_0_2_UNSUPPORTED"
        ),
        "translation_digest": translation_sha256,
        "corpus_sha256": corpus.corpus_sha256,
        "protocol_sha256": corpus.protocol_sha256,
        "translation_sha256": translation_sha256,
        "corpus_count": compression["corpus_count"],
        "category_counts": compression["category_counts"],
        "repository_count": len({case.repository for case in corpus.cases}),
        "ratios": compression["ratios"],
        "zero_annotation_private_fraction": compression[
            "zero_annotation_private_fraction"
        ],
        "forbidden": compression["forbidden"],
        "construct_ratios": {
            "median": construct_median,
            "p75": construct_p75,
        },
        "observations": records,
        "failed_case_ids": [item["id"] for item in failures],
        "blocked_case_ids": [item["id"] for item in failures],
        "blockers": {
            item["id"]: item["diagnostic"] for item in failures
        },
        "failures": failures,
        "gates": gates,
    }
    destination = (
        Path(report_path)
        if report_path is not None
        else challenge_root.parent / "merlo_surface_0_2.json"
    )
    _persist_surface_report(destination, report)
    return report


__all__ = [
    "CATEGORIES",
    "SurfaceChallengeCase",
    "SurfaceChallengeCorpus",
    "SurfaceChallengeError",
    "measure_surface_compression",
    "load_locked_corpus",
    "quantile",
    "run_surface_challenge",
    "significant_tokens",
    "validate_locked_corpus",
]
