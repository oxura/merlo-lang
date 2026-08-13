from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from merlo.native_c_backend import compile_c_source
from merlo.productive_performance import (
    APPLICATIONS,
    ARMS,
    ArmMeasurement,
    DEFAULT_MEASURED_RUNS,
    DEFAULT_WARMUPS,
    FROZEN_WORKLOADS,
    bootstrap_median_ci,
    build_productive_runner_registry,
    median_absolute_deviation,
    run_productive_performance,
    summarize_samples,
    validate_productive_performance_report,
)


def test_statistics_are_median_mad_and_seeded_bootstrap() -> None:
    samples = [1.0, 2.0, 3.0, 4.0]
    assert median_absolute_deviation(samples) == 1.0
    summary = summarize_samples(samples, seed=17, replicates=100)
    assert summary["median"] == 2.5
    assert summary["mad"] == 1.0
    assert summary["relative_mad"] == 0.4
    assert summary["bootstrap_median_95_ci"] == bootstrap_median_ci(samples, seed=17, replicates=100)


def test_empty_samples_are_honestly_unmeasured() -> None:
    summary = summarize_samples(())
    assert summary == {
        "samples": [],
        "sample_count": 0,
        "median": None,
        "mad": None,
        "relative_mad": None,
        "bootstrap_median_95_ci": None,
    }

def test_native_binary_is_reproducible_across_output_stems(tmp_path: Path) -> None:
    source = "#include <stdio.h>\nint main(void) { puts(\"ok\"); return 0; }\n"
    first = compile_c_source(source, output_dir=tmp_path / "first", stem="alpha")
    second = compile_c_source(source, output_dir=tmp_path / "second", stem="beta")
    assert first.status == second.status == "MEASURED"
    assert first.binary_sha256 == second.binary_sha256


def test_registry_builds_independent_merlo_arms_without_python_sidecars(monkeypatch: pytest.MonkeyPatch) -> None:
    import merlo.productive_performance as performance

    builder_calls: list[Path] = []
    canonical_builder_sources: list[str] = []
    subprocess_calls: list[list[str]] = []

    def fake_merlo_builder(
        entry: str | Path,
        *,
        emit_native: bool,
        output: str | Path,
        require_interface_lock: bool,
    ) -> object:
        assert emit_native is True
        del require_interface_lock
        entry_path = Path(entry)
        builder_calls.append(entry_path)
        if "canonical_roundtrip" in entry_path.parts:
            canonical_builder_sources.append(entry_path.read_text(encoding="utf-8"))
        binary = Path(output)
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(f"merlo:{entry_path}".encode())
        native = SimpleNamespace(
            status="MEASURED",
            binary_path=str(binary),
            binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
            compiler="fake",
            compiler_version="fake",
            command=("fake", str(binary)),
            stderr="",
            source_path=str(binary.with_suffix(".c")),
            source_sha256="generated",
        )
        return SimpleNamespace(
            native=native,
            elaborated=SimpleNamespace(
                canonical_source=(
                    "task main(path: Path) -> Result[Text,AppError]:\n"
                    "    return Ok(\"ok\")\n"
                ),
                tasks=(
                    SimpleNamespace(
                        name="main",
                        effects=("fs.read", "console.write"),
                    ),
                ),
            ),
        )

    def fake_native_builder(source: str, *, output_dir: str | Path, stem: str) -> object:
        binary = Path(output_dir) / stem
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(source.encode())
        return SimpleNamespace(
            status="MEASURED",
            binary_path=str(binary),
            binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
            compiler="fake",
            compiler_version="fake",
            command=("fake", str(binary)),
            stderr="",
            source_path=str(binary.with_suffix(".c")),
            source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        )

    def fake_subprocess_run(command: list[str], **_: object) -> object:
        subprocess_calls.append(command)
        return SimpleNamespace(returncode=0, stdout=b"shared-output", stderr=b"")

    monkeypatch.setattr(performance, "compile_project", fake_merlo_builder)
    monkeypatch.setattr(performance, "compile_c_source", fake_native_builder)
    monkeypatch.setattr(performance.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(
        performance,
        "_python_application_output",
        lambda application, path: b"python-output",
    )
    root = Path(__file__).parents[1]
    registry, builds = build_productive_runner_registry(root)

    assert set(registry) == {(application, arm) for application in APPLICATIONS for arm in ARMS}
    assert set(builds) == {
        f"{application}:{arm}"
        for application in APPLICATIONS
        for arm in ("concise_merlo", "canonical_merlo", "native", "python")
    }
    python_source = root / "merlo" / "productive_applications.py"
    python_source_digest = hashlib.sha256()
    python_source_digest.update(python_source.read_bytes())
    python_source_digest.update(b"\0")
    assert {
        builds[f"{application}:python"]["source_sha256"]
        for application in APPLICATIONS
    } == {python_source_digest.hexdigest()}
    assert all(path.name == "main.mlo" for path in builder_calls)
    assert all(path.parent.name == "app" for path in builder_calls)
    assert len({builds[f"{application}:concise_merlo"]["binary"] for application in APPLICATIONS}) == len(APPLICATIONS)
    assert len({builds[f"{application}:canonical_merlo"]["binary"] for application in APPLICATIONS}) == len(APPLICATIONS)
    assert len(canonical_builder_sources) == len(APPLICATIONS)
    assert all(
        source.startswith("module app.main\n")
        and "export task main(" in source
        and "uses fs.read, console.write" in source
        for source in canonical_builder_sources
    )

    workload = FROZEN_WORKLOADS[0]
    payload = (root / workload.fixture).read_bytes()
    python = registry[(workload.application, "python")](workload, payload)
    concise = registry[(workload.application, "concise_merlo")](workload, payload)
    canonical = registry[(workload.application, "canonical_merlo")](workload, payload)
    native = registry[(workload.application, "native")](workload, payload)
    assert python.result_digest == hashlib.sha256(b"python-output").hexdigest()
    assert concise.result_digest == canonical.result_digest == native.result_digest
    assert len(subprocess_calls) == 3
    assert len({call[0] for call in subprocess_calls}) == 3
    grep_workload = next(
        item for item in FROZEN_WORKLOADS if item.application == "grep"
    )
    grep_payload = (root / grep_workload.fixture).read_bytes()
    subprocess_calls.clear()
    registry[("grep", "concise_merlo")](grep_workload, grep_payload)
    registry[("grep", "native")](grep_workload, grep_payload)
    assert subprocess_calls[0] == [
        builds["grep:concise_merlo"]["binary"],
        str(root / grep_workload.fixture),
    ]
    assert subprocess_calls[1] == [
        builds["grep:native"]["binary"],
        str(root / grep_workload.fixture),
        "--contains",
        "needle",
    ]




def test_real_registry_builds_and_runs_every_merlo_arm() -> None:
    root = Path(__file__).parents[1]
    registry, builds = build_productive_runner_registry(root)

    assert set(registry) == {
        (application, arm)
        for application in APPLICATIONS
        for arm in ARMS
    }
    assert all(build["status"] == "MEASURED" for build in builds.values()), builds
    for key, build in builds.items():
        source_digest = hashlib.sha256()
        for source in build["source"]:
            source_digest.update((root / source).read_bytes())
            source_digest.update(b"\0")
        assert build["source_sha256"] == source_digest.hexdigest()
        if key.endswith(":python"):
            assert build["binary"] is None
            assert build["binary_sha256"] is None
            assert build["generated_source"] is None
            assert build["generated_source_sha256"] is None
            continue
        binary = Path(build["binary"])
        assert build["binary_sha256"] == hashlib.sha256(
            binary.read_bytes()
        ).hexdigest()
        generated_source = Path(build["generated_source"])
        assert build["generated_source_sha256"] == hashlib.sha256(
            generated_source.read_bytes()
        ).hexdigest()
    for application in APPLICATIONS:
        concise = builds[f"{application}:concise_merlo"]
        canonical = builds[f"{application}:canonical_merlo"]
        assert canonical["adapter"] == "canonical_expansion_roundtrip"
        assert canonical["generated_source_sha256"] == concise["generated_source_sha256"]
        assert canonical["binary_sha256"] == concise["binary_sha256"]
    for workload in FROZEN_WORKLOADS:
        payload = (root / workload.fixture).read_bytes()
        digests = {
            registry[(workload.application, arm)](workload, payload).result_digest
            for arm in ("concise_merlo", "canonical_merlo", "native")
        }
        assert len(digests) == 1


def test_small_run_has_frozen_workloads_randomized_schedule_and_four_arms() -> None:
    report = run_productive_performance(warmups=1, measured_runs=2, seed=41)

    assert report["status"] == "UNMEASURED"
    assert report["protocol"]["warmups"] == 1
    assert report["protocol"]["measured_runs"] == 2
    assert [item["application"] for item in report["applications"]] == list(APPLICATIONS)
    assert len(report["randomized_schedule"]["rounds"]) == 3
    for round_info in report["randomized_schedule"]["rounds"]:
        assert tuple(sorted(round_info["arms"])) == tuple(sorted(ARMS))
    for item in report["applications"]:
        assert tuple(item["arms"]) == ARMS
        assert item["workload"]["bytes"] >= 3_000
        assert item["workload"]["observation"]["status"] == "READY"
        assert item["arms"]["native"]["status"] == "UNMEASURED"
        assert item["arms"]["native"]["samples"] == []
    validate_productive_performance_report(report)


def test_defaults_and_mutation_lock_are_frozen() -> None:
    assert (DEFAULT_WARMUPS, DEFAULT_MEASURED_RUNS) == (5, 30)
    assert {item.application for item in FROZEN_WORKLOADS} == set(APPLICATIONS)
    report = run_productive_performance(warmups=0, measured_runs=1, seed=7)
    tampered = deepcopy(report)
    tampered["mutation_lock"]["digest"] = "tampered"
    with pytest.raises(ValueError, match="mutation lock"):
        validate_productive_performance_report(tampered)


def test_explicit_in_process_runners_measure_exact_runs_after_equivalence() -> None:
    def runner(workload: object, payload: bytes) -> ArmMeasurement:
        return ArmMeasurement(10.0, hashlib.sha256(payload).hexdigest())

    registry = {(application, arm): runner for application in APPLICATIONS for arm in ARMS}
    report = run_productive_performance(
        warmups=1,
        measured_runs=3,
        seed=19,
        runner_registry=registry,
    )

    assert report["status"] == "MEASURED"
    assert report["gates"]["artifact_result_equivalence"] is True
    assert report["passed"] is True
    for item in report["applications"]:
        assert item["result_equivalence"]["status"] == "MEASURED"
        assert all(arm["status"] == "MEASURED" for arm in item["arms"].values())
        assert all(arm["sample_count"] == 3 for arm in item["arms"].values())



def test_partial_python_native_report_is_measured_but_does_not_pass() -> None:
    def runner(workload: object, payload: bytes) -> ArmMeasurement:
        return ArmMeasurement(10.0, hashlib.sha256(payload).hexdigest())

    registry = {
        (application, arm): runner
        for application in APPLICATIONS
        for arm in ("python", "native")
    }
    report = run_productive_performance(
        warmups=0,
        measured_runs=2,
        seed=23,
        runner_registry=registry,
    )

    assert report["status"] == "MEASURED"
    assert report["passed"] is False
    assert report["gates"]["all_four_arms_measured"] is False
    validate_productive_performance_report(report)


def test_equivalence_mismatch_blocks_timing() -> None:
    def good_runner(workload: object, payload: bytes) -> ArmMeasurement:
        return ArmMeasurement(10.0, hashlib.sha256(payload).hexdigest())

    def bad_runner(workload: object, payload: bytes) -> ArmMeasurement:
        return ArmMeasurement(10.0, "wrong-result")

    registry = {(application, arm): good_runner for application in APPLICATIONS for arm in ARMS}
    registry[("csv", "native")] = bad_runner
    report = run_productive_performance(warmups=0, measured_runs=2, runner_registry=registry)
    csv = next(item for item in report["applications"] if item["application"] == "csv")
    assert csv["result_equivalence"]["status"] == "INVALID"
    assert csv["result_equivalence"]["reason"] == "RESULT_EQUIVALENCE_FAILED"
    assert all(arm["samples"] == [] for arm in csv["arms"].values())



def test_validator_rejects_forged_passed_value() -> None:
    report = run_productive_performance(warmups=0, measured_runs=1, seed=11)
    forged = deepcopy(report)
    forged["passed"] = True
    with pytest.raises(ValueError, match="passed"):
        validate_productive_performance_report(forged)


def test_validator_rejects_forged_status_value() -> None:
    report = run_productive_performance(warmups=0, measured_runs=1, seed=12)
    forged = deepcopy(report)
    forged["status"] = "MEASURED"
    with pytest.raises(ValueError, match="status"):
        validate_productive_performance_report(forged)


def test_validator_rejects_omitted_or_altered_gate() -> None:
    report = run_productive_performance(warmups=0, measured_runs=1, seed=13)
    omitted = deepcopy(report)
    del omitted["gates"]["all_four_arms_measured"]
    with pytest.raises(ValueError, match="gate"):
        validate_productive_performance_report(omitted)

    altered = deepcopy(report)
    altered["gates"]["all_four_arms_measured"] = True
    with pytest.raises(ValueError, match="gate"):
        validate_productive_performance_report(altered)
