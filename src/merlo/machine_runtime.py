"""Deterministic, immutable runtime semantics for source-declared machines.

The module deliberately has no process, filesystem, clock, or global registry
state.  A :class:`MachineRuntime` is constructed with the complete set of
host bindings needed to execute a definition and can therefore be replayed in
another process from the same definition and history.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Callable, Iterable


_SCHEMA = "merlo.machine.v1"
_ZERO = "0" * 64


class MachineRuntimeError(ValueError):
    """Stable, machine-readable rejection from machine validation/execution."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


def _error(code: str, detail: str = "") -> None:
    raise MachineRuntimeError(code, detail)


class FrozenDict(Mapping[str, Any]):
    """Small immutable, hashable mapping used by canonical payloads."""

    __slots__ = ("_items", "_dict", "_hash")

    def __init__(self, value: Mapping[str, Any] | Iterable[tuple[str, Any]] = ()) -> None:
        items = tuple(sorted(dict(value).items(), key=lambda item: item[0]))
        self._items = tuple((str(k), v) for k, v in items)
        self._dict = dict(self._items)
        self._hash = hash(self._items)

    def __getitem__(self, key: str) -> Any:
        return self._dict[key]

    def __iter__(self):
        return iter(self._dict)

    def __len__(self) -> int:
        return len(self._dict)

    def __hash__(self) -> int:
        return self._hash

    def __repr__(self) -> str:
        return f"FrozenDict({self._dict!r})"


def _freeze(value: Any, *, path: str = "value") -> Any:
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _error("MachineNonFiniteValue", path)
        return value
    if isinstance(value, Mapping):
        keys = list(value)
        if any(type(key) is not str for key in keys):
            _error("MachineNonStringKey", path)
        return FrozenDict((key, _freeze(value[key], path=f"{path}.{key}")) for key in sorted(keys))
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item, path=f"{path}[{index}]") for index, item in enumerate(value))
    _error("MachineNonCanonicalValue", path)


def _thaw(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _json(value: Any) -> str:
    return json.dumps(_thaw(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(value: Any, *, name: str) -> str:
    if type(value) is not str or not value:
        _error("MachineInvalidIdentifier", name)
    return value


def _names(values: Iterable[str], *, kind: str) -> tuple[str, ...]:
    result = tuple(_text(item, name=kind) for item in values)
    if len(set(result)) != len(result):
        _error(f"MachineDuplicate{kind.title()}")
    return tuple(sorted(result))


def _exact_mapping(
    value: Any,
    fields: set[str],
    code: str,
) -> Mapping[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != fields
    ):
        _error(code)
    return value


@dataclass(frozen=True)
class MachineInvariant:
    id: str
    states: tuple[str, ...] = ()
    predicate: Callable[["MachineSnapshot"], bool] | None = None

    def __post_init__(self) -> None:
        _text(self.id, name="invariant")
        states = tuple(self.states)
        if len(set(states)) != len(states):
            _error(
                "MachineDuplicateInvariantState",
                self.id,
            )
        if (
            self.predicate is not None
            and not callable(self.predicate)
        ):
            _error(
                "MachineInvariantNotCallable",
                self.id,
            )
        object.__setattr__(
            self,
            "states",
            tuple(sorted(states)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "states": list(self.states),
        }


@dataclass(frozen=True)
class MachineState:
    id: str
    fields: tuple[tuple[str, str], ...] = ()
    invariants: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.id, name="state")
        fields = tuple(
            tuple(item)
            for item in self.fields
        )
        if (
            any(
                len(item) != 2
                or any(
                    type(value) is not str
                    or not value
                    for value in item
                )
                for item in fields
            )
            or len(
                {
                    item[0]
                    for item in fields
                }
            )
            != len(fields)
        ):
            _error("MachineInvalidStateFields")
        object.__setattr__(self, "fields", fields)
        object.__setattr__(
            self,
            "invariants",
            _names(
                self.invariants,
                kind="invariant",
            ),
        )
        object.__setattr__(
            self,
            "capabilities",
            _names(
                self.capabilities,
                kind="capability",
            ),
        )
        object.__setattr__(
            self,
            "effects",
            _names(self.effects, kind="effect"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fields": [
                list(item)
                for item in self.fields
            ],
            "invariants": list(self.invariants),
            "capabilities": list(self.capabilities),
            "effects": list(self.effects),
        }

    @property
    def state_id(self) -> str:
        return self.id


@dataclass(frozen=True)
class MachineTransition:
    id: str
    source: str
    event: str
    target: str
    action_id: str | None = None
    capabilities: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    pre_invariants: tuple[str, ...] = ()
    post_invariants: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.id, name="transition")
        _text(self.source, name="transition source")
        _text(self.event, name="transition event")
        _text(self.target, name="transition target")
        if self.action_id is not None:
            _text(self.action_id, name="action")
        for name in ("capabilities", "effects", "pre_invariants", "post_invariants"):
            object.__setattr__(self, name, _names(getattr(self, name), kind=name.removesuffix("s")))

    @property
    def transition_id(self) -> str:
        return self.id
    @property
    def from_state(self) -> str:
        return self.source

    @property
    def to_state(self) -> str:
        return self.target

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "source": self.source, "event": self.event, "target": self.target, "action_id": self.action_id, "capabilities": list(self.capabilities), "effects": list(self.effects), "pre_invariants": list(self.pre_invariants), "post_invariants": list(self.post_invariants)}


@dataclass(frozen=True)
class MachineDefinition:
    states: tuple[MachineState | str, ...]
    transitions: tuple[MachineTransition, ...]
    initial_state: str
    events: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    invariants: tuple[MachineInvariant | str, ...] = ()

    def __post_init__(self) -> None:
        states = tuple(item if isinstance(item, MachineState) else MachineState(item) for item in self.states)
        state_ids = tuple(item.id for item in states)
        if len(set(state_ids)) != len(state_ids):
            _error("MachineDuplicateState")
        states = tuple(sorted(states, key=lambda item: item.id))
        state_ids = tuple(item.id for item in states)
        object.__setattr__(self, "states", states)
        transitions = tuple(self.transitions)
        if any(not isinstance(item, MachineTransition) for item in transitions):
            _error("MachineInvalidTransition")
        ids = tuple(item.id for item in transitions)
        if len(set(ids)) != len(ids):
            _error("MachineDuplicateTransition")
        transitions = tuple(sorted(transitions, key=lambda item: item.id))
        object.__setattr__(self, "transitions", transitions)
        if self.initial_state not in state_ids:
            _error("MachineInvalidInitialState")
        capabilities = _names(self.capabilities, kind="capability")
        effects = _names(self.effects, kind="effect")
        events = _names(self.events, kind="event")
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "effects", effects)
        object.__setattr__(self, "events", events)
        state_set = set(state_ids)
        raw_invariants = (
            tuple(self.invariants.items())
            if isinstance(self.invariants, Mapping)
            else tuple(self.invariants)
        )
        invariants: list[MachineInvariant] = []
        for item in raw_invariants:
            if isinstance(item, MachineInvariant):
                invariant = item
            elif isinstance(item, tuple) and len(item) == 2 and callable(item[1]):
                invariant = MachineInvariant(item[0], predicate=item[1])
            else:
                invariant = MachineInvariant(item)
            invariants.append(invariant)
        invariant_ids = tuple(item.id for item in invariants)
        if len(set(invariant_ids)) != len(invariant_ids):
            _error("MachineDuplicateInvariant")
        invariants.sort(key=lambda item: item.id)
        for invariant in invariants:
            if any(state not in state_set for state in invariant.states):
                _error("MachineInvariantUnknownState", invariant.id)
        object.__setattr__(self, "invariants", tuple(invariants))
        inv_set = set(invariant_ids)
        by_pair: set[tuple[str, str]] = set()
        for transition in transitions:
            if transition.source not in state_set or transition.target not in state_set:
                _error("MachineTransitionUnknownState", transition.id)
            pair = (transition.source, transition.event)
            if pair in by_pair:
                _error("MachineAmbiguousTransition", f"{transition.source}:{transition.event}")
            by_pair.add(pair)
            if transition.event and events and transition.event not in events:
                _error("MachineTransitionUnknownEvent", transition.id)
            if set(transition.capabilities) - set(capabilities):
                _error("MachineCapabilityOutOfBounds", transition.id)
            if set(transition.effects) - set(effects):
                _error("MachineEffectOutOfBounds", transition.id)
            if set(transition.pre_invariants + transition.post_invariants) - inv_set:
                _error("MachineTransitionUnknownInvariant", transition.id)
        for state in states:
            if set(state.capabilities) - set(capabilities):
                _error("MachineCapabilityOutOfBounds", state.id)
            if set(state.effects) - set(effects):
                _error("MachineEffectOutOfBounds", state.id)
            if set(state.invariants) - inv_set:
                _error("MachineStateUnknownInvariant", state.id)
        if events:
            declared = set(events)
            if {item.event for item in transitions} != declared:
                _error("MachineNonExhaustiveTransitions")
        # Every declared state must be reachable from the initial state.
        reachable = {self.initial_state}
        changed = True
        while changed:
            changed = False
            for transition in transitions:
                if transition.source in reachable and transition.target not in reachable:
                    reachable.add(transition.target)
                    changed = True
        if reachable != state_set:
            _error("MachineUnreachableState")

    @property
    def state_map(self) -> dict[str, MachineState]:
        return {item.id: item for item in self.states}

    @property
    def transition_map(self) -> dict[tuple[str, str], MachineTransition]:
        return {(item.source, item.event): item for item in self.transitions}

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())
    @property
    def initial_state_id(self) -> str:
        return self.initial_state

    def to_dict(self) -> dict[str, Any]:
        return {"schema": _SCHEMA, "states": [item.to_dict() for item in self.states], "transitions": [item.to_dict() for item in self.transitions], "initial_state": self.initial_state, "events": list(self.events), "capabilities": list(self.capabilities), "effects": list(self.effects), "invariants": [item.to_dict() for item in self.invariants]}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MachineDefinition":
        fields = {
            "schema",
            "states",
            "transitions",
            "initial_state",
            "events",
            "capabilities",
            "effects",
            "invariants",
        }
        value = _exact_mapping(
            payload,
            fields,
            "MachineSchemaMismatch",
        )
        if value["schema"] != _SCHEMA:
            _error("MachineSchemaMismatch")
        if not all(
            isinstance(value[name], list)
            for name in fields
            - {"schema", "initial_state"}
        ):
            _error("MachineSchemaMismatch")
        states = tuple(
            MachineState(
                **_exact_mapping(
                    item,
                    {
                        "id",
                        "fields",
                        "invariants",
                        "capabilities",
                        "effects",
                    },
                    "MachineStateSchemaMismatch",
                )
            )
            for item in value["states"]
        )
        transitions = tuple(
            MachineTransition(
                **_exact_mapping(
                    item,
                    {
                        "id",
                        "source",
                        "event",
                        "target",
                        "action_id",
                        "capabilities",
                        "effects",
                        "pre_invariants",
                        "post_invariants",
                    },
                    "MachineTransitionSchemaMismatch",
                )
            )
            for item in value["transitions"]
        )
        invariants = tuple(
            MachineInvariant(
                _exact_mapping(
                    item,
                    {"id", "states"},
                    "MachineInvariantSchemaMismatch",
                )["id"],
                tuple(item["states"]),
            )
            for item in value["invariants"]
        )
        return cls(
            states,
            transitions,
            value["initial_state"],
            tuple(value["events"]),
            tuple(value["capabilities"]),
            tuple(value["effects"]),
            invariants,
        )

    @classmethod
    def from_json(cls, payload: str) -> "MachineDefinition":
        try:
            value = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            _error("MachineInvalidJson")
        if not isinstance(value, Mapping):
            _error("MachineInvalidJson")
        return cls.from_dict(value)


@dataclass(frozen=True)
class MachineEvent:
    id: str
    payload: Any = None

    def __post_init__(self) -> None:
        _text(self.id, name="event")
        object.__setattr__(self, "payload", _freeze(self.payload))

    @property
    def event_id(self) -> str:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "payload": _thaw(self.payload)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MachineEvent":
        value = _exact_mapping(
            payload,
            {"id", "payload"},
            "MachineEventSchemaMismatch",
        )
        return cls(value["id"], value["payload"])
    def to_json(self) -> str:
        return _json(self.to_dict())


@dataclass(frozen=True)
class MachineSnapshot:
    state: str
    data: Any = None
    sequence: int = 0
    history_digest: str = _ZERO

    def __post_init__(self) -> None:
        _text(self.state, name="snapshot state")
        if type(self.sequence) is not int or self.sequence < 0:
            _error("MachineInvalidSequence")
        if type(self.history_digest) is not str or len(self.history_digest) != 64 or any(c not in "0123456789abcdef" for c in self.history_digest):
            _error("MachineInvalidHistoryDigest")
        object.__setattr__(self, "data", _freeze(self.data))

    @property
    def state_id(self) -> str:
        return self.state

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "data": _thaw(self.data), "sequence": self.sequence, "history_digest": self.history_digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MachineSnapshot":
        value = _exact_mapping(
            payload,
            {
                "state",
                "data",
                "sequence",
                "history_digest",
            },
            "MachineSnapshotSchemaMismatch",
        )
        return cls(
            value["state"],
            value["data"],
            value["sequence"],
            value["history_digest"],
        )


def _runtime_type(value: Any) -> str:
    return {
        bool: "Bool",
        int: "UInt64",
        float: "Float64",
        str: "Text",
        bytes: "Bytes",
        type(None): "Unit",
    }.get(type(value), type(value).__name__)


@dataclass(frozen=True)
class MachineEventRecord:
    sequence: int
    event: MachineEvent
    transition_id: str
    source: str
    target: str
    previous_digest: str
    digest: str

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            _error("MachineInvalidEventRecord")
        if not isinstance(self.event, MachineEvent):
            _error("MachineInvalidEventRecord")
        for value in (
            self.transition_id,
            self.source,
            self.target,
        ):
            _text(value, name="event record")
        for value in (
            self.previous_digest,
            self.digest,
        ):
            if (
                type(value) is not str
                or len(value) != 64
                or any(
                    char
                    not in "0123456789abcdef"
                    for char in value
                )
            ):
                _error("MachineInvalidEventRecord")

    def to_dict(self) -> dict[str, Any]:
        return {"sequence": self.sequence, "event": self.event.to_dict(), "transition_id": self.transition_id, "source": self.source, "target": self.target, "previous_digest": self.previous_digest, "digest": self.digest}
    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MachineEventRecord":
        value = _exact_mapping(
            payload,
            {
                "sequence",
                "event",
                "transition_id",
                "source",
                "target",
                "previous_digest",
                "digest",
            },
            "MachineEventRecordSchemaMismatch",
        )
        return cls(
            value["sequence"],
            MachineEvent.from_dict(value["event"]),
            value["transition_id"],
            value["source"],
            value["target"],
            value["previous_digest"],
            value["digest"],
        )


@dataclass(frozen=True)
class MachineExecution:
    snapshot: MachineSnapshot
    record: MachineEventRecord

    @property
    def digest(self) -> str:
        return self.record.digest

    @property
    def history(self) -> tuple[MachineEventRecord, ...]:
        return (self.record,)
    @property
    def event_record(self) -> MachineEventRecord:
        return self.record

    @property
    def event(self) -> MachineEvent:
        return self.record.event

    @property
    def history_digest(self) -> str:
        return self.snapshot.history_digest

    def to_dict(self) -> dict[str, Any]:
        return {"snapshot": self.snapshot.to_dict(), "record": self.record.to_dict()}


def _invoke(hook: Callable[..., Any], args: tuple[Any, ...], *, code: str) -> Any:
    try:
        signature = inspect.signature(hook)
    except (TypeError, ValueError):
        return hook(*args)
    positional = [item for item in signature.parameters.values() if item.kind in (item.POSITIONAL_ONLY, item.POSITIONAL_OR_KEYWORD)]
    required = sum(item.default is item.empty for item in positional)
    variadic = any(item.kind is item.VAR_POSITIONAL for item in signature.parameters.values())
    if variadic or len(positional) >= len(args):
        if required > len(args):
            _error(code)
        return hook(*args)
    if required > len(positional):
        _error(code)
    return hook(*args[: len(positional)])


class MachineRuntime:
    """Bound executor for one immutable machine definition."""

    def __init__(self, definition: MachineDefinition, *, capability_bindings: Mapping[str, Any] | None = None, action_handlers: Mapping[str, Callable[..., Any]] | None = None, invariant_bindings: Mapping[str, Callable[..., bool]] | None = None) -> None:
        if not isinstance(definition, MachineDefinition):
            _error("MachineInvalidDefinition")
        self.definition = definition
        self.capability_bindings = self._exact_bindings(capability_bindings, definition.capabilities, callable_values=False, code="MachineCapabilityBindingMismatch")
        action_ids = tuple(sorted({item.action_id for item in definition.transitions if item.action_id is not None}))
        self.action_handlers = self._exact_bindings(action_handlers, action_ids, callable_values=True, code="MachineActionBindingMismatch")
        invariant_ids = tuple(item.id for item in definition.invariants)
        self.invariant_bindings = self._exact_bindings(invariant_bindings, invariant_ids, callable_values=True, code="MachineInvariantBindingMismatch")

    @staticmethod
    def _exact_bindings(bindings: Mapping[str, Any] | None, expected: Iterable[str], *, callable_values: bool, code: str) -> FrozenDict:
        expected_tuple = tuple(expected)
        actual = {} if bindings is None else dict(bindings)
        if tuple(sorted(actual)) != expected_tuple:
            _error(code)
        if any(type(key) is not str for key in actual):
            _error(code)
        if callable_values and any(not callable(value) for value in actual.values()):
            _error(code)
        return FrozenDict(actual)

    @staticmethod
    def _check_state_data(
        state: MachineState,
        data: Any,
    ) -> None:
        if not state.fields:
            return
        if (
            not isinstance(data, Mapping)
            or set(data)
            != {
                name
                for name, _ in state.fields
            }
        ):
            _error(
                "MachineStatePayloadMismatch",
                state.id,
            )
        integer_types = {
            "Byte",
            "Int",
            "Int8",
            "Int16",
            "Int32",
            "Int64",
            "UInt",
            "UInt8",
            "UInt16",
            "UInt32",
            "UInt64",
        }
        for name, expected in state.fields:
            value = data[name]
            actual = _runtime_type(value)
            if (
                expected in integer_types
                and type(value) is int
            ):
                continue
            if actual != expected:
                _error(
                    "MachineStateFieldTypeMismatch",
                    f"{state.id}.{name}:"
                    f"{expected}:{actual}",
                )

    def initial_snapshot(self, data: Any = None) -> MachineSnapshot:
        state = self.definition.state_map[
            self.definition.initial_state
        ]
        self._check_state_data(state, data)
        snapshot = MachineSnapshot(
            self.definition.initial_state,
            data,
        )
        genesis = _digest(
            {
                "definition": self.definition.digest,
                "snapshot": snapshot.to_dict(),
            }
        )
        return MachineSnapshot(
            snapshot.state,
            snapshot.data,
            0,
            genesis,
        )

    def _invariant_ids(self, state: MachineState, transition: MachineTransition, *, pre: bool) -> tuple[str, ...]:
        selected = list(state.invariants)
        selected.extend(
            item.id
            for item in self.definition.invariants
            if (not item.states or state.id in item.states) and item.id not in selected
        )
        selected.extend(transition.pre_invariants if pre else transition.post_invariants)
        return tuple(dict.fromkeys(selected))

    def _check_invariants(self, snapshot: MachineSnapshot, ids: Iterable[str]) -> None:
        for invariant_id in ids:
            result = _invoke(self.invariant_bindings[invariant_id], (snapshot,), code="MachineInvariantHookSignature")
            if result is not True:
                _error("MachineInvariantFailed", invariant_id)

    def execute(
        self,
        snapshot: MachineSnapshot,
        event: MachineEvent,
    ) -> MachineExecution:
        if (
            not isinstance(snapshot, MachineSnapshot)
            or not isinstance(event, MachineEvent)
        ):
            _error("MachineInvalidExecutionInput")
        if snapshot.state not in self.definition.state_map:
            _error("MachineUnknownSnapshotState")
        state = self.definition.state_map[
            snapshot.state
        ]
        self._check_state_data(state, snapshot.data)
        expected_genesis = self.initial_snapshot(
            snapshot.data
        ).history_digest
        if (
            snapshot.sequence == 0
            and snapshot.history_digest
            != expected_genesis
        ):
            _error("MachineInvalidHistory")
        transition = self.definition.transition_map.get(
            (snapshot.state, event.id)
        )
        if transition is None:
            _error(
                "MachineIllegalEvent",
                f"{snapshot.state}:{event.id}",
            )
        self._check_invariants(
            snapshot,
            self._invariant_ids(
                state,
                transition,
                pre=True,
            ),
        )
        data = snapshot.data
        if transition.action_id is not None:
            bindings = FrozenDict(
                (
                    name,
                    self.capability_bindings[name],
                )
                for name in transition.capabilities
            )
            result = _invoke(
                self.action_handlers[
                    transition.action_id
                ],
                (snapshot, event, bindings),
                code="MachineActionHookSignature",
            )
            if result is not None:
                data = (
                    result.data
                    if isinstance(
                        result,
                        MachineSnapshot,
                    )
                    else result
                )
                data = _freeze(data)
        candidate = MachineSnapshot(
            transition.target,
            data,
            snapshot.sequence + 1,
            snapshot.history_digest,
        )
        target = self.definition.state_map[
            transition.target
        ]
        self._check_state_data(
            target,
            candidate.data,
        )
        self._check_invariants(
            candidate,
            self._invariant_ids(
                target,
                transition,
                pre=False,
            ),
        )
        record_payload = {
            "definition": self.definition.digest,
            "sequence": candidate.sequence,
            "event": event.to_dict(),
            "transition_id": transition.id,
            "source": snapshot.state,
            "target": candidate.state,
            "previous_digest": snapshot.history_digest,
            "data": _thaw(candidate.data),
        }
        digest = _digest(record_payload)
        record = MachineEventRecord(
            candidate.sequence,
            event,
            transition.id,
            snapshot.state,
            candidate.state,
            snapshot.history_digest,
            digest,
        )
        return MachineExecution(
            MachineSnapshot(
                candidate.state,
                candidate.data,
                candidate.sequence,
                digest,
            ),
            record,
        )

    def execute_event(self, snapshot: MachineSnapshot, event: MachineEvent) -> MachineExecution:
        return self.execute(snapshot, event)

    def replay(self, initial: MachineSnapshot, history: Sequence[MachineExecution | MachineEventRecord | Mapping[str, Any]]) -> MachineSnapshot:
        current = initial
        if current.state != self.definition.initial_state or current.sequence != 0:
            _error("MachineInvalidReplayStart")
        expected_genesis = self.initial_snapshot(
            current.data
        ).history_digest
        if current.history_digest != expected_genesis:
            _error("MachineHistoryDigestMismatch")
        for item in history:
            record = item.record if isinstance(item, MachineExecution) else item
            if isinstance(record, Mapping):
                record = MachineEventRecord(int(record["sequence"]), MachineEvent.from_dict(record["event"]), record["transition_id"], record["source"], record["target"], record["previous_digest"], record["digest"])
            if not isinstance(record, MachineEventRecord):
                _error("MachineInvalidHistoryRecord")
            if record.sequence != current.sequence + 1 or record.previous_digest != current.history_digest or record.source != current.state:
                _error("MachineHistoryDigestMismatch")
            execution = self.execute(current, record.event)
            if execution.record != record or execution.snapshot.state != record.target:
                _error("MachineHistoryTampered")
            current = execution.snapshot
        return current
    def replay_history(self, initial: MachineSnapshot, history: Sequence[MachineExecution | MachineEventRecord | Mapping[str, Any]]) -> MachineSnapshot:
        return self.replay(initial, history)


def execute_event(runtime: MachineRuntime, snapshot: MachineSnapshot, event: MachineEvent) -> MachineExecution:
    return runtime.execute(snapshot, event)


def replay_history(runtime: MachineRuntime, initial: MachineSnapshot, history: Sequence[MachineExecution | MachineEventRecord | Mapping[str, Any]]) -> MachineSnapshot:
    return runtime.replay(initial, history)


__all__ = ["MachineRuntimeError", "MachineInvariant", "MachineState", "MachineTransition", "MachineDefinition", "MachineEvent", "MachineSnapshot", "MachineEventRecord", "MachineExecution", "MachineRuntime", "execute_event", "replay_history"]
