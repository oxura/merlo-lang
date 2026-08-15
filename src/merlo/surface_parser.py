from __future__ import annotations

import re
from dataclasses import dataclass

from merlo.frontend.lexer import ExpressionLexError, ExpressionToken, lex_expression
from merlo.frontend.file_syntax import parse_file_cst
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
    SurfaceComment,
    SurfaceCase,
    SurfaceDeclaration,
    SurfaceEnum,
    SurfaceEnumVariant,
    SurfaceContinue,
    SurfaceExpression,
    SurfaceExpressionStatement,
    SurfaceField,
    SurfaceFor,
    SurfaceFunction,
    SurfaceIf,
    SurfaceImplementation,
    SurfaceInterface,
    SurfaceInterfaceMethod,
    SurfaceImplicitReceiver,
    SurfaceIndex,
    SurfaceList,
    SurfaceLambda,
    SurfaceLiteral,
    SurfaceMatch,
    SurfaceMember,
    SurfaceName,
    SurfaceParameter,
    SurfacePass,
    SurfacePrint,
    SurfaceRecord,
    SurfaceProgram,
    SurfaceReturn,
    SurfaceStatement,
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
    ) -> None:
        self.source = source
        self.path = path
        self.line = line
        self.base_column = base_column
        try:
            self.tokens = lex_expression(source)
        except ExpressionLexError as exc:
            self._error("InvalidExpression", exc.message, exc.position)
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
) -> SurfaceExpression:
    expression = _ExpressionParser(
        source,
        path,
        line,
        base_column=base_column,
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

    def delimiter_delta(text: str) -> int:
        depth = 0
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
                depth += 1
            elif character in ")]}":
                depth -= 1
        return depth

    result: list[_Line] = []
    pending: _Line | None = None
    pieces: list[str] = []
    depth = 0
    for line in physical:
        if pending is None:
            pending = line
            pieces = [line.text]
            depth = delimiter_delta(line.text)
        else:
            pieces.append(line.text.strip())
            depth += delimiter_delta(line.text)
        if depth <= 0:
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
            depth = 0
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
        *,
        line_offset: int = 0,
    ) -> None:
        self.source = source
        self.path = path
        self.line_offset = line_offset
        self.lines = _lines(source, path, line_offset=line_offset)
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
        self.index = prelude.body_source_lines[0] - 1 if prelude.body_source_lines else len(self.lines)
        self._skip_blank()
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.indent:
                raise SurfaceSyntaxError("UnexpectedIndent", "top-level declaration expected", _span(self.path, line))
            declarations.append(self._declaration())
            self._skip_blank()
        end = self.lines[-1] if self.lines else _Line(1, 0, "", "")
        return SurfaceProgram(
            SourceSpan(self.path, 1, 1, end.number, len(end.raw) + 1),
            tuple(declarations),
            prelude.module,
            prelude.imports,
            self.source,
        )

    def _declaration(self) -> SurfaceDeclaration:
        line = self.lines[self.index]
        raw = line.text
        exported = bool(re.match(r"export\s+", raw))
        raw = re.sub(r"^export\s+", "", raw)
        interface_match = re.fullmatch(r"interface\s+([A-Z]\w*)\s*:", raw)
        if interface_match:
            return self._interface(interface_match.group(1), exported)
        implementation_match = re.fullmatch(
            r"impl\s+([A-Z]\w*)\s+for\s+(.+)\s*:",
            raw,
        )
        if implementation_match:
            if exported:
                raise SurfaceSyntaxError(
                    "ExportedImplementationForbidden",
                    raw,
                    _span(self.path, line),
                )
            return self._implementation(
                implementation_match.group(1),
                _type_name(implementation_match.group(2)),
            )
        enum_match = re.fullmatch(r"enum\s+([A-Z]\w*)\s*:", raw)
        if enum_match:
            return self._enum(enum_match.group(1), exported)
        record_match = re.fullmatch(r"(?:record\s+)?([A-Z]\w*)\s*:", raw)
        if record_match:
            return self._record(record_match.group(1), exported)
        function_match = _FUNCTION_HEADER.fullmatch(raw)
        if function_match:
            return self._function(function_match, exported)
        raise SurfaceSyntaxError("ExpectedDeclaration", raw, _span(self.path, line))
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
            parameters = self._parameters(
                match.group(2),
                line,
                base_column=match.start(2),
            )
            if any(item.type_name is None for item in parameters):
                raise SurfaceSyntaxError(
                    "InterfaceBoundaryAnnotationRequired",
                    match.group(1),
                    _span(self.path, line),
                )
            methods.append(
                SurfaceInterfaceMethod(
                    _span(self.path, line),
                    match.group(1),
                    parameters,
                    _type_name(match.group(3)),
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
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if _trivia(line):
                self.index += 1
                continue
            if line.indent <= start.indent:
                break
            if line.indent != start.indent + 4:
                raise SurfaceSyntaxError("InvalidIndentation", "record field expected", _span(self.path, line))
            match = re.fullmatch(r"([A-Za-z_]\w*)\s*:\s*(.+)", line.text)
            if match is None:
                raise SurfaceSyntaxError("ExpectedRecordField", line.text, _span(self.path, line))
            fields.append(SurfaceField(_span(self.path, line), match.group(1), _type_name(match.group(2))))
            self.index += 1
        if not fields:
            raise SurfaceSyntaxError("EmptyRecord", name, _span(self.path, start))
        return SurfaceRecord(_span(self.path, start, end_line=self.lines[self.index - 1]), name, tuple(fields), exported)

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
            variants.append(SurfaceEnumVariant(_span(self.path, line), match.group(1), _type_name(match.group(2)) if match.group(2) else None))
            self.index += 1
        return SurfaceEnum(_span(self.path, start, end_line=self.lines[self.index - 1]), name, tuple(variants), exported)

    def _parameters(
        self,
        source: str,
        line: _Line,
        *,
        base_column: int = 0,
    ) -> tuple[SurfaceParameter, ...]:
        if not source.strip():
            return ()
        parameters = []
        cursor = 0
        for part in _split_top_level_commas(source):
            start = source.find(part, cursor)
            cursor = start + len(part)
            left = len(part) - len(part.lstrip())
            right = len(part.rstrip())
            lexical_start = base_column + start + left
            lexical_end = base_column + start + right
            name, separator, type_name = part.strip().partition(":")
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                raise SurfaceSyntaxError("InvalidParameter", part, _span(self.path, line))
            parameter_span = SourceSpan(
                self.path,
                line.number,
                line.indent + lexical_start + 1,
                line.number,
                line.indent + lexical_end + 1,
            )
            parameters.append(
                SurfaceParameter(
                    parameter_span,
                    name,
                    _type_name(type_name) if separator else None,
                )
            )
        if len({item.name for item in parameters}) != len(parameters):
            raise SurfaceSyntaxError("DuplicateParameter", "parameter names must be unique", _span(self.path, line))
        return tuple(parameters)

    def _function(self, match: re.Match[str], exported: bool) -> SurfaceFunction:
        start = self.lines[self.index]
        kind, name, raw_type_parameters, raw_parameters, raw_return, delimiter, inline = match.groups()
        type_parameters = self._type_parameters(
            raw_type_parameters,
            start,
            base_column=match.start(3) if raw_type_parameters is not None else 0,
        )
        parameters = self._parameters(
            raw_parameters,
            start,
            base_column=match.start(4),
        )
        return_type = _type_name(raw_return) if raw_return else None
        self.index += 1
        if delimiter == "=" and inline.strip():
            leading = len(inline) - len(inline.lstrip())
            expression = _parse_expression(
                inline.strip(),
                self.path,
                start,
                base_column=match.start(7) + leading,
            )
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
            expression = _parse_expression(
                expression_line.text,
                self.path,
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

    def _type_parameters(
        self,
        source: str | None,
        line: _Line,
        *,
        base_column: int,
    ) -> tuple[SurfaceTypeParameter, ...]:
        if source is None:
            return ()
        result: list[SurfaceTypeParameter] = []
        cursor = 0
        for raw in _split_top_level_commas(source):
            start = source.find(raw, cursor)
            cursor = start + len(raw)
            text = raw.strip()
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
                or any(re.fullmatch(r"[A-Z][A-Za-z0-9_]*", item) is None for item in constraints)
            ):
                raise SurfaceSyntaxError(
                    "InvalidTypeConstraint",
                    text,
                    _span(self.path, line),
                )
            lexical_start = base_column + start + (len(raw) - len(raw.lstrip()))
            lexical_end = base_column + start + len(raw.rstrip())
            result.append(
                SurfaceTypeParameter(
                    SourceSpan(
                        self.path,
                        line.number,
                        line.indent + lexical_start + 1,
                        line.number,
                        line.indent + lexical_end + 1,
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
            statements.append(self._statement())
        return tuple(statements)

    def _statement(self) -> SurfaceStatement:
        line = self.lines[self.index]
        if line.text.strip().startswith("#"):
            self.index += 1
            return SurfaceComment(
                _span(self.path, line),
                line.text.strip(),
            )
        if match := re.fullmatch(r"for\s+([A-Za-z_]\w*)\s+in\s+(.+)\s*:", line.text):
            self.index += 1
            body = self._block(line.indent + 4)
            end = self.lines[self.index - 1]
            return SurfaceFor(
                _span(self.path, line, end_line=end),
                match.group(1),
                _parse_expression(
                    match.group(2),
                    self.path,
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
                self.index += 1
                otherwise = self._block(line.indent + 4)
            end = self.lines[self.index - 1]
            return SurfaceIf(
                _span(self.path, line, end_line=end),
                _parse_expression(
                    match.group(1),
                    self.path,
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
                _parse_expression(
                    match.group(1),
                    self.path,
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
                _parse_expression(
                    match.group(1),
                    self.path,
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
                _parse_expression(
                    match.group(1),
                    self.path,
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
        if match := re.fullmatch(r"return(?:\s+(.+))?", line.text):
            self.index += 1
            return SurfaceReturn(
                _span(self.path, line),
                _parse_expression(
                    match.group(1),
                    self.path,
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
                _type_name(match.group(2)),
            )
        if match := re.fullmatch(
            r"(?:(let|var)\s+)?([A-Za-z_]\w*)"
            r"(?:\s*:\s*([^=]+))?\s*"
            r"(\+=|-=|\*=|/=|(?<![=!<>])=(?![=>]))\s*(.+)",
            line.text,
        ):
            self.index += 1
            kind, name, type_name, operator, value = match.groups()
            expression = _parse_expression(
                value,
                self.path,
                line,
                base_column=match.start(5),
            )
            if operator == "=":
                return SurfaceBinding(
                    _span(self.path, line),
                    name,
                    expression,
                    _type_name(type_name) if type_name else None,
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
                _parse_expression(
                    target,
                    self.path,
                    line,
                    base_column=match.start(1),
                ),
                _parse_expression(
                    value,
                    self.path,
                    line,
                    base_column=match.start(3),
                ),
                operator,
            )
        self.index += 1
        return SurfaceExpressionStatement(_span(self.path, line), _parse_expression(line.text, self.path, line))


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
    return _Parser(source, path, line_offset=line_offset).parse()


__all__ = ["SurfaceSyntaxError", "parse_surface"]
