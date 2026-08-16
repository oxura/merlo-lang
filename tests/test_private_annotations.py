from __future__ import annotations

from pathlib import Path

from tools.benchmarks.merlo.private_annotations import (
    SCHEMA_VERSION,
    measure_private_annotations,
)


ROOT = Path(__file__).resolve().parents[1]


def test_measure_private_annotations_excludes_exported_boundaries(
    tmp_path: Path,
) -> None:
    project = tmp_path / "examples" / "sample"
    (project / "src").mkdir(parents=True)
    (project / "merlo.toml").write_text("[project]\nname = 'sample'\n")
    (project / "src" / "main.mlo").write_text(
        "module main\n\n"
        "helper(first: UInt64, second) -> UInt64:\n"
        "    first + second\n\n"
        "export main(value: UInt64) -> UInt64:\n"
        "    helper(value, 1)\n",
        encoding="utf-8",
    )

    report = measure_private_annotations(tmp_path)

    assert report["schema"] == SCHEMA_VERSION
    assert report["project_count"] == 1
    assert report["private_function_count"] == 1
    assert report["annotation_slots"] == 3
    assert report["explicit_annotations"] == 2
    assert report["annotation_rate"] == 2 / 3
    assert report["functions"][0]["name"] == "helper"
    assert report["gates"] == {
        "projects_measured_at_least_15": False,
        "private_boundary_annotation_rate_at_most_one_third": False,
    }
    assert report["passed"] is False


def test_twenty_application_corpus_meets_private_annotation_gate() -> None:
    report = measure_private_annotations(ROOT)

    assert report["project_count"] == 20
    assert report["private_function_count"] == 9
    assert report["annotation_slots"] == 21
    assert report["explicit_annotations"] == 6
    assert report["annotation_rate"] == 6 / 21
    assert report["passed"] is True
