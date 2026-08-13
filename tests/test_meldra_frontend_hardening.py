from __future__ import annotations

import pytest

from merlo.frontend_hardening import run_frontend_hardening
from merlo.independent_corpus import verify_independent_corpus_lock


@pytest.fixture(scope="module")
def hardening_report():
    return run_frontend_hardening().to_dict()


def test_differential_values_and_complete_effect_traces_match(hardening_report):
    assert hardening_report["differential_semantics"] == {
        "pure_value_matches": 120,
        "pure_value_denominator": 120,
        "effectful_value_matches": 40,
        "effectful_value_denominator": 40,
        "effect_trace_matches": 40,
        "effect_trace_denominator": 40,
        "all_values_equal": True,
        "all_effect_traces_equal": True,
    }


def test_all_metamorphic_revision_relations_hold(hardening_report):
    assert hardening_report["metamorphic_revisions"] == {
        "passes": 200,
        "denominator": 200,
    }


def test_parser_fuzz_has_10000_cases_and_no_crashes(hardening_report):
    fuzz = hardening_report["parser_fuzz"]

    assert fuzz["seed"] == 20260810
    assert fuzz["cases"] == 10_000
    assert fuzz["crashes"] == 0
    assert fuzz["families"] == [
        {
            "family": "unicode_and_newlines",
            "cases": 3000,
            "accepted": 3000,
            "expected_rejections": 0,
            "crashes": 0,
            "byte_exact_roundtrips": 3000,
        },
        {
            "family": "malformed_and_partial",
            "cases": 4000,
            "accepted": 473,
            "expected_rejections": 3527,
            "crashes": 0,
            "byte_exact_roundtrips": 473,
        },
        {
            "family": "nesting_and_large_files",
            "cases": 3000,
            "accepted": 3000,
            "expected_rejections": 0,
            "crashes": 0,
            "byte_exact_roundtrips": 3000,
        },
    ]


def test_all_five_semantic_mutation_families_are_detected(hardening_report):
    probes = hardening_report["mutation_probes"]

    assert probes["killed"] == probes["denominator"] == 100
    assert probes["score"] == 1.0
    assert probes["level"] == (
        "SEMANTIC_OUTPUT_MUTATION_PROBES_NOT_SOURCE_MUTATION"
    )
    assert {item["family"] for item in probes["families"]} == {
        "binder",
        "effects",
        "capabilities",
        "interface_hashing",
        "lowering",
    }
    assert all(
        item["killed"] == item["mutants"] == 20
        for item in probes["families"]
    )


def test_lowering_is_identical_across_process_seed_and_file_order(
    hardening_report,
):
    assert hardening_report["determinism"] == {
        "baseline_sha256": (
            "3860746f0121a7cac489c2023bf13d0efbabc0eefd0668d48cd95c55d4efc60b"
        ),
        "multiprocess": {"matches": 8, "denominator": 8},
        "hash_seed": {"matches": 8, "denominator": 8},
        "file_order": {"matches": 24, "denominator": 24},
        "all_byte_identical": True,
    }


def test_hardening_passes_preregistered_generated_gates_without_alpha_claim(
    hardening_report,
):
    assert hardening_report["gates"] == {
        "differential_values_and_traces": True,
        "metamorphic_relations": True,
        "parser_crashes_zero": True,
        "parser_fuzz_minimum_10000": True,
        "mutation_score_minimum_0_90": True,
        "cross_process_hash_seed_file_order_determinism": True,
    }
    assert hardening_report["decision"] == "NO_GO_LANGUAGE_ALPHA"
    assert any(
        "not source-level mutants" in item
        for item in hardening_report["limitations"]
    )
    assert verify_independent_corpus_lock()["status"] == (
        "LOCKED_BEFORE_FRONTEND_HARDENING"
    )
