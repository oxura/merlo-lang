from __future__ import annotations

import json
from pathlib import Path

from merlo.cli import EXIT_DIAGNOSTIC, EXIT_OK, build_parser, main
from merlo.project import Project


def _project(tmp_path: Path) -> tuple[Project, Path, Path]:
    project = Project.create(tmp_path / "evolve-cli", name="evolve_cli")
    library = project.source_dir / "billing.mlo"
    library.write_text(
        "module billing\n\n"
        "export score(value: UInt64) -> UInt64:\n"
        "    require value > 0\n"
        "    ensure result >= value\n"
        "    value\n",
        encoding="utf-8",
    )
    application = project.source_dir / "main.mlo"
    application.write_text(
        "module main\n\n"
        "use billing\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    let value = billing.score(7)\n"
        "    console.write(\"evolved\")\n"
        "    Ok(\"evolved\")\n",
        encoding="utf-8",
    )
    return project, library, application


def test_parser_exposes_evolution_commands() -> None:
    parser = build_parser()
    rename = parser.parse_args(
        ["evolve", "rename", "billing.score", "rank"]
    )
    assert rename.command == "evolve"
    assert rename.evolution_operation == "rename"
    apply = parser.parse_args(["evolve", "apply", "plan.json"])
    assert apply.evolution_operation == "apply"


def test_multimodule_evolution_plan_applies_with_preservation_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    project, library, application = _project(tmp_path)
    plan_path = project.root / ".merlo" / "rename-plan.json"
    before_library = library.read_text(encoding="utf-8")
    before_application = application.read_text(encoding="utf-8")

    command = [
        "evolve",
        "rename",
        "billing.score",
        "rank",
        str(project.root),
        "--goal",
        "rename without changing behavior",
        "--plan-out",
        str(plan_path),
        "--json",
    ]
    assert main(command) == EXIT_OK
    preview_text = capsys.readouterr().out
    preview = json.loads(preview_text)
    assert preview["status"] == "preview"
    assert preview["plan_path"] == str(plan_path)
    assert preview["plan"]["impact"]["status"] == "ready"
    assert len(preview["plan"]["impact"]["directly_changed"]) == 1
    assert len(preview["plan"]["impact"]["transitively_affected"]) == 1
    assert len(preview["plan"]["impact"]["files"]) == 2
    assert library.read_text(encoding="utf-8") == before_library
    assert application.read_text(encoding="utf-8") == before_application

    saved_plan = plan_path.read_text(encoding="utf-8")
    assert main(command) == EXIT_OK
    assert capsys.readouterr().out == preview_text
    assert plan_path.read_text(encoding="utf-8") == saved_plan

    assert main(
        ["evolve", "apply", str(plan_path), str(project.root), "--json"]
    ) == EXIT_OK
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "committed"
    result = applied["result"]
    assert result["preservation"]["overall"] == "preserved"
    assert result["evidence"]["claims"]
    assert result["transaction"]["action"] == "commit"
    assert "export rank" in library.read_text(encoding="utf-8")
    assert "billing.rank(7)" in application.read_text(encoding="utf-8")
    assert main(["check", str(project.root), "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_tampered_evolution_plan_is_rejected_without_source_writes(
    tmp_path: Path,
    capsys,
) -> None:
    project, library, application = _project(tmp_path)
    plan_path = project.root / ".merlo" / "rename-plan.json"
    assert main(
        [
            "evolve",
            "rename",
            "billing.score",
            "rank",
            str(project.root),
            "--plan-out",
            str(plan_path),
            "--json",
        ]
    ) == EXIT_OK
    capsys.readouterr()
    before = (library.read_bytes(), application.read_bytes())
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["change_ir"]["metadata"]["new_name"] = "tampered"
    plan_path.write_text(json.dumps(payload), encoding="utf-8")

    assert main(
        ["evolve", "apply", str(plan_path), str(project.root), "--json"]
    ) == EXIT_DIAGNOSTIC
    failure = json.loads(capsys.readouterr().out)
    assert failure["ok"] is False
    assert "DigestMismatch" in failure["diagnostics"][0]["message"]
    assert (library.read_bytes(), application.read_bytes()) == before
