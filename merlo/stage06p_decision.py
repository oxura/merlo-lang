"""Evidence-only decision gate for Meldra Stage 0.6P."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from .stage06p_protocol import STAGE06P_DECISIONS


STAGE06P_DECISION_SCHEMA_VERSION = 2
STAGE06P_DECISION_FILENAME = "meldra_stage06p_decision.json"

_REQUIRED = {
    "protocol": "meldra_stage06p_protocol.json",
    "preflight": "meldra_stage06p_stage05p_audit.json",
    "correctness": "meldra_stage06p_correctness.json",
    "sanitizers": "meldra_stage06p_sanitizers.json",
    "benchmark": "meldra_stage06p_benchmark.json",
    "memory": "meldra_stage06p_memory.json",
    "extended": "meldra_stage06p_extended.json",
    "external": "meldra_stage06p_external_corpus.json",
    "ergonomics": "meldra_stage06p_ergonomics.json",
    "determinism": "meldra_stage06p_determinism.json",
    "compile_phases": "meldra_stage06p_compile_phases.json",
    "optimizer": "meldra_stage06p_optimizer.json",
    "codegen": "meldra_stage06p_codegen.json",
    "validation": "meldra_stage06p_validation.json",
    "leak_soak": "stage06p_leak_soak/report.json",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _geometric_mean(values: Iterable[float]) -> float | None:
    measured = tuple(value for value in values if value > 0 and math.isfinite(value))
    if not measured:
        return None
    return math.exp(sum(math.log(value) for value in measured) / len(measured))


def _gate(
    name: str,
    passed: bool,
    observed: Any,
    threshold: Any,
    evidence: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "observed": observed,
        "threshold": threshold,
        "evidence": evidence,
    }


def build_stage06p_decision(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root)
    benchmark_root = root_path / "benchmarks"
    paths = {name: benchmark_root / relative for name, relative in _REQUIRED.items()}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing Stage 0.6P evidence: " + ", ".join(missing))
    evidence = {name: _load(path) for name, path in paths.items()}
    correctness = evidence["correctness"]
    sanitizers = evidence["sanitizers"]
    benchmark = evidence["benchmark"]
    memory = evidence["memory"]
    extended = evidence["extended"]
    external = evidence["external"]
    ergonomics = evidence["ergonomics"]
    determinism = evidence["determinism"]
    validation = evidence["validation"]
    leak_soak = evidence["leak_soak"]
    optimizer = evidence["optimizer"]
    codegen = evidence["codegen"]

    ratio_by_workload = {
        item["workload"]: item["meldra_over_c"]
        for item in benchmark["meldra_over_c_ratios"]
    }
    unique_numeric_workloads = (
        "arithmetic_lcg",
        "fixed_array_scan",
        "map_filter_fold",
        "bubble_sort_8",
    )
    unique_numeric_geomean = _geometric_mean(
        ratio_by_workload[item]
        for item in unique_numeric_workloads
        if item in ratio_by_workload
    )
    common_failures = benchmark["correctness_failures"]
    key_languages = ("c", "rust", "go")
    key_baselines_measured = all(
        any(
            item["language"] == language and item["status"] == "MEASURED"
            for item in benchmark["observations"]
        )
        for language in key_languages
    )
    shared_ratio = ratio_by_workload.get("shared_allocations")
    memory_by_name = {item["name"]: item for item in memory["arms"]}
    meldra_lifetime_annotations = ergonomics["aggregates"]["meldra"][
        "median_lifetime_annotations"
    ]
    meldra_extended = [
        item
        for item in extended["observations"]
        if item["language"] == "meldra"
    ]
    extended_status = {item["workload"]: item["status"] for item in meldra_extended}

    differential_mismatches = (
        correctness["valid"]["interpreter_mismatch_count"]
        + correctness["valid"]["observation_mismatch_count"]
        + correctness["valid"]["native_mismatches"]
        + correctness["valid"].get("ownership_balance_failure_count", 0)
    )
    optimizer_mismatches = correctness["valid"]["per_pass_mismatch_count"]
    invalid_failures = correctness["invalid"]["failure_count"]
    sanitizer_violations = (
        len(sanitizers["valid_failures"])
        + len(sanitizers["unexpected_negative_cases"])
        + len(sanitizers.get("signed_integer_contract_failures", ()))
        + len(sanitizers.get("ownership_contract_failures", ()))
    )
    gates = [
        _gate(
            "differential_mismatches",
            differential_mismatches == 0,
            differential_mismatches,
            0,
            _REQUIRED["correctness"],
        ),
        _gate(
            "optimizer_semantic_mismatches",
            optimizer_mismatches == 0,
            optimizer_mismatches,
            0,
            _REQUIRED["correctness"],
        ),
        _gate(
            "invalid_program_acceptance_failures",
            invalid_failures == 0,
            invalid_failures,
            0,
            _REQUIRED["correctness"],
        ),
        _gate(
            "sanitizer_violations",
            sanitizer_violations == 0 and sanitizers["status"] == "PASS",
            sanitizer_violations,
            0,
            _REQUIRED["sanitizers"],
        ),
        _gate(
            "unique_numeric_array_geomean",
            unique_numeric_geomean is not None and unique_numeric_geomean <= 1.10,
            unique_numeric_geomean,
            "<= 1.10x C",
            _REQUIRED["benchmark"],
        ),
        _gate(
            "benchmark_runtime_calibration",
            benchmark["calibration_target_or_stable"],
            {
                item["workload"]: {
                    "median_ms": item["median_ms"],
                    "stable": item["stable"],
                    "target_met": item["target_met"],
                }
                for item in benchmark["calibration"]
            },
            "C median 200-500 ms or C/Meldra relative MAD <= 5%",
            _REQUIRED["benchmark"],
        ),
        _gate(
            "text_bytes_native_ratio",
            extended_status.get("text_bytes_utf8") == "MEASURED",
            extended_status.get("text_bytes_utf8"),
            "MEASURED and <= 1.25x strong native baseline",
            _REQUIRED["extended"],
        ),
        _gate(
            "recursive_values_native_ratio",
            extended_status.get("recursive_values") == "MEASURED",
            extended_status.get("recursive_values"),
            "MEASURED and <= 1.25x strong native baseline",
            _REQUIRED["extended"],
        ),
        _gate(
            "shared_workload_improves_stage05p",
            shared_ratio is not None and shared_ratio < 1.698,
            shared_ratio,
            "< 1.698x C",
            _REQUIRED["benchmark"],
        ),
        _gate(
            "ordinary_source_lifetime_annotations",
            meldra_lifetime_annotations == 0,
            meldra_lifetime_annotations,
            0,
            _REQUIRED["ergonomics"],
        ),
        _gate(
            "hidden_double_free_or_leak",
            leak_soak["status"] == "PASS"
            and not leak_soak["sanitizer_failure"],
            {
                "status": leak_soak["status"],
                "sanitizer_failure": leak_soak["sanitizer_failure"],
                "duration_seconds": leak_soak["duration_observed_seconds"],
            },
            "zero over >= 7200 seconds",
            _REQUIRED["leak_soak"],
        ),
        _gate(
            "interfaces_measured",
            extended_status.get("interface_dispatch") == "MEASURED",
            extended_status.get("interface_dispatch"),
            "MEASURED",
            _REQUIRED["extended"],
        ),
        _gate(
            "deterministic_builds",
            determinism["all_mir_equal"]
            and determinism["all_c_equal"]
            and determinism["all_binary_equal"],
            {
                "mir": determinism["all_mir_equal"],
                "c": determinism["all_c_equal"],
                "binary": determinism["all_binary_equal"],
            },
            True,
            _REQUIRED["determinism"],
        ),
        _gate(
            "required_native_baselines",
            key_baselines_measured,
            list(key_languages) if key_baselines_measured else "incomplete",
            list(key_languages),
            _REQUIRED["benchmark"],
        ),
        _gate(
            "external_project_count",
            external["scanned_project_count"] >= 5
            and not external["kernel_failures"],
            {
                "projects": external["scanned_project_count"],
                "kernel_failures": len(external["kernel_failures"]),
            },
            ">=5 projects and zero kernel failures",
            _REQUIRED["external"],
        ),
        _gate(
            "human_simplicity_measured",
            ergonomics["human_trial"]["status"] == "MEASURED",
            ergonomics["human_trial"]["status"],
            "MEASURED",
            _REQUIRED["ergonomics"],
        ),
        _gate(
            "ai_productivity_measured",
            ergonomics["ai_trial"]["status"] == "MEASURED",
            ergonomics["ai_trial"]["status"],
            "MEASURED",
            _REQUIRED["ergonomics"],
        ),
        _gate(
            "full_regression_and_artifact_validation",
            validation["status"] == "PASS",
            validation["status"],
            "PASS",
            _REQUIRED["validation"],
        ),
        _gate(
            "optimizer_positive_negative_evidence",
            optimizer["status"] == "PASS"
            and optimizer["pass_count"] == len(optimizer["passes"]),
            {
                "status": optimizer["status"],
                "passes": optimizer["pass_count"],
                "failures": len(optimizer["failures"]),
            },
            "every pass has passing positive/negative evidence",
            _REQUIRED["optimizer"],
        ),
        _gate(
            "codegen_assembly_records",
            codegen["status"] == "PASS"
            and codegen["workload_count"] >= 8,
            {
                "status": codegen["status"],
                "workloads": codegen["workload_count"],
                "failures": len(codegen["failures"]),
            },
            "assembly and optimization records for representative native kernels",
            _REQUIRED["codegen"],
        ),
    ]
    failed_gates = [item["name"] for item in gates if not item["passed"]]
    safety_gate_names = {
        "differential_mismatches",
        "optimizer_semantic_mismatches",
        "invalid_program_acceptance_failures",
        "sanitizer_violations",
        "hidden_double_free_or_leak",
        "deterministic_builds",
        "full_regression_and_artifact_validation",
        "optimizer_positive_negative_evidence",
    }
    safety_correctness_gates = [
        item for item in gates if item["name"] in safety_gate_names
    ]
    if not all(item["passed"] for item in safety_correctness_gates):
        decision = "NO_GO_NATIVE_LANGUAGE"
        rationale = "A correctness, safety, determinism, or regression gate failed."
    elif not failed_gates:
        decision = "GO_NATIVE_CORE_EXPANSION"
        rationale = "Every preregistered performance, scope, safety, and evidence gate passed."
    else:
        decision = "CONTINUE_PERFORMANCE_RESEARCH"
        rationale = (
            "The common numeric subset is correct and competitive, but Text/Bytes, true recursive values, "
            "closed interfaces, and independent human/model productivity evidence remain unmeasured or unsupported."
        )
    if decision not in STAGE06P_DECISIONS:
        raise AssertionError(decision)

    artifact_hashes = {
        name: {
            "path": str(path.relative_to(root_path)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for name, path in paths.items()
    }
    return {
        "schema_version": STAGE06P_DECISION_SCHEMA_VERSION,
        "kind": "MeldraStage06PDecision",
        "decision": decision,
        "rationale": rationale,
        "gates": gates,
        "failed_gates": failed_gates,
        "measurements": {
            "valid_generated_programs": correctness["valid"]["valid_programs"],
            "invalid_generated_programs": correctness["invalid"]["invalid_programs"],
            "native_generated_results": correctness["valid"]["native_results"],
            "sanitizer_valid_programs": sanitizers["valid_generated_programs"],
            "sanitizer_negative_cases": sanitizers["negative_detected"],
            "meldra_over_c_compute_geomean": benchmark[
                "meldra_over_c_compute_geometric_mean"
            ],
            "unique_numeric_array_geomean": unique_numeric_geomean,
            "meldra_over_c_memory_p95_geomean": benchmark[
                "meldra_over_c_memory_p95_geometric_mean"
            ],
            "shared_meldra_over_c": shared_ratio,
            "memory_models": {
                name: {
                    "runtime_over_c_manual": item["runtime_over_c_manual"],
                    "algorithm_allocations": item["algorithm_allocations"],
                }
                for name, item in memory_by_name.items()
            },
            "external_projects": external["scanned_project_count"],
            "external_functions": external["total_functions"],
            "external_executed_kernels": external["selected_kernel_count"],
            "human_trial": ergonomics["human_trial"]["status"],
            "ai_trial": ergonomics["ai_trial"]["status"],
            "leak_soak_seconds": leak_soak["duration_observed_seconds"],
        },
        "supported_operations": [
            "Int64 UInt64 Float32 Float64 Bool scalar semantics",
            "records and fixed arrays",
            "direct pure calls",
            "branches, while loops, range loops, early returns",
            "moves and inferred shared/unique borrows",
            "bounds checks with proven elimination",
            "SharedRc fallback and non-escaping allocation elision",
            "portable deterministic C11 backend",
        ],
        "unsupported_operations": [
            "native Text and Bytes",
            "true recursive pointer values and recursive enums",
            "closed interfaces and devirtualization",
            "cycle collection",
            "foreign ownership beyond declared boundaries",
            "threads, async runtime, networking, database, package registry, UI, GPU, LLVM backend",
        ],
        "known_limitations": [
            "The frozen Meldra surface cannot express Shared clones, so Meldra retain-heavy alias traffic is not measured.",
            "Text/Bytes, recursive values, and interface scenarios are measured only in external baseline languages.",
            "Human usability and AI productivity remain explicitly UNMEASURED; static source metrics are not substitutes.",
            "External-project evidence is syntax coverage plus pure executable kernels, not whole-project compilation.",
            "Runtime-internal allocations for GC languages are not claimed.",
        ],
        "next_stage_proposal": {
            "implemented": False,
            "name": "Stage 0.6R — Representation Completion",
            "entry_conditions": [
                "Add versioned Native HIR/MIR forms for Text/Bytes, recursive indirection, and closed interfaces.",
                "Retain the current differential, sanitizer, deterministic-build, and randomized benchmark gates.",
                "Recruit real users and provision one fixed real model API before usability/productivity claims.",
            ],
        },
        "artifact_hashes": artifact_hashes,
        "common_benchmark_correctness_failures": len(common_failures),
    }


def write_stage06p_decision(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root)
    result = build_stage06p_decision(root_path)
    destination = root_path / "benchmarks" / STAGE06P_DECISION_FILENAME
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


__all__ = [
    "STAGE06P_DECISION_FILENAME",
    "STAGE06P_DECISION_SCHEMA_VERSION",
    "build_stage06p_decision",
    "write_stage06p_decision",
]
