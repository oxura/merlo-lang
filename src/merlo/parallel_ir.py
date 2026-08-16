"""Target-neutral Parallel IR and conservative Performance MIR lowering.

The module deliberately keeps the IR small: values are typed by shape, operations
are an ordered immutable sequence, and the predecessor MIR digest binds the
result to the exact input that was lowered.
"""
from __future__ import annotations
from collections import deque

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from merlo.representation_mir import GeneralPerformanceMIR, GeneralMIRInstruction

PARALLEL_IR_SCHEMA_VERSION = 1
PARALLEL_IR_CONTRACT = "merlo.parallel-ir.v1"
_SCHEMA_KEYS = {"schema_version", "contract", "predecessor_digest", "operations", "digest"}
_OPERATION_KEYS = {
    "operation_id", "kind", "inputs", "result", "result_type", "dependencies",
    "ownership_mode", "effects", "attributes", "provenance", "function", "block",
}
_TYPE_KEYS = {"shape", "element_type", "lanes"}
_SCALAR_TYPES = (str, int, float, bool, type(None))
_KINDS = {"scalar", "map", "zip", "reduce", "scan", "filter", "gather", "scatter", "fused_map_zip"}
_VECTOR_KINDS = {"map", "zip", "reduce", "scan", "filter", "gather", "scatter"}
_FUSIBLE_KINDS = {"map", "zip"}
_OWNERSHIP = {"borrowed", "owned", "shared", "unique", "unique_owner", "moved", "unknown"}
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _error(code: str, detail: str = "") -> ValueError:
    return ValueError(code if not detail else f"{code}: {detail}")


def _canonical(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
            raise _error("ParallelIRInvalidValue", "non-finite float")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _error("ParallelIRInvalidValue", "attribute keys must be strings")
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    raise _error("ParallelIRInvalidValue", type(value).__name__)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _strict_keys(raw: Mapping[str, Any], expected: set[str], code: str) -> None:
    keys = set(raw)
    missing = expected - keys
    extra = keys - expected
    if missing:
        raise _error(code, f"missing {sorted(missing)}")
    if extra:
        raise _error(code, f"unknown {sorted(extra)}")


def _digest_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _loads_strict(text: str) -> dict[str, Any]:
    if not isinstance(text, str):
        raise _error("ParallelIRInvalidJSON", "expected string")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise _error("ParallelIRInvalidJSON", f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(_error("ParallelIRInvalidJSON", value)))
    except ValueError:
        raise
    except (TypeError, json.JSONDecodeError) as exc:
        raise _error("ParallelIRInvalidJSON", str(exc)) from exc
    if not isinstance(value, dict):
        raise _error("ParallelIRInvalidJSON", "root must be an object")
    return value


@dataclass(frozen=True)
class ParallelValueType:
    """Shape and element type of a value carried by a Parallel IR operation."""

    shape: str
    element_type: str
    lanes: int | None = None

    def __post_init__(self) -> None:
        if self.shape not in {"scalar", "vector"}:
            raise _error("ParallelIRInvalidType", self.shape)
        if not isinstance(self.element_type, str) or not self.element_type:
            raise _error("ParallelIRInvalidType", "element type required")
        if self.shape == "scalar" and self.lanes is not None:
            raise _error("ParallelIRInvalidType", "scalar lanes")
        if self.lanes is not None and (isinstance(self.lanes, bool) or not isinstance(self.lanes, int) or self.lanes <= 0):
            raise _error("ParallelIRInvalidType", "lanes must be positive")

    @classmethod
    def scalar(cls, element_type: str) -> "ParallelValueType":
        return cls("scalar", element_type)

    @classmethod
    def vector(cls, element_type: str, lanes: int | None = None) -> "ParallelValueType":
        return cls("vector", element_type, lanes)

    def to_dict(self) -> dict[str, Any]:
        return {"shape": self.shape, "element_type": self.element_type, "lanes": self.lanes}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ParallelValueType":
        if not isinstance(raw, Mapping):
            raise _error("ParallelIRInvalidType", "type must be object")
        _strict_keys(raw, _TYPE_KEYS, "ParallelIRInvalidType")
        shape, element_type, lanes = raw["shape"], raw["element_type"], raw["lanes"]
        if not isinstance(shape, str) or not isinstance(element_type, str):
            raise _error("ParallelIRInvalidType", "type fields")
        if lanes is not None and (isinstance(lanes, bool) or not isinstance(lanes, int)):
            raise _error("ParallelIRInvalidType", "lanes")
        return cls(shape, element_type, lanes)


@dataclass(frozen=True)
class ScalarValue:
    """A JSON scalar with an explicit language-level type name."""

    type_name: str
    value: str | int | float | bool | None

    def __post_init__(self) -> None:
        if not isinstance(self.type_name, str) or not self.type_name:
            raise _error("ParallelIRInvalidValue", "scalar type")
        if not isinstance(self.value, _SCALAR_TYPES):
            raise _error("ParallelIRInvalidValue", "scalar value")
        _canonical(self.value)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type_name, "value": self.value}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ScalarValue":
        if not isinstance(raw, Mapping):
            raise _error("ParallelIRInvalidValue", "scalar must be object")
        _strict_keys(raw, {"type", "value"}, "ParallelIRInvalidValue")
        return cls(raw["type"], raw["value"])


@dataclass(frozen=True)
class VectorValue:
    """A typed immutable vector of JSON scalar values."""

    element_type: str
    values: tuple[str | int | float | bool | None, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.element_type, str) or not self.element_type:
            raise _error("ParallelIRInvalidValue", "vector type")
        if not isinstance(self.values, tuple):
            object.__setattr__(self, "values", tuple(self.values))
        for value in self.values:
            if not isinstance(value, _SCALAR_TYPES):
                raise _error("ParallelIRInvalidValue", "vector element")
            _canonical(value)

    def to_dict(self) -> dict[str, Any]:
        return {"element_type": self.element_type, "values": list(self.values)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "VectorValue":
        if not isinstance(raw, Mapping):
            raise _error("ParallelIRInvalidValue", "vector must be object")
        _strict_keys(raw, {"element_type", "values"}, "ParallelIRInvalidValue")
        if not isinstance(raw["values"], list):
            raise _error("ParallelIRInvalidValue", "vector values")
        return cls(raw["element_type"], tuple(raw["values"]))


@dataclass(frozen=True)
class ParallelOperation:
    """One ordered operation and its explicit data/control dependencies."""

    operation_id: str
    kind: str
    inputs: tuple[str, ...] = ()
    result: str | None = None
    result_type: ParallelValueType | None = None
    dependencies: tuple[str, ...] = ()
    ownership_mode: str = "unknown"
    effects: tuple[str, ...] = ()
    attributes: tuple[tuple[str, Any], ...] = ()
    provenance: tuple[str, ...] = ()
    function: str = ""
    block: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, str) or not self.operation_id:
            raise _error("ParallelIRInvalidOperation", "operation id")
        if self.kind not in _KINDS:
            raise _error("ParallelIRInvalidOperation", f"kind {self.kind}")
        if not isinstance(self.inputs, tuple):
            object.__setattr__(self, "inputs", tuple(self.inputs))
        if not all(isinstance(item, str) and item for item in self.inputs):
            raise _error("ParallelIRInvalidOperation", "inputs")
        if self.result is not None and (not isinstance(self.result, str) or not self.result):
            raise _error("ParallelIRInvalidOperation", "result")
        if not isinstance(self.dependencies, tuple):
            object.__setattr__(self, "dependencies", tuple(self.dependencies))
        if len(set(self.dependencies)) != len(self.dependencies) or any(not isinstance(item, str) or not item for item in self.dependencies):
            raise _error("ParallelIRInvalidOperation", "dependencies")
        if not isinstance(self.ownership_mode, str) or self.ownership_mode not in _OWNERSHIP:
            raise _error("ParallelIRInvalidOperation", f"ownership {self.ownership_mode}")
        if not isinstance(self.effects, tuple):
            object.__setattr__(self, "effects", tuple(self.effects))
        if any(not isinstance(item, str) or not item for item in self.effects):
            raise _error("ParallelIRInvalidOperation", "effects")
        if len(set(self.effects)) != len(self.effects):
            raise _error("ParallelIRInvalidOperation", "duplicate effects")
        object.__setattr__(self, "effects", tuple(sorted(self.effects)))
        if not isinstance(self.attributes, tuple):
            object.__setattr__(self, "attributes", tuple(self.attributes.items()) if isinstance(self.attributes, Mapping) else tuple(self.attributes))
        attrs = []
        for key, value in self.attributes:
            if not isinstance(key, str) or not key:
                raise _error("ParallelIRInvalidOperation", "attribute key")
            attrs.append((key, _freeze(_canonical(value))))
        if len({key for key, _ in attrs}) != len(attrs):
            raise _error("ParallelIRInvalidOperation", "duplicate attributes")
        object.__setattr__(self, "attributes", tuple(sorted(attrs)))
        if (
            self.effects
            and self.kind in _VECTOR_KINDS
            and (
                dict(self.attributes).get(
                    "independent"
                )
                is True
                or dict(self.attributes).get(
                    "execution"
                )
                == "vector"
            )
        ):
            raise _error(
                "ParallelIREffectfulVectorization"
            )
        if not isinstance(self.provenance, tuple):
            object.__setattr__(self, "provenance", tuple(self.provenance))
        if any(not isinstance(item, str) or not item for item in self.provenance):
            raise _error("ParallelIRInvalidOperation", "provenance")
        if not isinstance(self.function, str) or not isinstance(self.block, str):
            raise _error("ParallelIRInvalidOperation", "location")

    @property
    def attribute_map(self) -> dict[str, Any]:
        return {key: _thaw(value) for key, value in self.attributes}

    @property
    def pure(self) -> bool:
        return not self.effects

    @property
    def vectorizable(self) -> bool:
        return self.kind in _VECTOR_KINDS and self.pure and self.attribute_map.get("independent", False) is True

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind,
            "inputs": list(self.inputs),
            "result": self.result,
            "result_type": self.result_type.to_dict() if self.result_type else None,
            "dependencies": list(self.dependencies),
            "ownership_mode": self.ownership_mode,
            "effects": list(self.effects),
            "attributes": {key: _thaw(value) for key, value in self.attributes},
            "provenance": list(self.provenance),
            "function": self.function,
            "block": self.block,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ParallelOperation":
        if not isinstance(raw, Mapping):
            raise _error("ParallelIRInvalidOperation", "operation must be object")
        _strict_keys(raw, _OPERATION_KEYS, "ParallelIRInvalidOperation")
        for name in ("inputs", "dependencies", "effects", "provenance"):
            if not isinstance(raw[name], list):
                raise _error("ParallelIRInvalidOperation", name)
        attrs = raw["attributes"]
        if not isinstance(attrs, Mapping):
            raise _error("ParallelIRInvalidOperation", "attributes")
        result_type = None if raw["result_type"] is None else ParallelValueType.from_dict(raw["result_type"])
        return cls(
            raw["operation_id"], raw["kind"], tuple(raw["inputs"]), raw["result"], result_type,
            tuple(raw["dependencies"]), raw["ownership_mode"], tuple(raw["effects"]),
            tuple((key, value) for key, value in attrs.items()), tuple(raw["provenance"]),
            raw["function"], raw["block"],
        )


@dataclass(frozen=True)
class ParallelIR:
    predecessor_digest: str
    operations: tuple[ParallelOperation, ...]
    schema_version: int = PARALLEL_IR_SCHEMA_VERSION
    contract: str = PARALLEL_IR_CONTRACT

    def __post_init__(self) -> None:
        if self.schema_version != PARALLEL_IR_SCHEMA_VERSION:
            raise _error("ParallelIRSchemaMismatch", str(self.schema_version))
        if self.contract != PARALLEL_IR_CONTRACT:
            raise _error("ParallelIRContractMismatch", self.contract)
        if not isinstance(self.predecessor_digest, str) or not _HEX_DIGEST.fullmatch(self.predecessor_digest):
            raise _error("ParallelIRInvalidPredecessorDigest")
        if not isinstance(self.operations, tuple):
            object.__setattr__(self, "operations", tuple(self.operations))
        ids = [item.operation_id for item in self.operations]
        if len(ids) != len(set(ids)):
            raise _error("ParallelIRDuplicateOperationId")
        available = set(ids)
        for operation in self.operations:
            missing = set(operation.dependencies) - available
            if missing:
                raise _error("ParallelIRUnknownDependency", f"{operation.operation_id}: {sorted(missing)}")
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        indegree = {item.operation_id: len(item.dependencies) for item in self.operations}
        children: dict[str, list[str]] = {item.operation_id: [] for item in self.operations}
        for operation in self.operations:
            for dependency in operation.dependencies:
                children[dependency].append(operation.operation_id)
        ready = deque(identifier for identifier, count in indegree.items() if count == 0)
        visited = 0
        while ready:
            identifier = ready.popleft()
            visited += 1
            for child in children[identifier]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if visited != len(self.operations):
            cyclic = next(identifier for identifier, count in indegree.items() if count > 0)
            raise _error("ParallelIRDependencyCycle", cyclic)

    @property
    def digest(self) -> str:
        return _digest_payload(self._payload_dict())

    def _payload_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "predecessor_digest": self.predecessor_digest,
            "operations": [item.to_dict() for item in self.operations],
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload_dict()
        return {**payload, "digest": self.digest}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ParallelIR":
        if not isinstance(raw, Mapping):
            raise _error("ParallelIRInvalidSchema", "root must be object")
        _strict_keys(raw, _SCHEMA_KEYS, "ParallelIRInvalidSchema")
        if raw["schema_version"] != PARALLEL_IR_SCHEMA_VERSION:
            raise _error("ParallelIRSchemaMismatch", str(raw["schema_version"]))
        if raw["contract"] != PARALLEL_IR_CONTRACT:
            raise _error("ParallelIRContractMismatch", str(raw["contract"]))
        if not isinstance(raw["operations"], list):
            raise _error("ParallelIRInvalidSchema", "operations")
        payload = {key: raw[key] for key in ("schema_version", "contract", "predecessor_digest", "operations")}
        if raw["digest"] != _digest_payload(payload):
            raise _error("ParallelIRDigestMismatch")
        operations = tuple(ParallelOperation.from_dict(item) for item in raw["operations"])
        result = cls(raw["predecessor_digest"], operations, raw["schema_version"], raw["contract"])
        if result.to_dict() != dict(raw):
            raise _error("ParallelIRNonCanonical")
        return result

    @classmethod
    def from_json(cls, text: str) -> "ParallelIR":
        return cls.from_dict(_loads_strict(text))

    def fuse(self) -> "ParallelIR":
        return fuse_parallel_ir(self)


def _stable_id(*parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    return "par_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _type_for_instruction(instruction: GeneralMIRInstruction) -> ParallelValueType | None:
    attrs = instruction.attribute_map
    shape = attrs.get("parallel_shape")
    if shape is None:
        shape = "vector" if attrs.get("vector", False) is True else "scalar"
    if shape not in {"scalar", "vector"}:
        shape = "scalar"
    type_name = instruction.type_name or str(attrs.get("element_type", "Unit"))
    lanes = attrs.get("lanes")
    if isinstance(lanes, bool) or not isinstance(lanes, (int, type(None))):
        lanes = None
    if shape == "scalar":
        lanes = None
    return ParallelValueType(shape, str(attrs.get("element_type", type_name)), lanes)


def _operation_kind(instruction: GeneralMIRInstruction) -> str | None:
    attrs = instruction.attribute_map
    candidate = attrs.get("parallel_operation", attrs.get("collection_operation"))
    if candidate is None and instruction.op in _VECTOR_KINDS:
        candidate = instruction.op
    return str(candidate) if candidate is not None else None


def _independence_proven(instruction: GeneralMIRInstruction, function_effects: tuple[str, ...]) -> bool:
    attrs = instruction.attribute_map
    if instruction.effects or function_effects:
        return False
    return attrs.get("independent") is True or attrs.get("parallel_safe") is True


def lower_performance_mir(mir: GeneralPerformanceMIR) -> ParallelIR:
    """Lower GeneralPerformanceMIR without guessing at unsupported parallelism."""
    if not isinstance(mir, GeneralPerformanceMIR):
        raise _error("ParallelIRInvalidPredecessor", "expected GeneralPerformanceMIR")
    predecessor = mir.digest
    operations: list[ParallelOperation] = []
    for function in mir.functions:
        producer_ids: dict[str, str] = {}
        for block in function.blocks:
            for instruction in block.instructions:
                candidate = _operation_kind(instruction)
                proven = candidate in _VECTOR_KINDS and _independence_proven(instruction, function.effects)
                kind = candidate if proven else "scalar"
                attrs = instruction.attribute_map
                attrs["original_operation"] = candidate or instruction.op
                attrs["independent"] = proven
                attrs["execution"] = "vector" if proven else "scalar"
                if not proven and candidate in _VECTOR_KINDS:
                    attrs["fallback_reason"] = "effectful_or_unproven_independence"
                dependencies = tuple(producer_ids[operand] for operand in instruction.operands if operand in producer_ids)
                operation_id = _stable_id(predecessor, function.name, block.id, instruction.id)
                operation = ParallelOperation(
                    operation_id,
                    kind,
                    instruction.operands,
                    instruction.result,
                    _type_for_instruction(instruction),
                    dependencies,
                    instruction.ownership_provenance if instruction.ownership_provenance in _OWNERSHIP else "unknown",
                    instruction.effects,
                    tuple(attrs.items()),
                    (instruction.id,),
                    function.name,
                    block.id,
                )
                operations.append(operation)
                if instruction.result is not None:
                    producer_ids[instruction.result] = operation_id
    return fuse_parallel_ir(ParallelIR(predecessor, tuple(operations)))


def fuse_parallel_ir(ir: ParallelIR) -> ParallelIR:
    """Fuse only adjacent pure map/zip dataflow stages with no later users."""
    operations = list(ir.operations)
    index = 0
    while index + 1 < len(operations):
        first, second = operations[index], operations[index + 1]
        chained = first.result is not None and (
            first.operation_id in second.dependencies or first.result in second.inputs
        )
        same_region = first.function == second.function and first.block == second.block
        if not (
            same_region and chained and first.kind in _FUSIBLE_KINDS and second.kind in _FUSIBLE_KINDS
            and first.pure and second.pure and first.attribute_map.get("independent") is True
            and second.attribute_map.get("independent") is True
        ):
            index += 1
            continue
        first_result = first.result
        used_later = any(
            first_result is not None and (first_result in op.inputs or first.operation_id in op.dependencies)
            for op in operations[index + 2:]
        )
        if used_later:
            index += 1
            continue
        inputs = tuple(first.inputs) + tuple(item for item in second.inputs if item != first_result)
        dependencies = tuple(dict.fromkeys(
            dependency for dependency in (*first.dependencies, *second.dependencies)
            if dependency not in {first.operation_id, second.operation_id}
        ))
        attrs = second.attribute_map
        attrs.update({
            "fused": True,
            "fused_operations": [first.kind, second.kind],
            "source_operation_ids": [first.operation_id, second.operation_id],
            "independent": True,
            "execution": "vector",
        })
        fused = ParallelOperation(
            _stable_id(ir.predecessor_digest, "fusion", first.operation_id, second.operation_id),
            "fused_map_zip", inputs, second.result, second.result_type, dependencies,
            second.ownership_mode, (), tuple(attrs.items()), first.provenance + second.provenance,
            second.function, second.block,
        )
        operations[index:index + 2] = [fused]
        index = max(0, index - 1)
    return ParallelIR(ir.predecessor_digest, tuple(operations), ir.schema_version, ir.contract)



__all__ = [
    "PARALLEL_IR_SCHEMA_VERSION",
    "PARALLEL_IR_CONTRACT",
    "ParallelValueType",
    "ScalarValue",
    "VectorValue",
    "ParallelOperation",
    "ParallelIR",
    "lower_performance_mir",
    "fuse_parallel_ir",
]
