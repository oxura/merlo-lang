from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from merlo.native_c_backend import compile_c_source
from merlo import typed_codegen
from merlo.intrinsics import CONTRACT_GRAPH
from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import (
    lower_rir_to_performance_mir,
    optimize_general_mir,
)
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.surface_elaborator import (
    SurfaceElaborationError,
    elaborate_surface,
)
from merlo.surface_parser import parse_surface


def compile_native(source: str, tmp_path: Path, stem: str) -> Path:
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
    return Path(build.binary_path)


def run_native(source: str, tmp_path: Path, stem: str) -> subprocess.CompletedProcess[bytes]:
    binary = compile_native(source, tmp_path, stem)
    return subprocess.run(
        [binary],
        input=b"",
        capture_output=True,
        check=False,
    )


def test_integer_division_remainder_shift_and_invert_are_deterministic(
    tmp_path: Path,
) -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let truncated: Int64 = -7 / 3\n"
        "    let floored: Int64 = -7 // 3\n"
        "    let remainder: Int64 = -7 % 3\n"
        "    let shifted: Int64 = -3 >> 1\n"
        "    let left: UInt64 = 3 << 2\n"
        "    UInt64(truncated + 3) + UInt64(floored + 3) + "
        "UInt64(remainder) + UInt64(shifted + 2) + left\n"
    )

    completed = run_native(source, tmp_path, "numeric-defined")
    assert completed.returncode == 0
    assert b"OK result=15" in completed.stdout

    inverted = run_native(
        "fn main(input: BytesView) -> UInt64:\n"
        "    ~0\n",
        tmp_path,
        "numeric-invert",
    )
    assert inverted.returncode == 0
    assert b"OK result=18446744073709551615" in inverted.stdout


def test_signed_floor_division_and_modulo_follow_divisor_sign(
    tmp_path: Path,
) -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let quotient: Int64 = 7 // -3\n"
        "    let remainder: Int64 = 7 % -3\n"
        "    UInt64(quotient + 3) + UInt64(remainder + 2)\n"
    )

    completed = run_native(source, tmp_path, "numeric-negative-divisor")
    assert completed.returncode == 0
    assert b"OK result=0" in completed.stdout


def test_numeric_casts_are_available_through_the_surface_pipeline(
    tmp_path: Path,
) -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let signed_value: Int64 = Int64(input.len())\n"
        "    let ratio: Float64 = Float64(signed_value) / 2.0\n"
        "    UInt64(ratio)\n"
    )

    binary = compile_native(source, tmp_path, "numeric-casts")
    completed = subprocess.run(
        [binary],
        input=b"abcdef",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert b"OK result=3" in completed.stdout


def test_byte_arithmetic_uses_byte_width_checks(
    tmp_path: Path,
) -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let value: Byte = 20\n"
        "    let divisor: Byte = 3\n"
        "    let quotient: Byte = value / divisor\n"
        "    let shifted: Byte = quotient << 2\n"
        "    UInt64(shifted)\n"
    )

    completed = run_native(source, tmp_path, "numeric-byte")
    assert completed.returncode == 0
    assert b"OK result=24" in completed.stdout


def test_numeric_hir_records_runtime_semantics() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let quotient: Int64 = -7 // 3\n"
        "    let shifted: Int64 = quotient << 1\n"
        "    UInt64(shifted + 6)\n"
    )
    canonical = elaborate_surface(parse_surface(source)).canonical
    hir = compile_canonical_hir(canonical)
    binary = [
        node
        for node in hir.function("main").walk()
        if node.kind == "Binary"
    ]

    assert binary[0].type_name == "Int64"
    assert binary[0].attribute_map == {
        "division_by_zero": "trap",
        "operator": "FloorDiv",
        "rounding": "toward_negative_infinity",
        "signed_overflow": "checked",
    }
    assert binary[1].attribute_map == {
        "operator": "LShift",
        "overflow": "checked",
        "shift_range": "checked",
    }

    float_source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let ratio: Float64 = 7.5 / 2.5\n"
        "    UInt64(ratio)\n"
    )
    float_hir = compile_canonical_hir(
        elaborate_surface(parse_surface(float_source)).canonical
    )
    float_divide = next(
        node
        for node in float_hir.function("main").walk()
        if node.kind == "Binary"
    )
    assert float_divide.attribute_map == {
        "division_by_zero": "ieee754",
        "operator": "Div",
        "rounding": "ieee754",
    }


@pytest.mark.parametrize(
    ("stem", "expression", "diagnostic"),
    [
        ("uint-zero", "1 / 0", b"MerloDivisionByZero:UInt64"),
        ("uint-mod-zero", "1 % 0", b"MerloDivisionByZero:UInt64"),
        ("shift-count", "1 << 64", b"MerloInvalidShift:UInt64"),
        (
            "shift-overflow",
            "18446744073709551615 << 1",
            b"MerloOverflow:UInt64Shift",
        ),
    ],
)
def test_invalid_unsigned_operations_trap_without_c_undefined_behavior(
    tmp_path: Path,
    stem: str,
    expression: str,
    diagnostic: bytes,
) -> None:
    completed = run_native(
        "fn main(input: BytesView) -> UInt64:\n"
        f"    {expression}\n",
        tmp_path,
        stem,
    )
    assert completed.returncode != 0
    assert diagnostic in completed.stderr


@pytest.mark.parametrize(
    ("stem", "operation", "diagnostic"),
    [
        ("int-div-overflow", "minimum / -1", b"MerloOverflow:Int64Div"),
        ("int-neg-overflow", "-minimum", b"MerloOverflow:Int64Neg"),
    ],
)
def test_signed_overflow_edges_trap(
    tmp_path: Path,
    stem: str,
    operation: str,
    diagnostic: bytes,
) -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let minimum: Int64 = -9223372036854775807 - 1\n"
        f"    let result: Int64 = {operation}\n"
        "    UInt64(result)\n"
    )

    completed = run_native(source, tmp_path, stem)
    assert completed.returncode != 0
    assert diagnostic in completed.stderr


def test_ordinary_int64_multiplication_overflow_remains_checked(
    tmp_path: Path,
) -> None:
    completed = run_native(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let product: Int64 = 3037000500 * 3037000500\n"
        "    UInt64(product)\n",
        tmp_path,
        "int64-multiply-overflow",
    )

    assert completed.returncode != 0
    assert b"MerloOverflow:Int64Mult" in completed.stderr


def test_wrapping_mul_uses_contract_hir_rir_mir_and_native_lowering(
    tmp_path: Path,
) -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    wrapping_mul(18446744073709551615, 2)\n"
    )
    contract = CONTRACT_GRAPH.functions["wrapping_mul"]
    canonical = elaborate_surface(parse_surface(source)).canonical
    hir = compile_canonical_hir(canonical)
    representation = lower_structured_hir_to_rir(hir)
    mir = optimize_general_mir(
        lower_rir_to_performance_mir(hir, representation)
    )
    numeric_hir = next(
        node
        for node in hir.function("main").walk()
        if node.kind == "NumericIntrinsic"
    )
    numeric_rir = next(
        operation
        for function in representation.functions
        for operation in function.walk()
        if operation.op == "numeric_intrinsic"
    )
    numeric_mir = next(
        instruction
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
        if instruction.op == "numeric_intrinsic"
    )

    assert contract.parameters == ("Integer", "Integer")
    assert contract.result_type == "Integer"
    assert numeric_hir.attribute_map["callee"] == "wrapping_mul"
    assert numeric_hir.attribute_map["overflow"] == "wrapping"
    assert numeric_rir.attribute_map["callee"] == "wrapping_mul"
    assert numeric_rir.attribute_map["overflow"] == "wrapping"
    assert numeric_mir.attribute_map["callee"] == "wrapping_mul"
    assert numeric_mir.attribute_map["overflow"] == "wrapping"

    generated = emit_general_c(hir, representation, mir)
    build = compile_c_source(
        generated.source,
        output_dir=tmp_path,
        stem="wrapping-multiply",
    )
    assert build.status == "MEASURED", build.stderr
    assert build.binary_path is not None
    completed = subprocess.run(
        [build.binary_path],
        input=b"",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert b"OK result=18446744073709551614" in completed.stdout


def test_c_backend_contains_no_source_or_benchmark_specific_dispatch() -> None:
    backend = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "merlo"
        / "representation_c_backend.py"
    ).read_text(encoding="utf-8")

    assert "_FROZEN_GENERAL_JSON_SHA256" not in backend
    assert "frozen_general_json" not in backend
    assert "checksum_byte" not in backend
    assert "general_json" not in backend
    assert "sha256(hir.source" not in backend
    assert "current_function.name ==" not in backend


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    [
        (
            "fn bad() -> Float64:\n    4.0 // 2.0\n",
            "IntegerOperatorRequired",
        ),
        (
            "fn bad() -> Float64:\n    4.0 << 2.0\n",
            "IntegerOperatorRequired",
        ),
        (
            "fn bad() -> UInt64:\n    -1\n",
            "UnsignedNegationForbidden",
        ),
        (
            "fn bad() -> Byte:\n    256\n",
            "NumericLiteralOutOfRange",
        ),
        (
            "fn bad() -> UInt64:\n    18446744073709551616\n",
            "NumericLiteralOutOfRange",
        ),
    ],
)
def test_invalid_numeric_programs_fail_before_hir(
    source: str,
    diagnostic: str,
) -> None:
    with pytest.raises(SurfaceElaborationError, match=diagnostic):
        elaborate_surface(parse_surface(source))

def test_scalar_codegen_uses_mir_without_typed_hir_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let value: UInt64 = 2\n"
        "    return value + 3\n"
    )
    canonical = elaborate_surface(parse_surface(source, path="mir-scalar.mlo")).canonical
    hir = compile_canonical_hir(canonical)
    representation = lower_structured_hir_to_rir(hir)
    mir = optimize_general_mir(lower_rir_to_performance_mir(hir, representation))

    def forbidden(_hir: object) -> object:
        raise AssertionError("scalar MIR path used typed HIR fallback")

    monkeypatch.setattr(typed_codegen, "lower_hir_program", forbidden)
    generated = emit_general_c(hir, representation, mir)
    build = compile_c_source(generated.source, output_dir=tmp_path, stem="mir-scalar")
    assert build.status == "MEASURED", build.stderr
    assert build.binary_path is not None
    completed = subprocess.run(
        [build.binary_path],
        input=b"",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert b"OK result=5" in completed.stdout

def test_cfg_codegen_uses_mir_without_typed_hir_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    var count: UInt64 = 0\n"
        "    var total: UInt64 = 0\n"
        "    while count < 3:\n"
        "        total = total + count\n"
        "        count = count + 1\n"
        "    return total\n"
    )
    canonical = elaborate_surface(parse_surface(source, path="mir-cfg.mlo")).canonical
    hir = compile_canonical_hir(canonical)
    representation = lower_structured_hir_to_rir(hir)
    mir = optimize_general_mir(lower_rir_to_performance_mir(hir, representation))

    def forbidden(_hir: object) -> object:
        raise AssertionError("CFG MIR path used typed HIR fallback")

    monkeypatch.setattr(typed_codegen, "lower_hir_program", forbidden)
    generated = emit_general_c(hir, representation, mir)
    build = compile_c_source(generated.source, output_dir=tmp_path, stem="mir-cfg")
    assert build.status == "MEASURED", build.stderr
    assert build.binary_path is not None
    completed = subprocess.run(
        [build.binary_path],
        input=b"",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert b"OK result=3" in completed.stdout

def test_calls_and_collections_codegen_use_mir_without_typed_hir_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = (
        "record Item:\n"
        "    active: Bool\n"
        "fn add(left: UInt64, right: UInt64) -> UInt64:\n"
        "    return left + right\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let items: Array[Item,2] = [Item(true), Item(false)]\n"
        "    let selected: UInt64 = items.count(.active)\n"
        "    return add(selected, 1)\n"
    )
    canonical = elaborate_surface(parse_surface(source, path="mir-calls.mlo")).canonical
    hir = compile_canonical_hir(canonical)
    representation = lower_structured_hir_to_rir(hir)
    mir = optimize_general_mir(lower_rir_to_performance_mir(hir, representation))

    def forbidden(_hir: object) -> object:
        raise AssertionError("calls/collections MIR path used typed HIR fallback")

    monkeypatch.setattr(typed_codegen, "lower_hir_program", forbidden)
    generated = emit_general_c(hir, representation, mir)
    build = compile_c_source(generated.source, output_dir=tmp_path, stem="mir-calls")
    assert build.status == "MEASURED", build.stderr
    assert build.binary_path is not None
    completed = subprocess.run(
        [build.binary_path],
        input=b"",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert b"OK result=2" in completed.stdout
