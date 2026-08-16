from __future__ import annotations

import json
import re
from collections import deque
from pathlib import Path
from typing import Any, Mapping

from merlo.refactor import preview_fill_hole
from merlo.semantic_world import SemanticWorld, StaleWorldError, WorldError
from merlo.synthesis import (
    CandidateRank,
    SynthesisCandidate,
    SynthesisRequest,
    build_synthesis_candidate,
)


PACKAGE_PRODUCER_REVISION = "package/v1"
_MAX_CANDIDATES = 256
_DEFAULT_MAX_CANDIDATES = _MAX_CANDIDATES
_FUNCTION_KINDS = frozenset({"fn", "function"})
_SIGNATURE = re.compile(
    r"^(?:fn|function)\s+(?:[A-Za-z_][A-Za-z0-9_]*)(?:\s*<[^>]*>)?\s*"
    r"\((?P<parameters>[^)]*)\)\s*(?:->\s*(?P<return>.+?))?\s*$"
)


def _request(value: SynthesisRequest | Mapping[str, Any]) -> SynthesisRequest:
    if isinstance(value, SynthesisRequest):
        return value
    if isinstance(value, Mapping):
        return SynthesisRequest.from_dict(value)
    raise WorldError("SynthesisRequestSchemaMismatch")


def _validate_request(request: SynthesisRequest) -> tuple[str, int]:
    if request.operation != "fill_hole":
        raise WorldError("PackageOperationMismatch")
    arguments = request.arguments
    if not isinstance(arguments, Mapping) or set(arguments) not in (
        {"hole_id"},
        {"hole_id", "max_candidates"},
    ):
        raise WorldError("PackageInvalidArguments")
    hole_id = arguments.get("hole_id")
    if type(hole_id) is not str or not hole_id:
        raise WorldError("PackageInvalidHoleId")
    maximum = arguments.get("max_candidates", _DEFAULT_MAX_CANDIDATES)
    if type(maximum) is not int or not 1 <= maximum <= _MAX_CANDIDATES:
        raise WorldError("PackageInvalidMaxCandidates")
    return hole_id, maximum


def _target_hole(world: SemanticWorld, target: str, hole_id: str) -> Mapping[str, Any]:
    symbol = world.resolve(target)
    holes = symbol.get("holes")
    if not isinstance(holes, (list, tuple)):
        raise WorldError("PackageMalformedHoles")
    matches = tuple(item for item in holes if isinstance(item, Mapping) and item.get("hole_id") == hole_id)
    if len(matches) != 1:
        raise WorldError("PackageHoleNotOwned")
    return matches[0]


def _capsule_hole(world: SemanticWorld, target: str, hole_id: str, hole: Mapping[str, Any]) -> None:
    capsule = world.compile_context(target)
    matches = tuple(
        item
        for item in capsule.holes
        if item.get("hole_id") == hole_id
    )
    if len(matches) != 1 or any(
        matches[0].get(key) != hole.get(key)
        for key in (
            "hole_id",
            "node_id",
            "expected_type",
        )
    ):
        raise WorldError(
            "PackageHoleBindingMismatch"
        )


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _module_records(world: SemanticWorld) -> dict[str, Mapping[str, Any]]:
    records = world.data.get("modules", ())
    if not isinstance(records, (list, tuple)):
        raise WorldError("PackageMalformedModules")
    result: dict[str, Mapping[str, Any]] = {}
    for item in records:
        if not isinstance(item, Mapping) or not _nonempty_text(item.get("name")):
            raise WorldError("PackageMalformedModules")
        name = str(item["name"])
        if name in result:
            raise WorldError("PackageDuplicateModule")
        result[name] = item
    return result


def _symbol_records(world: SemanticWorld) -> tuple[Mapping[str, Any], ...]:
    records = world.data.get("symbols", ())
    if not isinstance(records, (list, tuple)):
        raise WorldError("PackageMalformedSymbols")
    # SemanticWorld's resolver is the authoritative compiled symbol index.  Do
    # not accept symbols appended to a stale/hand-edited payload after build.
    known = getattr(world, "_symbols", {})
    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for item in records:
        if not isinstance(item, Mapping) or not _nonempty_text(item.get("symbol_id")):
            raise WorldError("PackageMalformedSymbols")
        symbol_id = str(item["symbol_id"])
        if symbol_id in seen:
            raise WorldError("PackageDuplicateSymbol")
        seen.add(symbol_id)
        if symbol_id not in known:
            continue
        # Identity and revision records must remain the compiled record; a
        # copied or unresolved record is not a reusable package candidate.
        if known[symbol_id] is not item:
            if dict(known[symbol_id]) != dict(item):
                continue
        result.append(item)
    return tuple(result)


def _module_symbol_ids(module: Mapping[str, Any]) -> set[str]:
    values = module.get("symbols", ())
    if not isinstance(values, (list, tuple)):
        return set()
    result: set[str] = set()
    for value in values:
        if isinstance(value, Mapping):
            value = value.get("symbol_id")
        if isinstance(value, str) and value:
            result.add(value)
    return result


def _graph_edges(world: SemanticWorld, modules: Mapping[str, Mapping[str, Any]]) -> dict[str, tuple[str, ...]]:
    edges: dict[str, set[str]] = {name: set() for name in modules}
    for name, module in modules.items():
        imports = module.get("imports", ())
        if isinstance(imports, (list, tuple)):
            edges[name].update(str(item) for item in imports if str(item) in modules)
    raw = world.data.get("module_dependencies", ())
    if isinstance(raw, Mapping):
        raw = raw.items()
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, Mapping):
                owner = item.get("module", item.get("owner"))
                imports = item.get("imports", item.get("dependencies", ()))
                if isinstance(owner, str) and owner in edges and isinstance(imports, (list, tuple)):
                    edges[owner].update(str(dep) for dep in imports if str(dep) in modules)
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                owner, imports = item
                if isinstance(owner, str) and owner in edges and isinstance(imports, (list, tuple)):
                    edges[owner].update(str(dep) for dep in imports if str(dep) in modules)
    return {name: tuple(sorted(values)) for name, values in edges.items()}


def _module_distances(start: str, edges: Mapping[str, tuple[str, ...]]) -> dict[str, int]:
    distances = {start: 0}
    queue: deque[str] = deque([start])
    while queue:
        current = queue.popleft()
        for child in edges.get(current, ()):
            if child not in distances:
                distances[child] = distances[current] + 1
                queue.append(child)
    return distances


def _package_graph(world: SemanticWorld) -> tuple[dict[str, tuple[str, ...]], dict[str, Mapping[str, Any]]]:
    raw_packages = world.data.get("packages", ())
    package_records: dict[str, Mapping[str, Any]] = {}
    graph: dict[str, tuple[str, ...]] = {}
    if isinstance(raw_packages, Mapping):
        # Some serialized worlds store {name: record}; lockfiles store a list.
        package_values = raw_packages.get("packages", ())
        if isinstance(raw_packages.get("graph"), Mapping):
            graph = {
                str(name): tuple(sorted(str(dep) for dep in deps if isinstance(deps, (list, tuple))))
                for name, deps in raw_packages["graph"].items()
            }
        raw_packages = package_values if isinstance(package_values, (list, tuple)) else ()
    if isinstance(raw_packages, (list, tuple)):
        for item in raw_packages:
            if not isinstance(item, Mapping) or not _nonempty_text(item.get("name")):
                continue
            package_records[str(item["name"])] = item
            dependencies = item.get("dependencies", item.get("imports", ()))
            if isinstance(dependencies, Mapping):
                dependencies = dependencies.keys()
            if isinstance(dependencies, (list, tuple, set)):
                graph.setdefault(str(item["name"]), tuple(sorted(str(dep) for dep in dependencies)))
    for key in ("package_graph", "dependency_graph"):
        raw_graph = world.data.get(key)
        if isinstance(raw_graph, Mapping):
            graph.update({str(name): tuple(sorted(str(dep) for dep in deps if isinstance(deps, (list, tuple)))) for name, deps in raw_graph.items()})
    lock_path = world.data.get("lockfile_path")
    if not graph and isinstance(lock_path, str) and Path(lock_path).is_file():
        try:
            raw = json.loads(Path(lock_path).read_text(encoding="utf-8"))
            lock_graph = raw.get("graph", {})
            if isinstance(lock_graph, Mapping):
                graph = {
                    str(name): tuple(sorted(str(dep) for dep in deps if isinstance(deps, (list, tuple))))
                    for name, deps in lock_graph.items()
                }
            for item in raw.get("packages", ()):
                if isinstance(item, Mapping) and _nonempty_text(item.get("name")):
                    package_records.setdefault(str(item["name"]), item)
        except (OSError, TypeError, ValueError):
            pass
    for name in set(package_records) | set(graph):
        graph.setdefault(name, ())
    return graph, package_records


def _package_name(symbol: Mapping[str, Any], module: Mapping[str, Any]) -> str:
    for item in (symbol, module):
        for key in ("package", "package_name", "owner_package"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _package_distances(start: str, graph: Mapping[str, tuple[str, ...]]) -> dict[str, int]:
    if not start:
        return {}
    return _module_distances(start, graph)


def _pure_zero_arg_return(
    symbol: Mapping[str, Any],
    expected: str,
) -> bool:
    kind = str(
        symbol.get("kind", "")
    ).casefold()
    if kind not in _FUNCTION_KINDS:
        return False
    if (
        symbol.get("stale") is True
        or symbol.get("resolved") is False
        or str(
            symbol.get("resolution", "exact")
        )
        not in {"", "exact", "resolved"}
        or symbol.get("effectful") is True
        or symbol.get("pure") is False
    ):
        return False
    for key in (
        "effects",
        "capabilities",
        "requirements",
        "resources",
    ):
        if symbol.get(key, ()):
            return False
    parameters = symbol.get("parameters")
    return_type = symbol.get("return_type")
    if parameters is not None:
        if (
            not isinstance(
                parameters,
                (list, tuple),
            )
            or parameters
        ):
            return False
    signature = symbol.get("signature")
    if isinstance(signature, str):
        match = _SIGNATURE.match(
            signature.strip()
        )
        if match is not None:
            if match.group(
                "parameters"
            ).strip():
                return False
            return_type = (
                return_type
                or match.group("return")
            )
    if (
        not isinstance(return_type, str)
        or not return_type.strip()
    ):
        return False
    return (
        return_type.strip().removesuffix(":")
        == expected
    )


def _accessible(target: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    if candidate.get("module") == target.get("module"):
        return True
    return candidate.get("exported") is True or candidate.get("public") is True


def _candidate_expression(target: Mapping[str, Any], candidate: Mapping[str, Any]) -> str:
    explicit = candidate.get("expression")
    if isinstance(explicit, str) and explicit:
        return (
            explicit
            if explicit.endswith("()")
            else f"{explicit}()"
        )
    return f"{candidate['name']}()"


def _locality(target: Mapping[str, Any], candidate: Mapping[str, Any], package_distance: int) -> int:
    if candidate.get("module") == target.get("module"):
        return 0
    if package_distance == 0:
        return 1
    return 2


def search_package_candidates(
    world: SemanticWorld,
    request: SynthesisRequest | Mapping[str, Any],
) -> tuple[SynthesisCandidate, ...]:
    """Propose pure zero-argument fills from the world's compiled graph.

    This producer only reads world state and returns preview ChangeIR objects;
    package resolution and source edits are deliberately outside this API.
    """
    if not isinstance(world, SemanticWorld):
        raise WorldError("SynthesisWorldMismatch")
    active = _request(request)
    hole_id, maximum = _validate_request(active)
    world.require_fresh()
    if active.world_digest != world.digest:
        raise StaleWorldError("StaleWorld: synthesis request belongs to another world")
    target = world.resolve(active.target)
    hole = _target_hole(world, active.target, hole_id)
    _capsule_hole(world, active.target, hole_id, hole)
    expected_type = hole.get("expected_type")
    if not isinstance(expected_type, str) or not expected_type:
        raise WorldError("PackageMalformedHole")

    modules = _module_records(world)
    target_module = str(target.get("module", ""))
    if target_module not in modules:
        raise WorldError("PackageUnknownTargetModule")
    module_edges = _graph_edges(world, modules)
    reachable_modules = _module_distances(target_module, module_edges)
    package_graph, package_records = _package_graph(world)
    target_package = _package_name(target, modules[target_module])
    package_distances = _package_distances(target_package, package_graph)
    if target_package and target_package not in package_distances:
        package_distances[target_package] = 0

    symbols = _symbol_records(world)
    candidates: list[tuple[int, int, str, str, Mapping[str, Any], str, str]] = []
    seen_expressions: set[str] = set()
    for candidate in symbols:
        module_name = candidate.get("module")
        if not isinstance(module_name, str) or module_name not in reachable_modules:
            continue
        if str(candidate.get("symbol_id")) == str(target.get("symbol_id")):
            continue
        module = modules[module_name]
        if str(candidate.get("symbol_id")) not in _module_symbol_ids(module):
            continue
        if not _accessible(target, candidate) or not _pure_zero_arg_return(candidate, expected_type):
            continue
        package = _package_name(candidate, module)
        distance = package_distances.get(package, 0 if package == target_package else reachable_modules[module_name])
        locality = _locality(target, candidate, distance)
        qualified = candidate.get("qualified_name")
        if not isinstance(qualified, str) or not qualified:
            qualified = f"{module_name}.{candidate['name']}"
        expression = _candidate_expression(target, candidate)
        if expression in seen_expressions:
            continue
        seen_expressions.add(expression)
        candidates.append((locality, distance, qualified, expression, candidate, package, module_name))

    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    result: list[SynthesisCandidate] = []
    lock_digest = world.data.get("lockfile_sha256")
    for locality, distance, qualified, expression, candidate, package, _module_name in candidates[:maximum]:
        change = preview_fill_hole(world, active.target, hole_id, expression)
        package_record = package_records.get(package, {})
        package_revision = package_record.get("source_hash", package_record.get("version", ""))
        provenance = {
            "algorithm": "compiled_package_graph_search",
            "expression": expression,
            "symbol_id": candidate["symbol_id"],
            "qualified_name": qualified,
            "revision_id": candidate["revision_id"],
            "interface_revision_id": candidate.get("interface_revision_id", ""),
            "implementation_revision_id": candidate.get("implementation_revision_id", ""),
            "package": package,
            "package_revision": package_revision,
            "lock_digest": lock_digest,
            "lockfile_sha256": lock_digest,
            "package_distance": distance,
            "locality": locality,
            "max_candidates": maximum,
        }
        result.append(
            build_synthesis_candidate(
                world,
                active,
                change,
                producer="package",
                producer_revision=PACKAGE_PRODUCER_REVISION,
                rank=CandidateRank(locality, distance, qualified),
                provenance=provenance,
            )
        )
    return tuple(result)


__all__ = ["PACKAGE_PRODUCER_REVISION", "search_package_candidates"]
