from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import MapDesc, RepresentationCompileError, lower_structured_hir_to_rir
from merlo.representation_mir import lower_rir_to_performance_mir, optimize_general_mir
from merlo.structured_hir_v2 import StructuredHIRCompileError, compile_structured_hir


MAP_SOURCE = """
fn main(input: BytesView) -> UInt64:
    let counts: Map[Text, UInt64] = Map.new()
    let k0: Text = Text.from_bytes(input, 0, 1)
    let k1: Text = Text.from_bytes(input, 1, 2)
    let k2: Text = Text.from_bytes(input, 2, 3)
    let k3: Text = Text.from_bytes(input, 3, 4)
    let k4: Text = Text.from_bytes(input, 4, 5)
    let k5: Text = Text.from_bytes(input, 5, 6)
    let k6: Text = Text.from_bytes(input, 6, 7)
    let k7: Text = Text.from_bytes(input, 7, 8)
    counts.insert(k0, 10)
    counts.insert(k1, 1)
    counts.insert(k2, 30)
    counts.insert(k3, 40)
    counts.insert(k4, 50)
    counts.insert(k5, 60)
    counts.insert(k6, 70)
    counts.insert(k7, 80)
    counts.increment(k1, 4)
    counts.insert(k0, 11)
    var checksum: UInt64 = 0
    for entry in counts.entries():
        checksum = checksum * 131 + entry.value
    return checksum + counts.get(k6)
""".strip()


OVERFLOW_SOURCE = """
fn main(input: BytesView) -> UInt64:
    let counts: Map[Text, UInt64] = Map.new()
    let key: Text = Text.from_bytes(input, 0, 1)
    counts.insert(key, 18446744073709551615)
    counts.increment(key, 1)
    return 0
""".strip()

UINT64_ARITHMETIC_CASES = (
    ("add", "return 40 + 2", "OK result=42", None),
    ("sub", "return 42 - 2", "OK result=40", None),
    ("mult", "return 6 * 7", "OK result=42", None),
    (
        "augmented",
        "var value: UInt64 = 40\n    value += 2\n    return value",
        "OK result=42",
        None,
    ),
    (
        "augmented_mult",
        "var value: UInt64 = 6\n    value *= 7\n    return value",
        "OK result=42",
        None,
    ),
    ("add_overflow", "return 18446744073709551615 + 1", None, b"MerloOverflow:UInt64Add"),
    ("sub_overflow", "return 0 - 1", None, b"MerloOverflow:UInt64Sub"),
    ("mult_overflow", "return 18446744073709551615 * 2", None, b"MerloOverflow:UInt64Mult"),
    (
        "augmented_overflow",
        "var value: UInt64 = 18446744073709551615\n    value += 1\n    return value",
        None,
        b"MerloOverflow:UInt64Add",
    ),
    (
        "augmented_mult_overflow",
        "var value: UInt64 = 18446744073709551615\n    value *= 2\n    return value",
        None,
        b"MerloOverflow:UInt64Mult",
    ),
)


def _layers(source: str = MAP_SOURCE):
    hir = compile_structured_hir(source, path="native-map.mlo")
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    return hir, representation, mir, optimize_general_mir(mir)


def _metrics(stdout: str) -> dict[str, int]:
    line = next(item for item in stdout.splitlines() if item.startswith("MERLO_METRICS "))
    return {name: int(value) for name, value in (field.split("=", 1) for field in line.split()[1:])}


def test_map_has_distinct_hir_rir_mir_and_drop_identities() -> None:
    hir, representation, mir, optimized = _layers()

    hir_map_nodes = [
        node
        for function in hir.functions
        for node in function.walk()
        if node.kind == "MapOperation"
    ]
    hir_map_calls = {
        node.attribute_map["map_operation"]
        for node in hir_map_nodes
    }
    assert hir_map_calls == {"new", "increment", "get", "insert", "entries"}
    assert len({node.id for node in hir_map_nodes}) == len(hir_map_nodes)
    assert all(node.attribute_map["map_specialization"] == "Map[Text,UInt64]" for node in hir_map_nodes)
    assert all(
        node.attribute_map["map_operation"] == "new"
        or node.children[0].type_name == "Map[Text,UInt64]"
        for node in hir_map_nodes
    )

    descriptor = representation.descriptor("Map[Text,UInt64]")
    assert isinstance(descriptor, MapDesc)
    assert (
        descriptor.kind,
        descriptor.size,
        descriptor.alignment,
        descriptor.copy_class,
        descriptor.move_class,
        descriptor.drop_class,
    ) == (
        "map",
        40,
        8,
        "forbidden",
        "bitwise_then_invalidate",
        "map_owned_keys_then_buffers",
    )
    assert (descriptor.key_type, descriptor.value_type) == ("Text", "UInt64")
    assert descriptor.indirect_dependencies == ("Text", "UInt64")
    drop_plan = next(item for item in representation.drop_plans if item.type_name == descriptor.name)
    assert drop_plan.action == "map_owned_keys_then_buffers"
    assert [(child.field_name, child.type_name, child.action) for child in drop_plan.children] == [
        ("key", "Text", "owner_free")
    ]

    rir_map_ops = [
        operation
        for function in representation.functions
        for operation in function.walk()
        if operation.op.startswith("map_")
    ]
    assert {operation.op for operation in rir_map_ops} == {
        "map_new",
        "map_increment",
        "map_get",
        "map_insert",
        "map_entries",
    }
    assert len({operation.id for operation in rir_map_ops}) == len(rir_map_ops)
    assert {operation.id for operation in rir_map_ops}.isdisjoint(
        node.id for node in hir_map_nodes
    )
    assert all(
        operation.attribute_map["map_operation"] == operation.op.removeprefix("map_")
        for operation in rir_map_ops
    )

    instructions = [
        instruction
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
    ]
    mir_map_ops = [instruction for instruction in instructions if instruction.op.startswith("map_")]
    assert {instruction.op for instruction in mir_map_ops} == {
        "map_new",
        "map_increment",
        "map_get",
        "map_insert",
        "map_entries",
    }
    assert len({instruction.id for instruction in mir_map_ops}) == len(mir_map_ops)
    assert all(
        instruction.attribute_map["map_operation"] == instruction.op.removeprefix("map_")
        for instruction in mir_map_ops
    )
    assert all(
        len(instruction.operands)
        == {
            "map_new": 0,
            "map_increment": 3,
            "map_get": 2,
            "map_insert": 3,
            "map_entries": 1,
        }[instruction.op]
        for instruction in mir_map_ops
    )
    assert {
        "borrow_key",
        "checked_growth",
        "checked_uint64_add",
        "copy_key_if_vacant",
    } <= {instruction.op for instruction in instructions}
    assert any(
        instruction.op == "drop_value" and instruction.type_name == "Map[Text,UInt64]"
        for instruction in instructions
    )
    optimized_ops = {
        instruction.op
        for function in optimized.functions
        for block in function.blocks
        for instruction in block.instructions
    }
    assert {instruction.op for instruction in mir_map_ops} <= optimized_ops


def test_native_map_collisions_growth_updates_iteration_and_borrows(tmp_path: Path) -> None:
    compiler = shutil.which("clang") or shutil.which("cc")
    if compiler is None:
        pytest.skip("a C11 compiler is required")
    hir, representation, _mir, optimized = _layers()
    generated = emit_general_c(hir, representation, optimized)
    lowered = generated.source.lower()
    assert generated.domain_opaque_calls == ()
    assert "fnv" in lowered
    assert "linear" in lowered
    assert all(word not in lowered for word in ("ndjson", "csv", "grep"))
    assert "UINT64_C(14695981039346656037)" in generated.source
    assert "UINT64_C(1099511628211)" in generated.source
    assert "hash & (map->capacity - 1)" in generated.source
    assert "required > map->capacity - map->capacity / 4" in generated.source
    assert "MapMutationDuringView" in generated.source
    assert "MapGrowthDuringView" in generated.source
    assert "const MerloText *key" in generated.source
    regenerated = emit_general_c(hir, representation, optimized)
    assert (generated.source_sha256, generated.source) == (
        regenerated.source_sha256,
        regenerated.source,
    )

    c_path = tmp_path / "native_map.c"
    binary = tmp_path / "native_map"
    c_path.write_text(generated.source, encoding="utf-8")
    built = subprocess.run(
        [compiler, "-std=c11", "-O2", str(c_path), "-o", str(binary)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr

    completed = subprocess.run(
        [str(binary)],
        input=b"!)19AIQY",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    stdout = completed.stdout.decode()
    assert "OK result=7309127684760226" in stdout
    metrics = _metrics(stdout)
    assert metrics["map_collisions"] > 0
    assert metrics["map_growths"] == 2
    assert metrics["map_updates"] == 2
    assert metrics["map_owned_keys_allocated"] == 8
    assert metrics["map_owned_keys_dropped"] == 8
    assert metrics["map_lookup_key_copies"] == 0
    assert metrics["map_allocations"] == metrics["map_frees"]
    assert metrics["allocations"] == metrics["frees"]
    assert metrics["text_allocations"] == metrics["text_frees"]


def test_native_map_uint64_increment_is_checked(tmp_path: Path) -> None:
    compiler = shutil.which("clang") or shutil.which("cc")
    if compiler is None:
        pytest.skip("a C11 compiler is required")
    hir, representation, _mir, optimized = _layers(OVERFLOW_SOURCE)
    generated = emit_general_c(hir, representation, optimized)
    c_path = tmp_path / "native_map_overflow.c"
    binary = tmp_path / "native_map_overflow"
    c_path.write_text(generated.source, encoding="utf-8")
    built = subprocess.run(
        [compiler, "-std=c11", "-O2", str(c_path), "-o", str(binary)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr

    completed = subprocess.run(
        [str(binary)],
        input=b"x",
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert b"MerloOverflow:MapUInt64" in completed.stderr


@pytest.mark.parametrize(
    ("case_name", "body", "expected_stdout", "expected_stderr"),
    UINT64_ARITHMETIC_CASES,
)
def test_native_uint64_arithmetic_is_checked(
    tmp_path: Path,
    case_name: str,
    body: str,
    expected_stdout: str | None,
    expected_stderr: bytes | None,
) -> None:
    compiler = shutil.which("clang") or shutil.which("cc")
    if compiler is None:
        pytest.skip("a C11 compiler is required")
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let guard: Text = Text.from_bytes(input, 0, 0)\n"
        f"    {body}\n"
    )
    hir, representation, _mir, optimized = _layers(source)
    generated = emit_general_c(hir, representation, optimized)
    c_path = tmp_path / f"native_uint64_{case_name}.c"
    binary = tmp_path / f"native_uint64_{case_name}"
    c_path.write_text(generated.source, encoding="utf-8")
    built = subprocess.run(
        [compiler, "-std=c11", "-O2", str(c_path), "-o", str(binary)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    completed = subprocess.run(
        [str(binary)],
        input=b"",
        capture_output=True,
        check=False,
    )
    if expected_stdout is not None:
        assert completed.returncode == 0, completed.stderr.decode()
        assert expected_stdout in completed.stdout.decode()
    else:
        assert completed.returncode != 0
        assert expected_stderr in completed.stderr


@pytest.mark.parametrize(
    "specialization",
    [
        "Map[Text,Text]",
        "Map[UInt64,UInt64]",
        "Map[Text,Map[Text,UInt64]]",
        "Map[Any,UInt64]",
    ],
)
def test_unsupported_map_specializations_are_rejected(specialization: str) -> None:
    source = f"""
fn main() -> UInt64:
    let values: {specialization} = Map.new()
    return 0
"""
    with pytest.raises(
        (StructuredHIRCompileError, RepresentationCompileError),
        match="Map.*Text.*scalar",
    ):
        lower_structured_hir_to_rir(compile_structured_hir(source, path="bad-map.mlo"))


def test_dynamic_any_is_rejected_before_representation_lowering() -> None:
    source = """
fn main(value: Any) -> UInt64:
    return 0
"""
    with pytest.raises(StructuredHIRCompileError, match="DynamicAny"):
        compile_structured_hir(source, path="dynamic-any.mlo")


def test_map_entry_field_owners_are_copied_from_borrowed_entries() -> None:
    source = """
fn main(input: BytesView) -> UInt64:
    let counts: Map[Text, UInt64] = Map.new()
    for entry in counts.entries():
        let copied: Text = entry.key
        return copied.len()
    return 0
""".strip()
    compile_structured_hir(source, path="map-entry-owner.mlo")


def test_map_entry_mutation_is_rejected_while_entries_are_borrowed() -> None:
    source = """
fn main(input: BytesView) -> UInt64:
    let counts: Map[Text, UInt64] = Map.new()
    for entry in counts.entries():
        counts.increment(entry.key)
    return 0
""".strip()
    with pytest.raises(StructuredHIRCompileError, match="MutationDuringBorrow"):
        compile_structured_hir(source, path="map-entry-mutation.mlo")
