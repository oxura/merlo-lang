from __future__ import annotations

import json
from dataclasses import replace

import pytest

from merlo.external_bench import (
    ExternalBenchmarkManifest,
    ExternalProject,
    ExternalTaskSpec,
)
from merlo.external_change_ground_truth import (
    ChangeGroundTruth,
    ChangeReviewLabel,
    ChangeReviewQueue,
    build_change_review_queue,
    validate_ground_truth,
)


def _manifest() -> ExternalBenchmarkManifest:
    project = ExternalProject(
        "project",
        "/tmp/project",
        "fixture",
        metadata=(("revision", "abc123"),),
    )
    tasks = []
    for operation in ("rename", "move", "change_signature"):
        for index in range(35):
            payload = {
                "rename": f"renamed_{index}",
                "move": f"module_{index}",
                "change_signature": f"(value, optional_{index}=None)",
            }[operation]
            tasks.append(
                ExternalTaskSpec.safe(
                    id=f"{operation}:{index}",
                    project=project.id,
                    operation=operation,
                    target=f"entity:{operation}:{index}",
                    payload=payload,
                    label_source="planner_preflight",
                    oracle="non_human_candidate",
                    metadata=(("target_revision", f"rev-{index}"),),
                )
            )
    return ExternalBenchmarkManifest((project,), tuple(tasks), seed=17)


def test_change_review_queue_selects_independent_30x3_without_labels():
    queue = build_change_review_queue((_manifest(),), seed=20260810)
    repeated = build_change_review_queue((_manifest(),), seed=20260810)

    assert queue == repeated
    assert queue.counts == {
        "rename": 30,
        "move": 30,
        "change_signature": 30,
    }
    assert queue.missing == {
        "rename": 0,
        "move": 0,
        "change_signature": 0,
    }
    assert queue.ready_for_adjudication_target is True
    assert all(
        item.to_dict()["expected_safe"] is None for item in queue.candidates
    )
    assert all(item.project_revision == "abc123" for item in queue.candidates)
    payload = queue.to_dict()
    assert ChangeReviewQueue.from_dict(payload) == queue
    assert json.loads(queue.to_json())["dataset_digest"] == queue.dataset_digest


def test_queue_digest_rejects_candidate_or_policy_tampering():
    queue = build_change_review_queue((_manifest(),))
    payload = queue.to_dict()
    payload["candidates"][0]["payload"] = "tampered"

    with pytest.raises(ValueError, match="digest mismatch"):
        ChangeReviewQueue.from_dict(payload)
    with pytest.raises(ValueError, match="must not carry inherited safety labels"):
        ChangeReviewQueue.from_dict(
            {
                **queue.to_dict(),
                "dataset_digest": None,
                "candidates": [
                    {**queue.to_dict()["candidates"][0], "expected_safe": True}
                ],
            }
        )


def test_ground_truth_requires_independent_reviewer_and_evidence():
    queue = build_change_review_queue((_manifest(),))
    candidate = queue.candidates[0]
    first = ChangeReviewLabel(
        candidate.id,
        "reviewer-a",
        "SAFE",
        "Declared contract and behavior were preserved.",
        ("tests:sha256-a", "api:sha256-b"),
    )
    second = replace(first, reviewer_id="reviewer-b")
    truth = ChangeGroundTruth(queue.dataset_digest, (second, first))

    validate_ground_truth(queue, truth)
    assert truth.agreement() == {
        "double_reviewed": 1,
        "agreements": 1,
        "rate": 1.0,
    }
    with pytest.raises(ValueError, match="at least one evidence"):
        ChangeReviewLabel(
            candidate.id,
            "reviewer-c",
            "SAFE",
            "No evidence is not acceptable.",
            (),
        )
    with pytest.raises(ValueError, match="another review queue"):
        validate_ground_truth(
            queue, ChangeGroundTruth("different", (first,))
        )


def test_incomplete_real_candidate_pool_reports_shortage_instead_of_padding():
    manifest = _manifest()
    reduced = ExternalBenchmarkManifest(
        manifest.projects,
        tuple(
            task
            for task in manifest.tasks
            if task.operation == "rename" and task.id.endswith(":0")
        ),
        seed=manifest.seed,
    )
    queue = build_change_review_queue((reduced,))

    assert queue.counts == {
        "rename": 1,
        "move": 0,
        "change_signature": 0,
    }
    assert queue.missing == {
        "rename": 29,
        "move": 30,
        "change_signature": 30,
    }
    assert queue.ready_for_adjudication_target is False
