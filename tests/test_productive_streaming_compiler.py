from __future__ import annotations
import os
import socket
import subprocess
import threading
from pathlib import Path

from merlo.frontend_model import ConciseApplicationError
from merlo.concise_services import elaborate_concise_core
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
    let file: FileWriter = fs.open_write(path)?
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
        "error_type_id": hir.type_context.type_id("AppError"),
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
        "error_type_id": hir.type_context.type_id("AppError"),
        "ok": "unwrap_and_continue",
        "result_type": "Result[FileReader,AppError]",
        "result_type_id": hir.type_context.type_id(
            "Result[FileReader,AppError]"
        ),
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


def test_resource_cleanup_is_emitted_for_normal_early_and_try_paths() -> None:
    sources = {
        "reader-normal": (
            "enum AppError:\n    FileOpen\n"
            "task main(path: Path) -> Result[Unit,AppError]:\n"
            "    uses fs.read\n"
            "    let reader: FileReader = fs.open_read(path)?\n"
            "    return Ok(Unit())\n",
            ("merlo_drop_FileReader(&reader);",),
        ),
        "writer-normal": (
            "enum AppError:\n    FileOpen\n"
            "task main(path: Path) -> Result[Unit,AppError]:\n"
            "    uses fs.write\n"
            "    let writer: FileWriter = fs.open_write(path)?\n"
            "    return Ok(Unit())\n",
            ("merlo_drop_FileWriter(&writer);",),
        ),
        "early-return": (
            "enum AppError:\n    FileOpen\n"
            "task main(path: Path) -> Result[Unit,AppError]:\n"
            "    uses fs.read\n"
            "    let reader: FileReader = fs.open_read(path)?\n"
            "    if true:\n"
            "        return Ok(Unit())\n"
            "    return Ok(Unit())\n",
            ("merlo_drop_FileReader(&reader);",),
        ),
        "two-handles": (
            "enum AppError:\n    FileOpen\n"
            "task main(path: Path) -> Result[Unit,AppError]:\n"
            "    uses fs.read\n"
            "    let first: FileReader = fs.open_read(path)?\n"
            "    let second: FileReader = fs.open_read(path)?\n"
            "    return Ok(Unit())\n",
            (
                "merlo_drop_FileReader(&first);",
                "merlo_drop_FileReader(&second);",
            ),
        ),
    }
    for name, (source, required) in sources.items():
        hir = compile_structured_hir(source, path=f"{name}.mlo", entry_function="main")
        rir = lower_structured_hir_to_rir(hir)
        mir = lower_rir_to_performance_mir(hir, rir)
        generated = emit_general_c(hir, rir, mir).source
        function_body = generated.split("merlo_fn_main(", 1)[1]
        for cleanup in required:
            assert cleanup in function_body


def test_explicit_close_consumes_handle_without_scope_double_close() -> None:
    source = (
        "enum AppError:\n    FileOpen\n    Closed\n"
        "task main(path: Path) -> Result[Unit,AppError]:\n"
        "    uses fs.read\n"
        "    let reader: FileReader = fs.open_read(path)?\n"
        "    fs.close_read(reader)?\n"
        "    return Ok(Unit())\n"
    )
    hir = compile_structured_hir(source, path="explicit-close.mlo", entry_function="main")
    rir = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, rir)
    function_body = emit_general_c(hir, rir, mir).source.split("merlo_fn_main(", 1)[1]
    assert "merlo_file_close(&(reader))" in function_body
    assert "merlo_drop_FileReader(&reader);" not in function_body


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


def _build_effect_program(
    source: str,
    tmp_path: Path,
    stem: str,
) -> Path:
    hir = compile_structured_hir(
        source,
        path=f"{stem}.mlo",
        entry_function="main",
    )
    rir = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, rir)
    generated = emit_general_c(hir, rir, mir)
    build = compile_c_source(
        generated.source,
        output_dir=tmp_path,
        stem=stem,
    )
    assert build.status == "MEASURED", build
    assert build.binary_path is not None
    return build.binary_path


def test_text_entry_reads_stdin_and_writes_exact_text(
    tmp_path: Path,
) -> None:
    binary = _build_effect_program(
        "task main(input: Text) -> Text:\n"
        "    return input\n",
        tmp_path,
        "text-entry",
    )
    completed = subprocess.run(
        [binary],
        input=b"hello\nworld",
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert (completed.returncode, completed.stdout, completed.stderr) == (
        0,
        b"hello\nworld",
        b"",
    )
    invalid = subprocess.run(
        [binary],
        input=b"\xff",
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert (invalid.returncode, invalid.stdout, invalid.stderr) == (
        74,
        b"",
        b"InvalidUtf8\n",
    )


def test_direct_host_result_evaluates_before_error_projection(
    tmp_path: Path,
) -> None:
    source = """
enum AppError:
    NotFound
    ReadFailure
    InvalidUtf8
    PermissionDenied
    Closed

task main(path: Path) -> Result[Text,AppError]:
    uses fs.read
    return fs.read_text(path)
"""
    binary = _build_effect_program(source, tmp_path, "direct-result")
    missing = tmp_path / "missing.txt"
    completed = subprocess.run(
        [binary, str(missing)],
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 74
    assert completed.stdout == b""
    assert completed.stderr == f"AppError.NotFound:{missing}\n".encode()


def test_console_read_line_and_all_are_distinct_in_native_runtime(
    tmp_path: Path,
) -> None:
    line_source = """
enum AppError:
    System

task main(path: Path) -> Result[Text,AppError]:
    uses console.read
    return Ok(console.read_line())
"""
    all_source = line_source.replace("read_line", "read_all")
    line = _build_effect_program(line_source, tmp_path, "read-line")
    read_all = _build_effect_program(all_source, tmp_path, "read-all")
    payload = b"first\nsecond"
    line_result = subprocess.run(
        [line, "unused"],
        input=payload,
        capture_output=True,
        check=False,
        timeout=10,
    )
    all_result = subprocess.run(
        [read_all, "unused"],
        input=payload,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert (line_result.returncode, line_result.stdout) == (0, b"first\n")
    invalid_result = subprocess.run(
        [read_all, "unused"],
        input=b"\xff",
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert invalid_result.returncode != 0
    assert invalid_result.stderr == b"InvalidUtf8\n"
    assert (all_result.returncode, all_result.stdout) == (
        0,
        b"first\nsecond\n",
    )


def test_process_argument_lookup_reaches_native_program(
    tmp_path: Path,
) -> None:
    source = """
enum AppError:
    System

task main(path: Path) -> Result[Text,AppError]:
    uses process.args
    return Ok(process.arg(0))
"""
    binary = _build_effect_program(source, tmp_path, "process-arg")
    completed = subprocess.run(
        [binary, "argument-value"],
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert (completed.returncode, completed.stdout, completed.stderr) == (
        0,
        b"argument-value\n",
        b"",
    )


def test_http_intrinsic_returns_actual_response_body(
    tmp_path: Path,
) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            request = b""
            while b"\r\n\r\n" not in request:
                request += connection.recv(4096)
            assert request.startswith(b"GET /body HTTP/1.0\r\n")
            connection.sendall(
                b"HTTP/1.0 200 OK\r\nContent-Length: 5\r\n\r\nhello"
            )
        listener.close()

    server = threading.Thread(target=serve, daemon=True)
    source = """
enum AppError:
    ConnectionRefused
    System

task main(path: Path) -> Result[Text,AppError]:
    uses network.http
    let bytes: Bytes = network.http_request(path.to_text())?
    return Ok(Text.from_bytes(bytes, 0, bytes.len()))
"""
    binary = _build_effect_program(source, tmp_path, "http-request")
    server.start()
    environment = dict(os.environ)
    environment["MERLO_NETWORK_HOST"] = "127.0.0.1"
    completed = subprocess.run(
        [binary, f"http://127.0.0.1:{port}/body"],
        capture_output=True,
        check=False,
        env=environment,
        timeout=10,
    )
    server.join(timeout=10)
    assert not server.is_alive()
    assert (completed.returncode, completed.stdout, completed.stderr) == (
        0,
        b"hello\n",
        b"",
    )
