"""Deterministic replay debugger for Merlo runtime traces."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

DEBUG_TRACE_SCHEMA_VERSION = 1
DEBUG_TRACE_CONTRACT = "merlo.debug-trace.v1"
_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timed_out", "compensated"})


def _error(code: str, detail: str = "") -> ValueError:
    return ValueError(code if not detail else f"{code}: {detail}")


def _canonical(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise _error("DebugTraceNonCanonicalValue")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _error("DebugTraceNonStringKey")
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    raise _error("DebugTraceNonCanonicalValue", type(value).__name__)


def _freeze(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise _error("DebugTraceNonCanonicalValue")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _error("DebugTraceNonStringKey")
        return MappingProxyType({key: _freeze(value[key]) for key in sorted(value)})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    raise _error("DebugTraceNonCanonicalValue", type(value).__name__)


def _json(value: Any) -> str:
    return json.dumps(_canonical(value), ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceLocation:
    path: str
    line: int
    column: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise _error("DebugInvalidPath")
        if type(self.line) is not int or self.line < 1:
            raise _error("DebugInvalidLine")
        if type(self.column) is not int or self.column < 1:
            raise _error("DebugInvalidColumn")

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line, "column": self.column}


@dataclass(frozen=True, slots=True)
class DebugEvent:
    sequence: int
    node_id: str
    status: str
    location: SourceLocation | None = None
    values: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 0:
            raise _error("DebugInvalidSequence")
        if not isinstance(self.node_id, str) or not self.node_id:
            raise _error("DebugInvalidNodeId")
        if not isinstance(self.status, str) or not self.status:
            raise _error("DebugInvalidStatus")
        if not isinstance(self.values, Mapping):
            raise _error("DebugValuesSchemaMismatch")
        object.__setattr__(self, "values", _freeze(self.values))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "node_id": self.node_id,
            "status": self.status,
            "location": None if self.location is None else self.location.to_dict(),
            "values": _canonical(self.values),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DebugEvent":
        if not isinstance(value, Mapping) or set(value) != {"sequence", "node_id", "status", "location", "values"}:
            raise _error("DebugEventSchemaMismatch")
        raw_location = value["location"]
        location = None
        if raw_location is not None:
            if not isinstance(raw_location, Mapping) or set(raw_location) != {"path", "line", "column"}:
                raise _error("DebugLocationSchemaMismatch")
            location = SourceLocation(raw_location["path"], raw_location["line"], raw_location["column"])
        values = value["values"]
        if not isinstance(values, Mapping):
            raise _error("DebugValuesSchemaMismatch")
        return cls(value["sequence"], value["node_id"], value["status"], location, values)


@dataclass(frozen=True, slots=True)
class DebugTrace:
    events: tuple[DebugEvent, ...]
    schema_version: int = DEBUG_TRACE_SCHEMA_VERSION
    contract: str = DEBUG_TRACE_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        events = tuple(self.events)
        if any(not isinstance(event, DebugEvent) for event in events):
            raise _error("DebugTraceEventsMismatch")
        if self.schema_version != DEBUG_TRACE_SCHEMA_VERSION or self.contract != DEBUG_TRACE_CONTRACT:
            raise _error("DebugTraceContractMismatch")
        if tuple(event.sequence for event in events) != tuple(range(len(events))):
            raise _error("DebugTraceSequenceMismatch")
        payload = {"schema_version": self.schema_version, "contract": self.contract, "events": [item.to_dict() for item in events]}
        expected = _digest(payload)
        if self.digest and self.digest != expected:
            raise _error("DebugTraceDigestMismatch")
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "digest", expected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "events": [item.to_dict() for item in self.events],
            "digest": self.digest,
        }

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DebugTrace":
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "contract", "events", "digest"}:
            raise _error("DebugTraceSchemaMismatch")
        if not isinstance(value["digest"], str) or len(value["digest"]) != 64:
            raise _error("DebugTraceDigestMismatch")
        raw_events = value["events"]
        if not isinstance(raw_events, list):
            raise _error("DebugTraceEventsMismatch")
        return cls(tuple(DebugEvent.from_dict(item) for item in raw_events), value["schema_version"], value["contract"], value["digest"])

    @classmethod
    def from_json(cls, payload: str) -> "DebugTrace":
        try:
            value = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise _error("DebugTraceInvalidJSON") from exc
        return cls.from_dict(value)


@dataclass(frozen=True, slots=True)
class Breakpoint:
    path: str | None = None
    line: int | None = None
    node_id: str | None = None
    statuses: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.path is None and self.node_id is None:
            raise _error("DebugBreakpointMissingTarget")
        if self.path is not None and (not isinstance(self.path, str) or not self.path):
            raise _error("DebugInvalidBreakpointPath")
        if self.line is not None and (self.path is None or type(self.line) is not int or self.line < 1):
            raise _error("DebugInvalidBreakpointLine")
        if self.node_id is not None and (not isinstance(self.node_id, str) or not self.node_id):
            raise _error("DebugInvalidBreakpointNode")
        try:
            statuses = frozenset(self.statuses)
        except TypeError as exc:
            raise _error("DebugInvalidBreakpointStatus") from exc
        if any(not isinstance(item, str) or not item for item in statuses):
            raise _error("DebugInvalidBreakpointStatus")
        object.__setattr__(self, "statuses", statuses)

    def matches(self, event: DebugEvent) -> bool:
        if self.node_id is not None and event.node_id != self.node_id:
            return False
        if self.path is not None:
            if event.location is None or event.location.path != self.path:
                return False
            if self.line is not None and event.location.line != self.line:
                return False
        return not self.statuses or event.status in self.statuses


class TraceDebugger:
    """Cursor-based stepping over a validated immutable trace."""

    def __init__(self, trace: DebugTrace, breakpoints: Iterable[Breakpoint] = ()) -> None:
        if not isinstance(trace, DebugTrace):
            raise _error("DebugInvalidTrace")
        self._trace = trace
        self._breakpoints = tuple(breakpoints)
        if any(not isinstance(item, Breakpoint) for item in self._breakpoints):
            raise _error("DebugInvalidBreakpoint")
        self._cursor = -1

    @property
    def current(self) -> DebugEvent | None:
        return None if self._cursor < 0 else self._trace.events[self._cursor]

    @property
    def finished(self) -> bool:
        return self._cursor >= len(self._trace.events) - 1

    def reset(self) -> None:
        self._cursor = -1

    def step(self) -> DebugEvent | None:
        if self.finished:
            return None
        self._cursor += 1
        return self.current

    def continue_run(self) -> DebugEvent | None:
        while not self.finished:
            event = self.step()
            if event is not None and any(item.matches(event) for item in self._breakpoints):
                return event
        return self.current

    def inspect(self, name: str) -> Any:
        if self.current is None:
            raise _error("DebugNotStarted")
        if name not in self.current.values:
            raise _error("DebugUnknownValue", name)
        return self.current.values[name]

    def terminal(self, node_id: str) -> DebugEvent | None:
        return next((item for item in reversed(self._trace.events) if item.node_id == node_id and item.status in _TERMINAL), None)


def build_debug_trace(events: Sequence[DebugEvent | Mapping[str, Any]]) -> DebugTrace:
    normalized = tuple(item if isinstance(item, DebugEvent) else DebugEvent.from_dict(item) for item in events)
    return DebugTrace(normalized)


__all__ = [
    "DEBUG_TRACE_CONTRACT",
    "DEBUG_TRACE_SCHEMA_VERSION",
    "Breakpoint",
    "DebugEvent",
    "DebugTrace",
    "SourceLocation",
    "TraceDebugger",
    "build_debug_trace",
]
