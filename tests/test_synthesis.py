from __future__ import annotations

from pathlib import Path

import pytest

from merlo.semantic_world import StaleWorldError, WorldError
from merlo.synthesis import (
    CandidateRank,
    SynthesisCandidate,
    SynthesisRequest,
    synthesize_rewrites,
)
from test_semantic_impact import _world


def _request(world, operation="rename", arguments=None):
    return SynthesisRequest(
        world_digest=world.digest,
        target="sym-a",
        operation=operation,
        arguments=arguments or {"new_name": "renamed"},
        goal="rewrite",
    )


def test_rename_is_deterministic_roundtrippable_and_read_only(tmp_path: Path) -> None:
    world = _world(tmp_path)
    source_bytes = {
        str(world.root / relative): (world.root / relative).read_bytes()
        for relative in world.data["source_hashes"]
    }
    request = _request(world)

    first = synthesize_rewrites(world, request)
    second = synthesize_rewrites(world, request.to_dict())

    assert len(first) == len(second) == 1
    candidate = first[0]
    assert candidate.to_json() == second[0].to_json()
    assert SynthesisCandidate.from_json(candidate.to_json()) == candidate
    assert candidate.status == "proposed"
    assert candidate.producer == "rewrite"
    assert candidate.change["operation"] == "rename"
    assert candidate.change["status"] == "ready"
    assert candidate.change["digest"] == candidate.change_ir.digest
    assert candidate.rank == CandidateRank(0, len(candidate.change["edits"]), candidate.change["digest"])
    assert not hasattr(candidate, "verified")
    assert not hasattr(candidate, "accepted")
    assert all(
        path.read_bytes() == original
        for path, original in ((Path(path), value) for path, value in source_bytes.items())
    )


def test_rename_edits_are_identifier_tokens_not_comments_or_literals(tmp_path: Path) -> None:
    world = _world(tmp_path)
    source = tmp_path / "src.mlo"
    original = source.read_text(encoding="utf-8")
    source.write_text(original.replace("fn a\n", "fn a # a\n\"a\"\n"), encoding="utf-8")
    # The saved world is intentionally stale after this source edit.
    with pytest.raises(StaleWorldError):
        synthesize_rewrites(world, _request(world))


def test_move_and_signature_are_blocked_with_exact_diagnostics(tmp_path: Path) -> None:
    world = _world(tmp_path)
    move = synthesize_rewrites(
        world,
        _request(world, "move", {"module": "other"}),
    )[0]
    signature = synthesize_rewrites(
        world,
        _request(world, "change_signature", {"signature": "(Text) -> Unit"}),
    )[0]

    for candidate in (move, signature):
        assert candidate.status == "blocked"
        assert candidate.change["status"] == "unsupported"
        assert candidate.diagnostic == candidate.change["diagnostic"]
        assert candidate.impact_digest
        assert candidate.capsule_digest
        with pytest.raises(WorldError):
            SynthesisCandidate(
                producer=candidate.producer,
                producer_revision=candidate.producer_revision,
                base_world_digest=candidate.base_world_digest,
                target_symbol_id=candidate.target_symbol_id,
                change=candidate.change,
                capsule_digest=candidate.capsule_digest,
                impact_digest=candidate.impact_digest,
                rank=candidate.rank,
                provenance=candidate.provenance,
                status="proposed",
            )


def test_stale_tampered_noncanonical_and_malformed_requests_reject(tmp_path: Path) -> None:
    world = _world(tmp_path)
    request = _request(world)
    stale = SynthesisRequest(
        world_digest="different-world",
        target=request.target,
        operation=request.operation,
        arguments=request.arguments,
        goal=request.goal,
    )
    with pytest.raises(StaleWorldError):
        synthesize_rewrites(world, stale)

    payload = request.to_dict()
    payload["arguments"] = {"new_name": "other"}
    with pytest.raises(WorldError, match="DigestMismatch"):
        SynthesisRequest.from_dict(payload)

    payload = request.to_dict()
    payload["arguments"] = {"new_name": "x", "unexpected": True}
    payload["digest"] = request.digest
    with pytest.raises(WorldError):
        synthesize_rewrites(world, payload)

    unknown = SynthesisRequest(
        world_digest=world.digest,
        target="sym-a",
        operation="unknown",
        arguments={},
    )
    with pytest.raises(WorldError, match="UnknownOperation"):
        synthesize_rewrites(world, unknown)


def test_fill_hole_request_shape_is_strict_and_rewrite_does_not_route_it(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    request = SynthesisRequest(
        world_digest=world.digest,
        target="sym-a",
        operation="fill_hole",
        arguments={"hole_id": "hole-1", "max_candidates": 3},
    )
    assert SynthesisRequest.from_json(request.to_json()) == request
    with pytest.raises(WorldError, match="UnknownOperation"):
        synthesize_rewrites(world, request)
    with pytest.raises(WorldError, match="max_candidates"):
        SynthesisRequest(
            world_digest=world.digest,
            target="sym-a",
            operation="fill_hole",
            arguments={"hole_id": "hole-1", "max_candidates": True},
        )

def test_candidate_tamper_and_extra_fields_reject(tmp_path: Path) -> None:
    world = _world(tmp_path)
    candidate = synthesize_rewrites(world, _request(world))[0]
    payload = candidate.to_dict()
    payload["status"] = "blocked"
    with pytest.raises(WorldError, match="DigestMismatch"):
        SynthesisCandidate.from_dict(payload)

    payload = candidate.to_dict()
    payload["verified"] = False
    with pytest.raises(WorldError, match="SchemaMismatch"):
        SynthesisCandidate.from_dict(payload)
