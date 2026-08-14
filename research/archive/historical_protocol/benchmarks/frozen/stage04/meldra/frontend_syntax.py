"""Lossless Stage 0.4 lexer, CST, and minimal surface parser."""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


FRONTEND_SYNTAX_SCHEMA_VERSION = 1
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_INTEGER = re.compile(r"[0-9]+")
_TWO_CHARACTER_TOKENS = frozenset(("::", "->", "=>", "==", "!=", "<=", ">="))
_ONE_CHARACTER_TOKENS = frozenset("{}()[]:,.=+-*/<>")
_TRIVIA_KINDS = frozenset(("WHITESPACE", "COMMENT"))
_BINARY_PRECEDENCE = {
    "==": 10,
    "!=": 10,
    "<": 10,
    "<=": 10,
    ">": 10,
    ">=": 10,
    "+": 20,
    "-": 20,
    "*": 30,
    "/": 30,
}


@dataclass(frozen=True)
class SourceSpan:
    start: int
    end: int
    line: int
    column: int
    end_line: int
    end_column: int

    def to_dict(self) -> dict[str, int]:
        return {
            "start": self.start,
            "end": self.end,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
        }


class FrontendSyntaxError(ValueError):
    def __init__(self, path: str, message: str, span: SourceSpan) -> None:
        self.path = path
        self.message = message
        self.span = span
        super().__init__(f"{path}:{span.line}:{span.column}: {message}")


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    span: SourceSpan
    synthetic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "span": self.span.to_dict(),
            "synthetic": self.synthetic,
        }


@dataclass(frozen=True)
class CallArgument:
    syntax_id: str
    span: SourceSpan
    name: str | None
    expression: "Expression"

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_id": self.syntax_id,
            "span": self.span.to_dict(),
            "name": self.name,
            "expression": self.expression.to_dict(),
        }


@dataclass(frozen=True)
class Expression:
    syntax_id: str
    kind: str
    span: SourceSpan
    value: Any = None
    name: str | None = None
    operator: str | None = None
    children: tuple["Expression", ...] = ()
    arguments: tuple[CallArgument, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_id": self.syntax_id,
            "kind": self.kind,
            "span": self.span.to_dict(),
            "value": self.value,
            "name": self.name,
            "operator": self.operator,
            "children": [item.to_dict() for item in self.children],
            "arguments": [item.to_dict() for item in self.arguments],
        }


@dataclass(frozen=True)
class Parameter:
    syntax_id: str
    span: SourceSpan
    name: str
    type_name: str
    capability: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_id": self.syntax_id,
            "span": self.span.to_dict(),
            "name": self.name,
            "type": self.type_name,
            "capability": self.capability,
        }


@dataclass(frozen=True)
class Member:
    syntax_id: str
    span: SourceSpan
    kind: str
    name: str
    type_name: str | None = None
    parameters: tuple[Parameter, ...] = ()
    return_type: str | None = None
    effect: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_id": self.syntax_id,
            "span": self.span.to_dict(),
            "kind": self.kind,
            "name": self.name,
            "type": self.type_name,
            "parameters": [item.to_dict() for item in self.parameters],
            "return_type": self.return_type,
            "effect": self.effect,
        }


@dataclass(frozen=True)
class MatchArm:
    syntax_id: str
    span: SourceSpan
    variant: str
    binding: str | None
    expression: Expression

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_id": self.syntax_id,
            "span": self.span.to_dict(),
            "variant": self.variant,
            "binding": self.binding,
            "expression": self.expression.to_dict(),
        }


@dataclass(frozen=True)
class Statement:
    syntax_id: str
    span: SourceSpan
    kind: str
    name: str | None = None
    type_name: str | None = None
    expression: Expression | None = None
    effect: str | None = None
    body: tuple["Statement", ...] = ()
    else_body: tuple["Statement", ...] = ()
    arms: tuple[MatchArm, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_id": self.syntax_id,
            "span": self.span.to_dict(),
            "kind": self.kind,
            "name": self.name,
            "type": self.type_name,
            "expression": self.expression.to_dict() if self.expression else None,
            "effect": self.effect,
            "body": [item.to_dict() for item in self.body],
            "else_body": [item.to_dict() for item in self.else_body],
            "arms": [item.to_dict() for item in self.arms],
        }


@dataclass(frozen=True)
class Declaration:
    syntax_id: str
    span: SourceSpan
    kind: str
    name: str
    parameters: tuple[Parameter, ...] = ()
    return_type: str | None = None
    members: tuple[Member, ...] = ()
    underlying_type: str | None = None
    value_type: str | None = None
    value: Expression | None = None
    body: tuple[Statement, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_id": self.syntax_id,
            "span": self.span.to_dict(),
            "kind": self.kind,
            "name": self.name,
            "parameters": [item.to_dict() for item in self.parameters],
            "return_type": self.return_type,
            "members": [item.to_dict() for item in self.members],
            "underlying_type": self.underlying_type,
            "value_type": self.value_type,
            "value": self.value.to_dict() if self.value else None,
            "body": [item.to_dict() for item in self.body],
        }


@dataclass(frozen=True)
class UseItem:
    syntax_id: str
    span: SourceSpan
    name: str
    alias: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_id": self.syntax_id,
            "span": self.span.to_dict(),
            "name": self.name,
            "alias": self.alias,
        }


@dataclass(frozen=True)
class UseDeclaration:
    syntax_id: str
    span: SourceSpan
    source: str
    items: tuple[UseItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_id": self.syntax_id,
            "span": self.span.to_dict(),
            "source": self.source,
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class ModuleSyntax:
    syntax_id: str
    span: SourceSpan
    package_path: str
    package_name: str
    module_name: str
    uses: tuple[UseDeclaration, ...]
    exports: tuple[str, ...]
    declarations: tuple[Declaration, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_id": self.syntax_id,
            "span": self.span.to_dict(),
            "package_path": self.package_path,
            "package_name": self.package_name,
            "module_name": self.module_name,
            "uses": [item.to_dict() for item in self.uses],
            "exports": list(self.exports),
            "declarations": [item.to_dict() for item in self.declarations],
        }


@dataclass(frozen=True)
class SourceCST:
    path: str
    source_bytes: bytes
    source_sha256: str
    tokens: tuple[Token, ...]
    module: ModuleSyntax
    schema_version: int = FRONTEND_SYNTAX_SCHEMA_VERSION

    def to_source_bytes(self) -> bytes:
        return b"".join(
            item.text.encode("utf-8")
            for item in self.tokens
            if not item.synthetic and item.kind != "EOF"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "path": self.path,
            "source_sha256": self.source_sha256,
            "tokens": [item.to_dict() for item in self.tokens],
            "module": self.module.to_dict(),
        }


def _span(
    byte_start: int,
    byte_end: int,
    line: int,
    column: int,
    end_line: int,
    end_column: int,
) -> SourceSpan:
    return SourceSpan(byte_start, byte_end, line, column, end_line, end_column)


def _node_id(
    path: str,
    source_sha256: str,
    kind: str,
    span: SourceSpan,
) -> str:
    payload = (
        f"{path}\0{source_sha256}\0{kind}\0{span.start}\0{span.end}"
    ).encode("utf-8")
    return "syn_" + hashlib.sha256(payload).hexdigest()


def _combine(first: SourceSpan, last: SourceSpan) -> SourceSpan:
    return SourceSpan(
        first.start,
        last.end,
        first.line,
        first.column,
        last.end_line,
        last.end_column,
    )


def lex_source(source: bytes | str, *, path: str = "<memory>") -> tuple[Token, ...]:
    data = source.encode("utf-8") if isinstance(source, str) else bytes(source)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        location = _span(exc.start, exc.end, 1, exc.start, 1, exc.end)
        raise FrontendSyntaxError(path, "source must be valid UTF-8", location) from exc

    byte_offsets = [0]
    for character in text:
        byte_offsets.append(byte_offsets[-1] + len(character.encode("utf-8")))

    tokens: list[Token] = []
    indent_stack = [0]
    bracket_depth = 0
    char_offset = 0
    line_number = 1

    def emit(
        kind: str,
        token_text: str,
        start_character: int,
        line: int,
        column: int,
        *,
        synthetic: bool = False,
    ) -> None:
        end_character = start_character + len(token_text)
        end_line = line
        end_column = column + len(token_text)
        if "\n" in token_text or "\r" in token_text:
            pieces = token_text.splitlines()
            end_line = line + max(0, len(pieces) - 1)
            end_column = len(pieces[-1]) if pieces else 0
        tokens.append(
            Token(
                kind,
                token_text,
                _span(
                    byte_offsets[start_character],
                    byte_offsets[end_character],
                    line,
                    column,
                    end_line,
                    end_column,
                ),
                synthetic,
            )
        )

    lines = text.splitlines(keepends=True)
    if text and not lines:
        lines = [text]
    for raw_line in lines:
        newline_match = re.search(r"(?:\r\n|\n|\r)$", raw_line)
        newline = newline_match.group(0) if newline_match else ""
        content = raw_line[: len(raw_line) - len(newline)] if newline else raw_line
        indent_length = len(content) - len(content.lstrip(" \t"))
        indentation = content[:indent_length]
        remainder = content[indent_length:]
        if "\t" in indentation:
            error_span = _span(
                byte_offsets[char_offset],
                byte_offsets[char_offset + indent_length],
                line_number,
                0,
                line_number,
                indent_length,
            )
            raise FrontendSyntaxError(path, "tabs are not allowed for indentation", error_span)
        significant_line = bool(remainder and not remainder.startswith("#"))
        if bracket_depth == 0 and significant_line:
            width = len(indentation)
            if width > indent_stack[-1]:
                indent_stack.append(width)
                emit(
                    "INDENT",
                    "",
                    char_offset + indent_length,
                    line_number,
                    indent_length,
                    synthetic=True,
                )
            elif width < indent_stack[-1]:
                while width < indent_stack[-1]:
                    indent_stack.pop()
                    emit(
                        "DEDENT",
                        "",
                        char_offset + indent_length,
                        line_number,
                        indent_length,
                        synthetic=True,
                    )
                if width != indent_stack[-1]:
                    error_span = _span(
                        byte_offsets[char_offset],
                        byte_offsets[char_offset + indent_length],
                        line_number,
                        0,
                        line_number,
                        indent_length,
                    )
                    raise FrontendSyntaxError(
                        path, "indentation does not match an outer block", error_span
                    )
        if indentation:
            emit(
                "WHITESPACE",
                indentation,
                char_offset,
                line_number,
                0,
            )

        index = indent_length
        while index < len(content):
            character = content[index]
            absolute = char_offset + index
            if character in " \t":
                end = index + 1
                while end < len(content) and content[end] in " \t":
                    end += 1
                emit(
                    "WHITESPACE",
                    content[index:end],
                    absolute,
                    line_number,
                    index,
                )
                index = end
                continue
            if character == "#":
                emit(
                    "COMMENT",
                    content[index:],
                    absolute,
                    line_number,
                    index,
                )
                index = len(content)
                continue
            identifier = _IDENTIFIER.match(content, index)
            if identifier:
                token_text = identifier.group(0)
                emit("IDENT", token_text, absolute, line_number, index)
                index = identifier.end()
                continue
            integer = _INTEGER.match(content, index)
            if integer:
                token_text = integer.group(0)
                emit("INT", token_text, absolute, line_number, index)
                index = integer.end()
                continue
            if character in {'"', "'"}:
                quote = character
                end = index + 1
                escaped = False
                while end < len(content):
                    current = content[end]
                    if escaped:
                        escaped = False
                    elif current == "\\":
                        escaped = True
                    elif current == quote:
                        end += 1
                        break
                    end += 1
                else:
                    error_span = _span(
                        byte_offsets[absolute],
                        byte_offsets[char_offset + len(content)],
                        line_number,
                        index,
                        line_number,
                        len(content),
                    )
                    raise FrontendSyntaxError(path, "unterminated string literal", error_span)
                emit("STRING", content[index:end], absolute, line_number, index)
                index = end
                continue
            pair = content[index : index + 2]
            if pair in _TWO_CHARACTER_TOKENS:
                emit("SYMBOL", pair, absolute, line_number, index)
                index += 2
                continue
            if character in _ONE_CHARACTER_TOKENS:
                emit("SYMBOL", character, absolute, line_number, index)
                if character in "({[":
                    bracket_depth += 1
                elif character in ")}]":
                    bracket_depth -= 1
                    if bracket_depth < 0:
                        error_span = tokens[-1].span
                        raise FrontendSyntaxError(
                            path, "unmatched closing delimiter", error_span
                        )
                index += 1
                continue
            error_span = _span(
                byte_offsets[absolute],
                byte_offsets[absolute + 1],
                line_number,
                index,
                line_number,
                index + 1,
            )
            raise FrontendSyntaxError(
                path, f"unexpected character {character!r}", error_span
            )
        if newline:
            emit(
                "NEWLINE",
                newline,
                char_offset + len(content),
                line_number,
                len(content),
            )
        char_offset += len(raw_line)
        line_number += 1

    if bracket_depth:
        eof_span = _span(len(data), len(data), line_number, 0, line_number, 0)
        raise FrontendSyntaxError(path, "unclosed delimiter", eof_span)
    while len(indent_stack) > 1:
        indent_stack.pop()
        emit("DEDENT", "", len(text), line_number, 0, synthetic=True)
    emit("EOF", "", len(text), line_number, 0, synthetic=True)

    reconstructed = b"".join(
        item.text.encode("utf-8")
        for item in tokens
        if not item.synthetic and item.kind != "EOF"
    )
    if reconstructed != data:
        raise AssertionError("lossless lexer did not preserve source bytes")
    return tuple(tokens)


class _Parser:
    def __init__(self, path: str, data: bytes, tokens: tuple[Token, ...]) -> None:
        self.path = path
        self.data = data
        self.source_sha256 = hashlib.sha256(data).hexdigest()
        self.tokens = tuple(item for item in tokens if item.kind not in _TRIVIA_KINDS)
        self.index = 0

    def parse(self) -> ModuleSyntax:
        self._skip_newlines()
        start = self._expect_keyword("package")
        package_path = self._parse_qualified_name()
        self._line_end()
        explicit_module: str | None = None
        uses: list[UseDeclaration] = []
        exports: list[str] = []
        declarations: list[Declaration] = []
        self._skip_newlines()
        while not self._peek_kind("EOF"):
            if self._peek_keyword("module"):
                if explicit_module is not None:
                    self._error(self._current(), "duplicate module declaration")
                self._advance()
                explicit_module = self._parse_qualified_name()
                self._line_end()
            elif self._peek_keyword("use"):
                uses.append(self._parse_use())
            elif self._peek_keyword("export"):
                self._advance()
                exports.extend(self._parse_name_list())
                self._line_end()
            else:
                declarations.append(self._parse_declaration())
            self._skip_newlines()
        eof = self._current()
        package_parts = package_path.split(".")
        if explicit_module is not None:
            package_name = package_path
            module_name = explicit_module
        elif len(package_parts) > 1:
            package_name = package_parts[0]
            module_name = ".".join(package_parts[1:])
        else:
            package_name = package_path
            module_name = Path(self.path).stem
        module_span = _combine(start.span, eof.span)
        return ModuleSyntax(
            self._id("module", module_span),
            module_span,
            package_path,
            package_name,
            module_name,
            tuple(uses),
            tuple(exports),
            tuple(declarations),
        )

    def _parse_use(self) -> UseDeclaration:
        start = self._expect_keyword("use")
        source = self._parse_qualified_name()
        self._expect_text("::")
        items: list[UseItem] = []
        if self._accept_text("{"):
            self._skip_newlines()
            while not self._accept_text("}"):
                items.append(self._parse_use_item())
                self._skip_newlines()
                if self._accept_text(","):
                    self._skip_newlines()
                    continue
                self._expect_text("}")
                break
        else:
            items.append(self._parse_use_item())
        end = self._previous()
        self._line_end()
        span = _combine(start.span, end.span)
        return UseDeclaration(self._id("use", span), span, source, tuple(items))

    def _parse_use_item(self) -> UseItem:
        start = self._expect_kind("IDENT")
        alias = start.text
        end = start
        if self._accept_keyword("as"):
            end = self._expect_kind("IDENT")
            alias = end.text
        span = _combine(start.span, end.span)
        return UseItem(self._id("use_item", span), span, start.text, alias)

    def _parse_name_list(self) -> tuple[str, ...]:
        braced = self._accept_text("{") is not None
        result: list[str] = []
        if braced:
            self._skip_newlines()
            while True:
                result.append(self._expect_kind("IDENT").text)
                self._skip_newlines()
                if self._accept_text(","):
                    self._skip_newlines()
                    continue
                self._expect_text("}")
                break
            return tuple(result)
        result.append(self._expect_kind("IDENT").text)
        while self._accept_text(","):
            result.append(self._expect_kind("IDENT").text)
        return tuple(result)

    def _parse_declaration(self) -> Declaration:
        start = self._expect_kind("IDENT")
        kind = start.text
        if kind not in {"record", "enum", "newtype", "capability", "value", "fn", "task"}:
            self._error(start, f"expected declaration, found {kind!r}")
        name = self._expect_kind("IDENT")
        if kind in {"record", "enum", "capability"}:
            members = self._parse_member_block(kind)
            end_span = members[-1].span if members else name.span
            span = _combine(start.span, end_span)
            return Declaration(
                self._id(kind, span), span, kind, name.text, members=members
            )
        if kind == "newtype":
            self._expect_text("=")
            underlying = self._parse_type_name()
            end = self._previous()
            self._line_end()
            span = _combine(start.span, end.span)
            return Declaration(
                self._id(kind, span),
                span,
                kind,
                name.text,
                underlying_type=underlying,
            )
        if kind == "value":
            self._expect_text(":")
            value_type = self._parse_type_name()
            self._expect_text("=")
            value = self._parse_expression()
            self._line_end()
            span = _combine(start.span, value.span)
            return Declaration(
                self._id(kind, span),
                span,
                kind,
                name.text,
                value_type=value_type,
                value=value,
            )
        parameters = self._parse_parameters()
        self._expect_text("->")
        return_type = self._parse_type_name()
        body = self._parse_statement_block()
        end_span = body[-1].span if body else self._previous().span
        span = _combine(start.span, end_span)
        return Declaration(
            self._id(kind, span),
            span,
            kind,
            name.text,
            parameters=parameters,
            return_type=return_type,
            body=body,
        )

    def _parse_member_block(self, declaration_kind: str) -> tuple[Member, ...]:
        self._block_open()
        members: list[Member] = []
        self._skip_newlines()
        while not self._peek_kind("DEDENT"):
            start = self._expect_kind("IDENT")
            if declaration_kind == "record":
                self._expect_text(":")
                type_name = self._parse_type_name()
                end = self._previous()
                self._line_end()
                span = _combine(start.span, end.span)
                members.append(
                    Member(self._id("field", span), span, "field", start.text, type_name)
                )
            elif declaration_kind == "enum":
                payload_type = None
                end = start
                if self._accept_text("("):
                    payload_type = self._parse_type_name()
                    end = self._expect_text(")")
                self._line_end()
                span = _combine(start.span, end.span)
                members.append(
                    Member(
                        self._id("variant", span),
                        span,
                        "variant",
                        start.text,
                        payload_type,
                    )
                )
            else:
                parameters = self._parse_parameters()
                self._expect_text("->")
                return_type = self._parse_type_name()
                self._expect_keyword("uses")
                effect = self._parse_qualified_name()
                end = self._previous()
                self._line_end()
                span = _combine(start.span, end.span)
                members.append(
                    Member(
                        self._id("capability_member", span),
                        span,
                        "capability_member",
                        start.text,
                        parameters=parameters,
                        return_type=return_type,
                        effect=effect,
                    )
                )
            self._skip_newlines()
        self._expect_kind("DEDENT")
        return tuple(members)

    def _parse_parameters(self) -> tuple[Parameter, ...]:
        self._expect_text("(")
        parameters: list[Parameter] = []
        self._skip_newlines()
        while not self._accept_text(")"):
            start = self._expect_kind("IDENT")
            self._expect_text(":")
            capability = self._accept_keyword("cap") is not None
            type_name = self._parse_type_name()
            end = self._previous()
            span = _combine(start.span, end.span)
            parameters.append(
                Parameter(
                    self._id("parameter", span),
                    span,
                    start.text,
                    type_name,
                    capability,
                )
            )
            self._skip_newlines()
            if self._accept_text(","):
                self._skip_newlines()
                continue
            self._expect_text(")")
            break
        return tuple(parameters)

    def _parse_statement_block(self) -> tuple[Statement, ...]:
        self._block_open()
        body: list[Statement] = []
        self._skip_newlines()
        while not self._peek_kind("DEDENT"):
            body.append(self._parse_statement())
            self._skip_newlines()
        self._expect_kind("DEDENT")
        if not body:
            self._error(self._previous(), "empty executable block")
        return tuple(body)

    def _parse_statement(self) -> Statement:
        if self._peek_keyword("uses"):
            start = self._advance()
            effect = self._parse_qualified_name()
            end = self._previous()
            self._line_end()
            span = _combine(start.span, end.span)
            return Statement(self._id("uses", span), span, "uses", effect=effect)
        if self._peek_keyword("let"):
            start = self._advance()
            name = self._expect_kind("IDENT")
            type_name = None
            if self._accept_text(":"):
                type_name = self._parse_type_name()
            self._expect_text("=")
            expression = self._parse_expression()
            self._line_end()
            span = _combine(start.span, expression.span)
            return Statement(
                self._id("let", span),
                span,
                "let",
                name=name.text,
                type_name=type_name,
                expression=expression,
            )
        if self._peek_keyword("if"):
            start = self._advance()
            condition = self._parse_expression(stop_texts={":"})
            body = self._parse_statement_block()
            self._skip_newlines()
            else_body: tuple[Statement, ...] = ()
            if self._accept_keyword("else"):
                else_body = self._parse_statement_block()
            end_span = (else_body or body)[-1].span
            span = _combine(start.span, end_span)
            return Statement(
                self._id("if", span),
                span,
                "if",
                expression=condition,
                body=body,
                else_body=else_body,
            )
        if self._peek_keyword("match"):
            return self._parse_match()
        expression = self._parse_expression()
        self._line_end()
        return Statement(
            self._id("expression_statement", expression.span),
            expression.span,
            "expression",
            expression=expression,
        )

    def _parse_match(self) -> Statement:
        start = self._expect_keyword("match")
        subject = self._parse_expression(stop_texts={":"})
        self._block_open()
        arms: list[MatchArm] = []
        self._skip_newlines()
        while not self._peek_kind("DEDENT"):
            variant = self._expect_kind("IDENT")
            binding = None
            if self._accept_text("("):
                binding = self._expect_kind("IDENT").text
                self._expect_text(")")
            self._expect_text(":")
            expression = self._parse_expression()
            self._line_end()
            span = _combine(variant.span, expression.span)
            arms.append(
                MatchArm(
                    self._id("match_arm", span),
                    span,
                    variant.text,
                    binding,
                    expression,
                )
            )
            self._skip_newlines()
        self._expect_kind("DEDENT")
        if not arms:
            self._error(self._previous(), "match must contain at least one arm")
        span = _combine(start.span, arms[-1].span)
        return Statement(
            self._id("match", span),
            span,
            "match",
            expression=subject,
            arms=tuple(arms),
        )

    def _parse_expression(
        self,
        minimum_precedence: int = 0,
        *,
        stop_texts: set[str] | None = None,
    ) -> Expression:
        stops = stop_texts or set()
        token = self._current()
        if token.text in {"-"}:
            operator = self._advance()
            child = self._parse_expression(40, stop_texts=stops)
            span = _combine(operator.span, child.span)
            left = Expression(
                self._id("unary", span),
                "unary",
                span,
                operator=operator.text,
                children=(child,),
            )
        elif token.text == "(":
            self._advance()
            left = self._parse_expression(stop_texts={")"})
            self._expect_text(")")
        elif token.kind == "INT":
            self._advance()
            left = Expression(
                self._id("integer", token.span), "literal", token.span, value=int(token.text)
            )
        elif token.kind == "STRING":
            self._advance()
            try:
                value = ast.literal_eval(token.text)
            except (SyntaxError, ValueError) as exc:
                raise FrontendSyntaxError(
                    self.path, "invalid string literal", token.span
                ) from exc
            left = Expression(
                self._id("string", token.span), "literal", token.span, value=value
            )
        elif token.kind == "IDENT" and token.text in {"true", "false", "unit"}:
            self._advance()
            value = {"true": True, "false": False, "unit": None}[token.text]
            left = Expression(
                self._id("literal", token.span), "literal", token.span, value=value
            )
        elif token.kind == "IDENT":
            self._advance()
            left = Expression(
                self._id("name", token.span), "name", token.span, name=token.text
            )
        else:
            self._error(token, "expected expression")

        while True:
            if self._accept_text("."):
                member = self._expect_kind("IDENT")
                span = _combine(left.span, member.span)
                left = Expression(
                    self._id("field", span),
                    "field",
                    span,
                    name=member.text,
                    children=(left,),
                )
                continue
            if self._accept_text("("):
                arguments: list[CallArgument] = []
                self._skip_newlines()
                close = self._accept_text(")")
                while close is None:
                    argument_start = self._current()
                    argument_name = None
                    if (
                        argument_start.kind == "IDENT"
                        and self._peek_offset(1).text == ":"
                    ):
                        argument_name = self._advance().text
                        self._expect_text(":")
                    expression = self._parse_expression(stop_texts={",", ")"})
                    argument_span = _combine(argument_start.span, expression.span)
                    arguments.append(
                        CallArgument(
                            self._id("call_argument", argument_span),
                            argument_span,
                            argument_name,
                            expression,
                        )
                    )
                    self._skip_newlines()
                    if self._accept_text(","):
                        self._skip_newlines()
                        close = self._accept_text(")")
                        continue
                    close = self._expect_text(")")
                span = _combine(left.span, close.span)
                left = Expression(
                    self._id("call", span),
                    "call",
                    span,
                    children=(left,),
                    arguments=tuple(arguments),
                )
                continue
            current = self._current()
            if (
                current.kind in {"NEWLINE", "DEDENT", "EOF"}
                or current.text in stops
            ):
                break
            precedence = _BINARY_PRECEDENCE.get(current.text)
            if precedence is None or precedence < minimum_precedence:
                break
            operator = self._advance()
            right = self._parse_expression(
                precedence + 1, stop_texts=stops
            )
            span = _combine(left.span, right.span)
            left = Expression(
                self._id("binary", span),
                "binary",
                span,
                operator=operator.text,
                children=(left, right),
            )
        return left

    def _parse_type_name(self) -> str:
        return self._parse_qualified_name()

    def _parse_qualified_name(self) -> str:
        parts = [self._expect_kind("IDENT").text]
        while self._accept_text("."):
            parts.append(self._expect_kind("IDENT").text)
        return ".".join(parts)

    def _block_open(self) -> None:
        self._expect_text(":")
        if not self._accept_kind("NEWLINE"):
            self._error(self._current(), "block must start on the next line")
        self._skip_newlines()
        self._expect_kind("INDENT")

    def _line_end(self) -> None:
        if self._accept_kind("NEWLINE"):
            return
        if self._peek_kind("EOF") or self._peek_kind("DEDENT"):
            return
        self._error(self._current(), "expected end of line")

    def _skip_newlines(self) -> None:
        while self._accept_kind("NEWLINE"):
            pass

    def _id(self, kind: str, span: SourceSpan) -> str:
        return _node_id(self.path, self.source_sha256, kind, span)

    def _current(self) -> Token:
        return self.tokens[min(self.index, len(self.tokens) - 1)]

    def _previous(self) -> Token:
        return self.tokens[max(0, self.index - 1)]

    def _peek_offset(self, offset: int) -> Token:
        return self.tokens[min(self.index + offset, len(self.tokens) - 1)]

    def _advance(self) -> Token:
        token = self._current()
        if token.kind != "EOF":
            self.index += 1
        return token

    def _peek_kind(self, kind: str) -> bool:
        return self._current().kind == kind

    def _peek_keyword(self, keyword: str) -> bool:
        token = self._current()
        return token.kind == "IDENT" and token.text == keyword

    def _accept_kind(self, kind: str) -> Token | None:
        if self._peek_kind(kind):
            return self._advance()
        return None

    def _accept_text(self, text: str) -> Token | None:
        if self._current().text == text:
            return self._advance()
        return None

    def _accept_keyword(self, keyword: str) -> Token | None:
        if self._peek_keyword(keyword):
            return self._advance()
        return None

    def _expect_kind(self, kind: str) -> Token:
        token = self._current()
        if token.kind != kind:
            self._error(token, f"expected {kind}, found {token.text!r}")
        return self._advance()

    def _expect_text(self, text: str) -> Token:
        token = self._current()
        if token.text != text:
            self._error(token, f"expected {text!r}, found {token.text!r}")
        return self._advance()

    def _expect_keyword(self, keyword: str) -> Token:
        token = self._current()
        if token.kind != "IDENT" or token.text != keyword:
            self._error(token, f"expected {keyword!r}, found {token.text!r}")
        return self._advance()

    def _error(self, token: Token, message: str) -> None:
        raise FrontendSyntaxError(self.path, message, token.span)


def parse_source(source: bytes | str, *, path: str = "<memory>") -> SourceCST:
    data = source.encode("utf-8") if isinstance(source, str) else bytes(source)
    tokens = lex_source(data, path=path)
    module = _Parser(path, data, tokens).parse()
    cst = SourceCST(
        path=path,
        source_bytes=data,
        source_sha256=hashlib.sha256(data).hexdigest(),
        tokens=tokens,
        module=module,
    )
    if cst.to_source_bytes() != data:
        raise AssertionError("CST roundtrip did not preserve source bytes")
    return cst


def parse_sources(sources: Mapping[str, bytes | str]) -> tuple[SourceCST, ...]:
    if not sources:
        raise ValueError("at least one Meldra source is required")
    return tuple(
        parse_source(source, path=path)
        for path, source in sorted(sources.items())
    )


__all__ = [
    "FRONTEND_SYNTAX_SCHEMA_VERSION",
    "CallArgument",
    "Declaration",
    "Expression",
    "FrontendSyntaxError",
    "MatchArm",
    "Member",
    "ModuleSyntax",
    "Parameter",
    "SourceCST",
    "SourceSpan",
    "Statement",
    "Token",
    "UseDeclaration",
    "UseItem",
    "lex_source",
    "parse_source",
    "parse_sources",
]
