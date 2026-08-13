from __future__ import annotations

from pathlib import Path

from merlo import scan_python
from merlo.language_experiment import (
    aggregate_language_coverage,
    measure_language_coverage,
)


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_language_experiment_classifies_without_claiming_implementation(
    tmp_path: Path,
):
    _write(
        tmp_path,
        "plugin.py",
        (
            "def dispatch(target):\n"
            "    handler = getattr(target, 'run')\n"
            "    return handler()\n"
        ),
    )
    report = measure_language_coverage(scan_python(tmp_path), project="plugin")
    payload = report.to_dict()

    assert payload["status"] == "theoretical_upper_bound"
    assert payload["uncertain_references"] > 0
    assert report.addressable_upper_bound <= report.uncertain_references
    assert report.unclassified_uncertainty >= 0
    assert "do not measure" in payload["claim"]
    assert any(item.affected_references > 0 for item in report.hypotheses)


def test_language_aggregate_keeps_raw_denominators(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write(first, "a.py", "def stable(value):\n    return value\n")
    _write(
        second,
        "b.py",
        "def dynamic(obj, name):\n    return getattr(obj, name)()\n",
    )

    aggregate = aggregate_language_coverage(
        (
            measure_language_coverage(scan_python(first), project="first"),
            measure_language_coverage(scan_python(second), project="second"),
        )
    )

    assert aggregate["projects"] == 2
    assert aggregate["addressable_upper_bound"]["denominator"] == aggregate[
        "uncertain_references"
    ]
    assert aggregate["addressable_upper_bound"]["count"] <= aggregate[
        "uncertain_references"
    ]
    assert aggregate["claim"] == "No language implementation was built or measured."
