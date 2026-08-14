from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tools.benchmarks.merlo.alpha_simplicity import (
    ARMS,
    REQUIRED_CATEGORIES,
    CorpusError,
    build_report,
    load_corpus,
    validate_observation_digest,
    validate_report,
)


CORPUS = Path("research/archive/alpha1/benchmarks/alpha_simplicity/corpus.json")


def test_frozen_corpus_has_required_pairs_and_independent_results() -> None:
    records = load_corpus(CORPUS)
    assert len(records) == 48
    assert {record.category for record in records} == set(REQUIRED_CATEGORIES)
    assert all(sum(record.category == category for record in records) == 6 for category in REQUIRED_CATEGORIES)
    assert all(set(record.arm_sources()) == set(ARMS) for record in records)
    assert all(record.expected["returncode"] == 0 for record in records)


def test_report_recomputes_metrics_gates_and_exact_worst_case_identity() -> None:
    report = build_report(load_corpus(CORPUS))
    assert report["count"] == 48
    assert report["passed"]
    assert all(report["gates"].values())
    assert report["summaries"]["ratios"]["lexical_merlo_over_python"]["median"] <= 0.80
    assert report["summaries"]["ratios"]["punctuation_merlo_over_python"]["median"] <= 0.80
    for arm in ARMS:
        for metric_summary in report["summaries"]["arms"][arm].values():
            worst = metric_summary["worst"]
            assert worst["case_id"] in {item["id"] for item in report["observations"]}
            assert len(worst["record_sha256"]) == 64


def test_python_arms_produce_their_preregistered_outputs(tmp_path: Path) -> None:
    for record in load_corpus(CORPUS):
        source = tmp_path / f"{record.id}.py"
        source.write_text(record.python, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(source)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == record.expected["returncode"]
        assert completed.stdout == record.expected["stdout"]
        assert completed.stderr == ""


def test_report_is_deterministic_and_rejects_source_mutation() -> None:
    records = load_corpus(CORPUS)
    first = build_report(records)
    assert first == build_report(records)
    changed = replace(records[0], concise=records[0].concise + "\n")
    changed_records = (changed,) + records[1:]
    with pytest.raises(CorpusError, match="content-addressed"):
        validate_report(first, changed_records)
    with pytest.raises(CorpusError, match="post-observation"):
        validate_observation_digest(first, changed_records)


def test_duplicate_and_missing_category_records_are_rejected(tmp_path: Path) -> None:
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    payload["records"].append(dict(payload["records"][0]))
    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusError, match="duplicate"):
        load_corpus(duplicate_path)

    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    payload["records"] = [item for item in payload["records"] if item["category"] != "network"]
    shortfall_path = tmp_path / "shortfall.json"
    shortfall_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusError, match="shortfall"):
        load_corpus(shortfall_path)


def test_ambiguous_arm_and_expected_result_records_are_rejected(tmp_path: Path) -> None:
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    payload["arms"] = ["concise", "python"]
    path = tmp_path / "ambiguous.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusError, match="ambiguous"):
        load_corpus(path)

    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    del payload["records"][0]["expected"]
    path = tmp_path / "missing-result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CorpusError, match="expected"):
        load_corpus(path)
