from __future__ import annotations

from merlo.context_experiment import (
    EFFECT_CONTEXT_REPETITIONS,
    effect_aware_context_closure,
    effect_blind_context_closure,
    generate_effect_context_cases,
    run_effect_context_benchmark,
)
from merlo.frontend_semantics import check_frontend
from merlo.maximal_python import analyze_maximal_python


def test_effect_context_closures_separate_unrelated_effect_branches():
    graph = {
        "root": {"pay", "mail", "Order"},
        "pay": {"root", "Payments", "Order"},
        "mail": {"root", "Mail", "Order"},
        "Payments": {"pay"},
        "Mail": {"mail"},
        "Order": {"root", "pay", "mail"},
    }
    effects = {
        "root": ("payments.charge", "mail.send"),
        "pay": ("payments.charge",),
        "mail": ("mail.send",),
        "Payments": ("payments.charge",),
        "Mail": ("mail.send",),
        "Order": (),
    }
    kinds = {
        "root": "task",
        "pay": "task",
        "mail": "task",
        "Payments": "capability",
        "Mail": "capability",
        "Order": "record",
    }

    assert effect_blind_context_closure(graph, "root") == (
        "Mail",
        "Order",
        "Payments",
        "mail",
        "pay",
        "root",
    )
    assert effect_aware_context_closure(
        graph, "root", "payments.charge", effects, kinds
    ) == ("Order", "Payments", "pay", "root")


def test_generated_context_corpus_has_72_compile_clean_changes():
    cases = generate_effect_context_cases()

    assert len(cases) == 6 * EFFECT_CONTEXT_REPETITIONS == 72
    assert len({item.category for item in cases}) == 6
    for case in cases:
        assert check_frontend(dict(case.meldra_before)).ok is True
        assert check_frontend(dict(case.meldra_after)).ok is True
        assert analyze_maximal_python(
            dict(case.python_before), case.python_manifest
        ).ok is True
        assert analyze_maximal_python(
            dict(case.python_after), case.python_manifest
        ).ok is True


def test_typed_effect_context_reduces_tokens_without_missing_context():
    payload = run_effect_context_benchmark().to_dict()
    current = payload["arms"]["current-python-sidecar"]
    maximal = payload["arms"]["maximal-python-profile"]
    meldra = payload["arms"]["meldra-closed"]

    assert current["context_symbols"] == 1080
    assert current["context_tokens"] == 29376
    assert current["unnecessary_context_ratio"] == 0.666667
    assert current["missing_context_requests"] == 0

    assert maximal["context_symbols"] == meldra["context_symbols"] == 360
    assert maximal["context_tokens"] == 14976
    assert meldra["context_tokens"] == 18504
    assert maximal["unnecessary_context_ratio"] == 0.0
    assert meldra["unnecessary_context_ratio"] == 0.0
    assert maximal["missing_context_requests"] == 0
    assert meldra["missing_context_requests"] == 0
    assert maximal["context_token_reduction_vs_effect_blind"] == 0.490196
    assert meldra["context_token_reduction_vs_effect_blind"] == 0.370098


def test_all_context_arms_verify_behavior_on_first_pass():
    payload = run_effect_context_benchmark().to_dict()

    for arm in payload["arms"].values():
        assert arm["tasks"] == 72
        assert arm["verified_changes"] == 72
        assert arm["agent_task_success"] == 1.0
        assert arm["first_pass_success"] == 1.0
        assert arm["infrastructure_failures"] == 0
    assert payload["arms"]["current-python-sidecar"][
        "verified_changes_per_1000_context_tokens"
    ] == 2.45098
    assert payload["arms"]["maximal-python-profile"][
        "verified_changes_per_1000_context_tokens"
    ] == 4.807692
    assert payload["arms"]["meldra-closed"][
        "verified_changes_per_1000_context_tokens"
    ] == 3.891051


def test_context_report_does_not_claim_external_or_agent_evidence():
    payload = run_effect_context_benchmark().to_dict()

    assert payload["statistical_units"] == {
        "tasks": 72,
        "program_templates": 1,
        "effect_categories": 6,
        "generated_repetitions_per_category": 12,
        "independent_programs": 0,
        "independent_authors": 0,
        "primary_external_gate_status": "UNMEASURED",
    }
    assert payload["evidence_level"] == "GENERATED_PILOT_NOT_EXTERNAL_EVIDENCE"
    assert payload["decision"] == "NO_GO_LANGUAGE_ALPHA"
