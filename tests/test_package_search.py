from __future__ import annotations

from pathlib import Path

import pytest

from merlo.package_search import search_package_candidates
from merlo.semantic_world import SemanticWorld, StaleWorldError, WorldError
from merlo.synthesis import SynthesisRequest


def _world(tmp_path: Path) -> tuple[SemanticWorld, Path]:
    app = tmp_path / "app"
    app.mkdir()
    source = app / "main.mlo"
    source.write_text(
        "module app.main\n"
        "use app.lib\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn local_value() -> UInt64:\n"
        "    1\n\n"
        "fn wrong_type() -> Text:\n"
        "    \"wrong\"\n\n"
        "fn target() -> UInt64:\n"
        "    ?\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"ok\")\n"
        "    Ok(\"ok\")\n",
        encoding="utf-8",
    )
    (app / "lib.mlo").write_text(
        "module app.lib\n\n"
        "export fn external_value() -> UInt64:\n"
        "    2\n\n"
        "fn private_value() -> UInt64:\n"
        "    3\n\n"
        "export fn external_text() -> Text:\n"
        "    \"text\"\n",
        encoding="utf-8",
    )
    lockfile = tmp_path / "merlo.lock"
    lockfile.write_text("locked", encoding="utf-8")
    return (
        SemanticWorld.build(
            source,
            lockfile=lockfile,
            require_interface_lock=False,
        ),
        source,
    )


def _request(
    world: SemanticWorld,
    *,
    hole_id: str | None = None,
    maximum: int | None = None,
) -> SynthesisRequest:
    actual = world.resolve("app.main.target")["holes"][0][
        "hole_id"
    ]
    arguments: dict[str, object] = {
        "hole_id": hole_id or actual,
    }
    if maximum is not None:
        arguments["max_candidates"] = maximum
    return SynthesisRequest(
        world.digest,
        "app.main.target",
        "fill_hole",
        arguments,
    )


def test_package_search_is_ranked_bound_and_read_only(
    tmp_path: Path,
) -> None:
    world, source = _world(tmp_path)
    before = source.read_bytes()

    candidates = search_package_candidates(
        world,
        _request(world),
    )

    assert [
        item.provenance["expression"]
        for item in candidates
    ] == ["local_value()", "external_value()"]
    assert [item.rank.priority for item in candidates] == [0, 1]
    assert all(
        item.producer == "package"
        and item.change_ir.operation == "fill_hole"
        and item.provenance["lock_digest"]
        == world.data["lockfile_sha256"]
        for item in candidates
    )
    assert source.read_bytes() == before

    bounded = search_package_candidates(
        world,
        _request(world, maximum=1),
    )
    assert len(bounded) == 1
    assert bounded[0].provenance["expression"] == "local_value()"


def test_package_search_rejects_stale_and_unknown_holes(
    tmp_path: Path,
) -> None:
    world, _ = _world(tmp_path)
    request = _request(world)
    stale = SynthesisRequest(
        "different",
        request.target,
        request.operation,
        request.arguments,
    )
    with pytest.raises(StaleWorldError):
        search_package_candidates(world, stale)
    with pytest.raises(WorldError, match="PackageHoleNotOwned"):
        search_package_candidates(
            world,
            _request(world, hole_id="missing"),
        )
