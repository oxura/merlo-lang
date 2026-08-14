from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from research.archive.historical_protocol.merlo.analyzer import scan_python
from research.archive.historical_protocol.merlo.evidence import rebind_evidence, validate_evidence
from research.archive.historical_protocol.merlo.evolution import (
    apply_plan,
    plan_change_signature,
    plan_move,
    plan_rename,
)
from research.archive.historical_protocol.merlo.model import (
    ChangePlan,
    EditCapability,
    Evidence,
    IdentityHint,
    IdentityStatus,
    Obligation,
    ObligationStatus,
    ProgramIR,
)
from research.archive.historical_protocol.merlo.obligations import make_obligation


WORLD_SCHEMA = 2


class WorldError(Exception):
    pass


@dataclass
class SoftwareWorld:
    root: Path
    state_path: Path
    program: ProgramIR
    evolution_log: list[dict[str, Any]] = field(default_factory=list)
    obligations: dict[str, Obligation] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    plans: dict[str, dict[str, Any]] = field(default_factory=dict)
    identity_confirmations: dict[str, str] = field(default_factory=dict)

    @classmethod
    def scan(
        cls,
        root: str | Path,
        state_path: str | Path | None = None,
    ) -> "SoftwareWorld":
        root_path = Path(root).resolve()
        world_path = (
            Path(state_path).resolve()
            if state_path is not None
            else root_path / ".merlo" / "world.json"
        )
        previous: ProgramIR | None = None
        history: list[dict[str, Any]] = []
        obligations: dict[str, Obligation] = {}
        evidence: dict[str, Evidence] = {}
        plans: dict[str, dict[str, Any]] = {}
        confirmations: dict[str, str] = {}
        if world_path.exists():
            payload = _read_world(world_path)
            stored_root = Path(payload["root"]).resolve()
            if stored_root != root_path:
                raise WorldError(
                    f"Meldra world belongs to {stored_root}, not {root_path}"
                )
            previous = ProgramIR.from_dict(payload["program"])
            history = list(payload.get("evolution_log", []))
            obligations = {
                item["id"]: Obligation.from_dict(item)
                for item in payload.get("obligations", [])
            }
            evidence = {
                item["id"]: Evidence.from_dict(item)
                for item in payload.get("evidence", [])
            }
            plans = {
                item["change"]["id"]: item for item in payload.get("plans", [])
            }
            confirmations = dict(payload.get("identity_confirmations", {}))
        hints = _confirmation_hints(previous, confirmations)
        program = scan_python(root_path, previous=previous, identity_hints=hints)
        evidence = {
            identifier: validate_evidence(item, program)
            for identifier, item in evidence.items()
        }
        world = cls(
            root=root_path,
            state_path=world_path,
            program=program,
            evolution_log=history,
            obligations=obligations,
            evidence=evidence,
            plans=plans,
            identity_confirmations=confirmations,
        )
        world._record_identity_obligations()
        return world

    def save(self) -> None:
        payload = {
            "schema": WORLD_SCHEMA,
            "project": "Meldra",
            "root": str(self.root),
            "program": self.program.to_dict(),
            "evolution_log": self.evolution_log,
            "obligations": [
                item.to_dict()
                for item in sorted(self.obligations.values(), key=lambda value: value.id)
            ],
            "evidence": [
                item.to_dict()
                for item in sorted(self.evidence.values(), key=lambda value: value.id)
            ],
            "plans": [self.plans[key] for key in sorted(self.plans)],
            "identity_confirmations": dict(sorted(self.identity_confirmations.items())),
        }
        encoded = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{self.state_path.name}.",
                dir=self.state_path.parent,
                delete=False,
            ) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, self.state_path)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    def plan_rename(
        self,
        target: str,
        new_name: str,
        capability: EditCapability,
        *,
        goal: str = "Rename a semantic symbol and migrate all known references",
    ) -> ChangePlan:
        plan = plan_rename(
            self.program, target, new_name, capability, goal=goal
        )
        self.record_plan(plan)
        return plan

    def plan_move(
        self,
        target: str,
        target_module: str,
        capability: EditCapability,
        *,
        goal: str = "Move a semantic symbol and migrate static imports",
    ) -> ChangePlan:
        plan = plan_move(
            self.program, target, target_module, capability, goal=goal
        )
        self.record_plan(plan)
        return plan

    def plan_change_signature(
        self,
        target: str,
        new_signature: str,
        capability: EditCapability,
        *,
        argument_values: dict[str, str] | None = None,
        goal: str = "Change a function signature and migrate exact callers",
    ) -> ChangePlan:
        plan = plan_change_signature(
            self.program,
            target,
            new_signature,
            capability,
            argument_values=argument_values,
            goal=goal,
        )
        self.record_plan(plan)
        return plan

    def record_plan(self, plan: ChangePlan) -> None:
        self.plans[plan.change.id] = plan.to_dict()
        for obligation in plan.obligations:
            self.obligations[obligation.id] = obligation
        for item in plan.evidence:
            self.evidence[item.id] = item

    def apply(self, plan: ChangePlan, capability: EditCapability | None = None) -> tuple[str, ...]:
        root = self.root
        originals = {
            relative: (root / relative).read_bytes() if (root / relative).exists() else None
            for relative in plan.affected_files
        }
        previous_program = self.program
        previous_log = list(self.evolution_log)
        previous_obligations = dict(self.obligations)
        previous_evidence = dict(self.evidence)
        previous_plans = dict(self.plans)
        world_before = previous_program.world_revision
        try:
            changed_files = apply_plan(self.program, plan)
            self.program = scan_python(
                self.root,
                previous=previous_program,
                identity_hints=plan.identity_hints,
            )
            migrated = self.program.entity(plan.change.target_id)
            _verify_change_result(plan, migrated)
            rebound = [rebind_evidence(item, self.program) for item in plan.evidence]
            self.evidence = {
                identifier: validate_evidence(item, self.program)
                for identifier, item in self.evidence.items()
                if item.produced_by != plan.change.id
            }
            self.evidence.update({item.id: item for item in rebound})
            resolved_ids: list[str] = []
            for obligation in plan.obligations:
                if obligation.blocking:
                    continue
                resolved = replace(obligation, status=ObligationStatus.RESOLVED)
                self.obligations[resolved.id] = resolved
                resolved_ids.append(resolved.id)
            self.plans[plan.change.id] = plan.to_dict()
            record = {
                "change_id": plan.change.id,
                "goal": plan.change.goal,
                "committed_at": datetime.now(timezone.utc).isoformat(),
                "operation": plan.change.operation,
                "operations": [plan.change.to_dict()],
                "target_ids": [plan.target.id],
                "before_revisions": {plan.target.id: plan.target.revision_hash},
                "after_revisions": {migrated.id: migrated.revision_hash},
                "world_before": world_before,
                "world_after": self.program.world_revision,
                "capability": capability.to_dict() if capability else None,
                "obligations_before": [item.id for item in plan.obligations],
                "obligations_resolved": resolved_ids,
                "evidence_produced": [item.id for item in rebound],
                "semantic_impact": plan.impact.to_dict() if plan.impact else None,
                "source_files_touched": list(changed_files),
                "inverse": plan.inverse,
                "result": "committed",
            }
            self.evolution_log.append(record)
            self.save()
            return changed_files
        except Exception as exc:
            _restore_sources(root, originals)
            self.program = previous_program
            self.evolution_log = previous_log
            self.obligations = previous_obligations
            self.evidence = previous_evidence
            self.plans = previous_plans
            from research.archive.historical_protocol.merlo.evolution import ChangeBlocked

            if isinstance(exc, ChangeBlocked):
                raise
            rollback_record = {
                "change_id": plan.change.id,
                "goal": plan.change.goal,
                "committed_at": datetime.now(timezone.utc).isoformat(),
                "operation": plan.change.operation,
                "operations": [plan.change.to_dict()],
                "target_ids": [plan.target.id],
                "before_revisions": {plan.target.id: plan.target.revision_hash},
                "after_revisions": {},
                "world_before": world_before,
                "world_after": world_before,
                "capability": capability.to_dict() if capability else None,
                "obligations_before": [item.id for item in plan.obligations],
                "obligations_resolved": [],
                "evidence_produced": [],
                "semantic_impact": plan.impact.to_dict() if plan.impact else None,
                "source_files_touched": list(plan.affected_files),
                "inverse": plan.inverse,
                "result": "rolled_back",
                "error": str(exc),
            }
            self.evolution_log.append(rollback_record)
            try:
                self.save()
            except Exception:
                self.evolution_log = previous_log
            raise WorldError(f"semantic transaction rolled back: {exc}") from exc

    def confirm_identity(self, new_entity: str, old_entity_id: str) -> Entity:
        entity = self.program.entity(new_entity)
        valid_relation = any(
            relation.old_id == old_entity_id
            and (
                (
                    relation.status == IdentityStatus.PROBABLE
                    and relation.new_id == entity.id
                    and relation.new_locator == entity.fqname
                )
                or (
                    relation.status == IdentityStatus.AMBIGUOUS
                    and any(
                        candidate.locator == entity.fqname
                        for candidate in relation.candidates
                    )
                )
            )
            for relation in self.program.identity_relations
        )
        if not valid_relation:
            raise WorldError(
                f"{old_entity_id} is not a reviewable predecessor of {entity.fqname}"
            )
        self.identity_confirmations[entity.fqname] = old_entity_id
        hint = IdentityHint(
            entity_id=old_entity_id,
            kind=entity.kind,
            module=entity.module,
            qualname=entity.qualname,
            caused_by="manual_identity_confirmation",
        )
        self.program = scan_python(
            self.root,
            previous=self.program,
            identity_hints=(hint,),
        )
        confirmed = self.program.entity(old_entity_id)
        for identifier, obligation in tuple(self.obligations.items()):
            if (
                obligation.kind in {"AmbiguousIdentity", "ProbableIdentity"}
                and (
                    entity.id in obligation.affected_entities
                    or entity.fqname in obligation.affected_entities
                )
            ):
                self.obligations[identifier] = replace(
                    obligation, status=ObligationStatus.RESOLVED
                )
        self.save()
        return confirmed

    def list_obligations(
        self,
        *,
        change_id: str | None = None,
        status: str | None = None,
    ) -> tuple[Obligation, ...]:
        values = self.obligations.values()
        return tuple(
            sorted(
                (
                    item
                    for item in values
                    if (change_id is None or item.root_change == change_id)
                    and (status is None or item.status == status)
                ),
                key=lambda item: (item.root_change, item.kind, item.id),
            )
        )

    def obligation(self, identifier: str) -> Obligation:
        try:
            return self.obligations[identifier]
        except KeyError as exc:
            raise KeyError(f"unknown obligation: {identifier}") from exc

    def evidence_item(self, identifier: str) -> Evidence:
        try:
            return self.evidence[identifier]
        except KeyError as exc:
            raise KeyError(f"unknown evidence: {identifier}") from exc

    def summary(self) -> dict[str, Any]:
        uncertain = sum(1 for item in self.program.references if item.uncertain)
        return {
            "project": "Meldra",
            "stage": "0.2",
            "root": str(self.root),
            "world_revision": self.program.world_revision,
            "entities": len(self.program.entities),
            "references": len(self.program.references),
            "uncertain_references": uncertain,
            "call_edges": len(self.program.calls),
            "hazards": len(self.program.hazards),
            "open_obligations": sum(
                1 for item in self.obligations.values() if item.blocking
            ),
            "valid_evidence": sum(
                1 for item in self.evidence.values() if item.status == "valid"
            ),
            "stale_evidence": sum(
                1 for item in self.evidence.values() if item.status == "stale"
            ),
            "evolutions": len(self.evolution_log),
            "state": str(self.state_path),
        }

    def _record_identity_obligations(self) -> None:
        change_id = f"scan:{self.program.world_revision[:16]}"
        for relation in self.program.identity_relations:
            if relation.status != IdentityStatus.AMBIGUOUS:
                continue
            obligation = make_obligation(
                change_id,
                "AmbiguousIdentity",
                relation.reason,
                affected_entities=tuple(
                    candidate.locator for candidate in relation.candidates
                ),
                evidence_required=("IdentityConfirmation",),
                possible_resolutions=(
                    "confirm one candidate explicitly",
                    "treat all candidates as new entities",
                ),
            )
            self.obligations[obligation.id] = obligation


def _confirmation_hints(
    previous: ProgramIR | None,
    confirmations: dict[str, str],
) -> tuple[IdentityHint, ...]:
    if previous is None:
        return ()
    previous_by_id = {entity.id: entity for entity in previous.entities}
    hints: list[IdentityHint] = []
    if previous.analyzer_version == "python-0.1":
        hints.extend(
            IdentityHint(
                entity_id=entity.id,
                kind=entity.kind,
                module=entity.module,
                qualname=entity.qualname,
                caused_by="world_schema_v1_to_v2_migration",
            )
            for entity in previous.entities
        )
    for locator, entity_id in confirmations.items():
        prior = previous_by_id.get(entity_id)
        if prior is None:
            continue
        module, _separator, qualname = locator.rpartition(".")
        if not module:
            module = prior.module
            qualname = locator
        hints.append(
            IdentityHint(
                entity_id=entity_id,
                kind=prior.kind,
                module=module,
                qualname=qualname,
                caused_by="persisted_identity_confirmation",
            )
        )
    return tuple(hints)


def _verify_change_result(plan: ChangePlan, entity: Entity) -> None:
    operation = plan.change.operation
    if operation == "rename_symbol" and entity.name != plan.change.new_name:
        raise WorldError("renamed declaration did not retain semantic identity")
    if operation == "move_symbol" and entity.module != plan.change.target_module:
        raise WorldError("moved declaration did not retain semantic identity")
    if operation == "change_signature" and entity.signature_source != plan.change.new_signature:
        raise WorldError("signature change did not retain semantic identity")
    if entity.identity_status != IdentityStatus.EXACT:
        raise WorldError(
            f"ChangeIR provenance was not honored: {entity.identity_status}"
        )


def _restore_sources(root: Path, originals: dict[str, bytes | None]) -> None:
    for relative, content in originals.items():
        path = root / relative
        if content is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def _read_world(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorldError(f"cannot read Meldra world {path}: {exc}") from exc
    payload = _migrate_world(payload)
    if payload.get("project") != "Meldra":
        raise WorldError(f"{path} is not a Meldra Software World")
    if not isinstance(payload.get("program"), dict):
        raise WorldError(f"{path} has no valid ProgramIR")
    return payload


def _migrate_world(payload: dict[str, Any]) -> dict[str, Any]:
    schema = int(payload.get("schema", 1))
    if schema == WORLD_SCHEMA:
        return payload
    if schema == 1:
        migrated = dict(payload)
        migrated["schema"] = WORLD_SCHEMA
        migrated.setdefault("obligations", [])
        migrated.setdefault("evidence", [])
        migrated.setdefault("plans", [])
        migrated.setdefault("identity_confirmations", {})
        return migrated
    raise WorldError(
        f"unsupported Meldra world schema {schema}; expected 1 or {WORLD_SCHEMA}"
    )
