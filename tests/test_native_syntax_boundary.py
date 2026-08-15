from __future__ import annotations

import ast as python_ast
from pathlib import Path


ENTRIES = tuple(sorted(Path("examples").glob("*/src/main.mlo")))


def test_project_compilation_never_calls_legacy_python_parser(monkeypatch) -> None:
    from merlo import native_syntax
    from merlo.compiler import compile_project

    def forbidden(*_args, **_kwargs):
        raise AssertionError("production compilation crossed the legacy parser boundary")

    monkeypatch.setattr(native_syntax, "parse", forbidden)
    assert ENTRIES
    for entry in ENTRIES:
        compilation = compile_project(entry, require_interface_lock=False)
        assert isinstance(compilation.hir.native_module, native_syntax.Module), entry
        assert all(
            not isinstance(node, python_ast.AST)
            for node in native_syntax.walk(compilation.hir.native_module)
        ), entry


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
