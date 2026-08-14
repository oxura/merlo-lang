from __future__ import annotations

import hashlib
import json
from pathlib import Path

from research.archive.historical_protocol.merlo.stage04e_decision import build_stage04e_decision


ROOT = Path(__file__).resolve().parents[4]


def test_frozen_go_gates_fail_on_observed_evidence_not_defaults():
    report = build_stage04e_decision(ROOT)
    gates = {item["id"]: item for item in report["gates"]}

    assert set(gates) == {
        "agent_value",
        "capability_safety",
        "engineering_integrity",
        "expressiveness",
        "interface_locality",
        "runtime_soundness",
    }
    assert all(item["passed"] is False for item in gates.values())
    assert gates["capability_safety"]["observed"] == {
        "attacks": 120,
        "violation_detection_recall": 0.8,
        "false_block_rate": 0.0,
        "false_safe": 24,
        "pre_materialization_detection_rate": 0.6,
        "runtime_escapes": 24,
    }
    assert gates["agent_value"]["observed"]["baseline_measured_tasks"] == 0
    assert gates["agent_value"]["observed"]["meldra_measured_tasks"] == 0
    assert gates["engineering_integrity"]["observed"][
        "external_success_rates"
    ] == {
        "change_signature": 1.0,
        "move": 0.466667,
        "rename": 0.966667,
    }


def test_decision_selects_semantic_layer_without_overclaiming_equivalence():
    report = json.loads(
        (ROOT / "benchmarks" / "meldra_stage04e_decision.json").read_text(
            encoding="utf-8"
        )
    )

    assert report["all_required_go_gates_pass"] is False
    assert report["language_alpha_decision"] == "NO_GO"
    assert report["selected_direction"] == "STRICT_SEMANTIC_LAYER"
    assert report["advance_language_alpha"] is False
    assert report["strict_python_equivalence"]["status"] == "INDETERMINATE"
    assert "agent productivity advantage" in report["do_not_claim"]
    assert "99.5% Python rename or move identity" in report["do_not_claim"]


def test_decision_artifact_is_linked_to_every_input_digest():
    report = json.loads(
        (ROOT / "benchmarks" / "meldra_stage04e_decision.json").read_text(
            encoding="utf-8"
        )
    )

    for name, expected in report["inputs"].items():
        assert expected == hashlib.sha256(
            (ROOT / "benchmarks" / name).read_bytes()
        ).hexdigest()
