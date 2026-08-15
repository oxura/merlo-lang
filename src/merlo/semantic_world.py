from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from merlo.modules import ModuleGraph
from merlo.version import VERSIONS

WORLD_SCHEMA_VERSION = VERSIONS.semantic_world
WORLD_CONTRACT = "merlo.semantic-world.v9"


class WorldError(ValueError):
    """A semantic world cannot answer an exact query."""


class StaleWorldError(WorldError):
    """The source or compiler inputs no longer match a saved world."""


class UnsupportedMigration(WorldError):
    """A refactor cannot be migrated from exact semantic facts."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _span_dict(span: Any, path: str) -> dict[str, Any]:
    if span is None:
        return {"path": path, "line": 1, "column": 0, "end_line": 1, "end_column": 0}
    result = span.to_dict() if hasattr(span, "to_dict") else dict(span)
    result.setdefault("path", path)
    return result


def _module_root(entry: Path) -> Path:
    if entry.name == "main.mlo" and entry.parent.name == "src":
        return entry.parent.parent
    return entry.parent


def _source_hashes(graph: ModuleGraph, root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for module in graph.modules:
        path = Path(module.path).resolve()
        try:
            key = str(path.relative_to(root))
        except ValueError:
            key = str(path)
        result[key] = hashlib.sha256(module.source.encode("utf-8")).hexdigest()
    return dict(sorted(result.items()))


def _lock_hash(root: Path, lockfile: str | Path | None) -> str | None:
    candidate = Path(lockfile).resolve() if lockfile is not None else root / "merlo.lock"
    if not candidate.is_file():
        return None
    return hashlib.sha256(candidate.read_bytes()).hexdigest()


def _source_for_symbol(module: Any, line: int) -> tuple[int, int, str]:
    lines = module.source.splitlines(keepends=True)
    start = max(0, line - 1)
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end].startswith((" ", "\t"))):
        end += 1
    return line, end, "".join(lines[start:end])


def _concise_span(compilation: Any, node: Any, fallback_path: str) -> dict[str, Any]:
    source_map = {
        item["node_id"]: item["concise"]
        for item in compilation.diagnostic_source_map
    }
    mapped = source_map.get(getattr(node, "id", None))
    if mapped is not None:
        return dict(mapped)
    return _span_dict(getattr(node, "source", None), fallback_path)

@dataclass
class SemanticWorld:
    root: Path
    state_path: Path
    data: dict[str, Any]
    _symbols: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    _by_name: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False)
    _references: tuple[dict[str, Any], ...] = field(default=(), repr=False)
    _calls: tuple[dict[str, Any], ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        self.state_path = Path(self.state_path).resolve()
        self._symbols = {item["symbol_id"]: item for item in self.data.get("symbols", ())}
        names: dict[str, list[str]] = {}
        for item in self._symbols.values():
            for key in (item["qualified_name"], item["name"]):
                names.setdefault(key, []).append(item["symbol_id"])
        self._by_name = {key: tuple(sorted(values)) for key, values in names.items()}
        self._references = tuple(sorted(self.data.get("references", ()), key=lambda x: (x.get("source", {}).get("path", ""), x.get("source", {}).get("line", 0), x.get("reference_id", ""))))
        self._calls = tuple(sorted(self.data.get("calls", ()), key=lambda x: (x.get("caller_id", ""), x.get("callee_id", ""), x.get("source", {}).get("line", 0))))

    @classmethod
    def build(
        cls,
        source: str | Path | Any,
        *,
        state_path: str | Path | None = None,
        lockfile: str | Path | None = None,
        previous: "SemanticWorld | None" = None,
        require_interface_lock: bool = False,
    ) -> "SemanticWorld":
        from merlo.compiler import ProjectCompilation, compile_project

        compilation = source if isinstance(source, ProjectCompilation) else compile_project(source, require_interface_lock=require_interface_lock)
        entry = Path(compilation.entry_path).resolve()
        root = _module_root(entry)
        world_path = Path(state_path).resolve() if state_path is not None else root / ".merlo" / "world.json"
        graph = compilation.module_graph
        source_hashes = _source_hashes(graph, root)
        lock_path = (Path(lockfile).resolve() if lockfile is not None else root / "merlo.lock")
        lock_hash = _lock_hash(root, lockfile)
        hir_by_location = {
            (str(Path(item.source.path).resolve()), item.source.line): item
            for item in compilation.hir.functions
        }
        hir_type_by_location = {
            (str(Path(item.source.path).resolve()), item.source.line): item
            for item in compilation.hir.types
        }
        task_by_location = {
            (str(Path(item.path).resolve()), item.line): item
            for item in compilation.elaborated.tasks
        }
        modules: list[dict[str, Any]] = []
        symbols: list[dict[str, Any]] = []
        symbol_by_location = {
            (str(Path(module.path).resolve()), item.line): item.symbol_id.value
            for module in graph.modules
            for item in module.symbols
        }
        for module in graph.modules:
            module_symbols: list[dict[str, Any]] = []
            for item in module.symbols:
                start, end, definition = _source_for_symbol(module, item.line)
                location = (str(Path(module.path).resolve()), item.line)
                hir = hir_by_location.get(location)
                task = task_by_location.get(location)
                hir_type = hir_type_by_location.get(location)
                obligation_owner = (
                    hir.symbol_id
                    if hir is not None
                    else hir_type.symbol_id
                    if hir_type is not None
                    else None
                )
                source_span = {
                    "path": module.path,
                    "line": start,
                    "column": 0,
                    "end_line": end,
                    "end_column": 0,
                }
                effects = tuple(sorted(set(getattr(hir, "effects", ())) | set(getattr(task, "effects", ()))))
                capabilities = tuple(sorted(set(getattr(task, "capabilities", ())) | set(effects)))
                record = {
                    "symbol_id": item.symbol_id.value,
                    "name": item.name,
                    "qualified_name": f"{module.name}.{item.name}",
                    "module": module.name,
                    "kind": item.kind,
                    "exported": item.exported,
                    "public": item.exported,
                    "signature": item.signature,
                    "revision_id": item.revision_id.value,
                    "interface_revision_id": item.interface_revision_id.value,
                    "implementation_revision_id": module.implementation_revision_id.value,
                    "source": source_span,
                    "definition": definition,
                    "types": sorted({getattr(parameter, "type_name", "") for parameter in getattr(hir, "parameters", ())} | ({getattr(hir, "return_type", "")} if hir is not None else set()) - {""}),
                    "effects": list(effects),
                    "capabilities": list(capabilities),
                    "requirements": [
                        item.expression
                        for item in hir.requirements
                    ] if hir is not None else [],
                    "ensures": [
                        item.expression
                        for item in hir.ensures
                    ] if hir is not None else [],
                    "invariants": [
                        item.expression
                        for item in hir_type.invariants
                    ] if hir_type is not None else [],
                    "holes": [
                        {
                            **node.attribute_map,
                            "source": node.source.to_dict(),
                            "node_id": node.id,
                        }
                        for node in hir.walk()
                        if node.kind == "TypedHole"
                    ] if hir is not None else [],
                    "obligations": [
                        obligation.obligation_id
                        for obligation
                        in compilation.obligations.obligations
                        if obligation.owner_symbol_id
                        == obligation_owner
                    ],
                    "ownership": sorted({getattr(parameter, "ownership", "") for parameter in getattr(hir, "parameters", ())} - {""}),
                    "resources": sorted({attribute.get("resource") for node in hir.walk() for attribute in [node.attribute_map] if attribute.get("resource") is not None}) if hir is not None else [],
                }
                symbols.append(record)
                module_symbols.append(record)
            resolved_path = Path(module.path).resolve()
            try:
                source_key = str(resolved_path.relative_to(root))
            except ValueError:
                source_key = module.path
            modules.append({
                "name": module.name,
                "path": module.path,
                "imports": sorted(module.imports),
                "symbols": [item["symbol_id"] for item in module_symbols],
                "interface_revision_id": module.interface_revision_id.value,
                "implementation_revision_id": module.implementation_revision_id.value,
                "source_sha256": source_hashes.get(source_key),
            })

        refs: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        hir_symbol_ids = {
            function.name: symbol_by_location.get(
                (str(Path(function.source.path).resolve()), function.source.line)
            )
            for function in compilation.hir.functions
        }
        for function in compilation.hir.functions:
            caller_id = hir_symbol_ids.get(function.name)
            if caller_id is None:
                continue
            for node in function.walk():
                attributes = node.attribute_map
                callee_text = attributes.get("callee")
                if not callee_text or node.kind not in {
                    "DirectCall",
                    "CallbackCall",
                    "ResultPropagation",
                }:
                    continue
                callee_id = hir_symbol_ids.get(str(callee_text))
                if callee_id is None:
                    continue
                span = _concise_span(compilation, node, compilation.hir.path)
                reference = {
                    "reference_id": _digest(
                        (caller_id, callee_id, span, node.kind)
                    )[:24],
                    "target_id": callee_id,
                    "owner_id": caller_id,
                    "kind": "call",
                    "usage": node.kind,
                    "source": span,
                    "resolution": "exact",
                }
                refs.append(reference)
                calls.append(
                    {
                        "call_id": reference["reference_id"],
                        "caller_id": caller_id,
                        "callee_id": callee_id,
                        "source": span,
                        "kind": node.kind,
                    }
                )
        unique_calls = {
            (item["caller_id"], item["callee_id"], item["source"]["path"], item["source"]["line"], item["source"]["column"]): item
            for item in calls
        }
        calls = list(unique_calls.values())
        unique_refs = {
            (
                item["owner_id"],
                item["target_id"],
                item["source"]["path"],
                item["source"]["line"],
                item["source"]["column"],
            ): item
            for item in refs
        }
        refs = list(unique_refs.values())

        types = [item.to_dict() for item in compilation.hir.types]
        data_dependencies = [{"owner_id": item["caller_id"], "target_id": item["callee_id"], "kind": "call", "source": item["source"]} for item in calls]
        interfaces = [item.to_dict() for item in compilation.elaborated.interfaces]
        tests: list[dict[str, Any]] = []
        tests_root = root / "tests"
        if tests_root.is_dir():
            for path in sorted(tests_root.rglob("*.mlo")):
                tests.append({"path": str(path), "name": path.stem, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        payload: dict[str, Any] = {
            "schema_version": WORLD_SCHEMA_VERSION,
            "contract": WORLD_CONTRACT,
            "root": str(root),
            "entry_path": str(entry),
            "versions": compilation.versions.to_dict(),
            "source_hashes": source_hashes,
            "lockfile_path": str(lock_path) if lock_path.is_file() else None,
            "lockfile_sha256": lock_hash,
            "modules": sorted(modules, key=lambda item: item["name"]),
            "symbols": sorted(symbols, key=lambda item: item["symbol_id"]),
            "revisions": sorted(({item["revision_id"]: {"revision_id": item["revision_id"], "symbol_id": item["symbol_id"], "interface_revision_id": item["interface_revision_id"], "implementation_revision_id": item["implementation_revision_id"]} for item in symbols}).values(), key=lambda item: item["revision_id"]),
            "definitions": sorted(({item["symbol_id"]: {"symbol_id": item["symbol_id"], "source": item["source"], "text": item["definition"]} for item in symbols}).values(), key=lambda item: item["symbol_id"]),
            "references": sorted(refs, key=lambda item: item["reference_id"]),
            "calls": sorted(calls, key=lambda item: item["call_id"]),
            "types": sorted(types, key=lambda item: (item.get("name", ""), item.get("symbol_id", ""))),
            "data_dependencies": sorted(data_dependencies, key=lambda item: (item["owner_id"], item["target_id"])),
            "module_dependencies": [{"module": item["name"], "imports": item["imports"]} for item in sorted(modules, key=lambda item: item["name"])],
            "effects": sorted({effect for item in symbols for effect in item["effects"]}),
            "capabilities": sorted({capability for item in symbols for capability in item["capabilities"]}),
            "ownership": sorted([[item["symbol_id"], value] for item in symbols for value in item["ownership"]]),
            "resources": sorted({value for item in symbols for value in item["resources"]}),
            "interfaces": sorted(interfaces, key=lambda item: (item.get("module", ""), item.get("name", ""))),
            "obligations": [
                item.to_dict()
                for item in compilation.obligations.obligations
            ],
            "range_analysis": compilation.range_analysis.to_dict(),
            "bounded_symbolic": compilation.bounded_symbolic.to_dict(),
            "smt": compilation.smt.to_dict(),
            "property_evidence": compilation.property_evidence.to_dict(),
            "tests": tests,
        }
        payload["world_digest"] = _digest(payload)
        return cls(root, world_path, payload)

    @classmethod
    def load(cls, state_path: str | Path) -> "SemanticWorld":
        path = Path(state_path).resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != WORLD_SCHEMA_VERSION or payload.get("contract") != WORLD_CONTRACT:
            raise WorldError("SemanticWorldVersionMismatch")
        expected = payload.get("world_digest")
        actual_payload = dict(payload)
        actual_payload.pop("world_digest", None)
        if expected != _digest(actual_payload):
            raise WorldError("SemanticWorldDigestMismatch")
        return cls(Path(payload["root"]), path, payload)

    @property
    def digest(self) -> str:
        return str(self.data["world_digest"])

    @property
    def world_revision(self) -> str:
        return self.digest

    def to_dict(self) -> dict[str, Any]:
        return json.loads(_json(self.data))

    def to_json(self) -> str:
        return _json(self.data)

    def save(self, path: str | Path | None = None) -> Path:
        destination = Path(path).resolve() if path is not None else self.state_path
        encoded = (self.to_json() + "\n").encode("utf-8")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent, delete=False) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
                temporary = Path(handle.name)
            os.replace(temporary, destination)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        self.state_path = destination
        return destination

    def require_fresh(self) -> None:
        current: dict[str, str] = {}
        for relative in self.data.get("source_hashes", {}):
            path = self.root / relative if not Path(relative).is_absolute() else Path(relative)
            if not path.is_file():
                raise StaleWorldError(f"StaleWorld: missing source {path}")
            current[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        if current != self.data.get("source_hashes", {}):
            raise StaleWorldError("StaleWorld: source hashes changed")
        lock_path = self.data.get("lockfile_path")
        lock = (
            hashlib.sha256(Path(lock_path).read_bytes()).hexdigest()
            if lock_path is not None and Path(lock_path).is_file()
            else None
        )
        if lock != self.data.get("lockfile_sha256"):
            raise StaleWorldError("StaleWorld: lockfile changed")

    def resolve(self, target: str) -> dict[str, Any]:
        if target in self._symbols:
            return self._symbols[target]
        ids = self._by_name.get(target, ())
        if len(ids) != 1:
            if not ids:
                raise WorldError(f"UnknownSymbol: {target}")
            raise WorldError(f"AmbiguousSymbol: {target}")
        return self._symbols[ids[0]]

    def inspect(self, target: str) -> dict[str, Any]:
        symbol = self.resolve(target)
        return {"symbol": symbol, "references": list(self.references(symbol["symbol_id"])), "callers": list(self.callers(symbol["symbol_id"])), "callees": list(self.callees(symbol["symbol_id"])), "dependencies": list(self.dependencies(symbol["symbol_id"]))}

    def references(self, target: str) -> tuple[dict[str, Any], ...]:
        identifier = self.resolve(target)["symbol_id"] if target not in self._symbols else target
        return tuple(item for item in self._references if item.get("target_id") == identifier)

    def callers(self, target: str, *, transitive: bool = False) -> tuple[dict[str, Any], ...]:
        identifier = self.resolve(target)["symbol_id"] if target not in self._symbols else target
        direct = {item["caller_id"] for item in self._calls if item["callee_id"] == identifier}
        ids = set(direct)
        if transitive:
            frontier = set(direct)
            while frontier:
                frontier = {item["caller_id"] for item in self._calls if item["callee_id"] in frontier} - ids
                ids.update(frontier)
        return tuple(self._symbols[item] for item in sorted(ids))

    def callees(self, target: str) -> tuple[dict[str, Any], ...]:
        identifier = self.resolve(target)["symbol_id"] if target not in self._symbols else target
        return tuple(self._symbols[item] for item in sorted({item["callee_id"] for item in self._calls if item["caller_id"] == identifier}))

    def dependencies(self, target: str) -> tuple[dict[str, Any], ...]:
        symbol = self.resolve(target)
        module = next(item for item in self.data["modules"] if item["name"] == symbol["module"])
        ids = {item["callee_id"] for item in self._calls if item["caller_id"] == symbol["symbol_id"]}
        ids.update(item["symbol_id"] for item in self._symbols.values() if item["module"] in module["imports"] and item["exported"])
        return tuple(self._symbols[item] for item in sorted(ids))

    def effects(self, target: str | None = None) -> tuple[str, ...]:
        return tuple(self.data.get("effects", ())) if target is None else tuple(self.resolve(target).get("effects", ()))

    def capabilities(self, target: str | None = None) -> tuple[str, ...]:
        return tuple(self.data.get("capabilities", ())) if target is None else tuple(self.resolve(target).get("capabilities", ()))

    def source(self, target: str) -> str:
        return str(self.resolve(target).get("definition", ""))

    def search(self, query: str) -> tuple[dict[str, Any], ...]:
        needle = query.casefold()
        return tuple(item for item in sorted(self._symbols.values(), key=lambda value: value["qualified_name"]) if needle in item["qualified_name"].casefold() or needle in item["signature"].casefold())

    def impact(self, target: str) -> dict[str, Any]:
        symbol = self.resolve(target)
        identifier = symbol["symbol_id"]
        refs = self.references(identifier)
        callers = self.callers(identifier, transitive=True)
        interface = symbol["interface_revision_id"] if symbol["exported"] else None
        tests = tuple(self.data.get("tests", ())) if symbol["exported"] else ()
        return {"target": symbol, "references": list(refs), "callers": list(callers), "callees": list(self.callees(identifier)), "dependencies": list(self.dependencies(identifier)), "interface_impact": {"exported": symbol["exported"], "interface_revision_id": interface}, "tests": list(tests), "files": sorted({item["source"]["path"] for item in refs} | {symbol["source"]["path"]})}

    def map(self, projection: str = "text") -> str | dict[str, Any]:
        if projection == "json":
            return self.to_dict()
        if projection == "dot":
            lines = ["digraph semantic_world {"]
            for item in sorted(self._symbols.values(), key=lambda value: value["symbol_id"]):
                lines.append(f'  "{item["symbol_id"]}" [label="{item["qualified_name"]}"];')
            for item in self._calls:
                lines.append(f'  "{item["caller_id"]}" -> "{item["callee_id"]}";')
            lines.append("}")
            return "\n".join(lines)
        lines = [f"SemanticWorld {self.digest}"]
        for module in self.data["modules"]:
            lines.append(f"module {module['name']} ({module['interface_revision_id']})")
            for identifier in module["symbols"]:
                lines.append(f"  {self._symbols[identifier]['qualified_name']} {self._symbols[identifier]['signature']}")
        return "\n".join(lines)

    def compile_context(self, target: str, *, goal: str = "") -> dict[str, Any]:
        symbol = self.resolve(target)
        impact = self.impact(symbol["symbol_id"])
        obligation_ids = set(symbol["obligations"])
        obligations = [
            item
            for item in self.data.get("obligations", ())
            if item["obligation_id"] in obligation_ids
        ]
        return {"kind": "TaskCapsule", "goal": goal, "target": {"symbol_id": symbol["symbol_id"], "qualified_name": symbol["qualified_name"], "module": symbol["module"], "name": symbol["name"]}, "source": self.source(symbol["symbol_id"]), "signature": symbol["signature"], "dependent_types": symbol["types"], "callers": [item["symbol_id"] for item in impact["callers"]], "dependencies": [item["symbol_id"] for item in impact["dependencies"]], "effects": list(symbol["effects"]), "capabilities": list(symbol["capabilities"]), "requirements": list(symbol["requirements"]), "ensures": list(symbol["ensures"]), "invariants": list(symbol["invariants"]), "holes": list(symbol["holes"]), "obligations": obligations, "public_boundary": symbol["exported"], "tests": [item["path"] for item in self.data.get("tests", ())] if symbol["exported"] else []}

    def diagnostics_explain(self, diagnostic: str | Mapping[str, Any]) -> dict[str, Any]:
        code = diagnostic.get("code") if isinstance(diagnostic, Mapping) else str(diagnostic).split(":", 1)[0]
        descriptions = {"UnknownSymbol": "The target does not resolve to exactly one symbol.", "AmbiguousSymbol": "The target resolves to multiple symbols; use a qualified name or SymbolId.", "StaleWorld": "The source or dependency lock changed after this world was built.", "UnsupportedMigration": "The requested refactor cannot be migrated using exact semantic references."}
        return {"code": code, "message": descriptions.get(code, "No explanation is registered for this diagnostic."), "diagnostic": diagnostic}


__all__ = ["SemanticWorld", "StaleWorldError", "UnsupportedMigration", "WORLD_CONTRACT", "WORLD_SCHEMA_VERSION", "WorldError"]
