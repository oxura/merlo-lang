"""Direct evaluations of the five Stage 0.5P native hypotheses."""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from merlo.native_c_backend import CEmitter, compile_c_source, find_c_compiler
from tools.benchmarks.merlo.performance_frontend import compile_performance_source
from merlo.performance_mir import PerformanceMIR
from tools.benchmarks.merlo.performance_opt import inlining, optimize_mir
from research.archive.historical_protocol.merlo.stage05p_freeze import assert_stage05p_frozen


NATIVE_HYPOTHESES_SCHEMA_VERSION = 1
NATIVE_HYPOTHESES_FILENAME = "meldra_stage05p_hypotheses.json"

_PIPELINE_SOURCE = """fn square(value: UInt64) -> UInt64:
    value * value

fn even(value: UInt64) -> Bool:
    value % 2 == 0

fn add(left: UInt64, right: UInt64) -> UInt64:
    left + right

fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 8] = [1, 2, 3, 4, 5, 6, 7, 8]
    var checksum: UInt64 = 0
    for i in 0..n:
        values[i & 7] = values[i & 7] + i
        checksum = checksum + fold(filter(map(values, square), even), 0, add)
    checksum
"""

_UNIQUE_SOURCE = """fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 4] = [1, 2, 3, 4]
    values[0] = values[0] + n
    values[0]
"""

_PURE_SOURCE = """fn double(value: UInt64) -> UInt64:
    value * 2

fn main(n: UInt64) -> UInt64:
    double(n) + 1
"""


def _instructions(mir: PerformanceMIR, function: str | None = None) -> list[Any]:
    functions = mir.functions if function is None else (mir.function(function),)
    return [
        instruction
        for item in functions
        for block in item.blocks
        for instruction in block.instructions
    ]


def _pipeline_hypothesis() -> dict[str, Any]:
    original = compile_performance_source(_PIPELINE_SOURCE, path="hypotheses/pipeline.meldra").mir
    optimized, snapshots = optimize_mir(original)
    operations = [item.op for item in _instructions(optimized, "main")]
    fusion = next(item for item in snapshots if item.name == "collection_fusion")
    fused = operations.count("fused_collection_loop")
    residual = [item for item in operations if item.startswith("collection_")]
    passed = (
        fused == 1
        and not residual
        and fusion.statistics.loops_fused == 1
        and fusion.statistics.allocations_removed == 2
    )
    return {
        "id": "high_level_collection_pipelines_lower_to_one_loop",
        "status": "PASS" if passed else "FAIL",
        "evidence": {
            "fused_collection_loops": fused,
            "residual_collection_operations": residual,
            "loops_fused": fusion.statistics.loops_fused,
            "allocations_removed": fusion.statistics.allocations_removed,
            "before_digest": fusion.before.digest,
            "after_digest": fusion.after.digest,
        },
        "scope": "one map-filter-fold chain over a fixed UInt64 array",
    }


def _unique_hypothesis() -> dict[str, Any]:
    original = compile_performance_source(_UNIQUE_SOURCE, path="hypotheses/unique.meldra").mir
    optimized, snapshots = optimize_mir(original)
    operations = [item.op for item in _instructions(optimized, "main")]
    memory = next(item for item in snapshots if item.name == "memory_model_lowering")
    passed = (
        "store_index" in operations
        and "alloc_heap" not in operations
        and memory.statistics.allocations_removed == 1
        and memory.statistics.in_place_reuses == 1
    )
    return {
        "id": "unique_values_update_without_copy",
        "status": "PASS" if passed else "FAIL",
        "evidence": {
            "store_index_count": operations.count("store_index"),
            "copy_count": operations.count("copy"),
            "heap_allocation_count": operations.count("alloc_heap"),
            "stack_allocation_count": operations.count("alloc_stack"),
            "allocations_removed": memory.statistics.allocations_removed,
            "in_place_reuses": memory.statistics.in_place_reuses,
        },
        "scope": "non-escaping fixed array with one unique in-place update",
    }


def _pure_hypothesis() -> dict[str, Any]:
    original = compile_performance_source(_PURE_SOURCE, path="hypotheses/pure.meldra").mir
    optimized, statistics = inlining(original)
    pure_calls = sum(item.op == "call" for item in _instructions(optimized, "main"))
    impure_functions = tuple(
        replace(item, pure=False) if item.name == "double" else item
        for item in original.functions
    )
    impure = replace(original, functions=impure_functions)
    ablated, ablated_statistics = inlining(impure)
    impure_calls = sum(item.op == "call" for item in _instructions(ablated, "main"))
    passed = statistics.calls_inlined == 1 and pure_calls == 0 and impure_calls == 1
    return {
        "id": "pure_functions_enable_stronger_optimization",
        "status": "PASS" if passed else "FAIL",
        "evidence": {
            "pure_calls_inlined": statistics.calls_inlined,
            "pure_residual_calls": pure_calls,
            "impure_ablation_calls_inlined": ablated_statistics.calls_inlined,
            "impure_ablation_residual_calls": impure_calls,
        },
        "scope": "single-basic-block direct function; purity ablated at MIR level",
    }


def _closed_interface_hypothesis() -> dict[str, Any]:
    return {
        "id": "closed_interfaces_enable_devirtualization",
        "status": "UNMEASURED_OUTSIDE_FROZEN_SUBSET",
        "evidence": {
            "interface_constructs": 0,
            "virtual_calls": 0,
            "devirtualization_pass": False,
        },
        "reason": (
            "The frozen Stage 0.5P subset permits direct calls only. Adding an "
            "interface model solely to pass this hypothesis would violate the scope."
        ),
    }


def _determinism_hypothesis() -> dict[str, Any]:
    original_a = compile_performance_source(_PIPELINE_SOURCE, path="hypotheses/deterministic.meldra").mir
    original_b = compile_performance_source(_PIPELINE_SOURCE, path="hypotheses/deterministic.meldra").mir
    optimized_a, _snapshots_a = optimize_mir(original_a)
    optimized_b, _snapshots_b = optimize_mir(original_b)
    source_a = CEmitter(optimized_a, runtime_arguments=True).emit()
    source_b = CEmitter(optimized_b, runtime_arguments=True).emit()
    compiler = find_c_compiler()
    if compiler is None:
        return {
            "id": "deterministic_source_produces_deterministic_native_output",
            "status": "UNMEASURED_COMPILER_UNAVAILABLE",
            "evidence": {
                "mir_equal": optimized_a.digest == optimized_b.digest,
                "c_source_equal": source_a == source_b,
                "binary_equal": None,
            },
        }
    with tempfile.TemporaryDirectory(prefix="meldra-determinism-a-") as first, tempfile.TemporaryDirectory(prefix="meldra-determinism-b-") as second:
        build_a = compile_c_source(source_a, output_dir=first, stem="deterministic", compiler=compiler)
        build_b = compile_c_source(source_b, output_dir=second, stem="deterministic", compiler=compiler)
    binary_equal = (
        build_a.status == "MEASURED"
        and build_b.status == "MEASURED"
        and build_a.binary_sha256 == build_b.binary_sha256
    )
    passed = optimized_a.digest == optimized_b.digest and source_a == source_b and binary_equal
    return {
        "id": "deterministic_source_produces_deterministic_native_output",
        "status": "PASS" if passed else "FAIL",
        "evidence": {
            "compiler": compiler,
            "mir_digest_a": optimized_a.digest,
            "mir_digest_b": optimized_b.digest,
            "c_source_sha256_a": hashlib.sha256(source_a.encode()).hexdigest(),
            "c_source_sha256_b": hashlib.sha256(source_b.encode()).hexdigest(),
            "binary_sha256_a": build_a.binary_sha256,
            "binary_sha256_b": build_b.binary_sha256,
            "mir_equal": optimized_a.digest == optimized_b.digest,
            "c_source_equal": source_a == source_b,
            "binary_equal": binary_equal,
            "build_statuses": [build_a.status, build_b.status],
        },
        "scope": "same compiler, flags, source mapping, and host; separate output directories",
    }


def evaluate_native_hypotheses(root: str | Path = Path(__file__).resolve().parents[1]) -> dict[str, Any]:
    root_path = Path(root)
    freeze = assert_stage05p_frozen(root_path)
    hypotheses = (
        _pipeline_hypothesis(),
        _unique_hypothesis(),
        _pure_hypothesis(),
        _closed_interface_hypothesis(),
        _determinism_hypothesis(),
    )
    payload = {
        "schema_version": NATIVE_HYPOTHESES_SCHEMA_VERSION,
        "kind": "MeldraStage05PNativeHypotheses",
        "stage04e_freeze": freeze.to_dict(),
        "hypotheses": list(hypotheses),
        "counts": {
            status: sum(item["status"] == status for item in hypotheses)
            for status in sorted({item["status"] for item in hypotheses})
        },
    }
    (root_path / "tools" / "benchmarks" / "merlo" / "benchmarks" / NATIVE_HYPOTHESES_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


__all__ = [
    "NATIVE_HYPOTHESES_FILENAME",
    "NATIVE_HYPOTHESES_SCHEMA_VERSION",
    "evaluate_native_hypotheses",
]
