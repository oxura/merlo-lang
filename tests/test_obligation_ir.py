from __future__ import annotations

import json
from dataclasses import replace

import pytest

from merlo.obligation_ir import (
    OBLIGATION_IR_CONTRACT,
    ObligationCategory,
    ObligationDisposition,
    ObligationProgram,
    build_obligation_ir,
    replace_obligations,
)
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface


SOURCE = """record Positive:
    value: UInt64
    invariant value > 0

fn checked(value: UInt64) -> UInt64:
    require value > 0
    ensure result >= value
    let item = Positive(value)
    item.value

fn main(input: BytesView) -> UInt64:
    checked(input.len())
"""


def _program() -> ObligationProgram:
    canonical = elaborate_surface(parse_surface(SOURCE)).canonical
    return build_obligation_ir(compile_canonical_hir(canonical))


def test_explicit_contracts_and_invariants_become_typed_obligations() -> None:
    program = _program()

    assert program.contract == OBLIGATION_IR_CONTRACT
    assert {
        item.category for item in program.obligations
    } == {
        ObligationCategory.FUNCTION_PRECONDITION,
        ObligationCategory.FUNCTION_POSTCONDITION,
        ObligationCategory.DATA_INVARIANT,
    }
    assert all(
        item.expected_type == "Bool"
        for item in program.obligations
    )
    assert all(
        item.disposition == ObligationDisposition.RUNTIME_GUARDED
        for item in program.obligations
    )
    postcondition = program.by_category(
        ObligationCategory.FUNCTION_POSTCONDITION
    )[0]
    assert [item.name for item in postcondition.context] == [
        "value",
        "result",
    ]
    invariant = program.by_category(
        ObligationCategory.DATA_INVARIANT
    )[0]
    assert invariant.context[0].to_dict() == {
        "name": "value",
        "type": "UInt64",
        "ownership": "field",
    }


def test_obligation_ids_are_stable_but_revisions_follow_predicates() -> None:
    original = _program()
    changed_source = SOURCE.replace(
        "ensure result >= value",
        "ensure result > value",
    )
    canonical = elaborate_surface(
        parse_surface(changed_source)
    ).canonical
    changed = build_obligation_ir(compile_canonical_hir(canonical))

    original_post = original.by_category(
        ObligationCategory.FUNCTION_POSTCONDITION
    )[0]
    changed_post = changed.by_category(
        ObligationCategory.FUNCTION_POSTCONDITION
    )[0]
    assert original_post.obligation_id == changed_post.obligation_id
    assert original_post.revision_id != changed_post.revision_id


def test_unrelated_contract_insertion_preserves_obligation_id() -> None:
    original = _program()
    inserted_source = SOURCE.replace(
        "    ensure result >= value\n",
        "    ensure result > 0\n"
        "    ensure result >= value\n",
    )
    canonical = elaborate_surface(
        parse_surface(inserted_source)
    ).canonical
    inserted = build_obligation_ir(compile_canonical_hir(canonical))

    original_item = next(
        item
        for item in original.obligations
        if item.predicate == "result >= value"
    )
    inserted_item = next(
        item
        for item in inserted.obligations
        if item.predicate == "result >= value"
    )
    assert original_item.obligation_id == inserted_item.obligation_id


def test_typed_hole_becomes_unresolved_completion_obligation() -> None:
    source = (
        "fn fill() -> UInt64:\n"
        "    ?\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    fill()\n"
    )
    canonical = elaborate_surface(parse_surface(source)).canonical
    program = build_obligation_ir(compile_canonical_hir(canonical))

    hole = program.by_category(ObligationCategory.TYPED_HOLE)[0]
    assert hole.expected_type == "UInt64"
    assert hole.disposition == ObligationDisposition.UNRESOLVED
    assert hole.predicate.startswith("complete hole_")


def test_obligation_serialization_is_canonical_and_digest_bound() -> None:
    first = _program()
    second = _program()

    assert first.to_json() == second.to_json()
    assert first.digest == second.digest
    payload = json.loads(first.to_json())
    assert payload["hir_digest"] == first.hir_digest
    assert [
        item["obligation_id"] for item in payload["obligations"]
    ] == sorted(
        item["obligation_id"] for item in payload["obligations"]
    )


def test_discharge_updates_are_typed_and_dependency_checked() -> None:
    program = _program()
    target = program.obligations[0]
    proven = replace(
        target,
        disposition=ObligationDisposition.STATICALLY_PROVEN,
        discharged_by="constant_evaluator",
    )
    updated = replace_obligations(program, (proven,))

    assert next(
        item
        for item in updated.obligations
        if item.obligation_id == target.obligation_id
    ).disposition == ObligationDisposition.STATICALLY_PROVEN
    with pytest.raises(KeyError, match="UnknownObligation"):
        replace_obligations(
            program,
            (replace(proven, obligation_id="obl_missing"),),
        )
    with pytest.raises(
        ValueError,
        match="ObligationIdentityMutation",
    ):
        replace_obligations(
            program,
            (
                replace(
                    proven,
                    category=ObligationCategory.TERMINATION,
                ),
            ),
        )
    with pytest.raises(
        ValueError,
        match="UnknownObligationDependency",
    ):
        ObligationProgram(
            program.hir_digest,
            (
                replace(
                    target,
                    dependencies=("obl_missing",),
                ),
            ),
        )
