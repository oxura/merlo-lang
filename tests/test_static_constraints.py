from __future__ import annotations

import pytest

from merlo.elaboration.diagnostics import SurfaceElaborationError
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface


def elaborate(source: str):
    return elaborate_surface(parse_surface(source, path="constraints.mlo"))


def test_comparable_constraint_specializes_to_concrete_static_comparison() -> None:
    source = (
        "fn smaller[T: Comparable](left: T, right: T) -> Bool:\n"
        "    left < right\n"
        "fn main() -> Bool:\n"
        "    smaller(2, 3)\n"
    )

    canonical = elaborate(source).canonical
    specialization = next(
        item for item in canonical.functions if item.name.startswith("smaller__mono_")
    )
    hir = compile_canonical_hir(canonical)
    specialized_hir = next(
        item for item in hir.functions if item.name == specialization.name
    )

    assert specialization.parameters == (("left", "UInt64"), ("right", "UInt64"))
    assert any(node.kind == "Compare" for node in specialized_hir.walk())
    assert all("constraint" not in node.kind.lower() for node in specialized_hir.walk())


def test_multiple_constraints_are_checked_for_one_concrete_instance() -> None:
    source = (
        "fn retain[T: Comparable + Hashable + Display + Encode](value: T) -> T:\n"
        "    value\n"
        "fn main() -> UInt64:\n"
        "    retain(9)\n"
    )

    canonical = elaborate(source).canonical
    specialization = next(
        item for item in canonical.functions if item.name.startswith("retain__mono_")
    )

    assert specialization.return_type == "UInt64"
    assert specialization.parameters == (("value", "UInt64"),)


def test_iterable_and_structural_encode_constraints_accept_supported_shapes() -> None:
    source = (
        "record Message:\n"
        "    text: Text\n"
        "    count: UInt64\n"
        "fn iterable[T: Iterable](value: T) -> T:\n"
        "    value\n"
        "fn encoded[T: Encode](value: T) -> T:\n"
        "    value\n"
        "fn keep_values(values: Vec[Text]) -> Vec[Text]:\n"
        "    iterable(values)\n"
        "fn keep_message(value: Message) -> Message:\n"
        "    encoded(value)\n"
        "fn main(values: Vec[Text]) -> Vec[Text]:\n"
        "    keep_values(values)\n"
    )

    canonical = elaborate(source).canonical
    specializations = {
        item.name: item for item in canonical.functions if "__mono_" in item.name
    }

    assert {item.return_type for item in specializations.values()} == {
        "Vec[Text]",
        "Message",
    }


@pytest.mark.parametrize(
    "constraint,type_name",
    [
        ("Comparable", "Vec[UInt64]"),
        ("Hashable", "Float64"),
        ("Iterable", "UInt64"),
        ("Display", "Map[Text,UInt64]"),
    ],
)
def test_unsatisfied_type_constraints_fail_before_hir(
    constraint: str, type_name: str
) -> None:
    source = (
        f"fn constrained[T: {constraint}](value: T) -> T:\n"
        "    value\n"
        f"fn main(value: {type_name}) -> {type_name}:\n"
        "    constrained(value)\n"
    )

    with pytest.raises(
        SurfaceElaborationError,
        match=rf"UnsatisfiedTypeConstraint: constrained.T: .* {constraint}",
    ):
        elaborate(source)


def test_unknown_type_constraints_fail_even_without_an_instantiation() -> None:
    source = (
        "fn unsupported[T: Serializable](value: T) -> T:\n"
        "    value\n"
        "fn main() -> UInt64:\n"
        "    1\n"
    )

    with pytest.raises(SurfaceElaborationError, match="UnknownTypeConstraint"):
        elaborate(source)
