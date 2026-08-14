"""Evidence-backed separation of Python, ChangeIR, and external identity claims."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .identity_ground_truth import IdentityReviewQueue
from .stage04e_protocol import assert_stage04e_protocol


IDENTITY_CLAIMS_SCHEMA_VERSION = 1
IDENTITY_CLAIMS_FILENAME = "meldra_identity_claims.json"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_identity_claims_report(
    root: str | Path = ".",
) -> dict[str, Any]:
    root_path = Path(root)
    protocol = assert_stage04e_protocol(root_path)
    benchmark_root = root_path / "benchmarks"
    summary_path = benchmark_root / "meldra_git_identity_summary.json"
    audit_path = benchmark_root / "meldra_identity_manual_audit.json"
    queue_path = benchmark_root / "meldra_identity_review_queue.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    queue_payload = json.loads(queue_path.read_text(encoding="utf-8"))
    queue = IdentityReviewQueue.from_dict(queue_payload)
    latest = summary["latest_first_parent_sample"]
    manual_precision = audit[
        "predicted_changed_precision_on_adjudicated_predictions"
    ]
    queued = len(queue.candidates)
    target = queue.target
    return {
        "schema_version": IDENTITY_CLAIMS_SCHEMA_VERSION,
        "kind": "MeldraSeparatedIdentityClaims",
        "protocol_sha256": protocol.protocol_sha256,
        "inputs": {
            "git_summary_sha256": _digest(summary_path),
            "manual_audit_sha256": _digest(audit_path),
            "review_queue_sha256": _digest(queue_path),
        },
        "python_identity": {
            "guarantee": (
                "Exact continuity is limited to unchanged semantic addresses; "
                "heuristic rename, move, split, and merge recovery is Probable "
                "or Ambiguous and receives a fresh EntityId."
            ),
            "all_link_git_proxy": latest["all_links"],
            "changed_only_git_proxy": latest["changed_only"],
            "manual_changed_prediction_precision": manual_precision,
            "manual_changed_recall": audit["manual_recall"],
            "claim_status": "NOT_ESTABLISHED",
            "reason": (
                "The changed-only manually adjudicated sample contains one "
                "verified false assignment (4/5 precision), has no recall "
                "denominator, and is too small for a population claim."
            ),
        },
        "changeir_identity": {
            "guarantee": (
                "Identity continuity is Exact only for an explicit ChangeIR "
                "IdentityHint applied by the transactional evolution engine and "
                "validated against the post-change world."
            ),
            "scope": [
                "source-preserving RenameSymbol",
                "source-preserving MoveSymbol",
                "source-preserving ChangeSignature",
            ],
            "non_guarantees": [
                "arbitrary external text edits",
                "Git rename/copy similarity",
                "heuristic content similarity",
                "split or merge hypotheses without reviewed provenance",
            ],
            "claim_status": "SCOPED_GUARANTEE",
        },
        "external_edit_identity": {
            "policy": (
                "External edits never inherit identity from a Probable or "
                "Ambiguous match automatically. They require explicit reviewed "
                "provenance before identity inheritance."
            ),
            "exact_auto_inheritance": [
                "unchanged semantic address",
                "explicit ChangeIR provenance",
            ],
            "review_required": [
                "probable rename",
                "probable move",
                "split hypothesis",
                "merge hypothesis",
                "Git proxy disagreement",
            ],
            "claim_status": "CONSERVATIVE_REVIEW_POLICY",
        },
        "provenance_policy": {
            "strict": True,
            "generated_git_proxy_is_ground_truth": False,
            "independent_human_review_required": True,
            "probable_is_exact": False,
            "ambiguous_inherits_identity": False,
        },
        "review_workflow": {
            "target": target,
            "queued_candidates": queued,
            "independently_adjudicated": 0,
            "remaining_to_target": target,
            "queue_fill_rate": round(queued / target, 6) if target else None,
            "status": "INCOMPLETE",
            "note": (
                "Six prior disagreements are queued but deliberately remain "
                "unverified; no generated or Git-proxy label is counted as an "
                "independent human adjudication."
            ),
        },
        "decision": "NO_BROAD_PYTHON_IDENTITY_CLAIM",
    }


__all__ = [
    "IDENTITY_CLAIMS_FILENAME",
    "IDENTITY_CLAIMS_SCHEMA_VERSION",
    "build_identity_claims_report",
]
