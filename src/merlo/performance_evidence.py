"""Strict, deterministic performance evidence manifests.

The module intentionally stores observations as integer nanoseconds and derives
all summaries from those observations. A manifest binds the workload and output
to the exact compiler/artifact, backend, machine, and environment that
produced the measurements. No clock, random seed, or floating-point
measurement is introduced here.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Mapping, Sequence

PERFORMANCE_EVIDENCE_SCHEMA_VERSION = 1
PERFORMANCE_EVIDENCE_CONTRACT = "merlo.performance-evidence.v1"
MINIMUM_SAMPLES = 3

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_MANIFEST_FIELDS = frozenset(
    {
        "schema_version", "contract", "workload_id", "workload_digest",
        "compiler_digest", "artifact_digest", "backend", "hardware_profile",
        "repetitions", "samples_ns", "warmup_count", "scalar_baseline_samples_ns",
        "output_digest", "output_digest_parity", "environment",
    }
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_digest(value: Any, code: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(code)
    return value


def _require_text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(code)
    return value


def _immutable_fields(value: Mapping[str, Any], code: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        raise ValueError(code)
    items: list[tuple[str, str]] = []
    for key, item in value.items():
        if not isinstance(key, str) or not key or not isinstance(item, str) or not item:
            raise ValueError(code)
        items.append((key, item))
    items.sort()
    return tuple(items)


def _fields_dict(value: tuple[tuple[str, str], ...]) -> dict[str, str]:
    return {key: item for key, item in value}


def _integer_samples(value: Any, code: str, *, allow_empty: bool = False) -> tuple[int, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise ValueError(code)
    try:
        result = tuple(value)
    except TypeError as exc:
        raise ValueError(code) from exc
    if not result and not allow_empty:
        raise ValueError(code)
    if any(not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in result):
        raise ValueError(code)
    return result


def _fraction(value: int | float | str | Fraction, code: str) -> Fraction:
    if isinstance(value, bool):
        raise ValueError(code)
    try:
        if isinstance(value, Fraction):
            result = value
        elif isinstance(value, int):
            result = Fraction(value, 1)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError(code)
            result = Fraction(str(value))
        elif isinstance(value, str):
            result = Fraction(value)
        else:
            raise ValueError(code)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(code) from exc
    return result


@dataclass(frozen=True)
class ExactRatio:
    """A positive exact ratio represented without a platform float."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.numerator, int) or isinstance(self.numerator, bool)
            or self.numerator < 1 or not isinstance(self.denominator, int)
            or isinstance(self.denominator, bool) or self.denominator < 1
        ):
            raise ValueError("InvalidRatio")

    def to_dict(self) -> dict[str, int]:
        return {"numerator": self.numerator, "denominator": self.denominator}

    @property
    def value(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)


@dataclass(frozen=True)
class SampleSummary:
    """Deterministic integer summaries of one sample sequence."""

    count: int
    minimum_ns: int
    p25_ns: int
    median_ns: int
    p75_ns: int
    p90_ns: int
    p95_ns: int
    p99_ns: int
    maximum_ns: int

    def to_dict(self) -> dict[str, int]:
        return {
            "count": self.count, "minimum_ns": self.minimum_ns,
            "p25_ns": self.p25_ns, "median_ns": self.median_ns,
            "p75_ns": self.p75_ns, "p90_ns": self.p90_ns,
            "p95_ns": self.p95_ns, "p99_ns": self.p99_ns,
            "maximum_ns": self.maximum_ns,
        }


@dataclass(frozen=True)
class PerformanceEvidenceManifest:
    """Canonical evidence for one measured compiler/artifact workload arm."""

    workload_id: str
    workload_digest: str
    compiler_digest: str
    artifact_digest: str
    backend: str
    hardware_profile: Mapping[str, str]
    repetitions: int
    samples_ns: tuple[int, ...]
    warmup_count: int
    scalar_baseline_samples_ns: tuple[int, ...]
    output_digest: str
    output_digest_parity: bool
    environment: Mapping[str, str]
    schema_version: int = PERFORMANCE_EVIDENCE_SCHEMA_VERSION
    contract: str = PERFORMANCE_EVIDENCE_CONTRACT

    def __post_init__(self) -> None:
        if self.schema_version != PERFORMANCE_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("PerformanceEvidenceSchemaVersionMismatch")
        if self.contract != PERFORMANCE_EVIDENCE_CONTRACT:
            raise ValueError("PerformanceEvidenceContractMismatch")
        object.__setattr__(self, "workload_id", _require_text(self.workload_id, "InvalidWorkloadId"))
        object.__setattr__(self, "workload_digest", _require_digest(self.workload_digest, "InvalidWorkloadDigest"))
        object.__setattr__(self, "compiler_digest", _require_digest(self.compiler_digest, "InvalidCompilerDigest"))
        object.__setattr__(self, "artifact_digest", _require_digest(self.artifact_digest, "InvalidArtifactDigest"))
        object.__setattr__(self, "output_digest", _require_digest(self.output_digest, "InvalidOutputDigest"))
        object.__setattr__(self, "backend", _require_text(self.backend, "InvalidBackend"))
        if not isinstance(self.repetitions, int) or isinstance(self.repetitions, bool) or self.repetitions < 1:
            raise ValueError("InvalidRepetitions")
        if not isinstance(self.warmup_count, int) or isinstance(self.warmup_count, bool) or self.warmup_count < 0:
            raise ValueError("InvalidWarmupCount")
        samples = _integer_samples(self.samples_ns, "InvalidSamples")
        if len(samples) != self.repetitions:
            raise ValueError("RepetitionsSamplesMismatch")
        baseline = _integer_samples(self.scalar_baseline_samples_ns, "InvalidScalarBaselineSamples")
        if not isinstance(self.output_digest_parity, bool):
            raise ValueError("InvalidOutputDigestParity")
        if not self.output_digest_parity:
            raise ValueError("OutputDigestParityRequired")
        object.__setattr__(self, "samples_ns", samples)
        object.__setattr__(self, "scalar_baseline_samples_ns", baseline)
        object.__setattr__(self, "hardware_profile", _immutable_fields(self.hardware_profile, "InvalidHardwareProfile"))
        object.__setattr__(self, "environment", _immutable_fields(self.environment, "InvalidEnvironment"))

    @property
    def digest(self) -> str:
        return _digest(self.to_json())

    @property
    def summary(self) -> SampleSummary:
        return summarize_samples(self.samples_ns)

    @property
    def scalar_baseline_summary(self) -> SampleSummary:
        return summarize_samples(self.scalar_baseline_samples_ns)

    @property
    def baseline_samples_ns(self) -> tuple[int, ...]:
        return self.scalar_baseline_samples_ns

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "contract": self.contract,
            "workload_id": self.workload_id, "workload_digest": self.workload_digest,
            "compiler_digest": self.compiler_digest, "artifact_digest": self.artifact_digest,
            "backend": self.backend, "hardware_profile": _fields_dict(self.hardware_profile),
            "repetitions": self.repetitions, "samples_ns": list(self.samples_ns),
            "warmup_count": self.warmup_count,
            "scalar_baseline_samples_ns": list(self.scalar_baseline_samples_ns),
            "output_digest": self.output_digest,
            "output_digest_parity": self.output_digest_parity,
            "environment": _fields_dict(self.environment),
        }

    def to_json(self) -> str:
        return _canonical(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PerformanceEvidenceManifest":
        if not isinstance(value, Mapping):
            raise ValueError("InvalidPerformanceEvidenceManifest")
        keys = frozenset(value)
        if keys - _ALLOWED_MANIFEST_FIELDS:
            raise ValueError("UnknownPerformanceEvidenceField")
        if _ALLOWED_MANIFEST_FIELDS - keys:
            raise ValueError("MissingPerformanceEvidenceField")
        if not isinstance(value["schema_version"], int) or isinstance(value["schema_version"], bool):
            raise ValueError("InvalidPerformanceEvidenceSchemaVersion")
        if not isinstance(value["contract"], str):
            raise ValueError("InvalidPerformanceEvidenceContract")
        return cls(
            workload_id=value["workload_id"], workload_digest=value["workload_digest"],
            compiler_digest=value["compiler_digest"], artifact_digest=value["artifact_digest"],
            backend=value["backend"], hardware_profile=value["hardware_profile"],
            repetitions=value["repetitions"], samples_ns=value["samples_ns"],
            warmup_count=value["warmup_count"], scalar_baseline_samples_ns=value["scalar_baseline_samples_ns"],
            output_digest=value["output_digest"], output_digest_parity=value["output_digest_parity"],
            environment=value["environment"], schema_version=value["schema_version"], contract=value["contract"],
        )

    @classmethod
    def from_json(cls, payload: str) -> "PerformanceEvidenceManifest":
        if not isinstance(payload, str):
            raise ValueError("InvalidPerformanceEvidenceJSON")
        try:
            value = json.loads(payload, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("InvalidPerformanceEvidenceJSON") from exc
        return cls.from_dict(value)


PerformanceManifest = PerformanceEvidenceManifest


def percentile_ns(samples: Sequence[int], percentile: int | float | str | Fraction) -> int:
    """Return a nearest-rank integer percentile, including exact boundaries."""
    values = _integer_samples(samples, "InvalidPercentileSamples")
    fraction = _fraction(percentile, "InvalidPercentile")
    if fraction < 0 or fraction > 1:
        raise ValueError("PercentileOutOfRange")
    rank = max(1, (fraction.numerator * len(values) + fraction.denominator - 1) // fraction.denominator)
    return sorted(values)[rank - 1]


def summarize_samples(samples: Sequence[int]) -> SampleSummary:
    values = _integer_samples(samples, "InvalidSummarySamples")
    return SampleSummary(
        count=len(values), minimum_ns=min(values),
        p25_ns=percentile_ns(values, Fraction(1, 4)),
        median_ns=percentile_ns(values, Fraction(1, 2)),
        p75_ns=percentile_ns(values, Fraction(3, 4)),
        p90_ns=percentile_ns(values, Fraction(9, 10)),
        p95_ns=percentile_ns(values, Fraction(19, 20)),
        p99_ns=percentile_ns(values, Fraction(99, 100)), maximum_ns=max(values),
    )


@dataclass(frozen=True)
class PerformanceComparison:
    candidate_digest: str
    baseline_digest: str
    candidate_median_ns: int
    baseline_median_ns: int
    speedup: ExactRatio
    classification: str
    threshold_name: str

    def __post_init__(self) -> None:
        if self.classification not in {"parity", "improved", "regressed"}:
            raise ValueError("InvalidPerformanceClassification")
        _require_digest(self.candidate_digest, "InvalidCandidateDigest")
        _require_digest(self.baseline_digest, "InvalidBaselineDigest")

    @property
    def speedup_numerator(self) -> int:
        return self.speedup.numerator

    @property
    def speedup_denominator(self) -> int:
        return self.speedup.denominator

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_digest": self.candidate_digest, "baseline_digest": self.baseline_digest,
            "candidate_median_ns": self.candidate_median_ns,
            "baseline_median_ns": self.baseline_median_ns,
            "speedup": self.speedup.to_dict(), "classification": self.classification,
            "threshold_name": self.threshold_name,
        }


def assert_compatible(candidate: PerformanceEvidenceManifest, baseline: PerformanceEvidenceManifest) -> None:
    """Reject comparisons that do not describe the same workload execution."""
    if not isinstance(candidate, PerformanceEvidenceManifest) or not isinstance(baseline, PerformanceEvidenceManifest):
        raise ValueError("InvalidPerformanceEvidenceManifest")
    if candidate.workload_id != baseline.workload_id or candidate.workload_digest != baseline.workload_digest:
        raise ValueError("WorkloadMismatch")
    if candidate.output_digest != baseline.output_digest or not candidate.output_digest_parity or not baseline.output_digest_parity:
        raise ValueError("OutputDigestMismatch")
    if candidate.environment != baseline.environment:
        raise ValueError("EnvironmentMismatch")
    if candidate.hardware_profile != baseline.hardware_profile:
        raise ValueError("HardwareProfileMismatch")


def _threshold(value: int | float | str | Fraction, name: str) -> Fraction:
    result = _fraction(value, name)
    if result < 0 or result >= 1:
        raise ValueError(name)
    return result


def compare_performance(
    candidate: PerformanceEvidenceManifest,
    baseline: PerformanceEvidenceManifest | None = None,
    *,
    minimum_samples: int = MINIMUM_SAMPLES,
    parity_threshold: int | float | str | Fraction = Fraction(1, 20),
    improvement_threshold: int | float | str | Fraction | None = None,
    regression_threshold: int | float | str | Fraction | None = None,
    threshold_name: str = "relative_median_threshold",
) -> PerformanceComparison:
    """Compare medians without manufacturing absent measurements."""
    if not isinstance(candidate, PerformanceEvidenceManifest):
        raise ValueError("InvalidPerformanceEvidenceManifest")
    if not isinstance(minimum_samples, int) or isinstance(minimum_samples, bool) or minimum_samples < 1:
        raise ValueError("InvalidMinimumSamples")
    if len(candidate.samples_ns) < minimum_samples:
        raise ValueError("InsufficientCandidateSamples")
    if baseline is not None:
        assert_compatible(candidate, baseline)
        baseline_samples = baseline.samples_ns
        baseline_digest = baseline.digest
    else:
        baseline_samples = candidate.scalar_baseline_samples_ns
        baseline_digest = _digest(_canonical(baseline_samples))
    if len(baseline_samples) < minimum_samples:
        raise ValueError("InsufficientBaselineSamples")
    parity = _threshold(parity_threshold, "InvalidParityThreshold")
    improved = parity if improvement_threshold is None else _threshold(improvement_threshold, "InvalidImprovementThreshold")
    regressed = parity if regression_threshold is None else _threshold(regression_threshold, "InvalidRegressionThreshold")
    candidate_median = summarize_samples(candidate.samples_ns).median_ns
    baseline_median = summarize_samples(baseline_samples).median_ns
    ratio = ExactRatio(baseline_median, candidate_median)
    speedup = ratio.value
    if speedup > 1 + improved:
        classification = "improved"
    elif speedup < 1 - regressed:
        classification = "regressed"
    else:
        classification = "parity"
    return PerformanceComparison(
        candidate_digest=candidate.digest, baseline_digest=baseline_digest,
        candidate_median_ns=candidate_median, baseline_median_ns=baseline_median,
        speedup=ratio, classification=classification, threshold_name=threshold_name,
    )


def compare_evidence(*args: Any, **kwargs: Any) -> PerformanceComparison:
    return compare_performance(*args, **kwargs)


def classify_performance(*args: Any, **kwargs: Any) -> str:
    return compare_performance(*args, **kwargs).classification


__all__ = [
    "PERFORMANCE_EVIDENCE_SCHEMA_VERSION", "PERFORMANCE_EVIDENCE_CONTRACT", "MINIMUM_SAMPLES",
    "ExactRatio", "SampleSummary", "PerformanceEvidenceManifest", "PerformanceManifest",
    "PerformanceComparison", "percentile_ns", "summarize_samples", "assert_compatible",
    "compare_performance", "compare_evidence", "classify_performance",
]
