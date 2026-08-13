from __future__ import annotations

from pathlib import Path

from merlo.productive_simplicity import (
    APPLICATIONS,
    ARMS,
    audit_productive_simplicity,
    extract_source_metrics,
)


def _write_arm(root: Path, application: str, arm: str, source: str) -> None:
    if arm == "concise_merlo":
        path = root / "merlo" / "programs" / f"productive_{application}" / "app" / "main.mlo"
    elif arm == "canonical_merlo":
        path = root / "merlo" / "programs" / f"productive_{application}" / "canonical" / "main.mlo"
    else:
        suffix = {"python": ".py", "c": ".c"}[arm]
        path = root / "benchmarks" / "productive_simplicity" / application / f"reference{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_extract_source_metrics_counts_a_controlled_surface() -> None:
    source = "fn f(value: Text) -> UInt64:\n    return 1\n"

    metrics = extract_source_metrics(source)

    assert metrics == {
        "lexical_tokens": 7,
        "punctuation_tokens": 6,
        "lines": 2,
        "source_bytes": len(source.encode("utf-8")),
        "explicit_type_annotations": 2,
        "lifetime_annotations": 0,
        "ownership_operations": 0,
        "manual_resource_operations": 0,
        "error_handling_boilerplate": 0,
        "dynamic_any": 0,
        "distinct_constructs": 2,
        "constructs": ["fn", "return"],
        "nesting_depth": 1,
    }


def test_extract_source_metrics_detects_forbidden_source_constructs() -> None:
    source = """fn borrow<'a>(value: &'a Any):
    owned = move(value)
    raw = malloc(8)
    fclose(raw)
    try:
        raise Error
"""

    metrics = extract_source_metrics(source)

    assert metrics["lifetime_annotations"] == 2
    assert metrics["ownership_operations"] == 1
    assert metrics["manual_resource_operations"] == 2
    assert metrics["error_handling_boilerplate"] == 2
    assert metrics["dynamic_any"] == 1


def test_audit_reports_every_application_and_arm_without_synthesizing_sources(tmp_path: Path) -> None:
    _write_arm(tmp_path, "ndjson", "concise_merlo", "fn main():\n    return 0\n")
    _write_arm(tmp_path, "ndjson", "python", "def main():\n    return 0\n")

    report = audit_productive_simplicity(tmp_path)

    assert [item["application"] for item in report["applications"]] == list(APPLICATIONS)
    assert all(tuple(item["arms"]) == ARMS for item in report["applications"])
    ndjson = report["applications"][0]
    assert ndjson["arms"]["concise_merlo"]["status"] == "MEASURED"
    assert ndjson["arms"]["python"]["status"] == "MEASURED"
    assert ndjson["arms"]["canonical_merlo"] == {
        "status": "UNMEASURED",
        "reason": "SOURCE_ARTIFACT_NOT_FOUND",
        "source_files": [],
        "metrics": None,
    }
    assert ndjson["arms"]["c"]["status"] == "UNMEASURED"
    assert report["applications"][1]["ratios"] == {}
    assert report["applications"][2]["ratios"] == {}
    assert report["ratio_medians"]["measured_application_count"] == 1


def test_audit_measures_every_merlo_module(tmp_path: Path) -> None:
    _write_arm(tmp_path, "ndjson", "concise_merlo", "fn main():\n    return 0\n")
    module = tmp_path / "merlo/programs/productive_ndjson/app/report.mlo"
    module.write_text("fn report():\n    return 1\n", encoding="utf-8")
    _write_arm(tmp_path, "ndjson", "python", "def main():\n    return 0\n")

    concise = audit_productive_simplicity(tmp_path)["applications"][0]["arms"]["concise_merlo"]

    assert concise["source_files"] == [
        "merlo/programs/productive_ndjson/app/main.mlo",
        "merlo/programs/productive_ndjson/app/report.mlo",
    ]
    assert concise["metrics"]["lexical_tokens"] == 8


def test_ratios_are_per_application_before_the_order_independent_median(tmp_path: Path) -> None:
    concise_counts = {"ndjson": 8, "csv": 2, "grep": 6}
    for application, count in concise_counts.items():
        _write_arm(tmp_path, application, "concise_merlo", "a;" * count)
        _write_arm(tmp_path, application, "python", "a;" * 10)

    report = audit_productive_simplicity(tmp_path)
    by_application = {item["application"]: item for item in report["applications"]}

    assert by_application["ndjson"]["ratios"]["lexical_tokens_merlo_over_python"] == 0.8
    assert by_application["csv"]["ratios"]["lexical_tokens_merlo_over_python"] == 0.2
    assert by_application["grep"]["ratios"]["lexical_tokens_merlo_over_python"] == 0.6
    assert report["ratio_medians"] == {
        "measured_application_count": 3,
        "lexical_tokens_merlo_over_python": 0.6,
        "punctuation_tokens_merlo_over_python": 0.6,
    }


def test_zero_source_safety_gates_require_measured_clean_concise_sources(tmp_path: Path) -> None:
    for application in APPLICATIONS:
        _write_arm(tmp_path, application, "concise_merlo", "a;" * 2)
        _write_arm(tmp_path, application, "python", "a;" * 4)

    report = audit_productive_simplicity(tmp_path)

    assert report["gates"] == {
        "median_merlo_python_tokens_at_most_0_80": True,
        "median_merlo_python_punctuation_at_most_0_80": True,
        "concise_lifetime_annotations_zero": True,
        "concise_manual_resource_operations_zero": True,
        "concise_dynamic_any_zero": True,
    }
    assert report["passed"] is True


def test_forbidden_concise_source_fails_each_safety_gate(tmp_path: Path) -> None:
    forbidden = "fn f<'a>(value: &'a Any):\n    raw = malloc(1)\n    free(raw)\n    fclose(raw)\n"
    for application in APPLICATIONS:
        _write_arm(tmp_path, application, "concise_merlo", forbidden)
        _write_arm(tmp_path, application, "python", forbidden + "extra extra extra extra\n")

    gates = audit_productive_simplicity(tmp_path)["gates"]

    assert gates["concise_lifetime_annotations_zero"] is False
    assert gates["concise_manual_resource_operations_zero"] is False
    assert gates["concise_dynamic_any_zero"] is False


def test_empty_measured_baseline_does_not_create_a_ratio_or_a_passing_median_gate(tmp_path: Path) -> None:
    for application in APPLICATIONS:
        _write_arm(tmp_path, application, "concise_merlo", "")
        _write_arm(tmp_path, application, "python", "")

    report = audit_productive_simplicity(tmp_path)

    assert all(item["ratios"] == {} for item in report["applications"])
    assert report["ratio_medians"] == {
        "measured_application_count": 0,
        "lexical_tokens_merlo_over_python": None,
        "punctuation_tokens_merlo_over_python": None,
    }
    assert report["gates"]["median_merlo_python_tokens_at_most_0_80"] is False
    assert report["gates"]["median_merlo_python_punctuation_at_most_0_80"] is False
