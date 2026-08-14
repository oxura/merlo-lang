"""Differential evaluators and native runner for Stage 0.6P correctness."""

from __future__ import annotations

import ast
import hashlib
import math
import os
import re
import subprocess
import tempfile
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .native_c_backend import CEmitter, compile_c_source
from tools.benchmarks.merlo.json_streaming import JsonTokenError, tokenize_json
from research.archive.alpha1.merlo.native_hir import NativeHIRProgram, compile_native_hir, lower_native_hir_to_performance
from .performance_mir import MIRFunction, MIRInstruction, PerformanceMIR, PerformanceType
from .performance_opt import OPTIMIZATION_PIPELINE


NATIVE_DIFFERENTIAL_SCHEMA_VERSION = 2
_MASK64 = (1 << 64) - 1


class NativeExecutionError(RuntimeError):
    def __init__(
        self, kind: str, message: str, *, offset: int | None = None
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.offset = offset


@dataclass
class _Collection:
    data: list[Any]
    heap: bool
    shared: bool
    refs: int = 1
    alive: bool = True
    ownership: str = "Unique"

    def check(self) -> None:
        if not self.alive:
            raise NativeExecutionError("UseAfterDrop", "collection already dropped")


@dataclass
class _Bytes:
    data: bytearray
    capacity: int
    alive: bool = True
    ownership: str = "Unique"

    def check(self) -> None:
        if not self.alive:
            raise NativeExecutionError("BytesUseAfterMove", "Bytes owner is not live")


@dataclass
class _BytesBuilder:
    data: bytearray
    capacity: int
    state: str = "Live"
    active_views: int = 0

    def check(self) -> None:
        if self.state != "Live":
            raise NativeExecutionError(
                f"BytesBuilderUseAfter{self.state}",
                f"BytesBuilder is {self.state.lower()}",
            )

class _TextBuilder(_BytesBuilder):
    """UTF-8-valid builder reusing the BytesBuilder storage engine."""

    def check(self) -> None:
        if self.state != "Live":
            raise NativeExecutionError(
                f"TextBuilderUseAfter{self.state}",
                f"TextBuilder is {self.state.lower()}",
            )

@dataclass
class _BytesView:
    owner: _Bytes | _BytesBuilder | "_Text" | "_TextBuilder"
    start: int
    length: int
    released: bool = False

    def check(self) -> None:
        if self.released:
            raise NativeExecutionError(
                "BytesViewAfterBorrowEnd", "BytesView borrow has ended"
            )
        self.owner.check()


@dataclass
class _Text:
    data: bytearray
    capacity: int
    alive: bool = True
    ownership: str = "Unique"

    def check(self) -> None:
        if not self.alive:
            raise NativeExecutionError(
                "TextUseAfterMove", "Text owner is not live"
            )


@dataclass
class _TextView:
    owner: _Text | _TextBuilder
    start: int
    length: int
    released: bool = False

    def check(self) -> None:
        if self.released:
            raise NativeExecutionError(
                "TextViewAfterBorrowEnd", "TextView borrow has ended"
            )
        self.owner.check()


@dataclass
class _Utf8Decode:
    valid: bool
    text: _Text | None
    error_offset: int
    consumed: bool = False

    def check(self) -> None:
        if self.consumed:
            raise NativeExecutionError(
                "Utf8DecodeAlreadyConsumed",
                "Utf8Decode result already matched",
            )

@dataclass
class _Slot:
    value: Any = None


@dataclass(frozen=True)
class ExecutionObservation:
    status: str
    return_value: Any = None
    printed_checksum: int | float | None = None
    error_kind: str | None = None
    error_offset: int | None = None
    effect_trace: tuple[str, ...] = ()
    allocations: int = 0
    drops: int = 0
    retains: int = 0
    releases: int = 0
    frees: int = 0
    reallocations: int = 0
    growth_copied_bytes: int = 0
    extend_copied_bytes: int = 0
    finish_copies: int = 0
    required_append_bytes: int = 0
    final_ownership_state: tuple[tuple[str, int], ...] = ()
    steps: int = 0

    def semantic_key(self) -> tuple[Any, ...]:
        return (
            self.status,
            self.return_value,
            self.printed_checksum,
            self.error_kind,
            self.error_offset,
            self.effect_trace,
            self.allocations,
            self.drops,
            self.final_ownership_state,
            self.frees,
            self.reallocations,
            self.growth_copied_bytes,
            self.extend_copied_bytes,
            self.finish_copies,
            self.required_append_bytes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "return_value": self.return_value,
            "printed_checksum": self.printed_checksum,
            "error_kind": self.error_kind,
            "error_offset": self.error_offset,
            "effect_trace": list(self.effect_trace),
            "allocations": self.allocations,
            "drops": self.drops,
            "retains": self.retains,
            "releases": self.releases,
            "frees": self.frees,
            "reallocations": self.reallocations,
            "growth_copied_bytes": self.growth_copied_bytes,
            "extend_copied_bytes": self.extend_copied_bytes,
            "finish_copies": self.finish_copies,
            "required_append_bytes": self.required_append_bytes,
            "final_ownership_state": dict(self.final_ownership_state),
            "steps": self.steps,
        }


@dataclass
class _Metrics:
    allocations: int = 0
    drops: int = 0
    retains: int = 0
    releases: int = 0
    steps: int = 0
    effects: list[str] = field(default_factory=list)
    frees: int = 0
    reallocations: int = 0
    growth_copied_bytes: int = 0
    extend_copied_bytes: int = 0
    finish_copies: int = 0
    required_append_bytes: int = 0
    collections: list[_Collection] = field(default_factory=list)
    bytes_owners: list[_Bytes] = field(default_factory=list)
    bytes_builders: list[_BytesBuilder] = field(default_factory=list)
    texts: list[_Text] = field(default_factory=list)

    def ownership(self) -> tuple[tuple[str, int], ...]:
        counts: dict[str, int] = {}
        for value in self.collections:
            state = "Dropped" if not value.alive else value.ownership
            counts[state] = counts.get(state, 0) + 1
        for value in self.bytes_owners:
            state = (
                "Dropped"
                if not value.alive and value.ownership != "Moved"
                else value.ownership
            )
            counts[state] = counts.get(state, 0) + 1
        for value in self.bytes_builders:
            counts[value.state] = counts.get(value.state, 0) + 1
        for value in self.texts:
            state = (
                "Dropped"
                if not value.alive and value.ownership != "Moved"
                else value.ownership
            )
            counts[state] = counts.get(state, 0) + 1
        return tuple(sorted(counts.items()))


def _grow_builder(
    builder: _BytesBuilder, additional: int, metrics: _Metrics
) -> None:
    kind = "TextBuilder" if isinstance(builder, _TextBuilder) else "BytesBuilder"
    if builder.active_views:
        raise NativeExecutionError(
            f"{kind}ActiveView",
            f"cannot grow {kind} while a view is live",
        )
    if additional < 0 or additional > _MASK64 - len(builder.data):
        raise NativeExecutionError(
            f"{kind}LengthOverflow", "length + additional overflow"
        )
    required = len(builder.data) + additional
    if required <= builder.capacity:
        return
    if builder.capacity == 0:
        new_capacity = max(8, required)
    else:
        if builder.capacity > _MASK64 // 2:
            raise NativeExecutionError(
                f"{kind}CapacityOverflow", "capacity * 2 overflow"
            )
        new_capacity = max(required, builder.capacity * 2)
    if new_capacity > (1 << 63) - 1:
        raise NativeExecutionError(
            f"{kind}AllocationSizeOverflow",
            "allocation byte size overflow",
        )
    metrics.allocations += 1
    if builder.capacity:
        metrics.reallocations += 1
        metrics.growth_copied_bytes += len(builder.data)
        metrics.frees += 1
    builder.capacity = new_capacity


def _finish_builder(
    builder: _BytesBuilder, metrics: _Metrics
) -> _Bytes:
    builder.check()
    if builder.active_views:
        raise NativeExecutionError(
            "BytesBuilderActiveView",
            "cannot finish BytesBuilder while a view is live",
        )
    result = _Bytes(builder.data, builder.capacity)
    metrics.bytes_owners.append(result)
    builder.data = bytearray()
    builder.capacity = 0
    builder.state = "Finished"
    return result

def _finish_text_builder(
    builder: _TextBuilder, metrics: _Metrics
) -> _Text:
    builder.check()
    if builder.active_views:
        raise NativeExecutionError(
            "TextBuilderActiveView",
            "cannot finish TextBuilder while a view is live",
        )
    result = _Text(builder.data, builder.capacity)
    metrics.texts.append(result)
    builder.data = bytearray()
    builder.capacity = 0
    builder.state = "Finished"
    return result


def _drop_builder(builder: _BytesBuilder, metrics: _Metrics) -> None:
    builder.check()
    if builder.active_views:
        raise NativeExecutionError(
            "BytesBuilderActiveView",
            "cannot drop BytesBuilder while a view is live",
        )
    if builder.capacity:
        metrics.frees += 1
    builder.data = bytearray()
    builder.capacity = 0
    builder.state = "Dropped"
    metrics.drops += 1

def _drop_text(text: _Text, metrics: _Metrics) -> None:
    text.check()
    if text.capacity:
        metrics.frees += 1
    text.data = bytearray()
    text.capacity = 0
    text.alive = False
    text.ownership = "Dropped"
    metrics.drops += 1
    metrics.releases += 1


def _json_token_checksum(value: Any, metrics: _Metrics) -> int:
    if isinstance(value, _Bytes):
        value.check()
        payload = bytes(value.data)
    elif isinstance(value, (_BytesView, _TextView)):
        value.check()
        payload = bytes(
            value.owner.data[value.start : value.start + value.length]
        )
    elif isinstance(value, (bytes, bytearray, memoryview)):
        payload = bytes(value)
    else:
        raise NativeExecutionError(
            "InvalidJsonTokenInput",
            "json_token_checksum requires Bytes, BytesView, or TextView",
        )
    try:
        result = tokenize_json(payload)
    except JsonTokenError as exc:
        raise NativeExecutionError(
            exc.kind, str(exc), offset=exc.offset
        ) from exc
    stats = result.stats
    metrics.allocations += (
        stats.text_builder_allocations
        + stats.text_builder_reallocations
    )
    metrics.reallocations += stats.text_builder_reallocations
    metrics.frees += stats.text_builder_frees
    metrics.growth_copied_bytes += stats.text_builder_growth_copied_bytes
    metrics.extend_copied_bytes += stats.text_builder_semantic_bytes
    metrics.finish_copies += stats.text_builder_finish_copies
    metrics.required_append_bytes += stats.text_builder_semantic_bytes
    return result.checksum


class MIRInterpreter:
    def __init__(self, mir: PerformanceMIR, *, max_steps: int = 2_000_000) -> None:
        self.mir = mir
        self.max_steps = max_steps
        self.functions = {item.name: item for item in mir.functions}
        self.metrics = _Metrics()
        self.active_reborrows: list[dict[str, Any]] = []
        self.active_builder_views: dict[str, _BytesBuilder] = {}

    def _step(self) -> None:
        self.metrics.steps += 1
        if self.metrics.steps > self.max_steps:
            raise NativeExecutionError("StepLimit", "MIR execution step limit exceeded")

    @staticmethod
    def _wrap(value: Any, type_: PerformanceType | None) -> Any:
        if type_ is None:
            return value
        if (
            type_.kind == "record"
            and type_.record in {"Bytes", "BytesView", "TextView"}
            and isinstance(value, (bytes, bytearray, memoryview))
        ):
            if type_.record == "TextView":
                text = _Text(bytearray(value), len(value))
                return _TextView(text, 0, len(text.data))
            owner = _Bytes(bytearray(value), len(value))
            return (
                owner
                if type_.record == "Bytes"
                else _BytesView(owner, 0, len(owner.data))
            )
        if type_.kind == "uint":
            mask = (1 << (type_.bits or 64)) - 1
            return int(value) & mask
        if type_.kind == "int":
            bits = type_.bits or 64
            raw = int(value) & ((1 << bits) - 1)
            return raw - (1 << bits) if raw >= (1 << (bits - 1)) else raw
        if type_.kind == "bool":
            return bool(value)
        if type_.kind == "float":
            result = float(value)
            if type_.bits == 32:
                try:
                    result = struct.unpack("!f", struct.pack("!f", result))[0]
                except OverflowError:
                    result = math.copysign(math.inf, result)
            return result
        return value

    @staticmethod
    def _c_div(left: int, right: int) -> int:
        if right == 0:
            raise NativeExecutionError("DivisionByZero", "division by zero")
        quotient = abs(left) // abs(right)
        return -quotient if (left < 0) != (right < 0) else quotient
    @staticmethod
    def _numeric_div(left: int | float, right: int | float) -> int | float:
        if isinstance(left, int) and isinstance(right, int):
            return MIRInterpreter._c_div(left, right)
        left_value = float(left)
        right_value = float(right)
        if right_value == 0.0:
            if left_value == 0.0 or math.isnan(left_value):
                return math.nan
            sign = math.copysign(1.0, left_value) * math.copysign(1.0, right_value)
            return math.copysign(math.inf, sign)
        return left_value / right_value

    def _binary(self, operator: str, left: Any, right: Any) -> Any:
        operations = {
            "add": lambda: left + right,
            "sub": lambda: left - right,
            "mul": lambda: left * right,
            "div": lambda: self._numeric_div(left, right),
            "mod": lambda: left - self._c_div(left, right) * right,
            "bit_and": lambda: left & right,
            "bit_or": lambda: left | right,
            "bit_xor": lambda: left ^ right,
            "shift_left": lambda: left << (right & 63),
            "shift_right": lambda: left >> (right & 63),
            "and": lambda: bool(left and right),
            "or": lambda: bool(left or right),
        }
        try:
            return operations[operator]()
        except KeyError as exc:
            raise NativeExecutionError("UnknownOperator", operator) from exc

    @staticmethod
    def _compare(operator: str, left: Any, right: Any) -> bool:
        return {
            "eq": left == right,
            "ne": left != right,
            "lt": left < right,
            "le": left <= right,
            "gt": left > right,
            "ge": left >= right,
        }[operator]

    def _drop(self, value: Any) -> None:
        if not isinstance(value, _Collection):
            return
        value.check()
        self.metrics.drops += 1
        self.metrics.releases += 1
        value.refs -= 1
        if value.refs < 0:
            raise NativeExecutionError("DoubleDrop", "reference count became negative")
        if value.refs == 0:
            value.alive = False
            value.ownership = "Dropped"

    def _call(self, name: str, arguments: tuple[Any, ...]) -> Any:
        try:
            function = self.functions[name]
        except KeyError as exc:
            raise NativeExecutionError("UnknownFunction", name) from exc
        return self._execute_function(function, arguments)

    def _instruction(self, instruction: MIRInstruction, values: dict[str, Any]) -> Any:
        self._step()
        operands = [values[item] for item in instruction.operands]
        attributes = instruction.attribute_map
        op = instruction.op
        if op == "const":
            result = attributes["value"]
        elif op == "binary":
            result = self._binary(attributes["operator"], operands[0], operands[1])
        elif op == "unary":
            result = {
                "neg": lambda: -operands[0],
                "not": lambda: not operands[0],
                "bit_not": lambda: ~operands[0],
            }[attributes["operator"]]()
        elif op == "compare":
            result = self._compare(attributes["operator"], operands[0], operands[1])
        elif op == "alloc_local":
            result = _Slot()
        elif op in {"alloc_stack", "alloc_region", "alloc_heap"}:
            if instruction.type and instruction.type.kind in {"array", "slice"}:
                length = instruction.type.length or 0
                result = _Collection(
                    [0] * length,
                    op == "alloc_heap",
                    bool(instruction.type.shared),
                    ownership="SharedRc" if instruction.type.shared else "Unique",
                )
                self.metrics.collections.append(result)
                if op == "alloc_heap":
                    self.metrics.allocations += 1
            else:
                result = _Slot()
        elif op == "bytes_new":
            length = int(operands[0])
            if length < 0 or length > (1 << 63) - 1:
                raise NativeExecutionError(
                    "BytesAllocationOverflow", f"invalid Bytes length {length}"
                )
            try:
                result = _Bytes(bytearray(length), length)
            except MemoryError as exc:
                raise NativeExecutionError(
                    "AllocationFailure", f"cannot allocate {length} bytes"
                ) from exc
            self.metrics.bytes_owners.append(result)
            if length:
                self.metrics.allocations += 1
        elif op == "bytes_len":
            value = operands[0]
            value.check()
            result = value.length if isinstance(value, _BytesView) else len(value.data)
        elif op == "bytes_bounds_check":
            index, length = int(operands[0]), int(operands[1])
            if index < 0 or index >= length:
                raise NativeExecutionError(
                    "BytesIndexOutOfBounds", f"{index} outside 0..{length}"
                )
            return None
        elif op == "bytes_load":
            value, index = operands[0], int(operands[1])
            value.check()
            if isinstance(value, _BytesView):
                result = value.owner.data[value.start + index]
            else:
                result = value.data[index]
        elif op == "bytes_store":
            value, index, byte = operands[0], int(operands[1]), int(operands[2])
            if not isinstance(value, _Bytes):
                raise NativeExecutionError(
                    "BytesViewMutation", "cannot mutate a BytesView"
                )
            value.check()
            value.data[index] = byte & 255
            return None
        elif op == "bytes_slice":
            owner, start, length = operands[0], int(operands[1]), int(operands[2])
            if not isinstance(owner, (_Bytes, _BytesView)):
                raise NativeExecutionError(
                    "InvalidBytesOwner", "slice receiver is not Bytes or BytesView"
                )
            owner.check()
            owner_length = (
                len(owner.data) if isinstance(owner, _Bytes) else owner.length
            )
            if (
                start < 0
                or start > owner_length
                or length < 0
                or length > owner_length - start
            ):
                raise NativeExecutionError(
                    "BytesSliceOutOfBounds",
                    f"start={start} length={length} owner={owner_length}",
                )
            if isinstance(owner, _BytesView):
                result = _BytesView(owner.owner, owner.start + start, length)
            else:
                result = _BytesView(owner, start, length)
        elif op == "json_token_checksum":
            result = _json_token_checksum(operands[0], self.metrics)
        elif op == "bytes_to_text_transfer":
            return None
        elif op == "utf8_validate":
            source = operands[0]
            if not isinstance(source, _Bytes):
                raise NativeExecutionError(
                    "InvalidUtf8Input", "UTF-8 input is not owned Bytes"
                )
            source.check()
            payload = source.data
            capacity = source.capacity
            try:
                bytes(payload).decode("utf-8", "strict")
            except UnicodeDecodeError as error:
                if capacity:
                    self.metrics.frees += 1
                source.data = bytearray()
                source.capacity = 0
                source.alive = False
                source.ownership = "Dropped"
                self.metrics.drops += 1
                self.metrics.releases += 1
                result = _Utf8Decode(False, None, int(error.start))
            else:
                text = _Text(payload, capacity)
                self.metrics.texts.append(text)
                source.data = bytearray()
                source.capacity = 0
                source.alive = False
                source.ownership = "Moved"
                result = _Utf8Decode(True, text, 0)
        elif op == "utf8_decode_is_valid":
            decoded = operands[0]
            if not isinstance(decoded, _Utf8Decode):
                raise NativeExecutionError("InvalidUtf8Decode", op)
            decoded.check()
            result = decoded.valid
        elif op == "utf8_decode_take_text":
            decoded = operands[0]
            if not isinstance(decoded, _Utf8Decode):
                raise NativeExecutionError("InvalidUtf8Decode", op)
            decoded.check()
            if not decoded.valid or decoded.text is None:
                raise NativeExecutionError(
                    "InvalidUtf8DecodeArm",
                    "cannot take Text from Invalid UTF-8",
                )
            decoded.consumed = True
            result = decoded.text
        elif op == "utf8_decode_error_offset":
            decoded = operands[0]
            if not isinstance(decoded, _Utf8Decode):
                raise NativeExecutionError("InvalidUtf8Decode", op)
            decoded.check()
            if decoded.valid:
                raise NativeExecutionError(
                    "InvalidUtf8DecodeArm",
                    "Valid UTF-8 has no error offset",
                )
            result = decoded.error_offset
        elif op == "utf8_decode_drop":
            decoded = operands[0]
            if not isinstance(decoded, _Utf8Decode):
                raise NativeExecutionError("InvalidUtf8Decode", op)
            decoded.check()
            if decoded.valid and decoded.text is not None:
                _drop_text(decoded.text, self.metrics)
            decoded.consumed = True
            return None
        elif op == "text_from_ascii":
            scalar = int(operands[0])
            if scalar < 0 or scalar > 0x7F:
                raise NativeExecutionError(
                    "TextAsciiOutOfRange", str(scalar)
                )
            data = bytearray((scalar,))
            result = _Text(data, 1)
            self.metrics.texts.append(result)
            self.metrics.allocations += 1
        elif op == "text_from_scalar":
            scalar = int(operands[0])
            if (
                scalar < 0
                or scalar > 0x10FFFF
                or 0xD800 <= scalar <= 0xDFFF
            ):
                raise NativeExecutionError(
                    "InvalidUnicodeScalar", str(scalar)
                )
            data = bytearray(chr(scalar).encode("utf-8"))
            result = _Text(data, len(data))
            self.metrics.texts.append(result)
            if data:
                self.metrics.allocations += 1
        elif op == "text_from_surrogate":
            high, low = int(operands[0]), int(operands[1])
            if not (
                0xD800 <= high <= 0xDBFF
                and 0xDC00 <= low <= 0xDFFF
            ):
                raise NativeExecutionError(
                    "InvalidUnicodeSurrogatePair",
                    f"{high},{low}",
                )
            scalar = 0x10000 + ((high - 0xD800) << 10) + (
                low - 0xDC00
            )
            data = bytearray(chr(scalar).encode("utf-8"))
            result = _Text(data, len(data))
            self.metrics.texts.append(result)
            self.metrics.allocations += 1
        elif op == "text_len_bytes":
            value = operands[0]
            if not isinstance(value, (_Text, _TextView)):
                raise NativeExecutionError("InvalidText", op)
            value.check()
            result = (
                value.length
                if isinstance(value, _TextView)
                else len(value.data)
            )
        elif op == "text_view":
            owner = operands[0]
            if not isinstance(owner, _Text):
                raise NativeExecutionError("InvalidText", op)
            owner.check()
            result = _TextView(owner, 0, len(owner.data))
        elif op == "utf8_boundary_check":
            view, start, length = (
                operands[0],
                int(operands[1]),
                int(operands[2]),
            )
            if not isinstance(view, _TextView):
                raise NativeExecutionError("InvalidTextView", op)
            view.check()
            if (
                start < 0
                or start > view.length
                or length < 0
                or length > view.length - start
            ):
                raise NativeExecutionError(
                    "TextSliceOutOfBounds",
                    f"start={start} length={length} owner={view.length}",
                )
            payload = view.owner.data
            absolute_start = view.start + start
            absolute_end = absolute_start + length
            for offset in (absolute_start, absolute_end):
                if (
                    offset not in {view.start, view.start + view.length}
                    and payload[offset] & 0xC0 == 0x80
                ):
                    raise NativeExecutionError(
                        "TextSliceNotOnUtf8Boundary", str(offset)
                    )
            return None
        elif op == "text_slice":
            view, start, length = (
                operands[0],
                int(operands[1]),
                int(operands[2]),
            )
            if not isinstance(view, _TextView):
                raise NativeExecutionError("InvalidTextView", op)
            view.check()
            result = _TextView(
                view.owner, view.start + start, length
            )
        elif op == "text_view_as_bytes":
            view = operands[0]
            if not isinstance(view, _TextView):
                raise NativeExecutionError("InvalidTextView", op)
            view.check()
            result = _BytesView(
                view.owner, view.start, view.length
            )
        elif op == "utf8_scalar_next":
            view, offset = operands[0], int(operands[1])
            if not isinstance(view, _TextView):
                raise NativeExecutionError("InvalidTextView", op)
            view.check()
            if offset < 0 or offset >= view.length:
                raise NativeExecutionError(
                    "TextScalarOutOfBounds", str(offset)
                )
            byte = view.owner.data[view.start + offset]
            if byte < 0x80:
                result = 1
            elif byte < 0xE0:
                result = 2
            elif byte < 0xF0:
                result = 3
            else:
                result = 4
        elif op == "utf8_scalar_count":
            view = operands[0]
            if not isinstance(view, _TextView):
                raise NativeExecutionError("InvalidTextView", op)
            view.check()
            offset = 0
            count = 0
            while offset < view.length:
                byte = view.owner.data[view.start + offset]
                if byte < 0x80:
                    offset += 1
                elif byte < 0xE0:
                    offset += 2
                elif byte < 0xF0:
                    offset += 3
                else:
                    offset += 4
                count += 1
            result = count
        elif op == "text_to_bytes_transfer":
            text = operands[0]
            if not isinstance(text, _Text):
                raise NativeExecutionError("InvalidText", op)
            text.check()
            result = _Bytes(text.data, text.capacity)
            self.metrics.bytes_owners.append(result)
            text.data = bytearray()
            text.capacity = 0
            text.alive = False
            text.ownership = "Moved"
        elif op == "text_drop":
            text = operands[0]
            if not isinstance(text, _Text):
                raise NativeExecutionError("InvalidText", op)
            _drop_text(text, self.metrics)
            return None
        elif op == "text_builder_create":
            capacity = int(operands[0])
            if capacity < 0 or capacity > (1 << 63) - 1:
                raise NativeExecutionError(
                    "TextBuilderAllocationSizeOverflow",
                    f"invalid capacity {capacity}",
                )
            result = _TextBuilder(bytearray(), capacity)
            self.metrics.bytes_builders.append(result)
            if capacity:
                self.metrics.allocations += 1
        elif op == "text_builder_append_account":
            self.metrics.required_append_bytes += int(operands[0])
            return None
        elif op == "text_builder_scalar_width":
            scalar = int(operands[0])
            if (
                scalar < 0
                or scalar > 0x10FFFF
                or 0xD800 <= scalar <= 0xDFFF
            ):
                raise NativeExecutionError(
                    "TextBuilderInvalidUnicodeScalar", str(scalar)
                )
            result = (
                1
                if scalar <= 0x7F
                else 2
                if scalar <= 0x7FF
                else 3
                if scalar <= 0xFFFF
                else 4
            )
        elif op == "text_builder_push_ascii":
            builder, scalar = operands[0], int(operands[1])
            if not isinstance(builder, _TextBuilder):
                raise NativeExecutionError("InvalidTextBuilder", op)
            builder.check()
            if builder.active_views:
                raise NativeExecutionError(
                    "TextBuilderActiveView", "push during live view"
                )
            if not 0 <= scalar <= 0x7F:
                raise NativeExecutionError(
                    "TextBuilderAsciiOutOfRange", str(scalar)
                )
            if len(builder.data) >= builder.capacity:
                raise NativeExecutionError(
                    "TextBuilderCapacityInvariant",
                    "push without capacity",
                )
            builder.data.append(scalar)
            return None
        elif op == "text_builder_push_scalar":
            builder, scalar, width = (
                operands[0],
                int(operands[1]),
                int(operands[2]),
            )
            if not isinstance(builder, _TextBuilder):
                raise NativeExecutionError("InvalidTextBuilder", op)
            builder.check()
            if builder.active_views:
                raise NativeExecutionError(
                    "TextBuilderActiveView", "push during live view"
                )
            if (
                scalar < 0
                or scalar > 0x10FFFF
                or 0xD800 <= scalar <= 0xDFFF
            ):
                raise NativeExecutionError(
                    "TextBuilderInvalidUnicodeScalar", str(scalar)
                )
            payload = chr(scalar).encode("utf-8")
            if len(payload) != width:
                raise NativeExecutionError(
                    "TextBuilderScalarWidthInvariant", str(scalar)
                )
            if len(builder.data) + width > builder.capacity:
                raise NativeExecutionError(
                    "TextBuilderCapacityInvariant",
                    "push without capacity",
                )
            builder.data.extend(payload)
            return None
        elif op == "text_builder_extend":
            builder, view = operands
            if not isinstance(builder, _TextBuilder) or not isinstance(
                view, _TextView
            ):
                raise NativeExecutionError("InvalidTextBuilder", op)
            builder.check()
            view.check()
            if builder.active_views or view.owner is builder:
                raise NativeExecutionError(
                    "TextBuilderOverlappingExtend",
                    "extend overlaps live builder storage",
                )
            if len(builder.data) + view.length > builder.capacity:
                raise NativeExecutionError(
                    "TextBuilderCapacityInvariant",
                    "extend without capacity",
                )
            payload = view.owner.data[
                view.start : view.start + view.length
            ]
            builder.data.extend(payload)
            self.metrics.extend_copied_bytes += view.length
            return None
        elif op == "text_builder_view":
            builder = operands[0]
            if not isinstance(builder, _TextBuilder):
                raise NativeExecutionError("InvalidTextBuilder", op)
            builder.check()
            borrow_id = str(
                attributes.get("borrow_id", instruction.id)
            )
            if borrow_id in self.active_builder_views:
                raise NativeExecutionError(
                    "DuplicateBuilderView", borrow_id
                )
            builder.active_views += 1
            self.active_builder_views[borrow_id] = builder
            result = _TextView(builder, 0, len(builder.data))
        elif op == "text_builder_finish_transfer":
            builder = operands[0]
            if not isinstance(builder, _TextBuilder):
                raise NativeExecutionError("InvalidTextBuilder", op)
            result = _finish_text_builder(builder, self.metrics)
        elif op == "text_builder_drop":
            builder = operands[0]
            if not isinstance(builder, _TextBuilder):
                raise NativeExecutionError("InvalidTextBuilder", op)
            _drop_builder(builder, self.metrics)
            return None
        elif op == "builder_create":
            capacity = int(operands[0])
            if capacity < 0 or capacity > (1 << 63) - 1:
                raise NativeExecutionError(
                    "BytesBuilderAllocationSizeOverflow",
                    f"invalid capacity {capacity}",
                )
            result = _BytesBuilder(bytearray(), capacity)
            self.metrics.bytes_builders.append(result)
            if capacity:
                self.metrics.allocations += 1
        elif op == "builder_len":
            builder = operands[0]
            if not isinstance(builder, _BytesBuilder):
                raise NativeExecutionError("InvalidBytesBuilder", op)
            builder.check()
            result = len(builder.data)
        elif op == "builder_capacity":
            builder = operands[0]
            if not isinstance(builder, _BytesBuilder):
                raise NativeExecutionError("InvalidBytesBuilder", op)
            builder.check()
            result = builder.capacity
        elif op == "builder_reserve":
            builder = operands[0]
            if not isinstance(builder, _BytesBuilder):
                raise NativeExecutionError("InvalidBytesBuilder", op)
            builder.check()
            if builder.active_views:
                raise NativeExecutionError(
                    "BytesBuilderActiveView", "reserve during live view"
                )
            return None
        elif op == "builder_grow":
            builder, additional = operands[0], int(operands[1])
            if not isinstance(builder, _BytesBuilder):
                raise NativeExecutionError("InvalidBytesBuilder", op)
            _grow_builder(builder, additional, self.metrics)
            return None
        elif op == "builder_push":
            builder, byte = operands[0], int(operands[1])
            if not isinstance(builder, _BytesBuilder):
                raise NativeExecutionError("InvalidBytesBuilder", op)
            builder.check()
            if builder.active_views:
                raise NativeExecutionError(
                    "BytesBuilderActiveView", "push during live view"
                )
            if not 0 <= byte <= 255:
                raise NativeExecutionError(
                    "BytesBuilderByteOutOfRange", str(byte)
                )
            if len(builder.data) >= builder.capacity:
                raise NativeExecutionError(
                    "BytesBuilderCapacityInvariant", "push without capacity"
                )
            builder.data.append(byte)
            return None
        elif op == "builder_extend":
            builder, view = operands
            if not isinstance(builder, _BytesBuilder) or not isinstance(
                view, _BytesView
            ):
                raise NativeExecutionError("InvalidBytesBuilder", op)
            builder.check()
            view.check()
            if builder.active_views or view.owner is builder:
                raise NativeExecutionError(
                    "BytesBuilderOverlappingExtend",
                    "extend overlaps live builder storage",
                )
            if len(builder.data) + view.length > builder.capacity:
                raise NativeExecutionError(
                    "BytesBuilderCapacityInvariant", "extend without capacity"
                )
            payload = view.owner.data[
                view.start : view.start + view.length
            ]
            builder.data.extend(payload)
            self.metrics.extend_copied_bytes += view.length
            return None
        elif op == "builder_view":
            builder = operands[0]
            if not isinstance(builder, _BytesBuilder):
                raise NativeExecutionError("InvalidBytesBuilder", op)
            builder.check()
            borrow_id = str(attributes.get("borrow_id", instruction.id))
            if borrow_id in self.active_builder_views:
                raise NativeExecutionError(
                    "DuplicateBuilderView", borrow_id
                )
            builder.active_views += 1
            self.active_builder_views[borrow_id] = builder
            result = _BytesView(builder, 0, len(builder.data))
        elif op == "builder_finish_transfer":
            builder = operands[0]
            if not isinstance(builder, _BytesBuilder):
                raise NativeExecutionError("InvalidBytesBuilder", op)
            result = _finish_builder(builder, self.metrics)
        elif op == "builder_drop":
            builder = operands[0]
            if not isinstance(builder, _BytesBuilder):
                raise NativeExecutionError("InvalidBytesBuilder", op)
            _drop_builder(builder, self.metrics)
            return None
        elif op in {"allocation", "payload_copy", "free"}:
            return None
        elif op == "store_local":
            slot = operands[0]
            if not isinstance(slot, _Slot):
                raise NativeExecutionError("InvalidSlot", instruction.id)
            slot.value = operands[1]
            return None
        elif op == "load_local":
            slot = operands[0]
            if not isinstance(slot, _Slot):
                raise NativeExecutionError("InvalidSlot", instruction.id)
            result = slot.value
        elif op == "array_init":
            collection = operands[0]
            if not isinstance(collection, _Collection):
                raise NativeExecutionError("InvalidCollection", instruction.id)
            collection.check()
            collection.data = list(operands[1:])
            result = collection
        elif op == "array_len":
            collection = operands[0]
            collection.check()
            result = len(collection.data)
        elif op == "bounds_check":
            index, length = int(operands[0]), int(operands[1])
            if index < 0 or index >= length:
                raise NativeExecutionError("BoundsError", f"{index} outside 0..{length}")
            return None
        elif op == "index_load":
            collection, index = operands
            collection.check()
            try:
                result = collection.data[int(index)]
            except IndexError as exc:
                raise NativeExecutionError("BoundsError", str(index)) from exc
        elif op == "store_index":
            collection, index, value = operands
            collection.check()
            try:
                collection.data[int(index)] = value
            except IndexError as exc:
                raise NativeExecutionError("BoundsError", str(index)) from exc
            return None
        elif op == "record_init":
            record = next(item for item in self.mir.records if item.name == attributes["record"])
            result = {
                name: value
                for (name, _type), value in zip(record.fields, operands, strict=True)
            }
        elif op == "field_load":
            result = operands[0][attributes["field"]]
        elif op == "move":
            value = operands[0]
            if isinstance(value, _Bytes):
                value.check()
                result = _Bytes(value.data, value.capacity)
                self.metrics.bytes_owners.append(result)
                value.data = bytearray()
                value.capacity = 0
                value.alive = False
                value.ownership = "Moved"
            elif isinstance(value, _TextBuilder):
                value.check()
                if value.active_views:
                    raise NativeExecutionError(
                        "TextBuilderActiveView", "move during live view"
                    )
                result = _TextBuilder(value.data, value.capacity)
                self.metrics.bytes_builders.append(result)
                value.data = bytearray()
                value.capacity = 0
                value.state = "Moved"
            elif isinstance(value, _BytesBuilder):
                value.check()
                if value.active_views:
                    raise NativeExecutionError(
                        "BytesBuilderActiveView", "move during live view"
                    )
                result = _BytesBuilder(value.data, value.capacity)
                self.metrics.bytes_builders.append(result)
                value.data = bytearray()
                value.capacity = 0
                value.state = "Moved"
            elif isinstance(value, _Text):
                value.check()
                result = _Text(value.data, value.capacity)
                self.metrics.texts.append(result)
                value.data = bytearray()
                value.capacity = 0
                value.alive = False
                value.ownership = "Moved"
            else:
                result = value
        elif op == "copy":
            value = operands[0]
            if isinstance(value, _Collection):
                value.check()
                result = _Collection(list(value.data), value.heap, value.shared, ownership="Unique")
                self.metrics.collections.append(result)
                if value.heap:
                    self.metrics.allocations += 1
            else:
                result = value
        elif op == "retain":
            value = operands[0]
            if isinstance(value, _Collection):
                value.check()
                value.refs += 1
                value.ownership = "BorrowedShared"
                self.metrics.retains += 1
            result = value
        elif op in {"release", "drop"}:
            value = operands[0]
            if isinstance(value, _Bytes):
                value.check()
                if value.capacity:
                    self.metrics.frees += 1
                value.data = bytearray()
                value.capacity = 0
                value.alive = False
                value.ownership = "Dropped"
                self.metrics.drops += 1
                self.metrics.releases += 1
            else:
                self._drop(value)
            return None
        elif op in {"borrow_shared", "borrow_mut"}:
            value = operands[0]
            if isinstance(value, _Collection):
                value.check()
                value.ownership = "BorrowedShared" if op == "borrow_shared" else "BorrowedMutable"
            result = value
        elif op == "region_promote":
            value = operands[0]
            if isinstance(value, _Collection):
                value.ownership = "RegionOwned"
            result = value
        elif op in {"borrow_argument", "reborrow_argument"}:
            view = operands[0]
            if not isinstance(view, (_BytesView, _TextView)):
                raise NativeExecutionError("InvalidReborrow", op)
            view.check()
            borrow_id = str(attributes.get("borrow_id", instruction.id))
            if op == "borrow_argument":
                if self.active_reborrows:
                    raise NativeExecutionError(
                        "NestedRootBorrow", borrow_id
                    )
            else:
                if not self.active_reborrows:
                    if (
                        not isinstance(view.owner, _BytesBuilder)
                        or view.owner.active_views <= 0
                    ):
                        raise NativeExecutionError(
                            "ParentBorrowEndedBeforeChild", borrow_id
                        )
                    parent = {
                        "owner": view.owner,
                        "start": 0,
                        "length": len(view.owner.data),
                    }
                else:
                    parent = self.active_reborrows[-1]
                if parent["owner"] is not view.owner:
                    raise NativeExecutionError(
                        "ReborrowRootOwnerMismatch", borrow_id
                    )
                parent_start = int(parent["start"])
                parent_end = parent_start + int(parent["length"])
                child_end = view.start + view.length
                if view.start < parent_start or child_end > parent_end:
                    raise NativeExecutionError(
                        "ReborrowOutsideParentRange", borrow_id
                    )
                if len(self.active_reborrows) >= 3:
                    raise NativeExecutionError(
                        "ReborrowDepthExceeded", borrow_id
                    )
            self.active_reborrows.append(
                {
                    "borrow_id": borrow_id,
                    "kind": op,
                    "owner": view.owner,
                    "root_owner": attributes.get("root_owner"),
                    "start": view.start,
                    "length": view.length,
                }
            )
            self.metrics.effects.append(
                f"{op}:{borrow_id}:depth={len(self.active_reborrows)}:"
                f"root={attributes.get('root_owner')}:same_payload=true"
            )
            return None
        elif op == "borrow_return_transfer":
            origin, returned = operands
            if not isinstance(
                origin, (_BytesView, _TextView)
            ) or not isinstance(returned, (_BytesView, _TextView)):
                raise NativeExecutionError(
                    "InvalidBorrowReturn", instruction.id
                )
            origin.check()
            returned.check()
            if origin.owner is not returned.owner:
                raise NativeExecutionError(
                    "WrongBorrowReturnOrigin", instruction.id
                )
            origin_end = origin.start + origin.length
            returned_end = returned.start + returned.length
            if returned.start < origin.start or returned_end > origin_end:
                raise NativeExecutionError(
                    "BorrowReturnOutsideOriginRange", instruction.id
                )
            if not self.active_reborrows:
                raise NativeExecutionError(
                    "BorrowReturnWithoutCallerBorrow", instruction.id
                )
            active = self.active_reborrows[-1]
            active_end = int(active["start"]) + int(active["length"])
            if (
                active["owner"] is not returned.owner
                or returned.start < int(active["start"])
                or returned_end > active_end
            ):
                raise NativeExecutionError(
                    "BorrowReturnOutsideRootRange", instruction.id
                )
            self.metrics.effects.append(
                f"borrow_return_transfer:{attributes.get('borrow_id')}:"
                f"root={attributes.get('root_owner')}:offset="
                f"{returned.start - origin.start}:length={returned.length}"
            )
            return None
        elif op == "caller_borrow_continue":
            returned = operands[0]
            if not isinstance(returned, (_BytesView, _TextView)):
                raise NativeExecutionError(
                    "InvalidCallerBorrowContinue", instruction.id
                )
            returned.check()
            if not self.active_reborrows:
                raise NativeExecutionError(
                    "CallerBorrowContinueWithoutStart", instruction.id
                )
            active = self.active_reborrows[-1]
            active_end = int(active["start"]) + int(active["length"])
            returned_end = returned.start + returned.length
            if (
                active["owner"] is not returned.owner
                or returned.start < int(active["start"])
                or returned_end > active_end
            ):
                raise NativeExecutionError(
                    "CallerBorrowContinueWrongOrigin", instruction.id
                )
            self.metrics.effects.append(
                f"caller_borrow_continue:{attributes.get('borrow_id')}:"
                f"root={attributes.get('root_owner')}:"
                f"last_use={attributes.get('last_use_line')}"
            )
            return None
        elif op in {"borrow_end", "reborrow_end"}:
            borrow_id = str(attributes.get("borrow_id", instruction.id))
            if attributes.get("builder_owner") is not None:
                try:
                    builder = self.active_builder_views.pop(borrow_id)
                except KeyError as exc:
                    raise NativeExecutionError(
                        "BorrowEndWithoutStart", borrow_id
                    ) from exc
                if builder.active_views <= 0:
                    raise NativeExecutionError(
                        "BorrowEndOrderViolation", borrow_id
                    )
                builder.active_views -= 1
                if operands and isinstance(
                    operands[0], (_BytesView, _TextView)
                ):
                    operands[0].released = True
                self.metrics.effects.append(
                    f"borrow_end:{borrow_id}:builder_views="
                    f"{builder.active_views}"
                )
                return None
            if attributes.get("text_owner") is not None:
                if operands and isinstance(operands[0], _TextView):
                    operands[0].released = True
                self.metrics.effects.append(
                    f"borrow_end:{borrow_id}:text_owner="
                    f"{attributes.get('text_owner')}"
                )
                return None
            if not self.active_reborrows:
                raise NativeExecutionError(
                    "BorrowEndWithoutStart", borrow_id
                )
            active = self.active_reborrows[-1]
            expected_kind = (
                "reborrow_argument"
                if op == "reborrow_end"
                else "borrow_argument"
            )
            if (
                active["borrow_id"] != borrow_id
                or active["kind"] != expected_kind
            ):
                raise NativeExecutionError(
                    "ReborrowEndOrderViolation", borrow_id
                )
            self.active_reborrows.pop()
            self.metrics.effects.append(
                f"{op}:{borrow_id}:remaining={len(self.active_reborrows)}"
            )
            return None
        elif op == "call":
            result = self._call(str(attributes["callee"]), tuple(operands))
        elif op.startswith("collection_map"):
            source, allocation = operands
            source.check()
            allocation.check()
            allocation.data = [
                self._call(str(attributes["function"]), (item,))
                for item in source.data
            ]
            result = allocation
        elif op.startswith("collection_filter"):
            source, allocation = operands
            source.check()
            allocation.check()
            allocation.data = [
                item
                for item in source.data
                if self._call(str(attributes["function"]), (item,))
            ]
            result = allocation
        elif op.startswith("collection_fold"):
            source, result = operands
            source.check()
            for item in source.data:
                result = self._call(str(attributes["function"]), (result, item))
        elif op == "fused_collection_loop":
            source, result = operands
            source.check()
            for item in source.data:
                mapped = self._call(str(attributes["map_function"]), (item,))
                predicate = attributes.get("filter_function")
                if predicate is None or self._call(str(predicate), (mapped,)):
                    result = self._call(str(attributes["fold_function"]), (result, mapped))
        else:
            raise NativeExecutionError("UnsupportedMIR", op)
        if instruction.result is not None:
            values[instruction.result] = (
                result
                if isinstance(
                    result,
                    (
                        _Slot,
                        _Collection,
                        _Bytes,
                        _BytesBuilder,
                        _BytesView,
                        _Text,
                        _TextView,
                        _Utf8Decode,
                        dict,
                    ),
                )
                else self._wrap(result, instruction.type)
            )
        return result

    def _execute_function(self, function: MIRFunction, arguments: tuple[Any, ...]) -> Any:
        if len(arguments) != len(function.parameters):
            raise NativeExecutionError("ArgumentCount", function.name)
        values = {
            parameter.value: self._wrap(argument, parameter.type)
            for parameter, argument in zip(
                function.parameters, arguments, strict=True
            )
        }
        blocks = {item.id: item for item in function.blocks}
        current = function.entry_block
        while True:
            self._step()
            block = blocks[current]
            for instruction in block.instructions:
                self._instruction(instruction, values)
            terminator = block.terminator
            if terminator.kind == "return":
                return values[terminator.value] if terminator.value is not None else None
            if terminator.kind == "jump":
                current = terminator.targets[0]
                continue
            if terminator.kind == "branch":
                current = terminator.targets[0] if values[terminator.condition] else terminator.targets[1]
                continue
            raise NativeExecutionError("Unreachable", block.id)

    def run(self, arguments: Iterable[Any] = ()) -> ExecutionObservation:
        try:
            result = self._call(self.mir.entry_function, tuple(arguments))
            if self.active_reborrows:
                raise NativeExecutionError(
                    "EscapingReborrow",
                    str(self.active_reborrows[-1]["borrow_id"]),
                )
            if self.active_builder_views:
                raise NativeExecutionError(
                    "EscapingBuilderView",
                    next(iter(self.active_builder_views)),
                )
        except NativeExecutionError as exc:
            return ExecutionObservation(
                "ERROR",
                error_kind=exc.kind,
                error_offset=exc.offset,
                effect_trace=tuple(self.metrics.effects),
                allocations=self.metrics.allocations,
                drops=self.metrics.drops,
                retains=self.metrics.retains,
                releases=self.metrics.releases,
                frees=self.metrics.frees,
                reallocations=self.metrics.reallocations,
                growth_copied_bytes=self.metrics.growth_copied_bytes,
                extend_copied_bytes=self.metrics.extend_copied_bytes,
                finish_copies=self.metrics.finish_copies,
                final_ownership_state=self.metrics.ownership(),
                required_append_bytes=self.metrics.required_append_bytes,
                steps=self.metrics.steps,
            )
        return_type = self.mir.function(self.mir.entry_function).return_type
        printed_checksum = (
            int(result) & _MASK64
            if isinstance(result, (int, bool)) and return_type.kind == "uint"
            else int(result)
            if isinstance(result, (int, bool))
            else None
        )
        return ExecutionObservation(
            "OK",
            result,
            printed_checksum,
            effect_trace=tuple(self.metrics.effects),
            allocations=self.metrics.allocations,
            drops=self.metrics.drops,
            retains=self.metrics.retains,
            releases=self.metrics.releases,
            frees=self.metrics.frees,
            reallocations=self.metrics.reallocations,
            growth_copied_bytes=self.metrics.growth_copied_bytes,
            extend_copied_bytes=self.metrics.extend_copied_bytes,
            finish_copies=self.metrics.finish_copies,
            final_ownership_state=self.metrics.ownership(),
            steps=self.metrics.steps,
            required_append_bytes=self.metrics.required_append_bytes,
        )


class _Return(Exception):
    def __init__(self, value: Any) -> None:
        self.value = value


class _Moved:
    pass


@dataclass
class _AstBinding:
    value: Any
    mutable: bool
    type_name: str | None = None


class _Scope:
    def __init__(self, parent: "_Scope | None" = None) -> None:
        self.parent = parent
        self.values: dict[str, _AstBinding] = {}

    def define(
        self,
        name: str,
        value: Any,
        mutable: bool,
        type_name: str | None = None,
    ) -> None:
        if name in self.values:
            raise NativeExecutionError("DuplicateBinding", name)
        self.values[name] = _AstBinding(value, mutable, type_name)

    def binding(self, name: str) -> _AstBinding:
        if name in self.values:
            return self.values[name]
        if self.parent is not None:
            return self.parent.binding(name)
        raise NativeExecutionError("UnknownValue", name)

    def read(self, name: str) -> Any:
        value = self.binding(name).value
        if isinstance(value, _Moved):
            raise NativeExecutionError("UseAfterMove", name)
        return value

    def assign(self, name: str, value: Any) -> None:
        binding = self.binding(name)
        if not binding.mutable:
            raise NativeExecutionError("ImmutableBinding", name)
        binding.value = value

class HIREvaluator:
    """Independent structured evaluator for Native HIR normalized source."""

    def __init__(self, program: NativeHIRProgram, *, max_steps: int = 2_000_000) -> None:
        self.program = program
        self.max_steps = max_steps
        self.metrics = _Metrics()
        module = ast.parse(program.cst.normalized_source, filename=program.cst.path)
        self.functions = {
            item.name: item for item in module.body if isinstance(item, ast.FunctionDef)
        }
        self.records = {
            item.name: tuple(
                statement.target.id
                for statement in item.body
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
            )
            for item in module.body
            if isinstance(item, ast.ClassDef)
        }
        from .performance_frontend import _preprocess

        self.declaration_kinds = _preprocess(program.cst.source).declaration_kinds
        self.numeric_context: list[str | None] = []

    def _step(self) -> None:
        self.metrics.steps += 1
        if self.metrics.steps > self.max_steps:
            raise NativeExecutionError("StepLimit", "HIR execution step limit exceeded")

    @staticmethod
    def _wrap(value: Any, type_name: str | None) -> Any:
        if (
            type_name in {"Bytes", "BytesView", "TextView"}
            and isinstance(value, (bytes, bytearray, memoryview))
        ):
            if type_name == "TextView":
                text = _Text(bytearray(value), len(value))
                return _TextView(text, 0, len(text.data))
            owner = _Bytes(bytearray(value), len(value))
            return (
                owner
                if type_name == "Bytes"
                else _BytesView(owner, 0, len(owner.data))
            )
        if type_name in {"Float32", "Float64"} and isinstance(value, (int, float)):
            result = float(value)
            if type_name == "Float32":
                try:
                    result = struct.unpack("!f", struct.pack("!f", result))[0]
                except OverflowError:
                    result = math.copysign(math.inf, result)
            return result
        if not isinstance(value, int) or isinstance(value, bool):
            return value
        if type_name == "UInt64":
            return int(value) & _MASK64
        if type_name == "Int64":
            raw = int(value) & _MASK64
            return raw - (1 << 64) if raw >= (1 << 63) else raw
        return value

    @staticmethod
    def _scalar_type(type_name: str | None) -> str | None:
        if not type_name:
            return None
        for candidate in ("UInt64", "Int64", "Float32", "Float64"):
            if candidate in type_name:
                return candidate
        return None

    def _numeric_type(self, node: ast.AST, scope: _Scope) -> str | None:
        if isinstance(node, ast.Name):
            if node.id in self.functions:
                return self._scalar_type(ast.unparse(self.functions[node.id].returns))
            try:
                return self._scalar_type(scope.binding(node.id).type_name)
            except NativeExecutionError:
                return None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function = self.functions.get(node.func.id)
            return (
                self._scalar_type(ast.unparse(function.returns))
                if function is not None
                else None
            )
        if isinstance(node, ast.Subscript):
            return self._numeric_type(node.value, scope)
        if isinstance(node, ast.UnaryOp):
            return self._numeric_type(node.operand, scope)
        if isinstance(node, ast.BinOp):
            return self._numeric_type(node.left, scope) or self._numeric_type(
                node.right, scope
            )
        return self.numeric_context[-1] if self.numeric_context else None

    def _drop(self, value: Any) -> None:
        if isinstance(value, _Bytes):
            value.check()
            if value.capacity:
                self.metrics.frees += 1
            value.data = bytearray()
            value.capacity = 0
            value.alive = False
            value.ownership = "Dropped"
            self.metrics.drops += 1
            self.metrics.releases += 1
            return
        if isinstance(value, _BytesBuilder):
            _drop_builder(value, self.metrics)
            return
        if isinstance(value, _Text):
            _drop_text(value, self.metrics)
            return
        if not isinstance(value, _Collection):
            return
        value.check()
        self.metrics.drops += 1
        self.metrics.releases += 1
        value.refs -= 1
        if value.refs == 0:
            value.alive = False
            value.ownership = "Dropped"
        elif value.refs < 0:
            raise NativeExecutionError("DoubleDrop", "negative reference count")

    def _call(self, name: str, arguments: list[Any], node: ast.Call | None = None, scope: _Scope | None = None) -> Any:
        if name == "len":
            value = arguments[0]
            return len(value.data if isinstance(value, _Collection) else value)
        if name == "json_token_checksum":
            return _json_token_checksum(arguments[0], self.metrics)
        if name == "move":
            if node is None or scope is None or not isinstance(node.args[0], ast.Name):
                raise NativeExecutionError("InvalidMove", name)
            binding = scope.binding(node.args[0].id)
            if isinstance(binding.value, _Moved):
                raise NativeExecutionError("DoubleMove", node.args[0].id)
            value = binding.value
            if isinstance(value, _TextBuilder):
                value.check()
                if value.active_views:
                    raise NativeExecutionError(
                        "TextBuilderActiveView", "move during live view"
                    )
                result = _TextBuilder(value.data, value.capacity)
                self.metrics.bytes_builders.append(result)
                value.data = bytearray()
                value.capacity = 0
                value.state = "Moved"
            elif isinstance(value, _BytesBuilder):
                value.check()
                if value.active_views:
                    raise NativeExecutionError(
                        "BytesBuilderActiveView", "move during live view"
                    )
                result = _BytesBuilder(value.data, value.capacity)
                self.metrics.bytes_builders.append(result)
                value.data = bytearray()
                value.capacity = 0
                value.state = "Moved"
            else:
                result = value
            binding.value = _Moved()
            return result
        if name in {"borrow", "borrow_shared", "meldra_borrow_shared"}:
            value = arguments[0]
            if isinstance(value, _Collection):
                value.check()
                value.ownership = "BorrowedShared"
            return value
        if name in {"borrow_mut", "meldra_borrow_mut"}:
            value = arguments[0]
            if isinstance(value, _Collection):
                value.check()
                value.ownership = "BorrowedMutable"
            return value
        if name == "retain":
            value = arguments[0]
            if isinstance(value, _Collection):
                value.check()
                value.refs += 1
                value.ownership = "BorrowedShared"
                self.metrics.retains += 1
            return value
        if name == "release":
            self._drop(arguments[0])
            return None
        if name == "drop":
            self._drop(arguments[0])
            return None
        if name in self.records:
            fields = self.records[name]
            values = dict(zip(fields, arguments, strict=False))
            if node is not None:
                for keyword in node.keywords:
                    if keyword.arg is not None:
                        values[keyword.arg] = self._expression(keyword.value, scope)
            return values
        if name == "map":
            source, function_name = arguments
            source.check()
            result = _Collection([self._invoke(function_name, [item]) for item in source.data], True, False)
            self.metrics.collections.append(result)
            self.metrics.allocations += 1
            return result
        if name == "filter":
            source, function_name = arguments
            source.check()
            result = _Collection([item for item in source.data if self._invoke(function_name, [item])], True, False)
            self.metrics.collections.append(result)
            self.metrics.allocations += 1
            return result
        if name == "fold":
            source, result, function_name = arguments
            source.check()
            for item in source.data:
                result = self._invoke(function_name, [result, item])
            return result
        return self._invoke(name, arguments)

    @staticmethod
    def _end_borrows(scope: _Scope) -> None:
        seen: set[int] = set()
        for binding in scope.values.values():
            value = binding.value
            if not isinstance(value, _Collection) or id(value) in seen:
                continue
            seen.add(id(value))
            if value.ownership in {"BorrowedShared", "BorrowedMutable"}:
                value.ownership = "SharedRc" if value.shared else "Unique"
    def _drop_owned_bytes(self, scope: _Scope) -> None:
        seen: set[int] = set()
        for binding in scope.values.values():
            value = binding.value
            if id(value) in seen:
                continue
            if isinstance(value, _Bytes):
                seen.add(id(value))
                if value.alive:
                    self._drop(value)
            elif isinstance(value, _BytesBuilder):
                seen.add(id(value))
                if value.state == "Live":
                    self._drop(value)
            elif isinstance(value, _Text):
                seen.add(id(value))
                if value.alive:
                    self._drop(value)

    def _finish_child_scope(
        self,
        scope: _Scope,
        escaping: Any | None = None,
    ) -> None:
        if isinstance(escaping, (_Bytes, _BytesBuilder, _Text)):
            for binding in scope.values.values():
                if binding.value is escaping:
                    binding.value = _Moved()
        self._end_borrows(scope)
        self._drop_owned_bytes(scope)

    def _invoke(self, name: str, arguments: list[Any]) -> Any:
        try:
            function = self.functions[name]
        except KeyError as exc:
            raise NativeExecutionError("UnknownFunction", name) from exc
        scope = _Scope()
        if len(arguments) != len(function.args.args):
            raise NativeExecutionError("ArgumentCount", name)
        for parameter, value in zip(function.args.args, arguments, strict=True):
            type_name = ast.unparse(parameter.annotation) if parameter.annotation else None
            scope.define(
                parameter.arg,
                self._wrap(value, type_name),
                False,
                type_name,
            )
        return_type = ast.unparse(function.returns) if function.returns else None
        self.numeric_context.append(self._scalar_type(return_type))
        try:
            self._statements(function.body, scope, tail_returns=True)
        except _Return as returned:
            result = self._wrap(returned.value, self._scalar_type(return_type))
            if isinstance(result, (_Bytes, _BytesBuilder, _Text)):
                for binding in scope.values.values():
                    if binding.value is result:
                        binding.value = _Moved()
            return result
        finally:
            self._end_borrows(scope)
            self._drop_owned_bytes(scope)
            self.numeric_context.pop()
        return None

    def _expression(self, node: ast.AST, scope: _Scope | None) -> Any:
        self._step()
        if scope is None:
            raise NativeExecutionError("MissingScope", type(node).__name__)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in {"Bytes", "BytesBuilder", "Text", "TextBuilder"}:
                return node.id
            if node.id in self.functions or node.id in self.records:
                return node.id
            return scope.read(node.id)
        if isinstance(node, ast.List):
            values = [self._expression(item, scope) for item in node.elts]
            collection = _Collection(values, True, False)
            self.metrics.collections.append(collection)
            self.metrics.allocations += 1
            return collection
        if isinstance(node, ast.BinOp):
            left = self._expression(node.left, scope)
            right = self._expression(node.right, scope)
            operator = {
                ast.Add: lambda: left + right,
                ast.Sub: lambda: left - right,
                ast.Mult: lambda: left * right,
                ast.Div: lambda: MIRInterpreter._numeric_div(left, right),
                ast.FloorDiv: lambda: MIRInterpreter._numeric_div(left, right),
                ast.Mod: lambda: left - MIRInterpreter._c_div(left, right) * right,
                ast.BitAnd: lambda: left & right,
                ast.BitOr: lambda: left | right,
                ast.BitXor: lambda: left ^ right,
                ast.LShift: lambda: left << (right & 63),
                ast.RShift: lambda: left >> (right & 63),
            }.get(type(node.op))
            if operator is None:
                raise NativeExecutionError("UnknownOperator", type(node.op).__name__)
            return self._wrap(operator(), self._numeric_type(node, scope))
        if isinstance(node, ast.UnaryOp):
            value = self._expression(node.operand, scope)
            if isinstance(node.op, ast.USub):
                return self._wrap(-value, self._numeric_type(node, scope))
            if isinstance(node.op, ast.Not):
                return not value
            if isinstance(node.op, ast.Invert):
                return self._wrap(~value, self._numeric_type(node, scope))
        if isinstance(node, ast.Compare):
            left = self._expression(node.left, scope)
            right = self._expression(node.comparators[0], scope)
            return {
                ast.Eq: left == right,
                ast.NotEq: left != right,
                ast.Lt: left < right,
                ast.LtE: left <= right,
                ast.Gt: left > right,
                ast.GtE: left >= right,
            }[type(node.ops[0])]
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                return all(self._expression(item, scope) for item in node.values)
            return any(self._expression(item, scope) for item in node.values)
        if isinstance(node, ast.Attribute):
            value = self._expression(node.value, scope)
            if isinstance(value, dict):
                return value[node.attr]
            raise NativeExecutionError(
                "UnsupportedAttribute", f"{type(value).__name__}.{node.attr}"
            )
        if isinstance(node, ast.Subscript):
            value = self._expression(node.value, scope)
            index = int(self._expression(node.slice, scope))
            if isinstance(value, _Collection):
                value.check()
                if index < 0 or index >= len(value.data):
                    raise NativeExecutionError("BoundsError", str(index))
                return value.data[index]
            if isinstance(value, _Bytes):
                value.check()
                if index < 0 or index >= len(value.data):
                    raise NativeExecutionError(
                        "BytesIndexOutOfBounds", str(index)
                    )
                return value.data[index]
            if isinstance(value, _BytesView):
                value.check()
                if index < 0 or index >= value.length:
                    raise NativeExecutionError(
                        "BytesIndexOutOfBounds", str(index)
                    )
                return value.owner.data[value.start + index]
            return value[index]
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                receiver = self._expression(node.func.value, scope)
                arguments = [
                    self._expression(item, scope) for item in node.args
                ]
                if receiver == "Bytes" and node.func.attr == "new":
                    length = int(arguments[0])
                    if length < 0 or length > (1 << 63) - 1:
                        raise NativeExecutionError(
                            "BytesAllocationOverflow",
                            f"invalid Bytes length {length}",
                        )
                    result = _Bytes(bytearray(length), length)
                    self.metrics.bytes_owners.append(result)
                    if length:
                        self.metrics.allocations += 1
                    return result
                if receiver == "Text":
                    if (
                        node.func.attr == "from_utf8"
                        and len(arguments) == 1
                    ):
                        source = arguments[0]
                        if not isinstance(source, _Bytes):
                            raise NativeExecutionError(
                                "InvalidUtf8Input",
                                "UTF-8 input is not owned Bytes",
                            )
                        source.check()
                        payload = source.data
                        capacity = source.capacity
                        try:
                            bytes(payload).decode("utf-8", "strict")
                        except UnicodeDecodeError as error:
                            if capacity:
                                self.metrics.frees += 1
                            source.data = bytearray()
                            source.capacity = 0
                            source.alive = False
                            source.ownership = "Dropped"
                            self.metrics.drops += 1
                            self.metrics.releases += 1
                            return _Utf8Decode(
                                False, None, int(error.start)
                            )
                        text = _Text(payload, capacity)
                        self.metrics.texts.append(text)
                        source.data = bytearray()
                        source.capacity = 0
                        source.alive = False
                        source.ownership = "Moved"
                        return _Utf8Decode(True, text, 0)
                    if (
                        node.func.attr == "from_ascii"
                        and len(arguments) == 1
                    ):
                        scalar = int(arguments[0])
                        if not 0 <= scalar <= 0x7F:
                            raise NativeExecutionError(
                                "TextAsciiOutOfRange", str(scalar)
                            )
                    elif (
                        node.func.attr == "from_scalar"
                        and len(arguments) == 1
                    ):
                        scalar = int(arguments[0])
                        if (
                            scalar < 0
                            or scalar > 0x10FFFF
                            or 0xD800 <= scalar <= 0xDFFF
                        ):
                            raise NativeExecutionError(
                                "InvalidUnicodeScalar", str(scalar)
                            )
                    elif (
                        node.func.attr == "from_surrogate"
                        and len(arguments) == 2
                    ):
                        high, low = (
                            int(arguments[0]),
                            int(arguments[1]),
                        )
                        if not (
                            0xD800 <= high <= 0xDBFF
                            and 0xDC00 <= low <= 0xDFFF
                        ):
                            raise NativeExecutionError(
                                "InvalidUnicodeSurrogatePair",
                                f"{high},{low}",
                            )
                        scalar = (
                            0x10000
                            + ((high - 0xD800) << 10)
                            + (low - 0xDC00)
                        )
                    else:
                        raise NativeExecutionError(
                            "DynamicCall",
                            f"unsupported Text constructor "
                            f"{node.func.attr}",
                        )
                    data = bytearray(chr(scalar).encode("utf-8"))
                    result = _Text(data, len(data))
                    self.metrics.texts.append(result)
                    if data:
                        self.metrics.allocations += 1
                    return result
                if isinstance(receiver, (_Text, _TextView)):
                    receiver.check()
                    if node.func.attr == "len_bytes" and not arguments:
                        return (
                            receiver.length
                            if isinstance(receiver, _TextView)
                            else len(receiver.data)
                        )
                    if (
                        isinstance(receiver, _Text)
                        and node.func.attr == "as_view"
                        and not arguments
                    ):
                        return _TextView(
                            receiver, 0, len(receiver.data)
                        )
                    if (
                        isinstance(receiver, _Text)
                        and node.func.attr == "into_bytes"
                        and not arguments
                    ):
                        result = _Bytes(
                            receiver.data, receiver.capacity
                        )
                        self.metrics.bytes_owners.append(result)
                        receiver.data = bytearray()
                        receiver.capacity = 0
                        receiver.alive = False
                        receiver.ownership = "Moved"
                        return result
                    if (
                        isinstance(receiver, _TextView)
                        and node.func.attr == "as_bytes"
                        and not arguments
                    ):
                        return _BytesView(
                            receiver.owner,
                            receiver.start,
                            receiver.length,
                        )
                    if (
                        isinstance(receiver, _TextView)
                        and node.func.attr == "slice_bytes"
                        and len(arguments) == 2
                    ):
                        start, length = (
                            int(arguments[0]),
                            int(arguments[1]),
                        )
                        if (
                            start < 0
                            or start > receiver.length
                            or length < 0
                            or length > receiver.length - start
                        ):
                            raise NativeExecutionError(
                                "TextSliceOutOfBounds",
                                f"start={start} length={length}",
                            )
                        absolute_start = receiver.start + start
                        absolute_end = absolute_start + length
                        for offset in (
                            absolute_start,
                            absolute_end,
                        ):
                            if (
                                offset
                                not in {
                                    receiver.start,
                                    receiver.start
                                    + receiver.length,
                                }
                                and receiver.owner.data[offset] & 0xC0
                                == 0x80
                            ):
                                raise NativeExecutionError(
                                    "TextSliceNotOnUtf8Boundary",
                                    str(offset),
                                )
                        return _TextView(
                            receiver.owner,
                            absolute_start,
                            length,
                        )
                    if (
                        isinstance(receiver, _TextView)
                        and node.func.attr == "scalar_count"
                        and not arguments
                    ):
                        offset = 0
                        count = 0
                        while offset < receiver.length:
                            byte = receiver.owner.data[
                                receiver.start + offset
                            ]
                            if byte < 0x80:
                                offset += 1
                            elif byte < 0xE0:
                                offset += 2
                            elif byte < 0xF0:
                                offset += 3
                            else:
                                offset += 4
                            count += 1
                        return count
                    if (
                        isinstance(receiver, _TextView)
                        and node.func.attr == "scalar_width_at"
                        and len(arguments) == 1
                    ):
                        offset = int(arguments[0])
                        if offset < 0 or offset >= receiver.length:
                            raise NativeExecutionError(
                                "TextScalarOutOfBounds", str(offset)
                            )
                        byte = receiver.owner.data[
                            receiver.start + offset
                        ]
                        if byte < 0x80:
                            return 1
                        if byte < 0xE0:
                            return 2
                        if byte < 0xF0:
                            return 3
                        return 4
                    raise NativeExecutionError(
                        "DynamicCall",
                        f"unsupported {type(receiver).__name__} "
                        f"method {node.func.attr}",
                    )
                if (
                    receiver == "TextBuilder"
                    and node.func.attr
                    in {"new", "with_capacity_bytes"}
                ):
                    capacity = (
                        0
                        if node.func.attr == "new"
                        else int(arguments[0])
                    )
                    if capacity < 0 or capacity > (1 << 63) - 1:
                        raise NativeExecutionError(
                            "TextBuilderAllocationSizeOverflow",
                            f"invalid capacity {capacity}",
                        )
                    result = _TextBuilder(bytearray(), capacity)
                    self.metrics.bytes_builders.append(result)
                    if capacity:
                        self.metrics.allocations += 1
                    return result
                if isinstance(receiver, _TextBuilder):
                    receiver.check()
                    if (
                        node.func.attr == "len_bytes"
                        and not arguments
                    ):
                        return len(receiver.data)
                    if (
                        node.func.attr == "capacity_bytes"
                        and not arguments
                    ):
                        return receiver.capacity
                    if (
                        node.func.attr == "reserve_bytes"
                        and len(arguments) == 1
                    ):
                        _grow_builder(
                            receiver, int(arguments[0]), self.metrics
                        )
                        return None
                    if (
                        node.func.attr == "push_ascii"
                        and len(arguments) == 1
                    ):
                        scalar = int(arguments[0])
                        if not 0 <= scalar <= 0x7F:
                            raise NativeExecutionError(
                                "TextBuilderAsciiOutOfRange",
                                str(scalar),
                            )
                        self.metrics.required_append_bytes += 1
                        _grow_builder(receiver, 1, self.metrics)
                        receiver.data.append(scalar)
                        return None
                    if (
                        node.func.attr == "push_scalar"
                        and len(arguments) == 1
                    ):
                        scalar = int(arguments[0])
                        if (
                            scalar < 0
                            or scalar > 0x10FFFF
                            or 0xD800 <= scalar <= 0xDFFF
                        ):
                            raise NativeExecutionError(
                                "TextBuilderInvalidUnicodeScalar",
                                str(scalar),
                            )
                        payload = chr(scalar).encode("utf-8")
                        self.metrics.required_append_bytes += len(
                            payload
                        )
                        _grow_builder(
                            receiver, len(payload), self.metrics
                        )
                        receiver.data.extend(payload)
                        return None
                    if (
                        node.func.attr == "extend"
                        and len(arguments) == 1
                    ):
                        view = arguments[0]
                        if not isinstance(view, _TextView):
                            raise NativeExecutionError(
                                "InvalidTextBuilder", "extend"
                            )
                        view.check()
                        if view.owner is receiver:
                            raise NativeExecutionError(
                                "TextBuilderOverlappingExtend",
                                "extend overlaps builder storage",
                            )
                        self.metrics.required_append_bytes += (
                            view.length
                        )
                        _grow_builder(
                            receiver, view.length, self.metrics
                        )
                        receiver.data.extend(
                            view.owner.data[
                                view.start : view.start + view.length
                            ]
                        )
                        self.metrics.extend_copied_bytes += view.length
                        return None
                    if (
                        node.func.attr == "as_view"
                        and not arguments
                    ):
                        return _TextView(
                            receiver, 0, len(receiver.data)
                        )
                    if node.func.attr == "finish" and not arguments:
                        return _finish_text_builder(
                            receiver, self.metrics
                        )
                    raise NativeExecutionError(
                        "DynamicCall",
                        f"unsupported TextBuilder method "
                        f"{node.func.attr}",
                    )
                if (
                    receiver == "BytesBuilder"
                    and node.func.attr in {"new", "with_capacity"}
                ):
                    if node.func.attr == "new":
                        capacity = 0
                    else:
                        capacity = int(arguments[0])
                    if capacity < 0 or capacity > (1 << 63) - 1:
                        raise NativeExecutionError(
                            "BytesBuilderAllocationSizeOverflow",
                            f"invalid capacity {capacity}",
                        )
                    result = _BytesBuilder(bytearray(), capacity)
                    self.metrics.bytes_builders.append(result)
                    if capacity:
                        self.metrics.allocations += 1
                    return result
                if isinstance(receiver, _BytesBuilder):
                    receiver.check()
                    if node.func.attr == "len" and not arguments:
                        return len(receiver.data)
                    if node.func.attr == "capacity" and not arguments:
                        return receiver.capacity
                    if node.func.attr == "reserve" and len(arguments) == 1:
                        _grow_builder(
                            receiver, int(arguments[0]), self.metrics
                        )
                        return None
                    if node.func.attr == "push" and len(arguments) == 1:
                        byte = int(arguments[0])
                        if not 0 <= byte <= 255:
                            raise NativeExecutionError(
                                "BytesBuilderByteOutOfRange", str(byte)
                            )
                        _grow_builder(receiver, 1, self.metrics)
                        receiver.data.append(byte)
                        return None
                    if node.func.attr == "extend" and len(arguments) == 1:
                        view = arguments[0]
                        if not isinstance(view, _BytesView):
                            raise NativeExecutionError(
                                "InvalidBytesBuilder", "extend"
                            )
                        view.check()
                        if view.owner is receiver:
                            raise NativeExecutionError(
                                "BytesBuilderOverlappingExtend",
                                "extend overlaps builder storage",
                            )
                        _grow_builder(
                            receiver, view.length, self.metrics
                        )
                        receiver.data.extend(
                            view.owner.data[
                                view.start : view.start + view.length
                            ]
                        )
                        self.metrics.extend_copied_bytes += view.length
                        return None
                    if node.func.attr == "as_view" and not arguments:
                        return _BytesView(
                            receiver, 0, len(receiver.data)
                        )
                    if node.func.attr == "finish" and not arguments:
                        return _finish_builder(receiver, self.metrics)
                    raise NativeExecutionError(
                        "DynamicCall",
                        f"unsupported BytesBuilder method "
                        f"{node.func.attr}",
                    )
                if node.func.attr == "len" and isinstance(
                    receiver, (_Bytes, _BytesView)
                ):
                    receiver.check()
                    return (
                        receiver.length
                        if isinstance(receiver, _BytesView)
                        else len(receiver.data)
                    )
                if node.func.attr == "slice" and isinstance(
                    receiver, (_Bytes, _BytesView)
                ):
                    receiver.check()
                    start, length = (int(item) for item in arguments)
                    owner_length = (
                        len(receiver.data)
                        if isinstance(receiver, _Bytes)
                        else receiver.length
                    )
                    if (
                        start < 0
                        or start > owner_length
                        or length < 0
                        or length > owner_length - start
                    ):
                        raise NativeExecutionError(
                            "BytesSliceOutOfBounds",
                            f"start={start} length={length}",
                        )
                    if isinstance(receiver, _BytesView):
                        return _BytesView(
                            receiver.owner,
                            receiver.start + start,
                            length,
                        )
                    return _BytesView(receiver, start, length)
                raise NativeExecutionError(
                    "DynamicCall", ast.dump(node.func)
                )
            if not isinstance(node.func, ast.Name):
                raise NativeExecutionError("DynamicCall", ast.dump(node.func))
            arguments = [self._expression(item, scope) for item in node.args]
            return self._call(node.func.id, arguments, node, scope)
        raise NativeExecutionError("UnsupportedHIR", type(node).__name__)

    def _assign_target(self, target: ast.AST, value: Any, scope: _Scope) -> None:
        if isinstance(target, ast.Name):
            scope.assign(target.id, value)
            return
        if isinstance(target, ast.Subscript):
            collection = self._expression(target.value, scope)
            index = int(self._expression(target.slice, scope))
            collection.check()
            if isinstance(collection, _BytesView):
                raise NativeExecutionError(
                    "BytesViewMutation", "cannot mutate a BytesView"
                )
            data = collection.data
            if index < 0 or index >= len(data):
                kind = (
                    "BytesIndexOutOfBounds"
                    if isinstance(collection, _Bytes)
                    else "BoundsError"
                )
                raise NativeExecutionError(kind, str(index))
            data[index] = (
                int(value) & 255
                if isinstance(collection, _Bytes)
                else value
            )
            return
        raise NativeExecutionError("InvalidMutation", ast.dump(target))

    def _statement(self, node: ast.stmt, scope: _Scope, *, tail: bool) -> None:
        self._step()
        if isinstance(node, ast.AnnAssign):
            if not isinstance(node.target, ast.Name) or node.value is None:
                raise NativeExecutionError("InvalidDeclaration", ast.dump(node))
            kind = self.declaration_kinds.get((node.lineno, node.target.id))
            value = self._expression(node.value, scope)
            if (
                isinstance(value, _Collection)
                and isinstance(node.annotation, ast.Subscript)
                and isinstance(node.annotation.value, ast.Name)
                and node.annotation.value.id == "Shared"
            ):
                value.shared = True
                value.ownership = "SharedRc"
            scope.define(
                node.target.id,
                value,
                kind == "var",
                ast.unparse(node.annotation),
            )
            return
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1:
                raise NativeExecutionError("InvalidMutation", ast.dump(node))
            self._assign_target(node.targets[0], self._expression(node.value, scope), scope)
            return
        if isinstance(node, ast.Return):
            raise _Return(self._expression(node.value, scope) if node.value else None)
        if isinstance(node, ast.Expr):
            value = self._expression(node.value, scope)
            if tail:
                raise _Return(value)
            return
        if isinstance(node, ast.If):
            body = node.body if self._expression(node.test, scope) else node.orelse
            self._statements(body, _Scope(scope), tail_returns=tail)
            return
        if isinstance(node, ast.While):
            while self._expression(node.test, scope):
                child = _Scope(scope)
                try:
                    self._statements(
                        node.body, child, tail_returns=False
                    )
                except _Return as returned:
                    self._finish_child_scope(child, returned.value)
                    raise
                else:
                    self._finish_child_scope(child)
            return
        if isinstance(node, ast.For):
            if not isinstance(node.target, ast.Name) or not isinstance(node.iter, ast.Call):
                raise NativeExecutionError("InvalidFor", ast.dump(node))
            start = int(self._expression(node.iter.args[0], scope))
            end = int(self._expression(node.iter.args[1], scope))
            for value in range(start, end):
                child = _Scope(scope)
                iterator_type = self._numeric_type(node.iter, scope)
                child.define(
                    node.target.id, value, False, iterator_type
                )
                try:
                    self._statements(
                        node.body, child, tail_returns=False
                    )
                except _Return as returned:
                    self._finish_child_scope(child, returned.value)
                    raise
                else:
                    self._finish_child_scope(child)
            return
        if isinstance(node, ast.Match):
            subject = self._expression(node.subject, scope)
            if isinstance(subject, _Utf8Decode):
                subject.check()
                wanted = "Valid" if subject.valid else "Invalid"
                for case in node.cases:
                    pattern = case.pattern
                    if (
                        not isinstance(pattern, ast.MatchClass)
                        or not isinstance(pattern.cls, ast.Name)
                        or pattern.cls.id != wanted
                        or len(pattern.patterns) != 1
                        or not isinstance(
                            pattern.patterns[0], ast.MatchAs
                        )
                        or pattern.patterns[0].name is None
                    ):
                        continue
                    payload = (
                        subject.text
                        if subject.valid
                        else subject.error_offset
                    )
                    if payload is None:
                        raise NativeExecutionError(
                            "InvalidUtf8DecodeArm", wanted
                        )
                    subject.consumed = True
                    child = _Scope(scope)
                    child.define(
                        str(pattern.patterns[0].name),
                        payload,
                        False,
                        "Text" if subject.valid else "UInt64",
                    )
                    try:
                        self._statements(
                            case.body,
                            child,
                            tail_returns=tail,
                        )
                    except _Return as returned:
                        if (
                            isinstance(payload, _Text)
                            and payload.alive
                            and returned.value is not payload
                        ):
                            self._drop(payload)
                        raise
                    if isinstance(payload, _Text) and payload.alive:
                        self._drop(payload)
                    return
                raise NativeExecutionError(
                    "NonExhaustiveMatch", wanted
                )
            for case in node.cases:
                matched = False
                if isinstance(case.pattern, ast.MatchAs) and case.pattern.name is None:
                    matched = True
                elif isinstance(case.pattern, ast.MatchValue):
                    matched = subject == self._expression(case.pattern.value, scope)
                elif isinstance(case.pattern, ast.MatchSingleton):
                    matched = subject is case.pattern.value
                if matched:
                    self._statements(case.body, _Scope(scope), tail_returns=tail)
                    return
            raise NativeExecutionError("NonExhaustiveMatch", str(subject))
        if isinstance(node, ast.Pass):
            return
        raise NativeExecutionError("UnsupportedHIR", type(node).__name__)

    def _statements(self, nodes: list[ast.stmt], scope: _Scope, *, tail_returns: bool) -> None:
        for index, node in enumerate(nodes):
            self._statement(node, scope, tail=tail_returns and index == len(nodes) - 1)

    def run(self, arguments: Iterable[Any] = ()) -> ExecutionObservation:
        try:
            result = self._invoke(self.program.entry_function, list(arguments))
        except NativeExecutionError as exc:
            return ExecutionObservation(
                "ERROR",
                error_kind=exc.kind,
                error_offset=exc.offset,
                allocations=self.metrics.allocations,
                drops=self.metrics.drops,
                retains=self.metrics.retains,
                releases=self.metrics.releases,
                frees=self.metrics.frees,
                reallocations=self.metrics.reallocations,
                growth_copied_bytes=self.metrics.growth_copied_bytes,
                extend_copied_bytes=self.metrics.extend_copied_bytes,
                finish_copies=self.metrics.finish_copies,
                final_ownership_state=self.metrics.ownership(),
                required_append_bytes=(
                    self.metrics.required_append_bytes
                ),
                steps=self.metrics.steps,
            )
        return_annotation = self.functions[self.program.entry_function].returns
        return_type = (
            ast.unparse(return_annotation)
            if return_annotation is not None
            else None
        )
        printed_checksum = (
            int(result) & _MASK64
            if isinstance(result, (int, bool)) and return_type == "UInt64"
            else int(result)
            if isinstance(result, (int, bool))
            else None
        )
        return ExecutionObservation(
            "OK",
            result,
            printed_checksum,
            allocations=self.metrics.allocations,
            drops=self.metrics.drops,
            retains=self.metrics.retains,
            releases=self.metrics.releases,
            frees=self.metrics.frees,
            reallocations=self.metrics.reallocations,
            growth_copied_bytes=self.metrics.growth_copied_bytes,
            extend_copied_bytes=self.metrics.extend_copied_bytes,
            finish_copies=self.metrics.finish_copies,
            final_ownership_state=self.metrics.ownership(),
            required_append_bytes=self.metrics.required_append_bytes,
            steps=self.metrics.steps,
        )


def evaluate_surface(source: str, arguments: Iterable[Any] = (), *, path: str = "main.mlo") -> ExecutionObservation:
    program = compile_native_hir(source, path=path)
    return HIREvaluator(program).run(arguments)


def evaluate_hir(program: NativeHIRProgram, arguments: Iterable[Any] = ()) -> ExecutionObservation:
    return HIREvaluator(program).run(arguments)


def evaluate_mir(mir: PerformanceMIR, arguments: Iterable[Any] = ()) -> ExecutionObservation:
    return MIRInterpreter(mir).run(arguments)


def _instrument_extended_metrics(source: str) -> str:
    source = source.replace(
        "static uint64_t meldra_heap_allocations = 0;",
        "static uint64_t meldra_heap_allocations = 0;\nstatic uint64_t meldra_drop_count = 0;",
    )
    pattern = re.compile(
        r"(?m)^(\s+)(meldra_[A-Za-z0-9_]+)\.data = NULL; \2\.refcount = NULL; \2\.length = 0;$"
    )
    source = pattern.sub(
        r"\1++meldra_drop_count;\n\1\2.data = NULL; \2.refcount = NULL; \2.length = 0;",
        source,
    )
    source = source.replace(
        '    fprintf(stderr, "MELDRA_ALLOCATIONS=%" PRIu64 "\\n", meldra_heap_allocations);',
        '    fprintf(stderr, "MELDRA_ALLOCATIONS=%" PRIu64 "\\nMELDRA_DROPS=%" PRIu64 "\\n", meldra_heap_allocations, meldra_drop_count);',
    )
    return source


def evaluate_native(
    mir: PerformanceMIR,
    arguments: Iterable[Any] = (),
    *,
    output_dir: str | Path | None = None,
    stem: str = "differential",
) -> ExecutionObservation:
    argument_values = tuple(arguments)
    temporary = None
    if output_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="meldra-differential-")
        output_dir = temporary.name
    source = _instrument_extended_metrics(CEmitter(mir, runtime_arguments=True).emit())
    build = compile_c_source(source, output_dir=output_dir, stem=stem)
    if build.status != "MEASURED" or build.binary_path is None:
        if temporary:
            temporary.cleanup()
        return ExecutionObservation("ERROR", error_kind="NativeBuildFailure")
    completed = subprocess.run(
        (
            build.binary_path,
            *(
                item if isinstance(item, (str, bytes)) else str(item)
                for item in argument_values
            ),
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=dict(os.environ, LC_ALL="C", TZ="UTC"),
    )
    allocations = re.findall(r"MELDRA_ALLOCATIONS=(\d+)", completed.stderr)
    drops = re.findall(r"MELDRA_DROPS=(\d+)", completed.stderr)
    frees = re.findall(r"MELDRA_FREES=(\d+)", completed.stderr)
    reallocations = re.findall(
        r"MELDRA_BUILDER_REALLOCATIONS=(\d+)", completed.stderr
    )
    growth_copies = re.findall(
        r"MELDRA_BUILDER_GROWTH_COPIED_BYTES=(\d+)",
        completed.stderr,
    )
    extend_copies = re.findall(
        r"MELDRA_BUILDER_EXTEND_COPIED_BYTES=(\d+)",
        completed.stderr,
    )
    finish_copies = re.findall(
        r"MELDRA_BUILDER_FINISH_COPIES=(\d+)", completed.stderr
    )
    required_append_bytes = re.findall(
        r"MELDRA_TEXT_BUILDER_REQUIRED_APPEND_BYTES=(\d+)",
        completed.stderr,
    )
    error_offsets = re.findall(r"offset=(\d+)", completed.stderr)
    if completed.returncode != 0:
        diagnostic_kinds = (
            "TextBuilderAsciiOutOfRange",
            "TextBuilderInvalidUnicodeScalar",
            "TextBuilderLengthOverflow",
            "TextBuilderCapacityOverflow",
            "TextBuilderAllocationSizeOverflow",
            "TextBuilderOverlappingExtend",
            "TextBuilderActiveView",
            "JsonInvalidUtf8",
            "JsonTruncatedInput",
            "JsonUnexpectedToken",
            "JsonInvalidStringControl",
            "JsonInvalidEscape",
            "JsonInvalidUnicodeEscape",
            "JsonUnfinishedString",
            "JsonMalformedNumber",
            "JsonNestingDepthExceeded",
            "JsonDelimiterMismatch",
            "JsonExpectedComma",
            "JsonExpectedObjectKey",
            "JsonExpectedColon",
        )
        error_kind = next(
            (
                kind
                for kind in diagnostic_kinds
                if kind in completed.stderr
            ),
            None,
        )
        if error_kind is None and "Meldra bounds failure" in completed.stderr:
            error_kind = "BoundsError"
        elif (
            error_kind is None
            and "Meldra division by zero" in completed.stderr
        ):
            error_kind = "DivisionByZero"
        elif error_kind is None:
            error_kind = "NativeRuntimeFailure"
        observation = ExecutionObservation(
            "ERROR",
            error_kind=error_kind,
            error_offset=(
                int(error_offsets[-1]) if error_offsets else None
            ),
            allocations=int(allocations[-1]) if allocations else 0,
            drops=int(drops[-1]) if drops else 0,
            frees=int(frees[-1]) if frees else 0,
            reallocations=(
                int(reallocations[-1]) if reallocations else 0
            ),
            growth_copied_bytes=(
                int(growth_copies[-1]) if growth_copies else 0
            ),
            extend_copied_bytes=(
                int(extend_copies[-1]) if extend_copies else 0
            ),
            finish_copies=(
                int(finish_copies[-1]) if finish_copies else 0
            ),
            required_append_bytes=(
                int(required_append_bytes[-1])
                if required_append_bytes
                else 0
            ),
        )
    else:
        return_type = mir.function(mir.entry_function).return_type
        output = completed.stdout.strip().splitlines()
        try:
            if return_type.kind == "float":
                return_value = float(output[-1])
                checksum: int | float | None = None
            else:
                parsed = int(output[-1])
                return_value = parsed
                checksum = parsed
                if return_type.kind == "int" and parsed >= (1 << 63):
                    return_value = parsed - (1 << 64)
                elif return_type.kind == "bool":
                    return_value = bool(parsed)
        except (IndexError, ValueError):
            observation = ExecutionObservation(
                "ERROR", error_kind="NativeOutputFailure"
            )
        else:
            observation = ExecutionObservation(
                "OK",
                return_value,
                checksum,
                allocations=(
                    int(allocations[-1]) if allocations else 0
                ),
                drops=int(drops[-1]) if drops else 0,
                frees=int(frees[-1]) if frees else 0,
                reallocations=(
                    int(reallocations[-1]) if reallocations else 0
                ),
                growth_copied_bytes=(
                    int(growth_copies[-1]) if growth_copies else 0
                ),
                extend_copied_bytes=(
                    int(extend_copies[-1]) if extend_copies else 0
                ),
                finish_copies=(
                    int(finish_copies[-1]) if finish_copies else 0
                ),
                required_append_bytes=(
                    int(required_append_bytes[-1])
                    if required_append_bytes
                    else 0
                ),
            )
    if temporary:
        temporary.cleanup()
    return observation


@dataclass(frozen=True)
class DifferentialResult:
    source_sha256: str
    arguments: tuple[Any, ...]
    observations: tuple[tuple[str, ExecutionObservation], ...]
    pass_digests: tuple[tuple[str, str, str], ...]
    mismatches: tuple[dict[str, Any], ...]

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_sha256": self.source_sha256,
            "arguments": list(self.arguments),
            "observations": {
                name: observation.to_dict() for name, observation in self.observations
            },
            "pass_digests": [
                {"pass": name, "before": before, "after": after}
                for name, before, after in self.pass_digests
            ],
            "mismatches": list(self.mismatches),
            "ok": self.ok,
        }


def _observed_values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, float) or isinstance(right, float):
        if not isinstance(left, float) or not isinstance(right, float):
            return False
        if math.isnan(left) or math.isnan(right):
            return math.isnan(left) and math.isnan(right)
        return left.hex() == right.hex()
    return left == right


def run_differential(
    source: str,
    arguments: Iterable[Any] = (),
    *,
    path: str = "main.mlo",
    include_native: bool = True,
    artifact_dir: str | Path | None = None,
) -> DifferentialResult:
    argument_values = tuple(arguments)
    hir = compile_native_hir(source, path=path)
    unoptimized = lower_native_hir_to_performance(hir)
    observations: list[tuple[str, ExecutionObservation]] = [
        ("surface", evaluate_surface(source, argument_values, path=path)),
        ("hir", evaluate_hir(hir, argument_values)),
        ("mir_unoptimized", evaluate_mir(unoptimized, argument_values)),
    ]
    current = unoptimized
    pass_digests = []
    for pass_function in OPTIMIZATION_PIPELINE:
        before = current
        current, _statistics = pass_function(before)
        pass_digests.append((pass_function.__name__, before.digest, current.digest))
        observations.append(
            (f"after_{pass_function.__name__}", evaluate_mir(current, argument_values))
        )
    observations.append(("mir_optimized", evaluate_mir(current, argument_values)))
    if include_native:
        observations.append(
            (
                "native",
                evaluate_native(
                    current,
                    argument_values,
                    output_dir=artifact_dir,
                    stem="differential_native",
                ),
            )
        )
    mismatches = []
    baseline_name, baseline = observations[0]
    for name, observation in observations[1:]:
        compared_fields = (
            "status",
            "return_value",
            "printed_checksum",
            "error_kind",
            "error_offset",
        )
        differences = {
            field_name: {
                "baseline": getattr(baseline, field_name),
                "observed": getattr(observation, field_name),
            }
            for field_name in compared_fields
            if not _observed_values_equal(
                getattr(baseline, field_name),
                getattr(observation, field_name),
            )
        }
        if differences:
            mismatches.append(
                {
                    "baseline": baseline_name,
                    "level": name,
                    "differences": differences,
                }
            )
    return DifferentialResult(
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
        argument_values,
        tuple(observations),
        tuple(pass_digests),
        tuple(mismatches),
    )


__all__ = [
    "NATIVE_DIFFERENTIAL_SCHEMA_VERSION",
    "DifferentialResult",
    "ExecutionObservation",
    "HIREvaluator",
    "MIRInterpreter",
    "NativeExecutionError",
    "evaluate_hir",
    "evaluate_mir",
    "evaluate_native",
    "evaluate_surface",
    "run_differential",
]
