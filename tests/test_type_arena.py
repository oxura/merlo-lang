from __future__ import annotations

import copy
import json

import pytest

from merlo.type_arena import (
    TYPE_ARENA_CONTRACT,
    TYPE_ARENA_SCHEMA_VERSION,
    TYPE_REF_CONTRACT,
    FrozenTypeArena,
    FrozenTypeArenaMutation,
    TypeArena,
    TypeContextBuilder,
    TypeArenaError,
    TypeArenaSchemaError,
    TypeDeclaration,
    TypeExpr,
    TypeId,
    TypeMember,
    TypeRef,
    UnknownTypeIdError,
    UnresolvedTypeError,
)


def test_nested_type_is_interned_structurally() -> None:
    arena = TypeArena()

    type_id = arena.intern_text("Map[Text, Option[Box[app.model.User]]]")

    assert arena.canonical(type_id) == "Map[Text,Option[Box[app.model.User]]]"
    root = arena.resolve(type_id)
    assert root.constructor == "Map"
    assert len(root.arguments) == 2
    assert arena.canonical(root.arguments[1]) == "Option[Box[app.model.User]]"


def test_aliases_normalize_at_the_boundary() -> None:
    arena = TypeArena()

    assert arena.intern_text("Int") == arena.intern_text("Int64")
    assert arena.intern_text("Vec[UInt]") == arena.intern_text("Vec[UInt64]")
    assert arena.intern_text("Result[Float,Int]") == arena.intern_text(
        "Result[Float64,Int64]"
    )

@pytest.mark.parametrize(
    ("alias", "canonical", "type_id"),
    (
        (
            "Int",
            "Int64",
            "d93b06a5a8bf17327f809694e869d72084c65ebcd902bef8b748e035cc29a2f2",
        ),
        (
            "UInt",
            "UInt64",
            "0739680aebee4551e05efd23f41edec5921510642a982ac19fbaf82382eb456d",
        ),
        (
            "Float",
            "Float64",
            "2d0e3ec626b027734f3d87e337957ed989deef01b58e2cb52eb0b76ab9114a4c",
        ),
    ),
)
def test_typeref_aliases_share_interned_payload_and_existing_ids(
    alias: str,
    canonical: str,
    type_id: str,
) -> None:
    arena = TypeArena()
    interned_id = arena.intern_text(alias)
    interned = arena.resolve(interned_id)
    direct = TypeRef(alias)
    decoded = TypeRef.from_dict(
        {
            "contract": TYPE_REF_CONTRACT,
            "constructor": alias,
            "arguments": [],
        }
    )

    assert interned_id == arena.intern_text(canonical) == TypeId(type_id)
    assert direct.constructor == decoded.constructor == canonical
    assert direct.semantic_payload() == interned.semantic_payload()
    assert decoded.semantic_payload() == interned.semantic_payload()



def test_qualified_nominal_names_remain_distinct() -> None:
    arena = TypeArena()

    left = arena.intern_text("app.model.User")
    right = arena.intern_text("other.model.User")

    assert left != right
    assert arena.canonical(left) == "app.model.User"
    assert arena.canonical(right) == "other.model.User"


def test_array_length_is_a_structural_atom() -> None:
    arena = TypeArena()

    type_id = arena.intern_text("Array[Text, 4]")

    assert arena.canonical(type_id) == "Array[Text,4]"
    length_id = arena.resolve(type_id).arguments[1]
    assert arena.resolve(length_id).constructor == "4"


def test_duplicate_interning_is_idempotent() -> None:
    arena = TypeArena()

    first = arena.intern_text("Option[Text]")
    node_count = len(arena)
    second = arena.intern_text("Option[Text]")

    assert first == second
    assert len(arena) == node_count



def test_freeze_rejects_every_mutation_entrypoint() -> None:
    arena = TypeArena()
    text = arena.intern_text("Text")
    frozen = arena.freeze()

    assert frozen.resolve(text).constructor == "Text"
    assert arena.freeze() is frozen
    for operation in (
        lambda: arena.intern_text("Bool"),
        lambda: arena.intern_many(("Bool",)),
        lambda: arena.intern_expr(TypeExpr("Bool")),
        lambda: arena.intern_node("Bool"),
    ):
        with pytest.raises(FrozenTypeArenaMutation):
            operation()
def test_type_context_uses_immutable_type_id_declarations() -> None:
    builder = TypeContextBuilder()
    type_id = builder.intern_text("Vec[Text]")
    text = builder.intern_text("Text")
    builder.register_declaration(
        TypeDeclaration(
            type_id,
            "record",
            fields=(TypeMember("value", text),),
        )
    )
    context = builder.freeze()

    declaration = context.declaration(type_id)
    assert context.resolve(type_id) == context.arena.resolve(type_id)
    assert declaration.type_id == type_id
    assert declaration.fields == (TypeMember("value", text),)
    assert context.render(type_id) == "Vec[Text]"
    assert context.type_id("Vec[Text]") == type_id
    with pytest.raises(UnknownTypeIdError):
        context.type_id("Vec[ Int ]")
    with pytest.raises(UnknownTypeIdError):
        context.declaration(TypeId("f" * 64))
    with pytest.raises(TypeError):
        context.declarations[type_id] = declaration
    with pytest.raises(FrozenTypeArenaMutation):
        context.arena = context.arena
    with pytest.raises(FrozenTypeArenaMutation):
        builder.intern_text("Bool")
    for target, attribute in (
        (context, "_declarations"),
        (context, "_type_ids"),
        (context.arena, "_nodes"),
        (context.arena, "allow_unresolved"),
    ):
        with pytest.raises(FrozenTypeArenaMutation):
            delattr(target, attribute)
    with pytest.raises(AttributeError):
        declaration.kind = "enum"


def test_frozen_type_arena_rejects_forged_identity_and_child_closure() -> None:
    arena = TypeArena()
    text = arena.intern_text("Text")
    root = arena.intern_text("Vec[Text]")

    with pytest.raises(TypeArenaSchemaError, match="TypeId/content mismatch"):
        FrozenTypeArena({TypeId("f" * 64): arena.resolve(text)})

    with pytest.raises(UnknownTypeIdError, match="unknown argument"):
        FrozenTypeArena({root: arena.resolve(root)})

def test_arena_serialization_is_insertion_order_independent() -> None:
    left = TypeArena()
    right = TypeArena()
    spellings = (
        "Map[Text,UInt64]",
        "Option[Box[app.User]]",
        "Result[Vec[Text],app.Error]",
        "Array[UInt64,8]",
    )

    left.intern_many(spellings)
    right.intern_many(reversed(spellings))

    assert left.to_json() == right.to_json()
    assert left.digest == right.digest


def test_json_roundtrip_preserves_ids_and_canonical_forms() -> None:
    arena = TypeArena()
    expected = {
        arena.intern_text("Text"): "Text",
        arena.intern_text("Vec[Text]"): "Vec[Text]",
        arena.intern_text("Result[Vec[Text],app.Error]"): (
            "Result[Vec[Text],app.Error]"
        ),
    }

    restored = TypeArena.from_json(arena.to_json())

    assert restored.digest == arena.digest
    assert restored.to_json() == arena.to_json()
    assert {type_id: restored.canonical(type_id) for type_id in expected} == expected


def test_serialized_contract_and_schema_are_closed() -> None:
    arena = TypeArena()
    arena.intern_text("Text")
    payload = arena.to_dict()

    assert payload["contract"] == TYPE_ARENA_CONTRACT
    assert payload["schema_version"] == TYPE_ARENA_SCHEMA_VERSION

    wrong_contract = copy.deepcopy(payload)
    wrong_contract["contract"] = "merlo.type-arena.v999"
    with pytest.raises(TypeArenaSchemaError, match="contract mismatch"):
        TypeArena.from_dict(wrong_contract)

    extra_key = copy.deepcopy(payload)
    extra_key["unknown"] = True
    with pytest.raises(TypeArenaSchemaError, match="invalid TypeArena"):
        TypeArena.from_dict(extra_key)

    bool_version = copy.deepcopy(payload)
    bool_version["schema_version"] = True
    with pytest.raises(TypeArenaSchemaError, match="schema version mismatch"):
        TypeArena.from_dict(bool_version)


def test_tampered_type_identity_is_rejected() -> None:
    arena = TypeArena()
    root = arena.intern_text("Vec[Text]")
    payload = copy.deepcopy(arena.to_dict())
    entry = next(
        item for item in payload["entries"] if item["id"]["value"] == root.value
    )
    entry["id"]["value"] = "0" * 64

    with pytest.raises(TypeArenaSchemaError, match="TypeId/content mismatch"):
        TypeArena.from_dict(payload)


def test_missing_argument_identity_is_rejected() -> None:
    arena = TypeArena()
    arena.intern_text("Vec[Text]")
    payload = copy.deepcopy(arena.to_dict())
    payload["entries"] = [
        entry
        for entry in payload["entries"]
        if entry["type"]["constructor"] != "Text"
    ]

    with pytest.raises(UnknownTypeIdError, match="unknown argument"):
        TypeArena.from_dict(payload)


def test_noncanonical_alias_in_serialized_arena_is_rejected() -> None:
    arena = TypeArena()
    canonical = arena.intern_text("Int64")
    payload = copy.deepcopy(arena.to_dict())
    entry = next(
        item for item in payload["entries"] if item["id"]["value"] == canonical.value
    )
    entry["type"]["constructor"] = "Int"

    with pytest.raises(TypeArenaSchemaError, match="noncanonical type alias"):
        TypeArena.from_dict(payload)


def test_unresolved_type_is_fail_closed_by_default() -> None:
    with pytest.raises(UnresolvedTypeError):
        TypeArena().intern_text("?")

    arena = TypeArena(allow_unresolved=True)
    unresolved = arena.intern_text("?")
    restored = TypeArena.from_json(arena.to_json())

    assert restored.canonical(unresolved) == "?"
    assert restored.allow_unresolved is True


@pytest.mark.parametrize("spelling", ("Map[Text,?]", "Map[?,Text]"))
def test_nested_unresolved_interning_is_atomic(spelling: str) -> None:
    arena = TypeArena()
    before = arena.to_json()

    with pytest.raises(UnresolvedTypeError):
        arena.intern_text(spelling)

    assert arena.to_json() == before
    assert len(arena) == 0


@pytest.mark.parametrize(
    "spelling",
    (
        "",
        "Result[Text]",
        "Option[]",
        "Vec[Text,UInt64]",
        "Array[Text]",
        "Fn[Text]",
        "Mystery[Text]",
        "Text trailing",
    ),
)
def test_invalid_type_spellings_are_rejected(spelling: str) -> None:
    with pytest.raises(TypeArenaError, match="invalid type expression"):
        TypeArena().intern_text(spelling)


@pytest.mark.parametrize("path", ("intern_node", "intern_expr", "intern_text"))
def test_invalid_creation_paths_leave_arena_unchanged(path: str) -> None:
    arena = TypeArena()
    text = arena.intern_text("Text")
    before = arena.to_json()

    with pytest.raises(TypeArenaError):
        if path == "intern_node":
            arena.intern_node("Array", (text,))
        elif path == "intern_expr":
            arena.intern_expr(TypeExpr("Array", (TypeExpr("Text"),)))
        else:
            arena.intern_text("Array[Text]")

    assert arena.to_json() == before


def test_public_type_ref_creation_uses_the_same_arity_authority() -> None:
    text = TypeArena().intern_text("Text")

    with pytest.raises(TypeArenaError, match="Array expects 2 arguments"):
        TypeRef("Array", (text,))
    with pytest.raises(TypeArenaError, match="unknown type constructor"):
        TypeRef("Mystery", (text,))


def test_decoded_type_ref_arity_is_a_schema_error() -> None:
    with pytest.raises(TypeArenaSchemaError, match="Array expects 2 arguments"):
        TypeRef.from_dict(
            {
                "contract": TYPE_REF_CONTRACT,
                "constructor": "Array",
                "arguments": [],
            }
        )


def test_tampered_constructor_arity_is_rejected_before_identity_checks() -> None:
    arena = TypeArena()
    arena.intern_text("Vec[Text]")
    payload = copy.deepcopy(arena.to_dict())
    root = next(item for item in payload["entries"] if item["type"]["constructor"] == "Vec")
    root["type"]["constructor"] = "Array"

    with pytest.raises(TypeArenaSchemaError, match="Array expects 2 arguments"):
        TypeArena.from_dict(payload)


@pytest.mark.parametrize(
    "spelling",
    ("Text", "Array[Text,4]", "Fn[Text,UInt64]", "Map[Text,Vec[UInt64]]"),
)
def test_every_successfully_serialized_shape_roundtrips(spelling: str) -> None:
    arena = TypeArena()
    type_id = arena.intern_text(spelling)
    restored = TypeArena.from_json(arena.to_json())

    assert restored.canonical(type_id) == arena.canonical(type_id)
    assert restored.to_json() == arena.to_json()


def test_unknown_type_identity_fails_closed() -> None:
    arena = TypeArena()
    unknown = TypeId("f" * 64)

    with pytest.raises(UnknownTypeIdError, match="unknown TypeId"):
        arena.resolve(unknown)
    with pytest.raises(UnknownTypeIdError, match="unknown TypeId"):
        arena.canonical(unknown)


def test_json_is_canonical_and_terminated() -> None:
    arena = TypeArena()
    arena.intern_many(("Vec[Text]", "Map[Text,UInt64]"))

    encoded = arena.to_json()

    assert encoded.endswith("\n")
    assert json.dumps(
        json.loads(encoded),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n" == encoded
