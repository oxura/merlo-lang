from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from merlo.preservation import PreservationReport, check_preservation
from merlo.refactor import preview_rename
from merlo.semantic_world import SemanticWorld, WorldError


def _world(tmp_path: Path) -> SemanticWorld:
    root = tmp_path / "app"
    source = root / "main.mlo"
    root.mkdir()
    source.write_text(
        "module app.main\n\n"
        "fn identity(value: Byte) -> Byte:\n"
        "    # identity stays documentation\n"
        "    require value >= Byte(0)\n"
        "    ensure result == value\n"
        "    value\n\n"
        "export task main(path: Path) -> Result[Text, Text]:\n"
        "    uses console.write\n"
        "    console.write(\"main\")\n"
        "    return Ok(\"main\")\n",
        encoding="utf-8",
    )
    return SemanticWorld.build(source, require_interface_lock=False)


def test_rename_preservation_is_canonical_and_roundtrips(tmp_path: Path) -> None:
    before_world = _world(tmp_path)
    before = before_world.compile_context("app.main.identity")
    change = preview_rename(before_world, "app.main.identity", "same")
    change.apply(before_world)
    after_world = SemanticWorld.build(tmp_path / "app" / "main.mlo", require_interface_lock=False)
    after = after_world.compile_context("app.main.same")

    report = check_preservation(change, before, after)
    assert report.overall == "preserved"
    assert [item.dimension for item in report.findings] == [
        "identity", "source", "signature", "dependent_types", "callers", "callees",
        "dependencies", "effects", "capabilities", "ownership", "resources",
        "requirements", "ensures", "invariants", "holes", "obligations", "tests", "verification",
    ]
    assert report.findings[0].status == "authorized_change"
    assert report.findings[1].status == "authorized_change"
    assert "# identity stays documentation" in after.source
    assert report.to_json() == report.to_json()
    assert PreservationReport.from_json(report.to_json()).to_dict() == report.to_dict()


def test_behavior_dimensions_are_violations(tmp_path: Path) -> None:
    world = _world(tmp_path)
    before = world.compile_context("app.main.identity")
    change = preview_rename(world, "app.main.identity", "same")
    after = replace(
        before,
        capabilities=("filesystem.read",),
        effects=("filesystem.read",),
        requirements=("value > Byte(1)",),
        ensures=("result != value",),
        invariants=("broken",),
        obligations=({"obligation_id": "changed"},),
        verification={"proof": "regressed"},
    )
    report = check_preservation(change, before, after)
    assert report.overall == "violated"
    assert {item.dimension for item in report.findings if item.status == "violated"} >= {
        "effects", "capabilities", "requirements", "ensures", "invariants", "obligations", "verification",
    }


def test_bindings_and_tampering_raise(tmp_path: Path) -> None:
    world = _world(tmp_path)
    before = world.compile_context("app.main.identity")
    change = preview_rename(world, "app.main.identity", "same")
    after = before
    with pytest.raises(WorldError, match="BeforeWorldDigestMismatch"):
        check_preservation(change, replace(before, world_digest="wrong"), after)
    with pytest.raises(WorldError, match="BeforeTargetRevisionMismatch"):
        check_preservation(change, replace(before, target_revision_id="wrong", target=replace(before.target, revision_id="wrong")), after)

    payload = json.loads(before.to_json())
    payload["source"] = "tampered"
    with pytest.raises(ValueError, match="SemanticCapsuleDigestMismatch"):
        check_preservation(change, payload, after)

    report = check_preservation(change, before, after)
    payload = json.loads(report.to_json())
    payload["findings"] = list(reversed(payload["findings"]))
    with pytest.raises(ValueError, match="PreservationReportDigestMismatch"):
        PreservationReport.from_dict(payload)
