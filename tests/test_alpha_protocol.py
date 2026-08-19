from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "app" / "main.mlo"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "module app.main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "task helper(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    # helper remains documentation\n"
        "    console.write(\"helper\")\n"
        "    return Ok(\"helper\")\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"main\")\n"
        "    return helper(path)\n",
        encoding="utf-8",
    )
    return path


def test_protocol_rename_preview_is_exact_and_apply_is_transactional(tmp_path: Path) -> None:
    from merlo.alpha_protocol import AlphaProtocol
    from merlo.semantic_world import SemanticWorld

    source = _source(tmp_path)
    protocol = AlphaProtocol(SemanticWorld.build(source, require_interface_lock=False))
    preview = protocol.call("refactor.rename", {"target": "app.main.helper", "new_name": "assist", "mode": "preview"})
    assert preview["operation"] == "rename"
    assert preview["schema_version"] == 1
    assert preview["contract"] == "merlo.change-ir.v1"
    assert preview["status"] == "ready"
    assert preview["digest"]
    assert preview["target"]["revision_id"]
    assert preview["edits"]
    assert all(edit["syntax_id"] for edit in preview["edits"])
    assert all(edit["token_id"] for edit in preview["edits"])
    assert all(isinstance(edit["token_ordinal"], int) for edit in preview["edits"])
    result = protocol.call("refactor.rename", {"target": "app.main.helper", "new_name": "assist", "mode": "apply"})
    assert result["committed"] is True
    from merlo.transaction import load_transaction

    transaction = load_transaction(
        source.parent,
        result["transaction"]["transaction_id"],
    )
    rolled_back = transaction.rollback()
    assert rolled_back.changed is True
    assert "task helper" in source.read_text(
        encoding="utf-8"
    )
    replayed = transaction.replay()
    assert replayed.changed is True
    updated = source.read_text(encoding="utf-8")
    assert "task assist" in updated
    assert "return assist(path)" in updated
    assert "# helper remains documentation" in updated
    assert 'console.write("helper")' in updated
    assert 'return Ok("helper")' in updated



def test_protocol_computes_change_bound_semantic_impact(
    tmp_path: Path,
) -> None:
    from merlo.alpha_protocol import AlphaProtocol
    from merlo.semantic_world import SemanticWorld

    source = _source(tmp_path)
    protocol = AlphaProtocol(
        SemanticWorld.build(
            source,
            require_interface_lock=False,
        )
    )
    change = protocol.call(
        "refactor.rename",
        {
            "target": "app.main.helper",
            "new_name": "assist",
        },
    )

    impact = protocol.call(
        "impact.change",
        {"change": change},
    )

    assert impact["contract"] == (
        "merlo.semantic-impact.v1"
    )
    assert impact["change_digest"] == change["digest"]
    assert impact["target_symbol_id"] == (
        change["target"]["symbol_id"]
    )
    assert impact["status"] == "ready"
    assert impact["directly_changed"]

def test_protocol_rejects_stale_and_unsupported_migrations_without_partial_write(tmp_path: Path) -> None:
    from merlo.alpha_protocol import AlphaProtocol
    from merlo.semantic_world import SemanticWorld
    from merlo.semantic_world import StaleWorldError

    source = _source(tmp_path)
    world = SemanticWorld.build(source, require_interface_lock=False)
    protocol = AlphaProtocol(world)
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(StaleWorldError):
        protocol.call("refactor.rename", {"target": "app.main.helper", "new_name": "assist", "mode": "apply"})
    assert "helper" in source.read_text(encoding="utf-8")
    unsupported = protocol.call("refactor.move", {"target": "app.main.helper", "module": "app.other", "mode": "preview"})
    assert unsupported["diagnostic"]["code"] == "UnsupportedMigration"
    assert unsupported["status"] == "unsupported"
    assert unsupported["edits"] == []


def test_move_private_function_tracks_symbol_lineage_and_compiles(
    tmp_path: Path,
) -> None:
    from merlo.compiler import compile_project
    from merlo.alpha_protocol import AlphaProtocol
    from merlo.refactor import ChangeIR, preview_move
    from merlo.semantic_world import SemanticWorld

    app = tmp_path / "app"
    app.mkdir()
    main = app / "main.mlo"
    bridge = app / "bridge.mlo"
    library = app / "lib.mlo"
    main.write_text(
        "module app.main\n"
        "use app.bridge\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn helper(value: Text) -> Text:\n"
        "    return value\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        '    console.write(helper("moved"))\n'
        '    return Ok(helper("ok"))\n',
        encoding="utf-8",
    )
    bridge.write_text(
        "module app.bridge\n"
        "use app.lib\n\n"
        "export fn bridge(value: UInt64) -> UInt64:\n"
        "    return existing(value)\n",
        encoding="utf-8",
    )
    library.write_text(
        "module app.lib\n\n"
        "export fn existing(value: UInt64) -> UInt64:\n"
        "    return value\n",
        encoding="utf-8",
    )
    world = SemanticWorld.build(
        main,
        require_interface_lock=False,
    )
    change = preview_move(
        world,
        "app.main.helper",
        "app.lib",
    )

    assert change.status == "ready"
    assert change.metadata["old_symbol_id"] == change.target.symbol_id
    assert change.metadata["new_symbol_id"] != change.target.symbol_id
    assert change.metadata["from_module"] == "app.main"
    assert change.metadata["module"] == "app.lib"
    assert {item.kind for item in change.edits} == {
        "move_declaration",
        "move_destination",
        "move_import",
    }
    restored = ChangeIR.from_json(change.to_json(), world=world)
    assert restored.to_dict() == change.to_dict()

    result = AlphaProtocol(world).call(
        "refactor.move",
        {
            "target": "app.main.helper",
            "module": "app.lib",
            "mode": "apply",
        },
    )
    assert result["committed"] is True
    assert result["lineage"] == {
        "old_symbol_id": change.target.symbol_id,
        "new_symbol_id": change.metadata["new_symbol_id"],
    }
    assert "fn helper" not in main.read_text(encoding="utf-8")
    assert "use app.lib" in main.read_text(encoding="utf-8")
    assert "export fn helper" in library.read_text(encoding="utf-8")
    compilation = compile_project(main, require_interface_lock=False)
    moved_world = SemanticWorld.build(
        compilation,
        require_interface_lock=False,
    )
    assert moved_world.resolve("app.lib.helper")["symbol_id"] == (
        change.metadata["new_symbol_id"]
    )


def test_move_rejects_forged_lineage_and_unsafe_dependencies(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from merlo.refactor import preview_move
    from merlo.semantic_world import SemanticWorld, WorldError

    app = tmp_path / "app"
    app.mkdir()
    main = app / "main.mlo"
    library = app / "lib.mlo"
    main.write_text(
        "module app.main\n"
        "use app.lib\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn local(value: Text) -> Text:\n"
        "    return value\n\n"
        "fn helper(value: Text) -> Text:\n"
        "    return local(value)\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        '    console.write(helper("moved"))\n'
        '    return Ok("ok")\n',
        encoding="utf-8",
    )
    library.write_text(
        "module app.lib\n\n"
        "export fn existing(value: UInt64) -> UInt64:\n"
        "    return value\n",
        encoding="utf-8",
    )
    world = SemanticWorld.build(main, require_interface_lock=False)
    unsafe = preview_move(world, "app.main.helper", "app.lib")
    assert unsafe.status == "unsupported"
    assert "safe alpha subset" in unsafe.diagnostic.message

    safe = preview_move(world, "app.main.local", "app.lib")
    assert safe.status == "ready"
    forged = replace(
        safe,
        metadata={
            **dict(safe.metadata),
            "new_symbol_id": "sym_forged",
        },
        digest="",
    )
    before = {
        main: main.read_bytes(),
        library: library.read_bytes(),
    }
    with pytest.raises(WorldError, match="ChangeIRMoveLineageMismatch"):
        forged.apply()
    assert {path: path.read_bytes() for path in before} == before


def test_protocol_rename_uses_only_semantic_spans_for_nested_calls(tmp_path: Path) -> None:
    from merlo.alpha_protocol import AlphaProtocol
    from merlo.semantic_world import SemanticWorld

    source = tmp_path / "app" / "main.mlo"
    source.parent.mkdir(parents=True)
    source.write_text(
        "module app.main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn helper(value: UInt64) -> UInt64:\n"
        "    return value\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        '    console.write("main")\n'
        "    let value: UInt64 = helper(helper(1))\n"
        '    return Ok("main")\n',
        encoding="utf-8",
    )
    protocol = AlphaProtocol(
        SemanticWorld.build(source, require_interface_lock=False)
    )

    preview = protocol.call(
        "refactor.rename",
        {
            "target": "app.main.helper",
            "new_name": "assist",
            "mode": "preview",
        },
    )
    edits = preview["edits"]
    assert len(edits) == 3
    assert len({(item["start"], item["end"]) for item in edits}) == 3
    assert len({item["token_id"] for item in edits}) == 3

    result = protocol.call(
        "refactor.rename",
        {
            "target": "app.main.helper",
            "new_name": "assist",
            "mode": "apply",
        },
    )
    assert result["committed"] is True
    updated = source.read_text(encoding="utf-8")
    assert "fn assist" in updated
    assert "assist(assist(1))" in updated
    assert "helper" not in updated


def test_change_signature_is_structural_verified_and_transactional(
    tmp_path: Path,
) -> None:
    from merlo.alpha_protocol import AlphaProtocol
    from merlo.compiler import compile_project
    from merlo.refactor import ChangeIR, preview_change_signature
    from merlo.semantic_world import (
        SemanticWorld,
        UnsupportedMigration,
        WorldError,
    )
    from merlo.transaction import load_transaction

    source = tmp_path / "signature" / "main.mlo"
    source.parent.mkdir(parents=True)
    source.write_text(
        "module signature\n\n"
        "fn identity(value: UInt64) -> UInt64:\n"
        "    value\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    let answer = identity(7)\n"
        "    console.write(\"signature\")\n"
        "    Ok(\"signature\")\n",
        encoding="utf-8",
    )
    world = SemanticWorld.build(source, require_interface_lock=False)
    before = source.read_bytes()
    change = preview_change_signature(
        world,
        "signature.identity",
        "(value: Int64) -> Int64",
    )
    assert change.status == "ready"
    assert change.edits[0].kind == "signature"
    assert change.metadata["old_signature"] == (
        "(value: UInt64) -> UInt64"
    )
    assert ChangeIR.from_json(
        change.to_json(),
        world=world,
    ).to_json() == change.to_json()

    spoofed = replace(
        change,
        edits=(
            replace(
                change.edits[0],
                replacement="(value: Text) -> Text",
            ),
        ),
        digest="",
    )
    with pytest.raises(WorldError, match="SemanticEditMismatch"):
        spoofed.apply()
    assert source.read_bytes() == before

    forged = replace(
        change,
        metadata={
            "old_signature": "(value: UInt64) -> UInt64",
            "signature": "(value: Text) -> Text",
        },
        edits=(
            replace(
                change.edits[0],
                replacement="(value: Text) -> Text",
            ),
        ),
        digest="",
    )
    with pytest.raises(
        UnsupportedMigration,
        match="no longer type-checks",
    ):
        forged.apply()
    assert source.read_bytes() == before

    receipt = AlphaProtocol(world).call(
        "refactor.signature.apply",
        {
            "target": "signature.identity",
            "signature": "(value: Int64) -> Int64",
        },
    )
    assert receipt["committed"] is True
    assert "identity(value: Int64) -> Int64" in source.read_text(
        encoding="utf-8"
    )
    compile_project(source, require_interface_lock=False)
    transaction = load_transaction(
        source.parent,
        receipt["transaction"]["transaction_id"],
    )
    assert transaction.rollback().changed is True
    assert source.read_bytes() == before


def test_change_signature_rejects_incompatible_callers_without_writes(
    tmp_path: Path,
) -> None:
    from merlo.refactor import preview_change_signature
    from merlo.semantic_world import SemanticWorld

    source = tmp_path / "signature" / "main.mlo"
    source.parent.mkdir(parents=True)
    source.write_text(
        "module signature\n\n"
        "fn identity(value: UInt64) -> UInt64:\n"
        "    value\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    let answer = identity(7)\n"
        "    console.write(\"signature\")\n"
        "    Ok(\"signature\")\n",
        encoding="utf-8",
    )
    world = SemanticWorld.build(source, require_interface_lock=False)
    before = source.read_bytes()
    change = preview_change_signature(
        world,
        "signature.identity",
        "(value: Text) -> Text",
    )
    assert change.status == "unsupported"
    assert change.edits == ()
    assert "caller/body migration" in change.diagnostic.message
    assert source.read_bytes() == before


def test_change_ir_roundtrip_tamper_and_apply_status(
    tmp_path: Path,
) -> None:
    import json

    from merlo.refactor import ChangeIR, preview_move, preview_rename
    from merlo.semantic_world import (
        SemanticWorld,
        StaleWorldError,
        UnsupportedMigration,
        WorldError,
    )

    source = _source(tmp_path)
    world = SemanticWorld.build(source, require_interface_lock=False)
    change = preview_rename(world, "app.main.helper", "assist")

    restored = ChangeIR.from_json(change.to_json(), world=world)
    assert restored.to_dict() == change.to_dict()
    assert restored.digest == change.digest

    tampered = json.loads(change.to_json())
    tampered["metadata"]["new_name"] = "different"
    first = change.edits[0]
    with pytest.raises(WorldError, match="sorted and unique"):
        replace(
            change,
            edits=(first, first),
            digest="",
        )
    with pytest.raises(WorldError, match="escapes project root"):
        replace(
            change,
            edits=(replace(first, path="/tmp/outside.mlo"),),
            digest="",
        )
    stale_target = replace(
        change.target,
        revision_id="rev_stale",
    )
    stale_change = replace(
        change,
        target=stale_target,
        digest="",
    )
    with pytest.raises(
        StaleWorldError,
        match="target identity changed",
    ):
        stale_change.apply()

    with pytest.raises(WorldError, match="ChangeIRDigestMismatch"):
        ChangeIR.from_dict(tampered, world=world)

    unsupported = preview_move(
        world,
        "app.main.helper",
        "app.other",
    )
    with pytest.raises(UnsupportedMigration):
        unsupported.apply()


def test_change_ir_rejects_operation_spoofing_and_unrelated_tokens(
    tmp_path: Path,
) -> None:
    from merlo.refactor import ChangeIR, preview_rename
    from merlo.semantic_world import SemanticWorld, WorldError

    source = _source(tmp_path)
    world = SemanticWorld.build(source, require_interface_lock=False)
    helper = preview_rename(
        world,
        "app.main.helper",
        "assist",
    )

    with pytest.raises(WorldError, match="ChangeIRInvalidOperation"):
        replace(
            helper,
            operation="move",
            digest="",
        )
    with pytest.raises(
        WorldError,
        match="non-finite numbers",
    ):
        replace(
            helper,
            metadata={"value": float("nan")},
            digest="",
        )

    main = preview_rename(
        world,
        "app.main.main",
        "entry",
    )
    unrelated = replace(
        main.edits[0],
        symbol_id=helper.target.symbol_id,
        replacement="assist",
    )
    spoofed = ChangeIR(
        operation="rename",
        status="ready",
        target=helper.target,
        expected_world_digest=world.digest,
        metadata={
            "old_name": "helper",
            "new_name": "assist",
        },
        edits=(unrelated,),
        world=world,
    )
    with pytest.raises(
        WorldError,
        match="ChangeIRSemanticEditMismatch",
    ):
        spoofed.apply()


def test_protocol_rejects_malformed_shapes_and_conflicting_modes(
    tmp_path: Path,
) -> None:
    from merlo.alpha_protocol import AlphaProtocol
    from merlo.semantic_world import SemanticWorld, WorldError

    source = _source(tmp_path)
    protocol = AlphaProtocol(
        SemanticWorld.build(
            source,
            require_interface_lock=False,
        )
    )
    rename = {
        "target": "app.main.helper",
        "new_name": "assist",
    }

    for operation, params in (
        (None, {}),
        ("world.search", []),
        ("world.search", {"query": 1}),
        (
            "world.callers",
            {
                "target": "app.main.helper",
                "transitive": "false",
            },
        ),
    ):
        with pytest.raises(WorldError):
            protocol.call(operation, params)

    with pytest.raises(
        WorldError,
        match="ConflictingRefactorMode",
    ):
        protocol.call(
            "refactor.rename.preview",
            {**rename, "mode": "apply"},
        )
    with pytest.raises(
        WorldError,
        match="ConflictingRefactorMode",
    ):
        protocol.call(
            "refactor.rename.apply",
            {**rename, "mode": "preview"},
        )
    assert "task helper" in source.read_text(
        encoding="utf-8"
    )

    unsupported = protocol.call(
        "refactor.move.apply",
        {
            "target": "app.main.helper",
            "module": "app.support",
        },
    )
    assert unsupported["status"] == "unsupported"
    assert unsupported["edits"] == []


def test_change_ir_parser_rejects_malformed_nested_shapes(
    tmp_path: Path,
) -> None:
    from merlo.refactor import ChangeIR, preview_rename
    from merlo.semantic_world import SemanticWorld, WorldError

    world = SemanticWorld.build(
        _source(tmp_path),
        require_interface_lock=False,
    )
    payload = preview_rename(
        world,
        "app.main.helper",
        "assist",
    ).to_dict()
    payload["edits"] = None
    with pytest.raises(
        WorldError,
        match="ChangeIRSchemaMismatch",
    ):
        ChangeIR.from_dict(payload, world=world)

    payload = preview_rename(
        world,
        "app.main.helper",
        "assist",
    ).to_dict()
    payload["metadata"] = {"value": float("nan")}
    with pytest.raises(
        WorldError,
        match="ChangeIRSchemaMismatch",
    ):
        ChangeIR.from_dict(payload, world=world)
