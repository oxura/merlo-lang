from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from merlo.concise_application import ConciseApplicationError, elaborate_concise_core
from merlo.native_c_backend import compile_c_source
from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import (
    ArrayDesc,
    CallbackDesc,
    SliceDesc,
    lower_structured_hir_to_rir,
)
from merlo.representation_mir import (
    lower_rir_to_performance_mir,
    optimize_general_mir,
)
from merlo.structured_hir_v2 import (
    StructuredHIRCompileError,
    compile_structured_hir,
)


def _layers(source: str):
    hir = compile_structured_hir(source, path="alpha-kernel.mlo")
    rir = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, rir)
    return hir, rir, mir, optimize_general_mir(mir)


def _native(source: str, tmp_path: Path, stem: str) -> Path:
    hir, rir, _mir, optimized = _layers(source)
    generated = emit_general_c(hir, rir, optimized)
    build = compile_c_source(generated.source, output_dir=tmp_path / stem, stem=stem)
    assert build.status == "MEASURED", build.stderr
    assert build.binary_path is not None
    return Path(build.binary_path)


def test_all_alpha_scalar_descriptors_have_fixed_layouts() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let narrow: Byte = Byte(input.len())\n"
        "    let signed_value: Int64 = Int64(input.len())\n"
        "    let single_value: Float32 = Float32(input.len())\n"
        "    let double_value: Float64 = Float64(input.len())\n"
        "    return UInt64(narrow)\n"
    )
    hir, rir, mir, optimized = _layers(source)
    descriptors = {item.name: item for item in rir.descriptors}

    assert {"Unit", "Bool", "Byte", "Int64", "UInt64", "Float32", "Float64"} <= set(descriptors)
    assert descriptors["Byte"].size == 1
    assert descriptors["Float32"].size == 4
    assert descriptors["Float64"].size == 8
    assert any(node.kind == "ScalarCast" for function in hir.functions for node in function.walk())
    assert any(operation.op == "scalar_cast" for function in rir.functions for operation in function.walk())
    assert any(
        instruction.op == "scalar_cast"
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
    )
    assert optimized.descriptors_digest == mir.descriptors_digest


def test_checked_and_wrapping_uint64_are_distinct_native_operations(tmp_path: Path) -> None:
    wrapping = _native(
        "fn main(input: BytesView) -> UInt64:\n"
        "    return wrapping_add(18446744073709551615, 1)\n",
        tmp_path,
        "wrapping",
    )
    checked = _native(
        "fn main(input: BytesView) -> UInt64:\n"
        "    return checked_add(18446744073709551615, 1)\n",
        tmp_path,
        "checked",
    )

    wrapped = subprocess.run([str(wrapping)], input=b"", capture_output=True, check=False)
    overflow = subprocess.run([str(checked)], input=b"", capture_output=True, check=False)

    assert wrapped.returncode == 0
    assert b"OK result=0" in wrapped.stdout
    assert overflow.returncode != 0
    assert b"MerloOverflow:UInt64Add" in overflow.stderr


def test_explicit_narrowing_cast_is_checked_natively(tmp_path: Path) -> None:
    binary = _native(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let value: Byte = Byte(input.len())\n"
        "    return UInt64(value)\n",
        tmp_path,
        "byte-cast",
    )

    accepted = subprocess.run([str(binary)], input=b"x" * 255, capture_output=True, check=False)
    rejected = subprocess.run([str(binary)], input=b"x" * 256, capture_output=True, check=False)

    assert accepted.returncode == 0
    assert b"OK result=255" in accepted.stdout
    assert rejected.returncode != 0
    assert b"MerloOverflow:ByteCast" in rejected.stderr


def test_ieee_float_comparison_handles_nan_and_signed_zero(tmp_path: Path) -> None:
    binary = _native(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let zero: Float64 = Float64(0)\n"
        "    let negative_zero: Float64 = -0.0\n"
        "    let nan: Float64 = zero / zero\n"
        "    if zero == negative_zero and nan != nan and not nan < Float64(1):\n"
        "        return 1\n"
        "    return 0\n",
        tmp_path,
        "float-semantics",
    )

    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=1" in completed.stdout


def test_value_families_have_monomorphized_descriptors() -> None:
    source = (
        "record Pair:\n    left: UInt64\n    right: Text\n"
        "enum Choice:\n    Empty\n    Value: Text\n"
        "fn main(input: BytesView, text: Text, view: TextView, path: Path, "
        "option: Option[Text], result: Result[UInt64,Choice], "
        "array: Array[UInt64,4], slice: Slice[UInt64], "
        "values: Vec[Text], counts: Map[Text,UInt64], boxed: Box[Pair]) -> UInt64:\n"
        "    return array[0]\n"
    )
    _hir, rir, _mir, _optimized = _layers(source)
    descriptors = {item.name: item for item in rir.descriptors}

    assert isinstance(descriptors["Array[UInt64,4]"], ArrayDesc)
    assert descriptors["Array[UInt64,4]"].length == 4
    assert isinstance(descriptors["Slice[UInt64]"], SliceDesc)
    assert descriptors["Option[Text]"].kind == "enum"
    assert descriptors["Result[UInt64,Choice]"].kind == "enum"
    assert descriptors["Vec[Text]"].kind == "vec"
    assert descriptors["Map[Text,UInt64]"].kind == "map"
    assert descriptors["Box[Pair]"].kind == "box"
    assert descriptors["Text"].kind == "text"
    assert descriptors["TextView"].kind == "borrow"
    assert descriptors["Bytes"].kind == "bytes"
    assert descriptors["BytesView"].kind == "borrow"
    assert descriptors["Path"].kind == "text"


def test_fixed_array_executes_with_bounds_checked_index(tmp_path: Path) -> None:
    binary = _native(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let values: Array[UInt64,3] = [10, 20, 30]\n"
        "    return values[1]\n",
        tmp_path,
        "array",
    )

    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=20" in completed.stdout


def test_control_flow_and_typed_propagation_reach_every_ir_layer() -> None:
    source = (
        "enum AppError:\n    Failed\n"
        "fn parse(value: UInt64) -> Result[UInt64,AppError]:\n"
        "    return Ok(value)\n"
        "fn main(input: BytesView) -> Result[UInt64,AppError]:\n"
        "    var index: UInt64 = 0\n"
        "    while index < input.len():\n"
        "        index += 1\n"
        "        if index == 2:\n            continue\n"
        "        if index == 3:\n            break\n"
        "    let value: UInt64 = parse(index)?\n"
        "    match AppError.Failed:\n"
        "        case AppError.Failed:\n            return Ok(value)\n"
    )
    hir, rir, mir, _optimized = _layers(source)
    kinds = {node.kind for function in hir.functions for node in function.walk()}
    operations = {item.op for function in rir.functions for item in function.walk()}
    instructions = {
        item.op
        for function in mir.functions
        for block in function.blocks
        for item in block.instructions
    }

    assert {"If", "While", "Break", "Continue", "Match", "ResultPropagation", "Return"} <= kinds
    assert {"if", "while", "break", "continue", "match_enum", "try_result", "return"} <= operations
    assert {"break", "continue", "result_branch"} <= instructions


def test_nonexhaustive_enum_match_is_rejected() -> None:
    source = (
        "enum Choice:\n    First\n    Second\n"
        "fn main(value: Choice) -> UInt64:\n"
        "    match value:\n"
        "        case Choice.First:\n            return 1\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="NonExhaustiveMatch.*Second"):
        compile_structured_hir(source)


def test_pure_functions_reject_effects_and_task_effects_are_explicit() -> None:
    with pytest.raises(ConciseApplicationError, match="EffectInPureFunction"):
        elaborate_concise_core(
            "fn bad(path: Path) -> Result[Bytes,AppError]:\n    fs.read(path)\n"
        )


def test_named_non_capturing_callback_compiles_and_lambda_is_rejected(tmp_path: Path) -> None:
    source = (
        "fn increment(value: UInt64) -> UInt64:\n    return value + 1\n"
        "fn apply(callback: Fn[UInt64,UInt64], value: UInt64) -> UInt64:\n"
        "    return callback(value)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return apply(increment, input.len())\n"
    )
    hir, rir, _mir, _optimized = _layers(source)
    assert isinstance(rir.descriptor("Fn[UInt64,UInt64]"), CallbackDesc)
    assert any(node.kind == "CallbackCall" for function in hir.functions for node in function.walk())
    binary = _native(source, tmp_path, "callback")
    completed = subprocess.run([str(binary)], input=b"abc", capture_output=True, check=False)
    assert completed.returncode == 0
    assert b"OK result=4" in completed.stdout

    with pytest.raises(StructuredHIRCompileError, match="CapturingClosureUnsupported"):
        compile_structured_hir(
            "fn main(input: BytesView) -> UInt64:\n"
            "    let callback = lambda value: value + input.len()\n"
            "    return callback(1)\n"
        )


def test_owned_and_borrowed_parameters_are_explicit() -> None:
    source = (
        "fn inspect(text: Text, view: TextView, values: Vec[UInt64]) -> UInt64:\n"
        "    values.push(text.len())\n"
        "    return view.len() + values.len()\n"
        "fn main(input: BytesView) -> UInt64:\n    return input.len()\n"
    )
    hir = compile_structured_hir(source)
    inspect = next(item for item in hir.functions if item.name == "inspect")
    ownership = {item.name: item.ownership for item in inspect.parameters}

    assert ownership == {"text": "borrow", "view": "borrow", "values": "borrow_mut"}


@pytest.mark.parametrize(
    ("scalar", "maximum", "wrapped_condition"),
    [
        ("Byte", "255", "value == Byte(0)"),
        ("UInt64", "18446744073709551615", "value == UInt64(0)"),
        ("Int64", "9223372036854775807", "value < Int64(0)"),
    ],
)
def test_wrapping_add_preserves_integer_width(
    scalar: str,
    maximum: str,
    wrapped_condition: str,
    tmp_path: Path,
) -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        f"    let value: {scalar} = wrapping_add({scalar}({maximum}), {scalar}(1))\n"
        f"    if {wrapped_condition}:\n"
        "        return 1\n"
        "    return 0\n"
    )
    binary = _native(source, tmp_path, f"wrap-{scalar.lower()}")

    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=1" in completed.stdout


@pytest.mark.parametrize(
    ("scalar", "maximum"),
    [
        ("Byte", "255"),
        ("UInt64", "18446744073709551615"),
        ("Int64", "9223372036854775807"),
    ],
)
def test_checked_add_traps_at_each_integer_width(
    scalar: str,
    maximum: str,
    tmp_path: Path,
) -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        f"    let value: {scalar} = checked_add({scalar}({maximum}), {scalar}(1))\n"
        "    return UInt64(value)\n"
    )
    binary = _native(source, tmp_path, f"checked-{scalar.lower()}")

    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)

    assert completed.returncode != 0
    assert f"MerloOverflow:{scalar}Add".encode() in completed.stderr


def test_ordinary_byte_arithmetic_is_checked(tmp_path: Path) -> None:
    binary = _native(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let value: Byte = Byte(255) + Byte(1)\n"
        "    return UInt64(value)\n",
        tmp_path,
        "checked-byte-expression",
    )

    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)

    assert completed.returncode != 0
    assert b"MerloOverflow:ByteAdd" in completed.stderr


def test_nan_to_integer_cast_traps_before_c_conversion(tmp_path: Path) -> None:
    binary = _native(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let zero: Float64 = Float64(0)\n"
        "    let nan: Float64 = zero / zero\n"
        "    return UInt64(Byte(nan))\n",
        tmp_path,
        "nan-byte-cast",
    )

    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)

    assert completed.returncode != 0
    assert b"MerloOverflow:ByteCast" in completed.stderr


def test_structured_hir_rejects_bool_numeric_comparison() -> None:
    with pytest.raises(StructuredHIRCompileError, match="ComparableOperandsRequired"):
        compile_structured_hir(
            "fn main(input: BytesView) -> UInt64:\n"
            "    if true == 1:\n"
            "        return 1\n"
            "    return 0\n"
        )


def test_generic_vec_map_and_box_execute_one_monomorphized_program(
    tmp_path: Path,
) -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let values: Vec[Byte] = Vec.new()\n"
        "    values.push(Byte(7))\n"
        "    let boxed: Box[Byte] = Box.new(values.get(0))\n"
        "    let counts: Map[Text,Byte] = Map.new()\n"
        '    counts.insert("key", boxed.get())\n'
        '    return UInt64(counts.get("key"))\n'
    )
    binary = _native(source, tmp_path, "generic-collections")

    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=7" in completed.stdout


@pytest.mark.parametrize(
    ("subject_type", "case", "missing"),
    [
        ("Option[UInt64]", "Some(item)", "NoneValue"),
        ("Result[UInt64,Text]", "Ok(item)", "Err"),
    ],
)
def test_generic_sum_matches_must_be_exhaustive(
    subject_type: str,
    case: str,
    missing: str,
) -> None:
    with pytest.raises(
        StructuredHIRCompileError,
        match=rf"NonExhaustiveMatch.*{missing}",
    ):
        compile_structured_hir(
            f"fn main(value: {subject_type}) -> UInt64:\n"
            "    match value:\n"
            f"        case {case}:\n"
            "            return item\n"
        )


def test_owned_returns_and_view_reborrows_are_explicit() -> None:
    source = (
        "fn pass_view(data: BytesView) -> BytesView:\n"
        "    return data\n"
        "fn own_text(data: BytesView) -> Text:\n"
        "    return Text.from_bytes(data, 0, data.len())\n"
        "fn forward(text: Text) -> Text:\n"
        "    return text\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let view: BytesView = pass_view(input)\n"
        "    let text: Text = own_text(view)\n"
        "    let moved: Text = forward(text)\n"
        "    return moved.len()\n"
    )
    hir = compile_structured_hir(source)
    pass_view = hir.function("pass_view")
    forward = hir.function("forward")
    main = hir.function("main")

    assert pass_view.parameters[0].ownership == "borrow"
    assert forward.parameters[0].ownership == "owned"
    assert next(node for node in pass_view.walk() if node.kind == "Return").ownership == "borrow"
    assert next(node for node in forward.walk() if node.kind == "Return").ownership == "owned"
    assert next(
        node
        for node in main.walk()
        if node.kind == "DirectCall" and node.attribute_map["callee"] == "pass_view"
    ).ownership == "borrow"



def test_owned_text_reborrows_as_text_view_for_native_calls(tmp_path: Path) -> None:
    binary = _native(
        "fn consume(view: TextView) -> UInt64:\n"
        "    return view.len()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    return consume(text)\n",
        tmp_path,
        "text-reborrow",
    )
    completed = subprocess.run([str(binary)], input=b"hello", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=5" in completed.stdout