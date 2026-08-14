from __future__ import annotations

import json

from research.archive.historical_protocol.merlo.cli import EXIT_DIAGNOSTIC, build_parser, main
from research.archive.alpha1.merlo.frontend_bench import DEFAULT_PROGRAM_COUNT
from research.archive.historical_protocol.merlo.frontend_evaluator import ReferenceEvaluator
from research.archive.historical_protocol.merlo.frontend_semantics import check_frontend, compile_frontend


def test_frontend_check_ir_and_run_use_current_apis(tmp_path):
    source = (
        "package demo.main\n"
        "export add\n"
        "fn add(value: Int) -> Int:\n"
        "    value + 2\n"
    )
    (tmp_path / "main.mlo").write_text(source, encoding="utf-8")
    sources = {"main.mlo": source}

    checked = check_frontend(sources)
    assert checked.ok is True
    assert checked.compilation is not None
    assert checked.compilation.hir.to_dict()["binding_counts"]["unknown_internal"] == 0

    compilation = compile_frontend(sources)
    core = compilation.core_program.to_dict()
    assert core["schema_version"] == 1
    assert core["packages"][0]["name"] == "demo"

    evaluated = ReferenceEvaluator(compilation).evaluate(
        "demo.main.add", {"value": 3}
    )
    assert evaluated.value == 5
    assert evaluated.effect_trace == ()


def test_frontend_evaluator_materializes_only_explicit_capability_handlers(
    tmp_path,
):
    source = (
        "package demo.main\n"
        "export Clock, now\n"
        "capability Clock:\n"
        "    read() -> Int uses clock.read\n"
        "task now(clock: cap Clock) -> Int:\n"
        "    uses clock.read\n"
        "    clock.read()\n"
    )
    (tmp_path / "main.mlo").write_text(source, encoding="utf-8")
    evaluator = ReferenceEvaluator(
        compile_frontend({"main.mlo": source}),
        handlers={"clock.read": lambda: 41},
    )

    evaluated = evaluator.evaluate(
        "demo.main.now",
        {"clock": evaluator.capability("demo.main.Clock")},
    )
    assert evaluated.value == 41
    assert [item.effect for item in evaluated.effect_trace] == ["clock.read"]


def test_historical_frontend_check_returns_diagnostic_exit_code(tmp_path, capsys):
    (tmp_path / "bad.mlo").write_text(
        "package demo.bad\nexport bad\nfn bad() -> Int:\n    missing\n",
        encoding="utf-8",
    )

    assert main(
        ["historical", "frontend-check", str(tmp_path), "--compact"]
    ) == EXIT_DIAGNOSTIC
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert [item["code"] for item in payload["diagnostics"]] == [
        "UnknownBinding"
    ]


def test_frontend_benchmark_is_isolated_in_historical_namespace():
    args = build_parser().parse_args(
        ["historical", "frontend-bench", "--compact"]
    )
    assert args.command == "historical"
    assert args.historical_command == "frontend-bench"
    assert 30 <= DEFAULT_PROGRAM_COUNT <= 50


def test_cli_uses_merlo_name_and_accepts_only_mlo_sources(tmp_path, capsys):
    assert build_parser().prog == "merlo"
    legacy = tmp_path / "legacy.meldra"
    legacy.write_text(
        "package demo.main\nexport value\nfn value() -> Int:\n    1\n",
        encoding="utf-8",
    )

    assert main(["check", str(legacy)]) == EXIT_DIAGNOSTIC
    error = capsys.readouterr().err
    assert "merlo: ValueError:" in error
    assert "expected .mlo source" in error
