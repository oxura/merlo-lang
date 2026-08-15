from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from merlo.refactor import preview_change_signature, preview_move, preview_rename
from merlo.semantic_world import SemanticWorld, WorldError


@dataclass(frozen=True)
class TaskCapsule:
    goal: str
    target: dict[str, Any]
    source: str
    signature: str
    dependent_types: tuple[str, ...]
    callers: tuple[str, ...]
    dependencies: tuple[str, ...]
    effects: tuple[str, ...]
    capabilities: tuple[str, ...]
    public_boundary: bool
    tests: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "TaskCapsule",
            "goal": self.goal,
            "target": self.target,
            "source": self.source,
            "signature": self.signature,
            "dependent_types": list(self.dependent_types),
            "callers": list(self.callers),
            "dependencies": list(self.dependencies),
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
            "public_boundary": self.public_boundary,
            "tests": list(self.tests),
        }


class AlphaProtocol:
    """Deterministic provider-neutral protocol over a compiler semantic world."""

    def __init__(self, world: SemanticWorld):
        self.world = world

    def _target(self, params: Mapping[str, Any]) -> str:
        target = params.get("target", params.get("symbol", params.get("id")))
        if not isinstance(target, str) or not target:
            raise WorldError("MissingTarget: semantic operation requires target")
        return target

    def search(self, query: str) -> list[dict[str, Any]]:
        return list(self.world.search(query))

    def inspect(self, target: str) -> dict[str, Any]:
        return self.world.inspect(target)

    def references(self, target: str) -> list[dict[str, Any]]:
        return list(self.world.references(target))

    def callers(self, target: str, *, transitive: bool = False) -> list[dict[str, Any]]:
        return list(self.world.callers(target, transitive=transitive))

    def callees(self, target: str) -> list[dict[str, Any]]:
        return list(self.world.callees(target))

    def dependencies(self, target: str) -> list[dict[str, Any]]:
        return list(self.world.dependencies(target))

    def effects(self, target: str | None = None) -> list[str]:
        return list(self.world.effects(target))

    def capabilities(self, target: str | None = None) -> list[str]:
        return list(self.world.capabilities(target))

    def source(self, target: str) -> str:
        return self.world.source(target)

    def compile_context(self, target: str, *, goal: str = "") -> TaskCapsule:
        payload = self.world.compile_context(target, goal=goal)
        return TaskCapsule(
            goal=payload["goal"], target=payload["target"], source=payload["source"], signature=payload["signature"],
            dependent_types=tuple(payload["dependent_types"]), callers=tuple(payload["callers"]), dependencies=tuple(payload["dependencies"]),
            effects=tuple(payload["effects"]), capabilities=tuple(payload["capabilities"]), public_boundary=bool(payload["public_boundary"]), tests=tuple(payload["tests"]),
        )

    def impact(self, target: str) -> dict[str, Any]:
        return self.world.impact(target)

    def diagnostics_explain(self, diagnostic: str | Mapping[str, Any]) -> dict[str, Any]:
        return self.world.diagnostics_explain(diagnostic)

    def call(self, operation: str, params: Mapping[str, Any] | None = None) -> Any:
        values = params or {}
        operation = operation.strip()
        if operation in {"world.search", "search"}:
            return self.search(str(values.get("query", "")))
        if operation in {"world.inspect", "inspect"}:
            return self.inspect(self._target(values))
        if operation in {"world.references", "refs", "references"}:
            return self.references(self._target(values))
        if operation in {"world.callers", "callers"}:
            return self.callers(self._target(values), transitive=bool(values.get("transitive", False)))
        if operation in {"world.callees", "callees"}:
            return self.callees(self._target(values))
        if operation in {"world.dependencies", "deps", "dependencies"}:
            return self.dependencies(self._target(values))
        if operation in {"world.effects", "effects"}:
            return self.effects(values.get("target"))
        if operation in {"world.capabilities", "capabilities"}:
            return self.capabilities(values.get("target"))
        if operation in {"world.source", "source"}:
            return self.source(self._target(values))
        if operation in {"world.map", "map"}:
            return self.world.map(str(values.get("projection", values.get("format", "text"))))
        if operation in {"context.compile", "context"}:
            return self.compile_context(self._target(values), goal=str(values.get("goal", ""))).to_dict()
        if operation in {"impact.analyze", "impact"}:
            return self.impact(self._target(values))
        if operation in {"diagnostics.explain", "diagnostic.explain"}:
            return self.diagnostics_explain(values.get("diagnostic", values.get("code", "")))
        if operation in {"refactor.rename", "refactor.rename.preview", "refactor.rename.apply"}:
            target = self._target(values)
            new_name = values.get("new_name", values.get("name"))
            if not isinstance(new_name, str):
                raise WorldError("MissingRenameName: rename requires new_name")
            change = preview_rename(self.world, target, new_name)
            mode = "apply" if operation.endswith(".apply") else str(values.get("mode", "preview"))
            return change.apply() if mode == "apply" else change.to_dict()
        if operation in {"refactor.move", "refactor.move.preview", "refactor.move.apply"}:
            change = preview_move(self.world, self._target(values), str(values.get("module", "")))
            mode = "apply" if operation.endswith(".apply") else str(values.get("mode", "preview"))
            return change.apply() if mode == "apply" else change.to_dict()
        if operation in {"refactor.signature", "refactor.change_signature", "refactor.signature.preview", "refactor.signature.apply", "refactor.change_signature.preview", "refactor.change_signature.apply"}:
            change = preview_change_signature(self.world, self._target(values), str(values.get("signature", "")))
            mode = "apply" if operation.endswith(".apply") else str(values.get("mode", "preview"))
            return change.apply() if mode == "apply" else change.to_dict()
        raise WorldError(f"UnknownOperation: {operation}")


__all__ = ["AlphaProtocol", "TaskCapsule"]
