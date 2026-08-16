"""Deterministic, bounded execution of immutable dependency graphs.

The runtime deliberately executes ready work in stable waves.  A wave contains
at most ``worker_count`` nodes, so the executor never queues an unbounded
number of futures.  Results and trace events are committed in input order,
independent of the order in which worker threads happen to finish.
"""

from __future__ import annotations

import heapq
import hashlib
import inspect
import json
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType
from typing import Any, TypeVar


SCHEMA_VERSION = 1
CONTRACT = "merlo.parallel-runtime.v1"
_TRACE_CONTRACT = "merlo.parallel-runtime.trace.v1"
_RESULT_CONTRACT = "merlo.parallel-runtime.result.v1"
MAX_WORKERS = 64

T = TypeVar("T")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _json_value(value: Any) -> Any:
    """Return a JSON value, rejecting values without a deterministic schema."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("ResultValueNotCanonical")
        return value
    if isinstance(value, Mapping):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("ResultValueNotCanonical")
            converted[key] = _json_value(item)
        return converted
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    raise ValueError("ResultValueNotCanonical")


def _freeze_value(value: Any) -> Any:
    canonical = _json_value(value)
    if isinstance(canonical, dict):
        return MappingProxyType({key: _freeze_value(item) for key, item in canonical.items()})
    if isinstance(canonical, list):
        return tuple(_freeze_value(item) for item in canonical)
    return canonical


def _require_keys(payload: Mapping[str, Any], expected: set[str], code: str) -> None:
    if set(payload) != expected:
        raise ValueError(code)


@dataclass(frozen=True, slots=True)
class DAGNode:
    """One immutable unit of work in a :class:`DependencyDAG`.

    ``action`` normally takes no arguments.  An action accepting one
    positional argument receives a read-only mapping of dependency id to
    dependency result.  This keeps simple tasks terse while allowing a DAG to
    pass values along its edges.
    """

    node_id: str
    dependencies: tuple[str, ...] = ()
    action: Callable[..., Any] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id:
            raise ValueError("InvalidNodeId")
        if self.action is not None and not callable(self.action):
            raise ValueError("InvalidNodeAction")
        try:
            dependencies = tuple(self.dependencies)
        except TypeError as exc:
            raise ValueError("InvalidNodeDependencies") from exc
        if any(not isinstance(item, str) or not item for item in dependencies):
            raise ValueError("InvalidNodeDependency")
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("DuplicateNodeDependency")
        object.__setattr__(self, "dependencies", tuple(sorted(dependencies)))



@dataclass(frozen=True, slots=True)
class DependencyDAG:
    """A validated DAG whose tuple order is the canonical input node order."""

    nodes: tuple[DAGNode, ...]

    def __post_init__(self) -> None:
        try:
            nodes = tuple(self.nodes)
        except TypeError as exc:
            raise ValueError("InvalidDAGNodes") from exc
        if any(not isinstance(node, DAGNode) for node in nodes):
            raise ValueError("InvalidDAGNode")
        ids = tuple(node.node_id for node in nodes)
        if len(set(ids)) != len(ids):
            raise ValueError("DuplicateNodeId")
        known = set(ids)
        for node in nodes:
            if any(dep not in known for dep in node.dependencies):
                raise ValueError("UnknownDependency")
        # Kahn's algorithm validates cycles without changing input order.
        position = {node_id: index for index, node_id in enumerate(ids)}
        indegree = {node.node_id: len(node.dependencies) for node in nodes}
        children: dict[str, list[str]] = {node_id: [] for node_id in ids}
        for node in nodes:
            for dependency in node.dependencies:
                children[dependency].append(node.node_id)
        ready = [(position[node_id], node_id) for node_id in ids if indegree[node_id] == 0]
        heapq.heapify(ready)
        visited = 0
        while ready:
            _, current = heapq.heappop(ready)
            visited += 1
            for child in children[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    heapq.heappush(ready, (position[child], child))
        if visited != len(nodes):
            raise ValueError("DependencyCycle")
        object.__setattr__(self, "nodes", nodes)

    @classmethod
    def from_nodes(cls, nodes: Iterable[DAGNode]) -> "DependencyDAG":
        return cls(tuple(nodes))

    @property
    def node_order(self) -> tuple[str, ...]:
        return tuple(node.node_id for node in self.nodes)


@dataclass(frozen=True, slots=True)
class NodeResult:
    node_id: str
    status: str
    value: Any = None
    error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id:
            raise ValueError("InvalidResultNodeId")
        if self.status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("InvalidResultStatus")
        if self.status == "succeeded" and self.error is not None:
            raise ValueError("SuccessfulResultHasError")
        if self.status != "succeeded" and self.value is not None:
            raise ValueError("UnsuccessfulResultHasValue")
        if self.status == "failed" and not self.error:
            raise ValueError("FailedResultMissingError")
        if self.status == "cancelled" and self.error is not None:
            raise ValueError("CancelledResultHasError")
        if self.status == "succeeded":
            object.__setattr__(self, "value", _freeze_value(self.value))


    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.error,
            "node_id": self.node_id,
            "status": self.status,
            "value": _json_value(self.value),
        }


@dataclass(frozen=True, slots=True)
class ExecutionEvent:
    sequence: int
    node_id: str
    kind: str

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("InvalidEventSequence")
        if not isinstance(self.node_id, str) or not self.node_id:
            raise ValueError("InvalidEventNodeId")
        if self.kind not in {"queued", "started", "succeeded", "failed", "cancelled"}:
            raise ValueError("InvalidEventKind")


    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "node_id": self.node_id, "sequence": self.sequence}


@dataclass(frozen=True, slots=True)
class ExecutionTrace:
    events: tuple[ExecutionEvent, ...]
    schema_version: int = SCHEMA_VERSION
    contract: str = _TRACE_CONTRACT

    def __post_init__(self) -> None:
        events = tuple(self.events)
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("UnsupportedTraceSchema")
        if self.contract != _TRACE_CONTRACT:
            raise ValueError("TraceContractMismatch")
        if tuple(event.sequence for event in events) != tuple(range(len(events))):
            raise ValueError("NonCanonicalEventSequence")
        if any(not isinstance(event, ExecutionEvent) for event in events):
            raise ValueError("InvalidExecutionEvent")
        object.__setattr__(self, "events", events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "events": [event.to_dict() for event in self.events],
            "schema_version": self.schema_version,
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExecutionTrace":
        if not isinstance(payload, Mapping):
            raise ValueError("InvalidTracePayload")
        _require_keys(payload, {"contract", "events", "schema_version"}, "TraceSchemaMismatch")
        if payload["contract"] != _TRACE_CONTRACT or payload["schema_version"] != SCHEMA_VERSION:
            raise ValueError("UnsupportedTraceSchema")
        if not isinstance(payload["events"], list):
            raise ValueError("InvalidTraceEvents")
        events: list[ExecutionEvent] = []
        for item in payload["events"]:
            if not isinstance(item, Mapping):
                raise ValueError("InvalidTraceEvent")
            _require_keys(item, {"kind", "node_id", "sequence"}, "TraceEventSchemaMismatch")
            events.append(ExecutionEvent(item["sequence"], item["node_id"], item["kind"]))
        return cls(tuple(events))

    @classmethod
    def from_json(cls, value: str) -> "ExecutionTrace":
        try:
            payload = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("InvalidTraceJSON") from exc
        return cls.from_dict(payload)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    results: tuple[NodeResult, ...]
    trace: ExecutionTrace
    requested_workers: int
    schema_version: int = SCHEMA_VERSION
    contract: str = _RESULT_CONTRACT

    def __post_init__(self) -> None:
        results = tuple(self.results)
        if type(self.requested_workers) is not int or self.requested_workers < 1:
            raise ValueError("InvalidWorkerCount")
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise ValueError("UnsupportedResultSchema")
        if self.contract != _RESULT_CONTRACT:
            raise ValueError("ResultContractMismatch")
        if not isinstance(self.trace, ExecutionTrace):
            raise ValueError("InvalidExecutionTrace")
        ids = tuple(result.node_id for result in results)
        if len(set(ids)) != len(ids):
            raise ValueError("DuplicateResultNodeId")
        if any(not isinstance(result, NodeResult) for result in results):
            raise ValueError("InvalidNodeResult")
        object.__setattr__(self, "results", results)


    @property
    def succeeded(self) -> bool:
        return all(item.status == "succeeded" for item in self.results)

    @property
    def values(self) -> tuple[Any, ...]:
        return tuple(item.value for item in self.results if item.status == "succeeded")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "requested_workers": self.requested_workers,
            "results": [result.to_dict() for result in self.results],
            "schema_version": self.schema_version,
            "trace": self.trace.to_dict(),
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExecutionResult":
        if not isinstance(payload, Mapping):
            raise ValueError("InvalidResultPayload")
        _require_keys(
            payload,
            {"contract", "requested_workers", "results", "schema_version", "trace"},
            "ResultSchemaMismatch",
        )
        if payload["contract"] != _RESULT_CONTRACT or payload["schema_version"] != SCHEMA_VERSION:
            raise ValueError("UnsupportedResultSchema")
        if not isinstance(payload["results"], list):
            raise ValueError("InvalidResultItems")
        results: list[NodeResult] = []
        for item in payload["results"]:
            if not isinstance(item, Mapping):
                raise ValueError("InvalidNodeResult")
            _require_keys(item, {"error", "node_id", "status", "value"}, "ResultItemSchemaMismatch")
            results.append(NodeResult(item["node_id"], item["status"], item["value"], item["error"]))
        return cls(
            tuple(results),
            ExecutionTrace.from_dict(payload["trace"]),
            payload["requested_workers"],
        )

    @classmethod
    def from_json(cls, value: str) -> "ExecutionResult":
        try:
            payload = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("InvalidResultJSON") from exc
        return cls.from_dict(payload)




def _call_action(
    action: Callable[..., Any],
    dependency_values: Mapping[str, Any],
) -> Any:
    """Call a task with either no arguments or its read-only dependency map."""
    try:
        signature = inspect.signature(action)
    except (TypeError, ValueError):
        return action()
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(
        parameter.kind == parameter.VAR_POSITIONAL
        for parameter in signature.parameters.values()
    )
    if positional or has_varargs:
        return action(MappingProxyType(dict(dependency_values)))
    return action()


def _coerce_dag(dag: DependencyDAG | Iterable[DAGNode] | Mapping[str, Any]) -> DependencyDAG:
    if isinstance(dag, DependencyDAG):
        return dag
    if isinstance(dag, Mapping):
        nodes: list[DAGNode] = []
        for node_id, item in dag.items():
            if isinstance(item, DAGNode):
                if item.node_id != node_id:
                    raise ValueError("DAGMappingIdMismatch")
                nodes.append(item)
            elif callable(item):
                nodes.append(DAGNode(node_id, (), item))
            else:
                raise ValueError("InvalidDAGMappingValue")
        return DependencyDAG(tuple(nodes))
    try:
        return DependencyDAG(tuple(dag))
    except TypeError as exc:
        raise ValueError("InvalidDAG") from exc


class ParallelRuntime:
    """Execute a validated DAG with a deterministic bounded worker pool."""

    __slots__ = ("requested_workers",)

    def __init__(self, worker_count: int = 1, *, max_workers: int | None = None) -> None:
        if max_workers is not None:
            if worker_count != 1:
                raise ValueError("ConflictingWorkerCount")
            worker_count = max_workers
        if (
            type(worker_count) is not int
            or not 1 <= worker_count <= MAX_WORKERS
        ):
            raise ValueError("InvalidWorkerCount")
        self.requested_workers = worker_count


    def execute(
        self,
        dag: DependencyDAG | Iterable[DAGNode] | Mapping[str, Any],
        operation: Callable[[DAGNode, Mapping[str, Any]], Any] | None = None,
    ) -> ExecutionResult:
        graph = _coerce_dag(dag)
        if operation is not None and not callable(operation):
            raise ValueError("InvalidOperation")
        nodes = graph.nodes
        state = {node.node_id: "pending" for node in nodes}
        values: dict[str, Any] = {}
        outcomes: dict[str, NodeResult] = {}
        events: list[ExecutionEvent] = []
        position = {node.node_id: index for index, node in enumerate(nodes)}
        by_id = {node.node_id: node for node in nodes}
        remaining = {node.node_id: len(node.dependencies) for node in nodes}
        children: dict[str, list[str]] = {node.node_id: [] for node in nodes}
        for node in nodes:
            for dependency in node.dependencies:
                children[dependency].append(node.node_id)
        ready = [(position[node.node_id], node.node_id) for node in nodes if not node.dependencies]
        heapq.heapify(ready)

        def emit(node_id: str, kind: str) -> None:
            events.append(ExecutionEvent(len(events), node_id, kind))

        def cancel_dependents(failed: list[str]) -> None:
            pending = list(failed)
            blocked: set[str] = set()
            while pending:
                parent = pending.pop()
                for child in children[parent]:
                    if child not in blocked and state[child] == "pending":
                        blocked.add(child)
                        pending.append(child)
            for node in nodes:
                if node.node_id in blocked:
                    state[node.node_id] = "cancelled"
                    outcomes[node.node_id] = NodeResult(node.node_id, "cancelled")
                    emit(node.node_id, "cancelled")
        def run_one(node: DAGNode) -> tuple[str, Any, Exception | None]:
            try:
                dependencies = {
                    dependency: values[dependency]
                    for dependency in node.dependencies
                }
                if operation is not None:
                    return (
                        node.node_id,
                        operation(
                            node,
                            MappingProxyType(
                                dependencies
                            ),
                        ),
                        None,
                    )
                if node.action is None:
                    raise ValueError("MissingNodeAction")
                return (
                    node.node_id,
                    _call_action(
                        node.action,
                        dependencies,
                    ),
                    None,
                )
            except Exception as exc:
                return node.node_id, None, exc

        while len(outcomes) < len(nodes):
            batch: list[DAGNode] = []
            while ready and len(batch) < self.requested_workers:
                _, node_id = heapq.heappop(ready)
                if state[node_id] == "pending":
                    batch.append(by_id[node_id])
            if not batch:
                if len(outcomes) != len(nodes):
                    raise ValueError("DAGExecutionStalled")
                break
            for node in batch:
                state[node.node_id] = "running"
                emit(node.node_id, "queued")
            for node in batch:
                emit(node.node_id, "started")
            if self.requested_workers == 1 or len(batch) == 1:
                completed = [run_one(batch[0])]
            else:
                with ThreadPoolExecutor(max_workers=self.requested_workers) as pool:
                    futures = [pool.submit(run_one, node) for node in batch]
                    completed = [future.result() for future in futures]
            failed: list[str] = []
            for node_id, value, error in completed:
                if error is None:
                    result = NodeResult(node_id, "succeeded", value)
                    state[node_id] = "succeeded"
                    values[node_id] = result.value
                    outcomes[node_id] = result
                    emit(node_id, "succeeded")
                    for child in children[node_id]:
                        if state[child] != "pending":
                            continue
                        remaining[child] -= 1
                        if remaining[child] == 0:
                            heapq.heappush(ready, (position[child], child))
                else:
                    state[node_id] = "failed"
                    error_text = f"{type(error).__name__}: {error}"
                    outcomes[node_id] = NodeResult(node_id, "failed", error=error_text)
                    emit(node_id, "failed")
                    failed.append(node_id)
            if failed:
                cancel_dependents(failed)

        ordered_results = tuple(outcomes[node.node_id] for node in nodes)
        return ExecutionResult(
            ordered_results,
            ExecutionTrace(tuple(events)),
            self.requested_workers,
        )


def execute_dag(
    dag: DependencyDAG | Iterable[DAGNode] | Mapping[str, Any],
    worker_count: int = 1,
    *,
    max_workers: int | None = None,
    operation: Callable[[DAGNode, Mapping[str, Any]], Any] | None = None,
) -> ExecutionResult:
    """Execute ``dag`` with a fresh deterministic runtime."""
    return ParallelRuntime(worker_count, max_workers=max_workers).execute(dag, operation)




def deterministic_reduce(values: Iterable[T], combine: Callable[[T, T], T]) -> T:
    """Reduce values using a fixed pairwise balanced tree.

    At each level adjacent pairs are combined left-to-right; an odd final
    value is carried unchanged to the next level.  The shape is therefore
    independent of worker scheduling and never depends on a mutable queue.
    """
    if not callable(combine):
        raise ValueError("InvalidReductionCombine")
    items = list(values)
    if not items:
        raise ValueError("EmptyReduction")
    while len(items) > 1:
        next_level: list[T] = []
        for index in range(0, len(items) - 1, 2):
            next_level.append(combine(items[index], items[index + 1]))
        if len(items) % 2:
            next_level.append(items[-1])
        items = next_level
    return items[0]




__all__ = [
    "CONTRACT",
    "DAGNode",
    "DependencyDAG",
    "ExecutionEvent",
    "ExecutionResult",
    "ExecutionTrace",
    "MAX_WORKERS",
    "NodeResult",
    "ParallelRuntime",
    "SCHEMA_VERSION",
    "deterministic_reduce",
    "execute_dag",
]
