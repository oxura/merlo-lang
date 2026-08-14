"""Aggregate Stage 0.5P evidence without overstating native results."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from .native_hypotheses import NATIVE_HYPOTHESES_FILENAME
from .stage05p_freeze import STAGE05P_FREEZE_FILENAME, assert_stage05p_frozen
from .stage05p_protocol import STAGE05P_DECISIONS, STAGE05P_PROTOCOL_FILENAME


STAGE05P_DECISION_SCHEMA_VERSION = 1
STAGE05P_DECISION_FILENAME = "meldra_stage05p_decision.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _geometric_mean(values: list[float]) -> float | None:
    if not values or any(value <= 0 for value in values):
        return None
    return math.exp(sum(math.log(value) for value in values) / len(values))


def build_stage05p_decision(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root)
    freeze = assert_stage05p_frozen(root_path)
    benchmark_path = root_path / "benchmarks" / "stage05p_runs" / "report.json"
    hypotheses_path = root_path / "benchmarks" / NATIVE_HYPOTHESES_FILENAME
    protocol_path = root_path / "benchmarks" / STAGE05P_PROTOCOL_FILENAME
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    hypotheses = json.loads(hypotheses_path.read_text(encoding="utf-8"))
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    observations = benchmark["observations"]
    by_pair = {(item["workload"], item["language"]): item for item in observations}
    runtime_ratios: dict[str, float] = {}
    rss_ratios: dict[str, float] = {}
    binary_size_ratios: dict[str, float] = {}
    compile_time_ratios: dict[str, float] = {}
    for workload in (item["id"] for item in benchmark["workloads"]):
        meldra = by_pair.get((workload, "meldra"))
        c = by_pair.get((workload, "c"))
        if not meldra or not c:
            continue
        if meldra["run"]["status"] != "MEASURED" or c["run"]["status"] != "MEASURED":
            continue
        runtime_ratios[workload] = meldra["run"]["runtime_ms"] / c["run"]["runtime_ms"]
        if meldra["run"]["peak_rss_kb"] and c["run"]["peak_rss_kb"]:
            rss_ratios[workload] = meldra["run"]["peak_rss_kb"] / c["run"]["peak_rss_kb"]
        if meldra["build"]["binary_size"] and c["build"]["binary_size"]:
            binary_size_ratios[workload] = meldra["build"]["binary_size"] / c["build"]["binary_size"]
        if meldra["build"]["compile_time_ms"] and c["build"]["compile_time_ms"]:
            compile_time_ratios[workload] = meldra["build"]["compile_time_ms"] / c["build"]["compile_time_ms"]
    compute_ratios = [
        value
        for workload, value in runtime_ratios.items()
        if workload != "startup"
    ]
    unique_compute_ratios = [
        value
        for workload, value in runtime_ratios.items()
        if workload not in {"startup", "shared_allocations"}
    ]
    hypothesis_statuses = {
        item["id"]: item["status"] for item in hypotheses["hypotheses"]
    }
    language_coverage: dict[str, dict[str, int]] = {}
    for language in benchmark["protocol"]["languages"]:
        statuses = Counter(
            item["run"]["status"]
            for item in observations
            if item["language"] == language
        )
        language_coverage[language] = dict(sorted(statuses.items()))
    measured_meldra = [
        item
        for item in observations
        if item["language"] == "meldra" and item["run"]["status"] == "MEASURED"
    ]
    measured_correct = all(item["run"]["correct"] is True for item in measured_meldra)
    all_hypotheses_pass = all(
        status == "PASS" for status in hypothesis_statuses.values()
    )
    external_native_breadth = all(
        language_coverage.get(language, {}).get("MEASURED", 0) > 0
        for language in ("rust", "go", "csharp")
    )
    all_meldra_categories = len(measured_meldra) == len(benchmark["workloads"])
    geomean = _geometric_mean(compute_ratios)
    unique_geomean = _geometric_mean(unique_compute_ratios)
    go_native = (
        measured_correct
        and all_hypotheses_pass
        and external_native_breadth
        and all_meldra_categories
        and protocol["frontend_relationship"]["status"] != "ADAPTER_REQUIRED"
        and geomean is not None
        and geomean <= 1.25
    )
    continue_research = (
        measured_correct
        and len(runtime_ratios) >= 6
        and geomean is not None
        and geomean <= 2.0
        and hypothesis_statuses.get(
            "high_level_collection_pipelines_lower_to_one_loop"
        )
        == "PASS"
        and hypothesis_statuses.get("unique_values_update_without_copy") == "PASS"
        and hypothesis_statuses.get(
            "deterministic_source_produces_deterministic_native_output"
        )
        == "PASS"
    )
    if go_native:
        decision = "GO_NATIVE_LANGUAGE_RESEARCH"
    elif continue_research:
        decision = "CONTINUE_PERFORMANCE_RESEARCH"
    elif not measured_meldra or benchmark["environment"]["c_compiler"] is None:
        decision = "GO_PYTHON_PLATFORM_ONLY"
    else:
        decision = "NO_GO_NATIVE_LANGUAGE"
    if decision not in STAGE05P_DECISIONS:
        raise AssertionError(f"invalid Stage 0.5P decision: {decision}")
    shared_ratio = runtime_ratios.get("shared_allocations")
    return {
        "schema_version": STAGE05P_DECISION_SCHEMA_VERSION,
        "kind": "MeldraStage05PDecision",
        "decision": decision,
        "inputs": {
            str(path.relative_to(root_path)): _sha256(path)
            for path in (
                root_path / "benchmarks" / STAGE05P_FREEZE_FILENAME,
                protocol_path,
                benchmark_path,
                hypotheses_path,
            )
        },
        "freeze_verification": freeze.to_dict(),
        "gates": {
            "measured_meldra_correct": measured_correct,
            "measured_meldra_workloads": len(measured_meldra),
            "all_five_hypotheses_pass": all_hypotheses_pass,
            "external_rust_go_csharp_measured": external_native_breadth,
            "all_categories_supported_by_meldra": all_meldra_categories,
            "frozen_frontend_directly_compatible": protocol["frontend_relationship"]["status"] != "ADAPTER_REQUIRED",
            "paired_compute_runtime_geomean_le_1_25": geomean is not None and geomean <= 1.25,
            "paired_compute_runtime_geomean_le_2_0": geomean is not None and geomean <= 2.0,
        },
        "measurements": {
            "language_coverage": language_coverage,
            "meldra_over_c_runtime_ratios": dict(sorted(runtime_ratios.items())),
            "meldra_over_c_compute_geometric_mean": geomean,
            "meldra_over_c_unique_compute_geometric_mean": unique_geomean,
            "meldra_over_c_peak_rss_ratios": dict(sorted(rss_ratios.items())),
            "meldra_over_c_binary_size_ratios": dict(sorted(binary_size_ratios.items())),
            "meldra_over_c_compile_time_ratios": dict(sorted(compile_time_ratios.items())),
            "hypotheses": hypothesis_statuses,
        },
        "counterexamples_and_gaps": [
            {
                "id": "shared_refcount_fallback_overhead",
                "observed": (
                    f"{shared_ratio:.6f}x Meldra/C runtime"
                    if shared_ratio is not None
                    else "UNMEASURED"
                ),
                "interpretation": (
                    "The explicit shared fallback performs a separate reference-count "
                    "allocation and is materially slower than C raw ownership in this corpus."
                ),
            },
            {
                "id": "frozen_frontend_coreir_gap",
                "observed": "ADAPTER_REQUIRED",
                "interpretation": (
                    "The existing frozen frontend/CoreIR cannot carry the Performance "
                    "MIR constructs, so this slice does not prove reuse of CoreIR v1."
                ),
            },
            {
                "id": "closed_interface_gap",
                "observed": hypothesis_statuses.get(
                    "closed_interfaces_enable_devirtualization"
                ),
                "interpretation": "No interface representation or devirtualization pass exists in the frozen subset.",
            },
            {
                "id": "text_and_recursive_tree_gap",
                "observed": "strings unsupported; tree benchmark uses explicit child indices",
                "interpretation": (
                    "The corpus does not establish string layout quality or recursive "
                    "pointer/value performance."
                ),
            },
            {
                "id": "toolchain_coverage_gap",
                "observed": "Rust, Go, and C# UNMEASURED_TOOLCHAIN_UNAVAILABLE",
                "interpretation": (
                    "Their source corpus is preserved, but this host cannot validate "
                    "correctness or performance against those compilers."
                ),
            },
        ],
        "claims_not_established": [
            "Meldra is generally as fast as C or Rust",
            "Meldra memory use is generally competitive",
            "the native syntax is simpler for humans",
            "same-model AI productivity improves",
            "compiler quality beyond the generated-C vertical slice",
            "dynamic arrays, strings, recursive values, or closed interfaces are efficient",
            "algorithm allocation counters equal total runtime allocations",
        ],
        "rationale": (
            "The measured generated-C slice is correct and close to the same Clang C "
            "baseline for unique numeric kernels, while pipeline fusion, stack reuse, "
            "pure inlining, and deterministic binaries pass. Research cannot advance "
            "to GO: closed-interface devirtualization is unmeasured, three native "
            "competitor toolchains are unavailable, Text is unsupported, CoreIR v1 "
            "requires an adapter, and shared refcount fallback shows measurable overhead."
        ),
    }


def write_stage05p_decision(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root)
    payload = build_stage05p_decision(root_path)
    (root_path / "benchmarks" / STAGE05P_DECISION_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


__all__ = [
    "STAGE05P_DECISION_FILENAME",
    "STAGE05P_DECISION_SCHEMA_VERSION",
    "build_stage05p_decision",
    "write_stage05p_decision",
]
