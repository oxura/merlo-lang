from __future__ import annotations

import json
from pathlib import Path

from merlo.cli import EXIT_DIAGNOSTIC, EXIT_OK, build_parser, main
from merlo.compiler import compile_project
from merlo.docgen import generate_documentation
from merlo.project import Project
from merlo.semantic_world import SemanticWorld
from merlo.test_runner import run_project_tests


def test_parser_has_project_and_historical_namespaces() -> None:
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
        "historical": ["historical", "bench"],
    }
    for command, arguments in cases.items():
        assert parser.parse_args(arguments).command == command
    assert parser.parse_args(["historical", "bench"]).command == "historical"


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


def test_world_queries_and_docs_use_exact_public_interfaces(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "app", name="app")
    compilation = compile_project(project.root, require_interface_lock=False)
    world = SemanticWorld.build(compilation, state_path=project.root / ".merlo" / "world.json", lockfile=project.lock_path, require_interface_lock=False)
    documentation = generate_documentation(world)
    assert documentation.digest == world.digest
    assert "main" in documentation.markdown
    assert world.resolve("main")["symbol_id"] in documentation.markdown


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
