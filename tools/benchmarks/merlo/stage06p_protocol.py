"""Machine-readable preregistered Stage 0.6P protocol."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


STAGE06P_PROTOCOL_SCHEMA_VERSION = 2
STAGE06P_PROTOCOL_FILENAME = "meldra_stage06p_protocol.json"
STAGE06P_DECISIONS = (
    "GO_NATIVE_CORE_EXPANSION",
    "CONTINUE_PERFORMANCE_RESEARCH",
    "GO_PYTHON_PLATFORM_ONLY",
    "NO_GO_NATIVE_LANGUAGE",
)
STAGE06P_NON_GOALS = (
    "flow",
    "machine",
    "scheduler",
    "async_runtime",
    "network_runtime",
    "database_layer",
    "package_registry",
    "package_downloader",
    "ui",
    "web_framework",
    "mobile",
    "gpu",
    "llvm_backend",
    "cranelift_backend",
    "jit",
    "custom_assembler",
    "cyclic_garbage_collector",
    "complex_generics",
    "macros",
    "inheritance",
    "distributed_runtime",
    "new_llm",
    "ide",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_stage06p_protocol(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root)
    protocol_doc = root_path / "merlo" / "STAGE_0_6P_PROTOCOL.md"
    freeze = root_path / "tools" / "benchmarks" / "merlo" / "benchmarks" / "meldra_stage05p_freeze_v2.json"
    return {
        "schema_version": STAGE06P_PROTOCOL_SCHEMA_VERSION,
        "kind": "MeldraStage06PProtocol",
        "preregistered": True,
        "protocol_document": {
            "path": "meldra/STAGE_0_6P_PROTOCOL.md",
            "sha256": _sha256(protocol_doc),
        },
        "stage05p_freeze_v2": {
            "path": "tools/benchmarks/merlo/benchmarks/meldra_stage05p_freeze_v2.json",
            "sha256": _sha256(freeze),
        },
        "goal": [
            "aggressive_native_compiler_correctness",
            "inferred_ownership_reduces_shared_overhead_without_lifetime_syntax",
            "near_native_performance_on_realistic_memory_and_text_families",
        ],
        "architecture": {
            "contract": "Native Typed HIR v1",
            "semantic_branch": [
                "SymbolId",
                "RevisionId",
                "references",
                "effects",
                "capabilities",
                "interfaces",
            ],
            "performance_branch": [
                "CFG",
                "ownership",
                "allocations",
                "drops",
                "layouts",
                "bounds_checks",
            ],
            "common_invariants": [
                "source_mappings",
                "function_types",
                "function_effects",
            ],
            "native_hir_only_invariants": ["SymbolId", "RevisionId"],
            "supported_compiler_core": [
                "Bool",
                "Int64",
                "UInt64",
                "Float32",
                "Float64",
                "flat_scalar_records",
                "fixed_arrays",
                "dynamic_slices",
                "direct_calls",
                "branches_match_loops",
                "checked_indexing",
                "unique_move_drop",
                "scoped_borrows",
                "explicit_shared_retain_release",
            ],
            "declared_unsupported_expansions": [
                "Text",
                "Bytes",
                "recursive_values",
                "cyclic_SharedRc",
                "generic_interfaces",
                "dynamic_dispatch",
            ],
            "stage04_frontend_policy": "FROZEN_ADAPTER_ONLY",
        },
        "correctness": {
            "valid_programs": 5000,
            "invalid_programs": 2000,
            "required_levels": [
                "surface_evaluator",
                "native_hir_evaluator",
                "unoptimized_mir_interpreter",
                "per_pass_mir_interpreter",
                "optimized_mir_interpreter",
                "generated_c_native",
            ],
            "semantic_equality_observations": [
                "status",
                "return_value",
                "printed_checksum",
                "error_kind",
                "effect_trace",
            ],
            "pass_specific_ownership_observations": [
                "allocations",
                "drops",
                "retains",
                "releases",
                "final_ownership_state",
            ],
            "optimizer_positive_negative_evidence_required": True,
        },
        "optimizer_evidence": {
            "positive_and_negative_case_per_pass": True,
            "before_after_mir": True,
            "semantic_observation_comparison": True,
            "pass_counters": True,
            "missed_reasons": True,
        },
        "codegen_evidence": {
            "representative_assembly": True,
            "optimization_records": True,
            "vectorization_counters": True,
            "call_and_branch_counters": True,
            "stack_adjustment_counters": True,
        },
        "scalar_policy": {
            "Int64": "two_complement_wrapping",
            "UInt64": "modulo_2^64",
            "Float32": "IEEE_754_host_operations_binary32_rounding",
            "Float64": "IEEE_754_host_operations_binary64",
            "NaN_payload_identity": "not_promised",
            "generated_c_flags": [
                "-fwrapv",
                "-fno-delete-null-pointer-checks",
                "-ffp-contract=off",
            ],
            "c_undefined_behavior_forbidden": True,
        },
        "sanitizers": [
            "AddressSanitizer",
            "UndefinedBehaviorSanitizer",
            "LeakSanitizer",
            "debug_assertions",
            "bounds_checks",
            "release",
        ],
        "ownership_states": [
            "Unique",
            "BorrowedShared",
            "BorrowedMutable",
            "RegionOwned",
            "SharedRc",
        ],
        "cycles": {
            "collector": False,
            "static_policy": "SharedCycleUnsupported",
            "foreign_policy": "explicit_boundary_outside_guarantee",
        },
        "benchmark_method": {
            "target_runtime_ms": [200, 500],
            "warmups": 5,
            "measurements": 30,
            "arm_order": "deterministically_randomized",
            "runtime_inputs": True,
            "observable_checksum": True,
            "confidence_interval": "deterministic_bootstrap_95_percent",
            "short_duration_stability_relative_mad_max": 0.05,
            "short_duration_requires_stable_c_and_meldra": True,
            "reported_distribution": [
                "median",
                "mean",
                "minimum",
                "p95",
                "standard_deviation",
                "MAD",
                "bootstrap_median_95_CI",
            ],
            "exclusion_rule": [
                "nonzero_exit",
                "checksum_mismatch",
                "timeout",
                "sanitizer_failure",
                "affinity_launch_failure",
            ],
            "timing_outliers_excluded": False,
        },
        "corpus_families": [
            "arithmetic",
            "vector_array_compute",
            "map_filter_fold",
            "records",
            "sorting",
            "bounds_heavy_loop",
            "text_utf8_scan",
            "text_builder",
            "word_count",
            "integer_parser",
            "bytes_transform",
            "recursive_tree",
            "linked_list",
            "shared_acyclic_dag",
            "interface_dispatch",
            "allocation_churn",
            "small_csv_parser",
            "startup",
        ],
        "toolchain_policy": {
            "host_first": True,
            "pinned_containers_allowed": True,
            "emulation_forbidden": True,
            "destructive_system_changes_forbidden": True,
            "key_native_baselines": ["c", "rust", "go"],
            "missing_key_toolchain_caps_decision": "CONTINUE_PERFORMANCE_RESEARCH",
        },
        "decision": {
            "allowed": list(STAGE06P_DECISIONS),
            "go_native_core_expansion": {
                "differential_mismatches_max": 0,
                "sanitizer_violations_max": 0,
                "unexplained_checksum_mismatches_max": 0,
                "optimizer_semantic_mismatches_max": 0,
                "generated_c_ub_allowed": False,
                "unique_numeric_array_geomean_max_c_ratio": 1.10,
                "text_bytes_systematic_native_ratio_max": 1.25,
                "recursive_unique_systematic_native_ratio_max": 1.25,
                "shared_stage05p_ratio_to_improve": 1.698,
                "lifetime_annotations_in_ordinary_source_max": 0,
                "hidden_double_frees_max": 0,
                "hidden_leaks_max": 0,
                "interfaces_measured": True,
                "deterministic_builds": True,
                "required_native_baselines": ["c", "rust", "go"],
            },
        },
        "human_simplicity": "UNMEASURED_WITH_USERS",
        "ai_productivity": "UNMEASURED_WITHOUT_REAL_MODEL_API",
        "non_goals": list(STAGE06P_NON_GOALS),
    }


def write_stage06p_protocol(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root)
    payload = build_stage06p_protocol(root_path)
    (root_path / "tools" / "benchmarks" / "merlo" / "benchmarks" / STAGE06P_PROTOCOL_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


__all__ = [
    "STAGE06P_DECISIONS",
    "STAGE06P_NON_GOALS",
    "STAGE06P_PROTOCOL_FILENAME",
    "STAGE06P_PROTOCOL_SCHEMA_VERSION",
    "build_stage06p_protocol",
    "write_stage06p_protocol",
]
