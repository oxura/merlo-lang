from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from merlo import native_syntax as ast
from merlo.structured_hir_v2 import (
    STRUCTURED_HIR_CONTRACT,
    StructuredHIRProgram,
    compile_canonical_hir,
    compile_structured_hir,
)
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface
from merlo.typed_ast import TypedAst, TypedAstError
from merlo.type_arena import (
    FrozenTypeArenaMutation,
    TypeArena,
    TypeContextBuilder,
    TypeExpr,
)


ROOT = Path(__file__).resolve().parents[1]


HIR_SOURCE = (
    "record Packet:\n"
    "    value: Vec[Option[Text]]\n"
    "enum Outcome:\n"
    "    Ok: Packet\n"
    "    Empty\n"
    "fn main(input: BytesView) -> UInt64:\n"
    "    require input.len() >= 0\n"
    "    let value: Int = 1\n"
    "    var count: UInt64 = 0\n"
    "    count = count + UInt64(value)\n"
    "    return count\n"
)

DIRECT_SOURCE = (
    "record DirectPacket:\n"
    "    payload: Map[Text,Option[Box[DirectPacket]]]\n"
    "fn main(input: BytesView) -> UInt64:\n"
    "    let value: UInt64 = 1\n"
    "    return value\n"
)

FLOW_MACHINE_SOURCE = (
    "durable flow ingest(input: Text) -> Result[Text,Err]:\n"
    "    output = read(input) idempotent by input\n"
    "machine Job(id: UInt64):\n"
    "    state Idle\n"
    "    state Running(value: Text)\n"
    "    initial Idle\n"
    "    invariant id >= 0\n"
    "    transition start from Idle -> Running:\n"
    "        value = read(id)\n"
)

FFI_SOURCE = (
    'extern "C" fn abs(value: Int32) -> Int32\n'
    "repr(C) record Pair:\n"
    "    tag: UInt8\n"
    "    value: UInt32\n\n"
    "fn main(input: BytesView) -> UInt64:\n"
    "    return 0\n"
)

FFI_POINTER_ALIAS_SOURCE = (
    'extern "C" fn pointers('
    "raw: RawPointer[ Int32 ] {read, borrowed}, "
    "ptr: Ptr[Int32] {read, borrowed}, "
    "const_ptr: ConstPointer[Int32] {read, borrowed}, "
    "mut_ptr: MutPointer[Int32] {write, borrowed}"
    ") -> Int32\n"
    "fn main(input: BytesView) -> UInt64:\n"
    "    return 0\n"
)


def _hir(source: str = HIR_SOURCE) -> StructuredHIRProgram:
    path = "hir-type-arena.mlo"
    if source == FFI_SOURCE:
        return compile_structured_hir(source, path=path)
    canonical = elaborate_surface(
        parse_surface(source, path=path)
    ).canonical
    return compile_canonical_hir(canonical)


def _typed_payload(program: StructuredHIRProgram) -> dict[str, Any]:
    payload = program.to_dict()
    assert payload["contract"] == STRUCTURED_HIR_CONTRACT
    assert payload["schema_version"] == 11
    assert "type_arena" in payload
    assert payload["type_arena_digest"] == program.type_arena_digest
    return payload

def _refresh_arena_digest(payload: dict[str, Any]) -> None:
    arena_json = (
        json.dumps(
            payload["type_arena"],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
    payload["type_arena_digest"] = hashlib.sha256(
        arena_json.encode("utf-8")
    ).hexdigest()


def _assert_type_pair(item: dict[str, Any], spelling: str, identity: str) -> None:
    value = item.get(spelling)
    type_id = item.get(identity)
    assert (value is None) == (type_id is None)
    if value is not None:
        assert isinstance(value, str)
        assert isinstance(type_id, dict)
        assert type_id["contract"] == "merlo.type-id.v1"
        assert isinstance(type_id["value"], str)


def _assert_hir_typed_positions(program: StructuredHIRProgram) -> None:
    payload = _typed_payload(program)
    for declaration in payload["types"]:
        assert isinstance(declaration["type_id"], dict)
        assert declaration["type_id"]["contract"] == "merlo.type-id.v1"
        assert isinstance(declaration["type_id"]["value"], str)
        for field in declaration["fields"]:
            _assert_type_pair(field, "type", "type_id")
        for variant in declaration["variants"]:
            _assert_type_pair(variant, "payload_type", "payload_type_id")

    for owner in (*payload["functions"], *payload["flows"]):
        for parameter in owner["parameters"]:
            _assert_type_pair(parameter, "type", "type_id")
        _assert_type_pair(owner, "return_type", "return_type_id")
        for contract in (*owner.get("requirements", []), *owner.get("ensures", [])):
            _assert_type_pair(contract["condition"], "type", "type_id")
        for node in owner["body"]:
            for nested in _walk_nodes(node):
                _assert_type_pair(nested, "type", "type_id")

    for machine in payload["machines"]:
        for parameter in machine["parameters"]:
            _assert_type_pair(parameter, "type", "type_id")
        for state in machine["states"]:
            for field in state["fields"]:
                _assert_type_pair(field, "type", "type_id")
        for node in machine["transitions"]:
            for nested in _walk_nodes(node):
                _assert_type_pair(nested, "type", "type_id")

    ffi = payload["ffi"]
    for function in ffi["extern_functions"]:
        for parameter in function["parameters"]:
            _assert_type_pair(parameter, "type", "type_id")
        _assert_type_pair(function, "return_type", "return_type_id")
        _assert_type_pair(function, "error_type", "error_type_id")
    for record in ffi["repr_c_records"]:
        for field in record["fields"]:
            _assert_type_pair(field, "type_name", "type_id")


def _walk_nodes(node: dict[str, Any]):
    yield node
    for child in node["children"]:
        yield from _walk_nodes(child)


def test_hir_v11_json_roundtrip_is_canonical_and_type_arena_is_closed() -> None:
    original = _hir()
    restored = StructuredHIRProgram.from_json(original.to_json())

    assert original.contract == "merlo.structured-typed-hir.v11"
    assert original.schema_version == 11
    assert original.type_context.arena.allow_unresolved is False
    assert original.type_arena_digest == original.type_context.arena.digest
    assert restored.to_json() == original.to_json()
    assert restored.digest == original.digest
    assert restored.type_context.arena.to_json() == original.type_context.arena.to_json()
    _assert_hir_typed_positions(original)


def test_hir_owns_frozen_context_and_type_declarations() -> None:
    program = _hir()
    assert not hasattr(program, "type_arena")
    declaration = program.types[0]
    projection = program.type_context.declaration(declaration.type_id)
    assert projection.type_id == declaration.type_id
    assert projection.kind == "record"
    assert tuple(item.name for item in projection.fields) == tuple(
        item.name for item in declaration.fields
    )
    with pytest.raises(TypeError):
        program.type_context.declarations[declaration.type_id] = projection
    with pytest.raises(AttributeError):
        projection.kind = "enum"
    for operation in (
        lambda: program.type_context.arena.intern_text("Bool"),
        lambda: program.type_context.arena.intern_many(("Bool",)),
        lambda: program.type_context.arena.intern_expr(TypeExpr("Bool")),
        lambda: program.type_context.arena.intern_node("Bool"),
    ):
        with pytest.raises(FrozenTypeArenaMutation):
            operation()

def test_hir_json_digest_and_reproduction_are_stable_in_a_fresh_process() -> None:
    warmup_source = (
        "record WarmupContainer:\n"
        "    payload: Map[Text,Option[Box[WarmupContainer]]]\n"
        "fn warmup_entry(long_input_name: BytesView) -> UInt64:\n"
        "    return 0\n"
    )
    warmup = elaborate_surface(
        parse_surface(warmup_source, path="warmup-with-materially-long-positions.mlo")
    ).canonical
    compile_canonical_hir(warmup, entry_function="warmup_entry")
    original = _hir()
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,sys; "
                "from merlo.structured_hir_v2 import compile_canonical_hir; "
                "from merlo.surface_elaborator import elaborate_surface; "
                "from merlo.surface_parser import parse_surface; "
                "source=sys.stdin.read(); "
                "program=compile_canonical_hir(elaborate_surface("
                "parse_surface(source, path='hir-type-arena.mlo')).canonical); "
                "print(json.dumps({'json': program.to_json(), 'hir': program.digest, "
                "'arena': program.type_arena_digest}, sort_keys=True))"
            ),
        ],
        cwd=ROOT,
        input=HIR_SOURCE,
        text=True,
        capture_output=True,
        check=False,
    )

    assert probe.returncode == 0, probe.stderr
    evidence = json.loads(probe.stdout)
    assert evidence == {
        "arena": original.type_arena_digest,
        "hir": original.digest,
        "json": original.to_json(),
    }


def test_direct_hir_compile_is_deterministic_after_direct_warmup() -> None:
    warmup = (
        "record WarmupDirectContainer:\n"
        "    payload: Map[Text,Option[Box[WarmupDirectContainer]]]\n"
        "fn main(long_input_name: BytesView) -> UInt64:\n"
        "    return 0\n"
    )
    compile_structured_hir(
        warmup,
        path="direct-warmup-with-materially-long-positions.mlo",
    )
    original = compile_structured_hir(DIRECT_SOURCE, path="direct-target.mlo")
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,sys; "
                "from merlo.structured_hir_v2 import compile_structured_hir; "
                "program=compile_structured_hir(sys.stdin.read(), path='direct-target.mlo'); "
                "print(json.dumps({'json': program.to_json(), 'hir': program.digest, "
                "'arena': program.type_arena_digest}, sort_keys=True))"
            ),
        ],
        cwd=ROOT,
        input=DIRECT_SOURCE,
        text=True,
        capture_output=True,
        check=False,
    )

    assert probe.returncode == 0, probe.stderr
    assert json.loads(probe.stdout) == {
        "arena": original.type_arena_digest,
        "hir": original.digest,
        "json": original.to_json(),
    }


def test_hir_type_ids_cover_local_contract_flow_and_machine_positions() -> None:
    _assert_hir_typed_positions(_hir())
    canonical = elaborate_surface(
        parse_surface(FLOW_MACHINE_SOURCE, path="flow-machine-types.mlo")
    ).canonical
    flow_machine = compile_canonical_hir(canonical)

    assert flow_machine.flows and flow_machine.machines
    _assert_hir_typed_positions(flow_machine)
    state_fields = flow_machine.to_dict()["machines"][0]["states"][1]["fields"]
    assert state_fields == [
        {
            "name": "value",
            "type": "Text",
            "type_id": flow_machine.type_context.type_id("Text").to_dict(),
        }
    ]


def test_hir_ffi_json_annotations_validate_then_restore_unannotated_ffi() -> None:
    program = _hir(FFI_SOURCE)
    payload = _typed_payload(program)
    _assert_hir_typed_positions(program)
    restored = StructuredHIRProgram.from_dict(payload)

    assert restored.ffi_program == program.ffi_program
    assert "type_id" not in program.ffi_program.to_dict()["extern_functions"][0]["parameters"][0]
    assert payload["ffi"]["extern_functions"][0]["parameters"][0]["type_id"]
    assert payload["ffi"]["extern_functions"][0]["return_type_id"]
    assert payload["ffi"]["repr_c_records"][0]["fields"][0]["type_id"]

def test_hir_ffi_pointer_aliases_roundtrip_with_canonical_type_ids() -> None:
    program = compile_structured_hir(
        FFI_POINTER_ALIAS_SOURCE,
        path="ffi-pointer-aliases.mlo",
    )
    extern = program.ffi_program.extern_functions[0]
    expected_spellings = (
        "RawPointer[ Int32 ]",
        "Ptr[Int32]",
        "ConstPointer[Int32]",
        "MutPointer[Int32]",
    )
    assert tuple(item.type_name for item in extern.parameters) == expected_spellings

    payload = _typed_payload(program)
    parameters = payload["ffi"]["extern_functions"][0]["parameters"]
    expected_id = program.type_context.type_id("RawPointer[Int32]").to_dict()
    assert [item["type_id"] for item in parameters] == [expected_id] * 4
    assert [item["type"] for item in parameters] == list(expected_spellings)
    assert StructuredHIRProgram.from_json(program.to_json()).to_json() == program.to_json()


def test_hir_aliases_nested_generics_and_qualified_nominals_converge() -> None:
    arena = TypeArena()
    aliases = arena.intern_text("Result[Vec[Int],Option[app.model.User]]")
    canonical = arena.intern_text("Result[Vec[Int64],Option[app.model.User]]")
    qualified_left = arena.intern_text("app.model.User")
    qualified_right = arena.intern_text("other.model.User")

    assert aliases == canonical
    assert arena.canonical(aliases) == "Result[Vec[Int64],Option[app.model.User]]"
    assert qualified_left != qualified_right

    alias_program = _hir(
        HIR_SOURCE.replace("value: Int", "value: Int64")
    )
    direct_program = _hir()
    assert (
        alias_program.function("main").parameters
        == direct_program.function("main").parameters
    )
    alias_let = next(
        node for node in alias_program.function("main").walk()
        if node.kind == "LetBinding"
    )
    direct_let = next(
        node for node in direct_program.function("main").walk()
        if node.kind == "LetBinding"
    )
    assert alias_let.type_id == direct_let.type_id


def test_hir_type_arena_identity_and_digest_ignore_insertion_order() -> None:
    spellings = (
        "Map[Text,Option[app.model.User]]",
        "Result[Vec[Int],app.model.Error]",
        "Array[UInt64,4]",
    )
    first = TypeArena()
    second = TypeArena()
    first.intern_many(spellings)
    second.intern_many(reversed(spellings))

    assert first.to_json() == second.to_json()
    assert first.digest == second.digest
    assert [first.intern_text(item) for item in spellings] == [
        second.intern_text(item) for item in spellings
    ]


def test_hir_nominal_type_id_is_stable_while_revision_and_digest_change() -> None:
    first = _hir()
    changed = _hir(
        HIR_SOURCE.replace(
            "value: Vec[Option[Text]]", "value: Vec[Option[UInt64]]"
        )
    )

    first_decl = first.type_decl("Packet")
    changed_decl = changed.type_decl("Packet")
    assert first_decl.type_id == changed_decl.type_id
    assert first_decl.type_id.value not in {
        first_decl.symbol_id,
        first_decl.revision_id,
    }
    assert changed_decl.type_id.value not in {
        changed_decl.symbol_id,
        changed_decl.revision_id,
    }
    assert first_decl.symbol_id == changed_decl.symbol_id
    assert first_decl.revision_id != changed_decl.revision_id
    assert first.digest != changed.digest


def _assert_missing_type_id_rejected(
    payload: dict[str, Any],
    item: dict[str, Any],
    identity: str = "type_id",
) -> None:
    assert item[identity]
    del item[identity]
    with pytest.raises(ValueError):
        StructuredHIRProgram.from_dict(payload)


@pytest.mark.parametrize(
    "position",
    (
        "declaration_field",
        "enum_payload",
        "function_parameter",
        "function_return",
        "function_node",
        "flow_parameter",
        "flow_return",
        "flow_node",
        "machine_parameter",
        "machine_state_field",
        "machine_transition_node",
        "ffi_parameter",
        "ffi_return",
        "ffi_error",
        "repr_c_field",
    ),
)
def test_hir_reader_rejects_missing_type_id_at_every_typed_position(
    position: str,
) -> None:
    if position in {
        "declaration_field",
        "enum_payload",
        "function_parameter",
        "function_return",
        "function_node",
    }:
        payload = _typed_payload(_hir())
        if position == "declaration_field":
            _assert_missing_type_id_rejected(
                payload, payload["types"][0]["fields"][0]
            )
            return
        elif position == "enum_payload":
            _assert_missing_type_id_rejected(
                payload,
                payload["types"][1]["variants"][0],
                "payload_type_id",
            )
            return
        else:
            function = payload["functions"][0]
            if position == "function_parameter":
                item = function["parameters"][0]
            elif position == "function_return":
                item = function
            else:
                item = next(
                    node
                    for root in function["body"]
                    for node in _walk_nodes(root)
                    if node["type"] is not None
                )
            _assert_missing_type_id_rejected(
                payload,
                item if position != "function_return" else function,
                "return_type_id" if position == "function_return" else "type_id",
            )
            return
    elif position.startswith("flow_") or position.startswith("machine_"):
        canonical = elaborate_surface(
            parse_surface(FLOW_MACHINE_SOURCE, path="missing-type-id.mlo")
        ).canonical
        payload = _typed_payload(compile_canonical_hir(canonical))
        if position == "flow_parameter":
            item = payload["flows"][0]["parameters"][0]
        elif position == "flow_return":
            item = payload["flows"][0]
        elif position == "flow_node":
            item = next(
                node
                for root in payload["flows"][0]["body"]
                for node in _walk_nodes(root)
                if node["type"] is not None
            )
        elif position == "machine_parameter":
            item = payload["machines"][0]["parameters"][0]
        elif position == "machine_state_field":
            item = payload["machines"][0]["states"][1]["fields"][0]
        else:
            item = next(
                node
                for root in payload["machines"][0]["transitions"]
                for node in _walk_nodes(root)
                if node["type"] is not None
            )
        _assert_missing_type_id_rejected(
            payload,
            item,
            "return_type_id" if position == "flow_return" else "type_id",
        )
        return
    else:
        program = _hir(FFI_SOURCE)
        payload = _typed_payload(program)
        extern = payload["ffi"]["extern_functions"][0]
        if position == "ffi_parameter":
            item = extern["parameters"][0]
        elif position == "ffi_return":
            item = extern
        elif position == "ffi_error":
            extern["error_type"] = "Int32"
            extern["error_type_id"] = program.type_context.type_id("Int32").to_dict()
            item = extern
        else:
            item = payload["ffi"]["repr_c_records"][0]["fields"][0]
        _assert_missing_type_id_rejected(
            payload,
            item,
            (
                "return_type_id"
                if position == "ffi_return"
                else "error_type_id"
                if position == "ffi_error"
                else "type_id"
            ),
        )
        return


def test_hir_reader_rejects_unknown_arena_child_before_typed_nodes() -> None:
    payload = _typed_payload(_hir())
    generic_entry = next(
        item for item in payload["type_arena"]["entries"]
        if item["type"]["arguments"]
    )
    generic_entry["type"]["arguments"][0] = "f" * 64
    _refresh_arena_digest(payload)

    with pytest.raises(ValueError):
        StructuredHIRProgram.from_dict(payload)


def test_hir_reader_rejects_tampered_type_id_and_mismatched_spelling() -> None:
    original = _typed_payload(_hir())
    tampered_id = copy.deepcopy(original)
    parameter = tampered_id["functions"][0]["parameters"][0]
    other = next(
        item["id"] for item in tampered_id["type_arena"]["entries"]
        if item["id"] != parameter["type_id"]
    )
    parameter["type_id"] = other
    with pytest.raises(ValueError):
        StructuredHIRProgram.from_dict(tampered_id)

    mismatched_spelling = copy.deepcopy(original)
    mismatched_spelling["functions"][0]["parameters"][0]["type"] = "Bool"
    with pytest.raises(ValueError):
        StructuredHIRProgram.from_dict(mismatched_spelling)


def test_hir_reader_rejects_unresolved_arena_and_hir_v9() -> None:
    tampered_digest = _typed_payload(_hir())
    tampered_digest["type_arena_digest"] = "0" * 64
    with pytest.raises(ValueError, match="Structured HIR type arena digest mismatch"):
        StructuredHIRProgram.from_dict(tampered_digest)

    unresolved = _typed_payload(_hir())
    unresolved["type_arena"]["allow_unresolved"] = True
    _refresh_arena_digest(unresolved)
    with pytest.raises(ValueError):
        StructuredHIRProgram.from_dict(unresolved)

    legacy = _typed_payload(_hir())
    legacy["schema_version"] = 9
    legacy["contract"] = "merlo.structured-typed-hir.v9"
    with pytest.raises(ValueError):
        StructuredHIRProgram.from_dict(legacy)


def test_typed_ast_is_identity_keyed_strict_and_conflict_checked() -> None:
    builder = TypeContextBuilder()
    text = builder.intern_text("Text")
    bytes_type = builder.intern_text("Bytes")
    option = builder.intern_text("Option[Text]")
    first = ast.parse("value = source").body[0].value
    second = ast.parse("value = source").body[0].value
    typed = TypedAst()

    assert typed.record_expression(first, text) == text
    assert typed.expression_type_id(first) == text
    with pytest.raises(TypedAstError, match="missing expression TypeId"):
        typed.expression_type_id(second)
    with pytest.raises(TypedAstError, match="expression must be TypeId"):
        typed.record_expression(second, "Text")
    with pytest.raises(TypedAstError, match="conflicting expression TypeId"):
        typed.record_expression(first, bytes_type)

    typed.record_variant_projection(option, "Some", text)
    typed.record_variant_projection_symbol_id(option, "Some", "variant-some")
    assert typed.variant_projection_type_id(option, "Some") == text
    assert typed.variant_projection_symbol_id(option, "Some") == "variant-some"
    with pytest.raises(TypedAstError, match="conflicting variant projection TypeId"):
        typed.record_variant_projection(option, "Some", bytes_type)
    with pytest.raises(TypedAstError, match="missing variant projection symbol"):
        typed.variant_projection_symbol_id(option, "None")
