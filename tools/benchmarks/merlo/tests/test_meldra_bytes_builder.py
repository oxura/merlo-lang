from __future__ import annotations

import re

import pytest

from research.archive.alpha1.merlo.bytes_builder import (
    bytes_builder_abi_manifest,
    bytes_builder_hir_manifest,
    bytes_builder_mir_manifest,
)
from merlo.native_c_backend import CEmitter
from research.archive.alpha1.merlo.native_differential import MIRInterpreter, evaluate_hir
from research.archive.alpha1.merlo.native_hir import compile_native_hir
from tools.benchmarks.merlo.performance_frontend import (
    PerformanceCompileError,
    compile_performance_source,
)
from tools.benchmarks.merlo.performance_opt import optimize_mir


BASIC_SOURCE = """fn main(n: UInt64) -> UInt64:
    let builder: BytesBuilder = BytesBuilder.new()
    builder.reserve(n)
    for i in 0..n:
        builder.push(i & 255)
    let view: BytesView = builder.as_view()
    let observed: UInt64 = view.len()
    let capacity: UInt64 = builder.capacity()
    let bytes: Bytes = builder.finish()
    return observed + bytes.len() + capacity
"""


@pytest.mark.parametrize("n", [0, 1, 8, 9, 17, 65, 257])
def test_builder_agrees_across_hir_mir_and_optimized(n):
    hir = compile_native_hir(BASIC_SOURCE)
    original = compile_performance_source(BASIC_SOURCE).mir
    optimized, _ = optimize_mir(original)
    observed = (
        evaluate_hir(hir, (n,)),
        MIRInterpreter(original).run((n,)),
        MIRInterpreter(optimized).run((n,)),
    )
    assert all(item.status == "OK" for item in observed)
    assert len({item.return_value for item in observed}) == 1
    assert all(item.finish_copies == 0 for item in observed)


def test_builder_reserved_path_has_one_allocation_and_no_growth_copy():
    mir = compile_performance_source(BASIC_SOURCE).mir
    result = MIRInterpreter(mir).run((1024,))
    assert result.allocations == 1
    assert result.reallocations == 0
    assert result.growth_copied_bytes == 0
    assert result.finish_copies == 0
    assert result.frees == 1


def test_builder_geometric_growth_preserves_amortized_bound():
    source = BASIC_SOURCE.replace("    builder.reserve(n)\n", "")
    result = MIRInterpreter(compile_performance_source(source).mir).run((4097,))
    capacity = 8192
    assert result.status == "OK"
    assert result.reallocations == 10
    assert result.growth_copied_bytes < 2 * capacity
    assert result.finish_copies == 0


def test_builder_hir_mir_and_abi_contracts_are_versioned():
    hir = compile_native_hir(BASIC_SOURCE, path="builder-contract.meldra")
    original = compile_performance_source(
        BASIC_SOURCE, path="builder-contract.meldra"
    ).mir
    optimized, _ = optimize_mir(original)
    hir_manifest = bytes_builder_hir_manifest(hir)
    mir_manifest = bytes_builder_mir_manifest(optimized)
    abi = bytes_builder_abi_manifest()
    assert hir_manifest["contract"] == "meldra.bytes-builder-hir.v1"
    assert hir_manifest["symbol_ids_present"] is True
    assert hir_manifest["source_mappings_present"] is True
    assert mir_manifest["contract"] == "meldra.bytes-builder-mir.v1"
    assert mir_manifest["validation"]["balanced_builder_views"] is True
    assert mir_manifest["validation"]["finish_transfer_visible"] is True
    assert abi["finish"]["payload_copies"] == 0


def test_builder_mir_event_identity_is_scoped_per_function():
    source = """fn abandoned(n: UInt64) -> UInt64:
    let builder: BytesBuilder = BytesBuilder.with_capacity(n)
    return builder.capacity()
fn main(n: UInt64) -> UInt64:
    let builder: BytesBuilder = BytesBuilder.new()
    builder.push(n & 255)
    let bytes: Bytes = builder.finish()
    return bytes.len()
"""
    manifest = bytes_builder_mir_manifest(
        compile_performance_source(source).mir
    )
    assert manifest["validation"]["builder_create_count"] == 2
    assert manifest["validation"]["builder_finish_count"] == 1
    assert manifest["validation"]["automatic_drop_present"] is True


@pytest.mark.parametrize(
    ("operation", "diagnostic"),
    [
        ("builder.push(2)", "cannot push BytesBuilder"),
        ("builder.reserve(2)", "cannot reserve BytesBuilder"),
        ("builder.finish()", "cannot finish BytesBuilder"),
        ("drop(builder)", "cannot drop BytesBuilder"),
        ("move(builder)", "cannot move BytesBuilder"),
    ],
)
def test_live_builder_view_blocks_owner_operations(operation, diagnostic):
    source = f"""fn main(n: UInt64) -> UInt64:
    let builder: BytesBuilder = BytesBuilder.new()
    builder.push(1)
    let view: BytesView = builder.as_view()
    {operation}
    return view.len()
"""
    with pytest.raises(PerformanceCompileError, match=diagnostic):
        compile_performance_source(source)


@pytest.mark.parametrize(
    ("body", "diagnostic"),
    [
        (
            "let first: Bytes = builder.finish()\n    let second: Bytes = builder.finish()",
            "use after finished",
        ),
        ("drop(builder)\n    drop(builder)", "double drop"),
        (
            "builder.extend(builder.as_view())",
            "overlapping alias mutation",
        ),
        ("builder.push(256)", "byte must be in 0..255"),
    ],
)
def test_builder_ownership_errors_are_compile_time(body, diagnostic):
    source = f"""fn main(n: UInt64) -> UInt64:
    let builder: BytesBuilder = BytesBuilder.new()
    {body}
    return n
"""
    with pytest.raises(PerformanceCompileError, match=diagnostic):
        compile_performance_source(source)


def test_unfinished_builder_gets_one_automatic_drop():
    source = """fn main(n: UInt64) -> UInt64:
    let builder: BytesBuilder = BytesBuilder.with_capacity(n)
    builder.push(1)
    return builder.len()
"""
    mir = compile_performance_source(source).mir
    drops = [
        instruction
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
        if instruction.op == "builder_drop"
    ]
    assert len(drops) == 1
    assert drops[0].attribute_map["automatic"] is True
    result = MIRInterpreter(mir).run((64,))
    assert result.status == "OK"
    assert result.allocations == result.frees == 1


def test_finish_codegen_is_direct_transfer_without_allocation_or_copy():
    generated = CEmitter(
        compile_performance_source(BASIC_SOURCE).mir,
        runtime_arguments=True,
    ).emit()
    line = next(
        item
        for item in generated.splitlines()
        if "meldra_bytes meldra_" in item and "= {" in item
    )
    assert "malloc" not in line
    assert "memcpy" not in line
    assert re.search(r"= \{ meldra_.*\.data, meldra_.*\.length", line)
