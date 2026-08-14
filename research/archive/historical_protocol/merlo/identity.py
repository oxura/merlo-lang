from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping

from research.archive.historical_protocol.merlo.model import (
    Entity,
    IdentityCandidate,
    IdentityHint,
    IdentityRelation,
    IdentityStatus,
)


AUTO_MATCH_THRESHOLD = 0.84
AMBIGUOUS_THRESHOLD = 0.65
MINIMUM_MARGIN = 0.12


@dataclass(frozen=True)
class IdentitySnapshot:
    kind: str
    module: str
    qualname: str
    name: str
    revision_hash: str
    source_hash: str
    features: dict[str, Any]

    @property
    def locator(self) -> str:
        return f"{self.module}.{self.qualname}" if self.module else self.qualname


@dataclass(frozen=True)
class IdentityAssignment:
    entity_id: str
    status: str
    score: float
    reason: str


@dataclass(frozen=True)
class IdentityResolution:
    assignments: Mapping[str, IdentityAssignment]
    relations: tuple[IdentityRelation, ...]


class IdentityResolver:
    """Recover entity continuity without turning heuristics into facts.

    Only explicit ChangeIR provenance and unchanged semantic addresses are
    classified as Exact. Heuristic recovery is Probable and requires a strong,
    mutual, well-separated match, but receives a fresh Entity ID until explicit
    confirmation. Close candidates remain Ambiguous and also never inherit the
    previous Entity ID.
    """

    def resolve(
        self,
        old_entities: Iterable[Entity],
        new_snapshots: Iterable[IdentitySnapshot],
        hints: Iterable[IdentityHint] = (),
    ) -> IdentityResolution:
        old = tuple(old_entities)
        new = tuple(new_snapshots)
        old_by_id = {entity.id: entity for entity in old}
        new_by_locator = {item.locator: item for item in new}
        assignments: dict[str, IdentityAssignment] = {}
        relations: list[IdentityRelation] = []
        used_old: set[str] = set()
        used_ids: set[str] = set(old_by_id)

        def assign_probable(
            prior: Entity,
            snapshot: IdentitySnapshot,
            score: float,
            reason: str,
            candidates: tuple[IdentityCandidate, ...] = (),
        ) -> None:
            entity_id = _fresh_id(snapshot, used_ids)
            used_ids.add(entity_id)
            assignments[snapshot.locator] = IdentityAssignment(
                entity_id=entity_id,
                status=IdentityStatus.PROBABLE,
                score=score,
                reason=reason,
            )
            used_old.add(prior.id)
            relations.append(
                IdentityRelation(
                    status=IdentityStatus.PROBABLE,
                    old_id=prior.id,
                    new_id=entity_id,
                    old_locator=prior.fqname,
                    new_locator=snapshot.locator,
                    score=score,
                    reason=reason,
                    candidates=candidates,
                )
            )

        for hint in hints:
            snapshot = new_by_locator.get(hint.locator)
            prior = old_by_id.get(hint.entity_id)
            if snapshot is None:
                continue
            if snapshot.kind != hint.kind or (
                prior is not None and prior.kind != hint.kind
            ):
                continue
            if snapshot.locator in assignments or hint.entity_id in used_old:
                raise ValueError("conflicting explicit identity hints")
            assignments[snapshot.locator] = IdentityAssignment(
                entity_id=hint.entity_id,
                status=IdentityStatus.EXACT,
                score=1.0,
                reason=f"explicit ChangeIR provenance from {hint.caused_by}",
            )
            used_old.add(hint.entity_id)
            relations.append(
                IdentityRelation(
                    status=IdentityStatus.EXACT,
                    old_id=hint.entity_id,
                    new_id=hint.entity_id,
                    old_locator=prior.fqname if prior is not None else None,
                    new_locator=snapshot.locator,
                    score=1.0,
                    reason=f"explicit ChangeIR provenance from {hint.caused_by}",
                    explicit=True,
                )
            )

        old_by_address = {
            (entity.kind, entity.module, entity.qualname): entity for entity in old
        }
        for snapshot in new:
            if snapshot.locator in assignments:
                continue
            prior = old_by_address.get(
                (snapshot.kind, snapshot.module, snapshot.qualname)
            )
            if (
                prior is not None
                and prior.id not in used_old
                and not prior.identity_features
            ):
                assign_probable(
                    prior,
                    snapshot,
                    0.75,
                    "legacy world migration by unchanged semantic address",
                )
                continue
            if (
                prior is not None
                and prior.id not in used_old
                and prior.revision_hash == snapshot.revision_hash
            ):
                assignments[snapshot.locator] = IdentityAssignment(
                    entity_id=prior.id,
                    status=IdentityStatus.EXACT,
                    score=1.0,
                    reason="unchanged semantic address and revision",
                )
                used_old.add(prior.id)
                relations.append(
                    IdentityRelation(
                        status=IdentityStatus.EXACT,
                        old_id=prior.id,
                        new_id=prior.id,
                        old_locator=prior.fqname,
                        new_locator=snapshot.locator,
                        score=1.0,
                        reason="unchanged semantic address and revision",
                    )
                )

        unmatched_old = [entity for entity in old if entity.id not in used_old]
        unmatched_new = [
            snapshot for snapshot in new if snapshot.locator not in assignments
        ]

        old_by_content: dict[tuple[str, str], list[Entity]] = {}
        new_by_content: dict[tuple[str, str], list[IdentitySnapshot]] = {}
        for entity in unmatched_old:
            content_hash = str(entity.identity_features.get("content_hash", ""))
            if content_hash:
                old_by_content.setdefault((entity.kind, content_hash), []).append(entity)
        for snapshot in unmatched_new:
            content_hash = str(snapshot.features.get("content_hash", ""))
            if content_hash:
                new_by_content.setdefault((snapshot.kind, content_hash), []).append(snapshot)

        content_ambiguous_old: set[str] = set()
        content_ambiguous_new: set[str] = set()
        for key in sorted(set(old_by_content) & set(new_by_content)):
            old_items = [item for item in old_by_content[key] if item.id not in used_old]
            new_items = [
                item
                for item in new_by_content[key]
                if item.locator not in assignments
            ]
            if len(old_items) == 1 and len(new_items) == 1:
                prior = old_items[0]
                snapshot = new_items[0]
                same_address = new_by_locator.get(prior.fqname)
                competing: tuple[float, tuple[str, ...]] | None = None
                if (
                    same_address is not None
                    and same_address.locator != snapshot.locator
                    and same_address.locator not in assignments
                    and same_address.kind == prior.kind
                ):
                    competing = _similarity(prior, same_address)
                if competing is not None and competing[0] >= AMBIGUOUS_THRESHOLD:
                    candidates = (
                        IdentityCandidate(
                            snapshot.locator, 1.0, ("identical content",)
                        ),
                        IdentityCandidate(
                            same_address.locator,
                            competing[0],
                            competing[1],
                        ),
                    )
                    relations.append(
                        IdentityRelation(
                            status=IdentityStatus.AMBIGUOUS,
                            old_id=prior.id,
                            new_id=None,
                            old_locator=prior.fqname,
                            new_locator=None,
                            score=1.0,
                            reason=(
                                "content continuity conflicts with address continuity"
                            ),
                            candidates=candidates,
                        )
                    )
                    content_ambiguous_old.add(prior.id)
                    content_ambiguous_new.update(
                        candidate.locator for candidate in candidates
                    )
                    continue
                assign_probable(
                    prior,
                    snapshot,
                    0.99,
                    "unique name-neutral semantic content",
                )
            elif old_items and new_items:
                for prior in old_items:
                    candidates = tuple(
                        IdentityCandidate(
                            locator=item.locator,
                            score=1.0,
                            signals=("identical content",),
                        )
                        for item in sorted(
                            new_items, key=lambda candidate: candidate.locator
                        )
                    )
                    relations.append(
                        IdentityRelation(
                            status=IdentityStatus.AMBIGUOUS,
                            old_id=prior.id,
                            new_id=None,
                            old_locator=prior.fqname,
                            new_locator=None,
                            score=1.0,
                            reason="identical content has multiple candidate entities",
                            candidates=candidates,
                        )
                    )
                    content_ambiguous_old.add(prior.id)
                    content_ambiguous_new.update(
                        candidate.locator for candidate in candidates
                    )

        unmatched_old = [
            entity
            for entity in old
            if entity.id not in used_old
            and entity.id not in content_ambiguous_old
        ]
        unmatched_new = [
            snapshot
            for snapshot in new
            if snapshot.locator not in assignments
            and snapshot.locator not in content_ambiguous_new
        ]
        scores: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}
        for prior in unmatched_old:
            for snapshot in unmatched_new:
                if prior.kind != snapshot.kind:
                    continue
                scores[(prior.id, snapshot.locator)] = _similarity(prior, snapshot)

        best_for_new: dict[str, tuple[str, float]] = {}
        for snapshot in unmatched_new:
            ranked = sorted(
                (
                    (prior.id, scores[(prior.id, snapshot.locator)][0])
                    for prior in unmatched_old
                    if (prior.id, snapshot.locator) in scores
                ),
                key=lambda item: (-item[1], item[0]),
            )
            if ranked:
                best_for_new[snapshot.locator] = ranked[0]

        ambiguous_new: set[str] = set()
        for prior in sorted(unmatched_old, key=lambda item: item.id):
            if prior.id in used_old:
                continue
            ranked = sorted(
                (
                    (
                        snapshot,
                        *scores[(prior.id, snapshot.locator)],
                    )
                    for snapshot in unmatched_new
                    if snapshot.locator not in assignments
                    and (prior.id, snapshot.locator) in scores
                ),
                key=lambda item: (-item[1], item[0].locator),
            )
            if not ranked:
                continue
            best_snapshot, best_score, best_signals = ranked[0]
            second_score = ranked[1][1] if len(ranked) > 1 else 0.0
            mutual = best_for_new.get(best_snapshot.locator, (None, 0.0))[0] == prior.id
            margin = best_score - second_score
            if (
                best_score >= AUTO_MATCH_THRESHOLD
                and margin >= MINIMUM_MARGIN
                and mutual
                and best_snapshot.locator not in ambiguous_new
            ):
                assign_probable(
                    prior,
                    best_snapshot,
                    best_score,
                    "unique high-confidence structural match",
                    (
                        IdentityCandidate(
                            best_snapshot.locator, best_score, best_signals
                        ),
                    ),
                )
                continue
            candidates = tuple(
                IdentityCandidate(item.locator, score, signals)
                for item, score, signals in ranked
                if score >= AMBIGUOUS_THRESHOLD
            )
            if candidates:
                ambiguous_new.update(
                    item.locator
                    for item, candidate_score, _signals in ranked
                    if candidate_score >= AMBIGUOUS_THRESHOLD
                )
                relations.append(
                    IdentityRelation(
                        status=IdentityStatus.AMBIGUOUS,
                        old_id=prior.id,
                        new_id=None,
                        old_locator=prior.fqname,
                        new_locator=None,
                        score=best_score,
                        reason=(
                            "structural candidates are not sufficiently separated"
                            if best_score >= AUTO_MATCH_THRESHOLD
                            else "no candidate reaches the automatic match threshold"
                        ),
                        candidates=candidates,
                    )
                )

        used_ids.update(
            assignment.entity_id for assignment in assignments.values()
        )
        ambiguous_locators = {
            candidate.locator
            for relation in relations
            if relation.status == IdentityStatus.AMBIGUOUS
            for candidate in relation.candidates
        }
        for snapshot in new:
            if snapshot.locator in assignments:
                continue
            entity_id = _fresh_id(snapshot, used_ids)
            used_ids.add(entity_id)
            status = (
                IdentityStatus.AMBIGUOUS
                if snapshot.locator in ambiguous_locators
                else IdentityStatus.NEW
            )
            reason = (
                "candidate in unresolved identity relation"
                if status == IdentityStatus.AMBIGUOUS
                else "no reliable predecessor"
            )
            assignments[snapshot.locator] = IdentityAssignment(
                entity_id=entity_id,
                status=status,
                score=0.0,
                reason=reason,
            )
            if status == IdentityStatus.NEW:
                relations.append(
                    IdentityRelation(
                        status=IdentityStatus.NEW,
                        old_id=None,
                        new_id=entity_id,
                        old_locator=None,
                        new_locator=snapshot.locator,
                        reason=reason,
                    )
                )

        matched_old = {
            relation.old_id
            for relation in relations
            if relation.old_id is not None
            and relation.status in {IdentityStatus.EXACT, IdentityStatus.PROBABLE}
        }
        ambiguous_old = {
            relation.old_id
            for relation in relations
            if relation.status == IdentityStatus.AMBIGUOUS
        }
        for prior in old:
            if prior.id in matched_old or prior.id in ambiguous_old:
                continue
            relations.append(
                IdentityRelation(
                    status=IdentityStatus.DELETED,
                    old_id=prior.id,
                    new_id=None,
                    old_locator=prior.fqname,
                    new_locator=None,
                    reason="no reliable successor",
                )
            )

        return IdentityResolution(
            assignments=assignments,
            relations=tuple(
                sorted(
                    relations,
                    key=lambda item: (
                        item.status,
                        item.old_locator or "",
                        item.new_locator or "",
                    ),
                )
            ),
        )


def _similarity(
    prior: Entity, snapshot: IdentitySnapshot
) -> tuple[float, tuple[str, ...]]:
    old_features = prior.identity_features
    new_features = snapshot.features
    signature = SequenceMatcher(
        None,
        str(old_features.get("signature_shape", prior.signature)),
        str(new_features.get("signature_shape", "")),
        autojunk=False,
    ).ratio()
    semantic = SequenceMatcher(
        None,
        str(old_features.get("semantic_shape", "")),
        str(new_features.get("semantic_shape", "")),
        autojunk=False,
    ).ratio()
    nodes = _counter_similarity(
        old_features.get("node_kinds", {}), new_features.get("node_kinds", {})
    )
    calls = _set_similarity(
        old_features.get("calls", []), new_features.get("calls", [])
    )
    references = _set_similarity(
        old_features.get("references", []), new_features.get("references", [])
    )
    address = 1.0 if (prior.module, prior.qualname) == (
        snapshot.module,
        snapshot.qualname,
    ) else 0.0
    score = (
        signature * 0.20
        + semantic * 0.30
        + nodes * 0.20
        + calls * 0.15
        + references * 0.10
        + address * 0.05
    )
    signals = (
        f"signature={signature:.3f}",
        f"semantic_shape={semantic:.3f}",
        f"node_kinds={nodes:.3f}",
        f"calls={calls:.3f}",
        f"references={references:.3f}",
        f"same_address={bool(address)}",
    )
    return score, signals


def _counter_similarity(left: Any, right: Any) -> float:
    left_counter = Counter({str(key): int(value) for key, value in dict(left).items()})
    right_counter = Counter({str(key): int(value) for key, value in dict(right).items()})
    union = sum((left_counter | right_counter).values())
    if union == 0:
        return 1.0
    intersection = sum((left_counter & right_counter).values())
    return intersection / union


def _set_similarity(left: Any, right: Any) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 1.0
    return len(left_set & right_set) / len(left_set | right_set)


def _fresh_id(snapshot: IdentitySnapshot, used: set[str]) -> str:
    seed = f"{snapshot.kind}\0{snapshot.module}\0{snapshot.qualname}"
    counter = 0
    while True:
        suffix = "" if counter == 0 else f"\0{counter}"
        digest = hashlib.sha256((seed + suffix).encode("utf-8")).hexdigest()[:16]
        candidate = f"ent_{digest}"
        if candidate not in used:
            return candidate
        counter += 1
