from __future__ import annotations

import pytest

from merlo.ffi import (
    ExternFunction,
    FFICompileError,
    FFIProgram,
    ForeignPointerPolicy,
    ReprCRecord,
    parse_ffi_declarations,
    validate_ffi,
)
from merlo.type_arena import TypeContextBuilder


def test_extern_c_pointer_policy_and_prototype_are_deterministic() -> None:
    program = parse_ffi_declarations(
        'extern "C" fn write(fd: Int32, buf: RawPointer[UInt8] {read, borrowed}, count: UInt64) -> Int64 effects [console.write]\n'
    )
    function = program.extern_functions[0]
    assert function.prototype == "extern int64_t write(int32_t fd, const uint8_t * buf, uint64_t count);"
    assert function.to_dict() == function.to_dict()
    assert function.effects == ("console.write",)


def test_foreign_owned_pointer_requires_destructor() -> None:
    with pytest.raises(FFICompileError, match="ForeignOwnershipUndeclared"):
        ForeignPointerPolicy("buffer", ownership="owned")


def test_repr_c_layout_and_fixed_width_validation() -> None:
    program = parse_ffi_declarations(
        "repr(C) record Pair:\n"
        "    tag: UInt8\n"
        "    value: UInt32\n"
    )
    record = program.repr_c_records[0]
    assert [(field.name, field.offset) for field in record.fields] == [("tag", 0), ("value", 4)]
    assert record.size == 8 and record.alignment == 4
    with pytest.raises(FFICompileError, match="FixedWidthABIRequired"):
        parse_ffi_declarations('extern "C" fn bad(value: Int) -> UInt64\n')
    with pytest.raises(FFICompileError, match="FixedWidthABIRequired"):
        parse_ffi_declarations('extern "C" fn bad(value: app.User) -> UInt64\n')


def test_unsafe_operations_require_explicit_non_propagating_block() -> None:
    with pytest.raises(FFICompileError, match="UnsafeOperationRequiresBlock"):
        validate_ffi("fn safe() -> Unit:\n    return ptr_read(p)\n")
    program = validate_ffi("fn wrapper() -> Unit:\n    unsafe:\n        ptr_read(p)\n")
    assert len(program.unsafe_operations) == 1
    assert program.unsafe_operations[0].propagates is False


def test_foreign_result_type_and_effects_are_preserved() -> None:
    function = ExternFunction("read", (), "Result[Int64,Errno]", ("fs.read",))
    assert function.return_type == "Result[Int64,Errno]"
    assert function.effects == ("fs.read",)


def test_ffi_types_bind_once_and_serialize_stored_identities() -> None:
    program = parse_ffi_declarations(
        'extern "C" fn write(buf: RawPointer[UInt8] {read, borrowed}) -> Int64\n'
        "repr(C) record Pair:\n"
        "    value: UInt32\n"
    )
    assert program.types_bound is False
    arena = TypeContextBuilder()
    arena.intern_many(("RawPointer[UInt8]", "UInt8", "Int64", "UInt32"))
    bound = program.bind_types(arena)
    parameter = bound.extern_functions[0].parameters[0]
    assert bound.types_bound is True
    assert parameter.type_id == arena.type_id("RawPointer[UInt8]")
    assert parameter.pointer is not None
    assert parameter.pointer.pointee_type_id == arena.type_id("UInt8")
    assert bound.extern_functions[0].return_type_id == arena.type_id("Int64")
    assert bound.repr_c_records[0].fields[0].type_id == arena.type_id("UInt32")
    assert bound.to_dict()["extern_functions"][0]["parameters"][0]["type_id"]
    with pytest.raises(FFICompileError, match="FFITypesAlreadyBound"):
        bound.bind_types(arena)


def test_bound_ffi_json_and_empty_record_lifecycle_roundtrip() -> None:
    arena = TypeContextBuilder()
    empty = FFIProgram(
        repr_c_records=(ReprCRecord("Empty", (), 0, 1),),
    ).bind_types(arena)

    restored = FFIProgram.from_json(empty.to_json())

    assert restored.types_bound is True
    assert restored == empty
