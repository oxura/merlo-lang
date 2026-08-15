from __future__ import annotations

import pytest

from merlo.flow_runtime import (
    BoundedExecutor,
    Compensation,
    CrashInjected,
    FlowCheckpointError,
    FlowDefinition,
    FlowSchemaError,
    FlowStep,
    InMemoryCheckpointStore,
    LogicalClock,
    RetryPolicy,
    execute_flow,
    replay_flow,
)


def test_resume_after_injected_crash_does_not_repeat_completed_step():
    class CrashStore(InMemoryCheckpointStore):
        crashed = False

        def append(self, checkpoint):
            super().append(checkpoint)
            if checkpoint.status == "completed" and not self.crashed:
                self.crashed = True
                raise CrashInjected()

    flow = FlowDefinition(
        "resume",
        (
            FlowStep("a", "a", output_type="UInt64"),
            FlowStep("b", "b", input_type="UInt64", output_type="UInt64", predecessors=("a",)),
        ),
    )
    calls = []
    handlers = {"a": lambda value: (calls.append("a"), 2)[1], "b": lambda value: (calls.append("b"), value + 1)[1]}
    store = CrashStore()
    with pytest.raises(CrashInjected):
        execute_flow(flow, "r", 1, handlers, store=store)
    result = execute_flow(flow, "r", 1, handlers, store=store)
    assert result.status == "succeeded"
    assert result.outputs == 3
    assert calls == ["a", "b"]


def test_retry_is_bounded_and_uses_idempotency_for_effects():
    flow = FlowDefinition(
        "retry",
        (FlowStep("send", "send", output_type="Text", effects=("write",), idempotency_key="send:v1",
                   retry_policy=RetryPolicy(max_retries=2, backoff=4)),),
        effects=("write",),
    )
    attempts = []
    clock = LogicalClock()

    def send(value, context):
        attempts.append(
            (
                clock.now(),
                context.idempotency_key,
            )
        )
        if len(attempts) < 3:
            raise RuntimeError("temporary")
        return "ok"

    result = execute_flow(flow, "r", "input", {"send": send}, clock=clock)
    assert result.status == "succeeded"
    assert result.outputs == "ok"
    assert attempts == [
        (0, "send:v1"),
        (4, "send:v1"),
        (12, "send:v1"),
    ]


def test_retry_exhaustion_and_timeout_are_stable_results():
    flow = FlowDefinition("fail", (FlowStep("x", "x", output_type="Text", retry_policy=RetryPolicy(max_retries=1)),))
    clock = LogicalClock()
    result = execute_flow(flow, "r", None, {"x": lambda value: (_ for _ in ()).throw(ValueError("bad"))}, clock=clock)
    assert result.status == "failed"
    assert [cp.status for cp in result.checkpoints if cp.phase == "step"] == ["before", "failed", "before", "failed"]

    timeout_flow = FlowDefinition("timeout", (FlowStep("x", "x", output_type="Text", timeout=2),))

    def slow(value, context):
        clock.advance(3)
        return "late"

    timed = execute_flow(timeout_flow, "t", None, {"x": slow}, clock=clock)
    assert timed.status == "timeout"
    assert timed.error == "Timeout"


def test_reverse_dependency_compensation_order():
    undo = []
    flow = FlowDefinition(
        "comp",
        (
            FlowStep("a", "a", output_type="Text", compensation=Compensation("undo_a")),
            FlowStep("b", "b", input_type="Text", output_type="Text", predecessors=("a",), compensation=Compensation("undo_b")),
            FlowStep("c", "c", input_type="Text", output_type="Text", predecessors=("b",)),
        ),
    )
    handlers = {"a": lambda value: "a", "b": lambda value: "b", "c": lambda value: (_ for _ in ()).throw(RuntimeError("stop")),
                "undo_a": lambda value: undo.append("a"), "undo_b": lambda value: undo.append("b")}
    result = execute_flow(flow, "r", None, handlers)
    assert result.status == "failed"
    assert undo == ["b", "a"]


def test_parallel_branches_have_stable_result_and_checkpoint_order():
    flow = FlowDefinition(
        "parallel",
        (FlowStep("left", "left", input_type="Text", output_type="UInt64", parallel_group="g"),
         FlowStep("right", "right", input_type="Text", output_type="UInt64", parallel_group="g")),
        input_type="Text",
    )
    with BoundedExecutor(2) as executor:
        result = execute_flow(flow, "r", "x", {"left": lambda value: 1, "right": lambda value: 2}, executor=executor)
    assert result.status == "succeeded"
    assert result.outputs == (1, 2)
    durable = [(cp.step_id, cp.status) for cp in result.checkpoints]
    assert durable == [("left", "before"), ("right", "before"), ("left", "completed"), ("right", "completed")]


def test_replay_consumes_recorded_outcomes_without_handlers():
    flow = FlowDefinition("replay", (FlowStep("x", "x", output_type="UInt64"),))
    store = InMemoryCheckpointStore()
    first = execute_flow(flow, "r", None, {"x": lambda value: 7}, store=store)
    called = []
    replay = replay_flow(flow, "r", store=store)
    assert first.outputs == replay.outputs == 7
    assert replay.status == "replayed"
    assert not called


def test_schema_cycle_types_and_retry_effect_rejected():
    with pytest.raises(FlowSchemaError, match="CyclicFlowGraph"):
        FlowDefinition("cycle", (FlowStep("a", "a", predecessors=("b",)), FlowStep("b", "b", predecessors=("a",))))
    with pytest.raises(FlowSchemaError, match="PredecessorResultTypeMismatch"):
        FlowDefinition("types", (FlowStep("a", "a", output_type="Text"), FlowStep("b", "b", input_type="UInt64", predecessors=("a",))))
    with pytest.raises(FlowSchemaError, match="RetryableEffectRequiresIdempotencyKey"):
        FlowStep("x", "x", effects=("write",), retry_policy=RetryPolicy(max_retries=1))
    with pytest.raises(FlowSchemaError, match="UndeclaredStepCapability"):
        FlowDefinition("caps", (FlowStep("x", "x", capabilities=("network",)),))


def test_checkpoint_tamper_rejected():
    flow = FlowDefinition("tamper", (FlowStep("x", "x", output_type="UInt64"),))
    store = InMemoryCheckpointStore()
    execute_flow(flow, "r", None, {"x": lambda value: 1}, store=store)
    object.__setattr__(store._records["r"][0], "output", "tampered")
    with pytest.raises(FlowCheckpointError, match="CheckpointDigestMismatch"):
        execute_flow(flow, "r", None, {}, store=store)
