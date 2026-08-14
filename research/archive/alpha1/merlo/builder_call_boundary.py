"""Versioned contracts for direct synchronous BytesBuilder ownership transfer."""

from __future__ import annotations

import hashlib
from typing import Any

from research.archive.alpha1.merlo.native_hir import NativeHIRProgram
from merlo.performance_mir import BYTES, BYTES_BUILDER, PerformanceMIR

BUILDER_CALL_HIR_SCHEMA_VERSION = 1
BUILDER_CALL_MIR_SCHEMA_VERSION = 1
BUILDER_CALL_ABI_VERSION = 1
BUILDER_CALL_HIR_CONTRACT = "meldra.bytes-builder-call-hir.v1"
BUILDER_CALL_MIR_CONTRACT = "meldra.bytes-builder-call-mir.v1"
BUILDER_CALL_ABI_CONTRACT = "meldra.bytes-builder-call-abi.v1"


def _source(value: Any) -> dict[str, Any] | None:
    return value.to_dict() if value is not None else None


def builder_call_hir_manifest(program: NativeHIRProgram) -> dict[str, Any]:
    """Project builder ownership transfers onto stable semantic identities."""

    mir = program.performance_mir
    if mir is None:
        raise ValueError("BytesBuilder call manifest requires MIR provenance")
    symbols = {item.name: item for item in program.symbols}
    functions = []
    for function in mir.functions:
        builder_parameters = []
        for parameter in function.parameters:
            if parameter.type != BYTES_BUILDER:
                continue
            symbol = symbols[f"{function.name}.{parameter.name}"]
            builder_parameters.append(
                {
                    "name": parameter.name,
                    "type": "BytesBuilder",
                    "ownership": "unique_owned_transfer",
                    "symbol_id": symbol.symbol_id,
                    "revision_id": symbol.revision_id,
                    "source": symbol.source.to_dict(),
                }
            )
        if not builder_parameters and function.return_type not in {
            BYTES_BUILDER,
            BYTES,
        }:
            continue
        symbol = symbols[function.name]
        functions.append(
            {
                "name": function.name,
                "symbol_id": symbol.symbol_id,
                "revision_id": symbol.revision_id,
                "source": symbol.source.to_dict(),
                "builder_parameters": builder_parameters,
                "return_type": function.return_type.name,
                "return_ownership": (
                    "builder_transfer"
                    if function.return_type == BYTES_BUILDER
                    else "finished_bytes_transfer"
                    if function.return_type == BYTES
                    and builder_parameters
                    else "owned_value"
                ),
                "required_parameter_outcome": (
                    "returned_finished_or_dropped_every_path"
                    if builder_parameters
                    else None
                ),
            }
        )
    return {
        "schema_version": BUILDER_CALL_HIR_SCHEMA_VERSION,
        "contract": BUILDER_CALL_HIR_CONTRACT,
        "source_sha256": mir.source_sha256,
        "call_scope": "direct_synchronous",
        "maximum_nested_call_depth": 2,
        "lifetime_annotations_in_surface": 0,
        "general_parameter_mode_system": False,
        "functions": functions,
        "source_mappings_present": all(
            item["source"] is not None
            and all(
                parameter["source"] is not None
                for parameter in item["builder_parameters"]
            )
            for item in functions
        ),
        "semantic_identities_present": all(
            item["symbol_id"]
            and item["revision_id"]
            and all(
                parameter["symbol_id"] and parameter["revision_id"]
                for parameter in item["builder_parameters"]
            )
            for item in functions
        ),
    }


def builder_call_mir_manifest(mir: PerformanceMIR) -> dict[str, Any]:
    """Expose direct builder call ownership without changing base MIR schema."""

    functions = {item.name: item for item in mir.functions}
    calls = []
    drops = []
    finishes = []
    for function in mir.functions:
        for block in function.blocks:
            for instruction in block.instructions:
                attributes = instruction.attribute_map
                if instruction.op == "call":
                    callee = functions[str(attributes["callee"])]
                    builder_indices = [
                        index
                        for index, parameter in enumerate(callee.parameters)
                        if parameter.type == BYTES_BUILDER
                    ]
                    if not builder_indices:
                        continue
                    ownership = tuple(
                        attributes.get("argument_ownership", ())
                    )
                    calls.append(
                        {
                            "caller": function.name,
                            "callee": callee.name,
                            "instruction_id": instruction.id,
                            "source": _source(instruction.source),
                            "builder_argument_indices": builder_indices,
                            "builder_argument_ownership": [
                                ownership[index] for index in builder_indices
                            ],
                            "direct": attributes.get("direct") is True,
                            "call_scope": attributes.get("call_scope"),
                            "return_type": callee.return_type.name,
                            "return_ownership": attributes.get(
                                "return_ownership"
                            ),
                        }
                    )
                elif instruction.op == "builder_drop":
                    drops.append(
                        {
                            "function": function.name,
                            "instruction_id": instruction.id,
                            "source": _source(instruction.source),
                            "automatic": attributes.get("automatic") is True,
                            "explicit": attributes.get("explicit") is True,
                        }
                    )
                elif instruction.op == "builder_finish_transfer":
                    finishes.append(
                        {
                            "function": function.name,
                            "instruction_id": instruction.id,
                            "source": _source(instruction.source),
                            "pointer_identity": attributes.get(
                                "pointer_identity"
                            ),
                            "payload_copies": attributes.get(
                                "payload_copies"
                            ),
                        }
                    )
    validation = {
        "calls_present": bool(calls),
        "direct_only": all(item["direct"] for item in calls),
        "synchronous_only": all(
            item["call_scope"] == "direct_synchronous" for item in calls
        ),
        "all_builder_arguments_moved": all(
            item["builder_argument_ownership"]
            and all(
                ownership == "move"
                for ownership in item["builder_argument_ownership"]
            )
            for item in calls
        ),
        "owned_returns_visible": all(
            item["return_ownership"] == "owned" for item in calls
        ),
        "finish_zero_copy_visible": all(
            item["pointer_identity"] == "preserved"
            and item["payload_copies"] == 0
            for item in finishes
        ),
        "source_mappings_complete": all(
            item["source"] is not None
            for item in (*calls, *drops, *finishes)
        ),
    }
    return {
        "schema_version": BUILDER_CALL_MIR_SCHEMA_VERSION,
        "contract": BUILDER_CALL_MIR_CONTRACT,
        "base_performance_mir_schema_version": mir.schema_version,
        "source_sha256": mir.source_sha256,
        "operations": {
            "caller_transfer": "move",
            "direct_call": "call.direct=true",
            "builder_return": "call.return_ownership=owned",
            "finish_in_callee": "builder_finish_transfer",
            "callee_drop": "builder_drop",
        },
        "calls": calls,
        "drops": drops,
        "finishes": finishes,
        "validation": validation,
    }


def builder_call_abi_manifest() -> dict[str, Any]:
    payload = {
        "schema_version": BUILDER_CALL_ABI_VERSION,
        "contract": BUILDER_CALL_ABI_CONTRACT,
        "parameter": "meldra_bytes_builder passed by value",
        "builder_return": {
            "descriptor": "same data pointer length capacity active_views state",
            "return_allocations": 0,
            "payload_copies": 0,
            "pointer_identity": "preserved",
        },
        "finish_return": {
            "descriptor": "meldra_bytes from builder descriptor",
            "finish_copies": 0,
            "pointer_identity": "preserved",
            "final_owner_count": 1,
        },
        "reference_counting": False,
        "surface_lifetime_syntax": False,
        "maximum_nested_call_depth": 2,
    }
    return {
        **payload,
        "sha256": hashlib.sha256(
            repr(sorted(payload.items())).encode()
        ).hexdigest(),
    }


__all__ = [
    "BUILDER_CALL_ABI_CONTRACT",
    "BUILDER_CALL_ABI_VERSION",
    "BUILDER_CALL_HIR_CONTRACT",
    "BUILDER_CALL_HIR_SCHEMA_VERSION",
    "BUILDER_CALL_MIR_CONTRACT",
    "BUILDER_CALL_MIR_SCHEMA_VERSION",
    "builder_call_abi_manifest",
    "builder_call_hir_manifest",
    "builder_call_mir_manifest",
]
