from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from merlo.canonical_ast import CanonicalProgram
from merlo.runtime_contract import ALPHA_EFFECTS
from merlo.version import VERSIONS

CONCISE_APPLICATION_SCHEMA_VERSION = VERSIONS.frontend
CONCISE_APPLICATION_CONTRACT = "merlo.concise-application.v8"
CONCISE_SURFACE_VERSION = VERSIONS.language
_ALLOWED_EFFECTS = ALPHA_EFFECTS
_OWNERS = frozenset({"Text", "Bytes", "TextBuilder"})


class ConciseApplicationError(ValueError):
    """A concise program cannot be elaborated without guessing."""


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()




@dataclass(frozen=True)
class SourceOrigin:
    canonical_line: int
    path: str
    source_line: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_line": self.canonical_line,
            "path": self.path,
            "source_line": self.source_line,
        }


@dataclass(frozen=True)
class InferenceDecision:
    owner: str
    name: str
    kind: str
    type_name: str
    mutable: bool
    path: str
    line: int
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "name": self.name,
            "kind": self.kind,
            "type": self.type_name,
            "mutable": self.mutable,
            "path": self.path,
            "line": self.line,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class TaskBoundary:
    name: str
    parameters: tuple[tuple[str, str], ...]
    return_type: str
    effects: tuple[str, ...]
    capabilities: tuple[str, ...]
    requirements: tuple[str, ...]
    ensures: tuple[str, ...]
    path: str
    line: int
    public: bool

    @property
    def revision_id(self) -> str:
        return _digest(
            {
                "name": self.name,
                "parameters": self.parameters,
                "return_type": self.return_type,
                "effects": self.effects,
                "capabilities": self.capabilities,
                "requirements": self.requirements,
                "ensures": self.ensures,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameters": [list(item) for item in self.parameters],
            "return_type": self.return_type,
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
            "requirements": list(self.requirements),
            "ensures": list(self.ensures),
            "path": self.path,
            "line": self.line,
            "public": self.public,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class PublicInterface:
    module: str
    name: str
    kind: str
    parameters: tuple[tuple[str, str], ...]
    return_type: str | None
    effects: tuple[str, ...]
    capabilities: tuple[str, ...]
    requirements: tuple[str, ...]
    ensures: tuple[str, ...]

    @property
    def revision_id(self) -> str:
        return _digest(
            {
                "module": self.module,
                "name": self.name,
                "kind": self.kind,
                "parameters": self.parameters,
                "return_type": self.return_type,
                "effects": self.effects,
                "capabilities": self.capabilities,
                "requirements": self.requirements,
                "ensures": self.ensures,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "name": self.name,
            "kind": self.kind,
            "parameters": [list(item) for item in self.parameters],
            "return_type": self.return_type,
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
            "requirements": list(self.requirements),
            "ensures": list(self.ensures),
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class ConciseApplicationElaboration:
    entry_path: str
    modules: tuple[str, ...]
    source_sha256: str
    canonical_source: str
    canonical_program: CanonicalProgram
    machine_source: str
    concise_semantic_digest: str
    canonical_semantic_digest: str
    decisions: tuple[InferenceDecision, ...]
    tasks: tuple[TaskBoundary, ...]
    interfaces: tuple[PublicInterface, ...]
    origins: tuple[SourceOrigin, ...]
    interface_lock_path: str
    interface_lock_valid: bool
    canonical_reference_equal: bool

    @property
    def semantic_ast_equal(self) -> bool:
        return self.concise_semantic_digest == self.canonical_semantic_digest

    @property
    def interface_revision(self) -> str:
        return _digest([item.to_dict() for item in self.interfaces])

    @property
    def effects(self) -> tuple[str, ...]:
        return tuple(sorted({effect for task in self.tasks for effect in task.effects}))

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(sorted({capability for task in self.tasks for capability in task.capabilities}))

    @property
    def ambiguous_points(self) -> tuple[str, ...]:
        return ()

    @property
    def argument_parsing(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "task": task.name,
                "name": name,
                "type": type_name,
                "checked": True,
                "failure": "typed AppError",
            }
            for task in self.tasks
            for name, type_name in task.parameters
        )

    @property
    def ownership_transfers(self) -> tuple[str, ...]:
        transfers = []
        if "fs.read" in self.effects:
            transfers.append("fs.read returns owned Bytes")
        if any(item.type_name in _OWNERS or item.type_name.startswith("Vec[") for item in self.decisions):
            transfers.append("owned locals move into constructors and are dropped on every exit")
        return tuple(transfers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONCISE_APPLICATION_SCHEMA_VERSION,
            "contract": CONCISE_APPLICATION_CONTRACT,
            "entry_path": self.entry_path,
            "modules": list(self.modules),
            "source_sha256": self.source_sha256,
            "canonical_sha256": hashlib.sha256(self.canonical_source.encode()).hexdigest(),
            "machine_sha256": hashlib.sha256(self.machine_source.encode()).hexdigest(),
            "semantic_ast": {
                "concise_digest": self.concise_semantic_digest,
                "canonical_digest": self.canonical_semantic_digest,
                "equal": self.semantic_ast_equal,
            },
            "decisions": [item.to_dict() for item in self.decisions],
            "tasks": [item.to_dict() for item in self.tasks],
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
            "implicit_argument_parsing": list(self.argument_parsing),
            "ownership_transfers": list(self.ownership_transfers),
            "ambiguous_points": list(self.ambiguous_points),
            "interfaces": [item.to_dict() for item in self.interfaces],
            "interface_revision": self.interface_revision,
            "interface_lock_path": self.interface_lock_path,
            "interface_lock_valid": self.interface_lock_valid,
            "canonical_reference_equal": self.canonical_reference_equal,
            "origins": [item.to_dict() for item in self.origins],
            "invariants": {
                "no_any": True,
                "ambiguity_rejected": True,
                "effects_explicit": bool(self.tasks) and all(item.effects for item in self.tasks),
                "capabilities_closed": set(self.capabilities) <= _ALLOWED_EFFECTS,
                "ordinary_lifetime_annotations": 0,
                "manual_memory_operations": 0,
            },
        }


__all__ = [
    "CONCISE_APPLICATION_CONTRACT",
    "CONCISE_APPLICATION_SCHEMA_VERSION",
    "CONCISE_SURFACE_VERSION",
    "ConciseApplicationElaboration",
    "ConciseApplicationError",
    "InferenceDecision",
    "PublicInterface",
    "SourceOrigin",
    "TaskBoundary",
]
