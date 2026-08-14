from __future__ import annotations

import re

import pytest

from research.archive.alpha1.merlo.bytes_call_boundary import (
    bytes_call_abi_manifest,
    bytes_call_hir_manifest,
    bytes_call_mir_manifest,
)
from research.archive.alpha1.merlo.bytes_call_boundary_experiment import (
    BENCHMARK_MELDRA_SOURCE,
    BYTES_CALL_VALID_FAMILIES,
    INVALID_COMPILE_FAMILIES,
    _abi_audit,
    _invalid_source,
    valid_reference,
    valid_template_source,
)
from research.archive.alpha1.merlo.native_differential import MIRInterpreter, evaluate_hir
from research.archive.alpha1.merlo.native_hir import compile_native_hir
from tools.benchmarks.merlo.performance_frontend import (
    PerformanceCompileError,
    compile_performance_source,
)
from tools.benchmarks.merlo.performance_opt import optimize_mir


CALL_SOURCE = """fn checksum(data: BytesView) -> UInt64:
    return data.len()

fn transform(data: Bytes) -> Bytes:
    return data

fn main(n: UInt64) -> UInt64:
    let owner: Bytes = Bytes.new(n)
    let view: BytesView = owner.slice(0, n)
    let value: UInt64 = checksum(view)
    let result: Bytes = transform(move(owner))
    return value + result.len()
"""


def test_call_boundary_hir_and_mir_contracts_are_versioned() -> None:
    hir = compile_native_hir(CALL_SOURCE, path="call-contract.meldra")
    original = compile_performance_source(CALL_SOURCE).mir
    optimized, _ = optimize_mir(original)
    hir_manifest = bytes_call_hir_manifest(hir)
    mir_manifest = bytes_call_mir_manifest(optimized)
    abi = bytes_call_abi_manifest()

    assert hir_manifest["contract"] == "meldra.bytes-call-hir.v1"
    assert hir_manifest["lifetime_annotations_in_surface"] == 0
    assert {(item["type"], item["ownership_mode"]) for item in hir_manifest["parameters"]} == {
        ("Bytes", "owned"),
        ("BytesView", "borrowed"),
    }
    assert all(item["symbol_id"].startswith("nhirs_") for item in hir_manifest["parameters"])
    assert all(item["revision_id"].startswith("rev_") for item in hir_manifest["parameters"])
    assert any(item["arguments"] == ["borrow"] for item in mir_manifest["calls"])
    assert any(item["arguments"] == ["move"] for item in mir_manifest["calls"])
    assert any(item["return_ownership"] == "owned" for item in mir_manifest["calls"])
    assert mir_manifest["borrow_ends"][0]["scope"] == "synchronous_call"
    assert abi["reference_counting"] is False


def test_borrowed_and_owned_calls_execute_without_copy_or_extra_allocation() -> None:
    original = compile_performance_source(CALL_SOURCE).mir
    optimized, _ = optimize_mir(original)
    for mir in (original, optimized):
        observation = MIRInterpreter(mir).run((8,))
        assert observation.status == "OK"
        assert observation.return_value == 16
        assert observation.allocations == 1
        assert observation.drops == 1
        assert observation.retains == 0
        assert observation.releases == 1


def test_owned_argument_requires_explicit_move_and_old_owner_is_unusable() -> None:
    without_move = CALL_SOURCE.replace("transform(move(owner))", "transform(owner)")
    with pytest.raises(PerformanceCompileError, match=r"requires move\(owner\)"):
        compile_performance_source(without_move)

    after_move = CALL_SOURCE.replace(
        "return value + result.len()",
        "return value + result.len() + owner.len()",
    )
    with pytest.raises(PerformanceCompileError, match="use after move"):
        compile_performance_source(after_move)


@pytest.mark.parametrize(
    "family",
    ["return_view", "record_contains_view"],
)
def test_borrowed_escape_forms_are_rejected(family: str) -> None:
    source, expected = _invalid_source(family, 7)
    with pytest.raises(PerformanceCompileError, match=expected):
        compile_performance_source(source)


def test_owner_can_be_mutated_or_moved_after_last_borrowed_call() -> None:
    for family in ("owner_mutation_after_call", "owner_move_after_call"):
        source, salt = valid_template_source(family)
        arguments = (17, 23, 3, 7, 1)
        expected = valid_reference(family, arguments, salt)
        hir = compile_native_hir(source, path=f"{family}.meldra")
        mir = compile_performance_source(source).mir
        assert evaluate_hir(hir, arguments).return_value == expected
        assert MIRInterpreter(mir).run(arguments).return_value == expected


def test_conditional_owned_return_is_balanced() -> None:
    source, salt = valid_template_source("conditional_owned_return")
    for flag in (0, 1):
        arguments = (17, 23, 3, 7, flag)
        expected = valid_reference("conditional_owned_return", arguments, salt)
        observation = MIRInterpreter(compile_performance_source(source).mir).run(arguments)
        assert observation.return_value == expected
        assert observation.allocations == 1
        assert observation.drops == 1


def test_every_valid_template_agrees_with_independent_reference() -> None:
    arguments = (17, 23, 3, 7, 1)
    for family in BYTES_CALL_VALID_FAMILIES:
        source, salt = valid_template_source(family)
        expected = valid_reference(family, arguments, salt)
        hir = compile_native_hir(source, path=f"templates/{family}.meldra")
        original = compile_performance_source(source).mir
        optimized, _ = optimize_mir(original)
        assert evaluate_hir(hir, arguments).return_value == expected
        assert MIRInterpreter(original).run(arguments).return_value == expected
        assert MIRInterpreter(optimized).run(arguments).return_value == expected


@pytest.mark.parametrize("family", INVALID_COMPILE_FAMILIES)
def test_every_compile_time_invalid_family_has_exact_diagnostic(family: str) -> None:
    source, expected = _invalid_source(family, 3)
    with pytest.raises(PerformanceCompileError, match=re.escape(expected)):
        compile_performance_source(source)


def test_noinline_abi_audit_detects_copy_control(tmp_path) -> None:
    original = compile_performance_source(BENCHMARK_MELDRA_SOURCE).mir
    optimized, _ = optimize_mir(original)
    evidence = _abi_audit(tmp_path, optimized)

    assert evidence["passed"] is True
    assert evidence["borrowed_parameter"] == {
        "allocations_during_call": 0,
        "frees_during_call": 0,
        "payload_copies": 0,
        "retains": 0,
        "releases": 0,
        "proof": "no-inline helper C and assembly contain no allocator, free, copy, or RC calls",
    }
    assert evidence["owned_transfer"]["source_allocations"] == 1
    assert evidence["owned_transfer"]["return_transfer_allocations"] == 0
    assert evidence["owned_transfer"]["final_frees"] == 1
    assert evidence["falsification_control"]["detected"] is True
