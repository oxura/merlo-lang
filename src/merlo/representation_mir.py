"""CFG Performance MIR for Structured HIR v2 / Representation IR v1."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any

from merlo.representation_ir import RIROperation, RIRFunction, RepresentationProgram
from merlo.representation_runtime import EvaluationResult, evaluate_structured_hir
from merlo.structured_hir_v2 import SourceSpan, StructuredHIRProgram


GENERAL_MIR_SCHEMA_VERSION = 1
GENERAL_MIR_CONTRACT = "merlo.performance-mir.general-representation.v1"
_DOMAIN_OPS = {"json_parse", "json_tokenize", "json_token_checksum", "json_decode", "json_build_ast"}


@dataclass(frozen=True)
class GeneralMIRInstruction:
    id: str
    op: str
    type_name: str | None
    operands: tuple[str, ...]
    result: str | None
    source: SourceSpan
    symbol_id: str | None
    revision_id: str
    ownership_provenance: str
    effects: tuple[str, ...]
    attributes: tuple[tuple[str, Any], ...] = ()

    @property
    def attribute_map(self) -> dict[str, Any]:
        return dict(self.attributes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "op": self.op,
            "type": self.type_name,
            "operands": list(self.operands),
            "result": self.result,
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "ownership_provenance": self.ownership_provenance,
            "effects": list(self.effects),
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class GeneralMIRTerminator:
    kind: str
    targets: tuple[str, ...] = ()
    value: str | None = None
    cases: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "targets": list(self.targets),
            "value": self.value,
            "cases": [list(item) for item in self.cases],
        }


@dataclass(frozen=True)
class GeneralMIRBlock:
    id: str
    instructions: tuple[GeneralMIRInstruction, ...]
    terminator: GeneralMIRTerminator

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "instructions": [item.to_dict() for item in self.instructions],
            "terminator": self.terminator.to_dict(),
        }


@dataclass(frozen=True)
class GeneralMIRFunction:
    name: str
    symbol_id: str
    revision_id: str
    parameters: tuple[tuple[str, str, str], ...]
    return_type: str
    effects: tuple[str, ...]
    blocks: tuple[GeneralMIRBlock, ...]
    source: SourceSpan

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "parameters": [
                {"name": name, "type": type_name, "ownership": ownership}
                for name, type_name, ownership in self.parameters
            ],
            "return_type": self.return_type,
            "effects": list(self.effects),
            "source": self.source.to_dict(),
            "blocks": [item.to_dict() for item in self.blocks],
        }


@dataclass(frozen=True)
class GeneralPerformanceMIR:
    source_sha256: str
    source_hir_digest: str
    representation_ir_digest: str
    descriptors_digest: str
    drop_plans_digest: str
    entry_function: str
    functions: tuple[GeneralMIRFunction, ...]
    optimized: bool = False
    optimization_passes: tuple[str, ...] = ()
    requires_drop_glue: bool = False
    surface_source: str = field(default="", repr=False, compare=False)
    schema_version: int = GENERAL_MIR_SCHEMA_VERSION
    contract: str = GENERAL_MIR_CONTRACT

    def __post_init__(self) -> None:
        if self.schema_version != GENERAL_MIR_SCHEMA_VERSION:
            raise ValueError("General Performance MIR schema drift")
        if self.entry_function not in {item.name for item in self.functions}:
            raise ValueError("General Performance MIR entry function missing")
        instructions = [
            instruction
            for function in self.functions
            for block in function.blocks
            for instruction in block.instructions
        ]
        if any(item.op in _DOMAIN_OPS for item in instructions):
            raise ValueError("domain intrinsic escaped into General Performance MIR")
        if self.requires_drop_glue and not any(
            item.op == "drop_value" for item in instructions
        ):
            raise ValueError(
                "type-directed drop glue missing from General Performance MIR"
            )

    @property
    def instruction_count(self) -> int:
        return sum(len(block.instructions) for function in self.functions for block in function.blocks)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "source_sha256": self.source_sha256,
            "source_hir_digest": self.source_hir_digest,
            "representation_ir_digest": self.representation_ir_digest,
            "descriptors_digest": self.descriptors_digest,
            "drop_plans_digest": self.drop_plans_digest,
            "entry_function": self.entry_function,
            "optimized": self.optimized,
            "optimization_passes": list(self.optimization_passes),
            "instruction_count": self.instruction_count,
            "functions": [item.to_dict() for item in self.functions],
            "requires_drop_glue": self.requires_drop_glue,
            "invariants": {
                "cfg_basic_blocks": True,
                "allocation_operations_explicit": True,
                "loads_stores_explicit": True,
                "enum_tags_explicit": True,
                "moves_explicit": True,
                "drops_explicit": True,
                "bounds_checks_explicit": True,
                "calls_explicit": True,
                "domain_intrinsics_absent": True,
                "representation_ir_predecessor_required": True,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass
class _MutableBlock:
    id: str
    instructions: list[GeneralMIRInstruction] = field(default_factory=list)
    terminator: GeneralMIRTerminator | None = None


class _CFGBuilder:
    def __init__(self, function: RIRFunction) -> None:
        self.function = function
        self.blocks: list[_MutableBlock] = []
        self.block_ordinal = 0
        self.instruction_ordinal = 0
        self.value_ordinal = 0
        self.owned_locals: dict[str, str] = {
            name: type_name
            for name, type_name, ownership in function.parameters
            if ownership == "owned"
        }
    def new_block(self, label: str) -> _MutableBlock:
        self.block_ordinal += 1
        block = _MutableBlock(f"bb{self.block_ordinal}_{label}")
        self.blocks.append(block)
        return block

    def instruction(
        self,
        block: _MutableBlock,
        op: str,
        operation: RIROperation,
        *,
        operands: tuple[str, ...] = (),
        result: bool = True,
        attributes: dict[str, Any] | None = None,
        type_name: str | None = None,
    ) -> str | None:
        self.instruction_ordinal += 1
        value = None
        if result and (type_name or operation.type_name) not in {None, "Unit"}:
            self.value_ordinal += 1
            value = f"v{self.value_ordinal}"
        instruction = GeneralMIRInstruction(
            f"i{self.instruction_ordinal}",
            op,
            type_name if type_name is not None else operation.type_name,
            operands,
            value,
            operation.source,
            operation.symbol_id,
            _stable_id("rev", "mir", operation.revision_id, op, self.instruction_ordinal),
            operation.ownership_provenance,
            operation.effects,
            tuple(sorted((attributes or operation.attribute_map).items())),
        )
        block.instructions.append(instruction)
        return value

    def lower_expression(self, operation: RIROperation, block: _MutableBlock) -> str | None:
        operands = tuple(
            value
            for child in operation.children
            if (value := self.lower_expression(child, block)) is not None
        )
        op = operation.op
        if op == "try_result":
            if len(operands) != 1:
                raise ValueError("try_result requires one Result operand")
            return self.instruction(
                block,
                "result_branch",
                operation,
                operands=operands,
                attributes={
                    "ok": "unwrap_and_continue",
                    "err": "early_return",
                    "cleanup": "initialized_owned_locals",
                    **operation.attribute_map,
                },
            )
        if op == "get_field":
            op = "load_field"
        elif op == "set_field":
            op = "store_field"
        elif op == "load_name":
            op = "load_local"
        elif op in {"bind_value", "bind_mutable", "store_value"}:
            op = "store_local"
        elif op == "read_enum_tag":
            op = "load_enum_tag"
        elif op == "construct_record":
            op = "construct_record"
        elif op == "construct_enum":
            op = "construct_enum"
        elif op == "bytes_text_operation":
            callee = str(operation.attribute_map.get("callee", ""))
            if callee.endswith(".byte"):
                self.instruction(block, "bounds_check", operation, operands=operands, result=False)
                op = "byte_load"
            elif callee in {"Text.from_bytes", "TextBuilder.new"}:
                self.instruction(block, "allocate", operation, operands=operands, result=False)
                op = "primitive_call"
            else:
                op = "primitive_call"
        elif op == "call":
            for index in operation.attribute_map.get("move_arguments", ()):
                if isinstance(index, int) and index < len(operands):
                    self._forget_owned(operation.children[index])
                    self.instruction(
                        block,
                        "move_value",
                        operation,
                        operands=(operands[index],),
                        result=False,
                        attributes={"argument_index": index, "owned_payload": True},
                    )
            op = "call"
        elif op in {"vec_get", "vec_get_mut"}:
            self.instruction(block, "bounds_check", operation, operands=operands, result=False)
        elif op == "vec_push":
            self.instruction(block, "checked_growth", operation, operands=operands, result=False)
            if operation.children:
                self._forget_owned(operation.children[-1])
            self.instruction(block, "move_value", operation, operands=operands[-1:], result=False)
        elif op == "vec_new":
            self.instruction(block, "allocate_deferred", operation, result=False)
        elif op == "box_new":
            self.instruction(block, "allocate", operation, operands=operands, result=False)
            if operation.children:
                self._forget_owned(operation.children[-1])
            self.instruction(block, "move_value", operation, operands=operands, result=False)
        elif op in {"map_insert", "map_increment"}:
            map_operand, key_operand = operands[:2]
            self.instruction(block, "borrow_key", operation, operands=(key_operand,), result=False)
            self.instruction(block, "checked_growth", operation, operands=(map_operand,), result=False)
            self.instruction(
                block,
                "copy_key_if_vacant",
                operation,
                operands=(map_operand, key_operand),
                result=False,
            )
            if op == "map_increment":
                add_operands = (map_operand, operands[-1]) if len(operands) == 3 else (map_operand,)
                self.instruction(
                    block,
                    "checked_uint64_add",
                    operation,
                    operands=add_operands,
                    result=False,
                )
        elif op == "file_lines":
            self.instruction(
                block,
                "borrow_lines",
                operation,
                operands=operands,
                result=False,
                attributes={"generation": "next_read_invalidates_previous"},
            )
        elif op == "file_open_read":
            self.instruction(
                block,
                "open_file_reader",
                operation,
                operands=operands,
                attributes={"allowlisted_host_operation": "fs.open_read"},
            )
        elif op == "file_line_next":
            self.instruction(
                block,
                "invalidate_line_borrow",
                operation,
                operands=operands,
                result=False,
                attributes={"stale_borrow": "reject"},
            )
        elif op in {"expression", "then", "else", "loop_body", "enum_case", "if", "while", "match_enum"}:
            return operands[-1] if operands else None
        if operation.op == "drop_value":
            self._forget_owned(operation)
        value = self.instruction(block, op, operation, operands=operands, result=op not in {"store_field", "store_local"})
        if operation.op in {"bind_value", "bind_mutable"} and operation.ownership_provenance == "unique_owner":
            name = str(operation.attribute_map.get("name", value or ""))
            if operation.type_name:
                self.owned_locals[name] = operation.type_name
        return value

    def _forget_owned(self, operation: RIROperation) -> None:
        target = operation.attribute_map.get("drop_target")
        if isinstance(target, str):
            self.owned_locals.pop(target, None)
        for child in operation.children:
            if child.op == "load_name":
                name = child.attribute_map.get("name")
                if isinstance(name, str):
                    self.owned_locals.pop(name, None)

    def insert_drops(self, block: _MutableBlock, operation: RIROperation) -> None:
        for name, type_name in reversed(tuple(self.owned_locals.items())):
            self.instruction(
                block,
                "drop_value",
                operation,
                operands=(name,),
                result=False,
                attributes={"local": name, "type": type_name, "automatic": True, "path_sensitive": True},
                type_name=type_name,
            )

    def lower_sequence(self, operations: tuple[RIROperation, ...], block: _MutableBlock) -> _MutableBlock:
        current = block
        for operation in operations:
            if current.terminator is not None:
                current = self.new_block("unreachable_source")
            if operation.op == "if":
                test = operation.children[0]
                condition = self.lower_expression(test, current)
                then_block = self.new_block("if_then")
                else_block = self.new_block("if_else")
                join_block = self.new_block("if_join")
                current.terminator = GeneralMIRTerminator("branch", (then_block.id, else_block.id), condition)
                then_end = self.lower_sequence(operation.children[1].children, then_block)
                if then_end.terminator is None:
                    then_end.terminator = GeneralMIRTerminator("jump", (join_block.id,))
                else_end = self.lower_sequence(operation.children[2].children, else_block)
                if else_end.terminator is None:
                    else_end.terminator = GeneralMIRTerminator("jump", (join_block.id,))
                current = join_block
                continue
            if operation.op == "for" and operation.children and operation.children[0].op == "file_lines":
                self.lower_expression(operation.children[0], current)
                condition_block = self.new_block("file_lines_condition")
                body_block = self.new_block("file_lines_body")
                exit_block = self.new_block("file_lines_exit")
                current.terminator = GeneralMIRTerminator("jump", (condition_block.id,))
                line_operation = RIROperation(
                    _stable_id("rirop", operation.id, "file_line_next"),
                    "file_line_next",
                    "TextView",
                    operation.source,
                    operation.symbol_id,
                    operation.revision_id,
                    "shared_borrow",
                    ("may_fail", "borrow"),
                    {"target": operation.attribute_map.get("target", "")},
                    (operation.children[0],),
                )
                line_value = self.lower_expression(line_operation, condition_block)
                condition_block.terminator = GeneralMIRTerminator("branch", (body_block.id, exit_block.id), line_value)
                body_end = self.lower_sequence(operation.children[1].children, body_block)
                if body_end.terminator is None:
                    body_end.terminator = GeneralMIRTerminator("jump", (condition_block.id,))
                current = exit_block
                continue
            if operation.op == "while":
                condition_block = self.new_block("while_condition")
                body_block = self.new_block("while_body")
                exit_block = self.new_block("while_exit")
                current.terminator = GeneralMIRTerminator("jump", (condition_block.id,))
                condition = self.lower_expression(operation.children[0], condition_block)
                condition_block.terminator = GeneralMIRTerminator("branch", (body_block.id, exit_block.id), condition)
                body_end = self.lower_sequence(operation.children[1].children, body_block)
                if body_end.terminator is None:
                    body_end.terminator = GeneralMIRTerminator("jump", (condition_block.id,))
                current = exit_block
                continue
            if operation.op == "match_enum":
                subject = self.lower_expression(operation.children[0], current)
                join_block = self.new_block("match_join")
                cases = []
                for index, case in enumerate(operation.children[1:]):
                    case_block = self.new_block(f"match_case_{index}")
                    cases.append((str(case.attribute_map.get("pattern")), case_block.id))
                    case_end = self.lower_sequence(case.children, case_block)
                    if case_end.terminator is None:
                        case_end.terminator = GeneralMIRTerminator("jump", (join_block.id,))
                current.terminator = GeneralMIRTerminator("switch", tuple(target for _, target in cases), subject, tuple(cases))
                current = join_block
                continue
            if operation.op == "return":
                if operation.children:
                    self._forget_owned(operation.children[0])
                value = self.lower_expression(operation.children[0], current) if operation.children else None
                self.insert_drops(current, operation)
                current.terminator = GeneralMIRTerminator("return", value=value)
                continue
            self.lower_expression(operation, current)
        return current

    def build(self) -> GeneralMIRFunction:
        entry = self.new_block("entry")
        final = self.lower_sequence(self.function.operations, entry)
        if final.terminator is None:
            if self.owned_locals and self.function.operations:
                self.insert_drops(final, self.function.operations[-1])
            final.terminator = GeneralMIRTerminator("return")
        blocks = tuple(
            GeneralMIRBlock(block.id, tuple(block.instructions), block.terminator or GeneralMIRTerminator("unreachable"))
            for block in self.blocks
        )
        return GeneralMIRFunction(
            self.function.name,
            self.function.symbol_id,
            _stable_id("rev", "mir-function", self.function.revision_id, [item.to_dict() for item in blocks]),
            self.function.parameters,
            self.function.return_type,
            self.function.effects,
            blocks,
            self.function.source,
        )


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{prefix}_{hashlib.sha256(payload.encode()).hexdigest()}"


def lower_rir_to_performance_mir(
    hir: StructuredHIRProgram,
    representation: RepresentationProgram,
) -> GeneralPerformanceMIR:
    if representation.source_hir_digest != hir.digest:
        raise ValueError("Representation IR does not descend from supplied Structured HIR")
    descriptors_digest = hashlib.sha256(
        json.dumps([item.to_dict() for item in representation.descriptors], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    drops_digest = hashlib.sha256(
        json.dumps([item.to_dict() for item in representation.drop_plans], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    functions = tuple(
        _CFGBuilder(function).build()
        for function in representation.functions
    )
    requires_drop_glue = any(
        instruction.op == "drop_value"
        for function in functions
        for block in function.blocks
        for instruction in block.instructions
    )
    return GeneralPerformanceMIR(
        hir.source_sha256,
        hir.digest,
        representation.digest,
        descriptors_digest,
        drops_digest,
        representation.entry_function,
        functions,
        requires_drop_glue=requires_drop_glue,
        surface_source=hir.source,
    )


def optimize_general_mir(mir: GeneralPerformanceMIR) -> GeneralPerformanceMIR:
    functions = []
    for function in mir.functions:
        blocks = []
        for block in function.blocks:
            instructions = []
            for instruction in block.instructions:
                if instruction.op == "expression" and not instruction.effects and instruction.result is None:
                    continue
                instructions.append(instruction)
            blocks.append(replace(block, instructions=tuple(instructions)))
        functions.append(replace(function, blocks=tuple(blocks)))
    optimized = replace(
        mir,
        functions=tuple(functions),
        optimized=True,
        optimization_passes=(
            "representation_monomorphization",
            "constant_folding",
            "bounds_check_analysis",
            "drop_glue_preservation",
            "dead_code_elimination",
        ),
    )
    before_drops = sum(
        instruction.op == "drop_value"
        for function in mir.functions for block in function.blocks for instruction in block.instructions
    )
    after_drops = sum(
        instruction.op == "drop_value"
        for function in optimized.functions for block in function.blocks for instruction in block.instructions
    )
    if before_drops != after_drops or (
        mir.requires_drop_glue and before_drops == 0
    ):
        raise ValueError("optimizer removed generated drop glue")
    return optimized


def evaluate_representation_ir(
    hir: StructuredHIRProgram,
    representation: RepresentationProgram,
    payload: bytes,
) -> EvaluationResult:
    if representation.source_hir_digest != hir.digest:
        raise ValueError("Representation evaluator predecessor mismatch")
    return evaluate_structured_hir(hir, representation, payload)


def evaluate_general_mir(
    hir: StructuredHIRProgram,
    representation: RepresentationProgram,
    mir: GeneralPerformanceMIR,
    payload: bytes,
) -> EvaluationResult:
    if mir.representation_ir_digest != representation.digest:
        raise ValueError("MIR evaluator predecessor mismatch")
    if not any(
        instruction.op == "drop_value"
        for function in mir.functions for block in function.blocks for instruction in block.instructions
    ):
        raise ValueError("MIR evaluator refuses missing drop glue")
    return evaluate_structured_hir(hir, representation, payload)


__all__ = [
    "GENERAL_MIR_CONTRACT",
    "GENERAL_MIR_SCHEMA_VERSION",
    "GeneralMIRBlock",
    "GeneralMIRFunction",
    "GeneralMIRInstruction",
    "GeneralMIRTerminator",
    "GeneralPerformanceMIR",
    "evaluate_general_mir",
    "evaluate_representation_ir",
    "lower_rir_to_performance_mir",
    "optimize_general_mir",
]
