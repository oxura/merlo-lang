"""Structural General collection protocol shared by compiler stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from merlo.type_arena import TypeArenaError, TypeId, TypeRef

COLLECTION_OPERATIONS = frozenset({"where", "map", "count"})
FUSIBLE_COLLECTION_ELEMENTS = frozenset(
    {
        "Bool",
        "Byte",
        "Int8",
        "UInt8",
        "Int16",
        "UInt16",
        "Int32",
        "UInt32",
        "Int64",
        "UInt64",
        "Float32",
        "Float64",
    }
)


class _TypeAuthority(Protocol):
    def resolve(self, type_id: TypeId) -> TypeRef: ...

    def render(self, type_id: TypeId) -> str: ...

    def type_id(self, spelling: str) -> TypeId: ...


@dataclass(frozen=True)
class CollectionShape:
    """Validated collection projection keyed by structural type identities.

    ``type_name`` and ``element_type`` are diagnostic renderings only. All
    semantic consumers must use the two ``TypeId`` fields.
    """

    type_id: TypeId
    kind: str
    element_type_id: TypeId
    fixed_length: int | None = None
    type_name: str = ""
    element_type: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.type_id, TypeId):
            raise TypeArenaError("CollectionShape.type_id must be TypeId")
        if not isinstance(self.element_type_id, TypeId):
            raise TypeArenaError("CollectionShape.element_type_id must be TypeId")

    @property
    def length_source(self) -> str:
        return "fixed" if self.fixed_length is not None else "runtime"


def _type_id(authority: _TypeAuthority, spelling: str) -> TypeId:
    try:
        return authority.type_id(spelling)
    except TypeArenaError:
        intern_text = getattr(authority, "intern_text", None)
        if intern_text is None:
            raise
        return intern_text(spelling)


def _shape(
    type_id: TypeId,
    authority: _TypeAuthority,
) -> CollectionShape | None:
    reference = authority.resolve(type_id)
    constructor = reference.constructor
    arguments = reference.arguments
    if constructor == "Borrow":
        if len(arguments) != 1:
            return None
        underlying = _shape(arguments[0], authority)
        if underlying is None:
            return None
        return CollectionShape(
            type_id,
            underlying.kind,
            underlying.element_type_id,
            underlying.fixed_length,
            authority.render(type_id),
            authority.render(underlying.element_type_id),
        )
    if constructor == "Vec":
        if len(arguments) != 1:
            return None
        return CollectionShape(
            type_id,
            "vec",
            arguments[0],
            type_name=authority.render(type_id),
            element_type=authority.render(arguments[0]),
        )
    if constructor == "Array":
        if len(arguments) != 2:
            return None
        try:
            length = int(authority.render(arguments[1]))
        except (TypeError, ValueError):
            return None
        if length < 0:
            return None
        return CollectionShape(
            type_id,
            "array",
            arguments[0],
            length,
            authority.render(type_id),
            authority.render(arguments[0]),
        )
    if constructor == "Slice":
        if len(arguments) != 1:
            return None
        return CollectionShape(
            type_id,
            "slice",
            arguments[0],
            type_name=authority.render(type_id),
            element_type=authority.render(arguments[0]),
        )
    elements = {
        "Bytes": ("bytes", "Byte"),
        "BytesView": ("bytes_view", "Byte"),
        "Text": ("text", "Byte"),
        "TextView": ("text_view", "Byte"),
    }
    matched = elements.get(constructor)
    if matched is None or arguments:
        return None
    element_type_id = _type_id(authority, matched[1])
    return CollectionShape(
        type_id,
        matched[0],
        element_type_id,
        type_name=authority.render(type_id),
        element_type=authority.render(element_type_id),
    )


def collection_shape(
    type_id: TypeId | str | None,
    authority: _TypeAuthority | None = None,
) -> CollectionShape | None:
    """Resolve a validated collection identity.

    The string form is retained only as a compatibility boundary for
    non-semantic adapters. Production semantic callers pass ``TypeId`` and
    their compiler-local authority explicitly.
    """

    if type_id is None:
        return None
    if isinstance(type_id, str):
        if authority is None:
            from merlo.type_arena import TypeContextBuilder

            compatibility = TypeContextBuilder(allow_unresolved=False)
            try:
                resolved = compatibility.intern_text(type_id)
            except TypeArenaError:
                return None
            return _shape(resolved, compatibility)
        try:
            type_id = authority.type_id(type_id)
        except TypeArenaError:
            return None
    if not isinstance(type_id, TypeId):
        raise TypeArenaError("collection_shape requires TypeId")
    if authority is None:
        raise TypeArenaError("collection_shape requires a TypeAuthority")
    return _shape(type_id, authority)


def _result_type_id(
    operation: str,
    element_type_id: TypeId,
    authority: _TypeAuthority,
) -> TypeId:
    if operation == "count":
        return _type_id(authority, "UInt64")
    if operation not in COLLECTION_OPERATIONS:
        raise ValueError(f"unknown General collection operation {operation}")
    constructor = TypeRef("Vec", (element_type_id,))
    intern_node = getattr(authority, "intern_node", None)
    if intern_node is not None:
        return intern_node(constructor.constructor, constructor.arguments)
    return authority.arena.identity(constructor)


def collection_result_type(
    operation: str,
    element_type_id: TypeId | str,
    authority: _TypeAuthority | None = None,
) -> TypeId | str:
    """Return the result identity for a General collection operation."""

    if isinstance(element_type_id, str):
        if authority is None:
            from merlo.type_arena import TypeContextBuilder

            compatibility = TypeContextBuilder(allow_unresolved=False)
            result = _result_type_id(
                operation,
                compatibility.intern_text(element_type_id),
                compatibility,
            )
            return compatibility.render(result)
        element_type_id = authority.type_id(element_type_id)
    if not isinstance(element_type_id, TypeId):
        raise TypeArenaError("collection_result_type requires TypeId")
    if authority is None:
        raise TypeArenaError("collection_result_type requires a TypeAuthority")
    return _result_type_id(operation, element_type_id, authority)


__all__ = [
    "COLLECTION_OPERATIONS",
    "FUSIBLE_COLLECTION_ELEMENTS",
    "CollectionShape",
    "collection_result_type",
    "collection_shape",
]
