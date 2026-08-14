from __future__ import annotations

import hashlib
import json
import shutil
import statistics
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence


BASELINE_TOOLS = ("source", "search", "edit", "test")
MELDRA_TOOLS = (
    "search",
    "inspect",
    "context",
    "context.expand",
    "impact",
    "change",
    "obligations",
    "evidence",
    "source",
)
MEASURED = "MEASURED"
UNMEASURED = "UNMEASURED"
BLOCKED = "BLOCKED"


def _json_value(value: Any) -> Any:
    """Return a detached JSON value with deterministic mapping order."""
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _median(values: Sequence[int | float]) -> float | None:
    return float(statistics.median(values)) if values else None


@dataclass(frozen=True)
class TrialBudget:
    wall_time_seconds: float = 300.0
    input_tokens: int = 100_000
    output_tokens: int = 25_000
    tool_calls: int = 100
    iterations: int = 25

    def __post_init__(self) -> None:
        if self.wall_time_seconds <= 0:
            raise ValueError("wall_time_seconds must be positive")
        for name in ("input_tokens", "output_tokens", "tool_calls", "iterations"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "wall_time_seconds": self.wall_time_seconds,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tool_calls": self.tool_calls,
            "iterations": self.iterations,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TrialBudget":
        return cls(
            wall_time_seconds=float(value.get("wall_time_seconds", 300.0)),
            input_tokens=int(value.get("input_tokens", 100_000)),
            output_tokens=int(value.get("output_tokens", 25_000)),
            tool_calls=int(value.get("tool_calls", 100)),
            iterations=int(value.get("iterations", 25)),
        )


@dataclass(frozen=True)
class TaskManifest:
    task_id: str
    repo: str
    root: str
    prompt: str
    expected_files: tuple[str, ...] = ()
    expected_contracts: tuple[str, ...] = ()
    test_argv: tuple[str, ...] = ()
    budget: TrialBudget = field(default_factory=TrialBudget)
    sequence_id: str | None = None
    sequence_index: int = 0

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id is required")
        if not self.root:
            raise ValueError("root is required")
        if not self.prompt:
            raise ValueError("prompt is required")
        if not self.repo:
            raise ValueError("repo is required")
        if self.sequence_index < 0:
            raise ValueError("sequence_index must be non-negative")
        if any(not isinstance(item, str) or not item for item in self.test_argv):
            raise ValueError("test_argv must contain non-empty strings")
        for relative in self.expected_files:
            if not isinstance(relative, str) or not relative:
                raise ValueError(
                    "expected_files must contain safe relative paths"
                )
            path = Path(relative)
            if path.is_absolute() or relative == "." or ".." in path.parts:
                raise ValueError(
                    "expected_files must contain safe relative paths"
                )
        if any(
            not isinstance(item, str) or not item
            for item in self.expected_contracts
        ):
            raise ValueError("expected_contracts must be non-empty strings")
        object.__setattr__(
            self, "expected_files", tuple(sorted(set(self.expected_files)))
        )
        object.__setattr__(
            self,
            "expected_contracts",
            tuple(sorted(set(self.expected_contracts))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "repo": self.repo,
            "root": self.root,
            "prompt": self.prompt,
            "expected_files": list(self.expected_files),
            "expected_contracts": list(self.expected_contracts),
            "test_argv": list(self.test_argv),
            "budget": self.budget.to_dict(),
            "sequence_id": self.sequence_id,
            "sequence_index": self.sequence_index,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskManifest":
        return cls(
            task_id=str(value["task_id"]),
            repo=str(value["repo"]),
            root=str(value["root"]),
            prompt=str(value["prompt"]),
            expected_files=tuple(str(item) for item in value.get("expected_files", ())),
            expected_contracts=tuple(
                str(item) for item in value.get("expected_contracts", ())
            ),
            test_argv=tuple(str(item) for item in value.get("test_argv", ())),
            budget=TrialBudget.from_dict(value.get("budget", {})),
            sequence_id=(
                str(value["sequence_id"])
                if value.get("sequence_id") is not None
                else None
            ),
            sequence_index=int(value.get("sequence_index", 0)),
        )


@dataclass(frozen=True)
class ProviderIdentity:
    provider: str
    model: str
    endpoint: str | None = None
    revision: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "endpoint": self.endpoint,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProviderIdentity":
        return cls(
            provider=str(value["provider"]),
            model=str(value["model"]),
            endpoint=(str(value["endpoint"]) if value.get("endpoint") else None),
            revision=(str(value["revision"]) if value.get("revision") else None),
        )


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": _json_value(dict(self.arguments))}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ToolCall":
        return cls(str(value["name"]), _json_value(value.get("arguments", {})))


@dataclass(frozen=True)
class ProviderResponse:
    tool_calls: tuple[ToolCall, ...] = ()
    final: Mapping[str, Any] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    wall_time_ms: int = 0
    status: str = MEASURED
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in (MEASURED, UNMEASURED, BLOCKED):
            raise ValueError(f"unknown provider status: {self.status}")
        if min(self.input_tokens, self.output_tokens, self.wall_time_ms) < 0:
            raise ValueError("usage values must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_calls": [item.to_dict() for item in self.tool_calls],
            "final": _json_value(dict(self.final)) if self.final is not None else None,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "wall_time_ms": self.wall_time_ms,
            "status": self.status,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProviderResponse":
        return cls(
            tool_calls=tuple(
                ToolCall.from_dict(item) for item in value.get("tool_calls", ())
            ),
            final=(
                _json_value(value["final"])
                if value.get("final") is not None
                else None
            ),
            input_tokens=int(value.get("input_tokens", 0)),
            output_tokens=int(value.get("output_tokens", 0)),
            wall_time_ms=int(value.get("wall_time_ms", 0)),
            status=str(value.get("status", MEASURED)),
            error=(str(value["error"]) if value.get("error") else None),
        )


@dataclass(frozen=True)
class ToolEvent:
    index: int
    iteration: int
    arm: str
    name: str
    arguments: Mapping[str, Any]
    result: Mapping[str, Any]
    status: str
    wall_time_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "iteration": self.iteration,
            "arm": self.arm,
            "name": self.name,
            "arguments": _json_value(dict(self.arguments)),
            "result": _json_value(dict(self.result)),
            "status": self.status,
            "wall_time_ms": self.wall_time_ms,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ToolEvent":
        return cls(
            index=int(value["index"]),
            iteration=int(value["iteration"]),
            arm=str(value["arm"]),
            name=str(value["name"]),
            arguments=_json_value(value.get("arguments", {})),
            result=_json_value(value.get("result", {})),
            status=str(value["status"]),
            wall_time_ms=int(value.get("wall_time_ms", 0)),
        )


@dataclass(frozen=True)
class ProviderRequest:
    manifest: TaskManifest
    arm: str
    iteration: int
    identity: ProviderIdentity
    budget: TrialBudget
    tools: tuple[str, ...]
    events: tuple[ToolEvent, ...]


class AgentProvider(Protocol):
    @property
    def identity(self) -> ProviderIdentity: ...

    @property
    def unavailable_reason(self) -> str | None: ...

    def complete(self, request: ProviderRequest) -> ProviderResponse: ...


class FakeProvider:
    """Stateless scripted provider: response selection is request-key based."""

    def __init__(
        self,
        responses: Sequence[ProviderResponse | Mapping[str, Any]]
        | Mapping[str, Sequence[ProviderResponse | Mapping[str, Any]]]
        | Callable[[ProviderRequest], ProviderResponse],
        *,
        model: str = "fake-model",
        revision: str = "deterministic",
        identity: ProviderIdentity | None = None,
    ) -> None:
        self._responses = responses
        self._identity = identity or ProviderIdentity(
            "fake", model, revision=revision
        )
        self.calls: list[ProviderRequest] = []

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    @property
    def unavailable_reason(self) -> str | None:
        return None

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if callable(self._responses):
            return self._responses(request)
        responses: Sequence[ProviderResponse | Mapping[str, Any]]
        if isinstance(self._responses, Mapping):
            responses = ()
            for key in (
                f"{request.manifest.task_id}:{request.arm}",
                request.manifest.task_id,
                request.arm,
                "default",
            ):
                if key in self._responses:
                    responses = self._responses[key]
                    break
        else:
            responses = self._responses
        offset = request.iteration - 1
        if offset >= len(responses):
            return ProviderResponse(
                status=BLOCKED,
                error="script exhausted before a final response",
            )
        response = responses[offset]
        return (
            response
            if isinstance(response, ProviderResponse)
            else ProviderResponse.from_dict(response)
        )


class ReplayProvider(FakeProvider):
    """Replay a persistent provider transcript without network access."""

    def __init__(
        self,
        responses: Sequence[ProviderResponse | Mapping[str, Any]]
        | Mapping[str, Sequence[ProviderResponse | Mapping[str, Any]]],
        *,
        identity: ProviderIdentity | None = None,
    ) -> None:
        super().__init__(responses, model="replay-model", revision="recorded")
        self._identity = identity or ProviderIdentity(
            "replay", "replay-model", revision="recorded"
        )

    def to_dict(self) -> dict[str, Any]:
        responses = self._responses
        if isinstance(responses, Mapping):
            payload: Any = {
                key: [
                    item.to_dict() if isinstance(item, ProviderResponse) else _json_value(item)
                    for item in value
                ]
                for key, value in sorted(responses.items())
            }
        else:
            payload = [
                item.to_dict() if isinstance(item, ProviderResponse) else _json_value(item)
                for item in responses
            ]
        return {"identity": self.identity.to_dict(), "responses": payload}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReplayProvider":
        return cls(
            value.get("responses", ()),
            identity=ProviderIdentity.from_dict(value["identity"]),
        )


class OpenAICompatibleProvider:
    """Minimal explicit OpenAI/Fireworks-compatible chat-completions adapter."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str | None,
        timeout_seconds: float = 60.0,
        provider_name: str = "openai-compatible",
    ) -> None:
        if not endpoint or not model:
            raise ValueError("endpoint and model must be explicit")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._identity = ProviderIdentity(provider_name, model, endpoint=endpoint)

    @property
    def identity(self) -> ProviderIdentity:
        return self._identity

    @property
    def unavailable_reason(self) -> str | None:
        return None if self.api_key else "API key was not provided"

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        if not self.api_key:
            return ProviderResponse(status=UNMEASURED, error=self.unavailable_reason)
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "Use only the declared tools. Return a final JSON object with "
                    "success, safe, contracts, and human_interventions."
                ),
            },
            {"role": "user", "content": request.manifest.prompt},
        ]
        for event in request.events:
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(event.to_dict(), sort_keys=True),
                }
            )
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "tools": [_openai_tool(name) for name in request.tools],
                "tool_choice": "auto",
                "temperature": 0,
                "max_tokens": request.budget.output_tokens,
            },
            sort_keys=True,
        ).encode("utf-8")
        http_request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(
                http_request,
                timeout=min(
                    self.timeout_seconds, request.budget.wall_time_seconds
                ),
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, ValueError) as exc:
            return ProviderResponse(
                status=UNMEASURED,
                error=f"provider infrastructure error: {type(exc).__name__}: {exc}",
                wall_time_ms=int((time.perf_counter() - started) * 1000),
            )
        try:
            choice = payload["choices"][0]["message"]
            calls = tuple(_parse_openai_call(item) for item in choice.get("tool_calls", ()))
            final = None
            if not calls and choice.get("content"):
                decoded = json.loads(choice["content"])
                if not isinstance(decoded, dict):
                    raise ValueError("final response is not an object")
                final = decoded
            usage = payload.get("usage", {})
            return ProviderResponse(
                tool_calls=calls,
                final=final,
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                wall_time_ms=int((time.perf_counter() - started) * 1000),
            )
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return ProviderResponse(
                status=UNMEASURED,
                error=f"invalid provider response: {type(exc).__name__}: {exc}",
                wall_time_ms=int((time.perf_counter() - started) * 1000),
            )


@dataclass(frozen=True)
class TrialResult:
    task_id: str
    arm: str
    provider: ProviderIdentity
    budget: TrialBudget
    status: str
    task_success: bool
    first_pass: bool
    tool_calls: int
    input_tokens: int
    output_tokens: int
    iterations: int
    unintended_files: tuple[str, ...]
    false_safe: bool
    false_block: bool
    human_interventions: int
    wall_time_ms: int
    sequence_id: str | None
    sequence_index: int
    events: tuple[ToolEvent, ...]
    error: str | None = None
    responses: tuple[ProviderResponse, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "arm": self.arm,
            "provider": self.provider.to_dict(),
            "budget": self.budget.to_dict(),
            "status": self.status,
            "task_success": self.task_success,
            "first_pass": self.first_pass,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "iterations": self.iterations,
            "unintended_files": list(self.unintended_files),
            "unintended_edit_count": len(self.unintended_files),
            "false_safe": self.false_safe,
            "false_block": self.false_block,
            "human_interventions": self.human_interventions,
            "wall_time_ms": self.wall_time_ms,
            "sequence_id": self.sequence_id,
            "sequence_index": self.sequence_index,
            "events": [item.to_dict() for item in self.events],
            "responses": [item.to_dict() for item in self.responses],
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TrialResult":
        return cls(
            task_id=str(value["task_id"]),
            arm=str(value["arm"]),
            provider=ProviderIdentity.from_dict(value["provider"]),
            budget=TrialBudget.from_dict(value["budget"]),
            status=str(value["status"]),
            task_success=bool(value["task_success"]),
            first_pass=bool(value["first_pass"]),
            tool_calls=int(value["tool_calls"]),
            input_tokens=int(value["input_tokens"]),
            output_tokens=int(value["output_tokens"]),
            iterations=int(value["iterations"]),
            unintended_files=tuple(str(item) for item in value["unintended_files"]),
            false_safe=bool(value["false_safe"]),
            false_block=bool(value["false_block"]),
            human_interventions=int(value["human_interventions"]),
            wall_time_ms=int(value["wall_time_ms"]),
            sequence_id=(str(value["sequence_id"]) if value.get("sequence_id") else None),
            sequence_index=int(value.get("sequence_index", 0)),
            events=tuple(ToolEvent.from_dict(item) for item in value.get("events", ())),
            error=(str(value["error"]) if value.get("error") else None),
            responses=tuple(
                ProviderResponse.from_dict(item)
                for item in value.get("responses", ())
            ),
        )


@dataclass(frozen=True)
class ArmAggregate:
    arm: str
    total_tasks: int
    measured_tasks: int
    unmeasured_tasks: int
    blocked_tasks: int
    successful_tasks: int
    task_success_denominator: int
    task_success_rate: float | None
    first_pass_tasks: int
    first_pass_denominator: int
    first_pass_rate: float | None
    false_safe_tasks: int
    false_safe_denominator: int
    false_safe_rate: float | None
    false_block_tasks: int
    false_block_denominator: int
    false_block_rate: float | None
    long_horizon_sequences: int
    successful_long_horizon_sequences: int
    long_horizon_denominator: int
    long_horizon_success_rate: float | None
    median_tool_calls: float | None
    median_input_tokens: float | None
    median_output_tokens: float | None
    median_iterations: float | None
    median_unintended_edits: float | None
    median_human_interventions: float | None
    median_wall_time_ms: float | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArmAggregate":
        return cls(**{name: value[name] for name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class AgentTrialReport:
    provider: ProviderIdentity
    results: tuple[TrialResult, ...]
    baseline: ArmAggregate
    meldra: ArmAggregate
    manifests: tuple[TaskManifest, ...] = ()

    @property
    def raw_events(self) -> tuple[ToolEvent, ...]:
        return tuple(event for result in self.results for event in result.events)

    @property
    def raw_event_log(self) -> tuple[dict[str, Any], ...]:
        log: list[dict[str, Any]] = []
        for result in self.results:
            for iteration, response in enumerate(result.responses, 1):
                log.append(
                    {
                        "type": "provider_response",
                        "task_id": result.task_id,
                        "arm": result.arm,
                        "iteration": iteration,
                        "payload": response.to_dict(),
                    }
                )
                for event in result.events:
                    if event.iteration != iteration:
                        continue
                    log.append(
                        {
                            "type": "tool_event",
                            "task_id": result.task_id,
                            "arm": result.arm,
                            "iteration": event.iteration,
                            "payload": event.to_dict(),
                        }
                    )
        return tuple(log)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider.to_dict(),
            "results": [item.to_dict() for item in self.results],
            "manifests": [item.to_dict() for item in self.manifests],
            "raw_events": [item.to_dict() for item in self.raw_events],
            "raw_event_log": list(self.raw_event_log),
            "aggregates": {
                "baseline": self.baseline.to_dict(),
                "meldra": self.meldra.to_dict(),
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentTrialReport":
        aggregates = value["aggregates"]
        return cls(
            provider=ProviderIdentity.from_dict(value["provider"]),
            results=tuple(TrialResult.from_dict(item) for item in value["results"]),
            baseline=ArmAggregate.from_dict(aggregates["baseline"]),
            meldra=ArmAggregate.from_dict(aggregates["meldra"]),
            manifests=tuple(
                TaskManifest.from_dict(item)
                for item in value.get("manifests", ())
            ),
        )


class AgentTrialHarness:
    def __init__(self, provider: AgentProvider) -> None:
        self.provider = provider

    def run(self, manifests: Sequence[TaskManifest]) -> AgentTrialReport:
        ordered = tuple(manifests)
        unavailable = getattr(self.provider, "unavailable_reason", None)
        _validate_manifests(ordered, require_roots=not bool(unavailable))
        results: list[TrialResult] = []
        if unavailable:
            for arm in ("baseline", "meldra"):
                for manifest in sorted(
                    ordered,
                    key=lambda item: (
                        item.sequence_id or item.task_id,
                        item.sequence_index,
                        item.task_id,
                    ),
                ):
                    results.append(self.run_task(manifest, arm))
        else:
            with tempfile.TemporaryDirectory(prefix="meldra-agent-trial-") as temporary:
                base = Path(temporary)
                for arm in ("baseline", "meldra"):
                    workspaces: dict[tuple[str, str], Path] = {}
                    for manifest in sorted(
                        ordered,
                        key=lambda item: (
                            item.sequence_id or item.task_id,
                            item.sequence_index,
                            item.task_id,
                        ),
                    ):
                        source = Path(manifest.root).resolve()
                        group = manifest.sequence_id or manifest.task_id
                        key = (str(source), group)
                        if key not in workspaces:
                            destination = base / arm / f"workspace-{len(workspaces):04d}"
                            shutil.copytree(
                                source,
                                destination,
                                ignore=shutil.ignore_patterns(
                                    ".git", ".merlo", "__pycache__", "*.pyc"
                                ),
                            )
                            workspaces[key] = destination
                        results.append(
                            self.run_task(manifest, arm, workspaces[key])
                        )
        results.sort(key=lambda item: (item.task_id, item.arm))
        frozen = tuple(results)
        return AgentTrialReport(
            provider=self.provider.identity,
            results=frozen,
            baseline=aggregate_results(frozen, "baseline"),
            meldra=aggregate_results(frozen, "meldra"),
            manifests=tuple(sorted(ordered, key=lambda item: item.task_id)),
        )

    def run_task(
        self, manifest: TaskManifest, arm: str, workspace: str | Path | None = None
    ) -> TrialResult:
        if arm not in ("baseline", "meldra"):
            raise ValueError("arm must be baseline or meldra")
        unavailable = getattr(self.provider, "unavailable_reason", None)
        if unavailable:
            return TrialResult(
                task_id=manifest.task_id,
                arm=arm,
                provider=self.provider.identity,
                budget=manifest.budget,
                status=UNMEASURED,
                task_success=False,
                first_pass=False,
                tool_calls=0,
                input_tokens=0,
                output_tokens=0,
                iterations=0,
                unintended_files=(),
                false_safe=False,
                false_block=False,
                human_interventions=0,
                wall_time_ms=0,
                sequence_id=manifest.sequence_id,
                sequence_index=manifest.sequence_index,
                events=(),
                error=unavailable,
                responses=(),
            )
        root = Path(workspace or manifest.root).resolve()
        before = _snapshot(root)
        started = time.perf_counter()
        events: list[ToolEvent] = []
        responses: list[ProviderResponse] = []
        input_tokens = 0
        output_tokens = 0
        provider_wall_ms = 0
        final: Mapping[str, Any] | None = None
        status = MEASURED
        error: str | None = None
        iterations = 0
        tools = BASELINE_TOOLS if arm == "baseline" else MELDRA_TOOLS

        while status == MEASURED and final is None:
            if iterations >= manifest.budget.iterations:
                status = BLOCKED
                error = "iteration budget exhausted"
                break
            if time.perf_counter() - started >= manifest.budget.wall_time_seconds:
                status = BLOCKED
                error = "wall-time budget exhausted"
                break
            iterations += 1
            request = ProviderRequest(
                manifest=manifest,
                arm=arm,
                iteration=iterations,
                identity=self.provider.identity,
                budget=manifest.budget,
                tools=tools,
                events=tuple(events),
            )
            response = self.provider.complete(request)
            responses.append(response)
            input_tokens += response.input_tokens
            output_tokens += response.output_tokens
            provider_wall_ms += response.wall_time_ms
            if response.status != MEASURED:
                status = response.status
                error = response.error
                break
            if input_tokens > manifest.budget.input_tokens:
                status = BLOCKED
                error = "input-token budget exceeded"
                break
            if output_tokens > manifest.budget.output_tokens:
                status = BLOCKED
                error = "output-token budget exceeded"
                break
            for call in response.tool_calls:
                if len(events) >= manifest.budget.tool_calls:
                    status = BLOCKED
                    error = "tool-call budget exhausted"
                    break
                event = self._execute_tool(
                    root, manifest, arm, iterations, len(events) + 1, call
                )
                events.append(event)
                if event.status == BLOCKED:
                    status = BLOCKED
                    error = str(event.result.get("error", "tool call blocked"))
                    break
            if status != MEASURED:
                break
            if response.final is not None:
                final = _json_value(dict(response.final))
            elif not response.tool_calls:
                status = BLOCKED
                error = response.error or "provider returned neither tools nor final result"

        after = _snapshot(root)
        changed = tuple(
            sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
        )
        allowed = set(manifest.expected_files)
        unintended = tuple(path for path in changed if path not in allowed)
        verification_events = [
            event for event in events if bool(event.result.get("verification"))
        ]
        tests_pass = (
            bool(verification_events)
            and bool(verification_events[-1].result.get("success"))
            if manifest.test_argv
            else True
        )
        files_exist = all((root / path).is_file() for path in manifest.expected_files)
        contracts = final.get("contracts", {}) if final else {}
        if isinstance(contracts, Sequence) and not isinstance(contracts, (str, bytes)):
            contract_success = all(item in contracts for item in manifest.expected_contracts)
        elif isinstance(contracts, Mapping):
            contract_success = all(bool(contracts.get(item)) for item in manifest.expected_contracts)
        else:
            contract_success = not manifest.expected_contracts
        claimed_success = bool(final and final.get("success"))
        claimed_safe = bool(final and final.get("safe", claimed_success))
        claimed_blocked = bool(final and final.get("blocked")) or status == BLOCKED
        observed_success = tests_pass and files_exist and contract_success
        task_success = status == MEASURED and claimed_success and observed_success
        first_pass = task_success and (
            (bool(verification_events[0].result.get("success")) if verification_events else iterations == 1)
        )
        false_safe = claimed_safe and not observed_success
        false_block = claimed_blocked and observed_success
        interventions = int(final.get("human_interventions", 0)) if final else 0
        wall_time_ms = max(
            provider_wall_ms,
            int((time.perf_counter() - started) * 1000),
        )
        return TrialResult(
            task_id=manifest.task_id,
            arm=arm,
            provider=self.provider.identity,
            budget=manifest.budget,
            status=status,
            task_success=task_success,
            first_pass=first_pass,
            tool_calls=len(events),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            iterations=iterations,
            unintended_files=unintended,
            false_safe=false_safe,
            false_block=false_block,
            human_interventions=interventions,
            wall_time_ms=wall_time_ms,
            sequence_id=manifest.sequence_id,
            sequence_index=manifest.sequence_index,
            events=tuple(events),
            error=error,
            responses=tuple(responses),
        )

    def _execute_tool(
        self,
        root: Path,
        manifest: TaskManifest,
        arm: str,
        iteration: int,
        index: int,
        call: ToolCall,
    ) -> ToolEvent:
        started = time.perf_counter()
        if call.name not in (BASELINE_TOOLS if arm == "baseline" else MELDRA_TOOLS):
            return ToolEvent(
                index,
                iteration,
                arm,
                call.name,
                _json_value(dict(call.arguments)),
                {"error": f"tool {call.name!r} is not available in {arm}"},
                BLOCKED,
                0,
            )
        try:
            result = _dispatch_tool(root, manifest, arm, call)
            status = MEASURED if not result.get("blocked") else BLOCKED
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
            status = BLOCKED
        return ToolEvent(
            index=index,
            iteration=iteration,
            arm=arm,
            name=call.name,
            arguments=_json_value(dict(call.arguments)),
            result=_json_value(result),
            status=status,
            wall_time_ms=int((time.perf_counter() - started) * 1000),
        )


def aggregate_results(results: Sequence[TrialResult], arm: str) -> ArmAggregate:
    selected = tuple(item for item in results if item.arm == arm)
    measured = tuple(item for item in selected if item.status != UNMEASURED)
    all_sequences: dict[str, list[TrialResult]] = {}
    for item in selected:
        if item.sequence_id is not None:
            all_sequences.setdefault(item.sequence_id, []).append(item)
    measured_sequences = {
        identifier: items
        for identifier, items in all_sequences.items()
        if all(item.status != UNMEASURED for item in items)
    }
    successful_sequences = sum(
        bool(items) and all(item.task_success for item in items)
        for items in measured_sequences.values()
    )
    successes = sum(item.task_success for item in measured)
    first_passes = sum(item.first_pass for item in measured)
    false_safe = sum(item.false_safe for item in measured)
    false_block = sum(item.false_block for item in measured)
    denominator = len(measured)
    return ArmAggregate(
        arm=arm,
        total_tasks=len(selected),
        measured_tasks=denominator,
        unmeasured_tasks=sum(item.status == UNMEASURED for item in selected),
        blocked_tasks=sum(item.status == BLOCKED for item in selected),
        successful_tasks=successes,
        task_success_denominator=denominator,
        task_success_rate=_ratio(successes, denominator),
        first_pass_tasks=first_passes,
        first_pass_denominator=denominator,
        first_pass_rate=_ratio(first_passes, denominator),
        false_safe_tasks=false_safe,
        false_safe_denominator=denominator,
        false_safe_rate=_ratio(false_safe, denominator),
        false_block_tasks=false_block,
        false_block_denominator=denominator,
        false_block_rate=_ratio(false_block, denominator),
        long_horizon_sequences=len(all_sequences),
        successful_long_horizon_sequences=successful_sequences,
        long_horizon_denominator=len(measured_sequences),
        long_horizon_success_rate=_ratio(
            successful_sequences, len(measured_sequences)
        ),
        median_tool_calls=_median([item.tool_calls for item in measured]),
        median_input_tokens=_median([item.input_tokens for item in measured]),
        median_output_tokens=_median([item.output_tokens for item in measured]),
        median_iterations=_median([item.iterations for item in measured]),
        median_unintended_edits=_median(
            [len(item.unintended_files) for item in measured]
        ),
        median_human_interventions=_median(
            [item.human_interventions for item in measured]
        ),
        median_wall_time_ms=_median([item.wall_time_ms for item in measured]),
    )


def _validate_manifests(
    manifests: Sequence[TaskManifest], *, require_roots: bool = True
) -> None:
    ids = [item.task_id for item in manifests]
    if len(ids) != len(set(ids)):
        raise ValueError("task_id values must be unique")
    seen_steps: set[tuple[str, int]] = set()
    for item in manifests:
        root = Path(item.root)
        if require_roots and not root.is_dir():
            raise ValueError(f"task root is not a directory: {root}")
        if item.sequence_id is not None:
            step = (item.sequence_id, item.sequence_index)
            if step in seen_steps:
                raise ValueError(f"duplicate sequence step: {step}")
            seen_steps.add(step)


def _snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if any(part in {".git", ".merlo", "__pycache__"} for part in relative.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _safe_path(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("path must be a non-empty relative string")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes task root") from exc
    return candidate


def _dispatch_tool(
    root: Path, manifest: TaskManifest, arm: str, call: ToolCall
) -> dict[str, Any]:
    arguments = dict(call.arguments)
    if call.name == "source":
        path = _safe_path(root, arguments.get("path"))
        return {
            "path": path.relative_to(root).as_posix(),
            "source": path.read_text("utf-8"),
            "source_escape": arm == "meldra",
        }
    if call.name == "search" and arm == "baseline":
        return _text_search(root, arguments)
    if call.name == "edit":
        return _apply_declared_edits(root, (arguments,))
    if call.name == "test":
        return _run_declared_tests(root, manifest)
    if call.name == "change" and "edits" in arguments:
        edits = arguments.get("edits")
        if not isinstance(edits, list) or not edits:
            return {"blocked": True, "error": "change edits must be a non-empty list"}
        return _apply_declared_edits(root, edits)
    if call.name == "evidence" and arguments.get("run_tests"):
        return _run_declared_tests(root, manifest)
    return _meldra_tool(root, manifest, call.name, arguments)


def _text_search(root: Path, arguments: Mapping[str, Any]) -> dict[str, Any]:
    query = arguments.get("query")
    if not isinstance(query, str) or not query:
        raise ValueError("query must be a non-empty string")
    scope = arguments.get("path", ".")
    path = _safe_path(root, scope)
    candidates = [path] if path.is_file() else sorted(path.rglob("*.py"))
    matches: list[dict[str, Any]] = []
    for candidate in candidates:
        for number, line in enumerate(candidate.read_text("utf-8").splitlines(), 1):
            if query.casefold() in line.casefold():
                matches.append(
                    {
                        "path": candidate.relative_to(root).as_posix(),
                        "line": number,
                        "text": line,
                    }
                )
    return {"matches": matches, "match_count": len(matches)}


def _apply_declared_edits(root: Path, edits: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    staged: dict[Path, str] = {}
    for edit in edits:
        path = _safe_path(root, edit.get("path"))
        old = edit.get("old")
        new = edit.get("new")
        if not isinstance(old, str) or not isinstance(new, str):
            raise ValueError("edit old and new must be strings")
        already_staged = path in staged
        source = (
            staged[path]
            if already_staged
            else path.read_text("utf-8") if path.exists() else ""
        )
        if not old:
            if already_staged or path.exists():
                raise ValueError("empty old is only valid for a new file")
            updated = new
        else:
            occurrences = source.count(old)
            if occurrences != 1:
                raise ValueError(
                    f"edit anchor must occur exactly once in {path.name}; found {occurrences}"
                )
            updated = source.replace(old, new, 1)
        staged[path] = updated
    for path in sorted(staged):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(staged[path], encoding="utf-8")
    return {
        "changed_files": [
            path.relative_to(root).as_posix() for path in sorted(staged)
        ],
        "edit_count": len(edits),
    }


def _run_declared_tests(root: Path, manifest: TaskManifest) -> dict[str, Any]:
    if not manifest.test_argv:
        return {"blocked": True, "error": "manifest has no declared test argv"}
    try:
        completed = subprocess.run(
            manifest.test_argv,
            cwd=root,
            timeout=manifest.budget.wall_time_seconds,
            shell=False,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "verification": True,
            "success": False,
            "infrastructure_error": f"{type(exc).__name__}: {exc}",
            "argv": list(manifest.test_argv),
        }
    return {
        "verification": True,
        "success": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "argv": list(manifest.test_argv),
    }


def _meldra_tool(
    root: Path, manifest: TaskManifest, name: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    from research.archive.historical_protocol.merlo.protocol import MerloProtocol
    from research.archive.historical_protocol.merlo.world import SoftwareWorld

    world = SoftwareWorld.scan(root)
    protocol = MerloProtocol(world)
    if name == "search":
        return {"entities": list(protocol.search(str(arguments.get("query", ""))))}
    if name == "inspect":
        return protocol.inspect(str(arguments["entity"]))
    if name == "impact":
        return protocol.impact(str(arguments["entity"]))
    if name == "context":
        capsule = protocol.compile_context(
            str(arguments["entity"]), goal=str(arguments.get("goal", manifest.prompt))
        )
        return capsule.to_dict()
    if name == "context.expand":
        entities = arguments.get("entities", ())
        if not isinstance(entities, list):
            raise ValueError("context.expand entities must be a list")
        return {
            "entities": [protocol.inspect(str(entity)) for entity in entities],
            "sources": [protocol.source_read(str(entity)) for entity in entities],
        }
    if name == "change":
        from research.archive.historical_protocol.merlo.model import EditCapability

        operation = str(arguments.get("operation", ""))
        target = world.program.entity(str(arguments["entity"]))
        capability_options = {
            "allowed_files": manifest.expected_files or None,
            "allow_new_dependencies": bool(
                arguments.get("allow_new_dependencies", False)
            ),
            "allow_public_api_break": bool(
                arguments.get("allow_public_api_break", False)
            ),
        }
        if operation == "rename":
            capability = EditCapability.rename(target.id, **capability_options)
            plan = world.plan_rename(
                target.id,
                str(arguments["new_name"]),
                capability,
                goal=str(arguments.get("goal", manifest.prompt)),
            )
        elif operation == "move":
            capability = EditCapability.move(target.id, **capability_options)
            plan = world.plan_move(
                target.id,
                str(arguments["target_module"]),
                capability,
                goal=str(arguments.get("goal", manifest.prompt)),
            )
        elif operation == "change_signature":
            capability = EditCapability.change_signature(
                target.id, **capability_options
            )
            raw_values = arguments.get("argument_values")
            if raw_values is not None and not isinstance(raw_values, dict):
                raise ValueError("argument_values must be an object")
            values = (
                {str(key): str(value) for key, value in raw_values.items()}
                if raw_values is not None
                else None
            )
            plan = world.plan_change_signature(
                target.id,
                str(arguments["new_signature"]),
                capability,
                argument_values=values,
                goal=str(arguments.get("goal", manifest.prompt)),
            )
        else:
            raise ValueError(
                "change operation must be rename, move, or change_signature"
            )
        changed_files: tuple[str, ...] = ()
        if bool(arguments.get("apply", True)):
            changed_files = world.apply(plan, capability)
        return {
            "plan": plan.to_dict(),
            "validation": protocol.validate(plan),
            "applied": bool(arguments.get("apply", True)),
            "changed_files": list(changed_files),
        }
    if name == "obligations":
        change_id = arguments.get("change_id")
        return {"obligations": list(protocol.obligations(change_id=change_id))}
    if name == "evidence":
        return protocol.evidence(str(arguments["identifier"]))
    raise ValueError(f"unsupported Meldra tool: {name}")


def _openai_tool(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Declared {name} operation; arguments are validated by the harness.",
            "parameters": {"type": "object", "additionalProperties": True},
        },
    }


def _parse_openai_call(value: Mapping[str, Any]) -> ToolCall:
    function = value["function"]
    arguments = json.loads(function.get("arguments", "{}"))
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments are not an object")
    return ToolCall(str(function["name"]), arguments)
