from __future__ import annotations

from pathlib import Path

from merlo import cli
from merlo.public_benchmark import PublicBenchmarkOutputError


def test_benchmark_cli_returns_zero_only_for_measured_pass(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "run_public_benchmark", lambda root, output: {"status": "MEASURED", "passed": True})
    assert cli.main(["benchmark", "--output", str(tmp_path / "report.json")]) == 0


def test_benchmark_cli_returns_one_for_unmeasured(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "run_public_benchmark", lambda root, output: {"status": "UNMEASURED", "passed": False})
    assert cli.main(["benchmark", "--output", str(tmp_path / "report.json")]) == 1


def test_benchmark_cli_returns_two_for_unwritable_output(monkeypatch, tmp_path: Path) -> None:
    def fail(root, output):
        raise PublicBenchmarkOutputError("unwritable")
    monkeypatch.setattr(cli, "run_public_benchmark", fail)
    assert cli.main(["benchmark", "--output", str(tmp_path / "report.json")]) == 2
