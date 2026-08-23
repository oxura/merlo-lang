from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from merlo.intrinsics import (
    BUILTIN_FUNCTIONS,
    BUILTIN_FUNCTION_SIGNATURES,
    BUILTIN_RECEIVERS,
    CONTRACT_GRAPH,
    INTRINSIC_EFFECTS,
    INTRINSIC_SIGNATURES,
    INSTANCE_METHOD_SIGNATURES,
    BuiltinContractGraph,
    InstanceMethodSignature,
    TypeConstructorId,
    TypeSchemeApplied,
    TypeSchemeConcrete,
    contextual_result_type,
    intrinsic_signature,
)
from merlo.type_arena import (
    TypeArenaError,
    TypeContextBuilder,
    TypeId,
    UnknownTypeIdError,
)
from merlo.structured_hir_v2 import (
    StructuredHIRCompileError,
    compile_structured_hir,
)
from merlo.compiler import compile_project
from merlo.project import Project


EXPECTED = {
    "console.read": ((), "Bytes", "console.read"),
    "console.read_line": ((), "Text", "console.read"),
    "console.read_all": ((), "Text", "console.read"),
    "console.write": (("TextView",), "Unit", "console.write"),
    "fs.open_read": (("Path",), "Result[FileReader,FileError]", "fs.read"),
    "fs.read": (("Path",), "Result[Bytes,FileError]", "fs.read"),
    "fs.read_text": (("Path",), "Result[Text,FileError]", "fs.read"),
    "fs.read_chunk": (("FileReader", "UInt64"), "Result[Bytes,FileError]", "fs.read"),
    "fs.open_write": (("Path",), "Result[FileWriter,FileError]", "fs.write"),
    "fs.write": (("Path", "BytesView"), "Result[Unit,FileError]", "fs.write"),
    "fs.write_text": (("Path", "TextView"), "Result[Unit,FileError]", "fs.write"),
    "fs.write_chunk": (("FileWriter", "BytesView"), "Result[Unit,FileError]", "fs.write"),
    "fs.close_read": (("FileReader",), "Result[Unit,FileError]", "fs.read"),
    "fs.close_write": (("FileWriter",), "Result[Unit,FileError]", "fs.write"),
    "env.read": (("Text",), "Text", "env.read"),
    "env.get": (("Text",), "Text", "env.read"),
    "clock.now": ((), "UInt64", "clock.now"),
    "random.read": (("UInt64",), "Bytes", "random.read"),
    "process.args": ((), "UInt64", "process.args"),
    "process.arg": (("UInt64",), "Text", "process.args"),
    "network.tcp_connect": (("Text", "UInt64"), "Result[UInt64,AppError]", "network.tcp"),
    "network.tcp_send": (("UInt64", "BytesView"), "Result[UInt64,AppError]", "network.tcp"),
    "network.tcp_receive": (("UInt64", "UInt64"), "Result[Bytes,AppError]", "network.tcp"),
    "network.tcp_close": (("UInt64",), "Result[Unit,AppError]", "network.tcp"),
    "network.http_request": (("Text",), "Result[Bytes,AppError]", "network.http"),
}


def test_every_canonical_entry_has_exact_contract() -> None:
    assert set(INTRINSIC_SIGNATURES) == set(EXPECTED)
    for name, (parameters, result, effect) in EXPECTED.items():
        signature = intrinsic_signature(name)
        assert signature is not None
        assert signature.parameters == parameters
        assert signature.result_type == result
        assert signature.effect == signature.capability == effect
        assert signature.arity == len(parameters)


def test_table_entries_are_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        intrinsic_signature("clock.now").result_type = "Text"  # type: ignore[misc]
    with pytest.raises(TypeError):
        INTRINSIC_SIGNATURES["clock.now"] = intrinsic_signature("clock.now")  # type: ignore[index]



def test_custom_contract_graph_mappings_are_immutable() -> None:
    graph = BuiltinContractGraph(
        {},
        {
            ("Vec[T]", "get"): InstanceMethodSignature(
                "Vec[T]",
                "get",
                (),
                "T",
                generic_variables=frozenset({"T"}),
            )
        },
        {},
        {},
    )
    with pytest.raises(TypeError):
        graph.methods[("Vec[T]", "get")] = graph.methods[("Vec[T]", "get")]


def test_contract_graph_unification_is_transactional_and_order_invariant() -> None:
    repeated = BuiltinContractGraph(
        {},
        {
            ("Result[T,T]", "same"): InstanceMethodSignature(
                "Result[T,T]",
                "same",
                (),
                "Text",
                generic_variables=frozenset({"T"}),
            )
        },
        {},
        {},
    )
    repeated_builder = TypeContextBuilder()
    repeated_actual = repeated_builder.intern_text("Result[Text,Int64]")
    assert repeated.prepare(repeated_builder).method(repeated_actual, "same") is None

    rows = [
        (
            ("Result[T,T]", "pick"),
            InstanceMethodSignature(
                "Result[T,T]",
                "pick",
                (),
                "Text",
                static=True,
                generic_variables=frozenset({"T"}),
            ),
        ),
        (
            ("Result[Text,T]", "pick"),
            InstanceMethodSignature(
                "Result[Text,T]",
                "pick",
                (),
                "Bytes",
                static=True,
                generic_variables=frozenset({"T"}),
            ),
        ),
    ]
    results = []
    for ordered_rows in (rows, list(reversed(rows))):
        builder = TypeContextBuilder()
        actual = builder.intern_text("Result[Text,Int64]")
        bound = BuiltinContractGraph({}, dict(ordered_rows), {}, {}).prepare(builder)
        resolved = bound.resolve_static_method(actual, "pick", ())
        assert resolved is not None
        results.append((resolved.result_type, resolved.result_type_id))
    assert results[0] == results[1] == (
        "Bytes",
        TypeContextBuilder().intern_text("Bytes"),
    )

def test_bound_contract_graph_matches_nested_type_ids_and_aliases() -> None:
    builder = TypeContextBuilder()
    nested = builder.intern_text("Vec[Option[Text]]")
    alias = builder.intern_text("Vec[UInt]")
    int64 = builder.intern_text("Int64")
    bound = CONTRACT_GRAPH.prepare(builder)

    nested_get = bound.method(nested, "get")
    assert nested_get is not None
    assert nested_get.result_type_id == builder.type_id("Option[Text]")
    assert nested_get.result_type == "Option[Text]"
    assert nested_get.result_ownership == "borrow"
    assert nested_get.effects == ("bounds_check",)

    alias_get = bound.method(alias, "get")
    assert alias_get is not None
    assert alias_get.result_type_id == builder.type_id("UInt64")
    assert alias_get.result_type == "UInt64"

    contextual_len = bound.method(
        nested,
        "len",
        int64,
    )
    assert contextual_len is not None
    assert contextual_len.result_type_id == int64
    assert contextual_len.result_type == "Int64"
    assert CONTRACT_GRAPH.bind(builder).method(nested, "get") == nested_get


def test_bound_contract_graph_matches_const_arguments_structurally() -> None:
    graph = BuiltinContractGraph(
        {},
        {
            ("Array[T,4]", "len"): InstanceMethodSignature(
                "Array[T,4]",
                "len",
                (),
                "UInt64",
                generic_variables=frozenset({"T"}),
            )
        },
        {},
        {},
    )
    builder = TypeContextBuilder()
    expected = builder.intern_text("Array[Text,4]")
    wrong_length = builder.intern_text("Array[Text,5]")
    bound = graph.prepare(builder)

    signature = bound.method(expected, "len")
    assert signature is not None
    assert signature.result_type_id == builder.type_id("UInt64")
    assert bound.method(wrong_length, "len") is None


def test_bound_static_contract_instantiates_validated_type_ids() -> None:
    builder = TypeContextBuilder()
    text = builder.intern_text("Text")
    expected_map = builder.intern_text("Map[Text,Byte]")
    bound = CONTRACT_GRAPH.prepare(builder)

    box = bound.resolve_static_method(TypeConstructorId("Box"), "new", (text,))
    assert box is not None
    assert box.parameter_type_ids == (text,)
    assert box.result_type_id == builder.type_id("Box[Text]")
    assert box.parameter_ownership == ("consuming",)
    with pytest.raises(ValueError, match="argument type mismatch"):
        bound.resolve_static_method(TypeConstructorId("Box"), "new", (None,))

    mapping = bound.resolve_static_method(
        TypeConstructorId("Map"),
        "new",
        (),
        expected_map,
    )
    assert mapping is not None
    assert mapping.result_type_id == expected_map
    assert mapping.result_type == "Map[Text,Byte]"


def test_bound_contract_graph_rejects_unknown_and_malformed_identities() -> None:
    builder = TypeContextBuilder()
    builder.intern_text("Vec[Text]")
    bound = CONTRACT_GRAPH.prepare(builder)
    with pytest.raises(UnknownTypeIdError):
        bound.method(TypeId("f" * 64), "get")

    malformed = BuiltinContractGraph(
        {},
        {
            ("Map[T]", "get"): InstanceMethodSignature(
                "Map[T]",
                "get",
                (),
                "T",
            )
        },
        {},
        {},
    )
    with pytest.raises(TypeArenaError):
        malformed.prepare(TypeContextBuilder())
    with pytest.raises(ValueError):
        TypeSchemeConcrete("not-a-type-id")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        TypeSchemeApplied("Vec", (object(),))  # type: ignore[arg-type]


def test_contextual_result_preserves_error_row() -> None:
    assert contextual_result_type(
        "Result[FileReader,AppError]", "Result[FileReader,FsError]"
    ) == "Result[FileReader,FsError]"
    assert contextual_result_type("Text", "Result[Text,FsError]") == "Text"


def test_effect_set_is_derived_from_rows() -> None:
    assert INTRINSIC_EFFECTS == frozenset(signature.effect for signature in INTRINSIC_SIGNATURES.values())


def test_binder_and_elaborator_contract_views_are_derived() -> None:
    assert {"Path", "Ok", "Some", "checked_add"} <= BUILTIN_FUNCTIONS
    assert {"console", "fs", "network", "Text", "Vec"} <= BUILTIN_RECEIVERS
    assert INSTANCE_METHOD_SIGNATURES[("Text", "contains")].parameters == ("Text",)
    assert INSTANCE_METHOD_SIGNATURES[("TextBuilder", "finish")].result_ownership == "owned"
    assert BUILTIN_FUNCTION_SIGNATURES["Path"].parameters == ("Text",)
    assert BUILTIN_FUNCTION_SIGNATURES["drop"].parameter_ownership == ("consuming",)
    assert BUILTIN_FUNCTIONS == frozenset(BUILTIN_FUNCTION_SIGNATURES)
    assert CONTRACT_GRAPH.intrinsic("fs.write_text") is INTRINSIC_SIGNATURES["fs.write_text"]
    assert CONTRACT_GRAPH.abi_lowering("fs.write_text") == "merlo_file_write_text"
    assert CONTRACT_GRAPH.method("TextBuilder", "append_text").receiver_ownership == "borrow_mut"  # type: ignore[union-attr]
    assert CONTRACT_GRAPH.method("TextBuilder", "finish").receiver_ownership == "consuming"  # type: ignore[union-attr]
    text_from_bytes = CONTRACT_GRAPH.static_method("Text", "from_bytes")
    assert text_from_bytes is not None
    assert text_from_bytes.parameters == ("BytesView", "UInt64", "UInt64")
    assert text_from_bytes.result_type == "Text"
    assert text_from_bytes.result_ownership == "owned"
    assert text_from_bytes.effects == ("allocate", "copy", "may_fail")
    assert CONTRACT_GRAPH.abi_lowering("Text.from_bytes") == (
        "merlo_text_from_bytes"
    )
    builder_new = CONTRACT_GRAPH.static_method("TextBuilder", "new")
    assert builder_new is not None
    assert builder_new.effects == ("allocate", "may_fail")
    assert CONTRACT_GRAPH.abi_lowering("TextBuilder.new") == (
        "merlo_text_builder_new"
    )
    vec_new = CONTRACT_GRAPH.resolve_static_method(
        "Vec",
        "new",
        (),
        "Vec[Text]",
    )
    assert vec_new is not None
    assert vec_new.result_type == "Vec[Text]"
    assert vec_new.operation_family == "vec"
    assert vec_new.result_ownership == "owned"
    assert vec_new.effects == ("allocate", "may_fail")
    inferred_vec_new = CONTRACT_GRAPH.resolve_static_method(
        "Vec",
        "new",
        (),
    )
    assert inferred_vec_new is not None
    assert inferred_vec_new.result_type == "Vec[Inferred]"
    map_new = CONTRACT_GRAPH.resolve_static_method(
        "Map",
        "new",
        (),
        "Map[Text,Byte]",
    )
    assert map_new is not None
    assert map_new.result_type == "Map[Text,Byte]"
    default_map_new = CONTRACT_GRAPH.resolve_static_method(
        "Map",
        "new",
        (),
    )
    assert default_map_new is not None
    assert default_map_new.result_type == "Map[Text,UInt64]"
    box_new = CONTRACT_GRAPH.resolve_static_method(
        "Box",
        "new",
        ("Text",),
    )
    assert box_new is not None
    assert box_new.parameters == ("Text",)
    assert box_new.parameter_ownership == ("consuming",)
    assert box_new.result_type == "Box[Text]"
    option_predicate = CONTRACT_GRAPH.method(
        "Option[Text]",
        "is_some",
    )
    assert option_predicate is not None
    assert option_predicate.receiver_type == "Option[Text]"
    assert option_predicate.result_type == "Bool"
    assert option_predicate.receiver_ownership == "borrow"
    assert option_predicate.representation_lowering == "option_is_some"
    result_predicate = CONTRACT_GRAPH.method(
        "Result[UInt64,Text]",
        "is_err",
    )
    assert result_predicate is not None
    assert result_predicate.result_type == "Bool"
    assert result_predicate.representation_lowering == "result_is_err"
    assert CONTRACT_GRAPH.method("Option[Text]", "is_ok") is None
    assert CONTRACT_GRAPH.method("Result[UInt64,Text]", "is_none") is None
    option_unwrap = CONTRACT_GRAPH.method("Option[Text]", "unwrap")
    assert option_unwrap is not None
    assert option_unwrap.result_type == "Text"
    assert option_unwrap.result_ownership == "payload_move"
    assert option_unwrap.receiver_ownership == "borrow"
    assert option_unwrap.effects == ("may_fail",)
    assert option_unwrap.representation_lowering == "option_unwrap_move"
    result_unwrap_err = CONTRACT_GRAPH.method(
        "Result[UInt64,Text]",
        "unwrap_err",
    )
    assert result_unwrap_err is not None
    assert result_unwrap_err.result_type == "Text"
    assert result_unwrap_err.representation_lowering == (
        "result_unwrap_err_move"
    )
    vec_get = CONTRACT_GRAPH.method("Vec[Text]", "get")
    assert vec_get is not None
    assert vec_get.parameters == ("UInt64",)
    assert vec_get.result_type == "Text"
    assert vec_get.result_ownership == "borrow"
    assert vec_get.effects == ("bounds_check",)
    assert vec_get.operation_family == "vec"
    vec_push = CONTRACT_GRAPH.method("Vec[Text]", "push")
    assert vec_push is not None
    assert vec_push.parameters == ("Text",)
    assert vec_push.parameter_ownership == ("consuming",)
    assert vec_push.receiver_ownership == "borrow_mut"
    vec_clone = CONTRACT_GRAPH.method("Vec[Text]", "clone")
    assert vec_clone is not None
    assert vec_clone.result_type == "Vec[Text]"
    assert vec_clone.result_ownership == "owned"
    assert vec_clone.effects == ("allocate", "copy", "may_fail")
    map_insert = CONTRACT_GRAPH.method(
        "Map[Text,UInt64]",
        "insert",
    )
    assert map_insert is not None
    assert map_insert.parameters == ("Text", "UInt64")
    assert map_insert.receiver_ownership == "borrow_mut"
    assert map_insert.operation_family == "map"
    map_increment = CONTRACT_GRAPH.method(
        "Map[Text,UInt64]",
        "increment",
    )
    assert map_increment is not None
    assert map_increment.parameters == ("Text", "UInt64")
    assert map_increment.minimum_arity == 1
    assert map_increment.accepts_arity(1)
    assert map_increment.accepts_arity(2)
    assert not map_increment.accepts_arity(0)
    assert not map_increment.accepts_arity(3)
    assert map_increment.parameters_for(1) == ("Text",)
    assert map_increment.ownership_for(1) == ("borrow",)
    assert map_increment.result_type == "Unit"
    assert map_increment.receiver_ownership == "borrow_mut"
    assert CONTRACT_GRAPH.method("Map[Text,Byte]", "increment") is None
    map_entries = CONTRACT_GRAPH.method(
        "Map[Text,UInt64]",
        "entries",
    )
    assert map_entries is not None
    assert map_entries.result_type == "Borrow[Map[Text,UInt64]]"
    box_get = CONTRACT_GRAPH.method("Box[Text]", "get")
    assert box_get is not None
    assert box_get.result_type == "Text"
    assert box_get.result_ownership == "borrow"
    assert CONTRACT_GRAPH.method("Map[Text,UInt64]", "push") is None
    bytes_view = CONTRACT_GRAPH.method("Bytes", "view")
    assert bytes_view is not None
    assert bytes_view.result_type == "BytesView"
    assert bytes_view.result_ownership == "borrow"
    assert bytes_view.operation_family == "bytes_text"
    text_slice = CONTRACT_GRAPH.method("TextView", "slice_bytes")
    assert text_slice is not None
    assert text_slice.parameters == ("UInt64", "UInt64")
    assert text_slice.result_ownership == "borrow"
    assert text_slice.effects == ("bounds_check",)
    text_copy = CONTRACT_GRAPH.method("TextView", "to_text")
    assert text_copy is not None
    assert text_copy.result_ownership == "owned"
    assert text_copy.effects == ("allocate", "copy", "may_fail")
    append_text = CONTRACT_GRAPH.method("TextBuilder", "append_text")
    assert append_text is not None
    assert append_text.receiver_ownership == "borrow_mut"
    assert append_text.parameter_ownership == ("borrow",)
    assert append_text.effects == ("allocate", "copy", "may_fail")
    text_len = CONTRACT_GRAPH.method("Text", "len")
    assert text_len is not None
    assert text_len.result_type == "UInt64"
    assert text_len.result_for("Int64") == "Int64"
    assert text_len.result_for("Bool") == "UInt64"


def test_static_contracts_drive_hir_type_ownership_effects_and_abi() -> None:
    hir = compile_structured_hir(
        "fn build(input: BytesView) -> Text:\n"
        "    let copied: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let builder: TextBuilder = TextBuilder.new()\n"
        "    builder.append_text(copied)\n"
        "    builder.finish()\n",
        entry_function="build",
    )
    calls = {
        node.attribute_map.get("callee"): node
        for function in hir.functions
        for node in function.walk()
        if node.attribute_map.get("callee")
        in {"Text.from_bytes", "TextBuilder.new"}
    }
    copied = calls["Text.from_bytes"]
    assert copied.type_name == "Text"
    assert copied.ownership == "owned"
    assert set(copied.effects) == {"allocate", "copy", "may_fail"}
    assert copied.attribute_map["abi_lowering"] == "merlo_text_from_bytes"
    builder = calls["TextBuilder.new"]
    assert builder.type_name == "TextBuilder"
    assert builder.ownership == "owned"
    assert set(builder.effects) == {"allocate", "may_fail"}
    assert builder.attribute_map["abi_lowering"] == "merlo_text_builder_new"


def test_generic_predicate_contracts_drive_hir_metadata() -> None:
    hir = compile_structured_hir(
        "fn flags(option: Option[UInt64], result: Result[UInt64,UInt64]) -> Bool:\n"
        "    let some: Bool = option.is_some()\n"
        "    let ok: Bool = result.is_ok()\n"
        "    return some and ok\n",
        entry_function="flags",
    )
    predicates = {
        node.attribute_map.get("representation_lowering"): node
        for function in hir.functions
        for node in function.walk()
        if node.attribute_map.get("representation_lowering")
        in {"option_is_some", "result_is_ok"}
    }
    assert set(predicates) == {"option_is_some", "result_is_ok"}
    assert predicates["option_is_some"].type_name == "Bool"
    assert predicates["option_is_some"].ownership == "value"
    assert predicates["option_is_some"].attribute_map[
        "contract_symbol"
    ] == "Option[UInt64].is_some"
    assert predicates["result_is_ok"].type_name == "Bool"
    assert predicates["result_is_ok"].attribute_map[
        "contract_symbol"
    ] == "Result[UInt64,UInt64].is_ok"


def test_generic_unwrap_contracts_derive_payload_ownership_and_effects() -> None:
    hir = compile_structured_hir(
        "fn text(option: Option[Text]) -> Text:\n"
        "    return option.unwrap()\n"
        "fn scalar(result: Result[UInt64,Text]) -> UInt64:\n"
        "    return result.unwrap()\n",
        entry_function="text",
    )
    accessors = {
        node.attribute_map.get("contract_symbol"): node
        for function in hir.functions
        for node in function.walk()
        if node.attribute_map.get("representation_lowering")
        in {"option_unwrap_move", "result_unwrap_move"}
    }
    text = accessors["Option[Text].unwrap"]
    assert text.type_name == "Text"
    assert text.ownership == "owned"
    assert text.effects == ("may_fail",)
    scalar = accessors["Result[UInt64,Text].unwrap"]
    assert scalar.type_name == "UInt64"
    assert scalar.ownership == "value"
    assert scalar.effects == ("may_fail",)


def test_collection_contracts_drive_hir_types_ownership_and_effects() -> None:
    hir = compile_structured_hir(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let values: Vec[Byte] = Vec.new()\n"
        "    values.push(Byte(7))\n"
        "    let boxed: Box[Byte] = Box.new(values.get(0))\n"
        "    let counts: Map[Text,Byte] = Map.new()\n"
        '    counts.insert("key", boxed.get())\n'
        '    return UInt64(counts.get("key"))\n',
        entry_function="main",
    )
    operations = {
        node.attribute_map.get("contract_symbol"): node
        for function in hir.functions
        for node in function.walk()
        if node.attribute_map.get("operation_family")
    }

    vec_push = operations["Vec[Byte].push"]
    assert vec_push.kind == "VecOperation"
    assert vec_push.type_name == "Unit"
    assert vec_push.attribute_map["receiver_ownership"] == "borrow_mut"
    assert set(vec_push.effects) == {"allocate", "may_fail"}

    vec_get = operations["Vec[Byte].get"]
    assert vec_get.type_name == "Byte"
    assert vec_get.ownership == "borrow"
    assert vec_get.effects == ("bounds_check",)

    map_insert = operations["Map[Text,Byte].insert"]
    assert map_insert.kind == "MapOperation"
    assert map_insert.type_name == "Unit"
    assert map_insert.attribute_map["receiver_ownership"] == "borrow_mut"
    assert set(map_insert.effects) == {"allocate", "copy", "may_fail"}

    box_get = operations["Box[Byte].get"]
    assert box_get.kind == "BoxOperation"
    assert box_get.type_name == "Byte"
    assert box_get.ownership == "borrow"

    vec_new = operations["Vec.new"]
    assert vec_new.kind == "VecOperation"
    assert vec_new.type_name == "Vec[Byte]"
    assert vec_new.ownership == "owned"
    assert set(vec_new.effects) == {"allocate", "may_fail"}

    map_new = operations["Map.new"]
    assert map_new.kind == "MapOperation"
    assert map_new.type_name == "Map[Text,Byte]"
    assert map_new.attribute_map["map_specialization"] == "Map[Text,Byte]"

    box_new = operations["Box.new"]
    assert box_new.kind == "BoxOperation"
    assert box_new.type_name == "Box[Byte]"
    assert box_new.attribute_map["result_ownership"] == "owned"


def test_vec_push_consumes_an_owned_element() -> None:
    with pytest.raises(StructuredHIRCompileError, match="UseAfterMove: text"):
        compile_structured_hir(
            "fn bad(text: Text) -> UInt64:\n"
            "    let values: Vec[Text] = Vec.new()\n"
            "    values.push(text)\n"
            "    return text.len()\n",
            entry_function="bad",
        )


def test_box_new_consumes_an_owned_payload() -> None:
    with pytest.raises(StructuredHIRCompileError, match="UseAfterMove: text"):
        compile_structured_hir(
            "fn bad(text: Text) -> UInt64:\n"
            "    let boxed: Box[Text] = Box.new(text)\n"
            "    return text.len()\n",
            entry_function="bad",
        )


def test_bytes_and_text_contracts_drive_hir_metadata() -> None:
    hir = compile_structured_hir(
        "fn inspect(data: Bytes, view: BytesView, text: Text, "
        "text_view: TextView) -> Text:\n"
        "    let data_view: BytesView = data.view()\n"
        "    let copied: Text = data.to_text()\n"
        "    let slice: BytesView = view.slice(0, view.len())\n"
        "    let text_slice: TextView = text.slice_bytes(0, text.len())\n"
        "    let byte: UInt64 = text_view.byte(0)\n"
        "    let builder: TextBuilder = TextBuilder.new()\n"
        "    builder.append_text(copied)\n"
        "    builder.append_byte(byte)\n"
        "    return builder.finish()\n",
        entry_function="inspect",
    )
    operations = {
        node.attribute_map.get("contract_symbol"): node
        for function in hir.functions
        for node in function.walk()
        if node.attribute_map.get("operation_family") == "bytes_text"
    }

    assert operations["Bytes.view"].ownership == "borrow"
    assert operations["Bytes.to_text"].ownership == "owned"
    assert set(operations["Bytes.to_text"].effects) == {
        "allocate",
        "copy",
        "may_fail",
    }
    assert operations["BytesView.slice"].ownership == "borrow"
    assert operations["BytesView.slice"].effects == ("bounds_check",)
    assert operations["Text.slice_bytes"].ownership == "borrow"
    assert operations["TextView.byte"].effects == ("bounds_check",)
    assert set(operations["TextBuilder.append_text"].effects) == {
        "allocate",
        "copy",
        "may_fail",
    }
    assert operations["TextBuilder.finish"].ownership == "owned"


def test_contextual_numeric_method_results_remain_compatible() -> None:
    hir = compile_structured_hir(
        "fn size(text: Text) -> Int64:\n"
        "    return text.len()\n",
        entry_function="size",
    )
    length = next(
        node
        for node in hir.function("size").walk()
        if node.attribute_map.get("contract_symbol") == "Text.len"
    )
    assert length.type_name == "Int64"


def test_optional_map_increment_contract_drives_both_arities() -> None:
    hir = compile_structured_hir(
        "fn update(counts: Map[Text,UInt64], key: Text) -> Unit:\n"
        "    counts.increment(key)\n"
        "    counts.increment(key, 4)\n",
        entry_function="update",
    )
    increments = [
        node
        for node in hir.function("update").walk()
        if node.attribute_map.get("contract_symbol")
        == "Map[Text,UInt64].increment"
    ]
    assert len(increments) == 2
    assert {len(node.children) for node in increments} == {2, 3}
    assert all(node.kind == "MapOperation" for node in increments)
    assert all(node.type_name == "Unit" for node in increments)
    assert all(node.attribute_map["receiver_ownership"] == "borrow_mut" for node in increments)
    assert all(set(node.effects) == {"allocate", "copy", "may_fail"} for node in increments)

    with pytest.raises(StructuredHIRCompileError, match="ArityMismatch.*increment"):
        compile_structured_hir(
            "fn bad(counts: Map[Text,UInt64], key: Text) -> Unit:\n"
            "    counts.increment(key, 1, 2)\n",
            entry_function="bad",
        )


def test_generic_predicates_lower_to_native_enum_tags(
    tmp_path: Path,
) -> None:
    project = Project.create(
        tmp_path / "generic-predicates",
        name="generic_predicates",
    )
    source = project.source_dir / "main.mlo"
    source.write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn make_option() -> Option[Text]:\n"
        '    return Some("value")\n\n'
        "fn make_result() -> Result[Text,AppError]:\n"
        '    return Ok("value")\n\n'
        "export task main(path: Path) -> Result[Text,AppError]:\n"
        "    uses console.write\n"
        "    let option = make_option()\n"
        "    let result = make_result()\n"
        "    if option.is_some() and not option.is_none():\n"
        "        if result.is_ok() and not result.is_err():\n"
        '            console.write("predicates-ok")\n'
        '    return Ok("done")\n',
        encoding="utf-8",
    )
    compilation = compile_project(
        project.root,
        emit_native=True,
        output=tmp_path / "generic-predicates-app",
        require_interface_lock=False,
    )
    assert compilation.native is not None
    generated = compilation.generated.source
    assert all(
        marker in generated
        for marker in (
            "NoneValue_TAG",
            "Some_TAG",
            "Err_TAG",
            "Ok_TAG",
        )
    )
    completed = subprocess.run(
        [compilation.native.binary_path, str(project.root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert completed.stdout == "predicates-ok\n"


def test_owning_unwrap_moves_payload_without_double_free(
    tmp_path: Path,
) -> None:
    project = Project.create(
        tmp_path / "owning-unwrap",
        name="owning_unwrap",
    )
    source = project.source_dir / "main.mlo"
    source.write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export enum Problem:\n"
        "    Message: Text\n\n"
        "fn make_option() -> Option[Text]:\n"
        '    return Some("option")\n\n'
        "fn make_result() -> Result[Text,AppError]:\n"
        '    return Ok("result")\n\n'
        "fn make_error() -> Result[UInt64,Problem]:\n"
        '    return Err(Problem.Message("error"))\n\n'
        "fn take() -> Text:\n"
        "    let option = make_option()\n"
        "    let first: Text = option.unwrap()\n"
        "    let result = make_result()\n"
        "    let third: Text = result.unwrap()\n"
        "    return first\n\n"
        "fn take_error() -> Text:\n"
        "    let result = make_error()\n"
        "    let first: Problem = result.unwrap_err()\n"
        "    match first:\n"
        "        case Problem.Message(text):\n"
        "            return text\n\n"
        "export task main(path: Path) -> Result[Text,AppError]:\n"
        "    uses console.write\n"
        "    let value: Text = take()\n"
        "    let error: Text = take_error()\n"
        "    console.write(value)\n"
        "    console.write(error)\n"
        '    return Ok("done")\n',
        encoding="utf-8",
    )
    compilation = compile_project(
        project.root,
        emit_native=True,
        output=tmp_path / "owning-unwrap-app",
        require_interface_lock=False,
    )
    assert compilation.native is not None
    assert "option_unwrap_clone" not in compilation.generated.source
    assert "merlo_move_Text(&(" in compilation.generated.source
    completed = subprocess.run(
        [compilation.native.binary_path, str(project.root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "optionerror\n"

    compiler = shutil.which("clang")
    if compiler is None:
        pytest.skip("clang is required for the unwrap sanitizer regression")
    generated_c = tmp_path / "owning-unwrap.c"
    sanitized_binary = tmp_path / "owning-unwrap-sanitized"
    generated_c.write_text(compilation.generated.source, encoding="utf-8")
    built = subprocess.run(
        [
            compiler,
            "-std=c11",
            "-O1",
            "-g",
            "-fno-omit-frame-pointer",
            "-fsanitize=address,undefined",
            str(generated_c),
            "-o",
            str(sanitized_binary),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    environment = dict(os.environ)
    environment.update(
        {
            "ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1",
            "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
        }
    )
    sanitized = subprocess.run(
        [sanitized_binary, str(project.root)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert sanitized.returncode == 0, sanitized.stderr
    assert sanitized.stdout == "optionerror\n"


def test_owning_unwrap_can_be_borrowed_inside_short_circuit_expression(
    tmp_path: Path,
) -> None:
    project = Project.create(
        tmp_path / "borrowed-unwrap",
        name="borrowed_unwrap",
    )
    source = project.source_dir / "main.mlo"
    source.write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn matches(message: Text, needle: Option[Text]) -> Bool:\n"
        "    return needle.is_some() and message.contains(needle.unwrap())\n\n"
        "export task main(path: Path) -> Result[Text,AppError]:\n"
        "    uses console.write\n"
        '    let message: Text = "the needle is here"\n'
        '    let needle: Option[Text] = Some("needle")\n'
        "    if matches(message, needle):\n"
        '        console.write("borrowed-ok")\n'
        '    return Ok("done")\n',
        encoding="utf-8",
    )
    compilation = compile_project(
        project.root,
        emit_native=True,
        output=tmp_path / "borrowed-unwrap-app",
        require_interface_lock=False,
    )
    assert compilation.native is not None
    assert "OptionUnwrapWrongVariant" in compilation.generated.source
    completed = subprocess.run(
        [compilation.native.binary_path, str(project.root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "borrowed-ok\n"


def test_unwrap_wrong_variant_traps_instead_of_reading_inactive_union(
    tmp_path: Path,
) -> None:
    project = Project.create(
        tmp_path / "wrong-unwrap",
        name="wrong_unwrap",
    )
    source = project.source_dir / "main.mlo"
    source.write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn invalid() -> UInt64:\n"
        "    let option: Option[UInt64] = None\n"
        "    return option.unwrap()\n\n"
        "export task main(path: Path) -> Result[Text,AppError]:\n"
        "    uses console.write\n"
        "    let value: UInt64 = invalid()\n"
        '    console.write("unreachable")\n'
        '    return Ok("done")\n',
        encoding="utf-8",
    )
    compilation = compile_project(
        project.root,
        emit_native=True,
        output=tmp_path / "wrong-unwrap-app",
        require_interface_lock=False,
    )
    assert compilation.native is not None
    completed = subprocess.run(
        [compilation.native.binary_path, str(project.root)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "MerloOwnership:OptionUnwrapWrongVariant" in completed.stderr


@pytest.mark.parametrize(
    "source",
    (
        "fn bad(value: Option[UInt64]) -> Bool:\n"
        "    return value.is_ok()\n",
        "fn bad(value: Result[UInt64,UInt64]) -> Bool:\n"
        "    return value.is_none()\n",
        "fn bad(value: Option[UInt64]) -> UInt64:\n"
        "    return value.unwrap_err()\n",
    ),
)
def test_generic_predicates_reject_wrong_receiver_family(
    source: str,
) -> None:
    with pytest.raises(
        StructuredHIRCompileError,
        match="UnknownCall",
    ):
        compile_structured_hir(source, entry_function="bad")


def test_static_contracts_drive_backend_primitive_manifest(
    tmp_path: Path,
) -> None:
    project = Project.create(tmp_path / "contract-graph", name="contract_graph")
    (project.source_dir / "main.mlo").write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    let input: Bytes = console.read()\n"
        "    let copied: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let builder: TextBuilder = TextBuilder.new()\n"
        "    builder.append_text(copied)\n"
        "    Ok(builder.finish())\n",
        encoding="utf-8",
    )
    generated = compile_project(
        project.root,
        require_interface_lock=False,
    ).generated
    manifest = {
        item["name"]: item
        for item in generated.primitive_manifest
    }
    copied = manifest["Text.from_bytes"]
    assert copied["type_signature"] == (
        "fn(BytesView, UInt64, UInt64) -> Text"
    )
    assert copied["may_allocate"] is True
    assert copied["may_copy"] is True
    assert copied["may_fail"] is True
    builder = manifest["TextBuilder.new"]
    assert builder["type_signature"] == "fn() -> TextBuilder"
    assert builder["may_allocate"] is True
    assert builder["may_copy"] is False
    assert builder["may_fail"] is True


@pytest.mark.parametrize("name", tuple(EXPECTED))
def test_arity_is_contractual(name: str) -> None:
    signature = intrinsic_signature(name)
    assert signature is not None
    assert signature.arity == len(signature.parameters)


def test_result_intrinsic_cannot_be_returned_as_its_ok_type() -> None:
    source = (
        "task bad(path: Path) -> Bytes:\n"
        "    uses fs.read\n"
        "    return fs.read(path)\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="ReturnTypeMismatch"):
        compile_structured_hir(source, entry_function="bad")


def test_removed_tcp_alias_is_rejected() -> None:
    source = (
        "task bad() -> UInt64:\n"
        "    uses network.tcp\n"
        "    return tcp.connect()\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="UnknownIntrinsic"):
        compile_structured_hir(source, entry_function="bad")


def test_file_handles_are_mode_specific() -> None:
    writer_to_reader = (
        "task bad(path: Path) -> Result[Bytes,AppError]:\n"
        "    uses fs.read, fs.write\n"
        "    let output: FileWriter = fs.open_write(path)?\n"
        "    return fs.read_chunk(output, 1)\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="IntrinsicTypeMismatch"):
        compile_structured_hir(writer_to_reader, entry_function="bad")

    reader_to_writer = (
        "task bad(path: Path, data: BytesView) -> Result[Unit,AppError]:\n"
        "    uses fs.read, fs.write\n"
        "    let input: FileReader = fs.open_read(path)?\n"
        "    return fs.write_chunk(input, data)\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="IntrinsicTypeMismatch"):
        compile_structured_hir(reader_to_writer, entry_function="bad")


def test_file_close_effect_matches_handle_mode() -> None:
    assert intrinsic_signature("fs.close_read").effect == "fs.read"  # type: ignore[union-attr]
    assert intrinsic_signature("fs.close_write").effect == "fs.write"  # type: ignore[union-attr]
    assert intrinsic_signature("fs.close") is None


@pytest.mark.parametrize(
    "source",
    (
        "task bad(path: Path, data: BytesView) -> Result[Unit,AppError]:\n"
        "    uses fs.write\n"
        "    let file: FileWriter = fs.open_write(path)?\n"
        "    fs.close_write(file)?\n"
        "    return fs.write_chunk(file, data)?\n",
        "task bad(path: Path) -> Result[Bytes,AppError]:\n"
        "    uses fs.read\n"
        "    let file: FileReader = fs.open_read(path)?\n"
        "    fs.close_read(file)?\n"
        "    return fs.read_chunk(file, 1)?\n",
    ),
)
def test_use_after_explicit_resource_close_is_rejected(source: str) -> None:
    with pytest.raises(StructuredHIRCompileError, match="UseAfterMove"):
        compile_structured_hir(source, entry_function="bad")


def test_consuming_instance_method_invalidates_receiver() -> None:
    source = (
        "task bad() -> Text:\n"
        "    let builder: TextBuilder = TextBuilder.new()\n"
        "    let text: Text = builder.finish()\n"
        "    builder.append_text(\"use after finish\")\n"
        "    return text\n"
    )

    with pytest.raises(StructuredHIRCompileError, match="UseAfterMove: builder"):
        compile_structured_hir(source, entry_function="bad")
