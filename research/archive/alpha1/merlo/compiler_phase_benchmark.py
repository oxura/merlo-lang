"""Repeated Stage 0.6P compiler phase and artifact-size measurements."""

from __future__ import annotations
import ast

import hashlib
import json
import os
import random
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from research.archive.alpha1.merlo.native_bench import WORKLOADS, _meldra_source
from merlo.native_c_backend import CEmitter, compiler_version, find_c_compiler
from research.archive.alpha1.merlo.native_hir import (
    compile_native_hir,
    lower_native_hir_to_performance,
    validate_native_source,
)
from tools.benchmarks.merlo.performance_frontend import (
    _preprocess,
    compile_performance_source,
)
from tools.benchmarks.merlo.performance_opt import optimize_mir
from research.archive.alpha1.merlo.stage06p_benchmark import BENCHMARK_SEED, _distribution


COMPILER_PHASE_SCHEMA_VERSION = 2


def _measure(action: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter_ns()
    result = action()
    return result, (time.perf_counter_ns() - started) / 1_000_000


def _summary(values: list[float], seed: int) -> dict[str, Any]:
    distribution = _distribution(values, seed=seed)
    return {
        "samples_ms": values,
        **{f"{name}_ms": value for name, value in distribution.items()},
    }


def _parse_surface(source: str, path: str) -> ast.Module:
    return ast.parse(_preprocess(source).source, filename=path)


def _external_compile_link(
    source: str,
    *,
    directory: Path,
    repetitions: int,
    seed: int,
) -> dict[str, Any]:
    compiler = find_c_compiler()
    if compiler is None:
        return {"status": "UNMEASURED_COMPILER_UNAVAILABLE"}
    directory.mkdir(parents=True, exist_ok=True)
    source_path = directory / "program.c"
    object_path = directory / "program.o"
    binary_path = directory / "program"
    source_path.write_text(source, encoding="utf-8")
    compile_command = (
        compiler,
        "-std=c11",
        "-O3",
        "-fwrapv",
        "-fno-delete-null-pointer-checks",
        "-ffp-contract=off",
        "-fno-ident",
        "-Werror",
        "-c",
        str(source_path),
        "-o",
        str(object_path),
    )
    link_command = (
        compiler,
        str(object_path),
        "-Wl,--build-id=none",
        "-o",
        str(binary_path),
    )
    environment = dict(
        os.environ,
        SOURCE_DATE_EPOCH="0",
        LC_ALL="C",
        TZ="UTC",
    )
    compile_samples = []
    link_samples = []
    for _ in range(repetitions):
        completed, elapsed = _measure(
            lambda: subprocess.run(
                compile_command,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
                env=environment,
            )
        )
        if completed.returncode != 0:
            return {
                "status": "FAILED",
                "phase": "compile",
                "stderr": completed.stderr,
            }
        compile_samples.append(elapsed)
        completed, elapsed = _measure(
            lambda: subprocess.run(
                link_command,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
                env=environment,
            )
        )
        if completed.returncode != 0:
            return {
                "status": "FAILED",
                "phase": "link",
                "stderr": completed.stderr,
            }
        link_samples.append(elapsed)
    return {
        "status": "MEASURED",
        "compiler": compiler,
        "compiler_version": compiler_version(compiler),
        "compile_command": list(compile_command),
        "link_command": list(link_command),
        "compile": _summary(compile_samples, seed),
        "link": _summary(link_samples, seed ^ 0x11A0),
        "binary_size": binary_path.stat().st_size,
        "binary_sha256": hashlib.sha256(binary_path.read_bytes()).hexdigest(),
    }


def _compile_in_process(source: str, path: str) -> str:
    hir = compile_native_hir(source, path=path)
    mir = lower_native_hir_to_performance(hir)
    optimized, _ = optimize_mir(mir)
    return CEmitter(optimized, runtime_arguments=True).emit()


def run_compiler_phase_benchmark(
    *,
    output_dir: str | Path = "tools/benchmarks/merlo/benchmarks/stage06p_compile_phases",
    repetitions: int = 30,
    warmups: int = 5,
) -> dict[str, Any]:
    if repetitions < 30 or warmups < 1:
        raise ValueError("compiler phases require 30 measured runs and warmups")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    workloads = tuple(item for item in WORKLOADS if item.meldra_supported)
    samples: dict[str, dict[str, list[float]]] = {
        item.id: {
            "surface_parse": [],
            "native_scope_validation": [],
            "performance_frontend_typecheck_mir": [],
            "native_hir_total": [],
            "hir_to_performance_adapter": [],
            "mir_optimization": [],
            "c_codegen": [],
            "in_process_total": [],
        }
        for item in workloads
    }
    rng = random.Random(BENCHMARK_SEED ^ 0xC011E)
    schedule_hash = hashlib.sha256()
    final_artifacts: dict[str, dict[str, Any]] = {}
    for round_index in range(warmups + repetitions):
        schedule = list(workloads)
        rng.shuffle(schedule)
        for workload in schedule:
            source = _meldra_source(workload)
            schedule_hash.update(f"{round_index}:{workload.id}\n".encode())
            _, parse_ms = _measure(
                lambda: _parse_surface(
                    source,
                    f"compile-phases/{workload.id}.meldra",
                )
            )
            _, validation_ms = _measure(
                lambda: validate_native_source(
                    source,
                    path=f"compile-phases/{workload.id}.meldra",
                )
            )
            hir, hir_ms = _measure(
                lambda: compile_native_hir(
                    source,
                    path=f"compile-phases/{workload.id}.meldra",
                )
            )
            frontend, frontend_ms = _measure(
                lambda: compile_performance_source(
                    source,
                    path=f"compile-phases/{workload.id}.meldra",
                )
            )
            adapted, adapter_ms = _measure(lambda: lower_native_hir_to_performance(hir))
            optimized_result, optimization_ms = _measure(lambda: optimize_mir(adapted))
            optimized, snapshots = optimized_result
            c_source, codegen_ms = _measure(
                lambda: CEmitter(optimized, runtime_arguments=True).emit()
            )
            _, in_process_total_ms = _measure(
                lambda: _compile_in_process(
                    source,
                    f"compile-phases/{workload.id}.meldra",
                )
            )
            if round_index >= warmups:
                target = samples[workload.id]
                target["surface_parse"].append(parse_ms)
                target["native_scope_validation"].append(validation_ms)
                target["performance_frontend_typecheck_mir"].append(frontend_ms)
                target["native_hir_total"].append(hir_ms)
                target["hir_to_performance_adapter"].append(adapter_ms)
                target["mir_optimization"].append(optimization_ms)
                target["c_codegen"].append(codegen_ms)
                target["in_process_total"].append(in_process_total_ms)
            final_artifacts[workload.id] = {
                "source_bytes": len(source.encode()),
                "hir_json_bytes": len(json.dumps(hir.to_dict(), sort_keys=True).encode()),
                "mir_json_bytes": len(optimized.to_json().encode()),
                "generated_c_bytes": len(c_source.encode()),
                "hir_digest": hir.digest,
                "mir_digest": optimized.digest,
                "generated_c_sha256": hashlib.sha256(c_source.encode()).hexdigest(),
                "optimizer_statistics": [
                    snapshot.statistics.to_dict() for snapshot in snapshots
                ],
            }
    observations = []
    for workload_index, workload in enumerate(workloads):
        phase_values = samples[workload.id]
        phases = {
            phase: _summary(values, BENCHMARK_SEED ^ workload_index ^ phase_index)
            for phase_index, (phase, values) in enumerate(phase_values.items())
        }
        # Attribution phases overlap; in_process_total is independently timed end-to-end.
        source = _meldra_source(workload)
        frontend = compile_performance_source(source)
        optimized, _ = optimize_mir(frontend.mir)
        c_source = CEmitter(optimized, runtime_arguments=True).emit()
        hir = compile_native_hir(
            source,
            path=f"compile-phases/{workload.id}.meldra",
        )
        hir_root = root / "hir"
        hir_root.mkdir(parents=True, exist_ok=True)
        (hir_root / f"{workload.id}.json").write_text(
            json.dumps(hir.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        external = _external_compile_link(
            c_source,
            directory=root / "artifacts" / workload.id,
            repetitions=repetitions,
            seed=BENCHMARK_SEED ^ workload_index ^ 0xC0DE,
        )
        observations.append(
            {
                "workload": workload.id,
                "phases": phases,
                "external_c_compile_link": external,
                **final_artifacts[workload.id],
            }
        )
    report = {
        "schema_version": COMPILER_PHASE_SCHEMA_VERSION,
        "kind": "MeldraStage06PCompilerPhases",
        "protocol": {
            "repetitions": repetitions,
            "warmups": warmups,
            "randomized_workload_order": True,
            "schedule_sha256": schedule_hash.hexdigest(),
            "timer": "time.perf_counter_ns",
            "external_c_compile_runs": repetitions,
            "external_link_runs": repetitions,
            "phase_boundaries": {
                "surface_parse": "preprocess plus Python AST parse",
                "native_scope_validation": "scope, move, and borrow validation; not the full type checker",
                "performance_frontend_typecheck_mir": "combined type checking and MIR lowering; inseparable in the current frontend",
                "native_hir_total": "Native HIR construction including the performance frontend adapter",
                "hir_to_performance_adapter": "versioned HIR/MIR contract check and handoff",
                "mir_optimization": "all registered optimizer passes",
                "c_codegen": "Performance MIR to C source",
                "in_process_total": "independently timed end-to-end source through C source",
            },
            "note": "Attribution phases overlap. No subtraction is used to invent an unobservable standalone type-check duration.",
        },
        "observations": observations,
    }
    (root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


__all__ = ["COMPILER_PHASE_SCHEMA_VERSION", "run_compiler_phase_benchmark"]
