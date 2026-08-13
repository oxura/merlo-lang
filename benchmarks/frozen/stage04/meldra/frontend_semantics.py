"""Closed binder, nominal checker, and deterministic CoreIR lowering for Stage 0.4."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

from .core_semantics import CoreProgram, CoreWorld, compile_core
from .frontend_syntax import (
    Declaration,
    Expression,
    FrontendSyntaxError,
    MatchArm,
    Member,
    ModuleSyntax,
    Parameter,
    SourceCST,
    SourceSpan,
    Statement,
    UseDeclaration,
    parse_source,
)


FRONTEND_HIR_SCHEMA_VERSION = 1
_BUILTIN_NAMES = frozenset(("Int", "Text", "Bool", "Unit"))
_CORE_BUILTINS = {"Int": "Int", "Text": "String", "Bool": "Bool", "Unit": "Unit"}
_TYPE_DECLARATIONS = frozenset(("record", "enum", "newtype"))
_ERROR_TYPE_NAME = "<error>"


class FrontendCompileError(ValueError):
    def __init__(self, diagnostics: Iterable["FrontendDiagnostic"]) -> None:
        self.diagnostics = tuple(diagnostics)
        summary = "; ".join(
            f"{item.code} at {item.path}:{item.span.line}:{item.span.column}"
            for item in self.diagnostics[:5]
        )
        super().__init__(summary or "frontend compilation failed")


@dataclass(frozen=True)
class FrontendDiagnostic:
    code: str
    message: str
    path: str
    span: SourceSpan

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True)
class HIRSymbol:
    syntax_node_id: str
    binding_id: str
    symbol_id: str
    revision_id: str
    package_name: str
    module_name: str
    name: str
    kind: str
    exported: bool
    path: str
    span: SourceSpan
    parent_symbol_id: str | None = None
    contract_json: str = field(default="null", repr=False)
    effects: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    @property
    def contract(self) -> Any:
        return json.loads(self.contract_json)

    @property
    def locator(self) -> str:
        return f"{self.package_name}.{self.module_name}.{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_node_id": self.syntax_node_id,
            "binding_id": self.binding_id,
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "package_name": self.package_name,
            "module_name": self.module_name,
            "name": self.name,
            "kind": self.kind,
            "exported": self.exported,
            "path": self.path,
            "span": self.span.to_dict(),
            "parent_symbol_id": self.parent_symbol_id,
            "contract": self.contract,
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class HIRReference:
    syntax_node_id: str
    owner_symbol_id: str
    binding_id: str
    spelling: str
    usage: str
    path: str
    span: SourceSpan
    ordinal: int
    target_binding_id: str
    target_symbol_id: str | None = None
    status: str = "Exact"

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_node_id": self.syntax_node_id,
            "owner_symbol_id": self.owner_symbol_id,
            "binding_id": self.binding_id,
            "spelling": self.spelling,
            "usage": self.usage,
            "path": self.path,
            "span": self.span.to_dict(),
            "ordinal": self.ordinal,
            "target_binding_id": self.target_binding_id,
            "target_symbol_id": self.target_symbol_id,
            "status": self.status,
        }


@dataclass(frozen=True)
class HIRLocalBinding:
    syntax_node_id: str
    binding_id: str
    owner_symbol_id: str
    name: str
    type_key: str
    kind: str
    path: str
    span: SourceSpan

    def to_dict(self) -> dict[str, Any]:
        return {
            "syntax_node_id": self.syntax_node_id,
            "binding_id": self.binding_id,
            "owner_symbol_id": self.owner_symbol_id,
            "name": self.name,
            "type_key": self.type_key,
            "kind": self.kind,
            "path": self.path,
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True)
class TypedHIR:
    symbols: tuple[HIRSymbol, ...]
    references: tuple[HIRReference, ...]
    local_bindings: tuple[HIRLocalBinding, ...]
    expression_types: tuple[tuple[str, str], ...]
    source_digests: tuple[tuple[str, str], ...]
    package_revisions: tuple[tuple[str, str, str], ...]
    schema_version: int = FRONTEND_HIR_SCHEMA_VERSION

    @property
    def exact_reference_count(self) -> int:
        return sum(item.status == "Exact" for item in self.references)

    @property
    def unknown_internal_reference_count(self) -> int:
        return sum(item.status == "Unknown" for item in self.references)

    def symbol(self, id_or_locator: str) -> HIRSymbol:
        matches = tuple(
            item
            for item in self.symbols
            if item.symbol_id == id_or_locator or item.locator == id_or_locator
        )
        if len(matches) != 1:
            raise KeyError(id_or_locator)
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "symbols": [item.to_dict() for item in self.symbols],
            "references": [item.to_dict() for item in self.references],
            "local_bindings": [item.to_dict() for item in self.local_bindings],
            "expression_types": dict(self.expression_types),
            "source_digests": dict(self.source_digests),
            "package_revisions": [
                {
                    "package": package,
                    "interface_revision_id": interface,
                    "implementation_revision_id": implementation,
                }
                for package, interface, implementation in self.package_revisions
            ],
            "binding_counts": {
                "exact": self.exact_reference_count,
                "unknown_internal": self.unknown_internal_reference_count,
            },
        }


@dataclass(frozen=True)
class FrontendCompilation:
    csts: tuple[SourceCST, ...]
    hir: TypedHIR
    core_program: CoreProgram
    world: CoreWorld

    def to_dict(self) -> dict[str, Any]:
        return {
            "hir": self.hir.to_dict(),
            "core_program": self.core_program.to_dict(),
            "world": self.world.to_dict(),
        }


@dataclass(frozen=True)
class FrontendCheckResult:
    diagnostics: tuple[FrontendDiagnostic, ...]
    compilation: FrontendCompilation | None

    @property
    def ok(self) -> bool:
        return not self.diagnostics and self.compilation is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "compilation": self.compilation.to_dict() if self.compilation else None,
        }


@dataclass(frozen=True)
class _ValueType:
    kind: str
    name: str
    symbol_id: str | None = None

    @property
    def key(self) -> str:
        if self.kind == "builtin":
            return f"builtin:{self.name}"
        if self.kind == "error":
            return _ERROR_TYPE_NAME
        return f"{self.kind}:{self.symbol_id}"

    @property
    def display(self) -> str:
        return self.name


_ERROR_TYPE = _ValueType("error", _ERROR_TYPE_NAME)
_UNIT_TYPE = _ValueType("builtin", "Unit")
_BOOL_TYPE = _ValueType("builtin", "Bool")
_INT_TYPE = _ValueType("builtin", "Int")
_TEXT_TYPE = _ValueType("builtin", "Text")
_BUILTIN_TYPES = {
    "Unit": _UNIT_TYPE,
    "Bool": _BOOL_TYPE,
    "Int": _INT_TYPE,
    "Text": _TEXT_TYPE,
}


@dataclass(frozen=True)
class _Signature:
    parameter_names: tuple[str, ...]
    parameter_types: tuple[_ValueType, ...]
    return_type: _ValueType
    effects: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Binding:
    binding_id: str
    name: str
    value_type: _ValueType
    kind: str
    syntax_id: str
    symbol_id: str | None = None


@dataclass
class _SymbolState:
    syntax_id: str
    binding_id: str
    symbol_id: str
    package_name: str
    module_name: str
    name: str
    kind: str
    exported: bool
    path: str
    span: SourceSpan
    declaration: Declaration | None = None
    member: Member | None = None
    parent_symbol_id: str | None = None
    contract: Any = None
    effects: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    @property
    def locator(self) -> str:
        return f"{self.package_name}.{self.module_name}.{self.name}"


@dataclass
class _ModuleState:
    cst: SourceCST
    syntax: ModuleSyntax
    declarations: dict[str, _SymbolState]
    members: dict[str, _SymbolState]
    imports: dict[str, _SymbolState]
    source_imports: list[tuple[str, _SymbolState]]

    @property
    def key(self) -> tuple[str, str]:
        return (self.syntax.package_name, self.syntax.module_name)

    @property
    def locator(self) -> str:
        return f"{self.syntax.package_name}.{self.syntax.module_name}"


@dataclass
class _BodyContext:
    module: _ModuleState
    owner: _SymbolState
    declaration_kind: str
    declared_effects: set[str]
    available_effects: set[str]
    scopes: list[dict[str, _Binding]]
    required_effects: set[str]


@dataclass(frozen=True)
class _ExprResult:
    value_type: _ValueType
    binding: _Binding | None = None


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(prefix: str, value: Any) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()}"


def _expression_semantic(expression: Expression | None) -> Any:
    if expression is None:
        return None
    return {
        "kind": expression.kind,
        "value": expression.value,
        "name": expression.name,
        "operator": expression.operator,
        "children": [_expression_semantic(item) for item in expression.children],
        "arguments": [
            {"name": item.name, "expression": _expression_semantic(item.expression)}
            for item in expression.arguments
        ],
    }


def _statement_semantic(statement: Statement) -> Any:
    return {
        "kind": statement.kind,
        "name": statement.name,
        "type": statement.type_name,
        "expression": _expression_semantic(statement.expression),
        "effect": statement.effect,
        "body": [_statement_semantic(item) for item in statement.body],
        "else_body": [_statement_semantic(item) for item in statement.else_body],
        "arms": [
            {
                "variant": arm.variant,
                "binding": arm.binding,
                "expression": _expression_semantic(arm.expression),
            }
            for arm in statement.arms
        ],
    }


def _declaration_semantic(declaration: Declaration) -> Any:
    return {
        "kind": declaration.kind,
        "name": declaration.name,
        "parameters": [
            {
                "name": item.name,
                "type": item.type_name,
                "capability": item.capability,
            }
            for item in declaration.parameters
        ],
        "return_type": declaration.return_type,
        "members": [
            {
                "kind": item.kind,
                "name": item.name,
                "type": item.type_name,
                "parameters": [
                    {
                        "name": parameter.name,
                        "type": parameter.type_name,
                        "capability": parameter.capability,
                    }
                    for parameter in item.parameters
                ],
                "return_type": item.return_type,
                "effect": item.effect,
            }
            for item in declaration.members
        ],
        "underlying_type": declaration.underlying_type,
        "value_type": declaration.value_type,
        "value": _expression_semantic(declaration.value),
        "body": [_statement_semantic(item) for item in declaration.body],
    }


class _Compiler:
    def __init__(
        self,
        csts: tuple[SourceCST, ...],
        identity_map: Mapping[str, str],
    ) -> None:
        self.csts = csts
        self.identity_map = dict(identity_map)
        self.diagnostics: list[FrontendDiagnostic] = []
        self.modules: dict[tuple[str, str], _ModuleState] = {}
        self.module_by_locator: dict[str, _ModuleState] = {}
        self.symbols_by_id: dict[str, _SymbolState] = {}
        self.symbols_by_syntax: dict[str, _SymbolState] = {}
        self.member_by_parent_name: dict[tuple[str, str], _SymbolState] = {}
        self.signatures: dict[str, _Signature] = {}
        self.value_types: dict[str, _ValueType] = {}
        self.references: list[HIRReference] = []
        self.local_bindings: list[HIRLocalBinding] = []
        self.expression_types: dict[str, str] = {}
        self.reference_ordinals: dict[str, int] = {}
        self.compilation_digest = _digest(
            "comp",
            [(item.path, item.source_sha256) for item in self.csts],
        )

    def run(self) -> FrontendCompilation | None:
        self._declare_modules_and_symbols()
        self._bind_imports()
        self._reject_package_cycles()
        self._resolve_contracts()
        self._check_bodies()
        if self.diagnostics:
            return None
        core_program = self._lower_core()
        try:
            world = compile_core(core_program)
        except Exception as exc:
            first = self.csts[0]
            self._error(
                "CoreLoweringError",
                str(exc),
                first.path,
                first.module.span,
            )
            return None
        revision_by_id = {item.id: item.revision_id for item in world.symbols}
        hir_symbols = tuple(
            sorted(
                (
                    HIRSymbol(
                        syntax_node_id=state.syntax_id,
                        binding_id=state.binding_id,
                        symbol_id=state.symbol_id,
                        revision_id=revision_by_id[state.symbol_id],
                        package_name=state.package_name,
                        module_name=state.module_name,
                        name=state.name,
                        kind=state.kind,
                        exported=state.exported,
                        path=state.path,
                        span=state.span,
                        parent_symbol_id=state.parent_symbol_id,
                        contract_json=_canonical(state.contract),
                        effects=state.effects,
                        capabilities=state.capabilities,
                    )
                    for state in self.symbols_by_id.values()
                ),
                key=lambda item: item.symbol_id,
            )
        )
        package_revisions = tuple(
            sorted(
                (
                    item.name,
                    item.interface_revision_id,
                    item.implementation_revision_id,
                )
                for item in world.packages
            )
        )
        hir = TypedHIR(
            symbols=hir_symbols,
            references=tuple(
                sorted(
                    self.references,
                    key=lambda item: (
                        item.owner_symbol_id,
                        item.ordinal,
                        item.syntax_node_id,
                    ),
                )
            ),
            local_bindings=tuple(
                sorted(self.local_bindings, key=lambda item: item.binding_id)
            ),
            expression_types=tuple(sorted(self.expression_types.items())),
            source_digests=tuple(
                sorted((item.path, item.source_sha256) for item in self.csts)
            ),
            package_revisions=package_revisions,
        )
        return FrontendCompilation(self.csts, hir, core_program, world)

    def _declare_modules_and_symbols(self) -> None:
        for cst in self.csts:
            syntax = cst.module
            key = (syntax.package_name, syntax.module_name)
            if key in self.modules:
                self._error(
                    "DuplicateModule",
                    f"duplicate module {syntax.package_name}.{syntax.module_name}",
                    cst.path,
                    syntax.span,
                )
                continue
            module = _ModuleState(cst, syntax, {}, {}, {}, [])
            self.modules[key] = module
            self.module_by_locator[module.locator] = module
            declaration_names: set[str] = set()
            export_names = set(syntax.exports)
            for declaration in syntax.declarations:
                if declaration.name in declaration_names:
                    self._error(
                        "DuplicateDeclaration",
                        f"duplicate declaration {declaration.name!r}",
                        cst.path,
                        declaration.span,
                    )
                    continue
                declaration_names.add(declaration.name)
                anchor = (
                    f"{syntax.package_name}.{syntax.module_name}."
                    f"{declaration.name}:{declaration.kind}"
                )
                semantic = _declaration_semantic(declaration)
                symbol_id = self.identity_map.get(
                    anchor,
                    _digest("sym", {"anchor": anchor, "semantic": semantic}),
                )
                state = _SymbolState(
                    syntax_id=declaration.syntax_id,
                    binding_id=self._binding_id(declaration.syntax_id, "symbol"),
                    symbol_id=symbol_id,
                    package_name=syntax.package_name,
                    module_name=syntax.module_name,
                    name=declaration.name,
                    kind=declaration.kind,
                    exported=declaration.name in export_names,
                    path=cst.path,
                    span=declaration.span,
                    declaration=declaration,
                )
                self._register_symbol(state)
                module.declarations[state.name] = state
            for export_name in sorted(export_names - declaration_names):
                self._error(
                    "MissingExport",
                    f"export {export_name!r} has no declaration",
                    cst.path,
                    syntax.span,
                )
            for state in tuple(module.declarations.values()):
                declaration = state.declaration
                assert declaration is not None
                generated_members: list[tuple[str, str, Member | None]] = []
                for member in declaration.members:
                    generated_members.append((member.kind, member.name, member))
                if declaration.kind == "newtype":
                    generated_members.append(("constructor", "new", None))
                for member_kind, member_name, member in generated_members:
                    generated_name = f"{state.name}${member_name}"
                    semantic = {
                        "kind": member_kind,
                        "name": member_name,
                        "type": member.type_name if member else None,
                        "parameters": [
                            {
                                "name": parameter.name,
                                "type": parameter.type_name,
                                "capability": parameter.capability,
                            }
                            for parameter in (member.parameters if member else ())
                        ],
                        "return_type": member.return_type if member else None,
                        "effect": member.effect if member else None,
                    }
                    anchor = f"{state.locator}.{member_name}:{member_kind}"
                    symbol_id = self.identity_map.get(
                        anchor,
                        _digest(
                            "sym",
                            {
                                "anchor": anchor,
                                "parent": state.symbol_id,
                                "semantic": semantic,
                            },
                        ),
                    )
                    member_span = member.span if member else declaration.span
                    member_syntax = member.syntax_id if member else declaration.syntax_id
                    member_state = _SymbolState(
                        syntax_id=member_syntax,
                        binding_id=self._binding_id(
                            member_syntax, f"member:{member_name}"
                        ),
                        symbol_id=symbol_id,
                        package_name=state.package_name,
                        module_name=state.module_name,
                        name=generated_name,
                        kind=member_kind,
                        exported=state.exported,
                        path=state.path,
                        span=member_span,
                        member=member,
                        parent_symbol_id=state.symbol_id,
                    )
                    self._register_symbol(member_state)
                    module.members[generated_name] = member_state
                    self.member_by_parent_name[(state.symbol_id, member_name)] = member_state

    def _register_symbol(self, state: _SymbolState) -> None:
        if state.symbol_id in self.symbols_by_id:
            self._error(
                "DuplicateSymbolId",
                f"duplicate SymbolId {state.symbol_id}",
                state.path,
                state.span,
            )
            return
        self.symbols_by_id[state.symbol_id] = state
        self.symbols_by_syntax[state.syntax_id] = state

    def _bind_imports(self) -> None:
        for module in self.modules.values():
            for use in module.syntax.uses:
                source = self.module_by_locator.get(use.source)
                if source is None:
                    self._error(
                        "ImportModuleNotFound",
                        f"module {use.source!r} does not exist",
                        module.cst.path,
                        use.span,
                    )
                    continue
                for item in use.items:
                    target = source.declarations.get(item.name)
                    if target is None:
                        self._error(
                            "ImportNameNotFound",
                            f"{use.source} has no declaration {item.name!r}",
                            module.cst.path,
                            item.span,
                        )
                        continue
                    if not target.exported:
                        self._error(
                            "PrivateImport",
                            f"{target.locator} is private",
                            module.cst.path,
                            item.span,
                        )
                        continue
                    if item.alias in module.declarations or item.alias in module.imports:
                        self._error(
                            "ImportCollision",
                            f"import alias {item.alias!r} collides in {module.locator}",
                            module.cst.path,
                            item.span,
                        )
                        continue
                    module.imports[item.alias] = target
                    module.source_imports.append((item.alias, target))

    def _reject_package_cycles(self) -> None:
        graph: dict[str, set[str]] = {
            package: set() for package, _ in self.modules
        }
        for module in self.modules.values():
            for _, target in module.source_imports:
                if target.package_name != module.syntax.package_name:
                    graph[module.syntax.package_name].add(target.package_name)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(package: str, stack: tuple[str, ...]) -> None:
            if package in visiting:
                cycle = " -> ".join((*stack, package))
                module = next(
                    item
                    for item in self.modules.values()
                    if item.syntax.package_name == package
                )
                self._error(
                    "PackageCycle",
                    f"cyclic package dependency: {cycle}",
                    module.cst.path,
                    module.syntax.span,
                )
                return
            if package in visited:
                return
            visiting.add(package)
            for dependency in sorted(graph.get(package, ())):
                visit(dependency, (*stack, package))
            visiting.remove(package)
            visited.add(package)

        for package in sorted(graph):
            visit(package, ())

    def _resolve_contracts(self) -> None:
        for module in sorted(self.modules.values(), key=lambda item: item.key):
            for state in sorted(module.declarations.values(), key=lambda item: item.name):
                declaration = state.declaration
                assert declaration is not None
                if declaration.kind == "record":
                    members: dict[str, str] = {}
                    seen: set[str] = set()
                    for member in declaration.members:
                        if member.name in seen:
                            self._error(
                                "DuplicateMember",
                                f"duplicate field {member.name!r}",
                                state.path,
                                member.span,
                            )
                        seen.add(member.name)
                        resolved = self._resolve_type(
                            module, state, member.syntax_id, member.span, member.type_name
                        )
                        members[member.name] = self._core_type(resolved)
                        member_state = self.member_by_parent_name[(state.symbol_id, member.name)]
                        member_state.contract = {"type": self._core_type(resolved)}
                        self.value_types[member_state.symbol_id] = resolved
                    state.contract = {"form": "record", "members": members}
                    self.value_types[state.symbol_id] = _ValueType(
                        "type", state.name, state.symbol_id
                    )
                elif declaration.kind == "enum":
                    members = {}
                    seen = set()
                    for member in declaration.members:
                        if member.name in seen:
                            self._error(
                                "DuplicateMember",
                                f"duplicate variant {member.name!r}",
                                state.path,
                                member.span,
                            )
                        seen.add(member.name)
                        payload = (
                            self._resolve_type(
                                module,
                                state,
                                member.syntax_id,
                                member.span,
                                member.type_name,
                            )
                            if member.type_name
                            else _UNIT_TYPE
                        )
                        members[member.name] = self._core_type(payload)
                        member_state = self.member_by_parent_name[(state.symbol_id, member.name)]
                        member_state.contract = {
                            "enum": state.name,
                            "payload": self._core_type(payload),
                        }
                        self.signatures[member_state.symbol_id] = _Signature(
                            ("value",) if payload != _UNIT_TYPE else (),
                            (payload,) if payload != _UNIT_TYPE else (),
                            _ValueType("nominal", state.name, state.symbol_id),
                        )
                    state.contract = {"form": "enum", "members": members}
                    self.value_types[state.symbol_id] = _ValueType(
                        "type", state.name, state.symbol_id
                    )
                elif declaration.kind == "newtype":
                    underlying = self._resolve_type(
                        module,
                        state,
                        declaration.syntax_id,
                        declaration.span,
                        declaration.underlying_type,
                    )
                    state.contract = {
                        "form": "newtype",
                        "type": self._core_type(underlying),
                    }
                    self.value_types[state.symbol_id] = _ValueType(
                        "type", state.name, state.symbol_id
                    )
                    constructor = self.member_by_parent_name[(state.symbol_id, "new")]
                    constructor.contract = {
                        "args": [{"name": "value", "type": self._core_type(underlying)}],
                        "returns": state.name,
                    }
                    self.signatures[constructor.symbol_id] = _Signature(
                        ("value",),
                        (underlying,),
                        _ValueType("nominal", state.name, state.symbol_id),
                    )
                elif declaration.kind == "capability":
                    effects: set[str] = set()
                    member_contracts: dict[str, Any] = {}
                    for member in declaration.members:
                        signature = self._resolve_signature(
                            module,
                            state,
                            member.parameters,
                            member.return_type,
                            effects=(member.effect,) if member.effect else (),
                        )
                        member_state = self.member_by_parent_name[(state.symbol_id, member.name)]
                        member_state.contract = self._signature_contract(signature)
                        self.signatures[member_state.symbol_id] = signature
                        if member.effect:
                            effects.add(member.effect)
                        member_contracts[member.name] = {
                            **self._signature_contract(signature),
                            "effect": member.effect,
                        }
                    state.effects = tuple(sorted(effects))
                    state.contract = {
                        "form": "capability",
                        "members": member_contracts,
                        "effects": list(state.effects),
                    }
                    self.value_types[state.symbol_id] = _ValueType(
                        "capability_type", state.name, state.symbol_id
                    )
                elif declaration.kind == "value":
                    resolved = self._resolve_type(
                        module,
                        state,
                        declaration.syntax_id,
                        declaration.span,
                        declaration.value_type,
                    )
                    state.contract = {"type": self._core_type(resolved)}
                    self.value_types[state.symbol_id] = resolved
                elif declaration.kind in {"fn", "task"}:
                    effects = tuple(
                        sorted(
                            {
                                statement.effect
                                for statement in declaration.body
                                if statement.kind == "uses" and statement.effect
                            }
                        )
                    )
                    signature = self._resolve_signature(
                        module,
                        state,
                        declaration.parameters,
                        declaration.return_type,
                        effects=effects if declaration.kind == "task" else (),
                    )
                    state.contract = self._signature_contract(signature)
                    state.effects = signature.effects
                    state.capabilities = tuple(
                        parameter.type_name
                        for parameter in declaration.parameters
                        if parameter.capability
                    )
                    self.signatures[state.symbol_id] = signature
                    self.value_types[state.symbol_id] = _ValueType(
                        "callable", state.name, state.symbol_id
                    )

    def _resolve_signature(
        self,
        module: _ModuleState,
        owner: _SymbolState,
        parameters: tuple[Parameter, ...],
        return_name: str | None,
        *,
        effects: tuple[str, ...],
    ) -> _Signature:
        names: list[str] = []
        types: list[_ValueType] = []
        for parameter in parameters:
            if parameter.name in names:
                self._error(
                    "DuplicateBinding",
                    f"duplicate parameter {parameter.name!r}",
                    owner.path,
                    parameter.span,
                )
            names.append(parameter.name)
            types.append(
                self._resolve_type(
                    module,
                    owner,
                    parameter.syntax_id,
                    parameter.span,
                    parameter.type_name,
                    capability=parameter.capability,
                )
            )
        returns = self._resolve_type(
            module,
            owner,
            owner.syntax_id,
            owner.span,
            return_name,
        )
        return _Signature(tuple(names), tuple(types), returns, tuple(sorted(effects)))

    def _resolve_type(
        self,
        module: _ModuleState,
        owner: _SymbolState,
        syntax_id: str,
        span: SourceSpan,
        name: str | None,
        *,
        capability: bool = False,
    ) -> _ValueType:
        if name is None:
            self._error("UnknownType", "missing type", owner.path, span)
            return _ERROR_TYPE
        if name in _BUILTIN_TYPES:
            if capability:
                self._error(
                    "WrongTypeKind",
                    f"builtin {name} is not a capability type",
                    owner.path,
                    span,
                )
                return _ERROR_TYPE
            return _BUILTIN_TYPES[name]
        target = module.declarations.get(name) or module.imports.get(name)
        if target is None:
            self._error(
                "UnknownType",
                f"unknown type {name!r}",
                owner.path,
                span,
            )
            return _ERROR_TYPE
        expected_kinds = {"capability"} if capability else set(_TYPE_DECLARATIONS)
        if target.kind not in expected_kinds:
            self._error(
                "WrongTypeKind",
                f"{name!r} is {target.kind}, expected "
                + ("capability" if capability else "nominal type"),
                owner.path,
                span,
            )
            return _ERROR_TYPE
        usage = "Capability" if capability else "Type"
        self._add_reference(
            owner,
            syntax_id,
            span,
            name,
            usage,
            _Binding(
                target.binding_id,
                name,
                _ValueType(
                    "capability_type" if capability else "type",
                    target.name,
                    target.symbol_id,
                ),
                target.kind,
                target.syntax_id,
                target.symbol_id,
            ),
        )
        return _ValueType(
            "capability" if capability else "nominal",
            target.name,
            target.symbol_id,
        )

    def _check_bodies(self) -> None:
        for module in sorted(self.modules.values(), key=lambda item: item.key):
            for state in sorted(module.declarations.values(), key=lambda item: item.name):
                declaration = state.declaration
                assert declaration is not None
                if declaration.kind == "value" and declaration.value is not None:
                    context = _BodyContext(
                        module, state, "value", set(), set(), [dict()], set()
                    )
                    actual = self._check_expression(declaration.value, context).value_type
                    expected = self.value_types.get(state.symbol_id, _ERROR_TYPE)
                    self._require_same_type(
                        expected,
                        actual,
                        "TypeMismatch",
                        f"value {state.name} expects {expected.display}, got {actual.display}",
                        state.path,
                        declaration.value.span,
                    )
                if declaration.kind not in {"fn", "task"}:
                    continue
                signature = self.signatures[state.symbol_id]
                scopes: list[dict[str, _Binding]] = [dict()]
                available_effects: set[str] = set()
                for parameter, parameter_type in zip(
                    declaration.parameters, signature.parameter_types
                ):
                    binding = _Binding(
                        self._binding_id(parameter.syntax_id, "parameter"),
                        parameter.name,
                        parameter_type,
                        "parameter",
                        parameter.syntax_id,
                    )
                    if parameter.name in scopes[0]:
                        self._error(
                            "DuplicateBinding",
                            f"duplicate parameter {parameter.name!r}",
                            state.path,
                            parameter.span,
                        )
                    scopes[0][parameter.name] = binding
                    self.local_bindings.append(
                        HIRLocalBinding(
                            parameter.syntax_id,
                            binding.binding_id,
                            state.symbol_id,
                            parameter.name,
                            parameter_type.key,
                            "parameter",
                            state.path,
                            parameter.span,
                        )
                    )
                    if parameter.capability and parameter_type.symbol_id:
                        capability_state = self.symbols_by_id.get(parameter_type.symbol_id)
                        if capability_state:
                            available_effects.update(capability_state.effects)
                declared_effects = set(signature.effects)
                if declaration.kind == "fn":
                    uses = [item for item in declaration.body if item.kind == "uses"]
                    for statement in uses:
                        self._error(
                            "EffectInPureFunction",
                            "fn cannot declare effects",
                            state.path,
                            statement.span,
                        )
                else:
                    for effect in sorted(declared_effects - available_effects):
                        self._error(
                            "CapabilityEscalation",
                            f"effect {effect!r} is not covered by the capability environment",
                            state.path,
                            state.span,
                        )
                context = _BodyContext(
                    module,
                    state,
                    declaration.kind,
                    declared_effects,
                    available_effects,
                    scopes,
                    set(),
                )
                actual = self._check_block(declaration.body, context, new_scope=False)
                self._require_same_type(
                    signature.return_type,
                    actual,
                    "ReturnTypeMismatch",
                    f"{state.name} returns {actual.display}, expected {signature.return_type.display}",
                    state.path,
                    state.span,
                )

    def _check_block(
        self,
        statements: tuple[Statement, ...],
        context: _BodyContext,
        *,
        new_scope: bool,
    ) -> _ValueType:
        if new_scope:
            context.scopes.append({})
        result = _UNIT_TYPE
        try:
            for statement in statements:
                if statement.kind == "uses":
                    continue
                if statement.kind == "let":
                    assert statement.expression is not None and statement.name is not None
                    actual = self._check_expression(
                        statement.expression, context
                    ).value_type
                    if statement.type_name:
                        expected = self._resolve_type(
                            context.module,
                            context.owner,
                            statement.syntax_id,
                            statement.span,
                            statement.type_name,
                        )
                        self._require_same_type(
                            expected,
                            actual,
                            "TypeMismatch",
                            f"let {statement.name} expects {expected.display}, got {actual.display}",
                            context.owner.path,
                            statement.span,
                        )
                        actual = expected
                    current_scope = context.scopes[-1]
                    if statement.name in current_scope:
                        self._error(
                            "DuplicateBinding",
                            f"duplicate local binding {statement.name!r}",
                            context.owner.path,
                            statement.span,
                        )
                    binding = _Binding(
                        self._binding_id(statement.syntax_id, "let"),
                        statement.name,
                        actual,
                        "let",
                        statement.syntax_id,
                    )
                    current_scope[statement.name] = binding
                    self.local_bindings.append(
                        HIRLocalBinding(
                            statement.syntax_id,
                            binding.binding_id,
                            context.owner.symbol_id,
                            statement.name,
                            actual.key,
                            "let",
                            context.owner.path,
                            statement.span,
                        )
                    )
                    result = _UNIT_TYPE
                elif statement.kind == "expression":
                    assert statement.expression is not None
                    result = self._check_expression(
                        statement.expression, context
                    ).value_type
                elif statement.kind == "if":
                    assert statement.expression is not None
                    condition = self._check_expression(
                        statement.expression, context
                    ).value_type
                    self._require_same_type(
                        _BOOL_TYPE,
                        condition,
                        "ConditionNotBool",
                        f"if condition has type {condition.display}",
                        context.owner.path,
                        statement.expression.span,
                    )
                    then_type = self._check_block(
                        statement.body, context, new_scope=True
                    )
                    else_type = (
                        self._check_block(
                            statement.else_body, context, new_scope=True
                        )
                        if statement.else_body
                        else _UNIT_TYPE
                    )
                    self._require_same_type(
                        then_type,
                        else_type,
                        "BranchTypeMismatch",
                        f"if branches return {then_type.display} and {else_type.display}",
                        context.owner.path,
                        statement.span,
                    )
                    result = then_type
                elif statement.kind == "match":
                    result = self._check_match(statement, context)
        finally:
            if new_scope:
                context.scopes.pop()
        return result

    def _check_match(
        self, statement: Statement, context: _BodyContext
    ) -> _ValueType:
        assert statement.expression is not None
        subject = self._check_expression(statement.expression, context).value_type
        if subject.kind != "nominal" or subject.symbol_id is None:
            self._error(
                "MatchNonEnum",
                f"match subject has non-enum type {subject.display}",
                context.owner.path,
                statement.expression.span,
            )
            return _ERROR_TYPE
        enum_state = self.symbols_by_id.get(subject.symbol_id)
        if enum_state is None or enum_state.kind != "enum":
            self._error(
                "MatchNonEnum",
                f"match subject has non-enum type {subject.display}",
                context.owner.path,
                statement.expression.span,
            )
            return _ERROR_TYPE
        declaration = enum_state.declaration
        assert declaration is not None
        variants = {member.name: member for member in declaration.members}
        seen: set[str] = set()
        arm_type: _ValueType | None = None
        for arm in statement.arms:
            member = variants.get(arm.variant)
            if member is None:
                self._error(
                    "UnknownVariant",
                    f"unknown variant {arm.variant!r} for {enum_state.name}",
                    context.owner.path,
                    arm.span,
                )
                continue
            if arm.variant in seen:
                self._error(
                    "DuplicateMatchArm",
                    f"duplicate match arm {arm.variant!r}",
                    context.owner.path,
                    arm.span,
                )
            seen.add(arm.variant)
            member_state = self.member_by_parent_name[
                (enum_state.symbol_id, arm.variant)
            ]
            self._add_reference(
                context.owner,
                arm.syntax_id,
                arm.span,
                arm.variant,
                "Pattern",
                _Binding(
                    member_state.binding_id,
                    arm.variant,
                    _ValueType("nominal", enum_state.name, enum_state.symbol_id),
                    "variant",
                    member_state.syntax_id,
                    member_state.symbol_id,
                ),
            )
            context.scopes.append({})
            try:
                if arm.binding:
                    payload_type = (
                        self._resolve_type(
                            context.module,
                            context.owner,
                            arm.syntax_id,
                            arm.span,
                            member.type_name,
                        )
                        if member.type_name
                        else _UNIT_TYPE
                    )
                    binding = _Binding(
                        self._binding_id(arm.syntax_id, "match"),
                        arm.binding,
                        payload_type,
                        "match",
                        arm.syntax_id,
                    )
                    context.scopes[-1][arm.binding] = binding
                    self.local_bindings.append(
                        HIRLocalBinding(
                            arm.syntax_id,
                            binding.binding_id,
                            context.owner.symbol_id,
                            arm.binding,
                            payload_type.key,
                            "match",
                            context.owner.path,
                            arm.span,
                        )
                    )
                current = self._check_expression(arm.expression, context).value_type
            finally:
                context.scopes.pop()
            if arm_type is None:
                arm_type = current
            else:
                self._require_same_type(
                    arm_type,
                    current,
                    "BranchTypeMismatch",
                    f"match arms return {arm_type.display} and {current.display}",
                    context.owner.path,
                    arm.span,
                )
        missing = sorted(set(variants) - seen)
        if missing:
            self._error(
                "NonExhaustiveMatch",
                "missing match variants: " + ", ".join(missing),
                context.owner.path,
                statement.span,
            )
        return arm_type or _ERROR_TYPE

    def _check_expression(
        self, expression: Expression, context: _BodyContext
    ) -> _ExprResult:
        if expression.kind == "literal":
            if expression.value is None:
                value_type = _UNIT_TYPE
            elif isinstance(expression.value, bool):
                value_type = _BOOL_TYPE
            elif isinstance(expression.value, int):
                value_type = _INT_TYPE
            elif isinstance(expression.value, str):
                value_type = _TEXT_TYPE
            else:
                value_type = _ERROR_TYPE
            return self._record_expression(expression, value_type)
        if expression.kind == "name":
            assert expression.name is not None
            binding = self._resolve_value_name(expression, context)
            if binding is None:
                return self._record_expression(expression, _ERROR_TYPE)
            return self._record_expression(expression, binding.value_type, binding)
        if expression.kind == "unary":
            child = self._check_expression(expression.children[0], context).value_type
            self._require_same_type(
                _INT_TYPE,
                child,
                "InvalidUnaryOperand",
                f"operator {expression.operator} expects Int",
                context.owner.path,
                expression.span,
            )
            return self._record_expression(expression, _INT_TYPE)
        if expression.kind == "binary":
            left = self._check_expression(expression.children[0], context).value_type
            right = self._check_expression(expression.children[1], context).value_type
            operator = expression.operator
            if operator in {"==", "!=", "<", "<=", ">", ">="}:
                self._require_same_type(
                    left,
                    right,
                    "InvalidBinaryOperands",
                    f"operator {operator} received {left.display} and {right.display}",
                    context.owner.path,
                    expression.span,
                )
                return self._record_expression(expression, _BOOL_TYPE)
            valid = left == right and left in {_INT_TYPE, _TEXT_TYPE}
            if operator in {"-", "*", "/"}:
                valid = left == right == _INT_TYPE
            if not valid and left != _ERROR_TYPE and right != _ERROR_TYPE:
                self._error(
                    "InvalidBinaryOperands",
                    f"operator {operator} received {left.display} and {right.display}",
                    context.owner.path,
                    expression.span,
                )
                return self._record_expression(expression, _ERROR_TYPE)
            return self._record_expression(expression, left)
        if expression.kind == "field":
            receiver = self._check_expression(expression.children[0], context)
            member = self._resolve_member(expression, receiver.value_type, context)
            if member is None:
                return self._record_expression(expression, _ERROR_TYPE)
            binding = _Binding(
                member.binding_id,
                expression.name or member.name,
                self._member_expression_type(member),
                member.kind,
                member.syntax_id,
                member.symbol_id,
            )
            self._add_reference(
                context.owner,
                expression.syntax_id,
                expression.span,
                expression.name or member.name,
                "Field",
                binding,
            )
            return self._record_expression(expression, binding.value_type, binding)
        if expression.kind == "call":
            callee = self._check_expression(expression.children[0], context)
            value_type = callee.value_type
            if value_type.kind == "type" and value_type.symbol_id:
                state = self.symbols_by_id[value_type.symbol_id]
                if state.kind == "record":
                    signature = self._record_constructor_signature(state)
                    self._check_call_arguments(expression, signature, context)
                    return self._record_expression(
                        expression,
                        _ValueType("nominal", state.name, state.symbol_id),
                    )
            if value_type.kind != "callable" or value_type.symbol_id is None:
                self._error(
                    "CallNonCallable",
                    f"expression of type {value_type.display} is not callable",
                    context.owner.path,
                    expression.span,
                )
                for argument in expression.arguments:
                    self._check_expression(argument.expression, context)
                return self._record_expression(expression, _ERROR_TYPE)
            signature = self.signatures.get(value_type.symbol_id)
            if signature is None:
                self._error(
                    "CallNonCallable",
                    "call target has no signature",
                    context.owner.path,
                    expression.span,
                )
                return self._record_expression(expression, _ERROR_TYPE)
            self._check_call_arguments(expression, signature, context)
            for effect in signature.effects:
                context.required_effects.add(effect)
                if context.declaration_kind != "task":
                    self._error(
                        "EffectInPureFunction",
                        f"pure {context.declaration_kind} cannot call effect {effect}",
                        context.owner.path,
                        expression.span,
                    )
                elif effect not in context.declared_effects:
                    self._error(
                        "EffectNotDeclared",
                        f"effect {effect!r} is not declared by task {context.owner.name}",
                        context.owner.path,
                        expression.span,
                    )
                elif effect not in context.available_effects:
                    self._error(
                        "CapabilityEscalation",
                        f"effect {effect!r} has no available capability",
                        context.owner.path,
                        expression.span,
                    )
            return self._record_expression(expression, signature.return_type)
        self._error(
            "UnsupportedExpression",
            f"unsupported expression kind {expression.kind}",
            context.owner.path,
            expression.span,
        )
        return self._record_expression(expression, _ERROR_TYPE)

    def _resolve_value_name(
        self, expression: Expression, context: _BodyContext
    ) -> _Binding | None:
        assert expression.name is not None
        for scope in reversed(context.scopes):
            binding = scope.get(expression.name)
            if binding is not None:
                self._add_reference(
                    context.owner,
                    expression.syntax_id,
                    expression.span,
                    expression.name,
                    "Local",
                    binding,
                )
                return binding
        state = context.module.declarations.get(expression.name) or context.module.imports.get(
            expression.name
        )
        if state is None:
            self._error(
                "UnknownBinding",
                f"unknown name {expression.name!r}",
                context.owner.path,
                expression.span,
            )
            return None
        if state.kind in _TYPE_DECLARATIONS:
            value_type = _ValueType("type", state.name, state.symbol_id)
        elif state.kind == "capability":
            value_type = _ValueType("capability_type", state.name, state.symbol_id)
        else:
            value_type = self.value_types.get(state.symbol_id, _ERROR_TYPE)
        binding = _Binding(
            state.binding_id,
            expression.name,
            value_type,
            state.kind,
            state.syntax_id,
            state.symbol_id,
        )
        self._add_reference(
            context.owner,
            expression.syntax_id,
            expression.span,
            expression.name,
            "Value",
            binding,
        )
        return binding

    def _resolve_member(
        self,
        expression: Expression,
        receiver: _ValueType,
        context: _BodyContext,
    ) -> _SymbolState | None:
        name = expression.name or ""
        parent_id = receiver.symbol_id
        if receiver.kind not in {"nominal", "type", "capability"} or parent_id is None:
            self._error(
                "UnknownField",
                f"type {receiver.display} has no field {name!r}",
                context.owner.path,
                expression.span,
            )
            return None
        member = self.member_by_parent_name.get((parent_id, name))
        if member is None:
            code = "UnknownVariant" if receiver.kind == "type" else "UnknownField"
            self._error(
                code,
                f"type {receiver.display} has no member {name!r}",
                context.owner.path,
                expression.span,
            )
            return None
        return member

    def _member_expression_type(self, member: _SymbolState) -> _ValueType:
        if member.kind == "field":
            return self.value_types.get(member.symbol_id, _ERROR_TYPE)
        if member.kind == "variant":
            signature = self.signatures.get(member.symbol_id)
            if signature is not None and not signature.parameter_types:
                return signature.return_type
        if member.kind in {"variant", "constructor", "capability_member"}:
            return _ValueType("callable", member.name, member.symbol_id)
        return _ERROR_TYPE

    def _record_constructor_signature(self, state: _SymbolState) -> _Signature:
        declaration = state.declaration
        assert declaration is not None
        names: list[str] = []
        types: list[_ValueType] = []
        for member in declaration.members:
            member_state = self.member_by_parent_name[(state.symbol_id, member.name)]
            names.append(member.name)
            types.append(self.value_types[member_state.symbol_id])
        return _Signature(
            tuple(names),
            tuple(types),
            _ValueType("nominal", state.name, state.symbol_id),
        )

    def _check_call_arguments(
        self,
        expression: Expression,
        signature: _Signature,
        context: _BodyContext,
    ) -> None:
        assigned: dict[int, Expression] = {}
        next_positional = 0
        for argument in expression.arguments:
            if argument.name is None:
                while next_positional in assigned:
                    next_positional += 1
                index = next_positional
                next_positional += 1
                if index >= len(signature.parameter_names):
                    self._error(
                        "ArityMismatch",
                        "too many positional arguments",
                        context.owner.path,
                        argument.span,
                    )
                    self._check_expression(argument.expression, context)
                    continue
            else:
                try:
                    index = signature.parameter_names.index(argument.name)
                except ValueError:
                    self._error(
                        "UnknownArgument",
                        f"unknown argument {argument.name!r}",
                        context.owner.path,
                        argument.span,
                    )
                    self._check_expression(argument.expression, context)
                    continue
            if index in assigned:
                self._error(
                    "DuplicateArgument",
                    f"argument {signature.parameter_names[index]!r} supplied twice",
                    context.owner.path,
                    argument.span,
                )
            assigned[index] = argument.expression
            actual = self._check_expression(argument.expression, context).value_type
            expected = signature.parameter_types[index]
            self._require_same_type(
                expected,
                actual,
                "ArgumentTypeMismatch",
                f"argument {signature.parameter_names[index]} expects {expected.display}, got {actual.display}",
                context.owner.path,
                argument.span,
            )
        missing = [
            name
            for index, name in enumerate(signature.parameter_names)
            if index not in assigned
        ]
        if missing:
            self._error(
                "ArityMismatch",
                "missing arguments: " + ", ".join(missing),
                context.owner.path,
                expression.span,
            )

    def _record_expression(
        self,
        expression: Expression,
        value_type: _ValueType,
        binding: _Binding | None = None,
    ) -> _ExprResult:
        self.expression_types[expression.syntax_id] = value_type.key
        return _ExprResult(value_type, binding)

    def _add_reference(
        self,
        owner: _SymbolState,
        syntax_id: str,
        span: SourceSpan,
        spelling: str,
        usage: str,
        target: _Binding,
    ) -> None:
        ordinal = self.reference_ordinals.get(owner.symbol_id, 0)
        self.reference_ordinals[owner.symbol_id] = ordinal + 1
        self.references.append(
            HIRReference(
                syntax_node_id=syntax_id,
                owner_symbol_id=owner.symbol_id,
                binding_id=self._binding_id(syntax_id, f"reference:{ordinal}"),
                spelling=spelling,
                usage=usage,
                path=owner.path,
                span=span,
                ordinal=ordinal,
                target_binding_id=target.binding_id,
                target_symbol_id=target.symbol_id,
            )
        )

    def _require_same_type(
        self,
        expected: _ValueType,
        actual: _ValueType,
        code: str,
        message: str,
        path: str,
        span: SourceSpan,
    ) -> None:
        if expected == _ERROR_TYPE or actual == _ERROR_TYPE:
            return
        if expected != actual:
            self._error(code, message, path, span)

    def _signature_contract(self, signature: _Signature) -> dict[str, Any]:
        return {
            "args": [
                {"name": name, "type": self._core_type(value_type)}
                for name, value_type in zip(
                    signature.parameter_names, signature.parameter_types
                )
            ],
            "returns": self._core_type(signature.return_type),
        }

    def _core_type(self, value_type: _ValueType) -> str:
        if value_type.kind == "builtin":
            return _CORE_BUILTINS[value_type.name]
        if value_type.kind in {"error", "capability", "capability_type"}:
            return "Any"
        return value_type.name

    def _binding_id(self, syntax_id: str, role: str) -> str:
        return _digest(
            "bind",
            {
                "compilation": self.compilation_digest,
                "syntax": syntax_id,
                "role": role,
            },
        )

    def _lower_core(self) -> CoreProgram:
        packages: list[dict[str, Any]] = []
        package_names = sorted({item.syntax.package_name for item in self.modules.values()})
        for package_name in package_names:
            package_id = _digest("pkg", package_name)
            modules: list[dict[str, Any]] = []
            for module in sorted(
                (
                    item
                    for item in self.modules.values()
                    if item.syntax.package_name == package_name
                ),
                key=lambda item: item.syntax.module_name,
            ):
                module_states = tuple(
                    sorted(
                        (
                            state
                            for state in self.symbols_by_id.values()
                            if state.package_name == package_name
                            and state.module_name == module.syntax.module_name
                        ),
                        key=lambda item: (item.name, item.symbol_id),
                    )
                )
                imports_by_target: dict[str, dict[str, Any]] = {}
                used_aliases: set[str] = set()
                for alias, target in sorted(module.source_imports):
                    imports_by_target[target.symbol_id] = {
                        "package": target.package_name,
                        "module": target.module_name,
                        "name": target.name,
                        "alias": alias,
                    }
                    used_aliases.add(alias)
                owner_ids = {item.symbol_id for item in module_states}
                for reference in self.references:
                    if reference.owner_symbol_id not in owner_ids or reference.target_symbol_id is None:
                        continue
                    target = self.symbols_by_id[reference.target_symbol_id]
                    if (
                        target.package_name == package_name
                        and target.module_name == module.syntax.module_name
                    ):
                        continue
                    if target.symbol_id in imports_by_target:
                        continue
                    alias = "__ref_" + hashlib.sha256(
                        target.symbol_id.encode("utf-8")
                    ).hexdigest()[:12]
                    while alias in used_aliases:
                        alias += "_"
                    used_aliases.add(alias)
                    imports_by_target[target.symbol_id] = {
                        "package": target.package_name,
                        "module": target.module_name,
                        "name": target.name,
                        "alias": alias,
                    }
                declarations = [
                    self._lower_declaration(state) for state in module_states
                ]
                modules.append(
                    {
                        "name": module.syntax.module_name,
                        "imports": sorted(
                            imports_by_target.values(),
                            key=lambda item: (
                                item["package"],
                                item["module"],
                                item["name"],
                                item["alias"],
                            ),
                        ),
                        "exports": sorted(
                            state.name
                            for state in module_states
                            if state.exported and state.parent_symbol_id is None
                        ),
                        "declarations": declarations,
                    }
                )
            packages.append(
                {"id": package_id, "name": package_name, "modules": modules}
            )
        return CoreProgram.from_dict(
            {"schema_version": 1, "packages": packages}
        )

    def _lower_declaration(self, state: _SymbolState) -> dict[str, Any]:
        core_kind = {
            "record": "interface",
            "enum": "interface",
            "newtype": "interface",
            "capability": "capability",
            "value": "value",
            "fn": "function",
            "task": "task",
            "field": "value",
            "variant": "value",
            "constructor": "function",
            "capability_member": "function",
        }[state.kind]
        references = [
            {
                "target_id": item.target_symbol_id,
                "name": item.spelling,
                "usage": item.usage,
            }
            for item in sorted(
                (
                    reference
                    for reference in self.references
                    if reference.owner_symbol_id == state.symbol_id
                    and reference.target_symbol_id is not None
                ),
                key=lambda item: item.ordinal,
            )
        ]
        implementation: Any
        if state.declaration is not None:
            implementation = _declaration_semantic(state.declaration)
        else:
            implementation = {
                "generated_member": state.name,
                "parent_symbol_id": state.parent_symbol_id,
                "kind": state.kind,
            }
        return {
            "id": state.symbol_id,
            "name": state.name,
            "kind": core_kind,
            "export": state.exported,
            "typed_contract": state.contract,
            "implementation": implementation,
            "effects": list(state.effects if core_kind in {"task", "capability"} else ()),
            "capabilities": list(state.capabilities if core_kind == "task" else ()),
            "references": references,
        }

    def _error(
        self,
        code: str,
        message: str,
        path: str,
        span: SourceSpan,
    ) -> None:
        self.diagnostics.append(FrontendDiagnostic(code, message, path, span))


def check_frontend(
    sources: Mapping[str, bytes | str],
    *,
    identity_map: Mapping[str, str] | None = None,
) -> FrontendCheckResult:
    if not sources:
        raise ValueError("at least one Meldra source is required")
    csts: list[SourceCST] = []
    diagnostics: list[FrontendDiagnostic] = []
    for path, source in sorted(sources.items()):
        try:
            csts.append(parse_source(source, path=path))
        except FrontendSyntaxError as exc:
            diagnostics.append(
                FrontendDiagnostic("ParseError", exc.message, exc.path, exc.span)
            )
    if diagnostics:
        return FrontendCheckResult(tuple(diagnostics), None)
    compiler = _Compiler(tuple(csts), identity_map or {})
    compilation = compiler.run()
    ordered = tuple(
        sorted(
            compiler.diagnostics,
            key=lambda item: (
                item.path,
                item.span.start,
                item.code,
                item.message,
            ),
        )
    )
    return FrontendCheckResult(ordered, compilation if not ordered else None)


def compile_frontend(
    sources: Mapping[str, bytes | str],
    *,
    identity_map: Mapping[str, str] | None = None,
) -> FrontendCompilation:
    result = check_frontend(sources, identity_map=identity_map)
    if not result.ok or result.compilation is None:
        raise FrontendCompileError(result.diagnostics)
    return result.compilation


__all__ = [
    "FRONTEND_HIR_SCHEMA_VERSION",
    "FrontendCheckResult",
    "FrontendCompilation",
    "FrontendCompileError",
    "FrontendDiagnostic",
    "HIRLocalBinding",
    "HIRReference",
    "HIRSymbol",
    "TypedHIR",
    "check_frontend",
    "compile_frontend",
]
