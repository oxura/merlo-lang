from __future__ import annotations

import subprocess

import pytest

from merlo.native_c_backend import compile_c_source, find_c_compiler
from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import lower_rir_to_performance_mir
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface
from merlo.structured_hir_v2 import (
    StructuredHIRCompileError,
    compile_canonical_hir,
    compile_structured_hir,
)


_USER_HEADER = (
    "record User:\n"
    "    name: Text\n"
    "    age: UInt64\n"
)


def _compile(source: str) -> None:
    compile_structured_hir(source)


def _user_main(operation: str, tail: str = "    return 0\n") -> str:
    return (
        _USER_HEADER
        + "fn main(input: BytesView) -> UInt64:\n"
        + "    let user: User = User(Text.from_bytes(input, 0, input.len()), 0)\n"
        + f"    {operation}\n"
        + tail
    )


@pytest.mark.parametrize(
    "source",
    [
        _user_main(
            "let name: Text = user.name",
            "    drop(user)\n"
            "    return name.len()\n",
        ),
        (
            "record Inner:\n"
            "    text: Text\n"
            "record Outer:\n"
            "    inner: Inner\n"
            "fn main(input: BytesView) -> UInt64:\n"
            "    let inner: Inner = Inner(Text.from_bytes(input, 0, input.len()))\n"
            "    let outer: Outer = Outer(inner)\n"
            "    let text: Text = outer.inner.text\n"
            "    drop(outer)\n"
            "    return text.len()\n"
        ),
        (
            "fn main(input: BytesView) -> UInt64:\n"
            "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
            "    let boxed: Box[Text] = Box.new(text)\n"
            "    let moved: Text = boxed.get()\n"
            "    drop(boxed)\n"
            "    return moved.len()\n"
        ),
        (
            "fn main(input: BytesView) -> UInt64:\n"
            "    let values: Array[Text,1] = [Text.from_bytes(input, 0, input.len())]\n"
            "    let moved: Text = values[0]\n"
            "    drop(values)\n"
            "    return moved.len()\n"
        ),
        _user_main(
            "if input.len() > 0:\n"
            "        let name: Text = user.name\n"
            "        drop(name)",
            "    drop(user)\n"
            "    return 0\n",
        ),
    ],
)
def test_projected_owner_moves_are_accepted(source: str) -> None:
    _compile(source)


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    [
        (
            _user_main(
                "let name: Text = user.name",
                "    return user.name.len()\n",
            ),
            "UseAfterMove: user.name",
        ),
        (
            _user_main(
                "let name: Text = user.name",
                "    let moved: User = user\n"
                "    return name.len() + moved.age\n",
            ),
            "PartiallyMovedValue: user",
        ),
        (
            _user_main(
                "if input.len() > 0:\n"
                "        let name: Text = user.name",
                "    return user.name.len()\n",
            ),
            "MaybeUninitialized: user.name",
        ),
    ],
)
def test_invalid_projected_owner_uses_are_rejected(
    source: str,
    diagnostic: str,
) -> None:
    with pytest.raises(StructuredHIRCompileError, match=f"^{diagnostic}$"):
        _compile(source)


def test_moved_field_can_be_reinitialized() -> None:
    _compile(
        _user_main(
            "let name: Text = user.name\n"
            "    user.name = Text.from_bytes(input, 0, input.len())",
            "    return name.len() + user.name.len()\n",
        )
    )


def test_borrow_disjoint_field_remains_accepted() -> None:
    _compile(
        _user_main(
            "let view: TextView = user.name.as_view()\n"
            "    user.age = 1",
            "    return view.len()\n",
        )
    )


def test_scalar_field_move_remains_accepted() -> None:
    _compile(
        _user_main(
            "let age: UInt64 = user.age",
            "    drop(user)\n"
            "    return age\n",
        )
    )


def test_borrow_drop_then_field_mutation_remains_accepted() -> None:
    _compile(
        _user_main(
            "let view: TextView = user.name.as_view()\n"
            "    drop(view)\n"
            "    user.name = Text.from_bytes(input, 0, input.len())",
        )
    )


def test_whole_owner_move_without_borrow_remains_accepted() -> None:
    _compile(
        _user_main(
            "let moved: User = user",
            "    return moved.age\n",
        )
    )


def test_independent_record_field_borrows_remain_accepted() -> None:
    _compile(
        "record User:\n"
        "    name: Text\n"
        "    other: Text\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let user: User = User(Text.from_bytes(input, 0, input.len()), Text.from_bytes(input, 0, input.len()))\n"
        "    let left: TextView = user.name.as_view()\n"
        "    let right: TextView = user.other.as_view()\n"
        "    return left.len() + right.len()\n"
    )


def test_fixed_array_disjoint_constant_index_remains_accepted() -> None:
    _compile(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let values: Array[Text,2] = [Text.from_bytes(input, 0, input.len()), Text.from_bytes(input, 0, input.len())]\n"
        "    let view: TextView = values[0].as_view()\n"
        "    values[1] = Text.from_bytes(input, 0, input.len())\n"
        "    return view.len()\n"
    )



@pytest.mark.parametrize(
    "source",
    [
        (
            "record User:\n"
            "    name: Text\n"
            "    age: UInt64\n"
            "fn main(input: BytesView) -> UInt64:\n"
            "    let user: User = User(Text.from_bytes(input, 0, input.len()), 0)\n"
            "    drop(user.name)\n"
            "    return user.age\n"
        ),
        (
            "fn main(input: BytesView) -> UInt64:\n"
            "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
            "    let boxed: Box[Text] = Box.new(text)\n"
            "    drop(boxed.get())\n"
            "    return 0\n"
        ),
        (
            "fn main(input: BytesView) -> UInt64:\n"
            "    let values: Array[Text,1] = [Text.from_bytes(input, 0, input.len())]\n"
            "    drop(values[0])\n"
            "    return 0\n"
        ),
        (
            "fn main(input: BytesView) -> UInt64:\n"
            "    let choice: Option[Text] = Some(Text.from_bytes(input, 0, input.len()))\n"
            "    drop(choice.unwrap())\n"
            "    return 0\n"
        ),
    ],
)
def test_projected_owner_drops_are_accepted(source: str) -> None:
    _compile(source)


def test_projected_move_runs_without_clone_or_double_free(tmp_path) -> None:
    if find_c_compiler() is None:
        pytest.skip("C compiler unavailable")
    source = _user_main(
        "let name: Text = user.name",
        "    return name.len() + user.age\n",
    )
    hir = compile_structured_hir(source)
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    generated = emit_general_c(hir, representation, mir)
    assert "name = merlo_move_Text(&((&user)->name));" in generated.source
    build = compile_c_source(
        generated.source,
        output_dir=tmp_path,
        stem="projected_move",
    )
    assert build.status == "MEASURED", build.stderr
    completed = subprocess.run(
        [build.binary_path],
        input=b"abc",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=3\n" in completed.stdout
    assert b"allocations=1 frees=1" in completed.stdout


@pytest.mark.parametrize(
    "operation",
    [
        "let first: Text = user.name\n"
        "    let second: Text = user.name",
        "drop(user.name)\n"
        "    drop(user.name)",
    ],
)
def test_projected_owner_cannot_be_consumed_twice(operation: str) -> None:
    with pytest.raises(
        StructuredHIRCompileError,
        match="^UseAfterMove: user.name$",
    ):
        _compile(_user_main(operation))


def test_enum_payload_move_uses_move_glue() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let choice: Option[Text] = Some(Text.from_bytes(input, 0, input.len()))\n"
        "    let moved: Text = choice.unwrap()\n"
        "    return moved.len()\n"
    )
    hir = compile_structured_hir(source)
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    generated = emit_general_c(hir, representation, mir).source
    assert "merlo_move_Text(&(" in generated
    assert any(
        operation.op == "load_take"
        and operation.place == "choice::variant::Some"
        for function in mir.ownership.functions
        for block in function.blocks
        for operation in block.operations
    )


def test_borrowed_enum_parameter_unwrap_clones_payload(tmp_path) -> None:
    source = (
        "fn take(choice: Option[Text]) -> Text:\n"
        "    return choice.unwrap()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let choice: Option[Text] = Some(Text.from_bytes(input, 0, input.len()))\n"
        "    let copied: Text = take(choice)\n"
        "    let moved: Text = choice.unwrap()\n"
        "    return copied.len() + moved.len()\n"
    )
    hir = compile_structured_hir(source)
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    generated = emit_general_c(hir, representation, mir).source
    body = generated.split(
        "static MerloText merlo_fn_take(MerloOption_Text *choice) {",
        1,
    )[1].split("\n}", 1)[0]
    assert "merlo_clone_Text(&(choice)->payload.Some)" in body
    assert "merlo_move_Text(&(choice)->payload.Some)" not in body
    if find_c_compiler() is None:
        pytest.skip("C compiler unavailable")
    build = compile_c_source(
        generated,
        output_dir=tmp_path,
        stem="borrowed_unwrap",
    )
    assert build.status == "MEASURED", build.stderr
    completed = subprocess.run(
        [build.binary_path],
        input=b"abc",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert b"OK result=6\n" in completed.stdout
    assert b"allocations=2 frees=2" in completed.stdout


def test_borrowed_projection_constructor_operands_are_not_taken() -> None:
    source = (
        "record User:\n"
        "    name: Text\n"
        "record Pair:\n"
        "    first: Text\n"
        "    second: Text\n"
        "fn pair(user: User) -> Pair:\n"
        "    return Pair(user.name, user.name)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    return 0\n"
    )
    hir = compile_structured_hir(source)
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    assert not any(
        operation.op == "load_take"
        and operation.place is not None
        and operation.place.startswith("user::")
        for function in mir.ownership.functions
        for block in function.blocks
        for operation in block.operations
    )


def test_distinct_constant_array_moves_have_distinct_mir_places() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let values: Array[Text,2] = [\n"
        "        Text.from_bytes(input, 0, input.len()),\n"
        "        Text.from_bytes(input, 0, input.len()),\n"
        "    ]\n"
        "    let first: Text = values[0]\n"
        "    let second: Text = values[1]\n"
        "    return first.len() + second.len()\n"
    )
    hir = compile_structured_hir(source)
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    moved_places = {
        operation.place
        for function in mir.ownership.functions
        for block in function.blocks
        for operation in block.operations
        if operation.op == "load_take"
        and operation.place is not None
        and operation.place.startswith("values::index::")
    }
    assert moved_places == {"values::index::0", "values::index::1"}


def test_dropping_borrowed_view_is_noop_in_mir() -> None:
    source = (
        _USER_HEADER
        + "fn main(input: BytesView) -> UInt64:\n"
        "    let user: User = User(Text.from_bytes(input, 0, input.len()), 0)\n"
        "    let view: TextView = user.name.as_view()\n"
        "    drop(view)\n"
        "    user.name = Text.from_bytes(input, 0, input.len())\n"
        "    return user.name.len()\n"
    )
    hir = compile_structured_hir(source)
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    assert not any(
        instruction.op == "drop_value"
        and instruction.type_name == "TextView"
        for function in mir.functions
        for block in function.blocks
        for instruction in block.instructions
    )


def test_loop_move_can_restore_field_before_backedge() -> None:
    _compile(
        _user_main(
            "while input.len() > 0:\n"
            "        let name: Text = user.name\n"
            "        user.name = Text.from_bytes(input, 0, input.len())\n"
            "        drop(name)\n"
            "        break",
            "    return user.name.len()\n",
        )
    )


def test_projected_argument_move_updates_caller_state() -> None:
    _compile(
        _USER_HEADER
        + "fn take(value: Text) -> Text:\n"
        "    return value\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let user: User = User(Text.from_bytes(input, 0, input.len()), 7)\n"
        "    let name: Text = take(user.name)\n"
        "    return name.len() + user.age\n"
    )


def test_partial_owner_cannot_escape_in_callback_capture() -> None:
    source = (
        _USER_HEADER
        + "fn make(input: BytesView) -> Fn[UInt64,UInt64]:\n"
        "    let user: User = User(Text.from_bytes(input, 0, input.len()), 7)\n"
        "    let name: Text = user.name\n"
        "    value => value + user.age\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    0\n"
    )
    elaborated = elaborate_surface(
        parse_surface(source, path="partial-capture.mlo")
    )
    with pytest.raises(
        StructuredHIRCompileError,
        match="^PartiallyMovedValue: user$",
    ):
        compile_canonical_hir(elaborated.canonical)


def test_resource_field_move_reaches_native_move_glue() -> None:
    source = (
        "enum AppError:\n"
        "    FileOpen\n"
        "    Closed\n"
        "record Holder:\n"
        "    reader: FileReader\n"
        "task main(path: Path) -> Result[Unit,AppError]:\n"
        "    uses fs.read\n"
        "    let reader: FileReader = fs.open_read(path)?\n"
        "    let holder: Holder = Holder(reader)\n"
        "    let moved: FileReader = holder.reader\n"
        "    fs.close_read(moved)?\n"
        "    return Ok(Unit())\n"
    )
    hir = compile_structured_hir(source, entry_function="main")
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    generated = emit_general_c(hir, representation, mir).source
    assert "merlo_move_FileReader(&((&holder)->reader))" in generated
