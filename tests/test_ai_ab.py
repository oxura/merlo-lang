from __future__ import annotations
import json
import shutil

from pathlib import Path
import subprocess
import sys

import pytest

from merlo.ai_ab import (
    PROVIDER_IDENTITY_INCOMPLETE,
    ProtocolError,
    canonical_json,
    paired_bootstrap,
    provider_identity_complete,
    report_from_attempts,
    render_prompt,
    sha256_bytes,
    unmeasured_report,
    validate_attempt_record,
    validate_protocol,
)

ROOT = Path(__file__).parents[1]


def test_public_manifest_has_all_distinct_mirrors_and_pairs() -> None:
    result = validate_protocol(ROOT)
    assert result.valid, result.errors
    assert result.task_count == 30
    assert result.pair_count == 90
    assert result.denominators["arm_attempts"] == 180


def test_normalized_prompt_parity_is_locked() -> None:
    tasks = json.loads((ROOT / "benchmarks/ai-ab-v1/tasks.json").read_text())
    task = tasks["tasks"][0]
    merlo = render_prompt(task, "Merlo", "/merlo/workspace", "merlo test")
    python = render_prompt(task, "Python 3.12", "/python/workspace", "python -m pytest -q")
    assert merlo.replace("Merlo", "{{LANGUAGE}}").replace("/merlo/workspace", "{{WORKSPACE}}").replace("merlo test", "{{TEST_COMMAND}}") == python.replace("Python 3.12", "{{LANGUAGE}}").replace("/python/workspace", "{{WORKSPACE}}").replace("python -m pytest -q", "{{TEST_COMMAND}}")


def test_fixture_tamper_is_rejected(tmp_path: Path) -> None:
    source = ROOT / "benchmarks/ai-ab-v1"
    target = tmp_path / "benchmarks/ai-ab-v1"
    shutil.copytree(source, target)
    path = target / "fixtures/d01-json-sort/python/main.py"
    path.write_text(path.read_text() + "\n# tampered\n")
    result = validate_protocol(tmp_path)
    assert not result.valid
    assert any("fixture hash mismatch" in error for error in result.errors)


def test_provider_identity_is_never_inferred() -> None:
    assert not provider_identity_complete({"provider": "x", "model": "y", "revision": None, "key_fingerprint": None})
    report = unmeasured_report()
    assert report["status"] == "UNMEASURED"
    assert report["terminal_reason"] == PROVIDER_IDENTITY_INCOMPLETE
    assert report["metrics"] is None


def test_paired_bootstrap_is_seeded_and_keeps_task_pairs() -> None:
    values = {"d01": (1, 0), "d02": (0, 1), "r01": (1, 1)}
    first = paired_bootstrap(values, replicates=100)
    second = paired_bootstrap(values, replicates=100)
    assert first == second
    assert first["replicates"] == 100


def _oracle_report(task: dict[str, object], passed: bool) -> dict[str, object]:
    spec = task["language_neutral_spec"]
    case_count = len(spec["cases"])
    report: dict[str, object] = {
        "case_id": task["id"],
        "passed": passed,
    }
    if task["stratum"] == "multi_module_api_migration":
        report["migration_passed"] = [passed] * case_count
        report["unaffected_passed"] = [True] * len(spec["unaffected_cases"])
    else:
        case_passes = [passed] * case_count
        if task["stratum"] == "regression_repair":
            case_passes = [passed] + [True] * (case_count - 1)
            report["defect_case_passed"] = passed
            report["unaffected_cases_passed"] = True
        report["cases"] = [{"passed": value} for value in case_passes]
    return report


def _digest_evidence(
    task: dict[str, object],
    arm: str,
) -> tuple[dict[str, str], str]:
    digest_map = {
        path: "a" * 64
        for path in task["fixtures"][arm]["source_files"]
    }
    return digest_map, sha256_bytes(canonical_json(digest_map))


def test_attempt_transcript_hash_and_absence_are_enforced() -> None:
    protocol = json.loads((ROOT / "benchmarks/ai-ab-v1/protocol.json").read_text())
    task = json.loads((ROOT / "benchmarks/ai-ab-v1/tasks.json").read_text())["tasks"][0]
    transcript = [{"kind": "request", "body": "redacted"}]
    schedule_entry = dict(
        json.loads((ROOT / "benchmarks/ai-ab-v1/tasks.json").read_text())["schedule"][0],
        schedule_index=0,
    )
    attestation = {"provider": "test", "model": "test", "revision": "rev", "key_fingerprint": "key",
                   "training_cutoff_attestation": "before-publication", "network_denied_attestation": "denied"}
    digest_map, digest = _digest_evidence(task, "merlo")
    record = {
        "task_id": task["id"], "replicate": 1, "arm": "merlo",
        "pair_id": schedule_entry["pair_id"], "schedule_index": 0,
        "arm_order": schedule_entry["arm_order"],
        "protocol_sha256": protocol["protocol_sha256"],
        "task_sha256": task["task_sha256"],
        "transcript": transcript,
        "transcript_sha256": sha256_bytes(canonical_json(transcript)),
        "fixture_tree_sha256": task["fixtures"]["merlo"]["sha256"],
        "pre_digest": digest, "post_digest": digest,
        "pre_digest_map": digest_map, "post_digest_map": digest_map,
        "oracle_sha256": task["oracle"]["sha256"],
        "oracle": _oracle_report(task, True), "stdout": "", "stderr": "",
        "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
        "iterations": 1, "tool_calls": 1, "provider_time_ms": 1,
        "wall_time_ms": 1, "terminal_reason": "completed",
        "changed_paths": [], "irrelevant_edit_count": 0,
        "regression_count": 0, "task_success": True,
        "provider_attestation": attestation,
        "contamination_attestation": protocol["contamination_policy"],
    }
    validate_attempt_record(record, protocol, task, schedule_entry)
    oracle_mismatch = dict(record, oracle=_oracle_report(task, False))
    with pytest.raises(ProtocolError, match="does not match oracle"):
        validate_attempt_record(oracle_mismatch, protocol, task, schedule_entry)
    missing_ratio = dict(record, wall_time_ms=0)
    with pytest.raises(ProtocolError, match="ratio denominator"):
        validate_attempt_record(missing_ratio, protocol, task, schedule_entry)
    incomplete_oracle = dict(record, oracle={"passed": True})
    with pytest.raises(ProtocolError, match="identity or aggregate"):
        validate_attempt_record(incomplete_oracle, protocol, task, schedule_entry)
    forged_digest = dict(record, post_digest="b" * 64)
    with pytest.raises(ProtocolError, match="post digest"):
        validate_attempt_record(forged_digest, protocol, task, schedule_entry)
    record.pop("transcript")
    with pytest.raises(ProtocolError, match="absent transcript"):
        validate_attempt_record(record, protocol, task)

def test_strata_baselines_execute_and_preserve_unaffected_contracts() -> None:
    tasks = json.loads((ROOT / "benchmarks/ai-ab-v1/tasks.json").read_text())["tasks"]
    for task in tasks:
        oracle = ROOT / "benchmarks/ai-ab-v1" / task["oracle"]["path"]
        fixture_root = ROOT / "benchmarks/ai-ab-v1" / "fixtures" / task["id"]
        if task["stratum"] == "deterministic_cli_data":
            continue
        result = subprocess.run(
            [sys.executable, str(oracle), "--workspace", str(fixture_root), "--arm", "python"],
            check=True, capture_output=True, text=True,
        )
        report = json.loads(result.stdout)
        assert report["passed"] is False
        if task["stratum"] == "multi_module_api_migration":
            assert all(report["unaffected_passed"])
            requests = [json.dumps(case[0], sort_keys=True) for case in task["language_neutral_spec"]["cases"]]
            untouched = [json.dumps(case[0], sort_keys=True) for case in task["language_neutral_spec"]["unaffected_cases"]]
            assert not set(requests) & set(untouched)
        else:
            assert report["defect_case_passed"] is False
            assert report["unaffected_cases_passed"] is True


def test_whitespace_only_edit_cannot_change_incomplete_baseline() -> None:
    source = ROOT / "benchmarks/ai-ab-v1/fixtures/d01-json-sort/python/main.py"
    before = source.read_text()
    after = before + "\n"
    assert "UNIMPLEMENTED" in before
    assert "UNIMPLEMENTED" in after


def test_stratified_bootstrap_preserves_each_stratum_denominator() -> None:
    values = {
        "d1": [1.0], "d2": [1.0],
        "m1": [0.0], "m2": [0.0],
        "r1": [-1.0], "r2": [-1.0],
    }
    strata = {
        "d1": "data", "d2": "data",
        "m1": "migration", "m2": "migration",
        "r1": "regression", "r2": "regression",
    }

    result = paired_bootstrap(values, strata=strata, replicates=100)

    assert result["estimate_pp"] == 0.0
    assert result["lower95_pp"] == 0.0
    assert result["upper95_pp"] == 0.0


def _complete_attempts(
    protocol: dict[str, object],
    tasks_document: dict[str, object],
) -> list[dict[str, object]]:
    task_by_id = {task["id"]: task for task in tasks_document["tasks"]}
    attestation = {
        "provider": "test-provider",
        "model": "test-model",
        "revision": "immutable-revision",
        "key_fingerprint": "sha256:key",
        "training_cutoff_attestation": "before-publication",
        "network_denied_attestation": "denied",
    }
    attempts = []
    for schedule_index, raw_entry in enumerate(tasks_document["schedule"]):
        entry = dict(raw_entry, schedule_index=schedule_index)
        task = task_by_id[entry["task_id"]]
        for arm in entry["arm_order"]:
            transcript = [{"kind": "request", "body": f"{entry['pair_id']}:{arm}"}]
            merlo = arm == "merlo"
            digest_map, digest = _digest_evidence(task, arm)
            attempts.append({
                "task_id": task["id"],
                "replicate": entry["replicate"],
                "arm": arm,
                "pair_id": entry["pair_id"],
                "schedule_index": schedule_index,
                "arm_order": entry["arm_order"],
                "protocol_sha256": protocol["protocol_sha256"],
                "task_sha256": task["task_sha256"],
                "transcript": transcript,
                "transcript_sha256": sha256_bytes(canonical_json(transcript)),
                "fixture_tree_sha256": task["fixtures"][arm]["sha256"],
                "pre_digest": digest,
                "post_digest": digest,
                "pre_digest_map": digest_map,
                "post_digest_map": digest_map,
                "oracle_sha256": task["oracle"]["sha256"],
                "oracle": _oracle_report(task, merlo),
                "stdout": "",
                "stderr": "",
                "input_tokens": 20,
                "output_tokens": 20 if merlo else 60,
                "total_tokens": 40 if merlo else 80,
                "iterations": 1,
                "tool_calls": 1,
                "provider_time_ms": 1,
                "wall_time_ms": 40 if merlo else 80,
                "terminal_reason": "completed",
                "changed_paths": [],
                "irrelevant_edit_count": 0,
                "regression_count": 0,
                "task_success": merlo,
                "provider_attestation": attestation,
                "contamination_attestation": protocol["contamination_policy"],
            })
    return attempts


def test_complete_attempt_denominator_produces_registered_decision() -> None:
    protocol = json.loads((ROOT / "benchmarks/ai-ab-v1/protocol.json").read_text())
    tasks_document = json.loads((ROOT / "benchmarks/ai-ab-v1/tasks.json").read_text())
    attempts = _complete_attempts(protocol, tasks_document)

    report = report_from_attempts(attempts, protocol, tasks_document)

    assert report["status"] == "MEASURED"
    assert report["decision"] == "ELIGIBLE_RESTRICTED_ADVANTAGE"
    assert report["claim_eligible"] is True
    assert report["denominators"] == {
        "pairs": 90,
        "measured_pairs": 90,
        "arm_attempts": 180,
    }
    assert report["provider_attestation"]["revision"] == "immutable-revision"


def test_report_rejects_rehashed_protocol_deviation() -> None:
    protocol = json.loads((ROOT / "benchmarks/ai-ab-v1/protocol.json").read_text())
    tasks_document = json.loads(
        (ROOT / "benchmarks/ai-ab-v1/tasks.json").read_text()
    )
    protocol["eligibility_gate"]["success_difference_pp_at_least"] = -100
    unlocked = dict(protocol)
    unlocked.pop("protocol_sha256")
    protocol["protocol_sha256"] = sha256_bytes(canonical_json(unlocked))

    report = report_from_attempts(
        _complete_attempts(protocol, tasks_document),
        protocol,
        tasks_document,
    )

    assert report["status"] == "INVALID"
    assert report["terminal_reason"] == "INVALID_PROTOCOL_DEVIATION"


def test_duplicate_attempt_cannot_satisfy_locked_denominator() -> None:
    protocol = json.loads((ROOT / "benchmarks/ai-ab-v1/protocol.json").read_text())
    tasks_document = json.loads((ROOT / "benchmarks/ai-ab-v1/tasks.json").read_text())
    attempts = _complete_attempts(protocol, tasks_document)
    attempts[-1] = attempts[0]

    report = report_from_attempts(attempts, protocol, tasks_document)

    assert report["status"] == "INVALID"
    assert report["terminal_reason"] == "INVALID_ATTEMPT_RECORD"
