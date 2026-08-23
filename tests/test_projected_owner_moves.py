from __future__ import annotations

import pytest

from merlo.structured_hir_v2 import StructuredHIRCompileError, compile_structured_hir


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
    ("name", "source"),
    [
        (
            "record field then drop parent",
            _user_main(
                "let name: Text = user.name",
                "    drop(user)\n"
                "    return name.len()\n",
            ),
        ),
        (
            "nested owning field",
            "record Inner:\n"
            "    text: Text\n"
            "record Outer:\n"
            "    inner: Inner\n"
            "fn main(input: BytesView) -> UInt64:\n"
            "    let inner: Inner = Inner(Text.from_bytes(input, 0, input.len()))\n"
            "    let outer: Outer = Outer(inner)\n"
            "    let text: Text = outer.inner.text\n"
            "    drop(outer)\n"
            "    return text.len()\n",
        ),
        (
            "Box owning payload",
            "fn main(input: BytesView) -> UInt64:\n"
            "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
            "    let boxed: Box[Text] = Box.new(text)\n"
            "    let moved: Text = boxed.get()\n"
            "    drop(boxed)\n"
            "    return moved.len()\n",
        ),
        (
            "Box owner containing borrow",
            "fn main(input: BytesView) -> UInt64:\n"
            "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
            "    let values: Vec[TextView] = Vec.new()\n"
            "    values.push(text.as_view())\n"
            "    let boxed: Box[Vec[TextView]] = Box.new(values)\n"
            "    let moved: Vec[TextView] = boxed.get()\n"
            "    return moved.len()\n",
        ),
        (
            "owner array element",
            "fn main(input: BytesView) -> UInt64:\n"
            "    let values: Array[Text,1] = [Text.from_bytes(input, 0, input.len())]\n"
            "    let moved: Text = values[0]\n"
            "    drop(values)\n"
            "    return moved.len()\n",
        ),
        (
            "conditional projected move",
            _user_main(
                "if input.len() > 0:\n"
                "        let name: Text = user.name",
                "    drop(user)\n"
                "    return 0\n",
            ),
        ),
        (
            "read after projected move",
            _user_main(
                "let name: Text = user.name",
                "    return user.name.len()\n",
            ),
        ),
        (
            "move parent after projected move",
            _user_main(
                "let name: Text = user.name",
                "    let moved: User = user\n"
                "    return name.len() + moved.age\n",
            ),
        ),
    ],
)
def test_projected_owner_moves_are_rejected(name: str, source: str) -> None:
    del name
    with pytest.raises(
        StructuredHIRCompileError,
        match="^ProjectedOwnerMoveRequiresPartialMoveSupport$",
    ):
        _compile(source)


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
            "    return 0\n"
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
            "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
            "    let values: Vec[TextView] = Vec.new()\n"
            "    values.push(text.as_view())\n"
            "    let boxed: Box[Vec[TextView]] = Box.new(values)\n"
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
def test_projected_owner_drops_are_rejected(source: str) -> None:
    with pytest.raises(
        StructuredHIRCompileError,
        match="^ProjectedOwnerDropRequiresPartialMoveSupport$",
    ):
        _compile(source)
