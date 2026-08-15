from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from merlo.refactor import ChangeIR
from merlo.semantic_world import SemanticWorld, WorldError


SEMANTIC_IMPACT_SCHEMA_VERSION = 1
SEMANTIC_IMPACT_CONTRACT = "merlo.semantic-impact.v1"


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if isinstance(value, float) and not math.isfinite(value):
        raise WorldError("SemanticImpactInvalidNonFiniteNumber")
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _required(value: Mapping[str, Any], fields: set[str], error: str) -> None:
    if set(value) != fields:
        raise WorldError(error)


def _strings(
    values: Any,
    error: str,
) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise WorldError(error)
    result = tuple(values)
    if any(
        type(item) is not str or not item
        for item in result
    ):
        raise WorldError(error)
    if result != tuple(sorted(set(result))):
        raise WorldError(error)
    return result


@dataclass(frozen=True)
class ImpactSymbol:
    symbol_id: str
    revision_id: str
    qualified_name: str
    module: str
    exported: bool
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.symbol_id, self.revision_id, self.qualified_name, self.module):
            if type(value) is not str or not value:
                raise WorldError("SemanticImpactInvalidSymbol")
        if type(self.exported) is not bool:
            raise WorldError("SemanticImpactInvalidSymbol")
        object.__setattr__(self, "reasons", _strings(self.reasons, "SemanticImpactInvalidSymbolReasons"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "qualified_name": self.qualified_name,
            "module": self.module,
            "exported": self.exported,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "ImpactSymbol":
        expected = {
            "symbol_id",
            "revision_id",
            "qualified_name",
            "module",
            "exported",
            "reasons",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != expected
            or not isinstance(
                value.get("reasons"),
                list,
            )
        ):
            raise WorldError(
                "SemanticImpactSymbolSchemaMismatch"
            )
        return cls(
            value["symbol_id"],
            value["revision_id"],
            value["qualified_name"],
            value["module"],
            value["exported"],
            tuple(value["reasons"]),
        )


@dataclass(frozen=True)
class ImpactFile:
    path: str
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.path) is not str
            or not self.path
            or not Path(self.path).is_absolute()
            or str(Path(self.path).resolve())
            != self.path
        ):
            raise WorldError(
                "SemanticImpactInvalidFile"
            )
        object.__setattr__(self, "reasons", _strings(self.reasons, "SemanticImpactInvalidFileReasons"))

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "reasons": list(self.reasons)}

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "ImpactFile":
        if (
            not isinstance(value, Mapping)
            or set(value) != {"path", "reasons"}
            or not isinstance(
                value.get("reasons"),
                list,
            )
        ):
            raise WorldError(
                "SemanticImpactFileSchemaMismatch"
            )
        return cls(
            value["path"],
            tuple(value["reasons"]),
        )


@dataclass(frozen=True)
class ImpactInterface:
    module: str
    revision_id: str
    symbol_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.module) is not str or not self.module or type(self.revision_id) is not str or not self.revision_id:
            raise WorldError("SemanticImpactInvalidInterface")
        object.__setattr__(self, "symbol_ids", _strings(self.symbol_ids, "SemanticImpactInvalidInterfaceSymbols"))

    def to_dict(self) -> dict[str, Any]:
        return {"module": self.module, "revision_id": self.revision_id, "symbol_ids": list(self.symbol_ids)}

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "ImpactInterface":
        expected = {
            "module",
            "revision_id",
            "symbol_ids",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != expected
            or not isinstance(
                value.get("symbol_ids"),
                list,
            )
        ):
            raise WorldError(
                "SemanticImpactInterfaceSchemaMismatch"
            )
        return cls(
            value["module"],
            value["revision_id"],
            tuple(value["symbol_ids"]),
        )


@dataclass(frozen=True)
class ImpactTest:
    path: str
    name: str
    reasons: tuple[str, ...] = ()
    def __post_init__(self) -> None:

        if (
            type(self.path) is not str
            or not self.path
            or not Path(self.path).is_absolute()
            or str(Path(self.path).resolve())
            != self.path
            or type(self.name) is not str
            or not self.name
        ):
            raise WorldError(
                "SemanticImpactInvalidTest"
            )
        object.__setattr__(self, "reasons", _strings(self.reasons, "SemanticImpactInvalidTestReasons"))

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "name": self.name, "reasons": list(self.reasons)}

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "ImpactTest":
        expected = {"path", "name", "reasons"}
        if (
            not isinstance(value, Mapping)
            or set(value) != expected
            or not isinstance(
                value.get("reasons"),
                list,
            )
        ):
            raise WorldError(
                "SemanticImpactTestSchemaMismatch"
            )
        return cls(
            value["path"],
            value["name"],
            tuple(value["reasons"]),
        )


@dataclass(frozen=True)
class ImpactDiagnostic:
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.code) is not str or not self.code or type(self.message) is not str or not self.message or not isinstance(self.details, Mapping):
            raise WorldError("SemanticImpactInvalidDiagnostic")
        object.__setattr__(self, "details", _freeze(self.details))

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": _thaw(self.details)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ImpactDiagnostic":
        if not isinstance(value, Mapping):
            raise WorldError("SemanticImpactInvalidDiagnostic")
        _required(value, {"code", "message", "details"}, "SemanticImpactDiagnosticSchemaMismatch")
        return cls(value["code"], value["message"], value["details"])


@dataclass(frozen=True)
class ImpactEdge:
    source_symbol_id: str
    target_symbol_id: str
    reason: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (self.source_symbol_id, self.target_symbol_id, self.reason)):
            raise WorldError("SemanticImpactInvalidEdge")

    def to_dict(self) -> dict[str, str]:
        return {"source_symbol_id": self.source_symbol_id, "target_symbol_id": self.target_symbol_id, "reason": self.reason}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ImpactEdge":
        if not isinstance(value, Mapping):
            raise WorldError("SemanticImpactInvalidEdge")
        _required(value, {"source_symbol_id", "target_symbol_id", "reason"}, "SemanticImpactEdgeSchemaMismatch")
        return cls(value["source_symbol_id"], value["target_symbol_id"], value["reason"])


@dataclass(frozen=True)
class SemanticImpactReport:
    world_digest: str
    change_digest: str
    target_symbol_id: str
    target_revision_id: str
    target_interface_revision_id: str
    target_implementation_revision_id: str
    status: str
    directly_changed: tuple[ImpactSymbol, ...]
    transitively_affected: tuple[ImpactSymbol, ...]
    context_symbols: tuple[ImpactSymbol, ...]
    callers: tuple[str, ...]
    references: tuple[str, ...]
    callees: tuple[str, ...]
    dependencies: tuple[str, ...]
    edges: tuple[ImpactEdge, ...]
    files: tuple[ImpactFile, ...]
    tests: tuple[ImpactTest, ...]
    interfaces: tuple[ImpactInterface, ...]
    diagnostic: ImpactDiagnostic | None = None
    schema_version: int = SEMANTIC_IMPACT_SCHEMA_VERSION
    contract: str = SEMANTIC_IMPACT_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SEMANTIC_IMPACT_SCHEMA_VERSION:
            raise WorldError("SemanticImpactSchemaVersionMismatch")
        if self.contract != SEMANTIC_IMPACT_CONTRACT:
            raise WorldError("SemanticImpactContractMismatch")
        for value in (self.world_digest, self.change_digest, self.target_symbol_id, self.target_revision_id, self.target_interface_revision_id, self.target_implementation_revision_id):
            if type(value) is not str or not value:
                raise WorldError("SemanticImpactInvalidEnvelope")
        if type(self.status) is not str or self.status not in {"ready", "unsupported"}:
            raise WorldError("SemanticImpactInvalidStatus")
        for name in (
            "callers",
            "references",
            "callees",
            "dependencies",
        ):
            object.__setattr__(
                self,
                name,
                _strings(
                    getattr(self, name),
                    f"SemanticImpactInvalid{name.title()}",
                ),
            )
        record_types = {
            "directly_changed": ImpactSymbol,
            "transitively_affected": ImpactSymbol,
            "context_symbols": ImpactSymbol,
            "edges": ImpactEdge,
            "files": ImpactFile,
            "tests": ImpactTest,
            "interfaces": ImpactInterface,
        }
        for name, record_type in record_types.items():
            items = tuple(getattr(self, name))
            if any(not isinstance(item, record_type) for item in items):
                raise WorldError("SemanticImpactInvalidRecords")
            key = {
                "directly_changed": lambda item: item.symbol_id,
                "transitively_affected": lambda item: item.symbol_id,
                "context_symbols": (
                    lambda item: item.symbol_id
                ),
                "edges": lambda item: (item.source_symbol_id, item.target_symbol_id, item.reason),
                "files": lambda item: item.path,
                "tests": lambda item: (item.path, item.name),
                "interfaces": lambda item: (item.revision_id, item.module),
            }[name]
            canonical = tuple(sorted(items, key=key))
            if items != canonical or len({_json(item.to_dict()) for item in items}) != len(items):
                raise WorldError(f"SemanticImpactNonCanonical:{name}")
            object.__setattr__(self, name, items)
        direct_ids = tuple(item.symbol_id for item in self.directly_changed)
        transitive_ids = tuple(item.symbol_id for item in self.transitively_affected)
        context_symbol_ids = tuple(
            item.symbol_id
            for item in self.context_symbols
        )
        if (
            direct_ids != tuple(sorted(set(direct_ids)))
            or transitive_ids
            != tuple(sorted(set(transitive_ids)))
            or context_symbol_ids
            != tuple(sorted(set(context_symbol_ids)))
            or set(direct_ids) & set(transitive_ids)
            or set(direct_ids) & set(context_symbol_ids)
            or set(transitive_ids)
            & set(context_symbol_ids)
        ):
            raise WorldError(
                "SemanticImpactSymbolPartitionMismatch"
            )
        affected_ids = set(direct_ids) | set(
            transitive_ids
        )
        if (
            self.status == "ready"
            and self.target_symbol_id
            not in set(direct_ids)
        ):
            raise WorldError(
                "SemanticImpactTargetNotDirect"
            )
        if self.status == "ready":
            target_record = next(
                item
                for item in self.directly_changed
                if item.symbol_id
                == self.target_symbol_id
            )
            if (
                target_record.revision_id
                != self.target_revision_id
            ):
                raise WorldError(
                    "SemanticImpactTargetRevisionMismatch"
                )
        expected_context_ids = (
            set(self.callees)
            | set(self.dependencies)
        ) - affected_ids
        if set(context_symbol_ids) != (
            expected_context_ids
        ):
            raise WorldError(
                "SemanticImpactContextMismatch"
            )
        context_ids = (
            affected_ids
            | set(context_symbol_ids)
        )
        if (
            not set(self.callers).issubset(
                affected_ids
            )
            or not set(self.references).issubset(
                affected_ids
            )
            or any(
                edge.source_symbol_id
                not in context_ids
                or edge.target_symbol_id
                not in context_ids
                for edge in self.edges
            )
        ):
            raise WorldError(
                "SemanticImpactGraphMismatch"
            )
        if self.status == "unsupported":
            if (
                self.directly_changed
                or self.transitively_affected
                or self.context_symbols
                or self.callers
                or self.references
                or self.callees
                or self.dependencies
                or self.edges
                or self.files
                or self.tests
                or self.interfaces
                or self.diagnostic is None
            ):
                raise WorldError(
                    "SemanticImpactUnsupportedNotEmpty"
                )
        elif self.diagnostic is not None:
            raise WorldError("SemanticImpactReadyDiagnostic")
        if self.diagnostic is not None and not isinstance(self.diagnostic, ImpactDiagnostic):
            raise WorldError("SemanticImpactInvalidDiagnostic")
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise WorldError("SemanticImpactDigestMismatch")
        object.__setattr__(self, "digest", expected)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "world_digest": self.world_digest,
            "change_digest": self.change_digest,
            "target_symbol_id": self.target_symbol_id,
            "target_revision_id": self.target_revision_id,
            "target_interface_revision_id": self.target_interface_revision_id,
            "target_implementation_revision_id": self.target_implementation_revision_id,
            "status": self.status,
            "directly_changed": [item.to_dict() for item in self.directly_changed],
            "transitively_affected": [item.to_dict() for item in self.transitively_affected],
            "context_symbols": [
                item.to_dict()
                for item in self.context_symbols
            ],
            "callers": list(self.callers),
            "references": list(self.references),
            "callees": list(self.callees),
            "dependencies": list(self.dependencies),
            "edges": [item.to_dict() for item in self.edges],
            "files": [item.to_dict() for item in self.files],
            "tests": [item.to_dict() for item in self.tests],
            "interfaces": [item.to_dict() for item in self.interfaces],
            "diagnostic": self.diagnostic.to_dict() if self.diagnostic is not None else None,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())


    @property
    def public_interface_revision_ids(self) -> tuple[str, ...]:
        return tuple(
            item.revision_id
            for item in self.interfaces
        )

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "SemanticImpactReport":
        if not isinstance(value, Mapping):
            raise WorldError(
                "SemanticImpactSchemaMismatch"
            )
        required = set(cls.__dataclass_fields__)
        collection_fields = {
            "directly_changed",
            "transitively_affected",
            "context_symbols",
            "callers",
            "references",
            "callees",
            "dependencies",
            "edges",
            "files",
            "tests",
            "interfaces",
        }
        if (
            set(value) != required
            or any(
                not isinstance(value[field], list)
                for field in collection_fields
            )
        ):
            raise WorldError(
                "SemanticImpactSchemaMismatch"
            )
        payload = {
            key: value[key]
            for key in required
            if key != "digest"
        }
        if value.get("digest") != _digest(payload):
            raise WorldError(
                "SemanticImpactDigestMismatch"
            )
        diagnostic = value["diagnostic"]
        return cls(
            world_digest=value["world_digest"],
            change_digest=value["change_digest"],
            target_symbol_id=value[
                "target_symbol_id"
            ],
            target_revision_id=value[
                "target_revision_id"
            ],
            target_interface_revision_id=value[
                "target_interface_revision_id"
            ],
            target_implementation_revision_id=value[
                "target_implementation_revision_id"
            ],
            status=value["status"],
            directly_changed=tuple(
                ImpactSymbol.from_dict(item)
                for item in value["directly_changed"]
            ),
            transitively_affected=tuple(
                ImpactSymbol.from_dict(item)
                for item in value[
                    "transitively_affected"
                ]
            ),
            context_symbols=tuple(
                ImpactSymbol.from_dict(item)
                for item in value[
                    "context_symbols"
                ]
            ),
            callers=tuple(value["callers"]),
            references=tuple(value["references"]),
            callees=tuple(value["callees"]),
            dependencies=tuple(
                value["dependencies"]
            ),
            edges=tuple(
                ImpactEdge.from_dict(item)
                for item in value["edges"]
            ),
            files=tuple(
                ImpactFile.from_dict(item)
                for item in value["files"]
            ),
            tests=tuple(
                ImpactTest.from_dict(item)
                for item in value["tests"]
            ),
            interfaces=tuple(
                ImpactInterface.from_dict(item)
                for item in value["interfaces"]
            ),
            diagnostic=(
                ImpactDiagnostic.from_dict(
                    diagnostic
                )
                if diagnostic is not None
                else None
            ),
            schema_version=value["schema_version"],
            contract=value["contract"],
            digest=value["digest"],
        )

    @classmethod
    def from_json(cls, value: str) -> "SemanticImpactReport":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise WorldError("SemanticImpactSchemaMismatch") from exc
        return cls.from_dict(payload)


def _symbol_record(
    symbol: Mapping[str, Any],
    reasons: set[str],
) -> ImpactSymbol:
    string_fields = (
        "symbol_id",
        "revision_id",
        "qualified_name",
        "module",
    )
    if (
        any(
            type(symbol.get(field)) is not str
            or not symbol[field]
            for field in string_fields
        )
        or type(symbol.get("exported")) is not bool
    ):
        raise WorldError(
            "SemanticImpactWorldSchemaMismatch"
        )
    return ImpactSymbol(
        symbol["symbol_id"],
        symbol["revision_id"],
        symbol["qualified_name"],
        symbol["module"],
        symbol["exported"],
        tuple(sorted(reasons)),
    )


def _world_path(world: SemanticWorld, value: str | Path) -> str:
    path = Path(value)
    return str((path if path.is_absolute() else Path(world.root) / path).resolve())

def _change_digest(change_ir: ChangeIR) -> str:
    payload = change_ir.to_dict()
    actual = payload.pop("digest", None)
    expected = _digest(payload)
    if actual != expected:
        raise WorldError("ChangeIRDigestMismatch")
    return expected


def compute_semantic_impact(world: SemanticWorld, change_ir: ChangeIR) -> SemanticImpactReport:
    if not isinstance(world, SemanticWorld) or not isinstance(change_ir, ChangeIR):
        raise WorldError("SemanticImpactBindingMismatch")
    world.require_fresh()
    change_digest = _change_digest(change_ir)
    if change_ir.expected_world_digest != world.digest:
        raise WorldError("StaleWorld: ChangeIR belongs to another world")
    if change_ir.world is not None and change_ir.world.digest != world.digest:
        raise WorldError("SemanticImpactBindingMismatch")
    target = world.resolve(
        change_ir.target.symbol_id
    )
    for key in ("symbol_id", "revision_id", "interface_revision_id", "implementation_revision_id"):
        if str(target.get(key, "")) != str(getattr(change_ir.target, key)):
            raise WorldError(f"StaleWorld: target identity changed ({key})")
    if change_ir.status == "ready":
        root = Path(world.root).resolve()
        for edit in change_ir.edits:
            path = Path(edit.path)
            if not path.is_absolute() or str(path) != str(path.resolve()):
                raise WorldError("ChangeIRInvalidPath: edit paths must be normalized absolute paths")
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise WorldError("ChangeIRInvalidPath: edit escapes project root") from exc
            relative = str(path.relative_to(root))
            if relative not in world.data.get("source_hashes", {}) and str(path) not in world.data.get("source_hashes", {}):
                raise WorldError("StaleWorld: edit source is not in the expected world")
    envelope = dict(
        world_digest=world.digest,
        change_digest=change_digest,
        target_symbol_id=change_ir.target.symbol_id,
        target_revision_id=change_ir.target.revision_id,
        target_interface_revision_id=change_ir.target.interface_revision_id,
        target_implementation_revision_id=change_ir.target.implementation_revision_id,
    )
    if change_ir.status == "unsupported":
        diagnostic = change_ir.diagnostic
        assert diagnostic is not None
        return SemanticImpactReport(**envelope, status="unsupported", directly_changed=(), transitively_affected=(), context_symbols=(), callers=(), references=(), callees=(), dependencies=(), edges=(), files=(), tests=(), interfaces=(), diagnostic=ImpactDiagnostic(diagnostic.code, diagnostic.message, diagnostic.details))

    symbols = {str(item["symbol_id"]): item for item in world.data.get("symbols", ())}
    edited_paths = {_world_path(world, item.path) for item in change_ir.edits}
    direct_reasons: dict[str, set[str]] = {}
    for edit in change_ir.edits:
        if edit.symbol_id not in symbols:
            raise WorldError(f"UnknownSymbol: {edit.symbol_id}")
        direct_reasons.setdefault(edit.symbol_id, set()).add("edit")
    direct_ids = set(direct_reasons)

    calls = tuple(world.data.get("calls", ()))
    refs = tuple(world.data.get("references", ()))
    edges: set[tuple[str, str, str]] = set()
    callers: set[str] = set()
    references: set[str] = set()
    callees: set[str] = set()
    dependencies: set[str] = set()
    transitive: set[str] = set()

    frontier = set(direct_ids)
    seen = set(direct_ids)
    while frontier:
        discovered: set[str] = set()
        for call in calls:
            caller = str(
                call.get("caller_id", "")
            )
            callee = str(
                call.get("callee_id", "")
            )
            if callee in frontier and caller in symbols:
                callers.add(caller)
                edges.add(
                    (caller, callee, "caller")
                )
                if caller not in seen:
                    discovered.add(caller)
        for reference in refs:
            owner = str(
                reference.get("owner_id", "")
            )
            target_id = str(
                reference.get("target_id", "")
            )
            if (
                target_id in frontier
                and owner in symbols
            ):
                references.add(owner)
                edges.add(
                    (
                        owner,
                        target_id,
                        "reference",
                    )
                )
                if owner not in seen:
                    discovered.add(owner)
        transitive.update(discovered)
        seen.update(discovered)
        frontier = discovered

    for direct_id in sorted(direct_ids):
        for call in calls:
            caller, callee = str(call.get("caller_id", "")), str(call.get("callee_id", ""))
            if caller == direct_id and callee in symbols:
                callees.add(callee)
                edges.add((caller, callee, "callee"))
        try:
            for dependency in world.dependencies(direct_id):
                dependency_id = str(dependency["symbol_id"])
                if dependency_id in symbols:
                    dependencies.add(dependency_id)
                    edges.add((direct_id, dependency_id, "dependency"))
        except (KeyError, StopIteration):
            raise WorldError("SemanticImpactWorldSchemaMismatch")

    direct_records = tuple(
        sorted(
            (
                _symbol_record(
                    symbols[item],
                    direct_reasons[item],
                )
                for item in direct_ids
            ),
            key=lambda item: item.symbol_id,
        )
    )
    transitive_reasons: dict[
        str,
        set[str],
    ] = {
        item: set()
        for item in transitive
    }
    for source, target_id, reason in edges:
        if source in transitive:
            transitive_reasons[source].add(reason)
        if target_id in transitive:
            transitive_reasons[target_id].add(
                reason
            )
    transitive_records = tuple(
        sorted(
            (
                _symbol_record(
                    symbols[item],
                    transitive_reasons[item],
                )
                for item in transitive
            ),
            key=lambda item: item.symbol_id,
        )
    )
    context_ids = (
        callees | dependencies
    ) - direct_ids - transitive
    context_records = tuple(
        sorted(
            (
                _symbol_record(
                    symbols[item],
                    {"context"},
                )
                for item in context_ids
            ),
            key=lambda item: item.symbol_id,
        )
    )

    file_reasons: dict[str, set[str]] = {path: {"edit"} for path in edited_paths}
    for item in direct_records:
        file_reasons.setdefault(_world_path(world, symbols[item.symbol_id]["source"]["path"]), set()).add("direct")
    for item in transitive_records:
        file_reasons.setdefault(_world_path(world, symbols[item.symbol_id]["source"]["path"]), set()).add("affected")
    files = tuple(ImpactFile(path, tuple(sorted(reasons))) for path, reasons in sorted(file_reasons.items()))

    public_direct = [item for item in direct_records if item.exported]
    interface_map: dict[str, tuple[str, set[str]]] = {}
    for item in public_direct:
        symbol = symbols[item.symbol_id]
        key = str(symbol["interface_revision_id"])
        interface_map.setdefault(key, (str(symbol["module"]), set()))[1].add(item.symbol_id)
    interfaces = tuple(ImpactInterface(module, revision, tuple(sorted(ids))) for revision, (module, ids) in sorted(interface_map.items()))

    test_items: list[ImpactTest] = []
    affected_ids = direct_ids | transitive
    for raw in world.data.get("tests", ()):
        path = _world_path(world, raw["path"])
        reasons: set[str] = set()
        linked = set(raw.get("symbol_ids", raw.get("symbols", ())))
        if path in edited_paths:
            reasons.add("edit")
        if linked & affected_ids:
            reasons.add("symbol")
        if public_direct:
            reasons.add("public_interface")
        if reasons:
            test_items.append(ImpactTest(path, str(raw.get("name", Path(path).stem)), tuple(sorted(reasons))))
    tests = tuple(sorted(test_items, key=lambda item: (item.path, item.name)))
    return SemanticImpactReport(**envelope, status="ready", directly_changed=direct_records, transitively_affected=transitive_records, context_symbols=context_records, callers=tuple(sorted(callers)), references=tuple(sorted(references)), callees=tuple(sorted(callees)), dependencies=tuple(sorted(dependencies)), edges=tuple(ImpactEdge(source, target_id, reason) for source, target_id, reason in sorted(edges)), files=files, tests=tests, interfaces=interfaces)


__all__ = [
    "SEMANTIC_IMPACT_CONTRACT",
    "SEMANTIC_IMPACT_SCHEMA_VERSION",
    "ImpactDiagnostic",
    "ImpactEdge",
    "ImpactFile",
    "ImpactInterface",
    "ImpactSymbol",
    "ImpactTest",
    "SemanticImpactReport",
    "compute_semantic_impact",
]
