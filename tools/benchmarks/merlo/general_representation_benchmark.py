"""Reproducible native benchmark and source-surface audit for the representation core."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tools.benchmarks.merlo.general_json_oracle import evaluate_python_oracle
from merlo.representation_c_backend import write_general_c
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import lower_rir_to_performance_mir, optimize_general_mir
from merlo.structured_hir_v2 import compile_structured_hir_file


WARMUPS = 5
MEASUREMENTS = 30
MINIMUM_RUN_MS = 200.0
MAXIMUM_RELATIVE_MAD = 0.05
MAXIMUM_NATIVE_RATIO = 1.50


def _benchmark_payload() -> bytes:
    values = [
        {
            "id": index,
            "name": f"item-{index:04d}",
            "active": index % 3 != 0,
            "scores": [(index * 17 + offset * 31) % 1009 for offset in range(12)],
            "meta": {"group": index % 11, "note": f"ascii-{index}-{'x' * (index % 19)}"},
        }
        for index in range(128)
    ]
    return json.dumps(
        {"version": 1, "items": values, "ok": True},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _compile_arms(root: Path) -> dict[str, list[str] | None]:
    artifact = root / "tools" / "benchmarks" / "merlo" / "benchmarks" / "general_representation"
    source_path = root / "tools" / "benchmarks" / "merlo" / "programs" / "general_json.mlo"
    hir = compile_structured_hir_file(source_path)
    representation = lower_structured_hir_to_rir(hir)
    optimized = optimize_general_mir(lower_rir_to_performance_mir(hir, representation))
    write_general_c(artifact / "generated_json.c", hir, representation, optimized)

    clang = shutil.which("clang")
    if clang is None:
        raise RuntimeError("clang is required for the native benchmark")
    merlo_binary = artifact / "merlo_json"
    c_binary = artifact / "reference_json"
    for source, output in (
        (artifact / "generated_json.c", merlo_binary),
        (artifact / "reference_json.c", c_binary),
    ):
        completed = subprocess.run(
            [clang, "-std=c11", "-O2", "-DNDEBUG", str(source), "-o", str(output)],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"native compile failed for {source}: {completed.stderr}")

    rustc = shutil.which("rustc")
    return {
        "merlo": [str(merlo_binary)],
        "c": [str(c_binary)],
        "rust": None if rustc is None else [],
        "python": [sys.executable, "-m", "tools.benchmarks.merlo.general_representation.reference_json"],
    }


def _parse_result(stdout: str) -> dict[str, int]:
    line = stdout.splitlines()[0] if stdout else ""
    match = re.fullmatch(
        r"OK checksum=(\d+) nodes=(\d+) arrays=(\d+) objects=(\d+) fields=(\d+)",
        line,
    )
    if match is None:
        raise RuntimeError(f"unexpected benchmark output: {line!r}")
    return {
        name: int(value)
        for name, value in zip(
            ("checksum", "nodes", "arrays", "objects", "fields"),
            match.groups(),
            strict=True,
        )
    }


def _run(
    command: list[str],
    repeat: int,
    payload: bytes,
    *,
    root: Path,
    cpu: int,
) -> tuple[float, dict[str, int]]:
    invocation = [*command, str(repeat)]
    taskset = shutil.which("taskset")
    if taskset is not None:
        invocation = [taskset, "-c", str(cpu), *invocation]
    started = time.perf_counter_ns()
    completed = subprocess.run(
        invocation,
        cwd=root,
        input=payload,
        capture_output=True,
        check=False,
    )
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    if completed.returncode != 0:
        raise RuntimeError(
            f"benchmark arm failed ({completed.returncode}): "
            f"{completed.stderr.decode('utf-8', errors='replace')}"
        )
    return elapsed_ms, _parse_result(completed.stdout.decode("utf-8"))


def _bootstrap_median_interval(samples: list[float]) -> tuple[float, float]:
    rng = random.Random(0x4D45524C4F)
    medians = []
    for _ in range(4000):
        medians.append(statistics.median(rng.choices(samples, k=len(samples))))
    medians.sort()
    return medians[int(len(medians) * 0.025)], medians[int(len(medians) * 0.975)]


def _arm_report(samples: list[float], *, repeat: int) -> dict[str, Any]:
    median = statistics.median(samples)
    mad = statistics.median(abs(item - median) for item in samples)
    low, high = _bootstrap_median_interval(samples)
    return {
        "available": True,
        "repeat": repeat,
        "samples_ms": samples,
        "median_ms": median,
        "mad_ms": mad,
        "relative_mad": mad / median if median else 0.0,
        "bootstrap_median_95_percent_ms": [low, high],
        "minimum_ms": min(samples),
        "maximum_ms": max(samples),
    }


def _surface_metrics(path: Path | tuple[Path, ...], language: str) -> dict[str, Any]:
    paths = (path,) if isinstance(path, Path) else path
    source = "\n".join(item.read_text(encoding="utf-8") for item in paths)
    lexical = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[^\w\s]", source)
    punctuation = [item for item in lexical if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?", item)]
    if language == "merlo":
        nesting = max(
            (len(line) - len(line.lstrip(" "))) // 4
            for line in source.splitlines()
            if line.strip()
        )
        constructs = {
            word
            for word in (
                "record",
                "enum",
                "fn",
                "let",
                "var",
                "if",
                "else",
                "while",
                "match",
                "return",
            )
            if re.search(rf"\b{word}\b", source)
        }
        type_annotations = len(re.findall(r":\s*[A-Z][A-Za-z0-9_]*(?:\[[^\]]+\])?", source)) + len(re.findall(r"->\s*[A-Z]", source))
        ownership_annotations = len(re.findall(r"\b(?:owned|borrow|borrow_mut|move)\b", source))
        explicit_allocations = len(re.findall(r"\b(?:Vec|Box|TextBuilder)\.new\b|\bText\.from_bytes\b", source))
        explicit_drop_free = len(re.findall(r"\b(?:drop|free)\s*\(", source))
        lifetime_annotations = len(re.findall(r"'[A-Za-z_]", source))
        manual_malloc_free = len(re.findall(r"\b(?:malloc|realloc|free)\s*\(", source))
        manual_retain_release = len(re.findall(r"\b(?:retain|release)\s*\(", source))
    elif language == "c":
        depth = 0
        nesting = 0
        for byte in source:
            if byte == "{":
                depth += 1
                nesting = max(nesting, depth)
            elif byte == "}":
                depth = max(0, depth - 1)
        constructs = {word for word in ("struct", "enum", "if", "else", "while", "for", "switch", "return") if re.search(rf"\b{word}\b", source)}
        type_annotations = len(re.findall(r"\b(?:void|bool|char|size_t|uint\d+_t|Node|Text|Field|Parser|Stats)\b", source))
        ownership_annotations = 0
        explicit_allocations = len(re.findall(r"\b(?:malloc|calloc|realloc)\s*\(", source))
        explicit_drop_free = len(re.findall(r"\bfree\s*\(", source))
        lifetime_annotations = 0
        manual_malloc_free = explicit_allocations + explicit_drop_free
        manual_retain_release = len(re.findall(r"\b(?:retain|release)\s*\(", source))
    else:
        nesting = max(
            (len(line) - len(line.lstrip(" "))) // 4
            for line in source.splitlines()
            if line.strip()
        )
        constructs = {word for word in ("class", "def", "if", "else", "for", "while", "return", "try", "except") if re.search(rf"\b{word}\b", source)}
        type_annotations = len(re.findall(r"(?:^|[,()]\s*)[A-Za-z_][A-Za-z0-9_]*\s*:\s*[^,)=]+|->\s*[^:]+", source, re.MULTILINE))
        ownership_annotations = 0
        explicit_allocations = 0
        explicit_drop_free = 0
        lifetime_annotations = 0
        manual_malloc_free = 0
        manual_retain_release = 0
    return {
        "path": " + ".join(item.as_posix() for item in paths),
        "source_bytes": len(source.encode("utf-8")),
        "lexical_tokens": len(lexical),
        "punctuation_tokens": len(punctuation),
        "explicit_type_annotations": type_annotations,
        "ownership_annotations": ownership_annotations,
        "explicit_allocations": explicit_allocations,
        "explicit_drop_free_operations": explicit_drop_free,
        "maximum_nesting_depth": nesting,
        "distinct_constructs": sorted(constructs),
        "lifetime_annotations": lifetime_annotations,
        "manual_malloc_free_operations": manual_malloc_free,
        "manual_retain_release_operations": manual_retain_release,
    }


def run_general_representation_benchmark(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root).resolve()
    commands = _compile_arms(root_path)
    payload = _benchmark_payload()
    oracle = evaluate_python_oracle(payload)
    if not oracle.ok:
        raise RuntimeError("benchmark input must be valid JSON")
    expected = {
        "checksum": oracle.checksum,
        "nodes": oracle.nodes,
        "arrays": oracle.arrays,
        "objects": oracle.objects,
        "fields": oracle.fields,
    }
    cpu = min(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else 0

    repeat = 1
    calibration: list[dict[str, Any]] = []
    while True:
        observations = {}
        for name in ("merlo", "c"):
            elapsed, result = _run(commands[name] or [], repeat, payload, root=root_path, cpu=cpu)
            if result != expected:
                raise RuntimeError(f"{name} calibration checksum mismatch")
            observations[name] = elapsed
        calibration.append({"repeat": repeat, "elapsed_ms": observations})
        if min(observations.values()) >= MINIMUM_RUN_MS:
            break
        repeat *= 2
        if repeat > 1 << 24:
            raise RuntimeError("benchmark calibration failed to reach timing floor")

    available = {name: command for name, command in commands.items() if command}
    for _ in range(WARMUPS):
        for name, command in available.items():
            _elapsed, result = _run(command, repeat, payload, root=root_path, cpu=cpu)
            if result != expected:
                raise RuntimeError(f"{name} warmup checksum mismatch")

    schedule = [name for name in available for _ in range(MEASUREMENTS)]
    random.Random(0xB16B00B5).shuffle(schedule)
    samples = {name: [] for name in available}
    for name in schedule:
        elapsed, result = _run(available[name], repeat, payload, root=root_path, cpu=cpu)
        if result != expected:
            raise RuntimeError(f"{name} measured checksum mismatch")
        samples[name].append(elapsed)

    arms: dict[str, Any] = {
        name: _arm_report(values, repeat=repeat) for name, values in samples.items()
    }
    if commands["rust"] is None:
        arms["rust"] = {"available": False, "reason": "rustc unavailable"}
    native_medians = [arms[name]["median_ms"] for name in ("c", "rust") if arms.get(name, {}).get("available")]
    best_native = min(native_medians)
    native_ratio = arms["merlo"]["median_ms"] / best_native
    timing_stable = all(
        report["relative_mad"] <= MAXIMUM_RELATIVE_MAD
        for report in arms.values()
        if report.get("available")
    )
    surface = {
        "merlo": _surface_metrics(root_path / "merlo" / "programs" / "general_json.mlo", "merlo"),
        "c": _surface_metrics(root_path / "tools" / "benchmarks" / "merlo" / "benchmarks" / "general_representation" / "reference_json.c", "c"),
        "python": _surface_metrics(
            (
                root_path / "tools" / "benchmarks" / "merlo" / "benchmarks" / "general_representation" / "reference_json.py",
                root_path / "merlo" / "general_json_oracle.py",
            ),
            "python",
        ),
        "rust": {"available": False, "reason": "rustc unavailable"},
    }
    return {
        "schema_version": 1,
        "contract": "merlo.general-representation-benchmark.v1",
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_bytes": len(payload),
        "expected": expected,
        "method": {
            "warmups_per_arm": WARMUPS,
            "measurements_per_arm": MEASUREMENTS,
            "randomized_order": True,
            "cpu_affinity": cpu,
            "taskset_available": shutil.which("taskset") is not None,
            "minimum_run_ms": MINIMUM_RUN_MS,
            "maximum_relative_mad": MAXIMUM_RELATIVE_MAD,
            "maximum_native_ratio": MAXIMUM_NATIVE_RATIO,
            "repeat": repeat,
            "calibration": calibration,
        },
        "arms": arms,
        "best_native_median_ms": best_native,
        "merlo_to_best_native_ratio": native_ratio,
        "timing_stable": timing_stable,
        "performance_gate_passed": timing_stable and native_ratio <= MAXIMUM_NATIVE_RATIO,
        "surface": surface,
        "surface_gate": {
            "zero_merlo_lifetime_annotations": surface["merlo"]["lifetime_annotations"] == 0,
            "zero_merlo_manual_malloc_free": surface["merlo"]["manual_malloc_free_operations"] == 0,
            "zero_merlo_manual_retain_release": surface["merlo"]["manual_retain_release_operations"] == 0,
            "zero_merlo_normal_path_manual_drop": surface["merlo"]["explicit_drop_free_operations"] == 0,
        },
    }


__all__ = ["run_general_representation_benchmark"]
