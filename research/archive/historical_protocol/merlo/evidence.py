from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any, Iterable

from research.archive.historical_protocol.merlo.model import (
    Evidence,
    EvidenceDependency,
    EvidenceStatus,
    ProgramIR,
)


def create_evidence(
    program: ProgramIR,
    change_id: str,
    kind: str,
    level: str,
    statement: str,
    *,
    details: dict[str, Any] | None = None,
    entity_ids: Iterable[str] = (),
    reference_targets: Iterable[str] = (),
    files: Iterable[str] = (),
    depend_on_world: bool = False,
) -> Evidence:
    dependencies: list[EvidenceDependency] = [
        EvidenceDependency("analyzer", "python", program.analyzer_version)
    ]
    for entity_id in sorted(set(entity_ids)):
        try:
            entity = program.entity(entity_id)
        except KeyError:
            continue
        dependencies.append(
            EvidenceDependency("entity", entity.id, entity.revision_hash)
        )
    for target_id in sorted(set(reference_targets)):
        dependencies.append(
            EvidenceDependency(
                "relation_set",
                target_id,
                program.reference_set_hash(target_id),
            )
        )
    for path in sorted(set(files)):
        try:
            digest = program.file_digest(path)
        except KeyError:
            continue
        dependencies.append(EvidenceDependency("file", path, digest))
    if depend_on_world:
        dependencies.append(
            EvidenceDependency("world", "ProgramIR", program.world_revision)
        )
    identifier = _evidence_id(change_id, kind, dependencies)
    return Evidence(
        id=identifier,
        kind=kind,
        level=level,
        statement=statement,
        details=dict(details or {}),
        status=EvidenceStatus.VALID,
        produced_by=change_id,
        dependencies=tuple(dependencies),
    )


def validate_evidence(evidence: Evidence, program: ProgramIR) -> Evidence:
    reasons: list[str] = []
    for dependency in evidence.dependencies:
        current = dependency_revision(program, dependency.kind, dependency.key)
        if current is None:
            reasons.append(f"missing {dependency.kind}:{dependency.key}")
        elif current != dependency.revision:
            reasons.append(
                f"changed {dependency.kind}:{dependency.key} "
                f"({dependency.revision[:12]} -> {current[:12]})"
            )
    if not reasons:
        return replace(
            evidence,
            status=EvidenceStatus.VALID,
            stale_reasons=(),
        )
    return replace(
        evidence,
        status=EvidenceStatus.STALE,
        stale_reasons=tuple(reasons),
    )


def rebind_evidence(evidence: Evidence, program: ProgramIR) -> Evidence:
    dependencies: list[EvidenceDependency] = []
    for dependency in evidence.dependencies:
        current = dependency_revision(program, dependency.kind, dependency.key)
        if current is None:
            continue
        dependencies.append(
            EvidenceDependency(dependency.kind, dependency.key, current)
        )
    return replace(
        evidence,
        id=_evidence_id(evidence.produced_by, evidence.kind, dependencies),
        dependencies=tuple(dependencies),
        status=EvidenceStatus.VALID,
        stale_reasons=(),
    )


def invalidated_evidence_ids(
    evidence_items: Iterable[Evidence], program: ProgramIR
) -> tuple[str, ...]:
    return tuple(
        sorted(
            item.id
            for item in evidence_items
            if validate_evidence(item, program).status == EvidenceStatus.STALE
        )
    )


def dependency_revision(
    program: ProgramIR, kind: str, key: str
) -> str | None:
    if kind == "analyzer":
        return program.analyzer_version
    if kind == "world":
        return program.world_revision
    if kind == "entity":
        try:
            return program.entity(key).revision_hash
        except KeyError:
            return None
    if kind == "relation_set":
        try:
            program.entity(key)
        except KeyError:
            return None
        return program.reference_set_hash(key)
    if kind == "file":
        try:
            return program.file_digest(key)
        except KeyError:
            return None
    return None


def _evidence_id(
    change_id: str,
    kind: str,
    dependencies: Iterable[EvidenceDependency],
) -> str:
    payload = "\0".join(
        [
            change_id,
            kind,
            *(
                f"{item.kind}:{item.key}:{item.revision}"
                for item in dependencies
            ),
        ]
    )
    return "ev_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
