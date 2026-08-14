from __future__ import annotations

import json
import re
from pathlib import Path

from merlo.concise_assembly import _CoreAssembly
from merlo.frontend_model import (
    ConciseApplicationError,
    PublicInterface,
    TaskBoundary,
)


def _public_type_name(type_name: str | None, public_names: dict[str, str]) -> str | None:
    if type_name is None:
        return None

    def replace_name(match: re.Match[str]) -> str:
        start, end = match.span()
        if (start and type_name[start - 1] == ".") or (
            end < len(type_name) and type_name[end] == "."
        ):
            return match.group(0)
        return public_names.get(match.group(0), match.group(0))

    return re.sub(r"\b[A-Za-z_]\w*\b", replace_name, type_name)
def _interfaces(
    assembly: _CoreAssembly,
    tasks: tuple[TaskBoundary, ...],
) -> tuple[PublicInterface, ...]:
    public_names = {
        internal: f"{module}.{public}"
        for module, public, internal in assembly.symbol_names
    }
    functions = {
        item.name: item for item in assembly.canonical_program.functions
    }
    result: list[PublicInterface] = []
    for module, name, kind, internal in assembly.export_symbols:
        if kind == "task":
            continue
        if kind == "fn":
            function = functions[internal]
            result.append(
                PublicInterface(
                    module,
                    name,
                    "fn",
                    tuple(
                        (
                            parameter,
                            _public_type_name(type_name, public_names) or "?",
                        )
                        for parameter, type_name in function.parameters
                    ),
                    _public_type_name(function.return_type, public_names),
                    (),
                    (),
                )
            )
        elif kind != "task":
            result.append(PublicInterface(module, name, kind, (), None, (), ()))
    task_modules = {
        (public, path): module
        for public, path, module in assembly.task_modules
    }
    for task in tasks:
        if not task.public:
            continue
        module = task_modules.get((task.name, task.path), "")
        result.append(
            PublicInterface(
                module,
                task.name,
                "task",
                task.parameters,
                task.return_type,
                task.effects,
                task.capabilities,
            )
        )
    return tuple(sorted(result, key=lambda item: (item.module, item.kind, item.name)))


def _interface_lock(
    root: Path,
    interfaces: tuple[PublicInterface, ...],
) -> tuple[Path, bool]:
    path = root / ".merlo-interface.json"
    actual = {
        "schema_version": 1,
        "interfaces": [item.to_dict() for item in interfaces],
    }
    if not path.exists():
        return path, False
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConciseApplicationError(
            f"{path}: invalid interface lock: {exc}"
        ) from exc
    return path, expected == actual
