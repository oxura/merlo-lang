"""Compiler-local TypeId annotations keyed by native AST object identity.

The typed AST is deliberately not serializable.  Native syntax nodes are retained
as keys by ``id(node)`` while the registry is live, preventing identity reuse and
making every semantic lookup fail closed when a traversal missed a node.
"""

from __future__ import annotations

from collections.abc import Mapping

from merlo import native_syntax as ast
from merlo.type_arena import TypeId


class TypedAstError(ValueError):
    """A typed-AST registration or required lookup is invalid."""


def _check_type_id(value: TypeId, label: str) -> TypeId:
    if not isinstance(value, TypeId):
        raise TypedAstError(f"{label} must be TypeId")
    return value


def _node_key(node: object, label: str) -> int:
    if not isinstance(node, ast.AST):
        raise TypedAstError(f"{label} must be a native_syntax AST node")
    return id(node)


class TypedAst:
    """Strict TypeId facts attached to one native AST object graph.

    Facts are intentionally keyed by object identity rather than source spelling,
    source locations, or strings.  The owner retains each registered AST object so
    Python cannot recycle an identity while the registry is in use.
    """

    __slots__ = (
        "_nodes",
        "_expressions",
        "_annotations",
        "_expected",
        "_function_parameters",
        "_function_returns",
        "_bindings",
        "_field_projections",
        "_variant_projections",
        "_field_projection_ids",
        "_variant_projection_ids",
    )

    def __init__(self) -> None:
        self._nodes: dict[int, object] = {}
        self._expressions: dict[int, TypeId] = {}
        self._annotations: dict[int, TypeId] = {}
        self._expected: dict[int, TypeId] = {}
        self._function_parameters: dict[tuple[int, object], TypeId] = {}
        self._function_returns: dict[int, TypeId] = {}
        self._bindings: dict[int, TypeId] = {}
        self._field_projections: dict[TypeId, dict[str, TypeId]] = {}
        self._variant_projections: dict[TypeId, dict[str, TypeId]] = {}
        self._field_projection_ids: dict[TypeId, dict[str, str]] = {}
        self._variant_projection_ids: dict[TypeId, dict[str, str]] = {}

    def _record(
        self,
        table: dict[int, TypeId],
        node: object,
        value: TypeId,
        label: str,
    ) -> TypeId:
        key = _node_key(node, label)
        checked = _check_type_id(value, label)
        previous = table.get(key)
        if previous is not None and previous != checked:
            raise TypedAstError(f"conflicting {label} TypeId for AST node")
        self._nodes[key] = node
        table[key] = checked
        return checked

    @staticmethod
    def _lookup(table: Mapping[int, TypeId], node: object, label: str) -> TypeId:
        key = _node_key(node, label)
        try:
            return table[key]
        except KeyError as exc:
            raise TypedAstError(f"missing {label} TypeId for AST node") from exc

    def record_expression(self, node: object, type_id: TypeId) -> TypeId:
        return self._record(self._expressions, node, type_id, "expression")

    def expression_type_id(self, node: object) -> TypeId:
        return self._lookup(self._expressions, node, "expression")


    def record_annotation(self, node: object, type_id: TypeId) -> TypeId:
        return self._record(self._annotations, node, type_id, "annotation")

    def annotation_type_id(self, node: object) -> TypeId:
        return self._lookup(self._annotations, node, "annotation")


    def record_expected(self, node: object, type_id: TypeId) -> TypeId:
        return self._record(self._expected, node, type_id, "expected context")

    def expected_type_id(self, node: object) -> TypeId:
        return self._lookup(self._expected, node, "expected context")


    @staticmethod
    def _function_key(function: object, parameter: object, label: str) -> tuple[int, object]:
        function_key = _node_key(function, f"{label} function")
        if isinstance(parameter, int) and not isinstance(parameter, bool):
            if parameter < 0:
                raise TypedAstError(f"{label} parameter index must be non-negative")
            parameter_key: object = parameter
        else:
            parameter_key = _node_key(parameter, f"{label} parameter")
        return function_key, parameter_key

    def record_function_parameter(
        self,
        function: object,
        parameter: object,
        type_id: TypeId,
    ) -> TypeId:
        key = self._function_key(function, parameter, "function parameter")
        checked = _check_type_id(type_id, "function parameter")
        previous = self._function_parameters.get(key)
        if previous is not None and previous != checked:
            raise TypedAstError("conflicting function parameter TypeId")
        self._nodes[id(function)] = function
        if not isinstance(parameter, int):
            self._nodes[id(parameter)] = parameter
        self._function_parameters[key] = checked
        return checked


    def function_parameter_type_id(self, function: object, parameter: object) -> TypeId:
        key = self._function_key(function, parameter, "function parameter")
        try:
            return self._function_parameters[key]
        except KeyError as exc:
            raise TypedAstError("missing function parameter TypeId") from exc


    def record_function_return(self, function: object, type_id: TypeId) -> TypeId:
        return self._record(self._function_returns, function, type_id, "function return")

    def function_return_type_id(self, function: object) -> TypeId:
        return self._lookup(self._function_returns, function, "function return")


    def record_binding(self, node: object, type_id: TypeId) -> TypeId:
        return self._record(self._bindings, node, type_id, "binding")

    def binding_type_id(self, node: object) -> TypeId:
        return self._lookup(self._bindings, node, "binding")


    @staticmethod
    def _projection_key(owner_type_id: TypeId, member: str, label: str) -> tuple[TypeId, str]:
        owner = _check_type_id(owner_type_id, f"{label} owner")
        if not isinstance(member, str) or not member:
            raise TypedAstError(f"{label} member must be non-empty text")
        return owner, member

    def _record_projection(
        self,
        table: dict[TypeId, dict[str, TypeId]],
        owner_type_id: TypeId,
        member: str,
        projected_type_id: TypeId,
        label: str,
    ) -> TypeId:
        owner, name = self._projection_key(owner_type_id, member, label)
        projected = _check_type_id(projected_type_id, label)
        previous = table.get(owner, {}).get(name)
        if previous is not None and previous != projected:
            raise TypedAstError(f"conflicting {label} TypeId for {name}")
        table.setdefault(owner, {})[name] = projected
        return projected

    @staticmethod
    def _lookup_projection(
        table: Mapping[TypeId, Mapping[str, TypeId]],
        owner_type_id: TypeId,
        member: str,
        label: str,
    ) -> TypeId:
        owner, name = TypedAst._projection_key(owner_type_id, member, label)
        try:
            return table[owner][name]
        except KeyError as exc:
            raise TypedAstError(f"missing {label} TypeId for {name}") from exc

    @staticmethod
    def _check_symbol_id(value: str, label: str) -> str:
        if not isinstance(value, str) or not value:
            raise TypedAstError(f"{label} must be non-empty text")
        return value

    def _record_projection_symbol(
        self,
        table: dict[TypeId, dict[str, str]],
        owner_type_id: TypeId,
        member: str,
        symbol_id: str,
        label: str,
    ) -> str:
        owner, name = self._projection_key(owner_type_id, member, label)
        symbol = self._check_symbol_id(symbol_id, label)
        previous = table.get(owner, {}).get(name)
        if previous is not None and previous != symbol:
            raise TypedAstError(f"conflicting {label} for {name}")
        table.setdefault(owner, {})[name] = symbol
        return symbol

    @staticmethod
    def _lookup_projection_symbol(
        table: Mapping[TypeId, Mapping[str, str]],
        owner_type_id: TypeId,
        member: str,
        label: str,
    ) -> str:
        owner, name = TypedAst._projection_key(owner_type_id, member, label)
        try:
            return table[owner][name]
        except KeyError as exc:
            raise TypedAstError(f"missing {label} for {name}") from exc

    def record_field_projection_symbol_id(
        self, owner_type_id: TypeId, field: str, symbol_id: str
    ) -> str:
        return self._record_projection_symbol(
            self._field_projection_ids,
            owner_type_id,
            field,
            symbol_id,
            "field projection symbol",
        )

    def field_projection_symbol_id(self, owner_type_id: TypeId, field: str) -> str:
        return self._lookup_projection_symbol(
            self._field_projection_ids,
            owner_type_id,
            field,
            "field projection symbol",
        )

    def record_variant_projection_symbol_id(
        self, owner_type_id: TypeId, variant: str, symbol_id: str
    ) -> str:
        return self._record_projection_symbol(
            self._variant_projection_ids,
            owner_type_id,
            variant,
            symbol_id,
            "variant projection symbol",
        )

    def variant_projection_symbol_id(self, owner_type_id: TypeId, variant: str) -> str:
        return self._lookup_projection_symbol(
            self._variant_projection_ids,
            owner_type_id,
            variant,
            "variant projection symbol",
        )


    def record_field_projection(self, owner_type_id: TypeId, field: str, type_id: TypeId) -> TypeId:
        return self._record_projection(self._field_projections, owner_type_id, field, type_id, "field projection")

    def field_projection_type_id(self, owner_type_id: TypeId, field: str) -> TypeId:
        return self._lookup_projection(self._field_projections, owner_type_id, field, "field projection")


    def record_variant_projection(self, owner_type_id: TypeId, variant: str, type_id: TypeId) -> TypeId:
        return self._record_projection(self._variant_projections, owner_type_id, variant, type_id, "variant projection")

    def variant_projection_type_id(self, owner_type_id: TypeId, variant: str) -> TypeId:
        return self._lookup_projection(self._variant_projections, owner_type_id, variant, "variant projection")



__all__ = ["TypedAst", "TypedAstError"]
