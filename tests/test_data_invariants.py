from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from merlo.compiler import compile_project
from merlo.native_c_backend import compile_c_source
from merlo.project import Project, resolve_dependencies
from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import (
    lower_rir_to_performance_mir,
    optimize_general_mir,
)
from merlo.semantic_world import SemanticWorld
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.surface_elaborator import SurfaceElaborationError, elaborate_surface
from merlo.surface_parser import parse_surface


SOURCE = """record Positive:
    value: UInt64
    invariant value > 0

fn checked(value: UInt64) -> UInt64:
    let item = Positive(value: value)
    item.value

fn main(input: BytesView) -> UInt64:
    checked(input.len())
"""


def _lower(source: str = SOURCE):
    canonical = elaborate_surface(parse_surface(source)).canonical
    hir = compile_canonical_hir(canonical)
    representation = lower_structured_hir_to_rir(hir)
    mir = optimize_general_mir(
        lower_rir_to_performance_mir(hir, representation)
    )
    generated = emit_general_c(hir, representation, mir)
    return canonical, hir, representation, generated


def test_record_invariant_survives_every_typed_layer() -> None:
    canonical, hir, representation, _generated = _lower()

    assert [
        item.expression for item in canonical.records[0].invariants
    ] == ["value > 0"]
    assert [item.expression for item in hir.types[0].invariants] == [
        "value > 0"
    ]
    descriptor = representation.descriptor("Positive")
    assert descriptor.invariants == ((
        "__merlo_invariant_Positive_0",
        3,
    ),)


@pytest.mark.parametrize(
    "construction",
    ("Positive(value: value)", "Positive(value)"),
)
def test_native_record_constructor_enforces_invariant(
    tmp_path: Path,
    construction: str,
) -> None:
    source = SOURCE.replace("Positive(value: value)", construction)
    _canonical, _hir, _representation, generated = _lower(source)
    build = compile_c_source(
        generated.source,
        output_dir=tmp_path,
        stem="data-invariant",
    )
    assert build.binary_path is not None, build.stderr

    accepted = subprocess.run(
        [build.binary_path],
        input=b"x",
        capture_output=True,
        check=False,
    )
    rejected = subprocess.run(
        [build.binary_path],
        input=b"",
        capture_output=True,
        check=False,
    )

    assert accepted.returncode == 0
    assert b"OK result=1" in accepted.stdout
    assert rejected.returncode != 0
    assert (
        b"MerloContractViolation:invariant:Positive:3"
        in rejected.stderr
    )


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    (
        (
            "record Bad:\n"
            "    value: UInt64\n"
            "    invariant value\n",
            "TypeConflict",
        ),
        (
            "record Bad:\n"
            "    value: UInt64\n"
            "    invariant missing > 0\n",
            "UnresolvedName: __merlo_invariant_Bad_0.missing",
        ),
        (
            "task live() -> Bool:\n"
            "    uses clock.now\n"
            "    clock.now() > 0\n\n"
            "record Bad:\n"
            "    value: UInt64\n"
            "    invariant live()\n",
            "EffectInInvariant: Bad",
        ),
    ),
)
def test_invalid_record_invariants_are_rejected(
    source: str,
    diagnostic: str,
) -> None:
    with pytest.raises(SurfaceElaborationError, match=diagnostic):
        elaborate_surface(parse_surface(source))


def test_invariant_changes_record_semantic_revision() -> None:
    with_invariant = elaborate_surface(parse_surface(SOURCE)).canonical
    without_invariant = elaborate_surface(
        parse_surface(SOURCE.replace("    invariant value > 0\n", ""))
    ).canonical

    assert with_invariant.semantic_hash != without_invariant.semantic_hash
    with_hir = compile_canonical_hir(with_invariant)
    without_hir = compile_canonical_hir(without_invariant)
    assert with_hir.types[0].revision_id != without_hir.types[0].revision_id


def test_semantic_world_exposes_record_invariants(
    tmp_path: Path,
) -> None:
    project = Project.create(tmp_path / "invariant-project")
    (project.source_dir / "main.mlo").write_text(
        "module main\n\n"
        "export record Positive:\n"
        "    value: UInt64\n"
        "    invariant value > 0\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    console.write(\"ok\")\n"
        "    Ok(\"ok\")\n",
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

    symbol = world.resolve("main.Positive")
    assert symbol["invariants"] == ["value > 0"]
    assert world.compile_context("main.Positive").invariants == (
        "value > 0",
    )
