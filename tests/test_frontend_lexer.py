from __future__ import annotations

import pytest

from merlo.frontend.lexer import ExpressionLexError, lex_expression


def test_lossless_expression_tokens_preserve_trivia_and_spans() -> None:
    source = 'name(1, r"two")  # note'
    tokens = lex_expression(source, include_trivia=True)
    assert "".join(token.text for token in tokens if token.kind != "eof") == source
    assert [(token.start, token.end) for token in tokens] == [
        (0, 4), (4, 5), (5, 6), (6, 7), (7, 8),
        (8, 14), (14, 15), (15, 17), (17, 23), (23, 23),
    ]
    assert [token.kind for token in tokens][-3:] == ["whitespace", "comment", "eof"]


def test_parser_token_stream_omits_trivia_by_default() -> None:
    assert [token.kind for token in lex_expression("value # comment")] == [
        "identifier",
        "eof",
    ]


def test_lexer_reports_the_exact_invalid_character_position() -> None:
    with pytest.raises(ExpressionLexError) as caught:
        lex_expression("ok @ bad")
    assert caught.value.position == 3


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ('b"\\x00"', b"\x00"),
        ('b"\\x41"', b"A"),
        ('b"\\xff"', b"\xff"),
        ('b"\\377"', b"\xff"),
        ('"\\u0061"', "a"),
    ),
)
def test_literal_escape_corpus(source: str, expected: object) -> None:
    token = lex_expression(source)[0]
    assert token.kind == "literal"
    assert token.value == expected


@pytest.mark.parametrize(
    ("source", "message"),
    (
        ('b"\\u0061"', "Unicode escapes are not allowed in bytes literals"),
        ('"\\uD800"', "text literal contains an invalid Unicode scalar"),
        ('"\\U00110000"', "text literal contains an invalid Unicode scalar"),
        ('"\\q"', "unknown escape sequence: \\q"),
        ('"\\x0"', "invalid \\x escape"),
        ('"unterminated', "unterminated string literal"),
    ),
)
def test_invalid_literal_escape_corpus_is_rejected(
    source: str,
    message: str,
) -> None:
    with pytest.raises(ExpressionLexError) as caught:
        lex_expression(source)
    assert caught.value.position == 0
    assert caught.value.message == message
