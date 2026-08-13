from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from merlo.canonical_ast import CanonicalFunction, CanonicalProgram, CanonicalReturn
from merlo.surface_ast import (
    SourceSpan,
    SurfaceBinary,
    SurfaceFunction,
    SurfaceImplicitReceiver,
    SurfaceName,
)
from merlo.surface_parser import SurfaceSyntaxError, parse_surface


@pytest.mark.parametrize(
    ("source", "name", "body_kind"),
    [
        ("double(x) = x * 2\n", "double", "expression"),
        (
            "total(values):\n"
            "    result = 0\n"
            "    for value in values:\n"
            "        result += value\n"
            "    result\n",
            "total",
            "block",
        ),
        ("export normalize(user) = user.name\n", "normalize", "expression"),
    ],
)
def test_parser_accepts_inferred_functions_with_exact_spans(
    source: str, name: str, body_kind: str
) -> None:
    program = parse_surface(source, path="sample.mlo")
    function = program.declarations[0]

    assert isinstance(function, SurfaceFunction)
    assert function.name == name
    assert function.body_kind == body_kind
    assert function.span == SourceSpan(
        "sample.mlo", 1, 1, len(source.splitlines()), len(source.splitlines()[-1]) + 1
    )


def test_parser_recognizes_records_optional_types_and_tail_expressions() -> None:
    program = parse_surface(
        "User:\n"
        "    name: Text\n"
        "    nickname: Text?\n"
        "    active: Bool\n\n"
        "display_name(user) = user.nickname or user.name\n"
    )

    record, function = program.declarations
    assert record.name == "User"
    assert [(field.name, field.type_name) for field in record.fields] == [
        ("name", "Text"),
        ("nickname", "Option[Text]"),
        ("active", "Bool"),
    ]
    assert isinstance(function.body, SurfaceBinary)
    assert function.body.operator == "or"


def test_parser_limits_implicit_receiver_to_callable_argument_syntax() -> None:
    program = parse_surface(
        'count_errors(events) = events.count(.level == "error")\n'
    )
    function = program.declarations[0]
    implicit = [
        node for node in function.body.walk() if isinstance(node, SurfaceImplicitReceiver)
    ]

    assert [item.field for item in implicit] == ["level"]
    with pytest.raises(SurfaceSyntaxError, match="ImplicitReceiverOutsideCallable"):
        parse_surface("field() = .name\n")
    with pytest.raises(SurfaceSyntaxError, match="NestedImplicitReceiverForbidden"):
        parse_surface("names(users) = users.map(.friends.map(.name))\n")


def test_surface_nodes_are_frozen_and_canonical_hash_is_semantic() -> None:
    span = SourceSpan("main.mlo", 1, 1, 1, 18)
    function = SurfaceFunction(
        "double",
        (),
        SurfaceName("x", span),
        "expression",
        False,
        span,
    )
    with pytest.raises(FrozenInstanceError):
        function.name = "changed"  # type: ignore[misc]

    left = CanonicalProgram(
        (),
        (CanonicalFunction("double", (("x", "UInt64"),), "UInt64", "fn", (), (), (), (CanonicalReturn("x * 2", span),), span),),
    )
    right = CanonicalProgram(
        (),
        (CanonicalFunction("double", (("x", "UInt64"),), "UInt64", "fn", (), (), (), (CanonicalReturn("x * 3", span),), span),),
    )
    assert left.semantic_hash != right.semantic_hash
    assert CanonicalProgram.from_payload(left.to_payload()).semantic_hash == left.semantic_hash


def test_parser_has_no_semantic_layer_imports() -> None:
    tree = ast.parse(Path("merlo/surface_parser.py").read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert not imports & {
        "surface_elaborator",
        "canonical_ast",
        "structured_hir_v2",
        "representation_ir",
        "representation_mir",
    }


def test_parser_rejects_tabs_and_inconsistent_indentation() -> None:
    with pytest.raises(SurfaceSyntaxError, match="TabIndentationForbidden"):
        parse_surface("value():\n\t1\n")
    with pytest.raises(SurfaceSyntaxError, match="InvalidIndentation"):
        parse_surface("value():\n   1\n")
