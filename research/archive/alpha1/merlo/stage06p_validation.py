"""Cross-artifact consistency checks for Stage 0.6P evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from merlo.native_c_backend import (
    NATIVE_BACKEND_IMPLEMENTATION_VERSION,
    NATIVE_BACKEND_SCHEMA_VERSION,
)
from research.archive.alpha1.merlo.native_hir import NATIVE_HIR_SCHEMA_VERSION
from tools.benchmarks.merlo.performance_frontend import (
    PERFORMANCE_FRONTEND_IMPLEMENTATION_VERSION,
    PERFORMANCE_FRONTEND_SCHEMA_VERSION,
)
from merlo.performance_mir import PERFORMANCE_MIR_SCHEMA_VERSION
from tools.benchmarks.merlo.performance_opt import (
    OPTIMIZATION_PASS_VERSIONS,
    PERFORMANCE_MEMORY_MODEL_VERSION,
)
from typing import Any


STAGE06P_VALIDATION_SCHEMA_VERSION = 2
STAGE06P_VALIDATION_FILENAME = "meldra_stage06p_validation.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_stage06p_artifacts(
    root: str | Path = ".",
    *,
    pytest_summary: str,
    pytest_passed: int,
    compileall_status: str,
    pyflakes_status: str,
) -> dict[str, Any]:
    root_path = Path(root)
    benchmarks = root_path / "tools" / "benchmarks" / "merlo" / "benchmarks"
    artifact_names = (
        "meldra_stage06p_protocol.json",
        "meldra_stage06p_stage05p_audit.json",
        "meldra_stage06p_correctness.json",
        "meldra_stage06p_sanitizers.json",
        "meldra_stage06p_benchmark.json",
        "meldra_stage06p_memory.json",
        "meldra_stage06p_extended.json",
        "meldra_stage06p_external_corpus.json",
        "meldra_stage06p_ergonomics.json",
        "meldra_stage06p_determinism.json",
        "meldra_stage06p_compile_phases.json",
        "meldra_stage06p_optimizer.json",
        "meldra_stage06p_codegen.json",
        "meldra_stage06p_overlap.json",
    )
    artifacts = {}
    failures = []
    for name in artifact_names:
        path = benchmarks / name
        if not path.is_file():
            failures.append(f"missing:{name}")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"invalid-json:{name}:{exc}")
            continue
        artifacts[name] = payload

    if len(artifacts) == len(artifact_names):
        audit = artifacts["meldra_stage06p_stage05p_audit.json"]
        correctness = artifacts["meldra_stage06p_correctness.json"]
        sanitizers = artifacts["meldra_stage06p_sanitizers.json"]
        benchmark = artifacts["meldra_stage06p_benchmark.json"]
        memory = artifacts["meldra_stage06p_memory.json"]
        extended = artifacts["meldra_stage06p_extended.json"]
        external = artifacts["meldra_stage06p_external_corpus.json"]
        ergonomics = artifacts["meldra_stage06p_ergonomics.json"]
        determinism = artifacts["meldra_stage06p_determinism.json"]
        compile_phases = artifacts["meldra_stage06p_compile_phases.json"]
        optimizer = artifacts["meldra_stage06p_optimizer.json"]
        codegen = artifacts["meldra_stage06p_codegen.json"]
        overlap = artifacts["meldra_stage06p_overlap.json"]

        distribution_fields = {
            "median",
            "mean",
            "minimum",
            "p95",
            "standard_deviation",
            "mad",
            "bootstrap_median_95_ci",
        }
        checks = {
            "stage05_measured_checksums": audit["checks"][
                "all_measured_checksums_correct"
            ],
            "valid_program_count": correctness["valid"]["valid_programs"] == 5_000,
            "invalid_program_count": (
                correctness["invalid"]["invalid_programs"] == 2_000
            ),
            "surface_hir_mir_native_mismatches": (
                correctness["valid"]["interpreter_mismatch_count"] == 0
                and correctness["valid"]["observation_mismatch_count"] == 0
                and correctness["valid"]["native_mismatches"] == 0
            ),
            "optimizer_pass_mismatches": (
                correctness["valid"]["per_pass_mismatch_count"] == 0
                and correctness["valid"].get(
                    "ownership_balance_failure_count", 0
                )
                == 0
            ),
            "optimizer_positive_negative_evidence": (
                optimizer["status"] == "PASS"
                and optimizer["pass_count"] == len(OPTIMIZATION_PASS_VERSIONS)
                and {item["pass"] for item in optimizer["passes"]}
                == set(OPTIMIZATION_PASS_VERSIONS)
            ),
            "invalid_diagnostic_failures": (
                correctness["invalid"]["failure_count"] == 0
            ),
            "sanitizers": (
                sanitizers["status"] == "PASS"
                and sanitizers["valid_generated_programs"] == 5_000
                and sanitizers["negative_detected"] == 500
                and not sanitizers["scalar_contract_failures"]
                and {
                    "bool_logic",
                    "wrapping_add",
                    "uint64_wrapping",
                    "float32_rounding",
                    "float32_overflow",
                    "float64_ieee_zero_division",
                }
                <= {
                    item["name"]
                    for item in sanitizers["scalar_contract_cases"]
                    if item["status"] == "PASS"
                }
            ),
            "benchmark_correctness": not benchmark["correctness_failures"],
            "benchmark_full_distributions": all(
                distribution_fields <= set(item["wall_ms"])
                for item in benchmark["observations"]
                if item["status"] == "MEASURED"
            ),
            "benchmark_repetitions": (
                benchmark["protocol"]["repetitions"] >= 30
            ),
            "benchmark_cpu_state": benchmark["environment"][
                "cpu_state_stable"
            ],
            "benchmark_calibration_reported": (
                len(benchmark["calibration"])
                == len(
                    [
                        item
                        for item in benchmark["workloads"]
                        if item["id"] != "startup" and item["meldra_supported"]
                    ]
                )
                and all(
                    item["target_ms"] == [200, 500]
                    and item["stability_relative_mad_max"] == 0.05
                    and item["target_or_stable"]
                    == (item["target_met"] or item["stable"])
                    for item in benchmark["calibration"]
                )
            ),
            "key_native_baselines": all(
                any(
                    item["language"] == language
                    and item["status"] == "MEASURED"
                    for item in benchmark["observations"]
                )
                for language in ("c", "rust", "go")
            ),
            "memory_models": (
                not memory["correctness_failures"]
                and all(
                    item.get("static_memory_counters") is not None
                    for item in memory["arms"]
                )
                and len(memory["model_recommendations"]) == 4
                and all(
                    distribution_fields <= set(item["wall_ms"])
                    for item in memory["arms"]
                    if item["status"] == "MEASURED"
                )
            ),
            "extended_external_arms": not extended["correctness_failures"],
            "extended_meldra_limitations_explicit": (
                extended["meldra_unsupported_category_count"] == 3
                and extended["meldra_unsupported_count"] >= 15
                and all(
                    item["status"] == "UNSUPPORTED_DECLARED"
                    for item in extended["unsupported_features"]
                )
            ),
            "codegen_assembly_records": (
                codegen["status"] == "PASS"
                and codegen["workload_count"] == 9
                and all(
                    arm["status"] == "UNSUPPORTED_DECLARED"
                    or (
                        arm["status"] == "MEASURED"
                        and arm["optimization_record_present"]
                    )
                    for observation in codegen["observations"]
                    for arm in observation["arms"]
                )
            ),
            "external_projects": (
                external["scanned_project_count"] >= 5
                and not external["kernel_failures"]
            ),
            "ergonomics_denominators": (
                ergonomics["task_count"] >= 15
                and ergonomics["category_count"] >= 3
                and ergonomics["human_trial"]["measured_users"] == 0
                and ergonomics["ai_trial"]["measured_tasks"] == 0
            ),
            "deterministic_mir_c_binary": (
                determinism["all_mir_equal"]
                and determinism["all_c_equal"]
                and determinism["all_binary_equal"]
            ),
            "compile_phase_samples": all(
                len(item["phases"]["in_process_total"]["samples_ms"]) >= 30
                for item in compile_phases["observations"]
            ),
            "separate_external_compile_link_samples": all(
                item["external_c_compile_link"]["status"] == "MEASURED"
                and len(
                    item["external_c_compile_link"]["compile"]["samples_ms"]
                )
                >= 30
                and len(
                    item["external_c_compile_link"]["link"]["samples_ms"]
                )
                >= 30
                for item in compile_phases["observations"]
            ),
            "stage04_overlap": overlap["ok"],
            "pytest": pytest_passed >= 425 and "passed" in pytest_summary,
            "compileall": compileall_status == "PASS",
            "changed_code_pyflakes": pyflakes_status == "PASS",
        }
        failures.extend(name for name, passed in checks.items() if not passed)

        valid_manifest = benchmarks / "stage06p_correctness" / "valid_manifest.json"
        invalid_manifest = benchmarks / "stage06p_correctness" / "invalid_manifest.json"
        manifest_checks = {
            "valid_manifest_sha256": _sha256(valid_manifest)
            == correctness["manifests"]["valid_manifest_sha256"],
            "invalid_manifest_sha256": _sha256(invalid_manifest)
            == correctness["manifests"]["invalid_manifest_sha256"],
        }
        failures.extend(name for name, passed in manifest_checks.items() if not passed)
    else:
        checks = {}
        manifest_checks = {}

    schema_checks: dict[str, Any] = {}
    try:
        import jsonschema

        mir_schema = _load_json(root_path / "merlo" / "performance_mir_schema_v1.json")
        stage06p_mir_extensions = ("retain", "release")
        mir_operations = mir_schema["$defs"]["instruction"]["properties"][
            "op"
        ]["anyOf"][0]["enum"]
        mir_operations.extend(
            operation
            for operation in stage06p_mir_extensions
            if operation not in mir_operations
        )
        hir_schema = _load_json(root_path / "merlo" / "native_hir_schema_v1.json")
        mir_paths = list((benchmarks / "stage06p_benchmark" / "mir").glob("**/*_after.json"))
        mir_paths += list((benchmarks / "stage06p_memory").glob("**/mir/*_after.json"))
        mir_paths += list(
            (benchmarks / "stage06p_correctness" / "native_batches").glob(
                "**/*_after.json"
            )
        )
        hir_paths = list((benchmarks / "stage06p_compile_phases" / "hir").glob("*.json"))
        for path in mir_paths:
            jsonschema.validate(_load_json(path), mir_schema)
        for path in hir_paths:
            jsonschema.validate(_load_json(path), hir_schema)
        optimizer_documents = 0
        optimizer_payload = artifacts.get("meldra_stage06p_optimizer.json", {})
        for pass_observation in optimizer_payload.get("passes", []):
            for case_name in ("positive", "negative"):
                case = pass_observation[case_name]
                jsonschema.validate(case["before_mir"], mir_schema)
                jsonschema.validate(case["after_mir"], mir_schema)
                optimizer_documents += 2
        schema_checks = {
            "status": "PASS",
            "mir_documents": len(mir_paths),
            "hir_documents": len(hir_paths),
            "optimizer_mir_documents": optimizer_documents,
            "stage06p_mir_extensions": list(stage06p_mir_extensions),
            "validator": getattr(jsonschema, "__version__", "installed"),
        }
    except Exception as exc:
        schema_checks = {
            "status": "FAIL",
            "error": f"{type(exc).__name__}: {exc}",
        }
        failures.append("json_schema_validation")

    artifact_hashes = {
        name: _sha256(benchmarks / name)
        for name in artifact_names
        if (benchmarks / name).is_file()
    }
    return {
        "schema_version": STAGE06P_VALIDATION_SCHEMA_VERSION,
        "kind": "MeldraStage06PValidation",
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "checks": checks,
        "manifest_checks": manifest_checks,
        "schema_checks": schema_checks,
        "commands": {
            "pytest": pytest_summary,
            "pytest_passed": pytest_passed,
            "compileall": compileall_status,
            "pyflakes_changed_scope": pyflakes_status,
        },
        "artifact_hashes": artifact_hashes,
        "implementation_versions": {
            "native_hir_schema": NATIVE_HIR_SCHEMA_VERSION,
            "performance_mir_schema": PERFORMANCE_MIR_SCHEMA_VERSION,
            "performance_frontend_schema": PERFORMANCE_FRONTEND_SCHEMA_VERSION,
            "performance_frontend_implementation": PERFORMANCE_FRONTEND_IMPLEMENTATION_VERSION,
            "native_backend_schema": NATIVE_BACKEND_SCHEMA_VERSION,
            "native_backend_implementation": NATIVE_BACKEND_IMPLEMENTATION_VERSION,
            "memory_model": PERFORMANCE_MEMORY_MODEL_VERSION,
            "optimizer_passes": dict(OPTIMIZATION_PASS_VERSIONS),
        },
    }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_stage06p_validation(root: str | Path = ".", **kwargs: Any) -> dict[str, Any]:
    root_path = Path(root)
    result = validate_stage06p_artifacts(root_path, **kwargs)
    (root_path / "tools" / "benchmarks" / "merlo" / "benchmarks" / STAGE06P_VALIDATION_FILENAME).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


__all__ = [
    "STAGE06P_VALIDATION_FILENAME",
    "STAGE06P_VALIDATION_SCHEMA_VERSION",
    "validate_stage06p_artifacts",
    "write_stage06p_validation",
]
