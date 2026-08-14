from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from research.archive.alpha1.merlo.native_bench import (
    NATIVE_BENCHMARK_LANGUAGES,
    WORKLOADS,
    NativeWorkload,
    competitor_source,
    reference_checksum,
    run_native_benchmark,
)
from merlo.native_c_backend import CEmitter, compile_native, find_c_compiler
from research.archive.alpha1.merlo.native_hypotheses import evaluate_native_hypotheses
from tools.benchmarks.merlo.performance_frontend import PerformanceCompileError, compile_performance_source
from merlo.performance_mir import STAGE05P_NON_GOALS, scalar_layout
from tools.benchmarks.merlo.performance_opt import inlining, optimize_mir
from research.archive.historical_protocol.merlo.stage05p_decision import build_stage05p_decision
from research.archive.historical_protocol.merlo.stage05p_freeze import assert_stage05p_frozen, verify_stage05p_freeze
from research.archive.historical_protocol.merlo.stage05p_protocol import build_stage05p_protocol


ROOT = Path(__file__).resolve().parents[4]


def _operations(mir, function: str = "main") -> list[str]:
    return [
        instruction.op
        for block in mir.function(function).blocks
        for instruction in block.instructions
    ]


def test_stage04e_and_python_sidecar_are_frozen() -> None:
    verification = verify_stage05p_freeze(ROOT)
    assert verification.ok
    assert verification.mismatches == ()
    assert assert_stage05p_frozen(ROOT) == verification
    payload = json.loads(
        (ROOT / "tools" / "benchmarks" / "merlo" / "benchmarks" / "meldra_stage05p_freeze.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["stage04e_decision"] == "NO_GO_LANGUAGE_ALPHA_USE_SEMANTIC_LAYER"
    assert payload["python_sidecar_policy"] == "CRITICAL_FIXES_ONLY"
    assert payload["native_research_is_separate"] is True


def test_protocol_freezes_subset_adapter_gap_and_non_goals() -> None:
    protocol = build_stage05p_protocol(ROOT)
    assert protocol["subset"]["types"] == [
        "Int64",
        "UInt64",
        "Float32",
        "Float64",
        "Bool",
        "records",
        "arrays",
        "slices",
    ]
    assert protocol["subset"]["forms"] == [
        "fn",
        "let",
        "var",
        "if",
        "match",
        "for",
        "while",
        "direct_function_calls",
    ]
    assert protocol["frontend_relationship"]["status"] == "ADAPTER_REQUIRED"
    assert tuple(protocol["non_goals"]) == STAGE05P_NON_GOALS
    assert "llvm_backend" in protocol["non_goals"]
    assert "flow" in protocol["non_goals"]
    schema_path = ROOT / protocol["performance_mir"]["schema_path"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["$id"].endswith("/performance-mir/v1")
    assert (
        hashlib.sha256(schema_path.read_bytes()).hexdigest()
        == protocol["performance_mir"]["schema_sha256"]
    )


def test_frontend_lowers_types_cfg_records_match_and_source_mappings() -> None:
    source = """record Pair:
    left: Float32
    right: Float64

fn select(flag: Bool, a: UInt64, b: UInt64) -> UInt64:
    var result: UInt64 = a
    if flag:
        result = b
    result

fn main(n: UInt64) -> UInt64:
    var i: UInt64 = 0
    var total: UInt64 = 0
    while i < n:
        total = total + select(i % 2 == 0, i, 1)
        i = i + 1
    match n:
        case 0:
            total = total + 1
        case _:
            total = total + 2
    total
"""
    result = compile_performance_source(source, path="types.meldra")
    assert result.frontend_compatibility == (
        "ADAPTER_REQUIRED_FROZEN_FRONTEND_LACKS_STAGE05P_FORMS"
    )
    assert result.mir.records[0].name == "Pair"
    assert [item[1].name for item in result.mir.records[0].fields] == [
        "Float32",
        "Float64",
    ]
    main = result.mir.function("main")
    assert len(main.blocks) >= 8
    assert {block.terminator.kind for block in main.blocks} >= {
        "jump",
        "branch",
        "return",
    }
    mappings = [
        instruction.source
        for block in main.blocks
        for instruction in block.instructions
        if instruction.source is not None
    ]
    assert mappings
    assert all(mapping.path == "types.meldra" for mapping in mappings)
    assert scalar_layout(result.mir.records[0].fields[0][1]).size == 4
    assert scalar_layout(result.mir.records[0].fields[1][1]).size == 8


def test_frontend_rejects_dynamic_calls_assignment_to_let_and_use_after_move() -> None:
    with pytest.raises(PerformanceCompileError, match="only direct calls"):
        compile_performance_source(
            """fn main(n: UInt64) -> UInt64:
    n.method()
"""
        )
    with pytest.raises(PerformanceCompileError, match="immutable let"):
        compile_performance_source(
            """fn main(n: UInt64) -> UInt64:
    let value: UInt64 = n
    value = 2
    value
"""
        )
    with pytest.raises(PerformanceCompileError, match="use after move"):
        compile_performance_source(
            """fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 2] = [1, 2]
    let moved: Array[UInt64, 2] = move(values)
    values[0]
"""
        )


def test_optimization_pipeline_fuses_removes_checks_and_records_snapshots(
    tmp_path: Path,
) -> None:
    source = """fn square(value: UInt64) -> UInt64:
    value * value

fn even(value: UInt64) -> Bool:
    value % 2 == 0

fn add(left: UInt64, right: UInt64) -> UInt64:
    left + right

fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 4] = [1, 2, 3, 4]
    var total: UInt64 = 0
    for i in 0..len(values):
        total = total + values[i]
    total + fold(filter(map(values, square), even), 0, add)
"""
    original = compile_performance_source(source).mir
    optimized, snapshots = optimize_mir(original, artifact_dir=tmp_path)
    by_name = {item.name: item for item in snapshots}
    assert by_name["monomorphization"].statistics.specializations_created == 3
    assert by_name["collection_fusion"].statistics.loops_fused == 1
    assert by_name["collection_fusion"].statistics.allocations_removed == 2
    assert by_name["bounds_check_elimination"].statistics.bounds_checks_removed == 1
    assert by_name["memory_model_lowering"].statistics.allocations_removed == 1
    assert "fused_collection_loop" in _operations(optimized)
    assert "bounds_check" not in _operations(optimized)
    assert "alloc_heap" not in _operations(optimized)
    assert len(tuple(tmp_path.glob("*_before.json"))) == len(snapshots)
    assert len(tuple(tmp_path.glob("*_after.json"))) == len(snapshots)
    assert len(tuple(tmp_path.glob("*_statistics.json"))) == len(snapshots)
    for path in tmp_path.glob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))


def test_purity_ablation_controls_inlining() -> None:
    source = """fn double(value: UInt64) -> UInt64:
    value * 2

fn main(n: UInt64) -> UInt64:
    double(n) + 1
"""
    original = compile_performance_source(source).mir
    optimized, pure_statistics = inlining(original)
    assert pure_statistics.calls_inlined == 1
    assert "call" not in _operations(optimized)
    impure = replace(
        original,
        functions=tuple(
            replace(function, pure=False)
            if function.name == "double"
            else function
            for function in original.functions
        ),
    )
    ablated, impure_statistics = inlining(impure)
    assert impure_statistics.calls_inlined == 0
    assert "call" in _operations(ablated)


def test_native_backend_compiles_runs_and_matches_checksum(tmp_path: Path) -> None:
    if find_c_compiler() is None:
        pytest.skip("Clang/GCC unavailable")
    source = """fn add(left: UInt64, right: UInt64) -> UInt64:
    left + right

fn main(n: UInt64) -> UInt64:
    var total: UInt64 = 0
    for i in 0..n:
        total = add(total, i)
    total
"""
    original = compile_performance_source(source).mir
    optimized, _snapshots = optimize_mir(original)
    build = compile_native(
        optimized,
        output_dir=tmp_path,
        entry_arguments=(100,),
        stem="checksum",
    )
    assert build.status == "MEASURED", build.stderr
    completed = subprocess.run(
        (build.binary_path,), capture_output=True, text=True, check=False, timeout=10
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "4950"
    assert "MELDRA_ALLOCATIONS=0" in completed.stderr


def test_runtime_argument_wrapper_prevents_literal_specialization() -> None:
    source = """fn main(n: UInt64) -> UInt64:
    n + 1
"""
    mir, _snapshots = optimize_mir(compile_performance_source(source).mir)
    generated = CEmitter(mir, runtime_arguments=True).emit()
    assert "int main(int argc, char **argv)" in generated
    assert "strtoull(argv[1]" in generated
    assert "UINT64_C(1)" in generated
    assert "meldra_fn_main(meldra_entry_argument_1)" in generated


def test_benchmark_corpus_has_all_categories_languages_and_runtime_inputs() -> None:
    assert {item.category for item in WORKLOADS} == {
        "arithmetic",
        "arrays",
        "pipelines",
        "strings",
        "records",
        "trees",
        "sorting",
        "allocation-heavy",
        "startup",
    }
    assert NATIVE_BENCHMARK_LANGUAGES == (
        "meldra",
        "c",
        "rust",
        "go",
        "csharp",
        "python",
    )
    for workload in WORKLOADS:
        assert isinstance(reference_checksum(workload), int)
        for language in NATIVE_BENCHMARK_LANGUAGES[1:]:
            source = competitor_source(language, workload)
            assert source.strip()
            assert str(workload.input) not in source or "BENCH_ALLOCATIONS" in source
    c_source = competitor_source("c", WORKLOADS[0])
    rust_source = competitor_source("rust", WORKLOADS[0])
    python_source = competitor_source("python", WORKLOADS[0])
    assert "strtoull(argv[1]" in c_source
    assert "std::env::args()" in rust_source
    assert "sys.argv[1]" in python_source


def test_small_benchmark_marks_missing_toolchains_unmeasured(tmp_path: Path) -> None:
    workload = NativeWorkload("startup", "startup", "return_constant_42", 0)
    report = run_native_benchmark(
        output_dir=tmp_path,
        workloads=(workload,),
        repetitions=1,
        warmups=0,
    )
    observations = {item["language"]: item for item in report["observations"]}
    assert observations["meldra"]["run"]["correct"] is True
    assert observations["c"]["run"]["correct"] is True
    assert observations["python"]["run"]["correct"] is True
    for language in ("rust", "go", "csharp"):
        if observations[language]["build"]["compiler"] is None:
            assert observations[language]["run"]["status"] == (
                "UNMEASURED_TOOLCHAIN_UNAVAILABLE"
            )


def test_hypotheses_and_frozen_decision_are_evidence_backed() -> None:
    hypotheses = evaluate_native_hypotheses(ROOT)
    statuses = {
        item["id"]: item["status"] for item in hypotheses["hypotheses"]
    }
    assert statuses == {
        "high_level_collection_pipelines_lower_to_one_loop": "PASS",
        "unique_values_update_without_copy": "PASS",
        "pure_functions_enable_stronger_optimization": "PASS",
        "closed_interfaces_enable_devirtualization": (
            "UNMEASURED_OUTSIDE_FROZEN_SUBSET"
        ),
        "deterministic_source_produces_deterministic_native_output": "PASS",
    }
    decision = build_stage05p_decision(ROOT)
    assert decision["decision"] == "CONTINUE_PERFORMANCE_RESEARCH"
    assert decision["gates"]["measured_meldra_correct"] is True
    assert decision["gates"]["all_five_hypotheses_pass"] is False
    assert decision["gates"]["external_rust_go_csharp_measured"] is False
    assert decision["measurements"][
        "meldra_over_c_unique_compute_geometric_mean"
    ] < 1.25
    assert decision["measurements"]["meldra_over_c_runtime_ratios"][
        "shared_allocations"
    ] > 1.5
    for relative, expected in decision["inputs"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected
