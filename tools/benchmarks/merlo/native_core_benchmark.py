from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "merlo.native-core-benchmark.v1"
RUST_IMAGE = "rust:1.88-slim"
WORKLOAD_NAMES = (
    "arithmetic_lcg",
    "bubble_sort_8",
    "fixed_array_scan",
    "implicit_tree",
    "map_filter_fold",
    "record_values",
    "shared_allocations",
)
ARMS = ("merlo", "c", "rust")
WARMUPS = 3
MEASUREMENTS = 15
MAXIMUM_RELATIVE_MAD = 0.08


@dataclass(frozen=True)
class CoreWorkload:
    name: str
    input: int
    expected: int
    sources: dict[str, Path]
    source_sha256: dict[str, str]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_workloads(root: str | Path) -> tuple[CoreWorkload, ...]:
    base = Path(root).resolve()
    benchmark_root = (
        base
        / "tools"
        / "benchmarks"
        / "merlo"
        / "benchmarks"
    )
    freeze = json.loads(
        (benchmark_root / "meldra_stage05p_freeze_v2.json").read_text(
            encoding="utf-8"
        )
    )["benchmark"]["inputs"]
    corpus = benchmark_root / "stage05p_runs" / "corpus"
    workloads: list[CoreWorkload] = []
    for name in WORKLOAD_NAMES:
        item = freeze[name]
        sources = {
            "merlo": corpus / name / "meldra" / "program.c",
            "c": corpus / name / "c" / "program.c",
            "rust": corpus / name / "rust" / "main.rs",
        }
        hashes = {arm: _sha256(path) for arm, path in sources.items()}
        for arm in ("c", "rust"):
            if hashes[arm] != item["arm_source_sha256"][arm]:
                raise RuntimeError(f"frozen source mismatch: {name}/{arm}")
        workloads.append(
            CoreWorkload(
                name,
                int(item["input"]),
                int(item["expected_checksum"]),
                sources,
                hashes,
            )
        )
    return tuple(workloads)


def _run(command: list[str], value: int, cpu: int) -> tuple[int, int]:
    invocation = [*command, str(value)]
    taskset = shutil.which("taskset")
    if taskset is not None:
        invocation = [taskset, "-c", str(cpu), *invocation]
    started = time.perf_counter_ns()
    completed = subprocess.run(
        invocation,
        capture_output=True,
        check=False,
        timeout=120,
    )
    elapsed = time.perf_counter_ns() - started
    if completed.returncode != 0:
        raise RuntimeError(
            f"benchmark command failed: {completed.stderr.decode(errors='replace')}"
        )
    try:
        result = int(completed.stdout.splitlines()[0])
    except (IndexError, ValueError) as exc:
        raise RuntimeError("benchmark command returned an invalid checksum") from exc
    return elapsed, result


def _compile(
    root: Path,
    workloads: tuple[CoreWorkload, ...],
    output: Path,
) -> tuple[dict[tuple[str, str], list[str]], dict[str, Any]]:
    clang = shutil.which("clang")
    docker = shutil.which("docker")
    if clang is None or docker is None:
        raise RuntimeError("clang and docker are required")
    inspected = subprocess.run(
        [docker, "image", "inspect", RUST_IMAGE, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if inspected.returncode != 0:
        raise RuntimeError(f"required cached image is unavailable: {RUST_IMAGE}")
    commands: dict[tuple[str, str], list[str]] = {}
    c_flags = (
        "-std=c11",
        "-O3",
        "-march=x86-64-v3",
        "-fwrapv",
        "-fno-ident",
        "-Werror",
        "-Wl,--build-id=none",
    )
    for workload in workloads:
        for arm in ("merlo", "c"):
            binary = output / f"{workload.name}-{arm}"
            completed = subprocess.run(
                [clang, *c_flags, str(workload.sources[arm]), "-o", str(binary)],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"compile failed for {workload.name}/{arm}: {completed.stderr}"
                )
            commands[(workload.name, arm)] = [str(binary)]
        rust_binary = output / f"{workload.name}-rust"
        source_dir = workload.sources["rust"].parent
        completed = subprocess.run(
            [
                docker,
                "run",
                "--rm",
                "-v",
                f"{source_dir}:/src:ro",
                "-v",
                f"{output}:/out",
                "-w",
                "/src",
                RUST_IMAGE,
                "rustc",
                "-C",
                "opt-level=3",
                "-C",
                "target-cpu=x86-64-v3",
                "main.rs",
                "-o",
                f"/out/{rust_binary.name}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"compile failed for {workload.name}/rust: {completed.stderr}"
            )
        commands[(workload.name, "rust")] = [str(rust_binary)]
    versions = {}
    for name, command in (
        ("clang", [clang, "--version"]),
        ("rust", [docker, "run", "--rm", RUST_IMAGE, "rustc", "--version"]),
    ):
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        versions[name] = completed.stdout.splitlines()[0]
    return commands, {
        "clang": versions["clang"],
        "c_flags": list(c_flags),
        "rust": versions["rust"],
        "rust_flags": ["-C", "opt-level=3", "-C", "target-cpu=x86-64-v3"],
        "rust_image": RUST_IMAGE,
        "rust_image_id": inspected.stdout.strip(),
    }


def _summary(samples: list[int]) -> dict[str, Any]:
    median = statistics.median(samples)
    mad = statistics.median(abs(value - median) for value in samples)
    return {
        "samples_ns": samples,
        "median_ns": median,
        "mad_ns": mad,
        "relative_mad": mad / median,
    }


def run_native_core_benchmark(root: str | Path = ".") -> dict[str, Any]:
    base = Path(root).resolve()
    workloads = load_workloads(base)
    cpu = min(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else 0
    with tempfile.TemporaryDirectory(prefix="merlo-native-core-") as temporary:
        commands, toolchains = _compile(base, workloads, Path(temporary))
        for workload in workloads:
            for arm in ARMS:
                for _ in range(WARMUPS):
                    _, result = _run(
                        commands[(workload.name, arm)], workload.input, cpu
                    )
                    if result != workload.expected:
                        raise RuntimeError(
                            f"checksum mismatch: {workload.name}/{arm}"
                        )
        schedule = [
            (workload.name, arm)
            for workload in workloads
            for arm in ARMS
            for _ in range(MEASUREMENTS)
        ]
        random.Random(0x4D45524C4F).shuffle(schedule)
        samples = {key: [] for key in commands}
        by_name = {workload.name: workload for workload in workloads}
        for name, arm in schedule:
            workload = by_name[name]
            elapsed, result = _run(commands[(name, arm)], workload.input, cpu)
            if result != workload.expected:
                raise RuntimeError(f"checksum mismatch: {name}/{arm}")
            samples[(name, arm)].append(elapsed)
    observations = []
    ratios = []
    for workload in workloads:
        arms = {
            arm: _summary(samples[(workload.name, arm)])
            for arm in ARMS
        }
        baseline = min(arms["c"]["median_ns"], arms["rust"]["median_ns"])
        ratio = arms["merlo"]["median_ns"] / baseline
        ratios.append(ratio)
        observations.append(
            {
                "name": workload.name,
                "input": workload.input,
                "expected_checksum": workload.expected,
                "source_sha256": workload.source_sha256,
                "arms": arms,
                "merlo_to_best_native_ratio": ratio,
            }
        )
    stable = all(
        item["arms"][arm]["relative_mad"] <= MAXIMUM_RELATIVE_MAD
        for item in observations
        for arm in ARMS
    )
    gates = {
        "all_three_arms_measured": all(
            set(item["arms"]) == set(ARMS) for item in observations
        ),
        "all_checksums_match": True,
        "all_relative_mad_at_most_0_08": stable,
    }
    return {
        "schema": SCHEMA_VERSION,
        "workload_count": len(workloads),
        "arms": list(ARMS),
        "protocol": {
            "warmups": WARMUPS,
            "measurements": MEASUREMENTS,
            "seed": 0x4D45524C4F,
            "cpu": cpu,
            "strictly_sequential": True,
        },
        "toolchains": toolchains,
        "observations": observations,
        "geometric_mean_merlo_to_best_native_ratio": math.prod(ratios)
        ** (1 / len(ratios)),
        "maximum_merlo_to_best_native_ratio": max(ratios),
        "gates": gates,
        "passed": all(gates.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark frozen Merlo native core workloads against C and Rust."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run_native_core_benchmark(arguments.root)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(payload, end="")
    else:
        arguments.output.write_text(payload, encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
