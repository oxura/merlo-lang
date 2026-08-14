from __future__ import annotations

from merlo.frontend_model import ConciseApplicationError
from merlo.runtime_contract import ALPHA_EFFECTS
from merlo.surface_ast import SurfaceFunction, SurfaceProgram, SurfaceUses


_ALLOWED_EFFECTS = ALPHA_EFFECTS


def _validate_declared_task_effects(
    program: SurfaceProgram,
    inferred_effects: dict[str, tuple[str, ...]],
) -> None:
    """Check explicit task capabilities against structurally inferred effects."""

    for declaration in program.declarations:
        if not isinstance(declaration, SurfaceFunction):
            continue
        if declaration.declared_kind != "task":
            continue
        statements = (
            declaration.body
            if declaration.body_kind == "block"
            else ()
        )
        uses_nodes = tuple(
            statement
            for statement in statements
            if isinstance(statement, SurfaceUses)
        )
        if not uses_nodes:
            raise ConciseApplicationError(
                f"{declaration.span.path}:{declaration.span.start_line}: "
                f"MissingEffectDeclaration {declaration.name}; "
                "task must declare `uses`"
            )
        if len(uses_nodes) != 1:
            raise ConciseApplicationError(
                f"{declaration.span.path}:{declaration.span.start_line}: "
                f"DuplicateEffectDeclaration {declaration.name}"
            )
        declared = set(uses_nodes[0].effects)
        unknown = declared - _ALLOWED_EFFECTS
        if unknown:
            raise ConciseApplicationError(
                f"{declaration.span.path}:{declaration.span.start_line}: "
                f"UnsupportedEffect {sorted(unknown)}"
            )
        missing = set(inferred_effects.get(declaration.name, ())) - declared
        if missing:
            raise ConciseApplicationError(
                f"{declaration.span.path}:{declaration.span.start_line}: "
                f"MissingCapability {declaration.name}: "
                f"declare {tuple(sorted(missing))} in the task uses list"
            )


__all__ = ["_ALLOWED_EFFECTS", "_validate_declared_task_effects"]
