from __future__ import annotations
from pathlib import Path

import pytest

from merlo.borrow_summary import BorrowPlaceStep
from merlo.semantic_world import SemanticWorld
from merlo.structured_hir_v2 import (
    StructuredHIRCompileError,
    StructuredHIRProgram,
    compile_structured_hir,
)


_USER_HEADER = (
    "record User:\n"
    "    name: Text\n"
    "    age: UInt64\n"
)


def _local_field_source(operation: str) -> str:
    return (
        _USER_HEADER
        + "fn main(input: BytesView) -> UInt64:\n"
        + "    let user: User = User(Text.from_bytes(input, 0, input.len()), 0)\n"
        + "    let view: TextView = user.name.as_view()\n"
        + f"    {operation}\n"
        + "    return view.len()\n"
    )


def _interprocedural_field_source(operation: str) -> str:
    return (
        _USER_HEADER
        + "fn borrow_name(user: User) -> TextView:\n"
        + "    return user.name.as_view()\n"
        + "fn main(input: BytesView) -> UInt64:\n"
        + "    let user: User = User(Text.from_bytes(input, 0, input.len()), 0)\n"
        + "    let view: TextView = borrow_name(user)\n"
        + f"    {operation}\n"
        + "    return view.len()\n"
    )


def test_field_borrow_allows_disjoint_mutation() -> None:
    compile_structured_hir(_local_field_source("user.age = 1"))


def test_field_borrow_rejects_mutation_of_the_borrowed_field() -> None:
    with pytest.raises(StructuredHIRCompileError, match="MutationDuringBorrow: user.name"):
        compile_structured_hir(
            _local_field_source(
                "user.name = Text.from_bytes(input, 0, input.len())"
            )
        )


def test_field_borrow_rejects_ancestor_drop_with_place_diagnostic() -> None:
    with pytest.raises(StructuredHIRCompileError) as raised:
        compile_structured_hir(_local_field_source("drop(user)"))
    diagnostic = str(raised.value)
    assert "BackingOwnerDropWhileBorrowed" in diagnostic
    assert "backing_owner=user" in diagnostic
    assert '"steps":[{"field_id"' in diagnostic


def test_field_borrow_rejects_ancestor_move() -> None:
    source = (
        _USER_HEADER
        + "fn forward(user: User) -> User:\n"
        + "    return user\n"
        + "fn main(input: BytesView) -> UInt64:\n"
        + "    let user: User = User(Text.from_bytes(input, 0, input.len()), 0)\n"
        + "    let view: TextView = user.name.as_view()\n"
        + "    let moved: User = forward(user)\n"
        + "    return view.len() + moved.age\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="BackingOwnerMoveWhileBorrowed"):
        compile_structured_hir(source)


def test_borrow_summary_preserves_field_source_path_and_disjoint_caller_mutation() -> None:
    hir = compile_structured_hir(_interprocedural_field_source("user.age = 1"))
    relation = hir.function("borrow_name").borrow_summary.relations[0]
    assert relation.source_path.steps == (
        BorrowPlaceStep.parameter(0),
        BorrowPlaceStep.field("name"),
    )


def test_interprocedural_field_borrow_rejects_overlapping_mutation() -> None:
    with pytest.raises(StructuredHIRCompileError, match="MutationDuringBorrow: user.name"):
        compile_structured_hir(
            _interprocedural_field_source(
                "user.name = Text.from_bytes(input, 0, input.len())"
            )
        )


def test_interprocedural_field_borrow_rejects_ancestor_drop() -> None:
    with pytest.raises(StructuredHIRCompileError, match="BackingOwnerDropWhileBorrowed"):
        compile_structured_hir(_interprocedural_field_source("drop(user)"))


def test_constant_index_places_are_disjoint() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let values: Vec[Text] = Vec.new()\n"
        "    values.push(Text.from_bytes(input, 0, input.len()))\n"
        "    let view: TextView = values.get(0).as_view()\n"
        "    values[1] = Text.from_bytes(input, 0, input.len())\n"
        "    return view.len()\n"
    )
    compile_structured_hir(source)


def test_constant_index_place_mutation_rejects_same_element() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let values: Vec[Text] = Vec.new()\n"
        "    values.push(Text.from_bytes(input, 0, input.len()))\n"
        "    let view: TextView = values.get(0).as_view()\n"
        "    values[0] = Text.from_bytes(input, 0, input.len())\n"
        "    return view.len()\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="MutationDuringBorrow"):
        compile_structured_hir(source)


def test_enum_payload_place_rejects_ancestor_drop() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let choice: Option[Text] = Some(Text.from_bytes(input, 0, input.len()))\n"
        "    let view: TextView = choice.unwrap().as_view()\n"
        "    drop(choice)\n"
        "    return view.len()\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="BackingOwnerDropWhileBorrowed"):
        compile_structured_hir(source)


def test_transitive_field_summary_has_one_structural_path() -> None:
    source = (
        _USER_HEADER
        + "fn borrow_name(user: User) -> TextView:\n"
        + "    return user.name.as_view()\n"
        + "fn wrapper(user: User) -> TextView:\n"
        + "    return borrow_name(user)\n"
        + "fn main(input: BytesView) -> UInt64:\n"
        + "    let user: User = User(Text.from_bytes(input, 0, input.len()), 0)\n"
        + "    let view: TextView = wrapper(user)\n"
        + "    user.age = 1\n"
        + "    return view.len()\n"
    )
    hir = compile_structured_hir(source)
    relation = hir.function("wrapper").borrow_summary.relations[0]
    assert relation.source_path.steps == (
        BorrowPlaceStep.parameter(0),
        BorrowPlaceStep.field("name"),
    )


def test_field_alias_move_requires_partial_move_support() -> None:
    source = (
        _USER_HEADER
        + "fn borrow_alias(user: User) -> TextView:\n"
        + "    let alias: Text = user.name\n"
        + "    return alias.as_view()\n"
        + "fn main(input: BytesView) -> UInt64:\n"
        + "    let user: User = User(Text.from_bytes(input, 0, input.len()), 0)\n"
        + "    let view: TextView = borrow_alias(user)\n"
        + "    user.age = 1\n"
        + "    return view.len()\n"
    )
    with pytest.raises(
        StructuredHIRCompileError,
        match="^ProjectedOwnerMoveRequiresPartialMoveSupport$",
    ):
        compile_structured_hir(source)


def test_record_borrow_field_flow_does_not_block_unrelated_owner() -> None:
    source = (
        "record Holder:\n"
        "    first: TextView\n"
        "    second: TextView\n"
        "fn pick_first(holder: Holder) -> TextView:\n"
        "    return holder.first\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let one: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let two: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let unrelated: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let holder: Holder = Holder(one.as_view(), two.as_view())\n"
        "    let view: TextView = pick_first(holder)\n"
        "    drop(unrelated)\n"
        "    return view.len()\n"
    )
    compile_structured_hir(source)


def test_box_dereference_summary_preserves_source_place() -> None:
    source = (
        "fn borrow_box(boxed: Box[Text]) -> TextView:\n"
        "    return boxed.get().as_view()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let boxed: Box[Text] = Box.new(text)\n"
        "    let view: TextView = borrow_box(boxed)\n"
        "    return view.len()\n"
    )
    hir = compile_structured_hir(source)
    relation = hir.function("borrow_box").borrow_summary.relations[0]
    assert relation.source_path.steps == (
        BorrowPlaceStep.parameter(0),
        BorrowPlaceStep.dereference(),
    )


def test_box_dereference_summary_rejects_ancestor_drop() -> None:
    source = (
        "fn borrow_box(boxed: Box[Text]) -> TextView:\n"
        "    return boxed.get().as_view()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let boxed: Box[Text] = Box.new(text)\n"
        "    let view: TextView = borrow_box(boxed)\n"
        "    drop(boxed)\n"
        "    return view.len()\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="BackingOwnerDropWhileBorrowed"):
        compile_structured_hir(source)


def test_field_place_summary_survives_hir_artifact_roundtrip() -> None:
    hir = compile_structured_hir(_interprocedural_field_source("user.age = 1"))
    restored = StructuredHIRProgram.from_json(hir.to_json())
    assert restored.digest == hir.digest
    assert (
        restored.function("borrow_name").borrow_summary.semantic_dict()
        == hir.function("borrow_name").borrow_summary.semantic_dict()
    )


def test_semantic_world_exposes_field_borrow_summary(tmp_path: Path) -> None:
    source = tmp_path / "main.mlo"
    source.write_text(
        "module app.main\n\n"
        "export record User:\n"
        "    name: Text\n"
        "    age: UInt64\n\n"
        "export fn borrow_name(user: User) -> TextView:\n"
        "    return user.name.as_view()\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export task main(path: Path) -> Result[Text,AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"summary\")\n"
        "    return Ok(\"ok\")\n",
        encoding="utf-8",
    )
    world = SemanticWorld.build(source, require_interface_lock=False)
    symbol = world.inspect("app.main.borrow_name")["symbol"]
    relation = symbol["borrow_summary"]["entries"][0]["relation"]
    assert relation["source_path"]["steps"][1] == {
        "kind": "Field",
        "value": "name",
    }
