from __future__ import annotations

import ast as python_ast
from pathlib import Path


ENTRIES = tuple(sorted(Path("examples").glob("*/src/main.mlo")))


def test_project_compilation_exposes_typed_hir_without_legacy_artifact() -> None:
    from merlo.compiler import compile_project

    assert ENTRIES
    for entry in ENTRIES:
        compilation = compile_project(entry, require_interface_lock=False)
        assert compilation.hir.functions or compilation.hir.flows, entry
        assert not hasattr(compilation.hir, "native_module"), entry
        assert not hasattr(compilation.hir, "native_syntax_json"), entry


def test_legacy_parser_converts_immediately_to_merlo_nodes() -> None:
    from merlo import native_syntax

    module = native_syntax.parse(
        "fn = lambda value: value + 1\n",
        filename="legacy.py",
    )

    assert isinstance(module, native_syntax.Module)
    assert all(
        not isinstance(node, python_ast.AST)
        for node in native_syntax.walk(module)
    )
