"""Versioned Native Typed HIR bridge shared by semantic and native branches.

Stage 0.6P deliberately adapts both frozen frontends instead of creating a third
parser. Performance source is parsed by the frozen Stage 0.5P normalization path;
Stage 0.4 source is adapted from its existing lossless CST and TypedHIR.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .frontend_semantics import FrontendCompilation
from .performance_mir import PerformanceMIR, SourceMapping


NATIVE_HIR_SCHEMA_VERSION = 1
NATIVE_HIR_CONTRACT = "meldra.native-typed-hir.v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _stable_id(prefix: str, *values: Any) -> str:
    raw = _canonical_json(values).encode("utf-8")
    return f"{prefix}_" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class NativeSourceCST:
    path: str
    source: str
    source_sha256: str
    normalized_source: str
    normalized_sha256: str
    adapter: str

    def __post_init__(self) -> None:
        if hashlib.sha256(self.source.encode("utf-8")).hexdigest() != self.source_sha256:
            raise ValueError("NativeSourceCST source digest mismatch")
        if hashlib.sha256(self.normalized_source.encode("utf-8")).hexdigest() != self.normalized_sha256:
            raise ValueError("NativeSourceCST normalized digest mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "source_sha256": self.source_sha256,
            "normalized_sha256": self.normalized_sha256,
            "adapter": self.adapter,
            "lossless": True,
        }


@dataclass(frozen=True)
class NativeHIRNode:
    id: str
    kind: str
    source: SourceMapping | None
    type_name: str | None
    name: str | None = None
    value: Any = None
    operator: str | None = None
    children: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source": self.source.to_dict() if self.source else None,
            "type": self.type_name,
            "name": self.name,
            "value": self.value,
            "operator": self.operator,
            "children": list(self.children),
        }


@dataclass(frozen=True)
class NativeHIRSymbol:
    syntax_node_id: str
    symbol_id: str
    revision_id: str
    name: str
    kind: str
    path: str
    source: SourceMapping
    type_name: str | None = None
    parameter_types: tuple[str, ...] = ()
    return_type: str | None = None
    effects: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    exported: bool = True
    origin: str = "native"

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_node_id": self.syntax_node_id,
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "name": self.name,
            "kind": self.kind,
            "path": self.path,
            "source": self.source.to_dict(),
            "type": self.type_name,
            "parameter_types": list(self.parameter_types),
            "return_type": self.return_type,
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
            "exported": self.exported,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class NativeHIRReference:
    syntax_node_id: str
    owner_symbol_id: str
    target_symbol_id: str | None
    spelling: str
    usage: str
    source: SourceMapping
    status: str = "Exact"

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_node_id": self.syntax_node_id,
            "owner_symbol_id": self.owner_symbol_id,
            "target_symbol_id": self.target_symbol_id,
            "spelling": self.spelling,
            "usage": self.usage,
            "source": self.source.to_dict(),
            "status": self.status,
        }


@dataclass(frozen=True)
class NativeHIRProgram:
    cst: NativeSourceCST
    symbols: tuple[NativeHIRSymbol, ...]
    references: tuple[NativeHIRReference, ...]
    nodes: tuple[NativeHIRNode, ...]
    entry_function: str
    source_kind: str
    performance_mir: PerformanceMIR | None = field(default=None, repr=False, compare=False)
    schema_version: int = NATIVE_HIR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        ids = [item.symbol_id for item in self.symbols]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate Native HIR SymbolId")
        node_ids = [item.id for item in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("duplicate Native HIR node id")
        symbol_ids = set(ids)
        for reference in self.references:
            if reference.owner_symbol_id not in symbol_ids:
                raise ValueError("Native HIR reference has unknown owner")
            if reference.target_symbol_id is not None and reference.target_symbol_id not in symbol_ids:
                raise ValueError("Native HIR reference has unknown target")
        functions = {item.name for item in self.symbols if item.kind == "function"}
        if self.entry_function not in functions:
            raise ValueError(f"Native HIR entry function is missing: {self.entry_function}")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def symbol(self, name_or_id: str) -> NativeHIRSymbol:
        matches = [
            item
            for item in self.symbols
            if item.name == name_or_id or item.symbol_id == name_or_id
        ]
        if len(matches) != 1:
            raise KeyError(name_or_id)
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": NATIVE_HIR_CONTRACT,
            "source_kind": self.source_kind,
            "entry_function": self.entry_function,
            "cst": self.cst.to_dict(),
            "symbols": [item.to_dict() for item in self.symbols],
            "references": [item.to_dict() for item in self.references],
            "nodes": [item.to_dict() for item in self.nodes],
            "invariants": {
                "lossless_source": True,
                "stable_symbol_ids": True,
                "source_mappings": True,
                "effects_and_capabilities_explicit": True,
            },
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())


@dataclass(frozen=True)
class NativeHIRBranchResult:
    kind: str
    symbols: tuple[dict[str, Any], ...]
    references: tuple[dict[str, Any], ...]
    source_mappings: tuple[dict[str, Any], ...]
    payload_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "symbols": list(self.symbols),
            "references": list(self.references),
            "source_mappings": list(self.source_mappings),
            "payload_digest": self.payload_digest,
        }


def _source(path: str, node: ast.AST) -> SourceMapping | None:
    if not hasattr(node, "lineno"):
        return None
    return SourceMapping(
        path,
        int(node.lineno),
        int(getattr(node, "col_offset", 0)),
        int(getattr(node, "end_lineno", node.lineno)),
        int(getattr(node, "end_col_offset", getattr(node, "col_offset", 0))),
    )


def _node_attributes(node: ast.AST) -> tuple[str | None, Any, str | None]:
    name = None
    value = None
    operator = None
    if isinstance(node, ast.Name):
        name = node.id
    elif isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.arg)):
        name = node.name if hasattr(node, "name") else node.arg
    elif isinstance(node, ast.Attribute):
        name = node.attr
    elif isinstance(node, ast.Constant):
        value = node.value
    elif isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare)):
        operator_node = (
            node.op
            if hasattr(node, "op")
            else node.ops[0]
            if isinstance(node, ast.Compare) and node.ops
            else None
        )
        operator = type(operator_node).__name__ if operator_node else None
    return name, value, operator


def _mir_type_map(mir: PerformanceMIR) -> dict[tuple[int, int, str], str]:
    result: dict[tuple[int, int, str], str] = {}
    for function in mir.functions:
        result[(function.source.line, function.source.column, "FunctionDef")] = function.return_type.name
        for block in function.blocks:
            for instruction in block.instructions:
                if instruction.source is None or instruction.type is None:
                    continue
                key = (
                    instruction.source.line,
                    instruction.source.column,
                    "*",
                )
                result.setdefault(key, instruction.type.name)
    return result


def _build_nodes(path: str, module: ast.Module, mir: PerformanceMIR) -> tuple[NativeHIRNode, ...]:
    type_map = _mir_type_map(mir)
    nodes: list[NativeHIRNode] = []
    id_by_object: dict[int, str] = {}
    ordered = []
    seen_objects: set[int] = set()
    for node in ast.walk(module):
        if id(node) in seen_objects:
            continue
        seen_objects.add(id(node))
        ordered.append(node)
    for ordinal, node in enumerate(ordered):
        mapping = _source(path, node)
        node_id = _stable_id(
            "nhirn",
            path,
            type(node).__name__,
            mapping.to_dict() if mapping else ordinal,
        )
        id_by_object[id(node)] = node_id
    for ordinal, node in enumerate(ordered):
        mapping = _source(path, node)
        name, value, operator = _node_attributes(node)
        type_name = None
        if mapping:
            type_name = type_map.get((mapping.line, mapping.column, type(node).__name__))
            type_name = type_name or type_map.get((mapping.line, mapping.column, "*"))
        children = tuple(
            id_by_object[id(child)]
            for child in ast.iter_child_nodes(node)
            if id(child) in id_by_object
        )
        nodes.append(
            NativeHIRNode(
                id_by_object[id(node)],
                type(node).__name__,
                mapping,
                type_name,
                name,
                value,
                operator,
                children,
            )
        )
    return tuple(nodes)


def _native_symbols(path: str, mir: PerformanceMIR) -> tuple[NativeHIRSymbol, ...]:
    symbols: list[NativeHIRSymbol] = []
    for record in mir.records:
        signature = {
            "kind": "record",
            "fields": [(name, type_.name) for name, type_ in record.fields],
        }
        symbol_id = _stable_id("nhirs", path, "record", record.name)
        symbols.append(
            NativeHIRSymbol(
                _stable_id("syn", path, record.name, record.source.to_dict()),
                symbol_id,
                _stable_id("rev", signature),
                record.name,
                "record",
                path,
                record.source,
                type_name=record.name,
            )
        )
        for field_name, field_type in record.fields:
            symbols.append(
                NativeHIRSymbol(
                    _stable_id("syn", path, record.name, field_name),
                    _stable_id("nhirs", path, "field", record.name, field_name),
                    _stable_id("rev", record.name, field_name, field_type.name),
                    f"{record.name}.{field_name}",
                    "field",
                    path,
                    record.source,
                    type_name=field_type.name,
                    exported=False,
                )
            )
    for function in mir.functions:
        parameter_types = tuple(item.type.name for item in function.parameters)
        signature = {
            "kind": "function",
            "parameters": parameter_types,
            "return": function.return_type.name,
            "effects": [],
            "capabilities": [],
        }
        symbols.append(
            NativeHIRSymbol(
                _stable_id("syn", path, function.name, function.source.to_dict()),
                _stable_id("nhirs", path, "function", function.name),
                _stable_id("rev", signature),
                function.name,
                "function",
                path,
                function.source,
                parameter_types=parameter_types,
                return_type=function.return_type.name,
            )
        )
        for parameter in function.parameters:
            symbols.append(
                NativeHIRSymbol(
                    _stable_id("syn", path, function.name, parameter.name),
                    _stable_id("nhirs", path, "parameter", function.name, parameter.name),
                    _stable_id("rev", function.name, parameter.name, parameter.type.name),
                    f"{function.name}.{parameter.name}",
                    "parameter",
                    path,
                    function.source,
                    type_name=parameter.type.name,
                    exported=False,
                )
            )
    return tuple(sorted(symbols, key=lambda item: (item.kind, item.name)))


def _native_references(
    path: str,
    module: ast.Module,
    symbols: tuple[NativeHIRSymbol, ...],
) -> tuple[NativeHIRReference, ...]:
    global_by_name = {
        item.name: item
        for item in symbols
        if item.kind in {"function", "record"}
    }
    functions = [item for item in module.body if isinstance(item, ast.FunctionDef)]
    function_symbols = {
        item.name: next(
            symbol
            for symbol in symbols
            if symbol.kind == "function" and symbol.name == item.name
        )
        for item in functions
    }
    references: list[NativeHIRReference] = []
    for function in functions:
        owner = function_symbols[function.name]
        parent_by_id = {
            id(child): parent
            for parent in ast.walk(function)
            for child in ast.iter_child_nodes(parent)
        }
        local_by_name = {
            symbol.name.split(".", 1)[1]: symbol
            for symbol in symbols
            if symbol.kind == "parameter" and symbol.name.startswith(function.name + ".")
        }
        for node in ast.walk(function):
            if isinstance(node, (ast.AnnAssign, ast.For)):
                target = node.target
                if isinstance(target, ast.Name) and target.id not in local_by_name:
                    mapping = _source(path, target) or owner.source
                    local_by_name[target.id] = NativeHIRSymbol(
                        _stable_id("syn", path, function.name, target.id, mapping.to_dict()),
                        _stable_id("nhirs", path, "local", function.name, target.id, mapping.line),
                        _stable_id("rev", function.name, target.id, mapping.line),
                        f"{function.name}.{target.id}",
                        "local",
                        path,
                        mapping,
                        exported=False,
                    )
            if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
                continue
            target = local_by_name.get(node.id) or global_by_name.get(node.id)
            mapping = _source(path, node) or owner.source
            references.append(
                NativeHIRReference(
                    _stable_id("synref", path, function.name, node.id, mapping.to_dict()),
                    owner.symbol_id,
                    target.symbol_id if target else None,
                    node.id,
                    (
                        "Call"
                        if isinstance(parent_by_id.get(id(node)), ast.Call)
                        and parent_by_id[id(node)].func is node
                        else "Read"
                    ),
                    mapping,
                    "Exact" if target else "BuiltinOrUnknown",
                )
            )
    return tuple(
        sorted(
            references,
            key=lambda item: (item.source.line, item.source.column, item.spelling),
        )
    )


def _adapt_stage06_intrinsics(source: str) -> str:
    return source


def validate_native_source(source: str, *, path: str = "main.mlo") -> None:
    """Validate Stage 0.6P lexical scopes and borrow-only rules before lowering."""

    from .performance_frontend import (
        PerformanceCompileError,
        _preprocess,
    )

    preprocessed = _preprocess(source)
    try:
        module = ast.parse(preprocessed.source, filename=path)
    except SyntaxError as exc:
        raise PerformanceCompileError(
            f"{path}:{exc.lineno}:{exc.offset}: {exc.msg}"
        ) from exc
    globals_ = {
        item.name
        for item in module.body
        if isinstance(item, (ast.FunctionDef, ast.ClassDef))
    }
    globals_.add("Bytes")
    globals_.add("BytesBuilder")
    globals_.add("Text")
    globals_.add("TextBuilder")
    builtins = {
        "len",
        "map",
        "filter",
        "fold",
        "move",
        "drop",
        "borrow",
        "borrow_shared",
        "borrow_mut",
        "retain",
        "release",
        "meldra_range",
        "json_token_checksum",
    }

    def check_expression(
        node: ast.AST,
        defined: set[str],
        mutable: set[str],
    ) -> None:
        parent_by_id = {
            id(child): parent
            for parent in ast.walk(node)
            for child in ast.iter_child_nodes(parent)
        }
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                if (
                    child.func.id == "borrow_mut"
                    and child.args
                    and isinstance(child.args[0], ast.Name)
                    and child.args[0].id not in mutable
                ):
                    raise PerformanceCompileError(
                        f"invalid mutable borrow of immutable value "
                        f"{child.args[0].id} at line {child.lineno}"
                    )
            if not isinstance(child, ast.Name) or not isinstance(child.ctx, ast.Load):
                continue
            parent = parent_by_id.get(id(child))
            if (
                isinstance(parent, ast.Call)
                and parent.func is child
                and child.id in builtins | globals_
            ):
                continue
            if child.id not in defined and child.id not in globals_:
                raise PerformanceCompileError(
                    f"out-of-scope or unknown value {child.id} at line {child.lineno}"
                )

    def check_block(
        statements: list[ast.stmt],
        inherited: set[str],
        inherited_mutable: set[str],
    ) -> None:
        defined = set(inherited)
        mutable = set(inherited_mutable)
        unavailable: set[str] = set()
        borrow_owners: dict[str, str] = {}
        borrow_is_mutable: dict[str, bool] = {}
        for index, statement in enumerate(statements):
            loaded_names = {
                child.id
                for child in ast.walk(statement)
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
            }
            future_names = {
                future_child.id
                for later in statements[index + 1 :]
                for future_child in ast.walk(later)
                if isinstance(future_child, ast.Name)
                and isinstance(future_child.ctx, ast.Load)
            }
            mutated_owners = {
                target.value.id
                for target in (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else ()
                )
                if isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
            }
            for alias, owner in borrow_owners.items():
                alias_is_live = alias in loaded_names or alias in future_names
                if not alias_is_live:
                    continue
                if (
                    borrow_is_mutable.get(alias, False)
                    and owner in loaded_names
                ) or owner in mutated_owners:
                    raise PerformanceCompileError(
                        f"use of {owner} precedes live borrow {alias} "
                        f"at line {statement.lineno}"
                    )
            invalid_names = loaded_names & unavailable
            if invalid_names:
                name = sorted(invalid_names)[0]
                raise PerformanceCompileError(
                    f"use after move or drop of {name} at line {statement.lineno}"
                )
            for child in ast.walk(statement):
                if (
                    not isinstance(child, ast.Call)
                    or not isinstance(child.func, ast.Name)
                    or child.func.id not in {"move", "drop"}
                    or len(child.args) != 1
                    or not isinstance(child.args[0], ast.Name)
                ):
                    continue
                owner = child.args[0].id
                live_aliases = {
                    alias
                    for alias, alias_owner in borrow_owners.items()
                    if alias_owner == owner and alias in future_names
                }
                if live_aliases:
                    alias = sorted(live_aliases)[0]
                    raise PerformanceCompileError(
                        f"{child.func.id} of {owner} precedes live borrow {alias} "
                        f"at line {child.lineno}"
                    )
                unavailable.add(owner)
            if isinstance(statement, ast.AnnAssign):
                if statement.value is not None:
                    check_expression(statement.value, defined, mutable)
                if isinstance(statement.target, ast.Name):
                    defined.add(statement.target.id)
                    if (
                        preprocessed.declaration_kinds.get(
                            (statement.lineno, statement.target.id)
                        )
                        == "var"
                    ):
                        mutable.add(statement.target.id)
                    if (
                        isinstance(statement.value, ast.Call)
                        and isinstance(statement.value.func, ast.Name)
                        and statement.value.func.id
                        in {"borrow", "borrow_shared", "borrow_mut"}
                        and statement.value.args
                        and isinstance(statement.value.args[0], ast.Name)
                    ):
                        borrow_owners[statement.target.id] = (
                            statement.value.args[0].id
                        )
                        borrow_is_mutable[statement.target.id] = (
                            statement.value.func.id == "borrow_mut"
                        )
                    elif (
                        isinstance(statement.value, ast.Call)
                        and isinstance(statement.value.func, ast.Attribute)
                        and statement.value.func.attr
                        in {"slice", "slice_bytes", "as_view", "as_bytes"}
                        and isinstance(statement.value.func.value, ast.Name)
                    ):
                        borrow_owners[statement.target.id] = (
                            statement.value.func.value.id
                        )
                        borrow_is_mutable[statement.target.id] = False
                continue
            if isinstance(statement, ast.Assign):
                check_expression(statement.value, defined, mutable)
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        if target.id not in defined:
                            raise PerformanceCompileError(
                                f"out-of-scope assignment {target.id} at line "
                                f"{statement.lineno}"
                            )
                    elif isinstance(target, ast.Subscript):
                        check_expression(target, defined, mutable)
                continue
            if isinstance(statement, ast.If):
                check_expression(statement.test, defined, mutable)
                check_block(statement.body, defined, mutable)
                check_block(statement.orelse, defined, mutable)
                continue
            if isinstance(statement, ast.While):
                check_expression(statement.test, defined, mutable)
                check_block(statement.body, defined, mutable)
                continue
            if isinstance(statement, ast.For):
                check_expression(statement.iter, defined, mutable)
                loop_defined = set(defined)
                loop_mutable = set(mutable)
                if isinstance(statement.target, ast.Name):
                    loop_defined.add(statement.target.id)
                check_block(statement.body, loop_defined, loop_mutable)
                continue
            if isinstance(statement, ast.Match):
                check_expression(statement.subject, defined, mutable)
                for case in statement.cases:
                    case_defined = set(defined)
                    case_defined.update(
                        child.name
                        for child in ast.walk(case.pattern)
                        if isinstance(child, ast.MatchAs)
                        and child.name is not None
                    )
                    check_block(case.body, case_defined, mutable)
                continue
            if isinstance(statement, ast.Return) and statement.value is not None:
                check_expression(statement.value, defined, mutable)
                continue
            if isinstance(statement, ast.Expr):
                check_expression(statement.value, defined, mutable)

    for function in (
        item for item in module.body if isinstance(item, ast.FunctionDef)
    ):
        parameters = {item.arg for item in function.args.args}
        check_block(function.body, parameters, set())

    if "# ownership: explicit" in source:
        shared_names = re.findall(
            r"(?m)^\s*(?:let|var)\s+([A-Za-z_]\w*)\s*:\s*Shared\[",
            source,
        )
        for name in shared_names:
            if not re.search(rf"\bdrop\(\s*{re.escape(name)}\s*\)", source):
                raise PerformanceCompileError(
                    f"missing drop path for explicit Shared value {name}"
                )


def _synthetic_intrinsic_nodes(
    source: str,
    *,
    path: str,
) -> tuple[NativeHIRNode, ...]:
    nodes = []
    pattern = re.compile(r"\b(borrow|borrow_shared|borrow_mut)\(")
    for line_number, line in enumerate(source.splitlines(), 1):
        for match in pattern.finditer(line):
            kind = {
                "borrow": "BorrowShared",
                "borrow_shared": "BorrowShared",
                "borrow_mut": "BorrowMutable",
            }[match.group(1)]
            mapping = SourceMapping(
                path,
                line_number,
                match.start(),
                line_number,
                match.end(),
            )
            nodes.append(
                NativeHIRNode(
                    _stable_id("nhirn", path, kind, mapping.to_dict()),
                    kind,
                    mapping,
                    None,
                    name=match.group(1),
                )
            )
    return tuple(nodes)


def compile_native_hir(
    source: str,
    *,
    path: str = "main.mlo",
    entry_function: str = "main",
) -> NativeHIRProgram:
    """Adapt frozen Stage 0.5P parsing/lowering into Native Typed HIR v1."""

    # Imports are local to keep the frozen module independent from this Stage 0.6P bridge.
    from .performance_frontend import _preprocess, compile_performance_source

    validate_native_source(source, path=path)
    adapted_source = _adapt_stage06_intrinsics(source)
    frontend = compile_performance_source(
        adapted_source,
        path=path,
        entry_function=entry_function,
    )
    preprocessed = _preprocess(adapted_source)
    module = ast.parse(preprocessed.source, filename=path)
    symbols = list(_native_symbols(path, frontend.mir))
    references = _native_references(path, module, tuple(symbols))
    local_names = {
        (item.owner_symbol_id, item.spelling, item.source.line)
        for item in references
        if item.target_symbol_id is None and item.status != "Exact"
    }
    # Local declarations are semantic symbols even when no later read exists.
    for function in (item for item in module.body if isinstance(item, ast.FunctionDef)):
        owner = next(
            item
            for item in symbols
            if item.kind == "function" and item.name == function.name
        )
        for node in ast.walk(function):
            target = node.target if isinstance(node, (ast.AnnAssign, ast.For)) else None
            if not isinstance(target, ast.Name):
                continue
            mapping = _source(path, target) or owner.source
            symbol_id = _stable_id(
                "nhirs", path, "local", function.name, target.id, mapping.line
            )
            if any(item.symbol_id == symbol_id for item in symbols):
                continue
            symbols.append(
                NativeHIRSymbol(
                    _stable_id("syn", path, function.name, target.id, mapping.to_dict()),
                    symbol_id,
                    _stable_id("rev", function.name, target.id, mapping.line),
                    f"{function.name}.{target.id}",
                    "local",
                    path,
                    mapping,
                    exported=False,
                )
            )
    del local_names
    cst = NativeSourceCST(
        path,
        source,
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
        preprocessed.source,
        hashlib.sha256(preprocessed.source.encode("utf-8")).hexdigest(),
        "stage05p_frozen_performance_parser",
    )
    return NativeHIRProgram(
        cst,
        tuple(sorted(symbols, key=lambda item: (item.kind, item.name, item.symbol_id))),
        references,
        _build_nodes(path, module, frontend.mir)
        + _synthetic_intrinsic_nodes(source, path=path),
        entry_function,
        "stage05p_performance_adapter",
        frontend.mir,
    )


def adapt_stage04_compilation(
    compilation: FrontendCompilation,
    *,
    entry_function: str | None = None,
) -> NativeHIRProgram:
    """Adapt frozen Stage 0.4 TypedHIR without re-parsing semantic rules."""

    if len(compilation.csts) != 1:
        raise ValueError("Stage 0.6P overlap adapter currently requires one Stage 0.4 CST")
    cst04 = compilation.csts[0]
    source = cst04.source_bytes.decode("utf-8")
    symbols = []
    for item in compilation.hir.symbols:
        contract = item.contract if isinstance(item.contract, Mapping) else {}
        signature = contract.get("signature", contract)
        parameter_values = (
            signature.get("parameters", signature.get("args", ()))
            if isinstance(signature, Mapping)
            else ()
        )
        parameters = tuple(
            str(value.get("type", "Unknown"))
            for value in parameter_values
            if isinstance(value, Mapping)
        )
        raw_return_type = (
            signature.get("return_type", signature.get("returns"))
            if isinstance(signature, Mapping)
            else None
        )
        return_type = str(raw_return_type) if raw_return_type is not None else None
        mapping = SourceMapping(
            item.path,
            item.span.line,
            item.span.column,
            item.span.end_line,
            item.span.end_column,
        )
        symbols.append(
            NativeHIRSymbol(
                item.syntax_node_id,
                item.symbol_id,
                item.revision_id,
                item.name,
                "function" if item.kind in {"fn", "task"} else item.kind,
                item.path,
                mapping,
                parameter_types=parameters,
                return_type=return_type,
                effects=item.effects,
                capabilities=item.capabilities,
                exported=item.exported,
                origin="stage04",
            )
        )
    symbol_ids = {item.symbol_id for item in symbols}
    references = tuple(
        NativeHIRReference(
            item.syntax_node_id,
            item.owner_symbol_id,
            item.target_symbol_id if item.target_symbol_id in symbol_ids else None,
            item.spelling,
            item.usage,
            SourceMapping(
                item.path,
                item.span.line,
                item.span.column,
                item.span.end_line,
                item.span.end_column,
            ),
            item.status,
        )
        for item in compilation.hir.references
    )
    function_names = [item.name for item in symbols if item.kind == "function"]
    selected_entry = entry_function or ("main" if "main" in function_names else function_names[0])
    source_mapping = SourceMapping(cst04.path, 1, 0, 1, 0)
    root_node = NativeHIRNode(
        _stable_id("nhirn", cst04.path, cst04.source_sha256),
        "Stage04TypedHIRAdapter",
        source_mapping,
        None,
        children=tuple(item.syntax_node_id for item in symbols),
    )
    cst = NativeSourceCST(
        cst04.path,
        source,
        cst04.source_sha256,
        source,
        cst04.source_sha256,
        "stage04_lossless_cst_typed_hir",
    )
    return NativeHIRProgram(
        cst,
        tuple(symbols),
        references,
        (root_node,),
        selected_entry,
        "stage04_semantic_adapter",
    )


def lower_native_hir_to_semantic(program: NativeHIRProgram) -> NativeHIRBranchResult:
    symbols = tuple(
        {
            "symbol_id": item.symbol_id,
            "revision_id": item.revision_id,
            "name": item.name,
            "kind": item.kind,
            "type": item.type_name,
            "parameters": list(item.parameter_types),
            "return_type": item.return_type,
            "effects": list(item.effects),
            "capabilities": list(item.capabilities),
        }
        for item in program.symbols
    )
    references = tuple(item.to_dict() for item in program.references)
    mappings = tuple(
        item.source.to_dict()
        for item in program.symbols
    )
    payload = {
        "symbols": symbols,
        "references": references,
        "source_mappings": mappings,
    }
    return NativeHIRBranchResult(
        "semantic",
        symbols,
        references,
        mappings,
        hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest(),
    )


def lower_native_hir_to_performance(program: NativeHIRProgram) -> PerformanceMIR:
    if program.performance_mir is None:
        raise ValueError("Stage 0.4 adapter has no Performance MIR for unsupported forms")
    mir = program.performance_mir
    hir_functions = {
        item.name: (item.parameter_types, item.return_type, item.effects)
        for item in program.symbols
        if item.kind == "function"
    }
    mir_functions = {
        item.name: (
            tuple(parameter.type.name for parameter in item.parameters),
            item.return_type.name,
            () if item.pure else ("unknown",),
        )
        for item in mir.functions
    }
    if hir_functions != mir_functions:
        raise AssertionError(
            f"Native HIR / Performance MIR signature divergence: {hir_functions!r} != {mir_functions!r}"
        )
    return mir


def compare_branch_contracts(
    left: NativeHIRProgram,
    right: NativeHIRProgram,
) -> dict[str, Any]:
    def summary(program: NativeHIRProgram) -> dict[str, Any]:
        return {
            "types": sorted(
                (
                    item.name,
                    item.type_name,
                    item.parameter_types,
                    item.return_type,
                )
                for item in program.symbols
                if item.kind in {"record", "function", "field"}
            ),
            "references": sorted(
                (item.spelling, item.usage, item.status)
                for item in program.references
            ),
            "effects": sorted(
                (item.name, item.effects)
                for item in program.symbols
                if item.kind == "function"
            ),
            "capabilities": sorted(
                (item.name, item.capabilities)
                for item in program.symbols
                if item.kind == "function"
            ),
        }

    left_summary = summary(left)
    right_summary = summary(right)
    return {
        "left_digest": left.digest,
        "right_digest": right.digest,
        "types_equal": left_summary["types"] == right_summary["types"],
        "references_equal": left_summary["references"] == right_summary["references"],
        "effects_equal": left_summary["effects"] == right_summary["effects"],
        "capabilities_equal": left_summary["capabilities"] == right_summary["capabilities"],
        "left": left_summary,
        "right": right_summary,
    }


__all__ = [
    "NATIVE_HIR_CONTRACT",
    "NATIVE_HIR_SCHEMA_VERSION",
    "NativeHIRBranchResult",
    "NativeHIRNode",
    "NativeHIRProgram",
    "NativeHIRReference",
    "NativeHIRSymbol",
    "validate_native_source",
    "NativeSourceCST",
    "adapt_stage04_compilation",
    "compare_branch_contracts",
    "compile_native_hir",
    "lower_native_hir_to_performance",
    "lower_native_hir_to_semantic",
]
