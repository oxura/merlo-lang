from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from .model import IdentityHint, IdentityRelation, IdentityStatus


SCHEMA_VERSION = 1
DEFAULT_REVIEW_TARGET = 600
QUEUE_KIND = "meldra.identity-review-queue"
CORPUS_KIND = "meldra.identity-ground-truth"


class GroundTruthError(ValueError):
    """Raised when identity review data is invalid or is not independent."""


class IdentityDecision:
    SAME = "same"
    DIFFERENT = "different"
    SPLIT = "split"
    MERGE = "merge"
    UNCERTAIN = "uncertain"

    ALL = frozenset({SAME, DIFFERENT, SPLIT, MERGE, UNCERTAIN})


class InheritanceAction:
    AUTO_INHERIT = "auto_inherit"
    INHERIT_AFTER_REVIEW = "inherit_after_review"
    REVIEW_ONLY = "review_only"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_object(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GroundTruthError(f"{description} must be a JSON object")
    return value


def _require_schema(value: Mapping[str, Any], kind: str) -> None:
    if value.get("kind") != kind:
        raise GroundTruthError(f"expected {kind!r}, got {value.get('kind')!r}")
    try:
        schema = int(value.get("schema", -1))
    except (TypeError, ValueError) as error:
        raise GroundTruthError("schema must be an integer") from error
    if schema != SCHEMA_VERSION:
        raise GroundTruthError(
            f"unsupported {kind} schema {schema}; expected {SCHEMA_VERSION}"
        )


def _strings(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in values if str(value)}))


def _stable_id(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"identity-review:{digest[:24]}"


def _is_generated_git_proxy(provenance: str) -> bool:
    normalized = provenance.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {
        "git",
        "git_history",
        "git_proxy",
        "generated",
        "generated_git",
        "generated_git_proxy",
        "git_heuristic",
    }:
        return True
    return "git" in normalized and any(
        token in normalized for token in ("generated", "proxy", "heuristic", "inferred")
    )


@dataclass(frozen=True, order=True)
class IdentityLink:
    old: str
    new: str

    def __post_init__(self) -> None:
        if not self.old or not self.new:
            raise GroundTruthError("identity link endpoints must be non-empty")

    def to_dict(self) -> dict[str, str]:
        return {"old": self.old, "new": self.new}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IdentityLink":
        return cls(old=str(value["old"]), new=str(value["new"]))


@dataclass(frozen=True)
class ReviewCandidate:
    candidate_id: str
    old_entities: tuple[str, ...]
    new_entities: tuple[str, ...]
    resolver_status: str
    resolver_score: float = 0.0
    resolver_reason: str = ""
    explicit: bool = False
    source: str = "resolver_link"
    signals: tuple[str, ...] = ()
    changed: bool = True

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise GroundTruthError("candidate_id is required")
        old_entities = _strings(self.old_entities)
        new_entities = _strings(self.new_entities)
        if not old_entities and not new_entities:
            raise GroundTruthError("a review candidate needs an old or new entity")
        if not self.resolver_status:
            raise GroundTruthError("resolver_status is required")
        if not 0.0 <= float(self.resolver_score) <= 1.0:
            raise GroundTruthError("resolver_score must be between zero and one")
        if not self.source:
            raise GroundTruthError("candidate source is required")
        object.__setattr__(self, "old_entities", old_entities)
        object.__setattr__(self, "new_entities", new_entities)
        object.__setattr__(self, "resolver_score", float(self.resolver_score))
        object.__setattr__(self, "signals", _strings(self.signals))

    @property
    def possible_links(self) -> tuple[IdentityLink, ...]:
        return tuple(
            IdentityLink(old, new)
            for old in self.old_entities
            for new in self.new_entities
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "old_entities": list(self.old_entities),
            "new_entities": list(self.new_entities),
            "resolver_status": self.resolver_status,
            "resolver_score": round(self.resolver_score, 6),
            "resolver_reason": self.resolver_reason,
            "explicit": self.explicit,
            "source": self.source,
            "signals": list(self.signals),
            "changed": self.changed,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReviewCandidate":
        return cls(
            candidate_id=str(value["candidate_id"]),
            old_entities=tuple(map(str, value.get("old_entities", ()))),
            new_entities=tuple(map(str, value.get("new_entities", ()))),
            resolver_status=str(value["resolver_status"]),
            resolver_score=float(value.get("resolver_score", 0.0)),
            resolver_reason=str(value.get("resolver_reason", "")),
            explicit=bool(value.get("explicit", False)),
            source=str(value.get("source", "resolver_link")),
            signals=tuple(map(str, value.get("signals", ()))),
            changed=bool(value.get("changed", True)),
        )


@dataclass(frozen=True)
class Adjudication:
    candidate_id: str
    decision: str
    reviewer: str
    provenance: str = "independent_human_review"
    links: tuple[IdentityLink, ...] = ()
    evidence: tuple[str, ...] = ()
    independent: bool = True

    def __post_init__(self) -> None:
        decision = self.decision.strip().lower()
        if not self.candidate_id:
            raise GroundTruthError("candidate_id is required")
        if decision not in IdentityDecision.ALL:
            raise GroundTruthError(f"unsupported identity decision: {self.decision}")
        if not self.reviewer.strip():
            raise GroundTruthError("reviewer is required")
        if not self.provenance.strip():
            raise GroundTruthError("adjudication provenance is required")
        if not self.independent:
            raise GroundTruthError("ground truth requires independent human review")
        if _is_generated_git_proxy(self.provenance):
            raise GroundTruthError(
                "generated Git proxy labels are not independent human ground truth"
            )
        links = tuple(sorted(set(self.links)))
        if decision in {IdentityDecision.DIFFERENT, IdentityDecision.UNCERTAIN} and links:
            raise GroundTruthError(f"{decision} adjudication cannot contain identity links")
        if decision == IdentityDecision.SAME and len(links) > 1:
            raise GroundTruthError("same adjudication can select at most one identity link")
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "reviewer", self.reviewer.strip())
        object.__setattr__(self, "provenance", self.provenance.strip())
        object.__setattr__(self, "links", links)
        object.__setattr__(self, "evidence", _strings(self.evidence))

    @property
    def independently_confirmed_same(self) -> bool:
        return self.independent and self.decision == IdentityDecision.SAME

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "decision": self.decision,
            "reviewer": self.reviewer,
            "provenance": self.provenance,
            "links": [link.to_dict() for link in self.links],
            "evidence": list(self.evidence),
            "independent": self.independent,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Adjudication":
        return cls(
            candidate_id=str(value["candidate_id"]),
            decision=str(value["decision"]),
            reviewer=str(value["reviewer"]),
            provenance=str(value.get("provenance", "independent_human_review")),
            links=tuple(
                IdentityLink.from_dict(_require_object(item, "identity link"))
                for item in value.get("links", ())
            ),
            evidence=tuple(map(str, value.get("evidence", ()))),
            independent=bool(value.get("independent", True)),
        )


@dataclass(frozen=True)
class ReviewProgress:
    reviewed: int
    target: int

    def __post_init__(self) -> None:
        if self.reviewed < 0:
            raise GroundTruthError("reviewed count cannot be negative")
        if self.target <= 0:
            raise GroundTruthError("review target must be positive")

    @property
    def remaining(self) -> int:
        return max(0, self.target - self.reviewed)

    @property
    def fraction(self) -> float:
        return min(1.0, self.reviewed / self.target)

    @property
    def complete(self) -> bool:
        return self.reviewed >= self.target

    def to_dict(self) -> dict[str, Any]:
        return {
            "reviewed": self.reviewed,
            "target": self.target,
            "remaining": self.remaining,
            "fraction": round(self.fraction, 6),
            "complete": self.complete,
        }


@dataclass(frozen=True)
class IdentityReviewQueue:
    candidates: tuple[ReviewCandidate, ...]
    target: int = DEFAULT_REVIEW_TARGET
    schema: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema != SCHEMA_VERSION:
            raise GroundTruthError(f"unsupported review queue schema: {self.schema}")
        if self.target <= 0:
            raise GroundTruthError("review target must be positive")
        candidates = tuple(sorted(self.candidates, key=lambda item: item.candidate_id))
        ids = [item.candidate_id for item in candidates]
        if len(ids) != len(set(ids)):
            raise GroundTruthError("duplicate review candidate id")
        object.__setattr__(self, "candidates", candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": QUEUE_KIND,
            "schema": self.schema,
            "target": self.target,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IdentityReviewQueue":
        _require_schema(value, QUEUE_KIND)
        return cls(
            candidates=tuple(
                ReviewCandidate.from_dict(_require_object(item, "review candidate"))
                for item in value.get("candidates", ())
            ),
            target=int(value.get("target", DEFAULT_REVIEW_TARGET)),
            schema=int(value["schema"]),
        )

    @classmethod
    def from_json(cls, payload: str) -> "IdentityReviewQueue":
        try:
            value = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as error:
            raise GroundTruthError("invalid review queue JSON") from error
        return cls.from_dict(_require_object(value, "review queue"))

    def start_corpus(self) -> "IdentityGroundTruthCorpus":
        return IdentityGroundTruthCorpus(candidates=self.candidates, target=self.target)


@dataclass(frozen=True)
class ChangedIdentityMetrics:
    true_positive: int
    false_positive: int
    false_negative: int

    def __post_init__(self) -> None:
        if min(self.true_positive, self.false_positive, self.false_negative) < 0:
            raise GroundTruthError("metric counts cannot be negative")

    @property
    def precision_denominator(self) -> int:
        return self.true_positive + self.false_positive

    @property
    def recall_denominator(self) -> int:
        return self.true_positive + self.false_negative

    @property
    def precision(self) -> float | None:
        if self.precision_denominator == 0:
            return None
        return self.true_positive / self.precision_denominator

    @property
    def recall(self) -> float | None:
        if self.recall_denominator == 0:
            return None
        return self.true_positive / self.recall_denominator

    @property
    def tp(self) -> int:
        return self.true_positive

    @property
    def fp(self) -> int:
        return self.false_positive

    @property
    def fn(self) -> int:
        return self.false_negative

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": "changed_identity_only",
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "precision_numerator": self.true_positive,
            "precision_denominator": self.precision_denominator,
            "precision": round(self.precision, 6) if self.precision is not None else None,
            "recall_numerator": self.true_positive,
            "recall_denominator": self.recall_denominator,
            "recall": round(self.recall, 6) if self.recall is not None else None,
        }


@dataclass(frozen=True)
class IdentityGroundTruthCorpus:
    candidates: tuple[ReviewCandidate, ...]
    adjudications: tuple[Adjudication, ...] = ()
    target: int = DEFAULT_REVIEW_TARGET
    schema: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema != SCHEMA_VERSION:
            raise GroundTruthError(f"unsupported ground-truth schema: {self.schema}")
        if self.target <= 0:
            raise GroundTruthError("review target must be positive")
        candidates = tuple(sorted(self.candidates, key=lambda item: item.candidate_id))
        adjudications = tuple(
            sorted(self.adjudications, key=lambda item: item.candidate_id)
        )
        candidate_ids = [item.candidate_id for item in candidates]
        candidates_by_id = {item.candidate_id: item for item in candidates}
        adjudicated_ids = [item.candidate_id for item in adjudications]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise GroundTruthError("duplicate review candidate id")
        if len(adjudicated_ids) != len(set(adjudicated_ids)):
            raise GroundTruthError("a candidate can have only one immutable adjudication")
        unknown = sorted(set(adjudicated_ids) - set(candidate_ids))
        if unknown:
            raise GroundTruthError(f"adjudications reference unknown candidates: {unknown}")
        for adjudication in adjudications:
            _validate_selected_links(
                candidates_by_id[adjudication.candidate_id], adjudication
            )
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "adjudications", adjudications)

    @property
    def progress(self) -> ReviewProgress:
        return ReviewProgress(len(self.adjudications), self.target)

    def adjudication_for(self, candidate_id: str) -> Adjudication | None:
        return next(
            (
                adjudication
                for adjudication in self.adjudications
                if adjudication.candidate_id == candidate_id
            ),
            None,
        )

    def with_adjudication(self, adjudication: Adjudication) -> "IdentityGroundTruthCorpus":
        if self.adjudication_for(adjudication.candidate_id) is not None:
            raise GroundTruthError(
                f"candidate {adjudication.candidate_id!r} is already adjudicated"
            )
        return replace(self, adjudications=(*self.adjudications, adjudication))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": CORPUS_KIND,
            "schema": self.schema,
            "target": self.target,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "adjudications": [item.to_dict() for item in self.adjudications],
            "progress": self.progress.to_dict(),
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IdentityGroundTruthCorpus":
        _require_schema(value, CORPUS_KIND)
        return cls(
            candidates=tuple(
                ReviewCandidate.from_dict(_require_object(item, "review candidate"))
                for item in value.get("candidates", ())
            ),
            adjudications=tuple(
                Adjudication.from_dict(_require_object(item, "adjudication"))
                for item in value.get("adjudications", ())
            ),
            target=int(value.get("target", DEFAULT_REVIEW_TARGET)),
            schema=int(value["schema"]),
        )

    @classmethod
    def from_json(cls, payload: str) -> "IdentityGroundTruthCorpus":
        try:
            value = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as error:
            raise GroundTruthError("invalid ground-truth JSON") from error
        return cls.from_dict(_require_object(value, "ground-truth corpus"))

    def metrics(self, predicted_links: Iterable[IdentityLink | tuple[str, str]]) -> ChangedIdentityMetrics:
        predictions = {
            link if isinstance(link, IdentityLink) else IdentityLink(str(link[0]), str(link[1]))
            for link in predicted_links
        }
        truth: set[IdentityLink] = set()
        reviewed_universe: set[IdentityLink] = set()
        candidates = {item.candidate_id: item for item in self.candidates if item.changed}
        for adjudication in self.adjudications:
            candidate = candidates.get(adjudication.candidate_id)
            if candidate is None or adjudication.decision == IdentityDecision.UNCERTAIN:
                continue
            reviewed_universe.update(candidate.possible_links)
            truth.update(_truth_links(candidate, adjudication))
        considered_predictions = predictions & reviewed_universe
        return ChangedIdentityMetrics(
            true_positive=len(considered_predictions & truth),
            false_positive=len(considered_predictions - truth),
            false_negative=len(truth - considered_predictions),
        )


def _validate_selected_links(
    candidate: ReviewCandidate, adjudication: Adjudication
) -> None:
    possible = set(candidate.possible_links)
    selected = set(adjudication.links)
    if not selected <= possible:
        raise GroundTruthError("adjudication selected a link outside its review candidate")
    decision = adjudication.decision
    effective = selected or set(candidate.possible_links)
    if decision == IdentityDecision.SAME:
        if selected and len(selected) != 1:
            raise GroundTruthError("same adjudication must select one link")
        if not selected and len(possible) != 1:
            raise GroundTruthError("same adjudication must disambiguate its selected link")
    elif decision == IdentityDecision.SPLIT:
        if not effective or len({link.old for link in effective}) != 1:
            raise GroundTruthError("split adjudication needs one old identity")
        if len({link.new for link in effective}) < 2:
            raise GroundTruthError("split adjudication needs at least two new identities")
    elif decision == IdentityDecision.MERGE:
        if not effective or len({link.new for link in effective}) != 1:
            raise GroundTruthError("merge adjudication needs one new identity")
        if len({link.old for link in effective}) < 2:
            raise GroundTruthError("merge adjudication needs at least two old identities")


def _truth_links(
    candidate: ReviewCandidate, adjudication: Adjudication
) -> set[IdentityLink]:
    if adjudication.decision in {IdentityDecision.DIFFERENT, IdentityDecision.UNCERTAIN}:
        return set()
    return set(adjudication.links or candidate.possible_links)


def inheritance_action(
    resolver_status: str,
    *,
    explicit: bool = False,
    adjudication: Adjudication | None = None,
) -> str:
    """Return the only policy-supported route for inheriting an old identity."""

    if resolver_status == IdentityStatus.EXACT:
        return InheritanceAction.AUTO_INHERIT
    if adjudication is not None and adjudication.independently_confirmed_same:
        return InheritanceAction.INHERIT_AFTER_REVIEW
    return InheritanceAction.REVIEW_ONLY


def can_auto_inherit(resolver_status: str, *, explicit: bool = False) -> bool:
    """Whether resolver evidence alone is sufficient to inherit identity."""

    return inheritance_action(resolver_status, explicit=explicit) == InheritanceAction.AUTO_INHERIT


def may_inherit_identity(
    resolver_status: str,
    *,
    explicit: bool = False,
    adjudication: Adjudication | None = None,
) -> bool:
    return inheritance_action(
        resolver_status, explicit=explicit, adjudication=adjudication
    ) in {InheritanceAction.AUTO_INHERIT, InheritanceAction.INHERIT_AFTER_REVIEW}


def export_review_queue(
    links: Iterable[IdentityRelation] | Any,
    hints: Iterable[IdentityHint] = (),
    *,
    target: int = DEFAULT_REVIEW_TARGET,
    include_unchanged: bool = False,
) -> IdentityReviewQueue:
    """Export resolver evidence as unlabeled review work.

    Links and hints remain candidate evidence.  This function deliberately has
    no path that creates an adjudication or treats a Git-derived hint as truth.
    """

    relations = getattr(links, "relations", links)
    hint_items = tuple(hints)
    hint_keys = {(hint.entity_id, hint.locator) for hint in hint_items}
    consumed_hints: set[tuple[str, str]] = set()
    candidates: list[ReviewCandidate] = []
    for relation in relations:
        old_entities = _strings(
            (relation.old_id or relation.old_locator,)
            if relation.old_id or relation.old_locator
            else ()
        )
        new_values: list[str] = []
        if relation.new_id or relation.new_locator:
            new_values.append(str(relation.new_id or relation.new_locator))
        new_values.extend(candidate.locator for candidate in relation.candidates)
        new_entities = _strings(new_values)
        changed = bool(
            relation.status not in {IdentityStatus.EXACT}
            or relation.old_locator != relation.new_locator
            or relation.old_id != relation.new_id
        )
        matching_hints = {
            key
            for key in hint_keys
            if key[0] == relation.old_id
            and (key[1] == relation.new_locator or key[1] in new_entities)
        }
        consumed_hints.update(matching_hints)
        if not changed and not include_unchanged:
            continue
        payload = {
            "old_entities": old_entities,
            "new_entities": new_entities,
            "status": relation.status,
            "reason": relation.reason,
        }
        signals = [
            signal
            for candidate in relation.candidates
            for signal in candidate.signals
        ]
        if matching_hints:
            signals.append("resolver identity hint present; not a human label")
        candidates.append(
            ReviewCandidate(
                candidate_id=_stable_id(payload),
                old_entities=old_entities,
                new_entities=new_entities,
                resolver_status=relation.status,
                resolver_score=float(relation.score),
                resolver_reason=relation.reason,
                explicit=bool(relation.explicit),
                source="resolver_link",
                signals=tuple(signals),
                changed=changed,
            )
        )
    for hint in hint_items:
        key = (hint.entity_id, hint.locator)
        if key in consumed_hints:
            continue
        payload = {
            "old_entities": (hint.entity_id,),
            "new_entities": (hint.locator,),
            "status": IdentityStatus.EXACT,
            "reason": hint.caused_by,
        }
        candidates.append(
            ReviewCandidate(
                candidate_id=_stable_id(payload),
                old_entities=(hint.entity_id,),
                new_entities=(hint.locator,),
                resolver_status=IdentityStatus.EXACT,
                resolver_score=1.0,
                resolver_reason=hint.caused_by,
                explicit=True,
                source="resolver_hint",
                signals=("resolver identity hint present; not a human label",),
                changed=True,
            )
        )
    return IdentityReviewQueue(candidates=tuple(candidates), target=target)


def changed_identity_metrics(
    corpus: IdentityGroundTruthCorpus,
    predicted_links: Iterable[IdentityLink | tuple[str, str]],
) -> ChangedIdentityMetrics:
    return corpus.metrics(predicted_links)


__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_REVIEW_TARGET",
    "GroundTruthError",
    "IdentityDecision",
    "InheritanceAction",
    "IdentityLink",
    "ReviewCandidate",
    "Adjudication",
    "ReviewProgress",
    "IdentityReviewQueue",
    "IdentityGroundTruthCorpus",
    "ChangedIdentityMetrics",
    "inheritance_action",
    "can_auto_inherit",
    "may_inherit_identity",
    "export_review_queue",
    "changed_identity_metrics",
]
