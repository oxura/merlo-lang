"""Repeated MIR, C, and binary determinism evidence for Stage 0.6P."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .native_bench import WORKLOADS, _meldra_source
from .native_c_backend import CEmitter, compile_c_source
from .performance_frontend import compile_performance_source
from .performance_opt import optimize_mir

DETERMINISM_EVIDENCE_SCHEMA_VERSION = 1


def run_determinism_evidence(
    *,
    output_dir: str | Path = "benchmarks/stage06p_determinism",
) -> dict[str, Any]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    observations = []
    for workload in WORKLOADS:
        if not workload.meldra_supported:
            continue
        source = _meldra_source(workload)
        optimized_programs = []
        c_sources = []
        builds = []
        for repetition in range(2):
            initial = compile_performance_source(
                source,
                path=f"determinism/{workload.id}.meldra",
            ).mir
            optimized, _ = optimize_mir(initial)
            c_source = CEmitter(
                optimized,
                executable=True,
                runtime_arguments=True,
            ).emit()
            build = compile_c_source(
                c_source,
                output_dir=root / workload.id / f"run_{repetition}",
                stem="program",
            )
            optimized_programs.append(optimized)
            c_sources.append(c_source)
            builds.append(build)
        mir_equal = optimized_programs[0].digest == optimized_programs[1].digest
        c_equal = c_sources[0] == c_sources[1]
        binary_equal = (
            all(build.status == "MEASURED" for build in builds)
            and builds[0].binary_sha256 == builds[1].binary_sha256
        )
        observations.append(
            {
                "workload": workload.id,
                "mir_equal": mir_equal,
                "c_equal": c_equal,
                "binary_equal": binary_equal,
                "mir_digests": [item.digest for item in optimized_programs],
                "c_source_sha256": [item.source_sha256 for item in builds],
                "binary_sha256": [item.binary_sha256 for item in builds],
                "build_statuses": [item.status for item in builds],
                "commands": [list(item.command) for item in builds],
                "compiler": builds[0].compiler,
                "compiler_version": builds[0].compiler_version,
            }
        )
    all_mir_equal = all(item["mir_equal"] for item in observations)
    all_c_equal = all(item["c_equal"] for item in observations)
    all_binary_equal = all(item["binary_equal"] for item in observations)
    return {
        "schema_version": DETERMINISM_EVIDENCE_SCHEMA_VERSION,
        "kind": "MeldraStage06PDeterminism",
        "status": (
            "PASS"
            if all_mir_equal and all_c_equal and all_binary_equal
            else "FAIL"
        ),
        "workload_count": len(observations),
        "all_mir_equal": all_mir_equal,
        "all_c_equal": all_c_equal,
        "all_binary_equal": all_binary_equal,
        "observations": observations,
    }


def write_determinism_evidence(
    destination: str | Path = "benchmarks/meldra_stage06p_determinism.json",
) -> dict[str, Any]:
    report = run_determinism_evidence()
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


__all__ = [
    "DETERMINISM_EVIDENCE_SCHEMA_VERSION",
    "run_determinism_evidence",
    "write_determinism_evidence",
]
