from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from merlo.self_host import SelfHostStageError, SelfHostStatus, _stage_command, run_self_host


ROOT = Path(__file__).resolve().parents[1]


def test_real_three_stage_chain_emits_executables_and_converges() -> None:
    report = run_self_host(ROOT)
    assert report.status is SelfHostStatus.OBSERVED
    assert report.executable_stages_observed
    assert report.semantic_convergence == "OBSERVED"
    assert report.byte_convergence in {"OBSERVED", "DIVERGED"}
    assert len(report.stages) == 3
    assert Path(report.canonical_bundle).is_file()
    for stage in report.stages:
        assert Path(stage.executable).is_file()
        assert Path(stage.executable).stat().st_size > 0
        assert Path(stage.c_source_path).is_file()
        assert stage.command
        assert len(stage.artifact_digest) == 64
        assert len(stage.semantic_digest) == 64

def test_linux_stage_execution_is_resource_limited() -> None:
    command = _stage_command(Path("/tmp/compiler"))
    if shutil.which("prlimit"):
        assert "--as=1073741824" in command
        assert "--cpu=60" in command
    else:
        assert command == ("/tmp/compiler",)


def test_tampered_bootstrap_source_fails_at_observed_stage(tmp_path: Path) -> None:
    clone = tmp_path / "repo"
    shutil.copytree(ROOT / "selfhost", clone / "selfhost")
    shutil.copytree(ROOT / "src" / "merlo", clone / "src" / "merlo")
    (clone / "selfhost" / "src" / "main.mlo").write_text(
        (clone / "selfhost" / "src" / "main.mlo").read_text(encoding="utf-8")
        + "\nthis is not a supported declaration\n",
        encoding="utf-8",
    )
    with pytest.raises(SelfHostStageError) as raised:
        run_self_host(clone)
    assert raised.value.stage in {"stage0", "bundle"}
    assert raised.value.code in {"CompileFailed", "MissingModule"}


def test_missing_stage_input_is_an_exact_bundle_error(tmp_path: Path) -> None:
    clone = tmp_path / "repo"
    (clone / "selfhost" / "src").mkdir(parents=True)
    (clone / "selfhost" / "src" / "main.mlo").write_text("module main\n", encoding="utf-8")
    with pytest.raises(SelfHostStageError) as raised:
        run_self_host(clone)
    assert raised.value.stage == "bundle"
    assert raised.value.code == "MissingModule"
