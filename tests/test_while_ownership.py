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
            "LoopOwnershipBackedgeRequiresFixedPointSupport",
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


def test_changed_owner_backedge_requires_fixed_point_support() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    var index: UInt64 = 0\n"
        "    while index < 2:\n"
        "        drop(text)\n"
        "        index += 1\n"
        "    return 0\n"
    )
    with pytest.raises(
        StructuredHIRCompileError,
        match="LoopOwnershipBackedgeRequiresFixedPointSupport",
    ):
        _compile(source)


def test_moved_owner_backedge_requires_fixed_point_support() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    var index: UInt64 = 0\n"
        "    while index < 2:\n"
        "        let moved: Text = text\n"
        "        drop(moved)\n"
        "        index += 1\n"
        "    return 0\n"
    )
    with pytest.raises(
        StructuredHIRCompileError,
        match="LoopOwnershipBackedgeRequiresFixedPointSupport",
    ):
        _compile(source)



def test_drop_then_continue_requires_fixed_point_support() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    var index: UInt64 = 0\n"
        "    while index < 2:\n"
        "        drop(text)\n"
        "        continue\n"
        "    return 0\n"
    )
    with pytest.raises(
        StructuredHIRCompileError,
        match="LoopOwnershipBackedgeRequiresFixedPointSupport",
    ):
        _compile(source)


def test_for_repeated_preloop_move_requires_fixed_point_support() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let values: Vec[UInt64] = Vec.new()\n"
        "    for item in values:\n"
        "        let moved: Text = text\n"
        "        drop(moved)\n"
        "    return 0\n"
    )
    with pytest.raises(
        StructuredHIRCompileError,
        match="LoopOwnershipBackedgeRequiresFixedPointSupport",
    ):
        _compile(source)


def test_break_drop_conflicts_with_zero_iteration_exit() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    var index: UInt64 = 0\n"
        "    while index < 1:\n"
        "        drop(text)\n"
        "        break\n"
        "    return 0\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="OwnershipAmbiguity"):
        _compile(source)


def test_conditional_drop_rejects_ambiguous_implicit_cleanup() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    if input.len() > 0:\n"
        "        drop(text)\n"
        "    return 0\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="OwnershipAmbiguity"):
        _compile(source)


def test_match_move_rejects_ambiguous_implicit_cleanup() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    match input.len():\n"
        "        case 0:\n"
        "            let moved: Text = text\n"
        "        case _:\n"
        "            pass\n"
        "    return 0\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="OwnershipAmbiguity"):
        _compile(source)


def test_borrow_reassignment_requires_backedge_fixed_point() -> None:
    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let view: TextView = text.as_view()\n"
        "    var index: UInt64 = 0\n"
        "    while index < 2:\n"
        "        view = text.as_view()\n"
        "        index += 1\n"
        "    return view.len()\n"
    )
    with pytest.raises(
        StructuredHIRCompileError,
        match="LoopOwnershipBackedgeRequiresFixedPointSupport",
    ):
        _compile(source)


def test_contained_borrow_mutation_is_rejected_inside_loop() -> None:
    _compile_contained = (
        "record Holder:\n"
        "    view: TextView\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let holder: Holder = Holder(text.as_view())\n"
        "    var index: UInt64 = 0\n"
        "    while index < 2:\n"
        "        holder.view = text.as_view()\n"
        "        index += 1\n"
        "    return holder.view.len()\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="MutationDuringBorrow"):
        _compile(_compile_contained)


def test_for_owner_binding_is_cleaned_before_backedge() -> None:
    _compile(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let values: Vec[Text] = Vec.new()\n"
        "    var index: UInt64 = 0\n"
        "    for item in values:\n"
        "        index += 1\n"
        "    return index\n"
    )


def test_for_owner_binding_is_clean_after_explicit_drop() -> None:
    _compile(
        "fn main(input: BytesView) -> UInt64:\n"
        "    let values: Vec[Text] = Vec.new()\n"
        "    for item in values:\n"
        "        drop(item)\n"
        "    return 0\n"
    )

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
