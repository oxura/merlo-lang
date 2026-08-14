from __future__ import annotations

import hashlib
import json
from pathlib import Path

from research.archive.alpha1.merlo.agent_experiment import (
    AGENT_EXPERIMENT_ABLATIONS,
    AGENT_EXPERIMENT_BUDGET,
    AGENT_EXPERIMENT_MANIFEST_FILENAME,
    run_agent_experiment,
)


ROOT = Path(__file__).resolve().parents[4]


def test_frozen_agent_manifest_uses_one_model_and_equal_budgets():
    path = ROOT / "tools" / "benchmarks" / "merlo" / "benchmarks" / AGENT_EXPERIMENT_MANIFEST_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["task_count"] == 90
    assert payload["provider"]["same_model_required"] is True
    assert payload["budget"] == AGENT_EXPERIMENT_BUDGET.to_dict()
    assert payload["arms"] == ["baseline", "meldra"]
    assert tuple(payload["ablation_arms"]) == AGENT_EXPERIMENT_ABLATIONS
    assert len({item["task_id"] for item in payload["tasks"]}) == 90
    assert {json.dumps(item["budget"], sort_keys=True) for item in payload["tasks"]} == {
        json.dumps(AGENT_EXPERIMENT_BUDGET.to_dict(), sort_keys=True)
    }


def test_missing_provider_is_explicitly_unmeasured_without_fake_scores():
    report = run_agent_experiment(ROOT, api_key=None).to_dict()

    assert report["evidence_level"] == "UNMEASURED_PROVIDER_UNAVAILABLE"
    assert report["decision"] == "UNMEASURED"
    assert report["paired"]["aggregates"]["baseline"]["measured_tasks"] == 0
    assert report["paired"]["aggregates"]["baseline"]["task_success_rate"] is None
    assert report["paired"]["aggregates"]["meldra"]["measured_tasks"] == 0
    assert report["paired"]["aggregates"]["meldra"]["task_success_rate"] is None
    assert all(item["status"] == "UNMEASURED" for item in report["ablations"])
    assert all(item["measured_tasks"] == 0 for item in report["ablations"])


def test_frozen_unmeasured_report_links_exact_manifest():
    manifest_path = ROOT / "tools" / "benchmarks" / "merlo" / "benchmarks" / AGENT_EXPERIMENT_MANIFEST_FILENAME
    report = json.loads(
        (ROOT / "tools" / "benchmarks" / "merlo" / "benchmarks" / "meldra_agent_experiment.json").read_text(
            encoding="utf-8"
        )
    )

    assert report["manifest_sha256"] == hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    assert report["constraints"]["same_model"] is True
    assert report["constraints"]["equal_budget"] is True
    assert report["constraints"]["equal_hardware"] is True
