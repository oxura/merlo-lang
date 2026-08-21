from __future__ import annotations

from dataclasses import dataclass

from merlo.type_arena import TypeArena
from merlo.type_properties import TypePropertyResolver


@dataclass(frozen=True)
class _Field:
    type_name: str


@dataclass(frozen=True)
class _Record:
    kind: str
    fields: tuple[_Field, ...]
    variants: tuple[object, ...] = ()


def test_descriptor_properties_recurse_without_type_spelling_tables() -> None:
    resolver = TypePropertyResolver(
        {
            "ScalarRecord": _Record("record", (_Field("UInt64"),)),
            "OwnedRecord": _Record("record", (_Field("Option[Text]"),)),
        }
    )
    assert resolver.resolve("Array[UInt64,4]").is_copy
    assert not resolver.resolve("Array[Text,4]").is_copy
    assert resolver.resolve("Array[Text,4]").needs_drop
    assert not resolver.resolve("Option[UInt64]").needs_drop
    assert resolver.resolve("Result[UInt64,Text]").needs_drop
    assert resolver.resolve("ScalarRecord").is_copy
    assert resolver.resolve("OwnedRecord").needs_drop
    assert resolver.resolve("FileWriter").is_resource
    assert resolver.resolve("Borrow[Text]").contains_borrow


def test_owning_generics_recursively_propagate_contained_borrows() -> None:
    resolver = TypePropertyResolver()

    cases = {
        "Vec[TextView]": ("TextView",),
        "Box[BytesView]": ("BytesView",),
        "Map[Text,TextView]": ("TextView",),
        "Option[Vec[TextView]]": ("TextView",),
        "Result[Box[BytesView],Text]": ("BytesView",),
        "Vec[Option[Borrow[Text]]]": ("Borrow[Text]",),
        "Future[Vec[TextView]]": ("TextView",),
        "Shared[Option[BytesView]]": ("BytesView",),
    }
    for type_name, borrow_types in cases.items():
        properties = resolver.resolve(type_name)
        assert properties.contains_borrow, type_name
        assert properties.borrow_types == borrow_types
        assert properties.is_move
        assert properties.needs_drop


def test_owning_generics_recursively_propagate_resources() -> None:
    resolver = TypePropertyResolver()

    assert resolver.resolve("Vec[FileReader]").contains_resource
    assert resolver.resolve("Box[Option[FileWriter]]").contains_resource
    assert resolver.resolve("Map[Text,FileReader]").contains_resource
    future = resolver.resolve("Future[UInt64]")
    assert future.is_resource
    assert future.contains_resource
    assert future.resource_types == ("Future[UInt64]",)


def test_non_borrowing_owning_containers_keep_existing_properties() -> None:
    resolver = TypePropertyResolver()

    for type_name in (
        "Vec[Text]",
        "Box[Text]",
        "Map[Text,UInt64]",
    ):
        properties = resolver.resolve(type_name)
        assert properties.is_move
        assert properties.needs_drop
        assert not properties.contains_borrow


def test_type_property_consumer_uses_arena_canonical_aliases() -> None:
    resolver = TypePropertyResolver()
    arena = TypeArena()

    # The consumer's private arena is intentionally local, but it must agree
    # with the public arena's structural identity and alias normalization.
    assert resolver.resolve("Vec[Int]") == resolver.resolve("Vec[Int64]")
    assert arena.canonical(arena.intern_text("Array[UInt,4]")) == "Array[UInt64,4]"
