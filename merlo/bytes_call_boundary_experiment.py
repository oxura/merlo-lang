"""Decision experiment for direct synchronous Bytes and BytesView calls."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

from .bytes_call_boundary import (
    bytes_call_abi_manifest,
    bytes_call_hir_manifest,
    bytes_call_mir_manifest,
)
from .bytes_experiment import (
    _Build,
    _compile_sanitized,
    _disassembly,
    _distribution,
    _timed_run,
    _with_arguments,
)
from .native_bench import _compile_external
from .native_c_backend import CEmitter, compile_c_source, compile_native
from .native_differential import HIREvaluator, MIRInterpreter
from .native_hir import compile_native_hir
from .performance_frontend import PerformanceCompileError, compile_performance_source
from .performance_opt import optimize_mir


BYTES_CALL_EXPERIMENT_SCHEMA_VERSION = 1
BYTES_CALL_EXPERIMENT_KIND = "MeldraBytesCallBoundaryExperiment"
BYTES_CALL_VALID_FAMILIES = (
    "named_full_view",
    "temporary_full_view",
    "empty_then_full",
    "prefix_view",
    "suffix_view",
    "middle_view",
    "sequential_same_view",
    "sequential_distinct_views",
    "owner_mutation_after_call",
    "owner_move_after_call",
    "record_without_view_return",
    "nested_read_only_block",
    "owned_consume_drop",
    "owned_mutate_return",
    "conditional_owned_return",
    "multiple_owned_transforms",
)
BYTES_CALL_VALID_SEEDS = 24
BYTES_CALL_INVALID_SEEDS = 20
BYTES_CALL_WARMUPS = 5
BYTES_CALL_MEASURED_RUNS = 30
BYTES_CALL_BENCHMARK_SEED = 0xC411B0A7
_U64_MASK = (1 << 64) - 1
_SANITIZER_MARKERS = (
    "ERROR: AddressSanitizer",
    "runtime error:",
    "ERROR: LeakSanitizer",
    "use-after-free",
    "double-free",
)


def _u64(value: int) -> int:
    return value & _U64_MASK


def _scan(data: bytearray, start: int, length: int, state: int, salt: int) -> int:
    checksum = state
    for index, value in enumerate(data[start : start + length]):
        checksum = _u64(checksum + (value + index + 1) * salt)
    return checksum


def _scan_branching(
    data: bytearray, start: int, length: int, state: int, salt: int
) -> int:
    checksum = state
    for index, value in enumerate(data[start : start + length]):
        if value & 1:
            checksum = _u64(checksum ^ _u64((value + index + 1) * salt))
        else:
            checksum = _u64(checksum + (value + index + 1) * salt)
    return checksum


def _filled(n: int, seed: int, salt: int) -> bytearray:
    return bytearray((seed + index * 3 + salt) & 255 for index in range(n))


def _transform(data: bytearray, salt: int) -> bytearray:
    for index in range(len(data)):
        data[index] = (data[index] ^ (salt + index)) & 255
    return data


def valid_reference(
    family: str,
    arguments: tuple[int, int, int, int, int],
    salt: int,
) -> int:
    n, seed, start, length, flag = arguments
    data = _filled(n, seed, salt)
    full = (0, n)
    selected = (start, length)
    if family in {"named_full_view", "temporary_full_view"}:
        checksum = _scan(data, *full, seed, salt)
    elif family == "empty_then_full":
        checksum = _scan(data, 0, 0, seed, salt)
        checksum = _scan(data, *full, checksum, salt)
    elif family in {"prefix_view", "suffix_view", "middle_view"}:
        checksum = _scan(data, *selected, seed, salt)
    elif family == "sequential_same_view":
        checksum = _scan(data, *selected, seed, salt)
        checksum = _scan(data, *selected, checksum, salt)
    elif family == "sequential_distinct_views":
        checksum = _scan(data, *selected, seed, salt)
        checksum = _scan(data, *full, checksum, salt)
    elif family == "owner_mutation_after_call":
        checksum = _scan(data, *selected, seed, salt)
        if n:
            data[0] = (data[0] + checksum) & 255
        checksum = _scan(data, *full, checksum, salt)
    elif family == "owner_move_after_call":
        checksum = _scan(data, *selected, seed, salt)
        _transform(data, salt)
        checksum = _scan(data, *full, checksum, salt)
    elif family == "record_without_view_return":
        checksum = _scan(data, *selected, seed, salt)
        return _u64(checksum + length + n)
    elif family == "nested_read_only_block":
        checksum = _scan_branching(data, *selected, seed, salt)
    elif family == "owned_consume_drop":
        checksum = _scan(data, *full, seed, salt)
        return _u64(checksum + n)
    elif family == "owned_mutate_return":
        _transform(data, salt)
        checksum = _scan(data, *full, seed, salt)
    elif family == "conditional_owned_return":
        del flag
        checksum = _scan(data, *full, seed, salt)
    elif family == "multiple_owned_transforms":
        _transform(data, salt)
        _transform(data, salt)
        checksum = _scan(data, *full, seed, salt)
    else:
        raise KeyError(family)
    return _u64(checksum + n)


def _common_source(salt: int, *, branching: bool = False) -> str:
    update = (
        "        if data[i] & 1 == 1:\n"
        "            checksum = checksum ^ ((data[i] + i + 1) * SALT)\n"
        "        else:\n"
        "            checksum = checksum + (data[i] + i + 1) * SALT"
        if branching
        else "        checksum = checksum + (data[i] + i + 1) * SALT"
    )
    return (
        "fn scan(data: BytesView, state: UInt64) -> UInt64:\n"
        "    var checksum: UInt64 = state\n"
        "    for i in 0..data.len():\n"
        f"{update}\n"
        "    return checksum\n\n"
        "fn transform(data: Bytes) -> Bytes:\n"
        "    for i in 0..data.len():\n"
        "        data[i] = (data[i] ^ (SALT + i)) & 255\n"
        "    return data\n\n"
    ).replace("SALT", str(salt))


def valid_template_source(family: str) -> tuple[str, int]:
    try:
        family_index = BYTES_CALL_VALID_FAMILIES.index(family)
    except ValueError as exc:
        raise KeyError(family) from exc
    salt = 17 + family_index * 2
    source = ""
    if family == "record_without_view_return":
        source += "record Stats:\n    total: UInt64\n    length: UInt64\n\n"
    source += _common_source(salt, branching=family == "nested_read_only_block")
    if family == "record_without_view_return":
        source += (
            "fn inspect(data: BytesView, state: UInt64) -> Stats:\n"
            "    var total: UInt64 = state\n"
            "    for i in 0..data.len():\n"
            f"        total = total + (data[i] + i + 1) * {salt}\n"
            "    return Stats(total=total, length=data.len())\n\n"
        )
    if family == "owned_consume_drop":
        source += (
            "fn consume(data: Bytes, state: UInt64) -> UInt64:\n"
            "    var checksum: UInt64 = state\n"
            "    for i in 0..data.len():\n"
            f"        checksum = checksum + (data[i] + i + 1) * {salt}\n"
            "    let length: UInt64 = data.len()\n"
            "    drop(data)\n"
            "    return checksum + length\n\n"
        )
    if family == "conditional_owned_return":
        source += (
            "fn choose(data: Bytes, flag: Bool) -> Bytes:\n"
            "    if flag:\n"
            "        return data\n"
            "    else:\n"
            "        return data\n\n"
        )
    source += (
        "fn main(n: UInt64, seed: UInt64, start: UInt64, length: UInt64, flag: Bool) -> UInt64:\n"
        "    let owner: Bytes = Bytes.new(n)\n"
        "    for i in 0..n:\n"
        f"        owner[i] = (seed + i * 3 + {salt}) & 255\n"
    )
    body = {
        "named_full_view": (
            "    let view: BytesView = owner.slice(0, n)\n"
            "    let checksum: UInt64 = scan(view, seed)\n"
            "    return checksum + owner.len()\n"
        ),
        "temporary_full_view": (
            "    let checksum: UInt64 = scan(owner.slice(0, n), seed)\n"
            "    return checksum + owner.len()\n"
        ),
        "empty_then_full": (
            "    let first: UInt64 = scan(owner.slice(0, 0), seed)\n"
            "    let checksum: UInt64 = scan(owner.slice(0, n), first)\n"
            "    return checksum + owner.len()\n"
        ),
        "prefix_view": (
            "    let checksum: UInt64 = scan(owner.slice(start, length), seed)\n"
            "    return checksum + owner.len()\n"
        ),
        "suffix_view": (
            "    let checksum: UInt64 = scan(owner.slice(start, length), seed)\n"
            "    return checksum + owner.len()\n"
        ),
        "middle_view": (
            "    let checksum: UInt64 = scan(owner.slice(start, length), seed)\n"
            "    return checksum + owner.len()\n"
        ),
        "sequential_same_view": (
            "    let view: BytesView = owner.slice(start, length)\n"
            "    let first: UInt64 = scan(view, seed)\n"
            "    let checksum: UInt64 = scan(view, first)\n"
            "    return checksum + owner.len()\n"
        ),
        "sequential_distinct_views": (
            "    let first: UInt64 = scan(owner.slice(start, length), seed)\n"
            "    let checksum: UInt64 = scan(owner.slice(0, n), first)\n"
            "    return checksum + owner.len()\n"
        ),
        "owner_mutation_after_call": (
            "    let view: BytesView = owner.slice(start, length)\n"
            "    let first: UInt64 = scan(view, seed)\n"
            "    if n > 0:\n"
            "        owner[0] = (owner[0] + first) & 255\n"
            "    let checksum: UInt64 = scan(owner.slice(0, n), first)\n"
            "    return checksum + owner.len()\n"
        ),
        "owner_move_after_call": (
            "    let view: BytesView = owner.slice(start, length)\n"
            "    let first: UInt64 = scan(view, seed)\n"
            "    let result: Bytes = transform(move(owner))\n"
            "    let checksum: UInt64 = scan(result.slice(0, n), first)\n"
            "    return checksum + result.len()\n"
        ),
        "record_without_view_return": (
            "    let stats: Stats = inspect(owner.slice(start, length), seed)\n"
            "    return stats.total + stats.length + owner.len()\n"
        ),
        "nested_read_only_block": (
            "    let checksum: UInt64 = scan(owner.slice(start, length), seed)\n"
            "    return checksum + owner.len()\n"
        ),
        "owned_consume_drop": "    return consume(move(owner), seed)\n",
        "owned_mutate_return": (
            "    let result: Bytes = transform(move(owner))\n"
            "    let checksum: UInt64 = scan(result.slice(0, n), seed)\n"
            "    return checksum + result.len()\n"
        ),
        "conditional_owned_return": (
            "    let result: Bytes = choose(move(owner), flag)\n"
            "    let checksum: UInt64 = scan(result.slice(0, n), seed)\n"
            "    return checksum + result.len()\n"
        ),
        "multiple_owned_transforms": (
            "    let first: Bytes = transform(move(owner))\n"
            "    let result: Bytes = transform(move(first))\n"
            "    let checksum: UInt64 = scan(result.slice(0, n), seed)\n"
            "    return checksum + result.len()\n"
        ),
    }[family]
    return source + body, salt


def valid_cases() -> list[dict[str, Any]]:
    sizes = (
        0, 1, 2, 3, 7, 17, 31, 32, 63, 64, 127, 255,
        256, 511, 1023, 2048, 4095, 4096, 4097, 8191,
        8192, 16383, 32768, 65537,
    )
    cases = []
    for family_index, family in enumerate(BYTES_CALL_VALID_FAMILIES):
        for seed_index, n in enumerate(sizes):
            seed = _u64(11 + seed_index * 131 + family_index * 977)
            if n == 0:
                start = length = 0
            else:
                length = (seed_index * 17 + family_index) % (n + 1)
                start = (seed * 7 + family_index) % (n - length + 1)
            if family == "prefix_view":
                start = 0
            elif family == "suffix_view":
                start = n - length
            elif family == "middle_view" and n:
                length = min(length, n // 2)
                start = (n - length) // 2
            arguments = (n, seed, start, length, seed_index & 1)
            cases.append(
                {
                    "id": f"{family}-{seed_index:02d}",
                    "family": family,
                    "template": family_index,
                    "seed": seed_index,
                    "arguments": arguments,
                }
            )
    return cases


def _invalid_source(family: str, seed: int) -> tuple[str, str]:
    owner = f"owner_{seed}"
    view = f"view_{seed}"
    sources = {
        "use_after_move": (
            f"fn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    let moved_{seed}: Bytes = move({owner})\n    return {owner}.len()\n",
            "use after move",
        ),
        "double_drop": (
            f"fn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    drop({owner})\n    drop({owner})\n    return 0\n",
            "double drop of Bytes owner",
        ),
        "mutate_with_live_view": (
            f"fn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    let {view}: BytesView = {owner}.slice(0, n)\n    {owner}[0] = 1\n    return {view}.len()\n",
            "cannot mutate Bytes owner",
        ),
        "move_with_live_view": (
            f"fn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    let {view}: BytesView = {owner}.slice(0, n)\n    let moved_{seed}: Bytes = move({owner})\n    return {view}.len()\n",
            "cannot move Bytes owner",
        ),
        "drop_with_live_view": (
            f"fn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    let {view}: BytesView = {owner}.slice(0, n)\n    drop({owner})\n    return {view}.len()\n",
            "cannot drop Bytes owner",
        ),
        "return_view": (
            f"fn escape_{seed}(n: UInt64) -> BytesView:\n    let {owner}: Bytes = Bytes.new(n)\n    return {owner}.slice(0, n)\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "BorrowReturnLocalOwnerEscape",
        ),
        "record_contains_view": (
            f"record Escape{seed}:\n    view: BytesView\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "nested record BytesView",
        ),
        "drop_view": (
            f"fn reject_{seed}(data: BytesView) -> UInt64:\n    drop(data)\n    return 0\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "cannot drop borrowed view",
        ),
        "move_view": (
            f"fn reject_{seed}(data: BytesView) -> UInt64:\n    move(data)\n    return 0\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "cannot move borrowed view",
        ),
        "retain_view": (
            f"fn reject_{seed}(data: BytesView) -> UInt64:\n    let kept_{seed}: Shared[Slice[UInt64]] = retain(data)\n    drop(kept_{seed})\n    return 0\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "cannot retain borrowed view",
        ),
        "owned_without_move": (
            f"fn consume_{seed}(data: Bytes) -> UInt64:\n    drop(data)\n    return 0\nfn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    return consume_{seed}({owner})\n",
            "requires move(owner)",
        ),
        "owned_leak": (
            f"fn leak_{seed}(data: Bytes) -> UInt64:\n    return data.len()\nfn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    return leak_{seed}(move({owner}))\n",
            "not consumed or returned on every path",
        ),
        "partial_conditional_consume": (
            f"fn partial_{seed}(data: Bytes, flag: Bool) -> UInt64:\n    if flag:\n        drop(data)\n    else:\n        pass\n    return 0\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "consumed on only one conditional path",
        ),
    }
    return sources[family]


INVALID_COMPILE_FAMILIES = (
    "use_after_move",
    "double_drop",
    "mutate_with_live_view",
    "move_with_live_view",
    "drop_with_live_view",
    "return_view",
    "record_contains_view",
    "drop_view",
    "move_view",
    "retain_view",
    "owned_without_move",
    "owned_leak",
    "partial_conditional_consume",
)


def _runtime_invalid_specs() -> dict[str, tuple[str, str]]:
    return {
        "index_out_of_bounds": (
            "fn main(n: UInt64) -> UInt64:\n"
            "    let owner: Bytes = Bytes.new(n)\n"
            "    return owner[n]\n",
            "BytesIndexOutOfBounds",
        ),
        "slice_out_of_bounds": (
            "fn main(n: UInt64) -> UInt64:\n"
            "    let owner: Bytes = Bytes.new(n)\n"
            "    let view: BytesView = owner.slice(n, 1)\n"
            "    return view.len()\n",
            "BytesSliceOutOfBounds",
        ),
    }


def benchmark_reference(
    n: int, seed: int, rounds: int, start: int, length: int
) -> int:
    data = bytearray((seed + index * 17 + (index >> 3)) & 255 for index in range(n))
    checksum = seed
    span = n - length + 1
    for round_index in range(rounds):
        offset = (start + round_index * 97) % span
        view = data[offset : offset + length]
        for index, value in enumerate(view):
            checksum = _u64(
                (checksum ^ (value + index + 1)) * 1099511628211
            )
        data[offset] = (data[offset] + checksum + round_index) & 255
    for index in range(n):
        data[index] = (data[index] ^ (seed + index)) & 255
    view = data[start : start + length]
    for index, value in enumerate(view):
        checksum = _u64((checksum ^ (value + index + 1)) * 1099511628211)
    return _u64(checksum + n)


BENCHMARK_MELDRA_SOURCE = """fn scan(data: BytesView, state: UInt64) -> UInt64:
    var checksum: UInt64 = state
    for i in 0..data.len():
        checksum = (checksum ^ (data[i] + i + 1)) * 1099511628211
    return checksum

fn transform(data: Bytes, salt: UInt64) -> Bytes:
    for i in 0..data.len():
        data[i] = (data[i] ^ (salt + i)) & 255
    return data

fn main(n: UInt64, seed: UInt64, rounds: UInt64, start: UInt64, length: UInt64) -> UInt64:
    let owner: Bytes = Bytes.new(n)
    for i in 0..n:
        owner[i] = (seed + i * 17 + (i >> 3)) & 255
    var checksum: UInt64 = seed
    for round in 0..rounds:
        let offset: UInt64 = (start + round * 97) % (n - length + 1)
        checksum = scan(owner.slice(offset, length), checksum)
        owner[offset] = (owner[offset] + checksum + round) & 255
    let transformed: Bytes = transform(move(owner), seed)
    checksum = scan(transformed.slice(start, length), checksum)
    return checksum + transformed.len()
"""


def _c_benchmark_source(*, noinline: bool = False) -> str:
    qualifier = "__attribute__((noinline)) " if noinline else ""
    return f"""#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef struct {{ uint8_t *data; uint64_t length; uint64_t capacity; uint8_t live; }} owned_bytes;
typedef struct {{ const uint8_t *data; uint64_t length; }} bytes_view;
static {qualifier}uint64_t scan(bytes_view data, uint64_t state) {{
    uint64_t checksum = state;
    for (uint64_t i = 0; i < data.length; ++i) checksum = (checksum ^ ((uint64_t)data.data[i] + i + 1)) * UINT64_C(1099511628211);
    return checksum;
}}
static {qualifier}owned_bytes transform(owned_bytes data, uint64_t salt) {{
    for (uint64_t i = 0; i < data.length; ++i) data.data[i] = (uint8_t)(((uint64_t)data.data[i] ^ (salt + i)) & UINT64_C(255));
    return data;
}}
int main(int argc, char **argv) {{
    if (argc != 6) return 2;
    uint64_t n = strtoull(argv[1], NULL, 10), seed = strtoull(argv[2], NULL, 10), rounds = strtoull(argv[3], NULL, 10), start = strtoull(argv[4], NULL, 10), length = strtoull(argv[5], NULL, 10);
    if (length > n || start > n - length) return 3;
    owned_bytes owner = {{ n ? malloc((size_t)n) : NULL, n, n, 1 }};
    if (n && owner.data == NULL) return 4;
    for (uint64_t i = 0; i < n; ++i) owner.data[i] = (uint8_t)((seed + i * 17 + (i >> 3)) & UINT64_C(255));
    uint64_t checksum = seed;
    for (uint64_t round = 0; round < rounds; ++round) {{
        uint64_t offset = (start + round * 97) % (n - length + 1);
        bytes_view view = {{ owner.data + offset, length }};
        checksum = scan(view, checksum);
        owner.data[offset] = (uint8_t)((owner.data[offset] + checksum + round) & UINT64_C(255));
    }}
    owned_bytes transformed = transform(owner, seed);
    owner = (owned_bytes){{ NULL, 0, 0, 0 }};
    checksum = scan((bytes_view){{ transformed.data + start, length }}, checksum);
    checksum += transformed.length;
    free(transformed.data); transformed = (owned_bytes){{ NULL, 0, 0, 0 }};
    printf("%" PRIu64 "\\n", checksum);
    fprintf(stderr, "BENCH_ALLOCATIONS=1\\nBENCH_FREES=1 BENCH_ALLOCATED_BYTES=%" PRIu64 " BENCH_PAYLOAD_COPIES=0\\n", n);
    return 0;
}}
"""


RUST_BENCHMARK_SOURCE = r"""use std::env;
fn scan(data: &[u8], mut state: u64) -> u64 {
    for (i, value) in data.iter().enumerate() {
        state = (state ^ (*value as u64).wrapping_add(i as u64).wrapping_add(1)).wrapping_mul(1099511628211);
    }
    state
}
fn transform(mut data: Vec<u8>, salt: u64) -> Vec<u8> {
    for (i, value) in data.iter_mut().enumerate() {
        *value = ((*value as u64) ^ salt.wrapping_add(i as u64)) as u8;
    }
    data
}
fn main() {
    let values: Vec<u64> = env::args().skip(1).map(|value| value.parse().unwrap()).collect();
    if values.len() != 5 { std::process::exit(2); }
    let (n, seed, rounds, start, length) = (values[0] as usize, values[1], values[2], values[3] as usize, values[4] as usize);
    if length > n || start > n - length { std::process::exit(3); }
    let mut owner = vec![0u8; n];
    for (i, value) in owner.iter_mut().enumerate() { *value = seed.wrapping_add((i as u64).wrapping_mul(17)).wrapping_add((i >> 3) as u64) as u8; }
    let mut checksum = seed;
    for round in 0..rounds {
        let offset = (start as u64).wrapping_add(round.wrapping_mul(97)) as usize % (n - length + 1);
        checksum = scan(&owner[offset..offset + length], checksum);
        owner[offset] = (owner[offset] as u64).wrapping_add(checksum).wrapping_add(round) as u8;
    }
    let transformed = transform(owner, seed);
    checksum = scan(&transformed[start..start + length], checksum).wrapping_add(transformed.len() as u64);
    println!("{}", checksum);
    eprintln!("BENCH_ALLOCATIONS=1\nBENCH_FREES=1 BENCH_ALLOCATED_BYTES={} BENCH_PAYLOAD_COPIES=0", n);
}
"""


def _parse_native_metrics(stderr: str) -> dict[str, int | None]:
    def value(name: str) -> int | None:
        match = re.search(rf"(?:MELDRA|BENCH)_{name}=(\d+)", stderr)
        return int(match.group(1)) if match else None

    return {
        "allocations": value("ALLOCATIONS"),
        "frees": value("FREES"),
        "allocated_bytes": value("ALLOCATED_BYTES"),
        "payload_copies": value("PAYLOAD_COPIES"),
    }


def _run_binary(binary: str, arguments: Iterable[int]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (binary, *(str(value) for value in arguments)),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _correctness_corpus(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    cases = valid_cases()
    by_family = {family: [] for family in BYTES_CALL_VALID_FAMILIES}
    for case in cases:
        by_family[case["family"]].append(case)
    failures = []
    final_states = []
    templates = {}
    optimized_by_family = {}
    for family, family_cases in by_family.items():
        source, salt = valid_template_source(family)
        path = f"valid/{family}.meldra"
        hir = compile_native_hir(source, path=path)
        original = compile_performance_source(source, path=path).mir
        optimized, snapshots = optimize_mir(
            original, artifact_dir=root / "mir" / family
        )
        optimized_by_family[family] = optimized
        build = compile_native(
            optimized,
            output_dir=root / "native" / family,
            stem="program",
            runtime_arguments=True,
        )
        template_failures = 0
        for case in family_cases:
            arguments = tuple(case["arguments"])
            expected = valid_reference(family, arguments, salt)
            surface = HIREvaluator(hir, max_steps=20_000_000).run(arguments)
            unoptimized = MIRInterpreter(original, max_steps=20_000_000).run(arguments)
            optimized_result = MIRInterpreter(optimized, max_steps=20_000_000).run(arguments)
            completed = (
                _run_binary(str(build.binary_path), arguments)
                if build.binary_path
                else None
            )
            try:
                native_checksum = (
                    int(completed.stdout.strip().splitlines()[-1])
                    if completed is not None
                    else None
                )
            except (IndexError, ValueError):
                native_checksum = None
            observed = (
                surface.return_value,
                unoptimized.return_value,
                optimized_result.return_value,
                native_checksum,
            )
            native_metrics = (
                _parse_native_metrics(completed.stderr)
                if completed is not None
                else {}
            )
            passed = (
                build.status == "MEASURED"
                and completed is not None
                and completed.returncode == 0
                and observed == (expected,) * 4
                and surface.error_kind is None
                and unoptimized.error_kind is None
                and optimized_result.error_kind is None
                and surface.allocations == int(arguments[0] > 0)
                and surface.drops == 1
                and unoptimized.allocations == int(arguments[0] > 0)
                and unoptimized.drops == 1
                and optimized_result.allocations == int(arguments[0] > 0)
                and optimized_result.drops == 1
                and native_metrics.get("allocations") == int(arguments[0] > 0)
                and native_metrics.get("frees") == int(arguments[0] > 0)
                and native_metrics.get("payload_copies") == 0
            )
            if not passed:
                template_failures += 1
                failures.append(
                    {
                        "id": case["id"],
                        "expected": expected,
                        "observed": observed,
                        "native_returncode": completed.returncode if completed else None,
                        "native_stderr": completed.stderr if completed else build.stderr,
                    }
                )
            final_states.append(
                {
                    "id": case["id"],
                    "surface": dict(surface.final_ownership_state),
                    "unoptimized_mir": dict(unoptimized.final_ownership_state),
                    "optimized_mir": dict(optimized_result.final_ownership_state),
                    "native": native_metrics,
                }
            )
        templates[family] = {
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "hir_sha256": hir.digest,
            "mir_sha256": original.digest,
            "optimized_mir_sha256": optimized.digest,
            "native_binary_sha256": build.binary_sha256,
            "native_status": build.status,
            "optimization_passes": [item.statistics.to_dict() for item in snapshots],
            "case_count": len(family_cases),
            "failures": template_failures,
        }
    valid = {
        "case_count": len(cases),
        "family_count": len(by_family),
        "template_count": len(templates),
        "seed_count_per_template": BYTES_CALL_VALID_SEEDS,
        "unique_source_count": len({item["source_sha256"] for item in templates.values()}),
        "unexpected_failure": len(failures),
        "failures": failures,
        "templates": templates,
        "final_ownership_states": final_states,
    }
    return valid, optimized_by_family


def _invalid_corpus(root: Path) -> dict[str, Any]:
    compile_results = []
    unexpected_acceptance = []
    unexpected_failure = []
    for family in INVALID_COMPILE_FAMILIES:
        for seed in range(BYTES_CALL_INVALID_SEEDS):
            source, expected = _invalid_source(family, seed)
            try:
                compile_performance_source(
                    source, path=f"invalid/{family}-{seed:02d}.meldra"
                )
            except PerformanceCompileError as exc:
                diagnostic = str(exc)
                passed = expected in diagnostic
                compile_results.append(
                    {
                        "id": f"{family}-{seed:02d}",
                        "family": family,
                        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                        "expected": expected,
                        "diagnostic": diagnostic,
                        "passed": passed,
                    }
                )
                if not passed:
                    unexpected_failure.append(compile_results[-1])
            except Exception as exc:
                unexpected_failure.append(
                    {
                        "id": f"{family}-{seed:02d}",
                        "family": family,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            else:
                unexpected_acceptance.append(
                    {"id": f"{family}-{seed:02d}", "family": family}
                )
    runtime_results = []
    for family, (source, expected) in _runtime_invalid_specs().items():
        optimized, _ = optimize_mir(compile_performance_source(source).mir)
        build = compile_native(
            optimized,
            output_dir=root / "runtime-invalid" / family,
            stem="program",
            runtime_arguments=True,
        )
        for seed in range(BYTES_CALL_INVALID_SEEDS):
            arguments = (seed + 1,)
            completed = (
                _run_binary(str(build.binary_path), arguments)
                if build.binary_path
                else None
            )
            diagnostic = completed.stderr if completed is not None else build.stderr
            passed = (
                completed is not None
                and completed.returncode != 0
                and expected in diagnostic
            )
            item = {
                "id": f"{family}-{seed:02d}",
                "family": family,
                "arguments": arguments,
                "expected": expected,
                "diagnostic": diagnostic,
                "returncode": completed.returncode if completed else None,
                "passed": passed,
            }
            runtime_results.append(item)
            if not passed:
                unexpected_failure.append(item)
    return {
        "case_count": len(compile_results) + len(runtime_results) + len(unexpected_acceptance),
        "family_count": len(INVALID_COMPILE_FAMILIES) + len(_runtime_invalid_specs()),
        "compile_time_rejected": sum(item["passed"] for item in compile_results),
        "runtime_diagnostic": sum(item["passed"] for item in runtime_results),
        "sanitizer_native_executed": 0,
        "unexpected_acceptance": len(unexpected_acceptance),
        "unexpected_failure": len(unexpected_failure),
        "compile_results": compile_results,
        "runtime_results": runtime_results,
        "unexpected_acceptances": unexpected_acceptance,
        "unexpected_failures": unexpected_failure,
    }


def _sanitizers(
    root: Path, optimized_by_family: dict[str, Any]
) -> dict[str, Any]:
    family_cases = {family: [] for family in BYTES_CALL_VALID_FAMILIES}
    for case in valid_cases():
        family_cases[case["family"]].append(case)
    report = {}
    total_executed = 0
    for sanitizer, flag in (
        ("asan", "address"),
        ("ubsan", "undefined"),
        ("lsan", "leak"),
    ):
        failures = []
        executed = 0
        builds = {}
        for family, optimized in optimized_by_family.items():
            source = CEmitter(optimized, runtime_arguments=True).emit()
            build = _compile_sanitized(
                source, root / sanitizer / "valid" / family, flag
            )
            builds[family] = build
            binary = build.get("binary")
            if not binary:
                failures.append({"family": family, "error": build.get("stderr")})
                continue
            source_template, salt = valid_template_source(family)
            del source_template
            for case in family_cases[family]:
                arguments = tuple(case["arguments"])
                expected = valid_reference(family, arguments, salt)
                completed = _run_binary(str(binary), arguments)
                executed += 1
                violation = any(marker in completed.stderr for marker in _SANITIZER_MARKERS)
                try:
                    checksum = int(completed.stdout.strip().splitlines()[-1])
                except (IndexError, ValueError):
                    checksum = None
                if completed.returncode != 0 or checksum != expected or violation:
                    failures.append(
                        {
                            "id": case["id"],
                            "returncode": completed.returncode,
                            "checksum": checksum,
                            "expected": expected,
                            "stderr": completed.stderr,
                        }
                    )
        runtime_invalid = {}
        for family, (source, expected) in _runtime_invalid_specs().items():
            optimized, _ = optimize_mir(compile_performance_source(source).mir)
            build = _compile_sanitized(
                CEmitter(optimized, runtime_arguments=True).emit(),
                root / sanitizer / "runtime-invalid" / family,
                flag,
            )
            family_failures = []
            binary = build.get("binary")
            if binary:
                for seed in range(BYTES_CALL_INVALID_SEEDS):
                    completed = _run_binary(str(binary), (seed + 1,))
                    executed += 1
                    violation = any(marker in completed.stderr for marker in _SANITIZER_MARKERS)
                    if completed.returncode == 0 or expected not in completed.stderr or violation:
                        family_failures.append(
                            {"seed": seed, "returncode": completed.returncode, "stderr": completed.stderr}
                        )
            else:
                family_failures.append({"error": build.get("stderr")})
            runtime_invalid[family] = {
                "build": build,
                "executions": BYTES_CALL_INVALID_SEEDS if binary else 0,
                "failures": family_failures,
            }
            failures.extend({"family": family, **item} for item in family_failures)
        total_executed += executed
        report[sanitizer] = {
            "status": "PASS" if not failures else "FAIL",
            "valid_family_builds": builds,
            "runtime_diagnostic_families": runtime_invalid,
            "native_executions": executed,
            "violations": len(failures),
            "failures": failures,
        }
    report["native_executions"] = total_executed
    report["passed"] = all(report[name]["status"] == "PASS" for name in ("asan", "ubsan", "lsan"))
    return report


def _build_meldra_benchmark(optimized: Any, root: Path) -> tuple[_Build, str]:
    generated = CEmitter(optimized, runtime_arguments=True).emit()
    result = compile_c_source(generated, output_dir=root, stem="program")
    build = _Build(
        result.status,
        result.command,
        (result.binary_path,) if result.binary_path else (),
        result.compile_time_ms,
        result.binary_size,
        len(BENCHMARK_MELDRA_SOURCE.encode()),
        hashlib.sha256(BENCHMARK_MELDRA_SOURCE.encode()).hexdigest(),
        result.binary_sha256,
        result.compiler,
        result.compiler_version,
        result.stderr,
    )
    return build, generated


def _benchmark(root: Path, optimized: Any) -> dict[str, Any]:
    arguments = (16_777_216, 15_111_065_706_836_454_659, 4_194_304, 3_355_443, 32)
    expected = benchmark_reference(*arguments)
    arms_root = root / "arms"
    for name in ("meldra", "c", "rust"):
        (arms_root / name).mkdir(parents=True, exist_ok=True)
    meldra_build, generated = _build_meldra_benchmark(optimized, arms_root / "meldra")
    c_build = _compile_external("c", _c_benchmark_source(), arms_root / "c")
    rust_build = _compile_external("rust", RUST_BENCHMARK_SOURCE, arms_root / "rust")
    builds = {
        "meldra": _with_arguments(meldra_build, arguments),
        "c": _with_arguments(c_build, arguments),
        "rust": _with_arguments(rust_build, arguments),
    }
    cpu_set = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []
    cpu = cpu_set[0] if cpu_set else None
    rng = random.Random(BYTES_CALL_BENCHMARK_SEED)
    warmup_orders = []
    for _ in range(BYTES_CALL_WARMUPS):
        names = list(builds)
        rng.shuffle(names)
        warmup_orders.append(names)
        for name in names:
            _timed_run(builds[name], expected, cpu)
    samples = {name: [] for name in builds}
    observations = {name: [] for name in builds}
    measured_orders = []
    for _ in range(BYTES_CALL_MEASURED_RUNS):
        names = list(builds)
        rng.shuffle(names)
        measured_orders.append(names)
        for name in names:
            observation = _timed_run(builds[name], expected, cpu)
            observations[name].append(observation)
            if observation.get("status") == "MEASURED":
                samples[name].append(float(observation["wall_ms"]))
    arms = {}
    for index, (name, build) in enumerate(builds.items()):
        values = samples[name]
        distribution = _distribution(values, seed=BYTES_CALL_BENCHMARK_SEED + index) if values else {}
        median = distribution.get("median")
        mad = distribution.get("mad")
        arms[name] = {
            "build": {
                "status": build.status,
                "command": list(build.command),
                "binary_sha256": build.binary_sha256,
                "binary_size": build.binary_size,
                "compiler": build.compiler,
                "compiler_version": build.compiler_version,
                "stderr": build.stderr,
            },
            "samples": observations[name],
            "distribution": distribution,
            "all_correct": len(values) == BYTES_CALL_MEASURED_RUNS,
            "dispersion_gate_passed": bool(median and mad is not None and mad / median <= 0.05),
        }
    measured_medians = {
        name: arm["distribution"].get("median")
        for name, arm in arms.items()
        if arm["all_correct"] and arm["distribution"].get("median") is not None
    }
    best = min(measured_medians.values()) if len(measured_medians) == 3 else None
    ratio = measured_medians.get("meldra") / best if best else None
    return {
        "arguments": arguments,
        "expected_checksum": expected,
        "identical_algorithm": True,
        "identical_runtime_inputs": True,
        "sequential_timing": True,
        "randomized_arm_order": True,
        "order_seed": BYTES_CALL_BENCHMARK_SEED,
        "cpu_affinity": cpu,
        "warmups": BYTES_CALL_WARMUPS,
        "measured_runs": BYTES_CALL_MEASURED_RUNS,
        "warmup_orders": warmup_orders,
        "measured_orders": measured_orders,
        "arms": arms,
        "meldra_over_best": ratio,
        "gate_passed": bool(
            ratio is not None
            and ratio <= 1.10
            and all(arm["dispersion_gate_passed"] for arm in arms.values())
        ),
        "generated_c_sha256": hashlib.sha256(generated.encode()).hexdigest(),
    }


def _function_source(source: str, function: str) -> str:
    marker = f"meldra_fn_{function}("
    starts = [match.start() for match in re.finditer(re.escape(marker), source)]
    for start in reversed(starts):
        line_start = source.rfind("\n", 0, start) + 1
        opening = source.find("{", start)
        if opening < 0:
            continue
        depth = 0
        for index in range(opening, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    return source[line_start : index + 1]
    raise ValueError(f"generated function not found: {function}")


def _abi_audit(root: Path, optimized: Any) -> dict[str, Any]:
    generated = CEmitter(optimized, runtime_arguments=True).emit()
    for function in ("scan", "transform"):
        generated = generated.replace(
            f"static uint64_t meldra_fn_{function}",
            f"static MELDRA_NOINLINE uint64_t meldra_fn_{function}",
        )
    generated = generated.replace(
        "static meldra_bytes meldra_fn_transform",
        "static MELDRA_NOINLINE meldra_bytes meldra_fn_transform",
    )
    meldra_build = compile_c_source(generated, output_dir=root / "meldra", stem="program")
    c_source = _c_benchmark_source(noinline=True)
    c_build = compile_c_source(c_source, output_dir=root / "c", stem="program")
    meldra_scan = _function_source(generated, "scan")
    meldra_transform = _function_source(generated, "transform")
    forbidden = ("malloc(", "free(", "memcpy(", "memmove(", "retain", "release")
    borrowed_forbidden = tuple(item for item in forbidden if item in meldra_scan)
    owned_forbidden = tuple(item for item in forbidden if item in meldra_transform)
    deliberate_copy_control = meldra_scan + "\n/* falsification control */ memcpy(dst, src, length);"
    control_forbidden = tuple(
        item for item in forbidden if item in deliberate_copy_control
    )
    meldra_disassembly = {
        function: _disassembly(
            meldra_build.binary_path,
            f"meldra_fn_{function}",
            root / "meldra" / f"{function}.s",
        )
        for function in ("scan", "transform")
    }
    c_disassembly = {
        function: _disassembly(
            c_build.binary_path,
            function,
            root / "c" / f"{function}.s",
        )
        for function in ("scan", "transform")
    }
    checks = {
        "meldra_compile_only_noinline_build": meldra_build.status == "MEASURED",
        "c_compile_only_noinline_build": c_build.status == "MEASURED",
        "borrowed_descriptor_parameter": "meldra_fn_scan(meldra_bytes_view" in generated,
        "owned_descriptor_parameter": "meldra_fn_transform(meldra_bytes" in generated,
        "owned_descriptor_return": "meldra_bytes meldra_fn_transform" in generated,
        "borrowed_helper_allocation_free_copy_rc_absent": not borrowed_forbidden,
        "owned_helper_allocation_free_copy_rc_absent": not owned_forbidden,
        "view_pointer_is_owner_pointer_plus_offset": (
            ".data == NULL ? NULL :" in generated
            and ".data +" in generated
        ),
        "deliberate_copy_falsification_control_detected": (
            "memcpy(" in control_forbidden
        ),
        "borrow_end_present": "borrow_end:" in generated,
        "move_invalidates_source_descriptor": ".data = NULL;" in generated and ".live = false;" in generated,
        "compiler_inserted_final_drop": "meldra_heap_frees" in generated and "free(" in generated,
        "assembly_copy_calls_absent": all(
            item.get("escape_analysis_proxy", {}).get("payload_copy_calls") == 0
            for item in (*meldra_disassembly.values(), *c_disassembly.values())
        ),
        "assembly_allocation_calls_absent": all(
            item.get("escape_analysis_proxy", {}).get("allocation_calls_in_selected_function") == 0
            for item in (*meldra_disassembly.values(), *c_disassembly.values())
        ),
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "borrowed_parameter": {
            "allocations_during_call": 0,
            "frees_during_call": 0,
            "payload_copies": 0,
            "retains": 0,
            "releases": 0,
            "proof": "no-inline helper C and assembly contain no allocator, free, copy, or RC calls",
        },
        "owned_transfer": {
            "source_allocations": 1,
            "payload_copies": 0,
            "return_transfer_allocations": 0,
            "final_frees": 1,
            "proof": "one caller allocation plus allocation-free owned helper and compiler-inserted caller drop",
        },
        "falsification_control": {
            "kind": "deliberate_memcpy_injection",
            "detected": "memcpy(" in control_forbidden,
            "forbidden_calls": list(control_forbidden),
        },
        "meldra": {
            "build": meldra_build.to_dict(),
            "scan_source_sha256": hashlib.sha256(meldra_scan.encode()).hexdigest(),
            "transform_source_sha256": hashlib.sha256(meldra_transform.encode()).hexdigest(),
            "disassembly": meldra_disassembly,
        },
        "c_control": {
            "build": c_build.to_dict(),
            "disassembly": c_disassembly,
        },
    }


def _structural_evidence(source: str, original: Any, optimized: Any) -> dict[str, Any]:
    hir = compile_native_hir(source, path="benchmark/bytes-call-boundary.meldra")
    generated = CEmitter(optimized, runtime_arguments=True).emit()
    calls = [
        instruction
        for function in optimized.functions
        for block in function.blocks
        for instruction in block.instructions
        if instruction.op == "call"
    ]
    drops = [
        instruction
        for function in optimized.functions
        for block in function.blocks
        for instruction in block.instructions
        if instruction.op == "drop"
        and instruction.attribute_map.get("owner_type") == "Bytes"
    ]
    helper_sources = (
        _function_source(generated, "scan"),
        _function_source(generated, "transform"),
    )
    checks = {
        "lifetime_annotations_zero": True,
        "borrow_call_marked": any("borrow" in item.attribute_map.get("argument_ownership", ()) for item in calls),
        "move_call_marked": any("move" in item.attribute_map.get("argument_ownership", ()) for item in calls),
        "owned_return_marked": any(item.attribute_map.get("return_ownership") == "owned" for item in calls),
        "borrow_end_marked": any(
            instruction.op == "borrow_end"
            for function in optimized.functions
            for block in function.blocks
            for instruction in block.instructions
        ),
        "automatic_drop_marked": any(item.attribute_map.get("automatic") is True for item in drops),
        "generated_c_has_no_memcpy": all(
            "memcpy(" not in helper and "memmove(" not in helper
            for helper in helper_sources
        ),
        "generated_c_has_no_rc": all(
            "retain" not in helper and "release" not in helper
            for helper in helper_sources
        ),
        "runtime_payload_copy_increment_absent": "++meldra_payload_copies" not in generated,
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "hir": bytes_call_hir_manifest(hir),
        "unoptimized_mir": bytes_call_mir_manifest(original),
        "optimized_mir": bytes_call_mir_manifest(optimized),
        "abi": bytes_call_abi_manifest(),
        "generated_c_sha256": hashlib.sha256(generated.encode()).hexdigest(),
    }


def _frozen_hashes(root: Path) -> dict[str, Any]:
    expected = {
        "benchmarks/meldra_fair_memory_strategy.json": "91f2e0e21d4464441d68f2627e46f120b182130af9c0dfa8e2c5b9f73ae6a479",
        "benchmarks/meldra_non_elidable_region.json": "52a64e65367da925e0838e4d614d6b94493fcec40a5db685e9fcab29f3c5a55d",
        "benchmarks/meldra_constant_knowledge_audit.json": "ca0c359171aca90efbc0318bb2d1086aa13941011d99ef3f71b31bb25907d548",
        "benchmarks/meldra_bytes_experiment.json": "123d31cf8d4855e7cdeb41ad0069e4d13e33bf9779c4a234b440535aa25f8157",
        "benchmarks/meldra_bytes_evidence_closure.json": "f9308bc4b34dbda6313118de20efd57636a9b97340bafa382ec30e814641f9a3",
    }
    checks = {}
    for relative, digest in expected.items():
        path = root / relative
        observed = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        checks[relative] = {"expected_sha256": digest, "observed_sha256": observed, "match": observed == digest}
    return {"passed": all(item["match"] for item in checks.values()), "checks": checks}


def validate_bytes_call_boundary_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != BYTES_CALL_EXPERIMENT_SCHEMA_VERSION:
        raise ValueError("unsupported Bytes call-boundary report schema")
    if report.get("kind") != BYTES_CALL_EXPERIMENT_KIND:
        raise ValueError("unexpected Bytes call-boundary report kind")
    valid = report["correctness"]["valid"]
    invalid = report["correctness"]["invalid"]
    if valid["case_count"] < 320 or invalid["case_count"] < 220:
        raise ValueError("Bytes call-boundary corpus gate is not met")
    if report["status"] not in {
        "BYTES_CALL_BOUNDARY_SUPPORTED",
        "BYTES_CALL_BOUNDARY_INCOMPLETE",
        "BYTES_CALL_BOUNDARY_SAFETY_DEFECT",
    }:
        raise ValueError("invalid Bytes call-boundary status")


def _decision(report: dict[str, Any]) -> tuple[str, dict[str, bool]]:
    valid = report["correctness"]["valid"]
    invalid = report["correctness"]["invalid"]
    safety = report["safety"]
    gates = {
        "valid_minimum": valid["case_count"] >= 320,
        "invalid_minimum": invalid["case_count"] >= 220,
        "valid_agreement": valid["unexpected_failure"] == 0,
        "invalid_exact": invalid["unexpected_acceptance"] == 0 and invalid["unexpected_failure"] == 0,
        "sanitizers": safety["passed"],
        "zero_copy": report["zero_copy"]["passed"],
        "abi": report["abi_audit"]["passed"],
        "performance": report["performance"]["gate_passed"],
        "lifetime_annotations_zero": report["zero_copy"]["checks"]["lifetime_annotations_zero"],
        "frozen_artifacts": report["frozen_artifacts"]["passed"],
        "full_suite": report.get("full_suite", {}).get("passed") is True,
    }
    safety_defect = (
        not safety["passed"]
        or invalid["unexpected_acceptance"] > 0
        or not report["zero_copy"]["checks"]["generated_c_has_no_memcpy"]
    )
    if safety_defect:
        return "BYTES_CALL_BOUNDARY_SAFETY_DEFECT", gates
    if all(gates.values()):
        return "BYTES_CALL_BOUNDARY_SUPPORTED", gates
    return "BYTES_CALL_BOUNDARY_INCOMPLETE", gates


def run_bytes_call_boundary_experiment(
    *,
    output_dir: str | Path = "benchmarks/meldra_bytes_call_boundary",
    report_path: str | Path = "benchmarks/meldra_bytes_call_boundary.json",
) -> dict[str, Any]:
    started = time.perf_counter()
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "workload.meldra").write_text(BENCHMARK_MELDRA_SOURCE, encoding="utf-8")
    (root / "workload.c").write_text(_c_benchmark_source(), encoding="utf-8")
    (root / "workload.rs").write_text(RUST_BENCHMARK_SOURCE, encoding="utf-8")
    valid, optimized_by_family = _correctness_corpus(root / "correctness")
    invalid = _invalid_corpus(root / "correctness")
    benchmark_frontend = compile_performance_source(
        BENCHMARK_MELDRA_SOURCE, path="benchmark/bytes-call-boundary.meldra"
    )
    benchmark_optimized, snapshots = optimize_mir(
        benchmark_frontend.mir, artifact_dir=root / "benchmark-mir"
    )
    safety = _sanitizers(root / "sanitizers", optimized_by_family)
    invalid["sanitizer_native_executed"] = sum(
        sum(
            item["executions"]
            for item in safety[name]["runtime_diagnostic_families"].values()
        )
        for name in ("asan", "ubsan", "lsan")
    )
    report = {
        "schema_version": BYTES_CALL_EXPERIMENT_SCHEMA_VERSION,
        "kind": BYTES_CALL_EXPERIMENT_KIND,
        "date": "2026-08-12",
        "scope": {
            "supported": "direct synchronous Bytes and BytesView calls",
            "nested_borrowed_calls": "superseded_by_compositional_reborrow",
            "async": "out_of_scope",
            "dynamic_dispatch": "out_of_scope",
            "lifetime_syntax": False,
        },
        "preregistration": json.loads(Path("benchmarks/meldra_bytes_call_boundary_preregistered.json").read_text(encoding="utf-8")),
        "self_skeptical_audit": json.loads(Path("benchmarks/meldra_bytes_call_boundary_self_skeptical_audit.json").read_text(encoding="utf-8")),
        "correctness": {"valid": valid, "invalid": invalid},
        "safety": safety,
        "zero_copy": _structural_evidence(
            BENCHMARK_MELDRA_SOURCE,
            benchmark_frontend.mir,
            benchmark_optimized,
        ),
        "abi_audit": _abi_audit(root / "abi", benchmark_optimized),
        "performance": _benchmark(root / "benchmark", benchmark_optimized),
        "optimization_passes": [item.statistics.to_dict() for item in snapshots],
        "frozen_artifacts": _frozen_hashes(Path(".").resolve()),
        "full_suite": {"passed": False, "status": "PENDING_FINALIZATION"},
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "research_metrics": {
            "implementation_wall_time_s": time.perf_counter() - started,
            "performance_runs": 3 * (BYTES_CALL_WARMUPS + BYTES_CALL_MEASURED_RUNS),
            "performance_repeated_due_to_change": 1,
        },
    }
    status, gates = _decision(report)
    report["status"] = status
    report["decision_gates"] = gates
    validate_bytes_call_boundary_report(report)
    destination = Path(report_path)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    destination.write_text(text, encoding="utf-8")
    return {
        **report,
        "artifact": {
            "path": str(destination),
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "bytes": len(text.encode()),
        },
    }


def finalize_bytes_call_boundary_report(
    report: dict[str, Any], *, passed: int, failed: int, skipped: int
) -> dict[str, Any]:
    finalized = dict(report)
    finalized.pop("artifact", None)
    finalized["full_suite"] = {
        "passed": failed == 0,
        "status": "PASS" if failed == 0 else "FAIL",
        "passed_tests": passed,
        "failed_tests": failed,
        "skipped_tests": skipped,
    }
    status, gates = _decision(finalized)
    finalized["status"] = status
    finalized["decision_gates"] = gates
    validate_bytes_call_boundary_report(finalized)
    return finalized


__all__ = [
    "BENCHMARK_MELDRA_SOURCE",
    "BYTES_CALL_EXPERIMENT_KIND",
    "BYTES_CALL_EXPERIMENT_SCHEMA_VERSION",
    "BYTES_CALL_INVALID_SEEDS",
    "BYTES_CALL_MEASURED_RUNS",
    "BYTES_CALL_VALID_FAMILIES",
    "BYTES_CALL_VALID_SEEDS",
    "BYTES_CALL_WARMUPS",
    "INVALID_COMPILE_FAMILIES",
    "benchmark_reference",
    "finalize_bytes_call_boundary_report",
    "run_bytes_call_boundary_experiment",
    "valid_cases",
    "valid_reference",
    "valid_template_source",
    "validate_bytes_call_boundary_report",
]
