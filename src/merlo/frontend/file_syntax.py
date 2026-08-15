"""Lossless full-file lexer and recoverable concrete syntax tree for Merlo."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from merlo.frontend.lexer import ExpressionLexError, ExpressionToken, lex_expression


@dataclass(frozen=True)
class FileDiagnostic:
    code: str
    message: str
    start: int
    end: int
    line: int
    column: int


@dataclass(frozen=True)
class FileToken:
    kind: str
    text: str
    start: int
    end: int
    line: int
    column: int
    token_id: str
    value: object = None
    synthetic: bool = False


@dataclass(frozen=True)
class FileLexResult:
    source: str
    path: str
    tokens: tuple[FileToken, ...]
    diagnostics: tuple[FileDiagnostic, ...]

    def to_source(self) -> str:
        return "".join(
            token.text
            for token in self.tokens
            if not token.synthetic and token.kind != "eof"
        )


@dataclass(frozen=True)
class SyntaxNode:
    kind: str
    syntax_id: str
    start: int
    end: int
    tokens: tuple[FileToken, ...]


@dataclass(frozen=True)
class FileCST:
    source: str
    path: str
    tokens: tuple[FileToken, ...]
    declarations: tuple[SyntaxNode, ...]
    diagnostics: tuple[FileDiagnostic, ...]
    syntax_id: str

    def to_source(self) -> str:
        return "".join(
            token.text
            for token in self.tokens
            if not token.synthetic and token.kind != "eof"
        )


def _digest(*parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _token(
    kind: str,
    text: str,
    start: int,
    end: int,
    line: int,
    column: int,
    *,
    value: object = None,
    synthetic: bool = False,
) -> FileToken:
    return FileToken(
        kind,
        text,
        start,
        end,
        line,
        column,
        _digest("token", kind, text, start, end),
        value,
        synthetic,
    )


def _line_tokens(
    text: str,
    *,
    offset: int,
    line_number: int,
    column_offset: int,
    diagnostics: list[FileDiagnostic],
) -> list[FileToken]:
    output: list[FileToken] = []
    cursor = 0
    while cursor < len(text):
        segment = text[cursor:]
        try:
            expression_tokens = lex_expression(segment, include_trivia=True)
        except ExpressionLexError as error:
            failure = cursor + error.position
            if failure > cursor:
                prefix = lex_expression(text[cursor:failure], include_trivia=True)
                output.extend(
                    _from_expression_token(
                        token,
                        offset=offset + cursor,
                        line_number=line_number,
                        column_offset=column_offset + cursor,
                    )
                    for token in prefix
                    if token.kind != "eof"
                )
            bad_end = min(failure + 1, len(text))
            diagnostics.append(
                FileDiagnostic(
                    "InvalidToken",
                    error.message,
                    offset + failure,
                    offset + bad_end,
                    line_number,
                    column_offset + failure + 1,
                )
            )
            output.append(
                _token(
                    "error",
                    text[failure:bad_end],
                    offset + failure,
                    offset + bad_end,
                    line_number,
                    column_offset + failure + 1,
                )
            )
            cursor = bad_end
            continue
        output.extend(
            _from_expression_token(
                token,
                offset=offset + cursor,
                line_number=line_number,
                column_offset=column_offset + cursor,
            )
            for token in expression_tokens
            if token.kind != "eof"
        )
        break
    return output


def _from_expression_token(
    token: ExpressionToken,
    *,
    offset: int,
    line_number: int,
    column_offset: int,
) -> FileToken:
    return _token(
        token.kind,
        token.text,
        offset + token.start,
        offset + token.end,
        line_number,
        column_offset + token.start + 1,
        value=token.value,
    )


def lex_file(source: str, *, path: str = "main.mlo") -> FileLexResult:
    tokens: list[FileToken] = []
    diagnostics: list[FileDiagnostic] = []
    indentation = [0]
    offset = 0
    lines = source.splitlines(keepends=True)
    if source and (not lines or sum(len(line) for line in lines) < len(source)):
        lines.append(source[offset:])
    for line_number, raw in enumerate(lines, 1):
        content = raw.rstrip("\r\n")
        newline = raw[len(content):]
        leading = len(content) - len(content.lstrip(" \t"))
        prefix = content[:leading]
        body = content[leading:]
        blank = not body or body.startswith("#")
        if "\t" in prefix:
            diagnostics.append(
                FileDiagnostic(
                    "TabIndentationForbidden",
                    "tabs are not allowed in indentation",
                    offset,
                    offset + leading,
                    line_number,
                    1,
                )
            )
        width = sum(4 if character == "\t" else 1 for character in prefix)
        if not blank:
            if width > indentation[-1]:
                if "\t" not in prefix and width % 4:
                    diagnostics.append(
                        FileDiagnostic(
                            "InvalidIndentation",
                            "indentation must be a multiple of four spaces",
                            offset,
                            offset + leading,
                            line_number,
                            1,
                        )
                    )
                indentation.append(width)
                tokens.append(
                    _token(
                        "indent", "", offset, offset, line_number, 1, synthetic=True
                    )
                )
            elif width < indentation[-1]:
                while len(indentation) > 1 and width < indentation[-1]:
                    indentation.pop()
                    tokens.append(
                        _token(
                            "dedent", "", offset, offset, line_number, 1, synthetic=True
                        )
                    )
                if width != indentation[-1]:
                    diagnostics.append(
                        FileDiagnostic(
                            "InconsistentDedent",
                            f"dedent to column {width + 1} does not match an outer block",
                            offset,
                            offset + leading,
                            line_number,
                            1,
                        )
                    )
        if prefix:
            tokens.append(
                _token(
                    "whitespace",
                    prefix,
                    offset,
                    offset + leading,
                    line_number,
                    1,
                )
            )
        if body:
            tokens.extend(
                _line_tokens(
                    body,
                    offset=offset + leading,
                    line_number=line_number,
                    column_offset=leading,
                    diagnostics=diagnostics,
                )
            )
        if newline:
            tokens.append(
                _token(
                    "newline",
                    newline,
                    offset + len(content),
                    offset + len(raw),
                    line_number,
                    len(content) + 1,
                )
            )
        offset += len(raw)
    eof_line = len(lines) + 1 if lines else 1
    while len(indentation) > 1:
        indentation.pop()
        tokens.append(_token("dedent", "", offset, offset, eof_line, 1, synthetic=True))
    tokens.append(_token("eof", "", offset, offset, eof_line, 1, synthetic=True))
    result = FileLexResult(source, path, tuple(tokens), tuple(diagnostics))
    if result.to_source() != source:
        raise AssertionError("file lexer violated lossless source reconstruction")
    return result


_DECLARATION_KEYWORDS = frozenset(
    {
        "module", "import", "export", "record", "enum", "fn", "task",
        "interface", "extern", "spec", "impl", "const",
    }
)
_TRIVIA = frozenset({"whitespace", "comment", "newline", "indent", "dedent"})


def parse_file_cst(source: str, *, path: str = "main.mlo") -> FileCST:
    lexed = lex_file(source, path=path)
    significant = [token for token in lexed.tokens if token.kind not in _TRIVIA | {"eof"}]
    declaration_starts: list[int] = []
    depth = 0
    line_first: FileToken | None = None
    for index, token in enumerate(lexed.tokens):
        if token.kind == "indent":
            depth += 1
            continue
        if token.kind == "dedent":
            depth = max(0, depth - 1)
            continue
        if token.kind == "newline":
            line_first = None
            continue
        if token.kind in {"whitespace", "comment", "eof"}:
            continue
        if line_first is None:
            line_first = token
            if depth == 0:
                declaration_starts.append(index)
    nodes: list[SyntaxNode] = []
    for ordinal, start_index in enumerate(declaration_starts):
        end_index = (
            declaration_starts[ordinal + 1]
            if ordinal + 1 < len(declaration_starts)
            else len(lexed.tokens) - 1
        )
        node_tokens = tuple(lexed.tokens[start_index:end_index])
        first = next((token for token in node_tokens if token.kind not in _TRIVIA), None)
        if first is None:
            continue
        keyword = first.text if first.text in _DECLARATION_KEYWORDS else "statement"
        semantic = tuple(
            (token.kind, token.text)
            for token in node_tokens
            if token.kind not in _TRIVIA and token.kind != "eof"
        )
        nodes.append(
            SyntaxNode(
                keyword,
                _digest("syntax", path, keyword, ordinal, semantic),
                first.start,
                max((token.end for token in node_tokens), default=first.end),
                node_tokens,
            )
        )
    root_id = _digest(
        "file",
        path,
        tuple(node.syntax_id for node in nodes),
        tuple((token.kind, token.text) for token in significant),
    )
    return FileCST(
        source,
        path,
        lexed.tokens,
        tuple(nodes),
        lexed.diagnostics,
        root_id,
    )


__all__ = [
    "FileCST", "FileDiagnostic", "FileLexResult", "FileToken", "SyntaxNode",
    "lex_file", "parse_file_cst",
]
