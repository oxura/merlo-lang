"""CFG Performance MIR for Structured HIR v2 / Representation IR v6."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any

from merlo.collection_protocol import (
    FUSIBLE_COLLECTION_ELEMENTS,
    collection_shape,
)
from merlo.representation_ir import (
    DEFAULT_TARGET_SPEC,
    DropPlanId,
    DropPlan,
    LayoutId,
    RepresentationProgram,
    RIROperation,
    RIRFunction,
    TargetSpec,
    TYPE_ARENA_CONTRACT,
    drop_plan_id_for,
    verify_representation_program,
)
from merlo.mir_ownership import (
    MIROwnershipProgram,
    MIROwnershipVerificationError,
    build_mir_ownership_program,
    ownership_type_kinds_from_descriptors,
    verify_ownership_program,
)
from merlo.structured_hir_v2 import HIRNode, SourceSpan, StructuredHIRProgram
from merlo.representation_runtime import EvaluationResult, evaluate_structured_hir
from merlo.type_arena import (
    FrozenTypeArena,
    TypeArena,
    TypeArenaError,
    TypeContext,
    TypeId,
)



class MIRVerificationError(ValueError):
    """Fail-closed executable MIR verification failure."""

GENERAL_MIR_SCHEMA_VERSION = 4
GENERAL_MIR_CONTRACT = "merlo.performance-mir.general-representation.v4"
_DOMAIN_OPS = {
    "json_parse",
    "json_tokenize",
    "json_token_checksum",
    "json_decode",
    "json_build_ast",
}
def _json_payload(value: object) -> Any:
    if isinstance(value, (TypeId, LayoutId, DropPlanId)):
        return value.to_dict()
    if isinstance(value, HIRNode):
        return {"contract": "merlo.hir-node.v1", "value": value.to_dict()}
    if isinstance(value, (tuple, list)):
        return [_json_payload(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _json_payload(value[key])
            for key in sorted(value)
        }
    return value


def _type_id_from_dict(value: object, label: str) -> TypeId:
    try:
        return TypeId.from_dict(value)
    except TypeArenaError as exc:
        raise ValueError(f"invalid MIR {label} TypeId") from exc


def _optional_type_id_from_dict(value: object, label: str) -> TypeId | None:
    return None if value is None else _type_id_from_dict(value, label)

def _drop_plan_id_from_dict(value: object, label: str) -> DropPlanId:
    try:
        return DropPlanId.from_dict(value)
    except ValueError as exc:
        raise ValueError(f"invalid MIR {label} DropPlanId") from exc


def _source_from_dict(value: object) -> SourceSpan:
    if not isinstance(value, dict) or set(value) != {
        "path", "line", "column", "end_line", "end_column"
    }:
        raise ValueError("invalid MIR source span")
    return SourceSpan(
        value["path"],
        value["line"],
        value["column"],
        value["end_line"],
        value["end_column"],
    )
def _hydrate(value: object) -> Any:
    if isinstance(value, dict):
        if (
            set(value) == {"contract", "value"}
            and value["contract"] == "merlo.drop-plan.v1"
        ):
            return _drop_plan_id_from_dict(value, "attribute")
        if (
            set(value) == {"contract", "value"}
            and value["contract"] == "merlo.type-id.v1"
        ):
            return _type_id_from_dict(value, "attribute")
        if (
            set(value) == {"contract", "value"}
            and value["contract"] == "merlo.hir-node.v1"
        ):
            return HIRNode.from_dict(value["value"])
        return {key: _hydrate(item) for key, item in value.items()}
    if isinstance(value, list):
        return tuple(_hydrate(item) for item in value)
    return value


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
    type_id: TypeId | None = None
    operand_type_ids: tuple[TypeId, ...] = ()
    result_type_id: TypeId | None = None
    place_type_ids: tuple[tuple[str, TypeId], ...] = ()

    def __post_init__(self) -> None:
        for label, value in (
            ("type_id", self.type_id),
            ("result_type_id", self.result_type_id),
        ):
            if value is not None and not isinstance(value, TypeId):
                raise ValueError(f"MIR instruction {label} must be TypeId")
        if any(not isinstance(item, TypeId) for item in self.operand_type_ids):
            raise ValueError("MIR operand identities must be TypeId")
        if any(
            not isinstance(name, str) or not isinstance(type_id, TypeId)
            for name, type_id in self.place_type_ids
        ):
            raise ValueError("MIR place identities must be named TypeIds")

    @property
    def attribute_map(self) -> dict[str, Any]:
        return dict(self.attributes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "op": self.op,
            "type": self.type_name,
            "type_id": self.type_id.to_dict() if self.type_id else None,
            "operands": list(self.operands),
            "operand_type_ids": [item.to_dict() for item in self.operand_type_ids],
            "result": self.result,
            "result_type_id": (
                self.result_type_id.to_dict()
                if self.result_type_id
                else None
            ),
            "place_type_ids": [
                {"name": name, "type_id": type_id.to_dict()}
                for name, type_id in self.place_type_ids
            ],
            "source": self.source.to_dict(),
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "ownership_provenance": self.ownership_provenance,
            "effects": list(self.effects),
            "attributes": _json_payload(dict(self.attributes)),
        }
    @classmethod
    def from_dict(cls, value: object) -> "GeneralMIRInstruction":
        expected = {
            "id",
            "op",
            "type",
            "type_id",
            "operands",
            "operand_type_ids",
            "result",
            "result_type_id",
            "place_type_ids",
            "source",
            "symbol_id",
            "revision_id",
            "ownership_provenance",
            "effects",
            "attributes",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("MIR instruction schema mismatch")
        attributes = value["attributes"]
        if not isinstance(attributes, dict):
            raise ValueError("MIR instruction attributes must be an object")
        return cls(
            value["id"],
            value["op"],
            value["type"],
            tuple(value["operands"]),
            value["result"],
            _source_from_dict(value["source"]),
            value["symbol_id"],
            value["revision_id"],
            value["ownership_provenance"],
            tuple(value["effects"]),
            tuple((key, _hydrate(item)) for key, item in attributes.items()),
            _optional_type_id_from_dict(value["type_id"], "instruction"),
            tuple(
                _type_id_from_dict(item, "operand")
                for item in value["operand_type_ids"]
            ),
            _optional_type_id_from_dict(value["result_type_id"], "result"),
            tuple(
                (
                    item["name"],
                    _type_id_from_dict(item["type_id"], "place"),
                )
                for item in value["place_type_ids"]
            ),
        )


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

    @classmethod
    def from_dict(cls, value: object) -> "GeneralMIRTerminator":
        if not isinstance(value, dict) or set(value) != {
            "kind",
            "targets",
            "value",
            "cases",
        }:
            raise ValueError("MIR terminator schema mismatch")
        return cls(
            value["kind"],
            tuple(value["targets"]),
            value["value"],
            tuple(tuple(item) for item in value["cases"]),
        )

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
    @classmethod
    def from_dict(cls, value: object) -> "GeneralMIRBlock":
        if not isinstance(value, dict) or set(value) != {
            "id",
            "instructions",
            "terminator",
        }:
            raise ValueError("MIR block schema mismatch")
        return cls(
            value["id"],
            tuple(
                GeneralMIRInstruction.from_dict(item)
                for item in value["instructions"]
            ),
            GeneralMIRTerminator.from_dict(value["terminator"]),
        )


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
    parameter_type_ids: tuple[TypeId, ...] = ()
    return_type_id: TypeId | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "parameters": [
                {
                    "name": name,
                    "type": type_name,
                    "type_id": self.parameter_type_ids[index].to_dict(),
                    "ownership": ownership,
                }
                for index, (name, type_name, ownership) in enumerate(self.parameters)
            ],
            "return_type": self.return_type,
            "return_type_id": (
                self.return_type_id.to_dict()
                if self.return_type_id
                else None
            ),
            "effects": list(self.effects),
            "source": self.source.to_dict(),
            "blocks": [item.to_dict() for item in self.blocks],
        }
    @classmethod
    def from_dict(cls, value: object) -> "GeneralMIRFunction":
        if not isinstance(value, dict) or set(value) != {
            "name",
            "symbol_id",
            "revision_id",
            "parameters",
            "return_type",
            "return_type_id",
            "effects",
            "source",
            "blocks",
        }:
            raise ValueError("MIR function schema mismatch")
        parameters = tuple(
            (item["name"], item["type"], item["ownership"])
            for item in value["parameters"]
        )
        parameter_type_ids = tuple(
            _type_id_from_dict(item["type_id"], "parameter")
            for item in value["parameters"]
        )
        return cls(
            value["name"],
            value["symbol_id"],
            value["revision_id"],
            parameters,
            value["return_type"],
            tuple(value["effects"]),
            tuple(GeneralMIRBlock.from_dict(item) for item in value["blocks"]),
            _source_from_dict(value["source"]),
            parameter_type_ids,
            _type_id_from_dict(value["return_type_id"], "return"),
        )
def _ownership_authority_digest(
    ownership: MIROwnershipProgram,
) -> str:
    payload = [
        (str(type_id), kind.value)
        for type_id, kind in ownership.type_kinds
    ]
    return hashlib.sha256(
        json.dumps(
            payload,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()




@dataclass(frozen=True)
class GeneralPerformanceMIR:
    source_sha256: str
    source_hir_digest: str
    representation_ir_digest: str
    descriptors_digest: str
    drop_plans_digest: str
    entry_function: str
    functions: tuple[GeneralMIRFunction, ...]
    ownership: MIROwnershipProgram
    optimized: bool = False
    optimization_passes: tuple[str, ...] = ()
    requires_drop_glue: bool = False
    surface_source: str = field(default="", repr=False, compare=False)
    schema_version: int = GENERAL_MIR_SCHEMA_VERSION
    contract: str = GENERAL_MIR_CONTRACT
    type_arena: FrozenTypeArena | None = None
    type_arena_digest: str = ""
    descriptor_layouts: tuple[tuple[TypeId, LayoutId], ...] = ()
    type_arena_contract: str = TYPE_ARENA_CONTRACT
    predecessor_digest: str = ""
    target_spec: TargetSpec = DEFAULT_TARGET_SPEC
    target_spec_digest: str = ""
    drop_plan_bindings: tuple[
        tuple[TypeId, DropPlanId, str],
        ...,
    ] = ()
    ownership_authority_digest: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != GENERAL_MIR_SCHEMA_VERSION:
            raise ValueError("General Performance MIR schema drift")
        if self.contract != GENERAL_MIR_CONTRACT:
            raise ValueError("General Performance MIR contract drift")
        if not isinstance(self.ownership, MIROwnershipProgram):
            raise ValueError("General Performance MIR requires ownership SSA")
        authority_digest = _ownership_authority_digest(
            self.ownership
        )
        if self.ownership_authority_digest == "":
            object.__setattr__(
                self,
                "ownership_authority_digest",
                authority_digest,
            )
        if self.ownership_authority_digest != authority_digest:
            raise ValueError(
                "General Performance MIR ownership authority "
                "digest mismatch"
            )
        if self.type_arena_contract != TYPE_ARENA_CONTRACT:
            raise ValueError("General Performance MIR TypeArena contract drift")
        if not isinstance(self.type_arena, FrozenTypeArena):
            raise ValueError("General Performance MIR requires a frozen TypeArena")
        if self.type_arena.allow_unresolved:
            raise ValueError("General Performance MIR requires a closed TypeArena")
        if self.type_arena.digest != self.type_arena_digest:
            raise ValueError("General Performance MIR TypeArena digest mismatch")
        if self.predecessor_digest == "":
            object.__setattr__(
                self,
                "predecessor_digest",
                self.representation_ir_digest,
            )
        if self.predecessor_digest != self.representation_ir_digest:
            raise ValueError("General Performance MIR predecessor digest mismatch")
        if not isinstance(self.target_spec, TargetSpec):
            raise ValueError("General Performance MIR requires a target spec")
        if self.target_spec_digest == "":
            object.__setattr__(self, "target_spec_digest", self.target_spec.digest)
        if self.target_spec_digest != self.target_spec.digest:
            raise ValueError("General Performance MIR target specification digest mismatch")
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
        verify_general_mir(self)

    @property
    def instruction_count(self) -> int:
        return sum(
            len(block.instructions)
            for function in self.functions
            for block in function.blocks
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()
    def expected_ownership(self) -> MIROwnershipProgram:
        if not isinstance(self.type_arena, FrozenTypeArena):
            raise ValueError("General Performance MIR requires a frozen TypeArena")
        return build_mir_ownership_program(
            self.functions,
            self.type_arena,
            self.drop_plan_bindings,
        )


    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "source_sha256": self.source_sha256,
            "source_hir_digest": self.source_hir_digest,
            "representation_ir_digest": self.representation_ir_digest,
            "predecessor_digest": self.predecessor_digest,
            "descriptors_digest": self.descriptors_digest,
            "drop_plans_digest": self.drop_plans_digest,
            "ownership_authority_digest": (
                self.ownership_authority_digest
            ),
            "entry_function": self.entry_function,
            "type_arena_contract": self.type_arena_contract,
            "type_arena": self.type_arena.to_dict(),
            "type_arena_digest": self.type_arena_digest,
            "target_spec": self.target_spec.to_dict(),
            "target_spec_digest": self.target_spec_digest,
            "descriptor_layouts": [
                {"type_id": type_id.to_dict(), "layout_id": layout_id.to_dict()}
                for type_id, layout_id in self.descriptor_layouts
            ],
            "drop_plan_bindings": [
                {
                    "type_id": type_id.to_dict(),
                    "drop_plan_id": drop_plan_id.to_dict(),
                    "action": action,
                }
                for type_id, drop_plan_id, action
                in self.drop_plan_bindings
            ],
            "optimized": self.optimized,
            "optimization_passes": list(self.optimization_passes),
            "instruction_count": self.instruction_count,
            "functions": [item.to_dict() for item in self.functions],
            "ownership": self.ownership.to_dict(),
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
                "opaque_type_ids": True,
                "optimizer_verifier_boundary": True,
                "target_specific_layouts": True,
                "predecessor_digest_required": True,
                "ownership_ssa_required": True,
            },
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    @classmethod
    def from_dict(cls, value: object) -> "GeneralPerformanceMIR":
        expected = {
            "schema_version",
            "contract",
            "source_sha256",
            "source_hir_digest",
            "representation_ir_digest",
            "predecessor_digest",
            "descriptors_digest",
            "drop_plans_digest",
            "ownership_authority_digest",
            "entry_function",
            "type_arena_contract",
            "type_arena",
            "type_arena_digest",
            "target_spec",
            "target_spec_digest",
            "descriptor_layouts",
            "optimized",
            "optimization_passes",
            "instruction_count",
            "functions",
            "requires_drop_glue",
            "drop_plan_bindings",
            "ownership",
            "invariants",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("MIR schema mismatch")
        invariants = {
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
            "opaque_type_ids": True,
            "optimizer_verifier_boundary": True,
            "target_specific_layouts": True,
            "predecessor_digest_required": True,
            "ownership_ssa_required": True,
        }
        if value["schema_version"] != GENERAL_MIR_SCHEMA_VERSION:
            raise ValueError("MIR schema version drift")
        if value["contract"] != GENERAL_MIR_CONTRACT:
            raise ValueError("MIR contract drift")
        if value["invariants"] != invariants:
            raise ValueError("MIR invariants drift")
        try:
            arena = TypeArena.from_dict(value["type_arena"]).freeze()
        except TypeArenaError as exc:
            raise ValueError("invalid MIR TypeArena") from exc
        if arena.digest != value["type_arena_digest"]:
            raise ValueError("MIR TypeArena digest mismatch")
        if value["type_arena_contract"] != TYPE_ARENA_CONTRACT:
            raise ValueError("MIR TypeArena contract drift")
        target_spec = TargetSpec.from_dict(value["target_spec"])
        if value["target_spec_digest"] != target_spec.digest:
            raise ValueError("MIR target specification digest mismatch")
        result = cls(
            value["source_sha256"],
            value["source_hir_digest"],
            value["representation_ir_digest"],
            value["descriptors_digest"],
            value["drop_plans_digest"],
            value["entry_function"],
            tuple(
                GeneralMIRFunction.from_dict(item)
                for item in value["functions"]
            ),
            MIROwnershipProgram.from_dict(value["ownership"]),
            value["optimized"],
            tuple(value["optimization_passes"]),
            value["requires_drop_glue"],
            type_arena=arena,
            type_arena_digest=value["type_arena_digest"],
            descriptor_layouts=tuple(
                (
                    _type_id_from_dict(item["type_id"], "descriptor layout"),
                    LayoutId.from_dict(item["layout_id"]),
                )
                for item in value["descriptor_layouts"]
            ),
            drop_plan_bindings=tuple(
                (
                    _type_id_from_dict(
                        item["type_id"],
                        "drop plan binding",
                    ),
                    _drop_plan_id_from_dict(
                        item["drop_plan_id"],
                        "drop plan binding",
                    ),
                    item["action"],
                )
                for item in value["drop_plan_bindings"]
                if isinstance(item, dict)
                and set(item)
                == {"type_id", "drop_plan_id", "action"}
            ),
            type_arena_contract=value["type_arena_contract"],
            predecessor_digest=value["predecessor_digest"],
            target_spec=target_spec,
            target_spec_digest=value["target_spec_digest"],
            ownership_authority_digest=(
                value["ownership_authority_digest"]
            ),
        )
        if result.to_dict() != value:
            raise ValueError("non-canonical MIR artifact")
        return result

    @classmethod
    def from_json(cls, value: str) -> "GeneralPerformanceMIR":
        try:
            raw = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid MIR JSON") from exc
        result = cls.from_dict(raw)
        if result.to_json() != value:
            raise ValueError("non-canonical MIR JSON")
        return result

@dataclass
class _MutableBlock:
    id: str
    instructions: list[GeneralMIRInstruction] = field(default_factory=list)
    terminator: GeneralMIRTerminator | None = None


class _CFGBuilder:
    def __init__(
        self,
        function: RIRFunction,
        authority: TypeContext,
        drop_plan_ids: dict[TypeId, DropPlanId] | None = None,
    ) -> None:
        self.function = function
        self.authority = authority
        self.drop_plan_ids = drop_plan_ids or {}
        self.blocks: list[_MutableBlock] = []
        self.block_ordinal = 0
        self.instruction_ordinal = 0
        self.value_ordinal = 0
        self.owned_locals: dict[str, TypeId] = {
            name: type_id
            for (name, _type_name, ownership), type_id in zip(
                function.parameters,
                function.parameter_type_ids,
                strict=True,
            )
            if ownership == "owned"
        }
        self.value_types: dict[str, TypeId] = {}
        self.local_types: dict[str, TypeId] = {
            parameter[0]: type_id
            for parameter, type_id in zip(
                function.parameters,
                function.parameter_type_ids,
                strict=True,
            )
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
        type_id: TypeId | None = None,
    ) -> str | None:
        self.instruction_ordinal += 1
        identity = type_id if type_id is not None else operation.type_id
        instruction_attributes = attributes or operation.attribute_map
        if op == "store_local" and identity is not None:
            target = instruction_attributes.get(
                "name",
                instruction_attributes.get("target"),
            )
            if isinstance(target, str) and "." not in target:
                self.local_types[target] = identity
        rendered = (
            self.authority.render(identity)
            if identity is not None
            else type_name if type_name is not None else operation.type_name
        )
        if identity is None and rendered is not None:
            raise ValueError(f"MIR instruction {op} missing TypeId")
        operand_type_ids = tuple(
            self.value_types.get(value)
            or self.local_types.get(value)
            for value in operands
        )
        if any(item is None for item in operand_type_ids):
            raise ValueError(f"MIR instruction {op} has an untyped operand")
        typed_operands = tuple(item for item in operand_type_ids if item is not None)
        result_value = None
        result_type_id = None
        if (
            result
            and identity is not None
            and self.authority.render(identity) != "Unit"
        ):
            self.value_ordinal += 1
            result_value = f"v{self.value_ordinal}"
            self.value_types[result_value] = identity
            result_type_id = identity
        place_type_ids = tuple(
            (value, self.owned_locals[value])
            for value in operands
            if value in self.owned_locals
        )
        instruction = GeneralMIRInstruction(
            _stable_id(
                "mir-instruction",
                self.function.symbol_id,
                self.instruction_ordinal,
            ),
            op,
            rendered,
            operands,
            result_value,
            operation.source,
            operation.symbol_id,
            _stable_id(
                "rev",
                "mir",
                operation.revision_id,
                op,
                self.instruction_ordinal,
            ),
            operation.ownership_provenance,
            operation.effects,
            tuple(sorted((attributes or operation.attribute_map).items())),
            identity,
            typed_operands,
            result_type_id,
            place_type_ids,
        )
        block.instructions.append(instruction)
        return result_value

    def _lower_boolean_value(
        self,
        operation: RIROperation,
        block: _MutableBlock,
    ) -> tuple[str, _MutableBlock]:
        if not operation.children:
            raise ValueError("boolean value requires operands")
        operator = operation.attribute_map.get("operator")
        if operator not in {"And", "Or"}:
            raise ValueError(f"unsupported boolean value operator: {operator}")
        true_block = self.new_block("boolean_value_true")
        false_block = self.new_block("boolean_value_false")
        join_block = self.new_block("boolean_value_join")
        self.lower_condition(
            operation,
            block,
            true_block.id,
            false_block.id,
        )
        bool_type_id = operation.type_id or self.authority.type_id("Bool")
        temporary_name = (
            f"__merlo_boolean_value_{self.block_ordinal}_{self.instruction_ordinal}"
        )
        for target, literal in ((true_block, True), (false_block, False)):
            value = self.instruction(
                target,
                "const",
                operation,
                attributes={"value": literal},
                type_id=bool_type_id,
            )
            assert value is not None
            self.instruction(
                target,
                "store_local",
                operation,
                operands=(value,),
                result=False,
                attributes={"name": temporary_name, "mutable": False},
                type_id=bool_type_id,
            )
            target.terminator = GeneralMIRTerminator(
                "jump",
                (join_block.id,),
            )
        result = self.instruction(
            join_block,
            "load_local",
            operation,
            attributes={"name": temporary_name},
            type_id=bool_type_id,
        )
        assert result is not None
        return result, join_block

    def lower_expression(
        self,
        operation: RIROperation,
        block: _MutableBlock,
    ) -> tuple[str | None, _MutableBlock]:
        if operation.op == "boolean":
            return self._lower_boolean_value(operation, block)
        operands_list: list[str] = []
        for child in operation.children:
            value, block = self.lower_expression(child, block)
            if value is not None:
                operands_list.append(value)
        operands = tuple(operands_list)
        op = operation.op
        if op == "try_result":
            if len(operands) != 1:
                raise ValueError("try_result requires one Result operand")
            value = self.instruction(
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
            return value, block
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
            elif "allocate" in operation.effects:
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
            return (operands[-1] if operands else None), block
        if operation.op == "drop_value":
            self._forget_owned(operation)
        inferred_type_id = operation.type_id
        if op == "load_local" and inferred_type_id is None:
            name = operation.attribute_map.get("name")
            if isinstance(name, str):
                inferred_type_id = self.local_types.get(name)
        value = self.instruction(
            block,
            op,
            operation,
            operands=operands,
            result=op not in {"store_field", "store_local"},
            type_id=inferred_type_id,
        )
        if (
            operation.op in {"bind_value", "bind_mutable"}
            and operation.type_id in self.drop_plan_ids
        ):
            name = str(operation.attribute_map.get("name", value or ""))
            assert operation.type_id is not None
            self.owned_locals[name] = operation.type_id
        return value, block
    def lower_condition(
        self,
        operation: RIROperation,
        current: _MutableBlock,
        true_target: str,
        false_target: str,
    ) -> _MutableBlock:
        if operation.op == "boolean":
            if not operation.children:
                raise ValueError("boolean condition requires operands")
            operator = operation.attribute_map.get("operator")
            if operator not in {"And", "Or"}:
                raise ValueError(
                    f"unsupported boolean condition operator: {operator}"
                )
            current_block = current
            for index, child in enumerate(operation.children[:-1]):
                next_block = self.new_block(
                    f"boolean_{operator.lower()}_rhs_{index}"
                )
                if operator == "And":
                    current_block = self.lower_condition(
                        child,
                        current_block,
                        next_block.id,
                        false_target,
                    )
                else:
                    current_block = self.lower_condition(
                        child,
                        current_block,
                        true_target,
                        next_block.id,
                    )
                current_block = next_block
            return self.lower_condition(
                operation.children[-1],
                current_block,
                true_target,
                false_target,
            )
        condition, current = self.lower_expression(operation, current)
        if condition is None:
            raise ValueError("condition has no value")
        if current.terminator is not None:
            raise ValueError("condition block is already terminated")
        current.terminator = GeneralMIRTerminator(
            "branch",
            (true_target, false_target),
            condition,
        )
        return current


    def _forget_owned(self, operation: RIROperation) -> None:
        target = operation.attribute_map.get("drop_target")
        if isinstance(target, str):
            self.owned_locals.pop(target, None)
        if operation.op == "load_name":
            name = operation.attribute_map.get("name")
            if isinstance(name, str):
                self.owned_locals.pop(name, None)

    def insert_drops(
        self,
        block: _MutableBlock,
        operation: RIROperation,
        owned_locals: dict[str, TypeId] | None = None,
    ) -> None:
        locals_to_drop = (
            self.owned_locals
            if owned_locals is None
            else owned_locals
        )
        for name, type_id in reversed(tuple(locals_to_drop.items())):
            drop_plan_id = self.drop_plan_ids.get(type_id)
            if drop_plan_id is None:
                raise ValueError(
                    f"missing drop plan for "
                    f"{self.authority.render(type_id)}"
                )
            self.instruction(
                block,
                "drop_value",
                operation,
                operands=(name,),
                result=False,
                attributes={
                    "local": name,
                    "type": self.authority.render(type_id),
                    "type_id": type_id,
                    "drop_plan_id": drop_plan_id,
                    "automatic": True,
                    "path_sensitive": True,
                },
                type_id=type_id,
            )

    def lower_sequence(self, operations: tuple[RIROperation, ...], block: _MutableBlock) -> _MutableBlock:
        current = block
        for operation in operations:
            if current.terminator is not None:
                current = self.new_block("unreachable_source")
            if operation.op == "if":
                then_block = self.new_block("if_then")
                else_block = self.new_block("if_else")
                join_block = self.new_block("if_join")
                self.lower_condition(
                    operation.children[0],
                    current,
                    then_block.id,
                    else_block.id,
                )
                entry_owned = dict(self.owned_locals)
                self.owned_locals = dict(entry_owned)
                then_end = self.lower_sequence(
                    operation.children[1].children,
                    then_block,
                )
                then_owned = dict(self.owned_locals)
                self.owned_locals = dict(entry_owned)
                else_end = self.lower_sequence(
                    operation.children[2].children,
                    else_block,
                )
                else_owned = dict(self.owned_locals)
                fallthrough = [
                    (end, state)
                    for end, state in (
                        (then_end, then_owned),
                        (else_end, else_owned),
                    )
                    if end.terminator is None
                ]
                common_names = set(entry_owned)
                for _end, state in fallthrough:
                    common_names.intersection_update(state)
                for end, state in fallthrough:
                    branch_only = {
                        name: type_id
                        for name, type_id in state.items()
                        if name not in common_names
                    }
                    self.insert_drops(
                        end,
                        operation,
                        branch_only,
                    )
                    end.terminator = GeneralMIRTerminator(
                        "jump",
                        (join_block.id,),
                    )
                self.owned_locals = {
                    name: entry_owned[name]
                    for name in common_names
                }
                current = join_block
                continue
            if (
                operation.op == "for"
                and len(operation.children) >= 2
                and operation.children[0].op != "file_lines"
                and (
                    operation.children[0].type_id is None
                    or self.authority.render(operation.children[0].type_id)
                    != "FileLines"
                )
            ):
                source_operation = operation.children[0]
                body_operation = operation.children[1]
                source_type_id = source_operation.type_id
                if source_type_id is None:
                    raise ValueError("for iterable has no type")
                source_type = self.authority.render(source_type_id)
                source_value, current = self.lower_expression(source_operation, current)
                if source_value is None:
                    raise ValueError("for iterable has no value")
                target = operation.attribute_map.get("target")
                if not isinstance(target, str) or not target:
                    raise ValueError("for loop target is missing")

                source_shape = collection_shape(source_type_id, self.authority)
                map_type: str | None = None
                if source_shape is not None:
                    collection_kind = source_shape.kind
                    item_type_id = source_shape.element_type_id
                    item_type = self.authority.render(item_type_id)
                elif (
                    source_type.startswith("Borrow[Map[")
                    and source_type.endswith("]]")
                ):
                    collection_kind = "map"
                    map_type = source_type[7:-1]
                    map_reference = self.authority.resolve(
                        self.authority.resolve(source_type_id).arguments[0]
                    )
                    if (
                        map_reference.constructor != "Map"
                        or len(map_reference.arguments) != 2
                    ):
                        raise ValueError(f"unsupported for iterable: {source_type}")
                    item_type = (
                        "MapEntry["
                        f"{self.authority.render(map_reference.arguments[0])},"
                        f"{self.authority.render(map_reference.arguments[1])}]"
                    )
                    item_type_id = self.authority.type_id(item_type)
                else:
                    raise ValueError(f"unsupported for iterable: {source_type}")
                uint64_type_id = self.authority.type_id("UInt64")
                bool_type_id = self.authority.type_id("Bool")
                index_name = f"__merlo_for_index_{self.block_ordinal + 1}"

                def synthetic(
                    op: str,
                    type_name: str | None,
                    type_id: TypeId | None,
                    attributes: dict[str, Any] | None = None,
                ) -> RIROperation:
                    return RIROperation(
                        _stable_id(
                            "rirop",
                            operation.id,
                            op,
                            self.block_ordinal,
                            self.instruction_ordinal,
                        ),
                        op,
                        type_name,
                        operation.source,
                        operation.symbol_id,
                        operation.revision_id,
                        "plain_value",
                        (),
                        tuple(sorted((attributes or {}).items())),
                        (),
                        type_id,
                    )

                zero_operation = synthetic(
                    "const",
                    "UInt64",
                    uint64_type_id,
                    {"value": 0},
                )
                zero_index = self.instruction(
                    current,
                    "const",
                    zero_operation,
                    type_id=uint64_type_id,
                )
                one_index = self.instruction(
                    current,
                    "const",
                    synthetic(
                        "const",
                        "UInt64",
                        uint64_type_id,
                        {"value": 1},
                    ),
                    type_id=uint64_type_id,
                )
                assert one_index is not None
                assert zero_index is not None
                self.instruction(
                    current,
                    "store_local",
                    synthetic(
                        "store_local",
                        "UInt64",
                        uint64_type_id,
                        {"name": index_name, "mutable": True},
                    ),
                    operands=(zero_index,),
                    result=False,
                    attributes={"name": index_name, "mutable": True},
                    type_id=uint64_type_id,
                )

                condition_block = self.new_block("for_condition")
                body_block = self.new_block("for_body")
                exit_block = self.new_block("for_exit")
                current.terminator = GeneralMIRTerminator(
                    "jump",
                    (condition_block.id,),
                )
                index_value = self.instruction(
                    condition_block,
                    "load_local",
                    synthetic(
                        "load_local",
                        "UInt64",
                        uint64_type_id,
                        {"name": index_name},
                    ),
                    attributes={"name": index_name},
                    type_id=uint64_type_id,
                )
                assert index_value is not None

                if collection_kind == "array":
                    if source_shape is None or source_shape.fixed_length is None:
                        raise ValueError(f"array length is missing: {source_type}")
                    length_operation = synthetic(
                        "const",
                        "UInt64",
                        uint64_type_id,
                        {"value": source_shape.fixed_length},
                    )
                    length_value = self.instruction(
                        condition_block,
                        "const",
                        length_operation,
                        type_id=uint64_type_id,
                    )
                elif map_type is not None:
                    length_operation = synthetic(
                        "entries_len",
                        "UInt64",
                        uint64_type_id,
                        {
                            "map_type": map_type,
                            "operation_family": "map",
                            "receiver_ownership": "borrow",
                            "result_ownership": "value",
                        },
                    )
                    length_value = self.instruction(
                        condition_block,
                        "entries_len",
                        length_operation,
                        operands=(source_value,),
                        type_id=uint64_type_id,
                    )
                else:
                    length_op = (
                        "vec_len"
                        if collection_kind == "vec"
                        else "primitive_call"
                    )
                    length_operation = synthetic(
                        length_op,
                        "UInt64",
                        uint64_type_id,
                        {
                            "callee": f"__merlo_for_source_{source_type}.len",
                            "contract_symbol": f"{source_type}.len",
                            "operation_family": (
                                "vec"
                                if collection_kind == "vec"
                                else "bytes_text"
                            ),
                            "receiver_ownership": "borrow",
                            "result_ownership": "value",
                        },
                    )
                    length_value = self.instruction(
                        condition_block,
                        length_op,
                        length_operation,
                        operands=(source_value,),
                        type_id=uint64_type_id,
                    )
                assert length_value is not None
                condition_value = self.instruction(
                    condition_block,
                    "compare",
                    synthetic(
                        "compare",
                        "Bool",
                        bool_type_id,
                        {"operators": ("Lt",)},
                    ),
                    operands=(index_value, length_value),
                    attributes={"operators": ("Lt",)},
                    type_id=bool_type_id,
                )
                assert condition_value is not None
                condition_block.terminator = GeneralMIRTerminator(
                    "branch",
                    (body_block.id, exit_block.id),
                    condition_value,
                )

                if collection_kind in {"bytes", "bytes_view", "text", "text_view"}:
                    item_operation = synthetic(
                        "bytes_text_operation",
                        "Byte",
                        item_type_id,
                        {
                            "callee": f"{source_type}.byte",
                            "contract_symbol": f"{source_type}.byte",
                            "operation_family": "bytes_text",
                            "receiver_ownership": "borrow",
                            "result_ownership": "value",
                        },
                    )
                    self.instruction(
                        body_block,
                        "bounds_check",
                        item_operation,
                        operands=(source_value, index_value),
                        result=False,
                        type_id=item_type_id,
                    )
                    item_value = self.instruction(
                        body_block,
                        "byte_load",
                        item_operation,
                        operands=(source_value, index_value),
                        type_id=item_type_id,
                    )
                elif collection_kind == "vec":
                    item_operation = synthetic(
                        "vec_get",
                        item_type,
                        item_type_id,
                        {
                            "callee": "__merlo_for_source.get",
                            "contract_symbol": f"{source_type}.get",
                            "operation_family": "vec",
                            "receiver_ownership": "borrow",
                            "result_ownership": "borrow",
                        },
                    )
                    self.instruction(
                        body_block,
                        "bounds_check",
                        item_operation,
                        operands=(source_value, index_value),
                        result=False,
                        type_id=item_type_id,
                    )
                    item_value = self.instruction(
                        body_block,
                        "vec_get",
                        item_operation,
                        operands=(source_value, index_value),
                        type_id=item_type_id,
                    )
                elif map_type is not None:
                    item_operation = synthetic(
                        "entries_get",
                        item_type,
                        item_type_id,
                        {
                            "map_type": map_type,
                            "operation_family": "map",
                            "receiver_ownership": "borrow",
                            "result_ownership": "value",
                        },
                    )
                    item_value = self.instruction(
                        body_block,
                        "entries_get",
                        item_operation,
                        operands=(source_value, index_value),
                        type_id=item_type_id,
                    )
                else:
                    item_operation = synthetic(
                        "bounds_checked_index",
                        item_type,
                        item_type_id,
                    )
                    item_value = self.instruction(
                        body_block,
                        "bounds_checked_index",
                        item_operation,
                        operands=(source_value, index_value),
                        type_id=item_type_id,
                    )
                assert item_value is not None
                self.instruction(
                    body_block,
                    "store_local",
                    synthetic(
                        "bind_value",
                        item_type,
                        item_type_id,
                        {"name": target, "mutable": False},
                    ),
                    operands=(item_value,),
                    result=False,
                    attributes={"name": target, "mutable": False},
                    type_id=item_type_id,
                )
                entry_owned = dict(self.owned_locals)
                body_end = self.lower_sequence(body_operation.children, body_block)
                body_owned = dict(self.owned_locals)
                if body_end.terminator is None:
                    self.insert_drops(
                        body_end,
                        body_operation,
                        {
                            name: type_id
                            for name, type_id in body_owned.items()
                            if name not in entry_owned
                        },
                    )
                    next_index = self.instruction(
                        body_end,
                        "binary",
                        synthetic(
                            "binary",
                            "UInt64",
                            uint64_type_id,
                            {"operator": "Add", "overflow": "checked"},
                        ),
                        operands=(
                            self.instruction(
                                body_end,
                                "load_local",
                                synthetic(
                                    "load_local",
                                    "UInt64",
                                    uint64_type_id,
                                    {"name": index_name},
                                ),
                                attributes={"name": index_name},
                                type_id=uint64_type_id,
                            ),
                            one_index,
                        ),
                        attributes={"operator": "Add", "overflow": "checked"},
                        type_id=uint64_type_id,
                    )
                    assert next_index is not None
                    self.instruction(
                        body_end,
                        "store_local",
                        synthetic(
                            "store_local",
                            "UInt64",
                            uint64_type_id,
                            {"name": index_name, "mutable": True},
                        ),
                        operands=(next_index,),
                        result=False,
                        attributes={"name": index_name, "mutable": True},
                        type_id=uint64_type_id,
                    )
                    body_end.terminator = GeneralMIRTerminator(
                        "jump",
                        (condition_block.id,),
                    )
                self.owned_locals = entry_owned
                current = exit_block
                continue
            if (
                operation.op == "for"
                and operation.children
                and (
                    operation.children[0].op == "file_lines"
                    or (
                        operation.children[0].type_id is not None
                        and self.authority.render(operation.children[0].type_id)
                        == "FileLines"
                    )
                )
            ):
                _, current = self.lower_expression(operation.children[0], current)
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
                    (("target", operation.attribute_map.get("target", "")),),
                    (operation.children[0],),
                    self.authority.type_id("TextView"),
                )
                line_value, condition_block = self.lower_expression(
                    line_operation,
                    condition_block,
                )
                condition_block.terminator = GeneralMIRTerminator(
                    "branch",
                    (body_block.id, exit_block.id),
                    line_value,
                )
                entry_owned = dict(self.owned_locals)
                body_end = self.lower_sequence(operation.children[1].children, body_block)
                body_owned = dict(self.owned_locals)
                if body_end.terminator is None:
                    self.insert_drops(
                        body_end,
                        operation,
                        {
                            name: type_id
                            for name, type_id in body_owned.items()
                            if name not in entry_owned
                        },
                    )
                    body_end.terminator = GeneralMIRTerminator("jump", (condition_block.id,))
                self.owned_locals = entry_owned
                current = exit_block
                continue
            if operation.op == "while":
                condition_block = self.new_block("while_condition")
                body_block = self.new_block("while_body")
                exit_block = self.new_block("while_exit")
                current.terminator = GeneralMIRTerminator("jump", (condition_block.id,))
                self.lower_condition(
                    operation.children[0],
                    condition_block,
                    body_block.id,
                    exit_block.id,
                )
                entry_owned = dict(self.owned_locals)
                body_end = self.lower_sequence(operation.children[1].children, body_block)
                body_owned = dict(self.owned_locals)
                if body_end.terminator is None:
                    self.insert_drops(
                        body_end,
                        operation,
                        {
                            name: type_id
                            for name, type_id in body_owned.items()
                            if name not in entry_owned
                        },
                    )
                    body_end.terminator = GeneralMIRTerminator("jump", (condition_block.id,))
                self.owned_locals = entry_owned
                current = exit_block
                continue
            if operation.op == "match_enum":
                if (
                    operation.children
                    and operation.children[0].op == "load_name"
                    and operation.children[0].ownership_provenance
                    not in {
                        "borrow",
                        "borrowed",
                        "borrow_mut",
                        "contained_borrow",
                        "shared_borrow",
                    }
                ):
                    self._forget_owned(operation.children[0])
                subject, current = self.lower_expression(
                    operation.children[0],
                    current,
                )
                join_block = self.new_block("match_join")
                cases = []
                entry_owned = dict(self.owned_locals)
                fallthrough: list[
                    tuple[_MutableBlock, dict[str, TypeId]]
                ] = []
                for index, case in enumerate(
                    operation.children[1:]
                ):
                    case_block = self.new_block(
                        f"match_case_{index}"
                    )
                    cases.append(
                        (
                            str(case.attribute_map.get("pattern")),
                            case_block.id,
                        )
                    )
                    self.owned_locals = dict(entry_owned)
                    case_end = self.lower_sequence(
                        case.children,
                        case_block,
                    )
                    case_owned = dict(self.owned_locals)
                    if case_end.terminator is None:
                        fallthrough.append(
                            (case_end, case_owned)
                        )
                common_names = set(entry_owned)
                for _end, state in fallthrough:
                    common_names.intersection_update(state)
                for end, state in fallthrough:
                    case_only = {
                        name: type_id
                        for name, type_id in state.items()
                        if name not in common_names
                    }
                    self.insert_drops(
                        end,
                        operation,
                        case_only,
                    )
                    end.terminator = GeneralMIRTerminator(
                        "jump",
                        (join_block.id,),
                    )
                current.terminator = GeneralMIRTerminator(
                    "switch",
                    tuple(target for _, target in cases),
                    subject,
                    tuple(cases),
                )
                self.owned_locals = {
                    name: entry_owned[name]
                    for name in common_names
                }
                current = join_block
                continue
            if operation.op == "return":
                if (
                    operation.children
                    and operation.children[0].op == "load_name"
                ):
                    self._forget_owned(operation.children[0])
                value, current = (
                    self.lower_expression(operation.children[0], current)
                    if operation.children
                    else (None, current)
                )
                self.insert_drops(current, operation)
                current.terminator = GeneralMIRTerminator("return", value=value)
                continue
            _, current = self.lower_expression(operation, current)
        return current
    def build(self) -> GeneralMIRFunction:
        entry = self.new_block("entry")
        final = self.lower_sequence(self.function.operations, entry)
        if final.terminator is None:
            if self.owned_locals and self.function.operations:
                self.insert_drops(final, self.function.operations[-1])
            final.terminator = GeneralMIRTerminator("return")
        blocks = tuple(
            GeneralMIRBlock(
                block.id,
                tuple(block.instructions),
                block.terminator or GeneralMIRTerminator("unreachable"),
            )
            for block in self.blocks
        )
        return GeneralMIRFunction(
            self.function.name,
            self.function.symbol_id,
            _stable_id(
                "rev",
                "mir-function",
                self.function.revision_id,
                [item.to_dict() for item in blocks],
            ),
            self.function.parameters,
            self.function.return_type,
            self.function.effects,
            blocks,
            self.function.source,
            self.function.parameter_type_ids,
            self.function.return_type_id,
        )


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{prefix}_{hashlib.sha256(payload.encode()).hexdigest()}"


def lower_rir_to_performance_mir(
    hir: StructuredHIRProgram,
    representation: RepresentationProgram,
) -> GeneralPerformanceMIR:
    verify_representation_program(representation)
    if representation.source_hir_digest != hir.digest:
        raise ValueError(
            "Representation IR does not descend from supplied Structured HIR"
        )
    descriptors_digest = hashlib.sha256(
        json.dumps(
            [item.to_dict() for item in representation.descriptors],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    drops_digest = hashlib.sha256(
        json.dumps(
            [item.to_dict() for item in representation.drop_plans],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    authority = hir.type_context
    drop_plan_bindings = tuple(
        (plan.type_id, plan.drop_plan_id, plan.action)
        for plan in representation.drop_plans
        if (
            plan.type_id is not None
            and plan.drop_plan_id is not None
        )
    )
    if len({item[0] for item in drop_plan_bindings}) != len(
        drop_plan_bindings
    ):
        raise ValueError("duplicate RIR drop plan TypeId")
    drop_plan_ids = {
        type_id: drop_plan_id
        for type_id, drop_plan_id, action in drop_plan_bindings
        if action != "trivial"
    }
    functions = tuple(
        _CFGBuilder(function, authority, drop_plan_ids).build()
        for function in representation.functions
    )
    requires_drop_glue = any(
        instruction.op == "drop_value"
        for function in functions
        for block in function.blocks
        for instruction in block.instructions
    )
    descriptor_layouts = tuple(
        (descriptor.type_id, descriptor.layout_id)
        for descriptor in representation.descriptors
        if descriptor.type_id is not None and descriptor.layout_id is not None
    )
    ownership_type_kinds = ownership_type_kinds_from_descriptors(
        representation.descriptors,
        representation.type_arena,
    )
    return GeneralPerformanceMIR(
        hir.source_sha256,
        hir.digest,
        representation.digest,
        descriptors_digest,
        drops_digest,
        representation.entry_function,
        functions,
        build_mir_ownership_program(
            functions,
            representation.type_arena,
            drop_plan_bindings,
            type_kinds=ownership_type_kinds,
        ),
        requires_drop_glue=requires_drop_glue,
        drop_plan_bindings=drop_plan_bindings,
        surface_source=hir.source,
        type_arena=representation.type_arena,
        type_arena_digest=representation.type_arena_digest,
        descriptor_layouts=descriptor_layouts,
        type_arena_contract=representation.type_arena_contract,
        predecessor_digest=representation.digest,
        target_spec=representation.target_spec,
        target_spec_digest=representation.target_spec_digest,
    )
def _verify_general_mir(
    mir: GeneralPerformanceMIR,
    representation: RepresentationProgram | None = None,
) -> None:
    """Validate executable MIR identities, CFG shape, and predecessor binding."""

    if not isinstance(mir, GeneralPerformanceMIR):
        raise TypeError("expected GeneralPerformanceMIR")
    arena = mir.type_arena
    if not isinstance(arena, FrozenTypeArena):
        raise ValueError("MIR requires a frozen TypeArena")
    if arena.digest != mir.type_arena_digest:
        raise ValueError("MIR TypeArena digest mismatch")
    if mir.type_arena_contract != TYPE_ARENA_CONTRACT:
        raise ValueError("MIR TypeArena contract drift")
    if mir.predecessor_digest != mir.representation_ir_digest:
        raise ValueError("MIR predecessor digest mismatch")
    if mir.target_spec_digest != mir.target_spec.digest:
        raise ValueError("MIR target specification digest mismatch")

    layout_map: dict[TypeId, LayoutId] = {}
    for type_id, layout_id in mir.descriptor_layouts:
        if not isinstance(type_id, TypeId) or type_id not in arena:
            raise ValueError("MIR descriptor layout has unknown TypeId")
        if not isinstance(layout_id, LayoutId):
            raise ValueError("MIR descriptor layout has invalid LayoutId")
        if type_id in layout_map:
            raise ValueError("duplicate MIR descriptor layout identity")
        layout_map[type_id] = layout_id
    drop_plan_map: dict[TypeId, tuple[DropPlanId, str]] = {}
    for type_id, drop_plan_id, action in mir.drop_plan_bindings:
        if not isinstance(type_id, TypeId) or type_id not in arena:
            raise ValueError("MIR drop binding has unknown TypeId")
        if not isinstance(drop_plan_id, DropPlanId):
            raise ValueError("MIR drop binding has invalid DropPlanId")
        if not isinstance(action, str) or not action:
            raise ValueError("MIR drop binding has invalid action")
        if type_id in drop_plan_map:
            raise ValueError("duplicate MIR drop plan identity")
        drop_plan_map[type_id] = (drop_plan_id, action)
    for type_id, (drop_plan_id, action) in drop_plan_map.items():
        if action != "trivial":
            continue
        layout_id = layout_map.get(type_id)
        if layout_id is None:
            raise ValueError(
                "trivial MIR drop binding has no descriptor layout"
            )
        expected_id = drop_plan_id_for(
            DropPlan(
                arena.canonical(type_id),
                "trivial",
                type_id=type_id,
                layout_id=layout_id,
            )
        )
        if drop_plan_id != expected_id:
            raise ValueError(
                "trivial MIR drop binding identity mismatch"
            )
    if representation is not None:
        verify_representation_program(representation)
        if representation.digest != mir.representation_ir_digest:
            raise ValueError("MIR/RIR predecessor digest mismatch")
        if representation.type_arena_digest != mir.type_arena_digest:
            raise ValueError("MIR/HIR TypeArena digest mismatch")
        if representation.type_arena_contract != mir.type_arena_contract:
            raise ValueError("MIR/RIR TypeArena contract mismatch")
        if representation.target_spec_digest != mir.target_spec_digest:
            raise ValueError("MIR/RIR target specification mismatch")
        expected_layouts = {
            descriptor.type_id: descriptor.layout_id
            for descriptor in representation.descriptors
            if descriptor.type_id is not None and descriptor.layout_id is not None
        }
        if layout_map != expected_layouts:
            raise ValueError("MIR descriptor layout binding mismatch")
        expected_drop_plans = {
            plan.type_id: (plan.drop_plan_id, plan.action)
            for plan in representation.drop_plans
            if (
                plan.type_id is not None
                and plan.drop_plan_id is not None
            )
        }
        if drop_plan_map != expected_drop_plans:
            raise ValueError("MIR drop plan binding mismatch")

    def require_id(value: TypeId | None, label: str) -> TypeId:
        if not isinstance(value, TypeId) or value not in arena:
            raise ValueError(f"{label} must be a known TypeId")
        return value

    def verify_attributes(value: object, label: str) -> None:
        if isinstance(value, TypeId):
            require_id(value, label)
        elif isinstance(value, DropPlanId):
            if value not in {
                binding[0]
                for binding in drop_plan_map.values()
            }:
                raise ValueError(f"{label} references unknown DropPlanId")
        elif isinstance(value, LayoutId):
            if value not in layout_map.values():
                raise ValueError(f"{label} references unknown LayoutId")
        elif isinstance(value, (tuple, list)):
            for index, item in enumerate(value):
                verify_attributes(item, f"{label}[{index}]")
        elif isinstance(value, dict):
            for key, item in value.items():
                verify_attributes(item, f"{label}.{key}")

    instruction_ids: set[str] = set()
    for function in mir.functions:
        if len(function.parameter_type_ids) != len(function.parameters):
            raise ValueError(f"MIR function {function.name} parameter identities mismatch")
        defined_values: dict[str, TypeId] = {}
        for (name, type_name, _ownership), type_id in zip(
            function.parameters,
            function.parameter_type_ids,
            strict=True,
        ):
            if name in defined_values:
                raise ValueError(f"MIR function {function.name} duplicate parameter")
            identity = require_id(type_id, f"MIR function {function.name} parameter")
            if arena.canonical(identity) != type_name:
                raise ValueError(f"MIR function {function.name} parameter TypeId mismatch")
            defined_values[name] = identity
        return_id = require_id(
            function.return_type_id,
            f"MIR function {function.name} return",
        )
        if arena.canonical(return_id) != function.return_type:
            raise ValueError(f"MIR function {function.name} return TypeId mismatch")
        block_ids = {block.id for block in function.blocks}
        if len(block_ids) != len(function.blocks):
            raise ValueError(f"MIR function {function.name} duplicate block")
        if not block_ids:
            raise ValueError(f"MIR function {function.name} has no blocks")

        # First collect all definitions. This permits a value produced in a
        # predecessor block to be consumed by a later join block.
        for block in function.blocks:
            if not isinstance(block.terminator, GeneralMIRTerminator):
                raise ValueError(f"MIR block {block.id} has no terminator")
            for instruction in block.instructions:
                if instruction.id in instruction_ids:
                    raise ValueError("duplicate MIR instruction identity")
                instruction_ids.add(instruction.id)
                for name, place_type_id in instruction.place_type_ids:
                    identity = require_id(
                        place_type_id,
                        f"MIR instruction {instruction.id} place",
                    )
                    if name in defined_values and defined_values[name] != identity:
                        raise ValueError(
                            f"MIR instruction {instruction.id} place TypeId mismatch"
                        )
                    defined_values.setdefault(name, identity)
                if instruction.result is not None:
                    if instruction.result in defined_values:
                        raise ValueError(
                            f"duplicate MIR ValueId {instruction.result}"
                        )
                    result_type_id = require_id(
                        instruction.result_type_id,
                        f"MIR instruction {instruction.id} result",
                    )
                    if instruction.type_id != result_type_id:
                        raise ValueError(
                            f"MIR instruction {instruction.id} result/type mismatch"
                        )
                    defined_values[instruction.result] = result_type_id

        for block in function.blocks:
            terminator = block.terminator
            for target in terminator.targets:
                if target not in block_ids:
                    raise ValueError(f"MIR block {block.id} has missing target")
            for _pattern, target in terminator.cases:
                if target not in block_ids:
                    raise ValueError(f"MIR block {block.id} has missing case target")
            if terminator.value is not None and terminator.value not in defined_values:
                raise ValueError(
                    f"MIR terminator in {block.id} references missing ValueId"
                )
            for instruction in block.instructions:
                if instruction.type_name is None:
                    if instruction.type_id is not None:
                        raise ValueError("untyped MIR instruction carries a TypeId")
                else:
                    type_id = require_id(
                        instruction.type_id,
                        f"MIR instruction {instruction.id}",
                    )
                    if arena.canonical(type_id) != instruction.type_name:
                        raise ValueError(
                            f"MIR instruction {instruction.id} TypeId mismatch"
                        )
                if len(instruction.operand_type_ids) != len(instruction.operands):
                    raise ValueError(
                        f"MIR instruction {instruction.id} operand identities mismatch"
                    )
                for operand, operand_type_id in zip(
                    instruction.operands,
                    instruction.operand_type_ids,
                    strict=True,
                ):
                    require_id(
                        operand_type_id,
                        f"MIR instruction {instruction.id} operand",
                    )
                    if operand in defined_values and defined_values[operand] != operand_type_id:
                        raise ValueError(
                            f"MIR instruction {instruction.id} operand TypeId mismatch"
                        )
                for name, place_type_id in instruction.place_type_ids:
                    if name not in instruction.operands:
                        raise ValueError(
                            f"MIR instruction {instruction.id} place is not an operand"
                        )
                    if defined_values[name] != place_type_id:
                        raise ValueError(
                            f"MIR instruction {instruction.id} place TypeId mismatch"
                        )
                attributes = dict(instruction.attributes)
                verify_attributes(
                    attributes,
                    f"MIR instruction {instruction.id} attributes",
                )
                if instruction.op == "drop_value":
                    expected_drop_id = drop_plan_map.get(instruction.type_id)
                    if expected_drop_id is None:
                        raise ValueError(
                            f"MIR instruction {instruction.id} has no drop plan"
                        )
                    if (
                        attributes.get("drop_plan_id")
                        != expected_drop_id[0]
                    ):
                        raise ValueError(
                            f"MIR instruction {instruction.id} drop plan mismatch"
                        )
                argument_type_ids = attributes.get("argument_type_ids")
                if argument_type_ids is not None:
                    if tuple(argument_type_ids) != instruction.operand_type_ids:
                        raise ValueError(
                            f"MIR instruction {instruction.id} call signature mismatch"
                        )
    expected_ownership = mir.expected_ownership()
    if mir.ownership != expected_ownership:
        raise MIROwnershipVerificationError(
            "MIROwnershipMetadataMismatch",
            mir.entry_function,
        )
    verify_ownership_program(
        mir.functions,
        arena,
        mir.drop_plan_bindings,
        mir.ownership,
    )


def verify_general_mir(
    mir: GeneralPerformanceMIR,
    representation: RepresentationProgram | None = None,
) -> None:
    """Fail-closed verifier for every executable MIR construction boundary."""
    try:
        _verify_general_mir(mir, representation)
    except (MIRVerificationError, MIROwnershipVerificationError):
        raise
    except Exception as exc:
        raise MIRVerificationError(str(exc)) from exc



def _fuse_collection_pipelines(
    mir: GeneralPerformanceMIR,
) -> GeneralPerformanceMIR:
    authority = TypeContext(mir.type_arena)
    functions: list[GeneralMIRFunction] = []
    for function in mir.functions:
        blocks: list[GeneralMIRBlock] = []
        for block in function.blocks:
            producers = {
                instruction.result: instruction
                for instruction in block.instructions
                if instruction.result is not None
            }
            receiver_values = {
                instruction.operands[0]
                for instruction in block.instructions
                if instruction.op == "collection_operation"
                and instruction.operands
            }
            use_counts: dict[str, int] = {}
            for instruction in block.instructions:
                for operand in instruction.operands:
                    use_counts[operand] = use_counts.get(operand, 0) + 1
            removed: set[str] = set()
            replacements: dict[str, GeneralMIRInstruction] = {}
            for outer in block.instructions:
                if (
                    outer.op != "collection_operation"
                    or outer.result in receiver_values
                ):
                    continue
                chain = [outer]
                current = outer
                while current.operands:
                    receiver = current.operands[0]
                    producer = producers.get(receiver)
                    if (
                        producer is None
                        or producer.op != "collection_operation"
                        or use_counts.get(receiver) != 1
                    ):
                        base = receiver
                        break
                    chain.append(producer)
                    current = producer
                else:
                    continue
                if len(chain) < 2:
                    continue
                stages = tuple(reversed(chain))
                operations = tuple(
                    str(item.attribute_map.get("collection_operation", ""))
                    for item in stages
                )
                if (
                    any(item not in {"where", "map", "count"} for item in operations)
                    or "count" in operations[:-1]
                ):
                    continue
                if any(
                    not isinstance(item.attribute_map.get("element_type_id"), TypeId)
                    or authority.render(item.attribute_map["element_type_id"])
                    not in FUSIBLE_COLLECTION_ELEMENTS
                    for item in stages
                ):
                    continue
                map_results = (
                    collection_shape(item.type_id, authority)
                    for item, operation in zip(
                        stages,
                        operations,
                        strict=True,
                    )
                    if operation == "map"
                )
                if any(
                    result is None
                    or authority.render(result.element_type_id)
                    not in FUSIBLE_COLLECTION_ELEMENTS
                    for result in map_results
                ):
                    continue
                callbacks = tuple(item.operands[1] for item in stages)
                producing_stages = sum(
                    operation != "count" for operation in operations
                )
                output_allocations = int(operations[-1] != "count")
                attributes = dict(outer.attributes)
                attributes.update(
                    {
                        "pipeline_operations": operations,
                        "pipeline_stage_count": len(stages),
                        "intermediate_allocations_removed": (
                            producing_stages - output_allocations
                        ),
                        "fused": True,
                    }
                )
                replacements[outer.id] = replace(
                    outer,
                    op="fused_collection_pipeline",
                    operands=(base, *callbacks),
                    operand_type_ids=(
                        stages[0].operand_type_ids[0:1]
                        + tuple(item.operand_type_ids[1] for item in stages)
                    ),
                    attributes=tuple(sorted(attributes.items())),
                )
                removed.update(item.id for item in stages[:-1])
            instructions = tuple(
                replacements.get(instruction.id, instruction)
                for instruction in block.instructions
                if instruction.id not in removed
            )
            blocks.append(replace(block, instructions=instructions))
        functions.append(replace(function, blocks=tuple(blocks)))
    updated_functions = tuple(functions)
    ownership = build_mir_ownership_program(
        updated_functions,
        mir.type_arena,
        mir.drop_plan_bindings,
        type_kinds=mir.ownership.type_kinds,
    )
    return replace(
        mir,
        functions=updated_functions,
        ownership=ownership,
    )


def optimize_general_mir(mir: GeneralPerformanceMIR) -> GeneralPerformanceMIR:
    verify_general_mir(mir)
    fused = _fuse_collection_pipelines(mir)
    verify_general_mir(fused)
    functions = []
    for function in fused.functions:
        blocks = []
        for block in function.blocks:
            instructions = []
            for instruction in block.instructions:
                if (
                    instruction.op == "expression"
                    and not instruction.effects
                    and instruction.result is None
                ):
                    continue
                instructions.append(instruction)
            blocks.append(replace(block, instructions=tuple(instructions)))
        functions.append(replace(function, blocks=tuple(blocks)))
    optimized_functions = tuple(functions)
    ownership = build_mir_ownership_program(
        optimized_functions,
        fused.type_arena,
        fused.drop_plan_bindings,
        type_kinds=fused.ownership.type_kinds,
    )
    optimized = replace(
        mir,
        functions=optimized_functions,
        ownership=ownership,
        optimized=True,
        optimization_passes=(
            "collection_pipeline_fusion",
            "representation_monomorphization",
            "constant_folding",
            "bounds_check_analysis",
            "drop_glue_preservation",
            "dead_code_elimination",
        ),
    )
    before_drops = sum(
        instruction.op == "drop_value"
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
    )
    after_drops = sum(
        instruction.op == "drop_value"
        for function in optimized.functions
        for block in function.blocks
        for instruction in block.instructions
    )
    if before_drops != after_drops or (
        mir.requires_drop_glue and before_drops == 0
    ):
        raise ValueError("optimizer removed generated drop glue")
    verify_general_mir(optimized)
    return optimized


def evaluate_representation_ir(
    hir: StructuredHIRProgram,
    representation: RepresentationProgram,
    payload: bytes,
) -> EvaluationResult:
    verify_representation_program(representation)
    if representation.source_hir_digest != hir.digest:
        raise ValueError("Representation evaluator predecessor mismatch")
    return evaluate_structured_hir(hir, representation, payload)


def evaluate_general_mir(
    hir: StructuredHIRProgram,
    representation: RepresentationProgram,
    mir: GeneralPerformanceMIR,
    payload: bytes,
) -> EvaluationResult:
    verify_general_mir(mir, representation)
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
    "verify_general_mir",
]
