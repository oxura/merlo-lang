from __future__ import annotations

import pytest

from merlo.ffi import (
    ExternFunction,
    FFICompileError,
    ForeignPointerPolicy,
    parse_ffi_declarations,
    validate_ffi,
)


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
