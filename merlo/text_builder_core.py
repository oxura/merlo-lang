"""Versioned HIR, MIR, and ABI contracts for UTF-8-valid TextBuilder."""

from __future__ import annotations

import hashlib
from typing import Any

from .native_hir import NativeHIRProgram
from .performance_mir import PerformanceMIR, TEXT_BUILDER

TEXT_BUILDER_HIR_SCHEMA_VERSION = 1
TEXT_BUILDER_MIR_SCHEMA_VERSION = 1
TEXT_BUILDER_ABI_VERSION = 1
TEXT_BUILDER_HIR_CONTRACT = "meldra.text-builder-hir.v1"
TEXT_BUILDER_MIR_CONTRACT = "meldra.text-builder-mir.v1"
TEXT_BUILDER_ABI_CONTRACT = "meldra.text-builder-abi.v1"

_TEXT_BUILDER_OPS = {
    "text_builder_create",
    "text_builder_append_account",
    "text_builder_scalar_width",
    "text_builder_push_ascii",
    "text_builder_push_scalar",
    "text_builder_extend",
    "text_builder_view",
    "text_builder_finish_transfer",
    "text_builder_drop",
}
_REUSED_BUILDER_OPS = {
    "builder_len",
    "builder_capacity",
    "builder_reserve",
    "builder_grow",
    "allocation",
    "payload_copy",
    "free",
}
_COST_MODEL = {
    "len_bytes": "O(1)",
    "capacity_bytes": "O(1)",
    "reserve_bytes": "amortized growth in bytes",
    "push_ascii": "amortized O(1), one required append byte",
    "push_scalar": "amortized O(1), one to four required append bytes",
    "extend": "O(view.len_bytes), one semantic payload copy",
    "as_view": "O(1), zero allocation, zero copy, zero retain/release",
    "finish": "O(1), zero allocation, zero copy, zero UTF-8 validation",
}


def _source(value: Any) -> dict[str, Any] | None:
    return value.to_dict() if value is not None else None


def _events(mir: PerformanceMIR) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for function in mir.functions:
        for block in function.blocks:
            for instruction in block.instructions:
                attributes = instruction.attribute_map
                if instruction.op not in _TEXT_BUILDER_OPS and not (
                    instruction.op in _REUSED_BUILDER_OPS
                    and attributes.get("builder_type") == "TextBuilder"
                ):
                    continue
                events.append(
                    {
                        "function": function.name,
                        "block": block.id,
                        "instruction_id": instruction.id,
                        "op": instruction.op,
                        "result": instruction.result,
                        "operands": list(instruction.operands),
                        "attributes": attributes,
                        "source": _source(instruction.source),
                    }
                )
    return events


def validate_text_builder_mir(mir: PerformanceMIR) -> dict[str, Any]:
    events = _events(mir)
    identities = {
        (event["function"], event["block"], event["instruction_id"])
        for event in events
    }
    operations = {event["op"] for event in events}
    finishes = [
        event
        for event in events
        if event["op"] == "text_builder_finish_transfer"
    ]
    views = [
        event for event in events if event["op"] == "text_builder_view"
    ]
    appends = [
        event
        for event in events
        if event["op"]
        in {
            "text_builder_push_ascii",
            "text_builder_push_scalar",
            "text_builder_extend",
        }
    ]
    return {
        "event_identity_unique": len(identities) == len(events),
        "source_mappings_complete": all(
            event["source"] is not None for event in events
        ),
        "operations_present": sorted(operations),
        "all_append_forms_present": {
            "text_builder_push_ascii",
            "text_builder_push_scalar",
            "text_builder_extend",
        }.issubset(operations),
        "utf8_invariant_explicit": bool(appends)
        and all(
            event["attributes"].get("utf8_invariant")
            == "payload_0_to_length_valid"
            for event in appends
        ),
        "view_zero_copy": bool(views)
        and all(
            event["attributes"].get("zero_copy") is True
            and event["attributes"].get("payload_copies") == 0
            and event["attributes"].get("retain_release") == 0
            for event in views
        ),
        "finish_zero_copy": bool(finishes)
        and all(
            event["attributes"].get("pointer_identity") == "preserved"
            and event["attributes"].get("length_identity") == "preserved"
            and event["attributes"].get("capacity_identity") == "preserved"
            and event["attributes"].get("allocations") == 0
            and event["attributes"].get("payload_copies") == 0
            and event["attributes"].get("validation_passes") == 0
            for event in finishes
        ),
        "finish_returns_text": any(
            event["op"] == "text_builder_finish_transfer"
            for event in events
        ),
        "growth_policy_reused": any(
            event["op"] == "builder_grow"
            and event["attributes"].get("growth_policy")
            == "zero_then_max_8_required_then_double"
            for event in events
        ),
    }


def text_builder_hir_manifest(program: NativeHIRProgram) -> dict[str, Any]:
    mir = program.performance_mir
    if mir is None:
        raise ValueError("TextBuilder HIR manifest requires Performance MIR")
    functions = []
    symbols = {symbol.name: symbol for symbol in program.symbols}
    for function in mir.functions:
        parameters = [
            parameter
            for parameter in function.parameters
            if parameter.type == TEXT_BUILDER
        ]
        if not parameters and function.return_type != TEXT_BUILDER:
            continue
        symbol = symbols[function.name]
        functions.append(
            {
                "name": function.name,
                "symbol_id": symbol.symbol_id,
                "revision_id": symbol.revision_id,
                "source": symbol.source.to_dict(),
                "text_builder_parameters": [
                    parameter.name for parameter in parameters
                ],
                "return_type": function.return_type.name,
            }
        )
    return {
        "schema_version": TEXT_BUILDER_HIR_SCHEMA_VERSION,
        "contract": TEXT_BUILDER_HIR_CONTRACT,
        "base_native_hir_schema_version": program.schema_version,
        "source_sha256": program.cst.source_sha256,
        "representation": {
            "storage": "BytesBuilder pointer-length-capacity-state",
            "payload_invariant": "data[0:length] is valid RFC 3629 UTF-8",
            "states": ["Live", "Moved", "Finished", "Dropped"],
            "view": "borrowed TextView over current payload",
            "finish": "ownership transfer directly to Text",
        },
        "cost_model": _COST_MODEL,
        "functions": functions,
        "semantic_identities_present": all(
            function["symbol_id"] and function["revision_id"]
            for function in functions
        ),
        "source_mappings_present": all(
            function["source"] is not None for function in functions
        ),
        "lifetime_annotations_in_surface": 0,
        "allocator_annotations_in_surface": 0,
        "retain_release_syntax_in_surface": 0,
    }


def text_builder_mir_manifest(mir: PerformanceMIR) -> dict[str, Any]:
    return {
        "schema_version": TEXT_BUILDER_MIR_SCHEMA_VERSION,
        "contract": TEXT_BUILDER_MIR_CONTRACT,
        "base_performance_mir_schema_version": mir.schema_version,
        "source_sha256": mir.source_sha256,
        "cost_model": _COST_MODEL,
        "events": _events(mir),
        "validation": validate_text_builder_mir(mir),
    }


def text_builder_abi_manifest() -> dict[str, Any]:
    payload = {
        "schema_version": TEXT_BUILDER_ABI_VERSION,
        "contract": TEXT_BUILDER_ABI_CONTRACT,
        "descriptor": (
            "uint8_t *data; uint64_t length; uint64_t capacity; "
            "uint64_t active_views; uint8_t state"
        ),
        "representation_reused_from": "BytesBuilder",
        "growth_policy": "zero_then_max_8_required_then_double",
        "payload_invariant": "data[0:length] is valid RFC 3629 UTF-8",
        "spare_capacity_validity": "excluded",
        "view": {
            "descriptor": "const uint8_t *data; uint64_t length",
            "pointer": "builder.data",
            "payload_copies": 0,
            "retains": 0,
            "releases": 0,
        },
        "finish": {
            "result": "Text",
            "pointer": "builder.data",
            "length": "builder.length",
            "capacity": "builder.capacity",
            "allocations": 0,
            "payload_copies": 0,
            "validation_passes": 0,
        },
        "states": ["Live", "Moved", "Finished", "Dropped"],
        "cost_model": _COST_MODEL,
    }
    return {
        **payload,
        "sha256": hashlib.sha256(
            repr(sorted(payload.items())).encode()
        ).hexdigest(),
    }


__all__ = [
    "TEXT_BUILDER_ABI_CONTRACT",
    "TEXT_BUILDER_ABI_VERSION",
    "TEXT_BUILDER_HIR_CONTRACT",
    "TEXT_BUILDER_HIR_SCHEMA_VERSION",
    "TEXT_BUILDER_MIR_CONTRACT",
    "TEXT_BUILDER_MIR_SCHEMA_VERSION",
    "text_builder_abi_manifest",
    "text_builder_hir_manifest",
    "text_builder_mir_manifest",
    "validate_text_builder_mir",
]
