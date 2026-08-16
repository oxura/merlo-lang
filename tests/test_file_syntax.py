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


def test_file_lexer_preserves_line_endings_and_terminal_text() -> None:
    sources = (
        "module sample",
        "module sample\r\n\r\nfn marker() -> Text:\r\n    \"# literal\"\r\n",
    )

    for source in sources:
        result = lex_file(source, path="sample.mlo")
        assert result.to_source() == source
        assert not result.diagnostics


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


def test_cst_builds_hierarchical_headers_blocks_statements_and_parts() -> None:
    result = parse_file_cst(SOURCE, path="sample.mlo")
    function = result.declarations[-1]

    assert result.root.kind == "file"
    assert result.root.children == result.declarations
    assert [child.kind for child in function.children] == ["header", "block"]
    header, block = function.children
    assert [child.kind for child in header.children] == ["parameters", "type"]
    assert [child.kind for child in block.children] == ["if", "return"]
    nested = block.children[0]
    assert [child.kind for child in nested.children] == ["header", "block"]
    assert [child.kind for child in nested.children[0].children] == ["expression"]
    assert [child.kind for child in nested.children[1].children] == ["return"]
    assert {node.kind for node in result.root.walk()} >= {
        "file", "fn", "header", "block", "parameters", "type",
        "if", "return", "expression",
    }


def test_hierarchical_ids_survive_unrelated_sibling_insertions() -> None:
    original = (
        "fn value() -> UInt64:\n"
        "    return 0\n"
    )
    changed = (
        "fn value() -> UInt64:\n"
        "    # retained trivia does not own identity\n"
        "    let marker: UInt64 = 1\n"
        "    return 0\n"
    )
    first = parse_file_cst(original, path="stable.mlo")
    second = parse_file_cst(changed, path="stable.mlo")
    first_return = next(
        node for node in first.root.walk() if node.kind == "return"
    )
    second_return = next(
        node for node in second.root.walk() if node.kind == "return"
    )

    assert first.declarations[0].syntax_id == second.declarations[0].syntax_id
    assert first_return.syntax_id == second_return.syntax_id
    assert original[first_return.start:first_return.end] == "return 0\n"
    assert changed[second_return.start:second_return.end] == "return 0\n"


def test_repeated_sibling_syntax_has_distinct_deterministic_ids() -> None:
    source = "fn repeated() -> UInt64:\n    return 0\n    return 0\n"
    first = parse_file_cst(source, path="repeat.mlo")
    second = parse_file_cst(source, path="repeat.mlo")
    first_ids = [
        node.syntax_id for node in first.root.walk() if node.kind == "return"
    ]
    second_ids = [
        node.syntax_id for node in second.root.walk() if node.kind == "return"
    ]

    assert len(first_ids) == len(set(first_ids)) == 2
    assert first_ids == second_ids


def test_cst_keeps_terminal_unterminated_line_at_its_nested_depth() -> None:
    source = "fn value() -> UInt64:\n    return 0"
    result = parse_file_cst(source, path="terminal.mlo")
    function = result.declarations[0]
    block = next(child for child in function.children if child.kind == "block")

    assert [child.kind for child in block.children] == ["return"]
    assert source[block.children[0].start:block.children[0].end] == "return 0"
    assert result.to_source() == source


def test_file_lexer_recovers_after_invalid_tokens_and_bad_dedent() -> None:
    source = "fn bad() -> UInt64:\n    let x: UInt64 = 1\n  return x $\n"
    result = lex_file(source, path="bad.mlo")
    assert result.to_source() == source
    assert {item.code for item in result.diagnostics} == {
        "InconsistentDedent",
        "InvalidToken",
    }
    assert any(token.kind == "error" and token.text == "$" for token in result.tokens)

    cst = parse_file_cst(source, path="bad.mlo")
    assert cst.to_source() == source
    assert {code for node in cst.errors for code in node.diagnostic_codes} == {
        "InconsistentDedent",
        "InvalidToken",
    }
    assert {code for node in cst.root.walk() for code in node.diagnostic_codes} == {
        "InconsistentDedent",
        "InvalidToken",
    }


def test_file_lexer_keeps_surface_indentation_diagnostic_codes() -> None:
    tabbed = lex_file("value():\n\t1\n")
    odd_width = lex_file("value():\n   1\n")

    assert [item.code for item in tabbed.diagnostics] == [
        "TabIndentationForbidden"
    ]
    assert [item.code for item in odd_width.diagnostics] == [
        "InvalidIndentation"
    ]
