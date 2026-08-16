from __future__ import annotations

import json
from pathlib import Path

from merlo.cli import EXIT_DIAGNOSTIC, EXIT_OK, main
from merlo.compiler import compile_project
from merlo.project import Project


def _project(tmp_path: Path) -> tuple[Project, Path]:
    project = Project.create(tmp_path / "signature-cli", name="signature_cli")
    source = project.source_dir / "main.mlo"
    source.write_text(
        "module main\n\n"
        "fn identity(value: UInt64) -> UInt64:\n"
        "    value\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    let answer = identity(7)\n"
        "    console.write(\"signature refactor\")\n"
        "    Ok(\"signature refactor\")\n",
        encoding="utf-8",
    )
    return project, source


def test_cli_previews_and_applies_verified_signature_change(
    tmp_path: Path,
    capsys,
) -> None:
    project, source = _project(tmp_path)
    before = source.read_bytes()
    command = [
        "refactor",
        "signature",
        "main.identity",
        "(value: Int64) -> Int64",
        str(project.root),
        "--json",
    ]

    assert main(command) == EXIT_OK
    preview_text = capsys.readouterr().out
    preview = json.loads(preview_text)
    assert preview["status"] == "ready"
    assert preview["edits"][0]["kind"] == "signature"
    assert source.read_bytes() == before
    assert main(command) == EXIT_OK
    assert capsys.readouterr().out == preview_text

    assert main([*command[:-1], "--apply", "--json"]) == EXIT_OK
    applied = json.loads(capsys.readouterr().out)
    assert applied["committed"] is True
    assert "identity(value: Int64) -> Int64" in source.read_text(
        encoding="utf-8"
    )
    compile_project(project.root, require_interface_lock=False)


def test_cli_rejects_incompatible_signature_without_writes(
    tmp_path: Path,
    capsys,
) -> None:
    project, source = _project(tmp_path)
    before = source.read_bytes()

    assert main(
        [
            "refactor",
            "signature",
            "main.identity",
            "(value: Text) -> Text",
            str(project.root),
            "--apply",
            "--json",
        ]
    ) == EXIT_DIAGNOSTIC
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "unsupported"
    assert "caller/body migration" in payload["diagnostic"]["message"]
    assert source.read_bytes() == before
