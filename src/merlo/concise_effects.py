from __future__ import annotations

import re
from typing import Iterable

from merlo.concise_syntax import (
    GenericTypeSyntaxError,
    _protected_mask,
    split_structural_commas,
)
from merlo.frontend_model import ConciseApplicationError, TaskBoundary
from merlo.intrinsics import INTRINSIC_SIGNATURES
from merlo.module_loader import _Module
from merlo.runtime_contract import ALPHA_EFFECTS

_ALLOWED_EFFECTS = ALPHA_EFFECTS

_EFFECT_CALL_PATTERNS: dict[str, tuple[str, ...]] = {}
for _intrinsic in INTRINSIC_SIGNATURES.values():
    _EFFECT_CALL_PATTERNS.setdefault(_intrinsic.effect, ())
    _EFFECT_CALL_PATTERNS[_intrinsic.effect] += (
        rf"\b{re.escape(_intrinsic.name)}\s*\(",
    )


def _direct_effects(source: str) -> set[str]:
    protected = _protected_mask(source)
    visible = "".join(
        " " if is_protected else character
        for character, is_protected in zip(source, protected, strict=True)
    )
    body = "\n".join(
        line for line in visible.splitlines()
        if not re.fullmatch(r"\s*uses\s+.+", line.strip())
    )
    effects = {
        effect
        for effect, patterns in _EFFECT_CALL_PATTERNS.items()
        if any(re.search(pattern, body) for pattern in patterns)
    }
    if re.search(r"\.lines\s*\(", body):
        effects.add("fs.read")
    return effects


def _resolve_task_effects(
    modules: tuple[_Module, ...],
    tasks: tuple[TaskBoundary, ...],
) -> tuple[TaskBoundary, ...]:
    bodies: dict[str, str] = {}
    for module in modules:
        lines = module.body.splitlines()
        index = 0
        while index < len(lines):
            match = re.fullmatch(
                r"(?:export\s+)?task\s+([A-Za-z_]\w*)\((.*)\)\s*->\s*(.+):",
                lines[index].strip(),
            )
            if match is None:
                index += 1
                continue
            name = match.group(1)
            index += 1
            body: list[str] = []
            while index < len(lines) and (
                not lines[index].strip() or lines[index].startswith((" ", "\t"))
            ):
                body.append(lines[index])
                index += 1
            bodies[name] = "\n".join(body)
    by_name = {task.name: task for task in tasks}
    direct = {name: _direct_effects(body) for name, body in bodies.items()}
    calls = {
        name: {
            candidate
            for candidate in by_name
            if candidate != name and re.search(rf"\b{re.escape(candidate)}\s*\(", body)
        }
        for name, body in bodies.items()
    }
    resolved: dict[str, frozenset[str]] = {}

    def visit(name: str, visiting: frozenset[str] = frozenset()) -> frozenset[str]:
        if name in resolved:
            return resolved[name]
        if name in visiting:
            raise ConciseApplicationError(f"EffectCycle:{name}")
        result = set(direct.get(name, set()))
        for callee in calls.get(name, set()):
            result.update(visit(callee, visiting | {name}))
        if "fs.close" in bodies.get(name, ""):
            result.update(
                effect for effect in by_name[name].effects
                if effect in {"fs.read", "fs.write"}
            )
        resolved[name] = frozenset(result)
        return resolved[name]

    output: list[TaskBoundary] = []
    for task in tasks:
        actual = tuple(sorted(visit(task.name)))
        declared = set(task.effects)
        missing = set(actual) - declared
        extra = declared - set(actual)
        if missing or extra:
            detail = f"missing={sorted(missing)} extra={sorted(extra)}"
            raise ConciseApplicationError(
                f"{task.path}:{task.line}: EffectDeclarationMismatch {task.name}: {detail}"
            )
        output.append(
            TaskBoundary(
                task.name, task.parameters, task.return_type, actual, actual,
                task.path, task.line, task.public,
            )
        )
    return tuple(output)


def _extract_task(
    module: _Module,
    effectful_dependencies: Iterable[TaskBoundary] = (),
) -> tuple[
    str,
    tuple[TaskBoundary, ...],
    str,
    tuple[int, ...],
    tuple[int, ...],
]:
    lines = module.body.splitlines()
    source_lines = module.body_source_lines
    output: list[str] = []
    output_origins: list[int] = []
    tasks: list[TaskBoundary] = []
    canonical_tasks: list[str] = []
    task_origins: list[int] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        source_line = source_lines[index]
        host_error = re.fullmatch(r"export\s+enum\s+AppError\s*:", line.strip())
        if host_error and module.name.endswith(".main"):
            canonical_tasks.append(re.sub(r"^export\s+", "", line))
            task_origins.append(source_line)
            index += 1
            while index < len(lines) and (
                not lines[index].strip()
                or lines[index].startswith((" ", "\t"))
            ):
                canonical_tasks.append(lines[index])
                task_origins.append(source_lines[index])
                index += 1
            continue
        match = re.fullmatch(
            r"(export\s+)?task\s+([A-Za-z_]\w*)\((.*)\)\s*->\s*(.+):",
            line.strip(),
        )
        if match is None:
            output.append(line)
            output_origins.append(source_line)
            index += 1
            continue
        public = bool(match.group(1))
        name = match.group(2)
        raw_parameters = match.group(3).strip()
        return_type = match.group(4).strip().replace(" ", "")
        parameters: list[tuple[str, str]] = []
        if raw_parameters:
            try:
                parameter_items = split_structural_commas(raw_parameters)
            except GenericTypeSyntaxError as error:
                raise ConciseApplicationError(
                    f"{module.path}:{source_line}: malformed parameter types: {error}"
                ) from error
            for item in parameter_items:
                if ":" not in item:
                    raise ConciseApplicationError(
                        f"{module.path}:{source_line}: public task parameters require annotations"
                    )
                parameter_name, type_name = item.split(":", 1)
                parameters.append((parameter_name.strip(), type_name.strip()))
        body: list[tuple[str, int]] = []
        index += 1
        while index < len(lines) and (
            not lines[index].strip()
            or lines[index].startswith((" ", "\t"))
        ):
            body.append((lines[index], source_lines[index]))
            index += 1
        uses = ()
        for body_line, _ in body:
            uses_match = re.fullmatch(r"\s*uses\s+(.+)", body_line)
            if uses_match:
                uses = tuple(
                    sorted(item.strip() for item in uses_match.group(1).split(","))
                )
                break
        if not uses:
            raise ConciseApplicationError(
                f"{module.path}:{source_line}: MissingEffectDeclaration {name}; "
                "task must declare `uses`"
            )
        unknown = set(uses) - _ALLOWED_EFFECTS
        if unknown:
            raise ConciseApplicationError(
                f"{module.path}:{source_line}: UnsupportedEffect {sorted(unknown)}"
            )
        joined = "\n".join(body_line for body_line, _ in body)
        uses_set = set(uses)
        actual = _direct_effects(joined)
        for dependency in effectful_dependencies:
            if re.search(rf"\b{re.escape(dependency.name)}\s*\(", joined):
                actual.update(dependency.effects)
        if not actual <= uses_set:
            missing = tuple(sorted(actual - uses_set))
            raise ConciseApplicationError(
                f"{module.path}:{source_line}: MissingCapability {name}: "
                f"declare {missing} in the task uses list"
            )
        if name == "main" and tuple(type_name for _, type_name in parameters) != ("Path",):
            raise ConciseApplicationError(
                f"{module.path}:{source_line}: CLI main requires exactly one Path parameter"
            )

        canonical_tasks.append(re.sub(r"^export\s+", "", line))
        task_origins.append(source_line)
        for body_line, body_source_line in body:
            if re.fullmatch(r"\s*uses\s+.+", body_line.strip()):
                continue
            canonical_tasks.append(body_line)
            task_origins.append(body_source_line)
        tasks.append(
            TaskBoundary(
                name,
                tuple(parameters),
                return_type,
                uses,
                uses,
                str(module.path),
                source_line,
                public,
            )
        )
    while output and not output[-1].strip():
        output.pop()
        output_origins.pop()
    while canonical_tasks and not canonical_tasks[-1].strip():
        canonical_tasks.pop()
        task_origins.pop()
    return (
        "\n".join(output) + "\n",
        tuple(tasks),
        "\n".join(canonical_tasks) + "\n",
        tuple(output_origins),
        tuple(task_origins),
    )


def _validate_declared_task_effects(
    modules: tuple[_Module, ...],
    inferred_effects: dict[str, tuple[str, ...]],
    symbols: dict[str, dict[str, tuple[str, bool, str]]],
) -> None:
    for module in modules:
        lines = module.body.splitlines()
        index = 0
        while index < len(lines):
            match = re.fullmatch(
                r"(?:export\s+)?task\s+([A-Za-z_]\w*)\(.*\)\s*->\s*.+:",
                lines[index].strip(),
            )
            if match is None:
                index += 1
                continue
            name = match.group(1)
            source_line = module.body_source_lines[index]
            index += 1
            uses: tuple[str, ...] = ()
            while index < len(lines) and (
                not lines[index].strip() or lines[index].startswith((" ", "\t"))
            ):
                if uses_match := re.fullmatch(r"\s*uses\s+(.+)", lines[index]):
                    uses = tuple(sorted(item.strip() for item in uses_match.group(1).split(",")))
                index += 1
            if not uses:
                raise ConciseApplicationError(
                    f"{module.path}:{source_line}: MissingEffectDeclaration {name}; "
                    "task must declare `uses`"
                )
            unknown = set(uses) - _ALLOWED_EFFECTS
            if unknown:
                raise ConciseApplicationError(
                    f"{module.path}:{source_line}: UnsupportedEffect {sorted(unknown)}"
                )
            internal = symbols[module.name].get(name, ("", False, name))[2]
            missing = set(inferred_effects.get(internal, ())) - set(uses)
            if missing:
                raise ConciseApplicationError(
                    f"{module.path}:{source_line}: MissingCapability {name}: "
                    f"declare {tuple(sorted(missing))} in the task uses list"
                )


__all__ = [
    "_ALLOWED_EFFECTS",
    "_EFFECT_CALL_PATTERNS",
    "_direct_effects",
    "_resolve_task_effects",
    "_extract_task",
    "_validate_declared_task_effects",
]
