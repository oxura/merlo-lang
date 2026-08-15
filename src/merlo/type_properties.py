"""Canonical semantic properties for Merlo types.

Compiler stages ask this resolver about behavior instead of recognizing type
spellings independently. Declarations are intentionally accepted by shape so
the resolver can be used before HIR has been constructed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from merlo.type_parser import generic_parts


@dataclass(frozen=True)
class TypeProperties:
    is_copy: bool
    is_move: bool
    needs_drop: bool
    contains_borrow: bool = False
    is_resource: bool = False
    layout: str = "value"


_COPY = TypeProperties(True, False, False)
_BORROW = TypeProperties(True, False, False, contains_borrow=True, layout="view")
_OWNER = TypeProperties(False, True, True, layout="owner")
_RESOURCE = TypeProperties(False, True, True, is_resource=True, layout="resource")

_SCALARS = frozenset(
    {
        "Unit", "Bool", "Byte", "Int8", "UInt8", "Int16", "UInt16",
        "Int32", "UInt32", "Int64", "UInt64", "Float32", "Float64",
    }
)
_OWNERS = frozenset({"Text", "Bytes", "TextBuilder", "Json", "Path"})
_RESOURCES = frozenset({"FileReader", "FileWriter"})
_BORROWS = frozenset({"TextView", "BytesView", "FileLines"})


class TypePropertyResolver:
    def __init__(self, declarations: Mapping[str, object] | None = None) -> None:
        self.declarations = declarations or {}
        self._cache: dict[str, TypeProperties] = {}

    def resolve(
        self,
        type_name: str | None,
        seen: frozenset[str] = frozenset(),
    ) -> TypeProperties:
        if not type_name:
            return _COPY
        cached = self._cache.get(type_name)
        if cached is not None and type_name not in seen:
            return cached
        if type_name in _SCALARS or type_name.startswith("fn("):
            return _COPY
        if type_name in _OWNERS:
            return _OWNER
        if type_name in _RESOURCES:
            return _RESOURCE
        if type_name in _BORROWS:
            return _BORROW
        for constructor in ("Slice", "Borrow"):
            if generic_parts(type_name, constructor) is not None:
                return _BORROW

        if type_name in seen:
            # Recursive nominal types require indirection in Merlo. Treat the
            # cycle as move-only while the outer descriptor owns the drop.
            return _OWNER
        next_seen = seen | {type_name}

        array = generic_parts(type_name, "Array", arity=2)
        if array is not None:
            result = self._aggregate((array[0],), next_seen, layout="array")
            self._cache[type_name] = result
            return result
        for constructor, layout in (
            ("Option", "enum"),
            ("Result", "enum"),
        ):
            parts = generic_parts(type_name, constructor)
            if parts is not None:
                result = self._aggregate(parts, next_seen, layout=layout)
                self._cache[type_name] = result
                return result
        for constructor in ("Vec", "Map", "Box", "Future", "Shared"):
            if generic_parts(type_name, constructor) is not None:
                result = TypeProperties(
                    False,
                    True,
                    True,
                    contains_borrow=False,
                    is_resource=constructor == "Future",
                    layout=constructor.casefold(),
                )
                self._cache[type_name] = result
                return result

        declaration = self.declarations.get(type_name)
        if declaration is not None:
            kind = getattr(declaration, "kind", "record")
            if kind == "record":
                children = tuple(
                    field.type_name for field in getattr(declaration, "fields", ())
                )
            else:
                children = tuple(
                    variant.payload_type
                    for variant in getattr(declaration, "variants", ())
                    if variant.payload_type is not None
                )
            result = self._aggregate(children, next_seen, layout=kind)
            self._cache[type_name] = result
            return result

        # Unknown nominal types are conservatively move-only. This fails safe
        # for ownership without teaching every compiler layer their spellings.
        return _OWNER

    def _aggregate(
        self,
        children: tuple[str, ...],
        seen: frozenset[str],
        *,
        layout: str,
    ) -> TypeProperties:
        properties = tuple(self.resolve(child, seen) for child in children)
        needs_drop = any(item.needs_drop for item in properties)
        contains_borrow = any(item.contains_borrow for item in properties)
        return TypeProperties(
            is_copy=not needs_drop and not contains_borrow,
            is_move=needs_drop or contains_borrow,
            needs_drop=needs_drop,
            contains_borrow=contains_borrow,
            is_resource=any(item.is_resource for item in properties),
            layout=layout,
        )


DEFAULT_TYPE_PROPERTIES = TypePropertyResolver()


__all__ = ["DEFAULT_TYPE_PROPERTIES", "TypeProperties", "TypePropertyResolver"]
