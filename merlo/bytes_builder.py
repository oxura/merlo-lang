"""Versioned HIR, MIR, and ABI contracts for BytesBuilder capacity growth."""

from __future__ import annotations

import hashlib
from typing import Any

from .native_hir import NativeHIRProgram
from .performance_mir import PerformanceMIR

BYTES_BUILDER_HIR_SCHEMA_VERSION = 1
BYTES_BUILDER_MIR_SCHEMA_VERSION = 1
BYTES_BUILDER_ABI_VERSION = 1
BYTES_BUILDER_HIR_CONTRACT = "meldra.bytes-builder-hir.v1"
BYTES_BUILDER_MIR_CONTRACT = "meldra.bytes-builder-mir.v1"
BYTES_BUILDER_ABI_CONTRACT = "meldra.bytes-builder-abi.v1"
_GROWTH_POLICY = {
    "initial_capacity": 0,
    "first_growth": "max(8, required)",
    "next_growth": "max(required, current_capacity * 2)",
    "overflow_checks": [
        "length_plus_additional",
        "capacity_times_two",
        "allocation_byte_size",
    ],
}
_BUILDER_OPS = {
    "builder_create",
    "builder_len",
    "builder_capacity",
    "builder_reserve",
    "builder_grow",
    "builder_push",
    "builder_extend",
    "builder_view",
    "builder_finish_transfer",
    "builder_drop",
    "allocation",
    "payload_copy",
    "free",
    "borrow_end",
}


def _source(value: Any) -> dict[str, Any] | None:
    return value.to_dict() if value is not None else None


def _symbol(program: NativeHIRProgram, name: str) -> dict[str, str] | None:
    matches = [
        item
        for item in program.symbols
        if item.name == name or item.name.endswith("." + name)
    ]
    if len(matches) != 1:
        return None
    item = matches[0]
    return {
        "name": item.name,
        "symbol_id": item.symbol_id,
        "revision_id": item.revision_id,
    }


def _events(mir: PerformanceMIR) -> list[dict[str, Any]]:
    events = []
    for function in mir.functions:
        for block in function.blocks:
            for instruction in block.instructions:
                if instruction.op not in _BUILDER_OPS:
                    continue
                attributes = instruction.attribute_map
                events.append(
                    {
                        "function": function.name,
                        "block": block.id,
                        "instruction_id": instruction.id,
                        "op": instruction.op,
                        "result": instruction.result,
                        "operands": list(instruction.operands),
                        "source": _source(instruction.source),
                        "attributes": attributes,
                    }
                )
    return events


def validate_bytes_builder_mir(mir: PerformanceMIR) -> dict[str, Any]:
    events = _events(mir)
    creates = [item for item in events if item["op"] == "builder_create"]
    finishes = [
        item for item in events if item["op"] == "builder_finish_transfer"
    ]
    drops = [item for item in events if item["op"] == "builder_drop"]
    views = [item for item in events if item["op"] == "builder_view"]
    borrow_ends = [item for item in events if item["op"] == "borrow_end"]
    grows = [item for item in events if item["op"] == "builder_grow"]
    event_ids = {
        (item["function"], item["block"], item["instruction_id"])
        for item in events
    }
    if len(event_ids) != len(events):
        raise ValueError("duplicate BytesBuilder MIR event identity")
    if any(item["source"] is None for item in events):
        raise ValueError("BytesBuilder MIR event lacks source mapping")
    view_ids = {
        str(item["attributes"].get("borrow_id")) for item in views
    }
    end_ids = {
        str(item["attributes"].get("borrow_id"))
        for item in borrow_ends
        if item["attributes"].get("builder_owner") is not None
    }
    finish_visible = all(
        item["attributes"].get("pointer_identity") == "preserved"
        and item["attributes"].get("payload_copies") == 0
        for item in finishes
    )
    growth_explicit = all(
        tuple(item["attributes"].get("overflow_checks", ()))
        == tuple(_GROWTH_POLICY["overflow_checks"])
        for item in grows
    )
    return {
        "balanced_builder_views": view_ids == end_ids,
        "builder_view_count": len(views),
        "borrow_end_count": len(end_ids),
        "builder_create_count": len(creates),
        "builder_finish_count": len(finishes),
        "builder_drop_count": len(drops),
        "builder_grow_count": len(grows),
        "finish_transfer_visible": finish_visible,
        "growth_policy_explicit": growth_explicit,
        "automatic_drop_present": any(
            item["attributes"].get("automatic") is True for item in drops
        ),
        "source_mappings_complete": all(
            item["source"] is not None for item in events
        ),
        "states": ["Live", "Moved", "Finished", "Dropped"],
    }


def bytes_builder_hir_manifest(program: NativeHIRProgram) -> dict[str, Any]:
    mir = program.performance_mir
    if mir is None:
        raise ValueError("BytesBuilder HIR manifest requires Performance MIR")
    events = _events(mir)
    builders = []
    for event in events:
        owner = event["attributes"].get("builder_owner")
        if owner is None:
            continue
        local = str(owner).split(".")[-1]
        builders.append(
            {
                "owner": owner,
                "symbol": _symbol(program, local),
                "operation": event["op"],
                "source": event["source"],
                "length": "runtime_descriptor.length",
                "capacity": "runtime_descriptor.capacity",
                "growth_policy": _GROWTH_POLICY,
                "active_view": (
                    {
                        "borrow_id": event["attributes"].get("borrow_id"),
                        "last_use_line": event["attributes"].get(
                            "last_use_line"
                        ),
                    }
                    if event["op"] in {"builder_view", "borrow_end"}
                    else None
                ),
                "finish_transfer": (
                    {
                        "pointer_identity": "preserved",
                        "payload_copies": 0,
                        "state": "Finished",
                    }
                    if event["op"] == "builder_finish_transfer"
                    else None
                ),
                "automatic_drop": event["attributes"].get("automatic")
                is True,
            }
        )
    return {
        "schema_version": BYTES_BUILDER_HIR_SCHEMA_VERSION,
        "contract": BYTES_BUILDER_HIR_CONTRACT,
        "base_native_hir_schema_version": program.schema_version,
        "source_sha256": program.cst.source_sha256,
        "growth_policy": _GROWTH_POLICY,
        "lifetime_annotations_in_surface": 0,
        "allocator_annotations_in_surface": 0,
        "retain_release_syntax_in_surface": 0,
        "builders": builders,
        "symbol_ids_present": all(
            item["symbol"] is not None for item in builders
        ),
        "source_mappings_present": all(
            item["source"] is not None for item in builders
        ),
    }


def bytes_builder_mir_manifest(mir: PerformanceMIR) -> dict[str, Any]:
    return {
        "schema_version": BYTES_BUILDER_MIR_SCHEMA_VERSION,
        "contract": BYTES_BUILDER_MIR_CONTRACT,
        "base_performance_mir_schema_version": mir.schema_version,
        "source_sha256": mir.source_sha256,
        "growth_policy": _GROWTH_POLICY,
        "operations": sorted(_BUILDER_OPS),
        "events": _events(mir),
        "validation": validate_bytes_builder_mir(mir),
    }


def bytes_builder_abi_manifest() -> dict[str, Any]:
    descriptor = (
        "uint8_t *data; uint64_t length; uint64_t capacity; "
        "uint64_t active_views; uint8_t state"
    )
    payload = {
        "schema_version": BYTES_BUILDER_ABI_VERSION,
        "contract": BYTES_BUILDER_ABI_CONTRACT,
        "builder_descriptor": descriptor,
        "bytes_descriptor": (
            "uint8_t *data; uint64_t length; uint64_t capacity; bool live"
        ),
        "finish": {
            "pointer": "builder.data",
            "length": "builder.length",
            "capacity": "builder.capacity",
            "payload_copies": 0,
            "builder_state_after": "Finished",
        },
        "growth": {
            "algorithm": "malloc-copy-free",
            "old_buffer_free": "after successful allocation and copy",
            "allocator_internal_metadata_excluded": True,
        },
    }
    return {
        **payload,
        "sha256": hashlib.sha256(
            repr(sorted(payload.items())).encode()
        ).hexdigest(),
    }


__all__ = [
    "BYTES_BUILDER_ABI_CONTRACT",
    "BYTES_BUILDER_ABI_VERSION",
    "BYTES_BUILDER_HIR_CONTRACT",
    "BYTES_BUILDER_HIR_SCHEMA_VERSION",
    "BYTES_BUILDER_MIR_CONTRACT",
    "BYTES_BUILDER_MIR_SCHEMA_VERSION",
    "bytes_builder_abi_manifest",
    "bytes_builder_hir_manifest",
    "bytes_builder_mir_manifest",
    "validate_bytes_builder_mir",
]
