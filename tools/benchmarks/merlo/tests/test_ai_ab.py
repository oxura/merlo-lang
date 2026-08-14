from __future__ import annotations
import json
import shutil

from pathlib import Path
import subprocess
import sys

import pytest

from tools.benchmarks.merlo.ai_ab import (
    _attempt_evidence_sha256,
    PROVIDER_IDENTITY_INCOMPLETE,
    PREREGISTRATION_ROOT_SHA256,
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
def test_calibration_manifest_is_disjoint_and_excluded() -> None:
    final = json.loads((ROOT / "benchmarks/ai-ab-v1/tasks.json").read_text())
    calibration = json.loads((ROOT / "benchmarks/ai-ab-v1/calibration/calibration.json").read_text())
    final_ids = {task["id"] for task in final["tasks"]}
    calibration_tasks = calibration["tasks"]
    assert len(calibration_tasks) == 6
    assert {task["id"] for task in calibration_tasks}.isdisjoint(final_ids)
    assert all(task["excluded_from_final_metrics"] is True and "outcome" not in task for task in calibration_tasks)
    assert all("def main" in (ROOT / "benchmarks/ai-ab-v1" / task["oracle"]["path"]).read_text() for task in calibration_tasks)


def _run_calibration_oracle(
    tmp_path: Path,
    calibration_id: str,
    evidence: dict[str, object],
) -> dict[str, object]:
    study = ROOT / "benchmarks/ai-ab-v1"
    manifest = json.loads(
        (study / "calibration/calibration.json").read_text(encoding="utf-8")
    )
    task = next(item for item in manifest["tasks"] if item["id"] == calibration_id)
    workspace = tmp_path / calibration_id
    workspace.mkdir()
    (workspace / "main.mlo").write_text("fn main() -> Int:\n    0\n")
    evidence_path = workspace / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    replacements = {
        "{workspace}": str(workspace),
        "{arm}": "merlo",
        "{evidence}": str(evidence_path),
        "{protocol}": str(study / "protocol.json"),
    }
    command = [
        replacements.get(argument, argument)
        for argument in task["oracle"]["run_command"]
    ]
    command[0] = sys.executable
    completed = subprocess.run(
        command,
        cwd=study,
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_tool_smoke_rejects_incomplete_or_failed_trace(tmp_path: Path) -> None:
    result = _run_calibration_oracle(tmp_path, "c01-tool-smoke", {
        "tool_trace": [
            {"tool": "shell", "success": True},
            {"tool": "read", "success": True, "path": "main.mlo"},
            {"tool": "search", "success": True},
            {"tool": "edit", "success": False, "path": "main.mlo"},
            {"tool": "test", "success": True},
        ],
    })

    assert result["status"] == "FAIL"
    assert result["checks"]["exact_common_tools"] is False


@pytest.mark.parametrize(
    "calibration_id,evidence",
    [
        (
            "c02-budget-boundary",
            {
                "input_tokens": 12_001,
                "output_tokens": 8_192,
                "iterations": 40,
                "tool_calls": 120,
                "wall_time_ms": 180_000,
                "boundary_field": "output_tokens",
                "terminal_reason": "token_budget",
            },
        ),
        (
            "c05-token-accounting",
            {
                "input_tokens": 12_001,
                "output_tokens": 1,
                "total_tokens": 12_002,
                "iterations": 1,
                "tool_calls": 1,
                "wall_time_ms": 1,
            },
        ),
    ],
)
def test_budget_calibration_rejects_over_limit_evidence(
    tmp_path: Path,
    calibration_id: str,
    evidence: dict[str, object],
) -> None:
    result = _run_calibration_oracle(tmp_path, calibration_id, evidence)

    assert result["status"] == "FAIL"
    assert result["checks"]["input_tokens_bounded"] is False


def test_trusted_attempt_digest_binds_terminal_and_raw_output() -> None:
    record = {
        "terminal_reason": "completed",
        "transcript_sha256": "a" * 64,
        "fixture_tree_sha256": "b" * 64,
        "stdout": "",
        "stderr": "",
        "provider_attestation": {"provider": "locked"},
        "contamination_attestation": {"network_retrieval": "disabled"},
    }
    digest = _attempt_evidence_sha256(record)

    assert _attempt_evidence_sha256({
        **record, "terminal_reason": "provider_unavailable",
    }) != digest
    assert _attempt_evidence_sha256({
        **record, "stdout": "forged",
    }) != digest


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
    assert not provider_identity_complete({"provider": "x", "model": "y", "revision": None, "credential_alias_hmac": None})
    report = unmeasured_report()
    assert report["status"] == "UNMEASURED"
    assert report["terminal_reason"] == PROVIDER_IDENTITY_INCOMPLETE
    assert report["metrics"] is None
    assert report["preregistration_root_sha256"] == PREREGISTRATION_ROOT_SHA256


def test_paired_bootstrap_is_seeded_and_keeps_task_pairs() -> None:
    values = {"d01": (1, 0), "d02": (0, 1), "r01": (1, 1)}
    first = paired_bootstrap(values, replicates=100)
    second = paired_bootstrap(values, replicates=100)
    assert first == second
    assert first["replicates"] == 100


def _case_evidence(outcomes: list[bool]) -> list[dict[str, object]]:
    return [
        {
            "case_id": index,
            "expected": {"value": index},
            "actual": {"value": index} if outcome else {"wrong": index},
            "outcome": outcome,
        }
        for index, outcome in enumerate(outcomes)
    ]


def _oracle_report(task: dict[str, object], passed: bool) -> dict[str, object]:
    spec = task["language_neutral_spec"]
    primary_outcomes = [passed] * len(spec["cases"])
    if task["stratum"] == "regression_repair":
        primary_outcomes = [passed] + [True] * (len(spec["cases"]) - 1)
    report: dict[str, object] = {
        "task_id": task["id"],
        "cases": _case_evidence(primary_outcomes),
    }
    all_outcomes = list(primary_outcomes)
    if task["stratum"] == "multi_module_api_migration":
        unaffected = [True] * len(spec["unaffected_cases"])
        report["unaffected_cases"] = _case_evidence(unaffected)
        all_outcomes.extend(unaffected)
    passed_count = sum(all_outcomes)
    report.update({
        "case_count": len(all_outcomes),
        "passed_count": passed_count,
        "failed_count": len(all_outcomes) - passed_count,
        "task_success": passed_count == len(all_outcomes),
    })
    return report


def _digest_evidence(
    task: dict[str, object],
    arm: str,
) -> tuple[dict[str, dict[str, object]], str]:
    fixture = task["fixtures"][arm]
    digest_map = {
        path: {
            "kind": "file",
            "content_sha256": "a" * 64,
            "executable": False,
            "symlink_target": None,
        }
        for path in fixture.get("workspace_files", fixture["source_files"])
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
    attestation = {"provider": "test", "model": "test", "revision": "rev", "credential_alias_hmac": "a" * 64,
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
        "trusted_oracle_attestation": {},
        "input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
        "iterations": 1, "tool_calls": 1, "provider_time_ms": 1,
        "wall_time_ms": 1, "terminal_reason": "completed",
        "changed_paths": [], "irrelevant_edit_count": 0,
        "regression_count": 0, "task_success": True,
        "provider_attestation": attestation,
        "contamination_attestation": protocol["contamination_policy"],
    }
    with pytest.raises(ProtocolError, match="trusted oracle authentication"):
        validate_attempt_record(record, protocol, task, schedule_entry)
    assert provider_identity_complete(attestation)
    assert not provider_identity_complete({
        **attestation,
        "credential_alias_hmac": "hmac:alias",
    })

def test_strata_baselines_emit_normalized_oracle_evidence() -> None:
    tasks = json.loads(
        (ROOT / "benchmarks/ai-ab-v1/tasks.json").read_text()
    )["tasks"]
    for task in tasks:
        oracle = ROOT / "benchmarks/ai-ab-v1" / task["oracle"]["path"]
        fixture_root = ROOT / "benchmarks/ai-ab-v1" / "fixtures" / task["id"]
        result = subprocess.run(
            [
                sys.executable, str(oracle), "--workspace", str(fixture_root),
                "--arm", "python",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(result.stdout)
        primary = report["cases"]
        unaffected = report.get("unaffected_cases", [])
        evidence = primary + unaffected
        assert report["task_id"] == task["id"]
        assert report["task_success"] is False
        assert report["case_count"] == len(evidence)
        assert report["passed_count"] == sum(
            case["outcome"] for case in evidence
        )
        assert report["failed_count"] == (
            report["case_count"] - report["passed_count"]
        )
        assert [case["case_id"] for case in primary] == list(
            range(len(primary))
        )
        assert all(
            case["outcome"] is (case["actual"] == case["expected"])
            for case in evidence
        )
        if task["stratum"] == "multi_module_api_migration":
            assert unaffected and all(case["outcome"] for case in unaffected)
            requests = {
                json.dumps(case[0], sort_keys=True)
                for case in task["language_neutral_spec"]["cases"]
            }
            untouched = {
                json.dumps(case[0], sort_keys=True)
                for case in task["language_neutral_spec"]["unaffected_cases"]
            }
            assert requests.isdisjoint(untouched)
        elif task["stratum"] == "regression_repair":
            assert primary[0]["outcome"] is False
            assert all(case["outcome"] for case in primary[1:])


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
        "credential_alias_hmac": "b" * 64,
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


def test_complete_attempts_remain_blocked_until_external_anchor() -> None:
    protocol = json.loads((ROOT / "benchmarks/ai-ab-v1/protocol.json").read_text())
    tasks_document = json.loads((ROOT / "benchmarks/ai-ab-v1/tasks.json").read_text())
    attempts = _complete_attempts(protocol, tasks_document)

    report = report_from_attempts(attempts, protocol, tasks_document)

    assert report["status"] == "INVALID"
    assert report["terminal_reason"] == "EXTERNAL_PREREGISTRATION_ANCHOR_INCOMPLETE"

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
    assert report["terminal_reason"] == "EXTERNAL_PREREGISTRATION_ANCHOR_INCOMPLETE"
