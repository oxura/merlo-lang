"""Aggregate frozen Stage 0.4E evidence and apply Language Alpha gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .stage04e_protocol import assert_stage04e_protocol


STAGE04E_DECISION_SCHEMA_VERSION = 1
STAGE04E_DECISION_FILENAME = "meldra_stage04e_decision.json"

_INPUTS = (
    "meldra_runtime_soundness.json",
    "meldra_maximal_python.json",
    "meldra_interface_locality.json",
    "meldra_effect_context.json",
    "meldra_capability_experiment.json",
    "meldra_benchmark_integrity.json",
    "meldra_independent_corpus_results.json",
    "meldra_frontend_hardening.json",
    "meldra_stage04_frontend_benchmark.json",
    "meldra_external_trials.json",
    "meldra_agent_experiment.json",
    "meldra_identity_claims.json",
    "meldra_environment_gates.json",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gate(
    gate_id: str,
    passed: bool,
    *,
    observed: Mapping[str, Any],
    required: Mapping[str, Any],
    evidence: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "id": gate_id,
        "passed": passed,
        "observed": dict(observed),
        "required": dict(required),
        "evidence": evidence,
        "reason": reason,
    }


def build_stage04e_decision(
    root: str | Path = ".",
) -> dict[str, Any]:
    root_path = Path(root)
    protocol_verification = assert_stage04e_protocol(root_path)
    benchmark_root = root_path / "benchmarks"
    artifacts = {name: _load(benchmark_root / name) for name in _INPUTS}
    protocol = _load(benchmark_root / "meldra_stage04e_protocol.json")
    go = protocol["language_alpha_go"]

    runtime = artifacts["meldra_runtime_soundness.json"]
    runtime_arm = runtime["arms"]["meldra-closed"]
    runtime_units = runtime["statistical_units"]
    runtime_metric_pass = (
        runtime_arm["unsound_exact_count"]
        <= go["runtime_soundness"]["meldra_unsound_exact"]
        and runtime_units["runtime_observations"]
        >= go["runtime_soundness"]["minimum_runtime_observations"]
    )
    runtime_external = (
        runtime_units["independent_programs"] > 0
        and runtime_units["primary_external_gate_status"] == "MEASURED"
    )
    runtime_gate = _gate(
        "runtime_soundness",
        runtime_metric_pass and runtime_external,
        observed={
            "unsound_exact_count": runtime_arm["unsound_exact_count"],
            "unsound_exact_rate": runtime_arm["unsound_exact_rate"],
            "runtime_observations": runtime_units["runtime_observations"],
            "independent_programs": runtime_units["independent_programs"],
            "primary_external_gate_status": runtime_units[
                "primary_external_gate_status"
            ],
        },
        required=go["runtime_soundness"],
        evidence=runtime["evidence_level"],
        reason=(
            "Generated runtime fixtures pass the internal metric, but the "
            "frozen primary external runtime gate is UNMEASURED."
        ),
    )

    capability = artifacts["meldra_capability_experiment.json"]
    capability_arm = capability["arms"]["meldra-closed"]
    capability_required = go["capability_safety"]
    capability_pass = (
        capability_arm["attacks"] >= capability_required["minimum_attacks"]
        and capability_arm["violation_detection_recall"]
        >= capability_required["detection_recall_min"]
        and capability_arm["false_block_rate"]
        <= capability_required["false_block_rate_max"]
        and capability_arm["false_safe"]
        <= capability_required["false_safe_max"]
        and capability_arm["pre_materialization_detection_rate"]
        >= capability_required["pre_materialization_rate_min"]
        and capability_arm["runtime_escapes"]
        <= capability_required["runtime_escapes_max"]
    )
    capability_gate = _gate(
        "capability_safety",
        capability_pass,
        observed={
            key: capability_arm[key]
            for key in (
                "attacks",
                "violation_detection_recall",
                "false_block_rate",
                "false_safe",
                "pre_materialization_detection_rate",
                "runtime_escapes",
            )
        },
        required=capability_required,
        evidence=capability["evidence_level"],
        reason=(
            "Meldra detects 80% of generated attacks; 24 are false-safe and "
            "reach runtime, violating the zero-escape frozen gate."
        ),
    )

    locality = artifacts["meldra_interface_locality.json"]
    locality_arm = locality["arms"]["meldra-closed"]
    locality_units = locality["statistical_units"]
    locality_required = go["interface_locality"]
    locality_metric_pass = (
        locality_arm["cases"] >= locality_required["minimum_changes"]
        and locality_arm["invalidation_precision"]
        >= locality_required["precision_min"]
        and locality_arm["invalidation_recall"]
        >= locality_required["recall_min"]
        and locality_arm["missed_invalidations"]
        <= locality_required["missed_invalidations_max"]
    )
    locality_external = locality_units["independent_programs"] > 0
    locality_gate = _gate(
        "interface_locality",
        locality_metric_pass and locality_external,
        observed={
            "changes": locality_arm["cases"],
            "precision": locality_arm["invalidation_precision"],
            "recall": locality_arm["invalidation_recall"],
            "missed_invalidations": locality_arm["missed_invalidations"],
            "independent_programs": locality_units["independent_programs"],
        },
        required=locality_required,
        evidence=locality["evidence_level"],
        reason=(
            "Generated locality cases pass numerically, but there are zero "
            "independent programs and the primary external gate is UNMEASURED."
        ),
    )

    integrity = artifacts["meldra_benchmark_integrity.json"]
    expressiveness = integrity["expressiveness_and_burden"]
    independent = artifacts["meldra_independent_corpus_results.json"]
    expressiveness_required = go["expressiveness"]
    expressiveness_metric_pass = (
        expressiveness["fully_expressible_program_rate"]
        >= expressiveness_required["fully_expressible_rate_min"]
        and expressiveness["foreign_escape_frequency"]
        <= expressiveness_required["foreign_escape_rate_max"]
        and expressiveness["median_meldra_source_overhead_ratio"]
        <= expressiveness_required["median_source_overhead_max"]
    )
    independent_authors = independent["statistical_units"][
        "independent_meldra_implementation_authors"
    ]
    expressiveness_gate = _gate(
        "expressiveness",
        expressiveness_metric_pass and independent_authors > 0,
        observed={
            "fully_expressible_rate": expressiveness[
                "fully_expressible_program_rate"
            ],
            "foreign_escape_rate": expressiveness["foreign_escape_frequency"],
            "median_source_overhead": expressiveness[
                "median_meldra_source_overhead_ratio"
            ],
            "independent_meldra_implementation_authors": independent_authors,
        },
        required=expressiveness_required,
        evidence=expressiveness["evidence_level"],
        reason=(
            "Repository-authored translations pass external specifications, "
            "but no independent author produced a Meldra implementation."
        ),
    )

    agent = artifacts["meldra_agent_experiment.json"]
    baseline_agent = agent["paired"]["aggregates"]["baseline"]
    meldra_agent = agent["paired"]["aggregates"]["meldra"]
    agent_measured = (
        baseline_agent["measured_tasks"] > 0
        and meldra_agent["measured_tasks"] > 0
    )
    agent_gate = _gate(
        "agent_value",
        False,
        observed={
            "baseline_measured_tasks": baseline_agent["measured_tasks"],
            "meldra_measured_tasks": meldra_agent["measured_tasks"],
            "baseline_success_rate": baseline_agent["task_success_rate"],
            "meldra_success_rate": meldra_agent["task_success_rate"],
            "provider_evidence": agent["evidence_level"],
        },
        required=go["agent_value"],
        evidence=agent["evidence_level"],
        reason=(
            "The same-model provider key is unavailable; all 90 paired tasks "
            "and all four ablations are explicitly UNMEASURED."
            if not agent_measured
            else "Measured agent deltas do not satisfy the frozen gate."
        ),
    )

    hardening = artifacts["meldra_frontend_hardening.json"]
    external = artifacts["meldra_external_trials.json"]
    hardening_pass = all(hardening["gates"].values())
    external_acceptance_pass = all(
        item["passed"] == item["trials"]
        for item in external["operations"].values()
    )
    engineering_gate = _gate(
        "engineering_integrity",
        hardening_pass and external_acceptance_pass,
        observed={
            "hardening_gates_all_pass": hardening_pass,
            "external_acceptance_all_pass": external_acceptance_pass,
            "external_success_rates": {
                name: value["success_rate"]
                for name, value in external["operations"].items()
            },
            "external_infrastructure_failures": external[
                "infrastructure_failures"
            ],
            "source_restoration_failures": external[
                "source_restoration_failures"
            ],
        },
        required=go["engineering_integrity"],
        evidence=external["evidence_level"],
        reason=(
            "Fuzz, mutation, and determinism gates pass, but external acceptance "
            "is 29/30 Rename, 14/30 Move, and 30/30 ChangeSignature."
        ),
    )

    gates = (
        runtime_gate,
        capability_gate,
        locality_gate,
        agent_gate,
        expressiveness_gate,
        engineering_gate,
    )
    all_required_pass = all(item["passed"] for item in gates)
    maximal_capability = capability["arms"]["maximal-python-profile"]
    maximal_locality = locality["arms"]["maximal-python-profile"]
    strict_equivalence = {
        "status": "INDETERMINATE",
        "locality_delta_points": round(
            100
            * abs(
                locality_arm["invalidation_recall"]
                - maximal_locality["invalidation_recall"]
            ),
            6,
        ),
        "policy_recall_delta_points": round(
            100
            * abs(
                capability_arm["violation_detection_recall"]
                - maximal_capability["violation_detection_recall"]
            ),
            6,
        ),
        "task_success_delta_points": None,
        "reason": (
            "Measured generated locality and policy axes are equal, but the "
            "mandatory agent axis and primary external runtime axis are unmeasured."
        ),
    }
    identity = artifacts["meldra_identity_claims.json"]
    no_go_reasons = [
        "mandatory_same_model_agent_metric_unmeasured",
        "capability_false_safe_and_runtime_escape_gate_failed",
        "external_move_acceptance_failed",
        "primary_external_runtime_and_locality_evidence_unmeasured",
        "independent_meldra_authorship_missing",
        "broad_python_changed_identity_claim_not_established",
    ]
    return {
        "schema_version": STAGE04E_DECISION_SCHEMA_VERSION,
        "kind": "MeldraStage04EDecision",
        "protocol_sha256": protocol_verification.protocol_sha256,
        "inputs": {
            name: _sha256(benchmark_root / name) for name in _INPUTS
        },
        "gates": list(gates),
        "all_required_go_gates_pass": all_required_pass,
        "strict_python_equivalence": strict_equivalence,
        "no_go_reasons": no_go_reasons,
        "identity_claim": identity["decision"],
        "language_alpha_decision": "NO_GO",
        "selected_direction": "STRICT_SEMANTIC_LAYER",
        "selected_profile": "maximal-python-profile-plus-meldra-semantics",
        "advance_language_alpha": False,
        "preserve": [
            "frozen Meldra closed frontend and evaluator as research artifacts",
            "typed effects and capability contracts as semantic-layer features",
            "source-preserving ChangeIR with scoped identity guarantees",
            "strict Python profile and runtime audit harness",
        ],
        "do_not_claim": [
            "production-ready new language",
            "external runtime soundness",
            "99.5% Python rename or move identity",
            "agent productivity advantage",
            "capability safety with zero runtime escape",
        ],
        "decision": "NO_GO_LANGUAGE_ALPHA_USE_SEMANTIC_LAYER",
    }


__all__ = [
    "STAGE04E_DECISION_FILENAME",
    "STAGE04E_DECISION_SCHEMA_VERSION",
    "build_stage04e_decision",
]
