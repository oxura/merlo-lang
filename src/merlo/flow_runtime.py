"""Deterministic, durable execution for typed Merlo flows.

The module deliberately keeps the execution boundary small: handlers, clocks,
checkpoint stores, and executors are supplied by the caller.  Definitions and
receipts are canonical, immutable data and contain no executable Python code.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
import hashlib
import inspect
import json
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence

SCHEMA_VERSION = 1


class FlowSchemaError(ValueError):
    """A flow or receipt does not satisfy the v1 schema."""


class FlowCheckpointError(ValueError):
    """A checkpoint chain is absent, malformed, or tampered with."""


class FlowExecutionError(RuntimeError):
    """An execution could not produce a valid result."""


class CrashInjected(BaseException):
    """A test-only crash marker that is intentionally allowed to escape run()."""


def _immutable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _immutable(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_immutable(v) for v in value)
    if isinstance(value, set | frozenset):
        return tuple(sorted((_immutable(v) for v in value), key=repr))
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest_payload(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _tuple_strings(value: Sequence[str], name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        raise FlowSchemaError(f"{name}MustBeSequence")
    try:
        result = tuple(value)
    except TypeError as exc:
        raise FlowSchemaError(f"{name}MustBeSequence") from exc
    if any(not isinstance(item, str) or not item for item in result):
        raise FlowSchemaError(f"{name}MustContainNonEmptyStrings")
    if len(set(result)) != len(result):
        raise FlowSchemaError(f"Duplicate{name.title()}")
    if result != tuple(sorted(result)):
        raise FlowSchemaError(f"{name}NotCanonical")
    return result


def _check_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise FlowSchemaError(f"Invalid{name}")
    return value


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 0
    backoff: int = 0
    max_delay: int = 0
    max_attempts: int | None = None

    def __post_init__(self) -> None:
        attempts = self.max_attempts
        retries = self.max_retries
        if attempts is not None:
            if not isinstance(attempts, int) or attempts < 1:
                raise FlowSchemaError("InvalidMaxAttempts")
            retries = attempts - 1
            object.__setattr__(self, "max_retries", retries)
        if not isinstance(retries, int) or retries < 0 or retries > 100:
            raise FlowSchemaError("RetryLimitOutOfBounds")
        if not isinstance(self.backoff, int) or self.backoff < 0:
            raise FlowSchemaError("InvalidRetryBackoff")
        if not isinstance(self.max_delay, int) or self.max_delay < 0:
            raise FlowSchemaError("InvalidRetryMaxDelay")
        if self.max_delay and self.backoff > self.max_delay:
            raise FlowSchemaError("RetryBackoffExceedsBound")
        object.__setattr__(self, "max_attempts", retries + 1)

    @property
    def attempts(self) -> int:
        return self.max_retries + 1

    def delay_for(self, retry_number: int) -> int:
        if retry_number < 1:
            return 0
        delay = self.backoff * retry_number
        return min(delay, self.max_delay) if self.max_delay else delay

    def to_dict(self) -> dict[str, Any]:
        return {"max_retries": self.max_retries, "backoff": self.backoff, "max_delay": self.max_delay}


@dataclass(frozen=True)
class Compensation:
    action: str
    compensation_id: str | None = None
    input_type: str = "Any"
    output_type: str = "Any"
    predecessors: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        _check_id(self.action, "CompensationAction")
        if self.compensation_id is not None:
            _check_id(self.compensation_id, "CompensationId")
        for attr in ("input_type", "output_type"):
            _check_id(getattr(self, attr), attr.title())
        object.__setattr__(self, "predecessors", _tuple_strings(self.predecessors, "predecessors"))
        object.__setattr__(self, "effects", _tuple_strings(self.effects, "effects"))
        object.__setattr__(self, "capabilities", _tuple_strings(self.capabilities, "capabilities"))
        if self.retry_policy.max_retries and self.effects and not self.idempotency_key:
            raise FlowSchemaError("RetryableEffectRequiresIdempotencyKey")

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "compensation_id": self.compensation_id, "input_type": self.input_type,
                "output_type": self.output_type, "predecessors": list(self.predecessors), "effects": list(self.effects),
                "capabilities": list(self.capabilities), "retry_policy": self.retry_policy.to_dict(),
                "idempotency_key": self.idempotency_key}


@dataclass(frozen=True)
class FlowStep:
    step_id: str
    action: str = ""
    input_type: str = "Any"
    output_type: str = "Any"
    predecessors: tuple[str, ...] = ()
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout: int | None = None
    effects: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    idempotency_key: str | None = None
    compensation: Compensation | None = None
    parallel_group: str | None = None
    handler: str | None = None
    result_type: str | None = None

    def __post_init__(self) -> None:
        _check_id(self.step_id, "StepId")
        action = self.handler if self.handler is not None else self.action
        _check_id(action, "StepAction")
        object.__setattr__(self, "action", action)
        if self.result_type is not None:
            _check_id(self.result_type, "ResultType")
            object.__setattr__(self, "output_type", self.result_type)
        for attr in ("input_type", "output_type"):
            _check_id(getattr(self, attr), attr.title())
        object.__setattr__(self, "predecessors", _tuple_strings(self.predecessors, "predecessors"))
        object.__setattr__(self, "effects", _tuple_strings(self.effects, "effects"))
        object.__setattr__(self, "capabilities", _tuple_strings(self.capabilities, "capabilities"))
        if self.timeout is not None and (not isinstance(self.timeout, int) or self.timeout <= 0 or self.timeout > 2**31):
            raise FlowSchemaError("TimeoutOutOfBounds")
        if self.parallel_group is not None:
            _check_id(self.parallel_group, "ParallelGroup")
        if self.retry_policy.max_retries and self.effects and not self.idempotency_key:
            raise FlowSchemaError("RetryableEffectRequiresIdempotencyKey")

    def to_dict(self) -> dict[str, Any]:
        return {"step_id": self.step_id, "action": self.action, "input_type": self.input_type,
                "output_type": self.output_type, "predecessors": list(self.predecessors),
                "retry_policy": self.retry_policy.to_dict(), "timeout": self.timeout,
                "effects": list(self.effects), "capabilities": list(self.capabilities),
                "idempotency_key": self.idempotency_key,
                "compensation": self.compensation.to_dict() if self.compensation else None,
                "parallel_group": self.parallel_group}


@dataclass(frozen=True)
class FlowDefinition:
    flow_id: str
    steps: tuple[FlowStep, ...]
    input_type: str = "Any"
    result_type: str = "Any"
    effects: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    version: int = SCHEMA_VERSION
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        _check_id(self.flow_id, "FlowId")
        if self.version != SCHEMA_VERSION:
            raise FlowSchemaError("UnsupportedFlowSchemaVersion")
        steps = tuple(self.steps)
        if not steps:
            raise FlowSchemaError("FlowMustHaveSteps")
        if any(not isinstance(step, FlowStep) for step in steps):
            raise FlowSchemaError("InvalidFlowStep")
        # Materialize derived compensation IDs at the definition boundary so
        # they participate in canonical serialization and are replay-stable.
        steps = tuple(
            replace(step, compensation=replace(step.compensation, compensation_id=f"{step.step_id}:compensate"))
            if step.compensation is not None and step.compensation.compensation_id is None else step
            for step in steps
        )
        object.__setattr__(self, "steps", steps)
        ids = tuple(step.step_id for step in steps)
        if len(set(ids)) != len(ids):
            raise FlowSchemaError("DuplicateStepId")
        for attr in ("input_type", "result_type"):
            _check_id(getattr(self, attr), attr.title())
        object.__setattr__(self, "effects", _tuple_strings(self.effects, "effects"))
        object.__setattr__(self, "capabilities", _tuple_strings(self.capabilities, "capabilities"))
        declared_effects, declared_caps = set(self.effects), set(self.capabilities)
        for step in steps:
            if not set(step.effects) <= declared_effects:
                raise FlowSchemaError("UndeclaredStepEffect")
            if not set(step.capabilities) <= declared_caps:
                raise FlowSchemaError("UndeclaredStepCapability")
        by_id = {step.step_id: step for step in steps}
        for step in steps:
            if not step.predecessors and self.input_type != "Any" and step.input_type != self.input_type:
                raise FlowSchemaError("RootInputTypeMismatch")
            for predecessor in step.predecessors:
                if predecessor not in by_id:
                    raise FlowSchemaError("UnknownPredecessor")
                if by_id[predecessor].output_type != step.input_type:
                    raise FlowSchemaError("PredecessorResultTypeMismatch")
        _topological(ids, by_id)
        comps: dict[str, Compensation] = {}
        for step in steps:
            comp = step.compensation
            if comp is not None:
                cid = comp.compensation_id or f"{step.step_id}:compensate"
                if cid in comps:
                    raise FlowSchemaError("DuplicateCompensationId")
                comps[cid] = comp
        for comp in comps.values():
            for predecessor in comp.predecessors:
                if predecessor not in comps:
                    raise FlowSchemaError("UnknownCompensationPredecessor")
                if comps[predecessor].output_type != comp.input_type:
                    raise FlowSchemaError("CompensationTypeMismatch")
        _topological(tuple(comps), comps)
        object.__setattr__(self, "digest", digest_payload({"schema": self.version, "flow": self.flow_id,
                                                            "input_type": self.input_type, "result_type": self.result_type,
                                                            "effects": self.effects, "capabilities": self.capabilities,
                                                            "steps": [step.to_dict() for step in steps]}))

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "flow_id": self.flow_id, "input_type": self.input_type,
                "result_type": self.result_type, "effects": list(self.effects), "capabilities": list(self.capabilities),
                "steps": [step.to_dict() for step in self.steps], "digest": self.digest}


def _topological(ids: Sequence[str], by_id: Mapping[str, Any]) -> tuple[str, ...]:
    state: dict[str, int] = {}
    result: list[str] = []

    def visit(node: str) -> None:
        mark = state.get(node, 0)
        if mark == 1:
            raise FlowSchemaError("CyclicFlowGraph")
        if mark == 2:
            return
        state[node] = 1
        for predecessor in by_id[node].predecessors:
            visit(predecessor)
        state[node] = 2
        result.append(node)

    for node in sorted(ids):
        visit(node)
    return tuple(result)


@dataclass(frozen=True)
class Checkpoint:
    flow_id: str
    run_id: str
    flow_digest: str
    sequence: int
    phase: str
    step_id: str | None
    attempt: int
    status: str
    logical_time: int
    input_digest: str | None = None
    output: Any = None
    error: str | None = None
    previous_digest: str = ""
    version: int = SCHEMA_VERSION
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        _check_id(self.flow_id, "FlowId")
        _check_id(self.run_id, "RunId")
        if self.version != SCHEMA_VERSION or self.sequence < 0 or self.attempt < 0 or not isinstance(self.logical_time, int):
            raise FlowCheckpointError("InvalidCheckpointMetadata")
        if self.phase not in {"step", "compensation", "run"} or self.status not in {"before", "completed", "failed", "timeout", "terminal"}:
            raise FlowCheckpointError("InvalidCheckpointStatus")
        if self.step_id is not None:
            _check_id(self.step_id, "StepId")
        output = _immutable(self.output)
        object.__setattr__(self, "output", output)
        body = self._body()
        object.__setattr__(self, "digest", digest_payload(body))

    def _body(self) -> dict[str, Any]:
        return {"version": self.version, "flow_id": self.flow_id, "run_id": self.run_id,
                "flow_digest": self.flow_digest, "sequence": self.sequence, "phase": self.phase,
                "step_id": self.step_id, "attempt": self.attempt, "status": self.status,
                "logical_time": self.logical_time, "input_digest": self.input_digest, "output": self.output,
                "error": self.error, "previous_digest": self.previous_digest}

    def to_dict(self) -> dict[str, Any]:
        result = self._body()
        result["output"] = _plain(self.output)
        result["digest"] = self.digest
        return result

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Checkpoint":
        data = dict(payload)
        supplied = data.pop("digest", None)
        checkpoint = cls(**data)
        if supplied != checkpoint.digest:
            raise FlowCheckpointError("CheckpointDigestMismatch")
        return checkpoint


class CheckpointStore(Protocol):
    def load(self, run_id: str) -> Sequence[Checkpoint]: ...
    def append(self, checkpoint: Checkpoint) -> None: ...


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._records: dict[str, list[Checkpoint]] = {}

    def load(self, run_id: str) -> tuple[Checkpoint, ...]:
        return tuple(self._records.get(run_id, ()))

    def append(self, checkpoint: Checkpoint) -> None:
        records = self._records.setdefault(checkpoint.run_id, [])
        if records:
            prior = records[-1]
            if checkpoint.sequence != prior.sequence + 1 or checkpoint.previous_digest != prior.digest:
                raise FlowCheckpointError("CheckpointChainMismatch")
        elif checkpoint.sequence != 0 or checkpoint.previous_digest:
            raise FlowCheckpointError("CheckpointChainMismatch")
        records.append(checkpoint)

    # Friendly aliases for stores used in tests and host adapters.
    def get(self, run_id: str) -> tuple[Checkpoint, ...]:
        return self.load(run_id)


@dataclass(frozen=True)
class Event:
    event_id: str
    run_id: str
    kind: str
    logical_time: int
    step_id: str | None = None
    attempt: int = 0
    payload: Any = None

    def __post_init__(self) -> None:
        _check_id(self.event_id, "EventId")
        _check_id(self.run_id, "RunId")
        _check_id(self.kind, "EventKind")
        if not isinstance(self.logical_time, int) or self.attempt < 0:
            raise FlowSchemaError("InvalidEventMetadata")
        object.__setattr__(self, "payload", _immutable(self.payload))

    def to_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "run_id": self.run_id, "kind": self.kind,
                "logical_time": self.logical_time, "step_id": self.step_id, "attempt": self.attempt,
                "payload": _plain(self.payload)}


@dataclass(frozen=True)
class RunResult:
    run_id: str
    flow_id: str
    status: str
    outputs: Any = None
    events: tuple[Event, ...] = ()
    checkpoints: tuple[Checkpoint, ...] = ()
    error: str | None = None
    digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed", "timeout", "replayed"}:
            raise FlowSchemaError("InvalidRunStatus")
        _check_id(self.run_id, "RunId")
        _check_id(self.flow_id, "FlowId")
        object.__setattr__(self, "outputs", _immutable(self.outputs))
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "checkpoints", tuple(self.checkpoints))
        object.__setattr__(self, "digest", digest_payload({"run_id": self.run_id, "flow_id": self.flow_id,
                                                              "status": self.status, "outputs": self.outputs,
                                                              "events": [e.to_dict() for e in self.events],
                                                              "checkpoints": [c.digest for c in self.checkpoints],
                                                              "error": self.error}))

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "flow_id": self.flow_id, "status": self.status,
                "outputs": _plain(self.outputs), "events": [e.to_dict() for e in self.events],
                "checkpoints": [c.to_dict() for c in self.checkpoints], "error": self.error, "digest": self.digest}


class LogicalClock:
    def __init__(self, start: int = 0) -> None:
        if not isinstance(start, int):
            raise TypeError("logical clock requires an integer")
        self._time = start

    def now(self) -> int:
        return self._time

    def advance(self, amount: int) -> None:
        if amount < 0:
            raise ValueError("clock cannot move backwards")
        self._time += amount


class BoundedExecutor:
    """A bounded injected executor whose results are consumed in caller order."""
    def __init__(self, max_workers: int = 1) -> None:
        if not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("ExecutorWorkerLimitOutOfBounds")
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any]:
        return self._executor.submit(fn, *args, **kwargs)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
    def __enter__(self) -> "BoundedExecutor":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.shutdown()


class FlowRuntime:
    def __init__(self, *, store: CheckpointStore | None = None, clock: Any | None = None,
                 executor: Any | None = None, capabilities: Sequence[str] = ()) -> None:
        self.store = store if store is not None else InMemoryCheckpointStore()
        self.clock = clock if clock is not None else LogicalClock()
        self.executor = executor
        self.capabilities = frozenset(capabilities)

    def run(self, flow: FlowDefinition, run_id: str, initial_input: Any = None,
            handlers: Mapping[str, Callable[..., Any]] | None = None, *, replay: bool = False) -> RunResult:
        if not isinstance(flow, FlowDefinition):
            raise FlowSchemaError("InvalidFlowDefinition")
        _check_id(run_id, "RunId")
        records = _verify_chain(self.store.load(run_id), flow, run_id)
        if replay:
            return self._replay(flow, run_id, records)
        handlers = handlers or {}
        completed = {c.step_id: c.output for c in records if c.phase == "step" and c.status == "completed" and c.step_id}
        events: list[Event] = []
        sequence = records[-1].sequence + 1 if records else 0
        previous = records[-1].digest if records else ""
        by_id = {step.step_id: step for step in flow.steps}
        outputs = dict(completed)
        if "__input__" not in outputs:
            outputs["__input__"] = initial_input
        failed: tuple[FlowStep, str, str] | None = None

        def append(phase: str, step_id: str | None, attempt: int, status: str, input_value: Any = None,
                   output: Any = None, error: str | None = None) -> Checkpoint:
            nonlocal sequence, previous
            cp = Checkpoint(flow.flow_id, run_id, flow.digest, sequence, phase, step_id, attempt, status,
                            self.clock.now(), digest_payload(input_value) if input_value is not None else None,
                            output, error, previous)
            self.store.append(cp)
            sequence += 1
            previous = cp.digest
            return cp

        remaining = set(by_id) - set(completed)
        try:
            while remaining:
                ready = sorted(step_id for step_id in remaining if all(pred in outputs for pred in by_id[step_id].predecessors))
                if not ready:
                    raise FlowExecutionError("NoExecutableStep")
                results = self._run_ready([by_id[sid] for sid in ready], outputs, handlers, append, events, flow, run_id)
                for step, outcome, error, status in results:
                    if status != "succeeded":
                        failed = (step, error or status, status)
                        break
                    outputs[step.step_id] = outcome
                    remaining.remove(step.step_id)
                if failed:
                    break
        except CrashInjected:
            raise
        except FlowCheckpointError:
            raise
        except Exception as exc:
            failed = (by_id[sorted(remaining)[0]], str(exc), "failed") if remaining else None

        if failed:
            step, error, status = failed
            comp_events, comp_cps = self._compensate(flow, run_id, outputs, handlers, append, events)
            checkpoints = tuple(self.store.load(run_id))
            public_outputs = {key: value for key, value in outputs.items() if key != "__input__"}
            return RunResult(run_id, flow.flow_id, "timeout" if status == "timeout" else "failed", public_outputs,
                             tuple(events), checkpoints, error)
        checkpoints = tuple(self.store.load(run_id))
        public_outputs = {key: value for key, value in outputs.items() if key != "__input__"}
        terminal = [sid for sid in by_id if sid in public_outputs and not any(sid in other.predecessors for other in by_id.values())]
        final = public_outputs[terminal[0]] if len(terminal) == 1 else (
            tuple(public_outputs[sid] for sid in sorted(terminal)) if terminal else None
        )
        if flow.result_type != "Any" and final is not None:
            self._check_type(final, flow.result_type, "FlowResult")
        return RunResult(run_id, flow.flow_id, "succeeded", final, tuple(events), checkpoints)

    def _run_ready(self, steps: list[FlowStep], outputs: dict[str, Any], handlers: Mapping[str, Callable[..., Any]],
                   append: Callable[..., Checkpoint], events: list[Event], flow: FlowDefinition, run_id: str) -> list[tuple[FlowStep, Any, str | None, str]]:
        def one(step: FlowStep) -> tuple[FlowStep, Any, str | None, str]:
            value = outputs.get(step.predecessors[0]) if len(step.predecessors) == 1 else tuple(outputs[p] for p in step.predecessors)
            if not step.predecessors:
                value = outputs.get("__input__")
            if not step.predecessors and "__input__" not in outputs:
                value = None
            self._check_type(value, step.input_type, "StepInput")
            if not set(step.capabilities) <= self.capabilities:
                return step, None, "MissingCapability", "failed"
            attempts = step.retry_policy.attempts
            for attempt in range(1, attempts + 1):
                append("step", step.step_id, attempt, "before", value)
                events.append(Event(f"{run_id}:{step.step_id}:{attempt}:before", run_id, "step_before", self.clock.now(), step.step_id, attempt))
                start = self.clock.now()
                try:
                    handler = handlers.get(step.action, handlers.get(step.step_id))
                    if handler is None:
                        raise FlowExecutionError("MissingHandler")
                    outcome = _invoke(
                        handler,
                        value,
                        StepContext(
                            run_id,
                            step.step_id,
                            attempt,
                            start,
                            step.idempotency_key,
                        ),
                    )
                    elapsed = self.clock.now() - start
                    if step.timeout is not None and elapsed > step.timeout:
                        append("step", step.step_id, attempt, "timeout", value, error="Timeout")
                        events.append(Event(f"{run_id}:{step.step_id}:{attempt}:timeout", run_id, "step_timeout", self.clock.now(), step.step_id, attempt))
                        return step, None, "Timeout", "timeout"
                    self._check_type(outcome, step.output_type, "StepOutput")
                    append("step", step.step_id, attempt, "completed", value, outcome)
                    events.append(Event(f"{run_id}:{step.step_id}:{attempt}:completed", run_id, "step_completed", self.clock.now(), step.step_id, attempt))
                    return step, outcome, None, "succeeded"
                except CrashInjected:
                    raise
                except Exception as exc:
                    message = type(exc).__name__ + ":" + str(exc)
                    append("step", step.step_id, attempt, "failed", value, error=message)
                    events.append(Event(f"{run_id}:{step.step_id}:{attempt}:failed", run_id, "step_failed", self.clock.now(), step.step_id, attempt, str(exc)))
                    if attempt >= attempts:
                        return step, None, str(exc) or type(exc).__name__, "failed"
                    delay = step.retry_policy.delay_for(attempt)
                    if hasattr(self.clock, "advance"):
                        self.clock.advance(delay)
            raise AssertionError("unreachable")

        if self.executor is None or len(steps) == 1 or any(step.retry_policy.max_retries for step in steps):
            return [one(step) for step in steps]
        # Handler calls may run in parallel, while all durable writes happen in
        # source-ID order.  This is the important distinction between bounded
        # concurrency and nondeterministic checkpointing.
        prepared: list[tuple[FlowStep, Any, int]] = []
        for step in steps:
            value = outputs.get(step.predecessors[0]) if len(step.predecessors) == 1 else tuple(outputs[p] for p in step.predecessors)
            if not step.predecessors:
                value = outputs.get("__input__")
            self._check_type(value, step.input_type, "StepInput")
            if not set(step.capabilities) <= self.capabilities:
                return [one(item) for item in steps]
            start = self.clock.now()
            append("step", step.step_id, 1, "before", value)
            events.append(Event(f"{run_id}:{step.step_id}:1:before", run_id, "step_before", start, step.step_id, 1))
            prepared.append((step, value, start))
        futures = {
            step.step_id: self.executor.submit(
                _invoke,
                handlers.get(
                    step.action,
                    handlers.get(
                        step.step_id,
                        _missing_handler,
                    ),
                ),
                value,
                StepContext(
                    run_id,
                    step.step_id,
                    1,
                    start,
                    step.idempotency_key,
                ),
            )
            for step, value, start in prepared
        }
        result: list[tuple[FlowStep, Any, str | None, str]] = []
        for step, value, start in prepared:
            try:
                outcome = futures[step.step_id].result()
                elapsed = self.clock.now() - start
                if step.timeout is not None and elapsed > step.timeout:
                    append("step", step.step_id, 1, "timeout", value, error="Timeout")
                    result.append((step, None, "Timeout", "timeout"))
                else:
                    self._check_type(outcome, step.output_type, "StepOutput")
                    append("step", step.step_id, 1, "completed", value, outcome)
                    events.append(Event(f"{run_id}:{step.step_id}:1:completed", run_id, "step_completed", self.clock.now(), step.step_id, 1))
                    result.append((step, outcome, None, "succeeded"))
            except CrashInjected:
                raise
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                append("step", step.step_id, 1, "failed", value, error=type(exc).__name__ + ":" + message)
                result.append((step, None, message, "failed"))
        return result

    def _compensate(self, flow: FlowDefinition, run_id: str, outputs: dict[str, Any], handlers: Mapping[str, Callable[..., Any]],
                    append: Callable[..., Checkpoint], events: list[Event]) -> tuple[list[Event], list[Checkpoint]]:
        all_completed = {step.step_id: step for step in flow.steps if step.step_id in outputs}
        completed = {sid: step for sid, step in all_completed.items() if step.compensation}
        order = [sid for sid in reversed(_topological(tuple(all_completed), all_completed)) if sid in completed] if completed else []
        for sid in order:
            step = completed[sid]
            comp = step.compensation
            assert comp is not None
            value = outputs[sid]
            cid = comp.compensation_id or f"{sid}:compensate"
            if comp.action not in handlers:
                append("compensation", cid, 1, "failed", value, error="MissingHandler")
                continue
            append("compensation", cid, 1, "before", value)
            try:
                result = _invoke(
                    handlers[comp.action],
                    value,
                    StepContext(
                        run_id,
                        cid,
                        1,
                        self.clock.now(),
                        comp.idempotency_key,
                    ),
                )
                self._check_type(result, comp.output_type, "CompensationOutput")
                append("compensation", cid, 1, "completed", value, result)
                events.append(Event(f"{run_id}:{cid}:completed", run_id, "compensation_completed", self.clock.now(), cid, 1))
            except CrashInjected:
                raise
            except Exception as exc:
                append("compensation", cid, 1, "failed", value, error=str(exc))
        return events, list(self.store.load(run_id))

    def _replay(self, flow: FlowDefinition, run_id: str, records: Sequence[Checkpoint]) -> RunResult:
        outputs = {c.step_id: c.output for c in records if c.phase == "step" and c.status == "completed" and c.step_id}
        failed = next((c for c in reversed(records) if c.phase == "step" and c.status in {"failed", "timeout"}), None)
        status = "timeout" if failed and failed.status == "timeout" else "failed" if failed else "replayed"
        terminals = sorted(sid for sid in outputs if not any(sid in step.predecessors for step in flow.steps))
        final = outputs[terminals[0]] if len(terminals) == 1 else tuple(outputs[sid] for sid in terminals)
        return RunResult(run_id, flow.flow_id, status, final, (), tuple(records), failed.error if failed else None)

    @staticmethod
    def _check_type(value: Any, expected: str, label: str) -> None:
        if expected == "Any":
            return
        actual = {str: "Text", int: "UInt64", bool: "Bool", float: "Float64", bytes: "Bytes", type(None): "Unit"}.get(type(value), type(value).__name__)
        if expected != actual:
            raise FlowSchemaError(f"{label}TypeMismatch:{expected}:{actual}")


@dataclass(frozen=True)
class StepContext:
    run_id: str
    step_id: str
    attempt: int
    logical_time: int
    idempotency_key: str | None = None


def _invoke(handler: Callable[..., Any], value: Any, context: StepContext) -> Any:
    try:
        signature = inspect.signature(handler)
        positional = [p for p in signature.parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if len(positional) >= 2:
            return handler(value, context)
    except (TypeError, ValueError):
        pass
    return handler(value)

def _missing_handler(value: Any) -> Any:
    raise FlowExecutionError("MissingHandler")


def _verify_chain(records: Sequence[Checkpoint], flow: FlowDefinition, run_id: str) -> tuple[Checkpoint, ...]:
    result = tuple(records)
    previous = ""
    for index, checkpoint in enumerate(result):
        if not isinstance(checkpoint, Checkpoint):
            raise FlowCheckpointError("InvalidCheckpointRecord")
        if checkpoint.run_id != run_id or checkpoint.flow_id != flow.flow_id or checkpoint.flow_digest != flow.digest:
            raise FlowCheckpointError("CheckpointFlowMismatch")
        if checkpoint.sequence != index or checkpoint.previous_digest != previous:
            raise FlowCheckpointError("CheckpointChainMismatch")
        if checkpoint.digest != digest_payload(checkpoint._body()):
            raise FlowCheckpointError("CheckpointDigestMismatch")
        previous = checkpoint.digest
    return result


def execute_flow(flow: FlowDefinition, run_id: str, initial_input: Any = None, handlers: Mapping[str, Callable[..., Any]] | None = None,
                 *, store: CheckpointStore | None = None, clock: Any | None = None, executor: Any | None = None,
                 capabilities: Sequence[str] = (), replay: bool = False) -> RunResult:
    runtime = FlowRuntime(store=store, clock=clock, executor=executor, capabilities=capabilities)
    # Roots use the input as a normal execution value without persisting it as a fake step.
    if initial_input is not None and flow.input_type != "Any":
        FlowRuntime._check_type(initial_input, flow.input_type, "FlowInput")
    return runtime.run(flow, run_id, initial_input, handlers, replay=replay)


def replay_flow(flow: FlowDefinition, run_id: str, *, store: CheckpointStore, clock: Any | None = None) -> RunResult:
    return FlowRuntime(store=store, clock=clock).run(flow, run_id, handlers={}, replay=True)


# JSON is intentionally derived from ``to_dict`` so every public schema has
# one canonical representation and no alternate serializer can drift.
def _to_json(self: Any) -> str:
    return _canonical(self.to_dict())


for _schema in (RetryPolicy, Compensation, FlowStep, FlowDefinition, Checkpoint, Event, RunResult):
    setattr(_schema, "to_json", _to_json)


MemoryCheckpointStore = InMemoryCheckpointStore
RunResult.result = property(lambda self: self.outputs)
RunResult.success = property(lambda self: self.status in {"succeeded", "replayed"})
FlowDefinition.content_digest = property(lambda self: self.digest)
Checkpoint.chain_digest = property(lambda self: self.digest)

__all__ = ["SCHEMA_VERSION", "FlowSchemaError", "FlowCheckpointError", "FlowExecutionError", "CrashInjected",
           "RetryPolicy", "Compensation", "FlowStep", "FlowDefinition", "CheckpointStore", "InMemoryCheckpointStore",
           "MemoryCheckpointStore", "Checkpoint", "Event", "RunResult", "LogicalClock", "BoundedExecutor", "StepContext",
           "FlowRuntime", "execute_flow", "replay_flow", "digest_payload"]
