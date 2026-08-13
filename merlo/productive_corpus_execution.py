"""One-run execution evidence for the committed Productive Core corpus.

The runner deliberately keeps application, compiler, and unavailable language
layers separate.  It records compact observations only; application stdout and
stderr are never retained in the report.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Any, Callable

from .productive_applications import (
    CsvOptions,
    GrepOptions,
    NdjsonOptions,
    ProductiveApplicationError,
    aggregate_csv,
    analyze_ndjson,
    run_csv_cli,
    run_grep_cli,
    run_ndjson_cli,
    search_text,
)
from .frontend_semantics import check_frontend
from .productive_corpus import load_productive_corpus


PRODUCTIVE_CORPUS_EXECUTION_SCHEMA_VERSION = 1
PRODUCTIVE_CORPUS_EXECUTION_KIND = "merlo.productive-corpus-execution"

_LAYER_NAMES = (
    "python_applications",
    "merlo_compiler",
    "concise_application",
    "canonical",
    "hir",
    "rir",
    "mir",
    "native",
)
_EXPECTED_DATA_COUNTS = {
    "ndjson": {
        "empty": (0, 0, 0),
        "one-line": (1, 1, 0),
        "unicode": (1, 1, 0),
        "large": (64, 64, 0),
        "map-collision": (8, 8, 0),
        "map-growth": (48, 48, 0),
        "map-duplicate": (12, 12, 0),
        "early-parse-error": (2, 1, 1),
        "late-parse-error": (2, 1, 1),
    },
    "csv": {
        "empty": (0, 0, 0),
        "one-line": (0, 0, 0),
        "unicode": (1, 1, 0),
        "large": (64, 64, 0),
        "map-collision": (8, 8, 0),
        "map-growth": (48, 48, 0),
        "map-duplicate": (12, 12, 0),
        "early-parse-error": (1, 0, 1),
        "late-parse-error": (2, 1, 1),
    },
    "grep": {
        "empty": (0, 0, 0),
        "one-line": (1, 1, 0),
        "unicode": (1, 1, 0),
        "large": (96, 96, 0),
        "map-collision": (8, 8, 0),
        "map-growth": (48, 48, 0),
        "map-duplicate": (12, 12, 0),
    },
}


def _counts() -> dict[str, int]:
    return {"total": 0, "attempted": 0, "passed": 0, "failed": 0, "unmeasured": 0}


def _layer(total: int = 0, *, reason: str = "") -> dict[str, Any]:
    return {
        "status": "UNMEASURED" if total == 0 else "PASSED",
        "total": total,
        "attempted": 0,
        "passed": 0,
        "failed": 0,
        "unmeasured": 0,
        "failure_ids": [],
        "unmeasured_ids": [],
        "unmeasured_reasons": {},
        "reason": reason,
    }


def _finish_layer(layer: dict[str, Any]) -> None:
    if layer["failed"]:
        layer["status"] = "FAILED"
    elif layer["unmeasured"] or not layer["total"]:
        layer["status"] = "UNMEASURED"
    else:
        layer["status"] = "PASSED"


def _record_count(bucket: dict[str, int], state: str) -> None:
    bucket["total"] += 1
    if state != "unmeasured":
        bucket["attempted"] += 1
    bucket[state] += 1


def _error_code(error: Exception) -> str:
    return str(error).split(maxsplit=1)[0]


def _path_for_case(root: Path, case: dict[str, Any]) -> Path:
    suffix = {"ndjson": ".ndjson", "csv": ".csv", "grep": ".txt"}[case["kind"]]
    return root / f"{case['id']}{suffix}"


def _expected_data_observation(case: dict[str, Any], result: Any) -> str | None:
    kind = case["kind"]
    family = case["family"]
    outcome = case["expected"]["outcome"]
    if outcome == "success" or outcome == "invalid-record":
        expected = _EXPECTED_DATA_COUNTS.get(kind, {}).get(family)
        if expected is None:
            return f"no independent metric oracle for {kind}/{family}"
        total, valid, invalid = expected
        if kind == "ndjson":
            observed = (result.total, result.valid, result.invalid)
        elif kind == "csv":
            observed = (result.total, result.valid, result.invalid)
        else:
            observed = (result.total_lines, result.matching_lines, 0)
        if observed != expected:
            return f"observed metrics {observed!r} != expected {expected!r}"
        if bool(case["validity"]) != (outcome == "success" and invalid == 0):
            return "case validity disagrees with expected outcome"
        return None
    return f"unexpected direct-API outcome {outcome!r}"


def _execute_data_case(case: dict[str, Any], path: Path) -> str | None:
    kind = case["kind"]
    expected = case["expected"]
    if case["family"] == "cli-failure":
        invocation = list(expected.get("invocation", ()))
        if not invocation:
            return "CLI failure case has no invocation"
        invocation[0] = str(path)
        runners: dict[str, Callable[[list[str]], Any]] = {
            "ndjson": run_ndjson_cli,
            "csv": run_csv_cli,
            "grep": run_grep_cli,
        }
        run = runners[kind](invocation)
        if run.exit_code != 2 or expected["outcome"] != "exit-2":
            return f"CLI exit code {run.exit_code!r} did not match expected exit-2"
        return None

    try:
        if kind == "ndjson":
            result = analyze_ndjson(path, NdjsonOptions())
        elif kind == "csv":
            result = aggregate_csv(path, CsvOptions())
        else:
            result = search_text(path, GrepOptions(contains="needle"))
    except ProductiveApplicationError as error:
        if _error_code(error) == expected["outcome"]:
            return None
        return f"application error {_error_code(error)!r} != expected {expected['outcome']!r}"
    except Exception as error:  # Keep one case from hiding later cases.
        return f"unexpected application exception {type(error).__name__}: {error}"
    return _expected_data_observation(case, result)


def _split_merlo_sources(source: str, *, fallback_path: str) -> dict[str, str]:
    sources: dict[str, list[str]] = {}
    current = fallback_path
    sources[current] = []
    for line in source.splitlines(keepends=True):
        if line.startswith("// file:"):
            current = line[len("// file:"):].strip()
            sources.setdefault(current, [])
            continue
        sources[current].append(line)
    return {path: "".join(lines) for path, lines in sources.items() if lines}


def _execute_merlo_case(case: dict[str, Any]) -> tuple[str, str | None]:
    sources = _split_merlo_sources(case["merlo_source"], fallback_path=f"{case['id']}.mlo")
    try:
        result = check_frontend(sources)
    except Exception as error:
        return "failed", f"unexpected compiler exception {type(error).__name__}: {error}"
    if not result.diagnostics:
        return "failed", "compiler accepted a case with an expected diagnostic"
    actual = result.diagnostics[0].code
    expected = case["expected"]["outcome"]
    if actual != expected:
        return "failed", f"diagnostic {actual!r} != expected {expected!r}"
    return "passed", None

def run_productive_corpus_execution(root: str | Path = ".") -> dict[str, Any]:
    """Execute every committed corpus case once in deterministic order."""
    workspace = Path(root)
    corpus = load_productive_corpus(workspace / "benchmarks/merlo_productive_corpus.json")
    cases = corpus["cases"]
    execution_layers = {name: _layer() for name in _LAYER_NAMES}
    execution_layers["python_applications"] = _layer(sum(case["kind"] != "merlo" for case in cases))
    execution_layers["merlo_compiler"] = _layer(
        sum(case["kind"] == "merlo" for case in cases),
    )
    per_kind: dict[str, dict[str, int]] = {}
    per_family: dict[str, dict[str, dict[str, int]]] = {}
    failures: list[dict[str, str]] = []

    with tempfile.TemporaryDirectory(prefix="merlo-productive-corpus-") as temporary:
        payload_root = Path(temporary)
        for case in cases:
            kind = case["kind"]
            family = case["family"]
            kind_counts = per_kind.setdefault(kind, _counts())
            family_counts = per_family.setdefault(kind, {}).setdefault(family, _counts())
            if kind != "merlo":
                path = _path_for_case(payload_root, case)
                path.write_bytes(base64.b64decode(case["payload_b64"], validate=True))
                reason = _execute_data_case(case, path)
                state = "passed" if reason is None else "failed"
                layer = execution_layers["python_applications"]
            else:
                path = payload_root / f"{case['id']}.mlo"
                path.write_text(case["merlo_source"], encoding="utf-8")
                state, reason = _execute_merlo_case(case)
                layer = execution_layers["merlo_compiler"]
            _record_count(kind_counts, state)
            _record_count(family_counts, state)
            layer["attempted"] += state != "unmeasured"
            layer[state] += 1
            if state == "failed":
                layer["failure_ids"].append(case["id"])
                failures.append(
                    {
                        "id": case["id"],
                        "kind": kind,
                        "family": family,
                        "layer": "python_applications" if kind != "merlo" else "merlo_compiler",
                        "reason": reason or "execution failed",
                    }
                )
            elif state == "unmeasured":
                layer["unmeasured_ids"].append(case["id"])
                layer["unmeasured_reasons"][case["id"]] = reason or "unsupported template"

    for layer in execution_layers.values():
        _finish_layer(layer)
    report = {
        "schema_version": PRODUCTIVE_CORPUS_EXECUTION_SCHEMA_VERSION,
        "kind": PRODUCTIVE_CORPUS_EXECUTION_KIND,
        "source_corpus_sha256": corpus["sha256"],
        "total_cases": len(cases),
        "attempted_cases": sum(item["attempted"] for item in per_kind.values()),
        "execution_layers": execution_layers,
        "per_kind": per_kind,
        "per_family": per_family,
        "failures": failures,
        "passed": all(layer["status"] == "PASSED" for layer in execution_layers.values()),
    }
    validate_productive_corpus_execution(report)
    return report


def _validate_counts(value: Any, *, label: str) -> None:
    if not isinstance(value, dict) or set(value) != {"total", "attempted", "passed", "failed", "unmeasured"}:
        raise ValueError(f"{label} counts do not match schema")
    if any(not isinstance(value[key], int) or isinstance(value[key], bool) or value[key] < 0 for key in value):
        raise ValueError(f"{label} counts must be non-negative integers")
    if value["attempted"] != value["passed"] + value["failed"]:
        raise ValueError(f"{label} attempted count does not reconcile")
    if value["total"] != value["attempted"] + value["unmeasured"]:
        raise ValueError(f"{label} total does not reconcile")


def validate_productive_corpus_execution(report: dict[str, Any]) -> None:
    """Strictly validate a runner report, including all aggregate invariants."""
    fields = {
        "schema_version", "kind", "source_corpus_sha256", "total_cases", "attempted_cases",
        "execution_layers", "per_kind", "per_family", "failures", "passed",
    }
    if not isinstance(report, dict) or set(report) != fields:
        raise ValueError("execution report top-level fields do not match schema")
    if report["schema_version"] != PRODUCTIVE_CORPUS_EXECUTION_SCHEMA_VERSION:
        raise ValueError("unsupported execution report schema version")
    if report["kind"] != PRODUCTIVE_CORPUS_EXECUTION_KIND:
        raise ValueError("unexpected execution report kind")
    expected_sha = load_productive_corpus()["sha256"]
    if report["source_corpus_sha256"] != expected_sha:
        raise ValueError("source_corpus_sha256 does not match committed corpus")
    if report["total_cases"] != 1360:
        raise ValueError("total_cases must be 1360")
    if report["attempted_cases"] != 1360:
        raise ValueError("attempted_cases must match all executed corpus cases")
    if not isinstance(report["passed"], bool):
        raise ValueError("passed must be boolean")

    layers = report["execution_layers"]
    if not isinstance(layers, dict) or set(layers) != set(_LAYER_NAMES):
        raise ValueError("execution_layers do not match schema")
    layer_fields = {
        "status", "total", "attempted", "passed", "failed", "unmeasured",
        "failure_ids", "unmeasured_ids", "unmeasured_reasons", "reason",
    }
    for name, layer in layers.items():
        if not isinstance(layer, dict) or set(layer) != layer_fields:
            raise ValueError(f"{name} layer fields do not match schema")
        _validate_counts({key: layer[key] for key in ("total", "attempted", "passed", "failed", "unmeasured")}, label=name)
        if layer["status"] not in {"PASSED", "FAILED", "UNMEASURED"}:
            raise ValueError(f"{name} layer status is invalid")
        if layer["total"] == 0 and layer["status"] != "UNMEASURED":
            raise ValueError(f"{name} empty layer must be UNMEASURED")
        if layer["failed"] != len(layer["failure_ids"]):
            raise ValueError(f"{name} failure_ids do not match failed count")
        if layer["unmeasured"] != len(layer["unmeasured_ids"]):
            raise ValueError(f"{name} unmeasured_ids do not match count")
        if set(layer["unmeasured_reasons"]) != set(layer["unmeasured_ids"]):
            raise ValueError(f"{name} unmeasured reasons do not match ids")
        if len(set(layer["failure_ids"])) != len(layer["failure_ids"]):
            raise ValueError(f"{name} failure_ids are not unique")
        if len(set(layer["unmeasured_ids"])) != len(layer["unmeasured_ids"]):
            raise ValueError(f"{name} unmeasured_ids are not unique")
        expected_status = "FAILED" if layer["failed"] else "UNMEASURED" if layer["unmeasured"] or not layer["total"] else "PASSED"
        if layer["status"] != expected_status:
            raise ValueError(f"{name} status does not match counts")
    expected_layer_totals = {
        "python_applications": 960,
        "merlo_compiler": 400,
        "concise_application": 0,
        "canonical": 0,
        "hir": 0,
        "rir": 0,
        "mir": 0,
        "native": 0,
    }
    for name, expected_total in expected_layer_totals.items():
        if layers[name]["total"] != expected_total:
            raise ValueError(f"{name} total does not match execution contract")
    if sum(layer["attempted"] for layer in layers.values()) != 1360:
        raise ValueError("execution layer attempts do not match executed cases")

    if not isinstance(report["per_kind"], dict):
        raise ValueError("per_kind must be an object")

    expected_kind_totals = {"ndjson": 320, "csv": 320, "grep": 320, "merlo": 400}
    if set(report["per_kind"]) != set(expected_kind_totals):
        raise ValueError("per_kind keys do not match corpus kinds")
    for kind, expected_total in expected_kind_totals.items():
        _validate_counts(report["per_kind"][kind], label=f"per_kind[{kind}]")
        if report["per_kind"][kind]["total"] != expected_total:
            raise ValueError(f"per_kind[{kind}] total does not match corpus")
    if set(report["per_family"]) != set(expected_kind_totals):
        raise ValueError("per_family kinds do not match corpus kinds")
    corpus = load_productive_corpus()
    expected_families = {
        kind: sorted({case["family"] for case in corpus["cases"] if case["kind"] == kind})
        for kind in expected_kind_totals
    }
    for kind, families in expected_families.items():
        observed = report["per_family"][kind]
        if set(observed) != set(families):
            raise ValueError(f"per_family[{kind}] families do not match corpus")
        family_totals = {family: 0 for family in families}
        for case in corpus["cases"]:
            if case["kind"] == kind:
                family_totals[case["family"]] += 1
        for family, counts in observed.items():
            _validate_counts(counts, label=f"per_family[{kind}][{family}]")
            if counts["total"] != family_totals[family]:
                raise ValueError(f"per_family[{kind}][{family}] total does not match corpus")
        for field in ("total", "attempted", "passed", "failed", "unmeasured"):
            if sum(counts[field] for counts in observed.values()) != report["per_kind"][kind][field]:
                raise ValueError(f"per_family[{kind}] does not reconcile with per_kind")
    if sum(counts["attempted"] for counts in report["per_kind"].values()) != 1360:
        raise ValueError("per_kind attempts do not match executed cases")
    if not isinstance(report["failures"], list):
        raise ValueError("failures must be a list")
    failure_fields = {"id", "kind", "family", "layer", "reason"}
    for item in report["failures"]:
        if not isinstance(item, dict) or set(item) != failure_fields:
            raise ValueError("failure record fields do not match schema")
        if item["layer"] not in {"python_applications", "merlo_compiler"}:
            raise ValueError("failure record layer is invalid")
        if not all(isinstance(item[field], str) and item[field] for field in failure_fields):
            raise ValueError("failure record values must be non-empty strings")
    failure_ids = [item["id"] for item in report["failures"]]
    if len(set(failure_ids)) != len(failure_ids):
        raise ValueError("failures must contain unique ids")
    layer_failures = set().union(*(set(layer["failure_ids"]) for layer in layers.values()))
    if layer_failures != set(failure_ids):
        raise ValueError("failures do not match layer failure_ids")
    expected_passed = all(layer["status"] == "PASSED" for layer in layers.values())
    if report["passed"] != expected_passed:
        raise ValueError("passed does not match per-layer statuses")


__all__ = [
    "PRODUCTIVE_CORPUS_EXECUTION_KIND",
    "PRODUCTIVE_CORPUS_EXECUTION_SCHEMA_VERSION",
    "run_productive_corpus_execution",
    "validate_productive_corpus_execution",
]
