from __future__ import annotations

import json
from pathlib import Path

import pytest

from merlo.cli import EXIT_DIAGNOSTIC, EXIT_OK, build_parser, main
from merlo.compiler import compile_project
from merlo.project import Project
from merlo.semantic_world import SemanticWorld
from merlo.synthesize_protocol import SynthesisRun, synthesize_typed_hole


def _project(tmp_path: Path, ensure: str = "result == value") -> Project:
    project = Project.create(tmp_path / "synthesize-cli", name="synthesize_cli")
    (project.source_dir / "main.mlo").write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn fill(value: UInt64) -> UInt64:\n"
        f"    ensure {ensure}\n"
        "    let answer: UInt64 = ?\n"
        "    answer\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    let value = fill(7)\n"
        "    console.write(\"synthesized\")\n"
        "    Ok(\"synthesized\")\n",
        encoding="utf-8",
    )
    return project


def test_parser_exposes_offline_synthesis() -> None:
    parsed = build_parser().parse_args(
        ["synthesize", "main.fill", "--max-candidates", "4"]
    )
    assert parsed.command == "synthesize"
    assert parsed.target == "main.fill"
    assert parsed.max_candidates == 4


def test_synthesis_is_deterministic_read_only_and_applies_verified_fill(
    tmp_path: Path,
    capsys,
) -> None:
    project = _project(tmp_path)
    source = project.source_dir / "main.mlo"
    before = source.read_bytes()
    report = project.root / ".merlo" / "synthesis.json"
    command = [
        "synthesize",
        "main.fill",
        str(project.root),
        "--goal",
        "preserve the postcondition",
        "--max-candidates",
        "4",
        "--report-out",
        str(report),
        "--json",
    ]

    assert main(command) == EXIT_OK
    first = capsys.readouterr().out
    payload = json.loads(first)
    assert payload["status"] == "selected"
    assert payload["selected_expression"] == "value"
    assert payload["run"]["selection"]["selected_candidate_digest"]
    assert any(
        item["status"] == "verified"
        for item in payload["run"]["verifications"]
    )
    assert source.read_bytes() == before
    saved = report.read_text(encoding="utf-8")
    assert SynthesisRun.from_json(saved).to_json() + "\n" == saved

    assert main(command) == EXIT_OK
    assert capsys.readouterr().out == first
    assert report.read_text(encoding="utf-8") == saved
    assert source.read_bytes() == before

    assert main([*command[:-1], "--apply", "--json"]) == EXIT_OK
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "committed"
    assert applied["application"]["transaction"]["action"] == "commit"
    assert applied["application"]["verification_metrics"]["unresolved"] == 0
    assert "let answer: UInt64 = value" in source.read_text(encoding="utf-8")
    assert main(["check", str(project.root), "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_synthesis_post_apply_evidence_failure_rolls_back_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import merlo.synthesize_protocol as protocol

    project = _project(tmp_path)
    source = project.source_dir / "main.mlo"
    before = source.read_bytes()
    compilation = compile_project(project.root, require_interface_lock=False)
    world = SemanticWorld.build(
        compilation,
        state_path=project.root / ".merlo" / "world.json",
        lockfile=project.lock_path,
        require_interface_lock=False,
    )
    world.save()
    symbol = world.resolve("main.fill")
    hole_id = symbol["holes"][0]["hole_id"]
    run = synthesize_typed_hole(
        world,
        symbol["symbol_id"],
        hole_id,
        max_candidates=4,
    )
    original_summary = protocol.verification_summary

    def mismatch(report: object) -> dict[str, object]:
        return {**original_summary(report), "unresolved": 99}

    monkeypatch.setattr(protocol, "verification_summary", mismatch)
    result = run.apply(world)
    assert result["status"] == "rolled_back"
    assert result["rollback"]["action"] == "rollback"
    assert "SynthesisApplyArtifactMismatch:verification" in (
        result["diagnostic"]["message"]
    )
    assert source.read_bytes() == before
    assert SemanticWorld.load(world.state_path).digest == world.digest

def test_synthesis_rejects_candidates_that_do_not_close_verification(
    tmp_path: Path,
    capsys,
) -> None:
    project = _project(tmp_path, ensure="result == value + 1")
    source = project.source_dir / "main.mlo"
    before = source.read_bytes()

    assert main(
        [
            "synthesize",
            "main.fill",
            str(project.root),
            "--max-candidates",
            "4",
            "--json",
        ]
    ) == EXIT_DIAGNOSTIC
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "no_verified_candidate"
    diagnostics = [
        item["diagnostic"]["message"]
        for item in payload["run"]["verifications"]
        if item["diagnostic"] is not None
    ]
    assert any(
        "CandidateIntroducedRefutedObligation" in item
        or "CandidateDidNotCloseTypedHoleObligation" in item
        for item in diagnostics
    )
    assert source.read_bytes() == before
