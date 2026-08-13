from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .model import EditCapability, Entity
from .world import SoftwareWorld


MANIFEST_SCHEMA = 1
_OPERATIONS = ("rename", "move", "change_signature")
_ALIASES = {"rename_symbol": "rename", "move_symbol": "move", "signature": "change_signature"}
_IGNORED_WORKSPACE_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        "__pycache__",
    }
)




def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _operation(value: str) -> str:
    normalized = _ALIASES.get(value, value)
    if normalized not in _OPERATIONS:
        raise ValueError(f"unsupported benchmark operation: {value}")
    return normalized


def _pairs(value: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> tuple[tuple[str, Any], ...]:
    items = value.items() if isinstance(value, Mapping) else value
    return tuple(sorted(((str(key), item) for key, item in items), key=lambda pair: pair[0]))


def _rank(seed: int, namespace: str, identifier: str) -> str:
    return hashlib.sha256(f"{seed}\0{namespace}\0{identifier}".encode()).hexdigest()


@dataclass(frozen=True)
class ExternalProject:
    id: str
    root: str
    category: str
    metadata: tuple[tuple[str, Any], ...] = ()
    held_out_count: int = 0
    entity_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id or not self.root or not self.category:
            raise ValueError("project id, root, and category are required")
        if self.held_out_count < 0:
            raise ValueError("held_out_count cannot be negative")
        object.__setattr__(self, "metadata", _pairs(self.metadata))
        object.__setattr__(self, "entity_kinds", tuple(sorted(set(self.entity_kinds))))

    @property
    def name(self) -> str:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "root": self.root,
            "category": self.category,
            "metadata": dict(self.metadata),
            "held_out_count": self.held_out_count,
            "entity_kinds": list(self.entity_kinds),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExternalProject":
        return cls(
            id=str(value.get("id", value.get("name", ""))),
            root=str(value["root"]),
            category=str(value["category"]),
            metadata=_pairs(value.get("metadata", {})),
            held_out_count=int(value.get("held_out_count", value.get("sample_entities", 0))),
            entity_kinds=tuple(map(str, value.get("entity_kinds", ()))),
        )


@dataclass(frozen=True)
class ExternalTaskSpec:
    id: str
    project: str
    operation: str
    target: str
    payload: str
    expected_safe: bool
    label_source: str = "manifest"
    oracle: str = "declared_expectation"
    argument_values: tuple[tuple[str, str], ...] = ()
    allow_public_api_break: bool = True
    allow_new_dependencies: bool = True
    metadata: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not all((self.id, self.project, self.target, self.payload, self.label_source, self.oracle)):
            raise ValueError("task id, project, target, payload, label_source, and oracle are required")
        object.__setattr__(self, "operation", _operation(self.operation))
        object.__setattr__(self, "argument_values", _pairs(self.argument_values))
        object.__setattr__(self, "metadata", _pairs(self.metadata))

    @classmethod
    def safe(cls, **kwargs: Any) -> "ExternalTaskSpec":
        return cls(expected_safe=True, **kwargs)

    @classmethod
    def unsafe(cls, **kwargs: Any) -> "ExternalTaskSpec":
        return cls(expected_safe=False, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "operation": self.operation,
            "target": self.target,
            "payload": self.payload,
            "expected_safe": self.expected_safe,
            "expectation": "safe" if self.expected_safe else "unsafe",
            "label_source": self.label_source,
            "oracle": self.oracle,
            "argument_values": dict(self.argument_values),
            "allow_public_api_break": self.allow_public_api_break,
            "allow_new_dependencies": self.allow_new_dependencies,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExternalTaskSpec":
        if "expected_safe" in value:
            expected_safe = bool(value["expected_safe"])
        else:
            expectation = str(value.get("expectation", "")).lower()
            if expectation not in {"safe", "unsafe"}:
                raise ValueError("task expectation must be safe or unsafe")
            expected_safe = expectation == "safe"
        return cls(
            id=str(value.get("id", value.get("name", ""))),
            project=str(value["project"]),
            operation=str(value["operation"]),
            target=str(value["target"]),
            payload=str(value["payload"]),
            expected_safe=expected_safe,
            label_source=str(value.get("label_source", "manifest")),
            oracle=str(value.get("oracle", "declared_expectation")),
            argument_values=_pairs(value.get("argument_values", {})),
            allow_public_api_break=bool(value.get("allow_public_api_break", True)),
            allow_new_dependencies=bool(value.get("allow_new_dependencies", True)),
            metadata=_pairs(value.get("metadata", {})),
        )


@dataclass(frozen=True)
class ExternalBenchmarkManifest:
    projects: tuple[ExternalProject, ...]
    tasks: tuple[ExternalTaskSpec, ...]
    seed: int = 0
    task_sample_size: int | None = None
    schema: int = MANIFEST_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != MANIFEST_SCHEMA:
            raise ValueError(f"unsupported manifest schema: {self.schema}")
        if self.task_sample_size is not None and self.task_sample_size < 0:
            raise ValueError("task_sample_size cannot be negative")
        project_ids = [item.id for item in self.projects]
        task_ids = [item.id for item in self.tasks]
        if len(project_ids) != len(set(project_ids)):
            raise ValueError("duplicate project id")
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("duplicate task id")
        unknown = sorted({item.project for item in self.tasks} - set(project_ids))
        if unknown:
            raise ValueError(f"tasks reference unknown projects: {unknown}")
        object.__setattr__(self, "projects", tuple(sorted(self.projects, key=lambda item: item.id)))
        object.__setattr__(self, "tasks", tuple(sorted(self.tasks, key=lambda item: item.id)))

    def selected_tasks(self) -> tuple[ExternalTaskSpec, ...]:
        if self.task_sample_size is None or self.task_sample_size >= len(self.tasks):
            return self.tasks
        ranked = sorted(self.tasks, key=lambda item: (_rank(self.seed, "tasks", item.id), item.id))
        return tuple(sorted(ranked[: self.task_sample_size], key=lambda item: item.id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "seed": self.seed,
            "task_sample_size": self.task_sample_size,
            "projects": [item.to_dict() for item in self.projects],
            "tasks": [item.to_dict() for item in self.tasks],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExternalBenchmarkManifest":
        return cls(
            projects=tuple(ExternalProject.from_dict(item) for item in value.get("projects", ())),
            tasks=tuple(ExternalTaskSpec.from_dict(item) for item in value.get("tasks", ())),
            seed=int(value.get("seed", 0)),
            task_sample_size=int(value["task_sample_size"]) if value.get("task_sample_size") is not None else None,
            schema=int(value.get("schema", MANIFEST_SCHEMA)),
        )

    def save(self, path: str | Path) -> None:
        save_manifest(self, path)

    @classmethod
    def load(cls, path: str | Path) -> "ExternalBenchmarkManifest":
        manifest = load_manifest(path)
        if not isinstance(manifest, cls):
            raise TypeError("loaded manifest has an unexpected type")
        return manifest


@dataclass(frozen=True)
class HeldOutEntity:
    project: str
    entity_id: str
    revision_hash: str
    locator: str
    kind: str
    file: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "entity_id": self.entity_id,
            "revision_hash": self.revision_hash,
            "locator": self.locator,
            "kind": self.kind,
            "file": self.file,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "HeldOutEntity":
        return cls(**{key: str(value[key]) for key in ("project", "entity_id", "revision_hash", "locator", "kind", "file")})


@dataclass(frozen=True)
class ExternalPlanResult:
    task_id: str
    project: str
    category: str
    operation: str
    target: str
    expected_safe: bool
    label_source: str
    oracle: str
    planner_allowed: bool | None
    outcome: str
    target_id: str | None = None
    target_revision: str | None = None
    plan_id: str | None = None
    edit_count: int = 0
    affected_files: tuple[str, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    error_kind: str | None = None
    error_message: str | None = None

    @property
    def evaluated(self) -> bool:
        return self.planner_allowed is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project": self.project,
            "category": self.category,
            "operation": self.operation,
            "target": self.target,
            "target_id": self.target_id,
            "target_revision": self.target_revision,
            "expected_safe": self.expected_safe,
            "label_source": self.label_source,
            "oracle": self.oracle,
            "planner_allowed": self.planner_allowed,
            "outcome": self.outcome,
            "plan_id": self.plan_id,
            "edit_count": self.edit_count,
            "affected_files": list(self.affected_files),
            "blocked_reasons": list(self.blocked_reasons),
            "error_kind": self.error_kind,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExternalPlanResult":
        allowed = value.get("planner_allowed")
        return cls(
            task_id=str(value["task_id"]), project=str(value["project"]),
            category=str(value["category"]), operation=str(value["operation"]),
            target=str(value["target"]), expected_safe=bool(value["expected_safe"]),
            label_source=str(value.get("label_source", "manifest")),
            oracle=str(value.get("oracle", "declared_expectation")),
            planner_allowed=bool(allowed) if allowed is not None else None,
            outcome=str(value["outcome"]), target_id=value.get("target_id"),
            target_revision=value.get("target_revision"), plan_id=value.get("plan_id"),
            edit_count=int(value.get("edit_count", 0)), affected_files=tuple(value.get("affected_files", ())),
            blocked_reasons=tuple(value.get("blocked_reasons", ())), error_kind=value.get("error_kind"),
            error_message=value.get("error_message"),
        )


@dataclass(frozen=True)
class ExternalCommandResult:
    argv: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    infrastructure_error: str | None = None

    @property
    def successful(self) -> bool:
        return (
            self.returncode == 0
            and not self.timed_out
            and self.infrastructure_error is None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "successful": self.successful,
            "timed_out": self.timed_out,
            "infrastructure_error": self.infrastructure_error,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalCommandResult":
        return cls(
            argv=tuple(map(str, value["argv"])),
            returncode=(
                int(value["returncode"])
                if value.get("returncode") is not None
                else None
            ),
            stdout=str(value.get("stdout", "")),
            stderr=str(value.get("stderr", "")),
            timed_out=bool(value.get("timed_out", False)),
            infrastructure_error=value.get("infrastructure_error"),
        )


_ACCEPTANCE_KINDS = frozenset(
    ("json", "lines", "pytest_collection", "pytest_passed_count", "text")
)


@dataclass(frozen=True)
class ExternalAcceptanceProbe:
    name: str
    argv: tuple[str, ...]
    kind: str = "text"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("acceptance probe name must be non-empty")
        if not self.argv or any(not item for item in self.argv):
            raise ValueError("acceptance probe argv must contain non-empty strings")
        if self.kind not in _ACCEPTANCE_KINDS:
            raise ValueError(f"unsupported acceptance probe kind: {self.kind}")

    @classmethod
    def create(
        cls,
        name: str,
        argv: Sequence[str],
        *,
        kind: str = "text",
    ) -> "ExternalAcceptanceProbe":
        return cls(name, tuple(argv), kind)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "argv": list(self.argv), "kind": self.kind}


@dataclass(frozen=True)
class ExternalAcceptanceResult:
    probe: ExternalAcceptanceProbe
    baseline: ExternalCommandResult
    changed: ExternalCommandResult | None
    baseline_value_json: str | None
    changed_value_json: str | None
    matched: bool | None
    error: str | None = None

    @property
    def baseline_value(self) -> Any:
        return (
            json.loads(self.baseline_value_json)
            if self.baseline_value_json is not None
            else None
        )

    @property
    def changed_value(self) -> Any:
        return (
            json.loads(self.changed_value_json)
            if self.changed_value_json is not None
            else None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe": self.probe.to_dict(),
            "baseline": self.baseline.to_dict(),
            "changed": self.changed.to_dict() if self.changed else None,
            "baseline_value": self.baseline_value,
            "changed_value": self.changed_value,
            "matched": self.matched,
            "error": self.error,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "ExternalAcceptanceResult":
        probe_value = value["probe"]
        changed = value.get("changed")
        return cls(
            probe=ExternalAcceptanceProbe.create(
                str(probe_value["name"]),
                tuple(map(str, probe_value["argv"])),
                kind=str(probe_value.get("kind", "text")),
            ),
            baseline=ExternalCommandResult.from_dict(value["baseline"]),
            changed=(
                ExternalCommandResult.from_dict(changed)
                if isinstance(changed, Mapping)
                else None
            ),
            baseline_value_json=(
                json.dumps(
                    value["baseline_value"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if value.get("baseline_value") is not None
                else None
            ),
            changed_value_json=(
                json.dumps(
                    value["changed_value"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                if value.get("changed_value") is not None
                else None
            ),
            matched=value.get("matched"),
            error=value.get("error"),
        )


@dataclass(frozen=True)
class ExternalInfrastructureError:
    stage: str
    kind: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"stage": self.stage, "kind": self.kind, "message": self.message}

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "ExternalInfrastructureError":
        return cls(
            stage=str(value["stage"]),
            kind=str(value["kind"]),
            message=str(value["message"]),
        )


@dataclass(frozen=True)
class ExternalApplyTestResult:
    task_id: str
    expected_world_revision: str
    observed_world_revision: str | None
    plan_ready: bool | None
    plan_id: str | None
    blocked_reasons: tuple[str, ...]
    materialization_eligible: bool
    apply_attempted: bool
    apply_succeeded: bool | None
    apply_error_kind: str | None
    apply_error_message: str | None
    changed_files: tuple[str, ...]
    world_revision_after: str | None
    commands: tuple[ExternalCommandResult, ...]
    source_digest_before: str | None
    source_digest_after: str | None
    restoration_attempted: bool
    restoration_succeeded: bool
    restoration_error: str | None
    temporary_workspace_removed: bool
    infrastructure_errors: tuple[ExternalInfrastructureError, ...] = ()
    acceptance: tuple[ExternalAcceptanceResult, ...] = ()

    @property
    def materialized(self) -> bool:
        return self.apply_succeeded is True

    @property
    def source_unchanged(self) -> bool | None:
        if self.source_digest_before is None or self.source_digest_after is None:
            return None
        return self.source_digest_before == self.source_digest_after

    @property
    def tests_successful(self) -> bool | None:
        if not self.commands:
            return None
        return all(item.successful for item in self.commands)

    @property
    def test_timeout_numerator(self) -> int:
        return sum(item.timed_out for item in self.commands)

    @property
    def test_infrastructure_error_numerator(self) -> int:
        return sum(item.infrastructure_error is not None for item in self.commands)

    @property
    def test_denominator(self) -> int:
        return len(self.commands)

    @property
    def acceptance_successful(self) -> bool | None:
        if not self.acceptance:
            return None
        return all(
            item.matched is True
            and item.baseline.successful
            and item.changed is not None
            and item.changed.successful
            for item in self.acceptance
        )

    @property
    def behaviorally_accepted(self) -> bool | None:
        checks = tuple(
            item
            for item in (self.tests_successful, self.acceptance_successful)
            if item is not None
        )
        if not checks:
            return None
        return self.materialized and all(checks)

    def to_dict(self) -> dict[str, Any]:
        denominator = self.test_denominator
        timeout_numerator = self.test_timeout_numerator
        error_numerator = self.test_infrastructure_error_numerator
        return {
            "task_id": self.task_id,
            "revision_guard": {
                "expected": self.expected_world_revision,
                "observed": self.observed_world_revision,
                "matched": (
                    self.observed_world_revision == self.expected_world_revision
                    if self.observed_world_revision is not None
                    else None
                ),
            },
            "plan": {
                "ready": self.plan_ready,
                "id": self.plan_id,
                "blocked_reasons": list(self.blocked_reasons),
                "materialization_eligible": self.materialization_eligible,
            },
            "apply": {
                "attempted": self.apply_attempted,
                "succeeded": self.apply_succeeded,
                "materialized": self.materialized,
                "error_kind": self.apply_error_kind,
                "error_message": self.apply_error_message,
                "changed_files": list(self.changed_files),
                "world_revision_after": self.world_revision_after,
            },
            "tests": {
                "successful": self.tests_successful,
                "attempted": denominator,
                "commands": [item.to_dict() for item in self.commands],
                "timeouts": {
                    "numerator": timeout_numerator,
                    "denominator": denominator,
                    "rate": (
                        round(timeout_numerator / denominator, 6)
                        if denominator
                        else None
                    ),
                },
                "infrastructure_errors": {
                    "numerator": error_numerator,
                    "denominator": denominator,
                    "rate": (
                        round(error_numerator / denominator, 6)
                        if denominator
                        else None
                    ),
                },
            },
            "acceptance": {
                "successful": self.acceptance_successful,
                "behaviorally_accepted": self.behaviorally_accepted,
                "probes": [item.to_dict() for item in self.acceptance],
            },
            "restoration": {
                "attempted": self.restoration_attempted,
                "succeeded": self.restoration_succeeded,
                "error": self.restoration_error,
                "source_digest_before": self.source_digest_before,
                "source_digest_after": self.source_digest_after,
                "source_unchanged": self.source_unchanged,
                "temporary_workspace_removed": self.temporary_workspace_removed,
            },
            "infrastructure_errors": [
                item.to_dict() for item in self.infrastructure_errors
            ],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExternalApplyTestResult":
        revision = value["revision_guard"]
        plan = value["plan"]
        apply = value["apply"]
        tests = value["tests"]
        restoration = value["restoration"]
        return cls(
            task_id=str(value["task_id"]),
            expected_world_revision=str(revision["expected"]),
            observed_world_revision=revision.get("observed"),
            plan_ready=plan.get("ready"),
            plan_id=plan.get("id"),
            blocked_reasons=tuple(map(str, plan.get("blocked_reasons", ()))),
            materialization_eligible=bool(
                plan.get("materialization_eligible", False)
            ),
            apply_attempted=bool(apply.get("attempted", False)),
            apply_succeeded=apply.get("succeeded"),
            apply_error_kind=apply.get("error_kind"),
            apply_error_message=apply.get("error_message"),
            changed_files=tuple(map(str, apply.get("changed_files", ()))),
            world_revision_after=apply.get("world_revision_after"),
            commands=tuple(
                ExternalCommandResult.from_dict(item)
                for item in tests.get("commands", ())
            ),
            source_digest_before=restoration.get("source_digest_before"),
            source_digest_after=restoration.get("source_digest_after"),
            restoration_attempted=bool(restoration.get("attempted", False)),
            restoration_succeeded=bool(restoration.get("succeeded", False)),
            restoration_error=restoration.get("error"),
            temporary_workspace_removed=bool(
                restoration.get("temporary_workspace_removed", False)
            ),
            infrastructure_errors=tuple(
                ExternalInfrastructureError.from_dict(item)
                for item in value.get("infrastructure_errors", ())
            ),
            acceptance=tuple(
                ExternalAcceptanceResult.from_dict(item)
                for item in value.get("acceptance", {}).get("probes", ())
            ),
        )


@dataclass(frozen=True)
class ValidationMetrics:
    total_tasks: int
    evaluated_tasks: int
    infrastructure_errors: int
    expected_safe_tasks: int
    expected_unsafe_tasks: int
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int

    @property
    def false_safe_numerator(self) -> int:
        return self.false_positive

    @property
    def false_block_numerator(self) -> int:
        return self.false_negative

    @property
    def safe_automation_numerator(self) -> int:
        return self.true_positive

    @property
    def false_safe_denominator(self) -> int:
        return self.false_positive + self.true_negative

    @property
    def false_block_denominator(self) -> int:
        return self.true_positive + self.false_negative

    @property
    def safe_automation_denominator(self) -> int:
        return self.false_block_denominator

    @property
    def false_safe_rate(self) -> float:
        return _ratio(self.false_positive, self.false_safe_denominator)

    @property
    def false_block_rate(self) -> float:
        return _ratio(self.false_negative, self.false_block_denominator)

    @property
    def safe_automation_coverage(self) -> float:
        return _ratio(self.true_positive, self.safe_automation_denominator)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tasks": self.total_tasks,
            "evaluated_tasks": self.evaluated_tasks,
            "infrastructure_errors": self.infrastructure_errors,
            "expected_safe_tasks": self.expected_safe_tasks,
            "expected_unsafe_tasks": self.expected_unsafe_tasks,
            "confusion_matrix": {"true_positive": self.true_positive, "false_positive": self.false_positive,
                                 "false_negative": self.false_negative, "true_negative": self.true_negative},
            "false_safe": {"numerator": self.false_positive, "denominator": self.false_safe_denominator,
                           "rate": round(self.false_safe_rate, 6)},
            "false_block": {"numerator": self.false_negative, "denominator": self.false_block_denominator,
                            "rate": round(self.false_block_rate, 6)},
            "safe_automation": {"numerator": self.true_positive, "denominator": self.safe_automation_denominator,
                                "coverage": round(self.safe_automation_coverage, 6)},
            "false_safe_rate": round(self.false_safe_rate, 6),
            "false_block_rate": round(self.false_block_rate, 6),
            "safe_automation_coverage": round(self.safe_automation_coverage, 6),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ValidationMetrics":
        matrix = value.get("confusion_matrix", value)
        return cls(
            total_tasks=int(value.get("total_tasks", 0)), evaluated_tasks=int(value.get("evaluated_tasks", 0)),
            infrastructure_errors=int(value.get("infrastructure_errors", 0)),
            expected_safe_tasks=int(value.get("expected_safe_tasks", 0)),
            expected_unsafe_tasks=int(value.get("expected_unsafe_tasks", 0)),
            true_positive=int(matrix.get("true_positive", 0)), false_positive=int(matrix.get("false_positive", 0)),
            false_negative=int(matrix.get("false_negative", 0)), true_negative=int(matrix.get("true_negative", 0)),
        )

    @classmethod
    def from_results(cls, results: Iterable[ExternalPlanResult]) -> "ValidationMetrics":
        values = tuple(results)
        evaluated = tuple(item for item in values if item.evaluated)
        return cls(
            total_tasks=len(values), evaluated_tasks=len(evaluated), infrastructure_errors=len(values) - len(evaluated),
            expected_safe_tasks=sum(item.expected_safe for item in values),
            expected_unsafe_tasks=sum(not item.expected_safe for item in values),
            true_positive=sum(item.expected_safe and item.planner_allowed is True for item in evaluated),
            false_positive=sum(not item.expected_safe and item.planner_allowed is True for item in evaluated),
            false_negative=sum(item.expected_safe and item.planner_allowed is False for item in evaluated),
            true_negative=sum(not item.expected_safe and item.planner_allowed is False for item in evaluated),
        )


@dataclass(frozen=True)
class BenchmarkBreakdown:
    key: str
    metrics: ValidationMetrics


@dataclass(frozen=True)
class ExternalBenchmarkReport:
    seed: int
    projects: tuple[ExternalProject, ...]
    metrics: ValidationMetrics
    results: tuple[ExternalPlanResult, ...]
    held_out_entities: tuple[HeldOutEntity, ...]
    blocked_reason_frequency: tuple[tuple[str, int], ...]
    category_breakdown: tuple[BenchmarkBreakdown, ...]
    operation_breakdown: tuple[BenchmarkBreakdown, ...]
    label_source_breakdown: tuple[BenchmarkBreakdown, ...]

    @property
    def false_safe_rate(self) -> float:
        return self.metrics.false_safe_rate

    @property
    def false_block_rate(self) -> float:
        return self.metrics.false_block_rate

    @property
    def safe_automation_coverage(self) -> float:
        return self.metrics.safe_automation_coverage

    def _breakdown(self, values: tuple[BenchmarkBreakdown, ...], key: str) -> ValidationMetrics:
        for item in values:
            if item.key == key:
                return item.metrics
        raise KeyError(key)

    def for_category(self, category: str) -> ValidationMetrics:
        return self._breakdown(self.category_breakdown, category)

    def for_operation(self, operation: str) -> ValidationMetrics:
        return self._breakdown(self.operation_breakdown, _operation(operation))

    def for_label_source(self, label_source: str) -> ValidationMetrics:
        return self._breakdown(self.label_source_breakdown, label_source)

    def to_dict(self) -> dict[str, Any]:
        def breakdown(values: tuple[BenchmarkBreakdown, ...]) -> dict[str, Any]:
            return {item.key: item.metrics.to_dict() for item in values}
        return {
            "seed": self.seed, "metrics": self.metrics.to_dict(),
            "projects": [item.to_dict() for item in self.projects],
            "blocked_reason_frequency": [{"reason": reason, "count": count} for reason, count in self.blocked_reason_frequency],
            "category_breakdown": breakdown(self.category_breakdown),
            "operation_breakdown": breakdown(self.operation_breakdown),
            "label_source_breakdown": breakdown(self.label_source_breakdown),
            "held_out_entities": [item.to_dict() for item in self.held_out_entities],
            "results": [item.to_dict() for item in self.results],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ExternalBenchmarkReport":
        def breakdown(name: str) -> tuple[BenchmarkBreakdown, ...]:
            return tuple(BenchmarkBreakdown(str(key), ValidationMetrics.from_dict(item))
                         for key, item in sorted(value.get(name, {}).items()))
        return cls(
            seed=int(value.get("seed", 0)), metrics=ValidationMetrics.from_dict(value.get("metrics", {})),
            projects=tuple(ExternalProject.from_dict(item) for item in value.get("projects", ())),
            results=tuple(ExternalPlanResult.from_dict(item) for item in value.get("results", ())),
            held_out_entities=tuple(HeldOutEntity.from_dict(item) for item in value.get("held_out_entities", ())),
            blocked_reason_frequency=tuple((str(item["reason"]), int(item["count"]))
                                           for item in value.get("blocked_reason_frequency", ())),
            category_breakdown=breakdown("category_breakdown"), operation_breakdown=breakdown("operation_breakdown"),
            label_source_breakdown=breakdown("label_source_breakdown"),
        )


def load_manifest(path: str | Path) -> ExternalBenchmarkManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("external benchmark manifest must be a JSON object")
    return ExternalBenchmarkManifest.from_dict(payload)


def save_manifest(manifest: ExternalBenchmarkManifest, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def discover_held_out_entities(world: SoftwareWorld, project: ExternalProject, seed: int) -> tuple[HeldOutEntity, ...]:
    candidates = [item for item in world.program.entities if not project.entity_kinds or item.kind in project.entity_kinds]
    ranked = sorted(candidates, key=lambda item: (_rank(seed, project.id, item.id), item.id))
    return tuple(_held_out(project.id, item) for item in sorted(ranked[: project.held_out_count], key=lambda item: item.id))


def run_external_benchmark(manifest: ExternalBenchmarkManifest, *, base_dir: str | Path | None = None) -> ExternalBenchmarkReport:
    base = Path(base_dir).resolve() if base_dir is not None else Path.cwd()
    tasks_by_project = {item.id: [] for item in manifest.projects}
    for task in manifest.selected_tasks():
        tasks_by_project[task.project].append(task)
    results: list[ExternalPlanResult] = []
    held_out: list[HeldOutEntity] = []
    with tempfile.TemporaryDirectory(prefix="meldra-external-bench-") as state_directory:
        for project in manifest.projects:
            tasks = tuple(sorted(tasks_by_project[project.id], key=lambda item: item.id))
            root = Path(project.root) if Path(project.root).is_absolute() else base / project.root
            try:
                if not root.is_dir():
                    raise FileNotFoundError(f"project root is not a directory: {root}")
                world = SoftwareWorld.scan(root, state_path=Path(state_directory) / f"{project.id}.json")
                held_out.extend(discover_held_out_entities(world, project, manifest.seed))
            except Exception as exc:
                results.extend(_error_result(task, project, exc) for task in tasks)
                continue
            for task in tasks:
                try:
                    results.append(_run_task(world, project, task))
                except Exception as exc:
                    results.append(_error_result(task, project, exc))
    ordered = tuple(sorted(results, key=lambda item: item.task_id))
    reasons = Counter(reason for result in ordered for reason in result.blocked_reasons)
    return ExternalBenchmarkReport(
        seed=manifest.seed, metrics=ValidationMetrics.from_results(ordered), results=ordered,
        projects=manifest.projects,
        held_out_entities=tuple(sorted(held_out, key=lambda item: (item.project, item.entity_id))),
        blocked_reason_frequency=tuple(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))),
        category_breakdown=_make_breakdown(ordered, "category"),
        operation_breakdown=_make_breakdown(ordered, "operation", order=_OPERATIONS),
        label_source_breakdown=_make_breakdown(ordered, "label_source"),
    )


class ExternalBenchmarkRunner:
    def __init__(self, manifest: ExternalBenchmarkManifest, *, base_dir: str | Path | None = None):
        self.manifest, self.base_dir = manifest, base_dir

    def run(self) -> ExternalBenchmarkReport:
        return run_external_benchmark(self.manifest, base_dir=self.base_dir)


def run_apply_and_test_validation(
    project: ExternalProject,
    task: ExternalTaskSpec,
    argv_sequence: Iterable[Sequence[str]],
    *,
    expected_world_revision: str,
    timeout: float = 30.0,
    base_dir: str | Path | None = None,
    acceptance_probes: Iterable[ExternalAcceptanceProbe] = (),
) -> ExternalApplyTestResult:
    """Validate one fixture in an isolated copy; planner output is not an oracle."""
    if task.project != project.id:
        raise ValueError("task and project identifiers do not match")
    if not expected_world_revision:
        raise ValueError("expected_world_revision is required")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    commands = _normalize_argv_sequence(argv_sequence)
    probes = tuple(acceptance_probes)
    if len({item.name for item in probes}) != len(probes):
        raise ValueError("acceptance probe names must be unique")
    base = Path(base_dir).resolve() if base_dir is not None else Path.cwd()
    source_root = (
        Path(project.root)
        if Path(project.root).is_absolute()
        else base / project.root
    ).resolve()

    errors: list[ExternalInfrastructureError] = []
    source_digest_before: str | None = None
    source_digest_after: str | None = None
    observed_revision: str | None = None
    plan_ready: bool | None = None
    plan_id: str | None = None
    blocked_reasons: tuple[str, ...] = ()
    eligible = False
    apply_attempted = False
    apply_succeeded: bool | None = None
    apply_error_kind: str | None = None
    apply_error_message: str | None = None
    changed_files: tuple[str, ...] = ()
    world_revision_after: str | None = None
    command_results: list[ExternalCommandResult] = []
    workspace_root: Path | None = None
    baseline_probes: dict[
        str, tuple[ExternalAcceptanceProbe, ExternalCommandResult, str | None, str | None]
    ] = {}
    acceptance_results: list[ExternalAcceptanceResult] = []

    try:
        source_digest_before = _tree_digest(source_root)
    except OSError as exc:
        errors.append(_infrastructure_error("source_snapshot", exc))

    with tempfile.TemporaryDirectory(
        prefix="meldra-apply-test-"
    ) as temporary_directory:
        workspace_root = Path(temporary_directory) / "project"
        if source_digest_before is not None:
            try:
                shutil.copytree(
                    source_root,
                    workspace_root,
                    ignore=shutil.ignore_patterns(
                        ".git",
                        ".hg",
                        ".merlo",
                        ".mypy_cache",
                        ".pytest_cache",
                        ".ruff_cache",
                        ".svn",
                        "__pycache__",
                        "*.pyc",
                    ),
                )
                world = SoftwareWorld.scan(
                    workspace_root,
                    state_path=Path(temporary_directory) / "world.json",
                )
                observed_revision = world.program.world_revision
            except Exception as exc:
                errors.append(_infrastructure_error("workspace_setup", exc))
                world = None

            if world is not None and observed_revision != expected_world_revision:
                apply_error_kind = "WorldRevisionMismatch"
                apply_error_message = (
                    "expected world revision "
                    f"{expected_world_revision}, observed {observed_revision}"
                )
            elif world is not None:
                baseline_ready = True
                for probe in probes:
                    baseline_command = _run_validation_command(
                        probe.argv, workspace_root, timeout
                    )
                    try:
                        baseline_value = _acceptance_value(
                            probe, baseline_command
                        )
                        baseline_value_json = json.dumps(
                            baseline_value,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        baseline_error = None
                    except (ValueError, json.JSONDecodeError) as exc:
                        baseline_ready = False
                        baseline_value_json = None
                        baseline_error = f"{type(exc).__name__}: {exc}"
                        errors.append(
                            ExternalInfrastructureError(
                                "acceptance_baseline",
                                type(exc).__name__,
                                f"{probe.name}: {exc}",
                            )
                        )
                    baseline_probes[probe.name] = (
                        probe,
                        baseline_command,
                        baseline_value_json,
                        baseline_error,
                    )
                try:
                    plan, capability = _plan_task(world, task)
                    plan_ready = plan.ready
                    plan_id = plan.change.id
                    blocked_reasons = tuple(
                        sorted(
                            item.kind
                            for item in plan.obligations
                            if item.blocking
                        )
                    )
                    eligible = task.expected_safe and plan.ready and baseline_ready
                except Exception as exc:
                    errors.append(_infrastructure_error("planner", exc))
                    plan = None
                    capability = None

                if eligible and plan is not None:
                    apply_attempted = True
                    try:
                        changed_files = tuple(world.apply(plan, capability))
                        apply_succeeded = True
                        world_revision_after = world.program.world_revision
                    except Exception as exc:
                        apply_succeeded = False
                        apply_error_kind = type(exc).__name__
                        apply_error_message = str(exc)
                if baseline_probes:
                    for (
                        probe,
                        baseline_command,
                        baseline_value_json,
                        baseline_error,
                    ) in baseline_probes.values():
                        if apply_succeeded is not True:
                            acceptance_results.append(
                                ExternalAcceptanceResult(
                                    probe,
                                    baseline_command,
                                    None,
                                    baseline_value_json,
                                    None,
                                    None,
                                    baseline_error,
                                )
                            )
                            continue
                        changed_command = _run_validation_command(
                            probe.argv, workspace_root, timeout
                        )
                        try:
                            changed_value = _acceptance_value(
                                probe, changed_command
                            )
                            changed_value_json = json.dumps(
                                changed_value,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            )
                            changed_error = None
                            matched = (
                                baseline_value_json == changed_value_json
                                and baseline_error is None
                            )
                        except (ValueError, json.JSONDecodeError) as exc:
                            changed_value_json = None
                            changed_error = f"{type(exc).__name__}: {exc}"
                            matched = False
                            errors.append(
                                ExternalInfrastructureError(
                                    "acceptance_changed",
                                    type(exc).__name__,
                                    f"{probe.name}: {exc}",
                                )
                            )
                        acceptance_results.append(
                            ExternalAcceptanceResult(
                                probe,
                                baseline_command,
                                changed_command,
                                baseline_value_json,
                                changed_value_json,
                                matched,
                                baseline_error or changed_error,
                            )
                        )

                if apply_succeeded is True:
                    for argv in commands:
                        result = _run_validation_command(
                            argv, workspace_root, timeout
                        )
                        command_results.append(result)
                        if result.infrastructure_error is not None:
                            kind = (
                                "TimeoutExpired"
                                if result.timed_out
                                else "OSError"
                            )
                            errors.append(
                                ExternalInfrastructureError(
                                    stage="test_command",
                                    kind=kind,
                                    message=result.infrastructure_error,
                                )
                            )

    temporary_workspace_removed = (
        workspace_root is not None and not workspace_root.exists()
    )
    restoration_messages: list[str] = []
    try:
        source_digest_after = _tree_digest(source_root)
    except OSError as exc:
        errors.append(_infrastructure_error("source_restoration_check", exc))
        restoration_messages.append(f"{type(exc).__name__}: {exc}")
    if (
        source_digest_before is not None
        and source_digest_after is not None
        and source_digest_before != source_digest_after
    ):
        restoration_messages.append("source project changed during validation")
    if not temporary_workspace_removed:
        restoration_messages.append("temporary workspace was not removed")
    restoration_succeeded = (
        source_digest_before is not None
        and source_digest_after == source_digest_before
        and temporary_workspace_removed
    )

    return ExternalApplyTestResult(
        task_id=task.id,
        expected_world_revision=expected_world_revision,
        observed_world_revision=observed_revision,
        plan_ready=plan_ready,
        plan_id=plan_id,
        blocked_reasons=blocked_reasons,
        materialization_eligible=eligible,
        apply_attempted=apply_attempted,
        apply_succeeded=apply_succeeded,
        apply_error_kind=apply_error_kind,
        apply_error_message=apply_error_message,
        changed_files=changed_files,
        world_revision_after=world_revision_after,
        commands=tuple(command_results),
        source_digest_before=source_digest_before,
        source_digest_after=source_digest_after,
        restoration_attempted=True,
        restoration_succeeded=restoration_succeeded,
        restoration_error=(
            "; ".join(restoration_messages) if restoration_messages else None
        ),
        temporary_workspace_removed=temporary_workspace_removed,
        infrastructure_errors=tuple(errors),
        acceptance=tuple(acceptance_results),
    )


def _normalize_argv_sequence(
    value: Iterable[Sequence[str]],
) -> tuple[tuple[str, ...], ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError("argv_sequence must contain argv sequences")
    commands: list[tuple[str, ...]] = []
    for argv in value:
        if isinstance(argv, (str, bytes)):
            raise ValueError("each command must be an argv sequence")
        command = tuple(argv)
        if not command or any(not isinstance(item, str) or not item for item in command):
            raise ValueError("command argv must contain non-empty strings")
        commands.append(command)
    return tuple(commands)


def _acceptance_value(
    probe: ExternalAcceptanceProbe,
    result: ExternalCommandResult,
) -> Any:
    if not result.successful:
        detail = result.infrastructure_error or f"return code {result.returncode}"
        raise ValueError(f"probe {probe.name!r} failed: {detail}")
    output = result.stdout
    if probe.kind == "text":
        return output.strip()
    if probe.kind == "json":
        return json.loads(output)
    lines = tuple(line.strip() for line in output.splitlines() if line.strip())
    if probe.kind == "lines":
        return sorted(set(lines))
    if probe.kind == "pytest_collection":
        node_ids = sorted(
            {
                line
                for line in lines
                if "::" in line
                and not line.startswith(("=", "ERROR ", "WARNING "))
            }
        )
        if not node_ids:
            raise ValueError("pytest collection probe produced no node IDs")
        return {"count": len(node_ids), "node_ids": node_ids}
    if probe.kind == "pytest_passed_count":
        matches = re.findall(r"(?m)(\d+)\s+passed\b", output)
        if not matches:
            raise ValueError("pytest output has no passed-test count")
        return int(matches[-1])
    raise ValueError(f"unsupported acceptance probe kind: {probe.kind}")


def _tree_digest(root: Path) -> str:
    if not root.is_dir():
        raise FileNotFoundError(f"project root is not a directory: {root}")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        relative_parts = Path(relative).parts
        if (
            any(part in _IGNORED_WORKSPACE_PARTS for part in relative_parts)
            or path.suffix == ".pyc"
        ):
            continue
        if path.is_symlink():
            digest.update(b"L\0")
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(str(path.readlink()).encode())
        elif path.is_file():
            digest.update(b"F\0")
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _infrastructure_error(
    stage: str, error: Exception
) -> ExternalInfrastructureError:
    return ExternalInfrastructureError(stage, type(error).__name__, str(error))


def _command_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _run_validation_command(
    argv: tuple[str, ...], cwd: Path, timeout: float
) -> ExternalCommandResult:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
        )
        return ExternalCommandResult(
            argv=argv,
            returncode=completed.returncode,
            stdout=_command_text(completed.stdout),
            stderr=_command_text(completed.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        message = f"timeout after {timeout:g} seconds"
        return ExternalCommandResult(
            argv=argv,
            returncode=None,
            stdout=_command_text(exc.stdout),
            stderr=_command_text(exc.stderr),
            timed_out=True,
            infrastructure_error=message,
        )
    except OSError as exc:
        return ExternalCommandResult(
            argv=argv,
            returncode=None,
            stdout="",
            stderr="",
            infrastructure_error=f"{type(exc).__name__}: {exc}",
        )


def generate_pilot_manifest(
    projects: Iterable[ExternalProject],
    *,
    seed: int = 0,
    safe_per_project: int = 10,
    unsafe_per_project: int = 15,
    base_dir: str | Path | None = None,
    balanced_safe: bool = False,
    allow_repeated_move_targets: bool = False,
) -> ExternalBenchmarkManifest:
    """Generate non-human candidates; balanced safe labels require preflight."""
    project_values = tuple(sorted(projects, key=lambda item: item.id))
    base = Path(base_dir).resolve() if base_dir is not None else Path.cwd()
    tasks: list[ExternalTaskSpec] = []
    with tempfile.TemporaryDirectory(prefix="meldra-pilot-manifest-") as state_directory:
        for project in project_values:
            root = Path(project.root) if Path(project.root).is_absolute() else base / project.root
            world = SoftwareWorld.scan(root, state_path=Path(state_directory) / f"{project.id}.json")
            if balanced_safe:
                tasks.extend(
                    _balanced_safe_tasks(
                        world,
                        project,
                        seed,
                        safe_per_project,
                        allow_repeated_move_targets=allow_repeated_move_targets,
                    )
                )
                tasks.extend(
                    _pilot_project_tasks(
                        world, project, seed, 0, unsafe_per_project
                    )
                )
            else:
                tasks.extend(
                    _pilot_project_tasks(
                        world,
                        project,
                        seed,
                        safe_per_project,
                        unsafe_per_project,
                    )
                )
    return ExternalBenchmarkManifest(projects=project_values, tasks=tuple(tasks), seed=seed)


def _balanced_safe_tasks(
    world: SoftwareWorld,
    project: ExternalProject,
    seed: int,
    safe_quota: int,
    *,
    allow_repeated_move_targets: bool = False,
) -> tuple[ExternalTaskSpec, ...]:
    desired_each, remainder = divmod(safe_quota, len(_OPERATIONS))
    quotas = {
        operation: desired_each + (index < remainder)
        for index, operation in enumerate(_OPERATIONS)
    }
    all_entities = tuple(
        sorted(
            world.program.entities,
            key=lambda item: (_rank(seed, project.id, item.id), item.id),
        )
    )
    id_multiplicity = Counter(item.id for item in all_entities)
    entities = tuple(
        item
        for item in all_entities
        if id_multiplicity[item.id] == 1
        and _balanced_candidate_eligible(item)
    )
    names_by_module = {(item.module, item.name) for item in all_entities}
    existing_modules = tuple(
        sorted(
            {
                snapshot.module
                for snapshot in world.program.files
                if _balanced_file_eligible(snapshot.path)
            }
        )
    )
    candidates: dict[str, list[tuple[Entity, str]]] = {
        operation: [] for operation in _OPERATIONS
    }

    rename_attempt = 0
    rename_limit = len(entities) * max(1, quotas["rename"])
    while (
        len(candidates["rename"]) < quotas["rename"]
        and rename_attempt < rename_limit
    ):
        target = entities[rename_attempt % len(entities)] if entities else None
        if target is not None:
            payload = _unique_name(
                target,
                names_by_module,
                f"meldra_safe_rename_{rename_attempt}",
            )
            task = _planner_candidate_task(
                project, target, "rename", payload, len(candidates["rename"])
            )
            if _task_plan_ready(world, task):
                candidates["rename"].append((target, payload))
        rename_attempt += 1

    if quotas["move"]:
        ready_move_pairs: list[tuple[Entity, str]] = []
        for target in entities:
            if "." in target.qualname:
                continue
            destinations = sorted(
                (
                    destination
                    for destination in existing_modules
                    if destination != target.module
                    and (destination, target.name) not in names_by_module
                ),
                key=lambda destination: (
                    _rank(
                        seed,
                        project.id + ":safe_move",
                        target.id + ":" + destination,
                    ),
                    destination,
                ),
            )
            for destination in destinations:
                task = _planner_candidate_task(
                    project,
                    target,
                    "move",
                    destination,
                    len(ready_move_pairs),
                )
                if _task_plan_ready(world, task):
                    ready_move_pairs.append((target, destination))
                    if not allow_repeated_move_targets:
                        break
                    if len(ready_move_pairs) == quotas["move"]:
                        break
            if len(ready_move_pairs) == quotas["move"]:
                break
        candidates["move"].extend(ready_move_pairs)

    functions = tuple(
        item
        for item in entities
        if item.kind in {"function", "async_function"}
        and item.signature_span is not None
    )
    signature_attempt = 0
    signature_limit = len(functions) * max(1, quotas["change_signature"])
    while (
        len(candidates["change_signature"]) < quotas["change_signature"]
        and signature_attempt < signature_limit
    ):
        target = (
            functions[signature_attempt % len(functions)]
            if functions
            else None
        )
        if target is not None:
            parameter = f"meldra_optional_{signature_attempt}"
            payload = _optional_signature(target.signature_source, parameter)
            if payload is not None:
                task = _planner_candidate_task(
                    project,
                    target,
                    "change_signature",
                    payload,
                    len(candidates["change_signature"]),
                )
                if _task_plan_ready(world, task):
                    candidates["change_signature"].append((target, payload))
        signature_attempt += 1

    missing = {
        operation: quotas[operation] - len(candidates[operation])
        for operation in _OPERATIONS
        if len(candidates[operation]) < quotas[operation]
    }
    if missing:
        detail = ", ".join(
            f"{operation}={count}" for operation, count in sorted(missing.items())
        )
        raise ValueError(
            f"project {project.id} lacks ready balanced-safe candidates: {detail}"
        )

    tasks: list[ExternalTaskSpec] = []
    for operation in _OPERATIONS:
        for index, (target, payload) in enumerate(candidates[operation]):
            tasks.append(
                _planner_candidate_task(
                    project, target, operation, payload, index
                )
            )
    return tuple(tasks)


def _planner_candidate_task(
    project: ExternalProject,
    target: Entity,
    operation: str,
    payload: str,
    index: int,
) -> ExternalTaskSpec:
    return ExternalTaskSpec.safe(
        id=(
            f"{project.id}:safe:{operation}:"
            f"planner_preflight_candidate:{index:02d}"
        ),
        project=project.id,
        operation=operation,
        target=target.id,
        payload=payload,
        label_source="validated_fixture",
        oracle="planner_preflight_candidate",
        metadata=(("target_revision", target.revision_hash),),
    )


def _task_plan_ready(world: SoftwareWorld, task: ExternalTaskSpec) -> bool:
    plan, _ = _plan_task(world, task)
    return plan.ready


def _balanced_candidate_eligible(entity: Entity) -> bool:
    return (
        _balanced_file_eligible(entity.file)
        and not any(
            segment.lower().startswith("test_")
            for segment in entity.qualname.split(".")
        )
    )


def _balanced_file_eligible(file: str) -> bool:
    parts = Path(file).parts
    stem = Path(file).stem.lower()
    return (
        not any(
            part.lower() in {"test", "testing", "tests"}
            or part.lower().startswith(
                ("demo", "doc", "example", "sample", "tutorial")
            )
            for part in parts[:-1]
        )
        and stem not in {"test", "tests", "testing", "conftest"}
        and not stem.startswith("test_")
        and not stem.endswith(("_test", "_tests", "_testing"))
    )


def _optional_signature(signature: str, parameter: str) -> str | None:
    stripped = signature.rstrip()
    if (
        not stripped.startswith("(")
        or not stripped.endswith(")")
        or "**" in stripped
        or parameter in stripped
    ):
        return None
    prefix = stripped[:-1]
    if prefix.endswith("("):
        separator = ""
    elif prefix.endswith(","):
        separator = " "
    else:
        separator = ", "
    return f"{prefix}{separator}{parameter}=None)"


def _pilot_project_tasks(world: SoftwareWorld, project: ExternalProject, seed: int,
                         safe_quota: int, unsafe_quota: int) -> tuple[ExternalTaskSpec, ...]:
    entities = sorted(world.program.entities, key=lambda item: (_rank(seed, project.id, item.id), item.id))
    safe_entities = [item for item in entities if not world.program.uncertain_references_to(item.id)]
    public = [item for item in entities if item.public]
    functions = [item for item in entities if item.kind in {"function", "async_function"}]
    if safe_quota and not safe_entities:
        raise ValueError(f"project {project.id} has no policy-safe rename candidates")
    if unsafe_quota and not (public or functions):
        raise ValueError(f"project {project.id} has no policy-unsafe candidates")
    tasks: list[ExternalTaskSpec] = []
    names_by_module = {(item.module, item.name) for item in entities}
    for index in range(safe_quota):
        target = safe_entities[index % len(safe_entities)]
        payload = _unique_name(target, names_by_module, f"meldra_safe_{index}")
        tasks.append(_policy_task(project, target, "rename", payload, True, index,
                                  "policy_safe_static_rename", allow_public=True))

    unsafe: list[ExternalTaskSpec] = []
    desired_each = unsafe_quota // 3
    remainder = unsafe_quota % 3
    quotas = (desired_each + (remainder > 0), desired_each + (remainder > 1), desired_each)
    for index in range(quotas[0]):
        if not public:
            break
        target = public[index % len(public)]
        payload = _unique_name(target, names_by_module, f"meldra_strict_{index}")
        unsafe.append(_policy_task(project, target, "rename", payload, False, index,
                                   "policy_strict_public_rename", allow_public=False))
    for index in range(quotas[1]):
        if not functions:
            break
        target = functions[index % len(functions)]
        unsafe.append(_policy_task(project, target, "change_signature", f"(*, meldra_required_{index})",
                                   False, index, "policy_required_signature_without_migration", allow_public=True))
    collisions = [(source, destination.module) for source in entities for destination in entities
                  if source.id != destination.id and source.module != destination.module and source.name == destination.name]
    collisions.sort(key=lambda item: (_rank(seed, project.id + ":collision", item[0].id + ":" + item[1]), item[0].id, item[1]))
    for index, (target, module) in enumerate(collisions[: quotas[2]]):
        unsafe.append(_policy_task(project, target, "move", module, False, index,
                                   "policy_move_name_collision", allow_public=True))
    backfill = 0
    while len(unsafe) < unsafe_quota:
        if public:
            target = public[backfill % len(public)]
            payload = _unique_name(target, names_by_module, f"meldra_strict_backfill_{backfill}")
            unsafe.append(_policy_task(project, target, "rename", payload, False, len(unsafe),
                                       "policy_strict_public_rename", allow_public=False))
        elif functions:
            target = functions[backfill % len(functions)]
            unsafe.append(_policy_task(project, target, "change_signature", f"(*, meldra_required_backfill_{backfill})",
                                       False, len(unsafe), "policy_required_signature_without_migration", allow_public=True))
        backfill += 1
    return tuple(tasks + unsafe)


def _policy_task(project: ExternalProject, target: Entity, operation: str, payload: str,
                 expected_safe: bool, index: int, oracle: str, *, allow_public: bool) -> ExternalTaskSpec:
    task_id = f"{project.id}:{'safe' if expected_safe else 'unsafe'}:{operation}:{oracle}:{index:02d}"
    return ExternalTaskSpec(
        id=task_id, project=project.id, operation=operation, target=target.id, payload=payload,
        expected_safe=expected_safe, label_source="policy", oracle=oracle,
        allow_public_api_break=allow_public,
        metadata=(("target_revision", target.revision_hash),),
    )


def _unique_name(target: Entity, names: set[tuple[str, str]], stem: str) -> str:
    candidate = stem
    suffix = 0
    while (target.module, candidate) in names:
        suffix += 1
        candidate = f"{stem}_{suffix}"
    return candidate


def _make_breakdown(results: tuple[ExternalPlanResult, ...], attribute: str,
                    order: tuple[str, ...] | None = None) -> tuple[BenchmarkBreakdown, ...]:
    keys = {str(getattr(item, attribute)) for item in results}
    ordered = [item for item in (order or ()) if item in keys] + sorted(keys - set(order or ()))
    return tuple(BenchmarkBreakdown(key, ValidationMetrics.from_results(
        item for item in results if getattr(item, attribute) == key)) for key in ordered)


def _held_out(project: str, entity: Entity) -> HeldOutEntity:
    return HeldOutEntity(project, entity.id, entity.revision_hash, entity.fqname, entity.kind, entity.file)


def _plan_task(world: SoftwareWorld, task: ExternalTaskSpec) -> tuple[Any, EditCapability]:
    target = world.program.entity(task.target)
    common = {
        "allow_public_api_break": task.allow_public_api_break,
        "allow_new_dependencies": task.allow_new_dependencies,
    }
    if task.operation == "rename":
        capability = EditCapability.rename(target.id, **common)
        plan = world.plan_rename(target.id, task.payload, capability)
    elif task.operation == "move":
        capability = EditCapability.move(target.id, **common)
        plan = world.plan_move(target.id, task.payload, capability)
    else:
        capability = EditCapability.change_signature(target.id, **common)
        plan = world.plan_change_signature(
            target.id,
            task.payload,
            capability,
            argument_values=dict(task.argument_values),
        )
    return plan, capability


def _run_task(world: SoftwareWorld, project: ExternalProject, task: ExternalTaskSpec) -> ExternalPlanResult:
    target = world.program.entity(task.target)
    plan, _ = _plan_task(world, task)
    reasons = tuple(sorted(item.kind for item in plan.obligations if item.blocking))
    outcome = {(True, True): "true_positive", (False, True): "false_positive",
               (True, False): "false_negative", (False, False): "true_negative"}[(task.expected_safe, plan.ready)]
    return ExternalPlanResult(
        task.id, project.id, project.category, task.operation, task.target, task.expected_safe,
        task.label_source, task.oracle, plan.ready, outcome, target.id, target.revision_hash,
        plan.change.id, len(plan.edits), plan.affected_files, reasons,
    )


def _error_result(task: ExternalTaskSpec, project: ExternalProject, error: Exception) -> ExternalPlanResult:
    return ExternalPlanResult(
        task.id, project.id, project.category, task.operation, task.target, task.expected_safe,
        task.label_source, task.oracle, None, "infrastructure_error",
        error_kind=type(error).__name__, error_message=str(error),
    )


ProjectSpec = ExternalProject
TaskSpec = ExternalTaskSpec
BenchmarkManifest = ExternalBenchmarkManifest
BenchmarkReport = ExternalBenchmarkReport
