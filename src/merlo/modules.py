from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from merlo.module_syntax import ModuleSyntaxError, parse_module_prelude
from merlo.surface_ast import (
    SurfaceEnum,
    SurfaceFunction,
    SurfaceImplementation,
    SurfaceInterface,
    SurfaceRecord,
)
from merlo.surface_parser import SurfaceSyntaxError, parse_surface


class ModuleError(ValueError):
    """A module graph cannot be bound exactly."""


def _digest(kind: str, *parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{kind}_{hashlib.sha256(payload.encode()).hexdigest()}"


@dataclass(frozen=True, order=True)
class SymbolId:
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, order=True)
class RevisionId:
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, order=True)
class InterfaceRevisionId:
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, order=True)
class ImplementationRevisionId:
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ModuleSymbol:
    module: str
    name: str
    kind: str
    exported: bool
    signature: str
    line: int
    symbol_id: SymbolId
    revision_id: RevisionId
    interface_revision_id: InterfaceRevisionId

    def to_dict(self) -> dict[str, object]:
        return {
            "module": self.module,
            "name": self.name,
            "kind": self.kind,
            "exported": self.exported,
            "signature": self.signature,
            "line": self.line,
            "symbol_id": self.symbol_id.value,
            "revision_id": self.revision_id.value,
            "interface_revision_id": self.interface_revision_id.value,
        }


@dataclass(frozen=True)
class Module:
    name: str
    path: str
    imports: tuple[str, ...]
    symbols: tuple[ModuleSymbol, ...]
    interface_revision_id: InterfaceRevisionId
    implementation_revision_id: ImplementationRevisionId
    source: str

    def symbol(self, name: str) -> ModuleSymbol:
        matches = [item for item in self.symbols if item.name == name]
        if len(matches) != 1:
            raise ModuleError(f"{self.name}: expected one symbol {name!r}, found {len(matches)}")
        return matches[0]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": f"{self.name.replace('.', '/')}.mlo",
            "imports": list(self.imports),
            "symbols": [item.to_dict() for item in self.symbols],
            "interface_revision_id": self.interface_revision_id.value,
            "implementation_revision_id": self.implementation_revision_id.value,
        }


_STDLIB_ROOT = Path(__file__).with_name("stdlib") / "std"
STDLIB_MODULES = {
    "app.json": Path(__file__).with_name("stdlib") / "json.mlo",
    "app.csv": Path(__file__).with_name("stdlib") / "csv.mlo",
    "std.core": _STDLIB_ROOT / "core.mlo",
    "std.option": _STDLIB_ROOT / "option.mlo",
    "std.result": _STDLIB_ROOT / "result.mlo",
    "std.text": _STDLIB_ROOT / "text.mlo",
    "std.bytes": _STDLIB_ROOT / "bytes.mlo",
    "std.collections": _STDLIB_ROOT / "collections.mlo",
    "std.io": _STDLIB_ROOT / "io.mlo",
    "std.fs": _STDLIB_ROOT / "fs.mlo",
    "std.cli": _STDLIB_ROOT / "cli.mlo",
    "std.time": _STDLIB_ROOT / "time.mlo",
    "std.random": _STDLIB_ROOT / "random.mlo",
    "std.json": _STDLIB_ROOT / "json.mlo",
    "std.net": _STDLIB_ROOT / "net.mlo",
    "std.http": _STDLIB_ROOT / "http.mlo",
}


def _normalized_lines(source: str) -> tuple[str, ...]:
    return tuple(line.rstrip() for line in source.replace("\r\n", "\n").splitlines())


def _module(source: str, *, path: str, expected_name: str | None = None) -> Module:
    try:
        prelude = parse_module_prelude(source, path=path)
        program = parse_surface(source, path=path)
    except (ModuleSyntaxError, SurfaceSyntaxError) as exc:
        raise ModuleError(str(exc)) from exc
    if prelude.module is None:
        raise ModuleError(f"{path}:1: expected `module qualified.name`")
    name = prelude.module
    if expected_name is not None and name != expected_name:
        raise ModuleError(f"{path}: declares {name!r}, expected {expected_name!r}")
    lines = _normalized_lines(source)
    symbols: list[ModuleSymbol] = []
    seen: set[str] = set()
    for declaration in program.declarations:
        if isinstance(declaration, SurfaceImplementation):
            continue
        symbol_name = declaration.name
        line_number = declaration.span.start_line
        if symbol_name in seen:
            raise ModuleError(f"{path}:{line_number}: duplicate symbol {symbol_name!r}")
        seen.add(symbol_name)
        if isinstance(declaration, SurfaceRecord):
            kind = "record"
        elif isinstance(declaration, SurfaceEnum):
            kind = "enum"
        elif isinstance(declaration, SurfaceFunction):
            kind = declaration.declared_kind or "fn"
        elif isinstance(declaration, SurfaceInterface):
            kind = "interface"
        else:
            raise AssertionError(type(declaration).__name__)
        header = lines[line_number - 1].strip()
        if declaration.exported:
            header = header.removeprefix("export ").lstrip()
        header = header.removeprefix(f"{kind} ").lstrip()
        if not header.startswith(symbol_name):
            raise ModuleError(f"{path}:{line_number}: malformed declaration header")
        suffix = header[len(symbol_name) :]
        block = "\\n".join(
            lines[line_number - 1 : declaration.span.end_line]
        ).strip() + "\\n"
        signature = " ".join(f"{kind} {symbol_name}{suffix}".split())
        symbol_id = SymbolId(_digest("sym", name, kind, symbol_name))
        interface_id = InterfaceRevisionId(
            _digest("iface", name, kind, symbol_name, signature, declaration.exported)
        )
        symbols.append(
            ModuleSymbol(
                name,
                symbol_name,
                kind,
                declaration.exported,
                signature,
                line_number,
                symbol_id,
                RevisionId(_digest("rev", symbol_id.value, block)),
                interface_id,
            )
        )
    exported_interfaces = [
        item.interface_revision_id.value
        for item in symbols
        if item.exported
    ]
    return Module(
        name,
        path,
        prelude.imports,
        tuple(symbols),
        InterfaceRevisionId(_digest("module_iface", name, exported_interfaces)),
        ImplementationRevisionId(_digest("module_impl", name, lines)),
        source,
    )
 
 
@dataclass(frozen=True)
class ModuleGraph:
    modules: tuple[Module, ...]

    @classmethod
    def from_sources(
        cls,
        sources: Mapping[str, str],
        *,
        paths: Mapping[str, str] | None = None,
    ) -> "ModuleGraph":
        parsed = {
            name: _module(
                source,
                path=(paths or {}).get(name, f"{name.replace('.', '/')}.mlo"),
                expected_name=name,
            )
            for name, source in sources.items()
        }
        return cls._validated(parsed)

    @classmethod
    def load(cls, entry: str | Path) -> "ModuleGraph":
        entry_path = Path(entry).resolve()
        first_source = entry_path.read_text(encoding="utf-8")
        first = _module(first_source, path=str(entry_path))
        # Canonical projects keep sources below ``src``. Legacy applications
        # keep their entry and sibling modules below an ``app`` directory.
        # The source directory is authoritative even when module names carry
        # a package prefix such as ``app.main``.
        if entry_path.parent.name == "src":
            root = entry_path.parent
        else:
            root = entry_path
            for _ in first.name.split("."):
                root = root.parent
        parsed: dict[str, Module] = {}
        def visit(name: str, path: Path) -> None:
            if name in parsed:
                return
            if not path.exists() and name in STDLIB_MODULES:
                path = STDLIB_MODULES[name]
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise ModuleError(f"{path}: cannot read module: {exc}") from exc
            module = _module(source, path=str(path), expected_name=name)
            parsed[name] = module
            for dependency in module.imports:
                visit(
                    dependency,
                    root.joinpath(*dependency.split(".")).with_suffix(".mlo"),
                )

        visit(first.name, entry_path)
        return cls._validated(parsed)

    @classmethod
    def _validated(cls, modules: Mapping[str, Module]) -> "ModuleGraph":
        for module in modules.values():
            missing = sorted(set(module.imports) - set(modules))
            if missing:
                raise ModuleError(f"{module.name}: unresolved imports {missing}")
        visiting: list[str] = []
        complete: set[str] = set()
        order: list[str] = []

        def visit(name: str) -> None:
            if name in visiting:
                cycle = visiting[visiting.index(name) :] + [name]
                raise ModuleError("CyclicModuleImport: " + " -> ".join(cycle))
            if name in complete:
                return
            visiting.append(name)
            for dependency in sorted(modules[name].imports):
                visit(dependency)
            visiting.pop()
            complete.add(name)
            order.append(name)

        for name in sorted(modules):
            visit(name)
        cls._validate_references(modules)
        return cls(tuple(modules[name] for name in order))

    def module(self, name: str) -> Module:
        matches = [item for item in self.modules if item.name == name]
        if len(matches) != 1:
            raise ModuleError(f"expected one module {name!r}, found {len(matches)}")
        return matches[0]


    @staticmethod
    def _validate_references(modules: Mapping[str, Module]) -> None:
        from merlo.surface_binding import bind_module

        symbols = {
            module.name: {
                symbol.name: (symbol.kind, symbol.exported, symbol.name)
                for symbol in module.symbols
            }
            for module in modules.values()
        }
        for module in modules.values():
            try:
                program = parse_surface(module.source, path=module.path)
                bind_module(
                    module,
                    program,
                    symbols,
                    reject_unknown_calls=False,
                )  # type: ignore[arg-type]
            except (SurfaceSyntaxError, ValueError) as exc:
                raise ModuleError(str(exc)) from exc

    def resolve(self, module: str, name: str, *, requester: str) -> ModuleSymbol:
        symbol = self.module(module).symbol(name)
        if requester != module and not symbol.exported:
            raise ModuleError(f"PrivateSymbol: {module}.{name} is not exported")
        return symbol

    @property
    def interface_revision_id(self) -> InterfaceRevisionId:
        return InterfaceRevisionId(
            _digest(
                "graph_iface",
                [(item.name, item.interface_revision_id.value) for item in self.modules],
            )
        )

    @property
    def implementation_revision_id(self) -> ImplementationRevisionId:
        return ImplementationRevisionId(
            _digest(
                "graph_impl",
                [(item.name, item.implementation_revision_id.value) for item in self.modules],
            )
        )

    def symbols(self) -> Iterable[ModuleSymbol]:
        for module in self.modules:
            yield from module.symbols

    def to_dict(self) -> dict[str, object]:
        return {
            "contract": "merlo.module-graph.v1",
            "interface_revision_id": self.interface_revision_id.value,
            "implementation_revision_id": self.implementation_revision_id.value,
            "modules": [item.to_dict() for item in self.modules],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


__all__ = [
    "ImplementationRevisionId",
    "InterfaceRevisionId",
    "Module",
    "ModuleError",
    "ModuleGraph",
    "ModuleSymbol",
    "RevisionId",
    "STDLIB_MODULES",
    "SymbolId",
]
