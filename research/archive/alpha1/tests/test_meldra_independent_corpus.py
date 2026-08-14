from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from research.archive.alpha1.merlo.independent_corpus import (
    acceptance_digest,
    load_independent_programs,
    run_adversarial_negatives,
    run_behavior_changes,
    run_independent_corpus,
    verify_independent_corpus_lock,
)


ROOT = Path(__file__).parents[1]
_REQUIRED_FILES = (
    "merlo/core_semantics.py",
    "benchmarks/frozen/stage04/meldra/frontend_bench.py",
    "merlo/frontend_evaluator.py",
    "benchmarks/frozen/stage04/meldra/frontend_semantics.py",
    "benchmarks/frozen/stage04/meldra/frontend_syntax.py",
    "merlo/frontend_semantics.py",
    "merlo/frontend_syntax.py",
    "merlo/python_binder.py",
    "benchmarks/frozen/stage04/meldra/core_ir_schema_v1.json",
    "benchmarks/frozen/stage04/meldra/STAGE_0_4_FREEZE.json",
    "merlo/independent_corpus.py",
    "benchmarks/meldra_stage04_support_profile.json",
    "benchmarks/meldra_stage04_frontend_benchmark.json",
    "benchmarks/meldra_stage04_freeze.json",
    "benchmarks/meldra_stage04_freeze_lock.json",
    "benchmarks/meldra_stage04e_protocol.json",
    "benchmarks/meldra_stage04e_protocol_lock.json",
    "benchmarks/meldra_stage04e_protocol_v1.json",
    "benchmarks/meldra_stage04e_protocol_v1_lock.json",
    "benchmarks/meldra_stage04e_protocol_v2.json",
    "benchmarks/meldra_stage04e_protocol_v2_lock.json",
    "benchmarks/meldra_independent_mbpp_subset.json",
    "benchmarks/meldra_independent_corpus_lock.json",
)


def _copy_locked_corpus(tmp_path: Path) -> Path:
    for relative in _REQUIRED_FILES:
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return tmp_path


def test_external_spec_subset_has_40_programs_ten_balanced_domains():
    programs = load_independent_programs(ROOT)

    assert len(programs) == 40
    assert len({item.program_id for item in programs}) == 40
    assert sum(len(item.acceptance_cases) for item in programs) == 120
    assert Counter(item.domain_adapter for item in programs) == {
        "cli": 4,
        "pricing": 4,
        "authorization": 4,
        "inventory": 4,
        "small-workflow": 4,
        "data-transformation": 4,
        "event-processing": 4,
        "plugin-like-dispatch": 4,
        "configuration-validation": 4,
        "notification-service": 4,
    }
    assert len({item.source_file for item in programs}) == 4
    assert acceptance_digest(programs) == (
        "a0d0f1d433823c0bd4a2707c19be597298e149801a55e17e5aa41685599534aa"
    )


def test_corpus_and_acceptance_lock_detects_tampering(tmp_path: Path):
    root = _copy_locked_corpus(tmp_path)
    assert verify_independent_corpus_lock(root)["status"] == (
        "LOCKED_BEFORE_FRONTEND_HARDENING"
    )

    corpus_path = root / "benchmarks/meldra_independent_mbpp_subset.json"
    payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    payload["records"][0]["acceptance_cases"][0]["expected"] += 1
    corpus_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="acceptance_sha256"):
        verify_independent_corpus_lock(root)


def test_harness_lock_detects_post_freeze_rewrites(tmp_path: Path):
    root = _copy_locked_corpus(tmp_path)
    harness = root / "merlo/independent_corpus.py"
    harness.write_text(harness.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="harness_sha256"):
        verify_independent_corpus_lock(root)


def test_all_200_behavior_mutations_are_observable_in_both_languages():
    results = run_behavior_changes(load_independent_programs(ROOT))

    assert len(results) == 200
    assert all(item.python_acceptance_rejected for item in results)
    assert all(item.meldra_acceptance_rejected for item in results)
    assert all(item.meldra_interface_preserved for item in results)
    assert all(item.meldra_implementation_changed for item in results)
    assert all(item.status == "DETECTED_BY_BOTH" for item in results)


def test_all_300_adversarial_negatives_reach_expected_diagnostic():
    results = run_adversarial_negatives()

    assert len(results) == 300
    assert all(item.detected for item in results)
    assert len(Counter(item.expected_code for item in results)) == 10
    assert all(count == 30 for count in Counter(
        item.expected_code for item in results
    ).values())


def test_external_python_and_meldra_pass_same_frozen_acceptance_values():
    payload = run_independent_corpus(ROOT).to_dict()

    assert payload["baseline"]["current-python-sidecar"] == {
        "program_passes": 40,
        "programs": 40,
        "assertion_passes": 120,
        "assertions": 120,
        "role": "external executable reference; sidecar not a runtime sandbox",
    }
    assert payload["baseline"]["meldra-closed"] == {
        "program_passes": 40,
        "programs": 40,
        "assertion_passes": 120,
        "assertions": 120,
        "role": "repository-authored translation checked against external tests",
    }
    assert payload["behavior_changes"]["passed"] == 200
    assert payload["adversarial_negatives"]["passed"] == 300


def test_report_marks_translation_independence_and_strict_burden_honestly():
    payload = run_independent_corpus(ROOT).to_dict()

    assert payload["source"]["external_specs_and_tests"] is True
    assert payload["source"]["python_references_from_external_dataset"] is True
    assert payload["source"]["meldra_implementations_external"] is False
    assert payload["source"]["human_adjudicated_meldra_translations"] is False
    assert payload["statistical_units"] == {
        "paired_programs": 40,
        "domains": 10,
        "acceptance_assertions": 120,
        "behavior_changes": 200,
        "adversarial_negatives": 300,
        "external_spec_source_groups": 4,
        "independent_meldra_implementation_authors": 0,
    }
    assert payload["baseline"]["maximal-python-profile"][
        "admitted_programs"
    ] == 0
    assert payload["baseline"]["maximal-python-profile"][
        "diagnostic_counts"
    ] == {"MissingParameterType": 40, "MissingReturnType": 40}
    assert payload["primary_external_gate_status"] == "PARTIAL"
    assert payload["decision"] == "NO_GO_LANGUAGE_ALPHA"
