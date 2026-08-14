"""Fair memory-strategy comparison for the frozen Stage 0.6P workload."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import statistics
import subprocess
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from research.archive.alpha1.merlo.codegen_evidence import _assembly_metrics, _compile_assembly
from research.archive.alpha1.merlo.memory_model_experiment import _compile_meldra
from research.archive.alpha1.merlo.native_bench import (
    WORKLOADS,
    _Build,
    _compile_external,
    _meldra_source,
    competitor_source,
    reference_checksum,
)
from .native_c_backend import find_c_compiler
from research.archive.alpha1.merlo.stage06p_benchmark import BENCHMARK_SEED, _cpu_state, _distribution, _run_one


FAIR_MEMORY_SCHEMA_VERSION = 1
DISPERSION_RELATIVE_MAD_MAX = 0.05
DEFAULT_BATCH_SIZE = 100
_ARM_ORDER = (
    "meldra_region",
    "meldra_borrow",
    "c_malloc",
    "c_arena",
    "c_preallocated",
    "rust_arena",
    "rust_preallocated",
)

_C_PREAMBLE = r'''#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#if defined(__GNUC__) || defined(__clang__)
#define NOINLINE __attribute__((noinline))
#else
#define NOINLINE
#endif
'''

_C_MALLOC_AUDIT_SOURCE = _C_PREAMBLE + r'''
static uint64_t alloc_calls = 0, dealloc_calls = 0, allocated_bytes = 0;
static NOINLINE void *counted_malloc(size_t size) {
    ++alloc_calls; allocated_bytes += (uint64_t)size;
    return malloc(size);
}
static NOINLINE void counted_free(void *pointer) {
    ++dealloc_calls; free(pointer);
}
static NOINLINE uint64_t *make_values(uint64_t i) {
    uint64_t *values = counted_malloc(sizeof(uint64_t) * 8);
    if (!values) abort();
    for (uint64_t j = 0; j < 8; ++j) values[j] = i + j;
    return values;
}
static uint64_t run(uint64_t n) {
    uint64_t checksum = 0;
    for (uint64_t i = 0; i < n; ++i) {
        uint64_t *values = make_values(i);
        checksum += values[0] + values[7];
        counted_free(values);
    }
    return checksum;
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t n = strtoull(argv[1], NULL, 10);
    uint64_t result = run(n);
    fprintf(stderr,
        "BENCH_ALLOCATIONS=%" PRIu64
        " FAIR_LOGICAL_ALLOCATIONS=%" PRIu64
        " FAIR_ALLOC_CALLS=%" PRIu64
        " FAIR_DEALLOC_CALLS=%" PRIu64
        " FAIR_ALLOCATED_BYTES=%" PRIu64
        " FAIR_COPIES=0 FAIR_WRITES=%" PRIu64 "\n",
        n, n, alloc_calls, dealloc_calls, allocated_bytes, n * UINT64_C(8));
    printf("%" PRIu64 "\n", result);
    return 0;
}
'''


def _c_arena_source() -> str:
    return _C_PREAMBLE + r'''
typedef struct { uint64_t *data; uint64_t capacity; uint64_t used; } Arena;
static uint64_t alloc_calls = 0, dealloc_calls = 0, allocated_bytes = 0;
static NOINLINE Arena arena_create(void) {
    uint64_t capacity = UINT64_C(8);
    uint64_t bytes = capacity * (uint64_t)sizeof(uint64_t);
    uint64_t *data = malloc((size_t)bytes);
    if (!data) abort();
    ++alloc_calls; allocated_bytes += bytes;
    Arena arena = { data, capacity, 0 };
    return arena;
}
static inline void arena_reset(Arena *arena) { arena->used = 0; }
static inline uint64_t *arena_allocate(Arena *arena) {
    if (arena->used + UINT64_C(8) > arena->capacity) abort();
    uint64_t *result = arena->data + arena->used;
    arena->used += UINT64_C(8);
    return result;
}
static NOINLINE void arena_destroy(Arena *arena) {
    free(arena->data); ++dealloc_calls;
    arena->data = NULL; arena->capacity = 0; arena->used = 0;
}
static uint64_t run(uint64_t n) {
    Arena arena = arena_create();
    uint64_t checksum = 0;
    for (uint64_t i = 0; i < n; ++i) {
        arena_reset(&arena);
        uint64_t *values = arena_allocate(&arena);
        for (uint64_t j = 0; j < 8; ++j) values[j] = i + j;
        checksum += values[0] + values[7];
    }
    arena_destroy(&arena);
    return checksum;
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t n = strtoull(argv[1], NULL, 10);
    uint64_t result = run(n);
    fprintf(stderr,
        "BENCH_ALLOCATIONS=%" PRIu64
        " FAIR_LOGICAL_ALLOCATIONS=%" PRIu64
        " FAIR_ALLOC_CALLS=%" PRIu64
        " FAIR_DEALLOC_CALLS=%" PRIu64
        " FAIR_ALLOCATED_BYTES=%" PRIu64
        " FAIR_COPIES=0 FAIR_WRITES=%" PRIu64 "\n",
        n, n, alloc_calls, dealloc_calls, allocated_bytes, n * UINT64_C(8));
    printf("%" PRIu64 "\n", result);
    return 0;
}
'''


def _c_preallocated_source() -> str:
    return _C_PREAMBLE + r'''
static uint64_t run(uint64_t n) {
    uint64_t values[8] = {0,0,0,0,0,0,0,0};
    uint64_t checksum = 0;
    for (uint64_t i = 0; i < n; ++i) {
        for (uint64_t j = 0; j < 8; ++j) values[j] = i + j;
        checksum += values[0] + values[7];
    }
    return checksum;
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t n = strtoull(argv[1], NULL, 10);
    uint64_t result = run(n);
    fprintf(stderr,
        "BENCH_ALLOCATIONS=%" PRIu64
        " FAIR_LOGICAL_ALLOCATIONS=%" PRIu64
        " FAIR_ALLOC_CALLS=0 FAIR_DEALLOC_CALLS=0"
        " FAIR_ALLOCATED_BYTES=0 FAIR_COPIES=0 FAIR_WRITES=%" PRIu64 "\n",
        n, n, n * UINT64_C(8));
    printf("%" PRIu64 "\n", result);
    return 0;
}
'''

_RUST_PREAMBLE = r'''use std::alloc::{GlobalAlloc, Layout, System};
use std::hint::black_box;
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


def _rust_source(strategy: str) -> str:
    if strategy == "arena":
        body = r'''
fn run(n: u64) -> u64 {
    let mut arena: Vec<[u64; 8]> = Vec::with_capacity(1);
    let mut checksum = 0u64;
    for i in 0..n {
        arena.clear();
        arena.push([i, i+1, i+2, i+3, i+4, i+5, i+6, i+7]);
        let values = &arena[0];
        checksum = checksum.wrapping_add(values[0]).wrapping_add(values[7]);
    }
    black_box(arena.as_ptr());
    checksum
}
'''
    elif strategy == "preallocated":
        body = r'''
fn run(n: u64) -> u64 {
    let mut values = [0u64; 8];
    let mut checksum = 0u64;
    for i in 0..n {
        for j in 0..8 { values[j] = i + j as u64; }
        checksum = checksum.wrapping_add(values[0]).wrapping_add(values[7]);
    }
    black_box(values.as_ptr());
    checksum
}
'''
    else:
        raise KeyError(strategy)
    return _RUST_PREAMBLE + body + r'''
fn main() {
    let n = std::env::args().nth(1).unwrap().parse::<u64>().unwrap();
    reset_counters();
    COUNTING.store(true, Ordering::Relaxed);
    let result = run(n);
    COUNTING.store(false, Ordering::Relaxed);
    let alloc_calls = ALLOC_CALLS.load(Ordering::Relaxed);
    let dealloc_calls = DEALLOC_CALLS.load(Ordering::Relaxed);
    let allocated_bytes = ALLOCATED_BYTES.load(Ordering::Relaxed);
    eprintln!(
        "BENCH_ALLOCATIONS={} FAIR_LOGICAL_ALLOCATIONS={} FAIR_ALLOC_CALLS={} FAIR_DEALLOC_CALLS={} FAIR_ALLOCATED_BYTES={} FAIR_COPIES=0 FAIR_WRITES={}",
        n, n, alloc_calls, dealloc_calls, allocated_bytes, n * 8
    );
    println!("{}", result);
}
'''


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_digest(value: Any) -> str:
    return _sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )


def _workload() -> Any:
    return next(item for item in WORKLOADS if item.id == "shared_allocations")


def _freeze_manifest(root: Path) -> dict[str, Any]:
    workload = _workload()
    source = _meldra_source(workload)
    prior_path = Path("tools/benchmarks/merlo/benchmarks/meldra_stage06p_memory.json")
    prior = json.loads(prior_path.read_text(encoding="utf-8"))
    by_name = {item["name"]: item for item in prior["arms"]}
    frozen_generated = {
        strategy: Path(
            f"tools/benchmarks/merlo/benchmarks/stage06p_memory/corpus/meldra_{strategy}/generated.c"
        )
        for strategy in ("borrow", "region")
    }
    workload_contract = {
        "id": workload.id,
        "algorithm": workload.algorithm,
        "runtime_input": workload.input,
        "logical_object_count": workload.input,
        "elements_per_object": 8,
        "expected_checksum": reference_checksum(workload),
        "meldra_source_sha256": _sha256_bytes(source.encode()),
        "c_malloc_source_sha256": _sha256_bytes(
            competitor_source("c", workload).encode()
        ),
    }
    return {
        "workload_contract": workload_contract,
        "workload_contract_sha256": _canonical_digest(workload_contract),
        "prior_report": str(prior_path),
        "prior_report_sha256": _sha256_path(prior_path),
        "prior_results": {
            name: {
                "median_ms": by_name[name]["wall_ms"]["median"],
                "runtime_over_c_manual": by_name[name][
                    "runtime_over_c_manual"
                ],
                "algorithm_allocations": by_name[name][
                    "algorithm_allocations"
                ],
            }
            for name in ("meldra_region", "meldra_borrow", "c_manual")
        },
        "frozen_implementations": {
            strategy: {
                "meldra_source_sha256": _sha256_bytes(source.encode()),
                "generated_c_path": str(path),
                "generated_c_sha256": _sha256_path(path),
            }
            for strategy, path in frozen_generated.items()
        },
    }


def _counter_contract(name: str, n: int) -> dict[str, Any]:
    contracts = {
        "meldra_region": (0, 0, 0, 64, 0),
        "meldra_borrow": (0, 0, 0, 64, 0),
        "c_malloc": (n, n, n * 64, 64, 0),
        "c_arena": (1, 1, 64, 64, 0),
        "c_preallocated": (0, 0, 0, 64, 0),
        "rust_arena": (1, 1, 64, 64, 0),
        "rust_preallocated": (0, 0, 0, 64, 0),
    }
    allocate, deallocate, heap_bytes, peak_storage, copies = contracts[name]
    return {
        "logical_allocation_operations": n,
        "logical_object_count": n,
        "elements_per_object": 8,
        "logical_element_initializations": n * 8,
        "payload_bytes_initialized": n * 64,
        "actual_allocator_allocate_calls": allocate,
        "actual_allocator_deallocate_calls": deallocate,
        "actual_allocator_call_count": allocate + deallocate,
        "heap_bytes_requested": heap_bytes,
        "counter_scope": "LOGICAL_SOURCE_OPERATIONS_EXCEPT_INSTRUMENTED_ALLOCATOR_CALLS",
        "maximum_backing_storage_bytes": peak_storage,
        "payload_copy_operations": copies,
    }


def _source_complexity(name: str, source: str) -> dict[str, Any]:
    patterns = {
        "meldra_region": ("drop(",),
        "meldra_borrow": ("drop(",),
        "c_malloc": ("malloc(", "free("),
        "c_arena": (
            "arena_create(",
            "arena_reset(",
            "arena_allocate(",
            "arena_destroy(",
        ),
        "c_preallocated": ("uint64_t values[8]",),
        "rust_arena": ("Vec::with_capacity(", "arena.push("),
        "rust_preallocated": ("let mut values = [0u64; 8]",),
    }[name]
    operations = {
        pattern: source.count(pattern)
        for pattern in patterns
        if source.count(pattern)
    }
    nonblank_lines = [line for line in source.splitlines() if line.strip()]
    return {
        "source_bytes": len(source.encode()),
        "source_lines": len(source.splitlines()),
        "nonblank_source_lines": len(nonblank_lines),
        "lexical_token_count": len(re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+|\S", source)),
        "explicit_memory_operation_sites": sum(operations.values()),
        "explicit_memory_operations": operations,
        "strategy_specific_memory_lines": [
            line.strip()
            for line in source.splitlines()
            if any(pattern in line for pattern in patterns)
        ],
    }


def _batch_observation(
    subruns: list[dict[str, Any]],
) -> dict[str, Any]:
    batch_size = len(subruns)
    measured = [item for item in subruns if item.get("status") == "MEASURED"]
    if len(measured) != batch_size:
        return {
            "status": "FAILED",
            "correct": False,
            "invocation_count": batch_size,
            "subruns": subruns,
        }
    wall = [float(item["wall_ms"]) for item in measured]
    cpu_values = [
        float(item["cpu_ms"])
        for item in measured
        if item.get("cpu_ms") is not None
    ]
    rss = [
        int(item["peak_rss_kb"])
        for item in measured
        if item.get("peak_rss_kb") is not None
    ]
    allocations = [
        int(item["algorithm_allocations"])
        for item in measured
        if item.get("algorithm_allocations") is not None
    ]
    return {
        "status": "MEASURED",
        "correct": True,
        "invocation_count": batch_size,
        "wall_ms": statistics.fmean(wall),
        "wall_ms_total": sum(wall),
        "cpu_ms": statistics.fmean(cpu_values) if cpu_values else None,
        "peak_rss_kb": max(rss) if rss else None,
        "logical_allocations_per_invocation": (
            statistics.median(allocations) if allocations else None
        ),
        "subruns": subruns,
    }


def _arm_summary(
    batches: list[dict[str, Any]],
    *,
    seed: int,
    expected_batches: int,
) -> dict[str, Any]:
    measured = [item for item in batches if item.get("status") == "MEASURED"]
    wall = [float(item["wall_ms"]) for item in measured]
    rss = [
        float(item["peak_rss_kb"])
        for item in measured
        if item.get("peak_rss_kb") is not None
    ]
    wall_distribution = _distribution(wall, seed=seed)
    median = wall_distribution["median"]
    mad = wall_distribution["mad"]
    relative_mad = mad / median if median and mad is not None else None
    return {
        "status": (
            "MEASURED" if len(measured) == expected_batches else "FAILED"
        ),
        "correct": len(measured) == expected_batches,
        "raw_batches": batches,
        "measured_batch_count": len(measured),
        "measured_invocation_count": sum(
            item["invocation_count"] for item in measured
        ),
        "wall_ms_per_invocation": wall_distribution,
        "peak_rss_kb": _distribution(rss, seed=seed ^ 0x525353),
        "relative_mad": relative_mad,
        "dispersion_gate_max": DISPERSION_RELATIVE_MAD_MAX,
        "dispersion_gate_passed": (
            relative_mad is not None
            and relative_mad <= DISPERSION_RELATIVE_MAD_MAX
        ),
    }


def _parse_counter_output(stderr: str) -> dict[str, int]:
    keys = {
        "FAIR_LOGICAL_ALLOCATIONS": "logical_allocation_operations",
        "FAIR_ALLOC_CALLS": "actual_allocator_allocate_calls",
        "FAIR_DEALLOC_CALLS": "actual_allocator_deallocate_calls",
        "FAIR_ALLOCATED_BYTES": "heap_bytes_requested",
        "FAIR_COPIES": "payload_copy_operations",
        "FAIR_WRITES": "logical_element_initializations",
    }
    parsed: dict[str, int] = {}
    for marker, name in keys.items():
        match = re.search(rf"{marker}=(\d+)", stderr)
        if match:
            parsed[name] = int(match.group(1))
    meldra = re.search(r"MELDRA_ALLOCATIONS=(\d+)", stderr)
    if meldra:
        parsed["actual_allocator_allocate_calls"] = int(meldra.group(1))
        parsed["actual_allocator_deallocate_calls"] = int(meldra.group(1))
        parsed["heap_bytes_requested"] = int(meldra.group(1)) * 72
    return parsed


def _representative_run(
    build: _Build,
    *,
    expected: int,
    cpu: int | None,
) -> dict[str, Any]:
    if build.status != "MEASURED":
        return {"status": build.status, "error": build.stderr}
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
    try:
        checksum = int(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        checksum = None
    return {
        "status": (
            "PASS"
            if completed.returncode == 0 and checksum == expected
            else "FAIL"
        ),
        "returncode": completed.returncode,
        "checksum": checksum,
        "stderr_sha256": _sha256_bytes(completed.stderr.encode()),
        "observed_counters": _parse_counter_output(completed.stderr),
    }


def _main_body_lines(assembly: str) -> list[str]:
    lines = assembly.splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(r"^main:\s*(?:#.*)?$", line)
        ),
        None,
    )
    if start is None:
        return []
    selected = []
    for line in lines[start + 1 :]:
        if re.match(r"^\.Lfunc_end\d+:|^\s*\.size\s+main", line):
            break
        selected.append(line)
    return selected


def _main_analysis(assembly: str) -> dict[str, Any]:
    lines = _main_body_lines(assembly)
    labels = {
        match.group(1): index
        for index, line in enumerate(lines)
        if (match := re.match(r"^(\.L[A-Za-z0-9_]+):", line))
    }
    branch_targets = [
        (index, match.group(1))
        for index, line in enumerate(lines)
        if (
            match := re.match(
                r"^\s+j[a-z]+\s+(\.L[A-Za-z0-9_]+)", line
            )
        )
    ]
    backedges = [
        target
        for index, target in branch_targets
        if target in labels and labels[target] < index
    ]
    memory_stores = [
        line.strip()
        for line in lines
        if re.match(
            r"^\s*(?:v?mov|stos)[A-Za-z0-9.]*\s+[^,]+,\s*"
            r"(?:[-+]?\d+)?\([^)]*\)",
            line,
        )
    ]
    memory_loads = [
        line.strip()
        for line in lines
        if re.match(
            r"^\s*(?:v?mov|lods)[A-Za-z0-9.]*\s+"
            r"(?:[-+]?\d+)?\([^)]*\),\s*%",
            line,
        )
    ]
    calls = [
        match.group(1)
        for line in lines
        if (match := re.match(r"^\s*callq?\s+([^\s#]+)", line))
    ]
    return {
        "backedge_branch_count": len(backedges),
        "backedge_targets": backedges,
        "loop_eliminated_from_main": len(backedges) == 0,
        "memory_store_site_count": len(memory_stores),
        "memory_store_examples": memory_stores[:20],
        "memory_load_site_count": len(memory_loads),
        "memory_load_examples": memory_loads[:20],
        "direct_call_targets": sorted(set(calls)),
    }


def _main_mnemonics(assembly: str) -> list[str]:
    return [
        match.group(1)
        for line in _main_body_lines(assembly)
        if (
            match := re.match(
                r"^\s+([A-Za-z][A-Za-z0-9.]*)\s", line
            )
        )
    ]


def _c_assembly_audit(
    name: str,
    source_path: Path,
    root: Path,
) -> dict[str, Any]:
    compiler = find_c_compiler()
    if compiler is None:
        return {"status": "UNMEASURED_TOOLCHAIN_UNAVAILABLE"}
    assembly = root / f"{name}.s"
    optimization = root / f"{name}.opt.yaml"
    result = _compile_assembly(
        compiler, source_path, assembly, optimization
    )
    if result["status"] != "MEASURED":
        return result
    text = assembly.read_text(encoding="utf-8")
    mnemonics = _main_mnemonics(text)
    main_analysis = _main_analysis(text)
    result.update(
        {
            "compiler_family": "c",
            "source_path": str(source_path),
            "assembly_path": str(assembly),
            "optimization_record_path": str(optimization),
            "main_instruction_count": len(mnemonics),
            "main_mnemonic_sha256": _canonical_digest(mnemonics),
            "main_mnemonic_histogram": dict(sorted(Counter(mnemonics).items())),
            "main_mnemonics": mnemonics,
            "main_analysis": main_analysis,
        }
    )
    return result


def _rust_assembly_audit(
    name: str,
    source_path: Path,
    build: _Build,
    root: Path,
) -> dict[str, Any]:
    assembly = root / f"{name}.s"
    rustc = shutil.which("rustc")
    if rustc:
        command = (
            rustc,
            "-C",
            "opt-level=3",
            "-C",
            "debuginfo=0",
            "-C",
            "codegen-units=1",
            "--emit=asm",
            str(source_path),
            "-o",
            str(assembly),
        )
    elif build.status == "MEASURED" and build.compiler and "rust:" in build.compiler:
        docker = shutil.which("docker")
        if docker is None:
            return {"status": "UNMEASURED_TOOLCHAIN_UNAVAILABLE"}
        image = build.compiler.split("@", 1)[0]
        directory = source_path.parent.resolve()
        command = (
            docker,
            "run",
            "--rm",
            "-u",
            f"{os.getuid()}:{os.getgid()}",
            "-e",
            "HOME=/tmp",
            "-e",
            "SOURCE_DATE_EPOCH=0",
            "-v",
            f"{directory}:/work",
            "-w",
            "/work",
            image,
            "rustc",
            "-C",
            "opt-level=3",
            "-C",
            "debuginfo=0",
            "-C",
            "codegen-units=1",
            "--emit=asm",
            "/work/main.rs",
            "-o",
            f"/work/{assembly.name}",
        )
        assembly = source_path.parent / assembly.name
    else:
        return {"status": "UNMEASURED_TOOLCHAIN_UNAVAILABLE"}
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
        env=dict(os.environ, LC_ALL="C", TZ="UTC", SOURCE_DATE_EPOCH="0"),
    )
    if completed.returncode != 0 or not assembly.is_file():
        return {
            "status": "FAILED",
            "command": list(command),
            "returncode": completed.returncode,
            "stderr": completed.stderr,
        }
    text = assembly.read_text(encoding="utf-8")
    mnemonics = _main_mnemonics(text)
    main_analysis = _main_analysis(text)
    return {
        "status": "MEASURED",
        "compiler_family": "rust",
        "command": list(command),
        "source_path": str(source_path),
        "assembly_path": str(assembly),
        "source_sha256": _sha256_path(source_path),
        "assembly_sha256": _sha256_path(assembly),
        "optimization_record_present": False,
        "metrics": _assembly_metrics(text),
        "main_instruction_count": len(mnemonics),
        "main_mnemonic_sha256": _canonical_digest(mnemonics),
        "main_mnemonic_histogram": dict(sorted(Counter(mnemonics).items())),
        "main_mnemonics": mnemonics,
        "main_analysis": main_analysis,
    }


def _assembly_comparison(
    left: dict[str, Any], right: dict[str, Any]
) -> dict[str, Any]:
    if left.get("status") != "MEASURED" or right.get("status") != "MEASURED":
        return {"status": "UNMEASURED"}
    left_sequence = left["main_mnemonics"]
    right_sequence = right["main_mnemonics"]
    left_calls = set(left["main_analysis"]["direct_call_targets"])
    right_calls = set(right["main_analysis"]["direct_call_targets"])
    return {
        "status": "MEASURED",
        "machine_main_identical": left["main_mnemonic_sha256"]
        == right["main_mnemonic_sha256"],
        "mnemonic_sequence_similarity": SequenceMatcher(
            None, left_sequence, right_sequence, autojunk=False
        ).ratio(),
        "left_instruction_count": len(left_sequence),
        "right_instruction_count": len(right_sequence),
        "left_only_call_targets": sorted(left_calls - right_calls),
        "right_only_call_targets": sorted(right_calls - left_calls),
        "left_conditional_branches": left["metrics"][
            "conditional_branch_count"
        ],
        "right_conditional_branches": right["metrics"][
            "conditional_branch_count"
        ],
        "left_malloc_mentions": left["metrics"]["malloc_mentions"],
        "right_malloc_mentions": right["metrics"]["malloc_mentions"],
        "left_free_mentions": left["metrics"]["free_mentions"],
        "right_free_mentions": right["metrics"]["free_mentions"],
        "left_main_analysis": left["main_analysis"],
        "right_main_analysis": right["main_analysis"],
        "same_c_compiler_policy": left["command"][:8] == right["command"][:8],
        "both_optimization_records_present": left[
            "optimization_record_present"
        ]
        and right["optimization_record_present"],
    }


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return numerator / denominator


def _decision_basis(report: dict[str, Any]) -> dict[str, Any]:
    ratios = report["ratios"]
    fair_c_over_malloc = min(
        value
        for value in (
            ratios["c_arena_over_c_malloc"],
            ratios["c_preallocated_over_c_malloc"],
        )
        if value is not None
    )
    meldra_over_malloc = min(
        value
        for value in (
            ratios["meldra_region_over_c_malloc"],
            ratios["meldra_borrow_over_c_malloc"],
        )
        if value is not None
    )
    fair_ratios = (
        ratios["meldra_region_over_fastest_fair_c"],
        ratios["meldra_borrow_over_fastest_fair_c"],
    )
    meldra_complexity = max(
        report["source_complexity"][name]["explicit_memory_operation_sites"]
        for name in ("meldra_region", "meldra_borrow")
    )
    manual_complexity = min(
        report["source_complexity"][name]["explicit_memory_operation_sites"]
        for name in ("c_arena", "c_preallocated")
    )
    return {
        "competitive_threshold": 1.10,
        "parity_band": [0.95, 1.10],
        "fair_c_over_c_malloc": fair_c_over_malloc,
        "best_meldra_over_c_malloc": meldra_over_malloc,
        "fair_c_closes_at_least_90_percent_of_old_gap": (
            fair_c_over_malloc <= meldra_over_malloc * 1.10
        ),
        "meldra_in_fair_c_parity_band": all(
            value is not None and 0.95 <= value <= 1.10
            for value in fair_ratios
        ),
        "meldra_explicit_memory_operation_sites": meldra_complexity,
        "manual_c_explicit_memory_operation_sites": manual_complexity,
    }


def _select_status(report: dict[str, Any]) -> str:
    measured = [
        item for item in report["arms"] if item["status"] == "MEASURED"
    ]
    if (
        report["correctness_failures"]
        or report["counter_contract_failures"]
        or not measured
        or any(not item["dispersion_gate_passed"] for item in measured)
    ):
        return "INCONCLUSIVE_MEASUREMENT"
    basis = _decision_basis(report)
    if (
        basis["fair_c_closes_at_least_90_percent_of_old_gap"]
        and basis["meldra_in_fair_c_parity_band"]
    ):
        return "ADVANTAGE_MOSTLY_BASELINE_ARTIFACT"
    competitive = all(
        report["ratios"][name] is not None
        and report["ratios"][name] <= basis["competitive_threshold"]
        for name in (
            "meldra_region_over_fastest_fair_c",
            "meldra_borrow_over_fastest_fair_c",
        )
    )
    if (
        competitive
        and basis["meldra_explicit_memory_operation_sites"]
        <= basis["manual_c_explicit_memory_operation_sites"]
    ):
        return "AUTO_REGION_ADVANTAGE_SUPPORTED"
    return "ADVANTAGE_MOSTLY_BASELINE_ARTIFACT"


def validate_fair_memory_report(report: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    protocol = report["protocol"]
    if protocol["warmups"] != 5 or protocol["measured_batches"] < 30:
        failures.append("run_denominators")
    if protocol["runtime_input"] != 500_000:
        failures.append("runtime_input_changed")
    if protocol["logical_object_count_per_invocation"] != 500_000:
        failures.append("logical_object_count_changed")
    if report["freeze"]["workload_contract"]["expected_checksum"] != 250_003_000_000:
        failures.append("expected_checksum_changed")
    if report["correctness_failures"]:
        failures.append("correctness")
    if report["counter_contract_failures"]:
        failures.append("counter_contract")
    if set(item["name"] for item in report["arms"]) != set(_ARM_ORDER):
        failures.append("arm_set")
    if report["decision"] not in {
        "AUTO_REGION_ADVANTAGE_SUPPORTED",
        "ADVANTAGE_MOSTLY_BASELINE_ARTIFACT",
        "INCONCLUSIVE_MEASUREMENT",
    }:
        failures.append("decision")
    return failures


def run_fair_memory_strategy_benchmark(
    *,
    output_dir: str | Path = "tools/benchmarks/merlo/benchmarks/fair_memory_strategy",
    repetitions: int = 30,
    warmups: int = 5,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, Any]:
    if repetitions < 30 or warmups != 5 or batch_size < 1:
        raise ValueError("fair memory benchmark requires 5 warmups and 30 measured batches")
    root = Path(output_dir)
    corpus = root / "corpus"
    audit_root = root / "audit"
    root.mkdir(parents=True, exist_ok=True)
    corpus.mkdir(parents=True, exist_ok=True)
    audit_root.mkdir(parents=True, exist_ok=True)
    workload = _workload()
    source = _meldra_source(workload)
    expected = reference_checksum(workload)
    freeze = _freeze_manifest(root)

    sources = {
        "meldra_region": source,
        "meldra_borrow": source,
        "c_malloc": competitor_source("c", workload),
        "c_arena": _c_arena_source(),
        "c_preallocated": _c_preallocated_source(),
        "rust_arena": _rust_source("arena"),
        "rust_preallocated": _rust_source("preallocated"),
    }
    builds: dict[str, _Build] = {}
    metadata: dict[str, Any] = {}
    for strategy in ("region", "borrow"):
        name = f"meldra_{strategy}"
        builds[name], metadata[name] = _compile_meldra(
            strategy,
            source,
            corpus / name,
            workload.input,
        )
        generated = corpus / name / "generated.c"
        frozen = freeze["frozen_implementations"][strategy]
        if _sha256_path(generated) != frozen["generated_c_sha256"]:
            raise AssertionError(f"frozen Meldra {strategy} implementation changed")
    for name in ("c_malloc", "c_arena", "c_preallocated"):
        (corpus / name).mkdir(parents=True, exist_ok=True)
        builds[name] = _compile_external(
            "c", sources[name], corpus / name, (str(workload.input),)
        )
    for name in ("rust_arena", "rust_preallocated"):
        (corpus / name).mkdir(parents=True, exist_ok=True)
        builds[name] = _compile_external(
            "rust", sources[name], corpus / name, (str(workload.input),)
        )

    (audit_root / "c_malloc_counters").mkdir(parents=True, exist_ok=True)
    audit_malloc_build = _compile_external(
        "c",
        _C_MALLOC_AUDIT_SOURCE,
        audit_root / "c_malloc_counters",
        (str(workload.input),),
    )
    (audit_root / "c_malloc_counter_source.c").write_text(
        _C_MALLOC_AUDIT_SOURCE, encoding="utf-8"
    )

    state_before = _cpu_state()
    cpu = state_before["selected_cpu"]
    measured_names = [
        name for name, build in builds.items() if build.status == "MEASURED"
    ]
    samples: dict[str, list[dict[str, Any]]] = {
        name: [] for name in measured_names
    }
    rng = random.Random(BENCHMARK_SEED ^ 0xFA17_0A11)
    schedule_hash = hashlib.sha256()
    for round_index in range(warmups + repetitions):
        round_subruns: dict[str, list[dict[str, Any]]] = {
            name: [] for name in measured_names
        }
        schedule = [
            name
            for name in measured_names
            for _ in range(batch_size)
        ]
        rng.shuffle(schedule)
        for invocation_index, name in enumerate(schedule):
            schedule_hash.update(
                f"{round_index}:{invocation_index}:{name}\n".encode()
            )
            round_subruns[name].append(
                _run_one(builds[name], expected, cpu)
            )
        if round_index >= warmups:
            for name in measured_names:
                samples[name].append(
                    _batch_observation(round_subruns[name])
                )
    state_after = _cpu_state()

    representatives: dict[str, dict[str, Any]] = {}
    for name, build in builds.items():
        counter_build = audit_malloc_build if name == "c_malloc" else build
        representatives[name] = _representative_run(
            counter_build, expected=expected, cpu=cpu
        )

    counter_failures = []
    counter_evidence: dict[str, dict[str, Any]] = {}
    for name in _ARM_ORDER:
        contract = _counter_contract(name, workload.input)
        representative = representatives[name]
        observed = representative.get("observed_counters", {})
        if name.startswith("meldra_"):
            observed = {
                **contract,
                **observed,
                "logical_allocation_operations": workload.input,
                "logical_element_initializations": workload.input * 8,
                "payload_copy_operations": 0,
            }
        matches = all(
            observed.get(key) == contract[key]
            for key in (
                "logical_allocation_operations",
                "actual_allocator_allocate_calls",
                "actual_allocator_deallocate_calls",
                "heap_bytes_requested",
                "payload_copy_operations",
                "logical_element_initializations",
            )
        ) if representative.get("status") == "PASS" else False
        if builds[name].status != "MEASURED":
            matches = True
        elif not matches:
            counter_failures.append(name)
        counter_evidence[name] = {
            "contract": contract,
            "representative_run": representative,
            "contract_matches_runtime_counters": matches,
        }

    arms = []
    for index, name in enumerate(_ARM_ORDER):
        build = builds[name]
        summary = _arm_summary(
            samples.get(name, []),
            seed=BENCHMARK_SEED ^ 0xFA17 ^ index,
            expected_batches=repetitions,
        )
        if build.status != "MEASURED":
            summary.update(
                {
                    "status": "UNMEASURED_TOOLCHAIN_UNAVAILABLE"
                    if name.startswith("rust_")
                    else build.status,
                    "correct": None,
                    "dispersion_gate_passed": None,
                }
            )
        arms.append(
            {
                "name": name,
                "build_status": build.status,
                "compiler": build.compiler,
                "compiler_version": build.compiler_version,
                "compile_command": list(build.command),
                "binary_size": build.binary_size,
                "binary_sha256": build.binary_sha256,
                "source_sha256": build.source_sha256,
                "source_path": str(
                    corpus
                    / name
                    / ("main.meldra" if name.startswith("meldra_") else "main.c" if name.startswith("c_") else "main.rs")
                ),
                "metadata": metadata.get(name),
                "counter_evidence": counter_evidence[name],
                **summary,
            }
        )

    assembly: dict[str, dict[str, Any]] = {}
    for name in ("meldra_region", "meldra_borrow"):
        assembly[name] = _c_assembly_audit(
            name, corpus / name / "generated.c", audit_root
        )
    for name in ("c_malloc", "c_arena", "c_preallocated"):
        assembly[name] = _c_assembly_audit(
            name, corpus / name / "main.c", audit_root
        )
    for name in ("rust_arena", "rust_preallocated"):
        assembly[name] = _rust_assembly_audit(
            name, corpus / name / "main.rs", builds[name], audit_root
        )

    assembly_comparisons = {
        "meldra_region_vs_c_arena": _assembly_comparison(
            assembly["meldra_region"], assembly["c_arena"]
        ),
        "meldra_region_vs_c_preallocated": _assembly_comparison(
            assembly["meldra_region"], assembly["c_preallocated"]
        ),
        "meldra_borrow_vs_c_preallocated": _assembly_comparison(
            assembly["meldra_borrow"], assembly["c_preallocated"]
        ),
    }

    source_complexity = {
        name: _source_complexity(name, sources[name]) for name in _ARM_ORDER
    }
    by_name = {item["name"]: item for item in arms}
    medians = {
        name: by_name[name]["wall_ms_per_invocation"]["median"]
        if by_name[name]["status"] == "MEASURED"
        else None
        for name in _ARM_ORDER
    }
    fair_c_medians = [
        medians[name]
        for name in ("c_arena", "c_preallocated")
        if medians[name] is not None
    ]
    fastest_fair_c = min(fair_c_medians) if fair_c_medians else None
    ratios = {
        "meldra_region_over_c_malloc": _ratio(
            medians["meldra_region"], medians["c_malloc"]
        ),
        "meldra_borrow_over_c_malloc": _ratio(
            medians["meldra_borrow"], medians["c_malloc"]
        ),
        "meldra_region_over_c_arena": _ratio(
            medians["meldra_region"], medians["c_arena"]
        ),
        "meldra_borrow_over_c_arena": _ratio(
            medians["meldra_borrow"], medians["c_arena"]
        ),
        "meldra_region_over_c_preallocated": _ratio(
            medians["meldra_region"], medians["c_preallocated"]
        ),
        "meldra_borrow_over_c_preallocated": _ratio(
            medians["meldra_borrow"], medians["c_preallocated"]
        ),
        "meldra_region_over_fastest_fair_c": _ratio(
            medians["meldra_region"], fastest_fair_c
        ),
        "meldra_borrow_over_fastest_fair_c": _ratio(
            medians["meldra_borrow"], fastest_fair_c
        ),
        "rust_arena_over_c_arena": _ratio(
            medians["rust_arena"], medians["c_arena"]
        ),
        "rust_preallocated_over_c_preallocated": _ratio(
            medians["rust_preallocated"], medians["c_preallocated"]
        ),
        "c_arena_over_c_malloc": _ratio(
            medians["c_arena"], medians["c_malloc"]
        ),
        "c_preallocated_over_c_malloc": _ratio(
            medians["c_preallocated"], medians["c_malloc"]
        ),
    }
    correctness_failures = [
        item["name"]
        for item in arms
        if item["status"] == "MEASURED" and not item["correct"]
    ]
    report = {
        "schema_version": FAIR_MEMORY_SCHEMA_VERSION,
        "kind": "MeldraFairMemoryStrategyBenchmark",
        "freeze": freeze,
        "protocol": {
            "runtime_input": workload.input,
            "logical_object_count_per_invocation": workload.input,
            "elements_per_object": 8,
            "expected_checksum": expected,
            "warmups": warmups,
            "measured_batches": repetitions,
            "invocations_per_batch": batch_size,
            "measured_invocations_per_arm": repetitions * batch_size,
            "warmup_invocations_per_arm": warmups * batch_size,
            "randomized_arm_order": True,
            "schedule_seed": BENCHMARK_SEED ^ 0xFA17_0A11,
            "schedule_sha256": schedule_hash.hexdigest(),
            "cpu_affinity": cpu,
            "dispersion_relative_mad_max": DISPERSION_RELATIVE_MAD_MAX,
            "stable_only_if_dispersion_passes": True,
            "duration_alone_never_implies_stability": True,
            "batching": "Each raw batch averages identical fresh-process invocations with the frozen runtime input; no algorithm or object count per invocation changes.",
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
        "arms": arms,
        "ratios": ratios,
        "counter_contract_failures": counter_failures,
        "correctness_failures": correctness_failures,
        "source_complexity": source_complexity,
        "assembly": assembly,
        "assembly_comparisons": assembly_comparisons,
        "audit_answers": {
            "region_loop_differs_from_c_arena": not assembly_comparisons[
                "meldra_region_vs_c_arena"
            ].get("machine_main_identical", False),
            "meldra_extra_calls": assembly_comparisons[
                "meldra_region_vs_c_arena"
            ].get("left_only_call_targets"),
            "same_c_compiler_policy": assembly_comparisons[
                "meldra_region_vs_c_arena"
            ].get("same_c_compiler_policy"),
            "automatic_strategy_competitive_threshold": "<=1.10x fastest of C arena and C preallocated",
            "meldra_surface_strategy_specific_allocator_calls": 0,
        },
        "limitations": [
            "The frozen workload creates non-escaping eight-element UInt64 objects; it does not represent escaping aliases or cyclic graphs.",
            "Batch means reduce fresh-process timing noise without changing the frozen runtime input or logical object count per invocation.",
            "C preallocated and Rust preallocated arms intentionally use one stack buffer because the object does not escape.",
            "The C arena and Rust arena reserve storage for every logical object, while preallocated and Meldra lowering reuse one local storage slot.",
            "Algorithm allocator counters exclude process startup and standard-library allocations outside the measured run function.",
            "Meldra surface contains one generic drop statement but no region, arena, malloc, free, capacity, or buffer-management API.",
            "C and Meldra C output use the same C compiler policy; Rust uses rustc and is not used to claim identical backend optimization.",
            "This experiment can support only a claim about automatic strategy selection on this workload, never that Meldra is generally faster than C or Rust.",
        ],
    }
    report["decision_basis"] = _decision_basis(report)
    report["decision"] = _select_status(report)
    report["validation_failures"] = validate_fair_memory_report(report)
    report["status"] = "PASS" if not report["validation_failures"] else "FAIL"
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (root / "report.json").write_text(text, encoding="utf-8")
    Path("tools/benchmarks/merlo/benchmarks/meldra_fair_memory_strategy.json").write_text(
        text, encoding="utf-8"
    )
    return report


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DISPERSION_RELATIVE_MAD_MAX",
    "FAIR_MEMORY_SCHEMA_VERSION",
    "run_fair_memory_strategy_benchmark",
    "validate_fair_memory_report",
]
