from __future__ import annotations

import pytest

from merlo.concise_application import lower_concise_sum_types
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


def test_nested_sum_types_lower_to_stable_nominal_names() -> None:
    lowered = lower_concise_sum_types(
        "fn main(value: Result[ Option[Text], AppError ]) -> UInt64:\n"
        "    match value:\n"
        "        case Ok(item):\n"
        "            return 0\n"
        "        case Err(error):\n"
        "            return 1\n"
    )
    assert "enum Result_Option_Text__AppError_:" in lowered
    assert "enum Option_Text_:" in lowered
    assert "Ok: Option[Text]" in lowered


def test_structured_hir_accepts_nested_map_vec_types() -> None:
    hir = compile_structured_hir(
        "fn main(value: Map[Text, Vec[Option[Text]]]) -> Unit:\n"
        "    return\n",
        path="nested-types.mlo",
    )
    assert hir.function("main").parameters[0].type_name == "Map[Text,Vec[Option[Text]]]"
