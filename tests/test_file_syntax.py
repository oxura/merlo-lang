from __future__ import annotations

from merlo.frontend.file_syntax import lex_file, parse_file_cst


SOURCE = (
    "module sample\n"
    "# retained comment\n"
    "record Item:\n"
    "    name: Text\n"
    "    count: UInt64\n"
    "\n"
    "fn total(item: Item) -> UInt64:\n"
    "    if item.count > 0:\n"
    "        return item.count\n"
    "    return 0\n"
)


def test_full_file_lexer_is_lossless_and_emits_layout_tokens() -> None:
    result = lex_file(SOURCE, path="sample.mlo")
    assert result.to_source() == SOURCE
    assert not result.diagnostics
    kinds = [token.kind for token in result.tokens]
    assert kinds.count("indent") == kinds.count("dedent") == 3
    assert "newline" in kinds
    assert "comment" in kinds


def test_cst_groups_declarations_and_keeps_stable_ids_across_trivia_changes() -> None:
    first = parse_file_cst(SOURCE, path="sample.mlo")
    changed = parse_file_cst(
        SOURCE.replace("# retained comment", "# a different retained comment"),
        path="sample.mlo",
    )
    assert first.to_source() == SOURCE
    assert [node.kind for node in first.declarations] == [
        "module", "record", "fn"
    ]
    assert [node.syntax_id for node in first.declarations] == [
        node.syntax_id for node in changed.declarations
    ]


def test_file_lexer_recovers_after_invalid_tokens_and_bad_dedent() -> None:
    source = "fn bad() -> UInt64:\n    let x: UInt64 = 1\n  return x $\n"
    result = lex_file(source, path="bad.mlo")
    assert result.to_source() == source
    assert {item.code for item in result.diagnostics} == {
        "InconsistentDedent",
        "InvalidToken",
    }
    assert any(token.kind == "error" and token.text == "$" for token in result.tokens)


def test_file_lexer_keeps_surface_indentation_diagnostic_codes() -> None:
    tabbed = lex_file("value():\n\t1\n")
    odd_width = lex_file("value():\n   1\n")

    assert [item.code for item in tabbed.diagnostics] == [
        "TabIndentationForbidden"
    ]
    assert [item.code for item in odd_width.diagnostics] == [
        "InvalidIndentation"
    ]
