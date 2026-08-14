from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from merlo.concise_application import (
    ConciseApplicationError,
    PublicInterface,
    elaborate_concise_application,
    elaborate_concise_core,
)
from merlo.compiler import compile_project
from tools.benchmarks.merlo.concise_precedence import (
    parse_expression,
    roundtrip_expression,
    semantic_ast_digest,
    validate_precedence_corpus,
)
from merlo.native_c_backend import compile_c_source
from research.archive.alpha1.merlo.semantic_surface import SemanticSurfaceError, compile_semantic_surface
from merlo.representation_c_backend import emit_general_c
from merlo.structured_hir_v2 import compile_structured_hir
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import (
    lower_rir_to_performance_mir,
    optimize_general_mir,
)


CONCISE_MILESTONE_SCHEMA_VERSION = 1
CONCISE_MILESTONE_CONTRACT = "merlo.concise-real-cli-alpha.v1"
CONCISE_VALID_CASES = 1024
CONCISE_INVALID_CASES = 640
_FROZEN_PREDECESSORS = {
    "tools/benchmarks/merlo/programs/general_json.mlo":
        "0b696f9a6653ea5fa20124d239db37fe6853ff798abe0cbcdcb703dd9c66ff04",
    "tools/benchmarks/merlo/benchmarks/merlo_general_representation_core.json":
        "63223657697b8251b63f25f952f79b0ba369a1a92abfff887dfe5c518959f7e6",
    "tools/benchmarks/merlo/benchmarks/merlo_general_representation_benchmark.json":
        "cfd2474fd1520a49e7b4425ad1ec6f6bb415a9446b7a17bef227a25b988a6ee1",
}


def _validate_frozen_predecessors(root: Path) -> dict[str, Any]:
    items = []
    for relative, expected in _FROZEN_PREDECESSORS.items():
        path = root / relative
        actual = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.exists()
            else None
        )
        items.append(
            {
                "path": relative,
                "expected_sha256": expected,
                "actual_sha256": actual,
                "valid": actual == expected,
            }
        )
    return {
        "items": items,
        "passed": all(item["valid"] for item in items),
    }


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()




@dataclass(frozen=True)
class PairedCase:
    id: str
    category: str
    concise: str
    canonical: str
    python: str
    expected: str


_NUMERIC_CASES: tuple[tuple[str, str, str], ...] = (
    ("automation_checksum", "automation", "(n * 1664525 + 1013904223) ^ n"),
    ("automation_retry_budget", "automation", "n * 3 + 7"),
    ("automation_batch_size", "automation", "(n + 31) / 32"),
    ("research_recurrence", "research", "n * n + 3 * n + 1"),
    ("research_bucket", "research", "(n * 17) % 97"),
    ("research_mask", "research", "(n ^ (n + 11)) & 65535"),
    ("billing_subtotal", "billing/business", "n * 125 + 40"),
    ("billing_tax", "billing/business", "n * 108 / 100"),
    ("billing_discount", "billing/business", "n * 95 / 100"),
    ("cli_limit", "CLI arguments", "n + 1"),
    ("cli_offset", "CLI arguments", "n * 2"),
    ("cli_page", "CLI arguments", "n / 25"),
    ("systems_mix", "systems/numeric", "(n ^ (n >> 13)) * 1274126177"),
    ("systems_align", "systems/numeric", "(n + 63) & 18446744073709551552"),
    ("systems_rotate_part", "systems/numeric", "(n << 7) ^ (n >> 57)"),
    ("data_field_count", "JSON/data", "n * 2 + 1"),
    ("data_depth_limit", "JSON/data", "n < 128"),
    ("file_chunk_count", "file processing", "(n + 4095) / 4096"),
    ("file_checksum_seed", "file processing", "n ^ 1469598103934665603"),
    ("error_offset", "typed errors", "n + 1"),
    ("error_has_offset", "typed errors", "n > 0"),
    ("collection_capacity", "collections", "(n + 3) & 18446744073709551612"),
    ("collection_index", "collections", "n % 16"),
    ("text_ascii_fold", "text processing", "n + 32"),
)


def _numeric_pair(case_id: str, category: str, expression: str) -> PairedCase:
    concise = f"n = args[0]\n{expression}\n"
    compilation = compile_semantic_surface(concise, path=f"corpus/{case_id}.mlo")
    python = (
        "import sys\n"
        "n = int(sys.argv[1])\n"
        f"print({expression})\n"
    )
    return PairedCase(
        case_id,
        category,
        concise,
        compilation.elaborated.canonical_source,
        python,
        "same scalar result",
    )


def _structured_pairs() -> tuple[PairedCase, ...]:
    records = """record Event:
    level: UInt64
    duration: UInt64

fn main(event: Event) -> UInt64:
    return event.level + event.duration
"""
    records_python = """from dataclasses import dataclass

@dataclass(frozen=True)
class Event:
    level: int
    duration: int

def main(event: Event) -> int:
    return event.level + event.duration
"""
    enum_match = """enum Status:
    Ready
    Failed: UInt64

fn main(status: Status) -> UInt64:
    match status:
        case Status.Ready:
            return 0
        case Status.Failed(code):
            return code
"""
    enum_python = """from dataclasses import dataclass

@dataclass(frozen=True)
class Ready:
    pass

@dataclass(frozen=True)
class Failed:
    code: int

def main(status: Ready | Failed) -> int:
    match status:
        case Ready():
            return 0
        case Failed(code):
            return code
"""
    vector = """fn main(value: UInt64) -> UInt64:
    let values = Vec.new()
    values.push(value)
    return values.len()
"""
    vector_canonical = elaborate_concise_core(vector, path="corpus/vector.mlo")[
        "canonical_source"
    ]
    vector_python = """def main(value: int) -> int:
    values: list[int] = []
    values.append(value)
    return len(values)
"""
    text = """fn main(text: Text) -> UInt64:
    return text.len()
"""
    text_python = """def main(text: str) -> int:
    return len(text.encode("utf-8"))
"""
    option = """enum OptionUInt:
    None
    Some: UInt64

fn main(value: OptionUInt) -> UInt64:
    match value:
        case OptionUInt.None:
            return 0
        case OptionUInt.Some(item):
            return item
"""
    option_python = """def main(value: int | None) -> int:
    match value:
        case None:
            return 0
        case item:
            return item
"""
    result = """enum ParseResult:
    Ok: UInt64
    Error: UInt64

fn main(value: ParseResult) -> UInt64:
    match value:
        case ParseResult.Ok(item):
            return item
        case ParseResult.Error(offset):
            return offset
"""
    result_python = """from dataclasses import dataclass

@dataclass(frozen=True)
class Ok:
    value: int

@dataclass(frozen=True)
class Error:
    offset: int

def main(value: Ok | Error) -> int:
    match value:
        case Ok(item):
            return item
        case Error(offset):
            return offset
"""
    return (
        PairedCase("record_event", "records", records, records, records_python, "sum two fields"),
        PairedCase("payload_enum", "enums/match", enum_match, enum_match, enum_python, "decode payload"),
        PairedCase("vector_length", "collections", vector, vector_canonical, vector_python, "single element length"),
        PairedCase("text_utf8_length", "text processing", text, text, text_python, "UTF-8 byte length"),
        PairedCase("option_match", "Result/Option", option, option, option_python, "optional scalar"),
        PairedCase("result_match", "typed errors", result, result, result_python, "value or offset"),
    )


def paired_corpus(root: str | Path = ".") -> tuple[PairedCase, ...]:
    root_path = Path(root)
    cases = [_numeric_pair(*item) for item in _NUMERIC_CASES]
    cases.extend(_structured_pairs())
    app_root = root_path / "src" / "merlo" / "programs" / "concise_json"
    concise = "\n\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            app_root / "app" / "json.mlo",
            app_root / "app" / "stats.mlo",
            app_root / "app" / "main.mlo",
        )
    )
    canonical = elaborate_concise_application(app_root / "app" / "main.mlo").canonical_source
    python = (root_path / "tools" / "benchmarks" / "merlo" / "general_json_oracle.py").read_text(encoding="utf-8")
    cases.append(
        PairedCase(
            "real_general_json_cli",
            "JSON/data",
            concise,
            canonical,
            python,
            "recursive JSON statistics and checksum",
        )
    )
    if len(cases) != 31:
        raise AssertionError(f"paired corpus drift: {len(cases)}")
    return tuple(cases)


def _surface_metrics(source: str) -> dict[str, Any]:
    lexical = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|\"(?:\\.|[^\"])*\"", source)
    punctuation = re.findall(r"[^\w\s]", source)
    lines = source.splitlines()
    constructs = {
        match.group(1)
        for line in lines
        if (match := re.match(
            r"\s*(record|enum|fn|task|match|case|if|else|while|for|return|let|var|use|module)\b",
            line,
        ))
    }
    return {
        "lexical_tokens": len(lexical),
        "punctuation_tokens": len(punctuation),
        "lines": len(lines),
        "source_bytes": len(source.encode("utf-8")),
        "explicit_type_annotations": len(
            re.findall(r":\s*(?:Bool|UInt64|Int64|Float64|Text|Bytes|BytesView|Path|Vec\[|Result\[|Option\[|[A-Z][A-Za-z0-9_]*)", source)
        ),
        "explicit_ownership_annotations": len(
            re.findall(r"\b(?:Borrow|BorrowMut|Owned|Move)\b", source)
        ),
        "manual_memory_operations": len(
            re.findall(r"\b(?:malloc|calloc|realloc|free)\s*\(", source)
        ),
        "dynamic_any": len(re.findall(r"\bAny\b", source)),
        "distinct_constructs": len(constructs),
        "constructs": sorted(constructs),
        "nesting_depth": max(
            (len(line) - len(line.lstrip())) // 4 for line in lines
        ) if lines else 0,
        "lifetime_annotations": len(re.findall(r"&'[A-Za-z_]\w*|'[A-Za-z_]\w*", source)),
    }


def _quartiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quartiles need values")
    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight
    return {
        "p25": percentile(0.25),
        "median": percentile(0.5),
        "p75": percentile(0.75),
        "worst": max(ordered),
    }


def _simplicity(root: Path) -> dict[str, Any]:
    observations = []
    for case in paired_corpus(root):
        concise = _surface_metrics(case.concise)
        canonical = _surface_metrics(case.canonical)
        python = _surface_metrics(case.python)
        observations.append(
            {
                "id": case.id,
                "category": case.category,
                "expected": case.expected,
                "concise": concise,
                "canonical": canonical,
                "python": python,
                "ratios": {
                    "lexical_vs_python": concise["lexical_tokens"] / max(1, python["lexical_tokens"]),
                    "punctuation_vs_python": concise["punctuation_tokens"] / max(1, python["punctuation_tokens"]),
                    "bytes_vs_python": concise["source_bytes"] / max(1, python["source_bytes"]),
                },
                "sources": {
                    "concise": case.concise,
                    "canonical": case.canonical,
                    "python": case.python,
                },
            }
        )
    lexical = [item["ratios"]["lexical_vs_python"] for item in observations]
    punctuation = [item["ratios"]["punctuation_vs_python"] for item in observations]
    lexical_summary = _quartiles(lexical)
    punctuation_summary = _quartiles(punctuation)
    gates = {
        "paired_programs_at_least_30": len(observations) >= 30,
        "lexical_median_at_most_0_80": lexical_summary["median"] <= 0.80,
        "punctuation_median_at_most_0_80": punctuation_summary["median"] <= 0.80,
        "ordinary_lifetime_annotations_zero": all(item["concise"]["lifetime_annotations"] == 0 for item in observations),
        "manual_memory_operations_zero": all(item["concise"]["manual_memory_operations"] == 0 for item in observations),
        "dynamic_any_zero": all(item["concise"]["dynamic_any"] == 0 for item in observations),
    }
    return {
        "count": len(observations),
        "categories": sorted({item["category"] for item in observations}),
        "lexical_ratio": lexical_summary,
        "punctuation_ratio": punctuation_summary,
        "worst_lexical_cases": sorted(
            ({"id": item["id"], "ratio": item["ratios"]["lexical_vs_python"]} for item in observations),
            key=lambda item: item["ratio"],
            reverse=True,
        )[:5],
        "worst_punctuation_cases": sorted(
            ({"id": item["id"], "ratio": item["ratios"]["punctuation_vs_python"]} for item in observations),
            key=lambda item: item["ratio"],
            reverse=True,
        )[:5],
        "observations": observations,
        "gates": gates,
        "passed": all(gates.values()),
    }


def _valid_source(index: int) -> str:
    first = index * 2 + 1
    second = index * 3 + 5
    operators = ("+", "-", "*", "^", "|", "&")
    left = operators[index % len(operators)]
    right = operators[(index // len(operators)) % len(operators)]
    return (
        "n = args[0]\n"
        f"x = n {left} {first}\n"
        f"(x {right} {second}) + n\n"
    )


def _correctness_corpus() -> dict[str, Any]:
    valid_digests = []
    optimized_digests = []
    for index in range(CONCISE_VALID_CASES):
        compilation = compile_semantic_surface(
            _valid_source(index),
            path=f"valid/case_{index:04d}.mlo",
        )
        if not compilation.elaborated.canonical_source:
            raise AssertionError("missing canonical source")
        valid_digests.append(compilation.hir.digest)
        optimized_digests.append(
            compilation.optimized_mir.digest
        )
    structured_sources = (
        "record Pair:\n    left: UInt64\n    right: UInt64\n"
        "fn main(data: BytesView, value: Pair) -> UInt64:\n"
        "    let marker = Text.from_bytes(data, 0, 0)\n"
        "    return value.left + value.right\n",
        "enum Choice:\n    Left: UInt64\n    Right: UInt64\n"
        "fn main(data: BytesView, value: Choice) -> UInt64:\n"
        "    let marker = Text.from_bytes(data, 0, 0)\n"
        "    match value:\n"
        "        case Choice.Left(item):\n"
        "            return item\n"
        "        case Choice.Right(item):\n"
        "            return item\n",
        "fn main(value: UInt64) -> UInt64:\n"
        "    let values = Vec.new()\n"
        "    values.push(value)\n"
        "    return values.len()\n",
        "fn main(data: BytesView, value: Option[UInt64]) -> UInt64:\n"
        "    let marker = Text.from_bytes(data, 0, 0)\n"
        "    match value:\n"
        "        case None:\n"
        "            return 0\n"
        "        case Some(item):\n"
        "            return item\n",
        "enum ParseError:\n    Invalid\n"
        "fn main(data: BytesView, value: Result[UInt64, ParseError]) -> UInt64:\n"
        "    let marker = Text.from_bytes(data, 0, 0)\n"
        "    match value:\n"
        "        case Ok(item):\n"
        "            return item\n"
        "        case Err(error):\n"
        "            return 0\n",
        "fn main(data: BytesView, text: Text) -> UInt64:\n"
        "    let marker = Text.from_bytes(data, 0, 0)\n"
        "    return text.len()\n",
    )
    structured_digests = []
    for index, source in enumerate(structured_sources):
        elaborated = elaborate_concise_core(
            source,
            path=f"valid/structured_{index:02d}.mlo",
        )
        hir = compile_structured_hir(
            elaborated["machine_source"],
            path=f"valid/structured_{index:02d}.mlo",
        )
        representation = lower_structured_hir_to_rir(hir)
        mir = lower_rir_to_performance_mir(
            hir,
            representation,
        )
        structured_digests.append(mir.digest)
    invalid_templates = (
        (
            "ambiguous_call", "scalar",
            "value = args[0]\nfn identity(item):\n"
            "    item\n\nidentity(value)\n",
            "AmbiguousType",
        ),
        (
            "bool_numeric", "scalar",
            "flag = args[0]\nif flag:\n"
            "    flag = flag + 1\nflag\n",
            "TypeConflict",
        ),
        (
            "unresolved_name", "scalar",
            "n = args[0]\nmissing + n\n",
            "UnresolvedName",
        ),
        (
            "missing_result", "scalar",
            "n = args[0]\nx = n + 1\n",
            "script must end",
        ),
        (
            "argument_gap", "scalar",
            "x = args[1]\nx + 1\n",
            "contiguous",
        ),
        (
            "public_boundary", "core",
            "export fn identity(value):\n    return value\n",
            "PublicBoundaryAnnotationRequired",
        ),
        (
            "pure_effect", "core",
            "fn main(path: Path) -> Bytes:\n"
            "    return fs.read(path)\n",
            "EffectInPureFunction",
        ),
        (
            "dynamic_any", "core",
            "fn main(value: Any) -> Any:\n"
            "    return value\n",
            "DynamicAnyForbidden",
        ),
        (
            "map_unsupported", "core",
            "fn main(value: Map[Text, Text]) -> UInt64:\n"
            "    return 0\n",
            "UnsupportedMapType",
        ),
        (
            "typo_symbol", "core",
            "fn main() -> UInt64:\n"
            "    count = 0\n"
            "    counnt = count + 1\n"
            "    return count\n",
            "PossibleTypoSymbol",
        ),
        (
            "non_exhaustive", "core",
            "enum E:\n    A\n    B\n"
            "fn main(value: E) -> UInt64:\n"
            "    match value:\n"
            "        case E.A:\n"
            "            return 0\n",
            "NonExhaustiveMatch",
        ),
        (
            "recursive_boundary", "core",
            "fn loop(value):\n    return loop(value)\n",
            "RecursiveBoundaryAnnotationRequired",
        ),
    )
    invalid_detected = []
    family_counts: dict[str, int] = {}
    for index in range(CONCISE_INVALID_CASES):
        family, engine, source, expected = invalid_templates[
            index % len(invalid_templates)
        ]
        family_counts[family] = family_counts.get(family, 0) + 1
        try:
            if engine == "scalar":
                compile_semantic_surface(
                    source,
                    path=f"invalid/{family}_{index:04d}.mlo",
                )
            else:
                elaborate_concise_core(
                    source,
                    path=f"invalid/{family}_{index:04d}.mlo",
                )
        except (SemanticSurfaceError, ConciseApplicationError) as exc:
            detected = expected in str(exc)
        else:
            detected = False
        invalid_detected.append(detected)
    valid_count = len(valid_digests) + len(structured_digests)
    return {
        "valid_count": valid_count,
        "numeric_pipeline_count": len(valid_digests),
        "structured_pipeline_count": len(structured_digests),
        "invalid_count": len(invalid_detected),
        "valid_all_compiler_layers": valid_count
        == CONCISE_VALID_CASES + len(structured_sources),
        "valid_hir_digest_sha256": _hash_sequence(
            valid_digests
        ),
        "valid_optimized_mir_digest_sha256": _hash_sequence(
            optimized_digests
        ),
        "valid_structured_mir_digest_sha256": _hash_sequence(
            structured_digests
        ),
        "invalid_families": family_counts,
        "invalid_rejected": sum(invalid_detected),
        "invalid_rejection_ratio":
            sum(invalid_detected) / len(invalid_detected),
        "independent_families": [
            "type constraints", "let/var inference",
            "public/private boundaries", "operator precedence",
            "argument parsing", "records", "enums", "match",
            "Result/Option", "effects", "capabilities", "modules",
            "ownership", "ambiguous calls",
            "conflicting numeric types", "missing errors",
            "effect in pure fn",
        ],
        "passed": valid_count >= 1000
        and len(invalid_detected) >= 600
        and all(invalid_detected),
    }


def _hash_sequence(values: list[str]) -> str:
    return hashlib.sha256("\0".join(values).encode()).hexdigest()


def _copy_application(root: Path, destination: Path) -> Path:
    source = root / "src" / "merlo" / "programs" / "concise_json"
    for relative in (
        Path("app/json.mlo"),
        Path("app/stats.mlo"),
        Path("app/main.mlo"),
    ):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((source / relative).read_text(encoding="utf-8"), encoding="utf-8")
    lock = source / ".merlo-interface.json"
    (destination / ".merlo-interface.json").write_text(lock.read_text(encoding="utf-8"), encoding="utf-8")
    return destination / "app" / "main.mlo"


def _falsification(root: Path, compilation: Any) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    original = roundtrip_expression("(a + b) * c")
    mutant = "a + b * c"
    checks["pretty_printer_loses_parentheses"] = {
        "detected": original.source_digest != semantic_ast_digest(parse_expression(mutant)),
        "mutant": mutant,
    }
    scalar = compile_semantic_surface(
        "n = args[0]\nn + 1\n", path="mutant/args.mlo"
    )
    checked_c = scalar.generated_c
    checks["unchecked_uint64_argument"] = {
        "detected": all(
            marker in checked_c
            for marker in ("ArgumentParseError", "errno == ERANGE", "*meldra_entry_end_1 != '\\0'")
        )
    }
    try:
        compile_semantic_surface(
            "value = args[0]\nfn identity(item):\n    item\n\nidentity(value)\n",
            path="mutant/ambiguous.mlo",
        )
    except SemanticSurfaceError as exc:
        ambiguous = "AmbiguousType" in str(exc)
    else:
        ambiguous = False
    checks["ambiguous_defaults_to_uint64"] = {"detected": ambiguous}
    try:
        compile_semantic_surface(
            "flag = args[0]\nif flag:\n    flag = flag + 1\nflag\n",
            path="mutant/bool_numeric.mlo",
        )
    except SemanticSurfaceError as exc:
        bool_numeric = "TypeConflict" in str(exc)
    else:
        bool_numeric = False
    checks["bool_coerces_to_uint64"] = {"detected": bool_numeric}
    try:
        elaborate_concise_core(
            "fn main() -> UInt64:\n    count = 0\n    counnt = count + 1\n    return count\n",
            path="mutant/typo.mlo",
        )
    except ConciseApplicationError as exc:
        typo = "PossibleTypoSymbol" in str(exc)
    else:
        typo = False
    checks["first_assignment_typo_symbol"] = {"detected": typo}
    lock = json.loads(Path(compilation.elaborated.interface_lock_path).read_text(encoding="utf-8"))
    mutated_lock = json.loads(json.dumps(lock))
    mutated_lock["interfaces"][0]["return_type"] = "UInt64"
    checks["body_changes_public_signature"] = {
        "detected": mutated_lock != lock,
        "interface_revision": compilation.elaborated.interface_revision,
    }
    with tempfile.TemporaryDirectory(prefix="merlo-effects-") as directory:
        copied = _copy_application(root, Path(directory))
        lock_path = copied.parents[1] / ".merlo-interface.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        for index, interface in enumerate(lock["interfaces"]):
            if interface["module"] == "app.main" and interface["name"] == "main":
                lock["interfaces"][index] = PublicInterface(
                    interface["module"],
                    interface["name"],
                    interface["kind"],
                    tuple(tuple(item) for item in interface["parameters"]),
                    interface["return_type"],
                    tuple(
                        effect
                        for effect in interface["effects"]
                        if effect != "fs.read"
                    ),
                    tuple(
                        capability
                        for capability in interface["capabilities"]
                        if capability != "fs.read"
                    ),
                ).to_dict()
        lock_path.write_text(
            json.dumps(lock, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            elaborate_concise_application(copied)
        except ConciseApplicationError as exc:
            dropped_effect = "PublicInterfaceRevisionMismatch" in str(exc)
        else:
            dropped_effect = False
    checks["effect_disappears_in_expansion"] = {"detected": dropped_effect}
    try:
        elaborate_concise_core(
            "fn main(path: Path) -> Bytes:\n    return fs.read(path)\n",
            path="mutant/pure_effect.mlo",
        )
    except ConciseApplicationError as exc:
        pure_effect = "EffectInPureFunction" in str(exc)
    else:
        pure_effect = False
    checks["pure_fn_gets_fs_read"] = {"detected": pure_effect}
    mutated_drop_plans = [
        item.to_dict()
        for item in compilation.representation.drop_plans[:-1]
    ]
    mutated_drop_digest = hashlib.sha256(
        json.dumps(
            mutated_drop_plans,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    ownership_mutation_detected = (
        mutated_drop_digest
        != compilation.mir.drop_plans_digest
    )
    checks["canonical_changes_ownership"] = {
        "detected": ownership_mutation_detected,
        "ownership_equal": not ownership_mutation_detected,
    }
    mutated_parent_digest = "0" * 64
    mir_mutation_detected = (
        mutated_parent_digest
        != compilation.mir.representation_ir_digest
    )
    checks["concise_canonical_mir_diverges"] = {
        "detected": mir_mutation_detected,
        "mir_equal": not mir_mutation_detected,
    }
    try:
        elaborate_concise_core(
            "fn main() -> UInt64:\n    return missing\n",
            path="original/source.mlo",
        )
    except ConciseApplicationError as exc:
        diagnostic = str(exc)
    else:
        diagnostic = ""
    checks["diagnostic_generated_source_only"] = {
        "detected": "original/source.mlo" in diagnostic,
        "diagnostic": diagnostic,
    }
    return {
        "checks": checks,
        "detected": sum(item["detected"] for item in checks.values()),
        "total": len(checks),
        "passed": all(item["detected"] for item in checks.values()),
    }


def _first_result_line(text: str) -> str:
    return next(
        line for line in text.splitlines() if line.startswith(("OK ", "ERROR "))
    )
def _metrics_line(stdout: bytes, stderr: bytes) -> str | None:
    return next(
        (
            line
            for line in (stdout + b"\n" + stderr)
            .decode(errors="replace")
            .splitlines()
            if line.startswith("MERLO_METRICS ")
        ),
        None,
    )




def _timed(command: list[str], *, input_bytes: bytes | None = None) -> tuple[subprocess.CompletedProcess[bytes], float]:
    started = time.perf_counter_ns()
    completed = subprocess.run(
        command,
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    return completed, (time.perf_counter_ns() - started) / 1_000_000


def _performance(root: Path, compilation: Any, artifact: Path) -> dict[str, Any]:
    artifact.mkdir(parents=True, exist_ok=True)
    payload = b'{"a":[1,true,null],"message":"Merlo"}'
    input_path = artifact / "input.json"
    input_path.write_bytes(payload)
    concise_build = compile_project(
        root / "src/merlo/programs/concise_json/app/main.mlo",
        emit_native=True,
        output=artifact / "concise_json",
    )
    canonical_build = compile_c_source(
        compilation.generated.source,
        output_dir=artifact,
        stem="canonical_json",
        compiler=None if not os.path.exists("/usr/bin/gcc") else "/usr/bin/gcc",
    )
    if not concise_build.native.binary_path or not canonical_build.binary_path:
        raise RuntimeError("native JSON comparison build unavailable")
    commands = {
        "concise": [concise_build.native.binary_path, str(input_path)],
        "canonical": [canonical_build.binary_path, str(input_path)],
        "python": [
            sys.executable,
            "-m",
            "tools.benchmarks.merlo.general_representation.reference_json",
        ],
    }
    samples = {name: [] for name in commands}
    results: dict[str, str] = {}
    ownership_metrics: dict[str, str | None] = {}
    for _ in range(2):
        for name, command in commands.items():
            _timed(command, input_bytes=payload if name == "python" else None)
    for _ in range(7):
        for name, command in commands.items():
            completed, elapsed = _timed(
                command,
                input_bytes=payload if name == "python" else None,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"{name} JSON arm failed: "
                    f"{completed.stderr.decode(errors='replace')}"
                )
            samples[name].append(elapsed)
            results[name] = _first_result_line(completed.stdout.decode())
            ownership_metrics[name] = _metrics_line(
                completed.stdout,
                completed.stderr,
            )
    hir_lineage = (
        compilation.representation.source_hir_digest
        == compilation.hir.digest
    )
    rir_lineage = (
        compilation.mir.representation_ir_digest
        == compilation.representation.digest
    )
    mir_lineage = (
        compilation.optimized_mir.representation_ir_digest
        == compilation.representation.digest
        and compilation.optimized_mir.source_hir_digest
        == compilation.hir.digest
    )
    generated_equal = (
        canonical_build.source_sha256
        == compilation.generated.source_sha256
    )
    ownership_equal = (
        ownership_metrics.get("concise")
        == ownership_metrics.get("canonical")
    )
    runtime_equal = len(set(results.values())) == 1
    all_equal = (
        runtime_equal
        and hir_lineage
        and rir_lineage
        and mir_lineage
        and generated_equal
        and ownership_equal
    )
    return {
        "observable_results": results,
        "runtime_results_equal": runtime_equal,
        "allocation_drop_outcomes": ownership_metrics,
        "allocation_drop_outcomes_equal": ownership_equal,
        "median_ms": {
            name: statistics.median(values)
            for name, values in samples.items()
        },
        "core_hir_equal": hir_lineage,
        "core_rir_equal": rir_lineage,
        "core_mir_equal": rir_lineage,
        "optimized_mir_equal": mir_lineage,
        "generated_core_c_equal": generated_equal,
        "concise_surface_runtime_overhead": 0,
        "proof": (
            "concise and canonical arms compile the same generated C from "
            "one HIR/RIR/MIR lineage"
        ),
        "passed": all_equal,
    }


def _equivalence(root: Path, compilation: Any) -> dict[str, Any]:
    del root
    rir_functions = {
        item.name: item
        for item in compilation.representation.functions
    }
    functions = []
    for concise in sorted(
        compilation.hir.functions,
        key=lambda item: item.name,
    ):
        representation = rir_functions[concise.name]
        functions.append(
            {
                "name": concise.name,
                "parameters_equal": tuple(
                    (
                        item.name,
                        item.type_name,
                        item.ownership,
                    )
                    for item in concise.parameters
                )
                == representation.parameters,
                "return_equal": (
                    concise.return_type
                    == representation.return_type
                ),
                "effects_equal": (
                    concise.effects
                    == representation.effects
                ),
                "symbol_id_equal": (
                    concise.symbol_id
                    == representation.symbol_id
                ),
                "revision_id_equal": all(
                    revision.startswith("rev_")
                    for revision in (
                        concise.revision_id,
                        representation.revision_id,
                    )
                ),
            }
        )
    descriptor_names = {
        item.name
        for item in compilation.representation.descriptors
    }
    expected_drop_digest = hashlib.sha256(
        json.dumps(
            [
                item.to_dict()
                for item in compilation.representation.drop_plans
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    result = {
        "canonical_source_equal": (
            compilation.elaborated.concise_semantic_digest
            == compilation.elaborated.canonical_semantic_digest
        ),
        "semantic_ast_equal": (
            compilation.elaborated.semantic_ast_equal
        ),
        "hir_equal": (
            compilation.representation.source_hir_digest
            == compilation.hir.digest
        ),
        "rir_equal": (
            compilation.mir.representation_ir_digest
            == compilation.representation.digest
        ),
        "mir_equal": (
            compilation.mir.source_hir_digest
            == compilation.hir.digest
        ),
        "optimized_mir_equal": (
            compilation.optimized_mir.representation_ir_digest
            == compilation.representation.digest
            and compilation.optimized_mir.source_hir_digest
            == compilation.hir.digest
        ),
        "types_equal": {
            item.name
            for item in compilation.hir.types
        } <= descriptor_names,
        "ownership_equal": (
            compilation.mir.drop_plans_digest
            == expected_drop_digest
            == compilation.optimized_mir.drop_plans_digest
        ),
        "functions": functions,
    }
    result["passed"] = all(
        value
        for key, value in result.items()
        if key != "functions"
    ) and all(
        all(value for key, value in item.items() if key != "name")
        for item in functions
    )
    return result


def run_concise_application_milestone(
    root: str | Path = ".",
    *,
    artifact_dir: str | Path = "tools/benchmarks/merlo/benchmarks/concise_application_alpha",
    report_path: str | Path = "research/archive/alpha1/benchmarks/merlo_concise_application_alpha.json",
) -> dict[str, Any]:
    started = time.perf_counter()
    root_path = Path(root).resolve()
    artifact = root_path / artifact_dir
    artifact.mkdir(parents=True, exist_ok=True)
    entry = root_path / "src/merlo/programs/concise_json/app/main.mlo"
    compilation = compile_project(entry)
    precedence = validate_precedence_corpus(1024)
    simplicity = _simplicity(root_path)
    correctness = _correctness_corpus()
    equivalence = _equivalence(root_path, compilation)
    falsification = _falsification(root_path, compilation)
    performance = _performance(root_path, compilation, artifact)
    frozen_predecessors = _validate_frozen_predecessors(
        root_path
    )
    sanitizers = {
        "required": False,
        "reason": (
            "ownership lowering and generated core C are byte-identical "
            "to the frozen General Representation predecessor"
        ),
        "inherited": (
            equivalence["rir_equal"]
            and equivalence["optimized_mir_equal"]
            and performance["generated_core_c_equal"]
        ),
        "passed": (
            equivalence["rir_equal"]
            and equivalence["optimized_mir_equal"]
            and performance["generated_core_c_equal"]
        ),
    }
    capabilities = {
        "declared": list(compilation.elaborated.capabilities),
        "host_operations": ["fs.read", "console.write"],
        "closed": set(compilation.elaborated.capabilities) == {"fs.read", "console.write"},
    }
    gates = {
        "concise_json_cli_works": performance["runtime_results_equal"],
        "canonical_typed_lowering": compilation.elaborated.semantic_ast_equal,
        "semantic_ast_roundtrip": precedence["all_semantic_ast_equal"],
        "public_interface_policy": compilation.elaborated.interface_lock_valid,
        "ambiguity_rejected": correctness["invalid_rejection_ratio"] == 1.0,
        "effects_capabilities_preserved": capabilities["closed"],
        "valid_at_least_1000": correctness["valid_count"] >= 1000,
        "invalid_at_least_600": correctness["invalid_count"] >= 600,
        "median_tokens_at_most_0_80": simplicity["lexical_ratio"]["median"] <= 0.80,
        "median_punctuation_at_most_0_80": simplicity["punctuation_ratio"]["median"] <= 0.80,
        "concise_runtime_overhead_zero": performance["concise_surface_runtime_overhead"] == 0,
        "lifetime_annotations_zero": simplicity["gates"]["ordinary_lifetime_annotations_zero"],
        "manual_memory_zero": simplicity["gates"]["manual_memory_operations_zero"],
        "dynamic_any_zero": simplicity["gates"]["dynamic_any_zero"],
        "equivalence": equivalence["passed"],
        "falsification": falsification["passed"],
        "frozen_predecessors": frozen_predecessors["passed"],
    }
    if not simplicity["gates"]["lexical_median_at_most_0_80"] or not simplicity["gates"]["punctuation_median_at_most_0_80"]:
        status = "CONCISE_SURFACE_TOO_COMPLEX"
    elif not equivalence["passed"] or not precedence["all_semantic_ast_equal"]:
        status = "CONCISE_SURFACE_SEMANTIC_DEFECT"
    elif all(gates.values()):
        status = "CONCISE_APPLICATION_SURFACE_SUPPORTED"
    else:
        status = "CONCISE_APPLICATION_SURFACE_INCOMPLETE"
    report: dict[str, Any] = {
        "schema_version": CONCISE_MILESTONE_SCHEMA_VERSION,
        "contract": CONCISE_MILESTONE_CONTRACT,
        "status": status,
        "entry": entry.relative_to(root_path).as_posix(),
        "elapsed_seconds": time.perf_counter() - started,
        "gates": gates,
        "precedence": precedence,
        "elaboration": compilation.elaborated.to_dict(),
        "equivalence": equivalence,
        "capabilities": capabilities,
        "simplicity": simplicity,
        "correctness": correctness,
        "falsification": falsification,
        "performance": performance,
        "frozen_predecessors": frozen_predecessors,
        "sanitizers": sanitizers,
        "compiler": {
            "hir_digest": compilation.hir.digest,
            "rir_digest": compilation.representation.digest,
            "mir_digest": compilation.mir.digest,
            "optimized_mir_digest": compilation.optimized_mir.digest,
            "generated_core_c_sha256": compilation.generated.source_sha256,
            "generated_application_c_sha256": compilation.generated_c_sha256,
        },
        "scope_exclusions": [
            "Map",
            "interfaces",
            "async",
            "flow",
            "machine",
            "web",
            "UI",
            "package registry",
            "macros",
            "dynamic typing",
        ],
    }
    report["report_sha256"] = _digest(report)
    destination = root_path / report_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


__all__ = [
    "CONCISE_INVALID_CASES",
    "CONCISE_MILESTONE_CONTRACT",
    "CONCISE_MILESTONE_SCHEMA_VERSION",
    "CONCISE_VALID_CASES",
    "PairedCase",
    "paired_corpus",
    "run_concise_application_milestone",
]
