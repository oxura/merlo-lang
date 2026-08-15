from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest

from merlo.ai_evidence import AIRawTaskRecord
from merlo.compiler import compile_project
from merlo.frozen_evidence import (
    BEND_PUBLIC_REVISION,
    CONTRACT,
    METRIC_APPLICATION_BUILDS,
    METRIC_DETERMINISTIC_REPEATED_BUILDS,
    METRIC_SUPPORTED_GPU_RATIO,
    MINIMUM_APPLICATION_BUILDS,
    MINIMUM_SEMANTIC_EDIT_AUDITS,
    ApplicationBuildObservation,
    EvidenceStatus,
    FrozenEvidenceReport,
    FrozenEvidenceRunner,
    MemorySafetyCorpusCase,
    MetricMeasurement,
    SemanticEditAuditObservation,
    digest_text,
    run_memory_safety_corpus,
    validate_frozen_evidence,
)


def _digest(label: str) -> str:
    return digest_text(label)


def _observation(index: int) -> ApplicationBuildObservation:
    return ApplicationBuildObservation(
        f"app-{index:02d}",
        ("merlo", "build", f"app-{index:02d}"),
        _digest(f"source-{index}"),
        _digest(f"artifact-{index}"),
    )


def _capabilities(*, gpu: bool) -> dict[str, bool]:
    return {"scalar_cpu": True, "vector_cpu": False, "multicore_cpu": True, "gpu": gpu, "hvm": False}


def test_current_fifteen_application_examples_are_explicitly_unavailable() -> None:
    runner = FrozenEvidenceRunner(environment={"LC_ALL": "C"})
    measurement = runner.application_build_metric(tuple(_observation(i) for i in range(15)))
    assert measurement.status is EvidenceStatus.UNAVAILABLE
    assert measurement.samples == ()
    assert "required 20" in (measurement.reason or "")
    assert len(measurement.source_digests) == 15
    assert MINIMUM_APPLICATION_BUILDS == 20


def test_report_roundtrip_is_digest_bound_and_requires_every_metric() -> None:
    runner = FrozenEvidenceRunner(environment={"LC_ALL": "C"})
    report = runner.run(
        application_builds=tuple(_observation(i) for i in range(15)),
        capabilities=_capabilities(gpu=False),
    )
    assert report.contract == CONTRACT
    restored = validate_frozen_evidence(report.to_json())
    assert restored == report

    tampered = copy.deepcopy(report.to_dict())
    tampered["metrics"][0]["reason"] = "fabricated"
    with pytest.raises(ValueError, match="DigestMismatch|Digest"):
        FrozenEvidenceReport.from_dict(tampered)


def test_unsupported_gpu_is_unavailable_not_zero_or_pass() -> None:
    runner = FrozenEvidenceRunner(environment={"LC_ALL": "C"})
    gpu = runner.gpu_metric(samples=(1, 2, 3), ratio=(1, 1), capabilities=_capabilities(gpu=False))
    assert gpu.status is EvidenceStatus.UNAVAILABLE
    assert gpu.samples == ()
    assert gpu.ratio_numerator is None
    assert "GPUUnavailable" in (gpu.reason or "")


def test_injected_measurement_preserves_raw_samples_and_exact_ratio() -> None:
    measurement = MetricMeasurement.create(
        METRIC_SUPPORTED_GPU_RATIO,
        status=EvidenceStatus.MEASURED,
        command=("merlo", "gpu-check"),
        config={"adapter": "fixture"},
        source_digests={"program": _digest("program")},
        environment={"GPU_FIXTURE": "present"},
        artifact_digests={"binary": _digest("binary")},
        samples=(101, 103, 107),
        ratio=(2, 3),
    )
    assert measurement.samples == (101, 103, 107)
    assert measurement.ratio_numerator == 2
    assert measurement.ratio_denominator == 3
    assert measurement.to_dict()["config"] == {"adapter": "fixture"}
    assert measurement.to_dict()["environment"] == {"GPU_FIXTURE": "present"}
    assert MetricMeasurement.from_json(measurement.to_json()) == measurement


def test_deterministic_mismatch_is_measured_failure_not_invented_success() -> None:
    runner = FrozenEvidenceRunner(environment={"LC_ALL": "C"})
    result = runner.deterministic_build_metric((_digest("a"), _digest("b")))
    assert result.status is EvidenceStatus.MEASURED
    assert result.samples == (1, 1)
    assert result.reason == "ArtifactDigestMismatch"
    assert len(result.artifact_digests) == 2


def test_staleness_validator_rejects_changed_config() -> None:
    runner = FrozenEvidenceRunner(environment={"LC_ALL": "C"}, config={"compiler": "clang"})
    report = runner.run(application_builds=tuple(_observation(i) for i in range(15)), capabilities=_capabilities(gpu=False))
    with pytest.raises(ValueError, match="StaleEvidenceConfig"):
        report.validate_current(config_digests={METRIC_APPLICATION_BUILDS: _digest("changed")})


def test_missing_required_metric_is_rejected() -> None:
    runner = FrozenEvidenceRunner(environment={"LC_ALL": "C"})
    report = runner.run(application_builds=tuple(_observation(i) for i in range(15)), capabilities=_capabilities(gpu=False))
    payload = report.to_dict()
    payload["metrics"] = [item for item in payload["metrics"] if item["metric_id"] != METRIC_DETERMINISTIC_REPEATED_BUILDS]
    with pytest.raises(ValueError, match="RequiredMetricsIncomplete"):
        FrozenEvidenceReport.from_dict(payload)


def _ai_record(
    task_id: str,
    arm: str,
    *,
    context_tokens: int,
    repair_iterations: int,
    success: bool = True,
) -> AIRawTaskRecord:
    prompt_hash = _digest(f"{task_id}:prompt")
    task_hash = _digest(f"{task_id}:task")
    dataset_hash = _digest(f"{task_id}:dataset")
    raw = {
        "tokenizer": "fixture-tokenizer",
        "accounting_contract": "provider-total-v1",
        "repair_iterations": repair_iterations,
        "oracle_passed": success,
        "output": "accepted" if success else "rejected",
    }
    return AIRawTaskRecord(
        task_id,
        arm,
        "fixture-provider",
        "fixture-model",
        "fixture-revision",
        prompt_hash,
        task_hash,
        dataset_hash,
        "fixture-oracle",
        success,
        context_tokens,
        1,
        context_tokens,
        repair_iterations,
        f"{task_id}:{arm}",
        _digest(f"{task_id}:{arm}:output"),
        raw=raw,
    )


def test_duplicate_application_ids_cannot_satisfy_twenty_build_gate() -> None:
    runner = FrozenEvidenceRunner(environment={})
    duplicate = _observation(0)
    measurement = runner.application_build_metric((duplicate,) * MINIMUM_APPLICATION_BUILDS)
    assert measurement.status is EvidenceStatus.UNAVAILABLE
    assert len(measurement.source_digests) == 1
    assert "observed 1 unique" in (measurement.reason or "")


def test_single_and_multicore_collectors_retain_paired_raw_samples(tmp_path: Path) -> None:
    runner = FrozenEvidenceRunner(root=tmp_path, environment={"LC_ALL": "C"})
    executable = Path("/bin/true")
    if not executable.is_file() or not hasattr(os, "sched_getaffinity"):
        pytest.skip("Linux process affinity and /bin/true are required")
    single = runner.collect_single_core_native_ratio(
        (str(executable),),
        (str(executable),),
        repetitions=2,
        warmups=0,
        cwd=tmp_path,
    )
    assert single.status is EvidenceStatus.MEASURED
    assert len(single.samples) == 4
    assert single.ratio_numerator is not None
    assert single.ratio_denominator is not None

    multicore = runner.collect_multicore_scaling(
        lambda cores, _affinity: 100 if cores == 1 else 25,
        configured_core_count=4,
        repetitions=2,
        warmups=0,
        affinity=(0, 1, 2, 3),
    )
    assert multicore.status is EvidenceStatus.MEASURED
    assert multicore.samples == (100, 25, 100, 25)
    assert (multicore.ratio_numerator, multicore.ratio_denominator) == (200, 200)


def test_gpu_measurement_requires_supported_device_probe_and_raw_pairs() -> None:
    runner = FrozenEvidenceRunner(environment={})
    capabilities = {
        "scalar_cpu": True,
        "vector_cpu": False,
        "multicore_cpu": True,
        "gpu": True,
        "hvm": False,
    }
    probe = {
        "available": True,
        "provider": "fixture",
        "version": "1",
        "device": "fixture-device",
        "runtime": "fixture-runtime",
        "metadata": {"device_count": 1},
    }
    measurement = runner.gpu_metric(
        capabilities=capabilities,
        backend_probe=probe,
        paired_samples=((10, 20), (12, 24), (11, 22)),
    )
    assert measurement.status is EvidenceStatus.MEASURED
    assert measurement.samples == (10, 20, 12, 24, 11, 22)
    assert (measurement.ratio_numerator, measurement.ratio_denominator) == (11, 22)


def test_proof_closure_is_derived_from_compiler_artifacts() -> None:
    root = Path(__file__).parents[1]
    compilation = compile_project(
        root / "examples" / "access-log",
        require_interface_lock=False,
    )
    measurement = FrozenEvidenceRunner(environment={}).proof_closure_metric_from_compilations(
        {"access-log": compilation}
    )
    assert measurement.status is EvidenceStatus.MEASURED
    assert measurement.samples == (
        compilation.verification_metrics.automatically_closed,
        compilation.verification_metrics.total_obligations,
    )
    assert dict(measurement.artifact_digests)["access-log:obligations"]


def test_ai_context_and_repair_metrics_require_paired_raw_records() -> None:
    records = (
        _ai_record("one", "semantic", context_tokens=10, repair_iterations=1),
        _ai_record("one", "text", context_tokens=30, repair_iterations=3),
        _ai_record("two", "semantic", context_tokens=20, repair_iterations=2),
        _ai_record("two", "text", context_tokens=40, repair_iterations=4),
    )
    runner = FrozenEvidenceRunner(environment={})
    context = runner.ai_context_reduction_metric(records=records)
    assert context.status is EvidenceStatus.MEASURED
    assert context.samples == (30, 10, 40, 20)
    assert (context.ratio_numerator, context.ratio_denominator) == (40, 70)

    repairs = runner.repair_iteration_metric(records)
    assert repairs.status is EvidenceStatus.MEASURED
    assert repairs.samples == (1, 3, 2, 4)
    assert (repairs.ratio_numerator, repairs.ratio_denominator) == (4, 7)


def test_semantic_edit_audit_requires_three_hundred_digest_bound_edits() -> None:
    observations = tuple(
        SemanticEditAuditObservation(
            f"edit-{index:03d}",
            _digest(f"operation:{index}"),
            _digest(f"before:{index}"),
            _digest(f"after:{index}"),
            ("module.target",),
            ("module.target",),
        )
        for index in range(MINIMUM_SEMANTIC_EDIT_AUDITS)
    )
    runner = FrozenEvidenceRunner(environment={})
    measurement = runner.unrelated_semantic_edit_metric(observations, seed=1729)
    assert measurement.status is EvidenceStatus.MEASURED
    assert measurement.samples == (0,) * MINIMUM_SEMANTIC_EDIT_AUDITS
    assert measurement.reason is None

    incomplete = runner.unrelated_semantic_edit_metric(observations[:-1], seed=1729)
    assert incomplete.status is EvidenceStatus.UNAVAILABLE
    assert incomplete.samples == ()


def test_unsupported_memory_sanitizer_is_explicitly_unavailable(tmp_path: Path) -> None:
    source = tmp_path / "safe.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    result = run_memory_safety_corpus(
        (MemorySafetyCorpusCase("safe", source),),
        root=tmp_path,
        sanitizers=("not-a-sanitizer",),
        environment={"LC_ALL": "C"},
        output_dir=tmp_path / "out",
    )
    assert result.status is EvidenceStatus.UNAVAILABLE
    assert "UnsupportedSanitizer" in (result.reason or "")
    measurement = FrozenEvidenceRunner(environment={}).memory_safety_metric(result=result)
    assert measurement.status is EvidenceStatus.UNAVAILABLE
    assert measurement.samples == ()


def test_repeated_builds_do_not_hide_unavailable_repetitions() -> None:
    measured = {
        "application_id": "app",
        "repeat": 0,
        "status": EvidenceStatus.MEASURED.value,
        "semantic_digests": {"hir": _digest("hir")},
        "artifact_roles": {"binary": "/tmp/app-0"},
        "artifact_digests": {"/tmp/app-0": _digest("binary")},
    }
    unavailable = {
        "application_id": "app",
        "repeat": 1,
        "status": EvidenceStatus.UNAVAILABLE.value,
        "reason": "CompilerUnavailable",
    }
    measurement = FrozenEvidenceRunner(environment={}).deterministic_build_metric(
        repeated_builds=(measured, unavailable)
    )
    assert measurement.status is EvidenceStatus.UNAVAILABLE
    assert "CompilerUnavailable" in (measurement.reason or "")


def test_bend_measurement_is_bound_to_verified_public_revision() -> None:
    runner = FrozenEvidenceRunner(environment={})
    remote = f"{BEND_PUBLIC_REVISION}\tHEAD\n"
    measurement = runner.bend_comparison_metric(
        samples=(100, 200),
        ratio=(1, 2),
        remote_result=remote,
    )
    assert measurement.status is EvidenceStatus.MEASURED
    assert (measurement.ratio_numerator, measurement.ratio_denominator) == (1, 2)
    rejected = runner.bend_comparison_metric(
        samples=(100, 200),
        ratio=(1, 2),
        remote_result=f"{'0' * 40}\tHEAD\n",
    )
    assert rejected.status is EvidenceStatus.UNAVAILABLE
    assert "RemoteRevisionMismatch" in (rejected.reason or "")
