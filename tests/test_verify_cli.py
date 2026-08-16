from __future__ import annotations

import json
from pathlib import Path

from merlo.cli import EXIT_DIAGNOSTIC, EXIT_OK, build_parser, main
from merlo.project import Project


def _project(tmp_path: Path, source: str) -> Project:
    project = Project.create(tmp_path / "verify-cli", name="verify_cli")
    (project.source_dir / "main.mlo").write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        + source,
        encoding="utf-8",
    )
    return project


def test_parser_exposes_verification_commands() -> None:
    parser = build_parser()

    assert parser.parse_args(["verify"]).command == "verify"
    assert parser.parse_args(["obligations"]).command == "obligations"
    assert parser.parse_args(["holes"]).command == "holes"
    explained = parser.parse_args(["explain-hole", "hole_example"])
    assert explained.command == "explain-hole"
    assert explained.hole_id == "hole_example"


def test_verify_and_obligations_expose_deterministic_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    project = _project(
        tmp_path,
        "fn checked(value: UInt64) -> UInt64:\n"
        "    require value > 0\n"
        "    ensure result >= value\n"
        "    value\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    let value = checked(1)\n"
        "    console.write(\"verified\")\n"
        "    Ok(\"verified\")\n",
    )

    assert main(["verify", str(project.root), "--json"]) == EXIT_OK
    verified = json.loads(capsys.readouterr().out)
    assert verified["ok"] is True
    assert verified["verification"]["total_obligations"] == 2
    assert verified["verification"]["unresolved"] == 0

    assert main(["obligations", str(project.root), "--json"]) == EXIT_OK
    first = capsys.readouterr().out
    assert main(["obligations", str(project.root), "--json"]) == EXIT_OK
    second = capsys.readouterr().out
    assert first == second
    payload = json.loads(first)
    assert sorted(row["category"] for row in payload["obligations"]) == [
        "function_postcondition",
        "function_precondition",
    ]
    assert all("verification" in row for row in payload["obligations"])
    assert all("bounded_symbolic" in row for row in payload["obligations"])
    assert all("smt" in row for row in payload["obligations"])


def test_typed_holes_fail_verify_and_have_explainable_context(
    tmp_path: Path,
    capsys,
) -> None:
    project = _project(
        tmp_path,
        "fn fill(value: UInt64) -> UInt64:\n"
        "    let prior = value\n"
        "    let answer: UInt64 = ?\n"
        "    answer\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    let value = fill(1)\n"
        "    console.write(\"hole\")\n"
        "    Ok(\"hole\")\n",
    )

    assert main(["verify", str(project.root), "--json"]) == EXIT_DIAGNOSTIC
    verified = json.loads(capsys.readouterr().out)
    assert verified["ok"] is False
    assert verified["verification"]["unresolved"] == 1

    assert main(["holes", str(project.root), "--json"]) == EXIT_OK
    holes = json.loads(capsys.readouterr().out)["holes"]
    assert len(holes) == 1
    hole = holes[0]
    assert hole["expected_type"] == "UInt64"
    assert hole["owner"] == "fill"
    assert [item["name"] for item in hole["context"]] == ["value", "prior"]

    assert main(
        ["explain-hole", hole["hole_id"], str(project.root), "--json"]
    ) == EXIT_OK
    explained = json.loads(capsys.readouterr().out)
    assert explained["hole"] == hole

    assert main(
        ["explain-hole", "hole_missing", str(project.root), "--json"]
    ) == EXIT_DIAGNOSTIC
    missing = json.loads(capsys.readouterr().out)
    assert missing["diagnostics"][0]["message"] == (
        "UnknownTypedHole: hole_missing"
    )
