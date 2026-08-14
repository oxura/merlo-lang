"""Validator and future runner for the public Merlo/Python AI A/B preregistration.

This module deliberately has no provider implementation.  It validates every
immutable input before a caller can submit an attempt, and returns an explicit
unmeasured report when provider identity is not locked.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "merlo.ai-ab.v1"
PROTOCOL_STATUS = "PREREGISTERED_UNRUN"
PAIR_COUNT = 90
TASK_COUNT = 30
REPLICATES = 3
BOOTSTRAP_REPLICATES = 10_000
PROVIDER_IDENTITY_INCOMPLETE = "UNMEASURED_PROVIDER_IDENTITY_INCOMPLETE"

class ProtocolError(ValueError):
    """Raised when a locked protocol or task artifact is invalid."""
LOCKED_PROTOCOL_SHA256 = "a4d8468f8e7b42f1b8849c9da10a868597d2c848481717604d5f977b2ffbea21"
LOCKED_TASKS_SHA256 = "e996fd273644ef8f369b6e7c5f45f5656b281f01a9569704d512bcb90a211e79"
PREREGISTRATION_ROOT_SHA256 = "f051064cd214e4447658ee86d904c3a7fb04079818302c69017b8194ff2272d1"

@dataclass(frozen=True)
class Validation:
    valid: bool
    errors: tuple[str, ...]
    task_count: int
    pair_count: int
    denominators: Mapping[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "errors": list(self.errors),
                "task_count": self.task_count, "pair_count": self.pair_count,
                "denominators": dict(self.denominators)}

def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def tree_digest(root: Path) -> str:
    """Content digest using sorted relative paths and length-delimited bytes."""
    if not root.is_dir():
        raise ProtocolError(f"missing fixture root: {root}")
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()

def _without_digest(value: Mapping[str, Any], key: str) -> dict[str, Any]:
    result = dict(value)
    result.pop(key, None)
    return result

def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"expected JSON object: {path}")
    return value

def _task_contract(task: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(_without_digest(task, "task_sha256")))

def _protocol_contract(protocol: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(_without_digest(protocol, "protocol_sha256")))

def render_prompt(
    task: Mapping[str, Any],
    language: str,
    workspace: str,
    test_command: str,
) -> str:
    template = str(task["prompt_template"])
    return (
        template.replace("{{TASK_ID}}", str(task["id"]))
        .replace("{{LANGUAGE}}", language)
        .replace("{{WORKSPACE}}", workspace)
        .replace("{{TEST_COMMAND}}", test_command)
    )


def _documents_match_preregistration_root(
    protocol: Mapping[str, Any],
    tasks: Mapping[str, Any],
) -> bool:
    schedule = tasks.get("schedule")
    component_root = sha256_bytes(canonical_json({
        "protocol_sha256": protocol.get("protocol_sha256"),
        "tasks_sha256": tasks.get("tasks_sha256"),
    }))
    return (
        component_root == PREREGISTRATION_ROOT_SHA256
        and protocol.get("protocol_sha256") == LOCKED_PROTOCOL_SHA256
        and tasks.get("tasks_sha256") == LOCKED_TASKS_SHA256
        and protocol.get("protocol_sha256") == _protocol_contract(protocol)
        and tasks.get("tasks_sha256")
        == sha256_bytes(canonical_json(_without_digest(tasks, "tasks_sha256")))
        and protocol.get("task_manifest_sha256") == sha256_bytes(canonical_json(tasks))
        and isinstance(schedule, list)
        and protocol.get("schedule_sha256") == sha256_bytes(canonical_json(schedule))
    )

def normalize_prompt(prompt: str) -> str:
    return (prompt.replace("LANGUAGE=Merlo", "LANGUAGE={{LANGUAGE}}")
            .replace("LANGUAGE=Python 3.12", "LANGUAGE={{LANGUAGE}}")
            .replace("WORKSPACE=/merlo/workspace", "WORKSPACE={{WORKSPACE}}")
            .replace("WORKSPACE=/python/workspace", "WORKSPACE={{WORKSPACE}}")
            .replace("TEST_COMMAND=merlo test", "TEST_COMMAND={{TEST_COMMAND}}")
            .replace("TEST_COMMAND=python -m pytest -q", "TEST_COMMAND={{TEST_COMMAND}}"))

def _error(errors: list[str], message: str) -> None:
    errors.append(message)

def validate_protocol(root: str | Path | None = None) -> Validation:
    base = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    study = base / "benchmarks" / "ai-ab-v1"
    errors: list[str] = []
    try:
        protocol = _load_json(study / "protocol.json")
    except ProtocolError as exc:
        return Validation(False, (str(exc),), 0, 0, {})
    try:
        tasks = _load_json(study / "tasks.json")
    except ProtocolError as exc:
        return Validation(False, (str(exc),), 0, 0, {})
    if protocol.get("schema_version") != SCHEMA_VERSION:
        _error(errors, "schema version mismatch")
    if protocol.get("status") != PROTOCOL_STATUS:
        _error(errors, "protocol status is not PREREGISTERED_UNRUN")
    if protocol.get("protocol_sha256") != _protocol_contract(protocol):
        _error(errors, "protocol hash mismatch")
    if tasks.get("schema_version") != SCHEMA_VERSION:
        _error(errors, "task schema version mismatch")
    task_items = tasks.get("tasks")
    if protocol.get("protocol_sha256") != LOCKED_PROTOCOL_SHA256:
        _error(errors, "protocol differs from published preregistration root")
    schedule = tasks.get("schedule")
    if not isinstance(task_items, list) or not isinstance(schedule, list):
        return Validation(False, tuple(errors + ["tasks and schedule must be arrays"]), 0, 0, {})
    if tasks.get("tasks_sha256") != sha256_bytes(canonical_json(_without_digest(tasks, "tasks_sha256"))):
        _error(errors, "task manifest hash mismatch")
    ids: set[str] = set()
    pair_ids: set[str] = set()
    expected_strata = {"deterministic_cli_data": 10, "multi_module_api_migration": 10, "regression_repair": 10}
    if tasks.get("tasks_sha256") != LOCKED_TASKS_SHA256:
        _error(errors, "tasks differ from published preregistration root")
    strata: dict[str, int] = {}
    for task in task_items:
        if not isinstance(task, dict):
            _error(errors, "non-object task entry")
            continue
        task_id = task.get("id")
        if not isinstance(task_id, str) or task_id in ids:
            _error(errors, f"missing or duplicate task id: {task_id!r}")
            continue
        ids.add(task_id)
        strata[task.get("stratum", "")] = strata.get(task.get("stratum", ""), 0) + 1
        if task.get("task_sha256") != _task_contract(task):
            _error(errors, f"task hash mismatch: {task_id}")
        for arm in ("merlo", "python"):
            fixture = task.get("fixtures", {}).get(arm, {})
            relative = fixture.get("path")
            if not isinstance(relative, str):
                _error(errors, f"missing {arm} mirror: {task_id}")
                continue
            fixture_root = study / relative
            if not fixture_root.is_dir() or not any(fixture_root.iterdir()):
                _error(errors, f"missing {arm} mirror: {task_id}")
            elif fixture.get("sha256") != tree_digest(fixture_root):
                _error(errors, f"fixture hash mismatch: {task_id}/{arm}")
            expected_command = (["merlo", "run", "{workspace}/main.mlo"]
                                if arm == "merlo" else ["python3", "-B", "{workspace}/main.py"])
            if fixture.get("run_command") != expected_command:
                _error(errors, f"execution command changed: {task_id}/{arm}")
            source_files = fixture.get("source_files")
            if not isinstance(source_files, list) or not source_files or any(not (fixture_root / name).is_file() for name in source_files):
                _error(errors, f"missing source files: {task_id}/{arm}")
        oracle = task.get("oracle", {})
        oracle_path = study / str(oracle.get("path", ""))
        if not oracle_path.is_file():
            _error(errors, f"missing oracle: {task_id}")
        elif oracle.get("sha256") != sha256_file(oracle_path):
            _error(errors, f"oracle hash mismatch: {task_id}")
        allowlist = task.get("allowlist")
        if (not isinstance(allowlist, dict) or set(allowlist) != {"merlo", "python"}
                or any(not isinstance(allowlist[arm], list) or not allowlist[arm]
                       or any(not isinstance(x, str) for x in allowlist[arm])
                       for arm in ("merlo", "python"))):
            _error(errors, f"invalid per-arm allowlist: {task_id}")
        template = task.get("prompt_template")
        if not isinstance(template, str) or "{{LANGUAGE}}" not in template or "{{WORKSPACE}}" not in template or "{{TEST_COMMAND}}" not in template:
            _error(errors, f"invalid prompt template: {task_id}")
        else:
            normalized_merlo = normalize_prompt(render_prompt(task, "Merlo", "/merlo/workspace", "merlo test"))
            normalized_python = normalize_prompt(render_prompt(task, "Python 3.12", "/python/workspace", "python -m pytest -q"))
            if normalized_merlo != normalized_python:
                _error(errors, f"prompt parity mismatch: {task_id}")
            normalized_hash = sha256_bytes(normalized_merlo.encode("utf-8"))
            if task.get("normalized_prompt_sha256") != normalized_hash:
                _error(errors, f"normalized prompt hash mismatch: {task_id}")
    if len(ids) != TASK_COUNT:
        _error(errors, f"expected {TASK_COUNT} distinct tasks, found {len(ids)}")
    for stratum, count in expected_strata.items():
        if strata.get(stratum) != count:
            _error(errors, f"stratum denominator mismatch: {stratum}")
    if len(schedule) != PAIR_COUNT:
        _error(errors, f"expected {PAIR_COUNT} scheduled pairs, found {len(schedule)}")
    expected_pairs = {(task_id, replicate) for task_id in ids for replicate in range(1, REPLICATES + 1)}
    actual_pairs: set[tuple[str, int]] = set()
    for entry in schedule:
        if not isinstance(entry, dict):
            _error(errors, "non-object schedule entry")
            continue
        pair_id = entry.get("pair_id")
        task_id = entry.get("task_id")
        replicate = entry.get("replicate")
        if not isinstance(pair_id, str) or pair_id in pair_ids:
            _error(errors, f"missing or duplicate pair id: {pair_id!r}")
        pair_ids.add(pair_id)
        if (task_id, replicate) in actual_pairs:
            _error(errors, f"duplicate pair assignment: {task_id}/{replicate}")
        actual_pairs.add((task_id, replicate))
        if entry.get("arm_order") not in (["merlo", "python"], ["python", "merlo"]):
            _error(errors, f"invalid arm order: {pair_id}")
    if actual_pairs != expected_pairs:
        _error(errors, "incomplete pair denominator")
    if protocol.get("schedule_sha256") != sha256_bytes(canonical_json(schedule)):
        _error(errors, "schedule hash mismatch")
    if protocol.get("task_manifest_sha256") != sha256_file(study / "tasks.json"):
        _error(errors, "protocol task manifest lock mismatch")
    if protocol.get("provider_identity") != {"provider": None, "model": None, "revision": None, "key_fingerprint": None}:
        _error(errors, "provider/model revision lock changed; publish an amended protocol")
    if protocol.get("settings") != {"temperature": 0.2, "top_p": 1.0, "max_output_tokens": 8192, "seed": 20260813}:
        _error(errors, "model settings changed")
    if protocol.get("budgets") != {"input_tokens": 12000, "output_tokens": 8192, "wall_time_ms": 180000, "iterations": 40, "tool_calls": 120, "replicates": 3}:
        _error(errors, "budget changed")
    if protocol.get("container") != {"image": "ghcr.io/oxura/merlo-ai-ab-v1@sha256:UNLOCKED_BEFORE_EXECUTION", "network": "denied", "cpu": "2", "memory_mb": 4096, "fresh_workspace": True}:
        _error(errors, "container lock changed")
    if protocol.get("tool_menu") != ["shell", "read", "search", "edit", "test"]:
        _error(errors, "tool menu changed")
    if protocol.get("schedule_seed") != 20260813:
        _error(errors, "schedule seed changed")
    denominators = {"tasks": len(ids), "pairs": len(pair_ids), "arm_attempts": len(pair_ids) * 2}
    return Validation(not errors, tuple(errors), len(ids), len(pair_ids), denominators)

def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validated_digest_map(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise ProtocolError(f"{label} digest map is empty")
    result: dict[str, str] = {}
    for path, digest in value.items():
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or ".." in Path(path).parts
            or not _is_sha256(digest)
        ):
            raise ProtocolError(f"invalid {label} digest map")
        result[path] = digest
    return result


def _validate_oracle_result(result: object, task: Mapping[str, Any]) -> None:
    if not isinstance(result, Mapping):
        raise ProtocolError("oracle result must be an object")
    case_count = len(task.get("language_neutral_spec", {}).get("cases", []))
    if (
        result.get("case_id") != task.get("id")
        or not isinstance(result.get("passed"), bool)
        or case_count < 1
    ):
        raise ProtocolError("oracle identity or aggregate is invalid")
    stratum = task.get("stratum")
    if stratum == "multi_module_api_migration":
        migration = result.get("migration_passed")
        unaffected = result.get("unaffected_passed")
        expected_unaffected = len(
            task.get("language_neutral_spec", {}).get("unaffected_cases", [])
        )
        if (
            not isinstance(migration, list)
            or len(migration) != case_count
            or not all(isinstance(value, bool) for value in migration)
            or not isinstance(unaffected, list)
            or len(unaffected) != expected_unaffected
            or not all(isinstance(value, bool) for value in unaffected)
            or result["passed"] is not all(migration + unaffected)
        ):
            raise ProtocolError("migration oracle evidence is incomplete")
        return
    cases = result.get("cases")
    if (
        not isinstance(cases, list)
        or len(cases) != case_count
        or any(
            not isinstance(item, Mapping)
            or not isinstance(item.get("passed"), bool)
            for item in cases
        )
    ):
        raise ProtocolError("oracle case evidence is incomplete")
    case_passes = [item["passed"] for item in cases]
    if stratum == "regression_repair":
        if (
            result.get("defect_case_passed") is not case_passes[0]
            or result.get("unaffected_cases_passed") is not all(case_passes[1:])
        ):
            raise ProtocolError("regression oracle evidence is inconsistent")
    if result["passed"] is not all(case_passes):
        raise ProtocolError("oracle aggregate does not match case evidence")


def validate_attempt_record(
    record: Mapping[str, Any],
    protocol: Mapping[str, Any],
    task: Mapping[str, Any],
    schedule_entry: Mapping[str, Any] | None = None,
) -> None:
    required = (
        "task_id", "replicate", "arm", "pair_id", "schedule_index", "arm_order",
        "transcript_sha256", "fixture_tree_sha256", "pre_digest", "post_digest",
        "pre_digest_map", "post_digest_map", "oracle_sha256", "oracle", "stdout",
        "stderr", "input_tokens", "output_tokens", "total_tokens", "iterations",
        "tool_calls", "provider_time_ms", "wall_time_ms", "terminal_reason",
        "changed_paths", "irrelevant_edit_count", "regression_count",
        "task_success", "provider_attestation", "contamination_attestation",
        "protocol_sha256", "task_sha256",
    )
    missing = [key for key in required if key not in record]
    if missing:
        raise ProtocolError("attempt missing fields: " + ", ".join(missing))
    if record["task_id"] != task.get("id") or record["arm"] not in ("merlo", "python"):
        raise ProtocolError("attempt identity does not match locked task")
    if (
        record["protocol_sha256"] != protocol.get("protocol_sha256")
        or record["task_sha256"] != task.get("task_sha256")
    ):
        raise ProtocolError("attempt lock hash mismatch")
    if schedule_entry is not None:
        for field in ("pair_id", "task_id", "replicate", "schedule_index", "arm_order"):
            if record.get(field) != schedule_entry.get(field):
                raise ProtocolError("attempt schedule identity mismatch")
        if record["arm"] not in schedule_entry["arm_order"]:
            raise ProtocolError("attempt arm not in locked order")
    transcript = record.get("transcript")
    if not isinstance(transcript, list) or not transcript:
        raise ProtocolError("absent transcript")
    if record["transcript_sha256"] != sha256_bytes(canonical_json(transcript)):
        raise ProtocolError("transcript hash mismatch")
    allowlist = task.get("allowlist", {}).get(record["arm"], [])
    changed_paths = record["changed_paths"]
    if (
        not isinstance(changed_paths, list)
        or any(path not in allowlist for path in changed_paths)
    ):
        raise ProtocolError("out-of-scope edit")
    if record["terminal_reason"] not in {
        "completed", "token_budget", "time_budget", "tool_budget",
        "iteration_budget", "provider_unavailable", "infrastructure_failure",
        "invalid_protocol",
    }:
        raise ProtocolError("invalid terminal reason")
    attestation = record["provider_attestation"]
    if not isinstance(attestation, Mapping) or not provider_identity_complete(attestation):
        raise ProtocolError("incomplete provider attestation")
    if record["contamination_attestation"] != protocol.get("contamination_policy"):
        raise ProtocolError("contamination policy attestation mismatch")
    numeric_fields = (
        "input_tokens", "output_tokens", "total_tokens", "iterations", "tool_calls",
        "provider_time_ms", "wall_time_ms", "irrelevant_edit_count", "regression_count",
    )
    if any(not isinstance(record[key], int) or record[key] < 0 for key in numeric_fields):
        raise ProtocolError("invalid attempt metric")
    if record["total_tokens"] != record["input_tokens"] + record["output_tokens"]:
        raise ProtocolError("token total mismatch")
    budgets = protocol.get("budgets", {})
    limits = {
        "input_tokens": budgets.get("input_tokens"),
        "output_tokens": budgets.get("output_tokens"),
        "iterations": budgets.get("iterations"),
        "tool_calls": budgets.get("tool_calls"),
        "wall_time_ms": budgets.get("wall_time_ms"),
    }
    if any(
        not isinstance(limit, int) or record[field] > limit
        for field, limit in limits.items()
    ):
        raise ProtocolError("attempt budget exceeded")
    if record["total_tokens"] == 0 or record["wall_time_ms"] == 0:
        raise ProtocolError("ratio denominator is not measured")
    if not isinstance(record["task_success"], bool):
        raise ProtocolError("task_success must be boolean")
    if record["oracle_sha256"] != task.get("oracle", {}).get("sha256"):
        raise ProtocolError("oracle source hash mismatch")
    _validate_oracle_result(record["oracle"], task)
    if record["oracle"]["passed"] is not record["task_success"]:
        raise ProtocolError("task_success does not match oracle result")
    fixture = task.get("fixtures", {}).get(record["arm"], {})
    if record["fixture_tree_sha256"] != fixture.get("sha256"):
        raise ProtocolError("fixture tree hash mismatch")
    pre_map = _validated_digest_map(record["pre_digest_map"], "pre")
    post_map = _validated_digest_map(record["post_digest_map"], "post")
    if record["pre_digest"] != sha256_bytes(canonical_json(pre_map)):
        raise ProtocolError("pre digest does not match map")
    if record["post_digest"] != sha256_bytes(canonical_json(post_map)):
        raise ProtocolError("post digest does not match map")
    source_files = set(fixture.get("source_files", []))
    if not source_files <= set(pre_map) or set(pre_map) != set(post_map):
        raise ProtocolError("digest map does not cover locked source files")
    observed_changes = sorted(
        path for path in pre_map if pre_map[path] != post_map[path]
    )
    if sorted(record["changed_paths"]) != observed_changes:
        raise ProtocolError("changed paths do not match digest maps")
    if not all(
        isinstance(record[key], str)
        for key in ("stdout", "stderr")
    ):
        raise ProtocolError("invalid raw attempt artifact")

def provider_identity_complete(provider: Mapping[str, Any] | None) -> bool:
    if not isinstance(provider, Mapping):
        return False
    required = ("provider", "model", "revision", "key_fingerprint", "training_cutoff_attestation", "network_denied_attestation")
    return all(isinstance(provider.get(key), str) and bool(provider.get(key)) and not str(provider.get(key)).startswith("UNSET") for key in required)


def unmeasured_report(reason: str = PROVIDER_IDENTITY_INCOMPLETE) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION, "status": "UNMEASURED",
        "terminal_reason": reason, "passed": False, "metrics": None,
        "preregistration_root_sha256": PREREGISTRATION_ROOT_SHA256,
        "denominators": {"pairs": 0, "measured_pairs": 0, "arm_attempts": 0},
        "claim_eligible": False,
    }

def paired_bootstrap(
    successes: Mapping[str, Iterable[float] | tuple[float, float]],
    *,
    strata: Mapping[str, str] | None = None,
    seed: int = 20260813,
    replicates: int = BOOTSTRAP_REPLICATES,
) -> dict[str, float | int]:
    """Resample task-level paired differences within every locked stratum."""
    import random

    if replicates <= 0:
        raise ProtocolError("bootstrap replicates must be positive")
    task_estimates: dict[str, float] = {}
    for task_id, values in successes.items():
        current = (
            [float(values[0]) - float(values[1])]
            if isinstance(values, tuple) and len(values) == 2
            else [float(value) for value in values]
        )
        if current:
            task_estimates[task_id] = sum(current) / len(current)
    if not task_estimates:
        return {
            "estimate_pp": 0.0, "lower95_pp": 0.0, "upper95_pp": 0.0,
            "replicates": replicates,
        }
    grouped: dict[str, list[float]] = {}
    for task_id, estimate in task_estimates.items():
        stratum = strata.get(task_id) if strata is not None else "all"
        if not isinstance(stratum, str) or not stratum:
            raise ProtocolError(f"missing bootstrap stratum: {task_id}")
        grouped.setdefault(stratum, []).append(estimate)
    rng = random.Random(seed)
    draws = []
    for _ in range(replicates):
        sampled = [
            rng.choice(group)
            for group in grouped.values()
            for _ in range(len(group))
        ]
        draws.append(sum(sampled) / len(sampled))
    draws.sort()
    estimate = sum(task_estimates.values()) / len(task_estimates)
    return {
        "estimate_pp": estimate * 100.0,
        "lower95_pp": draws[int(replicates * 0.025)] * 100.0,
        "upper95_pp": draws[max(0, int(replicates * 0.975) - 1)] * 100.0,
        "replicates": replicates,
    }

def _ratio_bootstrap(
    groups: Mapping[str, Iterable[float]],
    *,
    strata: Mapping[str, str],
    seed: int = 20260813,
    replicates: int = BOOTSTRAP_REPLICATES,
) -> dict[str, float | int | None]:
    import random
    from statistics import median

    if replicates <= 0:
        raise ProtocolError("bootstrap replicates must be positive")
    task_estimates = {
        task_id: median(current)
        for task_id, values in groups.items()
        if (current := [float(value) for value in values])
    }
    if not task_estimates:
        return {
            "estimate": None, "lower95": None, "upper95": None,
            "replicates": replicates,
        }
    grouped: dict[str, list[float]] = {}
    for task_id, estimate in task_estimates.items():
        stratum = strata.get(task_id)
        if not isinstance(stratum, str) or not stratum:
            raise ProtocolError(f"missing bootstrap stratum: {task_id}")
        grouped.setdefault(stratum, []).append(estimate)
    rng = random.Random(seed)
    draws = []
    for _ in range(replicates):
        sampled = [
            rng.choice(group)
            for group in grouped.values()
            for _ in range(len(group))
        ]
        draws.append(median(sampled))
    draws.sort()
    return {
        "estimate": median(task_estimates.values()),
        "lower95": draws[int(replicates * 0.025)],
        "upper95": draws[max(0, int(replicates * 0.975) - 1)],
        "replicates": replicates,
    }

def report_from_attempts(
    attempts: Iterable[Mapping[str, Any]],
    protocol: Mapping[str, Any],
    tasks: Mapping[str, Any],
) -> dict[str, Any]:
    if not _documents_match_preregistration_root(protocol, tasks):
        return {
            "schema_version": SCHEMA_VERSION, "status": "INVALID",
            "terminal_reason": "INVALID_PROTOCOL_DEVIATION", "passed": False,
            "metrics": None, "claim_eligible": False,
        }
    records = list(attempts)
    task_by_id = {task["id"]: task for task in tasks.get("tasks", [])}
    schedule = {
        entry["pair_id"]: dict(entry, schedule_index=index)
        for index, entry in enumerate(tasks.get("schedule", []))
    }
    seen: set[tuple[str, str]] = set()
    pair_records: dict[str, dict[str, Mapping[str, Any]]] = {}
    provider_attestations: dict[str, Mapping[str, Any]] = {}
    try:
        for record in records:
            task = task_by_id[record["task_id"]]
            entry = schedule[record["pair_id"]]
            validate_attempt_record(record, protocol, task, entry)
            key = (record["pair_id"], record["arm"])
            if key in seen:
                raise ProtocolError("duplicate arm attempt")
            seen.add(key)
            pair_records.setdefault(record["pair_id"], {})[record["arm"]] = record
            attestation = record["provider_attestation"]
            provider_attestations[
                sha256_bytes(canonical_json(attestation))
            ] = attestation
    except (KeyError, TypeError, ProtocolError) as exc:
        return {
            "schema_version": SCHEMA_VERSION, "status": "INVALID",
            "terminal_reason": "INVALID_ATTEMPT_RECORD", "passed": False,
            "metrics": None, "error": str(exc), "claim_eligible": False,
        }
    if (
        len(records) != PAIR_COUNT * 2
        or len(pair_records) != PAIR_COUNT
        or any(set(pair) != {"merlo", "python"} for pair in pair_records.values())
    ):
        return {
            "schema_version": SCHEMA_VERSION, "status": "INVALID",
            "terminal_reason": "INCOMPLETE_DENOMINATOR", "passed": False,
            "metrics": None, "claim_eligible": False,
        }
    if len(provider_attestations) != 1:
        return {
            "schema_version": SCHEMA_VERSION, "status": "INVALID",
            "terminal_reason": "PROVIDER_ATTESTATION_MISMATCH", "passed": False,
            "metrics": None, "claim_eligible": False,
        }
    success_groups: dict[str, list[float]] = {}
    token_groups: dict[str, list[float]] = {}
    wall_groups: dict[str, list[float]] = {}
    regressions = 0
    out_of_scope = 0
    for pair in pair_records.values():
        merlo, python = pair["merlo"], pair["python"]
        task_id = merlo["task_id"]
        success_groups.setdefault(task_id, []).append(
            float(merlo["task_success"]) - float(python["task_success"])
        )
        token_groups.setdefault(task_id, []).append(
            merlo["total_tokens"] / python["total_tokens"]
        )
        wall_groups.setdefault(task_id, []).append(
            merlo["wall_time_ms"] / python["wall_time_ms"]
        )
        regressions += merlo["regression_count"] + python["regression_count"]
        out_of_scope += (
            merlo["irrelevant_edit_count"] + python["irrelevant_edit_count"]
        )
    strata = {
        task_id: str(task["stratum"])
        for task_id, task in task_by_id.items()
    }
    bootstrap = protocol.get("metrics", {})
    bootstrap_seed = bootstrap.get("bootstrap_seed")
    bootstrap_replicates = bootstrap.get("bootstrap_replicates")
    if (
        not isinstance(bootstrap_seed, int)
        or not isinstance(bootstrap_replicates, int)
        or bootstrap_replicates <= 0
    ):
        return {
            "schema_version": SCHEMA_VERSION, "status": "INVALID",
            "terminal_reason": "INVALID_BOOTSTRAP_CONFIG", "passed": False,
            "metrics": None, "claim_eligible": False,
        }
    success_ci = paired_bootstrap(
        success_groups,
        strata=strata,
        seed=bootstrap_seed,
        replicates=bootstrap_replicates,
    )
    token_ci = _ratio_bootstrap(
        token_groups,
        strata=strata,
        seed=bootstrap_seed,
        replicates=bootstrap_replicates,
    )
    wall_ci = _ratio_bootstrap(
        wall_groups,
        strata=strata,
        seed=bootstrap_seed,
        replicates=bootstrap_replicates,
    )
    thresholds = protocol.get("eligibility_gate", {})
    required_thresholds = (
        "success_difference_pp_at_least", "success_lower95_pp_gt",
        "token_or_wall_upper95_ratio_lt", "max_regressions",
        "max_out_of_scope_edits",
    )
    if any(not isinstance(thresholds.get(key), (int, float)) for key in required_thresholds):
        return {
            "schema_version": SCHEMA_VERSION, "status": "INVALID",
            "terminal_reason": "INVALID_DECISION_GATE", "passed": False,
            "metrics": None, "claim_eligible": False,
        }
    efficiency_limit = float(thresholds["token_or_wall_upper95_ratio_lt"])
    gate = (
        success_ci["estimate_pp"]
        >= float(thresholds["success_difference_pp_at_least"])
        and success_ci["lower95_pp"]
        > float(thresholds["success_lower95_pp_gt"])
        and (
            token_ci["upper95"] is not None
            and token_ci["upper95"] < efficiency_limit
            or wall_ci["upper95"] is not None
            and wall_ci["upper95"] < efficiency_limit
        )
        and regressions <= int(thresholds["max_regressions"])
        and out_of_scope <= int(thresholds["max_out_of_scope_edits"])
    )
    if gate:
        decision = "ELIGIBLE_RESTRICTED_ADVANTAGE"
    elif success_ci["upper95_pp"] <= 0:
        decision = "MEASURED_NO_ADVANTAGE"
    else:
        decision = "MEASURED_INCONCLUSIVE"
    return {
        "schema_version": SCHEMA_VERSION, "status": "MEASURED",
        "terminal_reason": "completed", "passed": bool(gate),
        "claim_eligible": bool(gate), "decision": decision,
        "preregistration_root_sha256": PREREGISTRATION_ROOT_SHA256,
        "provider_attestation": next(iter(provider_attestations.values())),
        "metrics": {
            "success_difference": success_ci,
            "median_token_ratio": token_ci,
            "median_wall_time_ratio": wall_ci,
            "regressions": regressions,
            "out_of_scope_edits": out_of_scope,
        },
        "denominators": {
            "pairs": len(pair_records), "measured_pairs": len(pair_records),
            "arm_attempts": len(records),
        },
    }

def run(*, root: str | Path | None = None, provider: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate locks and return an unmeasured report without contacting providers."""
    validation = validate_protocol(root)
    if not validation.valid:
        return {"schema_version": SCHEMA_VERSION, "status": "INVALID", "terminal_reason": "INVALID_PROTOCOL_DEVIATION", "passed": False, "errors": list(validation.errors), "claim_eligible": False}
    if not provider_identity_complete(provider):
        return unmeasured_report()
    return unmeasured_report("PREREGISTERED_UNRUN")


__all__ = ["ProtocolError", "Validation", "canonical_json", "normalize_prompt", "paired_bootstrap", "provider_identity_complete", "render_prompt", "report_from_attempts", "run", "sha256_bytes", "sha256_file", "tree_digest", "unmeasured_report", "validate_attempt_record", "validate_protocol"]

def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Validate/run the preregistered Merlo/Python A/B")
    parser.add_argument("--root", default=None)
    parser.add_argument("--provider-config", default=None)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    provider: Mapping[str, Any] | None = None
    if args.provider_config:
        provider = _load_json(Path(args.provider_config))
    report = run(root=args.root, provider=provider)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ProtocolError(f"refusing to overwrite existing report: {destination}")
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(canonical_json(report))
    os.replace(temporary, destination)
    return 0 if report.get("status") == "MEASURED" and report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
