"""Stage 0.4E held-out-from-freeze capability safety experiment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .frontend_evaluator import EvaluationError, ReferenceEvaluator
from .frontend_semantics import FrontendCheckResult, check_frontend
from .maximal_python import (
    MaximalPythonManifest,
    MaximalPythonPackageManifest,
    MaximalPythonReport,
    analyze_maximal_python,
)
from .stage04e_protocol import assert_stage04e_protocol


CAPABILITY_EXPERIMENT_SCHEMA_VERSION = 1
CAPABILITY_ATTACK_REPETITIONS = 24
CAPABILITY_ATTACK_CATEGORIES = (
    "forbidden_database_scope",
    "forbidden_network_escalation",
    "arbitrary_host_escalation",
    "secret_to_ai_information_flow",
    "effect_inside_pure_function",
)
_ALLOWED_HOST = "api.safe.example"
_FORBIDDEN_HOST = "attacker.invalid"
_SECRET = "TOP_SECRET"


@dataclass(frozen=True)
class CapabilityCase:
    id: str
    variant: int
    category: str
    package: str
    safe_meldra_source: str
    attack_meldra_source: str
    safe_python_source: str
    attack_python_source: str
    safe_python_manifest: MaximalPythonManifest
    attack_python_manifest: MaximalPythonManifest
    allowed_effects: tuple[str, ...]
    allowed_capabilities: tuple[str, ...]
    allowed_hosts: tuple[str, ...] = (_ALLOWED_HOST,)
    provenance: str = "generated-held-out-after-stage04-freeze"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "variant": self.variant,
            "category": self.category,
            "package": self.package,
            "allowed_effects": list(self.allowed_effects),
            "allowed_capabilities": list(self.allowed_capabilities),
            "allowed_hosts": list(self.allowed_hosts),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class CapabilityObservation:
    case_id: str
    category: str
    arm: str
    unsafe: bool
    status: str
    detection_stage: str
    diagnostics: tuple[str, ...]
    runtime_executed: bool
    runtime_escape: bool
    infrastructure_failure: str | None = None

    @property
    def detected(self) -> bool:
        return self.status in {
            "BLOCKED_PRE_MATERIALIZATION",
            "BLOCKED_AT_RUNTIME_BOUNDARY",
        }

    @property
    def false_safe(self) -> bool:
        return self.unsafe and self.status == "FALSE_SAFE"

    @property
    def false_block(self) -> bool:
        return not self.unsafe and self.status == "FALSE_BLOCK"

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "arm": self.arm,
            "unsafe": self.unsafe,
            "status": self.status,
            "detected": self.detected,
            "detection_stage": self.detection_stage,
            "detection_steps": {
                "pre_materialization": 1,
                "runtime_boundary": 2,
                "post_execution_oracle": 3,
                "none": 0,
            }[self.detection_stage],
            "diagnostics": list(self.diagnostics),
            "runtime_executed": self.runtime_executed,
            "runtime_escape": self.runtime_escape,
            "false_safe": self.false_safe,
            "false_block": self.false_block,
            "infrastructure_failure": self.infrastructure_failure,
        }


@dataclass(frozen=True)
class CapabilityArmReport:
    arm: str
    attacks: tuple[CapabilityObservation, ...]
    controls: tuple[CapabilityObservation, ...]

    def to_dict(self) -> dict[str, Any]:
        detected = sum(item.detected for item in self.attacks)
        pre_materialization = sum(
            item.status == "BLOCKED_PRE_MATERIALIZATION"
            for item in self.attacks
        )
        category_results: dict[str, dict[str, int | float]] = {}
        for category in CAPABILITY_ATTACK_CATEGORIES:
            items = tuple(
                item for item in self.attacks if item.category == category
            )
            category_results[category] = {
                "attacks": len(items),
                "detected": sum(item.detected for item in items),
                "pre_materialization": sum(
                    item.status == "BLOCKED_PRE_MATERIALIZATION"
                    for item in items
                ),
                "false_safe": sum(item.false_safe for item in items),
                "runtime_escapes": sum(item.runtime_escape for item in items),
            }
        return {
            "arm": self.arm,
            "attacks": len(self.attacks),
            "safe_controls": len(self.controls),
            "detected_attacks": detected,
            "violation_detection_recall": _ratio(detected, len(self.attacks)),
            "pre_materialization_detection_rate": _ratio(
                pre_materialization, len(self.attacks)
            ),
            "false_safe": sum(item.false_safe for item in self.attacks),
            "false_safe_rate": _ratio(
                sum(item.false_safe for item in self.attacks),
                len(self.attacks),
            ),
            "false_block": sum(item.false_block for item in self.controls),
            "false_block_rate": _ratio(
                sum(item.false_block for item in self.controls),
                len(self.controls),
            ),
            "runtime_escapes": sum(
                item.runtime_escape for item in self.attacks
            ),
            "runtime_escape_rate": _ratio(
                sum(item.runtime_escape for item in self.attacks),
                len(self.attacks),
            ),
            "infrastructure_failures": sum(
                item.infrastructure_failure is not None
                for item in (*self.attacks, *self.controls)
            ),
            "categories": category_results,
            "attack_observations": [item.to_dict() for item in self.attacks],
            "control_observations": [
                item.to_dict() for item in self.controls
            ],
        }


@dataclass(frozen=True)
class CapabilityExperimentReport:
    cases: tuple[CapabilityCase, ...]
    current_python: CapabilityArmReport
    maximal_python: CapabilityArmReport
    meldra: CapabilityArmReport
    protocol_sha256: str
    schema_version: int = CAPABILITY_EXPERIMENT_SCHEMA_VERSION
    evidence_level: str = "GENERATED_HELD_OUT_FROM_FREEZE_NOT_EXTERNAL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_level": self.evidence_level,
            "protocol_sha256": self.protocol_sha256,
            "statistical_units": {
                "attacks": len(self.cases),
                "safe_controls": len(self.cases),
                "attack_templates": len(CAPABILITY_ATTACK_CATEGORIES),
                "generated_repetitions_per_template": CAPABILITY_ATTACK_REPETITIONS,
                "independent_programs": 0,
                "independent_authors": 0,
                "primary_external_gate_status": "UNMEASURED",
            },
            "cases": [item.to_dict() for item in self.cases],
            "arms": {
                self.current_python.arm: self.current_python.to_dict(),
                self.maximal_python.arm: self.maximal_python.to_dict(),
                self.meldra.arm: self.meldra.to_dict(),
            },
            "frozen_gate": {
                "minimum_attacks": 100,
                "recall_min": 0.99,
                "false_safe_max": 0,
                "false_block_rate_max": 0.03,
                "pre_materialization_rate_min": 1.0,
                "runtime_escapes_max": 0,
            },
            "decision": "NO_GO_LANGUAGE_ALPHA",
            "note": (
                "The frozen language/profile rules predate these five templates, but "
                "the 24 variants per template are generated repetitions, not an "
                "independent external hostile corpus."
            ),
        }


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0
    return round(numerator / denominator, 6)


def _meldra_source(package: str, category: str, attack: bool) -> str:
    prefix = f"package {package}\nmodule main\n"
    if category == "forbidden_database_scope":
        declarations = (
            "capability UsersDb:\n"
            "    read() -> Int uses db.users.read\n"
            "capability PaymentsDb:\n"
            "    read() -> Int uses db.payments.read\n"
        )
        if attack:
            operation = (
                "task operation(users: cap UsersDb, payments: cap PaymentsDb) -> Int:\n"
                "    uses db.users.read\n"
                "    uses db.payments.read\n"
                "    payments.read()\n"
            )
        else:
            operation = (
                "task operation(users: cap UsersDb) -> Int:\n"
                "    uses db.users.read\n"
                "    users.read()\n"
            )
    elif category == "forbidden_network_escalation":
        declarations = (
            "capability Network:\n"
            "    open(host: Text) -> Int uses network.open\n"
        )
        if attack:
            operation = (
                "task operation(network: cap Network) -> Int:\n"
                "    uses network.open\n"
                f'    network.open("{_ALLOWED_HOST}")\n'
            )
        else:
            operation = "task operation() -> Int:\n    1\n"
    elif category == "arbitrary_host_escalation":
        declarations = (
            "capability Network:\n"
            "    open(host: Text) -> Int uses network.open\n"
        )
        host = _FORBIDDEN_HOST if attack else _ALLOWED_HOST
        operation = (
            "task operation(network: cap Network) -> Int:\n"
            "    uses network.open\n"
            f'    network.open("{host}")\n'
        )
    elif category == "secret_to_ai_information_flow":
        declarations = (
            "capability Secrets:\n"
            "    read() -> Text uses secrets.read\n"
            "capability Ai:\n"
            "    send(value: Text) -> Int uses ai.send\n"
        )
        sent = "secret" if attack else '"redacted"'
        operation = (
            "task operation(secrets: cap Secrets, ai: cap Ai) -> Int:\n"
            "    uses secrets.read\n"
            "    uses ai.send\n"
            "    let secret = secrets.read()\n"
            f"    ai.send({sent})\n"
        )
    elif category == "effect_inside_pure_function":
        declarations = (
            "capability Clock:\n"
            "    now() -> Int uses clock.now\n"
        )
        if attack:
            operation = (
                "fn operation(clock: cap Clock) -> Int:\n"
                "    clock.now()\n"
            )
        else:
            operation = (
                "task operation(clock: cap Clock) -> Int:\n"
                "    uses clock.now\n"
                "    clock.now()\n"
            )
    else:
        raise ValueError(f"unknown capability category: {category}")
    return prefix + "export operation\n" + declarations + operation


def _python_source(package: str, category: str, attack: bool) -> str:
    if category == "forbidden_database_scope":
        declarations = (
            "class UsersDb:\n"
            "    def read(self) -> int:\n"
            "        return 1\n\n"
            "class PaymentsDb:\n"
            "    def read(self) -> int:\n"
            "        return 2\n\n"
        )
        if attack:
            operation = (
                "def operation(users: UsersDb, payments: PaymentsDb) -> int:\n"
                "    return payments.read()\n"
            )
        else:
            operation = (
                "def operation(users: UsersDb) -> int:\n"
                "    return users.read()\n"
            )
    elif category == "forbidden_network_escalation":
        declarations = (
            "class Network:\n"
            "    def open(self, host: str) -> int:\n"
            "        return 1\n\n"
        )
        if attack:
            operation = (
                "def operation(network: Network) -> int:\n"
                f"    return network.open({_ALLOWED_HOST!r})\n"
            )
        else:
            operation = "def operation() -> int:\n    return 1\n"
    elif category == "arbitrary_host_escalation":
        declarations = (
            "class Network:\n"
            "    def open(self, host: str) -> int:\n"
            "        return 1\n\n"
        )
        host = _FORBIDDEN_HOST if attack else _ALLOWED_HOST
        operation = (
            "def operation(network: Network) -> int:\n"
            f"    return network.open({host!r})\n"
        )
    elif category == "secret_to_ai_information_flow":
        declarations = (
            "class Secrets:\n"
            "    def read(self) -> str:\n"
            f"        return {_SECRET!r}\n\n"
            "class Ai:\n"
            "    def send(self, value: str) -> int:\n"
            "        return 1\n\n"
        )
        sent = "secret" if attack else repr("redacted")
        operation = (
            "def operation(secrets: Secrets, ai: Ai) -> int:\n"
            "    secret = secrets.read()\n"
            f"    return ai.send({sent})\n"
        )
    elif category == "effect_inside_pure_function":
        declarations = (
            "class Clock:\n"
            "    def now(self) -> int:\n"
            "        return 1\n\n"
        )
        operation = (
            "def operation(clock: Clock) -> int:\n"
            "    return clock.now()\n"
        )
    else:
        raise ValueError(f"unknown capability category: {category}")
    return declarations + operation


def _policy(category: str, attack: bool) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if category == "forbidden_database_scope":
        if attack:
            return ("db.users.read", "db.payments.read"), (
                "UsersDb",
                "PaymentsDb",
            )
        return ("db.users.read",), ("UsersDb",)
    if category == "forbidden_network_escalation":
        if attack:
            return ("network.open",), ("Network",)
        return (), ()
    if category == "arbitrary_host_escalation":
        return ("network.open",), ("Network",)
    if category == "secret_to_ai_information_flow":
        return ("secrets.read", "ai.send"), ("Secrets", "Ai")
    if category == "effect_inside_pure_function":
        if attack:
            return (), ()
        return ("clock.now",), ("Clock",)
    raise ValueError(category)


def _python_manifest(
    package: str, category: str, attack: bool
) -> MaximalPythonManifest:
    effects, capabilities = _policy(category, attack)
    module = f"{package}.main"
    class_names = {
        "forbidden_database_scope": ("UsersDb", "PaymentsDb"),
        "forbidden_network_escalation": ("Network",),
        "arbitrary_host_escalation": ("Network",),
        "secret_to_ai_information_flow": ("Secrets", "Ai"),
        "effect_inside_pure_function": ("Clock",),
    }[category]
    exports = tuple([f"{module}.{name}" for name in class_names] + [f"{module}.operation"])
    bindings = {
        "forbidden_database_scope": (
            ("users.read", "db.users.read"),
            ("payments.read", "db.payments.read"),
        ),
        "forbidden_network_escalation": (("network.open", "network.open"),),
        "arbitrary_host_escalation": (("network.open", "network.open"),),
        "secret_to_ai_information_flow": (
            ("secrets.read", "secrets.read"),
            ("ai.send", "ai.send"),
        ),
        "effect_inside_pure_function": (("clock.now", "clock.now"),),
    }[category]
    operation = f"{module}.operation"
    declared_effects = ((operation, effects),) if effects else ()
    declared_capabilities = ((operation, effects),) if effects else ()
    return MaximalPythonManifest(
        (
            MaximalPythonPackageManifest(
                package,
                package,
                exports,
                effect_bindings=bindings,
                function_effects=declared_effects,
                function_capabilities=declared_capabilities,
                allowed_network_hosts=(_ALLOWED_HOST,),
            ),
        )
    )


def generate_capability_cases() -> tuple[CapabilityCase, ...]:
    cases = []
    for variant in range(CAPABILITY_ATTACK_REPETITIONS):
        for category in CAPABILITY_ATTACK_CATEGORIES:
            package = f"cap{variant:02d}{category.split('_')[0]}"
            allowed_effects, allowed_capabilities = _policy(category, False)
            cases.append(
                CapabilityCase(
                    f"capability:{variant:02d}:{category}",
                    variant,
                    category,
                    package,
                    _meldra_source(package, category, False),
                    _meldra_source(package, category, True),
                    _python_source(package, category, False),
                    _python_source(package, category, True),
                    _python_manifest(package, category, False),
                    _python_manifest(package, category, True),
                    allowed_effects,
                    allowed_capabilities,
                )
            )
    return tuple(cases)


def _operation_contract_from_maximal(
    report: MaximalPythonReport, package: str
) -> tuple[set[str], set[str]]:
    symbol = report.symbol(f"{package}.main.operation")
    return set(symbol.effects), set(symbol.capabilities)


def _operation_contract_from_meldra(
    result: FrontendCheckResult, package: str
) -> tuple[set[str], set[str]]:
    if result.compilation is None:
        return set(), set()
    symbol = result.compilation.hir.symbol(f"{package}.main.operation")
    return set(symbol.effects), set(symbol.capabilities)


def _policy_escalations(
    effects: set[str],
    capabilities: set[str],
    case: CapabilityCase,
    *,
    python_profile: bool = False,
) -> tuple[str, ...]:
    diagnostics = []
    if effects - set(case.allowed_effects):
        diagnostics.append("PolicyEffectEscalation")
    allowed_capabilities = (
        set(case.allowed_effects)
        if python_profile
        else set(case.allowed_capabilities)
    )
    if capabilities - allowed_capabilities:
        diagnostics.append("PolicyCapabilityEscalation")
    return tuple(diagnostics)


def _runtime_python(
    case: CapabilityCase, *, attack: bool, enforce_host_scope: bool
) -> tuple[bool, bool, tuple[str, ...]]:
    namespace: dict[str, Any] = {}
    source = case.attack_python_source if attack else case.safe_python_source
    exec(compile(source, f"{case.package}/main.py", "exec"), namespace)
    operation = namespace["operation"]
    category = case.category
    diagnostics: tuple[str, ...] = ()
    escaped = False
    try:
        if category == "forbidden_database_scope":
            arguments = [namespace["UsersDb"]()]
            if attack:
                arguments.append(namespace["PaymentsDb"]())
            operation(*arguments)
            escaped = attack
        elif category == "forbidden_network_escalation":
            if attack:
                operation(namespace["Network"]())
                escaped = True
            else:
                operation()
        elif category == "arbitrary_host_escalation":
            opened: list[str] = []

            class NetworkBoundary:
                def open(self, host: str) -> int:
                    if enforce_host_scope and host not in case.allowed_hosts:
                        raise PermissionError(host)
                    opened.append(host)
                    return 1

            operation(NetworkBoundary())
            escaped = attack and _FORBIDDEN_HOST in opened
        elif category == "secret_to_ai_information_flow":
            sent: list[str] = []

            class SecretsBoundary:
                def read(self) -> str:
                    return _SECRET

            class AiBoundary:
                def send(self, value: str) -> int:
                    sent.append(value)
                    return 1

            operation(SecretsBoundary(), AiBoundary())
            escaped = attack and _SECRET in sent
        elif category == "effect_inside_pure_function":
            operation(namespace["Clock"]())
            escaped = attack
        else:
            raise ValueError(category)
    except PermissionError:
        diagnostics = ("HostScopeViolation",)
        return True, False, diagnostics
    return False, escaped, diagnostics


def _runtime_meldra(
    case: CapabilityCase,
    result: FrontendCheckResult,
    *,
    attack: bool,
) -> tuple[bool, bool, tuple[str, ...]]:
    if result.compilation is None:
        raise RuntimeError("cannot execute a failed frontend result")
    sent: list[str] = []

    def open_host(host: str) -> int:
        if host not in case.allowed_hosts:
            raise PermissionError(host)
        return 1

    handlers = {
        "db.users.read": lambda: 1,
        "db.payments.read": lambda: 2,
        "network.open": open_host,
        "secrets.read": lambda: _SECRET,
        "ai.send": lambda value: sent.append(value) or 1,
        "clock.now": lambda: 1,
    }
    evaluator = ReferenceEvaluator(result.compilation, handlers=handlers)
    arguments: dict[str, Any] = {}
    category = case.category
    if category == "forbidden_database_scope":
        arguments["users"] = evaluator.capability(f"{case.package}.main.UsersDb")
        if attack:
            arguments["payments"] = evaluator.capability(
                f"{case.package}.main.PaymentsDb"
            )
    elif category in {
        "forbidden_network_escalation",
        "arbitrary_host_escalation",
    } and (attack or category == "arbitrary_host_escalation"):
        arguments["network"] = evaluator.capability(
            f"{case.package}.main.Network"
        )
    elif category == "secret_to_ai_information_flow":
        arguments["secrets"] = evaluator.capability(
            f"{case.package}.main.Secrets"
        )
        arguments["ai"] = evaluator.capability(f"{case.package}.main.Ai")
    elif category == "effect_inside_pure_function":
        arguments["clock"] = evaluator.capability(
            f"{case.package}.main.Clock"
        )
    try:
        evaluator.evaluate(f"{case.package}.main.operation", arguments)
    except (EvaluationError, PermissionError) as exc:
        if "attacker.invalid" in str(exc):
            return True, False, ("HostScopeViolation",)
        raise
    escaped = attack and (
        category
        in {
            "forbidden_database_scope",
            "forbidden_network_escalation",
            "effect_inside_pure_function",
        }
        or (category == "secret_to_ai_information_flow" and _SECRET in sent)
    )
    return False, escaped, ()


def _status(
    *, unsafe: bool, preblocked: bool, runtime_blocked: bool, escaped: bool
) -> tuple[str, str]:
    if preblocked:
        return "BLOCKED_PRE_MATERIALIZATION", "pre_materialization"
    if runtime_blocked:
        return "BLOCKED_AT_RUNTIME_BOUNDARY", "runtime_boundary"
    if unsafe:
        return "FALSE_SAFE", "post_execution_oracle"
    if escaped:
        return "FALSE_BLOCK", "post_execution_oracle"
    return "ALLOWED_SAFE", "none"


def _observe_current(
    case: CapabilityCase, *, attack: bool
) -> CapabilityObservation:
    runtime_blocked, escaped, diagnostics = _runtime_python(
        case, attack=attack, enforce_host_scope=False
    )
    unsafe = attack
    status, stage = _status(
        unsafe=unsafe,
        preblocked=False,
        runtime_blocked=runtime_blocked,
        escaped=escaped,
    )
    return CapabilityObservation(
        case.id,
        case.category,
        "current-python-sidecar",
        unsafe,
        status,
        stage,
        diagnostics,
        True,
        escaped,
    )


def _observe_maximal(
    case: CapabilityCase, *, attack: bool
) -> CapabilityObservation:
    source = case.attack_python_source if attack else case.safe_python_source
    manifest = (
        case.attack_python_manifest if attack else case.safe_python_manifest
    )
    report = analyze_maximal_python({f"{case.package}/main.py": source}, manifest)
    diagnostics = tuple(
        sorted({item.code for item in report.blocking_diagnostics})
    )
    if report.ok:
        effects, capabilities = _operation_contract_from_maximal(
            report, case.package
        )
        diagnostics += _policy_escalations(
            effects, capabilities, case, python_profile=True
        )
    preblocked = bool(diagnostics)
    runtime_blocked = False
    escaped = False
    if not preblocked:
        runtime_blocked, escaped, runtime_diagnostics = _runtime_python(
            case, attack=attack, enforce_host_scope=True
        )
        diagnostics += runtime_diagnostics
    status, stage = _status(
        unsafe=attack,
        preblocked=preblocked,
        runtime_blocked=runtime_blocked,
        escaped=escaped,
    )
    return CapabilityObservation(
        case.id,
        case.category,
        "maximal-python-profile",
        attack,
        status,
        stage,
        tuple(sorted(set(diagnostics))),
        not preblocked,
        escaped,
    )


def _observe_meldra(
    case: CapabilityCase, *, attack: bool
) -> CapabilityObservation:
    source = case.attack_meldra_source if attack else case.safe_meldra_source
    result = check_frontend({f"{case.package}/main.meldra": source})
    diagnostics = tuple(sorted({item.code for item in result.diagnostics}))
    if result.ok:
        effects, capabilities = _operation_contract_from_meldra(
            result, case.package
        )
        diagnostics += _policy_escalations(effects, capabilities, case)
    preblocked = not result.ok or bool(diagnostics)
    runtime_blocked = False
    escaped = False
    if not preblocked:
        runtime_blocked, escaped, runtime_diagnostics = _runtime_meldra(
            case, result, attack=attack
        )
        diagnostics += runtime_diagnostics
    status, stage = _status(
        unsafe=attack,
        preblocked=preblocked,
        runtime_blocked=runtime_blocked,
        escaped=escaped,
    )
    return CapabilityObservation(
        case.id,
        case.category,
        "meldra-closed",
        attack,
        status,
        stage,
        tuple(sorted(set(diagnostics))),
        not preblocked,
        escaped,
    )


def run_capability_experiment() -> CapabilityExperimentReport:
    protocol = assert_stage04e_protocol()
    cases = generate_capability_cases()
    current_attacks = []
    current_controls = []
    maximal_attacks = []
    maximal_controls = []
    meldra_attacks = []
    meldra_controls = []
    for case in cases:
        current_controls.append(_observe_current(case, attack=False))
        current_attacks.append(_observe_current(case, attack=True))
        maximal_controls.append(_observe_maximal(case, attack=False))
        maximal_attacks.append(_observe_maximal(case, attack=True))
        meldra_controls.append(_observe_meldra(case, attack=False))
        meldra_attacks.append(_observe_meldra(case, attack=True))
    return CapabilityExperimentReport(
        cases,
        CapabilityArmReport(
            "current-python-sidecar",
            tuple(current_attacks),
            tuple(current_controls),
        ),
        CapabilityArmReport(
            "maximal-python-profile",
            tuple(maximal_attacks),
            tuple(maximal_controls),
        ),
        CapabilityArmReport(
            "meldra-closed",
            tuple(meldra_attacks),
            tuple(meldra_controls),
        ),
        protocol.protocol_sha256,
    )


__all__ = [
    "CAPABILITY_ATTACK_CATEGORIES",
    "CAPABILITY_ATTACK_REPETITIONS",
    "CAPABILITY_EXPERIMENT_SCHEMA_VERSION",
    "CapabilityArmReport",
    "CapabilityCase",
    "CapabilityExperimentReport",
    "CapabilityObservation",
    "generate_capability_cases",
    "run_capability_experiment",
]
