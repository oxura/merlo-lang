from __future__ import annotations

import pytest

from merlo.representation_ir import (
    build_drop_plans,
    build_type_descriptors,
    lower_structured_hir_to_rir,
    storage_policy_matrix,
)
from merlo.representation_mir import lower_rir_to_performance_mir
from merlo.structured_hir_v2 import StructuredHIRCompileError, compile_structured_hir


def test_storage_policy_matrix_covers_every_alpha_storage_class() -> None:
    hir = compile_structured_hir(
        "record Packet:\n"
        "    label: Text\n"
        "enum Tree:\n"
        "    Leaf: UInt64\n"
        "    Branch: Vec[Tree]\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return input.len()\n"
    )
    policies = storage_policy_matrix(build_type_descriptors(hir))

    assert policies["UInt64"].storage == "inline_copy"
    assert policies["BytesView"].storage == "borrowed_view"
    assert policies["Text"].storage == "unique_owner"
    assert policies["Packet"].drop == "fieldwise"
    assert policies["Tree"].drop == "tag_switch"
    assert all(policy.shared_ownership is False for policy in policies.values())


def test_use_after_move_is_rejected() -> None:
    source = (
        "fn forward(text: Text) -> Text:\n"
        "    return text\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let moved: Text = forward(text)\n"
        "    return text.len()\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="UseAfterMove: text"):
        compile_structured_hir(source)


def test_use_after_drop_and_duplicate_drop_are_rejected() -> None:
    use_after_drop = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    drop(text)\n"
        "    return text.len()\n"
    )
    duplicate_drop = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    drop(text)\n"
        "    drop(text)\n"
        "    return 0\n"
    )

    with pytest.raises(StructuredHIRCompileError, match="UseAfterDrop: text"):
        compile_structured_hir(use_after_drop)
    with pytest.raises(StructuredHIRCompileError, match="DuplicateDrop: text"):
        compile_structured_hir(duplicate_drop)


def test_mutation_during_live_shared_borrow_is_rejected() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let values: Vec[UInt64] = Vec.new()\n"
        "    values.push(input.len())\n"
        "    let view: Borrow[Vec[UInt64]] = values.view()\n"
        "    values.push(1)\n"
        "    return view.len()\n"
    )

    with pytest.raises(StructuredHIRCompileError, match="MutationDuringBorrow: values"):
        compile_structured_hir(source)


def test_view_cannot_escape_local_owner() -> None:
    source = (
        "fn main(input: BytesView) -> TextView:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    return text.as_view()\n"
    )

    with pytest.raises(StructuredHIRCompileError, match="EscapedView: text"):
        compile_structured_hir(source)


def test_recursive_owner_drop_plans_are_total() -> None:
    hir = compile_structured_hir(
        "enum Tree:\n"
        "    Leaf: UInt64\n"
        "    Branch: Vec[Tree]\n"
        "record Forest:\n"
        "    root: Box[Tree]\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return input.len()\n"
    )
    plans = {item.type_name: item for item in build_drop_plans(build_type_descriptors(hir))}

    assert plans["Tree"].action == "enum_active_payload"
    assert plans["Vec[Tree]"].action == "vec_initialized_elements_then_buffer"
    assert plans["Box[Tree]"].action == "box_payload_then_free"
    assert plans["Forest"].action == "record_fieldwise"


def test_owned_moves_and_cleanup_survive_rir_and_mir() -> None:
    source = (
        "fn forward(text: Text) -> Text:\n"
        "    return text\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let moved: Text = forward(text)\n"
        "    return moved.len()\n"
    )
    hir = compile_structured_hir(source)
    rir = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, rir)
    operations = {item.op for function in rir.functions for item in function.walk()}
    instructions = {
        item.op
        for function in mir.functions
        for block in function.blocks
        for item in block.instructions
    }

    assert "call" in operations
    assert "move_value" in instructions
    assert "drop_value" in instructions
    assert mir.requires_drop_glue is True
