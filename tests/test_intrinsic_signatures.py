from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from merlo.intrinsics import (
    INTRINSIC_EFFECTS,
    INTRINSIC_SIGNATURES,
    contextual_result_type,
    intrinsic_signature,
)


EXPECTED = {
    "console.read": ((), "Bytes", "console.read"),
    "console.write": (("TextView",), "Unit", "console.write"),
    "fs.open_read": (("Path",), "Result[FileReader,AppError]", "fs.read"),
    "fs.read": (("Path",), "Result[Bytes,AppError]", "fs.read"),
    "fs.read_text": (("Path",), "Result[Text,AppError]", "fs.read"),
    "fs.read_chunk": (("FileReader", "UInt64"), "Result[Bytes,AppError]", "fs.read"),
    "fs.open_write": (("Path",), "Result[FileReader,AppError]", "fs.write"),
    "fs.write": (("Path", "BytesView"), "Result[Unit,AppError]", "fs.write"),
    "fs.write_text": (("Path", "TextView"), "Result[Unit,AppError]", "fs.write"),
    "fs.write_chunk": (("FileReader", "BytesView"), "Result[Unit,AppError]", "fs.write"),
    "fs.close": (("FileReader",), "Result[Unit,AppError]", "fs.write"),
    "env.read": (("Text",), "Text", "env.read"),
    "env.get": (("Text",), "Text", "env.read"),
    "clock.now": ((), "UInt64", "clock.now"),
    "random.read": (("UInt64",), "Bytes", "random.read"),
    "process.args": ((), "UInt64", "process.args"),
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


@pytest.mark.parametrize("name", tuple(EXPECTED))
def test_arity_is_contractual(name: str) -> None:
    signature = intrinsic_signature(name)
    assert signature is not None
    assert signature.arity == len(signature.parameters)
