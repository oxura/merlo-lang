from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from merlo.elaboration.diagnostics import SurfaceElaborationError
from merlo.native_c_backend import compile_c_source
from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import (
    lower_rir_to_performance_mir,
    optimize_general_mir,
)
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.surface_ast import SurfaceImplementation, SurfaceInterface
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface


INTERFACE = (
    "interface Sized:\n"
    "    fn size(value: Self) -> UInt64\n"
    "impl Sized for Text:\n"
    "    fn size(value: Text) -> UInt64:\n"
    "        value.len()\n"
)


def elaborate(source: str):
    return elaborate_surface(parse_surface(source, path="interfaces.mlo"))


def test_parser_retains_interface_and_implementation_contracts() -> None:
    program = parse_surface(INTERFACE, path="interfaces.mlo")
    interface, implementation = program.declarations

    assert isinstance(interface, SurfaceInterface)
    assert interface.name == "Sized"
    assert [
        (item.name, item.type_name)
        for item in interface.methods[0].parameters
    ] == [("value", "Self")]
    assert interface.methods[0].return_type == "UInt64"
    assert isinstance(implementation, SurfaceImplementation)
    assert implementation.interface_name == "Sized"
    assert implementation.type_name == "Text"
    assert implementation.methods[0].name == "size"


def test_interface_method_without_fn_consumes_retained_signature() -> None:
    interface = parse_surface(
        "interface Sized:\n"
        "    size(value: Self) -> UInt64\n",
        path="interfaces.mlo",
    ).declarations[0]

    assert interface.methods[0].parameters[0].type_name == "Self"
    assert interface.methods[0].return_type == "UInt64"


def test_interface_call_becomes_one_direct_concrete_call_before_hir() -> None:
    source = INTERFACE + (
        "fn main(value: Text) -> UInt64:\n"
        "    Sized.size(value)\n"
    )

    canonical = elaborate(source).canonical
    assert len(canonical.functions) == 2
    implementation = next(
        item for item in canonical.functions if item.name.startswith("__merlo_impl_size_")
    )
    assert "Sized.size" not in canonical.to_source()
    assert implementation.name in canonical.function("main").body[0].expression

    hir = compile_canonical_hir(canonical)
    call = next(
        node
        for node in hir.function("main").walk()
        if node.kind == "DirectCall"
    )
    assert call.attribute_map["callee"] == implementation.name
    assert not any(
        "dispatch" in node.kind.casefold()
        for function in hir.functions
        for node in function.walk()
    )


def test_user_interface_is_a_static_generic_constraint() -> None:
    source = INTERFACE + (
        "fn measured[T: Sized](value: T) -> UInt64:\n"
        "    Sized.size(value)\n"
        "fn main(value: Text) -> UInt64:\n"
        "    measured(value)\n"
    )

    canonical = elaborate(source).canonical
    specialization = next(
        item for item in canonical.functions if item.name.startswith("measured__mono_")
    )

    assert specialization.parameters == (("value", "Text"),)
    assert "Sized" not in specialization.body[0].expression
    assert "__merlo_impl_size_" in specialization.body[0].expression


def test_static_interface_dispatch_runs_without_runtime_lookup(
    tmp_path: Path,
) -> None:
    source = INTERFACE + (
        "fn measured[T: Sized](value: T) -> UInt64:\n"
        "    Sized.size(value)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    measured(text)\n"
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
        stem="static-interface",
    )

    assert "vtable" not in generated.source.casefold()
    assert "interface_registry" not in generated.source
    assert build.status == "MEASURED", build.stderr
    assert build.binary_path is not None
    completed = subprocess.run(
        [build.binary_path],
        input=b"dispatch",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert b"OK result=8" in completed.stdout


@pytest.mark.parametrize(
    "source,diagnostic",
    [
        (
            "interface Sized:\n"
            "    fn size(value: Self) -> UInt64\n"
            "fn main(value: Text) -> UInt64:\n"
            "    Sized.size(value)\n",
            "MissingImplementation: Sized for Text",
        ),
        (
            INTERFACE
            + "impl Sized for Text:\n"
            "    fn size(value: Text) -> UInt64:\n"
            "        value.len()\n",
            "DuplicateImplementation: Sized for Text",
        ),
        (
            "interface Sized:\n"
            "    fn size(value: Self) -> UInt64\n"
            "impl Sized for Text:\n"
            "    fn size(value: Text) -> Bool:\n"
            "        true\n",
            "ImplementationSignatureMismatch",
        ),
        (
            "interface Invalid:\n"
            "    fn inspect(value: UInt64) -> UInt64\n"
            "impl Invalid for UInt64:\n"
            "    fn inspect(value: UInt64) -> UInt64:\n"
            "        value\n",
            "InterfaceReceiverRequired",
        ),
    ],
)
def test_interface_coherence_and_signatures_fail_closed(
    source: str,
    diagnostic: str,
) -> None:
    with pytest.raises(SurfaceElaborationError, match=diagnostic):
        elaborate(source)
