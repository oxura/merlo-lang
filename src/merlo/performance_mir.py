"""Typed Performance MIR for the Stage 0.5P native kernel."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


PERFORMANCE_MIR_SCHEMA_VERSION = 1
NATIVE_SUBSET_TYPES = (
    "Int64",
    "UInt64",
    "Float32",
    "Float64",
    "Bool",
    "records",
    "arrays",
    "slices",
)
NATIVE_SUBSET_FORMS = (
    "fn",
    "let",
    "var",
    "if",
    "match",
    "for",
    "while",
    "direct_function_calls",
)
STAGE05P_NON_GOALS = (
    "flow",
    "machine",
    "async_runtime",
    "package_manager",
    "ui",
    "gpu",
    "mobile",
    "llvm_backend",
    "large_standard_library",
)


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    raise TypeError(f"non-canonical MIR value: {type(value).__name__}")


def _pairs(value: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> tuple[tuple[str, Any], ...]:
    items = value.items() if isinstance(value, Mapping) else value
    return tuple(sorted((str(key), _canonical(item)) for key, item in items))


@dataclass(frozen=True)
class SourceMapping:
    path: str
    line: int
    column: int = 0
    end_line: int | None = None
    end_column: int | None = None

    def __post_init__(self) -> None:
        if not self.path or self.line < 1 or self.column < 0:
            raise ValueError("invalid source mapping")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line or self.line,
            "end_column": self.end_column if self.end_column is not None else self.column,
        }


@dataclass(frozen=True)
class PerformanceType:
    kind: str
    bits: int | None = None
    element: "PerformanceType | None" = None
    length: int | None = None
    record: str | None = None
    shared: bool = False

    def __post_init__(self) -> None:
        allowed = {"int", "uint", "float", "bool", "record", "array", "slice", "unit"}
        if self.kind not in allowed:
            raise ValueError(f"unsupported Performance MIR type kind: {self.kind}")
        if self.kind in {"int", "uint", "float"} and self.bits not in {32, 64}:
            raise ValueError(f"invalid scalar width: {self.bits}")
        if self.kind == "bool" and self.bits not in {None, 8}:
            raise ValueError("Bool layout is one byte")
        if self.kind in {"array", "slice"} and self.element is None:
            raise ValueError(f"{self.kind} requires an element type")
        if self.kind == "array" and (self.length is None or self.length < 0):
            raise ValueError("array requires a non-negative fixed length")
        if self.kind == "record" and not self.record:
            raise ValueError("record type requires a record name")

    @property
    def name(self) -> str:
        if self.kind == "int":
            return f"Int{self.bits}"
        if self.kind == "uint":
            return f"UInt{self.bits}"
        if self.kind == "float":
            return f"Float{self.bits}"
        if self.kind == "bool":
            return "Bool"
        if self.kind == "unit":
            return "Unit"
        if self.kind == "record":
            return self.record or "<record>"
        if self.kind == "array":
            return f"Array[{self.element.name};{self.length}]"
        return f"Slice[{self.element.name}]"

    @property
    def unique(self) -> bool:
        return (
            self.kind in {"array", "record"}
            and not self.shared
            and self.record
            not in {"BytesView", "TextView", "Utf8Decode"}
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "bits": self.bits,
            "element": self.element.to_dict() if self.element else None,
            "length": self.length,
            "record": self.record,
            "shared": self.shared,
            "unique": self.unique,
        }


INT64 = PerformanceType("int", bits=64)
UINT64 = PerformanceType("uint", bits=64)
FLOAT32 = PerformanceType("float", bits=32)
FLOAT64 = PerformanceType("float", bits=64)
BOOL = PerformanceType("bool", bits=8)
UNIT = PerformanceType("unit")
BYTES = PerformanceType("record", record="Bytes")
BYTES_VIEW = PerformanceType("record", record="BytesView")
BYTES_BUILDER = PerformanceType("record", record="BytesBuilder")
TEXT_BUILDER = PerformanceType("record", record="TextBuilder")
TEXT = PerformanceType("record", record="Text")
TEXT_VIEW = PerformanceType("record", record="TextView")
UTF8_DECODE = PerformanceType("record", record="Utf8Decode")


@dataclass(frozen=True)
class TypeLayout:
    type_name: str
    size: int
    alignment: int
    field_offsets: tuple[tuple[str, int], ...] = ()
    representation: str = "value"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_name,
            "size": self.size,
            "alignment": self.alignment,
            "field_offsets": dict(self.field_offsets),
            "representation": self.representation,
        }


@dataclass(frozen=True)
class MIRRecord:
    name: str
    fields: tuple[tuple[str, PerformanceType], ...]
    layout: TypeLayout
    source: SourceMapping

    def __post_init__(self) -> None:
        if not self.name or not self.fields:
            raise ValueError("record requires a name and fields")
        if len({name for name, _type in self.fields}) != len(self.fields):
            raise ValueError(f"duplicate field in record {self.name}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "fields": [
                {"name": name, "type": type_.to_dict()} for name, type_ in self.fields
            ],
            "layout": self.layout.to_dict(),
            "source": self.source.to_dict(),
        }


@dataclass(frozen=True)
class MIRParameter:
    name: str
    value: str
    type: PerformanceType
    ownership: str = "borrowed"

    def __post_init__(self) -> None:
        if self.ownership not in {"borrowed", "moved", "shared"}:
            raise ValueError(f"invalid parameter ownership: {self.ownership}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "type": self.type.to_dict(),
            "ownership": self.ownership,
        }


@dataclass(frozen=True)
class MIRInstruction:
    id: str
    op: str
    result: str | None = None
    type: PerformanceType | None = None
    operands: tuple[str, ...] = ()
    attributes: tuple[tuple[str, Any], ...] = ()
    source: SourceMapping | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.op:
            raise ValueError("MIR instruction id and op are required")
        object.__setattr__(self, "attributes", _pairs(self.attributes))
        if self.result is not None and self.type is None:
            raise ValueError(f"instruction {self.id} has a result without a type")

    @property
    def attribute_map(self) -> dict[str, Any]:
        return dict(self.attributes)

    @property
    def has_side_effect(self) -> bool:
        return self.op in {
            "alloc_heap",
            "bytes_new",
            "bytes_store",
            "bytes_bounds_check",
            "bytes_slice",
            "builder_create",
            "builder_reserve",
            "builder_grow",
            "builder_push",
            "builder_extend",
            "builder_view",
            "builder_finish_transfer",
            "builder_drop",
            "text_builder_create",
            "text_builder_append_account",
            "text_builder_scalar_width",
            "text_builder_push_ascii",
            "text_builder_push_scalar",
            "text_builder_extend",
            "text_builder_view",
            "text_builder_finish_transfer",
            "text_builder_drop",
            "utf8_validate",
            "bytes_to_text_transfer",
            "text_to_bytes_transfer",
            "text_view",
            "text_from_ascii",
            "text_from_scalar",
            "text_from_surrogate",
            "text_view_as_bytes",
            "text_slice",
            "utf8_boundary_check",
            "utf8_scalar_next",
            "utf8_decode_is_valid",
            "utf8_decode_take_text",
            "utf8_decode_error_offset",
            "utf8_decode_drop",
            "text_drop",
            "allocation",
            "payload_copy",
            "free",
            "store_local",
            "store_index",
            "call",
            "borrow_argument",
            "reborrow_argument",
            "borrow_return_transfer",
            "caller_borrow_continue",
            "reborrow_end",
            "borrow_end",
            "drop",
            "bounds_check",
            "panic",
        }

    def replace(self, **changes: Any) -> "MIRInstruction":
        values = {
            "id": self.id,
            "op": self.op,
            "result": self.result,
            "type": self.type,
            "operands": self.operands,
            "attributes": self.attributes,
            "source": self.source,
        }
        values.update(changes)
        return MIRInstruction(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "op": self.op,
            "result": self.result,
            "type": self.type.to_dict() if self.type else None,
            "operands": list(self.operands),
            "attributes": dict(self.attributes),
            "source": self.source.to_dict() if self.source else None,
        }


@dataclass(frozen=True)
class MIRTerminator:
    kind: str
    targets: tuple[str, ...] = ()
    condition: str | None = None
    value: str | None = None
    source: SourceMapping | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"jump", "branch", "return", "unreachable"}:
            raise ValueError(f"unsupported terminator: {self.kind}")
        if self.kind == "branch" and (self.condition is None or len(self.targets) != 2):
            raise ValueError("branch requires a condition and two targets")
        if self.kind == "jump" and len(self.targets) != 1:
            raise ValueError("jump requires one target")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "targets": list(self.targets),
            "condition": self.condition,
            "value": self.value,
            "source": self.source.to_dict() if self.source else None,
        }


@dataclass(frozen=True)
class MIRBasicBlock:
    id: str
    instructions: tuple[MIRInstruction, ...]
    terminator: MIRTerminator

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "instructions": [item.to_dict() for item in self.instructions],
            "terminator": self.terminator.to_dict(),
        }


@dataclass(frozen=True)
class MIRFunction:
    name: str
    parameters: tuple[MIRParameter, ...]
    return_type: PerformanceType
    blocks: tuple[MIRBasicBlock, ...]
    entry_block: str
    pure: bool
    source: SourceMapping
    generic_parameters: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not self.blocks:
            raise ValueError("MIR function requires a name and blocks")
        block_ids = [item.id for item in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError(f"duplicate block in {self.name}")
        if self.entry_block not in set(block_ids):
            raise ValueError(f"missing entry block in {self.name}")
        known = set(block_ids)
        for block in self.blocks:
            unknown = set(block.terminator.targets) - known
            if unknown:
                raise ValueError(f"unknown CFG targets in {self.name}: {sorted(unknown)}")
        results = [
            instruction.result
            for block in self.blocks
            for instruction in block.instructions
            if instruction.result is not None
        ]
        if len(results) != len(set(results)):
            raise ValueError(f"duplicate SSA result in {self.name}")

    @property
    def instruction_count(self) -> int:
        return sum(len(block.instructions) for block in self.blocks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameters": [item.to_dict() for item in self.parameters],
            "return_type": self.return_type.to_dict(),
            "blocks": [item.to_dict() for item in self.blocks],
            "entry_block": self.entry_block,
            "pure": self.pure,
            "source": self.source.to_dict(),
            "generic_parameters": list(self.generic_parameters),
            "instruction_count": self.instruction_count,
        }


@dataclass(frozen=True)
class PerformanceMIR:
    records: tuple[MIRRecord, ...]
    functions: tuple[MIRFunction, ...]
    entry_function: str
    source_sha256: str
    subset_types: tuple[str, ...] = NATIVE_SUBSET_TYPES
    subset_forms: tuple[str, ...] = NATIVE_SUBSET_FORMS
    schema_version: int = PERFORMANCE_MIR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        names = [item.name for item in self.functions]
        if len(names) != len(set(names)):
            raise ValueError("duplicate MIR function")
        if self.entry_function not in set(names):
            raise ValueError(f"unknown entry function: {self.entry_function}")
        record_names = [item.name for item in self.records]
        if len(record_names) != len(set(record_names)):
            raise ValueError("duplicate MIR record")

    @property
    def instruction_count(self) -> int:
        return sum(item.instruction_count for item in self.functions)

    def function(self, name: str) -> MIRFunction:
        matches = [item for item in self.functions if item.name == name]
        if len(matches) != 1:
            raise KeyError(name)
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "entry_function": self.entry_function,
            "source_sha256": self.source_sha256,
            "subset_types": list(self.subset_types),
            "subset_forms": list(self.subset_forms),
            "records": [item.to_dict() for item in self.records],
            "functions": [item.to_dict() for item in self.functions],
            "instruction_count": self.instruction_count,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PassStatistics:
    name: str
    instructions_before: int
    instructions_after: int
    instructions_removed: int = 0
    allocations_removed: int = 0
    loops_fused: int = 0
    bounds_checks_removed: int = 0
    calls_inlined: int = 0
    specializations_created: int = 0
    stack_allocations: int = 0
    heap_allocations: int = 0
    in_place_reuses: int = 0
    drops_inserted: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class PassSnapshot:
    name: str
    before: PerformanceMIR
    after: PerformanceMIR
    statistics: PassStatistics

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "before_digest": self.before.digest,
            "after_digest": self.after.digest,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "statistics": self.statistics.to_dict(),
        }


def scalar_layout(type_: PerformanceType) -> TypeLayout:
    if type_.kind == "bool":
        return TypeLayout(type_.name, 1, 1)
    if type_.kind in {"int", "uint", "float"}:
        size = (type_.bits or 64) // 8
        return TypeLayout(type_.name, size, size)
    if type_.kind == "slice":
        return TypeLayout(
            type_.name,
            32,
            8,
            (("data", 0), ("length", 8), ("heap", 16), ("refcount", 24)),
            "borrowed_or_shared_descriptor",
        )
    if type_.kind == "array":
        return TypeLayout(
            type_.name,
            32,
            8,
            (("data", 0), ("length", 8), ("heap", 16), ("refcount", 24)),
            "unique_or_shared_descriptor",
        )
    if type_.kind == "unit":
        return TypeLayout(type_.name, 0, 1)
    raise ValueError(f"layout requires record metadata: {type_.name}")


def record_layout(name: str, fields: tuple[tuple[str, PerformanceType], ...]) -> TypeLayout:
    offsets = []
    offset = 0
    maximum_alignment = 1
    for field_name, field_type in fields:
        layout = scalar_layout(field_type)
        maximum_alignment = max(maximum_alignment, layout.alignment)
        remainder = offset % layout.alignment
        if remainder:
            offset += layout.alignment - remainder
        offsets.append((field_name, offset))
        offset += layout.size
    remainder = offset % maximum_alignment
    if remainder:
        offset += maximum_alignment - remainder
    return TypeLayout(name, offset, maximum_alignment, tuple(offsets), "value")


__all__ = [
    "BOOL",
    "BYTES",
    "BYTES_BUILDER",
    "BYTES_VIEW",
    "TEXT",
    "TEXT_BUILDER",
    "TEXT_VIEW",
    "UTF8_DECODE",
    "FLOAT32",
    "FLOAT64",
    "INT64",
    "NATIVE_SUBSET_FORMS",
    "NATIVE_SUBSET_TYPES",
    "PERFORMANCE_MIR_SCHEMA_VERSION",
    "STAGE05P_NON_GOALS",
    "UINT64",
    "UNIT",
    "MIRBasicBlock",
    "MIRFunction",
    "MIRInstruction",
    "MIRParameter",
    "MIRRecord",
    "MIRTerminator",
    "PassSnapshot",
    "PassStatistics",
    "PerformanceMIR",
    "PerformanceType",
    "SourceMapping",
    "TypeLayout",
    "record_layout",
    "scalar_layout",
]
