from __future__ import annotations

import re
from dataclasses import dataclass

from merlo.frontend.lexer import ExpressionLexError, ExpressionToken, lex_expression
from merlo.frontend.file_syntax import FileCST, FileToken, SyntaxNode, parse_file_cst
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


def _cst_cursor_lines(
    cst: FileCST,
    *,
    line_offset: int = 0,
) -> list[_Line]:
    """Project CST construct headers into a temporary parser cursor.

    This view deliberately does not lex source text, count delimiters, or infer
    indentation. Those decisions already belong to ``parse_file_cst``. The
    cursor only preserves the small line-shaped compatibility surface needed
    while semantic traversal is migrated to CST children.
    """

    physical = cst.source.splitlines()
    entries: list[tuple[int, int, _Line]] = []
    construct_lines: set[int] = set()
    header_ranges: list[tuple[int, int]] = []

    def visit(nodes: tuple[SyntaxNode, ...]) -> None:
        for node in nodes:
            header = next(
                (child for child in node.children if child.kind == "header"),
                None,
            )
            if header is None:
                continue
            significant = tuple(
                token
                for token in header.tokens
                if token.kind not in {
                    "whitespace", "comment", "newline", "indent", "dedent", "eof",
                }
            )
            if not significant:
                continue
            first = significant[0]
            last = significant[-1]
            number = first.line + line_offset
            indent = first.column - 1
            text = cst.source[first.start:last.end]
            raw = physical[first.line - 1] if first.line <= len(physical) else text
            entries.append((first.start, 1, _Line(number, indent, text, raw)))
            construct_lines.add(first.line)
            header_ranges.append((header.start, header.end))
            block = next(
                (child for child in node.children if child.kind == "block"),
                None,
            )
            if block is not None:
                visit(block.children)

    # The lossless root owns source coverage. Semantic ``declarations`` are a
    # checked projection and may be forged independently in adversarial tests;
    # keeping the cursor rooted here ensures such disagreement fails closed.
    visit(cst.root.children)
    for token in cst.tokens:
        if token.kind != "comment" or token.line in construct_lines:
            continue
        if any(start <= token.start < end for start, end in header_ranges):
            continue
        raw = physical[token.line - 1] if token.line <= len(physical) else token.text
        entries.append((
            token.start,
            0,
            _Line(
                token.line + line_offset,
                token.column - 1,
                token.text,
                raw,
            ),
        ))
    entries.sort(key=lambda item: (item[0], item[1]))
    return [line for _start, _kind, line in entries]


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
        self.lines = _cst_cursor_lines(cst, line_offset=line_offset)
        self.statement_anchors: dict[int, SyntaxNode] = {}
        for declaration in cst.declarations:
            for node in declaration.walk()[1:]:
                if node.kind in {
                    "header", "block", "parameters", "parameter", "type",
                    "type_parameters", "type_parameter", "expression", "policy",
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
            diagnostic_line = exc.line + self.line_offset
            line = next(
                (item for item in self.lines if item.number == diagnostic_line),
                _Line(
                diagnostic_line,
                0,
                "",
                "",
                ),
            )
            raise SurfaceSyntaxError(exc.code, exc.message, _span(self.path, line)) from exc

        declarations: list[SurfaceDeclaration] = []
        anchors = tuple(
            node
            for node in self.cst.declarations
            if node.kind not in {"module", "use", "import"}
        )
        anchor_index = 0
        if prelude.body_source_lines:
            first_body_line = prelude.body_source_lines[0] + self.line_offset
            self.index = next(
                (
                    index
                    for index, line in enumerate(self.lines)
                    if line.number >= first_body_line
                ),
                len(self.lines),
            )
        else:
            self.index = len(self.lines)
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
        retained_tokens = list(self._retained_header_tokens(anchor))
        exported = bool(
            retained_tokens and retained_tokens[0].text == "export"
        )
        if exported:
            retained_tokens.pop(0)
        retained_texts = tuple(token.text for token in retained_tokens)
        raw = re.sub(r"^export\s+", "", raw)
        kind = anchor.kind
        if kind == "flow":
            return self._flow(exported, anchor)
        elif kind == "machine":
            return self._machine(exported, anchor)
        elif kind == "interface":
            name = self._nominal_declaration_name(
                retained_tokens,
                line,
                keyword="interface",
            )
            return self._interface(name, exported)
        elif kind == "impl":
            if (
                len(retained_tokens) < 5
                or retained_texts[0] != "impl"
                or retained_tokens[1].kind != "identifier"
                or retained_texts[2] != "for"
                or retained_texts[-1] != ":"
            ):
                raise SurfaceSyntaxError(
                    "CSTDeclarationMismatch",
                    "invalid retained implementation header",
                    _span(self.path, line),
                )
            if exported:
                raise SurfaceSyntaxError("ExportedImplementationForbidden", raw, _span(self.path, line))
            return self._implementation(retained_texts[1], anchor)
        elif kind == "enum":
            name = self._nominal_declaration_name(
                retained_tokens,
                line,
                keyword="enum",
            )
            return self._enum(name, exported)
        elif kind == "record":
            name = self._nominal_declaration_name(
                retained_tokens,
                line,
                keyword="record",
            )
            return self._record(name, exported)
        elif kind in {"fn", "task"}:
            return self._function(exported, anchor)
        elif kind == "statement":
            if (
                len(retained_tokens) == 2
                and retained_tokens[0].kind == "identifier"
                and retained_tokens[0].text[:1].isupper()
                and retained_texts[1] == ":"
            ):
                return self._record(retained_texts[0], exported)
            if any(token.text == "(" for token in retained_tokens):
                return self._function(exported, anchor)
        raise SurfaceSyntaxError("ExpectedDeclaration", raw, _span(self.path, line))

    def _nominal_declaration_name(
        self,
        tokens: list[FileToken],
        line: _Line,
        *,
        keyword: str,
    ) -> str:
        if (
            len(tokens) != 3
            or tokens[0].text != keyword
            or tokens[1].kind != "identifier"
            or not tokens[1].text[:1].isupper()
            or tokens[2].text != ":"
        ):
            raise SurfaceSyntaxError(
                "CSTDeclarationMismatch",
                f"invalid retained {keyword} declaration header",
                _span(self.path, line),
            )
        return tokens[1].text

    def _flow_policies(
        self,
        line: _Line,
        anchor: SyntaxNode,
    ) -> tuple[SurfacePolicy, ...]:
        header = next(
            (child for child in anchor.children if child.kind == "header"),
            None,
        )
        regions = tuple(
            child for child in (header.children if header is not None else ())
            if child.kind == "policy"
        )
        policies: list[SurfacePolicy] = []
        expression_ordinal = 1
        for region in regions:
            tokens = tuple(
                token for token in region.tokens
                if token.kind not in {"whitespace", "comment", "newline", "eof"}
            )
            if len(tokens) < 2:
                raise SurfaceSyntaxError(
                    "CSTStatementMismatch",
                    "retained flow policy is incomplete",
                    _span(self.path, line),
                )
            kind = tokens[0].text
            if kind == "timeout":
                policies.append(SurfacePolicy(
                    _span(self.path, line),
                    "timeout",
                    "".join(token.text for token in tokens[1:]),
                ))
            elif kind == "retry":
                if len(tokens) < 4 or not tokens[1].text.isdigit() or tokens[2].text != "on":
                    raise SurfaceSyntaxError(
                        "InvalidRetryPolicy",
                        self._retained_node_text(region, line, code="CSTStatementMismatch"),
                        _span(self.path, line),
                    )
                policies.append(SurfacePolicy(
                    _span(self.path, line),
                    "retry",
                    tokens[1].text,
                    _type_name("".join(token.text for token in tokens[3:])),
                ))
            elif kind == "idempotent":
                if len(tokens) < 3 or tokens[1].text != "by":
                    raise SurfaceSyntaxError(
                        "CSTStatementMismatch",
                        "invalid retained idempotency policy",
                        _span(self.path, line),
                    )
                expression_region = self._cst_expression_region(
                    anchor,
                    line,
                    ordinal=expression_ordinal,
                )
                policies.append(SurfacePolicy(
                    _span(self.path, line),
                    "idempotent",
                    self._retained_node_text(
                        expression_region,
                        line,
                        code="CSTExpressionMismatch",
                    ),
                    expression=self._parse_cst_expression(
                        anchor,
                        line,
                        ordinal=expression_ordinal,
                    ),
                ))
                expression_ordinal += 1
            elif kind == "compensate":
                policies.append(SurfacePolicy(
                    _span(self.path, line),
                    "compensate",
                    "".join(token.text for token in tokens[1:]),
                ))
            else:
                raise SurfaceSyntaxError(
                    "CSTStatementMismatch",
                    f"unknown retained flow policy {kind!r}",
                    _span(self.path, line),
                )
        if self._cst_expression_count(anchor) != 1 + sum(
            policy.kind == "idempotent" for policy in policies
        ):
            raise SurfaceSyntaxError(
                "CSTExpressionMismatch",
                "flow policy expressions disagree with retained policy regions",
                _span(self.path, line),
            )
        return tuple(policies)
    def _flow_step(
        self,
        line: _Line,
        anchor: SyntaxNode,
    ) -> SurfaceFlowStep:
        assignment = self._cst_assignment_header(anchor, line)
        if assignment is None:
            raise SurfaceSyntaxError("ExpectedFlowStep", line.text, _span(self.path, line))
        _kind, name, operator, complex_target, has_type = assignment
        if name is None or complex_target or operator != "=":
            raise SurfaceSyntaxError("ExpectedFlowStep", line.text, _span(self.path, line))
        policies = self._flow_policies(line, anchor)
        return SurfaceFlowStep(
            _span(self.path, line), name,
            self._parse_cst_expression(anchor, line),
            self._cst_type_region(
                anchor,
                line,
                expected=has_type,
            ),
            policies,
        )

    def _cst_named_parameter_header(
        self,
        anchor: SyntaxNode,
        line: _Line,
        *,
        keyword: str,
        allow_durable: bool = False,
        require_return: bool = False,
    ) -> tuple[str, bool]:
        tokens = list(self._retained_header_tokens(anchor))
        if tokens and tokens[0].text == "export":
            tokens.pop(0)
        durable = False
        if allow_durable and tokens and tokens[0].text == "durable":
            durable = True
            tokens.pop(0)
        if (
            len(tokens) < 4
            or tokens[0].text != keyword
            or tokens[1].kind != "identifier"
            or tokens[2].text != "("
            or tokens[-1].text != ":"
        ):
            raise SurfaceSyntaxError(
                "CSTDeclarationMismatch",
                f"invalid retained {keyword} header",
                _span(self.path, line),
            )
        has_arrow = any(
            tokens[index].text == "-" and tokens[index + 1].text == ">"
            for index in range(len(tokens) - 1)
        )
        if has_arrow != require_return:
            raise SurfaceSyntaxError(
                "CSTTypeMismatch",
                f"{keyword} return boundary disagrees with retained tokens",
                _span(self.path, line),
            )
        return tokens[1].text, durable

    def _flow(
        self,
        exported: bool,
        anchor: SyntaxNode,
    ) -> SurfaceFlow:
        start = self.lines[self.index]
        name, durable = self._cst_named_parameter_header(
            anchor,
            start,
            keyword="flow",
            allow_durable=True,
            require_return=True,
        )
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
            member_anchor = self._statement_anchor(line)
            if member_anchor.kind == "parallel":
                parallel_tokens = self._retained_header_tokens(member_anchor)
                if tuple(token.text for token in parallel_tokens) != ("parallel", ":"):
                    raise SurfaceSyntaxError(
                        "CSTStatementMismatch",
                        "invalid retained parallel header",
                        _span(self.path, line),
                    )
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
                    branches.append(
                        self._flow_step(
                            branch,
                            self._statement_anchor(branch),
                        )
                    )
                    self.index += 1
                if not branches:
                    raise SurfaceSyntaxError("EmptyParallel", name, _span(self.path, parallel_start))
                body.append(SurfaceParallel(
                    _span(self.path, parallel_start, end_line=self.lines[self.index - 1]),
                    tuple(branches),
                ))
                continue
            assignment = self._cst_assignment_header(member_anchor, line)
            if assignment is not None and assignment[1] is not None and assignment[2] == "=":
                body.append(
                    self._flow_step(
                        line,
                        member_anchor,
                    )
                )
                self.index += 1
            else:
                body.append(self._statement(member_anchor))
        end = self.lines[self.index - 1] if self.index else start
        return SurfaceFlow(
            _span(self.path, start, end_line=end), name, parameters,
            return_type, tuple(body), durable, exported,
        )

    def _machine(
        self,
        exported: bool,
        anchor: SyntaxNode,
    ) -> SurfaceMachine:
        start = self.lines[self.index]
        name, _durable = self._cst_named_parameter_header(
            anchor,
            start,
            keyword="machine",
        )
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
            member_anchor = self._statement_anchor(line)
            tokens = self._retained_header_tokens(member_anchor)
            texts = tuple(token.text for token in tokens)
            if member_anchor.kind == "state":
                if (
                    len(tokens) < 2
                    or tokens[0].text != "state"
                    or tokens[1].kind != "identifier"
                    or not tokens[1].text[:1].isupper()
                    or (len(tokens) > 2 and (tokens[2].text != "(" or tokens[-1].text != ")"))
                ):
                    raise SurfaceSyntaxError(
                        "CSTStatementMismatch",
                        "invalid retained machine state",
                        _span(self.path, line),
                    )
                state_name = tokens[1].text
                has_fields = len(tokens) > 2
                fields = (
                    self._cst_function_parameters(member_anchor, line)
                    if has_fields
                    else ()
                )
                if any(item.type_name is None for item in fields):
                    raise SurfaceSyntaxError("StateFieldTypeRequired", state_name, _span(self.path, line))
                states.append(SurfaceState(
                    _span(self.path, line), state_name,
                    tuple(SurfaceField(item.span, item.name, item.type_name or "Inferred") for item in fields),
                ))
                self.index += 1
                continue
            if texts[:1] == ("initial",):
                if (
                    len(tokens) != 2
                    or tokens[1].kind != "identifier"
                    or not tokens[1].text[:1].isupper()
                ):
                    raise SurfaceSyntaxError(
                        "CSTStatementMismatch",
                        "invalid retained initial state",
                        _span(self.path, line),
                    )
                initial = tokens[1].text
                self.index += 1
                continue
            if member_anchor.kind == "invariant":
                self._require_cst_expression_count(member_anchor, line, expected=1)
                invariant = self._parse_cst_expression(
                    member_anchor,
                    line,
                )
                self.index += 1
                continue
            if member_anchor.kind != "transition":
                raise SurfaceSyntaxError("ExpectedMachineMember", line.text, _span(self.path, line))
            if (
                len(tokens) < 7
                or tokens[0].text != "transition"
                or tokens[1].kind != "identifier"
                or not (tokens[1].text[:1].islower() or tokens[1].text.startswith("_"))
                or tokens[2].text != "from"
                or texts[-4:-2] != ("-", ">")
                or texts[-1] != ":"
            ):
                raise SurfaceSyntaxError(
                    "CSTStatementMismatch",
                    "invalid retained transition header",
                    _span(self.path, line),
                )
            transition_start = line
            source_tokens = tokens[3:-4]
            if not source_tokens or any(
                (index % 2 == 0 and (
                    token.kind != "identifier" or not token.text[:1].isupper()
                ))
                or (index % 2 == 1 and token.text != "|")
                for index, token in enumerate(source_tokens)
            ) or len(source_tokens) % 2 == 0:
                raise SurfaceSyntaxError("InvalidTransitionSource", line.text, _span(self.path, line))
            target_token = tokens[-2]
            if target_token.kind != "identifier" or not target_token.text[:1].isupper():
                raise SurfaceSyntaxError("InvalidTransitionTarget", line.text, _span(self.path, line))
            sources = tuple(token.text for token in source_tokens[::2])
            target = target_token.text
            self.index += 1
            body = self._block(line.indent + 4)
            transitions.append(SurfaceTransition(
                _span(self.path, transition_start, end_line=self.lines[self.index - 1]),
                tokens[1].text, sources, target, body,
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
            method_anchor = self._statement_anchor(
                line,
                kind="expression_statement",
            )
            (
                declared_kind,
                method_name,
                has_type_parameters,
                has_return,
                _delimiter,
                _inline,
            ) = self._cst_function_header(
                method_anchor,
                line,
                body_required=False,
            )
            if declared_kind not in {None, "fn"} or has_type_parameters:
                raise SurfaceSyntaxError(
                    "ExpectedInterfaceMethod",
                    line.text,
                    _span(self.path, line),
                )
            if not has_return:
                raise SurfaceSyntaxError(
                    "CSTTypeMismatch",
                    "interface method requires a retained return type",
                    _span(self.path, line),
                )
            parameters = self._cst_function_parameters(method_anchor, line)
            if any(item.type_name is None for item in parameters):
                raise SurfaceSyntaxError(
                    "InterfaceBoundaryAnnotationRequired",
                    method_name,
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
                    method_name,
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
        anchor: SyntaxNode,
    ) -> SurfaceImplementation:
        start = self.lines[self.index]
        type_name = self._cst_type_region(anchor, start, expected=True)
        if type_name is None:
            raise SurfaceSyntaxError(
                "CSTTypeMismatch",
                "implementation target type is missing from the CST",
                _span(self.path, start),
            )
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
            method_anchor = self._statement_anchor(
                line,
                kind="expression_statement",
            )
            methods.append(
                self._function(
                    False,
                    method_anchor,
                    allowed_kinds=frozenset({None, "fn"}),
                    allow_type_parameters=False,
                )
            )
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
            member_anchor = self._statement_anchor(line)
            if member_anchor.kind == "invariant":
                invariants.append(
                    SurfaceInvariant(
                        _span(self.path, line),
                        self._parse_cst_expression(
                            member_anchor,
                            line,
                        ),
                    )
                )
                self.index += 1
                continue
            if member_anchor.kind != "field":
                raise SurfaceSyntaxError(
                    "ExpectedRecordFieldOrInvariant",
                    line.text,
                    _span(self.path, line),
                )
            if invariants:
                field_tokens = self._retained_header_tokens(member_anchor)
                raise SurfaceSyntaxError(
                    "RecordFieldAfterInvariant",
                    field_tokens[0].text if field_tokens else line.text,
                    _span(self.path, line),
                )
            field_tokens = self._retained_header_tokens(member_anchor)
            if not field_tokens or field_tokens[0].kind != "identifier":
                raise SurfaceSyntaxError(
                    "CSTStatementMismatch",
                    "record field has no retained identifier",
                    _span(self.path, line),
                )
            fields.append(
                SurfaceField(
                    _span(self.path, line),
                    field_tokens[0].text,
                    self._cst_type_region(
                        member_anchor,
                        line,
                        expected=True,
                    ) or "",
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
            if line.indent != start.indent + 4:
                raise SurfaceSyntaxError("ExpectedEnumVariant", line.text, _span(self.path, line))
            anchor = self._statement_anchor(
                line,
            )
            tokens = self._retained_header_tokens(anchor)
            has_payload = anchor.kind == "field"
            if (
                anchor.kind not in {"field", "expression_statement"}
                or not tokens
                or tokens[0].kind != "identifier"
                or not tokens[0].text[:1].isupper()
                or (
                    has_payload
                    and (len(tokens) < 3 or tokens[1].text != ":")
                )
                or (not has_payload and len(tokens) != 1)
            ):
                raise SurfaceSyntaxError(
                    "ExpectedEnumVariant",
                    line.text,
                    _span(self.path, line),
                )
            variants.append(SurfaceEnumVariant(
                _span(self.path, line),
                tokens[0].text,
                self._cst_type_region(
                    anchor,
                    line,
                    expected=has_payload,
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

    def _cst_function_header(
        self,
        owner: SyntaxNode,
        line: _Line,
        *,
        body_required: bool = True,
    ) -> tuple[str | None, str, bool, bool, str, bool]:
        tokens = list(self._retained_header_tokens(owner))
        if tokens and tokens[0].text == "export":
            tokens.pop(0)
        declared_kind = None
        if tokens and tokens[0].text in {"fn", "task"}:
            declared_kind = tokens.pop(0).text
        if not tokens or tokens[0].kind != "identifier":
            raise SurfaceSyntaxError(
                "CSTDeclarationMismatch",
                "function header has no retained identifier",
                _span(self.path, line),
            )
        name = tokens[0].text
        open_index = next(
            (
                index
                for index, token in enumerate(tokens)
                if token.text == "("
            ),
            None,
        )
        if open_index is None:
            raise SurfaceSyntaxError(
                "CSTDeclarationMismatch",
                f"function {name!r} has no retained parameter list",
                _span(self.path, line),
            )
        has_type_parameters = open_index > 1 and tokens[1].text == "["
        delimiters: list[str] = []
        pairs = {"(": ")", "[": "]", "{": "}"}
        close_index: int | None = None
        for index in range(open_index, len(tokens)):
            text = tokens[index].text
            if text in pairs:
                delimiters.append(text)
            elif text in pairs.values():
                if not delimiters or pairs[delimiters[-1]] != text:
                    raise SurfaceSyntaxError(
                        "CSTDeclarationMismatch",
                        f"function {name!r} has mismatched delimiters",
                        _span(self.path, line),
                    )
                delimiters.pop()
                if not delimiters:
                    close_index = index
                    break
        if close_index is None:
            raise SurfaceSyntaxError(
                "CSTDeclarationMismatch",
                f"function {name!r} has no closing parameter delimiter",
                _span(self.path, line),
            )
        suffix = tokens[close_index + 1 :]
        has_return = (
            len(suffix) >= 2
            and suffix[0].text == "-"
            and suffix[1].text == ">"
        )
        delimiter_index = next(
            (
                index
                for index, token in enumerate(suffix)
                if token.text in {"=", ":"}
            ),
            None,
        )
        if delimiter_index is None and body_required:
            raise SurfaceSyntaxError(
                "CSTDeclarationMismatch",
                f"function {name!r} has no body delimiter",
                _span(self.path, line),
            )
        delimiter = suffix[delimiter_index].text if delimiter_index is not None else ""
        inline = (
            bool(suffix[delimiter_index + 1 :])
            if delimiter_index is not None
            else False
        )
        if owner.kind in {"fn", "task"} and declared_kind != owner.kind:
            raise SurfaceSyntaxError(
                "CSTDeclarationMismatch",
                f"retained {owner.kind!r} disagrees with function keyword",
                _span(self.path, line),
            )
        return (
            declared_kind,
            name,
            has_type_parameters,
            has_return,
            delimiter,
            inline,
        )

    def _function(
        self,
        exported: bool,
        anchor: SyntaxNode,
        *,
        allowed_kinds: frozenset[str | None] | None = None,
        allow_type_parameters: bool = True,
    ) -> SurfaceFunction:
        start = self.lines[self.index]
        (
            kind,
            name,
            has_type_parameters,
            has_return,
            delimiter,
            inline,
        ) = self._cst_function_header(anchor, start)
        if allowed_kinds is not None and kind not in allowed_kinds:
            raise SurfaceSyntaxError(
                "InvalidImplementationMethod",
                f"function kind {kind!r} is not allowed here",
                _span(self.path, start),
            )
        if has_type_parameters and not allow_type_parameters:
            raise SurfaceSyntaxError(
                "InvalidImplementationMethod",
                "generic implementation methods are not supported",
                _span(self.path, start),
            )
        type_parameters = self._cst_function_type_parameters(
            anchor,
            start,
            expected=has_type_parameters,
        )
        parameters = self._cst_function_parameters(anchor, start)
        return_type = self._cst_function_return_type(
            anchor,
            start,
            expected=has_return,
        )
        self.index += 1
        if delimiter == "=" and inline:
            expression = self._parse_cst_expression(anchor, start)
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
    def _retained_statement_kind(anchor: SyntaxNode) -> str:
        header = next(
            (child for child in anchor.children if child.kind == "header"),
            None,
        )
        tokens = tuple(
            token
            for token in (header.tokens if header is not None else ())
            if token.kind not in {"whitespace", "comment", "newline", "eof"}
        )
        if not tokens:
            return "error"
        if anchor.kind in {
            "field", "parallel", "transition", "state", "initial", "compensate",
        }:
            return anchor.kind
        first = tokens[0].text
        if first in {
            "let", "return", "if", "elif", "else", "for", "while",
            "match", "case", "require", "ensure", "invariant", "uses", "break",
            "continue", "yield", "print", "pass", "var",
        }:
            return first
        texts = tuple(token.text for token in tokens)
        if (
            len(tokens) >= 2
            and tokens[0].kind == "identifier"
            and texts[1] == ":"
            and "=" not in texts
        ):
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
        expected = kind or self._retained_statement_kind(anchor)
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
        line: _Line,
        *,
        ordinal: int = 0,
    ) -> SurfaceExpression:
        return self._parse_cst_expression(statement, line, ordinal=ordinal)

    def _parse_cst_expression(
        self,
        owner: SyntaxNode,
        line: _Line,
        *,
        ordinal: int = 0,
    ) -> SurfaceExpression:
        expression = self._cst_expression_region(
            owner,
            line,
            ordinal=ordinal,
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

    def _cst_expression_region(
        self,
        owner: SyntaxNode,
        line: _Line,
        *,
        ordinal: int = 0,
    ) -> SyntaxNode:
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
        return expression

    def _cst_expression_count(self, owner: SyntaxNode) -> int:
        header = next(
            (child for child in owner.children if child.kind == "header"),
            None,
        )
        return sum(
            child.kind == "expression"
            for child in (header.children if header is not None else ())
        )

    def _require_cst_expression_count(
        self,
        owner: SyntaxNode,
        line: _Line,
        *,
        expected: int,
    ) -> None:
        header = next(
            (child for child in owner.children if child.kind == "header"),
            None,
        )
        actual = len(
            tuple(
                child
                for child in header.children
                if child.kind == "expression"
            )
        ) if header is not None else 0
        if actual != expected:
            raise SurfaceSyntaxError(
                "CSTExpressionMismatch",
                f"expected {expected} CST expression regions, found {actual}",
                _span(self.path, line),
            )

    @staticmethod
    def _retained_header_tokens(owner: SyntaxNode) -> tuple[FileToken, ...]:
        header = next(
            (child for child in owner.children if child.kind == "header"),
            None,
        )
        return tuple(
            token
            for token in (header.tokens if header is not None else ())
            if token.kind not in {"whitespace", "comment", "newline", "eof"}
        )

    def _cst_assignment_header(
        self,
        owner: SyntaxNode,
        line: _Line,
    ) -> tuple[str | None, str | None, str, bool, bool] | None:
        tokens = self._retained_header_tokens(owner)
        delimiters: list[str] = []
        pairs = {"(": ")", "[": "]", "{": "}"}
        equals: int | None = None
        for index, token in enumerate(tokens):
            text = token.text
            if text in pairs:
                delimiters.append(text)
            elif text in pairs.values():
                if delimiters and pairs[delimiters[-1]] == text:
                    delimiters.pop()
            elif not delimiters and text in {"=", "+=", "-=", "*=", "/="}:
                equals = index
                break
        if equals is None:
            if owner.kind in {"let", "var"}:
                raise SurfaceSyntaxError(
                    "CSTStatementMismatch",
                    "retained binding header has no top-level operator",
                    _span(self.path, line),
                )
            return None
        operator = tokens[equals].text
        target_end = equals
        if operator == "=" and equals > 0 and tokens[equals - 1].text in {
            "+", "-", "*", "/",
        }:
            target_end -= 1
            operator = f"{tokens[equals - 1].text}="
        prefix = list(tokens[:target_end])
        binding_kind = None
        if prefix and prefix[0].text in {"let", "var"}:
            binding_kind = prefix.pop(0).text
        delimiters.clear()
        colon: int | None = None
        for index, token in enumerate(prefix):
            if token.text in pairs:
                delimiters.append(token.text)
            elif token.text in pairs.values():
                if delimiters and pairs[delimiters[-1]] == token.text:
                    delimiters.pop()
            elif not delimiters and token.text == ":":
                colon = index
                break
        target = prefix[:colon] if colon is not None else prefix
        simple = len(target) == 1 and target[0].kind == "identifier"
        if binding_kind is not None and not simple:
            raise SurfaceSyntaxError(
                "CSTStatementMismatch",
                f"{binding_kind} requires a retained identifier target",
                _span(self.path, line),
            )
        if not target:
            raise SurfaceSyntaxError(
                "CSTStatementMismatch",
                "retained assignment target is empty",
                _span(self.path, line),
            )
        return (
            binding_kind,
            target[0].text if simple else None,
            operator,
            not simple,
            colon is not None,
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
        if anchor.kind == "for":
            header = next(child for child in anchor.children if child.kind == "header")
            tokens = tuple(
                token
                for token in header.tokens
                if token.kind not in {"whitespace", "comment", "newline", "eof"}
            )
            in_index = next(
                (index for index, token in enumerate(tokens) if token.text == "in"),
                None,
            )
            if (
                in_index != 2
                or len(tokens) < 5
                or tokens[1].kind != "identifier"
                or tokens[-1].text != ":"
            ):
                raise SurfaceSyntaxError(
                    "CSTStatementMismatch",
                    "invalid retained for-statement header",
                    _span(self.path, line),
                )
            self.index += 1
            body = self._block(line.indent + 4)
            end = self.lines[self.index - 1]
            return SurfaceFor(
                _span(self.path, line, end_line=end),
                tokens[1].text,
                self._parse_statement_expression(
                    anchor,
                    line,
                ),
                body,
            )
        if anchor.kind == "if":
            self._require_cst_expression_count(anchor, line, expected=1)
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
                    line,
                ),
                body,
                otherwise,
            )
        if anchor.kind == "while":
            self._require_cst_expression_count(anchor, line, expected=1)
            self.index += 1
            body = self._block(line.indent + 4)
            return SurfaceWhile(
                _span(self.path, line, end_line=self.lines[self.index - 1]),
                self._parse_statement_expression(
                    anchor,
                    line,
                ),
                body,
            )
        if anchor.kind == "match":
            self._require_cst_expression_count(anchor, line, expected=1)
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
                case_anchor = self._statement_anchor(case_line, kind="case")
                if self._cst_expression_count(case_anchor) != 1:
                    raise SurfaceSyntaxError(
                        "ExpectedCase",
                        case_line.text,
                        _span(self.path, case_line),
                    )
                pattern_region = self._cst_expression_region(
                    case_anchor,
                    case_line,
                )
                self.index += 1
                body = self._block(case_line.indent + 4)
                if not body:
                    raise SurfaceSyntaxError(
                        "EmptyCase",
                        self.cst.source[pattern_region.start:pattern_region.end],
                        _span(self.path, case_line),
                    )
                pattern = self.cst.source[
                    pattern_region.start:pattern_region.end
                ].strip()
                first_pattern_token = pattern_region.tokens[0]
                last_pattern_token = pattern_region.tokens[-1]
                pattern_span = SourceSpan(
                    self.path,
                    first_pattern_token.line + self.line_offset,
                    first_pattern_token.column,
                    last_pattern_token.line + self.line_offset,
                    last_pattern_token.column + len(last_pattern_token.text),
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
                subject = self._cst_expression_region(anchor, line)
                raise SurfaceSyntaxError(
                    "EmptyMatch",
                    self.cst.source[subject.start:subject.end],
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
                    line,
                ),
                tuple(cases),
            )
        if anchor.kind == "uses":
            header = next(child for child in anchor.children if child.kind == "header")
            tokens = tuple(
                token
                for token in header.tokens
                if token.kind not in {"whitespace", "comment", "newline", "eof"}
            )
            retained_effects = (
                self.cst.source[tokens[1].start:tokens[-1].end]
                if len(tokens) > 1
                else ""
            )
            effects = tuple(
                sorted(item.strip() for item in retained_effects.split(","))
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
        if anchor.kind == "print":
            self._require_cst_expression_count(anchor, line, expected=1)
            self.index += 1
            return SurfacePrint(
                _span(self.path, line),
                self._parse_statement_expression(
                    anchor,
                    line,
                ),
            )
        if anchor.kind == "continue":
            self.index += 1
            return SurfaceContinue(_span(self.path, line))
        if anchor.kind == "break":
            self.index += 1
            return SurfaceBreak(_span(self.path, line))
        if anchor.kind == "pass":
            self.index += 1
            return SurfacePass(_span(self.path, line))
        if anchor.kind == "require":
            self._require_cst_expression_count(anchor, line, expected=1)
            self.index += 1
            return SurfaceRequire(
                _span(self.path, line),
                self._parse_statement_expression(
                    anchor,
                    line,
                ),
            )
        if anchor.kind == "ensure":
            self._require_cst_expression_count(anchor, line, expected=1)
            self.index += 1
            return SurfaceEnsure(
                _span(self.path, line),
                self._parse_statement_expression(
                    anchor,
                    line,
                ),
            )
        if anchor.kind == "return":
            header = next(child for child in anchor.children if child.kind == "header")
            retained_tokens = tuple(
                token
                for token in header.tokens
                if token.kind not in {"whitespace", "comment", "newline", "eof"}
            )
            expression_count = self._cst_expression_count(anchor)
            expected_count = 1 if len(retained_tokens) > 1 else 0
            if expression_count != expected_count:
                raise SurfaceSyntaxError(
                    "CSTExpressionMismatch",
                    "return retained expression count does not match its "
                    f"tokens: {expression_count} != {expected_count}",
                    _span(self.path, line),
                )
            self.index += 1
            return SurfaceReturn(
                _span(self.path, line),
                self._parse_statement_expression(
                    anchor,
                    line,
                )
                if expression_count == 1
                else None,
            )
        if anchor.kind == "field":
            header = next(child for child in anchor.children if child.kind == "header")
            tokens = tuple(
                token
                for token in header.tokens
                if token.kind not in {"whitespace", "comment", "newline", "eof"}
            )
            if not tokens or tokens[0].kind != "identifier":
                raise SurfaceSyntaxError(
                    "CSTStatementMismatch",
                    "local annotation has no retained identifier",
                    _span(self.path, line),
                )
            self.index += 1
            return SurfaceAnnotation(
                _span(self.path, line),
                tokens[0].text,
                self._cst_type_region(anchor, line, expected=True) or "",
            )
        assignment = self._cst_assignment_header(anchor, line)
        if assignment is not None:
            (
                binding_kind,
                name,
                operator,
                complex_target,
                has_type,
            ) = assignment
            if anchor.kind not in {
                binding_kind
                if binding_kind is not None
                else "expression_statement"
            }:
                raise SurfaceSyntaxError(
                    "CSTStatementMismatch",
                    f"retained {anchor.kind!r} cannot decode assignment",
                    _span(self.path, line),
                )
            self.index += 1
            if complex_target:
                self._require_cst_expression_count(anchor, line, expected=2)
                return SurfaceAssignment(
                    _span(self.path, line),
                    self._parse_statement_expression(
                        anchor,
                        line,
                        ordinal=0,
                    ),
                    self._parse_statement_expression(
                        anchor,
                        line,
                        ordinal=1,
                    ),
                    operator,
                )
            if name is None:
                raise SurfaceSyntaxError(
                    "CSTStatementMismatch",
                    "simple retained assignment has no identifier",
                    _span(self.path, line),
                )
            self._require_cst_expression_count(anchor, line, expected=1)
            expression = self._parse_statement_expression(
                anchor,
                line,
            )
            if operator == "=":
                return SurfaceBinding(
                    _span(self.path, line),
                    name,
                    expression,
                    self._cst_type_region(
                        anchor,
                        line,
                        expected=has_type,
                    ),
                    binding_kind,
                )
            return SurfaceAssignment(
                _span(self.path, line),
                SurfaceName(name, _span(self.path, line)),
                expression,
                operator,
            )
        if anchor.kind != "expression_statement":
            raise SurfaceSyntaxError(
                "CSTStatementMismatch",
                f"unsupported retained statement kind {anchor.kind!r}",
                _span(self.path, line),
            )
        self.index += 1
        return SurfaceExpressionStatement(
            _span(self.path, line),
            self._parse_statement_expression(anchor, line),
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
