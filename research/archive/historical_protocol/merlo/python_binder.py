"""Deterministic structural type-aware Python binder for the Stage 0.4 baseline."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping


PYTHON_BINDER_SCHEMA_VERSION = 1
_BUILTIN_TYPES = {"int": "Int", "str": "Text", "bool": "Bool", "None": "Unit"}
_BUILTIN_VALUES = frozenset(
    (
        "False",
        "None",
        "True",
        "abs",
        "bool",
        "dict",
        "enumerate",
        "float",
        "int",
        "len",
        "list",
        "max",
        "min",
        "range",
        "set",
        "str",
        "sum",
        "tuple",
        "zip",
    )
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(prefix: str, value: Any) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()}"


def _module_name(path: str) -> str:
    normalized = PurePosixPath(path.replace("\\", "/"))
    if normalized.suffix != ".py":
        raise ValueError(f"Python source path must end in .py: {path}")
    parts = list(normalized.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts:
        raise ValueError(f"Python source path has no module name: {path}")
    return ".".join(parts)


@dataclass(frozen=True)
class PythonBindingDiagnostic:
    code: str
    message: str
    path: str
    line: int
    column: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "column": self.column,
        }


@dataclass(frozen=True)
class PythonSymbol:
    id: str
    module: str
    qualname: str
    name: str
    kind: str
    path: str
    line: int
    column: int
    parent_id: str | None = None
    public: bool = True

    @property
    def locator(self) -> str:
        return f"{self.module}.{self.qualname}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "module": self.module,
            "qualname": self.qualname,
            "name": self.name,
            "kind": self.kind,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "parent_id": self.parent_id,
            "public": self.public,
        }


@dataclass(frozen=True)
class PythonBindingReference:
    id: str
    path: str
    module: str
    owner_symbol_id: str
    line: int
    column: int
    end_line: int
    end_column: int
    spelling: str
    usage: str
    status: str
    target_symbol_id: str | None = None
    target_binding_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "module": self.module,
            "owner_symbol_id": self.owner_symbol_id,
            "line": self.line,
            "column": self.column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "spelling": self.spelling,
            "usage": self.usage,
            "status": self.status,
            "target_symbol_id": self.target_symbol_id,
            "target_binding_id": self.target_binding_id,
        }


@dataclass(frozen=True)
class PythonBindingReport:
    symbols: tuple[PythonSymbol, ...]
    references: tuple[PythonBindingReference, ...]
    diagnostics: tuple[PythonBindingDiagnostic, ...]
    schema_version: int = PYTHON_BINDER_SCHEMA_VERSION

    @property
    def exact_count(self) -> int:
        return sum(item.status == "Exact" for item in self.references)

    @property
    def unknown_count(self) -> int:
        return sum(item.status == "Unknown" for item in self.references)

    @property
    def foreign_count(self) -> int:
        return sum(item.status == "Foreign" for item in self.references)

    def symbol(self, id_or_locator: str) -> PythonSymbol:
        matches = tuple(
            item
            for item in self.symbols
            if item.id == id_or_locator or item.locator == id_or_locator
        )
        if len(matches) != 1:
            raise KeyError(id_or_locator)
        return matches[0]

    def references_at(
        self, path: str, line: int, column: int, spelling: str
    ) -> tuple[PythonBindingReference, ...]:
        return tuple(
            item
            for item in self.references
            if item.path == path
            and item.line == line
            and item.column == column
            and item.spelling == spelling
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "symbols": [item.to_dict() for item in self.symbols],
            "references": [item.to_dict() for item in self.references],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "binding_counts": {
                "exact": self.exact_count,
                "unknown": self.unknown_count,
                "foreign": self.foreign_count,
                "total": len(self.references),
            },
        }

    def to_json(self) -> str:
        return _canonical(self.to_dict())


@dataclass(frozen=True)
class _Type:
    kind: str
    name: str
    symbol_id: str | None = None


_UNKNOWN = _Type("unknown", "Unknown")
_ANY = _Type("any", "Any")
_INT = _Type("builtin", "Int")
_TEXT = _Type("builtin", "Text")
_BOOL = _Type("builtin", "Bool")
_UNIT = _Type("builtin", "Unit")
_BUILTINS = {"int": _INT, "str": _TEXT, "bool": _BOOL, "None": _UNIT}


@dataclass(frozen=True)
class _Binding:
    id: str
    name: str
    value_type: _Type
    symbol_id: str | None
    kind: str


@dataclass
class _Module:
    path: str
    name: str
    tree: ast.Module
    top: dict[str, PythonSymbol]
    imports: dict[str, _Binding]
    module_imports: dict[str, str]


@dataclass(frozen=True)
class _Signature:
    names: tuple[str, ...]
    types: tuple[_Type, ...]
    returns: _Type


class _PythonBinder:
    def __init__(self, sources: Mapping[str, str]) -> None:
        self.sources = dict(sources)
        self.modules: dict[str, _Module] = {}
        self.symbols: dict[str, PythonSymbol] = {}
        self.top_by_module_name: dict[tuple[str, str], PythonSymbol] = {}
        self.members: dict[tuple[str, str], PythonSymbol] = {}
        self.member_types: dict[str, _Type] = {}
        self.value_types: dict[str, _Type] = {}
        self.signatures: dict[str, _Signature] = {}
        self.references: list[PythonBindingReference] = []
        self.diagnostics: list[PythonBindingDiagnostic] = []
        self.reference_ordinals: dict[str, int] = {}

    def run(self) -> PythonBindingReport:
        self._parse_and_declare()
        self._bind_imports()
        self._resolve_declaration_types()
        self._bind_bodies()
        return PythonBindingReport(
            symbols=tuple(sorted(self.symbols.values(), key=lambda item: item.id)),
            references=tuple(
                sorted(
                    self.references,
                    key=lambda item: (
                        item.path,
                        item.line,
                        item.column,
                        item.id,
                    ),
                )
            ),
            diagnostics=tuple(
                sorted(
                    self.diagnostics,
                    key=lambda item: (
                        item.path,
                        item.line,
                        item.column,
                        item.code,
                    ),
                )
            ),
        )

    def _parse_and_declare(self) -> None:
        for path, source in sorted(self.sources.items()):
            module_name = _module_name(path)
            try:
                tree = ast.parse(source, filename=path, type_comments=True)
            except SyntaxError as exc:
                self._diagnostic(
                    "ParseError",
                    str(exc),
                    path,
                    exc.lineno or 1,
                    exc.offset or 0,
                )
                continue
            if module_name in self.modules:
                self._diagnostic(
                    "DuplicateModule", f"duplicate module {module_name}", path, 1, 0
                )
                continue
            module = _Module(path, module_name, tree, {}, {}, {})
            self.modules[module_name] = module
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._declare_top(module, node.name, "function", node)
                elif isinstance(node, ast.ClassDef):
                    symbol = self._declare_top(module, node.name, "class", node)
                    if symbol is not None:
                        self._declare_class_members(module, symbol, node)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    self._declare_top(module, node.target.id, "value", node.target)
                elif isinstance(node, (ast.Assign, ast.NamedExpr)):
                    targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
                    for target in targets:
                        if isinstance(target, ast.Name):
                            self._declare_top(module, target.id, "value", target)

    def _declare_top(
        self,
        module: _Module,
        name: str,
        kind: str,
        node: ast.AST,
    ) -> PythonSymbol | None:
        if name in module.top:
            self._diagnostic(
                "DuplicateDeclaration",
                f"duplicate declaration {module.name}.{name}",
                module.path,
                getattr(node, "lineno", 1),
                getattr(node, "col_offset", 0),
            )
            return None
        symbol = PythonSymbol(
            id=_digest(
                "pysym", {"module": module.name, "qualname": name, "kind": kind}
            ),
            module=module.name,
            qualname=name,
            name=name,
            kind=kind,
            path=module.path,
            line=getattr(node, "lineno", 1),
            column=getattr(node, "col_offset", 0),
            public=not name.startswith("_"),
        )
        module.top[name] = symbol
        self.symbols[symbol.id] = symbol
        self.top_by_module_name[(module.name, name)] = symbol
        return symbol

    def _declare_class_members(
        self, module: _Module, parent: PythonSymbol, node: ast.ClassDef
    ) -> None:
        names: set[str] = set()
        for item in node.body:
            name: str | None = None
            kind = "member"
            source_node: ast.AST = item
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                name = item.target.id
                kind = "field"
                source_node = item.target
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = item.name
                kind = "method"
            elif isinstance(item, ast.Assign):
                targets = [target for target in item.targets if isinstance(target, ast.Name)]
                if len(targets) == 1:
                    name = targets[0].id
                    kind = "member"
                    source_node = targets[0]
            if name is None:
                continue
            if name in names:
                self._diagnostic(
                    "DuplicateMember",
                    f"duplicate member {parent.locator}.{name}",
                    module.path,
                    getattr(source_node, "lineno", 1),
                    getattr(source_node, "col_offset", 0),
                )
                continue
            names.add(name)
            symbol = PythonSymbol(
                id=_digest(
                    "pysym",
                    {
                        "module": module.name,
                        "qualname": f"{parent.name}.{name}",
                        "kind": kind,
                    },
                ),
                module=module.name,
                qualname=f"{parent.name}.{name}",
                name=name,
                kind=kind,
                path=module.path,
                line=getattr(source_node, "lineno", 1),
                column=getattr(source_node, "col_offset", 0),
                parent_id=parent.id,
                public=parent.public and not name.startswith("_"),
            )
            self.symbols[symbol.id] = symbol
            self.members[(parent.id, name)] = symbol

    def _bind_imports(self) -> None:
        for module in self.modules.values():
            for node in module.tree.body:
                if isinstance(node, ast.ImportFrom):
                    if node.module is None or node.level:
                        self._diagnostic(
                            "UnsupportedImport",
                            "only absolute from-imports are in Support Profile P0",
                            module.path,
                            node.lineno,
                            node.col_offset,
                        )
                        continue
                    for alias in node.names:
                        if alias.name == "*":
                            self._diagnostic(
                                "UnsupportedImport",
                                "wildcard import is outside Support Profile P0",
                                module.path,
                                node.lineno,
                                node.col_offset,
                            )
                            continue
                        local = alias.asname or alias.name
                        target = self.top_by_module_name.get((node.module, alias.name))
                        if target is None:
                            module.imports[local] = _Binding(
                                _digest("pybind", {"module": node.module, "name": alias.name}),
                                local,
                                _ANY,
                                None,
                                "foreign_import",
                            )
                            continue
                        if not target.public:
                            self._diagnostic(
                                "PrivateImport",
                                f"cannot import private symbol {target.locator}",
                                module.path,
                                node.lineno,
                                node.col_offset,
                            )
                            continue
                        module.imports[local] = self._symbol_binding(target, local)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        local = alias.asname or alias.name.split(".", 1)[0]
                        if alias.name in self.modules:
                            module.module_imports[local] = alias.name
                        else:
                            module.imports[local] = _Binding(
                                _digest("pybind", {"module": alias.name}),
                                local,
                                _ANY,
                                None,
                                "foreign_import",
                            )

    def _resolve_declaration_types(self) -> None:
        for module in sorted(self.modules.values(), key=lambda item: item.name):
            for node in module.tree.body:
                if isinstance(node, ast.ClassDef):
                    parent = module.top.get(node.name)
                    if parent is None:
                        continue
                    self.value_types[parent.id] = _Type("type", parent.name, parent.id)
                    for item in node.body:
                        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                            member = self.members.get((parent.id, item.target.id))
                            if member:
                                self.member_types[member.id] = self._annotation_type(
                                    item.annotation,
                                    module,
                                    parent,
                                )
                        elif isinstance(item, ast.Assign):
                            targets = [target for target in item.targets if isinstance(target, ast.Name)]
                            for target_node in targets:
                                member = self.members.get((parent.id, target_node.id))
                                if member:
                                    self.member_types[member.id] = _Type(
                                        "nominal", parent.name, parent.id
                                    )
                        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            member = self.members.get((parent.id, item.name))
                            if member:
                                signature = self._function_signature(item, module, member)
                                self.signatures[member.id] = signature
                                self.member_types[member.id] = _Type(
                                    "callable", member.name, member.id
                                )
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbol = module.top.get(node.name)
                    if symbol:
                        signature = self._function_signature(node, module, symbol)
                        self.signatures[symbol.id] = signature
                        self.value_types[symbol.id] = _Type(
                            "callable", symbol.name, symbol.id
                        )
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    symbol = module.top.get(node.target.id)
                    if symbol:
                        self.value_types[symbol.id] = self._annotation_type(
                            node.annotation, module, symbol
                        )

    def _function_signature(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        module: _Module,
        owner: PythonSymbol,
    ) -> _Signature:
        names: list[str] = []
        types: list[_Type] = []
        parameters = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        for parameter in parameters:
            names.append(parameter.arg)
            types.append(
                self._annotation_type(parameter.annotation, module, owner)
                if parameter.annotation is not None
                else _ANY
            )
        returns = (
            self._annotation_type(node.returns, module, owner)
            if node.returns is not None
            else _ANY
        )
        return _Signature(tuple(names), tuple(types), returns)

    def _annotation_type(
        self,
        node: ast.AST | None,
        module: _Module,
        owner: PythonSymbol,
    ) -> _Type:
        if node is None:
            return _ANY
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            name = node.value
            target = module.top.get(name) or self._import_symbol(module, name)
            if target:
                self._reference_node(module, owner, node, name, "Type", target)
                return _Type("nominal", target.name, target.id)
            return _BUILTINS.get(name, _UNKNOWN)
        if isinstance(node, ast.Name):
            if node.id in _BUILTINS:
                return _BUILTINS[node.id]
            target = module.top.get(node.id) or self._import_symbol(module, node.id)
            if target:
                self._reference_node(module, owner, node, node.id, "Type", target)
                return _Type("nominal", target.name, target.id)
            self._unknown_reference(module, owner, node, node.id, "Type")
            return _UNKNOWN
        return _ANY

    def _bind_bodies(self) -> None:
        for module in sorted(self.modules.values(), key=lambda item: item.name):
            for node in module.tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    owner = module.top.get(node.name)
                    if owner:
                        self._bind_function_body(module, owner, node)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    owner = module.top.get(node.target.id)
                    if owner and node.value is not None:
                        self._infer_expression(module, owner, {}, node.value)
                elif isinstance(node, ast.Assign):
                    owners = [module.top.get(target.id) for target in node.targets if isinstance(target, ast.Name)]
                    for owner in owners:
                        if owner:
                            self._infer_expression(module, owner, {}, node.value)

    def _bind_function_body(
        self,
        module: _Module,
        owner: PythonSymbol,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        scope: dict[str, _Binding] = {}
        signature = self.signatures.get(owner.id, _Signature((), (), _ANY))
        parameters = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        for index, parameter in enumerate(parameters):
            value_type = signature.types[index] if index < len(signature.types) else _ANY
            scope[parameter.arg] = _Binding(
                _digest(
                    "pybind",
                    {
                        "owner": owner.id,
                        "line": parameter.lineno,
                        "column": parameter.col_offset,
                        "name": parameter.arg,
                    },
                ),
                parameter.arg,
                value_type,
                None,
                "parameter",
            )
        self._bind_statements(module, owner, scope, node.body)

    def _bind_statements(
        self,
        module: _Module,
        owner: PythonSymbol,
        scope: dict[str, _Binding],
        statements: list[ast.stmt],
    ) -> None:
        for statement in statements:
            if isinstance(statement, ast.Return):
                if statement.value is not None:
                    self._infer_expression(module, owner, scope, statement.value)
            elif isinstance(statement, ast.Assign):
                value_type = self._infer_expression(
                    module, owner, scope, statement.value
                )
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        scope[target.id] = self._local_binding(owner, target, value_type)
                    else:
                        self._infer_expression(module, owner, scope, target)
            elif isinstance(statement, ast.AnnAssign):
                value_type = self._annotation_type(statement.annotation, module, owner)
                if statement.value is not None:
                    self._infer_expression(module, owner, scope, statement.value)
                if isinstance(statement.target, ast.Name):
                    scope[statement.target.id] = self._local_binding(
                        owner, statement.target, value_type
                    )
            elif isinstance(statement, ast.Expr):
                self._infer_expression(module, owner, scope, statement.value)
            elif isinstance(statement, ast.If):
                self._infer_expression(module, owner, scope, statement.test)
                self._bind_statements(module, owner, dict(scope), statement.body)
                self._bind_statements(module, owner, dict(scope), statement.orelse)
            elif isinstance(statement, (ast.For, ast.While, ast.With, ast.Try, ast.Match)):
                self._diagnostic(
                    "OutOfProfileStatement",
                    f"{type(statement).__name__} is outside Support Profile P0",
                    module.path,
                    statement.lineno,
                    statement.col_offset,
                )

    def _local_binding(
        self, owner: PythonSymbol, node: ast.Name, value_type: _Type
    ) -> _Binding:
        return _Binding(
            _digest(
                "pybind",
                {
                    "owner": owner.id,
                    "line": node.lineno,
                    "column": node.col_offset,
                    "name": node.id,
                },
            ),
            node.id,
            value_type,
            None,
            "local",
        )

    def _infer_expression(
        self,
        module: _Module,
        owner: PythonSymbol,
        scope: dict[str, _Binding],
        node: ast.AST,
        *,
        usage: str = "Value",
    ) -> _Type:
        if isinstance(node, ast.Constant):
            if node.value is None:
                return _UNIT
            if isinstance(node.value, bool):
                return _BOOL
            if isinstance(node.value, int):
                return _INT
            if isinstance(node.value, str):
                return _TEXT
            return _ANY
        if isinstance(node, ast.Name):
            binding = scope.get(node.id)
            if binding:
                self._reference_binding(module, owner, node, node.id, usage, binding)
                return binding.value_type
            symbol = module.top.get(node.id) or self._import_symbol(module, node.id)
            if symbol:
                self._reference_node(module, owner, node, node.id, usage, symbol)
                return self._symbol_type(symbol)
            if node.id in module.module_imports:
                return _Type("module", module.module_imports[node.id])
            if node.id in _BUILTIN_VALUES:
                self._foreign_reference(module, owner, node, node.id, usage)
                return _ANY
            imported = module.imports.get(node.id)
            if imported is not None and imported.symbol_id is None:
                self._foreign_reference(module, owner, node, node.id, usage)
                return imported.value_type
            self._unknown_reference(module, owner, node, node.id, usage)
            return _UNKNOWN
        if isinstance(node, ast.Attribute):
            receiver = self._infer_expression(module, owner, scope, node.value)
            column = max(node.col_offset, node.end_col_offset - len(node.attr))
            if receiver.kind == "module":
                target = self.top_by_module_name.get((receiver.name, node.attr))
            elif receiver.kind in {"nominal", "type"} and receiver.symbol_id:
                target = self.members.get((receiver.symbol_id, node.attr))
            else:
                target = None
            if target is None:
                self._reference_coordinates(
                    module,
                    owner,
                    node,
                    node.attr,
                    "Field",
                    "Unknown",
                    column=column,
                )
                return _UNKNOWN
            self._reference_coordinates(
                module,
                owner,
                node,
                node.attr,
                "Field",
                "Exact",
                target_symbol_id=target.id,
                target_binding_id=target.id,
                column=column,
            )
            return self.member_types.get(target.id, self._symbol_type(target))
        if isinstance(node, ast.Call):
            callee = self._infer_expression(
                module, owner, scope, node.func, usage="Call"
            )
            for argument in node.args:
                self._infer_expression(module, owner, scope, argument)
            for keyword in node.keywords:
                self._infer_expression(module, owner, scope, keyword.value)
            if callee.kind == "callable" and callee.symbol_id:
                return self.signatures.get(
                    callee.symbol_id, _Signature((), (), _ANY)
                ).returns
            if callee.kind == "type" and callee.symbol_id:
                return _Type("nominal", callee.name, callee.symbol_id)
            return _ANY
        if isinstance(node, ast.BinOp):
            left = self._infer_expression(module, owner, scope, node.left)
            right = self._infer_expression(module, owner, scope, node.right)
            return left if left == right else _ANY
        if isinstance(node, (ast.Compare, ast.BoolOp)):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self._infer_expression(module, owner, scope, child)
            return _BOOL
        if isinstance(node, ast.UnaryOp):
            return self._infer_expression(module, owner, scope, node.operand)
        if isinstance(node, ast.IfExp):
            self._infer_expression(module, owner, scope, node.test)
            body = self._infer_expression(module, owner, scope, node.body)
            other = self._infer_expression(module, owner, scope, node.orelse)
            return body if body == other else _ANY
        if isinstance(node, (ast.Tuple, ast.List, ast.Dict, ast.Set)):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self._infer_expression(module, owner, scope, child)
            return _ANY
        self._diagnostic(
            "OutOfProfileExpression",
            f"{type(node).__name__} is outside Support Profile P0",
            module.path,
            getattr(node, "lineno", 1),
            getattr(node, "col_offset", 0),
        )
        return _UNKNOWN

    def _symbol_type(self, symbol: PythonSymbol) -> _Type:
        if symbol.kind == "class":
            return _Type("type", symbol.name, symbol.id)
        if symbol.kind in {"function", "method"}:
            return _Type("callable", symbol.name, symbol.id)
        return self.value_types.get(symbol.id, _ANY)

    def _symbol_binding(self, symbol: PythonSymbol, name: str) -> _Binding:
        return _Binding(
            symbol.id,
            name,
            self._symbol_type(symbol),
            symbol.id,
            symbol.kind,
        )

    def _import_symbol(self, module: _Module, name: str) -> PythonSymbol | None:
        binding = module.imports.get(name)
        if binding is None or binding.symbol_id is None:
            return None
        return self.symbols[binding.symbol_id]

    def _reference_node(
        self,
        module: _Module,
        owner: PythonSymbol,
        node: ast.AST,
        spelling: str,
        usage: str,
        target: PythonSymbol,
    ) -> None:
        self._reference_coordinates(
            module,
            owner,
            node,
            spelling,
            usage,
            "Exact",
            target_symbol_id=target.id,
            target_binding_id=target.id,
        )

    def _reference_binding(
        self,
        module: _Module,
        owner: PythonSymbol,
        node: ast.AST,
        spelling: str,
        usage: str,
        binding: _Binding,
    ) -> None:
        self._reference_coordinates(
            module,
            owner,
            node,
            spelling,
            usage,
            "Exact",
            target_symbol_id=binding.symbol_id,
            target_binding_id=binding.id,
        )

    def _unknown_reference(
        self,
        module: _Module,
        owner: PythonSymbol,
        node: ast.AST,
        spelling: str,
        usage: str,
    ) -> None:
        self._reference_coordinates(
            module, owner, node, spelling, usage, "Unknown"
        )

    def _foreign_reference(
        self,
        module: _Module,
        owner: PythonSymbol,
        node: ast.AST,
        spelling: str,
        usage: str,
    ) -> None:
        self._reference_coordinates(
            module, owner, node, spelling, usage, "Foreign"
        )

    def _reference_coordinates(
        self,
        module: _Module,
        owner: PythonSymbol,
        node: ast.AST,
        spelling: str,
        usage: str,
        status: str,
        *,
        target_symbol_id: str | None = None,
        target_binding_id: str | None = None,
        column: int | None = None,
    ) -> None:
        line = getattr(node, "lineno", 1)
        start_column = getattr(node, "col_offset", 0) if column is None else column
        end_line = getattr(node, "end_lineno", line)
        end_column = (
            start_column + len(spelling)
            if column is not None
            else getattr(node, "end_col_offset", start_column + len(spelling))
        )
        ordinal = self.reference_ordinals.get(owner.id, 0)
        self.reference_ordinals[owner.id] = ordinal + 1
        reference_id = _digest(
            "pyref",
            {
                "owner": owner.id,
                "path": module.path,
                "line": line,
                "column": start_column,
                "spelling": spelling,
                "usage": usage,
                "ordinal": ordinal,
            },
        )
        self.references.append(
            PythonBindingReference(
                reference_id,
                module.path,
                module.name,
                owner.id,
                line,
                start_column,
                end_line,
                end_column,
                spelling,
                usage,
                status,
                target_symbol_id,
                target_binding_id,
            )
        )

    def _diagnostic(
        self,
        code: str,
        message: str,
        path: str,
        line: int,
        column: int,
    ) -> None:
        self.diagnostics.append(
            PythonBindingDiagnostic(code, message, path, line, column)
        )


def bind_python_sources(sources: Mapping[str, str]) -> PythonBindingReport:
    if not sources:
        raise ValueError("at least one Python source is required")
    return _PythonBinder(sources).run()


__all__ = [
    "PYTHON_BINDER_SCHEMA_VERSION",
    "PythonBindingDiagnostic",
    "PythonBindingReference",
    "PythonBindingReport",
    "PythonSymbol",
    "bind_python_sources",
]
