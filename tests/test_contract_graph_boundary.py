from __future__ import annotations

import ast
from pathlib import Path

import pytest

from merlo.intrinsics import CONTRACT_GRAPH, TypeConstructorId
from merlo.type_arena import TypeArenaError, TypeContextBuilder


SRC_ROOT = Path(__file__).parents[1] / "src" / "merlo"
LEGACY_METHODS = frozenset({"method", "static_method", "resolve_static_method"})


def _legacy_calls() -> list[str]:
    findings: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in LEGACY_METHODS:
                continue
            receiver = node.func.value
            if isinstance(receiver, ast.Name) and receiver.id == "CONTRACT_GRAPH":
                findings.append(f"{path}:{node.lineno}: {node.func.attr}")
    return findings


def test_production_has_no_legacy_contract_graph_calls() -> None:
    assert _legacy_calls() == []


def test_bound_graph_rejects_raw_string_static_receivers() -> None:
    builder = TypeContextBuilder()
    bound = CONTRACT_GRAPH.prepare(builder)
    with pytest.raises(TypeArenaError, match="receiver"):
        bound.resolve_static_method("Vec", "new", ())  # type: ignore[arg-type]
    assert bound.resolve_static_method(TypeConstructorId("Vec"), "new", ()) is not None
