from __future__ import annotations

import json
import math
from pathlib import Path

from tools.benchmarks.merlo.native_core_benchmark import (
    WORKLOAD_NAMES,
    _summary,
    load_workloads,
)


ROOT = Path(__file__).resolve().parents[1]


def test_native_core_workloads_match_frozen_c_and_rust_sources() -> None:
    workloads = load_workloads(ROOT)

    assert tuple(workload.name for workload in workloads) == WORKLOAD_NAMES
    assert all(workload.input >= 0 for workload in workloads)
    assert all(workload.expected >= 0 for workload in workloads)
    assert all(set(workload.sources) == {"merlo", "c", "rust"} for workload in workloads)
    assert all(
        len(digest) == 64
        for workload in workloads
        for digest in workload.source_sha256.values()
    )


def test_native_core_sample_summary_reports_median_and_mad() -> None:
    assert _summary([9, 10, 11, 100]) == {
        "samples_ns": [9, 10, 11, 100],
        "median_ns": 10.5,
        "mad_ns": 1.0,
        "relative_mad": 1 / 10.5,
    }


def test_checked_native_core_report_retains_all_raw_three_arm_evidence() -> None:
    report_path = (
        ROOT
        / "tools"
        / "benchmarks"
        / "merlo"
        / "benchmarks"
        / "merlo_native_core.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    workloads = {item.name: item for item in load_workloads(ROOT)}

    assert report["schema"] == "merlo.native-core-benchmark.v1"
    assert report["workload_count"] == len(WORKLOAD_NAMES)
    assert report["arms"] == ["merlo", "c", "rust"]
    assert report["passed"] is True
    assert all(report["gates"].values())
    ratios = []
    for observation in report["observations"]:
        workload = workloads[observation["name"]]
        assert observation["source_sha256"] == workload.source_sha256
        assert observation["expected_checksum"] == workload.expected
        assert set(observation["arms"]) == {"merlo", "c", "rust"}
        assert all(
            len(arm["samples_ns"]) == report["protocol"]["measurements"]
            for arm in observation["arms"].values()
        )
        ratios.append(observation["merlo_to_best_native_ratio"])
    assert report["geometric_mean_merlo_to_best_native_ratio"] == (
        math.prod(ratios) ** (1 / len(ratios))
    )
    assert report["maximum_merlo_to_best_native_ratio"] == max(ratios)
