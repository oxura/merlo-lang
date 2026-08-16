from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from merlo.native_c_backend import compile_c_source
from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import (
    RepresentationCompileError,
    build_type_descriptors,
    lower_structured_hir_to_rir,
)
from merlo.representation_mir import (
    lower_rir_to_performance_mir,
    optimize_general_mir,
)
from merlo.structured_hir_v2 import (
    compile_canonical_hir,
    compile_structured_hir,
)
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface


def run_native(
    source: str,
    tmp_path: Path,
    stem: str,
    payload: bytes = b"",
) -> bytes:
    canonical = elaborate_surface(
        parse_surface(source, path=f"{stem}.mlo")
    ).canonical
    hir = compile_canonical_hir(canonical)
    representation = lower_structured_hir_to_rir(hir)
    mir = optimize_general_mir(
        lower_rir_to_performance_mir(hir, representation)
    )
    generated = emit_general_c(hir, representation, mir)
    build = compile_c_source(
        generated.source,
        output_dir=tmp_path,
        stem=stem,
    )

    assert build.status == "MEASURED", build.stderr
    assert build.binary_path is not None
    completed = subprocess.run(
        [build.binary_path],
        input=payload,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    return completed.stdout


def test_boxed_recursive_enum_traverses_and_drops_once(
    tmp_path: Path,
) -> None:
    source = (
        "enum Tree:\n"
        "    Leaf: UInt64\n"
        "    Branch: Box[Tree]\n"
        "fn depth(tree: Tree) -> UInt64:\n"
        "    match tree:\n"
        "        case Tree.Leaf(value):\n"
        "            return value\n"
        "        case Tree.Branch(child):\n"
        "            return 1 + depth(child.get())\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let leaf: Tree = Tree.Leaf(7)\n"
        "    let root: Tree = Tree.Branch(Box.new(leaf))\n"
        "    depth(root)\n"
    )

    output = run_native(source, tmp_path, "recursive-box")
    assert b"OK result=8" in output
    assert b"allocations=1 frees=1" in output
    assert b"box_allocations=1 box_frees=1" in output
    assert b"ast_nodes_allocated=2 ast_nodes_freed=2" in output


def test_recursive_vector_drops_initialized_elements_before_buffer(
    tmp_path: Path,
) -> None:
    source = (
        "enum Tree:\n"
        "    Leaf: UInt64\n"
        "    Branch: Vec[Tree]\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let children: Vec[Tree] = Vec.new()\n"
        "    children.push(Tree.Leaf(1))\n"
        "    children.push(Tree.Leaf(2))\n"
        "    let root: Tree = Tree.Branch(children)\n"
        "    input.len()\n"
    )

    output = run_native(
        source,
        tmp_path,
        "recursive-vector",
        payload=b"abc",
    )
    assert b"OK result=3" in output
    assert b"vec_allocations=1 vec_frees=1" in output
    assert b"vec_initialized=2 vec_elements_dropped=2" in output
    assert b"ast_nodes_allocated=3 ast_nodes_freed=3" in output


def test_mutually_recursive_boxed_enums_have_finite_drop_glue(
    tmp_path: Path,
) -> None:
    source = (
        "enum Left:\n"
        "    End\n"
        "    Next: Box[Right]\n"
        "enum Right:\n"
        "    End\n"
        "    Next: Box[Left]\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let right: Right = Right.End\n"
        "    let left: Left = Left.Next(Box.new(right))\n"
        "    input.len()\n"
    )

    output = run_native(
        source,
        tmp_path,
        "mutual-recursion",
        payload=b"x",
    )
    assert b"OK result=1" in output
    assert b"box_allocations=1 box_frees=1" in output
    assert b"ast_nodes_allocated=2 ast_nodes_freed=2" in output


def test_inline_recursive_layout_requires_owning_indirection() -> None:
    hir = compile_structured_hir(
        "record Node:\n"
        "    next: Node\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return input.len()\n"
    )

    with pytest.raises(
        RepresentationCompileError,
        match=(
            "InlineRecursiveLayout: Node -> Node; "
            "add Box or Vec indirection"
        ),
    ):
        build_type_descriptors(hir)
