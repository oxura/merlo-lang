from __future__ import annotations
import subprocess
from pathlib import Path

from merlo.concise_application import ConciseApplicationError, elaborate_concise_core
from merlo.native_c_backend import compile_c_source
from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import lower_rir_to_performance_mir
from merlo.structured_hir_v2 import _preprocess, compile_structured_hir

STREAM_SOURCE = """

enum AppError:
    FileOpen
    InvalidUtf8

fn count(lines: FileLines) -> UInt64:
    var total: UInt64 = 0
    for line in lines:
        total = total + line.len()
    return total

fn main(path: Path) -> Result[Text, AppError]:
    let file: FileReader = fs.open_read(path)?
    let builder: TextBuilder = TextBuilder.new()
    for line in file.lines():
        builder.append_scalar(line.len() + 48)
    let result: Text = builder.finish()
    return Ok(result)
"""

NESTED_RESULT_SOURCE = """
enum AppError:
    FileOpen

fn load(path: Path) -> Result[FileReader, AppError]:
    let file: FileReader = fs.open_read(path)?
    return Ok(file)
fn main(path: Path) -> Result[Text, AppError]:
    let file: FileReader = load(path)?
    let builder: TextBuilder = TextBuilder.new()
    builder.append_byte(111)
    builder.append_byte(107)
    let result: Text = builder.finish()
    return Ok(result)
"""

OUTPUT_CLOSE_SOURCE = """
enum AppError:
    FileOpen
    FileWrite

fn main(path: Path) -> Result[Text, AppError]:
    let file: FileReader = fs.open_write(path)
    return Ok("ok")
"""


def test_streaming_operations_survive_hir_rir_mir() -> None:
    hir = compile_structured_hir(STREAM_SOURCE, path="stream.mlo", entry_function="main")
    kinds = {
        node.kind
        for function in hir.functions
        for root in function.body
        for node in root.walk()
    }
    assert {"FileOpen", "FileLines"} <= kinds
    file_open = next(
        node
        for function in hir.functions
        for node in function.walk()
        if node.kind == "FileOpen"
    )
    assert file_open.type_name == "Result[FileReader,AppError]"
    assert file_open.attribute_map == {
        "callee": "fs.open_read",
        "error_type": "AppError",
        "host_operation": "open_read",
        "resource": "FileReader",
    }
    rir = lower_structured_hir_to_rir(hir)
    operations = {
        operation.op
        for function in rir.functions
        for root in function.operations
        for operation in root.walk()
    }
    assert {"file_open_read", "file_lines"} <= operations
    file_lines = rir.descriptor("FileLines")
    assert (file_lines.size, file_lines.alignment) == (16, 8)
    mir = lower_rir_to_performance_mir(hir, rir)
    instructions = {
        instruction.op
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
    }
    assert {
        "open_file_reader",
        "file_line_next",
        "invalidate_line_borrow",
    } <= instructions

def test_postfix_result_propagation_survives_hir_rir_mir_without_raw_question_mark() -> None:
    hir = compile_structured_hir(NESTED_RESULT_SOURCE, path="nested-result.mlo", entry_function="main")
    nodes = [node for function in hir.functions for node in function.walk()]
    assert "?" not in _preprocess(NESTED_RESULT_SOURCE).source
    assert any(node.kind == "ResultPropagation" for node in nodes)
    rir = lower_structured_hir_to_rir(hir)
    operations = [operation for function in rir.functions for operation in function.walk()]
    assert any(operation.op == "try_result" for operation in operations)
    mir = lower_rir_to_performance_mir(hir, rir)
    instructions = [
        instruction
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
    ]
    branches = [instruction for instruction in instructions if instruction.op == "result_branch"]
    assert branches
    assert branches[0].attribute_map == {
        "cleanup": "initialized_owned_locals",
        "err": "early_return",
        "error_type": "AppError",
        "ok": "unwrap_and_continue",
        "result_type": "Result[FileReader,AppError]",
    }


def test_nested_result_propagation_returns_typed_file_error_without_post_effect(tmp_path: Path) -> None:
    hir = compile_structured_hir(NESTED_RESULT_SOURCE, path="nested-result.mlo", entry_function="main")
    rir = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, rir)
    generated = emit_general_c(hir, rir, mir)
    build = compile_c_source(generated.source, output_dir=tmp_path, stem="nested-result")
    assert build.status == "MEASURED"
    assert build.binary_path is not None

    missing = tmp_path / "missing.txt"
    failed = subprocess.run(
        [build.binary_path, str(missing)],
        check=False,
        capture_output=True,
        timeout=10,
    )
    assert failed.returncode == 74
    assert failed.stdout == b""
    assert failed.stderr == f"AppError.FileOpen:{missing}\n".encode()

    present = tmp_path / "present.txt"
    present.write_bytes(b"ok\n")
    succeeded = subprocess.run(
        [build.binary_path, str(present)],
        check=False,
        capture_output=True,
        timeout=10,
    )
    assert (succeeded.returncode, succeeded.stdout, succeeded.stderr) == (
        0,
        b"ok\n",
        b"",
    )

def test_generated_c_contains_allowlisted_stream_runtime_and_cfg_loop() -> None:
    hir = compile_structured_hir(STREAM_SOURCE, path="stream.mlo", entry_function="main")
    rir = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, rir)
    generated = emit_general_c(hir, rir, mir)
    assert "merlo_file_open_read" in generated.source
    assert "merlo_file_next" in generated.source
    assert "generation" in generated.source
    assert "fopen" in generated.source
    assert "fclose" in generated.source
    assert "json_parse" not in generated.source

def test_generated_stream_runtime_reuses_an_amortized_line_buffer() -> None:
    hir = compile_structured_hir(STREAM_SOURCE, path="stream.mlo", entry_function="main")
    rir = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, rir)
    generated = emit_general_c(hir, rir, mir)
    assert "buffer_capacity" in generated.source
    assert "getline(&line, &capacity, reader->stream)" in generated.source

def test_generated_collection_growth_avoids_short_parse_reallocations() -> None:
    hir = compile_structured_hir(STREAM_SOURCE, path="stream.mlo", entry_function="main")
    rir = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, rir)
    generated = emit_general_c(hir, rir, mir)
    assert "if (capacity < 32) capacity = 32;" in generated.source
    vec_hir = compile_structured_hir(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let values: Vec[UInt64] = Vec.new()\n"
        "    values.push(1)\n"
        "    return values.len()\n",
        path="vec.mlo",
        entry_function="main",
    )
    vec_rir = lower_structured_hir_to_rir(vec_hir)
    vec_mir = lower_rir_to_performance_mir(vec_hir, vec_rir)
    vec_generated = emit_general_c(vec_hir, vec_rir, vec_mir)
    assert "if (capacity < 8) capacity = 8;" in vec_generated.source

def test_path_host_elides_unobserved_runtime_metrics() -> None:
    hir = compile_structured_hir(STREAM_SOURCE, path="stream.mlo", entry_function="main")
    rir = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, rir)
    generated = emit_general_c(hir, rir, mir)
    assert "#define merlo_allocations ((uint64_t){0})" in generated.source




def test_native_streaming_reads_unterminated_line_and_projects_typed_errors(
    tmp_path: Path,
) -> None:
    hir = compile_structured_hir(STREAM_SOURCE, path="stream.mlo", entry_function="main")
    rir = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, rir)
    generated = emit_general_c(hir, rir, mir)
    build = compile_c_source(generated.source, output_dir=tmp_path, stem="stream")
    assert build.status == "MEASURED"
    assert build.binary_path is not None

    good = tmp_path / "good.txt"
    good.write_bytes(b"first\nsecond")
    completed = subprocess.run(
        [build.binary_path, str(good)],
        check=False,
        capture_output=True,
        timeout=10,
    )
    assert (completed.returncode, completed.stdout, completed.stderr) == (0, b"56\n", b"")

    invalid_inputs = {
        "invalid-leading-byte": b"ok\n\xffbad",
        "overlong-nul": b"ok\n\xe0\x80\x80",
        "surrogate": b"ok\n\xed\xa0\x80",
        "above-unicode-range": b"ok\n\xf4\x90\x80\x80",
    }
    for name, payload in invalid_inputs.items():
        invalid = tmp_path / f"{name}.txt"
        invalid.write_bytes(payload)
        completed = subprocess.run(
            [build.binary_path, str(invalid)],
            check=False,
            capture_output=True,
            timeout=10,
        )
        assert completed.returncode == 74
        assert completed.stdout == b""
        assert completed.stderr == b"AppError.InvalidUtf8:2\n"

    missing = tmp_path / "missing.txt"
    completed = subprocess.run(
        [build.binary_path, str(missing)],
        check=False,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 74
    assert completed.stdout == b""
    assert completed.stderr.startswith(b"AppError.FileOpen:")


def test_native_output_close_failure_is_observable(tmp_path: Path) -> None:
    hir = compile_structured_hir(
        OUTPUT_CLOSE_SOURCE,
        path="output-close.mlo",
        entry_function="main",
    )
    rir = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, rir)
    generated = emit_general_c(hir, rir, mir)
    marker = "static uint64_t merlo_allocations"
    faulted_source = generated.source.replace(
        marker,
        "#define fclose(stream) ((void)(stream), errno = ENOSPC, EOF)\n" + marker,
        1,
    )
    build = compile_c_source(
        faulted_source,
        output_dir=tmp_path,
        stem="output-close",
    )
    assert build.status == "MEASURED"
    assert build.binary_path is not None

    output = tmp_path / "output.bin"
    completed = subprocess.run(
        [build.binary_path, str(output)],
        check=False,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 74
    assert completed.stdout == b""
    assert completed.stderr == f"AppError.FileWrite:{output}\n".encode()


def test_pure_file_read_and_missing_capability_are_rejected() -> None:
    pure = "fn read(path: Path) -> FileReader:\n    return fs.open_read(path)?\n"
    try:
        elaborate_concise_core(pure, path="pure.mlo")
    except ConciseApplicationError as error:
        assert "EffectInPureFunction" in str(error)
    else:
        raise AssertionError("pure fs.open_read unexpectedly accepted")


def test_ordinary_merlo_does_not_spell_manual_close() -> None:
    assert "fclose" not in STREAM_SOURCE
    assert "malloc" not in STREAM_SOURCE
    assert "free" not in STREAM_SOURCE
