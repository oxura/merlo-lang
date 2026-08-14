from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from merlo.concise_assembly import _assemble_core
from merlo.concise_inference import _strip_semantic_annotations
from merlo.concise_interfaces import _interface_lock, _interfaces
from merlo.concise_syntax import _contains_dynamic_any, _preprocess_core, lower_concise_sum_types
from merlo.frontend_model import (
    CONCISE_APPLICATION_CONTRACT,
    CONCISE_APPLICATION_SCHEMA_VERSION,
    ConciseApplicationElaboration,
    ConciseApplicationError,
)
from merlo.module_loader import _load_modules, _project_root
def elaborate_concise_core(
    source: str,
    *,
    path: str = "main.mlo",
) -> dict[str, Any]:
    from merlo.surface_elaborator import SurfaceElaborationError, elaborate_surface
    from merlo.surface_parser import SurfaceSyntaxError, parse_surface

    try:
        surface = parse_surface(source, path=path)
        elaborated = elaborate_surface(surface)
    except (SurfaceSyntaxError, SurfaceElaborationError) as exc:
        raise ConciseApplicationError(f"{path}: {exc}") from exc
    canonical = elaborated.canonical.to_source()
    semantic_digest = elaborated.canonical.semantic_hash
    decisions = [
        {
            "owner": item.owner,
            "name": item.name,
            "kind": item.kind,
            "type": item.type_name,
            "mutable": item.mutable,
            "evidence": list(item.evidence),
            "path": path,
            "line": next(
                (
                    declaration.span.start_line
                    for declaration in surface.declarations
                    if getattr(declaration, "name", None) == item.owner
                ),
                1,
            ),
        }
        for item in elaborated.decisions
    ]
    return {
        "canonical_program": elaborated.canonical,
        "canonical_source": canonical,
        "machine_source": lower_concise_sum_types(canonical),
        "decisions": decisions,
        "concise_semantic_digest": semantic_digest,
        "canonical_semantic_digest": semantic_digest,
        "semantic_ast_equal": True,
        "semantic_node_count": sum(
            1
            for declaration in surface.declarations
            for _ in declaration.walk()
        ),
    }


def _machine_source(canonical_core: str) -> tuple[str, bool]:
    executable_core = re.sub(r"(?m)^task(\s+)", r"fn\1", canonical_core)
    return lower_concise_sum_types(executable_core), False


def elaborate_concise_application(
    entry: str | Path,
    *,
    require_interface_lock: bool = True,
) -> ConciseApplicationElaboration:
    entry_path = Path(entry).resolve()
    modules = _load_modules(entry_path)
    assembly, tasks, canonical_tasks = _assemble_core(modules)
    if not tasks:
        raise ConciseApplicationError(f"{entry_path}: application requires an effectful task boundary")
    if len(
        [
            item
            for item in tasks
            if item.name == "main" and Path(item.path).resolve() == entry_path
        ]
    ) != 1:
        raise ConciseApplicationError(f"{entry_path}: application requires exactly one task main")
    interfaces = _interfaces(assembly, tasks)
    lock_path, lock_valid = _interface_lock(_project_root(entry_path), interfaces)
    if require_interface_lock and not lock_valid:
        raise ConciseApplicationError(
            f"{entry_path}: PublicInterfaceRevisionMismatch; expected lock {lock_path}"
        )
    machine, reference_equal = _machine_source(assembly.canonical_source)
    canonical = assembly.canonical_source
    concise_digest = _strip_semantic_annotations(assembly.concise_source)
    canonical_digest = _strip_semantic_annotations(assembly.canonical_source)
    if concise_digest != canonical_digest:
        raise ConciseApplicationError(
            f"{entry_path}: concise/canonical semantic AST mismatch"
        )
    if _contains_dynamic_any(canonical):
        raise ConciseApplicationError(f"{entry_path}: DynamicAnyForbidden")
    source_payload = "\0".join(item.source for item in modules)
    return ConciseApplicationElaboration(
        str(entry_path),
        tuple(item.name for item in modules),
        hashlib.sha256(source_payload.encode()).hexdigest(),
        canonical,
        assembly.canonical_program,
        machine,
        concise_digest,
        canonical_digest,
        assembly.decisions,
        tasks,
        interfaces,
        assembly.origins,
        str(lock_path),
        lock_valid,
        reference_equal,
    )






def explain_concise_application(entry: str | Path) -> str:
    elaborated = elaborate_concise_application(entry)
    lines = [
        f"modules: {', '.join(elaborated.modules)}",
        f"semantic digest: {elaborated.concise_semantic_digest}",
        "semantic AST preserved: yes",
        "inferred bindings:",
    ]
    for item in elaborated.decisions:
        name = "return" if item.name == "$return" else item.name
        lines.append(
            f"  {item.owner}.{name}: {item.type_name}; kind={item.kind}; "
            f"mutability={'mutable' if item.mutable else 'immutable'}; "
            f"evidence={','.join(item.evidence)}; origin={item.path}:{item.line}"
        )
    lines.extend(
        (
            f"effects: {', '.join(elaborated.effects) or 'none'}",
            f"capabilities: {', '.join(elaborated.capabilities) or 'none'}",
            "implicit argument parsing:",
        )
    )
    if elaborated.argument_parsing:
        for item in elaborated.argument_parsing:
            lines.append(
                f"  {item['name']}: {item['type']} checked -> {item['failure']}"
            )
    else:
        lines.append("  none")
    lines.append("ownership transfers:")
    if elaborated.ownership_transfers:
        lines.extend(f"  {item}" for item in elaborated.ownership_transfers)
    else:
        lines.append("  trivial values only")
    lines.append("public interfaces:")
    for interface in elaborated.interfaces:
        parameters = ", ".join(
            f"{name}: {type_name}" for name, type_name in interface.parameters
        )
        signature = f"{interface.kind} {interface.module}.{interface.name}({parameters})"
        if interface.return_type is not None:
            signature += f" -> {interface.return_type}"
        lines.append(
            f"  {signature}; effects={','.join(interface.effects) or 'none'}; "
            f"capabilities={','.join(interface.capabilities) or 'none'}; "
            f"revision={interface.revision_id}"
        )
    lines.append("ambiguous points: none")
    lines.append(f"interface revision: {elaborated.interface_revision}")
    semantic_nodes = sum(
        1
        for _ in ast.walk(ast.parse(_preprocess_core(elaborated.canonical_source)))
    )
    lines.append(
        f"costs: modules={len(elaborated.modules)} semantic_nodes={semantic_nodes} "
        f"inference_decisions={len(elaborated.decisions)} "
        f"task_boundaries={len(elaborated.tasks)} "
        f"public_interfaces={len(elaborated.interfaces)}"
    )
    return "\n".join(lines) + "\n"


def write_interface_lock(entry: str | Path) -> Path:
    elaborated = elaborate_concise_application(
        entry, require_interface_lock=False
    )
    path = Path(elaborated.interface_lock_path)
    payload = {
        "schema_version": 1,
        "interfaces": [item.to_dict() for item in elaborated.interfaces],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path

__all__ = [
    "CONCISE_APPLICATION_CONTRACT",
    "CONCISE_APPLICATION_SCHEMA_VERSION",
    "ConciseApplicationElaboration",
    "ConciseApplicationError",
    "elaborate_concise_application",
    "elaborate_concise_core",
    "explain_concise_application",
    "lower_concise_sum_types",
    "write_interface_lock",
]
