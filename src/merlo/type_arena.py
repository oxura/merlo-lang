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
        try:
            expression = validate_type_expr(parse_type(type_name))
        except GenericTypeSyntaxError as exc:
            raise TypeArenaError(f"invalid type expression: {exc}") from exc
        return self.intern_expr(expression)

    def intern_many(self, type_names: Iterable[str]) -> tuple[TypeId, ...]:
        return tuple(self.intern_text(type_name) for type_name in type_names)

    def intern_expr(self, expression: TypeExpr) -> TypeId:
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


__all__ = [
    "TYPE_ARENA_CONTRACT",
    "TYPE_ARENA_SCHEMA_VERSION",
    "TYPE_ID_CONTRACT",
    "TYPE_REF_CONTRACT",
    "TypeArena",
    "TypeArenaError",
    "TypeArenaSchemaError",
    "TypeId",
    "TypeRef",
    "UnknownTypeIdError",
    "UnresolvedTypeError",
]
