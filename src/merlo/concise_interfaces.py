from __future__ import annotations

import ast
import json
from pathlib import Path

from merlo.concise_assembly import _CoreAssembly
from merlo.concise_inference import _public_type_name
from merlo.concise_syntax import _normalize_type, _preprocess_core
from merlo.frontend_model import (
    ConciseApplicationError,
    PublicInterface,
    TaskBoundary,
)
def _interfaces(
    assembly: _CoreAssembly,
    tasks: tuple[TaskBoundary, ...],
) -> tuple[PublicInterface, ...]:
    parsed = ast.parse(_preprocess_core(assembly.canonical_source))
    functions = {
        item.name: item for item in parsed.body if isinstance(item, ast.FunctionDef)
    }
    public_names = {
        internal: f"{module}.{public}"
        for module, public, internal in assembly.symbol_names
    }
    result: list[PublicInterface] = []
    for module, name, kind, internal in assembly.export_symbols:
        if kind == "task":
            continue
        if kind == "fn":
            node = functions[internal]
            result.append(
                PublicInterface(
                    module,
                    name,
                    "fn",
                    tuple(
                        (
                            item.arg,
                            _public_type_name(_normalize_type(item.annotation), public_names)
                            or "?",
                        )
                        for item in node.args.args
                    ),
                    _public_type_name(_normalize_type(node.returns), public_names),
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
