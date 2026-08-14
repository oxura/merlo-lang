from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from merlo.surface_ast import (
    SourceSpan,
    SurfaceAssignment,
    SurfaceBreak,
    SurfaceBinary,
    SurfaceBinding,
    SurfaceCall,
    SurfaceCallArgument,
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
    SurfaceImplicitReceiver,
    SurfaceIndex,
    SurfaceList,
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
    SurfaceUnary,
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
    source = source.replace("<", "[").replace(">", "]")
    aliases = {"Int": "Int64", "UInt": "UInt64", "Float": "Float64"}
    return aliases.get(source, source)

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

def _rewrite_postfix_try(source: str) -> str:
    cursor = 0
    quote: str | None = None
    while cursor < len(source):
        character = source[cursor]
        if quote is not None:
            if character == "\\":
                cursor += 2
                continue
            if character == quote:
                quote = None
            cursor += 1
            continue
        if character in {'"', "'"}:
            quote = character
            cursor += 1
            continue
        if character != "?":
            cursor += 1
            continue
        end = cursor
        start = end - 1
        while start >= 0 and source[start].isspace():
            start -= 1
        if start < 0:
            return source
        if source[start] == ")":
            depth = 1
            start -= 1
            while start >= 0 and depth:
                if source[start] == ")":
                    depth += 1
                elif source[start] == "(":
                    depth -= 1
                start -= 1
            if depth:
                return source
            start += 1
            while start > 0 and (
                source[start - 1].isalnum()
                or source[start - 1] in "_."
            ):
                start -= 1
        elif source[start].isalnum() or source[start] == "_":
            while start > 0 and (
                source[start - 1].isalnum()
                or source[start - 1] in "_."
            ):
                start -= 1
        else:
            return source
        expression = source[start:end].rstrip()
        source = (
            f"{source[:start]}__try__({expression})"
            f"{source[end + 1:]}"
        )
        cursor = start + len("__try__(") + len(expression) + 1
    return source


def _rewrite_expression(source: str) -> str:
    source = re.sub(r"\btrue\b", "True", source)
    source = re.sub(r"\bfalse\b", "False", source)
    source = re.sub(r"(?:(?<=\()|(?<=,))\s*([A-Za-z_]\w*)\s*:", r" \1=", source)
    source = re.sub(r"(?:(?<=\()|(?<=,))\s*\.", " __implicit__.", source)
    source = re.sub(r"^\.", "__implicit__.", source)
    source = re.sub(
        r"^if\s+(.+?)\s+then\s+(.+?)\s+else\s+(.+)$",
        r"\2 if \1 else \3",
        source,
    )
    return _rewrite_postfix_try(source)


def _operator(node: ast.AST) -> str:
    table = {
        ast.Add: "+",
        ast.Sub: "-",
        ast.Mult: "*",
        ast.Div: "/",
        ast.FloorDiv: "//",
        ast.Mod: "%",
        ast.BitOr: "|",
        ast.BitAnd: "&",
        ast.BitXor: "^",
        ast.LShift: "<<",
        ast.RShift: ">>",
        ast.And: "and",
        ast.Or: "or",
        ast.Eq: "==",
        ast.NotEq: "!=",
        ast.Lt: "<",
        ast.LtE: "<=",
        ast.Gt: ">",
        ast.GtE: ">=",
        ast.Is: "is",
        ast.IsNot: "is not",
        ast.In: "in",
        ast.NotIn: "not in",
        ast.Not: "not",
        ast.USub: "-",
        ast.UAdd: "+",
        ast.Invert: "~",
    }
    try:
        return table[type(node)]
    except KeyError as exc:
        raise ValueError(type(node).__name__) from exc


def _node_span(
    path: str,
    line: _Line,
    node: ast.AST,
    *,
    base_column: int = 0,
) -> SourceSpan:
    start = line.indent + base_column + int(getattr(node, "col_offset", 0)) + 1
    end = line.indent + base_column + int(
        getattr(node, "end_col_offset", len(line.text))
    ) + 1
    return SourceSpan(path, line.number, start, line.number, end)


def _expression_node(
    node: ast.AST,
    path: str,
    line: _Line,
    *,
    base_column: int = 0,
) -> SurfaceExpression:
    span = _node_span(path, line, node, base_column=base_column)
    if isinstance(node, ast.Name):
        return SurfaceName(node.id, span)
    if isinstance(node, ast.Constant):
        kind = (
            "Bool"
            if isinstance(node.value, bool)
            else "UInt64"
            if isinstance(node.value, int) and node.value >= 0
            else "Int64"
            if isinstance(node.value, int)
            else "Float64"
            if isinstance(node.value, float)
            else "Text"
            if isinstance(node.value, str)
            else "None"
        )
        return SurfaceLiteral(span, node.value, kind)
    if isinstance(node, ast.List):
        return SurfaceList(
            span,
            tuple(
                _expression_node(item, path, line, base_column=base_column)
                for item in node.elts
            ),
        )
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == "__implicit__":
            return SurfaceImplicitReceiver(span, node.attr)
        return SurfaceMember(
            span,
            _expression_node(node.value, path, line, base_column=base_column),
            node.attr,
        )
    if isinstance(node, ast.Subscript):
        return SurfaceIndex(
            span,
            _expression_node(node.value, path, line, base_column=base_column),
            _expression_node(node.slice, path, line, base_column=base_column),
        )
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "__try__" and len(node.args) == 1:
            return SurfaceTry(
                span,
                _expression_node(node.args[0], path, line, base_column=base_column),
            )
        arguments = [
            SurfaceCallArgument(
                _node_span(path, line, item, base_column=base_column),
                _expression_node(item, path, line, base_column=base_column),
            )
            for item in node.args
        ]
        arguments.extend(
            SurfaceCallArgument(
                _node_span(path, line, item.value, base_column=base_column),
                _expression_node(item.value, path, line, base_column=base_column),
                item.arg,
            )
            for item in node.keywords
        )
        return SurfaceCall(
            span,
            _expression_node(node.func, path, line, base_column=base_column),
            tuple(arguments),
        )
    if isinstance(node, ast.UnaryOp):
        return SurfaceUnary(
            span,
            _operator(node.op),
            _expression_node(node.operand, path, line, base_column=base_column),
        )
    if isinstance(node, ast.BinOp):
        return SurfaceBinary(
            _operator(node.op),
            _expression_node(node.left, path, line, base_column=base_column),
            _expression_node(node.right, path, line, base_column=base_column),
            span,
        )
    if isinstance(node, ast.BoolOp):
        values = [
            _expression_node(item, path, line, base_column=base_column)
            for item in node.values
        ]
        result = values[0]
        for value in values[1:]:
            result = SurfaceBinary(_operator(node.op), result, value, span)
        return result
    if isinstance(node, ast.Compare):
        left = _expression_node(node.left, path, line, base_column=base_column)
        comparisons = []
        for operation, comparator in zip(node.ops, node.comparators, strict=True):
            right = _expression_node(
                comparator, path, line, base_column=base_column
            )
            comparisons.append(
                SurfaceBinary(_operator(operation), left, right, span)
            )
            left = right
        result = comparisons[0]
        for comparison in comparisons[1:]:
            result = SurfaceBinary("and", result, comparison, span)
        return result
    raise SurfaceSyntaxError("UnsupportedExpression", type(node).__name__, span)


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
    try:
        parsed = ast.parse(_rewrite_expression(source), mode="eval").body
    except SyntaxError as exc:
        if "positional argument follows keyword argument" in exc.msg:
            raise SurfaceSyntaxError(
                "PositionalAfterKeyword",
                "positional argument follows keyword argument",
                SourceSpan(
                    path,
                    line.number,
                    line.indent + base_column + (exc.offset or 1),
                    line.number,
                    len(line.raw) + 1,
                ),
            ) from exc
        raise SurfaceSyntaxError(
            "InvalidExpression",
            exc.msg,
            SourceSpan(
                path,
                line.number,
                line.indent + base_column + (exc.offset or 1),
                line.number,
                len(line.raw) + 1,
            ),
        ) from exc
    expression = _expression_node(
        parsed,
        path,
        line,
        base_column=base_column,
    )
    _validate_implicit(expression, path, line)
    return expression


def _lines(source: str, path: str) -> list[_Line]:
    result = []
    for number, raw in enumerate(source.splitlines(), 1):
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
        result.append(_Line(number, indent, raw[indent:], raw))
    return result


class _Parser:
    def __init__(self, source: str, path: str) -> None:
        self.source = source
        self.path = path
        self.lines = _lines(source, path)
        self.index = 0

    def _skip_blank(self) -> None:
        while self.index < len(self.lines) and not self.lines[self.index].text.strip():
            self.index += 1

    def parse(self) -> SurfaceProgram:
        declarations: list[SurfaceDeclaration] = []
        module = None
        imports = []
        self._skip_blank()
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.indent:
                raise SurfaceSyntaxError("UnexpectedIndent", "top-level declaration expected", _span(self.path, line))
            if match := re.fullmatch(r"module\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)", line.text):
                module = match.group(1)
                self.index += 1
            elif match := re.fullmatch(r"use\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)", line.text):
                imports.append(match.group(1))
                self.index += 1
            else:
                declarations.append(self._declaration())
            self._skip_blank()
        end = self.lines[-1] if self.lines else _Line(1, 0, "", "")
        return SurfaceProgram(
            SourceSpan(self.path, 1, 1, end.number, len(end.raw) + 1),
            tuple(declarations),
            module,
            tuple(imports),
            self.source,
        )

    def _declaration(self) -> SurfaceDeclaration:
        line = self.lines[self.index]
        raw = line.text
        exported = bool(re.match(r"export\s+", raw))
        raw = re.sub(r"^export\s+", "", raw)
        enum_match = re.fullmatch(r"enum\s+([A-Z]\w*)\s*:", raw)
        if enum_match:
            return self._enum(enum_match.group(1), exported)
        record_match = re.fullmatch(r"(?:record\s+)?([A-Z]\w*)\s*:", raw)
        if record_match:
            return self._record(record_match.group(1), exported)
        function_match = re.fullmatch(
            r"(?:(fn|task)\s+)?([A-Za-z_]\w*)\((.*)\)\s*(?:->\s*([^:=]+))?\s*([:=])\s*(.*)",
            raw,
        )
        if function_match:
            return self._function(function_match, exported)
        raise SurfaceSyntaxError("ExpectedDeclaration", raw, _span(self.path, line))

    def _record(self, name: str, exported: bool) -> SurfaceRecord:
        start = self.lines[self.index]
        self.index += 1
        fields = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if not line.text.strip():
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
            if not line.text.strip():
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
        kind, name, raw_parameters, raw_return, delimiter, inline = match.groups()
        parameters = self._parameters(
            raw_parameters,
            start,
            base_column=match.start(3),
        )
        return_type = _type_name(raw_return) if raw_return else None
        self.index += 1
        if delimiter == "=" and inline.strip():
            leading = len(inline) - len(inline.lstrip())
            expression = _parse_expression(
                inline.strip(),
                self.path,
                start,
                base_column=match.start(6) + leading,
            )
            return SurfaceFunction(
                name, parameters, expression, "expression", exported,
                _span(self.path, start), kind, return_type,
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
            statements.append(self._statement())
        return tuple(statements)

    def _statement(self) -> SurfaceStatement:
        line = self.lines[self.index]
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
                if not case_line.text.strip():
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
            r"(?:(let|var)\s+)?([A-Za-z_]\w*)"
            r"(?:\s*:\s*([^=]+))?\s*"
            r"(\+=|-=|\*=|/=|(?<![=!<>])=(?!=))\s*(.+)",
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


def parse_surface(source: str, *, path: str = "main.mlo") -> SurfaceProgram:
    if not source.strip():
        raise SurfaceSyntaxError(
            "EmptySource",
            "source is empty",
            SourceSpan(path, 1, 1, 1, 1),
        )
    return _Parser(source, path).parse()


__all__ = ["SurfaceSyntaxError", "parse_surface"]
