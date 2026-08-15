from __future__ import annotations

from pathlib import Path

import pytest

from merlo.candidate_pipeline import (
    CandidateBenchmark,
    CandidateSelection,
    CandidateVerification,
    benchmark_candidates,
    verify_candidates,
)
from merlo.compiler import compile_project
from merlo.enumerative_search import enumerate_candidates
from merlo.project import Project, resolve_dependencies
from merlo.semantic_world import SemanticWorld
from merlo.synthesis import SynthesisRequest


def _world(tmp_path: Path) -> SemanticWorld:
    project = Project.create(tmp_path / "project")
    (project.source_dir / "main.mlo").write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    let answer: Text = ?\n"
        "    console.write(answer)\n"
        "    Ok(answer)\n",
        encoding="utf-8",
    )
    resolve_dependencies(project)
    compilation = compile_project(project.root, require_interface_lock=False)
    return SemanticWorld.build(compilation, require_interface_lock=False)


def test_verification_is_individual_and_source_preserving(tmp_path: Path) -> None:
    world = _world(tmp_path)
    before = {p: p.read_bytes() for p in world.root.rglob("*") if p.is_file()}
    hole = world.resolve("main.main")["holes"][0]["hole_id"]
    request = SynthesisRequest(world.digest, "main.main", "fill_hole", {"hole_id": hole, "max_candidates": 2})
    candidates = enumerate_candidates(world, request)
    stale = candidates[0].to_dict()
    stale["base_world_digest"] = "stale"
    stale["digest"] = candidates[0].digest
    reports = verify_candidates(world, [candidates[0], stale, {"unexpected": True}])
    assert len(reports) == 3
    assert reports[0].status == "verified"
    assert reports[1].status == reports[2].status == "rejected"
    assert reports[0].semantic_diff["changed_node"]["kind"] == "TypedHole"
    assert set(reports[0].semantic_diff["before"]) == {"type_digest", "effect_digest", "capability_digest", "hole_digest"}
    assert reports[0].semantic_diff["before"]["hole_digest"] != reports[0].semantic_diff["after"]["hole_digest"]
    assert all(path.read_bytes() == content for path, content in before.items())
    assert CandidateVerification.from_json(reports[0].to_json()) == reports[0]


def test_benchmark_is_deterministic_and_rejects_digest_mismatch(tmp_path: Path) -> None:
    world = _world(tmp_path)
    hole = world.resolve("main.main")["holes"][0]["hole_id"]
    request = SynthesisRequest(world.digest, "main.main", "fill_hole", {"hole_id": hole, "max_candidates": 2})
    verified = verify_candidates(world, enumerate_candidates(world, request))
    first = benchmark_candidates(world, verified)
    second = benchmark_candidates(world, tuple(reversed(verified)))
    assert isinstance(first, CandidateSelection)
    assert first.to_json() == second.to_json()
    assert first.selected_candidate_digest is not None

    calls: list[dict[str, object]] = []
    def evaluator(artifact: dict[str, object]) -> dict[str, object]:
        calls.append(artifact)
        return {"output_digest": "wrong", "semantic_digest": artifact["semantic_digest"], "measurements": {"steps": 1}}

    rejected = benchmark_candidates(world, verified, evaluator)
    assert calls
    assert rejected.selected_candidate_digest is None
    assert all(item.status == "rejected" for item in rejected)
    assert all(item.diagnostic["code"] == "WorldError" for item in rejected if item.diagnostic)
    assert CandidateBenchmark.from_json(first.benchmarks[0].to_json()) == first.benchmarks[0]


def test_strict_roundtrip_rejects_extra_fields() -> None:
    report = CandidateVerification("candidate", "rejected", diagnostic={"code": "X", "message": "x", "details": {}})
    payload = report.to_dict()
    payload["extra"] = True
    with pytest.raises(ValueError):
        CandidateVerification.from_dict(payload)
