"""Versioned HIR/MIR contracts for the isolated Stage 0.6B Bytes experiment."""

from __future__ import annotations

from typing import Any

from research.archive.alpha1.merlo.native_hir import NativeHIRProgram
from .performance_mir import PerformanceMIR


BYTES_HIR_SCHEMA_VERSION = 1
BYTES_MIR_EXTENSION_SCHEMA_VERSION = 1
BYTES_REPRESENTATION_VERSION = 1
BYTES_HIR_CONTRACT = "meldra.bytes-hir.v1"
BYTES_MIR_CONTRACT = "meldra.bytes-mir-extension.v1"
BYTES_MIR_OPERATIONS = (
    "bytes_new",
    "bytes_len",
    "bytes_bounds_check",
    "bytes_load",
    "bytes_store",
    "bytes_slice",
    "move",
    "drop",
)


def bytes_hir_manifest(program: NativeHIRProgram) -> dict[str, Any]:
    typed_nodes = tuple(
        sorted(
            node.id
            for node in program.nodes
            if node.type_name in {"Bytes", "BytesView"}
        )
    )
    return {
        "schema_version": BYTES_HIR_SCHEMA_VERSION,
        "contract": BYTES_HIR_CONTRACT,
        "source_sha256": program.cst.source_sha256,
        "owned_type": {
            "name": "Bytes",
            "ownership": "unique",
            "fields": ["pointer", "length", "capacity", "live"],
        },
        "borrowed_type": {
            "name": "BytesView",
            "ownership": "non_escaping_shared_borrow",
            "fields": ["pointer", "length"],
        },
        "typed_node_ids": list(typed_nodes),
    }


def bytes_mir_manifest(mir: PerformanceMIR) -> dict[str, Any]:
    operations = [
        instruction.op
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
        if instruction.op in BYTES_MIR_OPERATIONS
        and (
            instruction.op.startswith("bytes_")
            or instruction.type is not None
            and instruction.type.name in {"Bytes", "BytesView"}
            or instruction.operands
            and instruction.op == "drop"
        )
    ]
    return {
        "schema_version": BYTES_MIR_EXTENSION_SCHEMA_VERSION,
        "contract": BYTES_MIR_CONTRACT,
        "base_performance_mir_schema_version": mir.schema_version,
        "source_sha256": mir.source_sha256,
        "owner_layout": {
            "pointer": "uint8*",
            "length": "uint64",
            "capacity": "uint64",
            "ownership_state": "unique_live_bit",
        },
        "view_layout": {"pointer": "const uint8*", "length": "uint64"},
        "operations": operations,
        "automatic_drop": any(
            instruction.op == "drop"
            and instruction.attribute_map.get("automatic") is True
            for function in mir.functions
            for block in function.blocks
            for instruction in block.instructions
        ),
    }


__all__ = [
    "BYTES_HIR_CONTRACT",
    "BYTES_HIR_SCHEMA_VERSION",
    "BYTES_MIR_CONTRACT",
    "BYTES_MIR_EXTENSION_SCHEMA_VERSION",
    "BYTES_MIR_OPERATIONS",
    "BYTES_REPRESENTATION_VERSION",
    "bytes_hir_manifest",
    "bytes_mir_manifest",
]
