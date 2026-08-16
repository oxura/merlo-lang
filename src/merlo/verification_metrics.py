from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from merlo.bounded_symbolic import BoundedSymbolicReport, SymbolicStatus
from merlo.obligation_ir import (
    ObligationCategory,
    ObligationDisposition,
    ObligationProgram,
)
from merlo.smt_backend import SMTReport, SMTStatus


VERIFICATION_METRICS_SCHEMA_VERSION = 1
VERIFICATION_METRICS_CONTRACT = "merlo.verification-metrics.v1"

_STATIC_ENGINE = "static"
_BOUNDED_ENGINE = "bounded_symbolic"
_SMT_ENGINE = "smt"


class VerificationState(str, Enum):
    AUTOMATICALLY_CLOSED = "automatically_closed"
    REFUTED = "refuted"
    RUNTIME_GUARDED = "runtime_guarded"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class ObligationClosureEvidence:
    """Canonical classification and proof provenance for one obligation."""

    obligation_id: str
    category: ObligationCategory
    state: VerificationState
    provenance: tuple[tuple[str, str], ...] = ()
    statuses: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.provenance != tuple(sorted(set(self.provenance))):
            raise ValueError("ClosureProvenanceNotCanonical")
        if self.statuses != tuple(sorted(set(self.statuses))):
            raise ValueError("ClosureStatusesNotCanonical")

    @property
    def automatically_closed(self) -> bool:
        return self.state is VerificationState.AUTOMATICALLY_CLOSED


    def to_dict(self) -> dict[str, Any]:
        engines = tuple(sorted({engine for engine, _ in self.provenance}))
        dischargers = tuple(
            sorted({discharger for _, discharger in self.provenance})
        )
        return {
            "obligation_id": self.obligation_id,
            "category": self.category.value,
            "state": self.state.value,
            "automatically_closed": self.automatically_closed,
            "provenance": [
                {"engine": engine, "discharger": discharger}
                for engine, discharger in self.provenance
            ],
            "engines": list(engines),
            "dischargers": list(dischargers),
            "statuses": list(self.statuses),
        }


@dataclass(frozen=True)
class CategoryMetrics:
    category: ObligationCategory
    total: int
    closed: int

    def __post_init__(self) -> None:
        if self.total < 0 or self.closed < 0 or self.closed > self.total:
            raise ValueError("InvalidCategoryMetrics")


    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "total": self.total,
            "closed": self.closed,
        }


@dataclass(frozen=True)
class EngineMetrics:
    engine: str
    closed: int
    obligation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.closed < 0:
            raise ValueError("InvalidEngineMetrics")
        if self.obligation_ids != tuple(sorted(set(self.obligation_ids))):
            raise ValueError("EngineObligationsNotCanonical")
        if self.closed != len(self.obligation_ids):
            raise ValueError("EngineClosureCountMismatch")


    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "closed": self.closed,
            "obligation_ids": list(self.obligation_ids),
        }


@dataclass(frozen=True)
class VerificationMetricsReport:
    """Deterministic aggregate for one obligation-program compilation."""

    hir_digest: str
    obligation_digest: str
    bounded_digest: str
    smt_digest: str
    total_obligations: int
    automatically_closed: int
    refuted: int
    runtime_guarded: int
    unresolved: int
    closed_rate_numerator: int
    closed_rate_denominator: int
    closed_rate_basis_points: int
    obligations: tuple[ObligationClosureEvidence, ...]
    categories: tuple[CategoryMetrics, ...]
    engines: tuple[EngineMetrics, ...]
    schema_version: int = VERIFICATION_METRICS_SCHEMA_VERSION
    contract: str = VERIFICATION_METRICS_CONTRACT

    def __post_init__(self) -> None:
        if self.schema_version != VERIFICATION_METRICS_SCHEMA_VERSION:
            raise ValueError("VerificationMetricsSchemaMismatch")
        if self.contract != VERIFICATION_METRICS_CONTRACT:
            raise ValueError("VerificationMetricsContractMismatch")
        counts = (
            self.total_obligations,
            self.automatically_closed,
            self.refuted,
            self.runtime_guarded,
            self.unresolved,
            self.closed_rate_numerator,
            self.closed_rate_denominator,
            self.closed_rate_basis_points,
        )
        if any(value < 0 for value in counts):
            raise ValueError("NegativeVerificationMetric")
        if self.automatically_closed > self.total_obligations:
            raise ValueError("ClosedCountExceedsTotal")
        if (
            self.automatically_closed
            + self.refuted
            + self.runtime_guarded
            + self.unresolved
            != self.total_obligations
        ):
            raise ValueError("ClassificationCountMismatch")
        if self.closed_rate_numerator != self.automatically_closed:
            raise ValueError("ClosedRateNumeratorMismatch")
        if self.closed_rate_denominator != self.total_obligations:
            raise ValueError("ClosedRateDenominatorMismatch")
        expected_basis_points = (
            self.closed_rate_numerator * 10000 // self.closed_rate_denominator
            if self.closed_rate_denominator
            else 0
        )
        if self.closed_rate_basis_points != expected_basis_points:
            raise ValueError("ClosedRateBasisPointsMismatch")
        if self.closed_rate_basis_points > 10000:
            raise ValueError("InvalidClosedRateBasisPoints")
        evidence_ids = tuple(item.obligation_id for item in self.obligations)
        if evidence_ids != tuple(sorted(evidence_ids)):
            raise ValueError("ClosureEvidenceNotCanonical")
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("DuplicateClosureEvidence")
        if len(evidence_ids) != self.total_obligations:
            raise ValueError("EvidenceCountMismatch")

        category_totals: dict[ObligationCategory, int] = {}
        category_closed: dict[ObligationCategory, int] = {}
        state_counts = {
            VerificationState.AUTOMATICALLY_CLOSED: 0,
            VerificationState.REFUTED: 0,
            VerificationState.RUNTIME_GUARDED: 0,
            VerificationState.UNRESOLVED: 0,
        }
        expected_engine_ids: dict[str, set[str]] = {}
        for item in self.obligations:
            category_totals[item.category] = category_totals.get(item.category, 0) + 1
            if item.state not in state_counts:
                raise ValueError("UnknownVerificationState")
            state_counts[item.state] += 1
            if item.state == VerificationState.AUTOMATICALLY_CLOSED:
                category_closed[item.category] = (
                    category_closed.get(item.category, 0) + 1
                )
                for engine, _ in item.provenance:
                    expected_engine_ids.setdefault(engine, set()).add(
                        item.obligation_id
                    )
            elif (
                item.state != VerificationState.REFUTED
                and item.provenance
            ):
                raise ValueError("NonClosedEvidenceHasProvenance")

        if (
            state_counts[VerificationState.AUTOMATICALLY_CLOSED]
            != self.automatically_closed
            or state_counts[VerificationState.REFUTED] != self.refuted
            or state_counts[VerificationState.RUNTIME_GUARDED] != self.runtime_guarded
            or state_counts[VerificationState.UNRESOLVED] != self.unresolved
        ):
            raise ValueError("ClassificationEvidenceMismatch")

        category_ids = tuple(item.category for item in self.categories)
        if category_ids != tuple(sorted(category_ids, key=lambda item: item.value)):
            raise ValueError("CategoryMetricsNotCanonical")
        if len(set(category_ids)) != len(category_ids):
            raise ValueError("DuplicateCategoryMetrics")
        expected_categories = tuple(
            CategoryMetrics(
                category,
                category_totals[category],
                category_closed.get(category, 0),
            )
            for category in sorted(category_totals, key=lambda item: item.value)
        )
        if self.categories != expected_categories:
            raise ValueError("CategoryMetricsMismatch")

        engine_names = tuple(item.engine for item in self.engines)
        if engine_names != tuple(sorted(engine_names)):
            raise ValueError("EngineMetricsNotCanonical")
        if len(set(engine_names)) != len(engine_names):
            raise ValueError("DuplicateEngineMetrics")
        expected_engines = tuple(
            EngineMetrics(engine, len(ids), tuple(sorted(ids)))
            for engine, ids in sorted(expected_engine_ids.items())
        )
        if self.engines != expected_engines:
            raise ValueError("EngineMetricsMismatch")


    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "hir_digest": self.hir_digest,
            "obligation_digest": self.obligation_digest,
            "bounded_digest": self.bounded_digest,
            "smt_digest": self.smt_digest,
            "total_obligations": self.total_obligations,
            "automatically_closed": self.automatically_closed,
            "refuted": self.refuted,
            "runtime_guarded": self.runtime_guarded,
            "unresolved": self.unresolved,
            "closed_rate_numerator": self.closed_rate_numerator,
            "closed_rate_denominator": self.closed_rate_denominator,
            "closed_rate_basis_points": self.closed_rate_basis_points,
            "obligations": [item.to_dict() for item in self.obligations],
            "categories": [item.to_dict() for item in self.categories],
            "engines": [item.to_dict() for item in self.engines],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def _validate_inputs(
    obligations: ObligationProgram,
    bounded: BoundedSymbolicReport,
    smt: SMTReport,
) -> None:
    if bounded.hir_digest != obligations.hir_digest:
        raise ValueError("DigestMismatch: bounded report HIR")
    if smt.hir_digest != obligations.hir_digest:
        raise ValueError("DigestMismatch: SMT report HIR")
    obligation_digest = obligations.digest
    if bounded.obligation_digest != obligation_digest:
        raise ValueError("DigestMismatch: bounded report obligations")
    if smt.obligation_digest != obligation_digest:
        raise ValueError("DigestMismatch: SMT report obligations")

    expected = tuple(item.obligation_id for item in obligations.obligations)
    if expected != tuple(sorted(expected)) or len(set(expected)) != len(expected):
        raise ValueError("ObligationsNotCanonical")
    for name, report in (("bounded", bounded), ("SMT", smt)):
        identifiers = tuple(item.obligation_id for item in report.results)
        if identifiers != tuple(sorted(identifiers)):
            raise ValueError(f"{name}ResultsNotCanonical")
        if len(identifiers) != len(set(identifiers)):
            raise ValueError(f"Duplicate{name}Result")
        unknown = set(identifiers) - set(expected)
        if unknown:
            raise ValueError(f"{name}ResultForUnknownObligation: {sorted(unknown)}")


def measure_verification_metrics(
    obligations: ObligationProgram,
    bounded_report: BoundedSymbolicReport,
    smt_report: SMTReport,
) -> VerificationMetricsReport:
    """Measure conservative automatic closure for one compilation."""

    _validate_inputs(obligations, bounded_report, smt_report)
    bounded_by_id = {item.obligation_id: item for item in bounded_report.results}
    smt_by_id = {item.obligation_id: item for item in smt_report.results}

    evidence: list[ObligationClosureEvidence] = []
    category_totals: dict[ObligationCategory, int] = {
        category: 0 for category in ObligationCategory
    }
    category_closed: dict[ObligationCategory, int] = {
        category: 0 for category in ObligationCategory
    }
    engine_ids: dict[str, set[str]] = {}
    closed_count = refuted_count = runtime_count = unresolved_count = 0

    for obligation in obligations.obligations:
        category = obligation.category
        category_totals[category] += 1
        bounded_result = bounded_by_id.get(obligation.obligation_id)
        smt_result = smt_by_id.get(obligation.obligation_id)
        statuses: set[str] = {obligation.disposition.value}
        if bounded_result is not None:
            statuses.add(bounded_result.status.value)
        if smt_result is not None:
            statuses.add(smt_result.status.value)

        provenance: set[tuple[str, str]] = set()
        if obligation.disposition == ObligationDisposition.STATICALLY_PROVEN:
            provenance.add((_STATIC_ENGINE, obligation.discharged_by or "static"))
        if bounded_result is not None and bounded_result.status == SymbolicStatus.PROVEN:
            provenance.add((_BOUNDED_ENGINE, _BOUNDED_ENGINE))
        if smt_result is not None and smt_result.status == SMTStatus.PROVEN:
            provenance.add((_SMT_ENGINE, smt_result.backend))

        has_refutation = (
            obligation.disposition == ObligationDisposition.STATICALLY_REFUTED
            or (
                bounded_result is not None
                and bounded_result.status == SymbolicStatus.REFUTED
            )
            or (
                smt_result is not None
                and smt_result.status == SMTStatus.REFUTED
            )
        )
        is_closed = bool(provenance) and not has_refutation
        if has_refutation:
            state = VerificationState.REFUTED
            refuted_count += 1
        elif is_closed:
            state = VerificationState.AUTOMATICALLY_CLOSED
            closed_count += 1
            category_closed[category] += 1
            for engine, _ in provenance:
                engine_ids.setdefault(engine, set()).add(obligation.obligation_id)
        elif obligation.disposition == ObligationDisposition.RUNTIME_GUARDED:
            state = VerificationState.RUNTIME_GUARDED
            runtime_count += 1
        else:
            state = VerificationState.UNRESOLVED
            unresolved_count += 1

        evidence.append(
            ObligationClosureEvidence(
                obligation.obligation_id,
                category,
                state,
                tuple(sorted(provenance)),
                tuple(sorted(statuses)),
            )
        )

    categories = tuple(
        CategoryMetrics(category, category_totals[category], category_closed[category])
        for category in sorted(ObligationCategory, key=lambda item: item.value)
        if category_totals[category]
    )
    engines = tuple(
        EngineMetrics(engine, len(ids), tuple(sorted(ids)))
        for engine, ids in sorted(engine_ids.items())
    )
    total = len(obligations.obligations)
    basis_points = (closed_count * 10000) // total if total else 0
    return VerificationMetricsReport(
        hir_digest=obligations.hir_digest,
        obligation_digest=obligations.digest,
        bounded_digest=bounded_report.digest,
        smt_digest=smt_report.digest,
        total_obligations=total,
        automatically_closed=closed_count,
        refuted=refuted_count,
        runtime_guarded=runtime_count,
        unresolved=unresolved_count,
        closed_rate_numerator=closed_count,
        closed_rate_denominator=total,
        closed_rate_basis_points=basis_points,
        obligations=tuple(evidence),
        categories=categories,
        engines=engines,
    )



__all__ = [
    "VERIFICATION_METRICS_SCHEMA_VERSION",
    "VERIFICATION_METRICS_CONTRACT",
    "VerificationState",
    "ObligationClosureEvidence",
    "CategoryMetrics",
    "EngineMetrics",
    "VerificationMetricsReport",
    "measure_verification_metrics",
]
