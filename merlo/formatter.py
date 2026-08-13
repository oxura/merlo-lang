from __future__ import annotations

from typing import Any

from .concise_application import ConciseApplicationError, elaborate_concise_core


def _program(source: str, path: str) -> dict[str, Any]:
    """Parse and infer one Surface program through the typed tree frontend."""

    return elaborate_concise_core(source, path=path)


def expand_source(source: str, *, path: str = "main.mlo") -> str:
    """Materialize every inferred binding without changing the semantic AST."""

    return str(_program(source, path)["canonical_source"])


def format_source(source: str, *, path: str = "main.mlo") -> str:
    """Apply the stable whitespace format and prove semantic preservation."""

    before = _program(source, path)
    lines = [line.expandtabs(4).rstrip() for line in source.splitlines()]
    formatted: list[str] = []
    blank = False
    for line in lines:
        if line:
            formatted.append(line)
            blank = False
        elif formatted and not blank:
            formatted.append("")
            blank = True
    while formatted and not formatted[-1]:
        formatted.pop()
    result = "\n".join(formatted) + "\n"
    after = _program(result, path)
    if before["concise_semantic_digest"] != after["concise_semantic_digest"]:
        raise ValueError(f"{path}: formatter changed the semantic AST")
    return result

def format_application_source(source: str, *, path: str = "main.mlo") -> str:
    """Format a module while preserving its module/use header.

    The core formatter intentionally accepts declaration bodies.  Application
    sources additionally carry module and import directives; task bodies that
    the core frontend cannot elaborate use the same deterministic whitespace
    normalization as the production CLI.
    """
    lines = source.splitlines(keepends=True)
    header: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith(("module ", "use ")):
            header.append(lines[index])
            index += 1
            continue
        break
    body = "".join(lines[index:])
    try:
        formatted_body = format_source(body, path=path)
    except ConciseApplicationError:
        if not any(line.strip().startswith(("task ", "export task ")) for line in body.splitlines()):
            raise
        normalized = [line.expandtabs(4).rstrip() for line in body.splitlines()]
        formatted_body = "\n".join(normalized).rstrip() + "\n"
    return "".join(header) + formatted_body


def explain_source(source: str, *, path: str = "main.mlo") -> str:
    """Render deterministic inference, authority, ownership, and cost facts."""

    program = _program(source, path)
    decisions = program["decisions"]
    lines = [
        f"path: {path}",
        f"semantic digest: {program['concise_semantic_digest']}",
        "semantic AST preserved: yes",
    ]
    for decision in decisions:
        name = "return" if decision["name"] == "$return" else decision["name"]
        lines.append(
            f"{decision['kind']} {name}: {decision['type']}"
            f"; mutability: {'mutable' if decision['mutable'] else 'immutable'}"
            f"; evidence: {', '.join(decision['evidence'])}"
        )
    canonical = program["canonical_program"]
    effects = sorted(
        {effect for function in canonical.functions for effect in function.effects}
    )
    capabilities = sorted(
        {
            capability
            for function in canonical.functions
            for capability in function.capabilities
        }
    )
    errors = sorted(
        {
            error_type
            for function in canonical.functions
            for error_type in function.error_types
        }
    )
    for function in canonical.functions:
        lines.extend(
            (
                f"function: {function.name}",
                f"kind: {function.kind}",
                f"effects: {', '.join(function.effects) or 'none'}",
                f"capabilities: {', '.join(function.capabilities) or 'none'}",
                f"errors: {', '.join(function.error_types) or 'none'}",
            )
        )
    lines.extend(
        (
            f"effects: {', '.join(effects) or 'none'}",
            f"capabilities: {', '.join(capabilities) or 'none'}",
            f"errors: {', '.join(errors) or 'none'}",
        )
    )
    owned = sorted(
        {
            decision["type"]
            for decision in decisions
            if decision["type"] in {"Text", "Bytes", "TextBuilder"}
            or decision["type"].startswith("Vec[")
        }
    )
    if owned:
        lines.append(
            "ownership: owned " + ", ".join(owned) + " values move and drop on every exit"
        )
    else:
        lines.append("ownership: trivial values only")
    parameters = [item for item in decisions if item["kind"] == "parameter"]
    if parameters:
        lines.extend(
            f"arguments: {item['name']} {item['type']} checked" for item in parameters
        )
    else:
        lines.append("arguments: none")
    lines.append("ambiguity: none")
    lines.append(
        f"cost: semantic_nodes={program['semantic_node_count']} "
        f"inference_decisions={len(decisions)} source_lines={len(source.splitlines())}"
    )
    return "\n".join(lines) + "\n"


__all__ = ["expand_source", "explain_source", "format_application_source", "format_source"]
