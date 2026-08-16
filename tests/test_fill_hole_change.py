from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from merlo.compiler import compile_project
from merlo.project import Project, resolve_dependencies
from merlo.refactor import ChangeIR, preview_fill_hole
from merlo.semantic_world import SemanticWorld, StaleWorldError, WorldError


def _world(tmp_path: Path) -> tuple[SemanticWorld, Path]:
    project = Project.create(tmp_path / "project")
    source = project.source_dir / "main.mlo"
    source.write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn target(value: UInt64) -> UInt64:\n"
        "    let answer: UInt64 = ?\n"
        "    answer\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"ok\")\n"
        "    Ok(\"ok\")\n",
        encoding="utf-8",
    )
    resolve_dependencies(project)
    compilation = compile_project(
        project.root,
        require_interface_lock=False,
    )
    return (
        SemanticWorld.build(
            compilation,
            require_interface_lock=False,
        ),
        source,
    )


def test_fill_hole_preview_roundtrip_and_apply(tmp_path: Path) -> None:
    world, source = _world(tmp_path)
    symbol = world.resolve("main.target")
    hole_id = symbol["holes"][0]["hole_id"]
    before = source.read_bytes()

    change = preview_fill_hole(
        world,
        "main.target",
        hole_id,
        "value",
    )

    assert change.operation == "fill_hole"
    assert change.status == "ready"
    assert change.metadata == {
        "hole_id": hole_id,
        "expected_type": "UInt64",
        "replacement": "value",
    }
    assert source.read_bytes() == before
    restored = ChangeIR.from_json(
        change.to_json(),
        world=world,
    )
    assert restored == change

    receipt = restored.apply(world)
    assert receipt["committed"] is True
    assert "let answer: UInt64 = value" in source.read_text(
        encoding="utf-8"
    )
    rebuilt = SemanticWorld.build(
        source,
        require_interface_lock=False,
    )
    assert rebuilt.resolve("main.target")["holes"] == []
    with pytest.raises(StaleWorldError):
        change.apply(world)


def test_fill_hole_rejects_wrong_or_tampered_edits(
    tmp_path: Path,
) -> None:
    world, _ = _world(tmp_path)
    hole_id = world.resolve("main.target")["holes"][0][
        "hole_id"
    ]
    with pytest.raises(WorldError, match="FillHoleNotOwned"):
        preview_fill_hole(
            world,
            "main.target",
            "missing",
            "value",
        )
    with pytest.raises(
        WorldError,
        match="FillHoleInvalidReplacement",
    ):
        preview_fill_hole(
            world,
            "main.target",
            hole_id,
            "Here is the answer",
        )

    change = preview_fill_hole(
        world,
        "main.target",
        hole_id,
        "value",
    )
    forged = replace(
        change,
        edits=(
            replace(
                change.edits[0],
                replacement="0",
            ),
        ),
        digest="",
    )
    with pytest.raises(
        WorldError,
        match="SemanticEditMismatch",
    ):
        forged.apply(world)
