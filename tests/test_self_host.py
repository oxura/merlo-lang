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
    SelfHostReport,
    run_self_host,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def bootstrap_report() -> SelfHostReport:
    return run_self_host(ROOT)


def test_real_three_stage_chain_compiles_and_runs_consumers(
    tmp_path: Path, bootstrap_report: SelfHostReport,
) -> None:
    report = bootstrap_report
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
    "declaration",
    (
        "unused(value: Vec[Missing]) -> Unit:\n    return\n",
        "unused(value: Vec[UInt64, Text]) -> Unit:\n    return\n",
        "unused(value: Result[Text]) -> Unit:\n    return\n",
        "unused(value: Result[Text, Missing]) -> Unit:\n    return\n",
        "unused(value: Map[Text]) -> Unit:\n    return\n",
        "unused(value: Map[Text, Vec[Missing]]) -> Unit:\n    return\n",
        "unused() -> Vec[Missing]:\n    return\n",
        "unused() -> Result[Text]:\n    return\n",
        "Envelope:\n    values: Vec[Result[UInt64, Missing]]\n",
        "Envelope:\n    values: Vec[UInt64, Text]\n",
        "enum Failure:\n    Details: Result[Text]\n",
        "enum Failure:\n    Details: Map[Missing, UInt64]\n",
    ),
)
def test_native_stages_reject_invalid_declaration_types(
    tmp_path: Path, bootstrap_report: SelfHostReport, declaration: str,
) -> None:
    consumer = (
        f"module consumer\n\n{declaration}\n"
        'main(input: Text) -> Text:\n    "accepted invalid type\\n"\n'
    )
    for stage in bootstrap_report.stages:
        emitted = subprocess.run(
            [stage.executable], input=consumer.encode(), capture_output=True,
            check=True, env={**os.environ, "PATH": ""}, timeout=30,
        )
        compiled = compile_c_source(
            emitted.stdout.decode(), output_dir=tmp_path,
            stem=f"invalid-stage{stage.number}",
        )
        assert compiled.status == "FAILED"


def test_native_stages_accept_nested_spaced_nominal_types(
    tmp_path: Path, bootstrap_report: SelfHostReport,
) -> None:
    consumer = (
        "module consumer\n\n"
        "enum Failure:\n    Message: Text\n\n"
        "Envelope:\n"
        "    entries: Vec [ Result [ Vec [UInt64], Failure ] ]\n\n"
        "unused(value: Map [ Text, Vec [ Envelope ] ]) -> Unit:\n"
        "    return\n\n"
        'main(input: Text) -> Text:\n    "nested-types-ok\\n"\n'
    )
    for stage in bootstrap_report.stages:
        emitted = subprocess.run(
            [stage.executable], input=consumer.encode(), capture_output=True,
            check=True, env={**os.environ, "PATH": ""}, timeout=30,
        )
        compiled = compile_c_source(
            emitted.stdout.decode(), output_dir=tmp_path,
            stem=f"nested-stage{stage.number}",
        )
        assert compiled.status == "MEASURED", compiled.stderr
        result = subprocess.run(
            [compiled.binary_path], input=b"", capture_output=True,
            check=True, timeout=30,
        )
        assert result.stdout == b"nested-types-ok\n"


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
