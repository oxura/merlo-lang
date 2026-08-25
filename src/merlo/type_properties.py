"""Canonical semantic properties for Merlo types.

Type properties are resolved from the compiler-local immutable ``TypeContext``.
The resolver never parses or interns: source-boundary code must validate and
intern spellings before asking for properties by ``TypeId``.
"""

from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Protocol

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
    is_transferable: bool = False
    is_shareable: bool = False
    is_mutable_shareable: bool = False
    is_resource_transferable: bool = False
    is_thread_safe: bool = False
    is_device_transferable: bool = False
    is_pinned: bool = False
    requires_owner_proof: bool = False

    @property
    def transferable(self) -> bool:
        return self.is_transferable

    @property
    def shareable(self) -> bool:
        return self.is_shareable

    @property
    def mutable_shareable(self) -> bool:
        return self.is_mutable_shareable

    @property
    def resource_transferable(self) -> bool:
        return self.is_resource_transferable

    @property
    def thread_safe(self) -> bool:
        return self.is_thread_safe

    @property
    def device_transferable(self) -> bool:
        return self.is_device_transferable

    @property
    def pinned(self) -> bool:
        return self.is_pinned

    def can_transfer(self, *, owner_proof: bool = False) -> bool:
        """Return whether this value may cross a task boundary."""
        return self.is_transferable and (
            not self.requires_owner_proof or owner_proof
        )

    def transfer_to_dict(self) -> dict[str, bool]:
        return {
            "is_transferable": self.is_transferable,
            "is_shareable": self.is_shareable,
            "is_mutable_shareable": self.is_mutable_shareable,
            "is_resource_transferable": self.is_resource_transferable,
            "is_thread_safe": self.is_thread_safe,
            "is_device_transferable": self.is_device_transferable,
            "is_pinned": self.is_pinned,
            "requires_owner_proof": self.requires_owner_proof,
        }

    def to_dict(self, context: TypeAuthority | None = None) -> dict[str, object]:
        """Return the canonical diagnostic payload, rendering IDs through context."""
        if context is None and (self.borrow_types or self.resource_types):
            raise TypeArenaError(
                "TypeProperties.to_dict requires TypeAuthority for contained types"
            )
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
            **self.transfer_to_dict(),
        }
_COPY = TypeProperties(
    True,
    False,
    False,
    is_transferable=True,
    is_shareable=True,
    is_thread_safe=True,
    is_device_transferable=True,
)
_OWNER = TypeProperties(False, True, True, layout="owner")

_SCALARS = frozenset(
    {
        "Unit",
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
_IMMUTABLE_OWNERS = frozenset({"Text", "Bytes", "Json", "Path"})
_MUTABLE_OWNERS = frozenset({"TextBuilder"})
_RESOURCES = frozenset({"FileReader", "FileWriter"})
_BORROWS = frozenset({"TextView", "BytesView", "FileLines", "MapEntry"})
_OWNER_PROOF_BORROWS = frozenset({"TextView", "BytesView"})
_RAW_POINTERS = frozenset({"RawPointer", "Ptr", "ConstPointer", "MutPointer"})


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
    """Resolve ownership and task-boundary properties structurally."""

    def __init__(
        self,
        context: TypeAuthority,
        *,
        resource_transfer_policy: Mapping[str, bool] | Iterable[str] | None = None,
    ) -> None:
        required = ("resolve", "declaration", "render")
        if any(not callable(getattr(context, name, None)) for name in required):
            raise TypeArenaError("TypePropertyResolver requires TypeAuthority")
        self.context = context
        self._resource_transfer_policy = self._normalize_resource_policy(
            resource_transfer_policy
        )
        self._cache: dict[TypeId, TypeProperties] = {}

    @staticmethod
    def _normalize_resource_policy(
        policy: Mapping[str, bool] | Iterable[str] | None,
    ) -> frozenset[str]:
        if policy is None:
            return frozenset()
        if isinstance(policy, Mapping):
            items = tuple(policy.items())
            if any(
                not isinstance(name, str)
                or not name
                or type(enabled) is not bool
                for name, enabled in items
            ):
                raise TypeArenaError("invalid resource transfer policy")
            return frozenset(name for name, enabled in items if enabled)
        if isinstance(policy, (str, bytes)):
            raise TypeArenaError("resource transfer policy must be a mapping or sequence")
        values = tuple(policy)
        if any(not isinstance(name, str) or not name for name in values):
            raise TypeArenaError("invalid resource transfer policy")
        return frozenset(values)

    @property
    def resource_transfer_policy(self) -> tuple[str, ...]:
        return tuple(sorted(self._resource_transfer_policy))

    def _resource_allowed(self, type_id: TypeId, constructor: str) -> bool:
        return (
            constructor in self._resource_transfer_policy
            or self.context.render(type_id) in self._resource_transfer_policy
        )

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
        reference = self.context.resolve(type_id)
        if type_id in seen:
            return _OWNER
        cached = self._cache.get(type_id)
        if cached is not None and type_id not in seen:
            return cached
        constructor = reference.constructor
        if constructor in _SCALARS or (
            constructor == "Fn" and not reference.arguments
        ):
            result = _COPY
        elif constructor in _IMMUTABLE_OWNERS:
            result = TypeProperties(
                False,
                True,
                True,
                layout="owner",
                is_transferable=True,
                is_shareable=True,
                is_thread_safe=True,
                is_device_transferable=True,
            )
        elif constructor in _MUTABLE_OWNERS:
            result = TypeProperties(
                False,
                True,
                True,
                layout="owner",
                is_transferable=True,
                is_thread_safe=True,
            )
        elif constructor in _RESOURCES:
            allowed = self._resource_allowed(type_id, constructor)
            result = TypeProperties(
                False,
                True,
                True,
                is_resource=True,
                contains_resource=True,
                layout="resource",
                resource_types=(type_id,),
                is_transferable=allowed,
                is_resource_transferable=allowed,
                is_thread_safe=allowed,
                is_pinned=True,
            )
        elif constructor in _BORROWS:
            owner_proof = constructor in _OWNER_PROOF_BORROWS
            result = TypeProperties(
                True,
                False,
                False,
                contains_borrow=True,
                layout="view",
                borrow_types=(type_id,),
                is_transferable=owner_proof,
                is_thread_safe=False,
                requires_owner_proof=owner_proof,
            )
        elif constructor in _RAW_POINTERS:
            result = TypeProperties(
                False,
                True,
                False,
                layout="pointer",
                is_pinned=True,
            )
        else:
            next_seen = seen | {type_id}
            children = tuple(reference.arguments)
            if constructor in {"Slice", "Borrow"}:
                result = self._borrowed_generic(children, next_seen, type_id)
            elif constructor == "Fn":
                result = replace(
                    self._aggregate(
                        children,
                        next_seen,
                        layout="closure",
                        shareable=False,
                        device_transferable=False,
                    ),
                    is_copy=False,
                    is_move=True,
                    needs_drop=True,
                )
            elif constructor == "Array" and len(children) == 2:
                result = self._aggregate(
                    (children[0],),
                    next_seen,
                    layout="array",
                )
            elif constructor in {"Option", "Result"} and children:
                result = self._aggregate(
                    children,
                    next_seen,
                    layout="enum",
                )
            elif constructor in {
                "Vec",
                "Box",
                "Future",
                "Shared",
            } and len(children) == 1:
                result = self._owning_generic(
                    children,
                    next_seen,
                    type_id=type_id,
                    constructor=constructor,
                )
            elif constructor == "Map" and len(children) == 2:
                result = self._owning_generic(
                    children,
                    next_seen,
                    type_id=type_id,
                    constructor=constructor,
                )
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

    def _borrowed_generic(
        self,
        children: tuple[TypeId, ...],
        seen: frozenset[TypeId],
        type_id: TypeId,
    ) -> TypeProperties:
        nested = tuple(self._resolve(child, seen) for child in children)
        contains_resource = any(
            item.is_resource or item.contains_resource for item in nested
        )
        return TypeProperties(
            True,
            False,
            False,
            contains_borrow=True,
            contains_resource=contains_resource,
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
            is_thread_safe=False,
            requires_owner_proof=True,
        )

    def _aggregate(
        self,
        children: tuple[TypeId, ...],
        seen: frozenset[TypeId],
        *,
        layout: str,
        shareable: bool = True,
        device_transferable: bool = True,
    ) -> TypeProperties:
        properties = tuple(self._resolve(child, seen) for child in children)
        contains_borrow = any(item.contains_borrow for item in properties)
        contains_resource = any(
            item.is_resource or item.contains_resource for item in properties
        )
        requires_owner_proof = any(
            item.requires_owner_proof or item.contains_borrow
            for item in properties
        )
        resource_transferable = contains_resource and all(
            not item.is_resource
            and not item.contains_resource
            or item.is_resource_transferable
            for item in properties
        )
        transferable = (
            all(item.is_transferable for item in properties)
            and not contains_borrow
            and not requires_owner_proof
            and (not contains_resource or resource_transferable)
        )
        thread_safe = (
            all(item.is_thread_safe for item in properties)
            and not contains_borrow
            and (not contains_resource or resource_transferable)
        )
        return TypeProperties(
            is_copy=not any(item.needs_drop for item in properties)
            and not contains_borrow,
            is_move=any(item.needs_drop or item.contains_borrow for item in properties),
            needs_drop=any(item.needs_drop for item in properties),
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
            is_transferable=transferable,
            is_shareable=(
                shareable
                and all(item.is_shareable for item in properties)
                and not contains_resource
            ),
            is_resource_transferable=resource_transferable,
            is_thread_safe=thread_safe,
            is_device_transferable=(
                device_transferable
                and all(item.is_device_transferable for item in properties)
                and not contains_borrow
                and not contains_resource
            ),
            is_pinned=any(item.is_pinned for item in properties),
            requires_owner_proof=requires_owner_proof,
        )

    def _owning_generic(
        self,
        children: tuple[TypeId, ...],
        seen: frozenset[TypeId],
        *,
        type_id: TypeId,
        constructor: str,
    ) -> TypeProperties:
        properties = tuple(self._resolve(child, seen) for child in children)
        contains_borrow = any(item.contains_borrow for item in properties)
        contains_resource = any(
            item.is_resource or item.contains_resource for item in properties
        )
        requires_owner_proof = any(
            item.requires_owner_proof or item.contains_borrow
            for item in properties
        )
        resource_transferable = contains_resource and all(
            not item.is_resource
            and not item.contains_resource
            or item.is_resource_transferable
            for item in properties
        )
        allowed = (
            self._resource_allowed(type_id, constructor)
            if constructor == "Future"
            else True
        )
        transferable = (
            allowed
            and all(item.is_transferable for item in properties)
            and not contains_borrow
            and not requires_owner_proof
            and (not contains_resource or resource_transferable)
        )
        thread_safe = (
            allowed
            and all(item.is_thread_safe for item in properties)
            and not contains_borrow
            and (not contains_resource or resource_transferable)
        )
        mutable_shareable = (
            constructor == "Shared"
            and thread_safe
            and not contains_borrow
            and not contains_resource
        )
        return TypeProperties(
            False,
            True,
            True,
            contains_borrow=contains_borrow,
            is_resource=constructor == "Future",
            contains_resource=constructor == "Future" or contains_resource,
            layout=constructor.casefold(),
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
                    *(resource for item in properties for resource in item.resource_types),
                    *((type_id,) if constructor == "Future" else ()),
                ),
            ),
            is_transferable=transferable,
            is_mutable_shareable=mutable_shareable,
            is_resource_transferable=(
                allowed and (constructor == "Future" or resource_transferable)
            ),
            is_thread_safe=thread_safe,
            is_device_transferable=(
                constructor == "Vec"
                and all(item.is_device_transferable for item in properties)
                and not contains_borrow
                and not contains_resource
            ),
            is_pinned=constructor in {"Future", "Shared"}
            or any(item.is_pinned for item in properties),
            requires_owner_proof=requires_owner_proof,
        )


__all__ = ["TypeAuthority", "TypeProperties", "TypePropertyResolver"]
