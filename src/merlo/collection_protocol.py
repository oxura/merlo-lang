"""Structural General collection protocol shared by compiler stages."""

from __future__ import annotations

from dataclasses import dataclass

from merlo.type_parser import generic_parts

COLLECTION_OPERATIONS = frozenset({"where", "map", "count"})

@dataclass(frozen=True)
class CollectionShape:
    type_name: str
    kind: str
    element_type: str
    fixed_length: int | None = None

    @property
    def length_source(self) -> str:
        return "fixed" if self.fixed_length is not None else "runtime"


def collection_shape(type_name: str | None) -> CollectionShape | None:
    """Resolve a concrete type's zero-cost General collection instance."""

    if not type_name:
        return None
    borrowed = generic_parts(type_name, "Borrow", arity=1)
    if borrowed is not None:
        underlying = collection_shape(borrowed[0])
        if underlying is None:
            return None
        return CollectionShape(
            type_name,
            underlying.kind,
            underlying.element_type,
            underlying.fixed_length,
        )
    vector = generic_parts(type_name, "Vec", arity=1)
    if vector is not None:
        return CollectionShape(type_name, "vec", vector[0])
    array = generic_parts(type_name, "Array", arity=2)
    if array is not None:
        try:
            length = int(array[1])
        except ValueError:
            return None
        if length < 0:
            return None
        return CollectionShape(type_name, "array", array[0], length)
    sliced = generic_parts(type_name, "Slice", arity=1)
    if sliced is not None:
        return CollectionShape(type_name, "slice", sliced[0])
    elements = {
        "Bytes": ("bytes", "Byte"),
        "BytesView": ("bytes_view", "Byte"),
        "Text": ("text", "Byte"),
        "TextView": ("text_view", "Byte"),
    }
    matched = elements.get(type_name)
    if matched is None:
        return None
    return CollectionShape(type_name, matched[0], matched[1])


def collection_result_type(operation: str, element_type: str) -> str:
    """Return the normalized result type for a General collection operation."""

    if operation == "count":
        return "UInt64"
    if operation in COLLECTION_OPERATIONS:
        return f"Vec[{element_type}]"
    raise ValueError(f"unknown General collection operation {operation}")


__all__ = [
    "COLLECTION_OPERATIONS",
    "CollectionShape",
    "collection_result_type",
    "collection_shape",
]
