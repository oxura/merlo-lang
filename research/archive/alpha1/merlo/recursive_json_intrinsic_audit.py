"""Machine-readable boundary audit for the Recursive JSON Core milestone."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .json_streaming_c import json_streaming_c_source
from .native_c_backend import CEmitter
from research.archive.alpha1.merlo.native_hir import compile_native_hir, lower_native_hir_to_performance
from .performance_frontend import compile_performance_source
from .performance_opt import optimize_mir


AUDIT_SCHEMA_VERSION = 1
AUDIT_STATUS = "OPAQUE_INTRINSIC_BLOCKER"
TOKENIZER_SOURCE = """fn main(data: BytesView) -> UInt64:
    return json_token_checksum(data)
"""
ORDINARY_CONTROL_FLOW_PROBE = """fn is_ws(byte: UInt64) -> Bool:
    return byte == 32 or byte == 9 or byte == 10 or byte == 13
fn main(data: BytesView) -> UInt64:
    var index: UInt64 = 0
    var count: UInt64 = 0
    while index < data.len():
        if is_ws(data[index]):
            count = count + 1
        index = index + 1
    return count
"""
_REPRESENTATION_PROBES = {
    "recursive_record": """record Node:
    child: Node
fn main() -> UInt64:
    return 0
""",
    "nested_record": """record Leaf:
    value: UInt64
record Root:
    leaf: Leaf
fn main() -> UInt64:
    return 0
""",
    "generic_box": """record Root:
    child: Box[Root]
fn main() -> UInt64:
    return 0
""",
    "enum_declaration": """enum JsonValue:
    Null
fn main() -> UInt64:
    return 0
""",
    "recursive_borrowed_call": """fn scan(data: BytesView, index: UInt64) -> UInt64:
    if index == data.len():
        return index
    return scan(data, index + 1)
fn main(data: BytesView) -> UInt64:
    return scan(data, 0)
""",
}
_COMPONENTS = (
    (
        "tokenizer_entry",
        "surface json_token_checksum call -> MIR json_token_checksum -> meldra_json_token_checksum",
        True,
        True,
        True,
    ),
    (
        "json_state_machine",
        "meldra/json_streaming_c.py:meldra_json_token_checksum",
        False,
        False,
        False,
    ),
    (
        "whitespace_handling",
        "meldra/json_streaming_c.py:meldra_json_ws",
        False,
        False,
        False,
    ),
    (
        "delimiter_handling",
        "meldra/json_streaming_c.py:meldra_json_delimiter and meldra_json_token_checksum",
        False,
        False,
        False,
    ),
    (
        "literal_recognition",
        "meldra/json_streaming_c.py:meldra_json_parse_literal",
        False,
        False,
        False,
    ),
    (
        "number_grammar",
        "meldra/json_streaming_c.py:meldra_json_parse_number",
        False,
        False,
        False,
    ),
    (
        "string_scanning",
        "meldra/json_streaming_c.py:meldra_json_parse_string",
        False,
        False,
        False,
    ),
    (
        "escape_handling",
        "meldra/json_streaming_c.py:meldra_json_parse_string and meldra_json_scratch_scalar",
        False,
        False,
        False,
    ),
    (
        "diagnostic_control_flow",
        "meldra/json_streaming_c.py:meldra_panic_json and parser branches",
        False,
        False,
        False,
    ),
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str | None:
    return _sha256_bytes(path.read_bytes()) if path.exists() else None


def _artifact_hash(report: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in report.items()
        if key != "artifact_payload_sha256"
    }
    return _sha256_bytes(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _representation_probes() -> dict[str, Any]:
    results = {}
    for name, source in _REPRESENTATION_PROBES.items():
        try:
            compile_performance_source(source, path=f"audit/{name}.meldra")
        except Exception as exc:  # The exact compiler diagnostic is audit evidence.
            results[name] = {
                "accepted": False,
                "diagnostic_type": type(exc).__name__,
                "diagnostic": str(exc),
            }
        else:
            results[name] = {"accepted": True, "diagnostic": None}
    return results


def run_recursive_json_intrinsic_audit(
    destination: str | Path = "tools/benchmarks/merlo/benchmarks/meldra_recursive_json_intrinsic_audit.json",
) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parent.parent
    hir = compile_native_hir(
        TOKENIZER_SOURCE, path="audit/current-tokenizer.meldra"
    )
    mir = lower_native_hir_to_performance(hir)
    optimized, passes = optimize_mir(mir)
    generated_c = CEmitter(optimized, runtime_arguments=True).emit()
    ordinary = compile_performance_source(
        ORDINARY_CONTROL_FLOW_PROBE,
        path="audit/ordinary-control-flow.meldra",
    ).mir
    mir_instructions = [
        {
            "function": function.name,
            "block": block.id,
            "instruction_id": instruction.id,
            "op": instruction.op,
            "attributes": instruction.attribute_map,
            "source": (
                instruction.source.to_dict()
                if instruction.source is not None
                else None
            ),
        }
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
    ]
    ordinary_ops = sorted(
        {
            instruction.op
            for function in ordinary.functions
            for block in function.blocks
            for instruction in block.instructions
        }
    )
    c_runtime = json_streaming_c_source()
    component_matrix = [
        {
            "component": component,
            "implementation_location": location,
            "visible_in_surface": surface,
            "visible_in_hir": hir_visible,
            "visible_in_mir": mir_visible,
            "opaque_runtime_call": True,
            "handwritten_c_logic": True,
        }
        for component, location, surface, hir_visible, mir_visible in _COMPONENTS
    ]
    probes = _representation_probes()
    text_streaming_artifact = repo_root / "tools/benchmarks/merlo/benchmarks/meldra_text_streaming_core.json"
    report: dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "kind": "MeldraRecursiveJsonIntrinsicBoundaryAudit",
        "status": AUDIT_STATUS,
        "scope": {
            "checkpoint": "Intrinsic Boundary Audit",
            "later_milestone_phases_executed": False,
            "early_stop_rule": (
                "Current tokenizer is a monolithic opaque C/MIR operation and "
                "an owning recursive AST cannot be represented by the current "
                "frontend/HIR/MIR without a new representation architecture."
            ),
        },
        "tokenizer_path": {
            "surface_source": TOKENIZER_SOURCE,
            "surface_observation": (
                "The source exposes one call name and no JSON control flow."
            ),
            "hir": {
                "contract": "Native Typed HIR v1",
                "source_kind": hir.source_kind,
                "digest": hir.digest,
                "node_kinds": [item.kind for item in hir.nodes],
                "json_call_nodes": [
                    item.to_dict()
                    for item in hir.nodes
                    if item.kind == "Name"
                    and item.name == "json_token_checksum"
                ],
                "semantic_control_flow_nodes": 0,
                "observation": (
                    "HIR is an AST/symbol projection plus a stored PerformanceMIR "
                    "adapter; it contains the call but not tokenizer internals."
                ),
            },
            "mir": {
                "digest": mir.digest,
                "optimized_digest": optimized.digest,
                "instruction_count": mir.instruction_count,
                "instructions": mir_instructions,
                "json_semantic_basic_blocks": 0,
                "opaque_json_instruction_count": sum(
                    item["op"] == "json_token_checksum"
                    for item in mir_instructions
                ),
                "optimizer_preserved_opaque_instruction": any(
                    instruction.op == "json_token_checksum"
                    for function in optimized.functions
                    for block in function.blocks
                    for instruction in block.instructions
                ),
                "optimizer_passes": [item.name for item in passes],
            },
            "native_backend": {
                "emitted_helper_definition_count": generated_c.count(
                    "static uint64_t meldra_json_token_checksum("
                ),
                "emitted_helper_reference_count": generated_c.count(
                    "meldra_json_token_checksum("
                ),
                "handwritten_runtime_source_lines": len(c_runtime.splitlines()),
                "handwritten_runtime_sha256": _sha256_bytes(c_runtime.encode()),
                "implementation": "meldra/json_streaming_c.py",
            },
            "surface_and_hir_evaluator": {
                "implementation": "meldra/native_differential.py:_json_token_checksum",
                "behavior": (
                    "Copies the view into Python bytes and calls the independent "
                    "Python tokenize_json implementation; it does not execute HIR JSON flow."
                ),
            },
            "components": component_matrix,
        },
        "allowed_runtime_primitives": [
            "malloc",
            "realloc",
            "free",
            "memcpy",
            "memmove",
            "byte_load",
            "byte_store",
            "host_file_read",
            "host_file_write",
            "proven_Bytes_Text_Builder_primitives",
        ],
        "forbidden_runtime_semantics_observed": [
            "complete_JSON_state_machine",
            "whitespace_grammar",
            "delimiter_grammar",
            "literal_grammar",
            "number_grammar",
            "string_and_escape_grammar",
            "JSON_diagnostic_branching",
        ],
        "ordinary_control_flow_probe": {
            "accepted": True,
            "source": ORDINARY_CONTROL_FLOW_PROBE,
            "mir_digest": ordinary.digest,
            "mir_operations": ordinary_ops,
            "interpretation": (
                "Byte scanning, loops, branches and direct helper calls can be "
                "expressed in ordinary MIR. The current tokenizer has not been "
                "lowered that way."
            ),
        },
        "recursive_representation_probes": probes,
        "architecture_blockers": [
            {
                "capability": "generic_or_monomorphized_Box",
                "evidence": probes["generic_box"],
            },
            {
                "capability": "recursive_layout_through_indirection",
                "evidence": probes["recursive_record"],
            },
            {
                "capability": "nested_owning_records",
                "evidence": probes["nested_record"],
            },
            {
                "capability": "enum_declarations_and_payload_variants",
                "evidence": probes["enum_declaration"],
            },
            {
                "capability": "recursive_descent_over_borrowed_input",
                "evidence": probes["recursive_borrowed_call"],
            },
            {
                "capability": "recursive_drop_glue",
                "accepted": False,
                "evidence": (
                    "PerformanceMIR has no enum/box/vec type descriptors or "
                    "variant-directed drop representation."
                ),
            },
            {
                "capability": "independent_typed_HIR_lowering",
                "accepted": False,
                "evidence": (
                    "compile_native_hir labels source_kind stage05p_performance_adapter "
                    "and stores frontend.mir; lower_native_hir_to_performance returns it."
                ),
            },
        ],
        "decision": {
            "current_final_path_acceptable": False,
            "json_semantics_in_ordinary_surface": False,
            "json_semantics_in_hir": False,
            "json_semantics_in_mir_cfg": False,
            "handwritten_c_contains_json_semantics": True,
            "opaque_runtime_json_calls": 1,
            "byte_scanner_decomposition_possible_without_new_types": True,
            "owning_recursive_ast_possible_in_current_type_ir": False,
            "frontend_hir_mir_redesign_required": True,
            "selected_status": AUDIT_STATUS,
        },
        "required_architecture_before_retry": [
            "First-class monomorphized Vec and Box type descriptors in typed HIR/MIR.",
            "Enum payload layouts and exhaustive variant match lowering.",
            "Recursive layout graph with explicit indirection validation.",
            "Type-directed recursive drop glue preserved by optimization.",
            "Tokenizer state machine lowered from ordinary Meldra functions into CFG.",
            "A real HIR-to-MIR lowering boundary rather than a stored PerformanceMIR adapter.",
        ],
        "skipped_by_early_stop": {
            "vec_box_implementation": True,
            "recursive_enum_implementation": True,
            "ast_parser": True,
            "query_and_pretty": True,
            "json_tool": True,
            "integrated_corpus": True,
            "sanitizers": True,
            "soak": True,
            "benchmark": True,
            "full_suite": True,
        },
        "frozen_hashes": {
            "text_streaming_artifact_sha256": _sha256_file(
                text_streaming_artifact
            ),
            "performance_frontend_sha256": _sha256_file(
                repo_root / "tools/benchmarks/merlo/performance_frontend.py"
            ),
            "native_hir_sha256": _sha256_file(
                repo_root / "research/archive/alpha1/merlo/native_hir.py"
            ),
            "performance_mir_sha256": _sha256_file(
                repo_root / "src/merlo/performance_mir.py"
            ),
            "native_c_backend_sha256": _sha256_file(
                repo_root / "src/merlo/native_c_backend.py"
            ),
            "json_streaming_c_sha256": _sha256_file(
                repo_root / "src/merlo/json_streaming_c.py"
            ),
        },
        "limitations": [
            "This is a boundary audit, not a Recursive JSON implementation.",
            "No recursive corpus, sanitizer, soak, or performance claims are made.",
            "The ordinary control-flow probe proves only byte-loop expressibility, not recursive ownership support.",
        ],
        "next_action": (
            "Create a representation-IR milestone first: typed Vec/Box/enum "
            "descriptors, recursive layout validation, and generated drop glue; "
            "then port the tokenizer to ordinary Meldra CFG before retrying JSON AST."
        ),
    }
    report["artifact_payload_sha256"] = _artifact_hash(report)
    validate_recursive_json_intrinsic_audit(report)
    output = repo_root / destination
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def validate_recursive_json_intrinsic_audit(report: dict[str, Any]) -> None:
    if report.get("artifact_payload_sha256") != _artifact_hash(report):
        raise ValueError("recursive JSON intrinsic audit hash mismatch")
    if report.get("status") != AUDIT_STATUS:
        raise ValueError("recursive JSON intrinsic audit status drifted")
    decision = report.get("decision", {})
    required = {
        "current_final_path_acceptable": False,
        "json_semantics_in_ordinary_surface": False,
        "json_semantics_in_hir": False,
        "json_semantics_in_mir_cfg": False,
        "handwritten_c_contains_json_semantics": True,
        "opaque_runtime_json_calls": 1,
        "owning_recursive_ast_possible_in_current_type_ir": False,
        "frontend_hir_mir_redesign_required": True,
        "selected_status": AUDIT_STATUS,
    }
    for key, expected in required.items():
        if decision.get(key) != expected:
            raise ValueError(f"recursive JSON audit decision drifted: {key}")
    mir = report["tokenizer_path"]["mir"]
    if mir["instruction_count"] != 1 or mir["opaque_json_instruction_count"] != 1:
        raise ValueError("tokenizer is no longer represented by exactly one opaque MIR op")
    backend = report["tokenizer_path"]["native_backend"]
    if backend["emitted_helper_definition_count"] != 1:
        raise ValueError("generated C tokenizer helper boundary drifted")
    internals = [
        item
        for item in report["tokenizer_path"]["components"]
        if item["component"] != "tokenizer_entry"
    ]
    if not internals or any(
        item["visible_in_surface"]
        or item["visible_in_hir"]
        or item["visible_in_mir"]
        or not item["handwritten_c_logic"]
        for item in internals
    ):
        raise ValueError("tokenizer internals unexpectedly crossed the IR boundary")
    probes = report["recursive_representation_probes"]
    if any(item["accepted"] for item in probes.values()):
        raise ValueError("a recursive representation blocker probe unexpectedly passed")
    if not report["ordinary_control_flow_probe"]["accepted"]:
        raise ValueError("ordinary byte control-flow probe must remain expressible")


__all__ = [
    "AUDIT_STATUS",
    "ORDINARY_CONTROL_FLOW_PROBE",
    "TOKENIZER_SOURCE",
    "run_recursive_json_intrinsic_audit",
    "validate_recursive_json_intrinsic_audit",
]
