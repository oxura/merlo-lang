from __future__ import annotations

from merlo.elaboration.diagnostics import SurfaceElaborationError
from merlo.surface_ast import SurfaceCall, SurfaceCallArgument


def bind_call_arguments(
    expression: SurfaceCall,
    parameters: tuple[str, ...],
    label: str,
) -> tuple[tuple[str, SurfaceCallArgument], ...]:
    parameter_names = set(parameters)
    assigned: dict[str, SurfaceCallArgument] = {}
    next_positional = 0
    keyword_seen = False
    for argument in expression.arguments:
        if argument.name is None:
            if keyword_seen:
                raise SurfaceElaborationError(
                    f"PositionalAfterKeyword: {label}"
                )
            if next_positional >= len(parameters):
                raise SurfaceElaborationError(f"ArityMismatch: {label}")
            parameter_name = parameters[next_positional]
            next_positional += 1
        else:
            keyword_seen = True
            parameter_name = argument.name
            if parameter_name not in parameter_names:
                raise SurfaceElaborationError(
                    f"UnknownArgument: {label}.{parameter_name}"
                )
        if parameter_name in assigned:
            raise SurfaceElaborationError(
                f"DuplicateArgument: {label}.{parameter_name}"
            )
        assigned[parameter_name] = argument
    missing = tuple(name for name in parameters if name not in assigned)
    if missing:
        raise SurfaceElaborationError(
            f"MissingArgument: {label}: {', '.join(missing)}"
        )
    return tuple((name, assigned[name]) for name in parameters)


__all__ = ["bind_call_arguments"]
