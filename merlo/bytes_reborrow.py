"""Versioned HIR/MIR contracts for compositional BytesView reborrows."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from .native_hir import NativeHIRProgram
from .performance_mir import MIRInstruction, PerformanceMIR


BYTES_REBORROW_HIR_SCHEMA_VERSION = 1
BYTES_REBORROW_MIR_SCHEMA_VERSION = 1
BYTES_REBORROW_ABI_VERSION = 1
BYTES_REBORROW_HIR_CONTRACT = "meldra.bytes-reborrow-hir.v1"
BYTES_REBORROW_MIR_CONTRACT = "meldra.bytes-reborrow-mir.v1"
BYTES_REBORROW_ABI_CONTRACT = "meldra.bytes-reborrow-abi.v1"
_REBORROW_STARTS = {"borrow_argument", "reborrow_argument"}
_REBORROW_ENDS = {"borrow_end", "reborrow_end"}


def _digest(prefix: str, *values: Any) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return prefix + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source(instruction: MIRInstruction) -> dict[str, Any] | None:
    return instruction.source.to_dict() if instruction.source is not None else None


def _events(mir: PerformanceMIR) -> list[dict[str, Any]]:
    events = []
    for function in mir.functions:
        for block in function.blocks:
            for index, instruction in enumerate(block.instructions):
                if instruction.op not in _REBORROW_STARTS | _REBORROW_ENDS:
                    continue
                attributes = instruction.attribute_map
                events.append(
                    {
                        "function": function.name,
                        "block": block.id,
                        "index": index,
                        "instruction_id": instruction.id,
                        "op": instruction.op,
                        "borrow_id": attributes.get("borrow_id"),
                        "borrow_depth": attributes.get("borrow_depth"),
                        "callee": attributes.get("callee"),
                        "parameter": attributes.get("parameter"),
                        "parent_borrow": attributes.get("parent_borrow"),
                        "root_owner": attributes.get("root_owner"),
                        "call_scope": attributes.get("call_scope"),
                        "non_escaping": attributes.get("non_escaping"),
                        "end_order": attributes.get("end_order"),
                        "source": _source(instruction),
                    }
                )
    return events


def _calls(mir: PerformanceMIR) -> list[dict[str, Any]]:
    calls = []
    for function in mir.functions:
        for block in function.blocks:
            for index, instruction in enumerate(block.instructions):
                if instruction.op != "call":
                    continue
                attributes = instruction.attribute_map
                borrow_ids = list(attributes.get("argument_borrow_ids", ()))
                borrow_kinds = list(attributes.get("argument_borrow_kinds", ()))
                if not any(kind in _REBORROW_STARTS for kind in borrow_kinds):
                    continue
                calls.append(
                    {
                        "caller": function.name,
                        "callee": attributes.get("callee"),
                        "block": block.id,
                        "index": index,
                        "instruction_id": instruction.id,
                        "direct_call": attributes.get("direct") is True,
                        "call_scope": attributes.get("call_scope"),
                        "borrow_ids": borrow_ids,
                        "borrow_kinds": borrow_kinds,
                        "source": _source(instruction),
                    }
                )
    return calls


def validate_bytes_reborrow_mir(mir: PerformanceMIR) -> dict[str, Any]:
    """Validate lexical and compositional start/call/end nesting."""

    events = _events(mir)
    calls = _calls(mir)
    starts = {
        event["borrow_id"]: event
        for event in events
        if event["op"] in _REBORROW_STARTS
    }
    ends = {
        event["borrow_id"]: event
        for event in events
        if event["op"] in _REBORROW_ENDS
    }
    if len(starts) != sum(event["op"] in _REBORROW_STARTS for event in events):
        raise ValueError("duplicate reborrow start identity")
    if len(ends) != sum(event["op"] in _REBORROW_ENDS for event in events):
        raise ValueError("duplicate reborrow end identity")
    if set(starts) != set(ends):
        raise ValueError("reborrow start/end sets differ")
    if None in starts or None in ends:
        raise ValueError("reborrow identity is missing")
    call_by_borrow: dict[str, dict[str, Any]] = {}
    for call in calls:
        if not call["direct_call"] or call["call_scope"] != "direct_synchronous":
            raise ValueError("reborrow requires direct synchronous call")
        for borrow_id, kind in zip(
            call["borrow_ids"], call["borrow_kinds"], strict=True
        ):
            if kind not in _REBORROW_STARTS:
                continue
            if borrow_id in call_by_borrow:
                raise ValueError("reborrow belongs to multiple calls")
            call_by_borrow[borrow_id] = call
    if set(starts) != set(call_by_borrow):
        raise ValueError("reborrow marker has no matching direct call")
    for borrow_id, start in starts.items():
        end = ends[borrow_id]
        call = call_by_borrow[borrow_id]
        if not (
            start["function"] == call["caller"] == end["function"]
            and start["block"] == call["block"] == end["block"]
            and start["index"] < call["index"] < end["index"]
            and start["callee"] == call["callee"]
        ):
            raise ValueError(
                f"parent ends before child or branch end is unbalanced: {borrow_id}"
            )
        if (
            start["borrow_depth"] != end["borrow_depth"]
            or start["parent_borrow"] != end["parent_borrow"]
            or start["root_owner"] != end["root_owner"]
            or start["call_scope"] != end["call_scope"]
            or start["non_escaping"] != end["non_escaping"]
        ):
            raise ValueError(f"reborrow end metadata changed: {borrow_id}")
        if start["source"] is None or end["source"] is None:
            raise ValueError(f"reborrow source mapping is missing: {borrow_id}")
        if (
            start["call_scope"] != "direct_synchronous"
            or start["non_escaping"] is not True
        ):
            raise ValueError(f"invalid reborrow scope: {borrow_id}")
        if not start["root_owner"]:
            raise ValueError(f"reborrow root owner is missing: {borrow_id}")
        if end["end_order"] != "child_before_parent":
            raise ValueError(f"invalid reborrow end order: {borrow_id}")
        if start["op"] == "borrow_argument":
            if start["parent_borrow"] is not None or start["borrow_depth"] != 1:
                raise ValueError(f"invalid root borrow metadata: {borrow_id}")
            if end["op"] != "borrow_end":
                raise ValueError(f"root borrow lacks parent borrow_end: {borrow_id}")
        else:
            if start["parent_borrow"] is None:
                raise ValueError(f"child reborrow lacks parent identity: {borrow_id}")
            depth = start["borrow_depth"]
            if not isinstance(depth, int) or depth not in {2, 3}:
                raise ValueError(f"invalid reborrow depth: {borrow_id}")
            if end["op"] != "reborrow_end":
                raise ValueError(f"child reborrow lacks reborrow_end: {borrow_id}")
    roots_by_function_parameter: dict[tuple[str, str], set[str]] = defaultdict(set)
    depths_by_function_parameter: dict[tuple[str, str], set[int]] = defaultdict(set)
    for start in starts.values():
        callee = str(start["callee"])
        parameter = str(start["parameter"])
        root_owner = str(start["root_owner"])
        roots_by_function_parameter[(callee, parameter)].add(root_owner)
        if isinstance(start["borrow_depth"], int):
            depths_by_function_parameter[(callee, parameter)].add(start["borrow_depth"])
    for start in starts.values():
        if start["op"] != "reborrow_argument":
            continue
        source_parameter = str(start["parent_borrow"]).removeprefix("parameter:")
        if "." not in source_parameter:
            raise ValueError("reborrow parent parameter identity is malformed")
        source_function, source_name = source_parameter.split(".", 1)
        if source_function != start["function"]:
            raise ValueError(
                f"reborrow parent belongs to another function: {start['borrow_id']}"
            )
        expected_roots = roots_by_function_parameter.get((source_function, source_name), set())
        if expected_roots and start["root_owner"] not in expected_roots:
            raise ValueError(
                f"root owner identity changed across reborrow: {start['borrow_id']}"
            )
        expected_depths = depths_by_function_parameter.get((source_function, source_name), set())
        if expected_depths and start["borrow_depth"] not in {
            depth + 1 for depth in expected_depths
        }:
            raise ValueError(
                f"reborrow depth does not extend parent: {start['borrow_id']}"
            )
    return {
        "event_count": len(events),
        "start_count": len(starts),
        "end_count": len(ends),
        "call_count": len(calls),
        "maximum_depth": max(
            (
                event["borrow_depth"]
                for event in starts.values()
                if isinstance(event["borrow_depth"], int)
            ),
            default=0,
        ),
        "root_owner_sets": {
            f"{function}.{parameter}": sorted(roots)
            for (function, parameter), roots in sorted(roots_by_function_parameter.items())
        },
        "balanced": True,
    }


def bytes_reborrow_hir_manifest(program: NativeHIRProgram) -> dict[str, Any]:
    if program.performance_mir is None:
        raise ValueError("Bytes reborrow HIR requires Performance MIR provenance")
    validation = validate_bytes_reborrow_mir(program.performance_mir)
    symbols = {item.name: item for item in program.symbols}
    relationships = []
    for event in _events(program.performance_mir):
        if event["op"] not in _REBORROW_STARTS:
            continue
        caller = symbols[event["function"]]
        callee = symbols[event["callee"]]
        parameter = symbols[f"{event['callee']}.{event['parameter']}"]
        root_name = str(event["root_owner"])
        root = symbols.get(root_name)
        parent_name = str(event["parent_borrow"] or root_name).removeprefix("parameter:")
        parent = symbols.get(parent_name)
        child_id = _digest("rbr_", event["borrow_id"], event["source"])
        child_revision = _digest(
            "rev_",
            event["op"],
            event["borrow_depth"],
            event["root_owner"],
            event["parent_borrow"],
            event["callee"],
            event["parameter"],
        )
        relationships.append(
            {
                "kind": (
                    "root_borrow"
                    if event["op"] == "borrow_argument"
                    else "child_reborrow"
                ),
                "root_owner": {
                    "name": root_name,
                    "symbol_id": root.symbol_id if root else None,
                    "revision_id": root.revision_id if root else None,
                },
                "parent_borrow": {
                    "name": parent_name,
                    "symbol_id": parent.symbol_id if parent else None,
                    "revision_id": parent.revision_id if parent else None,
                },
                "child_reborrow": {
                    "semantic_id": child_id,
                    "revision_id": child_revision,
                },
                "borrow_depth": event["borrow_depth"],
                "call_scope": event["call_scope"],
                "non_escaping": event["non_escaping"],
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
                "parameter": {
                    "name": parameter.name,
                    "symbol_id": parameter.symbol_id,
                    "revision_id": parameter.revision_id,
                },
                "source": event["source"],
            }
        )
    return {
        "schema_version": BYTES_REBORROW_HIR_SCHEMA_VERSION,
        "contract": BYTES_REBORROW_HIR_CONTRACT,
        "source_sha256": program.cst.source_sha256,
        "lifetime_annotations_in_surface": 0,
        "scope": {
            "calls": "direct_synchronous",
            "maximum_depth": 3,
            "view": "immutable_non_escaping",
            "recursion": "rejected",
            "async": "rejected",
            "dynamic_dispatch": "rejected",
        },
        "relationships": relationships,
        "validation": validation,
    }


def bytes_reborrow_mir_manifest(mir: PerformanceMIR) -> dict[str, Any]:
    validation = validate_bytes_reborrow_mir(mir)
    events = _events(mir)
    ends = [event for event in events if event["op"] in _REBORROW_ENDS]
    end_order = sorted(
        (
            {
                "borrow_id": event["borrow_id"],
                "op": event["op"],
                "borrow_depth": event["borrow_depth"],
                "root_owner": event["root_owner"],
            }
            for event in ends
        ),
        key=lambda item: (
            str(item["root_owner"]),
            -(item["borrow_depth"] if isinstance(item["borrow_depth"], int) else 0),
            str(item["borrow_id"]),
        ),
    )
    return {
        "schema_version": BYTES_REBORROW_MIR_SCHEMA_VERSION,
        "contract": BYTES_REBORROW_MIR_CONTRACT,
        "base_performance_mir_schema_version": mir.schema_version,
        "source_sha256": mir.source_sha256,
        "operations": {
            "borrow_argument": "borrow_argument",
            "reborrow_argument": "reborrow_argument",
            "direct_call": "call.direct=true;call_scope=direct_synchronous",
            "reborrow_end": "reborrow_end",
            "parent_borrow_end": "borrow_end",
        },
        "events": events,
        "calls": _calls(mir),
        "end_order": end_order,
        "validation": validation,
    }


def bytes_reborrow_abi_manifest() -> dict[str, Any]:
    return {
        "schema_version": BYTES_REBORROW_ABI_VERSION,
        "contract": BYTES_REBORROW_ABI_CONTRACT,
        "descriptor": {
            "c_type": "meldra_bytes_view",
            "fields": ["const uint8_t *data", "uint64_t length"],
            "ownership_fields": 0,
            "reference_count_fields": 0,
        },
        "reborrow": "same descriptor value; same root payload pointer; no new owner",
        "maximum_depth": 3,
        "lifetime_annotations": 0,
    }


__all__ = [
    "BYTES_REBORROW_ABI_CONTRACT",
    "BYTES_REBORROW_ABI_VERSION",
    "BYTES_REBORROW_HIR_CONTRACT",
    "BYTES_REBORROW_HIR_SCHEMA_VERSION",
    "BYTES_REBORROW_MIR_CONTRACT",
    "BYTES_REBORROW_MIR_SCHEMA_VERSION",
    "bytes_reborrow_abi_manifest",
    "bytes_reborrow_hir_manifest",
    "bytes_reborrow_mir_manifest",
    "validate_bytes_reborrow_mir",
]
