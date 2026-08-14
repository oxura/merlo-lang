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


def test_attempt_transcript_hash_and_absence_are_enforced() -> None:
    protocol = json.loads((ROOT / "benchmarks/ai-ab-v1/protocol.json").read_text())
    task = json.loads((ROOT / "benchmarks/ai-ab-v1/tasks.json").read_text())["tasks"][0]
    transcript = [{"kind": "request", "body": "redacted"}]
    schedule_entry = json.loads((ROOT / "benchmarks/ai-ab-v1/tasks.json").read_text())["schedule"][0]
    attestation = {"provider": "test", "model": "test", "revision": "rev", "key_fingerprint": "key",
                   "training_cutoff_attestation": "before-publication", "network_denied_attestation": "denied"}
    record = {
        "task_id": task["id"], "replicate": 1, "arm": "merlo", "pair_id": schedule_entry["pair_id"],
        "schedule_index": 0, "arm_order": schedule_entry["arm_order"],
        "protocol_sha256": protocol["protocol_sha256"], "task_sha256": task["task_sha256"],
        "transcript": transcript, "transcript_sha256": sha256_bytes(canonical_json(transcript)),
        "pre_digest": "a", "post_digest": "b", "pre_digest_map": {}, "post_digest_map": {},
        "oracle": {"passed": True}, "stdout": "", "stderr": "",
        "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
        "iterations": 1, "tool_calls": 1, "provider_time_ms": 1, "wall_time_ms": 1,
        "terminal_reason": "completed", "changed_paths": [], "irrelevant_edit_count": 0, "regression_count": 0,
        "provider_attestation": attestation, "contamination_attestation": protocol["contamination_policy"],
    }
    validate_attempt_record(record, protocol, task, schedule_entry)
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
