"""Deterministic, measurable Performance MIR optimization passes."""

from __future__ import annotations

import json
import math
import struct
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .performance_mir import (
    MIRBasicBlock,
    MIRFunction,
    MIRInstruction,
    PassSnapshot,
    PassStatistics,
    PerformanceMIR,
    scalar_layout,
)

PERFORMANCE_MEMORY_MODEL_VERSION = 5

PassFunction = Callable[[PerformanceMIR], tuple[PerformanceMIR, PassStatistics]]


def _replace_functions(mir: PerformanceMIR, functions: Iterable[MIRFunction]) -> PerformanceMIR:
    return replace(mir, functions=tuple(functions))


def _replace_blocks(function: MIRFunction, blocks: Iterable[MIRBasicBlock]) -> MIRFunction:
    return replace(function, blocks=tuple(blocks))


def _stats(name: str, before: PerformanceMIR, after: PerformanceMIR, **values: int) -> PassStatistics:
    removed = max(0, before.instruction_count - after.instruction_count)
    return PassStatistics(
        name,
        before.instruction_count,
        after.instruction_count,
        instructions_removed=values.pop("instructions_removed", removed),
        **values,
    )


def _constant_value(operator: str, values: tuple[Any, ...]) -> Any:
    if len(values) == 1:
        value = values[0]
        return {
            "neg": lambda: -value,
            "not": lambda: not value,
            "bit_not": lambda: ~value,
        }[operator]()
    left, right = values

    def trunc_div() -> int:
        if right == 0:
            raise ZeroDivisionError
        quotient = abs(left) // abs(right)
        return -quotient if (left < 0) != (right < 0) else quotient

    operations = {
        "add": lambda: left + right,
        "sub": lambda: left - right,
        "mul": lambda: left * right,
        "div": lambda: (
            trunc_div()
            if isinstance(left, int) and isinstance(right, int)
            else left / right
        ),
        "mod": lambda: (
            left - trunc_div() * right
            if isinstance(left, int) and isinstance(right, int)
            else left % right
        ),
        "bit_and": lambda: left & right,
        "bit_or": lambda: left | right,
        "bit_xor": lambda: left ^ right,
        "shift_left": lambda: left << (right & 63),
        "shift_right": lambda: left >> (right & 63),
        "and": lambda: bool(left and right),
        "or": lambda: bool(left or right),
        "eq": lambda: left == right,
        "ne": lambda: left != right,
        "lt": lambda: left < right,
        "le": lambda: left <= right,
        "gt": lambda: left > right,
        "ge": lambda: left >= right,
    }
    return operations[operator]()


def _wrap_constant(value: Any, instruction: MIRInstruction) -> Any:
    type_ = instruction.type
    if type_ is None:
        return value
    if type_.kind == "uint":
        return int(value) & ((1 << (type_.bits or 64)) - 1)
    if type_.kind == "int":
        bits = type_.bits or 64
        raw = int(value) & ((1 << bits) - 1)
        return raw - (1 << bits) if raw >= (1 << (bits - 1)) else raw
    if type_.kind == "bool":
        return bool(value)
    if type_.kind == "float":
        result = float(value)
        if type_.bits == 32:
            try:
                result = struct.unpack("!f", struct.pack("!f", result))[0]
            except OverflowError:
                result = math.copysign(math.inf, result)
        return result
    return value


def constant_folding(mir: PerformanceMIR) -> tuple[PerformanceMIR, PassStatistics]:
    folded = 0
    functions = []
    for function in mir.functions:
        constants: dict[str, Any] = {}
        blocks = []
        for block in function.blocks:
            instructions = []
            for instruction in block.instructions:
                replacement = instruction
                if instruction.op == "const" and instruction.result is not None:
                    constants[instruction.result] = instruction.attribute_map["value"]
                elif (
                    instruction.op in {"binary", "unary", "compare"}
                    and instruction.result is not None
                    and all(operand in constants for operand in instruction.operands)
                ):
                    operator = instruction.attribute_map["operator"]
                    values = tuple(constants[operand] for operand in instruction.operands)
                    try:
                        value = _constant_value(operator, values)
                        value = _wrap_constant(value, instruction)
                        if isinstance(value, float) and not math.isfinite(value):
                            raise ArithmeticError("non-finite fold")
                    except (ArithmeticError, KeyError, TypeError, ValueError, ZeroDivisionError):
                        pass
                    else:
                        replacement = instruction.replace(
                            op="const", operands=(), attributes=(("value", value),)
                        )
                        constants[instruction.result] = value
                        folded += 1
                instructions.append(replacement)
            blocks.append(replace(block, instructions=tuple(instructions)))
        functions.append(_replace_blocks(function, blocks))
    after = _replace_functions(mir, functions)
    return after, _stats("constant_folding", mir, after, instructions_removed=0)


def dead_code_elimination(mir: PerformanceMIR) -> tuple[PerformanceMIR, PassStatistics]:
    functions = []
    for function in mir.functions:
        blocks = list(function.blocks)
        changed = True
        while changed:
            used = {
                operand
                for block in blocks
                for instruction in block.instructions
                for operand in instruction.operands
            }
            used.update(
                value
                for block in blocks
                for value in (block.terminator.condition, block.terminator.value)
                if value is not None
            )
            changed = False
            next_blocks = []
            for block in blocks:
                instructions = []
                for instruction in block.instructions:
                    if (
                        instruction.result is not None
                        and instruction.result not in used
                        and not instruction.has_side_effect
                    ):
                        changed = True
                        continue
                    instructions.append(instruction)
                next_blocks.append(replace(block, instructions=tuple(instructions)))
            blocks = next_blocks
        functions.append(_replace_blocks(function, blocks))
    after = _replace_functions(mir, functions)
    return after, _stats("dead_code_elimination", mir, after)


def monomorphization(mir: PerformanceMIR) -> tuple[PerformanceMIR, PassStatistics]:
    specializations: set[tuple[str, str, str]] = set()
    functions = []
    for function in mir.functions:
        blocks = []
        for block in function.blocks:
            instructions = []
            for instruction in block.instructions:
                attributes = instruction.attribute_map
                generic = attributes.pop("generic", None)
                if instruction.op.startswith("collection_") and generic:
                    suffix = str(generic).lower().replace("[", "_").replace("]", "").replace(";", "_")
                    specializations.add((instruction.op, str(generic), suffix))
                    attributes["specialized_type"] = generic
                    instruction = instruction.replace(
                        op=f"{instruction.op}_{suffix}", attributes=attributes
                    )
                instructions.append(instruction)
            blocks.append(replace(block, instructions=tuple(instructions)))
        functions.append(_replace_blocks(function, blocks))
    after = _replace_functions(mir, functions)
    return after, _stats(
        "monomorphization",
        mir,
        after,
        instructions_removed=0,
        specializations_created=len(specializations),
    )


def collection_fusion(mir: PerformanceMIR) -> tuple[PerformanceMIR, PassStatistics]:
    loops_fused = 0
    allocations_removed = 0
    functions = []
    for function in mir.functions:
        blocks = []
        for block in function.blocks:
            producers = {
                instruction.result: instruction
                for instruction in block.instructions
                if instruction.result is not None
            }
            remove_ids: set[str] = set()
            replacements: dict[str, MIRInstruction] = {}
            for fold in block.instructions:
                if not fold.op.startswith("collection_fold") or len(fold.operands) != 2:
                    continue
                chain_value = fold.operands[0]
                filter_instruction = producers.get(chain_value)
                map_instruction = None
                if filter_instruction and filter_instruction.op.startswith("collection_filter"):
                    map_instruction = producers.get(filter_instruction.operands[0])
                elif filter_instruction and filter_instruction.op.startswith("collection_map"):
                    map_instruction = filter_instruction
                    filter_instruction = None
                if map_instruction is None or not map_instruction.op.startswith("collection_map"):
                    continue
                base = map_instruction.operands[0]
                attributes = {
                    "map_function": map_instruction.attribute_map["function"],
                    "fold_function": fold.attribute_map["function"],
                    "specialized_type": fold.attribute_map.get("specialized_type", fold.type.name if fold.type else "unknown"),
                }
                if filter_instruction is not None:
                    attributes["filter_function"] = filter_instruction.attribute_map["function"]
                replacements[fold.id] = fold.replace(
                    op="fused_collection_loop",
                    operands=(base, fold.operands[1]),
                    attributes=attributes,
                )
                for item in (map_instruction, filter_instruction):
                    if item is None:
                        continue
                    remove_ids.add(item.id)
                    if len(item.operands) > 1:
                        allocation = producers.get(item.operands[1])
                        if allocation and allocation.op == "alloc_heap":
                            remove_ids.add(allocation.id)
                            allocations_removed += 1
                loops_fused += 1
            instructions = tuple(
                replacements.get(item.id, item)
                for item in block.instructions
                if item.id not in remove_ids
            )
            blocks.append(replace(block, instructions=instructions))
        functions.append(_replace_blocks(function, blocks))
    after = _replace_functions(mir, functions)
    return after, _stats(
        "collection_fusion",
        mir,
        after,
        allocations_removed=allocations_removed,
        loops_fused=loops_fused,
    )


def inlining(mir: PerformanceMIR) -> tuple[PerformanceMIR, PassStatistics]:
    inlineable: dict[str, MIRFunction] = {}
    for function in mir.functions:
        if (
            function.return_type.name in {"Bytes", "BytesView"}
            or any(
                parameter.type.name in {"Bytes", "BytesView"}
                for parameter in function.parameters
            )
        ):
            continue
        if not function.pure or len(function.blocks) != 1:
            continue
        block = function.blocks[0]
        if block.terminator.kind != "return" or block.terminator.value is None:
            continue
        if all(
            instruction.op
            in {
                "const",
                "binary",
                "unary",
                "compare",
                "field_load",
                "array_len",
                "alloc_heap",
                "array_init",
            }
            for instruction in block.instructions
        ):
            inlineable[function.name] = function
    calls_inlined = 0
    functions = []
    for function in mir.functions:
        blocks = []
        for block in function.blocks:
            output: list[MIRInstruction] = []
            for call in block.instructions:
                if call.op != "call":
                    output.append(call)
                    continue
                callee = inlineable.get(str(call.attribute_map.get("callee")))
                if callee is None or callee.name == function.name or call.result is None:
                    output.append(call)
                    continue
                value_map = {
                    parameter.value: operand
                    for parameter, operand in zip(callee.parameters, call.operands, strict=True)
                }
                callee_block = callee.blocks[0]
                return_value = callee_block.terminator.value
                for index, source_instruction in enumerate(callee_block.instructions):
                    if source_instruction.result is None:
                        continue
                    is_return = source_instruction.result == return_value
                    result = call.result if is_return else f"%inline_{call.id}_{index}"
                    operands = tuple(value_map.get(item, item) for item in source_instruction.operands)
                    value_map[source_instruction.result] = result
                    output.append(
                        source_instruction.replace(
                            id=f"{call.id}_inline_{source_instruction.id}",
                            result=result,
                            operands=operands,
                            source=call.source,
                        )
                    )
                if return_value in value_map and value_map[return_value] != call.result:
                    output.append(
                        MIRInstruction(
                            f"{call.id}_inline_move",
                            "move",
                            call.result,
                            call.type,
                            (value_map[return_value],),
                            (("inlined", callee.name),),
                            call.source,
                        )
                    )
                calls_inlined += 1
            blocks.append(replace(block, instructions=tuple(output)))
        functions.append(_replace_blocks(function, blocks))
    after = _replace_functions(mir, functions)
    return after, _stats("inlining", mir, after, calls_inlined=calls_inlined)

def owned_result_drop_insertion(
    mir: PerformanceMIR,
) -> tuple[PerformanceMIR, PassStatistics]:
    inserted = 0
    functions = []
    safe_uses = {
        "array_len",
        "bounds_check",
        "index_load",
        "store_index",
        "drop",
        "call",
        "retain",
        "borrow_shared",
        "borrow_mut",
    }

    def safe_use(instruction: MIRInstruction) -> bool:
        return instruction.op in safe_uses or instruction.op.startswith(
            ("collection_map", "collection_filter", "collection_fold")
        )
    for function in mir.functions:
        returned = {
            block.terminator.value
            for block in function.blocks
            if block.terminator.kind == "return"
            and block.terminator.value is not None
        }
        explicit_drops = {
            instruction.operands[0]
            for block in function.blocks
            for instruction in block.instructions
            if instruction.op == "drop" and instruction.operands
        }
        uses: dict[str, list[tuple[str, int, MIRInstruction]]] = {}
        borrow_aliases: dict[str, list[str]] = {}
        allocation_aliases: dict[str, tuple[str, int, MIRInstruction]] = {}
        for block in function.blocks:
            for index, instruction in enumerate(block.instructions):
                if (
                    instruction.result is not None
                    and instruction.operands
                    and instruction.op == "array_init"
                ):
                    allocation_aliases[instruction.operands[0]] = (
                        block.id,
                        index,
                        instruction,
                    )
                elif (
                    instruction.result is not None
                    and len(instruction.operands) > 1
                    and instruction.op.startswith(
                        ("collection_map", "collection_filter")
                    )
                ):
                    allocation_aliases[instruction.operands[1]] = (
                        block.id,
                        index,
                        instruction,
                    )
                if (
                    instruction.op in {"borrow_shared", "borrow_mut"}
                    and instruction.result is not None
                    and instruction.operands
                ):
                    borrow_aliases.setdefault(instruction.operands[0], []).append(
                        instruction.result
                    )
                for operand in instruction.operands:
                    uses.setdefault(operand, []).append(
                        (block.id, index, instruction)
                    )
        candidates: list[tuple[str, int, MIRInstruction, str]] = []
        for block in function.blocks:
            for index, instruction in enumerate(block.instructions):
                if (
                    instruction.op in {"call", "move", "retain"}
                    and instruction.result is not None
                    and instruction.type is not None
                    and instruction.type.kind in {"array", "slice"}
                ):
                    candidates.append(
                        (block.id, index, instruction, instruction.result)
                    )
                if instruction.op == "alloc_heap" and instruction.result is not None:
                    alias = allocation_aliases.get(instruction.result)
                    if alias is not None and alias[2].result is not None:
                        candidates.append((*alias, alias[2].result))
        insertion_points: dict[tuple[str, int], list[MIRInstruction]] = {}
        scheduled_values: set[str] = set()
        for block_id, producer_index, producer, value in candidates:
            if (
                value in scheduled_values
                or value in returned
                or value in explicit_drops
            ):
                continue
            result_uses = list(uses.get(value, ()))
            aliases_to_visit = list(borrow_aliases.get(value, ()))
            seen_aliases: set[str] = set()
            alias_escapes = False
            while aliases_to_visit:
                alias = aliases_to_visit.pop()
                if alias in seen_aliases:
                    continue
                seen_aliases.add(alias)
                if alias in returned:
                    alias_escapes = True
                    break
                result_uses.extend(uses.get(alias, ()))
                aliases_to_visit.extend(borrow_aliases.get(alias, ()))
            if alias_escapes:
                continue
            if (
                any(use_block != block_id for use_block, _, _ in result_uses)
                or any(not safe_use(use) for _, _, use in result_uses)
            ):
                continue
            last_use = max(
                (index for _, index, _ in result_uses),
                default=producer_index,
            )
            drop = MIRInstruction(
                f"drop_inferred_{function.name}_{block_id}_{producer.id}",
                "drop",
                operands=(value,),
                attributes=(
                    ("ownership", "inferred_scope_end"),
                    ("source_producer", producer.id),
                ),
                source=producer.source,
            )
            insertion_points.setdefault((block_id, last_use), []).append(drop)
            scheduled_values.add(value)
            inserted += 1
        blocks = []
        for block in function.blocks:
            instructions = []
            for index, instruction in enumerate(block.instructions):
                instructions.append(instruction)
                instructions.extend(insertion_points.get((block.id, index), ()))
            blocks.append(replace(block, instructions=tuple(instructions)))
        functions.append(_replace_blocks(function, blocks))
    after = _replace_functions(mir, functions)
    return after, _stats(
        "owned_result_drop_insertion",
        mir,
        after,
        instructions_removed=0,
        drops_inserted=inserted,
    )



def borrow_inference(mir: PerformanceMIR) -> tuple[PerformanceMIR, PassStatistics]:
    inferred = 0
    functions = []
    safe_uses = {
        "array_len",
        "bounds_check",
        "index_load",
        "store_index",
        "drop",
    }
    for function in mir.functions:
        instructions = [
            instruction
            for block in function.blocks
            for instruction in block.instructions
        ]
        uses: dict[str, list[MIRInstruction]] = {}
        for instruction in instructions:
            for operand in instruction.operands:
                uses.setdefault(operand, []).append(instruction)
        returned = {
            block.terminator.value
            for block in function.blocks
            if block.terminator.kind == "return"
            and block.terminator.value is not None
        }
        safe_allocations: set[str] = set()
        for instruction in instructions:
            if (
                instruction.op != "array_init"
                or instruction.result is None
                or not instruction.operands
            ):
                continue
            allocation = instruction.operands[0]
            allocation_uses = uses.get(allocation, [])
            value_uses = uses.get(instruction.result, [])
            if (
                instruction.result not in returned
                and len(allocation_uses) == 1
                and allocation_uses[0].id == instruction.id
                and sum(item.op == "drop" for item in value_uses) == 1
                and all(item.op in safe_uses for item in value_uses)
            ):
                safe_allocations.add(allocation)
        blocks = []
        for block in function.blocks:
            lowered = []
            for instruction in block.instructions:
                if (
                    instruction.op == "alloc_heap"
                    and instruction.result in safe_allocations
                    and instruction.type is not None
                    and instruction.type.shared
                ):
                    attributes = instruction.attribute_map
                    attributes.update(
                        {
                            "inferred_unique": True,
                            "borrowed_uses": True,
                            "ownership": "Unique",
                        }
                    )
                    instruction = instruction.replace(attributes=attributes)
                    inferred += 1
                lowered.append(instruction)
            blocks.append(replace(block, instructions=tuple(lowered)))
        functions.append(_replace_blocks(function, blocks))
    after = _replace_functions(mir, functions)
    return after, _stats(
        "borrow_inference",
        mir,
        after,
        allocations_removed=0,
        in_place_reuses=inferred,
    )


def bounds_check_elimination(mir: PerformanceMIR) -> tuple[PerformanceMIR, PassStatistics]:
    removed = 0
    functions = []
    for function in mir.functions:
        producers = {
            instruction.result: instruction
            for block in function.blocks
            for instruction in block.instructions
            if instruction.result is not None
        }
        blocks = []
        for block in function.blocks:
            instructions = []
            for instruction in block.instructions:
                if instruction.op != "bounds_check":
                    instructions.append(instruction)
                    continue
                attributes = instruction.attribute_map
                aggregate = attributes.get("aggregate")
                range_end = attributes.get("range_end")
                end_producer = producers.get(range_end)
                if (
                    aggregate is not None
                    and end_producer is not None
                    and end_producer.op == "array_len"
                    and end_producer.operands == (aggregate,)
                ):
                    removed += 1
                    continue
                instructions.append(instruction)
            blocks.append(replace(block, instructions=tuple(instructions)))
        functions.append(_replace_blocks(function, blocks))
    after = _replace_functions(mir, functions)
    return after, _stats(
        "bounds_check_elimination",
        mir,
        after,
        bounds_checks_removed=removed,
    )


def memory_model_lowering(mir: PerformanceMIR) -> tuple[PerformanceMIR, PassStatistics]:
    allocations_removed = 0
    stack_allocations = 0
    heap_allocations = 0
    in_place_reuses = 0
    functions = []
    for function in mir.functions:
        returned = {
            block.terminator.value
            for block in function.blocks
            if block.terminator.kind == "return" and block.terminator.value is not None
        }
        aliases: dict[str, str] = {}
        for block in function.blocks:
            for instruction in block.instructions:
                if (
                    instruction.op == "array_init"
                    and instruction.result is not None
                    and instruction.operands
                ):
                    aliases[instruction.operands[0]] = instruction.result
                elif (
                    instruction.op.startswith(
                        ("collection_map", "collection_filter")
                    )
                    and instruction.result is not None
                    and len(instruction.operands) > 1
                ):
                    aliases[instruction.operands[1]] = instruction.result
        explicit_drop_values = {
            instruction.operands[0]
            for block in function.blocks
            for instruction in block.instructions
            if instruction.op == "drop" and instruction.operands
        }
        explicitly_dropped_allocations = {
            allocation
            for allocation, value in aliases.items()
            if value in explicit_drop_values
        } | explicit_drop_values
        escaping_allocations = {
            allocation
            for allocation, value in aliases.items()
            if value in returned
        }
        entry_id = function.entry_block
        entry_heap_to_drop: list[tuple[str, Any]] = []
        blocks = []
        for block in function.blocks:
            instructions = []
            for instruction in block.instructions:
                if instruction.op == "store_index" and instruction.attribute_map.get("in_place"):
                    in_place_reuses += 1
                if instruction.op != "alloc_heap" or instruction.result is None or instruction.type is None:
                    if instruction.op == "alloc_stack":
                        stack_allocations += 1
                    instructions.append(instruction)
                    continue
                attributes = instruction.attribute_map
                try:
                    if instruction.type.kind == "array":
                        attributes["bytes"] = (
                            scalar_layout(instruction.type.element).size
                            * (instruction.type.length or 0)
                        )
                    else:
                        attributes["bytes"] = scalar_layout(instruction.type).size
                except ValueError:
                    attributes["bytes"] = 0
                if (
                    (instruction.type.unique or attributes.get("inferred_unique"))
                    and instruction.result not in escaping_allocations
                    and instruction.type.kind != "slice"
                    and (
                        not instruction.type.shared
                        or attributes.get("inferred_unique")
                    )
                ):
                    attributes.update(
                        {
                            "allocation": "stack",
                            "escape": False,
                            "ownership": "unique",
                        }
                    )
                    instructions.append(
                        instruction.replace(op="alloc_stack", attributes=attributes)
                    )
                    allocations_removed += 1
                    stack_allocations += 1
                else:
                    attributes.update(
                        {
                            "allocation": "heap",
                            "escape": instruction.result in escaping_allocations,
                            "ownership": "shared_refcounted" if instruction.type.shared else "unique_moved",
                            "fallback": "reference_counting" if instruction.type.shared else None,
                        }
                    )
                    instructions.append(instruction.replace(attributes=attributes))
                    heap_allocations += 1
                    if (
                        block.id == entry_id
                        and instruction.result not in escaping_allocations
                        and instruction.result not in explicitly_dropped_allocations
                    ):
                        entry_heap_to_drop.append((instruction.result, instruction.source))
            if block.terminator.kind == "return" and entry_heap_to_drop:
                for index, (value, source) in enumerate(entry_heap_to_drop):
                    instructions.append(
                        MIRInstruction(
                            f"drop_{function.name}_{block.id}_{index}",
                            "drop",
                            operands=(value,),
                            attributes=(("ownership", "explicit"),),
                            source=source,
                        )
                    )
            blocks.append(replace(block, instructions=tuple(instructions)))
        functions.append(_replace_blocks(function, blocks))
    after = _replace_functions(mir, functions)
    return after, _stats(
        "memory_model_lowering",
        mir,
        after,
        allocations_removed=allocations_removed,
        stack_allocations=stack_allocations,
        heap_allocations=heap_allocations,
        in_place_reuses=in_place_reuses,
    )

def region_ownership_lowering(
    mir: PerformanceMIR,
) -> tuple[PerformanceMIR, PassStatistics]:
    promoted = 0
    functions = []
    for function in mir.functions:
        blocks = []
        for block in function.blocks:
            instructions = []
            for instruction in block.instructions:
                if (
                    instruction.op == "alloc_stack"
                    and instruction.attribute_map.get("inferred_unique")
                ):
                    attributes = instruction.attribute_map
                    attributes.update(
                        {
                            "allocation": "region",
                            "ownership": "RegionOwned",
                            "region": f"{function.name}:{block.id}",
                        }
                    )
                    instruction = instruction.replace(
                        op="alloc_region",
                        attributes=attributes,
                    )
                    promoted += 1
                instructions.append(instruction)
            blocks.append(replace(block, instructions=tuple(instructions)))
        functions.append(_replace_blocks(function, blocks))
    after = _replace_functions(mir, functions)
    return after, _stats(
        "region_ownership_lowering",
        mir,
        after,
        allocations_removed=promoted,
        stack_allocations=promoted,
    )



OPTIMIZATION_PIPELINE: tuple[PassFunction, ...] = (
    monomorphization,
    collection_fusion,
    inlining,
    owned_result_drop_insertion,
    borrow_inference,
    constant_folding,
    bounds_check_elimination,
    memory_model_lowering,
    dead_code_elimination,
)
OPTIMIZATION_PASS_VERSIONS = {
    "monomorphization": 1,
    "collection_fusion": 1,
    "inlining": 2,
    "owned_result_drop_insertion": 2,
    "borrow_inference": 1,
    "constant_folding": 3,
    "bounds_check_elimination": 1,
    "memory_model_lowering": PERFORMANCE_MEMORY_MODEL_VERSION,
    "dead_code_elimination": 1,
    "region_ownership_lowering": 1,
}




def optimize_mir(
    mir: PerformanceMIR,
    *,
    artifact_dir: str | Path | None = None,
    passes: Iterable[PassFunction] = OPTIMIZATION_PIPELINE,
) -> tuple[PerformanceMIR, tuple[PassSnapshot, ...]]:
    current = mir
    snapshots = []
    destination = Path(artifact_dir) if artifact_dir is not None else None
    if destination is not None:
        destination.mkdir(parents=True, exist_ok=True)
    for index, pass_function in enumerate(passes, 1):
        before = current
        after, statistics = pass_function(before)
        snapshot = PassSnapshot(statistics.name, before, after, statistics)
        snapshots.append(snapshot)
        if destination is not None:
            stem = f"{index:02d}_{statistics.name}"
            (destination / f"{stem}_before.json").write_text(
                json.dumps(before.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (destination / f"{stem}_after.json").write_text(
                json.dumps(after.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (destination / f"{stem}_statistics.json").write_text(
                json.dumps(statistics.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        current = after
    return current, tuple(snapshots)


__all__ = [
    "PERFORMANCE_MEMORY_MODEL_VERSION",
    "OPTIMIZATION_PIPELINE",
    "OPTIMIZATION_PASS_VERSIONS",
    "bounds_check_elimination",
    "borrow_inference",
    "collection_fusion",
    "constant_folding",
    "dead_code_elimination",
    "inlining",
    "memory_model_lowering",
    "region_ownership_lowering",
    "owned_result_drop_insertion",
    "monomorphization",
    "optimize_mir",
]
