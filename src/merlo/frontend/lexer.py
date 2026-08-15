from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExpressionToken:
    kind: str
    text: str
    start: int
    end: int
    value: object = None


class ExpressionLexError(ValueError):
    def __init__(self, message: str, position: int) -> None:
        self.message = message
        self.position = position
        super().__init__(message)


_OPERATORS = (
    "=>", "==", "!=", "<=", ">=", "//", "<<", ">>", "+", "-", "*", "/",
    "%", "|", "&", "^", "<", ">", ":", "=", ".", ",", "(", ")",
    "[", "]", "?", "~", "{", "}",
    ";", "!",
)


def _validate_text_scalar(value: int) -> str:
    if value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
        raise ValueError("text literal contains an invalid Unicode scalar")
    return chr(value)


def _decode_string(body: str, *, raw: bool, bytes_literal: bool) -> object:
    if raw:
        if bytes_literal:
            try:
                return body.encode("ascii")
            except UnicodeEncodeError as exc:
                raise ValueError("bytes literal contains non-ASCII text") from exc
        return body
    text_result: list[str] = []
    bytes_result = bytearray()
    index = 0
    escapes = {
        "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
        "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"',
    }
    while index < len(body):
        character = body[index]
        if character != "\\":
            if bytes_literal:
                if ord(character) > 0x7F:
                    raise ValueError("bytes literal contains non-ASCII text")
                bytes_result.append(ord(character))
            else:
                text_result.append(character)
            index += 1
            continue
        if index + 1 >= len(body):
            raise ValueError("unterminated escape sequence")
        escaped = body[index + 1]
        if escaped in escapes:
            decoded = escapes[escaped]
            if bytes_literal:
                bytes_result.extend(decoded.encode("latin1"))
            else:
                text_result.append(decoded)
            index += 2
            continue
        if escaped in "xXuU":
            width = 2 if escaped in "xX" else 4 if escaped == "u" else 8
            if bytes_literal and escaped not in "xX":
                raise ValueError("Unicode escapes are not allowed in bytes literals")
            digits = body[index + 2 : index + 2 + width]
            if len(digits) != width or any(
                character not in "0123456789abcdefABCDEF" for character in digits
            ):
                raise ValueError(f"invalid \\{escaped} escape")
            value = int(digits, 16)
            if bytes_literal:
                bytes_result.append(value)
            else:
                text_result.append(_validate_text_scalar(value))
            index += 2 + width
            continue
        if escaped in "01234567":
            end = index + 2
            while end < len(body) and end < index + 4 and body[end] in "01234567":
                end += 1
            value = int(body[index + 1 : end], 8)
            if bytes_literal:
                if value > 0xFF:
                    raise ValueError("octal escape is outside byte range")
                bytes_result.append(value)
            else:
                text_result.append(_validate_text_scalar(value))
            index = end
            continue
        if escaped == "\n":
            index += 2
            continue
        raise ValueError(f"unknown escape sequence: \\{escaped}")
    return bytes(bytes_result) if bytes_literal else "".join(text_result)


def _quoted(
    source: str,
    start: int,
    index: int,
    *,
    prefix: str,
) -> tuple[ExpressionToken, int]:
    quote = source[index]
    index += 1
    body_start = index
    escaped = False
    while index < len(source):
        current = source[index]
        if current == quote and not escaped:
            body = source[body_start:index]
            index += 1
            try:
                value = _decode_string(
                    body,
                    raw="r" in prefix.casefold(),
                    bytes_literal="b" in prefix.casefold(),
                )
            except ValueError as exc:
                raise ExpressionLexError(str(exc), start) from exc
            return ExpressionToken("literal", source[start:index], start, index, value), index
        escaped = current == "\\" and not escaped
        if current != "\\":
            escaped = False
        index += 1
    raise ExpressionLexError("unterminated string literal", start)


def lex_expression(
    source: str,
    *,
    include_trivia: bool = False,
) -> tuple[ExpressionToken, ...]:
    """Return stable, source-spanned tokens, optionally retaining all trivia."""
    tokens: list[ExpressionToken] = []
    index = 0
    while index < len(source):
        if source[index].isspace():
            start = index
            while index < len(source) and source[index].isspace():
                index += 1
            if include_trivia:
                tokens.append(
                    ExpressionToken("whitespace", source[start:index], start, index)
                )
            continue
        if source[index] == "#":
            if include_trivia:
                tokens.append(
                    ExpressionToken("comment", source[index:], index, len(source))
                )
            break
        start = index
        character = source[index]
        if character in "'\"":
            token, index = _quoted(source, start, index, prefix="")
            tokens.append(token)
            continue
        if character.isalpha() or character == "_":
            prefix = ""
            lowered = source[index : index + 2].casefold()
            if lowered in {"br", "rb"} and index + 2 < len(source) and source[index + 2] in "'\"":
                prefix = source[index : index + 2]
                index += 2
            elif character.casefold() in {"b", "r", "u"} and index + 1 < len(source) and source[index + 1] in "'\"":
                prefix = character
                index += 1
            if prefix:
                token, index = _quoted(source, start, index, prefix=prefix)
                tokens.append(token)
                continue
            index = start + 1
            while index < len(source) and (source[index].isalnum() or source[index] == "_"):
                index += 1
            tokens.append(ExpressionToken("identifier", source[start:index], start, index))
            continue
        if character.isdigit() or (
            character == "." and index + 1 < len(source) and source[index + 1].isdigit()
        ):
            if source[index : index + 2].casefold() in {"0x", "0b", "0o"}:
                index += 2
                while index < len(source) and (source[index].isalnum() or source[index] == "_"):
                    index += 1
                text = source[start:index]
                if text.endswith("_") or "__" in text:
                    raise ExpressionLexError("invalid integer literal", start)
                try:
                    value = int(text.replace("_", ""), 0)
                except ValueError as exc:
                    raise ExpressionLexError("invalid integer literal", start) from exc
                kind = "UInt64" if value >= 0 else "Int64"
            else:
                if character == ".":
                    index += 1
                while index < len(source) and (source[index].isdigit() or source[index] == "_"):
                    index += 1
                is_float = character == "."
                if index < len(source) and source[index] == ".":
                    is_float = True
                    index += 1
                    while index < len(source) and (source[index].isdigit() or source[index] == "_"):
                        index += 1
                if index < len(source) and source[index] in "eE":
                    is_float = True
                    index += 1
                    if index < len(source) and source[index] in "+-":
                        index += 1
                    while index < len(source) and (source[index].isdigit() or source[index] == "_"):
                        index += 1
                text = source[start:index]
                if text.endswith("_") or "__" in text:
                    raise ExpressionLexError("invalid numeric literal", start)
                try:
                    value = float(text.replace("_", "")) if is_float else int(text.replace("_", ""), 10)
                except ValueError as exc:
                    raise ExpressionLexError("invalid numeric literal", start) from exc
                kind = "Float64" if is_float else "UInt64"
            tokens.append(ExpressionToken("literal", source[start:index], start, index, (value, kind)))
            continue
        matched = next(
            (operator for operator in _OPERATORS if source.startswith(operator, index)),
            None,
        )
        if matched is None:
            raise ExpressionLexError(f"unexpected character {character!r}", index)
        index += len(matched)
        tokens.append(ExpressionToken("operator", matched, start, index))
    tokens.append(ExpressionToken("eof", "", len(source), len(source)))
    return tuple(tokens)


__all__ = ["ExpressionLexError", "ExpressionToken", "lex_expression"]
