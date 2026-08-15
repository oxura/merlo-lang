from __future__ import annotations

from pathlib import Path

import pytest

from merlo.compiler import compile_project
from merlo.project import Project, resolve_dependencies
from merlo.semantic_world import StaleWorldError, WorldError, SemanticWorld
from merlo.synthesis import SynthesisCandidate, SynthesisRequest
from merlo.enumerative_search import enumerate_candidates


def _world(tmp_path: Path, expected_type: str = "UInt64") -> SemanticWorld:
    project = Project.create(tmp_path / "project")
    helper_literal = {
        "Bool": "false",
        "Text": '""',
        "Unit": "Unit",
        "Float64": "0.0",
        "Int64": "0",
    }.get(expected_type, "7")
    (project.source_dir / "main.mlo").write_text(
        "module main\n\n"
        f"fn helper() -> {expected_type}:\n"
        f"    {helper_literal}\n\n"
        f"fn target(value: {expected_type}, duplicate: {expected_type}) -> {expected_type}:\n"
        f"    let answer: {expected_type} = ?\n"
        "    answer\n\n"
        "export task main(path: Path) -> Unit:\n"
        "    uses console.write\n"
        "    console.write(\"ok\")\n",
        encoding="utf-8",
    )
    resolve_dependencies(project)
    compilation = compile_project(project.root, require_interface_lock=False)
    return SemanticWorld.build(compilation, require_interface_lock=False)


def _request(world: SemanticWorld, *, hole_id: str | None = None, max_candidates: int | None = None) -> SynthesisRequest:
    hole = world.resolve("main.target")["holes"][0]
    arguments = {"hole_id": hole_id or hole["hole_id"]}
    if max_candidates is not None:
        arguments["max_candidates"] = max_candidates
    return SynthesisRequest(world.digest, "main.target", "fill_hole", arguments)


def test_enumeration_is_deterministic_bounded_and_read_only(tmp_path: Path) -> None:
    world = _world(tmp_path)
    request = _request(world)
    before = (world.root / "src" / "main.mlo").read_bytes()

    first = enumerate_candidates(world, request)
    second = enumerate_candidates(world, request.to_dict())

    assert first and first == second
    assert SynthesisCandidate.from_json(first[0].to_json()) == first[0]
    assert [item.provenance["expression"] for item in first] == ["duplicate", "value", "0", "1", "helper()"]
    assert all(item.producer == "enumerative" and item.status == "proposed" for item in first)
    assert all(item.change_ir.operation == "fill_hole" for item in first)
    assert all(len(item.change_ir.edits) == 1 for item in first)
    assert (world.root / "src" / "main.mlo").read_bytes() == before

    bounded = enumerate_candidates(world, _request(world, max_candidates=2))
    assert [item.provenance["expression"] for item in bounded] == ["duplicate", "value"]
    assert all(item.provenance["max_candidates"] == 2 for item in bounded)


def test_literal_domains_are_type_specific(tmp_path: Path) -> None:
    for index, (expected, expressions) in enumerate(
        {
            "Bool": ["duplicate", "value", "false", "true", "helper()"],
            "Text": ["duplicate", "value", '""', "helper()"],
            "Unit": ["duplicate", "value", "Unit", "helper()"],
            "Float64": ["duplicate", "value", "0.0", "1.0", "helper()"],
            "Int64": ["duplicate", "value", "-1", "0", "1", "helper()"],
        }.items()
    ):
        world = _world(tmp_path / str(index), expected)
        result = enumerate_candidates(world, _request(world))
        assert [item.provenance["expression"] for item in result] == expressions


def test_malformed_stale_and_wrong_requests_reject(tmp_path: Path) -> None:
    world = _world(tmp_path)
    request = _request(world)
    with pytest.raises(StaleWorldError):
        enumerate_candidates(world, SynthesisRequest("other", request.target, request.operation, request.arguments))
    with pytest.raises(WorldError):
        enumerate_candidates(world, _request(world, max_candidates=0))
    with pytest.raises(WorldError):
        enumerate_candidates(world, _request(world, max_candidates=257))
    with pytest.raises(WorldError):
        enumerate_candidates(world, SynthesisRequest(world.digest, request.target, "rename", {"new_name": "x"}))
    with pytest.raises(WorldError):
        enumerate_candidates(world, SynthesisRequest(world.digest, request.target, "fill_hole", {"hole_id": "missing"}))
