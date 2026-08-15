"""Provider-neutral, tamper-evident paired AI evidence.

This module deliberately has no provider imports and performs no work at import time.
A provider is called only by :class:`PairedEvidenceRunner`, which is an explicit
experiment entry point.  Reports are records of observed provider runs; they are not
predictions and an empty/unavailable run cannot become measured evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

SCHEMA_VERSION = 1
AI_EVIDENCE_CONTRACT = "merlo.ai-evidence.v1"
ARMS = ("semantic", "text")
MEASURED = "measured"
UNMEASURED = "unmeasured"
UNAVAILABLE = "unavailable"


class AIEvidenceError(ValueError):
    """Raised when an evidence manifest or record is not admissible."""


class AIProvider(Protocol):
    """Minimal transport-neutral provider protocol used by the explicit runner."""

    identity: "AIProviderIdentity"

    def __call__(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AIEvidenceError("AI evidence contains a non-canonical JSON value") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _hash(value: Any, code: str = "invalid hash") -> str:
    if type(value) is not str or len(value) != 64:
        raise AIEvidenceError(code)
    try:
        int(value, 16)
    except ValueError as exc:
        raise AIEvidenceError(code) from exc
    return value


def _text(value: Any, code: str) -> str:
    if type(value) is not str or not value.strip():
        raise AIEvidenceError(code)
    return value


def _integer(value: Any, code: str, *, minimum: int = 0) -> int:
    if type(value) is not int or isinstance(value, bool) or value < minimum:
        raise AIEvidenceError(code)
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _freeze(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AIEvidenceError("AI evidence contains a non-finite number")
        raise AIEvidenceError("AI evidence forbids floating point values")
    return value

_ARM_ALIASES = {
    "semantic": "semantic",
    "merlo": "semantic",
    "semantic_protocol": "semantic",
    "text": "text",
    "baseline": "text",
    "text_baseline": "text",
}


def _canonical_arm(value: Any) -> str:
    if type(value) is not str or value not in _ARM_ALIASES:
        raise AIEvidenceError("AIRecordInvalidArm")
    return _ARM_ALIASES[value]

def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _required(value: Any, fields: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise AIEvidenceError(code)
    return value


def _ratio(numerator: int, denominator: int) -> dict[str, int] | None:
    """Return a canonical integer ratio (never a float)."""
    if denominator == 0:
        return None
    divisor = math.gcd(abs(numerator), abs(denominator))
    divisor = divisor or 1
    numerator //= divisor
    denominator //= divisor
    if denominator < 0:
        numerator, denominator = -numerator, -denominator
    return {"numerator": numerator, "denominator": denominator}


def _ratio_valid(value: Any) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, Mapping)
        and set(value) == {"numerator", "denominator"}
        and type(value["numerator"]) is int
        and type(value["denominator"]) is int
        and value["denominator"] > 0
        and _ratio(value["numerator"], value["denominator"]) == dict(value)
    )


def _marker(value: Any) -> bool:
    """Reject explicit synthetic/placeholder values without guessing about prose."""
    if isinstance(value, Mapping):
        return any(_marker(k) or _marker(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_marker(item) for item in value)
    if isinstance(value, str):
        text = value.strip().lower()
        return text in {
            "synthetic",
            "placeholder",
            "fake",
            "dummy",
            "mock",
            "fixture-only",
            "not measured",
            "unmeasured",
        }
    return False


@dataclass(frozen=True)
class AIProviderIdentity:
    provider: str
    model: str
    revision: str
    endpoint: str | None = None

    def __post_init__(self) -> None:
        _text(self.provider, "AIProviderMissing")
        _text(self.model, "AIModelMissing")
        _text(self.revision, "AIRevisionMissing")
        if self.endpoint is not None:
            _text(self.endpoint, "AIEndpointInvalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "revision": self.revision,
            "endpoint": self.endpoint,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AIProviderIdentity":
        _required(value, {"provider", "model", "revision", "endpoint"}, "AIProviderSchemaMismatch")
        return cls(value["provider"], value["model"], value["revision"], value["endpoint"])


ProviderIdentity = AIProviderIdentity


Oracle = Callable[[Mapping[str, Any]], bool]


@dataclass(frozen=True)
class AITask:
    """One frozen task and its success oracle.

    ``task`` and ``dataset`` are optional payloads used solely to derive stable
    hashes.  A caller may instead provide the already frozen hashes.
    """

    task_id: str
    prompt: str
    success_oracle: str | Oracle
    task: Any = None
    dataset: Any = None
    prompt_hash: str = ""
    task_hash: str = ""
    dataset_hash: str = ""

    def __post_init__(self) -> None:
        _text(self.task_id, "AITaskMissingId")
        _text(self.prompt, "AITaskMissingPrompt")
        if not isinstance(self.success_oracle, str) and not callable(self.success_oracle):
            raise AIEvidenceError("AITaskInvalidOracle")
        expected_prompt_hash = _digest(self.prompt)
        expected_task_hash = _digest(self.task if self.task is not None else self.task_id)
        expected_dataset_hash = _digest(self.dataset if self.dataset is not None else self.task_id)
        prompt_hash = self.prompt_hash or expected_prompt_hash
        task_hash = self.task_hash or expected_task_hash
        dataset_hash = self.dataset_hash or expected_dataset_hash
        _hash(prompt_hash, "AITaskInvalidPromptHash")
        _hash(task_hash, "AITaskInvalidTaskHash")
        _hash(dataset_hash, "AITaskInvalidDatasetHash")
        # Serialized manifests retain hashes but intentionally omit executable
        # payloads; live tasks must bind every supplied hash to its payload.
        if self.prompt != "<frozen prompt>" and prompt_hash != expected_prompt_hash:
            raise AIEvidenceError("AITaskPromptHashMismatch")
        if self.task is not None and task_hash != expected_task_hash:
            raise AIEvidenceError("AITaskTaskHashMismatch")
        if self.dataset is not None and dataset_hash != expected_dataset_hash:
            raise AIEvidenceError("AITaskDatasetHashMismatch")
        object.__setattr__(self, "prompt_hash", prompt_hash)
        object.__setattr__(self, "task_hash", task_hash)
        object.__setattr__(self, "dataset_hash", dataset_hash)

    @property
    def oracle_id(self) -> str:
        if isinstance(self.success_oracle, str):
            return self.success_oracle
        return getattr(self.success_oracle, "oracle_id", None) or (
            f"{self.success_oracle.__module__}.{self.success_oracle.__qualname__}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "prompt_hash": self.prompt_hash,
            "task_hash": self.task_hash,
            "dataset_hash": self.dataset_hash,
            "success_oracle": self.oracle_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AITask":
        _required(
            value,
            {"task_id", "prompt_hash", "task_hash", "dataset_hash", "success_oracle"},
            "AITaskSchemaMismatch",
        )
        # A serialized task intentionally does not pretend to be executable; the
        # oracle id is retained for report validation, while run-time callers pass
        # an executable AITask to the runner.
        return cls(
            value["task_id"],
            "<frozen prompt>",
            value["success_oracle"],
            prompt_hash=value["prompt_hash"],
            task_hash=value["task_hash"],
            dataset_hash=value["dataset_hash"],
        )


TaskSpec = AITask
TaskManifest = AITask


@dataclass(frozen=True)
class AIRawTaskRecord:
    task_id: str
    arm: str
    provider: str
    model: str
    revision: str
    prompt_hash: str
    task_hash: str
    dataset_hash: str
    success_oracle: str
    success: bool
    input_tokens: int
    output_tokens: int
    context_tokens: int
    repair_iterations: int
    run_id: str
    output_digest: str
    status: str = MEASURED
    synthetic: bool = False
    placeholder: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict)
    digest: str = ""

    def __post_init__(self) -> None:
        _text(self.task_id, "AIRecordMissingTask")
        if self.arm not in ARMS:
            raise AIEvidenceError("AIRecordInvalidArm")
        _text(self.provider, "AIRecordMissingProvider")
        _text(self.model, "AIRecordMissingModel")
        _text(self.revision, "AIRecordMissingRevision")
        for value, code in (
            (self.prompt_hash, "AIRecordInvalidPromptHash"),
            (self.task_hash, "AIRecordInvalidTaskHash"),
            (self.dataset_hash, "AIRecordInvalidDatasetHash"),
        ):
            _hash(value, code)
        _text(self.success_oracle, "AIRecordMissingOracle")
        if type(self.success) is not bool:
            raise AIEvidenceError("AIRecordInvalidSuccess")
        for name in ("input_tokens", "output_tokens", "context_tokens", "repair_iterations"):
            _integer(getattr(self, name), f"AIRecordInvalid{name.title().replace('_', '')}")
        _text(self.run_id, "AIRecordMissingRunId")
        _hash(self.output_digest, "AIRecordInvalidOutputDigest")
        if self.status != MEASURED:
            raise AIEvidenceError("AIRecordNotMeasured")
        if type(self.synthetic) is not bool or type(self.placeholder) is not bool:
            raise AIEvidenceError("AIRecordInvalidProvenanceFlags")
        if self.synthetic or self.placeholder or _marker(self.raw):
            raise AIEvidenceError("AIRecordSyntheticOrPlaceholder")
        if not isinstance(self.raw, Mapping):
            raise AIEvidenceError("AIRecordRawSchemaMismatch")
        object.__setattr__(self, "raw", _freeze(self.raw))
        expected = _digest(self._payload())
        object.__setattr__(self, "digest", expected)

    def _payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "arm": self.arm,
            "provider": self.provider,
            "model": self.model,
            "revision": self.revision,
            "prompt_hash": self.prompt_hash,
            "task_hash": self.task_hash,
            "dataset_hash": self.dataset_hash,
            "success_oracle": self.success_oracle,
            "success": self.success,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "context_tokens": self.context_tokens,
            "repair_iterations": self.repair_iterations,
            "run_id": self.run_id,
            "output_digest": self.output_digest,
            "status": self.status,
            "synthetic": self.synthetic,
            "placeholder": self.placeholder,
            "raw": _thaw(self.raw),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AIRawTaskRecord":
        fields = {
            "task_id", "arm", "provider", "model", "revision", "prompt_hash", "task_hash",
            "dataset_hash", "success_oracle", "success", "input_tokens", "output_tokens",
            "context_tokens", "repair_iterations", "run_id", "output_digest", "status",
            "synthetic", "placeholder", "raw", "digest",
        }
        _required(value, fields, "AIRecordSchemaMismatch")
        if type(value["digest"]) is not str or value["digest"] != _digest({k: value[k] for k in fields if k != "digest"}):
            raise AIEvidenceError("AIRecordDigestMismatch")
        return cls(**{key: value[key] for key in fields})

    @classmethod
    def from_provider(
        cls,
        task: AITask,
        arm: str,
        identity: AIProviderIdentity,
        response: Mapping[str, Any],
    ) -> "AIRawTaskRecord":
        if not isinstance(response, Mapping):
            raise AIEvidenceError("AIProviderResponseSchemaMismatch")
        response_provider = response.get("provider", identity.provider)
        response_model = response.get("model")
        response_revision = response.get("revision")
        if response_provider != identity.provider or response_model != identity.model or response_revision != identity.revision:
            raise AIEvidenceError("AIProviderMismatch")
        if response.get("task_id", task.task_id) != task.task_id or response.get("arm", arm) != arm:
            raise AIEvidenceError("AIProviderRequestMismatch")
        for field_name, expected, code in (
            ("prompt_hash", task.prompt_hash, "AIProviderPromptHashMismatch"),
            ("task_hash", task.task_hash, "AIProviderTaskHashMismatch"),
            ("dataset_hash", task.dataset_hash, "AIProviderDatasetHashMismatch"),
        ):
            if field_name in response and response[field_name] != expected:
                raise AIEvidenceError(code)
        if "output" not in response and "response" not in response and "output_digest" not in response:
            raise AIEvidenceError("AIProviderMissingOutput")
        output = response.get("output", response.get("response"))
        output_digest = response.get("output_digest") or _digest(output)
        if ("output" in response or "response" in response) and output_digest != _digest(output):
            raise AIEvidenceError("AIProviderOutputDigestMismatch")
        success = response.get("oracle_passed", response.get("success"))
        if success is None and isinstance(task.success_oracle, str):
            success = output == task.success_oracle
        if type(success) is not bool:
            raise AIEvidenceError("AIProviderMissingOracleResult")
        if callable(task.success_oracle):
            try:
                expected_success = task.success_oracle(response)
            except Exception as exc:
                raise AIEvidenceError("AISuccessOracleFailed") from exc
            if type(expected_success) is not bool or expected_success != success:
                raise AIEvidenceError("AISuccessOracleMismatch")
        run_id = response.get("run_id", response.get("provider_run_id"))
        raw = dict(response)
        return cls(
            task_id=task.task_id,
            arm=arm,
            provider=identity.provider,
            model=identity.model,
            revision=identity.revision,
            prompt_hash=task.prompt_hash,
            task_hash=task.task_hash,
            dataset_hash=task.dataset_hash,
            success_oracle=task.oracle_id,
            success=success,
            input_tokens=response.get("input_tokens"),
            output_tokens=response.get("output_tokens"),
            context_tokens=response.get("context_tokens"),
            repair_iterations=response.get("repair_iterations"),
            run_id=run_id,
            output_digest=output_digest,
            status=response.get("status", MEASURED),
            synthetic=response.get("synthetic", False),
            placeholder=response.get("placeholder", False),
            raw=raw,
        )


RawTaskRecord = AIRawTaskRecord


def _normalize_tasks(tasks: Iterable[AITask | Mapping[str, Any]]) -> tuple[AITask, ...]:
    values = tuple(item if isinstance(item, AITask) else AITask.from_dict(item) for item in tasks)
    if not values or len({item.task_id for item in values}) != len(values):
        raise AIEvidenceError("AITaskSetInvalid")
    return values


def _normalize_schedule(schedule: Any, task_ids: Sequence[str]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if schedule is None:
        schedule = [{"task_id": task_id, "arms": list(ARMS)} for task_id in sorted(task_ids)]
    items: list[tuple[str, tuple[str, ...]]] = []
    if isinstance(schedule, Mapping):
        schedule = [{"task_id": task_id, "arms": arms} for task_id, arms in schedule.items()]
    if not isinstance(schedule, (list, tuple)):
        raise AIEvidenceError("AIScheduleSchemaMismatch")
    for item in schedule:
        if isinstance(item, Mapping):
            task_id, arms = item.get("task_id"), item.get("arms", item.get("order"))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            task_id, arms = item
        else:
            raise AIEvidenceError("AIScheduleSchemaMismatch")
        _text(task_id, "AIScheduleMissingTask")
        if not isinstance(arms, (list, tuple)) or tuple(arms) not in (ARMS, ARMS[::-1]):
            raise AIEvidenceError("AIScheduleMustPairBothArms")
        items.append((task_id, tuple(arms)))
    if tuple(task_id for task_id, _ in items) != tuple(sorted(task_ids)):
        raise AIEvidenceError("AIScheduleNonCanonicalOrUnpaired")
    return tuple(items)


def _schedule_dict(schedule: Sequence[tuple[str, Sequence[str]]]) -> list[dict[str, Any]]:
    return [{"task_id": task_id, "arms": list(arms)} for task_id, arms in schedule]


def _validate_record_pairing(records: Sequence[AIRawTaskRecord]) -> None:
    """Require exactly one semantic/text record for every task."""
    seen: set[tuple[str, str]] = set()
    grouped: dict[str, set[str]] = {}
    for record in records:
        key = (record.task_id, record.arm)
        if key in seen:
            raise AIEvidenceError("AIUnpairedDuplicateArm")
        seen.add(key)
        grouped.setdefault(record.task_id, set()).add(record.arm)
    if any(arms != set(ARMS) for arms in grouped.values()):
        raise AIEvidenceError("AIUnpairedMissingArm")


@dataclass(frozen=True)
class AIAggregates:
    task_count: int
    semantic_successes: int
    text_successes: int
    semantic_input_tokens: int
    text_input_tokens: int
    semantic_output_tokens: int
    text_output_tokens: int
    semantic_context_tokens: int
    text_context_tokens: int
    semantic_repairs: int
    text_repairs: int
    ratios: Mapping[str, dict[str, int] | None]

    def __post_init__(self) -> None:
        _integer(self.task_count, "AIAggregateInvalidTaskCount", minimum=0)
        for name in (
            "semantic_successes", "text_successes", "semantic_input_tokens", "text_input_tokens",
            "semantic_output_tokens", "text_output_tokens", "semantic_context_tokens", "text_context_tokens",
            "semantic_repairs", "text_repairs",
        ):
            _integer(getattr(self, name), "AIAggregateInvalidValue")
        if not isinstance(self.ratios, Mapping) or set(self.ratios) != {
            "success", "input_tokens", "output_tokens", "context", "context_reduction", "repairs"
        } or any(not _ratio_valid(value) for value in self.ratios.values()):
            raise AIEvidenceError("AIAggregateRatioSchemaMismatch")
        object.__setattr__(self, "ratios", _freeze(self.ratios))

    @classmethod
    def unavailable(cls) -> "AIAggregates":
        empty = {"success": None, "input_tokens": None, "output_tokens": None, "context": None, "context_reduction": None, "repairs": None}
        return cls(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, empty)

    @classmethod
    def from_records(cls, records: Sequence[AIRawTaskRecord]) -> "AIAggregates":
        if not records:
            raise AIEvidenceError("AIUnmeasuredNoProviderRuns")
        _validate_record_pairing(records)
        grouped = {arm: [item for item in records if item.arm == arm] for arm in ARMS}
        if any(not grouped[arm] for arm in ARMS):
            raise AIEvidenceError("AIUnpairedMissingArm")
        sums = {
            arm: {
                "successes": sum(item.success for item in grouped[arm]),
                "input": sum(item.input_tokens for item in grouped[arm]),
                "output": sum(item.output_tokens for item in grouped[arm]),
                "context": sum(item.context_tokens for item in grouped[arm]),
                "repairs": sum(item.repair_iterations for item in grouped[arm]),
            }
            for arm in ARMS
        }
        return cls(
            len(grouped["semantic"]),
            sums["semantic"]["successes"], sums["text"]["successes"],
            sums["semantic"]["input"], sums["text"]["input"],
            sums["semantic"]["output"], sums["text"]["output"],
            sums["semantic"]["context"], sums["text"]["context"],
            sums["semantic"]["repairs"], sums["text"]["repairs"],
            {
                "success": _ratio(sums["semantic"]["successes"], sums["text"]["successes"]),
                "input_tokens": _ratio(sums["semantic"]["input"], sums["text"]["input"]),
                "output_tokens": _ratio(sums["semantic"]["output"], sums["text"]["output"]),
                "context": _ratio(sums["semantic"]["context"], sums["text"]["context"]),
                "context_reduction": _ratio(
                    sums["text"]["context"] - sums["semantic"]["context"], sums["text"]["context"]
                ),
                "repairs": _ratio(sums["semantic"]["repairs"], sums["text"]["repairs"]),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_count": self.task_count,
            "semantic": {
                "successes": self.semantic_successes,
                "input_tokens": self.semantic_input_tokens,
                "output_tokens": self.semantic_output_tokens,
                "context_tokens": self.semantic_context_tokens,
                "repair_iterations": self.semantic_repairs,
            },
            "text": {
                "successes": self.text_successes,
                "input_tokens": self.text_input_tokens,
                "output_tokens": self.text_output_tokens,
                "context_tokens": self.text_context_tokens,
                "repair_iterations": self.text_repairs,
            },
            "ratios": _thaw(self.ratios),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AIAggregates":
        _required(value, {"task_count", "semantic", "text", "ratios"}, "AIAggregateSchemaMismatch")
        for arm in ARMS:
            _required(value[arm], {"successes", "input_tokens", "output_tokens", "context_tokens", "repair_iterations"}, "AIAggregateSchemaMismatch")
        return cls(
            value["task_count"], value["semantic"]["successes"], value["text"]["successes"],
            value["semantic"]["input_tokens"], value["text"]["input_tokens"],
            value["semantic"]["output_tokens"], value["text"]["output_tokens"],
            value["semantic"]["context_tokens"], value["text"]["context_tokens"],
            value["semantic"]["repair_iterations"], value["text"]["repair_iterations"], value["ratios"],
        )

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


Aggregate = AIAggregates


@dataclass(frozen=True)
class AIEvidenceReport:
    provider: AIProviderIdentity
    prompt_hash: str
    task_hash: str
    dataset_hash: str
    success_oracle: str
    paired_schedule: tuple[tuple[str, tuple[str, ...]], ...]
    records: tuple[AIRawTaskRecord, ...]
    aggregates: AIAggregates
    status: str = MEASURED
    reason: str | None = None
    schema_version: int = SCHEMA_VERSION
    contract: str = AI_EVIDENCE_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.provider, AIProviderIdentity):
            raise AIEvidenceError("AIReportProviderSchemaMismatch")
        for value, code in (
            (self.prompt_hash, "AIReportInvalidPromptHash"),
            (self.task_hash, "AIReportInvalidTaskHash"),
            (self.dataset_hash, "AIReportInvalidDatasetHash"),
        ):
            _hash(value, code)
        _text(self.success_oracle, "AIReportMissingOracle")
        if self.status not in (MEASURED, UNAVAILABLE):
            raise AIEvidenceError("AIReportInvalidStatus")
        if self.status == UNAVAILABLE:
            _text(self.reason, "AIUnavailableReasonMissing")
        elif self.reason is not None:
            raise AIEvidenceError("AIReportInvalidReason")
        if self.schema_version != SCHEMA_VERSION or self.contract != AI_EVIDENCE_CONTRACT:
            raise AIEvidenceError("AIReportNotMeasuredOrVersionMismatch")
        records = tuple(self.records)
        if self.status == UNAVAILABLE:
            if records:
                raise AIEvidenceError("AIUnavailableHasProviderRuns")
            expected_empty = AIAggregates.unavailable()
            if self.aggregates != expected_empty:
                raise AIEvidenceError("AIUnavailableAggregateMismatch")
            schedule_ids = tuple(task_id for task_id, _ in self.paired_schedule)
            schedule = _normalize_schedule(self.paired_schedule, schedule_ids)
        else:
            if not records:
                raise AIEvidenceError("AIUnmeasuredNoProviderRuns")
            # Pairing is deliberately checked before hashes and aggregates so a
            # truncated report is diagnosed as Unpaired, not AggregateMismatch.
            _validate_record_pairing(records)
            if len({item.run_id for item in records}) != len(records):
                raise AIEvidenceError("AIDuplicateProviderRun")
            task_ids = tuple(sorted({item.task_id for item in records}))
            schedule = _normalize_schedule(self.paired_schedule, task_ids)
            scheduled = {task_id: set(arms) for task_id, arms in schedule}
            actual = {
                task_id: {item.arm for item in records if item.task_id == task_id}
                for task_id in task_ids
            }
            if scheduled != actual:
                raise AIEvidenceError("AIUnpairedMissingArm")
            identity = self.provider
            per_task_identity: dict[str, tuple[str, str, str]] = {}
            for record in records:
                current = (record.provider, record.model, record.revision)
                if current != (identity.provider, identity.model, identity.revision):
                    raise AIEvidenceError("AIProviderMismatch")
                previous = per_task_identity.setdefault(record.task_id, current)
                if previous != current:
                    raise AIEvidenceError("AIProviderMismatch")
            # Each raw record carries per-task source hashes.  The report carries
            # deterministic hashes of the complete frozen task columns.
            first_by_task: dict[str, AIRawTaskRecord] = {}
            for record in records:
                first = first_by_task.setdefault(record.task_id, record)
                if (record.prompt_hash, record.task_hash, record.dataset_hash) != (
                    first.prompt_hash, first.task_hash, first.dataset_hash
                ):
                    raise AIEvidenceError("AIStaleRecordHash")
                if record.success_oracle != first.success_oracle:
                    raise AIEvidenceError("AISuccessOracleMismatch")
            ordered = [first_by_task[task_id] for task_id in sorted(first_by_task)]
            expected_columns = (
                _digest([item.prompt_hash for item in ordered]),
                _digest([item.task_hash for item in ordered]),
                _digest([item.dataset_hash for item in ordered]),
            )
            source_columns = (
                ordered[0].prompt_hash,
                ordered[0].task_hash,
                ordered[0].dataset_hash,
            )
            if (self.prompt_hash, self.task_hash, self.dataset_hash) not in (
                expected_columns,
                source_columns if len(ordered) == 1 else (None, None, None),
            ):
                raise AIEvidenceError("AIStaleRecordHash")
            oracle_values = {record.success_oracle for record in records}
            if self.success_oracle not in oracle_values and self.success_oracle != _digest(sorted(oracle_values)):
                raise AIEvidenceError("AISuccessOracleMismatch")
            expected_aggregate = AIAggregates.from_records(records)
            if self.aggregates != expected_aggregate:
                raise AIEvidenceError("AIAggregateMismatch")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "paired_schedule", schedule)
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise AIEvidenceError("AIReportDigestMismatch")
        object.__setattr__(self, "digest", expected)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "provider": self.provider.to_dict(),
            "prompt_hash": self.prompt_hash,
            "task_hash": self.task_hash,
            "dataset_hash": self.dataset_hash,
            "success_oracle": self.success_oracle,
            "paired_schedule": _schedule_dict(self.paired_schedule),
            "records": [item.to_dict() for item in self.records],
            "aggregates": self.aggregates.to_dict(),
            "status": self.status,
            "reason": self.reason,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AIEvidenceReport":
        fields = {
            "schema_version", "contract", "provider", "prompt_hash", "task_hash",
            "dataset_hash", "success_oracle", "paired_schedule", "records",
            "aggregates", "status", "reason", "digest",
        }
        _required(value, fields, "AIReportSchemaMismatch")
        if type(value["digest"]) is not str or value["digest"] != _digest(
            {key: value[key] for key in fields if key != "digest"}
        ):
            raise AIEvidenceError("AIReportDigestMismatch")
        provider = AIProviderIdentity.from_dict(value["provider"])
        records = tuple(AIRawTaskRecord.from_dict(item) for item in value["records"])
        schedule = tuple((item["task_id"], tuple(item["arms"])) for item in value["paired_schedule"])
        return cls(
            provider=provider,
            prompt_hash=value["prompt_hash"],
            task_hash=value["task_hash"],
            dataset_hash=value["dataset_hash"],
            success_oracle=value["success_oracle"],
            paired_schedule=schedule,
            records=records,
            aggregates=AIAggregates.from_dict(value["aggregates"]),
            status=value["status"],
            reason=value["reason"],
            schema_version=value["schema_version"],
            contract=value["contract"],
            digest=value["digest"],
        )

    @classmethod
    def from_json(cls, value: str) -> "AIEvidenceReport":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise AIEvidenceError("AIReportSchemaMismatch") from exc
        return cls.from_dict(payload)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


EvidenceReport = AIEvidenceReport


def _report_hashes(tasks: Sequence[AITask]) -> tuple[str, str, str]:
    return (
        _digest([item.prompt_hash for item in tasks]),
        _digest([item.task_hash for item in tasks]),
        _digest([item.dataset_hash for item in tasks]),
    )


def _provider_identity(provider: Any, explicit: AIProviderIdentity | Mapping[str, Any] | None) -> AIProviderIdentity:
    candidate = explicit
    if candidate is None and provider is not None:
        candidate = getattr(provider, "identity", None)
        if candidate is None:
            candidate = getattr(provider, "provider_identity", None)
        if callable(candidate):
            candidate = candidate()
    if isinstance(candidate, AIProviderIdentity):
        return candidate
    if isinstance(candidate, Mapping):
        return AIProviderIdentity.from_dict(candidate)
    if provider is None:
        return AIProviderIdentity("unconfigured", "unavailable", "unavailable")
    raise AIEvidenceError("AIProviderIdentityRequired")


def _provider_unavailable_reason(provider: Any) -> str | None:
    if provider is None:
        return "No AI provider was explicitly configured"
    candidate = getattr(provider, "unavailable_reason", None)
    if callable(candidate):
        candidate = candidate()
    if candidate:
        return str(candidate)
    # Adapters commonly expose a configured flag or optional API key.  These
    # are checked without invoking transport code.
    if getattr(provider, "configured", True) is False:
        return "AI provider credentials are unavailable"
    if hasattr(provider, "api_key") and not getattr(provider, "api_key"):
        return "AI provider credentials are unavailable"
    return None


def _invoke(provider: Any, request: Mapping[str, Any]) -> Mapping[str, Any]:
    call = provider if callable(provider) else getattr(provider, "run", None)
    if not callable(call):
        raise AIEvidenceError("AIProviderTransportRequired")
    result = call(request)
    if not isinstance(result, Mapping):
        raise AIEvidenceError("AIProviderResponseSchemaMismatch")
    return result


class PairedEvidenceRunner:
    """Execute every frozen task once per arm using an injected provider."""

    def __init__(self, provider: Any = None, provider_identity: AIProviderIdentity | Mapping[str, Any] | None = None) -> None:
        self.provider = provider
        self.identity = _provider_identity(provider, provider_identity)

    def run(self, tasks: Iterable[AITask | Mapping[str, Any]], *, schedule: Any = None) -> AIEvidenceReport:
        frozen_tasks = _normalize_tasks(tasks)
        task_by_id = {item.task_id: item for item in frozen_tasks}
        paired_schedule = _normalize_schedule(schedule, tuple(task_by_id))
        prompt_hash, task_hash, dataset_hash = _report_hashes(frozen_tasks)
        oracle_ids = sorted({task.oracle_id for task in frozen_tasks})
        report_oracle = oracle_ids[0] if len(oracle_ids) == 1 else _digest(oracle_ids)
        unavailable = _provider_unavailable_reason(self.provider)
        if unavailable:
            # Availability is an explicit result; no transport call or
            # synthetic per-arm record is permitted.
            return AIEvidenceReport(
                provider=self.identity,
                prompt_hash=prompt_hash,
                task_hash=task_hash,
                dataset_hash=dataset_hash,
                success_oracle=report_oracle,
                paired_schedule=paired_schedule,
                records=(),
                aggregates=AIAggregates.unavailable(),
                status=UNAVAILABLE,
                reason=unavailable,
            )
        records: list[AIRawTaskRecord] = []
        for task_id, arms in paired_schedule:
            task = task_by_id[task_id]
            for arm in arms:
                request = {
                    "task_id": task.task_id,
                    "arm": arm,
                    "prompt": task.prompt,
                    "prompt_hash": task.prompt_hash,
                    "task_hash": task.task_hash,
                    "dataset_hash": task.dataset_hash,
                    "success_oracle": task.oracle_id,
                }
                response = _invoke(self.provider, request)
                records.append(AIRawTaskRecord.from_provider(task, arm, self.identity, response))
        return AIEvidenceReport(
            provider=self.identity,
            prompt_hash=prompt_hash,
            task_hash=task_hash,
            dataset_hash=dataset_hash,
            success_oracle=report_oracle,
            paired_schedule=paired_schedule,
            records=tuple(records),
            aggregates=AIAggregates.from_records(records),
        )


def run_paired_evidence(
    tasks: Iterable[AITask | Mapping[str, Any]],
    provider: Any = None,
    *,
    provider_identity: AIProviderIdentity | Mapping[str, Any] | None = None,
    schedule: Any = None,
) -> AIEvidenceReport:
    return PairedEvidenceRunner(provider, provider_identity).run(tasks, schedule=schedule)


def aggregate_evidence(records: Iterable[AIRawTaskRecord | Mapping[str, Any]]) -> AIAggregates:
    values = tuple(item if isinstance(item, AIRawTaskRecord) else AIRawTaskRecord.from_dict(item) for item in records)
    return AIAggregates.from_records(values)


def validate_evidence(value: AIEvidenceReport | Mapping[str, Any] | str) -> AIEvidenceReport:
    if isinstance(value, AIEvidenceReport):
        # Reconstructing validates all invariants and catches mutation of a detached mapping.
        return AIEvidenceReport.from_dict(value.to_dict())
    if isinstance(value, str):
        return AIEvidenceReport.from_json(value)
    return AIEvidenceReport.from_dict(value)


def load_task_manifest(path: str | Path) -> tuple[AITask, ...]:
    """Load an existing public JSON task manifest without executing a provider."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AIEvidenceError("AITaskManifestUnreadable") from exc
    if isinstance(payload, Mapping):
        payload = payload.get("tasks")
    if not isinstance(payload, list):
        raise AIEvidenceError("AITaskManifestSchemaMismatch")
    return _normalize_tasks(payload)


__all__ = [
    "AI_EVIDENCE_CONTRACT", "SCHEMA_VERSION", "ARMS", "MEASURED", "UNMEASURED", "UNAVAILABLE",
    "AIEvidenceError", "AIProvider", "AIProviderIdentity", "ProviderIdentity",
    "AITask", "TaskSpec", "TaskManifest", "AIRawTaskRecord", "RawTaskRecord",
    "AIAggregates", "Aggregate", "AIEvidenceReport", "EvidenceReport",
    "PairedEvidenceRunner", "run_paired_evidence", "aggregate_evidence",
    "validate_evidence", "load_task_manifest",
]
