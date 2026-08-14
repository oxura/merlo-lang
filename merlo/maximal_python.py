"""Maximal honest Python semantic profile for Stage 0.4E differential work."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping

from .model import EditCapability
from .python_binder import PythonBindingReport, bind_python_sources
from .world import SoftwareWorld


MAXIMAL_PYTHON_SCHEMA_VERSION = 1
_BLOCK = "BLOCK"
_BOUNDARY = "BOUNDARY"
_INFO = "INFO"

_DEFAULT_FORBIDDEN_IMPORTS = (
    "asyncio",
    "ctypes",
    "ftplib",
    "http",
    "multiprocessing",
    "os",
    "pathlib",
    "requests",
    "shutil",
    "smtplib",
    "socket",
    "sqlite3",
    "subprocess",
    "urllib",
)
_ALLOWED_DECORATORS = frozenset(
    ("effects", "requires", "staticmethod", "classmethod", "target")
)
_DYNAMIC_CALLS = frozenset(
    (
        "__import__",
        "compile",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "globals",
        "importlib.import_module",
        "locals",
        "setattr",
        "vars",
    )
)
_AUDIT_EVENT_PREFIXES = (
    "ctypes.dlopen",
    "os.system",
    "socket.connect",
    "subprocess.Popen",
)


def effects(*names: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Runtime-neutral declaration consumed by the strict profile."""

    normalized = _normalized_names(names, "effect")

    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        setattr(function, "__meldra_effects__", normalized)
        return function

    return decorate


def requires(*names: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Runtime-neutral capability declaration consumed by the strict profile."""

    normalized = _normalized_names(names, "capability")

    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        setattr(function, "__meldra_capabilities__", normalized)
        return function

    return decorate


def _normalized_names(values: Iterable[str], label: str) -> tuple[str, ...]:
    result = tuple(sorted(set(values)))
    if not result or any(not isinstance(item, str) or not item for item in result):
        raise ValueError(f"{label} declarations require non-empty strings")
    return result


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(prefix: str, value: Any) -> str:
    return f"{prefix}_{hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()}"


def _module_name(path: str) -> str:
    normalized = PurePosixPath(path.replace("\\", "/"))
    parts = list(normalized.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _dotted_name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return None


def _annotation(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return ast.dump(node, include_attributes=False)


def _literal_type(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant):
        if node.value is None:
            return "None"
        if isinstance(node.value, bool):
            return "bool"
        if isinstance(node.value, int):
            return "int"
        if isinstance(node.value, str):
            return "str"
    return None


@dataclass(frozen=True)
class MaximalPythonPackageManifest:
    name: str
    module_prefix: str
    exports: tuple[str, ...]
    effect_bindings: tuple[tuple[str, str], ...] = ()
    function_effects: tuple[tuple[str, tuple[str, ...]], ...] = ()
    function_capabilities: tuple[tuple[str, tuple[str, ...]], ...] = ()
    forbidden_imports: tuple[str, ...] = _DEFAULT_FORBIDDEN_IMPORTS
    allowed_ambient_imports: tuple[str, ...] = ()
    allowed_dynamic_boundaries: tuple[str, ...] = ()
    allowed_network_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not self.module_prefix:
            raise ValueError("package name and module_prefix are required")
        object.__setattr__(self, "exports", tuple(sorted(set(self.exports))))
        object.__setattr__(
            self,
            "effect_bindings",
            tuple(sorted((str(key), str(value)) for key, value in self.effect_bindings)),
        )
        object.__setattr__(
            self,
            "function_effects",
            tuple(
                sorted(
                    (str(key), tuple(sorted(set(value))))
                    for key, value in self.function_effects
                )
            ),
        )
        object.__setattr__(
            self,
            "function_capabilities",
            tuple(
                sorted(
                    (str(key), tuple(sorted(set(value))))
                    for key, value in self.function_capabilities
                )
            ),
        )
        object.__setattr__(
            self, "forbidden_imports", tuple(sorted(set(self.forbidden_imports)))
        )
        object.__setattr__(
            self,
            "allowed_ambient_imports",
            tuple(sorted(set(self.allowed_ambient_imports))),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MaximalPythonPackageManifest":
        def pairs(name: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
            raw = value.get(name, {})
            if not isinstance(raw, Mapping):
                raise ValueError(f"{name} must be an object")
            return tuple(
                (str(key), tuple(str(item) for item in items))
                for key, items in raw.items()
            )

        effect_bindings = value.get("effect_bindings", {})
        if not isinstance(effect_bindings, Mapping):
            raise ValueError("effect_bindings must be an object")
        return cls(
            name=str(value["name"]),
            module_prefix=str(value["module_prefix"]),
            exports=tuple(str(item) for item in value.get("exports", ())),
            effect_bindings=tuple(
                (str(key), str(item)) for key, item in effect_bindings.items()
            ),
            function_effects=pairs("function_effects"),
            function_capabilities=pairs("function_capabilities"),
            forbidden_imports=tuple(
                str(item)
                for item in value.get("forbidden_imports", _DEFAULT_FORBIDDEN_IMPORTS)
            ),
            allowed_ambient_imports=tuple(
                str(item) for item in value.get("allowed_ambient_imports", ())
            ),
            allowed_dynamic_boundaries=tuple(
                str(item) for item in value.get("allowed_dynamic_boundaries", ())
            ),
            allowed_network_hosts=tuple(
                str(item) for item in value.get("allowed_network_hosts", ())
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "module_prefix": self.module_prefix,
            "exports": list(self.exports),
            "effect_bindings": dict(self.effect_bindings),
            "function_effects": {
                key: list(value) for key, value in self.function_effects
            },
            "function_capabilities": {
                key: list(value) for key, value in self.function_capabilities
            },
            "forbidden_imports": list(self.forbidden_imports),
            "allowed_ambient_imports": list(self.allowed_ambient_imports),
            "allowed_dynamic_boundaries": list(self.allowed_dynamic_boundaries),
            "allowed_network_hosts": list(self.allowed_network_hosts),
        }


@dataclass(frozen=True)
class MaximalPythonManifest:
    packages: tuple[MaximalPythonPackageManifest, ...]
    schema_version: int = MAXIMAL_PYTHON_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.packages:
            raise ValueError("at least one strict Python package manifest is required")
        prefixes = [item.module_prefix for item in self.packages]
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("package module_prefix values must be unique")
        object.__setattr__(self, "packages", tuple(sorted(self.packages, key=lambda x: x.name)))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MaximalPythonManifest":
        if value.get("schema_version", MAXIMAL_PYTHON_SCHEMA_VERSION) != MAXIMAL_PYTHON_SCHEMA_VERSION:
            raise ValueError("unsupported maximal Python manifest schema")
        return cls(
            tuple(
                MaximalPythonPackageManifest.from_dict(item)
                for item in value.get("packages", ())
            )
        )

    def package_for_module(self, module: str) -> MaximalPythonPackageManifest | None:
        candidates = tuple(
            item
            for item in self.packages
            if module == item.module_prefix
            or module.startswith(item.module_prefix + ".")
        )
        return max(candidates, key=lambda item: len(item.module_prefix), default=None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "packages": [item.to_dict() for item in self.packages],
        }


@dataclass(frozen=True)
class MaximalPythonDiagnostic:
    code: str
    message: str
    path: str
    line: int
    column: int
    severity: str
    category: str

    @property
    def blocking(self) -> bool:
        return self.severity == _BLOCK

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "severity": self.severity,
            "category": self.category,
        }


@dataclass(frozen=True)
class MaximalPythonSymbol:
    symbol_id: str
    revision_id: str
    locator: str
    package: str
    kind: str
    signature: str
    effects: tuple[str, ...]
    capabilities: tuple[str, ...]
    exported: bool
    path: str
    line: int

    def contract(self) -> dict[str, Any]:
        return {
            "locator": self.locator,
            "kind": self.kind,
            "signature": self.signature,
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "locator": self.locator,
            "package": self.package,
            "kind": self.kind,
            "signature": self.signature,
            "effects": list(self.effects),
            "capabilities": list(self.capabilities),
            "exported": self.exported,
            "path": self.path,
            "line": self.line,
        }


@dataclass(frozen=True)
class MaximalPythonReference:
    reference_id: str
    path: str
    line: int
    spelling: str
    binder_status: str
    profile_status: str
    target_symbol_id: str | None
    target_locator: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "path": self.path,
            "line": self.line,
            "spelling": self.spelling,
            "binder_status": self.binder_status,
            "profile_status": self.profile_status,
            "target_symbol_id": self.target_symbol_id,
            "target_locator": self.target_locator,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PythonInterfaceSnapshot:
    package: str
    interface_revision_id: str
    implementation_revision_id: str
    exports: tuple[str, ...]
    contracts: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "interface_revision_id": self.interface_revision_id,
            "implementation_revision_id": self.implementation_revision_id,
            "exports": list(self.exports),
            "contracts": dict(self.contracts),
        }


@dataclass(frozen=True)
class MaximalPythonReport:
    manifest: MaximalPythonManifest
    symbols: tuple[MaximalPythonSymbol, ...]
    references: tuple[MaximalPythonReference, ...]
    diagnostics: tuple[MaximalPythonDiagnostic, ...]
    packages: tuple[PythonInterfaceSnapshot, ...]
    binder_counts: tuple[tuple[str, int], ...]
    lsp_status: str = "UNMEASURED_NO_LANGUAGE_SERVER"
    security_boundary: bool = False
    schema_version: int = MAXIMAL_PYTHON_SCHEMA_VERSION

    @property
    def ok(self) -> bool:
        return not any(item.blocking for item in self.diagnostics)

    @property
    def blocking_diagnostics(self) -> tuple[MaximalPythonDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.blocking)

    def symbol(self, id_or_locator: str) -> MaximalPythonSymbol:
        matches = tuple(
            item
            for item in self.symbols
            if item.symbol_id == id_or_locator or item.locator == id_or_locator
        )
        if len(matches) != 1:
            raise KeyError(id_or_locator)
        return matches[0]

    def package(self, name: str) -> PythonInterfaceSnapshot:
        matches = tuple(item for item in self.packages if item.package == name)
        if len(matches) != 1:
            raise KeyError(name)
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        counts = {
            "blocking": sum(item.blocking for item in self.diagnostics),
            "boundary": sum(item.severity == _BOUNDARY for item in self.diagnostics),
            "info": sum(item.severity == _INFO for item in self.diagnostics),
        }
        return {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "security_boundary": self.security_boundary,
            "guarantee": "strict profile enforcement, not a CPython language guarantee",
            "lsp_status": self.lsp_status,
            "manifest": self.manifest.to_dict(),
            "symbols": [item.to_dict() for item in self.symbols],
            "references": [item.to_dict() for item in self.references],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "diagnostic_counts": counts,
            "packages": [item.to_dict() for item in self.packages],
            "binder_counts": dict(self.binder_counts),
        }

    def to_json(self) -> str:
        return _canonical(self.to_dict())


@dataclass(frozen=True)
class StrictExecutionResult:
    status: str
    returncode: int | None
    stdout: str
    stderr: str
    blocked_reasons: tuple[str, ...]
    audit_events: tuple[str, ...]
    runtime_escape: bool | None
    infrastructure_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "blocked_reasons": list(self.blocked_reasons),
            "audit_events": list(self.audit_events),
            "runtime_escape": self.runtime_escape,
            "infrastructure_error": self.infrastructure_error,
            "security_boundary": False,
        }


@dataclass(frozen=True)
class MaximalPythonChange:
    operation: str
    target: str
    value: str
    argument_values: tuple[tuple[str, str], ...] = ()

    @classmethod
    def rename(cls, target: str, new_name: str) -> "MaximalPythonChange":
        return cls("rename", target, new_name)

    @classmethod
    def move(cls, target: str, target_module: str) -> "MaximalPythonChange":
        return cls("move", target, target_module)

    @classmethod
    def change_signature(
        cls,
        target: str,
        signature: str,
        argument_values: Mapping[str, str] | None = None,
    ) -> "MaximalPythonChange":
        return cls(
            "change_signature",
            target,
            signature,
            tuple(sorted((argument_values or {}).items())),
        )


@dataclass(frozen=True)
class MaximalPythonChangeResult:
    applied: bool
    blocked_reasons: tuple[str, ...]
    changed_files: tuple[str, ...]
    sources: tuple[tuple[str, str], ...]
    manifest: MaximalPythonManifest
    target_symbol_id_before: str | None
    target_symbol_id_after: str | None
    interface_changed_packages: tuple[str, ...]
    source_preserved_outside_edits: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "blocked_reasons": list(self.blocked_reasons),
            "changed_files": list(self.changed_files),
            "sources": dict(self.sources),
            "manifest": self.manifest.to_dict(),
            "target_symbol_id_before": self.target_symbol_id_before,
            "target_symbol_id_after": self.target_symbol_id_after,
            "identity_continuity": (
                self.target_symbol_id_before is not None
                and self.target_symbol_id_before == self.target_symbol_id_after
            ),
            "interface_changed_packages": list(self.interface_changed_packages),
            "source_preserved_outside_edits": self.source_preserved_outside_edits,
        }


class _ProfileAnalyzer:
    def __init__(
        self,
        sources: Mapping[str, str],
        manifest: MaximalPythonManifest,
        identity_map: Mapping[str, str] | None,
    ) -> None:
        self.sources = dict(sorted(sources.items()))
        self.manifest = manifest
        self.identity_map = dict(identity_map or {})
        self.trees: dict[str, ast.Module] = {}
        self.diagnostics: list[MaximalPythonDiagnostic] = []
        self.nodes: dict[str, tuple[str, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]] = {}
        self.node_effects: dict[str, tuple[str, ...]] = {}
        self.node_capabilities: dict[str, tuple[str, ...]] = {}

    def run(self) -> MaximalPythonReport:
        self._parse()
        self._collect_declarations()
        self._check_manifests()
        self._check_sources()
        binder = bind_python_sources(self.sources)
        symbols = self._symbols(binder)
        references = self._references(binder, symbols)
        packages = self._package_snapshots(symbols)
        diagnostics = tuple(
            sorted(
                self.diagnostics,
                key=lambda item: (
                    item.path,
                    item.line,
                    item.column,
                    item.severity,
                    item.code,
                ),
            )
        )
        return MaximalPythonReport(
            self.manifest,
            symbols,
            references,
            diagnostics,
            packages,
            tuple(
                sorted(
                    {
                        "exact": binder.exact_count,
                        "unknown": binder.unknown_count,
                        "foreign": binder.foreign_count,
                        "total": len(binder.references),
                    }.items()
                )
            ),
        )

    def _parse(self) -> None:
        for path, source in self.sources.items():
            try:
                self.trees[path] = ast.parse(source, filename=path, type_comments=True)
            except SyntaxError as exc:
                self._error(
                    "SyntaxError",
                    exc.msg,
                    path,
                    exc.lineno or 1,
                    (exc.offset or 1) - 1,
                    _BLOCK,
                    "syntax",
                )
        if len(self.trees) != len(self.sources):
            raise ValueError("maximal Python profile requires syntactically valid sources")

    def _collect_declarations(self) -> None:
        for path, tree in self.trees.items():
            module = _module_name(path)
            package = self.manifest.package_for_module(module)
            if package is None:
                self._error(
                    "MissingPackageManifest",
                    f"module {module} has no package manifest",
                    path,
                    1,
                    0,
                    _BLOCK,
                    "interface",
                )
                continue
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    locator = f"{module}.{node.name}"
                    self.nodes[locator] = (path, node)
                    effects_declared, capabilities = self._decorator_contract(node, path)
                    configured_effects = dict(package.function_effects).get(locator, ())
                    configured_capabilities = dict(package.function_capabilities).get(locator, ())
                    self.node_effects[locator] = tuple(
                        sorted(set(effects_declared) | set(configured_effects))
                    )
                    self.node_capabilities[locator] = tuple(
                        sorted(set(capabilities) | set(configured_capabilities))
                    )
                    if isinstance(node, ast.ClassDef):
                        for member in node.body:
                            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                member_locator = f"{locator}.{member.name}"
                                self.nodes[member_locator] = (path, member)
                                member_effects, member_caps = self._decorator_contract(member, path)
                                self.node_effects[member_locator] = member_effects
                                self.node_capabilities[member_locator] = member_caps

    def _decorator_contract(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        path: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        declared_effects: set[str] = set()
        declared_capabilities: set[str] = set()
        for decorator in node.decorator_list:
            value = decorator.func if isinstance(decorator, ast.Call) else decorator
            name = _dotted_name(value) or "<dynamic>"
            short = name.rsplit(".", 1)[-1]
            if short in {"effects", "requires"} and isinstance(decorator, ast.Call):
                values = tuple(
                    item.value
                    for item in decorator.args
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
                if len(values) != len(decorator.args):
                    self._error(
                        "NonLiteralSemanticDecorator",
                        f"@{short} arguments must be literal strings",
                        path,
                        decorator.lineno,
                        decorator.col_offset,
                        _BLOCK,
                        "effects" if short == "effects" else "capabilities",
                    )
                target = declared_effects if short == "effects" else declared_capabilities
                target.update(values)
            elif short not in _ALLOWED_DECORATORS:
                severity = (
                    _BOUNDARY
                    if short in {"property", "singledispatch", "register"}
                    else _BLOCK
                )
                self._error(
                    "DynamicDecorator",
                    f"decorator {name} may replace or wrap {node.name}",
                    path,
                    decorator.lineno,
                    decorator.col_offset,
                    severity,
                    "runtime_binding",
                )
        return tuple(sorted(declared_effects)), tuple(sorted(declared_capabilities))

    def _check_manifests(self) -> None:
        for package in self.manifest.packages:
            declarations = {
                locator for locator in self.nodes if locator.count(".") >= 1
            }
            for export in package.exports:
                if export not in declarations:
                    self._error(
                        "UnknownManifestExport",
                        f"export {export} has no declaration",
                        "<manifest>",
                        1,
                        0,
                        _BLOCK,
                        "interface",
                    )
        for locator, (path, node) in self.nodes.items():
            if "." in locator and locator.rsplit(".", 1)[-1].startswith("_"):
                continue
            module = locator.rsplit(".", 1)[0]
            package = self.manifest.package_for_module(module)
            if package is None or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "." in module and module in self.nodes:
                continue
            if locator not in package.exports and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self._error(
                    "UnmanifestedPublicDeclaration",
                    f"public declaration {locator} is absent from explicit exports",
                    path,
                    node.lineno,
                    node.col_offset,
                    _BLOCK,
                    "interface",
                )

    def _check_sources(self) -> None:
        for path, tree in self.trees.items():
            module = _module_name(path)
            package = self.manifest.package_for_module(module)
            if package is None:
                continue
            self._check_imports(path, tree, package)
            self._check_runtime_binding(path, tree, package)
            for locator, (owner_path, node) in self.nodes.items():
                if owner_path != path or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                self._check_function_contract(locator, path, node, package)

    def _check_imports(
        self,
        path: str,
        tree: ast.Module,
        package: MaximalPythonPackageManifest,
    ) -> None:
        forbidden = set(package.forbidden_imports) - set(package.allowed_ambient_imports)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                if any(item.name == "*" for item in node.names):
                    self._error(
                        "WildcardImport",
                        "wildcard imports are forbidden by the strict profile",
                        path,
                        node.lineno,
                        node.col_offset,
                        _BLOCK,
                        "binding",
                    )
                if node.module:
                    names = [node.module]
            for name in names:
                root = name.split(".", 1)[0]
                if root in forbidden:
                    self._error(
                        "ForbiddenAmbientImport",
                        f"ambient import {name} requires an explicit capability adapter",
                        path,
                        node.lineno,
                        node.col_offset,
                        _BLOCK,
                        "capabilities",
                    )

    def _check_runtime_binding(
        self,
        path: str,
        tree: ast.Module,
        package: MaximalPythonPackageManifest,
    ) -> None:
        declared = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        assigned: set[str] = set()
        class_methods: dict[str, set[str]] = {}
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                methods = {
                    item.name
                    for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                class_methods[node.name] = methods
                if node.keywords:
                    self._error(
                        "MetaclassBoundary",
                        f"class {node.name} uses a metaclass or dynamic class keyword",
                        path,
                        node.lineno,
                        node.col_offset,
                        _BLOCK,
                        "runtime_binding",
                    )
                for method in methods & {"__getattr__", "__getattribute__"}:
                    self._error(
                        "DynamicAttributeBoundary",
                        f"{node.name}.{method} changes runtime member resolution",
                        path,
                        node.lineno,
                        node.col_offset,
                        _BLOCK,
                        "runtime_binding",
                    )
                for base in node.bases:
                    base_name = _dotted_name(base)
                    if base_name and base_name in class_methods:
                        overridden = methods & class_methods[base_name]
                        if overridden:
                            self._error(
                                "OverrideDispatchBoundary",
                                f"{node.name} overrides {', '.join(sorted(overridden))}",
                                path,
                                node.lineno,
                                node.col_offset,
                                _BOUNDARY,
                                "runtime_binding",
                            )
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        if target.id in declared or target.id in assigned:
                            self._error(
                                "RuntimeBindingMutation",
                                f"module binding {target.id} is reassigned",
                                path,
                                target.lineno,
                                target.col_offset,
                                _BLOCK,
                                "runtime_binding",
                            )
                        assigned.add(target.id)
                    elif isinstance(target, ast.Attribute) or (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Call)
                        and (_dotted_name(target.value.func) in {"globals", "locals", "vars"})
                    ):
                        self._error(
                            "RuntimeBindingMutation",
                            "attribute or namespace mutation changes runtime targets",
                            path,
                            target.lineno,
                            target.col_offset,
                            _BLOCK,
                            "runtime_binding",
                        )
            if isinstance(node, ast.Delete):
                self._error(
                    "RuntimeBindingDeletion",
                    "deleting a module or class binding is forbidden",
                    path,
                    node.lineno,
                    node.col_offset,
                    _BLOCK,
                    "runtime_binding",
                )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _dotted_name(node.func)
                if name in _DYNAMIC_CALLS or (name and name.rsplit(".", 1)[-1] in _DYNAMIC_CALLS):
                    self._error(
                        "DynamicRuntimeEscape",
                        f"{name} can bypass static binding or import policy",
                        path,
                        node.lineno,
                        node.col_offset,
                        _BLOCK,
                        "runtime_binding",
                    )
                if name and (
                    name.endswith(".register")
                    or name in {"functools.partial", "partial", "singledispatch"}
                ):
                    self._error(
                        "ExplicitDynamicBoundary",
                        f"{name} uses runtime dispatch",
                        path,
                        node.lineno,
                        node.col_offset,
                        _BOUNDARY,
                        "runtime_binding",
                    )

    def _check_function_contract(
        self,
        locator: str,
        path: str,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        package: MaximalPythonPackageManifest,
    ) -> None:
        parameters = tuple((*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs))
        for parameter in parameters:
            if parameter.arg in {"self", "cls"}:
                continue
            if parameter.annotation is None:
                self._error(
                    "MissingParameterType",
                    f"{locator}.{parameter.arg} has no explicit type",
                    path,
                    parameter.lineno,
                    parameter.col_offset,
                    _BLOCK,
                    "types",
                )
        if node.returns is None:
            self._error(
                "MissingReturnType",
                f"{locator} has no explicit return type",
                path,
                node.lineno,
                node.col_offset,
                _BLOCK,
                "types",
            )
        expected_return = _annotation(node.returns)
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and child.value is not None:
                actual = _literal_type(child.value)
                if actual and expected_return and not _types_compatible(actual, expected_return):
                    self._error(
                        "ReturnTypeMismatch",
                        f"{locator} returns {actual}, declared {expected_return}",
                        path,
                        child.lineno,
                        child.col_offset,
                        _BLOCK,
                        "types",
                    )
        declared_effects = set(self.node_effects.get(locator, ()))
        declared_capabilities = set(self.node_capabilities.get(locator, ()))
        if declared_effects - declared_capabilities:
            self._error(
                "EffectWithoutCapability",
                f"{locator} effects lack capabilities: {sorted(declared_effects - declared_capabilities)}",
                path,
                node.lineno,
                node.col_offset,
                _BLOCK,
                "capabilities",
            )
        effect_bindings = dict(package.effect_bindings)
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            name = _dotted_name(child.func)
            effect = effect_bindings.get(name or "")
            if effect is None:
                continue
            if effect not in declared_effects:
                self._error(
                    "EffectNotDeclared",
                    f"{locator} calls {name} without effect {effect}",
                    path,
                    child.lineno,
                    child.col_offset,
                    _BLOCK,
                    "effects",
                )
            if effect not in declared_capabilities:
                self._error(
                    "CapabilityNotDeclared",
                    f"{locator} calls {name} without capability {effect}",
                    path,
                    child.lineno,
                    child.col_offset,
                    _BLOCK,
                    "capabilities",
                )

    def _symbols(self, binder: PythonBindingReport) -> tuple[MaximalPythonSymbol, ...]:
        result = []
        for item in binder.symbols:
            package = self.manifest.package_for_module(item.module)
            if package is None:
                continue
            node_info = self.nodes.get(item.locator)
            node = node_info[1] if node_info else None
            signature = _signature(node) if node is not None else item.kind
            semantic = ast.dump(node, include_attributes=False) if node is not None else item.locator
            anchor = f"{package.name}:{item.module}:{item.qualname}:{item.kind}"
            symbol_id = self.identity_map.get(anchor, _digest("pysym", anchor))
            effects_value = self.node_effects.get(item.locator, ())
            capabilities = self.node_capabilities.get(item.locator, ())
            revision_id = _digest(
                "pyrev",
                {
                    "semantic": semantic,
                    "signature": signature,
                    "effects": effects_value,
                    "capabilities": capabilities,
                },
            )
            result.append(
                MaximalPythonSymbol(
                    symbol_id,
                    revision_id,
                    item.locator,
                    package.name,
                    item.kind,
                    signature,
                    effects_value,
                    capabilities,
                    item.locator in package.exports,
                    item.path,
                    item.line,
                )
            )
        return tuple(sorted(result, key=lambda item: item.symbol_id))

    def _references(
        self,
        binder: PythonBindingReport,
        symbols: tuple[MaximalPythonSymbol, ...],
    ) -> tuple[MaximalPythonReference, ...]:
        profile_by_locator = {item.locator: item for item in symbols}
        blocked_paths = {
            item.path for item in self.diagnostics if item.severity == _BLOCK
        }
        boundary_paths = {
            item.path for item in self.diagnostics if item.severity == _BOUNDARY
        }
        result = []
        for reference in binder.references:
            target = (
                binder.symbol(reference.target_symbol_id)
                if reference.target_symbol_id is not None
                else None
            )
            profile_target = profile_by_locator.get(target.locator) if target else None
            if reference.path in blocked_paths:
                status = "RejectedByProfile"
                reason = "blocking strict-profile diagnostic in source unit"
            elif reference.path in boundary_paths:
                status = "DynamicBoundary"
                reason = "runtime dispatch is explicit and cannot be Exact"
            else:
                status = reference.status
                reason = "strong binder classification retained"
            result.append(
                MaximalPythonReference(
                    reference.id,
                    reference.path,
                    reference.line,
                    reference.spelling,
                    reference.status,
                    status,
                    profile_target.symbol_id if profile_target else None,
                    target.locator if target else None,
                    reason,
                )
            )
        return tuple(sorted(result, key=lambda item: item.reference_id))

    def _package_snapshots(
        self, symbols: tuple[MaximalPythonSymbol, ...]
    ) -> tuple[PythonInterfaceSnapshot, ...]:
        result = []
        for package in self.manifest.packages:
            values = tuple(item for item in symbols if item.package == package.name)
            exported = tuple(item for item in values if item.exported)
            contracts = tuple(
                sorted(
                    (item.locator, _canonical(item.contract())) for item in exported
                )
            )
            interface_revision = _digest("pyiface", contracts)
            implementation_revision = _digest(
                "pyimpl",
                tuple(
                    sorted((item.symbol_id, item.revision_id) for item in values)
                ),
            )
            result.append(
                PythonInterfaceSnapshot(
                    package.name,
                    interface_revision,
                    implementation_revision,
                    tuple(item.locator for item in sorted(exported, key=lambda x: x.locator)),
                    contracts,
                )
            )
        return tuple(sorted(result, key=lambda item: item.package))

    def _error(
        self,
        code: str,
        message: str,
        path: str,
        line: int,
        column: int,
        severity: str,
        category: str,
    ) -> None:
        self.diagnostics.append(
            MaximalPythonDiagnostic(
                code, message, path, line, column, severity, category
            )
        )


def _signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None,
) -> str:
    if isinstance(node, ast.ClassDef):
        members: list[tuple[str, str, str | None]] = []
        for member in node.body:
            if isinstance(member, ast.AnnAssign) and isinstance(
                member.target, ast.Name
            ):
                if not member.target.id.startswith("_"):
                    members.append(
                        (
                            member.target.id,
                            "field",
                            _annotation(member.annotation),
                        )
                    )
            elif isinstance(member, ast.Assign):
                for target in member.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith(
                        "_"
                    ):
                        members.append(
                            (
                                target.id,
                                "value",
                                ast.dump(
                                    member.value,
                                    include_attributes=False,
                                ),
                            )
                        )
            elif isinstance(
                member, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and not member.name.startswith("_"):
                members.append(
                    (member.name, "method", _signature(member))
                )
        return _canonical(
            {
                "bases": [
                    _annotation(base) or "<dynamic>" for base in node.bases
                ],
                "members": sorted(members),
            }
        )
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return "unknown"
    parameters = []
    for item in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
        parameters.append(
            f"{item.arg}: {_annotation(item.annotation) or 'Unknown'}"
        )
    return (
        f"({', '.join(parameters)}) -> "
        f"{_annotation(node.returns) or 'Unknown'}"
    )


def _types_compatible(actual: str, expected: str) -> bool:
    aliases = {"Int": "int", "Text": "str", "Bool": "bool", "Unit": "None"}
    return actual == aliases.get(expected, expected)


def analyze_maximal_python(
    sources: Mapping[str, str],
    manifest: MaximalPythonManifest,
    *,
    identity_map: Mapping[str, str] | None = None,
) -> MaximalPythonReport:
    if not sources:
        raise ValueError("at least one Python source is required")
    return _ProfileAnalyzer(sources, manifest, identity_map).run()


def manifest_for_sources(sources: Mapping[str, str]) -> MaximalPythonManifest:
    exports_by_package: dict[str, set[str]] = {}
    prefixes: set[str] = set()
    for path, source in sorted(sources.items()):
        module = _module_name(path)
        prefix = module.split(".", 1)[0]
        prefixes.add(prefix)
        exports = exports_by_package.setdefault(prefix, set())
        tree = ast.parse(source, filename=path)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                exports.add(f"{module}.{node.name}")
    return MaximalPythonManifest(
        tuple(
            MaximalPythonPackageManifest(
                name=prefix,
                module_prefix=prefix,
                exports=tuple(sorted(exports_by_package.get(prefix, ()))),
            )
            for prefix in sorted(prefixes)
        )
    )


_BOOTSTRAP_SOURCE = r'''import json
import os
import runpy
import sys

root = os.path.realpath(sys.argv[1])
entry = os.path.realpath(sys.argv[2])
forbidden_imports = set(json.loads(sys.argv[3]))
audit_events = []

class PolicyViolation(RuntimeError):
    pass


def audit(event, arguments):
    if event == "import" and arguments:
        root_name = str(arguments[0]).split(".", 1)[0]
        if root_name in forbidden_imports:
            audit_events.append(event + ":" + root_name)
            raise PolicyViolation("forbidden import: " + root_name)
    if event == "open" and arguments:
        value = arguments[0]
        if isinstance(value, str):
            path = os.path.realpath(value)
            allowed = path.startswith(root + os.sep) or path.startswith(sys.base_prefix + os.sep)
            if not allowed:
                audit_events.append("open:" + path)
                raise PolicyViolation("forbidden open: " + path)
    if any(event.startswith(prefix) for prefix in %r):
        audit_events.append(event)
        raise PolicyViolation("forbidden audit event: " + event)

sys.addaudithook(audit)
sys.path.insert(0, root)
status = "COMPLETED"
error = None
try:
    runpy.run_path(entry, run_name="__main__")
except PolicyViolation as exc:
    status = "RUNTIME_POLICY_BLOCK"
    error = str(exc)
except BaseException as exc:
    status = "PROGRAM_ERROR"
    error = type(exc).__name__ + ": " + str(exc)
print("__MELDRA_STRICT_RESULT__" + json.dumps({"status": status, "error": error, "audit_events": audit_events}, sort_keys=True))
''' % (_AUDIT_EVENT_PREFIXES,)


def run_restricted_python(
    sources: Mapping[str, str],
    manifest: MaximalPythonManifest,
    *,
    entry_path: str,
    timeout: float = 10.0,
) -> StrictExecutionResult:
    report = analyze_maximal_python(sources, manifest)
    if not report.ok:
        return StrictExecutionResult(
            "STATIC_POLICY_BLOCK",
            None,
            "",
            "",
            tuple(sorted({item.code for item in report.blocking_diagnostics})),
            (),
            False,
        )
    with tempfile.TemporaryDirectory(prefix="meldra-maximal-python-") as temporary:
        root = Path(temporary)
        for relative_path, source in sources.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        entry = root / entry_path
        if not entry.is_file():
            raise ValueError(f"entry path is missing: {entry_path}")
        forbidden = tuple(
            sorted(
                {
                    name
                    for package in manifest.packages
                    for name in package.forbidden_imports
                    if name not in package.allowed_ambient_imports
                }
            )
        )
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    _BOOTSTRAP_SOURCE,
                    str(root),
                    str(entry),
                    json.dumps(forbidden),
                ],
                cwd=root,
                env={**os.environ, "PYTHONHASHSEED": "0"},
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return StrictExecutionResult(
                "INFRASTRUCTURE_FAILURE",
                None,
                "",
                "",
                (),
                (),
                None,
                f"{type(exc).__name__}: {exc}",
            )
    marker = "__MELDRA_STRICT_RESULT__"
    result_line = next(
        (line for line in reversed(completed.stdout.splitlines()) if line.startswith(marker)),
        None,
    )
    if result_line is None:
        return StrictExecutionResult(
            "INFRASTRUCTURE_FAILURE",
            completed.returncode,
            completed.stdout,
            completed.stderr,
            (),
            (),
            None,
            "restricted harness emitted no result marker",
        )
    payload = json.loads(result_line.removeprefix(marker))
    stdout = "\n".join(
        line for line in completed.stdout.splitlines() if not line.startswith(marker)
    )
    status = str(payload["status"])
    return StrictExecutionResult(
        status,
        completed.returncode,
        stdout,
        completed.stderr,
        (str(payload["error"]),) if payload.get("error") else (),
        tuple(str(item) for item in payload.get("audit_events", ())),
        status == "COMPLETED" and bool(payload.get("audit_events")),
    )


def _updated_manifest(
    manifest: MaximalPythonManifest,
    change: MaximalPythonChange,
    old_locator: str,
    new_locator: str,
) -> MaximalPythonManifest:
    packages = []
    for package in manifest.packages:
        exports = tuple(
            new_locator if item == old_locator else item for item in package.exports
        )
        effects_value = tuple(
            (new_locator if key == old_locator else key, value)
            for key, value in package.function_effects
        )
        capabilities = tuple(
            (new_locator if key == old_locator else key, value)
            for key, value in package.function_capabilities
        )
        packages.append(
            replace(
                package,
                exports=exports,
                function_effects=effects_value,
                function_capabilities=capabilities,
            )
        )
    return MaximalPythonManifest(tuple(packages))


def apply_maximal_python_change(
    sources: Mapping[str, str],
    manifest: MaximalPythonManifest,
    change: MaximalPythonChange,
) -> MaximalPythonChangeResult:
    with tempfile.TemporaryDirectory(prefix="meldra-maximal-change-") as temporary:
        root = Path(temporary)
        for relative_path, source in sources.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        state = root / ".merlo" / "world.json"
        world = SoftwareWorld.scan(root, state)
        target = world.program.entity(change.target)
        operation = {
            "rename": "rename_symbol",
            "move": "move_symbol",
            "change_signature": "change_signature",
        }.get(change.operation)
        if operation is None:
            raise ValueError(f"unsupported maximal Python change: {change.operation}")
        capability = EditCapability.for_operation(
            operation,
            target.id,
            allowed_files=tuple(sorted(sources)),
            max_files=max(1, len(sources)),
            max_entities=500,
            max_edits=1000,
            allow_new_dependencies=True,
            allow_public_api_break=True,
        )
        if change.operation == "rename":
            plan = world.plan_rename(target.id, change.value, capability)
        elif change.operation == "move":
            plan = world.plan_move(target.id, change.value, capability)
        else:
            plan = world.plan_change_signature(
                target.id,
                change.value,
                capability,
                argument_values=dict(change.argument_values),
            )
        if not plan.ready:
            return MaximalPythonChangeResult(
                False,
                tuple(item.message for item in plan.obligations if item.blocking),
                (),
                tuple(sorted(sources.items())),
                manifest,
                target.id,
                None,
                (),
                True,
            )
        before_report = analyze_maximal_python(sources, manifest)
        changed_files = world.apply(plan, capability)
        changed_sources = {
            path: (root / path).read_text(encoding="utf-8")
            for path in sources
        }
        migrated = world.program.entity(target.id)
        next_manifest = _updated_manifest(
            manifest, change, target.fqname, migrated.fqname
        )
        after_report = analyze_maximal_python(changed_sources, next_manifest)
        changed_packages = tuple(
            sorted(
                package.package
                for package in before_report.packages
                if package.interface_revision_id
                != after_report.package(package.package).interface_revision_id
            )
        )
        unchanged_files = set(sources) - set(changed_files)
        preserved = all(changed_sources[path] == sources[path] for path in unchanged_files)
        return MaximalPythonChangeResult(
            True,
            (),
            tuple(changed_files),
            tuple(sorted(changed_sources.items())),
            next_manifest,
            target.id,
            migrated.id,
            changed_packages,
            preserved,
        )


__all__ = [
    "MAXIMAL_PYTHON_SCHEMA_VERSION",
    "MaximalPythonChange",
    "MaximalPythonChangeResult",
    "MaximalPythonDiagnostic",
    "MaximalPythonManifest",
    "MaximalPythonPackageManifest",
    "MaximalPythonReference",
    "MaximalPythonReport",
    "MaximalPythonSymbol",
    "PythonInterfaceSnapshot",
    "StrictExecutionResult",
    "analyze_maximal_python",
    "apply_maximal_python_change",
    "effects",
    "manifest_for_sources",
    "requires",
    "run_restricted_python",
]
