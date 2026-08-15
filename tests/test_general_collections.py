from __future__ import annotations

import subprocess
from pathlib import Path

from merlo.collection_protocol import collection_shape
from merlo.native_c_backend import compile_c_source
from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import (
    lower_rir_to_performance_mir,
    optimize_general_mir,
)
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface


SOURCE = (
    "record Item:\n"
    "    active: Bool\n"
    "    score: UInt64\n"
    "fn main(input: BytesView) -> UInt64:\n"
    "    let items: Array[Item,2] = "
    "[Item(true, 3), Item(false, 5)]\n"
    "    let active: Vec[Item] = items.where(.active)\n"
    "    let scores: Vec[UInt64] = active.map(.score)\n"
    "    let active_view = active.view()\n"
    "    let viewed: UInt64 = active_view.count(.active)\n"
    "    let selected: UInt64 = items.count(.active)\n"
    "    var total: UInt64 = 0\n"
    "    for item in items:\n"
    "        total = total + item.score\n"
    "    scores[0] + selected + viewed + total\n"
)


def elaborate(source: str = SOURCE):
    return elaborate_surface(parse_surface(source, path="collections.mlo"))


def test_general_collection_shapes_cover_owned_fixed_borrowed_and_text_data() -> None:
    expected = {
        "Vec[UInt64]": ("vec", "UInt64", None),
        "Borrow[Vec[UInt64]]": ("vec", "UInt64", None),
        "Array[Text,4]": ("array", "Text", 4),
        "Slice[Byte]": ("slice", "Byte", None),
        "Bytes": ("bytes", "Byte", None),
        "BytesView": ("bytes_view", "Byte", None),
        "Text": ("text", "Byte", None),
        "TextView": ("text_view", "Byte", None),
    }

    assert {
        type_name: (
            collection_shape(type_name).kind,
            collection_shape(type_name).element_type,
            collection_shape(type_name).fixed_length,
        )
        for type_name in expected
    } == expected
    assert collection_shape("UInt64") is None


def test_array_collection_operations_share_one_hir_protocol() -> None:
    hir = compile_canonical_hir(elaborate().canonical)
    operations = [
        node
        for node in hir.function("main").walk()
        if node.kind == "CollectionOperation"
    ]

    assert [item.attribute_map["collection_operation"] for item in operations] == [
        "where",
        "map",
        "count",
        "count",
    ]
    assert {item.attribute_map["collection_kind"] for item in operations} == {
        "array",
        "vec",
    }
    assert [item.type_name for item in operations] == [
        "Vec[Item]",
        "Vec[UInt64]",
        "UInt64",
        "UInt64",
    ]
    assert not any(item.kind == "VecOperation" for item in operations)


def test_general_collection_transforms_and_iteration_run_natively(
    tmp_path: Path,
) -> None:
    hir = compile_canonical_hir(elaborate().canonical)
    representation = lower_structured_hir_to_rir(hir)
    mir = optimize_general_mir(
        lower_rir_to_performance_mir(hir, representation)
    )
    generated = emit_general_c(hir, representation, mir)
    build = compile_c_source(
        generated.source,
        output_dir=tmp_path,
        stem="general-collections",
    )

    assert build.status == "MEASURED", build.stderr
    assert build.binary_path is not None
    completed = subprocess.run(
        [build.binary_path],
        input=b"unused",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert b"OK result=13" in completed.stdout
    assert b"vec_allocations=2 vec_frees=2" in completed.stdout


def test_text_and_bytes_are_indexable_general_collections(
    tmp_path: Path,
) -> None:
    source = (
        "fn nonzero(value: Byte) -> Bool:\n"
        "    let zero: Byte = 0\n"
        "    value != zero\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let first: Byte = input[0]\n"
        "    input.count(nonzero) + (first + 0)\n"
    )
    hir = compile_canonical_hir(elaborate(source).canonical)
    index = next(node for node in hir.function("main").walk() if node.kind == "Index")
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    generated = emit_general_c(hir, representation, mir)
    build = compile_c_source(
        generated.source,
        output_dir=tmp_path,
        stem="bytes-index",
    )

    assert index.type_name == "Byte"
    assert build.status == "MEASURED", build.stderr
    completed = subprocess.run(
        [build.binary_path],
        input=b"ABC",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert b"OK result=68" in completed.stdout


def test_iterable_constraint_uses_the_general_collection_protocol() -> None:
    source = (
        "fn retain[T: Iterable](value: T) -> T:\n"
        "    value\n"
        "fn keep_text(value: Text) -> Text:\n"
        "    retain(value)\n"
        "fn main(values: Array[UInt64,2]) -> Array[UInt64,2]:\n"
        "    retain(values)\n"
    )

    canonical = elaborate(source).canonical
    instances = [item for item in canonical.functions if item.name.startswith("retain__mono_")]
    assert {item.return_type for item in instances} == {
        "Text",
        "Array[UInt64,2]",
    }
