from __future__ import annotations

from pathlib import Path

import pytest

from merlo.concise_application import (
    ConciseApplicationError,
    elaborate_concise_application,
    elaborate_concise_core,
    write_interface_lock,
)
from merlo.concise_precedence import validate_precedence_corpus
from merlo.formatter import expand_source, explain_source, format_source


def test_expand_is_deterministic_semantic_compression() -> None:
    source = "fn add(a, b) -> UInt64:\n    total = a + b\n    total\n"

    expanded = expand_source(source, path="math.mlo")
    elaborated = elaborate_concise_core(source, path="math.mlo")

    assert expanded == elaborated["canonical_source"]
    assert "fn add(a: UInt64, b: UInt64) -> UInt64:" in expanded
    assert "let total: UInt64 = a + b" in expanded
    assert elaborated["semantic_ast_equal"] is True


def test_format_is_idempotent_and_preserves_semantic_ast() -> None:
    source = "fn add(a, b) -> UInt64:   \n    total = a + b    \n\n\n    total\n"

    formatted = format_source(source, path="math.mlo")

    assert formatted == "fn add(a, b) -> UInt64:\n    total = a + b\n\n    total\n"
    assert format_source(formatted, path="math.mlo") == formatted
    before = elaborate_concise_core(source, path="math.mlo")
    after = elaborate_concise_core(formatted, path="math.mlo")
    assert before["concise_semantic_digest"] == after["concise_semantic_digest"]


def test_dynamic_any_is_structural_not_textual() -> None:
    source = 'fn label() -> Text:\n    "Any is documentation, not a type"\n'
    assert "Any is documentation" in expand_source(source)

    with pytest.raises(ConciseApplicationError, match="DynamicAnyForbidden"):
        expand_source("fn identity(value: Any) -> Any:\n    value\n")


def test_ambiguous_and_bool_numeric_programs_are_rejected() -> None:
    with pytest.raises(ConciseApplicationError, match="AmbiguousType"):
        expand_source("fn identity(value):\n    value\n")

    with pytest.raises(ConciseApplicationError, match="numeric operator requires numeric operands, got Bool"):
        expand_source("fn add_flag() -> UInt64:\n    true + 1\n")


def test_canonical_scalar_aliases_materialize_width_and_sign() -> None:
    source = (
        "fn signed(value: Int) -> Int:\n"
        "    value\n\n"
        "fn unsigned(value: UInt) -> UInt:\n"
        "    value\n\n"
        "fn floating(value: Float) -> Float:\n"
        "    value\n"
    )

    expanded = expand_source(source)

    assert "fn signed(value: Int64) -> Int64:" in expanded
    assert "fn unsigned(value: UInt64) -> UInt64:" in expanded
    assert "fn floating(value: Float64) -> Float64:" in expanded


def test_public_interface_revision_ignores_body_only_drift(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    entry = app / "main.mlo"
    entry.write_text(
        "module app.main\n\n"
        "export enum AppError:\n    Failed\n\n"
        "export fn answer() -> UInt64:\n    41 + 1\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        '    console.write("ok")\n'
        '    return Ok("ok")\n',
        encoding="utf-8",
    )
    write_interface_lock(entry)
    first = elaborate_concise_application(entry)

    entry.write_text(
        "module app.main\n\n"
        "export enum AppError:\n    Failed\n\n"
        "export fn answer() -> UInt64:\n    40 + 2\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        '    console.write("ok")\n'
        '    return Ok("ok")\n',
        encoding="utf-8",
    )
    second = elaborate_concise_application(entry)

    assert first.interface_revision == second.interface_revision
    assert second.interface_lock_valid is True
    assert first.source_sha256 != second.source_sha256


def test_explain_reports_inference_ownership_and_costs() -> None:
    source = (
        "fn label(value: Text) -> Text:\n"
        "    text = value.clone()\n"
        "    text\n"
    )
    explanation = explain_source(source, path="labels.mlo")

    assert "parameter value: Text" in explanation
    assert "local text: Text" in explanation
    assert "mutability: immutable" in explanation
    assert "effects: none" in explanation
    assert "capabilities: none" in explanation
    assert "ownership: owned Text values move and drop on every exit" in explanation
    assert "arguments: value Text checked" in explanation
    assert "ambiguity: none" in explanation
    assert "cost: semantic_nodes=" in explanation


def test_origins_retain_concise_module_locations(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    entry = app / "main.mlo"
    entry.write_text(
        "module app.main\n\n"
        "export enum AppError:\n    Failed\n\n"
        "export fn answer() -> UInt64:\n    42\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        '    console.write("ok")\n'
        '    return Ok("ok")\n',
        encoding="utf-8",
    )
    elaborated = elaborate_concise_application(entry, require_interface_lock=False)

    assert elaborated.origins
    assert all(item.path == str(entry) for item in elaborated.origins)
    assert {item.source_line for item in elaborated.origins} >= {3, 4, 6, 7, 9, 11, 12}


def test_formal_precedence_corpus_is_frozen_and_semantic() -> None:
    report = validate_precedence_corpus(1024)

    assert report["count"] == 1024
    assert report["all_semantic_ast_equal"] is True
    assert len(report["table"]) == 12
