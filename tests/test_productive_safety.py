from __future__ import annotations

from copy import deepcopy

import pytest

from merlo.productive_safety import (
    MAP_SCENARIOS,
    RESOURCE_SCENARIOS,
    SAFETY_INVARIANTS,
    SAFETY_RECORD_FIELDS,
    validate_productive_safety_record,
    validate_productive_safety_report,
)


MAP_SANITIZERS = ("asan", "ubsan", "lsan")


def _record(
    *,
    scenario: str = "resource_unicode",
    sanitizer: str | None = None,
    status: str = "PASSED",
    exit_code: int | None = 0,
) -> dict[str, object]:
    if sanitizer is None:
        sanitizer = "asan" if scenario in MAP_SCENARIOS else "none"
    expectation = "typed_failure" if scenario == "map_uint64_overflow" else "success"
    command = ["python", "probe.py", scenario]
    if sanitizer != "none":
        command.append(sanitizer)
    if scenario in MAP_SCENARIOS:
        marker = "MerloOverflow:MapUInt64" if expectation == "typed_failure" else "OK result="
    else:
        marker = f"SAFETY_PROBE PASSED {scenario}"
    return {
        "scenario": scenario,
        "sanitizer": sanitizer,
        "command": command,
        "exit_code": exit_code,
        "stdout_sha256": "a" * 64,
        "stderr_sha256": "b" * 64,
        "status": status,
        "expectation": expectation,
        "reason": f"marker {marker} observed" if status == "PASSED" else "instrumentation unavailable",
    }


def _records(status: str = "UNSUPPORTED") -> list[dict[str, object]]:
    exit_code = 0 if status == "PASSED" else None
    return [
        _record(
            scenario=scenario,
            sanitizer=sanitizer,
            status=status,
            exit_code=-6 if scenario == MAP_SCENARIOS[1] and status == "PASSED" else exit_code,
        )
        for scenario in MAP_SCENARIOS
        for sanitizer in MAP_SANITIZERS
    ] + [
        _record(scenario=scenario, status=status, exit_code=exit_code)
        for scenario in RESOURCE_SCENARIOS
    ]


def _report(
    records: list[dict[str, object]],
    *,
    status: str = "UNSUPPORTED",
    invariant_status: str = "UNSUPPORTED",
    aggregate_proofs: dict[str, bool] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "merlo.productive-safety",
        "status": status,
        "records": records,
        "invariants": {name: invariant_status for name in SAFETY_INVARIANTS},
        "aggregate_proofs": {} if aggregate_proofs is None else aggregate_proofs,
    }


def test_safety_record_schema_is_explicit_and_validates() -> None:
    record = _record()
    validate_productive_safety_record(record)
    assert tuple(record) == SAFETY_RECORD_FIELDS


def test_safety_report_validator_requires_every_invariant() -> None:
    validate_productive_safety_report(_report(_records()))


def test_safety_report_accepts_a_fully_passed_schema() -> None:
    validate_productive_safety_report(
        _report(
            _records("PASSED"),
            status="PASSED",
            invariant_status="PASSED",
            aggregate_proofs={
                "map_safety": True,
                "streaming_resource_safety": True,
                "all_relevant_executable_checks": True,
            },
        )
    )
def test_safety_report_accepts_canonical_json_key_order() -> None:
    report = _report(_records())
    canonical = {
        key: report[key]
        for key in sorted(report)
    }
    validate_productive_safety_report(canonical)




def test_safety_report_preserves_an_executed_failure() -> None:
    records = _records("PASSED")
    records[0]["status"] = "FAILED"
    records[0]["exit_code"] = 1
    records[0]["reason"] = "Map behavior failed"
    report = _report(
        records,
        status="FAILED",
        invariant_status="PASSED",
        aggregate_proofs={"streaming_resource_safety": True},
    )
    report["invariants"].update(
        {
            "map_collisions": "FAILED",
            "map_growth": "FAILED",
            "map_updates": "FAILED",
            "map_cleanup": "FAILED",
        }
    )
    validate_productive_safety_report(report)


def test_safety_record_binds_map_sanitizer_to_command() -> None:
    record = _record(scenario=MAP_SCENARIOS[0], sanitizer="asan")
    record["command"] = ["python", "probe.py", MAP_SCENARIOS[0]]
    with pytest.raises(ValueError, match="sanitizer"):
        validate_productive_safety_record(record)


@pytest.mark.parametrize("mutation", ("duplicate", "missing"))
def test_safety_report_rejects_duplicate_or_missing_sanitizer_record(mutation: str) -> None:
    records = _records()
    if mutation == "duplicate":
        records[1] = deepcopy(records[0])
    else:
        records.pop(1)
    with pytest.raises(ValueError, match="coverage"):
        validate_productive_safety_report(_report(records))


def test_safety_record_rejects_forged_pass_and_missing_fields() -> None:
    forged = _record(status="PASSED", exit_code=1)
    with pytest.raises(ValueError, match="exit code"):
        validate_productive_safety_record(forged)

    missing = deepcopy(_record())
    del missing["stderr_sha256"]
    with pytest.raises(ValueError, match="fields"):
        validate_productive_safety_record(missing)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("invariants", {"map_collisions": "PASSED"}, "invariant"),
        ("status", "PASSED", "status"),
        ("aggregate_proofs", {"map_safety": True}, "aggregate proof"),
    ),
)
def test_safety_report_rejects_forged_derived_evidence(
    field: str, value: object, match: str
) -> None:
    report = _report(_records())
    if field == "invariants":
        report["invariants"] = {name: "UNSUPPORTED" for name in SAFETY_INVARIANTS}
        report["invariants"].update(value)  # type: ignore[union-attr]
    else:
        report[field] = value
    with pytest.raises(ValueError, match=match):
        validate_productive_safety_report(report)


def test_safety_report_rejects_unsupported_as_aggregate_proof() -> None:
    report = _report(_records(), aggregate_proofs={"all_relevant_executable_checks": True})
    with pytest.raises(ValueError, match="aggregate proof"):
        validate_productive_safety_report(report)
