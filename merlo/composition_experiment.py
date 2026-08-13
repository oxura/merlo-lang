from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .changes import ChangeDescriptor, changes_commute, changes_conflict


_METHOD = (
    "offline deterministic composition simulation; waves and throughput are "
    "theoretical; no changes, plans, agents, or subprocesses were executed"
)
_WAVE_MODEL = (
    "deterministic first-fit grouping; every pair within a theoretical wave "
    "must commute according to the existing ChangeIR composition rules"
)


def _descriptor_key(change: ChangeDescriptor) -> str:
    return json.dumps(
        change.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


@dataclass(frozen=True)
class CompositionSetMeasurement:
    sample_id: str
    change_count: int
    pair_count: int
    commuting_pair_count: int
    conflicting_pair_count: int
    non_commuting_pair_count: int
    theoretical_wave_widths: tuple[int, ...]

    @property
    def theoretical_wave_count(self) -> int:
        return len(self.theoretical_wave_widths)

    @property
    def theoretical_throughput(self) -> float:
        return _ratio(self.change_count, self.theoretical_wave_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "counts": {
                "changes": self.change_count,
                "pairs": self.pair_count,
                "commuting_pairs": self.commuting_pair_count,
                "conflicting_pairs": self.conflicting_pair_count,
                "non_commuting_pairs": self.non_commuting_pair_count,
                "theoretical_waves": self.theoretical_wave_count,
            },
            "theoretical_wave_widths": list(self.theoretical_wave_widths),
            "theoretical_throughput": {
                "numerator_changes": self.change_count,
                "denominator_waves": self.theoretical_wave_count,
                "value": round(self.theoretical_throughput, 6),
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CompositionSetMeasurement":
        counts = value["counts"]
        return cls(
            sample_id=str(value["sample_id"]),
            change_count=int(counts["changes"]),
            pair_count=int(counts["pairs"]),
            commuting_pair_count=int(counts["commuting_pairs"]),
            conflicting_pair_count=int(counts["conflicting_pairs"]),
            non_commuting_pair_count=int(counts["non_commuting_pairs"]),
            theoretical_wave_widths=tuple(
                int(item) for item in value.get("theoretical_wave_widths", ())
            ),
        )


@dataclass(frozen=True)
class CompositionExperimentReport:
    samples: tuple[CompositionSetMeasurement, ...]
    blocked_reason_frequency: tuple[tuple[str, int], ...]

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    @property
    def change_count(self) -> int:
        return sum(sample.change_count for sample in self.samples)

    @property
    def pair_count(self) -> int:
        return sum(sample.pair_count for sample in self.samples)

    @property
    def commuting_pair_count(self) -> int:
        return sum(sample.commuting_pair_count for sample in self.samples)

    @property
    def conflicting_pair_count(self) -> int:
        return sum(sample.conflicting_pair_count for sample in self.samples)

    @property
    def non_commuting_pair_count(self) -> int:
        return sum(sample.non_commuting_pair_count for sample in self.samples)

    @property
    def theoretical_wave_count(self) -> int:
        return sum(sample.theoretical_wave_count for sample in self.samples)

    @property
    def blocked_reason_count(self) -> int:
        return sum(count for _, count in self.blocked_reason_frequency)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": _METHOD,
            "simulation": True,
            "execution_performed": False,
            "wave_model": _WAVE_MODEL,
            "counts": {
                "samples": self.sample_count,
                "changes": self.change_count,
                "pairs": self.pair_count,
                "commuting_pairs": self.commuting_pair_count,
                "conflicting_pairs": self.conflicting_pair_count,
                "non_commuting_pairs": self.non_commuting_pair_count,
                "theoretical_waves": self.theoretical_wave_count,
                "blocked_reasons": self.blocked_reason_count,
            },
            "commute_rate": {
                "numerator_commuting_pairs": self.commuting_pair_count,
                "denominator_pairs": self.pair_count,
                "value": round(
                    _ratio(self.commuting_pair_count, self.pair_count), 6
                ),
            },
            "conflict_rate": {
                "numerator_conflicting_pairs": self.conflicting_pair_count,
                "denominator_pairs": self.pair_count,
                "value": round(
                    _ratio(self.conflicting_pair_count, self.pair_count), 6
                ),
            },
            "theoretical_throughput": {
                "numerator_changes": self.change_count,
                "denominator_waves": self.theoretical_wave_count,
                "value": round(
                    _ratio(self.change_count, self.theoretical_wave_count), 6
                ),
            },
            "blocked_reason_frequency": [
                {"blocked_reason": reason, "count": count}
                for reason, count in self.blocked_reason_frequency
            ],
            "samples": [sample.to_dict() for sample in self.samples],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CompositionExperimentReport":
        return cls(
            samples=tuple(
                CompositionSetMeasurement.from_dict(item)
                for item in value.get("samples", ())
            ),
            blocked_reason_frequency=tuple(
                sorted(
                    (
                        str(item["blocked_reason"]),
                        int(item["count"]),
                    )
                    for item in value.get("blocked_reason_frequency", ())
                )
            ),
        )


def measure_composition_set(
    changes: Iterable[ChangeDescriptor],
) -> CompositionSetMeasurement:
    """Measure one set without planning or applying any change."""

    ordered = tuple(sorted(changes, key=_descriptor_key))
    encoded = json.dumps(
        [change.to_dict() for change in ordered],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    sample_id = hashlib.sha256(encoded).hexdigest()[:16]

    commuting_pairs = 0
    conflicting_pairs = 0
    non_commuting_pairs = 0
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if changes_commute(left, right):
                commuting_pairs += 1
            else:
                non_commuting_pairs += 1
            if changes_conflict(left, right):
                conflicting_pairs += 1

    # This is deliberately a simulation metric, not an execution schedule.
    waves: list[list[ChangeDescriptor]] = []
    for change in ordered:
        for wave in waves:
            if all(changes_commute(change, other) for other in wave):
                wave.append(change)
                break
        else:
            waves.append([change])

    pair_count = len(ordered) * (len(ordered) - 1) // 2
    return CompositionSetMeasurement(
        sample_id=sample_id,
        change_count=len(ordered),
        pair_count=pair_count,
        commuting_pair_count=commuting_pairs,
        conflicting_pair_count=conflicting_pairs,
        non_commuting_pair_count=non_commuting_pairs,
        theoretical_wave_widths=tuple(len(wave) for wave in waves),
    )


def run_composition_experiment(
    change_sets: Iterable[Iterable[ChangeDescriptor]],
    *,
    blocked_reason_frequency: Mapping[str, int] | Iterable[str] = (),
) -> CompositionExperimentReport:
    """Aggregate composition simulation metrics and measured blocking reasons."""

    if isinstance(blocked_reason_frequency, Mapping):
        frequencies = Counter(
            {
                str(reason): int(count)
                for reason, count in blocked_reason_frequency.items()
            }
        )
    else:
        frequencies = Counter(str(reason) for reason in blocked_reason_frequency)
    negative = tuple(sorted(reason for reason, count in frequencies.items() if count < 0))
    if negative:
        raise ValueError(
            "blocked reason frequencies must be non-negative: " + ", ".join(negative)
        )
    measured = tuple(
        sorted((reason, count) for reason, count in frequencies.items() if count > 0)
    )
    samples = tuple(
        sorted(
            (measure_composition_set(changes) for changes in change_sets),
            key=lambda sample: (
                sample.sample_id,
                sample.change_count,
                sample.theoretical_wave_widths,
            ),
        )
    )
    return CompositionExperimentReport(samples, measured)
