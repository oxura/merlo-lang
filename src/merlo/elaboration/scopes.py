from __future__ import annotations

import hashlib
from dataclasses import fields, replace

from merlo import surface_ast as surface
from merlo.surface_binding import _payload_name


def bind_match_payloads(program: surface.SurfaceProgram) -> surface.SurfaceProgram:
    """Give each arm's payload its own inference/lowering name, retaining spans."""
    used_names = {
        node.name
        for node in program.walk()
        if isinstance(
            node,
            (
                surface.SurfaceName,
                surface.SurfaceFunction,
                surface.SurfaceParameter,
                surface.SurfaceBinding,
                surface.SurfaceAnnotation,
                surface.SurfaceFor,
            ),
        )
    }
    used_names.update(
        payload
        for node in program.walk()
        if isinstance(node, surface.SurfaceCase)
        if (payload := _payload_name(node.pattern)) is not None
    )
    used_names.update(
        parameter
        for node in program.walk()
        if isinstance(node, surface.SurfaceLambda)
        for parameter in node.parameters
    )

    ordinals: dict[str, int] = {}

    def payload_name(owner: str) -> str:
        ordinal = ordinals.get(owner, 0)
        ordinals[owner] = ordinal + 1
        identity = f"{owner}\0{ordinal}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        stem = f"__merlo_case_{digest}"
        result = stem
        suffix = 0
        while result in used_names:
            suffix += 1
            result = f"{stem}_{suffix}"
        used_names.add(result)
        return result

    def visit(
        node: surface.SurfaceNode, bindings: dict[str, str], owner: str,
    ) -> surface.SurfaceNode:
        if isinstance(node, surface.SurfaceName):
            target = bindings.get(node.name)
            return replace(node, name=target) if target is not None else node
        if isinstance(node, surface.SurfaceFunction):
            bindings = {}
            owner = node.name
        if isinstance(node, surface.SurfaceLambda):
            bindings = {
                name: target for name, target in bindings.items()
                if name not in node.parameters
            }
        if isinstance(node, surface.SurfaceMatch):
            cases = []
            for case in node.cases:
                scoped = dict(bindings)
                payload = _payload_name(case.pattern)
                pattern = case.pattern
                if payload is not None:
                    target = payload_name(owner)
                    scoped[payload] = target
                    head = pattern[:pattern.find("(")]
                    pattern = f"{head}({target})"
                cases.append(
                    replace(
                        case,
                        pattern=pattern,
                        body=tuple(visit(item, scoped, owner) for item in case.body),
                    )
                )
            return replace(
                node,
                expression=visit(node.expression, bindings, owner),
                cases=tuple(cases),
            )
        changed = {}
        if isinstance(
            node,
            (surface.SurfaceBinding, surface.SurfaceAnnotation, surface.SurfaceFor),
        ) and node.name in bindings:
            changed["name"] = bindings[node.name]
        for field in fields(node):
            value = getattr(node, field.name)
            if isinstance(value, surface.SurfaceNode):
                transformed = visit(value, bindings, owner)
                if transformed is not value:
                    changed[field.name] = transformed
            elif isinstance(value, tuple):
                transformed = tuple(
                    visit(item, bindings, owner)
                    if isinstance(item, surface.SurfaceNode) else item
                    for item in value
                )
                if any(left is not right for left, right in zip(value, transformed)):
                    changed[field.name] = transformed
        return replace(node, **changed) if changed else node

    result = visit(program, {}, "")
    assert isinstance(result, surface.SurfaceProgram)
    return result
