from __future__ import annotations

import pytest

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


def test_statement_keywords_used_as_receivers_remain_expressions() -> None:
    result = parse_file_cst(
        "fn emit(state: State):\n"
        "    state = State.new()\n"
        "    state.tokens.push(1)\n",
        path="receiver.mlo",
    )
    block = result.declarations[0].children[1]

    assert [child.kind for child in block.children] == [
        "expression_statement",
        "expression_statement",
    ]


def test_cst_splits_statement_expression_regions_at_semantic_boundaries() -> None:
    result = parse_file_cst(
        "fn update(items: Vec[Item]):\n"
        "    for item in items.where(.active):\n"
        "        item.count += item.delta\n",
        path="regions.mlo",
    )
    outer_block = result.declarations[0].children[1]
    loop = outer_block.children[0]
    loop_header, loop_block = loop.children
    assignment_header = loop_block.children[0].children[0]

    assert [token.text for token in loop_header.children[0].tokens] == [
        "items", ".", "where", "(", ".", "active", ")",
    ]
    assert [
        [token.text for token in expression.tokens]
        for expression in assignment_header.children
    ] == [
        ["item", ".", "count"],
        ["item", ".", "delta"],
    ]


def test_cst_retains_inline_declaration_expression_and_return_type_regions() -> None:
    result = parse_file_cst(
        "fn increment(value: UInt64, lookup: Map<Text, UInt64>) -> UInt64 = value + 1\n"
        "identity(value) = value\n",
        path="declaration-regions.mlo",
    )
    explicit_header = result.declarations[0].children[0]
    inferred_header = result.declarations[1].children[0]

    assert [child.kind for child in explicit_header.children] == [
        "parameters",
        "type",
        "expression",
    ]
    assert [token.text for token in explicit_header.children[1].tokens] == [
        "UInt64",
    ]
    assert [token.text for token in explicit_header.children[2].tokens] == [
        "value",
        "+",
        "1",
    ]
    assert [child.kind for child in inferred_header.children] == [
        "parameters",
        "expression",
    ]
    parameters = explicit_header.children[0].children
    assert [child.kind for child in parameters] == ["parameter", "parameter"]
    assert [
        [token.text for token in parameter.children[0].tokens]
        for parameter in parameters
    ] == [
        ["UInt64"],
        ["Map", "<", "Text", ",", "UInt64", ">"],
    ]


def test_cst_retains_generic_type_parameter_regions() -> None:
    result = parse_file_cst(
        "fn choose[T: Comparable + Display, U](left: T, right: U) -> T = left\n",
        path="generic-regions.mlo",
    )
    header = result.declarations[0].children[0]

    assert [child.kind for child in header.children] == [
        "type_parameters",
        "parameters",
        "type",
        "expression",
    ]
    assert [
        [token.text for token in parameter.tokens]
        for parameter in header.children[0].children
    ] == [
        ["T", ":", "Comparable", "+", "Display"],
        ["U"],
    ]


def test_multiline_delimited_expression_is_one_lossless_cst_region() -> None:
    source = (
        "fn values() -> Array[UInt64, 2]:\n"
        "    values: Array[UInt64, 2] = [\n"
        "        1,\n"
        "        2,\n"
        "    ]\n"
        "    return values\n"
    )
    lexed = lex_file(source, path="multiline.mlo")
    result = parse_file_cst(source, path="multiline.mlo")
    block = result.declarations[0].children[1]
    binding = block.children[0]
    expression = binding.children[0].children[-1]

    assert lexed.to_source() == source
    assert [token.kind for token in lexed.tokens].count("indent") == 1
    assert [child.kind for child in block.children] == [
        "expression_statement",
        "return",
    ]
    assert [token.text for token in expression.tokens] == [
        "[", "1", ",", "2", ",", "]",
    ]


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


@pytest.mark.parametrize(
    ("fragment", "codes"),
    [
        ("([)]", ("MismatchedDelimiter", "MismatchedDelimiter")),
        ("{[)}", ("MismatchedDelimiter",)),
        (")", ("UnexpectedClosingDelimiter",)),
        ("([", ("UnclosedDelimiter", "UnclosedDelimiter")),
    ],
)
def test_file_lexer_validates_typed_delimiter_pairs(
    fragment: str,
    codes: tuple[str, ...],
) -> None:
    source = f"fn bad():\n    value = {fragment}\n"
    result = lex_file(source, path="delimiters.mlo")

    assert result.to_source() == source
    assert tuple(item.code for item in result.diagnostics) == codes


def test_adversarial_multiline_tokens_keep_exact_offsets_and_trivia() -> None:
    source = (
        "fn choose(repeated: UInt64) -> UInt64:\r\n"
        "    return (\r\n"
        "        repeated # the same identifier follows on another line\r\n"
        "        + repeated\r\n"
        "    )\r\n"
    )
    result = parse_file_cst(source, path="offsets.mlo")
    expression = next(
        node for node in result.root.walk() if node.kind == "expression"
    )

    assert not result.diagnostics
    assert [token.text for token in expression.tokens].count("repeated") == 2
    assert all(
        source[token.start:token.end] == token.text
        for token in expression.tokens
    )
    assert [token.start for token in expression.tokens] == sorted(
        token.start for token in expression.tokens
    )
