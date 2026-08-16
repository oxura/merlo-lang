from __future__ import annotations

import re
from typing import Any, Mapping

from merlo.refactor import preview_fill_hole
from merlo.semantic_world import SemanticWorld, StaleWorldError, WorldError
from merlo.synthesis import (
    CandidateRank,
    SynthesisCandidate,
    SynthesisRequest,
    build_synthesis_candidate,
)


_MAX_CANDIDATES = 256
_DEFAULT_MAX_CANDIDATES = _MAX_CANDIDATES
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _request(value: SynthesisRequest | Mapping[str, Any]) -> SynthesisRequest:
    if isinstance(value, SynthesisRequest):
        return value
    if isinstance(value, Mapping):
        return SynthesisRequest.from_dict(value)
    raise WorldError("SynthesisRequestSchemaMismatch")


def _strings(value: Any, error: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(type(item) is not str or not item for item in value):
        raise WorldError(error)
    result = tuple(value)
    if len(result) != len(set(result)):
        raise WorldError(error)
    return result


def _source(source: Any) -> None:
    if not isinstance(source, Mapping) or set(source) != {"path", "line", "column", "end_line", "end_column"}:
        raise WorldError("EnumerativeMalformedHoleSource")
    if type(source["path"]) is not str or not source["path"]:
        raise WorldError("EnumerativeMalformedHoleSource")
    for key in ("line", "column", "end_line", "end_column"):
        if type(source[key]) is not int or source[key] < 0:
            raise WorldError("EnumerativeMalformedHoleSource")
    if source["line"] != source["end_line"] or source["column"] >= source["end_column"]:
        raise WorldError("EnumerativeMalformedHoleSource")


def _context(value: Any) -> tuple[tuple[str, str, str], ...]:
    if not isinstance(value, (list, tuple)):
        raise WorldError("EnumerativeMalformedHoleContext")
    result: list[tuple[str, str, str]] = []
    names: set[str] = set()
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            raise WorldError("EnumerativeMalformedHoleContext")
        name, type_name, ownership = item
        if (
            type(name) is not str
            or not _IDENTIFIER.fullmatch(name)
            or type(type_name) is not str
            or not type_name
            or type(ownership) is not str
            or not ownership
            or name in names
        ):
            raise WorldError("EnumerativeMalformedHoleContext")
        names.add(name)
        result.append((name, type_name, ownership))
    return tuple(result)


def _callables(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise WorldError("EnumerativeMalformedHoleCallables")
    result: list[Mapping[str, Any]] = []
    names: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"name", "parameters", "return_type", "effects", "capabilities"}:
            raise WorldError("EnumerativeMalformedHoleCallables")
        name = item["name"]
        if type(name) is not str or not name or name in names:
            raise WorldError("EnumerativeMalformedHoleCallables")
        parameters = item["parameters"]
        if not isinstance(parameters, (list, tuple)):
            raise WorldError("EnumerativeMalformedHoleCallables")
        parameter_names: set[str] = set()
        for parameter in parameters:
            if not isinstance(parameter, (list, tuple)) or len(parameter) != 2:
                raise WorldError("EnumerativeMalformedHoleCallables")
            parameter_name, parameter_type = parameter
            if (
                type(parameter_name) is not str
                or not _IDENTIFIER.fullmatch(parameter_name)
                or parameter_name in parameter_names
                or type(parameter_type) is not str
                or not parameter_type
            ):
                raise WorldError("EnumerativeMalformedHoleCallables")
            parameter_names.add(parameter_name)
        if type(item["return_type"]) is not str or not item["return_type"]:
            raise WorldError("EnumerativeMalformedHoleCallables")
        _strings(item["effects"], "EnumerativeMalformedHoleCallables")
        _strings(item["capabilities"], "EnumerativeMalformedHoleCallables")
        names.add(name)
        result.append(item)
    return tuple(result)


def _hole_payloads(symbol: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    holes = symbol.get("holes")
    if not isinstance(holes, (list, tuple)):
        raise WorldError("EnumerativeMalformedHoles")
    result: list[Mapping[str, Any]] = []
    ids: set[str] = set()
    required = {"hole_id", "expected_type", "source", "node_id", "context", "callables", "effects", "capabilities"}
    for hole in holes:
        if not isinstance(hole, Mapping) or set(hole) != required:
            raise WorldError("EnumerativeMalformedHole")
        hole_id = hole["hole_id"]
        if type(hole_id) is not str or not hole_id or hole_id in ids:
            raise WorldError("EnumerativeMalformedHole")
        if type(hole["expected_type"]) is not str or not hole["expected_type"]:
            raise WorldError("EnumerativeMalformedHole")
        if type(hole["node_id"]) is not str or not hole["node_id"]:
            raise WorldError("EnumerativeMalformedHole")
        _source(hole["source"])
        _context(hole["context"])
        _callables(hole["callables"])
        _strings(hole["effects"], "EnumerativeMalformedHoleEffects")
        _strings(hole["capabilities"], "EnumerativeMalformedHoleCapabilities")
        ids.add(hole_id)
        result.append(hole)
    return tuple(result)


def _validate_request(request: SynthesisRequest) -> tuple[str, int]:
    if request.operation != "fill_hole":
        raise WorldError("EnumerativeOperationMismatch")
    arguments = request.arguments
    if not isinstance(arguments, Mapping) or set(arguments) not in ({"hole_id"}, {"hole_id", "max_candidates"}):
        raise WorldError("EnumerativeInvalidArguments")
    hole_id = arguments.get("hole_id")
    if type(hole_id) is not str or not hole_id:
        raise WorldError("EnumerativeInvalidHoleId")
    max_candidates = arguments.get("max_candidates", _DEFAULT_MAX_CANDIDATES)
    if type(max_candidates) is not int or not 1 <= max_candidates <= _MAX_CANDIDATES:
        raise WorldError("EnumerativeInvalidMaxCandidates")
    return hole_id, max_candidates


def _domains(expected_type: str) -> tuple[tuple[str, str, int], ...]:
    if expected_type == "Bool":
        return (("false", "literal", 1), ("true", "literal", 1))
    if expected_type in {"Byte", "UInt", "UInt8", "UInt16", "UInt32", "UInt64"}:
        return (("0", "literal", 1), ("1", "literal", 1))
    if expected_type in {"Int", "Int8", "Int16", "Int32", "Int64"}:
        return (("-1", "literal", 1), ("0", "literal", 1), ("1", "literal", 1))
    if expected_type in {"Float32", "Float64"}:
        return (("0.0", "literal", 1), ("1.0", "literal", 1))
    if expected_type == "Text":
        return (("\"\"", "literal", 1),)
    if expected_type == "Unit":
        return (("Unit", "literal", 1),)
    return ()


def enumerate_candidates(
    world: SemanticWorld,
    request: SynthesisRequest | Mapping[str, Any],
) -> tuple[SynthesisCandidate, ...]:
    """Enumerate bounded, exact, proposed fills without applying source edits."""
    if not isinstance(world, SemanticWorld):
        raise WorldError("SynthesisWorldMismatch")
    active = _request(request)
    world.require_fresh()
    if active.world_digest != world.digest:
        raise StaleWorldError("StaleWorld: synthesis request belongs to another world")
    hole_id, max_candidates = _validate_request(active)
    symbol = world.resolve(active.target)
    holes = _hole_payloads(symbol)
    matching = tuple(item for item in holes if item["hole_id"] == hole_id)
    if len(matching) != 1:
        raise WorldError("EnumerativeHoleNotOwned")
    hole = matching[0]
    expected_type = hole["expected_type"]

    expressions: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    for name, type_name, _ownership in _context(hole["context"]):
        if type_name == expected_type and name not in seen:
            expressions.append((name, "context", 0))
            seen.add(name)
    for expression, category, cost in _domains(expected_type):
        if expression not in seen:
            expressions.append((expression, category, cost))
            seen.add(expression)
    target_names = {active.target, symbol.get("name"), symbol.get("qualified_name")}
    for callable_item in _callables(hole["callables"]):
        if callable_item["parameters"] or callable_item["return_type"] != expected_type:
            continue
        if callable_item["effects"] or callable_item["capabilities"]:
            continue
        if callable_item["name"] in target_names:
            continue
        expression = f"{callable_item['name']}()"
        if expression not in seen:
            expressions.append((expression, "callable", 1))
            seen.add(expression)

    expressions.sort(key=lambda item: ({"context": 0, "literal": 1, "callable": 2}[item[1]], item[2], item[0]))
    candidates: list[SynthesisCandidate] = []
    for expression, category, cost in expressions[:max_candidates]:
        change = preview_fill_hole(world, active.target, hole_id, expression)
        candidates.append(
            build_synthesis_candidate(
                world,
                active,
                change,
                producer="enumerative",
                producer_revision="v1",
                rank=CandidateRank({"context": 0, "literal": 1, "callable": 2}[category], cost, expression),
                provenance={
                    "algorithm": "bounded_typed_enumeration",
                    "expression": expression,
                    "category": category,
                    "max_candidates": max_candidates,
                },
            )
        )
    return tuple(candidates)


__all__ = ["enumerate_candidates"]
