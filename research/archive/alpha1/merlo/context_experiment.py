"""Stage 0.4E effect-blind versus typed-effect context experiment."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from research.archive.historical_protocol.merlo.analyzer import scan_python
from research.archive.historical_protocol.merlo.frontend_evaluator import RecordValue, ReferenceEvaluator
from research.archive.historical_protocol.merlo.frontend_semantics import FrontendCompilation, compile_frontend
from research.archive.alpha1.merlo.maximal_python import (
    MaximalPythonManifest,
    MaximalPythonPackageManifest,
    MaximalPythonReport,
    analyze_maximal_python,
)
from research.archive.historical_protocol.merlo.model import Entity, ProgramIR
from research.archive.historical_protocol.merlo.stage04e_protocol import assert_stage04e_protocol


EFFECT_CONTEXT_SCHEMA_VERSION = 1
EFFECT_CONTEXT_REPETITIONS = 12
_EFFECT_SPECS = (
    ("payment", "payments.charge", "Payments", "charge", "payments", "pay"),
    (
        "inventory",
        "inventory.reserve",
        "Inventory",
        "reserve",
        "inventory",
        "reserve_stock",
    ),
    ("notification", "mail.send", "Mail", "send", "mail", "notify"),
    ("audit", "audit.write", "Audit", "write", "audit", "write_audit"),
    ("cache", "cache.write", "Cache", "write", "cache", "cache_order"),
    ("metrics", "metrics.emit", "Metrics", "emit", "metrics", "measure"),
)
_TOKEN_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|==|!=|<=|>=|->|::|\|>|[^\s]"
)
_DATA_KINDS = frozenset({"record", "enum", "newtype", "class"})


@dataclass(frozen=True)
class EffectContextCase:
    id: str
    variant: int
    category: str
    selected_effect: str
    capability: str
    method: str
    parameter: str
    branch: str
    packages: tuple[str, str, str, str]
    meldra_before: tuple[tuple[str, str], ...]
    meldra_after: tuple[tuple[str, str], ...]
    python_before: tuple[tuple[str, str], ...]
    python_after: tuple[tuple[str, str], ...]
    python_manifest: MaximalPythonManifest
    provenance: str = "generated-stage04e-effect-context-template"

    @property
    def domain_package(self) -> str:
        return self.packages[0]

    @property
    def infra_package(self) -> str:
        return self.packages[1]

    @property
    def flow_package(self) -> str:
        return self.packages[2]

    @property
    def api_package(self) -> str:
        return self.packages[3]

    @property
    def python_root(self) -> str:
        return f"{self.flow_package}.main.orchestrate"

    @property
    def meldra_root(self) -> str:
        return f"{self.flow_package}.main.orchestrate"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "variant": self.variant,
            "category": self.category,
            "selected_effect": self.selected_effect,
            "packages": list(self.packages),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class ContextObservation:
    case_id: str
    category: str
    arm: str
    strategy: str
    selected_effect: str
    selected_symbols: tuple[str, ...]
    required_symbols: tuple[str, ...]
    context_tokens: int
    verified: bool
    first_pass: bool
    infrastructure_failure: str | None = None

    @property
    def missing_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.required_symbols) - set(self.selected_symbols)))

    @property
    def unnecessary_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.selected_symbols) - set(self.required_symbols)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "arm": self.arm,
            "strategy": self.strategy,
            "selected_effect": self.selected_effect,
            "selected_symbols": list(self.selected_symbols),
            "required_symbols": list(self.required_symbols),
            "context_symbols": len(self.selected_symbols),
            "context_tokens": self.context_tokens,
            "missing_symbols": list(self.missing_symbols),
            "missing_context_requests": len(self.missing_symbols),
            "unnecessary_symbols": list(self.unnecessary_symbols),
            "unnecessary_context_ratio": _ratio(
                len(self.unnecessary_symbols), len(self.selected_symbols)
            ),
            "verified": self.verified,
            "first_pass": self.first_pass,
            "infrastructure_failure": self.infrastructure_failure,
        }


@dataclass(frozen=True)
class ContextArmReport:
    arm: str
    strategy: str
    observations: tuple[ContextObservation, ...]

    def to_dict(self) -> dict[str, Any]:
        context_tokens = sum(item.context_tokens for item in self.observations)
        context_symbols = sum(
            len(item.selected_symbols) for item in self.observations
        )
        unnecessary = sum(
            len(item.unnecessary_symbols) for item in self.observations
        )
        missing = sum(len(item.missing_symbols) for item in self.observations)
        verified = sum(item.verified for item in self.observations)
        first_pass = sum(item.first_pass for item in self.observations)
        return {
            "arm": self.arm,
            "strategy": self.strategy,
            "tasks": len(self.observations),
            "verified_changes": verified,
            "agent_task_success": _ratio(verified, len(self.observations)),
            "first_pass_success": _ratio(first_pass, len(self.observations)),
            "context_symbols": context_symbols,
            "context_tokens": context_tokens,
            "missing_context_requests": missing,
            "unnecessary_context_ratio": _ratio(unnecessary, context_symbols),
            "verified_changes_per_1000_context_tokens": round(
                verified * 1000 / context_tokens, 6
            )
            if context_tokens
            else 0.0,
            "infrastructure_failures": sum(
                item.infrastructure_failure is not None
                for item in self.observations
            ),
            "observations": [item.to_dict() for item in self.observations],
        }


@dataclass(frozen=True)
class EffectContextReport:
    cases: tuple[EffectContextCase, ...]
    current_python: ContextArmReport
    maximal_python: ContextArmReport
    meldra: ContextArmReport
    protocol_sha256: str
    schema_version: int = EFFECT_CONTEXT_SCHEMA_VERSION
    evidence_level: str = "GENERATED_PILOT_NOT_EXTERNAL_EVIDENCE"

    def to_dict(self) -> dict[str, Any]:
        arms = {
            self.current_python.arm: self.current_python.to_dict(),
            self.maximal_python.arm: self.maximal_python.to_dict(),
            self.meldra.arm: self.meldra.to_dict(),
        }
        baseline = arms["current-python-sidecar"]["context_tokens"]
        for name in ("maximal-python-profile", "meldra-closed"):
            candidate = arms[name]["context_tokens"]
            arms[name]["context_token_reduction_vs_effect_blind"] = round(
                1.0 - candidate / baseline, 6
            )
        arms["current-python-sidecar"][
            "context_token_reduction_vs_effect_blind"
        ] = 0.0
        return {
            "schema_version": self.schema_version,
            "evidence_level": self.evidence_level,
            "protocol_sha256": self.protocol_sha256,
            "statistical_units": {
                "tasks": len(self.cases),
                "program_templates": 1,
                "effect_categories": len(_EFFECT_SPECS),
                "generated_repetitions_per_category": EFFECT_CONTEXT_REPETITIONS,
                "independent_programs": 0,
                "independent_authors": 0,
                "primary_external_gate_status": "UNMEASURED",
            },
            "cases": [item.to_dict() for item in self.cases],
            "arms": arms,
            "decision": "NO_GO_LANGUAGE_ALPHA",
            "note": (
                "Context oracles are generated from six frozen semantic roles over "
                "one repeated program graph; no model or external task was measured."
            ),
        }


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 6)


def _package_names(variant: int) -> tuple[str, str, str, str]:
    suffix = f"{variant:02d}"
    return (
        f"ctxdomain{suffix}",
        f"ctxinfra{suffix}",
        f"ctxflow{suffix}",
        f"ctxapi{suffix}",
    )


def _meldra_sources(
    variant: int, changed_effect: str | None
) -> dict[str, str]:
    domain, infra, flow, api = _package_names(variant)
    capability_exports = ", ".join(item[2] for item in _EFFECT_SPECS)
    capability_declarations = "".join(
        f"capability {capability}:\n"
        f"    {method}(amount: Int) -> Int uses {effect}\n"
        for _, effect, capability, method, _, _ in _EFFECT_SPECS
    )
    imports = ", ".join(item[2] for item in _EFFECT_SPECS)
    all_effect_lines = "".join(
        f"    uses {effect}\n" for _, effect, _, _, _, _ in _EFFECT_SPECS
    )
    parameters = ", ".join(
        ["order: Order"]
        + [
            f"{parameter}: cap {capability}"
            for _, _, capability, _, parameter, _ in _EFFECT_SPECS
        ]
    )
    arguments = ", ".join(
        ["order"] + [item[4] for item in _EFFECT_SPECS]
    )
    branches = []
    calls = []
    totals = []
    for index, (_, effect, capability, method, parameter, branch) in enumerate(
        _EFFECT_SPECS, 1
    ):
        addition = " + 1" if changed_effect == effect else ""
        branches.append(
            f"task {branch}(order: Order, {parameter}: cap {capability}) -> Int:\n"
            f"    uses {effect}\n"
            f"    {parameter}.{method}(order.amount){addition}\n"
        )
        local = f"step{index}"
        calls.append(f"    let {local} = {branch}(order, {parameter})\n")
        totals.append(local)
    flow_source = (
        f"package {flow}\nmodule main\n"
        f"use {domain}.model::{{Order}}\n"
        f"use {infra}.caps::{{{imports}}}\n"
        f"export {', '.join(item[5] for item in _EFFECT_SPECS)}, orchestrate\n"
        + "".join(branches)
        + f"task orchestrate({parameters}) -> Int:\n"
        + all_effect_lines
        + "".join(calls)
        + "    "
        + " + ".join(totals)
        + "\n"
    )
    return {
        f"{domain}/model.meldra": (
            f"package {domain}\nmodule model\nexport Order\n"
            "record Order:\n    amount: Int\n"
        ),
        f"{infra}/caps.meldra": (
            f"package {infra}\nmodule caps\nexport {capability_exports}\n"
            + capability_declarations
        ),
        f"{flow}/main.meldra": flow_source,
        f"{api}/main.meldra": (
            f"package {api}\nmodule main\n"
            f"use {domain}.model::{{Order}}\n"
            f"use {infra}.caps::{{{imports}}}\n"
            f"use {flow}.main::{{orchestrate}}\n"
            "export endpoint\n"
            f"task endpoint({parameters}) -> Int:\n"
            + all_effect_lines
            + f"    orchestrate({arguments})\n"
        ),
    }


def _python_sources(
    variant: int, changed_effect: str | None
) -> tuple[dict[str, str], MaximalPythonManifest]:
    domain, infra, flow, api = _package_names(variant)
    capability_classes = "".join(
        f"class {capability}:\n"
        f"    def {method}(self, amount: int) -> int:\n"
        f"        return amount\n\n"
        for _, _, capability, method, _, _ in _EFFECT_SPECS
    )
    imports = ", ".join(item[2] for item in _EFFECT_SPECS)
    parameters = ", ".join(
        ["order: Order"]
        + [
            f"{parameter}: {capability}"
            for _, _, capability, _, parameter, _ in _EFFECT_SPECS
        ]
    )
    arguments = ", ".join(
        ["order"] + [item[4] for item in _EFFECT_SPECS]
    )
    branches = []
    calls = []
    totals = []
    function_effects: dict[str, tuple[str, ...]] = {}
    function_capabilities: dict[str, tuple[str, ...]] = {}
    all_effects = tuple(item[1] for item in _EFFECT_SPECS)
    for index, (_, effect, capability, method, parameter, branch) in enumerate(
        _EFFECT_SPECS, 1
    ):
        addition = " + 1" if changed_effect == effect else ""
        branches.append(
            f"def {branch}(order: Order, {parameter}: {capability}) -> int:\n"
            f"    return {parameter}.{method}(order.amount){addition}\n\n"
        )
        local = f"step{index}"
        calls.append(f"    {local} = {branch}(order, {parameter})\n")
        totals.append(local)
        locator = f"{flow}.main.{branch}"
        function_effects[locator] = (effect,)
        function_capabilities[locator] = (effect,)
    root_locator = f"{flow}.main.orchestrate"
    endpoint_locator = f"{api}.main.endpoint"
    function_effects[root_locator] = all_effects
    function_effects[endpoint_locator] = all_effects
    function_capabilities[root_locator] = all_effects
    function_capabilities[endpoint_locator] = all_effects
    sources = {
        f"{domain}/model.py": (
            "class Order:\n"
            "    amount: int\n\n"
            "    def __init__(self, amount: int) -> None:\n"
            "        self.amount = amount\n"
        ),
        f"{infra}/caps.py": capability_classes,
        f"{flow}/main.py": (
            f"from {domain}.model import Order\n"
            f"from {infra}.caps import {imports}\n\n"
            + "".join(branches)
            + f"def orchestrate({parameters}) -> int:\n"
            + "".join(calls)
            + "    return "
            + " + ".join(totals)
            + "\n"
        ),
        f"{api}/main.py": (
            f"from {domain}.model import Order\n"
            f"from {infra}.caps import {imports}\n"
            f"from {flow}.main import orchestrate\n\n"
            f"def endpoint({parameters}) -> int:\n"
            f"    return orchestrate({arguments})\n"
        ),
    }
    exports = {
        domain: (f"{domain}.model.Order",),
        infra: tuple(f"{infra}.caps.{item[2]}" for item in _EFFECT_SPECS),
        flow: tuple(
            [f"{flow}.main.{item[5]}" for item in _EFFECT_SPECS]
            + [root_locator]
        ),
        api: (endpoint_locator,),
    }
    bindings = tuple(
        (f"{parameter}.{method}", effect)
        for _, effect, _, method, parameter, _ in _EFFECT_SPECS
    )
    packages = tuple(
        MaximalPythonPackageManifest(
            name,
            name,
            exports[name],
            effect_bindings=bindings,
            function_effects=tuple(function_effects.items()),
            function_capabilities=tuple(function_capabilities.items()),
        )
        for name in (domain, infra, flow, api)
    )
    return sources, MaximalPythonManifest(packages)


def generate_effect_context_cases() -> tuple[EffectContextCase, ...]:
    cases = []
    for variant in range(EFFECT_CONTEXT_REPETITIONS):
        meldra_before = _meldra_sources(variant, None)
        python_before, manifest = _python_sources(variant, None)
        for category, effect, capability, method, parameter, branch in _EFFECT_SPECS:
            python_after, _ = _python_sources(variant, effect)
            cases.append(
                EffectContextCase(
                    f"effect-context:{variant:02d}:{category}",
                    variant,
                    category,
                    effect,
                    capability,
                    method,
                    parameter,
                    branch,
                    _package_names(variant),
                    tuple(sorted(meldra_before.items())),
                    tuple(sorted(_meldra_sources(variant, effect).items())),
                    tuple(sorted(python_before.items())),
                    tuple(sorted(python_after.items())),
                    manifest,
                )
            )
    return tuple(cases)


def _token_count(source: str) -> int:
    return len(_TOKEN_RE.findall(source))


def _source_for_span(source: str, entity: Entity) -> str:
    span = entity.source_span or entity.definition_span
    lines = source.splitlines(keepends=True)
    start_line = max(0, span.start.line - 1)
    end_line = max(start_line, span.end.line - 1)
    if start_line >= len(lines):
        return ""
    if start_line == end_line:
        return lines[start_line][span.start.column : span.end.column]
    selected = [lines[start_line][span.start.column :]]
    selected.extend(lines[start_line + 1 : end_line])
    if end_line < len(lines):
        selected.append(lines[end_line][: span.end.column])
    return "".join(selected)


def _python_top_level(
    program: ProgramIR,
) -> tuple[
    dict[str, Entity],
    dict[str, str],
    dict[str, set[str]],
    dict[str, str],
]:
    top_entities = {
        item.fqname: item
        for item in program.entities
        if "." not in item.qualname
    }
    id_to_top: dict[str, str] = {}
    for item in program.entities:
        top_name = item.qualname.split(".", 1)[0]
        locator = f"{item.module}.{top_name}" if item.module else top_name
        if locator in top_entities:
            id_to_top[item.id] = locator
    graph: dict[str, set[str]] = {
        locator: set() for locator in top_entities
    }
    for reference in program.references:
        owner = id_to_top.get(reference.owner_id or "")
        target = id_to_top.get(reference.target_id or "")
        if owner and target and owner != target:
            graph[owner].add(target)
            graph[target].add(owner)
    for edge in program.calls:
        owner = id_to_top.get(edge.source_id or "")
        target = id_to_top.get(edge.target_id or "")
        if owner and target and owner != target:
            graph[owner].add(target)
            graph[target].add(owner)
    kinds = {locator: item.kind for locator, item in top_entities.items()}
    return top_entities, id_to_top, graph, kinds


def _add_python_annotation_edges(
    sources: Mapping[str, str],
    nodes: Mapping[str, Entity],
    graph: dict[str, set[str]],
) -> None:
    for path, source in sources.items():
        module = path.replace("\\", "/").removesuffix(".py").replace("/", ".")
        tree = ast.parse(source, filename=path)
        imports: dict[str, str] = {}
        for statement in tree.body:
            if isinstance(statement, ast.ImportFrom) and statement.module:
                for alias in statement.names:
                    imports[alias.asname or alias.name] = (
                        f"{statement.module}.{alias.name}"
                    )
        for statement in tree.body:
            if not isinstance(
                statement, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            owner = f"{module}.{statement.name}"
            if owner not in nodes:
                continue
            annotations = [
                item.annotation
                for item in (
                    *statement.args.posonlyargs,
                    *statement.args.args,
                    *statement.args.kwonlyargs,
                )
                if item.annotation is not None
            ]
            if statement.returns is not None:
                annotations.append(statement.returns)
            for annotation in annotations:
                for name in (
                    item.id
                    for item in ast.walk(annotation)
                    if isinstance(item, ast.Name)
                ):
                    target = imports.get(name, f"{module}.{name}")
                    if target in nodes and target != owner:
                        graph[owner].add(target)
                        graph[target].add(owner)


def _meldra_top_level(
    compilation: FrontendCompilation,
) -> tuple[dict[str, Any], dict[str, set[str]], dict[str, str]]:
    symbols = {item.symbol_id: item for item in compilation.hir.symbols}

    def top(symbol_id: str) -> str | None:
        symbol = symbols.get(symbol_id)
        if symbol is None:
            return None
        while symbol.parent_symbol_id is not None:
            parent = symbols.get(symbol.parent_symbol_id)
            if parent is None:
                break
            symbol = parent
        return symbol.locator

    nodes = {
        item.locator: item
        for item in compilation.hir.symbols
        if item.parent_symbol_id is None
    }
    graph: dict[str, set[str]] = {locator: set() for locator in nodes}
    for reference in compilation.hir.references:
        owner = top(reference.owner_symbol_id)
        target = top(reference.target_symbol_id or "")
        if owner and target and owner != target:
            graph[owner].add(target)
            graph[target].add(owner)
    kinds = {locator: item.kind for locator, item in nodes.items()}
    return nodes, graph, kinds


def effect_blind_context_closure(
    graph: Mapping[str, set[str]], root: str
) -> tuple[str, ...]:
    selected = {root}
    queue = deque([root])
    while queue:
        current = queue.popleft()
        for candidate in sorted(graph.get(current, ())):
            if candidate not in selected:
                selected.add(candidate)
                queue.append(candidate)
    return tuple(sorted(selected))


def effect_aware_context_closure(
    graph: Mapping[str, set[str]],
    root: str,
    selected_effect: str,
    effects: Mapping[str, tuple[str, ...]],
    kinds: Mapping[str, str],
) -> tuple[str, ...]:
    selected = {root}
    queue = deque([root])
    while queue:
        current = queue.popleft()
        for candidate in sorted(graph.get(current, ())):
            if candidate in selected:
                continue
            candidate_effects = set(effects.get(candidate, ()))
            if candidate_effects and selected_effect not in candidate_effects:
                continue
            if not candidate_effects and kinds.get(candidate) not in _DATA_KINDS:
                continue
            selected.add(candidate)
            queue.append(candidate)
    return tuple(sorted(selected))


def _python_required(case: EffectContextCase) -> tuple[str, ...]:
    return tuple(
        sorted(
            (
                f"{case.domain_package}.model.Order",
                f"{case.infra_package}.caps.{case.capability}",
                f"{case.flow_package}.main.{case.branch}",
                case.python_root,
                f"{case.api_package}.main.endpoint",
            )
        )
    )


def _meldra_required(case: EffectContextCase) -> tuple[str, ...]:
    return _python_required(case)


def _python_effects(
    case: EffectContextCase,
    report: MaximalPythonReport,
    nodes: Mapping[str, Entity],
) -> dict[str, tuple[str, ...]]:
    effects = {item.locator: item.effects for item in report.symbols}
    for _, effect, capability, _, _, _ in _EFFECT_SPECS:
        effects[f"{case.infra_package}.caps.{capability}"] = (effect,)
    return {locator: effects.get(locator, ()) for locator in nodes}


def _python_context_tokens(
    selected: tuple[str, ...],
    nodes: Mapping[str, Entity],
    sources: Mapping[str, str],
) -> int:
    return sum(
        _token_count(_source_for_span(sources[nodes[item].file], nodes[item]))
        for item in selected
    )


def _meldra_context_tokens(
    selected: tuple[str, ...],
    nodes: Mapping[str, Any],
    sources: Mapping[str, str],
) -> int:
    total = 0
    for item in selected:
        symbol = nodes[item]
        source = sources[symbol.path].encode("utf-8")
        total += _token_count(
            source[symbol.span.start : symbol.span.end].decode("utf-8")
        )
    return total


def _run_python_acceptance(case: EffectContextCase) -> bool:
    sources = dict(case.python_after)
    with tempfile.TemporaryDirectory(prefix="meldra-effect-context-") as temporary:
        root = Path(temporary)
        for relative_path, source in sources.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
            (path.parent / "__init__.py").touch()
        script = (
            "import sys; "
            f"sys.path.insert(0, {str(root)!r}); "
            f"from {case.domain_package}.model import Order; "
            f"from {case.infra_package}.caps import "
            + ", ".join(item[2] for item in _EFFECT_SPECS)
            + "; "
            f"from {case.api_package}.main import endpoint; "
            "print(endpoint(Order(1), "
            + ", ".join(f"{item[2]}()" for item in _EFFECT_SPECS)
            + "))"
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-c", script],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    return completed.returncode == 0 and completed.stdout.strip() == "7"


def _verify_meldra_change(
    case: EffectContextCase,
    before: FrontendCompilation,
    after: FrontendCompilation,
) -> bool:
    before_interfaces = {
        package: interface
        for package, interface, _ in before.hir.package_revisions
    }
    after_interfaces = {
        package: interface
        for package, interface, _ in after.hir.package_revisions
    }
    if before_interfaces != after_interfaces:
        return False
    branch = f"{case.flow_package}.main.{case.branch}"
    if before.hir.symbol(branch).revision_id == after.hir.symbol(branch).revision_id:
        return False
    handlers = {
        effect: (lambda amount: amount)
        for _, effect, _, _, _, _ in _EFFECT_SPECS
    }
    evaluator = ReferenceEvaluator(after, handlers=handlers)
    order_symbol = after.hir.symbol(f"{case.domain_package}.model.Order")
    arguments: dict[str, Any] = {
        "order": RecordValue(order_symbol.symbol_id, (("amount", 1),))
    }
    for _, _, capability, _, parameter, _ in _EFFECT_SPECS:
        arguments[parameter] = evaluator.capability(
            f"{case.infra_package}.caps.{capability}"
        )
    result = evaluator.evaluate(
        f"{case.api_package}.main.endpoint", arguments
    )
    return result.value == 7 and len(result.effect_trace) == len(_EFFECT_SPECS)


def _interface_map(report: MaximalPythonReport) -> dict[str, str]:
    return {
        item.package: item.interface_revision_id for item in report.packages
    }


def run_effect_context_benchmark() -> EffectContextReport:
    protocol = assert_stage04e_protocol()
    cases = generate_effect_context_cases()
    current_observations = []
    maximal_observations = []
    meldra_observations = []
    for case in cases:
        python_before = dict(case.python_before)
        python_after = dict(case.python_after)
        with tempfile.TemporaryDirectory(
            prefix="meldra-effect-context-scan-"
        ) as temporary:
            root = Path(temporary)
            for relative_path, source in python_before.items():
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
            current_program = scan_python(root)
        python_nodes, _, python_graph, python_kinds = _python_top_level(
            current_program
        )
        _add_python_annotation_edges(
            python_before, python_nodes, python_graph
        )
        maximal_before = analyze_maximal_python(
            python_before, case.python_manifest
        )
        maximal_after = analyze_maximal_python(
            python_after, case.python_manifest
        )
        if not maximal_before.ok or not maximal_after.ok:
            codes = sorted(
                {
                    item.code
                    for report in (maximal_before, maximal_after)
                    for item in report.blocking_diagnostics
                }
            )
            raise RuntimeError(f"strict effect context fixture failed: {codes}")
        python_verified = (
            _interface_map(maximal_before) == _interface_map(maximal_after)
            and maximal_before.symbol(
                f"{case.flow_package}.main.{case.branch}"
            ).revision_id
            != maximal_after.symbol(
                f"{case.flow_package}.main.{case.branch}"
            ).revision_id
            and _run_python_acceptance(case)
        )
        python_required = _python_required(case)
        blind = effect_blind_context_closure(
            python_graph, case.python_root
        )
        python_effect_map = _python_effects(
            case, maximal_before, python_nodes
        )
        aware = effect_aware_context_closure(
            python_graph,
            case.python_root,
            case.selected_effect,
            python_effect_map,
            python_kinds,
        )
        current_observations.append(
            ContextObservation(
                case.id,
                case.category,
                "current-python-sidecar",
                "effect-blind-structural-closure",
                case.selected_effect,
                blind,
                python_required,
                _python_context_tokens(blind, python_nodes, python_before),
                python_verified and not (set(python_required) - set(blind)),
                python_verified and not (set(python_required) - set(blind)),
            )
        )
        maximal_observations.append(
            ContextObservation(
                case.id,
                case.category,
                "maximal-python-profile",
                "declared-effect-aware-closure",
                case.selected_effect,
                aware,
                python_required,
                _python_context_tokens(aware, python_nodes, python_before),
                python_verified and not (set(python_required) - set(aware)),
                python_verified and not (set(python_required) - set(aware)),
            )
        )

        meldra_before = compile_frontend(dict(case.meldra_before))
        meldra_after = compile_frontend(dict(case.meldra_after))
        meldra_nodes, meldra_graph, meldra_kinds = _meldra_top_level(
            meldra_before
        )
        meldra_effects = {
            locator: symbol.effects
            for locator, symbol in meldra_nodes.items()
        }
        meldra_selected = effect_aware_context_closure(
            meldra_graph,
            case.meldra_root,
            case.selected_effect,
            meldra_effects,
            meldra_kinds,
        )
        meldra_required = _meldra_required(case)
        meldra_verified = _verify_meldra_change(
            case, meldra_before, meldra_after
        )
        meldra_observations.append(
            ContextObservation(
                case.id,
                case.category,
                "meldra-closed",
                "typed-effect-aware-closure",
                case.selected_effect,
                meldra_selected,
                meldra_required,
                _meldra_context_tokens(
                    meldra_selected, meldra_nodes, dict(case.meldra_before)
                ),
                meldra_verified
                and not (set(meldra_required) - set(meldra_selected)),
                meldra_verified
                and not (set(meldra_required) - set(meldra_selected)),
            )
        )
    return EffectContextReport(
        cases,
        ContextArmReport(
            "current-python-sidecar",
            "effect-blind-structural-closure",
            tuple(current_observations),
        ),
        ContextArmReport(
            "maximal-python-profile",
            "declared-effect-aware-closure",
            tuple(maximal_observations),
        ),
        ContextArmReport(
            "meldra-closed",
            "typed-effect-aware-closure",
            tuple(meldra_observations),
        ),
        protocol.protocol_sha256,
    )


__all__ = [
    "EFFECT_CONTEXT_REPETITIONS",
    "EFFECT_CONTEXT_SCHEMA_VERSION",
    "ContextArmReport",
    "ContextObservation",
    "EffectContextCase",
    "EffectContextReport",
    "effect_aware_context_closure",
    "effect_blind_context_closure",
    "generate_effect_context_cases",
    "run_effect_context_benchmark",
]
