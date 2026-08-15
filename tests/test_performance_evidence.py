from __future__ import annotations

import json

import pytest

from merlo.performance_evidence import (
    PERFORMANCE_EVIDENCE_CONTRACT,
    PerformanceEvidenceManifest,
    assert_compatible,
    classify_performance,
    compare_performance,
    percentile_ns,
    summarize_samples,
)


_HASHES = {
    "workload_digest": "1" * 64,
    "compiler_digest": "2" * 64,
    "artifact_digest": "3" * 64,
    "output_digest": "4" * 64,
}


def _manifest(*, samples=(80, 100, 120), baseline=(200, 220, 240), **changes):
    values = {
        "workload_id": "json/ndjson",
        **_HASHES,
        "backend": "native-c",
        "hardware_profile": {"architecture": "x86_64", "cpu": "test-cpu"},
        "repetitions": len(samples),
        "samples_ns": samples,
        "warmup_count": 2,
        "scalar_baseline_samples_ns": baseline,
        "output_digest_parity": True,
        "environment": {"kernel": "test", "os": "linux"},
    }
    values.update(changes)
    return PerformanceEvidenceManifest(**values)


def test_manifest_roundtrip_and_digest_are_deterministic() -> None:
    first = _manifest()
    second = PerformanceEvidenceManifest.from_json(first.to_json())
    assert first.to_json() == second.to_json()
    assert first.to_dict() == second.to_dict()
    assert first.digest == second.digest
    assert first.contract == PERFORMANCE_EVIDENCE_CONTRACT
    assert first.hardware_profile == (("architecture", "x86_64"), ("cpu", "test-cpu"))


def test_percentiles_use_integer_nearest_rank_boundaries() -> None:
    values = (9, 1, 5, 3)
    assert percentile_ns(values, 0) == 1
    assert percentile_ns(values, 0.25) == 1
    assert percentile_ns(values, 0.5) == 3
    assert percentile_ns(values, 0.75) == 5
    assert percentile_ns(values, 1) == 9
    summary = summarize_samples(values)
    assert summary.median_ns == 3
    assert summary.p95_ns == 9
    assert summary.to_dict()["count"] == 4


def test_output_and_environment_mismatches_are_rejected() -> None:
    baseline = _manifest(samples=(200, 210, 220), baseline=(200, 210, 220))
    with pytest.raises(ValueError, match="OutputDigestMismatch"):
        assert_compatible(_manifest(output_digest="5" * 64), baseline)
    with pytest.raises(ValueError, match="EnvironmentMismatch"):
        assert_compatible(_manifest(environment={"kernel": "other", "os": "linux"}), baseline)
    with pytest.raises(ValueError, match="WorkloadMismatch"):
        assert_compatible(_manifest(workload_id="other"), baseline)


def test_tamper_and_nonfinite_payloads_are_rejected() -> None:
    payload = json.loads(_manifest().to_json())
    payload["samples_ns"][0] = 101
    changed = PerformanceEvidenceManifest.from_dict(payload)
    assert changed.digest != _manifest().digest
    payload["samples_ns"][0] = float("nan")
    with pytest.raises(ValueError, match="InvalidSamples"):
        PerformanceEvidenceManifest.from_dict(payload)
    payload = json.loads(_manifest().to_json())
    payload["environment"]["x"] = float("inf")
    with pytest.raises(ValueError, match="InvalidEnvironment"):
        PerformanceEvidenceManifest.from_dict(payload)
    with pytest.raises(ValueError, match="UnknownPerformanceEvidenceField"):
        PerformanceEvidenceManifest.from_dict({**json.loads(_manifest().to_json()), "extra": 1})


def test_classification_uses_explicit_thresholds_and_exact_speedup() -> None:
    improved = _manifest(samples=(80, 90, 100), baseline=(200, 210, 220))
    result = compare_performance(improved, minimum_samples=3, improvement_threshold="0.20")
    assert result.classification == "improved"
    assert result.speedup_numerator == 210
    assert result.speedup_denominator == 90
    assert classify_performance(_manifest(samples=(190, 200, 210), baseline=(200, 210, 220)), minimum_samples=3) == "parity"
    regressed = _manifest(samples=(300, 310, 320), baseline=(200, 210, 220))
    assert compare_performance(regressed, minimum_samples=3).classification == "regressed"


def test_comparison_rejects_insufficient_samples_without_fabrication() -> None:
    short = _manifest(samples=(100, 110), baseline=(200, 210))
    with pytest.raises(ValueError, match="InsufficientCandidateSamples"):
        compare_performance(short)
    with pytest.raises(ValueError, match="InsufficientBaselineSamples"):
        compare_performance(_manifest(samples=(100, 110, 120), baseline=(200,)), minimum_samples=3)
