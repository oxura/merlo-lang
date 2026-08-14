from __future__ import annotations

import copy
from pathlib import Path

import pytest

from research.archive.historical_protocol.merlo.frontend_semantics import check_frontend
from tools.benchmarks.merlo.productive_corpus import load_productive_corpus
from tools.benchmarks.merlo.productive_corpus_execution import (
    PRODUCTIVE_CORPUS_EXECUTION_KIND,
    PRODUCTIVE_CORPUS_EXECUTION_SCHEMA_VERSION,
    _split_merlo_sources,
    run_productive_corpus_execution,
    validate_productive_corpus_execution,
)



@pytest.mark.parametrize(
    "family",
    (
        "missing-capability",
        "pure-effect-violation",
        "close-every-exit",
        "stale-line-view",
        "private-module-access",
        "cyclic-import",
        "public-interface-drift",
    ),
)
def test_merlo_compiler_emits_stable_invariant_diagnostic(family: str) -> None:
    corpus = load_productive_corpus(ROOT / "tools/benchmarks/merlo/benchmarks/merlo_productive_corpus.json")
    case = next(item for item in corpus["cases"] if item["kind"] == "merlo" and item["family"] == family)
    result = check_frontend(
        _split_merlo_sources(case["merlo_source"], fallback_path=f"{case['id']}.mlo")
    )
    assert result.diagnostics
    assert result.diagnostics[0].code == case["expected"]["outcome"]

ROOT = Path(__file__).parents[4]


def test_execution_runs_every_case_once_and_reports_honest_layers() -> None:
    report = run_productive_corpus_execution(ROOT)

    assert report["schema_version"] == PRODUCTIVE_CORPUS_EXECUTION_SCHEMA_VERSION
    assert report["kind"] == PRODUCTIVE_CORPUS_EXECUTION_KIND
    assert report["total_cases"] == 1360
    assert report["attempted_cases"] == 1360
    assert report["execution_layers"]["python_applications"]["status"] == "PASSED"
    assert report["execution_layers"]["python_applications"]["attempted"] == 960
    assert report["execution_layers"]["merlo_compiler"]["total"] == 400
    assert report["execution_layers"]["merlo_compiler"]["attempted"] == 400
    assert report["execution_layers"]["merlo_compiler"]["passed"] == 400
    assert report["execution_layers"]["merlo_compiler"]["failed"] == 0
    assert report["execution_layers"]["merlo_compiler"]["unmeasured"] == 0
    assert report["execution_layers"]["merlo_compiler"]["status"] == "PASSED"
    assert report["execution_layers"]["native"]["status"] == "UNMEASURED"
    assert report["execution_layers"]["hir"]["status"] == "UNMEASURED"
    assert report["execution_layers"]["rir"]["status"] == "UNMEASURED"
    assert report["execution_layers"]["mir"]["status"] == "UNMEASURED"
    assert report["execution_layers"]["canonical"]["status"] == "UNMEASURED"
    assert report["execution_layers"]["concise_application"]["status"] == "UNMEASURED"
    validate_productive_corpus_execution(report)


def test_execution_report_is_deterministic() -> None:
    first = run_productive_corpus_execution(ROOT)
    second = run_productive_corpus_execution(ROOT)
    assert first == second


def test_execution_validator_rejects_tampered_counts() -> None:
    report = run_productive_corpus_execution(ROOT)
    tampered = copy.deepcopy(report)
    tampered["attempted_cases"] -= 1

    with pytest.raises(ValueError, match="attempted_cases"):
        validate_productive_corpus_execution(tampered)


def test_execution_validator_rejects_tampered_layer_status() -> None:
    report = run_productive_corpus_execution(ROOT)
    tampered = copy.deepcopy(report)
    tampered["execution_layers"]["native"]["status"] = "PASSED"

    with pytest.raises(ValueError, match="native"):
        validate_productive_corpus_execution(tampered)


def test_execution_validator_rejects_failure_without_failure_id() -> None:
    report = run_productive_corpus_execution(ROOT)
    tampered = copy.deepcopy(report)
    tampered["execution_layers"]["python_applications"]["failed"] = 1
    tampered["execution_layers"]["python_applications"]["passed"] -= 1

    with pytest.raises(ValueError, match="failure_ids"):
        validate_productive_corpus_execution(tampered)
