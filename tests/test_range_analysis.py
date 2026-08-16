from __future__ import annotations

from dataclasses import replace

import pytest

from merlo.obligation_ir import (
    ObligationCategory,
    ObligationDisposition,
)
from merlo.range_analysis import (
    IntegerRange,
    analyze_constant_ranges,
)
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface


def _analyze(source: str):
    canonical = elaborate_surface(parse_surface(source)).canonical
    return analyze_constant_ranges(compile_canonical_hir(canonical))


def test_integer_range_arithmetic_and_intersection() -> None:
    value = IntegerRange(2, 5)

    assert value.intersect(IntegerRange(4, 8)) == IntegerRange(4, 5)
    assert value.intersect(IntegerRange(6, 8)) is None
    assert value.add(IntegerRange(1, 3)) == IntegerRange(3, 8)
    assert value.subtract(IntegerRange(1, 3)) == IntegerRange(-1, 4)
    assert value.multiply(IntegerRange(-2, 3)) == IntegerRange(-10, 15)


def test_preconditions_and_true_branches_refine_ranges() -> None:
    result = _analyze(
        "fn bounded(value: UInt64) -> UInt64:\n"
        "    require value <= 10\n"
        "    if value > 4:\n"
        "        return value + 1\n"
        "    0\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    bounded(input.len())\n"
    )

    branch_facts = [
        item
        for item in result.facts
        if item.name == "value" and item.reason == "branch_true"
    ]
    assert len(branch_facts) == 1
    assert branch_facts[0].value_range == IntegerRange(5, 10)
    assert len(result.obligations) == 1
    assert (
        result.obligations[0].disposition
        == ObligationDisposition.STATICALLY_PROVEN
    )
    assert result.obligations[0].discharged_by == (
        "constant_range_analysis"
    )


def test_unknown_and_impossible_overflow_are_distinct() -> None:
    unresolved = _analyze(
        "fn increment(value: UInt64) -> UInt64:\n"
        "    value + 1\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    increment(input.len())\n"
    )
    refuted = _analyze(
        "fn impossible() -> Byte:\n"
        "    Byte(250) + Byte(250)\n\n"
        "fn main(input: BytesView) -> Byte:\n"
        "    impossible()\n"
    )

    assert unresolved.obligations[0].disposition == (
        ObligationDisposition.UNRESOLVED
    )
    arithmetic = [
        item
        for item in refuted.obligations
        if item.category == ObligationCategory.ARITHMETIC_SAFETY
    ]
    assert arithmetic[0].disposition == (
        ObligationDisposition.STATICALLY_REFUTED
    )


def test_contradictory_branch_is_reported_unreachable() -> None:
    result = _analyze(
        "fn bounded(value: UInt64) -> UInt64:\n"
        "    require value <= 10\n"
        "    if value > 20:\n"
        "        return value + 1\n"
        "    0\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    bounded(input.len())\n"
    )

    assert len(result.unreachable_branch_ids) == 1
    assert not result.obligations



def test_cast_intrinsics_and_augmented_assignments_emit_obligations() -> None:
    cast = _analyze(
        "fn cast(value: UInt64) -> Byte:\n"
        "    Byte(value) + Byte(0)\n\n"
        "fn main(input: BytesView) -> Byte:\n"
        "    cast(input.len())\n"
    )
    checked = _analyze(
        "fn update(value: UInt64) -> UInt64:\n"
        "    let sum = checked_add(value, 1)\n"
        "    var total = value\n"
        "    total += 1\n"
        "    sum + total\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    update(input.len())\n"
    )
    indirect = _analyze(
        "fn narrow(value: UInt64) -> Byte:\n"
        "    Byte(value)\n\n"
        "fn add(value: UInt64) -> Byte:\n"
        "    checked_add(narrow(value), Byte(0))\n\n"
        "fn main(input: BytesView) -> Byte:\n"
        "    add(input.len())\n"
    )

    cast_safety = [
        item
        for item in cast.obligations
        if item.category == ObligationCategory.TYPE_SAFETY
    ]
    assert len(cast_safety) == 2
    assert any(
        item.disposition == ObligationDisposition.UNRESOLVED
        for item in cast_safety
    )
    cast_arithmetic = [
        item
        for item in cast.obligations
        if item.category == ObligationCategory.ARITHMETIC_SAFETY
    ]
    assert cast_arithmetic[0].disposition == (
        ObligationDisposition.UNRESOLVED
    )
    assert set(cast_arithmetic[0].dependencies) == {
        item.obligation_id for item in cast_safety
    }
    indirect_arithmetic = [
        item
        for item in indirect.obligations
        if item.category == ObligationCategory.ARITHMETIC_SAFETY
    ]
    assert indirect_arithmetic[0].disposition == (
        ObligationDisposition.UNRESOLVED
    )
    arithmetic = [
        item
        for item in checked.obligations
        if item.category == ObligationCategory.ARITHMETIC_SAFETY
    ]
    assert len(arithmetic) == 3
    assert all(
        item.disposition == ObligationDisposition.UNRESOLVED
        for item in arithmetic
    )


def test_range_result_rejects_noncanonical_collections() -> None:
    result = _analyze(
        "fn increment(value: UInt64) -> UInt64:\n"
        "    value + 1\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    increment(input.len())\n"
    )

    with pytest.raises(ValueError, match="RangeFactsNotCanonical"):
        replace(result, facts=tuple(reversed(result.facts)))

def test_range_analysis_is_deterministic() -> None:
    source = (
        "fn increment(value: UInt64) -> UInt64:\n"
        "    value + 1\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    increment(input.len())\n"
    )

    first = _analyze(source)
    second = _analyze(source)
    assert first.to_json() == second.to_json()
    assert first.digest == second.digest
