from __future__ import annotations

import json

import pytest

from merlo.borrow_summary import (
    BORROW_SUMMARY_CYCLE_MARKER,
    BorrowPlacePath,
    BorrowPlaceStep,
    BorrowRelation,
    BorrowSummary,
    BorrowSummaryEntry,
    _SummaryComputer,
)
from merlo.type_arena import TypeContextBuilder
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.semantic_world import _world_digest
from merlo.structured_hir_v2 import (
    StructuredHIRCompileError,
    StructuredHIRProgram,
    compile_structured_hir,
)


def _summary(source: str, name: str) -> BorrowSummary:
    return compile_structured_hir(source).function(name).borrow_summary


def _summary_context():
    builder = TypeContextBuilder()
    builder.intern_many(("Text", "TextView", "BytesView", "UInt64"))
    return builder.freeze()


def test_borrow_place_path_and_relation_roundtrip() -> None:
    path = BorrowPlacePath(
        (
            BorrowPlaceStep.parameter(1),
            BorrowPlaceStep.field("payload"),
            BorrowPlaceStep.element(),
            BorrowPlaceStep.variant_payload("Some"),
            BorrowPlaceStep.dereference(),
            BorrowPlaceStep.recursive_tail("left|right"),
        )
    )
    relation = BorrowRelation(
        1,
        path,
        _summary_context().type_id("TextView"),
        BorrowPlacePath((BorrowPlaceStep.element(),)),
        "contained",
        "owned_contained_borrow",
        "TextView",
    )
    assert tuple(step.kind for step in path.steps) == (
        "Parameter",
        "Field",
        "Element",
        "VariantPayload",
        "Deref",
        "RecursiveTail",
    )
    assert BorrowPlacePath.from_dict(path.to_dict()) == path
    assert BorrowRelation.from_dict(relation.to_dict()) == relation
    assert BorrowSummaryEntry.from_dict(BorrowSummaryEntry(relation, ("left",)).to_dict()).relation == relation
    with pytest.raises(ValueError, match="invalid borrow place step"):
        BorrowPlaceStep("arbitrary-string")
    with pytest.raises(ValueError, match="RecursiveTail must be"):
        BorrowPlacePath(
            (
                BorrowPlaceStep.recursive_tail("component"),
                BorrowPlaceStep.field("tail"),
            )
        )


def test_direct_and_mutual_recursion_converge_to_one_relation() -> None:
    direct = (
        "fn recursive_view(text: Text, depth: UInt64) -> TextView:\n"
        "    if depth == 0:\n"
        "        return text.as_view()\n"
        "    return recursive_view(text, depth - 1)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    return recursive_view(text, 1).len()\n"
    )
    mutual = (
        "fn left(text: Text, depth: UInt64) -> TextView:\n"
        "    if depth == 0:\n"
        "        return text.as_view()\n"
        "    return right(text, depth - 1)\n"
        "fn right(text: Text, depth: UInt64) -> TextView:\n"
        "    return left(text, depth)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    return right(text, 1).len()\n"
    )
    assert len(_summary(direct, "recursive_view").relations) == 1
    assert len(_summary(mutual, "left").relations) == 1
    assert len(_summary(mutual, "right").relations) == 1


def test_recursive_projection_uses_one_structural_recursive_tail() -> None:
    source = (
        "fn recursive_view(text: Text, depth: UInt64) -> TextView:\n"
        "    if depth == 0:\n"
        "        return text.as_view()\n"
        "    return recursive_view(text, depth - 1).slice(0, 1)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    return recursive_view(text, 1).len()\n"
    )
    summary = _summary(source, "recursive_view")
    recursive = [
        relation
        for relation in summary.relations
        if relation.result_path.steps
        and relation.result_path.steps[-1].kind == "RecursiveTail"
    ]
    assert len(recursive) == 1
    assert recursive[0].result_path.steps[-1].value == "recursive_view"
    assert len(summary.relations) == 2


def test_declaration_order_and_unrelated_wrapper_do_not_change_relation() -> None:
    recursive = (
        "fn recursive_view(text: Text, depth: UInt64) -> TextView:\n"
        "    if depth == 0:\n"
        "        return text.as_view()\n"
        "    return recursive_view(text, depth - 1)\n"
    )
    main = (
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    return recursive_view(text, 1).len()\n"
    )
    unrelated = (
        "fn scalar(value: UInt64) -> UInt64:\n"
        "    return value\n"
        "fn scalar_wrapper(value: UInt64) -> UInt64:\n"
        "    return scalar(value)\n"
    )
    first = compile_structured_hir(recursive + main)
    reordered = compile_structured_hir(main + recursive)
    extended = compile_structured_hir(recursive + main + unrelated)
    expected = first.function("recursive_view").borrow_summary.semantic_dict()
    assert reordered.function("recursive_view").borrow_summary.semantic_dict() == expected
    assert extended.function("recursive_view").borrow_summary.semantic_dict() == expected
    assert extended.function("recursive_view").revision_id == first.function("recursive_view").revision_id


def test_witness_only_improvement_requeues_mutual_recursion_caller() -> None:
    source = (
        "fn left(text: Text, depth: UInt64) -> TextView:\n"
        "    if depth == 0:\n"
        "        return text.as_view()\n"
        "    return right(text, depth - 1)\n"
        "fn right(text: Text, depth: UInt64) -> TextView:\n"
        "    return left(text, depth)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    return right(text, 1).len()\n"
    )
    summary = _summary(source, "right")
    assert len(summary.entries) == 1
    assert summary.entries[0].witness_path == ("left",)

def test_late_shorter_witness_replaces_longer_and_requeues_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (
        "fn left(text: Text, depth: UInt64) -> TextView:\n"
        "    if depth == 0:\n"
        "        return text.as_view()\n"
        "    return right(text, depth - 1)\n"
        "fn right(text: Text, depth: UInt64) -> TextView:\n"
        "    return left(text, depth)\n"
    )
    original = _SummaryComputer._compute_one
    witness_calls = 0

    def delayed_shorter(
        self: _SummaryComputer,
        name: str,
        *,
        final: bool = False,
        witness_only: bool = False,
    ) -> BorrowSummary:
        nonlocal witness_calls
        result = original(self, name, final=final, witness_only=witness_only)
        if name == "right" and witness_only:
            witness_calls += 1
            if witness_calls == 1:
                relation = self._summaries[name].relations[0]
                return BorrowSummary((BorrowSummaryEntry(relation, ("long", "path")),))
        return result

    monkeypatch.setattr(_SummaryComputer, "_compute_one", delayed_shorter)
    summary = compile_structured_hir(
        source,
        entry_function="left",
    ).function("right").borrow_summary
    assert witness_calls >= 2
    assert summary.entries[0].witness_path == ("left",)


def test_diamond_recursive_call_graph_converges_deterministically() -> None:
    source = (
        "fn root(text: Text, depth: UInt64) -> TextView:\n"
        "    if depth == 0:\n"
        "        return text.as_view()\n"
        "    if depth == 1:\n"
        "        return left(text, depth - 1)\n"
        "    return right(text, depth - 1)\n"
        "fn left(text: Text, depth: UInt64) -> TextView:\n"
        "    return root(text, depth).slice(0, 1)\n"
        "fn right(text: Text, depth: UInt64) -> TextView:\n"
        "    return root(text, depth).slice(0, 1)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    return root(text, 2).len()\n"
    )
    first = compile_structured_hir(source)
    second = compile_structured_hir(source)
    assert first.to_json() == second.to_json()
    assert len(first.function("root").borrow_summary.relations) == 2
    assert len(first.function("left").borrow_summary.relations) == 1
    assert len(first.function("right").borrow_summary.relations) == 1


def test_witness_change_does_not_change_hir_revision_or_digest() -> None:
    source = (
        "fn borrow_text(text: Text) -> TextView:\n"
        "    return text.as_view()\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    return borrow_text(text).len()\n"
    )
    original = compile_structured_hir(source)
    payload = json.loads(original.to_json())
    function = next(item for item in payload["functions"] if item["name"] == "borrow_text")
    function["borrow_summary"]["entries"][0]["witness_path"] = ["diagnostic-only"]
    changed = StructuredHIRProgram.from_dict(payload)
    assert changed.function("borrow_text").revision_id == original.function("borrow_text").revision_id
    assert changed.digest == original.digest
    assert changed.to_json() != original.to_json()
    original_rir = lower_structured_hir_to_rir(original)
    changed_rir = lower_structured_hir_to_rir(changed)
    assert changed_rir.digest == original_rir.digest
    assert changed_rir.to_json() != original_rir.to_json()
    original_summary = original.function("borrow_text").borrow_summary.to_dict()
    changed_summary = changed.function("borrow_text").borrow_summary.to_dict()
    original_world = {
        "symbols": [{"borrow_summary": original_summary}],
        "borrow_summaries": [{"summary": original_summary}],
    }
    changed_world = {
        "symbols": [{"borrow_summary": changed_summary}],
        "borrow_summaries": [{"summary": changed_summary}],
    }
    assert _world_digest(changed_world) == _world_digest(original_world)


def test_semantic_fixed_point_fails_closed_on_nonmonotone_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (
        "fn recursive_view(text: Text, depth: UInt64) -> TextView:\n"
        "    if depth == 0:\n"
        "        return text.as_view()\n"
        "    return recursive_view(text, depth - 1)\n"
    )
    original = _SummaryComputer._compute_one
    semantic_calls = 0

    def shrinking(
        self: _SummaryComputer,
        name: str,
        *,
        final: bool = False,
        witness_only: bool = False,
    ) -> BorrowSummary:
        nonlocal semantic_calls
        result = original(self, name, final=final, witness_only=witness_only)
        if name == "recursive_view" and not final and not witness_only:
            semantic_calls += 1
            if semantic_calls == 2:
                return BorrowSummary()
        return result

    monkeypatch.setattr(_SummaryComputer, "_compute_one", shrinking)
    with pytest.raises(
        StructuredHIRCompileError,
        match="OpaqueBorrowSummary.*BorrowSummaryNonMonotone",
    ):
        compile_structured_hir(
            source,
            entry_function="recursive_view",
        )
    assert semantic_calls == 2


def test_five_thousand_function_chain_has_no_python_recursion() -> None:
    count = 5_000
    source = "".join(
        (
            f"fn f{index:04d}(text: Text) -> TextView:\n"
            f"    return f{index + 1:04d}(text)\n"
        )
        if index + 1 < count
        else (
            f"fn f{index:04d}(text: Text) -> TextView:\n"
            "    return text.as_view()\n"
        )
        for index in range(count)
    )
    program = compile_structured_hir(source, entry_function="f0000")
    summaries = {
        function.name: function.borrow_summary
        for function in program.functions
    }
    assert len(summaries) == count
    assert summaries["f0000"].status == "known"
    assert len(summaries["f0000"].relations) == 1


def test_stable_borrow_expression_is_accepted_and_true_temporary_is_rejected() -> None:
    source = (
        "fn identity(view: TextView) -> TextView:\n"
        "    return view\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    let view: TextView = identity(text.as_view())\n"
        "    let size: UInt64 = view.len()\n"
        "    drop(view)\n"
        "    drop(text)\n"
        "    return size\n"
    )
    assert compile_structured_hir(source).function("identity").borrow_summary.status == "known"
    temporary = source.replace(
        "identity(text.as_view())",
        "identity(Text.from_bytes(input, 0, input.len()).as_view())",
    )
    with pytest.raises(StructuredHIRCompileError, match="BorrowFromTemporaryEscapes"):
        compile_structured_hir(temporary)


def test_recursive_witness_is_bounded_and_diagnostic_only() -> None:
    source = (
        "fn recursive_view(text: Text, depth: UInt64) -> TextView:\n"
        "    if depth == 0:\n"
        "        return text.as_view()\n"
        "    return recursive_view(text, depth - 1).slice(0, 1)\n"
        "fn main(input: BytesView) -> UInt64:\n"
        "    let text: Text = Text.from_bytes(input, 0, input.len())\n"
        "    return recursive_view(text, 1).len()\n"
    )
    summary = _summary(source, "recursive_view")
    assert max(map(len, (entry.witness_path for entry in summary.entries))) <= 1
    assert any(BORROW_SUMMARY_CYCLE_MARKER in entry.witness_path for entry in summary.entries)
    assert "witness_path" not in summary.semantic_dict()["relations"][0]
