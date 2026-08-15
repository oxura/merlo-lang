from __future__ import annotations

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
    assert preview["edits"]
    assert all(edit["syntax_id"] for edit in preview["edits"])
    assert all(edit["token_id"] for edit in preview["edits"])
    assert all(isinstance(edit["token_ordinal"], int) for edit in preview["edits"])
    result = protocol.call("refactor.rename", {"target": "app.main.helper", "new_name": "assist", "mode": "apply"})
    assert result["committed"] is True
    updated = source.read_text(encoding="utf-8")
    assert "task assist" in updated
    assert "return assist(path)" in updated
    assert "# helper remains documentation" in updated
    assert 'console.write("helper")' in updated
    assert 'return Ok("helper")' in updated


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
