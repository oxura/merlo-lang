from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from merlo.native_c_backend import compile_c_source
from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import (
    lower_rir_to_performance_mir,
    optimize_general_mir,
)
from merlo.semantic_world import SemanticWorld
from merlo.structured_hir_v2 import (
    StructuredHIRCompileError,
    compile_canonical_hir,
    compile_structured_hir,
)
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface


def _native(source: str, tmp_path: Path, stem: str) -> Path:
    hir = compile_structured_hir(source)
    representation = lower_structured_hir_to_rir(hir)
    mir = optimize_general_mir(
        lower_rir_to_performance_mir(hir, representation)
    )
    generated = emit_general_c(hir, representation, mir)
    build = compile_c_source(
        generated.source,
        output_dir=tmp_path / stem,
        stem=stem,
    )
    assert build.status == "MEASURED", build.stderr
    assert build.binary_path is not None
    return Path(build.binary_path)


def test_scoped_vec_of_text_view_runs_before_backing_owner_drop(
    tmp_path: Path,
) -> None:
    binary = _native(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let values: Vec[TextView] = Vec.new()\n"
        "    let view: TextView = text.as_view()\n"
        "    values.push(view)\n"
        "    return values.get(0).len()\n",
        tmp_path,
        "scoped-vec-text-view",
    )

    completed = subprocess.run(
        [binary],
        input=b"hello",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=5" in completed.stdout


def test_scoped_box_of_bytes_view_runs_before_backing_owner_drop(
    tmp_path: Path,
) -> None:
    binary = _native(
        "fn main(input: BytesView) -> UInt64:\n"
        '    let data: Bytes = b"hello"\n'
        "    let view: BytesView = data.view()\n"
        "    let boxed: Box[BytesView] = Box.new(view)\n"
        "    return boxed.get().len()\n",
        tmp_path,
        "scoped-box-bytes-view",
    )

    completed = subprocess.run(
        [binary],
        input=b"",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=5" in completed.stdout


@pytest.mark.parametrize(
    "source,container,borrow,owner",
    [
        (
            "fn bad(input: BytesView) -> Vec[TextView]:\n"
            "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
            "    let values: Vec[TextView] = Vec.new()\n"
            "    let view: TextView = text.as_view()\n"
            "    values.push(view)\n"
            "    return values\n",
            "Vec[TextView]",
            "TextView",
            "text",
        ),
        (
            "fn bad(input: BytesView) -> Box[BytesView]:\n"
            '    let data: Bytes = b"hello"\n'
            "    let view: BytesView = data.view()\n"
            "    let boxed: Box[BytesView] = Box.new(view)\n"
            "    return boxed\n",
            "Box[BytesView]",
            "BytesView",
            "data",
        ),
        (
            "fn bad(input: BytesView) -> Option[Vec[TextView]]:\n"
            "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
            "    let values: Vec[TextView] = Vec.new()\n"
            "    let view: TextView = text.as_view()\n"
            "    values.push(view)\n"
            "    let nested: Option[Vec[TextView]] = Some(values)\n"
            "    return nested\n",
            "Option[Vec[TextView]]",
            "TextView",
            "text",
        ),
    ],
)
def test_container_of_borrow_return_escape_is_rejected_with_provenance(
    source: str,
    container: str,
    borrow: str,
    owner: str,
) -> None:
    source += "fn main(input: BytesView) -> UInt64:\n    0\n"

    with pytest.raises(StructuredHIRCompileError) as raised:
        compile_structured_hir(source)

    diagnostic = str(raised.value)
    assert "EscapedContainedBorrow" in diagnostic
    assert f"container={container}" in diagnostic
    assert f"contained_borrow={borrow}" in diagnostic
    assert f"backing_owner={owner}" in diagnostic
    assert "escape_path=" in diagnostic


@pytest.mark.parametrize(
    "operation,diagnostic",
    [
        (
            "let moved: Text = forward(text)",
            "BackingOwnerMoveWhileBorrowed",
        ),
        ("drop(text)", "BackingOwnerDropWhileBorrowed"),
    ],
)
def test_backing_owner_cannot_move_or_drop_while_nested_borrow_is_live(
    operation: str,
    diagnostic: str,
) -> None:
    source = (
        "fn forward(value: Text) -> Text:\n"
        "    return value\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let values: Vec[TextView] = Vec.new()\n"
        "    let view: TextView = text.as_view()\n"
        "    values.push(view)\n"
        f"    {operation}\n"
        "    return values.get(0).len()\n"
    )

    with pytest.raises(StructuredHIRCompileError) as raised:
        compile_structured_hir(source)

    text = str(raised.value)
    assert diagnostic in text
    assert "container=Vec[TextView]" in text
    assert "contained_borrow=TextView" in text
    assert "backing_owner=text" in text


def test_nested_borrow_container_can_drop_before_backing_owner() -> None:
    compile_structured_hir(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let values: Vec[TextView] = Vec.new()\n"
        "    let view: TextView = text.as_view()\n"
        "    values.push(view)\n"
        "    drop(view)\n"
        "    drop(values)\n"
        "    drop(text)\n"
        "    return 0\n"
    )


def test_moving_container_transfers_nested_borrow_provenance() -> None:
    compile_structured_hir(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let values: Vec[TextView] = Vec.new()\n"
        "    let view: TextView = text.as_view()\n"
        "    values.push(view)\n"
        "    let moved: Vec[TextView] = values\n"
        "    drop(view)\n"
        "    drop(moved)\n"
        "    drop(text)\n"
        "    return 0\n"
    )


def test_container_cannot_be_stored_in_owner_that_outlives_backing() -> None:
    source = (
        "record Holder:\n"
        "    view: TextView\n"
        "fn bad(input: BytesView) -> Holder:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let holder: Holder = Holder(text.as_view())\n"
        "    return holder\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    0\n"
    )

    with pytest.raises(
        StructuredHIRCompileError,
        match="EscapedContainedBorrow",
    ) as raised:
        compile_structured_hir(source)

    diagnostic = str(raised.value)
    assert "container=Holder" in diagnostic
    assert "contained_borrow=TextView" in diagnostic
    assert "backing_owner=text" in diagnostic
    assert diagnostic.endswith("return(Holder)")


def test_container_of_borrow_closure_capture_is_rejected() -> None:
    source = (
        "fn bad(input: BytesView) -> Fn[UInt64,UInt64]:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let values: Vec[TextView] = Vec.new()\n"
        "    let view: TextView = text.as_view()\n"
        "    values.push(view)\n"
        "    value => value + values.len()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    0\n"
    )

    with pytest.raises(
        StructuredHIRCompileError,
        match="BorrowedClosureCaptureEscapes",
    ) as raised:
        canonical = elaborate_surface(
            parse_surface(source, path="contained-closure.mlo")
        ).canonical
        compile_canonical_hir(canonical)

    diagnostic = str(raised.value)
    assert "container=Vec[TextView]" in diagnostic
    assert "contained_borrow=TextView" in diagnostic
    assert "backing_owner=text" in diagnostic
    assert diagnostic.endswith("closure_capture(values)")


def test_future_with_contained_borrow_cannot_enter_escaping_closure() -> None:
    source = (
        "fn bad(future: Future[TextView]) -> Fn[UInt64,UInt64]:\n"
        "    value => value + future.len()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    0\n"
    )

    with pytest.raises(
        StructuredHIRCompileError,
        match="BorrowedClosureCaptureEscapes",
    ):
        canonical = elaborate_surface(
            parse_surface(source, path="future-capture.mlo")
        ).canonical
        compile_canonical_hir(canonical)


def test_hir_and_rir_retain_contained_borrow_metadata() -> None:
    hir = compile_structured_hir(
        "fn inspect(values: Vec[TextView]) -> UInt64:\n"
        "    return values.len()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return 0\n"
    )
    representation = lower_structured_hir_to_rir(hir)

    assert hir.function("inspect").parameters[0].ownership == "contained_borrow"
    descriptor = representation.descriptor("Vec[TextView]")
    assert descriptor.contains_borrow
    assert descriptor.contained_borrow_types == ("TextView",)
    parameter = representation.functions[0].parameters[0]
    assert parameter == ("values", "Vec[TextView]", "contained_borrow")


def test_semantic_world_exposes_recursive_type_properties(
    tmp_path: Path,
) -> None:
    source = tmp_path / "main.mlo"
    source.write_text(
        "module app.main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export fn inspect(values: Vec[TextView]) -> UInt64:\n"
        "    return values.len()\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"contained borrow metadata\")\n"
        "    return Ok(\"ok\")\n",
        encoding="utf-8",
    )

    world = SemanticWorld.build(source, require_interface_lock=False)
    properties = world.data["type_properties"]["Vec[TextView]"]
    symbol = world.inspect("app.main.inspect")["symbol"]

    assert properties["contains_borrow"] is True
    assert properties["borrow_types"] == ["TextView"]
    assert symbol["type_properties"]["Vec[TextView]"]["contains_borrow"] is True
