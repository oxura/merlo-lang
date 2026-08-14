from __future__ import annotations

import json
from pathlib import Path

import pytest

from merlo import (
    ChangeDescriptor,
    EditCapability,
    MerloProtocol,
    Obligation,
    ObligationGraph,
    SoftwareWorld,
    changes_commute,
    changes_conflict,
    compose_changes,
)
from merlo.world import WorldError


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _call_chain(root: Path) -> None:
    _write(
        root,
        "chain.py",
        (
            "def target(value):\n    return value + 1\n\n"
            "def direct(value):\n    return target(value)\n\n"
            "def transitive(value):\n    return direct(value)\n"
        ),
    )


def test_obligations_form_inspectable_dag(tmp_path: Path):
    _write(tmp_path, "api.py", "def compute(a):\n    return a\n")
    _write(
        tmp_path,
        "consumer.py",
        (
            "from api import compute\n"
            "stored = compute\n"
            "callback_register(compute)\n"
        ),
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("api.compute")
    capability = EditCapability.change_signature(
        target.id, allow_public_api_break=True
    )

    plan = world.plan_change_signature(
        target.id,
        "(a, *, model)",
        capability,
        argument_values={"model": "'fast'"},
    )
    graph = plan.obligation_graph
    root = next(
        item for item in graph.obligations if item.kind == "SignatureCompatibilityRoot"
    )
    children = [item for item in graph.obligations if root.id in item.depends_on]

    assert children
    assert all(item.root_change == plan.change.id for item in graph.obligations)
    assert graph.inspect(root.id) == root
    with pytest.raises(ValueError, match="DAG"):
        ObligationGraph(
            (
                Obligation("a", "A", "a", depends_on=("b",)),
                Obligation("b", "B", "b", depends_on=("a",)),
            )
        )


def test_revision_bound_evidence_becomes_stale_after_manual_edit(tmp_path: Path):
    state = tmp_path / ".meldra" / "world.json"
    _write(tmp_path, "api.py", "def compute(a):\n    return a\n")
    world = SoftwareWorld.scan(tmp_path, state)
    target = world.program.entity("api.compute")
    capability = EditCapability.change_signature(target.id)
    plan = world.plan_change_signature(
        target.id,
        "(a, *, model=None)",
        capability,
    )
    world.apply(plan, capability)

    assert world.evidence
    assert all(item.status == "valid" for item in world.evidence.values())
    path = tmp_path / "api.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace("return a", "return a + 1"),
        encoding="utf-8",
    )

    rescanned = SoftwareWorld.scan(tmp_path, state)

    assert any(item.status == "stale" for item in rescanned.evidence.values())
    assert any(item.stale_reasons for item in rescanned.evidence.values())


def test_semantic_impact_distinguishes_direct_and_transitive_callers(tmp_path: Path):
    _call_chain(tmp_path)
    world = SoftwareWorld.scan(tmp_path)
    protocol = MerloProtocol(world)
    target = world.program.entity("chain.target")

    impact = protocol.impact(target.id)

    direct = world.program.entity("chain.direct")
    transitive = world.program.entity("chain.transitive")
    assert impact["direct_callers"] == [direct.id]
    assert impact["transitive_callers"] == [transitive.id]
    assert impact["potential_semantic_reach"] == 3
    assert target.id in impact["public_boundaries"]


def test_task_capsule_and_protocol_are_deterministic_and_semantic_first(
    tmp_path: Path,
):
    _call_chain(tmp_path)
    world = SoftwareWorld.scan(tmp_path)
    protocol = MerloProtocol(world)
    target = world.program.entity("chain.target")

    first = protocol.compile_context(target.id, goal="Add model cost").to_dict()
    second = protocol.compile_context(target.id, goal="Add model cost").to_dict()

    assert first == second
    assert first["target"]["id"] == target.id
    assert "identity_features" not in first["target"]
    assert "def target" in first["definition_source"]
    assert first["direct_callers"]
    assert "identity_features" not in first["direct_callers"][0]
    assert first["transitive_callers"]
    assert protocol.source_read(target.id) == first["definition_source"]
    assert protocol.search("target")[0]["id"] == target.id


def test_semantic_edit_capability_exposes_derived_scope(tmp_path: Path):
    _call_chain(tmp_path)
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("chain.target")
    capability = EditCapability(
        target_ids=frozenset({target.id}),
        operations=frozenset({"rename_symbol"}),
        allow_related_entities=False,
        max_files=20,
        max_entities=1,
        max_edits=20,
        forbidden_categories=frozenset(),
    )

    plan = world.plan_rename(target.id, "renamed", capability)

    kinds = {item.kind for item in plan.obligations}
    assert "RelatedEntityScopeDenied" in kinds
    assert "EntityBudgetExceeded" in kinds
    assert plan.impact is not None
    assert plan.impact.expected_edits == len(plan.edits)


def test_public_rename_is_not_false_safe_by_default(tmp_path: Path):
    _write(tmp_path, "api.py", "def public_api():\n    return 1\n")
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("api.public_api")

    plan = world.plan_rename(
        target.id,
        "renamed",
        EditCapability.rename(target.id),
    )

    assert not plan.ready
    assert "PublicApiCompatibility" in {item.kind for item in plan.obligations}
    assert any(item.level == "Unknown" for item in plan.evidence)


def test_change_algebra_commutativity_and_conflicts():
    rename = ChangeDescriptor.create("Rename", "E1", new_name="B")
    move = ChangeDescriptor.create("Move", "E1", target_module="pkg.new")
    remove = ChangeDescriptor.create("Remove", "E1")
    replace = ChangeDescriptor.create("Replace", "E1", revision="R2")

    assert changes_commute(rename, move)
    assert compose_changes((rename, move)) == compose_changes((move, rename))
    assert changes_conflict(remove, replace)
    with pytest.raises(ValueError, match="conflicting"):
        compose_changes((remove, replace))


def test_rename_round_trip_restores_source_semantics(tmp_path: Path):
    _write(
        tmp_path,
        "module.py",
        "# keep\ndef alpha(value):\n    return 'x' + value\n",
    )
    original = (tmp_path / "module.py").read_bytes()
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("module.alpha")
    initial_revision = target.revision_hash
    capability = EditCapability.rename(
        target.id, allow_public_api_break=True
    )
    first = world.plan_rename(target.id, "beta", capability)
    world.apply(first, capability)
    second = world.plan_rename(target.id, "alpha", capability)
    world.apply(second, capability)

    restored = world.program.entity(target.id)
    assert restored.revision_hash == initial_revision
    assert (tmp_path / "module.py").read_bytes() == original


def test_disjoint_semantic_renames_commute_end_to_end(tmp_path: Path):
    operations = (
        ("a.alpha", "alpha_next"),
        ("b.beta", "beta_next"),
    )

    def evolve(root: Path, order: tuple[int, int]) -> SoftwareWorld:
        _write(root, "a.py", "def alpha(value):\n    return value + 1\n")
        _write(root, "b.py", "def beta(value):\n    return value * 2\n")
        world = SoftwareWorld.scan(root)
        for index in order:
            locator, new_name = operations[index]
            target = world.program.entity(locator)
            capability = EditCapability.rename(
                target.id, allow_public_api_break=True
            )
            plan = world.plan_rename(target.id, new_name, capability)
            assert plan.ready
            world.apply(plan, capability)
        return world

    left = evolve(tmp_path / "left", (0, 1))
    right = evolve(tmp_path / "right", (1, 0))

    assert (tmp_path / "left" / "a.py").read_bytes() == (
        tmp_path / "right" / "a.py"
    ).read_bytes()
    assert (tmp_path / "left" / "b.py").read_bytes() == (
        tmp_path / "right" / "b.py"
    ).read_bytes()
    assert left.program.world_revision == right.program.world_revision
    for locator in ("a.alpha_next", "b.beta_next"):
        assert left.program.entity(locator).id == right.program.entity(locator).id
        assert (
            left.program.entity(locator).revision_hash
            == right.program.entity(locator).revision_hash
        )


def test_world_level_rollback_restores_sources_when_rescan_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _write(tmp_path, "module.py", "def alpha():\n    return 1\n")
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("module.alpha")
    capability = EditCapability.rename(
        target.id, allow_public_api_break=True
    )
    plan = world.plan_rename(target.id, "beta", capability)
    original = (tmp_path / "module.py").read_bytes()

    def fail_scan(*args, **kwargs):
        raise RuntimeError("forced rescan failure")

    monkeypatch.setattr("merlo.world.scan_python", fail_scan)
    with pytest.raises(WorldError, match="rolled back"):
        world.apply(plan, capability)

    assert (tmp_path / "module.py").read_bytes() == original
    assert world.program.entity(target.id).name == "alpha"
    assert world.evolution_log[-1]["result"] == "rolled_back"


def test_multi_file_os_failure_rolls_back_every_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _write(tmp_path, "api.py", "def alpha():\n    return 1\n")
    _write(
        tmp_path,
        "consumer.py",
        "from api import alpha\nvalue = alpha()\n",
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("api.alpha")
    capability = EditCapability.rename(
        target.id, allow_public_api_break=True
    )
    plan = world.plan_rename(target.id, "beta", capability)
    originals = {
        path.name: path.read_bytes() for path in tmp_path.glob("*.py")
    }

    import merlo.evolution as evolution

    real_replace = evolution.os.replace
    calls = 0

    def fail_second_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("forced second-file failure")
        return real_replace(source, destination)

    monkeypatch.setattr(evolution.os, "replace", fail_second_replace)
    with pytest.raises(WorldError, match="rolled back"):
        world.apply(plan, capability)

    assert {
        path.name: path.read_bytes() for path in tmp_path.glob("*.py")
    } == originals


def test_world_storage_v1_migrates_atomically_and_deterministically(tmp_path: Path):
    state = tmp_path / "world.json"
    _write(tmp_path, "module.py", "def alpha():\n    return 1\n")
    initial = SoftwareWorld.scan(tmp_path, state)
    entity_id = initial.program.entity("module.alpha").id
    payload = {
        "schema": 1,
        "project": "Meldra",
        "root": str(tmp_path),
        "program": initial.program.to_dict(),
        "evolution_log": [],
    }
    payload["program"]["schema"] = 1
    payload["program"]["analyzer_version"] = "python-0.1"
    for entity in payload["program"]["entities"]:
        entity.pop("identity_features", None)
    state.write_text(json.dumps(payload), encoding="utf-8")

    migrated = SoftwareWorld.scan(tmp_path, state)
    migrated.save()
    first = state.read_bytes()
    migrated.save()

    assert json.loads(first)["schema"] == 2
    assert state.read_bytes() == first
    assert migrated.program.entity(entity_id).identity_status == "Exact"


def test_corrupt_world_and_foreign_root_are_rejected(tmp_path: Path):
    state = tmp_path / "world.json"
    _write(tmp_path, "module.py", "def alpha():\n    return 1\n")
    state.write_text("not-json", encoding="utf-8")
    with pytest.raises(WorldError, match="cannot read"):
        SoftwareWorld.scan(tmp_path, state)

    other = tmp_path / "other"
    other.mkdir()
    foreign = {
        "schema": 2,
        "project": "Meldra",
        "root": str(other),
        "program": {"schema": 2, "root": str(other), "entities": []},
    }
    state.write_text(json.dumps(foreign), encoding="utf-8")
    with pytest.raises(WorldError, match="belongs"):
        SoftwareWorld.scan(tmp_path, state)


def test_obligation_and_context_remain_available_through_current_api(
    tmp_path: Path,
):
    _write(tmp_path, "api.py", "def public_api():\n    return 1\n")
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("api.public_api")
    protocol = MerloProtocol(world)
    plan = protocol.preview_rename(
        target.id,
        "renamed",
        EditCapability.rename(target.id),
    )
    obligation_id = next(
        item.id
        for item in plan.obligations
        if item.kind == "PublicApiCompatibility"
    )

    obligation = protocol.obligation(obligation_id)
    assert obligation["id"] == obligation_id
    context = protocol.compile_context(
        target.id,
        goal="inspect",
    ).to_dict()
    assert context["kind"] == "TaskCapsule"
