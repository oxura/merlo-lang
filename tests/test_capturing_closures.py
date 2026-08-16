from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from merlo.elaboration.diagnostics import SurfaceElaborationError
from merlo.native_c_backend import compile_c_source
from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import CallbackDesc, lower_structured_hir_to_rir
from merlo.representation_mir import (
    lower_rir_to_performance_mir,
    optimize_general_mir,
)
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.surface_ast import SurfaceExpressionStatement, SurfaceLambda
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface


def elaborate(source: str):
    return elaborate_surface(parse_surface(source, path="closures.mlo"))


def test_parser_retains_arrow_closure_parameters_body_and_span() -> None:
    program = parse_surface(
        "fn make(limit: UInt64) -> Fn[UInt64,Bool]:\n"
        "    value => value > limit\n",
        path="closures.mlo",
    )
    statement = program.declarations[0].body[0]

    assert isinstance(statement, SurfaceExpressionStatement)
    assert isinstance(statement.expression, SurfaceLambda)
    assert statement.expression.parameters == ("value",)
    assert statement.expression.span.path == "closures.mlo"
    assert statement.expression.span.start_column == 5


def test_closure_conversion_records_owned_environment_before_hir() -> None:
    source = (
        "fn make(text: Text) -> Fn[UInt64,Bool]:\n"
        "    limit => text.len() > limit\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    0\n"
    )

    canonical = elaborate(source).canonical
    closure = canonical.function("make").closures[0]
    hir = compile_canonical_hir(canonical)
    representation = lower_structured_hir_to_rir(hir)
    descriptor = representation.descriptor("Fn[UInt64,Bool]")

    assert closure.parameters == (("limit", "UInt64"),)
    assert [
        (item.name, item.type_name, item.ownership) for item in closure.captures
    ] == [("text", "Text", "owned")]
    assert any(
        node.kind == "ClosureCreate" and node.ownership == "owned"
        for function in hir.functions
        for node in function.walk()
    )
    assert isinstance(descriptor, CallbackDesc)
    assert descriptor.kind == "closure"
    assert descriptor.drop_class == "closure_environment"


def test_capturing_closure_runs_and_releases_owned_environment(
    tmp_path: Path,
) -> None:
    source = (
        "fn length_above(text: Text) -> Fn[UInt64,Bool]:\n"
        "    limit => text.len() > limit\n"
        "fn apply(callback: Fn[UInt64,Bool], limit: UInt64) -> Bool:\n"
        "    callback(limit)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let owned: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let predicate: Fn[UInt64,Bool] = length_above(owned)\n"
        "    if apply(predicate, 0):\n"
        "        return 1\n"
        "    0\n"
    )

    hir = compile_canonical_hir(elaborate(source).canonical)
    representation = lower_structured_hir_to_rir(hir)
    mir = optimize_general_mir(
        lower_rir_to_performance_mir(hir, representation)
    )
    generated = emit_general_c(hir, representation, mir)
    build = compile_c_source(
        generated.source,
        output_dir=tmp_path,
        stem="capturing-closure",
    )

    assert build.status == "MEASURED", build.stderr
    assert build.binary_path is not None
    completed = subprocess.run(
        [build.binary_path],
        input=b"captured",
        capture_output=True,
        check=False,
    )
    output = completed.stdout.decode()
    assert completed.returncode == 0
    assert "OK result=1" in output
    assert "allocations=3 frees=3" in output
    assert "drops=3" in output


@pytest.mark.parametrize(
    "source,diagnostic",
    [
        (
            "fn bad(view: TextView) -> Fn[UInt64,UInt64]:\n"
            "    value => value + view.len()\n",
            "BorrowedClosureCaptureEscapes: view",
        ),
        (
            "fn bad() -> Fn[UInt64,UInt64]:\n"
            "    var offset: UInt64 = 1\n"
            "    value => value + offset\n",
            "MutableClosureCaptureForbidden: offset",
        ),
        (
            "fn bad(file: FileReader) -> Fn[UInt64,FileReader]:\n"
            "    value => file\n",
            "ResourceClosureCaptureForbidden: file",
        ),
    ],
)
def test_invalid_closure_environment_escapes_fail_closed(
    source: str,
    diagnostic: str,
) -> None:
    with pytest.raises(SurfaceElaborationError, match=diagnostic):
        elaborate(source + "fn main(input: BytesView) -> UInt64:\n    0\n")


def test_unannotated_closure_has_no_implicit_runtime_shape() -> None:
    source = (
        "fn bad(limit: UInt64):\n"
        "    value => value + limit\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    0\n"
    )

    with pytest.raises(
        SurfaceElaborationError,
        match="ClosureTypeAnnotationRequired",
    ):
        elaborate(source)
