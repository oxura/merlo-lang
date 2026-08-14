"""Long-lived ASan/LSan ownership soak for Stage 0.6P."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from research.archive.alpha1.merlo.native_bench import WORKLOADS, _meldra_source
from .native_c_backend import CEmitter
from .performance_frontend import compile_performance_source
from .performance_opt import (
    bounds_check_elimination,
    collection_fusion,
    constant_folding,
    dead_code_elimination,
    memory_model_lowering,
    monomorphization,
    optimize_mir,
)


LEAK_SOAK_SCHEMA_VERSION = 2
DEFAULT_DURATION_SECONDS = 7_200


def _rss_kb(pid: int) -> int | None:
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"(?m)^VmRSS:\s+(\d+)\s+kB$", status)
    return int(match.group(1)) if match else None


def _build_source() -> tuple[str, str]:
    workload = next(item for item in WORKLOADS if item.id == "shared_allocations")
    frontend = compile_performance_source(
        _meldra_source(workload),
        path="soak/shared_refcount.meldra",
    )
    passes = (
        monomorphization,
        collection_fusion,
        constant_folding,
        bounds_check_elimination,
        memory_model_lowering,
        dead_code_elimination,
    )
    optimized, _ = optimize_mir(frontend.mir, passes=passes)
    generated = CEmitter(optimized, executable=False).emit()
    harness = r'''
#include <time.h>
static double meldra_elapsed(struct timespec start, struct timespec end) {
    return (double)(end.tv_sec - start.tv_sec) + (double)(end.tv_nsec - start.tv_nsec) / 1000000000.0;
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    const double duration = strtod(argv[1], NULL);
    struct timespec start, now;
    if (clock_gettime(CLOCK_MONOTONIC, &start) != 0) return 3;
    uint64_t checksum = 0, rounds = 0;
    do {
        checksum ^= meldra_fn_main(UINT64_C(500000));
        ++rounds;
        if ((rounds & UINT64_C(255)) == 0) fflush(stderr);
        if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 4;
    } while (meldra_elapsed(start, now) < duration);
    fprintf(stderr, "SOAK_ROUNDS=%" PRIu64 " MELDRA_ALLOCATIONS=%" PRIu64 "\n", rounds, meldra_heap_allocations);
    printf("%" PRIu64 "\n", checksum);
    return 0;
}
'''
    return generated + harness, optimized.digest


def run_leak_soak(
    *,
    output_dir: str | Path = "tools/benchmarks/merlo/benchmarks/stage06p_leak_soak",
    duration_seconds: int = DEFAULT_DURATION_SECONDS,
    sample_interval_seconds: float = 5.0,
) -> dict[str, Any]:
    if duration_seconds < 1:
        raise ValueError("duration must be positive")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    source, mir_digest = _build_source()
    source_path = root / "soak.c"
    binary = root / "soak.sanitized"
    source_path.write_text(source, encoding="utf-8")
    compiler = shutil.which("clang") or shutil.which("gcc")
    if compiler is None:
        report = {
            "schema_version": LEAK_SOAK_SCHEMA_VERSION,
            "kind": "MeldraStage06PLeakSoak",
            "status": "UNMEASURED_TOOLCHAIN_UNAVAILABLE",
        }
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
    command = (
        compiler,
        "-std=c11",
        "-O1",
        "-g",
        "-D_POSIX_C_SOURCE=200809L",
        "-fno-omit-frame-pointer",
        "-fsanitize=address,undefined",
        "-fwrapv",
        "-fno-delete-null-pointer-checks",
        "-ffp-contract=off",
        str(source_path),
        "-o",
        str(binary),
    )
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if compiled.returncode != 0:
        raise RuntimeError(compiled.stderr or compiled.stdout)
    environment = dict(os.environ)
    environment["ASAN_OPTIONS"] = "detect_leaks=1:halt_on_error=1:abort_on_error=1"
    environment["UBSAN_OPTIONS"] = "halt_on_error=1:abort_on_error=1:print_stacktrace=1"
    started = time.time()
    process = subprocess.Popen(
        (str(binary), str(duration_seconds)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    print(f"SOAK_STARTED pid={process.pid} duration={duration_seconds}", flush=True)
    rss_samples = []
    while process.poll() is None:
        rss_samples.append(
            {
                "elapsed_seconds": time.time() - started,
                "rss_kb": _rss_kb(process.pid),
            }
        )
        time.sleep(sample_interval_seconds)
    stdout, stderr = process.communicate()
    elapsed_seconds = time.time() - started
    (root / "stdout.txt").write_text(stdout, encoding="utf-8")
    (root / "stderr.txt").write_text(stderr, encoding="utf-8")
    sanitizer_failure = any(
        marker in stderr.lower()
        for marker in (
            "addresssanitizer",
            "leaksanitizer",
            "runtime error:",
            "heap-use-after-free",
            "double-free",
        )
    )
    valid_rss = [item["rss_kb"] for item in rss_samples if item["rss_kb"] is not None]
    quarter = max(1, len(valid_rss) // 4)
    early_peak = max(valid_rss[:quarter]) if valid_rss else None
    late_peak = max(valid_rss[-quarter:]) if valid_rss else None
    rounds_match = re.search(r"SOAK_ROUNDS=(\d+)", stderr)
    allocations_match = re.search(r"MELDRA_ALLOCATIONS=(\d+)", stderr)
    report = {
        "schema_version": LEAK_SOAK_SCHEMA_VERSION,
        "kind": "MeldraStage06PLeakSoak",
        "status": (
            "PASS"
            if process.returncode == 0
            and not sanitizer_failure
            and elapsed_seconds >= duration_seconds
            else "FAIL"
        ),
        "duration_requested_seconds": duration_seconds,
        "duration_observed_seconds": elapsed_seconds,
        "sample_interval_seconds": sample_interval_seconds,
        "returncode": process.returncode,
        "sanitizer_failure": sanitizer_failure,
        "compiler": compiler,
        "compile_command": list(command),
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "mir_digest": mir_digest,
        "rounds": int(rounds_match.group(1)) if rounds_match else None,
        "allocations": int(allocations_match.group(1)) if allocations_match else None,
        "rss_samples": rss_samples,
        "rss_early_quarter_peak_kb": early_peak,
        "rss_late_quarter_peak_kb": late_peak,
        "rss_late_over_early": (
            late_peak / early_peak if early_peak and late_peak is not None else None
        ),
        "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
    }
    (root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"SOAK_FINISHED status={report['status']} rounds={report['rounds']}", flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--output-dir", default="tools/benchmarks/merlo/benchmarks/stage06p_leak_soak")
    args = parser.parse_args()
    result = run_leak_soak(
        output_dir=args.output_dir,
        duration_seconds=args.duration,
    )
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()


__all__ = ["DEFAULT_DURATION_SECONDS", "LEAK_SOAK_SCHEMA_VERSION", "run_leak_soak"]
