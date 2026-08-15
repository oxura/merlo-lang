from dataclasses import FrozenInstanceError
import json

import pytest

from merlo.machine_runtime import (
    MachineDefinition,
    MachineEvent,
    MachineExecution,
    MachineInvariant,
    MachineRuntime,
    MachineRuntimeError,
    MachineSnapshot,
    MachineState,
    MachineTransition,
)


def definition(*, invariant=None, action=False):
    transitions = [
        MachineTransition(
            "finish",
            "idle",
            "finish",
            "done",
            "mark" if action else None,
            ("clock",) if action else (),
            ("state",) if action else (),
        )
    ]
    return MachineDefinition(
        states=(MachineState("done", invariants=("ok",) if invariant else ()), MachineState("idle")),
        transitions=tuple(transitions),
        initial_state="idle",
        events=("finish",),
        capabilities=("clock",) if action else (),
        effects=("state",) if action else (),
        invariants=(MachineInvariant("ok", predicate=invariant),) if invariant else (),
    )


def test_definition_rejects_duplicate_and_non_exhaustive_contracts():
    with pytest.raises(MachineRuntimeError, match="MachineDuplicateState"):
        MachineDefinition(("idle", "idle"), (), "idle")
    with pytest.raises(MachineRuntimeError, match="MachineNonExhaustiveTransitions"):
        MachineDefinition(("done", "idle"), (), "idle", events=("finish",))


def test_legal_state_change_and_immutable_roundtrip():
    runtime = MachineRuntime(definition())
    initial = runtime.initial_snapshot({"n": 1})
    result = runtime.execute(initial, MachineEvent("finish", {"input": [1, 2]}))
    assert result.snapshot.state == "done"
    assert result.snapshot.sequence == 1
    assert initial.state == "idle" and initial.data == {"n": 1}
    assert MachineDefinition.from_dict(runtime.definition.to_dict()).digest == runtime.definition.digest
    with pytest.raises(FrozenInstanceError):
        result.snapshot.state = "idle"


def test_illegal_event_rejected():
    runtime = MachineRuntime(definition())
    with pytest.raises(MachineRuntimeError, match="MachineIllegalEvent"):
        runtime.execute(runtime.initial_snapshot(), MachineEvent("unknown"))


def test_invariant_failure_does_not_change_previous_snapshot():
    predicate = lambda snapshot: snapshot.data.get("allowed", False)
    runtime = MachineRuntime(
        definition(invariant=predicate), invariant_bindings={"ok": predicate}
    )
    initial = runtime.initial_snapshot({"allowed": False})
    with pytest.raises(MachineRuntimeError, match="MachineInvariantFailed"):
        runtime.execute(initial, MachineEvent("finish"))
    assert initial.state == "idle"
    assert initial.sequence == 0


def test_exact_capability_and_action_bindings():
    machine = definition(action=True)
    with pytest.raises(MachineRuntimeError, match="MachineCapabilityBindingMismatch"):
        MachineRuntime(machine, capability_bindings={})
    seen = []
    runtime = MachineRuntime(
        machine,
        capability_bindings={"clock": object()},
        action_handlers={"mark": lambda snapshot, event, capabilities: seen.append((event.id, "clock" in capabilities))},
    )
    runtime.execute(runtime.initial_snapshot(), MachineEvent("finish"))
    assert seen == [("finish", True)]


def test_history_replay_and_tamper_rejection():
    runtime = MachineRuntime(definition())
    initial = runtime.initial_snapshot()
    execution = runtime.execute(initial, MachineEvent("finish"))
    assert runtime.replay(initial, [execution]) == execution.snapshot
    tampered = MachineExecution(execution.snapshot, type(execution.record)(execution.record.sequence, MachineEvent("finish", {"tampered": True}), execution.record.transition_id, execution.record.source, execution.record.target, execution.record.previous_digest, execution.record.digest))
    with pytest.raises(MachineRuntimeError, match="Machine"):
        runtime.replay(initial, [tampered])


def test_schema_and_genesis_binding_are_strict():
    runtime = MachineRuntime(definition())
    payload = runtime.definition.to_dict()
    del payload["schema"]
    with pytest.raises(
        MachineRuntimeError,
        match="MachineSchemaMismatch",
    ):
        MachineDefinition.from_dict(payload)

    event = MachineEvent("finish").to_dict()
    event["extra"] = True
    with pytest.raises(
        MachineRuntimeError,
        match="MachineEventSchemaMismatch",
    ):
        MachineEvent.from_dict(event)

    unbound = MachineSnapshot("idle")
    with pytest.raises(
        MachineRuntimeError,
        match="MachineInvalidHistory",
    ):
        runtime.execute(
            unbound,
            MachineEvent("finish"),
        )

    noncanonical = json.dumps(
        {
            **runtime.definition.to_dict(),
            "extra": True,
        }
    )
    with pytest.raises(
        MachineRuntimeError,
        match="MachineSchemaMismatch",
    ):
        MachineDefinition.from_json(noncanonical)


def test_typed_state_payloads_are_checked_atomically():
    machine = MachineDefinition(
        states=(
            MachineState(
                "idle",
                fields=(("count", "UInt64"),),
            ),
            MachineState(
                "done",
                fields=(("result", "Text"),),
            ),
        ),
        transitions=(
            MachineTransition(
                "finish",
                "idle",
                "finish",
                "done",
                "finish_action",
            ),
        ),
        initial_state="idle",
        events=("finish",),
    )
    with pytest.raises(
        MachineRuntimeError,
        match="StateFieldTypeMismatch",
    ):
        MachineRuntime(
            machine,
            action_handlers={
                "finish_action": lambda: {
                    "result": "ok"
                },
            },
        ).initial_snapshot({"count": "wrong"})

    initial = MachineRuntime(
        machine,
        action_handlers={
            "finish_action": lambda: {
                "result": "ok"
            },
        },
    ).initial_snapshot({"count": 1})
    broken = MachineRuntime(
        machine,
        action_handlers={
            "finish_action": lambda: {
                "result": 1
            },
        },
    )
    with pytest.raises(
        MachineRuntimeError,
        match="StateFieldTypeMismatch",
    ):
        broken.execute(
            initial,
            MachineEvent("finish"),
        )
    assert initial.state == "idle"
    assert initial.data == {"count": 1}
