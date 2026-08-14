from __future__ import annotations

import pytest

from merlo.surface_elaborator import SurfaceElaborationError, elaborate_surface
from merlo.surface_parser import parse_surface


def elaborate(source: str):
    return elaborate_surface(parse_surface(source, path="sample.mlo"))


def test_calls_and_tail_expression_infer_private_signature_and_bindings() -> None:
    result = elaborate(
        "Point:\n"
        "    x: Float64\n"
        "    y: Float64\n\n"
        "use_point(point: Point) = distance(point, point)\n\n"
        "distance(a, b):\n"
        "    dx = a.x - b.x\n"
        "    dy = a.y - b.y\n"
        "    sqrt(dx * dx + dy * dy)\n"
    )
    distance = result.canonical.function("distance")

    assert distance.parameters == (("a", "Point"), ("b", "Point"))
    assert distance.return_type == "Float64"
    assert [(item.name, item.type_name, item.mutable) for item in distance.bindings] == [
        ("dx", "Float64", False),
        ("dy", "Float64", False),
    ]


def test_assignment_count_selects_var_without_source_keyword() -> None:
    result = elaborate(
        "run() = count(2)\n\n"
        "count(n):\n"
        "    total = 0\n"
        "    total += n\n"
        "    total\n"
    )

    total = result.canonical.function("count").binding("total")
    assert total.type_name == "UInt64"
    assert total.mutable is True
    assert "var total: UInt64 = 0" in result.canonical.to_source()


def test_ambiguous_identity_is_rejected_without_dynamic_fallback() -> None:
    with pytest.raises(SurfaceElaborationError, match="AmbiguousType: identity.x"):
        elaborate("identity(x) = x\n")


def test_explicit_boundary_constrains_identity() -> None:
    result = elaborate("identity(x: Text) = x\n")

    identity = result.canonical.function("identity")
    assert identity.parameters == (("x", "Text"),)
    assert identity.return_type == "Text"


def test_record_construction_named_fields_constrain_arguments() -> None:
    result = elaborate(
        "User:\n"
        "    name: Text\n"
        "    age: UInt64\n\n"
        "make(name) = User(name: name, age: 24)\n"
    )

    make = result.canonical.function("make")
    assert make.parameters == (("name", "Text"),)
    assert make.return_type == "User"



def test_private_call_graph_infers_task_effects_and_closed_errors() -> None:
    result = elaborate(
        "parse(text: Text) -> Result[Text,ParseError] = Ok(text)\n\n"
        "load(path: Path):\n"
        "    text = fs.read_text(path)?\n"
        "    parse(text)?\n\n"
        "main(path: Path) = load(path)?\n"
    )

    load = result.canonical.function("load")
    main = result.canonical.function("main")
    assert load.kind == main.kind == "task"
    assert load.effects == main.effects == ("fs.read",)
    assert load.capabilities == main.capabilities == ("fs.read",)
    assert load.error_types == ("FileError", "ParseError")
    assert main.error_types == ("FileError", "ParseError")


def test_print_infers_console_authority_and_pure_function_stays_fn() -> None:
    result = elaborate(
        "square(x: UInt64) = x * x\n\n"
        "show(x: UInt64):\n"
        "    print x\n"
        "    x\n"
    )

    assert result.canonical.function("square").kind == "fn"
    show = result.canonical.function("show")
    assert show.kind == "task"
    assert show.effects == ("console.write",)
    assert show.capabilities == ("console.write",)


def test_postfix_try_requires_result() -> None:
    with pytest.raises(SurfaceElaborationError, match="TryRequiresResult"):
        elaborate("bad() = 1?\n")


def test_recursive_effect_cycle_converges() -> None:
    result = elaborate(
        "left(value: UInt64) -> UInt64 = right(value)\n\n"
        "right(value: UInt64) -> UInt64:\n"
        "    print value\n"
        "    left(value)\n"
    )

    for name in ("left", "right"):
        function = result.canonical.function(name)
        assert function.kind == "task"
        assert function.effects == ("console.write",)
        assert function.capabilities == ("console.write",)


def test_named_call_arguments_bind_by_parameter_name_independent_of_order() -> None:
    result = elaborate(
        "subtract(left: Int64, right: Int64) = left - right\n\n"
        "run() = subtract(right: 2, left: 5)\n"
    )

    assert result.canonical.function("run").return_type == "Int64"
    assert "subtract(right: 2, left: 5)" in result.canonical.to_source()


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    [
        (
            "subtract(left: Int64, right: Int64) = left - right\n\n"
            "run() = subtract(middle: 2, left: 5)\n",
            "UnknownArgument",
        ),
        (
            "subtract(left: Int64, right: Int64) = left - right\n\n"
            "run() = subtract(left: 5, left: 2)\n",
            "DuplicateArgument",
        ),
        (
            "subtract(left: Int64, right: Int64) = left - right\n\n"
            "run() = subtract(left: 5)\n",
            "MissingArgument",
        ),
    ],
)
def test_named_call_arguments_reject_invalid_bindings(
    source: str, diagnostic: str
) -> None:
    with pytest.raises(SurfaceElaborationError, match=diagnostic):
        elaborate(source)


def test_loop_control_is_validated_and_preserved_in_canonical_source() -> None:
    result = elaborate(
        "run() = 0\n\n"
        "main():\n"
        "    value = 0\n"
        "    while value < 4:\n"
        "        value += 1\n"
        "        if value == 2:\n"
        "            continue\n"
        "        if value == 3:\n"
        "            break\n"
        "    value\n"
    )

    source = result.canonical.to_source()
    assert "            continue" in source
    assert "            break" in source

    with pytest.raises(SurfaceElaborationError, match="BreakOutsideLoop"):
        elaborate("bad():\n    break\n")
    with pytest.raises(SurfaceElaborationError, match="ContinueOutsideLoop"):
        elaborate("bad():\n    continue\n")