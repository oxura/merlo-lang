from __future__ import annotations

from typing import Any, Mapping

from merlo.refactor import (
    ChangeIR,
    preview_change_signature,
    preview_move,
    preview_rename,
)
from merlo.semantic_capsule import SemanticCapsule
from merlo.semantic_world import SemanticWorld, WorldError


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

    def compile_context(self, target: str, *, goal: str = "") -> SemanticCapsule:
        return self.world.compile_context(target, goal=goal)

    def impact(self, target: str) -> dict[str, Any]:
        return self.world.impact(target)

    def change_impact(
        self,
        change: ChangeIR | Mapping[str, Any],
    ) -> dict[str, Any]:
        bound = (
            change
            if isinstance(change, ChangeIR)
            else ChangeIR.from_dict(
                change,
                world=self.world,
            )
        )
        return self.world.change_impact(
            bound
        ).to_dict()

    def diagnostics_explain(self, diagnostic: str | Mapping[str, Any]) -> dict[str, Any]:
        return self.world.diagnostics_explain(diagnostic)

    @staticmethod
    def _mode(
        operation: str,
        params: Mapping[str, Any],
    ) -> str:
        explicit = (
            "apply"
            if operation.endswith(".apply")
            else "preview"
            if operation.endswith(".preview")
            else None
        )
        requested = params.get("mode")
        if (
            requested is not None
            and (
                type(requested) is not str
                or requested
                not in {"preview", "apply"}
            )
        ):
            raise WorldError(
                "InvalidRefactorMode"
            )
        if (
            explicit is not None
            and requested is not None
            and requested != explicit
        ):
            raise WorldError(
                "ConflictingRefactorMode"
            )
        return explicit or requested or "preview"

    def call(
        self,
        operation: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        if type(operation) is not str:
            raise WorldError("InvalidOperation")
        operation = operation.strip()
        if not operation:
            raise WorldError("InvalidOperation")
        if params is None:
            values: Mapping[str, Any] = {}
        elif isinstance(params, Mapping):
            values = params
        else:
            raise WorldError("InvalidParameters")
        if operation in {"world.search", "search"}:
            query = values.get("query", "")
            if type(query) is not str:
                raise WorldError("InvalidQuery")
            return self.search(query)
        if operation in {"world.inspect", "inspect"}:
            return self.inspect(self._target(values))
        if operation in {"world.references", "refs", "references"}:
            return self.references(self._target(values))
        if operation in {"world.callers", "callers"}:
            transitive = values.get(
                "transitive",
                False,
            )
            if type(transitive) is not bool:
                raise WorldError(
                    "InvalidTransitiveFlag"
                )
            return self.callers(
                self._target(values),
                transitive=transitive,
            )
        if operation in {"world.callees", "callees"}:
            return self.callees(self._target(values))
        if operation in {"world.dependencies", "deps", "dependencies"}:
            return self.dependencies(
                self._target(values)
            )
        if operation in {"world.effects", "effects"}:
            target = values.get("target")
            if target is not None and type(target) is not str:
                raise WorldError("InvalidTarget")
            return self.effects(target)
        if operation in {"world.capabilities", "capabilities"}:
            target = values.get("target")
            if target is not None and type(target) is not str:
                raise WorldError("InvalidTarget")
            return self.capabilities(target)
        if operation in {"world.source", "source"}:
            return self.source(self._target(values))
        if operation in {"world.map", "map"}:
            projection = values.get(
                "projection",
                values.get("format", "text"),
            )
            if type(projection) is not str:
                raise WorldError(
                    "InvalidProjection"
                )
            return self.world.map(projection)
        if operation in {"context.compile", "context"}:
            goal = values.get("goal", "")
            if type(goal) is not str:
                raise WorldError("InvalidGoal")
            return self.compile_context(
                self._target(values),
                goal=goal,
            ).to_dict()
        if operation in {"impact.analyze", "impact"}:
            return self.impact(self._target(values))
        if operation == "impact.change":
            change = values.get("change")
            if not isinstance(
                change,
                (ChangeIR, Mapping),
            ):
                raise WorldError(
                    "MissingChangeIR: impact.change requires change"
                )
            return self.change_impact(change)
        if operation in {
            "diagnostics.explain",
            "diagnostic.explain",
        }:
            diagnostic = values.get(
                "diagnostic",
                values.get("code", ""),
            )
            if not isinstance(
                diagnostic,
                (str, Mapping),
            ):
                raise WorldError(
                    "InvalidDiagnostic"
                )
            return self.diagnostics_explain(
                diagnostic
            )
        if operation in {
            "refactor.rename",
            "refactor.rename.preview",
            "refactor.rename.apply",
        }:
            new_name = values.get(
                "new_name",
                values.get("name"),
            )
            if type(new_name) is not str or not new_name:
                raise WorldError(
                    "MissingRenameName: rename requires new_name"
                )
            change = preview_rename(
                self.world,
                self._target(values),
                new_name,
            )
            return (
                change.apply()
                if self._mode(operation, values)
                == "apply"
                else change.to_dict()
            )
        if operation in {
            "refactor.move",
            "refactor.move.preview",
            "refactor.move.apply",
        }:
            module = values.get("module")
            if type(module) is not str or not module:
                raise WorldError(
                    "MissingMoveModule"
                )
            self._mode(operation, values)
            return preview_move(
                self.world,
                self._target(values),
                module,
            ).to_dict()
        if operation in {
            "refactor.signature",
            "refactor.change_signature",
            "refactor.signature.preview",
            "refactor.signature.apply",
            "refactor.change_signature.preview",
            "refactor.change_signature.apply",
        }:
            signature = values.get("signature")
            if (
                type(signature) is not str
                or not signature
            ):
                raise WorldError(
                    "MissingSignature"
                )
            mode = self._mode(operation, values)
            change = preview_change_signature(
                self.world,
                self._target(values),
                signature,
            )
            if mode == "apply" and change.status == "ready":
                return change.apply()
            return change.to_dict()
        raise WorldError(
            f"UnknownOperation: {operation}"
        )


__all__ = ["AlphaProtocol", "SemanticCapsule"]
