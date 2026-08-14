"""Small deterministic evaluator for Stage 0.4 benchmark semantics."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .frontend_semantics import FrontendCompilation, HIRLocalBinding, HIRReference, HIRSymbol
from .frontend_syntax import Declaration, Expression, MatchArm, Member, Statement


class EvaluationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RecordValue:
    symbol_id: str
    fields: tuple[tuple[str, Any], ...]

    def field(self, name: str) -> Any:
        values = dict(self.fields)
        if name not in values:
            raise EvaluationError(f"record has no field {name!r}")
        return values[name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "record",
            "symbol_id": self.symbol_id,
            "fields": {
                name: canonical_value(value) for name, value in self.fields
            },
        }


@dataclass(frozen=True)
class EnumValue:
    symbol_id: str
    variant: str
    payload: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "enum",
            "symbol_id": self.symbol_id,
            "variant": self.variant,
            "payload": canonical_value(self.payload),
        }


@dataclass(frozen=True)
class NewtypeValue:
    symbol_id: str
    value: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "newtype",
            "symbol_id": self.symbol_id,
            "value": canonical_value(self.value),
        }


@dataclass(frozen=True)
class CapabilityValue:
    symbol_id: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": "capability", "symbol_id": self.symbol_id}


@dataclass(frozen=True)
class EffectEvent:
    index: int
    effect: str
    member_symbol_id: str
    arguments: tuple[Any, ...]
    result: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "effect": self.effect,
            "member_symbol_id": self.member_symbol_id,
            "arguments": [canonical_value(item) for item in self.arguments],
            "result": canonical_value(self.result),
        }


@dataclass(frozen=True)
class EvaluationResult:
    value: Any
    effect_trace: tuple[EffectEvent, ...]
    executed_symbol_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": canonical_value(self.value),
            "effect_trace": [item.to_dict() for item in self.effect_trace],
            "executed_symbol_ids": list(self.executed_symbol_ids),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True)
class _SymbolCallable:
    symbol_id: str


@dataclass(frozen=True)
class _TypeConstructor:
    symbol_id: str


@dataclass(frozen=True)
class _MemberCallable:
    symbol_id: str
    receiver: Any


def canonical_value(value: Any) -> Any:
    if isinstance(value, (RecordValue, EnumValue, NewtypeValue, CapabilityValue)):
        return value.to_dict()
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, tuple):
        return [canonical_value(item) for item in value]
    if isinstance(value, list):
        return [canonical_value(item) for item in value]
    if isinstance(value, Mapping):
        return {
            str(key): canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    raise EvaluationError(f"non-canonical evaluator value: {type(value).__name__}")


class ReferenceEvaluator:
    def __init__(
        self,
        compilation: FrontendCompilation,
        *,
        handlers: Mapping[str, Callable[..., Any]] | None = None,
        max_call_depth: int = 64,
    ) -> None:
        if max_call_depth <= 0:
            raise ValueError("max_call_depth must be positive")
        self.compilation = compilation
        self.handlers = dict(handlers or {})
        self.max_call_depth = max_call_depth
        self.symbols = {item.symbol_id: item for item in compilation.hir.symbols}
        self.references_by_syntax: dict[str, list[HIRReference]] = {}
        for reference in compilation.hir.references:
            self.references_by_syntax.setdefault(reference.syntax_node_id, []).append(
                reference
            )
        self.locals_by_syntax: dict[tuple[str, str], HIRLocalBinding] = {
            (item.owner_symbol_id, item.syntax_node_id): item
            for item in compilation.hir.local_bindings
        }
        self.declarations: dict[str, Declaration] = {}
        self.members: dict[str, Member | None] = {}
        self.member_parent: dict[str, str] = {}
        syntax_declarations: dict[tuple[str, str], Declaration] = {}
        syntax_members: dict[tuple[str, str, str], Member] = {}
        for cst in compilation.csts:
            module = cst.module
            for declaration in module.declarations:
                syntax_declarations[(module.package_name, module.module_name)] = declaration
                for member in declaration.members:
                    syntax_members[
                        (module.package_name, module.module_name, f"{declaration.name}${member.name}")
                    ] = member
        for symbol in compilation.hir.symbols:
            module_key = (symbol.package_name, symbol.module_name)
            if symbol.parent_symbol_id is None:
                declaration = next(
                    (
                        item
                        for cst in compilation.csts
                        if (cst.module.package_name, cst.module.module_name) == module_key
                        for item in cst.module.declarations
                        if item.name == symbol.name
                    ),
                    None,
                )
                if declaration is not None:
                    self.declarations[symbol.symbol_id] = declaration
            else:
                self.member_parent[symbol.symbol_id] = symbol.parent_symbol_id
                self.members[symbol.symbol_id] = syntax_members.get(
                    (symbol.package_name, symbol.module_name, symbol.name)
                )
        self._effects: list[EffectEvent] = []
        self._executed: list[str] = []
        self._depth = 0

    def capability(self, id_or_locator: str) -> CapabilityValue:
        symbol = self.compilation.hir.symbol(id_or_locator)
        if symbol.kind != "capability":
            raise EvaluationError(f"{id_or_locator!r} is not a capability")
        return CapabilityValue(symbol.symbol_id)

    def newtype(self, id_or_locator: str, value: Any) -> NewtypeValue:
        symbol = self.compilation.hir.symbol(id_or_locator)
        if symbol.kind != "newtype":
            raise EvaluationError(f"{id_or_locator!r} is not a newtype")
        canonical_value(value)
        return NewtypeValue(symbol.symbol_id, value)

    def evaluate(
        self,
        id_or_locator: str,
        arguments: Mapping[str, Any] | Sequence[Any] = (),
    ) -> EvaluationResult:
        symbol = self.compilation.hir.symbol(id_or_locator)
        if symbol.kind not in {"fn", "task", "value"}:
            raise EvaluationError(f"{symbol.locator} is not executable")
        self._effects = []
        self._executed = []
        self._depth = 0
        if symbol.kind == "value":
            value = self._evaluate_global_value(symbol.symbol_id)
        else:
            value = self._call_declaration(symbol.symbol_id, arguments)
        canonical_value(value)
        return EvaluationResult(value, tuple(self._effects), tuple(self._executed))

    def _call_declaration(
        self,
        symbol_id: str,
        arguments: Mapping[str, Any] | Sequence[Any],
    ) -> Any:
        if self._depth >= self.max_call_depth:
            raise EvaluationError("maximum call depth exceeded")
        declaration = self.declarations.get(symbol_id)
        symbol = self.symbols[symbol_id]
        if declaration is None or declaration.kind not in {"fn", "task"}:
            raise EvaluationError(f"symbol {symbol.locator} is not callable")
        values = self._normalize_arguments(declaration, arguments)
        environment: dict[str, Any] = {}
        for parameter, value in zip(declaration.parameters, values):
            local = self.locals_by_syntax.get((symbol_id, parameter.syntax_id))
            if local is None:
                raise EvaluationError(
                    f"missing parameter binding for {symbol.locator}.{parameter.name}"
                )
            if parameter.capability:
                if not isinstance(value, CapabilityValue):
                    raise EvaluationError(
                        f"parameter {parameter.name} requires CapabilityValue"
                    )
                expected = self._type_reference_target(symbol_id, parameter.syntax_id)
                if expected is not None and value.symbol_id != expected:
                    raise EvaluationError(
                        f"capability {parameter.name} has the wrong semantic type"
                    )
            canonical_value(value)
            environment[local.binding_id] = value
        self._depth += 1
        self._executed.append(symbol_id)
        try:
            return self._evaluate_block(symbol_id, declaration.body, environment)
        finally:
            self._depth -= 1

    def _normalize_arguments(
        self,
        declaration: Declaration,
        arguments: Mapping[str, Any] | Sequence[Any],
    ) -> tuple[Any, ...]:
        names = tuple(item.name for item in declaration.parameters)
        if isinstance(arguments, Mapping):
            unknown = sorted(set(arguments) - set(names))
            missing = sorted(set(names) - set(arguments))
            if unknown or missing:
                raise EvaluationError(
                    f"argument mismatch; missing={missing}, unknown={unknown}"
                )
            return tuple(arguments[name] for name in names)
        values = tuple(arguments)
        if len(values) != len(names):
            raise EvaluationError(
                f"expected {len(names)} arguments, received {len(values)}"
            )
        return values

    def _evaluate_block(
        self,
        owner_symbol_id: str,
        statements: tuple[Statement, ...],
        environment: dict[str, Any],
    ) -> Any:
        result: Any = None
        for statement in statements:
            if statement.kind == "uses":
                continue
            if statement.kind == "let":
                assert statement.expression is not None
                value = self._evaluate_expression(
                    owner_symbol_id, statement.expression, environment
                )
                binding = self.locals_by_syntax.get(
                    (owner_symbol_id, statement.syntax_id)
                )
                if binding is None:
                    raise EvaluationError(
                        f"missing let binding for {statement.name!r}"
                    )
                environment[binding.binding_id] = value
                result = None
            elif statement.kind == "expression":
                assert statement.expression is not None
                result = self._evaluate_expression(
                    owner_symbol_id, statement.expression, environment
                )
            elif statement.kind == "if":
                assert statement.expression is not None
                condition = self._evaluate_expression(
                    owner_symbol_id, statement.expression, environment
                )
                branch = statement.body if condition else statement.else_body
                result = self._evaluate_block(
                    owner_symbol_id, branch, dict(environment)
                )
            elif statement.kind == "match":
                result = self._evaluate_match(
                    owner_symbol_id, statement, environment
                )
        return result

    def _evaluate_match(
        self,
        owner_symbol_id: str,
        statement: Statement,
        environment: dict[str, Any],
    ) -> Any:
        assert statement.expression is not None
        value = self._evaluate_expression(
            owner_symbol_id, statement.expression, environment
        )
        if not isinstance(value, EnumValue):
            raise EvaluationError("match received a non-enum value")
        arm = next(
            (item for item in statement.arms if item.variant == value.variant), None
        )
        if arm is None:
            raise EvaluationError(f"no match arm for {value.variant}")
        local_environment = dict(environment)
        if arm.binding:
            binding = self.locals_by_syntax.get((owner_symbol_id, arm.syntax_id))
            if binding is None:
                raise EvaluationError(
                    f"missing match binding for {arm.binding!r}"
                )
            local_environment[binding.binding_id] = value.payload
        return self._evaluate_expression(
            owner_symbol_id, arm.expression, local_environment
        )

    def _evaluate_expression(
        self,
        owner_symbol_id: str,
        expression: Expression,
        environment: dict[str, Any],
    ) -> Any:
        if expression.kind == "literal":
            return expression.value
        if expression.kind == "name":
            reference = self._reference(
                owner_symbol_id,
                expression.syntax_id,
                preferred=("Local", "Value"),
            )
            if reference.target_symbol_id is None:
                if reference.target_binding_id not in environment:
                    raise EvaluationError(
                        f"unbound local {expression.name!r} during evaluation"
                    )
                return environment[reference.target_binding_id]
            target = self.symbols[reference.target_symbol_id]
            if target.kind == "value":
                return self._evaluate_global_value(target.symbol_id)
            if target.kind in {"fn", "task"}:
                return _SymbolCallable(target.symbol_id)
            if target.kind in {"record", "enum", "newtype"}:
                return _TypeConstructor(target.symbol_id)
            raise EvaluationError(f"cannot evaluate name {target.locator}")
        if expression.kind == "field":
            receiver = self._evaluate_expression(
                owner_symbol_id, expression.children[0], environment
            )
            reference = self._reference(
                owner_symbol_id, expression.syntax_id, preferred=("Field",)
            )
            if reference.target_symbol_id is None:
                raise EvaluationError("field reference has no SymbolId")
            member = self.symbols[reference.target_symbol_id]
            if isinstance(receiver, RecordValue):
                return receiver.field(expression.name or "")
            if isinstance(receiver, _TypeConstructor):
                if member.kind == "variant":
                    member_syntax = self.members.get(member.symbol_id)
                    if member_syntax is not None and member_syntax.type_name is not None:
                        return _MemberCallable(member.symbol_id, receiver)
                    return EnumValue(receiver.symbol_id, expression.name or "")
                if member.kind == "constructor":
                    return _MemberCallable(member.symbol_id, receiver)
            if isinstance(receiver, CapabilityValue):
                expected_parent = self.member_parent.get(member.symbol_id)
                if expected_parent != receiver.symbol_id:
                    raise EvaluationError("capability member receiver mismatch")
                return _MemberCallable(member.symbol_id, receiver)
            raise EvaluationError(
                f"cannot read field {expression.name!r} from evaluator value"
            )
        if expression.kind == "call":
            callee = self._evaluate_expression(
                owner_symbol_id, expression.children[0], environment
            )
            positional: list[Any] = []
            named: dict[str, Any] = {}
            for argument in expression.arguments:
                value = self._evaluate_expression(
                    owner_symbol_id, argument.expression, environment
                )
                if argument.name is None:
                    positional.append(value)
                else:
                    named[argument.name] = value
            if isinstance(callee, _SymbolCallable):
                target = self.declarations[callee.symbol_id]
                arguments: Mapping[str, Any] | Sequence[Any]
                if named:
                    if positional:
                        combined = {
                            parameter.name: value
                            for parameter, value in zip(target.parameters, positional)
                        }
                        combined.update(named)
                        arguments = combined
                    else:
                        arguments = named
                else:
                    arguments = positional
                return self._call_declaration(callee.symbol_id, arguments)
            if isinstance(callee, _TypeConstructor):
                return self._construct_record(callee.symbol_id, positional, named)
            if isinstance(callee, _MemberCallable):
                member = self.symbols[callee.symbol_id]
                if member.kind == "constructor":
                    if len(positional) != 1 or named:
                        raise EvaluationError("newtype constructor expects one value")
                    return NewtypeValue(
                        self.member_parent[member.symbol_id], positional[0]
                    )
                if member.kind == "variant":
                    if len(positional) != 1 or named:
                        raise EvaluationError("payload variant expects one value")
                    return EnumValue(
                        self.member_parent[member.symbol_id],
                        member.name.split("$", 1)[-1],
                        positional[0],
                    )
                if member.kind == "capability_member":
                    if named:
                        member_syntax = self.members.get(member.symbol_id)
                        if member_syntax is None:
                            raise EvaluationError("missing capability member syntax")
                        positional = [
                            named[parameter.name]
                            for parameter in member_syntax.parameters
                        ]
                    return self._call_capability_member(member, tuple(positional))
            raise EvaluationError("expression is not callable")
        if expression.kind == "unary":
            value = self._evaluate_expression(
                owner_symbol_id, expression.children[0], environment
            )
            if expression.operator == "-":
                return -value
            raise EvaluationError(f"unsupported unary operator {expression.operator}")
        if expression.kind == "binary":
            left = self._evaluate_expression(
                owner_symbol_id, expression.children[0], environment
            )
            right = self._evaluate_expression(
                owner_symbol_id, expression.children[1], environment
            )
            operations = {
                "+": lambda: left + right,
                "-": lambda: left - right,
                "*": lambda: left * right,
                "/": lambda: left // right,
                "==": lambda: left == right,
                "!=": lambda: left != right,
                "<": lambda: left < right,
                "<=": lambda: left <= right,
                ">": lambda: left > right,
                ">=": lambda: left >= right,
            }
            operation = operations.get(expression.operator or "")
            if operation is None:
                raise EvaluationError(
                    f"unsupported binary operator {expression.operator}"
                )
            return operation()
        raise EvaluationError(f"unsupported expression kind {expression.kind}")

    def _construct_record(
        self,
        symbol_id: str,
        positional: Sequence[Any],
        named: Mapping[str, Any],
    ) -> RecordValue:
        declaration = self.declarations.get(symbol_id)
        if declaration is None or declaration.kind != "record":
            raise EvaluationError("type is not a record constructor")
        names = [item.name for item in declaration.members]
        values: dict[str, Any] = {
            name: value for name, value in zip(names, positional)
        }
        values.update(named)
        if set(values) != set(names):
            raise EvaluationError(
                f"record arguments mismatch; expected={sorted(names)}, actual={sorted(values)}"
            )
        return RecordValue(
            symbol_id,
            tuple((name, values[name]) for name in names),
        )

    def _call_capability_member(
        self, member: HIRSymbol, arguments: tuple[Any, ...]
    ) -> Any:
        member_syntax = self.members.get(member.symbol_id)
        if member_syntax is None or member_syntax.effect is None:
            raise EvaluationError("capability member has no effect declaration")
        handler = self.handlers.get(member_syntax.effect)
        if handler is None:
            raise EvaluationError(
                f"no evaluator handler for effect {member_syntax.effect!r}"
            )
        raw_result = handler(*arguments)
        result = self._coerce_effect_result(member, member_syntax, raw_result)
        canonical_value(result)
        self._executed.append(member.symbol_id)
        self._effects.append(
            EffectEvent(
                len(self._effects),
                member_syntax.effect,
                member.symbol_id,
                arguments,
                result,
            )
        )
        return result

    def _coerce_effect_result(
        self, member: HIRSymbol, syntax: Member, value: Any
    ) -> Any:
        parent_id = self.member_parent[member.symbol_id]
        target_id = next(
            (
                reference.target_symbol_id
                for references in self.references_by_syntax.values()
                for reference in references
                if reference.owner_symbol_id == parent_id
                and reference.usage == "Type"
                and reference.target_symbol_id is not None
                and self.symbols[reference.target_symbol_id].name
                == syntax.return_type
            ),
            None,
        )
        if target_id is not None:
            target = self.symbols[target_id]
            if target.kind == "newtype" and not isinstance(value, NewtypeValue):
                return NewtypeValue(target.symbol_id, value)
        return value

    def _evaluate_global_value(self, symbol_id: str) -> Any:
        declaration = self.declarations.get(symbol_id)
        if declaration is None or declaration.kind != "value" or declaration.value is None:
            raise EvaluationError("value declaration has no expression")
        self._executed.append(symbol_id)
        return self._evaluate_expression(symbol_id, declaration.value, {})

    def _reference(
        self,
        owner_symbol_id: str,
        syntax_id: str,
        *,
        preferred: tuple[str, ...],
    ) -> HIRReference:
        candidates = tuple(
            item
            for item in self.references_by_syntax.get(syntax_id, ())
            if item.owner_symbol_id == owner_symbol_id and item.usage in preferred
        )
        if len(candidates) != 1:
            raise EvaluationError(
                f"expected one {preferred} binding for syntax node {syntax_id}, found {len(candidates)}"
            )
        return candidates[0]

    def _type_reference_target(
        self, owner_symbol_id: str, syntax_id: str
    ) -> str | None:
        return next(
            (
                item.target_symbol_id
                for item in self.references_by_syntax.get(syntax_id, ())
                if item.owner_symbol_id == owner_symbol_id
                and item.usage == "Capability"
            ),
            None,
        )


__all__ = [
    "CapabilityValue",
    "EffectEvent",
    "EnumValue",
    "EvaluationError",
    "EvaluationResult",
    "NewtypeValue",
    "RecordValue",
    "ReferenceEvaluator",
    "canonical_value",
]
