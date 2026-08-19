from __future__ import annotations

import pytest

from merlo.structured_hir_v2 import (
    StructuredHIRCompileError,
    compile_structured_hir,
)


_USER_HEADER = (
    "record User:\n"
    "    name: Text\n"
    "    age: UInt64\n"
)


def _compile(source: str) -> None:
    compile_structured_hir(source, path="while-ownership.mlo")


def _user_loop(
    body: str,
    tail: str = "    return 0\n",
    prefix: str = "",
) -> str:
    return (
        _USER_HEADER
        + "fn main(input: BytesView) -> UInt64:\n"
        + "    let user: User = User(Text.from_bytes(input, 0, input.len()), 0)\n"
        + prefix
        + "    var index: UInt64 = 0\n"
        + "    while index < 1:\n"
        + body
        + "        index += 1\n"
        + tail
    )


@pytest.mark.parametrize(
    ("source", "error"),
    [
        (
            _user_loop("        let moved: Text = user.name\n"),
            "ProjectedOwnerMoveRequiresPartialMoveSupport",
        ),
        (
            _user_loop(
                "        drop(user)\n",
                "    return view.len()\n",
                "    let view: TextView = user.name.as_view()\n",
            ),
            "BackingOwnerDropWhileBorrowed",
        ),
        (
            _user_loop(
                "        user.name = Text.from_bytes(input, 0, input.len())\n",
                "    return view.len()\n",
                "    let view: TextView = user.name.as_view()\n",
            ),
            "MutationDuringBorrow",
        ),
        (
            _user_loop(
                "        if input.len() > 0:\n"
                "            let moved: User = user\n",
                "    return user.age\n",
            ),
            "OwnershipAmbiguity",
        ),
        (
            "fn main(input: BytesView) -> UInt64:\n"
            "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
            "    var index: UInt64 = 0\n"
            "    while index < 1:\n"
            "        drop(text)\n"
            "        drop(text)\n"
            "        index += 1\n"
            "    return 0\n",
            "DuplicateDrop",
        ),
        (
            "record Holder:\n"
            "    view: TextView\n"
            "fn main(input: BytesView) -> TextView:\n"
            "    var index: UInt64 = 0\n"
            "    while index < 1:\n"
            "        let text: Text = Text.from_bytes(input, 0, input.len())\n"
            "        let holder: Holder = Holder(text.as_view())\n"
            "        return holder.view\n"
            "    return Text.from_bytes(input, 0, input.len()).as_view()\n",
            "EscapedView",
        ),
    ],
)
def test_while_ownership_rejects_unsafe_paths(
    source: str,
    error: str,
) -> None:
    with pytest.raises(StructuredHIRCompileError, match=error):
        _compile(source)


def test_while_zero_iteration_join_rejects_owner_use_after_loop() -> None:
    source = _user_loop(
        "        drop(user)\n",
        "    return user.age\n",
    )
    with pytest.raises(StructuredHIRCompileError, match="OwnershipAmbiguity"):
        _compile(source)


def test_read_only_while_is_accepted() -> None:
    _compile(
        "fn main(input: BytesView) -> UInt64:\n"
        "    var index: UInt64 = 0\n"
        "    while index < input.len():\n"
        "        index += 1\n"
        "    return index\n"
    )


def test_disjoint_scalar_loop_mutation_is_accepted() -> None:
    _compile(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let values: Vec[Text] = Vec.new()\n"
        "    values.push(Text.from_bytes(input, 0, input.len()))\n"
        "    let view: TextView = values.get(0).as_view()\n"
        "    var index: UInt64 = 0\n"
        "    while index < 1:\n"
        "        values[1] = Text.from_bytes(input, 0, input.len())\n"
        "        index += 1\n"
        "    return view.len()\n"
    )


def test_loop_local_owner_does_not_escape_join() -> None:
    _compile(
        "fn main(input: BytesView) -> UInt64:\n"
        "    var index: UInt64 = 0\n"
        "    while index < 1:\n"
        "        let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "        drop(text)\n"
        "        index += 1\n"
        "    return index\n"
    )


def test_terminal_return_inside_loop_is_ownership_safe() -> None:
    _compile(
        "fn main(input: BytesView) -> UInt64:\n"
        "    var index: UInt64 = 0\n"
        "    while index < input.len():\n"
        "        let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "        return text.len()\n"
        "    return index\n"
    )


def test_break_and_continue_paths_join_without_leaking_loop_control() -> None:
    _compile(
        "fn main(input: BytesView) -> UInt64:\n"
        "    var index: UInt64 = 0\n"
        "    while index < input.len():\n"
        "        index += 1\n"
        "        if index == 1:\n"
        "            continue\n"
        "        if index == 2:\n"
        "            break\n"
        "    return index\n"
    )


def test_owner_reassignment_inside_loop_keeps_target_place() -> None:
    _compile(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let value: Text = Text.from_bytes(input, 0, input.len())\n"
        "    var index: UInt64 = 0\n"
        "    while index < 1:\n"
        "        let next: Text = Text.from_bytes(input, 0, input.len())\n"
        "        value = next\n"
        "        index += 1\n"
        "    return value.len()\n"
    )
