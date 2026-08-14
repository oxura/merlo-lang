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


def _elaborate_control_flow_application(root: Path, functions: str):
    app = root / "app"
    app.mkdir(parents=True)
    entry = app / "main.mlo"
    entry.write_text(
        "module app.main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        f"{functions}\n\n"
        "export task main(path: Path) -> Result[UInt64, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"control\")\n"
        "    return Ok(0)\n",
        encoding="utf-8",
    )
    return elaborate_concise_application(
        entry,
        require_interface_lock=False,
    )


def test_definite_assignment_intersects_conditional_paths_and_rejects_partial_locals(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConciseApplicationError, match="UnresolvedName.*value"):
        _elaborate_control_flow_application(
            tmp_path / "partial-branch",
            "fn read(flag: Bool) -> UInt64:\n"
            "    if flag:\n"
            "        value = 1\n"
            "    return value\n",
        )

    with pytest.raises(ConciseApplicationError, match="UnresolvedName.*value"):
        _elaborate_control_flow_application(
            tmp_path / "loop-only",
            "fn read() -> UInt64:\n"
            "    while false:\n"
            "        value = 1\n"
            "    return value\n",
        )


def test_definite_assignment_preserves_prebranch_bindings_and_accepts_both_branches(
    tmp_path: Path,
) -> None:
    elaborated = _elaborate_control_flow_application(
        tmp_path / "compatible-branches",
        "fn read(flag: Bool) -> UInt64:\n"
        "    value = 0\n"
        "    if flag:\n"
        "        value = 1\n"
        "    return value\n",
    )

    assert "var value: UInt64 = 0" in elaborated.canonical_source

    both_branches = _elaborate_control_flow_application(
        tmp_path / "both-branches",
        "fn read(flag: Bool) -> UInt64:\n"
        "    if flag:\n"
        "        value = 1\n"
        "    else:\n"
        "        value = 2\n"
        "    return value\n",
    )

    assert "var value: UInt64 = 1" in both_branches.canonical_source


def test_non_unit_functions_require_total_return_but_unit_keeps_fallthrough(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConciseApplicationError, match="MissingReturn"):
        _elaborate_control_flow_application(
            tmp_path / "partial-return",
            "fn read(flag: Bool) -> UInt64:\n"
            "    if flag:\n"
            "        return 1\n",
        )

    elaborated = _elaborate_control_flow_application(
        tmp_path / "total-return",
        "fn read(flag: Bool) -> UInt64:\n"
        "    if flag:\n"
        "        return 1\n"
        "    else:\n"
        "        return 2\n"
        "\n"
        "fn log(flag: Bool) -> Unit:\n"
        "    if flag:\n"
        "        return\n",
    )

    assert "fn read(flag: Bool) -> UInt64:" in elaborated.canonical_source
    assert "fn log(flag: Bool) -> Unit:" in elaborated.canonical_source


def test_control_flow_diagnostics_cover_shadowing_annotations_augassign_and_matches(
    tmp_path: Path,
) -> None:
    known_global = _elaborate_control_flow_application(
        tmp_path / "binding-rhs",
        "fn items(value: UInt64) -> UInt64:\n"
        "    return value\n"
        "\n"
        "fn read() -> UInt64:\n"
        "    items = items(1)\n"
        "    return items\n",
    )
    assert "fn read() -> UInt64:" in known_global.canonical_source

    before_later_binding = _elaborate_control_flow_application(
        tmp_path / "before-later-binding",
        "fn items(value: UInt64) -> UInt64:\n"
        "    return value\n"
        "\n"
        "fn read() -> UInt64:\n"
        "    value = items(1)\n"
        "    items = 0\n"
        "    return value\n",
    )
    assert "fn read() -> UInt64:" in before_later_binding.canonical_source

    with pytest.raises(ConciseApplicationError, match="UnresolvedName.*items"):
        _elaborate_control_flow_application(
            tmp_path / "conditional-shadow-self-init",
            "fn items(value: UInt64) -> UInt64:\n"
            "    return value\n"
            "\n"
            "fn read(flag: Bool) -> UInt64:\n"
            "    if flag:\n"
            "        items = 0\n"
            "    items = items(1)\n"
            "    return items\n",
        )

    multiline_global = _elaborate_control_flow_application(
        tmp_path / "multiline-binding-rhs",
        "fn items(value: UInt64) -> UInt64:\n"
        "    return value\n"
        "\n"
        "fn read() -> UInt64:\n"
        "    items = (\n"
        "        items(1)\n"
        "    )\n"
        "    return items\n",
    )
    assert "fn read() -> UInt64:" in multiline_global.canonical_source

    with pytest.raises(ConciseApplicationError, match="UnresolvedName.*item"):
        _elaborate_control_flow_application(
            tmp_path / "zero-iteration-for-shadow",
            "fn item(value: UInt64) -> UInt64:\n"
            "    return value\n"
            "\n"
            "fn read() -> UInt64:\n"
            "    for item in []:\n"
            "        return item(1)\n"
            "    return item(1)\n",
        )

    with pytest.raises(ConciseApplicationError, match="UnresolvedName.*helper"):
        _elaborate_control_flow_application(
            tmp_path / "shadowed-call",
            "fn helper() -> UInt64:\n"
            "    return 1\n"
            "\n"
            "fn read(flag: Bool) -> UInt64:\n"
            "    if flag:\n"
            "        helper = 0\n"
            "    return helper()\n",
        )

    with pytest.raises(ConciseApplicationError, match="UnresolvedName.*value"):
        _elaborate_control_flow_application(
            tmp_path / "annotation-only",
            "fn read() -> UInt64:\n"
            "    value: UInt64\n"
            "    return value\n",
        )

    with pytest.raises(ConciseApplicationError, match="UnresolvedName.*value"):
        _elaborate_control_flow_application(
            tmp_path / "augassign",
            "fn read(flag: Bool) -> UInt64:\n"
            "    if flag:\n"
            "        value = 0\n"
            "    value += 1\n"
            "    return value\n",
        )

    wildcard = _elaborate_control_flow_application(
        tmp_path / "wildcard-match",
        "fn read(value: UInt64) -> UInt64:\n"
        "    match value:\n"
        "        case _:\n"
        "            return 1\n",
    )
    assert "fn read(value: UInt64) -> UInt64:" in wildcard.canonical_source

    with pytest.raises(ConciseApplicationError, match="UnresolvedName.*item"):
        _elaborate_control_flow_application(
            tmp_path / "one-arm-capture",
            "enum Choice:\n"
            "    A: UInt64\n"
            "    B\n\n"
            "fn read(value: Choice) -> UInt64:\n"
            "    match value:\n"
            "        case Choice.A(item):\n"
            "            marker = 1\n"
            "        case Choice.B:\n"
            "            marker = 2\n"
            "    return item\n",
        )

    for name, function_source in (
        (
            "literal-if",
            "fn read() -> UInt64:\n"
            "    if true:\n"
            "        value = 1\n"
            "    return value\n",
        ),
        (
            "infinite-loop",
            "fn read() -> UInt64:\n"
            "    while true:\n"
            "        value = 1\n",
        ),
        (
            "nested-break",
            "fn read() -> UInt64:\n"
            "    while true:\n"
            "        while true:\n"
            "            break\n",
        ),
    ):
        _elaborate_control_flow_application(tmp_path / name, function_source)

    with pytest.raises(ConciseApplicationError, match="NonExhaustiveMatch"):
        _elaborate_control_flow_application(
            tmp_path / "non-exhaustive-match",
            "enum Choice:\n"
            "    A\n"
            "    B\n\n"
            "fn read(value: Choice) -> UInt64:\n"
            "    match value:\n"
            "        case Choice.A:\n"
            "            return 1\n",
        )


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
