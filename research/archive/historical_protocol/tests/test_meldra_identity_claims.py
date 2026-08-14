from __future__ import annotations

import hashlib
import json
from pathlib import Path

from research.archive.historical_protocol.merlo.identity_claims import build_identity_claims_report
from research.archive.historical_protocol.merlo.identity_ground_truth import (
    Adjudication,
    IdentityDecision,
    can_auto_inherit,
    may_inherit_identity,
)


ROOT = Path(__file__).resolve().parents[4]


def test_python_and_changeir_identity_claims_are_separate():
    report = build_identity_claims_report(ROOT)

    assert report["python_identity"]["claim_status"] == "NOT_ESTABLISHED"
    assert report["python_identity"]["manual_changed_prediction_precision"] == {
        "denominator": 5,
        "false_positive": 1,
        "numerator": 4,
        "true_positive": 4,
        "value": 0.8,
    }
    assert report["python_identity"]["manual_changed_recall"] is None
    assert report["changeir_identity"]["claim_status"] == "SCOPED_GUARANTEE"
    assert "arbitrary external text edits" in report["changeir_identity"][
        "non_guarantees"
    ]
    assert report["external_edit_identity"]["claim_status"] == (
        "CONSERVATIVE_REVIEW_POLICY"
    )


def test_strict_provenance_never_promotes_heuristics_to_exact():
    assert can_auto_inherit("Exact") is True
    assert can_auto_inherit("Probable") is False
    assert can_auto_inherit("Ambiguous") is False
    assert may_inherit_identity("Probable") is False
    reviewed = Adjudication(
        "candidate",
        IdentityDecision.SAME,
        "independent-reviewer",
        evidence=("Reviewed source before and after the change.",),
    )
    assert may_inherit_identity("Probable", adjudication=reviewed) is True


def test_600_label_workflow_remains_honestly_incomplete():
    report = json.loads(
        (ROOT / "benchmarks" / "meldra_identity_claims.json").read_text(
            encoding="utf-8"
        )
    )
    workflow = report["review_workflow"]

    assert workflow["target"] == 600
    assert workflow["queued_candidates"] == 6
    assert workflow["independently_adjudicated"] == 0
    assert workflow["remaining_to_target"] == 600
    assert workflow["status"] == "INCOMPLETE"
    assert report["provenance_policy"]["generated_git_proxy_is_ground_truth"] is False

    for name, digest in (
        ("meldra_git_identity_summary.json", "git_summary_sha256"),
        ("meldra_identity_manual_audit.json", "manual_audit_sha256"),
        ("meldra_identity_review_queue.json", "review_queue_sha256"),
    ):
        assert report["inputs"][digest] == hashlib.sha256(
            (ROOT / "benchmarks" / name).read_bytes()
        ).hexdigest()
