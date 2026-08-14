"""Versioned ownership metadata for direct Bytes call boundaries."""

from __future__ import annotations

from typing import Any

from research.archive.alpha1.merlo.native_hir import NativeHIRProgram
from merlo.performance_mir import PerformanceMIR


BYTES_CALL_HIR_SCHEMA_VERSION = 1
BYTES_CALL_MIR_SCHEMA_VERSION = 1
BYTES_CALL_ABI_VERSION = 1
BYTES_CALL_HIR_CONTRACT = "meldra.bytes-call-hir.v1"
BYTES_CALL_MIR_CONTRACT = "meldra.bytes-call-mir.v1"
BYTES_CALL_ABI_CONTRACT = "meldra.bytes-call-abi.v1"


def _source(source: Any) -> dict[str, Any] | None:
    return source.to_dict() if source is not None else None


def bytes_call_hir_manifest(program: NativeHIRProgram) -> dict[str, Any]:
    """Project Bytes parameter ownership onto stable HIR identities."""

    if program.performance_mir is None:
        raise ValueError("Bytes call manifest requires Performance MIR provenance")
    symbols = {item.name: item for item in program.symbols}
    parameters = []
    functions = []
    for function in program.performance_mir.functions:
        function_symbol = symbols[function.name]
        parameter_items = []
        for parameter in function.parameters:
            if parameter.type.name not in {"Bytes", "BytesView"}:
                continue
            symbol = symbols[f"{function.name}.{parameter.name}"]
            mode = "owned" if parameter.type.name == "Bytes" else "borrowed"
            parameter_items.append(
                {
                    "name": parameter.name,
                    "type": parameter.type.name,
                    "ownership_mode": mode,
                    "borrow_origin": "caller" if mode == "borrowed" else None,
                    "transfer": "caller_to_callee" if mode == "owned" else None,
                    "symbol_id": symbol.symbol_id,
                    "revision_id": symbol.revision_id,
                    "source": symbol.source.to_dict(),
                }
            )
        return_mode = "owned_transfer" if function.return_type.name == "Bytes" else "value"
        if parameter_items or return_mode == "owned_transfer":
            functions.append(
                {
                    "name": function.name,
                    "symbol_id": function_symbol.symbol_id,
                    "revision_id": function_symbol.revision_id,
                    "source": function_symbol.source.to_dict(),
                    "parameters": parameter_items,
                    "return_type": function.return_type.name,
                    "return_ownership": return_mode,
                }
            )
        parameters.extend(parameter_items)
    return {
        "schema_version": BYTES_CALL_HIR_SCHEMA_VERSION,
        "contract": BYTES_CALL_HIR_CONTRACT,
        "source_sha256": program.cst.source_sha256,
        "lifetime_annotations_in_surface": 0,
        "scope": {
            "calls": "direct_synchronous",
            "borrow_lifetime": "call_only",
            "nested_borrowed_calls": "unsupported_declared",
            "async": "unsupported",
            "dynamic_dispatch": "unsupported",
        },
        "functions": functions,
        "parameters": parameters,
    }


def bytes_call_mir_manifest(mir: PerformanceMIR) -> dict[str, Any]:
    """Expose ownership events without changing the frozen base MIR schema."""

    calls = []
    borrow_ends = []
    drops = []
    for function in mir.functions:
        for block in function.blocks:
            for instruction in block.instructions:
                attributes = instruction.attribute_map
                if instruction.op == "call" and (
                    "borrow" in attributes.get("argument_ownership", ())
                    or "move" in attributes.get("argument_ownership", ())
                    or attributes.get("return_ownership") == "owned"
                ):
                    calls.append(
                        {
                            "caller": function.name,
                            "callee": attributes["callee"],
                            "instruction_id": instruction.id,
                            "source": _source(instruction.source),
                            "arguments": list(attributes["argument_ownership"]),
                            "direct": attributes.get("direct") is True,
                            "return_ownership": attributes.get("return_ownership", "value"),
                        }
                    )
                elif instruction.op == "borrow_end":
                    borrow_ends.append(
                        {
                            "caller": function.name,
                            "callee": attributes["callee"],
                            "instruction_id": instruction.id,
                            "source": _source(instruction.source),
                            "scope": (
                                attributes.get("scope")
                                or (
                                    "synchronous_call"
                                    if attributes.get("call_scope")
                                    == "direct_synchronous"
                                    else attributes.get("call_scope")
                                )
                            ),
                        }
                    )
                elif instruction.op == "drop" and attributes.get("owner_type") == "Bytes":
                    drops.append(
                        {
                            "function": function.name,
                            "instruction_id": instruction.id,
                            "source": _source(instruction.source),
                            "automatic": attributes.get("automatic") is True,
                            "explicit": attributes.get("explicit") is True,
                        }
                    )
    return {
        "schema_version": BYTES_CALL_MIR_SCHEMA_VERSION,
        "contract": BYTES_CALL_MIR_CONTRACT,
        "base_performance_mir_schema_version": mir.schema_version,
        "source_sha256": mir.source_sha256,
        "operations": {
            "move_argument": "move",
            "borrow_argument": "borrow",
            "direct_call": "call.direct=true",
            "owned_return_transfer": "call.return_ownership=owned",
            "borrow_end": "borrow_end.scope=synchronous_call",
            "compiler_inserted_drop": "drop.automatic=true",
        },
        "calls": calls,
        "borrow_ends": borrow_ends,
        "drops": drops,
    }


def bytes_call_abi_manifest() -> dict[str, Any]:
    return {
        "schema_version": BYTES_CALL_ABI_VERSION,
        "contract": BYTES_CALL_ABI_CONTRACT,
        "borrowed": {
            "c_type": "meldra_bytes_view",
            "fields": ["const uint8_t *data", "uint64_t length"],
            "ownership": "non_owning",
        },
        "owned": {
            "c_type": "meldra_bytes",
            "fields": [
                "uint8_t *data",
                "uint64_t length",
                "uint64_t capacity",
                "bool live",
            ],
            "ownership": "unique_transfer",
        },
        "reference_counting": False,
    }


__all__ = [
    "BYTES_CALL_ABI_CONTRACT",
    "BYTES_CALL_ABI_VERSION",
    "BYTES_CALL_HIR_CONTRACT",
    "BYTES_CALL_HIR_SCHEMA_VERSION",
    "BYTES_CALL_MIR_CONTRACT",
    "BYTES_CALL_MIR_SCHEMA_VERSION",
    "bytes_call_abi_manifest",
    "bytes_call_hir_manifest",
    "bytes_call_mir_manifest",
]
