"""Pure Productive Core decision-report construction and integrity checks.

The module consumes evidence that has already been produced.  It deliberately
never runs a compiler, benchmark, sanitizer, test suite, or external process.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
PRODUCTIVE_CORE_CONTRACT = "merlo.productive-core-milestone.v1"
PRODUCTIVE_CORE_SUPPORTED = "PRODUCTIVE_CORE_SUPPORTED"
PRODUCTIVE_CORE_INCOMPLETE = "PRODUCTIVE_CORE_INCOMPLETE"
PRODUCTIVE_CORE_SAFETY_DEFECT = "PRODUCTIVE_CORE_SAFETY_DEFECT"
SUPPORTED = PRODUCTIVE_CORE_SUPPORTED
INCOMPLETE = PRODUCTIVE_CORE_INCOMPLETE
SAFETY_DEFECT = PRODUCTIVE_CORE_SAFETY_DEFECT
_ALLOWED_STATUSES = {
    PRODUCTIVE_CORE_SUPPORTED,
    PRODUCTIVE_CORE_INCOMPLETE,
    PRODUCTIVE_CORE_SAFETY_DEFECT,
}

_REQUIRED_SECTIONS = (
    "corpus",
    "external_fixtures",
    "map_evidence",
    "resources",
    "cli",
    "applications",
    "safety",
    "falsification",
    "performance",
    "simplicity",
    "ai_change_corpus",
    "full_suite",
)
_REQUIRED_GATES = (
    *_REQUIRED_SECTIONS,
    "predecessors",
    "cache_policy",
    "no_old_microbenchmarks",
    "no_python_sidecar_benchmarks",
    "decision_run_count",
)
_PREDECESSORS = {
    "concise_application_alpha": "benchmarks/merlo_concise_application_alpha.json",
    "general_representation": "benchmarks/merlo_general_representation_core.json",
}
_DISALLOWED_STATUSES = {
    "UNMEASURED",
    "UNSUPPORTED",
    "INCOMPLETE",
    "NOT_EXECUTED",
    "BLOCKED",
    "FAILED",
    "FAIL",
    "ERROR",
}
_CACHE_POLICY = {
    "kind": "content_addressed",
    "reuse_requires_matching_payload_sha256": True,
    "input_change_requires_recompute": True,
    "overwrite": "identical_payload_only",
}
_REPORT_WRITTEN_ONCE = {
    "enabled": True,
    "overwrite": "refuse_unless_identical_payload",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def canonical_payload_hash(report: Mapping[str, Any]) -> str:
    """Return SHA-256 of canonical report JSON without its own digest field."""
    payload = dict(report)
    payload.pop("artifact_payload_sha256", None)
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def productive_report_sha256(report: Mapping[str, Any]) -> str:
    """Compatibility spelling for the canonical report payload digest."""
    return canonical_payload_hash(report)


def _json_copy(value: Any, label: str) -> Any:
    try:
        copied = copy.deepcopy(value)
        _canonical_json(copied)
        return copied
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON-serializable") from exc


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return value


def _bad_statuses(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"status", "execution_status", "trial_execution_status"}:
                if isinstance(item, str) and item.upper() in _DISALLOWED_STATUSES:
                    return True
            if _bad_statuses(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_bad_statuses(item) for item in value)
    return False


def _validated_safety_evidence(value: Mapping[str, Any]) -> bool:
    if value.get("status") != "PASSED":
        return False
    invariants = value.get("invariants")
    aggregate_proofs = value.get("aggregate_proofs")
    return (
        isinstance(invariants, Mapping)
        and bool(invariants)
        and all(item == "PASSED" for item in invariants.values())
        and isinstance(aggregate_proofs, Mapping)
        and aggregate_proofs.get("all_relevant_executable_checks") is True
    )


def _passed_evidence(value: Any, *, validated_safety: bool = False) -> bool:
    if not isinstance(value, Mapping):
        return False
    if "passed" in value and value.get("passed") is not True:
        return False
    if "gates" in value:
        gates = value.get("gates")
        if not isinstance(gates, Mapping) or not all(item is True for item in gates.values()):
            return False
    if value.get("passed") is not True:
        gates = value.get("gates")
        if not (
            isinstance(gates, Mapping)
            and gates
            or validated_safety and _validated_safety_evidence(value)
        ):
            return False
    return not _bad_statuses(value)


def _validated_section(value: Any, section: str) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        if section == "safety" and value.get("kind") == "merlo.productive-safety":
            from .productive_safety import validate_productive_safety_report

            validate_productive_safety_report(value)
        elif section == "performance" and {
            "protocol",
            "frozen_workloads",
            "algorithms",
            "applications",
        }.issubset(value):
            from .productive_performance import validate_productive_performance_report

            validate_productive_performance_report(value)
    except (TypeError, ValueError):
        return False
    return True


def _corpus_gate(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    # The generated corpus is a plan artifact.  It is not execution evidence.
    plan = value.get("plan")
    execution = value.get("execution")
    if not isinstance(plan, Mapping) or not isinstance(execution, Mapping):
        return False
    return execution.get("passed") is True and not _bad_statuses(execution)


def _ai_gate(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    tasks = value.get("tasks")
    return (
        value.get("trial_execution_status") == "NOT_EXECUTED"
        and isinstance(tasks, list)
        and len(tasks) == 18
    )


def _safety_evidence_present(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    for key in ("records", "executions", "runs", "invariants", "checks"):
        item = value.get(key)
        if isinstance(item, (list, tuple, Mapping)) and len(item) > 0:
            return True
    return bool(value.get("executed") is True or value.get("execution_count", 0))


def _safety_defect(value: Any, executed: bool = False) -> bool:
    if isinstance(value, Mapping):
        status = value.get("status")
        status_text = status.upper() if isinstance(status, str) else ""
        command = value.get("command")
        has_command = isinstance(command, (list, tuple)) and bool(command)
        has_sanitizer = value.get("sanitizer") not in (None, False, "")
        failed_record_execution = (
            status_text in {"FAILED", "FAIL", "ERROR"}
            and has_sanitizer
            and has_command
            and "exit_code" in value
            and value.get("exit_code") is not None
        )
        if failed_record_execution:
            return True
        local_executed = executed or value.get("executed") is True
        if value.get("execution_status") in {"MEASURED", "PASSED", "FAILED", "EXECUTED"}:
            local_executed = True
        if status_text in {"SAFETY_DEFECT", "DEFECT", "MEMORY_DEFECT", "FD_DEFECT", "SANITIZER_DEFECT"}:
            if local_executed or value.get("executed") is not False:
                return True
        instrumented = any(
            value.get(key) not in (None, False, "", "none", "NONE", 0)
            for key in ("sanitizer", "sanitizers", "fd_check", "fd_checks", "memory_check", "memory_checks")
        )
        if local_executed and instrumented and status_text in {"FAILED", "FAIL", "ERROR"}:
            return True
        for key in ("defect", "safety_defect", "sanitizer_defect", "fd_defect", "memory_defect"):
            item = value.get(key)
            if local_executed and item not in (None, False, "", 0, [], {}):
                return True
        failures = value.get("failures")
        if local_executed and failures not in (None, False, 0, "", [], {}):
            return True
        return any(_safety_defect(item, local_executed) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_safety_defect(item, executed) for item in value)
    return False


def _zero_policy(value: Any, names: set[str]) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in names:
                if item is True:
                    return False
                if isinstance(item, (int, float)) and not isinstance(item, bool) and item != 0:
                    return False
                if item not in (None, False, 0, "", [], {}):
                    return False
            if not _zero_policy(item, names):
                return False
        return True
    if isinstance(value, (list, tuple)):
        return all(_zero_policy(item, names) for item in value)
    return True




def _predecessor_snapshot(root: str | Path) -> dict[str, dict[str, Any]]:
    root_path = Path(root).resolve()
    result: dict[str, dict[str, Any]] = {}
    for name, relative in _PREDECESSORS.items():
        path = root_path / relative
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result[name] = {"path": relative, "exists": True, "sha256": digest, "valid": True}
        else:
            result[name] = {"path": relative, "exists": False, "sha256": None, "valid": False}
    return result


def _gates(report: Mapping[str, Any], root: str | Path) -> dict[str, bool]:
    gates: dict[str, bool] = {
        "corpus": _corpus_gate(report.get("corpus")),
        "external_fixtures": _passed_evidence(report.get("external_fixtures")),
        "map_evidence": _passed_evidence(report.get("map_evidence")),
        "resources": _passed_evidence(report.get("resources")),
        "cli": _passed_evidence(report.get("cli")),
        "applications": _passed_evidence(report.get("applications")),
        "safety": (
            _safety_evidence_present(report.get("safety"))
            and _passed_evidence(report.get("safety"), validated_safety=True)
            and _validated_section(report.get("safety"), "safety")
        ),
        "falsification": _passed_evidence(report.get("falsification")),
        "performance": (
            _passed_evidence(report.get("performance"))
            and _validated_section(report.get("performance"), "performance")
        ),
        "simplicity": _passed_evidence(report.get("simplicity")),
        "ai_change_corpus": _ai_gate(report.get("ai_change_corpus")),
        "full_suite": _passed_evidence(report.get("full_suite")),
        "predecessors": False,
        "cache_policy": report.get("cache_policy") == _CACHE_POLICY,
        "no_old_microbenchmarks": False,
        "no_python_sidecar_benchmarks": False,
        "decision_run_count": report.get("decision_run_count") == 1,
    }
    expected_predecessors = _predecessor_snapshot(root)
    actual_predecessors = report.get("predecessors")
    gates["predecessors"] = actual_predecessors == expected_predecessors and all(
        item["exists"] and item["valid"] for item in expected_predecessors.values()
    )
    performance = report.get("performance")
    old_names = {
        "old_microbenchmarks",
        "old_microbenchmark_runs",
        "prior_microbenchmark_runs",
        "timing_benchmark_runs",
        "prior_bytes_benchmark_runs",
    }
    python_names = {
        "python_sidecar_benchmarks",
        "python_sidecar_benchmark_runs",
        "external_python_benchmark_runs",
        "prior_python_benchmark_runs",
    }
    gates["no_old_microbenchmarks"] = _zero_policy(performance, old_names)
    gates["no_python_sidecar_benchmarks"] = _zero_policy(performance, python_names)
    return gates


def _select_status(report: Mapping[str, Any], gates: Mapping[str, bool]) -> str:
    if _safety_defect(report.get("safety")):
        return PRODUCTIVE_CORE_SAFETY_DEFECT
    return PRODUCTIVE_CORE_SUPPORTED if all(gates.values()) else PRODUCTIVE_CORE_INCOMPLETE


def build_productive_report(
    *,
    root: str | Path = ".",
    corpus: Mapping[str, Any],
    external_fixtures: Mapping[str, Any],
    map_evidence: Mapping[str, Any],
    resources: Mapping[str, Any],
    cli: Mapping[str, Any],
    applications: Mapping[str, Any],
    safety: Mapping[str, Any],
    falsification: Mapping[str, Any],
    performance: Mapping[str, Any],
    simplicity: Mapping[str, Any],
    ai_change_corpus: Mapping[str, Any],
    full_suite: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble a report from supplied evidence without executing anything."""
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "contract": PRODUCTIVE_CORE_CONTRACT,
        "corpus": _json_copy(corpus, "corpus"),
        "external_fixtures": _json_copy(external_fixtures, "external_fixtures"),
        "map_evidence": _json_copy(map_evidence, "map_evidence"),
        "resources": _json_copy(resources, "resources"),
        "cli": _json_copy(cli, "cli"),
        "applications": _json_copy(applications, "applications"),
        "safety": _json_copy(safety, "safety"),
        "falsification": _json_copy(falsification, "falsification"),
        "performance": _json_copy(performance, "performance"),
        "simplicity": _json_copy(simplicity, "simplicity"),
        "ai_change_corpus": _json_copy(ai_change_corpus, "ai_change_corpus"),
        "full_suite": _json_copy(full_suite, "full_suite"),
        "predecessors": _predecessor_snapshot(root),
        "decision_run_count": 1,
        "cache_policy": dict(_CACHE_POLICY),
        "report_written_once": dict(_REPORT_WRITTEN_ONCE),
    }
    report["gates"] = _gates(report, root)
    report["status"] = _select_status(report, report["gates"])
    report["artifact_payload_sha256"] = canonical_payload_hash(report)
    validate_productive_report(report, root=root)
    return report


def validate_productive_report(report: Mapping[str, Any], root: str | Path = ".") -> None:
    """Reject missing, unsupported, stale, internally inconsistent, or tampered reports."""
    if not isinstance(report, Mapping):
        raise ValueError("productive report must be an object")
    expected_keys = {
        "schema_version",
        "contract",
        "status",
        "gates",
        *_REQUIRED_SECTIONS,
        "predecessors",
        "decision_run_count",
        "cache_policy",
        "report_written_once",
        "artifact_payload_sha256",
    }
    if set(report) != expected_keys:
        raise ValueError("productive report sections are incomplete or unexpected")
    if report.get("schema_version") != SCHEMA_VERSION or report.get("contract") != PRODUCTIVE_CORE_CONTRACT:
        raise ValueError("invalid productive report schema or contract")
    if report.get("status") not in _ALLOWED_STATUSES:
        raise ValueError("invalid productive core status")
    for section in _REQUIRED_SECTIONS:
        _mapping(report.get(section), section)
    gates = report.get("gates")
    if not isinstance(gates, Mapping) or set(gates) != set(_REQUIRED_GATES) or any(type(item) is not bool for item in gates.values()):
        raise ValueError("productive decision gates are incomplete")
    if report.get("decision_run_count") != 1:
        raise ValueError("decision_run_count must be exactly one")
    if report.get("cache_policy") != _CACHE_POLICY:
        raise ValueError("cache policy mismatch")
    if report.get("report_written_once") != _REPORT_WRITTEN_ONCE:
        raise ValueError("report_written_once policy mismatch")
    if report.get("artifact_payload_sha256") != canonical_payload_hash(report):
        raise ValueError("productive report payload hash mismatch")
    expected_gates = _gates(report, root)
    if dict(gates) != expected_gates:
        raise ValueError("productive decision gates are stale")
    if report.get("status") != _select_status(report, expected_gates):
        raise ValueError("productive core status disagrees with evidence gates")


def _validation_root(destination: Path) -> Path:
    if destination.parent.name == "benchmarks":
        return destination.parent.parent
    return destination.parent


def write_productive_report_once(
    report: Mapping[str, Any],
    path: str | Path = "benchmarks/merlo_productive_core.json",
) -> dict[str, Any]:
    """Write once; an existing destination is accepted only for identical payload."""
    destination = Path(path)
    validate_productive_report(report, root=_validation_root(destination))
    payload = dict(report)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        try:
            existing = json.loads(destination.read_text(encoding="utf-8"))
            validate_productive_report(existing, root=_validation_root(destination))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise FileExistsError(f"refusing existing productive report: {destination}") from exc
        if canonical_payload_hash(existing) != canonical_payload_hash(payload):
            raise FileExistsError(f"refusing to overwrite productive report: {destination}")
        return dict(existing)
    try:
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError as exc:
        raise FileExistsError(f"refusing to overwrite productive report: {destination}") from exc
    return dict(payload)


__all__ = [
    "PRODUCTIVE_CORE_CONTRACT",
    "PRODUCTIVE_CORE_SUPPORTED",
    "PRODUCTIVE_CORE_INCOMPLETE",
    "PRODUCTIVE_CORE_SAFETY_DEFECT",
    "SUPPORTED",
    "INCOMPLETE",
    "SAFETY_DEFECT",
    "canonical_payload_hash",
    "productive_report_sha256",
    "build_productive_report",
    "validate_productive_report",
    "write_productive_report_once",
]
