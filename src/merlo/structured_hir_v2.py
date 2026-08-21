"""Structured Typed HIR v10 for Merlo's general representation milestone.

The HIR is deliberately a tree. Control-flow graphs, allocation primitives, drop
flags, and pointer arithmetic belong to lower layers.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from merlo import native_syntax as ast
from merlo.collection_protocol import (
    COLLECTION_OPERATIONS,
    collection_result_type,
    collection_shape,
)
from merlo.ffi import FFICompileError, FFIProgram, validate_ffi
from merlo.canonical_ast import (
    CanonicalFlowStep,
    CanonicalParallel,
    CanonicalProgram,
)
from merlo.type_parser import TypeExpr, generic_parts, parse_type, validate_type_expr
from merlo.type_arena import (
    FrozenTypeArena,
    TypeArena,
    TypeArenaError,
    TypeContext,
    TypeContextBuilder,
    TypeId,
)
from merlo.intrinsics import (
    CONTRACT_GRAPH,
    contextual_result_type,
    format_intrinsic_arity,
    intrinsic_signature,
)
from merlo.type_properties import TypePropertyResolver
from merlo.borrow_summary import (
    BorrowSummary,
    compute_borrow_summaries,
)
from merlo.ownership import (
    _OwnershipChecker,
    _assigned_parameter_names,
    _is_borrowed,
    _stable_id,
)


STRUCTURED_HIR_SCHEMA_VERSION = 10
STRUCTURED_HIR_CONTRACT = "merlo.structured-typed-hir.v10"
_SCALAR_TYPES = frozenset(
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
_INTEGER_TYPES = frozenset({"Byte", "Int8", "UInt8", "Int16", "UInt16", "Int32", "UInt32", "Int64", "UInt64"})
_LANGUAGE_NUMERIC_TYPES = frozenset(
    {"Byte", "Int64", "UInt64", "Float32", "Float64"}
)
_INTEGER_BINARY_OPERATORS = (
    ast.FloorDiv,
    ast.Mod,
    ast.BitOr,
    ast.BitAnd,
    ast.BitXor,
    ast.LShift,
    ast.RShift,
)
_TYPE_ALIASES = {"Int": "Int64", "UInt": "UInt64", "Float": "Float64"}

def _type_leaf(type_name: str) -> str:
    return type_name.rsplit("__", 1)[-1].rsplit(".", 1)[-1]


def _type_compatible(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    # A bare source-facing alias may denote a qualified imported declaration.
    return (
        _type_leaf(actual) == _type_leaf(expected)
        and (("__" in actual or "." in actual) != ("__" in expected or "." in expected))
    )


class StructuredHIRCompileError(ValueError):
    """Typed source/HIR construction failure."""


def _artifact_keys(
    value: object,
    expected: set[str],
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise ValueError(f"{label} must be an object")
    if set(value) != expected:
        raise ValueError(f"invalid {label} keys")
    return value


def _artifact_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    return value


def _artifact_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _artifact_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _artifact_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _artifact_optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _artifact_text(value, label)


def _freeze_json(value: object, label: str = "artifact value") -> Any:
    if isinstance(value, list):
        return tuple(_freeze_json(item, label) for item in value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"{label} keys must be text")
        return {key: _freeze_json(value[key], label) for key in sorted(value)}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ValueError(f"unsupported {label}: {type(value).__name__}")


def _json_payload(value: object) -> Any:
    if isinstance(value, (list, tuple)):
        return [_json_payload(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _json_payload(value[key]) for key in sorted(value)}
    return value


@dataclass(frozen=True)
class SourceSpan:
    path: str
    line: int
    column: int
    end_line: int
    end_column: int

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class HIRField:
    name: str
    type_name: str
    type_id: TypeId
    source: SourceSpan
    symbol_id: str
    revision_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type_name,
            "type_id": self.type_id.to_dict(),
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class HIRVariant:
    name: str
    payload_type: str | None
    payload_type_id: TypeId | None
    tag: int
    source: SourceSpan
    symbol_id: str
    revision_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "payload_type": self.payload_type,
            "payload_type_id": (
                self.payload_type_id.to_dict() if self.payload_type_id else None
            ),
            "tag": self.tag,
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class HIRInvariant:
    function_name: str
    expression: str
    source: SourceSpan
    revision_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "function_name": self.function_name,
            "expression": self.expression,
            "source": self.source.to_dict(),
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class HIRTypeDecl:
    name: str
    type_id: TypeId
    kind: str
    source: SourceSpan
    symbol_id: str
    revision_id: str
    fields: tuple[HIRField, ...] = ()
    variants: tuple[HIRVariant, ...] = ()
    invariants: tuple[HIRInvariant, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type_id": self.type_id.to_dict(),
            "kind": self.kind,
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "fields": [item.to_dict() for item in self.fields],
            "variants": [item.to_dict() for item in self.variants],
            "invariants": [item.to_dict() for item in self.invariants],
        }
def _type_declaration_projection(declaration: HIRTypeDecl):
    from merlo.type_arena import TypeDeclaration, TypeMember

    return TypeDeclaration(
        declaration.type_id,
        declaration.kind,
        fields=tuple(TypeMember(item.name, item.type_id) for item in declaration.fields),
        variants=tuple(
            TypeMember(item.name, item.payload_type_id)
            for item in declaration.variants
        ),
    )

@dataclass(frozen=True)
class HIRParameter:
    name: str
    type_name: str
    type_id: TypeId
    ownership: str
    source: SourceSpan
    symbol_id: str
    revision_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type_name,
            "type_id": self.type_id.to_dict(),
            "ownership": self.ownership,
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class HIRNode:
    id: str
    kind: str
    source: SourceSpan
    scope_id: str
    type_name: str | None
    type_id: TypeId | None
    ownership: str
    effects: tuple[str, ...]
    symbol_id: str | None
    revision_id: str
    attributes: tuple[tuple[str, Any], ...] = ()
    children: tuple["HIRNode", ...] = ()

    @property
    def attribute_map(self) -> dict[str, Any]:
        return dict(self.attributes)

    def walk(self) -> Iterable["HIRNode"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source.to_dict(),
            "scope_id": self.scope_id,
            "type": self.type_name,
            "type_id": self.type_id.to_dict() if self.type_id else None,
            "ownership": self.ownership,
            "effects": list(self.effects),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "attributes": _json_payload(dict(self.attributes)),
            "children": [item.to_dict() for item in self.children],
        }


@dataclass(frozen=True)
class HIRContract:
    kind: str
    expression: str
    condition: HIRNode
    source: SourceSpan

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "expression": self.expression,
            "condition": self.condition.to_dict(),
            "source": self.source.to_dict(),
        }


@dataclass(frozen=True)
class HIRFunction:
    name: str
    parameters: tuple[HIRParameter, ...]
    return_type: str
    return_type_id: TypeId
    effects: tuple[str, ...]
    requirements: tuple[HIRContract, ...]
    ensures: tuple[HIRContract, ...]
    body: tuple[HIRNode, ...]
    source: SourceSpan
    scope_id: str
    symbol_id: str
    revision_id: str
    borrow_summary: BorrowSummary = field(default_factory=BorrowSummary)

    def walk(self) -> Iterable[HIRNode]:
        for contract in (*self.requirements, *self.ensures):
            yield from contract.condition.walk()
        for node in self.body:
            yield from node.walk()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameters": [item.to_dict() for item in self.parameters],
            "return_type": self.return_type,
            "return_type_id": self.return_type_id.to_dict(),
            "effects": list(self.effects),
            "requirements": [item.to_dict() for item in self.requirements],
            "ensures": [item.to_dict() for item in self.ensures],
            "body": [item.to_dict() for item in self.body],
            "source": self.source.to_dict(),
            "scope_id": self.scope_id,
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "borrow_summary": self.borrow_summary.to_dict(),
        }


@dataclass(frozen=True)
class HIRFlow:
    name: str
    parameters: tuple[HIRParameter, ...]
    return_type: str
    return_type_id: TypeId
    durable: bool
    effects: tuple[str, ...]
    capabilities: tuple[str, ...]
    body: tuple[HIRNode, ...]
    source: SourceSpan
    symbol_id: str
    revision_id: str

    def walk(self) -> Iterable[HIRNode]:
        for node in self.body:
            yield from node.walk()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameters": [item.to_dict() for item in self.parameters],
            "return_type": self.return_type,
            "return_type_id": self.return_type_id.to_dict(),
            "durable": self.durable,
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
            "body": [item.to_dict() for item in self.body],
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class HIRMachineStateField:
    name: str
    type_name: str
    type_id: TypeId

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type_name,
            "type_id": self.type_id.to_dict(),
        }


@dataclass(frozen=True)
class HIRMachine:
    name: str
    parameters: tuple[HIRParameter, ...]
    states: tuple[tuple[str, tuple[HIRMachineStateField, ...]], ...]
    initial: str | None
    invariant: str | None
    transitions: tuple[HIRNode, ...]
    source: SourceSpan
    symbol_id: str
    revision_id: str

    def walk(self) -> Iterable[HIRNode]:
        for node in self.transitions:
            yield from node.walk()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameters": [item.to_dict() for item in self.parameters],
            "states": [
                {"name": name, "fields": [field.to_dict() for field in fields]}
                for name, fields in self.states
            ],
            "initial": self.initial,
            "invariant": self.invariant,
            "transitions": [item.to_dict() for item in self.transitions],
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
        }




def _source_span_from_dict(value: object) -> SourceSpan:
    raw = _artifact_keys(
        value,
        {"path", "line", "column", "end_line", "end_column"},
        "source span",
    )
    return SourceSpan(
        _artifact_text(raw["path"], "source path"),
        _artifact_int(raw["line"], "source line"),
        _artifact_int(raw["column"], "source column"),
        _artifact_int(raw["end_line"], "source end line"),
        _artifact_int(raw["end_column"], "source end column"),
    )


def _typed_pair(
    spelling: object,
    identity: object,
    label: str,
    arena: TypeArena,
    *,
    optional: bool = False,
    canonicalize: Callable[[str], str] | None = None,
) -> tuple[str | None, TypeId | None]:
    type_name = (
        _artifact_optional_text(spelling, label)
        if optional else _artifact_text(spelling, label)
    )
    if identity is None:
        type_id = None
    else:
        try:
            type_id = TypeId.from_dict(identity)
        except TypeArenaError as exc:
            raise ValueError(f"invalid {label} identity") from exc
    if (type_name is None) != (type_id is None):
        raise ValueError(f"{label} and {label} identity must be paired")
    if type_id is not None:
        try:
            canonical = arena.canonical(type_id)
        except TypeArenaError as exc:
            raise ValueError(f"unknown {label} identity") from exc
        try:
            comparison = canonicalize(type_name) if canonicalize else type_name
        except ValueError as exc:
            raise ValueError(f"{label} identity does not match spelling") from exc
        if canonical != comparison:
            raise ValueError(f"{label} identity does not match spelling")
    return type_name, type_id


def _field_from_dict(value: object, arena: TypeArena) -> HIRField:
    raw = _artifact_keys(value, {"name", "type", "type_id", "source", "symbol_id", "revision_id"}, "HIR field")
    type_name, type_id = _typed_pair(raw["type"], raw["type_id"], "field type", arena)
    assert type_name is not None and type_id is not None
    return HIRField(_artifact_text(raw["name"], "field name"), type_name, type_id, _source_span_from_dict(raw["source"]), _artifact_text(raw["symbol_id"], "field symbol"), _artifact_text(raw["revision_id"], "field revision"))


def _variant_from_dict(value: object, arena: TypeArena) -> HIRVariant:
    raw = _artifact_keys(value, {"name", "payload_type", "payload_type_id", "tag", "source", "symbol_id", "revision_id"}, "HIR variant")
    payload, payload_id = _typed_pair(raw["payload_type"], raw["payload_type_id"], "variant payload", arena, optional=True)
    return HIRVariant(_artifact_text(raw["name"], "variant name"), payload, payload_id, _artifact_int(raw["tag"], "variant tag"), _source_span_from_dict(raw["source"]), _artifact_text(raw["symbol_id"], "variant symbol"), _artifact_text(raw["revision_id"], "variant revision"))


def _invariant_from_dict(value: object) -> HIRInvariant:
    raw = _artifact_keys(value, {"function_name", "expression", "source", "revision_id"}, "HIR invariant")
    return HIRInvariant(_artifact_text(raw["function_name"], "invariant function"), _artifact_text(raw["expression"], "invariant expression"), _source_span_from_dict(raw["source"]), _artifact_text(raw["revision_id"], "invariant revision"))


def _type_decl_from_dict(value: object, arena: TypeArena) -> HIRTypeDecl:
    raw = _artifact_keys(value, {"name", "type_id", "kind", "source", "symbol_id", "revision_id", "fields", "variants", "invariants"}, "HIR type")
    name, type_id = _typed_pair(raw["name"], raw["type_id"], "type name", arena)
    assert name is not None and type_id is not None
    return HIRTypeDecl(name, type_id, _artifact_text(raw["kind"], "type kind"), _source_span_from_dict(raw["source"]), _artifact_text(raw["symbol_id"], "type symbol"), _artifact_text(raw["revision_id"], "type revision"), tuple(_field_from_dict(item, arena) for item in _artifact_list(raw["fields"], "type fields")), tuple(_variant_from_dict(item, arena) for item in _artifact_list(raw["variants"], "type variants")), tuple(_invariant_from_dict(item) for item in _artifact_list(raw["invariants"], "type invariants")))


def _parameter_from_dict(value: object, arena: TypeArena) -> HIRParameter:
    raw = _artifact_keys(value, {"name", "type", "type_id", "ownership", "source", "symbol_id", "revision_id"}, "HIR parameter")
    type_name, type_id = _typed_pair(raw["type"], raw["type_id"], "parameter type", arena)
    assert type_name is not None and type_id is not None
    return HIRParameter(_artifact_text(raw["name"], "parameter name"), type_name, type_id, _artifact_text(raw["ownership"], "parameter ownership"), _source_span_from_dict(raw["source"]), _artifact_text(raw["symbol_id"], "parameter symbol"), _artifact_text(raw["revision_id"], "parameter revision"))


def _node_from_dict(value: object, arena: TypeArena) -> HIRNode:
    raw = _artifact_keys(value, {"id", "kind", "source", "scope_id", "type", "type_id", "ownership", "effects", "symbol_id", "revision_id", "attributes", "children"}, "HIR node")
    attributes = raw["attributes"]
    if not isinstance(attributes, Mapping) or not all(isinstance(key, str) for key in attributes):
        raise ValueError("HIR node attributes must be a string-keyed object")
    type_name, type_id = _typed_pair(raw["type"], raw["type_id"], "node type", arena, optional=True)
    return HIRNode(_artifact_text(raw["id"], "node id"), _artifact_text(raw["kind"], "node kind"), _source_span_from_dict(raw["source"]), _artifact_text(raw["scope_id"], "node scope"), type_name, type_id, _artifact_text(raw["ownership"], "node ownership"), tuple(_artifact_text(item, "node effect") for item in _artifact_list(raw["effects"], "node effects")), _artifact_optional_text(raw["symbol_id"], "node symbol"), _artifact_text(raw["revision_id"], "node revision"), tuple((key, _freeze_json(attributes[key], "HIR attribute")) for key in sorted(attributes)), tuple(_node_from_dict(item, arena) for item in _artifact_list(raw["children"], "node children")))


def _contract_from_dict(value: object, arena: TypeArena) -> HIRContract:
    raw = _artifact_keys(value, {"kind", "expression", "condition", "source"}, "HIR contract")
    return HIRContract(_artifact_text(raw["kind"], "contract kind"), _artifact_text(raw["expression"], "contract expression"), _node_from_dict(raw["condition"], arena), _source_span_from_dict(raw["source"]))


def _function_from_dict(value: object, arena: TypeArena) -> HIRFunction:
    raw = _artifact_keys(value, {"name", "parameters", "return_type", "return_type_id", "effects", "requirements", "ensures", "body", "source", "scope_id", "symbol_id", "revision_id", "borrow_summary"}, "HIR function")
    return_name, return_id = _typed_pair(raw["return_type"], raw["return_type_id"], "function return type", arena)
    assert return_name is not None and return_id is not None
    return HIRFunction(_artifact_text(raw["name"], "function name"), tuple(_parameter_from_dict(item, arena) for item in _artifact_list(raw["parameters"], "function parameters")), return_name, return_id, tuple(_artifact_text(item, "function effect") for item in _artifact_list(raw["effects"], "function effects")), tuple(_contract_from_dict(item, arena) for item in _artifact_list(raw["requirements"], "requirements")), tuple(_contract_from_dict(item, arena) for item in _artifact_list(raw["ensures"], "ensures")), tuple(_node_from_dict(item, arena) for item in _artifact_list(raw["body"], "function body")), _source_span_from_dict(raw["source"]), _artifact_text(raw["scope_id"], "function scope"), _artifact_text(raw["symbol_id"], "function symbol"), _artifact_text(raw["revision_id"], "function revision"), BorrowSummary.from_dict(_artifact_keys(raw["borrow_summary"], {"schema_version", "contract", "status", "reason", "entries"}, "borrow summary")))


def _flow_from_dict(value: object, arena: TypeArena) -> HIRFlow:
    raw = _artifact_keys(value, {"name", "parameters", "return_type", "return_type_id", "durable", "effects", "capabilities", "body", "source", "symbol_id", "revision_id"}, "HIR flow")
    return_name, return_id = _typed_pair(raw["return_type"], raw["return_type_id"], "flow return type", arena)
    assert return_name is not None and return_id is not None
    return HIRFlow(_artifact_text(raw["name"], "flow name"), tuple(_parameter_from_dict(item, arena) for item in _artifact_list(raw["parameters"], "flow parameters")), return_name, return_id, _artifact_bool(raw["durable"], "flow durable"), tuple(_artifact_text(item, "flow effect") for item in _artifact_list(raw["effects"], "flow effects")), tuple(_artifact_text(item, "flow capability") for item in _artifact_list(raw["capabilities"], "flow capabilities")), tuple(_node_from_dict(item, arena) for item in _artifact_list(raw["body"], "flow body")), _source_span_from_dict(raw["source"]), _artifact_text(raw["symbol_id"], "flow symbol"), _artifact_text(raw["revision_id"], "flow revision"))


def _machine_from_dict(value: object, arena: TypeArena) -> HIRMachine:
    raw = _artifact_keys(value, {"name", "parameters", "states", "initial", "invariant", "transitions", "source", "symbol_id", "revision_id"}, "HIR machine")
    states = []
    for item in _artifact_list(raw["states"], "machine states"):
        state = _artifact_keys(item, {"name", "fields"}, "machine state")
        fields = []
        for field_value in _artifact_list(state["fields"], "machine state fields"):
            field = _artifact_keys(field_value, {"name", "type", "type_id"}, "machine state field")
            field_name, field_id = _typed_pair(field["type"], field["type_id"], "machine field type", arena)
            assert field_name is not None and field_id is not None
            fields.append(HIRMachineStateField(_artifact_text(field["name"], "machine field name"), field_name, field_id))
        states.append((_artifact_text(state["name"], "state name"), tuple(fields)))
    return HIRMachine(_artifact_text(raw["name"], "machine name"), tuple(_parameter_from_dict(item, arena) for item in _artifact_list(raw["parameters"], "machine parameters")), tuple(states), _artifact_optional_text(raw["initial"], "machine initial state"), _artifact_optional_text(raw["invariant"], "machine invariant"), tuple(_node_from_dict(item, arena) for item in _artifact_list(raw["transitions"], "machine transitions")), _source_span_from_dict(raw["source"]), _artifact_text(raw["symbol_id"], "machine symbol"), _artifact_text(raw["revision_id"], "machine revision"))

def _ffi_hir_dict(ffi_program: FFIProgram, arena: TypeArena) -> dict[str, Any]:
    payload = json.loads(ffi_program.to_json())
    canonical_ids = {arena.canonical(type_id): type_id for type_id in arena.ids}

    def lookup(spelling: str, label: str) -> TypeId:
        try:
            normalized = _normalize_ffi_type(spelling)
            return canonical_ids[normalized]
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{label} is absent or noncanonical") from exc

    for extern in payload["extern_functions"]:
        for parameter in extern["parameters"]:
            parameter["type_id"] = lookup(parameter["type"], "FFI parameter type").to_dict()
        extern["return_type_id"] = lookup(extern["return_type"], "FFI return type").to_dict()
        error_type = extern["error_type"]
        extern["error_type_id"] = (
            lookup(error_type, "FFI error type").to_dict()
            if error_type is not None else None
        )
    for record in payload["repr_c_records"]:
        for ffi_field in record["fields"]:
            ffi_field["type_id"] = lookup(ffi_field["type_name"], "FFI repr(C) field type").to_dict()
    return payload


def _ffi_without_hir_ids(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(value))
    for extern in payload["extern_functions"]:
        for parameter in extern["parameters"]:
            parameter.pop("type_id", None)
        extern.pop("return_type_id", None)
        extern.pop("error_type_id", None)
    for record in payload["repr_c_records"]:
        for ffi_field in record["fields"]:
            ffi_field.pop("type_id", None)
    return payload


@dataclass(frozen=True)
class StructuredHIRProgram:
    source: str
    path: str
    source_sha256: str
    types: tuple[HIRTypeDecl, ...]
    functions: tuple[HIRFunction, ...]
    entry_function: str
    native_syntax_json: str
    ffi_program: FFIProgram
    type_context: TypeContext
    type_arena_digest: str
    schema_version: int = STRUCTURED_HIR_SCHEMA_VERSION
    contract: str = STRUCTURED_HIR_CONTRACT
    native_module: ast.Module | None = field(default=None, repr=False, compare=False)
    flows: tuple[HIRFlow, ...] = ()
    machines: tuple[HIRMachine, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != STRUCTURED_HIR_SCHEMA_VERSION:
            raise ValueError("Structured HIR schema version drift")
        if self.contract != STRUCTURED_HIR_CONTRACT:
            raise ValueError("Structured HIR contract drift")
        if not isinstance(self.type_context, TypeContext):
            raise ValueError("Structured HIR requires a TypeContext")
        if self.type_context.arena.allow_unresolved:
            raise ValueError("Structured HIR requires a closed TypeArena")
        if self.type_arena_digest != self.type_context.arena.digest:
            raise ValueError("Structured HIR type arena digest mismatch")

        def check_type(spelling: str | None, type_id: TypeId | None, label: str) -> None:
            if (spelling is None) != (type_id is None):
                raise ValueError(f"{label} and {label} identity must be paired")
            if type_id is None:
                return
            if not isinstance(type_id, TypeId):
                raise ValueError(f"{label} identity must be TypeId")
            try:
                canonical = self.type_context.render(type_id)
            except TypeArenaError as exc:
                raise ValueError(f"unknown {label} identity") from exc
            if canonical != spelling:
                raise ValueError(f"{label} identity does not match spelling")
        for declaration in self.types:
            check_type(declaration.name, declaration.type_id, "type name")
            for item in declaration.fields:
                check_type(item.type_name, item.type_id, "field type")
            for item in declaration.variants:
                check_type(item.payload_type, item.payload_type_id, "variant payload")
            try:
                context_declaration = self.type_context.declaration(declaration.type_id)
            except TypeArenaError as exc:
                raise ValueError("unknown type declaration identity") from exc
            if context_declaration != _type_declaration_projection(declaration):
                raise ValueError("TypeContext declaration mismatch")
        for owner in (*self.functions, *self.flows, *self.machines):
            for parameter in owner.parameters:
                check_type(parameter.type_name, parameter.type_id, "parameter type")
            if isinstance(owner, (HIRFunction, HIRFlow)):
                check_type(owner.return_type, owner.return_type_id, "return type")
            for node in owner.walk():
                check_type(node.type_name, node.type_id, "node type")
            if isinstance(owner, HIRMachine):
                for _, fields in owner.states:
                    for item in fields:
                        check_type(item.type_name, item.type_id, "machine field type")
        if hashlib.sha256(self.source.encode()).hexdigest() != self.source_sha256:
            raise ValueError("Structured HIR source digest mismatch")
        try:
            artifact_module = ast.module_from_json(self.native_syntax_json)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid Structured HIR native syntax artifact") from exc
        if self.native_module is not None and (
            ast.module_to_json(self.native_module) != self.native_syntax_json
        ):
            raise ValueError("Structured HIR hidden native module mismatch")
        if not isinstance(self.ffi_program, FFIProgram):
            raise ValueError("invalid Structured HIR FFI artifact")
        type_names = [item.name for item in self.types]
        function_names = [item.name for item in self.functions]
        if len(type_names) != len(set(type_names)):
            raise ValueError("duplicate Structured HIR type")
        if len(function_names) != len(set(function_names)):
            raise ValueError("duplicate Structured HIR function")
        for function in self.functions:
            if any(
                entry.relation.source_parameter_index >= len(function.parameters)
                for entry in function.borrow_summary.entries
            ):
                raise ValueError(
                    f"borrow summary parameter index out of range: {function.name}"
                )
        if self.entry_function not in set(function_names) and not (self.flows or self.machines):
            raise ValueError("missing Structured HIR entry function")
        node_ids = [
            node.id
            for owner in (*self.functions, *self.flows, *self.machines)
            for node in owner.walk()
        ]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("duplicate Structured HIR node id")
        forbidden = {"BasicBlock", "Goto", "Malloc", "Free", "DropFlag", "RawPointer"}
        actual = {
            node.kind
            for owner in (*self.functions, *self.flows, *self.machines)
            for node in owner.walk()
        }
        if actual & forbidden:
            raise ValueError("CFG or raw-memory detail escaped into Structured HIR")
        artifact_functions = [
            item.name
            for item in artifact_module.body
            if isinstance(item, ast.FunctionDef)
        ]
        if artifact_functions != function_names:
            raise ValueError("Structured HIR/native syntax function mismatch")

    @property
    def digest(self) -> str:
        payload = self.to_dict()
        for function in payload["functions"]:
            for entry in function["borrow_summary"]["entries"]:
                entry["witness_path"] = []
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode()).hexdigest()

    def type_decl(self, name: str) -> HIRTypeDecl:
        return next(item for item in self.types if item.name == name)

    def function(self, name: str) -> HIRFunction:
        return next(item for item in self.functions if item.name == name)

    def backend_module(self) -> ast.Module:
        """Restore backend syntax only from the digest-bound artifact.

        A retained in-memory module is accepted solely as a consistency witness;
        it is never the object consumed by code generation.
        """
        if self.native_module is not None and (
            ast.module_to_json(self.native_module) != self.native_syntax_json
        ):
            raise ValueError("Structured HIR hidden native module mismatch")
        return ast.module_from_json(self.native_syntax_json)

    def to_dict(self) -> dict[str, Any]:
        if self.type_context.arena.digest != self.type_arena_digest:
            raise ValueError("Structured HIR type arena digest mismatch")
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "path": self.path,
            "source": self.source,
            "source_sha256": self.source_sha256,
            "entry_function": self.entry_function,
            "type_arena": self.type_context.arena.to_dict(),
            "type_arena_digest": self.type_arena_digest,
            "native_syntax": json.loads(self.native_syntax_json),
            "ffi": _ffi_hir_dict(self.ffi_program, self.type_context.arena),
            "types": [item.to_dict() for item in self.types],
            "functions": [item.to_dict() for item in self.functions],
            "flows": [item.to_dict() for item in self.flows],
            "machines": [item.to_dict() for item in self.machines],
            "invariants": {
                "structured_program_tree": True,
                "cfg_absent": True,
                "raw_memory_absent": True,
                "stable_symbol_ids": True,
                "stable_revision_ids": True,
                "source_scopes": True,
                "source_mappings": True,
                "ownership_modes": True,
                "effect_sets": True,
                "borrow_summaries": True,
            },
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StructuredHIRProgram":
        invariants = {
            "structured_program_tree": True,
            "cfg_absent": True,
            "raw_memory_absent": True,
            "stable_symbol_ids": True,
            "stable_revision_ids": True,
            "source_scopes": True,
            "source_mappings": True,
            "ownership_modes": True,
            "effect_sets": True,
            "borrow_summaries": True,
        }
        raw = _artifact_keys(
            value,
            {
                "schema_version", "contract", "path", "source", "source_sha256",
                "entry_function", "type_arena", "type_arena_digest",
                "native_syntax", "ffi", "types", "functions", "flows", "machines",
                "invariants",
            },
            "Structured HIR",
        )
        if raw["schema_version"] != STRUCTURED_HIR_SCHEMA_VERSION:
            raise ValueError("Structured HIR schema version drift")
        if raw["contract"] != STRUCTURED_HIR_CONTRACT:
            raise ValueError("Structured HIR contract drift")
        if raw["invariants"] != invariants:
            raise ValueError("Structured HIR invariants drift")
        try:
            restored_arena = TypeArena.from_dict(raw["type_arena"])
        except TypeArenaError as exc:
            raise ValueError("invalid Structured HIR type arena") from exc
        if restored_arena.allow_unresolved:
            raise ValueError("Structured HIR type arena must be closed")
        arena_digest = _artifact_text(raw["type_arena_digest"], "type arena digest")
        if restored_arena.digest != arena_digest:
            raise ValueError("Structured HIR type arena digest mismatch")
        arena = restored_arena.freeze()

        ffi = _artifact_keys(
            raw["ffi"],
            {"abi", "extern_functions", "repr_c_records", "unsafe_operations"},
            "FFI artifact",
        )
        for extern in _artifact_list(ffi["extern_functions"], "FFI extern functions"):
            extern_raw = _artifact_keys(
                extern,
                {
                    "name", "abi", "parameters", "return_type", "effects",
                    "error_type", "safe_wrapper", "source", "prototype",
                    "return_type_id", "error_type_id",
                },
                "HIR FFI extern",
            )
            for parameter in _artifact_list(extern_raw["parameters"], "FFI parameters"):
                parameter_raw = _artifact_keys(
                    parameter,
                    {"name", "type", "pointer", "policy", "type_id"},
                    "HIR FFI parameter",
                )
                _typed_pair(
                    parameter_raw["type"],
                    parameter_raw["type_id"],
                    "FFI parameter type",
                    arena,
                    canonicalize=_normalize_ffi_type,
                )
            _typed_pair(
                extern_raw["return_type"],
                extern_raw["return_type_id"],
                "FFI return type",
                arena,
                canonicalize=_normalize_ffi_type,
            )
            _typed_pair(
                extern_raw["error_type"],
                extern_raw["error_type_id"],
                "FFI error type",
                arena,
                optional=True,
                canonicalize=_normalize_ffi_type,
            )
        for record in _artifact_list(ffi["repr_c_records"], "FFI repr(C) records"):
            record_raw = _artifact_keys(record, {"name", "abi", "fields", "size", "alignment"}, "HIR FFI record")
            for ffi_field in _artifact_list(record_raw["fields"], "FFI repr(C) fields"):
                field_raw = _artifact_keys(ffi_field, {"name", "type_name", "offset", "size", "alignment", "type_id"}, "HIR FFI field")
                _typed_pair(
                    field_raw["type_name"],
                    field_raw["type_id"],
                    "FFI repr(C) field type",
                    arena,
                    canonicalize=_normalize_ffi_type,
                )
        ffi_program = FFIProgram.from_dict(_ffi_without_hir_ids(ffi))

        native = _artifact_keys(raw["native_syntax"], {"schema_version", "contract", "module"}, "native syntax artifact")
        native_module = ast.module_from_dict(native)
        native_json = ast.module_to_json(native_module)
        types = tuple(
            _type_decl_from_dict(item, arena)
            for item in _artifact_list(raw["types"], "HIR types")
        )
        functions = tuple(
            _function_from_dict(item, arena)
            for item in _artifact_list(raw["functions"], "HIR functions")
        )
        context = TypeContext(
            arena,
            {
                item.type_id: _type_declaration_projection(item)
                for item in types
            },
        )
        program = cls(
            _artifact_text(raw["source"], "Structured HIR source"),
            _artifact_text(raw["path"], "Structured HIR path"),
            _artifact_text(raw["source_sha256"], "Structured HIR source digest"),
            types,
            functions,
            _artifact_text(raw["entry_function"], "HIR entry function"),
            native_json,
            ffi_program,
            context,
            arena_digest,
            flows=tuple(_flow_from_dict(item, arena) for item in _artifact_list(raw["flows"], "HIR flows")),
            machines=tuple(_machine_from_dict(item, arena) for item in _artifact_list(raw["machines"], "HIR machines")),
        )
        if program.to_dict() != dict(value):
            raise ValueError("non-canonical Structured HIR artifact")
        return program

    @classmethod
    def from_json(cls, payload: str) -> "StructuredHIRProgram":
        def decode_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError(f"duplicate Structured HIR JSON key: {key}")
                result[key] = item
            return result

        try:
            value = json.loads(payload, object_pairs_hook=decode_object)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid Structured HIR JSON") from exc
        if not isinstance(value, Mapping):
            raise ValueError("Structured HIR JSON root must be an object")
        return cls.from_dict(value)


@dataclass(frozen=True)
class _Preprocessed:
    source: str
    declaration_kinds: dict[str, str]
    binding_kinds: dict[int, str]


def _span(path: str, node: ast.AST) -> SourceSpan:
    return SourceSpan(
        str(getattr(node, "_merlo_path", path)),
        int(getattr(node, "lineno", 1)),
        int(getattr(node, "col_offset", 0)) + 1,
        int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
        int(getattr(node, "end_col_offset", getattr(node, "col_offset", 0))) + 1,
    )


def _ast_qualified_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _ast_qualified_name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return ""
def _ast_pattern_name(pattern: ast.pattern) -> str:
    if isinstance(pattern, ast.MatchValue):
        return _ast_qualified_name(pattern.value)
    if isinstance(pattern, ast.MatchClass):
        return _ast_qualified_name(pattern.cls)
    if isinstance(pattern, ast.MatchAs):
        return pattern.name or "_"
    if isinstance(pattern, ast.MatchSingleton):
        return "None" if pattern.value is None else str(pattern.value)
    return ""
def _type_name(node: ast.AST | None) -> str:
    if node is None:
        return "Unit"

    def render(item: ast.AST) -> str:
        if isinstance(item, ast.Name):
            return item.id
        if isinstance(item, ast.Attribute):
            owner = render(item.value)
            return f"{owner}.{item.attr}"
        if isinstance(item, ast.Constant) and isinstance(item.value, int):
            return str(item.value)
        if isinstance(item, ast.Subscript):
            parts = (
                item.slice.elts
                if isinstance(item.slice, ast.Tuple)
                else (item.slice,)
            )
            return (
                f"{render(item.value)}["
                f"{','.join(render(part) for part in parts)}]"
            )
        raise StructuredHIRCompileError(
            f"MalformedType: unsupported AST node {type(item).__name__}"
        )

    type_name = render(node)
    for alias, canonical in _TYPE_ALIASES.items():
        type_name = re.sub(rf"\b{alias}\b", canonical, type_name)
    try:
        return validate_type_expr(parse_type(type_name)).canonical
    except ValueError as error:
        raise StructuredHIRCompileError(f"MalformedType: {type_name}") from error

_DEFAULT_MAP = "Map[Text,UInt64]"


def _map_types(type_name: str | None) -> tuple[str, str] | None:
    parts = generic_parts(type_name, "Map", arity=2)
    return parts if parts is not None else None  # type: ignore[return-value]


def _sum_variants(type_name: str | None) -> dict[str, str | None] | None:
    option = generic_parts(type_name, "Option", arity=1)
    if option is not None:
        return {"NoneValue": None, "Some": option[0]}
    result = generic_parts(type_name, "Result", arity=2)
    if result is not None:
        return {"Ok": result[0], "Err": result[1]}
    return None


def _callback_parts(type_name: str) -> tuple[tuple[str, ...], str] | None:
    parts = generic_parts(type_name, "Fn")
    if parts is None or len(parts) < 2:
        return None
    return parts[:-1], parts[-1]


def _function_callback_type(function: ast.FunctionDef) -> str:
    parameters = [_type_name(item.annotation) for item in function.args.args]
    return "Fn[" + ",".join((*parameters, _type_name(function.returns))) + "]"


def _validate_map_specializations(module: ast.Module, path: str) -> None:
    layout_annotations = {
        id(statement.annotation)
        for declaration in module.body
        if isinstance(declaration, ast.ClassDef)
        for statement in declaration.body
        if isinstance(statement, ast.AnnAssign) and statement.annotation is not None
    }

    def validate(annotation: ast.AST, *, allow_layout_map: bool = False) -> None:
        if isinstance(annotation, ast.Subscript):
            if isinstance(annotation.value, ast.Name) and annotation.value.id == "Map":
                specialization = _type_name(annotation)
                map_types = _map_types(specialization)
                if (
                    map_types is None
                    or map_types[0] != "Text"
                    or (
                        not allow_layout_map
                        and map_types[1] not in _SCALAR_TYPES
                    )
                ):
                    raise StructuredHIRCompileError(
                        f"{path}:{getattr(annotation, 'lineno', 1)}: unsupported Map "
                        f"specialization {specialization}; alpha Map requires "
                        "Text keys and scalar values outside nominal layout fields"
                    )
                validate(annotation.slice, allow_layout_map=allow_layout_map)
                return
            for child in ast.iter_child_nodes(annotation):
                validate(child, allow_layout_map=allow_layout_map)
            return
        if isinstance(annotation, ast.Name):
            if annotation.id == "Any":
                raise StructuredHIRCompileError(
                    f"{path}:{getattr(annotation, 'lineno', 1)}: DynamicAnyForbidden"
                )
            if annotation.id == "Map":
                raise StructuredHIRCompileError(
                    f"{path}:{getattr(annotation, 'lineno', 1)}: unsupported Map; "
                    "alpha Map requires a Text key and concrete value type"
                )
            return
        for child in ast.iter_child_nodes(annotation):
            validate(child, allow_layout_map=allow_layout_map)

    for node in ast.walk(module):
        annotation: ast.AST | None = None
        if isinstance(node, ast.AnnAssign):
            annotation = node.annotation
        elif isinstance(node, ast.arg):
            annotation = node.annotation
        elif isinstance(node, ast.FunctionDef):
            annotation = node.returns
        if annotation is not None:
            validate(annotation, allow_layout_map=id(annotation) in layout_annotations)



def _rewrite_postfix_try(line: str) -> str:
    """Preserve postfix ``?`` as an explicit marker before Python parsing."""
    cursor = 0
    quote: str | None = None
    while cursor < len(line):
        character = line[cursor]
        if quote is not None:
            if character == "\\":
                cursor += 2
                continue
            if character == quote:
                quote = None
            cursor += 1
            continue
        if character in {'"', "'"}:
            quote = character
            cursor += 1
            continue
        if character != "?" or (cursor > 0 and line[cursor - 1] == "?") or (
            cursor + 1 < len(line) and line[cursor + 1] == "?"
        ):
            cursor += 1
            continue
        end = cursor
        start = end - 1
        while start >= 0 and line[start].isspace():
            start -= 1
        if start < 0:
            return line
        if line[start] == ")":
            depth = 1
            start -= 1
            while start >= 0 and depth:
                if line[start] == ")":
                    depth += 1
                elif line[start] == "(":
                    depth -= 1
                start -= 1
            if depth:
                return line
            start += 1
            while start > 0 and (line[start - 1].isalnum() or line[start - 1] in "_."):
                start -= 1
        elif line[start].isalnum() or line[start] == "_":
            while start > 0 and (line[start - 1].isalnum() or line[start - 1] in "_."):
                start -= 1
        else:
            return line
        expression = line[start:end].rstrip()
        line = f"{line[:start]}__merlo_try__({expression}){line[end + 1:]}"
        cursor = start + len("__merlo_try__(") + len(expression) + 1
    return line


def _preprocess(source: str) -> _Preprocessed:
    declaration_kinds: dict[str, str] = {}
    binding_kinds: dict[int, str] = {}
    output = []
    for line_number, line in enumerate(source.splitlines(), 1):
        declaration = re.match(r"^(\s*)(record|enum)\s+([A-Za-z_]\w*)\s*:\s*$", line)
        if declaration:
            indent, kind, name = declaration.groups()
            declaration_kinds[name] = kind
            output.append(f"{indent}class {name}:")
            continue
        constant = re.match(r"^(\s*)const\s+", line)
        if constant:
            line = re.sub(r"^(\s*)const\s+", r"\1", line)
        function = re.match(r"^(\s*)(fn|task)\s+", line)
        if function:
            line = re.sub(r"^(\s*)(?:fn|task)\s+", r"\1def ", line)
        binding = re.match(r"^(\s*)(let|var)\s+", line)
        if binding:
            binding_kinds[line_number] = binding.group(2)
            line = re.sub(r"^(\s*)(?:let|var)\s+", r"\1", line)
        if re.fullmatch(r"\s*uses\s+.+", line):
            line = ""
        line = re.sub(r"\bOption\.None\b", "Option.NoneValue", line)
        line = _rewrite_postfix_try(line)
        line = re.sub(r"\btrue\b", "True", line)
        line = re.sub(r"\bfalse\b", "False", line)
        if re.search(r"\b(?:and|or)\s*$", line):
            line += " " + chr(92)
        output.append(line)
    return _Preprocessed("\n".join(output) + "\n", declaration_kinds, binding_kinds)

def _preprocess_ffi_surface(source: str) -> str:
    """Erase declarations parsed by :mod:`merlo.ffi` before legacy syntax parsing."""
    output: list[str] = []
    extern_block = False
    for line in source.splitlines():
        stripped = line.strip()
        indent = line[: len(line) - len(line.lstrip())]
        if re.match(r'^extern\s*(?:"C"|C)\s*[{:]?\s*$', stripped):
            extern_block = True
            output.append(f"{indent}# extern C")
            continue
        if extern_block:
            if stripped in {"}", "};"}:
                extern_block = False
            output.append(f"{indent}# extern declaration")
            continue
        if re.match(r'^extern\s*(?:"C"|C)\s*(?:fn\s+)?[A-Za-z_]\w*\s*\(', stripped):
            output.append(f"{indent}# extern declaration")
            continue
        if re.match(r"^(?:@repr\(C\)\s+|repr\(C\)\s+)(record|enum)\s+", stripped):
            output.append(re.sub(r"^(?P<i>\s*)(?:@repr\(C\)\s+|repr\(C\)\s+)", r"\g<i>", line))
            continue
        if re.match(r"^\s*unsafe\s*:\s*$", line):
            output.append(re.sub(r"unsafe\s*:", "if __merlo_unsafe_scope__:", line))
            continue
        output.append(line)
    return "\n".join(output) + ("\n" if source.endswith("\n") else "")


def _normalize_ffi_type(type_name: str) -> str:
    """Normalize FFI aliases and pointer constructors to arena syntax."""
    try:
        expression = parse_type(type_name)
    except ValueError as exc:
        raise ValueError(f"invalid FFI type {type_name}") from exc

    aliases = {"Int": "Int64", "UInt": "UInt64", "Float": "Float64"}
    pointers = {"Ptr", "ConstPointer", "MutPointer"}

    def normalize(item: TypeExpr) -> TypeExpr:
        constructor = aliases.get(item.name, item.name)
        if constructor in pointers:
            constructor = "RawPointer"
        return TypeExpr(
            constructor,
            tuple(normalize(argument) for argument in item.args),
        )

    normalized = normalize(expression)
    try:
        validate_type_expr(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid FFI type {type_name}") from exc
    return normalized.canonical


def _intern_cached_type(
    type_arena: TypeArena,
    cache: dict[str, tuple[str, TypeId]],
    type_name: str | None,
    path: str,
) -> tuple[str, TypeId] | None:
    if type_name is None:
        return None
    cached = cache.get(type_name)
    if cached is not None:
        return cached
    try:
        type_id = type_arena.intern_text(type_name)
        canonical = type_arena.canonical(type_id)
    except TypeArenaError as exc:
        raise StructuredHIRCompileError(
            f"{path}: invalid HIR type {type_name}: {exc}"
        ) from exc
    result = (canonical, type_id)
    cache[type_name] = result
    cache.setdefault(canonical, result)
    return result


class _HIRBuilder:
    def __init__(
        self,
        path: str,
        source: str,
        preprocessed: _Preprocessed,
        types: dict[str, HIRTypeDecl],
        functions: dict[str, ast.FunctionDef],
        ffi_program: FFIProgram | None = None,
        borrow_summaries: dict[str, BorrowSummary] | None = None,
        type_arena: TypeArena | None = None,
        type_cache: dict[str, tuple[str, TypeId]] | None = None,
    ) -> None:
        self.path = path
        self.source = source
        self.preprocessed = preprocessed
        self.types = types
        self.functions = functions
        self.ffi_program = ffi_program or FFIProgram()
        self.borrow_summaries = borrow_summaries or {}
        self.type_arena = (
            type_arena
            if type_arena is not None
            else TypeContextBuilder(allow_unresolved=False)
        )
        self._type_cache = (
            type_cache if type_cache is not None else {}
        )
        self.extern_functions = {item.name: item for item in self.ffi_program.extern_functions}
        self.function_symbols = {
            name: _stable_id("shirs", path, "function", name) for name in functions
        }
        self.mutable_parameters = self._mutable_parameter_table(functions)
        self.local_types: dict[str, str] = {}
        self.current_function = ""
        self.ordinal = 0
        self.type_properties = TypePropertyResolver(self.type_arena)

    def _intern_type(self, type_name: str | None) -> tuple[str, TypeId] | None:
        return _intern_cached_type(
            self.type_arena,
            self._type_cache,
            type_name,
            self.path,
        )

    def _type_id(self, type_name: str | None) -> TypeId | None:
        item = self._intern_type(type_name)
        return item[1] if item is not None else None

    def _canonical_type(self, type_name: str | None) -> str | None:
        item = self._intern_type(type_name)
        return item[0] if item is not None else None
    def _owner(self, type_name: str | None) -> bool:
        if type_name is None:
            return False
        canonical = self._canonical_type(type_name)
        if canonical is None:
            return False
        return self.type_properties.resolve(
            self.type_arena.type_id(canonical)
        ).needs_drop

    def _contains_borrow(self, type_name: str | None) -> bool:
        if type_name is None:
            return False
        canonical = self._canonical_type(type_name)
        if canonical is None:
            return False
        return self.type_properties.resolve(
            self.type_arena.type_id(canonical)
        ).contains_borrow

    def _owned_ownership(self, type_name: str | None) -> str:
        if self._owner(type_name):
            return (
                "owned_contained_borrow"
                if self._contains_borrow(type_name)
                else "owned"
            )
        return "borrow" if _is_borrowed(type_name) else "value"

    def _use_ownership(self, type_name: str | None) -> str:
        if self._contains_borrow(type_name) and not _is_borrowed(type_name):
            return "contained_borrow"
        if self._owner(type_name) or _is_borrowed(type_name):
            return "borrow"
        return "value"

    @staticmethod
    def _mutable_parameter_table(
        functions: dict[str, ast.FunctionDef],
    ) -> dict[str, set[str]]:
        mutable = {
            name: _assigned_parameter_names(function)
            for name, function in functions.items()
        }
        parameter_names = {
            name: {parameter.arg for parameter in function.args.args}
            for name, function in functions.items()
        }
        changed = True
        while changed:
            changed = False
            for caller_name, caller in functions.items():
                for call in (
                    node
                    for node in ast.walk(caller)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in functions
                ):
                    callee = functions[call.func.id]
                    for argument, parameter in zip(
                        call.args,
                        callee.args.args,
                    ):
                        if (
                            parameter.arg in mutable[call.func.id]
                            and isinstance(argument, ast.Name)
                            and argument.id in parameter_names[caller_name]
                            and argument.id not in mutable[caller_name]
                        ):
                            mutable[caller_name].add(argument.id)
                            changed = True
        return mutable

    def _scope(self, suffix: str = "body") -> str:
        return _stable_id("scope", self.path, self.current_function, suffix)

    def _new_node(
        self,
        node: ast.AST,
        kind: str,
        *,
        type_name: str | None = None,
        ownership: str = "value",
        effects: Iterable[str] = (),
        symbol_id: str | None = None,
        attributes: dict[str, Any] | None = None,
        children: Iterable[HIRNode] = (),
        scope_id: str | None = None,
    ) -> HIRNode:
        self.ordinal += 1
        source = _span(self.path, node)
        attrs = tuple(sorted((attributes or {}).items()))
        child_tuple = tuple(children)
        typed = self._intern_type(type_name)
        canonical_type = typed[0] if typed is not None else None
        type_id = typed[1] if typed is not None else None
        revision = _stable_id(
            "rev",
            kind,
            canonical_type,
            ownership,
            tuple(sorted(set(effects))),
            attrs,
            tuple(item.revision_id for item in child_tuple),
        )
        return HIRNode(
            id=_stable_id(
                "shirn",
                self.path,
                self.current_function,
                source.to_dict(),
                kind,
                self.ordinal,
            ),
            kind=kind,
            source=source,
            scope_id=scope_id or self._scope(),
            type_name=canonical_type,
            type_id=type_id,
            ownership=ownership,
            effects=tuple(sorted(set(effects))),
            symbol_id=symbol_id,
            revision_id=revision,
            attributes=attrs,
            children=child_tuple,
        )

    def expression(
        self,
        node: ast.AST,
        *,
        expected: str | None = None,
    ) -> HIRNode:
        if isinstance(node, ast.Hole):
            if expected is not None and expected != node.expected_type:
                raise StructuredHIRCompileError(
                    "TypedHoleContextMismatch: "
                    f"{node.hole_id}: {node.expected_type} != "
                    f"{expected}"
                )
            return self._new_node(
                node,
                "TypedHole",
                type_name=node.expected_type,
                ownership=(
                    "owned"
                    if self._owner(node.expected_type)
                    else "value"
                ),
                symbol_id=node.hole_id,
                attributes={
                    "hole_id": node.hole_id,
                    "expected_type": node.expected_type,
                    "context": list(node.context),
                    "callables": list(node.callables),
                    "effects": list(node.effects),
                    "capabilities": list(node.capabilities),
                },
            )
        if isinstance(node, ast.Name):
            type_name = self.local_types.get(node.id)
            symbol = _stable_id(
                "shirs",
                self.path,
                self.current_function,
                "local",
                node.id,
            )
            if node.id in self.functions and type_name is None:
                type_name = _function_callback_type(self.functions[node.id])
                symbol = self.function_symbols[node.id]
            return self._new_node(
                node,
                "Name",
                type_name=type_name,
                ownership=self._use_ownership(type_name),
                symbol_id=symbol,
                attributes={"name": node.id},
            )
        if isinstance(node, ast.Constant):
            inferred_type = (
                "Bool"
                if isinstance(node.value, bool)
                else "UInt64"
                if isinstance(node.value, int)
                else "Float64"
                if isinstance(node.value, float)
                else "Text"
                if isinstance(node.value, str)
                else "Bytes"
                if isinstance(node.value, bytes)
                else "Unit"
            )
            type_name = (
                expected
                if expected in _LANGUAGE_NUMERIC_TYPES
                and inferred_type in _LANGUAGE_NUMERIC_TYPES
                else inferred_type
            )
            attributes: dict[str, Any] = {"value": node.value}
            if isinstance(node.value, bytes):
                attributes = {
                    "literal_encoding": "bytes",
                    "value": list(node.value),
                }
            return self._new_node(
                node,
                "Literal",
                type_name=type_name,
                attributes=attributes,
            )
        if isinstance(node, ast.Attribute):
            owner = self.expression(node.value)
            type_name = self._attribute_type(owner.type_name, node.attr)
            return self._new_node(
                node,
                "FieldAccess",
                type_name=type_name,
                ownership=self._use_ownership(type_name),
                attributes={"field": node.attr},
                children=(owner,),
            )
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "__merlo_try__"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Call)
            ):
                inner_call = node.args[0]
                signature = intrinsic_signature(ast.unparse(inner_call.func))
                inner_expected = None
                if signature is not None and signature.result_type.startswith("Result["):
                    function_return = _type_name(
                        self.functions[self.current_function].returns
                    )
                    function_parts = self._result_parts(function_return)
                    signature_parts = self._result_parts(signature.result_type)
                    if function_parts is not None and signature_parts is not None:
                        inner_expected = (
                            function_return
                            if (
                                signature.name == "network.tcp_connect"
                                and function_parts[0] == "TcpStream"
                            )
                            else (
                                f"Result[{signature_parts[0]},"
                                f"{function_parts[1]}]"
                            )
                        )
                arguments = (
                    self.expression(inner_call, expected=inner_expected),
                )
            else:
                argument_expected: list[str | None] = [None] * len(node.args)
                # A generic constructor's result annotation is the source of
                # truth for its payload type.  Passing that context into the
                # child expression keeps nested owner/view constructors
                # typed (Box.new(bytes.as_view()), Some(...), Ok(...), ...)
                # instead of making the child look like an untyped call.
                if isinstance(node.func, ast.Attribute):
                    receiver_name = _ast_qualified_name(node.func.value)
                    method_name = node.func.attr
                    if method_name == "new" and receiver_name == "Box":
                        parts = generic_parts(expected or "", "Box", arity=1)
                        if parts is not None and argument_expected:
                            argument_expected[0] = parts[0]
                elif isinstance(node.func, ast.Name):
                    if node.func.id == "Some":
                        parts = generic_parts(expected or "", "Option", arity=1)
                        if parts is not None and argument_expected:
                            argument_expected[0] = parts[0]
                    elif node.func.id in {"Ok", "Err"}:
                        parts = generic_parts(expected or "", "Result", arity=2)
                        if parts is not None and argument_expected:
                            argument_expected[0] = parts[0 if node.func.id == "Ok" else 1]
                arguments = tuple(
                    self.expression(item, expected=argument_expected[index])
                    for index, item in enumerate(node.args)
                    if getattr(item, "_merlo_implicit_callable", None) is None
                )
            return self._call(node, arguments, expected=expected)
        if isinstance(node, ast.BinOp):
            children = (
                self.expression(node.left, expected=expected),
                self.expression(node.right, expected=expected),
            )
            numeric = {
                item.type_name
                for item in children
                if item.type_name is not None
            }
            if (
                "Bool" in numeric
                or not numeric <= _LANGUAGE_NUMERIC_TYPES
            ):
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: NumericOperandsRequired "
                    f"{tuple(item.type_name for item in children)}"
                )
            non_literals = {
                item.type_name
                for item in children
                if item.kind != "Literal" and item.type_name is not None
            }
            if len(non_literals) > 1:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: NumericTypeMismatch "
                    f"{tuple(sorted(non_literals))}"
                )
            type_name = (
                expected
                if expected in _LANGUAGE_NUMERIC_TYPES
                else next(
                    iter(non_literals),
                    next(iter(numeric), "UInt64"),
                )
            )
            if (
                isinstance(node.op, _INTEGER_BINARY_OPERATORS)
                and type_name not in _INTEGER_TYPES
            ):
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: IntegerOperatorRequired "
                    f"{type(node.op).__name__} for {type_name}"
                )
            operator = type(node.op).__name__
            attributes: dict[str, Any] = {"operator": operator}
            if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
                attributes["overflow"] = (
                    "checked"
                    if type_name in _INTEGER_TYPES
                    else "ieee754"
                )
            elif isinstance(node.op, ast.Div):
                attributes.update(
                    division_by_zero=(
                        "trap"
                        if type_name in _INTEGER_TYPES
                        else "ieee754"
                    ),
                    rounding=(
                        "toward_zero"
                        if type_name in _INTEGER_TYPES
                        else "ieee754"
                    ),
                )
                if type_name == "Int64":
                    attributes["signed_overflow"] = "checked"
            elif isinstance(node.op, ast.FloorDiv):
                attributes.update(
                    division_by_zero="trap",
                    rounding="toward_negative_infinity",
                    signed_overflow="checked",
                )
            elif isinstance(node.op, ast.Mod):
                attributes.update(
                    division_by_zero="trap",
                    remainder_sign="divisor",
                    signed_overflow="checked",
                )
            elif isinstance(node.op, (ast.LShift, ast.RShift)):
                attributes.update(
                    shift_range="checked",
                    overflow=(
                        "checked"
                        if isinstance(node.op, ast.LShift)
                        else "not_applicable"
                    ),
                )
            return self._new_node(
                node,
                "Binary",
                type_name=type_name,
                attributes=attributes,
                children=children,
            )
        if isinstance(node, ast.BoolOp):
            return self._new_node(
                node,
                "Boolean",
                type_name="Bool",
                attributes={"operator": type(node.op).__name__},
                children=tuple(self.expression(item) for item in node.values),
            )
        if isinstance(node, ast.Compare):
            children = (self.expression(node.left),) + tuple(
                self.expression(item) for item in node.comparators
            )
            comparable = {
                item.type_name
                for item in children
                if item.type_name not in {None, "Inferred"}
            }
            if len(comparable) > 1:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: ComparableOperandsRequired "
                    f"{tuple(item.type_name for item in children)}"
                )
            return self._new_node(
                node,
                "Compare",
                type_name="Bool",
                attributes={"operators": [type(item).__name__ for item in node.ops]},
                children=children,
            )
        if isinstance(node, ast.UnaryOp):
            child = self.expression(node.operand, expected=expected)
            type_name = (
                "Bool"
                if isinstance(node.op, ast.Not)
                else expected
                if expected in _LANGUAGE_NUMERIC_TYPES
                else child.type_name
            )
            if (
                isinstance(node.op, ast.Invert)
                and type_name not in _INTEGER_TYPES
            ):
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: IntegerOperatorRequired "
                    f"Invert for {type_name}"
                )
            if (
                isinstance(node.op, ast.USub)
                and type_name in {"Byte", "UInt64"}
            ):
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: "
                    f"UnsignedNegationForbidden: {type_name}"
                )
            attributes: dict[str, Any] = {
                "operator": type(node.op).__name__
            }
            if isinstance(node.op, ast.USub) and type_name == "Int64":
                attributes["overflow"] = "checked"
            return self._new_node(
                node,
                "Unary",
                type_name=type_name,
                attributes=attributes,
                children=(child,),
            )
        if isinstance(node, (ast.List, ast.Tuple)):
            children = tuple(self.expression(item) for item in node.elts)
            element_types = {item.type_name for item in children}
            if len(element_types) > 1:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: HeterogeneousArray "
                    f"{sorted(str(item) for item in element_types)}"
                )
            element_type = next(iter(element_types), "Unit")
            return self._new_node(
                node,
                "ArrayLiteral",
                type_name=f"Array[{element_type},{len(children)}]",
                ownership=self._owned_ownership(
                    f"Array[{element_type},{len(children)}]"
                ),
                attributes={"length": len(children)},
                children=children,
            )
        if isinstance(node, ast.Subscript):
            owner = self.expression(node.value)
            shape = collection_shape(owner.type_name)
            if shape is None:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: IndexRequiresCollection"
                )
            return self._new_node(
                node,
                "Index",
                type_name=shape.element_type,
                ownership="borrow",
                effects=("bounds_check",),
                children=(owner, self.expression(node.slice)),
            )
        if isinstance(node, ast.Lambda):
            metadata = getattr(node, "_merlo_closure_metadata", None)
            if metadata is None:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: CapturingClosureUnsupported; "
                    "use a typed Surface closure"
                )
            closure_id, parameters, return_type, captures, owner = metadata
            callback_type = expected or (
                "Fn["
                + ",".join(
                    (*[type_name for _, type_name in parameters], return_type)
                )
                + "]"
            )
            capture_nodes = []
            for name, _type_name_, _ownership in captures:
                capture = ast.Name(id=name, ctx=ast.Load())
                for attribute in (
                    "lineno",
                    "col_offset",
                    "end_lineno",
                    "end_col_offset",
                    "_merlo_path",
                ):
                    if hasattr(node, attribute):
                        setattr(capture, attribute, getattr(node, attribute))
                capture_nodes.append(self.expression(capture))
            return self._new_node(
                node,
                "ClosureCreate",
                type_name=callback_type,
                ownership="owned",
                attributes={
                    "closure_id": closure_id,
                    "parameters": parameters,
                    "return_type": return_type,
                    "captures": captures,
                    "owner": owner,
                },
                children=capture_nodes,
            )
        raise StructuredHIRCompileError(
            f"{self.path}:{getattr(node, 'lineno', 1)}: unsupported expression {type(node).__name__}"
        )

    def _attribute_type(self, owner: str | None, field_name: str) -> str | None:
        if owner in self.types:
            declaration = self.types[owner]
            if declaration.kind == "record":
                for member in declaration.fields:
                    if member.name == field_name:
                        return member.type_name
        return None

    def _result_parts(self, type_name: str) -> tuple[str, str] | None:
        parts = generic_parts(type_name, "Result", arity=2)
        if parts is not None:
            return parts
        declaration = self.types.get(type_name)
        if declaration is None or declaration.kind != "enum":
            return None
        variants = {variant.name: variant.payload_type for variant in declaration.variants}
        ok_type = variants.get("Ok")
        error_type = variants.get("Err")
        if ok_type is None or error_type is None:
            return None
        return ok_type, error_type

    def _call(
        self,
        node: ast.Call,
        arguments: tuple[HIRNode, ...],
        *,
        expected: str | None = None,
    ) -> HIRNode:
        name = _ast_qualified_name(node.func)
        if isinstance(node.func, ast.Name) and node.func.id == "__merlo_try__":
            if len(arguments) != 1:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: postfix propagation expects one Result expression"
                )
            result_type = arguments[0].type_name or ""
            result_parts = self._result_parts(result_type)
            if result_parts is None:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: postfix propagation requires Result, not {result_type or 'unknown'}"
                )
            ok_type, error_type = result_parts
            function_return = _type_name(self.functions[self.current_function].returns)
            function_parts = self._result_parts(function_return)
            if function_parts is None:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: postfix propagation requires a Result-returning function"
                )
            expected_error = function_parts[1]
            if not _type_compatible(error_type, expected_error):
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: propagated error {error_type} does not match {expected_error}"
                )
            return self._new_node(
                node,
                "ResultPropagation",
                type_name=ok_type,
                ownership="owned" if self._owner(ok_type) else arguments[0].ownership,
                effects=arguments[0].effects + ("result_branch", "may_return_error"),
                attributes={"result_type": result_type, "error_type": error_type},
                children=arguments,
            )
        effects: set[str] = set()
        type_name: str | None = None
        ownership = "value"
        kind = "DirectCall"
        symbol_id = None
        call_attributes: dict[str, Any] = {"callee": name}
        operation_children = arguments
        if isinstance(node.func, ast.Name):
            name = node.func.id
            symbol_id = self.function_symbols.get(name)
            if name == "drop":
                if len(arguments) != 1:
                    raise StructuredHIRCompileError(f"{self.path}:{node.lineno}: drop expects one value")
                kind = "DropValue"
                type_name = "Unit"
                call_attributes["drop_target"] = (
                    arguments[0].attribute_map.get("name")
                    if arguments[0].kind == "Name"
                    else None
                )
            elif name == "Path":
                if len(arguments) != 1 or arguments[0].type_name != "Text":
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: Path constructor expects Text"
                    )
                kind = "DirectCall"
                type_name = "Path"
                ownership = "owned"
            elif name in self.types and self.types[name].kind == "record":
                kind = "RecordConstruct"
                type_name = name
                ownership = "owned" if self._owner(name) else "value"
            elif name not in self.functions and name in {
                "wrapping_add",
                "wrapping_sub",
                "wrapping_mul",
                "checked_add",
                "checked_sub",
                "checked_mul",
            }:
                numeric_types = {
                    argument.type_name
                    for argument in arguments
                    if argument.type_name is not None
                }
                if (
                    len(arguments) != 2
                    or len(numeric_types) != 1
                    or not numeric_types <= _INTEGER_TYPES
                ):
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: {name} expects two matching "
                        "Byte, Int64, or UInt64 arguments"
                    )
                kind = "NumericIntrinsic"
                type_name = next(iter(numeric_types))
                call_attributes["numeric_type"] = type_name
                call_attributes["overflow"] = (
                    "wrapping" if name.startswith("wrapping_") else "checked"
                )
            elif name in _SCALAR_TYPES:
                if len(arguments) != 1:
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: {name} cast expects one argument"
                    )
                if arguments[0].type_name == "Bool" and name != "Bool":
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: BoolNumericCastForbidden"
                    )
                kind = "ScalarCast"
                type_name = name
                call_attributes["target_type"] = name
            elif name in self.local_types and (
                callback := _callback_parts(self.local_types[name])
            ) is not None:
                parameter_types, return_type = callback
                if len(arguments) != len(parameter_types):
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: callback {name} expects "
                        f"{len(parameter_types)} arguments"
                    )
                kind = "CallbackCall"
                type_name = return_type
                symbol_id = _stable_id(
                    "shirs", self.path, self.current_function, "parameter", name
                )
            elif name in self.extern_functions:
                foreign = self.extern_functions[name]
                kind = "ForeignCall"
                type_name = foreign.return_type
                effects.update(foreign.effects)
                call_attributes.update(
                    {
                        "abi": foreign.abi,
                        "foreign": True,
                        "error_type": foreign.error_type,
                        "pointer_policies": [
                            item.policy.to_dict()
                            for item in foreign.parameters
                            if item.policy is not None
                        ],
                    }
                )
                ownership = "owned" if self._owner(type_name) else "value"
                symbol_id = _stable_id("shirs", self.path, "extern", name)
            elif name in self.functions:
                type_name = _type_name(self.functions[name].returns)
                effects.update(self._function_effect_hint(self.functions[name]))
                ownership = (
                    "owned"
                    if self._owner(type_name)
                    else "borrow"
                    if _is_borrowed(type_name)
                    else "value"
                )
                call_attributes["move_arguments"] = tuple(
                    index
                    for index, parameter in enumerate(self.functions[name].args.args)
                    if (
                        self._owner(_type_name(parameter.annotation))
                        or _type_name(parameter.annotation) in self.types
                    )
                    and any(
                        isinstance(item, ast.Return)
                        and isinstance(item.value, ast.Name)
                        and item.value.id == parameter.arg
                        for item in ast.walk(self.functions[name])
                    )
                )
            if name == "set_error":
                kind = "TypedError"
            elif (
                name not in self.functions
                and name not in self.types
                and name not in self.extern_functions
                and name not in _SCALAR_TYPES
                and name not in {
                    "drop",
                    "wrapping_add",
                    "wrapping_sub",
                    "wrapping_mul",
                    "checked_add",
                    "checked_sub",
                    "checked_mul",
                    "Ok",
                    "Err",
                    "Some",
                    "NoneValue",
                    "Unit",
                    "console",
                    "fs",
                    "env",
                    "clock",
                    "random",
                    "network",
                    "tcp",
                    "process",
                }
                and not (
                    name in self.local_types
                    and _callback_parts(self.local_types[name]) is not None
                )
                and name != "Path"
            ):
                raise StructuredHIRCompileError(f"UnresolvedName: {name}")
        if isinstance(node.func, ast.Attribute):
            receiver_text = _ast_qualified_name(node.func.value)
            receiver_type = self.local_types.get(receiver_text)
            method = node.func.attr
            callee = f"{receiver_text}.{method}"
            signature = intrinsic_signature(callee)
            static_contract = CONTRACT_GRAPH.static_method(
                receiver_text,
                method,
            )
            static_signature = static_contract
            if (
                static_contract is not None
                and static_contract.accepts_arity(len(arguments))
            ):
                try:
                    static_signature = CONTRACT_GRAPH.resolve_static_method(
                        receiver_text,
                        method,
                        tuple(argument.type_name for argument in arguments),
                        expected,
                    )
                except ValueError as exc:
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: "
                        f"StaticContractMismatch: {callee}: {exc}"
                    ) from exc
            if (
                receiver_type is None
                and signature is None
                and static_contract is None
            ):
                receiver_type = self.expression(
                    node.func.value
                ).type_name
            method_signature = CONTRACT_GRAPH.method(
                receiver_type or "",
                method,
            )
            if method_signature is not None and not method_signature.static:
                call_attributes.update(
                    {
                        "contract_symbol": f"{receiver_type}.{method}",
                        "receiver_ownership": (
                            method_signature.receiver_ownership
                        ),
                        "result_ownership": (
                            method_signature.result_ownership
                        ),
                    }
                )
                if method_signature.operation_family is not None:
                    call_attributes["operation_family"] = (
                        method_signature.operation_family
                    )
                if method_signature.representation_lowering is None:
                    type_name = method_signature.result_for(expected)
                    ownership = method_signature.result_ownership
                    effects.update(method_signature.effects)
            if method in COLLECTION_OPERATIONS:
                receiver = self.expression(node.func.value)
                receiver_type = receiver.type_name or receiver_type
                shape = collection_shape(receiver_type)
                metadata = (
                    getattr(node.args[0], "_merlo_implicit_callable", None)
                    if len(node.args) == 1
                    else None
                )
                if shape is None or metadata is None:
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: typed collection callable metadata required"
                    )
                callable_id, parameter, parameter_type, return_type, expression_text = metadata
                expected_return = "Bool" if method in {"where", "count"} else return_type
                if method in {"where", "count"} and return_type != "Bool":
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: {method} callable must return Bool"
                    )
                callback = self._new_node(
                    node.args[0],
                    "ImplicitCallable",
                    type_name=expected_return,
                    attributes={
                        "callable_id": callable_id,
                        "callable_parameter": parameter,
                        "parameter_type": parameter_type,
                        "expression": expression_text,
                    },
                )
                kind = "CollectionOperation"
                result_element = (
                    return_type if method == "map" else shape.element_type
                )
                type_name = collection_result_type(method, result_element)
                operation_children = (receiver, callback)
                call_attributes.update(
                    {
                        "collection_operation": method,
                        "collection_kind": shape.kind,
                        "source_collection_type": receiver_type,
                        "element_type": shape.element_type,
                        "callable_parameter": parameter,
                    }
                )
            elif signature is not None:
                if len(arguments) != signature.arity:
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: {format_intrinsic_arity(signature, len(arguments))}"
                    )
                for index, (argument, parameter_type) in enumerate(
                    zip(arguments, signature.parameters, strict=True), 1
                ):
                    actual = argument.type_name
                    if actual != parameter_type and not (
                        (actual, parameter_type) in {("Text", "TextView"), ("Bytes", "BytesView")}
                    ):
                        raise StructuredHIRCompileError(
                            f"{self.path}:{node.lineno}: IntrinsicTypeMismatch: {callee} "
                            f"argument {index} expects {parameter_type}, got {actual}"
                        )
                kind = "FileOpen" if callee in {"fs.open_read", "fs.open_write"} else "DirectCall"
                signature_parts = self._result_parts(signature.result_type)
                if (
                    callee == "network.tcp_connect"
                    and expected
                    and expected.startswith("Result[")
                    and (expected_parts := self._result_parts(expected)) is not None
                    and _type_leaf(expected_parts[0]) == "TcpStream"
                ):
                    type_name = expected
                elif (
                    signature_parts is not None
                    and expected == signature_parts[0]
                ):
                    function_return = _type_name(
                        self.functions[self.current_function].returns
                    )
                    function_parts = self._result_parts(function_return)
                    type_name = (
                        f"Result[{signature_parts[0]},{function_parts[1]}]"
                        if function_parts is not None
                        else signature.result_type
                    )
                else:
                    type_name = contextual_result_type(
                        signature.result_type,
                        expected,
                    )
                if expected and expected.startswith("Result["):
                    expected_parts = self._result_parts(expected)
                    result_parts = self._result_parts(signature.result_type)
                    if (
                        expected_parts is None
                        or result_parts is None
                        or (
                            not _type_compatible(expected_parts[0], result_parts[0])
                            and not (
                                callee == "network.tcp_connect"
                                and _type_leaf(expected_parts[0]) == "TcpStream"
                                and _type_leaf(result_parts[0]) == "UInt64"
                            )
                        )
                    ):
                        raise StructuredHIRCompileError(
                            f"{self.path}:{node.lineno}: {callee} returns "
                            f"{result_parts[0] if result_parts else signature.result_type}, "
                            f"not {expected_parts[0] if expected_parts else expected}"
                        )
                ownership = signature.result_ownership
                effects.add(signature.effect)
                if signature.result_type.startswith("Result["):
                    effects.add("may_fail")
                operation_children = arguments
                call_attributes["host_operation"] = method
                if type_name.startswith("Result["):
                    call_attributes["error_type"] = type_name.split(",", 1)[1].rstrip("]")
                if callee.startswith("fs."):
                    call_attributes["resource"] = (
                        "FileReader"
                        if method == "open_read"
                        else "FileWriter"
                        if method == "open_write"
                        else "Text"
                        if method == "read_text"
                        else "Bytes"
                    )
            elif receiver_text in {
                "console", "fs", "env", "clock", "random", "network", "tcp",
                "process",
            }:
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: UnknownIntrinsic: {callee}"
                )
            elif static_contract is not None:
                if static_signature is None:
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: "
                        f"AmbiguousType: {callee}"
                    )
                if len(arguments) != static_signature.arity:
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: {callee} expects "
                        f"{static_signature.arity} argument(s), got {len(arguments)}"
                    )
                for index, (argument, parameter_type) in enumerate(
                    zip(
                        arguments,
                        static_signature.parameters,
                        strict=True,
                    ),
                    1,
                ):
                    if argument.type_name != parameter_type and (
                        argument.type_name,
                        parameter_type,
                    ) not in {
                        ("Bytes", "BytesView"),
                        ("Text", "TextView"),
                    }:
                        raise StructuredHIRCompileError(
                            f"{self.path}:{node.lineno}: {callee} argument "
                            f"{index} expects {parameter_type}, got "
                            f"{argument.type_name}"
                        )
                kind = {
                    "vec": "VecOperation",
                    "map": "MapOperation",
                    "box": "BoxOperation",
                }.get(
                    static_signature.operation_family,
                    "BytesTextOperation",
                )
                type_name = static_signature.result_type
                ownership = static_signature.result_ownership
                effects.update(static_signature.effects)
                call_attributes.update(
                    {
                        "contract_symbol": callee,
                        "parameter_ownership": list(
                            static_signature.parameter_ownership
                        ),
                        "result_ownership": (
                            static_signature.result_ownership
                        ),
                    }
                )
                if static_signature.operation_family is not None:
                    call_attributes["operation_family"] = (
                        static_signature.operation_family
                    )
                if static_signature.operation_family == "map":
                    call_attributes.update(
                        {
                            "map_operation": method,
                            "map_specialization": type_name,
                        }
                    )
                if static_signature.abi_lowering is not None:
                    call_attributes["abi_lowering"] = (
                        static_signature.abi_lowering
                    )
            elif (
                method_signature is None
                and CONTRACT_GRAPH.has_representation_method(method)
            ):
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: UnknownCall: "
                    f"{receiver_type or 'unresolved'}.{method}"
                )
            elif (
                method_signature is not None
                and method_signature.operation_family == "resource"
            ):
                receiver = self.expression(node.func.value)
                kind = (
                    "FileLines"
                    if method_signature.representation_lowering == "file_lines"
                    else "DirectCall"
                )
                type_name = method_signature.result_for(expected)
                ownership = method_signature.result_ownership
                effects.update(method_signature.effects)
                operation_children = (receiver,) + arguments
                call_attributes.update(
                    {
                        "contract_symbol": f"{receiver_type}.{method}",
                        "representation_lowering": (
                            method_signature.representation_lowering
                        ),
                        "resource": type_name,
                        "borrowed_from": receiver_text,
                    }
                )
            elif (
                method_signature is not None
                and method_signature.operation_family == "bytes_text"
            ):
                kind = "BytesTextOperation"
                type_name = method_signature.result_for(expected)
                ownership = method_signature.result_ownership
                effects.update(method_signature.effects)
                operation_children = (
                    self.expression(node.func.value),
                ) + arguments
            elif (
                method_signature is not None
                and method_signature.representation_lowering is not None
            ):
                if not method_signature.accepts_arity(len(arguments)):
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: {receiver_type}.{method} "
                        f"expects {method_signature.minimum_arity}.."
                        f"{method_signature.arity} argument(s), "
                        f"got {len(arguments)}"
                    )
                receiver = self.expression(node.func.value)
                kind = "DirectCall"
                type_name = method_signature.result_type
                payload_clone = (
                    method_signature.result_ownership
                    == "payload_clone"
                )
                payload_owned = payload_clone and self._owner(type_name)
                ownership = (
                    "owned"
                    if payload_owned
                    else "value"
                    if payload_clone
                    else method_signature.result_ownership
                )
                effects.update(
                    effect
                    for effect in method_signature.effects
                    if not payload_clone
                    or payload_owned
                    or effect not in {"allocate", "copy"}
                )
                operation_children = (receiver,) + arguments
                call_attributes.update(
                    {
                        "contract_symbol": f"{receiver_type}.{method}",
                        "representation_lowering": (
                            method_signature.representation_lowering
                        ),
                    }
                )
            elif (
                method_signature is not None
                and method_signature.operation_family == "map"
            ):
                kind = "MapOperation"
                specialization = receiver_type
                map_types = _map_types(specialization)
                if map_types is None:
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: Map operation requires "
                        "a concrete specialization"
                    )
                key_type, value_type = map_types
                if not method_signature.accepts_arity(len(arguments)):
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: unsupported Map operation "
                        f"{method}/{len(arguments)}"
                    )
                if method == "increment" and value_type != "UInt64":
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: Map.increment requires UInt64 values"
                    )
                operation_children = (self.expression(node.func.value),) + arguments
                expected_types = method_signature.parameters_for(len(arguments))
                for argument, expected_type in zip(arguments, expected_types):
                    if argument.type_name not in {None, "Inferred", expected_type}:
                        raise StructuredHIRCompileError(
                            f"{self.path}:{node.lineno}: Map.{method} argument must be "
                            f"{expected_type}, not {argument.type_name}"
                        )
                call_attributes.update(
                    {"map_operation": method, "map_specialization": specialization}
                )
                type_name = method_signature.result_for(expected)
                ownership = method_signature.result_ownership
                effects.update(method_signature.effects)
            elif (
                method_signature is not None
                and method_signature.operation_family == "box"
            ):
                kind = "BoxOperation"
                if not method_signature.accepts_arity(len(arguments)):
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: unsupported Box operation "
                        f"{method}/{len(arguments)}"
                    )
            elif (
                method_signature is not None
                and method_signature.operation_family == "vec"
            ):
                kind = "VecOperation"
                if not method_signature.accepts_arity(len(arguments)):
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: unsupported Vec operation "
                        f"{method}/{len(arguments)} for "
                        f"{receiver_type or 'unresolved'}"
                    )
            elif receiver_text in self.types and self.types[receiver_text].kind == "enum":
                kind = "EnumConstruct"
                type_name = receiver_text
                ownership = "owned" if self._owner(receiver_text) else "value"
                variant = next((item for item in self.types[receiver_text].variants if item.name == method), None)
                if variant is None:
                    raise StructuredHIRCompileError(f"unknown enum variant {name}")
            elif method == "tag":
                kind = "EnumTag"
                type_name = "UInt64"
        if self._contains_borrow(type_name) and not _is_borrowed(type_name):
            ownership = (
                "owned_contained_borrow"
                if ownership == "owned" or self._owner(type_name)
                else "contained_borrow"
            )
        return self._new_node(
            node,
            kind,
            type_name=type_name,
            ownership=ownership,
            effects=effects,
            symbol_id=symbol_id,
            attributes=call_attributes,
            children=operation_children,
        )

    def _function_effect_hint(
        self,
        function: ast.FunctionDef,
        visiting: frozenset[str] = frozenset(),
    ) -> set[str]:
        if function.name in visiting:
            return set()
        visiting = visiting | {function.name}
        effects: set[str] = set()
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            name = _ast_qualified_name(node.func)
            if (
                ".push" in name
                or name in {"Vec.new", "Map.new"}
                or name.endswith(".insert")
                or name.endswith(".increment")
            ):
                effects.update(("allocate", "may_fail"))
            receiver, separator, method = name.partition(".")
            static_signature = (
                CONTRACT_GRAPH.static_method(receiver, method)
                if separator
                else None
            )
            if static_signature is not None:
                effects.update(static_signature.effects)
            signature = intrinsic_signature(name)
            if signature is not None:
                effects.add(signature.effect)
                if signature.result_type.startswith("Result["):
                    effects.add("may_fail")
            elif isinstance(node.func, ast.Name) and node.func.id in self.functions:
                effects.update(
                    self._function_effect_hint(self.functions[node.func.id], visiting)
                )
            if name == "set_error":
                effects.add("typed_error")
        return effects

    def statement(self, node: ast.stmt, *, scope_suffix: str = "body") -> HIRNode:
        scope_id = self._scope(scope_suffix)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            type_name = _type_name(node.annotation)
            self.local_types[node.target.id] = type_name
            value = (
                self.expression(node.value, expected=type_name)
                if node.value is not None
                else ()
            )
            if isinstance(value, HIRNode) and value.type_name != type_name:
                if self._result_parts(value.type_name) is not None:
                    raise StructuredHIRCompileError(
                        f"{self.path}:{node.lineno}: BindingTypeMismatch: "
                        f"{node.target.id} expects {type_name}, got {value.type_name}; "
                        "propagate or match the Result explicitly"
                    )
            binding = getattr(
                node,
                "_merlo_binding_kind",
                self.preprocessed.binding_kinds.get(node.lineno, "let"),
            )
            return self._new_node(
                node,
                "VarBinding" if binding == "var" else "LetBinding",
                type_name=type_name,
                ownership=self._owned_ownership(type_name),
                symbol_id=_stable_id("shirs", self.path, self.current_function, "local", node.target.id),
                attributes={"name": node.target.id, "mutable": binding == "var"},
                children=(value,) if isinstance(value, HIRNode) else (),
                scope_id=scope_id,
            )
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            value = self.expression(node.value)
            kind = "SetField" if isinstance(target, ast.Attribute) else "Assign"
            return self._new_node(
                node,
                kind,
                type_name=value.type_name,
                attributes={"target": _ast_qualified_name(target)},
                children=(value,),
                scope_id=scope_id,
            )
        if isinstance(node, ast.AugAssign):
            target = self.expression(node.target)
            value = self.expression(node.value)
            return self._new_node(
                node,
                "AugAssign",
                type_name=target.type_name or value.type_name,
                attributes={"target": _ast_qualified_name(node.target), "operator": type(node.op).__name__},
                children=(target, value),
                scope_id=scope_id,
            )
        if isinstance(node, ast.Expr):
            child = self.expression(node.value)
            return self._new_node(
                node,
                "Expression",
                effects=child.effects,
                children=(child,),
                scope_id=scope_id,
            )
        if isinstance(node, ast.Break):
            return self._new_node(node, "Break", scope_id=scope_id)
        if isinstance(node, ast.Return):
            expected_return = _type_name(
                self.functions[self.current_function].returns
            )
            child = (
                self.expression(node.value, expected=expected_return)
                if node.value is not None
                else None
            )
            if (
                child is not None
                and self._result_parts(child.type_name) is not None
                and self._result_parts(expected_return) is None
            ):
                raise StructuredHIRCompileError(
                    f"{self.path}:{node.lineno}: ReturnTypeMismatch: "
                    f"expected {expected_return}, got {child.type_name}"
                )
            return_type = child.type_name if child else "Unit"
            ownership = (
                self._owned_ownership(return_type)
                if self._owner(return_type) or _is_borrowed(return_type)
                else child.ownership if child else "value"
            )
            return self._new_node(
                node,
                "Return",
                type_name=return_type,
                ownership=ownership,
                children=(child,) if child else (),
                scope_id=scope_id,
            )
        if isinstance(node, ast.Continue):
            return self._new_node(node, "Continue", scope_id=scope_id)
        if isinstance(node, ast.Pass):
            return self._new_node(node, "Pass", scope_id=scope_id)
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "__merlo_unsafe_scope__"
        ):
            body = tuple(
                self.statement(item, scope_suffix=f"unsafe@{node.lineno}")
                for item in node.body
            )
            effects = tuple(
                sorted({effect for item in body for nested in item.walk() for effect in nested.effects})
            )
            return self._new_node(
                node,
                "UnsafeBlock",
                effects=effects,
                attributes={"non_propagating": True},
                children=body,
                scope_id=scope_id,
            )
        if isinstance(node, ast.If):
            test = self.expression(node.test)
            body = tuple(self.statement(item, scope_suffix=f"if@{node.lineno}.then") for item in node.body)
            other = tuple(self.statement(item, scope_suffix=f"if@{node.lineno}.else") for item in node.orelse)
            then_node = self._new_node(node, "Then", children=body, scope_id=self._scope(f"if@{node.lineno}.then"))
            else_node = self._new_node(node, "Else", children=other, scope_id=self._scope(f"if@{node.lineno}.else"))
            return self._new_node(node, "If", type_name="Unit", children=(test, then_node, else_node), scope_id=scope_id)
        if isinstance(node, ast.While):
            test = self.expression(node.test)
            body = tuple(self.statement(item, scope_suffix=f"while@{node.lineno}") for item in node.body)
            loop_body = self._new_node(node, "LoopBody", children=body, scope_id=self._scope(f"while@{node.lineno}"))
            return self._new_node(node, "While", children=(test, loop_body), scope_id=scope_id)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
            iterable = self.expression(node.iter)
            shape = collection_shape(iterable.type_name)
            self.local_types[node.target.id] = (
                shape.element_type
                if shape is not None
                else "TextView"
                if iterable.type_name == "FileLines"
                else "Inferred"
            )
            body = tuple(self.statement(item, scope_suffix=f"for@{node.lineno}") for item in node.body)
            loop_body = self._new_node(node, "LoopBody", children=body, scope_id=self._scope(f"for@{node.lineno}"))
            return self._new_node(
                node,
                "For",
                attributes={"target": node.target.id},
                children=(iterable, loop_body),
                scope_id=scope_id,
            )
        if isinstance(node, ast.Match):
            subject = self.expression(node.subject)
            cases = tuple(
                self._match_case(item, node, index, subject.type_name)
                for index, item in enumerate(node.cases)
            )
            self._validate_match(node, subject.type_name, node.cases)
            return self._new_node(node, "Match", children=(subject,) + cases, scope_id=scope_id)
        raise StructuredHIRCompileError(f"{self.path}:{node.lineno}: unsupported statement {type(node).__name__}")

    def _match_case(
        self,
        case: ast.match_case,
        owner: ast.Match,
        index: int,
        subject_type: str | None,
    ) -> HIRNode:
        pattern_text = _ast_pattern_name(case.pattern)
        bindings: dict[str, str] = {}
        if isinstance(case.pattern, ast.MatchClass):
            variant_name = (
                case.pattern.cls.attr
                if isinstance(case.pattern.cls, ast.Attribute)
                else case.pattern.cls.id
                if isinstance(case.pattern.cls, ast.Name)
                else ""
            )
            declaration = self.types.get(subject_type or "")
            variants = (
                {
                    variant.name: variant.payload_type
                    for variant in declaration.variants
                }
                if declaration is not None and declaration.kind == "enum"
                else _sum_variants(subject_type)
            )
            payload_type = variants.get(variant_name) if variants is not None else None
            if payload_type is not None:
                for pattern in case.pattern.patterns:
                    if isinstance(pattern, ast.MatchAs) and pattern.name:
                        bindings[pattern.name] = payload_type
        elif isinstance(case.pattern, ast.MatchAs) and case.pattern.name and subject_type:
            bindings[case.pattern.name] = subject_type
        previous = {
            name: self.local_types.get(name)
            for name in bindings
        }
        self.local_types.update(bindings)
        body = tuple(
            self.statement(item, scope_suffix=f"match@{owner.lineno}.case{index}")
            for item in case.body
        )
        for name, type_name in previous.items():
            if type_name is None:
                self.local_types.pop(name, None)
            else:
                self.local_types[name] = type_name
        return self._new_node(
            case.pattern,
            "MatchCase",
            attributes={"pattern": pattern_text, "wildcard": isinstance(case.pattern, ast.MatchAs) and case.pattern.name is None},
            children=body,
            scope_id=self._scope(f"match@{owner.lineno}.case{index}"),
        )

    def _validate_match(self, node: ast.Match, subject_type: str | None, cases: list[ast.match_case]) -> None:
        if any(isinstance(item.pattern, ast.MatchAs) and item.pattern.name is None for item in cases):
            return
        declaration = self.types.get(subject_type or "")
        variants = (
            {
                item.name: item.payload_type
                for item in declaration.variants
            }
            if declaration is not None and declaration.kind == "enum"
            else _sum_variants(subject_type)
        )
        if variants is None:
            return
        enum_name = subject_type
        expected = set(variants)
        seen = set()
        for item in cases:
            pattern = item.pattern
            if isinstance(pattern, ast.MatchSingleton) and pattern.value is None:
                seen.add("NoneValue")
            elif isinstance(pattern, ast.MatchValue) and isinstance(pattern.value, ast.Attribute):
                seen.add(pattern.value.attr)
            elif isinstance(pattern, ast.MatchClass):
                if isinstance(pattern.cls, ast.Attribute):
                    seen.add(pattern.cls.attr)
                elif isinstance(pattern.cls, ast.Name):
                    seen.add("NoneValue" if pattern.cls.id == "None" else pattern.cls.id)
        missing = sorted(expected - seen)
        if missing:
            raise StructuredHIRCompileError(
                f"{self.path}:{node.lineno}: NonExhaustiveMatch {enum_name}: {missing}"
            )

    def _contract(self, node: ast.Contract) -> HIRContract:
        source_node = (
            node
            if hasattr(node, "lineno")
            else node.condition
        )
        condition = self.expression(node.condition, expected="Bool")
        if condition.type_name != "Bool":
            raise StructuredHIRCompileError(
                f"{self.path}:{source_node.lineno}: ContractRequiresBool"
            )
        return HIRContract(
            node.kind,
            ast.unparse(node.condition),
            condition,
            _span(self.path, source_node),
        )


    def function(self, node: ast.FunctionDef) -> HIRFunction:
        self.current_function = node.name
        self.ordinal = 0
        self.local_types = {
            argument.arg: _type_name(argument.annotation) for argument in node.args.args
        }
        assigned = self.mutable_parameters[node.name]
        returned_parameters = {
            item.value.id
            for item in ast.walk(node)
            if isinstance(item, ast.Return)
            and isinstance(item.value, ast.Name)
        }
        parameters = []
        for argument in node.args.args:
            type_name = _type_name(argument.annotation)
            typed = self._intern_type(type_name)
            assert typed is not None
            type_name, type_id = typed
            source = _span(self.path, argument)
            owns_value = self._owner(type_name)
            ownership = (
                "owned"
                if owns_value and argument.arg in returned_parameters
                else "borrow_mut"
                if argument.arg in assigned
                else "contained_borrow"
                if self._contains_borrow(type_name) and not _is_borrowed(type_name)
                else "borrow"
                if owns_value or _is_borrowed(type_name)
                else "value"
            )
            symbol_id = _stable_id("shirs", self.path, node.name, "parameter", argument.arg)
            parameters.append(
                HIRParameter(
                    name=argument.arg,
                    type_name=type_name,
                    type_id=type_id,
                    ownership=ownership,
                    source=source,
                    symbol_id=symbol_id,
                    revision_id=_stable_id("rev", node.name, argument.arg, type_name, ownership),
                )
            )
        requirements = tuple(
            self._contract(item)
            for item in node.body
            if isinstance(item, ast.Contract)
            and item.kind == "require"
        )
        body = tuple(
            self.statement(item)
            for item in node.body
            if not isinstance(item, ast.Contract)
        )
        typed_return = self._intern_type(_type_name(node.returns))
        assert typed_return is not None
        return_type, return_type_id = typed_return
        self.local_types["result"] = return_type
        try:
            ensures = tuple(
                self._contract(
                    ast.Contract(
                        condition=condition,
                        kind="ensure",
                    )
                )
                for condition in getattr(node, "_merlo_ensures", ())
            )
        finally:
            del self.local_types["result"]
        effects = tuple(sorted({effect for item in body for nested in item.walk() for effect in nested.effects}))
        source = _span(self.path, node)
        symbol_id = self.function_symbols[node.name]
        revision_id = _stable_id(
            "rev",
            node.name,
            [(item.name, item.type_name, item.ownership) for item in parameters],
            return_type,
            effects,
            [item.revision_id for item in body],
            [item.condition.revision_id for item in requirements],
            [item.condition.revision_id for item in ensures],
            self.borrow_summaries.get(node.name, BorrowSummary()).semantic_dict(),
        )
        return HIRFunction(
            name=node.name,
            parameters=tuple(parameters),
            return_type=return_type,
            return_type_id=return_type_id,
            effects=effects,
            requirements=requirements,
            ensures=ensures,
            body=body,
            source=source,
            scope_id=self._scope(),
            symbol_id=symbol_id,
            revision_id=revision_id,
            borrow_summary=self.borrow_summaries.get(node.name, BorrowSummary()),
        )


def _parse_type_declarations(
    path: str,
    module: ast.Module,
    kinds: dict[str, str],
    type_arena: TypeArena,
    type_cache: dict[str, tuple[str, TypeId]],
) -> dict[str, HIRTypeDecl]:
    result: dict[str, HIRTypeDecl] = {}
    def intern(type_name: str | None) -> tuple[str, TypeId] | None:
        return _intern_cached_type(type_arena, type_cache, type_name, path)
    for node in (item for item in module.body if isinstance(item, ast.ClassDef)):
        kind = kinds.get(node.name)
        if kind not in {"record", "enum"}:
            raise StructuredHIRCompileError(
                f"{path}:{node.lineno}: unknown type declaration"
            )
        type_symbol = _stable_id("shirs", path, kind, node.name)
        typed_nominal = intern(node.name)
        assert typed_nominal is not None
        nominal_name, nominal_type_id = typed_nominal
        source = _span(path, node)
        fields: list[HIRField] = []
        variants: list[HIRVariant] = []
        invariants = tuple(
            HIRInvariant(
                function.name,
                ast.unparse(function.body[0].value),
                _span(path, function.body[0]),
                _stable_id(
                    "rev",
                    node.name,
                    "invariant",
                    ast.unparse(function.body[0].value),
                ),
            )
            for function in module.body
            if isinstance(function, ast.FunctionDef)
            and getattr(
                function,
                "_merlo_invariant_owner",
                None,
            )
            == node.name
            and len(function.body) == 1
            and isinstance(function.body[0], ast.Return)
            and function.body[0].value is not None
        )
        for ordinal, statement in enumerate(node.body):
            if kind == "record":
                if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
                    raise StructuredHIRCompileError(f"{path}:{statement.lineno}: record fields require types")
                name = statement.target.id
                typed = intern(_type_name(statement.annotation))
                assert typed is not None
                type_name, type_id = typed
                item_source = _span(path, statement)
                symbol = _stable_id("shirs", path, node.name, "field", name)
                fields.append(
                    HIRField(
                        name=name,
                        type_name=type_name,
                        type_id=type_id,
                        source=item_source,
                        symbol_id=symbol,
                        revision_id=_stable_id("rev", node.name, name, type_name),
                    )
                )
            else:
                if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Name):
                    name = statement.value.id
                    payload = None
                    payload_id = None
                elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    name = statement.target.id
                    typed = intern(_type_name(statement.annotation))
                    assert typed is not None
                    payload, payload_id = typed
                else:
                    raise StructuredHIRCompileError(f"{path}:{statement.lineno}: invalid enum variant")
                item_source = _span(path, statement)
                symbol = _stable_id("shirs", path, node.name, "variant", name)
                variants.append(
                    HIRVariant(
                        name=name,
                        payload_type=payload,
                        payload_type_id=payload_id,
                        tag=ordinal,
                        source=item_source,
                        symbol_id=symbol,
                        revision_id=_stable_id("rev", node.name, name, payload, ordinal),
                    )
                )
        revision = _stable_id(
            "rev",
            kind,
            node.name,
            [(item.name, item.type_name) for item in fields],
            [(item.name, item.payload_type, item.tag) for item in variants],
            [item.revision_id for item in invariants],
        )
        result[node.name] = HIRTypeDecl(
            name=nominal_name,
            type_id=nominal_type_id,
            kind=kind,
            source=source,
            symbol_id=type_symbol,
            revision_id=revision,
            fields=tuple(fields),
            variants=tuple(variants),
            invariants=invariants,
        )
    return result
def _canonical_span(span: Any) -> SourceSpan:
    return SourceSpan(span.path, span.start_line, span.start_column, span.end_line, span.end_column)

def _canonical_flow_machine_hir(
    program: CanonicalProgram,
    type_arena: TypeArena,
    type_cache: dict[str, tuple[str, TypeId]],
) -> tuple[tuple[HIRFlow, ...], tuple[HIRMachine, ...]]:
    def intern(type_name: str | None) -> tuple[str, TypeId] | None:
        return _intern_cached_type(
            type_arena,
            type_cache,
            type_name,
            "canonical",
        )

    flows: list[HIRFlow] = []
    for flow in program.flows:
        symbol = _stable_id("shirs", flow.span.path, "flow", flow.name)
        revision = _stable_id("rev", flow.to_payload())
        scope = _stable_id("scope", flow.span.path, flow.name, "flow")
        body: list[HIRNode] = []

        def flow_step(step: CanonicalFlowStep) -> HIRNode:
            typed = intern(step.type_name)
            assert typed is not None
            type_name, type_id = typed
            return HIRNode(
                id=_stable_id("node", flow.name, step.node_id),
                kind="FlowStep",
                source=_canonical_span(step.span),
                scope_id=scope,
                type_name=type_name,
                type_id=type_id,
                ownership="value",
                effects=tuple(),
                symbol_id=None,
                revision_id=revision,
                attributes=(
                    ("name", step.name),
                    ("value", step.value),
                    ("policies", step.to_payload()["policies"]),
                ),
            )

        for item in flow.body:
            if isinstance(item, CanonicalParallel):
                body.append(
                    HIRNode(
                        id=_stable_id("node", flow.name, item.node_id),
                        kind="Parallel",
                        source=_canonical_span(item.span),
                        scope_id=scope,
                        type_name=None,
                        type_id=None,
                        ownership="value",
                        effects=tuple(),
                        symbol_id=None,
                        revision_id=revision,
                        attributes=tuple(),
                        children=tuple(flow_step(branch) for branch in item.branches),
                    )
                )
            elif isinstance(item, CanonicalFlowStep):
                body.append(flow_step(item))
        parameters = []
        for name, raw_type in flow.parameters:
            typed = intern(raw_type)
            assert typed is not None
            type_name, type_id = typed
            parameters.append(
                HIRParameter(
                    name=name,
                    type_name=type_name,
                    type_id=type_id,
                    ownership="value",
                    source=_canonical_span(flow.span),
                    symbol_id=_stable_id("symbol", flow.name, name),
                    revision_id=revision,
                )
            )
        typed_return = intern(flow.return_type)
        assert typed_return is not None
        return_type, return_type_id = typed_return
        flows.append(
            HIRFlow(
                name=flow.name,
                parameters=tuple(parameters),
                return_type=return_type,
                return_type_id=return_type_id,
                durable=flow.durable,
                effects=flow.effects,
                capabilities=flow.capabilities,
                body=tuple(body),
                source=_canonical_span(flow.span),
                symbol_id=symbol,
                revision_id=revision,
            )
        )

    machines: list[HIRMachine] = []
    for machine in program.machines:
        symbol = _stable_id("shirs", machine.span.path, "machine", machine.name)
        revision = _stable_id("rev", machine.to_payload())
        scope = _stable_id("scope", machine.span.path, machine.name, "machine")
        transitions = []
        for transition in machine.transitions:
            typed = intern(transition.target)
            assert typed is not None
            target, target_id = typed
            transitions.append(
                HIRNode(
                    id=_stable_id("node", machine.name, transition.node_id),
                    kind="Transition",
                    source=_canonical_span(transition.span),
                    scope_id=scope,
                    type_name=target,
                    type_id=target_id,
                    ownership="value",
                    effects=transition.effects,
                    symbol_id=None,
                    revision_id=revision,
                    attributes=(
                        ("name", transition.name),
                        ("sources", transition.sources),
                        ("target", transition.target),
                    ),
                )
            )
        parameters = []
        for name, raw_type in machine.parameters:
            typed = intern(raw_type)
            assert typed is not None
            type_name, type_id = typed
            parameters.append(
                HIRParameter(
                    name=name,
                    type_name=type_name,
                    type_id=type_id,
                    ownership="value",
                    source=_canonical_span(machine.span),
                    symbol_id=_stable_id("symbol", machine.name, name),
                    revision_id=revision,
                )
            )
        states = []
        for state in machine.states:
            fields = []
            for name, raw_type in state.fields:
                typed = intern(raw_type)
                assert typed is not None
                type_name, type_id = typed
                fields.append(HIRMachineStateField(name, type_name, type_id))
            states.append((state.name, tuple(fields)))
        machines.append(
            HIRMachine(
                name=machine.name,
                parameters=tuple(parameters),
                states=tuple(states),
                initial=machine.initial,
                invariant=machine.invariant,
                transitions=tuple(transitions),
                source=_canonical_span(machine.span),
                symbol_id=symbol,
                revision_id=revision,
            )
        )
    return tuple(flows), tuple(machines)



def _normalize_native_context_metadata(module: ast.Module, path: str) -> None:
    """Make reused native expr_context defaults deterministic across compilations."""
    for node in ast.walk(module):
        if isinstance(node, ast.expr_context):
            node._merlo_path = path
            node.lineno = 1
            node.col_offset = 0
            node.end_lineno = 1
            node.end_col_offset = 0



def _intern_ffi_types(
    ffi_program: FFIProgram,
    type_arena: TypeArena,
    type_cache: dict[str, tuple[str, TypeId]],
    path: str,
) -> None:
    spellings: list[str] = []
    for function in ffi_program.extern_functions:
        spellings.extend(item.type_name for item in function.parameters)
        spellings.append(function.return_type)
        if function.error_type is not None:
            spellings.append(function.error_type)
    for record in ffi_program.repr_c_records:
        spellings.extend(item.type_name for item in record.fields)
    for type_name in spellings:
        try:
            normalized = _normalize_ffi_type(type_name)
        except ValueError as exc:
            raise StructuredHIRCompileError(
                f"{path}: invalid FFI HIR type {type_name}"
            ) from exc
        typed = _intern_cached_type(type_arena, type_cache, normalized, path)
        type_cache[type_name] = typed

def _preintern_analysis_types(
    functions: Mapping[str, ast.FunctionDef],
    type_context: TypeContextBuilder,
    type_cache: dict[str, tuple[str, TypeId]],
    path: str,
) -> None:
    for spelling in (
        "Unit", "Bool", "Byte", "Int8", "UInt8", "Int16", "UInt16",
        "Int32", "UInt32", "Int64", "UInt64", "Float32", "Float64",
        "Text", "Bytes", "TextBuilder", "Json", "Path",
        "TextView", "BytesView", "FileLines", "FileReader", "FileWriter",
    ):
        _intern_cached_type(type_context, type_cache, spelling, path)
    for function in functions.values():
        if function.returns is not None:
            _intern_cached_type(
                type_context,
                type_cache,
                _type_name(function.returns),
                path,
            )
        for node in ast.walk(function):
            annotation = (
                node.annotation
                if isinstance(node, (ast.arg, ast.AnnAssign))
                else None
            )
            if annotation is not None:
                _intern_cached_type(
                    type_context,
                    type_cache,
                    _type_name(annotation),
                    path,
                )


def compile_structured_hir(
    source: str,
    *,
    path: str = "main.mlo",
    entry_function: str = "main",
) -> StructuredHIRProgram:
    type_arena = TypeContextBuilder(allow_unresolved=False)
    type_cache: dict[str, tuple[str, TypeId]] = {}
    if not source.strip():
        raise StructuredHIRCompileError("empty Structured HIR source")
    try:
        ffi_program = validate_ffi(source, path=path)
    except FFICompileError as exc:
        raise StructuredHIRCompileError(str(exc)) from exc
    _intern_ffi_types(ffi_program, type_arena, type_cache, path)
    preprocessed = _preprocess(_preprocess_ffi_surface(source))
    try:
        module = ast.parse(preprocessed.source, filename=path)
    except SyntaxError as exc:
        raise StructuredHIRCompileError(f"{path}:{exc.lineno}:{exc.offset}: {exc.msg}") from exc
    _validate_map_specializations(module, path)
    types = _parse_type_declarations(
        path,
        module,
        preprocessed.declaration_kinds,
        type_arena,
        type_cache,
    )
    function_nodes = {
        item.name: item for item in module.body if isinstance(item, ast.FunctionDef)
    }
    unsupported = [
        type(item).__name__
        for item in module.body
        if not isinstance(item, (ast.ClassDef, ast.FunctionDef))
    ]
    if unsupported:
        raise StructuredHIRCompileError(f"unsupported top-level declarations: {unsupported}")
    if entry_function not in function_nodes:
        raise StructuredHIRCompileError(f"missing entry function: {entry_function}")
    _preintern_analysis_types(function_nodes, type_arena, type_cache, path)
    for item in types.values():
        type_arena.register_declaration(_type_declaration_projection(item))
    borrow_summaries = compute_borrow_summaries(
        function_nodes,
        types,
        type_arena,
    )
    _OwnershipChecker(
        path,
        types,
        function_nodes,
        borrow_summaries,
        preprocessed.binding_kinds,
        type_context=type_arena,
        compile_error=StructuredHIRCompileError,
        type_name=_type_name,
        stable_id=_stable_id,
        is_borrowed=_is_borrowed,
        qualified_name=_ast_qualified_name,
    ).check()
    builder = _HIRBuilder(
        path,
        source,
        preprocessed,
        types,
        function_nodes,
        ffi_program,
        borrow_summaries,
        type_arena=type_arena,
        type_cache=type_cache,
    )
    functions = tuple(builder.function(item) for item in function_nodes.values())
    _normalize_native_context_metadata(module, path)
    type_context = type_arena.freeze()
    frozen_arena = type_context.arena
    return StructuredHIRProgram(
        source=source,
        path=path,
        source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        types=tuple(types.values()),
        functions=functions,
        entry_function=entry_function,
        native_syntax_json=ast.module_to_json(module),
        ffi_program=ffi_program,
        type_context=type_context,
        type_arena_digest=frozen_arena.digest,
        native_module=module,
    )


def compile_canonical_hir(
    program: CanonicalProgram,
    *,
    entry_function: str = "main",
    type_context_builder: TypeContextBuilder | None = None,
) -> StructuredHIRProgram:
    """Lower the retained typed Surface tree through the production HIR builder."""
    if program.surface_program is None:
        raise StructuredHIRCompileError(
            "CanonicalSurfaceRequired: serialized projections are not compiler input"
        )
    source = program.projection_source or ""
    candidate = type_context_builder or program.type_context_builder
    if (
        isinstance(candidate, TypeContextBuilder)
        and getattr(candidate, "_frozen_context", None) is None
    ):
        type_arena = candidate
    else:
        type_arena = TypeContextBuilder(allow_unresolved=False)
    type_cache: dict[str, tuple[str, TypeId]] = {}
    path = program.source_path or next(
        (
            function.span.path
            for function in program.functions
            if function.name == entry_function
        ),
        "main.mlo",
    )
    from merlo.surface_elaborator import surface_lowering_module

    module, declaration_kinds, binding_kinds = surface_lowering_module(
        program.surface_program,
        program,
    )
    try:
        ffi_program = validate_ffi(source, path=path)
    except FFICompileError as exc:
        raise StructuredHIRCompileError(str(exc)) from exc
    preprocessed = _Preprocessed(
        source,
        dict(declaration_kinds),
        dict(binding_kinds),
    )
    _intern_ffi_types(ffi_program, type_arena, type_cache, path)
    types = _parse_type_declarations(
        path,
        module,
        preprocessed.declaration_kinds,
        type_arena,
        type_cache,
    )
    function_nodes = {
        item.name: item
        for item in module.body
        if isinstance(item, ast.FunctionDef)
    }
    unsupported = [
        type(item).__name__
        for item in module.body
        if not isinstance(item, (ast.ClassDef, ast.FunctionDef))
    ]
    if unsupported:
        raise StructuredHIRCompileError(
            f"unsupported top-level declarations: {unsupported}"
        )
    flows, machines = _canonical_flow_machine_hir(program, type_arena, type_cache)
    if entry_function not in function_nodes and not (flows or machines):
        raise StructuredHIRCompileError(
            f"missing entry function: {entry_function}"
        )
    _preintern_analysis_types(function_nodes, type_arena, type_cache, path)
    for item in types.values():
        type_arena.register_declaration(_type_declaration_projection(item))
    borrow_summaries = compute_borrow_summaries(
        function_nodes,
        types,
        type_arena,
    )
    _OwnershipChecker(
        path,
        types,
        function_nodes,
        borrow_summaries,
        preprocessed.binding_kinds,
        type_context=type_arena,
        compile_error=StructuredHIRCompileError,
        type_name=_type_name,
        stable_id=_stable_id,
        is_borrowed=_is_borrowed,
        qualified_name=_ast_qualified_name,
    ).check()
    builder = _HIRBuilder(
        path,
        source,
        preprocessed,
        types,
        function_nodes,
        borrow_summaries=borrow_summaries,
        type_arena=type_arena,
        type_cache=type_cache,
    )
    functions = tuple(
        builder.function(item) for item in function_nodes.values()
    )
    for node in ast.walk(module):
        if hasattr(node, "_merlo_binding_kind"):
            delattr(node, "_merlo_binding_kind")
    _normalize_native_context_metadata(module, path)
    type_context = type_arena.freeze()
    frozen_arena = type_context.arena
    return StructuredHIRProgram(
        source=source,
        path=path,
        source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        types=tuple(types.values()),
        functions=functions,
        entry_function=entry_function,
        native_syntax_json=ast.module_to_json(module),
        ffi_program=ffi_program,
        type_context=type_context,
        type_arena_digest=frozen_arena.digest,
        native_module=module,
        flows=flows,
        machines=machines,
    )


def compile_structured_hir_file(path: str | Path) -> StructuredHIRProgram:
    source_path = Path(path)
    return compile_structured_hir(source_path.read_text(encoding="utf-8"), path=str(source_path))


__all__ = [
    "HIRField",
    "HIRInvariant",
    "HIRFunction",
    "HIRFlow",
    "HIRMachine",
    "HIRMachineStateField",
    "HIRNode",
    "HIRParameter",
    "HIRTypeDecl",
    "HIRVariant",
    "BorrowSummary",
    "SourceSpan",
    "StructuredHIRCompileError",
    "StructuredHIRProgram",
    "STRUCTURED_HIR_CONTRACT",
    "STRUCTURED_HIR_SCHEMA_VERSION",
    "FrozenTypeArena",
    "TypeArena",
    "TypeContext",
    "TypeId",
    "TypeArenaError",
    "compile_structured_hir",
    "compile_canonical_hir",
    "compile_structured_hir_file",
]
