from __future__ import annotations

from pathlib import Path

import pytest

from merlo.compiler import compile_project
from merlo.llm_synthesis import LLM_MAX_CANDIDATES, generate_llm_candidates
from merlo.project import Project, resolve_dependencies
from merlo.semantic_world import SemanticWorld, StaleWorldError, WorldError
from merlo.synthesis import SynthesisRequest


def _world(tmp_path: Path) -> SemanticWorld:
    project = Project.create(tmp_path / "project")
    (project.source_dir / "main.mlo").write_text(
        "module main\n\n"
        "fn target(value: UInt64) -> UInt64:\n"
        "    let answer: UInt64 = ?\n"
        "    answer\n\n"
        "export task main(path: Path) -> Unit:\n"
        "    uses console.write\n"
        "    console.write(\"ok\")\n",
        encoding="utf-8",
    )
    resolve_dependencies(project)
    compilation = compile_project(project.root, require_interface_lock=False)
    return SemanticWorld.build(compilation, require_interface_lock=False)


def _request(world: SemanticWorld, *, max_candidates: int | None = None) -> SynthesisRequest:
    hole_id = world.resolve("main.target")["holes"][0]["hole_id"]
    arguments: dict[str, object] = {"hole_id": hole_id}
    if max_candidates is not None:
        arguments["max_candidates"] = max_candidates
    return SynthesisRequest(world.digest, "main.target", "fill_hole", arguments)


def test_opt_in_payload_is_minimized_and_output_is_deterministic(tmp_path: Path) -> None:
    world = _world(tmp_path)
    request = _request(world)
    before = (world.root / "src" / "main.mlo").read_bytes()
    calls: list[dict[str, object]] = []

    def provider(payload):
        calls.append(payload)
        return {
            "provider": "fake",
            "model": "model-1",
            "revision": "rev-1",
            "candidates": ["0", "value"],
        }
    first = generate_llm_candidates(world, request, provider)
    second = generate_llm_candidates(world, request.to_dict(), provider)

    assert len(calls) == 2
    payload = calls[0]
    assert set(payload) == {"schema_version", "contract", "request", "capsule"}
    assert set(payload["capsule"]) == {
        "schema_version", "contract", "digest", "world_digest", "target_revision_id",
        "goal", "target", "signature", "dependent_types", "effects", "capabilities",
        "ownership", "resources", "requirements", "ensures", "invariants", "hole",
    }
    assert "source" not in payload["capsule"]
    assert "verification" not in payload["capsule"]
    assert "callers" not in payload["capsule"]
    assert payload["capsule"]["hole"]["hole_id"] == request.arguments["hole_id"]
    assert [item.provenance["expression"] for item in first] == ["0", "value"]
    assert first == second
    assert all(item.producer == "llm" and item.status == "proposed" for item in first)
    assert [item.rank.cost for item in first] == [1, 5]
    assert all(item.provenance["provider"] == "fake" for item in first)
    assert (world.root / "src" / "main.mlo").read_bytes() == before


def test_provider_failures_and_strict_response_rejection(tmp_path: Path) -> None:
    world = _world(tmp_path)
    request = _request(world)

    def raising(_payload):
        raise RuntimeError("network should be owned by caller")

    with pytest.raises(WorldError, match="LLMProviderFailure"):
        generate_llm_candidates(world, request, raising)

    responses = [
        {"provider": "fake", "model": "m", "revision": "r", "candidates": ["0", "0"]},
        {"provider": "fake", "model": "m", "revision": "r", "candidates": ["Here is the answer"]},
        {"provider": "fake", "model": "m", "revision": "r", "candidates": ["```value```"]},
        {"provider": "fake", "model": "m", "revision": "r", "candidates": [1]},
        {"provider": "fake", "model": "m", "revision": "r", "candidates": ["0"], "extra": True},
    ]
    for response in responses:
        with pytest.raises(WorldError):
            generate_llm_candidates(world, request, lambda _payload, response=response: response)


def test_stale_and_over_limit_requests_are_rejected_before_provider(tmp_path: Path) -> None:
    world = _world(tmp_path)
    request = _request(world)
    calls = 0

    def provider(_payload):
        nonlocal calls
        calls += 1
        return {"provider": "fake", "model": "m", "revision": "r", "candidates": ["0"]}

    stale = SynthesisRequest("different-world", request.target, request.operation, request.arguments)
    with pytest.raises(StaleWorldError):
        generate_llm_candidates(world, stale, provider)
    assert calls == 0

    with pytest.raises(WorldError):
        generate_llm_candidates(world, _request(world, max_candidates=LLM_MAX_CANDIDATES + 1), provider)
    assert calls == 0

    with pytest.raises(WorldError):
        generate_llm_candidates(
            world,
            _request(
                world,
                max_candidates=1,
            ),
            lambda _payload: {"provider": "fake", "model": "m", "revision": "r", "candidates": ["0", "value"]},
        )


def test_provider_metadata_is_exact_provenance_and_request_binding_is_preserved(tmp_path: Path) -> None:
    world = _world(tmp_path)
    request = _request(world)
    result = generate_llm_candidates(
        world,
        request,
        lambda _payload: {
            "provider": "provider-x",
            "model": "model-y",
            "revision": "provider-revision-z",
            "candidates": ["value"],
        },
    )[0]
    assert result.producer_revision == "llm/v1"
    assert result.provenance == {
        "provider": "provider-x",
        "model": "model-y",
        "revision": "provider-revision-z",
        "expression": "value",
        "request_digest": request.digest,
        "capsule_digest": result.capsule_digest,
    }
    assert result.change_ir.metadata["hole_id"] == request.arguments["hole_id"]
    assert result.change_ir.metadata["replacement"] == "value"
