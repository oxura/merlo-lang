from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from tools.benchmarks.merlo.alpha_performance import (
    ARMS,
    BOOTSTRAP_REPLICATES,
    DEFAULT_SAMPLE_REPLICATES,
    FROZEN_WORKLOADS,
    FrozenWorkload,
    Measurement,
    PerformanceEvidenceError,
    bootstrap_median_ci,
    load_raw_report,
    median_absolute_deviation,
    run_alpha_performance,
    summarize_samples,
    validate_alpha_performance_report,
    write_raw_report,
)


def test_frozen_python_source_lock_tracks_reference_implementation() -> None:
    root = Path(__file__).parents[4]
    digest = hashlib.sha256()
    digest.update((root / "tools" / "benchmarks" / "merlo" / "productive_applications.py").read_bytes())
    digest.update(b"\0")

    assert {
        workload.source_sha256["python"]
        for workload in FROZEN_WORKLOADS
    } == {digest.hexdigest()}


def _fixture(tmp_path: Path) -> FrozenWorkload:
    path = tmp_path / "input.txt"
    payload = b"alpha performance fixture\n"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    return FrozenWorkload(
        "tiny", "text_bytes_collections", str(path), len(payload), digest, hashlib.sha256(b"expected").hexdigest(),
        "scan text and bytes", {arm: "a" * 64 for arm in ARMS}, "grep",
    )


def _artifacts(workload_id: str) -> dict[str, dict[str, dict[str, object]]]:
    return {
        workload_id: {
            arm: {
                "source_sha256": "a" * 64,
                "optimized_artifact_sha256": "b" * 64,
                "optimized_artifact_bytes": 4,
            }
            for arm in ARMS
        }
    }


def test_mad_and_bootstrap_are_deterministic() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert median_absolute_deviation(values) == 1.0
    assert bootstrap_median_ci(values, seed=7, replicates=31) == bootstrap_median_ci(values, seed=7, replicates=31)
    assert summarize_samples(values, seed=7, replicates=31)["sample_count"] == 4


def test_schedule_is_sequential_and_preserves_all_samples(tmp_path: Path) -> None:
    workload = _fixture(tmp_path)
    calls: list[str] = []

    def runner(current: FrozenWorkload, payload: bytes) -> Measurement:
        assert current is workload
        assert payload
        calls.append(current.id)
        return Measurement(
            10.0,
            current.expected_checksum,
            startup_ns=2.0,
            peak_rss_kb=32,
        )

    registry = {(workload.id, arm): runner for arm in ARMS if arm != "rust"}
    report = run_alpha_performance(
        tmp_path,
        workloads=(workload,),
        runner_registry=registry,
        artifact_metadata=_artifacts(workload.id),
        warmups=2,
        measured_runs=3,
        seed=11,
    )
    validate_alpha_performance_report(report)
    assert len(calls) == 4 * (2 + 3 * DEFAULT_SAMPLE_REPLICATES)
    assert len(report["raw_samples"]) == 4 * (2 + 3)
    measured = [
        sample for sample in report["raw_samples"] if sample["phase"] == "measured"
    ]
    assert all(
        len(sample["replicate_elapsed_ns"]) == DEFAULT_SAMPLE_REPLICATES
        for sample in measured
    )
    assert report["applications"][0]["arms"]["merlo_concise"]["sample_count"] == 3
    assert report["gates"]["sequential_schedule"]

def test_measured_sample_uses_minimum_replicate_and_preserves_all(
    tmp_path: Path,
) -> None:
    workload = _fixture(tmp_path)
    calls = {
        arm: 0 for arm in ("merlo_concise", "merlo_canonical", "c", "python")
    }

    def make_runner(arm: str):
        def runner(current: FrozenWorkload, payload: bytes) -> Measurement:
            del payload
            elapsed = float(DEFAULT_SAMPLE_REPLICATES - calls[arm])
            calls[arm] += 1
            return Measurement(elapsed, current.expected_checksum)

        return runner

    registry = {
        (workload.id, arm): make_runner(arm)
        for arm in ("merlo_concise", "merlo_canonical", "c", "python")
    }
    report = run_alpha_performance(
        tmp_path,
        workloads=(workload,),
        runner_registry=registry,
        artifact_metadata=_artifacts(workload.id),
        warmups=0,
        measured_runs=1,
    )
    for arm in registry:
        record = report["applications"][0]["arms"][arm[1]]
        assert record["samples"] == [1.0]
    assert all(
        len(sample["replicate_elapsed_ns"]) == DEFAULT_SAMPLE_REPLICATES
        for sample in report["raw_samples"]
    )


def test_optional_rust_unavailable_is_explicit(tmp_path: Path) -> None:
    workload = _fixture(tmp_path)

    def runner(current: FrozenWorkload, payload: bytes) -> Measurement:
        return Measurement(10.0, current.expected_checksum)

    registry = {(workload.id, arm): runner for arm in ("merlo_concise", "merlo_canonical", "c", "python")}
    report = run_alpha_performance(tmp_path, workloads=(workload,), runner_registry=registry, artifact_metadata=_artifacts(workload.id), warmups=1, measured_runs=2)
    rust = report["applications"][0]["arms"]["rust"]
    assert rust["status"] == "UNAVAILABLE"
    assert rust["reason"] == "OPTIONAL_TOOLCHAIN_UNAVAILABLE"
    assert report["gates"]["rust_optional"]


def test_checksum_and_raw_sample_tampering_are_rejected(tmp_path: Path) -> None:
    workload = _fixture(tmp_path)

    def runner(current: FrozenWorkload, payload: bytes) -> Measurement:
        return Measurement(10.0, current.expected_checksum)

    registry = {(workload.id, arm): runner for arm in ("merlo_concise", "merlo_canonical", "c", "python")}
    report = run_alpha_performance(tmp_path, workloads=(workload,), runner_registry=registry, artifact_metadata=_artifacts(workload.id), warmups=1, measured_runs=2)
    changed = copy.deepcopy(report)
    changed["raw_samples"][0]["checksum"] = "tampered"
    with pytest.raises(PerformanceEvidenceError, match="tampered"):
        validate_alpha_performance_report(changed)


def test_artifact_source_tampering_is_rejected(tmp_path: Path) -> None:
    workload = _fixture(tmp_path)

    def runner(current: FrozenWorkload, payload: bytes) -> Measurement:
        return Measurement(10.0, current.expected_checksum)

    registry = {
        (workload.id, arm): runner
        for arm in ("merlo_concise", "merlo_canonical", "c", "python")
    }
    report = run_alpha_performance(
        tmp_path,
        workloads=(workload,),
        runner_registry=registry,
        artifact_metadata=_artifacts(workload.id),
        warmups=1,
        measured_runs=2,
    )
    changed = copy.deepcopy(report)
    changed["artifacts"][workload.id]["merlo_concise"]["source_sha256"] = "c" * 64
    with pytest.raises(PerformanceEvidenceError, match="artifact source lock mismatch"):
        validate_alpha_performance_report(changed)

def test_environment_snapshot_excludes_process_secrets_and_private_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.benchmarks.merlo.alpha_performance as performance

    monkeypatch.setenv("FIREWORKS_API_KEY", "secret")
    monkeypatch.setenv("HOME", "/home/private-user")
    snapshot = performance._environment_snapshot()

    assert "env" not in snapshot
    assert "cwd" not in snapshot
    assert snapshot["python_executable"] == Path(snapshot["python_executable"]).name


def test_raw_report_round_trip_preserves_environment_and_samples(tmp_path: Path) -> None:
    workload = _fixture(tmp_path)

    def runner(current: FrozenWorkload, payload: bytes) -> Measurement:
        return Measurement(10.0, current.expected_checksum, startup_ns=1.0, peak_rss_kb=12)

    registry = {(workload.id, arm): runner for arm in ("merlo_concise", "merlo_canonical", "c", "python")}
    report = run_alpha_performance(tmp_path, workloads=(workload,), runner_registry=registry, artifact_metadata=_artifacts(workload.id), warmups=1, measured_runs=2)
    output = tmp_path / "raw.json"
    write_raw_report(report, output)
    loaded = load_raw_report(output)
    assert loaded["environment"] == report["environment"]
    assert loaded["raw_samples"] == report["raw_samples"]
    assert loaded["protocol"]["bootstrap_replicates"] == BOOTSTRAP_REPLICATES
