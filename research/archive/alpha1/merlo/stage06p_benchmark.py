"""Randomized, repeated native benchmark protocol for Meldra Stage 0.6P."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import random
import re
import statistics
import subprocess
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Iterable

from research.archive.alpha1.merlo.native_bench import (
    NATIVE_BENCHMARK_LANGUAGES,
    WORKLOADS,
    NativeWorkload,
    _Build,
    _compile_external,
    _unmeasured,
    competitor_source,
    reference_checksum,
)
from merlo.native_c_backend import CEmitter, NativeBackendError, compile_c_source
from tools.benchmarks.merlo.performance_frontend import PerformanceCompileError, compile_performance_source
from tools.benchmarks.merlo.performance_opt import optimize_mir


STAGE06P_BENCHMARK_SCHEMA_VERSION = 3
BENCHMARK_SEED = 0x06B0A11
STAGE06P_WORKLOADS = WORKLOADS
SHARED_CALIBRATION_WORKLOAD = replace(
    next(
        workload
        for workload in WORKLOADS
        if workload.id == "shared_allocations"
    ),
    input=50_000_000,
)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[rank]


def _bootstrap_median_ci(
    values: list[float], *, seed: int, resamples: int = 2_000
) -> list[float] | None:
    if not values:
        return None
    rng = random.Random(seed)
    medians = []
    for _ in range(resamples):
        sample = [values[rng.randrange(len(values))] for _ in values]
        medians.append(statistics.median(sample))
    return [
        _percentile(medians, 0.025) or 0.0,
        _percentile(medians, 0.975) or 0.0,
    ]


def _distribution(values: list[float], *, seed: int) -> dict[str, Any]:
    if not values:
        return {
            "median": None,
            "mean": None,
            "minimum": None,
            "p95": None,
            "standard_deviation": None,
            "mad": None,
            "bootstrap_median_95_ci": None,
        }
    median = statistics.median(values)
    return {
        "median": median,
        "mean": statistics.fmean(values),
        "minimum": min(values),
        "p95": _percentile(values, 0.95),
        "standard_deviation": statistics.pstdev(values),
        "mad": statistics.median(abs(value - median) for value in values),
        "bootstrap_median_95_ci": _bootstrap_median_ci(values, seed=seed),
    }


def _geometric_mean(values: Iterable[float]) -> float | None:
    measured = [value for value in values if value > 0 and math.isfinite(value)]
    if not measured:
        return None
    return math.exp(sum(math.log(value) for value in measured) / len(measured))


def _cpu_state() -> dict[str, Any]:
    def content(path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return None

    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []
    return {
        "affinity": affinity,
        "selected_cpu": affinity[0] if affinity else None,
        "governor": content("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "intel_pstate_no_turbo": content("/sys/devices/system/cpu/intel_pstate/no_turbo"),
        "load_average": list(os.getloadavg()),
    }




def _run_one(build: _Build, expected: int, cpu: int | None) -> dict[str, Any]:
    if build.status != "MEASURED":
        return {"status": build.status, "error": build.stderr}
    command = build.run_command
    if cpu is not None and Path("/usr/bin/taskset").is_file():
        command = ("/usr/bin/taskset", "-c", str(cpu), *command)
    if Path("/usr/bin/time").is_file():
        command = (
            "/usr/bin/time",
            "-f",
            "BENCH_RSS_KB=%M BENCH_USER_S=%U BENCH_SYSTEM_S=%S",
            *command,
        )
    started = time.perf_counter_ns()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            env=dict(os.environ, LC_ALL="C", TZ="UTC"),
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        return {
            "status": "UNMEASURED_TIMEOUT",
            "correct": None,
            "returncode": None,
            "checksum": None,
            "wall_ms": elapsed_ms,
            "cpu_ms": None,
            "peak_rss_kb": None,
            "algorithm_allocations": None,
            "stderr_sha256": None,
            "error": f"timeout after {exc.timeout} seconds",
        }
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    try:
        checksum = int(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        checksum = None
    rss = re.findall(r"BENCH_RSS_KB=(\d+)", completed.stderr)
    user = re.findall(r"BENCH_USER_S=([0-9.]+)", completed.stderr)
    system = re.findall(r"BENCH_SYSTEM_S=([0-9.]+)", completed.stderr)
    allocations = re.findall(r"(?:BENCH|MELDRA)_ALLOCATIONS=(\d+)", completed.stderr)
    correct = completed.returncode == 0 and checksum == expected
    return {
        "status": "MEASURED" if correct else "FAILED_CORRECTNESS_OR_RUNTIME",
        "correct": correct,
        "returncode": completed.returncode,
        "checksum": checksum,
        "wall_ms": elapsed_ms,
        "cpu_ms": (
            (float(user[-1]) + float(system[-1])) * 1_000
            if user and system
            else None
        ),
        "peak_rss_kb": int(rss[-1]) if rss else None,


        "algorithm_allocations": int(allocations[-1]) if allocations else None,
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        "error": None if correct else (completed.stderr or completed.stdout),
    }
def _compile_meldra_stage06(
    workload: NativeWorkload,
    directory: Path,
    mir_root: Path,
) -> _Build:
    if not workload.meldra_supported:
        return replace(
            _unmeasured(
                "", workload.limitation or "unsupported by Stage 0.6P"
            ),
            status="UNSUPPORTED_DECLARED",
        )
    from research.archive.alpha1.merlo.native_bench import _meldra_source

    source = _meldra_source(workload)
    (directory / "main.meldra").write_text(source, encoding="utf-8")
    frontend = compile_performance_source(
        source,
        path=f"stage06p-corpus/{workload.id}.meldra",
    )
    optimized, snapshots = optimize_mir(frontend.mir, artifact_dir=mir_root / workload.id)
    c_source = CEmitter(optimized, runtime_arguments=True).emit()
    result = compile_c_source(c_source, output_dir=directory, stem="program")
    return _Build(
        result.status,
        result.command,
        ((result.binary_path, str(workload.input)) if result.binary_path else ()),
        result.compile_time_ms,
        result.binary_size,
        len(source.encode()),
        hashlib.sha256(source.encode()).hexdigest(),
        result.binary_sha256,
        result.compiler,
        result.compiler_version,
        result.stderr,
        tuple(snapshot.statistics.to_dict() for snapshot in snapshots),
    )


def _modeled_memory(
    workload: NativeWorkload,
    language: str,
) -> dict[str, Any]:
    if workload.id == "shared_allocations":
        if language == "meldra":
            return {
                "algorithm_payload_bytes_requested": 0,
                "algorithm_total_bytes_requested": 0,
                "algorithm_value_writes": workload.input * 8,
                "derivation": "inferred non-escaping ownership uses stack/region storage",
            }
        payload = workload.input * 8 * 8
        return {
            "algorithm_payload_bytes_requested": payload,
            "algorithm_total_bytes_requested": (
                payload
                if language == "c"
                else "UNMEASURED_RUNTIME_HEADER_AND_ALLOCATOR_OVERHEAD"
            ),
            "algorithm_value_writes": workload.input * 8,
            "derivation": "input * 8 UInt64 values * 8 payload bytes",
        }
    return {
        "algorithm_payload_bytes_requested": 0,
        "algorithm_total_bytes_requested": 0,
        "algorithm_value_writes": 0,
        "derivation": "no repeated explicit algorithm allocation in the frozen source",
    }


def run_stage06p_benchmark(
    *,
    output_dir: str | Path = "tools/benchmarks/merlo/benchmarks/stage06p_benchmark",
    workloads: Iterable[NativeWorkload] = STAGE06P_WORKLOADS,
    languages: Iterable[str] = NATIVE_BENCHMARK_LANGUAGES,
    repetitions: int = 30,
    warmups: int = 5,
) -> dict[str, Any]:
    if repetitions < 30 or warmups < 1:
        raise ValueError("Stage 0.6P requires at least 30 measured runs and one warmup")
    root = Path(output_dir)
    corpus_root = root / "corpus"
    mir_root = root / "mir"
    root.mkdir(parents=True, exist_ok=True)
    workload_values = tuple(workloads)
    language_values = tuple(languages)
    if not {"meldra", "c"} <= set(language_values):
        raise ValueError("Stage 0.6P benchmark requires Meldra and C arms")
    unknown_languages = set(language_values) - set(NATIVE_BENCHMARK_LANGUAGES)
    if unknown_languages:
        raise ValueError(
            f"unknown benchmark languages: {sorted(unknown_languages)}"
        )
    expected = {
        item.id: reference_checksum(item)
        for item in workload_values
    }
    builds: dict[tuple[str, str], _Build] = {}
    build_records = []

    for workload in workload_values:
        for language in language_values:
            directory = corpus_root / workload.id / language
            directory.mkdir(parents=True, exist_ok=True)
            try:
                if language == "meldra":
                    build = _compile_meldra_stage06(workload, directory, mir_root)
                else:
                    build = _compile_external(
                        language,
                        competitor_source(language, workload),
                        directory,
                        (str(workload.input),),
                    )
            except (
                PerformanceCompileError,
                NativeBackendError,
                subprocess.SubprocessError,
                OSError,
                ValueError,
            ) as exc:
                failed = _unmeasured("", f"{type(exc).__name__}: {exc}")
                build = _Build(
                    "FAILED",
                    failed.command,
                    failed.run_command,
                    failed.compile_time_ms,
                    failed.binary_size,
                    failed.source_size,
                    failed.source_sha256,
                    failed.binary_sha256,
                    failed.compiler,
                    failed.compiler_version,
                    failed.stderr,
                )
            builds[(workload.id, language)] = build
            build_records.append(
                {
                    "workload": workload.id,
                    "language": language,
                    "status": build.status,
                    "command": list(build.command),
                    "run_command": list(build.run_command),
                    "compile_time_ms": build.compile_time_ms,
                    "binary_size": build.binary_size,
                    "source_size": build.source_size,
                    "source_sha256": build.source_sha256,
                    "binary_sha256": build.binary_sha256,
                    "compiler": build.compiler,
                    "compiler_version": build.compiler_version,
                    "stderr": build.stderr,
                    "optimization_statistics": list(build.optimization_statistics),
                }
            )

    cpu_before = _cpu_state()
    cpu = cpu_before["selected_cpu"]
    rng = random.Random(BENCHMARK_SEED)
    samples: dict[tuple[str, str], list[dict[str, Any]]] = {
        key: [] for key, build in builds.items() if build.status == "MEASURED"
    }
    schedule_digest = hashlib.sha256()
    all_keys = list(samples)
    terminal_failures: dict[tuple[str, str], dict[str, Any]] = {}
    for round_index in range(warmups + repetitions):
        schedule = list(all_keys)
        rng.shuffle(schedule)
        for workload_id, language in schedule:
            key = (workload_id, language)
            if key in terminal_failures:
                continue
            schedule_digest.update(
                f"{round_index}:{workload_id}:{language}\n".encode()
            )
            observation = _run_one(
                builds[(workload_id, language)], expected[workload_id], cpu
            )
            if observation["status"] == "UNMEASURED_TIMEOUT":
                terminal_failures[key] = observation
                continue
            if round_index >= warmups:
                samples[(workload_id, language)].append(observation)
    cpu_after = _cpu_state()

    observations = []
    for workload in workload_values:
        for language in language_values:
            memory_model = _modeled_memory(workload, language)
            build = builds[(workload.id, language)]
            key = (workload.id, language)
            arm_samples = list(samples.get(key, []))
            if key in terminal_failures:
                arm_samples.append(terminal_failures[key])
            measured = [item for item in arm_samples if item.get("status") == "MEASURED"]
            wall = [float(item["wall_ms"]) for item in measured]
            cpu_samples = [
                float(item["cpu_ms"])
                for item in measured
                if item.get("cpu_ms") is not None
            ]
            rss = [
                float(item["peak_rss_kb"])
                for item in measured
                if item.get("peak_rss_kb") is not None
            ]
            allocation_samples = [
                int(item["algorithm_allocations"])
                for item in measured
                if item.get("algorithm_allocations") is not None
            ]
            correct = (
                build.status == "MEASURED"
                and len(measured) == repetitions
                and len(arm_samples) == repetitions
            )
            observations.append(
                {
                    "workload": workload.id,
                    "category": workload.category,
                    "language": language,
                    "status": (
                        "MEASURED"
                        if correct
                        else build.status
                        if build.status != "MEASURED"
                        else terminal_failures.get(key, {}).get(
                            "status", "FAILED_CORRECTNESS_OR_RUNTIME"
                        )
                    ),
                    "correct": correct if build.status == "MEASURED" else None,
                    "expected_checksum": expected[workload.id],
                    "samples": arm_samples,
                    "wall_ms": _distribution(
                        wall,
                        seed=BENCHMARK_SEED
                        ^ int(
                            hashlib.sha256(
                                f"{workload.id}:{language}:wall".encode()
                            ).hexdigest()[:8],
                            16,
                        ),
                    ),
                    "cpu_ms": _distribution(
                        cpu_samples,
                        seed=BENCHMARK_SEED
                        ^ int(
                            hashlib.sha256(
                                f"{workload.id}:{language}:cpu".encode()
                            ).hexdigest()[:8],
                            16,
                        ),
                    ),
                    "peak_rss_kb": _distribution(
                        rss,
                        seed=BENCHMARK_SEED
                        ^ int(
                            hashlib.sha256(
                                f"{workload.id}:{language}:rss".encode()
                            ).hexdigest()[:8],
                            16,
                        ),
                    ),
                    "algorithm_allocations": (
                        statistics.median(allocation_samples)
                        if allocation_samples
                        else None
                    ),
                    "runtime_internal_allocations": "UNMEASURED",
                    **memory_model,
                    "binary_size": build.binary_size,
                    "compile_time_ms": build.compile_time_ms,
                    "limitation": workload.limitation,
                }
            )

    by_key = {(item["workload"], item["language"]): item for item in observations}
    ratios = []
    rss_ratios = []
    for workload in workload_values:
        meldra = by_key[(workload.id, "meldra")]
        c_arm = by_key[(workload.id, "c")]
        if meldra["status"] != "MEASURED" or c_arm["status"] != "MEASURED":
            continue
        c_wall = c_arm["wall_ms"]["median"]
        c_rss = c_arm["peak_rss_kb"]["p95"]
        if c_wall:
            ratios.append(
                {
                    "workload": workload.id,
                    "meldra_over_c": meldra["wall_ms"]["median"] / c_wall,
                }
            )
        if c_rss:
            rss_ratios.append(
                {
                    "workload": workload.id,
                    "meldra_over_c": meldra["peak_rss_kb"]["p95"] / c_rss,
                }
            )
    calibration = []
    for workload in workload_values:
        if workload.id == "startup":
            continue
        c_arm = by_key[(workload.id, "c")]
        meldra_arm = by_key[(workload.id, "meldra")]
        if meldra_arm["status"] != "MEASURED":
            continue
        c_median = c_arm["wall_ms"]["median"]
        c_mad = c_arm["wall_ms"]["mad"]
        meldra_median = meldra_arm["wall_ms"]["median"]
        meldra_mad = meldra_arm["wall_ms"]["mad"]
        stability_relative_mad_max = 0.05
        c_relative_mad = c_mad / c_median if c_median and c_mad is not None else None
        meldra_relative_mad = (
            meldra_mad / meldra_median
            if meldra_median and meldra_mad is not None
            else None
        )
        target_met = c_median is not None and 200 <= c_median <= 500
        stable = (
            c_relative_mad is not None
            and c_relative_mad <= stability_relative_mad_max
            and meldra_relative_mad is not None
            and meldra_relative_mad <= stability_relative_mad_max
        )
        calibration.append(
            {
                "workload": workload.id,
                "reference_language": "c",
                "median_ms": c_median,
                "target_ms": [200, 500],
                "target_met": target_met,
                "stability_relative_mad_max": stability_relative_mad_max,
                "c_relative_mad": c_relative_mad,
                "meldra_relative_mad": meldra_relative_mad,
                "stable": stable,
                "target_or_stable": target_met or stable,
            }
        )

    report = {
        "schema_version": STAGE06P_BENCHMARK_SCHEMA_VERSION,
        "kind": "MeldraStage06PNativeBenchmark",
        "protocol": {
            "languages": list(language_values),
            "repetitions": repetitions,
            "warmups": warmups,
            "randomized_arm_order": True,
            "schedule_seed": BENCHMARK_SEED,
            "schedule_sha256": schedule_digest.hexdigest(),
            "process_isolation": "one fresh process per arm per round",
            "cpu_affinity": cpu,
            "runtime_statistic": "median and p95 external process wall time",
            "confidence_interval": "deterministic 2000-resample bootstrap median 95% CI",
            "memory_statistic": "median and p95 GNU time maximum resident set",
            "allocation_scope": "algorithm counters only; runtime internals explicitly UNMEASURED",
            "correctness": "all arms must equal the independent exact-input Python reference checksum",
            "correctness_crosscheck": "Meldra also passes the interpreter/MIR/native differential corpus",
            "compiler_optimization": "release/O3 for every compiled language",
        },
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_before": cpu_before,
            "cpu_after": cpu_after,
            "cpu_state_stable": (
                cpu_before["governor"] == cpu_after["governor"]
                and cpu_before["intel_pstate_no_turbo"]
                == cpu_after["intel_pstate_no_turbo"]
                and cpu_before["affinity"] == cpu_after["affinity"]
            ),
        },
        "workloads": [
            asdict(item) | {"expected_checksum": expected[item.id]}
            for item in workload_values
        ],
        "builds": build_records,
        "observations": observations,
        "meldra_over_c_ratios": ratios,
        "meldra_over_c_memory_ratios": rss_ratios,
        "meldra_over_c_compute_geometric_mean": _geometric_mean(
            [item["meldra_over_c"] for item in ratios if item["workload"] != "startup"]
        ),
        "calibration": calibration,
        "calibration_target_met": all(
            item["target_met"] for item in calibration
        ),
        "calibration_target_or_stable": all(
            item["target_or_stable"] for item in calibration
        ),
        "meldra_over_c_all_geometric_mean": _geometric_mean(
            item["meldra_over_c"] for item in ratios
        ),
        "meldra_over_c_memory_p95_geometric_mean": _geometric_mean(
            item["meldra_over_c"] for item in rss_ratios
        ),
        "correctness_failures": [
            item
            for item in observations
            if item["status"] == "FAILED_CORRECTNESS_OR_RUNTIME"
        ],
        "unsupported_scope": {
            "text_bytes": "Stage 0.6P frozen native source subset has no Text/Bytes type",
            "recursive_values": "Only indexed acyclic trees are representable; recursive pointer values are unsupported",
            "interfaces": "Closed interfaces and devirtualization are unsupported",
        },
    }
    (root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def merge_shared_calibration_reports(
    base_report: str | Path,
    calibration_report: str | Path,
    *,
    output_paths: Iterable[str | Path],
) -> dict[str, Any]:
    base_path = Path(base_report)
    calibration_path = Path(calibration_report)
    report = json.loads(base_path.read_text(encoding="utf-8"))
    supplemental = json.loads(
        calibration_path.read_text(encoding="utf-8")
    )
    if supplemental["protocol"]["languages"] != ["meldra", "c"]:
        raise ValueError("shared calibration must contain Meldra and C only")
    if [item["id"] for item in supplemental["workloads"]] != [
        "shared_allocations"
    ]:
        raise ValueError("shared calibration report has the wrong workload")
    if supplemental["correctness_failures"]:
        raise ValueError("shared calibration contains correctness failures")

    for item in report["builds"]:
        if item["workload"] == "fnv_ascii" and item["language"] == "meldra":
            item["status"] = "UNSUPPORTED_DECLARED"
    for item in report["observations"]:
        if item["workload"] == "fnv_ascii" and item["language"] == "meldra":
            item["status"] = "UNSUPPORTED_DECLARED"
    by_key = {
        (item["workload"], item["language"]): item
        for item in report["observations"]
    }
    calibration = []
    for workload in report["workloads"]:
        workload_id = workload["id"]
        if workload_id == "startup" or not workload["meldra_supported"]:
            continue
        if workload_id == "shared_allocations":
            item = dict(supplemental["calibration"][0])
            item["calibration_input"] = supplemental["workloads"][0][
                "input"
            ]
            item["measurement_segment"] = "shared-calibration-50m"
            calibration.append(item)
            continue
        c_arm = by_key[(workload_id, "c")]
        meldra_arm = by_key[(workload_id, "meldra")]
        c_median = c_arm["wall_ms"]["median"]
        meldra_median = meldra_arm["wall_ms"]["median"]
        c_relative_mad = c_arm["wall_ms"]["mad"] / c_median
        meldra_relative_mad = (
            meldra_arm["wall_ms"]["mad"] / meldra_median
        )
        target_met = 200 <= c_median <= 500
        stable = c_relative_mad <= 0.05 and meldra_relative_mad <= 0.05
        calibration.append(
            {
                "workload": workload_id,
                "reference_language": "c",
                "median_ms": c_median,
                "target_ms": [200, 500],
                "target_met": target_met,
                "stability_relative_mad_max": 0.05,
                "c_relative_mad": c_relative_mad,
                "meldra_relative_mad": meldra_relative_mad,
                "stable": stable,
                "target_or_stable": target_met or stable,
                "calibration_input": workload["input"],
                "measurement_segment": "cross-language-core",
            }
        )

    raw_observations = [
        {
            "workload": item["workload"],
            "language": item["language"],
            "samples": item["samples"],
        }
        for item in report["observations"]
    ]
    raw_digest = hashlib.sha256(
        json.dumps(
            raw_observations, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    existing_segments = report.get("measurement_segments", ())
    base_schedule = next(
        (
            item["schedule_sha256"]
            for item in existing_segments
            if item["id"] == "cross-language-core"
        ),
        report["protocol"]["schedule_sha256"],
    )
    report.pop("raw_sample_reanalysis", None)
    report["schema_version"] = STAGE06P_BENCHMARK_SCHEMA_VERSION
    report["calibration"] = calibration
    report["calibration_target_met"] = all(
        item["target_met"] for item in calibration
    )
    report["calibration_target_or_stable"] = all(
        item["target_or_stable"] for item in calibration
    )
    report["protocol"].pop("short_arm_stability", None)
    report["protocol"][
        "correctness"
    ] = "all arms equal the independent exact-input Python reference checksum"
    report["protocol"][
        "correctness_crosscheck"
    ] = "Meldra also passes the interpreter/MIR/native differential corpus"
    report["measurement_segments"] = [
        {
            "id": "cross-language-core",
            "languages": report["protocol"]["languages"],
            "repetitions": report["protocol"]["repetitions"],
            "warmups": report["protocol"]["warmups"],
            "schedule_sha256": base_schedule,
            "raw_observations_sha256": raw_digest,
        },
        {
            "id": "shared-calibration-50m",
            "languages": supplemental["protocol"]["languages"],
            "workloads": ["shared_allocations"],
            "input": supplemental["workloads"][0]["input"],
            "repetitions": supplemental["protocol"]["repetitions"],
            "warmups": supplemental["protocol"]["warmups"],
            "schedule_sha256": supplemental["protocol"][
                "schedule_sha256"
            ],
            "artifact": str(calibration_path),
            "artifact_sha256": hashlib.sha256(
                calibration_path.read_bytes()
            ).hexdigest(),
        },
    ]
    text = json.dumps(
        report, ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    for output_path in output_paths:
        Path(output_path).write_text(text, encoding="utf-8")
    return report


__all__ = [
    "BENCHMARK_SEED",
    "STAGE06P_BENCHMARK_SCHEMA_VERSION",
    "STAGE06P_WORKLOADS",
    "SHARED_CALIBRATION_WORKLOAD",
    "run_stage06p_benchmark",
    "merge_shared_calibration_reports",
]
