from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from merlo.frontend_model import ConciseApplicationError
from merlo.concise_services import elaborate_concise_core
from merlo.native_c_backend import compile_c_source, find_c_compiler
from merlo.representation_c_backend import (
    RepresentationCBackendError,
    emit_general_c,
)
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


def test_generated_c_uses_real_view_descriptors_under_strict_aliasing(
    tmp_path: Path,
) -> None:
    source = (
        "fn consume(view: TextView) -> UInt64:\n"
        "    return view.len() + view.byte(0)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    return consume(text)\n"
    )
    hir, rir, _mir, optimized = _layers(source)
    generated = emit_general_c(hir, rir, optimized)
    forbidden = (
        r"\(const MerloTextView \*\)\s*&?\(?[A-Za-z_]",
        r"\(const MerloBytesView \*\)\s*&?\(?[A-Za-z_]",
        r"\(MerloTextView \*\)",
        r"\(MerloBytesView \*\)",
    )
    assert all(re.search(pattern, generated.source) is None for pattern in forbidden)

    source_path = tmp_path / "strict-aliasing.c"
    source_path.write_text(generated.source, encoding="utf-8")
    compilers = tuple(
        path
        for name in ("clang", "gcc")
        if (path := shutil.which(name)) is not None
    )
    assert compilers
    for compiler in compilers:
        command = [
            compiler,
            "-std=c11",
            "-O3",
            "-fstrict-aliasing",
        ]
        if Path(compiler).name == "gcc":
            command.append("-Wstrict-aliasing=2")
        completed = subprocess.run(
            [*command, str(source_path), "-o", str(tmp_path / Path(compiler).name)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr


def test_nested_owned_record_call_temporary_has_one_drop(tmp_path: Path) -> None:
    source = (
        "record Change:\n"
        "    old_path: Text\n"
        "    new_path: Text\n"
        "fn rename(change: Change) -> UInt64:\n"
        "    return change.old_path.len() + change.new_path.len()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return rename(Change(\"draft.txt\", \"published.txt\"))\n"
    )
    hir, rir, _mir, optimized = _layers(source)
    generated = emit_general_c(hir, rir, optimized)
    assert "__merlo_owned_temp_1 = merlo_make_Change(" in generated.source
    assert generated.source.count(
        "merlo_drop_Change(&__merlo_owned_temp_1);"
    ) == 1

    binary = _native(source, tmp_path, "owned-record-call")
    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=22" in completed.stdout
    assert b"text_allocations=2 text_frees=2" in completed.stdout


def test_owned_record_return_moves_named_argument_once(tmp_path: Path) -> None:
    source = (
        "record Change:\n"
        "    old_path: Text\n"
        "    new_path: Text\n"
        "fn take(change: Change) -> Change:\n"
        "    return change\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let source: Change = Change(\"a\", \"bc\")\n"
        "    let result: Change = take(source)\n"
        "    return result.old_path.len() + result.new_path.len()\n"
    )
    hir, rir, _mir, optimized = _layers(source)
    generated = emit_general_c(hir, rir, optimized)
    assert "merlo_fn_take(merlo_move_Change(&source))" in generated.source

    binary = _native(source, tmp_path, "owned-record-return")
    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=3" in completed.stdout
    assert b"text_allocations=2 text_frees=2" in completed.stdout


def test_borrowed_text_literal_remains_one_stack_argument() -> None:
    source = (
        "fn inspect(text: Text) -> UInt64:\n"
        "    return text.len()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return inspect(\"x\")\n"
    )
    hir, rir, _mir, optimized = _layers(source)
    generated = emit_general_c(hir, rir, optimized)
    assert "__merlo_owned_temp_" not in generated.source
    assert generated.source.count("= merlo_fn_inspect(") == 1


@pytest.mark.parametrize("keyword", ("if", "while"))
def test_control_flow_rejects_owned_temporary_borrow_arguments(keyword: str) -> None:
    body = (
        f"    {keyword} consume(Text.from_bytes(input, 0, input.len())):\n"
        "        return 1\n"
        if keyword == "if"
        else
        "    while consume(Text.from_bytes(input, 0, input.len())):\n"
        "        break\n"
    )
    source = (
        "fn consume(value: Text) -> Bool:\n"
        "    return value.len() > 0\n"
        "fn main(input: BytesView) -> UInt64:\n"
        + body
        + "    return 0\n"
    )
    hir, rir, _mir, optimized = _layers(source)
    with pytest.raises(RepresentationCBackendError, match="control-flow expression"):
        emit_general_c(hir, rir, optimized)


def test_borrowed_view_cannot_escape_materialized_text_owner() -> None:
    source = (
        "fn borrow_view(value: Text) -> TextView:\n"
        "    return value.view()\n"
        "fn wrapper(input: BytesView) -> TextView:\n"
        "    return borrow_view(Text.from_bytes(input, 0, input.len()))\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return wrapper(input).len()\n"
    )
    hir, rir, _mir, optimized = _layers(source)
    with pytest.raises(RepresentationCBackendError, match="borrowed result escapes"):
        emit_general_c(hir, rir, optimized)

def test_borrowed_map_cannot_be_shallow_cloned_for_owned_call() -> None:
    source = (
        "fn take(value: Map[Text,UInt64]) -> Map[Text,UInt64]:\n"
        "    return value\n"
        "fn wrapper(value: Map[Text,UInt64]) -> Map[Text,UInt64]:\n"
        "    return take(value)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return 0\n"
    )
    hir, rir, _mir, optimized = _layers(source)
    with pytest.raises(RepresentationCBackendError, match="cannot clone borrowed owner"):
        emit_general_c(hir, rir, optimized)


def test_branch_move_keeps_zeroed_source_cleanup_on_false_path() -> None:
    source = (
        "record Change:\n"
        "    old_path: Text\n"
        "    new_path: Text\n"
        "fn take(value: Change) -> Change:\n"
        "    return value\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let source: Change = Change(\"a\", \"bc\")\n"
        "    if input.len() > 0:\n"
        "        let moved: Change = take(source)\n"
        "        return moved.old_path.len()\n"
        "    return source.old_path.len()\n"
    )
    hir, rir, _mir, optimized = _layers(source)
    generated = emit_general_c(hir, rir, optimized)
    function_body = generated.source.split("merlo_fn_main(", 1)[1]
    assignment = function_body.index("source = merlo_make_Change(")
    first_cleanup = function_body.index("merlo_drop_Change(&source);")
    assert assignment < first_cleanup
    assert function_body.count("merlo_drop_Change(&source);") == 2
    assert function_body.count("merlo_drop_Change(&moved);") == 2


def test_loop_body_borrow_temporary_is_dropped_at_each_iteration() -> None:
    source = (
        "fn consume(value: Text) -> Unit:\n"
        "    return\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    var remaining: UInt64 = input.len()\n"
        "    while remaining > 0:\n"
        "        consume(Text.from_bytes(input, 0, input.len()))\n"
        "        remaining = 0\n"
        "    return remaining\n"
    )
    hir, rir, _mir, optimized = _layers(source)
    generated = emit_general_c(hir, rir, optimized)
    assert generated.source.count("__merlo_owned_temp_1 = merlo_text_from") == 1
    assert generated.source.count("merlo_drop_Text(&__merlo_owned_temp_1);") == 1


@pytest.mark.parametrize("binding", ("return", "assign"))
def test_nested_borrowed_holder_rejects_owner_escape(binding: str) -> None:
    expression = "Holder(borrow_view(Text.from_bytes(input, 0, input.len())))"
    if binding == "return":
        body = f"    return {expression}\n"
        return_type = "Holder"
    else:
        body = f"    let holder: Holder = {expression}\n    return 0\n"
        return_type = "UInt64"
    source = (
        "record Holder:\n"
        "    view: TextView\n"
        "fn borrow_view(value: Text) -> TextView:\n"
        "    return value.view()\n"
        f"fn wrapper(input: BytesView) -> {return_type}:\n"
        + body
        + "fn main(input: BytesView) -> UInt64:\n"
        + (
            "    return wrapper(input).view.len()\n"
            if binding == "return"
            else "    return wrapper(input)\n"
        )
    )
    hir, rir, _mir, optimized = _layers(source)
    with pytest.raises(RepresentationCBackendError, match="borrowed result escapes"):
        emit_general_c(hir, rir, optimized)

def test_try_propagation_drops_borrow_temporary_on_error_and_success() -> None:
    source = (
        "enum AppError:\n"
        "    Failed\n"
        "fn validate(value: Text) -> Result[UInt64,AppError]:\n"
        "    if value.len() > 0:\n"
        "        return Ok(1)\n"
        "    return Err(AppError.Failed)\n"
        "fn run(input: BytesView) -> Result[UInt64,AppError]:\n"
        "    let value: UInt64 = validate(Text.from_bytes(input, 0, input.len()))?\n"
        "    return Ok(value)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return 0\n"
    )
    hir, rir, _mir, optimized = _layers(source)
    generated = emit_general_c(hir, rir, optimized)
    assert generated.source.count("merlo_drop_Text(&__merlo_owned_temp_1);") == 2


def test_nested_borrowed_slice_rejects_materialized_vec_owner() -> None:
    source = (
        "record HolderSlice:\n"
        "    view: Slice[UInt64]\n"
        "fn borrow_slice(values: Vec[UInt64]) -> Slice[UInt64]:\n"
        "    return values.view()\n"
        "fn wrapper(input: BytesView) -> HolderSlice:\n"
        "    return HolderSlice(borrow_slice(Vec.new()))\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return 0\n"
    )
    hir, rir, _mir, optimized = _layers(source)
    with pytest.raises(RepresentationCBackendError, match="borrowed result escapes"):
        emit_general_c(hir, rir, optimized)


def test_vec_view_of_owned_call_rejects_borrowed_escape() -> None:
    source = (
        "fn make() -> Vec[UInt64]:\n"
        "    return Vec.new()\n"
        "fn wrapper(input: BytesView) -> Slice[UInt64]:\n"
        "    return make().view()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return 0\n"
    )
    hir, rir, _mir, optimized = _layers(source)
    with pytest.raises(RepresentationCBackendError, match="borrowed result escapes"):
        emit_general_c(hir, rir, optimized)


def test_vec_text_get_clones_payload_before_temporary_drop(tmp_path: Path) -> None:
    source = (
        "fn make() -> Vec[Text]:\n"
        "    let values: Vec[Text] = Vec.new()\n"
        "    values.push(\"abc\")\n"
        "    return values\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let value: Text = make().get(0)\n"
        "    return value.len()\n"
    )
    binary = _native(source, tmp_path, "vec-text-get")
    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=3" in completed.stdout
    assert b"text_allocations=2 text_frees=2" in completed.stdout


def test_box_text_get_clones_payload_before_temporary_drop(tmp_path: Path) -> None:
    source = (
        "fn make() -> Box[Text]:\n"
        "    return Box.new(\"abc\")\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let value: Text = make().get()\n"
        "    return value.len()\n"
    )
    binary = _native(source, tmp_path, "box-text-get")
    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=3" in completed.stdout
    assert b"text_allocations=2 text_frees=2" in completed.stdout


def test_vec_scalar_get_copies_before_temporary_drop(tmp_path: Path) -> None:
    source = (
        "fn make() -> Vec[UInt64]:\n"
        "    let values: Vec[UInt64] = Vec.new()\n"
        "    values.push(7)\n"
        "    return values\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let value: UInt64 = make().get(0)\n"
        "    return value\n"
    )
    binary = _native(source, tmp_path, "vec-scalar-get")
    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=7" in completed.stdout


def test_vec_get_mut_rejects_temporary_owner_escape() -> None:
    source = (
        "fn make() -> Vec[UInt64]:\n"
        "    return Vec.new()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let value: UInt64 = make().get_mut(0)\n"
        "    return value\n"
    )
    hir, rir, _mir, optimized = _layers(source)
    with pytest.raises(RepresentationCBackendError, match="borrowed result escapes"):
        emit_general_c(hir, rir, optimized)


def test_vec_len_uses_named_and_temporary_receivers_once(tmp_path: Path) -> None:
    source = (
        "fn make() -> Vec[UInt64]:\n"
        "    let values: Vec[UInt64] = Vec.new()\n"
        "    values.push(1)\n"
        "    return values\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let values: Vec[UInt64] = make()\n"
        "    return values.len() + make().len()\n"
    )
    binary = _native(source, tmp_path, "vec-len")
    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=2" in completed.stdout


def test_owner_callback_parameter_is_rejected_until_ownership_is_explicit() -> None:
    source = (
        "fn inspect(value: Text) -> UInt64:\n"
        "    return value.len()\n"
        "fn apply(callback: Fn[Text,UInt64], value: Text) -> UInt64:\n"
        "    return callback(value)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        '    return apply(inspect, "x")\n'
    )
    hir, rir, _mir, optimized = _layers(source)
    with pytest.raises(
        RepresentationCBackendError,
        match="owning callback parameters require explicit ownership",
    ):
        emit_general_c(hir, rir, optimized)


def test_get_mut_forwards_original_nested_vec(tmp_path: Path) -> None:
    source = (
        "fn append(values: Vec[UInt64]) -> Unit:\n"
        "    values.push(9)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let nested: Vec[Vec[UInt64]] = Vec.new()\n"
        "    let values: Vec[UInt64] = Vec.new()\n"
        "    nested.push(values)\n"
        "    append(nested.get_mut(0))\n"
        "    return nested.get(0).len()\n"
    )
    binary = _native(source, tmp_path, "nested-get-mut")
    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=1" in completed.stdout


def test_borrowed_match_payload_is_cloned_not_stolen(tmp_path: Path) -> None:
    source = (
        "enum Holder:\n"
        "    Value: Text\n"
        "fn extract(holder: Holder) -> Text:\n"
        "    match holder:\n"
        "        case Value(text):\n"
        "            return text\n"
        "fn main(input: BytesView) -> UInt64:\n"
        '    let holder: Holder = Holder.Value("abc")\n'
        "    let copied: Text = extract(holder)\n"
        "    match holder:\n"
        "        case Value(original):\n"
        "            return copied.len() + original.len()\n"
    )
    binary = _native(source, tmp_path, "borrowed-match")
    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=6" in completed.stdout


def test_borrowed_owner_assignment_clones_source(tmp_path: Path) -> None:
    source = (
        "fn copy(value: Text) -> Text:\n"
        "    let result: Text = value\n"
        "    return result\n"
        "fn main(input: BytesView) -> UInt64:\n"
        '    let source: Text = "abc"\n'
        "    let result: Text = copy(source)\n"
        "    return source.len() + result.len()\n"
    )
    binary = _native(source, tmp_path, "borrowed-assignment")
    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=6" in completed.stdout


def test_match_payload_from_vec_borrow_is_cloned(tmp_path: Path) -> None:
    source = (
        "enum Holder:\n"
        "    Value: Text\n"
        "fn extract(values: Vec[Holder]) -> Text:\n"
        "    match values.get(0):\n"
        "        case Value(text):\n"
        "            return text\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let values: Vec[Holder] = Vec.new()\n"
        '    values.push(Holder.Value("abc"))\n'
        "    let copied: Text = extract(values)\n"
        "    match values.get(0):\n"
        "        case Value(original):\n"
        "            return copied.len() + original.len()\n"
    )
    binary = _native(source, tmp_path, "borrowed-vec-match")
    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=6" in completed.stdout


def test_match_payload_from_borrowed_projection_is_cloned(tmp_path: Path) -> None:
    source = (
        "enum Holder:\n"
        "    Value: Text\n"
        "record Wrapper:\n"
        "    choice: Holder\n"
        "fn extract(wrapper: Wrapper) -> Text:\n"
        "    match wrapper.choice:\n"
        "        case Value(text):\n"
        "            return text\n"
        "fn main(input: BytesView) -> UInt64:\n"
        '    let wrapper: Wrapper = Wrapper(Holder.Value("abc"))\n'
        "    let copied: Text = extract(wrapper)\n"
        "    match wrapper.choice:\n"
        "        case Value(original):\n"
        "            return copied.len() + original.len()\n"
    )
    binary = _native(source, tmp_path, "borrowed-projection-match")
    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=6" in completed.stdout


def test_direct_owner_projection_from_borrow_is_cloned(tmp_path: Path) -> None:
    source = (
        "record Wrapper:\n"
        "    text: Text\n"
        "fn extract(wrapper: Wrapper) -> Text:\n"
        "    return wrapper.text\n"
        "fn main(input: BytesView) -> UInt64:\n"
        '    let wrapper: Wrapper = Wrapper("abc")\n'
        "    let copied: Text = extract(wrapper)\n"
        "    return copied.len() + wrapper.text.len()\n"
    )
    binary = _native(source, tmp_path, "borrowed-direct-projection")
    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=6" in completed.stdout


def test_owner_array_subscript_from_borrow_is_cloned(tmp_path: Path) -> None:
    source = (
        "fn extract(values: Array[Text,1]) -> Text:\n"
        "    return values[0]\n"
        "fn main(input: BytesView) -> UInt64:\n"
        '    let values: Array[Text,1] = ["abc"]\n'
        "    let copied: Text = extract(values)\n"
        "    return copied.len() + values[0].len()\n"
    )
    binary = _native(source, tmp_path, "borrowed-array-subscript")
    completed = subprocess.run([str(binary)], input=b"", capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=6" in completed.stdout


def test_owner_array_parameter_is_borrowed_but_scalar_array_is_value() -> None:
    hir = compile_structured_hir(
        "fn owner(values: Array[Text,1]) -> UInt64:\n"
        "    return values[0].len()\n"
        "fn scalar(values: Array[UInt64,1]) -> UInt64:\n"
        "    return values[0]\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return 0\n",
        path="array-ownership.mlo",
    )

    assert hir.function("owner").parameters[0].ownership == "borrow"
    assert hir.function("scalar").parameters[0].ownership == "value"


def test_owner_array_projection_is_clean_under_address_and_undefined_sanitizers(
    tmp_path: Path,
) -> None:
    source = (
        "fn extract(values: Array[Text,1]) -> Text:\n"
        "    return values[0]\n"
        "fn main(input: BytesView) -> UInt64:\n"
        '    let values: Array[Text,1] = ["abc"]\n'
        "    let copied: Text = extract(values)\n"
        "    return copied.len() + values[0].len()\n"
    )
    hir, rir, _mir, optimized = _layers(source)
    generated = emit_general_c(hir, rir, optimized)
    source_path = tmp_path / "array-owner-sanitized.c"
    binary = tmp_path / "array-owner-sanitized"
    source_path.write_text(generated.source, encoding="utf-8")
    compiler = find_c_compiler()
    assert compiler is not None
    built = subprocess.run(
        [
            compiler,
            "-std=c11",
            "-O1",
            "-g",
            "-fno-omit-frame-pointer",
            "-fsanitize=address,undefined",
            str(source_path),
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    environment = dict(os.environ)
    environment.update(
        {
            "ASAN_OPTIONS": "detect_leaks=0:halt_on_error=1",
            "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
        }
    )
    completed = subprocess.run(
        [str(binary)],
        input=b"",
        capture_output=True,
        check=False,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=6" in completed.stdout


def test_empty_console_write_builds_without_null_fwrite(tmp_path: Path) -> None:
    binary = _native(
        "fn main(input: BytesView) -> UInt64:\n"
        '    console.write("")\n'
        "    return 0\n",
        tmp_path,
        "empty-console-write",
    )
    assert binary.is_file()


def test_native_compiler_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    default = find_c_compiler()
    assert default is not None
    monkeypatch.setenv("MERLO_C_COMPILER", default)
    assert find_c_compiler() == default
    monkeypatch.setenv("MERLO_C_COMPILER", "merlo-compiler-that-does-not-exist")
    assert find_c_compiler() is None
