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
    result = protocol.call("refactor.rename", {"target": "app.main.helper", "new_name": "assist", "mode": "apply"})
    assert result["committed"] is True
    assert "assist" in source.read_text(encoding="utf-8")


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
