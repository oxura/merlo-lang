from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from merlo.surface_ast import SourceSpan, SurfaceProgram
from merlo.type_arena import TypeContextBuilder


def _span_payload(span: SourceSpan) -> dict[str, Any]:
    return {
        "path": span.path,
        "start_line": span.start_line,
        "start_column": span.start_column,
        "end_line": span.end_line,
        "end_column": span.end_column,
    }


@dataclass(frozen=True)
class CanonicalReturn:
    expression: str | None
    span: SourceSpan
    synthetic_reason: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "return",
            "expression": self.expression,
            "span": _span_payload(self.span),
            "synthetic_reason": self.synthetic_reason,
        }


@dataclass(frozen=True)
class CanonicalBinding:
    name: str
    type_name: str
    mutable: bool
    expression: str
    span: SourceSpan
    synthetic_reason: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "binding",
            "name": self.name,
            "type": self.type_name,
            "mutable": self.mutable,
            "expression": self.expression,
            "span": _span_payload(self.span),
            "synthetic_reason": self.synthetic_reason,
        }


@dataclass(frozen=True)
class CanonicalCapture:
    name: str
    type_name: str
    ownership: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type_name,
            "ownership": self.ownership,
        }


@dataclass(frozen=True)
class CanonicalClosure:
    closure_id: str
    parameters: tuple[tuple[str, str], ...]
    return_type: str
    expression: str
    captures: tuple[CanonicalCapture, ...]
    span: SourceSpan

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "closure",
            "closure_id": self.closure_id,
            "parameters": [list(item) for item in self.parameters],
            "return_type": self.return_type,
            "expression": self.expression,
            "captures": [item.to_payload() for item in self.captures],
            "span": _span_payload(self.span),
        }


@dataclass(frozen=True)
class CanonicalCallable:
    callable_id: str
    parameter: str
    parameter_type: str
    return_type: str
    expression: str
    span: SourceSpan

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "callable",
            "callable_id": self.callable_id,
            "parameter": self.parameter,
            "parameter_type": self.parameter_type,
            "return_type": self.return_type,
            "expression": self.expression,
            "span": _span_payload(self.span),
        }


@dataclass(frozen=True)
class CanonicalOptionFallback:
    option: str
    fallback: str
    type_name: str
    span: SourceSpan

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "option_fallback",
            "option": self.option,
            "fallback": self.fallback,
            "type": self.type_name,
            "span": _span_payload(self.span),
        }

@dataclass(frozen=True)
class CanonicalContract:
    kind: str
    expression: str
    span: SourceSpan

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "contract",
            "kind": self.kind,
            "expression": self.expression,
            "span": _span_payload(self.span),
        }


@dataclass(frozen=True)
class CanonicalHoleBinding:
    name: str
    type_name: str
    ownership: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type_name,
            "ownership": self.ownership,
        }


@dataclass(frozen=True)
class CanonicalHoleCallable:
    name: str
    parameters: tuple[tuple[str, str], ...]
    return_type: str
    effects: tuple[str, ...]
    capabilities: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameters": [list(item) for item in self.parameters],
            "return_type": self.return_type,
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class CanonicalHole:
    hole_id: str
    expected_type: str
    span: SourceSpan
    context: tuple[CanonicalHoleBinding, ...]
    callables: tuple[CanonicalHoleCallable, ...]
    effects: tuple[str, ...]
    capabilities: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "typed_hole",
            "hole_id": self.hole_id,
            "expected_type": self.expected_type,
            "span": _span_payload(self.span),
            "context": [
                item.to_payload() for item in self.context
            ],
            "callables": [
                item.to_payload() for item in self.callables
            ],
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class CanonicalPolicy:
    kind: str
    value: str
    error_type: str | None = None
    expression: str | None = None
    span: SourceSpan | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value,
            "error_type": self.error_type,
            "expression": self.expression,
            "span": _span_payload(self.span) if self.span else None,
        }


@dataclass(frozen=True)
class CanonicalFlowStep:
    node_id: str
    name: str
    value: str
    type_name: str
    policies: tuple[CanonicalPolicy, ...]
    span: SourceSpan

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "flow_step",
            "id": self.node_id,
            "name": self.name,
            "value": self.value,
            "type_name": self.type_name,
            "policies": [item.to_payload() for item in self.policies],
            "span": _span_payload(self.span),
        }


@dataclass(frozen=True)
class CanonicalParallel:
    node_id: str
    branches: tuple[CanonicalFlowStep, ...]
    span: SourceSpan

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "parallel",
            "id": self.node_id,
            "branches": [item.to_payload() for item in self.branches],
            "span": _span_payload(self.span),
        }


@dataclass(frozen=True)
class CanonicalFlow:
    name: str
    parameters: tuple[tuple[str, str], ...]
    return_type: str
    durable: bool
    effects: tuple[str, ...]
    capabilities: tuple[str, ...]
    body: tuple[CanonicalFlowStep | CanonicalParallel | CanonicalStatement, ...]
    span: SourceSpan
    exported: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "flow",
            "name": self.name,
            "parameters": [list(item) for item in self.parameters],
            "return_type": self.return_type,
            "durable": self.durable,
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
            "body": [item.to_payload() for item in self.body],
            "span": _span_payload(self.span),
            "exported": self.exported,
        }


@dataclass(frozen=True)
class CanonicalMachineState:
    name: str
    fields: tuple[tuple[str, str], ...]
    span: SourceSpan

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fields": [list(item) for item in self.fields],
            "span": _span_payload(self.span),
        }


@dataclass(frozen=True)
class CanonicalTransition:
    node_id: str
    name: str
    sources: tuple[str, ...]
    target: str
    effects: tuple[str, ...]
    body: tuple[CanonicalStatement, ...]
    span: SourceSpan

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "transition",
            "id": self.node_id,
            "name": self.name,
            "sources": list(self.sources),
            "target": self.target,
            "effects": list(self.effects),
            "body": [item.to_payload() for item in self.body],
            "span": _span_payload(self.span),
        }


@dataclass(frozen=True)
class CanonicalMachine:
    name: str
    parameters: tuple[tuple[str, str], ...]
    states: tuple[CanonicalMachineState, ...]
    initial: str | None
    invariant: str | None
    transitions: tuple[CanonicalTransition, ...]
    span: SourceSpan
    exported: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "machine",
            "name": self.name,
            "parameters": [list(item) for item in self.parameters],
            "states": [item.to_payload() for item in self.states],
            "initial": self.initial,
            "invariant": self.invariant,
            "transitions": [item.to_payload() for item in self.transitions],
            "span": _span_payload(self.span),
            "exported": self.exported,
        }


CanonicalStatement = CanonicalReturn | CanonicalBinding


@dataclass(frozen=True)
class CanonicalFunction:
    name: str
    parameters: tuple[tuple[str, str], ...]
    return_type: str
    kind: str
    effects: tuple[str, ...]
    capabilities: tuple[str, ...]
    error_types: tuple[str, ...]
    requirements: tuple[CanonicalContract, ...]
    ensures: tuple[CanonicalContract, ...]
    body: tuple[CanonicalStatement, ...]
    span: SourceSpan
    exported: bool = False
    synthetic_reason: str | None = None
    implicit_callables: tuple[CanonicalCallable, ...] = ()
    option_fallbacks: tuple[CanonicalOptionFallback, ...] = ()
    canonical_lines: tuple[str, ...] = ()
    closures: tuple[CanonicalClosure, ...] = ()
    holes: tuple[CanonicalHole, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "function",
            "name": self.name,
            "parameters": [list(item) for item in self.parameters],
            "return_type": self.return_type,
            "kind": self.kind,
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
            "error_types": list(self.error_types),
            "requirements": [
                item.to_payload() for item in self.requirements
            ],
            "ensures": [item.to_payload() for item in self.ensures],
            "body": [item.to_payload() for item in self.body],
            "span": _span_payload(self.span),
            "exported": self.exported,
            "synthetic_reason": self.synthetic_reason,
            "implicit_callables": [
                item.to_payload() for item in self.implicit_callables
            ],
            "option_fallbacks": [
                item.to_payload() for item in self.option_fallbacks
            ],
            "canonical_lines": list(self.canonical_lines),
            "closures": [item.to_payload() for item in self.closures],
            "holes": [item.to_payload() for item in self.holes],
        }

    @property
    def bindings(self) -> tuple[CanonicalBinding, ...]:
        return tuple(item for item in self.body if isinstance(item, CanonicalBinding))

    def binding(self, name: str) -> CanonicalBinding:
        return next(item for item in self.bindings if item.name == name)

@dataclass(frozen=True)
class CanonicalRecord:
    name: str
    fields: tuple[tuple[str, str], ...]
    span: SourceSpan
    exported: bool = False
    invariants: tuple[CanonicalContract, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "record",
            "name": self.name,
            "fields": [list(item) for item in self.fields],
            "span": _span_payload(self.span),
            "invariants": [
                item.to_payload() for item in self.invariants
            ],
            "exported": self.exported,
        }


@dataclass(frozen=True)
class CanonicalEnum:
    name: str
    variants: tuple[tuple[str, str | None], ...]
    span: SourceSpan
    exported: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "node": "enum",
            "name": self.name,
            "variants": [list(item) for item in self.variants],
            "span": _span_payload(self.span),
            "exported": self.exported,
        }

@dataclass(frozen=True)
class CanonicalProgram:
    records: tuple[CanonicalRecord, ...]
    functions: tuple[CanonicalFunction, ...]
    enums: tuple[CanonicalEnum, ...] = ()
    flows: tuple[CanonicalFlow, ...] = ()
    machines: tuple[CanonicalMachine, ...] = ()
    surface_program: SurfaceProgram | None = field(default=None, repr=False, compare=False)
    projection_source: str | None = field(default=None, repr=False, compare=False)
    source_path: str | None = field(default=None, repr=False, compare=False)
    source_sha256: str | None = field(default=None, repr=False, compare=False)
    type_context_builder: TypeContextBuilder | None = field(default=None, repr=False, compare=False)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": "merlo.canonical-typed-ast.v6",
            "records": [item.to_payload() for item in self.records],
            "functions": [item.to_payload() for item in self.functions],
            "enums": [item.to_payload() for item in self.enums],
            "flows": [item.to_payload() for item in self.flows],
            "machines": [item.to_payload() for item in self.machines],
        }

    @property
    def semantic_hash(self) -> str:
        payload = self.to_payload()
        for record in payload["records"]:
            record.pop("span", None)
            for invariant in record["invariants"]:
                invariant.pop("span", None)
        for enum in payload["enums"]:
            enum.pop("span", None)
        for function in payload["functions"]:
            function.pop("span", None)
            for contract in (
                *function["requirements"],
                *function["ensures"],
            ):
                contract.pop("span", None)
            for statement in function["body"]:
                statement.pop("span", None)
            for callable_expression in function["implicit_callables"]:
                callable_expression.pop("span", None)
            for fallback in function["option_fallbacks"]:
                fallback.pop("span", None)
            for closure in function["closures"]:
                closure.pop("span", None)
            for hole in function["holes"]:
                hole.pop("span", None)
        def strip_spans(value: object) -> None:
            if isinstance(value, dict):
                value.pop("span", None)
                for child in value.values():
                    strip_spans(child)
            elif isinstance(value, list):
                for child in value:
                    strip_spans(child)
        strip_spans(payload["flows"])
        strip_spans(payload["machines"])
        return hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CanonicalProgram:
        def span(value: dict[str, Any]) -> SourceSpan:
            return SourceSpan(**value)

        records = tuple(
            CanonicalRecord(
                item["name"],
                tuple(tuple(field) for field in item["fields"]),
                span(item["span"]),
                bool(item.get("exported", False)),
                tuple(
                    CanonicalContract(
                        value["kind"],
                        value["expression"],
                        span(value["span"]),
                    )
                    for value in item.get("invariants", ())
                ),
            )
            for item in payload.get("records", ())
        )
        enums = tuple(
            CanonicalEnum(
                item["name"],
                tuple(
                    (variant[0], variant[1])
                    for variant in item["variants"]
                ),
                span(item["span"]),
                bool(item.get("exported", False)),
            )
            for item in payload.get("enums", ())
        )
        functions = []
        for item in payload.get("functions", ()):
            body: list[CanonicalStatement] = []
            for statement in item["body"]:
                if statement["node"] == "return":
                    body.append(
                        CanonicalReturn(
                            statement["expression"],
                            span(statement["span"]),
                            statement.get("synthetic_reason"),
                        )
                    )
                else:
                    body.append(
                        CanonicalBinding(
                            statement["name"],
                            statement["type"],
                            bool(statement["mutable"]),
                            statement["expression"],
                            span(statement["span"]),
                            statement.get("synthetic_reason"),
                        )
                    )
            implicit_callables = tuple(
                CanonicalCallable(
                    value["callable_id"],
                    value["parameter"],
                    value["parameter_type"],
                    value["return_type"],
                    value["expression"],
                    span(value["span"]),
                )
                for value in item.get("implicit_callables", ())
            )
            option_fallbacks = tuple(
                CanonicalOptionFallback(
                    value["option"],
                    value["fallback"],
                    value["type"],
                    span(value["span"]),
                )
                for value in item.get("option_fallbacks", ())
            )
            requirements = tuple(
                CanonicalContract(
                    value["kind"],
                    value["expression"],
                    span(value["span"]),
                )
                for value in item.get("requirements", ())
            )
            ensures = tuple(
                CanonicalContract(
                    value["kind"],
                    value["expression"],
                    span(value["span"]),
                )
                for value in item.get("ensures", ())
            )
            closures = tuple(
                CanonicalClosure(
                    value["closure_id"],
                    tuple(tuple(parameter) for parameter in value["parameters"]),
                    value["return_type"],
                    value["expression"],
                    tuple(
                        CanonicalCapture(
                            capture["name"],
                            capture["type"],
                            capture["ownership"],
                        )
                        for capture in value.get("captures", ())
                    ),
                    span(value["span"]),
                )
                for value in item.get("closures", ())
            )
            holes = tuple(
                CanonicalHole(
                    value["hole_id"],
                    value["expected_type"],
                    span(value["span"]),
                    tuple(
                        CanonicalHoleBinding(
                            binding["name"],
                            binding["type"],
                            binding["ownership"],
                        )
                        for binding in value.get("context", ())
                    ),
                    tuple(
                        CanonicalHoleCallable(
                            callable_value["name"],
                            tuple(
                                tuple(parameter)
                                for parameter in callable_value.get(
                                    "parameters",
                                    (),
                                )
                            ),
                            callable_value["return_type"],
                            tuple(callable_value.get("effects", ())),
                            tuple(
                                callable_value.get(
                                    "capabilities",
                                    (),
                                )
                            ),
                        )
                        for callable_value in value.get(
                            "callables",
                            (),
                        )
                    ),
                    tuple(value.get("effects", ())),
                    tuple(value.get("capabilities", ())),
                )
                for value in item.get("holes", ())
            )
            functions.append(
                CanonicalFunction(
                    item["name"],
                    tuple(
                        tuple(parameter)
                        for parameter in item["parameters"]
                    ),
                    item["return_type"],
                    item["kind"],
                    tuple(item["effects"]),
                    tuple(item["capabilities"]),
                    tuple(item["error_types"]),
                    requirements,
                    ensures,
                    tuple(body),
                    span(item["span"]),
                    bool(item.get("exported", False)),
                    item.get("synthetic_reason"),
                    implicit_callables,
                    option_fallbacks,
                    tuple(item.get("canonical_lines", ())),
                    closures,
                    holes,
                )
            )
        return cls(records, tuple(functions), enums)

    def function(self, name: str) -> CanonicalFunction:
        return next(
            item for item in self.functions if item.name == name
        )

    def to_source(self) -> str:
        chunks: list[str] = []
        for record in self.records:
            prefix = "export " if record.exported else ""
            chunks.append(
                "\n".join(
                    (
                        f"{prefix}record {record.name}:",
                        *(
                            f"    {name}: {type_name}"
                            for name, type_name in record.fields
                        ),
                        *(
                            f"    invariant {item.expression}"
                            for item in record.invariants
                        ),
                    )
                )
            )
        for enum in self.enums:
            prefix = "export " if enum.exported else ""
            lines = [f"{prefix}enum {enum.name}:"]
            lines.extend(
                (
                    f"    {name}: {type_name}"
                    if type_name
                    else f"    {name}"
                )
                for name, type_name in enum.variants
            )
            chunks.append("\n".join(lines))
        for function in self.functions:
            prefix = "export " if function.exported else ""
            parameters = ", ".join(
                f"{name}: {type_name}"
                for name, type_name in function.parameters
            )
            lines = [
                f"{prefix}{function.kind} {function.name}"
                f"({parameters}) -> {function.return_type}:"
            ]
            if function.effects:
                lines.append(
                    f"    uses {', '.join(function.effects)}"
                )
            if function.canonical_lines:
                lines.extend(
                    f"    {line}" for line in function.canonical_lines
                )
            else:
                for statement in function.body:
                    if isinstance(statement, CanonicalBinding):
                        keyword = (
                            "var" if statement.mutable else "let"
                        )
                        lines.append(
                            f"    {keyword} {statement.name}: "
                            f"{statement.type_name} = "
                            f"{statement.expression}"
                        )
                    else:
                        suffix = (
                            f" {statement.expression}"
                            if statement.expression
                            else ""
                        )
                        lines.append(f"    return{suffix}")
            chunks.append("\n".join(lines))
        return "\n\n".join(chunks) + "\n"


__all__ = [
    "CanonicalEnum",
    "CanonicalCallable",
    "CanonicalBinding",
    "CanonicalCapture",
    "CanonicalClosure",
    "CanonicalContract",
    "CanonicalFunction",
    "CanonicalHole",
    "CanonicalHoleBinding",
    "CanonicalHoleCallable",
    "CanonicalOptionFallback",
    "CanonicalProgram",
    "CanonicalRecord",
    "CanonicalReturn",
]
