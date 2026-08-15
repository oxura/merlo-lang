from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import dataclass
from enum import Enum
from types import ModuleType
from typing import Any, Iterable

from merlo.obligation_ir import ObligationCategory, ObligationProgram
from merlo.range_analysis import IntegerRange
from merlo.structured_hir_v2 import (
    HIRContract,
    HIRFunction,
    HIRNode,
    StructuredHIRProgram,
)

SMT_SCHEMA_VERSION = 1
SMT_CONTRACT = "merlo.optional-smt.v1"


class SMTStatus(str, Enum):
    PROVEN = "proven"
    REFUTED = "refuted"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


@dataclass(frozen=True)
class SMTCounterexample:
    inputs: tuple[tuple[str, int | bool], ...]

    def __post_init__(self) -> None:
        if self.inputs != tuple(sorted(self.inputs)):
            raise ValueError("SMTCounterexampleNotCanonical")

    def to_dict(self) -> dict[str, Any]:
        return {"inputs": dict(self.inputs)}


@dataclass(frozen=True)
class SMTObligationResult:
    obligation_id: str
    status: SMTStatus
    backend: str
    query_sha256: str | None
    query_smt2: str | None
    counterexample: SMTCounterexample | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "status": self.status.value,
            "backend": self.backend,
            "query_sha256": self.query_sha256,
            "query_smt2": self.query_smt2,
            "counterexample": (
                self.counterexample.to_dict()
                if self.counterexample is not None
                else None
            ),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SMTReport:
    hir_digest: str
    obligation_digest: str
    backend: str
    backend_version: str | None
    timeout_ms: int
    max_paths: int
    results: tuple[SMTObligationResult, ...]
    schema_version: int = SMT_SCHEMA_VERSION
    contract: str = SMT_CONTRACT

    def __post_init__(self) -> None:
        identifiers = tuple(item.obligation_id for item in self.results)
        if identifiers != tuple(sorted(identifiers)):
            raise ValueError("SMTResultsNotCanonical")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("DuplicateSMTResult")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "hir_digest": self.hir_digest,
            "obligation_digest": self.obligation_digest,
            "backend": self.backend,
            "backend_version": self.backend_version,
            "timeout_ms": self.timeout_ms,
            "max_paths": self.max_paths,
            "results": [item.to_dict() for item in self.results],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )


class _UnsupportedSMT(ValueError):
    pass


@dataclass(frozen=True)
class _Term:
    text: str
    sort: str
    guards: tuple[str, ...] = ()
    constant: int | bool | None = None


@dataclass
class _Path:
    environment: dict[str, _Term]
    constraints: tuple[str, ...]
    result: _Term | None = None


@dataclass(frozen=True)
class _Variable:
    source_name: str
    smt_name: str
    type_name: str
    sort: str


@dataclass(frozen=True)
class _Query:
    obligation_id: str
    smt2: str
    variables: tuple[_Variable, ...]

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.smt2.encode()).hexdigest()


def _integer(value: int) -> str:
    return str(value) if value >= 0 else f"(- {-value})"


def _and(parts: Iterable[str]) -> str:
    values = tuple(parts)
    if not values:
        return "true"
    if len(values) == 1:
        return values[0]
    return f"(and {' '.join(values)})"


class _Translator:
    _BINARY = {
        "Add": "+",
        "Sub": "-",
        "Mult": "*",
        "FloorDiv": "div",
        "Mod": "mod",
        "And": "and",
        "Or": "or",
    }
    _COMPARE = {
        "Eq": "=",
        "NotEq": "distinct",
        "Lt": "<",
        "LtE": "<=",
        "Gt": ">",
        "GtE": ">=",
    }
    def __init__(self, max_paths: int) -> None:
        self.max_paths = max_paths


    def expression(
        self,
        node: HIRNode,
        environment: dict[str, _Term],
    ) -> _Term:
        attributes = node.attribute_map
        if node.kind == "Literal":
            value = attributes.get("value")
            if isinstance(value, bool):
                return _Term(
                    "true" if value else "false",
                    "Bool",
                    constant=value,
                )
            if isinstance(value, int):
                return _Term(
                    _integer(value),
                    "Int",
                    constant=value,
                )
        if node.kind == "Name":
            name = attributes.get("name")
            if isinstance(name, str) and name in environment:
                return environment[name]
            raise _UnsupportedSMT(f"UnknownSMTName: {name}")
        if node.kind == "ScalarCast" and len(node.children) == 1:
            value = self.expression(node.children[0], environment)
            value_range = IntegerRange.for_type(node.type_name or "")
            if value_range is None or value.sort != "Int":
                raise _UnsupportedSMT(
                    f"UnsupportedSMTCast: {node.type_name}"
                )
            guards = value.guards
            if not (
                isinstance(value.constant, int)
                and not isinstance(value.constant, bool)
                and value_range.lower
                <= value.constant
                <= value_range.upper
            ):
                guards = (
                    *guards,
                    _and(
                        (
                            f"(<= {_integer(value_range.lower)} {value.text})",
                            f"(<= {value.text} {_integer(value_range.upper)})",
                        )
                    ),
                )
            return _Term(
                value.text,
                "Int",
                guards,
                value.constant,
            )
        if node.kind == "Unary" and len(node.children) == 1:
            value = self.expression(node.children[0], environment)
            operator = str(attributes.get("operator", ""))
            if operator == "Not":
                return _Term(f"(not {value.text})", "Bool", value.guards)
            if operator == "USub":
                result = f"(- {value.text})"
                guards = value.guards
                value_range = IntegerRange.for_type(node.type_name or "")
                if value_range is not None:
                    guards = (
                        *guards,
                        _and(
                            (
                                f"(<= {_integer(value_range.lower)} {result})",
                                f"(<= {result} {_integer(value_range.upper)})",
                            )
                        ),
                    )
                return _Term(result, "Int", guards)
            if operator == "UAdd":
                return value
            raise _UnsupportedSMT(f"UnsupportedSMTUnary: {operator}")
        if node.kind in {
            "Binary",
            "NumericIntrinsic",
            "AugAssign",
        } and len(node.children) == 2:
            left = self.expression(node.children[0], environment)
            right = self.expression(node.children[1], environment)
            operator = str(attributes.get("operator", ""))
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
                wrapping = callee.startswith("wrapping_")
            smt_operator = self._BINARY.get(operator)
            if smt_operator is None or left.sort != right.sort:
                raise _UnsupportedSMT(
                    f"UnsupportedSMTBinary: {operator}"
                )
            if (
                operator in {"FloorDiv", "Mod"}
                and node.type_name
                in {"Int8", "Int16", "Int32", "Int64"}
            ):
                raise _UnsupportedSMT(
                    f"UnsupportedSignedSMTDivision: {operator}"
                )
            result_sort = "Bool" if operator in {"And", "Or"} else "Int"
            result = f"({smt_operator} {left.text} {right.text})"
            guards = (*left.guards, *right.guards)
            if operator in {"FloorDiv", "Mod"}:
                guards = (*guards, f"(distinct {right.text} 0)")
            value_range = IntegerRange.for_type(node.type_name or "")
            if result_sort == "Int" and value_range is not None:
                if wrapping:
                    width = value_range.upper - value_range.lower + 1
                    result = (
                        f"(+ {_integer(value_range.lower)} "
                        f"(mod (- {result} {_integer(value_range.lower)}) "
                        f"{width}))"
                    )
                else:
                    guards = (
                        *guards,
                        _and(
                            (
                                f"(<= {_integer(value_range.lower)} {result})",
                                f"(<= {result} {_integer(value_range.upper)})",
                            )
                        ),
                    )
            return _Term(result, result_sort, guards)
        if node.kind == "Compare" and len(node.children) >= 2:
            operators = attributes.get("operators")
            if not isinstance(operators, list):
                raise _UnsupportedSMT("UnsupportedSMTCompare")
            values = [
                self.expression(child, environment)
                for child in node.children
            ]
            if len(operators) != len(values) - 1:
                raise _UnsupportedSMT("UnsupportedSMTCompare")
            comparisons = []
            for operator, left, right in zip(
                operators,
                values[:-1],
                values[1:],
                strict=True,
            ):
                smt_operator = self._COMPARE.get(str(operator))
                if smt_operator is None or left.sort != right.sort:
                    raise _UnsupportedSMT(
                        f"UnsupportedSMTCompare: {operator}"
                    )
                comparisons.append(
                    f"({smt_operator} {left.text} {right.text})"
                )
            return _Term(
                _and(comparisons),
                "Bool",
                tuple(
                    guard
                    for value in values
                    for guard in value.guards
                ),
            )
        raise _UnsupportedSMT(f"UnsupportedSMTNode: {node.kind}")

    def statements(
        self,
        nodes: Iterable[HIRNode],
        paths: list[_Path],
    ) -> list[_Path]:
        active = paths
        for node in nodes:
            next_paths: list[_Path] = []
            for path in active:
                if len(next_paths) > self.max_paths:
                    raise _UnsupportedSMT(
                        f"SMTPathLimitExceeded: {self.max_paths}"
                    )
                if path.result is not None:
                    next_paths.append(path)
                    continue
                if node.kind in {"LetBinding", "VarBinding", "Assign"}:
                    if not node.children:
                        raise _UnsupportedSMT(
                            f"MissingSMTValue: {node.id}"
                        )
                    name = node.attribute_map.get("name")
                    if not isinstance(name, str):
                        raise _UnsupportedSMT(
                            f"MissingSMTBinding: {node.id}"
                        )
                    value = self.expression(
                        node.children[-1], path.environment
                    )
                    environment = dict(path.environment)
                    environment[name] = value
                    next_paths.append(
                        _Path(
                            environment,
                            (*path.constraints, *value.guards),
                        )
                    )
                    continue
                if node.kind == "AugAssign":
                    name = node.attribute_map.get("target")
                    if not isinstance(name, str):
                        raise _UnsupportedSMT(
                            f"MissingSMTBinding: {node.id}"
                        )
                    value = self.expression(node, path.environment)
                    environment = dict(path.environment)
                    environment[name] = value
                    next_paths.append(
                        _Path(
                            environment,
                            (*path.constraints, *value.guards),
                        )
                    )
                    continue
                if node.kind == "Return":
                    result = (
                        self.expression(node.children[0], path.environment)
                        if node.children
                        else _Term("true", "Bool")
                    )
                    next_paths.append(
                        _Path(
                            path.environment,
                            (*path.constraints, *result.guards),
                            result,
                        )
                    )
                    continue
                if node.kind == "If" and len(node.children) >= 3:
                    condition, then_node, else_node = node.children[:3]
                    value = self.expression(condition, path.environment)
                    if value.sort != "Bool":
                        raise _UnsupportedSMT("NonBooleanSMTCondition")
                    common = (*path.constraints, *value.guards)
                    next_paths.extend(
                        self.statements(
                            then_node.children,
                            [
                                _Path(
                                    dict(path.environment),
                                    (*common, value.text),
                                )
                            ],
                        )
                    )
                    next_paths.extend(
                        self.statements(
                            else_node.children,
                            [
                                _Path(
                                    dict(path.environment),
                                    (*common, f"(not {value.text})"),
                                )
                            ],
                        )
                    )
                    continue
                value = self.expression(node, path.environment)
                next_paths.append(
                    _Path(
                        path.environment,
                        (*path.constraints, *value.guards),
                    )
                )
            if len(next_paths) > self.max_paths:
                raise _UnsupportedSMT(
                    f"SMTPathLimitExceeded: {self.max_paths}"
                )
            active = next_paths
        return active


def _postconditions(
    function: HIRFunction,
    obligations: ObligationProgram,
) -> tuple[tuple[str, HIRContract], ...]:
    candidates = {
        (item.source.line, item.predicate): item.obligation_id
        for item in obligations.by_category(
            ObligationCategory.FUNCTION_POSTCONDITION
        )
        if item.owner_symbol_id == function.symbol_id
    }
    return tuple(
        (candidates[(contract.source.line, contract.expression)], contract)
        for contract in function.ensures
        if (contract.source.line, contract.expression) in candidates
    )


def _queries(
    hir: StructuredHIRProgram,
    obligations: ObligationProgram,
    max_paths: int,
) -> tuple[tuple[_Query, ...], dict[str, str]]:
    translator = _Translator(max_paths)
    queries: list[_Query] = []
    unsupported: dict[str, str] = {}
    for function in hir.functions:
        postconditions = _postconditions(function, obligations)
        if not postconditions:
            continue
        try:
            variables: list[_Variable] = []
            environment: dict[str, _Term] = {}
            declarations: list[str] = []
            base_constraints: list[str] = []
            for index, parameter in enumerate(function.parameters):
                name = f"p{index}"
                if parameter.type_name == "Bool":
                    sort = "Bool"
                else:
                    value_range = IntegerRange.for_type(parameter.type_name)
                    if value_range is None:
                        raise _UnsupportedSMT(
                            f"UnsupportedSMTType: {parameter.type_name}"
                        )
                    sort = "Int"
                    base_constraints.extend(
                        (
                            f"(<= {_integer(value_range.lower)} {name})",
                            f"(<= {name} {_integer(value_range.upper)})",
                        )
                    )
                variables.append(
                    _Variable(
                        parameter.name,
                        name,
                        parameter.type_name,
                        sort,
                    )
                )
                environment[parameter.name] = _Term(name, sort)
                declarations.append(f"(declare-const {name} {sort})")
            for requirement in function.requirements:
                value = translator.expression(
                    requirement.condition, environment
                )
                if value.sort != "Bool":
                    raise _UnsupportedSMT("NonBooleanSMTRequirement")
                base_constraints.extend((*value.guards, value.text))
            paths = translator.statements(
                function.body,
                [_Path(dict(environment), ())],
            )
            returned = [path for path in paths if path.result is not None]
            if not returned:
                raise _UnsupportedSMT("FunctionHasNoSMTReturnPath")
            for obligation_id, contract in postconditions:
                counterexample_paths = []
                for path in returned:
                    assert path.result is not None
                    post_environment = dict(path.environment)
                    post_environment["result"] = _Term(
                        path.result.text,
                        path.result.sort,
                        constant=path.result.constant,
                    )
                    value = translator.expression(
                        contract.condition, post_environment
                    )
                    if value.sort != "Bool":
                        raise _UnsupportedSMT("NonBooleanSMTPostcondition")
                    if value.guards:
                        unsupported[obligation_id] = (
                            "PostconditionMayTrap"
                        )
                        counterexample_paths = []
                        break
                    counterexample_paths.append(
                        _and(
                            (
                                *path.constraints,
                                f"(not {value.text})",
                            )
                        )
                    )
                if not counterexample_paths:
                    continue
                assertion = _and(
                    (
                        *base_constraints,
                        f"(or {' '.join(counterexample_paths)})"
                        if len(counterexample_paths) > 1
                        else counterexample_paths[0],
                    )
                )
                smt2 = "\n".join(
                    (
                        "(set-logic ALL)",
                        *declarations,
                        f"(assert {assertion})",
                        "",
                    )
                )
                queries.append(
                    _Query(obligation_id, smt2, tuple(variables))
                )
        except _UnsupportedSMT as exc:
            for obligation_id, _contract in postconditions:
                unsupported[obligation_id] = str(exc)
    return tuple(
        sorted(queries, key=lambda item: item.obligation_id)
    ), unsupported


def _load_z3() -> ModuleType | None:
    try:
        return importlib.import_module("z3")
    except ImportError:
        return None


def _z3_version(z3: ModuleType) -> str | None:
    getter = getattr(z3, "get_version_string", None)
    return str(getter()) if callable(getter) else None


def _solve_z3(
    query: _Query,
    z3: ModuleType,
    timeout_ms: int,
) -> SMTObligationResult:
    solver = z3.Solver()
    solver.set(timeout=timeout_ms, random_seed=0)
    solver.from_string(query.smt2)
    outcome = solver.check()
    if outcome == z3.unsat:
        return SMTObligationResult(
            query.obligation_id,
            SMTStatus.PROVEN,
            "z3",
            query.digest,
            query.smt2,
        )
    if outcome == z3.sat:
        model = solver.model()
        inputs = []
        for variable in query.variables:
            symbol = (
                z3.Bool(variable.smt_name)
                if variable.sort == "Bool"
                else z3.Int(variable.smt_name)
            )
            value = model.eval(symbol, model_completion=True)
            if variable.sort == "Bool":
                decoded: int | bool = bool(z3.is_true(value))
            else:
                decoded = int(value.as_long())
            inputs.append((variable.source_name, decoded))
        return SMTObligationResult(
            query.obligation_id,
            SMTStatus.REFUTED,
            "z3",
            query.digest,
            query.smt2,
            SMTCounterexample(tuple(sorted(inputs))),
        )
    return SMTObligationResult(
        query.obligation_id,
        SMTStatus.UNKNOWN,
        "z3",
        query.digest,
        query.smt2,
        reason=str(solver.reason_unknown()),
    )


def verify_smt(
    hir: StructuredHIRProgram,
    obligations: ObligationProgram,
    *,
    backend: str | None = None,
    timeout_ms: int = 1000,
    max_paths: int = 256,
    z3_module: ModuleType | None = None,
) -> SMTReport:
    if obligations.hir_digest != hir.digest:
        raise ValueError("ObligationHIRDigestMismatch")
    if timeout_ms < 1:
        raise ValueError("InvalidSMTTimeout")
    if max_paths < 1:
        raise ValueError("InvalidSMTPathLimit")
    if backend not in {None, "z3"}:
        raise ValueError(f"UnknownSMTBackend: {backend}")
    queries, unsupported = _queries(
        hir,
        obligations,
        max_paths,
    )
    query_by_id = {item.obligation_id: item for item in queries}
    obligation_ids = tuple(
        sorted(
            item.obligation_id
            for function in hir.functions
            for item in obligations.by_category(
                ObligationCategory.FUNCTION_POSTCONDITION
            )
            if item.owner_symbol_id == function.symbol_id
        )
    )
    loaded_z3 = None
    status = SMTStatus.DISABLED
    backend_name = "disabled"
    if backend == "z3":
        loaded_z3 = z3_module or _load_z3()
        status = (
            SMTStatus.UNAVAILABLE
            if loaded_z3 is None
            else SMTStatus.UNKNOWN
        )
        backend_name = "z3"
    results = []
    for obligation_id in obligation_ids:
        query = query_by_id.get(obligation_id)
        if obligation_id in unsupported:
            results.append(
                SMTObligationResult(
                    obligation_id,
                    SMTStatus.UNSUPPORTED,
                    backend_name,
                    None,
                    None,
                    reason=unsupported[obligation_id],
                )
            )
        elif query is None:
            results.append(
                SMTObligationResult(
                    obligation_id,
                    SMTStatus.UNSUPPORTED,
                    backend_name,
                    None,
                    None,
                    reason="SMTQueryMissing",
                )
            )
        elif loaded_z3 is not None:
            results.append(_solve_z3(query, loaded_z3, timeout_ms))
        else:
            results.append(
                SMTObligationResult(
                    obligation_id,
                    status,
                    backend_name,
                    query.digest,
                    query.smt2,
                    reason=(
                        "z3-solver is not installed"
                        if status == SMTStatus.UNAVAILABLE
                        else "SMT backend was not requested"
                    ),
                )
            )
    return SMTReport(
        hir.digest,
        obligations.digest,
        backend_name,
        _z3_version(loaded_z3) if loaded_z3 is not None else None,
        timeout_ms,
        max_paths,
        tuple(sorted(results, key=lambda item: item.obligation_id)),
    )


__all__ = [
    "SMT_CONTRACT",
    "SMT_SCHEMA_VERSION",
    "SMTCounterexample",
    "SMTObligationResult",
    "SMTReport",
    "SMTStatus",
    "verify_smt",
]
