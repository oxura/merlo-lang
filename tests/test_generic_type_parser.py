from __future__ import annotations

import pytest

from merlo.surface_ast import SurfaceFunction
from merlo.surface_parser import parse_surface
from merlo.structured_hir_v2 import compile_structured_hir
from merlo.type_parser import GenericTypeSyntaxError, generic_arguments, parse_type


def test_parse_type_recursively_normalizes_nested_arguments() -> None:
    parsed = parse_type(" Result [ Option [ Text ] , Map [ Text , Vec [ UInt64 ] ] ] ")
    assert parsed.name == "Result"
    assert parsed.canonical == "Result[Option[Text],Map[Text,Vec[UInt64]]]"
    assert generic_arguments(parsed) == ("Option[Text]", "Map[Text,Vec[UInt64]]")


def test_parse_type_rejects_unbalanced_and_empty_input() -> None:
    for malformed in (
        "Result[Option[Text],AppError",
        "Option[Result[Vec[Text],AppError]]junk",
        "Map[Text,]",
        "Option[]",
    ):
        with pytest.raises(GenericTypeSyntaxError):
            parse_type(malformed)


def test_generic_arguments_preserve_wrong_arity_for_caller_validation() -> None:
    parsed = parse_type("Result[Text,AppError,Extra]")
    assert generic_arguments(parsed) == ("Text", "AppError", "Extra")


def test_nested_sum_types_remain_structural_type_terms() -> None:
    program = parse_surface(
        "fn main(value: Result[ Option[Text], AppError ]) -> UInt64:\n"
        "    match value:\n"
        "        case Ok(item):\n"
        "            return 0\n"
        "        case Err(error):\n"
        "            return 1\n"
    )

    function = program.declarations[0]
    assert isinstance(function, SurfaceFunction)
    assert function.parameters[0].type_name == "Result[Option[Text],AppError]"
    assert function.return_type == "UInt64"


def test_parse_type_accepts_inferred_type_sentinel() -> None:
    assert parse_type("?").canonical == "?"


def test_structured_hir_accepts_nested_sum_vec_types() -> None:
    hir = compile_structured_hir(
        "fn main(value: Vec[Option[Text]]) -> Unit:\n"
        "    return\n",
        path="nested-types.mlo",
    )
    assert hir.function("main").parameters[0].type_name == "Vec[Option[Text]]"
