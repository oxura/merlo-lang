from __future__ import annotations

from typing import Any

from .context import TaskCapsule, compile_context, source_read
from .impact import analyze_impact
from .model import ChangePlan, EditCapability
from .world import SoftwareWorld


class MerloProtocol:
    """Provider-neutral semantic API for CLI, agents, IDEs, and future MCP."""

    def __init__(self, world: SoftwareWorld):
        self.world = world

    def search(self, query: str) -> tuple[dict[str, Any], ...]:
        needle = query.casefold()
        matches = [
            entity
            for entity in self.world.program.entities
            if needle in entity.fqname.casefold()
            or needle in entity.signature.casefold()
        ]
        return tuple(entity.to_dict() for entity in matches)

    def inspect(self, entity: str) -> dict[str, Any]:
        target = self.world.program.entity(entity)
        return {
            "entity": target.to_dict(),
            "references": [
                item.to_dict()
                for item in self.world.program.references_to(
                    target.id, include_possible=True
                )
            ],
            "callers": [
                self.world.program.entity(identifier).to_dict()
                for identifier in self.world.program.callers_of(target.id)
            ],
            "dependencies": [
                self.world.program.entity(identifier).to_dict()
                for identifier in self.world.program.dependencies_of(target.id)
            ],
        }

    def references(self, entity: str) -> tuple[dict[str, Any], ...]:
        target = self.world.program.entity(entity)
        return tuple(
            item.to_dict()
            for item in self.world.program.references_to(
                target.id, include_possible=True
            )
        )

    def callers(
        self, entity: str, *, transitive: bool = False
    ) -> tuple[dict[str, Any], ...]:
        target = self.world.program.entity(entity)
        return tuple(
            self.world.program.entity(identifier).to_dict()
            for identifier in self.world.program.callers_of(
                target.id, transitive=transitive
            )
        )

    def dependencies(self, entity: str) -> tuple[dict[str, Any], ...]:
        target = self.world.program.entity(entity)
        return tuple(
            self.world.program.entity(identifier).to_dict()
            for identifier in self.world.program.dependencies_of(target.id)
        )

    def source_read(self, entity: str) -> str:
        return source_read(self.world.program, self.world.program.entity(entity).id)

    def compile_context(
        self,
        entity: str,
        *,
        goal: str,
        capability: EditCapability | None = None,
    ) -> TaskCapsule:
        return compile_context(
            self.world.program,
            entity,
            goal=goal,
            obligations=self.world.obligations.values(),
            evidence=self.world.evidence.values(),
            capability=capability,
        )

    def preview_rename(
        self,
        entity: str,
        new_name: str,
        capability: EditCapability,
        *,
        goal: str = "Rename a semantic symbol",
    ) -> ChangePlan:
        return self.world.plan_rename(
            entity, new_name, capability, goal=goal
        )

    def preview_move(
        self,
        entity: str,
        target_module: str,
        capability: EditCapability,
        *,
        goal: str = "Move a semantic symbol",
    ) -> ChangePlan:
        return self.world.plan_move(
            entity, target_module, capability, goal=goal
        )

    def preview_change_signature(
        self,
        entity: str,
        new_signature: str,
        capability: EditCapability,
        *,
        argument_values: dict[str, str] | None = None,
        goal: str = "Change a semantic signature",
    ) -> ChangePlan:
        return self.world.plan_change_signature(
            entity,
            new_signature,
            capability,
            argument_values=argument_values,
            goal=goal,
        )

    @staticmethod
    def validate(plan: ChangePlan) -> dict[str, Any]:
        return {
            "ready": plan.ready,
            "obligation_graph": plan.obligation_graph.to_dict(),
            "impact": plan.impact.to_dict() if plan.impact else None,
            "edits": [item.to_dict() for item in plan.edits],
        }

    def apply(
        self, plan: ChangePlan, capability: EditCapability | None = None
    ) -> tuple[str, ...]:
        return self.world.apply(plan, capability)

    def obligations(
        self, *, change_id: str | None = None
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            item.to_dict()
            for item in self.world.list_obligations(change_id=change_id)
        )

    def obligation(self, identifier: str) -> dict[str, Any]:
        return self.world.obligation(identifier).to_dict()

    def evidence(self, identifier: str) -> dict[str, Any]:
        return self.world.evidence_item(identifier).to_dict()

    def impact(self, entity: str) -> dict[str, Any]:
        target = self.world.program.entity(entity)
        return analyze_impact(
            self.world.program,
            target.id,
            obligations=self.world.obligations.values(),
            evidence=self.world.evidence.values(),
        ).to_dict()
