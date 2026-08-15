from __future__ import annotations

from merlo.canonical_ast import CanonicalProgram
from merlo.elaboration.diagnostics import SurfaceElaborationError
from merlo import native_syntax as ast
from merlo.surface_ast import (
    SurfaceAnnotation,
    SurfaceAssignment,
    SurfaceBinary,
    SurfaceBinding,
    SurfaceBreak,
    SurfaceCall,
    SurfaceCase,
    SurfaceComment,
    SurfaceContinue,
    SurfaceEnum,
    SurfaceExpression,
    SurfaceExpressionStatement,
    SurfaceFor,
    SurfaceFunction,
    SurfaceIf,
    SurfaceImplicitReceiver,
    SurfaceIndex,
    SurfaceList,
    SurfaceLambda,
    SurfaceLiteral,
    SurfaceMatch,
    SurfaceMember,
    SurfaceName,
    SurfacePass,
    SurfacePrint,
    SurfaceProgram,
    SurfaceRecord,
    SurfaceReturn,
    SurfaceStatement,
    SurfaceTry,
    SurfaceUnary,
    SurfaceUses,
    SurfaceWhile,
)
from merlo.type_parser import parse_type

class _SurfaceNativeBuilder:
    """Project typed Surface nodes into Merlo-owned native syntax nodes."""

    _BINOPS = {
        "+": ast.Add,
        "-": ast.Sub,
        "*": ast.Mult,
        "/": ast.Div,
        "//": ast.FloorDiv,
        "%": ast.Mod,
        "|": ast.BitOr,
        "&": ast.BitAnd,
        "^": ast.BitXor,
        "<<": ast.LShift,
        ">>": ast.RShift,
    }
    _CMPOPS = {
        "==": ast.Eq,
        "!=": ast.NotEq,
        "<": ast.Lt,
        "<=": ast.LtE,
        ">": ast.Gt,
        ">=": ast.GtE,
    }

    def __init__(
        self,
        program: SurfaceProgram,
        canonical: CanonicalProgram,
    ) -> None:
        self.program = program
        self.canonical = canonical
        self.records = {
            item.name: item for item in program.declarations if isinstance(item, SurfaceRecord)
        }
        self.enums = {
            item.name: item for item in program.declarations if isinstance(item, SurfaceEnum)
        }
        self.functions = {
            item.name: item for item in program.declarations if isinstance(item, SurfaceFunction)
        }
        self.canonical_functions = {
            item.name: item for item in canonical.functions
        }
        self.callable_index = 0
        self.closure_index = 0
        self.current_function = ""
        self.local_types: dict[str, str] = {}

    @staticmethod
    def _loc(node: ast.AST, span) -> ast.AST:
        node.lineno = span.start_line
        node.col_offset = max(span.start_column - 1, 0)
        node.end_lineno = span.end_line
        node.end_col_offset = max(span.end_column - 1, node.col_offset)
        node._merlo_path = span.path
        return node

    def _annotation(self, type_name: str, span) -> ast.expr:
        try:
            parsed = parse_type(type_name)
        except ValueError as error:
            raise SurfaceElaborationError(f"MalformedType: {type_name}") from error

        def build(item) -> ast.expr:
            result: ast.expr = ast.Name(id=item.name, ctx=ast.Load())
            if item.args:
                result = ast.Subscript(
                    value=result,
                    slice=ast.Tuple(
                        elts=[build(argument) for argument in item.args],
                        ctx=ast.Load(),
                    )
                    if len(item.args) > 1
                    else build(item.args[0]),
                    ctx=ast.Load(),
                )
            return self._loc(result, span)

        return build(parsed)

    def _name(self, name: str, span, *, ctx: ast.expr_context = ast.Load()) -> ast.Name:
        return self._loc(ast.Name(id=name, ctx=ctx), span)

    def _expr(self, expression: SurfaceExpression) -> ast.expr:
        if isinstance(expression, SurfaceName):
            return self._name(expression.name, expression.span)
        if isinstance(expression, SurfaceLiteral):
            return self._loc(ast.Constant(value=expression.value), expression.span)
        if isinstance(expression, SurfaceImplicitReceiver):
            return self._loc(
                ast.Attribute(
                    value=self._name("__item", expression.span),
                    attr=expression.field,
                    ctx=ast.Load(),
                ),
                expression.span,
            )
        if isinstance(expression, SurfaceLambda):
            closures = self.canonical_functions[self.current_function].closures
            if self.closure_index >= len(closures):
                raise SurfaceElaborationError("MissingClosureMetadata")
            metadata = closures[self.closure_index]
            self.closure_index += 1
            arguments = ast.arguments(
                posonlyargs=[],
                args=[
                    self._loc(
                        ast.arg(
                            arg=name,
                            annotation=self._annotation(type_name, expression.span),
                        ),
                        expression.span,
                    )
                    for name, type_name in metadata.parameters
                ],
                kwonlyargs=[],
                kw_defaults=[],
                defaults=[],
                vararg=None,
                kwarg=None,
            )
            result = self._loc(
                ast.Lambda(args=arguments, body=self._expr(expression.body)),
                expression.span,
            )
            result._merlo_closure_metadata = (
                metadata.closure_id,
                metadata.parameters,
                metadata.return_type,
                tuple(
                    (item.name, item.type_name, item.ownership)
                    for item in metadata.captures
                ),
                self.current_function,
            )
            return result
        if isinstance(expression, SurfaceList):
            return self._loc(
                ast.List(elts=[self._expr(item) for item in expression.items], ctx=ast.Load()),
                expression.span,
            )
        if isinstance(expression, SurfaceMember):
            return self._loc(
                ast.Attribute(
                    value=self._expr(expression.receiver),
                    attr=expression.field,
                    ctx=ast.Load(),
                ),
                expression.span,
            )
        if isinstance(expression, SurfaceIndex):
            return self._loc(
                ast.Subscript(
                    value=self._expr(expression.receiver),
                    slice=self._expr(expression.index),
                    ctx=ast.Load(),
                ),
                expression.span,
            )
        if isinstance(expression, SurfaceUnary):
            operators = {"-": ast.USub, "+": ast.UAdd, "~": ast.Invert, "not": ast.Not}
            operator = operators.get(expression.operator)
            if operator is None:
                raise SurfaceElaborationError(f"UnsupportedUnary: {expression.operator}")
            return self._loc(
                ast.UnaryOp(op=operator(), operand=self._expr(expression.operand)),
                expression.span,
            )
        if isinstance(expression, SurfaceBinary):
            if expression.operator in {"and", "or"}:
                operator = ast.And() if expression.operator == "and" else ast.Or()
                return self._loc(
                    ast.BoolOp(
                        op=operator,
                        values=[self._expr(expression.left), self._expr(expression.right)],
                    ),
                    expression.span,
                )
            if expression.operator in self._CMPOPS:
                return self._loc(
                    ast.Compare(
                        left=self._expr(expression.left),
                        ops=[self._CMPOPS[expression.operator]()],
                        comparators=[self._expr(expression.right)],
                    ),
                    expression.span,
                )
            operator = self._BINOPS.get(expression.operator)
            if operator is None:
                raise SurfaceElaborationError(f"UnsupportedBinary: {expression.operator}")
            return self._loc(
                ast.BinOp(
                    left=self._expr(expression.left),
                    op=operator(),
                    right=self._expr(expression.right),
                ),
                expression.span,
            )
        if isinstance(expression, SurfaceTry):
            return self._loc(
                ast.Call(
                    func=self._name("__merlo_try__", expression.span),
                    args=[self._expr(expression.expression)],
                    keywords=[],
                ),
                expression.span,
            )
        if isinstance(expression, SurfaceCall):
            callee = self._expr(expression.callee)
            args: list[ast.expr] = []
            parameter_names: tuple[str, ...] = ()
            if isinstance(expression.callee, SurfaceName):
                if expression.callee.name in self.functions:
                    parameter_names = tuple(
                        item.name for item in self.functions[expression.callee.name].parameters
                    )
                elif expression.callee.name in self.records:
                    parameter_names = tuple(
                        item.name for item in self.records[expression.callee.name].fields
                    )
                elif expression.callee.name == "None":
                    parameter_names = ()
                elif expression.callee.name in {"Ok", "Err", "Some"}:
                    parameter_names = ("value",)
            if parameter_names:
                assigned = {
                    argument.name: argument
                    for argument in expression.arguments
                    if argument.name is not None
                }
                positional = [
                    argument
                    for argument in expression.arguments
                    if argument.name is None
                ]
                for index, name in enumerate(parameter_names):
                    argument = assigned.get(name)
                    if argument is None and index < len(positional):
                        argument = positional[index]
                    if argument is not None:
                        args.append(self._expr(argument.value))
            else:
                args = [self._expr(argument.value) for argument in expression.arguments]
            if isinstance(expression.callee, SurfaceMember) and expression.callee.field in {
                "where", "map", "count"
            }:
                for index, argument in enumerate(expression.arguments):
                    if any(isinstance(item, SurfaceImplicitReceiver) for item in argument.value.walk()):
                        if self.callable_index >= len(
                            self.canonical_functions[self.current_function].implicit_callables
                        ):
                            raise SurfaceElaborationError("MissingImplicitCallableMetadata")
                        metadata = self.canonical_functions[
                            self.current_function
                        ].implicit_callables[self.callable_index]
                        self.callable_index += 1
                        candidate = args[index] if index < len(args) else self._expr(argument.value)
                        candidate._merlo_implicit_callable = (
                            metadata.callable_id,
                            metadata.parameter,
                            metadata.parameter_type,
                            metadata.return_type,
                            metadata.expression,
                        )
            return self._loc(
                ast.Call(func=callee, args=args, keywords=[]),
                expression.span,
            )
        raise SurfaceElaborationError(f"UnsupportedExpression: {type(expression).__name__}")

    def _pattern(self, case: SurfaceCase, subject_type: str | None) -> ast.pattern:
        raw = case.pattern
        if raw == "_":
            return self._loc(
                ast.MatchAs(),
                case.pattern_span or case.span,
            )
        payload = None
        if "(" in raw and raw.endswith(")"):
            raw, payload = raw[:-1].split("(", 1)
            payload = payload or None
        owner = None
        variant = raw.rsplit(".", 1)[-1]
        if "." in raw:
            owner = raw.rsplit(".", 1)[0]
        elif subject_type in self.enums:
            owner = subject_type
        if owner in self.enums:
            value: ast.expr = self._loc(
                ast.Attribute(
                    value=self._name(owner, case.pattern_span or case.span),
                    attr=variant,
                    ctx=ast.Load(),
                ),
                case.pattern_span or case.span,
            )
            if payload is None:
                return self._loc(ast.MatchValue(value=value), case.pattern_span or case.span)
            return self._loc(
                ast.MatchClass(
                    cls=value,
                    patterns=[
                        self._loc(
                            ast.MatchAs(
                                name=None if payload == "_" else payload
                            ),
                            case.pattern_span or case.span,
                        )
                    ],
                    kwd_attrs=[],
                    kwd_patterns=[],
                ),
                case.pattern_span or case.span,
            )
        if variant in {"None", "NoneValue"} and payload is None:
            return self._loc(ast.MatchClass(cls=self._name("NoneValue", case.span), patterns=[], kwd_attrs=[], kwd_patterns=[]), case.pattern_span or case.span)
        if payload is None:
            return self._loc(
                ast.MatchClass(
                    cls=self._name(variant, case.pattern_span or case.span),
                    patterns=[],
                    kwd_attrs=[],
                    kwd_patterns=[],
                ),
                case.pattern_span or case.span,
            )
        return self._loc(
            ast.MatchClass(
                cls=self._name(variant, case.pattern_span or case.span),
                patterns=[
                    self._loc(
                        ast.MatchAs(name=None if payload == "_" else payload),
                        case.pattern_span or case.span,
                    )
                ],
                kwd_attrs=[],
                kwd_patterns=[],
            ),
            case.pattern_span or case.span,
        )

    def _statement(self, statement: SurfaceStatement, *, tail: bool = False) -> ast.stmt:
        if isinstance(statement, SurfaceBinding):
            binding = self.canonical_functions[self.current_function].binding(
                statement.name
            )
            binding_type = binding.type_name
            value = self._expr(statement.value)
            self.local_types[statement.name] = binding_type
            return self._loc(
                ast.AnnAssign(
                    target=self._name(statement.name, statement.span, ctx=ast.Store()),
                    annotation=self._annotation(binding_type, statement.span),
                    value=value,
                    simple=1,
                ),
                statement.span,
            )
        if isinstance(statement, SurfaceAnnotation):
            self.local_types[statement.name] = statement.type_name
            return self._loc(
                ast.AnnAssign(
                    target=self._name(
                        statement.name,
                        statement.span,
                        ctx=ast.Store(),
                    ),
                    annotation=self._annotation(
                        statement.type_name,
                        statement.span,
                    ),
                    value=None,
                    simple=1,
                ),
                statement.span,
            )
        if isinstance(statement, SurfaceAssignment):
            target = self._expr(statement.target)
            if isinstance(target, (ast.Name, ast.Attribute, ast.Subscript)):
                target.ctx = ast.Store()
            value = self._expr(statement.value)
            if statement.operator == "=":
                return self._loc(ast.Assign(targets=[target], value=value), statement.span)
            operators = {
                "+=": ast.Add,
                "-=": ast.Sub,
                "*=": ast.Mult,
                "/=": ast.Div,
            }
            operator = operators.get(statement.operator)
            if operator is None:
                raise SurfaceElaborationError(f"UnsupportedAssignment: {statement.operator}")
            return self._loc(ast.AugAssign(target=target, op=operator(), value=value), statement.span)
        if isinstance(statement, SurfaceReturn):
            return self._loc(
                ast.Return(value=self._expr(statement.expression) if statement.expression else None),
                statement.span,
            )
        if isinstance(statement, SurfaceBreak):
            return self._loc(ast.Break(), statement.span)
        if isinstance(statement, SurfaceContinue):
            return self._loc(ast.Continue(), statement.span)
        if isinstance(statement, SurfacePass):
            return self._loc(ast.Pass(), statement.span)
        if isinstance(statement, SurfacePrint):
            return self._loc(
                ast.Expr(
                    value=self._loc(
                        ast.Call(
                            func=self._loc(
                                ast.Attribute(
                                    value=self._name("console", statement.span),
                                    attr="write",
                                    ctx=ast.Load(),
                                ),
                                statement.span,
                            ),
                            args=[self._expr(statement.expression)],
                            keywords=[],
                        ),
                        statement.span,
                    )
                ),
                statement.span,
            )
        if isinstance(statement, SurfaceExpressionStatement):
            expression = self._expr(statement.expression)
            return self._loc(
                ast.Return(expression) if tail else ast.Expr(expression),
                statement.span,
            )
        if isinstance(statement, SurfaceIf):
            return self._loc(
                ast.If(
                    test=self._expr(statement.condition),
                    body=self._statements(statement.body, tail_returns=False),
                    orelse=self._statements(statement.otherwise, tail_returns=False),
                ),
                statement.span,
            )
        if isinstance(statement, SurfaceWhile):
            return self._loc(
                ast.While(
                    test=self._expr(statement.condition),
                    body=self._statements(statement.body, tail_returns=False),
                    orelse=[],
                ),
                statement.span,
            )
        if isinstance(statement, SurfaceFor):
            self.local_types[statement.name] = "Inferred"
            return self._loc(
                ast.For(
                    target=self._name(statement.name, statement.span, ctx=ast.Store()),
                    iter=self._expr(statement.iterable),
                    body=self._statements(statement.body, tail_returns=False),
                    orelse=[],
                    type_comment=None,
                ),
                statement.span,
            )
        if isinstance(statement, SurfaceMatch):
            subject = self._expr(statement.expression)
            if isinstance(statement.expression, SurfaceName):
                subject_type = self.local_types.get(statement.expression.name)
            elif (
                isinstance(statement.expression, SurfaceCall)
                and isinstance(statement.expression.callee, SurfaceName)
                and statement.expression.callee.name in self.canonical_functions
            ):
                subject_type = self.canonical_functions[
                    statement.expression.callee.name
                ].return_type
            else:
                subject_type = None
            return self._loc(
                ast.Match(
                    subject=subject,
                    cases=[
                        self._loc(
                            ast.match_case(
                                pattern=self._pattern(case, subject_type),
                                guard=None,
                                body=self._statements(case.body, tail_returns=False),
                            ),
                            case.span,
                        )
                        for case in statement.cases
                    ],
                ),
                statement.span,
            )
        raise SurfaceElaborationError(f"UnsupportedStatement: {type(statement).__name__}")

    def _statements(
        self,
        statements: tuple[SurfaceStatement, ...],
        *,
        tail_returns: bool = True,
    ) -> list[ast.stmt]:
        executable = tuple(
            statement
            for statement in statements
            if not isinstance(statement, (SurfaceUses, SurfaceComment))
        )
        return [
            self._statement(
                statement,
                tail=tail_returns
                and isinstance(statement, SurfaceExpressionStatement)
                and index == len(executable) - 1,
            )
            for index, statement in enumerate(executable)
        ]

    def build(self) -> tuple[ast.Module, tuple[tuple[str, str], ...], tuple[tuple[int, str], ...]]:
        body: list[ast.stmt] = []
        declaration_kinds: list[tuple[str, str]] = []
        for declaration in self.program.declarations:
            if isinstance(declaration, SurfaceRecord):
                declaration_kinds.append((declaration.name, "record"))
                fields = [
                    self._loc(
                        ast.AnnAssign(
                            target=self._name(field.name, field.span, ctx=ast.Store()),
                            annotation=self._annotation(field.type_name, field.span),
                            value=None,
                            simple=1,
                        ),
                        field.span,
                    )
                    for field in declaration.fields
                ]
                body.append(
                    self._loc(
                        ast.ClassDef(
                            name=declaration.name,
                            bases=[],
                            keywords=[],
                            body=fields,
                            decorator_list=[],
                        ),
                        declaration.span,
                    )
                )
            elif isinstance(declaration, SurfaceEnum):
                declaration_kinds.append((declaration.name, "enum"))
                variants: list[ast.stmt] = []
                for variant in declaration.variants:
                    if variant.type_name is None:
                        variants.append(
                            self._loc(
                                ast.Expr(value=self._name(variant.name, variant.span)),
                                variant.span,
                            )
                        )
                    else:
                        variants.append(
                            self._loc(
                                ast.AnnAssign(
                                    target=self._name(variant.name, variant.span, ctx=ast.Store()),
                                    annotation=self._annotation(variant.type_name, variant.span),
                                    value=None,
                                    simple=1,
                                ),
                                variant.span,
                            )
                        )
                body.append(
                    self._loc(
                        ast.ClassDef(
                            name=declaration.name,
                            bases=[],
                            keywords=[],
                            body=variants,
                            decorator_list=[],
                        ),
                        declaration.span,
                    )
                )
        binding_kinds: dict[int, str] = {}
        for declaration in self.program.declarations:
            if not isinstance(declaration, SurfaceFunction):
                continue
            canonical = self.canonical_functions[declaration.name]
            self.current_function = declaration.name
            self.callable_index = 0
            self.closure_index = 0
            self.local_types = dict(canonical.parameters)
            args = [
                self._loc(
                    ast.arg(
                        arg=parameter.name,
                        annotation=self._annotation(
                            dict(canonical.parameters).get(
                                parameter.name,
                                parameter.type_name or "Inferred",
                            ),
                            parameter.span,
                        ),
                    ),
                    parameter.span,
                )
                for parameter in declaration.parameters
            ]
            if declaration.body_kind == "expression":
                statements = [self._loc(ast.Return(self._expr(declaration.body)), declaration.body.span)]  # type: ignore[union-attr]
            else:
                statements = self._statements(declaration.body)  # type: ignore[arg-type]
            function = self._loc(
                ast.FunctionDef(
                    name=declaration.name,
                    args=ast.arguments(
                        posonlyargs=[],
                        args=args,
                        kwonlyargs=[],
                        kw_defaults=[],
                        defaults=[],
                        vararg=None,
                        kwarg=None,
                    ),
                    body=statements,
                    decorator_list=[],
                    returns=self._annotation(canonical.return_type, declaration.span),
                    type_comment=None,
                ),
                declaration.span,
            )
            body.append(function)
            for statement in declaration.body if declaration.body_kind == "block" else ():
                for nested in statement.walk():
                    if isinstance(nested, SurfaceBinding):
                        binding_kinds[nested.span.start_line] = (
                            "var" if nested.explicit_kind == "var" else "let"
                        )
        module = self._loc(ast.Module(body=body, type_ignores=[]), self.program.span)
        ast.fix_missing_locations(module)
        ast.validate_module(module)
        return module, tuple(declaration_kinds), tuple(sorted(binding_kinds.items()))

def surface_lowering_module(
    program: SurfaceProgram,
    canonical: CanonicalProgram,
) -> tuple[ast.Module, tuple[tuple[str, str], ...], tuple[tuple[int, str], ...]]:
    """Adapt the typed Surface tree at the HIR boundary without reparsing text."""
    return _SurfaceNativeBuilder(program, canonical).build()

__all__ = ["surface_lowering_module"]
