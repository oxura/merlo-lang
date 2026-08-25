from __future__ import annotations

from pathlib import Path

import pytest


def _entry(tmp_path: Path) -> Path:
    path = tmp_path / "app" / "main.mlo"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _main_source() -> str:
    return (
        "module app.main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"main\")\n"
        "    return Ok(\"main\")\n"
    )
def test_world_rebuild_is_byte_identical_and_source_located(tmp_path: Path) -> None:
    from merlo.semantic_world import SemanticWorld

    source = _entry(tmp_path)
    source.write_text(_main_source(), encoding="utf-8")
    first = SemanticWorld.build(source, require_interface_lock=False)
    second = SemanticWorld.build(source, require_interface_lock=False)
    assert first.to_json() == second.to_json()
    main = first.inspect("app.main.main")
    assert main["symbol"]["source"]["path"] == str(source.resolve())
    assert main["symbol"]["source"]["line"] == 6


def test_world_exact_resolution_and_stale_rejection(tmp_path: Path) -> None:
    from merlo.semantic_world import SemanticWorld, StaleWorldError, WorldError

    source = _entry(tmp_path)
    source.write_text(_main_source(), encoding="utf-8")
    world = SemanticWorld.build(source, require_interface_lock=False)
    with pytest.raises(WorldError):
        world.resolve("missing")
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(StaleWorldError):
        world.require_fresh()


def test_world_freshness_hashes_raw_source_bytes(
    tmp_path: Path,
) -> None:
    from merlo.semantic_world import SemanticWorld

    source = _entry(tmp_path)
    source.write_bytes(
        _main_source().replace(
            "\n",
            "\r\n",
        ).encode("utf-8")
    )

    world = SemanticWorld.build(
        source,
        require_interface_lock=False,
    )

    world.require_fresh()


def test_world_save_load_and_private_edit_locality(tmp_path: Path) -> None:
    from merlo.semantic_world import SemanticWorld

    source = _entry(tmp_path)
    source.write_text(
        "module app.main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "task helper(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"helper\")\n"
        "    return Ok(\"helper\")\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"main\")\n"
        "    return helper(path)\n",
        encoding="utf-8",
    )
    world = SemanticWorld.build(source, require_interface_lock=False)
    world_path = tmp_path / ".merlo" / "world.json"
    world.save(world_path)
    loaded = SemanticWorld.load(world_path)
    assert loaded.to_json() == world.to_json()
    old_interface = loaded.data["modules"][0]["interface_revision_id"]
    source.write_text(source.read_text(encoding="utf-8").replace("return Ok(\"helper\")", "return Ok(\"helper\")\n"), encoding="utf-8")
    updated = SemanticWorld.build(source, previous=loaded, require_interface_lock=False)
    assert updated.data["modules"][0]["interface_revision_id"] == old_interface
    helper = loaded.resolve("app.main.helper")
    main = loaded.resolve("app.main.main")
    assert helper["source"]["line"] == 6
    assert main["source"]["line"] == 11
    helper_calls = loaded.references(helper["symbol_id"])
    assert any(item["source"]["line"] == 14 for item in helper_calls)


def test_protocol_operations_and_minimal_capsule(tmp_path: Path) -> None:
    from merlo.alpha_protocol import AlphaProtocol
    from merlo.semantic_world import SemanticWorld

    source = _entry(tmp_path)
    source.write_text(_main_source(), encoding="utf-8")
    protocol = AlphaProtocol(SemanticWorld.build(source, require_interface_lock=False))
    for operation in (
        "world.search", "world.inspect", "world.references", "world.callers",
        "world.callees", "world.dependencies", "world.effects", "world.capabilities",
        "world.source", "context.compile", "impact.analyze", "diagnostics.explain",
    ):
        params = {"query": "main"} if operation == "world.search" else {"target": "app.main.main"}
        result = protocol.call(operation, params)
        assert result is not None
    capsule = protocol.call("context.compile", {"target": "app.main.main", "goal": "inspect"})
    assert capsule["schema_version"] == 2
    assert capsule["contract"] == "merlo.semantic-capsule.v2"
    assert capsule["digest"]
    assert capsule["world_digest"]
    assert capsule["target"]["revision_id"]
    assert "transfer_properties" in capsule
    assert capsule["transfer_properties"]
    assert set(next(iter(capsule["transfer_properties"].values()))) == {
        "is_transferable",
        "is_shareable",
        "is_mutable_shareable",
        "is_resource_transferable",
        "is_thread_safe",
        "is_device_transferable",
        "is_pinned",
        "requires_owner_proof",
    }


def test_world_uses_custom_lockfile_provenance(tmp_path: Path) -> None:
    from merlo.semantic_world import SemanticWorld, StaleWorldError

    source = _entry(tmp_path)
    source.write_text(_main_source(), encoding="utf-8")
    lockfile = tmp_path / "custom.lock"
    lockfile.write_text("lock-v1", encoding="utf-8")
    world = SemanticWorld.build(source, lockfile=lockfile, require_interface_lock=False)
    assert world.data["lockfile_path"] == str(lockfile.resolve())
    lockfile.write_text("lock-v2", encoding="utf-8")
    with pytest.raises(StaleWorldError):
        world.require_fresh()


def test_world_indexes_calls_in_imported_modules(tmp_path: Path) -> None:
    from merlo.semantic_world import SemanticWorld

    source = _entry(tmp_path)
    imported = tmp_path / "app" / "lib.mlo"
    source.write_text(
        _main_source().replace("module app.main\n", "module app.main\nuse app.lib\n"),
        encoding="utf-8",
    )
    imported.write_text(
        "module app.lib\n\n"
        "export fn helper(path: Path) -> Text:\n"
        "    return helper(path)\n",
        encoding="utf-8",
    )
    world = SemanticWorld.build(source, require_interface_lock=False)
    helper = world.resolve("app.lib.helper")
    assert any(item["source"]["path"] == str(imported.resolve()) for item in world.references(helper["symbol_id"]))


def test_world_resolves_duplicate_names_by_module(
    tmp_path: Path,
) -> None:
    from merlo.semantic_world import SemanticWorld

    source = _entry(tmp_path)
    imported = tmp_path / "app" / "lib.mlo"
    source.write_text(
        "module app.main\n"
        "use app.lib\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn helper(path: Path) -> Text:\n"
        "    \"local\"\n\n"
        "export task main(path: Path) -> "
        "Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"main\")\n"
        "    return Ok(helper(path))\n",
        encoding="utf-8",
    )
    imported.write_text(
        "module app.lib\n\n"
        "export fn helper(value: Byte) -> Byte:\n"
        "    helper(value)\n",
        encoding="utf-8",
    )

    world = SemanticWorld.build(
        source,
        require_interface_lock=False,
    )
    local = world.resolve("app.main.helper")
    external = world.resolve("app.lib.helper")

    assert {
        item["owner_id"]
        for item in world.references(local["symbol_id"])
    } == {
        world.resolve("app.main.main")["symbol_id"]
    }
    assert {
        item["owner_id"]
        for item in world.references(
            external["symbol_id"]
        )
    } == {external["symbol_id"]}
