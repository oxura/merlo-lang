"""ASan/UBSan validation for generated native code and memory-negative cases."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from .native_c_backend import CEmitter
from .performance_frontend import compile_performance_source
from .performance_opt import optimize_mir


SANITIZER_SCHEMA_VERSION = 2
NEGATIVE_CASE_COUNT = 500

_NEGATIVE_SOURCE = r'''#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>

static volatile uint64_t *meldra_escape;

int main(int argc, char **argv) {
    if (argc != 2) return 64;
    int scenario = atoi(argv[1]) % 5;
    if (scenario == 0) {
        volatile uint64_t *values = malloc(2 * sizeof(uint64_t));
        if (!values) return 70;
        values[0] = 42;
        free((void *)values);
        return (int)values[0];
    }
    if (scenario == 1) {
        volatile uint64_t *values = malloc(sizeof(uint64_t));
        if (!values) return 70;
        meldra_escape = values;
        free((void *)values);
        free((void *)values);
        return 0;
    }
    if (scenario == 2) {
        volatile uint64_t *values = malloc(16 * sizeof(uint64_t));
        if (!values) return 70;
        values[0] = (uint64_t)argc;
        fprintf(stderr, "leak anchor: %" PRIu64 "\n", values[0]);
        return 0;
    }
    if (scenario == 3) {
        volatile uint64_t *values = malloc(sizeof(uint64_t));
        if (!values) return 70;
        values[3] = 42;
        free((void *)values);
        return 0;
    }
    volatile int64_t value = INT64_MAX;
    value += 1;
    return (int)value;
}
'''

_EXPECTED_MARKERS = {
    0: ("heap-use-after-free", "use_after_free"),
    1: ("attempting double-free", "double_free"),
    2: ("detected memory leaks", "leak"),
    3: ("heap-buffer-overflow", "out_of_bounds"),
    4: ("signed integer overflow", "signed_overflow"),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compile(
    compiler: str,
    source: Path,
    binary: Path,
    *,
    defined_arithmetic: bool = True,
) -> dict[str, Any]:
    semantic_flags = (
        (
            "-fwrapv",
            "-fno-delete-null-pointer-checks",
            "-ffp-contract=off",
        )
        if defined_arithmetic
        else ()
    )
    command = (
        compiler,
        "-std=c11",
        "-O1",
        "-g",
        "-fno-omit-frame-pointer",
        "-fsanitize=address,undefined",
        *semantic_flags,
        str(source),
        "-o",
        str(binary),
    )
    started = time.perf_counter_ns()
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return {
        "command": list(command),
        "compile_time_ms": elapsed_ms,
        "source_sha256": _sha256(source),
        "binary_sha256": _sha256(binary),
        "binary_size": binary.stat().st_size,
    }


def _sanitizer_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["ASAN_OPTIONS"] = "detect_leaks=1:halt_on_error=1:abort_on_error=1"
    environment["UBSAN_OPTIONS"] = "halt_on_error=1:abort_on_error=1:print_stacktrace=1"
    return environment


def run_sanitizer_evidence(
    *,
    generated_batches: str | Path,
    artifact_dir: str | Path,
    negative_count: int = NEGATIVE_CASE_COUNT,
) -> dict[str, Any]:
    """Run all generated C batches cleanly, then require 500 negative failures."""

    compiler = shutil.which("clang") or shutil.which("gcc")
    if compiler is None:
        return {
            "schema_version": SANITIZER_SCHEMA_VERSION,
            "kind": "MeldraStage06PSanitizers",
            "status": "UNMEASURED_TOOLCHAIN_UNAVAILABLE",
            "reason": "clang/gcc unavailable",
        }
    root = Path(artifact_dir)
    root.mkdir(parents=True, exist_ok=True)
    environment = _sanitizer_environment()
    version = subprocess.run(
        (compiler, "--version"), capture_output=True, text=True, timeout=10
    ).stdout.splitlines()[0]

    valid = []
    valid_failures = []
    for batch in sorted(Path(generated_batches).glob("batch_*")):
        source = batch / "program.c"
        destination = root / "valid" / batch.name
        destination.mkdir(parents=True, exist_ok=True)
        binary = destination / "program.sanitized"
        build = _compile(compiler, source, binary)
        completed = subprocess.run(
            (str(binary), "0"),
            capture_output=True,
            text=True,
            timeout=180,
            env=environment,
        )
        output_lines = completed.stdout.splitlines()
        stderr_path = destination / "stderr.txt"
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        observation = {
            "batch": batch.name,
            **build,
            "status": "PASS" if completed.returncode == 0 else "FAIL",
            "returncode": completed.returncode,
            "result_lines": len(output_lines),
            "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
            "stderr_artifact": str(stderr_path),
        }
        valid.append(observation)
        if completed.returncode != 0 or len(output_lines) != 500:
            valid_failures.append(observation)

    negative_root = root / "negative"
    negative_root.mkdir(parents=True, exist_ok=True)
    negative_source = negative_root / "negative_memory.c"
    negative_source.write_text(_NEGATIVE_SOURCE, encoding="utf-8")
    negative_binary = negative_root / "negative_memory.sanitized"
    negative_build = _compile(
        compiler,
        negative_source,
        negative_binary,
        defined_arithmetic=False,
    )
    cases = []
    failure_counts: dict[str, int] = {}
    unexpected = []
    for index in range(negative_count):
        scenario = index % len(_EXPECTED_MARKERS)
        marker, category = _EXPECTED_MARKERS[scenario]
        completed = subprocess.run(
            (str(negative_binary), str(index)),
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
        normalized_stderr = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", completed.stderr)
        log_path = negative_root / f"case_{index:04d}.stderr.txt"
        log_path.write_text(completed.stderr, encoding="utf-8")
        detected = completed.returncode != 0 and marker in completed.stderr.lower()
        observation = {
            "id": f"memory-negative-{index:04d}",
            "category": category,
            "status": "DETECTED" if detected else "MISSED",
            "returncode": completed.returncode,
            "expected_marker": marker,
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
            "normalized_stderr_sha256": hashlib.sha256(
                normalized_stderr.encode()
            ).hexdigest(),
            "stderr_artifact": str(log_path),
        }
        cases.append(observation)
        if detected:
            failure_counts[category] = failure_counts.get(category, 0) + 1
        else:
            unexpected.append(observation)
    signed_root = root / "signed_integer_contract"
    signed_root.mkdir(parents=True, exist_ok=True)
    signed_specs = (
        (
            "wrapping_add",
            "fn main(n: Int64) -> Int64:\n    n + 1\n",
            ("9223372036854775807",),
            "-9223372036854775808",
            None,
        ),
        (
            "folded_wrapping_add",
            "fn main() -> Int64:\n    9223372036854775807 + 1\n",
            (),
            "-9223372036854775808",
            None,
        ),
        (
            "minimum_divide_negative_one",
            "fn main(a: Int64, b: Int64) -> Int64:\n    a / b\n",
            ("-9223372036854775808", "-1"),
            "-9223372036854775808",
            None,
        ),
        (
            "masked_signed_shift",
            "fn main(a: Int64, b: Int64) -> Int64:\n    a << b\n",
            ("-1", "65"),
            "-2",
            None,
        ),
        (
            "division_by_zero",
            "fn main(a: Int64, b: Int64) -> Int64:\n    a / b\n",
            ("7", "0"),
            None,
            "Meldra division by zero",
        ),
        (
            "uint64_wrapping",
            "fn main(n: UInt64) -> UInt64:\n    n + 1\n",
            ("18446744073709551615",),
            "0",
            None,
        ),
        (
            "bool_logic",
            "fn main(a: Bool, b: Bool) -> Bool:\n    (a and not b) or (not a and b)\n",
            ("1", "0"),
            "1",
            None,
        ),
        (
            "float32_rounding",
            """fn compute(x: Float32) -> Float32:
    x + 1.0
fn main(x: Float32) -> Bool:
    compute(x) == x
""",
            ("16777216",),
            "1",
            None,
        ),
        (
            "float64_ieee_zero_division",
            """fn compute(x: Float64) -> Float64:
    x / 0.0
fn main(x: Float64) -> Bool:
    compute(x) > 1e300
""",
            ("2",),
            "1",
            None,
        ),
        (
            "float32_overflow",
            """fn compute(x: Float32) -> Float32:
    x * x
fn main(x: Float32) -> Bool:
    compute(x) > 1e30
""",
            ("1e30",),
            "1",
            None,
        ),
    )
    signed_cases = []
    signed_failures = []
    sanitizer_markers = (
        "addresssanitizer",
        "undefinedbehaviorsanitizer",
        "runtime error:",
    )
    for name, meldra_source, arguments, expected_stdout, expected_error in signed_specs:
        mir = compile_performance_source(
            meldra_source,
            path=f"signed-contract/{name}.meldra",
        ).mir
        optimized, _ = optimize_mir(mir)
        c_source = CEmitter(optimized, runtime_arguments=True).emit()
        case_root = signed_root / name
        case_root.mkdir(parents=True, exist_ok=True)
        source_path = case_root / "program.c"
        source_path.write_text(c_source, encoding="utf-8")
        binary_path = case_root / "program.sanitized"
        build = _compile(compiler, source_path, binary_path)
        completed = subprocess.run(
            (str(binary_path), *arguments),
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
        sanitizer_clean = not any(
            marker in completed.stderr.lower() for marker in sanitizer_markers
        )
        if expected_error is None:
            passed = (
                completed.returncode == 0
                and completed.stdout.strip() == expected_stdout
                and sanitizer_clean
            )
        else:
            passed = (
                completed.returncode != 0
                and expected_error in completed.stderr
                and sanitizer_clean
            )
        observation = {
            "name": name,
            "status": "PASS" if passed else "FAIL",
            "arguments": list(arguments),
            "expected_stdout": expected_stdout,
            "expected_error": expected_error,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
            "sanitizer_clean": sanitizer_clean,
            "build": build,
        }
        signed_cases.append(observation)
        if not passed:
            signed_failures.append(observation)
    ownership_specs = (
        (
            "non_inlined_owned_result",
            """fn make_values(i: UInt64) -> Array[UInt64, 2]:
    if i % 2 == 0:
        return [i, i + 1]
    return [i + 2, i + 3]

fn main(n: UInt64) -> UInt64:
    var checksum: UInt64 = 0
    for i in 0..n:
        let values: Array[UInt64, 2] = make_values(i)
        checksum = checksum + values[0]
    checksum
""",
            ("1000",),
            "500500",
        ),
        (
            "dynamic_slice_scope",
            """fn square(value: UInt64) -> UInt64:
    value * value

fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 4] = [1, 2, 3, 4]
    var checksum: UInt64 = 0
    for i in 0..n:
        let mapped: Slice[UInt64] = map(values, square)
        checksum = checksum + mapped[i % 4]
    checksum
""",
            ("1000",),
            "7500",
        ),
        (
            "shared_retain_release_balance",
            """fn main(n: UInt64) -> UInt64:
    var checksum: UInt64 = 0
    for i in 0..n:
        let values: Shared[Array[UInt64, 2]] = [i, 2]
        let alias: Shared[Array[UInt64, 2]] = retain(values)
        checksum = checksum + alias[0] + alias[1]
        release(values)
        release(alias)
    checksum
""",
            ("1000",),
            "501500",
        ),
    )
    ownership_cases = []
    ownership_failures = []
    ownership_root = root / "ownership_contract"
    ownership_root.mkdir(parents=True, exist_ok=True)
    for name, meldra_source, arguments, expected_stdout in ownership_specs:
        mir = compile_performance_source(
            meldra_source,
            path=f"ownership-contract/{name}.meldra",
        ).mir
        optimized, _ = optimize_mir(mir)
        c_source = CEmitter(optimized, runtime_arguments=True).emit()
        case_root = ownership_root / name
        case_root.mkdir(parents=True, exist_ok=True)
        source_path = case_root / "program.c"
        source_path.write_text(c_source, encoding="utf-8")
        binary_path = case_root / "program.sanitized"
        build = _compile(compiler, source_path, binary_path)
        completed = subprocess.run(
            (str(binary_path), *arguments),
            capture_output=True,
            text=True,
            timeout=60,
            env=environment,
        )
        sanitizer_clean = not any(
            marker in completed.stderr.lower()
            for marker in (
                "addresssanitizer",
                "leaksanitizer",
                "undefinedbehaviorsanitizer",
                "runtime error:",
            )
        )
        passed = (
            completed.returncode == 0
            and completed.stdout.strip() == expected_stdout
            and sanitizer_clean
        )
        observation = {
            "name": name,
            "status": "PASS" if passed else "FAIL",
            "arguments": list(arguments),
            "expected_stdout": expected_stdout,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
            "sanitizer_clean": sanitizer_clean,
            "build": build,
        }
        ownership_cases.append(observation)
        if not passed:
            ownership_failures.append(observation)

    return {
        "schema_version": SANITIZER_SCHEMA_VERSION,
        "kind": "MeldraStage06PSanitizers",
        "status": "PASS" if not valid_failures and not unexpected and not signed_failures and not ownership_failures else "FAIL",
        "compiler": compiler,
        "compiler_version": version,
        "sanitizers": ["address", "undefined", "leak"],
        "valid_generated_programs": sum(item["result_lines"] for item in valid),
        "valid_batches": valid,
        "valid_failures": valid_failures,
        "signed_integer_contract_cases": signed_cases,
        "signed_integer_contract_failures": signed_failures,
        "scalar_contract_cases": signed_cases,
        "scalar_contract_failures": signed_failures,
        "ownership_contract_cases": ownership_cases,
        "ownership_contract_failures": ownership_failures,
        "negative_case_count": negative_count,
        "negative_detected": negative_count - len(unexpected),
        "negative_failure_counts": dict(sorted(failure_counts.items())),
        "negative_build": negative_build,
        "negative_cases": cases,
        "unexpected_negative_cases": unexpected,
    }


__all__ = [
    "NEGATIVE_CASE_COUNT",
    "SANITIZER_SCHEMA_VERSION",
    "run_sanitizer_evidence",
]
