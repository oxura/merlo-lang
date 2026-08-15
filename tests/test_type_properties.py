from __future__ import annotations

from dataclasses import dataclass

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
