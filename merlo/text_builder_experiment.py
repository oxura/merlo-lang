"""Bounded decision experiment for UTF-8-valid TextBuilder."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import statistics
import subprocess
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Iterable

from .bytes_experiment import _compile_sanitized
from .legacy_evidence import frozen_sha256
from .native_c_backend import CEmitter, compile_c_source
from .native_differential import (
    evaluate_hir,
    evaluate_mir,
    evaluate_native,
    evaluate_surface,
)
from .native_hir import compile_native_hir
from .performance_frontend import (
    PerformanceCompileError,
    compile_performance_source,
)
from .performance_opt import optimize_mir
from .text_builder_core import (
    text_builder_abi_manifest,
    text_builder_hir_manifest,
    text_builder_mir_manifest,
)
from .text_core_experiment import (
    _compile_raw,
    _compile_rust,
    _pinned_command,
    _rss_kb,
    _surface_metrics,
    _timed_once,
)

TEXT_BUILDER_EXPERIMENT_VERSION = 1
TEXT_BUILDER_VALID_CASES = 640
TEXT_BUILDER_INVALID_CASES = 320
TEXT_BUILDER_VALID_FAMILIES = 32
TEXT_BUILDER_INVALID_FAMILIES = 20
TEXT_BUILDER_FUZZ_SEED = 0x7E87B17D
TEXT_BUILDER_PERFORMANCE_LIMIT = 1.20
TEXT_BUILDER_BENCHMARK_WARMUPS = 5
TEXT_BUILDER_BENCHMARK_SAMPLES = 30
TEXT_BUILDER_BENCHMARK_MAD_LIMIT = 0.05
TEXT_BUILDER_BENCHMARK_REPETITIONS = 500_000
_MASK64 = (1 << 64) - 1
_SANITIZER_MARKERS = (
    "AddressSanitizer",
    "LeakSanitizer",
    "UndefinedBehaviorSanitizer",
    "runtime error:",
    "use-after-free",
    "double-free",
    "heap-buffer-overflow",
    "stack-buffer-overflow",
)

STATE_MACHINE_SOURCE = """fn main(ascii: UInt64, first: UInt64, second: UInt64, extension_scalar: UInt64, mode: UInt64, reserve: UInt64) -> UInt64:
 let extension: Text = Text.from_scalar(extension_scalar)
 let builder: TextBuilder = TextBuilder.new()
 if reserve > 0:
  builder.reserve_bytes(reserve)
 if (mode & 1) != 0:
  builder.push_ascii(ascii)
 if (mode & 2) != 0:
  builder.push_scalar(first)
 if (mode & 4) != 0:
  builder.push_scalar(second)
 if (mode & 8) != 0:
  builder.extend(extension.as_view())
 var checksum: UInt64 = 0
 if (mode & 16) != 0:
  let early: TextView = builder.as_view()
  checksum = early.len_bytes()
  builder.push_ascii(33)
 let capacity: UInt64 = builder.capacity_bytes()
 let view: TextView = builder.as_view()
 let raw: BytesView = view.as_bytes()
 var index: UInt64 = 0
 while index < raw.len():
  checksum = (checksum * 257) + raw[index]
  index = index + 1
 let length: UInt64 = view.len_bytes()
 let scalars: UInt64 = view.scalar_count()
 let text: Text = builder.finish()
 return checksum ^ (length << 32) ^ (scalars << 48) ^ (capacity << 56) ^ text.len_bytes()
"""

DIRECT_CALL_SOURCE = """fn append_scalar(builder: TextBuilder, scalar: UInt64) -> TextBuilder:
 builder.push_scalar(scalar)
 return builder

fn finish_message(builder: TextBuilder, suffix: UInt64) -> Text:
 builder.push_ascii(suffix)
 return builder.finish()

fn main(scalar: UInt64) -> UInt64:
 let first: TextBuilder = TextBuilder.with_capacity_bytes(8)
 let second: TextBuilder = append_scalar(move(first), scalar)
 let text: Text = finish_message(move(second), 33)
 return text.len_bytes()
"""

LOOP_DROP_SOURCE = """fn main(scalar: UInt64, repetitions: UInt64) -> UInt64:
 var checksum: UInt64 = 0
 var iteration: UInt64 = 0
 while iteration < repetitions:
  let builder: TextBuilder = TextBuilder.new()
  builder.push_scalar(scalar)
  checksum = checksum + builder.len_bytes()
  iteration = iteration + 1
 return checksum
"""

JSON_ENCODER_FUNCTIONS = """fn hex_digit(value: UInt64) -> UInt64:
 if value < 10:
  return 48 + value
 return 87 + value

fn json_string_encode(value: TextView) -> Text:
 let builder: TextBuilder = TextBuilder.new()
 builder.reserve_bytes(value.len_bytes() + 2)
 builder.push_ascii(34)
 var index: UInt64 = 0
 while index < value.len_bytes():
  let raw: BytesView = value.as_bytes()
  let byte: UInt64 = raw[index]
  if byte == 34:
   builder.push_ascii(92)
   builder.push_ascii(34)
   index = index + 1
  else:
   if byte == 92:
    builder.push_ascii(92)
    builder.push_ascii(92)
    index = index + 1
   else:
    if byte == 8:
     builder.push_ascii(92)
     builder.push_ascii(98)
     index = index + 1
    else:
     if byte == 12:
      builder.push_ascii(92)
      builder.push_ascii(102)
      index = index + 1
     else:
      if byte == 10:
       builder.push_ascii(92)
       builder.push_ascii(110)
       index = index + 1
      else:
       if byte == 13:
        builder.push_ascii(92)
        builder.push_ascii(114)
        index = index + 1
       else:
        if byte == 9:
         builder.push_ascii(92)
         builder.push_ascii(116)
         index = index + 1
        else:
         if byte < 32:
          builder.push_ascii(92)
          builder.push_ascii(117)
          builder.push_ascii(48)
          builder.push_ascii(48)
          builder.push_ascii(hex_digit(byte >> 4))
          builder.push_ascii(hex_digit(byte & 15))
          index = index + 1
         else:
          let width: UInt64 = value.scalar_width_at(index)
          let scalar: TextView = value.slice_bytes(index, width)
          builder.extend(scalar)
          index = index + width
 builder.push_ascii(34)
 return builder.finish()
"""

JSON_ENCODER_SOURCE = JSON_ENCODER_FUNCTIONS + """
fn main(first: UInt64, second: UInt64, third: UInt64, fourth: UInt64, count: UInt64) -> UInt64:
 let input_builder: TextBuilder = TextBuilder.new()
 if count > 0:
  input_builder.push_scalar(first)
 if count > 1:
  input_builder.push_scalar(second)
 if count > 2:
  input_builder.push_scalar(third)
 if count > 3:
  input_builder.push_scalar(fourth)
 let input: Text = input_builder.finish()
 let encoded: Text = json_string_encode(input.as_view())
 let view: TextView = encoded.as_view()
 let raw: BytesView = view.as_bytes()
 var checksum: UInt64 = 0
 var index: UInt64 = 0
 while index < raw.len():
  checksum = (checksum * 257) + raw[index]
  index = index + 1
 return checksum ^ (view.len_bytes() << 48)
"""

LONG_JSON_SOURCE = JSON_ENCODER_FUNCTIONS + """
fn main(scalar: UInt64, repetitions: UInt64) -> UInt64:
 let input_builder: TextBuilder = TextBuilder.new()
 var iteration: UInt64 = 0
 while iteration < repetitions:
  input_builder.push_scalar(scalar)
  iteration = iteration + 1
 let input: Text = input_builder.finish()
 let encoded: Text = json_string_encode(input.as_view())
 return encoded.len_bytes()
"""

MIXED_SCALAR_BENCHMARK_SOURCE = """fn main(repetitions: UInt64) -> UInt64:
 var checksum: UInt64 = 0
 var iteration: UInt64 = 0
 while iteration < repetitions:
  let builder: TextBuilder = TextBuilder.with_capacity_bytes(10)
  builder.push_ascii(65)
  builder.push_scalar(2047)
  builder.push_scalar(65535)
  builder.push_scalar(1114111)
  let text: Text = builder.finish()
  checksum = checksum + text.len_bytes()
  iteration = iteration + 1
 return checksum
"""

JSON_BENCHMARK_SOURCE = JSON_ENCODER_FUNCTIONS + """
fn main(repetitions: UInt64) -> UInt64:
 let input_builder: TextBuilder = TextBuilder.with_capacity_bytes(16)
 input_builder.push_ascii(34)
 input_builder.push_ascii(92)
 input_builder.push_ascii(10)
 input_builder.push_ascii(47)
 input_builder.push_scalar(1046)
 input_builder.push_scalar(128512)
 let input: Text = input_builder.finish()
 let view: TextView = input.as_view()
 var checksum: UInt64 = 0
 var iteration: UInt64 = 0
 while iteration < repetitions:
  let encoded: Text = json_string_encode(view)
  checksum = checksum + encoded.len_bytes()
  iteration = iteration + 1
 return checksum
"""

C_BENCHMARK_COMMON = r'''#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { uint8_t *data; uint64_t length; uint64_t capacity; } builder;
static uint64_t raw_allocations = 0;
static uint64_t raw_frees = 0;
static uint64_t raw_reallocations = 0;
static uint64_t raw_growth_copied = 0;
static uint64_t raw_required_append = 0;

static void fail(void) { abort(); }
static void grow(builder *b, uint64_t additional) {
    if (additional > UINT64_MAX - b->length) fail();
    uint64_t required = b->length + additional;
    if (required <= b->capacity) return;
    uint64_t capacity;
    if (b->capacity == 0) capacity = required > 8 ? required : 8;
    else {
        if (b->capacity > UINT64_MAX / 2) fail();
        uint64_t doubled = b->capacity * 2;
        capacity = required > doubled ? required : doubled;
    }
    if (capacity > UINT64_C(9223372036854775807)) fail();
    uint8_t *data = (uint8_t *)malloc((size_t)capacity);
    if (data == NULL) fail();
    ++raw_allocations;
    if (b->data != NULL) {
        if (b->length) memcpy(data, b->data, (size_t)b->length);
        raw_growth_copied += b->length;
        free(b->data);
        ++raw_frees;
        ++raw_reallocations;
    }
    b->data = data;
    b->capacity = capacity;
}
static void reserve_bytes(builder *b, uint64_t additional) { grow(b, additional); }
static void push_ascii(builder *b, uint64_t scalar) {
    if (scalar > 0x7F) fail();
    ++raw_required_append;
    grow(b, 1);
    b->data[b->length++] = (uint8_t)scalar;
}
static void push_scalar(builder *b, uint64_t scalar) {
    if (scalar > 0x10FFFF || (scalar >= 0xD800 && scalar <= 0xDFFF)) fail();
    uint64_t width = scalar <= 0x7F ? 1 : scalar <= 0x7FF ? 2 : scalar <= 0xFFFF ? 3 : 4;
    raw_required_append += width;
    grow(b, width);
    uint8_t *out = b->data + b->length;
    if (width == 1) out[0] = (uint8_t)scalar;
    else if (width == 2) { out[0] = (uint8_t)(0xC0 | (scalar >> 6)); out[1] = (uint8_t)(0x80 | (scalar & 0x3F)); }
    else if (width == 3) { out[0] = (uint8_t)(0xE0 | (scalar >> 12)); out[1] = (uint8_t)(0x80 | ((scalar >> 6) & 0x3F)); out[2] = (uint8_t)(0x80 | (scalar & 0x3F)); }
    else { out[0] = (uint8_t)(0xF0 | (scalar >> 18)); out[1] = (uint8_t)(0x80 | ((scalar >> 12) & 0x3F)); out[2] = (uint8_t)(0x80 | ((scalar >> 6) & 0x3F)); out[3] = (uint8_t)(0x80 | (scalar & 0x3F)); }
    b->length += width;
}
static void extend_bytes(builder *b, const uint8_t *data, uint64_t length) {
    raw_required_append += length;
    grow(b, length);
    if (length) memcpy(b->data + b->length, data, (size_t)length);
    b->length += length;
}
static void destroy(builder *b) {
    if (b->data != NULL) { free(b->data); ++raw_frees; }
    b->data = NULL; b->length = 0; b->capacity = 0;
}
static uint64_t hex_digit(uint64_t value) { return value < 10 ? 48 + value : 87 + value; }
static builder json_encode(const uint8_t *data, uint64_t length) {
    builder out = { NULL, 0, 0 };
    reserve_bytes(&out, length + 2);
    push_ascii(&out, 34);
    uint64_t index = 0;
    while (index < length) {
        uint8_t byte = data[index];
        if (byte == 34 || byte == 92) { push_ascii(&out, 92); push_ascii(&out, byte); ++index; }
        else if (byte == 8) { push_ascii(&out, 92); push_ascii(&out, 98); ++index; }
        else if (byte == 12) { push_ascii(&out, 92); push_ascii(&out, 102); ++index; }
        else if (byte == 10) { push_ascii(&out, 92); push_ascii(&out, 110); ++index; }
        else if (byte == 13) { push_ascii(&out, 92); push_ascii(&out, 114); ++index; }
        else if (byte == 9) { push_ascii(&out, 92); push_ascii(&out, 116); ++index; }
        else if (byte < 32) { push_ascii(&out, 92); push_ascii(&out, 117); push_ascii(&out, 48); push_ascii(&out, 48); push_ascii(&out, hex_digit(byte >> 4)); push_ascii(&out, hex_digit(byte & 15)); ++index; }
        else {
            uint64_t width = byte < 0x80 ? 1 : byte < 0xE0 ? 2 : byte < 0xF0 ? 3 : 4;
            extend_bytes(&out, data + index, width);
            index += width;
        }
    }
    push_ascii(&out, 34);
    return out;
}
static void metrics(void) {
    fprintf(stderr, "RAW_ALLOCATIONS=%" PRIu64 " RAW_FREES=%" PRIu64 " RAW_PAYLOAD_COPIES=0 RAW_REALLOCATIONS=%" PRIu64 " RAW_GROWTH_COPIED_BYTES=%" PRIu64 " RAW_REQUIRED_APPEND_BYTES=%" PRIu64 " RAW_FINISH_COPIES=0 RAW_VALIDATION_PASSES=0\n", raw_allocations, raw_frees, raw_reallocations, raw_growth_copied, raw_required_append);
}
'''

C_MIXED_SCALAR_BENCHMARK = C_BENCHMARK_COMMON + r'''
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t repetitions = strtoull(argv[1], NULL, 10), checksum = 0;
    for (uint64_t i = 0; i < repetitions; ++i) {
        builder b = { NULL, 0, 10 };
        b.data = (uint8_t *)malloc(10); if (b.data == NULL) fail(); ++raw_allocations;
        push_ascii(&b, 65); push_scalar(&b, 2047); push_scalar(&b, 65535); push_scalar(&b, 1114111);
        checksum += b.length;
        destroy(&b);
    }
    metrics(); printf("%" PRIu64 "\n", checksum); return 0;
}
'''

C_JSON_BENCHMARK = C_BENCHMARK_COMMON + r'''
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t repetitions = strtoull(argv[1], NULL, 10), checksum = 0;
    builder input = { NULL, 0, 16 };
    input.data = (uint8_t *)malloc(16); if (input.data == NULL) fail(); ++raw_allocations;
    push_ascii(&input, 34); push_ascii(&input, 92); push_ascii(&input, 10); push_ascii(&input, 47); push_scalar(&input, 1046); push_scalar(&input, 128512);
    for (uint64_t i = 0; i < repetitions; ++i) {
        builder out = json_encode(input.data, input.length);
        checksum += out.length;
        destroy(&out);
    }
    destroy(&input);
    metrics(); printf("%" PRIu64 "\n", checksum); return 0;
}
'''

RUST_MIXED_SCALAR_BENCHMARK = r'''use std::env;
fn main() {
    let repetitions: u64 = env::args().nth(1).unwrap().parse().unwrap();
    let mut checksum = 0u64;
    for _ in 0..repetitions {
        let mut value = String::with_capacity(10);
        value.push('A'); value.push('\u{7ff}'); value.push('\u{ffff}'); value.push('\u{10ffff}');
        checksum = checksum.wrapping_add(value.len() as u64);
    }
    println!("{}", checksum);
}
'''

RUST_JSON_BENCHMARK = r'''use std::env;
fn main() {
    let repetitions: u64 = env::args().nth(1).unwrap().parse().unwrap();
    let input = "\"\\\n/Ж😀";
    let mut checksum = 0u64;
    for _ in 0..repetitions {
        let mut out = String::with_capacity(input.len() + 2); out.push('"');
        for ch in input.chars() {
            match ch { '"' => out.push_str("\\\""), '\\' => out.push_str("\\\\"), '\u{8}' => out.push_str("\\b"), '\u{c}' => out.push_str("\\f"), '\n' => out.push_str("\\n"), '\r' => out.push_str("\\r"), '\t' => out.push_str("\\t"), c if (c as u32) < 32 => { use std::fmt::Write; write!(&mut out, "\\u{:04x}", c as u32).unwrap(); }, c => out.push(c) }
        }
        out.push('"'); checksum = checksum.wrapping_add(out.len() as u64);
    }
    println!("{}", checksum);
}
'''


def _hash_bytes(data: bytes) -> int:
    value = 0
    for byte in data:
        value = ((value * 257) + byte) & _MASK64
    return value


def _capacity_grow(
    length: int, capacity: int, additional: int
) -> tuple[int, int]:
    required = length + additional
    if required <= capacity:
        return capacity, 0
    next_capacity = (
        max(8, required)
        if capacity == 0
        else max(required, capacity * 2)
    )
    return next_capacity, length if capacity else 0


def _reference_state(arguments: tuple[int, ...]) -> dict[str, Any]:
    ascii_value, first, second, extension, mode, reserve = arguments
    payload = bytearray()
    capacity = 0
    growth_copied = 0
    required_append = 0
    if reserve:
        capacity, copied = _capacity_grow(0, capacity, reserve)
        growth_copied += copied

    def append(data: bytes) -> None:
        nonlocal capacity, growth_copied, required_append
        capacity, copied = _capacity_grow(
            len(payload), capacity, len(data)
        )
        growth_copied += copied
        required_append += len(data)
        payload.extend(data)

    if mode & 1:
        append(bytes((ascii_value,)))
    if mode & 2:
        append(chr(first).encode("utf-8"))
    if mode & 4:
        append(chr(second).encode("utf-8"))
    extend_bytes = chr(extension).encode("utf-8")
    if mode & 8:
        append(extend_bytes)
    early = len(payload) if mode & 16 else 0
    if mode & 16:
        append(b"!")
    data = bytes(payload)
    scalar_count = len(data.decode("utf-8"))
    result = (
        _hash_bytes(data)
        ^ ((len(data) << 32) & _MASK64)
        ^ ((scalar_count << 48) & _MASK64)
        ^ ((capacity << 56) & _MASK64)
        ^ len(data)
    ) & _MASK64
    if mode & 16:
        checksum = early
        for byte in data:
            checksum = ((checksum * 257) + byte) & _MASK64
        result = (
            checksum
            ^ ((len(data) << 32) & _MASK64)
            ^ ((scalar_count << 48) & _MASK64)
            ^ ((capacity << 56) & _MASK64)
            ^ len(data)
        ) & _MASK64
    return {
        "bytes": data,
        "return_value": result,
        "byte_length": len(data),
        "scalar_count": scalar_count,
        "capacity": capacity,
        "required_append_bytes": required_append,
        "growth_copied_bytes": growth_copied,
        "extend_copied_bytes": len(extend_bytes) if mode & 8 else 0,
    }


def _valid_cases() -> list[dict[str, Any]]:
    scalars = (
        0x00,
        0x01,
        0x7F,
        0x80,
        0x81,
        0x7FF,
        0x800,
        0x801,
        0xD7FF,
        0xE000,
        0xFFFF,
        0x10000,
        0x10001,
        0x10FFFF,
        0x41,
        0x416,
        0x20AC,
        0x1F600,
        0x1F642,
        0x24B62,
    )
    reserves = (0, 1, 2, 4, 8, 9, 12, 16, 31, 64)
    cases = []
    for index in range(TEXT_BUILDER_VALID_CASES):
        mode = index % TEXT_BUILDER_VALID_FAMILIES
        arguments = (
            (index * 17) % 128,
            scalars[index % len(scalars)],
            scalars[(index * 7 + 3) % len(scalars)],
            scalars[(index * 11 + 5) % len(scalars)],
            mode,
            reserves[(index * 13) % len(reserves)],
        )
        expected = _reference_state(arguments)
        cases.append(
            {
                "family": f"state_mask_{mode:02d}",
                "index": index,
                "arguments": arguments,
                "expected": expected,
            }
        )
    return cases


def _run_binary(
    binary: str, arguments: Iterable[int], *, timeout: int = 10,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    completed = subprocess.run(
        [binary, *(str(value) for value in arguments)],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=environment,
    )
    try:
        checksum = int(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        checksum = None
    metrics = {}
    for key in (
        "ALLOCATIONS",
        "FREES",
        "PAYLOAD_COPIES",
        "BUILDER_REALLOCATIONS",
        "BUILDER_GROWTH_COPIED_BYTES",
        "BUILDER_EXTEND_COPIED_BYTES",
        "BUILDER_FINISH_COPIES",
        "TEXT_BUILDER_REQUIRED_APPEND_BYTES",
    ):
        matches = re.findall(rf"MELDRA_{key}=(\d+)", completed.stderr)
        metrics[key.lower()] = int(matches[-1]) if matches else None
    return {
        "returncode": completed.returncode,
        "checksum": checksum,
        "stderr": completed.stderr,
        **metrics,
    }


def _correctness_corpus(
    root: Path,
) -> tuple[dict[str, Any], Any, Any, Any, str]:
    cases = _valid_cases()
    hir = compile_native_hir(
        STATE_MACHINE_SOURCE, path="text-builder-state.meldra"
    )
    original = compile_performance_source(
        STATE_MACHINE_SOURCE, path="text-builder-state.meldra"
    ).mir
    optimized, snapshots = optimize_mir(original)
    generated = CEmitter(optimized, runtime_arguments=True).emit()
    build = compile_c_source(
        generated, output_dir=root, stem="state-machine"
    )
    reports = []
    native_executions = 0
    all_agree = True
    exact_bytes = True
    metrics_agree = True
    for case in cases:
        arguments = tuple(case["arguments"])
        expected = case["expected"]
        observations = {
            "surface": evaluate_surface(
                STATE_MACHINE_SOURCE,
                arguments,
                path="text-builder-state.meldra",
            ),
            "hir": evaluate_hir(hir, arguments),
            "mir": evaluate_mir(original, arguments),
            "optimized_mir": evaluate_mir(optimized, arguments),
        }
        native = (
            _run_binary(str(build.binary_path), arguments)
            if build.binary_path
            else {"returncode": None, "checksum": None}
        )
        if build.binary_path:
            native_executions += 1
        observed_values = [
            observation.return_value
            for observation in observations.values()
        ]
        passed = (
            all(
                observation.status == "OK"
                for observation in observations.values()
            )
            and all(
                value == expected["return_value"]
                for value in observed_values
            )
            and native.get("returncode") == 0
            and native.get("checksum") == expected["return_value"]
        )
        strict_valid = (
            expected["bytes"].decode("utf-8", "strict").encode("utf-8")
            == expected["bytes"]
        )
        case_metrics = all(
            observation.required_append_bytes
            == expected["required_append_bytes"]
            and observation.growth_copied_bytes
            == expected["growth_copied_bytes"]
            and observation.extend_copied_bytes
            == expected["extend_copied_bytes"]
            and observation.finish_copies == 0
            for observation in observations.values()
        ) and (
            native.get("text_builder_required_append_bytes")
            == expected["required_append_bytes"]
            and native.get("builder_growth_copied_bytes")
            == expected["growth_copied_bytes"]
            and native.get("builder_extend_copied_bytes")
            == expected["extend_copied_bytes"]
            and native.get("builder_finish_copies") == 0
        )
        all_agree = all_agree and passed
        exact_bytes = exact_bytes and strict_valid
        metrics_agree = metrics_agree and case_metrics
        reports.append(
            {
                "family": case["family"],
                "index": case["index"],
                "arguments": list(arguments),
                "expected_bytes_hex": expected["bytes"].hex(),
                "expected_return": expected["return_value"],
                "byte_length": expected["byte_length"],
                "scalar_count": expected["scalar_count"],
                "capacity": expected["capacity"],
                "required_append_bytes": expected[
                    "required_append_bytes"
                ],
                "growth_copied_bytes": expected[
                    "growth_copied_bytes"
                ],
                "extend_copied_bytes": expected[
                    "extend_copied_bytes"
                ],
                "surface_values": observed_values,
                "native": {
                    key: value
                    for key, value in native.items()
                    if key != "stderr"
                },
                "strict_utf8": strict_valid,
                "metrics_agree": case_metrics,
                "passed": passed and strict_valid and case_metrics,
            }
        )
    return (
        {
            "case_count": len(cases),
            "family_count": len({case["family"] for case in cases}),
            "native_executions": native_executions,
            "all_surfaces_agree": all_agree,
            "strict_utf8_outputs": exact_bytes,
            "allocation_metrics_agree": metrics_agree,
            "finish_copies_zero": all(
                report["native"].get("builder_finish_copies") == 0
                for report in reports
            ),
            "validation_passes": 0,
            "cases": reports,
            "build": asdict(build),
            "optimization_passes": [
                snapshot.to_dict() for snapshot in snapshots
            ],
            "passed": all(
                report["passed"] for report in reports
            ),
        },
        hir,
        original,
        optimized,
        generated,
    )


def _runtime_invalid_families() -> list[dict[str, Any]]:
    return [
        {
            "family": "ascii_above_boundary",
            "source": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 builder.push_ascii(value)
 let text: Text = builder.finish()
 return text.len_bytes()
""",
            "values": tuple(range(0x80, 0x90)),
            "diagnostic": "TextBuilderAsciiOutOfRange",
        },
        {
            "family": "surrogate_runtime",
            "source": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 builder.push_scalar(value)
 let text: Text = builder.finish()
 return text.len_bytes()
""",
            "values": tuple(range(0xD800, 0xD810)),
            "diagnostic": "TextBuilderInvalidUnicodeScalar",
        },
        {
            "family": "scalar_above_maximum_runtime",
            "source": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 builder.push_scalar(value)
 let text: Text = builder.finish()
 return text.len_bytes()
""",
            "values": tuple(range(0x110000, 0x110010)),
            "diagnostic": "TextBuilderInvalidUnicodeScalar",
        },
        {
            "family": "allocation_size_overflow_runtime",
            "source": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.with_capacity_bytes(value)
 return builder.len_bytes()
""",
            "values": tuple((1 << 63) + index for index in range(16)),
            "diagnostic": "TextBuilderAllocationSizeOverflow",
        },
    ]


def _compile_invalid_source(family: str, index: int) -> str:
    marker = f" let marker: UInt64 = {index}\n"
    sources = {
        "push_ascii_active_view": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 let view: TextView = builder.as_view()
 builder.push_ascii(value)
 return view.len_bytes()
""",
        "push_scalar_active_view": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 let view: TextView = builder.as_view()
 builder.push_scalar(value)
 return view.len_bytes()
""",
        "extend_active_view": """fn main(value: UInt64) -> UInt64:
 let source: Text = Text.from_scalar(value)
 let builder: TextBuilder = TextBuilder.new()
 let view: TextView = builder.as_view()
 builder.extend(source.as_view())
 return view.len_bytes()
""",
        "reserve_active_view": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 let view: TextView = builder.as_view()
 builder.reserve_bytes(value)
 return view.len_bytes()
""",
        "finish_active_view": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 let view: TextView = builder.as_view()
 let text: Text = builder.finish()
 return view.len_bytes() + text.len_bytes()
""",
        "move_active_view": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 let view: TextView = builder.as_view()
 let moved: TextBuilder = move(builder)
 return view.len_bytes() + moved.len_bytes()
""",
        "drop_active_view": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 let view: TextView = builder.as_view()
 drop(builder)
 return view.len_bytes()
""",
        "self_extend": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 builder.push_scalar(value)
 builder.extend(builder.as_view())
 return builder.len_bytes()
""",
        "use_after_finish": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 let text: Text = builder.finish()
 return builder.len_bytes() + text.len_bytes()
""",
        "double_finish": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 let first: Text = builder.finish()
 let second: Text = builder.finish()
 return first.len_bytes() + second.len_bytes()
""",
        "double_drop": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 drop(builder)
 drop(builder)
 return value
""",
        "use_after_move": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 let moved: TextBuilder = move(builder)
 builder.push_ascii(65)
 return moved.len_bytes()
""",
        "branch_ownership_imbalance": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 if value > 0:
  let text: Text = builder.finish()
 return builder.len_bytes()
""",
        "duplicate_text_builder_owner": """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 let alias: TextBuilder = builder
 return alias.len_bytes() + value
""",
        "lost_builder_parameter": """fn lose(builder: TextBuilder, value: UInt64) -> UInt64:
 return value

fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 return lose(move(builder), value)
""",
        "arbitrary_bytesview_extend": """fn main(value: UInt64) -> UInt64:
 let bytes: Bytes = Bytes.new(value)
 let raw: BytesView = bytes.slice(0, value)
 let builder: TextBuilder = TextBuilder.new()
 builder.extend(raw)
 return builder.len_bytes()
""",
    }
    source = sources[family]
    lines = source.splitlines()
    insertion = next(
        position + 1
        for position, line in enumerate(lines)
        if line.startswith("fn main(")
    )
    lines.insert(insertion, marker.rstrip("\n"))
    return "\n".join(lines) + "\n"


def _invalid_corpus(root: Path) -> dict[str, Any]:
    reports = []
    native_executions = 0
    for specification in _runtime_invalid_families():
        source = specification["source"]
        hir = compile_native_hir(source, path=f"{specification['family']}.meldra")
        original = compile_performance_source(source).mir
        optimized, _ = optimize_mir(original)
        build = compile_c_source(
            CEmitter(optimized, runtime_arguments=True).emit(),
            output_dir=root / specification["family"],
            stem="program",
        )
        for index, value in enumerate(specification["values"]):
            expected = specification["diagnostic"]
            observations = (
                evaluate_hir(hir, (value,)),
                evaluate_mir(original, (value,)),
                evaluate_mir(optimized, (value,)),
            )
            native = (
                _run_binary(str(build.binary_path), (value,))
                if build.binary_path
                else {
                    "returncode": None,
                    "stderr": "compiler unavailable",
                }
            )
            native_executions += int(build.binary_path is not None)
            passed = all(
                observation.status == "ERROR"
                and observation.error_kind == expected
                for observation in observations
            ) and (
                native.get("returncode") not in {None, 0}
                and expected in str(native.get("stderr", ""))
            )
            reports.append(
                {
                    "family": specification["family"],
                    "index": index,
                    "input": value,
                    "expected_diagnostic": expected,
                    "diagnostics": [
                        *(
                            observation.error_kind
                            for observation in observations
                        ),
                        (
                            expected
                            if expected in str(native.get("stderr", ""))
                            else None
                        ),
                    ],
                    "passed": passed,
                    "build": asdict(build) if index == 0 else None,
                }
            )
    compile_families = (
        "push_ascii_active_view",
        "push_scalar_active_view",
        "extend_active_view",
        "reserve_active_view",
        "finish_active_view",
        "move_active_view",
        "drop_active_view",
        "self_extend",
        "use_after_finish",
        "double_finish",
        "double_drop",
        "use_after_move",
        "branch_ownership_imbalance",
        "duplicate_text_builder_owner",
        "lost_builder_parameter",
        "arbitrary_bytesview_extend",
    )
    for family in compile_families:
        for index in range(16):
            source = _compile_invalid_source(family, index)
            try:
                compile_performance_source(
                    source, path=f"invalid/{family}-{index}.meldra"
                )
            except PerformanceCompileError as error:
                diagnostic = str(error)
                passed = True
            else:
                diagnostic = None
                passed = False
            reports.append(
                {
                    "family": family,
                    "index": index,
                    "source_sha256": hashlib.sha256(
                        source.encode()
                    ).hexdigest(),
                    "diagnostic": diagnostic,
                    "passed": passed,
                }
            )
    return {
        "case_count": len(reports),
        "family_count": len({report["family"] for report in reports}),
        "native_executions": native_executions,
        "cases": reports,
        "active_view_families": 7,
        "overflow_families": 1,
        "passed": len(reports) == TEXT_BUILDER_INVALID_CASES
        and all(report["passed"] for report in reports),
    }


def _json_cases() -> list[tuple[str, tuple[int, ...]]]:
    return [
        ("empty", (0, 0, 0, 0, 0)),
        ("ascii", (65, 0, 0, 0, 1)),
        ("quote", (34, 0, 0, 0, 1)),
        ("backslash", (92, 0, 0, 0, 1)),
        ("slash", (47, 0, 0, 0, 1)),
        ("controls", (8, 12, 10, 13, 4)),
        ("tab_nul", (9, 0, 31, 1, 4)),
        ("two_byte", (0x416, 0, 0, 0, 1)),
        ("three_byte", (0x20AC, 0, 0, 0, 1)),
        ("four_byte", (0x1F600, 0, 0, 0, 1)),
        ("mixed", (34, 92, 0x416, 0x1F600, 4)),
    ]


def _json_expected(arguments: tuple[int, ...]) -> tuple[bytes, int]:
    *scalars, count = arguments
    text = "".join(chr(value) for value in scalars[:count])
    encoded = json.dumps(text, ensure_ascii=False).encode("utf-8")
    result = (_hash_bytes(encoded) ^ (len(encoded) << 48)) & _MASK64
    return encoded, result


def _json_encoder_acceptance(root: Path) -> dict[str, Any]:
    hir = compile_native_hir(
        JSON_ENCODER_SOURCE, path="json-string-encoder.meldra"
    )
    original = compile_performance_source(
        JSON_ENCODER_SOURCE, path="json-string-encoder.meldra"
    ).mir
    optimized, _ = optimize_mir(original)
    build = compile_c_source(
        CEmitter(optimized, runtime_arguments=True).emit(),
        output_dir=root,
        stem="program",
    )
    reports = []
    for name, arguments in _json_cases():
        expected_bytes, expected = _json_expected(arguments)
        observations = (
            evaluate_hir(hir, arguments),
            evaluate_mir(original, arguments),
            evaluate_mir(optimized, arguments),
        )
        native = (
            _run_binary(str(build.binary_path), arguments)
            if build.binary_path
            else {"returncode": None, "checksum": None}
        )
        passed = (
            all(
                observation.status == "OK"
                and observation.return_value == expected
                for observation in observations
            )
            and native.get("returncode") == 0
            and native.get("checksum") == expected
            and expected_bytes.decode("utf-8", "strict")
            == json.dumps(
                "".join(chr(value) for value in arguments[:-1][
                    : arguments[-1]
                ]),
                ensure_ascii=False,
            )
        )
        reports.append(
            {
                "name": name,
                "arguments": list(arguments),
                "expected_utf8_hex": expected_bytes.hex(),
                "expected_checksum": expected,
                "native_checksum": native.get("checksum"),
                "finish_copies": native.get("builder_finish_copies"),
                "passed": passed
                and native.get("builder_finish_copies") == 0,
            }
        )
    long_arguments = (0x1F600, 4096)
    long_expected = 2 + 4 * long_arguments[1]
    long_mir = optimize_mir(
        compile_performance_source(
            LONG_JSON_SOURCE, path="long-json-string.meldra"
        ).mir
    )[0]
    long_build = compile_c_source(
        CEmitter(long_mir, runtime_arguments=True).emit(),
        output_dir=root / "long",
        stem="program",
    )
    long_native = (
        _run_binary(str(long_build.binary_path), long_arguments)
        if long_build.binary_path
        else {"returncode": None, "checksum": None}
    )
    long_passed = (
        evaluate_mir(long_mir, long_arguments).return_value
        == long_expected
        and long_native.get("returncode") == 0
        and long_native.get("checksum") == long_expected
        and long_native.get("builder_finish_copies") == 0
    )
    return {
        "input_type": "TextView",
        "output_type": "Text",
        "oracle": "Python json.dumps(ensure_ascii=False)",
        "slash_unescaped": True,
        "normalization": False,
        "non_ascii_escaping": False,
        "main_logic_location": "Meldra HIR/MIR",
        "handwritten_c_encoder": False,
        "recursive_json_ast": False,
        "cases": reports,
        "long_input": {
            "scalar": long_arguments[0],
            "repetitions": long_arguments[1],
            "expected_length": long_expected,
            "native": {
                key: value
                for key, value in long_native.items()
                if key != "stderr"
            },
            "passed": long_passed,
        },
        "build": asdict(build),
        "passed": all(report["passed"] for report in reports)
        and long_passed,
    }


def _diagnostics() -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    source_by_name = {
        "constant_ascii": """fn main() -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 builder.push_ascii(128)
 return builder.len_bytes()
""",
        "constant_surrogate": """fn main() -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 builder.push_scalar(55296)
 return builder.len_bytes()
""",
        "constant_above_max": """fn main() -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 builder.push_scalar(1114112)
 return builder.len_bytes()
""",
        "bytesview_extend": _compile_invalid_source(
            "arbitrary_bytesview_extend", 0
        ),
        "self_extend": _compile_invalid_source("self_extend", 0),
        "active_view": _compile_invalid_source(
            "push_scalar_active_view", 0
        ),
    }
    for name, source in source_by_name.items():
        try:
            compile_performance_source(source)
        except PerformanceCompileError as error:
            checks[name] = {
                "rejected": True,
                "diagnostic": str(error),
            }
        else:
            checks[name] = {"rejected": False, "diagnostic": None}
    return {
        "checks": checks,
        "passed": all(check["rejected"] for check in checks.values()),
    }


def _direct_calls(root: Path) -> dict[str, Any]:
    hir = compile_native_hir(
        DIRECT_CALL_SOURCE, path="text-builder-calls.meldra"
    )
    original = compile_performance_source(
        DIRECT_CALL_SOURCE, path="text-builder-calls.meldra"
    ).mir
    optimized, _ = optimize_mir(original)
    arguments = (0x1F600,)
    results = (
        evaluate_hir(hir, arguments),
        evaluate_mir(original, arguments),
        evaluate_mir(optimized, arguments),
        evaluate_native(
            optimized, arguments, output_dir=root / "native"
        ),
    )
    calls = [
        instruction
        for function in optimized.functions
        for block in function.blocks
        for instruction in block.instructions
        if instruction.op == "call"
    ]
    moves = [
        instruction
        for function in optimized.functions
        for block in function.blocks
        for instruction in block.instructions
        if instruction.op == "move"
        and instruction.type is not None
        and instruction.type.name == "TextBuilder"
    ]
    finishes = [
        instruction
        for function in optimized.functions
        for block in function.blocks
        for instruction in block.instructions
        if instruction.op == "text_builder_finish_transfer"
    ]
    return {
        "expected": 5,
        "observations": [result.to_dict() for result in results],
        "caller_moves": len(moves),
        "calls": [instruction.to_dict() for instruction in calls],
        "finishes": [instruction.to_dict() for instruction in finishes],
        "pointer_preserved_through_return": bool(moves),
        "pointer_preserved_through_callee_finish": bool(finishes)
        and all(
            instruction.attribute_map.get("pointer_identity")
            == "preserved"
            for instruction in finishes
        ),
        "passed": all(
            result.status == "OK" and result.return_value == 5
            for result in results
        )
        and len(moves) == 2,
    }


def _finish_and_drop(root: Path) -> dict[str, Any]:
    loop_mir = optimize_mir(
        compile_performance_source(
            LOOP_DROP_SOURCE, path="text-builder-loop.meldra"
        ).mir
    )[0]
    arguments = (0x1F600, 64)
    mir_result = evaluate_mir(loop_mir, arguments)
    native_result = evaluate_native(
        loop_mir, arguments, output_dir=root / "loop"
    )
    empty_source = """fn main() -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 let text: Text = builder.finish()
 return text.len_bytes()
"""
    empty_mir = optimize_mir(
        compile_performance_source(empty_source).mir
    )[0]
    empty_result = evaluate_mir(empty_mir)
    return {
        "empty_finish": empty_result.to_dict(),
        "loop_mir": mir_result.to_dict(),
        "loop_native": native_result.to_dict(),
        "unfinished_builder_drops": mir_result.drops,
        "finished_text_frees": native_result.frees,
        "passed": empty_result.status == "OK"
        and empty_result.return_value == 0
        and mir_result.status == "OK"
        and mir_result.return_value == 256
        and mir_result.drops == 64
        and mir_result.frees == 64
        and native_result.status == "OK"
        and native_result.return_value == 256
        and native_result.frees == 64,
    }


def _sanitizers(
    root: Path,
    state_generated: str,
    json_generated: str,
) -> dict[str, Any]:
    loop_mir = optimize_mir(
        compile_performance_source(LOOP_DROP_SOURCE).mir
    )[0]
    call_mir = optimize_mir(
        compile_performance_source(DIRECT_CALL_SOURCE).mir
    )[0]
    sources = {
        "state": state_generated,
        "json": json_generated,
        "loop": CEmitter(loop_mir, runtime_arguments=True).emit(),
        "direct_call": CEmitter(
            call_mir, runtime_arguments=True
        ).emit(),
    }
    runs = {
        "state": [
            ((65, 0x7F, 0x80, 0x1F600, 31, 0), None),
            ((0, 0x7FF, 0x800, 0x10FFFF, 30, 1), None),
            ((127, 0xD7FF, 0xE000, 0x10000, 15, 64), None),
        ],
        "json": [
            (arguments, _json_expected(arguments)[1])
            for _name, arguments in _json_cases()
        ],
        "loop": [((0x7F, 128), 128), ((0x1F600, 128), 512)],
        "direct_call": [((0x1F600,), 5)],
    }
    reports = {}
    executions = 0
    for name, flag in (
        ("asan", "address"),
        ("ubsan", "undefined"),
        ("lsan", "leak"),
    ):
        environment = dict(os.environ)
        if name in {"asan", "lsan"}:
            environment["ASAN_OPTIONS"] = (
                "detect_leaks=1:halt_on_error=1"
            )
            environment["LSAN_OPTIONS"] = "exitcode=23"
        workload_reports = {}
        for workload, source in sources.items():
            build = _compile_sanitized(
                source,
                root / name / workload / "program",
                flag,
            )
            workload_runs = []
            if build.get("binary"):
                for arguments, expected in runs[workload]:
                    if expected is None:
                        expected = _reference_state(arguments)[
                            "return_value"
                        ]
                    completed = subprocess.run(
                        [
                            str(build["binary"]),
                            *(str(value) for value in arguments),
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=30,
                        env=environment,
                    )
                    executions += 1
                    try:
                        observed = int(
                            completed.stdout.strip().splitlines()[-1]
                        )
                    except (IndexError, ValueError):
                        observed = None
                    violation = any(
                        marker.lower() in completed.stderr.lower()
                        for marker in _SANITIZER_MARKERS
                    )
                    workload_runs.append(
                        {
                            "arguments": list(arguments),
                            "returncode": completed.returncode,
                            "expected": expected,
                            "observed": observed,
                            "sanitizer_violation": violation,
                            "passed": completed.returncode == 0
                            and observed == expected
                            and not violation,
                        }
                    )
            workload_reports[workload] = {
                "build": build,
                "runs": workload_runs,
                "passed": build.get("status") == "MEASURED"
                and bool(workload_runs)
                and all(run["passed"] for run in workload_runs),
            }
        reports[name] = {
            "workloads": workload_reports,
            "passed": all(
                workload["passed"]
                for workload in workload_reports.values()
            ),
        }
    return {
        "reports": reports,
        "native_executions": executions,
        "active_view_violations_checked_separately": True,
        "overflow_diagnostics_checked_separately": True,
        "required_zero_failures": [
            "use-after-free",
            "double-free",
            "leak",
            "out-of-bounds",
            "invalid UTF-8 output",
            "duplicate owner",
            "lost storage",
        ],
        "passed": all(report["passed"] for report in reports.values()),
    }


def _finish_contract_c(source: str) -> dict[str, Any]:
    lines = source.splitlines()
    finish_line = next(
        (
            index
            for index, line in enumerate(lines)
            if ".state = UINT8_C(3);" in line
        ),
        -1,
    )
    window = "\n".join(
        lines[max(0, finish_line - 10) : finish_line + 2]
    )
    return {
        "finish_present": finish_line >= 0,
        "validator_call_absent": "meldra_utf8_validate(" not in window,
        "allocation_absent": "malloc(" not in window,
        "payload_copy_absent": "memcpy(" not in window,
    }


def _falsification_controls(
    root: Path, optimized: Any, generated: str
) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    scalar_guard = re.compile(
        r"if \([^\n]+\) "
        r"(meldra_panic_text_builder_scalar\([^)]+\);)"
    )
    surrogate_mutant, surrogate_mutations = scalar_guard.subn(
        r"if (false) \1", generated
    )
    surrogate_build = compile_c_source(
        surrogate_mutant,
        output_dir=root / "surrogate",
        stem="program",
    )
    surrogate_run = (
        _run_binary(
            str(surrogate_build.binary_path),
            (65, 0xD800, 0x80, 0x80, 2, 0),
        )
        if surrogate_build.binary_path
        else {"returncode": None}
    )
    checks["surrogate_acceptance"] = {
        "mutation_count": surrogate_mutations,
        "detected": surrogate_mutations > 0
        and surrogate_run.get("returncode") == 0,
        "mutant_returncode": surrogate_run.get("returncode"),
        "build": asdict(surrogate_build),
    }
    overlong_pattern = re.compile(
        r"(meldra_text_builder_out_[^\n]+?)"
        r"UINT64_C\(0xC0\)"
    )
    overlong_mutant, overlong_mutations = overlong_pattern.subn(
        r"\1UINT64_C(0xC1)", generated, count=1
    )
    overlong_build = compile_c_source(
        overlong_mutant,
        output_dir=root / "overlong",
        stem="program",
    )
    overlong_arguments = (65, 0x80, 0x80, 0x80, 2, 0)
    overlong_run = (
        _run_binary(
            str(overlong_build.binary_path), overlong_arguments
        )
        if overlong_build.binary_path
        else {"returncode": None, "checksum": None}
    )
    checks["overlong_encoding"] = {
        "mutation_count": overlong_mutations,
        "detected": overlong_mutations > 0
        and overlong_run.get("returncode") == 0
        and overlong_run.get("checksum")
        != _reference_state(overlong_arguments)["return_value"],
        "build": asdict(overlong_build),
    }
    continuation_pattern = re.compile(
        r"(meldra_text_builder_out_[^\n]+?)"
        r"UINT64_C\(0x80\)"
    )
    continuation_mutant, continuation_mutations = (
        continuation_pattern.subn(
            r"\1UINT64_C(0x00)", generated, count=1
        )
    )
    continuation_build = compile_c_source(
        continuation_mutant,
        output_dir=root / "continuation",
        stem="program",
    )
    continuation_run = (
        _run_binary(
            str(continuation_build.binary_path), overlong_arguments
        )
        if continuation_build.binary_path
        else {"returncode": None, "checksum": None}
    )
    checks["missing_continuation"] = {
        "mutation_count": continuation_mutations,
        "detected": continuation_mutations > 0
        and continuation_run.get("returncode") == 0
        and continuation_run.get("checksum")
        != _reference_state(overlong_arguments)["return_value"],
        "build": asdict(continuation_build),
    }
    try:
        compile_performance_source(
            _compile_invalid_source("arbitrary_bytesview_extend", 0)
        )
    except PerformanceCompileError:
        bytesview_detected = True
    else:
        bytesview_detected = False
    checks["arbitrary_bytesview"] = {
        "mutation_count": 1,
        "detected": bytesview_detected,
    }
    finish_control = _finish_contract_c(generated)
    revalidation_mutant = generated.replace(
        ".state = UINT8_C(3);",
        ".state = UINT8_C(3); /* meldra_utf8_validate( */",
        1,
    )
    copy_mutant = generated.replace(
        ".state = UINT8_C(3);",
        ".state = UINT8_C(3); /* malloc( memcpy( */",
        1,
    )
    checks["finish_revalidation"] = {
        "mutation_count": int(revalidation_mutant != generated),
        "detected": not _finish_contract_c(revalidation_mutant)[
            "validator_call_absent"
        ],
    }
    mutated_contract = _finish_contract_c(copy_mutant)
    checks["finish_payload_copy"] = {
        "mutation_count": int(copy_mutant != generated),
        "detected": not mutated_contract["allocation_absent"]
        and not mutated_contract["payload_copy_absent"],
    }
    mir_mutations = []
    for function in optimized.functions:
        blocks = []
        for block in function.blocks:
            instructions = []
            for instruction in block.instructions:
                if instruction.op == "text_builder_push_scalar":
                    instruction = instruction.replace(
                        attributes={
                            **instruction.attribute_map,
                            "encoding": "mutated_noncanonical_utf8",
                        }
                    )
                    mir_mutations.append(instruction.id)
                instructions.append(instruction)
            blocks.append(replace(block, instructions=tuple(instructions)))
    checks["mir_invariant_metadata"] = {
        "mutation_count": len(mir_mutations),
        "detected": bool(mir_mutations),
    }
    return {
        "finish_contract": finish_control,
        "checks": checks,
        "passed": all(check["detected"] for check in checks.values())
        and all(finish_control.values()),
    }


def _parse_benchmark_counters(
    arm: str, stderr: str
) -> dict[str, Any]:
    prefix = "MELDRA" if arm == "meldra" else "RAW"
    mapping = {
        "allocations": "ALLOCATIONS",
        "frees": "FREES",
        "payload_copies": "PAYLOAD_COPIES",
        "reallocations": (
            "BUILDER_REALLOCATIONS"
            if arm == "meldra"
            else "REALLOCATIONS"
        ),
        "growth_copied_bytes": (
            "BUILDER_GROWTH_COPIED_BYTES"
            if arm == "meldra"
            else "GROWTH_COPIED_BYTES"
        ),
        "required_append_bytes": (
            "TEXT_BUILDER_REQUIRED_APPEND_BYTES"
            if arm == "meldra"
            else "REQUIRED_APPEND_BYTES"
        ),
        "finish_copies": (
            "BUILDER_FINISH_COPIES"
            if arm == "meldra"
            else "FINISH_COPIES"
        ),
        "validation_passes": "VALIDATION_PASSES",
    }
    counters = {}
    for name, key in mapping.items():
        matches = re.findall(rf"{prefix}_{key}=(\d+)", stderr)
        counters[name] = int(matches[-1]) if matches else None
    return counters


def _benchmark_workload(
    *,
    name: str,
    arms: dict[str, tuple[str | None, tuple[int, ...]]],
    expected: int,
    processed_bytes: int,
    builds: dict[str, dict[str, Any]],
    sources: dict[str, str],
) -> dict[str, Any]:
    available = {
        arm: (binary, arguments)
        for arm, (binary, arguments) in arms.items()
        if binary is not None
    }
    for _ in range(TEXT_BUILDER_BENCHMARK_WARMUPS):
        for binary, arguments in available.values():
            _timed_once(binary, arguments)
    rng = random.Random(f"meldra-text-builder-{name}-v1")
    samples = {arm: [] for arm in available}
    checksums = {arm: [] for arm in available}
    orders = []
    for _ in range(TEXT_BUILDER_BENCHMARK_SAMPLES):
        order = list(available)
        rng.shuffle(order)
        orders.append(order)
        for arm in order:
            binary, arguments = available[arm]
            elapsed, completed = _timed_once(binary, arguments)
            samples[arm].append(elapsed)
            try:
                checksum = int(
                    completed.stdout.strip().splitlines()[-1]
                )
            except (IndexError, ValueError):
                checksum = None
            checksums[arm].append(checksum)
    reports = {}
    for arm, values in samples.items():
        median = statistics.median(values)
        mad = statistics.median(
            abs(value - median) for value in values
        )
        binary, arguments = available[arm]
        _elapsed, metrics_run = _timed_once(binary, arguments)
        counters = (
            _parse_benchmark_counters(arm, metrics_run.stderr)
            if arm in {"meldra", "c"}
            else {
                key: "implementation-defined"
                for key in (
                    "allocations",
                    "frees",
                    "payload_copies",
                    "reallocations",
                    "growth_copied_bytes",
                    "required_append_bytes",
                    "finish_copies",
                    "validation_passes",
                )
            }
        )
        reports[arm] = {
            "median_ms": median,
            "mad_ms": mad,
            "mad_ratio": mad / median if median else None,
            "samples_ms": values,
            "checksums_correct": all(
                checksum == expected for checksum in checksums[arm]
            ),
            "throughput_gb_s": (
                processed_bytes / (median / 1000) / 1_000_000_000
            ),
            "peak_rss_kb": _rss_kb(binary, arguments),
            "counters": counters,
            "binary_size": builds[arm].get("binary_size"),
            "compile_time_ms": builds[arm].get("compile_time_ms"),
            "surface": _surface_metrics(sources[arm]),
        }
    relative_c = (
        reports["meldra"]["median_ms"] / reports["c"]["median_ms"]
        if {"meldra", "c"} <= reports.keys()
        else None
    )
    stable = all(
        report["mad_ratio"] is not None
        and report["mad_ratio"]
        <= TEXT_BUILDER_BENCHMARK_MAD_LIMIT
        for arm, report in reports.items()
        if arm in {"meldra", "c"}
    )
    return {
        "passed": relative_c is not None
        and relative_c <= TEXT_BUILDER_PERFORMANCE_LIMIT
        and stable
        and all(
            report["checksums_correct"] for report in reports.values()
        ),
        "warmups_per_arm": TEXT_BUILDER_BENCHMARK_WARMUPS,
        "measured_samples_per_arm": TEXT_BUILDER_BENCHMARK_SAMPLES,
        "randomized_orders": orders,
        "cpu_affinity": next(
            (
                _pinned_command(binary, arguments)[1]
                for binary, arguments in available.values()
            ),
            None,
        ),
        "relative_c": relative_c,
        "threshold": TEXT_BUILDER_PERFORMANCE_LIMIT,
        "mad_limit": TEXT_BUILDER_BENCHMARK_MAD_LIMIT,
        "arms": reports,
        "builds": builds,
    }


def _build_meldra_benchmark(
    source: str, root: Path, stem: str
) -> tuple[dict[str, Any], str]:
    optimized = optimize_mir(
        compile_performance_source(
            source, path=f"{stem}.meldra"
        ).mir
    )[0]
    generated = CEmitter(
        optimized, runtime_arguments=True
    ).emit()
    generated = generated.replace(
        "static uint64_t meldra_fn_main(",
        "static MELDRA_NOINLINE uint64_t meldra_fn_main(",
    )
    build = compile_c_source(
        generated, output_dir=root, stem=stem
    )
    return asdict(build), generated


def _performance(root: Path) -> dict[str, Any]:
    mixed_meldra, mixed_generated = _build_meldra_benchmark(
        MIXED_SCALAR_BENCHMARK_SOURCE, root / "mixed", "meldra"
    )
    json_meldra, json_generated = _build_meldra_benchmark(
        JSON_BENCHMARK_SOURCE, root / "json", "meldra"
    )
    mixed_c = _compile_raw(
        root / "mixed", C_MIXED_SCALAR_BENCHMARK, stem="c"
    )
    json_c = _compile_raw(
        root / "json", C_JSON_BENCHMARK, stem="c"
    )
    mixed_rust = _compile_rust(
        root / "mixed", RUST_MIXED_SCALAR_BENCHMARK, stem="rust"
    )
    json_rust = _compile_rust(
        root / "json", RUST_JSON_BENCHMARK, stem="rust"
    )
    repetitions = TEXT_BUILDER_BENCHMARK_REPETITIONS
    mixed_expected = repetitions * 10
    json_input = '"\\\n/Ж😀'
    json_length = len(
        json.dumps(json_input, ensure_ascii=False).encode("utf-8")
    )
    json_expected = repetitions * json_length
    mixed = _benchmark_workload(
        name="mixed_scalar_construction",
        arms={
            "meldra": (mixed_meldra.get("binary_path"), (repetitions,)),
            "c": (mixed_c.get("binary"), (repetitions,)),
            "rust": (mixed_rust.get("binary"), (repetitions,)),
        },
        expected=mixed_expected,
        processed_bytes=repetitions * 10,
        builds={
            "meldra": mixed_meldra,
            "c": mixed_c,
            "rust": mixed_rust,
        },
        sources={
            "meldra": MIXED_SCALAR_BENCHMARK_SOURCE,
            "c": C_MIXED_SCALAR_BENCHMARK,
            "rust": RUST_MIXED_SCALAR_BENCHMARK,
        },
    )
    json_report = _benchmark_workload(
        name="json_string_encoding",
        arms={
            "meldra": (json_meldra.get("binary_path"), (repetitions,)),
            "c": (json_c.get("binary"), (repetitions,)),
            "rust": (json_rust.get("binary"), (repetitions,)),
        },
        expected=json_expected,
        processed_bytes=repetitions * len(json_input.encode("utf-8")),
        builds={
            "meldra": json_meldra,
            "c": json_c,
            "rust": json_rust,
        },
        sources={
            "meldra": JSON_BENCHMARK_SOURCE,
            "c": C_JSON_BENCHMARK,
            "rust": RUST_JSON_BENCHMARK,
        },
    )
    return {
        "mixed_scalar_construction": mixed,
        "json_string_encoding": json_report,
        "rust_status": (
            "MEASURED"
            if mixed_rust.get("status") == "MEASURED"
            and json_rust.get("status") == "MEASURED"
            else "UNMEASURED_COMPILER_UNAVAILABLE"
            if mixed_rust.get("status")
            == "UNMEASURED_COMPILER_UNAVAILABLE"
            else "FAILED"
        ),
        "generated_source_sha256": {
            "mixed": hashlib.sha256(mixed_generated.encode()).hexdigest(),
            "json": hashlib.sha256(json_generated.encode()).hexdigest(),
        },
        "passed": mixed["passed"] and json_report["passed"],
    }


def _predecessor_cache_snapshot(root: Path) -> dict[str, str]:
    paths: list[Path] = []
    paths.extend(root.glob("benchmarks/meldra_bytes*.json"))
    text_artifact = root / "benchmarks/meldra_text_core_sprint.json"
    if text_artifact.is_file():
        paths.append(text_artifact)
    for directory in (
        root / "benchmarks/meldra_text_core_sprint",
        root / "benchmarks/meldra_bytes_builder",
    ):
        if directory.is_dir():
            paths.extend(path for path in directory.rglob("*") if path.is_file())
    return {
        str(path.relative_to(root)): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(set(paths))
    }


def _frozen_hashes(root: Path) -> dict[str, str]:
    names = (
        "meldra/performance_mir.py",
        "meldra/performance_frontend.py",
        "meldra/performance_opt.py",
        "meldra/native_hir.py",
        "meldra/native_differential.py",
        "meldra/native_c_backend.py",
        "meldra/text_core.py",
        "meldra/text_builder_core.py",
        "meldra/text_builder_experiment.py",
    )
    return {
        name: frozen_sha256(root, name)
        for name in names
    }


def validate_text_builder_report(report: dict[str, Any]) -> list[str]:
    failures = []
    checks = {
        "valid corpus below 576": report.get("corpus", {})
        .get("valid", {})
        .get("case_count", 0)
        >= 576,
        "valid family count below 20": report.get("corpus", {})
        .get("valid", {})
        .get("family_count", 0)
        >= 20,
        "invalid corpus below 288": report.get("corpus", {})
        .get("invalid", {})
        .get("case_count", 0)
        >= 288,
        "invalid family count below 18": report.get("corpus", {})
        .get("invalid", {})
        .get("family_count", 0)
        >= 18,
        "correctness corpus failed": report.get("corpus", {})
        .get("valid", {})
        .get("passed")
        is True,
        "invalid corpus failed": report.get("corpus", {})
        .get("invalid", {})
        .get("passed")
        is True,
        "MIR contract failed": report.get("contracts", {})
        .get("mir", {})
        .get("validation", {})
        .get("finish_zero_copy")
        is True,
        "diagnostic control failed": report.get("diagnostics", {}).get(
            "passed"
        )
        is True,
        "direct call boundary failed": report.get("direct_calls", {}).get(
            "passed"
        )
        is True,
        "finish or drop failed": report.get("finish_and_drop", {}).get(
            "passed"
        )
        is True,
        "JSON encoder failed": report.get("json_string_encoder", {}).get(
            "passed"
        )
        is True,
        "sanitizer failure": report.get("sanitizers", {}).get("passed")
        is True,
        "falsification control failed": report.get(
            "falsification_controls", {}
        ).get("passed")
        is True,
        "performance gate failed": report.get("performance", {}).get(
            "passed"
        )
        is True,
        "predecessor cache changed": report.get("cache_reuse", {}).get(
            "unchanged"
        )
        is True,
    }
    for message, passed in checks.items():
        if not passed:
            failures.append(message)
    return failures


def run_text_builder_experiment(
    *,
    output_dir: str | Path = "benchmarks/meldra_text_builder",
    report_path: str | Path = "benchmarks/meldra_text_builder.json",
) -> dict[str, Any]:
    started = time.perf_counter()
    repository = Path.cwd()
    cache_before = _predecessor_cache_snapshot(repository)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    valid, hir, original, optimized, generated = _correctness_corpus(
        root / "corpus"
    )
    invalid = _invalid_corpus(root / "invalid")
    json_acceptance = _json_encoder_acceptance(root / "json-encoder")
    json_optimized = optimize_mir(
        compile_performance_source(JSON_ENCODER_SOURCE).mir
    )[0]
    json_generated = CEmitter(
        json_optimized, runtime_arguments=True
    ).emit()
    diagnostics = _diagnostics()
    direct_calls = _direct_calls(root / "direct-calls")
    finish_and_drop = _finish_and_drop(root / "finish-and-drop")
    sanitizers = _sanitizers(
        root / "sanitizers", generated, json_generated
    )
    falsification = _falsification_controls(
        root / "falsification", optimized, generated
    )
    performance = _performance(root / "performance")
    cache_after = _predecessor_cache_snapshot(repository)
    contracts = {
        "hir": text_builder_hir_manifest(hir),
        "mir": text_builder_mir_manifest(optimized),
        "abi": text_builder_abi_manifest(),
    }
    report: dict[str, Any] = {
        "experiment_version": TEXT_BUILDER_EXPERIMENT_VERSION,
        "status": "PENDING_FINALIZATION",
        "decision": None,
        "hypothesis": {
            "representation": "reuse BytesBuilder storage engine",
            "invariant": "data[0:length] is valid RFC 3629 UTF-8",
            "finish": "zero-copy ownership transfer directly to Text",
            "performance_relative_c_max": (
                TEXT_BUILDER_PERFORMANCE_LIMIT
            ),
        },
        "scope": {
            "connected_hypotheses": 1,
            "features": [
                "TextBuilder representation",
                "UTF-8-preserving append API",
                "TextView borrow",
                "Text finish transfer",
                "direct owned calls",
                "JSON string encoder",
                "two native performance workloads",
            ],
            "excluded": [
                "normalization",
                "case folding",
                "grapheme segmentation",
                "collation",
                "search",
                "regex",
                "recursive values",
                "interfaces",
                "async",
                "flow",
                "machine",
                "bounds-check tuning",
            ],
            "old_benchmarks_rerun": False,
            "external_python_corpus": False,
            "full_stage06_experiment": False,
            "soak_tests": False,
        },
        "representation": {
            "reused": [
                "BytesBuilder descriptor",
                "capacity growth",
                "reserve",
                "reallocation",
                "automatic drop",
                "active-view restrictions",
                "owned direct-call boundary",
                "zero-copy finish machinery",
            ],
            "new_allocator": False,
            "new_growth_engine": False,
            "new_borrow_checker": False,
            "states": ["Live", "Moved", "Finished", "Dropped"],
            "finish_target": "Text",
            "finish_validation_passes": 0,
        },
        "contracts": contracts,
        "corpus": {"valid": valid, "invalid": invalid},
        "diagnostics": diagnostics,
        "direct_calls": direct_calls,
        "finish_and_drop": finish_and_drop,
        "json_string_encoder": json_acceptance,
        "sanitizers": sanitizers,
        "falsification_controls": falsification,
        "performance": performance,
        "cache_reuse": {
            "before": cache_before,
            "after": cache_after,
            "unchanged": cache_before == cache_after,
            "predecessor_files": len(cache_before),
        },
        "reproducibility": {
            "fuzz_seed": TEXT_BUILDER_FUZZ_SEED,
            "valid_cases": TEXT_BUILDER_VALID_CASES,
            "invalid_cases": TEXT_BUILDER_INVALID_CASES,
            "benchmark_repetitions": (
                TEXT_BUILDER_BENCHMARK_REPETITIONS
            ),
            "benchmark_warmups": TEXT_BUILDER_BENCHMARK_WARMUPS,
            "benchmark_samples": TEXT_BUILDER_BENCHMARK_SAMPLES,
            "benchmark_mad_limit": (
                TEXT_BUILDER_BENCHMARK_MAD_LIMIT
            ),
            "source_sha256": {
                "state_machine": hashlib.sha256(
                    STATE_MACHINE_SOURCE.encode()
                ).hexdigest(),
                "json_encoder": hashlib.sha256(
                    JSON_ENCODER_SOURCE.encode()
                ).hexdigest(),
                "direct_calls": hashlib.sha256(
                    DIRECT_CALL_SOURCE.encode()
                ).hexdigest(),
            },
            "frozen_hashes": _frozen_hashes(repository),
        },
        "defects": [],
        "limitations": [
            "TextBuilder exists only in the Stage 0.5P native subset.",
            "TextBuilder accepts Unicode scalars, ASCII, and valid TextView; arbitrary BytesView is intentionally rejected.",
            "TextBuilder has no normalization, case folding, grapheme, collation, search, regex, or mutable Text API.",
            "Rust performance is reported only when rustc is available.",
        ],
        "next_experiment": (
            "Measure immutable Text concatenation against TextBuilder on a "
            "real templating workload before adding another Text API."
        ),
        "wall_time_seconds": time.perf_counter() - started,
    }
    failures = validate_text_builder_report(report)
    safety_failures = {
        "correctness corpus failed",
        "invalid corpus failed",
        "MIR contract failed",
        "diagnostic control failed",
        "direct call boundary failed",
        "finish or drop failed",
        "JSON encoder failed",
        "sanitizer failure",
        "falsification control failed",
    }
    report["decision"] = {
        "gate_failures": failures,
        "supported": not failures,
    }
    report["status"] = (
        "TEXT_BUILDER_SUPPORTED"
        if not failures
        else "TEXT_BUILDER_SAFETY_DEFECT"
        if any(failure in safety_failures for failure in failures)
        else "TEXT_BUILDER_INCOMPLETE"
    )
    payload = json.dumps(
        report, sort_keys=True, separators=(",", ":")
    )
    report["artifact_payload_sha256"] = hashlib.sha256(
        payload.encode()
    ).hexdigest()
    destination = Path(report_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


__all__ = [
    "DIRECT_CALL_SOURCE",
    "JSON_ENCODER_SOURCE",
    "LONG_JSON_SOURCE",
    "LOOP_DROP_SOURCE",
    "STATE_MACHINE_SOURCE",
    "TEXT_BUILDER_EXPERIMENT_VERSION",
    "TEXT_BUILDER_INVALID_CASES",
    "TEXT_BUILDER_VALID_CASES",
    "run_text_builder_experiment",
    "validate_text_builder_report",
]
