from __future__ import annotations

import re

import pytest

from research.archive.alpha1.merlo.bytes_borrowed_return import (
    bytes_borrowed_return_abi_manifest,
    bytes_borrowed_return_hir_manifest,
    bytes_borrowed_return_mir_manifest,
    validate_bytes_borrowed_return_mir,
)
from research.archive.alpha1.merlo.bytes_borrowed_return_experiment import (
    BYTES_BORROWED_RETURN_INVALID_FAMILIES,
    BYTES_BORROWED_RETURN_VALID_FAMILIES,
    _abi_audit,
    _invalid_surface_source,
    _malformed_transfer_mir,
    valid_reference,
    valid_template_source,
)
from research.archive.alpha1.merlo.native_differential import HIREvaluator, MIRInterpreter
from research.archive.alpha1.merlo.native_hir import compile_native_hir
from research.archive.alpha1.merlo.performance_frontend import (
    PerformanceCompileError,
    compile_performance_source,
)
from research.archive.alpha1.merlo.performance_opt import optimize_mir


def test_hir_records_unique_origin_root_scope_and_stable_identities() -> None:
    source, _salt = valid_template_source("two_return_chain")
    manifest = bytes_borrowed_return_hir_manifest(
        compile_native_hir(source, path="contracts/borrowed-return.meldra")
    )
    assert manifest["contract"] == "meldra.bytes-borrowed-return-hir.v1"
    assert manifest["lifetime_annotations_in_surface"] == 0
    assert manifest["scope"]["borrowed_source_parameters"] == 1
    assert manifest["scope"]["borrowed_return_chain_maximum"] == 2
    assert manifest["validation"]["unique_origin_per_call"] is True
    assert manifest["validation"]["last_use_proven"] is True
    assert manifest["relationships"]
    for relationship in manifest["relationships"]:
        assert relationship["return_origin"].endswith(".data")
        assert relationship["root_owner"]["name"] == "main.owner"
        assert relationship["root_owner"]["symbol_id"].startswith("nhirs_")
        assert relationship["root_owner"]["revision_id"].startswith("rev_")
        assert relationship["borrowed_source_parameter"]["symbol_id"].startswith(
            "nhirs_"
        )
        assert relationship["borrowed_source_parameter"][
            "revision_id"
        ].startswith("rev_")
        assert relationship["returned_child_borrow"][
            "semantic_id"
        ].startswith("brr_")
        assert relationship["caller_scope"]["symbol_id"].startswith("nhirs_")
        assert relationship["last_use"]["line"] > 0
        assert relationship["last_use"]["source"]["path"] == "contracts/borrowed-return.meldra"
        assert relationship["call_source"]["path"] == "contracts/borrowed-return.meldra"


def test_mir_transfer_operations_and_optimizer_preservation() -> None:
    source, _salt = valid_template_source("two_return_chain")
    original = compile_performance_source(source).mir
    optimized, _ = optimize_mir(original)
    manifests = [
        bytes_borrowed_return_mir_manifest(original),
        bytes_borrowed_return_mir_manifest(optimized),
    ]
    for manifest in manifests:
        assert manifest["contract"] == "meldra.bytes-borrowed-return-mir.v1"
        assert manifest["validation"]["balanced"] is True
        assert manifest["validation"]["maximum_chain_depth"] == 2
        assert manifest["validation"]["root_owner_sets"] == ["main.owner"]
        ops = {event["op"] for event in manifest["events"]}
        assert {
            "borrow_argument",
            "reborrow_argument",
            "borrow_return_transfer",
            "caller_borrow_continue",
            "borrow_end",
        } <= ops
        assert all(call["direct_call"] for call in manifest["calls"])
        assert all(
            call["call_scope"] == "direct_synchronous"
            for call in manifest["calls"]
        )
    assert manifests[0]["validation"] == manifests[1]["validation"]


def test_caller_borrow_ends_at_last_use_then_owner_mutation_is_allowed() -> None:
    source, salt = valid_template_source("owner_mutation_after")
    arguments = (31, 47, 3, 17, 5, True)
    expected = valid_reference("owner_mutation_after", arguments, salt)
    original = compile_performance_source(source).mir
    optimized, _ = optimize_mir(original)
    for mir in (original, optimized):
        observation = MIRInterpreter(mir).run(arguments)
        assert observation.status == "OK"
        assert observation.return_value == expected
        assert observation.retains == 0
        assert observation.releases == 1
        continue_index = next(
            index
            for index, item in enumerate(observation.effect_trace)
            if item.startswith("caller_borrow_continue:")
        )
        end_index = next(
            index
            for index, item in enumerate(observation.effect_trace)
            if item.startswith("borrow_end:")
        )
        assert continue_index < end_index


def test_unused_returned_borrow_ends_on_call_statement() -> None:
    source, salt = valid_template_source("unused_result")
    arguments = (17, 29, 2, 7, 3, False)
    expected = valid_reference("unused_result", arguments, salt)
    mir = compile_performance_source(source).mir
    observation = MIRInterpreter(mir).run(arguments)
    assert observation.status == "OK"
    assert observation.return_value == expected
    manifest = bytes_borrowed_return_mir_manifest(mir)
    assert manifest["validation"]["last_use_proven"] is True
    assert all(
        event["last_use_line"] == (event["source"] or {})["line"]
        for event in manifest["events"]
        if event["op"] == "caller_borrow_continue"
    )


@pytest.mark.parametrize("family", BYTES_BORROWED_RETURN_VALID_FAMILIES)
def test_valid_template_matches_surface_mir_and_optimized_reference(
    family: str,
) -> None:
    source, salt = valid_template_source(family)
    arguments = (31, 47, 3, 17, 5, True)
    expected = valid_reference(family, arguments, salt)
    hir = compile_native_hir(source, path=f"valid/{family}.meldra")
    original = compile_performance_source(source).mir
    optimized, _ = optimize_mir(original)
    assert HIREvaluator(hir).run(arguments).return_value == expected
    assert MIRInterpreter(original).run(arguments).return_value == expected
    optimized_result = MIRInterpreter(optimized).run(arguments)
    assert optimized_result.return_value == expected
    assert optimized_result.retains == 0
    assert bytes_borrowed_return_mir_manifest(optimized)["validation"][
        "unique_origin_per_call"
    ] is True


@pytest.mark.parametrize(
    "family",
    [
        item
        for item in BYTES_BORROWED_RETURN_INVALID_FAMILIES
        if item not in {"early_optimizer_end", "branch_only_end"}
    ],
)
def test_invalid_surface_family_has_exact_diagnostic(family: str) -> None:
    source, expected = _invalid_surface_source(family, 3)
    with pytest.raises(PerformanceCompileError, match=re.escape(expected)):
        compile_performance_source(source)


@pytest.mark.parametrize(
    ("family", "expected"),
    [
        ("early_optimizer_end", "before caller last use"),
        ("branch_only_end", "start/continue/end sets differ"),
    ],
)
def test_malformed_transfer_metadata_is_rejected(
    family: str, expected: str
) -> None:
    with pytest.raises(ValueError, match=re.escape(expected)):
        validate_bytes_borrowed_return_mir(_malformed_transfer_mir(family, 5))


def test_abi_manifest_is_nonowning_pointer_length_return() -> None:
    manifest = bytes_borrowed_return_abi_manifest()
    assert manifest["contract"] == "meldra.bytes-borrowed-return-abi.v1"
    assert manifest["parameter"]["fields"] == [
        "const uint8_t *data",
        "uint64_t length",
    ]
    assert manifest["return"]["ownership"] == "borrowed_transfer_to_caller"
    assert manifest["allocation"] is False
    assert manifest["payload_copy"] is False
    assert manifest["reference_counting"] is False
    assert manifest["new_owner"] is False


def test_noinline_abi_audit_detects_controls_without_overhead(tmp_path) -> None:
    evidence = _abi_audit(tmp_path)
    assert evidence["passed"] is True
    assert all(evidence["checks"].values())
    assert all(value == 0 for value in evidence["call_chain_counters"].values())
    assert evidence["falsification_control"]["deliberate_copy"] is True
    assert evidence["falsification_control"]["wrong_origin"] is True
