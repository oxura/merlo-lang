"""Compile-time specialization and static dispatch for Surface generics."""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import replace

from merlo.elaboration.calls import bind_call_arguments
from merlo.elaboration.diagnostics import SurfaceElaborationError
from merlo.static_constraints import SUPPORTED_CONSTRAINTS, satisfies_constraint
from merlo.surface_ast import (
    SurfaceAnnotation,
    SurfaceAssignment,
    SurfaceBinary,
    SurfaceBinding,
    SurfaceCall,
    SurfaceCallArgument,
    SurfaceExpression,
    SurfaceExpressionStatement,
    SurfaceFor,
    SurfaceFunction,
    SurfaceIf,
    SurfaceIndex,
    SurfaceList,
    SurfaceLiteral,
    SurfaceMatch,
    SurfaceMember,
    SurfaceName,
    SurfacePrint,
    SurfaceProgram,
    SurfaceRecord,
    SurfaceReturn,
    SurfaceEnum,
    SurfaceStatement,
    SurfaceTry,
    SurfaceUnary,
    SurfaceWhile,
)
from merlo.type_parser import GenericTypeSyntaxError, TypeExpr, parse_type


def _parsed(type_name: str) -> TypeExpr:
    try:
        return parse_type(type_name)
    except GenericTypeSyntaxError as error:
        raise SurfaceElaborationError(f"MalformedType: {type_name}") from error


def _substitute_expression(expression: TypeExpr, mapping: dict[str, str]) -> TypeExpr:
    if not expression.args and expression.name in mapping:
        return _parsed(mapping[expression.name])
    return TypeExpr(
        expression.name,
        tuple(_substitute_expression(item, mapping) for item in expression.args),
    )


def _substitute(type_name: str | None, mapping: dict[str, str]) -> str | None:
    if type_name is None:
        return None
    return _substitute_expression(_parsed(type_name), mapping).canonical


def _contains_parameter(expression: TypeExpr, parameters: frozenset[str]) -> bool:
    return expression.name in parameters or any(
        _contains_parameter(item, parameters) for item in expression.args
    )


def _bind_type(
    pattern_name: str,
    actual_name: str | None,
    parameters: frozenset[str],
    mapping: dict[str, str],
    *,
    context: str,
) -> None:
    pattern = _parsed(pattern_name)
    if actual_name is None:
        if _contains_parameter(pattern, parameters):
            raise SurfaceElaborationError(
                f"GenericBoundaryAnnotationRequired: {context}"
            )
        return
    actual = _parsed(actual_name)

    def visit(expected: TypeExpr, observed: TypeExpr) -> None:
        if not expected.args and expected.name in parameters:
            previous = mapping.get(expected.name)
            concrete = observed.canonical
            if previous is not None and previous != concrete:
                raise SurfaceElaborationError(
                    f"GenericTypeConflict: {context}: {previous} vs {concrete}"
                )
            mapping[expected.name] = concrete
            return
        if expected.name != observed.name or len(expected.args) != len(observed.args):
            raise SurfaceElaborationError(
                f"GenericTypeConflict: {context}: "
                f"{expected.canonical} vs {observed.canonical}"
            )
        for expected_item, observed_item in zip(
            expected.args, observed.args, strict=True
        ):
            visit(expected_item, observed_item)

    visit(pattern, actual)


def _collection_element(type_name: str | None) -> str | None:
    if type_name is None:
        return None
    parsed = _parsed(type_name)
    if parsed.name in {"Vec", "Option", "Box"} and len(parsed.args) == 1:
        return parsed.args[0].canonical
    if parsed.name == "Array" and parsed.args:
        return parsed.args[0].canonical
    if parsed.name == "Map" and len(parsed.args) == 2:
        return parsed.args[1].canonical
    if parsed.name in {"Bytes", "BytesView", "Text", "TextView"}:
        return "Byte"
    return None


class _Monomorphizer:
    def __init__(self, program: SurfaceProgram) -> None:
        self.program = program
        self.templates = {
            item.name: item
            for item in program.declarations
            if isinstance(item, SurfaceFunction) and item.type_parameters
        }
        self.records = {
            item.name: item
            for item in program.declarations
            if isinstance(item, SurfaceRecord)
        }
        self.enums = {
            item.name: item
            for item in program.declarations
            if isinstance(item, SurfaceEnum)
        }
        self.functions = {
            item.name: item
            for item in program.declarations
            if isinstance(item, SurfaceFunction)
        }
        self.returns = {
            item.name: item.return_type
            for item in self.functions.values()
            if not item.type_parameters and item.return_type is not None
        }
        self.instances: dict[tuple[str, tuple[str, ...]], str] = {}
        self.instance_returns: dict[str, str | None] = {}
        self.pending: deque[tuple[SurfaceFunction, dict[str, str], str]] = deque()

    def _validate_boundaries(self) -> None:
        for template in self.templates.values():
            for parameter in template.parameters:
                if parameter.type_name is None:
                    raise SurfaceElaborationError(
                        f"GenericBoundaryAnnotationRequired: "
                        f"{template.name}.{parameter.name}"
                    )
            if template.return_type is None:
                raise SurfaceElaborationError(
                    f"GenericBoundaryAnnotationRequired: {template.name} return"
                )
            for type_parameter in template.type_parameters:
                for constraint in type_parameter.constraints:
                    if constraint not in SUPPORTED_CONSTRAINTS:
                        raise SurfaceElaborationError(
                            f"UnknownTypeConstraint: {constraint}"
                        )

    def _infer(self, expression: SurfaceExpression, environment: dict[str, str]) -> str | None:
        if isinstance(expression, SurfaceLiteral):
            return expression.kind
        if isinstance(expression, SurfaceName):
            return environment.get(expression.name) or self.returns.get(expression.name)
        if isinstance(expression, SurfaceList):
            if not expression.items:
                return None
            item_types = tuple(self._infer(item, environment) for item in expression.items)
            if item_types[0] is not None and all(item == item_types[0] for item in item_types):
                return f"Vec[{item_types[0]}]"
            return None
        if isinstance(expression, SurfaceIndex):
            return _collection_element(self._infer(expression.receiver, environment))
        if isinstance(expression, SurfaceUnary):
            if expression.operator == "not":
                return "Bool"
            return self._infer(expression.operand, environment)
        if isinstance(expression, SurfaceBinary):
            if expression.operator in {"==", "!=", "<", "<=", ">", ">="}:
                return "Bool"
            return self._infer(expression.left, environment) or self._infer(
                expression.right, environment
            )
        if isinstance(expression, SurfaceTry):
            inner = self._infer(expression.expression, environment)
            if inner is None:
                return None
            parsed = _parsed(inner)
            if parsed.name in {"Option", "Result"} and parsed.args:
                return parsed.args[0].canonical
            return inner
        if isinstance(expression, SurfaceCall) and isinstance(
            expression.callee, SurfaceName
        ):
            return self.instance_returns.get(expression.callee.name) or self.returns.get(
                expression.callee.name
            )
        return None

    def _specialization(
        self,
        template: SurfaceFunction,
        mapping: dict[str, str],
    ) -> str:
        ordered = tuple(mapping[item.name] for item in template.type_parameters)
        key = (template.name, ordered)
        existing = self.instances.get(key)
        if existing is not None:
            return existing
        digest = hashlib.sha256(
            (template.name + "\x1f" + "\x1f".join(ordered)).encode("utf-8")
        ).hexdigest()[:12]
        name = f"{template.name}__mono_{digest}"
        self.instances[key] = name
        result_type = _substitute(template.return_type, mapping)
        self.instance_returns[name] = result_type
        self.returns[name] = result_type
        self.pending.append((template, dict(mapping), name))
        return name

    def _generic_call(
        self,
        expression: SurfaceCall,
        template: SurfaceFunction,
        environment: dict[str, str],
        expected: str | None,
    ) -> SurfaceCall:
        parameters = frozenset(item.name for item in template.type_parameters)
        mapping: dict[str, str] = {}
        bound = bind_call_arguments(
            expression,
            tuple(item.name for item in template.parameters),
            template.name,
        )
        rewritten: dict[str, SurfaceCallArgument] = {}
        for parameter_name, argument in bound:
            parameter = next(
                item for item in template.parameters if item.name == parameter_name
            )
            parameter_expected = _substitute(parameter.type_name, mapping)
            value = self._expression(argument.value, environment, parameter_expected)
            actual = self._infer(value, environment)
            if parameter.type_name is not None:
                _bind_type(
                    parameter.type_name,
                    actual,
                    parameters,
                    mapping,
                    context=f"call {template.name}.{parameter.name}",
                )
            rewritten[parameter_name] = replace(argument, value=value)
        if expected is not None and template.return_type is not None:
            _bind_type(
                template.return_type,
                expected,
                parameters,
                mapping,
                context=f"call {template.name} return",
            )
        missing = [item.name for item in template.type_parameters if item.name not in mapping]
        if missing:
            raise SurfaceElaborationError(
                f"GenericBoundaryAnnotationRequired: {template.name}: "
                + ",".join(missing)
            )
        for type_parameter in template.type_parameters:
            concrete = mapping[type_parameter.name]
            for constraint in type_parameter.constraints:
                if not satisfies_constraint(
                    constraint,
                    concrete,
                    records=self.records,
                    enums=self.enums,
                ):
                    raise SurfaceElaborationError(
                        f"UnsatisfiedTypeConstraint: {template.name}."
                        f"{type_parameter.name}: {concrete} does not satisfy "
                        f"{constraint}"
                    )
        name = self._specialization(template, mapping)
        arguments = tuple(
            rewritten[item.name] for item in template.parameters
        )
        return replace(
            expression,
            callee=SurfaceName(name, expression.callee.span),
            arguments=arguments,
        )

    def _expression(
        self,
        expression: SurfaceExpression,
        environment: dict[str, str],
        expected: str | None = None,
    ) -> SurfaceExpression:
        if isinstance(expression, SurfaceName):
            if expression.name in self.templates:
                raise SurfaceElaborationError(
                    f"GenericFunctionValueRequiresInstantiation: {expression.name}"
                )
            return expression
        if isinstance(expression, (SurfaceLiteral,)):
            return expression
        if isinstance(expression, SurfaceList):
            return replace(
                expression,
                items=tuple(self._expression(item, environment) for item in expression.items),
            )
        if isinstance(expression, SurfaceMember):
            return replace(
                expression,
                receiver=self._expression(expression.receiver, environment),
            )
        if isinstance(expression, SurfaceIndex):
            return replace(
                expression,
                receiver=self._expression(expression.receiver, environment),
                index=self._expression(expression.index, environment),
            )
        if isinstance(expression, SurfaceUnary):
            return replace(
                expression,
                operand=self._expression(expression.operand, environment, expected),
            )
        if isinstance(expression, SurfaceBinary):
            return replace(
                expression,
                left=self._expression(expression.left, environment, expected),
                right=self._expression(expression.right, environment, expected),
            )
        if isinstance(expression, SurfaceTry):
            return replace(
                expression,
                expression=self._expression(expression.expression, environment),
            )
        if isinstance(expression, SurfaceCall):
            if isinstance(expression.callee, SurfaceName):
                template = self.templates.get(expression.callee.name)
                if template is not None:
                    return self._generic_call(
                        expression, template, environment, expected
                    )
            return replace(
                expression,
                callee=self._expression(expression.callee, environment),
                arguments=tuple(
                    replace(
                        argument,
                        value=self._expression(argument.value, environment),
                    )
                    for argument in expression.arguments
                ),
            )
        return expression

    def _statements(
        self,
        statements: tuple[SurfaceStatement, ...],
        environment: dict[str, str],
        return_type: str | None,
    ) -> tuple[SurfaceStatement, ...]:
        output: list[SurfaceStatement] = []
        for index, statement in enumerate(statements):
            tail = index == len(statements) - 1
            if isinstance(statement, SurfaceBinding):
                value = self._expression(
                    statement.value, environment, statement.type_name
                )
                inferred = statement.type_name or self._infer(value, environment)
                if inferred is not None:
                    environment[statement.name] = inferred
                output.append(replace(statement, value=value))
            elif isinstance(statement, SurfaceAnnotation):
                environment[statement.name] = statement.type_name
                output.append(statement)
            elif isinstance(statement, SurfaceAssignment):
                expected = (
                    environment.get(statement.target.name)
                    if isinstance(statement.target, SurfaceName)
                    else None
                )
                output.append(
                    replace(
                        statement,
                        target=self._expression(statement.target, environment),
                        value=self._expression(statement.value, environment, expected),
                    )
                )
            elif isinstance(statement, SurfaceExpressionStatement):
                output.append(
                    replace(
                        statement,
                        expression=self._expression(
                            statement.expression,
                            environment,
                            return_type if tail else None,
                        ),
                    )
                )
            elif isinstance(statement, SurfaceReturn):
                output.append(
                    replace(
                        statement,
                        expression=(
                            self._expression(statement.expression, environment, return_type)
                            if statement.expression is not None
                            else None
                        ),
                    )
                )
            elif isinstance(statement, SurfacePrint):
                output.append(
                    replace(
                        statement,
                        expression=self._expression(statement.expression, environment),
                    )
                )
            elif isinstance(statement, SurfaceFor):
                iterable = self._expression(statement.iterable, environment)
                nested = dict(environment)
                element = _collection_element(self._infer(iterable, environment))
                if element is not None:
                    nested[statement.name] = element
                output.append(
                    replace(
                        statement,
                        iterable=iterable,
                        body=self._statements(statement.body, nested, return_type),
                    )
                )
            elif isinstance(statement, SurfaceIf):
                output.append(
                    replace(
                        statement,
                        condition=self._expression(
                            statement.condition, environment, "Bool"
                        ),
                        body=self._statements(
                            statement.body, dict(environment), return_type
                        ),
                        otherwise=self._statements(
                            statement.otherwise, dict(environment), return_type
                        ),
                    )
                )
            elif isinstance(statement, SurfaceWhile):
                output.append(
                    replace(
                        statement,
                        condition=self._expression(
                            statement.condition, environment, "Bool"
                        ),
                        body=self._statements(
                            statement.body, dict(environment), return_type
                        ),
                    )
                )
            elif isinstance(statement, SurfaceMatch):
                output.append(
                    replace(
                        statement,
                        expression=self._expression(statement.expression, environment),
                        cases=tuple(
                            replace(
                                case,
                                body=self._statements(
                                    case.body, dict(environment), return_type
                                ),
                            )
                            for case in statement.cases
                        ),
                    )
                )
            else:
                output.append(statement)
        return tuple(output)

    def _function(
        self,
        function: SurfaceFunction,
        mapping: dict[str, str] | None = None,
        name: str | None = None,
    ) -> SurfaceFunction:
        substitutions = mapping or {}
        parameters = tuple(
            replace(
                item,
                type_name=_substitute(item.type_name, substitutions),
            )
            for item in function.parameters
        )
        return_type = _substitute(function.return_type, substitutions)
        environment = {
            item.name: item.type_name
            for item in parameters
            if item.type_name is not None
        }
        if isinstance(function.body, tuple):
            body: SurfaceExpression | tuple[SurfaceStatement, ...] = self._statements(
                function.body, environment, return_type
            )
        else:
            body = self._expression(function.body, environment, return_type)
        return replace(
            function,
            name=name or function.name,
            parameters=parameters,
            body=body,
            return_type=return_type,
            exported=function.exported if mapping is None else False,
            type_parameters=(),
        )

    def run(self) -> SurfaceProgram:
        if not self.templates:
            return self.program
        self._validate_boundaries()
        declarations = [
            self._function(item) if isinstance(item, SurfaceFunction) else item
            for item in self.program.declarations
            if not isinstance(item, SurfaceFunction) or not item.type_parameters
        ]
        specializations: list[SurfaceFunction] = []
        while self.pending:
            template, mapping, name = self.pending.popleft()
            specializations.append(self._function(template, mapping, name))
        specializations.sort(key=lambda item: item.name)
        return replace(
            self.program,
            declarations=tuple((*declarations, *specializations)),
        )


def monomorphize_surface(program: SurfaceProgram) -> SurfaceProgram:
    """Replace generic templates with deterministic concrete specializations."""

    return _Monomorphizer(program).run()


__all__ = ["monomorphize_surface"]
