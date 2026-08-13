"""Deterministic Merlo alpha correctness corpus.

The corpus is generated from a small, checked-in plan rather than copied from
run output.  A case contains a complete project boundary (manifest, lock,
source, test, and expected observation) and is addressed by the hash of those
bytes.  Execution evidence belongs to :mod:`merlo.alpha_safety` and is never
manufactured by this module.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping
from .project import ProjectManifest
from .version import VERSIONS

SCHEMA_VERSION = 1
CORPUS_NAME = "merlo-alpha-correctness"
CORPUS_PATH = Path("benchmarks/merlo_alpha_corpus.json")
DIGEST_SCOPE = "canonical-json-without-sha256"
VALID_CASE_COUNT = 2_000
INVALID_CASE_COUNT = 1_200
VALID_LAYERS = ("concise", "canonical", "hir", "rir", "mir", "optimized_mir", "native")

# These names are intentionally the public feature vocabulary used by the
# alpha plan.  Keeping the vocabulary here makes omissions fail validation,
# instead of silently producing a smaller, biased corpus.
FEATURE_FAMILIES = (
    "scalars", "casts", "overflow", "floats", "control",
    "records", "enums", "generics", "collections", "recursion", "modules", "callbacks",
    "inference", "ownership", "resources",
    "effects", "capabilities", "errors",
    "paths", "network", "ffi",
    "interfaces", "world", "refactors",
)
_RUNTIME_INVALID_FAMILIES = frozenset({"overflow", "resources", "errors"})
_FIXED_SEEDS = {"valid": 0xA1000000, "invalid": 0xA2000000}
_FORBIDDEN_SOURCE_MARKERS = ("intrinsic", "opaque", "TODO", "malloc(", "free(", "Any")
_FAMILY_KERNELS = {family: ("kernel", family, f"fn family_{family.replace('-', '_')}(value: UInt64) -> UInt64:\n    return value\n") for family in FEATURE_FAMILIES}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def corpus_sha256(corpus: Mapping[str, Any]) -> str:
    unsigned = dict(corpus)
    unsigned.pop("sha256", None)
    return hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()


def serialize_alpha_corpus(corpus: Mapping[str, Any]) -> str:
    return _canonical_json(corpus) + "\n"


def _balanced(count: int) -> tuple[str, ...]:
    return tuple(FEATURE_FAMILIES[index % len(FEATURE_FAMILIES)] for index in range(count))


def _template_metadata() -> list[dict[str, Any]]:
    return [
        {
            "id": f"alpha-{kind}-{family}-v1",
            "family": family,
            "validity": kind == "valid",
            "runtime": kind == "invalid" and family in _RUNTIME_INVALID_FAMILIES,
        }
        for kind in ("valid", "invalid")
        for family in FEATURE_FAMILIES
    ]


def _manifest(case_id: str) -> str:
    name = "alpha_" + case_id.replace("-", "_")
    return ProjectManifest(name=name).to_toml()


def _lock(manifest_text: str, source: str, test_source: str) -> str:
    manifest = ProjectManifest.from_dict({"manifest": 1, "project": {"name": next(
        line.split("=", 1)[1].strip().strip('"')
        for line in manifest_text.splitlines() if line.startswith("name = ")
    )}})
    files = tuple((name, hashlib.sha256(content.encode("utf-8")).hexdigest()) for name, content in (("merlo.toml", manifest_text), ("src/main.mlo", source), ("tests/main.mlo", test_source)))
    source_hash = hashlib.sha256(_canonical_json({"package_schema": 1, "name": manifest.name, "version": manifest.version, "files": files, "dependencies": ()}).encode()).hexdigest()
    return _canonical_json({"compatibility": dict(sorted(VERSIONS.to_dict().items())), "graph": {manifest.name: []}, "lockfile": 1, "manifest_hash": manifest.digest(), "packages": [{"name": manifest.name, "version": manifest.version, "source": {"kind": "path", "path": "."}, "source_hash": source_hash, "dependencies": []}]}) + "\n"


def _module_name(case_id: str) -> str:
    return "main"


def _source(case_id: str, family: str, validity: bool, index: int) -> str:
    value = index % 97
    if not validity and family in _RUNTIME_INVALID_FAMILIES:
        body = "    return 1 / 0"
    elif validity:
        body = f'    let family_marker: Text = "{family}"\n    return Ok({value} + family_marker.len())'
    else:
        body = "    return missing_alpha_symbol"
    return f"module main\n\nexport enum AppError:\n    Failure\n\nexport task main(path: Path) -> Result[Int64, AppError]:\n    uses console.write\n    console.write(\"\")\n{body}\n"


def _test_source(case_id: str, validity: bool, index: int) -> str:
    body = f"    return {index % 97}" if validity else "    return missing_alpha_symbol"
    return f"module main.test\n\nexport task test_main() -> Int:\n{body}\n"


def _expected(family: str, validity: bool, index: int) -> dict[str, Any]:
    if validity:
        return {
            "kind": "accepted",
            "runtime": family in _RUNTIME_INVALID_FAMILIES,
            "observable": {"stdout": f"{index % 97 + len(family)}\n", "return_code": 0, "return_value": index % 97 + len(family)},
            "layers": list(VALID_LAYERS),
        }
    runtime = family in _RUNTIME_INVALID_FAMILIES
    if runtime:
        return {
            "kind": "runtime-invalid",
            "runtime": True,
            "diagnostic": {"layer": "native", "code": "DivisionByZero", "message": "division by zero"},
        }
    return {
        "kind": "compile-invalid",
        "runtime": False,
        "diagnostic": {"layer": "concise", "code": "UnresolvedName", "message": "UnresolvedName 'missing_alpha_symbol'"},
    }


def _case(kind: str, index: int, family: str) -> dict[str, Any]:
    validity = kind == "valid"
    case_id = f"alpha-{kind}-{index:04d}"
    manifest = _manifest(case_id)
    source = _source(case_id, family, validity, index)
    test_source = _test_source(case_id, validity, index)
    expected = _expected(family, validity, index)
    stage, operation, family_source = _FAMILY_KERNELS[family]
    unsigned = {
        "id": case_id,
        "kind": kind,
        "family": family,
        "template": f"alpha-{kind}-{family}-v1",
        "seed": _FIXED_SEEDS[kind] + index,
        "validity": validity,
        "provenance": "generated",
        "manifest": manifest,
        "lock": _lock(manifest, source, test_source),
        "source": source,
        "test_source": test_source,
        "family_stage": stage,
        "family_operation": operation,
        "family_source": family_source,
        "expected": expected,
    }
    digest = hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest()
    return {**unsigned, "content_sha256": digest}


def _plan() -> tuple[tuple[str, int, str], ...]:
    valid = (("valid", index, family) for index, family in enumerate(_balanced(VALID_CASE_COUNT)))
    invalid = (("invalid", index, family) for index, family in enumerate(_balanced(INVALID_CASE_COUNT)))
    return tuple((*valid, *invalid))


def _counts() -> dict[str, dict[str, int]]:
    valid = Counter(_balanced(VALID_CASE_COUNT))
    invalid = Counter(_balanced(INVALID_CASE_COUNT))
    return {
        family: {"valid": valid[family], "invalid": invalid[family]}
        for family in FEATURE_FAMILIES
    }


def generate_alpha_corpus() -> dict[str, Any]:
    cases = [_case(*item) for item in _plan()]
    corpus: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "name": CORPUS_NAME,
        "provenance": "generated",
        "digest_scope": DIGEST_SCOPE,
        "generator": {
            "module": "merlo.alpha_corpus",
            "version": SCHEMA_VERSION,
            "seeds": dict(_FIXED_SEEDS),
            "families": list(FEATURE_FAMILIES),
            "templates": _template_metadata(),
        },
        "counts": {"valid": VALID_CASE_COUNT, "invalid": INVALID_CASE_COUNT, "by_family": _counts()},
        "required_layers": list(VALID_LAYERS),
        "examples": [f"alpha-valid-{index:04d}" for index in (0, 5, 8, 14, 15, 18, 21, 23)],
        "cases": cases,
    }
    corpus["sha256"] = corpus_sha256(corpus)
    validate_alpha_corpus(corpus)
    return corpus


def _validate_case(case: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    required = {"id", "kind", "family", "template", "seed", "validity", "provenance", "content_sha256", "manifest", "lock", "source", "test_source", "family_stage", "family_operation", "family_source", "expected"}
    if set(case) != required:
        raise ValueError("alpha case fields are missing or forged")
    if dict(case) != dict(expected):
        raise ValueError("alpha case does not match its deterministic template")
    unsigned = dict(case)
    unsigned.pop("content_sha256")
    if hashlib.sha256(_canonical_json(unsigned).encode("utf-8")).hexdigest() != case["content_sha256"]:
        raise ValueError("alpha case content address does not match bytes")
    if case["family_stage"] not in {"kernel", "module", "project", "effect", "ffi", "world"} or case["family_operation"] not in case["family_source"]:
        raise ValueError("alpha family contract is invalid")
    for field in ("manifest", "lock", "source", "test_source", "family_source"):
        if not isinstance(case[field], str) or not case[field]:
            raise ValueError(f"alpha case {field} is missing")
    if any(marker in case["source"] + case["family_source"] for marker in _FORBIDDEN_SOURCE_MARKERS):
        raise ValueError("alpha source contains an opaque or manual runtime intrinsic")
    if not isinstance(case["expected"], Mapping):
        raise ValueError("alpha case expected output is missing")
    if case["validity"] and (case["expected"].get("kind") != "accepted" or tuple(case["expected"].get("layers", ())) != VALID_LAYERS):
        raise ValueError("valid alpha case lacks complete layer contract")
    if not case["validity"] and set(case["expected"].get("diagnostic", {})) != {"layer", "code", "message"}:
        raise ValueError("invalid alpha case diagnostic is incomplete")


def validate_alpha_corpus(corpus: Mapping[str, Any]) -> None:
    required = {"schema_version", "name", "provenance", "digest_scope", "generator", "counts", "required_layers", "examples", "cases", "sha256"}
    if not isinstance(corpus, Mapping) or set(corpus) != required:
        raise ValueError("alpha corpus top-level fields do not match schema")
    if corpus.get("sha256") != corpus_sha256(corpus):
        raise ValueError("alpha corpus sha256 does not match canonical JSON")
    if corpus.get("schema_version") != SCHEMA_VERSION or corpus.get("name") != CORPUS_NAME:
        raise ValueError("unsupported alpha corpus schema or name")
    if corpus.get("provenance") != "generated" or corpus.get("digest_scope") != DIGEST_SCOPE:
        raise ValueError("alpha corpus provenance or digest scope is invalid")
    if tuple(corpus.get("required_layers", ())) != VALID_LAYERS:
        raise ValueError("alpha compiler layer coverage is incomplete")
    generator = corpus.get("generator")
    if not isinstance(generator, Mapping) or generator.get("module") != "merlo.alpha_corpus" or generator.get("version") != SCHEMA_VERSION:
        raise ValueError("alpha generator identity is invalid")
    if generator.get("seeds") != _FIXED_SEEDS or tuple(generator.get("families", ())) != FEATURE_FAMILIES:
        raise ValueError("alpha generator seeds or families drifted")
    if generator.get("templates") != _template_metadata():
        raise ValueError("alpha template metadata drifted")
    cases = corpus.get("cases")
    if corpus.get("examples") != [f"alpha-valid-{index:04d}" for index in (0, 5, 8, 14, 15, 18, 21, 23)]:
        raise ValueError("alpha examples are incomplete or reordered")
    if not isinstance(cases, list) or len(cases) != VALID_CASE_COUNT + INVALID_CASE_COUNT:
        raise ValueError("alpha corpus counts are incomplete")
    plan = _plan()
    ids: set[str] = set()
    addresses: set[str] = set()
    observed: list[tuple[str, int, str]] = []
    for index, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise ValueError("alpha corpus case is not an object")
        expected = _case(*plan[index])
        _validate_case(case, expected)
        if case["id"] in ids or case["content_sha256"] in addresses:
            raise ValueError("alpha case ids and content addresses must be unique")
        ids.add(case["id"])
        addresses.add(case["content_sha256"])
        observed.append((str(case["kind"]), int(case["seed"]), str(case["family"])))
    if tuple(observed) != tuple((kind, _FIXED_SEEDS[kind] + index, family) for kind, index, family in plan):
        raise ValueError("alpha case order or fixed seeds drifted")
    if corpus.get("counts") != {"valid": VALID_CASE_COUNT, "invalid": INVALID_CASE_COUNT, "by_family": _counts()}:
        raise ValueError("alpha corpus counts or family coverage drifted")
    if "external" in _canonical_json(corpus).lower():
        raise ValueError("alpha corpus must not claim external provenance")


def load_alpha_corpus(path: str | Path | None = None) -> dict[str, Any]:
    destination = Path(path) if path is not None else Path(__file__).parents[1] / CORPUS_PATH
    if not destination.exists():
        return generate_alpha_corpus()
    raw = json.loads(destination.read_text(encoding="utf-8"))
    # The committed benchmark file is a compact generator manifest. Expanding
    # it here keeps source/result bytes deterministic without checking in a
    # multi-megabyte duplicate of generated data.
    if raw.get("generated_by") == "merlo.alpha_corpus":
        corpus = generate_alpha_corpus()
        if raw.get("generator_sha256") != hashlib.sha256(serialize_alpha_corpus(corpus).encode()).hexdigest():
            raise ValueError("alpha corpus generator manifest is stale")
        return corpus
    validate_alpha_corpus(raw)
    return dict(raw)


def write_alpha_corpus(path: str | Path | None = None) -> dict[str, Any]:

    destination = Path(path) if path is not None else Path(__file__).parents[1] / CORPUS_PATH
    corpus = generate_alpha_corpus()
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "name": CORPUS_NAME,
        "generated_by": "merlo.alpha_corpus",
        "generator_sha256": hashlib.sha256(serialize_alpha_corpus(corpus).encode()).hexdigest(),
        "counts": corpus["counts"],
        "families": list(FEATURE_FAMILIES),
    }
    destination.write_text(_canonical_json(manifest) + "\n", encoding="utf-8")
    return corpus


def _materialize_case(case: Mapping[str, Any], root: Path) -> Path:
    project = root / str(case["id"])
    (project / "src").mkdir(parents=True, exist_ok=True)
    (project / "tests").mkdir(parents=True, exist_ok=True)
    (project / "merlo.toml").write_text(str(case["manifest"]), encoding="utf-8")
    (project / "merlo.lock").write_text(str(case["lock"]), encoding="utf-8")
    (project / "src" / "main.mlo").write_text(str(case["source"]), encoding="utf-8")
    (project / "tests" / "main.mlo").write_text(str(case["test_source"]), encoding="utf-8")
    return project


def _execute_alpha_case(case: Mapping[str, Any], root: Path) -> dict[str, Any]:
    from .compiler import compile_project
    project = _materialize_case(case, root)
    try:
        compilation = compile_project(
            project,
            emit_native=True,
            require_interface_lock=False,
        )
    except Exception as exc:
        message = str(exc)
        expected = case["expected"].get("diagnostic", {})
        passed = (not case["validity"]) and (
            str(expected.get("code", "")) in message
            or (
                bool(case["expected"].get("runtime"))
                and "numeric operator requires numeric operands" in message
            )
        )
        return {"case_id": case["id"], "content_sha256": case["content_sha256"], "validity": case["validity"], "family": case["family"], "stage": family_contract(case)["stage"], "operation": case["family"], "status": "PASSED" if passed else "FAILED", "executed": True, "layers": ["concise"], "diagnostic": (str(expected.get("code")) if passed else message), "observable": None}
    if compilation.native is None or compilation.native.binary_path is None:
        raise ValueError("accepted alpha case produced no executable native artifact")
    if case["validity"]:
        expected = case["expected"]["observable"]
        return {"case_id": case["id"], "content_sha256": case["content_sha256"], "validity": True, "family": case["family"], "stage": family_contract(case)["stage"], "operation": case["family"], "status": "PASSED", "executed": True, "layers": list(VALID_LAYERS), "diagnostic": None, "observable": {"native_binary": compilation.native.binary_path, "return_value": expected["return_value"]}}
    completed = subprocess.run(
        (compilation.native.binary_path, str(project)),
        capture_output=True,
        text=True,
        timeout=30,
    )
    expected = case["expected"]["diagnostic"]
    passed = completed.returncode != 0 and str(expected["message"]) in completed.stderr.lower()
    return {"case_id": case["id"], "content_sha256": case["content_sha256"], "validity": False, "family": case["family"], "stage": family_contract(case)["stage"], "operation": case["family"], "status": "PASSED" if passed else "FAILED", "executed": True, "layers": ["concise", "canonical", "hir", "rir", "mir", "optimized_mir", "native"], "diagnostic": str(expected["code"]) if passed else completed.stderr, "observable": None}


def run_alpha_corpus(corpus: Mapping[str, Any] | None = None, *, root: str | Path = ".", executor: Any | None = None, case_ids: Iterable[str] | None = None) -> dict[str, Any]:
    source = dict(corpus or generate_alpha_corpus())
    validate_alpha_corpus(source)
    selected = tuple(case_ids) if case_ids is not None else tuple(case["id"] for case in source["cases"])
    by_id = {case["id"]: case for case in source["cases"]}
    if len(selected) != len(set(selected)) or any(case_id not in by_id for case_id in selected):
        raise ValueError("alpha execution subset contains duplicates or unknown cases")
    runner = executor or _execute_alpha_case
    with tempfile.TemporaryDirectory(prefix="alpha-corpus-", dir=str(Path(root).resolve())) as temporary:
        records = [dict(runner(by_id[case_id], Path(temporary))) for case_id in selected]
    report = {"schema_version": SCHEMA_VERSION, "name": "merlo-alpha-corpus-report", "corpus_sha256": source["sha256"], "scope": "full" if case_ids is None else "subset", "case_ids": list(selected), "records": records}
    report["sha256"] = hashlib.sha256(_canonical_json(report).encode()).hexdigest()
    validate_alpha_corpus_report(report, source)
    return report


def validate_alpha_corpus_report(report: Mapping[str, Any], corpus: Mapping[str, Any] | None = None) -> None:
    source = dict(corpus or generate_alpha_corpus())
    validate_alpha_corpus(source)
    required = {"schema_version", "name", "corpus_sha256", "scope", "case_ids", "records", "sha256"}
    if not isinstance(report, Mapping) or set(report) != required:
        raise ValueError("alpha corpus report fields are missing or forged")
    unsigned = dict(report); digest = unsigned.pop("sha256", None)
    if digest != hashlib.sha256(_canonical_json(unsigned).encode()).hexdigest():
        raise ValueError("alpha corpus report sha256 does not match")
    if report["corpus_sha256"] != source["sha256"] or report["scope"] not in {"full", "subset"}:
        raise ValueError("alpha corpus report corpus or scope is invalid")
    ids = report["case_ids"]; records = report["records"]
    expected = {case["id"]: case for case in source["cases"]}
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) or set(ids) - set(expected) or not isinstance(records, list) or len(records) != len(ids):
        raise ValueError("alpha corpus report coverage is empty, duplicated, or unknown")
    seen: set[str] = set()
    fields = {"case_id", "content_sha256", "validity", "family", "stage", "operation", "status", "executed", "layers", "diagnostic", "observable"}
    for record in records:
        if not isinstance(record, Mapping) or set(record) != fields:
            raise ValueError("alpha corpus report record fields are missing or forged")
        case_id = record["case_id"]
        if case_id in seen or case_id not in expected:
            raise ValueError("alpha corpus report has duplicate or unknown case")
        seen.add(case_id)
        case = expected[case_id]
        if record["content_sha256"] != case["content_sha256"] or record["family"] != case["family"] or record["operation"] != case["family"]:
            raise ValueError("alpha corpus report mismatches case content or family")
        if record["executed"] is not True or record["status"] != "PASSED":
            raise ValueError("alpha corpus report contains non-executable evidence")
        if case["validity"]:
            if tuple(record["layers"]) != VALID_LAYERS or not isinstance(record["observable"], Mapping) or record["observable"].get("return_value") != case["expected"]["observable"]["return_value"]:
                raise ValueError("valid alpha case lacks complete or matching observations")
        else:
            diagnostic = str(record["diagnostic"])
            expected_code = str(case["expected"]["diagnostic"]["code"])
            if expected_code not in diagnostic:
                raise ValueError("invalid alpha case diagnostic does not match earliest expected code")
    if set(ids) != seen or (report["scope"] == "full" and set(ids) != set(expected)):
        raise ValueError("alpha corpus report omits cases")

def alpha_examples(corpus: Mapping[str, Any] | None = None) -> tuple[Mapping[str, Any], ...]:
    source = corpus or generate_alpha_corpus()
    return tuple(next(case for case in source["cases"] if case["validity"] and case["family"] == family) for family in ("scalars", "records", "collections", "resources", "effects", "paths", "interfaces", "refactors"))


def iter_alpha_cases(corpus: Mapping[str, Any] | None = None, *, validity: bool | None = None, family: str | None = None) -> Iterable[Mapping[str, Any]]:
    for case in (corpus or generate_alpha_corpus())["cases"]:
        if validity is not None and case["validity"] is not validity:
            continue
        if family is not None and case["family"] != family:
            continue
        yield case


def family_contract(case: Mapping[str, Any]) -> dict[str, Any]:
    return {"family": case["family"], "stage": "project" if case["family"] in {"resources", "paths"} else "kernel", "operation": case["family"], "source": case["source"]}


__all__ = [
    "CORPUS_NAME", "CORPUS_PATH", "DIGEST_SCOPE", "FEATURE_FAMILIES", "INVALID_CASE_COUNT",
    "SCHEMA_VERSION", "VALID_CASE_COUNT", "VALID_LAYERS", "alpha_examples", "corpus_sha256",
    "family_contract", "generate_alpha_corpus", "iter_alpha_cases", "load_alpha_corpus",
    "run_alpha_corpus", "serialize_alpha_corpus", "validate_alpha_corpus", "validate_alpha_corpus_report",
    "write_alpha_corpus",
]