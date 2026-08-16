"""Lossless full-file lexer and recoverable concrete syntax tree for Merlo."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

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
    children: tuple[SyntaxNode, ...] = ()
    diagnostic_codes: tuple[str, ...] = ()

    def walk(self) -> tuple[SyntaxNode, ...]:
        return (self,) + tuple(
            descendant
            for child in self.children
            for descendant in child.walk()
        )


@dataclass(frozen=True)
class FileCST:
    source: str
    path: str
    tokens: tuple[FileToken, ...]
    declarations: tuple[SyntaxNode, ...]
    diagnostics: tuple[FileDiagnostic, ...]
    syntax_id: str
    root: SyntaxNode
    errors: tuple[SyntaxNode, ...] = ()

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
        "flow", "durable", "machine", "interface", "extern", "spec",
        "impl", "const",
    }
)
_TRIVIA = frozenset({"whitespace", "comment", "newline", "indent", "dedent"})


@dataclass
class _LogicalLine:
    depth: int
    layout_start_index: int
    start_index: int
    header_end_index: int
    children: list[_LogicalLine] = field(default_factory=list)
    end_index: int = 0


_STATEMENT_KEYWORDS = frozenset(
    {
        "let", "return", "if", "elif", "else", "for", "while", "match",
        "case", "require", "ensure", "invariant", "uses", "parallel",
        "compensate", "transition", "state", "goal", "modify", "preserve",
        "forbid", "yield", "break", "continue",
    }
)


def _logical_lines(tokens: tuple[FileToken, ...]) -> tuple[_LogicalLine, ...]:
    lines: list[_LogicalLine] = []
    depth = 0
    layout_start = 0
    first: int | None = None
    first_depth: int | None = None
    for index, token in enumerate(tokens):
        if token.kind == "indent":
            depth += 1
            continue
        if token.kind == "dedent":
            depth = max(0, depth - 1)
            continue
        if token.kind == "newline":
            if first is not None:
                lines.append(
                    _LogicalLine(
                        first_depth if first_depth is not None else depth,
                        layout_start,
                        first,
                        index + 1,
                    )
                )
            first = None
            first_depth = None
            layout_start = index + 1
            continue
        if token.kind in {"whitespace", "comment", "eof"}:
            continue
        if first is None:
            first = index
            first_depth = depth
    if first is not None:
        lines.append(
            _LogicalLine(
                first_depth if first_depth is not None else depth,
                layout_start,
                first,
                len(tokens) - 1,
            )
        )

    stack: list[_LogicalLine] = []
    roots: list[_LogicalLine] = []
    for line in lines:
        line.end_index = len(tokens) - 1
        while stack and stack[-1].depth >= line.depth:
            stack.pop().end_index = line.layout_start_index
        if stack:
            stack[-1].children.append(line)
        else:
            roots.append(line)
        stack.append(line)
    return tuple(roots)


def _significant(
    tokens: tuple[FileToken, ...],
) -> tuple[FileToken, ...]:
    return tuple(
        token
        for token in tokens
        if token.kind not in _TRIVIA and token.kind != "eof"
    )


def _construct_kind(
    tokens: tuple[FileToken, ...],
    *,
    top_level: bool,
) -> str:
    significant = _significant(tokens)
    if not significant or any(token.kind == "error" for token in significant):
        return "error"
    texts = [token.text for token in significant]
    first = texts[0]
    if first == "export" and len(texts) > 1:
        first = texts[1]
    if first == "durable" and len(texts) > 1 and texts[1] == "flow":
        return "flow"
    if top_level and (first in _DECLARATION_KEYWORDS or first in {"use"}):
        return first
    if (
        not top_level
        and first in _STATEMENT_KEYWORDS
        and (
            len(significant) == 1
            or significant[1].text not in {".", "=", "["}
        )
    ):
        return first
    if (
        not top_level
        and len(significant) >= 2
        and significant[0].kind == "identifier"
        and significant[1].text == ":"
        and not any(token.text == "=" for token in significant)
    ):
        return "field"
    return "statement" if top_level else "expression_statement"


def _semantic_key(tokens: tuple[FileToken, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((token.kind, token.text) for token in _significant(tokens))


def _leaf_node(
    kind: str,
    tokens: tuple[FileToken, ...],
    *,
    parent_id: str,
    ordinal: int,
) -> SyntaxNode | None:
    significant = _significant(tokens)
    if not significant:
        return None
    return SyntaxNode(
        kind,
        _digest("syntax-v2-leaf", parent_id, kind, ordinal, _semantic_key(tokens)),
        significant[0].start,
        max(token.end for token in tokens),
        tokens,
    )


def _header_parts(
    kind: str,
    tokens: tuple[FileToken, ...],
    *,
    parent_id: str,
) -> tuple[SyntaxNode, ...]:
    significant = list(_significant(tokens))
    if not significant:
        return ()
    parts: list[SyntaxNode] = []

    def add(part_kind: str, start: int, end: int) -> None:
        if start >= end:
            return
        selected = tuple(significant[start:end])
        node = _leaf_node(
            part_kind,
            selected,
            parent_id=parent_id,
            ordinal=len(parts),
        )
        if node is not None:
            parts.append(node)

    texts = [token.text for token in significant]
    trailing_colon = len(texts) - 1 if texts[-1] == ":" else len(texts)
    if kind in {"fn", "task", "flow", "extern"}:
        try:
            open_index = texts.index("(")
            close_index = len(texts) - 1 - texts[::-1].index(")")
        except ValueError:
            open_index = close_index = -1
        if 0 <= open_index < close_index:
            add("parameters", open_index, close_index + 1)
            arrow = next(
                (
                    index
                    for index in range(close_index + 1, trailing_colon - 1)
                    if texts[index : index + 2] == ["-", ">"]
                ),
                None,
            )
            if arrow is not None:
                add("type", arrow + 2, trailing_colon)
    if kind == "let":
        equals = next((index for index, text in enumerate(texts) if text == "="), None)
        colon = next((index for index, text in enumerate(texts) if text == ":"), None)
        if colon is not None:
            add("type", colon + 1, equals if equals is not None else len(texts))
        if equals is not None:
            add("expression", equals + 1, len(texts))
    elif kind == "field":
        colon = next((index for index, text in enumerate(texts) if text == ":"), None)
        if colon is not None:
            add("type", colon + 1, len(texts))
    elif kind in {"return", "require", "ensure", "yield"}:
        add("expression", 1, trailing_colon)
    elif kind in {"if", "elif", "while", "match", "case", "for"}:
        add("expression", 1, trailing_colon)
    elif "=" in texts:
        equals = texts.index("=")
        add("expression", equals + 1, len(texts))
    return tuple(parts)


def _build_nodes(
    lines: tuple[_LogicalLine, ...] | list[_LogicalLine],
    tokens: tuple[FileToken, ...],
    *,
    path: str,
    parent_anchor: str,
) -> tuple[SyntaxNode, ...]:
    occurrences: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
    output: list[SyntaxNode] = []
    for line in lines:
        header_tokens = tuple(tokens[line.start_index : line.header_end_index])
        full_tokens = tuple(tokens[line.start_index : line.end_index])
        kind = _construct_kind(header_tokens, top_level=line.depth == 0)
        semantic = _semantic_key(header_tokens)
        occurrence_key = (kind, semantic)
        occurrence = occurrences.get(occurrence_key, 0)
        occurrences[occurrence_key] = occurrence + 1
        anchor = _digest("syntax-v2-anchor", path, kind, semantic)
        syntax_id = _digest(
            "syntax-v2",
            parent_anchor,
            anchor,
            occurrence,
        )
        header_id = _digest("syntax-v2-header", syntax_id)
        header = SyntaxNode(
            "header",
            header_id,
            _significant(header_tokens)[0].start,
            max(token.end for token in header_tokens),
            header_tokens,
            _header_parts(kind, header_tokens, parent_id=header_id),
        )
        construct_children = _build_nodes(
            line.children,
            tokens,
            path=path,
            parent_anchor=syntax_id,
        )
        children: tuple[SyntaxNode, ...] = (header,)
        if line.children:
            block_start = line.header_end_index
            block_tokens = tuple(tokens[block_start : line.end_index])
            block = SyntaxNode(
                "block",
                _digest("syntax-v2-block", syntax_id),
                block_tokens[0].start if block_tokens else header.end,
                max((token.end for token in block_tokens), default=header.end),
                block_tokens,
                construct_children,
            )
            children += (block,)
        diagnostics = ("InvalidToken",) if kind == "error" else ()
        output.append(
            SyntaxNode(
                kind,
                syntax_id,
                _significant(header_tokens)[0].start,
                max((token.end for token in full_tokens), default=header.end),
                full_tokens,
                children,
                diagnostics,
            )
        )
    return tuple(output)


def parse_file_cst(source: str, *, path: str = "main.mlo") -> FileCST:
    lexed = lex_file(source, path=path)
    lines = _logical_lines(lexed.tokens)
    declarations = _build_nodes(
        lines,
        lexed.tokens,
        path=path,
        parent_anchor=_digest("syntax-v2-file-anchor", path),
    )
    recovered_nodes = tuple(
        node
        for declaration in declarations
        for node in declaration.walk()
        if node.kind == "error"
    )
    covered_codes = {
        code
        for node in recovered_nodes
        for code in node.diagnostic_codes
    }
    errors = list(recovered_nodes)
    standalone_errors: list[SyntaxNode] = []
    for diagnostic in lexed.diagnostics:
        if diagnostic.code in covered_codes:
            continue
        diagnostic_tokens = tuple(
            token
            for token in lexed.tokens
            if (
                diagnostic.start <= token.start <= diagnostic.end
                or token.start <= diagnostic.start < token.end
            )
        )
        recovered = SyntaxNode(
            "error",
            _digest(
                "syntax-v2-error",
                path,
                diagnostic.code,
                tuple((token.kind, token.text) for token in diagnostic_tokens),
            ),
            diagnostic.start,
            diagnostic.end,
            diagnostic_tokens,
            (),
            (diagnostic.code,),
        )
        errors.append(recovered)
        standalone_errors.append(recovered)
    root_id = _digest(
        "syntax-v2-file",
        path,
        tuple(node.syntax_id for node in declarations),
        tuple(node.syntax_id for node in standalone_errors),
    )
    root = SyntaxNode(
        "file",
        root_id,
        0,
        len(source),
        lexed.tokens,
        tuple(
            sorted(
                declarations + tuple(standalone_errors),
                key=lambda node: (node.start, node.end, node.syntax_id),
            )
        ),
    )
    return FileCST(
        source,
        path,
        lexed.tokens,
        declarations,
        lexed.diagnostics,
        root_id,
        root,
        tuple(errors),
    )


__all__ = [
    "FileCST", "FileDiagnostic", "FileLexResult", "FileToken", "SyntaxNode",
    "lex_file", "parse_file_cst",
]
