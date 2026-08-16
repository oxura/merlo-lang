from __future__ import annotations

import pytest

from merlo.elaboration.diagnostics import SurfaceElaborationError
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.surface_ast import SurfaceFunction
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import SurfaceSyntaxError, parse_surface


def elaborate(source: str):
    return elaborate_surface(parse_surface(source, path="generics.mlo"))


def test_parser_retains_generic_parameters_constraints_and_spans() -> None:
    program = parse_surface(
        "fn choose[T: Comparable + Display, U](left: T, right: U) -> T:\n"
        "    left\n",
        path="generics.mlo",
    )

    function = program.declarations[0]
    assert isinstance(function, SurfaceFunction)
    assert [(item.name, item.constraints) for item in function.type_parameters] == [
        ("T", ("Comparable", "Display")),
        ("U", ()),
    ]
    assert function.type_parameters[0].span.path == "generics.mlo"
    assert function.type_parameters[0].span.start_column < function.type_parameters[0].span.end_column


@pytest.mark.parametrize(
    "source,diagnostic",
    [
        ("fn bad[t](value: t) -> t:\n    value\n", "InvalidTypeParameter"),
        ("fn bad[T:](value: T) -> T:\n    value\n", "InvalidTypeConstraint"),
        ("fn bad[T, T](value: T) -> T:\n    value\n", "DuplicateTypeParameter"),
    ],
)
def test_parser_rejects_malformed_generic_parameter_lists(
    source: str, diagnostic: str
) -> None:
    with pytest.raises(SurfaceSyntaxError, match=diagnostic):
        parse_surface(source)


def test_elaboration_emits_one_deterministic_specialization_per_concrete_type() -> None:
    source = (
        "fn identity[T](value: T) -> T:\n"
        "    value\n"
        "fn number() -> UInt64:\n"
        "    identity(41)\n"
        "fn another_number() -> UInt64:\n"
        "    identity(7)\n"
        "fn text() -> Text:\n"
        "    identity(\"ok\")\n"
        "fn main() -> UInt64:\n"
        "    number()\n"
    )

    first = elaborate(source).canonical
    second = elaborate(source).canonical
    specializations = [
        item for item in first.functions if item.name.startswith("identity__mono_")
    ]

    assert len(specializations) == 2
    assert {item.return_type for item in specializations} == {"UInt64", "Text"}
    assert {item.parameters[0][1] for item in specializations} == {"UInt64", "Text"}
    assert not any(item.name == "identity" for item in first.functions)
    assert first.semantic_hash == second.semantic_hash
    assert first.to_source() == second.to_source()


def test_nested_generic_types_are_unified_structurally_before_hir() -> None:
    source = (
        "fn size[T](values: Vec[T]) -> UInt64:\n"
        "    values.len()\n"
        "fn main(values: Vec[Text]) -> UInt64:\n"
        "    size(values)\n"
    )

    canonical = elaborate(source).canonical
    specialization = next(
        item for item in canonical.functions if item.name.startswith("size__mono_")
    )
    hir = compile_canonical_hir(canonical)

    assert specialization.parameters == (("values", "Vec[Text]"),)
    assert all(function.name != "size" for function in hir.functions)
    assert any(function.name == specialization.name for function in hir.functions)
    assert {
        parameter.type_name
        for function in hir.functions
        for parameter in function.parameters
    } == {"Vec[Text]"}


def test_generic_type_conflicts_and_ambiguous_boundaries_fail_closed() -> None:
    conflict = (
        "fn same[T](left: T, right: T) -> T:\n"
        "    left\n"
        "fn main() -> UInt64:\n"
        "    same(1, \"wrong\")\n"
    )
    ambiguous = (
        "fn make[T]() -> T:\n"
        "    1\n"
        "fn main():\n"
        "    make()\n"
    )

    with pytest.raises(SurfaceElaborationError, match="GenericTypeConflict"):
        elaborate(conflict)
    with pytest.raises(
        SurfaceElaborationError, match="GenericBoundaryAnnotationRequired"
    ):
        elaborate(ambiguous)


@pytest.mark.parametrize(
    "declaration,boundary",
    [
        ("fn identity[T](value) -> T:\n    value\n", "identity.value"),
        ("fn identity[T](value: T):\n    value\n", "identity return"),
    ],
)
def test_generic_declarations_require_explicit_boundary_types(
    declaration: str, boundary: str
) -> None:
    with pytest.raises(
        SurfaceElaborationError,
        match=f"GenericBoundaryAnnotationRequired: {boundary}",
    ):
        elaborate(declaration + "fn main() -> UInt64:\n    1\n")


def test_generic_templates_cannot_escape_as_runtime_function_values() -> None:
    source = (
        "fn identity[T](value: T) -> T:\n"
        "    value\n"
        "fn main():\n"
        "    let callback = identity\n"
        "    callback(1)\n"
    )

    with pytest.raises(
        SurfaceElaborationError, match="GenericFunctionValueRequiresInstantiation"
    ):
        elaborate(source)
