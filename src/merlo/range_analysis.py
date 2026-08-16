from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from merlo.obligation_ir import (
    ObligationBinding,
    ObligationCategory,
    ObligationDisposition,
    TypedObligation,
)
from merlo.structured_hir_v2 import (
    HIRFunction,
    HIRNode,
    StructuredHIRProgram,
)


RANGE_ANALYSIS_SCHEMA_VERSION = 1
RANGE_ANALYSIS_CONTRACT = "merlo.constant-range-analysis.v1"

_INTEGER_BOUNDS = {
    "Byte": (0, 255),
    "UInt8": (0, 255),
    "Int8": (-128, 127),
    "UInt16": (0, 65535),
    "Int16": (-32768, 32767),
    "UInt32": (0, 4294967295),
    "Int32": (-2147483648, 2147483647),
    "UInt64": (0, 18446744073709551615),
    "Int64": (-9223372036854775808, 9223372036854775807),
}


@dataclass(frozen=True)
class IntegerRange:
    lower: int
    upper: int

    def __post_init__(self) -> None:
        if self.lower > self.upper:
            raise ValueError(
                f"EmptyIntegerRange: {self.lower}..{self.upper}"
            )

    @classmethod
    def for_type(cls, type_name: str) -> "IntegerRange | None":
        bounds = _INTEGER_BOUNDS.get(type_name)
        return cls(*bounds) if bounds is not None else None

    @classmethod
    def constant(cls, value: int) -> "IntegerRange":
        return cls(value, value)

    def intersect(
        self,
        other: "IntegerRange",
    ) -> "IntegerRange | None":
        lower = max(self.lower, other.lower)
        upper = min(self.upper, other.upper)
        return (
            IntegerRange(lower, upper)
            if lower <= upper
            else None
        )

    def add(self, other: "IntegerRange") -> "IntegerRange":
        return IntegerRange(
            self.lower + other.lower,
            self.upper + other.upper,
        )

    def subtract(self, other: "IntegerRange") -> "IntegerRange":
        return IntegerRange(
            self.lower - other.upper,
            self.upper - other.lower,
        )

    def multiply(self, other: "IntegerRange") -> "IntegerRange":
        products = (
            self.lower * other.lower,
            self.lower * other.upper,
            self.upper * other.lower,
            self.upper * other.upper,
        )
        return IntegerRange(min(products), max(products))

    def to_dict(self) -> dict[str, int]:
        return {"lower": self.lower, "upper": self.upper}


@dataclass(frozen=True)
class RangeFact:
    node_id: str
    scope_id: str
    name: str | None
    type_name: str
    value_range: IntegerRange
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "scope_id": self.scope_id,
            "name": self.name,
            "type": self.type_name,
            "range": self.value_range.to_dict(),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RangeAnalysisResult:
    hir_digest: str
    facts: tuple[RangeFact, ...]
    obligations: tuple[TypedObligation, ...]
    unreachable_branch_ids: tuple[str, ...]
    schema_version: int = RANGE_ANALYSIS_SCHEMA_VERSION
    contract: str = RANGE_ANALYSIS_CONTRACT
    def __post_init__(self) -> None:
        fact_keys = tuple(
            (item.node_id, item.name or "", item.reason)
            for item in self.facts
        )
        if fact_keys != tuple(sorted(fact_keys)):
            raise ValueError("RangeFactsNotCanonical")
        obligation_ids = tuple(
            item.obligation_id for item in self.obligations
        )
        if obligation_ids != tuple(sorted(obligation_ids)):
            raise ValueError("RangeObligationsNotCanonical")
        if self.unreachable_branch_ids != tuple(
            sorted(set(self.unreachable_branch_ids))
        ):
            raise ValueError("UnreachableBranchesNotCanonical")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "hir_digest": self.hir_digest,
            "facts": [item.to_dict() for item in self.facts],
            "obligations": [
                item.to_dict() for item in self.obligations
            ],
            "unreachable_branch_ids": list(
                self.unreachable_branch_ids
            ),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass
class _State:
    ranges: dict[str, IntegerRange]

    def clone(self) -> "_State":
        return _State(dict(self.ranges))


class _Analyzer:
    def __init__(self, hir: StructuredHIRProgram) -> None:
        self.hir = hir
        self.facts: list[RangeFact] = []
        self.obligations: list[TypedObligation] = []
        self.unreachable: set[str] = set()
        self.node_obligations: dict[str, set[str]] = {}
        self.obligation_dispositions: dict[
            str, ObligationDisposition
        ] = {}

    @staticmethod
    def _name(node: HIRNode) -> str | None:
        value = node.attribute_map.get("name")
        return str(value) if value is not None else None

    @staticmethod
    def _literal(node: HIRNode) -> int | None:
        value = node.attribute_map.get("value")
        return (
            value
            if isinstance(value, int)
            and not isinstance(value, bool)
            else None
        )

    def _record_fact(
        self,
        node: HIRNode,
        value_range: IntegerRange,
        reason: str,
        *,
        name: str | None = None,
    ) -> None:
        if node.type_name is None:
            return
        self.facts.append(
            RangeFact(
                node.id,
                node.scope_id,
                name,
                node.type_name,
                value_range,
                reason,
            )
        )

    def _dependencies(
        self,
        nodes: tuple[HIRNode, ...],
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    obligation_id
                    for node in nodes
                    for obligation_id in self.node_obligations.get(
                        node.id,
                        (),
                    )
                }
            )
        )
    @staticmethod
    def _contains_call(nodes: tuple[HIRNode, ...]) -> bool:
        return any(
            descendant.kind.endswith("Call")
            for node in nodes
            for descendant in node.walk()
        )

    @staticmethod
    def _binary_result(
        operator: str,
        left: IntegerRange | None,
        right: IntegerRange | None,
    ) -> IntegerRange | None:
        if left is None or right is None:
            return None
        if operator == "Add":
            return left.add(right)
        if operator == "Sub":
            return left.subtract(right)
        if operator in {"Mult", "Mul"}:
            return left.multiply(right)
        return None

    def _expression(
        self,
        node: HIRNode,
        state: _State,
        function: HIRFunction,
    ) -> IntegerRange | None:
        attributes = node.attribute_map
        if node.kind == "Literal":
            value = self._literal(node)
            result = (
                IntegerRange.constant(value)
                if value is not None
                else None
            )
        elif node.kind == "Name":
            name = self._name(node)
            result = state.ranges.get(name or "")
            if result is None and node.type_name is not None:
                result = IntegerRange.for_type(node.type_name)
        elif node.kind == "ScalarCast" and len(node.children) == 1:
            source_range = self._expression(
                node.children[0],
                state,
                function,
            )
            target_range = IntegerRange.for_type(
                node.type_name or ""
            )
            if source_range is not None and target_range is not None:
                self._range_obligation(
                    node,
                    source_range,
                    target_range,
                    function,
                    ObligationCategory.TYPE_SAFETY,
                    (
                        f"{target_range.lower} <= cast_input <= "
                        f"{target_range.upper}"
                    ),
                    dependencies=self._dependencies(node.children),
                )
                result = (
                    source_range.intersect(target_range)
                    or target_range
                )
            else:
                result = target_range or source_range
        elif node.kind in {
            "Binary",
            "NumericIntrinsic",
            "AugAssign",
        } and len(node.children) == 2:
            left = self._expression(
                node.children[0],
                state,
                function,
            )
            right = self._expression(
                node.children[1],
                state,
                function,
            )
            operator = str(attributes.get("operator", ""))
            checked = node.kind in {"Binary", "AugAssign"}
            if node.kind == "NumericIntrinsic":
                callee = str(attributes.get("callee", ""))
                operator = {
                    "checked_add": "Add",
                    "checked_sub": "Sub",
                    "checked_mul": "Mult",
                    "wrapping_add": "Add",
                    "wrapping_sub": "Sub",
                    "wrapping_mul": "Mult",
                }.get(callee, operator)
                checked = callee.startswith("checked_")
            result = self._binary_result(
                operator,
                left,
                right,
            )
            if not checked and node.kind == "NumericIntrinsic":
                result = IntegerRange.for_type(node.type_name or "")
            elif checked:
                if result is None:
                    self._unresolved_arithmetic_obligation(
                        node,
                        function,
                        dependencies=self._dependencies(node.children),
                    )
                else:
                    self._arithmetic_obligation(
                        node,
                        result,
                        function,
                        dependencies=self._dependencies(node.children),
                        force_unresolved=self._contains_call(
                            node.children
                        ),
                    )
        elif node.kind == "Unary" and len(node.children) == 1:
            operand = self._expression(
                node.children[0],
                state,
                function,
            )
            operator = str(attributes.get("operator", ""))
            result = (
                IntegerRange(-operand.upper, -operand.lower)
                if operand is not None and operator == "USub"
                else operand
            )
            if (
                result is not None
                and attributes.get("overflow") == "checked"
            ):
                self._arithmetic_obligation(
                    node,
                    result,
                    function,
                    dependencies=self._dependencies(node.children),
                    force_unresolved=self._contains_call(node.children),
                )
        else:
            result = None
            for child in node.children:
                self._expression(child, state, function)
            dependencies = self._dependencies(node.children)
            if dependencies:
                self.node_obligations[node.id] = set(dependencies)
        if result is None and node.type_name is not None:
            result = IntegerRange.for_type(node.type_name)
        if result is not None:
            self._record_fact(
                node,
                result,
                "constant_expression"
                if result.lower == result.upper
                else "interval_expression",
                name=self._name(node),
            )
        return result

    def _range_obligation(
        self,
        node: HIRNode,
        value_range: IntegerRange,
        allowed_range: IntegerRange,
        function: HIRFunction,
        category: ObligationCategory,
        predicate: str,
        *,
        force_unresolved: bool = False,
        dependencies: tuple[str, ...] = (),
    ) -> None:
        inside = (
            value_range.lower >= allowed_range.lower
            and value_range.upper <= allowed_range.upper
        )
        outside = (
            value_range.upper < allowed_range.lower
            or value_range.lower > allowed_range.upper
        )
        prerequisites_proven = all(
            self.obligation_dispositions.get(dependency)
            == ObligationDisposition.STATICALLY_PROVEN
            for dependency in dependencies
        )
        disposition = (
            ObligationDisposition.UNRESOLVED
            if force_unresolved or not prerequisites_proven
            else ObligationDisposition.STATICALLY_PROVEN
            if inside
            else ObligationDisposition.STATICALLY_REFUTED
            if outside
            else ObligationDisposition.UNRESOLVED
        )
        discharger = (
            "constant_range_analysis"
            if disposition
            in {
                ObligationDisposition.STATICALLY_PROVEN,
                ObligationDisposition.STATICALLY_REFUTED,
            }
            else None
        )
        identity = "obl_" + hashlib.sha256(
            (
                f"{function.symbol_id}\0{category.value}\0"
                f"{node.id}"
            ).encode()
        ).hexdigest()[:24]
        revision = "rev_" + hashlib.sha256(
            (
                f"{identity}\0{node.revision_id}\0"
                f"{value_range.lower}\0{value_range.upper}\0"
                f"{allowed_range.lower}\0{allowed_range.upper}\0"
                f"{','.join(dependencies)}"
            ).encode()
        ).hexdigest()[:24]
        context = tuple(
            ObligationBinding(
                parameter.name,
                parameter.type_name,
                parameter.ownership,
            )
            for parameter in function.parameters
        )
        self.obligations.append(
            TypedObligation(
                identity,
                revision,
                category,
                predicate,
                "Bool",
                function.symbol_id,
                function.revision_id,
                node.source,
                context,
                dependencies=dependencies,
                disposition=disposition,
                discharged_by=discharger,
            )
        )
        self.node_obligations.setdefault(node.id, set()).update(
            (*dependencies, identity)
        )
        self.obligation_dispositions[identity] = disposition

    def _arithmetic_obligation(
        self,
        node: HIRNode,
        result: IntegerRange,
        function: HIRFunction,
        *,
        dependencies: tuple[str, ...] = (),
        force_unresolved: bool = False,
    ) -> None:
        type_range = IntegerRange.for_type(node.type_name or "")
        if type_range is None:
            return
        self._range_obligation(
            node,
            result,
            type_range,
            function,
            ObligationCategory.ARITHMETIC_SAFETY,
            (
                f"{type_range.lower} <= result <= "
                f"{type_range.upper}"
            ),
            dependencies=dependencies,
            force_unresolved=force_unresolved,
        )

    def _unresolved_arithmetic_obligation(
        self,
        node: HIRNode,
        function: HIRFunction,
        *,
        dependencies: tuple[str, ...] = (),
    ) -> None:
        type_range = IntegerRange.for_type(node.type_name or "")
        if type_range is None:
            return
        self._range_obligation(
            node,
            type_range,
            type_range,
            function,
            ObligationCategory.ARITHMETIC_SAFETY,
            (
                f"{type_range.lower} <= result <= "
                f"{type_range.upper}"
            ),
            force_unresolved=True,
            dependencies=dependencies,
        )

    def _comparison_refinement(
        self,
        node: HIRNode,
        state: _State,
        *,
        truth: bool,
        reason: str | None = None,
    ) -> _State | None:
        if node.kind != "Compare" or len(node.children) != 2:
            return state.clone()
        left, right = node.children
        name = self._name(left)
        constant = self._literal(right)
        operators = node.attribute_map.get("operators")
        if (
            name is None
            or constant is None
            or not isinstance(operators, list)
            or len(operators) != 1
        ):
            return state.clone()
        current = state.ranges.get(name)
        if current is None:
            current = IntegerRange.for_type(left.type_name or "")
        if current is None:
            return state.clone()
        operator = str(operators[0])
        if not truth:
            operator = {
                "Lt": "GtE",
                "LtE": "Gt",
                "Gt": "LtE",
                "GtE": "Lt",
                "Eq": "NotEq",
                "NotEq": "Eq",
            }.get(operator, operator)
        candidate = {
            "Lt": IntegerRange(current.lower, constant - 1)
            if current.lower <= constant - 1
            else None,
            "LtE": IntegerRange(current.lower, constant)
            if current.lower <= constant
            else None,
            "Gt": IntegerRange(constant + 1, current.upper)
            if constant + 1 <= current.upper
            else None,
            "GtE": IntegerRange(constant, current.upper)
            if constant <= current.upper
            else None,
            "Eq": IntegerRange.constant(constant),
        }.get(operator)
        if operator == "NotEq":
            if current.lower == current.upper == constant:
                return None
            return state.clone()
        if candidate is None:
            return None
        refined = current.intersect(candidate)
        if refined is None:
            return None
        result = state.clone()
        result.ranges[name] = refined
        self._record_fact(
            left,
            refined,
            reason
            or ("branch_true" if truth else "branch_false"),
            name=name,
        )
        return result

    def _statement(
        self,
        node: HIRNode,
        state: _State,
        function: HIRFunction,
    ) -> None:
        if node.kind == "AugAssign":
            value_range = self._expression(
                node,
                state,
                function,
            )
            name = node.attribute_map.get("target")
            if isinstance(name, str) and value_range is not None:
                state.ranges[name] = value_range
                self._record_fact(
                    node,
                    value_range,
                    "augmented_assignment",
                    name=name,
                )
            return
        if node.kind in {"LetBinding", "VarBinding", "Assign"}:
            if node.children:
                value_range = self._expression(
                    node.children[-1],
                    state,
                    function,
                )
                name = self._name(node)
                if name is not None and value_range is not None:
                    state.ranges[name] = value_range
                    self._record_fact(
                        node,
                        value_range,
                        "binding",
                        name=name,
                    )
            return
        if node.kind == "If" and len(node.children) >= 3:
            condition, then_node, else_node = node.children[:3]
            self._expression(condition, state, function)
            then_state = self._comparison_refinement(
                condition,
                state,
                truth=True,
            )
            else_state = self._comparison_refinement(
                condition,
                state,
                truth=False,
            )
            if then_state is None:
                self.unreachable.add(then_node.id)
            else:
                for child in then_node.children:
                    self._statement(child, then_state, function)
            if else_state is None:
                self.unreachable.add(else_node.id)
            else:
                for child in else_node.children:
                    self._statement(child, else_state, function)
            return
        self._expression(node, state, function)

    def _function(self, function: HIRFunction) -> None:
        state = _State(
            {
                parameter.name: value_range
                for parameter in function.parameters
                for value_range in (
                    IntegerRange.for_type(parameter.type_name),
                )
                if value_range is not None
            }
        )
        for contract in function.requirements:
            refined = self._comparison_refinement(
                contract.condition,
                state,
                truth=True,
                reason="precondition",
            )
            if refined is None:
                self.unreachable.add(function.scope_id)
                return
            state = refined
        for node in function.body:
            self._statement(node, state, function)

    def analyze(self) -> RangeAnalysisResult:
        for function in self.hir.functions:
            self._function(function)
        facts = tuple(
            sorted(
                self.facts,
                key=lambda item: (
                    item.node_id,
                    item.name or "",
                    item.reason,
                ),
            )
        )
        obligations = tuple(
            sorted(
                self.obligations,
                key=lambda item: item.obligation_id,
            )
        )
        return RangeAnalysisResult(
            self.hir.digest,
            facts,
            obligations,
            tuple(sorted(self.unreachable)),
        )


def analyze_constant_ranges(
    hir: StructuredHIRProgram,
) -> RangeAnalysisResult:
    return _Analyzer(hir).analyze()


__all__ = [
    "RANGE_ANALYSIS_CONTRACT",
    "RANGE_ANALYSIS_SCHEMA_VERSION",
    "IntegerRange",
    "RangeAnalysisResult",
    "RangeFact",
    "analyze_constant_ranges",
]
