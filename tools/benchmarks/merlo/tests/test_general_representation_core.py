from __future__ import annotations

import json
import subprocess

from pathlib import Path

import pytest

from tools.benchmarks.merlo.general_json_oracle import evaluate_python_oracle
from tools.benchmarks.merlo.general_representation_corpus import (
    invalid_json_cases,
    layout_sources,
    valid_json_cases,
)
from tools.benchmarks.merlo.general_representation_falsification import run_falsification_controls
from tools.benchmarks.merlo.general_representation_milestone import (
    SUPPORTED,
    validate_general_representation_report,
)
from merlo.representation_c_backend import emit_general_c
from merlo.native_c_backend import compile_c_source
from merlo.representation_ir import (
    BoxDesc,
    EnumDesc,
    RecordDesc,
    ScalarDesc,
    VecDesc,
    lower_structured_hir_to_rir,
    validate_recursive_layouts,
)
from merlo.representation_mir import (
    evaluate_general_mir,
    evaluate_representation_ir,
    lower_rir_to_performance_mir,
    optimize_general_mir,
)
from merlo.representation_runtime import (
    evaluate_structured_hir,
    exercise_vec_box_runtime,
)
from merlo.structured_hir_v2 import (
    compile_structured_hir,
    compile_structured_hir_file,
)


SOURCE_PATH = Path("tools/benchmarks/merlo/programs/general_json.mlo")
FORBIDDEN_DOMAIN_OPS = {
    "json_parse",
    "json_tokenize",
    "json_token_checksum",
    "json_decode",
    "json_build_ast",
}


@pytest.fixture(scope="module")
def layers():
    hir = compile_structured_hir_file(SOURCE_PATH)
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    optimized = optimize_general_mir(mir)
    return hir, representation, mir, optimized


def test_structured_hir_is_typed_structural_and_source_mapped(layers):
    hir, _representation, _mir, _optimized = layers
    assert hir.contract == "merlo.structured-typed-hir.v9"
    assert hir.path.endswith("general_json.mlo")
    assert {item.name for item in hir.types} == {
        "ErrorKind",
        "Json",
        "JsonField",
        "Parser",
        "ProgramResult",
        "Stats",
        "Token",
        "TokenKind",
    }
    assert hir.entry_function == "main"

    nodes = [node for function in hir.functions for node in function.walk()]
    kinds = {node.kind for node in nodes}
    assert {
        "LetBinding",
        "VarBinding",
        "If",
        "While",
        "Match",
        "DirectCall",
        "RecordConstruct",
        "FieldAccess",
        "SetField",
        "EnumConstruct",
        "EnumTag",
        "VecOperation",
        "BoxOperation",
        "BytesTextOperation",
        "Return",
        "TypedError",
    } <= kinds
    assert not kinds & {"BasicBlock", "Goto", "Malloc", "Free", "DropFlag", "RawPointer"}
    assert all(node.source.path.endswith("general_json.mlo") for node in nodes)
    assert all(node.scope_id and node.revision_id for node in nodes)
    assert all(parameter.ownership for function in hir.functions for parameter in function.parameters)


def test_representation_ir_describes_layout_ownership_and_drop(layers):
    hir, representation, _mir, _optimized = layers
    assert representation.contract == "merlo.representation-ir.v5"
    assert representation.source_hir_digest == hir.digest
    assert isinstance(representation.descriptor("UInt64"), ScalarDesc)
    assert isinstance(representation.descriptor("JsonField"), RecordDesc)
    assert isinstance(representation.descriptor("Json"), EnumDesc)
    assert isinstance(representation.descriptor("Vec[Json]"), VecDesc)
    assert isinstance(representation.descriptor("Box[UInt64]"), BoxDesc)

    json_descriptor = representation.descriptor("Json")
    assert json_descriptor.size == 40
    assert json_descriptor.alignment == 8
    assert json_descriptor.copy_class == "forbidden"
    assert json_descriptor.move_class == "bitwise_then_invalidate"
    assert json_descriptor.drop_class == "tag_switch"
    assert json_descriptor.indirect_dependencies == ("Json", "JsonField")
    assert [item[0] for item in json_descriptor.variants] == [
        "Null",
        "Bool",
        "Number",
        "String",
        "Array",
        "Object",
    ]

    json_plan = next(item for item in representation.drop_plans if item.type_name == "Json")
    assert json_plan.action == "enum_active_payload"
    assert {(item.variant_name, item.type_name) for item in json_plan.children} == {
        ("Number", "Text"),
        ("String", "Text"),
        ("Array", "Vec[Json]"),
        ("Object", "Vec[JsonField]"),
    }
    operations = [item for function in representation.functions for item in function.walk()]
    assert operations
    assert not {item.op for item in operations} & FORBIDDEN_DOMAIN_OPS
    assert all(item.revision_id and item.source.path.endswith("general_json.mlo") for item in operations)
    assert all(item.ownership_provenance for item in operations)


def test_layout_validation_rejects_inline_cycles_and_allows_owning_indirection():
    invalid = compile_structured_hir(
        "record Bad:\n    next: Bad\nfn main() -> Unit:\n    return\n",
        path="invalid-inline.mlo",
    )
    rejected = validate_recursive_layouts(invalid.types)
    assert rejected.accepted is False
    assert rejected.minimal_cycle_path == ("Bad", "Bad")
    assert rejected.diagnostic == (
        "InlineRecursiveLayout: Bad --field[next]--> Bad; "
        "add Box or Vec indirection"
    )

    valid = compile_structured_hir(
        "enum Tree:\n    Leaf: UInt64\n    Branch: Vec[Tree]\n"
        "record Node:\n    next: Box[Node]\n"
        "fn main() -> Unit:\n    return\n",
        path="valid-indirection.mlo",
    )
    accepted = validate_recursive_layouts(valid.types)
    assert accepted.accepted is True
    assert dict(accepted.inline_graph) == {"Node": (), "Tree": ()}


def test_hir_rir_mir_and_optimized_mir_agree_with_python_oracle(layers):
    hir, representation, mir, optimized = layers
    payload = b'{"a":[1,true,false,null],"a":23,"text":"\\uD83D\\uDE00"}'
    oracle = evaluate_python_oracle(payload)
    results = [
        evaluate_structured_hir(hir, representation, payload),
        evaluate_representation_ir(hir, representation, payload),
        evaluate_general_mir(hir, representation, mir, payload),
        evaluate_general_mir(hir, representation, optimized, payload),
    ]
    assert oracle.ok is True
    expected = {
        "nodes": oracle.nodes,
        "arrays": oracle.arrays,
        "objects": oracle.objects,
        "fields": oracle.fields,
        "checksum": oracle.checksum,
    }
    for result in results:
        assert result.status == "OK"
        assert result.ownership_balanced is True
        assert {name: result.result[name] for name in expected} == expected
        assert result.metrics["ast_nodes_allocated"] == result.metrics["ast_nodes_freed"]
        assert result.metrics["vec_initialized"] == result.metrics["vec_elements_dropped"]


def test_invalid_input_returns_typed_offset_and_cleans_partial_ast(layers):
    hir, representation, _mir, _optimized = layers
    malformed = evaluate_structured_hir(hir, representation, b'{"a":[1,]}')
    invalid_utf8 = evaluate_structured_hir(hir, representation, b'"\x80"')

    assert malformed.status == "ERROR"
    assert malformed.result["error"] != 0
    assert malformed.result["error_offset"] == 8
    assert malformed.ownership_balanced is True
    assert malformed.metrics["ast_nodes_allocated"] == malformed.metrics["ast_nodes_freed"]
    assert invalid_utf8.status == "ERROR"
    assert invalid_utf8.result["error"] == 1
    assert invalid_utf8.result["error_offset"] == 1
    assert invalid_utf8.ownership_balanced is True


def test_mir_is_cfg_with_explicit_memory_and_keeps_drop_glue(layers):
    hir, representation, mir, optimized = layers
    assert mir.contract == "merlo.performance-mir.general-representation.v2"
    assert mir.source_hir_digest == hir.digest
    assert mir.representation_ir_digest == representation.digest
    assert optimized.optimized is True
    assert optimized.optimization_passes

    instructions = [
        item
        for function in mir.functions
        for block in function.blocks
        for item in block.instructions
    ]
    ops = {item.op for item in instructions}
    assert {
        "allocate",
        "bounds_check",
        "byte_load",
        "construct_enum",
        "construct_record",
        "move_value",
        "drop_value",
        "load_enum_tag",
        "load_field",
        "store_field",
        "call",
    } <= ops
    direct_calls = [item for item in instructions if item.op == "call"]
    assert direct_calls
    assert all(item.attribute_map.get("callee") for item in direct_calls)
    assert "primitive_call" in ops
    assert not ops & FORBIDDEN_DOMAIN_OPS
    assert any(len(function.blocks) > 1 for function in mir.functions)
    assert all(block.terminator.kind for function in mir.functions for block in function.blocks)
    assert sum(item.op == "drop_value" for item in instructions) == sum(
        item.op == "drop_value"
        for function in optimized.functions
        for block in function.blocks
        for item in block.instructions
    )


def test_runtime_vec_box_rules_and_c_intrinsic_boundary(layers):
    hir, representation, _mir, optimized = layers
    runtime = exercise_vec_box_runtime(representation)
    assert runtime["blocked_growth_with_view"] is True
    assert runtime["box_move_preserved"] is True
    assert runtime["balanced"] is True
    assert runtime["metrics"]["vec_initialized"] == runtime["metrics"]["vec_elements_dropped"]

    generated = emit_general_c(hir, representation, optimized)
    manifest = {item["name"]: item for item in generated.primitive_manifest}
    assert generated.domain_opaque_calls == ()
    assert "MERLO_METRICS" in generated.source
    assert "MELDRA" not in generated.source
    assert FORBIDDEN_DOMAIN_OPS.isdisjoint(generated.source)
    assert "socket(" not in generated.source
    assert "merlo_network_tcp_guard" not in generated.source
    assert "<sys/socket.h>" not in generated.source
    assert {
        "malloc",
        "realloc",
        "free",
        "memcpy",
        "memmove",
        "byte_load",
        "byte_store",
        "host_input",
        "host_output",
            "overflow_trap",
            "Vec.new",
            "Map.new",
            "Box.new",
            "Text.from_bytes",
        "TextBuilder.new",
        "TextBuilder.append",
        "TextBuilder.finish",
    } == set(manifest)
    assert all(item["type_signature"] for item in manifest.values())
    assert all(item["ownership_behavior"] for item in manifest.values())
    assert all(item["effect"] for item in manifest.values())
    assert all(item["complexity"] for item in manifest.values())
    assert all(item["handwritten_implementation_size_lines"] >= 0 for item in manifest.values())


def test_frozen_json_and_source_variant_share_explicit_wrapping_semantics(
    layers,
    tmp_path: Path,
):
    hir, representation, _mir, optimized = layers
    source = SOURCE_PATH.read_text(encoding="utf-8")
    variant_source = source + "\n# semantically equivalent source payload\n"
    variant_hir = compile_structured_hir(
        variant_source,
        path=str(SOURCE_PATH),
    )
    variant_representation = lower_structured_hir_to_rir(variant_hir)
    variant_mir = optimize_general_mir(
        lower_rir_to_performance_mir(
            variant_hir,
            variant_representation,
        )
    )
    generated = emit_general_c(hir, representation, optimized)
    variant_generated = emit_general_c(
        variant_hir,
        variant_representation,
        variant_mir,
    )
    payload = b'{"a":[1,true,false,null],"text":"merlo"}'
    oracle = evaluate_python_oracle(payload)
    base_result = evaluate_structured_hir(hir, representation, payload)
    variant_result = evaluate_structured_hir(
        variant_hir,
        variant_representation,
        payload,
    )
    wrapping = next(
        node
        for node in hir.function("checksum_byte").walk()
        if node.kind == "NumericIntrinsic"
    )

    assert hir.digest != variant_hir.digest
    assert wrapping.attribute_map["callee"] == "wrapping_mul"
    assert wrapping.attribute_map["overflow"] == "wrapping"
    assert generated.source == variant_generated.source
    assert base_result.result == variant_result.result
    assert base_result.result["checksum"] == oracle.checksum

    build = compile_c_source(
        generated.source,
        output_dir=tmp_path,
        stem="general-json-wrapping",
    )
    assert build.status == "MEASURED", build.stderr
    assert build.binary_path is not None
    completed = subprocess.run(
        [build.binary_path],
        input=payload,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert f"OK checksum={oracle.checksum} ".encode() in completed.stdout


def test_corpus_cardinalities_are_deterministic_and_independently_partitioned():
    valid = valid_json_cases()
    invalid = invalid_json_cases()
    valid_layouts, invalid_layouts = layout_sources()
    assert len(valid) == 600
    assert len(invalid) == 400
    assert len(valid_layouts) == 500
    assert len(invalid_layouts) == 300
    assert [item.case_id for item in valid] == [item.case_id for item in valid_json_cases()]
    assert [item.case_id for item in invalid] == [item.case_id for item in invalid_json_cases()]
    assert {item.partition for item in valid + invalid} == {
        "generated_internal",
        "held_out_internal",
    }
    assert len({item.family for item in valid}) == 20
    assert len({item.family for item in invalid}) == 20


def test_required_falsification_mutants_are_detected(layers):
    hir, representation, mir, optimized = layers
    parse_error = evaluate_structured_hir(hir, representation, b'{"a":[1,]}')
    generated = emit_general_c(hir, representation, optimized)
    result = run_falsification_controls(
        hir,
        representation,
        mir,
        optimized,
        generated.source,
        parse_error.metrics,
    )
    assert result["passed"] is True
    assert result["detected_count"] == result["required_count"]
    assert result["required_count"] >= 12


def test_final_milestone_artifact_is_self_consistent_and_supported():
    report = json.loads(
        Path("tools/benchmarks/merlo/benchmarks/merlo_general_representation_core.json").read_text(
            encoding="utf-8"
        )
    )
    validate_general_representation_report(report)
    assert report["status"] == SUPPORTED
    assert all(report["gates"].values())
    assert report["correction"]["component_statuses"][
        "JSON tokenizer as ordinary Merlo program"
    ] == "SUPPORTED"
