"""Bounded multicore execution over an immutable dependency DAG."""

from __future__ import annotations

import hashlib
import inspect
import json
import multiprocessing
import os
import heapq
from itertools import islice
import pickle
import time
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from merlo.parallel_runtime import DAGNode, DependencyDAG

WORK_STEALING_SCHEMA_VERSION = 1
WORK_STEALING_CONTRACT = "merlo.work-stealing.v1"
MAX_WORKERS = 64
MAX_TASKS = 10_000


def _error(code: str, detail: str = "") -> ValueError:
    return ValueError(code if not detail else f"{code}: {detail}")


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise _error("WorkStealingResultNotCanonical")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _error("WorkStealingResultNotCanonical")
        return {key: _json_value(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    raise _error("WorkStealingResultNotCanonical", type(value).__name__)


def _freeze_result(value: Any) -> Any:
    canonical = _json_value(value)
    if isinstance(canonical, dict):
        return MappingProxyType({key: _freeze_result(item) for key, item in canonical.items()})
    if isinstance(canonical, list):
        return tuple(_freeze_result(item) for item in canonical)
    return canonical


def _thaw_result(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_result(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_result(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(_json_value(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _invoke(action: Any, dependencies: Mapping[str, Any]) -> Any:
    try:
        signature = inspect.signature(action)
    except (TypeError, ValueError):
        return action()
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(parameter.kind == parameter.VAR_POSITIONAL for parameter in signature.parameters.values())
    if positional or has_varargs:
        return action(MappingProxyType(dict(dependencies)))
    return action()


def _run_action(action: Any, dependencies: dict[str, Any]) -> tuple[int, bool, Any, str | None, str | None]:
    """Child-process boundary. Return failures as data so exception pickling is irrelevant."""

    try:
        value = _invoke(action, dependencies)
        canonical = _json_value(value)
        pickle.dumps(canonical, protocol=5)
        return os.getpid(), True, canonical, None, None
    except Exception as exc:
        return os.getpid(), False, None, type(exc).__name__, str(exc)


def _coerce_dag(value: DependencyDAG | Iterable[DAGNode]) -> DependencyDAG:
    if isinstance(value, DependencyDAG):
        return value
    try:
        items = tuple(islice(iter(value), MAX_TASKS + 1))
    except TypeError as exc:
        raise _error("WorkStealingInvalidDAG") from exc
    if len(items) > MAX_TASKS:
        raise _error("WorkStealingTaskLimit")
    return DependencyDAG(items)


def _topological_nodes(dag: DependencyDAG) -> tuple[DAGNode, ...]:
    positions = {node.node_id: index for index, node in enumerate(dag.nodes)}
    by_id = {node.node_id: node for node in dag.nodes}
    indegree = {node.node_id: len(node.dependencies) for node in dag.nodes}
    children: dict[str, list[str]] = {node.node_id: [] for node in dag.nodes}
    for node in dag.nodes:
        for dependency in node.dependencies:
            children[dependency].append(node.node_id)
    ready = [(positions[node_id], node_id) for node_id, count in indegree.items() if count == 0]
    heapq.heapify(ready)
    ordered: list[DAGNode] = []
    while ready:
        _, node_id = heapq.heappop(ready)
        ordered.append(by_id[node_id])
        for child in sorted(children[node_id], key=positions.__getitem__):
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, (positions[child], child))
    if len(ordered) != len(dag.nodes):
        raise _error("WorkStealingDependencyCycle")
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class WorkRecord:
    node_id: str
    status: str
    result: Any = None
    worker_pid: int | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id:
            raise _error("WorkStealingInvalidNodeId")
        if self.status not in {"succeeded", "failed", "cancelled"}:
            raise _error("WorkStealingInvalidStatus")
        if self.status == "succeeded":
            object.__setattr__(self, "result", _freeze_result(self.result))
            if type(self.worker_pid) is not int or self.worker_pid < 1:
                raise _error("WorkStealingInvalidWorkerPid")
            if self.error_code is not None or self.error_message is not None:
                raise _error("WorkStealingUnexpectedError")
        elif self.status == "failed":
            if self.result is not None:
                raise _error("WorkStealingUnexpectedResult")
            if type(self.worker_pid) is not int or self.worker_pid < 1:
                raise _error("WorkStealingInvalidWorkerPid")
            if not isinstance(self.error_code, str) or not self.error_code:
                raise _error("WorkStealingMissingError")
            if not isinstance(self.error_message, str):
                raise _error("WorkStealingMissingError")
        else:
            if (
                self.worker_pid is not None
                or self.result is not None
                or self.error_code is not None
                or self.error_message is not None
            ):
                raise _error("WorkStealingInvalidCancellation")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "status": self.status,
            "result": _thaw_result(self.result),
            "error_code": self.error_code,
            "error_message": self.error_message,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.semantic_dict(), "worker_pid": self.worker_pid}


@dataclass(frozen=True, slots=True)
class WorkStealingResult:
    records: tuple[WorkRecord, ...]
    requested_workers: int
    worker_pids: tuple[int, ...]
    schema_version: int = WORK_STEALING_SCHEMA_VERSION
    contract: str = WORK_STEALING_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        records = tuple(self.records)
        if not records or any(not isinstance(item, WorkRecord) for item in records):
            raise _error("WorkStealingInvalidRecords")
        if len({item.node_id for item in records}) != len(records):
            raise _error("WorkStealingDuplicateRecord")
        if type(self.requested_workers) is not int or not 1 <= self.requested_workers <= MAX_WORKERS:
            raise _error("WorkStealingInvalidWorkerCount")
        pids = tuple(sorted(set(self.worker_pids)))
        if any(type(pid) is not int or pid < 1 for pid in pids):
            raise _error("WorkStealingInvalidWorkerPid")
        payload = {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "requested_workers": self.requested_workers,
            "records": [item.semantic_dict() for item in records],
        }
        if self.schema_version != WORK_STEALING_SCHEMA_VERSION or self.contract != WORK_STEALING_CONTRACT:
            raise _error("WorkStealingContractMismatch")
        expected = _digest(payload)
        if self.digest and self.digest != expected:
            raise _error("WorkStealingDigestMismatch")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "worker_pids", pids)
        object.__setattr__(self, "digest", expected)

    @property
    def succeeded(self) -> bool:
        return all(item.status == "succeeded" for item in self.records)

    @property
    def trace(self) -> tuple[tuple[str, str], ...]:
        return tuple((item.node_id, item.status) for item in self.records)

    @property
    def results(self) -> Mapping[str, Any]:
        return MappingProxyType({item.node_id: item.result for item in self.records if item.status == "succeeded"})

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "requested_workers": self.requested_workers,
            "worker_pids": list(self.worker_pids),
            "records": [item.to_dict() for item in self.records],
            "digest": self.digest,
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


class WorkStealingExecutor:
    """Process-based ready-queue executor whose idle workers claim available work."""

    def __init__(
        self,
        worker_count: int | None = None,
        *,
        start_method: str = "spawn",
        execution_timeout: float = 60.0,
    ) -> None:
        workers = worker_count if worker_count is not None else min(MAX_WORKERS, os.cpu_count() or 1)
        if type(workers) is not int or not 1 <= workers <= MAX_WORKERS:
            raise _error("WorkStealingInvalidWorkerCount")
        if start_method not in {"spawn", "forkserver"}:
            raise _error("WorkStealingInvalidStartMethod")
        if isinstance(execution_timeout, bool) or not isinstance(execution_timeout, (int, float)) or not 0 < execution_timeout <= 86_400:
            raise _error("WorkStealingInvalidTimeout")
        try:
            context = multiprocessing.get_context(start_method)
        except ValueError as exc:
            raise _error("WorkStealingStartMethodUnavailable", start_method) from exc
        self.worker_count = workers
        self.execution_timeout = float(execution_timeout)
        self._context = context

    @staticmethod
    def _terminate(pool: ProcessPoolExecutor) -> None:
        terminate_workers = getattr(pool, "terminate_workers", None)
        if callable(terminate_workers):
            terminate_workers()
            return
        processes = tuple((getattr(pool, "_processes", None) or {}).values())
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=1.0)

    def execute(self, dag: DependencyDAG | Iterable[DAGNode]) -> WorkStealingResult:
        graph = _coerce_dag(dag)
        if not graph.nodes:
            raise _error("WorkStealingEmptyDAG")
        if len(graph.nodes) > MAX_TASKS:
            raise _error("WorkStealingTaskLimit")
        nodes = _topological_nodes(graph)
        order = {node.node_id: index for index, node in enumerate(nodes)}
        for node in nodes:
            if node.action is None:
                raise _error("WorkStealingMissingNodeAction", node.node_id)
            try:
                pickle.dumps(node.action, protocol=5)
            except Exception as exc:
                raise _error("WorkStealingUnpicklableAction", node.node_id) from exc

        state = {node.node_id: "pending" for node in nodes}
        values: dict[str, Any] = {}
        records: dict[str, WorkRecord] = {}
        futures: dict[Future[tuple[int, bool, Any, str | None, str | None]], str] = {}
        worker_pids: set[int] = set()
        by_id = {node.node_id: node for node in nodes}
        children: dict[str, list[str]] = {node.node_id: [] for node in nodes}
        remaining = {node.node_id: len(node.dependencies) for node in nodes}
        ready = [(order[node.node_id], node.node_id) for node in nodes if not node.dependencies]
        heapq.heapify(ready)
        for node in nodes:
            for dependency in node.dependencies:
                children[dependency].append(node.node_id)

        def cancel_dependents(node_id: str) -> None:
            pending = list(reversed(children[node_id]))
            while pending:
                child_id = pending.pop()
                if state[child_id] != "pending":
                    continue
                state[child_id] = "cancelled"
                records[child_id] = WorkRecord(child_id, "cancelled")
                pending.extend(reversed(children[child_id]))

        pool = ProcessPoolExecutor(max_workers=self.worker_count, mp_context=self._context)
        failed_pool = False
        deadline = time.monotonic() + self.execution_timeout
        try:
            while len(records) < len(nodes):
                capacity = self.worker_count - len(futures)
                while capacity > 0 and ready:
                    _, node_id = heapq.heappop(ready)
                    node = by_id[node_id]
                    dependency_values = {dependency: values[dependency] for dependency in node.dependencies}
                    future = pool.submit(_run_action, node.action, dependency_values)
                    futures[future] = node_id
                    state[node_id] = "running"
                    capacity -= 1
                if not futures:
                    if len(records) != len(nodes):
                        raise _error("WorkStealingExecutionStalled")
                    break
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    raise _error("WorkStealingTimeout")
                done, _ = wait(tuple(futures), timeout=timeout, return_when=FIRST_COMPLETED)
                if not done:
                    raise _error("WorkStealingTimeout")
                completed = sorted(done, key=lambda item: order[futures[item]])
                for future in completed:
                    node_id = futures.pop(future)
                    try:
                        pid, success, result, error_code, error_message = future.result()
                    except BrokenProcessPool as exc:
                        raise _error("WorkStealingWorkerCrash", node_id) from exc
                    except Exception as exc:
                        raise _error("WorkStealingWorkerFailure", node_id) from exc
                    worker_pids.add(pid)
                    if success:
                        state[node_id] = "succeeded"
                        record = WorkRecord(node_id, "succeeded", result, pid)
                        values[node_id] = record.result
                        records[node_id] = record
                        for child_id in children[node_id]:
                            if state[child_id] != "pending":
                                continue
                            remaining[child_id] -= 1
                            if remaining[child_id] == 0:
                                heapq.heappush(ready, (order[child_id], child_id))
                    else:
                        state[node_id] = "failed"
                        records[node_id] = WorkRecord(node_id, "failed", None, pid, error_code, error_message)
                        cancel_dependents(node_id)
        except BaseException:
            failed_pool = True
            self._terminate(pool)
            raise
        finally:
            pool.shutdown(wait=not failed_pool, cancel_futures=True)

        ordered_records = tuple(records[node.node_id] for node in nodes)
        return WorkStealingResult(ordered_records, self.worker_count, tuple(worker_pids))

def execute_work_stealing(
    dag: DependencyDAG | Iterable[DAGNode],
    *,
    worker_count: int | None = None,
    start_method: str = "spawn",
    execution_timeout: float = 60.0,
) -> WorkStealingResult:
    return WorkStealingExecutor(
        worker_count,
        start_method=start_method,
        execution_timeout=execution_timeout,
    ).execute(dag)


__all__ = [
    "MAX_TASKS",
    "MAX_WORKERS",
    "WORK_STEALING_CONTRACT",
    "WORK_STEALING_SCHEMA_VERSION",
    "WorkRecord",
    "WorkStealingExecutor",
    "WorkStealingResult",
    "execute_work_stealing",
]
