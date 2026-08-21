"""Content-addressed structural type identities for Merlo compiler stages.

Text spellings enter through `TypeArena.intern_text`; direct structural `TypeRef`
values also normalize aliases. The arena interns the structural type graph and
returns stable `TypeId` values. The v1 arena is deliberately small: it does not
replace the existing frontend yet, but provides a versioned bridge for migrating
HIR, RIR, MIR, ownership, and ContractGraph away from free-form type strings.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from merlo.type_parser import (
    GenericTypeSyntaxError,
    TypeExpr,
    parse_type,
    validate_constructor_arity,
    validate_type_expr,
)


TYPE_ARENA_SCHEMA_VERSION = 1
TYPE_ARENA_CONTRACT = "merlo.type-arena.v1"
TYPE_ID_CONTRACT = "merlo.type-id.v1"
TYPE_REF_CONTRACT = "merlo.type-ref.v1"

_TYPE_ALIASES = {
    "Int": "Int64",
    "UInt": "UInt64",
    "Float": "Float64",
}
_TYPE_ATOM = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*|\d+|\?")


class TypeArenaError(ValueError):
    """Base error for malformed or inconsistent type-arena data."""


class UnknownTypeIdError(TypeArenaError):
    """A requested or referenced type identity is absent from the arena."""


class TypeArenaSchemaError(TypeArenaError):
    """Serialized type-arena data violates the closed v1 schema."""


class UnresolvedTypeError(TypeArenaError):
    """An unresolved ``?`` type crossed a boundary that requires closed types."""


class FrozenTypeArenaMutation(TypeArenaError):
    """Interning was attempted after the type arena was frozen."""


def _mapping(
    value: object,
    expected: set[str],
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise TypeArenaSchemaError(f"invalid {label}")
    if not all(isinstance(key, str) for key in value):
        raise TypeArenaSchemaError(f"{label} keys must be text")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TypeArenaSchemaError(f"{label} must be text")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _normalize_constructor(name: str) -> str:
    return _TYPE_ALIASES.get(name, name)


def _validate_constructor(
    constructor: str,
    argument_count: int,
    *,
    schema: bool = False,
) -> None:
    try:
        validate_constructor_arity(constructor, argument_count)
    except GenericTypeSyntaxError as exc:
        error = TypeArenaSchemaError if schema else TypeArenaError
        raise error(str(exc)) from exc


@dataclass(frozen=True, order=True)
class TypeId:
    """A full SHA-256 identity of one normalized structural type node."""

    value: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.value):
            raise TypeArenaSchemaError("TypeId must be 64 lowercase hexadecimal characters")

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> dict[str, str]:
        return {
            "contract": TYPE_ID_CONTRACT,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> "TypeId":
        raw = _mapping(value, {"contract", "value"}, "TypeId")
        if raw["contract"] != TYPE_ID_CONTRACT:
            raise TypeArenaSchemaError("TypeId contract mismatch")
        return cls(_text(raw["value"], "TypeId value"))


@dataclass(frozen=True, order=True)
class TypeRef:
    """One normalized node whose arguments are other interned TypeIds."""

    constructor: str
    arguments: tuple[TypeId, ...] = ()

    def __post_init__(self) -> None:
        constructor = self.constructor
        if not isinstance(constructor, str):
            raise TypeArenaError(f"invalid type constructor: {constructor!r}")
        constructor = _normalize_constructor(constructor)
        if not _TYPE_ATOM.fullmatch(constructor):
            raise TypeArenaError(f"invalid type constructor: {self.constructor!r}")
        try:
            arguments = tuple(self.arguments)
        except TypeError as exc:
            raise TypeArenaError("TypeRef arguments must be iterable") from exc
        if any(not isinstance(argument, TypeId) for argument in arguments):
            raise TypeArenaError("TypeRef arguments must be TypeId values")
        _validate_constructor(constructor, len(arguments))
        object.__setattr__(self, "constructor", constructor)
        object.__setattr__(self, "arguments", arguments)

    def semantic_payload(self) -> dict[str, object]:
        return {
            "contract": TYPE_REF_CONTRACT,
            "constructor": self.constructor,
            "arguments": [argument.value for argument in self.arguments],
        }

    def to_dict(self) -> dict[str, object]:
        return self.semantic_payload()

    @classmethod
    def from_dict(cls, value: object) -> "TypeRef":
        raw = _mapping(
            value,
            {"contract", "constructor", "arguments"},
            "TypeRef",
        )
        if raw["contract"] != TYPE_REF_CONTRACT:
            raise TypeArenaSchemaError("TypeRef contract mismatch")
        arguments = raw["arguments"]
        if not isinstance(arguments, list):
            raise TypeArenaSchemaError("TypeRef arguments must be a list")
        try:
            return cls(
                _text(raw["constructor"], "type constructor"),
                tuple(TypeId(_text(item, "type argument identity")) for item in arguments),
            )
        except TypeArenaError as exc:
            raise TypeArenaSchemaError(str(exc)) from exc


def _identity(reference: TypeRef) -> TypeId:
    envelope = {
        "contract": TYPE_ID_CONTRACT,
        "payload": reference.semantic_payload(),
    }
    payload = _canonical_json(envelope).encode("utf-8")
    return TypeId(hashlib.sha256(payload).hexdigest())


def _contains_unresolved(expression: TypeExpr) -> bool:
    return expression.name == "?" or any(
        _contains_unresolved(argument) for argument in expression.args
    )


class TypeArena:
    """Deterministic interning and serialization for structural Merlo types."""

    def __init__(self, *, allow_unresolved: bool = False) -> None:
        self.allow_unresolved = bool(allow_unresolved)
        self._nodes: dict[TypeId, TypeRef] = {}
        self._frozen_snapshot: FrozenTypeArena | None = None

    def _assert_mutable(self) -> None:
        if self._frozen_snapshot is not None:
            raise FrozenTypeArenaMutation("TypeArena is frozen")

    def freeze(self) -> FrozenTypeArena:
        if self._frozen_snapshot is None:
            self._frozen_snapshot = FrozenTypeArena(
                self._nodes,
                allow_unresolved=self.allow_unresolved,
            )
        return self._frozen_snapshot

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, type_id: object) -> bool:
        return isinstance(type_id, TypeId) and type_id in self._nodes

    @property
    def ids(self) -> tuple[TypeId, ...]:
        return tuple(sorted(self._nodes))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def intern_text(self, type_name: str) -> TypeId:
        self._assert_mutable()
        try:
            expression = validate_type_expr(parse_type(type_name))
        except GenericTypeSyntaxError as exc:
            raise TypeArenaError(f"invalid type expression: {exc}") from exc
        return self.intern_expr(expression)

    def intern_many(self, type_names: Iterable[str]) -> tuple[TypeId, ...]:
        self._assert_mutable()
        return tuple(self.intern_text(type_name) for type_name in type_names)

    def intern_expr(self, expression: TypeExpr) -> TypeId:
        self._assert_mutable()
        if not isinstance(expression, TypeExpr):
            raise TypeArenaError("intern_expr requires TypeExpr")
        try:
            validate_type_expr(expression)
        except GenericTypeSyntaxError as exc:
            raise TypeArenaError(f"invalid type expression: {exc}") from exc
        if not self.allow_unresolved and _contains_unresolved(expression):
            raise UnresolvedTypeError("unresolved type cannot enter a closed TypeArena")
        constructor = _normalize_constructor(expression.name)
        # Full shape validation and unresolved preflight happen before
        # recursion, so no rejected expression can leave partial child nodes.
        arguments = tuple(self.intern_expr(argument) for argument in expression.args)
        return self.intern_node(constructor, arguments)

    def intern_node(
        self,
        constructor: str,
        arguments: Iterable[TypeId] = (),
    ) -> TypeId:
        """Intern a node only after every contract check has passed.

        In particular, invalid arity, unknown generic constructors, unresolved
        policy, and missing arguments are all checked before ``_nodes`` changes.
        """
        self._assert_mutable()
        if not isinstance(constructor, str):
            raise TypeArenaError("type constructor must be text")
        normalized = _normalize_constructor(constructor)
        try:
            argument_values = tuple(arguments)
        except TypeError as exc:
            raise TypeArenaError("type arguments must be iterable") from exc
        _validate_constructor(normalized, len(argument_values))
        if normalized == "?" and not self.allow_unresolved:
            raise UnresolvedTypeError("unresolved type cannot enter a closed TypeArena")
        reference = TypeRef(normalized, argument_values)
        for argument in reference.arguments:
            if argument not in self._nodes:
                raise UnknownTypeIdError(
                    f"type argument is absent from arena: {argument.value}"
                )
        type_id = _identity(reference)
        existing = self._nodes.get(type_id)
        if existing is not None and existing != reference:
            raise TypeArenaError(f"TypeId collision: {type_id.value}")
        self._nodes[type_id] = reference
        return type_id

    def resolve(self, type_id: TypeId) -> TypeRef:
        if not isinstance(type_id, TypeId):
            raise TypeArenaError("resolve requires TypeId")
        try:
            return self._nodes[type_id]
        except KeyError as exc:
            raise UnknownTypeIdError(f"unknown TypeId: {type_id.value}") from exc

    def canonical(self, type_id: TypeId) -> str:
        reference = self.resolve(type_id)
        if not reference.arguments:
            return reference.constructor
        return (
            f"{reference.constructor}["
            + ",".join(self.canonical(argument) for argument in reference.arguments)
            + "]"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "contract": TYPE_ARENA_CONTRACT,
            "schema_version": TYPE_ARENA_SCHEMA_VERSION,
            "allow_unresolved": self.allow_unresolved,
            "entries": [
                {
                    "id": type_id.to_dict(),
                    "type": self._nodes[type_id].to_dict(),
                }
                for type_id in self.ids
            ],
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict()) + "\n"

    @classmethod
    def from_json(cls, value: str) -> "TypeArena":
        if not isinstance(value, str):
            raise TypeArenaSchemaError("type arena JSON must be text")
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            raise TypeArenaSchemaError("invalid type arena JSON") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, value: object) -> "TypeArena":
        raw = _mapping(
            value,
            {"contract", "schema_version", "allow_unresolved", "entries"},
            "TypeArena",
        )
        if raw["contract"] != TYPE_ARENA_CONTRACT:
            raise TypeArenaSchemaError("TypeArena contract mismatch")
        if (
            type(raw["schema_version"]) is not int
            or raw["schema_version"] != TYPE_ARENA_SCHEMA_VERSION
        ):
            raise TypeArenaSchemaError("TypeArena schema version mismatch")
        if not isinstance(raw["allow_unresolved"], bool):
            raise TypeArenaSchemaError("allow_unresolved must be boolean")
        entries = raw["entries"]
        if not isinstance(entries, list):
            raise TypeArenaSchemaError("TypeArena entries must be a list")

        arena = cls(allow_unresolved=raw["allow_unresolved"])
        pending: dict[TypeId, TypeRef] = {}
        for entry in entries:
            item = _mapping(entry, {"id", "type"}, "TypeArena entry")
            type_id = TypeId.from_dict(item["id"])
            serialized_type = _mapping(
                item["type"],
                {"contract", "constructor", "arguments"},
                "TypeRef",
            )
            if serialized_type["contract"] != TYPE_REF_CONTRACT:
                raise TypeArenaSchemaError("TypeRef contract mismatch")
            if not isinstance(serialized_type["arguments"], list):
                raise TypeArenaSchemaError("TypeRef arguments must be a list")
            serialized_constructor = _text(
                serialized_type["constructor"],
                "type constructor",
            )
            if serialized_constructor in _TYPE_ALIASES:
                raise TypeArenaSchemaError(
                    f"noncanonical type alias: {serialized_constructor}"
                )
            reference = TypeRef.from_dict(serialized_type)
            if type_id in pending:
                raise TypeArenaSchemaError(f"duplicate TypeId: {type_id.value}")
            if reference.constructor == "?" and not arena.allow_unresolved:
                raise UnresolvedTypeError(
                    "unresolved type is not allowed by serialized arena"
                )
            pending[type_id] = reference

        for type_id, reference in pending.items():
            for argument in reference.arguments:
                if argument not in pending:
                    raise UnknownTypeIdError(
                        f"TypeId {type_id.value} references unknown argument "
                        f"{argument.value}"
                    )

        visiting: set[TypeId] = set()
        visited: set[TypeId] = set()

        def validate(type_id: TypeId) -> None:
            if type_id in visited:
                return
            if type_id in visiting:
                raise TypeArenaSchemaError("cyclic TypeRef graph")
            visiting.add(type_id)
            reference = pending[type_id]
            for argument in reference.arguments:
                validate(argument)
            expected = _identity(reference)
            if expected != type_id:
                raise TypeArenaSchemaError(
                    f"TypeId/content mismatch for {type_id.value}"
                )
            visiting.remove(type_id)
            visited.add(type_id)

        for type_id in sorted(pending):
            validate(type_id)

        arena._nodes = pending
        for type_id in arena.ids:
            try:
                validate_type_expr(parse_type(arena.canonical(type_id)))
            except GenericTypeSyntaxError as exc:
                raise TypeArenaSchemaError(
                    f"invalid structural type {type_id.value}: {exc}"
                ) from exc
        return arena


def _validate_frozen_nodes(
    nodes: Mapping[TypeId, TypeRef],
    *,
    allow_unresolved: bool,
) -> dict[TypeId, TypeRef]:
    """Validate a closed structural graph before exposing an immutable view."""
    if not isinstance(nodes, Mapping):
        raise TypeArenaError("FrozenTypeArena nodes must be a mapping")
    normalized = dict(nodes)
    for type_id, reference in normalized.items():
        if not isinstance(type_id, TypeId):
            raise TypeArenaError("FrozenTypeArena keys must be TypeId")
        if not isinstance(reference, TypeRef):
            raise TypeArenaError("FrozenTypeArena values must be TypeRef")
        if reference.constructor == "?" and not allow_unresolved:
            raise UnresolvedTypeError(
                "unresolved type is not allowed by frozen TypeArena"
            )
        if _identity(reference) != type_id:
            raise TypeArenaSchemaError(
                f"TypeId/content mismatch for {type_id.value}"
            )
        for argument in reference.arguments:
            if argument not in normalized:
                raise UnknownTypeIdError(
                    f"TypeId {type_id.value} references unknown argument "
                    f"{argument.value}"
                )
    visiting: set[TypeId] = set()
    visited: set[TypeId] = set()

    def visit(type_id: TypeId) -> None:
        if type_id in visited:
            return
        if type_id in visiting:
            raise TypeArenaSchemaError("cyclic TypeRef graph")
        visiting.add(type_id)
        for argument in normalized[type_id].arguments:
            visit(argument)
        visiting.remove(type_id)
        visited.add(type_id)

    for type_id in sorted(normalized):
        visit(type_id)
    return normalized


class FrozenTypeArena:
    """Immutable post-build snapshot of a ``TypeArena``."""

    __slots__ = ("allow_unresolved", "_nodes", "_sealed")

    def __init__(
        self,
        nodes: Mapping[TypeId, TypeRef],
        *,
        allow_unresolved: bool = False,
    ) -> None:
        if type(allow_unresolved) is not bool:
            raise TypeArenaError("allow_unresolved must be boolean")
        normalized = _validate_frozen_nodes(
            nodes,
            allow_unresolved=allow_unresolved,
        )
        object.__setattr__(self, "allow_unresolved", allow_unresolved)
        object.__setattr__(self, "_nodes", MappingProxyType(normalized))
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise FrozenTypeArenaMutation("FrozenTypeArena is immutable")
        object.__setattr__(self, name, value)
    def __delattr__(self, name: str) -> None:
        if getattr(self, "_sealed", False):
            raise FrozenTypeArenaMutation("FrozenTypeArena is immutable")
        object.__delattr__(self, name)

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, type_id: object) -> bool:
        return isinstance(type_id, TypeId) and type_id in self._nodes

    def _reject_mutation(self) -> None:
        raise FrozenTypeArenaMutation("FrozenTypeArena cannot intern types")

    def intern_text(self, type_name: str) -> TypeId:
        self._reject_mutation()

    def intern_many(self, type_names: Iterable[str]) -> tuple[TypeId, ...]:
        self._reject_mutation()

    def intern_expr(self, expression: TypeExpr) -> TypeId:
        self._reject_mutation()

    def intern_node(
        self,
        constructor: str,
        arguments: Iterable[TypeId] = (),
    ) -> TypeId:
        self._reject_mutation()


    @property
    def ids(self) -> tuple[TypeId, ...]:
        return tuple(sorted(self._nodes))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def resolve(self, type_id: TypeId) -> TypeRef:
        if not isinstance(type_id, TypeId):
            raise TypeArenaError("resolve requires TypeId")
        try:
            return self._nodes[type_id]
        except KeyError as exc:
            raise UnknownTypeIdError(f"unknown TypeId: {type_id.value}") from exc
    def lookup_node(self, type_id: TypeId) -> TypeRef:
        """Return an already interned node without permitting construction."""
        return self.resolve(type_id)

    def identity(self, reference: TypeRef) -> TypeId:
        """Return an identity only when its exact node is already interned."""
        if not isinstance(reference, TypeRef):
            raise TypeArenaError("identity requires TypeRef")
        type_id = _identity(reference)
        if self._nodes.get(type_id) != reference:
            raise UnknownTypeIdError(f"unknown TypeId: {type_id.value}")
        return type_id

    def canonical(self, type_id: TypeId) -> str:
        reference = self.resolve(type_id)
        if not reference.arguments:
            return reference.constructor
        return (
            f"{reference.constructor}["
            + ",".join(self.canonical(argument) for argument in reference.arguments)
            + "]"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "contract": TYPE_ARENA_CONTRACT,
            "schema_version": TYPE_ARENA_SCHEMA_VERSION,
            "allow_unresolved": self.allow_unresolved,
            "entries": [
                {
                    "id": type_id.to_dict(),
                    "type": self._nodes[type_id].to_dict(),
                }
                for type_id in self.ids
            ],
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict()) + "\n"


@dataclass(frozen=True)
class TypeMember:
    """Stage-independent nominal member projection."""

    name: str
    type_id: TypeId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeArenaError("TypeMember name must be text")
        if self.type_id is not None and not isinstance(self.type_id, TypeId):
            raise TypeArenaError("TypeMember type_id must be TypeId")


@dataclass(frozen=True)
class TypeDeclaration:
    """Immutable nominal declaration projection keyed by its TypeId."""

    type_id: TypeId
    kind: str
    fields: tuple[TypeMember, ...] = ()
    variants: tuple[TypeMember, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.type_id, TypeId):
            raise TypeArenaError("TypeDeclaration type_id must be TypeId")
        if self.kind not in {"record", "enum"}:
            raise TypeArenaError("TypeDeclaration kind must be record or enum")
        fields = tuple(self.fields)
        variants = tuple(self.variants)
        if any(not isinstance(item, TypeMember) for item in (*fields, *variants)):
            raise TypeArenaError("TypeDeclaration members must be TypeMember")
        object.__setattr__(self, "fields", fields)
        object.__setattr__(self, "variants", variants)


class TypeContextBuilder:
    """One mutable compiler-local arena and declaration projection registry."""

    __slots__ = ("arena", "_declarations", "_frozen_context", "_type_ids")

    def __init__(self, *, allow_unresolved: bool = False) -> None:
        self.arena = TypeArena(allow_unresolved=allow_unresolved)
        self._declarations: dict[TypeId, TypeDeclaration] = {}
        self._frozen_context: TypeContext | None = None
        self._type_ids: dict[str, TypeId] = {}

    def _assert_mutable(self) -> None:
        if self._frozen_context is not None:
            raise FrozenTypeArenaMutation("TypeContextBuilder is frozen")

    def _remember(self, type_id: TypeId) -> TypeId:
        pending = [type_id]
        while pending:
            current = pending.pop()
            canonical = self.arena.canonical(current)
            if canonical in self._type_ids:
                continue
            self._type_ids[canonical] = current
            pending.extend(self.arena.resolve(current).arguments)
        return type_id

    def intern_text(self, type_name: str) -> TypeId:
        self._assert_mutable()
        return self._remember(self.arena.intern_text(type_name))

    def intern_many(self, type_names: Iterable[str]) -> tuple[TypeId, ...]:
        self._assert_mutable()
        return tuple(self.intern_text(type_name) for type_name in type_names)

    def intern_expr(self, expression: TypeExpr) -> TypeId:
        self._assert_mutable()
        return self._remember(self.arena.intern_expr(expression))

    def intern_node(
        self,
        constructor: str,
        arguments: Iterable[TypeId] = (),
    ) -> TypeId:
        self._assert_mutable()
        return self._remember(self.arena.intern_node(constructor, arguments))

    def register_declaration(self, declaration: TypeDeclaration) -> TypeId:
        self._assert_mutable()
        if not isinstance(declaration, TypeDeclaration):
            raise TypeArenaError(
                "TypeContextBuilder.register_declaration requires TypeDeclaration"
            )
        if declaration.type_id not in self.arena:
            raise UnknownTypeIdError(
                f"declaration identity is absent: {declaration.type_id.value}"
            )
        for member in (*declaration.fields, *declaration.variants):
            if member.type_id is not None and member.type_id not in self.arena:
                raise UnknownTypeIdError(
                    f"declaration member identity is absent: {member.type_id.value}"
                )
        existing = self._declarations.get(declaration.type_id)
        if existing is not None and existing != declaration:
            raise TypeArenaError(
                f"conflicting declaration: {declaration.type_id.value}"
            )
        self._declarations[declaration.type_id] = declaration
        return declaration.type_id

    def resolve(self, type_id: TypeId) -> TypeRef:
        return self.arena.resolve(type_id)

    def type_id(self, spelling: str) -> TypeId:
        if not isinstance(spelling, str):
            raise TypeArenaError("type_id requires canonical text")
        try:
            return self._type_ids[spelling]
        except KeyError as exc:
            raise UnknownTypeIdError(
                f"unknown canonical type spelling: {spelling}"
            ) from exc

    def render(self, type_id: TypeId) -> str:
        return self.arena.canonical(type_id)

    def canonical(self, type_id: TypeId) -> str:
        return self.render(type_id)

    def declaration(self, type_id: TypeId) -> TypeDeclaration:
        if not isinstance(type_id, TypeId):
            raise TypeArenaError("declaration requires TypeId")
        try:
            return self._declarations[type_id]
        except KeyError as exc:
            raise UnknownTypeIdError(
                f"unknown declaration TypeId: {type_id.value}"
            ) from exc

    @property
    def declarations(self) -> Mapping[TypeId, TypeDeclaration]:
        return MappingProxyType(dict(self._declarations))

    def freeze(self) -> TypeContext:
        if self._frozen_context is None:
            self._frozen_context = TypeContext(
                self.arena.freeze(),
                self._declarations,
            )
        return self._frozen_context


class TypeContext:
    """Immutable compiler-local authority for types and nominal declarations."""

    __slots__ = ("arena", "_declarations", "_type_ids", "_sealed")

    def __init__(
        self,
        arena: FrozenTypeArena,
        declarations: Mapping[TypeId, TypeDeclaration] | None = None,
    ) -> None:
        if not isinstance(arena, FrozenTypeArena):
            raise TypeArenaError("TypeContext requires a FrozenTypeArena")
        declaration_map = dict(declarations or {})
        for type_id, declaration in declaration_map.items():
            if not isinstance(type_id, TypeId):
                raise TypeArenaError("TypeContext declaration keys must be TypeId")
            if not isinstance(declaration, TypeDeclaration):
                raise TypeArenaError(
                    "TypeContext declarations must be TypeDeclaration"
                )
            if type_id != declaration.type_id or type_id not in arena:
                raise UnknownTypeIdError("TypeContext declaration identity is absent")
            for member in (*declaration.fields, *declaration.variants):
                if member.type_id is not None and member.type_id not in arena:
                    raise UnknownTypeIdError(
                        "TypeContext declaration member identity is absent"
                    )
        type_ids = {arena.canonical(type_id): type_id for type_id in arena.ids}
        object.__setattr__(self, "arena", arena)
        object.__setattr__(self, "_declarations", MappingProxyType(declaration_map))
        object.__setattr__(self, "_type_ids", MappingProxyType(type_ids))
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise FrozenTypeArenaMutation("TypeContext is immutable")
        object.__setattr__(self, name, value)
    def __delattr__(self, name: str) -> None:
        if getattr(self, "_sealed", False):
            raise FrozenTypeArenaMutation("TypeContext is immutable")
        object.__delattr__(self, name)

    @property
    def declarations(self) -> Mapping[TypeId, TypeDeclaration]:
        return self._declarations

    def resolve(self, type_id: TypeId) -> TypeRef:
        return self.arena.resolve(type_id)

    def type_id(self, spelling: str) -> TypeId:
        """Resolve one exact canonical spelling from the frozen arena."""
        if not isinstance(spelling, str):
            raise TypeArenaError("type_id requires canonical text")
        try:
            return self._type_ids[spelling]
        except KeyError as exc:
            raise UnknownTypeIdError(
                f"unknown canonical type spelling: {spelling}"
            ) from exc

    def canonical(self, type_id: TypeId) -> str:
        return self.render(type_id)

    def declaration(self, type_id: TypeId) -> object:
        if not isinstance(type_id, TypeId):
            raise TypeArenaError("declaration requires TypeId")
        try:
            return self._declarations[type_id]
        except KeyError as exc:
            raise UnknownTypeIdError(
                f"unknown declaration TypeId: {type_id.value}"
            ) from exc

    def render(self, type_id: TypeId) -> str:
        return self.arena.canonical(type_id)


__all__ = [
    "TYPE_ARENA_CONTRACT",
    "TYPE_ARENA_SCHEMA_VERSION",
    "TYPE_ID_CONTRACT",
    "TYPE_REF_CONTRACT",
    "FrozenTypeArena",
    "FrozenTypeArenaMutation",
    "TypeArena",
    "TypeArenaError",
    "TypeArenaSchemaError",
    "TypeContext",
    "TypeContextBuilder",
    "TypeDeclaration",
    "TypeId",
    "TypeMember",
    "TypeRef",
    "UnknownTypeIdError",
    "UnresolvedTypeError",
]
