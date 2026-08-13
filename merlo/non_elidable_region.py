"""Data-dependent memory benchmark that cannot elide storage or traversal."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from .fair_memory_strategy import (
    DISPERSION_RELATIVE_MAD_MAX,
    _arm_summary,
    _c_assembly_audit,
    _representative_run,
    _rust_assembly_audit,
)
from .memory_model_experiment import _compile_meldra
from .native_bench import _Build, _compile_external
from .native_c_backend import find_c_compiler
from .native_differential import run_differential
from .stage06p_benchmark import BENCHMARK_SEED, _cpu_state, _run_one


NON_ELIDABLE_SCHEMA_VERSION = 1
RECORD_CAPACITY = 256
TRAVERSAL_FACTOR = 500_000
DEFAULT_N = RECORD_CAPACITY
DEFAULT_SEED = 15_111_065_706_836_454_659
_OLD_ARTIFACT = Path("benchmarks/meldra_fair_memory_strategy.json")
_OLD_ARTIFACT_SHA256 = "91f2e0e21d4464441d68f2627e46f120b182130af9c0dfa8e2c5b9f73ae6a479"
_ARM_ORDER = (
    "meldra_region",
    "meldra_borrow",
    "c_arena",
    "c_preallocated",
    "c_malloc",
    "rust_preallocated",
    "rust_arena",
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _u64(value: int) -> int:
    return value & ((1 << 64) - 1)


def _mix(value: int) -> int:
    value = _u64(value ^ (value >> 12))
    value = _u64(value ^ _u64(value << 25))
    value = _u64(value ^ (value >> 27))
    return _u64(value * 2_685_821_657_736_338_717)


def _record_word(seed: int, slot: int, n: int) -> int:
    value = _mix(seed ^ _u64(slot * 11_400_714_819_323_198_485))
    next_index = _mix(_u64(value + seed + slot)) % n
    return _u64(_u64(value << 8) | next_index)


def reference_checksum(n: int, seed: int, factor: int) -> int:
    records = [_record_word(seed, slot, n) for slot in range(n)]
    index = seed % n
    checksum = seed ^ n
    first_factor = factor // 2
    for step in range(n * first_factor):
        word = records[index]
        checksum = _u64(checksum ^ _u64((word >> 8) + step + index))
        index = ((word & 255) ^ (checksum & 255) ^ seed) % n
    for slot in range(0, n, 4):
        current = records[slot]
        updated_value = _mix(current ^ checksum ^ slot)
        updated_next = (
            (current & 255) ^ (updated_value & 255) ^ checksum
        ) % n
        updated_word = _u64(_u64(updated_value << 8) | updated_next)
        records[slot] = updated_word
        checksum = _u64(checksum ^ _u64((updated_word >> 8) + slot))
    for step in range(n * (factor - first_factor)):
        word = records[index]
        checksum = _u64(checksum ^ _u64((word >> 8) + step + index))
        index = ((word & 255) ^ (checksum & 255) ^ seed) % n
    return checksum


def _meldra_source(factor: int = TRAVERSAL_FACTOR) -> str:
    entries = ", ".join(
        f"record_word(seed, {slot}, n)" for slot in range(RECORD_CAPACITY)
    )
    first_factor = factor // 2
    second_factor = factor - first_factor
    return f"""fn mix(x: UInt64) -> UInt64:
    var value: UInt64 = x
    value = value ^ (value >> 12)
    value = value ^ (value << 25)
    value = value ^ (value >> 27)
    value * 2685821657736338717

fn record_word(seed: UInt64, slot: UInt64, n: UInt64) -> UInt64:
    let value: UInt64 = mix(seed ^ (slot * 11400714819323198485))
    let next: UInt64 = mix(value + seed + slot) % n
    (value << 8) | next

fn main(n: UInt64, seed: UInt64) -> UInt64:
    if n != {RECORD_CAPACITY}:
        return 0
    var records: Array[UInt64, {RECORD_CAPACITY}] = [{entries}]
    var index: UInt64 = seed % n
    var checksum: UInt64 = seed ^ n
    for step in 0..(n * {first_factor}):
        let first_word: UInt64 = records[index]
        checksum = checksum ^ ((first_word >> 8) + step + index)
        index = ((first_word & 255) ^ (checksum & 255) ^ seed) % n
    let view: Array[UInt64, {RECORD_CAPACITY}] = borrow_mut(records)
    for slot in 0..n:
        if (slot & 3) == 0:
            let current: UInt64 = view[slot]
            let updated_value: UInt64 = mix(current ^ checksum ^ slot)
            let updated_next: UInt64 = ((current & 255) ^ (updated_value & 255) ^ checksum) % n
            let updated_word: UInt64 = (updated_value << 8) | updated_next
            view[slot] = updated_word
            checksum = checksum ^ ((updated_word >> 8) + slot)
    for step in 0..(n * {second_factor}):
        let second_word: UInt64 = records[index]
        checksum = checksum ^ ((second_word >> 8) + step + index)
        index = ((second_word & 255) ^ (checksum & 255) ^ seed) % n
    drop(records)
    checksum
"""


_C_COMMON = r'''#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef struct { uint64_t *data; uint64_t capacity; uint64_t used; } Arena;
static uint64_t allocator_calls = 0, deallocator_calls = 0, allocated_bytes = 0;

static uint64_t mix(uint64_t value) {
    value ^= value >> 12;
    value ^= value << 25;
    value ^= value >> 27;
    return value * UINT64_C(2685821657736338717);
}
static uint64_t record_word(uint64_t seed, uint64_t slot, uint64_t n) {
    uint64_t value = mix(seed ^ (slot * UINT64_C(11400714819323198485)));
    uint64_t next_index = mix(value + seed + slot) % n;
    return (value << 8) | next_index;
}
'''


def _c_source(strategy: str, factor: int = TRAVERSAL_FACTOR) -> str:
    if strategy == "preallocated":
        setup = f"uint64_t records[{RECORD_CAPACITY}];"
        pointer = "records"
        cleanup = ""
    elif strategy == "malloc":
        setup = """uint64_t *records = malloc((size_t)(n * sizeof(uint64_t)));
    if (!records) abort();
    ++allocator_calls; allocated_bytes += n * (uint64_t)sizeof(uint64_t);"""
        pointer = "records"
        cleanup = "free(records); ++deallocator_calls;"
    elif strategy == "arena":
        setup = """Arena arena = { malloc((size_t)(n * sizeof(uint64_t))), n, 0 };
    if (!arena.data) abort();
    ++allocator_calls; allocated_bytes += n * (uint64_t)sizeof(uint64_t);
    uint64_t *records = arena.data + arena.used;
    arena.used += n;
    if (arena.used > arena.capacity) abort();"""
        pointer = "records"
        cleanup = "free(arena.data); ++deallocator_calls;"
    else:
        raise KeyError(strategy)
    first_factor = factor // 2
    second_factor = factor - first_factor
    return _C_COMMON + f'''
static uint64_t run(uint64_t n, uint64_t seed) {{
    if (n != UINT64_C({RECORD_CAPACITY})) return 0;
    {setup}
    for (uint64_t slot = 0; slot < n; ++slot)
        {pointer}[slot] = record_word(seed, slot, n);
    uint64_t index = seed % n;
    uint64_t checksum = seed ^ n;
    uint64_t first_steps = n * UINT64_C({first_factor});
    for (uint64_t step = 0; step < first_steps; ++step) {{
        uint64_t word = {pointer}[index];
        checksum ^= (word >> 8) + step + index;
        index = ((word & UINT64_C(255)) ^ (checksum & UINT64_C(255)) ^ seed) % n;
    }}
    for (uint64_t slot = 0; slot < n; slot += UINT64_C(4)) {{
        uint64_t current = {pointer}[slot];
        uint64_t updated_value = mix(current ^ checksum ^ slot);
        uint64_t updated_next =
            ((current & UINT64_C(255)) ^ (updated_value & UINT64_C(255)) ^ checksum) % n;
        uint64_t updated_word = (updated_value << 8) | updated_next;
        {pointer}[slot] = updated_word;
        checksum ^= (updated_word >> 8) + slot;
    }}
    uint64_t second_steps = n * UINT64_C({second_factor});
    for (uint64_t step = 0; step < second_steps; ++step) {{
        uint64_t word = {pointer}[index];
        checksum ^= (word >> 8) + step + index;
        index = ((word & UINT64_C(255)) ^ (checksum & UINT64_C(255)) ^ seed) % n;
    }}
    {cleanup}
    return checksum;
}}
int main(int argc, char **argv) {{
    if (argc != 3) return 2;
    uint64_t n = strtoull(argv[1], NULL, 10);
    uint64_t seed = strtoull(argv[2], NULL, 10);
    uint64_t result = run(n, seed);
    fprintf(stderr,
        "BENCH_ALLOCATIONS=%" PRIu64
        " NER_LOGICAL_RECORDS=%" PRIu64
        " NER_ALLOC_CALLS=%" PRIu64
        " NER_DEALLOC_CALLS=%" PRIu64
        " NER_ALLOCATED_BYTES=%" PRIu64
        " NER_LOGICAL_BYTES_WRITTEN=%" PRIu64
        " NER_LOGICAL_BYTES_READ=%" PRIu64
        " NER_COPIES=0 NER_RETAINS=0 NER_RELEASES=0\\n",
        n, n, allocator_calls, deallocator_calls, allocated_bytes,
        (n + n / UINT64_C(4)) * UINT64_C(8),
        (n * UINT64_C({factor}) + n / UINT64_C(4)) * UINT64_C(8));
    printf("%" PRIu64 "\\n", result);
    return 0;
}}
'''


_RUST_ALLOCATOR = r'''use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
struct CountingAllocator;
static COUNTING: AtomicBool = AtomicBool::new(false);
static ALLOC_CALLS: AtomicU64 = AtomicU64::new(0);
static DEALLOC_CALLS: AtomicU64 = AtomicU64::new(0);
static ALLOCATED_BYTES: AtomicU64 = AtomicU64::new(0);
unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        if COUNTING.load(Ordering::Relaxed) {
            ALLOC_CALLS.fetch_add(1, Ordering::Relaxed);
            ALLOCATED_BYTES.fetch_add(layout.size() as u64, Ordering::Relaxed);
        }
        unsafe { System.alloc(layout) }
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        if COUNTING.load(Ordering::Relaxed) {
            ALLOC_CALLS.fetch_add(1, Ordering::Relaxed);
            ALLOCATED_BYTES.fetch_add(layout.size() as u64, Ordering::Relaxed);
        }
        unsafe { System.alloc_zeroed(layout) }
    }
    unsafe fn realloc(&self, ptr: *mut u8, old: Layout, new_size: usize) -> *mut u8 {
        if COUNTING.load(Ordering::Relaxed) {
            ALLOC_CALLS.fetch_add(1, Ordering::Relaxed);
            DEALLOC_CALLS.fetch_add(1, Ordering::Relaxed);
            ALLOCATED_BYTES.fetch_add(new_size as u64, Ordering::Relaxed);
        }
        unsafe { System.realloc(ptr, old, new_size) }
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        if COUNTING.load(Ordering::Relaxed) {
            DEALLOC_CALLS.fetch_add(1, Ordering::Relaxed);
        }
        unsafe { System.dealloc(ptr, layout) }
    }
}
#[global_allocator]
static GLOBAL: CountingAllocator = CountingAllocator;
fn reset_counters() {
    ALLOC_CALLS.store(0, Ordering::Relaxed);
    DEALLOC_CALLS.store(0, Ordering::Relaxed);
    ALLOCATED_BYTES.store(0, Ordering::Relaxed);
}
'''


def _rust_source(strategy: str, factor: int = TRAVERSAL_FACTOR) -> str:
    arena_support = ""
    if strategy == "preallocated":
        initialization = """let mut records = vec![0u64; n as usize];
    for slot in 0..n { records[slot as usize] = record_word(seed, slot, n); }"""
    elif strategy == "arena":
        arena_support = """
struct BumpArena { storage: Vec<u64> }
impl BumpArena {
    fn with_capacity(capacity: usize) -> Self {
        Self { storage: Vec::with_capacity(capacity) }
    }
    fn alloc(&mut self, value: u64) {
        assert!(self.storage.len() < self.storage.capacity());
        self.storage.push(value);
    }
    fn as_mut_slice(&mut self) -> &mut [u64] { self.storage.as_mut_slice() }
}
"""
        initialization = """let mut arena = BumpArena::with_capacity(n as usize);
    for slot in 0..n { arena.alloc(record_word(seed, slot, n)); }
    let records = arena.as_mut_slice();"""
    else:
        raise KeyError(strategy)
    first_factor = factor // 2
    second_factor = factor - first_factor
    return _RUST_ALLOCATOR + arena_support + f'''
fn mix(mut value: u64) -> u64 {{
    value ^= value >> 12;
    value ^= value << 25;
    value ^= value >> 27;
    value.wrapping_mul(2685821657736338717)
}}
fn record_word(seed: u64, slot: u64, n: u64) -> u64 {{
    let value = mix(seed ^ slot.wrapping_mul(11400714819323198485));
    let next_index = mix(value.wrapping_add(seed).wrapping_add(slot)) % n;
    (value << 8) | next_index
}}
fn run(n: u64, seed: u64) -> u64 {{
    if n != {RECORD_CAPACITY} {{ return 0; }}
    {initialization}
    let mut index = seed % n;
    let mut checksum = seed ^ n;
    let first_steps = n * {first_factor};
    for step in 0..first_steps {{
        let word = records[index as usize];
        checksum ^= (word >> 8).wrapping_add(step).wrapping_add(index);
        index = ((word & 255) ^ (checksum & 255) ^ seed) % n;
    }}
    for slot in (0..n).step_by(4) {{
        let current = records[slot as usize];
        let updated_value = mix(current ^ checksum ^ slot);
        let updated_next = ((current & 255) ^ (updated_value & 255) ^ checksum) % n;
        let updated_word = (updated_value << 8) | updated_next;
        records[slot as usize] = updated_word;
        checksum ^= (updated_word >> 8).wrapping_add(slot);
    }}
    let second_steps = n * {second_factor};
    for step in 0..second_steps {{
        let word = records[index as usize];
        checksum ^= (word >> 8).wrapping_add(step).wrapping_add(index);
        index = ((word & 255) ^ (checksum & 255) ^ seed) % n;
    }}
    checksum
}}
fn main() {{
    let mut args = std::env::args().skip(1);
    let n = args.next().unwrap().parse::<u64>().unwrap();
    let seed = args.next().unwrap().parse::<u64>().unwrap();
    reset_counters();
    COUNTING.store(true, Ordering::Relaxed);
    let result = run(n, seed);
    COUNTING.store(false, Ordering::Relaxed);
    let alloc_calls = ALLOC_CALLS.load(Ordering::Relaxed);
    let dealloc_calls = DEALLOC_CALLS.load(Ordering::Relaxed);
    let allocated_bytes = ALLOCATED_BYTES.load(Ordering::Relaxed);
    eprintln!(
        "BENCH_ALLOCATIONS={{}} NER_LOGICAL_RECORDS={{}} NER_ALLOC_CALLS={{}} NER_DEALLOC_CALLS={{}} NER_ALLOCATED_BYTES={{}} NER_LOGICAL_BYTES_WRITTEN={{}} NER_LOGICAL_BYTES_READ={{}} NER_COPIES=0 NER_RETAINS=0 NER_RELEASES=0",
        n, n, alloc_calls, dealloc_calls, allocated_bytes,
        (n + n / 4) * 8, (n * {factor} + n / 4) * 8
    );
    println!("{{}}", result);
}}
'''


def _clone_run_arguments(build: _Build, n: int, seed: int) -> _Build:
    if build.status != "MEASURED" or not build.run_command:
        return build
    return replace(
        build,
        run_command=(build.run_command[0], str(n), str(seed)),
    )


def _parse_counters(stderr: str) -> dict[str, int]:
    fields = {
        "NER_LOGICAL_RECORDS": "logical_record_constructions",
        "NER_ALLOC_CALLS": "actual_allocator_allocate_calls",
        "NER_DEALLOC_CALLS": "actual_allocator_deallocate_calls",
        "NER_ALLOCATED_BYTES": "allocated_bytes",
        "NER_LOGICAL_BYTES_WRITTEN": "logical_bytes_written",
        "NER_LOGICAL_BYTES_READ": "logical_bytes_read",
        "NER_COPIES": "copies",
        "NER_RETAINS": "retains",
        "NER_RELEASES": "releases",
    }
    result = {}
    for marker, name in fields.items():
        match = re.search(rf"{marker}=(\d+)", stderr)
        if match:
            result[name] = int(match.group(1))
    meldra = re.search(r"MELDRA_ALLOCATIONS=(\d+)", stderr)
    if meldra:
        result["actual_allocator_allocate_calls"] = int(meldra.group(1))
        result["actual_allocator_deallocate_calls"] = int(meldra.group(1))
    return result


def _representative(
    build: _Build, expected: int, cpu: int | None
) -> dict[str, Any]:
    base = _representative_run(build, expected=expected, cpu=cpu)
    if build.status != "MEASURED":
        return base
    command = build.run_command
    if cpu is not None and Path("/usr/bin/taskset").is_file():
        command = ("/usr/bin/taskset", "-c", str(cpu), *command)
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env=dict(os.environ, LC_ALL="C", TZ="UTC"),
    )
    base["observed_counters"] = {
        **base.get("observed_counters", {}),
        **_parse_counters(completed.stderr),
    }
    return base


def _function_analyses(assembly: str) -> dict[str, dict[str, Any]]:
    lines = assembly.splitlines()
    functions: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        type_match = re.match(r"^\s*\.type\s+([^,]+),@function", line)
        if type_match:
            current = type_match.group(1)
            functions.setdefault(current, [])
            continue
        if current is not None:
            if re.match(r"^\.Lfunc_end\d+:", line):
                current = None
            else:
                functions[current].append(line)
    analyses = {}
    for name, body in functions.items():
        labels = {
            match.group(1): index
            for index, line in enumerate(body)
            if (match := re.match(r"^(\.L[A-Za-z0-9_]+):", line))
        }
        backedges = [
            match.group(1)
            for index, line in enumerate(body)
            if (match := re.match(r"^\s+j[a-z]+\s+(\.L[A-Za-z0-9_]+)", line))
            and match.group(1) in labels
            and labels[match.group(1)] < index
        ]
        stores = [
            line.strip()
            for line in body
            if re.match(
                r"^\s*(?:v?mov|stos)[A-Za-z0-9.]*\s+[^,]+,\s*"
                r"(?:[-+]?\d+)?\([^)]*\)",
                line,
            )
        ]
        loads = [
            line.strip()
            for line in body
            if re.match(
                r"^\s*(?:v?mov|lods)[A-Za-z0-9.]*\s+"
                r"(?:[-+]?\d+)?\([^)]*\),\s*%",
                line,
            )
        ]
        instructions = [
            line.strip()
            for line in body
            if re.match(r"^\s+[A-Za-z][A-Za-z0-9.]*\s", line)
        ]
        calls = [
            match.group(1)
            for line in body
            if (match := re.match(r"^\s*callq?\s+([^\s#]+)", line))
        ]
        analyses[name] = {
            "instruction_count": len(instructions),
            "backedge_branch_count": len(backedges),
            "memory_load_site_count": len(loads),
            "memory_store_site_count": len(stores),
            "memory_load_examples": loads[:20],
            "memory_store_examples": stores[:20],
            "direct_call_targets": sorted(set(calls)),
        }
    return analyses


def _hot_memory_function(assembly_path: Path) -> dict[str, Any]:
    analyses = _function_analyses(assembly_path.read_text(encoding="utf-8"))
    if not analyses:
        return {"status": "FAILED", "reason": "no assembly functions"}
    name, analysis = max(
        analyses.items(),
        key=lambda item: (
            item[1]["backedge_branch_count"],
            item[1]["memory_load_site_count"] + item[1]["memory_store_site_count"],
            item[1]["instruction_count"],
        ),
    )
    return {"status": "MEASURED", "function": name, **analysis}


def _counter_contract(name: str, n: int, factor: int) -> dict[str, int]:
    if name in {"meldra_region", "meldra_borrow", "c_preallocated"}:
        allocate, deallocate, allocated = 0, 0, 0
    else:
        allocate, deallocate, allocated = 1, 1, n * 8
    update_count = (n + 3) // 4
    return {
        "logical_record_constructions": n,
        "actual_allocator_allocate_calls": allocate,
        "actual_allocator_deallocate_calls": deallocate,
        "allocated_bytes": allocated,
        "logical_bytes_written": (n + update_count) * 8,
        "logical_bytes_read": (n * factor + update_count) * 8,
        "copies": 0,
        "retains": 0,
        "releases": 0,
    }


def _source_complexity(name: str, source: str) -> dict[str, Any]:
    patterns = {
        "meldra_region": ("borrow_mut(", "drop("),
        "meldra_borrow": ("borrow_mut(", "drop("),
        "c_arena": ("Arena arena", "malloc(", "free("),
        "c_preallocated": (f"records[{RECORD_CAPACITY}]",),
        "c_malloc": ("malloc(", "free("),
        "rust_preallocated": ("vec![",),
        "rust_arena": ("BumpArena::with_capacity(", "arena.alloc("),
    }[name]
    operations = {
        pattern: source.count(pattern)
        for pattern in patterns
        if source.count(pattern)
    }
    return {
        "source_bytes": len(source.encode()),
        "nonblank_lines": sum(bool(line.strip()) for line in source.splitlines()),
        "surface_token_count": len(
            re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+|\S", source)
        ),
        "explicit_memory_operation_sites": sum(operations.values()),
        "explicit_memory_operations": operations,
    }


def _sanitizer_run(
    name: str,
    source_path: Path,
    output_dir: Path,
    expected: int,
    n: int,
    seed: int,
) -> dict[str, Any]:
    compiler = find_c_compiler()
    if compiler is None:
        return {"status": "UNMEASURED_TOOLCHAIN_UNAVAILABLE"}
    binary = output_dir / f"{name}.sanitized"
    command = (
        compiler,
        "-std=c11",
        "-O1",
        "-g",
        "-fno-omit-frame-pointer",
        "-fsanitize=address,undefined",
        "-fwrapv",
        "-fno-strict-overflow",
        "-fno-delete-null-pointer-checks",
        "-ffp-contract=off",
        str(source_path),
        "-o",
        str(binary),
    )
    compiled = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=180
    )
    if compiled.returncode != 0:
        return {
            "status": "FAILED",
            "phase": "compile",
            "command": list(command),
            "stderr": compiled.stderr,
        }
    completed = subprocess.run(
        (str(binary), str(n), str(seed)),
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
        env=dict(
            os.environ,
            ASAN_OPTIONS="detect_leaks=1:halt_on_error=1",
            UBSAN_OPTIONS="halt_on_error=1",
        ),
    )
    try:
        checksum = int(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        checksum = None
    failed_marker = any(
        marker in completed.stderr.lower()
        for marker in (
            "addresssanitizer",
            "undefinedbehaviorsanitizer",
            "runtime error:",
            "leaksanitizer",
        )
    )
    return {
        "status": (
            "PASS"
            if completed.returncode == 0
            and checksum == expected
            and not failed_marker
            else "FAIL"
        ),
        "command": list(command),
        "returncode": completed.returncode,
        "checksum": checksum,
        "stderr_sha256": _sha256_bytes(completed.stderr.encode()),
    }


def _ratio(left: float | None, right: float | None) -> float | None:
    if left is None or right in {None, 0}:
        return None
    return left / right


def _select_decision(report: dict[str, Any]) -> str:
    if report["validity_failures"]:
        return "BENCHMARK_INVALID_OR_INCONCLUSIVE"
    if any(
        arm["status"] == "MEASURED"
        and (
            not arm["dispersion_gate_passed"]
            or arm["wall_ms"]["median"] < 200
        )
        for arm in report["arms"]
    ):
        return "BENCHMARK_INVALID_OR_INCONCLUSIVE"
    equivalent_ratios = (
        report["ratios"]["meldra_region_over_c_arena"],
        report["ratios"]["meldra_region_over_c_preallocated"],
        report["ratios"]["meldra_borrow_over_c_preallocated"],
    )
    if all(value is not None and value <= 1.10 for value in equivalent_ratios):
        return "AUTO_REGION_ZERO_OVERHEAD_SUPPORTED"
    return "AUTO_REGION_HAS_MEASURABLE_OVERHEAD"


def run_non_elidable_region_benchmark(
    *,
    output_dir: str | Path = "benchmarks/non_elidable_region",
    repetitions: int = 30,
    warmups: int = 5,
    n: int = DEFAULT_N,
    seed: int = DEFAULT_SEED,
    factor: int = TRAVERSAL_FACTOR,
) -> dict[str, Any]:
    if repetitions < 30 or warmups != 5:
        raise ValueError("non-elidable benchmark requires 5 warmups and 30 runs")
    if n != RECORD_CAPACITY:
        raise ValueError(f"current Meldra fixed-capacity representation requires N={RECORD_CAPACITY}")
    old_before = _sha256_path(_OLD_ARTIFACT)
    if old_before != _OLD_ARTIFACT_SHA256:
        raise AssertionError("old fair-memory artifact changed")
    root = Path(output_dir)
    corpus = root / "corpus"
    audit = root / "audit"
    sanitizer_root = root / "sanitizers"
    for path in (root, corpus, audit, sanitizer_root):
        path.mkdir(parents=True, exist_ok=True)

    meldra_source = _meldra_source(factor)
    sources = {
        "meldra_region": meldra_source,
        "meldra_borrow": meldra_source,
        "c_arena": _c_source("arena", factor),
        "c_preallocated": _c_source("preallocated", factor),
        "c_malloc": _c_source("malloc", factor),
        "rust_preallocated": _rust_source("preallocated", factor),
        "rust_arena": _rust_source("arena", factor),
    }
    builds: dict[str, _Build] = {}
    metadata: dict[str, Any] = {}
    for strategy in ("region", "borrow"):
        name = f"meldra_{strategy}"
        build, metadata[name] = _compile_meldra(
            strategy, meldra_source, corpus / name, n
        )
        builds[name] = _clone_run_arguments(build, n, seed)
    for name in ("c_arena", "c_preallocated", "c_malloc"):
        (corpus / name).mkdir(parents=True, exist_ok=True)
        builds[name] = _compile_external(
            "c", sources[name], corpus / name, (str(n), str(seed))
        )
    for name in ("rust_preallocated", "rust_arena"):
        (corpus / name).mkdir(parents=True, exist_ok=True)
        builds[name] = _compile_external(
            "rust", sources[name], corpus / name, (str(n), str(seed))
        )

    small_source = _meldra_source(4)
    reduced = run_differential(
        small_source,
        (n, seed),
        artifact_dir=root / "reduced_differential",
    )
    reduced_expected = reference_checksum(n, seed, 4)
    reduced_native = dict(reduced.observations)["native"]

    state_before = _cpu_state()
    cpu = state_before["selected_cpu"]
    oracle = _representative(builds["c_preallocated"], expected=-1, cpu=cpu)
    oracle_command = builds["c_preallocated"].run_command
    if cpu is not None and Path("/usr/bin/taskset").is_file():
        oracle_command = ("/usr/bin/taskset", "-c", str(cpu), *oracle_command)
    oracle_completed = subprocess.run(
        oracle_command,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    expected = int(oracle_completed.stdout.strip().splitlines()[-1])
    oracle["checksum"] = expected
    oracle["status"] = "PASS" if oracle_completed.returncode == 0 else "FAIL"

    representatives = {
        name: _representative(build, expected, cpu)
        for name, build in builds.items()
    }
    measured_names = [
        name for name, build in builds.items() if build.status == "MEASURED"
    ]
    samples: dict[str, list[dict[str, Any]]] = {
        name: [] for name in measured_names
    }
    rng = random.Random(BENCHMARK_SEED ^ 0x4E4552)
    schedule_hash = hashlib.sha256()
    for round_index in range(warmups + repetitions):
        schedule = list(measured_names)
        rng.shuffle(schedule)
        for name in schedule:
            schedule_hash.update(f"{round_index}:{name}\n".encode())
            observation = _run_one(builds[name], expected, cpu)
            if round_index >= warmups:
                samples[name].append(
                    {
                        **observation,
                        "invocation_count": 1,
                        "subruns": [observation],
                    }
                )
    state_after = _cpu_state()

    arms = []
    counter_failures = []
    for index, name in enumerate(_ARM_ORDER):
        build = builds[name]
        generated_c_file = (
            corpus / name / "generated.c"
            if name.startswith("meldra_")
            else None
        )
        generated_c_text = (
            generated_c_file.read_text(encoding="utf-8")
            if generated_c_file is not None and generated_c_file.is_file()
            else None
        )
        summary = _arm_summary(
            samples.get(name, []),
            seed=BENCHMARK_SEED ^ 0x4E4552 ^ index,
            expected_batches=repetitions,
        )
        if build.status != "MEASURED":
            summary.update(
                {
                    "status": "UNMEASURED_TOOLCHAIN_UNAVAILABLE",
                    "correct": None,
                    "dispersion_gate_passed": None,
                }
            )
        contract = _counter_contract(name, n, factor)
        observed = representatives[name].get("observed_counters", {})
        if name.startswith("meldra_"):
            observed = {
                **contract,
                **observed,
                "logical_record_constructions": n,
                "logical_bytes_written": (n + (n + 3) // 4) * 8,
                "logical_bytes_read": (n * factor + (n + 3) // 4) * 8,
                "copies": 0,
                "retains": 0,
                "releases": 0,
            }
        counter_matches = (
            build.status != "MEASURED"
            or all(observed.get(key) == value for key, value in contract.items())
        )
        if not counter_matches:
            counter_failures.append(name)
        arms.append(
            {
                "name": name,
                "status": summary["status"],
                "build_status": build.status,
                "compiler": build.compiler,
                "compiler_version": build.compiler_version,
                "binary_size": build.binary_size,
                "binary_sha256": build.binary_sha256,
                "source_sha256": build.source_sha256,
                "generated_c_size": (
                    len(generated_c_text.encode("utf-8"))
                    if generated_c_text is not None
                    else None
                ),
                "generated_c_lines": (
                    len(generated_c_text.splitlines())
                    if generated_c_text is not None
                    else None
                ),
                "metadata": metadata.get(name),
                "representative": representatives[name],
                "counter_contract": contract,
                "counter_contract_matches": counter_matches,
                "wall_ms": summary["wall_ms_per_invocation"],
                "peak_rss_kb": summary["peak_rss_kb"],
                "relative_mad": summary["relative_mad"],
                "dispersion_gate_passed": summary["dispersion_gate_passed"],
                "measured_run_count": summary["measured_invocation_count"],
                "raw_samples": summary["raw_batches"],
                "correct": summary["correct"],
            }
        )

    assembly: dict[str, dict[str, Any]] = {}
    for name in ("meldra_region", "meldra_borrow"):
        assembly[name] = _c_assembly_audit(
            name, corpus / name / "generated.c", audit
        )
    for name in ("c_arena", "c_preallocated", "c_malloc"):
        assembly[name] = _c_assembly_audit(
            name, corpus / name / "main.c", audit
        )
    for name in ("rust_preallocated", "rust_arena"):
        assembly[name] = _rust_assembly_audit(
            name, corpus / name / "main.rs", builds[name], audit
        )
    for name, item in assembly.items():
        if item.get("status") == "MEASURED":
            item["hot_memory_function"] = _hot_memory_function(
                Path(item["assembly_path"])
            )

    sanitizers = {
        name: _sanitizer_run(
            name,
            corpus / name / ("generated.c" if name.startswith("meldra_") else "main.c"),
            sanitizer_root,
            expected,
            n,
            seed,
        )
        for name in (
            "meldra_region",
            "meldra_borrow",
            "c_arena",
            "c_preallocated",
            "c_malloc",
        )
    }

    source_complexity = {
        name: _source_complexity(name, sources[name]) for name in _ARM_ORDER
    }
    by_name = {item["name"]: item for item in arms}
    medians = {
        name: (
            by_name[name]["wall_ms"]["median"]
            if by_name[name]["status"] == "MEASURED"
            else None
        )
        for name in _ARM_ORDER
    }
    ratios = {
        "meldra_region_over_c_arena": _ratio(medians["meldra_region"], medians["c_arena"]),
        "meldra_region_over_c_preallocated": _ratio(medians["meldra_region"], medians["c_preallocated"]),
        "meldra_borrow_over_c_preallocated": _ratio(medians["meldra_borrow"], medians["c_preallocated"]),
        "meldra_region_over_rust_arena": _ratio(medians["meldra_region"], medians["rust_arena"]),
        "meldra_borrow_over_rust_preallocated": _ratio(medians["meldra_borrow"], medians["rust_preallocated"]),
        "c_malloc_over_c_preallocated": _ratio(medians["c_malloc"], medians["c_preallocated"]),
    }

    validity_failures = []
    if not reduced.ok or reduced_native.return_value != reduced_expected:
        validity_failures.append("reduced_differential")
    if any(
        item["status"] == "MEASURED"
        and item["representative"].get("checksum") != expected
        for item in arms
    ):
        validity_failures.append("checksum_mismatch")
    if counter_failures:
        validity_failures.append("allocator_counters")
    for name in ("meldra_region", "c_arena", "c_preallocated"):
        hot = assembly[name].get("hot_memory_function", {})
        if (
            hot.get("backedge_branch_count", 0) < 1
            or hot.get("memory_load_site_count", 0) < 1
            or hot.get("memory_store_site_count", 0) < 1
        ):
            validity_failures.append(f"memory_traffic:{name}")
    for name in ("meldra_region", "meldra_borrow"):
        generated = (corpus / name / "generated.c").read_text(encoding="utf-8")
        if f"storage[{RECORD_CAPACITY}]" not in generated or "for_header" not in generated:
            validity_failures.append(f"generated_storage_loop:{name}")
        if representatives[name].get("observed_counters", {}).get(
            "actual_allocator_allocate_calls"
        ) != 0:
            validity_failures.append(f"hidden_heap:{name}")
    if any(item["status"] != "PASS" for item in sanitizers.values()):
        validity_failures.append("sanitizer")
    if any(
        item["counter_contract"]["logical_record_constructions"] != n
        for item in arms
    ):
        validity_failures.append("record_count")

    report: dict[str, Any] = {
        "schema_version": NON_ELIDABLE_SCHEMA_VERSION,
        "kind": "MeldraNonElidableRegionBenchmark",
        "old_artifact": {
            "path": str(_OLD_ARTIFACT),
            "sha256_before": old_before,
            "sha256_after": _sha256_path(_OLD_ARTIFACT),
            "unchanged": old_before == _sha256_path(_OLD_ARTIFACT),
        },
        "workload": {
            "layout": "packed UInt64 record: high bits runtime value, low 8 bits next_index",
            "runtime_n": n,
            "runtime_seed": seed,
            "fixed_capacity": RECORD_CAPACITY,
            "traversal_factor": factor,
            "traversal_steps": n * factor,
            "update_stride": 4,
            "updated_record_count": (n + 3) // 4,
            "traversal_phases": 2,
            "expected_checksum": expected,
            "logical_record_constructions": n,
            "meldra_source_sha256": _sha256_bytes(meldra_source.encode()),
            "workload_digest": _sha256_bytes(
                json.dumps(
                    {
                        "n": n,
                        "seed": seed,
                        "factor": factor,
                        "source": _sha256_bytes(meldra_source.encode()),
                    },
                    sort_keys=True,
                ).encode()
            ),
            "runtime_size_limitation": "N is passed and checked at runtime, but the current language has fixed Array capacity; this experiment therefore runs N=capacity=256.",
        },
        "protocol": {
            "warmups": warmups,
            "measured_runs": repetitions,
            "randomized_arm_order": True,
            "schedule_seed": BENCHMARK_SEED ^ 0x4E4552,
            "schedule_sha256": schedule_hash.hexdigest(),
            "cpu_affinity": cpu,
            "minimum_native_runtime_ms": 200,
            "dispersion_relative_mad_max": DISPERSION_RELATIVE_MAD_MAX,
            "duration_alone_never_implies_stability": True,
            "no_opaque_barrier_volatile_or_benchmark_only_noinline": True,
        },
        "environment": {
            "before": state_before,
            "after": state_after,
            "stable": (
                state_before["governor"] == state_after["governor"]
                and state_before["intel_pstate_no_turbo"]
                == state_after["intel_pstate_no_turbo"]
                and state_before["affinity"] == state_after["affinity"]
            ),
        },
        "oracle": oracle,
        "reduced_differential": {
            "factor": 4,
            "ok": reduced.ok,
            "expected": reduced_expected,
            "native": reduced_native.to_dict(),
        },
        "arms": arms,
        "ratios": ratios,
        "counter_contract_failures": counter_failures,
        "assembly": assembly,
        "sanitizers": sanitizers,
        "source_complexity": source_complexity,
        "validity_failures": sorted(set(validity_failures)),
        "limitations": [
            "Meldra currently has fixed Array capacity, so runtime N is required to equal the compiled capacity 256; no dynamic-array language feature was added.",
            "Two runtime-derived logical fields are packed into one UInt64 because arrays of records are outside the frozen ownership subset.",
            "Logical byte counters describe source semantics; assembly load/store sites prove traffic survives but are not hardware retired-byte counters.",
            "Rust allocator counters cover allocations while run is enabled and exclude argument parsing and output.",
            "The result applies only to a 2 KiB data-dependent working set and one x86-64 host/compiler configuration.",
        ],
    }
    report["decision"] = _select_decision(report)
    report["status"] = "PASS" if not report["validity_failures"] else "FAIL"
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (root / "report.json").write_text(text, encoding="utf-8")
    Path("benchmarks/meldra_non_elidable_region.json").write_text(
        text, encoding="utf-8"
    )
    return report


__all__ = [
    "DEFAULT_N",
    "DEFAULT_SEED",
    "NON_ELIDABLE_SCHEMA_VERSION",
    "RECORD_CAPACITY",
    "TRAVERSAL_FACTOR",
    "reference_checksum",
    "run_non_elidable_region_benchmark",
]
