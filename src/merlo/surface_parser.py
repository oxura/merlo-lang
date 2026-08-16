from __future__ import annotations

import re
from dataclasses import dataclass

from merlo.frontend.lexer import ExpressionLexError, ExpressionToken, lex_expression
from merlo.frontend.file_syntax import FileCST, SyntaxNode, parse_file_cst
from merlo.module_syntax import ModuleSyntaxError, parse_module_prelude
from merlo.surface_ast import (
    SourceSpan,
    SurfaceAnnotation,
    SurfaceAssignment,
    SurfaceBreak,
    SurfaceBinary,
    SurfaceBinding,
    SurfaceCall,
    SurfaceCallArgument,
    SurfaceCase,
    SurfaceComment,
    SurfaceContinue,
    SurfaceDeclaration,
    SurfaceEnum,
    SurfaceEnumVariant,
    SurfaceEnsure,
    SurfaceExpression,
    SurfaceExpressionStatement,
    SurfaceField,
    SurfaceFlow,
    SurfaceFlowStep,
    SurfaceFor,
    SurfaceFunction,
    SurfaceHole,
    SurfaceIf,
    SurfaceImplementation,
    SurfaceInterface,
    SurfaceInterfaceMethod,
    SurfaceImplicitReceiver,
    SurfaceIndex,
    SurfaceInvariant,
    SurfaceLambda,
    SurfaceList,
    SurfaceLiteral,
    SurfaceMachine,
    SurfaceMatch,
    SurfaceMember,
    SurfaceName,
    SurfaceParallel,
    SurfaceParameter,
    SurfacePass,
    SurfacePolicy,
    SurfacePrint,
    SurfaceRecord,
    SurfaceProgram,
    SurfaceRequire,
    SurfaceReturn,
    SurfaceState,
    SurfaceStatement,
    SurfaceTransition,
    SurfaceTry,
    SurfaceTypeParameter,
    SurfaceUnary,
    SurfaceUses,
    SurfaceWhile,
)


class SurfaceSyntaxError(ValueError):
    def __init__(self, code: str, message: str, span: SourceSpan) -> None:
        self.code = code
        self.message = message
        self.span = span
        super().__init__(f"{span.path}:{span.start_line}:{span.start_column}: {code}: {message}")


@dataclass(frozen=True)
class _Line:
    number: int
    indent: int
    text: str
    raw: str

def _trivia(line: _Line) -> bool:
    text = line.text.strip()
    return not text or text.startswith("#")



def _span(path: str, line: _Line, *, end_line: _Line | None = None) -> SourceSpan:
    end = end_line or line
    return SourceSpan(
        path,
        line.number,
        line.indent + 1,
        end.number,
        len(end.raw) + 1,
    )


def _type_name(source: str) -> str:
    source = re.sub(r"\s+", "", source)
    if source.endswith("?"):
        return f"Option[{_type_name(source[:-1])}]"
    if source.startswith("fn("):
        depth = 0
        closing = None
        for index, character in enumerate(source[2:], 2):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing is None or not source[closing + 1 :].startswith("->"):
            raise ValueError(f"malformed function type {source!r}")
        parameters = _split_top_level_commas(source[3:closing])
        result = source[closing + 3 :]
        if not parameters or not result:
            raise ValueError(f"malformed function type {source!r}")
        return "Fn[" + ",".join(
            (*(_type_name(item) for item in parameters), _type_name(result))
        ) + "]"
    source = source.replace("<", "[").replace(">", "]")
    aliases = {"Int": "Int64", "UInt": "UInt64", "Float": "Float64"}
    for alias, canonical in aliases.items():
        source = re.sub(rf"\b{alias}\b", canonical, source)
    return source

def _split_top_level_commas(source: str) -> tuple[str, ...]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(source):
        if character in "[(<":
            depth += 1
        elif character in "])" or (
            character == ">" and depth > 0
        ):
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(source[start:index])
            start = index + 1
    parts.append(source[start:])
    return tuple(parts)


_FUNCTION_HEADER = re.compile(
    r"(?:(fn|task)\s+)?([A-Za-z_]\w*)(?:\[([^\]]+)\])?"
    r"\((.*)\)\s*(?:->\s*([^:=]+))?\s*([:=])\s*(.*)"
)

class _ExpressionParser:
    _INFIX_PRECEDENCE = {
        "or": 1,
        "and": 2,
        "==": 3,
        "!=": 3,
        "<": 3,
        "<=": 3,
        ">": 3,
        ">=": 3,
        "is": 3,
        "is not": 3,
        "in": 3,
        "not in": 3,
        "|": 4,
        "^": 5,
        "&": 6,
        "<<": 7,
        ">>": 7,
        "+": 8,
        "-": 8,
        "*": 9,
        "/": 9,
        "//": 9,
        "%": 9,
    }
    _COMPARISONS = frozenset(
        {"==", "!=", "<", "<=", ">", ">=", "is", "is not", "in", "not in"}
    )

    def __init__(
        self,
        source: str,
        path: str,
        line: _Line,
        *,
        base_column: int,
        tokens: tuple[ExpressionToken, ...] | None = None,
    ) -> None:
        self.source = source
        self.path = path
        self.line = line
        self.base_column = base_column
        if tokens is None:
            try:
                self.tokens = lex_expression(source)
            except ExpressionLexError as exc:
                self._error("InvalidExpression", exc.message, exc.position)
        else:
            self.tokens = tokens
        self.index = 0

    def _error(self, code: str, message: str, position: int | None = None) -> None:
        if position is None:
            position = self.tokens[self.index].start if hasattr(self, "tokens") else 0
        raise SurfaceSyntaxError(
            code,
            message,
            SourceSpan(
                self.path,
                self.line.number,
                self.line.indent + self.base_column + position + 1,
                self.line.number,
                len(self.line.raw) + 1,
            ),
        )

    @property
    def current(self) -> ExpressionToken:
        return self.tokens[self.index]

    def _peek(self, count: int = 1) -> ExpressionToken:
        return self.tokens[min(self.index + count, len(self.tokens) - 1)]

    def _take(self, text: str | None = None) -> ExpressionToken:
        token = self.current
        if text is not None and token.text != text:
            self._error("InvalidExpression", f"expected {text!r}")
        self.index += 1
        return token

    def _span(self, start: int, end: int) -> SourceSpan:
        return SourceSpan(
            self.path,
            self.line.number,
            self.line.indent + self.base_column + start + 1,
            self.line.number,
            self.line.indent + self.base_column + end + 1,
        )

    def parse(self) -> SurfaceExpression:
        if self.current.kind == "eof":
            self._error("InvalidExpression", "expression expected", 0)
        expression = self._expression(0)
        if self.current.kind != "eof":
            self._error("InvalidExpression", f"unexpected token {self.current.text!r}")
        return expression

    def _infix(self) -> tuple[str, int, int] | None:
        token = self.current
        text = token.text
        if text == "is" and self._peek().text == "not":
            return "is not", 3, 2
        if text == "not" and self._peek().text == "in":
            return "not in", 3, 2
        precedence = self._INFIX_PRECEDENCE.get(text)
        if precedence is None:
            return None
        return text, precedence, 1

    def _expression(self, minimum: int) -> SurfaceExpression:
        if (
            minimum == 0
            and self.current.kind == "identifier"
            and self._peek().text == "=>"
        ):
            parameter = self._take()
            self._take("=>")
            body = self._expression(0)
            return SurfaceLambda(
                self._span(parameter.start, body.span.end_column - self.line.indent - self.base_column - 1),
                (parameter.text,),
                body,
            )
        left = self._prefix()
        comparisons: list[SurfaceBinary] = []
        while True:
            infix = self._infix()
            if infix is None or infix[1] < minimum:
                break
            operator, precedence, width = infix
            self.index += width
            right = self._expression(precedence + 1)
            if operator in self._COMPARISONS:
                comparisons.append(SurfaceBinary(operator, left, right, self._span(
                    left.span.start_column - self.line.indent - self.base_column - 1,
                    right.span.end_column - self.line.indent - self.base_column - 1,
                )))
                left = right
                continue
            if comparisons:
                left = self._chain(comparisons)
                comparisons = []
            left = SurfaceBinary(operator, left, right, self._span(
                left.span.start_column - self.line.indent - self.base_column - 1,
                right.span.end_column - self.line.indent - self.base_column - 1,
            ))
        if comparisons:
            left = self._chain(comparisons)
        return left

    def _chain(self, comparisons: list[SurfaceBinary]) -> SurfaceExpression:
        result: SurfaceExpression = comparisons[0]
        for comparison in comparisons[1:]:
            result = SurfaceBinary("and", result, comparison, self._span(
                result.span.start_column - self.line.indent - self.base_column - 1,
                comparison.span.end_column - self.line.indent - self.base_column - 1,
            ))
        return result

    def _prefix(self) -> SurfaceExpression:
        token = self.current
        if token.text in {"+", "-", "~"}:
            self._take()
            operand = self._expression(10)
            expression: SurfaceExpression = SurfaceUnary(
                self._span(token.start, operand.span.end_column - self.line.indent - self.base_column - 1),
                token.text,
                operand,
            )
        elif token.text == "not":
            self._take()
            operand = self._expression(3)
            expression = SurfaceUnary(
                self._span(token.start, operand.span.end_column - self.line.indent - self.base_column - 1),
                "not",
                operand,
            )
        else:
            expression = self._atom()
        while True:
            if self.current.text == ".":
                self._take()
                field = self.current
                if field.kind != "identifier":
                    self._error("InvalidExpression", "member name expected")
                self._take()
                expression = SurfaceMember(self._span(
                    expression.span.start_column - self.line.indent - self.base_column - 1,
                    field.end,
                ), expression, field.text)
            elif self.current.text == "[":
                self._take()
                index = self._expression(0)
                if self.current.text != "]":
                    self._error("InvalidExpression", "expected ']'", self.current.start)
                closing = self._take()
                expression = SurfaceIndex(self._span(
                    expression.span.start_column - self.line.indent - self.base_column - 1,
                    closing.end,
                ), expression, index)
            elif self.current.text == "(":
                self._take()
                arguments: list[SurfaceCallArgument] = []
                named = False
                if self.current.text != ")":
                    while True:
                        is_named = (
                            self.current.kind == "identifier"
                            and self._peek().text in {":", "="}
                        )
                        if is_named:
                            name = self._take().text
                            self._take()
                            named = True
                        else:
                            name = None
                            if named:
                                self._error(
                                    "PositionalAfterKeyword",
                                    "positional argument follows keyword argument",
                                )
                        value = self._expression(0)
                        arguments.append(SurfaceCallArgument(value.span, value, name))
                        if self.current.text != ",":
                            break
                        self._take(",")
                        if self.current.text == ")":
                            break
                if self.current.text != ")":
                    self._error("InvalidExpression", "expected ')'", self.current.start)
                closing = self._take()
                expression = SurfaceCall(self._span(
                    expression.span.start_column - self.line.indent - self.base_column - 1,
                    closing.end,
                ), expression, tuple(arguments))
            elif self.current.text == "?":
                closing = self._take()
                expression = SurfaceTry(self._span(
                    expression.span.start_column - self.line.indent - self.base_column - 1,
                    closing.end,
                ), expression)
            else:
                break
        return expression

    def _atom(self) -> SurfaceExpression:
        token = self.current
        if token.text == "?":
            self._take()
            return SurfaceHole(
                self._span(token.start, token.end)
            )
        if token.text in {"{", "}"}:
            self._error("UnsupportedExpression", "DictOrSet", token.start)
        if token.text == ".":
            dot = self._take()
            field = self.current
            if field.kind != "identifier":
                self._error("InvalidExpression", "implicit receiver field expected")
            self._take()
            return SurfaceImplicitReceiver(self._span(dot.start, field.end), field.text)
        if token.text == "(":
            self._take()
            expression = self._expression(0)
            if self.current.text == ",":
                self._error("UnsupportedExpression", "Tuple", token.start)
            if self.current.text != ")":
                self._error("InvalidExpression", "expected ')'", self.current.start)
            self._take()
            return expression
        if token.text == "[":
            opening = self._take()
            items: list[SurfaceExpression] = []
            if self.current.text != "]":
                while True:
                    items.append(self._expression(0))
                    if self.current.text != ",":
                        break
                    self._take(",")
                    if self.current.text == "]":
                        break
            if self.current.text != "]":
                self._error("InvalidExpression", "expected ']'", self.current.start)
            closing = self._take()
            return SurfaceList(self._span(opening.start, closing.end), tuple(items))
        if token.kind == "literal":
            self._take()
            if isinstance(token.value, tuple):
                value, kind = token.value
            else:
                value = token.value
                kind = "Bytes" if isinstance(value, bytes) else "Text"
                while (
                    self.current.kind == "literal"
                    and isinstance(self.current.value, type(value))
                ):
                    adjacent = self._take()
                    value += adjacent.value
                    token = ExpressionToken(
                        "literal",
                        token.text,
                        token.start,
                        adjacent.end,
                        value,
                    )
            return SurfaceLiteral(self._span(token.start, token.end), value, kind)
        if token.kind == "identifier":
            self._take()
            aliases = {
                "true": (True, "Bool"),
                "false": (False, "Bool"),
                "True": (True, "Bool"),
                "False": (False, "Bool"),
                "none": (None, "None"),
                "null": (None, "None"),
                "None": (None, "None"),
            }
            if token.text in aliases:
                value, kind = aliases[token.text]
                return SurfaceLiteral(self._span(token.start, token.end), value, kind)
            return SurfaceName(token.text, self._span(token.start, token.end))
        self._error("InvalidExpression", "expression expected", token.start)
        raise AssertionError("unreachable")


def _validate_implicit(expression: SurfaceExpression, path: str, line: _Line) -> None:
    implicit = [item for item in expression.walk() if isinstance(item, SurfaceImplicitReceiver)]
    if not implicit:
        return
    calls = [item for item in expression.walk() if isinstance(item, SurfaceCall)]
    if not calls:
        raise SurfaceSyntaxError(
            "ImplicitReceiverOutsideCallable",
            "implicit receiver is allowed only in a call argument",
            implicit[0].span,
        )
    placements: set[int] = set()
    for call in calls:
        for argument in call.arguments:
            nested = [
                item
                for item in argument.value.walk()
                if isinstance(item, SurfaceImplicitReceiver)
            ]
            if len(nested) > 1:
                raise SurfaceSyntaxError(
                    "NestedImplicitReceiverForbidden",
                    "one implicit receiver is allowed per callable argument",
                    nested[1].span,
                )
            if nested and any(
                isinstance(item, SurfaceCall)
                for item in argument.value.walk()
            ):
                raise SurfaceSyntaxError(
                    "NestedImplicitReceiverForbidden",
                    "implicit receiver cannot contain a nested call",
                    nested[0].span,
                )
            placements.update(id(item) for item in nested)
    if any(id(item) not in placements for item in implicit):
        raise SurfaceSyntaxError(
            "ImplicitReceiverOutsideCallable",
            "implicit receiver is allowed only in a call argument",
            implicit[0].span,
        )


def _parse_expression(
    source: str,
    path: str,
    line: _Line,
    *,
    base_column: int = 0,
    tokens: tuple[ExpressionToken, ...] | None = None,
) -> SurfaceExpression:
    expression = _ExpressionParser(
        source,
        path,
        line,
        base_column=base_column,
        tokens=tokens,
    ).parse()
    _validate_implicit(expression, path, line)
    return expression


def _lines(
    source: str,
    path: str,
    *,
    line_offset: int = 0,
) -> list[_Line]:
    physical: list[_Line] = []
    for number, raw in enumerate(source.splitlines(), 1 + line_offset):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise SurfaceSyntaxError(
                "TabIndentationForbidden",
                "use four spaces",
                SourceSpan(path, number, 1, number, len(raw) + 1),
            )
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 4:
            raise SurfaceSyntaxError(
                "InvalidIndentation",
                "indentation must be a multiple of four spaces",
                SourceSpan(path, number, 1, number, indent + 1),
            )
        physical.append(_Line(number, indent, raw[indent:], raw))

    def update_delimiters(text: str, stack: list[str]) -> None:
        quote: str | None = None
        escaped = False
        for character in text:
            if quote is not None:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
                continue
            if character in {'"', "'"}:
                quote = character
            elif character == "#":
                break
            elif character in "([{":
                stack.append(character)
            elif character in ")]}" and stack:
                # parse_file_cst rejects mismatches before this transitional
                # line view is built. The typed stack still prevents an
                # arbitrary closer from silently balancing another opener.
                expected = {"(": ")", "[": "]", "{": "}"}[stack[-1]]
                if character == expected:
                    stack.pop()

    result: list[_Line] = []
    pending: _Line | None = None
    pieces: list[str] = []
    delimiters: list[str] = []
    for line in physical:
        if pending is None:
            pending = line
            pieces = [line.text]
            delimiters = []
            update_delimiters(line.text, delimiters)
        else:
            pieces.append(line.text.strip())
            update_delimiters(line.text, delimiters)
        if not delimiters:
            text = " ".join(piece for piece in pieces if piece)
            result.append(
                _Line(
                    pending.number,
                    pending.indent,
                    text,
                    (" " * pending.indent) + text,
                )
            )
            pending = None
            pieces = []
    if pending is not None:
        text = " ".join(piece for piece in pieces if piece)
        result.append(
            _Line(
                pending.number,
                pending.indent,
                text,
                (" " * pending.indent) + text,
            )
        )
    return result


class _Parser:
    def __init__(
        self,
        source: str,
        path: str,
        cst: FileCST,
        *,
        line_offset: int = 0,
    ) -> None:
        self.source = source
        self.path = path
        self.cst = cst
        self.line_offset = line_offset
        self.lines = _lines(source, path, line_offset=line_offset)
        self.statement_anchors: dict[int, SyntaxNode] = {}
        for declaration in cst.declarations:
            for node in declaration.walk()[1:]:
                if node.kind in {
                    "header", "block", "parameters", "parameter", "type",
                    "type_parameters", "type_parameter", "expression",
                }:
                    continue
                line = next(
                    token.line
                    for token in node.tokens
                    if token.start == node.start
                ) + line_offset
                if line in self.statement_anchors:
                    raise SurfaceSyntaxError(
                        "CSTStatementMismatch",
                        f"multiple CST statement anchors on line {line}",
                        SourceSpan(path, line, 1, line, 1),
                    )
                self.statement_anchors[line] = node
        self.index = 0

    def _skip_blank(self) -> None:
        while self.index < len(self.lines) and _trivia(self.lines[self.index]):
            self.index += 1

    def parse(self) -> SurfaceProgram:
        try:
            prelude = parse_module_prelude(
                self.source,
                path=self.path,
                require_module=False,
            )
        except ModuleSyntaxError as exc:
            line = self.lines[exc.line - 1] if 0 < exc.line <= len(self.lines) else _Line(
                exc.line,
                0,
                "",
                "",
            )
            raise SurfaceSyntaxError(exc.code, exc.message, _span(self.path, line)) from exc

        declarations: list[SurfaceDeclaration] = []
        anchors = tuple(
            node
            for node in self.cst.declarations
            if node.kind not in {"module", "use", "import"}
        )
        anchor_index = 0
        self.index = prelude.body_source_lines[0] - 1 if prelude.body_source_lines else len(self.lines)
        self._skip_blank()
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.indent:
                raise SurfaceSyntaxError("UnexpectedIndent", "top-level declaration expected", _span(self.path, line))
            if anchor_index >= len(anchors):
                raise SurfaceSyntaxError(
                    "CSTDeclarationMismatch",
                    "semantic declaration has no CST anchor",
                    _span(self.path, line),
                )
            anchor = anchors[anchor_index]
            anchor_line = next(
                token.line
                for token in anchor.tokens
                if token.start == anchor.start
            ) + self.line_offset
            if anchor_line != line.number:
                raise SurfaceSyntaxError(
                    "CSTDeclarationMismatch",
                    f"expected CST declaration anchor on line {line.number}",
                    _span(self.path, line),
                )
            declarations.append(self._declaration(anchor))
            anchor_index += 1
            self._skip_blank()
        if anchor_index != len(anchors):
            anchor = anchors[anchor_index]
            token = next(token for token in anchor.tokens if token.start == anchor.start)
            line = _Line(
                token.line + self.line_offset,
                token.column - 1,
                token.text,
                token.text,
            )
            raise SurfaceSyntaxError(
                "CSTDeclarationMismatch",
                "CST declaration anchor was not consumed",
                _span(self.path, line),
            )
        end = self.lines[-1] if self.lines else _Line(1, 0, "", "")
        return SurfaceProgram(
            SourceSpan(self.path, 1, 1, end.number, len(end.raw) + 1),
            tuple(declarations),
            prelude.module,
            prelude.imports,
            self.source,
        )

    def _declaration(self, anchor: SyntaxNode) -> SurfaceDeclaration:
        line = self.lines[self.index]
        raw = line.text
        exported = bool(re.match(r"export\s+", raw))
        raw = re.sub(r"^export\s+", "", raw)
        kind = anchor.kind
        if kind == "flow":
            match = re.fullmatch(
                r"(durable\s+)?flow\s+([a-z_]\w*)\((.*)\)\s*->\s*([^:]+)\s*:",
                raw,
            )
            if match:
                return self._flow(match, exported, anchor)
        elif kind == "machine":
            match = re.fullmatch(r"machine\s+([A-Z]\w*)\((.*)\)\s*:", raw)
            if match:
                return self._machine(match, exported, anchor)
        elif kind == "interface":
            match = re.fullmatch(r"interface\s+([A-Z]\w*)\s*:", raw)
            if match:
                return self._interface(match.group(1), exported)
        elif kind == "impl":
            match = re.fullmatch(r"impl\s+([A-Z]\w*)\s+for\s+(.+)\s*:", raw)
            if match:
                if exported:
                    raise SurfaceSyntaxError("ExportedImplementationForbidden", raw, _span(self.path, line))
                return self._implementation(match.group(1), _type_name(match.group(2)))
        elif kind == "enum":
            match = re.fullmatch(r"enum\s+([A-Z]\w*)\s*:", raw)
            if match:
                return self._enum(match.group(1), exported)
        elif kind == "record":
            match = re.fullmatch(r"record\s+([A-Z]\w*)\s*:", raw)
            if match:
                return self._record(match.group(1), exported)
        elif kind in {"fn", "task"}:
            match = _FUNCTION_HEADER.fullmatch(raw)
            if match:
                return self._function(match, exported, anchor)
        elif kind == "statement":
            record_match = re.fullmatch(r"([A-Z]\w*)\s*:", raw)
            if record_match:
                return self._record(record_match.group(1), exported)
            function_match = _FUNCTION_HEADER.fullmatch(raw)
            if function_match:
                return self._function(function_match, exported, anchor)
        raise SurfaceSyntaxError("ExpectedDeclaration", raw, _span(self.path, line))

    def _flow_policies(
        self, source: str, line: _Line, base_column: int
    ) -> tuple[str, tuple[SurfacePolicy, ...]]:
        matches = list(re.finditer(
            r"\s+(timeout\s+\S+|retry\s+\d+\s+on\s+[A-Za-z_]\w*(?:\[[^\]]+\])?|"
            r"idempotent\s+by\s+.+|compensate\s+.+)$",
            source,
        ))
        if not matches:
            return source.strip(), ()
        # Policy clauses are deliberately parsed from the first recognized suffix;
        # expressions may contain spaces and are retained verbatim.
        starts = [m.start() for m in re.finditer(
            r"\s+(?=(?:timeout|retry|idempotent|compensate)\b)", source
        )]
        cut = min(starts) if starts else len(source)
        value = source[:cut].rstrip()
        suffix = source[cut:].strip()
        policies: list[SurfacePolicy] = []
        clause_starts = list(re.finditer(
            r"(?<!\w)(?:timeout|retry|idempotent|compensate)\b", suffix
        ))
        for index, marker in enumerate(clause_starts):
            end = clause_starts[index + 1].start() if index + 1 < len(clause_starts) else len(suffix)
            clause = suffix[marker.start():end].strip()
            if clause.startswith("timeout "):
                policies.append(SurfacePolicy(_span(self.path, line), "timeout", clause[8:].strip()))
            elif clause.startswith("retry "):
                retry = re.fullmatch(r"retry\s+(\d+)\s+on\s+(.+)", clause)
                if retry is None:
                    raise SurfaceSyntaxError("InvalidRetryPolicy", clause, _span(self.path, line))
                policies.append(SurfacePolicy(
                    _span(self.path, line), "retry", retry.group(1), _type_name(retry.group(2))
                ))
            elif clause.startswith("idempotent by "):
                expression = clause[len("idempotent by "):].strip()
                policies.append(SurfacePolicy(
                    _span(self.path, line), "idempotent", expression,
                    expression= _parse_expression(
                        expression, self.path, line,
                        base_column=base_column + source.find(expression),
                    )
                ))
            elif clause.startswith("compensate "):
                policies.append(SurfacePolicy(
                    _span(self.path, line), "compensate", clause[len("compensate "):].strip()
                ))
        return value, tuple(policies)
    def _flow_step(self, line: _Line) -> SurfaceFlowStep:
        match = re.fullmatch(
            r"(?:(let|var)\s+)?([A-Za-z_]\w*)(?:\s*:\s*([^=]+))?\s*=\s*(.+)",
            line.text,
        )
        if match is None:
            raise SurfaceSyntaxError("ExpectedFlowStep", line.text, _span(self.path, line))
        kind, name, type_name, raw_value = match.groups()
        value, policies = self._flow_policies(raw_value, line, match.start(4))
        return SurfaceFlowStep(
            _span(self.path, line), name,
            _parse_expression(value, self.path, line, base_column=match.start(4)),
            _type_name(type_name) if type_name else None,
            policies,
        )
    def _flow(
        self,
        match: re.Match[str],
        exported: bool,
        anchor: SyntaxNode,
    ) -> SurfaceFlow:
        start = self.lines[self.index]
        durable, name, raw_parameters, raw_return = match.groups()
        del raw_parameters
        parameters = self._cst_function_parameters(anchor, start)
        return_type = self._cst_function_return_type(
            anchor,
            start,
            expected=True,
        )
        if return_type is None:
            raise SurfaceSyntaxError(
                "CSTTypeMismatch",
                "flow return type is missing from the CST",
                _span(self.path, start),
            )
        del raw_return
        self.index += 1
        body: list[SurfaceStatement] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if _trivia(line):
                self.index += 1
                continue
            if line.indent <= start.indent:
                break
            if line.indent != start.indent + 4:
                raise SurfaceSyntaxError("InvalidIndentation", "flow body expected", _span(self.path, line))
            if line.text == "parallel:":
                parallel_start = line
                self.index += 1
                branches: list[SurfaceFlowStep] = []
                while self.index < len(self.lines):
                    branch = self.lines[self.index]
                    if _trivia(branch):
                        self.index += 1
                        continue
                    if branch.indent <= parallel_start.indent:
                        break
                    if branch.indent != parallel_start.indent + 4:
                        raise SurfaceSyntaxError("InvalidIndentation", "parallel branch expected", _span(self.path, branch))
                    branches.append(self._flow_step(branch))
                    self.index += 1
                if not branches:
                    raise SurfaceSyntaxError("EmptyParallel", name, _span(self.path, parallel_start))
                body.append(SurfaceParallel(
                    _span(self.path, parallel_start, end_line=self.lines[self.index - 1]),
                    tuple(branches),
                ))
                continue
            if re.match(r"(?:(?:let|var)\s+)?[A-Za-z_]\w*(?:\s*:\s*[^=]+)?\s*=", line.text):
                body.append(self._flow_step(line))
                self.index += 1
            else:
                body.append(self._statement())
        end = self.lines[self.index - 1] if self.index else start
        return SurfaceFlow(
            _span(self.path, start, end_line=end), name, parameters,
            return_type, tuple(body), bool(durable), exported,
        )

    def _machine(
        self,
        match: re.Match[str],
        exported: bool,
        anchor: SyntaxNode,
    ) -> SurfaceMachine:
        start = self.lines[self.index]
        name, raw_parameters = match.groups()
        del raw_parameters
        parameters = self._cst_function_parameters(anchor, start)
        self.index += 1
        states: list[SurfaceState] = []
        transitions: list[SurfaceTransition] = []
        initial: str | None = None
        invariant: SurfaceExpression | None = None
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if _trivia(line):
                self.index += 1
                continue
            if line.indent <= start.indent:
                break
            if line.indent != start.indent + 4:
                raise SurfaceSyntaxError("InvalidIndentation", "machine member expected", _span(self.path, line))
            state_match = re.fullmatch(r"state\s+([A-Z]\w*)(?:\((.*)\))?", line.text)
            if state_match:
                state_anchor = self._statement_anchor(line, kind="state")
                fields = (
                    self._cst_function_parameters(state_anchor, line)
                    if state_match.group(2) is not None
                    else ()
                )
                if any(item.type_name is None for item in fields):
                    raise SurfaceSyntaxError("StateFieldTypeRequired", state_match.group(1), _span(self.path, line))
                states.append(SurfaceState(
                    _span(self.path, line), state_match.group(1),
                    tuple(SurfaceField(item.span, item.name, item.type_name or "Inferred") for item in fields),
                ))
                self.index += 1
                continue
            initial_match = re.fullmatch(r"initial\s+([A-Z]\w*)", line.text)
            if initial_match:
                initial = initial_match.group(1)
                self.index += 1
                continue
            invariant_match = re.fullmatch(r"invariant\s+(.+)", line.text)
            if invariant_match:
                invariant = _parse_expression(
                    invariant_match.group(1), self.path, line,
                    base_column=invariant_match.start(1),
                )
                self.index += 1
                continue
            transition_match = re.fullmatch(
                r"transition\s+([a-z_]\w*)\s+from\s+(.+?)\s*->\s*([A-Z]\w*)\s*:",
                line.text,
            )
            if transition_match is None:
                raise SurfaceSyntaxError("ExpectedMachineMember", line.text, _span(self.path, line))
            transition_start = line
            sources = tuple(item.strip() for item in transition_match.group(2).split("|"))
            if not sources or any(re.fullmatch(r"[A-Z]\w*", item) is None for item in sources):
                raise SurfaceSyntaxError("InvalidTransitionSource", line.text, _span(self.path, line))
            target = transition_match.group(3)
            self.index += 1
            body = self._block(line.indent + 4)
            transitions.append(SurfaceTransition(
                _span(self.path, transition_start, end_line=self.lines[self.index - 1]),
                transition_match.group(1), sources, target, body,
            ))
        if not states:
            raise SurfaceSyntaxError("EmptyMachine", name, _span(self.path, start))
        return SurfaceMachine(
            _span(self.path, start, end_line=self.lines[self.index - 1]),
            name, parameters, tuple(states), initial, invariant, tuple(transitions), exported,
        )

    def _interface(self, name: str, exported: bool) -> SurfaceInterface:
        start = self.lines[self.index]
        self.index += 1
        methods: list[SurfaceInterfaceMethod] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if _trivia(line):
                self.index += 1
                continue
            if line.indent <= start.indent:
                break
            if line.indent != start.indent + 4:
                raise SurfaceSyntaxError(
                    "InvalidIndentation",
                    "interface method expected",
                    _span(self.path, line),
                )
            match = re.fullmatch(
                r"(?:fn\s+)?([A-Za-z_]\w*)\((.*)\)\s*->\s*(.+)",
                line.text,
            )
            if match is None:
                raise SurfaceSyntaxError(
                    "ExpectedInterfaceMethod",
                    line.text,
                    _span(self.path, line),
                )
            method_anchor = self._statement_anchor(
                line,
                kind="expression_statement",
            )
            parameters = self._cst_function_parameters(method_anchor, line)
            if any(item.type_name is None for item in parameters):
                raise SurfaceSyntaxError(
                    "InterfaceBoundaryAnnotationRequired",
                    match.group(1),
                    _span(self.path, line),
                )
            return_type = self._cst_function_return_type(
                method_anchor,
                line,
                expected=True,
            )
            if return_type is None:
                raise SurfaceSyntaxError(
                    "CSTTypeMismatch",
                    "interface return type is missing from the CST",
                    _span(self.path, line),
                )
            methods.append(
                SurfaceInterfaceMethod(
                    _span(self.path, line),
                    match.group(1),
                    parameters,
                    return_type,
                )
            )
            self.index += 1
        if not methods:
            raise SurfaceSyntaxError(
                "EmptyInterface",
                name,
                _span(self.path, start),
            )
        if len({item.name for item in methods}) != len(methods):
            raise SurfaceSyntaxError(
                "DuplicateInterfaceMethod",
                name,
                _span(self.path, start),
            )
        return SurfaceInterface(
            _span(self.path, start, end_line=self.lines[self.index - 1]),
            name,
            tuple(methods),
            exported,
        )

    def _implementation(
        self,
        interface_name: str,
        type_name: str,
    ) -> SurfaceImplementation:
        start = self.lines[self.index]
        self.index += 1
        methods: list[SurfaceFunction] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if _trivia(line):
                self.index += 1
                continue
            if line.indent <= start.indent:
                break
            if line.indent != start.indent + 4:
                raise SurfaceSyntaxError(
                    "InvalidIndentation",
                    "implementation method expected",
                    _span(self.path, line),
                )
            match = _FUNCTION_HEADER.fullmatch(line.text)
            if match is None:
                raise SurfaceSyntaxError(
                    "ExpectedImplementationMethod",
                    line.text,
                    _span(self.path, line),
                )
            if match.group(1) not in {None, "fn"} or match.group(3) is not None:
                raise SurfaceSyntaxError(
                    "InvalidImplementationMethod",
                    line.text,
                    _span(self.path, line),
                )
            methods.append(self._function(match, False))
        if not methods:
            raise SurfaceSyntaxError(
                "EmptyImplementation",
                f"{interface_name} for {type_name}",
                _span(self.path, start),
            )
        if len({item.name for item in methods}) != len(methods):
            raise SurfaceSyntaxError(
                "DuplicateImplementationMethod",
                interface_name,
                _span(self.path, start),
            )
        return SurfaceImplementation(
            _span(self.path, start, end_line=self.lines[self.index - 1]),
            interface_name,
            type_name,
            tuple(methods),
        )


    def _record(self, name: str, exported: bool) -> SurfaceRecord:
        start = self.lines[self.index]
        self.index += 1
        fields = []
        invariants = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if _trivia(line):
                self.index += 1
                continue
            if line.indent <= start.indent:
                break
            if line.indent != start.indent + 4:
                raise SurfaceSyntaxError(
                    "InvalidIndentation",
                    "record field or invariant expected",
                    _span(self.path, line),
                )
            if match := re.fullmatch(r"invariant\s+(.+)", line.text):
                invariants.append(
                    SurfaceInvariant(
                        _span(self.path, line),
                        _parse_expression(
                            match.group(1),
                            self.path,
                            line,
                            base_column=line.text.index(match.group(1)),
                        ),
                    )
                )
                self.index += 1
                continue
            match = re.fullmatch(r"([A-Za-z_]\w*)\s*:\s*(.+)", line.text)
            if match is None:
                raise SurfaceSyntaxError(
                    "ExpectedRecordFieldOrInvariant",
                    line.text,
                    _span(self.path, line),
                )
            if invariants:
                raise SurfaceSyntaxError(
                    "RecordFieldAfterInvariant",
                    match.group(1),
                    _span(self.path, line),
                )
            anchor = self._statement_anchor(line, kind="field")
            fields.append(
                SurfaceField(
                    _span(self.path, line),
                    match.group(1),
                    self._cst_type_region(anchor, line, expected=True) or "",
                )
            )
            self.index += 1
        if not fields:
            raise SurfaceSyntaxError("EmptyRecord", name, _span(self.path, start))
        return SurfaceRecord(
            _span(self.path, start, end_line=self.lines[self.index - 1]),
            name,
            tuple(fields),
            exported,
            tuple(invariants),
        )

    def _enum(self, name: str, exported: bool) -> SurfaceEnum:
        start = self.lines[self.index]
        self.index += 1
        variants = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if _trivia(line):
                self.index += 1
                continue
            if line.indent <= start.indent:
                break
            match = re.fullmatch(r"([A-Z]\w*)(?:\s*:\s*(.+))?", line.text)
            if line.indent != start.indent + 4 or match is None:
                raise SurfaceSyntaxError("ExpectedEnumVariant", line.text, _span(self.path, line))
            anchor = self._statement_anchor(
                line,
                kind="field" if match.group(2) is not None else "expression_statement",
            )
            variants.append(SurfaceEnumVariant(
                _span(self.path, line),
                match.group(1),
                self._cst_type_region(
                    anchor,
                    line,
                    expected=match.group(2) is not None,
                ),
            ))
            self.index += 1
        return SurfaceEnum(_span(self.path, start, end_line=self.lines[self.index - 1]), name, tuple(variants), exported)

    def _retained_node_text(
        self,
        node: SyntaxNode,
        line: _Line,
        *,
        code: str,
    ) -> str:
        previous_end = node.start
        pieces: list[str] = []
        for token in node.tokens:
            if (
                token.start < node.start
                or token.end > node.end
                or token.start < previous_end
                or self.cst.source[token.start:token.end] != token.text
            ):
                raise SurfaceSyntaxError(
                    code,
                    f"CST {node.kind} tokens have invalid retained offsets",
                    _span(self.path, line),
                )
            pieces.append(token.text)
            previous_end = token.end
        return "".join(pieces)

    def _cst_function_parameters(
        self,
        owner: SyntaxNode,
        line: _Line,
    ) -> tuple[SurfaceParameter, ...]:
        header = next(
            (child for child in owner.children if child.kind == "header"),
            None,
        )
        regions = (
            tuple(child for child in header.children if child.kind == "parameters")
            if header is not None
            else ()
        )
        if len(regions) != 1:
            raise SurfaceSyntaxError(
                "CSTTypeMismatch",
                "function declaration must have one CST parameter region",
                _span(self.path, line),
            )
        parameters: list[SurfaceParameter] = []
        for parameter in regions[0].children:
            if parameter.kind != "parameter" or not parameter.tokens:
                raise SurfaceSyntaxError(
                    "CSTTypeMismatch",
                    "CST parameter region is malformed",
                    _span(self.path, line),
                )
            name = parameter.tokens[0].text
            if re.fullmatch(r"[A-Za-z_]\w*", name) is None:
                raise SurfaceSyntaxError(
                    "InvalidParameter",
                    name,
                    _span(self.path, line),
                )
            types = tuple(
                child for child in parameter.children if child.kind == "type"
            )
            has_annotation = any(
                token.text == ":" for token in parameter.tokens[1:]
            )
            if len(types) > 1 or bool(types) != has_annotation:
                raise SurfaceSyntaxError(
                    "CSTTypeMismatch",
                    f"parameter {name!r} has inconsistent CST type regions",
                    _span(self.path, line),
                )
            type_name = (
                _type_name(
                    self._retained_node_text(
                        types[0],
                        line,
                        code="CSTTypeMismatch",
                    )
                )
                if types
                else None
            )
            first = parameter.tokens[0]
            last = parameter.tokens[-1]
            parameters.append(
                SurfaceParameter(
                    SourceSpan(
                        self.path,
                        first.line + self.line_offset,
                        first.column,
                        last.line + self.line_offset,
                        last.column + len(last.text),
                    ),
                    name,
                    type_name,
                )
            )
        if len({item.name for item in parameters}) != len(parameters):
            raise SurfaceSyntaxError(
                "DuplicateParameter",
                "parameter names must be unique",
                _span(self.path, line),
            )
        return tuple(parameters)

    def _cst_function_return_type(
        self,
        owner: SyntaxNode,
        line: _Line,
        *,
        expected: bool,
    ) -> str | None:
        header = next(
            (child for child in owner.children if child.kind == "header"),
            None,
        )
        regions = (
            tuple(child for child in header.children if child.kind == "type")
            if header is not None
            else ()
        )
        if len(regions) != (1 if expected else 0):
            raise SurfaceSyntaxError(
                "CSTTypeMismatch",
                "CST return type region disagrees with the function boundary",
                _span(self.path, line),
            )
        if not regions:
            return None
        return _type_name(
            self._retained_node_text(
                regions[0],
                line,
                code="CSTTypeMismatch",
            )
        )

    def _cst_type_region(
        self,
        owner: SyntaxNode,
        line: _Line,
        *,
        expected: bool,
        ordinal: int = 0,
    ) -> str | None:
        header = next(
            (child for child in owner.children if child.kind == "header"),
            None,
        )
        regions = (
            tuple(child for child in header.children if child.kind == "type")
            if header is not None
            else ()
        )
        required_count = ordinal + 1 if expected else 0
        if len(regions) != required_count:
            raise SurfaceSyntaxError(
                "CSTTypeMismatch",
                "CST type regions disagree with the semantic boundary",
                _span(self.path, line),
            )
        if not expected:
            return None
        return _type_name(
            self._retained_node_text(
                regions[ordinal],
                line,
                code="CSTTypeMismatch",
            )
        )

    def _cst_function_type_parameters(
        self,
        owner: SyntaxNode,
        line: _Line,
        *,
        expected: bool,
    ) -> tuple[SurfaceTypeParameter, ...]:
        header = next(
            (child for child in owner.children if child.kind == "header"),
            None,
        )
        regions = (
            tuple(
                child
                for child in header.children
                if child.kind == "type_parameters"
            )
            if header is not None
            else ()
        )
        if len(regions) != (1 if expected else 0):
            raise SurfaceSyntaxError(
                "CSTTypeMismatch",
                "CST generic parameter region disagrees with the function boundary",
                _span(self.path, line),
            )
        if not regions:
            return ()
        result: list[SurfaceTypeParameter] = []
        for parameter in regions[0].children:
            if parameter.kind != "type_parameter" or not parameter.tokens:
                raise SurfaceSyntaxError(
                    "CSTTypeMismatch",
                    "CST generic parameter region is malformed",
                    _span(self.path, line),
                )
            text = self._retained_node_text(
                parameter,
                line,
                code="CSTTypeMismatch",
            )
            name, separator, constraints_text = text.partition(":")
            name = name.strip()
            if re.fullmatch(r"[A-Z][A-Za-z0-9_]*", name) is None:
                raise SurfaceSyntaxError(
                    "InvalidTypeParameter",
                    f"generic type parameter must start with an uppercase letter: {name!r}",
                    _span(self.path, line),
                )
            constraints = tuple(
                item.strip()
                for item in constraints_text.split("+")
                if item.strip()
            ) if separator else ()
            if separator and (
                not constraints
                or any(
                    re.fullmatch(r"[A-Z][A-Za-z0-9_]*", item) is None
                    for item in constraints
                )
            ):
                raise SurfaceSyntaxError(
                    "InvalidTypeConstraint",
                    text,
                    _span(self.path, line),
                )
            first = parameter.tokens[0]
            last = parameter.tokens[-1]
            result.append(
                SurfaceTypeParameter(
                    SourceSpan(
                        self.path,
                        first.line + self.line_offset,
                        first.column,
                        last.line + self.line_offset,
                        last.column + len(last.text),
                    ),
                    name,
                    constraints,
                )
            )
        if len({item.name for item in result}) != len(result):
            raise SurfaceSyntaxError(
                "DuplicateTypeParameter",
                "generic type parameters must be unique",
                _span(self.path, line),
            )
        return tuple(result)

    def _function(
        self,
        match: re.Match[str],
        exported: bool,
        anchor: SyntaxNode | None = None,
    ) -> SurfaceFunction:
        start = self.lines[self.index]
        kind, name, raw_type_parameters, raw_parameters, raw_return, delimiter, inline = match.groups()
        function_anchor = anchor or self._statement_anchor(
            start,
            kind="expression_statement",
        )
        type_parameters = self._cst_function_type_parameters(
            function_anchor,
            start,
            expected=raw_type_parameters is not None,
        )
        parameters = self._cst_function_parameters(function_anchor, start)
        return_type = self._cst_function_return_type(
            function_anchor,
            start,
            expected=raw_return is not None,
        )
        self.index += 1
        if delimiter == "=" and inline.strip():
            expression = self._parse_cst_expression(function_anchor, start)
            return SurfaceFunction(
                name, parameters, expression, "expression", exported,
                _span(self.path, start), kind, return_type, type_parameters,
            )
        if delimiter == "=":
            self._skip_blank()
            if (
                self.index >= len(self.lines)
                or self.lines[self.index].indent != start.indent + 4
            ):
                raise SurfaceSyntaxError(
                    "ExpectedExpressionBody",
                    name,
                    _span(self.path, start),
                )
            expression_line = self.lines[self.index]
            expression = self._parse_cst_expression(
                self._statement_anchor(expression_line),
                expression_line,
            )
            self.index += 1
            return SurfaceFunction(
                name,
                parameters,
                expression,
                "expression",
                exported,
                _span(self.path, start, end_line=expression_line),
                kind,
                return_type,
                type_parameters,
            )
        statements = self._block(start.indent + 4)
        if not statements:
            raise SurfaceSyntaxError(
                "EmptyFunction",
                name,
                _span(self.path, start),
            )
        end_line = self.lines[self.index - 1]
        return SurfaceFunction(
            name,
            parameters,
            statements,
            "block",
            exported,
            _span(self.path, start, end_line=end_line),
            kind,
            return_type,
            type_parameters,
        )

    @staticmethod
    def _expected_statement_kind(line: _Line) -> str:
        first = line.text.split(maxsplit=1)[0].rstrip(":") if line.text else ""
        if first in {
            "let", "return", "if", "elif", "else", "for", "while",
            "match", "case", "require", "ensure", "uses", "break",
            "continue", "yield", "print", "pass", "var",
        }:
            return first
        if re.fullmatch(r"[A-Za-z_]\w*\s*:\s*[^=]+", line.text):
            return "field"
        return "expression_statement"

    def _statement_anchor(self, line: _Line, *, kind: str | None = None) -> SyntaxNode:
        anchor = self.statement_anchors.get(line.number)
        if anchor is None:
            raise SurfaceSyntaxError(
                "CSTStatementMismatch",
                "semantic statement has no CST anchor",
                _span(self.path, line),
            )
        expected = kind or self._expected_statement_kind(line)
        if anchor.kind != expected:
            raise SurfaceSyntaxError(
                "CSTStatementMismatch",
                f"expected CST {expected!r} anchor, found {anchor.kind!r}",
                _span(self.path, line),
            )
        return anchor

    def _parse_statement_expression(
        self,
        statement: SyntaxNode,
        source: str,
        line: _Line,
        *,
        base_column: int = 0,
        ordinal: int = 0,
    ) -> SurfaceExpression:
        # source/base_column remain until the transitional statement parser is
        # removed. The retained CST region is the expression source of truth.
        del source, base_column
        return self._parse_cst_expression(statement, line, ordinal=ordinal)

    def _parse_cst_expression(
        self,
        owner: SyntaxNode,
        line: _Line,
        *,
        ordinal: int = 0,
    ) -> SurfaceExpression:
        header = next(
            (child for child in owner.children if child.kind == "header"),
            None,
        )
        expressions = (
            tuple(child for child in header.children if child.kind == "expression")
            if header is not None
            else ()
        )
        if ordinal >= len(expressions):
            raise SurfaceSyntaxError(
                "CSTExpressionMismatch",
                f"semantic expression {ordinal + 1} has no CST region",
                _span(self.path, line),
            )
        expression = expressions[ordinal]
        if not expression.tokens:
            raise SurfaceSyntaxError(
                "CSTExpressionMismatch",
                "CST expression region is empty",
                _span(self.path, line),
            )
        converted: list[ExpressionToken] = []
        previous_end = expression.start
        for token in expression.tokens:
            if (
                token.start < expression.start
                or token.end > expression.end
                or token.start < previous_end
                or self.cst.source[token.start:token.end] != token.text
            ):
                raise SurfaceSyntaxError(
                    "CSTExpressionMismatch",
                    "CST expression tokens have invalid retained offsets",
                    _span(self.path, line),
                )
            start = token.start - expression.start
            end = token.end - expression.start
            converted.append(
                ExpressionToken(token.kind, token.text, start, end, token.value)
            )
            previous_end = token.end
        retained_source = self.cst.source[expression.start:expression.end]
        converted.append(
            ExpressionToken("eof", "", len(retained_source), len(retained_source))
        )
        first = expression.tokens[0]
        retained_line = _Line(
            first.line + self.line_offset,
            first.column - 1,
            retained_source,
            (" " * (first.column - 1)) + retained_source,
        )
        return _parse_expression(
            retained_source,
            self.path,
            retained_line,
            base_column=0,
            tokens=tuple(converted),
        )

    def _block(self, indent: int) -> tuple[SurfaceStatement, ...]:
        statements = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if not line.text.strip():
                self.index += 1
                continue
            if line.indent < indent:
                break
            if line.indent != indent:
                raise SurfaceSyntaxError(
                    "InvalidIndentation",
                    "statement indentation mismatch",
                    _span(self.path, line),
                )
            if line.text.strip().startswith("#"):
                statements.append(self._statement())
                continue
            statements.append(self._statement(self._statement_anchor(line)))
        return tuple(statements)

    def _statement(self, anchor: SyntaxNode | None = None) -> SurfaceStatement:
        line = self.lines[self.index]
        if line.text.strip().startswith("#"):
            self.index += 1
            return SurfaceComment(
                _span(self.path, line),
                line.text.strip(),
            )
        if anchor is None:
            anchor = self._statement_anchor(line)
        if match := re.fullmatch(r"for\s+([A-Za-z_]\w*)\s+in\s+(.+)\s*:", line.text):
            self.index += 1
            body = self._block(line.indent + 4)
            end = self.lines[self.index - 1]
            return SurfaceFor(
                _span(self.path, line, end_line=end),
                match.group(1),
                self._parse_statement_expression(
                    anchor,
                    match.group(2),
                    line,
                    base_column=match.start(2),
                ),
                body,
            )
        if match := re.fullmatch(r"if\s+(.+)\s*:", line.text):
            self.index += 1
            body = self._block(line.indent + 4)
            otherwise = ()
            if self.index < len(self.lines) and self.lines[self.index].indent == line.indent and self.lines[self.index].text == "else:":
                self._statement_anchor(self.lines[self.index], kind="else")
                self.index += 1
                otherwise = self._block(line.indent + 4)
            end = self.lines[self.index - 1]
            return SurfaceIf(
                _span(self.path, line, end_line=end),
                self._parse_statement_expression(
                    anchor,
                    match.group(1),
                    line,
                    base_column=match.start(1),
                ),
                body,
                otherwise,
            )
        if match := re.fullmatch(r"while\s+(.+)\s*:", line.text):
            self.index += 1
            body = self._block(line.indent + 4)
            return SurfaceWhile(
                _span(self.path, line, end_line=self.lines[self.index - 1]),
                self._parse_statement_expression(
                    anchor,
                    match.group(1),
                    line,
                    base_column=match.start(1),
                ),
                body,
            )
        if match := re.fullmatch(r"match\s+(.+)\s*:", line.text):
            self.index += 1
            cases: list[SurfaceCase] = []
            while self.index < len(self.lines):
                case_line = self.lines[self.index]
                if _trivia(case_line):
                    self.index += 1
                    continue
                if case_line.indent < line.indent + 4:
                    break
                if case_line.indent != line.indent + 4:
                    raise SurfaceSyntaxError(
                        "InvalidIndentation",
                        "case indentation mismatch",
                        _span(self.path, case_line),
                    )
                case_match = re.fullmatch(r"case\s+(.+)\s*:", case_line.text)
                if case_match is None:
                    raise SurfaceSyntaxError(
                        "ExpectedCase",
                        case_line.text,
                        _span(self.path, case_line),
                    )
                self._statement_anchor(case_line, kind="case")
                self.index += 1
                body = self._block(case_line.indent + 4)
                if not body:
                    raise SurfaceSyntaxError(
                        "EmptyCase",
                        case_match.group(1),
                        _span(self.path, case_line),
                    )
                pattern = case_match.group(1).strip()
                pattern_start = case_match.start(1) + (
                    len(case_match.group(1)) - len(case_match.group(1).lstrip())
                )
                pattern_span = SourceSpan(
                    self.path,
                    case_line.number,
                    case_line.indent + pattern_start + 1,
                    case_line.number,
                    case_line.indent + pattern_start + len(pattern) + 1,
                )
                cases.append(
                    SurfaceCase(
                        _span(
                            self.path,
                            case_line,
                            end_line=self.lines[self.index - 1],
                        ),
                        pattern,
                        body,
                        pattern_span,
                    )
                )
            if not cases:
                raise SurfaceSyntaxError(
                    "EmptyMatch",
                    match.group(1),
                    _span(self.path, line),
                )
            return SurfaceMatch(
                _span(
                    self.path,
                    line,
                    end_line=self.lines[self.index - 1],
                ),
                self._parse_statement_expression(
                    anchor,
                    match.group(1),
                    line,
                    base_column=match.start(1),
                ),
                tuple(cases),
            )
        if match := re.fullmatch(r"uses\s+(.+)", line.text):
            effects = tuple(
                sorted(item.strip() for item in match.group(1).split(","))
            )
            if not effects or any(
                re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", effect)
                is None
                for effect in effects
            ):
                raise SurfaceSyntaxError(
                    "InvalidUses",
                    line.text,
                    _span(self.path, line),
                )
            self.index += 1
            return SurfaceUses(_span(self.path, line), effects)
        if match := re.fullmatch(r"print\s+(.+)", line.text):
            self.index += 1
            return SurfacePrint(
                _span(self.path, line),
                self._parse_statement_expression(
                    anchor,
                    match.group(1),
                    line,
                    base_column=match.start(1),
                ),
            )
        if line.text == "continue":
            self.index += 1
            return SurfaceContinue(_span(self.path, line))
        if line.text == "break":
            self.index += 1
            return SurfaceBreak(_span(self.path, line))
        if line.text == "pass":
            self.index += 1
            return SurfacePass(_span(self.path, line))
        if match := re.fullmatch(r"require\s+(.+)", line.text):
            self.index += 1
            return SurfaceRequire(
                _span(self.path, line),
                self._parse_statement_expression(
                    anchor,
                    match.group(1),
                    line,
                    base_column=match.start(1),
                ),
            )
        if match := re.fullmatch(r"ensure\s+(.+)", line.text):
            self.index += 1
            return SurfaceEnsure(
                _span(self.path, line),
                self._parse_statement_expression(
                    anchor,
                    match.group(1),
                    line,
                    base_column=match.start(1),
                ),
            )
        if match := re.fullmatch(r"return(?:\s+(.+))?", line.text):
            self.index += 1
            return SurfaceReturn(
                _span(self.path, line),
                self._parse_statement_expression(
                    anchor,
                    match.group(1),
                    line,
                    base_column=match.start(1),
                )
                if match.group(1)
                else None,
            )
        if match := re.fullmatch(
            r"([A-Za-z_]\w*)\s*:\s*([^=]+)",
            line.text,
        ):
            self.index += 1
            return SurfaceAnnotation(
                _span(self.path, line),
                match.group(1),
                self._cst_type_region(anchor, line, expected=True) or "",
            )
        if match := re.fullmatch(
            r"(?:(let|var)\s+)?([A-Za-z_]\w*)"
            r"(?:\s*:\s*([^=]+))?\s*"
            r"(\+=|-=|\*=|/=|(?<![=!<>])=(?![=>]))\s*(.+)",
            line.text,
        ):
            self.index += 1
            kind, name, type_name, operator, value = match.groups()
            expression = self._parse_statement_expression(
                anchor,
                value,
                line,
                base_column=match.start(5),
            )
            if operator == "=":
                return SurfaceBinding(
                    _span(self.path, line),
                    name,
                    expression,
                    self._cst_type_region(
                        anchor,
                        line,
                        expected=type_name is not None,
                    ),
                    kind,
                )
            return SurfaceAssignment(
                _span(self.path, line),
                SurfaceName(name, _span(self.path, line)),
                expression,
                operator,
            )
        if match := re.fullmatch(
            r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*"
            r"|\[[^\]]+\])+)\s*"
            r"(\+=|-=|\*=|/=|(?<![=!<>])=(?!=))\s*(.+)",
            line.text,
        ):
            target, operator, value = match.groups()
            self.index += 1
            return SurfaceAssignment(
                _span(self.path, line),
                self._parse_statement_expression(
                    anchor,
                    target,
                    line,
                    base_column=match.start(1),
                    ordinal=0,
                ),
                self._parse_statement_expression(
                    anchor,
                    value,
                    line,
                    base_column=match.start(3),
                    ordinal=1,
                ),
                operator,
            )
        self.index += 1
        return SurfaceExpressionStatement(
            _span(self.path, line),
            self._parse_statement_expression(anchor, line.text, line),
        )


def parse_surface(
    source: str,
    *,
    path: str = "main.mlo",
    line_offset: int = 0,
) -> SurfaceProgram:
    if not source.strip():
        line = 1 + line_offset
        raise SurfaceSyntaxError(
            "EmptySource",
            "source is empty",
            SourceSpan(path, line, 1, line, 1),
        )
    cst = parse_file_cst(source, path=path)
    if cst.diagnostics:
        diagnostic = cst.diagnostics[0]
        raise SurfaceSyntaxError(
            diagnostic.code,
            diagnostic.message,
            SourceSpan(
                path,
                diagnostic.line + line_offset,
                diagnostic.column,
                diagnostic.line + line_offset,
                diagnostic.column + max(1, diagnostic.end - diagnostic.start),
            ),
        )
    return _Parser(source, path, cst, line_offset=line_offset).parse()


__all__ = ["SurfaceSyntaxError", "parse_surface"]
