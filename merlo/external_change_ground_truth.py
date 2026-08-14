"""Independent 30x3 external semantic-change review workflow."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .external_bench import ExternalBenchmarkManifest


CHANGE_REVIEW_SCHEMA_VERSION = 1
REQUIRED_PER_OPERATION = 30
OPERATIONS = ("rename", "move", "change_signature")
LABELS = frozenset(("SAFE", "UNSAFE", "INVALID"))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChangeReviewCandidate:
    id: str
    project: str
    operation: str
    target: str
    payload: str
    project_revision: str
    target_revision: str | None
    candidate_source: str
    candidate_oracle: str

    def __post_init__(self) -> None:
        if self.operation not in OPERATIONS:
            raise ValueError(f"unsupported operation: {self.operation}")
        required = (
            self.id,
            self.project,
            self.target,
            self.payload,
            self.project_revision,
            self.candidate_source,
            self.candidate_oracle,
        )
        if not all(required):
            raise ValueError("change review candidate fields must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "operation": self.operation,
            "target": self.target,
            "payload": self.payload,
            "project_revision": self.project_revision,
            "target_revision": self.target_revision,
            "candidate_source": self.candidate_source,
            "candidate_oracle": self.candidate_oracle,
            "expected_safe": None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChangeReviewCandidate":
        if value.get("expected_safe") is not None:
            raise ValueError("review candidates must not carry inherited safety labels")
        return cls(
            id=str(value["id"]),
            project=str(value["project"]),
            operation=str(value["operation"]),
            target=str(value["target"]),
            payload=str(value["payload"]),
            project_revision=str(value["project_revision"]),
            target_revision=(
                str(value["target_revision"])
                if value.get("target_revision") is not None
                else None
            ),
            candidate_source=str(value["candidate_source"]),
            candidate_oracle=str(value["candidate_oracle"]),
        )


@dataclass(frozen=True)
class ChangeReviewQueue:
    candidates: tuple[ChangeReviewCandidate, ...]
    required_per_operation: int = REQUIRED_PER_OPERATION
    seed: int = 20260810
    schema_version: int = CHANGE_REVIEW_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CHANGE_REVIEW_SCHEMA_VERSION:
            raise ValueError("unsupported change review schema")
        if self.required_per_operation <= 0:
            raise ValueError("required_per_operation must be positive")
        ordered = tuple(sorted(self.candidates, key=lambda item: item.id))
        if len({item.id for item in ordered}) != len(ordered):
            raise ValueError("duplicate change review candidate id")
        object.__setattr__(self, "candidates", ordered)

    @property
    def dataset_digest(self) -> str:
        return _digest(
            {
                "schema_version": self.schema_version,
                "required_per_operation": self.required_per_operation,
                "seed": self.seed,
                "candidates": [item.to_dict() for item in self.candidates],
            }
        )

    @property
    def counts(self) -> dict[str, int]:
        return {
            operation: sum(
                item.operation == operation for item in self.candidates
            )
            for operation in OPERATIONS
        }

    @property
    def missing(self) -> dict[str, int]:
        return {
            operation: max(0, self.required_per_operation - count)
            for operation, count in self.counts.items()
        }

    @property
    def ready_for_adjudication_target(self) -> bool:
        return not any(self.missing.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": "MeldraExternalChangeReviewQueue",
            "seed": self.seed,
            "required_per_operation": self.required_per_operation,
            "dataset_digest": self.dataset_digest,
            "counts": self.counts,
            "missing": self.missing,
            "ready_for_adjudication_target": self.ready_for_adjudication_target,
            "label_policy": {
                "candidate_labels_inherited": False,
                "reviewer_must_not_see_planner_outcome": True,
                "required_evidence": [
                    "declared intent",
                    "baseline and changed collected node IDs",
                    "public API snapshot",
                    "selected behavior outputs",
                    "project tests",
                    "source restoration digest",
                ],
            },
            "candidates": [item.to_dict() for item in self.candidates],
        }

    def to_json(self) -> str:
        return _canonical(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChangeReviewQueue":
        queue = cls(
            candidates=tuple(
                ChangeReviewCandidate.from_dict(item)
                for item in value.get("candidates", ())
            ),
            required_per_operation=int(
                value.get("required_per_operation", REQUIRED_PER_OPERATION)
            ),
            seed=int(value.get("seed", 20260810)),
            schema_version=int(
                value.get("schema_version", CHANGE_REVIEW_SCHEMA_VERSION)
            ),
        )
        if value.get("dataset_digest") not in (None, queue.dataset_digest):
            raise ValueError("change review queue digest mismatch")
        return queue


@dataclass(frozen=True)
class ChangeReviewLabel:
    candidate_id: str
    reviewer_id: str
    label: str
    rationale: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.label not in LABELS:
            raise ValueError(f"unsupported review label: {self.label}")
        if not self.candidate_id or not self.reviewer_id or not self.rationale:
            raise ValueError("review label fields must be non-empty")
        if not self.evidence_ids:
            raise ValueError("at least one evidence id is required")
        object.__setattr__(
            self, "evidence_ids", tuple(sorted(set(self.evidence_ids)))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "reviewer_id": self.reviewer_id,
            "label": self.label,
            "rationale": self.rationale,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class ChangeGroundTruth:
    queue_digest: str
    labels: tuple[ChangeReviewLabel, ...]

    def __post_init__(self) -> None:
        if not self.queue_digest:
            raise ValueError("queue_digest is required")
        keys = [(item.candidate_id, item.reviewer_id) for item in self.labels]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate candidate/reviewer label")
        object.__setattr__(
            self,
            "labels",
            tuple(
                sorted(
                    self.labels,
                    key=lambda item: (item.candidate_id, item.reviewer_id),
                )
            ),
        )

    def agreement(self) -> dict[str, Any]:
        by_candidate: dict[str, list[str]] = {}
        for item in self.labels:
            by_candidate.setdefault(item.candidate_id, []).append(item.label)
        double_reviewed = {
            key: labels for key, labels in by_candidate.items() if len(labels) >= 2
        }
        agreements = sum(len(set(labels)) == 1 for labels in double_reviewed.values())
        denominator = len(double_reviewed)
        return {
            "double_reviewed": denominator,
            "agreements": agreements,
            "rate": round(agreements / denominator, 6) if denominator else None,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CHANGE_REVIEW_SCHEMA_VERSION,
            "queue_digest": self.queue_digest,
            "labels": [item.to_dict() for item in self.labels],
            "agreement": self.agreement(),
        }


def candidates_from_manifest(
    manifest: ExternalBenchmarkManifest,
) -> tuple[ChangeReviewCandidate, ...]:
    projects = {item.id: item for item in manifest.projects}
    result: list[ChangeReviewCandidate] = []
    for task in manifest.tasks:
        project = projects[task.project]
        metadata = dict(project.metadata)
        task_metadata = dict(task.metadata)
        revision = str(metadata.get("revision", "unversioned"))
        candidate_id = "review_" + _digest(
            {
                "project": task.project,
                "revision": revision,
                "operation": task.operation,
                "target": task.target,
                "payload": task.payload,
            }
        )[:24]
        result.append(
            ChangeReviewCandidate(
                id=candidate_id,
                project=task.project,
                operation=task.operation,
                target=task.target,
                payload=task.payload,
                project_revision=revision,
                target_revision=(
                    str(task_metadata["target_revision"])
                    if task_metadata.get("target_revision") is not None
                    else None
                ),
                candidate_source=task.label_source,
                candidate_oracle=task.oracle,
            )
        )
    return tuple(result)


def build_change_review_queue(
    manifests: Iterable[ExternalBenchmarkManifest],
    *,
    required_per_operation: int = REQUIRED_PER_OPERATION,
    seed: int = 20260810,
) -> ChangeReviewQueue:
    unique: dict[tuple[str, str, str, str], ChangeReviewCandidate] = {}
    for manifest in manifests:
        for candidate in candidates_from_manifest(manifest):
            key = (
                candidate.project,
                candidate.operation,
                candidate.target,
                candidate.payload,
            )
            unique.setdefault(key, candidate)
    selected: list[ChangeReviewCandidate] = []
    for operation in OPERATIONS:
        candidates = [
            item for item in unique.values() if item.operation == operation
        ]
        candidates.sort(
            key=lambda item: (
                _digest({"seed": seed, "id": item.id}),
                item.id,
            )
        )
        selected.extend(candidates[:required_per_operation])
    return ChangeReviewQueue(
        tuple(selected),
        required_per_operation=required_per_operation,
        seed=seed,
    )


def validate_ground_truth(
    queue: ChangeReviewQueue,
    ground_truth: ChangeGroundTruth,
) -> None:
    if ground_truth.queue_digest != queue.dataset_digest:
        raise ValueError("ground truth belongs to another review queue")
    candidate_ids = {item.id for item in queue.candidates}
    unknown = sorted(
        {
            item.candidate_id
            for item in ground_truth.labels
            if item.candidate_id not in candidate_ids
        }
    )
    if unknown:
        raise ValueError("unknown reviewed candidates: " + ", ".join(unknown))


__all__ = [
    "CHANGE_REVIEW_SCHEMA_VERSION",
    "LABELS",
    "OPERATIONS",
    "REQUIRED_PER_OPERATION",
    "ChangeGroundTruth",
    "ChangeReviewCandidate",
    "ChangeReviewLabel",
    "ChangeReviewQueue",
    "build_change_review_queue",
    "candidates_from_manifest",
    "validate_ground_truth",
]
