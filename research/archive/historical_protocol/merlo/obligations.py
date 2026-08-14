from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Iterable

from research.archive.historical_protocol.merlo.model import (
    IdentityStatus,
    Obligation,
    ObligationGraph,
    ObligationStatus,
    ProgramIR,
    Reference,
)


def make_obligation(
    change_id: str,
    kind: str,
    message: str,
    *,
    files: Iterable[str] = (),
    affected_entities: Iterable[str] = (),
    severity: str = "error",
    depends_on: Iterable[str] = (),
    caused_by: Iterable[str] = (),
    evidence_required: Iterable[str] = (),
    possible_resolutions: Iterable[str] = (),
    status: str = ObligationStatus.OPEN,
) -> Obligation:
    normalized_files = tuple(sorted(set(files)))
    normalized_entities = tuple(sorted(set(affected_entities)))
    normalized_dependencies = tuple(sorted(set(depends_on)))
    payload = "\0".join(
        (
            change_id,
            kind,
            message,
            "|".join(normalized_files),
            "|".join(normalized_entities),
            "|".join(normalized_dependencies),
        )
    )
    identifier = "obl_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return Obligation(
        id=identifier,
        kind=kind,
        message=message,
        files=normalized_files,
        affected_entities=normalized_entities,
        severity=severity,
        status=status,
        root_change=change_id,
        caused_by=tuple(sorted(set(caused_by))) or (change_id,),
        evidence_required=tuple(sorted(set(evidence_required))),
        possible_resolutions=tuple(sorted(set(possible_resolutions))),
        depends_on=normalized_dependencies,
    )


def build_graph(obligations: Iterable[Obligation]) -> ObligationGraph:
    unique = {item.id: item for item in obligations}
    return ObligationGraph(
        tuple(sorted(unique.values(), key=lambda item: (item.kind, item.id)))
    )


def identity_obligations(
    program: ProgramIR,
    change_id: str,
    target_id: str,
) -> tuple[Obligation, ...]:
    target = program.entity(target_id)
    result: list[Obligation] = []
    if target.identity_status == IdentityStatus.AMBIGUOUS:
        result.append(
            make_obligation(
                change_id,
                "AmbiguousIdentity",
                f"identity of {target.fqname} is unresolved",
                files=(target.file,),
                affected_entities=(target.id,),
                evidence_required=("IdentityConfirmation",),
                possible_resolutions=(
                    "confirm one predecessor explicitly",
                    "treat the declaration as a new entity",
                ),
            )
        )
    elif target.identity_status == IdentityStatus.PROBABLE:
        result.append(
            make_obligation(
                change_id,
                "ProbableIdentity",
                (
                    f"identity of {target.fqname} was recovered heuristically "
                    f"(score {target.identity_score:.3f})"
                ),
                files=(target.file,),
                affected_entities=(target.id,),
                evidence_required=("IdentityConfirmation",),
                possible_resolutions=(
                    "confirm recovered identity",
                    "rescan after applying a provenance-bearing ChangeIR",
                ),
            )
        )
    return tuple(result)


def uncertain_reference_obligation(
    change_id: str,
    reference: Reference,
    target_id: str,
    *,
    kind: str | None = None,
    depends_on: Iterable[str] = (),
) -> Obligation:
    obligation_kind = kind or _reference_obligation_kind(reference)
    return make_obligation(
        change_id,
        obligation_kind,
        (
            f"{reference.provenance} reference at "
            f"{reference.file}:{reference.span.start.line} is "
            f"{reference.resolution}, not exact"
        ),
        files=(reference.file,),
        affected_entities=tuple(
            sorted(set(reference.possible_target_ids) | {target_id})
        ),
        depends_on=depends_on,
        caused_by=(reference.id,),
        evidence_required=("ManualBindingResolution",),
        possible_resolutions=(
            "replace the dynamic binding with a static reference",
            "supply runtime binding evidence",
            "waive after manual review",
        ),
    )


def update_status(
    graph: ObligationGraph,
    identifier: str,
    status: str,
) -> ObligationGraph:
    if status not in {
        ObligationStatus.OPEN,
        ObligationStatus.CLAIMED,
        ObligationStatus.RESOLVED,
        ObligationStatus.WAIVED,
    }:
        raise ValueError(f"invalid obligation status: {status}")
    graph.inspect(identifier)
    return ObligationGraph(
        tuple(
            replace(item, status=status) if item.id == identifier else item
            for item in graph.obligations
        )
    )


def _reference_obligation_kind(reference: Reference) -> str:
    return {
        "Reflection": "DynamicReference",
        "Wildcard": "WildcardReference",
        "StringLiteral": "StringReference",
        "RuntimeObserved": "RuntimeObservedReference",
    }.get(reference.provenance, "UnknownReference")
