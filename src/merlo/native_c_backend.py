"""Portable C11 backend and Clang/GCC -O3 bootstrap compiler for Stage 0.5P."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from merlo.json_streaming_c import json_streaming_c_source
from merlo.performance_mir import (
    MIRFunction,
    MIRInstruction,
    PerformanceMIR,
    PerformanceType,
)


NATIVE_BACKEND_SCHEMA_VERSION = 1
NATIVE_BACKEND_IMPLEMENTATION_VERSION = 10


class NativeBackendError(RuntimeError):
    pass


def _identifier(value: str) -> str:
    raw = value.removeprefix("%")
    output = []
    for character in raw:
        output.append(character if character.isalnum() or character == "_" else "_")
    return "meldra_" + "".join(output)


def _type_key(type_: PerformanceType) -> str:
    if type_.kind in {"array", "slice"}:
        return "slice_" + _type_key(type_.element)
    if type_.kind == "record":
        return "record_" + (type_.record or "unknown")
    return type_.name.lower()


def _ctype(type_: PerformanceType) -> str:
    if type_.kind == "int":
        return "int64_t" if type_.bits == 64 else "int32_t"
    if type_.kind == "uint":
        return "uint64_t" if type_.bits == 64 else "uint32_t"
    if type_.kind == "float":
        return "double" if type_.bits == 64 else "float"
    if type_.kind == "bool":
        return "bool"
    if type_.kind == "unit":
        return "void"
    if type_.kind == "record" and type_.record == "Bytes":
        return "meldra_bytes"
    if type_.kind == "record" and type_.record == "BytesView":
        return "meldra_bytes_view"
    if type_.kind == "record" and type_.record == "BytesBuilder":
        return "meldra_bytes_builder"
    if type_.kind == "record" and type_.record == "TextBuilder":
        return "meldra_bytes_builder"
    if type_.kind == "record" and type_.record == "Text":
        return "meldra_text"
    if type_.kind == "record" and type_.record == "TextView":
        return "meldra_text_view"
    if type_.kind == "record" and type_.record == "Utf8Decode":
        return "meldra_utf8_decode"
    if type_.kind == "record":
        return "meldra_record_" + (type_.record or "unknown")
    if type_.kind in {"array", "slice"}:
        return "meldra_" + _type_key(type_)
    raise NativeBackendError(f"unsupported C type: {type_.name}")


def _literal(value: Any, type_: PerformanceType) -> str:
    if type_.kind == "bool":
        return "true" if bool(value) else "false"
    if type_.kind == "float":
        try:
            float_value = float(value)
        except OverflowError:
            float_value = math.copysign(math.inf, int(value))
        if math.isnan(float_value):
            return "NAN"
        if math.isinf(float_value) or (
            type_.bits == 32 and abs(float_value) > 3.4028234663852886e38
        ):
            return (
                "-INFINITY"
                if math.copysign(1.0, float_value) < 0
                else "INFINITY"
            )
        return repr(float_value) + ("f" if type_.bits == 32 else "")
    if type_.kind in {"int", "uint"}:
        bits = type_.bits or 64
        raw = int(value) & ((1 << bits) - 1)
        if type_.kind == "uint":
            macro = "UINT64_C" if bits == 64 else "UINT32_C"
            return f"{macro}({raw})"
        signed = raw - (1 << bits) if raw >= (1 << (bits - 1)) else raw
        macro = "INT64_C" if bits == 64 else "INT32_C"
        minimum = -(1 << (bits - 1))
        if signed == minimum:
            maximum = (1 << (bits - 1)) - 1
            return f"(-{macro}({maximum}) - {macro}(1))"
        return f"{macro}({signed})"
    raise NativeBackendError(f"unsupported C literal: {value!r}")


def _operator(value: str) -> str:
    operators = {
        "add": "+",
        "sub": "-",
        "mul": "*",
        "div": "/",
        "mod": "%",
        "bit_and": "&",
        "bit_or": "|",
        "bit_xor": "^",
        "shift_left": "<<",
        "shift_right": ">>",
        "and": "&&",
        "or": "||",
        "eq": "==",
        "ne": "!=",
        "lt": "<",
        "le": "<=",
        "gt": ">",
        "ge": ">=",
    }
    try:
        return operators[value]
    except KeyError as exc:
        raise NativeBackendError(f"unsupported C operator: {value}") from exc


def _collect_types(mir: PerformanceMIR) -> tuple[PerformanceType, ...]:
    found: dict[str, PerformanceType] = {}

    def add(type_: PerformanceType | None) -> None:
        if type_ is None:
            return
        found[type_.name] = type_
        if type_.element is not None:
            add(type_.element)

    for record in mir.records:
        for _name, type_ in record.fields:
            add(type_)
    for function in mir.functions:
        add(function.return_type)
        for parameter in function.parameters:
            add(parameter.type)
        for block in function.blocks:
            for instruction in block.instructions:
                add(instruction.type)
    return tuple(found[name] for name in sorted(found))


class CEmitter:
    def __init__(
        self,
        mir: PerformanceMIR,
        *,
        entry_arguments: Iterable[int | float | bool] = (),
        executable: bool = True,
        runtime_arguments: bool = False,
    ) -> None:
        self.mir = mir
        self.entry_arguments = tuple(entry_arguments)
        self.executable = executable
        self.runtime_arguments = runtime_arguments
        self.value_types: dict[str, PerformanceType] = {}
        self.function_map = {item.name: item for item in mir.functions}
        for function in mir.functions:
            for parameter in function.parameters:
                self.value_types[parameter.value] = parameter.type
            for block in function.blocks:
                for instruction in block.instructions:
                    if instruction.result is not None and instruction.type is not None:
                        self.value_types[instruction.result] = instruction.type

    def _function_name(self, name: str) -> str:
        return "meldra_fn_" + name

    def _value(self, value: str) -> str:
        return _identifier(value)

    def _mapping(self, instruction: MIRInstruction) -> str:
        if instruction.source is None:
            return ""
        source = instruction.source
        return f"    /* {source.path}:{source.line}:{source.column} {instruction.id} */\n"

    def _allocation(self, instruction: MIRInstruction) -> list[str]:
        if instruction.type is None or instruction.result is None:
            raise NativeBackendError("allocation requires a typed result")
        target = self._value(instruction.result)
        type_ = instruction.type
        if instruction.op == "alloc_local":
            return [f"    {_ctype(type_)} {target};"]
        if type_.kind not in {"array", "slice"}:
            return [f"    {_ctype(type_)} {target};"]
        element = _ctype(type_.element)
        fixed_length = type_.length or 0
        if instruction.op in {"alloc_stack", "alloc_region"}:
            capacity = max(1, fixed_length)
            storage = target + "_storage"
            return [
                f"    {element} {storage}[{capacity}];",
                f"    {_ctype(type_)} {target} = {{ {storage}, UINT64_C({fixed_length}), false, NULL }};",
            ]
        capacity = fixed_length
        return [
            f"    {_ctype(type_)} {target} = {{ NULL, UINT64_C({capacity}), true, NULL }};",
            f"    if ({capacity} > 0) {{",
            f"        {target}.data = ({element} *)malloc(sizeof({element}) * {capacity});",
            f"        {target}.refcount = (uint64_t *)malloc(sizeof(uint64_t));",
            f"        if ({target}.data == NULL || {target}.refcount == NULL) meldra_panic_alloc();",
            f"        *{target}.refcount = UINT64_C(1);",
            "        ++meldra_heap_allocations;",
            "    }",
        ]

    def _instruction(self, instruction: MIRInstruction) -> list[str]:
        attributes = instruction.attribute_map
        operands = [self._value(item) for item in instruction.operands]
        result = self._value(instruction.result) if instruction.result else None
        ctype = _ctype(instruction.type) if instruction.type else None
        lines = []
        mapping = self._mapping(instruction)
        if mapping:
            lines.append(mapping.rstrip("\n"))
        if instruction.op in {"alloc_local", "alloc_stack", "alloc_region", "alloc_heap"}:
            lines.extend(self._allocation(instruction))
        elif instruction.op == "bytes_new":
            length = operands[0]
            lines.extend(
                [
                    f"    if ({length} > UINT64_C(9223372036854775807)) meldra_panic_bytes_allocation_overflow({length});",
                    f"    meldra_bytes {result} = {{ NULL, {length}, {length}, true }};",
                    f"    if ({length} > 0) {{",
                    f"        {result}.data = (uint8_t *)malloc((size_t){length});",
                    f"        if ({result}.data == NULL) meldra_panic_alloc();",
                    "        ++meldra_heap_allocations;",
                    f"        meldra_allocated_bytes += {length};",
                    "    }",
                ]
            )
        elif instruction.op == "bytes_len":
            lines.append(f"    uint64_t {result} = {operands[0]}.length;")
        elif instruction.op == "bytes_bounds_check":
            lines.extend(
                [
                    "    ++meldra_bounds_checks;",
                    f"    if ({operands[0]} >= {operands[1]}) meldra_panic_bytes_bounds({operands[0]}, {operands[1]});",
                ]
            )
        elif instruction.op == "bytes_load":
            lines.append(
                f"    uint64_t {result} = (uint64_t){operands[0]}.data[{operands[1]}];"
            )
        elif instruction.op == "bytes_store":
            lines.append(
                f"    {operands[0]}.data[{operands[1]}] = (uint8_t)({operands[2]} & UINT64_C(255));"
            )
        elif instruction.op == "bytes_slice":
            owner, start, length = operands
            lines.extend(
                [
                    f"    if ({start} > {owner}.length || {length} > {owner}.length - {start}) meldra_panic_bytes_slice({start}, {length}, {owner}.length);",
                    f"    meldra_bytes_view {result} = {{ {owner}.data == NULL ? NULL : {owner}.data + {start}, {length} }};",
                ]
            )
        elif instruction.op == "json_token_checksum":
            lines.append(
                f"    uint64_t {result} = meldra_json_token_checksum("
                f"{operands[0]}.data, {operands[0]}.length);"
            )
        elif instruction.op == "bytes_to_text_transfer":
            lines.append(
                "    /* Bytes -> Text ownership transfer; "
                "payload copy count is zero. */"
            )
        elif instruction.op == "utf8_validate":
            source = operands[0]
            error = f"meldra_utf8_error_{instruction.id}"
            lines.extend(
                [
                    f"    uint64_t {error} = UINT64_C(0);",
                    f"    bool meldra_utf8_valid_{instruction.id} = "
                    f"meldra_utf8_validate({source}.data, "
                    f"{source}.length, &{error});",
                    f"    meldra_utf8_decode {result} = {{0}};",
                    f"    {result}.valid = "
                    f"meldra_utf8_valid_{instruction.id};",
                    f"    {result}.error_offset = {error};",
                    f"    if ({result}.valid) {{",
                    f"        {result}.text = (meldra_text){{ "
                    f"{source}.data, {source}.length, "
                    f"{source}.capacity, true }};",
                    "    } else {",
                    f"        if ({source}.data != NULL) {{ "
                    f"free({source}.data); ++meldra_heap_frees; }}",
                    "    }",
                    f"    {source}.data = NULL;",
                    f"    {source}.length = 0;",
                    f"    {source}.capacity = 0;",
                    f"    {source}.live = false;",
                ]
            )
        elif instruction.op == "utf8_decode_is_valid":
            lines.extend(
                [
                    f"    if ({operands[0]}.consumed) "
                    "meldra_panic_utf8_decode_state();",
                    f"    bool {result} = {operands[0]}.valid;",
                ]
            )
        elif instruction.op == "utf8_decode_take_text":
            decoded = operands[0]
            lines.extend(
                [
                    f"    if ({decoded}.consumed || !{decoded}.valid) "
                    "meldra_panic_utf8_decode_state();",
                    f"    meldra_text {result} = {decoded}.text;",
                    f"    {decoded}.text = (meldra_text){{0}};",
                    f"    {decoded}.consumed = true;",
                ]
            )
        elif instruction.op == "utf8_decode_error_offset":
            decoded = operands[0]
            lines.extend(
                [
                    f"    if ({decoded}.consumed || {decoded}.valid) "
                    "meldra_panic_utf8_decode_state();",
                    f"    uint64_t {result} = "
                    f"{decoded}.error_offset;",
                ]
            )
        elif instruction.op == "utf8_decode_drop":
            decoded = operands[0]
            lines.extend(
                [
                    f"    if ({decoded}.consumed) "
                    "meldra_panic_utf8_decode_state();",
                    f"    if ({decoded}.valid && "
                    f"{decoded}.text.data != NULL) {{",
                    f"        free({decoded}.text.data);",
                    "        ++meldra_heap_frees;",
                    "    }",
                    f"    {decoded}.text = (meldra_text){{0}};",
                    f"    {decoded}.consumed = true;",
                ]
            )
        elif instruction.op == "text_from_ascii":
            scalar = operands[0]
            lines.extend(
                [
                    f"    if ({scalar} > UINT64_C(127)) "
                    f"meldra_panic_ascii({scalar});",
                    f"    meldra_text {result} = "
                    f"meldra_text_from_scalar({scalar});",
                ]
            )
        elif instruction.op == "text_from_scalar":
            lines.append(
                f"    meldra_text {result} = "
                f"meldra_text_from_scalar({operands[0]});"
            )
        elif instruction.op == "text_from_surrogate":
            high, low = operands
            scalar = f"meldra_scalar_{instruction.id}"
            lines.extend(
                [
                    f"    if ({high} < UINT64_C(0xD800) || "
                    f"{high} > UINT64_C(0xDBFF) || "
                    f"{low} < UINT64_C(0xDC00) || "
                    f"{low} > UINT64_C(0xDFFF)) "
                    "meldra_panic_surrogate();",
                    f"    uint64_t {scalar} = UINT64_C(0x10000) "
                    f"+ (({high} - UINT64_C(0xD800)) << 10) "
                    f"+ ({low} - UINT64_C(0xDC00));",
                    f"    meldra_text {result} = "
                    f"meldra_text_from_scalar({scalar});",
                ]
            )
        elif instruction.op == "text_len_bytes":
            lines.append(
                f"    uint64_t {result} = {operands[0]}.length;"
            )
        elif instruction.op == "text_view":
            lines.append(
                f"    meldra_text_view {result} = {{ "
                f"{operands[0]}.data, {operands[0]}.length }};"
            )
        elif instruction.op == "utf8_boundary_check":
            view, start, length = operands
            lines.append(
                f"    meldra_utf8_boundary_check({view}.data, "
                f"{view}.length, {start}, {length});"
            )
        elif instruction.op == "text_slice":
            view, start, length = operands
            lines.append(
                f"    meldra_text_view {result} = {{ "
                f"{view}.data == NULL ? NULL : {view}.data + {start}, "
                f"{length} }};"
            )
        elif instruction.op == "text_view_as_bytes":
            lines.append(
                f"    meldra_bytes_view {result} = {{ "
                f"{operands[0]}.data, {operands[0]}.length }};"
            )
        elif instruction.op == "utf8_scalar_count":
            view = operands[0]
            index = f"meldra_scalar_index_{instruction.id}"
            lines.extend(
                [
                    f"    uint64_t {result} = UINT64_C(0);",
                    f"    uint64_t {index} = UINT64_C(0);",
                    f"    while ({index} < {view}.length) {{",
                    f"        {index} += meldra_utf8_scalar_width("
                    f"{view}.data, {view}.length, {index});",
                    f"        ++{result};",
                    "    }",
                ]
            )
        elif instruction.op == "utf8_scalar_next":
            lines.append(
                f"    uint64_t {result} = meldra_utf8_scalar_width("
                f"{operands[0]}.data, {operands[0]}.length, "
                f"{operands[1]});"
            )
        elif instruction.op == "text_to_bytes_transfer":
            text = operands[0]
            lines.extend(
                [
                    f"    meldra_bytes {result} = {{ {text}.data, "
                    f"{text}.length, {text}.capacity, true }};",
                    f"    {text}.data = NULL;",
                    f"    {text}.length = 0;",
                    f"    {text}.capacity = 0;",
                    f"    {text}.live = false;",
                ]
            )
        elif instruction.op == "text_drop":
            text = operands[0]
            lines.extend(
                [
                    f"    if (!{text}.live) "
                    "meldra_panic_text_double_drop();",
                    f"    if ({text}.data != NULL) {{ "
                    f"free({text}.data); ++meldra_heap_frees; }}",
                    f"    {text}.data = NULL;",
                    f"    {text}.length = 0;",
                    f"    {text}.capacity = 0;",
                    f"    {text}.live = false;",
                ]
            )
        elif instruction.op == "text_builder_create":
            capacity = operands[0]
            lines.extend(
                [
                    f"    if ({capacity} > UINT64_C(9223372036854775807)) meldra_panic_text_builder_allocation_size_overflow();",
                    f"    meldra_bytes_builder {result} = {{ NULL, UINT64_C(0), {capacity}, UINT64_C(0), UINT8_C(1) }};",
                    f"    if ({capacity} > 0) {{",
                    f"        {result}.data = (uint8_t *)malloc((size_t){capacity});",
                    f"        if ({result}.data == NULL) meldra_panic_alloc();",
                    "        ++meldra_heap_allocations;",
                    f"        meldra_allocated_bytes += {capacity};",
                    "    }",
                ]
            )
        elif instruction.op == "text_builder_append_account":
            lines.append(
                f"    meldra_text_builder_required_append_bytes += {operands[0]};"
            )
        elif instruction.op == "text_builder_scalar_width":
            scalar = operands[0]
            lines.extend(
                [
                    f"    if ({scalar} > UINT64_C(0x10FFFF) || ({scalar} >= UINT64_C(0xD800) && {scalar} <= UINT64_C(0xDFFF))) meldra_panic_text_builder_scalar({scalar});",
                    f"    uint64_t {result} = {scalar} <= UINT64_C(0x7F) ? UINT64_C(1) : ({scalar} <= UINT64_C(0x7FF) ? UINT64_C(2) : ({scalar} <= UINT64_C(0xFFFF) ? UINT64_C(3) : UINT64_C(4)));",
                ]
            )
        elif instruction.op == "text_builder_push_ascii":
            builder, scalar = operands
            lines.extend(
                [
                    f"    if ({builder}.state != UINT8_C(1)) meldra_panic_builder_state();",
                    f"    if ({builder}.active_views != 0) meldra_panic_builder_active_view();",
                    f"    if ({scalar} > UINT64_C(0x7F)) meldra_panic_text_builder_ascii({scalar});",
                    f"    if ({builder}.length >= {builder}.capacity) meldra_panic_builder_capacity_invariant();",
                    f"    {builder}.data[{builder}.length++] = (uint8_t){scalar};",
                ]
            )
        elif instruction.op == "text_builder_push_scalar":
            builder, scalar, width = operands
            lines.extend(
                [
                    f"    if ({builder}.state != UINT8_C(1)) meldra_panic_builder_state();",
                    f"    if ({builder}.active_views != 0) meldra_panic_builder_active_view();",
                    f"    if ({scalar} > UINT64_C(0x10FFFF) || ({scalar} >= UINT64_C(0xD800) && {scalar} <= UINT64_C(0xDFFF))) meldra_panic_text_builder_scalar({scalar});",
                    f"    if ({width} > {builder}.capacity - {builder}.length) meldra_panic_builder_capacity_invariant();",
                    f"    uint8_t *meldra_text_builder_out_{instruction.id} = {builder}.data + {builder}.length;",
                    f"    if ({width} == UINT64_C(1)) meldra_text_builder_out_{instruction.id}[0] = (uint8_t){scalar};",
                    f"    else if ({width} == UINT64_C(2)) {{ meldra_text_builder_out_{instruction.id}[0] = (uint8_t)(UINT64_C(0xC0) | ({scalar} >> 6)); meldra_text_builder_out_{instruction.id}[1] = (uint8_t)(UINT64_C(0x80) | ({scalar} & UINT64_C(0x3F))); }}",
                    f"    else if ({width} == UINT64_C(3)) {{ meldra_text_builder_out_{instruction.id}[0] = (uint8_t)(UINT64_C(0xE0) | ({scalar} >> 12)); meldra_text_builder_out_{instruction.id}[1] = (uint8_t)(UINT64_C(0x80) | (({scalar} >> 6) & UINT64_C(0x3F))); meldra_text_builder_out_{instruction.id}[2] = (uint8_t)(UINT64_C(0x80) | ({scalar} & UINT64_C(0x3F))); }}",
                    f"    else {{ meldra_text_builder_out_{instruction.id}[0] = (uint8_t)(UINT64_C(0xF0) | ({scalar} >> 18)); meldra_text_builder_out_{instruction.id}[1] = (uint8_t)(UINT64_C(0x80) | (({scalar} >> 12) & UINT64_C(0x3F))); meldra_text_builder_out_{instruction.id}[2] = (uint8_t)(UINT64_C(0x80) | (({scalar} >> 6) & UINT64_C(0x3F))); meldra_text_builder_out_{instruction.id}[3] = (uint8_t)(UINT64_C(0x80) | ({scalar} & UINT64_C(0x3F))); }}",
                    f"    {builder}.length += {width};",
                ]
            )
        elif instruction.op == "text_builder_extend":
            builder, view = operands
            lines.extend(
                [
                    f"    if ({builder}.state != UINT8_C(1)) meldra_panic_builder_state();",
                    f"    if ({builder}.active_views != 0) meldra_panic_builder_active_view();",
                    f"    if ({view}.length != 0 && {builder}.data != NULL && (uintptr_t){view}.data >= (uintptr_t){builder}.data && (uintptr_t){view}.data < (uintptr_t)({builder}.data + {builder}.capacity)) meldra_panic_text_builder_overlap();",
                    f"    if ({view}.length > {builder}.capacity - {builder}.length) meldra_panic_builder_capacity_invariant();",
                    f"    if ({view}.length != 0) memcpy({builder}.data + {builder}.length, {view}.data, (size_t){view}.length);",
                    f"    {builder}.length += {view}.length;",
                    f"    meldra_builder_extend_copied_bytes += {view}.length;",
                ]
            )
        elif instruction.op == "text_builder_view":
            builder = operands[0]
            lines.extend(
                [
                    f"    if ({builder}.state != UINT8_C(1)) meldra_panic_builder_state();",
                    f"    ++{builder}.active_views;",
                    f"    meldra_text_view {result} = {{ {builder}.data, {builder}.length }};",
                ]
            )
        elif instruction.op == "text_builder_finish_transfer":
            builder = operands[0]
            lines.extend(
                [
                    f"    if ({builder}.state != UINT8_C(1)) meldra_panic_builder_state();",
                    f"    if ({builder}.active_views != 0) meldra_panic_builder_active_view();",
                    f"    meldra_text {result} = {{ {builder}.data, {builder}.length, {builder}.capacity, true }};",
                    f"    {builder}.data = NULL;",
                    f"    {builder}.length = 0;",
                    f"    {builder}.capacity = 0;",
                    f"    {builder}.active_views = 0;",
                    f"    {builder}.state = UINT8_C(3);",
                ]
            )
        elif instruction.op == "text_builder_drop":
            builder = operands[0]
            lines.extend(
                [
                    f"    if ({builder}.state != UINT8_C(1)) meldra_panic_builder_state();",
                    f"    if ({builder}.active_views != 0) meldra_panic_builder_active_view();",
                    f"    if ({builder}.data != NULL) {{ free({builder}.data); ++meldra_heap_frees; }}",
                    f"    {builder}.data = NULL;",
                    f"    {builder}.length = 0;",
                    f"    {builder}.capacity = 0;",
                    f"    {builder}.active_views = 0;",
                    f"    {builder}.state = UINT8_C(4);",
                ]
            )
        elif instruction.op == "builder_create":
            capacity = operands[0]
            lines.extend(
                [
                    f"    if ({capacity} > UINT64_C(9223372036854775807)) meldra_panic_builder_allocation_size_overflow();",
                    f"    meldra_bytes_builder {result} = {{ NULL, UINT64_C(0), {capacity}, UINT64_C(0), UINT8_C(1) }};",
                    f"    if ({capacity} > 0) {{",
                    f"        {result}.data = (uint8_t *)malloc((size_t){capacity});",
                    f"        if ({result}.data == NULL) meldra_panic_alloc();",
                    "        ++meldra_heap_allocations;",
                    f"        meldra_allocated_bytes += {capacity};",
                    "    }",
                ]
            )
        elif instruction.op == "builder_len":
            lines.append(
                f"    if ({operands[0]}.state != UINT8_C(1)) meldra_panic_builder_state();"
            )
            lines.append(
                f"    uint64_t {result} = {operands[0]}.length;"
            )
        elif instruction.op == "builder_capacity":
            lines.append(
                f"    if ({operands[0]}.state != UINT8_C(1)) meldra_panic_builder_state();"
            )
            lines.append(
                f"    uint64_t {result} = {operands[0]}.capacity;"
            )
        elif instruction.op == "builder_reserve":
            lines.extend(
                [
                    f"    if ({operands[0]}.state != UINT8_C(1)) meldra_panic_builder_state();",
                    f"    if ({operands[0]}.active_views != 0) meldra_panic_builder_active_view();",
                    f"    /* builder_reserve guarantee for {operands[1]} additional bytes */",
                ]
            )
        elif instruction.op == "builder_grow":
            builder, additional = operands
            required = f"meldra_builder_required_{instruction.id}"
            doubled = f"meldra_builder_doubled_{instruction.id}"
            capacity = f"meldra_builder_capacity_{instruction.id}"
            storage = f"meldra_builder_storage_{instruction.id}"
            text_builder = attributes.get("builder_type") == "TextBuilder"
            length_panic = (
                "meldra_panic_text_builder_length_overflow"
                if text_builder
                else "meldra_panic_builder_length_overflow"
            )
            capacity_panic = (
                "meldra_panic_text_builder_capacity_overflow"
                if text_builder
                else "meldra_panic_builder_capacity_overflow"
            )
            allocation_panic = (
                "meldra_panic_text_builder_allocation_size_overflow"
                if text_builder
                else "meldra_panic_builder_allocation_size_overflow"
            )
            lines.extend(
                [
                    f"    if ({builder}.state != UINT8_C(1)) meldra_panic_builder_state();",
                    f"    if ({builder}.active_views != 0) meldra_panic_builder_active_view();",
                    f"    if ({additional} > UINT64_MAX - {builder}.length) {length_panic}();",
                    f"    uint64_t {required} = {builder}.length + {additional};",
                    f"    if ({required} > {builder}.capacity) {{",
                    f"        uint64_t {capacity};",
                    f"        if ({builder}.capacity == 0) {capacity} = {required} > UINT64_C(8) ? {required} : UINT64_C(8);",
                    "        else {",
                    f"            if ({builder}.capacity > UINT64_MAX / UINT64_C(2)) {capacity_panic}();",
                    f"            uint64_t {doubled} = {builder}.capacity * UINT64_C(2);",
                    f"            {capacity} = {required} > {doubled} ? {required} : {doubled};",
                    "        }",
                    f"        if ({capacity} > UINT64_C(9223372036854775807)) {allocation_panic}();",
                    f"        uint8_t *{storage} = (uint8_t *)malloc((size_t){capacity});",
                    f"        if ({storage} == NULL) meldra_panic_alloc();",
                    "        ++meldra_heap_allocations;",
                    f"        meldra_allocated_bytes += {capacity};",
                    f"        if ({builder}.data != NULL) {{",
                    f"            if ({builder}.length != 0) memcpy({storage}, {builder}.data, (size_t){builder}.length);",
                    f"            meldra_builder_growth_copied_bytes += {builder}.length;",
                    f"            free({builder}.data);",
                    "            ++meldra_heap_frees;",
                    "            ++meldra_builder_reallocations;",
                    "        }",
                    f"        {builder}.data = {storage};",
                    f"        {builder}.capacity = {capacity};",
                    "    }",
                ]
            )
        elif instruction.op == "builder_push":
            builder, byte = operands
            lines.extend(
                [
                    f"    if ({builder}.state != UINT8_C(1)) meldra_panic_builder_state();",
                    f"    if ({builder}.active_views != 0) meldra_panic_builder_active_view();",
                    f"    if ({byte} > UINT64_C(255)) meldra_panic_builder_byte({byte});",
                    f"    if ({builder}.length >= {builder}.capacity) meldra_panic_builder_capacity_invariant();",
                    f"    {builder}.data[{builder}.length++] = (uint8_t){byte};",
                ]
            )
        elif instruction.op == "builder_extend":
            builder, view = operands
            lines.extend(
                [
                    f"    if ({builder}.state != UINT8_C(1)) meldra_panic_builder_state();",
                    f"    if ({builder}.active_views != 0) meldra_panic_builder_active_view();",
                    f"    if ({view}.length != 0 && {builder}.data != NULL && (uintptr_t){view}.data >= (uintptr_t){builder}.data && (uintptr_t){view}.data < (uintptr_t)({builder}.data + {builder}.capacity)) meldra_panic_builder_overlap();",
                    f"    if ({view}.length > {builder}.capacity - {builder}.length) meldra_panic_builder_capacity_invariant();",
                    f"    if ({view}.length != 0) memcpy({builder}.data + {builder}.length, {view}.data, (size_t){view}.length);",
                    f"    {builder}.length += {view}.length;",
                    f"    meldra_builder_extend_copied_bytes += {view}.length;",
                ]
            )
        elif instruction.op == "builder_view":
            builder = operands[0]
            lines.extend(
                [
                    f"    if ({builder}.state != UINT8_C(1)) meldra_panic_builder_state();",
                    f"    ++{builder}.active_views;",
                    f"    meldra_bytes_view {result} = {{ {builder}.data, {builder}.length }};",
                ]
            )
        elif instruction.op == "builder_finish_transfer":
            builder = operands[0]
            lines.extend(
                [
                    f"    if ({builder}.state != UINT8_C(1)) meldra_panic_builder_state();",
                    f"    if ({builder}.active_views != 0) meldra_panic_builder_active_view();",
                    f"    meldra_bytes {result} = {{ {builder}.data, {builder}.length, {builder}.capacity, true }};",
                    f"    {builder}.data = NULL;",
                    f"    {builder}.length = 0;",
                    f"    {builder}.capacity = 0;",
                    f"    {builder}.active_views = 0;",
                    f"    {builder}.state = UINT8_C(3);",
                ]
            )
        elif instruction.op == "builder_drop":
            builder = operands[0]
            lines.extend(
                [
                    f"    if ({builder}.state != UINT8_C(1)) meldra_panic_builder_state();",
                    f"    if ({builder}.active_views != 0) meldra_panic_builder_active_view();",
                    f"    if ({builder}.data != NULL) {{ free({builder}.data); ++meldra_heap_frees; }}",
                    f"    {builder}.data = NULL;",
                    f"    {builder}.length = 0;",
                    f"    {builder}.capacity = 0;",
                    f"    {builder}.active_views = 0;",
                    f"    {builder}.state = UINT8_C(4);",
                ]
            )
        elif instruction.op in {"allocation", "payload_copy", "free"}:
            lines.append(
                f"    /* conditional {instruction.op} event: "
                f"{attributes.get('condition', 'builder_growth')} */"
            )
        elif instruction.op == "const":
            lines.append(f"    {ctype} {result} = {_literal(attributes['value'], instruction.type)};")
        elif instruction.op == "binary":
            operator = str(attributes["operator"])
            if instruction.type and instruction.type.kind == "int" and operator in {
                "add",
                "sub",
                "mul",
                "div",
                "mod",
                "shift_left",
                "shift_right",
            }:
                lines.append(
                    f"    {ctype} {result} = meldra_i64_{operator}({operands[0]}, {operands[1]});"
                )
            elif instruction.type and instruction.type.kind == "uint" and operator in {
                "div",
                "mod",
                "shift_left",
                "shift_right",
            }:
                lines.append(
                    f"    {ctype} {result} = meldra_u64_{operator}({operands[0]}, {operands[1]});"
                )
            else:
                lines.append(
                    f"    {ctype} {result} = {operands[0]} {_operator(operator)} {operands[1]};"
                )
        elif instruction.op == "compare":
            lines.append(f"    bool {result} = {operands[0]} {_operator(attributes['operator'])} {operands[1]};")
        elif instruction.op == "unary":
            operator = str(attributes["operator"])
            if (
                instruction.type
                and instruction.type.kind == "int"
                and operator in {"neg", "bit_not"}
            ):
                lines.append(
                    f"    {ctype} {result} = meldra_i64_{operator}({operands[0]});"
                )
            else:
                unary = {"neg": "-", "not": "!", "bit_not": "~"}[operator]
                lines.append(f"    {ctype} {result} = {unary}{operands[0]};")
        elif instruction.op == "store_local":
            lines.append(f"    {operands[0]} = {operands[1]};")
        elif instruction.op == "load_local":
            lines.append(f"    {ctype} {result} = {operands[0]};")
        elif instruction.op == "move":
            lines.append(f"    {ctype} {result} = {operands[0]};")
            if instruction.type and instruction.type.kind in {"array", "slice"}:
                lines.extend(
                    [
                        f"    {operands[0]}.data = NULL;",
                        f"    {operands[0]}.length = 0;",
                        f"    {operands[0]}.heap = false;",
                        f"    {operands[0]}.refcount = NULL;",
                    ]
                )
            elif instruction.type and instruction.type.record == "Bytes":
                lines.extend(
                    [
                        f"    {operands[0]}.data = NULL;",
                        f"    {operands[0]}.length = 0;",
                        f"    {operands[0]}.capacity = 0;",
                        f"    {operands[0]}.live = false;",
                    ]
                )
            elif instruction.type and instruction.type.record == "Text":
                lines.extend(
                    [
                        f"    {operands[0]}.data = NULL;",
                        f"    {operands[0]}.length = 0;",
                        f"    {operands[0]}.capacity = 0;",
                        f"    {operands[0]}.live = false;",
                    ]
                )
            elif instruction.type and instruction.type.record in {
                "BytesBuilder",
                "TextBuilder",
            }:
                lines.extend(
                    [
                        f"    {operands[0]}.data = NULL;",
                        f"    {operands[0]}.length = 0;",
                        f"    {operands[0]}.capacity = 0;",
                        f"    {operands[0]}.active_views = 0;",
                        f"    {operands[0]}.state = UINT8_C(2);",
                    ]
                )
        elif instruction.op == "retain":
            lines.extend(
                [
                    f"    {ctype} {result} = {operands[0]};",
                    f"    if ({result}.heap && {result}.refcount != NULL) ++*{result}.refcount;",
                ]
            )
        elif instruction.op == "array_init":
            lines.append(f"    {ctype} {result} = {operands[0]};")
            lines.append(f"    {result}.length = UINT64_C({attributes['length']});")
            for index, value in enumerate(operands[1:]):
                lines.append(f"    {result}.data[{index}] = {value};")
        elif instruction.op == "array_len":
            lines.append(f"    uint64_t {result} = {operands[0]}.length;")
        elif instruction.op == "bounds_check":
            lines.append(f"    if ({operands[0]} >= {operands[1]}) meldra_panic_bounds({operands[0]}, {operands[1]});")
        elif instruction.op == "index_load":
            lines.append(f"    {ctype} {result} = {operands[0]}.data[{operands[1]}];")
        elif instruction.op == "store_index":
            lines.append(f"    {operands[0]}.data[{operands[1]}] = {operands[2]};")
        elif instruction.op == "record_init":
            record = self.mir.records[[item.name for item in self.mir.records].index(attributes["record"])]
            initializers = ", ".join(
                f".{field} = {value}"
                for (field, _type), value in zip(record.fields, operands, strict=True)
            )
            lines.append(f"    {ctype} {result} = {{ {initializers} }};")
        elif instruction.op == "field_load":
            lines.append(f"    {ctype} {result} = {operands[0]}.{attributes['field']};")
        elif instruction.op == "call":
            callee = self._function_name(str(attributes["callee"]))
            arguments = ", ".join(operands)
            if instruction.type and instruction.type.kind == "unit":
                lines.append(f"    {callee}({arguments});")
            else:
                lines.append(f"    {ctype} {result} = {callee}({arguments});")
        elif (
            instruction.op == "borrow_end"
            and attributes.get("builder_owner") is not None
        ):
            builder = operands[0]
            lines.extend(
                [
                    f"    if ({builder}.active_views == 0) meldra_panic_builder_active_view();",
                    f"    --{builder}.active_views;",
                    f"    /* builder borrow_end: {attributes.get('borrow_id', instruction.id)} */",
                ]
            )
        elif (
            instruction.op == "borrow_end"
            and attributes.get("text_owner") is not None
        ):
            lines.append(
                f"    /* TextView borrow_end: "
                f"{attributes.get('borrow_id', instruction.id)} "
                f"owner={attributes.get('text_owner')} */"
            )
        elif instruction.op in {
            "borrow_argument",
            "reborrow_argument",
            "borrow_return_transfer",
            "caller_borrow_continue",
            "reborrow_end",
            "borrow_end",
        }:
            lines.append(
                f"    /* {instruction.op}: "
                f"{attributes.get('borrow_id', attributes.get('callee', 'direct_call'))} "
                f"root={attributes.get('root_owner', 'unknown')} */"
            )
        elif instruction.op.startswith("collection_map"):
            function = self._function_name(str(attributes["function"]))
            source, allocation = operands
            element = _ctype(instruction.type.element)
            lines.extend(
                [
                    f"    if ({allocation}.data == NULL && {source}.length > 0) {{",
                    f"        {allocation}.data = ({element} *)malloc(sizeof({element}) * {source}.length);",
                    f"        {allocation}.refcount = (uint64_t *)malloc(sizeof(uint64_t));",
                    f"        if ({allocation}.data == NULL || {allocation}.refcount == NULL) meldra_panic_alloc();",
                    f"        *{allocation}.refcount = UINT64_C(1);",
                    f"        {allocation}.heap = true; ++meldra_heap_allocations;",
                    "    }",
                    f"    {allocation}.length = {source}.length;",
                    f"    for (uint64_t meldra_i_{instruction.id} = 0; meldra_i_{instruction.id} < {source}.length; ++meldra_i_{instruction.id})",
                    f"        {allocation}.data[meldra_i_{instruction.id}] = {function}({source}.data[meldra_i_{instruction.id}]);",
                    f"    {ctype} {result} = {allocation};",
                ]
            )
        elif instruction.op.startswith("collection_filter"):
            function = self._function_name(str(attributes["function"]))
            source, allocation = operands
            element = _ctype(instruction.type.element)
            index = f"meldra_i_{instruction.id}"
            count = f"meldra_count_{instruction.id}"
            lines.extend(
                [
                    f"    if ({allocation}.data == NULL && {source}.length > 0) {{",
                    f"        {allocation}.data = ({element} *)malloc(sizeof({element}) * {source}.length);",
                    f"        {allocation}.refcount = (uint64_t *)malloc(sizeof(uint64_t));",
                    f"        if ({allocation}.data == NULL || {allocation}.refcount == NULL) meldra_panic_alloc();",
                    f"        *{allocation}.refcount = UINT64_C(1);",
                    f"        {allocation}.heap = true; ++meldra_heap_allocations;",
                    "    }",
                    f"    uint64_t {count} = 0;",
                    f"    for (uint64_t {index} = 0; {index} < {source}.length; ++{index}) {{",
                    f"        if ({function}({source}.data[{index}])) {allocation}.data[{count}++] = {source}.data[{index}];",
                    "    }",
                    f"    {allocation}.length = {count};",
                    f"    {ctype} {result} = {allocation};",
                ]
            )
        elif instruction.op.startswith("collection_fold"):
            function = self._function_name(str(attributes["function"]))
            index = f"meldra_i_{instruction.id}"
            lines.extend(
                [
                    f"    {ctype} {result} = {operands[1]};",
                    f"    for (uint64_t {index} = 0; {index} < {operands[0]}.length; ++{index})",
                    f"        {result} = {function}({result}, {operands[0]}.data[{index}]);",
                ]
            )
        elif instruction.op == "fused_collection_loop":
            map_function = self._function_name(str(attributes["map_function"]))
            fold_function = self._function_name(str(attributes["fold_function"]))
            filter_function = attributes.get("filter_function")
            index = f"meldra_i_{instruction.id}"
            mapped_type = self.function_map[str(attributes["map_function"])].return_type
            mapped = f"meldra_mapped_{instruction.id}"
            lines.extend(
                [
                    f"    {ctype} {result} = {operands[1]};",
                    f"    for (uint64_t {index} = 0; {index} < {operands[0]}.length; ++{index}) {{",
                    f"        {_ctype(mapped_type)} {mapped} = {map_function}({operands[0]}.data[{index}]);",
                ]
            )
            if filter_function:
                predicate = self._function_name(str(filter_function))
                lines.append(f"        if ({predicate}({mapped})) {result} = {fold_function}({result}, {mapped});")
            else:
                lines.append(f"        {result} = {fold_function}({result}, {mapped});")
            lines.append("    }")
        elif instruction.op == "drop":
            operand_type = self.value_types.get(instruction.operands[0])
            owner_type = attributes.get("owner_type")
            if owner_type == "Bytes" or (
                operand_type is not None and operand_type.record == "Bytes"
            ):
                lines.extend(
                    [
                        f"    if (!{operands[0]}.live) meldra_panic_bytes_double_drop();",
                        f"    if ({operands[0]}.data != NULL) {{ free({operands[0]}.data); ++meldra_heap_frees; }}",
                        f"    {operands[0]}.data = NULL; {operands[0]}.length = 0; {operands[0]}.capacity = 0; {operands[0]}.live = false;",
                    ]
                )
            else:
                lines.extend(
                    [
                        f"    if ({operands[0]}.heap && {operands[0]}.refcount != NULL && --*{operands[0]}.refcount == 0) {{",
                        f"        free({operands[0]}.data); free({operands[0]}.refcount);",
                        "    }",
                        f"    {operands[0]}.data = NULL; {operands[0]}.refcount = NULL; {operands[0]}.length = 0;",
                    ]
                )
        else:
            raise NativeBackendError(f"unsupported MIR instruction: {instruction.op}")
        return lines

    def _prototype(self, function: MIRFunction) -> str:
        parameters = ", ".join(
            f"{_ctype(item.type)} {self._value(item.value)}" for item in function.parameters
        ) or "void"
        qualifier = "MELDRA_NOINLINE " if function.return_type.shared else ""
        return f"static {qualifier}{_ctype(function.return_type)} {self._function_name(function.name)}({parameters})"

    def _function(self, function: MIRFunction) -> str:
        lines = [self._prototype(function) + " {"]
        for block in function.blocks:
            lines.append(f"{_identifier(block.id)}: ;")
            for instruction in block.instructions:
                lines.extend(self._instruction(instruction))
            terminator = block.terminator
            if terminator.kind == "jump":
                lines.append(f"    goto {_identifier(terminator.targets[0])};")
            elif terminator.kind == "branch":
                lines.append(
                    f"    if ({self._value(terminator.condition or '')}) goto {_identifier(terminator.targets[0])}; else goto {_identifier(terminator.targets[1])};"
                )
            elif terminator.kind == "return":
                if terminator.value is None:
                    lines.append("    return;")
                else:
                    lines.append(f"    return {self._value(terminator.value)};")
            else:
                lines.append("    abort();")
        lines.append("}")
        return "\n".join(lines)

    def emit(self) -> str:
        lines = [
            "/* Deterministic C11 generated by Meldra Stage 0.6B Bytes experiment. */",
            "#include <errno.h>",
            "#include <stdbool.h>",
            "#include <inttypes.h>",
            "#include <limits.h>",
            "#include <stddef.h>",
            "#include <stdint.h>",
            "#include <stdio.h>",
            "#include <math.h>",
            "#include <stdlib.h>",
            "#include <string.h>",
            "#if defined(__GNUC__) || defined(__clang__)",
            "#define MELDRA_NOINLINE __attribute__((noinline))",
            "#else",
            "#define MELDRA_NOINLINE",
            "#endif",
            "",
            "typedef struct { uint8_t *data; uint64_t length; uint64_t capacity; bool live; } meldra_bytes;",
            "typedef struct { const uint8_t *data; uint64_t length; } meldra_bytes_view;",
            "typedef struct { uint8_t *data; uint64_t length; uint64_t capacity; uint64_t active_views; uint8_t state; } meldra_bytes_builder;",
            "typedef struct { uint8_t *data; uint64_t length; uint64_t capacity; bool live; } meldra_text;",
            "typedef struct { const uint8_t *data; uint64_t length; } meldra_text_view;",
            "typedef struct { bool valid; meldra_text text; uint64_t error_offset; bool consumed; } meldra_utf8_decode;",
            "",
            "static uint64_t meldra_heap_allocations = 0;",
            "static uint64_t meldra_heap_frees = 0;",
            "static uint64_t meldra_allocated_bytes = 0;",
            "static uint64_t meldra_payload_copies = 0;",
            "static uint64_t meldra_bounds_checks = 0;",
            "static uint64_t meldra_builder_reallocations = 0;",
            "static uint64_t meldra_builder_growth_copied_bytes = 0;",
            "static uint64_t meldra_builder_extend_copied_bytes = 0;",
            "static uint64_t meldra_builder_finish_copies = 0;",
            "static uint64_t meldra_text_builder_required_append_bytes = 0;",
            "static void meldra_panic_alloc(void) { fputs(\"Meldra allocation failure\\n\", stderr); abort(); }",
            "static void meldra_panic_bytes_allocation_overflow(uint64_t length) { fprintf(stderr, \"BytesAllocationOverflow: %\" PRIu64 \"\\n\", length); abort(); }",
            "static void meldra_panic_bytes_bounds(uint64_t index, uint64_t length) { fprintf(stderr, \"BytesIndexOutOfBounds: %\" PRIu64 \" >= %\" PRIu64 \"\\n\", index, length); abort(); }",
            "static void meldra_panic_bytes_slice(uint64_t start, uint64_t length, uint64_t owner_length) { fprintf(stderr, \"BytesSliceOutOfBounds: start=%\" PRIu64 \" length=%\" PRIu64 \" owner=%\" PRIu64 \"\\n\", start, length, owner_length); abort(); }",
            "static void meldra_panic_bytes_double_drop(void) { fputs(\"BytesDoubleDrop\\n\", stderr); abort(); }",
            "static void meldra_panic_builder_state(void) { fputs(\"BytesBuilderInvalidState\\n\", stderr); abort(); }",
            "static void meldra_panic_builder_active_view(void) { fputs(\"BytesBuilderActiveView\\n\", stderr); abort(); }",
            "static void meldra_panic_builder_length_overflow(void) { fputs(\"BytesBuilderLengthOverflow\\n\", stderr); abort(); }",
            "static void meldra_panic_builder_capacity_overflow(void) { fputs(\"BytesBuilderCapacityOverflow\\n\", stderr); abort(); }",
            "static void meldra_panic_builder_allocation_size_overflow(void) { fputs(\"BytesBuilderAllocationSizeOverflow\\n\", stderr); abort(); }",
            "static void meldra_panic_builder_byte(uint64_t byte) { fprintf(stderr, \"BytesBuilderByteOutOfRange: %\" PRIu64 \"\\n\", byte); abort(); }",
            "static void meldra_panic_builder_overlap(void) { fputs(\"BytesBuilderOverlappingExtend\\n\", stderr); abort(); }",
            "static void meldra_panic_builder_capacity_invariant(void) { fputs(\"BytesBuilderCapacityInvariant\\n\", stderr); abort(); }",
            "static void meldra_panic_text_builder_allocation_size_overflow(void) { fputs(\"TextBuilderAllocationSizeOverflow\\n\", stderr); abort(); }",
            "static void meldra_panic_text_builder_length_overflow(void) { fputs(\"TextBuilderLengthOverflow\\n\", stderr); abort(); }",
            "static void meldra_panic_text_builder_capacity_overflow(void) { fputs(\"TextBuilderCapacityOverflow\\n\", stderr); abort(); }",
            "static void meldra_panic_text_builder_ascii(uint64_t scalar) { fprintf(stderr, \"TextBuilderAsciiOutOfRange: %\" PRIu64 \"\\n\", scalar); abort(); }",
            "static void meldra_panic_text_builder_scalar(uint64_t scalar) { fprintf(stderr, \"TextBuilderInvalidUnicodeScalar: %\" PRIu64 \"\\n\", scalar); abort(); }",
            "static void meldra_panic_text_builder_overlap(void) { fputs(\"TextBuilderOverlappingExtend\\n\", stderr); abort(); }",
            "static void meldra_panic_text_double_drop(void) { fputs(\"TextDoubleDrop\\n\", stderr); abort(); }",
            "static void meldra_panic_utf8_decode_state(void) { fputs(\"Utf8DecodeInvalidState\\n\", stderr); abort(); }",
            "static void meldra_panic_text_boundary(uint64_t offset) { fprintf(stderr, \"TextSliceNotOnUtf8Boundary: %\" PRIu64 \"\\n\", offset); abort(); }",
            "static void meldra_panic_text_slice(uint64_t start, uint64_t length, uint64_t owner) { fprintf(stderr, \"TextSliceOutOfBounds: start=%\" PRIu64 \" length=%\" PRIu64 \" owner=%\" PRIu64 \"\\n\", start, length, owner); abort(); }",
            "static void meldra_panic_text_scalar(uint64_t scalar) { fprintf(stderr, \"InvalidUnicodeScalar: %\" PRIu64 \"\\n\", scalar); abort(); }",
            "static void meldra_panic_ascii(uint64_t scalar) { fprintf(stderr, \"TextAsciiOutOfRange: %\" PRIu64 \"\\n\", scalar); abort(); }",
            "static void meldra_panic_surrogate(void) { fputs(\"InvalidUnicodeSurrogatePair\\n\", stderr); abort(); }",
            "static bool meldra_utf8_cont(uint8_t byte) { return (byte & UINT8_C(0xC0)) == UINT8_C(0x80); }",
            "static bool meldra_utf8_validate(const uint8_t *data, uint64_t length, uint64_t *error) {",
            "    uint64_t i = 0;",
            "    while (i < length) {",
            "        uint8_t first = data[i];",
            "        if (first <= UINT8_C(0x7F)) { ++i; continue; }",
            "        if (first >= UINT8_C(0xC2) && first <= UINT8_C(0xDF)) {",
            "            if (i + UINT64_C(1) >= length || !meldra_utf8_cont(data[i + 1])) { *error = i; return false; }",
            "            i += UINT64_C(2); continue;",
            "        }",
            "        if (first >= UINT8_C(0xE0) && first <= UINT8_C(0xEF)) {",
            "            if (i + UINT64_C(2) >= length) { *error = i; return false; }",
            "            uint8_t second = data[i + 1];",
            "            bool second_ok = meldra_utf8_cont(second);",
            "            if (first == UINT8_C(0xE0)) second_ok = second >= UINT8_C(0xA0) && second <= UINT8_C(0xBF);",
            "            if (first == UINT8_C(0xED)) second_ok = second >= UINT8_C(0x80) && second <= UINT8_C(0x9F);",
            "            if (!second_ok || !meldra_utf8_cont(data[i + 2])) { *error = i; return false; }",
            "            i += UINT64_C(3); continue;",
            "        }",
            "        if (first >= UINT8_C(0xF0) && first <= UINT8_C(0xF4)) {",
            "            if (i + UINT64_C(3) >= length) { *error = i; return false; }",
            "            uint8_t second = data[i + 1];",
            "            bool second_ok = meldra_utf8_cont(second);",
            "            if (first == UINT8_C(0xF0)) second_ok = second >= UINT8_C(0x90) && second <= UINT8_C(0xBF);",
            "            if (first == UINT8_C(0xF4)) second_ok = second >= UINT8_C(0x80) && second <= UINT8_C(0x8F);",
            "            if (!second_ok || !meldra_utf8_cont(data[i + 2]) || !meldra_utf8_cont(data[i + 3])) { *error = i; return false; }",
            "            i += UINT64_C(4); continue;",
            "        }",
            "        *error = i; return false;",
            "    }",
            "    *error = UINT64_C(0); return true;",
            "}",
            "static void meldra_utf8_boundary_check(const uint8_t *data, uint64_t owner, uint64_t start, uint64_t length) {",
            "    if (start > owner || length > owner - start) meldra_panic_text_slice(start, length, owner);",
            "    uint64_t end = start + length;",
            "    if (start > 0 && meldra_utf8_cont(data[start])) meldra_panic_text_boundary(start);",
            "    if (end < owner && meldra_utf8_cont(data[end])) meldra_panic_text_boundary(end);",
            "}",
            "static uint64_t meldra_utf8_scalar_width(const uint8_t *data, uint64_t length, uint64_t offset) {",
            "    if (offset >= length) meldra_panic_text_slice(offset, UINT64_C(1), length);",
            "    uint8_t first = data[offset];",
            "    if (first <= UINT8_C(0x7F)) return UINT64_C(1);",
            "    if (first <= UINT8_C(0xDF)) return UINT64_C(2);",
            "    if (first <= UINT8_C(0xEF)) return UINT64_C(3);",
            "    return UINT64_C(4);",
            "}",
            "static meldra_text meldra_text_from_scalar(uint64_t scalar) {",
            "    if (scalar > UINT64_C(0x10FFFF) || (scalar >= UINT64_C(0xD800) && scalar <= UINT64_C(0xDFFF))) meldra_panic_text_scalar(scalar);",
            "    uint64_t length = scalar <= UINT64_C(0x7F) ? UINT64_C(1) : scalar <= UINT64_C(0x7FF) ? UINT64_C(2) : scalar <= UINT64_C(0xFFFF) ? UINT64_C(3) : UINT64_C(4);",
            "    uint8_t *data = (uint8_t *)malloc((size_t)length);",
            "    if (data == NULL) meldra_panic_alloc();",
            "    ++meldra_heap_allocations; meldra_allocated_bytes += length;",
            "    if (length == UINT64_C(1)) data[0] = (uint8_t)scalar;",
            "    else if (length == UINT64_C(2)) { data[0] = (uint8_t)(UINT64_C(0xC0) | (scalar >> 6)); data[1] = (uint8_t)(UINT64_C(0x80) | (scalar & UINT64_C(0x3F))); }",
            "    else if (length == UINT64_C(3)) { data[0] = (uint8_t)(UINT64_C(0xE0) | (scalar >> 12)); data[1] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 6) & UINT64_C(0x3F))); data[2] = (uint8_t)(UINT64_C(0x80) | (scalar & UINT64_C(0x3F))); }",
            "    else { data[0] = (uint8_t)(UINT64_C(0xF0) | (scalar >> 18)); data[1] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 12) & UINT64_C(0x3F))); data[2] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 6) & UINT64_C(0x3F))); data[3] = (uint8_t)(UINT64_C(0x80) | (scalar & UINT64_C(0x3F))); }",
            "    return (meldra_text){ data, length, length, true };",
            "}",
            "static void meldra_panic_bounds(uint64_t index, uint64_t length) {",
            "    fprintf(stderr, \"Meldra bounds failure: %\" PRIu64 \" >= %\" PRIu64 \"\\n\", index, length);",
            "    abort();",
            "}",
            "static void meldra_panic_division(void) { fputs(\"Meldra division by zero\\n\", stderr); abort(); }",
            "static int64_t meldra_bits_i64(uint64_t value) { int64_t result; memcpy(&result, &value, sizeof(result)); return result; }",
            "static int64_t meldra_i64_add(int64_t left, int64_t right) { return meldra_bits_i64((uint64_t)left + (uint64_t)right); }",
            "static int64_t meldra_i64_sub(int64_t left, int64_t right) { return meldra_bits_i64((uint64_t)left - (uint64_t)right); }",
            "static int64_t meldra_i64_mul(int64_t left, int64_t right) { return meldra_bits_i64((uint64_t)left * (uint64_t)right); }",
            "static int64_t meldra_i64_div(int64_t left, int64_t right) { if (right == 0) meldra_panic_division(); if (left == INT64_MIN && right == -1) return INT64_MIN; return left / right; }",
            "static int64_t meldra_i64_mod(int64_t left, int64_t right) { if (right == 0) meldra_panic_division(); if (left == INT64_MIN && right == -1) return 0; return left % right; }",
            "static int64_t meldra_i64_shift_left(int64_t left, int64_t right) { uint64_t shift = (uint64_t)right & UINT64_C(63); return meldra_bits_i64((uint64_t)left << shift); }",
            "static int64_t meldra_i64_shift_right(int64_t left, int64_t right) { uint64_t shift = (uint64_t)right & UINT64_C(63); uint64_t value = (uint64_t)left; if (shift == 0) return left; if (left < 0) value = (value >> shift) | (UINT64_MAX << (64 - shift)); else value >>= shift; return meldra_bits_i64(value); }",
            "static int64_t meldra_i64_neg(int64_t value) { return meldra_bits_i64(UINT64_C(0) - (uint64_t)value); }",
            "static int64_t meldra_i64_bit_not(int64_t value) { return meldra_bits_i64(~(uint64_t)value); }",
            "static uint64_t meldra_u64_div(uint64_t left, uint64_t right) { if (right == 0) meldra_panic_division(); return left / right; }",
            "static uint64_t meldra_u64_mod(uint64_t left, uint64_t right) { if (right == 0) meldra_panic_division(); return left % right; }",
            "static uint64_t meldra_u64_shift_left(uint64_t left, uint64_t right) { return left << (right & UINT64_C(63)); }",
            "static uint64_t meldra_u64_shift_right(uint64_t left, uint64_t right) { return left >> (right & UINT64_C(63)); }",
            "",
        ]
        lines.extend(json_streaming_c_source().splitlines())
        lines.append("")
        emitted_collection_types: set[str] = set()
        for type_ in _collect_types(self.mir):
            if type_.kind not in {"array", "slice"}:
                continue
            ctype = _ctype(type_)
            if ctype in emitted_collection_types:
                continue
            emitted_collection_types.add(ctype)
            lines.extend(
                [
                    f"typedef struct {{ {_ctype(type_.element)} *data; uint64_t length; bool heap; uint64_t *refcount; }} {ctype};",
                    "",
                ]
            )
        for record in self.mir.records:
            if record.name in {
                "Bytes",
                "BytesView",
                "BytesBuilder",
                "TextBuilder",
            }:
                continue
            lines.append(f"typedef struct {_ctype(PerformanceType('record', record=record.name))} {{")
            for name, type_ in record.fields:
                lines.append(f"    {_ctype(type_)} {name};")
            lines.append(f"}} {_ctype(PerformanceType('record', record=record.name))};")
            lines.append(f"_Static_assert(sizeof({_ctype(PerformanceType('record', record=record.name))}) == {record.layout.size}, \"record layout drift\");")
            lines.append("")
        for function in self.mir.functions:
            lines.append(self._prototype(function) + ";")
        lines.append("")
        for function in self.mir.functions:
            lines.append(self._function(function))
            lines.append("")
        if self.executable:
            entry = self.mir.function(self.mir.entry_function)
            if not self.runtime_arguments and len(self.entry_arguments) != len(entry.parameters):
                raise NativeBackendError(
                    f"entry {entry.name} needs {len(entry.parameters)} arguments, got {len(self.entry_arguments)}"
                )
            argument_declarations: list[str] = []
            if self.runtime_arguments:
                for index, parameter in enumerate(entry.parameters, 1):
                    ctype = _ctype(parameter.type)
                    name = f"meldra_entry_argument_{index}"
                    expected = parameter.type.name
                    failure = (
                        f'fprintf(stderr, "ArgumentParseError: index={index - 1} '
                        f'expected={expected}\\n"); return 2;'
                    )
                    if parameter.type.kind == "float":
                        argument_declarations.extend(
                            [
                                f"    errno = 0; char *meldra_entry_end_{index} = NULL;",
                                f"    double meldra_entry_parsed_{index} = strtod(argv[{index}], &meldra_entry_end_{index});",
                                f"    if (errno == ERANGE || meldra_entry_end_{index} == argv[{index}] || *meldra_entry_end_{index} != '\\0' || !isfinite(meldra_entry_parsed_{index})) {{ {failure} }}",
                                f"    {ctype} {name} = ({ctype})meldra_entry_parsed_{index};",
                            ]
                        )
                    elif parameter.type.kind == "int":
                        bits = parameter.type.bits or 64
                        range_check = ""
                        if bits < 64:
                            minimum = -(1 << (bits - 1))
                            maximum = (1 << (bits - 1)) - 1
                            range_check = (
                                f" || meldra_entry_parsed_{index} < "
                                f"{minimum}LL || meldra_entry_parsed_{index} "
                                f"> {maximum}LL"
                            )
                        argument_declarations.extend(
                            [
                                f"    errno = 0; char *meldra_entry_end_{index} = NULL;",
                                f"    long long meldra_entry_parsed_{index} = strtoll(argv[{index}], &meldra_entry_end_{index}, 10);",
                                f"    if (errno == ERANGE || meldra_entry_end_{index} == argv[{index}] || *meldra_entry_end_{index} != '\\0'{range_check}) {{ {failure} }}",
                                f"    {ctype} {name} = ({ctype})meldra_entry_parsed_{index};",
                            ]
                        )
                    elif parameter.type.kind == "bool":
                        argument_declarations.extend(
                            [
                                f"    bool {name};",
                                f"    if (strcmp(argv[{index}], \"true\") == 0 || strcmp(argv[{index}], \"1\") == 0) {name} = true;",
                                f"    else if (strcmp(argv[{index}], \"false\") == 0 || strcmp(argv[{index}], \"0\") == 0) {name} = false;",
                                f"    else {{ {failure} }}",
                            ]
                        )
                    elif parameter.type.kind == "uint":
                        maximum = (1 << (parameter.type.bits or 64)) - 1
                        limit = "ULLONG_MAX" if maximum == (1 << 64) - 1 else f"{maximum}ULL"
                        argument_declarations.extend(
                            [
                                f"    errno = 0; char *meldra_entry_end_{index} = NULL;",
                                f"    unsigned long long meldra_entry_parsed_{index} = strtoull(argv[{index}], &meldra_entry_end_{index}, 10);",
                                f"    if (errno == ERANGE || argv[{index}][0] == '-' || meldra_entry_end_{index} == argv[{index}] || *meldra_entry_end_{index} != '\\0' || meldra_entry_parsed_{index} > {limit}) {{ {failure} }}",
                                f"    {ctype} {name} = ({ctype})meldra_entry_parsed_{index};",
                            ]
                        )
                    elif (
                        parameter.type.kind == "record"
                        and parameter.type.record in {"BytesView", "TextView"}
                    ):
                        view_type = (
                            "meldra_bytes_view"
                            if parameter.type.record == "BytesView"
                            else "meldra_text_view"
                        )
                        argument_declarations.append(
                            f"    {ctype} {name} = ({view_type}){{ "
                            f"(const uint8_t *)argv[{index}], "
                            f"(uint64_t)strlen(argv[{index}]) }};"
                        )
                    else:
                        raise NativeBackendError(
                            "runtime entry arguments must be scalar, BytesView, or TextView"
                        )
                arguments = ", ".join(
                    f"meldra_entry_argument_{index}"
                    for index in range(1, len(entry.parameters) + 1)
                )
            else:
                arguments = ", ".join(
                    _literal(value, parameter.type)
                    for value, parameter in zip(self.entry_arguments, entry.parameters, strict=True)
                )
            return_type = entry.return_type
            if return_type.kind not in {"int", "uint", "bool", "float"}:
                raise NativeBackendError("executable entry checksum must be scalar")
            main_declaration = "int main(int argc, char **argv) {" if self.runtime_arguments else "int main(void) {"
            lines.append(main_declaration)
            if self.runtime_arguments:
                lines.append(
                    f"    if (argc != {len(entry.parameters) + 1}) {{ fputs(\"invalid entry argument count\\n\", stderr); return 2; }}"
                )
            lines.extend(argument_declarations)
            lines.append(
                f"    {_ctype(return_type)} result = {self._function_name(entry.name)}({arguments});"
            )
            lines.append(
                "    fprintf(stderr, \"MELDRA_ALLOCATIONS=%\" PRIu64 \"\\n\", meldra_heap_allocations);"
            )
            lines.append(
                "    fprintf(stderr, \"MELDRA_FREES=%\" PRIu64 \" MELDRA_ALLOCATED_BYTES=%\" PRIu64 \" MELDRA_PAYLOAD_COPIES=%\" PRIu64 \" MELDRA_BOUNDS_CHECKS=%\" PRIu64 \"\\n\", meldra_heap_frees, meldra_allocated_bytes, meldra_payload_copies, meldra_bounds_checks);"
            )
            lines.append(
                "    fprintf(stderr, \"MELDRA_BUILDER_REALLOCATIONS=%\" PRIu64 \" MELDRA_BUILDER_GROWTH_COPIED_BYTES=%\" PRIu64 \" MELDRA_BUILDER_EXTEND_COPIED_BYTES=%\" PRIu64 \" MELDRA_BUILDER_FINISH_COPIES=%\" PRIu64 \"\\n\", meldra_builder_reallocations, meldra_builder_growth_copied_bytes, meldra_builder_extend_copied_bytes, meldra_builder_finish_copies);"
            )
            lines.append(
                "    fprintf(stderr, \"MELDRA_TEXT_BUILDER_REQUIRED_APPEND_BYTES=%\" PRIu64 \"\\n\", meldra_text_builder_required_append_bytes);"
            )
            lines.append(
                "    fprintf(stderr, \"MELDRA_JSON_TOKENS=%\" PRIu64 \" MELDRA_JSON_UNESCAPED_STRINGS=%\" PRIu64 \" MELDRA_JSON_ESCAPED_STRINGS=%\" PRIu64 \" MELDRA_JSON_SEMANTIC_OUTPUT_BYTES=%\" PRIu64 \"\\n\", meldra_json_token_count, meldra_json_unescaped_strings, meldra_json_escaped_strings, meldra_json_semantic_output_bytes);"
            )
            if return_type.kind == "float":
                precision = 9 if return_type.bits == 32 else 17
                lines.append(
                    f'    printf("%.{precision}g\\n", (double)result);'
                )
            elif return_type.kind == "int":
                lines.append(
                    '    printf("%" PRIi64 "\\n", (int64_t)result);'
                )
            else:
                lines.append(
                    '    printf("%" PRIu64 "\\n", (uint64_t)result);'
                )
            lines.extend(
                [
                    "    return 0;",
                    "}",
                ]
            )
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class NativeBuildResult:
    status: str
    compiler: str | None
    compiler_version: str | None
    command: tuple[str, ...]
    source_path: str
    binary_path: str | None
    compile_time_ms: float | None
    binary_size: int | None
    source_sha256: str
    binary_sha256: str | None
    stderr: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def find_c_compiler(preferred: str | None = None) -> str | None:
    candidates = (preferred,) if preferred else ("clang", "gcc", "cc")
    for candidate in candidates:
        if candidate and shutil.which(candidate):
            return str(shutil.which(candidate))
    return None


def compiler_version(compiler: str) -> str:
    completed = subprocess.run(
        (compiler, "--version"), capture_output=True, text=True, check=False, timeout=10
    )
    return (completed.stdout or completed.stderr).splitlines()[0]


def compile_c_source(
    source: str,
    *,
    output_dir: str | Path,
    stem: str = "meldra_native",
    compiler: str | None = None,
) -> NativeBuildResult:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    source_path = destination / f"source_{source_digest[:16]}.c"
    binary_path = destination / stem
    source_path.write_text(source, encoding="utf-8")
    selected = find_c_compiler(compiler)
    if selected is None:
        return NativeBuildResult(
            "UNMEASURED_COMPILER_UNAVAILABLE",
            None,
            None,
            (),
            str(source_path),
            None,
            None,
            None,
            source_digest,
            None,
            "No Clang, GCC, or cc executable was found.",
        )
    command = (
        selected,
        "-std=c11",
        "-O3",
        "-fwrapv",
        "-fno-delete-null-pointer-checks",
        "-ffp-contract=off",
        "-fno-ident",
        "-Werror",
        "-Wl,--build-id=none",
        str(source_path),
        "-o",
        str(binary_path),
    )
    environment = dict(os.environ)
    environment.update({"SOURCE_DATE_EPOCH": "0", "LC_ALL": "C", "TZ": "UTC"})
    started = time.perf_counter_ns()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=environment,
    )
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    if completed.returncode != 0:
        return NativeBuildResult(
            "FAILED",
            selected,
            compiler_version(selected),
            command,
            str(source_path),
            None,
            elapsed_ms,
            None,
            source_digest,
            None,
            completed.stderr,
        )
    raw = binary_path.read_bytes()
    return NativeBuildResult(
        "MEASURED",
        selected,
        compiler_version(selected),
        command,
        str(source_path),
        str(binary_path),
        elapsed_ms,
        len(raw),
        source_digest,
        hashlib.sha256(raw).hexdigest(),
        completed.stderr,
    )


def compile_native(
    mir: PerformanceMIR,
    *,
    output_dir: str | Path,
    entry_arguments: Iterable[int | float | bool] = (),
    stem: str = "meldra_native",
    compiler: str | None = None,
    runtime_arguments: bool = False,
) -> NativeBuildResult:
    source = CEmitter(
        mir,
        entry_arguments=entry_arguments,
        runtime_arguments=runtime_arguments,
    ).emit()
    return compile_c_source(
        source,
        output_dir=output_dir,
        stem=stem,
        compiler=compiler,
    )


__all__ = [
    "CEmitter",
    "NATIVE_BACKEND_IMPLEMENTATION_VERSION",
    "NATIVE_BACKEND_SCHEMA_VERSION",
    "NativeBackendError",
    "NativeBuildResult",
    "compile_c_source",
    "compile_native",
    "compiler_version",
    "find_c_compiler",
]
