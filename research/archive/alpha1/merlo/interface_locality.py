"""Stage 0.4E three-arm package interface locality benchmark."""

from __future__ import annotations

import json
import statistics
import tempfile
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from research.archive.historical_protocol.merlo.analyzer import scan_python
from research.archive.historical_protocol.merlo.frontend_semantics import FrontendCompilation, compile_frontend
from research.archive.historical_protocol.merlo.impact import analyze_impact
from research.archive.alpha1.merlo.maximal_python import (
    MaximalPythonManifest,
    MaximalPythonPackageManifest,
    MaximalPythonReport,
    analyze_maximal_python,
)
from research.archive.historical_protocol.merlo.stage04e_protocol import assert_stage04e_protocol


INTERFACE_LOCALITY_SCHEMA_VERSION = 1
INTERFACE_LOCALITY_REPETITIONS = 12
INTERFACE_LOCALITY_CATEGORIES = (
    "private_body_edit",
    "private_rename",
    "private_type_replacement",
    "private_dependency_replacement",
    "public_signature_change",
    "public_return_type_change",
    "public_effect_widening",
    "public_capability_widening",
    "public_enum_variant_addition",
)
_PRIVATE_CATEGORIES = frozenset(INTERFACE_LOCALITY_CATEGORIES[:4])


@dataclass(frozen=True)
class LocalityCase:
    id: str
    variant: int
    category: str
    changed_package: str
    expected_invalidated_packages: tuple[str, ...]
    current_target: str
    meldra_before: tuple[tuple[str, str], ...]
    meldra_after: tuple[tuple[str, str], ...]
    python_before: tuple[tuple[str, str], ...]
    python_after: tuple[tuple[str, str], ...]
    python_manifest_before: MaximalPythonManifest
    python_manifest_after: MaximalPythonManifest
    provenance: str = "generated-stage04e-locality-template"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "variant": self.variant,
            "category": self.category,
            "changed_package": self.changed_package,
            "expected_invalidated_packages": list(
                self.expected_invalidated_packages
            ),
            "current_target": self.current_target,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class LocalityObservation:
    case_id: str
    category: str
    arm: str
    expected: tuple[str, ...]
    predicted: tuple[str, ...]
    changed_interfaces: tuple[str, ...]
    context_closure_size: int
    expected_context_closure_size: int
    evidence_recalculation_size: int
    status: str = "OBSERVED"

    @property
    def true_positive(self) -> int:
        return len(set(self.expected) & set(self.predicted))

    @property
    def false_positive(self) -> int:
        return len(set(self.predicted) - set(self.expected))

    @property
    def false_negative(self) -> int:
        return len(set(self.expected) - set(self.predicted))

    @property
    def exact(self) -> bool:
        return self.expected == self.predicted

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "arm": self.arm,
            "expected": list(self.expected),
            "predicted": list(self.predicted),
            "changed_interfaces": list(self.changed_interfaces),
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "exact": self.exact,
            "context_closure_size": self.context_closure_size,
            "expected_context_closure_size": self.expected_context_closure_size,
            "evidence_recalculation_size": self.evidence_recalculation_size,
            "status": self.status,
        }


@dataclass(frozen=True)
class LocalityArmReport:
    arm: str
    observations: tuple[LocalityObservation, ...]

    def to_dict(self) -> dict[str, Any]:
        true_positive = sum(item.true_positive for item in self.observations)
        false_positive = sum(item.false_positive for item in self.observations)
        false_negative = sum(item.false_negative for item in self.observations)
        precision_denominator = true_positive + false_positive
        recall_denominator = true_positive + false_negative
        categories: dict[str, dict[str, int]] = {}
        for category in INTERFACE_LOCALITY_CATEGORIES:
            values = tuple(
                item for item in self.observations if item.category == category
            )
            categories[category] = {
                "cases": len(values),
                "exact": sum(item.exact for item in values),
                "false_positive": sum(item.false_positive for item in values),
                "false_negative": sum(item.false_negative for item in values),
            }
        return {
            "arm": self.arm,
            "cases": len(self.observations),
            "exact_cases": sum(item.exact for item in self.observations),
            "invalidation_precision": _ratio(
                true_positive, precision_denominator
            ),
            "invalidation_recall": _ratio(true_positive, recall_denominator),
            "true_positive": true_positive,
            "unnecessary_invalidations": false_positive,
            "missed_invalidations": false_negative,
            "median_context_closure_size": statistics.median(
                item.context_closure_size for item in self.observations
            ),
            "median_expected_context_closure_size": statistics.median(
                item.expected_context_closure_size
                for item in self.observations
            ),
            "total_evidence_recalculation_size": sum(
                item.evidence_recalculation_size for item in self.observations
            ),
            "categories": categories,
            "observations": [item.to_dict() for item in self.observations],
        }


@dataclass(frozen=True)
class InterfaceLocalityReport:
    cases: tuple[LocalityCase, ...]
    current_python: LocalityArmReport
    maximal_python: LocalityArmReport
    meldra: LocalityArmReport
    protocol_sha256: str
    evidence_level: str = "GENERATED_PILOT_NOT_EXTERNAL_EVIDENCE"
    schema_version: int = INTERFACE_LOCALITY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_level": self.evidence_level,
            "protocol_sha256": self.protocol_sha256,
            "statistical_units": {
                "semantic_changes": len(self.cases),
                "program_templates": 1,
                "change_templates": len(INTERFACE_LOCALITY_CATEGORIES),
                "generated_repetitions_per_change_template": INTERFACE_LOCALITY_REPETITIONS,
                "independent_programs": 0,
                "independent_authors": 0,
                "primary_external_gate_status": "UNMEASURED",
            },
            "categories": list(INTERFACE_LOCALITY_CATEGORIES),
            "cases": [item.to_dict() for item in self.cases],
            "arms": {
                self.current_python.arm: self.current_python.to_dict(),
                self.maximal_python.arm: self.maximal_python.to_dict(),
                self.meldra.arm: self.meldra.to_dict(),
            },
            "decision": "NO_GO_LANGUAGE_ALPHA",
            "note": (
                "The 108 changes are generated repetitions over one package-graph "
                "template; they validate harness behavior, not external locality."
            ),
        }


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 6)


def _names(variant: int) -> tuple[str, str, str]:
    return f"loc{variant:02d}", f"client{variant:02d}", f"app{variant:02d}"


def _change_state(category: str | None) -> dict[str, Any]:
    changed = category is not None
    return {
        "private_body": changed and category == "private_body_edit",
        "private_name": (
            "_calculate"
            if changed and category == "private_rename"
            else "_private_helper"
        ),
        "private_type": (
            "str"
            if changed and category == "private_type_replacement"
            else "int"
        ),
        "private_type_meldra": (
            "Text"
            if changed and category == "private_type_replacement"
            else "Int"
        ),
        "private_dependency": changed
        and category == "private_dependency_replacement",
        "public_signature": changed
        and category == "public_signature_change",
        "public_return": changed and category == "public_return_type_change",
        "effect_widening": changed and category == "public_effect_widening",
        "capability_widening": changed
        and category == "public_capability_widening",
        "enum_addition": changed
        and category == "public_enum_variant_addition",
    }


def _meldra_sources(variant: int, category: str | None) -> dict[str, str]:
    owner, client, app = _names(variant)
    state = _change_state(category)
    helper = state["private_name"]
    helper_dependency = "_fallback" if state["private_dependency"] else "_base"
    base_body = "value + 10" if state["private_body"] else "value"
    status_pending = "    Pending\n" if state["enum_addition"] else ""
    network_decl = (
        "capability Network:\n"
        "    post() -> Int uses network.post\n"
        if state["effect_widening"]
        else ""
    )
    audit_decl = (
        "capability Audit:\n"
        "    marker() -> Int uses audit.write\n"
        if state["capability_widening"]
        else ""
    )
    extra_export = (
        ", Network" if state["effect_widening"] else ""
    ) + (", Audit" if state["capability_widening"] else "")
    process_parameters = "item: Public, reader: cap Reader"
    call_arguments = "item, reader"
    effects = ["db.read"]
    if state["public_signature"]:
        process_parameters += ", mode: Int"
        call_arguments += ", 1"
    if state["effect_widening"]:
        process_parameters += ", network: cap Network"
        call_arguments += ", network"
        effects.append("network.post")
    if state["capability_widening"]:
        process_parameters += ", audit: cap Audit"
        call_arguments += ", audit"
    return_type = "Text" if state["public_return"] else "Int"
    process_lines = [
        *(f"    uses {effect}" for effect in effects),
        "    let stored = reader.read()",
    ]
    if state["effect_widening"]:
        process_lines.append("    let posted = network.post()")
    if state["public_return"]:
        process_lines.append('    "done"')
    else:
        expression = f"{helper}(item.value) + stored"
        if state["public_signature"]:
            expression += " + mode"
        if state["effect_widening"]:
            expression += " + posted"
        process_lines.append("    " + expression)

    client_parameters = "item: Public, reader: cap Reader"
    client_arguments = "item, reader"
    if state["public_signature"]:
        client_arguments += ", 1"
    client_effects = ["db.read"]
    client_imports = "Public, Status, Reader, process"
    if state["effect_widening"]:
        client_parameters += ", network: cap Network"
        client_arguments += ", network"
        client_effects.append("network.post")
        client_imports += ", Network"
    if state["capability_widening"]:
        client_parameters += ", audit: cap Audit"
        client_arguments += ", audit"
        client_imports += ", Audit"
    pending_arm = "        Pending: 3\n" if state["enum_addition"] else ""

    app_parameters = client_parameters
    app_arguments = "item, reader"
    app_imports = "Public, Status, Reader"
    if state["effect_widening"]:
        app_imports += ", Network"
        app_arguments += ", network"
    if state["capability_widening"]:
        app_imports += ", Audit"
        app_arguments += ", audit"

    owner_source = (
        f"package {owner}\nmodule api\n"
        f"export Public, Status, Reader, process{extra_export}\n"
        "record Public:\n    value: Int\n"
        "enum Status:\n    Ready\n    Failed\n"
        + status_pending
        + f"newtype _PrivateToken = {state['private_type_meldra']}\n"
        + "capability Reader:\n    read() -> Int uses db.read\n"
        + network_decl
        + audit_decl
        + f"fn _base(value: Int) -> Int:\n    {base_body}\n"
        + "fn _fallback(value: Int) -> Int:\n    value + 1\n"
        + f"fn {helper}(value: Int) -> Int:\n    {helper_dependency}(value)\n"
        + f"task process({process_parameters}) -> {return_type}:\n"
        + "\n".join(process_lines)
        + "\n"
    )
    client_source = (
        f"package {client}\nmodule main\n"
        f"use {owner}.api::{{{client_imports}}}\n"
        "export call, label\n"
        f"task call({client_parameters}) -> {return_type}:\n"
        + "".join(f"    uses {effect}\n" for effect in client_effects)
        + f"    process({client_arguments})\n"
        + "fn label(status: Status) -> Int:\n"
        + "    match status:\n"
        + "        Ready: 1\n"
        + "        Failed: 2\n"
        + pending_arm
    )
    app_source = (
        f"package {app}\nmodule main\n"
        f"use {owner}.api::{{{app_imports}}}\n"
        f"use {client}.main::{{call, label}}\n"
        "export run, show\n"
        f"task run({app_parameters}) -> {return_type}:\n"
        + "".join(f"    uses {effect}\n" for effect in client_effects)
        + f"    call({app_arguments})\n"
        + "fn show(status: Status) -> Int:\n"
        + "    label(status)\n"
    )
    return {
        f"{owner}/api.meldra": owner_source,
        f"{client}/main.meldra": client_source,
        f"{app}/main.meldra": app_source,
    }


def _python_sources_and_manifest(
    variant: int, category: str | None
) -> tuple[dict[str, str], MaximalPythonManifest]:
    owner, client, app = _names(variant)
    state = _change_state(category)
    helper = state["private_name"]
    dependency = "_fallback" if state["private_dependency"] else "_base"
    base_body = "value + 10" if state["private_body"] else "value"
    pending = "    Pending = 'pending'\n" if state["enum_addition"] else ""
    network_class = (
        "class Network:\n"
        "    def post(self) -> int:\n"
        "        return 1\n\n"
        if state["effect_widening"]
        else ""
    )
    audit_class = (
        "class Audit:\n"
        "    def marker(self) -> int:\n"
        "        return 1\n\n"
        if state["capability_widening"]
        else ""
    )
    process_parameters = "item: Public, reader: Reader"
    call_arguments = "item, reader"
    imports = "Public, Status, Reader, process"
    effects = ["db.read"]
    capabilities = ["db.read"]
    extra_exports: list[str] = []
    if state["public_signature"]:
        process_parameters += ", mode: int"
        call_arguments += ", 1"
    if state["effect_widening"]:
        process_parameters += ", network: Network"
        call_arguments += ", network"
        imports += ", Network"
        effects.append("network.post")
        capabilities.append("network.post")
        extra_exports.append(f"{owner}.api.Network")
    if state["capability_widening"]:
        process_parameters += ", audit: Audit"
        call_arguments += ", audit"
        imports += ", Audit"
        capabilities.append("audit.write")
        extra_exports.append(f"{owner}.api.Audit")
    return_type = "str" if state["public_return"] else "int"
    process_lines = ["    stored = reader.read()"]
    if state["effect_widening"]:
        process_lines.append("    posted = network.post()")
    if state["public_return"]:
        process_lines.append("    return 'done'")
    else:
        expression = f"{helper}(item.value) + stored"
        if state["public_signature"]:
            expression += " + mode"
        if state["effect_widening"]:
            expression += " + posted"
        process_lines.append("    return " + expression)

    client_parameters = "item: Public, reader: Reader"
    client_arguments = "item, reader"
    if state["public_signature"]:
        client_arguments += ", 1"
    client_imports = imports
    if state["effect_widening"]:
        client_parameters += ", network: Network"
        client_arguments += ", network"
    if state["capability_widening"]:
        client_parameters += ", audit: Audit"
        client_arguments += ", audit"
    app_parameters = client_parameters
    app_arguments = "item, reader"
    app_imports = "Public, Status, Reader"
    if state["effect_widening"]:
        app_imports += ", Network"
        app_arguments += ", network"
    if state["capability_widening"]:
        app_imports += ", Audit"
        app_arguments += ", audit"

    owner_source = (
        "class Public:\n    value: int\n\n"
        "class Status:\n    Ready = 'ready'\n    Failed = 'failed'\n"
        + pending
        + f"\nclass _PrivateToken:\n    value: {state['private_type']}\n\n"
        + "class Reader:\n"
        "    def read(self) -> int:\n"
        "        return 1\n\n"
        + network_class
        + audit_class
        + f"def _base(value: int) -> int:\n    return {base_body}\n\n"
        + "def _fallback(value: int) -> int:\n    return value + 1\n\n"
        + f"def {helper}(value: int) -> int:\n    return {dependency}(value)\n\n"
        + f"def process({process_parameters}) -> {return_type}:\n"
        + "\n".join(process_lines)
        + "\n"
    )
    client_pending = (
        "    if status == Status.Pending:\n        return 3\n"
        if state["enum_addition"]
        else ""
    )
    client_source = (
        f"from {owner}.api import {client_imports}\n\n"
        f"def call({client_parameters}) -> {return_type}:\n"
        f"    return process({client_arguments})\n\n"
        "def label(status: Status) -> int:\n"
        "    if status == Status.Ready:\n"
        "        return 1\n"
        + client_pending
        + "    return 2\n"
    )
    app_source = (
        f"from {owner}.api import {app_imports}\n"
        f"from {client}.main import call, label\n\n"
        f"def run({app_parameters}) -> {return_type}:\n"
        f"    return call({app_arguments})\n\n"
        "def show(status: Status) -> int:\n"
        "    return label(status)\n"
    )
    sources = {
        f"{owner}/api.py": owner_source,
        f"{client}/main.py": client_source,
        f"{app}/main.py": app_source,
    }
    owner_exports = [
        f"{owner}.api.Public",
        f"{owner}.api.Status",
        f"{owner}.api.Reader",
        f"{owner}.api.process",
        *extra_exports,
    ]
    process_locator = f"{owner}.api.process"
    call_locator = f"{client}.main.call"
    run_locator = f"{app}.main.run"
    function_effects = {
        process_locator: tuple(effects),
        call_locator: tuple(effects),
        run_locator: tuple(effects),
    }
    function_capabilities = {
        process_locator: tuple(capabilities),
        call_locator: tuple(capabilities),
        run_locator: tuple(capabilities),
    }
    packages = (
        MaximalPythonPackageManifest(
            owner,
            owner,
            tuple(owner_exports),
            effect_bindings=(
                ("reader.read", "db.read"),
                ("network.post", "network.post"),
            ),
            function_effects=tuple(function_effects.items()),
            function_capabilities=tuple(function_capabilities.items()),
        ),
        MaximalPythonPackageManifest(
            client,
            client,
            (call_locator, f"{client}.main.label"),
            function_effects=tuple(function_effects.items()),
            function_capabilities=tuple(function_capabilities.items()),
        ),
        MaximalPythonPackageManifest(
            app,
            app,
            (run_locator, f"{app}.main.show"),
            function_effects=tuple(function_effects.items()),
            function_capabilities=tuple(function_capabilities.items()),
        ),
    )
    return sources, MaximalPythonManifest(packages)


def _expected(variant: int, category: str) -> tuple[str, ...]:
    _, client, app = _names(variant)
    if category in _PRIVATE_CATEGORIES:
        return ()
    if category == "public_signature_change":
        return (client,)
    return tuple(sorted((client, app)))


def _current_target(variant: int, category: str) -> str:
    owner, _, _ = _names(variant)
    name = {
        "private_body_edit": "_base",
        "private_rename": "_private_helper",
        "private_type_replacement": "_PrivateToken",
        "private_dependency_replacement": "_private_helper",
        "public_signature_change": "process",
        "public_return_type_change": "process",
        "public_effect_widening": "process",
        "public_capability_widening": "process",
        "public_enum_variant_addition": "Status",
    }[category]
    return f"{owner}.api.{name}"


def generate_locality_cases() -> tuple[LocalityCase, ...]:
    cases = []
    for variant in range(INTERFACE_LOCALITY_REPETITIONS):
        owner, _, _ = _names(variant)
        meldra_before = _meldra_sources(variant, None)
        python_before, manifest_before = _python_sources_and_manifest(
            variant, None
        )
        for category in INTERFACE_LOCALITY_CATEGORIES:
            python_after, manifest_after = _python_sources_and_manifest(
                variant, category
            )
            cases.append(
                LocalityCase(
                    f"locality:{variant:02d}:{category}",
                    variant,
                    category,
                    owner,
                    _expected(variant, category),
                    _current_target(variant, category),
                    tuple(sorted(meldra_before.items())),
                    tuple(sorted(_meldra_sources(variant, category).items())),
                    tuple(sorted(python_before.items())),
                    tuple(sorted(python_after.items())),
                    manifest_before,
                    manifest_after,
                )
            )
    return tuple(cases)


def _meldra_contracts(
    compilation: FrontendCompilation,
) -> dict[str, tuple[str, str]]:
    return {
        symbol.locator: (
            symbol.package_name,
            json.dumps(
                {
                    "kind": symbol.kind,
                    "contract": symbol.contract,
                    "effects": symbol.effects,
                    "capabilities": symbol.capabilities,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        for symbol in compilation.hir.symbols
        if symbol.exported and symbol.parent_symbol_id is None
    }


def _meldra_consumers(
    before: FrontendCompilation, after: FrontendCompilation
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for compilation in (before, after):
        symbols = {item.symbol_id: item for item in compilation.hir.symbols}
        for reference in compilation.hir.references:
            target = symbols.get(reference.target_symbol_id or "")
            owner = symbols.get(reference.owner_symbol_id)
            if target is not None and owner is not None:
                result[target.locator].add(owner.package_name)
    return result


def _maximal_contracts(
    report: MaximalPythonReport,
) -> dict[str, tuple[str, str]]:
    return {
        symbol.locator: (
            symbol.package,
            json.dumps(symbol.contract(), sort_keys=True, separators=(",", ":")),
        )
        for symbol in report.symbols
        if symbol.exported
    }


def _maximal_consumers(
    before: MaximalPythonReport, after: MaximalPythonReport
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for report in (before, after):
        for reference in report.references:
            if reference.target_locator is None:
                continue
            module = reference.path.replace("\\", "/").removesuffix(".py").replace("/", ".")
            package = report.manifest.package_for_module(module)
            if package is not None:
                result[reference.target_locator].add(package.name)
    return result


def _changed_contracts(
    before: Mapping[str, tuple[str, str]],
    after: Mapping[str, tuple[str, str]],
) -> dict[str, set[str]]:
    changed: dict[str, set[str]] = defaultdict(set)
    for locator in set(before) | set(after):
        old = before.get(locator)
        new = after.get(locator)
        if old != new:
            package = (new or old)[0]
            changed[package].add(locator)
    return changed


def _propagate_interfaces(
    changed_package: str,
    before_contracts: Mapping[str, tuple[str, str]],
    after_contracts: Mapping[str, tuple[str, str]],
    consumers: Mapping[str, set[str]],
    changed_interfaces: set[str],
) -> tuple[str, ...]:
    changed = _changed_contracts(before_contracts, after_contracts)
    queue = deque(sorted(changed.get(changed_package, ())))
    visited_targets: set[str] = set()
    invalidated: set[str] = set()
    while queue:
        target = queue.popleft()
        if target in visited_targets:
            continue
        visited_targets.add(target)
        for package in sorted(consumers.get(target, ())):
            if package == changed_package:
                continue
            first = package not in invalidated
            invalidated.add(package)
            if first and package in changed_interfaces:
                queue.extend(sorted(changed.get(package, ())))
    return tuple(sorted(invalidated))


def _current_observation(case: LocalityCase) -> LocalityObservation:
    sources = dict(case.python_before)
    with tempfile.TemporaryDirectory(prefix="meldra-locality-current-") as temporary:
        root = Path(temporary)
        for relative_path, source in sources.items():
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        program = scan_python(root)
    target = program.entity(case.current_target)
    impact = analyze_impact(program, target.id)
    predicted = tuple(
        sorted(
            {
                Path(path).parts[0]
                for path in impact.affected_files
                if Path(path).parts and Path(path).parts[0] != case.changed_package
            }
        )
    )
    symbol_counts = Counter(
        Path(item.file).parts[0]
        for item in program.entities
        if Path(item.file).parts
    )
    return LocalityObservation(
        case.id,
        case.category,
        "current-python-sidecar",
        case.expected_invalidated_packages,
        predicted,
        (),
        sum(symbol_counts[item] for item in predicted),
        sum(symbol_counts[item] for item in case.expected_invalidated_packages),
        len(predicted),
    )


def _maximal_observation(case: LocalityCase) -> LocalityObservation:
    before = analyze_maximal_python(
        dict(case.python_before), case.python_manifest_before
    )
    after = analyze_maximal_python(
        dict(case.python_after), case.python_manifest_after
    )
    if not before.ok or not after.ok:
        codes = sorted(
            {
                item.code
                for report in (before, after)
                for item in report.blocking_diagnostics
            }
        )
        raise RuntimeError(f"maximal locality fixture failed strict profile: {codes}")
    before_interfaces = {
        item.package: item.interface_revision_id for item in before.packages
    }
    after_interfaces = {
        item.package: item.interface_revision_id for item in after.packages
    }
    changed_interfaces = {
        package
        for package in before_interfaces
        if before_interfaces[package] != after_interfaces[package]
    }
    predicted = _propagate_interfaces(
        case.changed_package,
        _maximal_contracts(before),
        _maximal_contracts(after),
        _maximal_consumers(before, after),
        changed_interfaces,
    )
    symbol_counts = Counter(item.package for item in before.symbols)
    return LocalityObservation(
        case.id,
        case.category,
        "maximal-python-profile",
        case.expected_invalidated_packages,
        predicted,
        tuple(sorted(changed_interfaces)),
        sum(symbol_counts[item] for item in predicted),
        sum(symbol_counts[item] for item in case.expected_invalidated_packages),
        len(predicted),
    )


def _meldra_observation(case: LocalityCase) -> LocalityObservation:
    before = compile_frontend(dict(case.meldra_before))
    after = compile_frontend(dict(case.meldra_after))
    before_interfaces = {
        package: interface
        for package, interface, _ in before.hir.package_revisions
    }
    after_interfaces = {
        package: interface
        for package, interface, _ in after.hir.package_revisions
    }
    changed_interfaces = {
        package
        for package in before_interfaces
        if before_interfaces[package] != after_interfaces[package]
    }
    predicted = _propagate_interfaces(
        case.changed_package,
        _meldra_contracts(before),
        _meldra_contracts(after),
        _meldra_consumers(before, after),
        changed_interfaces,
    )
    symbol_counts = Counter(item.package_name for item in before.hir.symbols)
    return LocalityObservation(
        case.id,
        case.category,
        "meldra-closed",
        case.expected_invalidated_packages,
        predicted,
        tuple(sorted(changed_interfaces)),
        sum(symbol_counts[item] for item in predicted),
        sum(symbol_counts[item] for item in case.expected_invalidated_packages),
        len(predicted),
    )


def run_interface_locality_benchmark() -> InterfaceLocalityReport:
    protocol = assert_stage04e_protocol()
    cases = generate_locality_cases()
    current = []
    maximal = []
    meldra = []
    for case in cases:
        current.append(_current_observation(case))
        maximal.append(_maximal_observation(case))
        meldra.append(_meldra_observation(case))
    return InterfaceLocalityReport(
        cases,
        LocalityArmReport("current-python-sidecar", tuple(current)),
        LocalityArmReport("maximal-python-profile", tuple(maximal)),
        LocalityArmReport("meldra-closed", tuple(meldra)),
        protocol.protocol_sha256,
    )


__all__ = [
    "INTERFACE_LOCALITY_CATEGORIES",
    "INTERFACE_LOCALITY_REPETITIONS",
    "INTERFACE_LOCALITY_SCHEMA_VERSION",
    "InterfaceLocalityReport",
    "LocalityArmReport",
    "LocalityCase",
    "LocalityObservation",
    "generate_locality_cases",
    "run_interface_locality_benchmark",
]
