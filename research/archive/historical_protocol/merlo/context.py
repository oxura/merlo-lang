from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from research.archive.historical_protocol.merlo.impact import analyze_impact
from research.archive.historical_protocol.merlo.model import (
    EditCapability,
    Entity,
    Evidence,
    Obligation,
    ProgramIR,
    Reference,
    Span,
)


@dataclass(frozen=True)
class TaskCapsule:
    goal: str
    target: dict[str, Any]
    definition_source: str
    direct_references: tuple[dict[str, Any], ...]
    direct_callers: tuple[dict[str, Any], ...]
    transitive_callers: tuple[dict[str, Any], ...]
    dependencies: tuple[dict[str, Any], ...]
    uncertain_boundaries: tuple[dict[str, Any], ...]
    public_boundaries: tuple[str, ...]
    obligations: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]
    editable_scope: dict[str, Any] | None
    semantic_impact: dict[str, Any]
    world_revision: str
    analyzer_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "TaskCapsule",
            "goal": self.goal,
            "target": self.target,
            "definition_source": self.definition_source,
            "direct_references": list(self.direct_references),
            "direct_callers": list(self.direct_callers),
            "transitive_callers": list(self.transitive_callers),
            "dependencies": list(self.dependencies),
            "uncertain_boundaries": list(self.uncertain_boundaries),
            "public_boundaries": list(self.public_boundaries),
            "current_obligations": list(self.obligations),
            "current_evidence": list(self.evidence),
            "editable_scope": self.editable_scope,
            "semantic_impact": self.semantic_impact,
            "world_revision": self.world_revision,
            "analyzer_version": self.analyzer_version,
        }


def compile_context(
    program: ProgramIR,
    target: str,
    *,
    goal: str,
    obligations: Iterable[Obligation] = (),
    evidence: Iterable[Evidence] = (),
    capability: EditCapability | None = None,
) -> TaskCapsule:
    entity = program.entity(target)
    direct_references = program.references_to(entity.id)
    uncertain = program.uncertain_references_to(entity.id)
    direct_caller_ids = program.callers_of(entity.id)
    transitive_ids = tuple(
        identifier
        for identifier in program.callers_of(entity.id, transitive=True)
        if identifier not in set(direct_caller_ids)
    )
    dependency_ids = program.dependencies_of(entity.id)
    direct_callers = tuple(
        _entity_context(program.entity(identifier)) for identifier in direct_caller_ids
    )
    transitive_callers = tuple(
        _entity_context(program.entity(identifier)) for identifier in transitive_ids
    )
    dependencies = tuple(
        _entity_context(program.entity(identifier)) for identifier in dependency_ids
    )
    relevant_obligations = tuple(
        item
        for item in obligations
        if entity.id in item.affected_entities
        or item.root_change.startswith("scan:")
    )
    relevant_evidence = tuple(
        item
        for item in evidence
        if any(
            dependency.kind == "entity" and dependency.key == entity.id
            for dependency in item.dependencies
        )
    )
    impact = analyze_impact(
        program,
        entity.id,
        obligations=relevant_obligations,
        evidence=relevant_evidence,
    )
    public_boundaries = tuple(
        sorted(
            {
                identifier
                for identifier in (entity.id, *direct_caller_ids, *transitive_ids)
                if program.entity(identifier).public
            }
        )
    )
    return TaskCapsule(
        goal=goal,
        target=_entity_context(entity),
        definition_source=source_read(program, entity.id),
        direct_references=tuple(_reference_context(item) for item in direct_references),
        direct_callers=direct_callers,
        transitive_callers=transitive_callers,
        dependencies=dependencies,
        uncertain_boundaries=tuple(_reference_context(item) for item in uncertain),
        public_boundaries=public_boundaries,
        obligations=tuple(item.to_dict() for item in relevant_obligations),
        evidence=tuple(item.to_dict() for item in relevant_evidence),
        editable_scope=capability.to_dict() if capability else None,
        semantic_impact=impact.to_dict(),
        world_revision=program.world_revision,
        analyzer_version=program.analyzer_version,
    )


def _entity_context(entity: Entity) -> dict[str, Any]:
    """Compact semantic coordinates; source and recovery fingerprints stay in ProgramIR."""
    return {
        "id": entity.id,
        "kind": entity.kind,
        "module": entity.module,
        "qualname": entity.qualname,
        "name": entity.name,
        "file": entity.file,
        "definition_span": entity.definition_span.to_dict(),
        "source_span": (
            entity.source_span.to_dict() if entity.source_span is not None else None
        ),
        "revision_hash": entity.revision_hash,
        "source_hash": entity.source_hash,
        "signature": entity.signature,
        "signature_source": entity.signature_source,
        "public": entity.public,
        "identity_status": entity.identity_status,
        "identity_score": entity.identity_score,
        "identity_reason": entity.identity_reason,
    }


def _reference_context(reference: Reference) -> dict[str, Any]:
    return {
        "id": reference.id,
        "target_id": reference.target_id,
        "possible_target_ids": list(reference.possible_target_ids),
        "file": reference.file,
        "span": reference.span.to_dict(),
        "kind": reference.kind,
        "usage": reference.usage,
        "resolution": reference.resolution,
        "provenance": reference.provenance,
        "expected": reference.expected,
        "qualifier": reference.qualifier,
        "qualifier_span": (
            reference.qualifier_span.to_dict()
            if reference.qualifier_span is not None
            else None
        ),
        "rename_on_target": reference.rename_on_target,
        "owner_id": reference.owner_id,
        "metadata": reference.metadata,
    }


def source_read(program: ProgramIR, entity_id: str) -> str:
    entity = program.entity(entity_id)
    path = Path(program.root) / entity.file
    source = path.read_text(encoding="utf-8").removeprefix("\ufeff")
    span = entity.source_span or entity.definition_span
    return _extract(source, span)


def _extract(source: str, span: Span) -> str:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    start = offsets[span.start.line - 1] + span.start.column
    end = offsets[span.end.line - 1] + span.end.column
    return source[start:end]
