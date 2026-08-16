from __future__ import annotations

import json
from pathlib import Path

import pytest


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "app" / "main.mlo"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "module app.main\n\n"
        "task helper(path: Path) -> Result[Text, Text]:\n"
        "    uses console.write\n"
        "    return Ok(\"helper\")\n\n"
        "export task main(path: Path) -> Result[Text, Text]:\n"
        "    uses console.write\n"
        "    return helper(path)\n",
        encoding="utf-8",
    )
    return path


def test_preview_roundtrip_and_commit_saves_world(tmp_path: Path) -> None:
    from merlo.evolve_protocol import EvolutionPlan, EvolutionResult, VerifiedEvolutionProtocol
    from merlo.semantic_world import SemanticWorld

    source = _source(tmp_path)
    world = SemanticWorld.build(source, require_interface_lock=False)
    protocol = VerifiedEvolutionProtocol(world)
    plan = protocol.preview_rename("app.main.helper", "assist", goal="keep behavior")
    assert plan.contract == "merlo.evolution-plan.v1"
    assert not hasattr(plan, "change")
    assert not hasattr(plan, "semantic_capsule")
    assert not hasattr(plan, "semantic_impact")
    assert plan.to_json() == EvolutionPlan.from_json(plan.to_json(), world=world).to_json()
    result = protocol.apply(plan.to_dict())
    assert result.status == "committed"
    assert result.preservation is not None and result.preservation.overall == "preserved"
    assert result.evidence is not None
    assert result.transaction is not None and result.transaction["action"] == "commit"
    assert not hasattr(result, "patch_evidence")
    assert not hasattr(result, "preservation_report")
    assert result.after_world_digest
    assert protocol.world.digest == result.after_world_digest
    assert protocol.world.state_path.is_file()
    from merlo.semantic_world import SemanticWorld
    assert SemanticWorld.load(protocol.world.state_path).digest == result.after_world_digest
    assert "task assist" in source.read_text(encoding="utf-8")
    assert EvolutionResult.from_json(result.to_json()).to_json() == result.to_json()


def test_tampered_or_stale_plan_rejected_without_writes(tmp_path: Path) -> None:
    from merlo.evolve_protocol import VerifiedEvolutionProtocol
    from merlo.semantic_world import SemanticWorld, WorldError

    source = _source(tmp_path)
    world = SemanticWorld.build(source, require_interface_lock=False)
    protocol = VerifiedEvolutionProtocol(world)
    plan = protocol.preview_rename("app.main.helper", "assist")
    before = source.read_bytes()
    tampered = json.loads(plan.to_json())
    tampered["change_ir"]["metadata"]["new_name"] = "other"
    with pytest.raises(WorldError):
        protocol.apply(tampered)
    assert source.read_bytes() == before
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(WorldError):
        protocol.apply(plan)
    assert "helper" in source.read_text(encoding="utf-8")


def test_post_apply_failure_rolls_back_and_reports_diagnostic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import merlo.evolve_protocol as evolve
    from merlo.evolve_protocol import VerifiedEvolutionProtocol
    from merlo.semantic_world import SemanticWorld, WorldError

    source = _source(tmp_path)
    world = SemanticWorld.build(source, require_interface_lock=False)
    protocol = VerifiedEvolutionProtocol(world)
    world.save()
    original_digest = world.digest
    plan = protocol.preview_rename("app.main.helper", "assist")

    def fail(*args: object, **kwargs: object) -> object:
        raise WorldError("InjectedPostApplyFailure")

    monkeypatch.setattr(evolve, "check_preservation", fail)
    result = protocol.apply(plan)
    assert result.status == "rolled_back"
    assert result.diagnostic is not None and result.diagnostic.message == "InjectedPostApplyFailure"
    assert result.rollback is not None and result.rollback["action"] == "rollback"
    assert result.transaction is not None
    assert "helper" in source.read_text(encoding="utf-8")
    assert protocol.world.digest == original_digest
    assert SemanticWorld.load(protocol.world.state_path).digest == original_digest


def test_result_rejects_noncanonical_diagnostic() -> None:
    from merlo.evolve_protocol import EvolutionDiagnostic, EvolutionResult
    from merlo.semantic_world import WorldError

    with pytest.raises(WorldError):
        EvolutionDiagnostic.from_dict({"code": "x", "message": "x", "details": {"x": 1}, "extra": 2})
    with pytest.raises(WorldError):
        EvolutionResult.from_dict({"status": "committed"})
