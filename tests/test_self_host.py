from __future__ import annotations

import os
import shutil
import subprocess
import sys
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
def development_bootstrap_report() -> SelfHostReport:
    return run_self_host(ROOT)


@pytest.fixture(scope="module", params=("development_library", "external_cli"))
def bootstrap_report(request: pytest.FixtureRequest) -> SelfHostReport:
    if request.param == "development_library":
        return request.getfixturevalue("development_bootstrap_report")
    published_cli = os.environ.get("MERLO_SELF_HOST_STAGE0")
    stage0_command = (
        (published_cli,) if published_cli else (sys.executable, "-m", "merlo")
    )
    return run_self_host(ROOT, stage0_command=stage0_command)


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
        'module consumer.app\n\n'
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


def test_native_stages_preserve_newly_accepted_text_escapes(
    tmp_path: Path, bootstrap_report: SelfHostReport,
) -> None:
    assert tuple(stage.number for stage in bootstrap_report.stages) == (1, 2, 3)
    consumer = (
        "module consumer\n\n"
        "main(input: Text) -> Text:\n"
        r'    "\x41\a\v\101\U0001F600"' + "\n"
    )
    for stage in bootstrap_report.stages:
        emitted = subprocess.run(
            [stage.executable], input=consumer.encode(), capture_output=True,
            check=True, env={**os.environ, "PATH": ""}, timeout=30,
        )
        compiled = compile_c_source(
            emitted.stdout.decode(), output_dir=tmp_path,
            stem=f"escaped-text-stage{stage.number}",
        )
        assert compiled.status == "MEASURED", compiled.stderr
        result = subprocess.run(
            [compiled.binary_path], input=b"unused", capture_output=True,
            check=True, timeout=30, env={"PATH": ""},
        )
        assert result.stdout == "A\a\vA😀".encode()
        assert result.stderr == b""


def test_native_stages_compile_hexadecimal_integer_arithmetic(
    tmp_path: Path, bootstrap_report: SelfHostReport,
) -> None:
    assert tuple(stage.number for stage in bootstrap_report.stages) == (1, 2, 3)
    consumer = (
        "module consumer\n\n"
        "main(input: Text) -> Text:\n"
        "    if 0x2A + 0x10 == 58:\n"
        '        return "hex-ok\\n"\n'
        "    else:\n"
        '        return "wrong\\n"\n'
    )
    for stage in bootstrap_report.stages:
        emitted = subprocess.run(
            [stage.executable], input=consumer.encode(), capture_output=True,
            check=True, env={**os.environ, "PATH": ""}, timeout=30,
        )
        compiled = compile_c_source(
            emitted.stdout.decode(), output_dir=tmp_path,
            stem=f"hexadecimal-stage{stage.number}",
        )
        assert compiled.status == "MEASURED", compiled.stderr
        result = subprocess.run(
            [compiled.binary_path], input=b"unused", capture_output=True,
            check=True, timeout=30, env={"PATH": ""},
        )
        assert result.stdout == b"hex-ok\n"
        assert result.stderr == b""


def test_native_stages_evaluate_bitwise_xor(
    tmp_path: Path, bootstrap_report: SelfHostReport,
) -> None:
    assert tuple(stage.number for stage in bootstrap_report.stages) == (1, 2, 3)
    consumer = (
        "module consumer\n\n"
        "main(input: Text) -> Text:\n"
        "    if (input.len() ^ 3) == 5 and (7 ^ 7) == 0 and "
        "(input.len() | 1 ^ 3) == 6 and (input.len() ^ 3 & 1) == 7:\n"
        '        return "xor-ok\\n"\n'
        "    else:\n"
        '        return "other\\n"\n'
    )
    for stage in bootstrap_report.stages:
        emitted = subprocess.run(
            [stage.executable], input=consumer.encode(), capture_output=True,
            check=True, env={**os.environ, "PATH": ""}, timeout=30,
        )
        compiled = compile_c_source(
            emitted.stdout.decode(), output_dir=tmp_path,
            stem=f"xor-stage{stage.number}",
        )
        assert compiled.status == "MEASURED", compiled.stderr
        for payload, expected in ((b"123456", b"xor-ok\n"), (b"1234", b"other\n")):
            result = subprocess.run(
                [compiled.binary_path], input=payload, capture_output=True,
                check=True, timeout=30, env={"PATH": ""},
            )
            assert result.stdout == expected
            assert result.stderr == b""


def test_native_stages_preserve_shift_boundaries_and_precedence(
    tmp_path: Path, bootstrap_report: SelfHostReport,
) -> None:
    assert tuple(stage.number for stage in bootstrap_report.stages) == (1, 2, 3)
    consumer = (
        "module consumer\n\n"
        "main(input: Text) -> Text:\n"
        "    if (input.len() << 1) == 12 and (16 >> 2) == 4 and "
        "input.len() << 1 < 13 and input.len() << 1 >= 12 and "
        "input.len() < 1 << 3 and (256 >> 2 >> 2) == 16 and "
        "(input.len() + 2 << 1) == 16 and (input.len() << 1 ^ 1) == 13 and "
        "(16 << 1 >> 2) == 8 and (16 >> 2 << 1) == 8:\n"
        '        return "shift-ok\\n"\n'
        "    else:\n"
        '        return "other\\n"\n'
    )
    for stage in bootstrap_report.stages:
        emitted = subprocess.run(
            [stage.executable], input=consumer.encode(), capture_output=True,
            check=True, env={**os.environ, "PATH": ""}, timeout=30,
        )
        compiled = compile_c_source(
            emitted.stdout.decode(), output_dir=tmp_path,
            stem=f"shift-stage{stage.number}",
        )
        assert compiled.status == "MEASURED", compiled.stderr
        for payload, expected in ((b"123456", b"shift-ok\n"), (b"1234", b"other\n")):
            result = subprocess.run(
                [compiled.binary_path], input=payload, capture_output=True,
                check=True, timeout=30, env={"PATH": ""},
            )
            assert result.stdout == expected
            assert result.stderr == b""


def test_native_parser_consumes_one_trivia_preserving_stream(
    tmp_path: Path, bootstrap_report: SelfHostReport,
) -> None:
    consumer = (
        '# before the module\r\n'
        'module consumer  # module comment\r\n'
        '\r\n'
        'Envelope :  # record comment\r\n'
        '    name : Text  # field comment\r\n'
        '\r\n'
        '# between declarations\r\n'
        'main \t ( input : Text ) -> Text :  # header comment\r\n'
        '    "# remains literal\\n"\r\n'
        '# final comment without a newline'
    )
    for stage in bootstrap_report.stages:
        emitted = subprocess.run(
            [stage.executable], input=consumer.encode(), capture_output=True,
            check=True, env={**os.environ, "PATH": ""}, timeout=30,
        )
        compiled = compile_c_source(
            emitted.stdout.decode(), output_dir=tmp_path,
            stem=f"retained-trivia-stage{stage.number}",
        )
        assert compiled.status == "MEASURED", compiled.stderr
        result = subprocess.run(
            [compiled.binary_path], input=b"unused\n", capture_output=True,
            check=True, timeout=30, env={"PATH": ""},
        )
        assert result.stdout == b"# remains literal\n"
        assert result.stderr == b""


@pytest.mark.parametrize("compiler_name", ("clang", "gcc"))
def test_development_seed_emits_complete_bundle_without_leaks(
    tmp_path: Path, development_bootstrap_report: SelfHostReport, compiler_name: str,
) -> None:
    compiler = shutil.which(compiler_name)
    if compiler is None:
        pytest.skip(f"{compiler_name} is required for the bootstrap sanitizer regression")
    report = development_bootstrap_report
    source = next(
        argument for argument in report.stages[0].command if argument.endswith(".c")
    )
    binary = tmp_path / "compiler-sanitized"
    built = subprocess.run(
        [
            compiler, "-std=c11", "-O1", "-g", "-fno-omit-frame-pointer",
            "-fsanitize=address,undefined", source, "-o", str(binary),
        ],
        capture_output=True, text=True, check=False, timeout=120,
    )
    assert built.returncode == 0, built.stderr
    emitted = subprocess.run(
        [str(binary)], input=Path(report.canonical_bundle).read_bytes(),
        capture_output=True, check=False, timeout=60,
        env={
            **os.environ,
            "ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1",
            "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
        },
    )
    assert emitted.returncode == 0, emitted.stderr.decode(errors="replace")
    assert emitted.stdout == Path(report.stages[0].c_source_path).read_bytes()


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


def test_missing_external_stage0_cannot_use_development_compiler(tmp_path: Path) -> None:
    with pytest.raises(SelfHostStageError) as raised:
        run_self_host(ROOT, stage0_command=(str(tmp_path / "unavailable-merlo"),))
    assert raised.value.stage == "stage0"
    assert raised.value.code == "ExecutionFailed"
