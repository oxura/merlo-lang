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


def test_surface_handoff_retains_tree_and_preserves_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from merlo.structured_hir_v2 import compile_canonical_hir

    source = (
        "User:\n"
        "    name: Text\n"
        "    active: Bool\n\n"
        "active_names(users: Vec[User]) -> Vec[Text] = "
        "users.where(.active).map(.name)\n"
    )
    result = elaborate(source)

    def reject_serialization(_program: object) -> str:
        raise AssertionError("Surface handoff serialized canonical text")

    monkeypatch.setattr(
        "merlo.canonical_ast.CanonicalProgram.to_source",
        reject_serialization,
    )
    hir = compile_canonical_hir(result.canonical, entry_function="active_names")
    assert result.canonical.surface_program is not None
    assert not hasattr(hir, "native_module")
    assert not hasattr(hir, "native_syntax_json")
    returned = hir.function("active_names").body[0]
    operation = returned.children[0]
    assert operation.kind == "CollectionOperation"
    assert operation.attribute_map["collection_kind"] == "vec"
    assert operation.type_name == "Vec[Text]"
    assert hir.source == source
    assert hir.source_sha256 == __import__("hashlib").sha256(source.encode()).hexdigest()
    assert hir.function("active_names").parameters[0].source.column == 14


def test_serialized_canonical_projection_is_not_compiler_input() -> None:
    from merlo.canonical_ast import CanonicalProgram
    from merlo.structured_hir_v2 import (
        StructuredHIRCompileError,
        compile_canonical_hir,
    )

    result = elaborate("main() -> Unit = Unit\n")
    serialized = CanonicalProgram.from_payload(result.canonical.to_payload())

    with pytest.raises(
        StructuredHIRCompileError,
        match="CanonicalSurfaceRequired",
    ):
        compile_canonical_hir(serialized)

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
        "merlo.structured_hir_v2.compile_structured_hir",
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


def test_bytes_literal_survives_project_artifacts_and_native_roundtrip(
    tmp_path: Path,
) -> None:
    import json
    import subprocess

    from merlo.compiler import compile_project

    source = tmp_path / "main.mlo"
    output = tmp_path / "roundtrip.bin"
    source.write_text(
        "module main\n\n"
        "export enum AppError:\n"
        "    FileFailure\n"
        "    InvalidUtf8\n\n"
        "export main(path: Path) -> Result[Text, AppError]:\n"
        "    writer = fs.open_write(path)?\n"
        '    payload = b"bytes-roundtrip\\n"\n'
        "    fs.write_chunk(writer, payload.view())?\n"
        "    fs.close_write(writer)?\n"
        "    reader = fs.open_read(path)?\n"
        "    stored = fs.read_chunk(reader, 4096)?\n"
        "    fs.close_read(reader)?\n"
        "    message = stored.to_text()\n"
        "    console.write(message)\n"
        "    Ok(message)\n",
        encoding="utf-8",
    )

    compilation = compile_project(
        source,
        emit_native=True,
        output=tmp_path / "app",
        require_interface_lock=False,
    )
    literal = next(
        node
        for node in compilation.hir.function("main").walk()
        if node.kind == "Literal" and node.type_name == "Bytes"
    )
    assert literal.attribute_map == {
        "literal_encoding": "bytes",
        "value": list(b"bytes-roundtrip\n"),
    }
    json.loads(compilation.hir.to_json())
    assert "merlo_bytes_literal(" in compilation.generated_c

    completed = subprocess.run(
        [str(compilation.native.binary_path), str(output)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "bytes-roundtrip\n"
    assert completed.stderr == ""
    assert output.read_bytes() == b"bytes-roundtrip\n"



def test_surface_controls_reach_c_through_production_project_handoff(
    tmp_path: Path,
) -> None:
    from merlo.compiler import compile_project

    source = tmp_path / "main.mlo"
    source.write_text(
        "module main\n\n"
        "enum AppError:\n"
        "    Failed\n\n"
        "subtract(left: UInt64, right: UInt64) -> UInt64 = left - right\n\n"
        "export main(path: Path) -> Result[UInt64, AppError]:\n"
        "    uses console.write\n"
        "    value = 0\n"
        "    while value < 4:\n"
        "        value += 1\n"
        "        if value == 2:\n"
        "            continue\n"
        "        if value == 3:\n"
        "            break\n"
        "    result = subtract(value, 10)\n"
        "    console.write(\"control\")\n"
        "    Ok(result)\n",
        encoding="utf-8",
    )

    compilation = compile_project(
        source,
        require_interface_lock=False,
    )
    kinds = {node.kind for node in compilation.hir.function("main").walk()}
    assert {"Break", "Continue"} <= kinds
    assert "continue;" in compilation.generated_c
    assert "break;" in compilation.generated_c


def test_structured_hir_constructor_and_option_none_preprocessing() -> None:
    from merlo.structured_hir_v2 import _preprocess, compile_structured_hir

    hir = compile_structured_hir(
        "fn main() -> UInt64:\n"
        "    1\n",
        path="constructor.mlo",
    )
    assert hir.function("main").return_type == "UInt64"
    assert "Option.NoneValue" in _preprocess(
        "fn main() -> UInt64:\n"
        "    Option.None\n"
    ).source


def test_break_in_match_inside_loop_targets_loop_exit_in_c() -> None:
    from merlo.representation_c_backend import emit_general_c
    from merlo.representation_ir import lower_structured_hir_to_rir
    from merlo.representation_mir import lower_rir_to_performance_mir
    from merlo.structured_hir_v2 import compile_structured_hir

    hir = compile_structured_hir(
        "enum Signal:\n"
        "    Stop\n"
        "    Keep\n\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    value = 0\n"
        "    signal: Signal = Signal.Stop\n"
        "    while value < 4:\n"
        "        match signal:\n"
        "            case Signal.Stop:\n"
        "                break\n"
        "            case Signal.Keep:\n"
        "                value += 1\n"
        "        value += 1\n"
        "    value\n",
        path="nested-break.mlo",
    )
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    generated = emit_general_c(hir, representation, mir)
    assert "switch" in generated.source
    assert "goto __merlo_loop_exit_" in generated.source


def test_structured_hir_keeps_postfix_try_rewrite_after_option_none() -> None:
    from merlo.structured_hir_v2 import _preprocess

    source = (
        "fn main() -> UInt64:\n"
        "    Option.None\n"
        "    value?\n"
    )
    preprocessed = _preprocess(source).source
    assert "Option.NoneValue" in preprocessed
    assert "__merlo_try__(value)" in preprocessed
