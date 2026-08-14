from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from research.archive.alpha1.merlo.bytes_reborrow import (
    bytes_reborrow_hir_manifest,
    bytes_reborrow_mir_manifest,
    validate_bytes_reborrow_mir,
)
from research.archive.alpha1.merlo.bytes_reborrow_experiment import (
    BYTES_REBORROW_INVALID_FAMILIES,
    BYTES_REBORROW_VALID_FAMILIES,
    _abi_audit,
    _invalid_surface_source,
    _malformed_reborrow_mir,
    valid_reference,
    valid_template_source,
    validate_bytes_reborrow_report,
)
from research.archive.alpha1.merlo.native_differential import HIREvaluator, MIRInterpreter
from research.archive.alpha1.merlo.native_hir import compile_native_hir
from research.archive.alpha1.merlo.performance_frontend import PerformanceCompileError, compile_performance_source
from research.archive.alpha1.merlo.performance_opt import optimize_mir


CHAIN_SOURCE = """fn leaf(data: BytesView, state: UInt64) -> UInt64:
    var checksum: UInt64 = state
    for i in 0..data.len():
        checksum = checksum + data[i] * (i + 1)
    return checksum

fn middle(data: BytesView, state: UInt64) -> UInt64:
    return leaf(data, state)

fn outer(data: BytesView, state: UInt64) -> UInt64:
    return middle(data, state)

fn transfer(data: Bytes) -> Bytes:
    return data

fn main(n: UInt64) -> UInt64:
    let owner: Bytes = Bytes.new(n)
    for i in 0..n:
        owner[i] = (i * 3 + 1) & 255
    let first: UInt64 = outer(owner.slice(0, n), 7)
    let second: UInt64 = outer(owner.slice(0, n), first)
    let moved: Bytes = transfer(move(owner))
    return second + moved.len()
"""


def test_hir_represents_root_parent_child_and_stable_identities() -> None:
    hir = compile_native_hir(CHAIN_SOURCE, path="contracts/reborrow.meldra")
    manifest = bytes_reborrow_hir_manifest(hir)

    assert manifest["contract"] == "meldra.bytes-reborrow-hir.v1"
    assert manifest["lifetime_annotations_in_surface"] == 0
    assert manifest["validation"]["maximum_depth"] == 3
    relationships = manifest["relationships"]
    assert {item["borrow_depth"] for item in relationships} == {1, 2, 3}
    assert {item["root_owner"]["name"] for item in relationships} == {"main.owner"}
    assert all(item["call_scope"] == "direct_synchronous" for item in relationships)
    assert all(item["non_escaping"] is True for item in relationships)
    assert all(item["caller"]["symbol_id"].startswith("nhirs_") for item in relationships)
    assert all(item["caller"]["revision_id"].startswith("rev_") for item in relationships)
    assert all(item["source"] is not None for item in relationships)
    assert all(
        item["root_owner"]["symbol_id"]
        and item["root_owner"]["revision_id"]
        and item["parent_borrow"]["symbol_id"]
        and item["parent_borrow"]["revision_id"]
        and item["child_reborrow"]["semantic_id"]
        and item["child_reborrow"]["revision_id"]
        for item in relationships
    )


def test_mir_preserves_reborrow_metadata_after_optimization() -> None:
    original = compile_performance_source(CHAIN_SOURCE).mir
    optimized, _ = optimize_mir(original)
    before = bytes_reborrow_mir_manifest(original)
    after = bytes_reborrow_mir_manifest(optimized)

    assert before["contract"] == "meldra.bytes-reborrow-mir.v1"
    assert before["validation"] == after["validation"]
    assert before["validation"]["balanced"] is True
    assert before["validation"]["maximum_depth"] == 3
    assert {item["op"] for item in after["events"]} == {
        "borrow_argument",
        "reborrow_argument",
        "reborrow_end",
        "borrow_end",
    }
    assert all(call["direct_call"] is True for call in after["calls"])


def test_three_level_chain_uses_one_root_and_lifo_end_order() -> None:
    original = compile_performance_source(CHAIN_SOURCE).mir
    optimized, _ = optimize_mir(original)
    for mir in (original, optimized):
        observation = MIRInterpreter(mir).run((32,))
        assert observation.status == "OK"
        assert observation.return_value == 66567
        assert observation.allocations == 1
        assert observation.drops == 1
        assert observation.retains == 0
        assert observation.releases == 1
        starts = [
            item
            for item in observation.effect_trace
            if item.startswith(("borrow_argument:", "reborrow_argument:"))
        ]
        ends = [item for item in observation.effect_trace if item.startswith(("borrow_end:", "reborrow_end:"))]
        assert any(":depth=3:" in item for item in starts)
        assert all(":root=main.owner:" in item for item in starts)
        assert [re.search(r"remaining=(\d+)$", item).group(1) for item in ends[:3]] == ["2", "1", "0"]


def test_conditional_reborrow_requires_both_branches() -> None:
    valid, _salt, _depth = valid_template_source("conditional_balanced")
    compile_performance_source(valid)
    invalid, expected = _invalid_surface_source("branch_unbalanced_reborrow", 4)
    with pytest.raises(PerformanceCompileError, match=expected):
        compile_performance_source(invalid)


def test_owner_is_available_only_after_complete_chain() -> None:
    for family in ("owner_mutation_after", "owner_move_after"):
        source, salt, _depth = valid_template_source(family)
        arguments = (17, 23, 3, 7, 1)
        expected = valid_reference(family, arguments, salt)
        hir = compile_native_hir(source, path=f"owner/{family}.meldra")
        optimized, _ = optimize_mir(compile_performance_source(source).mir)
        assert HIREvaluator(hir).run(arguments).return_value == expected
        assert MIRInterpreter(optimized).run(arguments).return_value == expected


@pytest.mark.parametrize("family", BYTES_REBORROW_VALID_FAMILIES)
def test_every_valid_template_matches_independent_reference(family: str) -> None:
    source, salt, depth = valid_template_source(family)
    arguments = (17, 23, 3, 7, 1)
    expected = valid_reference(family, arguments, salt)
    hir = compile_native_hir(source, path=f"templates/{family}.meldra")
    original = compile_performance_source(source).mir
    optimized, _ = optimize_mir(original)

    assert HIREvaluator(hir).run(arguments).return_value == expected
    assert MIRInterpreter(original).run(arguments).return_value == expected
    optimized_result = MIRInterpreter(optimized).run(arguments)
    assert optimized_result.return_value == expected
    assert bytes_reborrow_mir_manifest(optimized)["validation"]["maximum_depth"] == depth


@pytest.mark.parametrize(
    "family",
    [item for item in BYTES_REBORROW_INVALID_FAMILIES if item not in {"parent_ends_before_child", "child_outlives_parent"}],
)
def test_surface_invalid_families_are_rejected(family: str) -> None:
    source, expected = _invalid_surface_source(family, 3)
    with pytest.raises(PerformanceCompileError, match=re.escape(expected)):
        compile_performance_source(source)


@pytest.mark.parametrize(
    ("family", "diagnostic"),
    [
        ("parent_ends_before_child", "parent ends before child"),
        ("child_outlives_parent", "start/end sets differ"),
    ],
)
def test_malformed_reborrow_mir_is_rejected(family: str, diagnostic: str) -> None:
    malformed = _malformed_reborrow_mir(family, 3)
    with pytest.raises(ValueError, match=diagnostic):
        validate_bytes_reborrow_mir(malformed)


def test_noinline_abi_audit_exposes_every_level_without_overhead(tmp_path) -> None:
    evidence = _abi_audit(tmp_path)

    assert evidence["passed"] is True
    assert evidence["call_chain_counters"] == {
        "allocations": 0,
        "frees": 0,
        "payload_copies": 0,
        "retains": 0,
        "releases": 0,
    }
    assert evidence["pointer_proof"]["same_payload_pointer"] is True
    assert evidence["pointer_proof"]["child_inside_root"] is True
    assert evidence["checks"]["pointer_length_parameter_every_level"] is True
    assert evidence["falsification_control"]["detected"] is True
    assert evidence["pointer_proof"]["same_length"] is True
    assert evidence["counter_sources"] == {
        "allocations": "generated_noinline_before_after_snapshot",
        "frees": "generated_noinline_before_after_snapshot",
        "payload_copies": "generated_noinline_before_after_snapshot",
        "retains": "generated_helper_reference_count_call_scan",
        "releases": "generated_helper_reference_count_call_scan",
    }
    assert evidence["meldra_noinline"]["metrics"] == {
        "chain_allocations": 0,
        "chain_frees": 0,
        "chain_payload_copies": 0,
        "same_pointer": 1,
        "same_length": 1,
        "child_inside_root": 1,
    }
    assert all(
        all(value == 0 for value in helper["overhead_scan"].values())
        for helper in evidence["meldra_noinline"]["helpers"].values()
    )
    assert all(
        control["detected"]
        for control in evidence["falsification_controls"].values()
    )


def test_decision_artifact_is_internally_valid() -> None:
    report = json.loads(
        Path("tools/benchmarks/merlo/benchmarks/meldra_bytes_reborrow.json").read_text(
            encoding="utf-8"
        )
    )

    validate_bytes_reborrow_report(report)
    assert report["correctness"]["valid"]["case_count"] >= 256
    assert report["correctness"]["invalid"]["case_count"] >= 192
    assert report["artifact_payload_sha256"]
