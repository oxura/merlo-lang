from __future__ import annotations

from pathlib import Path

import pytest

from merlo.compiler import compile_project
from merlo.native_c_backend import compile_c_source
from merlo.project import Project, resolve_dependencies
from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import lower_rir_to_performance_mir
from merlo.semantic_world import SemanticWorld
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.surface_ast import SurfaceHole, SurfaceTry
from merlo.surface_elaborator import SurfaceElaborationError, elaborate_surface
from merlo.surface_parser import parse_surface


def _lower(source: str):
    canonical = elaborate_surface(parse_surface(source)).canonical
    hir = compile_canonical_hir(canonical)
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    generated = emit_general_c(hir, representation, mir)
    return canonical, hir, representation, mir, generated


def test_bare_hole_and_postfix_try_are_distinct() -> None:
    parsed = parse_surface(
        "fn fill(value: UInt64) -> UInt64:\n"
        "    let answer: UInt64 = ?\n"
        "    value?\n"
    )
    function = parsed.declarations[0]

    assert isinstance(function.body[0].value, SurfaceHole)
    assert isinstance(function.body[1].expression, SurfaceTry)


def test_hole_gets_exact_type_and_visible_scope_context() -> None:
    source = (
        "fn fill(value: UInt64) -> UInt64:\n"
        "    let prior = value + 1\n"
        "    let answer: UInt64 = ?\n"
        "    answer\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    fill(input.len())\n"
    )
    canonical, hir, _representation, _mir, _generated = _lower(source)

    hole = canonical.function("fill").holes[0]
    assert hole.expected_type == "UInt64"
    assert [item.name for item in hole.context] == ["value", "prior"]
    assert {item.name for item in hole.callables} == {"fill", "main"}
    hir_hole = next(
        node
        for node in hir.function("fill").walk()
        if node.kind == "TypedHole"
    )
    assert hir_hole.type_name == "UInt64"
    assert hir_hole.attribute_map["hole_id"] == hole.hole_id


def test_call_argument_and_return_contexts_type_holes() -> None:
    source = (
        "fn accept(value: UInt64) -> UInt64 = value\n"
        "fn delegated() -> UInt64 = accept(?)\n"
        "fn returned() -> UInt64 = ?\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    delegated() + returned()\n"
    )
    canonical = elaborate_surface(parse_surface(source)).canonical

    assert canonical.function("delegated").holes[0].expected_type == (
        "UInt64"
    )
    assert canonical.function("returned").holes[0].expected_type == (
        "UInt64"
    )


def test_unconstrained_hole_is_rejected() -> None:
    with pytest.raises(
        SurfaceElaborationError,
        match="UnconstrainedTypedHole",
    ):
        elaborate_surface(
            parse_surface(
                "fn bad() -> UInt64:\n"
                "    let missing = ?\n"
                "    0\n"
            )
        )


def test_hole_identity_is_deterministic_and_source_sensitive() -> None:
    source = (
        "fn fill() -> UInt64:\n"
        "    ?\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    fill()\n"
    )
    first = elaborate_surface(parse_surface(source)).canonical
    second = elaborate_surface(parse_surface(source)).canonical
    moved = elaborate_surface(
        parse_surface(source.replace("    ?\n", "\n    ?\n"))
    ).canonical

    assert first.function("fill").holes[0].hole_id == (
        second.function("fill").holes[0].hole_id
    )
    assert first.function("fill").holes[0].hole_id != (
        moved.function("fill").holes[0].hole_id
    )


def test_holes_reach_ir_but_never_get_an_executable_fallback(
    tmp_path: Path,
) -> None:
    source = (
        "fn fill() -> UInt64:\n"
        "    ?\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    fill()\n"
    )
    _canonical, _hir, representation, mir, generated = _lower(source)

    assert any(
        operation.op == "typed_hole"
        for function in representation.functions
        for operation in function.walk()
    )
    assert any(
        instruction.op == "typed_hole"
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
    )
    assert generated.source.startswith(
        '#error "TypedHoleNotExecutable:'
    )
    build = compile_c_source(
        generated.source,
        output_dir=tmp_path,
        stem="typed-hole",
    )
    assert build.binary_path is None
    assert "TypedHoleNotExecutable" in build.stderr


def test_semantic_world_exposes_hole_completion_context(
    tmp_path: Path,
) -> None:
    project = Project.create(tmp_path / "hole-project")
    (project.source_dir / "main.mlo").write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    let text: Text = ?\n"
        "    console.write(text)\n"
        "    Ok(text)\n",
        encoding="utf-8",
    )
    resolve_dependencies(project)
    compilation = compile_project(
        project.root,
        require_interface_lock=False,
    )
    world = SemanticWorld.build(
        compilation,
        require_interface_lock=False,
    )

    symbol = world.resolve("main.main")
    assert symbol["holes"][0]["expected_type"] == "Text"
    capsule = world.compile_context("main.main")
    assert capsule["holes"][0]["hole_id"].startswith("hole_")
