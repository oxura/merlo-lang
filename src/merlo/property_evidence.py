from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from merlo.bounded_symbolic import BoundedSymbolicReport, SymbolicStatus
from merlo.obligation_ir import ObligationCategory, ObligationProgram, TypedObligation
from merlo.range_analysis import IntegerRange
from merlo.smt_backend import SMTReport, SMTStatus
from merlo.structured_hir_v2 import SourceSpan, StructuredHIRProgram

PROPERTY_EVIDENCE_SCHEMA_VERSION = 1
PROPERTY_EVIDENCE_CONTRACT = "merlo.property-evidence.v1"
_Value = int | bool


def _source_from_dict(value: Mapping[str, Any]) -> SourceSpan:
    return SourceSpan(
        path=str(value["path"]), line=int(value["line"]), column=int(value["column"]),
        end_line=int(value["end_line"]), end_column=int(value["end_column"]),
    )


def _input_key(inputs: tuple[tuple[str, _Value], ...]) -> tuple[Any, ...]:
    return tuple((name, int(value) if isinstance(value, bool) else value) for name, value in inputs)


def _check_inputs(inputs: tuple[tuple[str, _Value], ...], error: str) -> None:
    names = tuple(name for name, _value in inputs)
    if names != tuple(sorted(names)):
        raise ValueError(error)
    if len(names) != len(set(names)):
        raise ValueError("DuplicateInputName")
    for name, value in inputs:
        if not isinstance(name, str) or not name:
            raise ValueError("InvalidInputName")
        if not isinstance(value, (bool, int)):
            raise ValueError("InvalidTypedInput")


def _typed_inputs(value: Any, error: str) -> tuple[tuple[str, _Value], ...]:
    if not isinstance(value, Mapping):
        raise ValueError(error)
    return tuple((str(name), item) for name, item in value.items())


@dataclass(frozen=True)
class GeneratedParameterDomain:
    name: str
    type_name: str
    values: tuple[_Value, ...]
    exhaustive: bool

    def __post_init__(self) -> None:
        if not self.name or not self.type_name:
            raise ValueError("InvalidParameterDomain")
        if self.values != tuple(sorted(self.values, key=lambda item: (int(item), isinstance(item, bool)))):
            raise ValueError("ParameterDomainValuesNotCanonical")
        if len(self.values) != len(set(self.values)):
            raise ValueError("DuplicateParameterDomainValue")
        if any(not isinstance(item, (bool, int)) for item in self.values):
            raise ValueError("InvalidParameterDomainValue")
        if self.type_name == "Bool":
            if any(not isinstance(item, bool) for item in self.values):
                raise ValueError("BoolDomainHasInteger")
            if self.exhaustive and self.values != (False, True):
                raise ValueError("IncompleteExhaustiveBoolDomain")
            return
        value_range = IntegerRange.for_type(self.type_name)
        if value_range is None:
            if self.values or self.exhaustive:
                raise ValueError(f"UnsupportedParameterType: {self.type_name}")
            return
        if any(isinstance(item, bool) or not value_range.lower <= item <= value_range.upper for item in self.values):
            raise ValueError("ParameterDomainValueOutOfRange")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type_name, "values": list(self.values), "exhaustive": self.exhaustive}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GeneratedParameterDomain":
        return cls(str(value["name"]), str(value["type"]), tuple(value["values"]), bool(value["exhaustive"]))


@dataclass(frozen=True)
class GeneratedCase:
    inputs: tuple[tuple[str, _Value], ...]

    def __post_init__(self) -> None:
        _check_inputs(self.inputs, "GeneratedCaseInputsNotCanonical")

    def to_dict(self) -> dict[str, Any]:
        return {"inputs": dict(self.inputs)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GeneratedCase":
        return cls(_typed_inputs(value["inputs"], "InvalidGeneratedCaseInputs"))


@dataclass(frozen=True)
class GeneratedProperty:
    obligation_id: str
    owner_symbol_id: str
    owner_revision_id: str
    function_name: str
    predicate: str
    source: SourceSpan
    requirements: tuple[str, ...]
    parameters: tuple[GeneratedParameterDomain, ...]
    cases: tuple[GeneratedCase, ...]
    exhaustive: bool
    case_cap: int
    def __post_init__(self) -> None:
        if not self.obligation_id or not self.owner_symbol_id:
            raise ValueError("InvalidGeneratedPropertyIdentity")
        if len(self.requirements) != len(set(self.requirements)):
            raise ValueError("DuplicateRequirement")
        if self.requirements != tuple(sorted(self.requirements)):
            raise ValueError("RequirementsNotCanonical")
        names = tuple(item.name for item in self.parameters)
        if names != tuple(sorted(names)):
            raise ValueError("ParameterDomainsNotCanonical")
        if len(names) != len(set(names)):
            raise ValueError("DuplicateParameterDomain")
        if self.case_cap < 1 or len(self.cases) > self.case_cap:
            raise ValueError("InvalidGeneratedPropertyCaseCap")
        expected_names = set(names)
        domain_map = {item.name: item for item in self.parameters}
        case_keys: list[tuple[Any, ...]] = []
        for case in self.cases:
            case_names = {name for name, _value in case.inputs}
            if case_names != expected_names:
                raise ValueError("GeneratedCaseInputNamesMismatch")
            for name, value in case.inputs:
                domain = domain_map[name]
                if value not in domain.values:
                    raise ValueError("GeneratedCaseValueOutsideDomain")
            case_keys.append(_input_key(case.inputs))
        keys = tuple(case_keys)
        if keys != tuple(sorted(keys)):
            raise ValueError("GeneratedCasesNotCanonical")
        if len(keys) != len(set(keys)):
            raise ValueError("DuplicateGeneratedCase")
        if self.exhaustive:
            if not all(item.exhaustive for item in self.parameters):
                raise ValueError("SampledDomainClaimedExhaustive")
            expected = tuple(_input_key(tuple(zip(names, values))) for values in itertools.product(*(domain_map[name].values for name in names)))
            if keys != expected:
                raise ValueError("IncompleteExhaustiveCases")

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id, "owner_symbol_id": self.owner_symbol_id,
            "owner_revision_id": self.owner_revision_id, "function": self.function_name,
            "predicate": self.predicate, "source": self.source.to_dict(),
            "requirements": list(self.requirements), "parameters": [item.to_dict() for item in self.parameters],
            "cases": [item.to_dict() for item in self.cases], "exhaustive": self.exhaustive,
            "case_cap": self.case_cap,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GeneratedProperty":
        return cls(
            obligation_id=str(value["obligation_id"]), owner_symbol_id=str(value["owner_symbol_id"]),
            owner_revision_id=str(value["owner_revision_id"]), function_name=str(value["function"]),
            predicate=str(value["predicate"]), source=_source_from_dict(value["source"]),
            requirements=tuple(str(item) for item in value.get("requirements", ())),
            parameters=tuple(GeneratedParameterDomain.from_dict(item) for item in value["parameters"]),
            cases=tuple(GeneratedCase.from_dict(item) for item in value["cases"]),
            exhaustive=bool(value["exhaustive"]), case_cap=int(value["case_cap"]),
        )


@dataclass(frozen=True)
class NormalizedCounterexample:
    obligation_id: str
    owner_symbol_id: str
    owner_revision_id: str
    predicate: str
    engine: str
    inputs: tuple[tuple[str, _Value], ...]
    input_types: tuple[tuple[str, str], ...]
    result: _Value | None = None
    backend: str | None = None

    def __post_init__(self) -> None:
        if not self.obligation_id or not self.owner_symbol_id or not self.engine:
            raise ValueError("InvalidCounterexampleIdentity")
        _check_inputs(self.inputs, "CounterexampleInputsNotCanonical")
        type_names = tuple(name for name, _type in self.input_types)
        if type_names != tuple(sorted(type_names)) or len(type_names) != len(set(type_names)):
            raise ValueError("CounterexampleInputTypesNotCanonical")
        if type_names != tuple(name for name, _value in self.inputs):
            raise ValueError("CounterexampleInputTypesMismatch")
        if any(not isinstance(name, str) or not name or not isinstance(type_name, str) or not type_name for name, type_name in self.input_types):
            raise ValueError("InvalidCounterexampleInputType")
        if self.result is not None and not isinstance(self.result, (bool, int)):
            raise ValueError("InvalidCounterexampleResult")

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id, "owner_symbol_id": self.owner_symbol_id,
            "owner_revision_id": self.owner_revision_id, "predicate": self.predicate,
            "engine": self.engine, "backend": self.backend, "inputs": dict(self.inputs),
            "input_types": dict(self.input_types), "result": self.result,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NormalizedCounterexample":
        return cls(
            obligation_id=str(value["obligation_id"]), owner_symbol_id=str(value["owner_symbol_id"]),
            owner_revision_id=str(value["owner_revision_id"]), predicate=str(value["predicate"]),
            engine=str(value["engine"]), inputs=_typed_inputs(value["inputs"], "InvalidCounterexampleInputs"),
            input_types=tuple((str(name), str(item)) for name, item in _typed_inputs(value["input_types"], "InvalidCounterexampleInputTypes")),
            result=value.get("result"), backend=None if value.get("backend") is None else str(value["backend"]),
        )


@dataclass(frozen=True)
class PropertyEvidenceReport:
    hir_digest: str
    obligation_digest: str
    parameter_bounds: tuple[tuple[str, int], ...]
    case_cap: int
    properties: tuple[GeneratedProperty, ...]
    counterexamples: tuple[NormalizedCounterexample, ...]
    schema_version: int = PROPERTY_EVIDENCE_SCHEMA_VERSION
    contract: str = PROPERTY_EVIDENCE_CONTRACT

    def __post_init__(self) -> None:
        if self.schema_version != PROPERTY_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("PropertyEvidenceSchemaVersionMismatch")
        if self.contract != PROPERTY_EVIDENCE_CONTRACT:
            raise ValueError("PropertyEvidenceContractMismatch")
        names = tuple(name for name, _bound in self.parameter_bounds)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("ParameterBoundsNotCanonical")
        if any(not name or not isinstance(bound, int) or isinstance(bound, bool) or bound < 1 for name, bound in self.parameter_bounds):
            raise ValueError("InvalidParameterBound")
        if self.case_cap < 1:
            raise ValueError("InvalidCaseCap")
        ids = tuple(item.obligation_id for item in self.properties)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ValueError("PropertiesNotCanonical")
        cex_keys = tuple((item.obligation_id, item.engine, _input_key(item.inputs)) for item in self.counterexamples)
        if cex_keys != tuple(sorted(cex_keys)) or len(cex_keys) != len(set(cex_keys)):
            raise ValueError("CounterexamplesNotCanonical")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "contract": self.contract,
            "hir_digest": self.hir_digest, "obligation_digest": self.obligation_digest,
            "parameter_bounds": {name: bound for name, bound in self.parameter_bounds}, "case_cap": self.case_cap,
            "properties": [item.to_dict() for item in self.properties],
            "counterexamples": [item.to_dict() for item in self.counterexamples],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PropertyEvidenceReport":
        bounds = value["parameter_bounds"]
        if not isinstance(bounds, Mapping):
            raise ValueError("InvalidParameterBounds")
        return cls(
            hir_digest=str(value["hir_digest"]), obligation_digest=str(value["obligation_digest"]),
            parameter_bounds=tuple((str(name), int(bound)) for name, bound in bounds.items()), case_cap=int(value["case_cap"]),
            properties=tuple(GeneratedProperty.from_dict(item) for item in value["properties"]),
            counterexamples=tuple(NormalizedCounterexample.from_dict(item) for item in value["counterexamples"]),
            schema_version=int(value["schema_version"]),
            contract=str(value["contract"]),
        )

    @classmethod
    def from_json(cls, payload: str) -> "PropertyEvidenceReport":
        value = json.loads(payload)
        if not isinstance(value, Mapping):
            raise ValueError("InvalidPropertyEvidenceJSON")
        return cls.from_dict(value)


def _resolve_bounds(parameter_bounds: int | Mapping[str, int] | None, case_cap: int | None) -> tuple[dict[str, int], int]:
    raw = 512 if parameter_bounds is None else parameter_bounds
    if isinstance(raw, bool):
        raise ValueError("InvalidParameterBound")
    if isinstance(raw, int):
        bounds = {"*": raw}
    elif isinstance(raw, Mapping):
        bounds = dict(raw)
    else:
        raise ValueError("InvalidParameterBound")
    cap = 4096 if case_cap is None else case_cap
    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
        raise ValueError("InvalidCaseCap")
    return bounds, cap


def _parameter_cap(bounds: Mapping[str, int], name: str) -> int:
    return int(bounds.get(name, bounds.get("*", 512)))


def _domain(parameter: Any, bounds: Mapping[str, int]) -> GeneratedParameterDomain:
    type_name = parameter.type_name
    cap = _parameter_cap(bounds, parameter.name)
    if type_name == "Bool":
        values = (False, True)[:cap]
        return GeneratedParameterDomain(parameter.name, type_name, values, cap >= 2)
    value_range = IntegerRange.for_type(type_name)
    if value_range is None:
        return GeneratedParameterDomain(parameter.name, type_name, (), False)
    width = value_range.upper - value_range.lower + 1
    if width <= cap:
        return GeneratedParameterDomain(parameter.name, type_name, tuple(range(value_range.lower, value_range.upper + 1)), True)
    if cap == 1:
        values = (value_range.lower,)
    elif cap == 2:
        values = (value_range.lower, value_range.upper)
    else:
        values = tuple(sorted({value_range.lower, value_range.upper, value_range.lower + (value_range.upper - value_range.lower) // 2}))[:cap]
    return GeneratedParameterDomain(parameter.name, type_name, values, False)


def _cases(domains: tuple[GeneratedParameterDomain, ...], case_cap: int) -> tuple[GeneratedCase, ...]:
    if not domains:
        return (GeneratedCase(()),)
    names = tuple(domain.name for domain in domains)
    result: list[GeneratedCase] = []
    for values in itertools.product(*(domain.values for domain in domains)):
        result.append(GeneratedCase(tuple(zip(names, values))))
        if len(result) == case_cap:
            break
    return tuple(result)


def _is_refuted(status: Any, enum: type[Enum]) -> bool:
    return status == enum.REFUTED or status == enum.REFUTED.value


def _postconditions(obligations: ObligationProgram) -> dict[str, TypedObligation]:
    return {item.obligation_id: item for item in obligations.obligations if item.category == ObligationCategory.FUNCTION_POSTCONDITION}


def _counterexample(obligation: TypedObligation, input_types: tuple[tuple[str, str], ...], *, engine: str, inputs: tuple[tuple[str, _Value], ...], result: _Value | None, backend: str | None = None, predicate: str | None = None) -> NormalizedCounterexample:
    if predicate is not None and predicate != obligation.predicate:
        raise ValueError("CounterexamplePredicateMismatch")
    return NormalizedCounterexample(obligation.obligation_id, obligation.owner_symbol_id, obligation.owner_revision_id, obligation.predicate, engine, inputs, input_types, result, backend)


def generate_property_evidence(
    hir: StructuredHIRProgram,
    obligations: ObligationProgram,
    bounded_report: BoundedSymbolicReport | None,
    smt_report: SMTReport | None,
    *,
    parameter_bounds: int | Mapping[str, int] | None = None,
    case_cap: int | None = None,
) -> PropertyEvidenceReport:
    if obligations.hir_digest != hir.digest:
        raise ValueError("ObligationHIRDigestMismatch")
    for report in (bounded_report, smt_report):
        if report is not None and (report.hir_digest != hir.digest or report.obligation_digest != obligations.digest):
            raise ValueError("EvidenceDigestMismatch")
    bounds, resolved_case_cap = _resolve_bounds(parameter_bounds, case_cap)
    post = _postconditions(obligations)
    functions = {function.symbol_id: function for function in hir.functions}
    known_names = {parameter.name for function in functions.values() for parameter in function.parameters}
    if any(name != "*" and name not in known_names for name in bounds):
        raise ValueError("UnknownParameterBound")
    properties: list[GeneratedProperty] = []
    for obligation in post.values():
        function = functions.get(obligation.owner_symbol_id)
        if function is None or function.revision_id != obligation.owner_revision_id:
            raise ValueError("PostconditionOwnerMismatch")
        domains = tuple(sorted((_domain(parameter, bounds) for parameter in function.parameters), key=lambda item: item.name))
        total = 1
        for domain in domains:
            total *= len(domain.values)
        properties.append(GeneratedProperty(
            obligation.obligation_id, obligation.owner_symbol_id, obligation.owner_revision_id, function.name,
            obligation.predicate, obligation.source, tuple(sorted(item.expression for item in function.requirements)), domains,
            _cases(domains, resolved_case_cap), all(item.exhaustive for item in domains) and total <= resolved_case_cap, resolved_case_cap,
        ))
    properties.sort(key=lambda item: item.obligation_id)
    counterexamples: list[NormalizedCounterexample] = []
    for report, engine in ((bounded_report, "bounded"), (smt_report, "smt")):
        if report is None:
            continue
        for result in report.results:
            obligation = post.get(result.obligation_id)
            if obligation is None or result.counterexample is None:
                continue
            status = result.status
            if not (_is_refuted(status, SymbolicStatus) if engine == "bounded" else _is_refuted(status, SMTStatus)):
                continue
            function = functions[obligation.owner_symbol_id]
            types = tuple(sorted((parameter.name, parameter.type_name) for parameter in function.parameters))
            if engine == "bounded":
                counterexamples.append(_counterexample(obligation, types, engine=engine, inputs=result.counterexample.inputs, result=result.counterexample.result, predicate=result.counterexample.predicate))
            else:
                counterexamples.append(_counterexample(obligation, types, engine=engine, inputs=result.counterexample.inputs, result=None, backend=result.backend))
    counterexamples.sort(key=lambda item: (item.obligation_id, item.engine, _input_key(item.inputs)))
    return PropertyEvidenceReport(hir.digest, obligations.digest, tuple(sorted(bounds.items())), resolved_case_cap, tuple(properties), tuple(counterexamples))


__all__ = [
    "PROPERTY_EVIDENCE_SCHEMA_VERSION", "PROPERTY_EVIDENCE_CONTRACT", "GeneratedParameterDomain",
    "GeneratedCase", "GeneratedProperty", "NormalizedCounterexample", "PropertyEvidenceReport",
    "generate_property_evidence",
]
