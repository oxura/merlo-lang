from __future__ import annotations

from pathlib import Path

import pytest

from merlo.surface_elaborator import SurfaceElaborationError, elaborate_surface
from merlo.surface_parser import parse_surface

from merlo.formatter import expand_source, explain_source

def elaborate(source: str):
    return elaborate_surface(parse_surface(source, path="sample.mlo"))


def test_typed_collection_shorthand_expands_to_explicit_callables() -> None:
    result = elaborate(
        "User:\n"
        "    name: Text\n"
        "    active: Bool\n\n"
        "active_names(users) = users.where(.active).map(.name)\n"
    )

    function = result.canonical.function("active_names")
    assert function.parameters == (("users", "Vec[User]"),)
    assert function.return_type == "Vec[Text]"
    assert [call.parameter_type for call in function.implicit_callables] == [
        "User",
        "User",
    ]
    assert [call.return_type for call in function.implicit_callables] == [
        "Bool",
        "Text",
    ]
    assert len({call.callable_id for call in function.implicit_callables}) == 2


def test_count_shorthand_returns_uint64_without_output_collection() -> None:
    result = elaborate(
        "Event:\n"
        "    level: Text\n\n"
        "count_errors(events) = events.count(.level == \"error\")\n"
    )

    function = result.canonical.function("count_errors")
    assert function.parameters == (("events", "Vec[Event]"),)
    assert function.return_type == "UInt64"
    assert function.implicit_callables[0].return_type == "Bool"


def test_option_or_is_strict_fallback_while_bool_or_remains_boolean() -> None:
    result = elaborate(
        "User:\n"
        "    name: Text\n"
        "    nickname: Text?\n\n"
        "display_name(user) = user.nickname or user.name\n\n"
        "either(left: Bool, right: Bool) = left or right\n"
    )

    display = result.canonical.function("display_name")
    assert display.parameters == (("user", "User"),)
    assert display.return_type == "Text"
    assert len(display.option_fallbacks) == 1
    assert display.option_fallbacks[0].type_name == "Text"
    assert result.canonical.function("either").option_fallbacks == ()


@pytest.mark.parametrize(
    "source",
    [
        'bad(left: Text, right: Text) = left or right\n',
        'bad(left: UInt64, right: UInt64) = left or right\n',
        'bad(left: Vec[Text], right: Vec[Text]) = left or right\n',
    ],
)
def test_or_rejects_truthiness_for_non_bool_non_option(source: str) -> None:
    with pytest.raises(SurfaceElaborationError, match="TruthinessForbidden"):
        elaborate(source)


def test_expand_materializes_inferred_machine_facts() -> None:
    expanded = expand_source(
        "double(x: UInt64) = x * 2\n",
        path="math.mlo",
    )

    assert expanded == (
        "fn double(x: UInt64) -> UInt64:\n"
        "    return x * 2\n"
    )


def test_explain_reports_inferred_task_error_and_authority() -> None:
    explanation = explain_source(
        "load(path: Path) = fs.read_text(path)?\n",
        path="io.mlo",
    )

    assert "kind: task" in explanation
    assert "effects: fs.read" in explanation
    assert "capabilities: fs.read" in explanation
    assert "errors: FileError" in explanation


def test_project_compilation_uses_surface_tree_without_canonical_reparse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from merlo.compiler import compile_project

    source = tmp_path / "main.mlo"
    source.write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    ReadFailure: Text\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    data = fs.read(path)?\n"
        "    result = data.to_text()\n"
        "    print result\n"
        "    Ok(result)\n",
        encoding="utf-8",
    )

    def reject_text_reparse(*args: object, **kwargs: object) -> None:
        raise AssertionError("production compiler reparsed canonical text")

    monkeypatch.setattr(
        "merlo.compiler.compile_structured_hir",
        reject_text_reparse,
    )
    compilation = compile_project(
        source,
        require_interface_lock=False,
    )

    assert compilation.elaborated.canonical_program.function("main").kind == "task"
    assert compilation.hir.function("main").return_type == "Result[Text,AppError]"
    assert compilation.optimized_mir.functions


def test_unit_tail_expression_runs_before_native_return(
    tmp_path: Path,
) -> None:
    import subprocess

    from merlo.compiler import compile_project

    source = tmp_path / "main.mlo"
    source.write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "append_tail(output: TextBuilder) -> Unit:\n"
        "    output.append_text(\"tail\")\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    output = TextBuilder.new()\n"
        "    append_tail(output)\n"
        "    result = output.finish()\n"
        "    print result\n"
        "    Ok(result)\n",
        encoding="utf-8",
    )
    compilation = compile_project(
        source,
        emit_native=True,
        output=tmp_path / "app",
        require_interface_lock=False,
    )

    completed = subprocess.run(
        [str(compilation.native.binary_path), "unused"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == "tail\n"



def test_surface_control_and_named_calls_reach_hir_and_c_lowering() -> None:
    from merlo.representation_c_backend import emit_general_c
    from merlo.representation_ir import lower_structured_hir_to_rir
    from merlo.representation_mir import lower_rir_to_performance_mir
    from merlo.structured_hir_v2 import compile_structured_hir

    result = elaborate(
        "subtract(left: Int64, right: Int64) = left - right\n\n"
        "main():\n"
        "    value = 0\n"
        "    while value < 4:\n"
        "        value += 1\n"
        "        if value == 2:\n"
        "            continue\n"
        "        if value == 3:\n"
        "            break\n"
        "    subtract(right: value, left: 10)\n"
    )
    canonical_source = result.canonical.to_source()
    hir = compile_structured_hir(canonical_source, path="surface.mlo")
    kinds = {node.kind for node in hir.function("main").walk()}
    assert {"Break", "Continue"} <= kinds

    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    generated = emit_general_c(hir, representation, mir)
    assert "continue;" in generated.source
    assert "break;" in generated.source
    assert "merlo_fn_subtract(10, value)" in generated.source