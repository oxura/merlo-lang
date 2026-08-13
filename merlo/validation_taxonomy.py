from __future__ import annotations

"""Deterministic diagnostics for external validation failures.

False-block primary causes use ``FALSE_BLOCK_CAUSE_PRIORITY`` from left to
right.  A task may contribute once to every distinct raw cause, but exactly
once to the primary-cause Pareto.  Causes absent from the documented list are
ordered lexicographically after known causes; a task without a reason is
classified as ``unknown``.

Infrastructure classification only examines ``error_kind`` and
``error_message``.  It is descriptive: validation metrics and their confusion
matrix denominators are copied from the source report, never recomputed from
or changed by the taxonomy.
"""

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .external_bench import ExternalBenchmarkReport, ExternalPlanResult, ValidationMetrics


FALSE_BLOCK_CAUSE_PRIORITY = (
    "IdentityCollision",
    "TargetCollision",
    "SyntaxInvalid",
    "UnsupportedBinding",
    "UnsupportedSignatureMigration",
    "MissingArgumentMigration",
    "MoveDependencyCollision",
    "CyclicDependency",
    "PublicApiCompatibility",
    "EntityBudgetExceeded",
)

INFRASTRUCTURE_CATEGORIES = (
    "timeout",
    "identity_collision",
    "parser_frontend",
    "checkout_archive",
    "missing_dependency",
    "test_harness",
    "encoding",
    "unknown",
)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _cause_order(cause: str) -> tuple[int, str]:
    try:
        return (FALSE_BLOCK_CAUSE_PRIORITY.index(cause), cause)
    except ValueError:
        return (len(FALSE_BLOCK_CAUSE_PRIORITY), cause)


def _distinct_causes(result: ExternalPlanResult) -> tuple[str, ...]:
    causes = {reason.strip() for reason in result.blocked_reasons if reason.strip()}
    return tuple(sorted(causes, key=_cause_order)) or ("unknown",)


def _is_false_block(result: ExternalPlanResult) -> bool:
    return result.expected_safe and result.planner_allowed is False


@dataclass(frozen=True)
class CauseCount:
    cause: str
    count: int
    ratio: float | None

    def to_dict(self) -> dict[str, Any]:
        return {"cause": self.cause, "count": self.count, "ratio": self.ratio}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CauseCount":
        ratio = value.get("ratio")
        return cls(str(value["cause"]), int(value["count"]), None if ratio is None else float(ratio))


@dataclass(frozen=True)
class ParetoEntry:
    cause: str
    count: int
    ratio: float | None
    cumulative_count: int
    cumulative_ratio: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cause": self.cause,
            "count": self.count,
            "ratio": self.ratio,
            "cumulative_count": self.cumulative_count,
            "cumulative_ratio": self.cumulative_ratio,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ParetoEntry":
        ratio = value.get("ratio")
        cumulative_ratio = value.get("cumulative_ratio")
        return cls(
            cause=str(value["cause"]),
            count=int(value["count"]),
            ratio=None if ratio is None else float(ratio),
            cumulative_count=int(value["cumulative_count"]),
            cumulative_ratio=None if cumulative_ratio is None else float(cumulative_ratio),
        )


def _cause_tables(
    results: Iterable[ExternalPlanResult], denominator: int
) -> tuple[tuple[CauseCount, ...], tuple[ParetoEntry, ...]]:
    raw: Counter[str] = Counter()
    primary: Counter[str] = Counter()
    for result in results:
        causes = _distinct_causes(result)
        raw.update(causes)
        primary[causes[0]] += 1

    raw_counts = tuple(
        CauseCount(cause, count, _ratio(count, denominator))
        for cause, count in sorted(raw.items(), key=lambda item: (-item[1], _cause_order(item[0])))
    )
    ordered_primary = sorted(primary.items(), key=lambda item: (-item[1], _cause_order(item[0])))
    cumulative = 0
    pareto: list[ParetoEntry] = []
    for cause, count in ordered_primary:
        cumulative += count
        pareto.append(ParetoEntry(cause, count, _ratio(count, denominator), cumulative, _ratio(cumulative, denominator)))
    return raw_counts, tuple(pareto)


@dataclass(frozen=True)
class FalseBlockBreakdown:
    key: str
    numerator: int
    denominator: int
    ratio: float | None
    raw_multi_cause_counts: tuple[CauseCount, ...]
    pareto: tuple[ParetoEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "false_block": {
                "numerator": self.numerator,
                "denominator": self.denominator,
                "ratio": self.ratio,
            },
            "raw_multi_cause_counts": [item.to_dict() for item in self.raw_multi_cause_counts],
            "pareto": [item.to_dict() for item in self.pareto],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FalseBlockBreakdown":
        false_block = value["false_block"]
        ratio = false_block.get("ratio")
        return cls(
            key=str(value["key"]),
            numerator=int(false_block["numerator"]),
            denominator=int(false_block["denominator"]),
            ratio=None if ratio is None else float(ratio),
            raw_multi_cause_counts=tuple(CauseCount.from_dict(item) for item in value.get("raw_multi_cause_counts", ())),
            pareto=tuple(ParetoEntry.from_dict(item) for item in value.get("pareto", ())),
        )


def _classify_infrastructure(result: ExternalPlanResult) -> str:
    kind = (result.error_kind or "").strip().casefold()
    message = (result.error_message or "").strip().casefold()
    combined = f"{kind} {message}"

    if "timeout" in kind or "timed out" in message or "did not complete" in message or "before cancellation" in message:
        return "timeout"
    if (
        "identitycollision" in kind
        or "entitycollision" in kind
        or "ambiguous semantic entity" in message
        or "logical id collision" in message
        or "identity collision" in message
    ):
        return "identity_collision"
    if (
        kind in {"syntaxerror", "indentationerror", "taberror", "tokenerror", "parsererror", "parseerror"}
        or "parser frontend" in message
        or "failed to parse" in message
        or "syntax error" in message
        or "tokeniz" in message
    ):
        return "parser_frontend"
    if (
        kind in {"checkouterror", "archiveerror", "badzipfile", "repositorynotfound"}
        or "git checkout" in message
        or "git clone" in message
        or "checkout failed" in message
        or "archive" in message and ("extract" in message or "unpack" in message or "download" in message)
        or "project root is not a directory" in message
        or "repository not found" in message
    ):
        return "checkout_archive"
    if (
        kind in {"modulenotfounderror", "importerror", "missingdependency", "dependencymissingerror"}
        or "missing dependency" in message
        or "no module named" in message
        or "dependency not found" in message
    ):
        return "missing_dependency"
    if (
        kind in {"testharnesserror", "calledprocesserror", "subprocesserror"}
        and any(token in combined for token in ("test", "pytest", "unittest", "harness"))
        or "test harness" in message
        or "pytest collection" in message
        or "test command failed" in message
    ):
        return "test_harness"
    if (
        kind.startswith("unicode")
        or kind in {"lookuperror", "encodingerror", "decodeerror"}
        or "encoding" in message
        or "codec can't" in message
        or "codec can\u2019t" in message
        or "invalid utf-8" in message
    ):
        return "encoding"
    return "unknown"


@dataclass(frozen=True)
class InfrastructureCount:
    category: str
    count: int
    ratio: float | None

    def to_dict(self) -> dict[str, Any]:
        return {"category": self.category, "count": self.count, "ratio": self.ratio}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "InfrastructureCount":
        ratio = value.get("ratio")
        return cls(str(value["category"]), int(value["count"]), None if ratio is None else float(ratio))


@dataclass(frozen=True)
class InfrastructureTaxonomy:
    source_count: int
    classified_count: int
    categories: tuple[InfrastructureCount, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_count": self.source_count,
            "classified_count": self.classified_count,
            "categories": [item.to_dict() for item in self.categories],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "InfrastructureTaxonomy":
        return cls(
            source_count=int(value["source_count"]),
            classified_count=int(value["classified_count"]),
            categories=tuple(InfrastructureCount.from_dict(item) for item in value.get("categories", ())),
        )


@dataclass(frozen=True)
class ValidationFailureAnalysis:
    metrics: ValidationMetrics
    primary_cause_priority: tuple[str, ...]
    raw_multi_cause_counts: tuple[CauseCount, ...]
    pareto: tuple[ParetoEntry, ...]
    operation_breakdown: tuple[FalseBlockBreakdown, ...]
    category_breakdown: tuple[FalseBlockBreakdown, ...]
    infrastructure: InfrastructureTaxonomy

    @property
    def false_block_numerator(self) -> int:
        return self.metrics.false_block_numerator

    @property
    def false_block_denominator(self) -> int:
        return self.metrics.false_block_denominator

    @property
    def false_block_ratio(self) -> float | None:
        return _ratio(self.false_block_numerator, self.false_block_denominator)

    @property
    def validation_metrics(self) -> ValidationMetrics:
        return self.metrics

    def to_dict(self) -> dict[str, Any]:
        return {
            "validation_metrics": self.metrics.to_dict(),
            "false_block": {
                "numerator": self.false_block_numerator,
                "denominator": self.false_block_denominator,
                "ratio": self.false_block_ratio,
            },
            "primary_cause_priority": list(self.primary_cause_priority),
            "raw_multi_cause_counts": [item.to_dict() for item in self.raw_multi_cause_counts],
            "pareto": [item.to_dict() for item in self.pareto],
            "operation_breakdown": [item.to_dict() for item in self.operation_breakdown],
            "category_breakdown": [item.to_dict() for item in self.category_breakdown],
            "infrastructure": self.infrastructure.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidationFailureAnalysis":
        return cls(
            metrics=ValidationMetrics.from_dict(dict(value["validation_metrics"])),
            primary_cause_priority=tuple(map(str, value.get("primary_cause_priority", ()))),
            raw_multi_cause_counts=tuple(CauseCount.from_dict(item) for item in value.get("raw_multi_cause_counts", ())),
            pareto=tuple(ParetoEntry.from_dict(item) for item in value.get("pareto", ())),
            operation_breakdown=tuple(FalseBlockBreakdown.from_dict(item) for item in value.get("operation_breakdown", ())),
            category_breakdown=tuple(FalseBlockBreakdown.from_dict(item) for item in value.get("category_breakdown", ())),
            infrastructure=InfrastructureTaxonomy.from_dict(value["infrastructure"]),
        )


def _breakdowns(
    report: ExternalBenchmarkReport,
    dimension: str,
) -> tuple[FalseBlockBreakdown, ...]:
    reported = report.operation_breakdown if dimension == "operation" else report.category_breakdown
    metrics_by_key = {item.key: item.metrics for item in reported}
    result_keys = {getattr(item, dimension) for item in report.results}
    rows: list[FalseBlockBreakdown] = []
    for key in sorted(set(metrics_by_key) | result_keys):
        selected = tuple(item for item in report.results if getattr(item, dimension) == key)
        metrics = metrics_by_key.get(key, ValidationMetrics.from_results(selected))
        false_blocks = tuple(item for item in selected if _is_false_block(item))
        raw, pareto = _cause_tables(false_blocks, metrics.false_block_numerator)
        rows.append(
            FalseBlockBreakdown(
                key=key,
                numerator=metrics.false_block_numerator,
                denominator=metrics.false_block_denominator,
                ratio=_ratio(metrics.false_block_numerator, metrics.false_block_denominator),
                raw_multi_cause_counts=raw,
                pareto=pareto,
            )
        )
    return tuple(rows)


def analyze_validation_failures(report: ExternalBenchmarkReport) -> ValidationFailureAnalysis:
    """Analyze false blocks and infrastructure errors without changing metrics."""

    false_blocks = tuple(item for item in report.results if _is_false_block(item))
    raw, pareto = _cause_tables(false_blocks, report.metrics.false_block_numerator)

    infrastructure_results = tuple(item for item in report.results if not item.evaluated)
    infrastructure_counts = Counter(_classify_infrastructure(item) for item in infrastructure_results)
    classified_count = len(infrastructure_results)
    infrastructure = InfrastructureTaxonomy(
        source_count=report.metrics.infrastructure_errors,
        classified_count=classified_count,
        categories=tuple(
            InfrastructureCount(category, infrastructure_counts[category], _ratio(infrastructure_counts[category], classified_count))
            for category in INFRASTRUCTURE_CATEGORIES
        ),
    )

    return ValidationFailureAnalysis(
        metrics=report.metrics,
        primary_cause_priority=FALSE_BLOCK_CAUSE_PRIORITY,
        raw_multi_cause_counts=raw,
        pareto=pareto,
        operation_breakdown=_breakdowns(report, "operation"),
        category_breakdown=_breakdowns(report, "category"),
        infrastructure=infrastructure,
    )


__all__ = [
    "FALSE_BLOCK_CAUSE_PRIORITY",
    "INFRASTRUCTURE_CATEGORIES",
    "CauseCount",
    "ParetoEntry",
    "FalseBlockBreakdown",
    "InfrastructureCount",
    "InfrastructureTaxonomy",
    "ValidationFailureAnalysis",
    "analyze_validation_failures",
]
