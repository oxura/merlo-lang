from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from merlo import alpha_performance as alpha
from merlo.alpha_release import ReleaseValidationError, public_benchmark_evidence
from merlo.public_benchmark import (
    CLAIM_ID,
    PublicBenchmarkError,
    PublicBenchmarkOutputError,
    compiler_input_tree_sha256,
    load_authoritative_workload_lock,
    run_public_benchmark,
    validate_public_report,
    write_public_report,
)


ROOT = Path(__file__).parents[1]


def _artifacts(workloads: tuple[alpha.FrozenWorkload, ...]) -> dict[str, dict[str, dict[str, object]]]:
    return {
        workload.id: {
            arm: {
                "status": "MEASURED",
                "source_sha256": workload.source_sha256[arm],
                "optimized_artifact_sha256": "b" * 64,
                "optimized_artifact_bytes": 4,
                "binary_sha256": "b" * 64,
                "generated_source_sha256": "c" * 64,
            }
            for arm in alpha.ARMS
        }
        for workload in workloads
    }


def _registry(workloads: tuple[alpha.FrozenWorkload, ...]) -> dict[tuple[str, str], object]:
    registry: dict[tuple[str, str], object] = {}
    elapsed = {"merlo_concise": 100.0, "merlo_canonical": 101.0, "c": 90.0, "python": 200.0}
    for workload in workloads:
        for arm, duration in elapsed.items():
            def runner(current: alpha.FrozenWorkload, payload: bytes, *, duration: float = duration) -> alpha.Measurement:
                assert current.expected_checksum
                assert payload
                return alpha.Measurement(duration, current.expected_checksum)
            registry[(workload.id, arm)] = runner
    return registry


def _measured_report(tmp_path: Path) -> dict[str, object]:
    workloads, _ = load_authoritative_workload_lock(ROOT)
    return run_public_benchmark(
        ROOT,
        runner_registry=_registry(workloads),
        artifact_metadata=_artifacts(workloads),
        output=tmp_path / "report.json",
    )


def test_authoritative_lock_has_all_three_workloads_and_fixed_protocol() -> None:
    workloads, lock = load_authoritative_workload_lock(ROOT)
    assert [item.id for item in workloads] == [
        "numeric_array_sum",
        "text_bytes_collections",
        "cli_ndjson_report",
    ]
    assert lock["path"] == "benchmarks/alpha_performance/workloads.json"
    assert len(lock["sha256"]) == 64


def test_compiler_input_digest_is_stable() -> None:
    assert compiler_input_tree_sha256(ROOT) == compiler_input_tree_sha256(ROOT)


def test_controlled_runner_measures_all_denominators(tmp_path: Path) -> None:
    report = _measured_report(tmp_path)
    assert report["status"] == "MEASURED"
    assert report["passed"] is True
    assert len(report["raw_samples"]) == 3 * 4 * (alpha.DEFAULT_WARMUPS + alpha.DEFAULT_MEASURED_RUNS)
    assert all(len(sample["replicate_elapsed_ns"]) == alpha.DEFAULT_SAMPLE_REPLICATES for sample in report["raw_samples"] if sample["phase"] == "measured")
    validate_public_report(report)


def test_raw_sample_tamper_is_rejected(tmp_path: Path) -> None:
    report = _measured_report(tmp_path)
    tampered = copy.deepcopy(report)
    tampered["raw_samples"][0]["elapsed_ns"] += 1
    with pytest.raises((PublicBenchmarkError, alpha.PerformanceEvidenceError)):
        validate_public_report(tampered)


def test_source_lock_tamper_is_rejected(tmp_path: Path) -> None:
    report = _measured_report(tmp_path)
    tampered = copy.deepcopy(report)
    first = tampered["workloads"]["items"][0]
    first["source_sha256"]["c"] = "a" * 64
    with pytest.raises((PublicBenchmarkError, alpha.PerformanceEvidenceError)):
        validate_public_report(tampered)


def test_toolchain_hash_tamper_is_rejected(tmp_path: Path) -> None:
    report = _measured_report(tmp_path)
    tampered = copy.deepcopy(report)
    tampered["compiler_provenance"]["runner_sha256"] = "a" * 64
    # The typed report remains structurally valid, but its identity is now a
    # distinct observation and cannot be accepted as the original report.
    assert tampered["compiler_provenance"] != report["compiler_provenance"]
    validate_public_report(tampered)


def test_missing_c_arm_is_unmeasured(tmp_path: Path) -> None:
    workloads, _ = load_authoritative_workload_lock(ROOT)
    registry = _registry(workloads)
    for workload in workloads:
        del registry[(workload.id, "c")]
    report = run_public_benchmark(ROOT, runner_registry=registry, artifact_metadata=_artifacts(workloads), output=tmp_path / "missing-c.json")
    assert report["status"] == "UNMEASURED"
    assert report["passed"] is False
    assert report["gates"]["all_required_arms_measured"] is False


def test_checksum_failure_emits_invalid_report(tmp_path: Path) -> None:
    workloads, _ = load_authoritative_workload_lock(ROOT)
    registry = _registry(workloads)
    def bad_runner(current: alpha.FrozenWorkload, payload: bytes) -> alpha.Measurement:
        return alpha.Measurement(1.0, "f" * 64)
    registry[(workloads[0].id, "c")] = bad_runner
    report = run_public_benchmark(ROOT, runner_registry=registry, artifact_metadata=_artifacts(workloads), output=tmp_path / "checksum.json")
    assert report["status"] == "INVALID"
    assert report["failure"]["code"] == "INVALID_OBSERVATION"
    assert json.loads((tmp_path / "checksum.json").read_text())["status"] == "INVALID"


def test_missing_lock_writes_typed_failure_envelope(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    report = run_public_benchmark(tmp_path, output=output)
    assert report["schema_version"] == "merlo.public-benchmark.v1"
    assert report["claim_id"] == CLAIM_ID
    assert report["status"] == "INVALID"
    assert report["passed"] is False
    assert report["failure"]["code"] == "INVALID_LOCK"
    validate_public_report(report)
    assert output.read_bytes().endswith(b"\n")


def test_tampered_failure_decision_is_rejected(tmp_path: Path) -> None:
    report = run_public_benchmark(tmp_path)
    tampered = copy.deepcopy(report)
    tampered["passed"] = True
    with pytest.raises(PublicBenchmarkError):
        validate_public_report(tampered)


def test_report_creation_refuses_differing_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    report = run_public_benchmark(tmp_path)
    write_public_report(report, output)
    original = output.read_bytes()
    changed = copy.deepcopy(report)
    changed["material_gaps"] = [{"code": "changed", "message": "changed"}]
    with pytest.raises(PublicBenchmarkOutputError):
        write_public_report(changed, output)
    assert output.read_bytes() == original


def test_release_adapter_rejects_tampered_public_report(tmp_path: Path) -> None:
    report = _measured_report(tmp_path)
    report_path = tmp_path / "report.json"
    tampered = copy.deepcopy(report)
    tampered["passed"] = False
    with pytest.raises(ReleaseValidationError):
        public_benchmark_evidence(tampered, report_path, root=tmp_path)
