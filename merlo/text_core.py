"""Versioned HIR, MIR, and ABI contracts for owned UTF-8 Text."""

from __future__ import annotations

import hashlib
from typing import Any

from .native_hir import NativeHIRProgram
from .performance_mir import PerformanceMIR

TEXT_HIR_SCHEMA_VERSION = 1
TEXT_MIR_SCHEMA_VERSION = 1
TEXT_ABI_VERSION = 1
TEXT_HIR_CONTRACT = "meldra.text-hir.v1"
TEXT_MIR_CONTRACT = "meldra.text-mir.v1"
TEXT_ABI_CONTRACT = "meldra.text-abi.v1"

_TEXT_OPS = {
    "utf8_validate",
    "bytes_to_text_transfer",
    "text_to_bytes_transfer",
    "text_from_ascii",
    "text_from_scalar",
    "text_from_surrogate",
    "text_len_bytes",
    "text_view",
    "text_view_as_bytes",
    "text_slice",
    "utf8_boundary_check",
    "utf8_scalar_next",
    "utf8_scalar_count",
    "utf8_decode_is_valid",
    "utf8_decode_take_text",
    "utf8_decode_error_offset",
    "utf8_decode_drop",
    "text_drop",
}
_ZERO_COPY_OPS = {
    "bytes_to_text_transfer",
    "text_to_bytes_transfer",
    "text_view",
    "text_view_as_bytes",
    "text_slice",
    "utf8_decode_take_text",
}
_COST_MODEL = {
    "len_bytes": "O(1)",
    "as_view": "O(1), zero allocation, zero copy",
    "as_bytes": "O(1), zero allocation, zero copy",
    "slice_bytes": "O(1) after bounds and UTF-8 boundary checks",
    "scalar_count": "O(n) in UTF-8 bytes",
    "from_utf8": "O(n) validation, zero payload copies",
    "into_bytes": "O(1), zero allocation, zero copy",
}


def _source(value: Any) -> dict[str, Any] | None:
    return value.to_dict() if value is not None else None


def _events(mir: PerformanceMIR) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for function in mir.functions:
        for block in function.blocks:
            for instruction in block.instructions:
                if instruction.op not in _TEXT_OPS:
                    continue
                events.append(
                    {
                        "function": function.name,
                        "block": block.id,
                        "instruction_id": instruction.id,
                        "op": instruction.op,
                        "result": instruction.result,
                        "operands": list(instruction.operands),
                        "attributes": instruction.attribute_map,
                        "source": _source(instruction.source),
                    }
                )
    return events


def validate_text_mir(mir: PerformanceMIR) -> dict[str, Any]:
    events = _events(mir)
    identities = {
        (event["function"], event["block"], event["instruction_id"])
        for event in events
    }
    if len(identities) != len(events):
        raise ValueError("duplicate Text MIR event identity")
    if any(event["source"] is None for event in events):
        raise ValueError("Text MIR event lacks source mapping")
    operations = {event["op"] for event in events}
    transfers = [
        event
        for event in events
        if event["op"]
        in {"bytes_to_text_transfer", "text_to_bytes_transfer"}
    ]
    return {
        "source_mappings_complete": True,
        "event_identity_unique": True,
        "operations_present": sorted(operations),
        "utf8_validation_present": "utf8_validate" in operations,
        "typed_decode_match_present": {
            "utf8_decode_is_valid",
            "utf8_decode_take_text",
            "utf8_decode_error_offset",
        }.issubset(operations),
        "utf8_boundary_checks_present": (
            "utf8_boundary_check" in operations
        ),
        "scalar_iteration_present": (
            "utf8_scalar_next" in operations
            or "utf8_scalar_count" in operations
        ),
        "zero_copy_operations": sorted(operations & _ZERO_COPY_OPS),
        "transfer_payload_copies_zero": all(
            event["attributes"].get("payload_copies", 0) == 0
            for event in transfers
        ),
        "invalid_input_consumed": any(
            event["op"] == "bytes_to_text_transfer"
            and event["attributes"].get("input_consumed") is True
            for event in events
        ),
    }


def text_hir_manifest(program: NativeHIRProgram) -> dict[str, Any]:
    mir = program.performance_mir
    if mir is None:
        raise ValueError("Text HIR manifest requires Performance MIR")
    events = _events(mir)
    return {
        "schema_version": TEXT_HIR_SCHEMA_VERSION,
        "contract": TEXT_HIR_CONTRACT,
        "base_native_hir_schema_version": program.schema_version,
        "source_sha256": program.cst.source_sha256,
        "representation": {
            "Text": "owned valid UTF-8 bytes",
            "TextView": "borrowed UTF-8 byte range",
            "Utf8Decode": "Valid(Text) | Invalid(error_offset: UInt64)",
        },
        "cost_model": _COST_MODEL,
        "semantic_symbols": [
            {
                "name": symbol.name,
                "kind": symbol.kind,
                "symbol_id": symbol.symbol_id,
                "revision_id": symbol.revision_id,
                "source": _source(symbol.source),
            }
            for symbol in program.symbols
        ],
        "symbol_ids_present": all(
            bool(symbol.symbol_id) for symbol in program.symbols
        ),
        "revision_ids_present": all(
            bool(symbol.revision_id) for symbol in program.symbols
        ),
        "source_mappings_present": all(
            event["source"] is not None for event in events
        ),
        "lifetime_annotations_in_surface": 0,
        "allocator_annotations_in_surface": 0,
        "retain_release_syntax_in_surface": 0,
    }


def text_mir_manifest(mir: PerformanceMIR) -> dict[str, Any]:
    return {
        "schema_version": TEXT_MIR_SCHEMA_VERSION,
        "contract": TEXT_MIR_CONTRACT,
        "base_performance_mir_schema_version": mir.schema_version,
        "source_sha256": mir.source_sha256,
        "cost_model": _COST_MODEL,
        "operations": sorted(_TEXT_OPS),
        "events": _events(mir),
        "validation": validate_text_mir(mir),
    }


def text_abi_manifest() -> dict[str, Any]:
    payload = {
        "schema_version": TEXT_ABI_VERSION,
        "contract": TEXT_ABI_CONTRACT,
        "text_descriptor": (
            "uint8_t *data; uint64_t length; uint64_t capacity; bool live"
        ),
        "text_view_descriptor": (
            "const uint8_t *data; uint64_t length"
        ),
        "cost_model": _COST_MODEL,
        "decode_descriptor": (
            "bool valid; Text text; uint64_t error_offset; bool consumed"
        ),
        "invariant": "Text payload is always valid RFC 3629 UTF-8",
        "invalid_offset": "zero-based first invalid sequence byte",
        "bytes_to_text": {
            "input": "consumed on Valid and Invalid",
            "valid_pointer": "input.data",
            "payload_copies": 0,
        },
        "text_to_bytes": {
            "pointer": "text.data",
            "payload_copies": 0,
        },
        "text_view": {
            "ownership": "borrowed",
            "payload_copies": 0,
            "slice_boundaries": "UTF-8 code-point boundaries only",
        },
    }
    return {
        **payload,
        "sha256": hashlib.sha256(
            repr(sorted(payload.items())).encode()
        ).hexdigest(),
    }


__all__ = [
    "TEXT_ABI_CONTRACT",
    "TEXT_ABI_VERSION",
    "TEXT_HIR_CONTRACT",
    "TEXT_HIR_SCHEMA_VERSION",
    "TEXT_MIR_CONTRACT",
    "TEXT_MIR_SCHEMA_VERSION",
    "text_abi_manifest",
    "text_hir_manifest",
    "text_mir_manifest",
    "validate_text_mir",
]
