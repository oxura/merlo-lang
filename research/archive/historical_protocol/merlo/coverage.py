from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from research.archive.historical_protocol.merlo.model import ProgramIR, Resolution


_RESOLUTIONS = (
    Resolution.EXACT,
    Resolution.DERIVED,
    Resolution.CONDITIONAL,
    "Observed",
    Resolution.DYNAMIC,
    Resolution.UNKNOWN,
)
_USABLE_RESOLUTIONS = frozenset({Resolution.EXACT, Resolution.DERIVED, "Observed"})
_CHANGE_SAFE_RESOLUTIONS = frozenset({Resolution.EXACT, Resolution.DERIVED})
_OPERATIONS = ("rename", "move", "change_signature")
_SIGNATURE_RELEVANT_USAGES = frozenset(
    {"CallCallee", "Callback", "Partial", "StoredValue", "Decorator"}
)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _normalized_counts(values: Iterable[str]) -> tuple[tuple[str, int], ...]:
    counts = {resolution: 0 for resolution in _RESOLUTIONS}
    for value in values:
        resolution = value if value in counts else Resolution.UNKNOWN
        counts[resolution] += 1
    return tuple((resolution, counts[resolution]) for resolution in _RESOLUTIONS)


@dataclass(frozen=True)
class ChangeSafeCoverage:
    operation: str
    safe_count: int
    total_count: int
    resolution_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if self.operation not in _OPERATIONS:
            raise ValueError(f"unsupported change operation: {self.operation}")
        if self.safe_count < 0 or self.total_count < 0:
            raise ValueError("coverage counts cannot be negative")
        if self.safe_count > self.total_count:
            raise ValueError("safe count cannot exceed total count")
        if sum(count for _, count in self.resolution_counts) != self.total_count:
            raise ValueError("resolution counts must sum to total count")

    @property
    def blocked_count(self) -> int:
        return self.total_count - self.safe_count

    @property
    def numerator(self) -> int:
        return self.safe_count

    @property
    def denominator(self) -> int:
        return self.total_count

    @property
    def ratio(self) -> float:
        return _ratio(self.safe_count, self.total_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "safe_count": self.safe_count,
            "blocked_count": self.blocked_count,
            "total_count": self.total_count,
            "numerator": self.safe_count,
            "denominator": self.total_count,
            "ratio": round(self.ratio, 6),
            "resolution_counts": dict(self.resolution_counts),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ChangeSafeCoverage":
        counts = value.get("resolution_counts", {})
        normalized = tuple(
            (resolution, int(counts.get(resolution, 0)))
            for resolution in _RESOLUTIONS
        )
        total = int(value.get("total_count", value.get("denominator", sum(dict(normalized).values()))))
        return cls(
            operation=str(value["operation"]),
            safe_count=int(value.get("safe_count", value.get("numerator", 0))),
            total_count=total,
            resolution_counts=normalized,
        )


@dataclass(frozen=True)
class SemanticCoverageReport:
    total_references: int
    exact_references: int
    usable_references: int
    resolution_counts: tuple[tuple[str, int], ...]
    change_safe_coverage: tuple[ChangeSafeCoverage, ...]

    def __post_init__(self) -> None:
        if min(self.total_references, self.exact_references, self.usable_references) < 0:
            raise ValueError("coverage counts cannot be negative")
        if self.exact_references > self.usable_references:
            raise ValueError("exact references must be usable")
        if self.usable_references > self.total_references:
            raise ValueError("usable references cannot exceed total references")
        if sum(count for _, count in self.resolution_counts) != self.total_references:
            raise ValueError("resolution counts must sum to total references")
        if tuple(item.operation for item in self.change_safe_coverage) != _OPERATIONS:
            raise ValueError("change-safe coverage must contain rename, move, and change_signature")

    @property
    def exact_ratio(self) -> float:
        return _ratio(self.exact_references, self.total_references)

    @property
    def usable_ratio(self) -> float:
        return _ratio(self.usable_references, self.total_references)

    def for_operation(self, operation: str) -> ChangeSafeCoverage:
        normalized = {
            "rename_symbol": "rename",
            "move_symbol": "move",
            "signature": "change_signature",
        }.get(operation, operation)
        for item in self.change_safe_coverage:
            if item.operation == normalized:
                return item
        raise KeyError(operation)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_references": self.total_references,
            "resolution_counts": dict(self.resolution_counts),
            "exact": {
                "count": self.exact_references,
                "denominator": self.total_references,
                "ratio": round(self.exact_ratio, 6),
            },
            "usable": {
                "count": self.usable_references,
                "denominator": self.total_references,
                "ratio": round(self.usable_ratio, 6),
            },
            "exact_ratio": round(self.exact_ratio, 6),
            "usable_ratio": round(self.usable_ratio, 6),
            "change_safe_coverage": {
                item.operation: item.to_dict() for item in self.change_safe_coverage
            },
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SemanticCoverageReport":
        counts = value.get("resolution_counts", {})
        normalized_counts = tuple(
            (resolution, int(counts.get(resolution, 0)))
            for resolution in _RESOLUTIONS
        )
        raw_operations = value.get("change_safe_coverage", {})
        operations = tuple(
            ChangeSafeCoverage.from_dict(
                {**dict(raw_operations.get(operation, {})), "operation": operation}
            )
            for operation in _OPERATIONS
        )
        exact = value.get("exact", {})
        usable = value.get("usable", {})
        return cls(
            total_references=int(value.get("total_references", sum(dict(normalized_counts).values()))),
            exact_references=int(exact.get("count", counts.get(Resolution.EXACT, 0))),
            usable_references=int(
                usable.get(
                    "count",
                    sum(counts.get(resolution, 0) for resolution in _USABLE_RESOLUTIONS),
                )
            ),
            resolution_counts=normalized_counts,
            change_safe_coverage=operations,
        )

    @classmethod
    def from_program(cls, program: ProgramIR) -> "SemanticCoverageReport":
        return semantic_coverage(program)


def semantic_coverage(program: ProgramIR) -> SemanticCoverageReport:
    reference_resolutions = tuple(reference.resolution for reference in program.references)
    counts = _normalized_counts(reference_resolutions)
    count_map = dict(counts)
    exact = count_map[Resolution.EXACT]
    usable = sum(count_map[resolution] for resolution in _USABLE_RESOLUTIONS)

    reference_safe = sum(
        reference.resolution in _CHANGE_SAFE_RESOLUTIONS
        for reference in program.references
    )
    signature_references = tuple(
        reference
        for reference in program.references
        if reference.usage in _SIGNATURE_RELEVANT_USAGES
    )
    call_counts = _normalized_counts(
        reference.resolution for reference in signature_references
    )
    call_safe = sum(
        reference.usage == "CallCallee"
        and reference.resolution in _CHANGE_SAFE_RESOLUTIONS
        for reference in signature_references
    )
    operation_coverage = (
        ChangeSafeCoverage("rename", reference_safe, len(program.references), counts),
        ChangeSafeCoverage("move", reference_safe, len(program.references), counts),
        ChangeSafeCoverage(
            "change_signature",
            call_safe,
            len(signature_references),
            call_counts,
        ),
    )
    return SemanticCoverageReport(
        total_references=len(program.references),
        exact_references=exact,
        usable_references=usable,
        resolution_counts=counts,
        change_safe_coverage=operation_coverage,
    )


compute_semantic_coverage = semantic_coverage
