from __future__ import annotations

import subprocess

import pytest

from research.archive.alpha1.merlo.bytes_contract import (
    BYTES_HIR_CONTRACT,
    BYTES_MIR_CONTRACT,
    bytes_hir_manifest,
    bytes_mir_manifest,
)
from merlo.native_c_backend import CEmitter, compile_c_source
from research.archive.alpha1.merlo.native_differential import MIRInterpreter
from research.archive.alpha1.merlo.native_hir import compile_native_hir
from tools.benchmarks.merlo.performance_frontend import PerformanceCompileError, compile_performance_source
from tools.benchmarks.merlo.performance_opt import optimize_mir


NLL_SOURCE = """fn main(n: UInt64) -> UInt64:
    let owner: Bytes = Bytes.new(n)
    owner[0] = 7
    let view: BytesView = owner.slice(0, n)
    let observed: UInt64 = view[0] + view.len()
    owner[0] = observed
    observed
"""


def test_owned_bytes_and_nonlexical_view_run_in_mir_and_native(tmp_path):
    original = compile_performance_source(NLL_SOURCE, path="bytes-nll.meldra").mir
    optimized, _ = optimize_mir(original)

    original_observation = MIRInterpreter(original).run((8,))
    optimized_observation = MIRInterpreter(optimized).run((8,))
    assert original_observation.return_value == optimized_observation.return_value == 15
    assert dict(original_observation.final_ownership_state) == {"Dropped": 1}
    assert dict(optimized_observation.final_ownership_state) == {"Dropped": 1}

    build = compile_c_source(
        CEmitter(optimized, runtime_arguments=True).emit(),
        output_dir=tmp_path,
        stem="bytes_nll",
    )
    assert build.status == "MEASURED", build.stderr
    completed = subprocess.run(
        (build.binary_path, "8"), capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "15"
    assert "MELDRA_ALLOCATIONS=1" in completed.stderr
    assert "MELDRA_FREES=1" in completed.stderr
    assert "MELDRA_PAYLOAD_COPIES=0" in completed.stderr


def test_bytes_move_invalidates_source_and_automatically_drops_destination():
    source = """fn main(n: UInt64) -> UInt64:
    let owner: Bytes = Bytes.new(n)
    owner[0] = 23
    let moved: Bytes = move(owner)
    moved[0]
"""
    mir = compile_performance_source(source).mir
    observation = MIRInterpreter(mir).run((4,))
    assert observation.return_value == 23
    assert dict(observation.final_ownership_state) == {"Dropped": 1, "Moved": 1}

    invalid = """fn main(n: UInt64) -> UInt64:
    let owner: Bytes = Bytes.new(n)
    let moved: Bytes = move(owner)
    owner.len() + moved.len()
"""
    with pytest.raises(PerformanceCompileError, match="use after move: owner"):
        compile_performance_source(invalid)


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    [
        (
            """fn main(n: UInt64) -> UInt64:
    let owner: Bytes = Bytes.new(n)
    let view: BytesView = owner.slice(0, n)
    owner[0] = 1
    view[0]
""",
            "cannot mutate Bytes owner owner while view view is live",
        ),
        (
            """fn main(n: UInt64) -> UInt64:
    let owner: Bytes = Bytes.new(n)
    let view: BytesView = owner.slice(0, n)
    drop(owner)
    view.len()
""",
            "cannot drop Bytes owner owner while view view is live",
        ),
        (
            """fn main(n: UInt64) -> UInt64:
    let owner: Bytes = Bytes.new(n)
    let view: BytesView = owner.slice(0, n)
    view[0] = 1
    view[0]
""",
            "cannot mutate borrowed BytesView",
        ),
        (
            """fn main(n: UInt64) -> BytesView:
    let owner: Bytes = Bytes.new(n)
    let view: BytesView = owner.slice(0, n)
    view
""",
            "borrowed BytesView view cannot escape main",
        ),
        (
            """fn main(n: UInt64) -> UInt64:
    let owner: Bytes = Bytes.new(n)
    drop(owner)
    drop(owner)
    0
""",
            "double drop of Bytes owner owner",
        ),
    ],
)
def test_invalid_bytes_ownership_is_rejected(source, diagnostic):
    with pytest.raises(PerformanceCompileError, match=diagnostic):
        compile_performance_source(source)


def test_bytes_runtime_bounds_and_slice_diagnostics_are_typed():
    index_source = """fn main(n: UInt64, index: UInt64) -> UInt64:
    let owner: Bytes = Bytes.new(n)
    owner[index]
"""
    index_observation = MIRInterpreter(
        compile_performance_source(index_source).mir
    ).run((8, 8))
    assert index_observation.status == "ERROR"
    assert index_observation.error_kind == "BytesIndexOutOfBounds"

    slice_source = """fn main(n: UInt64, start: UInt64, length: UInt64) -> UInt64:
    let owner: Bytes = Bytes.new(n)
    let view: BytesView = owner.slice(start, length)
    view.len()
"""
    slice_observation = MIRInterpreter(
        compile_performance_source(slice_source).mir
    ).run((8, 7, 2))
    assert slice_observation.status == "ERROR"
    assert slice_observation.error_kind == "BytesSliceOutOfBounds"


def test_bytes_hir_and_mir_contracts_are_versioned():
    native_hir = compile_native_hir(NLL_SOURCE, path="bytes-contract.meldra")
    mir = compile_performance_source(NLL_SOURCE, path="bytes-contract.meldra").mir
    hir = bytes_hir_manifest(native_hir)
    lowered = bytes_mir_manifest(mir)

    assert hir["contract"] == BYTES_HIR_CONTRACT
    assert hir["schema_version"] == 1
    assert hir["owned_type"]["fields"] == ["pointer", "length", "capacity", "live"]
    assert hir["borrowed_type"]["fields"] == ["pointer", "length"]
    assert hir["typed_node_ids"]
    assert lowered["contract"] == BYTES_MIR_CONTRACT
    assert lowered["schema_version"] == 1
    assert lowered["automatic_drop"] is True
    assert {"bytes_new", "bytes_slice", "bytes_load", "bytes_store", "drop"}.issubset(
        lowered["operations"]
    )
