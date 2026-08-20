"""Structured Typed HIR v3 for Merlo's general representation milestone.

The HIR is deliberately a tree. Control-flow graphs, allocation primitives, drop
flags, and pointer arithmetic belong to lower layers.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
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
from merlo.type_parser import generic_parts, parse_type, validate_type_expr
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
from merlo.place import (
    IndexClass,
    OverlapRelation,
    Place,
    PlaceRoot,
    PlaceStep,
    overlap_relation,
)


STRUCTURED_HIR_SCHEMA_VERSION = 9
STRUCTURED_HIR_CONTRACT = "merlo.structured-typed-hir.v9"
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
    source: SourceSpan
    symbol_id: str
    revision_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type_name,
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class HIRVariant:
    name: str
    payload_type: str | None
    tag: int
    source: SourceSpan
    symbol_id: str
    revision_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "payload_type": self.payload_type,
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
            "kind": self.kind,
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "fields": [item.to_dict() for item in self.fields],
            "variants": [item.to_dict() for item in self.variants],
            "invariants": [
                item.to_dict() for item in self.invariants
            ],
        }


@dataclass(frozen=True)
class HIRParameter:
    name: str
    type_name: str
    ownership: str
    source: SourceSpan
    symbol_id: str
    revision_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type_name,
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
            "effects": list(self.effects),
            "requirements": [
                item.to_dict() for item in self.requirements
            ],
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
            "durable": self.durable,
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
            "body": [item.to_dict() for item in self.body],
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class HIRMachine:
    name: str
    parameters: tuple[HIRParameter, ...]
    states: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
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
                {"name": name, "fields": [list(field) for field in fields]}
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


def _field_from_dict(value: object) -> HIRField:
    raw = _artifact_keys(
        value,
        {"name", "type", "source", "symbol_id", "revision_id"},
        "HIR field",
    )
    return HIRField(
        _artifact_text(raw["name"], "field name"),
        _artifact_text(raw["type"], "field type"),
        _source_span_from_dict(raw["source"]),
        _artifact_text(raw["symbol_id"], "field symbol"),
        _artifact_text(raw["revision_id"], "field revision"),
    )


def _variant_from_dict(value: object) -> HIRVariant:
    raw = _artifact_keys(
        value,
        {"name", "payload_type", "tag", "source", "symbol_id", "revision_id"},
        "HIR variant",
    )
    return HIRVariant(
        _artifact_text(raw["name"], "variant name"),
        _artifact_optional_text(raw["payload_type"], "variant payload"),
        _artifact_int(raw["tag"], "variant tag"),
        _source_span_from_dict(raw["source"]),
        _artifact_text(raw["symbol_id"], "variant symbol"),
        _artifact_text(raw["revision_id"], "variant revision"),
    )


def _invariant_from_dict(value: object) -> HIRInvariant:
    raw = _artifact_keys(
        value,
        {"function_name", "expression", "source", "revision_id"},
        "HIR invariant",
    )
    return HIRInvariant(
        _artifact_text(raw["function_name"], "invariant function"),
        _artifact_text(raw["expression"], "invariant expression"),
        _source_span_from_dict(raw["source"]),
        _artifact_text(raw["revision_id"], "invariant revision"),
    )


def _type_decl_from_dict(value: object) -> HIRTypeDecl:
    raw = _artifact_keys(
        value,
        {
            "name", "kind", "source", "symbol_id", "revision_id", "fields",
            "variants", "invariants",
        },
        "HIR type",
    )
    return HIRTypeDecl(
        _artifact_text(raw["name"], "type name"),
        _artifact_text(raw["kind"], "type kind"),
        _source_span_from_dict(raw["source"]),
        _artifact_text(raw["symbol_id"], "type symbol"),
        _artifact_text(raw["revision_id"], "type revision"),
        tuple(
            _field_from_dict(item)
            for item in _artifact_list(raw["fields"], "type fields")
        ),
        tuple(
            _variant_from_dict(item)
            for item in _artifact_list(raw["variants"], "type variants")
        ),
        tuple(
            _invariant_from_dict(item)
            for item in _artifact_list(raw["invariants"], "type invariants")
        ),
    )


def _parameter_from_dict(value: object) -> HIRParameter:
    raw = _artifact_keys(
        value,
        {"name", "type", "ownership", "source", "symbol_id", "revision_id"},
        "HIR parameter",
    )
    return HIRParameter(
        _artifact_text(raw["name"], "parameter name"),
        _artifact_text(raw["type"], "parameter type"),
        _artifact_text(raw["ownership"], "parameter ownership"),
        _source_span_from_dict(raw["source"]),
        _artifact_text(raw["symbol_id"], "parameter symbol"),
        _artifact_text(raw["revision_id"], "parameter revision"),
    )


def _node_from_dict(value: object) -> HIRNode:
    raw = _artifact_keys(
        value,
        {
            "id", "kind", "source", "scope_id", "type", "ownership", "effects",
            "symbol_id", "revision_id", "attributes", "children",
        },
        "HIR node",
    )
    attributes = raw["attributes"]
    if not isinstance(attributes, Mapping) or not all(
        isinstance(key, str) for key in attributes
    ):
        raise ValueError("HIR node attributes must be a string-keyed object")
    return HIRNode(
        _artifact_text(raw["id"], "node id"),
        _artifact_text(raw["kind"], "node kind"),
        _source_span_from_dict(raw["source"]),
        _artifact_text(raw["scope_id"], "node scope"),
        _artifact_optional_text(raw["type"], "node type"),
        _artifact_text(raw["ownership"], "node ownership"),
        tuple(
            _artifact_text(item, "node effect")
            for item in _artifact_list(raw["effects"], "node effects")
        ),
        _artifact_optional_text(raw["symbol_id"], "node symbol"),
        _artifact_text(raw["revision_id"], "node revision"),
        tuple(
            (key, _freeze_json(attributes[key], "HIR attribute"))
            for key in sorted(attributes)
        ),
        tuple(
            _node_from_dict(item)
            for item in _artifact_list(raw["children"], "node children")
        ),
    )


def _contract_from_dict(value: object) -> HIRContract:
    raw = _artifact_keys(
        value,
        {"kind", "expression", "condition", "source"},
        "HIR contract",
    )
    return HIRContract(
        _artifact_text(raw["kind"], "contract kind"),
        _artifact_text(raw["expression"], "contract expression"),
        _node_from_dict(raw["condition"]),
        _source_span_from_dict(raw["source"]),
    )


def _function_from_dict(value: object) -> HIRFunction:
    raw = _artifact_keys(
        value,
        {
            "name", "parameters", "return_type", "effects", "requirements",
            "ensures", "body", "source", "scope_id", "symbol_id", "revision_id",
            "borrow_summary",
        },
        "HIR function",
    )
    return HIRFunction(
        _artifact_text(raw["name"], "function name"),
        tuple(
            _parameter_from_dict(item)
            for item in _artifact_list(raw["parameters"], "function parameters")
        ),
        _artifact_text(raw["return_type"], "function return type"),
        tuple(
            _artifact_text(item, "function effect")
            for item in _artifact_list(raw["effects"], "function effects")
        ),
        tuple(
            _contract_from_dict(item)
            for item in _artifact_list(raw["requirements"], "requirements")
        ),
        tuple(
            _contract_from_dict(item)
            for item in _artifact_list(raw["ensures"], "ensures")
        ),
        tuple(
            _node_from_dict(item)
            for item in _artifact_list(raw["body"], "function body")
        ),
        _source_span_from_dict(raw["source"]),
        _artifact_text(raw["scope_id"], "function scope"),
        _artifact_text(raw["symbol_id"], "function symbol"),
        _artifact_text(raw["revision_id"], "function revision"),
        BorrowSummary.from_dict(
            _artifact_keys(
                raw["borrow_summary"],
                {"schema_version", "contract", "status", "reason", "entries"},
                "borrow summary",
            )
        ),
    )


def _flow_from_dict(value: object) -> HIRFlow:
    raw = _artifact_keys(
        value,
        {
            "name", "parameters", "return_type", "durable", "effects",
            "capabilities", "body", "source", "symbol_id", "revision_id",
        },
        "HIR flow",
    )
    return HIRFlow(
        _artifact_text(raw["name"], "flow name"),
        tuple(
            _parameter_from_dict(item)
            for item in _artifact_list(raw["parameters"], "flow parameters")
        ),
        _artifact_text(raw["return_type"], "flow return type"),
        _artifact_bool(raw["durable"], "flow durable"),
        tuple(
            _artifact_text(item, "flow effect")
            for item in _artifact_list(raw["effects"], "flow effects")
        ),
        tuple(
            _artifact_text(item, "flow capability")
            for item in _artifact_list(raw["capabilities"], "flow capabilities")
        ),
        tuple(
            _node_from_dict(item)
            for item in _artifact_list(raw["body"], "flow body")
        ),
        _source_span_from_dict(raw["source"]),
        _artifact_text(raw["symbol_id"], "flow symbol"),
        _artifact_text(raw["revision_id"], "flow revision"),
    )


def _machine_from_dict(value: object) -> HIRMachine:
    raw = _artifact_keys(
        value,
        {
            "name", "parameters", "states", "initial", "invariant", "transitions",
            "source", "symbol_id", "revision_id",
        },
        "HIR machine",
    )
    states = []
    for item in _artifact_list(raw["states"], "machine states"):
        state = _artifact_keys(item, {"name", "fields"}, "machine state")
        fields = []
        for field_value in _artifact_list(state["fields"], "machine state fields"):
            field = _artifact_list(field_value, "machine state field")
            if len(field) != 2:
                raise ValueError("machine state field must have name and type")
            fields.append(
                (
                    _artifact_text(field[0], "machine field name"),
                    _artifact_text(field[1], "machine field type"),
                )
            )
        states.append((_artifact_text(state["name"], "state name"), tuple(fields)))
    return HIRMachine(
        _artifact_text(raw["name"], "machine name"),
        tuple(
            _parameter_from_dict(item)
            for item in _artifact_list(raw["parameters"], "machine parameters")
        ),
        tuple(states),
        _artifact_optional_text(raw["initial"], "machine initial state"),
        _artifact_optional_text(raw["invariant"], "machine invariant"),
        tuple(
            _node_from_dict(item)
            for item in _artifact_list(raw["transitions"], "machine transitions")
        ),
        _source_span_from_dict(raw["source"]),
        _artifact_text(raw["symbol_id"], "machine symbol"),
        _artifact_text(raw["revision_id"], "machine revision"),
    )

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
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "path": self.path,
            "source": self.source,
            "source_sha256": self.source_sha256,
            "entry_function": self.entry_function,
            "native_syntax": json.loads(self.native_syntax_json),
            "ffi": self.ffi_program.to_dict(),
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
                "entry_function", "native_syntax", "ffi", "types", "functions",
                "flows", "machines", "invariants",
            },
            "Structured HIR",
        )
        if raw["schema_version"] != STRUCTURED_HIR_SCHEMA_VERSION:
            raise ValueError("Structured HIR schema version drift")
        if raw["contract"] != STRUCTURED_HIR_CONTRACT:
            raise ValueError("Structured HIR contract drift")
        if raw["invariants"] != invariants:
            raise ValueError("Structured HIR invariants drift")
        native = _artifact_keys(
            raw["native_syntax"],
            {"schema_version", "contract", "module"},
            "native syntax artifact",
        )
        native_module = ast.module_from_dict(native)
        native_json = ast.module_to_json(native_module)
        ffi = _artifact_keys(
            raw["ffi"],
            {"abi", "extern_functions", "repr_c_records", "unsafe_operations"},
            "FFI artifact",
        )
        program = cls(
            _artifact_text(raw["source"], "Structured HIR source"),
            _artifact_text(raw["path"], "Structured HIR path"),
            _artifact_text(raw["source_sha256"], "Structured HIR source digest"),
            tuple(
                _type_decl_from_dict(item)
                for item in _artifact_list(raw["types"], "HIR types")
            ),
            tuple(
                _function_from_dict(item)
                for item in _artifact_list(raw["functions"], "HIR functions")
            ),
            _artifact_text(raw["entry_function"], "HIR entry function"),
            native_json,
            FFIProgram.from_dict(ffi),
            flows=tuple(
                _flow_from_dict(item)
                for item in _artifact_list(raw["flows"], "HIR flows")
            ),
            machines=tuple(
                _machine_from_dict(item)
                for item in _artifact_list(raw["machines"], "HIR machines")
            ),
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


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{prefix}_{hashlib.sha256(payload.encode()).hexdigest()}"


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


def _is_borrowed(type_name: str | None) -> bool:
    return bool(type_name) and (
        type_name in {"BytesView", "TextView", "FileLines"}
        or type_name.startswith(("Slice[", "Borrow["))
    )


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


def _assigned_parameter_names(function: ast.FunctionDef) -> set[str]:
    parameters = {item.arg for item in function.args.args}
    assigned: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            root: ast.AST = node.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in parameters:
                assigned.add(root.id)
        if isinstance(node, ast.Call) and isinstance(
            node.func,
            ast.Attribute,
        ):
            root = node.func.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if (
                isinstance(root, ast.Name)
                and root.id in parameters
                and node.func.attr
                in {
                    "push",
                    "get_mut",
                    "append_byte",
                    "append_scalar",
                    "append_text",
                    "append_uint64",
                    "insert",
                }
            ):
                assigned.add(root.id)
    return assigned


@dataclass
class _OwnershipState:
    statuses: dict[str, str]
    borrows: dict[str, tuple["_BorrowProvenance", ...]]
    places: dict[str, Place]
    terminal: bool = False
    borrow_places: dict[str, Place] = field(default_factory=dict)
    break_paths: tuple["_OwnershipState", ...] = ()
    backedge_paths: tuple["_OwnershipState", ...] = ()

    def clone(self) -> "_OwnershipState":
        return _OwnershipState(
            dict(self.statuses),
            dict(self.borrows),
            dict(self.places),
            self.terminal,
            dict(self.borrow_places),
            tuple(path.clone() for path in self.break_paths),
            tuple(path.clone() for path in self.backedge_paths),
        )


@dataclass(frozen=True, order=True)
class _BorrowProvenance:
    backing_place: Place
    borrow_type: str
    escape_path: tuple[str, ...]
    backing_owner_name: str = field(default="", compare=False)

    @property
    def backing_owner(self) -> str:
        """Legacy diagnostic spelling; semantic identity is ``backing_place``."""
        return self.backing_owner_name or self.backing_place.root.symbol_id
class _OwnershipChecker:
    """Conservative source ownership analysis used to gate HIR construction."""

    def __init__(
        self,
        path: str,
        types: dict[str, HIRTypeDecl],
        functions: dict[str, ast.FunctionDef],
        borrow_summaries: dict[str, BorrowSummary] | None = None,
        binding_kinds: Mapping[int, str] | None = None,
    ) -> None:
        self.path = path
        self.types = types
        self.functions = functions
        self.borrow_summaries = borrow_summaries or {}
        self.binding_kinds = binding_kinds or {}
        self.binding_kinds_by_name: dict[str, str] = {}
        self.current: ast.FunctionDef | None = None
        self.type_properties = TypePropertyResolver(types)
        self.env: dict[str, str] = {}
        self.parameters: set[str] = set()
    def _error(self, name: str, variable: str | None = None) -> None:
        suffix = f": {variable}" if variable else ""
        raise StructuredHIRCompileError(f"{name}{suffix}")

    def _owner(self, type_name: str | None) -> bool:
        return self.type_properties.resolve(type_name).needs_drop

    def _contains_borrow(self, type_name: str | None) -> bool:
        return self.type_properties.resolve(type_name).contains_borrow

    def _borrow_type(self, type_name: str | None) -> str:
        properties = self.type_properties.resolve(type_name)
        return properties.borrow_types[0] if properties.borrow_types else str(type_name)
    def _root_place(self, name: str) -> Place:
        is_parameter = name in self.parameters
        kind = "parameter" if is_parameter else "local"
        root = (
            PlaceRoot.param
            if is_parameter
            else PlaceRoot.local
        )(
            _stable_id(
                "shirs",
                self.path,
                self.current.name if self.current is not None else "",
                kind,
                name,
            )
        )
        return Place(root)
    def _is_parameter_place(self, place: Place) -> bool:
        return any(
            place.root == self._root_place(name).root
            for name in self.parameters
        )

    def _field_id(self, type_name: str | None, field_name: str) -> str | None:
        declaration = self.types.get(type_name or "")
        if declaration is None or declaration.kind != "record":
            return None
        field = next(
            (
                item
                for item in declaration.fields
                if item.name == field_name or item.symbol_id == field_name
            ),
            None,
        )
        return field.symbol_id if field is not None else None

    def _field_type(self, type_name: str | None, field_name: str) -> str | None:
        declaration = self.types.get(type_name or "")
        if declaration is None or declaration.kind != "record":
            return None
        field = next(
            (
                item
                for item in declaration.fields
                if item.name == field_name or item.symbol_id == field_name
            ),
            None,
        )
        return field.type_name if field is not None else None

    def _variant_id(self, type_name: str | None, variant_name: str) -> str:
        declaration = self.types.get(type_name or "")
        if declaration is not None and declaration.kind == "enum":
            variant = next(
                (
                    item
                    for item in declaration.variants
                    if item.name == variant_name or item.symbol_id == variant_name
                ),
                None,
            )
            if variant is not None:
                return variant.symbol_id
        return _stable_id("variant", type_name or "", variant_name)

    def _variant_payload_type(
        self,
        type_name: str | None,
        variant_name: str,
    ) -> str | None:
        declaration = self.types.get(type_name or "")
        if declaration is not None and declaration.kind == "enum":
            variant = next(
                (
                    item
                    for item in declaration.variants
                    if item.name == variant_name or item.symbol_id == variant_name
                ),
                None,
            )
            return variant.payload_type if variant is not None else None
        option = generic_parts(type_name or "", "Option", arity=1)
        if option is not None and variant_name in {"Some", "unwrap"}:
            return option[0]
        result = generic_parts(type_name or "", "Result", arity=2)
        if result is not None and variant_name in {"Ok", "Err", "unwrap", "unwrap_err"}:
            return result[0 if variant_name in {"Ok", "unwrap"} else 1]
        return None

    def _substitute_summary_place(
        self,
        place: Place,
        type_name: str | None,
        source_path: Any,
    ) -> Place | None:
        result = place
        current_type = type_name
        for step in source_path.steps[1:]:
            if step.kind == "Field":
                field_name = str(step.value)
                field_id = self._field_id(current_type, field_name)
                if field_id is None:
                    return None
                result = result.project(PlaceStep.field(field_id))
                current_type = self._field_type(current_type, field_name)
            elif step.kind == "Element":
                shape = collection_shape(current_type)
                if shape is None:
                    return None
                result = result.project(PlaceStep.index(IndexClass.dynamic()))
                current_type = shape.element_type
            elif step.kind == "VariantPayload":
                variant_name = str(step.value)
                if variant_name == "unwrap":
                    variant_name = "Some"
                elif variant_name == "unwrap_err":
                    variant_name = "Err"
                result = result.project(
                    PlaceStep.variant_payload(
                        self._variant_id(current_type, variant_name)
                    )
                )
                current_type = self._variant_payload_type(
                    current_type,
                    variant_name,
                )
                if current_type is None:
                    return None
            elif step.kind == "Deref":
                parts = generic_parts(current_type or "", "Box", arity=1)
                if parts is None:
                    return None
                result = result.project(PlaceStep.dereference())
                current_type = parts[0]
            elif step.kind == "RecursiveTail":
                return None
            else:
                return None
        return result

    def _storage_type(self, name: str) -> str | None:
        parts = name.split(".")
        current = self.env.get(parts[0])
        for field_name in parts[1:]:
            current = self._field_type(current, field_name)
            if current is None:
                return None
        return current
    def _binding_place(
        self,
        node: ast.AST | None,
        state: _OwnershipState,
    ) -> Place | None:
        if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
            return self._place_for_expr(node, state)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                node.func.attr in {"get", "get_mut"}
                and not self._contains_borrow(self._expr_type(node))
            ):
                return None
            if node.func.attr in {
                "get",
                "get_mut",
                "byte",
                "view",
                "as_view",
                "slice",
                "slice_bytes",
                "lines",
                "entries",
            }:
                return self._place_for_expr(node, state)
        return None
    def _register_record_borrows(
        self,
        state: _OwnershipState,
        name: str,
        type_name: str,
        value: ast.AST,
        fallback: tuple[_BorrowProvenance, ...],
    ) -> None:
        declaration = self.types.get(type_name)
        if not (
            declaration is not None
            and declaration.kind == "record"
            and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == type_name
        ):
            self._register_borrows(
                state,
                name,
                self._extend_provenance(fallback, f"bind({name}:{type_name})"),
                place=self._root_place(name),
            )
            return
        root_place = self._root_place(name)
        registered = False
        for field_decl, argument in zip(declaration.fields, value.args, strict=False):
            field_provenances = self._borrow_provenances(
                argument,
                field_decl.type_name,
                state,
            )
            if not field_provenances:
                continue
            registered = True
            field_place = root_place.project(PlaceStep.field(field_decl.symbol_id))
            self._register_borrows(
                state,
                f"{name}.{field_decl.name}",
                self._extend_provenance(
                    field_provenances,
                    f"bind({name}.{field_decl.name}:{field_decl.type_name})",
                ),
                place=field_place,
            )
        if not registered and fallback:
            self._register_borrows(
                state,
                name,
                self._extend_provenance(fallback, f"bind({name}:{type_name})"),
                place=root_place,
            )
        return None


    @staticmethod
    def _index_class(node: ast.AST | None) -> IndexClass:
        if isinstance(node, ast.Constant) and type(node.value) is int:
            try:
                return IndexClass.constant(node.value)
            except ValueError:
                pass
        return IndexClass.dynamic()

    def _place_for_expr(
        self,
        node: ast.AST | None,
        state: _OwnershipState,
    ) -> Place | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return state.places.get(node.id) or (
                self._root_place(node.id) if node.id in self.env else None
            )
        if isinstance(node, ast.Attribute):
            receiver = self._place_for_expr(node.value, state)
            field_id = self._field_id(self._expr_type(node.value), node.attr)
            if receiver is None or field_id is None:
                return None
            return receiver.project(PlaceStep.field(field_id))
        if isinstance(node, ast.Subscript):
            receiver = self._place_for_expr(node.value, state)
            if receiver is None:
                return None
            return receiver.project(PlaceStep.index(self._index_class(node.slice)))
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                receiver = self._place_for_expr(node.func.value, state)
                if receiver is None:
                    return None
                method = node.func.attr
                if method in {"get", "get_mut", "byte"} and node.args:
                    return receiver.project(
                        PlaceStep.index(self._index_class(node.args[0]))
                    )
                if (
                    method in {"get", "get_mut"}
                    and not node.args
                    and generic_parts(
                        self._expr_type(node.func.value) or "",
                        "Box",
                        arity=1,
                    )
                ):
                    return receiver.project(PlaceStep.dereference())
                if method in {"unwrap", "unwrap_err"}:
                    receiver_type = self._expr_type(node.func.value)
                    variant = (
                        "Err"
                        if method == "unwrap_err"
                        else "Ok"
                        if generic_parts(receiver_type, "Result", arity=2)
                        else "Some"
                    )
                    return receiver.project(
                        PlaceStep.variant_payload(
                            self._variant_id(receiver_type, variant)
                        )
                    )
                if method in {
                    "view",
                    "as_view",
                    "slice",
                    "slice_bytes",
                    "lines",
                    "entries",
                }:
                    return receiver
            for argument in node.args:
                candidate = self._place_for_expr(argument, state)
                if candidate is not None:
                    return candidate
        return None

    def _contained_borrow_error(
        self,
        code: str,
        *,
        container_type: str | None,
        provenance: _BorrowProvenance,
        path: str,
    ) -> None:
        complete_path = " -> ".join((*provenance.escape_path, path))
        callee = next(
            (
                item
                for item in provenance.escape_path
                if item.startswith("formal[")
            ),
            None,
        )
        callee_prefix = ""
        if callee is not None:
            index = provenance.escape_path.index(callee)
            if index:
                callee_prefix = f"callee={provenance.escape_path[index - 1]}; "
        raise StructuredHIRCompileError(
            f"{code}: {callee_prefix}container={container_type}; "
            f"contained_borrow={provenance.borrow_type}; "
            f"backing_owner={provenance.backing_owner}; "
            f"backing_place={provenance.backing_place.to_json().rstrip()}; "
            f"escape_path={complete_path}"
        )

    def _borrow_summary_error(
        self,
        code: str,
        *,
        callee: str,
        formal: str,
        actual: str,
        result_type: str | None,
        borrow_type: str | None = None,
        path: tuple[str, ...] = (),
    ) -> None:
        escape = " -> ".join(path) if path else "call"
        raise StructuredHIRCompileError(
            f"{code}: callee={callee}; formal={formal}; actual_owner={actual}; "
            f"returned_container={result_type}; contained_borrow={borrow_type or result_type}; "
            f"escape_path={escape}"
        )

    def _summary_provenances(
        self,
        node: ast.Call,
        result_type: str | None,
        state: _OwnershipState,
    ) -> tuple[_BorrowProvenance, ...]:
        if not isinstance(node.func, ast.Name) or node.func.id not in self.functions:
            return ()
        callee_name = node.func.id
        summary = self.borrow_summaries.get(callee_name)
        if summary is None:
            if self._contains_borrow(result_type):
                self._borrow_summary_error(
                    "OpaqueBorrowSummary",
                    callee=callee_name,
                    formal="<unknown>",
                    actual="<unknown>",
                    result_type=result_type,
                    path=(callee_name, "opaque"),
                )
            return ()
        if summary.opaque:
            if self._contains_borrow(result_type):
                self._borrow_summary_error(
                    "OpaqueBorrowSummary",
                    callee=callee_name,
                    formal="<summary>",
                    actual="<unknown>",
                    result_type=result_type,
                    path=(callee_name, summary.reason or "opaque"),
                )
            return ()
        provenances: list[_BorrowProvenance] = []
        for entry in summary.entries:
            relation = entry.relation
            parameter_index = relation.source_parameter_index
            if parameter_index >= len(node.args):
                self._borrow_summary_error(
                    "OpaqueBorrowSummary",
                    callee=callee_name,
                    formal=f"parameter[{parameter_index}]",
                    actual="<missing>",
                    result_type=result_type,
                    borrow_type=relation.borrow_type,
                    path=(callee_name, "arity"),
                )
            argument = node.args[parameter_index]
            formal = self.functions[callee_name].args.args[parameter_index].arg
            actual_name = self._root_name(argument) or self._stable_borrow_root(argument)
            actual_place = self._place_for_expr(argument, state)
            actual_type = self._expr_type(argument)
            if actual_name is None or actual_place is None:
                # A temporary owner has no stable place, regardless of which
                # function contains the call. The HIR ownership stage rejects
                # it before a backend escape gate can be reached.
                self._borrow_summary_error(
                    "BorrowFromTemporaryEscapes",
                    callee=callee_name,
                    formal=formal,
                    actual=ast.unparse(argument),
                    result_type=result_type,
                    borrow_type=relation.borrow_type,
                    path=(callee_name, formal, *relation.result_path.rendered()),
                )
            source_place = self._substitute_summary_place(
                actual_place,
                actual_type,
                relation.source_path,
            )
            if source_place is None:
                self._borrow_summary_error(
                    "OpaqueBorrowSummary",
                    callee=callee_name,
                    formal=formal,
                    actual=actual_name,
                    result_type=result_type,
                    borrow_type=relation.borrow_type,
                    path=(callee_name, formal, "unsupported-place"),
                )
            tracked = self._borrow_provenances_at(
                state,
                source_place,
                actual_name,
            )
            path = (
                callee_name,
                f"formal[{parameter_index}]={formal}",
                *relation.source_path.rendered()[1:],
                *relation.result_path.rendered(),
                *entry.witness_path,
            )
            if tracked and (
                relation.kind == "contained"
                or _is_borrowed(actual_type)
                or self._contains_borrow(actual_type)
            ):
                for item in tracked:
                    provenances.append(
                        _BorrowProvenance(
                            item.backing_place,
                            relation.borrow_type,
                            (*path, *item.escape_path),
                            item.backing_owner_name,
                        )
                    )
                continue
            if self._owner(actual_type) or _is_borrowed(actual_type):
                provenances.append(
                    _BorrowProvenance(
                        source_place,
                        relation.borrow_type,
                        (*path, actual_name),
                        actual_name,
                    )
                )
                continue
            if self._contains_borrow(result_type):
                self._borrow_summary_error(
                    "OpaqueBorrowSummary",
                    callee=callee_name,
                    formal=formal,
                    actual=actual_name,
                    result_type=result_type,
                    borrow_type=relation.borrow_type,
                    path=(callee_name, formal),
                )
        if self._contains_borrow(result_type) and summary.entries and not provenances:
            self._borrow_summary_error(
                "OpaqueBorrowSummary",
                callee=callee_name,
                formal="<summary>",
                actual="<unknown>",
                result_type=result_type,
                path=(callee_name, "unmapped"),
            )
        if self._contains_borrow(result_type) and not summary.entries:
            self._borrow_summary_error(
                "OpaqueBorrowSummary",
                callee=callee_name,
                formal="<summary>",
                actual="<unknown>",
                result_type=result_type,
                path=(callee_name, "missing-origin"),
            )
        return tuple(sorted(set(provenances)))

    def _reject_unknown_borrow_call(
        self,
        node: ast.Call,
        result_type: str | None,
    ) -> None:
        if not (
            isinstance(node.func, ast.Name)
            and node.func.id not in self.functions
            and node.func.id not in self.types
            and node.func.id not in {"Some", "None", "Ok", "Err", "drop"}
            and self._contains_borrow(result_type)
        ):
            return
        self._borrow_summary_error(
            "OpaqueBorrowSummary",
            callee=node.func.id,
            formal="<external>",
            actual="<unknown>",
            result_type=result_type,
            path=(node.func.id, "opaque"),
        )

    @staticmethod
    def _extend_provenance(
        provenances: tuple[_BorrowProvenance, ...],
        step: str,
    ) -> tuple[_BorrowProvenance, ...]:
        return tuple(
            _BorrowProvenance(
                item.backing_place,
                item.borrow_type,
                (*item.escape_path, step),
                item.backing_owner_name,
            )
            for item in provenances
        )
    def _borrow_provenances_at(
        self,
        state: _OwnershipState,
        target: Place,
        fallback_name: str | None = None,
    ) -> tuple[_BorrowProvenance, ...]:
        collected: list[_BorrowProvenance] = []
        for name, provenances in state.borrows.items():
            storage = state.borrow_places.get(name)
            if storage is None:
                if name == fallback_name:
                    collected.extend(provenances)
                continue
            relation = overlap_relation(target, storage)
            if relation in {
                OverlapRelation.EQUAL,
                OverlapRelation.ANCESTOR,
            }:
                collected.extend(provenances)
        return tuple(sorted(set(collected)))

    @staticmethod
    def _remove_borrows_at_place(
        state: _OwnershipState,
        target: Place,
    ) -> None:
        for name, place in tuple(state.borrow_places.items()):
            if place == target:
                state.borrows.pop(name, None)
                state.borrow_places.pop(name, None)

    @staticmethod
    def _borrow_storage_overlaps(
        state: _OwnershipState,
        target: Place,
    ) -> bool:
        return any(
            overlap_relation(target, place) is not OverlapRelation.DISJOINT
            for place in state.borrow_places.values()
            if place is not None
        )
    @staticmethod
    def _register_borrows(
        state: _OwnershipState,
        name: str,
        provenances: tuple[_BorrowProvenance, ...],
        place: Place | None = None,
    ) -> None:
        if provenances:
            state.borrows[name] = tuple(sorted(set(provenances)))
            state.borrow_places[name] = place or state.places.get(name)
        else:
            state.borrows.pop(name, None)
            state.borrow_places.pop(name, None)


    def _borrow_provenances(
        self,
        node: ast.AST | None,
        type_name: str | None,
        state: _OwnershipState,
    ) -> tuple[_BorrowProvenance, ...]:
        if node is None or not self._contains_borrow(type_name):
            return ()
        if isinstance(node, ast.Name):
            place = self._place_for_expr(node, state)
            tracked = (
                self._borrow_provenances_at(state, place, node.id)
                if place is not None
                else ()
            )
            if tracked:
                return self._extend_provenance(tracked, node.id)
            if place is not None:
                return (
                    _BorrowProvenance(
                        place,
                        self._borrow_type(type_name),
                        (node.id,),
                        node.id,
                    ),
                )
            return ()
        if isinstance(node, (ast.Attribute, ast.Subscript)):
            root = self._root_name(node)
            place = self._place_for_expr(node, state)
            tracked = (
                self._borrow_provenances_at(state, place, root)
                if place is not None
                else ()
            )
            if tracked:
                return self._extend_provenance(tracked, ast.unparse(node))
            place = self._place_for_expr(node, state)
            if place is not None:
                return (
                    _BorrowProvenance(
                        place,
                        self._borrow_type(type_name),
                        (ast.unparse(node),),
                        root or ast.unparse(node),
                    ),
                )
        if isinstance(node, ast.Call):
            step = ast.unparse(node.func)
            result_type = self._expr_type(node, type_name)
            if isinstance(node.func, ast.Name) and node.func.id in self.functions:
                summary_provenances = self._summary_provenances(
                    node,
                    result_type,
                    state,
                )
                if summary_provenances:
                    return tuple(
                        self._extend_provenance(summary_provenances, step)
                    )
            self._reject_unknown_borrow_call(node, result_type)
            if isinstance(node.func, ast.Attribute):
                receiver_root = self._root_name(node.func.value)
                candidate = self._place_for_expr(node.func.value, state)
                tracked = (
                    self._borrow_provenances_at(
                        state,
                        candidate,
                        receiver_root,
                    )
                    if candidate is not None
                    else ()
                )
                if tracked:
                    return self._extend_provenance(tracked, step)
                receiver_type = self._expr_type(node.func.value)
                if candidate is not None and (
                    self._owner(receiver_type)
                    or _is_borrowed(receiver_type)
                    or self._contains_borrow(type_name)
                ):
                    return (
                        _BorrowProvenance(
                            candidate,
                            self._borrow_type(type_name),
                            (ast.unparse(node.func.value), step),
                            receiver_root or ast.unparse(node.func.value),
                        ),
                    )
            collected: list[_BorrowProvenance] = []
            for argument in node.args:
                argument_type = self._expr_type(argument)
                # Constructors carry the only useful expected type for a
                # payload in this syntax. Preserve that type while walking
                # the argument so a borrow nested in Box/Option/Result is
                # attributed to its real backing place.
                if argument_type is None:
                    qualified = _ast_qualified_name(node.func)
                    if qualified == "Box.new":
                        parts = generic_parts(result_type or "", "Box", arity=1)
                        argument_type = parts[0] if parts is not None else None
                    elif qualified == "Some":
                        parts = generic_parts(result_type or "", "Option", arity=1)
                        argument_type = parts[0] if parts is not None else None
                    elif qualified in {"Ok", "Err"}:
                        parts = generic_parts(result_type or "", "Result", arity=2)
                        if parts is not None:
                            argument_type = parts[0 if qualified == "Ok" else 1]
                tracked = self._borrow_provenances(
                    argument,
                    argument_type,
                    state,
                )
                collected.extend(self._extend_provenance(tracked, step))
            return tuple(sorted(set(collected)))
        return ()
    def _live_borrow_of(
        self,
        state: _OwnershipState,
        owner: str | Place,
        *,
        nested_only: bool = False,
    ) -> tuple[str, _BorrowProvenance] | None:
        target = (
            state.places.get(owner) or self._root_place(owner)
            if isinstance(owner, str)
            else owner
        )
        return next(
            (
                (container, provenance)
                for container, provenances in sorted(state.borrows.items())
                for provenance in provenances
                if overlap_relation(provenance.backing_place, target)
                is not OverlapRelation.DISJOINT
                and (
                    not nested_only
                    or not _is_borrowed(self.env.get(container))
                    or any(
                        step.startswith("formal[")
                        for step in provenance.escape_path
                    )
                )
            ),
            None,
        )

    def _expr_type(self, node: ast.AST | None, expected: str | None = None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return self.env.get(node.id)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return "Bool"
            if isinstance(node.value, int):
                return "UInt64"
            if isinstance(node.value, float):
                return "Float64"
            if isinstance(node.value, str):
                return "Text"
            return "Unit"
        if isinstance(node, ast.Attribute):
            owner = self._expr_type(node.value)
            if owner in self.types and self.types[owner].kind == "record":
                return next(
                    (field.type_name for field in self.types[owner].fields if field.name == node.attr),
                    None,
                )
            return owner if isinstance(node.value, ast.Name) and node.value.id in self.types else None
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                name = node.func.id
                if name in self.functions:
                    return _type_name(self.functions[name].returns)
                if name in self.types:
                    return name
                if name in {"Text", "Bytes", "Path", "TextBuilder"}:
                    return name
                if name == "drop":
                    return "Unit"
            if isinstance(node.func, ast.Attribute):
                receiver = self._expr_type(node.func.value)
                method = node.func.attr
                receiver_text = _ast_qualified_name(node.func.value)
                static_signature = CONTRACT_GRAPH.static_method(
                    receiver_text,
                    method,
                )
                if static_signature is not None:
                    resolved_static = CONTRACT_GRAPH.resolve_static_method(
                        receiver_text,
                        method,
                        tuple(self._expr_type(argument) for argument in node.args),
                        expected,
                    )
                    return (
                        resolved_static.result_type
                        if resolved_static is not None
                        else expected
                    )
                method_signature = CONTRACT_GRAPH.method(
                    receiver or "",
                    method,
                )
                if method_signature is not None:
                    return method_signature.result_for(expected)
        if isinstance(node, ast.Subscript):
            owner = self._expr_type(node.value)
            shape = collection_shape(owner)
            if shape is not None:
                return shape.element_type
            return expected
        return expected

    @staticmethod
    def _root_name(node: ast.AST | None) -> str | None:
        while isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None

    @classmethod
    def _stable_borrow_root(cls, node: ast.AST | None) -> str | None:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"view", "as_view", "slice", "slice_bytes"}
        ):
            return cls._root_name(node.func.value)
        return None

    @staticmethod
    def _borrow_source(node: ast.AST | None) -> ast.AST | None:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return node.func.value
        return node

    def _check_name(self, name: str, state: _OwnershipState) -> None:
        status = state.statuses.get(name)
        if status == "ambiguous":
            self._error("OwnershipAmbiguity", name)
        if status == "moved":
            self._error("UseAfterMove", name)
        if status == "dropped":
            self._error("UseAfterDrop", name)
    def _consume_place(
        self,
        place: Place,
        state: _OwnershipState,
        *,
        name: str | None = None,
        display: str | None = None,
    ) -> None:
        if name is not None:
            self._check_name(name, state)
        live = self._live_borrow_of(state, place)
        if live is not None:
            container, provenance = live
            self._contained_borrow_error(
                "BackingOwnerMoveWhileBorrowed",
                container_type=self._storage_type(container),
                provenance=provenance,
                path=f"move({display or name or place})",
            )
        if (
            name is not None
            and name in state.statuses
            and place == (state.places.get(name) or self._root_place(name))
        ):
            state.statuses[name] = "moved"
            state.borrows.pop(name, None)
            state.borrow_places.pop(name, None)

    def _consume(self, name: str, state: _OwnershipState) -> None:
        self._consume_place(
            state.places.get(name) or self._root_place(name),
            state,
            name=name,
            display=name,
        )
    def _reject_projected_owner_move(
        self,
        place: Place | None,
        type_name: str | None,
        *,
        allow_borrowed_or_temporary_parent: bool = False,
    ) -> None:
        if (
            place is not None
            and place.steps
            and self._owner(type_name)
            and not _is_borrowed(type_name)
            and not allow_borrowed_or_temporary_parent
        ):
            self._error("ProjectedOwnerMoveRequiresPartialMoveSupport")


    def _consume_expr(
        self,
        node: ast.AST,
        state: _OwnershipState,
        *,
        expected: str | None = None,
        allow_projected_owner_move: bool = False,
    ) -> None:
        if isinstance(node, ast.Call):
            qualified = _ast_qualified_name(node.func)
            if qualified in {"Some", "Ok", "Err"}:
                for argument in node.args:
                    argument_type = self._expr_type(argument)
                    if self._owner(argument_type):
                        self._consume_expr(
                            argument,
                            state,
                            expected=argument_type,
                            allow_projected_owner_move=allow_projected_owner_move,
                        )
            elif isinstance(node.func, ast.Name) and node.func.id in self.types:
                declaration = self.types[node.func.id]
                if declaration.kind == "record":
                    for field_decl, argument in zip(
                        declaration.fields,
                        node.args,
                        strict=False,
                    ):
                        if self._owner(field_decl.type_name):
                            self._consume_expr(
                                argument,
                                state,
                                expected=field_decl.type_name,
                                allow_projected_owner_move=allow_projected_owner_move,
                            )
            if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                receiver = node.func.value
                receiver_type = self._expr_type(receiver)
                receiver_generic = generic_parts(receiver_type or "", "Box", arity=1)
                result_type = self._expr_type(node, expected)
                if (
                    receiver_generic is not None
                    and self._owner(result_type)
                ):
                    self._reject_projected_owner_move(
                        self._place_for_expr(node, state),
                        result_type,
                        allow_borrowed_or_temporary_parent=isinstance(
                            receiver,
                            ast.Call,
                        ),
                    )
            return
        if not isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
            return
        root = self._root_name(node)
        if root is not None:
            self._check_name(root, state)
        place = self._place_for_expr(node, state)
        self._reject_projected_owner_move(
            place,
            self._expr_type(node, expected),
            allow_borrowed_or_temporary_parent=(
                allow_projected_owner_move and root in self.parameters
            ),
        )
        self._consume_place(
            place,
            state,
            name=root,
            display=ast.unparse(node),
        )

    def _reject_projected_owner_drop(
        self,
        place: Place | None,
        type_name: str | None,
    ) -> None:
        if (
            place is not None
            and place.steps
            and self._owner(type_name)
            and not _is_borrowed(type_name)
        ):
            self._error("ProjectedOwnerDropRequiresPartialMoveSupport")

    def _check_mutation(
        self,
        name: str,
        state: _OwnershipState,
        *,
        place: Place | None = None,
        display: str | None = None,
    ) -> None:
        self._check_name(name, state)
        target = place or state.places.get(name) or self._root_place(name)
        if (
            self._live_borrow_of(state, target) is not None
            or self._borrow_provenances_at(state, target)
            or self._borrow_storage_overlaps(state, target)
        ):
            self._error("MutationDuringBorrow", display or name)

    def _check_mutation_expr(self, node: ast.AST, state: _OwnershipState) -> None:
        root = self._root_name(node)
        place = self._place_for_expr(node, state)
        if root is not None:
            self._check_name(root, state)
        if place is None:
            return
        if (
            self._live_borrow_of(state, place)
            or self._borrow_provenances_at(state, place)
            or self._borrow_storage_overlaps(state, place)
        ):
            self._error("MutationDuringBorrow", ast.unparse(node))

    def _borrow_result(
        self,
        expression: ast.AST,
        result_type: str | None,
        state: _OwnershipState,
    ) -> None:
        if not self._contains_borrow(result_type):
            return
        root = self._root_name(self._borrow_source(expression))
        if root is not None:
            self._check_name(root, state)

    def _check_expr(
        self,
        node: ast.AST | None,
        state: _OwnershipState,
        *,
        expected: str | None = None,
    ) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            self._check_name(node.id, state)
            return self.env.get(node.id)
        if isinstance(node, ast.Attribute):
            root = self._root_name(node)
            if root is not None:
                self._check_name(root, state)
            self._check_expr(node.value, state)
            return self._expr_type(node)
        if isinstance(node, ast.Subscript):
            root = self._root_name(node.value)
            if root is not None:
                self._check_name(root, state)
            self._check_expr(node.value, state)
            self._check_expr(node.slice, state)
            return self._expr_type(node, expected)
        if isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else _ast_qualified_name(node.func)
            )
            if isinstance(node.func, ast.Name) and node.func.id == "drop":
                if len(node.args) != 1:
                    self._error("InvalidDrop")
                target_node = node.args[0]
                target_name = (
                    target_node.id if isinstance(target_node, ast.Name) else None
                )
                target_place = self._place_for_expr(target_node, state)
                if target_name is not None and target_name in state.borrows and target_name not in state.statuses:
                    del state.borrows[target_name]
                    state.borrow_places.pop(target_name, None)
                    return "Unit"
                if target_name is not None and state.statuses.get(target_name) == "dropped":
                    self._error("DuplicateDrop", target_name)
                if target_name is not None:
                    self._check_name(target_name, state)
                self._reject_projected_owner_drop(
                    target_place,
                    self._expr_type(target_node),
                )
                if target_place is None:
                    self._error("InvalidDrop")
                if target_name is None and _is_borrowed(self._expr_type(target_node)):
                    self._remove_borrows_at_place(state, target_place)
                    return "Unit"
                live = self._live_borrow_of(state, target_place)
                if live is not None:
                    container, provenance = live
                    self._contained_borrow_error(
                        "BackingOwnerDropWhileBorrowed",
                        container_type=self._storage_type(container),
                        provenance=provenance,
                        path=f"drop({ast.unparse(target_node)})",
                    )
                if target_name is not None:
                    state.borrows.pop(target_name, None)
                    state.borrow_places.pop(target_name, None)
                    state.statuses[target_name] = "dropped"
                return "Unit"
            receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
            receiver_type = self._expr_type(receiver)
            method = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            map_types = generic_parts(receiver_type or "", "Map", arity=2)
            if (
                map_types is not None
                and self._owner(map_types[1])
                and method in {"get", "insert", "increment", "entries"}
            ):
                self._error("MapOperationsRequireScalarValues", receiver_type)
            receiver_root = self._root_name(receiver)
            receiver_place = self._place_for_expr(receiver, state)
            if receiver_root is not None:
                self._check_name(receiver_root, state)
            if receiver_root and method in {
                "push", "get_mut", "insert", "increment",
                "append_byte", "append_scalar", "append_text", "append_uint64",
            }:
                self._check_mutation(
                    receiver_root,
                    state,
                    place=receiver_place,
                    display=ast.unparse(receiver),
                )
            if receiver_root and method in {"view", "get", "get_mut", "entries", "as_view"}:
                self._check_name(receiver_root, state)
            if receiver is not None:
                self._check_expr(receiver, state)
            for argument in node.args:
                if getattr(argument, "_merlo_implicit_callable", None) is None:
                    self._check_expr(argument, state)
            method_signature = (
                CONTRACT_GRAPH.method(receiver_type, method)
                if receiver_type is not None and method
                else None
            )
            if method_signature is None and method:
                static_receiver = _ast_qualified_name(receiver)
                static_signature = CONTRACT_GRAPH.static_method(
                    static_receiver,
                    method,
                )
                if static_signature is not None:
                    method_signature = static_signature
            if method_signature is not None:
                if not method_signature.accepts_arity(len(node.args)):
                    self._error(
                        "ArityMismatch",
                        f"{receiver_type}.{method}",
                    )
                if receiver_root is not None and not method_signature.static:
                    if method_signature.receiver_ownership == "borrow_mut":
                        self._check_mutation(
                            receiver_root,
                            state,
                            place=receiver_place,
                            display=ast.unparse(receiver),
                        )
                    elif method_signature.receiver_ownership == "consuming":
                        self._consume_place(
                            receiver_place or self._root_place(receiver_root),
                            state,
                            name=receiver_root,
                            display=ast.unparse(receiver),
                        )
                for argument, parameter_ownership in zip(
                    node.args,
                    method_signature.ownership_for(len(node.args)),
                    strict=True,
                ):
                    root = self._root_name(argument)
                    place = self._place_for_expr(argument, state)
                    if root is None and place is None:
                        continue
                    if parameter_ownership == "borrow_mut":
                        if root is not None:
                            self._check_mutation(
                                root,
                                state,
                                place=place,
                                display=ast.unparse(argument),
                            )
                    elif parameter_ownership in {"owned", "consuming"}:
                        self._consume_expr(
                            argument,
                            state,
                            expected=self._expr_type(argument),
                        )
            signature = intrinsic_signature(name)
            if signature is not None:
                for argument, parameter_ownership in zip(
                    node.args, signature.parameter_ownership, strict=True
                ):
                    root = self._root_name(argument)
                    place = self._place_for_expr(argument, state)
                    if root is None and place is None:
                        continue
                    if parameter_ownership == "borrow_mut":
                        if root is not None:
                            self._check_mutation(
                                root,
                                state,
                                place=place,
                                display=ast.unparse(argument),
                            )
                    elif parameter_ownership in {"owned", "consuming"}:
                        self._consume_expr(
                            argument,
                            state,
                            expected=self._expr_type(argument),
                        )
            if isinstance(node.func, ast.Name) and node.func.id in self.functions:
                callee = self.functions[node.func.id]
                for argument, parameter in zip(node.args, callee.args.args):
                    parameter_type = _type_name(parameter.annotation)
                    returned = any(
                        isinstance(item, ast.Return)
                        and isinstance(item.value, ast.Name)
                        and item.value.id == parameter.arg
                        for item in ast.walk(callee)
                    )
                    if self._owner(parameter_type) and returned:
                        self._consume_expr(
                            argument,
                            state,
                            expected=parameter_type,
                        )
            elif (
                method_signature is None
                and receiver_root
                and method == "push"
                and node.args
            ):
                vec_parts = generic_parts(receiver_type, "Vec", arity=1)
                element = vec_parts[0] if vec_parts is not None else None
                if self._owner(element):
                    self._consume_expr(
                        node.args[0],
                        state,
                        expected=element,
                    )
            if receiver_root and method == "push" and node.args:
                vec_parts = generic_parts(receiver_type, "Vec", arity=1)
                element_type = vec_parts[0] if vec_parts is not None else None
                if self._contains_borrow(element_type):
                    provenances = self._borrow_provenances(
                        node.args[0],
                        element_type,
                        state,
                    )
                    if not provenances:
                        self._error(
                            "ContainedBorrowProvenanceUnknown",
                            f"{receiver_type}.push",
                        )
                    self._register_borrows(
                        state,
                        receiver_root,
                        tuple(
                            sorted(
                                set(
                                    self._borrow_provenances_at(
                                        state,
                                        receiver_place or self._root_place(receiver_root),
                                        receiver_root,
                                    )
                                )
                                | set(
                                    self._extend_provenance(
                                        provenances,
                                        f"{receiver_type}.push",
                                    )
                                )
                            )
                        ),
                        place=receiver_place,
                    )
            result_type = self._expr_type(node, expected)
            if isinstance(node.func, ast.Name) and node.func.id in self.functions:
                self._summary_provenances(node, result_type, state)
            self._reject_unknown_borrow_call(node, result_type)
            self._borrow_result(node.func.value if receiver is not None else node, result_type, state)
            return result_type
        if isinstance(node, ast.Lambda):
            metadata = getattr(node, "_merlo_closure_metadata", None)
            if metadata is None:
                self._error("CapturingClosureUnsupported")
            for name, capture_type, _ownership in metadata[3]:
                self._check_name(name, state)
                properties = self.type_properties.resolve(capture_type)
                if properties.contains_borrow:
                    capture_place = state.places.get(name) or self._root_place(name)
                    provenances = self._borrow_provenances_at(
                        state,
                        capture_place,
                        name,
                    ) or (
                        _BorrowProvenance(
                            capture_place,
                            self._borrow_type(capture_type),
                            (name,),
                            name,
                        ),
                    )
                    self._contained_borrow_error(
                        "BorrowedClosureCaptureEscapes",
                        container_type=capture_type,
                        provenance=provenances[0],
                        path=f"closure_capture({name})",
                    )
                if properties.is_resource or properties.contains_resource:
                    self._error("ResourceClosureCaptureForbidden", name)
            return expected
    def _merge(self, before: _OwnershipState, branches: tuple[_OwnershipState, ...]) -> _OwnershipState:
        live = tuple(branch for branch in branches if not branch.terminal)
        break_paths = tuple(
            path
            for branch in branches
            for path in branch.break_paths
        )
        backedge_paths = tuple(
            path
            for branch in branches
            for path in branch.backedge_paths
        )
        if not live:
            return _OwnershipState(
                dict(before.statuses),
                dict(before.borrows),
                dict(before.places),
                True,
                dict(before.borrow_places),
                break_paths,
                backedge_paths,
            )
        merged_statuses = dict(live[0].statuses)
        for name in sorted(before.statuses):
            statuses = {
                branch.statuses.get(name, "absent")
                for branch in live
            }
            if len(statuses) > 1:
                if self.binding_kinds_by_name.get(name) != "binding":
                    self._error("OwnershipAmbiguity", name)
                # Inferred rebindings use drop-flag slots; keep the ambiguity
                # visible to any use until a later assignment resolves it.
                merged_statuses[name] = "ambiguous"
        borrows = [branch.borrows for branch in live]
        if borrows and any(item != borrows[0] for item in borrows[1:]):
            self._error("OwnershipAmbiguity")
        tracked_place_names = set(before.statuses) | set(before.borrow_places)
        branch_places = tuple(
            tuple(
                (name, branch.places.get(name))
                for name in sorted(tracked_place_names)
            )
            for branch in live
        )
        if branch_places and any(item != branch_places[0] for item in branch_places[1:]):
            self._error("OwnershipAmbiguity")
        borrow_places = [branch.borrow_places for branch in live]
        if borrow_places and any(item != borrow_places[0] for item in borrow_places[1:]):
            self._error("OwnershipAmbiguity")
        return _OwnershipState(
            merged_statuses,
            dict(live[0].borrows),
            dict(live[0].places),
            False,
            dict(live[0].borrow_places),
            break_paths,
            backedge_paths,
        )
    @staticmethod
    def _snapshot_state(state: _OwnershipState) -> _OwnershipState:
        return _OwnershipState(
            dict(state.statuses),
            dict(state.borrows),
            dict(state.places),
            False,
            dict(state.borrow_places),
        )
    @staticmethod
    def _loop_visible_state(
        before: _OwnershipState,
        candidate: _OwnershipState,
    ) -> _OwnershipState:
        return _OwnershipState(
            {
                name: candidate.statuses.get(name, "absent")
                for name in before.statuses
            },
            {
                name: candidate.borrows[name]
                for name in before.borrows
                if name in candidate.borrows
            },
            {
                name: candidate.places[name]
                for name in before.places
                if name in candidate.places
            },
            False,
            {
                name: candidate.borrow_places[name]
                for name in before.borrow_places
                if name in candidate.borrow_places
            },
        )
    @staticmethod
    def _loop_assignment_names(
        statements: list[ast.stmt],
        binding_kinds: Mapping[int, str],
    ) -> set[str]:
        """Find compiler-managed inferred bindings in a loop body."""
        names = {
            target.id
            for statement in statements
            for node in ast.walk(statement)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        names.update(
            node.target.id
            for statement in statements
            for node in ast.walk(statement)
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and getattr(
                    node,
                    "_merlo_binding_kind",
                    binding_kinds.get(node.lineno),
                )
                not in {"let", "var"}
            )
        )
        return names

    @staticmethod
    def _loop_implicit_cleanup_names(statements: list[ast.stmt]) -> set[str]:
        """Recognize owned collection values whose scope cleanup is deterministic."""
        return {
            node.target.id
            for statement in statements
            for node in ast.walk(statement)
            if (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "get"
            )
        }
    def _require_loop_backedge_stable(
        self,
        before: _OwnershipState,
        candidate: _OwnershipState,
        *,
        assignment_names: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        """Require every ownership-visible backedge to reach the loop entry."""
        for name in sorted(set(candidate.statuses) - set(before.statuses)):
            if (
                candidate.statuses[name] not in {"dropped", "moved"}
                and name not in assignment_names
            ):
                self._error(
                    "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                    name,
                )
        for name in sorted(before.statuses):
            if candidate.statuses.get(name, "absent") != before.statuses[name]:
                self._error(
                    "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                    name,
                )
        for name in sorted(before.places):
            if candidate.places.get(name) != before.places[name]:
                self._error(
                    "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                    name,
                )
        for name in sorted(before.borrows):
            if candidate.borrows.get(name) != before.borrows[name]:
                self._error(
                    "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                    name,
                )
        for name in sorted(before.borrow_places):
            if candidate.borrow_places.get(name) != before.borrow_places[name]:
                self._error(
                    "LoopOwnershipBackedgeRequiresFixedPointSupport: OwnershipAmbiguity",
                    name,
                )
    def _loop_exit_state(
        self,
        before: _OwnershipState,
        candidate: _OwnershipState,
        *,
        assignment_names: set[str] | frozenset[str] = frozenset(),
    ) -> _OwnershipState:
        """Project a break exit only after cleaning iteration-local owners."""
        for name in sorted(set(candidate.statuses) - set(before.statuses)):
            if (
                candidate.statuses[name] not in {"dropped", "moved"}
                and name not in assignment_names
            ):
                self._error("OwnershipAmbiguity", name)
        return self._loop_visible_state(before, candidate)
    def _join_loop_states(
        self,
        before: _OwnershipState,
        candidates: tuple[_OwnershipState, ...],
    ) -> _OwnershipState:
        joined = self._merge(
            before,
            tuple(
                self._loop_visible_state(before, candidate)
                for candidate in candidates
            ),
        )
        return _OwnershipState(
            dict(joined.statuses),
            dict(joined.borrows),
            dict(joined.places),
            joined.terminal,
            dict(joined.borrow_places),
        )
    def _check_statements(self, statements: list[ast.stmt], state: _OwnershipState) -> _OwnershipState:
        for node in statements:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                self.binding_kinds_by_name.setdefault(
                    node.target.id,
                    getattr(
                        node,
                        "_merlo_binding_kind",
                        self.binding_kinds.get(node.lineno, "let"),
                    ),
                )
                type_name = _type_name(node.annotation)
                if node.value is not None:
                    provenances = self._borrow_provenances(
                        node.value,
                        type_name,
                        state,
                    )
                    self._check_expr(node.value, state, expected=type_name)
                    if self._owner(type_name):
                        self._consume_expr(
                            node.value,
                            state,
                            expected=type_name,
                        )
                    if self._contains_borrow(type_name):
                        self._register_record_borrows(
                            state,
                            node.target.id,
                            type_name,
                            node.value,
                            provenances,
                        )
                self.env[node.target.id] = type_name
                existing_place = state.places.get(node.target.id)
                if existing_place is not None:
                    state.places[node.target.id] = existing_place
                else:
                    binding_place = self._binding_place(node.value, state)
                    state.places[node.target.id] = (
                        binding_place or self._root_place(node.target.id)
                        if (
                            self._owner(type_name)
                            and not self._contains_borrow(type_name)
                            and not _is_borrowed(type_name)
                        )
                        else self._root_place(node.target.id)
                    )
                if self._owner(type_name):
                    state.statuses[node.target.id] = "available"
                continue
            if isinstance(node, ast.Assign):
                predicted_type = self._expr_type(node.value)
                provenances = self._borrow_provenances(
                    node.value,
                    predicted_type,
                    state,
                )
                value_type = self._check_expr(node.value, state)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.binding_kinds_by_name.setdefault(target.id, "binding")
                        target_type = self.env.get(target.id)
                        target_place = (
                            state.places.get(target.id)
                            or self._root_place(target.id)
                        )
                        state.places[target.id] = target_place
                        if target_type and self._owner(target_type):
                            self._consume_expr(
                                node.value,
                                state,
                                expected=target_type,
                            )
                            state.statuses[target.id] = "available"
                        if target_type and self._contains_borrow(target_type):
                            self._register_borrows(
                                state,
                                target.id,
                                self._extend_provenance(
                                    provenances,
                                    f"assign({target.id}:{target_type})",
                                ),
                                place=state.places.get(target.id),
                            )
                    elif isinstance(target, (ast.Attribute, ast.Subscript)):
                        self._check_mutation_expr(target, state)
                        target_root = self._root_name(target)
                        target_type = self._expr_type(target)
                        if (
                            target_root is not None
                            and self._contains_borrow(target_type or value_type)
                        ):
                            if (
                                target_root in self.parameters
                                and any(
                                    not self._is_parameter_place(item.backing_place)
                                    for item in provenances
                                )
                            ):
                                provenance = next(
                                    item
                                    for item in provenances
                                    if not self._is_parameter_place(item.backing_place)
                                )
                                self._contained_borrow_error(
                                    "ContainedBorrowStoredInEscapingOwner",
                                    container_type=self._storage_type(target_root),
                                    provenance=provenance,
                                    path=f"store({ast.unparse(target)})",
                                )
                            self._register_borrows(
                                state,
                                target_root,
                                self._extend_provenance(
                                    provenances,
                                    f"store({ast.unparse(target)})",
                                ),
                                place=self._place_for_expr(target, state),
                            )
                continue
            if isinstance(node, ast.Expr):
                self._check_expr(node.value, state)
                continue
            if isinstance(node, ast.Contract):
                self._check_expr(node.condition, state)
                continue
            if isinstance(node, ast.Break):
                state.break_paths = (
                    *state.break_paths,
                    self._snapshot_state(state),
                )
                state.terminal = True
                break
            if isinstance(node, ast.Continue):
                state.backedge_paths = (
                    *state.backedge_paths,
                    self._snapshot_state(state),
                )
                state.terminal = True
                break
            if isinstance(node, ast.Return):
                result_type = _type_name(self.current.returns if self.current else None)
                provenances = self._borrow_provenances(
                    node.value,
                    result_type,
                    state,
                )
                value_type = self._check_expr(node.value, state, expected=result_type)
                if self._contains_borrow(result_type):
                    escaping = next(
                        (
                            item
                            for item in provenances
                            if not self._is_parameter_place(item.backing_place)
                        ),
                        None,
                    )
                    if escaping is not None:
                        self._contained_borrow_error(
                            (
                                f"EscapedView: {escaping.backing_owner}"
                                if _is_borrowed(result_type)
                                else "EscapedContainedBorrow"
                            ),
                            container_type=result_type,
                            provenance=escaping,
                            path=f"return({result_type})",
                        )
                    if not provenances:
                        root = self._root_name(self._borrow_source(node.value))
                        place = self._place_for_expr(self._borrow_source(node.value), state)
                        if root is not None and (
                            place is None or not self._is_parameter_place(place)
                        ):
                            self._error("EscapedView", root)
                self._borrow_result(node.value, value_type or result_type, state)
                if self._owner(result_type):
                    target_place = self._place_for_expr(node.value, state)
                    if target_place is not None:
                        for name, provenances in tuple(state.borrows.items()):
                            if (
                                _is_borrowed(self.env.get(name))
                                and any(
                                    overlap_relation(
                                        target_place,
                                        provenance.backing_place,
                                    )
                                    is not OverlapRelation.DISJOINT
                                    for provenance in provenances
                                )
                            ):
                                state.borrows.pop(name, None)
                                state.borrow_places.pop(name, None)
                    self._consume_expr(
                        node.value,
                        state,
                        expected=result_type,
                        allow_projected_owner_move=True,
                    )
                state.terminal = True
                break
            if isinstance(node, ast.If):
                self._check_expr(node.test, state)
                before_statuses = set(state.statuses)
                before_borrows = set(state.borrows)
                before_borrow_places = set(state.borrow_places)
                before_places = set(state.places)
                then_state = self._check_statements(node.body, state.clone())
                else_state = self._check_statements(node.orelse, state.clone())
                for branch in (then_state, else_state):
                    for name in set(branch.statuses) - before_statuses:
                        branch.statuses.pop(name, None)
                    for name in set(branch.borrows) - before_borrows:
                        branch.borrows.pop(name, None)
                    for name in set(branch.borrow_places) - before_borrow_places:
                        branch.borrow_places.pop(name, None)
                    for name in set(branch.places) - before_places:
                        branch.places.pop(name, None)
                state = self._merge(state, (then_state, else_state))
                continue
            if isinstance(node, ast.While):
                loop_entry = state.clone()
                test_state = loop_entry.clone()
                self._check_expr(node.test, test_state)
                self._require_loop_backedge_stable(loop_entry, test_state)
                body_state = self._check_statements(
                    node.body,
                    test_state.clone(),
                )
                assignment_names = (
                    self._loop_assignment_names(
                        node.body,
                        self.binding_kinds,
                    )
                    | self._loop_implicit_cleanup_names(node.body)
                )
                exit_candidates = [test_state]
                backedge_candidates = []
                if not body_state.terminal:
                    backedge_candidates.append(body_state)
                backedge_candidates.extend(body_state.backedge_paths)
                for candidate in backedge_candidates:
                    self._require_loop_backedge_stable(
                        test_state,
                        candidate,
                        assignment_names=assignment_names,
                    )
                exit_candidates.extend(
                    self._loop_exit_state(
                        test_state,
                        path,
                        assignment_names=assignment_names,
                    )
                    for path in body_state.break_paths
                )
                state = self._join_loop_states(
                    test_state,
                    tuple(exit_candidates),
                )
                continue
            if isinstance(node, ast.For):
                iterable_type = self._check_expr(node.iter, state)
                before_loop = state.clone()
                loop_state = before_loop.clone()
                if isinstance(node.target, ast.Name):
                    shape = collection_shape(iterable_type)
                    target_type = (
                        shape.element_type
                        if shape is not None
                        else "TextView"
                        if iterable_type == "FileLines"
                        else "Inferred"
                    )
                    self.env[node.target.id] = target_type
                    loop_state.places[node.target.id] = self._root_place(node.target.id)
                    if (
                        not iterable_type.startswith("Borrow[")
                        and target_type != "Inferred"
                        and self._owner(target_type)
                    ):
                        loop_state.statuses[node.target.id] = "available"
                body_state = self._check_statements(node.body, loop_state)
                assignment_names = (
                    self._loop_assignment_names(
                        node.body,
                        self.binding_kinds,
                    )
                    | self._loop_implicit_cleanup_names(node.body)
                )
                exit_candidates = [before_loop]
                backedge_candidates = []
                if not body_state.terminal:
                    backedge_candidates.append(body_state)
                backedge_candidates.extend(body_state.backedge_paths)
                for candidate in backedge_candidates:
                    self._require_loop_backedge_stable(
                        before_loop,
                        candidate,
                        assignment_names=assignment_names,
                    )
                exit_candidates.extend(
                    self._loop_exit_state(
                        before_loop,
                        path,
                        assignment_names=assignment_names,
                    )
                    for path in body_state.break_paths
                )
                state = self._join_loop_states(
                    before_loop,
                    tuple(exit_candidates),
                )
                continue
            if isinstance(node, ast.Match):
                self._check_expr(node.subject, state)
                branches = []
                before_statuses = set(state.statuses)
                before_borrows = set(state.borrows)
                before_borrow_places = set(state.borrow_places)
                before_places = set(state.places)
                for case in node.cases:
                    branch = state.clone()
                    self._check_statements(case.body, branch)
                    for name in set(branch.statuses) - before_statuses:
                        branch.statuses.pop(name, None)
                    for name in set(branch.borrows) - before_borrows:
                        branch.borrows.pop(name, None)
                    for name in set(branch.places) - before_places:
                        branch.places.pop(name, None)
                    for name in set(branch.borrow_places) - before_borrow_places:
                        branch.borrow_places.pop(name, None)
                    branches.append(branch)
                if branches:
                    state = self._merge(state, tuple(branches))
                continue
        return state

    def _validate_function_end(self, state: _OwnershipState) -> None:
        for name, status in sorted(state.statuses.items()):
            if (
                status == "ambiguous"
                and self.binding_kinds_by_name.get(name) != "binding"
            ):
                self._error("OwnershipAmbiguity", name)
    def check(self) -> None:
        for function in self.functions.values():
            self.current = function
            self.binding_kinds_by_name = {}
            self.env = {
                argument.arg: _type_name(argument.annotation)
                for argument in function.args.args
            }
            self.parameters = set(self.env)
            state = _OwnershipState(
                {
                    name: "available"
                    for name, type_name in self.env.items()
                    if self._owner(type_name)
                },
                {},
                {
                    name: self._root_place(name)
                    for name in self.env
                },
            )
            final_state = self._check_statements(function.body, state)
            self._validate_function_end(final_state)


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
    ) -> None:
        self.path = path
        self.source = source
        self.preprocessed = preprocessed
        self.types = types
        self.functions = functions
        self.ffi_program = ffi_program or FFIProgram()
        self.borrow_summaries = borrow_summaries or {}
        self.extern_functions = {item.name: item for item in self.ffi_program.extern_functions}
        self.function_symbols = {
            name: _stable_id("shirs", path, "function", name) for name in functions
        }
        self.mutable_parameters = self._mutable_parameter_table(functions)
        self.local_types: dict[str, str] = {}
        self.current_function = ""
        self.ordinal = 0
        self.type_properties = TypePropertyResolver(types)

    def _owner(self, type_name: str | None, seen: frozenset[str] = frozenset()) -> bool:
        return self.type_properties.resolve(type_name, seen).needs_drop

    def _contains_borrow(self, type_name: str | None) -> bool:
        return self.type_properties.resolve(type_name).contains_borrow

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
        revision = _stable_id(
            "rev",
            kind,
            type_name,
            ownership,
            tuple(sorted(set(effects))),
            attrs,
            tuple(item.revision_id for item in child_tuple),
        )
        return HIRNode(
            _stable_id(
                "shirn",
                self.path,
                self.current_function,
                source.to_dict(),
                kind,
                self.ordinal,
            ),
            kind,
            source,
            scope_id or self._scope(),
            type_name,
            ownership,
            tuple(sorted(set(effects))),
            symbol_id,
            revision,
            attrs,
            child_tuple,
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
                    argument.arg,
                    type_name,
                    ownership,
                    source,
                    symbol_id,
                    _stable_id("rev", node.name, argument.arg, type_name, ownership),
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
        return_type = _type_name(node.returns)
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
            node.name,
            tuple(parameters),
            return_type,
            effects,
            requirements,
            ensures,
            body,
            source,
            self._scope(),
            symbol_id,
            revision_id,
            self.borrow_summaries.get(node.name, BorrowSummary()),
        )


def _parse_type_declarations(
    path: str,
    module: ast.Module,
    kinds: dict[str, str],
) -> dict[str, HIRTypeDecl]:
    result: dict[str, HIRTypeDecl] = {}
    for node in (item for item in module.body if isinstance(item, ast.ClassDef)):
        kind = kinds.get(node.name)
        if kind not in {"record", "enum"}:
            raise StructuredHIRCompileError(f"{path}:{node.lineno}: unknown type declaration")
        source = _span(path, node)
        type_symbol = _stable_id("shirs", path, kind, node.name)
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
                type_name = _type_name(statement.annotation)
                item_source = _span(path, statement)
                symbol = _stable_id("shirs", path, node.name, "field", name)
                fields.append(HIRField(name, type_name, item_source, symbol, _stable_id("rev", node.name, name, type_name)))
            else:
                if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Name):
                    name = statement.value.id
                    payload = None
                elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    name = statement.target.id
                    payload = _type_name(statement.annotation)
                else:
                    raise StructuredHIRCompileError(f"{path}:{statement.lineno}: invalid enum variant")
                item_source = _span(path, statement)
                symbol = _stable_id("shirs", path, node.name, "variant", name)
                variants.append(HIRVariant(name, payload, ordinal, item_source, symbol, _stable_id("rev", node.name, name, payload, ordinal)))
        revision = _stable_id(
            "rev",
            kind,
            node.name,
            [(item.name, item.type_name) for item in fields],
            [(item.name, item.payload_type, item.tag) for item in variants],
            [item.revision_id for item in invariants],
        )
        result[node.name] = HIRTypeDecl(
            node.name,
            kind,
            source,
            type_symbol,
            revision,
            tuple(fields),
            tuple(variants),
            invariants,
        )
    return result
def _canonical_span(span: Any) -> SourceSpan:
    return SourceSpan(span.path, span.start_line, span.start_column, span.end_line, span.end_column)

def _canonical_flow_machine_hir(program: CanonicalProgram) -> tuple[tuple[HIRFlow, ...], tuple[HIRMachine, ...]]:
    flows: list[HIRFlow] = []
    for flow in program.flows:
        symbol = _stable_id("shirs", flow.span.path, "flow", flow.name)
        revision = _stable_id(
            "rev",
            flow.to_payload(),
        )
        scope = _stable_id("scope", flow.span.path, flow.name, "flow")
        body: list[HIRNode] = []
        for item in flow.body:
            if isinstance(item, CanonicalParallel):
                children = tuple(
                    HIRNode(
                        _stable_id("node", flow.name, branch.node_id),
                        "FlowStep",
                        _canonical_span(branch.span),
                        scope,
                        branch.type_name,
                        "value",
                        tuple(),
                        None,
                        revision,
                        (("name", branch.name), ("value", branch.value), ("policies", branch.to_payload()["policies"])),
                    )
                    for branch in item.branches
                )
                body.append(HIRNode(
                    _stable_id("node", flow.name, item.node_id),
                    "Parallel",
                    _canonical_span(item.span),
                    scope,
                    None,
                    "value",
                    tuple(),
                    None,
                    revision,
                    (),
                    children,
                ))
            elif isinstance(item, CanonicalFlowStep):
                body.append(HIRNode(
                    _stable_id("node", flow.name, item.node_id),
                    "FlowStep",
                    _canonical_span(item.span),
                    scope,
                    item.type_name,
                    "value",
                    tuple(),
                    None,
                    revision,
                    (("name", item.name), ("value", item.value), ("policies", item.to_payload()["policies"])),
                ))
        parameters = tuple(
            HIRParameter(
                name,
                type_name,
                "value",
                _canonical_span(flow.span),
                _stable_id("symbol", flow.name, name),
                revision,
            )
            for name, type_name in flow.parameters
        )
        flows.append(HIRFlow(
            flow.name,
            parameters,
            flow.return_type,
            flow.durable,
            flow.effects,
            flow.capabilities,
            tuple(body),
            _canonical_span(flow.span),
            symbol,
            revision,
        ))
    machines: list[HIRMachine] = []
    for machine in program.machines:
        symbol = _stable_id("shirs", machine.span.path, "machine", machine.name)
        revision = _stable_id(
            "rev",
            machine.to_payload(),
        )
        scope = _stable_id("scope", machine.span.path, machine.name, "machine")
        transitions = tuple(
            HIRNode(
                _stable_id("node", machine.name, transition.node_id),
                "Transition",
                _canonical_span(transition.span),
                scope,
                transition.target,
                "value",
                transition.effects,
                None,
                revision,
                (("name", transition.name), ("sources", transition.sources), ("target", transition.target)),
            )
            for transition in machine.transitions
        )
        parameters = tuple(
            HIRParameter(
                name,
                type_name,
                "value",
                _canonical_span(machine.span),
                _stable_id("symbol", machine.name, name),
                revision,
            )
            for name, type_name in machine.parameters
        )
        machines.append(HIRMachine(
            machine.name,
            parameters,
            tuple((state.name, state.fields) for state in machine.states),
            machine.initial,
            machine.invariant,
            transitions,
            _canonical_span(machine.span),
            symbol,
            revision,
        ))
    return tuple(flows), tuple(machines)





def compile_structured_hir(
    source: str,
    *,
    path: str = "main.mlo",
    entry_function: str = "main",
) -> StructuredHIRProgram:
    if not source.strip():
        raise StructuredHIRCompileError("empty Structured HIR source")
    try:
        ffi_program = validate_ffi(source, path=path)
    except FFICompileError as exc:
        raise StructuredHIRCompileError(str(exc)) from exc
    preprocessed = _preprocess(_preprocess_ffi_surface(source))
    try:
        module = ast.parse(preprocessed.source, filename=path)
    except SyntaxError as exc:
        raise StructuredHIRCompileError(f"{path}:{exc.lineno}:{exc.offset}: {exc.msg}") from exc
    _validate_map_specializations(module, path)
    types = _parse_type_declarations(path, module, preprocessed.declaration_kinds)
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
    borrow_summaries = compute_borrow_summaries(function_nodes, types)
    _OwnershipChecker(
        path,
        types,
        function_nodes,
        borrow_summaries,
        preprocessed.binding_kinds,
    ).check()
    builder = _HIRBuilder(
        path,
        source,
        preprocessed,
        types,
        function_nodes,
        ffi_program,
        borrow_summaries,
    )
    functions = tuple(builder.function(item) for item in function_nodes.values())
    return StructuredHIRProgram(
        source,
        path,
        hashlib.sha256(source.encode()).hexdigest(),
        tuple(types.values()),
        functions,
        entry_function,
        ast.module_to_json(module),
        ffi_program,
        native_module=module,
    )


def compile_canonical_hir(
    program: CanonicalProgram,
    *,
    entry_function: str = "main",
) -> StructuredHIRProgram:
    """Lower the retained typed Surface tree through the production HIR builder."""
    if program.surface_program is None:
        raise StructuredHIRCompileError(
            "CanonicalSurfaceRequired: serialized projections are not compiler input"
        )
    source = program.projection_source or ""
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
    types = _parse_type_declarations(
        path,
        module,
        preprocessed.declaration_kinds,
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
    flows, machines = _canonical_flow_machine_hir(program)
    if entry_function not in function_nodes and not (flows or machines):
        raise StructuredHIRCompileError(
            f"missing entry function: {entry_function}"
        )
    borrow_summaries = compute_borrow_summaries(function_nodes, types)
    _OwnershipChecker(
        path,
        types,
        function_nodes,
        borrow_summaries,
        preprocessed.binding_kinds,
    ).check()
    builder = _HIRBuilder(
        path,
        source,
        preprocessed,
        types,
        function_nodes,
        borrow_summaries=borrow_summaries,
    )
    functions = tuple(
        builder.function(item) for item in function_nodes.values()
    )
    for node in ast.walk(module):
        if hasattr(node, "_merlo_binding_kind"):
            delattr(node, "_merlo_binding_kind")
    return StructuredHIRProgram(
        source,
        path,
        hashlib.sha256(source.encode()).hexdigest(),
        tuple(types.values()),
        functions,
        entry_function,
        ast.module_to_json(module),
        ffi_program,
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
    "compile_structured_hir",
    "compile_canonical_hir",
    "compile_structured_hir_file",
]
