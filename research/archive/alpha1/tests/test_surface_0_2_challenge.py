from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from research.archive.alpha1.merlo.surface_challenge import (
    CATEGORIES,
    SurfaceChallengeError,
    load_locked_corpus,
    measure_surface_compression,
    run_surface_challenge,
)


def test_surface_challenge_reports_every_locked_blocker(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report = run_surface_challenge(report_path=report_path)

    expected_ids = [f"surface02-{index:03d}" for index in range(1, 101)]
    assert report["corpus_count"] == 100
    assert report["category_counts"] == {category: 10 for category in CATEGORIES}
    assert report["repository_count"] == 5
    assert report["failed_case_ids"] == expected_ids
    assert report["blocked_case_ids"] == expected_ids
    assert report["status"] == "MERLO_SURFACE_0_2_UNSUPPORTED"
    assert report["gates"]["observable_result_equality"] is False
    assert report["gates"]["canonical_ast_equality"] is False
    assert report["gates"]["optimized_mir_equality"] is False
    assert report["gates"]["pipeline_fusion"] is False
    assert report_path.is_file()
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["status"] == report["status"]
    assert persisted["failed_case_ids"] == expected_ids


def _challenge_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "surface_0_2"
    shutil.copytree(Path("benchmarks") / "surface_0_2", destination)
    return destination


def test_surface_challenge_reports_missing_translation_without_skipping(
    tmp_path: Path,
) -> None:
    root = _challenge_copy(tmp_path)
    payload = json.loads((root / "translations.json").read_text(encoding="utf-8"))
    payload["translations"].pop("surface02-001")
    (root / "translations.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises(SurfaceChallengeError, match="translation identities"):
        run_surface_challenge(root, report_path=tmp_path / "missing.json")


def test_surface_challenge_marks_nonexecutable_and_missing_equivalence(
    tmp_path: Path,
) -> None:
    root = _challenge_copy(tmp_path)
    path = root / "translations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["translations"]["surface02-001"]["merlo_source"] = "not Merlo"
    payload["translations"]["surface02-002"]["manual_canonical_source"] = (
        "fn wrong(value: UInt64) -> UInt64:\n    return value\n"
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = run_surface_challenge(root, report_path=tmp_path / "failed.json")

    assert {"surface02-001", "surface02-002"} <= set(report["failed_case_ids"])
    assert report["gates"]["python_and_merlo_executed"] is False
    assert report["gates"]["canonical_ast_equality"] is False
    assert report["status"] == "MERLO_SURFACE_0_2_UNSUPPORTED"


def test_surface_challenge_rejects_valid_unrelated_semantic_sabotage(
    tmp_path: Path,
) -> None:
    root = _challenge_copy(tmp_path)
    path = root / "translations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["translations"]["surface02-001"]["merlo_source"] = (
        "fn case_surface02_001_clamp(value):\n"
        "    return 0\n\n"
        "fn main(value) -> UInt64:\n"
        "    return case_surface02_001_clamp(value)\n"
    )
    payload["translations"]["surface02-001"]["manual_canonical_source"] = (
        "fn case_surface02_001_clamp(value: UInt64) -> UInt64:\n"
        "    return 0\n\n"
        "fn main(value: UInt64) -> UInt64:\n"
        "    return case_surface02_001_clamp(value)\n"
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = run_surface_challenge(root, report_path=tmp_path / "sabotage.json")

    assert "surface02-001" in report["failed_case_ids"]
    assert report["gates"]["observable_result_equality"] is False


def test_compression_metrics_fail_closed_on_missing_or_forbidden_translation() -> None:
    payload = json.loads(
        (Path("benchmarks") / "surface_0_2" / "translations.json").read_text(
            encoding="utf-8"
        )
    )
    corpus = load_locked_corpus()
    translations = {
        case_id: item["merlo_source"]
        for case_id, item in payload["translations"].items()
    }
    translations.pop(corpus.cases[0].id)
    with pytest.raises(SurfaceChallengeError, match="translation identities"):
        measure_surface_compression(corpus, translations)

    translations[corpus.cases[0].id] = "fn bad(value: Any) -> Any:\n    return value\n"
    report = measure_surface_compression(corpus, translations)
    assert report["forbidden"]["any_dynamic"] >= 1
    assert report["gates"]["any_dynamic_zero"] is False
