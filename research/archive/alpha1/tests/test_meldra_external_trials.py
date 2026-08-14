from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from research.archive.alpha1.merlo.external_trials import (
    EXTERNAL_TRIAL_MANIFEST_FILENAME,
    ExternalTrialObservation,
    ExternalTrialsReport,
    run_external_trials,
)


ROOT = Path(__file__).resolve().parents[1]


def _observation(**overrides: object) -> ExternalTrialObservation:
    values = {
        "task_id": "project:task",
        "project": "project",
        "operation": "rename",
        "old_locator": "package.old",
        "new_locator": "package.new",
        "target_public": False,
        "plan_ready": True,
        "apply_succeeded": True,
        "collection_guard": True,
        "passed_count_guard": True,
        "public_api_guard": True,
        "acceptance_errors": (),
        "restoration_succeeded": True,
        "source_unchanged": True,
        "infrastructure_errors": (),
        "blocked_reasons": (),
        "changed_files": ("package.py",),
        "status": "PASSED",
    }
    values.update(overrides)
    return ExternalTrialObservation(**values)


def test_report_preserves_guard_denominators_and_parallel_provenance():
    observations = (
        _observation(),
        _observation(
            task_id="project:move",
            operation="move",
            new_locator="other.old",
            collection_guard=False,
            status="FAILED",
            acceptance_errors=("pytest_collection:mismatch",),
        ),
        _observation(
            task_id="project:signature",
            operation="change_signature",
            new_locator="package.old",
        ),
    )

    payload = ExternalTrialsReport(
        observations,
        "manifest-digest",
        "protocol-digest",
        4,
    ).to_dict()

    assert payload["execution"] == {
        "workers": 4,
        "mode": "process_pool",
        "isolated_workspace_per_trial": True,
    }
    assert payload["operations"]["move"]["trials"] == 1
    assert payload["operations"]["move"]["collection_guard_passed"] == 0
    assert payload["operations"]["move"]["passed_count_guard_passed"] == 1
    assert payload["operations"]["move"]["public_api_guard_passed"] == 1
    assert payload["source_restoration_failures"] == 0


def test_frozen_external_trial_artifacts_are_complete_and_linked():
    manifest_path = ROOT / "tools" / "benchmarks" / "merlo" / "benchmarks" / EXTERNAL_TRIAL_MANIFEST_FILENAME
    report_path = ROOT / "tools" / "benchmarks" / "merlo" / "benchmarks" / "meldra_external_trials.json"
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert manifest["operation_counts"] == {
        "change_signature": 30,
        "move": 30,
        "rename": 30,
    }
    assert len(manifest["targets"]) == 90
    assert len({item["task"]["id"] for item in manifest["targets"]}) == 90
    assert {item["id"] for item in manifest["projects"]} == {
        "boltons",
        "click",
        "pluggy",
    }
    assert all(item["metadata"]["revision"] for item in manifest["projects"])
    assert report["manifest_sha256"] == hashlib.sha256(manifest_raw).hexdigest()
    assert report["execution"]["workers"] == 8
    assert report["statistical_units"]["external_trials"] == 90
    assert report["statistical_units"]["trials_per_operation"] == 30

    observations = report["observations"]
    assert Counter(item["operation"] for item in observations) == {
        "change_signature": 30,
        "move": 30,
        "rename": 30,
    }
    assert all(isinstance(item["collection_guard"], bool) for item in observations)
    assert all(isinstance(item["public_api_guard"], bool) for item in observations)
    assert all(item["restoration_succeeded"] for item in observations)
    assert all(item["source_unchanged"] is True for item in observations)
    assert all(not item["infrastructure_errors"] for item in observations)


def test_external_trial_worker_count_must_be_positive():
    with pytest.raises(ValueError, match="workers must be positive"):
        run_external_trials(ROOT, workers=0)
