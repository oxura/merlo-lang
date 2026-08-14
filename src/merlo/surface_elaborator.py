from __future__ import annotations
import ast
import hashlib

from dataclasses import dataclass, field, replace
from typing import Iterable

from merlo.canonical_ast import (
    CanonicalBinding,
    CanonicalCallable,
    CanonicalEnum,
    CanonicalFunction,
    CanonicalProgram,
    CanonicalRecord,
    CanonicalReturn,
    CanonicalOptionFallback,
)
from merlo.surface_ast import (
    SurfaceAssignment,
    SurfaceBinary,
    SurfaceBinding,
    SurfaceBreak,
    SurfaceCall,
    SurfaceCallArgument,
    SurfaceContinue,
    SurfaceExpression,
    SurfaceExpressionStatement,
    SurfaceFor,
    SurfaceFunction,
    SurfaceIf,
    SurfaceCase,
    SurfaceEnum,
    SurfaceIndex,
    SurfaceList,
    SurfaceLiteral,
    SurfaceMember,
    SurfaceMatch,
    SurfaceName,
    SurfaceImplicitReceiver,
    SurfacePass,
    SurfacePrint,
    SurfaceProgram,
    SurfaceRecord,
    SurfaceReturn,
    SurfaceStatement,
    SurfaceTry,
    SurfaceUnary,
    SurfaceWhile,
)
from merlo.type_parser import generic_parts, parse_type
from merlo.intrinsics import INTRINSIC_SIGNATURES, contextual_result_type

class SurfaceElaborationError(ValueError):
    pass

_HOST_CALLS = {
    name: (signature.parameters, signature.result_type, signature.effect, signature.capability)
    for name, signature in INTRINSIC_SIGNATURES.items()
}


def _generic_parts(type_name: str, constructor: str) -> tuple[str, ...] | None:
    return generic_parts(type_name, constructor)

def _edit_distance_one(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(
            first != second
            for first, second in zip(left, right, strict=True)
        ) == 1
    shorter, longer = (
        (left, right) if len(left) < len(right) else (right, left)
    )
    return any(
        longer[:index] + longer[index + 1:] == shorter
        for index in range(len(longer))
    )


@dataclass(frozen=True)
class InferenceDecision:
    owner: str
    name: str
    kind: str
    type_name: str
    mutable: bool
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class SurfaceElaboration:
    canonical: CanonicalProgram
    decisions: tuple[InferenceDecision, ...]


class _Types:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.concrete: dict[str, str] = {}

    def variable(self, name: str) -> str:
        self.parent.setdefault(name, name)
        return name

    def find(self, term: str) -> str:
        self.parent.setdefault(term, term)
        while self.parent[term] != term:
            self.parent[term] = self.parent[self.parent[term]]
            term = self.parent[term]
        return term

    def typed(self, type_name: str) -> str:
        try:
            parsed = parse_type(type_name)
        except ValueError as error:
            raise SurfaceElaborationError(f"MalformedType: {type_name}") from error
        stack = [parsed]
        while stack:
            current = stack.pop()
            if current.name == "Any":
                raise SurfaceElaborationError("DynamicAnyForbidden")
            stack.extend(current.args)
        canonical = parsed.canonical
        if canonical.startswith("Map[") and canonical != "Map[Text,UInt64]":
            raise SurfaceElaborationError(f"UnsupportedMapType: {canonical}")
        term = self.variable(f"type:{canonical}")
        self.concrete[self.find(term)] = canonical
        return term

    def unify(self, left: str, right: str, *, context: str) -> str:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return left_root
        left_type = self.concrete.get(left_root)
        right_type = self.concrete.get(right_root)
        if left_type and right_type and left_type != right_type:
            raise SurfaceElaborationError(
                f"TypeConflict: {context}: {left_type} vs {right_type}"
            )
        self.parent[right_root] = left_root
        selected = left_type or right_type
        if selected:
            self.concrete[left_root] = selected
        self.concrete.pop(right_root, None)
        return left_root

    def resolve(self, term: str, *, name: str) -> str:
        value = self.concrete.get(self.find(term))
        if value is None:
            raise SurfaceElaborationError(f"AmbiguousType: {name}")
        return value


@dataclass
class _Function:
    source: SurfaceFunction
    parameters: dict[str, str]
    return_term: str
    locals: dict[str, str]
    assignments: dict[str, int]
    first_bindings: dict[str, SurfaceBinding]
    evidence: dict[str, set[str]]
    calls: set[str] = field(default_factory=set)
    error_calls: set[str] = field(default_factory=set)
    effects: set[str] = field(default_factory=set)
    capabilities: set[str] = field(default_factory=set)
    errors: set[str] = field(default_factory=set)
    implicit_callables: dict[str, CanonicalCallable] = field(default_factory=dict)
    option_fallbacks: dict[str, CanonicalOptionFallback] = field(default_factory=dict)
    read_counts: dict[str, int] = field(default_factory=dict)


def _bind_call_arguments(
    expression: SurfaceCall,
    parameters: tuple[str, ...],
    label: str,
) -> tuple[tuple[str, SurfaceCallArgument], ...]:
    parameter_names = set(parameters)
    assigned: dict[str, SurfaceCallArgument] = {}
    next_positional = 0
    keyword_seen = False
    for argument in expression.arguments:
        if argument.name is None:
            if keyword_seen:
                raise SurfaceElaborationError(
                    f"PositionalAfterKeyword: {label}"
                )
            if next_positional >= len(parameters):
                raise SurfaceElaborationError(f"ArityMismatch: {label}")
            parameter_name = parameters[next_positional]
            next_positional += 1
        else:
            keyword_seen = True
            parameter_name = argument.name
            if parameter_name not in parameter_names:
                raise SurfaceElaborationError(
                    f"UnknownArgument: {label}.{parameter_name}"
                )
        if parameter_name in assigned:
            raise SurfaceElaborationError(
                f"DuplicateArgument: {label}.{parameter_name}"
            )
        assigned[parameter_name] = argument
    missing = tuple(name for name in parameters if name not in assigned)
    if missing:
        raise SurfaceElaborationError(
            f"MissingArgument: {label}: {', '.join(missing)}"
        )
    return tuple((name, assigned[name]) for name in parameters)


class _Elaborator:
    def __init__(self, program: SurfaceProgram) -> None:
        self.program = program
        self.types = _Types()
        self.records = {
            declaration.name: declaration
            for declaration in program.declarations
            if isinstance(declaration, SurfaceRecord)
        }
        self.enums = {
            declaration.name: declaration
            for declaration in program.declarations
            if isinstance(declaration, SurfaceEnum)
        }
        self.functions: dict[str, _Function] = {}
        for declaration in program.declarations:
            if not isinstance(declaration, SurfaceFunction):
                continue
            if declaration.name in self.functions:
                raise SurfaceElaborationError(f"DuplicateFunction: {declaration.name}")
            parameters = {
                item.name: self.types.variable(
                    f"function:{declaration.name}:parameter:{item.name}"
                )
                for item in declaration.parameters
            }
            function = _Function(
                declaration,
                parameters,
                self.types.variable(f"function:{declaration.name}:return"),
                {},
                {},
                {},
                {},
            )
            self.functions[declaration.name] = function
            for parameter in declaration.parameters:
                if parameter.type_name:
                    self.types.unify(
                        parameters[parameter.name],
                        self.types.typed(parameter.type_name),
                        context=f"{declaration.name}.{parameter.name}",
                    )
                    self._note(function, parameter.name, "explicit_boundary")
            if declaration.return_type:
                result_parts = _generic_parts(declaration.return_type, "Result")
                return_type = (
                    result_parts[0]
                    if result_parts and len(result_parts) == 2
                    else declaration.return_type
                )
                self.types.unify(
                    function.return_term,
                    self.types.typed(return_type),
                    context=f"{declaration.name} return",
                )
                self._note(function, "$return", "explicit_boundary")
                if result_parts and len(result_parts) == 2:
                    function.errors.add(result_parts[1])
        for function in self.functions.values():
            body = self._body(function.source)
            self._count(body, function)
        for _ in range(max(1, len(self.functions) * 4)):
            snapshot = self._snapshot()
            for function in self.functions.values():
                self._statements(self._body(function.source), function)
            if snapshot == self._snapshot():
                break
        for _ in range(max(1, len(self.functions) * 2)):
            changed = False
            for function in self.functions.values():
                effects = set(function.effects)
                capabilities = set(function.capabilities)
                errors = set(function.errors)
                for callee in function.calls:
                    effects.update(self.functions[callee].effects)
                    capabilities.update(self.functions[callee].capabilities)
                for callee in function.error_calls:
                    errors.update(self.functions[callee].errors)
                if (
                    effects != function.effects
                    or capabilities != function.capabilities
                    or errors != function.errors
                ):
                    function.effects = effects
                    function.capabilities = capabilities
                    function.errors = errors
                    changed = True
            if not changed:
                break
        for function in self.functions.values():
            for callee in function.error_calls:
                if not self.functions[callee].errors:
                    raise SurfaceElaborationError(
                        f"TryRequiresResult: {callee} has no error row"
                    )

    @staticmethod
    def _body(function: SurfaceFunction) -> tuple[SurfaceStatement, ...]:
        if function.body_kind == "expression":
            return (
                SurfaceExpressionStatement(function.body.span, function.body),  # type: ignore[union-attr]
            )
        return function.body  # type: ignore[return-value]

    def _snapshot(self) -> tuple[tuple[str, str, str | None], ...]:
        return tuple(
            sorted(
                (term, self.types.find(term), self.types.concrete.get(self.types.find(term)))
                for term in self.types.parent
            )
        )

    @staticmethod
    def _note(function: _Function, name: str, evidence: str) -> None:
        function.evidence.setdefault(name, set()).add(evidence)

    def _local(self, function: _Function, name: str) -> str:
        if name in function.parameters:
            return function.parameters[name]
        return function.locals.setdefault(
            name, self.types.variable(f"function:{function.source.name}:local:{name}")
        )

    def _lookup(self, function: _Function, name: str) -> str:
        if name in function.parameters:
            return function.parameters[name]
        if name in function.locals:
            return function.locals[name]
        raise SurfaceElaborationError(
            f"UnresolvedName: {function.source.name}.{name}"
        )

    def _count(self, statements: Iterable[SurfaceStatement], function: _Function) -> None:
        for statement in statements:
            for node in statement.walk():
                if isinstance(node, SurfaceName):
                    function.read_counts[node.name] = (
                        function.read_counts.get(node.name, 0) + 1
                    )
            if (
                isinstance(statement, SurfaceAssignment)
                and isinstance(statement.target, SurfaceName)
            ):
                function.read_counts[statement.target.name] -= 1
            if isinstance(statement, SurfaceBinding):
                function.assignments[statement.name] = function.assignments.get(statement.name, 0) + 1
                function.first_bindings.setdefault(statement.name, statement)
            elif isinstance(statement, SurfaceAssignment) and isinstance(statement.target, SurfaceName):
                function.assignments[statement.target.name] = function.assignments.get(statement.target.name, 0) + 1
            if isinstance(statement, (SurfaceFor, SurfaceWhile)):
                self._count(statement.body, function)
            elif isinstance(statement, SurfaceIf):
                self._count(statement.body, function)
                self._count(statement.otherwise, function)
            elif isinstance(statement, SurfaceMatch):
                for case in statement.cases:
                    self._count(case.body, function)

    def _member(self, expression: SurfaceMember, function: _Function, expected: str | None) -> str:
        if (
            isinstance(expression.receiver, SurfaceName)
            and expression.receiver.name in self.enums
        ):
            enum = self.enums[expression.receiver.name]
            variants = {item.name: item.type_name for item in enum.variants}
            if expression.field not in variants:
                raise SurfaceElaborationError(
                    f"UnknownVariant: {enum.name}.{expression.field}"
                )
            if variants[expression.field] is not None:
                raise SurfaceElaborationError(
                    f"VariantPayloadRequired: {enum.name}.{expression.field}"
                )
            term = self.types.typed(enum.name)
        else:
            receiver = self._expression(expression.receiver, function)
            receiver_type = self.types.concrete.get(self.types.find(receiver))
            field_tables = {
                name: {field.name: field.type_name for field in record.fields}
                for name, record in self.records.items()
            }
            candidates = [
                (name, fields[expression.field])
                for name, fields in field_tables.items()
                if expression.field in fields
            ]
            if receiver_type in self.records:
                field_type = field_tables[receiver_type][expression.field]
            elif len(candidates) == 1:
                owner_type, field_type = candidates[0]
                self.types.unify(receiver, self.types.typed(owner_type), context=f"field {expression.field}")
            else:
                raise SurfaceElaborationError(
                    f"AmbiguousField: {function.source.name}.{expression.field}"
                )
            term = self.types.typed(field_type)
        if expected:
            self.types.unify(term, self.types.typed(expected), context=f"field {expression.field}")
        return term

    def _implicit_owner(self, expression: SurfaceExpression) -> str | None:
        receivers = [
            item
            for item in expression.walk()
            if isinstance(item, SurfaceImplicitReceiver)
        ]
        if len(receivers) != 1:
            return None
        field_name = receivers[0].field
        candidates = [
            name
            for name, record in self.records.items()
            if field_name in {field.name for field in record.fields}
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _implicit_expression(
        self,
        expression: SurfaceExpression,
        function: _Function,
        element_type: str,
        expected: str | None = None,
    ) -> str:
        if isinstance(expression, SurfaceImplicitReceiver):
            record = self.records.get(element_type)
            fields = (
                {field.name: field.type_name for field in record.fields}
                if record is not None
                else {}
            )
            if expression.field not in fields:
                raise SurfaceElaborationError(
                    f"UnknownImplicitField: {element_type}.{expression.field}"
                )
            term = self.types.typed(fields[expression.field])
        elif isinstance(expression, SurfaceLiteral):
            term = self.types.typed(expression.kind)
        elif isinstance(expression, SurfaceUnary):
            required = "Bool" if expression.operator == "not" else expected
            term = self._implicit_expression(
                expression.operand, function, element_type, required
            )
        elif isinstance(expression, SurfaceBinary):
            if expression.operator in {"==", "!=", "<", "<=", ">", ">="}:
                left = self._implicit_expression(
                    expression.left, function, element_type
                )
                left_type = self.types.concrete.get(self.types.find(left))
                right = self._implicit_expression(
                    expression.right, function, element_type, left_type
                )
                self.types.unify(left, right, context="implicit comparison")
                term = self.types.typed("Bool")
            elif expression.operator in {"and", "or"}:
                left = self._implicit_expression(
                    expression.left, function, element_type, "Bool"
                )
                right = self._implicit_expression(
                    expression.right, function, element_type, "Bool"
                )
                self.types.unify(left, right, context="implicit boolean")
                term = self.types.typed("Bool")
            else:
                left = self._implicit_expression(
                    expression.left, function, element_type, expected
                )
                left_type = self.types.concrete.get(self.types.find(left))
                right = self._implicit_expression(
                    expression.right, function, element_type, left_type
                )
                self.types.unify(left, right, context="implicit arithmetic")
                term = left
        elif isinstance(expression, SurfaceName):
            raise SurfaceElaborationError(
                f"ImplicitCaptureForbidden: {expression.name}"
            )
        else:
            raise SurfaceElaborationError(
                f"NestedImplicitReceiverForbidden: {type(expression).__name__}"
            )
        if expected:
            self.types.unify(
                term,
                self.types.typed(expected),
                context="implicit callable result",
            )
        return term

    def _collection_call(
        self,
        expression: SurfaceCall,
        function: _Function,
        expected: str | None,
    ) -> str:
        assert isinstance(expression.callee, SurfaceMember)
        operation = expression.callee.field
        if len(expression.arguments) != 1:
            raise SurfaceElaborationError(f"ArityMismatch: collection {operation}")
        argument = expression.arguments[0].value
        receiver = self._expression(expression.callee.receiver, function)
        receiver_type = self.types.concrete.get(self.types.find(receiver))
        if receiver_type is None:
            owner = self._implicit_owner(argument)
            if owner is None:
                raise SurfaceElaborationError(
                    f"UnknownCollectionElementType: {operation}"
                )
            receiver_type = f"Vec[{owner}]"
            self.types.unify(
                receiver,
                self.types.typed(receiver_type),
                context=f"collection {operation} receiver",
            )
        parts = _generic_parts(receiver_type, "Vec")
        if parts is None or len(parts) != 1:
            raise SurfaceElaborationError(
                f"CollectionReceiverRequired: {receiver_type}"
            )
        element_type = parts[0]
        callable_expected = "Bool" if operation in {"where", "count"} else None
        callable_term = self._implicit_expression(
            argument,
            function,
            element_type,
            callable_expected,
        )
        callable_return = self.types.resolve(
            callable_term,
            name=f"{function.source.name}.{operation} callable",
        )
        digest_input = (
            f"{function.source.name}\0{expression.span.path}\0"
            f"{expression.span.start_line}\0{expression.span.start_column}\0"
            f"{operation}\0{element_type}"
        )
        callable_id = hashlib.sha256(digest_input.encode()).hexdigest()[:16]
        function.implicit_callables[callable_id] = CanonicalCallable(
            callable_id,
            "__item",
            element_type,
            callable_return,
            _emit_implicit_expression(argument),
            argument.span,
        )
        if operation == "map":
            term = self.types.typed(f"Vec[{callable_return}]")
        elif operation == "where":
            term = self.types.typed(receiver_type)
        else:
            term = self.types.typed("UInt64")
        if expected:
            self.types.unify(
                term,
                self.types.typed(expected),
                context=f"collection {operation} result",
            )
        return term
    def _expression(
        self,
        expression: SurfaceExpression,
        function: _Function,
        expected: str | None = None,
    ) -> str:
        if isinstance(expression, SurfaceLiteral):
            if expression.kind == "None" and expected and expected.startswith("Option["):
                term = self.types.typed(expected)
            else:
                term = self.types.typed(expression.kind)
        elif isinstance(expression, SurfaceName):
            term = self._lookup(function, expression.name)
        elif isinstance(expression, SurfaceMember):
            term = self._member(expression, function, expected)
            expected = None
        elif isinstance(expression, SurfaceUnary):
            required = "Bool" if expression.operator == "not" else expected
            term = self._expression(expression.operand, function, required)
        elif isinstance(expression, SurfaceBinary):
            if expression.operator in {"==", "!=", "<", "<=", ">", ">="}:
                left = self._expression(expression.left, function)
                right = self._expression(expression.right, function)
                self.types.unify(left, right, context="comparison")
                term = self.types.typed("Bool")
            elif expression.operator == "and":
                left = self._expression(expression.left, function, "Bool")
                right = self._expression(expression.right, function, "Bool")
                self.types.unify(left, right, context="boolean operator")
                term = self.types.typed("Bool")
            elif expression.operator == "or":
                left = self._expression(expression.left, function)
                left_type = self.types.concrete.get(self.types.find(left))
                option_parts = (
                    _generic_parts(left_type, "Option")
                    if left_type is not None
                    else None
                )
                if left_type == "Bool":
                    right = self._expression(expression.right, function, "Bool")
                    self.types.unify(left, right, context="boolean operator")
                    term = self.types.typed("Bool")
                elif option_parts and len(option_parts) == 1:
                    inner_type = option_parts[0]
                    right = self._expression(
                        expression.right, function, inner_type
                    )
                    self.types.unify(
                        right,
                        self.types.typed(inner_type),
                        context="option fallback",
                    )
                    term = self.types.typed(inner_type)
                    key = (
                        f"{expression.span.path}:{expression.span.start_line}:"
                        f"{expression.span.start_column}"
                    )
                    function.option_fallbacks[key] = CanonicalOptionFallback(
                        str(_emit_expression(expression.left)),
                        str(_emit_expression(expression.right)),
                        inner_type,
                        expression.span,
                    )
                else:
                    raise SurfaceElaborationError(
                        f"TruthinessForbidden: {left_type or 'unresolved'}"
                    )
            else:
                left = self._expression(expression.left, function)
                left_type = self.types.concrete.get(self.types.find(left))
                numeric = {"Byte", "UInt64", "Int64", "Float32", "Float64"}
                if left_type is not None and left_type not in numeric:
                    raise SurfaceElaborationError(
                        "numeric operator requires numeric operands, "
                        f"got {left_type}"
                    )
                operand_type = expected or left_type
                right = self._expression(
                    expression.right, function, operand_type
                )
                right_type = self.types.concrete.get(self.types.find(right))
                if right_type is not None and right_type not in numeric:
                    raise SurfaceElaborationError(
                        "numeric operator requires numeric operands, "
                        f"got {right_type}"
                    )
                self.types.unify(left, right, context="numeric operator")
                if expected:
                    self.types.unify(
                        left,
                        self.types.typed(expected),
                        context="numeric result",
                    )
                term = left
        elif isinstance(expression, SurfaceCall):
            if isinstance(expression.callee, SurfaceName):
                name = expression.callee.name
                if name == "sqrt":
                    for argument in expression.arguments:
                        self._expression(argument.value, function, "Float64")
                    term = self.types.typed("Float64")
                elif name in {"min", "max"}:
                    if not expression.arguments:
                        raise SurfaceElaborationError(f"ArityMismatch: {name}")
                    first = self._expression(expression.arguments[0].value, function, expected)
                    for argument in expression.arguments[1:]:
                        self.types.unify(first, self._expression(argument.value, function, expected), context=name)
                    term = first
                elif name in self.records:
                    record = self.records[name]
                    field_types = {
                        field.name: field.type_name
                        for field in record.fields
                    }
                    bound = _bind_call_arguments(
                        expression,
                        tuple(field_types),
                        name,
                    )
                    for field_name, argument in bound:
                        self._expression(
                            argument.value,
                            function,
                            field_types[field_name],
                        )
                    term = self.types.typed(name)
                elif name in {"Ok", "Err", "Some", "None"}:
                    constructor_type = expected
                    declared_type = function.source.return_type
                    if (
                        name in {"Ok", "Err"}
                        and declared_type
                        and _generic_parts(declared_type, "Result")
                    ):
                        constructor_type = declared_type
                    if constructor_type is None:
                        raise SurfaceElaborationError(
                            f"AmbiguousConstructor: {name}"
                        )
                    result_parts = _generic_parts(constructor_type, "Result")
                    option_parts = _generic_parts(constructor_type, "Option")
                    if name in {"Ok", "Err"} and result_parts and len(result_parts) == 2:
                        argument_type = result_parts[0 if name == "Ok" else 1]
                        produced_type = result_parts[0]
                    elif name == "Some" and option_parts and len(option_parts) == 1:
                        argument_type = option_parts[0]
                        produced_type = constructor_type
                    elif name == "None" and option_parts and len(option_parts) == 1:
                        argument_type = None
                        produced_type = constructor_type
                    else:
                        raise SurfaceElaborationError(
                            f"ConstructorTypeMismatch: {name} for {constructor_type}"
                        )
                    bound = _bind_call_arguments(
                        expression,
                        () if name == "None" else ("value",),
                        name,
                    )
                    if argument_type is not None:
                        self._expression(
                            bound[0][1].value,
                            function,
                            argument_type,
                        )
                    term = self.types.typed(produced_type)
                elif name in self.functions:
                    target = self.functions[name]
                    function.calls.add(name)
                    bound = _bind_call_arguments(
                        expression,
                        tuple(target.parameters),
                        name,
                    )
                    for parameter_name, argument in bound:
                        self.types.unify(
                            self._expression(argument.value, function),
                            target.parameters[parameter_name],
                            context=f"call {name}.{parameter_name}",
                        )
                    term = target.return_term
                else:
                    raise SurfaceElaborationError(f"UnknownCall: {name}")
            elif isinstance(expression.callee, SurfaceMember):
                receiver_expression = expression.callee.receiver
                method = expression.callee.field
                if (
                    isinstance(receiver_expression, SurfaceName)
                    and receiver_expression.name in self.enums
                ):
                    enum = self.enums[receiver_expression.name]
                    variants = {
                        item.name: item.type_name for item in enum.variants
                    }
                    if method not in variants:
                        raise SurfaceElaborationError(
                            f"UnknownVariant: {enum.name}.{method}"
                        )
                    payload_type = variants[method]
                    if payload_type is None or len(expression.arguments) != 1:
                        raise SurfaceElaborationError(
                            f"VariantPayloadMismatch: {enum.name}.{method}"
                        )
                    self._expression(
                        expression.arguments[0].value,
                        function,
                        payload_type,
                    )
                    term = self.types.typed(enum.name)
                elif (
                    isinstance(receiver_expression, SurfaceName)
                    and receiver_expression.name == "Vec"
                    and method == "new"
                ):
                    if expression.arguments:
                        raise SurfaceElaborationError("ArityMismatch: Vec.new")
                    term = self.types.variable(
                        f"vec-new:{expression.span.path}:"
                        f"{expression.span.start_line}:"
                        f"{expression.span.start_column}"
                    )
                    if expected and expected.startswith("Vec["):
                        self.types.unify(
                            term,
                            self.types.typed(expected),
                            context="Vec.new expected type",
                        )
                elif (
                    isinstance(receiver_expression, SurfaceName)
                    and receiver_expression.name == "Map"
                    and method == "new"
                ):
                    if expression.arguments:
                        raise SurfaceElaborationError("ArityMismatch: Map.new")
                    term = self.types.typed("Map[Text,UInt64]")
                elif method in {"where", "map", "count"}:
                    term = self._collection_call(expression, function, expected)
                    expected = None
                elif method == "clone":
                    if expression.arguments:
                        raise SurfaceElaborationError("ArityMismatch: clone")
                    term = self._expression(receiver_expression, function)
                elif method == "len":
                    if expression.arguments:
                        raise SurfaceElaborationError("ArityMismatch: len")
                    self._expression(receiver_expression, function)
                    term = self.types.typed("UInt64")
                elif method == "push":
                    if len(expression.arguments) != 1:
                        raise SurfaceElaborationError("ArityMismatch: push")
                    receiver = self._expression(receiver_expression, function)
                    receiver_type = self.types.concrete.get(
                        self.types.find(receiver)
                    )
                    value = self._expression(
                        expression.arguments[0].value, function
                    )
                    value_type = self.types.concrete.get(self.types.find(value))
                    if receiver_type is None and value_type is not None:
                        self.types.unify(
                            receiver,
                            self.types.typed(f"Vec[{value_type}]"),
                            context="Vec.push receiver",
                        )
                    elif not receiver_type or not receiver_type.startswith("Vec["):
                        raise SurfaceElaborationError(
                            "CollectionReceiverRequired: push"
                        )
                    else:
                        self.types.unify(
                            value,
                            self.types.typed(_generic_parts(receiver_type, "Vec")[0]),  # type: ignore[index]
                            context="Vec.push value",
                        )
                    term = self.types.typed("Unit")
                elif method in {"increment", "get"}:
                    if len(expression.arguments) != 1:
                        raise SurfaceElaborationError(
                            f"ArityMismatch: {method}"
                        )
                    receiver = self._expression(
                        receiver_expression,
                        function,
                        "Map[Text,UInt64]",
                    )
                    self.types.unify(
                        receiver,
                        self.types.typed("Map[Text,UInt64]"),
                        context=f"Map.{method} receiver",
                    )
                    self._expression(
                        expression.arguments[0].value,
                        function,
                        "Text",
                    )
                    term = self.types.typed(
                        "UInt64" if method == "get" else "Unit"
                    )
                elif (
                    isinstance(receiver_expression, SurfaceName)
                    and receiver_expression.name == "Text"
                    and method == "from_bytes"
                ):
                    for argument in expression.arguments:
                        self._expression(argument.value, function)
                    term = self.types.typed("Text")
                else:
                    if not isinstance(receiver_expression, SurfaceName):
                        raise SurfaceElaborationError(
                            "UnsupportedCall: member call"
                        )
                    name = f"{receiver_expression.name}.{method}"
                    signature = _HOST_CALLS.get(name)
                    if signature is None:
                        raise SurfaceElaborationError(f"UnknownCall: {name}")
                    parameters, return_type, effect, capability = signature
                    if len(expression.arguments) != len(parameters):
                        raise SurfaceElaborationError(f"ArityMismatch: {name}")
                    for argument, parameter_type in zip(
                        expression.arguments, parameters, strict=True
                    ):
                        self._expression(argument.value, function, parameter_type)
                    if function.source.declared_kind == "fn":
                        raise SurfaceElaborationError(
                            f"EffectInPureFunction: {function.source.name}"
                        )
                    function.effects.add(effect)
                    function.capabilities.add(capability)
                    term = self.types.typed(
                        expected
                        if (
                            name == "network.tcp_connect"
                            and expected
                            and expected.startswith("Result[TcpStream,")
                        )
                        else contextual_result_type(return_type, expected)
                    )
            else:
                raise SurfaceElaborationError("UnsupportedCall")
        elif isinstance(expression, SurfaceIndex):
            self._expression(expression.index, function, "UInt64")
            owner = self._expression(expression.receiver, function)
            owner_type = self.types.concrete.get(self.types.find(owner))
            if not owner_type or not owner_type.startswith("Vec["):
                raise SurfaceElaborationError("IndexRequiresCollection")
            element_parts = _generic_parts(owner_type, "Vec")
            if element_parts is None:
                raise SurfaceElaborationError("IndexRequiresCollection")
            term = self.types.typed(element_parts[0])
        elif isinstance(expression, SurfaceList):
            element = self.types.variable(f"list:{expression.span.start_line}:{expression.span.start_column}")
            for item in expression.items:
                self.types.unify(element, self._expression(item, function), context="list element")
            expected_parts = _generic_parts(expected, "Vec") if expected else None
            if expected_parts is not None:
                self.types.unify(element, self.types.typed(expected_parts[0]), context="list expected type")
                term = self.types.typed(expected)
            else:
                element_type = self.types.concrete.get(self.types.find(element))
                term = self.types.typed(f"Vec[{element_type}]") if element_type else self.types.variable(f"vec:{expression.span.start_line}")
        elif isinstance(expression, SurfaceTry):
            inner_call = expression.expression
            if (
                isinstance(inner_call, SurfaceCall)
                and isinstance(inner_call.callee, SurfaceName)
                and inner_call.callee.name in self.functions
            ):
                term = self._expression(inner_call, function)
                function.error_calls.add(inner_call.callee.name)
            else:
                inner = self._expression(inner_call, function)
                inner_type = self.types.concrete.get(self.types.find(inner))
                result_parts = (
                    _generic_parts(inner_type, "Result")
                    if inner_type is not None
                    else None
                )
                if inner_type is not None and (
                    result_parts is None or len(result_parts) != 2
                ):
                    raise SurfaceElaborationError(
                        f"TryRequiresResult: found {inner_type}"
                    )
                term = self.types.variable(
                    f"try:{expression.span.path}:{expression.span.start_line}:"
                    f"{expression.span.start_column}"
                )
                if result_parts:
                    self.types.unify(
                        term,
                        self.types.typed(result_parts[0]),
                        context="postfix try",
                    )
                    function.errors.add(result_parts[1])
        else:
            raise SurfaceElaborationError(f"UnsupportedExpression: {type(expression).__name__}")
        if expected:
            expected_term = self.types.typed(expected)
            actual_type = self.types.concrete.get(self.types.find(term))
            if (actual_type, expected) in {
                ("Text", "TextView"),
                ("Bytes", "BytesView"),
            }:
                return expected_term
            self.types.unify(term, expected_term, context="expected expression type")
        return term
    def _statements(
        self,
        statements: tuple[SurfaceStatement, ...],
        function: _Function,
        *,
        loop_depth: int = 0,
    ) -> None:
        for index, statement in enumerate(statements):
            if isinstance(statement, SurfaceBinding):
                if (
                    statement.name not in function.locals
                    and statement.name not in function.parameters
                    and function.read_counts.get(statement.name, 0) == 0
                ):
                    referenced = {
                        item.name
                        for item in statement.value.walk()
                        if isinstance(item, SurfaceName)
                    }
                    similar = next(
                        (
                            name
                            for name in function.locals
                            if name in referenced
                            and _edit_distance_one(name, statement.name)
                        ),
                        None,
                    )
                    if similar is not None:
                        raise SurfaceElaborationError(
                            f"PossibleTypoSymbol: {statement.name}; "
                            f"did you mean {similar}?"
                        )
                term = self._local(function, statement.name)
                value = self._expression(statement.value, function, statement.type_name)
                self.types.unify(term, value, context=f"assignment {statement.name}")
                self._note(function, statement.name, "assignment_value")
            elif isinstance(statement, SurfaceAssignment):
                if not isinstance(statement.target, SurfaceName):
                    raise SurfaceElaborationError("UnsupportedAssignmentTarget")
                target = self._lookup(function, statement.target.name)
                value = self._expression(statement.value, function)
                self.types.unify(target, value, context=f"mutation {statement.target.name}")
                self._note(function, statement.target.name, "mutated")
            elif isinstance(statement, SurfaceReturn):
                if statement.expression is None:
                    actual = self.types.typed("Unit")
                else:
                    expected = self.types.concrete.get(
                        self.types.find(function.return_term)
                    )
                    actual = self._expression(
                        statement.expression, function, expected
                    )
                self.types.unify(function.return_term, actual, context=f"{function.source.name} return")
                self._note(function, "$return", "explicit_return")
            elif isinstance(statement, SurfaceBreak):
                if loop_depth == 0:
                    raise SurfaceElaborationError("BreakOutsideLoop")
            elif isinstance(statement, SurfaceContinue):
                if loop_depth == 0:
                    raise SurfaceElaborationError("ContinueOutsideLoop")
            elif isinstance(statement, SurfaceExpressionStatement):
                expected = (
                    self.types.concrete.get(self.types.find(function.return_term))
                    if index == len(statements) - 1
                    else None
                )
                actual = self._expression(
                    statement.expression, function, expected
                )
                if index == len(statements) - 1:
                    self.types.unify(function.return_term, actual, context=f"{function.source.name} tail")
                    self._note(function, "$return", "tail_expression")
            elif isinstance(statement, SurfaceIf):
                self._expression(statement.condition, function, "Bool")
                self._statements(statement.body, function, loop_depth=loop_depth)
                self._statements(statement.otherwise, function, loop_depth=loop_depth)
            elif isinstance(statement, SurfaceWhile):
                self._expression(statement.condition, function, "Bool")
                self._statements(
                    statement.body,
                    function,
                    loop_depth=loop_depth + 1,
                )
            elif isinstance(statement, SurfaceFor):
                iterable = self._expression(statement.iterable, function)
                iterable_type = self.types.concrete.get(self.types.find(iterable))
                vec_parts = _generic_parts(iterable_type, "Vec")
                if vec_parts is not None:
                    item_type = vec_parts[0]
                else:
                    item_type = "UInt64"
                self.types.unify(self._local(function, statement.name), self.types.typed(item_type), context="for item")
                self._statements(
                    statement.body,
                    function,
                    loop_depth=loop_depth + 1,
                )
            elif isinstance(statement, SurfacePrint):
                self._expression(statement.expression, function)
                function.effects.add("console.write")
                function.capabilities.add("console.write")
            elif isinstance(statement, SurfaceMatch):
                matched = self._expression(statement.expression, function)
                matched_type = self.types.concrete.get(
                    self.types.find(matched)
                )
                option_parts = (
                    _generic_parts(matched_type, "Option")
                    if matched_type is not None
                    else None
                )
                result_parts = (
                    _generic_parts(matched_type, "Result")
                    if matched_type is not None
                    else None
                )
                if option_parts and len(option_parts) == 1:
                    variants = {"None": None, "Some": option_parts[0]}
                elif result_parts and len(result_parts) == 2:
                    variants = {
                        "Ok": result_parts[0],
                        "Err": result_parts[1],
                    }
                elif matched_type in self.enums:
                    variants = {
                        item.name: item.type_name
                        for item in self.enums[matched_type].variants
                    }
                else:
                    raise SurfaceElaborationError(
                        f"MatchRequiresClosedSum: {matched_type or 'unresolved'}"
                    )
                observed: set[str] = set()
                for case in statement.cases:
                    raw = case.pattern
                    pattern = raw.rsplit(".", 1)[-1]
                    variant_match = __import__("re").fullmatch(
                        r"([A-Za-z_]\w*)(?:\(([A-Za-z_]\w*)\))?",
                        pattern,
                    )
                    if variant_match is None:
                        raise SurfaceElaborationError(
                            f"InvalidPattern: {raw}"
                        )
                    variant, binding = variant_match.groups()
                    if variant not in variants:
                        raise SurfaceElaborationError(
                            f"UnknownVariant: {raw}"
                        )
                    if variant in observed:
                        raise SurfaceElaborationError(
                            f"DuplicateCase: {raw}"
                        )
                    observed.add(variant)
                    payload_type = variants[variant]
                    if binding and payload_type is None:
                        raise SurfaceElaborationError(
                            f"PatternPayloadForbidden: {raw}"
                        )
                    if payload_type and not binding:
                        raise SurfaceElaborationError(
                            f"PatternPayloadRequired: {raw}"
                        )
                    if binding and payload_type:
                        self.types.unify(
                            self._local(function, binding),
                            self.types.typed(payload_type),
                            context=f"pattern {raw}",
                        )
                        self._note(function, binding, "pattern_payload")
                    self._statements(
                        case.body,
                        function,
                        loop_depth=loop_depth,
                    )
                missing = set(variants) - observed
                if missing:
                    raise SurfaceElaborationError(
                        f"NonExhaustiveMatch: missing {sorted(missing)}"
                    )
    def _canonical_block(
        self,
        statements: tuple[SurfaceStatement, ...],
        function: _Function,
        *,
        depth: int,
        tail_returns: bool,
    ) -> tuple[str, ...]:
        lines = self._canonical_lines(
            statements,
            function,
            depth=depth,
            tail_returns=tail_returns,
        )
        return lines or (("    " * depth) + "pass",)

    def _canonical_lines(
        self,
        statements: tuple[SurfaceStatement, ...],
        function: _Function,
        *,
        depth: int = 0,
        tail_returns: bool = True,
    ) -> tuple[str, ...]:
        lines: list[str] = []
        prefix = "    " * depth
        for index, statement in enumerate(statements):
            if isinstance(statement, SurfaceBinding):
                type_name = self.types.resolve(
                    function.locals[statement.name],
                    name=f"{function.source.name}.{statement.name}",
                )
                keyword = (
                    "var"
                    if function.assignments.get(statement.name, 0) > 1
                    else "let"
                )
                lines.append(
                    f"{prefix}{keyword} {statement.name}: {type_name} = "
                    f"{_emit_expression(statement.value)}"
                )
            elif isinstance(statement, SurfaceAssignment):
                lines.append(
                    f"{prefix}{_emit_expression(statement.target)} "
                    f"{statement.operator} {_emit_expression(statement.value)}"
                )
            elif isinstance(statement, SurfaceReturn):
                suffix = (
                    f" {_emit_expression(statement.expression)}"
                    if statement.expression is not None
                    else ""
                )
                lines.append(f"{prefix}return{suffix}")
            elif isinstance(statement, SurfaceBreak):
                lines.append(f"{prefix}break")
            elif isinstance(statement, SurfaceContinue):
                lines.append(f"{prefix}continue")
            elif isinstance(statement, SurfacePass):
                lines.append(f"{prefix}pass")
            elif isinstance(statement, SurfaceExpressionStatement):
                keyword = (
                    "return "
                    if tail_returns and index == len(statements) - 1
                    else ""
                )
                lines.append(
                    f"{prefix}{keyword}{_emit_expression(statement.expression)}"
                )
            elif isinstance(statement, SurfacePrint):
                lines.append(
                    f"{prefix}console.write("
                    f"{_emit_expression(statement.expression)})"
                )
            elif isinstance(statement, SurfaceIf):
                lines.append(
                    f"{prefix}if {_emit_expression(statement.condition)}:"
                )
                lines.extend(
                    self._canonical_block(
                        statement.body,
                        function,
                        depth=depth + 1,
                        tail_returns=False,
                    )
                )
                if statement.otherwise:
                    lines.append(f"{prefix}else:")
                    lines.extend(
                        self._canonical_block(
                            statement.otherwise,
                            function,
                            depth=depth + 1,
                            tail_returns=False,
                        )
                    )
            elif isinstance(statement, SurfaceWhile):
                lines.append(
                    f"{prefix}while {_emit_expression(statement.condition)}:"
                )
                lines.extend(
                    self._canonical_block(
                        statement.body,
                        function,
                        depth=depth + 1,
                        tail_returns=False,
                    )
                )
            elif isinstance(statement, SurfaceFor):
                lines.append(
                    f"{prefix}for {statement.name} in "
                    f"{_emit_expression(statement.iterable)}:"
                )
                lines.extend(
                    self._canonical_block(
                        statement.body,
                        function,
                        depth=depth + 1,
                        tail_returns=False,
                    )
                )
            elif isinstance(statement, SurfaceMatch):
                lines.append(
                    f"{prefix}match {_emit_expression(statement.expression)}:"
                )
                for case in statement.cases:
                    lines.append(f"{prefix}    case {case.pattern}:")
                    lines.extend(
                        self._canonical_block(
                            case.body,
                            function,
                            depth=depth + 2,
                            tail_returns=False,
                        )
                    )
        return tuple(lines)
    def result(self) -> SurfaceElaboration:
        records = tuple(
            CanonicalRecord(
                record.name,
                tuple((field.name, field.type_name) for field in record.fields),
                record.span,
                record.exported,
            )
            for record in self.records.values()
        )
        enums = tuple(
            CanonicalEnum(
                enum.name,
                tuple(
                    (variant.name, variant.type_name)
                    for variant in enum.variants
                ),
                enum.span,
                enum.exported,
            )
            for enum in self.enums.values()
        )
        functions = []
        decisions = []
        for function in self.functions.values():
            def reaches(
                current: str,
                target: str,
                seen: frozenset[str] = frozenset(),
            ) -> bool:
                if current in seen:
                    return False
                return any(
                    callee == target
                    or reaches(callee, target, seen | {current})
                    for callee in self.functions[current].calls
                )

            unresolved_boundary = any(
                self.types.concrete.get(self.types.find(term)) is None
                for term in (
                    *function.parameters.values(),
                    function.return_term,
                )
            )
            if unresolved_boundary and reaches(
                function.source.name,
                function.source.name,
            ):
                raise SurfaceElaborationError(
                    "RecursiveBoundaryAnnotationRequired: "
                    f"{function.source.name}"
                )
            if function.source.exported and unresolved_boundary:
                raise SurfaceElaborationError(
                    "PublicBoundaryAnnotationRequired: "
                    f"{function.source.name}"
                )
            parameter_types = tuple(
                (
                    name,
                    self.types.resolve(term, name=f"{function.source.name}.{name}"),
                )
                for name, term in function.parameters.items()
            )
            return_type = self.types.resolve(
                function.return_term, name=f"{function.source.name}.$return"
            )
            source_result = (
                _generic_parts(function.source.return_type, "Result")
                if function.source.return_type
                else None
            )
            canonical_return_type = (
                function.source.return_type
                if source_result and len(source_result) == 2
                else return_type
            )
            body = []
            for name, binding in sorted(function.first_bindings.items(), key=lambda item: item[1].span.start_line):
                type_name = self.types.resolve(function.locals[name], name=f"{function.source.name}.{name}")
                body.append(
                    CanonicalBinding(
                        name,
                        type_name,
                        function.assignments.get(name, 0) > 1,
                        _emit_expression(binding.value),
                        binding.span,
                        "inferred_binding",
                    )
                )
                decisions.append(
                    InferenceDecision(
                        function.source.name,
                        name,
                        "local",
                        type_name,
                        function.assignments.get(name, 0) > 1,
                        tuple(sorted(function.evidence.get(name, ()))),
                    )
                )
            statements = self._body(function.source)
            tail = statements[-1]
            if isinstance(tail, SurfaceExpressionStatement):
                body.append(
                    CanonicalReturn(
                        _emit_expression(tail.expression),
                        tail.span,
                        "tail_expression",
                    )
                )
            elif isinstance(tail, SurfaceReturn):
                body.append(CanonicalReturn(_emit_expression(tail.expression) if tail.expression else None, tail.span))
            for name, type_name in parameter_types:
                decisions.append(
                    InferenceDecision(
                        function.source.name,
                        name,
                        "parameter",
                        type_name,
                        False,
                        tuple(sorted(function.evidence.get(name, {"call_or_use_constraint"}))),
                    )
                )
            decisions.append(
                InferenceDecision(
                    function.source.name,
                    "$return",
                    "return",
                    return_type,
                    False,
                    tuple(sorted(function.evidence.get("$return", {"body_constraint"}))),
                )
            )
            functions.append(
                CanonicalFunction(
                    function.source.name,
                    parameter_types,
                    canonical_return_type,
                    function.source.declared_kind
                    or ("task" if function.effects else "fn"),
                    tuple(sorted(function.effects)),
                    tuple(sorted(function.capabilities)),
                    tuple(sorted(function.errors)),
                    tuple(body),
                    function.source.span,
                    function.source.exported,
                    "inferred_declaration",
                    tuple(function.implicit_callables.values()),
                    tuple(function.option_fallbacks.values()),
                    self._canonical_lines(statements, function),
                )
            )
        canonical = CanonicalProgram(records, tuple(functions), enums)
        native_module, declaration_kinds, binding_kinds = _SurfaceNativeBuilder(
            self.program,
            canonical,
        ).build()
        canonical = replace(
            canonical,
            native_module=native_module,
            native_declaration_kinds=declaration_kinds,
            native_binding_kinds=binding_kinds,
            projection_source=self.program.source or None,
            source_path=self.program.span.path,
            source_sha256=hashlib.sha256(
                self.program.source.encode()
            ).hexdigest()
            if self.program.source
            else None,
        )
        return SurfaceElaboration(
            canonical,
            tuple(sorted(decisions, key=lambda item: (item.owner, item.kind, item.name))),
        )


def _emit_expression(expression: SurfaceExpression | None) -> str | None:
    if expression is None:
        return None
    if isinstance(expression, SurfaceName):
        return expression.name
    if isinstance(expression, SurfaceLiteral):
        if expression.kind == "Bool":
            return "true" if expression.value else "false"
        return repr(expression.value)
    if isinstance(expression, SurfaceImplicitReceiver):
        return f".{expression.field}"
    if isinstance(expression, SurfaceMember):
        return f"{_emit_expression(expression.receiver)}.{expression.field}"
    if isinstance(expression, SurfaceBinary):
        return f"{_emit_expression(expression.left)} {expression.operator} {_emit_expression(expression.right)}"
    if isinstance(expression, SurfaceUnary):
        return f"{expression.operator} {_emit_expression(expression.operand)}"
    if isinstance(expression, SurfaceCall):
        arguments = ", ".join(
            f"{item.name}: {_emit_expression(item.value)}" if item.name else str(_emit_expression(item.value))
            for item in expression.arguments
        )
        return f"{_emit_expression(expression.callee)}({arguments})"
    if isinstance(expression, SurfaceIndex):
        return f"{_emit_expression(expression.receiver)}[{_emit_expression(expression.index)}]"
    if isinstance(expression, SurfaceTry):
        return f"{_emit_expression(expression.expression)}?"
    if isinstance(expression, SurfaceList):
        return f"[{', '.join(str(_emit_expression(item)) for item in expression.items)}]"
    raise SurfaceElaborationError(f"CannotEmitExpression: {type(expression).__name__}")



def _emit_implicit_expression(expression: SurfaceExpression) -> str:
    if isinstance(expression, SurfaceImplicitReceiver):
        return f"__item.{expression.field}"
    if isinstance(expression, SurfaceLiteral):
        value = _emit_expression(expression)
        return str(value)
    if isinstance(expression, SurfaceBinary):
        return (
            f"{_emit_implicit_expression(expression.left)} "
            f"{expression.operator} "
            f"{_emit_implicit_expression(expression.right)}"
        )
    if isinstance(expression, SurfaceUnary):
        return (
            f"{expression.operator} "
            f"{_emit_implicit_expression(expression.operand)}"
        )
    raise SurfaceElaborationError(
        f"CannotEmitImplicitExpression: {type(expression).__name__}"
    )
class _SurfaceNativeBuilder:
    """Project typed Surface nodes into the production Python AST contract."""

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
        self.current_function = ""
        self.local_types: dict[str, str] = {}

    @staticmethod
    def _loc(node: ast.AST, span) -> ast.AST:
        node.lineno = span.start_line
        node.col_offset = max(span.start_column - 1, 0)
        node.end_lineno = span.end_line
        node.end_col_offset = max(span.end_column - 1, node.col_offset)
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
        payload = None
        if "(" in raw and raw.endswith(")"):
            raw, payload = raw[:-1].split("(", 1)
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
                    patterns=[self._loc(ast.MatchAs(name=payload), case.pattern_span or case.span)],
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
                patterns=[self._loc(ast.MatchAs(name=payload), case.pattern_span or case.span)],
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
        if isinstance(statement, SurfaceAssignment):
            target = self._expr(statement.target)
            if isinstance(target, ast.Name):
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
                    body=self._statements(statement.body),
                    orelse=self._statements(statement.otherwise),
                ),
                statement.span,
            )
        if isinstance(statement, SurfaceWhile):
            return self._loc(
                ast.While(
                    test=self._expr(statement.condition),
                    body=self._statements(statement.body),
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
                    body=self._statements(statement.body),
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
                                body=self._statements(case.body),
                            ),
                            case.span,
                        )
                        for case in statement.cases
                    ],
                ),
                statement.span,
            )
        raise SurfaceElaborationError(f"UnsupportedStatement: {type(statement).__name__}")

    def _statements(self, statements: tuple[SurfaceStatement, ...]) -> list[ast.stmt]:
        result: list[ast.stmt] = []
        for index, statement in enumerate(statements):
            result.append(
                self._statement(
                    statement,
                    tail=isinstance(statement, SurfaceExpressionStatement)
                    and index == len(statements) - 1,
                )
            )
        return result

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
        try:
            compile(module, self.program.span.path, "exec")
        except (TypeError, ValueError, SyntaxError) as error:
            raise SurfaceElaborationError(f"InvalidNativeAST: {error}") from error
        return module, tuple(declaration_kinds), tuple(sorted(binding_kinds.items()))


def elaborate_surface(program: SurfaceProgram) -> SurfaceElaboration:
    return _Elaborator(program).result()


__all__ = [
    "InferenceDecision",
    "SurfaceElaboration",
    "SurfaceElaborationError",
    "elaborate_surface",
]
