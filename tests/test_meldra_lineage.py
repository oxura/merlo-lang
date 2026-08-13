from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from merlo.analyzer import scan_python
from merlo.git_identity import (
    GitIdentityMetrics,
    benchmark_git_identity,
    measure_split_merge_hypotheses,
    run_git,
)


def _write(root: Path, files: dict[str, str]) -> None:
    for path in root.rglob("*.py"):
        path.unlink()
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
    )
    return completed.stdout.strip()


def _repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Meldra Test")
    _git(root, "config", "user.email", "meldra@example.invalid")


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def test_split_hypothesis_is_read_only_measurement_and_keeps_raw_denominator(
    tmp_path: Path,
):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    old_root.mkdir()
    new_root.mkdir()
    _write(
        old_root,
        {"m.py": "def process(x):\n    left = x + 1\n    right = x * 2\n    return left + right\n"},
    )
    _write(
        new_root,
        {
            "m.py": (
                "def make_left(x):\n    return x + 1\n\n"
                "def make_right(x):\n    return x * 2\n"
            )
        },
    )
    old = scan_python(old_root)
    new = scan_python(new_root, previous=old)
    old_entity_id = old.entity("m.process").id
    before = old.to_dict(), new.to_dict()

    measurement = measure_split_merge_hypotheses(old, new)

    assert measurement.split_groups_assessed == 1
    assert measurement.split_candidate_count == 1
    candidate = next(item for item in measurement.candidates if item.kind == "split")
    assert candidate.source_ids == (old_entity_id,)
    assert len(candidate.target_ids) == 2
    assert candidate.matched_feature_count <= candidate.whole_feature_count
    assert measurement.to_dict()["split_groups_assessed"] == 1
    assert (old.to_dict(), new.to_dict()) == before
    assert sum(entity.id == old_entity_id for entity in new.entities) <= 1


def test_merge_hypothesis_is_statistics_not_an_identity_assignment(tmp_path: Path):
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    old_root.mkdir()
    new_root.mkdir()
    _write(
        old_root,
        {
            "m.py": (
                "def make_left(x):\n    return x + 1\n\n"
                "def make_right(x):\n    return x * 2\n"
            )
        },
    )
    _write(
        new_root,
        {"m.py": "def process(x):\n    left = x + 1\n    right = x * 2\n    return left + right\n"},
    )
    old = scan_python(old_root)
    new = scan_python(new_root, previous=old)
    prior_ids = {entity.id for entity in old.entities}
    before_ids = tuple(entity.id for entity in new.entities)

    measurement = measure_split_merge_hypotheses(old, new)

    assert measurement.merge_groups_assessed == 1
    assert measurement.merge_candidate_count == 1
    candidate = next(item for item in measurement.candidates if item.kind == "merge")
    assert len(candidate.source_ids) == 2
    assert len(candidate.target_ids) == 1
    assert tuple(entity.id for entity in new.entities) == before_ids
    assert all(sum(entity.id == old_id for entity in new.entities) <= 1 for old_id in prior_ids)


def test_git_benchmark_rename_and_rewrite_has_raw_precision_denominator(tmp_path: Path):
    _repo(tmp_path)
    _write(tmp_path, {"module.py": "def alpha(x):\n    return x + 1\n"})
    old = _commit(tmp_path, "old")
    _write(tmp_path, {"module.py": "def beta(x):\n    return x + 2\n"})
    new = _commit(tmp_path, "rename and rewrite")

    report = benchmark_git_identity(tmp_path, commit_pairs=((old, new),))

    assert report.ok
    assert len(report.pair_results) == 1
    result = report.pair_results[0]
    assert any(
        hint.kind == "Renamed"
        and hint.source_locators == ("module.alpha",)
        and hint.target_locators == ("module.beta",)
        for hint in result.hints
    )
    payload = report.to_dict()
    assert payload["metrics"]["precision_denominator"] == result.metrics.predicted_link_count
    assert payload["metrics"]["precision_numerator"] == result.metrics.true_positive_links
    evolved = payload["evolved_identity_metrics"]
    assert evolved["ground_truth_changed_links"] == 1
    assert evolved["precision_denominator"] == 1
    assert evolved["true_positive_links"] == 1
    assert payload["completed_pair_count"] == 1
    assert "split_groups_assessed" in payload["split_merge_hypotheses"]


def test_git_copy_modify_is_ambiguous_ground_truth_not_a_precision_link(tmp_path: Path):
    _repo(tmp_path)
    _write(tmp_path, {"a.py": "def alpha(x):\n    return x + 1\n"})
    old = _commit(tmp_path, "old")
    _write(
        tmp_path,
        {
            "a.py": "def alpha(x):\n    return x + 2\n",
            "b.py": "def alpha(x):\n    return x + 1\n",
        },
    )
    new = _commit(tmp_path, "copy and modify")

    report = benchmark_git_identity(tmp_path, commit_pairs=((old, new),))

    assert report.ok
    result = report.pair_results[0]
    assert any(hint.kind == "Copied" and hint.ambiguous for hint in result.hints)
    assert result.metrics.ground_truth_ambiguous_count >= 1
    assert result.metrics.resolver_ambiguous_count >= 1
    assert result.metrics.resolver_no_link_count >= 1


def test_precision_denominator_is_predicted_links_not_ground_truth_links():
    metrics = GitIdentityMetrics(
        true_positive_links=2,
        false_positive_links=1,
        predicted_link_count=3,
        ground_truth_link_count=9,
        matched_ground_truth_links=2,
        ground_truth_ambiguous_count=4,
        resolver_ambiguous_count=3,
        resolver_no_link_count=5,
        new_entity_count=12,
    )

    assert metrics.precision == pytest.approx(2 / 3)
    assert metrics.to_dict()["precision_denominator"] == 3
    assert metrics.to_dict()["recall_denominator"] == 9


def test_git_command_boundary_rejects_strings_and_reports_infrastructure_failure(tmp_path: Path):
    with pytest.raises(TypeError, match="sequence"):
        run_git("git status", tmp_path, 1.0)

    report = benchmark_git_identity(
        tmp_path,
        git_argv=(str(tmp_path / "missing-git"),),
        timeout=1.0,
    )

    assert not report.ok
    assert not report.pair_results
    payload = report.to_dict()
    assert payload["infrastructure_error_count"] == 1
    assert payload["infrastructure_errors"][0]["operation"] == "select_commit_pairs"
    assert payload["metrics"]["precision_denominator"] == 0
