from __future__ import annotations

import pytest

from merlo.structured_hir_v2 import (
    StructuredHIRCompileError,
    compile_structured_hir,
)


def _compile(source: str) -> None:
    compile_structured_hir(source, path="loop-borrow-state-regression.mlo")


def test_backedge_rejects_new_borrow_in_preexisting_container() -> None:
    """A loop-created borrow must not disappear from the backedge state.

    ``views`` exists before the loop.  The body stores a view backed by
    ``text`` into it.  If that new borrow state is discarded at the loop
    boundary, ``drop(text)`` becomes a false-safe use-after-free path.
    """

    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let views: Vec[TextView] = Vec.new()\n"
        "    var index: UInt64 = 0\n"
        "    while index < 1:\n"
        "        views.push(text.as_view())\n"
        "        index += 1\n"
        "    drop(text)\n"
        "    return views.get(0).len()\n"
    )

    with pytest.raises(StructuredHIRCompileError):
        _compile(source)


def test_break_exit_preserves_new_borrow_in_preexisting_container() -> None:
    """A borrow introduced on a break exit must participate in the exit join."""

    source = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let views: Vec[TextView] = Vec.new()\n"
        "    var index: UInt64 = 0\n"
        "    while index < 1:\n"
        "        views.push(text.as_view())\n"
        "        break\n"
        "    drop(text)\n"
        "    return views.get(0).len()\n"
    )

    with pytest.raises(StructuredHIRCompileError):
        _compile(source)
