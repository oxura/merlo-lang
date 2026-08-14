"""Frozen protocol and scope contract for Meldra Stage 0.5P."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

_ARCHIVE_ROOT = Path(__file__).resolve().parents[1]
from typing import Any

from .performance_mir import (
    NATIVE_SUBSET_FORMS,
    NATIVE_SUBSET_TYPES,
    PERFORMANCE_MIR_SCHEMA_VERSION,
    STAGE05P_NON_GOALS,
)
from .performance_opt import OPTIMIZATION_PIPELINE
from .stage05p_freeze import STAGE05P_FREEZE_FILENAME


STAGE05P_PROTOCOL_SCHEMA_VERSION = 1
STAGE05P_PROTOCOL_FILENAME = "meldra_stage05p_protocol.json"
STAGE05P_DECISIONS = (
    "GO_NATIVE_LANGUAGE_RESEARCH",
    "CONTINUE_PERFORMANCE_RESEARCH",
    "GO_PYTHON_PLATFORM_ONLY",
    "NO_GO_NATIVE_LANGUAGE",
)


def build_stage05p_protocol(root: str | Path = ".") -> dict[str, Any]:
    root_path = (Path(root) if str(root) != "." else _ARCHIVE_ROOT)
    freeze_raw = (root_path / "benchmarks" / STAGE05P_FREEZE_FILENAME).read_bytes()
    mir_schema_path = root_path / "merlo" / "performance_mir_schema_v1.json"
    return {
        "schema_version": STAGE05P_PROTOCOL_SCHEMA_VERSION,
        "kind": "MeldraStage05PProtocol",
        "stage04e_freeze_sha256": hashlib.sha256(freeze_raw).hexdigest(),
        "goal": (
            "Test whether a minimal pure Meldra subset can lower through typed "
            "Performance MIR to competitive native code; seek counterexamples, "
            "not a claim that Meldra is fast."
        ),
        "subset": {
            "types": list(NATIVE_SUBSET_TYPES),
            "forms": list(NATIVE_SUBSET_FORMS),
            "integer_semantics": "two_complement_wrapping",
            "float_semantics": "IEEE_754",
            "ordinary_values": "value_semantics",
            "arrays": "fixed_length_owned_descriptor_and_contiguous_payload",
            "slices": "borrowed_descriptor_data_length",
            "records": "declared_order_explicit_size_alignment_offsets",
            "functions": "pure_and_direct_calls_only",
        },
        "frontend_relationship": {
            "status": "ADAPTER_REQUIRED",
            "reason": (
                "Frozen Stage 0.4 CoreIR cannot encode var, for, while, fixed arrays, "
                "width-specific scalars, allocations, moves, drops, or bounds checks."
            ),
            "policy": (
                "Stage 0.5P uses a separate restricted surface adapter and Performance "
                "MIR; no Stage 0.4 frontend or Python-sidecar file may change."
            ),
        },
        "performance_mir": {
            "schema_version": PERFORMANCE_MIR_SCHEMA_VERSION,
            "schema_path": "merlo/performance_mir_schema_v1.json",
            "schema_sha256": hashlib.sha256(mir_schema_path.read_bytes()).hexdigest(),
            "cfg": "functions -> basic blocks -> typed instructions + terminator",
            "explicit": [
                "layouts",
                "moves",
                "alloc_stack",
                "alloc_heap",
                "drops",
                "direct_calls",
                "bounds_checks",
                "source_mappings",
            ],
        },
        "memory_model": {
            "unique": "move invalidates source; non-escaping fixed payloads use stack",
            "escape_analysis": "conservative returned-value escape classification",
            "in_place_reuse": "unique index stores mutate owned payload",
            "shared_fallback": "heap payload plus atomicity-free reference count and explicit drop",
            "known_limit": (
                "Shared fallback allocates payload and reference count separately; "
                "alias retain insertion is not in the restricted surface."
            ),
        },
        "backend": {
            "kind": "portable_C11",
            "compilers": ["clang", "gcc", "cc"],
            "flags": [
                "-O3",
                "-fwrapv",
                "-fno-ident",
                "-Werror",
                "-Wl,--build-id=none",
            ],
            "missing_compiler_policy": "UNMEASURED_COMPILER_UNAVAILABLE",
        },
        "optimization_pipeline": [item.__name__ for item in OPTIMIZATION_PIPELINE],
        "pass_evidence": {
            "required_snapshots": ["before_mir", "after_mir", "statistics"],
            "statistics": [
                "instructions_removed",
                "allocations_removed",
                "loops_fused",
                "bounds_checks_removed",
                "calls_inlined",
                "specializations_created",
                "stack_allocations",
                "heap_allocations",
                "in_place_reuses",
            ],
        },
        "benchmark": {
            "competitors": ["C", "Rust", "Go", "C#", "Python"],
            "categories": [
                "arithmetic",
                "arrays",
                "pipelines",
                "strings",
                "records",
                "trees",
                "sorting",
                "allocation-heavy",
                "startup",
            ],
            "metrics": [
                "runtime",
                "peak_rss",
                "instrumented_algorithm_allocations",
                "binary_size",
                "startup",
                "compile_time",
                "correctness",
            ],
            "fairness": "same algorithm, runtime input, expected checksum, and release optimization",
        },
        "hypotheses": [
            "high_level_collection_pipelines_lower_to_one_loop",
            "unique_values_update_without_copy",
            "pure_functions_enable_stronger_optimization",
            "closed_interfaces_enable_devirtualization",
            "deterministic_source_produces_deterministic_native_output",
        ],
        "non_goals": list(STAGE05P_NON_GOALS),
        "allowed_decisions": list(STAGE05P_DECISIONS),
    }


def write_stage05p_protocol(root: str | Path = ".") -> dict[str, Any]:
    root_path = (Path(root) if str(root) != "." else _ARCHIVE_ROOT)
    payload = build_stage05p_protocol(root_path)
    (root_path / "benchmarks" / STAGE05P_PROTOCOL_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


__all__ = [
    "STAGE05P_DECISIONS",
    "STAGE05P_PROTOCOL_FILENAME",
    "STAGE05P_PROTOCOL_SCHEMA_VERSION",
    "build_stage05p_protocol",
    "write_stage05p_protocol",
]
