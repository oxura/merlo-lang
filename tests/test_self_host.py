from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from merlo.native_c_backend import compile_c_source
from merlo.self_host import (
    SelfHostStageError,
    SelfHostStatus,
    _require_c_source_convergence,
    _stage_command,
    run_self_host,
)


ROOT = Path(__file__).resolve().parents[1]


def test_real_three_stage_chain_compiles_and_runs_consumers(tmp_path: Path) -> None:
    report = run_self_host(ROOT)
    assert report.status is SelfHostStatus.OBSERVED
    assert report.c_source_convergence == "OBSERVED"
    assert report.executable_convergence == "OBSERVED"
    sources = tuple(Path(stage.c_source_path).read_bytes() for stage in report.stages)
    assert sources[0] == sources[1] == sources[2]
    assert (
        Path(report.stages[1].executable).read_bytes()
        == Path(report.stages[2].executable).read_bytes()
    )
    consumer = (
        'module consumer\n\n'
        'increment(value: UInt64) -> UInt64:\n'
        '    value + 1\n\n'
        'main(input: Text) -> Text:\n'
        '    if increment(41) == 42:\n'
        '        return "a b // c /* d */\\n"\n'
        '    else:\n'
        '        return input\n'
    )
    for stage in report.stages:
        emitted = subprocess.run(
            [stage.executable], input=consumer.encode(), capture_output=True,
            check=True, env={**os.environ, "PATH": ""}, timeout=30,
        )
        compiled = compile_c_source(
            emitted.stdout.decode(), output_dir=tmp_path,
            stem=f"consumer-stage{stage.number}",
        )
        assert compiled.status == "MEASURED", compiled.stderr
        result = subprocess.run(
            [compiled.binary_path], input=b"unexpected branch\n",
            capture_output=True, check=True, timeout=30,
        )
        assert result.stdout == b"a b // c /* d */\n"


@pytest.mark.parametrize(
    ("original", "changed"),
    (
        (b'puts("a b");', b'puts("ab");'),
        (b'puts("http://one");', b'puts("http://two");'),
        (b'puts("a/*one*/b");', b'puts("a/*two*/b");'),
        (b"#define VALUE 1\n+2\n", b"#define VALUE 1+2\n"),
    ),
)
def test_stage_convergence_rejects_literal_and_preprocessor_changes(
    original: bytes, changed: bytes,
) -> None:
    with pytest.raises(SelfHostStageError) as raised:
        _require_c_source_convergence((original, changed, original))
    assert raised.value.stage == "convergence"
    assert raised.value.code == "NonConvergentCSource"


@pytest.mark.skipif(not shutil.which("prlimit"), reason="Linux prlimit unavailable")
def test_stage_execution_enforces_resource_limits() -> None:
    import sys

    command = _stage_command(Path(sys.executable))
    process = subprocess.run(
        [*command, "-c",
         "import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0]); "
         "print(resource.getrlimit(resource.RLIMIT_CPU)[0])"],
        capture_output=True, text=True, check=True, timeout=10,
    )
    assert process.stdout.splitlines() == ["1073741824", "60"]


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
