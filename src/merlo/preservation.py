from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping

from merlo.refactor import ChangeIR
from merlo.semantic_capsule import SemanticCapsule
from merlo.frontend.file_syntax import (
    parse_file_cst,
)
from merlo.semantic_world import WorldError

PRESERVATION_SCHEMA_VERSION = 1
PRESERVATION_CONTRACT = "merlo.preservation-report.v1"

_DIMENSIONS = (
    "identity",
    "source",
    "signature",
    "dependent_types",
    "callers",
    "callees",
    "dependencies",
    "effects",
    "capabilities",
    "ownership",
    "resources",
    "requirements",
    "ensures",
    "invariants",
    "holes",
    "obligations",
    "tests",
    "verification",
)


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
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("PreservationNonFiniteValue")
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _canonical_capsule(value: SemanticCapsule | Mapping[str, Any], label: str) -> SemanticCapsule:
    capsule = value if isinstance(value, SemanticCapsule) else SemanticCapsule.from_dict(value)
    # Reparse the canonical representation.  This catches a capsule assembled
    # through object.__setattr__ as well as malformed nested values.
    restored = SemanticCapsule.from_dict(capsule.to_dict())
    if restored.to_dict() != capsule.to_dict() or restored.digest != capsule.digest:
        raise ValueError(f"Preservation{label.title()}CapsuleDigestMismatch")
    return restored


def _canonical_change(
    value: ChangeIR | Mapping[str, Any],
) -> ChangeIR:
    try:
        change = (
            value
            if isinstance(value, ChangeIR)
            else ChangeIR.from_dict(value)
        )
        payload = change.to_dict()
        restored = ChangeIR.from_dict(payload)
    except WorldError:
        raise
    except (TypeError, ValueError) as exc:
        raise WorldError(
            "PreservationInvalidChangeIR"
        ) from exc
    if (
        restored.to_dict() != payload
        or restored.digest != change.digest
    ):
        raise WorldError("ChangeIRDigestMismatch")
    if change.status != "ready":
        raise WorldError(
            "PreservationChangeNotReady"
        )
    return restored


def _identity_ids(
    before: SemanticCapsule,
    after: SemanticCapsule,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    pairs = (
        (
            before.target.symbol_id,
            after.target.symbol_id,
        ),
        (
            before.target.revision_id,
            after.target.revision_id,
        ),
        (
            before.target.interface_revision_id,
            after.target.interface_revision_id,
        ),
        (
            before.target.implementation_revision_id,
            after.target.implementation_revision_id,
        ),
    )
    for index, (old, new) in enumerate(pairs):
        if old != new:
            token = f"<target-identity-{index}>"
            mapping[old] = token
            mapping[new] = token

    def match_rows(
        old_rows: list[Mapping[str, Any]],
        new_rows: list[Mapping[str, Any]],
        identity_fields: tuple[str, ...],
        label: str,
    ) -> None:
        def semantic_key(
            row: Mapping[str, Any],
        ) -> str:
            return _json(
                {
                    key: item
                    for key, item in row.items()
                    if key not in identity_fields
                }
            )

        new_by_key = {
            semantic_key(row): row
            for row in new_rows
        }
        for index, old_row in enumerate(old_rows):
            new_row = new_by_key.get(
                semantic_key(old_row)
            )
            if new_row is None:
                continue
            for key in identity_fields:
                old = old_row.get(key)
                new = new_row.get(key)
                if (
                    isinstance(old, str)
                    and isinstance(new, str)
                    and old != new
                ):
                    token = (
                        f"<{label}-identity-"
                        f"{index}-{key}>"
                    )
                    mapping[old] = token
                    mapping[new] = token

    obligation_fields = (
        "obligation_id",
        "revision_id",
        "owner_symbol_id",
        "owner_revision_id",
    )
    match_rows(
        _thaw(before.obligations),
        _thaw(after.obligations),
        obligation_fields,
        "obligation",
    )
    hole_fields = (
        "hole_id",
        "node_id",
        "revision_id",
        "owner_symbol_id",
        "owner_revision_id",
    )
    match_rows(
        _thaw(before.holes),
        _thaw(after.holes),
        hole_fields,
        "hole",
    )
    return mapping


def _normalize(
    value: Any,
    mapping: Mapping[str, str],
    *,
    verification: bool = False,
    function_names: tuple[str, str] | None = None,
) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if (
                verification
                and name
                in {
                    "digest",
                    "hir_digest",
                    "obligation_digest",
                }
            ):
                continue
            if (
                verification
                and name == "function"
                and function_names is not None
                and item in function_names
            ):
                result[name] = "<target-name>"
            else:
                result[name] = _normalize(
                    item,
                    mapping,
                    verification=verification,
                    function_names=function_names,
                )
        return result
    if isinstance(value, (list, tuple)):
        return [
            _normalize(
                item,
                mapping,
                verification=verification,
                function_names=function_names,
            )
            for item in value
        ]
    if isinstance(value, str):
        return mapping.get(value, value)
    return value


def _canonicalized(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonicalized(item) for key, item in value.items()}
    if isinstance(value, list):
        items = [_canonicalized(item) for item in value]
        if all(isinstance(item, Mapping) for item in items):
            return sorted(items, key=_json)
        return items
    return value


def _rename_source(
    source: str,
    change: ChangeIR,
) -> str:
    old_name = change.metadata.get("old_name")
    new_name = change.metadata.get("new_name")
    definition_edits = tuple(
        item
        for item in change.edits
        if item.kind == "definition"
    )
    if (
        not isinstance(old_name, str)
        or not isinstance(new_name, str)
        or len(definition_edits) != 1
    ):
        raise WorldError(
            "PreservationRenameEditMismatch"
        )
    definition_edit = definition_edits[0]
    cst = parse_file_cst(
        source,
        path="<semantic-capsule>",
    )
    declaration = next(
        (
            item
            for item in cst.declarations
            if any(
                token.kind == "identifier"
                and token.text == old_name
                for token in item.tokens
            )
        ),
        None,
    )
    if declaration is None:
        raise WorldError(
            "PreservationRenameEditMismatch"
        )
    significant = tuple(
        item
        for item in declaration.tokens
        if item.kind
        not in {
            "whitespace",
            "comment",
            "newline",
            "indent",
            "dedent",
            "eof",
        }
    )
    if (
        definition_edit.token_ordinal
        >= len(significant)
    ):
        raise WorldError(
            "PreservationRenameEditMismatch"
        )
    definition_token = significant[
        definition_edit.token_ordinal
    ]
    if (
        definition_token.text != old_name
        or definition_token.kind != "identifier"
    ):
        raise WorldError(
            "PreservationRenameEditMismatch"
        )
    source_offset = (
        definition_edit.start
        - definition_token.start
    )
    local_edits: list[tuple[int, int, str]] = []
    source_end = source_offset + len(source)
    for item in change.edits:
        if (
            item.path != definition_edit.path
            or item.start < source_offset
            or item.end > source_end
        ):
            continue
        start = item.start - source_offset
        end = item.end - source_offset
        if source[start:end] != old_name:
            raise WorldError(
                "PreservationRenameEditMismatch"
            )
        local_edits.append(
            (start, end, item.replacement)
        )
    if not local_edits:
        raise WorldError(
            "PreservationRenameEditMismatch"
        )
    result = source
    for start, end, replacement in sorted(
        local_edits,
        reverse=True,
    ):
        result = (
            result[:start]
            + replacement
            + result[end:]
        )
    return result

def _rename_authorization(change: ChangeIR, before: SemanticCapsule, after: SemanticCapsule) -> tuple[bool, str]:
    if change.operation != "rename":
        return False, "only rename identity metadata can authorize an identity delta"
    old_name = change.metadata.get("old_name")
    new_name = change.metadata.get("new_name")
    if not isinstance(old_name, str) or not isinstance(new_name, str) or before.target.name != old_name:
        return False, "rename metadata does not describe the before target"
    expected_qualified = f"{before.target.module}.{new_name}"
    expected_source = _rename_source(
        before.source,
        change,
    )
    if (
        after.target.name != new_name
        or after.target.qualified_name
        != expected_qualified
    ):
        return (
            False,
            "after target name is not the operation-declared rename",
        )
    if after.source != expected_source:
        return (
            False,
            "after source is not the exact operation-declared rename",
        )
    return True, "name, qualified name, source, and revision identities authorized by rename metadata"


def _value(capsule: SemanticCapsule, dimension: str) -> Any:
    if dimension == "identity":
        return capsule.target.to_dict()
    if dimension == "source":
        return capsule.source
    return _thaw(getattr(capsule, dimension))


@dataclass(frozen=True)
class PreservationFinding:
    dimension: str
    status: str
    before: Any
    after: Any
    reason: str

    def __post_init__(self) -> None:
        if self.dimension not in _DIMENSIONS:
            raise ValueError("PreservationFindingDimensionMismatch")
        if self.status not in {"preserved", "authorized_change", "violated"}:
            raise ValueError("PreservationFindingStatusMismatch")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("PreservationFindingReasonRequired")
        object.__setattr__(self, "before", _freeze(self.before))
        object.__setattr__(self, "after", _freeze(self.after))

    def to_dict(self) -> dict[str, Any]:
        return {"dimension": self.dimension, "status": self.status, "before": _thaw(self.before), "after": _thaw(self.after), "reason": self.reason}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PreservationFinding":
        required = {"dimension", "status", "before", "after", "reason"}
        if not isinstance(value, Mapping) or set(value) != required:
            raise ValueError("PreservationFindingSchemaMismatch")
        return cls(value["dimension"], value["status"], value["before"], value["after"], value["reason"])


@dataclass(frozen=True)
class PreservationReport:
    change_digest: str
    before_capsule_digest: str
    after_capsule_digest: str
    overall: str
    findings: tuple[PreservationFinding, ...]
    schema_version: int = PRESERVATION_SCHEMA_VERSION
    contract: str = PRESERVATION_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != PRESERVATION_SCHEMA_VERSION:
            raise ValueError("PreservationReportSchemaVersionMismatch")
        if self.contract != PRESERVATION_CONTRACT:
            raise ValueError("PreservationReportContractMismatch")
        for value in (
            self.change_digest,
            self.before_capsule_digest,
            self.after_capsule_digest,
        ):
            if (
                not isinstance(value, str)
                or re.fullmatch(
                    r"[0-9a-f]{64}",
                    value,
                )
                is None
            ):
                raise ValueError(
                    "PreservationReportInvalidBinding"
                )
        if self.overall not in {"preserved", "violated"}:
            raise ValueError("PreservationReportStatusMismatch")
        findings = tuple(item if isinstance(item, PreservationFinding) else PreservationFinding.from_dict(item) for item in self.findings)
        if tuple(item.dimension for item in findings) != _DIMENSIONS:
            raise ValueError("PreservationReportFindingsNotCanonical")
        expected = "preserved" if all(item.status != "violated" for item in findings) else "violated"
        if self.overall != expected:
            raise ValueError("PreservationReportOverallMismatch")
        object.__setattr__(self, "findings", findings)
        expected_digest = _digest(self._payload())
        if self.digest and self.digest != expected_digest:
            raise ValueError("PreservationReportDigestMismatch")
        object.__setattr__(self, "digest", expected_digest)

    def _payload(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "contract": self.contract, "change_digest": self.change_digest, "before_capsule_digest": self.before_capsule_digest, "after_capsule_digest": self.after_capsule_digest, "overall": self.overall, "findings": [item.to_dict() for item in self.findings]}

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "PreservationReport":
        required = set(cls.__dataclass_fields__)
        if (
            not isinstance(value, Mapping)
            or set(value) != required
            or not isinstance(
                value.get("findings"),
                list,
            )
        ):
            raise ValueError(
                "PreservationReportSchemaMismatch"
            )
        payload = {
            key: value[key]
            for key in required
            if key != "digest"
        }
        if value.get("digest") != _digest(payload):
            raise ValueError(
                "PreservationReportDigestMismatch"
            )
        return cls(
            value["change_digest"],
            value["before_capsule_digest"],
            value["after_capsule_digest"],
            value["overall"],
            tuple(
                PreservationFinding.from_dict(item)
                for item in value["findings"]
            ),
            value["schema_version"],
            value["contract"],
            value["digest"],
        )

    @classmethod
    def from_json(cls, value: str) -> "PreservationReport":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("PreservationReportJSONMismatch") from exc
        return cls.from_dict(payload)


def check_preservation(
    change: ChangeIR | Mapping[str, Any],
    before: SemanticCapsule | Mapping[str, Any],
    after: SemanticCapsule | Mapping[str, Any],
) -> PreservationReport:
    change_ir = _canonical_change(change)
    before_capsule = _canonical_capsule(
        before,
        "before",
    )
    after_capsule = _canonical_capsule(
        after,
        "after",
    )
    if (
        before_capsule.world_digest
        != change_ir.expected_world_digest
    ):
        raise WorldError(
            "PreservationBeforeWorldDigestMismatch"
        )
    target = change_ir.target
    if (
        before_capsule.target.symbol_id
        != target.symbol_id
        or before_capsule.target.revision_id
        != target.revision_id
        or before_capsule.target.interface_revision_id
        != target.interface_revision_id
        or before_capsule.target.implementation_revision_id
        != target.implementation_revision_id
    ):
        raise WorldError(
            "PreservationBeforeTargetRevisionMismatch"
        )
    if (
        before_capsule.target_revision_id
        != before_capsule.target.revision_id
    ):
        raise WorldError(
            "PreservationBeforeTargetRevisionMismatch"
        )
    if (
        after_capsule.target.module
        != before_capsule.target.module
        or after_capsule.target.kind
        != before_capsule.target.kind
        or after_capsule.target.public_boundary
        != before_capsule.target.public_boundary
    ):
        raise WorldError(
            "PreservationAfterTargetLineageMismatch"
        )
    if (
        after_capsule.target.qualified_name.rpartition(
            "."
        )[0]
        != before_capsule.target.module
    ):
        raise WorldError(
            "PreservationAfterTargetLineageMismatch"
        )
    if (
        after_capsule.target_revision_id
        != after_capsule.target.revision_id
    ):
        raise WorldError(
            "PreservationAfterTargetRevisionMismatch"
        )
    authorized, identity_reason = (
        _rename_authorization(
            change_ir,
            before_capsule,
            after_capsule,
        )
    )
    mapping = _identity_ids(
        before_capsule,
        after_capsule,
    )
    findings: list[PreservationFinding] = []
    function_names = (
        before_capsule.target.name,
        after_capsule.target.name,
    )
    for dimension in _DIMENSIONS:
        old = _value(before_capsule, dimension)
        new = _value(after_capsule, dimension)
        if dimension in {"identity", "source"}:
            if authorized and old != new:
                status = "authorized_change"
                reason = identity_reason
            elif old == new:
                status = "preserved"
                reason = "exactly preserved"
            else:
                status = "violated"
                reason = identity_reason
        else:
            if (
                dimension == "signature"
                and change_ir.operation == "rename"
            ):
                old_name = change_ir.metadata.get(
                    "old_name"
                )
                new_name = change_ir.metadata.get(
                    "new_name"
                )
                normalized_old = (
                    old.replace(
                        old_name,
                        new_name,
                        1,
                    )
                    if isinstance(old, str)
                    and isinstance(old_name, str)
                    and isinstance(new_name, str)
                    else old
                )
                normalized_new = new
            else:
                verification = (
                    dimension == "verification"
                )
                normalized_old = _canonicalized(
                    _normalize(
                        old,
                        mapping,
                        verification=verification,
                        function_names=function_names,
                    )
                )
                normalized_new = _canonicalized(
                    _normalize(
                        new,
                        mapping,
                        verification=verification,
                        function_names=function_names,
                    )
                )
            if normalized_old == normalized_new:
                status = "preserved"
                reason = (
                    "values match after authorized "
                    "identity normalization"
                    if old != new
                    else "exactly preserved"
                )
            else:
                status = "violated"
                reason = (
                    "behavioral contract or "
                    "verification evidence changed"
                )
        findings.append(
            PreservationFinding(
                dimension,
                status,
                old,
                new,
                reason,
            )
        )
    overall = (
        "violated"
        if any(
            item.status == "violated"
            for item in findings
        )
        else "preserved"
    )
    return PreservationReport(
        change_ir.digest,
        before_capsule.digest,
        after_capsule.digest,
        overall,
        tuple(findings),
    )

__all__ = [
    "PRESERVATION_CONTRACT",
    "PRESERVATION_SCHEMA_VERSION",
    "PreservationFinding",
    "PreservationReport",
    "check_preservation",
]
