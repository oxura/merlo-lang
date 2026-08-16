from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from merlo.structured_hir_v2 import (
    HIRContract,
    HIRFunction,
    HIRInvariant,
    HIRNode,
    HIRTypeDecl,
    SourceSpan,
    StructuredHIRProgram,
)


OBLIGATION_IR_SCHEMA_VERSION = 1
OBLIGATION_IR_CONTRACT = "merlo.typed-obligation-ir.v1"


class ObligationCategory(str, Enum):
    FUNCTION_PRECONDITION = "function_precondition"
    FUNCTION_POSTCONDITION = "function_postcondition"
    DATA_INVARIANT = "data_invariant"
    TYPED_HOLE = "typed_hole"
    TYPE_SAFETY = "type_safety"
    EFFECT_SAFETY = "effect_safety"
    CAPABILITY_SAFETY = "capability_safety"
    OWNERSHIP_SAFETY = "ownership_safety"
    CONTROL_FLOW_SAFETY = "control_flow_safety"
    ARITHMETIC_SAFETY = "arithmetic_safety"
    TERMINATION = "termination"


class ObligationDisposition(str, Enum):
    UNRESOLVED = "unresolved"
    STATICALLY_PROVEN = "statically_proven"
    STATICALLY_REFUTED = "statically_refuted"
    RUNTIME_GUARDED = "runtime_guarded"
    EXPLICITLY_DEFERRED = "explicitly_deferred"


@dataclass(frozen=True)
class ObligationBinding:
    name: str
    type_name: str
    ownership: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "type": self.type_name,
            "ownership": self.ownership,
        }


@dataclass(frozen=True)
class TypedObligation:
    obligation_id: str
    revision_id: str
    category: ObligationCategory
    predicate: str
    expected_type: str
    owner_symbol_id: str
    owner_revision_id: str
    source: SourceSpan
    context: tuple[ObligationBinding, ...]
    dependencies: tuple[str, ...] = ()
    disposition: ObligationDisposition = ObligationDisposition.UNRESOLVED
    discharged_by: str | None = None

    def __post_init__(self) -> None:
        if self.discharged_by is None and self.disposition in {
            ObligationDisposition.STATICALLY_PROVEN,
            ObligationDisposition.STATICALLY_REFUTED,
        }:
            raise ValueError(
                "ProvenObligationRequiresDischarger: "
                f"{self.obligation_id}"
            )
        if self.discharged_by is not None and self.disposition in {
            ObligationDisposition.UNRESOLVED,
            ObligationDisposition.EXPLICITLY_DEFERRED,
        }:
            raise ValueError(
                "UndischargedObligationHasDischarger: "
                f"{self.obligation_id}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "revision_id": self.revision_id,
            "category": self.category.value,
            "predicate": self.predicate,
            "expected_type": self.expected_type,
            "owner_symbol_id": self.owner_symbol_id,
            "owner_revision_id": self.owner_revision_id,
            "source": self.source.to_dict(),
            "context": [item.to_dict() for item in self.context],
            "dependencies": list(self.dependencies),
            "disposition": self.disposition.value,
            "discharged_by": self.discharged_by,
        }


@dataclass(frozen=True)
class ObligationProgram:
    hir_digest: str
    obligations: tuple[TypedObligation, ...]
    schema_version: int = OBLIGATION_IR_SCHEMA_VERSION
    contract: str = OBLIGATION_IR_CONTRACT

    def __post_init__(self) -> None:
        identifiers = [item.obligation_id for item in self.obligations]
        if identifiers != sorted(identifiers):
            raise ValueError("ObligationsNotCanonical")
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("DuplicateObligationId")
        available = set(identifiers)
        for item in self.obligations:
            missing = set(item.dependencies) - available
            if missing:
                raise ValueError(
                    "UnknownObligationDependency: "
                    f"{item.obligation_id}: {sorted(missing)}"
                )

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    @property
    def unresolved(self) -> tuple[TypedObligation, ...]:
        return tuple(
            item
            for item in self.obligations
            if item.disposition == ObligationDisposition.UNRESOLVED
        )

    def by_category(
        self,
        category: ObligationCategory,
    ) -> tuple[TypedObligation, ...]:
        return tuple(
            item for item in self.obligations
            if item.category == category
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "hir_digest": self.hir_digest,
            "obligations": [
                item.to_dict() for item in self.obligations
            ],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )


def _stable_id(*parts: object) -> str:
    payload = json.dumps(
        parts,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "obl_" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def _revision_id(*parts: object) -> str:
    payload = json.dumps(
        parts,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "rev_" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def _function_context(
    function: HIRFunction,
    *,
    include_result: bool,
) -> tuple[ObligationBinding, ...]:
    bindings = [
        ObligationBinding(
            parameter.name,
            parameter.type_name,
            parameter.ownership,
        )
        for parameter in function.parameters
    ]
    if include_result:
        bindings.append(
            ObligationBinding("result", function.return_type, "value")
        )
    return tuple(bindings)


def _type_context(
    declaration: HIRTypeDecl,
) -> tuple[ObligationBinding, ...]:
    return tuple(
        ObligationBinding(field.name, field.type_name, "field")
        for field in declaration.fields
    )


def _node_shape(node: HIRNode) -> tuple[Any, ...]:
    attributes = node.attribute_map
    semantic_names = tuple(
        (name, attributes[name])
        for name in ("name", "field", "callee", "target_type")
        if name in attributes
    )
    return (
        node.kind,
        node.type_name,
        semantic_names,
        tuple(_node_shape(child) for child in node.children),
    )


def _invariant_shape(expression: str) -> tuple[str, ...]:
    return tuple(
        re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression)
    )


def _contract_obligation(
    function: HIRFunction,
    contract: HIRContract,
    *,
    category: ObligationCategory,
    anchor: tuple[Any, ...],
) -> TypedObligation:
    identity = _stable_id(
        function.symbol_id,
        category.value,
        anchor,
    )
    context = _function_context(
        function,
        include_result=(
            category == ObligationCategory.FUNCTION_POSTCONDITION
        ),
    )
    return TypedObligation(
        obligation_id=identity,
        revision_id=_revision_id(
            identity,
            function.revision_id,
            contract.expression,
            [item.to_dict() for item in context],
        ),
        category=category,
        predicate=contract.expression,
        expected_type="Bool",
        owner_symbol_id=function.symbol_id,
        owner_revision_id=function.revision_id,
        source=contract.source,
        context=context,
        disposition=ObligationDisposition.RUNTIME_GUARDED,
        discharged_by="native_contract_guard",
    )


def _invariant_obligation(
    declaration: HIRTypeDecl,
    invariant: HIRInvariant,
    anchor: tuple[Any, ...],
) -> TypedObligation:
    identity = _stable_id(
        declaration.symbol_id,
        ObligationCategory.DATA_INVARIANT.value,
        anchor,
    )
    context = _type_context(declaration)
    return TypedObligation(
        obligation_id=identity,
        revision_id=_revision_id(
            identity,
            declaration.revision_id,
            invariant.revision_id,
            [item.to_dict() for item in context],
        ),
        category=ObligationCategory.DATA_INVARIANT,
        predicate=invariant.expression,
        expected_type="Bool",
        owner_symbol_id=declaration.symbol_id,
        owner_revision_id=declaration.revision_id,
        source=invariant.source,
        context=context,
        disposition=ObligationDisposition.RUNTIME_GUARDED,
        discharged_by="native_record_invariant_guard",
    )


def _hole_obligation(
    function: HIRFunction,
    node: Any,
) -> TypedObligation:
    attributes = node.attribute_map
    hole_id = str(attributes["hole_id"])
    expected_type = str(
        attributes.get("expected_type") or node.type_name
    )
    context = tuple(
        ObligationBinding(
            str(item[0]),
            str(item[1]),
            str(item[2]),
        )
        for item in attributes.get("context", ())
        if isinstance(item, (list, tuple))
        and len(item) == 3
    )
    identity = _stable_id(
        hole_id,
        ObligationCategory.TYPED_HOLE.value,
    )
    return TypedObligation(
        obligation_id=identity,
        revision_id=_revision_id(
            identity,
            node.revision_id,
            expected_type,
            [item.to_dict() for item in context],
        ),
        category=ObligationCategory.TYPED_HOLE,
        predicate=f"complete {hole_id}",
        expected_type=expected_type,
        owner_symbol_id=function.symbol_id,
        owner_revision_id=function.revision_id,
        source=node.source,
        context=context,
    )


def build_obligation_ir(hir: StructuredHIRProgram) -> ObligationProgram:
    obligations: list[TypedObligation] = []
    for function in hir.functions:
        for category, contracts in (
            (
                ObligationCategory.FUNCTION_PRECONDITION,
                function.requirements,
            ),
            (
                ObligationCategory.FUNCTION_POSTCONDITION,
                function.ensures,
            ),
        ):
            shape_counts: dict[tuple[Any, ...], int] = {}
            for contract in contracts:
                shape = _node_shape(contract.condition)
                occurrence = shape_counts.get(shape, 0)
                shape_counts[shape] = occurrence + 1
                obligations.append(
                    _contract_obligation(
                        function,
                        contract,
                        category=category,
                        anchor=(shape, occurrence),
                    )
                )
        obligations.extend(
            _hole_obligation(function, node)
            for node in function.walk()
            if node.kind == "TypedHole"
        )
    for declaration in hir.types:
        shape_counts: dict[tuple[Any, ...], int] = {}
        for invariant in declaration.invariants:
            shape = _invariant_shape(invariant.expression)
            occurrence = shape_counts.get(shape, 0)
            shape_counts[shape] = occurrence + 1
            obligations.append(
                _invariant_obligation(
                    declaration,
                    invariant,
                    (shape, occurrence),
                )
            )
    return ObligationProgram(
        hir.digest,
        tuple(sorted(obligations, key=lambda item: item.obligation_id)),
    )


def extend_obligations(
    program: ObligationProgram,
    additions: Iterable[TypedObligation],
) -> ObligationProgram:
    by_id = {
        item.obligation_id: item for item in program.obligations
    }
    for item in additions:
        existing = by_id.get(item.obligation_id)
        if existing is not None and existing != item:
            raise ValueError(
                f"ConflictingObligation: {item.obligation_id}"
            )
        by_id[item.obligation_id] = item
    return ObligationProgram(
        program.hir_digest,
        tuple(sorted(by_id.values(), key=lambda item: item.obligation_id)),
    )


def replace_obligations(
    program: ObligationProgram,
    replacements: Iterable[TypedObligation],
) -> ObligationProgram:
    by_id = {
        item.obligation_id: item for item in program.obligations
    }
    for item in replacements:
        if item.obligation_id not in by_id:
            raise KeyError(
                f"UnknownObligation: {item.obligation_id}"
            )
        original = by_id[item.obligation_id]
        if (
            item.category != original.category
            or item.owner_symbol_id != original.owner_symbol_id
        ):
            raise ValueError(
                "ObligationIdentityMutation: "
                f"{item.obligation_id}"
            )
        by_id[item.obligation_id] = item
    return ObligationProgram(
        program.hir_digest,
        tuple(sorted(by_id.values(), key=lambda item: item.obligation_id)),
    )


__all__ = [
    "OBLIGATION_IR_CONTRACT",
    "OBLIGATION_IR_SCHEMA_VERSION",
    "ObligationBinding",
    "ObligationCategory",
    "ObligationDisposition",
    "ObligationProgram",
    "TypedObligation",
    "build_obligation_ir",
    "replace_obligations",
    "extend_obligations",
]
