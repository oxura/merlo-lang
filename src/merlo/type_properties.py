"""Canonical semantic properties for Merlo types.

Type properties are resolved from the compiler-local immutable ``TypeContext``.
The resolver never parses or interns: source-boundary code must validate and
intern spellings before asking for properties by ``TypeId``.
"""

from dataclasses import dataclass
from typing import Iterable, Protocol

from merlo.type_arena import TypeArenaError, TypeId


class TypeAuthority(Protocol):
    """Read-only type operations shared by builders and frozen contexts."""

    def resolve(self, type_id: TypeId) -> object: ...
    def declaration(self, type_id: TypeId) -> object: ...
    def render(self, type_id: TypeId) -> str: ...
    def type_id(self, spelling: str) -> TypeId: ...


@dataclass(frozen=True)
class TypeProperties:
    is_copy: bool
    is_move: bool
    needs_drop: bool
    contains_borrow: bool = False
    is_resource: bool = False
    contains_resource: bool = False
    layout: str = "value"
    borrow_types: tuple[TypeId, ...] = ()
    resource_types: tuple[TypeId, ...] = ()

    def to_dict(self, context: TypeAuthority | None = None) -> dict[str, object]:
        """Return the legacy diagnostic payload, rendering IDs through context.

        The in-memory properties remain TypeId-only.  Callers serializing the
        legacy property payload provide the owning context to retain canonical
        spellings without introducing a parser at this layer.
        """
        if context is None and (self.borrow_types or self.resource_types):
            raise TypeArenaError("TypeProperties.to_dict requires TypeAuthority for contained types")
        render = context.render if context is not None else str
        return {
            "is_copy": self.is_copy,
            "is_move": self.is_move,
            "needs_drop": self.needs_drop,
            "contains_borrow": self.contains_borrow,
            "is_resource": self.is_resource,
            "contains_resource": self.contains_resource,
            "layout": self.layout,
            "borrow_types": [render(item) for item in self.borrow_types],
            "resource_types": [render(item) for item in self.resource_types],
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


def _unique_ids(
    context: TypeAuthority,
    values: Iterable[TypeId],
) -> tuple[TypeId, ...]:
    return tuple(sorted(set(values), key=context.render))


def _member_type_id(member: object) -> TypeId | None:
    """Read a declaration projection member without accepting spellings."""
    if isinstance(member, TypeId):
        return member
    value = getattr(member, "type_id", None)
    if value is not None and not isinstance(value, TypeId):
        raise TypeArenaError("declaration member type_id must be TypeId")
    return value


class TypePropertyResolver:
    """Resolve ownership properties through one read-only type authority."""

    def __init__(self, context: TypeAuthority) -> None:
        required = ("resolve", "declaration", "render")
        if any(not callable(getattr(context, name, None)) for name in required):
            raise TypeArenaError("TypePropertyResolver requires TypeAuthority")
        self.context = context
        self._cache: dict[TypeId, TypeProperties] = {}

    def resolve(self, type_id: TypeId) -> TypeProperties:
        """Resolve one validated type identity."""
        return self._resolve(type_id, frozenset())

    def _resolve(
        self,
        type_id: TypeId,
        seen: frozenset[TypeId],
    ) -> TypeProperties:
        if not isinstance(type_id, TypeId):
            raise TypeArenaError("TypePropertyResolver.resolve requires TypeId")
        # Exact lookup is intentionally the first operation: malformed or
        # foreign identities cannot receive a conservative fallback.
        reference = self.context.resolve(type_id)
        if type_id in seen:
            return _OWNER
        cached = self._cache.get(type_id)
        if cached is not None and type_id not in seen:
            return cached
        constructor = reference.constructor
        if constructor in _SCALARS or constructor == "Fn" and not reference.arguments:
            result = _COPY
        elif constructor in _OWNERS:
            result = _OWNER
        elif constructor in _RESOURCES:
            result = TypeProperties(
                False,
                True,
                True,
                is_resource=True,
                contains_resource=True,
                layout="resource",
                resource_types=(type_id,),
            )
        elif constructor in _BORROWS:
            result = TypeProperties(
                True,
                False,
                False,
                contains_borrow=True,
                layout="view",
                borrow_types=(type_id,),
            )
        else:
            next_seen = seen | {type_id}
            children = tuple(reference.arguments)
            if constructor in {"Slice", "Borrow"}:
                # A view is copyable, but retain nested contained properties
                # so recursive generic graphs cannot hide resources.
                nested = tuple(self._resolve(child, next_seen) for child in children)
                result = TypeProperties(
                    True,
                    False,
                    False,
                    contains_borrow=True,
                    contains_resource=any(
                        item.is_resource or item.contains_resource for item in nested
                    ),
                    layout="view",
                    borrow_types=_unique_ids(
                        self.context,
                        (
                            type_id,
                            *(borrow for item in nested for borrow in item.borrow_types),
                        ),
                    ),
                    resource_types=_unique_ids(
                        self.context,
                        (
                            resource
                            for item in nested
                            for resource in item.resource_types
                        ),
                    ),
                )
            elif constructor == "Fn":
                nested = tuple(self._resolve(child, next_seen) for child in children)
                result = TypeProperties(
                    False,
                    True,
                    True,
                    contains_borrow=any(item.contains_borrow for item in nested),
                    contains_resource=any(
                        item.is_resource or item.contains_resource for item in nested
                    ),
                    layout="closure",
                    borrow_types=_unique_ids(
                        self.context,
                        (
                            borrow
                            for item in nested
                            for borrow in item.borrow_types
                        ),
                    ),
                    resource_types=_unique_ids(
                        self.context,
                        (
                            resource
                            for item in nested
                            for resource in item.resource_types
                        ),
                    ),
                )
            elif constructor == "Array" and len(children) == 2:
                result = self._aggregate((children[0],), next_seen, layout="array")
            elif constructor in {"Option", "Result"} and children:
                result = self._aggregate(children, next_seen, layout="enum")
            elif constructor in {"Vec", "Box", "Future", "Shared"} and len(children) == 1:
                result = self._owning_generic(
                    children,
                    next_seen,
                    layout=constructor.casefold(),
                    is_resource=constructor == "Future",
                    resource_id=type_id if constructor == "Future" else None,
                )
            elif constructor == "Map" and len(children) == 2:
                result = self._owning_generic(children, next_seen, layout="map")
            else:
                try:
                    declaration = self.context.declaration(type_id)
                except TypeArenaError:
                    declaration = None
                if declaration is None:
                    result = _OWNER
                else:
                    kind = getattr(
                        declaration,
                        "kind",
                        "enum" if hasattr(declaration, "variants") else "record",
                    )
                    if kind == "record":
                        members = getattr(declaration, "fields", ())
                        declaration_children = tuple(
                            member_id
                            for member in members
                            for member_id in (_member_type_id(member),)
                            if member_id is not None
                        )
                    else:
                        variants = getattr(declaration, "variants", ())
                        declaration_children = tuple(
                            member_id
                            for variant in variants
                            for member_id in (_member_type_id(variant),)
                            if member_id is not None
                        )
                    result = self._aggregate(
                        declaration_children,
                        next_seen,
                        layout=kind,
                    )
        if not seen:
            self._cache[type_id] = result
        return result

    def _aggregate(
        self,
        children: tuple[TypeId, ...],
        seen: frozenset[TypeId],
        *,
        layout: str,
    ) -> TypeProperties:
        properties = tuple(self._resolve(child, seen) for child in children)
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
            borrow_types=_unique_ids(
                self.context,
                (
                    borrow
                    for item in properties
                    for borrow in item.borrow_types
                ),
            ),
            resource_types=_unique_ids(
                self.context,
                (
                    resource
                    for item in properties
                    for resource in item.resource_types
                ),
            ),
        )

    def _owning_generic(
        self,
        children: tuple[TypeId, ...],
        seen: frozenset[TypeId],
        *,
        layout: str,
        is_resource: bool = False,
        resource_id: TypeId | None = None,
    ) -> TypeProperties:
        properties = tuple(self._resolve(child, seen) for child in children)
        resource_types = {
            resource for item in properties for resource in item.resource_types
        }
        if resource_id is not None:
            resource_types.add(resource_id)
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
            borrow_types=_unique_ids(
                self.context,
                (
                    borrow
                    for item in properties
                    for borrow in item.borrow_types
                ),
            ),
            resource_types=_unique_ids(self.context, resource_types),
        )


__all__ = ["TypeAuthority", "TypeProperties", "TypePropertyResolver"]
