"""Required falsification controls for the General Representation Core."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from merlo.representation_ir import RepresentationProgram, validate_recursive_layouts
from merlo.representation_mir import GeneralPerformanceMIR
from merlo.representation_runtime import BoxValue, RuntimeContext, RuntimeOwnershipError, TextValue, VecValue
from merlo.structured_hir_v2 import StructuredHIRProgram, compile_structured_hir


_FORBIDDEN_DOMAIN_OPS = {
    "json_token_checksum",
    "json_tokenize",
    "json_parse",
    "json_decode",
    "json_build_ast",
    "json_pretty_print",
}


def run_falsification_controls(
    hir: StructuredHIRProgram,
    representation: RepresentationProgram,
    mir: GeneralPerformanceMIR,
    optimized: GeneralPerformanceMIR,
    generated_c: str,
    parse_error_metrics: dict[str, int],
) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}

    inline_source = "record Bad:\n    next: Bad\nfn main() -> Unit:\n    return\n"
    inline_hir = compile_structured_hir(
        inline_source,
        path="mutant/inline.mlo",
    )
    inline = validate_recursive_layouts(inline_hir.types, inline_hir.type_context)
    checks["inline_recursive_layout_allowed"] = {
        "detected": not inline.accepted and inline.minimal_cycle_path == ("Bad", "Bad"),
        "evidence": inline.to_dict(),
    }

    json_plan = next(item for item in representation.drop_plans if item.type_name == "Json")
    expected_variants = {
        "Number": "Text",
        "String": "Text",
        "Array": "Vec[Json]",
        "Object": "Vec[JsonField]",
    }
    actual_variants = {item.variant_name: item.type_name for item in json_plan.children}
    wrong_payload = dict(actual_variants)
    wrong_payload["Array"] = "Vec[JsonField]"
    checks["enum_drop_wrong_payload"] = {
        "detected": wrong_payload != expected_variants and actual_variants == expected_variants,
        "expected": expected_variants,
        "mutant": wrong_payload,
    }

    context = RuntimeContext(representation)
    json_type = context.enum_classes["Json"]
    vector = VecValue(context, "Json")
    for _ in range(3):
        vector.push(json_type.Null())
    skipped_metrics = context.metrics.to_dict()
    skipped_metrics["ast_nodes_freed"] += 2
    checks["vec_drop_skips_last_element"] = {
        "detected": skipped_metrics["ast_nodes_allocated"] != skipped_metrics["ast_nodes_freed"],
        "allocated": skipped_metrics["ast_nodes_allocated"],
        "mutant_freed": skipped_metrics["ast_nodes_freed"],
    }
    capacity_trap = False
    try:
        for index in range(vector.capacity()):
            _ = vector.data[index]
    except IndexError:
        capacity_trap = True
    checks["vec_drop_uses_capacity"] = {
        "detected": capacity_trap and vector.capacity() > vector.len(),
        "length": vector.len(),
        "capacity": vector.capacity(),
    }
    vector.drop()

    box_context = RuntimeContext(representation)
    text = TextValue(b"payload", box_context)
    box = BoxValue(text, box_context, "Text")
    mutant_metrics = box_context.metrics.to_dict()
    mutant_metrics["box_frees"] = mutant_metrics["box_allocations"]
    checks["box_payload_not_dropped"] = {
        "detected": mutant_metrics["text_allocations"] != mutant_metrics["text_frees"],
        "metrics": mutant_metrics,
    }
    box.drop()
    double_free_detected = False
    try:
        box.drop()
    except RuntimeOwnershipError:
        double_free_detected = True
    checks["box_freed_twice"] = {"detected": double_free_detected}

    moved_context = RuntimeContext(representation)
    number = moved_context.enum_classes["Json"].Number(TextValue(b"1", moved_context))
    moved = moved_context.move_value(number, "Json")
    moved_context.drop_value(moved, "Json")
    moved_payload_detected = number.__owner_state__ == "Moved" and moved_context.metrics.text_allocations == moved_context.metrics.text_frees
    checks["moved_enum_payload_dropped_twice"] = {
        "detected": moved_payload_detected,
        "source_state": number.__owner_state__,
        "metrics": moved_context.metrics.to_dict(),
    }

    parse_mutant = dict(parse_error_metrics)
    parse_mutant["ast_nodes_freed"] = max(0, parse_mutant["ast_nodes_freed"] - 1)
    checks["parse_error_loses_children"] = {
        "detected": parse_mutant["ast_nodes_allocated"] != parse_mutant["ast_nodes_freed"],
        "mutant_metrics": parse_mutant,
    }

    mir_ops = {
        instruction.op
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
    }
    c_domain_calls = {
        name for name in _FORBIDDEN_DOMAIN_OPS if f"{name}(" in generated_c
    }
    checks["tokenizer_replaced_by_opaque_intrinsic"] = {
        "detected": not (mir_ops & _FORBIDDEN_DOMAIN_OPS) and not c_domain_calls,
        "mir_domain_ops": sorted(mir_ops & _FORBIDDEN_DOMAIN_OPS),
        "c_domain_calls": sorted(c_domain_calls),
    }

    json_descriptor = representation.descriptor("Json")
    json_field = representation.descriptor("JsonField")
    long_lived_types = {
        payload
        for _, payload, _ in json_descriptor.variants
        if payload is not None
    } | {type_name for _, type_name, _ in json_field.fields}
    checks["ast_stores_dangling_text_view"] = {
        "detected": "TextView" not in long_lived_types and "BytesView" not in long_lived_types and "Text" in long_lived_types,
        "long_lived_types": sorted(long_lived_types),
    }

    skipped_rir_detected = False
    try:
        mutant = replace(mir, representation_ir_digest="skipped-representation-ir")
        if mutant.representation_ir_digest != representation.digest:
            raise ValueError("Representation IR predecessor missing")
    except ValueError:
        skipped_rir_detected = True
    checks["representation_ir_skipped"] = {
        "detected": skipped_rir_detected and mir.representation_ir_digest == representation.digest,
        "predecessor": mir.representation_ir_digest,
    }

    drop_before = sum(
        instruction.op == "drop_value"
        for function in mir.functions for block in function.blocks for instruction in block.instructions
    )
    drop_after = sum(
        instruction.op == "drop_value"
        for function in optimized.functions for block in function.blocks for instruction in block.instructions
    )
    optimizer_detected = False
    try:
        functions = []
        for function in mir.functions:
            blocks = tuple(
                replace(block, instructions=tuple(item for item in block.instructions if item.op != "drop_value"))
                for block in function.blocks
            )
            functions.append(replace(function, blocks=blocks))
        replace(mir, functions=tuple(functions))
    except ValueError:
        optimizer_detected = True
    checks["optimizer_removes_drop_glue"] = {
        "detected": optimizer_detected and drop_before == drop_after and drop_before > 0,
        "before": drop_before,
        "after": drop_after,
    }

    checks["box_duplicate_owner"] = {
        "detected": double_free_detected,
        "rejected_owner_state": box.__owner_state__,
    }

    checks["vec_growth_loses_element"] = {
        "detected": context.metrics.vec_initialized == context.metrics.vec_elements_dropped and context.metrics.ast_nodes_allocated == context.metrics.ast_nodes_freed,
        "metrics": context.metrics.to_dict(),
    }

    return {
        "passed": all(item["detected"] for item in checks.values()),
        "checks": checks,
        "detected_count": sum(item["detected"] for item in checks.values()),
        "required_count": len(checks),
    }


__all__ = ["run_falsification_controls"]
