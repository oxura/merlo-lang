from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from merlo.refactor import ChangeIR
from merlo.semantic_capsule import (
    SemanticCapsule,
    extract_semantic_capsule,
)
from merlo.transaction import load_transaction
from merlo.semantic_world import (
    WORLD_CONTRACT,
    WORLD_SCHEMA_VERSION,
    WorldError,
    SemanticWorld,
)

PATCH_EVIDENCE_SCHEMA_VERSION = 1
PATCH_EVIDENCE_CONTRACT = "merlo.patch-evidence.v1"
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze(item)
                for key, item in sorted(
                    value.items(),
                    key=lambda pair: str(pair[0]),
                )
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(
            sorted(
                (_freeze(item) for item in value),
                key=repr,
            )
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise _error(
            "PatchEvidenceNonFiniteNumber"
        )
    return value


def _json(value: Any) -> str:
    return json.dumps(
        _thaw(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _error(code: str) -> ValueError:
    return ValueError(code)


def _require_text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise _error(code)
    return value


def _require_hash(value: Any, code: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise _error(code)
    return value


def _normal_path(value: Any, code: str) -> str:
    path = _require_text(value, code)
    resolved = str(Path(path).resolve())
    if path != resolved:
        raise _error(code)
    return path


def _mapping_rows(value: Any, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, Mapping) for item in value):
        raise _error(f"PatchEvidenceInvalid:{field_name}")
    return tuple(_freeze(item) for item in value)


class PatchClaimStatus(str, Enum):
    PROVEN = "proven"



def _provenance(
    value: Any,
    function_names: tuple[str, str] | None = None,
) -> Any:
    ignored = {"digest", "hir_digest", "obligation_digest", "obligation_id", "owner_symbol_id", "owner_revision_id", "revision_id", "query_sha256"}
    if isinstance(value, Mapping):
        return {
            key: (
                "<target-name>"
                if key == "function"
                and isinstance(item, str)
                and function_names is not None
                and any(
                    item == function_name
                    or item.endswith(f"__{function_name}")
                    for function_name in function_names
                )
                else _provenance(item, function_names)
            )
            for key, item in sorted(value.items())
            if key not in ignored
        }
    if isinstance(value, (list, tuple)):
        rows = [_provenance(item, function_names) for item in value]
        return sorted(rows, key=_json)
    return value

@dataclass(frozen=True)
class PatchFileEvidence:
    path: str
    before_sha256: str
    after_sha256: str
    edits: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _normal_path(self.path, "PatchFileInvalidPath"))
        _require_hash(self.before_sha256, "PatchFileInvalidBeforeHash")
        _require_hash(self.after_sha256, "PatchFileInvalidAfterHash")
        rows = _mapping_rows(self.edits, "edits")
        keys: list[tuple[Any, ...]] = []
        for row in rows:
            required = {"start", "end", "replacement", "symbol_id", "kind", "syntax_id", "token_id", "token_ordinal"}
            if set(row) != required:
                raise _error("PatchFileEditSchemaMismatch")
            if type(row["start"]) is not int or type(row["end"]) is not int or row["start"] < 0 or row["end"] < row["start"]:
                raise _error("PatchFileInvalidEditSpan")
            if type(row["token_ordinal"]) is not int or row["token_ordinal"] < 0:
                raise _error("PatchFileInvalidTokenOrdinal")
            for key in ("replacement", "symbol_id", "kind", "syntax_id", "token_id"):
                _require_text(row[key], "PatchFileInvalidEditIdentity")
            keys.append((row["start"], row["end"], row["kind"], row["symbol_id"], row["syntax_id"], row["token_ordinal"], row["token_id"], row["replacement"]))
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise _error("PatchFileEditsNotCanonical")
        object.__setattr__(self, "edits", rows)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "before_sha256": self.before_sha256, "after_sha256": self.after_sha256, "edits": _thaw(self.edits)}

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "PatchFileEvidence":
        if (
            not isinstance(value, Mapping)
            or set(value)
            != {
                "path",
                "before_sha256",
                "after_sha256",
                "edits",
            }
            or not isinstance(
                value.get("edits"),
                list,
            )
        ):
            raise _error(
                "PatchFileSchemaMismatch"
            )
        return cls(
            value["path"],
            value["before_sha256"],
            value["after_sha256"],
            tuple(value["edits"]),
        )


@dataclass(frozen=True)
class PatchTargetLineage:
    before_symbol_id: str
    after_symbol_id: str
    before_qualified_name: str
    after_qualified_name: str
    before_revision_id: str
    after_revision_id: str
    before_interface_revision_id: str
    after_interface_revision_id: str
    before_implementation_revision_id: str
    after_implementation_revision_id: str
    before_source_path: str
    after_source_path: str

    def __post_init__(self) -> None:
        for field_name in ("before_symbol_id", "after_symbol_id", "before_qualified_name", "after_qualified_name", "before_revision_id", "after_revision_id", "before_interface_revision_id", "after_interface_revision_id", "before_implementation_revision_id", "after_implementation_revision_id"):
            _require_text(getattr(self, field_name), "PatchTargetInvalidIdentity")
        object.__setattr__(self, "before_source_path", _normal_path(self.before_source_path, "PatchTargetInvalidSourcePath"))
        object.__setattr__(self, "after_source_path", _normal_path(self.after_source_path, "PatchTargetInvalidSourcePath"))

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PatchTargetLineage":
        fields = set(cls.__dataclass_fields__)
        if not isinstance(value, Mapping) or set(value) != fields:
            raise _error("PatchTargetSchemaMismatch")
        return cls(**{name: value[name] for name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class PatchClaim:
    name: str
    status: PatchClaimStatus
    observed: str

    def __post_init__(self) -> None:
        _require_text(self.name, "PatchClaimInvalidName")
        if not isinstance(self.status, PatchClaimStatus):
            try:
                object.__setattr__(self, "status", PatchClaimStatus(self.status))
            except (TypeError, ValueError) as exc:
                raise _error("PatchClaimInvalidStatus") from exc
        _require_text(self.observed, "PatchClaimInvalidObservation")

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status.value, "observed": self.observed}

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "PatchClaim":
        if (
            not isinstance(value, Mapping)
            or set(value)
            != {"name", "status", "observed"}
        ):
            raise _error(
                "PatchClaimSchemaMismatch"
            )
        try:
            status = PatchClaimStatus(
                value["status"]
            )
        except (TypeError, ValueError) as exc:
            raise _error(
                "PatchClaimInvalidStatus"
            ) from exc
        return cls(
            value["name"],
            status,
            value["observed"],
        )


@dataclass(frozen=True)
class PatchEvidenceBundle:
    change_digest: str
    before_world_digest: str
    after_world_digest: str
    before_capsule_digest: str
    after_capsule_digest: str
    apply_result: Mapping[str, Any]
    files: tuple[PatchFileEvidence, ...]
    target: PatchTargetLineage
    obligations: tuple[Mapping[str, Any], ...]
    verification: Mapping[str, Any]
    claims: tuple[PatchClaim, ...]
    schema_version: int = PATCH_EVIDENCE_SCHEMA_VERSION
    contract: str = PATCH_EVIDENCE_CONTRACT
    digest: str = field(default="", compare=True)

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != PATCH_EVIDENCE_SCHEMA_VERSION:
            raise _error("PatchEvidenceSchemaVersionMismatch")
        if self.contract != PATCH_EVIDENCE_CONTRACT:
            raise _error("PatchEvidenceContractMismatch")
        for value, code in ((self.change_digest, "PatchEvidenceInvalidChangeDigest"), (self.before_world_digest, "PatchEvidenceInvalidBeforeWorldDigest"), (self.after_world_digest, "PatchEvidenceInvalidAfterWorldDigest"), (self.before_capsule_digest, "PatchEvidenceInvalidBeforeCapsuleDigest"), (self.after_capsule_digest, "PatchEvidenceInvalidAfterCapsuleDigest")):
            _require_hash(value, code)
        if not isinstance(self.apply_result, Mapping):
            raise _error("PatchEvidenceInvalidApplyResult")
        object.__setattr__(self, "apply_result", _freeze(self.apply_result))
        files = tuple(item if isinstance(item, PatchFileEvidence) else PatchFileEvidence.from_dict(item) for item in self.files)
        paths = tuple(item.path for item in files)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise _error("PatchEvidenceFilesNotCanonical")
        object.__setattr__(self, "files", files)
        if not isinstance(self.target, PatchTargetLineage):
            object.__setattr__(self, "target", PatchTargetLineage.from_dict(self.target))
        obligations = _mapping_rows(self.obligations, "obligations")
        obligation_ids = tuple(str(item.get("obligation_id", "")) for item in obligations)
        if any(not item for item in obligation_ids) or obligation_ids != tuple(sorted(obligation_ids)) or len(obligation_ids) != len(set(obligation_ids)):
            raise _error("PatchEvidenceObligationsNotCanonical")
        object.__setattr__(self, "obligations", obligations)
        if not isinstance(self.verification, Mapping):
            raise _error("PatchEvidenceInvalidVerification")
        object.__setattr__(self, "verification", _freeze(self.verification))
        claims = tuple(item if isinstance(item, PatchClaim) else PatchClaim.from_dict(item) for item in self.claims)
        names = tuple(item.name for item in claims)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise _error("PatchEvidenceClaimsNotCanonical")
        object.__setattr__(self, "claims", claims)
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise _error("PatchEvidenceDigestMismatch")
        object.__setattr__(self, "digest", expected)

    def _payload(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "contract": self.contract, "change_digest": self.change_digest, "before_world_digest": self.before_world_digest, "after_world_digest": self.after_world_digest, "before_capsule_digest": self.before_capsule_digest, "after_capsule_digest": self.after_capsule_digest, "apply_result": _thaw(self.apply_result), "files": [item.to_dict() for item in self.files], "target": self.target.to_dict(), "obligations": _thaw(self.obligations), "verification": _thaw(self.verification), "claims": [item.to_dict() for item in self.claims]}

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "PatchEvidenceBundle":
        fields = set(cls.__dataclass_fields__)
        if (
            not isinstance(value, Mapping)
            or set(value) != fields
            or not isinstance(
                value.get("apply_result"),
                Mapping,
            )
            or not isinstance(
                value.get("files"),
                list,
            )
            or not isinstance(
                value.get("target"),
                Mapping,
            )
            or not isinstance(
                value.get("obligations"),
                list,
            )
            or not isinstance(
                value.get("verification"),
                Mapping,
            )
            or not isinstance(
                value.get("claims"),
                list,
            )
        ):
            raise _error(
                "PatchEvidenceSchemaMismatch"
            )
        if value.get(
            "schema_version"
        ) != PATCH_EVIDENCE_SCHEMA_VERSION:
            raise _error(
                "PatchEvidenceSchemaVersionMismatch"
            )
        if value.get("contract") != (
            PATCH_EVIDENCE_CONTRACT
        ):
            raise _error(
                "PatchEvidenceContractMismatch"
            )
        supplied = value.get("digest")
        payload = {
            key: value[key]
            for key in fields
            if key != "digest"
        }
        if (
            not isinstance(supplied, str)
            or supplied != _digest(payload)
        ):
            raise _error(
                "PatchEvidenceDigestMismatch"
            )
        return cls(
            value["change_digest"],
            value["before_world_digest"],
            value["after_world_digest"],
            value["before_capsule_digest"],
            value["after_capsule_digest"],
            value["apply_result"],
            tuple(
                PatchFileEvidence.from_dict(item)
                for item in value["files"]
            ),
            PatchTargetLineage.from_dict(
                value["target"]
            ),
            tuple(value["obligations"]),
            value["verification"],
            tuple(
                PatchClaim.from_dict(item)
                for item in value["claims"]
            ),
            value["schema_version"],
            value["contract"],
            supplied,
        )

    @classmethod
    def from_json(cls, value: str) -> "PatchEvidenceBundle":
        try:
            payload = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise _error("PatchEvidenceJSONMismatch") from exc
        return cls.from_dict(payload)


def _world_payload_digest(world: SemanticWorld) -> str:
    payload = dict(world.data)
    payload.pop("world_digest", None)
    return _digest(payload)


def _source_key(path: Path, world: SemanticWorld) -> str:
    try:
        return str(path.resolve().relative_to(world.root.resolve()))
    except ValueError:
        return str(path.resolve())


def _symbol(
    world: SemanticWorld,
    symbol_id: str,
) -> Mapping[str, Any]:
    try:
        return world.resolve(symbol_id)
    except WorldError as exc:
        raise _error(
            "PatchEvidenceTargetBindingMismatch"
        ) from exc


def _target_after(world: SemanticWorld, before: Mapping[str, Any], new_name: str) -> Mapping[str, Any]:
    candidates = [item for item in world.data.get("symbols", ()) if item.get("module") == before.get("module") and item.get("name") == new_name]
    if len(candidates) != 1:
        raise _error("PatchEvidenceTargetRebindMismatch")
    return candidates[0]


def _read_sources(world: SemanticWorld) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for key in world.data.get("source_hashes", {}):
        path = world.root / key if not Path(key).is_absolute() else Path(key)
        if not path.is_file():
            raise _error("PatchEvidenceWorldNotFresh")
        result[key] = path.read_bytes()
    return result


def emit_patch_evidence(change: ChangeIR, before_world: SemanticWorld, after_world: SemanticWorld, apply_result: Mapping[str, Any], before_capsule: SemanticCapsule, after_capsule: SemanticCapsule) -> PatchEvidenceBundle:
    if not isinstance(change, ChangeIR) or change.status != "ready" or change.operation != "rename":
        raise _error("PatchEvidenceUnsupportedChange")
    if not isinstance(before_world, SemanticWorld) or not isinstance(after_world, SemanticWorld):
        raise _error("PatchEvidenceInvalidWorld")
    if not isinstance(before_capsule, SemanticCapsule) or not isinstance(after_capsule, SemanticCapsule):
        raise _error("PatchEvidenceInvalidCapsule")
    for world in (before_world, after_world):
        if world.data.get("schema_version") != WORLD_SCHEMA_VERSION or world.data.get("contract") != WORLD_CONTRACT:
            raise _error("PatchEvidenceWorldSchemaMismatch")
    change_payload = change.to_dict()
    supplied_change_digest = change_payload.pop("digest", None)
    if supplied_change_digest != _digest(change_payload):
        raise _error("PatchEvidenceChangeDigestMismatch")
    if (
        not isinstance(apply_result, Mapping)
        or set(apply_result)
        != {
            "committed",
            "operation",
            "target_id",
            "files",
            "edits",
            "transaction",
        }
        or apply_result.get("committed") is not True
        or not isinstance(
            apply_result.get("files"),
            list,
        )
        or not isinstance(
            apply_result.get("edits"),
            list,
        )
        or not isinstance(
            apply_result.get("transaction"),
            Mapping,
        )
    ):
        raise _error(
            "PatchEvidenceApplyNotCommitted"
        )
    if apply_result.get("operation") != change.operation or apply_result.get("target_id") != change.target.symbol_id:
        raise _error("PatchEvidenceApplyBindingMismatch")
    expected_edit_dicts = [item.to_dict() for item in change.edits]
    if apply_result.get("edits") != expected_edit_dicts:
        raise _error("PatchEvidenceApplyEditsMismatch")
    edit_paths = tuple(sorted({str(Path(item.path).resolve()) for item in change.edits}))
    receipt_paths = tuple(sorted(str(Path(item).resolve()) for item in apply_result.get("files", ())))
    if receipt_paths != edit_paths:
        raise _error("PatchEvidenceChangedFilesMismatch")
    transaction_result = apply_result[
        "transaction"
    ]
    transaction_fields = {
        "transaction_id",
        "transaction_digest",
        "action",
        "changed",
        "files",
        "resulting_hashes",
    }
    if (
        set(transaction_result)
        != transaction_fields
        or transaction_result.get("action")
        != "commit"
        or transaction_result.get("changed")
        is not True
        or not isinstance(
            transaction_result.get("files"),
            list,
        )
        or not isinstance(
            transaction_result.get(
                "resulting_hashes"
            ),
            Mapping,
        )
    ):
        raise _error(
            "PatchEvidenceTransactionMismatch"
        )
    transaction = load_transaction(
        before_world.root,
        transaction_result.get(
            "transaction_id"
        ),
    )
    transaction_paths = tuple(
        str(
            (
                before_world.root
                / item.path
            ).resolve()
        )
        for item in transaction.files
    )
    if (
        transaction.change_digest
        != change.digest
        or transaction.world_digest
        != before_world.digest
        or Path(transaction.root)
        != before_world.root.resolve()
        or transaction.digest
        != transaction_result.get(
            "transaction_digest"
        )
        or tuple(sorted(transaction_paths))
        != edit_paths
    ):
        raise _error(
            "PatchEvidenceTransactionBindingMismatch"
        )
    if before_world.digest != change.expected_world_digest or before_world.digest != before_world.data.get("world_digest") or _world_payload_digest(before_world) != before_world.digest:
        raise _error("PatchEvidenceBeforeWorldBindingMismatch")
    if after_world.digest != after_world.data.get("world_digest") or _world_payload_digest(after_world) != after_world.digest:
        raise _error("PatchEvidenceAfterWorldBindingMismatch")
    if before_world.root != after_world.root or before_world.data.get("source_hashes", {}).keys() != after_world.data.get("source_hashes", {}).keys():
        raise _error("PatchEvidenceWorldBindingMismatch")
    before_symbol = _symbol(before_world, change.target.symbol_id)
    for key in ("revision_id", "interface_revision_id", "implementation_revision_id"):
        if before_symbol.get(key) != getattr(change.target, key):
            raise _error("PatchEvidenceChangeTargetMismatch")
    if before_capsule.world_digest != before_world.digest or after_capsule.world_digest != after_world.digest:
        raise _error("PatchEvidenceCapsuleWorldMismatch")
    if before_capsule.target.symbol_id != before_symbol.get("symbol_id") or before_capsule.target_revision_id != before_symbol.get("revision_id"):
        raise _error("PatchEvidenceBeforeCapsuleTargetMismatch")
    old_name = change.metadata.get("old_name")
    new_name = change.metadata.get("new_name")
    if not isinstance(old_name, str) or not isinstance(new_name, str) or before_symbol.get("name") != old_name:
        raise _error("PatchEvidenceRenameMetadataMismatch")
    after_symbol = _target_after(after_world, before_symbol, new_name)
    if after_capsule.target.symbol_id != after_symbol.get("symbol_id") or after_capsule.target_revision_id != after_symbol.get("revision_id"):
        raise _error("PatchEvidenceAfterCapsuleTargetMismatch")
    expected_before_capsule = (
        extract_semantic_capsule(
            before_world,
            before_symbol["symbol_id"],
            goal=before_capsule.goal,
        )
    )
    expected_after_capsule = (
        extract_semantic_capsule(
            after_world,
            after_symbol["symbol_id"],
            goal=after_capsule.goal,
        )
    )
    if (
        expected_before_capsule.to_json()
        != before_capsule.to_json()
        or expected_after_capsule.to_json()
        != after_capsule.to_json()
    ):
        raise _error(
            "PatchEvidenceCapsuleExtractionMismatch"
        )
    before_source = _read_sources(before_world)
    after_source = _read_sources(after_world)
    before_hashes = before_world.data.get("source_hashes", {})
    after_hashes = after_world.data.get("source_hashes", {})
    resulting_hashes = transaction_result[
        "resulting_hashes"
    ]
    expected_transaction_files = tuple(
        item.path
        for item in transaction.files
    )
    if (
        tuple(transaction_result["files"])
        != expected_transaction_files
        or set(resulting_hashes)
        != set(expected_transaction_files)
        or any(
            resulting_hashes[item.path]
            != item.after_sha256
            for item in transaction.files
        )
    ):
        raise _error(
            "PatchEvidenceTransactionHashMismatch"
        )
    file_rows: list[PatchFileEvidence] = []
    changed_keys = {_source_key(Path(path_name), before_world) for path_name in edit_paths}
    if set(before_hashes) != set(after_hashes):
        raise _error("PatchEvidenceSourceSetMismatch")
    for key in before_hashes:
        if key in changed_keys:
            continue
        if hashlib.sha256(before_source[key]).hexdigest() != before_hashes[key] or hashlib.sha256(after_source[key]).hexdigest() != after_hashes[key] or before_hashes[key] != after_hashes[key]:
            raise _error("PatchEvidenceWorldNotFresh")
    for path_name in edit_paths:
        path = Path(path_name)
        key = _source_key(path, before_world)
        if key not in before_hashes or key not in after_hashes:
            raise _error("PatchEvidenceSourceBindingMismatch")
        current = after_source[key]
        try:
            text = current.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _error("PatchEvidenceSourceEncodingMismatch") from exc
        edits = [item for item in change.edits if str(Path(item.path).resolve()) == path_name]
        reconstructed = text
        for item in sorted(edits, key=lambda row: row.start):
            position = item.start
            if position < 0 or position + len(item.replacement) > len(reconstructed) or reconstructed[position:position + len(item.replacement)] != item.replacement:
                raise _error("PatchEvidenceEditSourceMismatch")
            reconstructed = reconstructed[:position] + old_name + reconstructed[position + len(item.replacement):]
        rebuilt = reconstructed
        for item in sorted(edits, key=lambda row: row.start, reverse=True):
            if item.end > len(rebuilt) or rebuilt[item.start:item.end] != old_name:
                raise _error("PatchEvidenceEditSourceMismatch")
            rebuilt = rebuilt[:item.start] + item.replacement + rebuilt[item.end:]
        if rebuilt.encode("utf-8") != current:
            raise _error("PatchEvidenceEditSourceMismatch")
        before_bytes = reconstructed.encode("utf-8")
        if hashlib.sha256(before_bytes).hexdigest() != before_hashes[key] or hashlib.sha256(current).hexdigest() != after_hashes[key]:
            raise _error("PatchEvidenceSourceHashMismatch")
        row_edits = tuple(sorted((
            {
                key: item.to_dict()[key]
                for key in ("start", "end", "replacement", "symbol_id", "kind", "syntax_id", "token_id", "token_ordinal")
            }
            for item in edits
        ), key=lambda row: (row["start"], row["end"], row["kind"], row["symbol_id"], row["syntax_id"], row["token_ordinal"], row["token_id"], row["replacement"])))
        file_rows.append(PatchFileEvidence(path_name, before_hashes[key], after_hashes[key], row_edits))
    before_target_path = str(Path(before_symbol["source"]["path"]).resolve())
    after_target_path = str(Path(after_symbol["source"]["path"]).resolve())
    if before_target_path != after_target_path or before_target_path not in edit_paths:
        raise _error("PatchEvidenceTargetSourceLineageMismatch")
    function_names = (
        before_capsule.target.name,
        after_capsule.target.name,
    )
    if _provenance(before_capsule.obligations, function_names) != _provenance(after_capsule.obligations, function_names):
        raise _error("PatchEvidenceObligationProvenanceMismatch")
    if set(before_capsule.verification) != set(after_capsule.verification) or _provenance(before_capsule.verification, function_names) != _provenance(after_capsule.verification, function_names):
        raise _error("PatchEvidenceVerificationProvenanceMismatch")
    target = PatchTargetLineage(
        before_symbol["symbol_id"],
        after_symbol["symbol_id"],
        before_symbol["qualified_name"],
        after_symbol["qualified_name"],
        before_symbol["revision_id"],
        after_symbol["revision_id"],
        before_symbol["interface_revision_id"],
        after_symbol["interface_revision_id"],
        before_symbol["implementation_revision_id"],
        after_symbol["implementation_revision_id"],
        before_target_path,
        after_target_path,
    )
    claims = tuple(sorted((PatchClaim("authorized_edits", PatchClaimStatus.PROVEN, "ChangeIR ready and edit identities matched before-world target"), PatchClaim("atomic_apply_receipt_observed", PatchClaimStatus.PROVEN, "ChangeIR.apply receipt committed with exact files and edits"), PatchClaim("world_rebuilt", PatchClaimStatus.PROVEN, "after-world digest and fresh source hashes matched rebuilt sources"), PatchClaim("target_rebound", PatchClaimStatus.PROVEN, "after-world target matched rename module and new name"), PatchClaim("evidence_carried", PatchClaimStatus.PROVEN, "obligation and verification provenance matched across capsules")), key=lambda item: item.name))
    return PatchEvidenceBundle(change.digest, before_world.digest, after_world.digest, before_capsule.digest, after_capsule.digest, dict(apply_result), tuple(sorted(file_rows, key=lambda item: item.path)), target, tuple(before_capsule.obligations), dict(before_capsule.verification), claims)


__all__ = ["PATCH_EVIDENCE_SCHEMA_VERSION", "PATCH_EVIDENCE_CONTRACT", "PatchClaimStatus", "PatchFileEvidence", "PatchTargetLineage", "PatchClaim", "PatchEvidenceBundle", "emit_patch_evidence"]
