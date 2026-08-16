from __future__ import annotations

import json

import pytest

from merlo.debugger import (
    Breakpoint,
    DebugEvent,
    DebugTrace,
    SourceLocation,
    TraceDebugger,
)
from merlo.profiler import MAX_WORK_UNITS, ProfileReport, merge_profiles, profile_callable


def _trace() -> DebugTrace:
    return DebugTrace(
        (
            DebugEvent(0, "load", "started", SourceLocation("main.mlo", 4), {"path": "input"}),
            DebugEvent(1, "load", "succeeded", SourceLocation("main.mlo", 4), {"bytes": 12}),
            DebugEvent(2, "emit", "succeeded", SourceLocation("main.mlo", 7), {"rows": [1, 2]}),
        )
    )


def test_debug_trace_roundtrip_step_continue_and_inspect() -> None:
    trace = _trace()
    debugger = TraceDebugger(
        DebugTrace.from_json(trace.to_json()),
        (Breakpoint(path="main.mlo", line=4, statuses=frozenset({"succeeded"})),),
    )

    assert debugger.step().status == "started"
    assert debugger.continue_run().sequence == 1
    assert debugger.inspect("bytes") == 12
    assert debugger.terminal("load").status == "succeeded"
    assert debugger.continue_run().node_id == "emit"
    assert debugger.finished
    assert debugger.step() is None
    debugger.reset()
    assert debugger.current is None


def test_debug_trace_rejects_tampering_and_invalid_breakpoints() -> None:
    payload = json.loads(_trace().to_json())
    payload["events"][1]["values"]["bytes"] = 99
    with pytest.raises(ValueError, match="DebugTraceDigestMismatch"):
        DebugTrace.from_dict(payload)
    with pytest.raises(ValueError, match="DebugBreakpointMissingTarget"):
        Breakpoint()
    with pytest.raises(ValueError, match="DebugTraceSequenceMismatch"):
        DebugTrace((DebugEvent(2, "node", "started"),))


def test_profile_callable_uses_bounded_samples_and_exact_summary() -> None:
    ticks = iter((0, 2, 5, 10, 17, 26, 37))
    calls: list[int] = []

    def action() -> int:
        calls.append(len(calls))
        return len(calls)

    report, result = profile_callable(
        "work",
        action,
        iterations=3,
        warmups=1,
        work_units=2,
        clock=lambda: next(ticks),
    )

    assert result == 4
    assert [item.duration_ns for item in report.samples] == [2, 5, 9]
    assert report.total_ns == 16
    assert report.median_ns == 5
    assert report.p95_ns == 9
    assert report.to_dict()["summary"]["work_units"] == 6
    assert ProfileReport.from_json(report.to_json()) == report


def test_profile_rejects_tampering_clock_regression_and_merges() -> None:
    first, _ = profile_callable("one", lambda: None, iterations=1, warmups=0, clock=iter((1, 3)).__next__)
    second, _ = profile_callable("two", lambda: None, iterations=1, warmups=0, clock=iter((5, 8)).__next__)
    merged = merge_profiles("all", (first, second))
    assert [item.duration_ns for item in merged.samples] == [2, 3]

    payload = json.loads(merged.to_json())
    payload["samples"][0]["duration_ns"] = 200
    with pytest.raises(ValueError, match="ProfileDigestMismatch"):
        ProfileReport.from_dict(payload)
    with pytest.raises(ValueError, match="ProfileClockWentBackwards"):
        profile_callable("bad", lambda: None, iterations=1, warmups=0, clock=iter((2, 1)).__next__)
    with pytest.raises(ValueError, match="ProfileInvalidIterations"):
        profile_callable("bad", lambda: None, iterations=0)
    with pytest.raises(ValueError, match="ProfileInvalidWorkUnits"):
        profile_callable("bad", lambda: None, work_units=MAX_WORK_UNITS + 1)
