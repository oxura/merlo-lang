from __future__ import annotations

from merlo.capability_experiment import (
    CAPABILITY_ATTACK_CATEGORIES,
    CAPABILITY_ATTACK_REPETITIONS,
    generate_capability_cases,
    run_capability_experiment,
)
from merlo.frontend_semantics import check_frontend
from merlo.maximal_python import analyze_maximal_python


def test_capability_corpus_has_120_attacks_and_120_safe_controls():
    cases = generate_capability_cases()

    assert len(cases) == 120
    assert {item.category for item in cases} == set(
        CAPABILITY_ATTACK_CATEGORIES
    )
    assert {item.variant for item in cases} == set(
        range(CAPABILITY_ATTACK_REPETITIONS)
    )
    for case in cases:
        safe_meldra = check_frontend(
            {f"{case.package}/main.meldra": case.safe_meldra_source}
        )
        safe_python = analyze_maximal_python(
            {f"{case.package}/main.py": case.safe_python_source},
            case.safe_python_manifest,
        )
        assert safe_meldra.ok is True
        assert safe_python.ok is True


def test_three_arms_have_equal_attack_and_control_denominators():
    payload = run_capability_experiment().to_dict()

    for arm in payload["arms"].values():
        assert arm["attacks"] == 120
        assert arm["safe_controls"] == 120
        assert arm["infrastructure_failures"] == 0
        assert set(arm["categories"]) == set(CAPABILITY_ATTACK_CATEGORIES)
        assert all(
            item["attacks"] == CAPABILITY_ATTACK_REPETITIONS
            for item in arm["categories"].values()
        )


def test_current_sidecar_physically_prevents_no_hostile_category():
    current = run_capability_experiment().to_dict()["arms"][
        "current-python-sidecar"
    ]

    assert current["detected_attacks"] == 0
    assert current["violation_detection_recall"] == 0.0
    assert current["pre_materialization_detection_rate"] == 0.0
    assert current["false_safe"] == 120
    assert current["runtime_escapes"] == 120
    assert current["false_block"] == 0


def test_strict_profiles_block_scope_pure_and_host_but_not_secret_flow():
    payload = run_capability_experiment().to_dict()

    for name in ("maximal-python-profile", "meldra-closed"):
        arm = payload["arms"][name]
        assert arm["detected_attacks"] == 96
        assert arm["violation_detection_recall"] == 0.8
        assert arm["pre_materialization_detection_rate"] == 0.6
        assert arm["false_safe"] == 24
        assert arm["false_safe_rate"] == 0.2
        assert arm["runtime_escapes"] == 24
        assert arm["runtime_escape_rate"] == 0.2
        assert arm["false_block"] == 0
        assert arm["false_block_rate"] == 0.0

        categories = arm["categories"]
        for category in (
            "forbidden_database_scope",
            "forbidden_network_escalation",
            "effect_inside_pure_function",
        ):
            assert categories[category]["detected"] == 24
            assert categories[category]["pre_materialization"] == 24
            assert categories[category]["runtime_escapes"] == 0
        assert categories["arbitrary_host_escalation"] == {
            "attacks": 24,
            "detected": 24,
            "pre_materialization": 0,
            "false_safe": 0,
            "runtime_escapes": 0,
        }
        assert categories["secret_to_ai_information_flow"] == {
            "attacks": 24,
            "detected": 0,
            "pre_materialization": 0,
            "false_safe": 24,
            "runtime_escapes": 24,
        }


def test_capability_result_fails_frozen_go_gate_honestly():
    payload = run_capability_experiment().to_dict()

    assert payload["statistical_units"] == {
        "attacks": 120,
        "safe_controls": 120,
        "attack_templates": 5,
        "generated_repetitions_per_template": 24,
        "independent_programs": 0,
        "independent_authors": 0,
        "primary_external_gate_status": "UNMEASURED",
    }
    assert payload["evidence_level"] == (
        "GENERATED_HELD_OUT_FROM_FREEZE_NOT_EXTERNAL"
    )
    assert payload["decision"] == "NO_GO_LANGUAGE_ALPHA"
    assert payload["arms"]["meldra-closed"][
        "violation_detection_recall"
    ] < payload["frozen_gate"]["recall_min"]
    assert payload["arms"]["meldra-closed"]["false_safe"] > 0
