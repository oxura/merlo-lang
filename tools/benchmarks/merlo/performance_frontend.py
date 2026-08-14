"""Restricted Stage 0.5P surface parser and typed Performance MIR lowering.

The frozen Stage 0.4 frontend cannot represent loops, mutable locals, fixed arrays,
or 32/64-bit numeric types. This adapter deliberately leaves that frontend untouched,
uses the same indentation-oriented surface conventions, and records the compatibility
gap in every compilation.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable

from merlo.performance_mir import (
    BOOL,
    BYTES,
    BYTES_BUILDER,
    BYTES_VIEW,
    FLOAT32,
    FLOAT64,
    INT64,
    TEXT,
    TEXT_BUILDER,
    TEXT_VIEW,
    UINT64,
    UNIT,
    UTF8_DECODE,
    MIRBasicBlock,
    MIRFunction,
    MIRInstruction,
    MIRParameter,
    MIRRecord,
    MIRTerminator,
    PerformanceMIR,
    PerformanceType,
    SourceMapping,
    record_layout,
)


PERFORMANCE_FRONTEND_SCHEMA_VERSION = 1
PERFORMANCE_FRONTEND_IMPLEMENTATION_VERSION = 12
_SCALARS = {
    "Int64": INT64,
    "UInt64": UINT64,
    "Float32": FLOAT32,
    "Float64": FLOAT64,
    "Bool": BOOL,
    "Unit": UNIT,
    "Bytes": BYTES,
    "BytesBuilder": BYTES_BUILDER,
    "BytesView": BYTES_VIEW,
    "Text": TEXT,
    "TextBuilder": TEXT_BUILDER,
    "TextView": TEXT_VIEW,
    "Utf8Decode": UTF8_DECODE,
}
_BORROWED_VIEW_TYPES = {BYTES_VIEW, TEXT_VIEW}
_OWNED_TRANSFER_TYPES = {BYTES, BYTES_BUILDER, TEXT_BUILDER, TEXT}
_BINARY_OPS = {
    ast.Add: "add",
    ast.Sub: "sub",
    ast.Mult: "mul",
    ast.Div: "div",
    ast.FloorDiv: "div",
    ast.Mod: "mod",
    ast.BitAnd: "bit_and",
    ast.BitOr: "bit_or",
    ast.BitXor: "bit_xor",
    ast.LShift: "shift_left",
    ast.RShift: "shift_right",
}
_COMPARE_OPS = {
    ast.Eq: "eq",
    ast.NotEq: "ne",
    ast.Lt: "lt",
    ast.LtE: "le",
    ast.Gt: "gt",
    ast.GtE: "ge",
}


class PerformanceCompileError(ValueError):
    pass


@dataclass(frozen=True)
class PerformanceFrontendResult:
    mir: PerformanceMIR
    normalized_source: str
    frontend_compatibility: str
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PERFORMANCE_FRONTEND_SCHEMA_VERSION,
            "frontend_compatibility": self.frontend_compatibility,
            "diagnostics": list(self.diagnostics),
            "normalized_source_sha256": hashlib.sha256(
                self.normalized_source.encode("utf-8")
            ).hexdigest(),
            "mir": self.mir.to_dict(),
        }


@dataclass
class _Block:
    id: str
    instructions: list[MIRInstruction]
    terminator: MIRTerminator | None = None


@dataclass(frozen=True)
class _Binding:
    type: PerformanceType
    value: str
    mutable: bool = False
    moved: bool = False
    parameter: bool = False
    borrowed: bool = False
    borrowed_mut: bool = False
    borrow_owner: str | None = None
    borrow_depth: int | None = None
    borrow_id: str | None = None
    root_owner: str | None = None
    borrow_chain: tuple[Any, ...] = ()
    ownership_state: str = "Live"


@dataclass(frozen=True)
class _BorrowState:
    marker: str
    value: str
    attributes: tuple[tuple[str, Any], ...]

    @property
    def attribute_map(self) -> dict[str, Any]:
        return dict(self.attributes)


@dataclass(frozen=True)
class _Signature:
    parameters: tuple[PerformanceType, ...]
    return_type: PerformanceType
    parameter_names: tuple[str, ...] = ()
    pure: bool = True


@dataclass(frozen=True)
class _Preprocessed:
    source: str
    declaration_kinds: dict[tuple[int, str], str]


@dataclass(frozen=True)
class _ReborrowAnalysis:
    parameter_depths: dict[tuple[str, str], int | None]
    root_owners: dict[tuple[str, str], str]
    borrowed_return_origins: dict[str, str]
    borrowed_return_depths: dict[str, int]


def _local_view_owners(function: ast.FunctionDef) -> dict[str, str]:
    owners: dict[str, str] = {}
    for statement in ast.walk(function):
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and isinstance(statement.annotation, ast.Name)
            and statement.annotation.id in {"BytesView", "TextView"}
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)
            and statement.value.func.attr
            in {"slice", "slice_bytes", "as_view", "as_bytes"}
            and isinstance(statement.value.func.value, ast.Name)
        ):
            owners[statement.target.id] = statement.value.func.value.id
    return owners


def _borrowed_call_arguments(
    function: ast.FunctionDef,
    signatures: dict[str, _Signature],
) -> list[tuple[ast.Call, str, int, ast.AST]]:
    calls = []
    for node in ast.walk(function):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in signatures
        ):
            continue
        signature = signatures[node.func.id]
        for index, (argument, parameter_type) in enumerate(
            zip(node.args, signature.parameters, strict=False)
        ):
            if parameter_type in _BORROWED_VIEW_TYPES:
                calls.append((node, node.func.id, index, argument))
    return calls


def _validate_builder_call_graph(
    functions: list[ast.FunctionDef],
    signatures: dict[str, _Signature],
) -> None:
    edges: dict[str, set[str]] = {
        function.name: set() for function in functions
    }
    for function in functions:
        for node in ast.walk(function):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in signatures
            ):
                continue
            signature = signatures[node.func.id]
            if any(
                builder_type in signature.parameters
                for builder_type in {BYTES_BUILDER, TEXT_BUILDER}
            ):
                edges[function.name].add(node.func.id)

    visiting: set[str] = set()
    depths: dict[str, int] = {}

    def depth(function_name: str) -> int:
        if function_name in depths:
            return depths[function_name]
        if function_name in visiting:
            raise PerformanceCompileError(
                "recursive builder direct call chain is unsupported"
            )
        visiting.add(function_name)
        observed = 0
        for callee in edges[function_name]:
            observed = max(observed, 1 + depth(callee))
        visiting.remove(function_name)
        depths[function_name] = observed
        return observed

    if any(depth(function.name) > 2 for function in functions):
        raise PerformanceCompileError(
            "builder direct call chain exceeds maximum depth 2"
        )


def _forwarded_borrow_parameters(
    statements: list[ast.stmt],
    parameter_names: set[str],
    signatures: dict[str, _Signature],
) -> set[str]:
    forwarded = set()
    for statement in statements:
        for node in ast.walk(statement):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in signatures
            ):
                continue
            signature = signatures[node.func.id]
            for argument, parameter_type in zip(
                node.args, signature.parameters, strict=False
            ):
                if (
                    parameter_type in _BORROWED_VIEW_TYPES
                    and isinstance(argument, ast.Name)
                    and argument.id in parameter_names
                ):
                    forwarded.add(argument.id)
    return forwarded



def _borrowed_return_analysis(
    functions: list[ast.FunctionDef],
    signatures: dict[str, _Signature],
) -> tuple[dict[str, str], dict[str, int]]:
    function_by_name = {function.name: function for function in functions}
    origins: dict[str, str] = {}
    depths: dict[str, int] = {}
    visiting: set[str] = set()

    def resolve(function_name: str) -> tuple[str, int]:
        if function_name in origins:
            return origins[function_name], depths[function_name]
        if function_name in visiting:
            raise PerformanceCompileError(
                f"recursive borrowed-return chain involving {function_name}"
            )
        function = function_by_name[function_name]
        signature = signatures[function_name]
        borrowed_parameters = [
            name
            for name, type_ in zip(
                signature.parameter_names,
                signature.parameters,
                strict=True,
            )
            if type_ in _BORROWED_VIEW_TYPES
        ]
        if not borrowed_parameters:
            returned_name = next(
                (
                    expression.id
                    for expression in (
                        [
                            item.value
                            for item in ast.walk(function)
                            if isinstance(item, ast.Return)
                            and isinstance(item.value, ast.Name)
                        ]
                        + (
                            [function.body[-1].value]
                            if function.body
                            and isinstance(function.body[-1], ast.Expr)
                            and isinstance(function.body[-1].value, ast.Name)
                            else []
                        )
                    )
                ),
                "view",
            )
            raise PerformanceCompileError(
                f"BorrowReturnLocalOwnerEscape: borrowed BytesView "
                f"{returned_name} cannot escape {function_name}; no borrowed "
                "BytesView source parameter"
            )
        if len(borrowed_parameters) != 1:
            raise PerformanceCompileError(
                f"AmbiguousBorrowReturnOrigin: {function_name} requires exactly "
                "one borrowed BytesView parameter"
            )
        source_parameter = borrowed_parameters[0]
        parameter_types = dict(
            zip(signature.parameter_names, signature.parameters, strict=True)
        )
        local_values = {
            node.target.id: node.value
            for node in ast.walk(function)
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        }
        visiting.add(function_name)

        def derive(expression: ast.AST, seen: set[str]) -> tuple[str, int] | None:
            if isinstance(expression, ast.Name):
                if parameter_types.get(expression.id) in _BORROWED_VIEW_TYPES:
                    return expression.id, 1
                if expression.id in local_values and expression.id not in seen:
                    return derive(
                        local_values[expression.id], seen | {expression.id}
                    )
                return None
            if (
                isinstance(expression, ast.Call)
                and isinstance(expression.func, ast.Attribute)
                and expression.func.attr
                in {"slice", "slice_bytes", "as_view", "as_bytes"}
            ):
                return derive(expression.func.value, seen)
            if (
                isinstance(expression, ast.Call)
                and isinstance(expression.func, ast.Name)
                and expression.func.id in signatures
                and signatures[expression.func.id].return_type
                in _BORROWED_VIEW_TYPES
            ):
                callee_origin, callee_depth = resolve(expression.func.id)
                callee_signature = signatures[expression.func.id]
                origin_index = callee_signature.parameter_names.index(callee_origin)
                if origin_index >= len(expression.args):
                    return None
                derived = derive(expression.args[origin_index], seen)
                if derived is None:
                    return None
                return derived[0], max(callee_depth + 1, derived[1] + 1)
            return None

        return_expressions = [
            node.value
            for node in ast.walk(function)
            if isinstance(node, ast.Return) and node.value is not None
        ]
        if (
            function.body
            and isinstance(function.body[-1], ast.Expr)
            and function.body[-1].value not in return_expressions
        ):
            return_expressions.append(function.body[-1].value)
        if not return_expressions:
            visiting.remove(function_name)
            raise PerformanceCompileError(
                f"borrowed-return function {function_name} has no return value"
            )
        derived_returns = [
            derive(expression, set()) for expression in return_expressions
        ]
        visiting.remove(function_name)
        if any(item is None for item in derived_returns):
            raise PerformanceCompileError(
                f"BorrowReturnLocalOwnerEscape: {function_name} return is not "
                f"derived from borrowed parameter {source_parameter}"
            )
        return_origins = {item[0] for item in derived_returns if item is not None}
        if return_origins != {source_parameter}:
            raise PerformanceCompileError(
                f"AmbiguousBorrowReturnOrigin: {function_name} returns "
                f"{sorted(return_origins)}"
            )
        maximum_depth = max(item[1] for item in derived_returns if item is not None)
        if maximum_depth > 2:
            raise PerformanceCompileError(
                f"borrowed-return chain exceeds 2 at {function_name}"
            )
        origins[function_name] = source_parameter
        depths[function_name] = maximum_depth
        return source_parameter, maximum_depth

    for function in functions:
        if signatures[function.name].return_type in _BORROWED_VIEW_TYPES:
            resolve(function.name)
    return origins, depths

def _validate_reborrow_graph(
    functions: list[ast.FunctionDef],
    signatures: dict[str, _Signature],
) -> _ReborrowAnalysis:
    borrowed_return_origins, borrowed_return_depths = (
        _borrowed_return_analysis(functions, signatures)
    )
    function_by_name = {function.name: function for function in functions}
    borrowed_parameters = {
        (function.name, parameter_name)
        for function in functions
        for parameter_name, parameter_type in zip(
            signatures[function.name].parameter_names,
            signatures[function.name].parameters,
            strict=True,
        )
        if parameter_type in _BORROWED_VIEW_TYPES
    }
    forwarding_edges: list[
        tuple[tuple[str, str], tuple[str, str]]
    ] = []
    borrowed_call_edges: dict[str, set[str]] = {
        function.name: set() for function in functions
    }
    root_seeds: dict[tuple[str, str], set[tuple[int, str]]] = {}
    incoming: dict[tuple[str, str], int] = {
        key: 0 for key in borrowed_parameters
    }
    for function in functions:
        signature = signatures[function.name]
        parameter_types = dict(
            zip(signature.parameter_names, signature.parameters, strict=True)
        )
        local_owners = _local_view_owners(function)
        local_values = {
            item.target.id: item.value
            for item in ast.walk(function)
            if isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and item.value is not None
        }

        def forwarded_parameter(
            expression: ast.AST, seen: set[str]
        ) -> str | None:
            if isinstance(expression, ast.Name):
                if (
                    parameter_types.get(expression.id)
                    in _BORROWED_VIEW_TYPES
                ):
                    return expression.id
                if expression.id in local_values and expression.id not in seen:
                    return forwarded_parameter(
                        local_values[expression.id], seen | {expression.id}
                    )
                return None
            if (
                isinstance(expression, ast.Call)
                and isinstance(expression.func, ast.Attribute)
                and expression.func.attr == "slice"
            ):
                return forwarded_parameter(expression.func.value, seen)
            if (
                isinstance(expression, ast.Call)
                and isinstance(expression.func, ast.Name)
                and expression.func.id in borrowed_return_origins
            ):
                callee_signature = signatures[expression.func.id]
                origin_index = callee_signature.parameter_names.index(
                    borrowed_return_origins[expression.func.id]
                )
                if origin_index < len(expression.args):
                    return forwarded_parameter(
                        expression.args[origin_index], seen
                    )
            return None
        for call, callee, index, argument in _borrowed_call_arguments(
            function, signatures
        ):
            borrowed_call_edges[function.name].add(callee)
            target_name = signatures[callee].parameter_names[index]
            target = (callee, target_name)
            forwarded = forwarded_parameter(argument, set())
            if forwarded is not None:
                source = (function.name, forwarded)
                forwarding_edges.append((source, target))
                incoming[target] = incoming.get(target, 0) + 1
                continue
            root_owner = None
            if (
                isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Attribute)
                and argument.func.attr
                in {"slice", "slice_bytes", "as_view", "as_bytes"}
                and isinstance(argument.func.value, ast.Name)
            ):
                root_owner = f"{function.name}.{argument.func.value.id}"
            elif isinstance(argument, ast.Name) and argument.id in local_owners:
                root_owner = f"{function.name}.{local_owners[argument.id]}"
            if root_owner is not None:
                root_seeds.setdefault(target, set()).add((1, root_owner))
        borrowed_names = {
            name
            for name, type_ in parameter_types.items()
            if type_ in _BORROWED_VIEW_TYPES
        }
        for branch in (
            node
            for node in ast.walk(function)
            if isinstance(node, ast.If)
        ):
            then_forwarded = _forwarded_borrow_parameters(
                branch.body, borrowed_names, signatures
            )
            else_forwarded = _forwarded_borrow_parameters(
                branch.orelse, borrowed_names, signatures
            )
            if then_forwarded != else_forwarded:
                names = ", ".join(sorted(then_forwarded ^ else_forwarded))
                raise PerformanceCompileError(
                    f"child reborrow for {names} must end on every conditional branch"
                )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(function_name: str) -> None:
        if function_name in visiting:
            raise PerformanceCompileError(
                f"recursive borrowed call chain involving {function_name}"
            )
        if function_name in visited:
            return
        visiting.add(function_name)
        for callee in borrowed_call_edges.get(function_name, ()):
            visit(callee)
        visiting.remove(function_name)
        visited.add(function_name)

    for function_name in function_by_name:
        visit(function_name)
    states = {
        key: set(values) for key, values in root_seeds.items()
    }
    for key in borrowed_parameters:
        if incoming.get(key, 0) == 0 and key not in root_seeds:
            states.setdefault(key, set()).add(
                (1, f"external:{key[0]}.{key[1]}")
            )
    changed = True
    while changed:
        changed = False
        for source, target in forwarding_edges:
            for depth, root_owner in states.get(source, ()):
                child = (depth + 1, root_owner)
                target_states = states.setdefault(target, set())
                if child not in target_states:
                    target_states.add(child)
                    changed = True
    parameter_depths: dict[tuple[str, str], int | None] = {}
    root_owners: dict[tuple[str, str], str] = {}
    for key in borrowed_parameters:
        values = states.get(key) or {(1, f"external:{key[0]}.{key[1]}")}
        if any(depth > 3 for depth, _root in values):
            raise PerformanceCompileError(
                f"BytesView reborrow depth exceeds 3 at {key[0]}.{key[1]}"
            )
        depths = {depth for depth, _root in values}
        roots = {root for _depth, root in values}
        parameter_depths[key] = next(iter(depths)) if len(depths) == 1 else None
        root_owners[key] = (
            next(iter(roots))
            if len(roots) == 1
            else f"dynamic_root:{key[0]}.{key[1]}"
        )
    return _ReborrowAnalysis(
        parameter_depths,
        root_owners,
        borrowed_return_origins,
        borrowed_return_depths,
    )


def _preprocess(source: str) -> _Preprocessed:
    output = []
    declaration_kinds: dict[tuple[int, str], str] = {}
    for line_number, original in enumerate(source.splitlines(), 1):
        prefix = original[: len(original) - len(original.lstrip())]
        stripped = original.strip()
        line = original
        if stripped.startswith("record ") and stripped.endswith(":"):
            line = prefix + "class " + stripped[len("record ") :]
        elif stripped.startswith("fn "):
            line = prefix + "def " + stripped[len("fn ") :]
        else:
            declaration = re.match(r"^(let|var)\s+([A-Za-z_]\w*)", stripped)
            if declaration:
                kind, name = declaration.groups()
                declaration_kinds[(line_number, name)] = kind
                line = prefix + stripped[len(kind) + 1 :]
            range_match = re.match(
                r"^for\s+([A-Za-z_]\w*)\s+in\s+(.+?)\.\.(.+):$", line.strip()
            )
            if range_match:
                name, start, end = range_match.groups()
                line = prefix + f"for {name} in meldra_range({start}, {end}):"
        line = re.sub(r"\btrue\b", "True", line)
        line = re.sub(r"\bfalse\b", "False", line)
        output.append(line)
    return _Preprocessed("\n".join(output) + "\n", declaration_kinds)


def _source(path: str, node: ast.AST) -> SourceMapping:
    return SourceMapping(
        path,
        int(getattr(node, "lineno", 1)),
        int(getattr(node, "col_offset", 0)),
        int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
        int(getattr(node, "end_col_offset", getattr(node, "col_offset", 0))),
    )


def _annotation_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript):
        if isinstance(node.value, ast.Name):
            inner = node.slice
            if isinstance(inner, ast.Tuple):
                values = ",".join(_annotation_name(item) for item in inner.elts)
            else:
                values = _annotation_name(inner)
            return f"{node.value.id}[{values}]"
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return str(node.value)
    raise PerformanceCompileError("unsupported type annotation")


def _parse_type(node: ast.AST, records: set[str]) -> PerformanceType:
    if isinstance(node, ast.Name):
        if node.id in _SCALARS:
            return _SCALARS[node.id]
        if node.id in records:
            return PerformanceType("record", record=node.id)
        raise PerformanceCompileError(f"unknown type: {node.id}")
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
        constructor = node.value.id
        values = node.slice.elts if isinstance(node.slice, ast.Tuple) else (node.slice,)
        if constructor == "Array" and len(values) == 2:
            element = _parse_type(values[0], records)
            if element.kind in {"array", "slice"}:
                raise PerformanceCompileError(
                    "nested collection ownership is outside Stage 0.6P"
                )
            if not isinstance(values[1], ast.Constant) or not isinstance(values[1].value, int):
                raise PerformanceCompileError("Array length must be an integer constant")
            return PerformanceType("array", element=element, length=values[1].value)
        if constructor == "Slice" and len(values) == 1:
            element = _parse_type(values[0], records)
            if element.kind in {"array", "slice"}:
                raise PerformanceCompileError(
                    "nested collection ownership is outside Stage 0.6P"
                )
            return PerformanceType("slice", element=element)
        if constructor == "Shared" and len(values) == 1:
            inner = _parse_type(values[0], records)
            if inner.kind == "record":
                raise PerformanceCompileError(
                    "SharedCycleUnsupported: Shared records require cycle-aware indirection"
                )
            if inner.kind not in {"array", "slice"}:
                raise PerformanceCompileError(
                    "Shared is limited to arrays and slices in Stage 0.6P"
                )
            return replace(inner, shared=True)
    raise PerformanceCompileError(f"unsupported type: {_annotation_name(node)}")


class _FunctionLowerer:
    def __init__(
        self,
        node: ast.FunctionDef,
        *,
        path: str,
        records: dict[str, MIRRecord],
        signatures: dict[str, _Signature],
        declaration_kinds: dict[tuple[int, str], str],
        reborrow_analysis: _ReborrowAnalysis,
    ) -> None:
        self.node = node
        self.path = path
        self.records = records
        self.signatures = signatures
        self.declaration_kinds = declaration_kinds
        self.reborrow_analysis = reborrow_analysis
        self.blocks: list[_Block] = []
        self.block_counter = 0
        self.value_counter = 0
        self.instruction_counter = 0
        self.current = self._new_block("entry")
        self.bindings: dict[str, _Binding] = {}
        self.parameters: list[MIRParameter] = []
        self.range_bounds: dict[str, tuple[str, str]] = {}
        self.view_borrows: dict[str, tuple[str, int, int]] = {}
        self.returned_borrows: dict[str, tuple[_BorrowState, ...]] = {}
        self.returned_view_metadata: dict[str, dict[str, Any]] = {}
        self.local_last_uses: dict[str, int] = {}
        self.return_call_bindings: dict[tuple[int, int], tuple[str, int]] = {}
        self.pending_builder_views: dict[str, _BorrowState] = {}
        self.pending_text_views: dict[str, _BorrowState] = {}
        for declaration in (
            item
            for item in ast.walk(node)
            if isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
            and item.value is not None
        ):
            local_name = declaration.target.id
            uses = [
                int(getattr(candidate, "lineno", declaration.lineno))
                for candidate in ast.walk(node)
                if isinstance(candidate, ast.Name)
                and isinstance(candidate.ctx, ast.Load)
                and candidate.id == local_name
            ]
            last_use = max(uses, default=int(declaration.lineno))
            self.local_last_uses[local_name] = last_use
            for call in (
                candidate
                for candidate in ast.walk(declaration.value)
                if isinstance(candidate, ast.Call)
                and isinstance(candidate.func, ast.Name)
                and candidate.func.id
                in reborrow_analysis.borrowed_return_origins
            ):
                self.return_call_bindings[
                    (int(call.lineno), int(call.col_offset))
                ] = (local_name, last_use)
        for call in (
            item
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id in reborrow_analysis.borrowed_return_origins
            and (int(item.lineno), int(item.col_offset))
            not in self.return_call_bindings
        ):
            last_use = int(
                next(
                    (
                        statement.end_lineno
                        for statement in ast.walk(node)
                        if isinstance(statement, ast.Expr)
                        and statement.value is call
                    ),
                    call.lineno,
                )
            )
            self.return_call_bindings[
                (int(call.lineno), int(call.col_offset))
            ] = (
                f"ephemeral_{call.lineno}_{call.col_offset}",
                last_use,
            )
        local_view_owners = _local_view_owners(node)

        def root_view_owner(name: str) -> str:
            seen: set[str] = set()
            owner = name
            while owner in local_view_owners and owner not in seen:
                seen.add(owner)
                owner = local_view_owners[owner]
            return owner

        for statement in ast.walk(node):
            if (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and isinstance(statement.annotation, ast.Name)
                and statement.annotation.id in {"BytesView", "TextView"}
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
                and statement.value.func.attr
                in {"slice", "slice_bytes", "as_view", "as_bytes"}
                and isinstance(statement.value.func.value, ast.Name)
            ):
                view_name = statement.target.id
                owner_name = root_view_owner(
                    statement.value.func.value.id
                )
                uses = [
                    int(getattr(candidate, "lineno", statement.lineno))
                    for candidate in ast.walk(node)
                    if isinstance(candidate, ast.Name)
                    and isinstance(candidate.ctx, ast.Load)
                    and candidate.id == view_name
                ]
                self.view_borrows[view_name] = (
                    owner_name,
                    int(statement.lineno),
                    max(uses, default=int(statement.lineno)),
                )
        signature = signatures[node.name]
        for argument, type_ in zip(node.args.args, signature.parameters, strict=True):
            value = self._value(argument.arg)
            if type_ in _OWNED_TRANSFER_TYPES:
                ownership = "moved"
            elif type_ in _BORROWED_VIEW_TYPES:
                ownership = "borrowed"
            else:
                ownership = "shared" if type_.shared else "borrowed"
            self.parameters.append(
                MIRParameter(argument.arg, value, type_, ownership)
            )
            parameter_key = (node.name, argument.arg)
            self.bindings[argument.arg] = _Binding(
                type_,
                value,
                parameter=True,
                borrowed=type_ in _BORROWED_VIEW_TYPES,
                borrow_depth=(
                    reborrow_analysis.parameter_depths.get(parameter_key)
                    if type_ in _BORROWED_VIEW_TYPES
                    else None
                ),
                borrow_id=(
                    f"parameter:{node.name}.{argument.arg}"
                    if type_ in _BORROWED_VIEW_TYPES
                    else None
                ),
                root_owner=(
                    reborrow_analysis.root_owners.get(parameter_key)
                    if type_ in _BORROWED_VIEW_TYPES
                    else None
                ),
            )

    def _new_block(self, stem: str) -> _Block:
        self.block_counter += 1
        block = _Block(f"bb{self.block_counter}_{stem}", [])
        self.blocks.append(block)
        return block

    def _value(self, stem: str = "v") -> str:
        self.value_counter += 1
        return f"%{stem}_{self.value_counter}"

    def _emit(
        self,
        op: str,
        *,
        type_: PerformanceType | None = None,
        operands: Iterable[str] = (),
        attributes: dict[str, Any] | Iterable[tuple[str, Any]] = (),
        node: ast.AST | None = None,
        result: bool = True,
    ) -> str | None:
        self.instruction_counter += 1
        value = self._value() if result else None
        instruction = MIRInstruction(
            f"i{self.instruction_counter}",
            op,
            value,
            type_,
            tuple(operands),
            tuple(attributes.items()) if isinstance(attributes, dict) else tuple(attributes),
            _source(self.path, node) if node is not None else None,
        )
        self.current.instructions.append(instruction)
        return value

    def _returned_call_scope(self, node: ast.Call) -> tuple[str, int]:
        binding = self.return_call_bindings.get(
            (int(node.lineno), int(node.col_offset))
        )
        if binding is None:
            return (
                f"{self.node.name}:ephemeral:{node.lineno}:{node.col_offset}",
                int(node.lineno),
            )
        name, last_use = binding
        return f"{self.node.name}.{name}", last_use

    def _end_borrow_chain(
        self,
        chain: tuple[_BorrowState, ...],
        node: ast.AST,
    ) -> None:
        for state in reversed(chain):
            attributes = state.attribute_map
            end_op = (
                "reborrow_end"
                if state.marker == "reborrow_argument"
                else "borrow_end"
            )
            self._emit(
                end_op,
                operands=(state.value,),
                attributes={
                    **attributes,
                    "end_order": "child_before_parent",
                },
                node=node,
                result=False,
            )

    def _close_statement_borrows(self, node: ast.stmt) -> None:
        line = int(getattr(node, "end_lineno", node.lineno))
        for name, binding in tuple(self.bindings.items()):
            chain_last_use = max(
                (
                    int(state.attribute_map["last_use_line"])
                    for state in binding.borrow_chain
                    if isinstance(
                        state.attribute_map.get("last_use_line"), int
                    )
                ),
                default=self.local_last_uses.get(name, line),
            )
            if binding.borrow_chain and chain_last_use <= line:
                self._end_borrow_chain(binding.borrow_chain, node)
                self.bindings[name] = replace(binding, borrow_chain=())
        for value, chain in tuple(self.returned_borrows.items()):
            self.returned_view_metadata.pop(value, None)
            self._end_borrow_chain(chain, node)
            del self.returned_borrows[value]
        for value, state in tuple(self.pending_builder_views.items()):
            self._end_borrow_chain((state,), node)
            del self.pending_builder_views[value]
        for value, state in tuple(self.pending_text_views.items()):
            self._end_borrow_chain((state,), node)
            del self.pending_text_views[value]

    def _terminate(self, terminator: MIRTerminator) -> None:
        if self.current.terminator is not None:
            raise PerformanceCompileError(f"block {self.current.id} already terminated")
        self.current.terminator = terminator

    def _binding_value(self, name: str, node: ast.AST) -> tuple[str, PerformanceType]:
        try:
            binding = self.bindings[name]
        except KeyError as exc:
            raise PerformanceCompileError(f"unknown value {name} at line {node.lineno}") from exc
        if binding.moved:
            if binding.type == BYTES_BUILDER:
                raise PerformanceCompileError(
                    f"use after {binding.ownership_state.lower()}: {name} "
                    f"at line {node.lineno}"
                )
            raise PerformanceCompileError(
                f"use after move: {name} at line {node.lineno}"
            )
        if binding.mutable:
            value = self._emit(
                "load_local", type_=binding.type, operands=(binding.value,), node=node
            )
            return str(value), binding.type
        return binding.value, binding.type
    def _ensure_bytes_unborrowed(
        self, owner: str, node: ast.AST, action: str
    ) -> None:
        line = int(getattr(node, "lineno", 0))
        for view, (borrow_owner, declared, last_use) in self.view_borrows.items():
            if borrow_owner == owner and declared <= line < last_use:
                raise PerformanceCompileError(
                    f"cannot {action} Bytes owner {owner} while view {view} is live "
                    f"(last use line {last_use})"
                )

    def _ensure_builder_unborrowed(
        self, owner: str, node: ast.AST, action: str
    ) -> None:
        line = int(getattr(node, "lineno", 0))
        for view, (borrow_owner, declared, last_use) in self.view_borrows.items():
            if borrow_owner == owner and declared <= line <= last_use:
                raise PerformanceCompileError(
                    f"cannot {action} BytesBuilder {owner} while view {view} "
                    f"is live (last use line {last_use})"
                )

    def _ensure_text_unborrowed(
        self, owner: str, node: ast.AST, action: str
    ) -> None:
        line = int(getattr(node, "lineno", 0))
        for view, (
            borrow_owner,
            declared,
            last_use,
        ) in self.view_borrows.items():
            if borrow_owner == owner and declared <= line < last_use:
                raise PerformanceCompileError(
                    f"cannot {action} Text owner {owner} while TextView "
                    f"{view} is live (last use line {last_use})"
                )

    def _constant(
        self, node: ast.Constant, expected: PerformanceType | None
    ) -> tuple[str, PerformanceType]:
        value = node.value
        if isinstance(value, bool):
            type_ = BOOL
        elif isinstance(value, int):
            type_ = expected if expected and expected.kind in {"int", "uint"} else INT64
        elif isinstance(value, float):
            type_ = expected if expected and expected.kind == "float" else FLOAT64
        else:
            raise PerformanceCompileError(
                f"unsupported literal {value!r} at line {node.lineno}"
            )
        result = self._emit("const", type_=type_, attributes={"value": value}, node=node)
        return str(result), type_

    def expression(
        self, node: ast.AST, expected: PerformanceType | None = None
    ) -> tuple[str, PerformanceType]:
        if isinstance(node, ast.Constant):
            return self._constant(node, expected)
        if isinstance(node, ast.Name):
            return self._binding_value(node.id, node)
        if isinstance(node, ast.BinOp):
            left, left_type = self.expression(node.left, expected)
            right, right_type = self.expression(node.right, left_type)
            if left_type != right_type:
                raise PerformanceCompileError(f"binary type mismatch at line {node.lineno}")
            try:
                op = _BINARY_OPS[type(node.op)]
            except KeyError as exc:
                raise PerformanceCompileError(f"unsupported binary operator at line {node.lineno}") from exc
            if left_type.kind == "float" and op not in {
                "add",
                "sub",
                "mul",
                "div",
            }:
                raise PerformanceCompileError(
                    f"operator {op} is invalid for {left_type.name}"
                )
            if left_type.kind in {"int", "uint"}:
                pass
            elif left_type.kind != "float":
                raise PerformanceCompileError(
                    f"binary operator {op} requires numeric operands"
                )
            result = self._emit(
                "binary", type_=left_type, operands=(left, right), attributes={"operator": op}, node=node
            )
            return str(result), left_type
        if isinstance(node, ast.UnaryOp):
            value, type_ = self.expression(node.operand, expected)
            if isinstance(node.op, ast.USub):
                if type_.kind not in {"int", "uint", "float"}:
                    raise PerformanceCompileError("negation requires a numeric value")
                op = "neg"
            elif isinstance(node.op, ast.Not):
                if type_ != BOOL:
                    raise PerformanceCompileError("not requires Bool")
                op = "not"
            elif isinstance(node.op, ast.Invert):
                if type_.kind not in {"int", "uint"}:
                    raise PerformanceCompileError("bitwise not requires an integer")
                op = "bit_not"
            else:
                raise PerformanceCompileError(f"unsupported unary operator at line {node.lineno}")
            result = self._emit("unary", type_=type_, operands=(value,), attributes={"operator": op}, node=node)
            return str(result), type_
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise PerformanceCompileError("chained comparisons are outside Stage 0.5P")
            left, left_type = self.expression(node.left)
            right, right_type = self.expression(node.comparators[0], left_type)
            if left_type != right_type:
                raise PerformanceCompileError(f"comparison type mismatch at line {node.lineno}")
            if left_type.kind not in {"int", "uint", "float", "bool"}:
                raise PerformanceCompileError(
                    f"comparison is unsupported for {left_type.name}"
                )
            try:
                op = _COMPARE_OPS[type(node.ops[0])]
            except KeyError as exc:
                raise PerformanceCompileError(f"unsupported comparison at line {node.lineno}") from exc
            result = self._emit("compare", type_=BOOL, operands=(left, right), attributes={"operator": op}, node=node)
            return str(result), BOOL
        if isinstance(node, ast.BoolOp):
            if len(node.values) < 2:
                raise PerformanceCompileError("invalid boolean expression")
            typed_values = [self.expression(item, BOOL) for item in node.values]
            if any(type_ != BOOL for _, type_ in typed_values):
                raise PerformanceCompileError("boolean operators require Bool")
            values = [value for value, _ in typed_values]
            op = "and" if isinstance(node.op, ast.And) else "or"
            current = values[0]
            for value in values[1:]:
                current = str(self._emit("binary", type_=BOOL, operands=(current, value), attributes={"operator": op}, node=node))
            return current, BOOL
        if isinstance(node, ast.List):
            element_type = expected.element if expected and expected.kind == "array" else None
            items: list[str] = []
            for item in node.elts:
                value, observed_type = self.expression(item, element_type)
                element_type = element_type or observed_type
                if observed_type != element_type:
                    raise PerformanceCompileError(f"heterogeneous array at line {node.lineno}")
                items.append(value)
            element_type = element_type or INT64
            if expected and expected.kind == "array" and expected.length == len(items):
                type_ = expected
            else:
                type_ = PerformanceType("array", element=element_type, length=len(items))
            allocation = str(self._emit("alloc_heap", type_=type_, attributes={"reason": "unclassified_array_literal", "bytes": 0}, node=node))
            result = self._emit("array_init", type_=type_, operands=(allocation, *items), attributes={"length": len(items)}, node=node)
            return str(result), type_
        if isinstance(node, ast.Subscript):
            aggregate, aggregate_type = self.expression(node.value)
            if aggregate_type in {BYTES, BYTES_VIEW}:
                index, index_type = self.expression(node.slice, UINT64)
                if index_type.kind not in {"int", "uint"}:
                    raise PerformanceCompileError(
                        f"non-integer byte index at line {node.lineno}"
                    )
                length = str(
                    self._emit("bytes_len", type_=UINT64, operands=(aggregate,), node=node)
                )
                self._emit(
                    "bytes_bounds_check",
                    operands=(index, length),
                    attributes={"diagnostic": "BytesIndexOutOfBounds"},
                    node=node,
                    result=False,
                )
                result = self._emit(
                    "bytes_load", type_=UINT64, operands=(aggregate, index), node=node
                )
                return str(result), UINT64
            if aggregate_type.kind not in {"array", "slice"}:
                raise PerformanceCompileError(f"indexing non-array at line {node.lineno}")
            index, index_type = self.expression(node.slice, UINT64)
            if index_type.kind not in {"int", "uint"}:
                raise PerformanceCompileError(f"non-integer index at line {node.lineno}")
            length = str(self._emit("array_len", type_=UINT64, operands=(aggregate,), node=node))
            check_attributes: dict[str, Any] = {"proven": False, "aggregate": aggregate}
            if isinstance(node.slice, ast.Name) and node.slice.id in self.range_bounds:
                range_end, iterator_slot = self.range_bounds[node.slice.id]
                check_attributes.update({"range_end": range_end, "iterator_slot": iterator_slot})
            self._emit("bounds_check", operands=(index, length), attributes=check_attributes, node=node, result=False)
            result = self._emit("index_load", type_=aggregate_type.element, operands=(aggregate, index), node=node)
            return str(result), aggregate_type.element
        if isinstance(node, ast.Attribute):
            aggregate, aggregate_type = self.expression(node.value)
            if aggregate_type.kind != "record":
                raise PerformanceCompileError(f"field access on non-record at line {node.lineno}")
            record = self.records[aggregate_type.record or ""]
            field_types = dict(record.fields)
            if node.attr not in field_types:
                raise PerformanceCompileError(f"unknown field {node.attr} at line {node.lineno}")
            result = self._emit("field_load", type_=field_types[node.attr], operands=(aggregate,), attributes={"field": node.attr}, node=node)
            return str(result), field_types[node.attr]
        if isinstance(node, ast.IfExp):
            raise PerformanceCompileError("conditional expressions are outside Stage 0.5P")
        if isinstance(node, ast.Call):
            return self._call(node, expected)
        raise PerformanceCompileError(
            f"unsupported expression {type(node).__name__} at line {getattr(node, 'lineno', '?')}"
        )

    def _call(
        self, node: ast.Call, expected: PerformanceType | None
    ) -> tuple[str, PerformanceType]:
        if isinstance(node.func, ast.Attribute):
            attribute = node.func.attr
            receiver = node.func.value
            if (
                attribute == "new"
                and isinstance(receiver, ast.Name)
                and receiver.id == "Bytes"
                and len(node.args) == 1
                and not node.keywords
            ):
                length, length_type = self.expression(node.args[0], UINT64)
                if length_type != UINT64:
                    raise PerformanceCompileError("Bytes.new length must be UInt64")
                result = self._emit(
                    "bytes_new",
                    type_=BYTES,
                    operands=(length,),
                    attributes={
                        "representation": "owned_pointer_length_capacity_v1",
                        "overflow_diagnostic": "BytesAllocationOverflow",
                    },
                    node=node,
                )
                return str(result), BYTES
            if (
                isinstance(receiver, ast.Name)
                and receiver.id == "BytesBuilder"
                and not node.keywords
                and (
                    (attribute == "new" and not node.args)
                    or (attribute == "with_capacity" and len(node.args) == 1)
                )
            ):
                if attribute == "new":
                    capacity = str(
                        self._emit(
                            "const",
                            type_=UINT64,
                            attributes={"value": 0},
                            node=node,
                        )
                    )
                else:
                    capacity, capacity_type = self.expression(
                        node.args[0], UINT64
                    )
                    if capacity_type != UINT64:
                        raise PerformanceCompileError(
                            "BytesBuilder.with_capacity requires UInt64"
                        )
                result = self._emit(
                    "builder_create",
                    type_=BYTES_BUILDER,
                    operands=(capacity,),
                    attributes={
                        "representation": "unique_pointer_length_capacity_state_v1",
                        "growth_policy": "zero_then_max_8_required_then_double",
                        "initial_state": "Live",
                        "overflow_diagnostic": "BytesBuilderAllocationSizeOverflow",
                    },
                    node=node,
                )
                return str(result), BYTES_BUILDER
            if (
                isinstance(receiver, ast.Name)
                and receiver.id == "TextBuilder"
                and not node.keywords
                and (
                    (attribute == "new" and not node.args)
                    or (
                        attribute == "with_capacity_bytes"
                        and len(node.args) == 1
                    )
                )
            ):
                if attribute == "new":
                    capacity = str(
                        self._emit(
                            "const",
                            type_=UINT64,
                            attributes={"value": 0},
                            node=node,
                        )
                    )
                else:
                    capacity, capacity_type = self.expression(
                        node.args[0], UINT64
                    )
                    if capacity_type != UINT64:
                        raise PerformanceCompileError(
                            "TextBuilder.with_capacity_bytes requires UInt64"
                        )
                result = self._emit(
                    "text_builder_create",
                    type_=TEXT_BUILDER,
                    operands=(capacity,),
                    attributes={
                        "representation": (
                            "reused_bytes_builder_"
                            "unique_pointer_length_capacity_state_v1"
                        ),
                        "growth_policy": (
                            "zero_then_max_8_required_then_double"
                        ),
                        "initial_state": "Live",
                        "utf8_invariant": "payload_0_to_length_valid",
                        "overflow_diagnostic": (
                            "TextBuilderAllocationSizeOverflow"
                        ),
                    },
                    node=node,
                )
                return str(result), TEXT_BUILDER
            if (
                attribute == "from_utf8"
                and isinstance(receiver, ast.Name)
                and receiver.id == "Text"
                and len(node.args) == 1
                and not node.keywords
            ):
                if not (
                    isinstance(node.args[0], ast.Call)
                    and isinstance(node.args[0].func, ast.Name)
                    and node.args[0].func.id == "move"
                ):
                    raise PerformanceCompileError(
                        "Text.from_utf8 requires explicit move(Bytes)"
                    )
                source, source_type = self.expression(node.args[0], BYTES)
                if source_type != BYTES:
                    raise PerformanceCompileError(
                        "Text.from_utf8 requires owned Bytes"
                    )
                self._emit(
                    "bytes_to_text_transfer",
                    operands=(source,),
                    attributes={
                        "payload_copies": 0,
                        "input_consumed": True,
                        "result": "Utf8Decode",
                    },
                    node=node,
                    result=False,
                )
                result = self._emit(
                    "utf8_validate",
                    type_=UTF8_DECODE,
                    operands=(source,),
                    attributes={
                        "algorithm": "rfc3629",
                        "invalid_offset": "first_invalid_byte",
                        "payload_copies": 0,
                    },
                    node=node,
                )
                return str(result), UTF8_DECODE
            if (
                attribute == "from_ascii"
                and isinstance(receiver, ast.Name)
                and receiver.id == "Text"
                and len(node.args) == 1
                and not node.keywords
            ):
                source, source_type = self.expression(node.args[0], UINT64)
                if source_type != UINT64:
                    raise PerformanceCompileError(
                        "Text.from_ascii requires UInt64"
                    )
                result = self._emit(
                    "text_from_ascii",
                    type_=TEXT,
                    operands=(source,),
                    attributes={"encoding": "ascii", "payload_copies": 1},
                    node=node,
                )
                return str(result), TEXT
            if (
                attribute == "from_scalar"
                and isinstance(receiver, ast.Name)
                and receiver.id == "Text"
                and len(node.args) == 1
                and not node.keywords
            ):
                scalar, scalar_type = self.expression(node.args[0], UINT64)
                if scalar_type != UINT64:
                    raise PerformanceCompileError(
                        "Text.from_scalar requires UInt64"
                    )
                result = self._emit(
                    "text_from_scalar",
                    type_=TEXT,
                    operands=(scalar,),
                    attributes={
                        "encoding": "utf8",
                        "diagnostic": "InvalidUnicodeScalar",
                    },
                    node=node,
                )
                return str(result), TEXT
            if (
                attribute == "from_surrogate"
                and isinstance(receiver, ast.Name)
                and receiver.id == "Text"
                and len(node.args) == 2
                and not node.keywords
            ):
                high, high_type = self.expression(node.args[0], UINT64)
                low, low_type = self.expression(node.args[1], UINT64)
                if high_type != UINT64 or low_type != UINT64:
                    raise PerformanceCompileError(
                        "Text.from_surrogate requires two UInt64 values"
                    )
                result = self._emit(
                    "text_from_surrogate",
                    type_=TEXT,
                    operands=(high, low),
                    attributes={
                        "encoding": "utf8",
                        "diagnostic": "InvalidUnicodeSurrogatePair",
                    },
                    node=node,
                )
                return str(result), TEXT
            aggregate, aggregate_type = self.expression(receiver)
            if aggregate_type == BYTES_BUILDER:
                if not isinstance(receiver, ast.Name):
                    raise PerformanceCompileError(
                        "BytesBuilder methods require a named unique owner"
                    )
                builder_name = receiver.id
                owner = f"{self.node.name}.{builder_name}"
                common_attributes = {
                    "builder_owner": owner,
                    "growth_policy": "zero_then_max_8_required_then_double",
                }
                if attribute == "len" and not node.args and not node.keywords:
                    result = self._emit(
                        "builder_len",
                        type_=UINT64,
                        operands=(aggregate,),
                        attributes=common_attributes,
                        node=node,
                    )
                    return str(result), UINT64
                if (
                    attribute == "capacity"
                    and not node.args
                    and not node.keywords
                ):
                    result = self._emit(
                        "builder_capacity",
                        type_=UINT64,
                        operands=(aggregate,),
                        attributes=common_attributes,
                        node=node,
                    )
                    return str(result), UINT64
                if (
                    attribute == "reserve"
                    and len(node.args) == 1
                    and not node.keywords
                ):
                    self._ensure_builder_unborrowed(
                        builder_name, node, "reserve"
                    )
                    additional, additional_type = self.expression(
                        node.args[0], UINT64
                    )
                    if additional_type != UINT64:
                        raise PerformanceCompileError(
                            "BytesBuilder.reserve requires UInt64"
                        )
                    self._emit(
                        "builder_reserve",
                        operands=(aggregate, additional),
                        attributes={
                            **common_attributes,
                            "guarantee": "capacity_at_least_length_plus_additional",
                        },
                        node=node,
                        result=False,
                    )
                    self._emit(
                        "builder_grow",
                        operands=(aggregate, additional),
                        attributes={
                            **common_attributes,
                            "reason": "reserve",
                            "overflow_checks": (
                                "length_plus_additional",
                                "capacity_times_two",
                                "allocation_byte_size",
                            ),
                        },
                        node=node,
                        result=False,
                    )
                    for event in ("allocation", "payload_copy", "free"):
                        self._emit(
                            event,
                            operands=(aggregate, additional),
                            attributes={
                                **common_attributes,
                                "reason": "reserve_growth",
                                "condition": "required_exceeds_capacity",
                            },
                            node=node,
                            result=False,
                        )
                    return additional, UNIT
                if (
                    attribute == "push"
                    and len(node.args) == 1
                    and not node.keywords
                ):
                    self._ensure_builder_unborrowed(
                        builder_name, node, "push"
                    )
                    if (
                        isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, int)
                        and not 0 <= node.args[0].value <= 255
                    ):
                        raise PerformanceCompileError(
                            "BytesBuilder.push byte must be in 0..255"
                        )
                    byte, byte_type = self.expression(node.args[0], UINT64)
                    if byte_type != UINT64:
                        raise PerformanceCompileError(
                            "BytesBuilder.push requires UInt64 byte"
                        )
                    one = str(
                        self._emit(
                            "const",
                            type_=UINT64,
                            attributes={"value": 1},
                            node=node,
                        )
                    )
                    self._emit(
                        "builder_grow",
                        operands=(aggregate, one),
                        attributes={
                            **common_attributes,
                            "reason": "push",
                            "overflow_checks": (
                                "length_plus_additional",
                                "capacity_times_two",
                                "allocation_byte_size",
                            ),
                        },
                        node=node,
                        result=False,
                    )
                    for event in ("allocation", "payload_copy", "free"):
                        self._emit(
                            event,
                            operands=(aggregate, one),
                            attributes={
                                **common_attributes,
                                "reason": "push_growth",
                                "condition": "required_exceeds_capacity",
                            },
                            node=node,
                            result=False,
                        )
                    self._emit(
                        "builder_push",
                        operands=(aggregate, byte),
                        attributes={
                            **common_attributes,
                            "byte_range_diagnostic": "BytesBuilderByteOutOfRange",
                        },
                        node=node,
                        result=False,
                    )
                    return byte, UNIT
                if (
                    attribute == "extend"
                    and len(node.args) == 1
                    and not node.keywords
                ):
                    if (
                        isinstance(node.args[0], ast.Call)
                        and isinstance(node.args[0].func, ast.Attribute)
                        and node.args[0].func.attr == "as_view"
                        and isinstance(
                            node.args[0].func.value, ast.Name
                        )
                        and node.args[0].func.value.id == builder_name
                    ):
                        raise PerformanceCompileError(
                            "BytesBuilder self-extend through its own view is "
                            "an overlapping alias mutation"
                        )
                    self._ensure_builder_unborrowed(
                        builder_name, node, "extend"
                    )
                    view, view_type = self.expression(
                        node.args[0], BYTES_VIEW
                    )
                    if view_type != BYTES_VIEW:
                        raise PerformanceCompileError(
                            "BytesBuilder.extend requires BytesView"
                        )
                    additional = str(
                        self._emit(
                            "bytes_len",
                            type_=UINT64,
                            operands=(view,),
                            node=node,
                        )
                    )
                    self._emit(
                        "builder_grow",
                        operands=(aggregate, additional),
                        attributes={
                            **common_attributes,
                            "reason": "extend",
                            "overflow_checks": (
                                "length_plus_additional",
                                "capacity_times_two",
                                "allocation_byte_size",
                            ),
                        },
                        node=node,
                        result=False,
                    )
                    for event in ("allocation", "payload_copy", "free"):
                        self._emit(
                            event,
                            operands=(aggregate, additional),
                            attributes={
                                **common_attributes,
                                "reason": "extend_growth",
                                "condition": "required_exceeds_capacity",
                            },
                            node=node,
                            result=False,
                        )
                    self._emit(
                        "builder_extend",
                        operands=(aggregate, view),
                        attributes={
                            **common_attributes,
                            "overlap": "forbidden",
                        },
                        node=node,
                        result=False,
                    )
                    return view, UNIT
                if (
                    attribute == "as_view"
                    and not node.args
                    and not node.keywords
                ):
                    view_name, last_use = next(
                        (
                            (name, end)
                            for name, (
                                borrow_owner,
                                declared,
                                end,
                            ) in self.view_borrows.items()
                            if borrow_owner == builder_name
                            and declared == int(node.lineno)
                        ),
                        (
                            f"ephemeral_{node.lineno}_"
                            f"{getattr(node, 'col_offset', 0)}",
                            int(node.lineno),
                        ),
                    )
                    borrow_id = (
                        f"builder_view:{self.node.name}:{view_name}:"
                        f"{node.lineno}:{getattr(node, 'col_offset', 0)}"
                    )
                    attributes = {
                        **common_attributes,
                        "borrow_id": borrow_id,
                        "view_name": view_name,
                        "last_use_line": last_use,
                        "range_relation": "current_builder_payload",
                        "zero_copy": True,
                    }
                    result = self._emit(
                        "builder_view",
                        type_=BYTES_VIEW,
                        operands=(aggregate,),
                        attributes=attributes,
                        node=node,
                    )
                    self.pending_builder_views[str(result)] = _BorrowState(
                        "builder_view",
                        aggregate,
                        tuple(attributes.items()),
                    )
                    return str(result), BYTES_VIEW
                if (
                    attribute == "finish"
                    and not node.args
                    and not node.keywords
                ):
                    self._ensure_builder_unborrowed(
                        builder_name, node, "finish"
                    )
                    binding = self.bindings[builder_name]
                    self.bindings[builder_name] = replace(
                        binding,
                        moved=True,
                        ownership_state="Finished",
                    )
                    result = self._emit(
                        "builder_finish_transfer",
                        type_=BYTES,
                        operands=(aggregate,),
                        attributes={
                            **common_attributes,
                            "source_state": "Live",
                            "target_state": "Finished",
                            "pointer_identity": "preserved",
                            "payload_copies": 0,
                        },
                        node=node,
                    )
                    return str(result), BYTES
                raise PerformanceCompileError(
                    f"unsupported BytesBuilder method {attribute} "
                    f"at line {node.lineno}"
                )
            if aggregate_type == TEXT_BUILDER:
                if not isinstance(receiver, ast.Name):
                    raise PerformanceCompileError(
                        "TextBuilder methods require a named unique owner"
                    )
                builder_name = receiver.id
                owner = f"{self.node.name}.{builder_name}"
                common_attributes = {
                    "builder_owner": owner,
                    "builder_type": "TextBuilder",
                    "growth_policy": (
                        "zero_then_max_8_required_then_double"
                    ),
                    "utf8_invariant": "payload_0_to_length_valid",
                }
                if (
                    attribute == "len_bytes"
                    and not node.args
                    and not node.keywords
                ):
                    result = self._emit(
                        "builder_len",
                        type_=UINT64,
                        operands=(aggregate,),
                        attributes=common_attributes,
                        node=node,
                    )
                    return str(result), UINT64
                if (
                    attribute == "capacity_bytes"
                    and not node.args
                    and not node.keywords
                ):
                    result = self._emit(
                        "builder_capacity",
                        type_=UINT64,
                        operands=(aggregate,),
                        attributes=common_attributes,
                        node=node,
                    )
                    return str(result), UINT64
                if (
                    attribute == "reserve_bytes"
                    and len(node.args) == 1
                    and not node.keywords
                ):
                    self._ensure_builder_unborrowed(
                        builder_name, node, "reserve_bytes"
                    )
                    additional, additional_type = self.expression(
                        node.args[0], UINT64
                    )
                    if additional_type != UINT64:
                        raise PerformanceCompileError(
                            "TextBuilder.reserve_bytes requires UInt64"
                        )
                    self._emit(
                        "builder_reserve",
                        operands=(aggregate, additional),
                        attributes={
                            **common_attributes,
                            "units": "bytes",
                            "guarantee": (
                                "capacity_at_least_"
                                "length_plus_additional"
                            ),
                        },
                        node=node,
                        result=False,
                    )
                    self._emit(
                        "builder_grow",
                        operands=(aggregate, additional),
                        attributes={
                            **common_attributes,
                            "reason": "reserve_bytes",
                            "overflow_checks": (
                                "length_plus_additional",
                                "capacity_times_two",
                                "allocation_byte_size",
                            ),
                        },
                        node=node,
                        result=False,
                    )
                    for event in ("allocation", "payload_copy", "free"):
                        self._emit(
                            event,
                            operands=(aggregate, additional),
                            attributes={
                                **common_attributes,
                                "reason": "reserve_growth",
                                "condition": "required_exceeds_capacity",
                            },
                            node=node,
                            result=False,
                        )
                    return additional, UNIT
                if (
                    attribute == "push_ascii"
                    and len(node.args) == 1
                    and not node.keywords
                ):
                    self._ensure_builder_unborrowed(
                        builder_name, node, "push_ascii"
                    )
                    if (
                        isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, int)
                        and not 0 <= node.args[0].value <= 0x7F
                    ):
                        raise PerformanceCompileError(
                            "TextBuilderAsciiOutOfRange"
                        )
                    scalar, scalar_type = self.expression(
                        node.args[0], UINT64
                    )
                    if scalar_type != UINT64:
                        raise PerformanceCompileError(
                            "TextBuilder.push_ascii requires UInt64"
                        )
                    one = str(
                        self._emit(
                            "const",
                            type_=UINT64,
                            attributes={"value": 1},
                            node=node,
                        )
                    )
                    self._emit(
                        "text_builder_append_account",
                        operands=(one,),
                        attributes={
                            **common_attributes,
                            "kind": "ascii",
                        },
                        node=node,
                        result=False,
                    )
                    self._emit(
                        "builder_grow",
                        operands=(aggregate, one),
                        attributes={
                            **common_attributes,
                            "reason": "push_ascii",
                            "overflow_checks": (
                                "length_plus_additional",
                                "capacity_times_two",
                                "allocation_byte_size",
                            ),
                        },
                        node=node,
                        result=False,
                    )
                    for event in ("allocation", "payload_copy", "free"):
                        self._emit(
                            event,
                            operands=(aggregate, one),
                            attributes={
                                **common_attributes,
                                "reason": "push_ascii_growth",
                                "condition": "required_exceeds_capacity",
                            },
                            node=node,
                            result=False,
                        )
                    self._emit(
                        "text_builder_push_ascii",
                        operands=(aggregate, scalar),
                        attributes={
                            **common_attributes,
                            "diagnostic": "TextBuilderAsciiOutOfRange",
                        },
                        node=node,
                        result=False,
                    )
                    return scalar, UNIT
                if (
                    attribute == "push_scalar"
                    and len(node.args) == 1
                    and not node.keywords
                ):
                    self._ensure_builder_unborrowed(
                        builder_name, node, "push_scalar"
                    )
                    if (
                        isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, int)
                        and (
                            node.args[0].value > 0x10FFFF
                            or 0xD800 <= node.args[0].value <= 0xDFFF
                            or node.args[0].value < 0
                        )
                    ):
                        raise PerformanceCompileError(
                            "TextBuilderInvalidUnicodeScalar"
                        )
                    scalar, scalar_type = self.expression(
                        node.args[0], UINT64
                    )
                    if scalar_type != UINT64:
                        raise PerformanceCompileError(
                            "TextBuilder.push_scalar requires UInt64"
                        )
                    width = str(
                        self._emit(
                            "text_builder_scalar_width",
                            type_=UINT64,
                            operands=(scalar,),
                            attributes={
                                "diagnostic": (
                                    "TextBuilderInvalidUnicodeScalar"
                                ),
                                "encoding": "canonical_utf8",
                            },
                            node=node,
                        )
                    )
                    self._emit(
                        "text_builder_append_account",
                        operands=(width,),
                        attributes={
                            **common_attributes,
                            "kind": "scalar",
                        },
                        node=node,
                        result=False,
                    )
                    self._emit(
                        "builder_grow",
                        operands=(aggregate, width),
                        attributes={
                            **common_attributes,
                            "reason": "push_scalar",
                            "overflow_checks": (
                                "length_plus_additional",
                                "capacity_times_two",
                                "allocation_byte_size",
                            ),
                        },
                        node=node,
                        result=False,
                    )
                    for event in ("allocation", "payload_copy", "free"):
                        self._emit(
                            event,
                            operands=(aggregate, width),
                            attributes={
                                **common_attributes,
                                "reason": "push_scalar_growth",
                                "condition": "required_exceeds_capacity",
                            },
                            node=node,
                            result=False,
                        )
                    self._emit(
                        "text_builder_push_scalar",
                        operands=(aggregate, scalar, width),
                        attributes={
                            **common_attributes,
                            "encoding": "canonical_utf8",
                        },
                        node=node,
                        result=False,
                    )
                    return scalar, UNIT
                if (
                    attribute == "extend"
                    and len(node.args) == 1
                    and not node.keywords
                ):
                    if (
                        isinstance(node.args[0], ast.Call)
                        and isinstance(node.args[0].func, ast.Attribute)
                        and node.args[0].func.attr == "as_view"
                        and isinstance(
                            node.args[0].func.value, ast.Name
                        )
                        and node.args[0].func.value.id == builder_name
                    ):
                        raise PerformanceCompileError(
                            "TextBuilder self-extend through its own view is "
                            "an overlapping alias mutation"
                        )
                    self._ensure_builder_unborrowed(
                        builder_name, node, "extend"
                    )
                    view, view_type = self.expression(
                        node.args[0], TEXT_VIEW
                    )
                    if view_type != TEXT_VIEW:
                        raise PerformanceCompileError(
                            "TextBuilder.extend requires TextView"
                        )
                    additional = str(
                        self._emit(
                            "text_len_bytes",
                            type_=UINT64,
                            operands=(view,),
                            node=node,
                        )
                    )
                    self._emit(
                        "text_builder_append_account",
                        operands=(additional,),
                        attributes={
                            **common_attributes,
                            "kind": "text_view",
                        },
                        node=node,
                        result=False,
                    )
                    self._emit(
                        "builder_grow",
                        operands=(aggregate, additional),
                        attributes={
                            **common_attributes,
                            "reason": "extend_text_view",
                            "overflow_checks": (
                                "length_plus_additional",
                                "capacity_times_two",
                                "allocation_byte_size",
                            ),
                        },
                        node=node,
                        result=False,
                    )
                    for event in ("allocation", "payload_copy", "free"):
                        self._emit(
                            event,
                            operands=(aggregate, additional),
                            attributes={
                                **common_attributes,
                                "reason": "extend_growth",
                                "condition": "required_exceeds_capacity",
                            },
                            node=node,
                            result=False,
                        )
                    self._emit(
                        "text_builder_extend",
                        operands=(aggregate, view),
                        attributes={
                            **common_attributes,
                            "source_validity": "TextView",
                            "overlap": "forbidden",
                        },
                        node=node,
                        result=False,
                    )
                    return view, UNIT
                if (
                    attribute == "as_view"
                    and not node.args
                    and not node.keywords
                ):
                    view_name, last_use = next(
                        (
                            (name, end)
                            for name, (
                                borrow_owner,
                                declared,
                                end,
                            ) in self.view_borrows.items()
                            if borrow_owner == builder_name
                            and declared == int(node.lineno)
                        ),
                        (
                            f"ephemeral_{node.lineno}_"
                            f"{getattr(node, 'col_offset', 0)}",
                            int(node.lineno),
                        ),
                    )
                    borrow_id = (
                        f"text_builder_view:{self.node.name}:{view_name}:"
                        f"{node.lineno}:{getattr(node, 'col_offset', 0)}"
                    )
                    attributes = {
                        **common_attributes,
                        "borrow_id": borrow_id,
                        "view_name": view_name,
                        "last_use_line": last_use,
                        "range_relation": "current_builder_payload",
                        "zero_copy": True,
                        "payload_copies": 0,
                        "retain_release": 0,
                    }
                    result = self._emit(
                        "text_builder_view",
                        type_=TEXT_VIEW,
                        operands=(aggregate,),
                        attributes=attributes,
                        node=node,
                    )
                    self.pending_builder_views[str(result)] = (
                        _BorrowState(
                            "text_builder_view",
                            aggregate,
                            tuple(attributes.items()),
                        )
                    )
                    return str(result), TEXT_VIEW
                if (
                    attribute == "finish"
                    and not node.args
                    and not node.keywords
                ):
                    self._ensure_builder_unborrowed(
                        builder_name, node, "finish"
                    )
                    binding = self.bindings[builder_name]
                    self.bindings[builder_name] = replace(
                        binding,
                        moved=True,
                        ownership_state="Finished",
                    )
                    result = self._emit(
                        "text_builder_finish_transfer",
                        type_=TEXT,
                        operands=(aggregate,),
                        attributes={
                            **common_attributes,
                            "source_state": "Live",
                            "target_state": "Finished",
                            "result_ownership": "Unique",
                            "pointer_identity": "preserved",
                            "length_identity": "preserved",
                            "capacity_identity": "preserved",
                            "allocations": 0,
                            "payload_copies": 0,
                            "validation_passes": 0,
                        },
                        node=node,
                    )
                    return str(result), TEXT
                raise PerformanceCompileError(
                    f"unsupported TextBuilder method {attribute} "
                    f"at line {node.lineno}"
                )
            if aggregate_type in {TEXT, TEXT_VIEW}:
                if (
                    attribute == "len_bytes"
                    and not node.args
                    and not node.keywords
                ):
                    result = self._emit(
                        "text_len_bytes",
                        type_=UINT64,
                        operands=(aggregate,),
                        node=node,
                    )
                    return str(result), UINT64
                if (
                    aggregate_type == TEXT
                    and attribute == "as_view"
                    and not node.args
                    and not node.keywords
                    and isinstance(receiver, ast.Name)
                ):
                    owner_name = receiver.id
                    declared_view = next(
                        (
                            name
                            for name, (
                                borrow_owner,
                                declared,
                                _last_use,
                            ) in self.view_borrows.items()
                            if borrow_owner == owner_name
                            and declared == int(node.lineno)
                        ),
                        (
                            f"ephemeral_{node.lineno}_"
                            f"{getattr(node, 'col_offset', 0)}"
                        ),
                    )
                    last_use = max(
                        (
                            end
                            for borrow_owner, _declared, end
                            in self.view_borrows.values()
                            if borrow_owner == owner_name
                        ),
                        default=int(node.lineno),
                    )
                    borrow_id = (
                        f"text_view:{self.node.name}:{declared_view}:"
                        f"{node.lineno}:{getattr(node, 'col_offset', 0)}"
                    )
                    attributes = {
                        "representation": "borrowed_pointer_length_v1",
                        "zero_copy": True,
                        "range_relation": "full_text_payload",
                        "text_owner": f"{self.node.name}.{owner_name}",
                        "root_owner": f"{self.node.name}.{owner_name}",
                        "borrow_id": borrow_id,
                        "last_use_line": last_use,
                    }
                    result = self._emit(
                        "text_view",
                        type_=TEXT_VIEW,
                        operands=(aggregate,),
                        attributes=attributes,
                        node=node,
                    )
                    self.pending_text_views[str(result)] = _BorrowState(
                        "text_view",
                        str(result),
                        tuple(attributes.items()),
                    )
                    return str(result), TEXT_VIEW
                if (
                    aggregate_type == TEXT
                    and attribute == "into_bytes"
                    and not node.args
                    and not node.keywords
                    and isinstance(receiver, ast.Name)
                ):
                    owner_name = receiver.id
                    self._ensure_text_unborrowed(
                        owner_name, node, "convert into Bytes"
                    )
                    binding = self.bindings[owner_name]
                    self.bindings[owner_name] = replace(
                        binding,
                        moved=True,
                        ownership_state="Moved",
                    )
                    result = self._emit(
                        "text_to_bytes_transfer",
                        type_=BYTES,
                        operands=(aggregate,),
                        attributes={
                            "pointer_identity": "preserved",
                            "payload_copies": 0,
                        },
                        node=node,
                    )
                    return str(result), BYTES
                if (
                    aggregate_type == TEXT_VIEW
                    and attribute == "as_bytes"
                    and not node.args
                    and not node.keywords
                ):
                    result = self._emit(
                        "text_view_as_bytes",
                        type_=BYTES_VIEW,
                        operands=(aggregate,),
                        attributes={
                            "representation": "borrowed_pointer_length_v1",
                            "zero_copy": True,
                            "range_relation": "same_payload",
                        },
                        node=node,
                    )
                    return str(result), BYTES_VIEW
                if (
                    aggregate_type == TEXT_VIEW
                    and attribute == "slice_bytes"
                    and len(node.args) == 2
                    and not node.keywords
                ):
                    start, start_type = self.expression(
                        node.args[0], UINT64
                    )
                    length, length_type = self.expression(
                        node.args[1], UINT64
                    )
                    if start_type != UINT64 or length_type != UINT64:
                        raise PerformanceCompileError(
                            "TextView.slice_bytes start and length "
                            "must be UInt64"
                        )
                    self._emit(
                        "utf8_boundary_check",
                        operands=(aggregate, start, length),
                        attributes={
                            "diagnostic": "TextSliceNotOnUtf8Boundary",
                            "proof": "checked_at_runtime",
                        },
                        node=node,
                        result=False,
                    )
                    result = self._emit(
                        "text_slice",
                        type_=TEXT_VIEW,
                        operands=(aggregate, start, length),
                        attributes={
                            "representation": "borrowed_pointer_length_v1",
                            "zero_copy": True,
                            "range_relation": "result_inside_receiver",
                        },
                        node=node,
                    )
                    if aggregate in self.returned_borrows:
                        self.returned_borrows[str(result)] = (
                            self.returned_borrows.pop(aggregate)
                        )
                    if aggregate in self.returned_view_metadata:
                        self.returned_view_metadata[str(result)] = (
                            self.returned_view_metadata.pop(aggregate)
                        )
                    return str(result), TEXT_VIEW
                if (
                    aggregate_type == TEXT_VIEW
                    and attribute == "scalar_count"
                    and not node.args
                    and not node.keywords
                ):
                    result = self._emit(
                        "utf8_scalar_count",
                        type_=UINT64,
                        operands=(aggregate,),
                        attributes={"complexity": "linear_in_bytes"},
                        node=node,
                    )
                    return str(result), UINT64
                if (
                    aggregate_type == TEXT_VIEW
                    and attribute == "scalar_width_at"
                    and len(node.args) == 1
                    and not node.keywords
                ):
                    offset, offset_type = self.expression(
                        node.args[0], UINT64
                    )
                    if offset_type != UINT64:
                        raise PerformanceCompileError(
                            "TextView.scalar_width_at offset must be UInt64"
                        )
                    result = self._emit(
                        "utf8_scalar_next",
                        type_=UINT64,
                        operands=(aggregate, offset),
                        attributes={
                            "result": "utf8_sequence_byte_length"
                        },
                        node=node,
                    )
                    return str(result), UINT64
                raise PerformanceCompileError(
                    f"unsupported {aggregate_type.name} method {attribute} "
                    f"at line {node.lineno}"
                )
            if aggregate_type not in {BYTES, BYTES_VIEW}:
                raise PerformanceCompileError(
                    f"only direct calls are allowed at line {node.lineno}"
                )
            if attribute == "len" and not node.args and not node.keywords:
                if aggregate_type not in {BYTES, BYTES_VIEW}:
                    raise PerformanceCompileError("len method requires Bytes or BytesView")
                result = self._emit(
                    "bytes_len", type_=UINT64, operands=(aggregate,), node=node
                )
                return str(result), UINT64
            if attribute == "slice" and len(node.args) == 2 and not node.keywords:
                if aggregate_type not in {BYTES, BYTES_VIEW}:
                    raise PerformanceCompileError(
                        "slice requires a Bytes or BytesView value"
                    )
                start, start_type = self.expression(node.args[0], UINT64)
                length, length_type = self.expression(node.args[1], UINT64)
                if start_type != UINT64 or length_type != UINT64:
                    raise PerformanceCompileError(
                        "Bytes.slice start and length must be UInt64"
                    )
                result = self._emit(
                    "bytes_slice",
                    type_=BYTES_VIEW,
                    operands=(aggregate, start, length),
                    attributes={
                        "representation": "borrowed_pointer_length_v1",
                        "diagnostic": "BytesSliceOutOfBounds",
                        "receiver_kind": aggregate_type.name,
                        "range_relation": "result_inside_receiver",
                    },
                    node=node,
                )
                if (
                    aggregate_type in _BORROWED_VIEW_TYPES
                    and aggregate in self.returned_borrows
                ):
                    self.returned_borrows[str(result)] = (
                        self.returned_borrows.pop(aggregate)
                    )
                if (
                    aggregate_type in _BORROWED_VIEW_TYPES
                    and aggregate in self.returned_view_metadata
                ):
                    self.returned_view_metadata[str(result)] = (
                        self.returned_view_metadata.pop(aggregate)
                    )
                return str(result), BYTES_VIEW
            raise PerformanceCompileError(
                f"unsupported Bytes method {attribute} at line {node.lineno}"
            )
        if not isinstance(node.func, ast.Name):
            raise PerformanceCompileError(f"only direct calls are allowed at line {node.lineno}")
        name = node.func.id
        if (
            name == "json_token_checksum"
            and len(node.args) == 1
            and not node.keywords
        ):
            aggregate, aggregate_type = self.expression(node.args[0])
            if aggregate_type not in {BYTES, BYTES_VIEW, TEXT_VIEW}:
                raise PerformanceCompileError(
                    "json_token_checksum requires Bytes, BytesView, or TextView"
                )
            result = self._emit(
                "json_token_checksum",
                type_=UINT64,
                operands=(aggregate,),
                attributes={
                    "consumer": "deterministic_fnv1a64_v1",
                    "streaming": True,
                    "constructs_ast": False,
                    "receiver_kind": aggregate_type.name,
                },
                node=node,
            )
            return str(result), UINT64
        if name == "len" and len(node.args) == 1 and not node.keywords:
            aggregate, aggregate_type = self.expression(node.args[0])
            if aggregate_type.kind not in {"array", "slice"}:
                raise PerformanceCompileError("len requires an array or slice")
            result = self._emit("array_len", type_=UINT64, operands=(aggregate,), node=node)
            return str(result), UINT64
        if (
            name
            in {
                "borrow",
                "borrow_shared",
                "borrow_mut",
                "meldra_borrow_shared",
                "meldra_borrow_mut",
            }
            and len(node.args) == 1
            and not node.keywords
        ):
            if (
                isinstance(node.args[0], ast.Name)
                and self.bindings.get(
                    node.args[0].id, _Binding(UNIT, "")
                ).borrowed
            ):
                raise PerformanceCompileError(
                    f"nested borrow of view {node.args[0].id} is outside Stage 0.6P"
                )
            aggregate, aggregate_type = self.expression(node.args[0])
            if aggregate_type.kind not in {"array", "slice"}:
                raise PerformanceCompileError(
                    f"{name} requires an array or slice"
                )
            return aggregate, aggregate_type
        if name == "retain" and len(node.args) == 1 and not node.keywords:
            if (
                isinstance(node.args[0], ast.Name)
                and self.bindings.get(
                    node.args[0].id, _Binding(UNIT, "")
                ).borrowed
            ):
                raise PerformanceCompileError(
                    f"cannot retain borrowed view {node.args[0].id}"
                )
            aggregate, aggregate_type = self.expression(node.args[0])
            if aggregate_type.kind not in {"array", "slice"} or not aggregate_type.shared:
                raise PerformanceCompileError("retain requires a Shared collection")
            result = self._emit(
                "retain",
                type_=aggregate_type,
                operands=(aggregate,),
                node=node,
            )
            return str(result), aggregate_type
        if (
            name in {"drop", "release"}
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Name)
        ):
            binding_name = node.args[0].id
            binding = self.bindings.get(binding_name, _Binding(UNIT, ""))
            if binding.moved and binding.type in _OWNED_TRANSFER_TYPES:
                raise PerformanceCompileError(
                    f"double drop of {binding.type.name} owner {binding_name} "
                    f"after {binding.ownership_state.lower()} at line "
                    f"{node.lineno}"
                )
            if binding.borrowed:
                raise PerformanceCompileError(
                    f"cannot {name} borrowed view {binding_name}"
                )
            if (
                binding.parameter
                and binding.type not in _OWNED_TRANSFER_TYPES
            ):
                raise PerformanceCompileError(
                    f"cannot drop borrowed parameter {binding_name}"
                )
            value, type_ = self._binding_value(binding_name, node)
            if (
                type_.kind not in {"array", "slice"}
                and type_ not in _OWNED_TRANSFER_TYPES
            ):
                raise PerformanceCompileError(
                    "drop requires an owned array, slice, Bytes, "
                    "BytesBuilder, TextBuilder, or Text"
                )
            if name == "release" and not type_.shared:
                raise PerformanceCompileError("release requires a Shared collection")
            if type_ == BYTES:
                self._ensure_bytes_unborrowed(binding_name, node, "drop")
            if type_ in {BYTES_BUILDER, TEXT_BUILDER}:
                self._ensure_builder_unborrowed(
                    binding_name, node, "drop"
                )
            if type_ == TEXT:
                self._ensure_text_unborrowed(binding_name, node, "drop")
            self.bindings[binding_name] = replace(
                binding,
                moved=True,
                ownership_state=(
                    "Dropped"
                    if type_ in _OWNED_TRANSFER_TYPES
                    else binding.ownership_state
                ),
            )
            self._emit(
                (
                    "builder_drop"
                    if type_ == BYTES_BUILDER
                    else (
                        "text_builder_drop"
                        if type_ == TEXT_BUILDER
                        else "text_drop" if type_ == TEXT else "drop"
                    )
                ),
                operands=(value,),
                attributes={"explicit": True, "owner_type": type_.name},
                node=node,
                result=False,
            )
            unit = self._emit("const", type_=UNIT, attributes={"value": 0}, node=node)
            return str(unit), UNIT
        if name == "move" and len(node.args) == 1 and isinstance(node.args[0], ast.Name):
            binding_name = node.args[0].id
            value, type_ = self._binding_value(binding_name, node)
            binding = self.bindings.get(binding_name, _Binding(UNIT, ""))
            if binding.borrowed:
                raise PerformanceCompileError(
                    f"cannot move borrowed view {binding_name}"
                )
            if binding.parameter and (
                binding.type not in _OWNED_TRANSFER_TYPES
                or self.signatures[self.node.name].return_type
                in _BORROWED_VIEW_TYPES
            ):
                raise PerformanceCompileError(
                    f"cannot move borrowed parameter {binding_name}"
                )
            if not type_.unique:
                raise PerformanceCompileError(f"move requires a unique value at line {node.lineno}")
            if type_ == BYTES:
                self._ensure_bytes_unborrowed(binding_name, node, "move")
            if type_ in {BYTES_BUILDER, TEXT_BUILDER}:
                self._ensure_builder_unborrowed(
                    binding_name, node, "move"
                )
            if type_ == TEXT:
                self._ensure_text_unborrowed(binding_name, node, "move")
            self.bindings[binding_name] = replace(
                binding,
                moved=True,
                ownership_state=(
                    "Moved"
                    if type_ in {BYTES_BUILDER, TEXT_BUILDER, TEXT}
                    else binding.ownership_state
                ),
            )
            result = self._emit(
                "move", type_=type_, operands=(value,), node=node
            )
            return str(result), type_
        if name in self.records:
            record = self.records[name]
            values: dict[str, tuple[str, PerformanceType]] = {}
            for (field_name, field_type), argument in zip(record.fields, node.args, strict=False):
                values[field_name] = self.expression(argument, field_type)
            for keyword in node.keywords:
                if keyword.arg is None:
                    raise PerformanceCompileError("record splats are outside Stage 0.5P")
                field_type = dict(record.fields).get(keyword.arg)
                if field_type is None:
                    raise PerformanceCompileError(f"unknown {name} field: {keyword.arg}")
                values[keyword.arg] = self.expression(keyword.value, field_type)
            if set(values) != {field for field, _type in record.fields}:
                raise PerformanceCompileError(f"record constructor {name} requires every field")
            operands = tuple(values[field][0] for field, _type in record.fields)
            type_ = PerformanceType("record", record=name)
            result = self._emit("record_init", type_=type_, operands=operands, attributes={"record": name}, node=node)
            return str(result), type_
        if name in {"map", "filter"}:
            if len(node.args) != 2 or not isinstance(node.args[1], ast.Name):
                raise PerformanceCompileError(
                    f"{name} requires a collection and direct function"
                )
            aggregate, aggregate_type = self.expression(node.args[0])
            if aggregate_type.kind not in {"array", "slice"}:
                raise PerformanceCompileError(
                    f"{name} requires an array or slice"
                )
            function = node.args[1].id
            if function not in self.signatures:
                raise PerformanceCompileError(
                    f"unknown collection function: {function}"
                )
            result_element = (
                self.signatures[function].return_type
                if name == "map"
                else aggregate_type.element
            )
            result_type = PerformanceType("slice", element=result_element)
            allocation = str(
                self._emit(
                    "alloc_heap",
                    type_=result_type,
                    attributes={
                        "collection": name,
                        "length_source": aggregate,
                    },
                    node=node,
                )
            )
            result = self._emit(
                f"collection_{name}",
                type_=result_type,
                operands=(aggregate, allocation),
                attributes={
                    "function": function,
                    "generic": aggregate_type.element.name,
                },
                node=node,
            )
            return str(result), result_type
        if name == "fold":
            if len(node.args) != 3 or not isinstance(node.args[2], ast.Name):
                raise PerformanceCompileError("fold requires collection, initial value, direct function")
            aggregate, aggregate_type = self.expression(node.args[0])
            if aggregate_type.kind not in {"array", "slice"}:
                raise PerformanceCompileError("fold requires an array or slice")
            initial, initial_type = self.expression(node.args[1], expected)
            function = node.args[2].id
            if function not in self.signatures:
                raise PerformanceCompileError(f"unknown fold function: {function}")
            result = self._emit(
                "collection_fold",
                type_=initial_type,
                operands=(aggregate, initial),
                attributes={"function": function, "generic": aggregate_type.element.name},
                node=node,
            )
            return str(result), initial_type
        try:
            signature = self.signatures[name]
        except KeyError as exc:
            raise PerformanceCompileError(f"unknown function: {name}") from exc
        if node.keywords:
            raise PerformanceCompileError("named call arguments are outside the performance kernel")
        if len(node.args) != len(signature.parameters):
            raise PerformanceCompileError(f"wrong argument count for {name}")
        operands = []
        argument_ownership = []
        argument_borrow_kinds = []
        argument_borrow_ids = []
        active_borrows: list[tuple[str, _BorrowState, tuple[_BorrowState, ...], str | None]] = []
        borrowed_root_owners = set()
        moved_root_owners = set()
        for argument, parameter_type in zip(
            node.args, signature.parameters, strict=True
        ):
            if parameter_type in _BORROWED_VIEW_TYPES:
                root_owner = None
                if isinstance(argument, ast.Name):
                    binding = self.bindings.get(argument.id)
                    root_owner = binding.root_owner if binding is not None else None
                elif (
                    isinstance(argument, ast.Call)
                    and isinstance(argument.func, ast.Attribute)
                    and argument.func.attr
                    in {"slice", "slice_bytes", "as_view", "as_bytes"}
                    and isinstance(argument.func.value, ast.Name)
                ):
                    receiver_binding = self.bindings.get(argument.func.value.id)
                    root_owner = (
                        receiver_binding.root_owner
                        if receiver_binding is not None
                        and receiver_binding.type in _BORROWED_VIEW_TYPES
                        else f"{self.node.name}.{argument.func.value.id}"
                    )
                if root_owner is not None:
                    borrowed_root_owners.add(root_owner)
            elif (
                parameter_type in _OWNED_TRANSFER_TYPES
                and isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Name)
                and argument.func.id == "move"
                and len(argument.args) == 1
                and isinstance(argument.args[0], ast.Name)
            ):
                moved_root_owners.add(
                    f"{self.node.name}.{argument.args[0].id}"
                )
        overlap = borrowed_root_owners & moved_root_owners
        if overlap:
            owner = sorted(overlap)[0]
            raise PerformanceCompileError(
                f"cannot move or mutate root owner {owner} during nested borrowed call"
            )
        call_token = (
            f"{self.node.name}:{node.lineno}:"
            f"{getattr(node, 'col_offset', 0)}:{name}"
        )
        borrowed_return_origin = (
            self.reborrow_analysis.borrowed_return_origins.get(name)
        )
        caller_scope, caller_last_use = self._returned_call_scope(node)
        for argument, parameter_type, parameter_name in zip(
            node.args,
            signature.parameters,
            signature.parameter_names,
            strict=True,
        ):
            if (
                parameter_type.kind in {"array", "slice"}
                and isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Name)
                and argument.func.id == "move"
            ):
                raise PerformanceCompileError(
                    "cross-function collection move is outside Stage 0.6P"
                )
            if parameter_type in _OWNED_TRANSFER_TYPES:
                if not (
                    isinstance(argument, ast.Call)
                    and isinstance(argument.func, ast.Name)
                    and argument.func.id == "move"
                    and len(argument.args) == 1
                    and isinstance(argument.args[0], ast.Name)
                ):
                    raise PerformanceCompileError(
                        f"owned {parameter_type.name} parameter "
                        f"{parameter_name} requires move(owner)"
                    )
                owner = argument.args[0].id
                if parameter_type == BYTES:
                    self._ensure_bytes_unborrowed(
                        owner, argument, "move"
                    )
                elif parameter_type in {
                    BYTES_BUILDER,
                    TEXT_BUILDER,
                }:
                    self._ensure_builder_unborrowed(
                        owner, argument, "move"
                    )
                else:
                    self._ensure_text_unborrowed(
                        owner, argument, "move"
                    )
                value, observed_type = self.expression(
                    argument, parameter_type
                )
                argument_ownership.append("move")
                argument_borrow_kinds.append("none")
                argument_borrow_ids.append(None)
            elif parameter_type in _BORROWED_VIEW_TYPES:
                argument_name = argument.id if isinstance(argument, ast.Name) else None
                argument_binding = (
                    self.bindings.get(argument_name)
                    if argument_name is not None
                    else None
                )
                value, observed_type = self.expression(argument, parameter_type)
                inherited_chain = self.returned_borrows.get(value, ())
                if not inherited_chain and argument_binding is not None:
                    inherited_chain = tuple(argument_binding.borrow_chain)
                provenance_binding = argument_binding
                if (
                    provenance_binding is None
                    and isinstance(argument, ast.Call)
                    and isinstance(argument.func, ast.Attribute)
                    and isinstance(argument.func.value, ast.Name)
                ):
                    provenance_binding = self.bindings.get(
                        argument.func.value.id
                    )
                parent_state = (
                    inherited_chain[-1]
                    if inherited_chain
                    and inherited_chain[-1].marker != "text_view"
                    else None
                )
                is_reborrow = bool(
                    parent_state is not None
                    or (
                        provenance_binding is not None
                        and provenance_binding.type in _BORROWED_VIEW_TYPES
                        and (
                            provenance_binding.parameter
                            or (
                                provenance_binding.borrow_depth is not None
                                and provenance_binding.borrow_depth > 0
                            )
                        )
                    )
                )
                marker = "reborrow_argument" if is_reborrow else "borrow_argument"
                if parent_state is not None:
                    parent_attributes = parent_state.attribute_map
                    parent_depth = parent_attributes.get("borrow_depth")
                    depth = (
                        int(parent_depth) + 1
                        if isinstance(parent_depth, int)
                        else None
                    )
                    parent_borrow = parent_attributes.get("borrow_id")
                    root_owner = parent_attributes.get("root_owner")
                elif is_reborrow and provenance_binding is not None:
                    depth = (
                        provenance_binding.borrow_depth + 1
                        if provenance_binding.borrow_depth is not None
                        else None
                    )
                    parent_borrow = provenance_binding.borrow_id
                    root_owner = provenance_binding.root_owner
                else:
                    depth = 1
                    parent_borrow = None
                    if (
                        provenance_binding is not None
                        and provenance_binding.type in _BORROWED_VIEW_TYPES
                    ):
                        root_owner = provenance_binding.root_owner
                    elif (
                        isinstance(argument, ast.Call)
                        and isinstance(argument.func, ast.Attribute)
                        and isinstance(argument.func.value, ast.Name)
                    ):
                        root_owner = f"{self.node.name}.{argument.func.value.id}"
                    else:
                        root_owner = f"dynamic_root:{call_token}:{parameter_name}"
                borrow_id = f"{marker}:{call_token}:{parameter_name}"
                transfers_return = (
                    signature.return_type in _BORROWED_VIEW_TYPES
                    and parameter_name == borrowed_return_origin
                )
                marker_attributes = {
                    "borrow_id": borrow_id,
                    "borrow_depth": depth if depth is not None else "dynamic",
                    "callee": name,
                    "call_scope": "direct_synchronous",
                    "caller_scope": (
                        caller_scope if transfers_return else f"{self.node.name}:call"
                    ),
                    "last_use_line": (
                        caller_last_use if transfers_return else int(node.lineno)
                    ),
                    "non_escaping": True,
                    "parameter": parameter_name,
                    "parent_borrow": parent_borrow,
                    "return_transfer": transfers_return,
                    "root_owner": root_owner,
                }
                self._emit(
                    marker,
                    operands=(value,),
                    attributes=marker_attributes,
                    node=node,
                    result=False,
                )
                state = _BorrowState(
                    marker,
                    value,
                    tuple(marker_attributes.items()),
                )
                active_borrows.append(
                    (parameter_name, state, inherited_chain, argument_name)
                )
                argument_ownership.append("borrow")
                argument_borrow_kinds.append(marker)
                argument_borrow_ids.append(borrow_id)
            else:
                value, observed_type = self.expression(argument, parameter_type)
                argument_ownership.append("value")
                argument_borrow_kinds.append("none")
                argument_borrow_ids.append(None)
            if observed_type != parameter_type:
                if not (
                    observed_type.kind in {"array", "slice"}
                    and parameter_type.kind == "slice"
                    and observed_type.element == parameter_type.element
                ):
                    raise PerformanceCompileError(f"argument type mismatch for {name}")
            operands.append(value)
        return_ownership = (
            "owned"
            if signature.return_type in _OWNED_TRANSFER_TYPES
            else (
                "borrowed_transfer"
                if signature.return_type in _BORROWED_VIEW_TYPES
                else "value"
            )
        )
        origin_active = next(
            (
                item
                for item in active_borrows
                if item[0] == borrowed_return_origin
            ),
            None,
        )
        result = self._emit(
            "call",
            type_=signature.return_type,
            operands=tuple(operands),
            attributes={
                "callee": name,
                "pure": signature.pure,
                "direct": True,
                "call_scope": "direct_synchronous",
                "argument_ownership": tuple(argument_ownership),
                "argument_borrow_kinds": tuple(argument_borrow_kinds),
                "argument_borrow_ids": tuple(argument_borrow_ids),
                "borrowed_return_origin": (
                    f"parameter:{name}.{borrowed_return_origin}"
                    if borrowed_return_origin is not None
                    else None
                ),
                "return_ownership": return_ownership,
            },
            node=node,
        )
        if signature.return_type in _BORROWED_VIEW_TYPES:
            if origin_active is None:
                raise PerformanceCompileError(
                    f"AmbiguousBorrowReturnOrigin: {name} call has no proven source"
                )
            _parameter, state, inherited_chain, argument_name = origin_active
            attributes = state.attribute_map
            self._emit(
                "caller_borrow_continue",
                operands=(str(result),),
                attributes={
                    **attributes,
                    "returned_value": str(result),
                    "return_origin": f"parameter:{name}.{borrowed_return_origin}",
                },
                node=node,
                result=False,
            )
            if inherited_chain:
                self.returned_borrows.pop(state.value, None)
                if argument_name is not None:
                    source_binding = self.bindings.get(argument_name)
                    if source_binding is not None and source_binding.borrow_chain:
                        self.bindings[argument_name] = replace(
                            source_binding, borrow_chain=()
                        )
            chain = (*inherited_chain, state)
            self.returned_borrows[str(result)] = chain
            self.returned_view_metadata[str(result)] = {
                **attributes,
                "returned_value": str(result),
                "return_origin": f"parameter:{name}.{borrowed_return_origin}",
            }
        else:
            self._end_borrow_chain(
                tuple(item[1] for item in active_borrows),
                node,
            )
        return str(result), signature.return_type

    def _declare(self, node: ast.AnnAssign) -> None:
        if not isinstance(node.target, ast.Name) or node.value is None:
            raise PerformanceCompileError("declaration requires a named initialized value")
        name = node.target.id
        if name in self.bindings:
            raise PerformanceCompileError(f"duplicate local: {name}")
        type_ = _parse_type(node.annotation, set(self.records))
        if type_.kind in {"array", "slice"} and isinstance(node.value, ast.Name):
            raise PerformanceCompileError(
                f"collection alias {name} requires borrow or move"
            )
        if (
            type_ in {
                BYTES,
                BYTES_VIEW,
                BYTES_BUILDER,
                TEXT_BUILDER,
                TEXT,
                TEXT_VIEW,
            }
            and isinstance(node.value, ast.Name)
        ):
            raise PerformanceCompileError(
                f"owned or borrowed alias {name} requires an explicit "
                "constructor or move"
            )
        kind = self.declaration_kinds.get((node.lineno, name))
        if kind not in {"let", "var"}:
            raise PerformanceCompileError(
                f"local {name} at line {node.lineno} must use let or var"
            )
        if (
            kind == "var"
            and type_.kind in {"array", "slice"}
            and not isinstance(node.value, ast.List)
        ):
            raise PerformanceCompileError(
                f"mutable collection {name} requires a fixed array literal in Stage 0.6P"
            )
        if kind == "var" and type_ in {
            BYTES,
            BYTES_VIEW,
            BYTES_BUILDER,
            TEXT_BUILDER,
            TEXT,
            TEXT_VIEW,
            UTF8_DECODE,
        }:
            raise PerformanceCompileError(
                f"{type_.name} bindings use immutable owner identities; "
                "mutate through their explicit APIs"
            )
        value, observed_type = self.expression(node.value, type_)
        if observed_type != type_:
            raise PerformanceCompileError(f"initializer type mismatch for {name}")
        if type_ == BYTES and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name) and node.value.func.id == "move":
                pass
            elif isinstance(node.value.func, ast.Name):
                signature = self.signatures.get(node.value.func.id)
                if signature is None or signature.return_type != BYTES:
                    raise PerformanceCompileError(
                        f"Bytes initializer for {name} is not an ownership-producing call"
                    )
            elif not (
                isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name)
                and (
                    (
                        node.value.func.value.id == "Bytes"
                        and node.value.func.attr == "new"
                    )
                    or (
                        node.value.func.attr == "finish"
                        and self.bindings.get(
                            node.value.func.value.id, _Binding(UNIT, "")
                        ).type
                        == BYTES_BUILDER
                    )
                    or (
                        node.value.func.attr == "into_bytes"
                        and self.bindings.get(
                            node.value.func.value.id, _Binding(UNIT, "")
                        ).type
                        == TEXT
                    )
                )
            ):
                raise PerformanceCompileError(
                    f"Bytes initializer for {name} is not an ownership-producing call"
                )
        if type_ == BYTES_BUILDER:
            if not isinstance(node.value, ast.Call):
                raise PerformanceCompileError(
                    f"BytesBuilder initializer for {name} is not "
                    "ownership-producing"
                )
            if isinstance(node.value.func, ast.Name):
                signature = self.signatures.get(node.value.func.id)
                if (
                    node.value.func.id != "move"
                    and (
                        signature is None
                        or signature.return_type != BYTES_BUILDER
                    )
                ):
                    raise PerformanceCompileError(
                        f"BytesBuilder initializer for {name} is not "
                        "ownership-producing"
                    )
            elif not (
                isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == "BytesBuilder"
                and node.value.func.attr in {"new", "with_capacity"}
            ):
                raise PerformanceCompileError(
                    f"BytesBuilder initializer for {name} is not "
                    "ownership-producing"
                )
        if type_ == TEXT_BUILDER:
            if not isinstance(node.value, ast.Call):
                raise PerformanceCompileError(
                    f"TextBuilder initializer for {name} is not "
                    "ownership-producing"
                )
            if isinstance(node.value.func, ast.Name):
                signature = self.signatures.get(node.value.func.id)
                if (
                    node.value.func.id != "move"
                    and (
                        signature is None
                        or signature.return_type != TEXT_BUILDER
                    )
                ):
                    raise PerformanceCompileError(
                        f"TextBuilder initializer for {name} is not "
                        "ownership-producing"
                    )
            elif not (
                isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == "TextBuilder"
                and node.value.func.attr
                in {"new", "with_capacity_bytes"}
            ):
                raise PerformanceCompileError(
                    f"TextBuilder initializer for {name} is not "
                    "ownership-producing"
                )
        if type_ == TEXT:
            if not isinstance(node.value, ast.Call):
                raise PerformanceCompileError(
                    f"Text initializer for {name} is not "
                    "ownership-producing"
                )
            if isinstance(node.value.func, ast.Name):
                signature = self.signatures.get(node.value.func.id)
                if (
                    node.value.func.id != "move"
                    and (
                        signature is None
                        or signature.return_type != TEXT
                    )
                ):
                    raise PerformanceCompileError(
                        f"Text initializer for {name} is not "
                        "ownership-producing"
                    )
            elif not (
                isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name)
                and (
                    (
                        node.value.func.value.id == "Text"
                        and node.value.func.attr
                        in {"from_ascii", "from_scalar", "from_surrogate"}
                    )
                    or (
                        node.value.func.attr == "finish"
                        and self.bindings.get(
                            node.value.func.value.id,
                            _Binding(UNIT, ""),
                        ).type
                        == TEXT_BUILDER
                    )
                )
            ):
                raise PerformanceCompileError(
                    f"Text initializer for {name} is not "
                    "ownership-producing"
                )
        if kind == "let":
            borrow_name = (
                node.value.func.id
                if isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                else None
            )
            borrowed = borrow_name in {
                "borrow",
                "borrow_shared",
                "borrow_mut",
                "meldra_borrow_shared",
                "meldra_borrow_mut",
            }
            borrow_owner = None
            borrow_depth = None
            borrow_id = None
            root_owner = None
            borrow_chain: tuple[_BorrowState, ...] = ()
            if type_ in _BORROWED_VIEW_TYPES:
                builder_state = self.pending_builder_views.pop(value, None)
                text_state = self.pending_text_views.pop(value, None)
                returned_metadata = self.returned_view_metadata.pop(value, None)
                if builder_state is not None:
                    attributes = builder_state.attribute_map
                    borrow_chain = (builder_state,)
                    root_owner = str(attributes["builder_owner"])
                    borrow_owner = root_owner.removeprefix(
                        f"{self.node.name}."
                    )
                    borrow_depth = 0
                    borrow_id = str(attributes["borrow_id"])
                elif text_state is not None:
                    attributes = text_state.attribute_map
                    borrow_chain = (text_state,)
                    root_owner = str(attributes["root_owner"])
                    borrow_owner = root_owner.removeprefix(
                        f"{self.node.name}."
                    )
                    borrow_depth = 0
                    borrow_id = str(attributes["borrow_id"])
                elif returned_metadata is not None:
                    borrow_chain = self.returned_borrows.pop(value, ())
                    root_owner = returned_metadata.get("root_owner")
                    borrow_depth = returned_metadata.get("borrow_depth")
                    borrow_id = returned_metadata.get("borrow_id")
                    prefix = f"{self.node.name}."
                    borrow_owner = (
                        str(root_owner).removeprefix(prefix)
                        if root_owner is not None
                        else None
                    )
                    self.view_borrows[name] = (
                        str(borrow_owner),
                        int(node.lineno),
                        self.local_last_uses.get(name, int(node.lineno)),
                    )
                elif (
                    isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and isinstance(node.value.func.value, ast.Name)
                    and node.value.func.attr
                    in {"slice", "slice_bytes", "as_view", "as_bytes"}
                ):
                    receiver_name = node.value.func.value.id
                    receiver_binding = self.bindings.get(receiver_name)
                    if (
                        receiver_binding is not None
                        and receiver_binding.type in _BORROWED_VIEW_TYPES
                    ):
                        root_owner = receiver_binding.root_owner
                        borrow_depth = receiver_binding.borrow_depth
                        borrow_id = receiver_binding.borrow_id
                        if any(
                            state.marker == "text_view"
                            for state in receiver_binding.borrow_chain
                        ):
                            borrow_chain = ()
                        else:
                            borrow_chain = tuple(
                                receiver_binding.borrow_chain
                            )
                            if borrow_chain:
                                self.bindings[receiver_name] = replace(
                                    receiver_binding,
                                    borrow_chain=(),
                                )
                        borrow_owner = str(root_owner).removeprefix(
                            f"{self.node.name}."
                        )
                    else:
                        borrow_owner = receiver_name
                        root_owner = f"{self.node.name}.{borrow_owner}"
                        borrow_depth = 0
                        borrow_id = f"view:{self.node.name}.{name}"
                else:
                    raise PerformanceCompileError(
                        f"{type_.name} {name} must be created by a proven "
                        "view method or borrowed-return call"
                    )
                if borrow_owner is not None:
                    self.view_borrows[name] = (
                        str(borrow_owner),
                        int(node.lineno),
                        self.local_last_uses.get(name, int(node.lineno)),
                    )
            self.bindings[name] = _Binding(
                type_,
                value,
                borrowed=borrowed or type_ in _BORROWED_VIEW_TYPES,
                borrowed_mut=borrow_name in {"borrow_mut", "meldra_borrow_mut"},
                borrow_owner=borrow_owner,
                borrow_depth=borrow_depth,
                borrow_id=borrow_id,
                root_owner=root_owner,
                borrow_chain=borrow_chain,
            )
            return
        slot_op = (
            "alloc_local"
            if type_.kind in {"array", "slice", "record"}
            else "alloc_stack"
        )
        slot = str(
            self._emit(
                slot_op,
                type_=type_,
                attributes={"local": name, "bytes": 0},
                node=node,
            )
        )
        self._emit("store_local", operands=(slot, value), node=node, result=False)
        self.bindings[name] = _Binding(type_, slot, mutable=True)

    def _assign(self, node: ast.Assign) -> None:
        if len(node.targets) != 1:
            raise PerformanceCompileError("multiple assignment is outside Stage 0.5P")
        target = node.targets[0]
        if isinstance(target, ast.Name):
            try:
                binding = self.bindings[target.id]
            except KeyError as exc:
                raise PerformanceCompileError(f"unknown assignment target: {target.id}") from exc
            if not binding.mutable:
                raise PerformanceCompileError(f"cannot assign immutable let {target.id}")
            value, observed_type = self.expression(node.value, binding.type)
            if observed_type != binding.type:
                raise PerformanceCompileError(f"assignment type mismatch for {target.id}")
            self._emit("store_local", operands=(binding.value, value), node=node, result=False)
            return
        if isinstance(target, ast.Subscript):
            aggregate, aggregate_type = self.expression(target.value)
            if aggregate_type in _BORROWED_VIEW_TYPES:
                raise PerformanceCompileError(
                    f"cannot mutate borrowed {aggregate_type.name} "
                    f"at line {node.lineno}"
                )
            if aggregate_type in {BYTES_BUILDER, TEXT_BUILDER}:
                if isinstance(target.value, ast.Name):
                    self._ensure_builder_unborrowed(
                        target.value.id, node, "mutate payload of"
                    )
                raise PerformanceCompileError(
                    f"{aggregate_type.name} payload mutation is only "
                    "available through its explicit append APIs"
                )
            if aggregate_type == BYTES:
                if not isinstance(target.value, ast.Name):
                    raise PerformanceCompileError(
                        "Bytes mutation requires a named owner"
                    )
                self._ensure_bytes_unborrowed(
                    target.value.id, node, "mutate"
                )
                index, index_type = self.expression(target.slice, UINT64)
                if index_type.kind not in {"int", "uint"}:
                    raise PerformanceCompileError("byte index must be an integer")
                value, observed_type = self.expression(node.value, UINT64)
                if observed_type != UINT64:
                    raise PerformanceCompileError(
                        "Bytes elements are UInt64 values in the range 0..255"
                    )
                length = str(
                    self._emit("bytes_len", type_=UINT64, operands=(aggregate,), node=node)
                )
                self._emit(
                    "bytes_bounds_check",
                    operands=(index, length),
                    attributes={"diagnostic": "BytesIndexOutOfBounds"},
                    node=node,
                    result=False,
                )
                self._emit(
                    "bytes_store",
                    operands=(aggregate, index, value),
                    attributes={"truncate_to_byte": True},
                    node=node,
                    result=False,
                )
                return
            if (
                isinstance(target.value, ast.Name)
                and self.bindings.get(
                    target.value.id, _Binding(UNIT, "")
                ).parameter
            ):
                raise PerformanceCompileError(
                    f"cannot mutate borrowed parameter {target.value.id}"
                )
            target_binding = (
                self.bindings.get(target.value.id)
                if isinstance(target.value, ast.Name)
                else None
            )
            if (
                target_binding is not None
                and target_binding.borrowed
                and not target_binding.borrowed_mut
            ):
                raise PerformanceCompileError(
                    f"cannot mutate shared borrowed view {target.value.id}"
                )
            if not aggregate_type.unique:
                raise PerformanceCompileError("shared collection update requires explicit fallback")
            index, _ = self.expression(target.slice, UINT64)
            value, observed_type = self.expression(node.value, aggregate_type.element)
            if observed_type != aggregate_type.element:
                raise PerformanceCompileError("index assignment type mismatch")
            length = str(self._emit("array_len", type_=UINT64, operands=(aggregate,), node=node))
            self._emit("bounds_check", operands=(index, length), node=node, result=False)
            self._emit("store_index", operands=(aggregate, index, value), attributes={"in_place": True}, node=node, result=False)
            return
        raise PerformanceCompileError(
            f"unsupported assignment target at line {node.lineno}"
        )

    def _if(self, node: ast.If) -> None:
        condition, condition_type = self.expression(node.test, BOOL)
        if condition_type != BOOL:
            raise PerformanceCompileError("if condition must be Bool")
        origin_bindings = dict(self.bindings)
        origin = self.current
        then_block = self._new_block("if_then")
        else_block = self._new_block("if_else")
        join_block = self._new_block("if_join")
        branch_source = _source(self.path, node)
        origin.terminator = MIRTerminator("branch", (then_block.id, else_block.id), condition, source=branch_source)
        self.current = then_block
        self.bindings = dict(origin_bindings)
        self.statements(node.body)
        then_bindings = dict(self.bindings)
        then_terminal = self.current.terminator is not None
        if not then_terminal:
            self._terminate(MIRTerminator("jump", (join_block.id,), source=branch_source))
        self.current = else_block
        self.bindings = dict(origin_bindings)
        self.statements(node.orelse)
        else_bindings = dict(self.bindings)
        else_terminal = self.current.terminator is not None
        if not else_terminal:
            self._terminate(MIRTerminator("jump", (join_block.id,), source=branch_source))
        merged = dict(origin_bindings)
        for name, before in origin_bindings.items():
            then_binding = then_bindings.get(name, before)
            else_binding = else_bindings.get(name, before)
            if before.type in {BYTES, TEXT} and (
                then_binding.moved != else_binding.moved
            ):
                raise PerformanceCompileError(
                    f"owned {before.type.name} {name} consumed on only "
                    "one conditional path"
                )
            if before.type in {BYTES_BUILDER, TEXT_BUILDER} and (
                then_binding.moved != else_binding.moved
                or then_binding.ownership_state
                != else_binding.ownership_state
            ):
                raise PerformanceCompileError(
                    f"owned {before.type.name} {name} has mismatched "
                    "ownership between conditional branches"
                )
            merged[name] = replace(
                before,
                moved=then_binding.moved and else_binding.moved,
                ownership_state=(
                    then_binding.ownership_state
                    if then_binding.ownership_state
                    == else_binding.ownership_state
                    else before.ownership_state
                ),
            )
        self.bindings = merged
        self.current = join_block
        if then_terminal and else_terminal:
            self._terminate(MIRTerminator("unreachable", source=branch_source))

    def _drop_loop_locals(
        self,
        outer_names: set[str],
        node: ast.AST,
    ) -> None:
        for name, binding in tuple(self.bindings.items()):
            if name in outer_names:
                continue
            if binding.borrow_chain:
                self._end_borrow_chain(binding.borrow_chain, node)
            if not binding.moved:
                if binding.type == BYTES:
                    self._emit(
                        "drop",
                        operands=(binding.value,),
                        attributes={
                            "automatic": True,
                            "owner_type": "Bytes",
                            "scope_exit": "loop_iteration",
                        },
                        node=node,
                        result=False,
                    )
                elif binding.type == BYTES_BUILDER:
                    self._emit(
                        "builder_drop",
                        operands=(binding.value,),
                        attributes={
                            "automatic": True,
                            "owner_type": "BytesBuilder",
                            "scope_exit": "loop_iteration",
                            "state_transition": "Live_to_Dropped",
                        },
                        node=node,
                        result=False,
                    )
                elif binding.type == TEXT_BUILDER:
                    self._emit(
                        "text_builder_drop",
                        operands=(binding.value,),
                        attributes={
                            "automatic": True,
                            "owner_type": "TextBuilder",
                            "scope_exit": "loop_iteration",
                            "state_transition": "Live_to_Dropped",
                        },
                        node=node,
                        result=False,
                    )
                elif binding.type == TEXT:
                    self._emit(
                        "text_drop",
                        operands=(binding.value,),
                        attributes={
                            "automatic": True,
                            "owner_type": "Text",
                            "scope_exit": "loop_iteration",
                        },
                        node=node,
                        result=False,
                    )
            self.bindings.pop(name)

    def _while(self, node: ast.While) -> None:
        if node.orelse:
            raise PerformanceCompileError("while-else is outside Stage 0.5P")
        outer_names = set(self.bindings)
        origin = self.current
        header = self._new_block("while_header")
        body = self._new_block("while_body")
        exit_block = self._new_block("while_exit")
        mapping = _source(self.path, node)
        origin.terminator = MIRTerminator("jump", (header.id,), source=mapping)
        self.current = header
        condition, condition_type = self.expression(node.test, BOOL)
        if condition_type != BOOL:
            raise PerformanceCompileError("while condition must be Bool")
        self._terminate(MIRTerminator("branch", (body.id, exit_block.id), condition, source=mapping))
        self.current = body
        self.statements(node.body)
        self._drop_loop_locals(outer_names, node)
        if self.current.terminator is None:
            self._terminate(MIRTerminator("jump", (header.id,), source=mapping))
        self.current = exit_block

    def _for(self, node: ast.For) -> None:
        if node.orelse or not isinstance(node.target, ast.Name):
            raise PerformanceCompileError("Stage 0.5P for requires a named range iterator")
        call = node.iter
        if (
            not isinstance(call, ast.Call)
            or not isinstance(call.func, ast.Name)
            or call.func.id != "meldra_range"
            or len(call.args) != 2
        ):
            raise PerformanceCompileError("for supports only start..end ranges")
        outer_names = set(self.bindings)
        start, start_type = self.expression(node.iter.args[0], UINT64)
        end, end_type = self.expression(call.args[1], start_type)
        if start_type != end_type or start_type.kind not in {"int", "uint"}:
            raise PerformanceCompileError("range bounds must have the same integer type")
        iterator_slot = str(self._emit("alloc_stack", type_=start_type, attributes={"local": node.target.id}, node=node))
        self._emit("store_local", operands=(iterator_slot, start), node=node, result=False)
        prior = self.bindings.get(node.target.id)
        self.bindings[node.target.id] = _Binding(start_type, iterator_slot, mutable=True)
        origin = self.current
        header = self._new_block("for_header")
        body = self._new_block("for_body")
        exit_block = self._new_block("for_exit")
        mapping = _source(self.path, node)
        origin.terminator = MIRTerminator("jump", (header.id,), source=mapping)
        self.current = header
        iterator = str(self._emit("load_local", type_=start_type, operands=(iterator_slot,), node=node))
        condition = str(self._emit("compare", type_=BOOL, operands=(iterator, end), attributes={"operator": "lt", "range_guard": True}, node=node))
        self._terminate(MIRTerminator("branch", (body.id, exit_block.id), condition, source=mapping))
        self.current = body
        prior_bound = self.range_bounds.get(node.target.id)
        self.range_bounds[node.target.id] = (end, iterator_slot)
        self.statements(node.body)
        self._drop_loop_locals(
            outer_names | {node.target.id}, node
        )
        if self.current.terminator is None:
            current_value = str(self._emit("load_local", type_=start_type, operands=(iterator_slot,), node=node))
            one = str(self._emit("const", type_=start_type, attributes={"value": 1}, node=node))
            next_value = str(self._emit("binary", type_=start_type, operands=(current_value, one), attributes={"operator": "add"}, node=node))
            self._emit("store_local", operands=(iterator_slot, next_value), node=node, result=False)
            self._terminate(MIRTerminator("jump", (header.id,), source=mapping))
        self.current = exit_block
        if prior_bound is None:
            self.range_bounds.pop(node.target.id, None)
        else:
            self.range_bounds[node.target.id] = prior_bound
        if prior is None:
            del self.bindings[node.target.id]
        else:
            self.bindings[node.target.id] = prior

    def _match_utf8_decode(
        self,
        node: ast.Match,
        subject: str,
    ) -> None:
        arms: dict[str, tuple[str, list[ast.stmt]]] = {}
        for case in node.cases:
            pattern = case.pattern
            if (
                case.guard is not None
                or not isinstance(pattern, ast.MatchClass)
                or not isinstance(pattern.cls, ast.Name)
                or pattern.cls.id not in {"Valid", "Invalid"}
                or len(pattern.patterns) != 1
                or pattern.kwd_attrs
                or pattern.kwd_patterns
                or not isinstance(pattern.patterns[0], ast.MatchAs)
                or pattern.patterns[0].name is None
            ):
                raise PerformanceCompileError(
                    "Utf8Decode match requires exhaustive "
                    "Valid(text) and Invalid(error_offset) arms"
                )
            variant = pattern.cls.id
            if variant in arms:
                raise PerformanceCompileError(
                    f"duplicate Utf8Decode match arm {variant}"
                )
            arms[variant] = (
                str(pattern.patterns[0].name),
                case.body,
            )
        if set(arms) != {"Valid", "Invalid"}:
            raise PerformanceCompileError(
                "Utf8Decode match requires exhaustive "
                "Valid(text) and Invalid(error_offset) arms"
            )
        if isinstance(node.subject, ast.Name):
            binding = self.bindings.get(node.subject.id)
            if binding is not None:
                if binding.moved:
                    raise PerformanceCompileError(
                        f"Utf8Decode {node.subject.id} already consumed"
                    )
                self.bindings[node.subject.id] = replace(
                    binding,
                    moved=True,
                    ownership_state="Matched",
                )
        mapping = _source(self.path, node)
        valid_block = self._new_block("utf8_valid")
        invalid_block = self._new_block("utf8_invalid")
        join = self._new_block("utf8_match_join")
        condition = str(
            self._emit(
                "utf8_decode_is_valid",
                type_=BOOL,
                operands=(subject,),
                node=node,
            )
        )
        self._terminate(
            MIRTerminator(
                "branch",
                (valid_block.id, invalid_block.id),
                condition,
                source=mapping,
            )
        )
        terminal_arms = 0
        for variant, block in (
            ("Valid", valid_block),
            ("Invalid", invalid_block),
        ):
            binding_name, statements = arms[variant]
            if binding_name in self.bindings:
                raise PerformanceCompileError(
                    f"Utf8Decode pattern shadows existing value "
                    f"{binding_name}"
                )
            self.current = block
            if variant == "Valid":
                payload_type = TEXT
                payload = str(
                    self._emit(
                        "utf8_decode_take_text",
                        type_=TEXT,
                        operands=(subject,),
                        attributes={
                            "payload_copies": 0,
                            "pointer_identity": "preserved",
                        },
                        node=node,
                    )
                )
            else:
                payload_type = UINT64
                payload = str(
                    self._emit(
                        "utf8_decode_error_offset",
                        type_=UINT64,
                        operands=(subject,),
                        node=node,
                    )
                )
                self._emit(
                    "utf8_decode_drop",
                    operands=(subject,),
                    attributes={"automatic": True},
                    node=node,
                    result=False,
                )
            self.bindings[binding_name] = _Binding(
                payload_type,
                payload,
                ownership_state=(
                    "Live" if payload_type == TEXT else "Borrowed"
                ),
            )
            self.statements(statements)
            payload_binding = self.bindings.pop(binding_name)
            if payload_type == TEXT and not payload_binding.moved:
                self._emit(
                    "text_drop",
                    operands=(payload_binding.value,),
                    attributes={
                        "automatic": True,
                        "owner_type": "Text",
                    },
                    node=node,
                    result=False,
                )
            if self.current.terminator is None:
                self._terminate(
                    MIRTerminator("jump", (join.id,), source=mapping)
                )
            else:
                terminal_arms += 1
        self.current = join
        if terminal_arms == 2:
            self._terminate(
                MIRTerminator("unreachable", source=mapping)
            )

    def _match(self, node: ast.Match) -> None:
        subject, subject_type = self.expression(node.subject)
        if subject_type == UTF8_DECODE:
            self._match_utf8_decode(node, subject)
            return
        join = self._new_block("match_join")
        test_block = self.current
        mapping = _source(self.path, node)
        for index, case in enumerate(node.cases):
            body = self._new_block(f"match_case_{index}")
            is_last = index == len(node.cases) - 1
            if isinstance(case.pattern, ast.MatchAs) and case.pattern.name is None:
                test_block.terminator = MIRTerminator("jump", (body.id,), source=mapping)
            else:
                if isinstance(case.pattern, ast.MatchValue):
                    pattern_node = case.pattern.value
                elif isinstance(case.pattern, ast.MatchSingleton):
                    pattern_node = ast.Constant(case.pattern.value, lineno=node.lineno, col_offset=node.col_offset)
                else:
                    raise PerformanceCompileError("match supports constants and wildcard only")
                self.current = test_block
                pattern, pattern_type = self.expression(pattern_node, subject_type)
                if pattern_type != subject_type:
                    raise PerformanceCompileError("match pattern type mismatch")
                condition = str(self._emit("compare", type_=BOOL, operands=(subject, pattern), attributes={"operator": "eq"}, node=node))
                fallback = self._new_block(f"match_test_{index + 1}")
                test_block.terminator = MIRTerminator("branch", (body.id, fallback.id), condition, source=mapping)
                test_block = fallback
            self.current = body
            self.statements(case.body)
            if self.current.terminator is None:
                self._terminate(MIRTerminator("jump", (join.id,), source=mapping))
            if is_last and test_block.terminator is None:
                test_block.terminator = MIRTerminator("unreachable", source=mapping)
        self.current = join

    def _reject_borrowed_return(self, node: ast.AST) -> None:
        if (
            self.signatures[self.node.name].return_type
            in _BORROWED_VIEW_TYPES
        ):
            return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {
                "borrow",
                "borrow_shared",
                "borrow_mut",
                "meldra_borrow_shared",
                "meldra_borrow_mut",
            }:
                raise PerformanceCompileError(
                    f"borrowed collection cannot escape {self.node.name}"
                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr
            in {"slice", "slice_bytes", "as_view", "as_bytes"}
        ):
            raise PerformanceCompileError(
                f"borrowed view cannot escape {self.node.name}"
            )
        if isinstance(node, ast.Name):
            binding = self.bindings.get(node.id)
            if (
                binding is not None
                and binding.type in _BORROWED_VIEW_TYPES
            ):
                raise PerformanceCompileError(
                    f"borrowed {binding.type.name} {node.id} cannot "
                    f"escape {self.node.name}"
                )
            if (
                binding is not None
                and binding.type.kind in {"array", "slice"}
                and (binding.borrowed or binding.parameter)
            ):
                raise PerformanceCompileError(
                    f"borrowed collection {node.id} cannot escape "
                    f"{self.node.name}"
                )

    def _emit_borrowed_return(
        self,
        value: str,
        node: ast.AST,
    ) -> None:
        origin_name = self.reborrow_analysis.borrowed_return_origins.get(
            self.node.name
        )
        if origin_name is None:
            raise PerformanceCompileError(
                f"BorrowReturnLocalOwnerEscape: {self.node.name} has no "
                "proven borrowed return origin"
            )
        origin = self.bindings[origin_name]
        return_id = (
            f"borrow_return:{self.node.name}:{node.lineno}:"
            f"{getattr(node, 'col_offset', 0)}"
        )
        self._emit(
            "borrow_return_transfer",
            operands=(origin.value, value),
            attributes={
                "borrow_id": return_id,
                "borrow_depth": (
                    self.reborrow_analysis.borrowed_return_depths[
                        self.node.name
                    ]
                ),
                "call_scope": "direct_synchronous",
                "caller_scope": f"caller_of:{self.node.name}",
                "non_escaping": True,
                "parent_borrow": origin.borrow_id,
                "range_relation": "return_inside_borrowed_parameter",
                "return_origin": (
                    f"parameter:{self.node.name}.{origin_name}"
                ),
                "returned_child_borrow": return_id,
                "root_owner": origin.root_owner,
            },
            node=node,
            result=False,
        )
        chain = self.returned_borrows.pop(value, ())
        self.returned_view_metadata.pop(value, None)
        if isinstance(node, ast.Name):
            binding = self.bindings.get(node.id)
            if binding is not None and binding.borrow_chain:
                chain = tuple(binding.borrow_chain)
                self.bindings[node.id] = replace(
                    binding, borrow_chain=()
                )
        self._end_borrow_chain(chain, node)


    def statement(self, node: ast.stmt, *, is_last: bool = False) -> None:
        if self.current.terminator is not None:
            raise PerformanceCompileError(f"unreachable statement at line {node.lineno}")
        if isinstance(node, ast.AnnAssign):
            self._declare(node)
        elif isinstance(node, ast.Assign):
            self._assign(node)
        elif isinstance(node, ast.If):
            self._if(node)
        elif isinstance(node, ast.While):
            self._while(node)
        elif isinstance(node, ast.For):
            self._for(node)
        elif isinstance(node, ast.Match):
            self._match(node)
        elif isinstance(node, ast.Return):
            if node.value is None:
                value, type_ = None, UNIT
            else:
                self._reject_borrowed_return(node.value)
                value, type_ = self.expression(
                    node.value,
                    self.signatures[self.node.name].return_type,
                )
                if isinstance(node.value, ast.Name):
                    binding = self.bindings.get(node.value.id)
                    if (
                        binding is not None
                        and binding.type in _OWNED_TRANSFER_TYPES
                    ):
                        self.bindings[node.value.id] = replace(
                            binding,
                            moved=True,
                            ownership_state=(
                                "Moved"
                                if binding.type
                                in {BYTES_BUILDER, TEXT_BUILDER}
                                else binding.ownership_state
                            ),
                        )
            if type_ != self.signatures[self.node.name].return_type:
                raise PerformanceCompileError(f"return type mismatch in {self.node.name}")
            if type_ in _BORROWED_VIEW_TYPES:
                self._emit_borrowed_return(str(value), node.value or node)
            else:
                self._close_statement_borrows(node)
            self._terminate(MIRTerminator("return", value=value, source=_source(self.path, node)))
        elif isinstance(node, ast.Expr):
            if is_last:
                self._reject_borrowed_return(node.value)
            value, type_ = self.expression(
                node.value,
                self.signatures[self.node.name].return_type if is_last else None,
            )
            if is_last:
                if isinstance(node.value, ast.Name):
                    binding = self.bindings.get(node.value.id)
                    if (
                        binding is not None
                        and binding.type in _OWNED_TRANSFER_TYPES
                    ):
                        self.bindings[node.value.id] = replace(
                            binding,
                            moved=True,
                            ownership_state=(
                                "Moved"
                                if binding.type
                                in {BYTES_BUILDER, TEXT_BUILDER}
                                else binding.ownership_state
                            ),
                        )
                if type_ != self.signatures[self.node.name].return_type:
                    raise PerformanceCompileError(f"tail expression type mismatch in {self.node.name}")
                if type_ in _BORROWED_VIEW_TYPES:
                    self._emit_borrowed_return(str(value), node.value)
                else:
                    self._close_statement_borrows(node)
                self._terminate(MIRTerminator("return", value=value, source=_source(self.path, node)))
        elif isinstance(node, ast.Pass):
            return
        else:
            raise PerformanceCompileError(f"unsupported statement {type(node).__name__} at line {node.lineno}")
        if self.current.terminator is None:
            self._close_statement_borrows(node)

    def statements(self, nodes: list[ast.stmt]) -> None:
        for index, node in enumerate(nodes):
            self.statement(node, is_last=index == len(nodes) - 1 and self.current is self.blocks[-1])

    def lower(self) -> MIRFunction:
        for index, statement in enumerate(self.node.body):
            self.statement(statement, is_last=index == len(self.node.body) - 1)
        if self.current.terminator is None:
            if self.signatures[self.node.name].return_type == UNIT:
                self._terminate(MIRTerminator("return", source=_source(self.path, self.node)))
            else:
                raise PerformanceCompileError(f"function {self.node.name} has no return value")
        for name, binding in self.bindings.items():
            if (
                binding.parameter
                and binding.type in _OWNED_TRANSFER_TYPES
                and not binding.moved
            ):
                raise PerformanceCompileError(
                    f"owned {binding.type.name} parameter {name} is not "
                    "consumed or returned on every path"
                )
            if binding.type == UTF8_DECODE and not binding.moved:
                raise PerformanceCompileError(
                    f"Utf8Decode {name} must be handled by an exhaustive "
                    "Valid/Invalid match"
                )
        blocks = []

        owned_bytes = tuple(
            binding
            for binding in self.bindings.values()
            if binding.type == BYTES and not binding.parameter and not binding.moved
        )
        owned_builders = tuple(
            binding
            for binding in self.bindings.values()
            if binding.type == BYTES_BUILDER
            and not binding.parameter
            and not binding.moved
        )
        owned_text_builders = tuple(
            binding
            for binding in self.bindings.values()
            if binding.type == TEXT_BUILDER
            and not binding.parameter
            and not binding.moved
        )
        owned_texts = tuple(
            binding
            for binding in self.bindings.values()
            if binding.type == TEXT
            and not binding.parameter
            and not binding.moved
        )
        for block in self.blocks:
            terminator = block.terminator or MIRTerminator("unreachable")
            instructions = list(block.instructions)
            if terminator.kind == "return":
                for binding in owned_bytes:
                    if terminator.value == binding.value:
                        continue
                    self.instruction_counter += 1
                    instructions.append(
                        MIRInstruction(
                            f"i{self.instruction_counter}",
                            "drop",
                            operands=(binding.value,),
                            attributes=(("automatic", True), ("owner_type", "Bytes")),
                            source=terminator.source,
                        )
                    )
                for binding in owned_builders:
                    self.instruction_counter += 1
                    instructions.append(
                        MIRInstruction(
                            f"i{self.instruction_counter}",
                            "builder_drop",
                            operands=(binding.value,),
                            attributes=(
                                ("automatic", True),
                                ("owner_type", "BytesBuilder"),
                                ("state_transition", "Live_to_Dropped"),
                            ),
                            source=terminator.source,
                        )
                    )
                for binding in owned_text_builders:
                    self.instruction_counter += 1
                    instructions.append(
                        MIRInstruction(
                            f"i{self.instruction_counter}",
                            "text_builder_drop",
                            operands=(binding.value,),
                            attributes=(
                                ("automatic", True),
                                ("owner_type", "TextBuilder"),
                                ("state_transition", "Live_to_Dropped"),
                            ),
                            source=terminator.source,
                        )
                    )
                for binding in owned_texts:
                    if terminator.value == binding.value:
                        continue
                    self.instruction_counter += 1
                    instructions.append(
                        MIRInstruction(
                            f"i{self.instruction_counter}",
                            "text_drop",
                            operands=(binding.value,),
                            attributes=(
                                ("automatic", True),
                                ("owner_type", "Text"),
                            ),
                            source=terminator.source,
                        )
                    )
            blocks.append(MIRBasicBlock(block.id, tuple(instructions), terminator))
        return MIRFunction(
            self.node.name,
            tuple(self.parameters),
            self.signatures[self.node.name].return_type,
            tuple(blocks),
            blocks[0].id,
            True,
            _source(self.path, self.node),
        )
def _is_shared_annotation(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Shared"
    )


def _validate_shared_scope(statements: list[ast.stmt]) -> None:
    for index, statement in enumerate(statements):
        if isinstance(statement, (ast.If, ast.For, ast.While)):
            _validate_shared_scope(statement.body)
            _validate_shared_scope(statement.orelse)
            control = statement.iter if isinstance(statement, ast.For) else statement.test
            if any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "retain"
                for node in ast.walk(control)
            ):
                raise PerformanceCompileError(
                    "retain result requires a named Shared local with drop or release"
                )
            continue
        if isinstance(statement, ast.Match):
            for case in statement.cases:
                _validate_shared_scope(case.body)
            if any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "retain"
                for node in ast.walk(statement.subject)
            ):
                raise PerformanceCompileError(
                    "retain result requires a named Shared local with drop or release"
                )
            continue
        retain_calls = [
            node
            for node in ast.walk(statement)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "retain"
        ]
        retain_is_named_shared_initializer = (
            isinstance(statement, ast.AnnAssign)
            and _is_shared_annotation(statement.annotation)
            and statement.value in retain_calls
        )
        if retain_calls and not retain_is_named_shared_initializer:
            raise PerformanceCompileError(
                "retain result requires a named Shared local with drop or release"
            )
        if (
            not isinstance(statement, ast.AnnAssign)
            or not isinstance(statement.target, ast.Name)
            or not _is_shared_annotation(statement.annotation)
        ):
            continue
        name = statement.target.id
        closed = False
        for later in statements[index + 1 :]:
            if (
                isinstance(later, ast.Expr)
                and isinstance(later.value, ast.Call)
                and isinstance(later.value.func, ast.Name)
                and later.value.func.id in {"drop", "release"}
                and len(later.value.args) == 1
                and isinstance(later.value.args[0], ast.Name)
                and later.value.args[0].id == name
            ):
                closed = True
                break
            if (
                isinstance(later, ast.Return)
                and isinstance(later.value, ast.Name)
                and later.value.id == name
            ):
                closed = True
                break
            if isinstance(later, ast.Return):
                break
        if not closed:
            raise PerformanceCompileError(
                f"Shared local {name} requires drop or ownership-return in the same scope"
            )



def compile_performance_source(
    source: str,
    *,
    path: str = "main.mlo",
    entry_function: str = "main",
) -> PerformanceFrontendResult:
    if not source.strip():
        raise PerformanceCompileError("empty performance source")
    if re.search(r"(?m)^\s*async\s+fn\b", source):
        raise PerformanceCompileError(
            "async borrowed calls are outside Bytes reborrow scope"
        )
    preprocessed = _preprocess(source)
    try:
        module = ast.parse(preprocessed.source, filename=path)
    except SyntaxError as exc:
        raise PerformanceCompileError(f"{path}:{exc.lineno}:{exc.offset}: {exc.msg}") from exc
    record_nodes = [item for item in module.body if isinstance(item, ast.ClassDef)]
    function_nodes = [item for item in module.body if isinstance(item, ast.FunctionDef)]
    for function_node in function_nodes:
        _validate_shared_scope(function_node.body)
    if len(record_nodes) + len(function_nodes) != len(module.body):
        unsupported = [type(item).__name__ for item in module.body if not isinstance(item, (ast.ClassDef, ast.FunctionDef))]
        raise PerformanceCompileError(f"only record and fn declarations are allowed: {unsupported}")
    record_names = {item.name for item in record_nodes}
    if len(record_names) != len(record_nodes):
        raise PerformanceCompileError("duplicate record declaration")
    records: dict[str, MIRRecord] = {}
    for node in record_nodes:
        fields = []
        for statement in node.body:
            if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name) or statement.value is not None:
                raise PerformanceCompileError(f"record {node.name} may contain typed fields only")
            field_type = _parse_type(statement.annotation, record_names)
            if field_type.kind == "record":
                if field_type.record == node.name:
                    raise PerformanceCompileError(
                        f"SharedCycleUnsupported: recursive record {node.name}"
                    )
                raise PerformanceCompileError(
                    f"nested record {field_type.record} is outside Stage 0.6P layout scope"
                )
            if field_type.kind in {"array", "slice"} or field_type.shared:
                raise PerformanceCompileError(
                    f"record {node.name} collection ownership is outside Stage 0.6P"
                )
            fields.append((statement.target.id, field_type))
        field_tuple = tuple(fields)
        records[node.name] = MIRRecord(
            node.name,
            field_tuple,
            record_layout(node.name, field_tuple),
            _source(path, node),
        )
    signatures: dict[str, _Signature] = {}
    for node in function_nodes:
        if node.name in signatures or node.name in record_names:
            raise PerformanceCompileError(f"duplicate declaration: {node.name}")
        if node.args.vararg or node.args.kwarg or node.args.kwonlyargs or node.args.defaults:
            raise PerformanceCompileError("variadic, keyword-only, and default parameters are outside Stage 0.5P")
        if node.returns is None or any(argument.annotation is None for argument in node.args.args):
            raise PerformanceCompileError(f"fn {node.name} requires complete parameter and return types")
        return_type = _parse_type(node.returns, record_names)
        parameter_types = tuple(
            _parse_type(argument.annotation, record_names)
            for argument in node.args.args
        )
        signatures[node.name] = _Signature(
            parameter_types,
            return_type,
            tuple(argument.arg for argument in node.args.args),
        )
    _validate_builder_call_graph(function_nodes, signatures)
    reborrow_analysis = _validate_reborrow_graph(
        function_nodes, signatures
    )
    if entry_function not in signatures:
        raise PerformanceCompileError(f"missing entry function: {entry_function}")
    functions = tuple(
        _FunctionLowerer(
            node,
            path=path,
            records=records,
            signatures=signatures,
            declaration_kinds=preprocessed.declaration_kinds,
            reborrow_analysis=reborrow_analysis,
        ).lower()
        for node in function_nodes
    )
    mir = PerformanceMIR(
        tuple(records[name] for name in sorted(records)),
        tuple(sorted(functions, key=lambda item: item.name)),
        entry_function,
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
    )
    return PerformanceFrontendResult(
        mir,
        preprocessed.source,
        "ADAPTER_REQUIRED_FROZEN_FRONTEND_LACKS_STAGE05P_FORMS",
        (
            "Stage 0.4 frontend/CoreIR remains frozen and cannot encode var/for/while, fixed arrays, or width-specific scalars.",
        ),
    )


__all__ = [
    "PERFORMANCE_FRONTEND_IMPLEMENTATION_VERSION",
    "PERFORMANCE_FRONTEND_SCHEMA_VERSION",
    "PerformanceCompileError",
    "PerformanceFrontendResult",
    "compile_performance_source",
]
