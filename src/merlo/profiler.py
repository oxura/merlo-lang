"""Bounded sampling profiler with canonical, tamper-evident reports."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, TypeVar

PROFILE_SCHEMA_VERSION = 1
PROFILE_CONTRACT = "merlo.profile.v1"
MAX_PROFILE_ITERATIONS = 10_000
T = TypeVar("T")

MAX_WORK_UNITS = 2**63 - 1

def _error(code: str, detail: str = "") -> ValueError:
    return ValueError(code if not detail else f"{code}: {detail}")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _percentile(samples: tuple[int, ...], percentile: int) -> int:
    index = max(0, math.ceil(len(samples) * percentile / 100) - 1)
    return sorted(samples)[index]


@dataclass(frozen=True, slots=True)
class ProfileSample:
    iteration: int
    duration_ns: int
    work_units: int = 1

    def __post_init__(self) -> None:
        if type(self.iteration) is not int or self.iteration < 0:
            raise _error("ProfileInvalidIteration")
        if type(self.duration_ns) is not int or self.duration_ns < 0:
            raise _error("ProfileInvalidDuration")
        if type(self.work_units) is not int or not 1 <= self.work_units <= MAX_WORK_UNITS:
            raise _error("ProfileInvalidWorkUnits")

    def to_dict(self) -> dict[str, int]:
        return {"iteration": self.iteration, "duration_ns": self.duration_ns, "work_units": self.work_units}


@dataclass(frozen=True, slots=True)
class ProfileReport:
    label: str
    samples: tuple[ProfileSample, ...]
    schema_version: int = PROFILE_SCHEMA_VERSION
    contract: str = PROFILE_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label:
            raise _error("ProfileInvalidLabel")
        samples = tuple(self.samples)
        if not samples or len(samples) > MAX_PROFILE_ITERATIONS:
            raise _error("ProfileInvalidSampleCount")
        if any(not isinstance(sample, ProfileSample) for sample in samples):
            raise _error("ProfileSamplesMismatch")
        if tuple(item.iteration for item in samples) != tuple(range(len(samples))):
            raise _error("ProfileIterationMismatch")
        if self.schema_version != PROFILE_SCHEMA_VERSION or self.contract != PROFILE_CONTRACT:
            raise _error("ProfileContractMismatch")
        payload = {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "label": self.label,
            "samples": [item.to_dict() for item in samples],
        }
        expected = _digest(payload)
        if self.digest and self.digest != expected:
            raise _error("ProfileDigestMismatch")
        object.__setattr__(self, "samples", samples)
        object.__setattr__(self, "digest", expected)

    @property
    def total_ns(self) -> int:
        return sum(item.duration_ns for item in self.samples)

    @property
    def minimum_ns(self) -> int:
        return min(item.duration_ns for item in self.samples)

    @property
    def maximum_ns(self) -> int:
        return max(item.duration_ns for item in self.samples)

    @property
    def median_ns(self) -> int:
        values = tuple(sorted(item.duration_ns for item in self.samples))
        middle = len(values) // 2
        return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) // 2

    @property
    def p95_ns(self) -> int:
        return _percentile(tuple(item.duration_ns for item in self.samples), 95)

    @property
    def throughput_per_second(self) -> float | None:
        if self.total_ns == 0:
            return None
        return sum(item.work_units for item in self.samples) * 1_000_000_000 / self.total_ns

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "label": self.label,
            "samples": [item.to_dict() for item in self.samples],
            "summary": {
                "total_ns": self.total_ns,
                "minimum_ns": self.minimum_ns,
                "maximum_ns": self.maximum_ns,
                "median_ns": self.median_ns,
                "p95_ns": self.p95_ns,
                "work_units": sum(item.work_units for item in self.samples),
            },
            "digest": self.digest,
        }

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProfileReport":
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "contract", "label", "samples", "summary", "digest"}:
            raise _error("ProfileSchemaMismatch")
        if not isinstance(value["digest"], str) or len(value["digest"]) != 64:
            raise _error("ProfileDigestMismatch")
        raw_samples = value["samples"]
        if not isinstance(raw_samples, list):
            raise _error("ProfileSamplesMismatch")
        samples: list[ProfileSample] = []
        for raw in raw_samples:
            if not isinstance(raw, Mapping) or set(raw) != {"iteration", "duration_ns", "work_units"}:
                raise _error("ProfileSampleSchemaMismatch")
            samples.append(ProfileSample(raw["iteration"], raw["duration_ns"], raw["work_units"]))
        report = cls(value["label"], tuple(samples), value["schema_version"], value["contract"], value["digest"])
        if value["summary"] != report.to_dict()["summary"]:
            raise _error("ProfileSummaryMismatch")
        return report

    @classmethod
    def from_json(cls, payload: str) -> "ProfileReport":
        try:
            value = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise _error("ProfileInvalidJSON") from exc
        return cls.from_dict(value)


def profile_callable(
    label: str,
    action: Callable[[], T],
    *,
    iterations: int = 10,
    warmups: int = 1,
    work_units: int = 1,
    clock: Callable[[], int] = time.perf_counter_ns,
) -> tuple[ProfileReport, T]:
    """Profile a zero-argument callable; exceptions propagate without a partial report."""

    if not callable(action) or not callable(clock):
        raise _error("ProfileInvalidCallable")
    if type(iterations) is not int or not 1 <= iterations <= MAX_PROFILE_ITERATIONS:
        raise _error("ProfileInvalidIterations")
    if type(warmups) is not int or not 0 <= warmups <= MAX_PROFILE_ITERATIONS:
        raise _error("ProfileInvalidWarmups")
    if type(work_units) is not int or not 1 <= work_units <= MAX_WORK_UNITS:
        raise _error("ProfileInvalidWorkUnits")
    for _ in range(warmups):
        action()
    samples: list[ProfileSample] = []
    result: T
    for iteration in range(iterations):
        before = clock()
        if type(before) is not int:
            raise _error("ProfileClockNonInteger")
        result = action()
        after = clock()
        if type(after) is not int or after < before:
            raise _error("ProfileClockWentBackwards")
        samples.append(ProfileSample(iteration, after - before, work_units))
    return ProfileReport(label, tuple(samples)), result


def merge_profiles(label: str, reports: Iterable[ProfileReport]) -> ProfileReport:
    """Merge compatible reports without retaining their original iteration numbers."""

    items = tuple(reports)
    if not items:
        raise _error("ProfileMergeEmpty")
    if any(not isinstance(item, ProfileReport) for item in items):
        raise _error("ProfileMergeInvalidReport")
    samples = tuple(
        ProfileSample(index, sample.duration_ns, sample.work_units)
        for index, sample in enumerate(sample for item in items for sample in item.samples)
    )
    return ProfileReport(label, samples)


__all__ = [
    "MAX_PROFILE_ITERATIONS",
    "MAX_WORK_UNITS",
    "PROFILE_CONTRACT",
    "PROFILE_SCHEMA_VERSION",
    "ProfileReport",
    "ProfileSample",
    "merge_profiles",
    "profile_callable",
]
