from __future__ import annotations

import io
import json
import re
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

from research.archive.historical_protocol.merlo.analyzer import AnalysisError, scan_python
from research.archive.historical_protocol.merlo.model import Entity, IdentityStatus, ProgramIR


@dataclass(frozen=True, order=True)
class GitCommitPair:
    old_commit: str
    new_commit: str

    def to_dict(self) -> dict[str, str]:
        return {"old_commit": self.old_commit, "new_commit": self.new_commit}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GitCommitPair":
        return cls(old_commit=str(value["old_commit"]), new_commit=str(value["new_commit"]))


@dataclass(frozen=True)
class InfrastructureError:
    operation: str
    message: str
    pair: GitCommitPair | None = None
    returncode: int | None = None
    stderr: str = ""
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "message": self.message,
            "pair": self.pair.to_dict() if self.pair else None,
            "returncode": self.returncode,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "InfrastructureError":
        return cls(
            operation=str(value["operation"]),
            message=str(value["message"]),
            pair=(GitCommitPair.from_dict(value["pair"]) if value.get("pair") else None),
            returncode=(int(value["returncode"]) if value.get("returncode") is not None else None),
            stderr=str(value.get("stderr", "")),
            timed_out=bool(value.get("timed_out", False)),
        )


@dataclass(frozen=True)
class GitCommandResult:
    argv: tuple[str, ...]
    cwd: str
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return not self.timed_out and not self.error and self.returncode == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "cwd": self.cwd,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "error": self.error,
            "succeeded": self.succeeded,
        }


def run_git(argv: Sequence[str], cwd: str | Path, timeout: float) -> GitCommandResult:
    """Run an explicit git argv boundary; errors are returned as data."""

    command = _validate_argv(argv)
    directory = str(Path(cwd))
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    try:
        completed = subprocess.run(
            command,
            cwd=directory,
            timeout=timeout,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return GitCommandResult(
            argv=command,
            cwd=directory,
            returncode=None,
            stdout=_timeout_text(exc.stdout),
            stderr=_timeout_text(exc.stderr),
            timed_out=True,
            error="git command timed out",
        )
    except OSError as exc:
        return GitCommandResult(
            argv=command,
            cwd=directory,
            returncode=None,
            stdout="",
            stderr="",
            error=f"{type(exc).__name__}: {exc}",
        )
    return GitCommandResult(
        argv=command,
        cwd=directory,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


@dataclass(frozen=True)
class CommitPairSelection:
    pairs: tuple[GitCommitPair, ...]
    infrastructure_errors: tuple[InfrastructureError, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.infrastructure_errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "pairs": [item.to_dict() for item in self.pairs],
            "infrastructure_errors": [item.to_dict() for item in self.infrastructure_errors],
            "pair_count": len(self.pairs),
        }
    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CommitPairSelection":
        return cls(
            pairs=tuple(GitCommitPair.from_dict(item) for item in value.get("pairs", ())),
            infrastructure_errors=tuple(
                InfrastructureError.from_dict(item)
                for item in value.get("infrastructure_errors", ())
            ),
        )



def select_commit_pairs(
    checkout: str | Path,
    *,
    max_pairs: int = 10,
    git_argv: Sequence[str] = ("git",),
    timeout: float = 30.0,
) -> CommitPairSelection:
    if max_pairs < 1:
        raise ValueError("max_pairs must be positive")
    root = Path(checkout).resolve()
    command = (*_validate_argv(git_argv), "rev-list", "--first-parent", "--reverse", "HEAD")
    result = run_git(command, root, timeout)
    if not result.succeeded:
        return CommitPairSelection(
            pairs=(),
            infrastructure_errors=(_command_error("select_commit_pairs", result),),
        )
    commits = tuple(line.strip() for line in result.stdout.splitlines() if line.strip())
    if len(commits) < 2:
        return CommitPairSelection(
            pairs=(),
            infrastructure_errors=(
                InfrastructureError(
                    operation="select_commit_pairs",
                    message="checkout has fewer than two first-parent commits",
                ),
            ),
        )
    pairs = tuple(GitCommitPair(left, right) for left, right in zip(commits, commits[1:]))
    return CommitPairSelection(pairs=pairs[-max_pairs:])


@dataclass(frozen=True)
class GitIdentityHint:
    kind: str
    source_locators: tuple[str, ...]
    target_locators: tuple[str, ...]
    confidence: float
    ambiguous: bool
    provenance: str

    def __post_init__(self) -> None:
        sources = tuple(sorted(set(self.source_locators)))
        targets = tuple(sorted(set(self.target_locators)))
        if not sources or not targets:
            raise ValueError("git identity hints require non-empty endpoints")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("git hint confidence must be between 0 and 1")
        object.__setattr__(self, "source_locators", sources)
        object.__setattr__(self, "target_locators", targets)
        object.__setattr__(self, "confidence", float(self.confidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_locators": list(self.source_locators),
            "target_locators": list(self.target_locators),
            "confidence": round(self.confidence, 6),
            "ambiguous": self.ambiguous,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GitIdentityHint":
        return cls(
            kind=str(value["kind"]),
            source_locators=tuple(str(item) for item in value["source_locators"]),
            target_locators=tuple(str(item) for item in value["target_locators"]),
            confidence=float(value["confidence"]),
            ambiguous=bool(value["ambiguous"]),
            provenance=str(value["provenance"]),
        )


@dataclass(frozen=True)
class SplitMergeHypothesisCandidate:
    kind: str
    source_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    confidence: float
    matched_feature_count: int
    whole_feature_count: int

    def __post_init__(self) -> None:
        if self.kind not in {"split", "merge"}:
            raise ValueError(f"unknown split/merge hypothesis kind: {self.kind}")
        sources = tuple(sorted(set(self.source_ids)))
        targets = tuple(sorted(set(self.target_ids)))
        if not sources or not targets:
            raise ValueError("split/merge hypotheses require non-empty endpoints")
        if self.kind == "split" and (len(sources) != 1 or len(targets) < 2):
            raise ValueError("split hypotheses require 1:N endpoints")
        if self.kind == "merge" and (len(sources) < 2 or len(targets) != 1):
            raise ValueError("merge hypotheses require N:1 endpoints")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("hypothesis confidence must be between 0 and 1")
        if (
            self.matched_feature_count < 0
            or self.whole_feature_count < 0
            or self.matched_feature_count > self.whole_feature_count
        ):
            raise ValueError("invalid hypothesis feature denominator")
        object.__setattr__(self, "source_ids", sources)
        object.__setattr__(self, "target_ids", targets)
        object.__setattr__(self, "confidence", float(self.confidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_ids": list(self.source_ids),
            "target_ids": list(self.target_ids),
            "confidence": round(self.confidence, 6),
            "matched_feature_count": self.matched_feature_count,
            "whole_feature_count": self.whole_feature_count,
        }
    @classmethod
    def from_dict(
        cls, value: dict[str, Any]
    ) -> "SplitMergeHypothesisCandidate":
        return cls(
            kind=str(value["kind"]),
            source_ids=tuple(str(item) for item in value["source_ids"]),
            target_ids=tuple(str(item) for item in value["target_ids"]),
            confidence=float(value["confidence"]),
            matched_feature_count=int(value["matched_feature_count"]),
            whole_feature_count=int(value["whole_feature_count"]),
        )



@dataclass(frozen=True)
class SplitMergeHypothesisMeasurement:
    old_entity_count: int
    new_entity_count: int
    unmatched_old_count: int
    unmatched_new_count: int
    split_groups_assessed: int
    merge_groups_assessed: int
    candidates: tuple[SplitMergeHypothesisCandidate, ...] = ()

    def __post_init__(self) -> None:
        counts = (
            self.old_entity_count,
            self.new_entity_count,
            self.unmatched_old_count,
            self.unmatched_new_count,
            self.split_groups_assessed,
            self.merge_groups_assessed,
        )
        if any(item < 0 for item in counts):
            raise ValueError("split/merge measurement counts cannot be negative")
        if self.unmatched_old_count > self.old_entity_count:
            raise ValueError("unmatched old denominator exceeds old entity count")
        if self.unmatched_new_count > self.new_entity_count:
            raise ValueError("unmatched new denominator exceeds new entity count")
        candidates = tuple(
            sorted(
                self.candidates,
                key=lambda item: (
                    item.kind,
                    item.source_ids,
                    item.target_ids,
                    -item.confidence,
                ),
            )
        )
        if sum(item.kind == "split" for item in candidates) > self.split_groups_assessed:
            raise ValueError("split candidates exceed assessed group denominator")
        if sum(item.kind == "merge" for item in candidates) > self.merge_groups_assessed:
            raise ValueError("merge candidates exceed assessed group denominator")
        object.__setattr__(self, "candidates", candidates)

    @property
    def split_candidate_count(self) -> int:
        return sum(item.kind == "split" for item in self.candidates)

    @property
    def merge_candidate_count(self) -> int:
        return sum(item.kind == "merge" for item in self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "old_entity_count": self.old_entity_count,
            "new_entity_count": self.new_entity_count,
            "unmatched_old_count": self.unmatched_old_count,
            "unmatched_new_count": self.unmatched_new_count,
            "split_groups_assessed": self.split_groups_assessed,
            "merge_groups_assessed": self.merge_groups_assessed,
            "split_candidate_count": self.split_candidate_count,
            "merge_candidate_count": self.merge_candidate_count,
            "candidates": [item.to_dict() for item in self.candidates],
        }
    @classmethod
    def from_dict(
        cls, value: dict[str, Any]
    ) -> "SplitMergeHypothesisMeasurement":
        return cls(
            old_entity_count=int(value["old_entity_count"]),
            new_entity_count=int(value["new_entity_count"]),
            unmatched_old_count=int(value["unmatched_old_count"]),
            unmatched_new_count=int(value["unmatched_new_count"]),
            split_groups_assessed=int(value["split_groups_assessed"]),
            merge_groups_assessed=int(value["merge_groups_assessed"]),
            candidates=tuple(
                SplitMergeHypothesisCandidate.from_dict(item)
                for item in value.get("candidates", ())
            ),
        )



def measure_split_merge_hypotheses(
    old: ProgramIR,
    new: ProgramIR,
    *,
    minimum_confidence: float = 0.72,
    max_group_size: int = 4,
) -> SplitMergeHypothesisMeasurement:
    """Measure read-only split/merge hypotheses without creating lineage data."""

    if not 0.0 <= minimum_confidence <= 1.0:
        raise ValueError("minimum_confidence must be between 0 and 1")
    if max_group_size < 2:
        raise ValueError("max_group_size must be at least 2")
    old_ids = {item.id for item in old.entities}
    new_ids = {item.id for item in new.entities}
    old_unmatched = tuple(
        sorted(
            (item for item in old.entities if item.id not in new_ids),
            key=lambda item: (item.kind, item.fqname, item.id),
        )
    )
    new_unmatched = tuple(
        sorted(
            (item for item in new.entities if item.id not in old_ids),
            key=lambda item: (item.kind, item.fqname, item.id),
        )
    )
    candidates: list[SplitMergeHypothesisCandidate] = []
    split_assessed = 0
    merge_assessed = 0
    for whole in old_unmatched:
        compatible = tuple(item for item in new_unmatched if item.kind == whole.kind)
        for group in _entity_groups(compatible, max_group_size):
            split_assessed += 1
            assessment = _assess_hypothesis(whole, group)
            if assessment is not None and assessment[0] >= minimum_confidence:
                candidates.append(
                    SplitMergeHypothesisCandidate(
                        kind="split",
                        source_ids=(whole.id,),
                        target_ids=tuple(sorted(item.id for item in group)),
                        confidence=assessment[0],
                        matched_feature_count=assessment[1],
                        whole_feature_count=assessment[2],
                    )
                )
    for whole in new_unmatched:
        compatible = tuple(item for item in old_unmatched if item.kind == whole.kind)
        for group in _entity_groups(compatible, max_group_size):
            merge_assessed += 1
            assessment = _assess_hypothesis(whole, group)
            if assessment is not None and assessment[0] >= minimum_confidence:
                candidates.append(
                    SplitMergeHypothesisCandidate(
                        kind="merge",
                        source_ids=tuple(sorted(item.id for item in group)),
                        target_ids=(whole.id,),
                        confidence=assessment[0],
                        matched_feature_count=assessment[1],
                        whole_feature_count=assessment[2],
                    )
                )
    ordered = tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.kind,
                item.source_ids,
                item.target_ids,
                -item.confidence,
            ),
        )
    )
    return SplitMergeHypothesisMeasurement(
        old_entity_count=len(old.entities),
        new_entity_count=len(new.entities),
        unmatched_old_count=len(old_unmatched),
        unmatched_new_count=len(new_unmatched),
        split_groups_assessed=split_assessed,
        merge_groups_assessed=merge_assessed,
        candidates=ordered,
    )


@dataclass(frozen=True, order=True)
class ResolverLink:
    old_locator: str
    new_locator: str
    status: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "old_locator": self.old_locator,
            "new_locator": self.new_locator,
            "status": self.status,
            "score": round(self.score, 6),
        }
    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ResolverLink":
        return cls(
            old_locator=str(value["old_locator"]),
            new_locator=str(value["new_locator"]),
            status=str(value["status"]),
            score=float(value["score"]),
        )



@dataclass(frozen=True)
class GitIdentityMetrics:
    true_positive_links: int
    false_positive_links: int
    predicted_link_count: int
    ground_truth_link_count: int
    matched_ground_truth_links: int
    ground_truth_ambiguous_count: int
    resolver_ambiguous_count: int
    resolver_no_link_count: int
    new_entity_count: int

    def __post_init__(self) -> None:
        values = (
            self.true_positive_links,
            self.false_positive_links,
            self.predicted_link_count,
            self.ground_truth_link_count,
            self.matched_ground_truth_links,
            self.ground_truth_ambiguous_count,
            self.resolver_ambiguous_count,
            self.resolver_no_link_count,
            self.new_entity_count,
        )
        if any(value < 0 for value in values):
            raise ValueError("identity metric counts cannot be negative")
        if self.true_positive_links + self.false_positive_links != self.predicted_link_count:
            raise ValueError("predicted link denominator must equal TP + FP")

    @property
    def precision(self) -> float | None:
        if self.predicted_link_count == 0:
            return None
        return self.true_positive_links / self.predicted_link_count

    @property
    def recall(self) -> float | None:
        if self.ground_truth_link_count == 0:
            return None
        return self.matched_ground_truth_links / self.ground_truth_link_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "true_positive_links": self.true_positive_links,
            "false_positive_links": self.false_positive_links,
            "predicted_link_count": self.predicted_link_count,
            "precision_numerator": self.true_positive_links,
            "precision_denominator": self.predicted_link_count,
            "precision": round(self.precision, 6) if self.precision is not None else None,
            "ground_truth_link_count": self.ground_truth_link_count,
            "matched_ground_truth_links": self.matched_ground_truth_links,
            "recall_numerator": self.matched_ground_truth_links,
            "recall_denominator": self.ground_truth_link_count,
            "recall": round(self.recall, 6) if self.recall is not None else None,
            "ground_truth_ambiguous_count": self.ground_truth_ambiguous_count,
            "resolver_ambiguous_count": self.resolver_ambiguous_count,
            "resolver_no_link_count": self.resolver_no_link_count,
            "new_entity_count": self.new_entity_count,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GitIdentityMetrics":
        return cls(
            true_positive_links=int(value["true_positive_links"]),
            false_positive_links=int(value["false_positive_links"]),
            predicted_link_count=int(value["predicted_link_count"]),
            ground_truth_link_count=int(value["ground_truth_link_count"]),
            matched_ground_truth_links=int(value["matched_ground_truth_links"]),
            ground_truth_ambiguous_count=int(value["ground_truth_ambiguous_count"]),
            resolver_ambiguous_count=int(value["resolver_ambiguous_count"]),
            resolver_no_link_count=int(value["resolver_no_link_count"]),
            new_entity_count=int(value["new_entity_count"]),
        )


@dataclass(frozen=True)
class GitIdentityPairResult:
    pair: GitCommitPair
    hints: tuple[GitIdentityHint, ...]
    resolver_links: tuple[ResolverLink, ...]
    metrics: GitIdentityMetrics
    split_merge_hypotheses: SplitMergeHypothesisMeasurement

    def to_dict(self) -> dict[str, Any]:
        return {
            "pair": self.pair.to_dict(),
            "hints": [item.to_dict() for item in self.hints],
            "resolver_links": [item.to_dict() for item in self.resolver_links],
            "metrics": self.metrics.to_dict(),
            "evolved_identity_metrics": _evolved_link_metrics(
                self.hints, self.resolver_links
            ),
            "split_merge_hypotheses": self.split_merge_hypotheses.to_dict(),
        }
    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GitIdentityPairResult":
        return cls(
            pair=GitCommitPair.from_dict(value["pair"]),
            hints=tuple(GitIdentityHint.from_dict(item) for item in value.get("hints", ())),
            resolver_links=tuple(
                ResolverLink.from_dict(item) for item in value.get("resolver_links", ())
            ),
            metrics=GitIdentityMetrics.from_dict(value["metrics"]),
            split_merge_hypotheses=SplitMergeHypothesisMeasurement.from_dict(
                value["split_merge_hypotheses"]
            ),
        )



@dataclass(frozen=True)
class GitIdentityBenchmarkReport:
    checkout: str
    requested_pair_count: int
    pair_results: tuple[GitIdentityPairResult, ...]
    metrics: GitIdentityMetrics
    split_merge_hypotheses: SplitMergeHypothesisMeasurement
    infrastructure_errors: tuple[InfrastructureError, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.infrastructure_errors

    def to_dict(self) -> dict[str, Any]:
        # Metrics carry every raw numerator/denominator; infrastructure failure
        # is orthogonal to semantic success and is never folded into precision.
        return {
            "checkout": self.checkout,
            "requested_pair_count": self.requested_pair_count,
            "completed_pair_count": len(self.pair_results),
            "pair_results": [item.to_dict() for item in self.pair_results],
            "metrics": self.metrics.to_dict(),
            "split_merge_hypotheses": self.split_merge_hypotheses.to_dict(),
            "evolved_identity_metrics": _aggregate_evolved_metrics(
                self.pair_results
            ),
            "infrastructure_error_count": len(self.infrastructure_errors),
            "infrastructure_errors": [item.to_dict() for item in self.infrastructure_errors],
            "ok": self.ok,
        }
    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GitIdentityBenchmarkReport":
        return cls(
            checkout=str(value["checkout"]),
            requested_pair_count=int(value["requested_pair_count"]),
            pair_results=tuple(
                GitIdentityPairResult.from_dict(item)
                for item in value.get("pair_results", ())
            ),
            metrics=GitIdentityMetrics.from_dict(value["metrics"]),
            split_merge_hypotheses=SplitMergeHypothesisMeasurement.from_dict(
                value["split_merge_hypotheses"]
            ),
            infrastructure_errors=tuple(
                InfrastructureError.from_dict(item)
                for item in value.get("infrastructure_errors", ())
            ),
        )



_EMPTY_METRICS = GitIdentityMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0)
_EMPTY_HYPOTHESES = SplitMergeHypothesisMeasurement(0, 0, 0, 0, 0, 0)


def benchmark_git_identity(
    checkout: str | Path,
    *,
    commit_pairs: Iterable[GitCommitPair | tuple[str, str]] | None = None,
    max_pairs: int = 10,
    git_argv: Sequence[str] = ("git",),
    timeout: float = 30.0,
) -> GitIdentityBenchmarkReport:
    root = Path(checkout).resolve()
    prefix = _validate_argv(git_argv)
    errors: list[InfrastructureError] = []
    if commit_pairs is None:
        selection = select_commit_pairs(
            root, max_pairs=max_pairs, git_argv=prefix, timeout=timeout
        )
        pairs = selection.pairs
        errors.extend(selection.infrastructure_errors)
    else:
        pairs = tuple(_coerce_pair(item) for item in commit_pairs)
    results: list[GitIdentityPairResult] = []
    for pair in pairs:
        result, pair_errors = _benchmark_pair(root, pair, prefix, timeout)
        errors.extend(pair_errors)
        if result is not None:
            results.append(result)
    aggregate = _sum_metrics(item.metrics for item in results)
    aggregate_hypotheses = _sum_hypotheses(
        item.split_merge_hypotheses for item in results
    )
    return GitIdentityBenchmarkReport(
        checkout=str(root),
        requested_pair_count=len(pairs),
        pair_results=tuple(results),
        metrics=aggregate,
        split_merge_hypotheses=aggregate_hypotheses,
        infrastructure_errors=tuple(sorted(errors, key=_error_key)),
    )


def _benchmark_pair(
    checkout: Path,
    pair: GitCommitPair,
    git_argv: tuple[str, ...],
    timeout: float,
) -> tuple[GitIdentityPairResult | None, tuple[InfrastructureError, ...]]:
    resolved_pair, resolution_error = _resolve_pair(
        checkout, pair, git_argv, timeout
    )
    if resolution_error is not None:
        return None, (resolution_error,)
    assert resolved_pair is not None
    pair = resolved_pair
    with tempfile.TemporaryDirectory(prefix="meldra-git-identity-") as temporary:
        base = Path(temporary)
        old_root = base / "old"
        new_root = base / "new"
        old_error = _materialize_snapshot(checkout, pair.old_commit, old_root, git_argv, timeout, pair)
        new_error = _materialize_snapshot(checkout, pair.new_commit, new_root, git_argv, timeout, pair)
        snapshot_errors = tuple(item for item in (old_error, new_error) if item is not None)
        if snapshot_errors:
            return None, snapshot_errors
        try:
            old_program = scan_python(old_root)
            new_program = scan_python(new_root, previous=old_program)
        except (AnalysisError, OSError, UnicodeError) as exc:
            return None, (
                InfrastructureError(
                    operation="analyze_snapshots",
                    message=f"{type(exc).__name__}: {exc}",
                    pair=pair,
                ),
            )
        hints, hint_error = _ground_truth_hints(
            checkout, pair, old_program, new_program, git_argv, timeout
        )
        if hint_error is not None:
            return None, (hint_error,)
        links = _resolver_links(new_program)
        metrics = _score_links(new_program, hints, links)
        hypotheses = measure_split_merge_hypotheses(old_program, new_program)
        return GitIdentityPairResult(pair, hints, links, metrics, hypotheses), ()


def _resolve_pair(
    checkout: Path,
    pair: GitCommitPair,
    git_argv: tuple[str, ...],
    timeout: float,
) -> tuple[GitCommitPair | None, InfrastructureError | None]:
    resolved: list[str] = []
    for revision in (pair.old_commit, pair.new_commit):
        result = run_git(
            (
                *git_argv,
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{revision}^{{commit}}",
            ),
            checkout,
            timeout,
        )
        if not result.succeeded:
            return None, _command_error("resolve_commit", result, pair)
        object_id = result.stdout.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", object_id):
            return None, InfrastructureError(
                operation="resolve_commit",
                message="git rev-parse returned an invalid object ID",
                pair=pair,
            )
        resolved.append(object_id.lower())
    return GitCommitPair(resolved[0], resolved[1]), None


def _materialize_snapshot(
    checkout: Path,
    commit: str,
    destination: Path,
    git_argv: tuple[str, ...],
    timeout: float,
    pair: GitCommitPair,
) -> InfrastructureError | None:
    command = (*git_argv, "archive", "--format=tar", commit)
    raw, returncode, stderr, timed_out, error = _run_binary(command, checkout, timeout)
    if timed_out or error or returncode != 0:
        return InfrastructureError(
            operation="materialize_snapshot",
            message=error or f"git archive failed for {commit}",
            pair=pair,
            returncode=returncode,
            stderr=stderr,
            timed_out=timed_out,
        )
    destination.mkdir(parents=True, exist_ok=True)
    try:
        _extract_safe_tar(raw, destination)
    except (tarfile.TarError, OSError, ValueError) as exc:
        return InfrastructureError(
            operation="extract_snapshot",
            message=f"{type(exc).__name__}: {exc}",
            pair=pair,
        )
    return None


def _ground_truth_hints(
    checkout: Path,
    pair: GitCommitPair,
    old: ProgramIR,
    new: ProgramIR,
    git_argv: tuple[str, ...],
    timeout: float,
) -> tuple[tuple[GitIdentityHint, ...], InfrastructureError | None]:
    name_status = run_git(
        (
            *git_argv,
            "diff",
            "--find-renames=40%",
            "--find-copies=40%",
            "--find-copies-harder",
            "--name-status",
            pair.old_commit,
            pair.new_commit,
            "--",
        ),
        checkout,
        timeout,
    )
    patch = run_git(
        (
            *git_argv,
            "diff",
            "--find-renames=40%",
            "--find-copies=40%",
            "--find-copies-harder",
            "--unified=0",
            pair.old_commit,
            pair.new_commit,
            "--",
        ),
        checkout,
        timeout,
    )
    for operation, result in (("git_diff_name_status", name_status), ("git_diff_patch", patch)):
        if not result.succeeded:
            return (), _command_error(operation, result, pair)

    hints: list[GitIdentityHint] = []
    old_by_path = _entities_by_path(old)
    new_by_path = _entities_by_path(new)
    for status, paths in _parse_name_status(name_status.stdout):
        if status.startswith(("R", "C")) and len(paths) == 2:
            source_path, target_path = paths
            copied = status.startswith("C")
            for source in old_by_path.get(source_path, ()):
                matches = tuple(
                    target
                    for target in new_by_path.get(target_path, ())
                    if target.kind == source.kind and target.qualname == source.qualname
                )
                if len(matches) == 1:
                    hints.append(
                        GitIdentityHint(
                            kind="Copied" if copied else "Moved",
                            source_locators=(source.fqname,),
                            target_locators=(matches[0].fqname,),
                            confidence=0.7 if copied else 0.9,
                            ambiguous=copied,
                            provenance="git diff --find-renames/--find-copies",
                        )
                    )

    for source_names, target_names, old_path, new_path in _parse_definition_changes(
        patch.stdout
    ):
        source_name_set = set(source_names)
        target_name_set = set(target_names)
        sources = tuple(
            entity.fqname
            for entity in old_by_path.get(old_path, ())
            if entity.name in source_name_set
        )
        targets = tuple(
            entity.fqname
            for entity in new_by_path.get(new_path, ())
            if entity.name in target_name_set
        )
        if sources and targets:
            hints.append(
                GitIdentityHint(
                    kind="Renamed" if len(sources) == len(targets) == 1 else "AmbiguousPatch",
                    source_locators=sources,
                    target_locators=targets,
                    confidence=0.85 if len(sources) == len(targets) == 1 else 0.5,
                    ambiguous=not (len(sources) == len(targets) == 1),
                    provenance="git diff definition hunk",
                )
            )

    # Unchanged semantic addresses are strong preservation ground truth even if
    # their bodies changed. They are excluded when a patch explicitly renames
    # the definition.
    explicitly_changed = {
        locator
        for hint in hints
        for locator in (*hint.source_locators, *hint.target_locators)
    }
    new_by_locator = {entity.fqname: entity for entity in new.entities}
    for source in old.entities:
        target = new_by_locator.get(source.fqname)
        if (
            target is not None
            and target.kind == source.kind
            and source.fqname not in explicitly_changed
        ):
            hints.append(
                GitIdentityHint(
                    kind="Preserved",
                    source_locators=(source.fqname,),
                    target_locators=(target.fqname,),
                    confidence=0.95,
                    ambiguous=False,
                    provenance="unchanged semantic address across git commits",
                )
            )

    unique = {json.dumps(item.to_dict(), sort_keys=True): item for item in hints}
    return tuple(sorted(unique.values(), key=_hint_key)), None


def _resolver_links(program: ProgramIR) -> tuple[ResolverLink, ...]:
    links = [
        ResolverLink(
            old_locator=relation.old_locator,
            new_locator=relation.new_locator,
            status=relation.status,
            score=relation.score,
        )
        for relation in program.identity_relations
        if relation.old_locator
        and relation.new_locator
        and relation.status in {IdentityStatus.EXACT, IdentityStatus.PROBABLE}
    ]
    return tuple(sorted(set(links)))


def _evolved_link_metrics(
    hints: Iterable[GitIdentityHint],
    links: Iterable[ResolverLink],
) -> dict[str, Any]:
    """Score only rename/move identity, excluding trivial preserved addresses."""
    truth = {
        (hint.source_locators[0], hint.target_locators[0])
        for hint in hints
        if hint.kind != "Preserved"
        and not hint.ambiguous
        and len(hint.source_locators) == 1
        and len(hint.target_locators) == 1
    }
    predicted = {
        (item.old_locator, item.new_locator)
        for item in links
        if item.old_locator != item.new_locator
    }
    true_positive = len(truth & predicted)
    false_positive = len(predicted - truth)
    false_negative = len(truth - predicted)
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    return {
        "true_positive_links": true_positive,
        "false_positive_links": false_positive,
        "false_negative_links": false_negative,
        "predicted_changed_links": len(predicted),
        "ground_truth_changed_links": len(truth),
        "precision_numerator": true_positive,
        "precision_denominator": precision_denominator,
        "precision": (
            round(true_positive / precision_denominator, 6)
            if precision_denominator
            else None
        ),
        "recall_numerator": true_positive,
        "recall_denominator": recall_denominator,
        "recall": (
            round(true_positive / recall_denominator, 6)
            if recall_denominator
            else None
        ),
        "ambiguous_changed_hints": sum(
            hint.kind != "Preserved" and hint.ambiguous for hint in hints
        ),
    }


def _aggregate_evolved_metrics(
    results: Iterable[GitIdentityPairResult],
) -> dict[str, Any]:
    rows = tuple(
        _evolved_link_metrics(item.hints, item.resolver_links)
        for item in results
    )
    true_positive = sum(item["true_positive_links"] for item in rows)
    false_positive = sum(item["false_positive_links"] for item in rows)
    false_negative = sum(item["false_negative_links"] for item in rows)
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    return {
        "true_positive_links": true_positive,
        "false_positive_links": false_positive,
        "false_negative_links": false_negative,
        "predicted_changed_links": sum(
            item["predicted_changed_links"] for item in rows
        ),
        "ground_truth_changed_links": sum(
            item["ground_truth_changed_links"] for item in rows
        ),
        "precision_numerator": true_positive,
        "precision_denominator": precision_denominator,
        "precision": (
            round(true_positive / precision_denominator, 6)
            if precision_denominator
            else None
        ),
        "recall_numerator": true_positive,
        "recall_denominator": recall_denominator,
        "recall": (
            round(true_positive / recall_denominator, 6)
            if recall_denominator
            else None
        ),
        "ambiguous_changed_hints": sum(
            item["ambiguous_changed_hints"] for item in rows
        ),
    }


def _score_links(
    new: ProgramIR,
    hints: tuple[GitIdentityHint, ...],
    links: tuple[ResolverLink, ...],
) -> GitIdentityMetrics:
    truth = {
        (hint.source_locators[0], hint.target_locators[0])
        for hint in hints
        if not hint.ambiguous
        and len(hint.source_locators) == 1
        and len(hint.target_locators) == 1
    }
    predicted = {(item.old_locator, item.new_locator) for item in links}
    true_positive = len(predicted & truth)
    matched = len(truth & predicted)
    linked_new_ids = {
        relation.new_id
        for relation in new.identity_relations
        if relation.new_id is not None
        and relation.status in {IdentityStatus.EXACT, IdentityStatus.PROBABLE}
    }
    ambiguous = sum(
        1 for relation in new.identity_relations if relation.status == IdentityStatus.AMBIGUOUS
    )
    no_link = sum(1 for entity in new.entities if entity.id not in linked_new_ids)
    return GitIdentityMetrics(
        true_positive_links=true_positive,
        false_positive_links=len(predicted) - true_positive,
        predicted_link_count=len(predicted),
        ground_truth_link_count=len(truth),
        matched_ground_truth_links=matched,
        ground_truth_ambiguous_count=sum(1 for item in hints if item.ambiguous),
        resolver_ambiguous_count=ambiguous,
        resolver_no_link_count=no_link,
        new_entity_count=len(new.entities),
    )


def _sum_metrics(metrics: Iterable[GitIdentityMetrics]) -> GitIdentityMetrics:
    items = tuple(metrics)
    if not items:
        return _EMPTY_METRICS
    return GitIdentityMetrics(
        true_positive_links=sum(item.true_positive_links for item in items),
        false_positive_links=sum(item.false_positive_links for item in items),
        predicted_link_count=sum(item.predicted_link_count for item in items),
        ground_truth_link_count=sum(item.ground_truth_link_count for item in items),
        matched_ground_truth_links=sum(item.matched_ground_truth_links for item in items),
        ground_truth_ambiguous_count=sum(item.ground_truth_ambiguous_count for item in items),
        resolver_ambiguous_count=sum(item.resolver_ambiguous_count for item in items),
        resolver_no_link_count=sum(item.resolver_no_link_count for item in items),
        new_entity_count=sum(item.new_entity_count for item in items),
    )


def _sum_hypotheses(
    measurements: Iterable[SplitMergeHypothesisMeasurement],
) -> SplitMergeHypothesisMeasurement:
    items = tuple(measurements)
    if not items:
        return _EMPTY_HYPOTHESES
    return SplitMergeHypothesisMeasurement(
        old_entity_count=sum(item.old_entity_count for item in items),
        new_entity_count=sum(item.new_entity_count for item in items),
        unmatched_old_count=sum(item.unmatched_old_count for item in items),
        unmatched_new_count=sum(item.unmatched_new_count for item in items),
        split_groups_assessed=sum(item.split_groups_assessed for item in items),
        merge_groups_assessed=sum(item.merge_groups_assessed for item in items),
        candidates=tuple(
            candidate for item in items for candidate in item.candidates
        ),
    )


def _entity_groups(
    entities: tuple[Entity, ...], max_group_size: int
) -> Iterable[tuple[Entity, ...]]:
    for size in range(2, min(len(entities), max_group_size) + 1):
        yield from combinations(entities, size)


def _assess_hypothesis(
    whole: Entity, parts: tuple[Entity, ...]
) -> tuple[float, int, int] | None:
    whole_features = _identity_feature_set(whole)
    part_features = tuple(_identity_feature_set(item) for item in parts)
    if not whole_features or any(not item for item in part_features):
        return None
    containment = tuple(
        len(item & whole_features) / len(item) for item in part_features
    )
    if min(containment) < 0.58:
        return None
    combined = set().union(*part_features)
    matched = len(whole_features & combined)
    coverage = matched / len(whole_features)
    if coverage < 0.68:
        return None
    for index, features in enumerate(part_features):
        siblings = set().union(
            *(item for sibling, item in enumerate(part_features) if sibling != index)
        )
        if not features - siblings:
            return None
    overlaps = [
        len(left & right) / len(left | right)
        for left, right in combinations(part_features, 2)
    ]
    mean_overlap = sum(overlaps) / len(overlaps) if overlaps else 1.0
    confidence = min(
        0.95,
        0.45 * coverage
        + 0.45 * min(containment)
        + 0.10 * (1.0 - mean_overlap),
    )
    return confidence, matched, len(whole_features)


def _identity_feature_set(entity: Entity) -> frozenset[str]:
    features = entity.identity_features
    values: set[str] = set()
    node_kinds = features.get("node_kinds", {})
    if isinstance(node_kinds, dict):
        values.update(
            f"node:{name}"
            for name, count in node_kinds.items()
            if int(count) > 0 and name not in _STRUCTURAL_NODE_KINDS
        )
    for field in ("references", "calls"):
        entries = features.get(field, ())
        if isinstance(entries, (list, tuple, set, frozenset)):
            values.update(f"{field}:{entry}" for entry in entries)
    shape = str(features.get("semantic_shape", ""))
    values.update(
        f"shape:{token}"
        for token in _SHAPE_TOKEN.findall(shape)
        if len(token) > 1 and token not in _STRUCTURAL_TOKENS
    )
    return frozenset(values)


def _entities_by_path(program: ProgramIR) -> dict[str, tuple[Entity, ...]]:
    grouped: dict[str, list[Entity]] = {}
    for entity in program.entities:
        grouped.setdefault(entity.file, []).append(entity)
    return {
        path: tuple(sorted(entities, key=lambda item: (item.qualname, item.id)))
        for path, entities in grouped.items()
    }


def _parse_name_status(output: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    records: list[tuple[str, tuple[str, ...]]] = []
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2:
            records.append((fields[0], tuple(fields[1:])))
    return tuple(records)


def _parse_definition_changes(
    output: str,
) -> tuple[tuple[tuple[str, ...], tuple[str, ...], str, str], ...]:
    results: list[tuple[tuple[str, ...], tuple[str, ...], str, str]] = []
    old_path = ""
    new_path = ""
    deleted: list[str] = []
    added: list[str] = []

    def flush() -> None:
        if deleted and added and deleted != added:
            results.append(
                (
                    tuple(sorted(set(deleted))),
                    tuple(sorted(set(added))),
                    old_path,
                    new_path,
                )
            )
        deleted.clear()
        added.clear()

    for line in output.splitlines():
        if line.startswith("diff --git "):
            flush()
        elif line.startswith("--- "):
            flush()
            old_path = _diff_path(line[4:])
        elif line.startswith("+++ "):
            new_path = _diff_path(line[4:])
        elif line.startswith("@@"):
            flush()
        elif line.startswith("-") and not line.startswith("---"):
            match = _DEFINITION.match(line[1:])
            if match:
                deleted.append(match.group("name"))
        elif line.startswith("+") and not line.startswith("+++"):
            match = _DEFINITION.match(line[1:])
            if match:
                added.append(match.group("name"))
    flush()
    return tuple(results)


def _diff_path(value: str) -> str:
    path = value.split("\t", 1)[0]
    if path == "/dev/null":
        return ""
    return path[2:] if path.startswith(("a/", "b/")) else path


def _extract_safe_tar(payload: bytes, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive.getmembers():
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts or member.issym() or member.islnk():
                raise ValueError(f"unsafe git archive member: {member.name}")
            target = destination.joinpath(*pure.parts)
            resolved = target.resolve()
            if resolved != root and root not in resolved.parents:
                raise ValueError(f"archive member escapes destination: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"cannot read git archive member: {member.name}")
                with source, target.open("wb") as output:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
            else:
                raise ValueError(f"unsupported git archive member: {member.name}")


def _run_binary(
    argv: Sequence[str], cwd: Path, timeout: float
) -> tuple[bytes, int | None, str, bool, str]:
    command = _validate_argv(argv)
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            timeout=timeout,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, bytes) else b""
        return stdout, None, _timeout_text(exc.stderr), True, "git command timed out"
    except OSError as exc:
        return b"", None, "", False, f"{type(exc).__name__}: {exc}"
    return (
        completed.stdout,
        completed.returncode,
        completed.stderr.decode("utf-8", errors="replace"),
        False,
        "",
    )


def _validate_argv(argv: Sequence[str]) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)):
        raise TypeError("argv must be a sequence of arguments, not a command string")
    command = tuple(str(item) for item in argv)
    if not command or any(not item for item in command):
        raise ValueError("argv must contain non-empty arguments")
    return command


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _command_error(
    operation: str, result: GitCommandResult, pair: GitCommitPair | None = None
) -> InfrastructureError:
    return InfrastructureError(
        operation=operation,
        message=result.error or f"git command exited with status {result.returncode}",
        pair=pair,
        returncode=result.returncode,
        stderr=result.stderr,
        timed_out=result.timed_out,
    )


def _coerce_pair(value: GitCommitPair | tuple[str, str]) -> GitCommitPair:
    if isinstance(value, GitCommitPair):
        return value
    old, new = value
    return GitCommitPair(str(old), str(new))


def _hint_key(item: GitIdentityHint) -> tuple[Any, ...]:
    return item.kind, item.source_locators, item.target_locators, item.ambiguous, item.provenance


def _error_key(item: InfrastructureError) -> tuple[str, str, str, str]:
    pair = item.pair or GitCommitPair("", "")
    return pair.old_commit, pair.new_commit, item.operation, item.message


_DEFINITION = re.compile(r"^\s*(?:async\s+def|def|class)\s+(?P<name>[A-Za-z_]\w*)\b")
_SHAPE_TOKEN = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_STRUCTURAL_NODE_KINDS = frozenset(
    {
        "FunctionDef",
        "AsyncFunctionDef",
        "ClassDef",
        "arguments",
        "arg",
        "Name",
        "Load",
        "Store",
    }
)
_STRUCTURAL_TOKENS = frozenset(
    {
        "FunctionDef",
        "AsyncFunctionDef",
        "ClassDef",
        "name",
        "entity",
        "args",
        "arguments",
        "arg",
        "body",
        "ctx",
        "Load",
        "Store",
        "id",
        "value",
        "values",
        "targets",
        "target",
    }
)
