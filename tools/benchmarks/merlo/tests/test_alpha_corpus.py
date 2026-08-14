from __future__ import annotations

import copy
from collections import Counter
from pathlib import Path

import pytest
from merlo.compiler import compile_project

from tools.benchmarks.merlo.alpha_corpus import (
    FEATURE_FAMILIES,
    INVALID_CASE_COUNT,
    VALID_CASE_COUNT,
    VALID_LAYERS,
    corpus_sha256,
    generate_alpha_corpus,
    family_contract,
    run_alpha_corpus,
    validate_alpha_corpus,
    validate_alpha_corpus_report,
)


def test_alpha_corpus_meets_counts_and_feature_coverage() -> None:
    corpus = generate_alpha_corpus()
    assert len(corpus["cases"]) == VALID_CASE_COUNT + INVALID_CASE_COUNT
    assert Counter(case["validity"] for case in corpus["cases"]) == {True: VALID_CASE_COUNT, False: INVALID_CASE_COUNT}
    assert {case["family"] for case in corpus["cases"]} == set(FEATURE_FAMILIES)
    assert corpus["required_layers"] == list(VALID_LAYERS)
    assert len({case["id"] for case in corpus["cases"]}) == len(corpus["cases"])
    assert len({case["content_sha256"] for case in corpus["cases"]}) == len(corpus["cases"])
    assert len(corpus["examples"]) == 8


def test_alpha_corpus_regeneration_is_byte_stable() -> None:
    first = generate_alpha_corpus()
    second = generate_alpha_corpus()
    assert first == second
    assert first["sha256"] == corpus_sha256(first)


@pytest.mark.parametrize("family", FEATURE_FAMILIES)
def test_each_family_representative_compiles_through_production_lineage(tmp_path: Path, family: str) -> None:

    case = next(item for item in generate_alpha_corpus()["cases"] if item["validity"] and item["family"] == family)
    contract = family_contract(case)
    assert contract["family"] == family
    assert family in contract["operation"] or contract["operation"] in contract["source"]
    root = tmp_path / family
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "merlo.toml").write_text(case["manifest"], encoding="utf-8")
    (root / "merlo.lock").write_text(case["lock"], encoding="utf-8")
    (root / "src" / "main.mlo").write_text(case["source"], encoding="utf-8")
    (root / "tests" / "main.mlo").write_text(case["test_source"], encoding="utf-8")
    compilation = compile_project(root, require_interface_lock=False)
    assert tuple(compilation.artifacts)[:6] == ("modules", "concise", "canonical", "hir", "rir", "mir")
    assert set(("optimized_mir", "c11")) <= set(compilation.artifacts)


def test_alpha_corpus_rejects_resigned_content_tampering() -> None:
    tampered = copy.deepcopy(generate_alpha_corpus())
    tampered["cases"][0]["source"] = tampered["cases"][0]["source"].replace("return Ok(", "return Err(")
    tampered["sha256"] = corpus_sha256(tampered)
    with pytest.raises(ValueError, match="deterministic template|content address"):
        validate_alpha_corpus(tampered)


def test_alpha_corpus_rejects_omitted_case_even_with_new_digest() -> None:
    tampered = copy.deepcopy(generate_alpha_corpus())
    tampered["cases"].pop()
    tampered["sha256"] = corpus_sha256(tampered)
    with pytest.raises(ValueError, match="counts are incomplete"):
        validate_alpha_corpus(tampered)



def _deterministic_executor(case, _root):
    return {
        "case_id": case["id"],
        "content_sha256": case["content_sha256"],
        "validity": case["validity"],
        "diagnostic": None if case["validity"] else case["expected"]["diagnostic"]["code"] + ": deterministic",
        "family": case["family"],
        "stage": "kernel",
        "operation": case["family"],
        "status": "PASSED",
        "executed": True,
        "layers": list(VALID_LAYERS) if case["validity"] else ["concise"],
        "observable": {"return_value": case["expected"]["observable"]["return_value"]} if case["validity"] else None,
    }


def test_alpha_corpus_runner_executes_full_deterministic_3200_records() -> None:
    corpus = generate_alpha_corpus()
    report = run_alpha_corpus(corpus, executor=_deterministic_executor)
    assert report["scope"] == "full"
    assert len(report["case_ids"]) == 3_200
    assert report["case_ids"] == [case["id"] for case in corpus["cases"]]
    assert len(report["records"]) == 3_200
    validate_alpha_corpus_report(report, corpus)


def test_alpha_corpus_runner_rejects_missing_and_duplicate_records() -> None:
    corpus = generate_alpha_corpus()
    report = run_alpha_corpus(corpus, executor=_deterministic_executor, case_ids=("alpha-valid-0000", "alpha-valid-0001"))
    broken = copy.deepcopy(report)
    broken["records"].pop()
    with pytest.raises(ValueError, match="coverage|omits|sha256"):
        validate_alpha_corpus_report(broken, corpus)


def test_alpha_corpus_runner_rejects_tampered_observation() -> None:
    corpus = generate_alpha_corpus()
    report = run_alpha_corpus(corpus, executor=_deterministic_executor, case_ids=("alpha-valid-0000",))
    broken = copy.deepcopy(report)
    broken["records"][0]["observable"]["return_value"] = 999
    with pytest.raises(ValueError, match="sha256"):
        validate_alpha_corpus_report(broken, corpus)