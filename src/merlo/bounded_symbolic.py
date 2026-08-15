from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from merlo.obligation_ir import (
    ObligationCategory,
    ObligationProgram,
)
from merlo.range_analysis import IntegerRange
from merlo.structured_hir_v2 import (
    HIRContract,
    HIRFunction,
    HIRNode,
    StructuredHIRProgram,
)


BOUNDED_SYMBOLIC_SCHEMA_VERSION = 1
BOUNDED_SYMBOLIC_CONTRACT = "merlo.bounded-symbolic.v1"


class SymbolicStatus(str, Enum):
    PROVEN = "proven"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class SymbolicCounterexample:
    inputs: tuple[tuple[str, int | bool], ...]
    result: int | bool | None
    predicate: str
    def __post_init__(self) -> None:
        if self.inputs != tuple(sorted(self.inputs)):
            raise ValueError("CounterexampleInputsNotCanonical")

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputs": dict(self.inputs),
            "result": self.result,
            "predicate": self.predicate,
        }


@dataclass(frozen=True)
class SymbolicObligationResult:
    obligation_id: str
    status: SymbolicStatus
    explored_cases: int
    complete_domain: bool
    counterexample: SymbolicCounterexample | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "status": self.status.value,
            "explored_cases": self.explored_cases,
            "complete_domain": self.complete_domain,
            "counterexample": (
                self.counterexample.to_dict()
                if self.counterexample is not None
                else None
            ),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class BoundedSymbolicReport:
    hir_digest: str
    obligation_digest: str
    results: tuple[SymbolicObligationResult, ...]
    max_cases: int
    max_values_per_parameter: int
    schema_version: int = BOUNDED_SYMBOLIC_SCHEMA_VERSION
    contract: str = BOUNDED_SYMBOLIC_CONTRACT
    def __post_init__(self) -> None:
        identifiers = tuple(
            item.obligation_id for item in self.results
        )
        if identifiers != tuple(sorted(identifiers)):
            raise ValueError("SymbolicResultsNotCanonical")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("DuplicateSymbolicResult")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "hir_digest": self.hir_digest,
            "obligation_digest": self.obligation_digest,
            "max_cases": self.max_cases,
            "max_values_per_parameter": (
                self.max_values_per_parameter
            ),
            "results": [item.to_dict() for item in self.results],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )


class _UnsupportedSymbolicNode(ValueError):
    pass


class _Executor:
    _BINARY = {
        "Add": lambda left, right: left + right,
        "Sub": lambda left, right: left - right,
        "Mult": lambda left, right: left * right,
        "FloorDiv": lambda left, right: left // right,
        "Mod": lambda left, right: left % right,
        "BitAnd": lambda left, right: left & right,
        "BitOr": lambda left, right: left | right,
        "BitXor": lambda left, right: left ^ right,
        "LShift": lambda left, right: left << right,
        "RShift": lambda left, right: left >> right,
        "And": lambda left, right: bool(left and right),
        "Or": lambda left, right: bool(left or right),
    }
    _COMPARE = {
        "Eq": lambda left, right: left == right,
        "NotEq": lambda left, right: left != right,
        "Lt": lambda left, right: left < right,
        "LtE": lambda left, right: left <= right,
        "Gt": lambda left, right: left > right,
        "GtE": lambda left, right: left >= right,
    }
    def __init__(self, invariant_types: frozenset[str]) -> None:
        self.invariant_types = invariant_types


    @staticmethod
    def _bounded_value(node: HIRNode, value: Any) -> Any:
        if not isinstance(value, int) or isinstance(value, bool):
            return value
        value_range = IntegerRange.for_type(node.type_name or "")
        if value_range is None:
            return value
        if not value_range.lower <= value <= value_range.upper:
            raise ArithmeticError(
                f"SymbolicOverflow: {node.id}"
            )
        return value

    @classmethod
    def _scalar_cast(cls, node: HIRNode, value: Any) -> Any:
        target = node.type_name or ""
        value_range = IntegerRange.for_type(target)
        if value_range is not None:
            if isinstance(value, float):
                if not math.isfinite(value):
                    raise _UnsupportedSymbolicNode(
                        f"UnsupportedSymbolicFloatCast: {node.id}"
                    )
                value = int(value)
            if not isinstance(value, int) or isinstance(value, bool):
                raise _UnsupportedSymbolicNode(
                    f"UnsupportedSymbolicCast: {target}"
                )
            return cls._bounded_value(node, value)
        if target == "Float64" and isinstance(value, (int, float)):
            return float(value)
        raise _UnsupportedSymbolicNode(
            f"UnsupportedSymbolicCast: {target}"
        )

    @staticmethod
    def _wrapping_value(node: HIRNode, value: Any) -> Any:
        if not isinstance(value, int) or isinstance(value, bool):
            return value
        value_range = IntegerRange.for_type(node.type_name or "")
        if value_range is None:
            return value
        width = value_range.upper - value_range.lower + 1
        return (value - value_range.lower) % width + value_range.lower

    def expression(
        self,
        node: HIRNode,
        environment: dict[str, Any],
    ) -> Any:
        attributes = node.attribute_map
        if node.kind == "Literal":
            return attributes.get("value")
        if node.kind == "Name":
            name = str(attributes.get("name"))
            if name not in environment:
                raise _UnsupportedSymbolicNode(
                    f"UnknownSymbolicName: {name}"
                )
            return environment[name]
        if node.kind == "ScalarCast" and len(node.children) == 1:
            return self._scalar_cast(
                node,
                self.expression(node.children[0], environment),
            )
        if node.kind == "Unary" and len(node.children) == 1:
            value = self.expression(node.children[0], environment)
            operator = str(attributes.get("operator"))
            if operator == "Not":
                return not value
            if operator == "USub":
                return -value
            if operator == "UAdd":
                return +value
            if operator == "Invert":
                return ~value
        if node.kind in {
            "Binary",
            "NumericIntrinsic",
            "AugAssign",
        } and len(node.children) == 2:
            operator = str(attributes.get("operator", ""))
            checked = node.kind in {"Binary", "AugAssign"}
            wrapping = False
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
                wrapping = callee.startswith("wrapping_")
            operation = self._BINARY.get(operator)
            if operation is None:
                raise _UnsupportedSymbolicNode(
                    f"UnsupportedSymbolicBinary: {operator}"
                )
            result = operation(
                self.expression(node.children[0], environment),
                self.expression(node.children[1], environment),
            )
            if checked:
                return self._bounded_value(node, result)
            if wrapping:
                return self._wrapping_value(node, result)
            return result
        if node.kind == "Compare" and len(node.children) >= 2:
            operators = attributes.get("operators")
            if not isinstance(operators, list):
                raise _UnsupportedSymbolicNode(
                    "UnsupportedSymbolicCompare"
                )
            values = [
                self.expression(child, environment)
                for child in node.children
            ]
            return all(
                self._COMPARE[str(operator)](left, right)
                for operator, left, right in zip(
                    operators,
                    values[:-1],
                    values[1:],
                    strict=True,
                )
            )
        if node.kind == "FieldAccess" and len(node.children) == 1:
            value = self.expression(node.children[0], environment)
            field = str(attributes.get("field"))
            if isinstance(value, dict) and field in value:
                return value[field]
        if node.kind == "RecordConstruct":
            if node.type_name in self.invariant_types:
                raise _UnsupportedSymbolicNode(
                    f"RecordInvariantRequiresGuard: {node.type_name}"
                )
            field_names = attributes.get("field_names")
            if not isinstance(field_names, list):
                field_names = [
                    str(index) for index in range(len(node.children))
                ]
            return {
                name: self.expression(child, environment)
                for name, child in zip(
                    field_names,
                    node.children,
                    strict=True,
                )
            }
        raise _UnsupportedSymbolicNode(
            f"UnsupportedSymbolicNode: {node.kind}"
        )

    def statements(
        self,
        nodes: Iterable[HIRNode],
        environment: dict[str, Any],
    ) -> tuple[bool, Any]:
        for node in nodes:
            if node.kind in {"LetBinding", "VarBinding", "Assign"}:
                if not node.children:
                    raise _UnsupportedSymbolicNode(
                        f"MissingSymbolicValue: {node.id}"
                    )
                name = node.attribute_map.get("name")
                if not isinstance(name, str):
                    raise _UnsupportedSymbolicNode(
                        f"MissingSymbolicBinding: {node.id}"
                    )
                environment[name] = self.expression(
                    node.children[-1],
                    environment,
                )
                continue
            if node.kind == "AugAssign":
                name = node.attribute_map.get("target")
                if not isinstance(name, str):
                    raise _UnsupportedSymbolicNode(
                        f"MissingSymbolicBinding: {node.id}"
                    )
                if "." in name:
                    raise _UnsupportedSymbolicNode(
                        f"UnsupportedSymbolicFieldMutation: {name}"
                    )
                environment[name] = self.expression(
                    node,
                    environment,
                )
                continue
            if node.kind == "Return":
                value = (
                    self.expression(node.children[0], environment)
                    if node.children
                    else None
                )
                return True, value
            if node.kind == "If" and len(node.children) >= 3:
                condition, then_node, else_node = node.children[:3]
                branch = (
                    then_node
                    if self.expression(condition, environment)
                    else else_node
                )
                branch_environment = dict(environment)
                returned, value = self.statements(
                    branch.children,
                    branch_environment,
                )
                if returned:
                    return True, value
                environment.clear()
                environment.update(branch_environment)
                continue
            self.expression(node, environment)
        return False, None


class _Verifier:
    def __init__(
        self,
        hir: StructuredHIRProgram,
        obligations: ObligationProgram,
        *,
        max_cases: int,
        max_values_per_parameter: int,
    ) -> None:
        if obligations.hir_digest != hir.digest:
            raise ValueError("ObligationHIRDigestMismatch")
        if max_cases < 1 or max_values_per_parameter < 1:
            raise ValueError("InvalidSymbolicBound")
        self.hir = hir
        self.obligations = obligations
        self.max_cases = max_cases
        self.max_values_per_parameter = max_values_per_parameter
        self.executor = _Executor(
            frozenset(
                declaration.name
                for declaration in hir.types
                if declaration.invariants
            )
        )

    @staticmethod
    def _simple_bound(
        contract: HIRContract,
        parameter: str,
        current: IntegerRange,
    ) -> IntegerRange | None:
        node = contract.condition
        if node.kind != "Compare" or len(node.children) != 2:
            return current
        left, right = node.children
        name = left.attribute_map.get("name")
        value = right.attribute_map.get("value")
        operators = node.attribute_map.get("operators")
        if (
            name != parameter
            or not isinstance(value, int)
            or isinstance(value, bool)
            or not isinstance(operators, list)
            or len(operators) != 1
        ):
            return current
        operator = str(operators[0])
        candidate = {
            "Lt": IntegerRange(current.lower, value - 1)
            if current.lower <= value - 1
            else None,
            "LtE": IntegerRange(current.lower, value)
            if current.lower <= value
            else None,
            "Gt": IntegerRange(value + 1, current.upper)
            if value + 1 <= current.upper
            else None,
            "GtE": IntegerRange(value, current.upper)
            if value <= current.upper
            else None,
            "Eq": IntegerRange.constant(value),
        }.get(operator, current)
        return (
            current.intersect(candidate)
            if candidate is not None
            else None
        )

    def _domains(
        self,
        function: HIRFunction,
    ) -> tuple[
        tuple[tuple[str, tuple[int | bool, ...]], ...],
        bool,
    ]:
        domains: list[tuple[str, tuple[int | bool, ...]]] = []
        complete = True
        for parameter in function.parameters:
            if parameter.type_name == "Bool":
                domains.append((parameter.name, (False, True)))
                continue
            value_range = IntegerRange.for_type(parameter.type_name)
            if value_range is None:
                return (), False
            for requirement in function.requirements:
                value_range = self._simple_bound(
                    requirement,
                    parameter.name,
                    value_range,
                )
                if value_range is None:
                    break
            if value_range is None:
                values: tuple[int, ...] = ()
            else:
                width = value_range.upper - value_range.lower + 1
                if width <= self.max_values_per_parameter:
                    values = tuple(
                        range(
                            value_range.lower,
                            value_range.upper + 1,
                        )
                    )
                else:
                    complete = False
                    candidates = (
                        value_range.lower,
                        value_range.upper,
                        value_range.lower
                        + (
                            value_range.upper
                            - value_range.lower
                        )
                        // 2,
                    )
                    values = tuple(
                        sorted(set(candidates))[
                            : self.max_values_per_parameter
                        ]
                    )
            domains.append((parameter.name, values))
        cases = 1
        for _name, values in domains:
            cases *= len(values)
        if cases > self.max_cases:
            complete = False
        return tuple(domains), complete

    def _postconditions(
        self,
        function: HIRFunction,
    ) -> tuple[tuple[str, HIRContract], ...]:
        candidates = {
            (
                item.source.line,
                item.predicate,
            ): item.obligation_id
            for item in self.obligations.by_category(
                ObligationCategory.FUNCTION_POSTCONDITION
            )
            if item.owner_symbol_id == function.symbol_id
        }
        return tuple(
            (
                candidates[(contract.source.line, contract.expression)],
                contract,
            )
            for contract in function.ensures
            if (contract.source.line, contract.expression) in candidates
        )

    def _function(
        self,
        function: HIRFunction,
    ) -> tuple[SymbolicObligationResult, ...]:
        postconditions = self._postconditions(function)
        if not postconditions:
            return ()
        domains, complete = self._domains(function)
        if not domains and function.parameters:
            return tuple(
                SymbolicObligationResult(
                    obligation_id,
                    SymbolicStatus.UNSUPPORTED,
                    0,
                    False,
                    reason="unsupported parameter domain",
                )
                for obligation_id, _contract in postconditions
            )
        combinations = itertools.product(
            *(values for _name, values in domains)
        )
        failures: dict[str, SymbolicCounterexample] = {}
        unsupported: str | None = None
        explored = 0
        names = tuple(name for name, _values in domains)
        for values in combinations:
            if explored >= self.max_cases:
                complete = False
                break
            explored += 1
            environment = dict(zip(names, values, strict=True))
            try:
                if not all(
                    bool(
                        self.executor.expression(
                            requirement.condition,
                            environment,
                        )
                    )
                    for requirement in function.requirements
                ):
                    continue
                returned, result = self.executor.statements(
                    function.body,
                    dict(environment),
                )
                if not returned:
                    unsupported = "function has an unreturned path"
                    break
                post_environment = dict(environment)
                post_environment["result"] = result
                for obligation_id, contract in postconditions:
                    if obligation_id in failures:
                        continue
                    if not bool(
                        self.executor.expression(
                            contract.condition,
                            post_environment,
                        )
                    ):
                        failures[obligation_id] = (
                            SymbolicCounterexample(
                                tuple(sorted(environment.items())),
                                result,
                                contract.expression,
                            )
                        )
            except (
                ArithmeticError,
                KeyError,
                TypeError,
                ValueError,
                _UnsupportedSymbolicNode,
            ) as exc:
                unsupported = str(exc)
                break
        results = []
        for obligation_id, _contract in postconditions:
            counterexample = failures.get(obligation_id)
            if counterexample is not None:
                status = SymbolicStatus.REFUTED
                reason = None
            elif unsupported is not None:
                status = SymbolicStatus.UNSUPPORTED
                reason = unsupported
            elif complete:
                status = SymbolicStatus.PROVEN
                reason = None
            else:
                status = SymbolicStatus.INCONCLUSIVE
                reason = "bounded domain was not exhaustive"
            results.append(
                SymbolicObligationResult(
                    obligation_id,
                    status,
                    explored,
                    complete,
                    counterexample,
                    reason,
                )
            )
        return tuple(results)

    def verify(self) -> BoundedSymbolicReport:
        results = tuple(
            sorted(
                (
                    result
                    for function in self.hir.functions
                    for result in self._function(function)
                ),
                key=lambda item: item.obligation_id,
            )
        )
        return BoundedSymbolicReport(
            self.hir.digest,
            self.obligations.digest,
            results,
            self.max_cases,
            self.max_values_per_parameter,
        )


def verify_bounded(
    hir: StructuredHIRProgram,
    obligations: ObligationProgram,
    *,
    max_cases: int = 4096,
    max_values_per_parameter: int = 512,
) -> BoundedSymbolicReport:
    return _Verifier(
        hir,
        obligations,
        max_cases=max_cases,
        max_values_per_parameter=max_values_per_parameter,
    ).verify()


__all__ = [
    "BOUNDED_SYMBOLIC_CONTRACT",
    "BOUNDED_SYMBOLIC_SCHEMA_VERSION",
    "BoundedSymbolicReport",
    "SymbolicCounterexample",
    "SymbolicObligationResult",
    "SymbolicStatus",
    "verify_bounded",
]
