from __future__ import annotations

import json
from dataclasses import replace

import pytest

from merlo.bounded_symbolic import BoundedSymbolicReport, SymbolicCounterexample, SymbolicObligationResult, SymbolicStatus, verify_bounded
from merlo.obligation_ir import build_obligation_ir
from merlo.property_evidence import PropertyEvidenceReport, generate_property_evidence
from merlo.smt_backend import SMTCounterexample, SMTObligationResult, SMTReport, SMTStatus
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface


def _program(source: str):
    hir = compile_canonical_hir(elaborate_surface(parse_surface(source)).canonical)
    return hir, build_obligation_ir(hir)


def _source(type_name: str, predicate: str = "result == value") -> str:
    return f"fn identity(value: {type_name}) -> {type_name}:\n    ensure {predicate}\n    value\n\nfn main(input: BytesView) -> Byte:\n    Byte(input.len())\n"


def test_bool_and_byte_domains_are_exhaustive_when_they_fit() -> None:
    hir, obligations = _program(_source("Byte"))
    report = generate_property_evidence(hir, obligations, None, None, parameter_bounds=256, case_cap=256)
    prop = report.properties[0]
    assert prop.parameters[0].values == tuple(range(256))
    assert prop.parameters[0].exhaustive and prop.exhaustive and len(prop.cases) == 256
    bool_hir, bool_obligations = _program(_source("Bool"))
    bool_report = generate_property_evidence(bool_hir, bool_obligations, None, None, parameter_bounds=2)
    assert bool_report.properties[0].parameters[0].values == (False, True)
    assert bool_report.properties[0].parameters[0].exhaustive


def test_bool_cap_and_uint64_boundaries_are_sampled() -> None:
    hir, obligations = _program(_source("UInt64"))
    report = generate_property_evidence(hir, obligations, None, None, parameter_bounds=2)
    domain = report.properties[0].parameters[0]
    assert domain.values == (0, 18446744073709551615) and not domain.exhaustive
    bool_hir, bool_obligations = _program(_source("Bool"))
    bool_report = generate_property_evidence(bool_hir, bool_obligations, None, None, parameter_bounds=1)
    assert bool_report.properties[0].parameters[0].values == (False,)
    assert not bool_report.properties[0].parameters[0].exhaustive


def test_cartesian_cases_stop_at_case_cap() -> None:
    source = "fn pair(left: Byte, right: Byte) -> Byte:\n    ensure result == left\n    left\n\nfn main(input: BytesView) -> Byte:\n    Byte(input.len())\n"
    hir, obligations = _program(source)
    prop = generate_property_evidence(hir, obligations, None, None, parameter_bounds=2, case_cap=3).properties[0]
    assert len(prop.cases) == 3 and prop.cases[0].inputs == (("left", 0), ("right", 0)) and not prop.exhaustive


def test_deterministic_digest_and_all_bounds_serialize() -> None:
    hir, obligations = _program(_source("Byte"))
    first = generate_property_evidence(hir, obligations, None, None, parameter_bounds={"*": 2, "value": 1})
    second = generate_property_evidence(hir, obligations, None, None, parameter_bounds={"*": 2, "value": 1})
    assert first.to_json() == second.to_json() and first.digest == second.digest
    assert json.loads(first.to_json())["parameter_bounds"] == {"*": 2, "value": 1}


def test_real_bounded_and_smt_counterexamples_keep_result_and_types() -> None:
    hir, obligations = _program(_source("Byte", "result > value"))
    bounded = verify_bounded(hir, obligations)
    post = obligations.obligations[0]
    smt = SMTReport(hir.digest, obligations.digest, "test-smt", "1", 10, 2, (SMTObligationResult(post.obligation_id, SMTStatus.REFUTED, "test-smt", None, None, SMTCounterexample((("value", 0),))),))
    report = generate_property_evidence(hir, obligations, bounded, smt)
    assert [item.engine for item in report.counterexamples] == ["bounded", "smt"]
    assert report.counterexamples[0].result == 0 and report.counterexamples[1].result is None
    assert report.counterexamples[0].input_types == (("value", "Byte"),)


def test_non_refuted_statuses_do_not_create_evidence() -> None:
    hir, obligations = _program(_source("Byte"))
    post = obligations.obligations[0]
    bounded = BoundedSymbolicReport(hir.digest, obligations.digest, (SymbolicObligationResult(post.obligation_id, SymbolicStatus.PROVEN, 1, True, SymbolicCounterexample((("value", 0),), 0, post.predicate)),), 1, 1)
    assert generate_property_evidence(hir, obligations, bounded, None).counterexamples == ()


def test_digest_bound_schema_contract_and_order_validation() -> None:
    hir, obligations = _program(_source("Byte"))
    with pytest.raises(ValueError, match="EvidenceDigestMismatch"):
        generate_property_evidence(hir, obligations, replace(verify_bounded(hir, obligations), hir_digest="bad"), None)
    with pytest.raises(ValueError, match="InvalidParameterBound"):
        generate_property_evidence(hir, obligations, None, None, parameter_bounds=0)
    report = generate_property_evidence(hir, obligations, None, None, parameter_bounds=2)
    payload = json.loads(report.to_json())
    payload["schema_version"] = 99
    with pytest.raises(ValueError, match="SchemaVersionMismatch"):
        PropertyEvidenceReport.from_json(json.dumps(payload))
    payload["schema_version"] = 1
    payload["contract"] = "wrong"
    with pytest.raises(ValueError, match="ContractMismatch"):
        PropertyEvidenceReport.from_json(json.dumps(payload))


def test_unsupported_parameter_type_is_empty_sampled_domain() -> None:
    hir, obligations = _program(_source("Text"))
    domain = generate_property_evidence(hir, obligations, None, None).properties[0].parameters[0]
    assert domain.values == () and not domain.exhaustive
