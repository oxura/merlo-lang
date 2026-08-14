"""Decision experiment for owned UTF-8 Text and borrowed TextView."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
from dataclasses import asdict, replace
import random
import time
from pathlib import Path
from typing import Any, Callable, Iterable
from research.archive.alpha1.merlo.builder_call_boundary_experiment import (
    run_builder_call_boundary_gate,
)

from research.archive.alpha1.merlo.bytes_experiment import _compile_sanitized
from research.archive.historical_protocol.merlo.legacy_evidence import frozen_sha256
from .native_c_backend import CEmitter, compile_c_source, find_c_compiler
from research.archive.alpha1.merlo.native_differential import evaluate_hir, evaluate_mir
from research.archive.alpha1.merlo.native_hir import compile_native_hir
from .performance_frontend import PerformanceCompileError, compile_performance_source
from .performance_opt import optimize_mir
from research.archive.alpha1.merlo.text_core import (
    text_abi_manifest,
    text_hir_manifest,
    text_mir_manifest,
    validate_text_mir,
)

TEXT_CORE_EXPERIMENT_VERSION = 1
TEXT_STRUCTURED_VALID_CASES = 640
TEXT_STRUCTURED_INVALID_CASES = 480
TEXT_FUZZ_VALID_CASES = 128
TEXT_FUZZ_INVALID_CASES = 160
TEXT_VALID_CASES = TEXT_STRUCTURED_VALID_CASES + TEXT_FUZZ_VALID_CASES
TEXT_INVALID_CASES = TEXT_STRUCTURED_INVALID_CASES + TEXT_FUZZ_INVALID_CASES
TEXT_VALID_FAMILIES = 21
TEXT_INVALID_FAMILIES = 21
TEXT_FUZZ_SEED = 0x5EEDC0DE
TEXT_PERFORMANCE_LIMIT = 1.20
TEXT_BENCHMARK_REPETITIONS = 2_500_000
TEXT_BENCHMARK_WARMUPS = 5
TEXT_BENCHMARK_SAMPLES = 30
TEXT_BENCHMARK_MAD_LIMIT = 0.05
_MASK64 = (1 << 64) - 1
_SANITIZER_MARKERS = (
    "AddressSanitizer",
    "LeakSanitizer",
    "UndefinedBehaviorSanitizer",
    "runtime error:",
    "use-after-free",
    "double-free",
)

UTF8_INSPECTOR_SOURCE = """fn main(packed: UInt64, length: UInt64, repetitions: UInt64) -> UInt64:
 var checksum: UInt64 = 0
 var iteration: UInt64 = 0
 while iteration < repetitions:
  let builder: BytesBuilder = BytesBuilder.with_capacity(length)
  var index: UInt64 = 0
  while index < length:
   builder.push((packed >> (index * 8)) & 255)
   index = index + 1
  let data: Bytes = builder.finish()
  let decoded: Utf8Decode = Text.from_utf8(move(data))
  match decoded:
   case Valid(text):
    let view: TextView = text.as_view()
    var offset: UInt64 = 0
    var scalars: UInt64 = 0
    while offset < view.len_bytes():
     offset = offset + view.scalar_width_at(offset)
     scalars = scalars + 1
    checksum = checksum + ((1 << 63) | (scalars << 32) | view.len_bytes())
   case Invalid(error_offset):
    checksum = checksum + error_offset
  iteration = iteration + 1
 return checksum
"""

ROUNDTRIP_SOURCE = """fn main(scalar: UInt64) -> UInt64:
 let text: Text = Text.from_scalar(scalar)
 let view: TextView = text.as_view()
 let raw: BytesView = view.as_bytes()
 let length: UInt64 = raw.len()
 let bytes: Bytes = text.into_bytes()
 return length + bytes.len()
"""

BOUNDARY_SOURCE = """fn main(scalar: UInt64) -> UInt64:
 let text: Text = Text.from_scalar(scalar)
 let view: TextView = text.as_view()
 let invalid: TextView = view.slice_bytes(1, 1)
 return invalid.len_bytes()
"""

TEXTVIEW_INSPECTOR_SOURCE = """fn main(packed: UInt64, length: UInt64, start: UInt64, take: UInt64) -> UInt64:
 let builder: BytesBuilder = BytesBuilder.with_capacity(length)
 var index: UInt64 = 0
 while index < length:
  builder.push((packed >> (index * 8)) & 255)
  index = index + 1
 let data: Bytes = builder.finish()
 let decoded: Utf8Decode = Text.from_utf8(move(data))
 match decoded:
  case Valid(text):
   let whole: TextView = text.as_view()
   let selected: TextView = whole.slice_bytes(start, take)
   let raw: BytesView = selected.as_bytes()
   var checksum: UInt64 = 0
   var byte_index: UInt64 = 0
   while byte_index < raw.len():
    checksum = (checksum * 257) + raw[byte_index]
    byte_index = byte_index + 1
   return (selected.len_bytes() << 32) | checksum
  case Invalid(error_offset):
   return error_offset
"""

TEXT_BYTES_TEXT_SOURCE = """fn main(scalar: UInt64) -> UInt64:
 let first: Text = Text.from_scalar(scalar)
 let bytes: Bytes = first.into_bytes()
 let decoded: Utf8Decode = Text.from_utf8(move(bytes))
 match decoded:
  case Valid(second):
   let output: Bytes = second.into_bytes()
   return output.len()
  case Invalid(error_offset):
   return error_offset + 100
"""

SANITIZER_TEXTVIEW_SOURCE = """fn main(scalar: UInt64) -> UInt64:
 let text: Text = Text.from_scalar(scalar)
 let view: TextView = text.as_view()
 let whole: TextView = view.slice_bytes(0, view.len_bytes())
 let raw: BytesView = whole.as_bytes()
 let observed: UInt64 = raw.len() + whole.scalar_count()
 let bytes: Bytes = text.into_bytes()
 return observed + bytes.len()
"""

LARGE_TEXT_SOURCE = """fn main(length: UInt64) -> UInt64:
 let builder: BytesBuilder = BytesBuilder.with_capacity(length)
 var index: UInt64 = 0
 while index < length:
  builder.push(65)
  index = index + 1
 let data: Bytes = builder.finish()
 let decoded: Utf8Decode = Text.from_utf8(move(data))
 match decoded:
  case Valid(text):
   let view: TextView = text.as_view()
   return view.scalar_count()
  case Invalid(error_offset):
   return error_offset
"""

TEXT_CONSTRUCTION_SOURCE = """fn main(ascii: UInt64, scalar: UInt64) -> UInt64:
 let first: Text = Text.from_ascii(ascii)
 let second: Text = Text.from_scalar(scalar)
 return first.len_bytes() + second.len_bytes()
"""

SCALAR_BENCHMARK_SOURCE = """fn main(scalar: UInt64, repetitions: UInt64) -> UInt64:
 var checksum: UInt64 = 0
 var iteration: UInt64 = 0
 while iteration < repetitions:
  let text: Text = Text.from_scalar(scalar)
  let view: TextView = text.as_view()
  checksum = checksum + view.len_bytes() + view.scalar_count()
  iteration = iteration + 1
 return checksum
"""

RAW_INSPECTOR_C = r'''#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    uint8_t *data;
    uint64_t length;
    uint64_t capacity;
    uint8_t state;
} raw_builder;

static uint64_t raw_allocations = 0;
static uint64_t raw_frees = 0;

static raw_builder builder_with_capacity(uint64_t capacity) {
    if (capacity > UINT64_C(9223372036854775807)) abort();
    uint8_t *data = capacity == 0 ? NULL : (uint8_t *)malloc((size_t)capacity);
    if (capacity != 0 && data == NULL) abort();
    if (data != NULL) ++raw_allocations;
    return (raw_builder){data, 0, capacity, 1};
}
static void builder_push(raw_builder *builder, uint64_t byte) {
    if (builder->state != 1 || byte > 255) abort();
    if (builder->length == UINT64_MAX) abort();
    uint64_t required = builder->length + 1;
    if (required > builder->capacity) {
        if (builder->capacity > UINT64_MAX / 2) abort();
        uint64_t doubled = builder->capacity * 2;
        uint64_t capacity = required > doubled ? required : doubled;
        if (capacity < 8) capacity = 8;
        if (capacity > UINT64_C(9223372036854775807)) abort();
        uint8_t *replacement = (uint8_t *)malloc((size_t)capacity);
        if (replacement == NULL) abort();
        ++raw_allocations;
        if (builder->length != 0) {
            memcpy(replacement, builder->data, (size_t)builder->length);
        }
        if (builder->data != NULL) {
            free(builder->data);
            ++raw_frees;
        }
        builder->data = replacement;
        builder->capacity = capacity;
    }
    if (builder->length >= builder->capacity) abort();
    builder->data[builder->length++] = (uint8_t)byte;
}
static uint8_t *builder_finish(raw_builder *builder, uint64_t *length) {
    if (builder->state != 1) abort();
    builder->state = 3;
    *length = builder->length;
    return builder->data;
}
static int cont(uint8_t byte) {
    return (byte & UINT8_C(0xC0)) == UINT8_C(0x80);
}
static int validate(const uint8_t *data, uint64_t length, uint64_t *error) {
    uint64_t i = 0;
    while (i < length) {
        uint8_t first = data[i];
        if (first <= UINT8_C(0x7F)) { ++i; continue; }
        if (first >= UINT8_C(0xC2) && first <= UINT8_C(0xDF)) {
            if (i + 1 >= length || !cont(data[i + 1])) { *error = i; return 0; }
            i += 2; continue;
        }
        if (first >= UINT8_C(0xE0) && first <= UINT8_C(0xEF)) {
            if (i + 2 >= length) { *error = i; return 0; }
            uint8_t second = data[i + 1];
            int ok = cont(second);
            if (first == UINT8_C(0xE0)) ok = second >= UINT8_C(0xA0) && second <= UINT8_C(0xBF);
            if (first == UINT8_C(0xED)) ok = second >= UINT8_C(0x80) && second <= UINT8_C(0x9F);
            if (!ok || !cont(data[i + 2])) { *error = i; return 0; }
            i += 3; continue;
        }
        if (first >= UINT8_C(0xF0) && first <= UINT8_C(0xF4)) {
            if (i + 3 >= length) { *error = i; return 0; }
            uint8_t second = data[i + 1];
            int ok = cont(second);
            if (first == UINT8_C(0xF0)) ok = second >= UINT8_C(0x90) && second <= UINT8_C(0xBF);
            if (first == UINT8_C(0xF4)) ok = second >= UINT8_C(0x80) && second <= UINT8_C(0x8F);
            if (!ok || !cont(data[i + 2]) || !cont(data[i + 3])) { *error = i; return 0; }
            i += 4; continue;
        }
        *error = i; return 0;
    }
    *error = 0; return 1;
}
static uint64_t scalar_count(const uint8_t *data, uint64_t length) {
    uint64_t count = 0;
    for (uint64_t i = 0; i < length; ++count) {
        uint8_t first = data[i];
        i += first <= UINT8_C(0x7F) ? 1 : first <= UINT8_C(0xDF) ? 2 : first <= UINT8_C(0xEF) ? 3 : 4;
    }
    return count;
}
int main(int argc, char **argv) {
    if (argc != 4) return 2;
    uint64_t packed = strtoull(argv[1], NULL, 10);
    uint64_t requested = strtoull(argv[2], NULL, 10);
    uint64_t repetitions = strtoull(argv[3], NULL, 10);
    uint64_t checksum = 0;
    for (uint64_t repetition = 0; repetition < repetitions; ++repetition) {
        raw_builder builder = builder_with_capacity(requested);
        for (uint64_t i = 0; i < requested; ++i) {
            builder_push(&builder, (packed >> (i * 8)) & 255);
        }
        uint64_t length = 0;
        uint8_t *data = builder_finish(&builder, &length);
        uint64_t error = 0;
        if (validate(data, length, &error)) {
            checksum += (UINT64_C(1) << 63) | (scalar_count(data, length) << 32) | length;
        } else {
            checksum += error;
        }
        if (data != NULL) {
            free(data);
            ++raw_frees;
        }
    }
    fprintf(stderr, "RAW_ALLOCATIONS=%" PRIu64 " RAW_FREES=%" PRIu64 " RAW_PAYLOAD_COPIES=0\n", raw_allocations, raw_frees);
    printf("%" PRIu64 "\n", checksum);
    return 0;
}
'''

RAW_SCALAR_C = r'''#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static uint64_t encode(uint64_t scalar, uint8_t *data) {
    if (scalar > UINT64_C(0x10FFFF) || (scalar >= UINT64_C(0xD800) && scalar <= UINT64_C(0xDFFF))) return 0;
    if (scalar <= UINT64_C(0x7F)) { data[0] = (uint8_t)scalar; return 1; }
    if (scalar <= UINT64_C(0x7FF)) {
        data[0] = (uint8_t)(UINT64_C(0xC0) | (scalar >> 6));
        data[1] = (uint8_t)(UINT64_C(0x80) | (scalar & UINT64_C(0x3F)));
        return 2;
    }
    if (scalar <= UINT64_C(0xFFFF)) {
        data[0] = (uint8_t)(UINT64_C(0xE0) | (scalar >> 12));
        data[1] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 6) & UINT64_C(0x3F)));
        data[2] = (uint8_t)(UINT64_C(0x80) | (scalar & UINT64_C(0x3F)));
        return 3;
    }
    data[0] = (uint8_t)(UINT64_C(0xF0) | (scalar >> 18));
    data[1] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 12) & UINT64_C(0x3F)));
    data[2] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 6) & UINT64_C(0x3F)));
    data[3] = (uint8_t)(UINT64_C(0x80) | (scalar & UINT64_C(0x3F)));
    return 4;
}
static uint64_t scalar_count(const uint8_t *data, uint64_t length) {
    uint64_t count = 0;
    for (uint64_t i = 0; i < length; ++count) {
        uint8_t first = data[i];
        i += first <= UINT8_C(0x7F) ? 1 : first <= UINT8_C(0xDF) ? 2 : first <= UINT8_C(0xEF) ? 3 : 4;
    }
    return count;
}
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    uint64_t scalar = strtoull(argv[1], NULL, 10);
    uint64_t repetitions = strtoull(argv[2], NULL, 10);
    uint64_t checksum = 0;
    uint64_t allocations = 0;
    uint64_t frees = 0;
    for (uint64_t iteration = 0; iteration < repetitions; ++iteration) {
        uint8_t *data = (uint8_t *)malloc(4);
        ++allocations;
        if (data == NULL) return 3;
        uint64_t length = encode(scalar, data);
        if (length == 0) return 4;
        checksum += length + scalar_count(data, length);
        free(data);
        ++frees;
    }
    fprintf(stderr, "RAW_ALLOCATIONS=%" PRIu64 " RAW_FREES=%" PRIu64 " RAW_PAYLOAD_COPIES=0\n", allocations, frees);
    printf("%" PRIu64 "\n", checksum);
    return 0;
}
'''

RUST_INSPECTOR = r'''use std::env;
fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() != 4 { std::process::exit(2); }
    let packed: u64 = args[1].parse().unwrap();
    let length: usize = args[2].parse().unwrap();
    let repetitions: u64 = args[3].parse().unwrap();
    let mut checksum = 0u64;
    for _ in 0..repetitions {
        let mut data = Vec::with_capacity(length);
        for index in 0..length {
            data.push((packed >> ((index * 8) & 63)) as u8);
        }
        match std::str::from_utf8(&data) {
            Ok(text) => {
                checksum = checksum.wrapping_add(
                    (1u64 << 63)
                        | ((text.chars().count() as u64) << 32)
                        | data.len() as u64,
                );
            }
            Err(error) => {
                checksum = checksum.wrapping_add(error.valid_up_to() as u64);
            }
        }
    }
    println!("{checksum}");
}'''

RUST_SCALAR = r'''use std::env;
fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() != 3 { std::process::exit(2); }
    let scalar: u32 = args[1].parse().unwrap();
    let repetitions: u64 = args[2].parse().unwrap();
    let value = char::from_u32(scalar).unwrap();
    let mut checksum = 0u64;
    for _ in 0..repetitions {
        let mut encoded = [0u8; 4];
        let text = value.encode_utf8(&mut encoded);
        let mut data = Vec::with_capacity(text.len());
        data.extend_from_slice(text.as_bytes());
        checksum = checksum.wrapping_add(data.len() as u64 + 1);
    }
    println!("{checksum}");
}'''


def _pack(data: bytes) -> int:
    if len(data) > 8:
        raise ValueError("UTF-8 Inspector accepts at most eight bytes")
    return sum(byte << (8 * index) for index, byte in enumerate(data))


def _expected(data: bytes) -> tuple[int, bool, int | None, int | None]:
    try:
        decoded = data.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        return int(error.start), False, int(error.start), None
    return (
        ((1 << 63) | (len(decoded) << 32) | len(data)) & _MASK64,
        True,
        None,
        len(decoded),
    )


def _valid_corpus() -> list[dict[str, Any]]:
    boundaries = (
        0x0000,
        0x007F,
        0x0080,
        0x07FF,
        0x0800,
        0xD7FF,
        0xE000,
        0xFFFF,
        0x10000,
        0x10FFFF,
    )
    makers: list[tuple[str, Callable[[int], str]]] = [
        ("empty", lambda _i: ""),
        ("embedded_nul", lambda i: "\x00" + chr(0x41 + i % 20)),
        ("scalar_boundaries", lambda i: chr(boundaries[i % len(boundaries)])),
        ("ascii", lambda i: chr(0x20 + i)),
        ("two_byte_min", lambda i: chr(0x80 + i)),
        ("two_byte_mid", lambda i: chr(0x400 + i)),
        ("two_byte_max", lambda i: chr(0x7E0 + i)),
        ("three_byte_min", lambda i: chr(0x800 + i)),
        ("bmp_cyrillic", lambda i: chr(0x400 + i)),
        ("bmp_arabic", lambda i: chr(0x600 + i)),
        ("bmp_devanagari", lambda i: chr(0x900 + i)),
        ("bmp_cjk", lambda i: chr(0x4E00 + i)),
        ("pre_surrogate", lambda i: chr(0xD7FF - i)),
        ("post_surrogate", lambda i: chr(0xE000 + i)),
        ("four_byte_min", lambda i: chr(0x10000 + i)),
        ("emoji", lambda i: chr(0x1F600 + i)),
        ("plane_two", lambda i: chr(0x20000 + i)),
        ("ascii_pair", lambda i: chr(0x41 + i % 20) + chr(0x61 + i % 20)),
        ("two_byte_pair", lambda i: chr(0x180 + i) + chr(0x280 + i)),
        ("mixed_width", lambda i: chr(0x41 + i % 20) + chr(0x900 + i) + chr(0x1F600 + i)),
    ]
    cases: list[dict[str, Any]] = []
    for family, maker in makers:
        for index in range(TEXT_STRUCTURED_VALID_CASES // len(makers)):
            data = maker(index).encode("utf-8")
            expected, valid, error_offset, scalar_count = _expected(data)
            cases.append(
                {
                    "family": family,
                    "index": index,
                    "data": data,
                    "packed": _pack(data),
                    "expected": expected,
                    "valid": valid,
                    "error_offset": error_offset,
                    "scalar_count": scalar_count,
                }
            )
    rng = random.Random(TEXT_FUZZ_SEED)
    for index in range(TEXT_FUZZ_VALID_CASES):
        payload = bytearray()
        while len(payload) < 8:
            scalar = rng.randrange(0x110000)
            if 0xD800 <= scalar <= 0xDFFF:
                continue
            encoded = chr(scalar).encode("utf-8")
            if len(payload) + len(encoded) > 8:
                break
            payload.extend(encoded)
            if rng.randrange(3) == 0:
                break
        data = bytes(payload)
        expected, valid, error_offset, scalar_count = _expected(data)
        cases.append(
            {
                "family": "seeded_random_valid",
                "index": index,
                "data": data,
                "packed": _pack(data),
                "expected": expected,
                "valid": valid,
                "error_offset": error_offset,
                "scalar_count": scalar_count,
            }
        )
    return cases


def _invalid_corpus() -> list[dict[str, Any]]:
    makers: list[tuple[str, Callable[[int], bytes]]] = [
        ("lone_continuation", lambda i: bytes((0x80 + i % 0x40,))),
        ("overlong_c0", lambda i: bytes((0xC0, 0x80 + i % 0x40))),
        ("overlong_c1", lambda i: bytes((0xC1, 0x80 + i % 0x40))),
        ("overlong_e0", lambda i: bytes((0xE0, 0x80 + i % 0x20, 0x80))),
        ("surrogate_ed", lambda i: bytes((0xED, 0xA0 + i % 0x20, 0x80))),
        ("overlong_f0", lambda i: bytes((0xF0, 0x80 + i % 0x10, 0x80, 0x80))),
        ("above_max_f4", lambda i: bytes((0xF4, 0x90 + i % 0x30, 0x80, 0x80))),
        ("leading_f5", lambda i: bytes((0xF5 + i % 3, 0x80, 0x80, 0x80))),
        ("leading_fe_ff", lambda i: bytes((0xFE + i % 2,))),
        ("truncated_two", lambda i: bytes((0xC2 + i % 0x1E,))),
        ("truncated_three_one", lambda i: bytes((0xE1 + i % 0x0C,))),
        ("truncated_three_two", lambda i: bytes((0xE1, 0x80 + i % 0x40))),
        ("truncated_four_one", lambda i: bytes((0xF0 + i % 5,))),
        ("truncated_four_two", lambda i: bytes((0xF1, 0x80 + i % 0x40))),
        ("truncated_four_three", lambda i: bytes((0xF1, 0x80, 0x80 + i % 0x40))),
        ("bad_two_continuation", lambda i: bytes((0xC2, i % 0x80))),
        ("bad_three_second", lambda i: bytes((0xE2, i % 0x80, 0x80))),
        ("bad_three_third", lambda i: bytes((0xE2, 0x80, i % 0x80))),
        ("bad_four_second", lambda i: bytes((0xF1, i % 0x80, 0x80, 0x80))),
        ("bad_four_tail", lambda i: bytes((0xF1, 0x80, 0x80, i % 0x80))),
    ]
    cases: list[dict[str, Any]] = []
    for family, maker in makers:
        for index in range(TEXT_STRUCTURED_INVALID_CASES // len(makers)):
            prefix = bytes((0x41,)) * (index % 4)
            data = prefix + maker(index)
            expected, valid, error_offset, scalar_count = _expected(data)
            cases.append(
                {
                    "family": family,
                    "index": index,
                    "data": data,
                    "packed": _pack(data),
                    "expected": expected,
                    "valid": valid,
                    "error_offset": error_offset,
                    "scalar_count": scalar_count,
                }
            )
    rng = random.Random(TEXT_FUZZ_SEED ^ 0xFFFFFFFF)
    index = 0
    while index < TEXT_FUZZ_INVALID_CASES:
        data = bytes(
            rng.randrange(256) for _ in range(rng.randrange(1, 9))
        )
        expected, valid, error_offset, scalar_count = _expected(data)
        if valid:
            continue
        cases.append(
            {
                "family": "seeded_random_invalid",
                "index": index,
                "data": data,
                "packed": _pack(data),
                "expected": expected,
                "valid": valid,
                "error_offset": error_offset,
                "scalar_count": scalar_count,
            }
        )
        index += 1
    return cases


def _shrink_bytes(
    data: bytes,
    predicate: Callable[[bytes], bool],
) -> bytes:
    current = data
    changed = True
    while changed:
        changed = False
        for index in range(len(current)):
            candidate = current[:index] + current[index + 1 :]
            if predicate(candidate):
                current = candidate
                changed = True
                break
    for index, byte in enumerate(current):
        if byte == 0:
            continue
        candidate = current[:index] + b"\x00" + current[index + 1 :]
        if predicate(candidate):
            current = candidate
    return current


def _parse_native(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        checksum = int(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        checksum = None
    metrics = {}
    for key in ("ALLOCATIONS", "FREES", "PAYLOAD_COPIES"):
        matches = re.findall(rf"MELDRA_{key}=(\d+)", completed.stderr)
        metrics[key.lower()] = int(matches[-1]) if matches else None
    return {
        "returncode": completed.returncode,
        "checksum": checksum,
        **metrics,
    }


def _run_native(binary: str, arguments: Iterable[int]) -> dict[str, Any]:
    completed = subprocess.run(
        [binary, *(str(value) for value in arguments)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return _parse_native(completed)


def _corpus(root: Path) -> tuple[dict[str, Any], Any, Any, str, dict[str, Any]]:
    hir = compile_native_hir(UTF8_INSPECTOR_SOURCE, path="utf8-inspector.meldra")
    frontend = compile_performance_source(
        UTF8_INSPECTOR_SOURCE, path="utf8-inspector.meldra"
    )
    original = frontend.mir
    optimized, snapshots = optimize_mir(original)
    generated = CEmitter(optimized, runtime_arguments=True).emit()
    build = compile_c_source(
        generated, output_dir=root / "native", stem="utf8-inspector"
    )
    valid_cases = _valid_corpus()
    invalid_cases = _invalid_corpus()
    failures: list[dict[str, Any]] = []
    native_executions = 0
    zero_copy_native = True

    def diverges(candidate: bytes) -> bool:
        expected, _valid, _offset, _scalars = _expected(candidate)
        candidate_arguments = (
            _pack(candidate),
            len(candidate),
            1,
        )
        candidate_hir = evaluate_hir(hir, candidate_arguments)
        candidate_mir = evaluate_mir(original, candidate_arguments)
        candidate_optimized = evaluate_mir(
            optimized, candidate_arguments
        )
        candidate_native = (
            _run_native(
                str(build.binary_path), candidate_arguments
            )
            if build.binary_path
            else {"returncode": None, "checksum": None}
        )
        return not (
            candidate_hir.status == "OK"
            and candidate_mir.status == "OK"
            and candidate_optimized.status == "OK"
            and candidate_native["returncode"] == 0
            and {
                expected,
                candidate_hir.return_value,
                candidate_mir.return_value,
                candidate_optimized.return_value,
                candidate_native["checksum"],
            }
            == {expected}
        )
    for case in (*valid_cases, *invalid_cases):
        arguments = (case["packed"], len(case["data"]), 1)
        hir_result = evaluate_hir(hir, arguments)
        mir_result = evaluate_mir(original, arguments)
        optimized_result = evaluate_mir(optimized, arguments)
        native = (
            _run_native(str(build.binary_path), arguments)
            if build.binary_path
            else {
                "returncode": None,
                "checksum": None,
                "allocations": None,
                "frees": None,
                "payload_copies": None,
            }
        )
        if build.binary_path:
            native_executions += 1
            expected_heap_ops = 0 if not case["data"] else 1
            zero_copy_native = zero_copy_native and (
                native["payload_copies"] == 0
                and native["allocations"] == expected_heap_ops
                and native["frees"] == expected_heap_ops
            )
        expected = case["expected"]
        observed = {
            "reference": expected,
            "hir": hir_result.return_value,
            "mir": mir_result.return_value,
            "optimized_mir": optimized_result.return_value,
            "native": native["checksum"],
        }
        passed = (
            hir_result.status == "OK"
            and mir_result.status == "OK"
            and optimized_result.status == "OK"
            and native["returncode"] == 0
            and len(set(observed.values())) == 1
        )
        if not passed:
            failures.append(
                {
                    "family": case["family"],
                    "index": case["index"],
                    "bytes_hex": case["data"].hex(),
                    "observed": observed,
                    "hir_status": hir_result.status,
                    "mir_status": mir_result.status,
                    "optimized_status": optimized_result.status,
                    "native": native,
                    "minimized_bytes_hex": _shrink_bytes(
                        case["data"], diverges
                    ).hex(),
                }
            )
    return (
        {
            "valid": {
                "case_count": len(valid_cases),
                "family_count": len({case["family"] for case in valid_cases}),
                "families": sorted({case["family"] for case in valid_cases}),
            },
            "invalid": {
                "case_count": len(invalid_cases),
                "family_count": len({case["family"] for case in invalid_cases}),
                "families": sorted({case["family"] for case in invalid_cases}),
                "error_offsets_checked": len(invalid_cases),
            },
            "fuzz": {
                "seed": TEXT_FUZZ_SEED,
                "valid_cases": TEXT_FUZZ_VALID_CASES,
                "invalid_cases": TEXT_FUZZ_INVALID_CASES,
                "shrinker": "delete_each_byte_to_fixed_point_then_zero_each_byte",
                "failures_minimized": len(failures),
            },
            "native_executions": native_executions,
            "unexpected_failure": len(failures),
            "failures": failures,
            "all_surfaces_agree": not failures,
            "native_zero_payload_copies": zero_copy_native,
            "optimization_passes": [
                snapshot.to_dict() for snapshot in snapshots
            ],
        },
        hir,
        optimized,
        generated,
        asdict(build),
    )


def _textview_acceptance(root: Path) -> dict[str, Any]:
    data = "AЖB".encode("utf-8")
    cases = (
        ("full", 0, 4),
        ("empty_start", 0, 0),
        ("empty_end", 4, 0),
        ("prefix", 0, 1),
        ("middle", 1, 2),
        ("suffix", 3, 1),
    )
    hir = compile_native_hir(
        TEXTVIEW_INSPECTOR_SOURCE, path="textview-inspector.meldra"
    )
    original = compile_performance_source(
        TEXTVIEW_INSPECTOR_SOURCE,
        path="textview-inspector.meldra",
    ).mir
    optimized, _ = optimize_mir(original)
    generated = CEmitter(optimized, runtime_arguments=True).emit()
    build = compile_c_source(
        generated,
        output_dir=root / "textview",
        stem="program",
    )
    reports = []
    for name, start, take in cases:
        selected = data[start : start + take]
        checksum = 0
        for byte in selected:
            checksum = (checksum * 257 + byte) & _MASK64
        expected = ((take << 32) | checksum) & _MASK64
        arguments = (_pack(data), len(data), start, take)
        hir_result = evaluate_hir(hir, arguments)
        mir_result = evaluate_mir(original, arguments)
        optimized_result = evaluate_mir(optimized, arguments)
        native = (
            _run_native(str(build.binary_path), arguments)
            if build.binary_path
            else {"returncode": None, "checksum": None}
        )
        reports.append(
            {
                "family": name,
                "start": start,
                "length": take,
                "payload_hex": selected.hex(),
                "expected": expected,
                "hir": hir_result.return_value,
                "mir": mir_result.return_value,
                "optimized_mir": optimized_result.return_value,
                "native": native,
                "passed": hir_result.return_value == expected
                and mir_result.return_value == expected
                and optimized_result.return_value == expected
                and native.get("returncode") == 0
                and native.get("checksum") == expected,
            }
        )
    operations = [
        instruction
        for function in optimized.functions
        for block in function.blocks
        for instruction in block.instructions
    ]
    text_views = [
        instruction
        for instruction in operations
        if instruction.op == "text_view"
    ]
    text_borrow_ends = [
        instruction
        for instruction in operations
        if instruction.op == "borrow_end"
        and instruction.attribute_map.get("text_owner") is not None
    ]
    return {
        "passed": all(report["passed"] for report in reports)
        and len(text_views) == len(text_borrow_ends) == 1,
        "cases": reports,
        "build": asdict(build),
        "text_view_count": len(text_views),
        "text_borrow_end_count": len(text_borrow_ends),
        "borrow_ids_balanced": {
            str(item.attribute_map.get("borrow_id"))
            for item in text_views
        }
        == {
            str(item.attribute_map.get("borrow_id"))
            for item in text_borrow_ends
        },
        "root_owners": sorted(
            {
                str(item.attribute_map.get("root_owner"))
                for item in text_views
            }
        ),
    }


def _text_construction_acceptance(root: Path) -> dict[str, Any]:
    checks = []
    for name, source, arguments, expected, heap_ops in (
        (
            "ascii_and_scalar",
            TEXT_CONSTRUCTION_SOURCE,
            (65, 0x1F600),
            5,
            2,
        ),
        (
            "repeated_create_destroy",
            SCALAR_BENCHMARK_SOURCE,
            (0x1F600, 32),
            160,
            32,
        ),
    ):
        hir = compile_native_hir(
            source, path=f"{name}.meldra"
        )
        original = compile_performance_source(
            source, path=f"{name}.meldra"
        ).mir
        optimized, _ = optimize_mir(original)
        results = (
            evaluate_hir(hir, arguments),
            evaluate_mir(original, arguments),
            evaluate_mir(optimized, arguments),
        )
        build = compile_c_source(
            CEmitter(
                optimized, runtime_arguments=True
            ).emit(),
            output_dir=root / name,
            stem="program",
        )
        native = (
            _run_native(str(build.binary_path), arguments)
            if build.binary_path
            else {"returncode": None, "checksum": None}
        )
        checks.append(
            {
                "name": name,
                "expected": expected,
                "heap_operations": heap_ops,
                "surfaces": [
                    result.to_dict() for result in results
                ],
                "native": native,
                "build": asdict(build),
                "passed": all(
                    result.status == "OK"
                    and result.return_value == expected
                    and result.allocations == heap_ops
                    and result.frees == heap_ops
                    for result in results
                )
                and native.get("returncode") == 0
                and native.get("checksum") == expected
                and native.get("allocations") == heap_ops
                and native.get("frees") == heap_ops,
            }
        )
    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def _text_bytes_text_roundtrip(root: Path) -> dict[str, Any]:
    hir = compile_native_hir(
        TEXT_BYTES_TEXT_SOURCE, path="text-bytes-text.meldra"
    )
    original = compile_performance_source(
        TEXT_BYTES_TEXT_SOURCE, path="text-bytes-text.meldra"
    ).mir
    optimized, _ = optimize_mir(original)
    arguments = (0x1F600,)
    results = (
        evaluate_hir(hir, arguments),
        evaluate_mir(original, arguments),
        evaluate_mir(optimized, arguments),
    )
    build = compile_c_source(
        CEmitter(optimized, runtime_arguments=True).emit(),
        output_dir=root / "text-bytes-text",
        stem="program",
    )
    native = (
        _run_native(str(build.binary_path), arguments)
        if build.binary_path
        else {"returncode": None, "checksum": None}
    )
    operations = [
        instruction.op
        for function in optimized.functions
        for block in function.blocks
        for instruction in block.instructions
    ]
    return {
        "passed": all(
            result.status == "OK" and result.return_value == 4
            for result in results
        )
        and native.get("returncode") == 0
        and native.get("checksum") == 4
        and native.get("payload_copies") == 0
        and operations.count("text_to_bytes_transfer") == 2
        and operations.count("utf8_validate") == 1,
        "hir": results[0].to_dict(),
        "mir": results[1].to_dict(),
        "optimized_mir": results[2].to_dict(),
        "native": native,
        "build": asdict(build),
        "operations": operations,
    }


def _diagnostics() -> dict[str, Any]:
    incomplete = """fn main(n: UInt64) -> UInt64:
 let data: Bytes = Bytes.new(n)
 let decoded: Utf8Decode = Text.from_utf8(move(data))
 return n
"""
    live_view = """fn main(scalar: UInt64) -> UInt64:
 let text: Text = Text.from_scalar(scalar)
 let view: TextView = text.as_view()
 let bytes: Bytes = text.into_bytes()
 return view.len_bytes() + bytes.len()
"""
    derived_view = """fn main(scalar: UInt64) -> UInt64:
 let text: Text = Text.from_scalar(scalar)
 let view: TextView = text.as_view()
 let raw: BytesView = view.as_bytes()
 let bytes: Bytes = text.into_bytes()
 return raw.len() + bytes.len()
"""
    wrong_root = """fn bad(scalar: UInt64) -> TextView:
 let text: Text = Text.from_scalar(scalar)
 let view: TextView = text.as_view()
 return view

fn main(n: UInt64) -> UInt64:
 return n
"""
    reports: dict[str, Any] = {}
    for name, source, expected in (
        ("unmatched_decode", incomplete, "must be handled by an exhaustive"),
        ("move_during_live_view", live_view, "while TextView view is live"),
        ("derived_view_root", derived_view, "while TextView raw is live"),
        ("wrong_textview_root", wrong_root, "BorrowReturnLocalOwnerEscape"),
    ):
        try:
            compile_performance_source(source, path=f"{name}.meldra")
        except PerformanceCompileError as error:
            reports[name] = {
                "rejected": expected in str(error),
                "diagnostic": str(error),
            }
        else:
            reports[name] = {"rejected": False, "diagnostic": None}
    boundary = compile_performance_source(
        BOUNDARY_SOURCE, path="boundary.meldra"
    ).mir
    boundary_result = evaluate_mir(boundary, (0x1F600,))
    reports["non_boundary_slice"] = {
        "rejected": boundary_result.status == "ERROR"
        and boundary_result.error_kind == "TextSliceNotOnUtf8Boundary",
        "diagnostic": boundary_result.error_kind,
    }
    scalar_source = """fn main(value: UInt64) -> UInt64:
 let text: Text = Text.from_scalar(value)
 return text.len_bytes()
"""
    scalar_mir = compile_performance_source(
        scalar_source, path="scalar.meldra"
    ).mir
    scalar_result = evaluate_mir(scalar_mir, (0xD800,))
    reports["surrogate_scalar"] = {
        "rejected": scalar_result.status == "ERROR"
        and scalar_result.error_kind == "InvalidUnicodeScalar",
        "diagnostic": scalar_result.error_kind,
    }
    return {
        "passed": all(item["rejected"] for item in reports.values()),
        "checks": reports,
    }


def _sanitizers(
    root: Path,
    generated: str,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    executions = 0
    acceptance_mir = optimize_mir(
        compile_performance_source(
            SANITIZER_TEXTVIEW_SOURCE,
            path="sanitizer-textview.meldra",
        ).mir
    )[0]
    acceptance_c = CEmitter(
        acceptance_mir, runtime_arguments=True
    ).emit()
    large_mir = optimize_mir(
        compile_performance_source(
            LARGE_TEXT_SOURCE, path="sanitizer-large-text.meldra"
        ).mir
    )[0]
    large_c = CEmitter(large_mir, runtime_arguments=True).emit()
    for name, flag in (
        ("asan", "address"),
        ("ubsan", "undefined"),
        ("lsan", "leak"),
    ):
        builds = {
            "corpus": _compile_sanitized(
                generated, root / name / "utf8-inspector", flag
            ),
            "textview_transfer": _compile_sanitized(
                acceptance_c, root / name / "textview-transfer", flag
            ),
            "large_text": _compile_sanitized(
                large_c, root / name / "large-text", flag
            ),
        }
        environment = dict(os.environ)
        if name in {"asan", "lsan"}:
            environment["ASAN_OPTIONS"] = (
                "detect_leaks=1:halt_on_error=1"
            )
            environment["LSAN_OPTIONS"] = "exitcode=23"

        def execute(
            family: str,
            build: dict[str, Any],
            arguments: tuple[int, ...],
            expected: int,
        ) -> dict[str, Any]:
            nonlocal executions
            if not build.get("binary"):
                return {
                    "family": family,
                    "passed": False,
                    "build_unavailable": True,
                }
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
            violation = any(
                marker in completed.stderr
                for marker in _SANITIZER_MARKERS
            )
            parsed = _parse_native(completed)
            return {
                "family": family,
                "returncode": completed.returncode,
                "checksum": parsed["checksum"],
                "expected": expected,
                "sanitizer_violation": violation,
                "passed": completed.returncode == 0
                and parsed["checksum"] == expected
                and not violation,
            }

        runs = [
            execute(
                str(case["family"]),
                builds["corpus"],
                (case["packed"], len(case["data"]), 1),
                int(case["expected"]),
            )
            for case in cases
        ]
        runs.extend(
            [
                execute(
                    "textview_slice_and_into_bytes",
                    builds["textview_transfer"],
                    (0x1F600,),
                    9,
                ),
                execute(
                    "large_text_4096",
                    builds["large_text"],
                    (4096,),
                    4096,
                ),
            ]
        )
        reports[name] = {
            "builds": builds,
            "runs": runs,
            "valid_family_count": len(
                {
                    case["family"]
                    for case in cases
                    if case["valid"]
                }
            ),
            "invalid_family_count": len(
                {
                    case["family"]
                    for case in cases
                    if not case["valid"]
                }
            ),
            "passed": bool(runs)
            and all(run["passed"] for run in runs),
        }
    return {
        "passed": all(
            report["passed"] for report in reports.values()
        ),
        "native_executions": executions,
        "reports": reports,
    }


def _compile_raw(
    root: Path,
    source_text: str,
    *,
    stem: str,
) -> dict[str, Any]:
    compiler = find_c_compiler()
    root.mkdir(parents=True, exist_ok=True)
    source = root / f"{stem}.c"
    binary = root / stem
    source.write_text(source_text, encoding="utf-8")
    if compiler is None:
        return {
            "status": "UNMEASURED_COMPILER_UNAVAILABLE",
            "binary": None,
            "stderr": "compiler unavailable",
        }
    command = (
        compiler,
        "-std=c11",
        "-O3",
        "-fwrapv",
        str(source),
        "-o",
        str(binary),
    )
    started = time.perf_counter_ns()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    return {
        "status": (
            "MEASURED" if completed.returncode == 0 else "FAILED"
        ),
        "binary": str(binary) if completed.returncode == 0 else None,
        "command": list(command),
        "compile_time_ms": (
            time.perf_counter_ns() - started
        )
        / 1_000_000,
        "binary_size": (
            binary.stat().st_size if completed.returncode == 0 else None
        ),
        "source_size": len(source_text.encode()),
        "source_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
        "stderr": completed.stderr,
    }


def _compile_rust(
    root: Path,
    source_text: str,
    *,
    stem: str,
) -> dict[str, Any]:
    compiler = shutil.which("rustc")
    root.mkdir(parents=True, exist_ok=True)
    source = root / f"{stem}.rs"
    binary = root / stem
    source.write_text(source_text, encoding="utf-8")
    if compiler is None:
        return {
            "status": "UNMEASURED_COMPILER_UNAVAILABLE",
            "binary": None,
            "source_size": len(source_text.encode()),
            "source_sha256": hashlib.sha256(
                source_text.encode()
            ).hexdigest(),
            "stderr": "rustc unavailable",
        }
    command = (
        compiler,
        "-C",
        "opt-level=3",
        "-C",
        "debuginfo=0",
        str(source),
        "-o",
        str(binary),
    )
    started = time.perf_counter_ns()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    return {
        "status": (
            "MEASURED" if completed.returncode == 0 else "FAILED"
        ),
        "binary": str(binary) if completed.returncode == 0 else None,
        "command": list(command),
        "compile_time_ms": (
            time.perf_counter_ns() - started
        )
        / 1_000_000,
        "binary_size": (
            binary.stat().st_size if completed.returncode == 0 else None
        ),
        "source_size": len(source_text.encode()),
        "source_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
        "stderr": completed.stderr,
    }


def _pinned_command(binary: str, arguments: tuple[int, ...]) -> tuple[list[str], int | None]:
    command = [binary, *(str(value) for value in arguments)]
    affinity = None
    try:
        available = sorted(os.sched_getaffinity(0))
        affinity = available[0] if available else None
    except (AttributeError, OSError):
        affinity = None
    if affinity is not None and shutil.which("taskset"):
        command = [
            str(shutil.which("taskset")),
            "-c",
            str(affinity),
            *command,
        ]
    return command, affinity


def _timed_once(
    binary: str,
    arguments: tuple[int, ...],
) -> tuple[float, subprocess.CompletedProcess[str]]:
    command, _affinity = _pinned_command(binary, arguments)
    started = time.perf_counter_ns()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return (
        (time.perf_counter_ns() - started) / 1_000_000,
        completed,
    )


def _rss_kb(binary: str, arguments: tuple[int, ...]) -> int | None:
    timer = Path("/usr/bin/time")
    if not timer.is_file():
        return None
    command, _affinity = _pinned_command(binary, arguments)
    completed = subprocess.run(
        [str(timer), "-f", "TEXT_RSS_KB=%M", *command],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    matches = re.findall(r"TEXT_RSS_KB=(\d+)", completed.stderr)
    return int(matches[-1]) if matches else None


def _surface_metrics(source: str) -> dict[str, Any]:
    tokens = re.findall(
        r"[A-Za-z_]\w*|\d+|->|==|!=|<=|>=|<<|>>|\S",
        source,
    )
    ownership = re.findall(
        r"\b(?:move|drop|finish|into_bytes|from_utf8|as_view)\b",
        source,
    )
    return {
        "tokens": len(tokens),
        "explicit_ownership_operations": len(ownership),
    }


def _benchmark_workload(
    *,
    name: str,
    arms: dict[str, tuple[str, tuple[int, ...]]],
    expected: int,
    processed_bytes: int,
    builds: dict[str, dict[str, Any]],
    sources: dict[str, str],
) -> dict[str, Any]:
    available = {
        arm: (binary, arguments)
        for arm, (binary, arguments) in arms.items()
        if binary
    }
    for _ in range(TEXT_BENCHMARK_WARMUPS):
        for binary, arguments in available.values():
            _timed_once(binary, arguments)
    rng = random.Random(f"meldra-text-{name}-v1")
    samples: dict[str, list[float]] = {
        arm: [] for arm in available
    }
    checksums: dict[str, list[int | None]] = {
        arm: [] for arm in available
    }
    orders = []
    for _ in range(TEXT_BENCHMARK_SAMPLES):
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
    reports: dict[str, Any] = {}
    for arm, values in samples.items():
        median = statistics.median(values)
        mad = statistics.median(
            abs(value - median) for value in values
        )
        binary, arguments = available[arm]
        _elapsed, metrics_run = _timed_once(binary, arguments)
        if arm == "meldra":
            counters = _parse_native(metrics_run)
        elif arm == "c":
            counters = {}
            for key in (
                "ALLOCATIONS",
                "FREES",
                "PAYLOAD_COPIES",
            ):
                matches = re.findall(
                    rf"RAW_{key}=(\d+)", metrics_run.stderr
                )
                counters[key.lower()] = (
                    int(matches[-1]) if matches else None
                )
        else:
            counters = {
                "allocations": "Vec implementation-defined",
                "frees": "Vec implementation-defined",
                "payload_copies": "Vec implementation-defined",
            }
        reports[arm] = {
            "median_ms": median,
            "mad_ms": mad,
            "mad_ratio": mad / median if median else None,
            "samples_ms": values,
            "checksums_correct": all(
                value == expected for value in checksums[arm]
            ),
            "throughput_gb_s": (
                processed_bytes / (median / 1000) / 1_000_000_000
            ),
            "peak_rss_kb": _rss_kb(binary, arguments),
            "counters": counters,
            "binary_size": builds[arm].get("binary_size"),
            "compile_time_ms": builds[arm].get(
                "compile_time_ms"
            ),
            "surface": _surface_metrics(sources[arm]),
        }
    relative_c = (
        reports["meldra"]["median_ms"] / reports["c"]["median_ms"]
        if {"meldra", "c"} <= reports.keys()
        else None
    )
    stable = all(
        report["mad_ratio"] is not None
        and report["mad_ratio"] <= TEXT_BENCHMARK_MAD_LIMIT
        for arm, report in reports.items()
        if arm in {"meldra", "c"}
    )
    return {
        "passed": relative_c is not None
        and relative_c <= TEXT_PERFORMANCE_LIMIT
        and stable
        and all(
            report["checksums_correct"]
            for report in reports.values()
        ),
        "warmups_per_arm": TEXT_BENCHMARK_WARMUPS,
        "measured_samples_per_arm": TEXT_BENCHMARK_SAMPLES,
        "randomized_orders": orders,
        "cpu_affinity": next(
            (
                _pinned_command(binary, arguments)[1]
                for binary, arguments in available.values()
            ),
            None,
        ),
        "relative_c": relative_c,
        "threshold": TEXT_PERFORMANCE_LIMIT,
        "mad_limit": TEXT_BENCHMARK_MAD_LIMIT,
        "arms": reports,
        "builds": builds,
    }


def _performance(
    root: Path,
    native_build: dict[str, Any],
) -> dict[str, Any]:
    validation_c = _compile_raw(
        root / "validation" / "c",
        RAW_INSPECTOR_C,
        stem="program",
    )
    validation_rust = _compile_rust(
        root / "validation" / "rust",
        RUST_INSPECTOR,
        stem="program",
    )
    scalar_mir = optimize_mir(
        compile_performance_source(
            SCALAR_BENCHMARK_SOURCE,
            path="scalar-benchmark.meldra",
        ).mir
    )[0]
    scalar_source = CEmitter(
        scalar_mir, runtime_arguments=True
    ).emit()
    scalar_meldra_result = compile_c_source(
        scalar_source,
        output_dir=root / "scalar" / "meldra",
        stem="program",
    )
    scalar_meldra = asdict(scalar_meldra_result)
    scalar_c = _compile_raw(
        root / "scalar" / "c",
        RAW_SCALAR_C,
        stem="program",
    )
    scalar_rust = _compile_rust(
        root / "scalar" / "rust",
        RUST_SCALAR,
        stem="program",
    )
    sequence = "AЖ😀".encode("utf-8")
    repetitions = TEXT_BENCHMARK_REPETITIONS
    unit, valid, _error, _count = _expected(sequence)
    if not valid:
        raise AssertionError("benchmark sequence must be valid UTF-8")
    validation_expected = (unit * repetitions) & _MASK64
    validation_builds = {
        "meldra": native_build,
        "c": validation_c,
        "rust": validation_rust,
    }
    validation_sources = {
        "meldra": UTF8_INSPECTOR_SOURCE,
        "c": RAW_INSPECTOR_C,
        "rust": RUST_INSPECTOR,
    }
    validation_arms = {
        "meldra": (
            str(native_build.get("binary_path") or ""),
            (_pack(sequence), len(sequence), repetitions),
        ),
        "c": (
            str(validation_c.get("binary") or ""),
            (_pack(sequence), len(sequence), repetitions),
        ),
        "rust": (
            str(validation_rust.get("binary") or ""),
            (_pack(sequence), len(sequence), repetitions),
        ),
    }
    validation = _benchmark_workload(
        name="utf8-validation",
        arms=validation_arms,
        expected=validation_expected,
        processed_bytes=len(sequence) * repetitions,
        builds=validation_builds,
        sources=validation_sources,
    )
    scalar = 0x1F600
    scalar_expected = ((4 + 1) * repetitions) & _MASK64
    scalar_builds = {
        "meldra": scalar_meldra,
        "c": scalar_c,
        "rust": scalar_rust,
    }
    scalar_sources = {
        "meldra": SCALAR_BENCHMARK_SOURCE,
        "c": RAW_SCALAR_C,
        "rust": RUST_SCALAR,
    }
    scalar_arms = {
        "meldra": (
            str(scalar_meldra.get("binary_path") or ""),
            (scalar, repetitions),
        ),
        "c": (
            str(scalar_c.get("binary") or ""),
            (scalar, repetitions),
        ),
        "rust": (
            str(scalar_rust.get("binary") or ""),
            (scalar, repetitions),
        ),
    }
    scalar_report = _benchmark_workload(
        name="unicode-scalar",
        arms=scalar_arms,
        expected=scalar_expected,
        processed_bytes=4 * repetitions,
        builds=scalar_builds,
        sources=scalar_sources,
    )
    rust_available = (
        validation_rust["status"] == "MEASURED"
        and scalar_rust["status"] == "MEASURED"
    )
    return {
        "passed": validation["passed"] and scalar_report["passed"],
        "threshold": TEXT_PERFORMANCE_LIMIT,
        "utf8_validation": validation,
        "unicode_scalar": scalar_report,
        "rust_policy": (
            "measured"
            if rust_available
            else "UNMEASURED_COMPILER_UNAVAILABLE; no Rust claim"
        ),
    }

def _assembly(
    root: Path,
    native_build: dict[str, Any],
    *,
    stem: str,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    compiler = native_build.get("compiler") or find_c_compiler()
    source = native_build.get("source_path")
    if not compiler or not source:
        return {"status": "UNMEASURED_COMPILER_UNAVAILABLE"}
    assembly = root / f"{stem}.s"
    command = [
        str(compiler),
        "-std=c11",
        "-O3",
        "-S",
        str(source),
        "-o",
        str(assembly),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    text = assembly.read_text(encoding="utf-8") if completed.returncode == 0 else ""
    return {
        "status": "MEASURED" if completed.returncode == 0 else "FAILED",
        "path": str(assembly),
        "sha256": hashlib.sha256(text.encode()).hexdigest() if text else None,
        "malloc_call_sites": len(re.findall(r"\bcall[^\n]*malloc", text)),
        "free_call_sites": len(re.findall(r"\bcall[^\n]*free", text)),
        "memcpy_call_sites": len(re.findall(r"\bcall[^\n]*memcpy", text)),
        "utf8_validation_visible": "utf8" in text.lower(),
        "stderr": completed.stderr,
    }


def _cache_snapshot(root: Path) -> dict[str, dict[str, Any]]:
    names = (
        "tools/benchmarks/merlo/benchmarks/meldra_bytes_experiment.json",
        "tools/benchmarks/merlo/benchmarks/meldra_bytes_call_boundary.json",
        "tools/benchmarks/merlo/benchmarks/meldra_bytes_reborrow.json",
        "tools/benchmarks/merlo/benchmarks/meldra_bytes_borrowed_return.json",
        "tools/benchmarks/merlo/benchmarks/meldra_bytes_builder.json",
    )
    snapshot = {}
    for name in names:
        path = root / name
        if not path.is_file():
            continue
        stat = path.stat()
        snapshot[name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return snapshot


def _falsification_controls(
    root: Path,
    optimized: Any,
    generated: str,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    mutated_functions = []
    copy_mutated = False
    for function in optimized.functions:
        blocks = []
        for block in function.blocks:
            instructions = []
            for instruction in block.instructions:
                if (
                    not copy_mutated
                    and instruction.op == "bytes_to_text_transfer"
                ):
                    attributes = {
                        **instruction.attribute_map,
                        "payload_copies": 1,
                    }
                    instruction = instruction.replace(
                        attributes=attributes
                    )
                    copy_mutated = True
                instructions.append(instruction)
            blocks.append(
                replace(block, instructions=tuple(instructions))
            )
        mutated_functions.append(
            replace(function, blocks=tuple(blocks))
        )
    copy_control_mir = replace(
        optimized, functions=tuple(mutated_functions)
    )
    copy_validation = validate_text_mir(copy_control_mir)

    free_pattern = re.compile(
        r"if \((meldra_v_\d+)\.data != NULL\) "
        r"\{ free\(\1\.data\); \+\+meldra_heap_frees; \}"
    )
    missing_free_source, replacements = free_pattern.subn(
        r"if (\1.data != NULL) { /* deliberate missing free */ }",
        generated,
        count=1,
    )
    missing_free_build = _compile_sanitized(
        missing_free_source,
        root / "missing-invalid-free",
        "leak",
    )
    missing_free_detected = False
    missing_free_stderr = ""
    if missing_free_build.get("binary") and replacements == 1:
        case = _invalid_corpus()[0]
        environment = dict(os.environ)
        environment["ASAN_OPTIONS"] = "detect_leaks=1:halt_on_error=1"
        environment["LSAN_OPTIONS"] = "exitcode=23"
        completed = subprocess.run(
            [
                str(missing_free_build["binary"]),
                str(case["packed"]),
                str(len(case["data"])),
                "1",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=environment,
        )
        missing_free_stderr = completed.stderr
        missing_free_detected = (
            completed.returncode != 0
            and "LeakSanitizer" in completed.stderr
        )
    checks = {
        "deliberate_payload_copy": {
            "detected": copy_mutated
            and not copy_validation[
                "transfer_payload_copies_zero"
            ],
            "validation": copy_validation,
        },
        "wrong_boundary_acceptance": {
            "detected": diagnostics["checks"][
                "non_boundary_slice"
            ]["rejected"],
        },
        "wrong_textview_root": {
            "detected": diagnostics["checks"][
                "wrong_textview_root"
            ]["rejected"],
        },
        "derived_textview_root": {
            "detected": diagnostics["checks"][
                "derived_view_root"
            ]["rejected"],
        },
        "missing_invalid_free": {
            "detected": missing_free_detected,
            "mutation_count": replacements,
            "build": missing_free_build,
            "stderr_sha256": hashlib.sha256(
                missing_free_stderr.encode()
            ).hexdigest(),
        },
    }
    return {
        "passed": all(
            check["detected"] for check in checks.values()
        ),
        "checks": checks,
    }


def _frozen_hashes(root: Path) -> dict[str, str]:
    names = (
        "meldra/performance_mir.py",
        "meldra/performance_frontend.py",
        "meldra/performance_opt.py",
        "meldra/native_hir.py",
        "meldra/native_differential.py",
        "meldra/native_c_backend.py",
        "meldra/builder_call_boundary.py",
        "meldra/builder_call_boundary_experiment.py",
        "meldra/text_core.py",
        "meldra/text_core_experiment.py",
    )
    return {
        name: frozen_sha256(root, name)
        for name in names
    }


def _environment(native_build: dict[str, Any]) -> dict[str, Any]:
    def setting(path: str) -> str | None:
        candidate = Path(path)
        if not candidate.is_file():
            return None
        return candidate.read_text(encoding="utf-8").strip()

    cpu_model = None
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            if line.startswith("model name") and ":" in line:
                cpu_model = line.split(":", 1)[1].strip()
                break
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "cpu_model": cpu_model,
        "cpu_governor": setting(
            "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
        ),
        "intel_pstate_no_turbo": setting(
            "/sys/devices/system/cpu/intel_pstate/no_turbo"
        ),
        "c_compiler": native_build.get("compiler"),
        "c_compiler_version": native_build.get("compiler_version"),
        "c_compile_command": native_build.get("command"),
        "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
    }


def validate_text_report(report: dict[str, Any]) -> list[str]:
    failures = []
    corpus = report.get("corpus", {})
    valid = corpus.get("valid", {})
    invalid = corpus.get("invalid", {})
    checks = {
        "valid corpus below 640": valid.get("case_count", 0) >= 640,
        "valid family count below 20": valid.get("family_count", 0) >= 20,
        "invalid corpus below 480": invalid.get("case_count", 0) >= 480,
        "invalid family count below 18": invalid.get("family_count", 0) >= 18,
        "surface disagreement": corpus.get("all_surfaces_agree") is True,
        "native corpus incomplete": corpus.get("native_executions")
        == valid.get("case_count", 0) + invalid.get("case_count", 0),
        "payload copy detected": corpus.get("native_zero_payload_copies") is True,
        "contract validation failed": report.get("contracts", {})
        .get("optimized_mir", {})
        .get("validation", {})
        .get("typed_decode_match_present")
        is True,
        "builder boundary failed": report.get("builder_boundary", {}).get("passed") is True,
        "diagnostic control failed": report.get("diagnostics", {}).get("passed") is True,
        "TextView acceptance failed": report.get("textview_acceptance", {}).get("passed") is True,
        "Text construction acceptance failed": report.get("text_construction_acceptance", {}).get("passed") is True,
        "sanitizer failure": report.get("sanitizers", {}).get("passed") is True,
        "roundtrip failure": report.get("zero_copy_roundtrip", {}).get("passed") is True,
        "performance gate failed": report.get("performance", {}).get("passed") is True,
        "falsification control failed": report.get("falsification_controls", {}).get("passed") is True,
        "predecessor cache changed": report.get("cache_reuse", {}).get("unchanged") is True,
    }
    for message, passed in checks.items():
        if not passed:
            failures.append(message)
    return failures


def run_text_core_experiment(
    *,
    output_dir: str | Path = "tools/benchmarks/merlo/benchmarks/meldra_text_core_sprint",
    report_path: str | Path = "tools/benchmarks/merlo/benchmarks/meldra_text_core_sprint.json",
) -> dict[str, Any]:
    started = time.perf_counter()
    repository = Path.cwd()
    cache_before = _cache_snapshot(repository)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    builder_boundary = run_builder_call_boundary_gate(
        output_dir=root / "builder-boundary"
    )
    if not builder_boundary["passed"]:
        raise RuntimeError(
            "builder call boundary prerequisite gate failed"
        )
    corpus, hir, optimized, generated, native_build = _corpus(root)
    representative = _valid_corpus() + _invalid_corpus()
    frozen_hashes = _frozen_hashes(repository)
    environment = _environment(native_build)
    reproducible_inputs = {
        "experiment_version": TEXT_CORE_EXPERIMENT_VERSION,
        "source_sha256": {
            "utf8_inspector": hashlib.sha256(
                UTF8_INSPECTOR_SOURCE.encode()
            ).hexdigest(),
            "textview_inspector": hashlib.sha256(
                TEXTVIEW_INSPECTOR_SOURCE.encode()
            ).hexdigest(),
            "unicode_scalar": hashlib.sha256(
                SCALAR_BENCHMARK_SOURCE.encode()
            ).hexdigest(),
            "c_utf8_reference": hashlib.sha256(
                RAW_INSPECTOR_C.encode()
            ).hexdigest(),
            "c_scalar_reference": hashlib.sha256(
                RAW_SCALAR_C.encode()
            ).hexdigest(),
            "rust_utf8_reference": hashlib.sha256(
                RUST_INSPECTOR.encode()
            ).hexdigest(),
            "rust_scalar_reference": hashlib.sha256(
                RUST_SCALAR.encode()
            ).hexdigest(),
        },
        "frozen_hashes": frozen_hashes,
        "corpus": {
            "valid_cases": TEXT_VALID_CASES,
            "invalid_cases": TEXT_INVALID_CASES,
            "fuzz_seed": TEXT_FUZZ_SEED,
        },
        "measurement": {
            "repetitions": TEXT_BENCHMARK_REPETITIONS,
            "warmups": TEXT_BENCHMARK_WARMUPS,
            "samples": TEXT_BENCHMARK_SAMPLES,
            "mad_limit": TEXT_BENCHMARK_MAD_LIMIT,
            "relative_c_limit": TEXT_PERFORMANCE_LIMIT,
        },
        "compiler": {
            "path": environment["c_compiler"],
            "version": environment["c_compiler_version"],
            "command": environment["c_compile_command"],
        },
    }
    reproducible_input_payload = json.dumps(
        reproducible_inputs,
        sort_keys=True,
        separators=(",", ":"),
    )
    diagnostics = _diagnostics()
    textview_acceptance = _textview_acceptance(
        root / "textview-acceptance"
    )
    text_construction_acceptance = (
        _text_construction_acceptance(
            root / "text-construction-acceptance"
        )
    )
    sanitizers = _sanitizers(
        root / "sanitizers", generated, representative
    )
    roundtrip_mir = compile_performance_source(
        ROUNDTRIP_SOURCE, path="text-roundtrip.meldra"
    ).mir
    roundtrip_optimized, _ = optimize_mir(roundtrip_mir)
    roundtrip = evaluate_mir(roundtrip_optimized, (0x1F600,))
    roundtrip_build = compile_c_source(
        CEmitter(
            roundtrip_optimized, runtime_arguments=True
        ).emit(),
        output_dir=root / "roundtrip",
        stem="program",
    )
    roundtrip_native = (
        _run_native(
            str(roundtrip_build.binary_path), (0x1F600,)
        )
        if roundtrip_build.binary_path
        else {
            "returncode": None,
            "checksum": None,
            "payload_copies": None,
        }
    )
    zero_copy_roundtrip = {
        "passed": roundtrip.status == "OK"
        and roundtrip.return_value == 8
        and roundtrip.allocations == 1
        and roundtrip.frees == 1
        and roundtrip_native.get("returncode") == 0
        and roundtrip_native.get("checksum") == 8
        and roundtrip_native.get("payload_copies") == 0,
        "mir": roundtrip.to_dict(),
        "native": roundtrip_native,
        "build": asdict(roundtrip_build),
    }
    text_bytes_text = _text_bytes_text_roundtrip(
        root / "text-bytes-text"
    )
    text_construction_acceptance["valid_utf8_transfer"] = {
        "passed": text_bytes_text["passed"],
        "valid_corpus_cases": corpus["valid"]["case_count"],
    }
    text_construction_acceptance["passed"] = (
        text_construction_acceptance["passed"]
        and text_bytes_text["passed"]
        and corpus["valid"]["case_count"] >= 640
    )
    contracts = {
        "hir": text_hir_manifest(hir),
        "optimized_mir": text_mir_manifest(optimized),
        "abi": text_abi_manifest(),
    }
    performance = _performance(
        root / "performance", native_build
    )
    report: dict[str, Any] = {
        "experiment_version": TEXT_CORE_EXPERIMENT_VERSION,
        "status": "PENDING_FINALIZATION",
        "decision": None,
        "environment": environment,
        "hypothesis": {
            "Text": "owned valid UTF-8",
            "TextView": "borrowed zero-copy UTF-8 range",
            "bytes_text_transfer_payload_copies": 0,
            "performance_relative_raw_max": TEXT_PERFORMANCE_LIMIT,
        },
        "utf8_inspector": {
            "source": UTF8_INSPECTOR_SOURCE,
            "source_sha256": hashlib.sha256(
                UTF8_INSPECTOR_SOURCE.encode()
            ).hexdigest(),
            "runtime_input": "BytesBuilder",
            "reports_validity": True,
            "reports_scalar_count": True,
            "reports_error_offset": True,
        },
        "oracle": {
            "implementation": (
                "Python bytes.decode('utf-8', 'strict')"
            ),
            "formula": (
                "invalid:first_error_byte; "
                "valid:(1<<63)|(scalar_count<<32)|byte_length"
            ),
            "python_error_offset_differences": 0,
            "cases": [
                {
                    "family": case["family"],
                    "index": case["index"],
                    "bytes_hex": case["data"].hex(),
                    "valid": case["valid"],
                    "error_offset": case["error_offset"],
                    "scalar_count": case["scalar_count"],
                    "expected": case["expected"],
                }
                for case in representative
            ],
        },
        "frozen_hashes": frozen_hashes,
        "reproducibility": {
            "inputs": reproducible_inputs,
            "input_sha256": hashlib.sha256(
                reproducible_input_payload.encode()
            ).hexdigest(),
        },
        "contracts": contracts,
        "corpus": corpus,
        "native_build": native_build,
        "builder_boundary": builder_boundary,
        "diagnostics": diagnostics,
        "textview_acceptance": textview_acceptance,
        "text_construction_acceptance": text_construction_acceptance,
        "sanitizers": sanitizers,
        "zero_copy_roundtrip": zero_copy_roundtrip,
        "text_bytes_text_roundtrip": text_bytes_text,
        "performance": performance,
        "assembly": {
            "utf8_inspector": _assembly(
                root / "assembly",
                native_build,
                stem="utf8-inspector",
            ),
            "unicode_scalar": _assembly(
                root / "assembly",
                performance["unicode_scalar"]["builds"]["meldra"],
                stem="unicode-scalar",
            ),
        },
        "falsification_controls": _falsification_controls(
            root / "falsification",
            optimized,
            generated,
            diagnostics,
        ),
        "cache_reuse": {},
        "research_efficiency": {
            "graft_queries": 2,
            "external_research_queries": 0,
        },
        "defects": [],
        "limitations": [
            "Text is implemented only in the Stage 0.5P native subset.",
            "Text normalization, collation, grapheme segmentation, formatting, and regex are excluded.",
            "TextView slicing is byte-indexed and accepts only UTF-8 code-point boundaries.",
            "Runtime entry packing limits the decision corpus sequence length to eight bytes.",
            "Rust Vec allocation counters are implementation-defined; only time, RSS, binary, compile, and surface metrics are compared.",
        ],
        "next_experiment": (
            "Add immutable Text concatenation only if a measured "
            "workload justifies an allocation policy."
        ),
        "wall_time_seconds": time.perf_counter() - started,
    }
    cache_after = _cache_snapshot(repository)
    report["cache_reuse"] = {
        "before": cache_before,
        "after": cache_after,
        "unchanged": cache_before == cache_after,
    }
    failures = validate_text_report(report)
    safety_failure = any(
        failure
        in {
            "sanitizer failure",
            "diagnostic control failed",
            "TextView acceptance failed",
            "Text construction acceptance failed",
            "roundtrip failure",
        }
        for failure in failures
    )
    report["decision"] = {
        "gate_failures": failures,
        "supported": not failures,
    }
    report["status"] = (
        "TEXT_CORE_SUPPORTED"
        if not failures
        else "TEXT_CORE_SAFETY_DEFECT"
        if safety_failure
        else "TEXT_CORE_INCOMPLETE"
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
    "BOUNDARY_SOURCE",
    "ROUNDTRIP_SOURCE",
    "TEXT_CORE_EXPERIMENT_VERSION",
    "TEXT_INVALID_CASES",
    "TEXT_VALID_CASES",
    "UTF8_INSPECTOR_SOURCE",
    "run_text_core_experiment",
    "validate_text_report",
]
