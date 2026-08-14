from __future__ import annotations

from typing import Iterable

from .model import Evidence, Obligation, ProgramIR, SemanticImpact, SourceEdit


def analyze_impact(
    program: ProgramIR,
    target_id: str,
    *,
    edits: Iterable[SourceEdit] = (),
    obligations: Iterable[Obligation] = (),
    evidence: Iterable[Evidence] = (),
) -> SemanticImpact:
    target = program.entity(target_id)
    edits = tuple(edits)
    obligations = tuple(obligations)
    exact_references = tuple(
        reference
        for reference in program.references_to(target_id)
        if not reference.uncertain
    )
    uncertain = program.uncertain_references_to(target_id)
    direct_callers = program.callers_of(target_id)
    transitive = tuple(
        identifier
        for identifier in program.callers_of(target_id, transitive=True)
        if identifier not in set(direct_callers)
    )
    public_boundaries: set[str] = set()
    if target.public:
        public_boundaries.add(target.id)
    for identifier in (*direct_callers, *transitive):
        try:
            if program.entity(identifier).public:
                public_boundaries.add(identifier)
        except KeyError:
            continue
    affected_files = {target.file}
    affected_files.update(reference.file for reference in exact_references)
    affected_files.update(reference.file for reference in uncertain)
    affected_files.update(edit.file for edit in edits)
    risk_factors: list[str] = []
    if target.public:
        risk_factors.append("public target may have external consumers")
    if uncertain:
        risk_factors.append(
            f"{len(uncertain)} dynamic or unknown reference boundaries"
        )
    if transitive:
        risk_factors.append(f"{len(transitive)} transitive callers")
    if any(item.kind == "AmbiguousIdentity" for item in obligations):
        risk_factors.append("target identity is ambiguous")
    stale_evidence = tuple(
        sorted(item.id for item in evidence if item.status == "stale")
    )
    return SemanticImpact(
        target_id=target_id,
        direct_definitions=(target.id,),
        direct_references=tuple(reference.id for reference in exact_references),
        direct_callers=tuple(direct_callers),
        transitive_callers=tuple(transitive),
        affected_files=tuple(sorted(affected_files)),
        public_boundaries=tuple(sorted(public_boundaries)),
        uncertain_references=tuple(reference.id for reference in uncertain),
        obligations=tuple(item.id for item in obligations),
        invalidated_evidence=stale_evidence,
        expected_edits=len(edits),
        risk_factors=tuple(risk_factors),
    )
