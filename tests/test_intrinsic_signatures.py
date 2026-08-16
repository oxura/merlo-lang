from __future__ import annotations

from dataclasses import FrozenInstanceError

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
