from __future__ import annotations

from research.archive.historical_protocol.merlo.frontend_semantics import check_frontend
from research.archive.alpha1.merlo.interface_locality import (
    INTERFACE_LOCALITY_CATEGORIES,
    INTERFACE_LOCALITY_REPETITIONS,
    generate_locality_cases,
    run_interface_locality_benchmark,
)
from research.archive.alpha1.merlo.maximal_python import analyze_maximal_python


def test_locality_corpus_has_108_compile_clean_semantic_changes():
    cases = generate_locality_cases()

    assert len(cases) == 108
    assert {item.category for item in cases} == set(INTERFACE_LOCALITY_CATEGORIES)
    assert {item.variant for item in cases} == set(
        range(INTERFACE_LOCALITY_REPETITIONS)
    )
    for case in cases:
        assert check_frontend(dict(case.meldra_before)).ok is True
        assert check_frontend(dict(case.meldra_after)).ok is True
        assert analyze_maximal_python(
            dict(case.python_before), case.python_manifest_before
        ).ok is True
        assert analyze_maximal_python(
            dict(case.python_after), case.python_manifest_after
        ).ok is True


def test_private_edits_preserve_interfaces_and_require_no_downstream_work():
    report = run_interface_locality_benchmark().to_dict()

    for arm in report["arms"].values():
        private = [
            item
            for item in arm["observations"]
            if item["category"].startswith("private_")
        ]
        assert len(private) == 4 * INTERFACE_LOCALITY_REPETITIONS
        assert all(item["expected"] == [] for item in private)
        assert all(item["predicted"] == [] for item in private)
        assert all(
            item["changed_interfaces"] == [
                item["case_id"].split(":")[1].join(("loc", ""))
            ]
            or item["changed_interfaces"] == []
            for item in private
        )


def test_interface_revision_propagation_is_exact_for_closed_arms():
    payload = run_interface_locality_benchmark().to_dict()

    for name in ("maximal-python-profile", "meldra-closed"):
        arm = payload["arms"][name]
        assert arm["cases"] == 108
        assert arm["exact_cases"] == 108
        assert arm["invalidation_precision"] == 1.0
        assert arm["invalidation_recall"] == 1.0
        assert arm["unnecessary_invalidations"] == 0
        assert arm["missed_invalidations"] == 0


def test_current_sidecar_misses_transitive_public_contract_invalidations():
    payload = run_interface_locality_benchmark().to_dict()
    current = payload["arms"]["current-python-sidecar"]

    assert current["exact_cases"] == 72
    assert current["invalidation_precision"] == 1.0
    assert current["invalidation_recall"] == 0.666667
    assert current["unnecessary_invalidations"] == 0
    assert current["missed_invalidations"] == 36
    assert current["categories"]["public_return_type_change"][
        "false_negative"
    ] == INTERFACE_LOCALITY_REPETITIONS
    assert current["categories"]["public_effect_widening"][
        "false_negative"
    ] == INTERFACE_LOCALITY_REPETITIONS
    assert current["categories"]["public_capability_widening"][
        "false_negative"
    ] == INTERFACE_LOCALITY_REPETITIONS


def test_locality_report_marks_generated_non_independent_evidence():
    payload = run_interface_locality_benchmark().to_dict()

    assert payload["statistical_units"] == {
        "semantic_changes": 108,
        "program_templates": 1,
        "change_templates": 9,
        "generated_repetitions_per_change_template": 12,
        "independent_programs": 0,
        "independent_authors": 0,
        "primary_external_gate_status": "UNMEASURED",
    }
    assert payload["evidence_level"] == "GENERATED_PILOT_NOT_EXTERNAL_EVIDENCE"
    assert payload["decision"] == "NO_GO_LANGUAGE_ALPHA"
