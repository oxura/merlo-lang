from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from merlo.borrow_summary import (
    BORROW_SUMMARY_CYCLE_MARKER,
    BorrowPlacePath,
    BorrowPlaceStep,
    BorrowSummaryEntry,
)
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.semantic_world import SemanticWorld
from merlo.structured_hir_v2 import (
    StructuredHIRCompileError,
    StructuredHIRProgram,
    compile_structured_hir,
)


def test_direct_borrow_summary_is_versioned_and_interprocedural() -> None:
    source = (
        "fn borrow_text(text: Text) -> TextView:\n"
        "    return text.as_view()\n"
        "fn wrapper(text: Text) -> TextView:\n"
        "    return borrow_text(text)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let view: TextView = wrapper(text)\n"
        "    drop(text)\n"
        "    return view.len()\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="BackingOwnerDropWhileBorrowed") as raised:
        compile_structured_hir(source)
    text = str(raised.value)
    assert "callee=wrapper" in text
    assert "formal[0]=text" in text
    assert "backing_owner=text" in text
    assert "borrow_text" in text


def test_contained_summary_supports_box_and_nested_option() -> None:
    source = (
        "fn make(text: Text) -> Option[Box[TextView]]:\n"
        "    let boxed: Box[TextView] = Box.new(text.as_view())\n"
        "    return Some(boxed)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let value: Option[Box[TextView]] = make(text)\n"
        "    drop(text)\n"
        "    drop(value)\n"
        "    return 0\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="BackingOwnerDropWhileBorrowed") as raised:
        compile_structured_hir(source)
    assert "container=Option[Box[TextView]]" in str(raised.value)
    assert "contained_borrow=TextView" in str(raised.value)


def test_bytes_box_summary_tracks_actual_owner() -> None:
    source = (
        "fn make(bytes: Bytes) -> Box[BytesView]:\n"
        "    return Box.new(bytes.view())\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let bytes: Bytes = Bytes.from_view(input)\n"
        "    let boxed: Box[BytesView] = make(bytes)\n"
        "    drop(bytes)\n"
        "    drop(boxed)\n"
        "    return 0\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="BackingOwnerDropWhileBorrowed"):
        compile_structured_hir(source)


def test_temporary_owner_argument_is_rejected_at_call_site() -> None:
    source = (
        "fn borrow_text(text: Text) -> TextView:\n"
        "    return text.as_view()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let view: TextView = borrow_text("
        "Text.from_bytes(input, 0, input.len()))\n"
        "    return view.len()\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="BorrowFromTemporaryEscapes"):
        compile_structured_hir(source)


def test_opaque_external_borrow_result_fails_closed() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let view: TextView = foreign_view(input)\n"
        "    return view.len()\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="OpaqueBorrowSummary"):
        compile_structured_hir(source)


def test_branch_summary_unions_two_parameter_origins() -> None:
    source = (
        "fn choose(left: Text, right: Text, flag: Bool) -> TextView:\n"
        "    if flag:\n"
        "        return left.as_view()\n"
        "    return right.as_view()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let left: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let right: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let view: TextView = choose(left, right, input.len() > 0)\n"
        "    drop(left)\n"
        "    return view.len()\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="backing_owner=left"):
        compile_structured_hir(source)


def test_contained_borrow_lifetime_positive_and_owned_return_unchanged() -> None:
    source = (
        "fn make_views(text: Text) -> Vec[TextView]:\n"
        "    let values: Vec[TextView] = Vec.new()\n"
        "    values.push(text.as_view())\n"
        "    return values\n"
        "fn own(text: Text) -> Text:\n"
        "    return text\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let values: Vec[TextView] = make_views(text)\n"
        "    let n: UInt64 = values.get(0).len()\n"
        "    drop(values)\n"
        "    let moved: Text = own(text)\n"
        "    return n + moved.len()\n"
    )
    hir = compile_structured_hir(source)
    make_summary = hir.function("make_views").borrow_summary
    assert make_summary.status == "known"
    assert make_summary.entries[0].relation.kind == "contained"
    assert make_summary.entries[0].relation.source_parameter_index == 0
    assert make_summary.entries[0].relation.result_path == BorrowPlacePath((BorrowPlaceStep.element(),))
    assert not hir.function("own").borrow_summary.entries


def test_existing_contained_container_provenance_transfers_through_identity() -> None:
    source = (
        "fn identity(values: Vec[TextView]) -> Vec[TextView]:\n"
        "    return values\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let values: Vec[TextView] = Vec.new()\n"
        "    values.push(text.as_view())\n"
        "    let moved: Vec[TextView] = identity(values)\n"
        "    drop(moved)\n"
        "    drop(text)\n"
        "    return 0\n"
    )
    hir = compile_structured_hir(source)
    summary = hir.function("identity").borrow_summary
    assert summary.status == "known"
    assert summary.entries[0].relation.source_parameter_index == 0
    assert summary.entries[0].relation.kind == "contained"


def test_summary_hir_roundtrip_rir_and_cross_process() -> None:
    source = (
        "fn borrow_text(text: Text) -> TextView:\n"
        "    return text.as_view()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let view: TextView = borrow_text(text)\n"
        "    let n: UInt64 = view.len()\n"
        "    drop(view)\n"
        "    drop(text)\n"
        "    return n\n"
    )
    hir = compile_structured_hir(source)
    restored = StructuredHIRProgram.from_json(hir.to_json())
    assert restored.digest == hir.digest
    tampered = json.loads(hir.to_json())
    tampered["functions"][0]["borrow_summary"]["entries"][0]["relation"]["borrow_type"] = "BytesView"
    assert StructuredHIRProgram.from_dict(tampered).digest != hir.digest
    rir = lower_structured_hir_to_rir(restored)
    assert rir.function("borrow_text").borrow_summary == restored.function("borrow_text").borrow_summary
    payload = hir.to_json()
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from merlo.structured_hir_v2 import StructuredHIRProgram; "
            "print(StructuredHIRProgram.from_json(sys.stdin.read()).digest)",
        ],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
    )
    assert probe.stdout.strip() == hir.digest


def test_semantic_world_exposes_function_borrow_summary(tmp_path: Path) -> None:
    source = tmp_path / "main.mlo"
    source.write_text(
        "module app.main\n\n"
        "export fn borrow_text(text: Text) -> TextView:\n"
        "    return text.as_view()\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"summary\")\n"
        "    return Ok(\"ok\")\n",
        encoding="utf-8",
    )
    world = SemanticWorld.build(source, require_interface_lock=False)
    symbol = world.inspect("app.main.borrow_text")["symbol"]
    summary = symbol["borrow_summary"]
    assert summary["schema_version"] == 3
    assert summary["entries"][0]["relation"]["source_parameter_index"] == 0
    assert world.data["borrow_summaries"]


def test_direct_recursion_has_one_semantic_relation_and_bounded_witness() -> None:
    source = (
        "fn recursive_view(text: Text, depth: UInt64) -> TextView:\n"
        "    if depth == 0:\n"
        "        return text.as_view()\n"
        "    return recursive_view(text, depth - 1)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let view: TextView = recursive_view(text, 1)\n"
        "    return view.len()\n"
    )
    hir = compile_structured_hir(source)
    summary = hir.function("recursive_view").borrow_summary
    assert len(summary.entries) == 1
    assert summary.entries[0].relation.semantic_key() == (
        0,
        BorrowPlacePath.parameter(0),
        "TextView",
        BorrowPlacePath(),
        "direct",
        "borrow",
    )
    assert len(summary.entries[0].witness_path) <= 1
    assert summary.entries[0].witness_path.count("recursive_view") <= 1


def test_mutual_recursion_converges_deterministically() -> None:
    source = (
        "fn left(text: Text, depth: UInt64) -> TextView:\n"
        "    if depth == 0:\n"
        "        return text.as_view()\n"
        "    return right(text, depth - 1)\n"
        "fn right(text: Text, depth: UInt64) -> TextView:\n"
        "    if depth == 0:\n"
        "        return text.as_view()\n"
        "    return left(text, depth - 1)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let view: TextView = left(text, 1)\n"
        "    return view.len()\n"
    )
    first = compile_structured_hir(source)
    second = compile_structured_hir(source)
    assert first.function("left").borrow_summary == second.function("left").borrow_summary
    assert first.function("right").borrow_summary == second.function("right").borrow_summary
    assert first.function("left").borrow_summary.entries[0].witness_path == ()


def test_recursive_contained_borrow_summary_is_finite() -> None:
    source = (
        "fn recursive_views(text: Text, depth: UInt64) -> Vec[TextView]:\n"
        "    let values: Vec[TextView] = Vec.new()\n"
        "    values.push(text.as_view())\n"
        "    if depth == 0:\n"
        "        return values\n"
        "    return recursive_views(text, depth - 1)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let values: Vec[TextView] = recursive_views(text, 1)\n"
        "    return values.len()\n"
    )
    summary = compile_structured_hir(source).function("recursive_views").borrow_summary
    assert len(summary.entries) == 1
    assert summary.entries[0].relation.kind == "contained"
    assert summary.entries[0].relation.result_path == BorrowPlacePath((BorrowPlaceStep.element(),))


def test_recursive_projection_uses_cycle_marker_instead_of_growing_path() -> None:
    source = (
        "fn recursive_view(text: Text, depth: UInt64) -> TextView:\n"
        "    if depth == 0:\n"
        "        return text.as_view()\n"
        "    return recursive_view(text, depth - 1).slice(0, 1)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let view: TextView = recursive_view(text, 1)\n"
        "    return view.len()\n"
    )
    summary = compile_structured_hir(source).function("recursive_view").borrow_summary
    assert len(summary.entries) == 2
    recursive_entries = [
        item
        for item in summary.entries
        if item.relation.result_path.steps
        and item.relation.result_path.steps[-1].kind == "RecursiveTail"
    ]
    assert len(recursive_entries) == 1
    assert recursive_entries[0].relation.result_path.steps[-1].value == "recursive_view"
    assert recursive_entries[0].witness_path.count(BORROW_SUMMARY_CYCLE_MARKER) == 1


def test_no_origin_recursion_is_opaque_and_fails_closed() -> None:
    source = (
        "fn no_origin(depth: UInt64) -> TextView:\n"
        "    return no_origin(depth - 1)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let view: TextView = no_origin(1)\n"
        "    return view.len()\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="OpaqueBorrowSummary"):
        compile_structured_hir(source)


def test_unrelated_scalar_does_not_change_recursive_function_revision() -> None:
    base = (
        "fn recursive_view(text: Text, depth: UInt64) -> TextView:\n"
        "    if depth == 0:\n"
        "        return text.as_view()\n"
        "    return recursive_view(text, depth - 1)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let view: TextView = recursive_view(text, 1)\n"
        "    return view.len()\n"
    )
    extra = base.replace(
        "fn main(input: BytesView) -> UInt64:\n",
        "fn scalar(value: UInt64) -> UInt64:\n"
        "    return value + 1\n"
        "fn main(input: BytesView) -> UInt64:\n",
    )
    first = compile_structured_hir(base)
    second = compile_structured_hir(extra)
    assert first.function("recursive_view").borrow_summary.semantic_dict() == (
        second.function("recursive_view").borrow_summary.semantic_dict()
    )
    assert first.function("recursive_view").revision_id == second.function("recursive_view").revision_id


def test_helper_temporary_owner_is_rejected_before_backend() -> None:
    source = (
        "fn borrow_text(text: Text) -> TextView:\n"
        "    return text.as_view()\n"
        "fn helper(input: BytesView) -> TextView:\n"
        "    return borrow_text(Text.from_bytes(input, 0, input.len()))\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let view: TextView = helper(input)\n"
        "    return view.len()\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="BorrowFromTemporaryEscapes") as raised:
        compile_structured_hir(source)
    assert "callee=borrow_text" in str(raised.value)
    assert "escape_path=borrow_text -> text" in str(raised.value)


def test_semantic_equality_ignores_witness_path() -> None:
    source = (
        "fn borrow_text(text: Text) -> TextView:\n"
        "    return text.as_view()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let view: TextView = borrow_text(text)\n"
        "    return view.len()\n"
    )
    entry = compile_structured_hir(source).function("borrow_text").borrow_summary.entries[0]
    altered = BorrowSummaryEntry(
        entry.relation,
        (BORROW_SUMMARY_CYCLE_MARKER,),
    )
    assert entry == altered
    assert entry.relation.semantic_key() == altered.relation.semantic_key()
    serialized = entry.to_dict()
    assert "witness_path" in serialized
    assert "relation" in serialized
