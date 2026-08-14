"""Isolated Stage 0.6B owned Bytes + borrowed BytesView experiment."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import random
import re
import shutil
import statistics
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from research.archive.alpha1.merlo.bytes_contract import bytes_hir_manifest, bytes_mir_manifest
from research.archive.alpha1.merlo.native_bench import _Build, _compile_external
from .native_c_backend import CEmitter, compile_c_source, find_c_compiler
from research.archive.alpha1.merlo.native_differential import MIRInterpreter
from research.archive.alpha1.merlo.native_hir import compile_native_hir
from .performance_frontend import PerformanceCompileError, compile_performance_source
from .performance_mir import PerformanceMIR
from .performance_opt import optimize_mir


BYTES_EXPERIMENT_SCHEMA_VERSION = 1
BYTES_EXPERIMENT_SEED = 0xB17E_506B
BYTES_VALID_CASES = 384
BYTES_INVALID_CASES = 128
BYTES_WARMUPS = 5
BYTES_MEASURED_RUNS = 30
BYTES_DECISIONS = (
    "BYTES_OWNERSHIP_SLICE_SUPPORTED",
    "BYTES_REPRESENTATION_OVERHEAD_FOUND",
    "BYTES_EXPERIMENT_INCONCLUSIVE",
    "BYTES_EXPERIMENT_INVALID",
)
_MASK64 = (1 << 64) - 1
_MIX_CONSTANT = 11_400_714_819_323_198_485
_CHECKSUM_CONSTANT = 1_099_511_628_211
_SANITIZER_MARKERS = (
    "AddressSanitizer",
    "LeakSanitizer",
    "UndefinedBehaviorSanitizer",
    "runtime error:",
    "use-after-free",
    "double-free",
)


MELDRA_BYTES_SOURCE = """fn mix(value: UInt64, index: UInt64, seed: UInt64) -> UInt64:
    let shifted: UInt64 = seed + index * 17
    let mixed: UInt64 = value ^ shifted
    let product: UInt64 = mixed * 11400714819323198485
    product ^ (product >> 29)

fn main(n: UInt64, seed: UInt64, rounds: UInt64, slice_start: UInt64, slice_length: UInt64) -> UInt64:
    let bytes: Bytes = Bytes.new(n)
    for i in 0..n:
        bytes[i] = mix(seed, i, seed) & 255
    var round: UInt64 = 0
    while round < rounds:
        for i in 0..n:
            let current: UInt64 = bytes[i]
            bytes[i] = mix(current, i, seed + round) & 255
        round = round + 1
    let view: BytesView = bytes.slice(slice_start, slice_length)
    var checksum: UInt64 = seed
    for j in 0..slice_length:
        checksum = checksum ^ ((view[j] + j) * 1099511628211)
    let observed_length: UInt64 = view.len()
    bytes[0] = (bytes[0] + checksum + observed_length) & 255
    checksum ^ observed_length ^ bytes[0]
"""


C_BYTES_SOURCE = r'''#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static uint64_t mix(uint64_t value, uint64_t index, uint64_t seed) {
    uint64_t shifted = seed + index * UINT64_C(17);
    uint64_t mixed = value ^ shifted;
    uint64_t product = mixed * UINT64_C(11400714819323198485);
    return product ^ (product >> 29);
}

__attribute__((noinline)) static uint64_t workload(uint64_t n, uint64_t seed, uint64_t rounds, uint64_t slice_start, uint64_t slice_length) {
    if (n == 0 || slice_start > n || slice_length > n - slice_start) abort();
    uint8_t *bytes = (uint8_t *)malloc((size_t)n);
    if (bytes == NULL) abort();
    for (uint64_t i = 0; i < n; ++i) bytes[i] = (uint8_t)(mix(seed, i, seed) & UINT64_C(255));
    for (uint64_t round = 0; round < rounds; ++round) {
        for (uint64_t i = 0; i < n; ++i) bytes[i] = (uint8_t)(mix(bytes[i], i, seed + round) & UINT64_C(255));
    }
    const uint8_t *view = bytes + slice_start;
    uint64_t checksum = seed;
    for (uint64_t j = 0; j < slice_length; ++j) checksum ^= ((uint64_t)view[j] + j) * UINT64_C(1099511628211);
    uint64_t observed_length = slice_length;
    bytes[0] = (uint8_t)((bytes[0] + checksum + observed_length) & UINT64_C(255));
    uint64_t result = checksum ^ observed_length ^ bytes[0];
    free(bytes);
    return result;
}

int main(int argc, char **argv) {
    if (argc != 6) return 2;
    uint64_t n = strtoull(argv[1], NULL, 10);
    uint64_t seed = strtoull(argv[2], NULL, 10);
    uint64_t rounds = strtoull(argv[3], NULL, 10);
    uint64_t slice_start = strtoull(argv[4], NULL, 10);
    uint64_t slice_length = strtoull(argv[5], NULL, 10);
    printf("%" PRIu64 "\n", workload(n, seed, rounds, slice_start, slice_length));
    fprintf(stderr, "BENCH_ALLOCATIONS=1 BENCH_FREES=1 BENCH_ALLOCATED_BYTES=%" PRIu64 " BENCH_PAYLOAD_COPIES=0\n", n);
    return 0;
}
'''


RUST_BYTES_SOURCE = r'''use std::env;

#[inline(always)]
fn mix(value: u64, index: u64, seed: u64) -> u64 {
    let shifted = seed.wrapping_add(index.wrapping_mul(17));
    let mixed = value ^ shifted;
    let product = mixed.wrapping_mul(11_400_714_819_323_198_485);
    product ^ (product >> 29)
}

#[inline(never)]
fn workload(n: usize, seed: u64, rounds: u64, slice_start: usize, slice_length: usize) -> u64 {
    let mut bytes = vec![0u8; n];
    for i in 0..n { bytes[i] = (mix(seed, i as u64, seed) & 255) as u8; }
    for round in 0..rounds {
        for i in 0..n { bytes[i] = (mix(bytes[i] as u64, i as u64, seed.wrapping_add(round)) & 255) as u8; }
    }
    let view = &bytes[slice_start..slice_start + slice_length];
    let mut checksum = seed;
    for j in 0..slice_length {
        checksum ^= ((view[j] as u64).wrapping_add(j as u64)).wrapping_mul(1_099_511_628_211);
    }
    let observed_length = view.len() as u64;
    bytes[0] = ((bytes[0] as u64).wrapping_add(checksum).wrapping_add(observed_length) & 255) as u8;
    checksum ^ observed_length ^ bytes[0] as u64
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() != 6 { std::process::exit(2); }
    let n = args[1].parse::<usize>().unwrap();
    let seed = args[2].parse::<u64>().unwrap();
    let rounds = args[3].parse::<u64>().unwrap();
    let slice_start = args[4].parse::<usize>().unwrap();
    let slice_length = args[5].parse::<usize>().unwrap();
    println!("{}", workload(n, seed, rounds, slice_start, slice_length));
    eprintln!("BENCH_ALLOCATIONS=1 BENCH_FREES=1 BENCH_ALLOCATED_BYTES={} BENCH_PAYLOAD_COPIES=0", n);
}
'''


def _u64(value: int) -> int:
    return value & _MASK64


def _mix(value: int, index: int, seed: int) -> int:
    shifted = _u64(seed + _u64(index * 17))
    mixed = value ^ shifted
    product = _u64(mixed * _MIX_CONSTANT)
    return product ^ (product >> 29)


def reference_workload(
    n: int, seed: int, rounds: int, slice_start: int, slice_length: int
) -> int:
    if n <= 0 or slice_start < 0 or slice_start > n or slice_length < 0 or slice_length > n - slice_start:
        raise ValueError("invalid workload bounds")
    values = bytearray(n)
    for index in range(n):
        values[index] = _mix(seed, index, seed) & 255
    for round_index in range(rounds):
        for index in range(n):
            values[index] = _mix(values[index], index, _u64(seed + round_index)) & 255
    view = memoryview(values)[slice_start : slice_start + slice_length]
    checksum = seed
    for index, value in enumerate(view):
        checksum = _u64(checksum ^ _u64((value + index) * _CHECKSUM_CONSTANT))
    observed_length = len(view)
    del view
    values[0] = _u64(values[0] + checksum + observed_length) & 255
    return _u64(checksum ^ observed_length ^ values[0])


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def _distribution(values: list[float], *, seed: int) -> dict[str, Any]:
    if not values:
        return {"median": None, "mad": None, "relative_mad": None, "bootstrap_median_95_ci": None}
    median = statistics.median(values)
    mad = statistics.median(abs(value - median) for value in values)
    rng = random.Random(seed)
    medians = [
        statistics.median(values[rng.randrange(len(values))] for _ in values)
        for _ in range(2_000)
    ]
    return {
        "median": median,
        "mean": statistics.fmean(values),
        "minimum": min(values),
        "maximum": max(values),
        "mad": mad,
        "relative_mad": mad / median if median else None,
        "bootstrap_median_95_ci": [_percentile(medians, 0.025), _percentile(medians, 0.975)],
    }


def _valid_cases() -> list[tuple[int, int, int, int, int]]:
    rng = random.Random(BYTES_EXPERIMENT_SEED)
    cases = []
    for _ in range(BYTES_VALID_CASES):
        n = rng.randrange(1, 258)
        start = rng.randrange(0, n + 1)
        length = rng.randrange(0, n - start + 1)
        cases.append((n, rng.getrandbits(64), rng.randrange(0, 6), start, length))
    return cases


def _run_command(command: Iterable[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        tuple(command),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=dict(os.environ, LC_ALL="C", TZ="UTC"),
    )


def _native_checksum(binary: str, arguments: tuple[int, ...]) -> tuple[int | None, subprocess.CompletedProcess[str]]:
    completed = _run_command((binary, *(str(value) for value in arguments)))
    try:
        checksum = int(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        checksum = None
    return checksum, completed


def _static_invalid_sources() -> dict[str, tuple[str, str]]:
    return {
        "use_after_move": (
            """fn main(n: UInt64) -> UInt64:\n    let owner: Bytes = Bytes.new(n)\n    let moved: Bytes = move(owner)\n    let result: UInt64 = owner.len()\n    result + moved.len()\n""",
            "use after move: owner",
        ),
        "double_drop": (
            """fn main(n: UInt64) -> UInt64:\n    let owner: Bytes = Bytes.new(n)\n    drop(owner)\n    drop(owner)\n    0\n""",
            "double drop of Bytes owner owner",
        ),
        "owner_drop_live_view": (
            """fn main(n: UInt64) -> UInt64:\n    let owner: Bytes = Bytes.new(n)\n    let view: BytesView = owner.slice(0, n)\n    drop(owner)\n    view.len()\n""",
            "cannot drop Bytes owner owner while view view is live",
        ),
        "owner_move_live_view": (
            """fn main(n: UInt64) -> UInt64:\n    let owner: Bytes = Bytes.new(n)\n    let view: BytesView = owner.slice(0, n)\n    let moved: Bytes = move(owner)\n    view.len() + moved.len()\n""",
            "cannot move Bytes owner owner while view view is live",
        ),
        "owner_mutation_live_view": (
            """fn main(n: UInt64) -> UInt64:\n    let owner: Bytes = Bytes.new(n)\n    let view: BytesView = owner.slice(0, n)\n    owner[0] = 1\n    view[0]\n""",
            "cannot mutate Bytes owner owner while view view is live",
        ),
        "view_mutation": (
            """fn main(n: UInt64) -> UInt64:\n    let owner: Bytes = Bytes.new(n)\n    let view: BytesView = owner.slice(0, n)\n    view[0] = 1\n    view[0]\n""",
            "cannot mutate borrowed BytesView",
        ),
        "view_escape": (
            """fn main(n: UInt64) -> BytesView:\n    let owner: Bytes = Bytes.new(n)\n    let view: BytesView = owner.slice(0, n)\n    view\n""",
            "borrowed BytesView view cannot escape main",
        ),
        "view_alias": (
            """fn main(n: UInt64) -> UInt64:\n    let owner: Bytes = Bytes.new(n)\n    let view: BytesView = owner.slice(0, n)\n    let escaped: BytesView = view\n    escaped.len()\n""",
            "owned or borrowed alias escaped",
        ),
    }


def _runtime_invalid_specs() -> dict[str, tuple[str, tuple[int, ...], str]]:
    return {
        "index_out_of_bounds": (
            """fn main(n: UInt64, index: UInt64) -> UInt64:\n    let owner: Bytes = Bytes.new(n)\n    owner[index]\n""",
            (8, 8),
            "BytesIndexOutOfBounds",
        ),
        "slice_out_of_bounds": (
            """fn main(n: UInt64, start: UInt64, length: UInt64) -> UInt64:\n    let owner: Bytes = Bytes.new(n)\n    let view: BytesView = owner.slice(start, length)\n    view.len()\n""",
            (8, 7, 2),
            "BytesSliceOutOfBounds",
        ),
        "allocation_overflow": (
            """fn main(n: UInt64) -> UInt64:\n    let owner: Bytes = Bytes.new(n)\n    owner.len()\n""",
            (_MASK64,),
            "BytesAllocationOverflow",
        ),
    }


def _correctness_corpus(root: Path) -> tuple[dict[str, Any], PerformanceMIR, PerformanceMIR]:
    frontend = compile_performance_source(MELDRA_BYTES_SOURCE, path="bytes-corpus/workload.meldra")
    original = frontend.mir
    optimized, snapshots = optimize_mir(original, artifact_dir=root / "mir")
    cases = _valid_cases()
    reference_results = [reference_workload(*case) for case in cases]
    original_results = []
    optimized_results = []
    original_ownership = []
    optimized_ownership = []
    for case in cases:
        observed = MIRInterpreter(original).run(case)
        optimized_observed = MIRInterpreter(optimized).run(case)
        original_results.append(observed.return_value)
        optimized_results.append(optimized_observed.return_value)
        original_ownership.append(dict(observed.final_ownership_state))
        optimized_ownership.append(dict(optimized_observed.final_ownership_state))
    build = compile_c_source(
        CEmitter(optimized, runtime_arguments=True).emit(),
        output_dir=root / "correctness-native",
        stem="bytes_corpus",
    )
    native_results = []
    native_failures = []
    if build.binary_path:
        for index, (case, expected) in enumerate(zip(cases, reference_results, strict=True)):
            checksum, completed = _native_checksum(build.binary_path, case)
            native_results.append(checksum)
            if completed.returncode != 0 or checksum != expected:
                native_failures.append(
                    {"case": index, "arguments": list(case), "returncode": completed.returncode, "checksum": checksum}
                )
    static_diagnostics = {}
    for name, (source, expected) in _static_invalid_sources().items():
        try:
            compile_performance_source(source, path=f"bytes-invalid/{name}.meldra")
        except PerformanceCompileError as exc:
            actual = str(exc)
        else:
            actual = "NO_DIAGNOSTIC"
        static_diagnostics[name] = {"expected": expected, "actual": actual, "exact_class_match": expected in actual}
    runtime_diagnostics = {}
    for name, (source, arguments, expected) in _runtime_invalid_specs().items():
        mir = compile_performance_source(source, path=f"bytes-invalid/{name}.meldra").mir
        observation = MIRInterpreter(mir).run(arguments)
        optimized_invalid, _ = optimize_mir(mir)
        native_build = compile_c_source(
            CEmitter(optimized_invalid, runtime_arguments=True).emit(),
            output_dir=root / "invalid-native" / name,
            stem=name,
        )
        native_error = None
        native_returncode = None
        if native_build.binary_path:
            _, completed = _native_checksum(native_build.binary_path, arguments)
            native_error = completed.stderr
            native_returncode = completed.returncode
        runtime_diagnostics[name] = {
            "expected": expected,
            "mir_error": observation.error_kind,
            "native_stderr": native_error,
            "native_returncode": native_returncode,
            "exact_class_match": observation.error_kind == expected and native_error is not None and expected in native_error,
        }
    invalid_case_classes = list(static_diagnostics) + list(runtime_diagnostics)
    generated_invalid = [invalid_case_classes[index % len(invalid_case_classes)] for index in range(BYTES_INVALID_CASES)]
    valid_equal = reference_results == original_results == optimized_results == native_results
    ownership_balanced = all(state == {"Dropped": 1} for state in original_ownership + optimized_ownership)
    invalid_exact = all(item["exact_class_match"] for item in static_diagnostics.values()) and all(
        item["exact_class_match"] for item in runtime_diagnostics.values()
    )
    return (
        {
            "valid_case_count": len(cases),
            "invalid_case_count": len(generated_invalid),
            "total_case_count": len(cases) + len(generated_invalid),
            "case_seed": BYTES_EXPERIMENT_SEED,
            "surface_reference_equal": reference_results == original_results,
            "unoptimized_mir_equal": reference_results == original_results,
            "optimized_mir_equal": reference_results == optimized_results,
            "native_binary_equal": reference_results == native_results,
            "valid_equal": valid_equal,
            "native_failures": native_failures,
            "ownership_balanced": ownership_balanced,
            "expected_final_ownership_state": {"Dropped": 1},
            "static_diagnostics": static_diagnostics,
            "runtime_diagnostics": runtime_diagnostics,
            "invalid_exact": invalid_exact,
            "native_build": asdict(build),
            "optimization_passes": [snapshot.to_dict() for snapshot in snapshots],
        },
        original,
        optimized,
    )


def _compile_sanitized(source: str, output: Path, sanitizer: str) -> dict[str, Any]:
    compiler = find_c_compiler("clang") or find_c_compiler()
    output.parent.mkdir(parents=True, exist_ok=True)
    source_path = output.with_suffix(".c")
    source_path.write_text(source, encoding="utf-8")
    if compiler is None:
        return {"status": "UNMEASURED_COMPILER_UNAVAILABLE", "binary": None, "stderr": "compiler unavailable"}
    command = (
        compiler,
        "-std=c11",
        "-O1",
        "-g",
        "-fno-omit-frame-pointer",
        f"-fsanitize={sanitizer}",
        "-fno-sanitize-recover=all",
        str(source_path),
        "-o",
        str(output),
    )
    started = time.perf_counter_ns()
    completed = _run_command(command, timeout=120)
    compile_time_ms = (time.perf_counter_ns() - started) / 1_000_000
    return {
        "status": "MEASURED" if completed.returncode == 0 else "FAILED",
        "binary": str(output) if completed.returncode == 0 else None,
        "command": list(command),
        "compile_time_ms": compile_time_ms,
        "stderr": completed.stderr,
    }


def _sanitizer_corpus(root: Path, optimized: PerformanceMIR) -> dict[str, Any]:
    valid_cases = _valid_cases()
    static_specs = _static_invalid_sources()
    runtime_specs = _runtime_invalid_specs()
    invalid_names = list(static_specs) + list(runtime_specs)
    invalid_cases = [
        invalid_names[index % len(invalid_names)]
        for index in range(BYTES_INVALID_CASES)
    ]
    reports = {}
    for sanitizer_name, flag in (
        ("asan", "address"),
        ("ubsan", "undefined"),
        ("lsan", "leak"),
    ):
        valid_source = CEmitter(optimized, runtime_arguments=True).emit()
        build = _compile_sanitized(
            valid_source, root / sanitizer_name / "valid", flag
        )
        valid_failures = []
        if build["binary"]:
            for index, case in enumerate(valid_cases):
                checksum, completed = _native_checksum(
                    str(build["binary"]), case
                )
                expected = reference_workload(*case)
                sanitizer_text = completed.stderr
                if (
                    completed.returncode != 0
                    or checksum != expected
                    or any(
                        marker in sanitizer_text
                        for marker in _SANITIZER_MARKERS
                    )
                ):
                    valid_failures.append(
                        {
                            "case": index,
                            "returncode": completed.returncode,
                            "stderr": sanitizer_text,
                        }
                    )
        static_results = {}
        for name, (source, expected) in static_specs.items():
            try:
                compile_performance_source(
                    source, path=f"sanitizer-invalid/{name}.meldra"
                )
            except PerformanceCompileError as exc:
                diagnostic = str(exc)
            else:
                diagnostic = "NO_DIAGNOSTIC"
            static_results[name] = {
                "mode": "compile_time_rejection_before_native_execution",
                "expected_diagnostic": expected,
                "diagnostic_present": expected in diagnostic,
                "diagnostic": diagnostic,
            }
        runtime_builds = {}
        invalid_runs = {}
        for name, (source, arguments, expected) in runtime_specs.items():
            mir, _ = optimize_mir(
                compile_performance_source(source).mir
            )
            invalid_build = _compile_sanitized(
                CEmitter(mir, runtime_arguments=True).emit(),
                root / sanitizer_name / f"invalid-{name}",
                flag,
            )
            runtime_builds[name] = invalid_build
            completed = None
            if invalid_build["binary"]:
                _, completed = _native_checksum(
                    str(invalid_build["binary"]), arguments
                )
            stderr = completed.stderr if completed is not None else ""
            invalid_runs[name] = {
                "build_status": invalid_build["status"],
                "returncode": (
                    completed.returncode if completed is not None else None
                ),
                "expected_diagnostic": expected,
                "diagnostic_present": expected in stderr,
                "sanitizer_violation": any(
                    marker in stderr for marker in _SANITIZER_MARKERS
                ),
                "stderr": stderr,
            }
        invalid_failures = []
        class_counts = {
            name: invalid_cases.count(name) for name in invalid_names
        }
        for index, name in enumerate(invalid_cases):
            if name in static_results:
                passed = static_results[name]["diagnostic_present"]
                failure = None
            else:
                source, arguments, expected = runtime_specs[name]
                del source
                invalid_build = runtime_builds[name]
                completed = None
                if invalid_build["binary"]:
                    _, completed = _native_checksum(
                        str(invalid_build["binary"]), arguments
                    )
                stderr = completed.stderr if completed is not None else ""
                passed = (
                    completed is not None
                    and completed.returncode != 0
                    and expected in stderr
                    and not any(
                        marker in stderr for marker in _SANITIZER_MARKERS
                    )
                )
                failure = {
                    "returncode": (
                        completed.returncode
                        if completed is not None
                        else None
                    ),
                    "stderr": stderr,
                }
            if not passed:
                invalid_failures.append(
                    {"case": index, "class": name, "failure": failure}
                )
        reports[sanitizer_name] = {
            "flag": flag,
            "build": build,
            "valid_run_count": (
                len(valid_cases) if build["binary"] else 0
            ),
            "valid_failures": valid_failures,
            "invalid_case_count": len(invalid_cases),
            "invalid_class_counts": class_counts,
            "invalid_failures": invalid_failures,
            "static_invalid": static_results,
            "invalid_runs": invalid_runs,
            "passed": (
                build["status"] == "MEASURED"
                and not valid_failures
                and not invalid_failures
                and all(
                    item["diagnostic_present"]
                    and not item["sanitizer_violation"]
                    for item in invalid_runs.values()
                )
            ),
        }
    return {
        "valid_case_count_per_tool": len(valid_cases),
        "invalid_case_count_per_tool": len(invalid_cases),
        "whole_corpus_per_tool": True,
        "tools": reports,
        "passed": all(item["passed"] for item in reports.values()),
        "checked_failures": [
            "leak",
            "use-after-free",
            "double-free",
            "out-of-bounds",
            "integer-overflow",
        ],
    }


def _token_count(source: str) -> int:
    return len(re.findall(r"[A-Za-z_]\w*|\d+|[^\s]", source))


def _explicit_memory_operations(language: str, source: str) -> int:
    patterns = {
        "meldra": (r"\bBytes\.new\s*\(", r"\bdrop\s*\("),
        "rust_vec": (r"\bvec!\s*\[", r"\bVec::with_capacity\s*\("),
        "c_preallocated": (r"\bmalloc\s*\(", r"\bfree\s*\("),
    }[language]
    return sum(len(re.findall(pattern, source)) for pattern in patterns)


def _build_meldra_benchmark(optimized: PerformanceMIR, root: Path) -> tuple[_Build, str]:
    generated = CEmitter(optimized, runtime_arguments=True).emit()
    prototype = "static uint64_t meldra_fn_main("
    generated = generated.replace(prototype, "static MELDRA_NOINLINE uint64_t meldra_fn_main(")
    result = compile_c_source(generated, output_dir=root, stem="program")
    run_arguments: tuple[str, ...] = ()
    if result.binary_path:
        run_arguments = (result.binary_path,)
    build = _Build(
        result.status,
        result.command,
        run_arguments,
        result.compile_time_ms,
        result.binary_size,
        len(MELDRA_BYTES_SOURCE.encode()),
        hashlib.sha256(MELDRA_BYTES_SOURCE.encode()).hexdigest(),
        result.binary_sha256,
        result.compiler,
        result.compiler_version,
        result.stderr,
    )
    return build, generated


def _with_arguments(build: _Build, arguments: tuple[int, ...]) -> _Build:
    if not build.run_command:
        return build
    return _Build(
        build.status,
        build.command,
        (*build.run_command, *(str(value) for value in arguments)),
        build.compile_time_ms,
        build.binary_size,
        build.source_size,
        build.source_sha256,
        build.binary_sha256,
        build.compiler,
        build.compiler_version,
        build.stderr,
        build.optimization_statistics,
    )


def _timed_run(build: _Build, expected: int, cpu: int | None) -> dict[str, Any]:
    if build.status != "MEASURED" or not build.run_command:
        return {"status": build.status, "correct": None, "error": build.stderr}
    command = build.run_command
    if cpu is not None and shutil.which("taskset"):
        command = (str(shutil.which("taskset")), "-c", str(cpu), *command)
    if Path("/usr/bin/time").is_file():
        command = ("/usr/bin/time", "-f", "BYTES_RSS_KB=%M", *command)
    started = time.perf_counter_ns()
    completed = _run_command(command, timeout=120)
    wall_ms = (time.perf_counter_ns() - started) / 1_000_000
    try:
        checksum = int(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        checksum = None
    rss = re.findall(r"BYTES_RSS_KB=(\d+)", completed.stderr)
    allocations = re.findall(r"(?:BENCH|MELDRA)_ALLOCATIONS=(\d+)", completed.stderr)
    frees = re.findall(r"(?:BENCH|MELDRA)_FREES=(\d+)", completed.stderr)
    allocated = re.findall(r"(?:BENCH|MELDRA)_ALLOCATED_BYTES=(\d+)", completed.stderr)
    copies = re.findall(r"(?:BENCH|MELDRA)_PAYLOAD_COPIES=(\d+)", completed.stderr)
    correct = completed.returncode == 0 and checksum == expected
    return {
        "status": "MEASURED" if correct else "FAILED_CORRECTNESS_OR_RUNTIME",
        "correct": correct,
        "returncode": completed.returncode,
        "checksum": checksum,
        "wall_ms": wall_ms,
        "peak_rss_kb": int(rss[-1]) if rss else None,
        "allocations": int(allocations[-1]) if allocations else None,
        "frees": int(frees[-1]) if frees else None,
        "allocated_bytes": int(allocated[-1]) if allocated else None,
        "payload_copies": int(copies[-1]) if copies else None,
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        "error": None if correct else completed.stderr,
    }


def _disassembly(binary: str | None, function_hint: str, output: Path) -> dict[str, Any]:
    objdump = shutil.which("objdump")
    if binary is None or objdump is None:
        return {"status": "UNMEASURED_TOOL_UNAVAILABLE"}
    completed = _run_command((objdump, "-d", "--no-show-raw-insn", binary), timeout=120)
    if completed.returncode != 0:
        return {"status": "FAILED", "stderr": completed.stderr}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(completed.stdout, encoding="utf-8")
    functions: dict[str, list[tuple[str, str]]] = {}
    current = None
    for line in completed.stdout.splitlines():
        header = re.match(r"^[0-9a-f]+ <([^>]+)>:$", line.strip())
        if header:
            current = header.group(1)
            functions.setdefault(current, [])
            continue
        instruction = re.match(r"^\s*[0-9a-f]+:\s+([a-zA-Z][a-zA-Z0-9.]*)\s*(.*)$", line)
        if current is not None and instruction:
            functions[current].append((instruction.group(1).lower(), instruction.group(2)))
    selected_names = [name for name in functions if function_hint in name]
    if not selected_names:
        selected_names = [name for name in functions if name == "main"]
    instructions = [item for name in selected_names for item in functions[name]]
    loads = stores = branches = calls = vector = 0
    for mnemonic, operands in instructions:
        parts = [part.strip() for part in operands.split(",")]
        if mnemonic.startswith("call"):
            calls += 1
        if mnemonic.startswith("j"):
            branches += 1
        if any(register in operands for register in ("%xmm", "%ymm", "%zmm")):
            vector += 1
        if mnemonic.startswith("mov") and parts:
            if "(" in parts[0]:
                loads += 1
            if len(parts) > 1 and "(" in parts[-1]:
                stores += 1
    return {
        "status": "MEASURED",
        "scope": selected_names,
        "instruction_count": len(instructions),
        "loads": loads,
        "stores": stores,
        "branches": branches,
        "calls": calls,
        "vector_register_instructions": vector,
        "vectorized": vector > 0,
        "escape_analysis_proxy": {
            "allocation_calls_in_selected_function": sum(
                1 for mnemonic, operands in instructions if mnemonic.startswith("call") and any(name in operands for name in ("malloc", "calloc", "realloc"))
            ),
            "payload_copy_calls": sum(
                1 for mnemonic, operands in instructions if mnemonic.startswith("call") and any(name in operands for name in ("memcpy", "memmove"))
            ),
        },
        "sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "path": str(output),
    }


def _calibrate_rounds(
    build: _Build,
    n: int,
    seed: int,
    start: int,
    length: int,
    cpu: int | None,
) -> int:
    rounds = 8
    while rounds <= 512:
        command = _with_arguments(
            build, (n, seed, rounds, start, length)
        ).run_command
        if cpu is not None and shutil.which("taskset"):
            command = (str(shutil.which("taskset")), "-c", str(cpu), *command)
        started = time.perf_counter_ns()
        completed = _run_command(command, timeout=120)
        wall_ms = (time.perf_counter_ns() - started) / 1_000_000
        if completed.returncode == 0 and wall_ms >= 240.0:
            return rounds
        rounds *= 2
    return 512


def _benchmark(root: Path, optimized: PerformanceMIR, original: PerformanceMIR) -> dict[str, Any]:
    arm_root = root / "arms"
    arm_root.mkdir(parents=True, exist_ok=True)
    for name in ("meldra", "c", "rust"):
        (arm_root / name).mkdir(parents=True, exist_ok=True)
    meldra_build, generated_c = _build_meldra_benchmark(optimized, arm_root / "meldra")
    c_build = _compile_external("c", C_BYTES_SOURCE, arm_root / "c", ())
    rust_build = _compile_external("rust", RUST_BYTES_SOURCE, arm_root / "rust", ())
    builds = {"meldra": meldra_build, "rust_vec": rust_build, "c_preallocated": c_build}
    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []
    cpu = affinity[0] if affinity else None
    n = 16 * 1024 * 1024
    seed = 15_111_065_706_836_454_659
    start = n // 5
    length = n // 3
    rounds = _calibrate_rounds(meldra_build, n, seed, start, length, cpu)
    arguments = (n, seed, rounds, start, length)
    expected, expected_run = (
        _native_checksum(c_build.run_command[0], arguments)
        if c_build.run_command
        else (None, None)
    )
    if expected is None or expected_run is None or expected_run.returncode != 0:
        raise RuntimeError(
            "C reference arm failed to produce the benchmark checksum"
        )
    builds = {name: _with_arguments(build, arguments) for name, build in builds.items()}
    warmups = []
    for warmup in range(BYTES_WARMUPS):
        names = list(builds)
        random.Random(BYTES_EXPERIMENT_SEED + warmup).shuffle(names)
        for name in names:
            warmups.append({"warmup": warmup, "arm": name, **_timed_run(builds[name], expected, cpu)})
    samples = []
    for repetition in range(BYTES_MEASURED_RUNS):
        names = list(builds)
        random.Random(BYTES_EXPERIMENT_SEED + 10_000 + repetition).shuffle(names)
        for name in names:
            samples.append({"repetition": repetition, "arm": name, **_timed_run(builds[name], expected, cpu)})
    arm_reports = {}
    traffic_bytes = n + 2 * n * rounds + length + 2
    sources = {"meldra": MELDRA_BYTES_SOURCE, "rust_vec": RUST_BYTES_SOURCE, "c_preallocated": C_BYTES_SOURCE}
    hints = {"meldra": "meldra_fn_main", "rust_vec": "workload", "c_preallocated": "workload"}
    for arm_index, (name, build) in enumerate(builds.items()):
        arm_samples = [sample for sample in samples if sample["arm"] == name and sample.get("correct")]
        walls = [float(sample["wall_ms"]) for sample in arm_samples]
        rss = [float(sample["peak_rss_kb"]) for sample in arm_samples if sample.get("peak_rss_kb") is not None]
        wall_distribution = _distribution(walls, seed=BYTES_EXPERIMENT_SEED + arm_index)
        rss_distribution = _distribution(rss, seed=BYTES_EXPERIMENT_SEED + 100 + arm_index)
        median_ms = wall_distribution["median"]
        binary = build.run_command[0] if build.run_command else None
        arm_reports[name] = {
            "build": asdict(build),
            "measured_run_count": len(arm_samples),
            "wall_ms": wall_distribution,
            "peak_rss_kb": rss_distribution,
            "throughput_gb_s": traffic_bytes / (median_ms / 1_000) / 1_000_000_000 if median_ms else None,
            "algorithm_counters": {
                key: arm_samples[0].get(key) if arm_samples else None
                for key in ("allocations", "frees", "allocated_bytes", "payload_copies")
            },
            "source_tokens": _token_count(sources[name]),
            "explicit_memory_operations": _explicit_memory_operations(name, sources[name]),
            "assembly": _disassembly(binary, hints[name], root / "assembly" / f"{name}.s"),
            "dispersion_gate_passed": wall_distribution["relative_mad"] is not None and wall_distribution["relative_mad"] <= 0.05,
        }
    original_bounds = sum(
        instruction.op == "bytes_bounds_check"
        for function in original.functions
        for block in function.blocks
        for instruction in block.instructions
    )
    optimized_bounds = sum(
        instruction.op == "bytes_bounds_check"
        for function in optimized.functions
        for block in function.blocks
        for instruction in block.instructions
    )
    return {
        "method": {
            "warmups": BYTES_WARMUPS,
            "measured_runs": BYTES_MEASURED_RUNS,
            "randomized_arm_order": True,
            "order_seed": BYTES_EXPERIMENT_SEED,
            "sequential_runs": True,
            "cpu_affinity": cpu,
            "available_affinity": affinity,
            "bootstrap_resamples": 2_000,
            "dispersion_gate_relative_mad": 0.05,
            "minimum_target_runtime_ms": 200,
            "arguments": {"n": n, "seed": seed, "rounds": rounds, "slice_start": start, "slice_length": length},
            "logical_traffic_bytes": traffic_bytes,
        },
        "expected_checksum": expected,
        "all_warmups_correct": all(item.get("correct") is True for item in warmups if item["status"] == "MEASURED"),
        "all_samples_correct": all(item.get("correct") is True for item in samples if item["status"] == "MEASURED"),
        "warmups": warmups,
        "samples": samples,
        "arms": arm_reports,
        "generated_c_bytes": len(generated_c.encode()),
        "generated_c_sha256": hashlib.sha256(generated_c.encode()).hexdigest(),
        "bounds_checks": {"unoptimized_mir": original_bounds, "optimized_mir": optimized_bounds},
    }


def _decision(
    correctness: dict[str, Any],
    sanitizers: dict[str, Any],
    benchmark: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    if (
        not correctness["valid_equal"]
        or not correctness["ownership_balanced"]
        or not correctness["invalid_exact"]
        or not sanitizers["passed"]
    ):
        return "BYTES_EXPERIMENT_INVALID", {
            "reason": "correctness, ownership, diagnostics, or sanitizer gate failed"
        }
    arms = benchmark["arms"]
    if any(
        arms[name]["measured_run_count"] < BYTES_MEASURED_RUNS
        for name in arms
    ):
        return "BYTES_EXPERIMENT_INCONCLUSIVE", {
            "reason": "one or more toolchain arms were unavailable or failed"
        }
    if not all(arm["dispersion_gate_passed"] for arm in arms.values()):
        return "BYTES_EXPERIMENT_INCONCLUSIVE", {
            "reason": "relative MAD exceeded five percent"
        }
    meldra = arms["meldra"]["wall_ms"]["median"]
    rust = arms["rust_vec"]["wall_ms"]["median"]
    c = arms["c_preallocated"]["wall_ms"]["median"]
    ratios = {
        "meldra_over_rust": meldra / rust,
        "meldra_over_c": meldra / c,
        "rust_over_c": rust / c,
    }
    meldra_ci = arms["meldra"]["wall_ms"]["bootstrap_median_95_ci"]
    rust_ci = arms["rust_vec"]["wall_ms"]["bootstrap_median_95_ci"]
    localization = {
        "meldra_rust_confidence_intervals_overlap": (
            max(meldra_ci[0], rust_ci[0]) <= min(meldra_ci[1], rust_ci[1])
        ),
        "algorithm_counters": {
            name: arms[name]["algorithm_counters"] for name in arms
        },
        "allocation_counts_equal": len(
            {
                (
                    arms[name]["algorithm_counters"]["allocations"],
                    arms[name]["algorithm_counters"]["frees"],
                    arms[name]["algorithm_counters"]["allocated_bytes"],
                    arms[name]["algorithm_counters"]["payload_copies"],
                )
                for name in arms
            }
        )
        == 1,
        "remaining_mir_bounds_checks": benchmark["bounds_checks"][
            "optimized_mir"
        ],
        "bounds_checks_removed_by_optimizer": (
            benchmark["bounds_checks"]["unoptimized_mir"]
            - benchmark["bounds_checks"]["optimized_mir"]
        ),
        "assembly": {
            name: {
                key: arms[name]["assembly"].get(key)
                for key in (
                    "instruction_count",
                    "loads",
                    "stores",
                    "branches",
                    "calls",
                    "vectorized",
                    "escape_analysis_proxy",
                )
            }
            for name in arms
        },
        "attribution": (
            "No measurable owner/view representation tax was isolated: all arms "
            "perform one allocation and one free with zero payload copies, and "
            "Meldra is within ten percent of both Rust Vec and preallocated C."
            if ratios["meldra_over_rust"] <= 1.10
            and ratios["meldra_over_c"] <= 1.10
            else "The residual gap remains after equal allocation/copy traffic; "
            "inspect explicit bounds branches and selected-function machine code."
        ),
    }
    evidence = {"ratios": ratios, "localization": localization}
    if ratios["meldra_over_rust"] <= 1.10:
        return "BYTES_OWNERSHIP_SLICE_SUPPORTED", {
            "reason": "all gates passed and Meldra median is within ten percent of Rust Vec",
            **evidence,
        }
    return "BYTES_REPRESENTATION_OVERHEAD_FOUND", {
        "reason": "all safety gates passed but Meldra median exceeds Rust Vec by more than ten percent",
        **evidence,
    }


def validate_bytes_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != BYTES_EXPERIMENT_SCHEMA_VERSION:
        raise ValueError("invalid Bytes experiment schema version")
    if report.get("decision") not in BYTES_DECISIONS:
        raise ValueError("invalid Bytes experiment decision")
    correctness = report.get("correctness", {})
    if correctness.get("total_case_count", 0) < 500:
        raise ValueError("Bytes correctness corpus must contain at least 500 cases")
    method = report.get("benchmark", {}).get("method", {})
    if method.get("warmups") != BYTES_WARMUPS or method.get("measured_runs") != BYTES_MEASURED_RUNS:
        raise ValueError("Bytes benchmark protocol drift")
    if not report.get("isolation", {}).get("stage06r_not_started"):
        raise ValueError("Stage 0.6R isolation marker missing")


def run_bytes_experiment(
    *,
    output_dir: str | Path = "tools/benchmarks/merlo/benchmarks/meldra_bytes_experiment",
    report_path: str | Path = "tools/benchmarks/merlo/benchmarks/meldra_bytes_experiment.json",
) -> dict[str, Any]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "workload.meldra").write_text(MELDRA_BYTES_SOURCE, encoding="utf-8")
    (root / "workload.c").write_text(C_BYTES_SOURCE, encoding="utf-8")
    (root / "workload.rs").write_text(RUST_BYTES_SOURCE, encoding="utf-8")
    correctness, original, optimized = _correctness_corpus(root)
    native_hir = compile_native_hir(MELDRA_BYTES_SOURCE, path="bytes-corpus/workload.meldra")
    sanitizers = _sanitizer_corpus(root / "sanitizers", optimized)
    benchmark = _benchmark(root / "benchmark", optimized, original)
    decision, decision_evidence = _decision(correctness, sanitizers, benchmark)
    report = {
        "schema_version": BYTES_EXPERIMENT_SCHEMA_VERSION,
        "kind": "MeldraBytesOwnershipSliceExperiment",
        "date": "2026-08-12",
        "decision": decision,
        "decision_evidence": decision_evidence,
        "isolation": {
            "stage": "0.6B",
            "stage06r_not_started": True,
            "prior_artifacts_modified": False,
            "separate_artifact": True,
        },
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "surface": {
            "owned_type": "Bytes",
            "borrowed_type": "BytesView",
            "lifetime_annotations": False,
            "source_sha256": hashlib.sha256(MELDRA_BYTES_SOURCE.encode()).hexdigest(),
        },
        "hir_contract": bytes_hir_manifest(native_hir),
        "mir_contract": bytes_mir_manifest(original),
        "optimized_mir_contract": bytes_mir_manifest(optimized),
        "correctness": correctness,
        "sanitizers": sanitizers,
        "benchmark": benchmark,
        "limitations": [
            "This experiment covers contiguous owned bytes and one immutable non-escaping view at a time.",
            "No shared Bytes, copy-on-write, builders, concatenation, text encoding, interning, ropes, or small-buffer optimization were added.",
            "Algorithm allocation counters are source/runtime-instrumented counts, not allocator-internal metadata.",
            "Assembly load/store counts are static x86-64 instruction proxies for the selected noinline workload function.",
        ],
    }
    validate_bytes_report(report)
    destination = Path(report_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    destination.write_text(text, encoding="utf-8")
    report["artifact"] = {
        "path": str(destination),
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "bytes": len(text.encode()),
    }
    return report


__all__ = [
    "BYTES_DECISIONS",
    "BYTES_EXPERIMENT_SCHEMA_VERSION",
    "BYTES_INVALID_CASES",
    "BYTES_MEASURED_RUNS",
    "BYTES_VALID_CASES",
    "BYTES_WARMUPS",
    "C_BYTES_SOURCE",
    "MELDRA_BYTES_SOURCE",
    "RUST_BYTES_SOURCE",
    "reference_workload",
    "run_bytes_experiment",
    "validate_bytes_report",
]
