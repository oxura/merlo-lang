"""Measured Stage 0.6P ownership, borrow, region, RC, and GC alternatives."""

from __future__ import annotations

import hashlib
import json
import random
import statistics
from pathlib import Path
from typing import Any

from .native_bench import (
    WORKLOADS,
    _Build,
    _compile_external,
    _meldra_source,
    competitor_source,
    reference_checksum,
)
from .native_c_backend import CEmitter, compile_c_source
from .performance_frontend import compile_performance_source
from .performance_opt import (
    OPTIMIZATION_PIPELINE,
    bounds_check_elimination,
    collection_fusion,
    constant_folding,
    dead_code_elimination,
    memory_model_lowering,
    monomorphization,
    optimize_mir,
    region_ownership_lowering,
)
from .stage06p_benchmark import (
    BENCHMARK_SEED,
    _cpu_state,
    _distribution,
    _run_one,
)


MEMORY_EXPERIMENT_SCHEMA_VERSION = 2

_C_RC_SOURCE = r'''#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#if defined(__GNUC__) || defined(__clang__)
#define NOINLINE __attribute__((noinline))
#else
#define NOINLINE
#endif
typedef struct { uint64_t *data; uint64_t *refs; } Values;
static uint64_t allocations = 0, retains = 0, releases = 0;
static NOINLINE Values make_values(uint64_t i) {
    Values value = { malloc(8 * sizeof(uint64_t)), malloc(sizeof(uint64_t)) };
    if (!value.data || !value.refs) abort();
    *value.refs = 1; ++allocations;
    for (uint64_t j = 0; j < 8; ++j) value.data[j] = i + j;
    return value;
}
static NOINLINE Values retain(Values value) { ++*value.refs; ++retains; return value; }
static NOINLINE void release(Values value) {
    ++releases;
    if (--*value.refs == 0) { free(value.data); free(value.refs); }
}
static uint64_t run(uint64_t n) {
    uint64_t checksum = 0;
    for (uint64_t i = 0; i < n; ++i) {
        Values value = make_values(i);
        Values alias = retain(value);
        checksum += alias.data[0] + alias.data[7];
        release(alias); release(value);
    }
    return checksum;
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t result = run(strtoull(argv[1], NULL, 10));
    fprintf(stderr, "BENCH_ALLOCATIONS=%" PRIu64 " RETAINS=%" PRIu64 " RELEASES=%" PRIu64 "\n", allocations, retains, releases);
    printf("%" PRIu64 "\n", result);
    return 0;
}
'''

_RUST_TEMPLATE = r'''use std::sync::Arc;
use std::rc::Rc;
fn run(n:u64)->u64 {
    let mut checksum=0u64;
    for i in 0..n {
        let value = WRAPPER::new([i,i+1,i+2,i+3,i+4,i+5,i+6,i+7]);
        let alias = WRAPPER::clone(&value);
        checksum=checksum.wrapping_add(alias[0]).wrapping_add(alias[7]);
        drop(alias); drop(value);
    }
    checksum
}
fn main() {
    let n=std::env::args().nth(1).unwrap().parse::<u64>().unwrap();
    let result=run(n);
    eprintln!("BENCH_ALLOCATIONS={}",n);
    println!("{}",result);
}
'''


def _meldra_refcount_source(source: str) -> str:
    return source.replace(
        "        checksum = checksum + values[0] + values[7]\n"
        "        drop(values)\n",
        "        let alias: Shared[Array[UInt64, 8]] = retain(values)\n"
        "        checksum = checksum + alias[0] + alias[7]\n"
        "        release(alias)\n"
        "        release(values)\n",
    )


def _compile_meldra(
    strategy: str,
    source: str,
    directory: Path,
    input_value: int,
) -> tuple[_Build, dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    source_path = directory / "main.meldra"
    source_path.write_text(source, encoding="utf-8")
    frontend = compile_performance_source(source, path=f"memory/{strategy}.meldra")
    if strategy == "refcount":
        passes = (
            monomorphization,
            collection_fusion,
            constant_folding,
            bounds_check_elimination,
            memory_model_lowering,
            dead_code_elimination,
        )
    else:
        passes = OPTIMIZATION_PIPELINE
    optimized, snapshots = optimize_mir(
        frontend.mir,
        artifact_dir=directory / "mir",
        passes=passes,
    )
    region_statistics = None
    if strategy == "region":
        optimized, region_statistics = region_ownership_lowering(optimized)
        (directory / "mir" / "region_after.json").write_text(
            json.dumps(optimized.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    c_source = CEmitter(optimized, runtime_arguments=True).emit()
    (directory / "generated.c").write_text(c_source, encoding="utf-8")
    result = compile_c_source(c_source, output_dir=directory, stem="program")
    build = _Build(
        result.status,
        result.command,
        ((result.binary_path, str(input_value)) if result.binary_path else ()),
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
    metadata = {
        "passes": [snapshot.statistics.to_dict() for snapshot in snapshots],
        "region_statistics": (
            region_statistics.to_dict() if region_statistics is not None else None
        ),
        "mir_digest": optimized.digest,
    }
    return build, metadata


def _summary(
    samples: list[dict[str, Any]], *, seed: int, expected_runs: int
) -> dict[str, Any]:
    measured = [item for item in samples if item.get("status") == "MEASURED"]
    wall = [float(item["wall_ms"]) for item in measured]
    rss = [
        float(item["peak_rss_kb"])
        for item in measured
        if item.get("peak_rss_kb") is not None
    ]
    allocations = [
        int(item["algorithm_allocations"])
        for item in measured
        if item.get("algorithm_allocations") is not None
    ]
    return {
        "status": "MEASURED" if len(measured) == expected_runs else "FAILED",
        "correct": len(measured) == expected_runs,
        "samples": samples,
        "wall_ms": _distribution(wall, seed=seed),
        "peak_rss_kb": _distribution(rss, seed=seed ^ 0x525353),
        "algorithm_allocations": (
            statistics.median(allocations) if allocations else None
        ),
    }
def _static_memory_counters(name: str, input_value: int) -> dict[str, Any]:
    zero_allocation = name in {"meldra_borrow", "meldra_region"}
    payload_bytes = 0 if zero_allocation else input_value * 8 * 8
    known_requested_bytes = {
        "meldra_refcount": input_value * (8 * 8 + 8),
        "meldra_borrow": 0,
        "meldra_region": 0,
        "c_manual": input_value * 8 * 8,
        "c_refcount": input_value * (8 * 8 + 8),
    }.get(name)
    retain_release = {
        "meldra_refcount": (input_value, input_value * 2),
        "meldra_borrow": (0, 0),
        "meldra_region": (0, 0),
        "c_manual": (0, input_value),
        "c_refcount": (input_value, input_value * 2),
        "rust_rc": (input_value, input_value * 2),
        "rust_arc": (input_value, input_value * 2),
        "go_gc": (0, 0),
        "csharp_gc": (0, 0),
    }[name]
    return {
        "payload_bytes_requested": payload_bytes,
        "total_bytes_requested": (
            known_requested_bytes
            if known_requested_bytes is not None
            else "UNMEASURED_RUNTIME_HEADER_AND_ALLOCATOR_OVERHEAD"
        ),
        "value_writes": 0 if zero_allocation else input_value * 8,
        "retains": retain_release[0],
        "releases_or_gc_retirements": retain_release[1],
        "system_allocator_call_count": {
            "meldra_refcount": input_value * 2,
            "c_manual": input_value,
            "c_refcount": input_value * 2,
        }.get(name, "UNMEASURED_RUNTIME_INTERNAL"),
    }



def run_memory_model_experiment(
    *,
    output_dir: str | Path = "benchmarks/stage06p_memory",
    repetitions: int = 30,
    warmups: int = 5,
) -> dict[str, Any]:
    if repetitions < 30 or warmups < 1:
        raise ValueError("memory experiment requires 30 measured runs and warmups")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    workload = next(item for item in WORKLOADS if item.id == "shared_allocations")
    source = _meldra_source(workload)
    expected = reference_checksum(workload)
    builds: dict[str, _Build] = {}
    metadata: dict[str, Any] = {}

    for strategy in ("refcount", "borrow", "region"):
        strategy_source = (
            _meldra_refcount_source(source) if strategy == "refcount" else source
        )
        build, details = _compile_meldra(
            strategy,
            strategy_source,
            root / "corpus" / f"meldra_{strategy}",
            workload.input,
        )
        builds[f"meldra_{strategy}"] = build
        metadata[f"meldra_{strategy}"] = details

    for arm_name in (
        "c_manual",
        "c_refcount",
        "rust_rc",
        "rust_arc",
        "go_gc",
        "csharp_gc",
    ):
        (root / "corpus" / arm_name).mkdir(parents=True, exist_ok=True)
    builds["c_manual"] = _compile_external(
        "c",
        competitor_source("c", workload),
        root / "corpus" / "c_manual",
        (str(workload.input),),
    )
    builds["c_refcount"] = _compile_external(
        "c",
        _C_RC_SOURCE,
        root / "corpus" / "c_refcount",
        (str(workload.input),),
    )
    for name, wrapper in (("rust_rc", "Rc"), ("rust_arc", "Arc")):
        builds[name] = _compile_external(
            "rust",
            _RUST_TEMPLATE.replace("WRAPPER", wrapper),
            root / "corpus" / name,
            (str(workload.input),),
        )
    builds["go_gc"] = _compile_external(
        "go",
        competitor_source("go", workload),
        root / "corpus" / "go_gc",
        (str(workload.input),),
    )
    builds["csharp_gc"] = _compile_external(
        "csharp",
        competitor_source("csharp", workload),
        root / "corpus" / "csharp_gc",
        (str(workload.input),),
    )

    state_before = _cpu_state()
    cpu = state_before["selected_cpu"]
    samples = {name: [] for name, build in builds.items() if build.status == "MEASURED"}
    rng = random.Random(BENCHMARK_SEED ^ 0x4D454D)
    schedule_hash = hashlib.sha256()
    for round_index in range(warmups + repetitions):
        schedule = list(samples)
        rng.shuffle(schedule)
        for name in schedule:
            schedule_hash.update(f"{round_index}:{name}\n".encode())
            observation = _run_one(builds[name], expected, cpu)
            if round_index >= warmups:
                samples[name].append(observation)
    state_after = _cpu_state()

    arms = []
    for index, (name, build) in enumerate(builds.items()):
        summary = _summary(
            samples.get(name, []),
            seed=BENCHMARK_SEED ^ index,
            expected_runs=repetitions,
        )
        arms.append(
            {
                "name": name,
                "build_status": build.status,
                "compile_command": list(build.command),
                "compiler": build.compiler,
                "compiler_version": build.compiler_version,
                "binary_size": build.binary_size,
                "source_sha256": build.source_sha256,
                "binary_sha256": build.binary_sha256,
                "metadata": metadata.get(name),
                "static_memory_counters": _static_memory_counters(
                    name,
                    workload.input,
                ),
                **summary,
            }
        )

    by_name = {item["name"]: item for item in arms}
    c_median = by_name["c_manual"]["wall_ms"]["median"]
    for arm in arms:
        median = arm["wall_ms"]["median"]
        arm["runtime_over_c_manual"] = (
            median / c_median if median is not None and c_median else None
        )
    def fastest(names: tuple[str, ...]) -> str | None:
        measured = [
            (name, by_name[name]["wall_ms"]["median"])
            for name in names
            if by_name[name]["wall_ms"]["median"] is not None
        ]
        return min(measured, key=lambda item: item[1])[0] if measured else None

    model_recommendations = [
        {
            "workload_class": "proven_non_escaping_unique",
            "winner": fastest(("meldra_borrow", "meldra_region", "c_manual")),
            "eligible_models": ["meldra_borrow", "meldra_region", "c_manual"],
        },
        {
            "workload_class": "explicit_shared_alias",
            "winner": fastest(
                ("meldra_refcount", "c_refcount", "rust_rc", "rust_arc")
            ),
            "eligible_models": [
                "meldra_refcount",
                "c_refcount",
                "rust_rc",
                "rust_arc",
            ],
        },
        {
            "workload_class": "tracing_gc_reference",
            "winner": fastest(("go_gc", "csharp_gc")),
            "eligible_models": ["go_gc", "csharp_gc"],
        },
        {
            "workload_class": "cyclic_shared_graph",
            "winner": None,
            "status": "UNSUPPORTED_DECLARED",
        },
    ]

    report = {
        "schema_version": MEMORY_EXPERIMENT_SCHEMA_VERSION,
        "kind": "MeldraStage06PMemoryModels",
        "protocol": {
            "input": workload.input,
            "expected_checksum": expected,
            "repetitions": repetitions,
            "warmups": warmups,
            "randomized_order": True,
            "schedule_sha256": schedule_hash.hexdigest(),
            "ordinary_meldra_lifetime_annotations": 0,
            "runtime_internal_allocations": "UNMEASURED except explicit algorithm counters",
        },
        "environment": {
            "before": state_before,
            "after": state_after,
            "stable": (
                state_before["governor"] == state_after["governor"]
                and state_before["intel_pstate_no_turbo"]
                == state_after["intel_pstate_no_turbo"]
            ),
        },
        "arms": arms,
        "correctness_failures": [item["name"] for item in arms if not item["correct"]],
        "model_recommendations": model_recommendations,
        "limitations": [
            "The Meldra and C reference-counted arms each perform one retain and two releases per iteration.",
            "Borrow and region arms are valid for this non-escaping workload; they do not prove general alias inference.",
            "RegionOwned is a lexical stack-backed region for this proof; no general arena allocator or escaping region is claimed.",
            "Go and C# runtime-internal allocation counts are not claimed.",
            "Cycles remain unsupported and are not benchmarked.",
        ],
    }
    (root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


__all__ = [
    "MEMORY_EXPERIMENT_SCHEMA_VERSION",
    "run_memory_model_experiment",
]
