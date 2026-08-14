from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from research.archive.historical_protocol.merlo.coverage import SemanticCoverageReport, semantic_coverage
from research.archive.historical_protocol.merlo.model import ProgramIR, Resolution


@dataclass(frozen=True)
class LanguageConstraintHypothesis:
    problem: str
    desired_property: str
    reference_kinds: tuple[str, ...]
    affected_references: int
    assumption: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "problem": self.problem,
            "desired_property": self.desired_property,
            "reference_kinds": list(self.reference_kinds),
            "affected_references": self.affected_references,
            "assumption": self.assumption,
        }


@dataclass(frozen=True)
class LanguageCoverageResult:
    project: str
    python_coverage: SemanticCoverageReport
    uncertain_references: int
    uncertain_kind_counts: tuple[tuple[str, int], ...]
    hazard_counts: tuple[tuple[str, int], ...]
    hypotheses: tuple[LanguageConstraintHypothesis, ...]
    addressable_upper_bound: int
    unclassified_uncertainty: int

    @property
    def upper_bound_ratio(self) -> float:
        if not self.uncertain_references:
            return 0.0
        return self.addressable_upper_bound / self.uncertain_references

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "LanguageCoverageExperiment",
            "status": "theoretical_upper_bound",
            "project": self.project,
            "python_coverage": self.python_coverage.to_dict(),
            "uncertain_references": self.uncertain_references,
            "uncertain_kind_counts": dict(self.uncertain_kind_counts),
            "hazard_counts": dict(self.hazard_counts),
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "addressable_upper_bound": {
                "count": self.addressable_upper_bound,
                "denominator": self.uncertain_references,
                "ratio": round(self.upper_bound_ratio, 6),
            },
            "unclassified_uncertainty": self.unclassified_uncertainty,
            "claim": (
                "Counts classify Python uncertainty under explicit language-design "
                "assumptions; they do not measure a Meldra Language implementation."
            ),
        }


_HYPOTHESES: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    (
        "runtime-selected imports hide the binding graph",
        "module dependencies are statically enumerable; dynamic lookup is explicit",
        ("dynamic_import",),
        "every runtime-selected module would cross an explicit capability boundary",
    ),
    (
        "wildcard imports make the imported namespace open-ended",
        "imports enumerate their exported semantic identities",
        ("wildcard_import",),
        "there is no implicit runtime mutation of a module export set",
    ),
    (
        "reflection and dynamic namespaces hide call targets",
        "reflection is represented by a typed dynamic-dispatch capability",
        ("dynamic", "dynamic_namespace", "module_getattr"),
        "all reflective dispatch goes through the explicit capability",
    ),
    (
        "string-based exports disconnect names from entities",
        "exports reference semantic identities instead of untyped strings",
        ("string_export",),
        "foreign interfaces expose an explicit adapter rather than raw name lookup",
    ),
    (
        "external package imports lack a closed semantic interface",
        "packages publish typed, revisioned semantic exports",
        ("import",),
        "foreign Python packages are accessed only through declared adapters",
    ),
    (
        "dynamic object attributes hide member identity",
        "values expose closed nominal or structural member interfaces",
        ("attribute",),
        "runtime mutation cannot add undeclared members to typed values",
    ),
    (
        "open Python name resolution leaves unresolved bindings",
        "unresolved names and ambient globals are declared capabilities",
        ("name", "unknown_name"),
        "generated code and monkey patching cannot add undeclared bindings",
    ),
)


def measure_language_coverage(program: ProgramIR, *, project: str = "") -> LanguageCoverageResult:
    uncertain = tuple(
        reference
        for reference in program.references
        if reference.resolution
        in {Resolution.CONDITIONAL, Resolution.DYNAMIC, Resolution.UNKNOWN}
    )
    kind_counts = Counter(reference.kind for reference in uncertain)
    hazard_counts = Counter(hazard.kind for hazard in program.hazards)
    classified_kinds: set[str] = set()
    hypotheses: list[LanguageConstraintHypothesis] = []
    for problem, desired_property, kinds, assumption in _HYPOTHESES:
        count = sum(kind_counts.get(kind, 0) for kind in kinds)
        if count:
            classified_kinds.update(kinds)
            hypotheses.append(
                LanguageConstraintHypothesis(
                    problem=problem,
                    desired_property=desired_property,
                    reference_kinds=kinds,
                    affected_references=count,
                    assumption=assumption,
                )
            )
    addressable = sum(
        count for kind, count in kind_counts.items() if kind in classified_kinds
    )
    return LanguageCoverageResult(
        project=project or program.root,
        python_coverage=semantic_coverage(program),
        uncertain_references=len(uncertain),
        uncertain_kind_counts=tuple(sorted(kind_counts.items())),
        hazard_counts=tuple(sorted(hazard_counts.items())),
        hypotheses=tuple(hypotheses),
        addressable_upper_bound=addressable,
        unclassified_uncertainty=len(uncertain) - addressable,
    )


def aggregate_language_coverage(
    results: Iterable[LanguageCoverageResult],
) -> dict[str, Any]:
    values = tuple(results)
    uncertain = sum(item.uncertain_references for item in values)
    addressable = sum(item.addressable_upper_bound for item in values)
    kinds: Counter[str] = Counter()
    hazards: Counter[str] = Counter()
    for item in values:
        kinds.update(dict(item.uncertain_kind_counts))
        hazards.update(dict(item.hazard_counts))
    return {
        "kind": "LanguageCoverageAggregate",
        "status": "theoretical_upper_bound",
        "projects": len(values),
        "uncertain_references": uncertain,
        "addressable_upper_bound": {
            "count": addressable,
            "denominator": uncertain,
            "ratio": round(addressable / uncertain, 6) if uncertain else 0.0,
        },
        "unclassified_uncertainty": uncertain - addressable,
        "uncertain_kind_counts": dict(sorted(kinds.items())),
        "hazard_counts": dict(sorted(hazards.items())),
        "claim": "No language implementation was built or measured.",
    }
