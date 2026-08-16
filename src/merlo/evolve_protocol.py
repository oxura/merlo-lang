from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from merlo.patch_evidence import PatchEvidenceBundle, emit_patch_evidence
from merlo.preservation import PreservationReport, check_preservation
from merlo.refactor import ChangeIR, preview_rename
from merlo.semantic_capsule import SemanticCapsule, extract_semantic_capsule
from merlo.semantic_impact import SemanticImpactReport, compute_semantic_impact
from merlo.semantic_world import SemanticWorld, WorldError
from merlo.transaction import load_transaction

EVOLUTION_PLAN_SCHEMA_VERSION = 1
EVOLUTION_PLAN_CONTRACT = "merlo.evolution-plan.v1"
EVOLUTION_RESULT_SCHEMA_VERSION = 1
EVOLUTION_RESULT_CONTRACT = "merlo.evolution-result.v1"


def _json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise WorldError(
            "EvolutionInvalidJSONValue"
        ) from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze(item)
                for key, item in sorted(
                    value.items(),
                    key=lambda item: str(item[0]),
                )
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze(item)
            for item in value
        )
    if isinstance(value, (set, frozenset)):
        return tuple(
            sorted(
                (_freeze(item) for item in value),
                key=repr,
            )
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise WorldError(
            "EvolutionNonFiniteNumber"
        )
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def _diagnostic(value: Any) -> "EvolutionDiagnostic":
    if isinstance(value, EvolutionDiagnostic):
        return value
    return EvolutionDiagnostic.from_dict(value)


@dataclass(frozen=True)
class EvolutionDiagnostic:
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.code) is not str or not self.code:
            raise WorldError("EvolutionDiagnosticInvalidCode")
        if type(self.message) is not str or not self.message:
            raise WorldError("EvolutionDiagnosticInvalidMessage")
        if not isinstance(self.details, Mapping):
            raise WorldError("EvolutionDiagnosticInvalidDetails")
        object.__setattr__(self, "details", _freeze(self.details))

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": _thaw(self.details)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvolutionDiagnostic":
        if not isinstance(value, Mapping) or set(value) != {"code", "message", "details"}:
            raise WorldError("EvolutionDiagnosticSchemaMismatch")
        if not isinstance(value.get("details"), Mapping):
            raise WorldError("EvolutionDiagnosticSchemaMismatch")
        return cls(value["code"], value["message"], value["details"])


@dataclass(frozen=True)
class EvolutionPlan:
    world_digest: str
    change_ir: ChangeIR
    capsule: SemanticCapsule
    impact: SemanticImpactReport
    change_digest: str
    capsule_digest: str
    impact_digest: str
    schema_version: int = EVOLUTION_PLAN_SCHEMA_VERSION
    contract: str = EVOLUTION_PLAN_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != EVOLUTION_PLAN_SCHEMA_VERSION:
            raise WorldError("EvolutionPlanVersionMismatch")
        if self.contract != EVOLUTION_PLAN_CONTRACT:
            raise WorldError("EvolutionPlanContractMismatch")
        for name in ("world_digest", "change_digest", "capsule_digest", "impact_digest"):
            value = getattr(self, name)
            if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise WorldError(f"EvolutionPlanInvalid{name.title().replace('_', '')}")
        if not isinstance(self.change_ir, ChangeIR) or not isinstance(self.capsule, SemanticCapsule) or not isinstance(self.impact, SemanticImpactReport):
            raise WorldError("EvolutionPlanEnvelopeMismatch")
        if self.change_ir.status != "ready" or self.change_ir.operation != "rename":
            raise WorldError("EvolutionPlanUnsupportedChange")
        if self.capsule.world_digest != self.world_digest or self.impact.world_digest != self.world_digest:
            raise WorldError("EvolutionPlanWorldBindingMismatch")
        if self.change_ir.expected_world_digest != self.world_digest:
            raise WorldError("EvolutionPlanChangeWorldMismatch")
        if self.change_ir.digest != self.change_digest or self.capsule.digest != self.capsule_digest or self.impact.digest != self.impact_digest:
            raise WorldError("EvolutionPlanEnvelopeDigestMismatch")
        if self.impact.change_digest != self.change_digest:
            raise WorldError("EvolutionPlanImpactChangeMismatch")
        if self.capsule.target.symbol_id != self.change_ir.target.symbol_id or self.impact.target_symbol_id != self.change_ir.target.symbol_id:
            raise WorldError("EvolutionPlanTargetMismatch")
        if self.capsule.target_revision_id != self.change_ir.target.revision_id or self.impact.target_revision_id != self.change_ir.target.revision_id:
            raise WorldError("EvolutionPlanTargetRevisionMismatch")
        if self.change_ir.metadata.get("old_name") != self.capsule.target.name:
            raise WorldError("EvolutionPlanRenameSourceMismatch")
        if self.change_ir.target.interface_revision_id != self.impact.target_interface_revision_id or self.change_ir.target.implementation_revision_id != self.impact.target_implementation_revision_id:
            raise WorldError("EvolutionPlanTargetRevisionMismatch")
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise WorldError("EvolutionPlanDigestMismatch")
        object.__setattr__(self, "digest", expected)


    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "world_digest": self.world_digest,
            "change_ir": self.change_ir.to_dict(),
            "capsule": self.capsule.to_dict(),
            "impact": self.impact.to_dict(),
            "change_digest": self.change_digest,
            "capsule_digest": self.capsule_digest,
            "impact_digest": self.impact_digest,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, world: SemanticWorld | None = None) -> "EvolutionPlan":
        fields = {"schema_version", "contract", "world_digest", "change_ir", "capsule", "impact", "change_digest", "capsule_digest", "impact_digest", "digest"}
        if not isinstance(value, Mapping) or set(value) != fields or not all(isinstance(value.get(name), Mapping) for name in ("change_ir", "capsule", "impact")):
            raise WorldError("EvolutionPlanSchemaMismatch")
        if value.get("schema_version") != EVOLUTION_PLAN_SCHEMA_VERSION or value.get("contract") != EVOLUTION_PLAN_CONTRACT:
            raise WorldError("EvolutionPlanVersionMismatch")
        payload = {key: value[key] for key in fields if key != "digest"}
        if type(value.get("digest")) is not str or value["digest"] != _digest(payload):
            raise WorldError("EvolutionPlanDigestMismatch")
        try:
            change = ChangeIR.from_dict(value["change_ir"], world=world)
            capsule = SemanticCapsule.from_dict(value["capsule"])
            impact = SemanticImpactReport.from_dict(value["impact"])
            return cls(value["world_digest"], change, capsule, impact, value["change_digest"], value["capsule_digest"], value["impact_digest"], value["schema_version"], value["contract"], value["digest"])
        except WorldError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise WorldError("EvolutionPlanEnvelopeMismatch") from exc

    @classmethod
    def from_json(cls, value: str, *, world: SemanticWorld | None = None) -> "EvolutionPlan":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise WorldError("EvolutionPlanSchemaMismatch") from exc
        return cls.from_dict(payload, world=world)


@dataclass(frozen=True)
class EvolutionResult:
    status: str
    plan_digest: str
    world_digest: str
    after_world_digest: str | None
    change_digest: str
    capsule_digest: str
    impact_digest: str
    impact: SemanticImpactReport | None = None
    preservation: PreservationReport | None = None
    evidence: PatchEvidenceBundle | None = None
    transaction: Mapping[str, Any] | None = None
    rollback: Mapping[str, Any] | None = None
    diagnostic: EvolutionDiagnostic | None = None
    schema_version: int = EVOLUTION_RESULT_SCHEMA_VERSION
    contract: str = EVOLUTION_RESULT_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != EVOLUTION_RESULT_SCHEMA_VERSION:
            raise WorldError("EvolutionResultVersionMismatch")
        if self.contract != EVOLUTION_RESULT_CONTRACT:
            raise WorldError("EvolutionResultContractMismatch")
        if self.status not in {"committed", "rolled_back"}:
            raise WorldError("EvolutionResultInvalidStatus")
        for name in ("plan_digest", "world_digest", "change_digest", "capsule_digest", "impact_digest"):
            value = getattr(self, name)
            if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise WorldError(f"EvolutionResultInvalid{name.title().replace('_', '')}")
        if self.after_world_digest is not None and (type(self.after_world_digest) is not str or len(self.after_world_digest) != 64 or any(char not in "0123456789abcdef" for char in self.after_world_digest)):
            raise WorldError("EvolutionResultInvalidAfterWorldDigest")
        if self.impact is not None and not isinstance(self.impact, SemanticImpactReport):
            raise WorldError("EvolutionResultImpactMismatch")
        if self.preservation is not None and not isinstance(self.preservation, PreservationReport):
            raise WorldError("EvolutionResultPreservationMismatch")
        if self.evidence is not None and not isinstance(self.evidence, PatchEvidenceBundle):
            raise WorldError("EvolutionResultEvidenceMismatch")
        for name in ("transaction", "rollback"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, Mapping):
                    raise WorldError(f"EvolutionResult{name.title()}Mismatch")
                object.__setattr__(self, name, _freeze(value))
        if self.diagnostic is not None and not isinstance(self.diagnostic, EvolutionDiagnostic):
            object.__setattr__(self, "diagnostic", _diagnostic(self.diagnostic))
        if self.impact is not None and (self.impact.digest != self.impact_digest or self.impact.world_digest != self.world_digest):
            raise WorldError("EvolutionResultImpactBindingMismatch")
        if self.preservation is not None and (self.preservation.change_digest != self.change_digest or self.preservation.before_capsule_digest != self.capsule_digest):
            raise WorldError("EvolutionResultPreservationBindingMismatch")
        if self.evidence is not None and (
            self.evidence.change_digest != self.change_digest
            or self.evidence.before_world_digest != self.world_digest
            or self.evidence.after_world_digest != self.after_world_digest
            or self.evidence.before_capsule_digest != self.capsule_digest
        ):
            raise WorldError("EvolutionResultEvidenceBindingMismatch")
        if self.transaction is not None:
            _validate_receipt(self.transaction, "commit")
        if self.rollback is not None:
            _validate_receipt(self.rollback, "rollback")
        if self.status == "committed":
            if self.after_world_digest is None or self.impact is None or self.preservation is None or self.evidence is None or self.transaction is None or self.rollback is not None or self.diagnostic is not None:
                raise WorldError("EvolutionResultCommittedFieldsMismatch")
            if self.preservation.overall != "preserved":
                raise WorldError("EvolutionResultCommittedRequiresPreservation")
            if (
                self.impact.change_digest
                != self.change_digest
                or self.preservation.after_capsule_digest
                != self.evidence.after_capsule_digest
                or _thaw(self.transaction)
                != self.evidence.to_dict()[
                    "apply_result"
                ]["transaction"]
            ):
                raise WorldError(
                    "EvolutionResultBindingMismatch"
                )
        else:
            if self.after_world_digest is not None or self.rollback is None or self.diagnostic is None or self.transaction is None or self.impact is not None or self.preservation is not None or self.evidence is not None:
                raise WorldError("EvolutionResultRolledBackFieldsMismatch")
            if (
                self.transaction[
                    "transaction_id"
                ]
                != self.rollback["transaction_id"]
                or self.transaction[
                    "transaction_digest"
                ]
                != self.rollback[
                    "transaction_digest"
                ]
                or tuple(self.transaction["files"])
                != tuple(self.rollback["files"])
            ):
                raise WorldError(
                    "EvolutionResultRollbackBindingMismatch"
                )
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise WorldError("EvolutionResultDigestMismatch")
        object.__setattr__(self, "digest", expected)


    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "status": self.status,
            "plan_digest": self.plan_digest,
            "world_digest": self.world_digest,
            "after_world_digest": self.after_world_digest,
            "change_digest": self.change_digest,
            "capsule_digest": self.capsule_digest,
            "impact_digest": self.impact_digest,
            "impact": self.impact.to_dict() if self.impact is not None else None,
            "preservation": self.preservation.to_dict() if self.preservation is not None else None,
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
            "transaction": _thaw(self.transaction) if self.transaction is not None else None,
            "rollback": _thaw(self.rollback) if self.rollback is not None else None,
            "diagnostic": self.diagnostic.to_dict() if self.diagnostic is not None else None,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvolutionResult":
        fields = set(cls.__dataclass_fields__)
        if not isinstance(value, Mapping) or set(value) != fields:
            raise WorldError("EvolutionResultSchemaMismatch")
        payload = {key: value[key] for key in fields if key != "digest"}
        if type(value.get("digest")) is not str or value["digest"] != _digest(payload):
            raise WorldError("EvolutionResultDigestMismatch")
        try:
            return cls(
                value["status"], value["plan_digest"], value["world_digest"], value["after_world_digest"], value["change_digest"], value["capsule_digest"], value["impact_digest"],
                SemanticImpactReport.from_dict(value["impact"]) if value["impact"] is not None else None,
                PreservationReport.from_dict(value["preservation"]) if value["preservation"] is not None else None,
                PatchEvidenceBundle.from_dict(value["evidence"]) if value["evidence"] is not None else None,
                value["transaction"], value["rollback"], EvolutionDiagnostic.from_dict(value["diagnostic"]) if value["diagnostic"] is not None else None,
                value["schema_version"], value["contract"], value["digest"],
            )
        except WorldError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise WorldError("EvolutionResultEnvelopeMismatch") from exc

    @classmethod
    def from_json(cls, value: str) -> "EvolutionResult":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise WorldError("EvolutionResultSchemaMismatch") from exc
        return cls.from_dict(payload)


def _observed_diagnostic(exc: BaseException) -> EvolutionDiagnostic:
    message = str(exc) or exc.__class__.__name__
    return EvolutionDiagnostic(exc.__class__.__name__, message, {"exception": exc.__class__.__name__})


def _validate_receipt(value: Mapping[str, Any], action: str) -> None:
    fields = {"transaction_id", "transaction_digest", "action", "changed", "files", "resulting_hashes"}
    if set(value) != fields or value.get("action") != action or value.get("changed") is not True:
        raise WorldError("EvolutionResultTransactionMismatch")
    if not isinstance(value.get("transaction_id"), str) or not isinstance(value.get("transaction_digest"), str):
        raise WorldError("EvolutionResultTransactionMismatch")
    files = value.get("files")
    hashes = value.get("resulting_hashes")
    if not isinstance(files, (list, tuple)) or not isinstance(hashes, Mapping) or any(type(item) is not str for item in files):
        raise WorldError("EvolutionResultTransactionMismatch")
    transaction_id = value["transaction_id"]
    if len(transaction_id) != 64 or any(char not in "0123456789abcdef" for char in transaction_id):
        raise WorldError("EvolutionResultTransactionMismatch")
    transaction_digest = value["transaction_digest"]
    if len(transaction_digest) != 64 or any(char not in "0123456789abcdef" for char in transaction_digest):
        raise WorldError("EvolutionResultTransactionMismatch")
    if (
        tuple(files) != tuple(sorted(files))
        or len(files) != len(set(files))
        or set(hashes) != set(files)
        or any(
            type(key) is not str
            or type(item) is not str
            or len(item) != 64
            or any(
                char
                not in "0123456789abcdef"
                for char in item
            )
            for key, item in hashes.items()
        )
    ):
        raise WorldError(
            "EvolutionResultTransactionMismatch"
        )


def _same_json(left: Any, right: Any) -> bool:
    if hasattr(left, "to_json") and hasattr(right, "to_json"):
        return left.to_json() == right.to_json()
    return _json(left) == _json(right)


class VerifiedEvolutionProtocol:
    def __init__(self, world: SemanticWorld):
        if not isinstance(world, SemanticWorld):
            raise WorldError("EvolutionProtocolWorldRequired")
        self.world = world

    def preview_rename(self, target: str, new_name: str, goal: str = "") -> EvolutionPlan:
        if type(target) is not str or not target or type(new_name) is not str or type(goal) is not str:
            raise WorldError("EvolutionPreviewInvalidArguments")
        self.world.require_fresh()
        change = preview_rename(self.world, target, new_name)
        capsule = extract_semantic_capsule(self.world, change.target.symbol_id, goal=goal)
        impact = compute_semantic_impact(self.world, change)
        return EvolutionPlan(self.world.digest, change, capsule, impact, change.digest, capsule.digest, impact.digest)

    def _canonical_plan(self, plan: EvolutionPlan | Mapping[str, Any]) -> EvolutionPlan:
        if isinstance(plan, EvolutionPlan):
            # Re-run constructor invariants even for a dataclass instance whose nested objects were replaced.
            return EvolutionPlan.from_dict(plan.to_dict(), world=self.world)
        if isinstance(plan, Mapping):
            return EvolutionPlan.from_dict(plan, world=self.world)
        raise WorldError("EvolutionPlanExpected")

    def _recompute(self, plan: EvolutionPlan) -> tuple[ChangeIR, SemanticCapsule, SemanticImpactReport]:
        self.world.require_fresh()
        if self.world.digest != plan.world_digest:
            raise WorldError("StaleWorld: evolution plan belongs to another world")
        recomputed_change = preview_rename(self.world, plan.change_ir.target.symbol_id, str(plan.change_ir.metadata.get("new_name", "")))
        recomputed_capsule = extract_semantic_capsule(self.world, plan.change_ir.target.symbol_id, goal=plan.capsule.goal)
        recomputed_impact = compute_semantic_impact(self.world, recomputed_change)
        for name, supplied, actual in (("ChangeIR", plan.change_ir, recomputed_change), ("SemanticCapsule", plan.capsule, recomputed_capsule), ("SemanticImpactReport", plan.impact, recomputed_impact)):
            if not _same_json(supplied, actual):
                raise WorldError(f"EvolutionPlan{name}Mismatch")
        return recomputed_change, recomputed_capsule, recomputed_impact

    def _build_after(self) -> SemanticWorld:
        data = self.world.data
        return SemanticWorld.build(
            data.get("entry_path", self.world.root / "src" / "main.mlo"),
            state_path=self.world.state_path,
            lockfile=data.get("lockfile_path"),
            require_interface_lock=False,
        )

    def apply(self, plan: EvolutionPlan | Mapping[str, Any]) -> EvolutionResult:
        try:
            canonical = self._canonical_plan(plan)
            change, before_capsule, impact = self._recompute(canonical)
            if canonical.capsule_digest != before_capsule.digest or canonical.impact_digest != impact.digest:
                raise WorldError("EvolutionPlanEnvelopeDigestMismatch")
            apply_receipt = change.apply(self.world)
        except WorldError:
            raise
        except Exception as exc:
            raise WorldError(str(exc) or exc.__class__.__name__) from exc
        # From this point a committed receipt exists: every failure is an exact journal rollback.
        try:
            transaction = load_transaction(self.world.root, apply_receipt["transaction"]["transaction_id"])
            after_world = self._build_after()
            after_capsule = extract_semantic_capsule(after_world, f"{before_capsule.target.module}.{change.metadata['new_name']}", goal=before_capsule.goal)
            preservation = check_preservation(change, before_capsule, after_capsule)
            if preservation.overall != "preserved":
                raise WorldError("EvolutionPreservationViolated")
            evidence = emit_patch_evidence(change, self.world, after_world, apply_receipt, before_capsule, after_capsule)
            result = EvolutionResult("committed", canonical.digest, canonical.world_digest, after_world.digest, change.digest, before_capsule.digest, impact.digest, impact, preservation, evidence, apply_receipt["transaction"])
            after_world.save(self.world.state_path)
            self.world = after_world
            return result
        except Exception as exc:
            diagnostic = _observed_diagnostic(exc)
            try:
                transaction = load_transaction(self.world.root, apply_receipt["transaction"]["transaction_id"])
                rollback_receipt = transaction.rollback().to_dict()
                restored = self._build_after()
                if restored.digest != canonical.world_digest:
                    raise WorldError("EvolutionRollbackWorldDigestMismatch")
                restored.save(self.world.state_path)
                self.world = restored
            except Exception as rollback_exc:
                raise WorldError(f"EvolutionRollbackFailed: {rollback_exc}") from rollback_exc
            return EvolutionResult("rolled_back", canonical.digest, canonical.world_digest, None, change.digest, before_capsule.digest, impact.digest, transaction=apply_receipt["transaction"], rollback=rollback_receipt, diagnostic=diagnostic)


__all__ = [
    "EVOLUTION_PLAN_CONTRACT",
    "EVOLUTION_PLAN_SCHEMA_VERSION",
    "EVOLUTION_RESULT_CONTRACT",
    "EVOLUTION_RESULT_SCHEMA_VERSION",
    "EvolutionDiagnostic",
    "EvolutionPlan",
    "EvolutionResult",
    "VerifiedEvolutionProtocol",
]
