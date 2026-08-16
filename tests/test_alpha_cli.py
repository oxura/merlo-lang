from __future__ import annotations

import json
from pathlib import Path

from merlo.cli import EXIT_DIAGNOSTIC, EXIT_OK, build_parser, main
from merlo.package import package_from_root
from merlo.project import Project
from merlo.test_runner import run_project_tests


def test_parser_has_project_namespace() -> None:
    parser = build_parser()
    cases = {
        "new": ["new"],
        "check": ["check"],
        "build": ["build"],
        "run": ["run"],
        "test": ["test"],
        "fmt": ["fmt"],
        "expand": ["expand"],
        "explain": ["explain"],
        "doc": ["doc"],
        "map": ["map"],
        "inspect": ["inspect", "main"],
        "refs": ["refs", "main"],
        "callers": ["callers", "main"],
        "callees": ["callees", "main"],
        "deps": ["deps", "main"],
        "impact": ["impact", "main"],
        "why": ["why", "UnknownSymbol"],
        "context": ["context", "main"],
        "refactor": ["refactor", "rename", "main", "renamed"],
        "add": ["add", "--path", "lib", "../lib"],
    }
    for command, arguments in cases.items():
        assert parser.parse_args(arguments).command == command
    options = parser.parse_args(
        [
            "check",
            "--smt",
            "z3",
            "--smt-timeout-ms",
            "25",
            "--smt-max-paths",
            "12",
        ]
    )
    assert options.smt == "z3"
    assert options.smt_timeout_ms == 25
    assert options.smt_max_paths == 12


def test_new_json_is_deterministic_and_discovers_source_project(tmp_path: Path, capsys) -> None:
    root = tmp_path / "demo"
    assert main(["new", str(root), "--json"]) == EXIT_OK
    created = json.loads(capsys.readouterr().out)
    assert created["ok"] is True
    source = root / "src" / "main.mlo"
    assert main(["check", str(source), "--json"]) == EXIT_OK
    checked = json.loads(capsys.readouterr().out)
    assert checked["ok"] is True
    assert checked["entry_path"] == str(source)


def test_refactor_move_cli_previews_then_applies_verified_lineage(
    tmp_path: Path,
    capsys,
) -> None:
    project = Project.create(tmp_path / "move_cli", name="move_cli")
    source = project.source_dir / "main.mlo"
    support = project.source_dir / "support.mlo"
    source.write_text(
        "module main\n"
        "use support\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn helper(value: Text) -> Text:\n"
        "    return value\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        '    console.write(helper("move-cli"))\n'
        '    return Ok(helper("ok"))\n',
        encoding="utf-8",
    )
    support.write_text(
        "module support\n\n"
        "export fn existing(value: UInt64) -> UInt64:\n"
        "    return value\n",
        encoding="utf-8",
    )

    command = [
        "refactor",
        "move",
        "main.helper",
        "support",
        str(project.root),
        "--json",
    ]
    assert main(command) == EXIT_OK
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "ready"
    assert preview["metadata"]["old_symbol_id"] != (
        preview["metadata"]["new_symbol_id"]
    )
    assert "fn helper" in source.read_text(encoding="utf-8")

    assert main([*command, "--apply"]) == EXIT_OK
    applied = json.loads(capsys.readouterr().out)
    assert applied["committed"] is True
    assert applied["lineage"] == {
        "old_symbol_id": preview["metadata"]["old_symbol_id"],
        "new_symbol_id": preview["metadata"]["new_symbol_id"],
    }
    assert "fn helper" not in source.read_text(encoding="utf-8")
    assert "export fn helper" in support.read_text(encoding="utf-8")


def test_check_exposes_optional_smt_outcome(tmp_path: Path, capsys) -> None:
    project = Project.create(tmp_path / "smt", name="smt")
    source = project.source_dir / "main.mlo"
    original = source.read_text(encoding="utf-8")
    module, body = original.split("\n", 1)
    source.write_text(
        module
        + "\n\nfn identity(value: Byte) -> Byte:\n"
        "    ensure result == value\n"
        "    value\n\n"
        + body,
        encoding="utf-8",
    )

    assert main(
        ["check", str(source), "--smt", "z3", "--json"]
    ) == EXIT_OK
    checked = json.loads(capsys.readouterr().out)
    smt = checked["compiler"]["smt"]
    assert smt["backend"] == "z3"
    assert smt["result_count"] == 1
    assert smt["timeout_ms"] == 1000
    assert smt["max_paths"] == 256
    assert "backend_version" in smt
    assert smt["results"][0]["status"] in {
        "proven",
        "unavailable",
    }


def test_editing_application_source_keeps_lock_fresh_for_check_and_build(tmp_path: Path, capsys) -> None:
    root = tmp_path / "demo"
    assert main(["new", str(root), "--json"]) == EXIT_OK
    capsys.readouterr()

    source = root / "src" / "main.mlo"
    source.write_text(
        source.read_text(encoding="utf-8").replace('console.write("ok")', 'console.write("hello")'),
        encoding="utf-8",
    )

    assert main(["check", str(source), "--json"]) == EXIT_OK
    checked = json.loads(capsys.readouterr().out)
    assert checked["ok"] is True

    assert main(["build", str(root), "--json"]) == EXIT_OK
    built = json.loads(capsys.readouterr().out)
    assert built["ok"] is True


def test_build_wasm_target_writes_selected_pure_entry(tmp_path: Path, capsys) -> None:
    project = Project.create(tmp_path / "wasm", name="wasm")
    source = project.source_dir / "main.mlo"
    module, body = source.read_text(encoding="utf-8").split("\n", 1)
    source.write_text(
        module + "\n\nfn value() -> UInt64:\n    7\n\n" + body,
        encoding="utf-8",
    )
    output = tmp_path / "value.wasm"

    assert main(
        [
            "build",
            str(project.root),
            "--target",
            "wasm",
            "--entry",
            "value",
            "--output",
            str(output),
            "--json",
        ]
    ) == EXIT_OK
    built = json.loads(capsys.readouterr().out)
    assert built["ok"] is True
    assert built["exports"] == ["value"]
    assert output.read_bytes().startswith(b"\0asm\1\0\0\0")


def test_legacy_root_content_hash_lock_allows_unchanged_and_edited_check(tmp_path: Path, capsys) -> None:
    root = tmp_path / "legacy"
    project = Project.create(root, name="legacy")
    raw = json.loads(project.lock_path.read_text(encoding="utf-8"))
    root_record = next(record for record in raw["packages"] if record["name"] == "legacy")
    root_record["source_hash"] = package_from_root(root).content_hash()
    project.lock_path.write_text(json.dumps(raw), encoding="utf-8")
    source = root / "src" / "main.mlo"

    assert main(["check", str(source), "--json"]) == EXIT_OK
    unchanged = json.loads(capsys.readouterr().out)
    assert unchanged["ok"] is True

    source.write_text(
        source.read_text(encoding="utf-8").replace('console.write("ok")', 'console.write("edited")'),
        encoding="utf-8",
    )
    assert main(["check", str(source), "--json"]) == EXIT_OK
    edited = json.loads(capsys.readouterr().out)
    assert edited["ok"] is True


def test_new_project_run_defaults_its_required_path_argument(tmp_path: Path, capfd) -> None:
    root = tmp_path / "demo"
    assert main(["new", str(root), "--json"]) == EXIT_OK
    capfd.readouterr()

    assert main(["run", str(root), "--json"]) == EXIT_OK
    output = capfd.readouterr().out
    assert '"ok":true' in output
    assert '"returncode":0' in output


def test_source_shorthand_still_validates_ancestor_lock(tmp_path: Path, capsys) -> None:
    project = Project.create(tmp_path / "app", name="app")
    project.manifest_path.write_text(
        project.manifest_path.read_text(encoding="utf-8").replace(
            'version = "0.1.0"', 'version = "0.1.1"'
        ),
        encoding="utf-8",
    )

    assert main(["check", str(project.source_dir / "main.mlo"), "--json"]) == EXIT_DIAGNOSTIC
    payload = json.loads(capsys.readouterr().out)
    assert "StaleLockfile" in payload["diagnostics"][0]["message"]
    assert payload["diagnostics"][0]["message"].endswith("merlo.lock")




def test_empty_project_test_suite_is_successful(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "app", name="app")
    report = run_project_tests(project)
    assert report.to_dict()["ok"] is True
    assert report.failed == 0


def test_project_test_runner_passes_required_path_argument(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "app", name="app")
    test_source = project.tests_dir / "test_case.mlo"
    test_source.write_text(
        "module test_case\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"ok\")\n"
        "    return Ok(\"ok\")\n",
        encoding="utf-8",
    )
    report = run_project_tests(project)
    result = report.tests[0]
    assert result.status == "passed"
    assert result.command[-1] == str(test_source)


def test_fmt_check_reports_diagnostic_exit_code(tmp_path: Path, capsys) -> None:
    project = Project.create(tmp_path / "app", name="app")
    source = project.source_dir / "main.mlo"
    code = main(["fmt", str(source), "--check", "--json"])
    assert code in (EXIT_OK, EXIT_DIAGNOSTIC)
    payload = json.loads(capsys.readouterr().out)
    assert {"ok", "changed", "path"} <= payload.keys()
