from __future__ import annotations

import ast
import builtins
import copy
import hashlib
import io
import tokenize
import warnings
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from research.archive.historical_protocol.merlo.identity import IdentityResolver, IdentitySnapshot
from research.archive.historical_protocol.merlo.model import (
    CallArgument,
    CallEdge,
    Entity,
    FileSnapshot,
    IdentityHint,
    IdentityRelation,
    IdentityStatus,
    Position,
    ProgramIR,
    Provenance,
    Reference,
    Resolution,
    SemanticHazard,
    Span,
)


ANALYZER_VERSION = "python-0.2"
_IGNORED_DIRECTORIES = {
    ".git",
    ".merlo",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
_BUILTIN_NAMES = frozenset(dir(builtins))


class AnalysisError(Exception):
    def __init__(self, messages: Iterable[str]):
        self.messages = tuple(messages)
        super().__init__("\n".join(self.messages))


@dataclass
class _ModuleSource:
    module: str
    path: str
    absolute_path: Path
    source: str
    lines: list[str]
    tree: ast.Module
    is_package: bool
    tokens: tuple[tokenize.TokenInfo, ...]


@dataclass
class _EntityDraft:
    kind: str
    module: str
    qualname: str
    name: str
    file: str
    node: ast.AST
    definition_span: Span
    source_span: Span
    source_hash: str
    revision_hash: str
    signature: str
    signature_span: Span | None
    signature_source: str
    public: bool
    identity_features: dict[str, object]
    entity_id: str = ""
    identity_status: str = IdentityStatus.NEW
    identity_score: float = 0.0
    identity_reason: str = ""

    @property
    def locator(self) -> str:
        return f"{self.module}.{self.qualname}" if self.module else self.qualname


@dataclass(frozen=True)
class _SymbolBinding:
    entity: Entity | None
    rename_uses: bool
    resolution: str
    provenance: str
    possible_target_ids: tuple[str, ...] = ()
    qualifier: str | None = None
    qualifier_span: Span | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class _ModuleBinding:
    module: str
    local_name: str
    qualifier: str
    qualifier_span: Span


@dataclass(frozen=True)
class _Scope:
    kind: str
    bound: frozenset[str]
    globals: frozenset[str]


class _CanonicalNames(ast.NodeTransformer):
    def __init__(self, old_name: str):
        self.old_name = old_name

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id == self.old_name:
            node.id = "$entity"
        return node


class _LocalBindingCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.bound: set[str] = set()
        self.globals: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bound.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.bound.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.bound.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.bound.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.bound.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bound.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Global(self, node: ast.Global) -> None:
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.bound.update(node.names)


class _ImportCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: list[ast.Import | ast.ImportFrom] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


class _ReferenceVisitor(ast.NodeVisitor):
    def __init__(
        self,
        context: _ModuleSource,
        local_entities: Mapping[str, Entity],
        symbol_bindings: Mapping[str, _SymbolBinding],
        module_bindings: Mapping[str, _ModuleBinding],
        entities_by_node: Mapping[int, Entity],
        exports: Mapping[tuple[str, str], _SymbolBinding],
    ) -> None:
        self.context = context
        self.local_entities = local_entities
        self.symbol_bindings = symbol_bindings
        self.module_bindings = module_bindings
        self.entities_by_node = entities_by_node
        self.exports = exports
        self.references: list[Reference] = []
        self.calls: list[CallEdge] = []
        self.hazards: list[SemanticHazard] = []
        self.scopes: list[_Scope] = [_Scope("module", frozenset(), frozenset())]
        self.scoped_symbols: list[dict[str, _SymbolBinding]] = [{}]
        self.scoped_modules: list[dict[str, _ModuleBinding]] = [{}]
        self.owner_id: str | None = None
        self.usage = "Value"
        module_collector = _LocalBindingCollector()
        for statement in context.tree.body:
            module_collector.visit(statement)
        self.module_bound = frozenset(module_collector.bound)

    def _resolve_name(self, name: str) -> _SymbolBinding | None:
        for index in range(len(self.scopes) - 1, 0, -1):
            scope = self.scopes[index]
            if scope.kind == "class":
                continue
            if name in scope.globals:
                break
            binding = self.scoped_symbols[index].get(name)
            if binding is not None:
                return binding
            if name in scope.bound:
                return None
        if name in self.symbol_bindings:
            return self.symbol_bindings[name]
        entity = self.local_entities.get(name)
        if entity is not None:
            return _SymbolBinding(
                entity=entity,
                rename_uses=True,
                resolution=Resolution.EXACT,
                provenance=Provenance.DIRECT_NAME,
            )
        return None

    def _name_is_bound(self, name: str) -> bool:
        for index in range(len(self.scopes) - 1, 0, -1):
            scope = self.scopes[index]
            if scope.kind == "class":
                continue
            if name in scope.globals:
                break
            if name in scope.bound:
                return True
        return (
            name in self.symbol_bindings
            or name in self.local_entities
            or name in self.module_bound
        )

    def _resolve_module(self, name: str) -> _ModuleBinding | None:
        for index in range(len(self.scopes) - 1, 0, -1):
            scope = self.scopes[index]
            if scope.kind == "class":
                continue
            if name in scope.globals:
                break
            module = self.scoped_modules[index].get(name)
            if module is not None:
                return module
            if name in scope.bound:
                return None
        return self.module_bindings.get(name)

    @staticmethod
    def _flatten_attribute(node: ast.AST) -> list[str] | None:
        parts: list[str] = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if not isinstance(current, ast.Name):
            return None
        parts.append(current.id)
        parts.reverse()
        return parts

    def _resolve_expression(self, node: ast.AST) -> _SymbolBinding | None:
        if isinstance(node, ast.Name):
            return self._resolve_name(node.id)
        if not isinstance(node, ast.Attribute):
            return None
        parts = self._flatten_attribute(node)
        if not parts:
            return None
        module_binding = self._resolve_module(parts[0])
        if module_binding is None:
            return None
        suffix = parts[1:]
        module = module_binding.module
        if len(suffix) > 1:
            module = ".".join((module, *suffix[:-1]))
        symbol = suffix[-1] if suffix else ""
        exported = self.exports.get((module, symbol))
        if exported is not None:
            return _SymbolBinding(
                entity=exported.entity,
                rename_uses=exported.rename_uses,
                resolution=Resolution.DERIVED,
                provenance=Provenance.ATTRIBUTE,
                possible_target_ids=(exported.entity.id,) if exported.entity else (),
                qualifier=module_binding.qualifier,
                qualifier_span=module_binding.qualifier_span,
                metadata={
                    "module_alias": module_binding.local_name,
                    "imported_module": module_binding.module,
                },
            )
        return _SymbolBinding(
            entity=None,
            rename_uses=False,
            resolution=Resolution.UNKNOWN,
            provenance=Provenance.EXTERNAL_IMPORT,
            qualifier=module_binding.qualifier,
            qualifier_span=module_binding.qualifier_span,
            metadata={
                "module_alias": module_binding.local_name,
                "imported_module": module_binding.module,
                "attribute": ".".join(suffix),
            },
        )

    def _with_usage(self, usage: str, node: ast.AST) -> None:
        previous = self.usage
        self.usage = usage
        self.visit(node)
        self.usage = previous

    def _add_reference(
        self,
        *,
        target_id: str | None,
        possible_target_ids: tuple[str, ...],
        span: Span,
        kind: str,
        expected: str,
        resolution: str,
        provenance: str,
        rename_on_target: bool,
        usage: str | None = None,
        qualifier: str | None = None,
        qualifier_span: Span | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Reference:
        reference_id = _relation_id(
            "ref",
            self.context.path,
            span,
            expected,
            self.owner_id,
            provenance,
        )
        reference = Reference(
            id=reference_id,
            target_id=target_id,
            possible_target_ids=possible_target_ids,
            file=self.context.path,
            span=span,
            kind=kind,
            expected=expected,
            owner_id=self.owner_id,
            rename_on_target=rename_on_target,
            resolution=resolution,
            provenance=provenance,
            usage=usage or self.usage,
            qualifier=qualifier,
            qualifier_span=qualifier_span,
            metadata=dict(metadata or {}),
        )
        self.references.append(reference)
        return reference

    def _add_binding_reference(
        self, node: ast.Name | ast.Attribute, binding: _SymbolBinding, usage: str | None = None
    ) -> Reference:
        if isinstance(node, ast.Name):
            span = _ast_span(self.context, node)
            expected = node.id
            kind = "name"
        else:
            span = _attribute_name_span(self.context, node)
            expected = node.attr
            kind = "attribute"
        target_id = binding.entity.id if binding.entity is not None else None
        possible = binding.possible_target_ids or ((target_id,) if target_id else ())
        resolution = binding.resolution
        provenance = binding.provenance
        rename_on_target = binding.rename_uses
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            resolution = Resolution.CONDITIONAL
            provenance = Provenance.ATTRIBUTE
            rename_on_target = False
            usage = "MonkeyPatch"
        reference = self._add_reference(
            target_id=target_id,
            possible_target_ids=possible,
            span=span,
            kind=kind,
            expected=expected,
            resolution=resolution,
            provenance=provenance,
            rename_on_target=rename_on_target,
            usage=usage,
            qualifier=binding.qualifier,
            qualifier_span=binding.qualifier_span,
            metadata=binding.metadata,
        )
        if reference.uncertain and reference.possible_target_ids:
            self._add_hazard(
                "uncertain_reference",
                expected,
                span.start.line,
                "reference target is not statically exact",
                reference.id,
            )
        return reference

    def _possible_targets(self, symbol: str, module: str | None = None) -> tuple[str, ...]:
        ids = {
            binding.entity.id
            for (export_module, export_name), binding in self.exports.items()
            if binding.entity is not None
            and export_name == symbol
            and (module is None or export_module == module)
        }
        return tuple(sorted(ids))

    def _add_hazard(
        self,
        kind: str,
        symbol: str,
        line: int,
        message: str,
        reference_id: str | None = None,
    ) -> None:
        self.hazards.append(
            SemanticHazard(
                kind=kind,
                symbol=symbol,
                file=self.context.path,
                line=line,
                message=message,
                reference_id=reference_id,
            )
        )

    def _record_dynamic_lookup(
        self, node: ast.Call, *, usage: str | None = None
    ) -> Reference | None:
        symbol = _dynamic_symbol_name(node)
        if symbol is None:
            return None
        module: str | None = None
        if node.args and isinstance(node.args[0], ast.Name):
            module_binding = self._resolve_module(node.args[0].id)
            if module_binding is not None:
                module = module_binding.module
        possible = self._possible_targets(symbol, module)
        reference = self._add_reference(
            target_id=None,
            possible_target_ids=possible,
            span=_ast_span(self.context, node),
            kind="dynamic",
            expected=symbol,
            resolution=Resolution.DYNAMIC,
            provenance=Provenance.REFLECTION,
            rename_on_target=False,
            usage=usage or self.usage,
            qualifier=module,
            metadata={"lookup": _call_name(node.func) or "reflection"},
        )
        self._add_hazard(
            "dynamic_reference",
            symbol,
            node.lineno,
            f"dynamic lookup of {symbol!r} cannot be migrated statically",
            reference.id,
        )
        return reference

    def visit_Name(self, node: ast.Name) -> None:
        binding = self._resolve_name(node.id)
        if binding is not None:
            self._add_binding_reference(node, binding)
            return
        if (
            isinstance(node.ctx, ast.Load)
            and node.id not in _BUILTIN_NAMES
            and not self._name_is_bound(node.id)
        ):
            possible = self._possible_targets(node.id)
            self._add_reference(
                target_id=None,
                possible_target_ids=possible,
                span=_ast_span(self.context, node),
                kind="unknown_name",
                expected=node.id,
                resolution=Resolution.UNKNOWN,
                provenance=Provenance.DIRECT_NAME,
                rename_on_target=False,
            )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        binding = self._resolve_expression(node)
        if binding is not None:
            self._add_binding_reference(node, binding)
        self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        binding = self._resolve_expression(node.func)
        dynamic_reference: Reference | None = None
        callee_reference: Reference | None = None
        if binding is not None and isinstance(node.func, (ast.Name, ast.Attribute)):
            callee_reference = self._add_binding_reference(
                node.func, binding, usage="CallCallee"
            )
        elif isinstance(node.func, ast.Call):
            dynamic_reference = self._record_dynamic_lookup(
                node.func, usage="CallCallee"
            )
            if dynamic_reference is None:
                self._with_usage("CallCallee", node.func)
            else:
                for argument in node.func.args:
                    self._with_usage("Value", argument)
        else:
            self._with_usage("CallCallee", node.func)

        target_id = callee_reference.target_id if callee_reference else None
        possible = (
            callee_reference.possible_target_ids
            if callee_reference
            else dynamic_reference.possible_target_ids
            if dynamic_reference
            else ()
        )
        resolution = (
            callee_reference.resolution
            if callee_reference
            else dynamic_reference.resolution
            if dynamic_reference
            else Resolution.UNKNOWN
        )
        provenance = (
            callee_reference.provenance
            if callee_reference
            else dynamic_reference.provenance
            if dynamic_reference
            else Provenance.DIRECT_NAME
        )
        reference_id = (
            callee_reference.id
            if callee_reference
            else dynamic_reference.id
            if dynamic_reference
            else None
        )
        arguments = tuple(_call_arguments(node))
        if target_id is not None or possible:
            span = _ast_span(self.context, node)
            self.calls.append(
                CallEdge(
                    id=_relation_id(
                        "call",
                        self.context.path,
                        span,
                        str(target_id or possible),
                        self.owner_id,
                        provenance,
                    ),
                    source_id=self.owner_id,
                    target_id=target_id,
                    possible_target_ids=possible,
                    file=self.context.path,
                    line=node.lineno,
                    column=_ast_position(
                        self.context, node.lineno, node.col_offset
                    ).column,
                    reference_id=reference_id,
                    resolution=resolution,
                    provenance=provenance,
                    arguments=arguments,
                    span=span,
                )
            )

        call_name = _call_name(node.func) or ""
        argument_usage = "Partial" if call_name.endswith("partial") else "Callback"
        for argument in node.args:
            if isinstance(argument, ast.Starred):
                self._with_usage("Variadic", argument.value)
            else:
                self._with_usage(argument_usage, argument)
        for keyword in node.keywords:
            self._with_usage(
                "Variadic" if keyword.arg is None else argument_usage,
                keyword.value,
            )

        if dynamic_reference is None:
            self._record_dynamic_lookup(node)
        if call_name.endswith("import_module") and node.args:
            argument = node.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                reference = self._add_reference(
                    target_id=None,
                    possible_target_ids=(),
                    span=_ast_span(self.context, node),
                    kind="dynamic_import",
                    expected=argument.value,
                    resolution=Resolution.DYNAMIC,
                    provenance=Provenance.REFLECTION,
                    rename_on_target=False,
                    usage="Import",
                )
                self._add_hazard(
                    "dynamic_import",
                    argument.value,
                    node.lineno,
                    "importlib.import_module is resolved only at runtime",
                    reference.id,
                )

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id in {"globals", "locals"}
            and not node.value.args
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            symbol = node.slice.value
            possible = self._possible_targets(
                symbol, self.context.module if node.value.func.id == "globals" else None
            )
            reference = self._add_reference(
                target_id=None,
                possible_target_ids=possible,
                span=_ast_span(self.context, node),
                kind="dynamic_namespace",
                expected=symbol,
                resolution=Resolution.DYNAMIC,
                provenance=Provenance.REFLECTION,
                rename_on_target=False,
            )
            self._add_hazard(
                "dynamic_reference",
                symbol,
                node.lineno,
                f"dynamic namespace lookup of {symbol!r} cannot be migrated statically",
                reference.id,
            )
        self.generic_visit(node)

    def _record_string_exports(self, value: ast.AST) -> None:
        if len(self.scopes) != 1:
            return
        for child in ast.walk(value):
            if not isinstance(child, ast.Constant) or not isinstance(child.value, str):
                continue
            binding = self.exports.get((self.context.module, child.value))
            target_id = binding.entity.id if binding and binding.entity else None
            possible = (target_id,) if target_id else self._possible_targets(child.value)
            reference = self._add_reference(
                target_id=target_id,
                possible_target_ids=possible,
                span=_ast_span(self.context, child),
                kind="string_export",
                expected=child.value,
                resolution=Resolution.CONDITIONAL,
                provenance=Provenance.STRING_LITERAL,
                rename_on_target=False,
                usage="Export",
            )
            self._add_hazard(
                "string_export",
                child.value,
                child.lineno,
                f"string export {child.value!r} requires an explicit compatibility decision",
                reference.id,
            )

    def visit_Assign(self, node: ast.Assign) -> None:
        if any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            self._record_string_exports(node.value)
        for target in node.targets:
            self._with_usage("AssignmentTarget", target)
        self._with_usage("StoredValue", node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if (
            isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
            and node.value is not None
        ):
            self._record_string_exports(node.value)
        self._with_usage("AssignmentTarget", node.target)
        self._with_usage("Annotation", node.annotation)
        if node.value is not None:
            self._with_usage("StoredValue", node.value)

    def visit_Import(self, node: ast.Import) -> None:
        return

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        return

    def _visit_function_header(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        for decorator in node.decorator_list:
            self._with_usage("Decorator", decorator)
        for default in node.args.defaults:
            self._with_usage("DefaultValue", default)
        for default in node.args.kw_defaults:
            if default is not None:
                self._with_usage("DefaultValue", default)
        annotations: list[ast.AST | None] = [
            *(argument.annotation for argument in node.args.posonlyargs),
            *(argument.annotation for argument in node.args.args),
            *(argument.annotation for argument in node.args.kwonlyargs),
            node.args.vararg.annotation if node.args.vararg else None,
            node.args.kwarg.annotation if node.args.kwarg else None,
            node.returns,
        ]
        for annotation in annotations:
            if annotation is not None:
                self._with_usage("Annotation", annotation)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        previous_owner = self.owner_id
        entity = self.entities_by_node.get(id(node))
        if entity is not None:
            self.owner_id = entity.id
        self._visit_function_header(node)
        collector = _LocalBindingCollector()
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            collector.bound.add(argument.arg)
        if node.args.vararg:
            collector.bound.add(node.args.vararg.arg)
        if node.args.kwarg:
            collector.bound.add(node.args.kwarg.arg)
        for statement in node.body:
            collector.visit(statement)
        collector.bound.difference_update(collector.globals)

        symbols, modules, import_refs, import_hazards = _build_import_bindings(
            self.context,
            self.exports,
            statements=node.body,
            owner_id=self.owner_id,
        )
        self.references.extend(import_refs)
        self.hazards.extend(import_hazards)
        self.scopes.append(
            _Scope(
                "function",
                frozenset(collector.bound),
                frozenset(collector.globals),
            )
        )
        self.scoped_symbols.append(symbols)
        self.scoped_modules.append(modules)
        for statement in node.body:
            self.visit(statement)
        self.scoped_modules.pop()
        self.scoped_symbols.pop()
        self.scopes.pop()
        self.owner_id = previous_owner

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        previous_owner = self.owner_id
        entity = self.entities_by_node.get(id(node))
        if entity is not None:
            self.owner_id = entity.id
        for decorator in node.decorator_list:
            self._with_usage("Decorator", decorator)
        for base in node.bases:
            self._with_usage("BaseClass", base)
        for keyword in node.keywords:
            self._with_usage("BaseClass", keyword.value)

        bound = {
            child.name
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        symbols, modules, import_refs, import_hazards = _build_import_bindings(
            self.context,
            self.exports,
            statements=node.body,
            owner_id=self.owner_id,
        )
        self.references.extend(import_refs)
        self.hazards.extend(import_hazards)
        self.scopes.append(_Scope("class", frozenset(bound), frozenset()))
        self.scoped_symbols.append(symbols)
        self.scoped_modules.append(modules)
        for statement in node.body:
            self.visit(statement)
        self.scoped_modules.pop()
        self.scoped_symbols.pop()
        self.scopes.pop()
        self.owner_id = previous_owner


def scan_python(
    root: str | Path,
    previous: ProgramIR | None = None,
    *,
    identity_hints: Iterable[IdentityHint] = (),
) -> ProgramIR:
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise AnalysisError([f"workspace is not a directory: {root_path}"])

    modules, files = _load_modules(root_path)
    drafts: list[_EntityDraft] = []
    draft_by_node: dict[int, _EntityDraft] = {}
    for context in modules:
        discovered = _discover_entities(context)
        drafts.extend(discovered)
        draft_by_node.update({id(item.node): item for item in discovered})

    snapshots = tuple(
        IdentitySnapshot(
            kind=draft.kind,
            module=draft.module,
            qualname=draft.qualname,
            name=draft.name,
            revision_hash=draft.revision_hash,
            source_hash=draft.source_hash,
            features=draft.identity_features,
        )
        for draft in drafts
    )
    resolution = IdentityResolver().resolve(
        previous.entities if previous is not None else (),
        snapshots,
        identity_hints,
    )
    for draft in drafts:
        assignment = resolution.assignments[draft.locator]
        draft.entity_id = assignment.entity_id
        draft.identity_status = assignment.status
        draft.identity_score = assignment.score
        draft.identity_reason = assignment.reason

    entities = tuple(
        Entity(
            id=draft.entity_id,
            kind=draft.kind,
            module=draft.module,
            qualname=draft.qualname,
            name=draft.name,
            file=draft.file,
            definition_span=draft.definition_span,
            source_span=draft.source_span,
            source_hash=draft.source_hash,
            signature_span=draft.signature_span,
            signature_source=draft.signature_source,
            revision_hash=draft.revision_hash,
            signature=draft.signature,
            public=draft.public,
            identity_status=draft.identity_status,
            identity_score=draft.identity_score,
            identity_reason=draft.identity_reason,
            identity_features=draft.identity_features,
        )
        for draft in sorted(drafts, key=lambda item: (item.module, item.qualname))
    )
    entity_by_draft = {
        id(draft): next(entity for entity in entities if entity.id == draft.entity_id)
        for draft in drafts
    }
    entities_by_node = {
        node_id: entity_by_draft[id(draft)]
        for node_id, draft in draft_by_node.items()
    }
    actual = {
        (entity.module, entity.name): _SymbolBinding(
            entity=entity,
            rename_uses=True,
            resolution=Resolution.EXACT,
            provenance=Provenance.DIRECT_NAME,
        )
        for entity in entities
        if "." not in entity.qualname
    }
    exports = _build_exports(modules, actual)

    references: list[Reference] = []
    calls: list[CallEdge] = []
    hazards: list[SemanticHazard] = []
    for context in modules:
        local = {
            name: binding.entity
            for (module, name), binding in actual.items()
            if module == context.module and binding.entity is not None
        }
        symbol_bindings, module_bindings, import_refs, import_hazards = (
            _build_import_bindings(context, exports)
        )
        references.extend(import_refs)
        hazards.extend(import_hazards)
        visitor = _ReferenceVisitor(
            context=context,
            local_entities=local,
            symbol_bindings=symbol_bindings,
            module_bindings=module_bindings,
            entities_by_node=entities_by_node,
            exports=exports,
        )
        visitor.visit(context.tree)
        if "__getattr__" in local:
            possible = tuple(
                sorted(entity.id for entity in local.values() if entity.public)
            )
            definition = local["__getattr__"]
            reference = Reference(
                id=_relation_id(
                    "ref",
                    context.path,
                    definition.definition_span,
                    "__getattr__",
                    definition.id,
                    Provenance.REFLECTION,
                ),
                target_id=None,
                possible_target_ids=possible,
                file=context.path,
                span=definition.definition_span,
                kind="module_getattr",
                expected="__getattr__",
                owner_id=definition.id,
                rename_on_target=False,
                resolution=Resolution.DYNAMIC,
                provenance=Provenance.REFLECTION,
                usage="ModuleLookup",
            )
            visitor.references.append(reference)
            visitor.hazards.append(
                SemanticHazard(
                    kind="module_getattr",
                    symbol=context.module,
                    file=context.path,
                    line=definition.definition_span.start.line,
                    message="module __getattr__ can resolve names dynamically",
                    reference_id=reference.id,
                )
            )
        references.extend(visitor.references)
        calls.extend(visitor.calls)
        hazards.extend(visitor.hazards)

    references = _deduplicate_references(references)
    calls = _deduplicate_calls(calls)
    hazards = _deduplicate_hazards(hazards)
    for relation in resolution.relations:
        if relation.status != IdentityStatus.AMBIGUOUS:
            continue
        hazards.append(
            SemanticHazard(
                kind="ambiguous_identity",
                symbol=relation.old_locator or relation.old_id or "unknown",
                file="",
                line=0,
                message=relation.reason,
            )
        )
    program = ProgramIR(
        root=str(root_path),
        entities=entities,
        references=tuple(references),
        calls=tuple(calls),
        files=tuple(files),
        hazards=tuple(_deduplicate_hazards(hazards)),
        identity_relations=resolution.relations,
        analyzer_version=ANALYZER_VERSION,
    )
    return program.with_world_revision()


def _load_modules(root: Path) -> tuple[list[_ModuleSource], list[FileSnapshot]]:
    contexts: list[_ModuleSource] = []
    snapshots: list[FileSnapshot] = []
    failures: list[str] = []
    import_root_prefixes = _import_root_prefixes(root)
    for path in _python_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            raw = path.read_bytes()
            source = raw.decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            failures.append(f"{relative}: cannot read UTF-8 source: {exc}")
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(source, filename=relative, type_comments=True)
        except SyntaxError as exc:
            failures.append(
                f"{relative}:{exc.lineno or 1}:{exc.offset or 1}: {exc.msg}"
            )
            continue
        try:
            tokens = tuple(tokenize.generate_tokens(io.StringIO(source).readline))
        except tokenize.TokenError as exc:
            failures.append(f"{relative}: cannot tokenize source: {exc}")
            continue
        module_path = Path(relative)
        if (
            module_path.parts
            and module_path.parts[0] in import_root_prefixes
        ):
            module_path = Path(*module_path.parts[1:])
        module = _module_name(module_path)
        contexts.append(
            _ModuleSource(
                module=module,
                path=relative,
                absolute_path=path,
                source=source,
                lines=source.splitlines(),
                tree=tree,
                is_package=path.name == "__init__.py",
                tokens=tokens,
            )
        )
        snapshots.append(
            FileSnapshot(
                path=relative,
                digest=hashlib.sha256(raw).hexdigest(),
                module=module,
            )
        )
    if failures:
        raise AnalysisError(failures)
    contexts.sort(key=lambda item: item.path)
    snapshots.sort(key=lambda item: item.path)
    return contexts, snapshots


def _python_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*.py")):
        relative_parts = path.relative_to(root).parts
        if any(part in _IGNORED_DIRECTORIES for part in relative_parts):
            continue
        yield path


def _import_root_prefixes(root: Path) -> frozenset[str]:
    """Recognize conventional import roots without changing stored file paths."""
    source_root = root / "src"
    if not source_root.is_dir():
        return frozenset()
    has_top_level_package = any(
        child.is_dir() and (child / "__init__.py").is_file()
        for child in source_root.iterdir()
    )
    has_top_level_module = any(
        child.is_file() and child.suffix == ".py"
        for child in source_root.iterdir()
    )
    return (
        frozenset(("src",))
        if has_top_level_package or has_top_level_module
        else frozenset()
    )


def _module_name(path: Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or "__root__"


def _discover_entities(context: _ModuleSource) -> list[_EntityDraft]:
    drafts: list[_EntityDraft] = []
    for node in context.tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            drafts.append(_make_draft(context, node, node.name, node.name))
        elif isinstance(node, ast.ClassDef):
            drafts.append(_make_draft(context, node, node.name, node.name))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    drafts.append(
                        _make_draft(
                            context,
                            child,
                            f"{node.name}.{child.name}",
                            child.name,
                        )
                    )
    return drafts


def _make_draft(
    context: _ModuleSource,
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    qualname: str,
    name: str,
) -> _EntityDraft:
    if isinstance(node, ast.ClassDef):
        kind = "class"
        signature = f"class {name}"
        signature_span = None
        signature_source = ""
    else:
        kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
        signature = _function_signature(node)
        signature_span = _signature_span(context, node)
        signature_source = _text_for_span(context, signature_span)
    source_span = _entity_source_span(context, node)
    source_text = _text_for_span(context, source_span)
    features = _identity_features(node, name)
    revision_hash = _semantic_revision_hash(
        node=node,
        kind=kind,
        module=context.module,
        qualname=qualname,
    )
    return _EntityDraft(
        kind=kind,
        module=context.module,
        qualname=qualname,
        name=name,
        file=context.path,
        node=node,
        definition_span=_definition_name_span(context, node),
        source_span=source_span,
        source_hash=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        revision_hash=revision_hash,
        signature=signature,
        signature_span=signature_span,
        signature_source=signature_source,
        public=not name.startswith("_"),
        identity_features=features,
    )


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    arguments: list[str] = []
    positional = [*node.args.posonlyargs, *node.args.args]
    defaults_offset = len(positional) - len(node.args.defaults)
    for index, argument in enumerate(positional):
        item = argument.arg
        if argument.annotation is not None:
            item += f": {ast.unparse(argument.annotation)}"
        if index >= defaults_offset:
            item += f" = {ast.unparse(node.args.defaults[index - defaults_offset])}"
        arguments.append(item)
    if node.args.vararg:
        arguments.append(f"*{node.args.vararg.arg}")
    elif node.args.kwonlyargs:
        arguments.append("*")
    for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        item = argument.arg
        if argument.annotation is not None:
            item += f": {ast.unparse(argument.annotation)}"
        if default is not None:
            item += f" = {ast.unparse(default)}"
        arguments.append(item)
    if node.args.kwarg:
        arguments.append(f"**{node.args.kwarg.arg}")
    result = f"({', '.join(arguments)})"
    if node.returns is not None:
        result += f" -> {ast.unparse(node.returns)}"
    return result


def _semantic_revision_hash(
    *, node: ast.AST, kind: str, module: str, qualname: str
) -> str:
    payload = "\0".join(
        (
            "python",
            kind,
            module,
            qualname,
            ast.dump(node, annotate_fields=True, include_attributes=False),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _identity_features(node: ast.AST, name: str) -> dict[str, object]:
    canonical = copy.deepcopy(node)
    if isinstance(canonical, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        canonical.name = "$entity"
    canonical = _CanonicalNames(name).visit(canonical)
    ast.fix_missing_locations(canonical)
    semantic_shape = ast.dump(
        canonical, annotate_fields=True, include_attributes=False
    )
    calls = sorted(
        filter(None, (_call_name(item.func) for item in ast.walk(node) if isinstance(item, ast.Call)))
    )
    references = sorted(
        {
            item.id
            for item in ast.walk(node)
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
        }
    )
    node_kinds = Counter(type(item).__name__ for item in ast.walk(canonical))
    signature_shape = ""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        signature_shape = ast.dump(
            node.args, annotate_fields=True, include_attributes=False
        )
    return {
        "content_hash": hashlib.sha256(semantic_shape.encode("utf-8")).hexdigest(),
        "semantic_shape": semantic_shape,
        "signature_shape": signature_shape,
        "node_kinds": dict(sorted(node_kinds.items())),
        "calls": calls,
        "references": references,
    }


def _build_exports(
    contexts: Iterable[_ModuleSource],
    actual: Mapping[tuple[str, str], _SymbolBinding],
) -> dict[tuple[str, str], _SymbolBinding]:
    contexts = tuple(contexts)
    exports = dict(actual)
    for _ in range(max(1, len(contexts))):
        changed = False
        for context in contexts:
            for statement in context.tree.body:
                if not isinstance(statement, ast.ImportFrom):
                    continue
                source_module = _resolve_import_from(context, statement)
                for alias in statement.names:
                    if alias.name == "*":
                        continue
                    source = exports.get((source_module, alias.name))
                    if source is None:
                        continue
                    exported_name = alias.asname or alias.name
                    binding = _SymbolBinding(
                        entity=source.entity,
                        rename_uses=source.rename_uses and alias.asname is None,
                        resolution=Resolution.DERIVED,
                        provenance=Provenance.ALIAS if alias.asname else Provenance.IMPORT,
                        possible_target_ids=(source.entity.id,) if source.entity else (),
                        metadata={"reexport_module": context.module},
                    )
                    key = (context.module, exported_name)
                    if exports.get(key) != binding:
                        exports[key] = binding
                        changed = True
        if not changed:
            break
    return exports


def _build_import_bindings(
    context: _ModuleSource,
    exports: Mapping[tuple[str, str], _SymbolBinding],
    *,
    statements: Iterable[ast.stmt] | None = None,
    owner_id: str | None = None,
) -> tuple[
    dict[str, _SymbolBinding],
    dict[str, _ModuleBinding],
    list[Reference],
    list[SemanticHazard],
]:
    symbols: dict[str, _SymbolBinding] = {}
    known_modules = {module for module, _name in exports}
    modules: dict[str, _ModuleBinding] = {}
    references: list[Reference] = []
    hazards: list[SemanticHazard] = []
    collector = _ImportCollector()
    for statement in statements if statements is not None else context.tree.body:
        collector.visit(statement)
    for node in collector.imports:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", 1)[0]
                qualifier_span = _import_alias_module_span(context, alias)
                modules[local_name] = _ModuleBinding(
                    module=alias.name if alias.asname else local_name,
                    local_name=local_name,
                    qualifier=_text_for_span(context, qualifier_span),
                    qualifier_span=qualifier_span,
                )
            continue
        imported_module = _resolve_import_from(context, node)
        qualifier_span = _import_from_module_span(context, node)
        qualifier = _text_for_span(context, qualifier_span)
        for alias in node.names:
            if alias.name == "*":
                possible = tuple(
                    sorted(
                        {
                            binding.entity.id
                            for (module, _name), binding in exports.items()
                            if module == imported_module
                            and binding.entity is not None
                            and binding.entity.public
                        }
                    )
                )
                span = _imported_name_span(context, alias)
                reference_id = _relation_id(
                    "ref", context.path, span, "*", owner_id, Provenance.WILDCARD
                )
                references.append(
                    Reference(
                        id=reference_id,
                        target_id=None,
                        possible_target_ids=possible,
                        file=context.path,
                        span=span,
                        kind="wildcard_import",
                        expected="*",
                        owner_id=owner_id,
                        rename_on_target=False,
                        resolution=Resolution.UNKNOWN,
                        provenance=Provenance.WILDCARD,
                        usage="Import",
                        qualifier=qualifier,
                        qualifier_span=qualifier_span,
                        metadata={"imported_module": imported_module},
                    )
                )
                hazards.append(
                    SemanticHazard(
                        kind="wildcard_import",
                        symbol=imported_module,
                        file=context.path,
                        line=node.lineno,
                        message=(
                            f"wildcard import from {imported_module!r} prevents complete reference migration"
                        ),
                        reference_id=reference_id,
                    )
                )
                continue
            exported = exports.get((imported_module, alias.name))
            local_name = alias.asname or alias.name
            submodule = ".".join(
                part for part in (imported_module, alias.name) if part
            )
            if submodule in known_modules:
                span = _imported_name_span(context, alias)
                modules[local_name] = _ModuleBinding(
                    module=submodule,
                    local_name=local_name,
                    qualifier=local_name,
                    qualifier_span=span,
                )
                continue
            if exported is None:
                binding = _SymbolBinding(
                    entity=None,
                    rename_uses=False,
                    resolution=Resolution.UNKNOWN,
                    provenance=Provenance.EXTERNAL_IMPORT,
                    qualifier=qualifier,
                    qualifier_span=qualifier_span,
                    metadata={
                        "imported_module": imported_module,
                        "source_name": alias.name,
                        "local_name": local_name,
                    },
                )
            else:
                rename_uses = alias.asname is None and exported.rename_uses
                binding = _SymbolBinding(
                    entity=exported.entity,
                    rename_uses=rename_uses,
                    resolution=Resolution.EXACT,
                    provenance=Provenance.ALIAS if alias.asname else Provenance.IMPORT,
                    possible_target_ids=(exported.entity.id,) if exported.entity else (),
                    qualifier=qualifier,
                    qualifier_span=qualifier_span,
                    metadata={
                        "imported_module": imported_module,
                        "source_name": alias.name,
                        "local_name": local_name,
                        "reexport": context.is_package,
                    },
                )
            symbols[local_name] = binding
            span = _imported_name_span(context, alias)
            target_id = binding.entity.id if binding.entity else None
            references.append(
                Reference(
                    id=_relation_id(
                        "ref",
                        context.path,
                        span,
                        alias.name,
                        owner_id,
                        binding.provenance,
                    ),
                    target_id=target_id,
                    possible_target_ids=binding.possible_target_ids,
                    file=context.path,
                    span=span,
                    kind="import",
                    expected=alias.name,
                    owner_id=owner_id,
                    rename_on_target=(
                        exported.rename_uses if exported is not None else False
                    ),
                    resolution=binding.resolution,
                    provenance=binding.provenance,
                    usage="Import",
                    qualifier=qualifier,
                    qualifier_span=qualifier_span,
                    metadata=dict(binding.metadata or {}),
                )
            )
    return symbols, modules, references, hazards


def _resolve_import_from(context: _ModuleSource, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = context.module.split(".")
    if not context.is_package:
        package = package[:-1]
    ascend = node.level - 1
    if ascend:
        package = package[:-ascend] if ascend <= len(package) else []
    if node.module:
        package.extend(node.module.split("."))
    return ".".join(package)


def _dynamic_symbol_name(node: ast.Call) -> str | None:
    if (
        isinstance(node.func, ast.Name)
        and node.func.id in {"getattr", "hasattr", "setattr", "delattr"}
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        return node.args[1].value
    return None


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts: list[str] = []
        current: ast.AST = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))
    return None


def _call_arguments(node: ast.Call) -> Iterator[CallArgument]:
    for argument in node.args:
        yield CallArgument("starred" if isinstance(argument, ast.Starred) else "positional")
    for keyword in node.keywords:
        yield CallArgument("double_star" if keyword.arg is None else "keyword", keyword.arg)


def _ast_span(context: _ModuleSource, node: ast.AST) -> Span:
    return Span(
        start=_ast_position(context, node.lineno, node.col_offset),
        end=_ast_position(context, node.end_lineno, node.end_col_offset),
    )


def _attribute_name_span(context: _ModuleSource, node: ast.Attribute) -> Span:
    end = _ast_position(context, node.end_lineno, node.end_col_offset)
    return Span(start=Position(end.line, end.column - len(node.attr)), end=end)


def _ast_position(context: _ModuleSource, line: int, byte_column: int) -> Position:
    text = context.lines[line - 1] if line <= len(context.lines) else ""
    prefix = text.encode("utf-8")[:byte_column].decode("utf-8")
    return Position(line=line, column=len(prefix))


def _definition_name_span(
    context: _ModuleSource,
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> Span:
    for token in context.tokens:
        if token.type != tokenize.NAME or token.string != node.name:
            continue
        if token.start[0] != node.lineno or token.start[1] <= node.col_offset:
            continue
        return Span(
            Position(token.start[0], token.start[1]),
            Position(token.end[0], token.end[1]),
        )
    raise AnalysisError(
        [f"{context.path}:{node.lineno}: cannot locate definition name {node.name!r}"]
    )


def _entity_source_span(
    context: _ModuleSource,
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> Span:
    decorators = getattr(node, "decorator_list", ())
    if decorators:
        first = decorators[0]
        start = _ast_position(
            context,
            first.lineno,
            max(0, first.col_offset - 1),
        )
    else:
        start = _ast_position(context, node.lineno, node.col_offset)
    return Span(
        start=start,
        end=_ast_position(context, node.end_lineno, node.end_col_offset),
    )


def _signature_span(
    context: _ModuleSource, node: ast.FunctionDef | ast.AsyncFunctionDef
) -> Span:
    definition = _definition_name_span(context, node)
    started = False
    depth = 0
    start: tuple[int, int] | None = None
    for token in context.tokens:
        if token.start < (definition.end.line, definition.end.column):
            continue
        if token.type == tokenize.OP and token.string == "(":
            if not started:
                started = True
                start = token.start
            depth += 1
        elif started and token.type == tokenize.OP and token.string == ")":
            depth -= 1
            if depth == 0 and start is not None:
                return Span(Position(*start), Position(*token.end))
    raise AnalysisError([f"{context.path}:{node.lineno}: cannot locate signature"])


def _imported_name_span(context: _ModuleSource, alias: ast.alias) -> Span:
    for token in context.tokens:
        if token.string != alias.name and not (
            alias.name == "*" and token.type == tokenize.OP and token.string == "*"
        ):
            continue
        if token.start[0] < alias.lineno or token.end[0] > alias.end_lineno:
            continue
        if token.start[0] == alias.lineno and token.start[1] < alias.col_offset:
            continue
        return Span(Position(*token.start), Position(*token.end))
    raise AnalysisError(
        [f"{context.path}:{alias.lineno}: cannot locate imported name {alias.name!r}"]
    )


def _import_from_module_span(context: _ModuleSource, node: ast.ImportFrom) -> Span:
    relevant = [
        token
        for token in context.tokens
        if (node.lineno, node.col_offset) <= token.start
        and token.end <= (node.end_lineno, node.end_col_offset)
    ]
    from_index = next(
        index for index, token in enumerate(relevant) if token.string == "from"
    )
    import_index = next(
        index
        for index, token in enumerate(relevant[from_index + 1 :], from_index + 1)
        if token.string == "import"
    )
    qualifier_tokens = [
        token
        for token in relevant[from_index + 1 : import_index]
        if token.type not in {tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT}
    ]
    if not qualifier_tokens:
        raise AnalysisError([f"{context.path}:{node.lineno}: missing import module"])
    return Span(
        Position(*qualifier_tokens[0].start),
        Position(*qualifier_tokens[-1].end),
    )


def _import_alias_module_span(context: _ModuleSource, alias: ast.alias) -> Span:
    tokens = [
        token
        for token in context.tokens
        if alias.lineno <= token.start[0] <= alias.end_lineno
        and (token.start[0], token.start[1]) >= (alias.lineno, alias.col_offset)
        and token.string != "as"
    ]
    consumed: list[tokenize.TokenInfo] = []
    text = ""
    for token in tokens:
        if token.type not in {tokenize.NAME, tokenize.OP}:
            continue
        if token.string not in {"."} and token.type != tokenize.NAME:
            continue
        consumed.append(token)
        text += token.string
        if text == alias.name:
            break
    if text != alias.name or not consumed:
        raise AnalysisError([f"{context.path}:{alias.lineno}: cannot locate import"])
    return Span(Position(*consumed[0].start), Position(*consumed[-1].end))


def _text_for_span(context: _ModuleSource, span: Span) -> str:
    offsets = _line_offsets(context.source)
    start = offsets[span.start.line - 1] + span.start.column
    end = offsets[span.end.line - 1] + span.end.column
    return context.source[start:end]


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for index, character in enumerate(source):
        if character == "\n":
            offsets.append(index + 1)
    return offsets


def _relation_id(
    prefix: str,
    file: str,
    span: Span,
    symbol: str,
    owner_id: str | None,
    provenance: str,
) -> str:
    payload = "\0".join(
        (
            prefix,
            file,
            str(span.start.line),
            str(span.start.column),
            str(span.end.line),
            str(span.end.column),
            symbol,
            owner_id or "",
            provenance,
        )
    )
    return f"{prefix}_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _deduplicate_references(items: list[Reference]) -> list[Reference]:
    unique = {item.id: item for item in items}
    return sorted(
        unique.values(),
        key=lambda item: (
            item.file,
            item.span.start.line,
            item.span.start.column,
            item.id,
        ),
    )


def _deduplicate_calls(items: list[CallEdge]) -> list[CallEdge]:
    unique = {item.id: item for item in items}
    return sorted(
        unique.values(),
        key=lambda item: (item.file, item.line, item.column, item.id),
    )


def _deduplicate_hazards(items: list[SemanticHazard]) -> list[SemanticHazard]:
    unique = {
        (item.kind, item.symbol, item.file, item.line, item.reference_id): item
        for item in items
    }
    return sorted(
        unique.values(),
        key=lambda item: (
            item.file,
            item.line,
            item.kind,
            item.symbol,
            item.reference_id or "",
        ),
    )
