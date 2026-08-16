from __future__ import annotations

from dataclasses import replace

import pytest

from merlo.bounded_symbolic import SymbolicStatus, verify_bounded
from merlo.obligation_ir import build_obligation_ir
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface


def _verify(source: str, **bounds):
    canonical = elaborate_surface(parse_surface(source)).canonical
    hir = compile_canonical_hir(canonical)
    obligations = build_obligation_ir(hir)
    return verify_bounded(hir, obligations, **bounds)


def test_exhaustive_byte_domain_proves_postcondition() -> None:
    report = _verify(
        "fn identity(value: Byte) -> Byte:\n"
        "    ensure result == value\n"
        "    value\n\n"
        "fn main(input: BytesView) -> Byte:\n"
        "    identity(Byte(input.len()))\n"
    )

    assert len(report.results) == 1
    result = report.results[0]
    assert result.status == SymbolicStatus.PROVEN
    assert result.complete_domain
    assert result.explored_cases == 256


def test_bounded_executor_returns_a_real_counterexample() -> None:
    report = _verify(
        "fn wrong(value: Byte) -> Byte:\n"
        "    ensure result > value\n"
        "    value\n\n"
        "fn main(input: BytesView) -> Byte:\n"
        "    wrong(Byte(input.len()))\n"
    )

    result = report.results[0]
    assert result.status == SymbolicStatus.REFUTED
    assert result.counterexample is not None
    assert result.counterexample.inputs == (("value", 0),)
    assert result.counterexample.result == 0


def test_precondition_creates_a_complete_small_uint64_domain() -> None:
    report = _verify(
        "fn increment(value: UInt64) -> UInt64:\n"
        "    require value <= 10\n"
        "    ensure result > value\n"
        "    value + 1\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    increment(input.len())\n"
    )

    result = report.results[0]
    assert result.status == SymbolicStatus.PROVEN
    assert result.explored_cases == 11
    assert result.complete_domain


def test_large_unbounded_domain_is_never_claimed_as_proven() -> None:
    report = _verify(
        "fn identity(value: UInt64) -> UInt64:\n"
        "    ensure result == value\n"
        "    value\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    identity(input.len())\n",
        max_values_per_parameter=8,
    )

    result = report.results[0]
    assert result.status == SymbolicStatus.INCONCLUSIVE
    assert result.explored_cases == 3
    assert not result.complete_domain


def test_parameter_sampling_respects_the_configured_limit() -> None:
    report = _verify(
        "fn identity(value: UInt64) -> UInt64:\n"
        "    ensure result == value\n"
        "    value\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    UInt64(0)\n",
        max_values_per_parameter=1,
    )

    result = report.results[0]
    assert result.status == SymbolicStatus.INCONCLUSIVE
    assert result.explored_cases == 1



def test_checked_overflow_is_not_claimed_as_a_proof() -> None:
    report = _verify(
        "fn increment(value: Byte) -> Byte:\n"
        "    ensure result > value\n"
        "    value + Byte(1)\n\n"
        "fn main(input: BytesView) -> Byte:\n"
        "    Byte(0)\n"
    )

    result = report.results[0]
    assert result.status == SymbolicStatus.UNSUPPORTED
    assert result.explored_cases == 256
    assert result.reason is not None
    assert "SymbolicOverflow" in result.reason


def test_bool_domains_are_exhaustive() -> None:
    report = _verify(
        "fn invert(flag: Bool) -> Bool:\n"
        "    ensure result != flag\n"
        "    not flag\n\n"
        "fn main(input: BytesView) -> Bool:\n"
        "    true\n"
    )

    result = report.results[0]
    assert result.status == SymbolicStatus.PROVEN
    assert result.complete_domain
    assert result.explored_cases == 2


def test_case_bound_counts_rejected_precondition_inputs() -> None:
    report = _verify(
        "fn identity(value: Byte) -> Byte:\n"
        "    require value % Byte(2) == Byte(0)\n"
        "    ensure result == value\n"
        "    value\n\n"
        "fn main(input: BytesView) -> Byte:\n"
        "    Byte(0)\n",
        max_cases=10,
    )

    result = report.results[0]
    assert result.status == SymbolicStatus.INCONCLUSIVE
    assert result.explored_cases == 10
    assert not result.complete_domain




def test_branch_mutations_flow_to_following_statements() -> None:
    report = _verify(
        "fn choose(flag: Bool) -> UInt64:\n"
        "    ensure result == UInt64(0)\n"
        "    var value = UInt64(0)\n"
        "    if flag:\n"
        "        value += UInt64(1)\n"
        "    else:\n"
        "        value += UInt64(0)\n"
        "    value\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    UInt64(0)\n"
    )

    result = report.results[0]
    assert result.status == SymbolicStatus.REFUTED
    assert result.counterexample is not None
    assert result.counterexample.inputs == (("flag", True),)


def test_invariant_record_construction_is_not_assumed_safe() -> None:
    report = _verify(
        "record Positive:\n"
        "    value: UInt64\n"
        "    invariant value > UInt64(0)\n\n"
        "fn invalid() -> UInt64:\n"
        "    ensure result == UInt64(0)\n"
        "    Positive(value: UInt64(0)).value\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    UInt64(0)\n"
    )

    result = report.results[0]
    assert result.status == SymbolicStatus.UNSUPPORTED
    assert result.reason == "RecordInvariantRequiresGuard: Positive"


def test_float_to_integer_cast_uses_native_truncation() -> None:
    report = _verify(
        "fn truncate() -> Int64:\n"
        "    ensure result == Int64(1)\n"
        "    Int64(1.9)\n\n"
        "fn main(input: BytesView) -> Int64:\n"
        "    Int64(0)\n"
    )

    assert report.results[0].status == SymbolicStatus.PROVEN


def test_augmented_record_field_assignment_is_explicitly_unsupported() -> None:
    report = _verify(
        "record Counter:\n"
        "    value: UInt64\n\n"
        "fn bump() -> UInt64:\n"
        "    ensure result == UInt64(2)\n"
        "    var counter = Counter(value: UInt64(1))\n"
        "    counter.value += UInt64(1)\n"
        "    counter.value\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    UInt64(0)\n"
    )

    result = report.results[0]
    assert result.status == SymbolicStatus.UNSUPPORTED
    assert result.reason is not None
    assert "UnsupportedSymbolicFieldMutation" in result.reason


def test_negative_shift_degrades_to_unsupported() -> None:
    report = _verify(
        "fn shift(amount: Int64) -> Int64:\n"
        "    require amount >= -Int64(1)\n"
        "    require amount <= Int64(0)\n"
        "    ensure result == Int64(0)\n"
        "    Int64(1) << amount\n\n"
        "fn main(input: BytesView) -> Int64:\n"
        "    Int64(0)\n"
    )

    assert report.results[0].status == SymbolicStatus.UNSUPPORTED
def test_report_rejects_noncanonical_result_order() -> None:
    report = _verify(
        "fn identity(value: Byte) -> Byte:\n"
        "    ensure result == value\n"
        "    ensure result >= Byte(0)\n"
        "    value\n\n"
        "fn main(input: BytesView) -> Byte:\n"
        "    Byte(0)\n"
    )

    with pytest.raises(ValueError, match="SymbolicResultsNotCanonical"):
        replace(report, results=tuple(reversed(report.results)))

def test_symbolic_report_is_deterministic_and_hir_bound() -> None:
    source = (
        "fn identity(value: Byte) -> Byte:\n"
        "    ensure result == value\n"
        "    value\n\n"
        "fn main(input: BytesView) -> Byte:\n"
        "    identity(Byte(input.len()))\n"
    )

    first = _verify(source)
    second = _verify(source)
    assert first.to_json() == second.to_json()
    assert first.digest == second.digest
