from __future__ import annotations

import merlo.type_arena as type_arena_module
import pytest

from merlo.type_arena import (
    TypeArenaError,
    TypeContextBuilder,
    TypeDeclaration,
    TypeId,
    TypeMember,
    UnknownTypeIdError,
)
from merlo.type_properties import TypePropertyResolver


def _frozen(spellings: tuple[str, ...], *, declarations=()):
    builder = TypeContextBuilder()
    builder.intern_many(spellings)
    for declaration in declarations:
        builder.register_declaration(declaration)
    return builder, builder.freeze()


def test_descriptor_properties_recurse_through_nested_type_refs() -> None:
    builder = TypeContextBuilder()
    builder.intern_many(
        (
            "Array[UInt64,4]",
            "Array[Text,4]",
            "Option[UInt64]",
            "Result[UInt64,Text]",
            "ScalarRecord",
            "OwnedRecord",
            "UInt64",
            "Option[Text]",
            "FileWriter",
            "Borrow[Text]",
        )
    )
    builder.register_declaration(
        TypeDeclaration(
            builder.intern_text("ScalarRecord"),
            "record",
            fields=(TypeMember("value", builder.intern_text("UInt64")),),
        )
    )
    builder.register_declaration(
        TypeDeclaration(
            builder.intern_text("OwnedRecord"),
            "record",
            fields=(TypeMember("value", builder.intern_text("Option[Text]")),),
        )
    )
    context = builder.freeze()
    resolver = TypePropertyResolver(context)

    array = context.type_id("Array[UInt64,4]")
    assert context.resolve(array).constructor == "Array"
    assert tuple(context.render(item) for item in context.resolve(array).arguments) == (
        "UInt64",
        "4",
    )
    assert resolver.resolve(array).is_copy
    assert not resolver.resolve(context.type_id("Array[Text,4]")).is_copy
    assert resolver.resolve(context.type_id("Array[Text,4]")).needs_drop
    assert not resolver.resolve(context.type_id("Option[UInt64]")).needs_drop
    assert resolver.resolve(context.type_id("Result[UInt64,Text]")).needs_drop
    assert resolver.resolve(context.type_id("ScalarRecord")).is_copy
    assert resolver.resolve(context.type_id("OwnedRecord")).needs_drop
    assert resolver.resolve(context.type_id("FileWriter")).is_resource
    borrow = resolver.resolve(context.type_id("Borrow[Text]"))
    assert borrow.contains_borrow
    assert borrow.to_dict(context)["borrow_types"] == ["Borrow[Text]"]


def test_owning_generics_recursively_propagate_contained_borrows() -> None:
    cases = {
        "Vec[TextView]": ("TextView",),
        "Box[BytesView]": ("BytesView",),
        "Map[Text,TextView]": ("TextView",),
        "Option[Vec[TextView]]": ("TextView",),
        "Result[Box[BytesView],Text]": ("BytesView",),
        "Vec[Option[Borrow[Text]]]": ("Borrow[Text]",),
        "Future[Vec[TextView]]": ("TextView",),
        "Shared[Option[BytesView]]": ("BytesView",),
    }
    builder, context = _frozen(tuple(cases))
    resolver = TypePropertyResolver(context)

    for spelling, borrow_types in cases.items():
        properties = resolver.resolve(builder.type_id(spelling))
        rendered = tuple(context.render(item) for item in properties.borrow_types)
        assert properties.contains_borrow, spelling
        assert rendered == borrow_types
        assert properties.is_move
        assert properties.needs_drop


def test_owning_generics_recursively_propagate_resources() -> None:
    spellings = (
        "Vec[FileReader]",
        "Box[Option[FileWriter]]",
        "Map[Text,FileReader]",
        "Future[UInt64]",
    )
    _, context = _frozen(spellings)
    resolver = TypePropertyResolver(context)

    assert resolver.resolve(context.type_id("Vec[FileReader]")).contains_resource
    assert resolver.resolve(context.type_id("Box[Option[FileWriter]]")).contains_resource
    assert resolver.resolve(context.type_id("Map[Text,FileReader]")).contains_resource
    future = resolver.resolve(context.type_id("Future[UInt64]"))
    assert future.is_resource
    assert future.contains_resource
    assert tuple(context.render(item) for item in future.resource_types) == (
        "Future[UInt64]",
    )
    assert future.to_dict(context)["resource_types"] == ["Future[UInt64]"]


def test_non_borrowing_owning_containers_keep_existing_properties() -> None:
    builder, context = _frozen(
        ("Vec[Text]", "Box[Text]", "Map[Text,UInt64]")
    )
    resolver = TypePropertyResolver(context)

    for type_name in ("Vec[Text]", "Box[Text]", "Map[Text,UInt64]"):
        properties = resolver.resolve(builder.type_id(type_name))
        assert properties.is_move
        assert properties.needs_drop
        assert not properties.contains_borrow


def test_recursive_record_and_enum_declarations_are_resolved_nominally() -> None:
    builder = TypeContextBuilder()
    node = builder.intern_text("Node")
    option_node = builder.intern_text("Option[Node]")
    tree = builder.intern_text("Tree")
    branch = builder.intern_text("Vec[Tree]")
    uint = builder.intern_text("UInt64")
    builder.register_declaration(
        TypeDeclaration(
            node,
            "record",
            fields=(TypeMember("next", option_node),),
        )
    )
    builder.register_declaration(
        TypeDeclaration(
            tree,
            "enum",
            variants=(
                TypeMember("leaf", uint),
                TypeMember("branch", branch),
            ),
        )
    )
    context = builder.freeze()
    resolver = TypePropertyResolver(context)

    node_properties = resolver.resolve(node)
    tree_properties = resolver.resolve(tree)
    assert node_properties.layout == "record"
    assert node_properties.is_move and node_properties.needs_drop
    assert not node_properties.contains_borrow
    assert tree_properties.layout == "enum"
    assert tree_properties.is_move and tree_properties.needs_drop
    assert not tree_properties.contains_borrow


def test_alias_convergence_and_qualified_nominal_lookup_use_one_context() -> None:
    builder = TypeContextBuilder()
    alias = builder.intern_text("Result[Vec[Int],Option[app.model.User]]")
    canonical = builder.intern_text("Result[Vec[Int64],Option[app.model.User]]")
    left = builder.intern_text("app.model.User")
    right = builder.intern_text("other.model.User")
    context = builder.freeze()
    assert alias == canonical
    assert left != right
    assert context.render(alias) == "Result[Vec[Int64],Option[app.model.User]]"
    assert context.render(left) == "app.model.User"
    assert context.render(right) == "other.model.User"
    assert TypePropertyResolver(context).resolve(alias) == TypePropertyResolver(context).resolve(canonical)


def test_malformed_unknown_and_foreign_type_ids_are_rejected() -> None:
    builder, context = _frozen(("Text",))
    resolver = TypePropertyResolver(context)

    with pytest.raises(TypeArenaError):
        TypeContextBuilder().intern_text("Vec[")
    with pytest.raises(TypeArenaError, match="requires TypeId"):
        resolver.resolve(object())
    with pytest.raises(UnknownTypeIdError):
        resolver.resolve(TypeId("f" * 64))

    foreign_builder = TypeContextBuilder()
    same_text = foreign_builder.intern_text("Text")
    foreign = foreign_builder.intern_text("Bytes")
    assert resolver.resolve(same_text) == resolver.resolve(builder.type_id("Text"))
    with pytest.raises(UnknownTypeIdError):
        resolver.resolve(foreign)

    with pytest.raises(TypeArenaError, match="requires TypeAuthority"):
        TypePropertyResolver(None)


def test_frozen_context_queries_do_not_parse_or_intern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder, context = _frozen(("Text", "Vec[TextView]"))
    text = builder.type_id("Text")
    values = builder.type_id("Vec[TextView]")

    def fail(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("post-freeze source-boundary operation")

    monkeypatch.setattr(type_arena_module, "parse_type", fail)
    monkeypatch.setattr(TypeContextBuilder, "intern_text", fail)
    resolver = TypePropertyResolver(context)

    assert resolver.resolve(text).needs_drop
    properties = resolver.resolve(values)
    assert properties.contains_borrow
    assert tuple(context.render(item) for item in properties.borrow_types) == ("TextView",)


def test_map_entry_is_a_borrowed_view_not_an_implicit_copy() -> None:
    builder, context = _frozen(("MapEntry[Text,Text]",))
    properties = TypePropertyResolver(context).resolve(
        builder.type_id("MapEntry[Text,Text]")
    )
    assert properties.contains_borrow
    assert properties.is_move is False
    assert properties.needs_drop is False
    assert tuple(context.render(item) for item in properties.borrow_types) == (
        "MapEntry[Text,Text]",
    )
