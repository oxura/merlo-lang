from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

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
    assert make_summary.entries[0].kind == "contained"
    assert make_summary.entries[0].source_parameter_index == 0
    assert make_summary.entries[0].result_path == ("elements",)
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
    assert summary.entries[0].source_parameter_index == 0
    assert summary.entries[0].kind == "contained"


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
    tampered["functions"][0]["borrow_summary"]["entries"][0]["borrow_type"] = "BytesView"
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
    assert summary["schema_version"] == 1
    assert summary["entries"][0]["source_parameter_index"] == 0
    assert world.data["borrow_summaries"]
