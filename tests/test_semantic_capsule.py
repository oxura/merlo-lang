from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from merlo.semantic_capsule import SemanticCapsule
from merlo.semantic_world import SemanticWorld


def _world(tmp_path: Path) -> SemanticWorld:
    root = tmp_path / "app"
    source = root / "main.mlo"
    source.parent.mkdir(parents=True)
    source.write_text(
        "module app.main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn identity(value: Byte) -> Byte:\n"
        "    require value >= Byte(0)\n"
        "    ensure result == value\n"
        "    value\n\n"
        "fn wrong(value: Byte) -> Byte:\n"
        "    ensure result > value\n"
        "    value\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"main\")\n"
        "    return Ok(\"main\")\n",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "public.mlo").write_text("test fixture", encoding="utf-8")
    return SemanticWorld.build(source, require_interface_lock=False)


def test_capsule_is_revision_bound_deterministic_and_roundtrips(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)

    first = world.compile_context("app.main.identity", goal="repair")
    second = world.compile_context("app.main.identity", goal="repair")
    restored = SemanticCapsule.from_json(first.to_json())

    assert first.to_json() == second.to_json()
    assert restored.to_dict() == first.to_dict()
    assert first.world_digest == world.digest
    assert first.target_revision_id == first.target.revision_id
    assert first.target.kind == "fn"
    assert first.goal == "repair"
    assert first.requirements == ("value >= Byte(0)",)
    assert first.ensures == ("result == value",)
    assert first.tests == ()


def test_capsule_filters_unrelated_verification_evidence(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    identity = world.compile_context("app.main.identity")
    identity_ids = {
        item["obligation_id"] for item in identity.obligations
    }
    wrong_ids = set(world.resolve("app.main.wrong")["obligations"])
    payload = identity.to_dict()

    assert identity_ids
    assert identity_ids.isdisjoint(wrong_ids)
    for report in payload["verification"].values():
        for value in report.values():
            if not isinstance(value, list):
                continue
            for row in value:
                assert row["obligation_id"] in identity_ids


def test_public_capsule_includes_project_tests(tmp_path: Path) -> None:
    world = _world(tmp_path)

    capsule = world.compile_context("app.main.main")

    assert len(capsule.tests) == 1
    assert capsule.tests[0].endswith("tests/public.mlo")


def test_capsule_rejects_tamper_and_schema_drift(tmp_path: Path) -> None:
    world = _world(tmp_path)
    capsule = world.compile_context("app.main.identity")
    payload = json.loads(capsule.to_json())
    payload["goal"] = "tampered"

    with pytest.raises(ValueError, match="SemanticCapsuleDigestMismatch"):
        SemanticCapsule.from_dict(payload)

    payload = json.loads(capsule.to_json())
    payload["schema_version"] = 1
    with pytest.raises(
        ValueError,
        match="SemanticCapsuleSchemaVersionMismatch",
    ):
        SemanticCapsule.from_dict(payload)


def test_capsule_rejects_noncanonical_or_invalid_nested_data(
    tmp_path: Path,
) -> None:
    capsule = _world(tmp_path).compile_context(
        "app.main.identity"
    )

    with pytest.raises(
        ValueError,
        match="CallersNotCanonical",
    ):
        replace(
            capsule,
            callers=("z", "a"),
        )
    with pytest.raises(
        ValueError,
        match="InvalidHolesIdentity",
    ):
        replace(capsule, holes=({},))
    with pytest.raises(
        ValueError,
        match="SemanticCapsuleNonFiniteNumber",
    ):
        replace(
            capsule,
            verification={"score": float("nan")},
        )
