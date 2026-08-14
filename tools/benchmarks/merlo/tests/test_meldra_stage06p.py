from __future__ import annotations
import math
import subprocess
import pytest

from research.archive.alpha1.merlo.constant_knowledge_audit import (
    _meldra_shaped_c_source,
    _select_status as select_constant_knowledge_status,
)
from research.archive.alpha1.merlo.fair_memory_strategy import (
    _c_arena_source,
    _c_preallocated_source,
    _counter_contract,
    _select_status,
    _parse_counter_output,
    validate_fair_memory_report,
)
from research.archive.alpha1.merlo.non_elidable_region import (
    _select_decision as select_non_elidable_decision,
    reference_checksum as non_elidable_reference_checksum,
)
from research.archive.alpha1.merlo.native_bench import _compile_external
from research.archive.alpha1.merlo.native_differential import evaluate_mir, run_differential
from research.archive.alpha1.merlo.optimizer_evidence import run_optimizer_evidence
from research.archive.alpha1.merlo.native_hir import compile_native_hir
from research.archive.alpha1.merlo.stage06p_overlap import compare_stage04_overlap
from research.archive.alpha1.merlo.performance_frontend import (
    PerformanceCompileError,
    compile_performance_source,
)
from research.archive.alpha1.merlo.performance_opt import optimize_mir, region_ownership_lowering


def test_mutable_array_uses_descriptor_local_not_second_payload_allocation(tmp_path):
    source = """fn main(n: UInt64) -> UInt64:
    var values: Array[UInt64, 4] = [1, 2, 3, 4]
    let view: Array[UInt64, 4] = borrow_mut(values)
    view[0] = view[0] + n
    values[0]
"""
    result = run_differential(
        source,
        (41,),
        artifact_dir=tmp_path,
    )
    assert result.ok, result.to_dict()
    assert dict(result.observations)["native"].return_value == 42



def test_memory_lowering_does_not_duplicate_explicit_shared_drop(tmp_path):
    source = """fn main(n: UInt64) -> UInt64:
    let values: Shared[Array[UInt64, 2]] = [n, 2]
    let result: UInt64 = values[0] + values[1]
    drop(values)
    result
"""
    result = run_differential(
        source,
        (40,),
        artifact_dir=tmp_path,
    )
    assert result.ok, result.to_dict()
    assert dict(result.observations)["native"].return_value == 42



def test_shared_retain_and_release_balance_reference_count(tmp_path):
    source = """fn main(n: UInt64) -> UInt64:
    let values: Shared[Array[UInt64, 2]] = [n, 2]
    let alias: Shared[Array[UInt64, 2]] = retain(values)
    let result: UInt64 = alias[0] + alias[1]
    release(values)
    release(alias)
    result
"""
    result = run_differential(source, (40,), artifact_dir=tmp_path)
    observations = dict(result.observations)
    assert result.ok, result.to_dict()
    assert observations["surface"].retains == 1
    assert observations["surface"].releases == 2
    assert observations["native"].allocations == 1
    assert observations["native"].drops == 2
    unbalanced = """fn main(n: UInt64) -> UInt64:
    let values: Shared[Array[UInt64, 2]] = [n, 2]
    retain(values)
    drop(values)
    n
"""
    with pytest.raises(PerformanceCompileError, match="retain result requires a named Shared local"):
        compile_performance_source(unbalanced)

def test_stage04_and_native_hir_overlap_is_semantically_identical():
    result = compare_stage04_overlap()
    assert result["ok"], result
    assert result["contracts_equal"]
    assert result["references_equal"]
    assert result["values_equal"]


def test_inferred_unique_shared_result_elides_runtime_allocation(tmp_path):
    source = """fn make_values(i: UInt64) -> Shared[Array[UInt64, 2]]:
    [i, i + 1]

fn main(n: UInt64) -> UInt64:
    let values: Shared[Array[UInt64, 2]] = make_values(n)
    let result: UInt64 = values[0] + values[1]
    drop(values)
    result

"""
    result = run_differential(source, (20,), artifact_dir=tmp_path)
    observations = dict(result.observations)
    assert result.ok, result.to_dict()
    assert observations["surface"].allocations == 1
    assert observations["native"].allocations == 0
    assert observations["native"].return_value == 41


def test_every_optimizer_pass_has_positive_and_negative_semantic_evidence():
    report = run_optimizer_evidence()
    assert report["status"] == "PASS", report["failures"]
    assert report["pass_count"] == 10
    assert all(item["positive"]["changed"] for item in report["passes"])
    assert all(not item["negative"]["changed"] for item in report["passes"])


def test_region_lowering_preserves_inferred_shared_temporary():
    source = """fn make_values(i: UInt64) -> Shared[Array[UInt64, 2]]:
    [i, i + 1]

fn main(n: UInt64) -> UInt64:
    let values: Shared[Array[UInt64, 2]] = make_values(n)
    let result: UInt64 = values[0] + values[1]
    drop(values)
    result
"""
    optimized, _ = optimize_mir(compile_performance_source(source).mir)
    region, statistics = region_ownership_lowering(optimized)
    region_allocations = [
        instruction
        for function in region.functions
        for block in function.blocks
        for instruction in block.instructions
        if instruction.op == "alloc_region"
    ]
    assert statistics.allocations_removed == 1
    assert len(region_allocations) == 1
    assert region_allocations[0].attribute_map["ownership"] == "RegionOwned"
    observation = evaluate_mir(region, (20,))
    assert observation.return_value == 41
    assert observation.allocations == 0


def test_int64_wrapping_shifts_and_division_match_native_without_ub(tmp_path):
    cases = (
        ("n + 1", (9223372036854775807,), ("n",), -9223372036854775808, None),
        (
            "9223372036854775807 + 1",
            (),
            (),
            -9223372036854775808,
            None,
        ),
        ("-7 / 3", (), (), -2, None),
        (
            "9223372036854775808",
            (),
            (),
            -9223372036854775808,
            None,
        ),
        ("-1 << 65", (), (), -2, None),
        ("a / b", (-7, 3), ("a", "b"), -2, None),
        ("a << b", (-1, 65), ("a", "b"), -2, None),
        (
            "a / b",
            (-9223372036854775808, -1),
            ("a", "b"),
            -9223372036854775808,
            None,
        ),
        ("a / b", (7, 0), ("a", "b"), None, "DivisionByZero"),
    )
    for index, (expression, arguments, names, expected, error_kind) in enumerate(cases):
        parameters = ", ".join(f"{name}: Int64" for name in names)
        source = f"fn main({parameters}) -> Int64:\n    {expression}\n"
        result = run_differential(
            source,
            arguments,
            artifact_dir=tmp_path / str(index),
        )
        native = dict(result.observations)["native"]
        assert result.ok, result.to_dict()
        assert native.return_value == expected
        assert native.error_kind == error_kind


def test_float32_rounding_and_ieee_zero_division_match_native(tmp_path):
    cases = (
        (
            "Float32",
            "x + 1.0",
            "compute(x) == x",
            16777216.0,
            True,
        ),
        (
            "Float64",
            "x + 1.0",
            "compute(x) == x",
            16777216.0,
            False,
        ),
        (
            "Float64",
            "x / 0.0",
            "compute(x) > 1e300",
            2.0,
            True,
        ),
    )
    for index, (type_name, expression, predicate, argument, expected) in enumerate(cases):
        source = f"""fn compute(x: {type_name}) -> {type_name}:
    {expression}
fn main(x: {type_name}) -> Bool:
    {predicate}
"""
        result = run_differential(
            source,
            (argument,),
            artifact_dir=tmp_path / str(index),
        )
        native = dict(result.observations)["native"]
        assert result.ok, result.to_dict()
        assert native.return_value is expected
    direct_cases = (
        ("Float64", "x * 1.5 + 0.25", 2.0, 3.25),
        ("Float32", "x + 1.0", 16777216.0, 16777216.0),
        ("Float64", "x / 0.0", 2.0, math.inf),
        ("Float64", "x / 0.0", 0.0, math.nan),
        ("Float32", "x * x", 1e30, math.inf),
    )
    for index, (type_name, expression, argument, expected) in enumerate(direct_cases):
        source = f"""fn main(x: {type_name}) -> {type_name}:
    {expression}
"""
        result = run_differential(
            source,
            (argument,),
            artifact_dir=tmp_path / f"direct-{index}",
        )
        native = dict(result.observations)["native"]
        assert result.ok, result.to_dict()
        if math.isnan(expected):
            assert math.isnan(native.return_value)
        else:
            assert native.return_value == expected
    mixed_record = """record Sample:
    enabled: Bool
    weight: Float64
    count: Int64

fn main(x: Float64) -> Bool:
    let sample: Sample = Sample(enabled=x > 0.0, weight=x, count=-3)
    sample.enabled and sample.weight == x and sample.count == -3
"""
    mixed_result = run_differential(
        mixed_record,
        (2.5,),
        artifact_dir=tmp_path / "mixed-record",
    )
    assert mixed_result.ok, mixed_result.to_dict()
    assert dict(mixed_result.observations)["native"].return_value is True


def test_non_inlined_owned_collection_result_drops_at_last_use(tmp_path):
    source = """fn make_values(i: UInt64) -> Array[UInt64, 2]:
    if i % 2 == 0:
        return [i, i + 1]
    return [i + 2, i + 3]

fn main(n: UInt64) -> UInt64:
    var checksum: UInt64 = 0
    for i in 0..n:
        let values: Array[UInt64, 2] = make_values(i)
        checksum = checksum + values[0]
    checksum
"""
    result = run_differential(source, (10,), artifact_dir=tmp_path)
    native = dict(result.observations)["native"]
    assert result.ok, result.to_dict()
    assert native.return_value == 55
    assert native.allocations == 10
    assert native.drops == 10


def test_dynamic_slice_allocations_drop_each_loop_iteration(tmp_path):
    source = """fn square(value: UInt64) -> UInt64:
    value * value

fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 4] = [1, 2, 3, 4]
    var checksum: UInt64 = 0
    for i in 0..n:
        let mapped: Slice[UInt64] = map(values, square)
        checksum = checksum + mapped[i % 4]
    checksum
"""
    result = run_differential(source, (10,), artifact_dir=tmp_path)
    native = dict(result.observations)["native"]
    assert result.ok, result.to_dict()
    assert native.return_value == 65
    assert native.allocations == 10
    assert native.drops == 10


def test_returned_dynamic_slice_transfers_then_drops_in_caller(tmp_path):
    source = """fn square(value: UInt64) -> UInt64:
    value * value

fn make_values(n: UInt64) -> Slice[UInt64]:
    let values: Array[UInt64, 2] = [n, n + 1]
    map(values, square)

fn main(n: UInt64) -> UInt64:
    let mapped: Slice[UInt64] = make_values(n)
    mapped[1]
"""
    result = run_differential(source, (6,), artifact_dir=tmp_path)
    native = dict(result.observations)["native"]
    assert result.ok, result.to_dict()
    assert native.return_value == 49
    assert native.allocations == 1
    assert native.drops == 2


def test_unsupported_nested_and_cross_function_ownership_is_rejected():
    nested = """record Payload:
    values: Array[UInt64, 2]

fn main(n: UInt64) -> UInt64:
    n
"""
    recursive = """record Node:
    value: UInt64
    next: Node

fn main(n: UInt64) -> UInt64:
    n
"""
    moved = """fn consume(values: Array[UInt64, 2]) -> UInt64:
    values[0]

fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 2] = [n, 2]
    consume(move(values))
"""
    aliased = """fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 2] = [n, 2]
    let alias: Array[UInt64, 2] = values
    alias[0]
"""
    dropped_parameter = """fn consume(values: Array[UInt64, 2]) -> UInt64:
    drop(values)
    0

fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 2] = [n, 2]
    consume(values)
"""
    mutated_parameter = """fn mutate(values: Array[UInt64, 2]) -> UInt64:
    values[0] = 1
    values[0]

fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 2] = [n, 2]
    mutate(values)
"""
    shared_borrow_mutation = """fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 2] = [n, 2]
    let view: Array[UInt64, 2] = borrow(values)
    view[0] = 1
    values[0]
"""
    nested_borrow = """fn main(n: UInt64) -> UInt64:
    var values: Array[UInt64, 2] = [n, 2]
    let view: Array[UInt64, 2] = borrow_mut(values)
    let nested: Array[UInt64, 2] = borrow(view)
    nested[0]
"""
    with pytest.raises(PerformanceCompileError, match="record Payload collection ownership"):
        compile_performance_source(nested)
    with pytest.raises(PerformanceCompileError, match="SharedCycleUnsupported"):
        compile_performance_source(recursive)
    with pytest.raises(PerformanceCompileError, match="cross-function collection move"):
        compile_performance_source(moved)
    borrowed_return = """fn identity(values: Array[UInt64, 2]) -> Array[UInt64, 2]:
    values

fn main(n: UInt64) -> UInt64:
    n
"""
    with pytest.raises(PerformanceCompileError, match="borrowed collection values cannot escape"):
        compile_performance_source(borrowed_return)
    with pytest.raises(PerformanceCompileError, match="collection alias alias"):
        compile_performance_source(aliased)
    with pytest.raises(PerformanceCompileError, match="cannot drop borrowed parameter values"):
        compile_performance_source(dropped_parameter)
    with pytest.raises(PerformanceCompileError, match="cannot mutate borrowed parameter values"):
        compile_performance_source(mutated_parameter)
    with pytest.raises(PerformanceCompileError, match="cannot mutate shared borrowed view view"):
        compile_performance_source(shared_borrow_mutation)
    with pytest.raises(PerformanceCompileError, match="nested borrow of view view"):
        compile_performance_source(nested_borrow)


def test_drop_before_live_borrow_and_use_after_move_are_rejected():
    live_borrow = """fn main(n: UInt64) -> UInt64:
    var values: Array[UInt64, 2] = [n, 2]
    let view: Array[UInt64, 2] = borrow_mut(values)
    drop(values)
    view[0]
"""
    moved_use = """fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 2] = [n, 2]
    let transferred: Array[UInt64, 2] = move(values)
    values[0]
"""
    borrowed_drop = """fn main(n: UInt64) -> UInt64:
    var values: Array[UInt64, 2] = [n, 2]
    let view: Array[UInt64, 2] = borrow_mut(values)
    drop(view)
    values[0]
"""
    owner_mutation = """fn main(n: UInt64) -> UInt64:
    var values: Array[UInt64, 2] = [n, 2]
    let view: Array[UInt64, 2] = borrow_mut(values)
    values[0] = 1
    view[0]
"""
    with pytest.raises(PerformanceCompileError, match="precedes live borrow view"):
        compile_native_hir(live_borrow)
    with pytest.raises(PerformanceCompileError, match="use after move or drop of values"):
        compile_native_hir(moved_use)
    with pytest.raises(PerformanceCompileError, match="cannot drop borrowed view view"):
        compile_native_hir(borrowed_drop)
    with pytest.raises(PerformanceCompileError, match="use of values precedes live borrow view"):
        compile_native_hir(owner_mutation)


@pytest.mark.parametrize(
    ("name", "source_factory"),
    (
        ("c_arena", _c_arena_source),
        ("c_preallocated", _c_preallocated_source),
    ),
)
def test_fair_c_memory_arms_preserve_frozen_algorithm(
    tmp_path, name, source_factory
):
    input_value = 7
    (tmp_path / name).mkdir()
    build = _compile_external(
        "c",
        source_factory(),
        tmp_path / name,
        (str(input_value),),
    )
    assert build.status == "MEASURED", build.stderr
    completed = subprocess.run(
        build.run_command,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert int(completed.stdout.strip()) == input_value * (input_value + 6)
    observed = _parse_counter_output(completed.stderr)
    contract = _counter_contract(name, input_value)
    for field in (
        "logical_allocation_operations",
        "actual_allocator_allocate_calls",
        "actual_allocator_deallocate_calls",
        "heap_bytes_requested",
        "payload_copy_operations",
        "logical_element_initializations",
    ):
        assert observed[field] == contract[field]


def test_fair_memory_report_validator_guards_frozen_denominators():
    report = {
        "protocol": {
            "warmups": 5,
            "measured_batches": 30,
            "runtime_input": 500_000,
            "logical_object_count_per_invocation": 500_000,
        },
        "freeze": {
            "workload_contract": {
                "expected_checksum": 250_003_000_000,
            }
        },
        "correctness_failures": [],
        "counter_contract_failures": [],
        "arms": [{"name": name} for name in (
            "meldra_region",
            "meldra_borrow",
            "c_malloc",
            "c_arena",
            "c_preallocated",
            "rust_arena",
            "rust_preallocated",
        )],
        "decision": "INCONCLUSIVE_MEASUREMENT",
    }
    assert validate_fair_memory_report(report) == []
    report["protocol"]["runtime_input"] = 500_001
    assert validate_fair_memory_report(report) == ["runtime_input_changed"]


def test_fair_memory_decision_calls_manual_strategy_parity_a_baseline_artifact():
    report = {
        "arms": [
            {"status": "MEASURED", "dispersion_gate_passed": True}
            for _ in range(7)
        ],
        "correctness_failures": [],
        "counter_contract_failures": [],
        "ratios": {
            "c_arena_over_c_malloc": 0.26,
            "c_preallocated_over_c_malloc": 0.25,
            "meldra_region_over_c_malloc": 0.25,
            "meldra_borrow_over_c_malloc": 0.26,
            "meldra_region_over_fastest_fair_c": 1.00,
            "meldra_borrow_over_fastest_fair_c": 1.04,
        },
        "source_complexity": {
            "meldra_region": {"explicit_memory_operation_sites": 1},
            "meldra_borrow": {"explicit_memory_operation_sites": 1},
            "c_arena": {"explicit_memory_operation_sites": 8},
            "c_preallocated": {"explicit_memory_operation_sites": 1},
        },
    }
    assert _select_status(report) == "ADVANTAGE_MOSTLY_BASELINE_ARTIFACT"


def test_non_elidable_reference_depends_on_runtime_seed():
    first = non_elidable_reference_checksum(16, 123, 4)
    second = non_elidable_reference_checksum(16, 124, 4)
    assert first != second
    updated_only = non_elidable_reference_checksum(16, 123, 0)
    assert updated_only != (123 ^ 16)
    assert first != updated_only


def test_non_elidable_decision_requires_valid_stable_memory_traffic():
    report = {
        "validity_failures": [],
        "arms": [
            {
                "status": "MEASURED",
                "dispersion_gate_passed": True,
                "wall_ms": {"median": 250.0},
            }
            for _ in range(7)
        ],
        "ratios": {
            "meldra_region_over_c_arena": 1.05,
            "meldra_region_over_c_preallocated": 1.04,
            "meldra_borrow_over_c_preallocated": 1.03,
        },
    }
    assert (
        select_non_elidable_decision(report)
        == "AUTO_REGION_ZERO_OVERHEAD_SUPPORTED"
    )
    report["validity_failures"] = ["memory_traffic:meldra_region"]
    assert (
        select_non_elidable_decision(report)
        == "BENCHMARK_INVALID_OR_INCONCLUSIVE"
    )


def test_meldra_shaped_c_control_matches_frozen_reference(tmp_path):
    seed = 123
    factor = 4
    build = _compile_external(
        "c",
        _meldra_shaped_c_source(factor),
        tmp_path,
        ("256", str(seed)),
    )
    assert build.status == "MEASURED", build.stderr
    completed = subprocess.run(
        build.run_command,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0
    assert int(completed.stdout.strip()) == non_elidable_reference_checksum(
        256, seed, factor
    )


def test_constant_knowledge_decision_attributes_source_shape():
    def arm(name, median, interval):
        return {
            "name": name,
            "wall_ms": {
                "median": median,
                "bootstrap_median_95_ci": interval,
            },
        }

    report = {
        "validity_failures": [],
        "arms": [
            arm("meldra_current_region", 300.0, [298.0, 302.0]),
            arm("c_preallocated_runtime_n", 315.0, [313.0, 317.0]),
            arm("c_preallocated_const_n", 315.0, [313.0, 317.0]),
            arm("c_preallocated_meldra_shape", 301.0, [299.0, 303.0]),
        ],
        "hot_loop_comparisons": {
            "meldra_vs_meldra_shaped_c": {"loops": {}}
        },
    }
    assert (
        select_constant_knowledge_status(report)
        == "GAP_EXPLAINED_BY_SOURCE_OR_CODEGEN_SHAPE"
    )