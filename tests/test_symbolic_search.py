from __future__ import annotations

from pathlib import Path

import pytest

from merlo.compiler import compile_project
from merlo.project import Project, resolve_dependencies
from merlo.semantic_world import StaleWorldError, WorldError, SemanticWorld
from merlo.symbolic_search import (
    _names_allowed,
    _projection,
    search_symbolic_candidates,
)
from merlo.synthesis import SynthesisRequest


def _world(tmp_path: Path, ensures: tuple[str, ...] = ("result == value + 1",)) -> tuple[SemanticWorld, str]:
    project = Project.create(tmp_path / "symbolic")
    clauses = "".join(f"    ensure {item}\n" for item in ensures)
    (project.source_dir / "main.mlo").write_text(
        "module main\n\n"
        "fn identity(value: UInt64) -> UInt64:\n"
        f"{clauses}"
        "    let result_value: UInt64 = ?\n"
        "    result_value\n\n"
        "export task main(path: Path) -> Result[Text, Text]:\n"
        "    uses console.write\n"
        "    Ok(\"ok\")\n",
        encoding="utf-8",
    )
    resolve_dependencies(project)
    compilation = compile_project(project.root, require_interface_lock=False)
    world = SemanticWorld.build(compilation, require_interface_lock=False)
    hole_id = world.resolve("main.identity")["holes"][0]["hole_id"]
    return world, hole_id


def _request(world: SemanticWorld, hole_id: str, *, target: str = "main.identity") -> SynthesisRequest:
    return SynthesisRequest(world.digest, target, "fill_hole", {"hole_id": hole_id})


def test_projects_exact_equality_and_keeps_change_proposed(tmp_path: Path) -> None:
    world, hole_id = _world(tmp_path)
    source = (world.root / "src" / "main.mlo").read_bytes()
    candidates = search_symbolic_candidates(world, _request(world, hole_id))
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.change_ir.metadata["replacement"] == "value + 1"
    assert candidate.change_ir.metadata["hole_id"] == hole_id
    assert candidate.status == "proposed"
    assert not hasattr(candidate, "verified")
    assert (world.root / "src" / "main.mlo").read_bytes() == source
    assert candidate.provenance["algorithm"] == "postcondition_equality_projection"
    assert candidate.provenance["ensure_index"] == 0


def test_reversed_equality_parentheses_and_strings_are_token_aware(tmp_path: Path) -> None:
    world, hole_id = _world(tmp_path, ("(value + 1) == (result)",))
    candidates = search_symbolic_candidates(world, _request(world, hole_id))
    assert [item.change_ir.metadata["replacement"] for item in candidates] == ["value + 1"]
    assert _projection('result == "contains == safely"') == ('"contains == safely"', 1)

def test_only_one_result_equality_is_projectable(tmp_path: Path) -> None:
    world, hole_id = _world(tmp_path)
    assert _projection("result != value") is None
    assert _projection("result > value") is None
    assert _projection("result == value or result == value + 1") is None
    assert _projection("value == 1") is None
    assert _projection("result == (value == 1)") == ("value == 1", 3)
    assert search_symbolic_candidates(world, _request(world, hole_id))

def test_effectful_and_unbound_names_are_rejected(tmp_path: Path) -> None:
    world, hole_id = _world(tmp_path)
    hole = world.resolve("main.identity")["holes"][0]
    assert not _names_allowed("console.write(value)", world, hole)
    assert not _names_allowed("missing(value)", world, hole)

def test_duplicate_projections_and_binding_are_strict(tmp_path: Path) -> None:
    world, hole_id = _world(tmp_path, ("result == value + 1", "value + 1 == result"))
    first = search_symbolic_candidates(world, _request(world, hole_id))
    second = search_symbolic_candidates(world, _request(world, hole_id).to_dict())
    assert len(first) == 1
    assert first[0].to_json() == second[0].to_json()
    with pytest.raises(WorldError):
        search_symbolic_candidates(world, _request(world, "missing-hole"))
    wrong = _request(world, hole_id, target="missing.identity")
    with pytest.raises(WorldError):
        search_symbolic_candidates(world, wrong)
    stale = SynthesisRequest("stale", "main.identity", "fill_hole", {"hole_id": hole_id})
    with pytest.raises(StaleWorldError):
        search_symbolic_candidates(world, stale)
