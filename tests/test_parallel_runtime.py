from __future__ import annotations

import threading
import time
import pytest

from merlo.parallel_runtime import (
    DAGNode,
    DependencyDAG,
    ExecutionResult,
    ParallelRuntime,
    deterministic_reduce,
)


def _graph() -> DependencyDAG:
    return DependencyDAG(
        (
            DAGNode("left", action=lambda: 2),
            DAGNode("right", action=lambda: 3),
            DAGNode(
                "sum",
                ("left", "right"),
                lambda values: values["left"]
                + values["right"],
            ),
        )
    )


def test_single_and_multi_worker_results_are_deterministic() -> None:
    single = ParallelRuntime(1).execute(_graph())
    repeated = [
        ParallelRuntime(3).execute(_graph())
        for _ in range(5)
    ]

    assert single.succeeded is True
    assert [item.value for item in single.results] == [
        2,
        3,
        5,
    ]
    assert all(
        item.to_json() == repeated[0].to_json()
        for item in repeated
    )
    assert ExecutionResult.from_json(
        repeated[0].to_json()
    ) == repeated[0]


def test_failure_cancels_only_dependent_work() -> None:
    graph = DependencyDAG(
        (
            DAGNode(
                "failed",
                action=lambda: (_ for _ in ()).throw(
                    ValueError("boom")
                ),
            ),
            DAGNode(
                "dependent",
                ("failed",),
                lambda: "never",
            ),
            DAGNode(
                "independent",
                action=lambda: "ok",
            ),
        )
    )
    result = ParallelRuntime(2).execute(graph)

    assert [item.status for item in result.results] == [
        "failed",
        "cancelled",
        "succeeded",
    ]
    assert result.results[0].error == "ValueError: boom"
    assert [
        (event.node_id, event.kind)
        for event in result.trace.events
        if event.kind in {"failed", "cancelled"}
    ] == [
        ("failed", "failed"),
        ("dependent", "cancelled"),
    ]


def test_worker_count_is_bounded() -> None:
    lock = threading.Lock()
    active = 0
    maximum = 0

    def work() -> int:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return 1

    graph = DependencyDAG(
        tuple(
            DAGNode(str(index), action=work)
            for index in range(8)
        )
    )
    result = ParallelRuntime(2).execute(graph)
    assert result.succeeded is True
    assert maximum == 2


def test_results_are_deeply_immutable_and_large_chain_is_linear() -> None:
    immutable = ParallelRuntime(1).execute(
        DependencyDAG((DAGNode("value", action=lambda: {"items": [1, 2]}),))
    )
    value = immutable.results[0].value
    with pytest.raises(TypeError):
        value["items"] = ()
    with pytest.raises(AttributeError):
        value["items"].append(3)
    assert immutable.results[0].to_dict()["value"] == {"items": [1, 2]}

    nodes = [DAGNode("node-0", action=lambda: 0)]
    nodes.extend(
        DAGNode(
            f"node-{index}",
            (f"node-{index - 1}",),
            lambda values, previous=f"node-{index - 1}": values[previous] + 1,
        )
        for index in range(1, 2_000)
    )
    result = ParallelRuntime(1).execute(DependencyDAG(tuple(nodes)))
    assert result.results[-1].value == 1_999


def test_balanced_reduction_has_fixed_tree() -> None:
    calls: list[tuple[str, str]] = []

    def combine(left: str, right: str) -> str:
        calls.append((left, right))
        return f"({left}+{right})"

    result = deterministic_reduce(
        ("a", "b", "c", "d", "e"),
        combine,
    )
    assert result == "(((a+b)+(c+d))+e)"
    assert calls == [
        ("a", "b"),
        ("c", "d"),
        ("(a+b)", "(c+d)"),
        ("((a+b)+(c+d))", "e"),
    ]
