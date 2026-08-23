"""Versioned ownership SSA carried by executable representation MIR."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from merlo.representation_ir import DropPlanId
from merlo.type_arena import FrozenTypeArena, TypeArenaError, TypeId


MIR_OWNERSHIP_SCHEMA_VERSION = 1
MIR_OWNERSHIP_CONTRACT = "merlo.mir-ownership.v1"
MIR_OWNERSHIP_OPERATIONS = frozenset(
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
_POINTS = frozenset({"entry", "before", "after", "terminator", "exit"})
_BORROW_OWNERSHIP = frozenset(
    {
        "borrow",
        "borrowed",
        "borrow_mut",
        "contained_borrow",
        "shared_borrow",
        "shared_contained_borrow",
    }
)
_OWNERSHIP_PREPARATION_OPERATIONS = frozenset(
    {"allocate", "allocate_deferred"}
)
_PARAMETER_OWNERSHIP = _BORROW_OWNERSHIP | frozenset(
    {
        "consuming",
        "copy",
        "move",
        "owned",
        "trivial",
        "unowned",
        "value",
    }
)
_RESULT_OWNERSHIP = _BORROW_OWNERSHIP | frozenset(
    {
        "copy",
        "owned",
        "payload_clone",
        "trivial",
        "unowned",
        "value",
    }
)
_RAW_POINTER_CONSTRUCTORS = frozenset(
    {"RawPointer", "Ptr", "ConstPointer", "MutPointer"}
)
_GUARANTEED_CONSTRUCTORS = frozenset(
    {"Borrow", "Slice", "TextView", "BytesView", "FileLines"}
)
_TRIVIAL_CONSTRUCTORS = frozenset(
    {
        "Unit",
        "Bool",
        "Byte",
        "Int8",
        "UInt8",
        "Int16",
        "UInt16",
        "Int32",
        "UInt32",
        "Int64",
        "UInt64",
        "Float32",
        "Float64",
        "FileError",
        "Inferred",
        "Json",
    }
)
_OWNED_CONSTRUCTORS = frozenset(
    {
        "Text",
        "Path",
        "Bytes",
        "TextBuilder",
        "Vec",
        "Map",
        "Box",
        "FileReader",
        "FileWriter",
        "Fn",
    }
)


class MIROwnershipKind(str, Enum):
    TRIVIAL = "Trivial"
    OWNED = "Owned"
    GUARANTEED = "Guaranteed"
    UNOWNED = "Unowned"


class MIROwnershipVerificationError(ValueError):
    """Stable fail-closed ownership SSA diagnostic."""

    def __init__(self, diagnostic: str, detail: str = "") -> None:
        self.diagnostic = diagnostic
        self.detail = detail
        super().__init__(f"{diagnostic}: {detail}" if detail else diagnostic)


@dataclass(frozen=True)
class MIROwnershipOperation:
    id: str
    op: str
    block_id: str
    instruction_id: str | None
    point: str
    type_id: TypeId
    kind: MIROwnershipKind
    value: str | None = None
    place: str | None = None
    base: str | None = None
    target: str | None = None

    def __post_init__(self) -> None:
        if self.op not in MIR_OWNERSHIP_OPERATIONS:
            raise ValueError(f"unknown MIR ownership operation: {self.op}")
        if self.point not in _POINTS:
            raise ValueError(f"unknown MIR ownership point: {self.point}")
        if not isinstance(self.type_id, TypeId):
            raise ValueError("MIR ownership operation requires TypeId")
        if self.value is None and self.place is None:
            raise ValueError("MIR ownership operation requires value or place")
        if self.op in {"begin_borrow", "end_borrow"} and self.base is None:
            raise ValueError("MIR borrow operation requires one base")

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "op": self.op,
            "block_id": self.block_id,
            "instruction_id": self.instruction_id,
            "point": self.point,
            "type_id": self.type_id.to_dict(),
            "kind": self.kind.value,
            "value": self.value,
            "place": self.place,
            "base": self.base,
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, value: object) -> "MIROwnershipOperation":
        expected = {
            "id",
            "op",
            "block_id",
            "instruction_id",
            "point",
            "type_id",
            "kind",
            "value",
            "place",
            "base",
            "target",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("MIR ownership operation schema mismatch")
        try:
            type_id = TypeId.from_dict(value["type_id"])
            kind = MIROwnershipKind(value["kind"])
        except (TypeArenaError, ValueError) as exc:
            raise ValueError("invalid MIR ownership operation identity") from exc
        return cls(
            value["id"],
            value["op"],
            value["block_id"],
            value["instruction_id"],
            value["point"],
            type_id,
            kind,
            value["value"],
            value["place"],
            value["base"],
            value["target"],
        )


@dataclass(frozen=True)
class MIROwnershipBlock:
    id: str
    operations: tuple[MIROwnershipOperation, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "operations": [item.to_dict() for item in self.operations],
        }

    @classmethod
    def from_dict(cls, value: object) -> "MIROwnershipBlock":
        if not isinstance(value, dict) or set(value) != {"id", "operations"}:
            raise ValueError("MIR ownership block schema mismatch")
        return cls(
            value["id"],
            tuple(MIROwnershipOperation.from_dict(item) for item in value["operations"]),
        )


@dataclass(frozen=True)
class MIROwnershipFunction:
    name: str
    symbol_id: str
    entry_block: str
    blocks: tuple[MIROwnershipBlock, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "symbol_id": self.symbol_id,
            "entry_block": self.entry_block,
            "blocks": [item.to_dict() for item in self.blocks],
        }

    @classmethod
    def from_dict(cls, value: object) -> "MIROwnershipFunction":
        expected = {"name", "symbol_id", "entry_block", "blocks"}
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("MIR ownership function schema mismatch")
        return cls(
            value["name"],
            value["symbol_id"],
            value["entry_block"],
            tuple(MIROwnershipBlock.from_dict(item) for item in value["blocks"]),
        )


@dataclass(frozen=True)
class MIROwnershipProgram:
    type_kinds: tuple[tuple[TypeId, MIROwnershipKind], ...]
    functions: tuple[MIROwnershipFunction, ...]
    schema_version: int = MIR_OWNERSHIP_SCHEMA_VERSION
    contract: str = MIR_OWNERSHIP_CONTRACT

    def __post_init__(self) -> None:
        if self.schema_version != MIR_OWNERSHIP_SCHEMA_VERSION:
            raise ValueError("MIR ownership schema drift")
        if self.contract != MIR_OWNERSHIP_CONTRACT:
            raise ValueError("MIR ownership contract drift")
        if len(dict(self.type_kinds)) != len(self.type_kinds):
            raise ValueError("duplicate MIR ownership type kind")
        if tuple(sorted(self.type_kinds, key=lambda item: str(item[0]))) != self.type_kinds:
            raise ValueError("MIR ownership type kinds must be canonical")

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "type_kinds": [
                {"type_id": type_id.to_dict(), "kind": kind.value}
                for type_id, kind in self.type_kinds
            ],
            "functions": [item.to_dict() for item in self.functions],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, value: object) -> "MIROwnershipProgram":
        expected = {"schema_version", "contract", "type_kinds", "functions"}
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("MIR ownership program schema mismatch")
        try:
            type_kinds = tuple(
                (
                    TypeId.from_dict(item["type_id"]),
                    MIROwnershipKind(item["kind"]),
                )
                for item in value["type_kinds"]
            )
        except (KeyError, TypeArenaError, ValueError) as exc:
            raise ValueError("invalid MIR ownership type kind") from exc
        result = cls(
            type_kinds,
            tuple(MIROwnershipFunction.from_dict(item) for item in value["functions"]),
            value["schema_version"],
            value["contract"],
        )
        if result.to_dict() != value:
            raise ValueError("non-canonical MIR ownership program")
        return result


@dataclass(frozen=True)
class _State:
    values: tuple[tuple[str, str], ...] = ()
    places: tuple[tuple[str, str], ...] = ()
    borrows: tuple[tuple[str, str, str], ...] = ()

    def mutable(self) -> tuple[dict[str, str], dict[str, str], dict[str, tuple[str, str]]]:
        return (
            dict(self.values),
            dict(self.places),
            {name: (status, base) for name, status, base in self.borrows},
        )


def _freeze_state(
    values: dict[str, str],
    places: dict[str, str],
    borrows: dict[str, tuple[str, str]],
) -> _State:
    return _State(
        tuple(sorted(values.items())),
        tuple(sorted(places.items())),
        tuple(sorted((name, status, base) for name, (status, base) in borrows.items())),
    )


def _stable_id(*parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _kind_for_type(
    arena: FrozenTypeArena,
    type_kinds: dict[TypeId, MIROwnershipKind],
    type_id: TypeId,
) -> MIROwnershipKind:
    declared = type_kinds.get(type_id)
    if declared is not None:
        return declared
    reference = arena.resolve(type_id)
    constructor = reference.constructor
    if constructor in _RAW_POINTER_CONSTRUCTORS:
        return MIROwnershipKind.UNOWNED
    if constructor in _GUARANTEED_CONSTRUCTORS:
        return MIROwnershipKind.GUARANTEED
    if constructor in _TRIVIAL_CONSTRUCTORS:
        return MIROwnershipKind.TRIVIAL
    if constructor in _OWNED_CONSTRUCTORS:
        return MIROwnershipKind.OWNED
    child_kinds = tuple(
        _kind_for_type(arena, type_kinds, child)
        for child in reference.arguments
    )
    if MIROwnershipKind.OWNED in child_kinds:
        return MIROwnershipKind.OWNED
    if MIROwnershipKind.GUARANTEED in child_kinds:
        return MIROwnershipKind.GUARANTEED
    return MIROwnershipKind.TRIVIAL


def ownership_type_kinds_from_descriptors(
    descriptors: Iterable[Any],
    arena: FrozenTypeArena,
) -> tuple[tuple[TypeId, MIROwnershipKind], ...]:
    result: dict[TypeId, MIROwnershipKind] = {}
    for descriptor in descriptors:
        type_id = descriptor.type_id
        if not isinstance(type_id, TypeId):
            raise MIROwnershipVerificationError(
                "MIROwnershipTypeAuthorityMissing",
                descriptor.name,
            )
        constructor = arena.resolve(type_id).constructor
        if constructor in _RAW_POINTER_CONSTRUCTORS:
            kind = MIROwnershipKind.UNOWNED
        elif constructor in _GUARANTEED_CONSTRUCTORS:
            kind = MIROwnershipKind.GUARANTEED
        elif descriptor.drop_class != "trivial":
            kind = MIROwnershipKind.OWNED
        else:
            kind = MIROwnershipKind.TRIVIAL
        result[type_id] = kind
    return tuple(sorted(result.items(), key=lambda item: str(item[0])))


def _borrowed_provenance(instruction: Any) -> bool:
    attributes = instruction.attribute_map
    result_ownership = attributes.get("result_ownership")
    if result_ownership in _BORROW_OWNERSHIP:
        return True
    if result_ownership in {"owned", "value"}:
        return False
    if instruction.op == "load_field" and not instruction.operands:
        return False
    return (
        instruction.op
        in {
            "load_local",
            "load_field",
            "vec_get",
            "vec_get_mut",
            "bounds_checked_index",
            "load_enum_payload",
        }
        and instruction.ownership_provenance
        in _BORROW_OWNERSHIP
    )


def _successors(function: Any) -> dict[str, tuple[str, ...]]:
    return {
        block.id: tuple(
            dict.fromkeys((*block.terminator.targets, *(target for _, target in block.terminator.cases)))
        )
        for block in function.blocks
    }


def _predecessors(function: Any) -> dict[str, tuple[str, ...]]:
    result: dict[str, list[str]] = {block.id: [] for block in function.blocks}
    for source, targets in _successors(function).items():
        for target in targets:
            result[target].append(source)
    return {name: tuple(items) for name, items in result.items()}


def _liveness(
    function: Any,
    tracked: frozenset[str],
    *,
    place_loads: bool = False,
    aliases: dict[str, str] | None = None,
    storage_before: dict[
        tuple[str, str],
        frozenset[str],
    ]
    | None = None,
) -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    aliases = aliases or {}
    uses: dict[str, set[str]] = {
        block.id: set() for block in function.blocks
    }
    definitions: dict[str, set[str]] = {
        block.id: set() for block in function.blocks
    }
    for block in function.blocks:
        seen: set[str] = set()
        for instruction in block.instructions:
            if place_loads:
                attributes = instruction.attribute_map
                name = attributes.get("name")
                if (
                    instruction.op == "load_local"
                    and name in tracked
                    and name not in seen
                ):
                    uses[block.id].add(name)
                local = attributes.get("local")
                if (
                    instruction.op == "drop_value"
                    and local in tracked
                    and local not in seen
                ):
                    uses[block.id].add(local)
                target = attributes.get(
                    "name", attributes.get("target")
                )
                if instruction.op == "store_local" and target in tracked:
                    if (
                        storage_before is not None
                        and target
                        in storage_before[
                            (block.id, instruction.id)
                        ]
                    ):
                        if target not in seen:
                            uses[block.id].add(target)
                    else:
                        definitions[block.id].add(target)
                        seen.add(target)
                for value in (
                    *instruction.operands,
                    instruction.result,
                ):
                    base = aliases.get(value)
                    if base in tracked and base not in seen:
                        uses[block.id].add(base)
            else:
                for operand in instruction.operands:
                    for candidate in (operand, aliases.get(operand)):
                        if candidate in tracked and candidate not in seen:
                            uses[block.id].add(candidate)
                if instruction.result in tracked:
                    definitions[block.id].add(instruction.result)
                    seen.add(instruction.result)
                result_source = aliases.get(instruction.result)
                if (
                    result_source in tracked
                    and result_source not in seen
                ):
                    uses[block.id].add(result_source)
        if not place_loads:
            return_value = block.terminator.value
            for candidate in (
                return_value,
                aliases.get(return_value) if return_value is not None else None,
            ):
                if candidate in tracked and candidate not in seen:
                    uses[block.id].add(candidate)
    successors = _successors(function)
    live_in = {
        block.id: frozenset() for block in function.blocks
    }
    live_out = {
        block.id: frozenset() for block in function.blocks
    }
    work = deque(block.id for block in reversed(function.blocks))
    queued = set(work)
    predecessors = _predecessors(function)
    while work:
        block_id = work.popleft()
        queued.discard(block_id)
        outgoing = frozenset().union(
            *(live_in[target] for target in successors[block_id])
        )
        incoming = frozenset(
            uses[block_id]
            | (set(outgoing) - definitions[block_id])
        )
        if outgoing == live_out[block_id] and incoming == live_in[block_id]:
            continue
        live_out[block_id] = outgoing
        live_in[block_id] = incoming
        for predecessor in predecessors[block_id]:
            if predecessor not in queued:
                work.append(predecessor)
                queued.add(predecessor)
    return live_in, live_out


def _operation(
    function: Any,
    block_id: str,
    ordinal: int,
    op: str,
    point: str,
    type_id: TypeId,
    kind: MIROwnershipKind,
    *,
    instruction_id: str | None = None,
    value: str | None = None,
    place: str | None = None,
    base: str | None = None,
    target: str | None = None,
) -> MIROwnershipOperation:
    return MIROwnershipOperation(
        _stable_id(
            MIR_OWNERSHIP_CONTRACT,
            function.symbol_id,
            block_id,
            ordinal,
            op,
            point,
            instruction_id,
            value,
            place,
            base,
            target,
            str(type_id),
            kind.value,
        ),
        op,
        block_id,
        instruction_id,
        point,
        type_id,
        kind,
        value,
        place,
        base,
        target,
    )


def _build_function(
    function: Any,
    arena: FrozenTypeArena,
    type_kinds: dict[TypeId, MIROwnershipKind],
    function_symbols: frozenset[str],
) -> MIROwnershipFunction:
    blocks = {block.id: block for block in function.blocks}
    definitions = {
        instruction.result: instruction
        for block in function.blocks
        for instruction in block.instructions
        if instruction.result is not None
    }
    value_types = {
        value: instruction.result_type_id
        for value, instruction in definitions.items()
    }
    place_types: dict[str, TypeId] = {
        name: type_id
        for (name, _type_name, _ownership), type_id in zip(
            function.parameters,
            function.parameter_type_ids,
            strict=True,
        )
    }
    place_parameter_ownership = {
        name: ownership for name, _type_name, ownership in function.parameters
    }
    for block in function.blocks:
        for instruction in block.instructions:
            if instruction.op != "store_local":
                continue
            target = instruction.attribute_map.get(
                "name", instruction.attribute_map.get("target")
            )
            if not isinstance(target, str) or instruction.type_id is None:
                continue
            place_types.setdefault(target, instruction.type_id)

    take_values: set[str] = set()
    for block in function.blocks:
        for instruction in block.instructions:
            if instruction.op == "move_value":
                take_values.update(instruction.operands)
            if instruction.op == "store_local" and instruction.operands:
                target = instruction.attribute_map.get(
                    "name", instruction.attribute_map.get("target")
                )
                target_type = place_types.get(target)
                source = instruction.operands[0]
                source_instruction = definitions.get(source)
                if (
                    target_type is not None
                    and _kind_for_type(arena, type_kinds, target_type)
                    is MIROwnershipKind.OWNED
                    and (
                        source_instruction is None
                        or not _borrowed_provenance(source_instruction)
                    )
                ):
                    take_values.add(source)
        return_value = block.terminator.value
        if (
            block.terminator.kind == "return"
            and return_value is not None
        ):
            return_type = value_types.get(return_value)
            if (
                return_type is not None
                and _kind_for_type(arena, type_kinds, return_type)
                is MIROwnershipKind.OWNED
            ):
                take_values.add(return_value)

    implicit_sources: dict[tuple[str, str], str] = {}
    block_rank = {
        block.id: index
        for index, block in enumerate(function.blocks)
    }
    for block in function.blocks:
        terminator = block.terminator
        if terminator.kind == "switch" and terminator.value is not None:
            graph = _successors(function)
            reachable_by_target: dict[str, set[str]] = {}
            for target in terminator.targets:
                reachable: set[str] = set()
                pending = [target]
                while pending:
                    candidate = pending.pop()
                    if candidate in reachable:
                        continue
                    reachable.add(candidate)
                    pending.extend(
                        successor
                        for successor in graph[candidate]
                        if block_rank[successor]
                        > block_rank[candidate]
                    )
                reachable_by_target[target] = reachable
            for target, reachable in reachable_by_target.items():
                other_reachable = set().union(
                    *(
                        blocks_reached
                        for other, blocks_reached
                        in reachable_by_target.items()
                        if other != target
                    )
                )
                for block_id in reachable - other_reachable:
                    for instruction in blocks[block_id].instructions:
                        if instruction.op != "load_local":
                            continue
                        name = instruction.attribute_map.get("name")
                        if (
                            isinstance(name, str)
                            and name not in place_types
                            and (block_id, name)
                            not in implicit_sources
                            and instruction.symbol_id
                            not in function_symbols
                        ):
                            implicit_sources[(block_id, name)] = (
                                terminator.value
                            )
        if (
            terminator.kind == "branch"
            and terminator.targets
        ):
            for instruction in block.instructions:
                if (
                    instruction.op != "file_line_next"
                    or instruction.result is None
                ):
                    continue
                target_name = instruction.attribute_map.get(
                    "target"
                )
                if isinstance(target_name, str):
                    source_block = terminator.targets[0]
                    pending = [source_block]
                    visited: set[str] = set()
                    graph = _successors(function)
                    while pending:
                        candidate = pending.pop()
                        if candidate in visited:
                            continue
                        visited.add(candidate)
                        key = (candidate, target_name)
                        existing = implicit_sources.get(key)
                        if existing not in {None, instruction.result}:
                            raise MIROwnershipVerificationError(
                                "MIROwnershipAmbiguousBase",
                                f"{function.name}.{target_name}",
                            )
                        implicit_sources[key] = instruction.result
                        pending.extend(
                            successor
                            for successor in graph[candidate]
                            if block_rank[successor]
                            > block_rank[candidate]
                        )

    place_kinds: dict[str, MIROwnershipKind] = {}
    for name, type_id in place_types.items():
        kind = _kind_for_type(arena, type_kinds, type_id)
        if place_parameter_ownership.get(name) in _BORROW_OWNERSHIP:
            kind = MIROwnershipKind.GUARANTEED
        place_kinds[name] = kind

    value_kinds: dict[str, MIROwnershipKind] = {}
    for block in function.blocks:
        for instruction in block.instructions:
            value = instruction.result
            type_id = instruction.result_type_id
            if value is None or type_id is None:
                continue
            kind = _kind_for_type(arena, type_kinds, type_id)
            name = instruction.attribute_map.get("name")
            static_reference = (
                instruction.op == "load_local"
                and instruction.symbol_id in function_symbols
            )
            implicit_reference = (
                instruction.op == "load_local"
                and isinstance(name, str)
                and (block.id, name) in implicit_sources
            )
            guaranteed_place_load = (
                instruction.op == "load_local"
                and isinstance(name, str)
                and place_kinds.get(name)
                is MIROwnershipKind.GUARANTEED
            )
            borrowed_local_load = (
                instruction.op == "load_local"
                and kind is MIROwnershipKind.OWNED
                and value not in take_values
            )
            if guaranteed_place_load or borrowed_local_load or (
                _borrowed_provenance(instruction)
                and kind is MIROwnershipKind.OWNED
                and (
                    value not in take_values
                    or static_reference
                    or implicit_reference
                )
            ):
                kind = MIROwnershipKind.GUARANTEED
            value_kinds[value] = kind
    all_places = frozenset(place_types)
    parameter_places = frozenset(place_parameter_ownership)
    place_predecessors = _predecessors(function)
    initialized_in = {
        block.id: (
            parameter_places
            if block.id == function.blocks[0].id
            else all_places
        )
        for block in function.blocks
    }
    initialized_out = dict(initialized_in)
    storage_in = dict(initialized_in)
    storage_out = dict(initialized_in)

    def transfer_places(
        block: Any,
        initialized: frozenset[str],
        storage: frozenset[str],
    ) -> tuple[frozenset[str], frozenset[str]]:
        current_initialized = set(initialized)
        current_storage = set(storage)
        for instruction in block.instructions:
            attributes = instruction.attribute_map
            if instruction.op == "store_local":
                target = attributes.get(
                    "name", attributes.get("target")
                )
                if target in place_types:
                    current_storage.add(target)
                    current_initialized.add(target)
            elif (
                instruction.op == "load_local"
                and instruction.result is not None
                and value_kinds.get(instruction.result)
                is MIROwnershipKind.OWNED
            ):
                name = attributes.get("name")
                if name in place_types:
                    current_initialized.discard(name)
            elif instruction.op == "drop_value":
                local = attributes.get("local")
                if local in place_types:
                    current_initialized.discard(local)
        return (
            frozenset(current_initialized),
            frozenset(current_storage),
        )

    changed = True
    while changed:
        changed = False
        for block in function.blocks:
            if block.id == function.blocks[0].id:
                next_initialized = parameter_places
                next_storage = parameter_places
            else:
                block_predecessors = place_predecessors[block.id]
                next_initialized = (
                    frozenset.intersection(
                        *(
                            initialized_out[predecessor]
                            for predecessor in block_predecessors
                        )
                    )
                    if block_predecessors
                    else frozenset()
                )
                next_storage = (
                    frozenset.intersection(
                        *(
                            storage_out[predecessor]
                            for predecessor in block_predecessors
                        )
                    )
                    if block_predecessors
                    else frozenset()
                )
            next_initialized_out, next_storage_out = (
                transfer_places(
                    block,
                    next_initialized,
                    next_storage,
                )
            )
            if (
                next_initialized != initialized_in[block.id]
                or next_storage != storage_in[block.id]
                or next_initialized_out != initialized_out[block.id]
                or next_storage_out != storage_out[block.id]
            ):
                initialized_in[block.id] = next_initialized
                storage_in[block.id] = next_storage
                initialized_out[block.id] = next_initialized_out
                storage_out[block.id] = next_storage_out
                changed = True

    initialized_before: dict[tuple[str, str], frozenset[str]] = {}
    storage_before: dict[tuple[str, str], frozenset[str]] = {}
    initialized_after: dict[
        tuple[str, str],
        frozenset[str],
    ] = {}
    for block in function.blocks:
        current_initialized = initialized_in[block.id]
        current_storage = storage_in[block.id]
        for instruction in block.instructions:
            initialized_before[(block.id, instruction.id)] = (
                current_initialized
            )
            storage_before[(block.id, instruction.id)] = (
                current_storage
            )
            attributes = instruction.attribute_map
            next_initialized = set(current_initialized)
            next_storage = set(current_storage)
            if instruction.op == "store_local":
                target = attributes.get(
                    "name", attributes.get("target")
                )
                if target in place_types:
                    next_storage.add(target)
                    next_initialized.add(target)
            elif (
                instruction.op == "load_local"
                and instruction.result is not None
                and value_kinds.get(instruction.result)
                is MIROwnershipKind.OWNED
            ):
                name = attributes.get("name")
                if name in place_types:
                    next_initialized.discard(name)
            elif instruction.op == "drop_value":
                local = attributes.get("local")
                if local in place_types:
                    next_initialized.discard(local)
            current_initialized = frozenset(next_initialized)
            current_storage = frozenset(next_storage)
            initialized_after[(block.id, instruction.id)] = (
                current_initialized
            )

    value_bases: dict[str, str] = {}
    place_bases: dict[str, str] = {
        name: f"caller::{name}"
        for name, kind in place_kinds.items()
        if kind is MIROwnershipKind.GUARANTEED and name in place_parameter_ownership
    }
    pending = True
    while pending:
        pending = False
        for block in function.blocks:
            for instruction in block.instructions:
                result = instruction.result
                if result is not None and value_kinds.get(result) is MIROwnershipKind.GUARANTEED and result not in value_bases:
                    base: str | None = None
                    if instruction.op == "load_local":
                        name = instruction.attribute_map.get("name")
                        if isinstance(name, str):
                            implicit_source = implicit_sources.get(
                                (block.id, name)
                            )
                            if (
                                base is None
                                and implicit_source is not None
                            ):
                                source_kind = value_kinds.get(
                                    implicit_source
                                )
                                if (
                                    source_kind
                                    is MIROwnershipKind.GUARANTEED
                                ):
                                    base = value_bases.get(
                                        implicit_source
                                    )
                                elif (
                                    source_kind
                                    is MIROwnershipKind.OWNED
                                ):
                                    base = implicit_source
                            if base is None:
                                base = place_bases.get(name)
                            if (
                                base is None
                                and instruction.symbol_id
                                in function_symbols
                            ):
                                base = f"static::{name}"
                            if base is None and place_kinds.get(name) is MIROwnershipKind.OWNED:
                                base = name
                    if base is None:
                        for operand in instruction.operands:
                            operand_kind = value_kinds.get(operand)
                            if operand_kind is MIROwnershipKind.GUARANTEED:
                                base = value_bases.get(operand)
                            elif operand_kind is MIROwnershipKind.OWNED:
                                base = operand
                            if base is not None:
                                break
                    if (
                        base is None
                        and instruction.symbol_id in function_symbols
                    ):
                        base = f"static::{result}"
                    if base is not None:
                        value_bases[result] = base
                        pending = True
                if instruction.op == "store_local" and instruction.operands:
                    target = instruction.attribute_map.get(
                        "name", instruction.attribute_map.get("target")
                    )
                    source = instruction.operands[0]
                    if isinstance(target, str) and place_kinds.get(target) is MIROwnershipKind.GUARANTEED:
                        base = value_bases.get(source)
                        existing = place_bases.get(target)
                        if base is not None and existing is None:
                            place_bases[target] = base
                            pending = True
                        elif base is not None and existing not in {None, base}:
                            raise MIROwnershipVerificationError(
                                "MIROwnershipAmbiguousBase",
                                f"{function.name}.{target}",
                            )

    return_kind = _kind_for_type(
        arena,
        type_kinds,
        function.return_type_id,
    )
    return_clones: dict[str, tuple[str, str | None]] = {}
    if return_kind is MIROwnershipKind.OWNED:
        for block in function.blocks:
            if block.terminator.kind != "return":
                continue
            value = block.terminator.value
            if value_kinds.get(value) is not MIROwnershipKind.GUARANTEED:
                continue
            base = value_bases.get(value)
            before_instruction = next(
                (
                    instruction.id
                    for instruction in block.instructions
                    if instruction.op == "drop_value"
                    and (
                        instruction.attribute_map.get("local") == base
                        or base in instruction.operands
                    )
                ),
                None,
            )
            return_clones[block.id] = (
                value,
                before_instruction,
            )
    implicit_result_sources = {
        instruction.result: implicit_sources[(block.id, name)]
        for block in function.blocks
        for instruction in block.instructions
        if (
            instruction.op == "load_local"
            and instruction.result is not None
            and isinstance(
                name := instruction.attribute_map.get("name"),
                str,
            )
            and (block.id, name) in implicit_sources
        )
    }
    guaranteed_values = frozenset(
        value
        for value, kind in value_kinds.items()
        if kind is MIROwnershipKind.GUARANTEED
    )
    guaranteed_places = frozenset(
        name
        for name, kind in place_kinds.items()
        if kind is MIROwnershipKind.GUARANTEED
    )
    owned_values = frozenset(
        value
        for value, kind in value_kinds.items()
        if kind is MIROwnershipKind.OWNED and value not in take_values
    )
    value_live_in, value_live_out = _liveness(
        function,
        guaranteed_values,
        aliases=implicit_result_sources,
    )
    place_value_bases = dict(value_bases)
    for value, source in implicit_result_sources.items():
        base = value_bases.get(source)
        if base is not None:
            place_value_bases[value] = base
    owned_live_in, owned_live_out = _liveness(
        function,
        owned_values,
        aliases=value_bases,
    )
    tracked_guaranteed_places = (
        guaranteed_places - frozenset(place_parameter_ownership)
    )
    place_live_in, place_live_out = _liveness(
        function,
        tracked_guaranteed_places,
        place_loads=True,
        aliases=place_value_bases,
        storage_before=storage_before,
    )
    all_place_live_in, all_place_live_out = _liveness(
        function,
        frozenset(place_types),
        place_loads=True,
        aliases=place_value_bases,
        storage_before=storage_before,
    )
    predecessors = _predecessors(function)
    successors = _successors(function)
    value_definitions = {
        block.id: frozenset(
            instruction.result
            for instruction in block.instructions
            if instruction.result is not None
        )
        for block in function.blocks
    }
    all_values = frozenset(value_types)
    value_available_in = {
        block.id: (
            frozenset()
            if block is function.blocks[0]
            else all_values
        )
        for block in function.blocks
    }
    value_available_out = {
        block.id: (
            value_definitions[block.id]
            if block is function.blocks[0]
            else all_values
        )
        for block in function.blocks
    }
    changed = True
    while changed:
        changed = False
        for block in function.blocks[1:]:
            block_predecessors = predecessors[block.id]
            available_in = (
                frozenset.intersection(
                    *(
                        value_available_out[predecessor]
                        for predecessor in block_predecessors
                    )
                )
                if block_predecessors
                else frozenset()
            )
            available_out = (
                available_in | value_definitions[block.id]
            )
            if (
                available_in != value_available_in[block.id]
                or available_out != value_available_out[block.id]
            ):
                value_available_in[block.id] = available_in
                value_available_out[block.id] = available_out
                changed = True

    value_ends: dict[tuple[str, str | None, str], set[str]] = {}
    owned_value_ends: dict[tuple[str, str | None, str], set[str]] = {}
    place_ends: dict[tuple[str, str | None, str], set[str]] = {}
    for block in function.blocks:
        live_values = set(value_live_out[block.id])
        if (
            block.terminator.value in guaranteed_values
            and block.terminator.value not in live_values
            and block.id not in return_clones
        ):
            value_ends.setdefault(
                (block.id, None, "terminator"),
                set(),
            ).add(block.terminator.value)
            live_values.add(block.terminator.value)
        live_owned = set(owned_live_out[block.id])
        return_owner = value_bases.get(block.terminator.value)
        if return_owner in owned_values:
            owned_value_ends.setdefault(
                (block.id, None, "terminator"),
                set(),
            ).add(return_owner)
            live_owned.add(return_owner)
        live_places = set(place_live_out[block.id])
        for instruction in reversed(block.instructions):
            result = instruction.result
            if result in guaranteed_values:
                if result not in live_values:
                    value_ends.setdefault(
                        (block.id, instruction.id, "after"),
                        set(),
                    ).add(result)
                live_values.discard(result)
            source = implicit_result_sources.get(result)
            if source in guaranteed_values:
                if source not in live_values:
                    value_ends.setdefault(
                        (
                            block.id,
                            instruction.id,
                            "after",
                        ),
                        set(),
                    ).add(source)
                live_values.add(source)
            if result in owned_values:
                if result not in live_owned:
                    owned_value_ends.setdefault(
                        (block.id, instruction.id, "after"),
                        set(),
                    ).add(result)
                live_owned.discard(result)
            for operand in instruction.operands:
                if operand in guaranteed_values:
                    if operand not in live_values:
                        value_ends.setdefault(
                            (block.id, instruction.id, "after"),
                            set(),
                        ).add(operand)
                    live_values.add(operand)
                source = implicit_result_sources.get(operand)
                if source in guaranteed_values:
                    if source not in live_values:
                        value_ends.setdefault(
                            (
                                block.id,
                                instruction.id,
                                "after",
                            ),
                            set(),
                        ).add(source)
                    live_values.add(source)
                if operand in owned_values:
                    if operand not in live_owned:
                        owned_value_ends.setdefault(
                            (block.id, instruction.id, "after"),
                            set(),
                        ).add(operand)
                    live_owned.add(operand)
                owner = value_bases.get(operand)
                if owner in owned_values:
                    if owner not in live_owned:
                        owned_value_ends.setdefault(
                            (block.id, instruction.id, "after"),
                            set(),
                        ).add(owner)
                    live_owned.add(owner)
            if instruction.op == "load_local":
                name = instruction.attribute_map.get("name")
                if name in tracked_guaranteed_places:
                    if name not in live_places:
                        place_ends.setdefault(
                            (block.id, instruction.id, "after"),
                            set(),
                        ).add(name)
                    live_places.add(name)
            if instruction.op == "store_local":
                target = instruction.attribute_map.get(
                    "name",
                    instruction.attribute_map.get("target"),
                )
                if target in tracked_guaranteed_places:
                    if target not in live_places:
                        place_ends.setdefault(
                            (block.id, instruction.id, "after"),
                            set(),
                        ).add(target)
                    live_places.discard(target)
        for value in guaranteed_values:
            if value in value_live_in[block.id]:
                continue
            ended_on_predecessor = False
            for predecessor in predecessors[block.id]:
                if (
                    value in value_live_out[predecessor]
                    and all(
                        value not in value_live_in[target]
                        for target in successors[predecessor]
                    )
                ):
                    value_ends.setdefault(
                        (predecessor, None, "terminator"),
                        set(),
                    ).add(value)
                    ended_on_predecessor = True
            block_predecessors = predecessors[block.id]
            if (
                not ended_on_predecessor
                and block_predecessors
                and all(
                    value in value_live_out[predecessor]
                    and value in value_available_out[predecessor]
                    for predecessor in block_predecessors
                )
            ):
                value_ends.setdefault(
                    (block.id, None, "entry"),
                    set(),
                ).add(value)
        for value in owned_values:
            if value in owned_live_in[block.id]:
                continue
            ended_on_predecessor = False
            for predecessor in predecessors[block.id]:
                if (
                    value in owned_live_out[predecessor]
                    and all(
                        value not in owned_live_in[target]
                        for target in successors[predecessor]
                    )
                ):
                    owned_value_ends.setdefault(
                        (predecessor, None, "terminator"),
                        set(),
                    ).add(value)
                    ended_on_predecessor = True
            block_predecessors = predecessors[block.id]
            if (
                not ended_on_predecessor
                and block_predecessors
                and all(
                    value in owned_live_out[predecessor]
                    and value in value_available_out[predecessor]
                    for predecessor in block_predecessors
                )
            ):
                owned_value_ends.setdefault(
                    (block.id, None, "entry"),
                    set(),
                ).add(value)
        for place in tracked_guaranteed_places:
            if place in place_live_in[block.id]:
                continue
            ended_on_predecessor = False
            for predecessor in predecessors[block.id]:
                if (
                    place in place_live_out[predecessor]
                    and all(
                        place not in place_live_in[target]
                        for target in successors[predecessor]
                    )
                ):
                    place_ends.setdefault(
                        (predecessor, None, "terminator"),
                        set(),
                    ).add(place)
                    ended_on_predecessor = True
            block_predecessors = predecessors[block.id]
            if (
                not ended_on_predecessor
                and block_predecessors
                and all(
                    place in place_live_out[predecessor]
                    and place in storage_out[predecessor]
                    for predecessor in block_predecessors
                )
            ):
                place_ends.setdefault(
                    (block.id, None, "entry"),
                    set(),
                ).add(place)

    storage_origins: dict[str, tuple[str, str]] = {}
    for block in function.blocks:
        for instruction in block.instructions:
            if instruction.op != "store_local":
                continue
            target = instruction.attribute_map.get(
                "name",
                instruction.attribute_map.get("target"),
            )
            if target not in place_types:
                continue
            if target not in storage_before[
                (block.id, instruction.id)
            ]:
                storage_origins.setdefault(
                    target,
                    (block.id, instruction.id),
                )
    local_places = frozenset(
        place
        for place, origin in storage_origins.items()
        if origin[0] != function.blocks[0].id
    )
    storage_deads: dict[
        tuple[str, str | None, str],
        set[str],
    ] = {}
    for block in function.blocks:
        live_storage = set(all_place_live_out[block.id])
        for instruction in reversed(block.instructions):
            attributes = instruction.attribute_map
            used_place: str | None = None
            if instruction.op == "load_local":
                name = attributes.get("name")
                if name in local_places:
                    used_place = name
            elif instruction.op == "drop_value":
                local = attributes.get("local")
                if local in local_places:
                    used_place = local
            if used_place is not None:
                if used_place not in live_storage:
                    storage_deads.setdefault(
                        (
                            block.id,
                            instruction.id,
                            "after",
                        ),
                        set(),
                    ).add(used_place)
                live_storage.add(used_place)
            if instruction.op == "store_local":
                target = attributes.get(
                    "name", attributes.get("target")
                )
                if target in local_places:
                    if target not in live_storage:
                        storage_deads.setdefault(
                            (
                                block.id,
                                instruction.id,
                                "after",
                            ),
                            set(),
                        ).add(target)
                    if target in storage_before[
                        (block.id, instruction.id)
                    ]:
                        live_storage.add(target)
                    else:
                        live_storage.discard(target)
    for block in function.blocks:
        for place in local_places:
            if place in all_place_live_in[block.id]:
                continue
            for predecessor in predecessors[block.id]:
                if (
                    place in storage_out[predecessor]
                    and all(
                        place not in all_place_live_in[target]
                        for target in successors[predecessor]
                    )
                    and not any(
                        event_block == predecessor
                        and place in scheduled
                        for (
                            event_block,
                            _instruction,
                            _point,
                        ), scheduled in storage_deads.items()
                    )
                ):
                    storage_deads.setdefault(
                        (predecessor, None, "terminator"),
                        set(),
                    ).add(place)

    for block_id, (value, _instruction_id) in return_clones.items():
        for (event_block, _instruction, _point), values in (
            value_ends.items()
        ):
            if event_block == block_id:
                values.discard(value)

    event_specs: dict[str, list[dict[str, object]]] = {
        block.id: [] for block in function.blocks
    }

    def add(
        block_id: str,
        op: str,
        point: str,
        type_id: TypeId,
        kind: MIROwnershipKind,
        *,
        instruction_id: str | None = None,
        value: str | None = None,
        place: str | None = None,
        base: str | None = None,
        target: str | None = None,
    ) -> None:
        if op in {"begin_borrow", "end_borrow"} and base is None:
            raise MIROwnershipVerificationError(
                "MIROwnershipMissingBase",
                value or place or function.name,
            )
        event_specs[block_id].append(
            {
                "op": op,
                "point": point,
                "instruction_id": instruction_id,
                "type_id": type_id,
                "kind": kind,
                "value": value,
                "place": place,
                "base": base,
                "target": target,
            }
        )

    def close_storage(
        block_id: str,
        point: str,
        place: str,
        *,
        instruction_id: str | None = None,
        initialized: bool,
    ) -> None:
        type_id = place_types[place]
        kind = place_kinds[place]
        if kind is MIROwnershipKind.OWNED and initialized:
            add(
                block_id,
                "destroy_value",
                point,
                type_id,
                kind,
                instruction_id=instruction_id,
                place=place,
            )
        add(
            block_id,
            "storage_dead",
            point,
            type_id,
            kind,
            instruction_id=instruction_id,
            place=place,
        )

    entry = function.blocks[0].id
    for (name, _type_name, _ownership), type_id in zip(
        function.parameters,
        function.parameter_type_ids,
        strict=True,
    ):
        kind = place_kinds[name]
        add(entry, "storage_live", "entry", type_id, kind, place=name)
        add(entry, "store_init", "entry", type_id, kind, place=name, base=place_bases.get(name), target="parameter")
        if kind is MIROwnershipKind.GUARANTEED:
            add(entry, "begin_borrow", "entry", type_id, kind, place=name, base=place_bases[name])

    pending_moves: dict[tuple[str, str], str] = {}
    for block in function.blocks:
        instructions = list(block.instructions)
        for index, instruction in enumerate(instructions):
            type_id = instruction.type_id
            attributes = instruction.attribute_map
            return_clone = return_clones.get(block.id)
            if (
                return_clone is not None
                and return_clone[1] == instruction.id
            ):
                value = return_clone[0]
                add(
                    block.id,
                    "copy_value",
                    "before",
                    value_types[value],
                    MIROwnershipKind.OWNED,
                    instruction_id=instruction.id,
                    value=value,
                    base=value_bases.get(value),
                    target="clone::return",
                )
                add(
                    block.id,
                    "end_borrow",
                    "before",
                    value_types[value],
                    MIROwnershipKind.GUARANTEED,
                    instruction_id=instruction.id,
                    value=value,
                    base=value_bases.get(value),
                )
            if attributes.get("ffi") or attributes.get("extern_abi"):
                policies = attributes.get("parameter_ownership")
                result_policy = attributes.get(
                    "result_ownership"
                )
                if (
                    not isinstance(policies, (tuple, list))
                    or len(policies) != len(instruction.operands)
                    or any(
                        policy not in _PARAMETER_OWNERSHIP
                        for policy in policies
                    )
                    or (
                        instruction.result is not None
                        and result_policy not in _RESULT_OWNERSHIP
                    )
                ):
                    raise MIROwnershipVerificationError(
                        "MIROwnershipMissingFFIOwnership",
                        instruction.id,
                    )
            if instruction.op == "move_value":
                for operand in instruction.operands:
                    operand_type = value_types.get(operand)
                    operand_kind = value_kinds.get(operand)
                    if (
                        operand_type is None
                        or operand_kind is not MIROwnershipKind.OWNED
                    ):
                        continue
                    target = next(
                        (
                            item.id
                            for item in instructions[index + 1 :]
                            if operand in item.operands
                        ),
                        "unknown-consumer",
                    )
                    pending_moves[(block.id, operand)] = target
                    add(
                        block.id,
                        "move_value",
                        "before",
                        operand_type,
                        operand_kind,
                        instruction_id=instruction.id,
                        value=operand,
                        target=target,
                    )
            elif instruction.op == "store_local" and instruction.operands and type_id is not None:
                target = attributes.get("name", attributes.get("target"))
                if isinstance(target, str):
                    point = (block.id, instruction.id)
                    if target not in storage_before[point]:
                        add(
                            block.id,
                            "storage_live",
                            "before",
                            type_id,
                            place_kinds[target],
                            instruction_id=instruction.id,
                            place=target,
                        )
                    store_op = (
                        "store_assign"
                        if target in initialized_before[point]
                        else "store_init"
                    )
                    source = instruction.operands[0]
                    if (
                        place_kinds[target] is MIROwnershipKind.OWNED
                        and value_kinds.get(source)
                        is MIROwnershipKind.GUARANTEED
                    ):
                        add(
                            block.id,
                            "copy_value",
                            "before",
                            type_id,
                            MIROwnershipKind.OWNED,
                            instruction_id=instruction.id,
                            value=source,
                            base=value_bases.get(source),
                            target=f"clone::{target}",
                        )
                    add(
                        block.id,
                        store_op,
                        "after",
                        type_id,
                        place_kinds[target],
                        instruction_id=instruction.id,
                        value=source,
                        place=target,
                        base=place_bases.get(target),
                        target=target,
                    )
                    if place_kinds[target] is MIROwnershipKind.GUARANTEED:
                        add(
                            block.id,
                            "begin_borrow",
                            "after",
                            type_id,
                            MIROwnershipKind.GUARANTEED,
                            instruction_id=instruction.id,
                            place=target,
                            base=place_bases.get(target),
                        )
            elif instruction.op == "load_local" and instruction.result is not None and type_id is not None:
                name = attributes.get("name")
                if isinstance(name, str):
                    kind = value_kinds[instruction.result]
                    base = value_bases.get(instruction.result)
                    implicit_reference = (
                        (block.id, name) in implicit_sources
                    )
                    if implicit_reference:
                        if kind is MIROwnershipKind.GUARANTEED:
                            add(
                                block.id,
                                "begin_borrow",
                                "after",
                                type_id,
                                kind,
                                instruction_id=instruction.id,
                                value=instruction.result,
                                base=base,
                            )
                        add(
                            block.id,
                            "copy_value",
                            "after",
                            type_id,
                            kind,
                            instruction_id=instruction.id,
                            value=instruction.result,
                            base=base,
                            target=instruction.id,
                        )
                    else:
                        add(
                            block.id,
                            (
                                "load_take"
                                if kind is MIROwnershipKind.OWNED
                                else "load_copy"
                            ),
                            "after",
                            type_id,
                            kind,
                            instruction_id=instruction.id,
                            value=instruction.result,
                            place=name,
                            base=base,
                        )
                        if kind is MIROwnershipKind.GUARANTEED:
                            add(
                                block.id,
                                "begin_borrow",
                                "after",
                                type_id,
                                kind,
                                instruction_id=instruction.id,
                                value=instruction.result,
                                base=base,
                            )
            elif instruction.op == "drop_value" and type_id is not None:
                place = attributes.get("local")
                value = instruction.operands[0] if instruction.operands else None
                add(
                    block.id,
                    "destroy_value",
                    "after",
                    type_id,
                    MIROwnershipKind.OWNED,
                    instruction_id=instruction.id,
                    value=(
                        None if isinstance(place, str) else value
                    ),
                    place=(
                        place if isinstance(place, str) else None
                    ),
                )
            elif (
                instruction.op
                in _OWNERSHIP_PREPARATION_OPERATIONS
            ):
                pass
            else:
                ownership_policies = tuple(attributes.get("parameter_ownership", ()))
                for operand_index, operand in enumerate(instruction.operands):
                    operand_type = value_types.get(operand)
                    operand_kind = value_kinds.get(operand)
                    if operand_type is None or operand_kind is None:
                        continue
                    if pending_moves.get((block.id, operand)) == instruction.id:
                        pending_moves.pop((block.id, operand), None)
                        continue
                    policy = (
                        ownership_policies[operand_index]
                        if operand_index < len(ownership_policies)
                        else attributes.get("receiver_ownership")
                        if operand_index == 0
                        else None
                    )
                    if operand_kind is MIROwnershipKind.OWNED:
                        if policy in _BORROW_OWNERSHIP or policy is None:
                            borrow_value = f"borrow::{instruction.id}::{operand_index}"
                            add(
                                block.id,
                                "begin_borrow",
                                "before",
                                operand_type,
                                MIROwnershipKind.GUARANTEED,
                                instruction_id=instruction.id,
                                value=borrow_value,
                                base=operand,
                            )
                            add(
                                block.id,
                                "copy_value",
                                "before",
                                operand_type,
                                MIROwnershipKind.GUARANTEED,
                                instruction_id=instruction.id,
                                value=borrow_value,
                                base=operand,
                                target=instruction.id,
                            )
                            add(
                                block.id,
                                "end_borrow",
                                "after",
                                operand_type,
                                MIROwnershipKind.GUARANTEED,
                                instruction_id=instruction.id,
                                value=borrow_value,
                                base=operand,
                            )
                        elif policy in {"owned", "consuming", "move"}:
                            add(
                                block.id,
                                "move_value",
                                "before",
                                operand_type,
                                operand_kind,
                                instruction_id=instruction.id,
                                value=operand,
                                target=instruction.id,
                            )
                    else:
                        add(
                            block.id,
                            "copy_value",
                            "before",
                            operand_type,
                            operand_kind,
                            instruction_id=instruction.id,
                            value=operand,
                            base=value_bases.get(operand),
                            target=instruction.id,
                        )
                if instruction.result is not None and value_kinds.get(instruction.result) is MIROwnershipKind.GUARANTEED and type_id is not None:
                    add(
                        block.id,
                        "begin_borrow",
                        "after",
                        type_id,
                        MIROwnershipKind.GUARANTEED,
                        instruction_id=instruction.id,
                        value=instruction.result,
                        base=value_bases.get(instruction.result),
                    )

            for value in sorted(value_ends.get((block.id, instruction.id, "after"), ())):
                add(
                    block.id,
                    "end_borrow",
                    "after",
                    value_types[value],
                    MIROwnershipKind.GUARANTEED,
                    instruction_id=instruction.id,
                    value=value,
                    base=value_bases.get(value),
                )
            for value in sorted(
                owned_value_ends.get(
                    (block.id, instruction.id, "after"),
                    (),
                )
            ):
                add(
                    block.id,
                    "destroy_value",
                    "after",
                    value_types[value],
                    MIROwnershipKind.OWNED,
                    instruction_id=instruction.id,
                    value=value,
                )
            for place in sorted(place_ends.get((block.id, instruction.id, "after"), ())):
                add(
                    block.id,
                    "end_borrow",
                    "after",
                    place_types[place],
                    MIROwnershipKind.GUARANTEED,
                    instruction_id=instruction.id,
                    place=place,
                    base=place_bases.get(place),
                )
            for place in sorted(
                storage_deads.get(
                    (block.id, instruction.id, "after"),
                    (),
                )
            ):
                close_storage(
                    block.id,
                    "after",
                    place,
                    instruction_id=instruction.id,
                    initialized=(
                        place
                        in initialized_after[
                            (block.id, instruction.id)
                        ]
                    ),
                )

        for value in sorted(value_ends.get((block.id, None, "entry"), ())):
            add(
                block.id,
                "end_borrow",
                "entry",
                value_types[value],
                MIROwnershipKind.GUARANTEED,
                value=value,
                base=value_bases.get(value),
            )
        for value in sorted(
            owned_value_ends.get((block.id, None, "entry"), ())
        ):
            add(
                block.id,
                "destroy_value",
                "entry",
                value_types[value],
                MIROwnershipKind.OWNED,
                value=value,
            )
        for place in sorted(place_ends.get((block.id, None, "entry"), ())):
            add(
                block.id,
                "end_borrow",
                "entry",
                place_types[place],
                MIROwnershipKind.GUARANTEED,
                place=place,
                base=place_bases.get(place),
            )

        return_clone = return_clones.get(block.id)
        if (
            return_clone is not None
            and return_clone[1] is None
        ):
            value = return_clone[0]
            add(
                block.id,
                "copy_value",
                "terminator",
                value_types[value],
                MIROwnershipKind.OWNED,
                value=value,
                base=value_bases.get(value),
                target="clone::return",
            )
            add(
                block.id,
                "end_borrow",
                "terminator",
                value_types[value],
                MIROwnershipKind.GUARANTEED,
                value=value,
                base=value_bases.get(value),
            )
        return_value = block.terminator.value
        if (
            block.terminator.kind == "return"
            and return_value is not None
            and return_value in value_types
            and return_clone is None
        ):
            return_kind = value_kinds[return_value]
            add(
                block.id,
                "move_value" if return_kind is MIROwnershipKind.OWNED else "copy_value",
                "terminator",
                value_types[return_value],
                return_kind,
                value=return_value,
                base=value_bases.get(return_value),
                target="return",
            )
        for value in sorted(value_ends.get((block.id, None, "terminator"), ())):
            add(
                block.id,
                "end_borrow",
                "terminator",
                value_types[value],
                MIROwnershipKind.GUARANTEED,
                value=value,
                base=value_bases.get(value),
                target="return",
            )
        for place in sorted(
            place_ends.get(
                (block.id, None, "terminator"),
                (),
            )
        ):
            add(
                block.id,
                "end_borrow",
                "terminator",
                place_types[place],
                MIROwnershipKind.GUARANTEED,
                place=place,
                base=place_bases.get(place),
            )
        for value in sorted(
            owned_value_ends.get(
                (block.id, None, "terminator"),
                (),
            )
        ):
            add(
                block.id,
                "destroy_value",
                "terminator",
                value_types[value],
                MIROwnershipKind.OWNED,
                value=value,
            )
        for place in sorted(
            storage_deads.get(
                (block.id, None, "terminator"),
                (),
            )
        ):
            close_storage(
                block.id,
                "terminator",
                place,
                initialized=place in initialized_out[block.id],
            )
        if block.terminator.kind in {"return", "unreachable"}:
            scheduled_place_ends = set().union(
                *(
                    places
                    for (event_block, _instruction, _point), places
                    in place_ends.items()
                    if event_block == block.id
                )
            )
            for place in sorted(
                (
                    guaranteed_places
                    & frozenset(place_parameter_ownership)
                )
                - scheduled_place_ends
            ):
                add(
                    block.id,
                    "end_borrow",
                    "exit",
                    place_types[place],
                    MIROwnershipKind.GUARANTEED,
                    place=place,
                    base=place_bases.get(place),
                )
            closed_storage = set().union(
                *(
                    places
                    for (event_block, _instruction, _point), places
                    in storage_deads.items()
                    if event_block == block.id
                )
            )
            for place in sorted(place_types):
                if place in closed_storage:
                    continue
                close_storage(
                    block.id,
                    "exit",
                    place,
                    initialized=place in initialized_out[block.id],
                )

    point_rank = {"entry": 0, "before": 1, "after": 2, "terminator": 3, "exit": 4}
    result_blocks = []
    for block in function.blocks:
        instruction_rank = {
            instruction.id: index for index, instruction in enumerate(block.instructions)
        }
        specs = event_specs[block.id]
        specs.sort(
            key=lambda item: (
                -1 if item["point"] == "entry" else instruction_rank.get(item["instruction_id"], len(instruction_rank)),
                point_rank[item["point"]],
            )
        )
        operations = tuple(
            _operation(
                function,
                block.id,
                ordinal,
                item["op"],
                item["point"],
                item["type_id"],
                item["kind"],
                instruction_id=item["instruction_id"],
                value=item["value"],
                place=item["place"],
                base=item["base"],
                target=item["target"],
            )
            for ordinal, item in enumerate(specs, 1)
        )
        result_blocks.append(MIROwnershipBlock(block.id, operations))
    return MIROwnershipFunction(
        function.name,
        function.symbol_id,
        function.blocks[0].id,
        tuple(result_blocks),
    )


def _used_type_ids(
    functions: tuple[Any, ...],
) -> frozenset[TypeId]:
    return frozenset(
        type_id
        for function in functions
        for type_id in (
            *function.parameter_type_ids,
            function.return_type_id,
            *(
                instruction.type_id
                for block in function.blocks
                for instruction in block.instructions
            ),
            *(
                type_id
                for block in function.blocks
                for instruction in block.instructions
                for type_id in instruction.operand_type_ids
            ),
        )
        if isinstance(type_id, TypeId)
    )


def _authoritative_type_kinds(
    arena: FrozenTypeArena,
    drop_plan_bindings: tuple[
        tuple[TypeId, DropPlanId, str],
        ...,
    ],
    type_ids: frozenset[TypeId],
) -> tuple[tuple[TypeId, MIROwnershipKind], ...]:
    plans: dict[TypeId, tuple[DropPlanId, str]] = {}
    for type_id, drop_plan_id, action in drop_plan_bindings:
        if type_id not in arena:
            raise MIROwnershipVerificationError(
                "MIROwnershipUnknownType",
                str(type_id),
            )
        if (
            type_id in plans
            or not isinstance(drop_plan_id, DropPlanId)
            or not isinstance(action, str)
            or not action
        ):
            raise MIROwnershipVerificationError(
                "MIROwnershipTypeAuthorityMismatch",
                str(type_id),
            )
        plans[type_id] = (drop_plan_id, action)
    authority: dict[TypeId, MIROwnershipKind] = {}

    def classify(type_id: TypeId) -> MIROwnershipKind:
        known = authority.get(type_id)
        if known is not None:
            return known
        if type_id not in arena:
            raise MIROwnershipVerificationError(
                "MIROwnershipUnknownType",
                str(type_id),
            )
        reference = arena.resolve(type_id)
        constructor = reference.constructor
        if constructor in _RAW_POINTER_CONSTRUCTORS:
            kind = MIROwnershipKind.UNOWNED
        elif constructor in _GUARANTEED_CONSTRUCTORS:
            kind = MIROwnershipKind.GUARANTEED
        elif constructor in _OWNED_CONSTRUCTORS:
            kind = MIROwnershipKind.OWNED
        elif (
            constructor in _TRIVIAL_CONSTRUCTORS
            or constructor.isdecimal()
        ):
            kind = MIROwnershipKind.TRIVIAL
        elif type_id in plans:
            kind = (
                MIROwnershipKind.TRIVIAL
                if plans[type_id][1] == "trivial"
                else MIROwnershipKind.OWNED
            )
        elif reference.arguments:
            child_kinds = tuple(
                classify(child)
                for child in reference.arguments
            )
            if MIROwnershipKind.OWNED in child_kinds:
                kind = MIROwnershipKind.OWNED
            elif MIROwnershipKind.GUARANTEED in child_kinds:
                kind = MIROwnershipKind.GUARANTEED
            else:
                kind = MIROwnershipKind.TRIVIAL
        else:
            raise MIROwnershipVerificationError(
                "MIROwnershipTypeAuthorityMissing",
                arena.canonical(type_id),
            )
        authority[type_id] = kind
        return kind

    for type_id in type_ids | frozenset(plans):
        classify(type_id)
    return tuple(
        sorted(authority.items(), key=lambda item: str(item[0]))
    )


def build_mir_ownership_program(
    functions: Iterable[Any],
    arena: FrozenTypeArena,
    drop_plan_bindings: Iterable[tuple[TypeId, DropPlanId, str]],
    *,
    type_kinds: Iterable[tuple[TypeId, MIROwnershipKind]] | None = None,
) -> MIROwnershipProgram:
    if not isinstance(arena, FrozenTypeArena) or arena.allow_unresolved:
        raise MIROwnershipVerificationError(
            "MIROwnershipTypeAuthorityMissing",
            "closed FrozenTypeArena required",
        )
    functions_tuple = tuple(functions)
    bindings = tuple(drop_plan_bindings)
    claimed = dict(type_kinds or ())
    canonical_type_kinds = _authoritative_type_kinds(
        arena,
        bindings,
        frozenset(arena.ids),
    )
    authoritative = dict(canonical_type_kinds)
    for type_id, kind in claimed.items():
        if authoritative.get(type_id) is not kind:
            raise MIROwnershipVerificationError(
                "MIROwnershipTypeKindMismatch",
                arena.canonical(type_id),
            )
    function_symbols = frozenset(
        function.symbol_id for function in functions_tuple
    )
    return MIROwnershipProgram(
        canonical_type_kinds,
        tuple(
            _build_function(
                function,
                arena,
                authoritative,
                function_symbols,
            )
            for function in functions_tuple
        ),
    )


def _join_states(states: tuple[_State, ...], block_id: str) -> _State:
    if not states:
        return _State()
    value_maps = [dict(state.values) for state in states]
    place_maps = [dict(state.places) for state in states]
    borrow_maps = [
        {name: (status, base) for name, status, base in state.borrows}
        for state in states
    ]
    values: dict[str, str] = {}
    for name in set().union(*(mapping.keys() for mapping in value_maps)):
        statuses = {mapping.get(name, "absent") for mapping in value_maps}
        if statuses <= {"absent", "consumed"}:
            values[name] = "consumed"
        elif len(statuses) == 1:
            values[name] = statuses.pop()
        else:
            raise MIROwnershipVerificationError(
                "MIROwnershipPhiMismatch",
                f"{block_id}.{name}: {sorted(statuses)}",
            )
    places: dict[str, str] = {}
    for name in set().union(*(mapping.keys() for mapping in place_maps)):
        statuses = {mapping.get(name, "absent") for mapping in place_maps}
        if statuses <= {"absent", "dead"}:
            places[name] = "dead"
        elif statuses == {"dead", "initialized"}:
            places[name] = "maybe_initialized"
        elif len(statuses) == 1:
            places[name] = statuses.pop()
        else:
            raise MIROwnershipVerificationError(
                "MIROwnershipPhiMismatch",
                f"{block_id}.{name}: {sorted(statuses)}",
            )
    borrows: dict[str, tuple[str, str]] = {}
    for name in set().union(*(mapping.keys() for mapping in borrow_maps)):
        states_for_name = {mapping.get(name, ("absent", "")) for mapping in borrow_maps}
        if {status for status, _base in states_for_name} <= {"absent", "ended"}:
            base = next((base for status, base in states_for_name if status == "ended"), "")
            borrows[name] = ("ended", base)
        elif len(states_for_name) == 1:
            borrows[name] = states_for_name.pop()
        else:
            raise MIROwnershipVerificationError(
                "MIROwnershipPhiMismatch",
                f"{block_id}.{name}: {sorted(states_for_name)}",
            )
    return _freeze_state(values, places, borrows)


def _verify_function(
    function: Any,
    ownership: MIROwnershipFunction,
    arena: FrozenTypeArena,
    type_kinds: dict[TypeId, MIROwnershipKind],
) -> None:
    mir_blocks = {block.id: block for block in function.blocks}
    ownership_blocks = {block.id: block for block in ownership.blocks}
    if set(mir_blocks) != set(ownership_blocks) or ownership.entry_block != function.blocks[0].id:
        raise MIROwnershipVerificationError(
            "MIROwnershipControlFlowMismatch",
            function.name,
        )
    if ownership.name != function.name or ownership.symbol_id != function.symbol_id:
        raise MIROwnershipVerificationError(
            "MIROwnershipFunctionMismatch",
            function.name,
        )
    value_types = {
        instruction.result: instruction.result_type_id
        for block in function.blocks
        for instruction in block.instructions
        if instruction.result is not None and instruction.result_type_id is not None
    }
    value_kinds = {
        value: _kind_for_type(arena, type_kinds, type_id)
        for value, type_id in value_types.items()
    }
    explicitly_owned_values = {
        operation.value
        for ownership_block in ownership.blocks
        for operation in ownership_block.operations
        if operation.value is not None
        and operation.kind is MIROwnershipKind.OWNED
        and operation.op in {
            "load_take",
            "move_value",
            "destroy_value",
        }
    }
    explicitly_borrowed_values = {
        operation.value
        for ownership_block in ownership.blocks
        for operation in ownership_block.operations
        if operation.value is not None
        and operation.kind is MIROwnershipKind.GUARANTEED
        and operation.op == "begin_borrow"
    }
    for block in function.blocks:
        for instruction in block.instructions:
            if (
                instruction.result in value_kinds
                and value_kinds[instruction.result]
                is MIROwnershipKind.OWNED
                and instruction.result not in explicitly_owned_values
                and (
                    _borrowed_provenance(instruction)
                    or instruction.result
                    in explicitly_borrowed_values
                )
            ):
                value_kinds[instruction.result] = (
                    MIROwnershipKind.GUARANTEED
                )
    instruction_blocks = {
        instruction.id: block.id
        for block in function.blocks
        for instruction in block.instructions
    }
    for ownership_block in ownership.blocks:
        for operation in ownership_block.operations:
            if operation.block_id != ownership_block.id:
                raise MIROwnershipVerificationError(
                    "MIROwnershipControlFlowMismatch",
                    operation.id,
                )
            if operation.type_id not in arena:
                raise MIROwnershipVerificationError(
                    "MIROwnershipUnknownType",
                    operation.id,
                )
            if operation.instruction_id is not None:
                if operation.instruction_id not in instruction_blocks:
                    raise MIROwnershipVerificationError(
                        "MIROwnershipUnknownInstruction",
                        operation.id,
                    )
                if (
                    instruction_blocks[operation.instruction_id]
                    != ownership_block.id
                ):
                    raise MIROwnershipVerificationError(
                        "MIROwnershipControlFlowMismatch",
                        operation.id,
                    )

    successors = _successors(function)
    predecessors = _predecessors(function)
    incoming: dict[str, _State] = {function.blocks[0].id: _State()}
    outgoing: dict[str, _State] = {}
    work = deque([function.blocks[0].id])
    queued = {function.blocks[0].id}

    def borrow_key(operation: MIROwnershipOperation) -> str:
        return f"value:{operation.value}" if operation.value is not None else f"place:{operation.place}"

    while work:
        block_id = work.popleft()
        queued.discard(block_id)
        state = incoming[block_id]
        values, places, borrows = state.mutable()
        clones: set[tuple[str, str]] = set()
        block = mir_blocks[block_id]
        ownership_block = ownership_blocks[block_id]
        by_point: dict[tuple[str | None, str], list[MIROwnershipOperation]] = {}
        for operation in ownership_block.operations:
            by_point.setdefault((operation.instruction_id, operation.point), []).append(operation)

        def base_is_live(base: str) -> bool:
            if base.startswith(("caller::", "static::")):
                return True
            return places.get(base) == "initialized" or values.get(base) == "live"

        def require_no_live_borrow(base: str) -> None:
            if any(status == "live" and borrow_base == base for status, borrow_base in borrows.values()):
                raise MIROwnershipVerificationError(
                    "MIROwnershipBaseDestroyedDuringBorrow",
                    base,
                )

        def consume_value(value: str) -> None:
            status = values.get(value)
            if status != "live":
                raise MIROwnershipVerificationError(
                    "MIROwnershipDoubleConsume",
                    value,
                )
            require_no_live_borrow(value)
            values[value] = "consumed"

        def apply(operation: MIROwnershipOperation) -> None:
            key = borrow_key(operation)
            if operation.op == "storage_live":
                if operation.place is None:
                    raise MIROwnershipVerificationError("MIROwnershipMalformedOperation", operation.id)
                if places.get(operation.place) not in {None, "dead"}:
                    raise MIROwnershipVerificationError("MIROwnershipStorageAlreadyLive", operation.place)
                places[operation.place] = "uninitialized"
            elif operation.op in {"store_init", "store_assign"}:
                if operation.place is None:
                    raise MIROwnershipVerificationError("MIROwnershipMalformedOperation", operation.id)
                expected = "uninitialized" if operation.op == "store_init" else "initialized"
                if places.get(operation.place) != expected:
                    diagnostic = (
                        "MIROwnershipDoubleConsume"
                        if places.get(operation.place) in {"uninitialized", "dead"}
                        else "MIROwnershipStorageStateMismatch"
                    )
                    raise MIROwnershipVerificationError(diagnostic, operation.place)
                require_no_live_borrow(operation.place)
                if operation.kind is MIROwnershipKind.OWNED and operation.value is not None:
                    source_kind = value_kinds.get(operation.value)
                    if source_kind is MIROwnershipKind.GUARANTEED:
                        clone = (
                            operation.value,
                            operation.place,
                        )
                        if clone not in clones:
                            raise MIROwnershipVerificationError(
                                "MIROwnershipUnownedToOwned",
                                operation.value,
                            )
                        clones.remove(clone)
                    elif source_kind is MIROwnershipKind.UNOWNED:
                        raise MIROwnershipVerificationError(
                            "MIROwnershipUnownedToOwned",
                            operation.value,
                        )
                    else:
                        consume_value(operation.value)
                places[operation.place] = "initialized"
            elif operation.op == "load_take":
                if operation.place is None or operation.value is None:
                    raise MIROwnershipVerificationError("MIROwnershipMalformedOperation", operation.id)
                if places.get(operation.place) != "initialized":
                    raise MIROwnershipVerificationError("MIROwnershipDoubleConsume", operation.place)
                require_no_live_borrow(operation.place)
                places[operation.place] = "uninitialized"
            elif operation.op == "load_copy":
                if operation.place is None:
                    raise MIROwnershipVerificationError(
                        "MIROwnershipMalformedOperation",
                        operation.id,
                    )
                if (
                    not (
                        operation.base is not None
                        and operation.base.startswith("static::")
                    )
                    and places.get(operation.place) != "initialized"
                ):
                    raise MIROwnershipVerificationError(
                        "MIROwnershipUseAfterConsume",
                        operation.place,
                    )
                if operation.kind is MIROwnershipKind.OWNED:
                    raise MIROwnershipVerificationError("MIROwnershipImplicitClone", operation.place)
                if operation.kind is MIROwnershipKind.GUARANTEED:
                    place_borrow = borrows.get(f"place:{operation.place}")
                    if place_borrow is not None and place_borrow[0] != "live":
                        raise MIROwnershipVerificationError(
                            "MIROwnershipBorrowUseAfterEnd",
                            operation.place,
                        )
            elif operation.op == "move_value":
                if operation.value is None or operation.kind is not MIROwnershipKind.OWNED:
                    raise MIROwnershipVerificationError("MIROwnershipInvalidMove", operation.id)
                consume_value(operation.value)
            elif operation.op == "copy_value":
                if operation.kind is MIROwnershipKind.OWNED:
                    if (
                        operation.value is None
                        or operation.target is None
                        or not operation.target.startswith("clone::")
                    ):
                        raise MIROwnershipVerificationError(
                            "MIROwnershipImplicitClone",
                            operation.value or operation.id,
                        )
                    status = borrows.get(key)
                    if (
                        status is None
                        or status[0] != "live"
                        or status[1] != operation.base
                    ):
                        raise MIROwnershipVerificationError(
                            "MIROwnershipBorrowUseAfterEnd",
                            operation.value,
                        )
                    clones.add(
                        (
                            operation.value,
                            operation.target.removeprefix("clone::"),
                        )
                    )
                if operation.kind is MIROwnershipKind.GUARANTEED:
                    status = borrows.get(key)
                    if (
                        status is None
                        or status[0] != "live"
                        or status[1] != operation.base
                    ):
                        raise MIROwnershipVerificationError(
                            "MIROwnershipBorrowUseAfterEnd",
                            operation.value or operation.place or operation.id,
                        )
                    if (
                        operation.target == "return"
                        and operation.base is not None
                        and not operation.base.startswith(
                            ("caller::", "static::")
                        )
                    ):
                        raise MIROwnershipVerificationError(
                            "MIROwnershipBorrowEscapes",
                            operation.value
                            or operation.place
                            or operation.id,
                        )
            elif operation.op == "destroy_value":
                if operation.place is not None:
                    if places.get(operation.place) != "initialized":
                        raise MIROwnershipVerificationError("MIROwnershipDoubleConsume", operation.place)
                    require_no_live_borrow(operation.place)
                    places[operation.place] = "uninitialized"
                elif operation.value is not None:
                    consume_value(operation.value)
                else:
                    raise MIROwnershipVerificationError("MIROwnershipMalformedOperation", operation.id)
            elif operation.op == "begin_borrow":
                if operation.base is None or not base_is_live(operation.base):
                    raise MIROwnershipVerificationError(
                        "MIROwnershipMissingBase",
                        operation.base or operation.id,
                    )
                current = borrows.get(key)
                if current is not None and current[0] == "live":
                    raise MIROwnershipVerificationError("MIROwnershipBorrowAlreadyLive", key)
                borrows[key] = ("live", operation.base)
            elif operation.op == "end_borrow":
                current = borrows.get(key)
                if current is None or current[0] != "live" or current[1] != operation.base:
                    raise MIROwnershipVerificationError(
                        "MIROwnershipBorrowEndMismatch",
                        operation.value or operation.place or operation.id,
                    )
                borrows[key] = ("ended", current[1])
            elif operation.op == "storage_dead":
                if operation.place is None:
                    raise MIROwnershipVerificationError("MIROwnershipMalformedOperation", operation.id)
                status = places.get(operation.place)
                if (
                    operation.kind is MIROwnershipKind.OWNED
                    and status
                    in {"initialized", "maybe_initialized"}
                ):
                    raise MIROwnershipVerificationError(
                        "MIROwnershipOwnedValueLeak",
                        operation.place,
                    )
                place_borrow = borrows.get(f"place:{operation.place}")
                if place_borrow is not None and place_borrow[0] == "live":
                    raise MIROwnershipVerificationError(
                        "MIROwnershipBorrowEscapes",
                        operation.place,
                    )
                places[operation.place] = "dead"

        for operation in by_point.get((None, "entry"), ()):
            apply(operation)
        for instruction in block.instructions:
            for operation in by_point.get((instruction.id, "before"), ()):
                apply(operation)
            if instruction.result is not None and instruction.result_type_id is not None:
                kind = value_kinds.get(instruction.result)
                if kind is MIROwnershipKind.OWNED:
                    if values.get(instruction.result) == "live":
                        raise MIROwnershipVerificationError(
                            "MIROwnershipDuplicateDefinition",
                            instruction.result,
                        )
                    values[instruction.result] = "live"
            for operation in by_point.get((instruction.id, "after"), ()):
                apply(operation)
        for operation in by_point.get((None, "terminator"), ()):
            apply(operation)
        for operation in by_point.get((None, "exit"), ()):
            apply(operation)

        next_state = _freeze_state(values, places, borrows)
        if outgoing.get(block_id) == next_state:
            continue
        outgoing[block_id] = next_state
        for target in successors[block_id]:
            predecessor_states = tuple(
                outgoing[predecessor]
                for predecessor in predecessors[target]
                if predecessor in outgoing
            )
            joined = _join_states(predecessor_states, target)
            if incoming.get(target) != joined:
                incoming[target] = joined
                if target not in queued:
                    work.append(target)
                    queued.add(target)

    for block in function.blocks:
        if block.terminator.kind not in {"return", "unreachable"} or block.id not in outgoing:
            continue
        values, places, borrows = outgoing[block.id].mutable()
        live_values = sorted(name for name, status in values.items() if status == "live")
        initialized_owned = sorted(
            name
            for name, status in places.items()
            if status in {"initialized", "maybe_initialized"}
            and _kind_for_type(
                arena,
                type_kinds,
                next(
                    operation.type_id
                    for operation in ownership_blocks[block.id].operations
                    if operation.place == name
                ),
            )
            is MIROwnershipKind.OWNED
        )
        live_borrows = sorted(name for name, (status, _base) in borrows.items() if status == "live")
        if live_values or initialized_owned:
            raise MIROwnershipVerificationError(
                "MIROwnershipOwnedValueLeak",
                ",".join((*live_values, *initialized_owned)),
            )
        if live_borrows:
            raise MIROwnershipVerificationError(
                "MIROwnershipBorrowEscapes",
                ",".join(live_borrows),
            )


def verify_ownership_program(
    functions: Iterable[Any],
    arena: FrozenTypeArena,
    drop_plan_bindings: Iterable[tuple[TypeId, DropPlanId, str]],
    ownership: MIROwnershipProgram,
) -> None:
    if not isinstance(ownership, MIROwnershipProgram):
        raise MIROwnershipVerificationError(
            "MIROwnershipMetadataMissing",
            "ownership program required",
        )
    functions_tuple = tuple(functions)
    if len(functions_tuple) != len(ownership.functions):
        raise MIROwnershipVerificationError(
            "MIROwnershipFunctionMismatch",
            "function count",
        )
    if not isinstance(arena, FrozenTypeArena) or arena.allow_unresolved:
        raise MIROwnershipVerificationError(
            "MIROwnershipTypeAuthorityMissing",
            "closed FrozenTypeArena required",
        )
    expected_type_kinds = _authoritative_type_kinds(
        arena,
        tuple(drop_plan_bindings),
        frozenset(arena.ids),
    )
    if ownership.type_kinds != expected_type_kinds:
        raise MIROwnershipVerificationError(
            "MIROwnershipTypeKindMismatch",
            ownership.contract,
        )
    type_kinds = dict(expected_type_kinds)
    for function, ownership_function in zip(
        functions_tuple,
        ownership.functions,
        strict=True,
    ):
        _verify_function(function, ownership_function, arena, type_kinds)


__all__ = [
    "MIR_OWNERSHIP_CONTRACT",
    "MIR_OWNERSHIP_OPERATIONS",
    "MIR_OWNERSHIP_SCHEMA_VERSION",
    "MIROwnershipBlock",
    "MIROwnershipFunction",
    "MIROwnershipKind",
    "MIROwnershipOperation",
    "MIROwnershipProgram",
    "MIROwnershipVerificationError",
    "build_mir_ownership_program",
    "verify_ownership_program",
    "ownership_type_kinds_from_descriptors",
]
