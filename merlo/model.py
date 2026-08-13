from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, TypeAlias


SCHEMA_VERSION = 2


class IdentityStatus:
    EXACT = "Exact"
    PROBABLE = "Probable"
    AMBIGUOUS = "Ambiguous"
    NEW = "New"
    DELETED = "Deleted"


class Resolution:
    EXACT = "Exact"
    DERIVED = "Derived"
    CONDITIONAL = "Conditional"
    DYNAMIC = "Dynamic"
    UNKNOWN = "Unknown"


class Provenance:
    DIRECT_NAME = "DirectName"
    IMPORT = "Import"
    ALIAS = "Alias"
    ATTRIBUTE = "Attribute"
    STRING_LITERAL = "StringLiteral"
    REFLECTION = "Reflection"
    WILDCARD = "Wildcard"
    RUNTIME_OBSERVED = "RuntimeObserved"
    EXTERNAL_IMPORT = "ExternalImport"


class ObligationStatus:
    OPEN = "open"
    CLAIMED = "claimed"
    RESOLVED = "resolved"
    WAIVED = "waived"


class EvidenceStatus:
    VALID = "valid"
    STALE = "stale"


@dataclass(frozen=True, order=True)
class Position:
    line: int
    column: int

    def to_dict(self) -> dict[str, int]:
        return {"line": self.line, "column": self.column}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Position":
        return cls(line=int(value["line"]), column=int(value["column"]))


@dataclass(frozen=True)
class Span:
    start: Position
    end: Position

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start.to_dict(), "end": self.end.to_dict()}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Span":
        return cls(
            start=Position.from_dict(value["start"]),
            end=Position.from_dict(value["end"]),
        )


@dataclass(frozen=True)
class IdentityCandidate:
    locator: str
    score: float
    signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "locator": self.locator,
            "score": round(self.score, 6),
            "signals": list(self.signals),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "IdentityCandidate":
        return cls(
            locator=value["locator"],
            score=float(value["score"]),
            signals=tuple(value.get("signals", [])),
        )


@dataclass(frozen=True)
class IdentityRelation:
    status: str
    old_id: str | None
    new_id: str | None
    old_locator: str | None
    new_locator: str | None
    score: float = 0.0
    reason: str = ""
    candidates: tuple[IdentityCandidate, ...] = ()
    explicit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "old_id": self.old_id,
            "new_id": self.new_id,
            "old_locator": self.old_locator,
            "new_locator": self.new_locator,
            "score": round(self.score, 6),
            "reason": self.reason,
            "candidates": [item.to_dict() for item in self.candidates],
            "explicit": self.explicit,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "IdentityRelation":
        return cls(
            status=value["status"],
            old_id=value.get("old_id"),
            new_id=value.get("new_id"),
            old_locator=value.get("old_locator"),
            new_locator=value.get("new_locator"),
            score=float(value.get("score", 0.0)),
            reason=value.get("reason", ""),
            candidates=tuple(
                IdentityCandidate.from_dict(item)
                for item in value.get("candidates", [])
            ),
            explicit=bool(value.get("explicit", False)),
        )


@dataclass(frozen=True)
class IdentityHint:
    entity_id: str
    kind: str
    module: str
    qualname: str
    caused_by: str

    @property
    def locator(self) -> str:
        return f"{self.module}.{self.qualname}" if self.module else self.qualname

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "kind": self.kind,
            "module": self.module,
            "qualname": self.qualname,
            "caused_by": self.caused_by,
        }


@dataclass(frozen=True)
class Entity:
    id: str
    kind: str
    module: str
    qualname: str
    name: str
    file: str
    definition_span: Span
    revision_hash: str
    signature: str
    public: bool
    source_span: Span | None = None
    source_hash: str = ""
    signature_span: Span | None = None
    signature_source: str = ""
    identity_status: str = IdentityStatus.EXACT
    identity_score: float = 1.0
    identity_reason: str = ""
    identity_features: dict[str, Any] = field(default_factory=dict)

    @property
    def fqname(self) -> str:
        return f"{self.module}.{self.qualname}" if self.module else self.qualname

    @property
    def semantic_hash(self) -> str:
        """Backward-compatible name for the semantic Revision Hash."""

        return self.revision_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "module": self.module,
            "qualname": self.qualname,
            "name": self.name,
            "file": self.file,
            "definition_span": self.definition_span.to_dict(),
            "source_span": (self.source_span or self.definition_span).to_dict(),
            "source_hash": self.source_hash,
            "signature_span": self.signature_span.to_dict() if self.signature_span else None,
            "signature_source": self.signature_source,
            "revision_hash": self.revision_hash,
            "signature": self.signature,
            "public": self.public,
            "identity_status": self.identity_status,
            "identity_score": round(self.identity_score, 6),
            "identity_reason": self.identity_reason,
            "identity_features": self.identity_features,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Entity":
        definition_span = Span.from_dict(value["definition_span"])
        return cls(
            id=value["id"],
            kind=value["kind"],
            module=value["module"],
            qualname=value["qualname"],
            name=value["name"],
            file=value["file"],
            definition_span=definition_span,
            source_span=Span.from_dict(value.get("source_span", value["definition_span"])),
            source_hash=value.get("source_hash", ""),
            signature_span=(
                Span.from_dict(value["signature_span"])
                if value.get("signature_span")
                else None
            ),
            signature_source=value.get("signature_source", ""),
            revision_hash=value["revision_hash"],
            signature=value.get("signature", ""),
            public=bool(value.get("public", True)),
            identity_status=value.get("identity_status", IdentityStatus.EXACT),
            identity_score=float(value.get("identity_score", 1.0)),
            identity_reason=value.get("identity_reason", "legacy world"),
            identity_features=dict(value.get("identity_features", {})),
        )


@dataclass(frozen=True)
class Reference:
    target_id: str | None
    file: str
    span: Span
    kind: str
    expected: str
    owner_id: str | None = None
    rename_on_target: bool = True
    id: str = ""
    resolution: str = Resolution.EXACT
    provenance: str = Provenance.DIRECT_NAME
    usage: str = "Value"
    possible_target_ids: tuple[str, ...] = ()
    qualifier: str | None = None
    qualifier_span: Span | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def uncertain(self) -> bool:
        return self.resolution in {
            Resolution.CONDITIONAL,
            Resolution.DYNAMIC,
            Resolution.UNKNOWN,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target_id": self.target_id,
            "possible_target_ids": list(self.possible_target_ids),
            "file": self.file,
            "span": self.span.to_dict(),
            "kind": self.kind,
            "expected": self.expected,
            "owner_id": self.owner_id,
            "rename_on_target": self.rename_on_target,
            "resolution": self.resolution,
            "provenance": self.provenance,
            "usage": self.usage,
            "qualifier": self.qualifier,
            "qualifier_span": self.qualifier_span.to_dict() if self.qualifier_span else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Reference":
        return cls(
            id=value.get("id", ""),
            target_id=value.get("target_id"),
            possible_target_ids=tuple(value.get("possible_target_ids", [])),
            file=value["file"],
            span=Span.from_dict(value["span"]),
            kind=value["kind"],
            expected=value["expected"],
            owner_id=value.get("owner_id"),
            rename_on_target=bool(value.get("rename_on_target", True)),
            resolution=value.get("resolution", Resolution.EXACT),
            provenance=value.get("provenance", _legacy_provenance(value.get("kind", "name"))),
            usage=value.get("usage", "Value"),
            qualifier=value.get("qualifier"),
            qualifier_span=(
                Span.from_dict(value["qualifier_span"])
                if value.get("qualifier_span")
                else None
            ),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(frozen=True)
class CallArgument:
    kind: str
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CallArgument":
        return cls(kind=value["kind"], name=value.get("name"))


@dataclass(frozen=True)
class CallEdge:
    source_id: str | None
    target_id: str | None
    file: str
    line: int
    column: int = 0
    id: str = ""
    reference_id: str | None = None
    resolution: str = Resolution.EXACT
    provenance: str = Provenance.DIRECT_NAME
    possible_target_ids: tuple[str, ...] = ()
    arguments: tuple[CallArgument, ...] = ()
    span: Span | None = None

    @property
    def uncertain(self) -> bool:
        return self.resolution in {
            Resolution.CONDITIONAL,
            Resolution.DYNAMIC,
            Resolution.UNKNOWN,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "possible_target_ids": list(self.possible_target_ids),
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "reference_id": self.reference_id,
            "resolution": self.resolution,
            "provenance": self.provenance,
            "arguments": [argument.to_dict() for argument in self.arguments],
            "span": self.span.to_dict() if self.span else None,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CallEdge":
        return cls(
            id=value.get("id", ""),
            source_id=value.get("source_id"),
            target_id=value.get("target_id"),
            possible_target_ids=tuple(value.get("possible_target_ids", [])),
            file=value["file"],
            line=int(value["line"]),
            column=int(value.get("column", 0)),
            reference_id=value.get("reference_id"),
            resolution=value.get("resolution", Resolution.EXACT),
            provenance=value.get("provenance", Provenance.DIRECT_NAME),
            arguments=tuple(
                CallArgument.from_dict(item) for item in value.get("arguments", [])
            ),
            span=Span.from_dict(value["span"]) if value.get("span") else None,
        )


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    digest: str
    module: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "digest": self.digest, "module": self.module}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FileSnapshot":
        return cls(
            path=value["path"],
            digest=value["digest"],
            module=value.get("module", ""),
        )


@dataclass(frozen=True)
class SemanticHazard:
    kind: str
    symbol: str
    file: str
    line: int
    message: str
    reference_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "reference_id": self.reference_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SemanticHazard":
        return cls(
            kind=value["kind"],
            symbol=value["symbol"],
            file=value["file"],
            line=int(value["line"]),
            message=value["message"],
            reference_id=value.get("reference_id"),
        )


@dataclass(frozen=True)
class ProgramIR:
    root: str
    entities: tuple[Entity, ...]
    references: tuple[Reference, ...]
    calls: tuple[CallEdge, ...]
    files: tuple[FileSnapshot, ...]
    hazards: tuple[SemanticHazard, ...] = ()
    identity_relations: tuple[IdentityRelation, ...] = ()
    world_revision: str = ""
    analyzer_version: str = "python-0.2"

    def entity(self, identifier: str) -> Entity:
        exact = [
            entity
            for entity in self.entities
            if identifier in {entity.id, entity.fqname}
        ]
        if not exact:
            exact = [
                entity
                for entity in self.entities
                if identifier in {entity.qualname, entity.name}
            ]
        if len(exact) == 1:
            return exact[0]
        if not exact:
            raise KeyError(f"unknown semantic entity: {identifier}")
        names = ", ".join(sorted(entity.fqname for entity in exact))
        raise KeyError(f"ambiguous semantic entity {identifier!r}: {names}")

    def references_to(
        self, entity_id: str, *, include_possible: bool = False
    ) -> tuple[Reference, ...]:
        return tuple(
            ref
            for ref in self.references
            if ref.target_id == entity_id
            or (include_possible and entity_id in ref.possible_target_ids)
        )

    def uncertain_references_to(self, entity_id: str) -> tuple[Reference, ...]:
        return tuple(
            ref
            for ref in self.references_to(entity_id, include_possible=True)
            if ref.uncertain
        )

    def callers_of(self, entity_id: str, *, transitive: bool = False) -> tuple[str, ...]:
        direct = {
            edge.source_id
            for edge in self.calls
            if edge.target_id == entity_id and edge.source_id is not None
        }
        if not transitive:
            return tuple(sorted(direct))
        found = set(direct)
        frontier = list(direct)
        while frontier:
            target = frontier.pop()
            for edge in self.calls:
                if edge.target_id != target or edge.source_id is None:
                    continue
                if edge.source_id not in found:
                    found.add(edge.source_id)
                    frontier.append(edge.source_id)
        return tuple(sorted(found))

    def dependencies_of(self, entity_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    ref.target_id
                    for ref in self.references
                    if ref.owner_id == entity_id
                    and ref.target_id is not None
                    and ref.target_id != entity_id
                }
            )
        )

    def file_digest(self, path: str) -> str:
        for snapshot in self.files:
            if snapshot.path == path:
                return snapshot.digest
        raise KeyError(path)

    def file_for_module(self, module: str) -> str | None:
        for snapshot in self.files:
            if snapshot.module == module:
                return snapshot.path
        return None

    def reference_set_hash(self, entity_id: str) -> str:
        payload = [
            ref.to_dict()
            for ref in self.references_to(entity_id, include_possible=True)
        ]
        return _stable_hash(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "root": self.root,
            "world_revision": self.world_revision,
            "analyzer_version": self.analyzer_version,
            "entities": [entity.to_dict() for entity in self.entities],
            "references": [reference.to_dict() for reference in self.references],
            "calls": [call.to_dict() for call in self.calls],
            "files": [snapshot.to_dict() for snapshot in self.files],
            "hazards": [hazard.to_dict() for hazard in self.hazards],
            "identity_relations": [
                relation.to_dict() for relation in self.identity_relations
            ],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProgramIR":
        schema = int(value.get("schema", 1))
        if schema not in {1, SCHEMA_VERSION}:
            raise ValueError(
                f"unsupported ProgramIR schema {schema}; expected 1 or {SCHEMA_VERSION}"
            )
        program = cls(
            root=value["root"],
            entities=tuple(Entity.from_dict(item) for item in value["entities"]),
            references=tuple(
                Reference.from_dict(item) for item in value.get("references", [])
            ),
            calls=tuple(CallEdge.from_dict(item) for item in value.get("calls", [])),
            files=tuple(
                FileSnapshot.from_dict(item) for item in value.get("files", [])
            ),
            hazards=tuple(
                SemanticHazard.from_dict(item)
                for item in value.get("hazards", [])
            ),
            identity_relations=tuple(
                IdentityRelation.from_dict(item)
                for item in value.get("identity_relations", [])
            ),
            world_revision=value.get("world_revision", ""),
            analyzer_version=value.get("analyzer_version", "python-0.1"),
        )
        if program.world_revision:
            return program
        return program.with_world_revision()

    def with_world_revision(self) -> "ProgramIR":
        payload = {
            "analyzer": self.analyzer_version,
            "entities": [
                (entity.id, entity.revision_hash, entity.source_hash)
                for entity in self.entities
            ],
            "references": [ref.to_dict() for ref in self.references],
            "calls": [call.to_dict() for call in self.calls],
            "files": [snapshot.to_dict() for snapshot in self.files],
        }
        return ProgramIR(
            root=self.root,
            entities=self.entities,
            references=self.references,
            calls=self.calls,
            files=self.files,
            hazards=self.hazards,
            identity_relations=self.identity_relations,
            world_revision=_stable_hash(payload),
            analyzer_version=self.analyzer_version,
        )


@dataclass(frozen=True)
class EditCapability:
    target_ids: frozenset[str]
    operations: frozenset[str]
    allowed_files: frozenset[str] | None = None
    related_entity_ids: frozenset[str] = frozenset()
    allow_related_entities: bool = True
    max_files: int = 20
    max_entities: int = 50
    max_edits: int = 200
    allow_new_dependencies: bool = False
    forbidden_categories: frozenset[str] = frozenset(
        {"delete_entity", "public_api_break", "scope_expansion"}
    )

    @classmethod
    def for_operation(
        cls,
        operation: str,
        target_id: str,
        *,
        allowed_files: Iterable[str] | None = None,
        related_entity_ids: Iterable[str] = (),
        max_files: int = 20,
        max_entities: int = 50,
        max_edits: int = 200,
        allow_new_dependencies: bool = False,
        allow_public_api_break: bool = False,
    ) -> "EditCapability":
        normalized = (
            frozenset(path.replace("\\", "/") for path in allowed_files)
            if allowed_files is not None
            else None
        )
        forbidden = {"delete_entity", "scope_expansion"}
        if not allow_public_api_break:
            forbidden.add("public_api_break")
        if not allow_new_dependencies:
            forbidden.add("new_dependency")
        return cls(
            target_ids=frozenset({target_id}),
            operations=frozenset({operation}),
            allowed_files=normalized,
            related_entity_ids=frozenset(related_entity_ids),
            max_files=max_files,
            max_entities=max_entities,
            max_edits=max_edits,
            allow_new_dependencies=allow_new_dependencies,
            forbidden_categories=frozenset(forbidden),
        )

    @classmethod
    def rename(cls, target_id: str, **kwargs: Any) -> "EditCapability":
        return cls.for_operation("rename_symbol", target_id, **kwargs)

    @classmethod
    def move(cls, target_id: str, **kwargs: Any) -> "EditCapability":
        return cls.for_operation("move_symbol", target_id, **kwargs)

    @classmethod
    def change_signature(cls, target_id: str, **kwargs: Any) -> "EditCapability":
        return cls.for_operation("change_signature", target_id, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_ids": sorted(self.target_ids),
            "operations": sorted(self.operations),
            "allowed_files": sorted(self.allowed_files) if self.allowed_files else None,
            "related_entity_ids": sorted(self.related_entity_ids),
            "allow_related_entities": self.allow_related_entities,
            "max_files": self.max_files,
            "max_entities": self.max_entities,
            "max_semantic_edits": self.max_edits,
            "allow_new_dependencies": self.allow_new_dependencies,
            "forbidden_categories": sorted(self.forbidden_categories),
        }


@dataclass(frozen=True)
class RenameSymbol:
    id: str
    target_id: str
    new_name: str
    goal: str

    @property
    def operation(self) -> str:
        return "rename_symbol"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "operation": self.operation,
            "target_id": self.target_id,
            "new_name": self.new_name,
            "goal": self.goal,
        }


@dataclass(frozen=True)
class MoveSymbol:
    id: str
    target_id: str
    target_module: str
    goal: str

    @property
    def operation(self) -> str:
        return "move_symbol"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "operation": self.operation,
            "target_id": self.target_id,
            "target_module": self.target_module,
            "goal": self.goal,
        }


@dataclass(frozen=True)
class ChangeSignature:
    id: str
    target_id: str
    new_signature: str
    goal: str
    argument_values: tuple[tuple[str, str], ...] = ()

    @property
    def operation(self) -> str:
        return "change_signature"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "operation": self.operation,
            "target_id": self.target_id,
            "new_signature": self.new_signature,
            "goal": self.goal,
            "argument_values": dict(self.argument_values),
        }


SemanticChange: TypeAlias = RenameSymbol | MoveSymbol | ChangeSignature


@dataclass(frozen=True)
class SourceEdit:
    file: str
    span: Span
    expected: str
    replacement: str
    reason: str
    category: str = "semantic_edit"
    affected_entity_ids: tuple[str, ...] = ()
    allow_create: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "span": self.span.to_dict(),
            "expected": self.expected,
            "replacement": self.replacement,
            "reason": self.reason,
            "category": self.category,
            "affected_entity_ids": list(self.affected_entity_ids),
            "allow_create": self.allow_create,
        }


@dataclass(frozen=True)
class Obligation:
    id: str
    kind: str
    message: str
    files: tuple[str, ...] = ()
    severity: str = "error"
    depends_on: tuple[str, ...] = ()
    root_change: str = ""
    caused_by: tuple[str, ...] = ()
    affected_entities: tuple[str, ...] = ()
    status: str = ObligationStatus.OPEN
    evidence_required: tuple[str, ...] = ()
    possible_resolutions: tuple[str, ...] = ()

    @property
    def blocking(self) -> bool:
        return self.severity == "error" and self.status in {
            ObligationStatus.OPEN,
            ObligationStatus.CLAIMED,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "message": self.message,
            "files": list(self.files),
            "severity": self.severity,
            "status": self.status,
            "root_change": self.root_change,
            "caused_by": list(self.caused_by),
            "affected_entities": list(self.affected_entities),
            "evidence_required": list(self.evidence_required),
            "possible_resolutions": list(self.possible_resolutions),
            "depends_on": list(self.depends_on),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Obligation":
        return cls(
            id=value["id"],
            kind=value["kind"],
            message=value["message"],
            files=tuple(value.get("files", [])),
            severity=value.get("severity", "error"),
            status=value.get("status", ObligationStatus.OPEN),
            root_change=value.get("root_change", ""),
            caused_by=tuple(value.get("caused_by", [])),
            affected_entities=tuple(value.get("affected_entities", [])),
            evidence_required=tuple(value.get("evidence_required", [])),
            possible_resolutions=tuple(value.get("possible_resolutions", [])),
            depends_on=tuple(value.get("depends_on", [])),
        )


@dataclass(frozen=True)
class ObligationGraph:
    obligations: tuple[Obligation, ...]

    def __post_init__(self) -> None:
        by_id = {item.id: item for item in self.obligations}
        if len(by_id) != len(self.obligations):
            raise ValueError("duplicate obligation ID")
        for item in self.obligations:
            missing = set(item.depends_on) - set(by_id)
            if missing:
                raise ValueError(
                    f"obligation {item.id} has missing dependencies: {sorted(missing)}"
                )
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(identifier: str) -> None:
            if identifier in visiting:
                raise ValueError("obligation dependencies must form a DAG")
            if identifier in visited:
                return
            visiting.add(identifier)
            for dependency in by_id[identifier].depends_on:
                visit(dependency)
            visiting.remove(identifier)
            visited.add(identifier)

        for identifier in by_id:
            visit(identifier)

    @property
    def blocking(self) -> tuple[Obligation, ...]:
        return tuple(item for item in self.obligations if item.blocking)

    def inspect(self, identifier: str) -> Obligation:
        for item in self.obligations:
            if item.id == identifier:
                return item
        raise KeyError(f"unknown obligation: {identifier}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "obligations": [item.to_dict() for item in self.obligations],
            "roots": [
                item.id for item in self.obligations if not item.depends_on
            ],
        }


@dataclass(frozen=True)
class EvidenceDependency:
    kind: str
    key: str
    revision: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "key": self.key, "revision": self.revision}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvidenceDependency":
        return cls(
            kind=value["kind"], key=value["key"], revision=value["revision"]
        )


@dataclass(frozen=True)
class Evidence:
    kind: str
    level: str
    statement: str
    details: dict[str, Any] = field(default_factory=dict)
    id: str = ""
    status: str = EvidenceStatus.VALID
    produced_by: str = ""
    dependencies: tuple[EvidenceDependency, ...] = ()
    stale_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "level": self.level,
            "statement": self.statement,
            "details": self.details,
            "status": self.status,
            "produced_by": self.produced_by,
            "dependencies": [item.to_dict() for item in self.dependencies],
            "stale_reasons": list(self.stale_reasons),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Evidence":
        return cls(
            id=value.get("id", ""),
            kind=value["kind"],
            level=value["level"],
            statement=value["statement"],
            details=dict(value.get("details", {})),
            status=value.get("status", EvidenceStatus.VALID),
            produced_by=value.get("produced_by", ""),
            dependencies=tuple(
                EvidenceDependency.from_dict(item)
                for item in value.get("dependencies", [])
            ),
            stale_reasons=tuple(value.get("stale_reasons", [])),
        )


@dataclass(frozen=True)
class SemanticImpact:
    target_id: str
    direct_definitions: tuple[str, ...]
    direct_references: tuple[str, ...]
    direct_callers: tuple[str, ...]
    transitive_callers: tuple[str, ...]
    affected_files: tuple[str, ...]
    public_boundaries: tuple[str, ...]
    uncertain_references: tuple[str, ...]
    obligations: tuple[str, ...]
    invalidated_evidence: tuple[str, ...] = ()
    expected_edits: int = 0
    risk_factors: tuple[str, ...] = ()

    @property
    def potential_semantic_reach(self) -> int:
        return len(
            set(self.direct_definitions)
            | set(self.direct_callers)
            | set(self.transitive_callers)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "direct_definitions": list(self.direct_definitions),
            "direct_references": list(self.direct_references),
            "direct_callers": list(self.direct_callers),
            "transitive_callers": list(self.transitive_callers),
            "affected_files": list(self.affected_files),
            "public_boundaries": list(self.public_boundaries),
            "uncertain_references": list(self.uncertain_references),
            "obligations": list(self.obligations),
            "invalidated_evidence": list(self.invalidated_evidence),
            "expected_edits": self.expected_edits,
            "potential_semantic_reach": self.potential_semantic_reach,
            "risk_factors": list(self.risk_factors),
        }


@dataclass(frozen=True)
class ChangePlan:
    change: SemanticChange
    target: Entity
    edits: tuple[SourceEdit, ...]
    obligations: tuple[Obligation, ...]
    evidence: tuple[Evidence, ...]
    identity_hints: tuple[IdentityHint, ...] = ()
    impact: SemanticImpact | None = None
    inverse: dict[str, Any] | None = None

    @property
    def ready(self) -> bool:
        return not any(item.blocking for item in self.obligations)

    @property
    def affected_files(self) -> tuple[str, ...]:
        return tuple(sorted({edit.file for edit in self.edits}))

    @property
    def obligation_graph(self) -> ObligationGraph:
        return ObligationGraph(self.obligations)

    def to_dict(self) -> dict[str, Any]:
        change = self.change.to_dict()
        if isinstance(self.change, RenameSymbol):
            change["old_name"] = self.target.name
        if isinstance(self.change, MoveSymbol):
            change["source_module"] = self.target.module
        if isinstance(self.change, ChangeSignature):
            change["old_signature"] = self.target.signature
        return {
            "change": change,
            "status": "ready" if self.ready else "blocked",
            "target": self.target.to_dict(),
            "affected_files": list(self.affected_files),
            "edits": [edit.to_dict() for edit in self.edits],
            "obligation_graph": self.obligation_graph.to_dict(),
            "obligations": [item.to_dict() for item in self.obligations],
            "evidence": [item.to_dict() for item in self.evidence],
            "identity_hints": [item.to_dict() for item in self.identity_hints],
            "impact": self.impact.to_dict() if self.impact else None,
            "inverse": self.inverse,
        }


def _legacy_provenance(kind: str) -> str:
    return {
        "import": Provenance.IMPORT,
        "attribute": Provenance.ATTRIBUTE,
        "name": Provenance.DIRECT_NAME,
    }.get(kind, Provenance.DIRECT_NAME)


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
