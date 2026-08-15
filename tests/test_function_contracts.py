from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from merlo.native_c_backend import compile_c_source
from merlo.compiler import compile_project
from merlo.project import Project, resolve_dependencies
from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import (
    lower_rir_to_performance_mir,
    optimize_general_mir,
)
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.surface_ast import SurfaceEnsure, SurfaceRequire
from merlo.semantic_world import SemanticWorld
from merlo.surface_elaborator import SurfaceElaborationError, elaborate_surface
from merlo.surface_parser import parse_surface


SOURCE = """fn checked(value: UInt64) -> UInt64:
    require value > 0
    ensure result > 0
    if value > 1:
        return value
    0

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
    return canonical, hir, generated


def test_require_and_ensure_survive_canonical_hir_and_c() -> None:
    parsed = parse_surface(SOURCE)
    checked = parsed.declarations[0]
    assert isinstance(checked.body[0], SurfaceRequire)
    assert isinstance(checked.body[1], SurfaceEnsure)

    canonical, hir, generated = _lower()
    function = canonical.function("checked")
    assert [item.expression for item in function.requirements] == ["value > 0"]
    assert [item.expression for item in function.ensures] == ["result > 0"]
    hir_function = next(item for item in hir.functions if item.name == "checked")
    assert [item.expression for item in hir_function.requirements] == ["value > 0"]
    assert [item.expression for item in hir_function.ensures] == ["result > 0"]
    assert "MerloContractViolation" in generated.source
    assert 'merlo_contract_trap("require", "checked"' in generated.source
    assert generated.source.count('merlo_contract_trap("ensure", "checked"') == 2


def test_native_require_and_every_return_ensure_are_enforced(
    tmp_path: Path,
) -> None:
    _canonical, _hir, generated = _lower()
    build = compile_c_source(
        generated.source,
        output_dir=tmp_path,
        stem="function-contracts",
    )
    assert build.binary_path is not None, build.stderr

    accepted = subprocess.run(
        [build.binary_path],
        input=b"ok",
        capture_output=True,
        check=False,
    )
    require_failure = subprocess.run(
        [build.binary_path],
        input=b"",
        capture_output=True,
        check=False,
    )
    ensure_failure = subprocess.run(
        [build.binary_path],
        input=b"x",
        capture_output=True,
        check=False,
    )

    assert accepted.returncode == 0
    assert b"OK result=2" in accepted.stdout
    assert require_failure.returncode != 0
    assert b"MerloContractViolation:require:checked:2" in require_failure.stderr
    assert ensure_failure.returncode != 0
    assert b"MerloContractViolation:ensure:checked:3" in ensure_failure.stderr


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    (
        (
            "fn bad(value: UInt64) -> UInt64:\n"
            "    require result > 0\n"
            "    value\n",
            "RequireResultForbidden",
        ),
        (
            "fn bad(value: UInt64) -> UInt64:\n"
            "    value = value + 1\n"
            "    require value > 0\n"
            "    value\n",
            "ContractClauseAfterBody",
        ),
        (
            "fn bad(value: UInt64) -> UInt64:\n"
            "    if value > 0:\n"
            "        ensure result > 0\n"
            "    value\n",
            "NestedContractClauseForbidden",
        ),
        (
            "fn bad(value: UInt64) -> UInt64:\n"
            "    require value\n"
            "    value\n",
            "TypeConflict",
        ),
        (
            "fn bad() -> Unit:\n"
            "    ensure result == Unit\n"
            "    Unit\n",
            "UnitEnsureResultForbidden",
        ),
    ),
)
def test_invalid_contract_forms_are_rejected(
    source: str,
    diagnostic: str,
) -> None:
    with pytest.raises(SurfaceElaborationError, match=diagnostic):
        elaborate_surface(parse_surface(source))


def test_effectful_contract_expression_is_rejected_after_inference() -> None:
    source = (
        "clock_is_live() -> Bool:\n"
        "    uses clock.now\n"
        "    clock.now() > 0\n"
        "checked(value: UInt64) -> UInt64:\n"
        "    require clock_is_live()\n"
        "    value\n"
    )

    with pytest.raises(SurfaceElaborationError, match="EffectInContract"):
        elaborate_surface(parse_surface(source))


def test_contracts_participate_in_the_semantic_hash() -> None:
    with_contract = elaborate_surface(parse_surface(SOURCE)).canonical
    without_contract = elaborate_surface(
        parse_surface(SOURCE.replace("    ensure result > 0\n", ""))
    ).canonical

    assert with_contract.semantic_hash != without_contract.semantic_hash


def test_public_interfaces_and_semantic_world_expose_contracts(
    tmp_path: Path,
) -> None:
    project = Project.create(tmp_path / "contract-project")
    (project.source_dir / "main.mlo").write_text(
        "module main\n\n"
        "export checked(value: UInt64) -> UInt64:\n"
        "    require value > 0\n"
        "    ensure result >= value\n"
        "    value + 1\n\n"
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
    interface = next(
        item
        for item in compilation.elaborated.interfaces
        if item.name == "checked"
    )
    assert interface.requirements == ("value > 0",)
    assert interface.ensures == ("result >= value",)
    world = SemanticWorld.build(
        compilation,
        require_interface_lock=False,
    )
    symbol = world.resolve("main.checked")
    assert symbol["requirements"] == ["value > 0"]
    assert symbol["ensures"] == ["result >= value"]
    capsule = world.compile_context("main.checked")
    assert capsule["requirements"] == ["value > 0"]
    assert capsule["ensures"] == ["result >= value"]
