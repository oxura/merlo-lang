"""Versioned HIR/MIR/ABI contracts for restricted BytesView borrowed returns."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from research.archive.alpha1.merlo.native_hir import NativeHIRProgram
from .performance_mir import PerformanceMIR, SourceMapping


BYTES_BORROWED_RETURN_HIR_SCHEMA_VERSION = 1
BYTES_BORROWED_RETURN_MIR_SCHEMA_VERSION = 1
BYTES_BORROWED_RETURN_ABI_VERSION = 1
BYTES_BORROWED_RETURN_HIR_CONTRACT = "meldra.bytes-borrowed-return-hir.v1"
BYTES_BORROWED_RETURN_MIR_CONTRACT = "meldra.bytes-borrowed-return-mir.v1"
BYTES_BORROWED_RETURN_ABI_CONTRACT = "meldra.bytes-borrowed-return-abi.v1"
_BORROW_STARTS = {"borrow_argument", "reborrow_argument"}
_BORROW_ENDS = {"borrow_end", "reborrow_end"}
_TRANSFER_OPS = {
    "borrow_argument",
    "reborrow_argument",
    "borrow_return_transfer",
    "caller_borrow_continue",
    "borrow_end",
    "reborrow_end",
}


def _source(source: SourceMapping | None) -> dict[str, Any] | None:
    return source.to_dict() if source is not None else None


def _digest(prefix: str, *values: Any) -> str:
    payload = "\0".join(str(value) for value in values).encode("utf-8")
    return prefix + hashlib.sha256(payload).hexdigest()[:24]


def _events(mir: PerformanceMIR) -> list[dict[str, Any]]:
    events = []
    for function in mir.functions:
        flat_index = 0
        for block in function.blocks:
            for block_index, instruction in enumerate(block.instructions):
                if instruction.op not in _TRANSFER_OPS:
                    flat_index += 1
                    continue
                attributes = instruction.attribute_map
                events.append(
                    {
                        "function": function.name,
                        "block": block.id,
                        "block_index": block_index,
                        "flat_index": flat_index,
                        "instruction_id": instruction.id,
                        "op": instruction.op,
                        "operands": list(instruction.operands),
                        "borrow_id": attributes.get("borrow_id"),
                        "borrow_depth": attributes.get("borrow_depth"),
                        "callee": attributes.get("callee"),
                        "call_scope": attributes.get("call_scope"),
                        "caller_scope": attributes.get("caller_scope"),
                        "last_use_line": attributes.get("last_use_line"),
                        "non_escaping": attributes.get("non_escaping"),
                        "parameter": attributes.get("parameter"),
                        "parent_borrow": attributes.get("parent_borrow"),
                        "range_relation": attributes.get("range_relation"),
                        "return_origin": attributes.get("return_origin"),
                        "return_transfer": attributes.get("return_transfer"),
                        "returned_child_borrow": attributes.get(
                            "returned_child_borrow"
                        ),
                        "returned_value": attributes.get("returned_value"),
                        "root_owner": attributes.get("root_owner"),
                        "source": _source(instruction.source),
                    }
                )
                flat_index += 1
    return events


def _calls(mir: PerformanceMIR) -> list[dict[str, Any]]:
    calls = []
    for function in mir.functions:
        flat_index = 0
        for block in function.blocks:
            for block_index, instruction in enumerate(block.instructions):
                if (
                    instruction.op == "call"
                    and instruction.attribute_map.get("return_ownership")
                    == "borrowed_transfer"
                ):
                    attributes = instruction.attribute_map
                    calls.append(
                        {
                            "caller": function.name,
                            "callee": attributes.get("callee"),
                            "block": block.id,
                            "block_index": block_index,
                            "flat_index": flat_index,
                            "instruction_id": instruction.id,
                            "result": instruction.result,
                            "direct_call": attributes.get("direct") is True,
                            "call_scope": attributes.get("call_scope"),
                            "borrow_ids": list(
                                attributes.get("argument_borrow_ids", ())
                            ),
                            "borrow_kinds": list(
                                attributes.get("argument_borrow_kinds", ())
                            ),
                            "return_origin": attributes.get(
                                "borrowed_return_origin"
                            ),
                            "source": _source(instruction.source),
                        }
                    )
                flat_index += 1
    return calls


def _instruction_uses(
    mir: PerformanceMIR,
) -> dict[tuple[str, str], list[tuple[int, int]]]:
    uses: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for function in mir.functions:
        flat_index = 0
        for block in function.blocks:
            for instruction in block.instructions:
                if instruction.op not in _TRANSFER_OPS:
                    line = instruction.source.line if instruction.source else 0
                    for operand in instruction.operands:
                        uses[(function.name, operand)].append((flat_index, line))
                flat_index += 1
    return uses


def validate_bytes_borrowed_return_mir(mir: PerformanceMIR) -> dict[str, Any]:
    """Validate unique origin, transfer pairing, root identity, and caller last-use."""

    events = _events(mir)
    calls = _calls(mir)
    uses = _instruction_uses(mir)
    starts = {
        event["borrow_id"]: event
        for event in events
        if event["op"] in _BORROW_STARTS and event["return_transfer"] is True
    }
    continues = {
        event["borrow_id"]: event
        for event in events
        if event["op"] == "caller_borrow_continue"
    }
    ends = {
        event["borrow_id"]: event
        for event in events
        if event["op"] in _BORROW_ENDS and event["return_transfer"] is True
    }
    if len(starts) != sum(
        event["op"] in _BORROW_STARTS and event["return_transfer"] is True
        for event in events
    ):
        raise ValueError("duplicate borrowed-return start identity")
    if set(starts) != set(continues) or set(starts) != set(ends):
        raise ValueError("borrowed-return start/continue/end sets differ")
    call_by_borrow: dict[str, dict[str, Any]] = {}
    for call in calls:
        if not call["direct_call"] or call["call_scope"] != "direct_synchronous":
            raise ValueError("borrowed return requires direct synchronous call")
        candidates = [
            borrow_id
            for borrow_id, kind in zip(
                call["borrow_ids"], call["borrow_kinds"], strict=True
            )
            if kind in _BORROW_STARTS and borrow_id in starts
        ]
        if len(candidates) != 1:
            raise ValueError("AmbiguousBorrowReturnOrigin")
        borrow_id = candidates[0]
        if borrow_id in call_by_borrow:
            raise ValueError("borrow transfer belongs to multiple calls")
        call_by_borrow[borrow_id] = call
    if set(call_by_borrow) != set(starts):
        raise ValueError("borrowed-return marker has no matching call")
    roots = set()
    caller_scopes = set()
    for borrow_id, start in starts.items():
        call = call_by_borrow[borrow_id]
        continuation = continues[borrow_id]
        end = ends[borrow_id]
        if not (
            start["function"]
            == call["caller"]
            == continuation["function"]
            == end["function"]
        ):
            raise ValueError(f"borrow transfer crosses caller function: {borrow_id}")
        if not (
            start["flat_index"]
            < call["flat_index"]
            < continuation["flat_index"]
            < end["flat_index"]
        ):
            raise ValueError(f"borrow ends before caller last use: {borrow_id}")
        if start["call_scope"] != "direct_synchronous":
            raise ValueError(f"invalid borrowed-return scope: {borrow_id}")
        if start["non_escaping"] is not True:
            raise ValueError(f"escaping returned borrow: {borrow_id}")
        root_set = {
            start["root_owner"],
            continuation["root_owner"],
            end["root_owner"],
        }
        if None in root_set or len(root_set) != 1:
            raise ValueError(f"borrowed-return root identity changed: {borrow_id}")
        root = next(iter(root_set))
        if str(root).startswith("dynamic_root:"):
            raise ValueError(f"AmbiguousBorrowReturnOrigin: {borrow_id}")
        roots.add(str(root))
        if continuation["return_origin"] != call["return_origin"]:
            raise ValueError(f"borrowed-return origin changed: {borrow_id}")
        caller_scope = str(continuation["caller_scope"])
        if not caller_scope:
            raise ValueError(f"borrowed return lacks caller scope: {borrow_id}")
        caller_scopes.add(caller_scope)
        semantic_uses = uses.get((call["caller"], str(call["result"])), [])
        last_use_line = max(
            (line for _index, line in semantic_uses),
            default=int(continuation["last_use_line"]),
        )
        declared_last_use = continuation["last_use_line"]
        end_line = (end["source"] or {}).get("line", 0)
        if declared_last_use != last_use_line or end_line < last_use_line:
            raise ValueError(f"borrow ends before proven last use: {borrow_id}")
    transfers = [
        event for event in events if event["op"] == "borrow_return_transfer"
    ]
    if not transfers:
        raise ValueError("borrowed-return MIR has no callee transfer")
    for transfer in transfers:
        if (
            len(transfer["operands"]) != 2
            or not str(transfer["return_origin"]).startswith("parameter:")
            or transfer["range_relation"] != "return_inside_borrowed_parameter"
            or transfer["non_escaping"] is not True
        ):
            raise ValueError("invalid callee borrow-return transfer")
        if transfer["root_owner"] is None:
            raise ValueError("callee borrow-return transfer lacks root owner")
    maximum_chain = max(
        (
            int(transfer["borrow_depth"])
            for transfer in transfers
            if isinstance(transfer["borrow_depth"], int)
        ),
        default=0,
    )
    if maximum_chain > 2:
        raise ValueError("borrowed-return chain exceeds 2")
    return {
        "balanced": True,
        "borrowed_return_call_count": len(calls),
        "callee_transfer_count": len(transfers),
        "caller_continue_count": len(continues),
        "caller_scope_count": len(caller_scopes),
        "maximum_chain_depth": maximum_chain,
        "root_owner_sets": sorted(roots),
        "unique_origin_per_call": True,
        "last_use_proven": True,
    }


def bytes_borrowed_return_mir_manifest(mir: PerformanceMIR) -> dict[str, Any]:
    validation = validate_bytes_borrowed_return_mir(mir)
    return {
        "schema_version": BYTES_BORROWED_RETURN_MIR_SCHEMA_VERSION,
        "contract": BYTES_BORROWED_RETURN_MIR_CONTRACT,
        "base_performance_mir_schema_version": mir.schema_version,
        "source_sha256": mir.source_sha256,
        "operations": {
            "borrow_argument": "borrow_argument",
            "reborrow_argument": "reborrow_argument",
            "borrow_return_transfer": "borrow_return_transfer",
            "direct_call": "call.direct=true;call_scope=direct_synchronous",
            "caller_borrow_continue": "caller_borrow_continue",
            "borrow_end": "borrow_end after caller last use",
        },
        "events": _events(mir),
        "calls": _calls(mir),
        "validation": validation,
    }


def bytes_borrowed_return_hir_manifest(
    program: NativeHIRProgram,
) -> dict[str, Any]:
    if program.performance_mir is None:
        raise ValueError("Bytes borrowed-return HIR requires MIR provenance")
    mir_manifest = bytes_borrowed_return_mir_manifest(program.performance_mir)
    symbols = {item.name: item for item in program.symbols}
    events = mir_manifest["events"]
    ends = {
        event["borrow_id"]: event
        for event in events
        if event["op"] in _BORROW_ENDS and event["return_transfer"] is True
    }
    relationships = []
    for continuation in (
        event for event in events if event["op"] == "caller_borrow_continue"
    ):
        caller = symbols[str(continuation["function"])]
        callee = symbols[str(continuation["callee"])]
        origin_name = str(continuation["return_origin"]).removeprefix(
            "parameter:"
        )
        origin = symbols.get(origin_name)
        root_name = str(continuation["root_owner"])
        root = symbols.get(root_name)
        scope_name = str(continuation["caller_scope"])
        scope = symbols.get(scope_name)
        end = ends[str(continuation["borrow_id"])]
        child_id = _digest(
            "brr_",
            continuation["borrow_id"],
            continuation["returned_value"],
            scope_name,
        )
        relationships.append(
            {
                "kind": "borrowed_return_transfer",
                "root_owner": {
                    "name": root_name,
                    "symbol_id": root.symbol_id if root else None,
                    "revision_id": root.revision_id if root else None,
                },
                "borrowed_source_parameter": {
                    "name": origin_name,
                    "symbol_id": origin.symbol_id if origin else None,
                    "revision_id": origin.revision_id if origin else None,
                },
                "return_origin": origin_name,
                "parent_borrow": continuation["parent_borrow"],
                "returned_child_borrow": {
                    "semantic_id": child_id,
                    "revision_id": _digest(
                        "rev_", child_id, continuation["root_owner"]
                    ),
                },
                "caller": {
                    "name": caller.name,
                    "symbol_id": caller.symbol_id,
                    "revision_id": caller.revision_id,
                },
                "callee": {
                    "name": callee.name,
                    "symbol_id": callee.symbol_id,
                    "revision_id": callee.revision_id,
                },
                "caller_scope": {
                    "name": scope_name,
                    "symbol_id": scope.symbol_id if scope else caller.symbol_id,
                    "revision_id": (
                        scope.revision_id if scope else caller.revision_id
                    ),
                },
                "last_use": {
                    "line": continuation["last_use_line"],
                    "source": end["source"],
                },
                "call_source": continuation["source"],
                "call_scope": continuation["call_scope"],
                "non_escaping": continuation["non_escaping"],
            }
        )
    return {
        "schema_version": BYTES_BORROWED_RETURN_HIR_SCHEMA_VERSION,
        "contract": BYTES_BORROWED_RETURN_HIR_CONTRACT,
        "source_sha256": program.cst.source_sha256,
        "lifetime_annotations_in_surface": 0,
        "scope": {
            "calls": "direct_synchronous",
            "borrowed_source_parameters": 1,
            "borrowed_return_chain_maximum": 2,
            "caller_storage": "local_only",
            "general_lifetime_solver": False,
            "view": "immutable",
        },
        "relationships": relationships,
        "validation": mir_manifest["validation"],
    }


def bytes_borrowed_return_abi_manifest() -> dict[str, Any]:
    return {
        "schema_version": BYTES_BORROWED_RETURN_ABI_VERSION,
        "contract": BYTES_BORROWED_RETURN_ABI_CONTRACT,
        "parameter": {
            "c_type": "meldra_bytes_view",
            "fields": ["const uint8_t *data", "uint64_t length"],
            "ownership": "non_owning",
        },
        "return": {
            "c_type": "meldra_bytes_view",
            "fields": ["const uint8_t *data", "uint64_t length"],
            "ownership": "borrowed_transfer_to_caller",
        },
        "pointer_relation": "returned.data == input.data + proven_offset",
        "range_relations": [
            "returned range inside input range",
            "input range inside root owner range",
        ],
        "allocation": False,
        "payload_copy": False,
        "reference_counting": False,
        "new_owner": False,
    }


__all__ = [
    "BYTES_BORROWED_RETURN_ABI_CONTRACT",
    "BYTES_BORROWED_RETURN_ABI_VERSION",
    "BYTES_BORROWED_RETURN_HIR_CONTRACT",
    "BYTES_BORROWED_RETURN_HIR_SCHEMA_VERSION",
    "BYTES_BORROWED_RETURN_MIR_CONTRACT",
    "BYTES_BORROWED_RETURN_MIR_SCHEMA_VERSION",
    "bytes_borrowed_return_abi_manifest",
    "bytes_borrowed_return_hir_manifest",
    "bytes_borrowed_return_mir_manifest",
    "validate_bytes_borrowed_return_mir",
]
