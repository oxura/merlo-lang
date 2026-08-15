from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from merlo.bounded_symbolic import (
    BoundedSymbolicReport,
    SymbolicObligationResult,
    SymbolicStatus,
)
from merlo.obligation_ir import (
    ObligationCategory,
    ObligationDisposition,
    ObligationProgram,
    TypedObligation,
)
from merlo.smt_backend import (
    SMTObligationResult,
    SMTReport,
    SMTStatus,
)
from merlo.structured_hir_v2 import SourceSpan
from merlo.verification_metrics import (
    VERIFICATION_METRICS_CONTRACT,
    VerificationState,
    measure_verification_metrics,
)


def _obligation(
    identifier: str,
    category: ObligationCategory,
    disposition: ObligationDisposition = ObligationDisposition.UNRESOLVED,
    discharged_by: str | None = None,
) -> TypedObligation:
    return TypedObligation(
        obligation_id=identifier,
        revision_id=f"rev-{identifier}",
        category=category,
        predicate=f"{identifier} predicate",
        expected_type="Bool",
        owner_symbol_id="fn-main",
        owner_revision_id="rev-main",
        source=SourceSpan("test.mlo", 1, 1, 1, 1),
        context=(),
        disposition=disposition,
        discharged_by=discharged_by,
    )


def _reports(
    obligations: ObligationProgram,
    bounded_results: tuple[SymbolicObligationResult, ...] = (),
    smt_results: tuple[SMTObligationResult, ...] = (),
) -> tuple[BoundedSymbolicReport, SMTReport]:
    bounded = BoundedSymbolicReport(
        obligations.hir_digest,
        obligations.digest,
        bounded_results,
        64,
        64,
    )
    smt = SMTReport(
        obligations.hir_digest,
        obligations.digest,
        "z3",
        "test-z3",
        1000,
        64,
        smt_results,
    )
    return bounded, smt


def test_static_range_and_bounded_and_smt_closures_are_deduplicated() -> None:
    program = ObligationProgram(
        "hir-digest",
        (
            _obligation(
                "a-static",
                ObligationCategory.ARITHMETIC_SAFETY,
                ObligationDisposition.STATICALLY_PROVEN,
                "range_analysis",
            ),
            _obligation("b-bounded", ObligationCategory.TYPE_SAFETY),
            _obligation("c-both", ObligationCategory.DATA_INVARIANT),
        ),
    )
    bounded, smt = _reports(
        program,
        (
            SymbolicObligationResult("b-bounded", SymbolicStatus.PROVEN, 2, True),
            SymbolicObligationResult("c-both", SymbolicStatus.PROVEN, 2, True),
        ),
        (
            SMTObligationResult("c-both", SMTStatus.PROVEN, "z3", "q", "(check-sat)"),
        ),
    )

    report = measure_verification_metrics(program, bounded, smt)

    assert report.contract == VERIFICATION_METRICS_CONTRACT
    assert report.total_obligations == 3
    assert report.automatically_closed == 3
    assert report.closed_rate_numerator == 3
    assert report.closed_rate_denominator == 3
    assert report.closed_rate_basis_points == 10000
    both = report.obligations[2]
    assert both.automatically_closed
    assert tuple(engine for engine, _ in both.provenance) == (
        "bounded_symbolic",
        "smt",
    )
    assert tuple(discharger for _, discharger in both.provenance) == (
        "bounded_symbolic",
        "z3",
    )
    assert [(item.engine, item.closed) for item in report.engines] == [
        ("bounded_symbolic", 2),
        ("smt", 1),
        ("static", 1),
    ]


def test_refuted_unsupported_unknown_and_runtime_guarded_are_not_closed() -> None:
    program = ObligationProgram(
        "hir",
        (
            _obligation("a-refuted", ObligationCategory.TYPE_SAFETY),
            _obligation("b-unsupported", ObligationCategory.TYPE_SAFETY),
            _obligation("c-unknown", ObligationCategory.TYPE_SAFETY),
            _obligation(
                "d-guarded",
                ObligationCategory.TYPE_SAFETY,
                ObligationDisposition.RUNTIME_GUARDED,
                "native_contract_guard",
            ),
        ),
    )
    bounded, smt = _reports(
        program,
        (
            SymbolicObligationResult("a-refuted", SymbolicStatus.REFUTED, 1, True),
            SymbolicObligationResult("b-unsupported", SymbolicStatus.UNSUPPORTED, 0, False),
        ),
        (
            SMTObligationResult("c-unknown", SMTStatus.UNKNOWN, "z3", "q", None),
        ),
    )
    report = measure_verification_metrics(program, bounded, smt)
    assert report.automatically_closed == 0
    assert report.refuted == 1
    assert report.runtime_guarded == 1
    assert report.unresolved == 2
    assert all(not item.automatically_closed for item in report.obligations)

def test_refutation_takes_precedence_over_contradictory_proof() -> None:
    program = ObligationProgram("hir", (_obligation("a", ObligationCategory.TYPE_SAFETY),))
    bounded, smt = _reports(
        program,
        (SymbolicObligationResult("a", SymbolicStatus.PROVEN, 1, True),),
        (SMTObligationResult("a", SMTStatus.REFUTED, "z3", "q", None),),
    )

    report = measure_verification_metrics(program, bounded, smt)

    assert report.automatically_closed == 0
    assert report.refuted == 1
    assert report.obligations[0].state is VerificationState.REFUTED
    assert report.obligations[0].statuses == (
        "proven",
        "refuted",
        "unresolved",
    )
    assert report.obligations[0].provenance == (("bounded_symbolic", "bounded_symbolic"),)
    assert report.engines == ()


def test_categories_zero_rate_and_deterministic_digest() -> None:
    program = ObligationProgram("hir", ())
    bounded, smt = _reports(program)
    report = measure_verification_metrics(program, bounded, smt)

    assert report.closed_rate_denominator == 0
    assert report.closed_rate_numerator == 0
    assert report.closed_rate_basis_points == 0
    assert report.categories == ()
    assert report.engines == ()
    assert report.to_json() == measure_verification_metrics(program, bounded, smt).to_json()
    assert report.digest == measure_verification_metrics(program, bounded, smt).digest


def test_digest_relationships_are_validated() -> None:
    program = ObligationProgram("hir", (_obligation("a", ObligationCategory.TYPE_SAFETY),))
    bounded, smt = _reports(program)
    bad_bounded = BoundedSymbolicReport(
        "other-hir", bounded.obligation_digest, (), 64, 64
    )
    with pytest.raises(ValueError, match="DigestMismatch"):
        measure_verification_metrics(program, bad_bounded, smt)

    bad_smt = SMTReport(
        smt.hir_digest,
        "wrong-obligation-digest",
        smt.backend,
        smt.backend_version,
        smt.timeout_ms,
        smt.max_paths,
        smt.results,
    )
    with pytest.raises(ValueError, match="DigestMismatch"):
        measure_verification_metrics(program, bounded, bad_smt)

def test_report_rejects_noncanonical_boundary_counts_and_contract() -> None:
    program = ObligationProgram("hir", ())
    bounded, smt = _reports(program)
    report = measure_verification_metrics(program, bounded, smt)

    with pytest.raises(ValueError, match="ClassificationCountMismatch"):
        replace(report, unresolved=1)
    with pytest.raises(ValueError, match="ClosedRateBasisPointsMismatch"):
        replace(report, closed_rate_basis_points=1)
    with pytest.raises(ValueError, match="VerificationMetricsContractMismatch"):
        replace(report, contract="wrong")


def test_dataclasses_are_frozen_and_json_has_no_float_rate() -> None:
    program = ObligationProgram("hir", (_obligation("a", ObligationCategory.TYPE_SAFETY),))
    bounded, smt = _reports(
        program,
        (SymbolicObligationResult("a", SymbolicStatus.PROVEN, 1, True),),
    )
    report = measure_verification_metrics(program, bounded, smt)
    with pytest.raises(FrozenInstanceError):
        report.automatically_closed = 1  # type: ignore[misc]
    assert "closed_rate" in report.to_json()
    assert ".0" not in report.to_json()
