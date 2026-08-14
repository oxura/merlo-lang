from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


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


_DECLARATION = re.compile(
    r"^(export\s+)?(fn|task|record|enum|const)\s+([A-Za-z_]\w*)(.*)$"
)
_INFERRED_FUNCTION = re.compile(
    r"^(export\s+)?([a-z_][A-Za-z0-9_]*)\s*(\(.*)$"
)
_INFERRED_RECORD = re.compile(
    r"^(export\s+)?([A-Z][A-Za-z0-9_]*)\s*(:)$"
)


def _declaration(source: str) -> tuple[str | None, str, str, str] | None:
    explicit = _DECLARATION.fullmatch(source)
    if explicit is not None:
        exported, kind, name, suffix = explicit.groups()
        return exported, kind, name, suffix
    function = _INFERRED_FUNCTION.fullmatch(source)
    if function is not None:
        exported, name, suffix = function.groups()
        if re.search(r"(?:=|:)\s*(?:[^:]*)$", suffix):
            return exported, "fn", name, suffix
    record = _INFERRED_RECORD.fullmatch(source)
    if record is not None:
        exported, name, suffix = record.groups()
        return exported, "record", name, suffix
    return None
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STDLIB_MODULES = {
    "app.json": Path(__file__).with_name("stdlib") / "json.mlo",
    "app.csv": Path(__file__).with_name("stdlib") / "csv.mlo",
    "std.core": _REPOSITORY_ROOT / "stdlib" / "std" / "core.mlo",
    "std.option": _REPOSITORY_ROOT / "stdlib" / "std" / "option.mlo",
    "std.result": _REPOSITORY_ROOT / "stdlib" / "std" / "result.mlo",
    "std.text": _REPOSITORY_ROOT / "stdlib" / "std" / "text.mlo",
    "std.bytes": _REPOSITORY_ROOT / "stdlib" / "std" / "bytes.mlo",
    "std.collections": _REPOSITORY_ROOT / "stdlib" / "std" / "collections.mlo",
    "std.io": _REPOSITORY_ROOT / "stdlib" / "std" / "io.mlo",
    "std.fs": _REPOSITORY_ROOT / "stdlib" / "std" / "fs.mlo",
    "std.cli": _REPOSITORY_ROOT / "stdlib" / "std" / "cli.mlo",
    "std.time": _REPOSITORY_ROOT / "stdlib" / "std" / "time.mlo",
    "std.random": _REPOSITORY_ROOT / "stdlib" / "std" / "random.mlo",
    "std.json": _REPOSITORY_ROOT / "stdlib" / "std" / "json.mlo",
    "std.net": _REPOSITORY_ROOT / "stdlib" / "std" / "net.mlo",
    "std.http": _REPOSITORY_ROOT / "stdlib" / "std" / "http.mlo",
}


def _normalized_lines(source: str) -> tuple[str, ...]:
    return tuple(line.rstrip() for line in source.replace("\r\n", "\n").splitlines())


def _module(source: str, *, path: str, expected_name: str | None = None) -> Module:
    lines = _normalized_lines(source)
    if not lines:
        raise ModuleError(f"{path}: empty module")
    declaration = re.fullmatch(
        r"module\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)",
        lines[0].strip(),
    )
    if declaration is None:
        raise ModuleError(f"{path}:1: expected `module qualified.name`")
    name = declaration.group(1)
    if expected_name is not None and name != expected_name:
        raise ModuleError(f"{path}: declares {name!r}, expected {expected_name!r}")
    imports: list[str] = []
    symbols: list[ModuleSymbol] = []
    seen: set[str] = set()
    index = 1
    header = True
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        imported = re.fullmatch(
            r"use\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)",
            stripped,
        )
        if imported is not None:
            if not header:
                raise ModuleError(f"{path}:{index + 1}: imports must precede declarations")
            imports.append(imported.group(1))
            index += 1
            continue
        header = False
        matched = _declaration(stripped)
        if matched is None:
            raise ModuleError(f"{path}:{index + 1}: unsupported top-level declaration")
        exported, kind, symbol_name, suffix = matched
        if symbol_name in seen:
            raise ModuleError(f"{path}:{index + 1}: duplicate symbol {symbol_name!r}")
        seen.add(symbol_name)
        start = index
        index += 1
        while index < len(lines) and (
            not lines[index].strip() or lines[index].startswith((" ", "\t"))
        ):
            index += 1
        block = "\n".join(lines[start:index]).strip() + "\n"
        signature = re.sub(r"\s+", " ", f"{kind} {symbol_name}{suffix}".strip())
        symbol_id = SymbolId(_digest("sym", name, kind, symbol_name))
        interface_id = InterfaceRevisionId(
            _digest("iface", name, kind, symbol_name, signature, bool(exported))
        )
        symbols.append(
            ModuleSymbol(
                name,
                symbol_name,
                kind,
                bool(exported),
                signature,
                start + 1,
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
        tuple(imports),
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
        for module in modules.values():
            locals_by_name = {item.name for item in module.symbols}
            dependencies = [modules[name] for name in module.imports]
            aliases = {
                alias: dependency
                for dependency in dependencies
                for alias in (dependency.name, dependency.name.rsplit(".", 1)[-1])
            }

            def validate_unqualified(name: str) -> None:
                if name in locals_by_name:
                    return
                candidates = [
                    dependency.symbol(name)
                    for dependency in dependencies
                    if any(item.name == name for item in dependency.symbols)
                ]
                exported = [item for item in candidates if item.exported]
                if len(exported) > 1:
                    raise ModuleError(f"AmbiguousReference: {module.name}.{name}")
                if candidates and not exported:
                    owner = candidates[0].module
                    raise ModuleError(
                        f"PrivateSymbol: {owner}.{name} is not exported"
                    )

            for symbol in module.symbols:
                for name in re.findall(r"\b[A-Za-z_]\w*\b", symbol.signature):
                    validate_unqualified(name)
            for alias, name in re.findall(
                r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\(",
                module.source,
            ):
                dependency = aliases.get(alias)
                if dependency is None:
                    continue
                symbol = dependency.symbol(name)
                if not symbol.exported:
                    raise ModuleError(
                        f"PrivateSymbol: {dependency.name}.{name} is not exported"
                    )
            for name in re.findall(
                r"(?<![\w.])([A-Za-z_]\w*)\s*\(",
                module.source,
            ):
                validate_unqualified(name)

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
