from __future__ import annotations

from dataclasses import replace

import pytest

from merlo.mir_ownership import (
    MIR_OWNERSHIP_CONTRACT,
    MIR_OWNERSHIP_OPERATIONS,
    MIR_OWNERSHIP_SCHEMA_VERSION,
    MIROwnershipKind,
    MIROwnershipVerificationError,
    verify_ownership_program,
)
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import (
    GENERAL_MIR_SCHEMA_VERSION,
    GeneralPerformanceMIR,
    lower_rir_to_performance_mir,
    optimize_general_mir,
    verify_general_mir,
)
from merlo.structured_hir_v2 import (
    compile_canonical_hir,
    compile_structured_hir,
)


SOURCE = (
    "fn main(input: BytesView) -> UInt64:\n"
    "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
    "    let view: TextView = text.as_view()\n"
    "    let size: UInt64 = view.len()\n"
    "    return size\n"
)


def _lower(source: str = SOURCE):
    hir = compile_structured_hir(source, path="ownership-ssa.mlo")
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    return representation, mir


def _operations(mir: GeneralPerformanceMIR):
    return [
        operation
        for function in mir.ownership.functions
        for block in function.blocks
        for operation in block.operations
    ]


def _replace_block_operations(mir: GeneralPerformanceMIR, block_id: str, operations):
    ownership_function = mir.ownership.functions[0]
    blocks = tuple(
        replace(block, operations=tuple(operations))
        if block.id == block_id
        else block
        for block in ownership_function.blocks
    )
    return replace(
        mir.ownership,
        functions=(replace(ownership_function, blocks=blocks),),
    )


def test_mir_v4_carries_closed_ownership_ssa_v1_contract() -> None:
    _representation, mir = _lower()

    assert GENERAL_MIR_SCHEMA_VERSION == 5
    assert mir.schema_version == 5
    assert mir.ownership.schema_version == MIR_OWNERSHIP_SCHEMA_VERSION == 2
    assert mir.ownership.contract == MIR_OWNERSHIP_CONTRACT
    assert {item.value for item in MIROwnershipKind} == {
        "Trivial",
        "Owned",
        "Guaranteed",
        "Unowned",
    }
    assert MIR_OWNERSHIP_OPERATIONS == frozenset(
        {
            "move_value",
            "copy_value",
            "destroy_value",
            "begin_borrow",
            "end_borrow",
            "load_copy",
            "load_take",
            "store_init",
            "store_assign",
            "storage_live",
            "storage_dead",
        }
    )

    operations = _operations(mir)
    assert {item.kind for item in operations} >= {
        MIROwnershipKind.TRIVIAL,
        MIROwnershipKind.OWNED,
        MIROwnershipKind.GUARANTEED,
    }
    assert {item.op for item in operations} >= {
        "storage_live",
        "store_init",
        "load_copy",
        "begin_borrow",
        "end_borrow",
        "destroy_value",
        "storage_dead",
    }
    verify_general_mir(mir, _representation)


def test_ownership_metadata_is_required_and_round_trips_canonically() -> None:
    _representation, mir = _lower()
    payload = mir.to_dict()

    assert payload["ownership"]["contract"] == MIR_OWNERSHIP_CONTRACT
    decoded = GeneralPerformanceMIR.from_json(mir.to_json())
    assert decoded.to_json() == mir.to_json()

    del payload["ownership"]
    with pytest.raises(ValueError, match="MIR schema mismatch"):
        GeneralPerformanceMIR.from_dict(payload)


def test_optimizer_rebuilds_and_verifies_ownership_after_transform() -> None:
    _representation, mir = _lower()

    optimized = optimize_general_mir(mir)

    verify_general_mir(optimized)
    assert optimized.ownership == optimized.expected_ownership()

def test_ownership_authority_digest_binds_type_kinds() -> None:
    _representation, mir = _lower()
    text_type_id = next(
        type_id
        for type_id, _kind in mir.ownership.type_kinds
        if mir.type_arena.canonical(type_id) == "Text"
    )
    assert dict(mir.ownership.type_kinds)[text_type_id] is MIROwnershipKind.OWNED
    type_kinds = tuple(
        (
            type_id,
            MIROwnershipKind.TRIVIAL
            if type_id == text_type_id
            else kind,
        )
        for type_id, kind in mir.ownership.type_kinds
    )
    mutated_ownership = replace(mir.ownership, type_kinds=type_kinds)

    with pytest.raises(
        ValueError,
        match="General Performance MIR ownership authority digest mismatch",
    ):
        replace(mir, ownership=mutated_ownership)


def test_opaque_nominal_type_requires_drop_plan_authority() -> None:
    representation, mir = _lower(
        "record Holder:\n"
        "    text: Text\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return input.len()\n"
    )
    holder_type_id = next(
        descriptor.type_id
        for descriptor in representation.descriptors
        if descriptor.name == "Holder"
    )
    assert holder_type_id is not None
    bindings = tuple(
        binding
        for binding in mir.drop_plan_bindings
        if binding[0] != holder_type_id
    )

    with pytest.raises(
        MIROwnershipVerificationError,
        match="MIROwnershipTypeAuthorityMissing",
    ):
        verify_ownership_program(
            mir.functions,
            mir.type_arena,
            bindings,
            mir.ownership,
        )



def test_drop_plan_action_is_bound_to_content_address() -> None:
    representation, mir = _lower(
        "record Holder:\n"
        "    text: Text\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return input.len()\n"
    )
    holder_type_id = next(
        descriptor.type_id
        for descriptor in representation.descriptors
        if descriptor.name == "Holder"
    )
    bindings = tuple(
        (
            type_id,
            drop_plan_id,
            "trivial" if type_id == holder_type_id else action,
        )
        for type_id, drop_plan_id, action
        in mir.drop_plan_bindings
    )

    with pytest.raises(
        ValueError,
        match="trivial MIR drop binding identity mismatch",
    ):
        replace(mir, drop_plan_bindings=bindings)

def test_guaranteed_view_from_local_owner_cannot_return() -> None:
    _representation, mir = _lower()
    ownership_function = mir.ownership.functions[0]
    borrowed_operation = next(
        operation
        for block in ownership_function.blocks
        for operation in block.operations
        if (
            operation.op == "copy_value"
            and operation.kind is MIROwnershipKind.GUARANTEED
            and mir.type_arena.canonical(operation.type_id) == "TextView"
            and operation.base is not None
            and not operation.base.startswith(("caller::", "static::"))
        )
    )
    borrowed_block = next(
        block for block in ownership_function.blocks if borrowed_operation in block.operations
    )
    mutated = _replace_block_operations(
        mir,
        borrowed_block.id,
        [
            replace(operation, target="return")
            if operation is borrowed_operation
            else operation
            for operation in borrowed_block.operations
        ],
    )

    with pytest.raises(MIROwnershipVerificationError, match="MIROwnershipBorrowEscapes"):
        verify_ownership_program(
            mir.functions,
            mir.type_arena,
            mir.drop_plan_bindings,
            mutated,
        )


def test_unknown_ffi_ownership_policy_is_rejected_at_mir_boundary() -> None:
    _representation, mir = _lower()
    instruction = next(
        instruction
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
        if instruction.result is not None and not instruction.operands
    )
    attributes = dict(instruction.attributes)
    attributes.update(
        {
            "ffi": True,
            "parameter_ownership": (),
            "result_ownership": "unknown",
        }
    )
    mutated_instruction = replace(
        instruction,
        attributes=tuple(sorted(attributes.items())),
    )
    functions = tuple(
        replace(
            function,
            blocks=tuple(
                replace(
                    block,
                    instructions=tuple(
                        mutated_instruction if item is instruction else item
                        for item in block.instructions
                    ),
                )
                for block in function.blocks
            ),
        )
        for function in mir.functions
    )

    with pytest.raises(
        MIROwnershipVerificationError,
        match="MIROwnershipMissingFFIOwnership",
    ):
        replace(mir, functions=functions)


def test_duplicate_consume_is_rejected_by_ownership_cfg_verifier() -> None:
    _representation, mir = _lower()
    ownership_function = mir.ownership.functions[0]
    block = next(
        block
        for block in ownership_function.blocks
        if any(operation.op == "destroy_value" for operation in block.operations)
    )
    operations = list(block.operations)
    consume_index = next(
        index
        for index, operation in enumerate(operations)
        if operation.op == "destroy_value"
    )
    operations.insert(consume_index + 1, operations[consume_index])
    mutated = _replace_block_operations(mir, block.id, operations)

    with pytest.raises(MIROwnershipVerificationError, match="MIROwnershipDoubleConsume"):
        verify_ownership_program(mir.functions, mir.type_arena, mir.drop_plan_bindings, mutated)


def test_removed_destroy_is_rejected_as_owned_leak() -> None:
    _representation, mir = _lower()
    ownership_function = mir.ownership.functions[0]
    block = next(
        block
        for block in ownership_function.blocks
        if any(operation.op == "destroy_value" for operation in block.operations)
    )
    operations = [
        operation
        for operation in block.operations
        if operation.op != "destroy_value"
    ]
    mutated = _replace_block_operations(mir, block.id, operations)

    with pytest.raises(MIROwnershipVerificationError, match="MIROwnershipOwnedValueLeak"):
        verify_ownership_program(mir.functions, mir.type_arena, mir.drop_plan_bindings, mutated)


def test_ending_borrow_before_last_use_is_rejected() -> None:
    _representation, mir = _lower()
    ownership_function = mir.ownership.functions[0]
    block = next(
        block
        for block in ownership_function.blocks
        if any(operation.op == "begin_borrow" for operation in block.operations)
        and any(operation.op == "end_borrow" for operation in block.operations)
    )
    operations = list(block.operations)
    end_index = next(
        index
        for index, operation in enumerate(operations)
        if operation.op == "end_borrow"
        and operation.value is not None
        and any(
            prior.value == operation.value
            and prior.op in {"copy_value", "move_value"}
            for prior in operations[:index]
        )
    )
    borrow_value = operations[end_index].value
    use_index = next(
        index
        for index, operation in enumerate(operations)
        if index < end_index
        and operation.value == borrow_value
        and operation.op in {"copy_value", "move_value"}
    )
    end = replace(
        operations.pop(end_index),
        instruction_id=operations[use_index].instruction_id,
        point="before",
    )
    operations.insert(use_index, end)
    mutated = _replace_block_operations(mir, block.id, operations)

    with pytest.raises(MIROwnershipVerificationError, match="MIROwnershipBorrowUseAfterEnd"):
        verify_ownership_program(mir.functions, mir.type_arena, mir.drop_plan_bindings, mutated)


def test_destroying_base_before_end_borrow_is_rejected() -> None:
    _representation, mir = _lower()
    ownership_function = mir.ownership.functions[0]
    block = next(
        block
        for block in ownership_function.blocks
        if any(operation.op == "end_borrow" for operation in block.operations)
        and any(operation.op == "destroy_value" for operation in block.operations)
    )
    operations = list(block.operations)
    end_index = next(
        index
        for index, operation in enumerate(operations)
        if operation.op == "end_borrow"
        and operation.base is not None
        and any(
            later.op == "destroy_value"
            and later.place == operation.base
            for later in operations[index + 1 :]
        )
    )
    base = operations[end_index].base
    destroy_index = next(
        index
        for index, operation in enumerate(operations)
        if operation.op == "destroy_value"
        and operation.place == base
        and index > end_index
    )
    destroy = replace(
        operations.pop(destroy_index),
        instruction_id=operations[end_index].instruction_id,
        point="before",
    )
    operations.insert(end_index, destroy)
    mutated = _replace_block_operations(mir, block.id, operations)

    with pytest.raises(
        MIROwnershipVerificationError,
        match="MIROwnershipBaseDestroyedDuringBorrow",
    ):
        verify_ownership_program(mir.functions, mir.type_arena, mir.drop_plan_bindings, mutated)


def test_erased_or_changed_ownership_metadata_fails_integrated_verification() -> None:
    _representation, mir = _lower()
    ownership_function = mir.ownership.functions[0]
    first_block = ownership_function.blocks[0]
    mutated_block = replace(first_block, operations=first_block.operations[1:])
    mutated = replace(
        mir.ownership,
        functions=(
            replace(
                ownership_function,
                blocks=(mutated_block, *ownership_function.blocks[1:]),
            ),
        ),
    )

    with pytest.raises(
        MIROwnershipVerificationError,
        match="MIROwnershipMetadataMismatch",
    ):
        replace(mir, ownership=mutated)

def test_static_function_reference_is_non_owning_but_captured_closure_is_owned() -> None:
    static_source = (
        "fn increment(value: UInt64) -> UInt64:\n"
        "    return value + 1\n"
        "fn apply(callback: Fn[UInt64,UInt64], value: UInt64) -> UInt64:\n"
        "    return callback(value)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return apply(increment, input.len())\n"
    )
    captured_source = (
        "fn length_above(text: Text) -> Fn[UInt64,Bool]:\n"
        "    limit => text.len() > limit\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let predicate: Fn[UInt64,Bool] = length_above(text)\n"
        "    if predicate(2):\n"
        "        return 1\n"
        "    return 0\n"
    )
    _representation, static_mir = _lower(static_source)
    captured_hir = compile_canonical_hir(
        elaborate_surface(
            parse_surface(
                captured_source,
                path="ownership-closure.mlo",
            )
        ).canonical
    )
    captured_representation = lower_structured_hir_to_rir(
        captured_hir
    )
    captured_mir = lower_rir_to_performance_mir(
        captured_hir,
        captured_representation,
    )

    static_reference = [
        operation
        for operation in _operations(static_mir)
        if (
            operation.op == "load_copy"
            and operation.place == "increment"
        )
    ]
    assert len(static_reference) == 1
    assert static_reference[0].kind is MIROwnershipKind.GUARANTEED
    assert static_reference[0].base == "static::increment"

    captured_closure_drops = [
        operation
        for operation in _operations(captured_mir)
        if (
            operation.op == "destroy_value"
            and operation.place == "predicate"
        )
    ]
    return_blocks = {
        block.id
        for function in captured_mir.functions
        if function.name == "main"
        for block in function.blocks
        if block.terminator.kind == "return"
    }
    assert {
        operation.block_id
        for operation in captured_closure_drops
    } == return_blocks
    assert all(
        operation.kind is MIROwnershipKind.OWNED
        for operation in captured_closure_drops
    )


def test_branch_local_owned_value_is_destroyed_only_on_its_branch() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    if input.len() > 0:\n"
        "        let branch_text: Text = Text.from_bytes(input, 0, input.len())\n"
        "        branch_text.len()\n"
        "    else:\n"
        "        input.len()\n"
        "    return input.len()\n"
    )
    _representation, mir = _lower(source)

    main = next(function for function in mir.functions if function.name == "main")
    main_ownership = next(
        function
        for function in mir.ownership.functions
        if function.name == "main"
    )
    branch_local_blocks = {
        block.id
        for block in main.blocks
        if any(
            instruction.op == "store_local"
            and instruction.attribute_map.get("name") == "branch_text"
            for instruction in block.instructions
        )
    }
    assert len(branch_local_blocks) == 1
    destroy_blocks = {
        block.id
        for block in main_ownership.blocks
        if any(
            operation.op == "destroy_value"
            and operation.place == "branch_text"
            for operation in block.operations
        )
    }
    assert destroy_blocks == branch_local_blocks


def test_temporary_owned_receiver_is_destroyed_after_derived_borrow_ends() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    return Text.from_bytes(input, 0, input.len()).len()\n"
    )
    _representation, mir = _lower(source)

    ownership_function = mir.ownership.functions[0]
    operations = [
        operation
        for block in ownership_function.blocks
        for operation in block.operations
    ]
    destroy_index, destroy = next(
        (index, operation)
        for index, operation in enumerate(operations)
        if operation.op == "destroy_value" and operation.value is not None
    )
    borrow_end_index, borrow_end = next(
        (index, operation)
        for index, operation in enumerate(operations)
        if operation.op == "end_borrow"
        and operation.base == destroy.value
    )
    assert borrow_end.kind is MIROwnershipKind.GUARANTEED
    assert destroy.kind is MIROwnershipKind.OWNED
    assert borrow_end_index < destroy_index
