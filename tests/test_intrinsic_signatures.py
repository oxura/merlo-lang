from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from merlo.intrinsics import (
    BUILTIN_FUNCTIONS,
    BUILTIN_FUNCTION_SIGNATURES,
    BUILTIN_RECEIVERS,
    CONTRACT_GRAPH,
    INTRINSIC_EFFECTS,
    INTRINSIC_SIGNATURES,
    INSTANCE_METHOD_SIGNATURES,
    contextual_result_type,
    intrinsic_signature,
)
from merlo.structured_hir_v2 import (
    StructuredHIRCompileError,
    compile_structured_hir,
)
from merlo.compiler import compile_project
from merlo.project import Project


EXPECTED = {
    "console.read": ((), "Bytes", "console.read"),
    "console.read_line": ((), "Text", "console.read"),
    "console.read_all": ((), "Text", "console.read"),
    "console.write": (("TextView",), "Unit", "console.write"),
    "fs.open_read": (("Path",), "Result[FileReader,FileError]", "fs.read"),
    "fs.read": (("Path",), "Result[Bytes,FileError]", "fs.read"),
    "fs.read_text": (("Path",), "Result[Text,FileError]", "fs.read"),
    "fs.read_chunk": (("FileReader", "UInt64"), "Result[Bytes,FileError]", "fs.read"),
    "fs.open_write": (("Path",), "Result[FileWriter,FileError]", "fs.write"),
    "fs.write": (("Path", "BytesView"), "Result[Unit,FileError]", "fs.write"),
    "fs.write_text": (("Path", "TextView"), "Result[Unit,FileError]", "fs.write"),
    "fs.write_chunk": (("FileWriter", "BytesView"), "Result[Unit,FileError]", "fs.write"),
    "fs.close_read": (("FileReader",), "Result[Unit,FileError]", "fs.read"),
    "fs.close_write": (("FileWriter",), "Result[Unit,FileError]", "fs.write"),
    "env.read": (("Text",), "Text", "env.read"),
    "env.get": (("Text",), "Text", "env.read"),
    "clock.now": ((), "UInt64", "clock.now"),
    "random.read": (("UInt64",), "Bytes", "random.read"),
    "process.args": ((), "UInt64", "process.args"),
    "process.arg": (("UInt64",), "Text", "process.args"),
    "network.tcp_connect": (("Text", "UInt64"), "Result[UInt64,AppError]", "network.tcp"),
    "network.tcp_send": (("UInt64", "BytesView"), "Result[UInt64,AppError]", "network.tcp"),
    "network.tcp_receive": (("UInt64", "UInt64"), "Result[Bytes,AppError]", "network.tcp"),
    "network.tcp_close": (("UInt64",), "Result[Unit,AppError]", "network.tcp"),
    "network.http_request": (("Text",), "Result[Bytes,AppError]", "network.http"),
}


def test_every_canonical_entry_has_exact_contract() -> None:
    assert set(INTRINSIC_SIGNATURES) == set(EXPECTED)
    for name, (parameters, result, effect) in EXPECTED.items():
        signature = intrinsic_signature(name)
        assert signature is not None
        assert signature.parameters == parameters
        assert signature.result_type == result
        assert signature.effect == signature.capability == effect
        assert signature.arity == len(parameters)


def test_table_entries_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        intrinsic_signature("clock.now").result_type = "Text"  # type: ignore[misc]
    with pytest.raises(TypeError):
        INTRINSIC_SIGNATURES["clock.now"] = intrinsic_signature("clock.now")  # type: ignore[index]


def test_contextual_result_preserves_error_row() -> None:
    assert contextual_result_type(
        "Result[FileReader,AppError]", "Result[FileReader,FsError]"
    ) == "Result[FileReader,FsError]"
    assert contextual_result_type("Text", "Result[Text,FsError]") == "Text"


def test_effect_set_is_derived_from_rows() -> None:
    assert INTRINSIC_EFFECTS == frozenset(signature.effect for signature in INTRINSIC_SIGNATURES.values())


def test_binder_and_elaborator_contract_views_are_derived() -> None:
    assert {"Path", "Ok", "Some", "checked_add"} <= BUILTIN_FUNCTIONS
    assert {"console", "fs", "network", "Text", "Vec"} <= BUILTIN_RECEIVERS
    assert INSTANCE_METHOD_SIGNATURES[("Text", "contains")].parameters == ("Text",)
    assert INSTANCE_METHOD_SIGNATURES[("TextBuilder", "finish")].result_ownership == "owned"
    assert BUILTIN_FUNCTION_SIGNATURES["Path"].parameters == ("Text",)
    assert BUILTIN_FUNCTION_SIGNATURES["drop"].parameter_ownership == ("consuming",)
    assert BUILTIN_FUNCTIONS == frozenset(BUILTIN_FUNCTION_SIGNATURES)
    assert CONTRACT_GRAPH.intrinsic("fs.write_text") is INTRINSIC_SIGNATURES["fs.write_text"]
    assert CONTRACT_GRAPH.abi_lowering("fs.write_text") == "merlo_file_write_text"
    assert CONTRACT_GRAPH.method("TextBuilder", "append_text").receiver_ownership == "borrow_mut"  # type: ignore[union-attr]
    assert CONTRACT_GRAPH.method("TextBuilder", "finish").receiver_ownership == "consuming"  # type: ignore[union-attr]
    text_from_bytes = CONTRACT_GRAPH.static_method("Text", "from_bytes")
    assert text_from_bytes is not None
    assert text_from_bytes.parameters == ("BytesView", "UInt64", "UInt64")
    assert text_from_bytes.result_type == "Text"
    assert text_from_bytes.result_ownership == "owned"
    assert text_from_bytes.effects == ("allocate", "copy", "may_fail")
    assert CONTRACT_GRAPH.abi_lowering("Text.from_bytes") == (
        "merlo_text_from_bytes"
    )
    builder_new = CONTRACT_GRAPH.static_method("TextBuilder", "new")
    assert builder_new is not None
    assert builder_new.effects == ("allocate", "may_fail")
    assert CONTRACT_GRAPH.abi_lowering("TextBuilder.new") == (
        "merlo_text_builder_new"
    )
    option_predicate = CONTRACT_GRAPH.method(
        "Option[Text]",
        "is_some",
    )
    assert option_predicate is not None
    assert option_predicate.receiver_type == "Option[Text]"
    assert option_predicate.result_type == "Bool"
    assert option_predicate.receiver_ownership == "borrow"
    assert option_predicate.representation_lowering == "option_is_some"
    result_predicate = CONTRACT_GRAPH.method(
        "Result[UInt64,Text]",
        "is_err",
    )
    assert result_predicate is not None
    assert result_predicate.result_type == "Bool"
    assert result_predicate.representation_lowering == "result_is_err"
    assert CONTRACT_GRAPH.method("Option[Text]", "is_ok") is None
    assert CONTRACT_GRAPH.method("Result[UInt64,Text]", "is_none") is None
    option_unwrap = CONTRACT_GRAPH.method("Option[Text]", "unwrap")
    assert option_unwrap is not None
    assert option_unwrap.result_type == "Text"
    assert option_unwrap.result_ownership == "payload_clone"
    assert option_unwrap.receiver_ownership == "borrow"
    assert option_unwrap.effects == ("allocate", "copy", "may_fail")
    assert option_unwrap.representation_lowering == "option_unwrap_clone"
    result_unwrap_err = CONTRACT_GRAPH.method(
        "Result[UInt64,Text]",
        "unwrap_err",
    )
    assert result_unwrap_err is not None
    assert result_unwrap_err.result_type == "Text"
    assert result_unwrap_err.representation_lowering == (
        "result_unwrap_err_clone"
    )


def test_static_contracts_drive_hir_type_ownership_effects_and_abi() -> None:
    hir = compile_structured_hir(
        "fn build(input: BytesView) -> Text:\n"
        "    let copied: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let builder: TextBuilder = TextBuilder.new()\n"
        "    builder.append_text(copied)\n"
        "    builder.finish()\n",
        entry_function="build",
    )
    calls = {
        node.attribute_map.get("callee"): node
        for function in hir.functions
        for node in function.walk()
        if node.attribute_map.get("callee")
        in {"Text.from_bytes", "TextBuilder.new"}
    }
    copied = calls["Text.from_bytes"]
    assert copied.type_name == "Text"
    assert copied.ownership == "owned"
    assert set(copied.effects) == {"allocate", "copy", "may_fail"}
    assert copied.attribute_map["abi_lowering"] == "merlo_text_from_bytes"
    builder = calls["TextBuilder.new"]
    assert builder.type_name == "TextBuilder"
    assert builder.ownership == "owned"
    assert set(builder.effects) == {"allocate", "may_fail"}
    assert builder.attribute_map["abi_lowering"] == "merlo_text_builder_new"


def test_generic_predicate_contracts_drive_hir_metadata() -> None:
    hir = compile_structured_hir(
        "fn flags(option: Option[UInt64], result: Result[UInt64,UInt64]) -> Bool:\n"
        "    let some: Bool = option.is_some()\n"
        "    let ok: Bool = result.is_ok()\n"
        "    return some and ok\n",
        entry_function="flags",
    )
    predicates = {
        node.attribute_map.get("representation_lowering"): node
        for function in hir.functions
        for node in function.walk()
        if node.attribute_map.get("representation_lowering")
        in {"option_is_some", "result_is_ok"}
    }
    assert set(predicates) == {"option_is_some", "result_is_ok"}
    assert predicates["option_is_some"].type_name == "Bool"
    assert predicates["option_is_some"].ownership == "value"
    assert predicates["option_is_some"].attribute_map[
        "contract_symbol"
    ] == "Option[UInt64].is_some"
    assert predicates["result_is_ok"].type_name == "Bool"
    assert predicates["result_is_ok"].attribute_map[
        "contract_symbol"
    ] == "Result[UInt64,UInt64].is_ok"


def test_generic_unwrap_contracts_derive_payload_ownership_and_effects() -> None:
    hir = compile_structured_hir(
        "fn text(option: Option[Text]) -> Text:\n"
        "    return option.unwrap()\n"
        "fn scalar(result: Result[UInt64,Text]) -> UInt64:\n"
        "    return result.unwrap()\n",
        entry_function="text",
    )
    accessors = {
        node.attribute_map.get("contract_symbol"): node
        for function in hir.functions
        for node in function.walk()
        if node.attribute_map.get("representation_lowering")
        in {"option_unwrap_clone", "result_unwrap_clone"}
    }
    text = accessors["Option[Text].unwrap"]
    assert text.type_name == "Text"
    assert text.ownership == "owned"
    assert set(text.effects) == {"allocate", "copy", "may_fail"}
    scalar = accessors["Result[UInt64,Text].unwrap"]
    assert scalar.type_name == "UInt64"
    assert scalar.ownership == "value"
    assert scalar.effects == ("may_fail",)


def test_generic_predicates_lower_to_native_enum_tags(
    tmp_path: Path,
) -> None:
    project = Project.create(
        tmp_path / "generic-predicates",
        name="generic_predicates",
    )
    source = project.source_dir / "main.mlo"
    source.write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn make_option() -> Option[Text]:\n"
        '    return Some("value")\n\n'
        "fn make_result() -> Result[Text,AppError]:\n"
        '    return Ok("value")\n\n'
        "export task main(path: Path) -> Result[Text,AppError]:\n"
        "    uses console.write\n"
        "    let option = make_option()\n"
        "    let result = make_result()\n"
        "    if option.is_some() and not option.is_none():\n"
        "        if result.is_ok() and not result.is_err():\n"
        '            console.write("predicates-ok")\n'
        '    return Ok("done")\n',
        encoding="utf-8",
    )
    compilation = compile_project(
        project.root,
        emit_native=True,
        output=tmp_path / "generic-predicates-app",
        require_interface_lock=False,
    )
    assert compilation.native is not None
    generated = compilation.generated.source
    assert all(
        marker in generated
        for marker in (
            "NoneValue_TAG",
            "Some_TAG",
            "Err_TAG",
            "Ok_TAG",
        )
    )
    completed = subprocess.run(
        [compilation.native.binary_path, str(project.root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert completed.stdout == "predicates-ok\n"


def test_owning_unwrap_clones_payload_without_double_free(
    tmp_path: Path,
) -> None:
    project = Project.create(
        tmp_path / "owning-unwrap",
        name="owning_unwrap",
    )
    source = project.source_dir / "main.mlo"
    source.write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export enum Problem:\n"
        "    Message: Text\n\n"
        "fn make_option() -> Option[Text]:\n"
        '    return Some("option")\n\n'
        "fn make_result() -> Result[Text,AppError]:\n"
        '    return Ok("result")\n\n'
        "fn make_error() -> Result[UInt64,Problem]:\n"
        '    return Err(Problem.Message("error"))\n\n'
        "fn take_twice() -> Text:\n"
        "    let option = make_option()\n"
        "    let first: Text = option.unwrap()\n"
        "    let second: Text = option.unwrap()\n"
        "    let result = make_result()\n"
        "    let third: Text = result.unwrap()\n"
        "    let fourth: Text = result.unwrap()\n"
        "    return first\n\n"
        "fn take_error_twice() -> Text:\n"
        "    let result = make_error()\n"
        "    let first: Problem = result.unwrap_err()\n"
        "    let second: Problem = result.unwrap_err()\n"
        "    match first:\n"
        "        case Problem.Message(text):\n"
        "            return text\n\n"
        "export task main(path: Path) -> Result[Text,AppError]:\n"
        "    uses console.write\n"
        "    let value: Text = take_twice()\n"
        "    let error: Text = take_error_twice()\n"
        "    console.write(value)\n"
        "    console.write(error)\n"
        '    return Ok("done")\n',
        encoding="utf-8",
    )
    compilation = compile_project(
        project.root,
        emit_native=True,
        output=tmp_path / "owning-unwrap-app",
        require_interface_lock=False,
    )
    assert compilation.native is not None
    assert "option_unwrap_clone" not in compilation.generated.source
    assert "merlo_clone_Text" in compilation.generated.source
    completed = subprocess.run(
        [compilation.native.binary_path, str(project.root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "optionerror\n"

    compiler = shutil.which("clang")
    if compiler is None:
        pytest.skip("clang is required for the unwrap sanitizer regression")
    generated_c = tmp_path / "owning-unwrap.c"
    sanitized_binary = tmp_path / "owning-unwrap-sanitized"
    generated_c.write_text(compilation.generated.source, encoding="utf-8")
    built = subprocess.run(
        [
            compiler,
            "-std=c11",
            "-O1",
            "-g",
            "-fno-omit-frame-pointer",
            "-fsanitize=address,undefined",
            str(generated_c),
            "-o",
            str(sanitized_binary),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    environment = dict(os.environ)
    environment.update(
        {
            "ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1",
            "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
        }
    )
    sanitized = subprocess.run(
        [sanitized_binary, str(project.root)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert sanitized.returncode == 0, sanitized.stderr
    assert sanitized.stdout == "optionerror\n"


def test_owning_unwrap_can_be_borrowed_inside_short_circuit_expression(
    tmp_path: Path,
) -> None:
    project = Project.create(
        tmp_path / "borrowed-unwrap",
        name="borrowed_unwrap",
    )
    source = project.source_dir / "main.mlo"
    source.write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn matches(message: Text, needle: Option[Text]) -> Bool:\n"
        "    return needle.is_some() and message.contains(needle.unwrap())\n\n"
        "export task main(path: Path) -> Result[Text,AppError]:\n"
        "    uses console.write\n"
        '    let message: Text = "the needle is here"\n'
        '    let needle: Option[Text] = Some("needle")\n'
        "    if matches(message, needle):\n"
        '        console.write("borrowed-ok")\n'
        '    return Ok("done")\n',
        encoding="utf-8",
    )
    compilation = compile_project(
        project.root,
        emit_native=True,
        output=tmp_path / "borrowed-unwrap-app",
        require_interface_lock=False,
    )
    assert compilation.native is not None
    assert "OptionUnwrapWrongVariant" in compilation.generated.source
    completed = subprocess.run(
        [compilation.native.binary_path, str(project.root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "borrowed-ok\n"


def test_unwrap_wrong_variant_traps_instead_of_reading_inactive_union(
    tmp_path: Path,
) -> None:
    project = Project.create(
        tmp_path / "wrong-unwrap",
        name="wrong_unwrap",
    )
    source = project.source_dir / "main.mlo"
    source.write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn invalid() -> UInt64:\n"
        "    let option: Option[UInt64] = None\n"
        "    return option.unwrap()\n\n"
        "export task main(path: Path) -> Result[Text,AppError]:\n"
        "    uses console.write\n"
        "    let value: UInt64 = invalid()\n"
        '    console.write("unreachable")\n'
        '    return Ok("done")\n',
        encoding="utf-8",
    )
    compilation = compile_project(
        project.root,
        emit_native=True,
        output=tmp_path / "wrong-unwrap-app",
        require_interface_lock=False,
    )
    assert compilation.native is not None
    completed = subprocess.run(
        [compilation.native.binary_path, str(project.root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "MerloOwnership:OptionUnwrapWrongVariant" in completed.stderr


@pytest.mark.parametrize(
    "source",
    (
        "fn bad(value: Option[UInt64]) -> Bool:\n"
        "    return value.is_ok()\n",
        "fn bad(value: Result[UInt64,UInt64]) -> Bool:\n"
        "    return value.is_none()\n",
        "fn bad(value: Option[UInt64]) -> UInt64:\n"
        "    return value.unwrap_err()\n",
    ),
)
def test_generic_predicates_reject_wrong_receiver_family(
    source: str,
) -> None:
    with pytest.raises(
        StructuredHIRCompileError,
        match="UnknownCall",
    ):
        compile_structured_hir(source, entry_function="bad")


def test_static_contracts_drive_backend_primitive_manifest(
    tmp_path: Path,
) -> None:
    project = Project.create(tmp_path / "contract-graph", name="contract_graph")
    (project.source_dir / "main.mlo").write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    let input: Bytes = console.read()\n"
        "    let copied: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let builder: TextBuilder = TextBuilder.new()\n"
        "    builder.append_text(copied)\n"
        "    Ok(builder.finish())\n",
        encoding="utf-8",
    )
    generated = compile_project(
        project.root,
        require_interface_lock=False,
    ).generated
    manifest = {
        item["name"]: item
        for item in generated.primitive_manifest
    }
    copied = manifest["Text.from_bytes"]
    assert copied["type_signature"] == (
        "fn(BytesView, UInt64, UInt64) -> Text"
    )
    assert copied["may_allocate"] is True
    assert copied["may_copy"] is True
    assert copied["may_fail"] is True
    builder = manifest["TextBuilder.new"]
    assert builder["type_signature"] == "fn() -> TextBuilder"
    assert builder["may_allocate"] is True
    assert builder["may_copy"] is False
    assert builder["may_fail"] is True


@pytest.mark.parametrize("name", tuple(EXPECTED))
def test_arity_is_contractual(name: str) -> None:
    signature = intrinsic_signature(name)
    assert signature is not None
    assert signature.arity == len(signature.parameters)


def test_result_intrinsic_cannot_be_returned_as_its_ok_type() -> None:
    source = (
        "task bad(path: Path) -> Bytes:\n"
        "    uses fs.read\n"
        "    return fs.read(path)\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="ReturnTypeMismatch"):
        compile_structured_hir(source, entry_function="bad")


def test_removed_tcp_alias_is_rejected() -> None:
    source = (
        "task bad() -> UInt64:\n"
        "    uses network.tcp\n"
        "    return tcp.connect()\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="UnknownIntrinsic"):
        compile_structured_hir(source, entry_function="bad")


def test_file_handles_are_mode_specific() -> None:
    writer_to_reader = (
        "task bad(path: Path) -> Result[Bytes,AppError]:\n"
        "    uses fs.read, fs.write\n"
        "    let output: FileWriter = fs.open_write(path)?\n"
        "    return fs.read_chunk(output, 1)\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="IntrinsicTypeMismatch"):
        compile_structured_hir(writer_to_reader, entry_function="bad")

    reader_to_writer = (
        "task bad(path: Path, data: BytesView) -> Result[Unit,AppError]:\n"
        "    uses fs.read, fs.write\n"
        "    let input: FileReader = fs.open_read(path)?\n"
        "    return fs.write_chunk(input, data)\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="IntrinsicTypeMismatch"):
        compile_structured_hir(reader_to_writer, entry_function="bad")


def test_file_close_effect_matches_handle_mode() -> None:
    assert intrinsic_signature("fs.close_read").effect == "fs.read"  # type: ignore[union-attr]
    assert intrinsic_signature("fs.close_write").effect == "fs.write"  # type: ignore[union-attr]
    assert intrinsic_signature("fs.close") is None


@pytest.mark.parametrize(
    "source",
    (
        "task bad(path: Path, data: BytesView) -> Result[Unit,AppError]:\n"
        "    uses fs.write\n"
        "    let file: FileWriter = fs.open_write(path)?\n"
        "    fs.close_write(file)?\n"
        "    return fs.write_chunk(file, data)?\n",
        "task bad(path: Path) -> Result[Bytes,AppError]:\n"
        "    uses fs.read\n"
        "    let file: FileReader = fs.open_read(path)?\n"
        "    fs.close_read(file)?\n"
        "    return fs.read_chunk(file, 1)?\n",
    ),
)
def test_use_after_explicit_resource_close_is_rejected(source: str) -> None:
    with pytest.raises(StructuredHIRCompileError, match="UseAfterMove"):
        compile_structured_hir(source, entry_function="bad")


def test_consuming_instance_method_invalidates_receiver() -> None:
    source = (
        "task bad() -> Text:\n"
        "    let builder: TextBuilder = TextBuilder.new()\n"
        "    let text: Text = builder.finish()\n"
        "    builder.append_text(\"use after finish\")\n"
        "    return text\n"
    )

    with pytest.raises(StructuredHIRCompileError, match="UseAfterMove: builder"):
        compile_structured_hir(source, entry_function="bad")
