from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from merlo.elaboration.diagnostics import SurfaceElaborationError
from merlo.ffi import FFICompileError, parse_ffi_declarations
from merlo.native_c_backend import compile_c_source
from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import (
    lower_rir_to_performance_mir,
    optimize_general_mir,
)
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface
from merlo.structured_hir_v2 import (
    compile_canonical_hir,
    compile_structured_hir,
)


def _native(source: str, tmp_path: Path, stem: str) -> Path:
    hir = compile_structured_hir(source, path=f"{stem}.mlo")
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


def _surface_native(source: str, tmp_path: Path, stem: str) -> Path:
    hir = compile_canonical_hir(
        elaborate_surface(parse_surface(source, path=f"{stem}.mlo")).canonical
    )
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


def _run(binary: Path, payload: bytes = b"") -> str:
    completed = subprocess.run(
        [binary],
        input=payload,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    return completed.stdout.decode()


def test_contained_borrow_argument_uses_pointer_abi_without_copy(
    tmp_path: Path,
) -> None:
    source = (
        "fn inspect(values: Vec[TextView]) -> UInt64:\n"
        "    return values.len()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let views: Vec[TextView] = Vec.new()\n"
        "    views.push(text.as_view())\n"
        "    return inspect(views)\n"
    )

    output = _run(_native(source, tmp_path, "contained-borrow"), b"owner")

    assert "OK result=1" in output


def test_direct_borrow_and_three_reborrow_returns_preserve_owner_lifetime(
    tmp_path: Path,
) -> None:
    source = (
        "fn direct(data: BytesView) -> BytesView:\n"
        "    return data\n"
        "fn reborrow1(data: BytesView) -> BytesView:\n"
        "    return direct(data)\n"
        "fn reborrow2(data: BytesView) -> BytesView:\n"
        "    return reborrow1(data)\n"
        "fn reborrow3(data: BytesView) -> BytesView:\n"
        "    return reborrow2(data)\n"
        "fn count(data: BytesView) -> UInt64:\n"
        "    return data.len()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let view: BytesView = reborrow3(input)\n"
        "    return count(view)\n"
    )

    output = _run(_native(source, tmp_path, "reborrow-depth-three"), b"abcdef")

    assert "OK result=6" in output


def test_owned_argument_and_return_transfer_once(
    tmp_path: Path,
) -> None:
    source = (
        "fn own(data: BytesView) -> Text:\n"
        "    return Text.from_bytes(data, 0, data.len())\n"
        "fn forward(text: Text) -> Text:\n"
        "    return text\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = own(input)\n"
        "    let moved: Text = forward(text)\n"
        "    return moved.len()\n"
    )

    output = _run(_native(source, tmp_path, "owned-transfer"), b"owned")

    assert "OK result=5" in output


def test_nested_closure_owner_capture_preserves_callback_abi(
    tmp_path: Path,
) -> None:
    source = (
        "fn add(base: UInt64) -> Fn[UInt64,UInt64]:\n"
        "    value => value + base\n"
        "fn wrap(inner: Fn[UInt64,UInt64]) -> Fn[UInt64,UInt64]:\n"
        "    value => inner(value) + 1\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let first: Fn[UInt64,UInt64] = add(input.len())\n"
        "    let second: Fn[UInt64,UInt64] = wrap(first)\n"
        "    return second(2)\n"
    )

    output = _run(_surface_native(source, tmp_path, "nested-closure"), b"abc")

    assert "OK result=6" in output


def test_scalar_capture_and_compound_literal_callback_execute(
    tmp_path: Path,
) -> None:
    source = (
        "fn increment(value: UInt64) -> UInt64:\n"
        "    return value + 1\n"
        "fn apply(callback: Fn[UInt64,UInt64], value: UInt64) -> UInt64:\n"
        "    return callback(value)\n"
        "fn shift(offset: UInt64) -> Fn[UInt64,UInt64]:\n"
        "    value => value + offset\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let shifted: Fn[UInt64,UInt64] = shift(input.len())\n"
        "    return apply(increment, shifted(2))\n"
    )

    output = _run(_surface_native(source, tmp_path, "scalar-capture"), b"abc")

    assert "OK result=6" in output


def test_owned_capture_releases_callback_environment(
    tmp_path: Path,
) -> None:
    source = (
        "fn length_above(text: Text) -> Fn[UInt64,Bool]:\n"
        "    limit => text.len() > limit\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let callback: Fn[UInt64,Bool] = length_above(text)\n"
        "    if callback(2):\n"
        "        return 1\n"
        "    return 0\n"
    )

    output = _run(_surface_native(source, tmp_path, "owned-capture"), b"owner")

    assert "OK result=1" in output
    assert "allocations=3 frees=3" in output


def test_callback_cannot_outlive_borrowed_backing_owner() -> None:
    source = (
        "fn bad(data: BytesView) -> Fn[UInt64,UInt64]:\n"
        "    let text: Text = Text.from_bytes(data, 0, data.len())\n"
        "    let view: TextView = text.as_view()\n"
        "    value => value + view.len()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return 0\n"
    )

    with pytest.raises(
        SurfaceElaborationError,
        match="BorrowedClosureCaptureEscapes: view",
    ):
        elaborate_surface(parse_surface(source, path="borrow-capture.mlo"))


def test_ffi_callback_is_rejected_without_a_fixed_width_abi() -> None:
    with pytest.raises(FFICompileError, match="FixedWidthABIRequired"):
        parse_ffi_declarations(
            'extern "C" fn register(callback: Fn[UInt64,UInt64]) -> UInt64\n'
        )
