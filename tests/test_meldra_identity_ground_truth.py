from __future__ import annotations

import json

import pytest

from merlo.identity_ground_truth import (
    DEFAULT_REVIEW_TARGET,
    Adjudication,
    GroundTruthError,
    IdentityDecision,
    IdentityGroundTruthCorpus,
    IdentityLink,
    IdentityReviewQueue,
    InheritanceAction,
    ReviewCandidate,
    can_auto_inherit,
    export_review_queue,
    inheritance_action,
    may_inherit_identity,
)
from merlo.model import (
    IdentityCandidate,
    IdentityHint,
    IdentityRelation,
    IdentityStatus,
)


def _candidate(
    candidate_id: str = "rename-1",
    *,
    old_entities: tuple[str, ...] = ("old",),
    new_entities: tuple[str, ...] = ("new",),
    changed: bool = True,
) -> ReviewCandidate:
    return ReviewCandidate(
        candidate_id=candidate_id,
        old_entities=old_entities,
        new_entities=new_entities,
        resolver_status=IdentityStatus.PROBABLE,
        resolver_score=0.91,
        resolver_reason="unique structural candidate",
        changed=changed,
    )


def _adjudication(
    candidate_id: str = "rename-1",
    decision: str = IdentityDecision.SAME,
    *,
    links: tuple[IdentityLink, ...] = (),
) -> Adjudication:
    return Adjudication(
        candidate_id=candidate_id,
        decision=decision,
        reviewer="reviewer@example.test",
        provenance="independent_manual_source_review",
        links=links,
        evidence=("compared both definitions",),
    )


def test_zero_denominators_remain_unmeasured() -> None:
    different = _candidate()
    corpus = IdentityGroundTruthCorpus(
        candidates=(different,),
        adjudications=(_adjudication(decision=IdentityDecision.DIFFERENT),),
    )

    metrics = corpus.metrics(())

    assert metrics.true_positive == 0
    assert metrics.false_positive == 0
    assert metrics.false_negative == 0
    assert metrics.precision_denominator == 0
    assert metrics.recall_denominator == 0
    assert metrics.precision is None
    assert metrics.recall is None
    assert metrics.to_dict()["precision"] is None
    assert metrics.to_dict()["recall"] is None


def test_changed_only_metrics_report_tp_fp_and_fn() -> None:
    same = _candidate("same", old_entities=("old-a",), new_entities=("new-a",))
    different = _candidate(
        "different", old_entities=("old-b",), new_entities=("new-b",)
    )
    missed = _candidate("missed", old_entities=("old-c",), new_entities=("new-c",))
    unchanged = _candidate(
        "unchanged",
        old_entities=("stable",),
        new_entities=("stable",),
        changed=False,
    )
    corpus = IdentityGroundTruthCorpus(
        candidates=(same, different, missed, unchanged),
        adjudications=(
            _adjudication("same"),
            _adjudication("different", IdentityDecision.DIFFERENT),
            _adjudication("missed"),
            _adjudication("unchanged"),
        ),
    )

    metrics = corpus.metrics(
        (
            ("old-a", "new-a"),
            ("old-b", "new-b"),
            ("stable", "stable"),
        )
    )

    assert (metrics.tp, metrics.fp, metrics.fn) == (1, 1, 1)
    assert metrics.precision == 0.5
    assert metrics.recall == 0.5


def test_generated_git_proxy_is_rejected_as_ground_truth() -> None:
    for provenance in ("generated_git_proxy", "git_history", "Git inferred label"):
        with pytest.raises(GroundTruthError, match="not independent human ground truth"):
            Adjudication(
                candidate_id="rename-1",
                decision=IdentityDecision.SAME,
                reviewer="reviewer@example.test",
                provenance=provenance,
            )

    with pytest.raises(GroundTruthError, match="independent human review"):
        Adjudication(
            candidate_id="rename-1",
            decision=IdentityDecision.SAME,
            reviewer="reviewer@example.test",
            provenance="manual_review",
            independent=False,
        )


def test_default_progress_target_is_600_and_is_configurable() -> None:
    queue = IdentityReviewQueue(candidates=(_candidate(),))
    corpus = queue.start_corpus().with_adjudication(_adjudication())

    assert DEFAULT_REVIEW_TARGET == 600
    assert corpus.progress.target == 600
    assert corpus.progress.reviewed == 1
    assert corpus.progress.remaining == 599
    assert corpus.progress.fraction == 1 / 600
    assert not corpus.progress.complete

    custom = IdentityGroundTruthCorpus(candidates=(), target=2)
    assert custom.progress.to_dict() == {
        "reviewed": 0,
        "target": 2,
        "remaining": 2,
        "fraction": 0.0,
        "complete": False,
    }


def test_queue_and_corpus_have_stable_versioned_json_round_trips() -> None:
    first = _candidate("b")
    second = _candidate("a")
    queue = IdentityReviewQueue(candidates=(first, second))

    queue_payload = queue.to_json()
    assert queue_payload == IdentityReviewQueue.from_json(queue_payload).to_json()
    assert queue_payload == json.dumps(
        json.loads(queue_payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert json.loads(queue_payload)["schema"] == 1
    assert [item["candidate_id"] for item in json.loads(queue_payload)["candidates"]] == [
        "a",
        "b",
    ]

    corpus = queue.start_corpus().with_adjudication(_adjudication("a"))
    corpus_payload = corpus.to_json()
    assert corpus_payload == IdentityGroundTruthCorpus.from_json(corpus_payload).to_json()
    assert json.loads(corpus_payload)["kind"] == "meldra.identity-ground-truth"

    malformed = json.loads(queue_payload)
    malformed["schema"] = 99
    with pytest.raises(GroundTruthError, match="unsupported"):
        IdentityReviewQueue.from_dict(malformed)


def test_probable_and_ambiguous_never_auto_inherit() -> None:
    assert not can_auto_inherit(IdentityStatus.PROBABLE)
    assert not can_auto_inherit(IdentityStatus.PROBABLE, explicit=True)
    assert not can_auto_inherit(IdentityStatus.AMBIGUOUS, explicit=True)
    assert inheritance_action(IdentityStatus.PROBABLE) == InheritanceAction.REVIEW_ONLY
    assert inheritance_action(IdentityStatus.AMBIGUOUS) == InheritanceAction.REVIEW_ONLY

    assert can_auto_inherit(IdentityStatus.EXACT, explicit=True)
    assert can_auto_inherit(IdentityStatus.EXACT, explicit=False)
    reviewed = _adjudication()
    assert (
        inheritance_action(IdentityStatus.PROBABLE, adjudication=reviewed)
        == InheritanceAction.INHERIT_AFTER_REVIEW
    )
    assert may_inherit_identity(IdentityStatus.PROBABLE, adjudication=reviewed)


def test_queue_export_preserves_resolver_evidence_without_labels() -> None:
    relation = IdentityRelation(
        status=IdentityStatus.AMBIGUOUS,
        old_id="entity-old",
        new_id=None,
        old_locator="pkg.before",
        new_locator=None,
        score=0.72,
        reason="two close candidates",
        candidates=(
            IdentityCandidate("pkg.after", 0.72, ("body",)),
            IdentityCandidate("pkg.other", 0.70, ("signature",)),
        ),
    )
    hint = IdentityHint(
        entity_id="hint-old",
        kind="function",
        module="pkg",
        qualname="hinted",
        caused_by="generated git rename proxy",
    )

    queue = export_review_queue((relation,), (hint,))

    assert len(queue.candidates) == 2
    assert {item.source for item in queue.candidates} == {
        "resolver_link",
        "resolver_hint",
    }
    assert "adjudications" not in queue.to_dict()
    hinted = next(item for item in queue.candidates if item.source == "resolver_hint")
    assert hinted.resolver_status == IdentityStatus.EXACT
    assert hinted.explicit
    assert any("not a human label" in signal for signal in hinted.signals)


def test_split_and_merge_adjudications_round_trip_as_links() -> None:
    split = _candidate(
        "split", old_entities=("old",), new_entities=("new-a", "new-b")
    )
    merge = _candidate(
        "merge", old_entities=("old-a", "old-b"), new_entities=("new",)
    )
    corpus = IdentityGroundTruthCorpus(
        candidates=(split, merge),
        adjudications=(
            _adjudication("split", IdentityDecision.SPLIT),
            _adjudication("merge", IdentityDecision.MERGE),
        ),
    )

    restored = IdentityGroundTruthCorpus.from_json(corpus.to_json())

    assert restored == corpus
    metrics = restored.metrics(
        (IdentityLink("old", "new-a"), IdentityLink("old-a", "new"))
    )
    assert (metrics.tp, metrics.fp, metrics.fn) == (2, 0, 2)
