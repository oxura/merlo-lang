from __future__ import annotations

import os
import time

import pytest

from merlo.parallel_runtime import DAGNode, DependencyDAG
from merlo.work_stealing import (
    MAX_TASKS,
    WorkRecord,
    WorkStealingExecutor,
    execute_work_stealing,
)


def _pid() -> int:
    time.sleep(0.05)
    return os.getpid()


def _seed() -> int:
    return 7


def _double(values: dict[str, int]) -> int:
    return values["seed"] * 2


def _fail() -> int:
    raise RuntimeError("broken")


def _nested() -> dict[str, object]:
    return {"items": [1, 2]}


def _slow() -> int:
    time.sleep(1)
    return 1


def _graph() -> DependencyDAG:
    return DependencyDAG(
        (
            DAGNode("seed", action=_seed),
            DAGNode("double", ("seed",), _double),
        )
    )


def test_process_workers_execute_dependencies_and_stable_trace() -> None:
    first = execute_work_stealing(_graph(), worker_count=2)
    second = execute_work_stealing(_graph(), worker_count=2)

    assert first.succeeded
    assert first.results == {"seed": 7, "double": 14}
    assert all(pid != os.getpid() for pid in first.worker_pids)
    assert first.trace == (("seed", "succeeded"), ("double", "succeeded"))
    assert second.trace == first.trace
    assert second.digest == first.digest


def test_idle_workers_claim_shared_ready_work() -> None:
    graph = DependencyDAG(tuple(DAGNode(f"job-{index}", action=_pid) for index in range(6)))
    result = WorkStealingExecutor(2).execute(graph)

    assert result.succeeded
    assert len(result.worker_pids) == 2
    assert set(result.results.values()) == set(result.worker_pids)


def test_failure_cancels_dependents_without_cancelling_independent_work() -> None:
    graph = DependencyDAG(
        (
            DAGNode("fail", action=_fail),
            DAGNode("blocked", ("fail",), _seed),
            DAGNode("independent", action=_seed),
        )
    )
    result = execute_work_stealing(graph, worker_count=2)

    assert result.trace == (
        ("fail", "failed"),
        ("blocked", "cancelled"),
        ("independent", "succeeded"),
    )
    assert result.records[0].error_code == "RuntimeError"
    assert result.results == {"independent": 7}


def test_rejects_unpicklable_work_and_invalid_bounds() -> None:
    graph = DependencyDAG((DAGNode("closure", action=lambda: 1),))
    with pytest.raises(ValueError, match="WorkStealingUnpicklableAction"):
        execute_work_stealing(graph, worker_count=1)
    with pytest.raises(ValueError, match="WorkStealingInvalidWorkerCount"):
        WorkStealingExecutor(0)
    with pytest.raises(ValueError, match="WorkStealingInvalidStartMethod"):
        WorkStealingExecutor(1, start_method="fork")
    with pytest.raises(ValueError, match="DependencyCycle"):
        DependencyDAG((DAGNode("a", ("b",), _seed), DAGNode("b", ("a",), _seed)))


def test_results_are_immutable_inputs_are_bounded_and_timeouts_terminate() -> None:
    result = execute_work_stealing(
        DependencyDAG((DAGNode("nested", action=_nested),)),
        worker_count=1,
    )
    value = result.records[0].result
    with pytest.raises(TypeError):
        value["items"] = ()
    with pytest.raises(AttributeError):
        value["items"].append(3)
    assert result.records[0].semantic_dict()["result"] == {"items": [1, 2]}

    with pytest.raises(ValueError, match="WorkStealingUnexpectedResult"):
        WorkRecord("bad", "failed", result=1, worker_pid=1, error_code="X", error_message="x")
    with pytest.raises(ValueError, match="WorkStealingInvalidCancellation"):
        WorkRecord("bad", "cancelled", error_code="X")

    oversized = (DAGNode(str(index), action=_seed) for index in range(MAX_TASKS + 1))
    with pytest.raises(ValueError, match="WorkStealingTaskLimit"):
        execute_work_stealing(oversized, worker_count=1)

    started = time.monotonic()
    with pytest.raises(ValueError, match="WorkStealingTimeout"):
        execute_work_stealing(
            DependencyDAG((DAGNode("slow", action=_slow),)),
            worker_count=1,
            execution_timeout=0.05,
        )
    assert time.monotonic() - started < 0.8
