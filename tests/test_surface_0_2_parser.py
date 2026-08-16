from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import merlo.surface_parser as surface_parser_module
from merlo.canonical_ast import CanonicalFunction, CanonicalProgram, CanonicalReturn
from merlo.frontend.file_syntax import parse_file_cst
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


def test_surface_declarations_require_and_dispatch_through_cst_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "value() = 1\n"
    cst = parse_file_cst(source, path="anchored.mlo")
    without_anchor = replace(cst, declarations=())
    monkeypatch.setattr(
        surface_parser_module,
        "parse_file_cst",
        lambda _source, *, path: without_anchor,
    )
    with pytest.raises(SurfaceSyntaxError, match="CSTDeclarationMismatch"):
        parse_surface(source, path="anchored.mlo")

    wrong_kind = replace(cst.declarations[0], kind="enum")
    wrong_dispatch = replace(cst, declarations=(wrong_kind,))
    monkeypatch.setattr(
        surface_parser_module,
        "parse_file_cst",
        lambda _source, *, path: wrong_dispatch,
    )
    with pytest.raises(SurfaceSyntaxError, match="ExpectedDeclaration"):
        parse_surface(source, path="anchored.mlo")


def test_cst_anchor_lines_compose_with_module_body_offsets() -> None:
    program = parse_surface(
        "value():\n"
        "    return 1\n",
        path="offset.mlo",
        line_offset=9,
    )

    assert program.declarations[0].span.start_line == 10


def test_surface_statement_blocks_fail_closed_on_cst_disagreement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "value():\n    return 1\n"
    cst = parse_file_cst(source, path="statement-anchor.mlo")
    declaration = cst.declarations[0]
    header, block = declaration.children
    statement = block.children[0]

    missing_block = replace(block, children=())
    missing_declaration = replace(
        declaration,
        children=(header, missing_block),
    )
    missing = replace(cst, declarations=(missing_declaration,))
    monkeypatch.setattr(
        surface_parser_module,
        "parse_file_cst",
        lambda _source, *, path: missing,
    )
    with pytest.raises(SurfaceSyntaxError, match="CSTStatementMismatch"):
        parse_surface(source, path="statement-anchor.mlo")

    wrong_statement = replace(statement, kind="expression_statement")
    wrong_block = replace(block, children=(wrong_statement,))
    wrong_declaration = replace(
        declaration,
        children=(header, wrong_block),
    )
    wrong = replace(cst, declarations=(wrong_declaration,))
    monkeypatch.setattr(
        surface_parser_module,
        "parse_file_cst",
        lambda _source, *, path: wrong,
    )
    with pytest.raises(SurfaceSyntaxError, match="CSTStatementMismatch"):
        parse_surface(source, path="statement-anchor.mlo")


def test_surface_statement_expressions_consume_cst_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "value():\n    return 1 + 2\n"

    monkeypatch.setattr(
        surface_parser_module,
        "lex_expression",
        lambda _source: (_ for _ in ()).throw(AssertionError("re-lexed")),
    )

    program = parse_surface(source, path="expression-region.mlo")
    assert program.declarations[0].body[0].expression is not None


def test_surface_statement_expressions_fail_closed_on_cst_disagreement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "value():\n    return 1 + 2\n"
    cst = parse_file_cst(source, path="expression-region.mlo")
    declaration = cst.declarations[0]
    declaration_header, block = declaration.children
    statement = block.children[0]
    statement_header = statement.children[0]
    missing_expression_header = replace(statement_header, children=())
    missing_statement = replace(
        statement,
        children=(missing_expression_header,),
    )
    missing_block = replace(block, children=(missing_statement,))
    missing_declaration = replace(
        declaration,
        children=(declaration_header, missing_block),
    )
    missing = replace(cst, declarations=(missing_declaration,))
    monkeypatch.setattr(
        surface_parser_module,
        "parse_file_cst",
        lambda _source, *, path: missing,
    )

    with pytest.raises(SurfaceSyntaxError, match="CSTExpressionMismatch"):
        parse_surface(source, path="expression-region.mlo")


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
        (CanonicalFunction("double", (("x", "UInt64"),), "UInt64", "fn", (), (), (), (), (), (CanonicalReturn("x * 2", span),), span),),
    )
    right = CanonicalProgram(
        (),
        (CanonicalFunction("double", (("x", "UInt64"),), "UInt64", "fn", (), (), (), (), (), (CanonicalReturn("x * 3", span),), span),),
    )
    assert left.semantic_hash != right.semantic_hash
    assert CanonicalProgram.from_payload(left.to_payload()).semantic_hash == left.semantic_hash


def test_parser_has_no_semantic_layer_imports() -> None:
    tree = ast.parse(Path("src/merlo/surface_parser.py").read_text(encoding="utf-8"))
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



def test_parser_reports_positional_argument_after_keyword_stably() -> None:
    with pytest.raises(SurfaceSyntaxError, match="PositionalAfterKeyword"):
        parse_surface("invoke(value) = target(value=1, 2)\n")
