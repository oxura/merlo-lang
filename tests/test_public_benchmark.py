from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from merlo import alpha_performance as alpha
from merlo import public_benchmark as public
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


def _artifacts(
    workloads: tuple[alpha.FrozenWorkload, ...],
) -> dict[str, dict[str, dict[str, object]]]:
    from merlo.productive_performance import (
        build_productive_runner_registry,
    )

    _, builds = build_productive_runner_registry(ROOT)
    return alpha._metadata_by_builds(workloads, builds)


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


def test_compiler_input_digest_changes_with_source(tmp_path: Path) -> None:
    package = tmp_path / "merlo"
    package.mkdir()
    source = package / "compiler.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    before = compiler_input_tree_sha256(tmp_path)
    source.write_text("VALUE = 2\n", encoding="utf-8")
    assert compiler_input_tree_sha256(tmp_path) != before


def test_controlled_runner_measures_all_denominators(tmp_path: Path) -> None:
    report = _measured_report(tmp_path)
    assert report["status"] == "MEASURED"
    assert report["passed"] is True
    assert len(report["raw_samples"]) == 3 * 4 * (alpha.DEFAULT_WARMUPS + alpha.DEFAULT_MEASURED_RUNS)
    assert all(len(sample["replicate_elapsed_ns"]) == alpha.DEFAULT_SAMPLE_REPLICATES for sample in report["raw_samples"] if sample["phase"] == "measured")
    validate_public_report(report)


def test_validator_rejects_public_contract_tampering(tmp_path: Path) -> None:
    report = _measured_report(tmp_path)

    non_measured_pass = copy.deepcopy(report)
    non_measured_pass["status"] = "UNMEASURED"
    with pytest.raises(PublicBenchmarkError):
        validate_public_report(non_measured_pass, root=ROOT)

    affinity = copy.deepcopy(report)
    affinity["affinity"]["applied"] = False
    with pytest.raises(PublicBenchmarkError):
        validate_public_report(affinity, root=ROOT)

    schedule = copy.deepcopy(report)
    schedule["randomized_schedule"]["rounds"][0]["arms"].reverse()
    with pytest.raises(PublicBenchmarkError):
        validate_public_report(schedule, root=ROOT)

    toolchain = copy.deepcopy(report)
    toolchain["toolchains"]["c"]["version_sha256"] = "0" * 64
    with pytest.raises(PublicBenchmarkError):
        validate_public_report(toolchain, root=ROOT)

    first_workload = next(iter(report["artifacts"].values()))
    first_measured = next(
        record
        for record in first_workload.values()
        if record.get("binary")
    )
    (ROOT / first_measured["binary"]).unlink()
    with pytest.raises(PublicBenchmarkError):
        validate_public_report(report, root=ROOT)


def test_lock_requires_complete_protocol(tmp_path: Path) -> None:
    lock_path = (
        tmp_path
        / "benchmarks"
        / "alpha_performance"
        / "workloads.json"
    )
    lock_path.parent.mkdir(parents=True)
    lock = json.loads(
        (
            ROOT
            / "benchmarks"
            / "alpha_performance"
            / "workloads.json"
        ).read_text()
    )
    del lock["protocol"]
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(PublicBenchmarkError):
        load_authoritative_workload_lock(tmp_path)


def test_release_adapter_rejects_mismatched_report_file(
    tmp_path: Path,
) -> None:
    report = _measured_report(tmp_path)
    report_path = tmp_path / "report.json"
    report_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ReleaseValidationError, match="disagrees"):
        public_benchmark_evidence(
            report,
            report_path,
            root=tmp_path,
            compiler_sha256=report["toolchains"]["c"]["binary_sha256"],
        )


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
    with pytest.raises(PublicBenchmarkError):
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


def test_controlled_environment_has_no_inherited_build_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LD_PRELOAD", "/tmp/injected.so")
    monkeypatch.setenv("CFLAGS", "-Dinjected")
    with public._controlled_environment():
        assert dict(os.environ) == public.CONTROLLED_BUILD_ENVIRONMENT


def test_unmeasured_partial_samples_still_receive_alpha_validation(
    tmp_path: Path,
) -> None:
    workloads, _ = load_authoritative_workload_lock(ROOT)
    registry = _registry(workloads)
    for workload in workloads:
        del registry[(workload.id, "c")]
    report = run_public_benchmark(
        ROOT,
        runner_registry=registry,
        artifact_metadata=_artifacts(workloads),
    )
    report["raw_samples"][0]["elapsed_ns"] += 1
    with pytest.raises(PublicBenchmarkError, match="alpha performance evidence"):
        validate_public_report(report, root=ROOT)


def test_native_artifacts_must_remain_executable(tmp_path: Path) -> None:
    report = _measured_report(tmp_path)
    record = next(
        record
        for arms in report["artifacts"].values()
        for arm, record in arms.items()
        if arm != "python" and record.get("binary")
    )
    binary = ROOT / record["binary"]
    original_mode = binary.stat().st_mode
    try:
        binary.chmod(0o644)
        with pytest.raises(PublicBenchmarkError, match="not executable"):
            validate_public_report(report, root=ROOT)
    finally:
        binary.chmod(original_mode)


def test_root_relative_artifact_symlink_is_rejected(tmp_path: Path) -> None:
    report = _measured_report(tmp_path)
    record = next(iter(next(iter(report["artifacts"].values())).values()))
    original = ROOT / record["source"][0]
    link = (
        ROOT
        / ".merlo"
        / "public-benchmark-v1"
        / "tests"
        / tmp_path.name
        / original.name
    )
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(original)
    record["source"][0] = link.relative_to(ROOT).as_posix()
    try:
        with pytest.raises(PublicBenchmarkError, match="symlinked"):
            validate_public_report(report, root=ROOT)
    finally:
        link.unlink()


def test_custom_root_is_preserved_when_writing_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _measured_report(tmp_path)
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "written-from-other-cwd.json"
    write_public_report(report, output, root=ROOT)
    assert output.read_bytes() == public.canonical_report_bytes(report)


def test_materialized_artifacts_are_content_addressed(tmp_path: Path) -> None:
    source = tmp_path / "app"
    source.write_bytes(b"first")
    source.chmod(0o755)
    first = public._retain_immutable_artifact(
        source,
        tmp_path / "retained",
        label="binary",
    )
    source.write_bytes(b"second")
    second = public._retain_immutable_artifact(
        source,
        tmp_path / "retained",
        label="binary",
    )
    assert first != second
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"


def test_release_adapter_rejects_lock_override(tmp_path: Path) -> None:
    report = _measured_report(tmp_path)
    report_path = (
        ROOT
        / ".merlo"
        / "public-benchmark-v1"
        / "tests"
        / tmp_path.name
        / "report.json"
    )
    write_public_report(report, report_path, root=ROOT)
    retained = json.loads(report_path.read_text())
    try:
        with pytest.raises(ReleaseValidationError, match="override disagrees"):
            public_benchmark_evidence(
                retained,
                report_path,
                root=ROOT,
                compiler_sha256=retained["toolchains"]["c"]["binary_sha256"],
                lock_sha256="0" * 64,
            )
    finally:
        report_path.unlink()
