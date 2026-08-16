from __future__ import annotations
import hashlib

from dataclasses import replace
from typing import Iterable

from merlo.collection_protocol import (
    COLLECTION_OPERATIONS,
    collection_result_type,
    collection_shape,
)
from merlo.canonical_ast import (
    CanonicalBinding,
    CanonicalCallable,
    CanonicalCapture,
    CanonicalContract,
    CanonicalClosure,
    CanonicalFlow,
    CanonicalFlowStep,
    CanonicalParallel,
    CanonicalPolicy,
    CanonicalMachine,
    CanonicalMachineState,
    CanonicalTransition,
    CanonicalHole,
    CanonicalHoleBinding,
    CanonicalHoleCallable,
    CanonicalEnum,
    CanonicalFunction,
    CanonicalProgram,
    CanonicalRecord,
    CanonicalReturn,
    CanonicalOptionFallback,
)
from merlo.elaboration.calls import bind_call_arguments as _bind_call_arguments
from merlo.elaboration.native_lowering import surface_lowering_module
from merlo.elaboration.constraints import TypeConstraints as _Types
from merlo.elaboration.diagnostics import (
    SurfaceElaborationError,
    edit_distance_one as _edit_distance_one,
)
from merlo.elaboration.model import (
    FunctionState as _Function,
    InferenceDecision,
    SurfaceElaboration,
)
from merlo.surface_ast import (
    SurfaceAnnotation,
    SurfaceAssignment,
    SurfaceBinary,
    SurfaceBinding,
    SurfaceBreak,
    SurfaceCall,
    SurfaceComment,
    SurfaceContinue,
    SurfaceEnum,
    SurfaceEnsure,
    SurfaceExpression,
    SurfaceExpressionStatement,
    SurfaceFlow,
    SurfaceFlowStep,
    SurfaceFor,
    SurfaceFunction,
    SurfaceHole,
    SurfaceIf,
    SurfaceIndex,
    SurfaceList,
    SurfaceLambda,
    SurfaceLiteral,
    SurfaceMachine,
    SurfaceMatch,
    SurfaceMember,
    SurfaceName,
    SurfaceParallel,
    SurfaceParameter,
    SurfaceImplicitReceiver,
    SurfacePass,
    SurfacePrint,
    SurfaceRecord,
    SurfaceProgram,
    SurfaceRequire,
    SurfaceReturn,
    SurfaceStatement,
    SurfaceTry,
    SurfaceUnary,
    SurfaceUses,
    SurfaceWhile,
)
from merlo.type_parser import generic_parts
from merlo.type_properties import TypePropertyResolver
from merlo.intrinsics import (
    CONTRACT_GRAPH,
    INSTANCE_METHOD_NAMES,
    contextual_result_type,
)

_HOST_CALLS = {
    name: (signature.parameters, signature.result_type, signature.effect, signature.capability)
    for name, signature in CONTRACT_GRAPH.intrinsics.items()
}
_NUMERIC_TYPES = frozenset(
    {"Byte", "UInt64", "Int64", "Float32", "Float64"}
)
_INTEGER_TYPES = frozenset({"Byte", "UInt64", "Int64"})
_INTEGER_ONLY_OPERATORS = frozenset(
    {"//", "%", "|", "^", "&", "<<", ">>"}
)
_INTEGER_BOUNDS = {
    "Byte": (0, 255),
    "UInt64": (0, 18446744073709551615),
    "Int64": (-9223372036854775808, 9223372036854775807),
}



def _generic_parts(type_name: str, constructor: str) -> tuple[str, ...] | None:
    return generic_parts(type_name, constructor)

class _Elaborator:
    def __init__(self, program: SurfaceProgram) -> None:
        self._active_binding: str | None = None
        self._active_contract_result: _Function | None = None
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
        self.type_properties = TypePropertyResolver({**self.records, **self.enums})
        invariant_function_names = {
            f"__merlo_invariant_{record.name}_{index}"
            for record in self.records.values()
            for index, _ in enumerate(record.invariants)
        }
        self.functions: dict[str, _Function] = {}
        for declaration in program.declarations:
            if not isinstance(declaration, SurfaceFunction):
                continue
            if declaration.name in invariant_function_names:
                raise SurfaceElaborationError(
                    f"ReservedInvariantFunction: {declaration.name}"
                )
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
            if isinstance(declaration.body, tuple):
                for statement in declaration.body:
                    if isinstance(statement, SurfaceUses):
                        function.effects.update(statement.effects)
                        function.capabilities.update(statement.effects)
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
            self._validate_contract_layout(function)
            self._validate_function_flow(function)
            self._count(body, function)
        for _ in range(max(1, len(self.functions) * 4)):
            snapshot = self._snapshot()
            for function in self.functions.values():
                self._statements(self._body(function.source), function)
            if snapshot == self._snapshot():
                break
        for function in self.functions.values():
            self._validate_function_return(function)
            if any(
                isinstance(statement, SurfaceEnsure)
                and any(
                    isinstance(node, SurfaceName)
                    and node.name == "result"
                    for node in statement.condition.walk()
                )
                for statement in self._body(function.source)
            ) and self.types.resolve(
                function.return_term,
                name=f"{function.source.name}.return",
            ) == "Unit":
                raise SurfaceElaborationError(
                    "UnitEnsureResultForbidden: "
                    f"{function.source.name}"
                )
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
            for callee in function.collection_callbacks:
                if self.functions[callee].effects:
                    raise SurfaceElaborationError(
                        f"EffectInCollectionCallable: {callee}"
                    )
            for callee in function.contract_calls:
                if self.functions[callee].effects:
                    raise SurfaceElaborationError(
                        f"EffectInContract: {callee}"
                    )
        self._validate_record_invariants()

    def _validate_record_invariants(self) -> None:
        for record in self.records.values():
            for index, invariant in enumerate(record.invariants):
                if any(
                    isinstance(node, SurfaceHole)
                    for node in invariant.condition.walk()
                ):
                    raise SurfaceElaborationError(
                        "TypedHoleInInvariantForbidden: "
                        f"{record.name}"
                    )
                name = f"__merlo_invariant_{record.name}_{index}"
                parameters = tuple(
                    SurfaceParameter(
                        field.span,
                        field.name,
                        field.type_name,
                    )
                    for field in record.fields
                )
                source = SurfaceFunction(
                    name,
                    parameters,
                    invariant.condition,
                    "expression",
                    False,
                    invariant.span,
                    "fn",
                    "Bool",
                )
                state = _Function(
                    source,
                    {
                        field.name: self.types.typed(field.type_name)
                        for field in record.fields
                    },
                    self.types.typed("Bool"),
                    {},
                    {},
                    {},
                    {},
                )
                try:
                    self._expression(
                        invariant.condition,
                        state,
                        "Bool",
                    )
                except SurfaceElaborationError as exc:
                    if str(exc).startswith(
                        "EffectInPureFunction:"
                    ):
                        raise SurfaceElaborationError(
                            f"EffectInInvariant: {record.name}"
                        ) from exc
                    raise
                if state.effects or any(
                    self.functions[callee].effects
                    for callee in state.calls
                    if callee in self.functions
                ):
                    raise SurfaceElaborationError(
                        f"EffectInInvariant: {record.name}"
                    )

    @staticmethod
    def _body(function: SurfaceFunction) -> tuple[SurfaceStatement, ...]:
        if function.body_kind == "expression":
            return (
                SurfaceExpressionStatement(function.body.span, function.body),  # type: ignore[union-attr]
            )
        return function.body  # type: ignore[return-value]
    @staticmethod
    def _validate_contract_layout(function: _Function) -> None:
        body = _Elaborator._body(function.source)
        executable_seen = False
        for statement in body:
            if isinstance(statement, (SurfaceComment, SurfaceUses)):
                continue
            if isinstance(statement, (SurfaceRequire, SurfaceEnsure)):
                if executable_seen:
                    raise SurfaceElaborationError(
                        "ContractClauseAfterBody: "
                        f"{function.source.name}"
                    )
                if isinstance(statement, SurfaceRequire) and any(
                    isinstance(node, SurfaceName) and node.name == "result"
                    for node in statement.condition.walk()
                ):
                    raise SurfaceElaborationError(
                        "RequireResultForbidden: "
                        f"{function.source.name}"
                    )
                continue
            executable_seen = True
            if any(
                isinstance(node, (SurfaceRequire, SurfaceEnsure))
                for node in statement.walk()
                if node is not statement
            ):
                raise SurfaceElaborationError(
                    "NestedContractClauseForbidden: "
                    f"{function.source.name}"
                )
    @staticmethod
    def _literal_true(expression: SurfaceExpression) -> bool:
        return (
            isinstance(expression, SurfaceLiteral)
            and expression.kind == "Bool"
            and expression.value is True
        )

    @staticmethod
    def _pattern_binding(pattern: str) -> str | None:
        if "(" not in pattern or not pattern.endswith(")"):
            return None
        binding = pattern[:-1].rsplit("(", 1)[1]
        return binding if binding and binding != "_" else None

    def _match_exhaustive(self, statement: SurfaceMatch) -> bool:
        patterns = {case.pattern for case in statement.cases}
        if "_" in patterns:
            return True
        variants = {pattern.rsplit(".", 1)[-1].split("(", 1)[0] for pattern in patterns}
        if variants in ({"Ok", "Err"}, {"Some", "None"}):
            return True
        owners = {
            pattern.rsplit(".", 1)[0]
            for pattern in patterns
            if "." in pattern
        }
        if len(owners) != 1:
            return False
        enum = self.enums.get(next(iter(owners)))
        return enum is not None and variants == {
            item.name for item in enum.variants
        }

    @classmethod
    def _has_current_loop_break(
        cls,
        statements: tuple[SurfaceStatement, ...],
    ) -> bool:
        for statement in statements:
            if isinstance(statement, SurfaceBreak):
                return True
            if isinstance(statement, SurfaceIf):
                if cls._has_current_loop_break(
                    statement.body
                ) or cls._has_current_loop_break(statement.otherwise):
                    return True
            elif isinstance(statement, SurfaceMatch):
                if any(
                    cls._has_current_loop_break(case.body)
                    for case in statement.cases
                ):
                    return True
        return False

    def _block_terminates(
        self,
        statements: tuple[SurfaceStatement, ...],
        *,
        tail_position: bool = True,
    ) -> bool:
        executable = tuple(
            statement
            for statement in statements
            if not isinstance(statement, (SurfaceUses, SurfaceComment))
        )
        for index, statement in enumerate(executable):
            if isinstance(statement, SurfaceReturn):
                return True
            if (
                tail_position
                and index == len(executable) - 1
                and isinstance(statement, SurfaceExpressionStatement)
            ):
                return True
            if isinstance(statement, SurfaceIf):
                if (
                    statement.otherwise
                    and self._block_terminates(
                        statement.body,
                        tail_position=False,
                    )
                    and self._block_terminates(
                        statement.otherwise,
                        tail_position=False,
                    )
                ):
                    return True
            elif isinstance(statement, SurfaceMatch):
                if (
                    statement.cases
                    and self._match_exhaustive(statement)
                    and all(
                        self._block_terminates(
                            case.body,
                            tail_position=False,
                        )
                        for case in statement.cases
                    )
                ):
                    return True
            elif (
                isinstance(statement, SurfaceWhile)
                and self._literal_true(statement.condition)
                and not self._has_current_loop_break(statement.body)
            ):
                return True
        return False

    @staticmethod
    def _validate_expression_reads(
        expression: SurfaceExpression,
        assigned: set[str],
        declared: set[str],
    ) -> None:
        for item in expression.walk():
            if (
                isinstance(item, SurfaceName)
                and item.name in declared
                and item.name not in assigned
            ):
                raise SurfaceElaborationError(
                    f"UnresolvedName: {item.name}"
                )

    def _validate_block_flow(
        self,
        statements: tuple[SurfaceStatement, ...],
        assigned: set[str],
        declared: set[str],
    ) -> tuple[set[str], set[str], bool]:
        assigned = set(assigned)
        declared = set(declared)
        reachable = True
        for statement in statements:
            if not reachable:
                break
            if isinstance(
                statement,
                (SurfaceUses, SurfaceComment, SurfacePass),
            ):
                continue
            if isinstance(statement, (SurfaceRequire, SurfaceEnsure)):
                contract_assigned = set(assigned)
                contract_declared = set(declared)
                if isinstance(statement, SurfaceEnsure):
                    contract_assigned.add("result")
                    contract_declared.add("result")
                self._validate_expression_reads(
                    statement.condition,
                    contract_assigned,
                    contract_declared,
                )
                continue
            if isinstance(statement, SurfaceBinding):
                self._validate_expression_reads(
                    statement.value,
                    assigned,
                    declared,
                )
                declared.add(statement.name)
                assigned.add(statement.name)
            elif isinstance(statement, SurfaceAnnotation):
                declared.add(statement.name)
            elif isinstance(statement, SurfaceAssignment):
                if isinstance(statement.target, SurfaceName):
                    if (
                        statement.operator != "="
                        and statement.target.name not in assigned
                    ):
                        raise SurfaceElaborationError(
                            f"UnresolvedName: {statement.target.name}"
                        )
                    declared.add(statement.target.name)
                    assigned.add(statement.target.name)
                else:
                    self._validate_expression_reads(
                        statement.target,
                        assigned,
                        declared,
                    )
                self._validate_expression_reads(
                    statement.value,
                    assigned,
                    declared,
                )
            elif isinstance(statement, SurfaceExpressionStatement):
                self._validate_expression_reads(
                    statement.expression,
                    assigned,
                    declared,
                )
            elif isinstance(statement, SurfaceReturn):
                if statement.expression is not None:
                    self._validate_expression_reads(
                        statement.expression,
                        assigned,
                        declared,
                    )
                reachable = False
            elif isinstance(statement, SurfacePrint):
                self._validate_expression_reads(
                    statement.expression,
                    assigned,
                    declared,
                )
            elif isinstance(statement, (SurfaceBreak, SurfaceContinue)):
                reachable = False
            elif isinstance(statement, SurfaceIf):
                self._validate_expression_reads(
                    statement.condition,
                    assigned,
                    declared,
                )
                body_assigned, body_declared, body_reachable = (
                    self._validate_block_flow(
                        statement.body,
                        assigned,
                        declared,
                    )
                )
                else_assigned, else_declared, else_reachable = (
                    self._validate_block_flow(
                        statement.otherwise,
                        assigned,
                        declared,
                    )
                )
                declared |= body_declared | else_declared
                if self._literal_true(statement.condition):
                    assigned = body_assigned
                    reachable = body_reachable
                elif (
                    isinstance(statement.condition, SurfaceLiteral)
                    and statement.condition.kind == "Bool"
                    and statement.condition.value is False
                ):
                    assigned = else_assigned
                    reachable = else_reachable
                else:
                    assigned = body_assigned & else_assigned
                    reachable = body_reachable or else_reachable
            elif isinstance(statement, SurfaceWhile):
                self._validate_expression_reads(
                    statement.condition,
                    assigned,
                    declared,
                )
                _, body_declared, _ = self._validate_block_flow(
                    statement.body,
                    assigned,
                    declared,
                )
                declared |= body_declared
                if (
                    self._literal_true(statement.condition)
                    and not self._has_current_loop_break(statement.body)
                ):
                    reachable = False
            elif isinstance(statement, SurfaceFor):
                self._validate_expression_reads(
                    statement.iterable,
                    assigned,
                    declared,
                )
                loop_declared = declared | {statement.name}
                loop_assigned = assigned | {statement.name}
                _, body_declared, _ = self._validate_block_flow(
                    statement.body,
                    loop_assigned,
                    loop_declared,
                )
                declared |= body_declared | {statement.name}
            elif isinstance(statement, SurfaceMatch):
                self._validate_expression_reads(
                    statement.expression,
                    assigned,
                    declared,
                )
                case_states = []
                case_declared = set(declared)
                for case in statement.cases:
                    binding = self._pattern_binding(case.pattern)
                    incoming_assigned = set(assigned)
                    incoming_declared = set(declared)
                    if binding:
                        incoming_assigned.add(binding)
                        incoming_declared.add(binding)
                    case_assigned, declared_in_case, case_reachable = (
                        self._validate_block_flow(
                            case.body,
                            incoming_assigned,
                            incoming_declared,
                        )
                    )
                    case_states.append((case_assigned, case_reachable))
                    case_declared |= declared_in_case
                declared = case_declared
                if case_states and self._match_exhaustive(statement):
                    assigned = set.intersection(
                        *(state for state, _ in case_states)
                    )
                    reachable = any(
                        state_reachable
                        for _, state_reachable in case_states
                    )
        return assigned, declared, reachable

    def _validate_function_flow(self, function: _Function) -> None:
        parameters = set(function.parameters)
        self._validate_block_flow(
            self._body(function.source),
            parameters,
            parameters,
        )

    def _validate_function_return(self, function: _Function) -> None:
        declared_return = function.source.return_type
        result_parts = (
            _generic_parts(declared_return, "Result")
            if declared_return
            else None
        )
        value_return = (
            result_parts[0]
            if result_parts and len(result_parts) == 2
            else declared_return
        )
        body = self._body(function.source)
        if (
            function.source.body_kind == "block"
            and value_return not in {None, "Unit"}
            and not self._block_terminates(body)
        ):
            raise SurfaceElaborationError(
                f"MissingReturn: {function.source.name}"
            )

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
    def _local_visible(
        self,
        function: _Function,
        name: str,
        span,
    ) -> bool:
        if name in function.parameters:
            return True
        if name == self._active_binding:
            return False
        first = function.first_bindings.get(name)
        if first is None:
            return name in function.locals
        return (
            first.span.start_line,
            first.span.start_column,
        ) < (
            span.start_line,
            span.start_column,
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
            map_entry_parts = (
                _generic_parts(receiver_type, "MapEntry")
                if receiver_type
                else None
            )
            candidates = [
                (name, fields[expression.field])
                for name, fields in field_tables.items()
                if expression.field in fields
            ]
            if map_entry_parts and expression.field in {"key", "value"}:
                field_type = map_entry_parts[
                    0 if expression.field == "key" else 1
                ]
            elif receiver_type in self.records:
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
        shape = collection_shape(receiver_type)
        if shape is None:
            raise SurfaceElaborationError(
                f"CollectionReceiverRequired: {receiver_type}"
            )
        element_type = shape.element_type
        callable_expected = "Bool" if operation in {"where", "count"} else None
        callable_parameter = "__item"
        if isinstance(argument, SurfaceName) and argument.name in self.functions:
            target = self.functions[argument.name]
            if not self.type_properties.resolve(element_type).is_copy:
                raise SurfaceElaborationError(
                    "OwnedCollectionCallableRequiresImplicitExpression: "
                    f"{argument.name}"
                )
            if target.source.declared_kind == "task":
                raise SurfaceElaborationError(
                    f"EffectInCollectionCallable: {argument.name}"
                )
            if len(target.parameters) != 1:
                raise SurfaceElaborationError(
                    f"ArityMismatch: collection callable {argument.name}"
                )
            parameter_term = next(iter(target.parameters.values()))
            self.types.unify(
                parameter_term,
                self.types.typed(element_type),
                context=f"collection {operation} callable parameter",
            )
            callable_term = target.return_term
            callable_expression = f"{argument.name}(__item)"
            function.calls.add(argument.name)
            function.collection_callbacks.add(argument.name)
        else:
            callable_term = self._implicit_expression(
                argument,
                function,
                element_type,
                callable_expected,
            )
            callable_expression = _emit_implicit_expression(argument)
        callable_return = self.types.resolve(
            callable_term,
            name=f"{function.source.name}.{operation} callable",
        )
        if callable_expected is not None and callable_return != callable_expected:
            raise SurfaceElaborationError(
                f"CollectionCallableReturnMismatch: {operation} "
                f"requires {callable_expected}, got {callable_return}"
            )
        digest_input = (
            f"{function.source.name}\0{expression.span.path}\0"
            f"{expression.span.start_line}\0{expression.span.start_column}\0"
            f"{operation}\0{element_type}"
        )
        callable_id = hashlib.sha256(digest_input.encode()).hexdigest()[:16]
        function.implicit_callables[callable_id] = CanonicalCallable(
            callable_id,
            callable_parameter,
            element_type,
            callable_return,
            callable_expression,
            argument.span,
        )
        result_element = (
            callable_return if operation == "map" else element_type
        )
        term = self.types.typed(
            collection_result_type(operation, result_element)
        )
        if expected:
            self.types.unify(
                term,
                self.types.typed(expected),
                context=f"collection {operation} result",
            )
        return term
    def _lambda(
        self,
        expression: SurfaceLambda,
        function: _Function,
        expected: str | None,
    ) -> str:
        callback = _generic_parts(expected or "", "Fn")
        if callback is None or len(callback) != len(expression.parameters) + 1:
            raise SurfaceElaborationError(
                f"ClosureTypeAnnotationRequired: {function.source.name}"
            )
        parameter_types = callback[:-1]
        return_type = callback[-1]
        parameter_names = frozenset(expression.parameters)
        captures: list[CanonicalCapture] = []
        captured_names: set[str] = set()
        for node in expression.body.walk():
            if not isinstance(node, SurfaceName) or node.name in parameter_names:
                continue
            if node.name in captured_names:
                continue
            if not self._local_visible(function, node.name, node.span):
                continue
            if node.name not in function.parameters and node.name not in function.locals:
                continue
            first_binding = function.first_bindings.get(node.name)
            if (
                (
                    first_binding is not None
                    and first_binding.explicit_kind == "var"
                )
                or function.assignments.get(node.name, 0) > 1
            ):
                raise SurfaceElaborationError(
                    f"MutableClosureCaptureForbidden: {node.name}"
                )
            type_name = self.types.resolve(
                self._lookup(function, node.name),
                name=f"{function.source.name}.closure_capture.{node.name}",
            )
            properties = self.type_properties.resolve(type_name)
            if properties.contains_borrow:
                raise SurfaceElaborationError(
                    f"BorrowedClosureCaptureEscapes: {node.name}"
                )
            if properties.is_resource:
                raise SurfaceElaborationError(
                    f"ResourceClosureCaptureForbidden: {node.name}"
                )
            captures.append(
                CanonicalCapture(
                    node.name,
                    type_name,
                    "owned" if properties.needs_drop else "copy",
                )
            )
            captured_names.add(node.name)

        previous = {
            name: function.locals.get(name)
            for name in expression.parameters
        }
        try:
            for name, type_name in zip(
                expression.parameters,
                parameter_types,
                strict=True,
            ):
                function.locals[name] = self.types.typed(type_name)
            body = self._expression(
                expression.body,
                function,
                return_type,
            )
            self.types.unify(
                body,
                self.types.typed(return_type),
                context=f"{function.source.name} closure return",
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    function.locals.pop(name, None)
                else:
                    function.locals[name] = value
        digest_input = (
            f"{function.source.name}\0{expression.span.path}\0"
            f"{expression.span.start_line}\0{expression.span.start_column}\0"
            + ",".join(callback)
        )
        closure_id = hashlib.sha256(digest_input.encode()).hexdigest()[:16]
        function.closures[closure_id] = CanonicalClosure(
            closure_id,
            tuple(zip(expression.parameters, parameter_types, strict=True)),
            return_type,
            _emit_expression(expression.body),
            tuple(captures),
            expression.span,
        )
        return self.types.typed(expected)

    def _expression(
        self,
        expression: SurfaceExpression,
        function: _Function,
        expected: str | None = None,
    ) -> str:
        if isinstance(expression, SurfaceHole):
            if expected is None or expected == "Inferred":
                raise SurfaceElaborationError(
                    "UnconstrainedTypedHole: "
                    f"{function.source.name}:"
                    f"{expression.span.start_line}:"
                    f"{expression.span.start_column}"
                )
            key = (
                f"{expression.span.path}:"
                f"{expression.span.start_line}:"
                f"{expression.span.start_column}"
            )
            function.holes[key] = (expression, expected)
            term = self.types.typed(expected)
        elif isinstance(expression, SurfaceLiteral):
            if (
                expected in _INTEGER_BOUNDS
                and isinstance(expression.value, int)
                and not isinstance(expression.value, bool)
            ):
                lower, upper = _INTEGER_BOUNDS[expected]
                if not lower <= expression.value <= upper:
                    raise SurfaceElaborationError(
                        "NumericLiteralOutOfRange: "
                        f"{expression.value} for {expected}"
                    )
            if expected in _NUMERIC_TYPES and expression.kind in _NUMERIC_TYPES:
                term = self.types.typed(expected)
            elif expression.kind == "None" and expected and expected.startswith("Option["):
                term = self.types.typed(expected)
            else:
                term = self.types.typed(expression.kind)
        elif isinstance(expression, SurfaceName):
            if (
                expression.name == "result"
                and self._active_contract_result is function
            ):
                term = function.return_term
            elif expression.name == "Unit":
                term = self.types.typed("Unit")
            elif self._local_visible(
                function,
                expression.name,
                expression.span,
            ):
                term = self._lookup(function, expression.name)
            elif expression.name in self.functions:
                target = self.functions[expression.name]
                parameter_types = tuple(
                    self.types.resolve(
                        item,
                        name=f"{expression.name}.callback_parameter",
                    )
                    for item in target.parameters.values()
                )
                return_type = self.types.resolve(
                    target.return_term,
                    name=f"{expression.name}.callback_return",
                )
                term = self.types.typed(
                    "Fn[" + ",".join((*parameter_types, return_type)) + "]"
                )
            else:
                term = self._lookup(function, expression.name)
        elif isinstance(expression, SurfaceMember):
            term = self._member(expression, function, expected)
            expected = None
        elif isinstance(expression, SurfaceUnary):
            required = "Bool" if expression.operator == "not" else expected
            if (
                expression.operator == "-"
                and expected == "Int64"
                and isinstance(expression.operand, SurfaceLiteral)
                and expression.operand.value == 9223372036854775808
            ):
                term = self.types.typed("Int64")
            else:
                term = self._expression(
                    expression.operand,
                    function,
                    required,
                )
            operand_type = self.types.concrete.get(self.types.find(term))
            if expression.operator in {"+", "-", "~"}:
                if operand_type not in _NUMERIC_TYPES:
                    raise SurfaceElaborationError(
                        "numeric unary operator requires a numeric operand, "
                        f"got {operand_type or 'unresolved'}"
                    )
                if expression.operator == "~" and operand_type not in _INTEGER_TYPES:
                    raise SurfaceElaborationError(
                        f"IntegerOperatorRequired: ~ for {operand_type}"
                    )
                if expression.operator == "-" and operand_type in {"Byte", "UInt64"}:
                    raise SurfaceElaborationError(
                        f"UnsignedNegationForbidden: {operand_type}"
                    )
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
                contextual = expected if expected in _NUMERIC_TYPES else None
                left = self._expression(
                    expression.left,
                    function,
                    contextual,
                )
                left_type = self.types.concrete.get(self.types.find(left))
                if left_type is not None and left_type not in _NUMERIC_TYPES:
                    raise SurfaceElaborationError(
                        "numeric operator requires numeric operands, "
                        f"got {left_type}"
                    )
                if contextual is not None:
                    left = self.types.typed(contextual)
                    left_type = contextual
                operand_type = contextual or left_type
                right = self._expression(
                    expression.right, function, operand_type
                )
                right_type = self.types.concrete.get(self.types.find(right))
                if right_type is not None and right_type not in _NUMERIC_TYPES:
                    raise SurfaceElaborationError(
                        "numeric operator requires numeric operands, "
                        f"got {right_type}"
                    )
                operation_type = contextual or left_type or right_type
                if (
                    expression.operator in _INTEGER_ONLY_OPERATORS
                    and operation_type not in _INTEGER_TYPES
                ):
                    raise SurfaceElaborationError(
                        "IntegerOperatorRequired: "
                        f"{expression.operator} for "
                        f"{operation_type or 'unresolved'}"
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
                if name in _NUMERIC_TYPES:
                    if (
                        len(expression.arguments) != 1
                        or expression.arguments[0].name is not None
                    ):
                        raise SurfaceElaborationError(
                            f"ArityMismatch: {name}"
                        )
                    source_term = self._expression(
                        expression.arguments[0].value,
                        function,
                    )
                    source_type = self.types.concrete.get(
                        self.types.find(source_term)
                    )
                    if source_type not in _NUMERIC_TYPES:
                        raise SurfaceElaborationError(
                            f"NumericCastRequired: {source_type or 'unresolved'}"
                        )
                    term = self.types.typed(name)
                elif name == "sqrt":
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
                elif name in {
                    "wrapping_add",
                    "wrapping_sub",
                    "wrapping_mul",
                    "checked_add",
                    "checked_sub",
                    "checked_mul",
                }:
                    if len(expression.arguments) != 2:
                        raise SurfaceElaborationError(
                            f"ArityMismatch: {name}"
                        )
                    first = self._expression(
                        expression.arguments[0].value,
                        function,
                        expected,
                    )
                    first_type = self.types.concrete.get(
                        self.types.find(first)
                    )
                    if first_type not in {"Byte", "Int64", "UInt64"}:
                        raise SurfaceElaborationError(
                            f"NumericArgumentsRequired: {name}"
                        )
                    second = self._expression(
                        expression.arguments[1].value,
                        function,
                        first_type,
                    )
                    self.types.unify(
                        first,
                        second,
                        context=name,
                    )
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
                elif name == "Path":
                    if len(expression.arguments) != 1:
                        raise SurfaceElaborationError(
                            "ArityMismatch: Path"
                        )
                    self._expression(
                        expression.arguments[0].value,
                        function,
                        "Text",
                    )
                    term = self.types.typed("Path")
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
                elif self._local_visible(function, name, expression.span):
                    callback = self._lookup(function, name)
                    callback_type = self.types.concrete.get(
                        self.types.find(callback)
                    )
                    parts = (
                        _generic_parts(callback_type, "Fn")
                        if callback_type is not None
                        else None
                    )
                    if not parts or len(parts) < 1:
                        raise SurfaceElaborationError(
                            f"NotCallable: {name}"
                        )
                    parameter_types = parts[:-1]
                    if (
                        len(expression.arguments) != len(parameter_types)
                        or any(
                            argument.name is not None
                            for argument in expression.arguments
                        )
                    ):
                        raise SurfaceElaborationError(
                            f"ArityMismatch: {name}"
                        )
                    for argument, parameter_type in zip(
                        expression.arguments,
                        parameter_types,
                        strict=True,
                    ):
                        self._expression(
                            argument.value,
                            function,
                            parameter_type,
                        )
                    term = self.types.typed(parts[-1])
                elif name in self.functions:
                    target = self.functions[name]
                    if (
                        function.source.declared_kind == "fn"
                        and target.source.declared_kind == "task"
                    ):
                        raise SurfaceElaborationError(
                            f"EffectInPureFunction: {function.source.name}"
                        )
                    function.calls.add(name)
                    bound = _bind_call_arguments(
                        expression,
                        tuple(target.parameters),
                        name,
                    )
                    for parameter_name, argument in bound:
                        parameter = target.parameters[parameter_name]
                        self.types.unify(
                            self._expression(
                                argument.value,
                                function,
                                self.types.concrete.get(
                                    self.types.find(parameter)
                                ),
                            ),
                            parameter,
                            context=f"call {name}.{parameter_name}",
                        )
                    declared_result = (
                        target.source.return_type
                        if target.source.return_type
                        and _generic_parts(
                            target.source.return_type,
                            "Result",
                        )
                        else None
                    )
                    term = (
                        self.types.typed(declared_result)
                        if declared_result
                        else target.return_term
                    )
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
                    if payload_type is None:
                        if expression.arguments:
                            raise SurfaceElaborationError(
                                f"VariantPayloadMismatch: {enum.name}.{method}"
                            )
                    elif len(expression.arguments) != 1:
                        raise SurfaceElaborationError(
                            f"VariantPayloadMismatch: {enum.name}.{method}"
                        )
                    else:
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
                elif (
                    isinstance(receiver_expression, SurfaceName)
                    and (
                        static_signature
                        := CONTRACT_GRAPH.static_method(
                            receiver_expression.name,
                            method,
                        )
                    )
                ):
                    if len(expression.arguments) != len(
                        static_signature.parameters
                    ):
                        raise SurfaceElaborationError(
                            "ArityMismatch: "
                            f"{receiver_expression.name}.{method}"
                        )
                    for argument, parameter_type in zip(
                        expression.arguments,
                        static_signature.parameters,
                        strict=True,
                    ):
                        self._expression(
                            argument.value,
                            function,
                            parameter_type,
                        )
                    term = self.types.typed(
                        static_signature.result_type
                    )
                elif (
                    isinstance(receiver_expression, SurfaceName)
                    and receiver_expression.name == "Box"
                    and method == "new"
                ):
                    if len(expression.arguments) != 1:
                        raise SurfaceElaborationError(
                            "ArityMismatch: Box.new"
                        )
                    payload = self._expression(
                        expression.arguments[0].value,
                        function,
                    )
                    payload_type = self.types.concrete.get(
                        self.types.find(payload)
                    )
                    box_type = (
                        expected
                        if expected and expected.startswith("Box[")
                        else f"Box[{payload_type}]"
                        if payload_type
                        else None
                    )
                    if box_type is None:
                        raise SurfaceElaborationError(
                            "AmbiguousType: Box.new"
                        )
                    term = self.types.typed(box_type)
                elif method in COLLECTION_OPERATIONS:
                    term = self._collection_call(expression, function, expected)
                    expected = None
                elif method == "clone":
                    if expression.arguments:
                        raise SurfaceElaborationError("ArityMismatch: clone")
                    term = self._expression(receiver_expression, function)
                elif method in {"len", "capacity", "byte", "tag"}:
                    arity = 1 if method == "byte" else 0
                    if len(expression.arguments) != arity:
                        raise SurfaceElaborationError(
                            f"ArityMismatch: {method}"
                        )
                    self._expression(receiver_expression, function)
                    for argument in expression.arguments:
                        self._expression(
                            argument.value,
                            function,
                            "UInt64",
                        )
                    result_type = expected if expected in {
                        "Byte",
                        "UInt64",
                        "Int64",
                        "Float32",
                        "Float64",
                    } else "UInt64"
                    term = self.types.typed(result_type)
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
                elif method == "increment":
                    if len(expression.arguments) not in {1, 2}:
                        raise SurfaceElaborationError(
                            "ArityMismatch: increment"
                        )
                    receiver = self._expression(
                        receiver_expression,
                        function,
                        "Map[Text,UInt64]",
                    )
                    self.types.unify(
                        receiver,
                        self.types.typed("Map[Text,UInt64]"),
                        context="Map.increment receiver",
                    )
                    self._expression(
                        expression.arguments[0].value,
                        function,
                        "Text",
                    )
                    if len(expression.arguments) == 2:
                        self._expression(
                            expression.arguments[1].value,
                            function,
                            "UInt64",
                        )
                    term = self.types.typed("Unit")
                elif method == "insert":
                    if len(expression.arguments) != 2:
                        raise SurfaceElaborationError(
                            "ArityMismatch: insert"
                        )
                    receiver = self._expression(
                        receiver_expression,
                        function,
                    )
                    receiver_type = self.types.concrete.get(
                        self.types.find(receiver)
                    )
                    map_parts = (
                        _generic_parts(receiver_type, "Map")
                        if receiver_type
                        else None
                    )
                    if not map_parts or len(map_parts) != 2:
                        raise SurfaceElaborationError(
                            f"UnknownCall: {receiver_type or 'unresolved'}.insert"
                        )
                    self._expression(
                        expression.arguments[0].value,
                        function,
                        map_parts[0],
                    )
                    self._expression(
                        expression.arguments[1].value,
                        function,
                        map_parts[1],
                    )
                    term = self.types.typed("Unit")
                elif method == "get":
                    receiver = self._expression(
                        receiver_expression,
                        function,
                    )
                    receiver_type = self.types.concrete.get(
                        self.types.find(receiver)
                    )
                    map_parts = (
                        _generic_parts(receiver_type, "Map")
                        if receiver_type
                        else None
                    )
                    vec_parts = (
                        _generic_parts(receiver_type, "Vec")
                        if receiver_type
                        else None
                    )
                    box_parts = (
                        _generic_parts(receiver_type, "Box")
                        if receiver_type
                        else None
                    )
                    if map_parts and len(map_parts) == 2 and len(expression.arguments) == 1:
                        self._expression(
                            expression.arguments[0].value,
                            function,
                            map_parts[0],
                        )
                        term = self.types.typed(map_parts[1])
                    elif vec_parts and len(vec_parts) == 1 and len(expression.arguments) == 1:
                        self._expression(
                            expression.arguments[0].value,
                            function,
                            "UInt64",
                        )
                        term = self.types.typed(vec_parts[0])
                    elif box_parts and len(box_parts) == 1 and not expression.arguments:
                        term = self.types.typed(box_parts[0])
                    else:
                        raise SurfaceElaborationError(
                            f"ArityMismatch: {receiver_type or 'unresolved'}.get"
                        )
                elif method == "entries":
                    if expression.arguments:
                        raise SurfaceElaborationError(
                            "ArityMismatch: entries"
                        )
                    receiver = self._expression(
                        receiver_expression,
                        function,
                    )
                    receiver_type = self.types.concrete.get(
                        self.types.find(receiver)
                    )
                    map_parts = (
                        _generic_parts(receiver_type, "Map")
                        if receiver_type
                        else None
                    )
                    if not map_parts or len(map_parts) != 2:
                        raise SurfaceElaborationError(
                            f"UnknownCall: {receiver_type or 'unresolved'}.entries"
                        )
                    term = self.types.typed(f"Borrow[{receiver_type}]")
                elif method in {
                    "is_none",
                    "is_some",
                    "is_ok",
                    "is_err",
                    "unwrap",
                    "unwrap_err",
                }:
                    if expression.arguments:
                        raise SurfaceElaborationError(
                            f"ArityMismatch: {method}"
                        )
                    receiver = self._expression(
                        receiver_expression,
                        function,
                    )
                    receiver_type = self.types.concrete.get(
                        self.types.find(receiver)
                    )
                    option_parts = (
                        _generic_parts(receiver_type, "Option")
                        if receiver_type is not None
                        else None
                    )
                    result_parts = (
                        _generic_parts(receiver_type, "Result")
                        if receiver_type is not None
                        else None
                    )
                    if method in {"is_none", "is_some"} and option_parts:
                        term = self.types.typed("Bool")
                    elif method in {"is_ok", "is_err"} and result_parts:
                        term = self.types.typed("Bool")
                    elif method == "unwrap" and option_parts:
                        term = self.types.typed(option_parts[0])
                    elif method == "unwrap" and result_parts:
                        term = self.types.typed(result_parts[0])
                    elif method == "unwrap_err" and result_parts:
                        term = self.types.typed(result_parts[1])
                    else:
                        raise SurfaceElaborationError(
                            f"UnknownCall: {receiver_type or 'unresolved'}.{method}"
                        )
                elif method == "view":
                    if expression.arguments:
                        raise SurfaceElaborationError(
                            "ArityMismatch: view"
                        )
                    receiver = self._expression(
                        receiver_expression,
                        function,
                    )
                    receiver_type = self.types.concrete.get(
                        self.types.find(receiver)
                    )
                    if receiver_type == "Bytes":
                        result_type = "BytesView"
                    elif receiver_type == "Text":
                        result_type = "TextView"
                    elif receiver_type and receiver_type.startswith("Vec["):
                        result_type = f"Borrow[{receiver_type}]"
                    else:
                        raise SurfaceElaborationError(
                            f"UnknownCall: {receiver_type or 'unresolved'}.view"
                        )
                    term = self.types.typed(result_type)
                elif (
                    method in INSTANCE_METHOD_NAMES
                    and not (
                        isinstance(receiver_expression, SurfaceName)
                        and f"{receiver_expression.name}.{method}" in _HOST_CALLS
                    )
                ):
                    receiver = self._expression(
                        receiver_expression,
                        function,
                    )
                    receiver_type = self.types.concrete.get(
                        self.types.find(receiver)
                    )
                    signature = CONTRACT_GRAPH.method(receiver_type or "", method)
                    if signature is None:
                        raise SurfaceElaborationError(
                            f"UnknownCall: {receiver_type or 'unresolved'}.{method}"
                        )
                    parameters = signature.parameters
                    result_type = signature.result_type
                    if len(expression.arguments) != len(parameters):
                        raise SurfaceElaborationError(
                            f"ArityMismatch: {receiver_type}.{method}"
                        )
                    for argument, parameter_type in zip(
                        expression.arguments,
                        parameters,
                        strict=True,
                    ):
                        self._expression(
                            argument.value,
                            function,
                            parameter_type,
                        )
                    term = self.types.typed(result_type)
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
                        expected or contextual_result_type(return_type, expected)
                    )
            else:
                raise SurfaceElaborationError("UnsupportedCall")
        elif isinstance(expression, SurfaceLambda):
            term = self._lambda(expression, function, expected)
        elif isinstance(expression, SurfaceIndex):
            self._expression(expression.index, function, "UInt64")
            owner = self._expression(expression.receiver, function)
            owner_type = self.types.concrete.get(self.types.find(owner))
            shape = collection_shape(owner_type)
            if shape is None:
                raise SurfaceElaborationError("IndexRequiresCollection")
            term = self.types.typed(shape.element_type)
        elif isinstance(expression, SurfaceList):
            element = self.types.variable(f"list:{expression.span.start_line}:{expression.span.start_column}")
            for item in expression.items:
                self.types.unify(element, self._expression(item, function), context="list element")
            expected_shape = collection_shape(expected)
            if expected_shape is not None:
                self.types.unify(
                    element,
                    self.types.typed(expected_shape.element_type),
                    context="list expected type",
                )
                term = self.types.typed(expected)
            else:
                element_type = self.types.concrete.get(self.types.find(element))
                term = (
                    self.types.typed(f"Vec[{element_type}]")
                    if element_type
                    else self.types.variable(
                        f"vec:{expression.span.start_line}"
                    )
                )
        elif isinstance(expression, SurfaceTry):
            inner_call = expression.expression
            named_error_call = (
                isinstance(inner_call, SurfaceCall)
                and isinstance(inner_call.callee, SurfaceName)
                and inner_call.callee.name in self.functions
            )
            inner = self._expression(inner_call, function)
            if named_error_call:
                function.error_calls.add(inner_call.callee.name)
            inner_type = self.types.concrete.get(self.types.find(inner))
            result_parts = (
                _generic_parts(inner_type, "Result")
                if inner_type is not None
                else None
            )
            if result_parts:
                term = self.types.typed(result_parts[0])
                function.errors.add(result_parts[1])
            elif named_error_call and self.functions[
                inner_call.callee.name
            ].errors:
                term = inner
            elif inner_type is not None:
                raise SurfaceElaborationError(
                    f"TryRequiresResult: found {inner_type}"
                )
            else:
                term = self.types.variable(
                    f"try:{expression.span.path}:{expression.span.start_line}:"
                    f"{expression.span.start_column}"
                )
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
        tail_position: bool = True,
    ) -> None:
        for index, statement in enumerate(statements):
            if isinstance(statement, (SurfaceUses, SurfaceComment)):
                continue
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
                previous_binding = self._active_binding
                self._active_binding = statement.name
                try:
                    value = self._expression(
                        statement.value,
                        function,
                        statement.type_name
                        or self.types.concrete.get(
                            self.types.find(term)
                        ),
                    )
                finally:
                    self._active_binding = previous_binding
                self.types.unify(term, value, context=f"assignment {statement.name}")
                self._note(function, statement.name, "assignment_value")
            elif isinstance(statement, SurfaceAnnotation):
                term = self._local(function, statement.name)
                self.types.unify(
                    term,
                    self.types.typed(statement.type_name),
                    context=f"annotation {statement.name}",
                )
                self._note(function, statement.name, "explicit_annotation")
            elif isinstance(statement, SurfaceAssignment):
                if isinstance(statement.target, SurfaceName):
                    target = self._lookup(function, statement.target.name)
                    label = statement.target.name
                    self._note(function, label, "mutated")
                else:
                    target = self._expression(
                        statement.target,
                        function,
                    )
                    label = type(statement.target).__name__
                value = self._expression(
                    statement.value,
                    function,
                    self.types.concrete.get(self.types.find(target)),
                )
                self.types.unify(
                    target,
                    value,
                    context=f"mutation {label}",
                )
            elif isinstance(statement, SurfaceReturn):
                if statement.expression is None:
                    actual = self.types.typed("Unit")
                    actual_result = None
                else:
                    direct_result_call = (
                        isinstance(statement.expression, SurfaceCall)
                        and isinstance(
                            statement.expression.callee,
                            SurfaceName,
                        )
                        and statement.expression.callee.name in self.functions
                        and self.functions[
                            statement.expression.callee.name
                        ].source.return_type is not None
                        and _generic_parts(
                            self.functions[
                                statement.expression.callee.name
                            ].source.return_type,
                            "Result",
                        )
                        is not None
                    )
                    expected = self.types.concrete.get(
                        self.types.find(function.return_term)
                    )
                    actual = self._expression(
                        statement.expression,
                        function,
                        None if direct_result_call else expected,
                    )
                    actual_type = self.types.concrete.get(
                        self.types.find(actual)
                    )
                    actual_result = (
                        _generic_parts(actual_type, "Result")
                        if actual_type
                        else None
                    )
                if actual_result and len(actual_result) == 2:
                    self.types.unify(
                        function.return_term,
                        self.types.typed(actual_result[0]),
                        context=f"{function.source.name} return",
                    )
                    function.errors.add(actual_result[1])
                else:
                    self.types.unify(
                        function.return_term,
                        actual,
                        context=f"{function.source.name} return",
                    )
                self._note(function, "$return", "explicit_return")
            elif isinstance(statement, (SurfaceRequire, SurfaceEnsure)):
                calls_before = set(function.calls)
                effects_before = set(function.effects)
                capabilities_before = set(function.capabilities)
                previous_contract = self._active_contract_result
                self._active_contract_result = (
                    function if isinstance(statement, SurfaceEnsure) else None
                )
                try:
                    self._expression(statement.condition, function, "Bool")
                finally:
                    self._active_contract_result = previous_contract
                function.contract_calls.update(function.calls - calls_before)
                if (
                    function.effects != effects_before
                    or function.capabilities != capabilities_before
                ):
                    raise SurfaceElaborationError(
                        "EffectInContract: "
                        f"{function.source.name}"
                    )
            elif isinstance(statement, SurfaceBreak):
                if loop_depth == 0:
                    raise SurfaceElaborationError("BreakOutsideLoop")
            elif isinstance(statement, SurfaceContinue):
                if loop_depth == 0:
                    raise SurfaceElaborationError("ContinueOutsideLoop")
            elif isinstance(statement, SurfaceExpressionStatement):
                is_tail = tail_position and not any(
                    not isinstance(item, (SurfaceUses, SurfaceComment))
                    for item in statements[index + 1 :]
                )
                direct_result_call = False
                if is_tail and isinstance(statement.expression, SurfaceCall):
                    callee = statement.expression.callee
                    if (
                        isinstance(callee, SurfaceName)
                        and callee.name in self.functions
                    ):
                        declared = self.functions[
                            callee.name
                        ].source.return_type
                        direct_result_call = bool(
                            declared
                            and _generic_parts(declared, "Result")
                        )
                    elif (
                        isinstance(callee, SurfaceMember)
                        and isinstance(callee.receiver, SurfaceName)
                    ):
                        signature = _HOST_CALLS.get(
                            f"{callee.receiver.name}.{callee.field}"
                        )
                        direct_result_call = bool(
                            signature
                            and _generic_parts(signature[1], "Result")
                        )
                declared_result = (
                    function.source.return_type
                    if function.source.return_type
                    and _generic_parts(
                        function.source.return_type,
                        "Result",
                    )
                    else None
                )
                expected = (
                    declared_result
                    if is_tail and direct_result_call
                    else self.types.concrete.get(
                        self.types.find(function.return_term)
                    )
                    if is_tail
                    else None
                )
                actual = self._expression(
                    statement.expression,
                    function,
                    expected,
                )
                if is_tail:
                    actual_type = self.types.concrete.get(
                        self.types.find(actual)
                    )
                    actual_result = (
                        _generic_parts(actual_type, "Result")
                        if actual_type
                        else None
                    )
                    if actual_result and len(actual_result) == 2:
                        self.types.unify(
                            function.return_term,
                            self.types.typed(actual_result[0]),
                            context=f"{function.source.name} tail",
                        )
                        function.errors.add(actual_result[1])
                    else:
                        self.types.unify(
                            function.return_term,
                            actual,
                            context=f"{function.source.name} tail",
                        )
                    self._note(function, "$return", "tail_expression")
            elif isinstance(statement, SurfaceIf):
                self._expression(statement.condition, function, "Bool")
                self._statements(
                    statement.body,
                    function,
                    loop_depth=loop_depth,
                    tail_position=False,
                )
                self._statements(
                    statement.otherwise,
                    function,
                    loop_depth=loop_depth,
                    tail_position=False,
                )
            elif isinstance(statement, SurfaceWhile):
                self._expression(statement.condition, function, "Bool")
                self._statements(
                    statement.body,
                    function,
                    loop_depth=loop_depth + 1,
                    tail_position=False,
                )
            elif isinstance(statement, SurfaceFor):
                iterable = self._expression(statement.iterable, function)
                iterable_type = self.types.concrete.get(self.types.find(iterable))
                shape = collection_shape(iterable_type)
                borrowed_parts = _generic_parts(iterable_type, "Borrow")
                borrowed_type = (
                    borrowed_parts[0]
                    if borrowed_parts and len(borrowed_parts) == 1
                    else None
                )
                borrowed_shape = collection_shape(borrowed_type)
                borrowed_map_parts = (
                    _generic_parts(borrowed_type, "Map")
                    if borrowed_type
                    else None
                )
                if shape is not None:
                    item_type = shape.element_type
                elif borrowed_shape is not None:
                    item_type = borrowed_shape.element_type
                elif borrowed_map_parts is not None:
                    item_type = (
                        f"MapEntry[{borrowed_map_parts[0]},"
                        f"{borrowed_map_parts[1]}]"
                    )
                elif iterable_type == "FileLines":
                    item_type = "TextView"
                else:
                    raise SurfaceElaborationError(
                        f"CollectionReceiverRequired: for {iterable_type}"
                    )
                self.types.unify(
                    self._local(function, statement.name),
                    self.types.typed(item_type),
                    context="for item",
                )
                self._statements(
                    statement.body,
                    function,
                    loop_depth=loop_depth + 1,
                    tail_position=False,
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
                elif statement.cases and all(
                    case.pattern == "_" for case in statement.cases
                ):
                    variants = {"_": None}
                else:
                    raise SurfaceElaborationError(
                        f"MatchRequiresClosedSum: {matched_type or 'unresolved'}"
                    )
                observed: set[str] = set()
                for case in statement.cases:
                    raw = case.pattern
                    if raw == "_":
                        observed.update(variants)
                        binding = None
                        payload_type = None
                    else:
                        pattern = raw.rsplit(".", 1)[-1]
                        variant_match = __import__("re").fullmatch(
                            r"([A-Za-z_]\w*)(?:\(([A-Za-z_]\w*)?\))?",
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
                        if binding == "_":
                            binding = None
                            payload_type = None
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
                        tail_position=False,
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
            if isinstance(statement, SurfaceUses):
                continue
            if isinstance(statement, SurfaceComment):
                lines.append(f"{prefix}{statement.text}")
                continue
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
            elif isinstance(statement, SurfaceAnnotation):
                lines.append(
                    f"{prefix}{statement.name}: {statement.type_name}"
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
            elif isinstance(statement, SurfaceRequire):
                lines.append(
                    f"{prefix}require "
                    f"{_emit_expression(statement.condition)}"
                )
            elif isinstance(statement, SurfaceEnsure):
                lines.append(
                    f"{prefix}ensure "
                    f"{_emit_expression(statement.condition)}"
                )
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
    @staticmethod
    def _node_id(kind: str, name: str, span, ordinal: int = 0) -> str:
        payload = f"{kind}\0{name}\0{span.path}\0{span.start_line}\0{span.start_column}\0{ordinal}"
        return hashlib.sha256(payload.encode()).hexdigest()[:24]

    def _canonical_flow(self, flow: SurfaceFlow) -> CanonicalFlow:
        names = {item.name for item in flow.parameters}
        body: list[CanonicalFlowStep | CanonicalParallel | CanonicalReturn] = []
        effects: set[str] = set()
        capabilities: set[str] = set()
        ordinal = 0
        for statement in flow.body:
            if isinstance(statement, SurfaceUses):
                effects.update(statement.effects)
                capabilities.update(statement.effects)
                continue
            if isinstance(statement, SurfaceParallel):
                branches: list[CanonicalFlowStep] = []
                for branch in statement.branches:
                    if branch.name in names:
                        raise SurfaceElaborationError(
                            f"FlowDuplicateBinding: {flow.name}.{branch.name}"
                        )
                    names.add(branch.name)
                    policies = tuple(
                        CanonicalPolicy(
                            policy.kind,
                            policy.value,
                            policy.error_type,
                            _emit_expression(policy.expression) if policy.expression else None,
                            policy.span,
                        )
                        for policy in branch.policies
                    )
                    self._validate_flow_policies(flow, branch, policies)
                    branches.append(CanonicalFlowStep(
                        self._node_id("flow-step", branch.name, branch.span, ordinal),
                        branch.name,
                        str(_emit_expression(branch.value)),
                        branch.type_name or self._flow_value_type(branch.value),
                        policies,
                        branch.span,
                    ))
                    ordinal += 1
                body.append(CanonicalParallel(
                    self._node_id("parallel", flow.name, statement.span, ordinal),
                    tuple(branches), statement.span,
                ))
                continue
            if isinstance(statement, SurfaceFlowStep):
                if statement.name in names:
                    raise SurfaceElaborationError(
                        f"FlowDuplicateBinding: {flow.name}.{statement.name}"
                    )
                names.add(statement.name)
                policies = tuple(
                    CanonicalPolicy(
                        policy.kind,
                        policy.value,
                        policy.error_type,
                        _emit_expression(policy.expression) if policy.expression else None,
                        policy.span,
                    )
                    for policy in statement.policies
                )
                self._validate_flow_policies(flow, statement, policies)
                body.append(CanonicalFlowStep(
                    self._node_id("flow-step", statement.name, statement.span, ordinal),
                    statement.name,
                    str(_emit_expression(statement.value)),
                    statement.type_name or self._flow_value_type(statement.value),
                    policies,
                    statement.span,
                ))
                ordinal += 1
                continue
            if isinstance(statement, SurfaceReturn) and statement.expression is not None:
                body.append(CanonicalReturn(str(_emit_expression(statement.expression)), statement.span))
        return CanonicalFlow(
            flow.name,
            tuple((item.name, item.type_name or "Inferred") for item in flow.parameters),
            flow.return_type,
            flow.durable,
            tuple(sorted(effects)),
            tuple(sorted(capabilities)),
            tuple(body),
            flow.span,
            flow.exported,
        )

    @staticmethod
    def _flow_value_type(expression: SurfaceExpression) -> str:
        if isinstance(expression, SurfaceLiteral):
            return {"bool": "Bool", "int": "Int64", "float": "Float64", "str": "Text"}.get(
                type(expression.value).__name__, "Inferred"
            )
        return "Inferred"

    @staticmethod
    def _validate_flow_policies(
        flow: SurfaceFlow, step: SurfaceFlowStep, policies: tuple[CanonicalPolicy, ...]
    ) -> None:
        retry = next((item for item in policies if item.kind == "retry"), None)
        if retry is not None:
            if int(retry.value) <= 0:
                raise SurfaceElaborationError(
                    f"RetryCountMustBePositive: {flow.name}.{step.name}"
                )
            if not any(item.kind == "idempotent" for item in policies):
                raise SurfaceElaborationError(
                    f"RetryRequiresIdempotency: {flow.name}.{step.name}"
                )

    def _canonical_machine(self, machine: SurfaceMachine) -> CanonicalMachine:
        state_names = [item.name for item in machine.states]
        if len(state_names) != len(set(state_names)):
            raise SurfaceElaborationError(f"DuplicateState: {machine.name}")
        states = tuple(
            CanonicalMachineState(
                item.name,
                tuple((field.name, field.type_name) for field in item.fields),
                item.span,
            )
            for item in machine.states
        )
        known = set(state_names)
        if machine.initial is not None and machine.initial not in known:
            raise SurfaceElaborationError(f"UnknownInitialState: {machine.name}.{machine.initial}")
        transitions: list[CanonicalTransition] = []
        transition_names: set[str] = set()
        for transition in machine.transitions:
            if transition.name in transition_names:
                raise SurfaceElaborationError(f"DuplicateTransition: {machine.name}.{transition.name}")
            transition_names.add(transition.name)
            if any(source not in known for source in transition.sources):
                unknown = next(source for source in transition.sources if source not in known)
                raise SurfaceElaborationError(f"UnknownState: {machine.name}.{unknown}")
            if transition.target not in known:
                raise SurfaceElaborationError(f"IllegalTargetState: {machine.name}.{transition.target}")
            effects = tuple(sorted(
                effect
                for statement in transition.body
                if isinstance(statement, SurfaceUses)
                for effect in statement.effects
            ))
            body = tuple(
                CanonicalBinding(
                    statement.name,
                    "Inferred",
                    False,
                    str(_emit_expression(statement.value)),
                    statement.span,
                )
                for statement in transition.body
                if isinstance(statement, SurfaceBinding)
            )
            transitions.append(CanonicalTransition(
                self._node_id("transition", transition.name, transition.span),
                transition.name,
                transition.sources,
                transition.target,
                effects,
                body,
                transition.span,
            ))
        if machine.initial is not None:
            reachable = {machine.initial}
            changed = True
            while changed:
                changed = False
                for transition in transitions:
                    if set(transition.sources) & reachable and transition.target not in reachable:
                        reachable.add(transition.target)
                        changed = True
            if reachable != known:
                missing = sorted(known - reachable)[0]
                raise SurfaceElaborationError(f"UnreachableState: {machine.name}.{missing}")
        return CanonicalMachine(
            machine.name,
            tuple((item.name, item.type_name or "Inferred") for item in machine.parameters),
            states,
            machine.initial,
            _emit_expression(machine.invariant) if machine.invariant else None,
            tuple(transitions),
            machine.span,
            machine.exported,
        )
    def result(self) -> SurfaceElaboration:
        records = tuple(
            CanonicalRecord(
                record.name,
                tuple((field.name, field.type_name) for field in record.fields),
                record.span,
                record.exported,
                tuple(
                    CanonicalContract(
                        "invariant",
                        _emit_expression(invariant.condition),
                        invariant.span,
                    )
                    for invariant in record.invariants
                ),
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
            requirements = tuple(
                CanonicalContract(
                    "require",
                    _emit_expression(statement.condition),
                    statement.span,
                )
                for statement in statements
                if isinstance(statement, SurfaceRequire)
            )
            ensures = tuple(
                CanonicalContract(
                    "ensure",
                    _emit_expression(statement.condition),
                    statement.span,
                )
                for statement in statements
                if isinstance(statement, SurfaceEnsure)
            )
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
                    canonical_return_type,
                    False,
                    tuple(sorted(function.evidence.get("$return", {"body_constraint"}))),
                )
            )
            holes = []
            for _key, (hole, expected_type) in sorted(
                function.holes.items()
            ):
                context_items = [
                    CanonicalHoleBinding(
                        name,
                        type_name,
                        (
                            "owned"
                            if self.type_properties.resolve(
                                type_name
                            ).needs_drop
                            else "copy"
                        ),
                    )
                    for name, type_name in parameter_types
                ]
                for name, binding in sorted(
                    function.first_bindings.items(),
                    key=lambda item: (
                        item[1].span.start_line,
                        item[1].span.start_column,
                        item[0],
                    ),
                ):
                    if (
                        binding.span.start_line
                        >= hole.span.start_line
                    ):
                        continue
                    type_name = self.types.resolve(
                        function.locals[name],
                        name=(
                            f"{function.source.name}."
                            f"hole_context.{name}"
                        ),
                    )
                    context_items.append(
                        CanonicalHoleBinding(
                            name,
                            type_name,
                            (
                                "owned"
                                if self.type_properties.resolve(
                                    type_name
                                ).needs_drop
                                else "copy"
                            ),
                        )
                    )
                callable_items = tuple(
                    CanonicalHoleCallable(
                        name,
                        tuple(
                            (
                                parameter_name,
                                self.types.resolve(
                                    term,
                                    name=(
                                        f"{name}."
                                        f"{parameter_name}"
                                    ),
                                ),
                            )
                            for parameter_name, term
                            in target.parameters.items()
                        ),
                        self.types.resolve(
                            target.return_term,
                            name=f"{name}.$return",
                        ),
                        tuple(sorted(target.effects)),
                        tuple(sorted(target.capabilities)),
                    )
                    for name, target in sorted(
                        self.functions.items()
                    )
                )
                identity = hashlib.sha256(
                    (
                        f"hole\0{function.source.name}\0"
                        f"{hole.span.path}\0"
                        f"{hole.span.start_line}\0"
                        f"{hole.span.start_column}"
                    ).encode()
                ).hexdigest()
                holes.append(
                    CanonicalHole(
                        f"hole_{identity[:24]}",
                        expected_type,
                        hole.span,
                        tuple(context_items),
                        callable_items,
                        tuple(sorted(function.effects)),
                        tuple(sorted(function.capabilities)),
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
                    requirements,
                    ensures,
                    tuple(body),
                    function.source.span,
                    function.source.exported,
                    "inferred_declaration",
                    tuple(function.implicit_callables.values()),
                    tuple(function.option_fallbacks.values()),
                    self._canonical_lines(statements, function),
                    tuple(function.closures.values()),
                    tuple(holes),
                )
            )
        flows = tuple(
            self._canonical_flow(item)
            for item in self.program.declarations
            if isinstance(item, SurfaceFlow)
        )
        machines = tuple(
            self._canonical_machine(item)
            for item in self.program.declarations
            if isinstance(item, SurfaceMachine)
        )
        canonical = CanonicalProgram(records, tuple(functions), enums, flows, machines)
        canonical = replace(
            canonical,
            surface_program=self.program,
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


def _emit_nested(expression: SurfaceExpression) -> str:
    rendered = str(_emit_expression(expression))
    return f"({rendered})" if isinstance(expression, SurfaceBinary) else rendered


def _emit_expression(expression: SurfaceExpression | None) -> str | None:
    if expression is None:
        return None
    if isinstance(expression, SurfaceHole):
        return "?"
    if isinstance(expression, SurfaceName):
        return expression.name
    if isinstance(expression, SurfaceLiteral):
        if expression.kind == "Bool":
            return "true" if expression.value else "false"
        return repr(expression.value)
    if isinstance(expression, SurfaceLambda):
        return (
            f"{', '.join(expression.parameters)} => "
            f"{_emit_expression(expression.body)}"
        )
    if isinstance(expression, SurfaceImplicitReceiver):
        return f".{expression.field}"
    if isinstance(expression, SurfaceMember):
        return f"{_emit_nested(expression.receiver)}.{expression.field}"
    if isinstance(expression, SurfaceBinary):
        return (
            f"{_emit_nested(expression.left)} {expression.operator} "
            f"{_emit_nested(expression.right)}"
        )
    if isinstance(expression, SurfaceUnary):
        return f"{expression.operator} {_emit_nested(expression.operand)}"
    if isinstance(expression, SurfaceCall):
        arguments = ", ".join(
            f"{item.name}: {_emit_expression(item.value)}" if item.name else str(_emit_expression(item.value))
            for item in expression.arguments
        )
        return f"{_emit_nested(expression.callee)}({arguments})"
    if isinstance(expression, SurfaceIndex):
        return (
            f"{_emit_nested(expression.receiver)}"
            f"[{_emit_expression(expression.index)}]"
        )
    if isinstance(expression, SurfaceTry):
        return f"{_emit_nested(expression.expression)}?"
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
        left = _emit_implicit_expression(expression.left)
        right = _emit_implicit_expression(expression.right)
        if isinstance(expression.left, SurfaceBinary):
            left = f"({left})"
        if isinstance(expression.right, SurfaceBinary):
            right = f"({right})"
        return f"{left} {expression.operator} {right}"
    if isinstance(expression, SurfaceUnary):
        operand = _emit_implicit_expression(expression.operand)
        if isinstance(expression.operand, SurfaceBinary):
            operand = f"({operand})"
        return f"{expression.operator} {operand}"
    raise SurfaceElaborationError(
        f"CannotEmitImplicitExpression: {type(expression).__name__}"
    )
def elaborate_surface(program: SurfaceProgram) -> SurfaceElaboration:
    from merlo.monomorphization import monomorphize_surface

    return _Elaborator(monomorphize_surface(program)).result()


__all__ = [
    "InferenceDecision",
    "SurfaceElaboration",
    "SurfaceElaborationError",
    "surface_lowering_module",
    "elaborate_surface",
]
