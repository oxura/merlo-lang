"""Canonical semantic properties for Merlo types.

Compiler stages ask this resolver about behavior instead of recognizing type
spellings independently. Declarations are intentionally accepted by shape so
the resolver can be used before HIR has been constructed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from merlo.type_arena import TypeArena, TypeArenaError


@dataclass(frozen=True)
class TypeProperties:
    is_copy: bool
    is_move: bool
    needs_drop: bool
    contains_borrow: bool = False
    is_resource: bool = False
    contains_resource: bool = False
    layout: str = "value"
    borrow_types: tuple[str, ...] = ()
    resource_types: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "is_copy": self.is_copy,
            "is_move": self.is_move,
            "needs_drop": self.needs_drop,
            "contains_borrow": self.contains_borrow,
            "is_resource": self.is_resource,
            "contains_resource": self.contains_resource,
            "layout": self.layout,
            "borrow_types": list(self.borrow_types),
            "resource_types": list(self.resource_types),
        }


_COPY = TypeProperties(True, False, False)
_OWNER = TypeProperties(False, True, True, layout="owner")

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
        # This is deliberately resolver-local rather than a process-wide arena:
        # property analysis remains isolated while all structural validation is
        # delegated to the same authority used by TypeArena serialization.
        self._type_arena = TypeArena()

    def _canonical_type(self, type_name: str) -> str:
        try:
            type_id = self._type_arena.intern_text(type_name)
        except TypeArenaError:
            # Preserve the resolver's conservative fallback for malformed or
            # unsupported names while valid structural names use one parser.
            return type_name
        return self._type_arena.canonical(type_id)

    def _generic_parts(
        self,
        type_name: str,
        constructor: str,
        *,
        arity: int | None = None,
    ) -> tuple[str, ...] | None:
        try:
            type_id = self._type_arena.intern_text(type_name)
            reference = self._type_arena.resolve(type_id)
        except TypeArenaError:
            return None
        if reference.constructor != constructor or not reference.arguments:
            return None
        if arity is not None and len(reference.arguments) != arity:
            return None
        return tuple(self._type_arena.canonical(argument) for argument in reference.arguments)

    def resolve(
        self,
        type_name: str | None,
        seen: frozenset[str] = frozenset(),
    ) -> TypeProperties:
        if not type_name:
            return _COPY
        type_name = self._canonical_type(type_name)
        cached = self._cache.get(type_name)
        if cached is not None and type_name not in seen:
            return cached
        if type_name in _SCALARS or type_name.startswith("fn("):
            return _COPY
        if type_name in _OWNERS:
            return _OWNER
        if type_name in _RESOURCES:
            return TypeProperties(
                False,
                True,
                True,
                is_resource=True,
                contains_resource=True,
                layout="resource",
                resource_types=(type_name,),
            )
        if type_name in _BORROWS:
            return TypeProperties(
                True,
                False,
                False,
                contains_borrow=True,
                layout="view",
                borrow_types=(type_name,),
            )
        for constructor in ("Slice", "Borrow"):
            if self._generic_parts(type_name, constructor) is not None:
                return TypeProperties(
                    True,
                    False,
                    False,
                    contains_borrow=True,
                    layout="view",
                    borrow_types=(type_name,),
                )
        if self._generic_parts(type_name, "Fn") is not None:
            return TypeProperties(
                False,
                True,
                True,
                layout="closure",
            )

        if type_name in seen:
            # Recursive nominal types require indirection in Merlo. Treat the
            # cycle as move-only while the outer descriptor owns the drop.
            return _OWNER
        next_seen = seen | {type_name}

        array = self._generic_parts(type_name, "Array", arity=2)
        if array is not None:
            result = self._aggregate((array[0],), next_seen, layout="array")
            self._cache[type_name] = result
            return result
        for constructor, layout in (
            ("Option", "enum"),
            ("Result", "enum"),
        ):
            parts = self._generic_parts(type_name, constructor)
            if parts is not None:
                result = self._aggregate(parts, next_seen, layout=layout)
                self._cache[type_name] = result
                return result
        for constructor in ("Vec", "Box", "Future", "Shared"):
            parts = self._generic_parts(type_name, constructor, arity=1)
            if parts is not None:
                result = self._owning_generic(
                    parts,
                    next_seen,
                    layout=constructor.casefold(),
                    is_resource=constructor == "Future",
                    resource_name=type_name if constructor == "Future" else None,
                )
                self._cache[type_name] = result
                return result
        map_parts = self._generic_parts(type_name, "Map", arity=2)
        if map_parts is not None:
            result = self._owning_generic(
                map_parts,
                next_seen,
                layout="map",
            )
            self._cache[type_name] = result
            return result

        declaration = self.declarations.get(type_name)
        if declaration is not None:
            kind = getattr(
                declaration,
                "kind",
                "enum" if hasattr(declaration, "variants") else "record",
            )
            if kind == "record":
                children = tuple(
                    field.type_name for field in getattr(declaration, "fields", ())
                )
            else:
                children = tuple(
                    variant.payload_type for variant in getattr(declaration, "variants", ())
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
        contains_resource = any(
            item.is_resource or item.contains_resource for item in properties
        )
        return TypeProperties(
            is_copy=not needs_drop and not contains_borrow,
            is_move=needs_drop or contains_borrow,
            needs_drop=needs_drop,
            contains_borrow=contains_borrow,
            contains_resource=contains_resource,
            layout=layout,
            borrow_types=tuple(sorted({
                borrow for item in properties for borrow in item.borrow_types
            })),
            resource_types=tuple(sorted({
                resource for item in properties for resource in item.resource_types
            })),
        )

    def _owning_generic(
        self,
        children: tuple[str, ...],
        seen: frozenset[str],
        *,
        layout: str,
        is_resource: bool = False,
        resource_name: str | None = None,
    ) -> TypeProperties:
        properties = tuple(self.resolve(child, seen) for child in children)
        borrow_types = tuple(sorted({
            borrow for item in properties for borrow in item.borrow_types
        }))
        resource_types = {
            resource for item in properties for resource in item.resource_types
        }
        if resource_name is not None:
            resource_types.add(resource_name)
        return TypeProperties(
            is_copy=False,
            is_move=True,
            needs_drop=True,
            contains_borrow=any(item.contains_borrow for item in properties),
            is_resource=is_resource,
            contains_resource=is_resource or any(
                item.is_resource or item.contains_resource for item in properties
            ),
            layout=layout,
            borrow_types=borrow_types,
            resource_types=tuple(sorted(resource_types)),
        )


DEFAULT_TYPE_PROPERTIES = TypePropertyResolver()


__all__ = ["DEFAULT_TYPE_PROPERTIES", "TypeProperties", "TypePropertyResolver"]
