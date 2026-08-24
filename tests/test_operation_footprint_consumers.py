from __future__ import annotations

from dataclasses import replace

import pytest

from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import (
    MIRVerificationError,
    lower_rir_to_performance_mir,
    verify_general_mir,
)
from merlo.structured_hir_v2 import compile_structured_hir


def _layers(source: str, *, entry_function: str = "main"):
    hir = compile_structured_hir(source, entry_function=entry_function)
    rir = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, rir)
    return hir, mir


def test_vec_get_footprint_is_identical_in_hir_and_mir() -> None:
    hir, mir = _layers(
        "fn main(values: Vec[Byte], index: UInt64) -> Byte:\n"
        "    return values.get(index)\n"
    )
    hir_node = next(
        node
        for node in hir.function("main").walk()
        if node.kind == "VecOperation"
    )
    mir_instruction = next(
        instruction
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
        if instruction.attribute_map.get("contract_symbol") == "Vec[Byte].get"
    )
    assert hir_node.attribute_map["operation_footprint"] == (
        mir_instruction.attribute_map["operation_footprint"]
    )


def test_fs_read_chunk_footprint_carries_state_write_and_blocking() -> None:
    hir, mir = _layers(
        "enum AppError:\n"
        "    FileOpen\n"
        "task main(path: Path) -> Result[Bytes,AppError]:\n"
        "    uses fs.read\n"
        "    let file: FileReader = fs.open_read(path)?\n"
        "    return fs.read_chunk(file, 1)?\n",
    )
    hir_node = next(
        node
        for node in hir.function("main").walk()
        if node.attribute_map.get("contract_symbol") == "fs.read_chunk"
    )
    footprint = hir_node.attribute_map["operation_footprint"]
    assert footprint["write_places"] == ("parameter[0].state",)
    assert footprint["blocking"] is True
    mir_instruction = next(
        instruction
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
        if instruction.attribute_map.get("contract_symbol") == "fs.read_chunk"
    )
    assert mir_instruction.attribute_map["operation_footprint"] == footprint


def test_missing_known_mir_footprint_is_rejected() -> None:
    _hir, mir = _layers(
        "fn main(values: Vec[Byte], index: UInt64) -> Byte:\n"
        "    return values.get(index)\n"
    )
    function = mir.functions[0]
    block = function.blocks[0]
    index = next(
        index
        for index, instruction in enumerate(block.instructions)
        if instruction.attribute_map.get("contract_symbol") == "Vec[Byte].get"
    )
    instruction = block.instructions[index]
    tampered = replace(
        instruction,
        attributes=tuple(
            (key, value)
            for key, value in instruction.attributes
            if key != "operation_footprint"
        ),
    )
    tampered_block = replace(
        block,
        instructions=block.instructions[:index]
        + (tampered,)
        + block.instructions[index + 1 :],
    )
    object.__setattr__(mir, "functions", (replace(function, blocks=(tampered_block,)),))
    with pytest.raises(MIRVerificationError, match="operation footprint mismatch"):
        verify_general_mir(mir)


def test_missing_known_rir_footprint_is_rejected() -> None:
    hir = compile_structured_hir(
        "fn main(values: Vec[Byte], index: UInt64) -> Byte:\n"
        "    return values.get(index)\n"
    )
    rir = lower_structured_hir_to_rir(hir)
    function = rir.functions[0]

    def strip_footprint(operation):
        if operation.attribute_map.get("contract_symbol") == "Vec[Byte].get":
            return replace(
                operation,
                attributes=tuple(
                    (key, value)
                    for key, value in operation.attributes
                    if key != "operation_footprint"
                ),
            )
        return replace(
            operation,
            children=tuple(strip_footprint(child) for child in operation.children),
        )

    with pytest.raises(ValueError, match="operation footprint mismatch"):
        replace(
            rir,
            functions=(
                replace(
                    function,
                    operations=tuple(
                        strip_footprint(operation)
                        for operation in function.operations
                    ),
                ),
            ),
        )
