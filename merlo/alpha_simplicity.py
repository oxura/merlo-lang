from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Mapping

from .productive_simplicity import extract_source_metrics


SCHEMA_VERSION = "merlo.alpha-simplicity.report.v1"
CORPUS_SCHEMA = "merlo.alpha-simplicity.corpus.v1"
ARMS = ("concise", "canonical", "python", "native")
REQUIRED_CATEGORIES = (
    "scripts/automation",
    "numeric/research",
    "business/text",
    "files/JSON",
    "records/enums/collections",
    "errors/modules",
    "network",
    "FFI",
)
CASES_PER_CATEGORY = 6
METRICS = (
    "lexical_tokens",
    "punctuation_tokens",
    "lines",
    "source_bytes",
    "explicit_type_annotations",
    "explicit_ownership_annotations",
    "lifetime_annotations",
    "manual_memory_operations",
    "error_handling_boilerplate",
    "effect_annotations",
    "distinct_constructs",
    "nesting_depth",
)
_EFFECT_RE = re.compile(
    r"\b(?:console\.read|console\.write|fs\.read|fs\.write|env\.read|"
    r"clock\.now|random\.read|network\.tcp|network\.http|process\.args)\b"
)
_OWNERSHIP_ANNOTATION_RE = re.compile(
    r"\b(?:Owned|Borrow(?:Mut)?|Move|Shared|Box|Vec|Map|BytesView|TextView)\b"
)


class CorpusError(ValueError):
    """Raised when immutable alpha evidence is incomplete or inconsistent."""


@dataclass(frozen=True)
class AlphaRecord:
    id: str
    category: str
    concise: str
    canonical: str
    python: str
    native: str
    expected: Mapping[str, Any]

    def arm_sources(self) -> dict[str, str]:
        return {arm: getattr(self, arm) for arm in ARMS}

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "concise": self.concise,
            "canonical": self.canonical,
            "python": self.python,
            "native": self.native,
            "expected": dict(self.expected),
        }


DEFAULT_CORPUS = Path(__file__).resolve().parents[1] / "benchmarks" / "alpha_simplicity" / "corpus.json"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    elif not isinstance(value, bytes):
        value = _json_bytes(value)
    return hashlib.sha256(value).hexdigest()


def _corpus_path(path: str | Path | None) -> Path:
    if path is None:
        return DEFAULT_CORPUS
    candidate = Path(path)
    if candidate.is_dir():
        candidate = candidate / "corpus.json"
    return candidate


def _check_expected(record: AlphaRecord) -> None:
    expected = record.expected
    if set(expected) != {"stdout", "returncode"}:
        raise CorpusError(f"{record.id}: expected result must contain stdout and returncode")
    if not isinstance(expected["stdout"], str) or not expected["stdout"].endswith("\n"):
        raise CorpusError(f"{record.id}: expected stdout must be newline terminated text")
    if expected["returncode"] != 0:
        raise CorpusError(f"{record.id}: only successful standalone examples are admissible")
    if f'print(main())' not in record.python or "__main__" not in record.python:
        raise CorpusError(f"{record.id}: Python arm is not independently executable")
    if "int main" not in record.native:
        raise CorpusError(f"{record.id}: native arm is not independently executable")


def _parse_records(payload: Mapping[str, Any]) -> tuple[AlphaRecord, ...]:
    if payload.get("schema") != CORPUS_SCHEMA:
        raise CorpusError("unsupported alpha simplicity corpus schema")
    if tuple(payload.get("arms", ())) != ARMS:
        raise CorpusError("corpus arm set is ambiguous or incomplete")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise CorpusError("corpus has no records")
    records: list[AlphaRecord] = []
    seen_ids: set[str] = set()
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise CorpusError("record is not an object")
        identifier = raw.get("id")
        category = raw.get("category")
        if not isinstance(identifier, str) or not identifier or identifier in seen_ids:
            raise CorpusError(f"duplicate or missing case identity: {identifier!r}")
        if category not in REQUIRED_CATEGORIES:
            raise CorpusError(f"{identifier}: unsupported or ambiguous category")
        seen_ids.add(identifier)
        values = {arm: raw.get(arm) for arm in ARMS}
        if any(not isinstance(value, str) or not value.strip() for value in values.values()):
            raise CorpusError(f"{identifier}: missing executable arm")
        expected = raw.get("expected")
        if not isinstance(expected, dict):
            raise CorpusError(f"{identifier}: missing expected result")
        record = AlphaRecord(identifier, category, **values, expected=expected)
        _check_expected(record)
        records.append(record)
    counts = {category: sum(item.category == category for item in records) for category in REQUIRED_CATEGORIES}
    shortfalls = {category: count for category, count in counts.items() if count < CASES_PER_CATEGORY}
    if shortfalls:
        raise CorpusError(f"category shortfall: {shortfalls}")
    return tuple(sorted(records, key=lambda item: item.id))


def load_corpus(path: str | Path | None = None) -> tuple[AlphaRecord, ...]:
    """Load and validate the frozen, alpha-owned corpus inputs."""
    candidate = _corpus_path(path)
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusError(f"cannot read corpus {candidate}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CorpusError("corpus root must be an object")
    return _parse_records(payload)


def record_hash(record: AlphaRecord) -> str:
    return _sha256(record.to_payload())


def corpus_digest(records: tuple[AlphaRecord, ...] | list[AlphaRecord]) -> str:
    """Return a deterministic digest over IDs, source arms, and expected outputs."""
    return _sha256([record.to_payload() for record in sorted(records, key=lambda item: item.id)])


def source_hashes(record: AlphaRecord) -> dict[str, str]:
    return {arm: _sha256(getattr(record, arm)) for arm in ARMS}


def measure_source(source: str) -> dict[str, Any]:
    """Measure all requested source-surface dimensions from source text."""
    metrics = dict(extract_source_metrics(source))
    metrics.update(
        {
            "explicit_ownership_annotations": len(_OWNERSHIP_ANNOTATION_RE.findall(source)),
            "manual_memory_operations": metrics["manual_resource_operations"],
            "effect_annotations": len(_EFFECT_RE.findall(source)),
        }
    )
    return {key: metrics[key] for key in METRICS} | {
        "constructs": metrics["constructs"],
        "ownership_operations": metrics["ownership_operations"],
        "dynamic_any": metrics["dynamic_any"],
    }


def _quartile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _distribution(
    values: list[tuple[str, str, float]],
) -> dict[str, Any]:
    if not values:
        raise CorpusError("cannot summarize an empty metric")
    numbers = [value for _, _, value in values]
    worst_id, worst_hash, worst_value = max(values, key=lambda item: (item[2], item[0]))
    return {
        "p25": _quartile(numbers, 0.25),
        "median": float(median(numbers)),
        "p75": _quartile(numbers, 0.75),
        "worst": {
            "case_id": worst_id,
            "record_sha256": worst_hash,
            "value": worst_value,
        },
    }


def _validate_independent_result(record: AlphaRecord) -> None:
    _check_expected(record)


def build_report(records: tuple[AlphaRecord, ...] | None = None) -> dict[str, Any]:
    """Compute a report exclusively from frozen source strings and expected results."""
    records = load_corpus() if records is None else tuple(records)
    if len({record.id for record in records}) != len(records):
        raise CorpusError("duplicate case identity")
    observations: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: item.id):
        _validate_independent_result(record)
        measured = {arm: measure_source(getattr(record, arm)) for arm in ARMS}
        digest = record_hash(record)
        observations.append(
            {
                "id": record.id,
                "category": record.category,
                "record_sha256": digest,
                "source_sha256": source_hashes(record),
                "expected": dict(record.expected),
                "arms": measured,
                "ratios": {
                    "lexical_merlo_over_python": measured["concise"]["lexical_tokens"] / measured["python"]["lexical_tokens"],
                    "punctuation_merlo_over_python": measured["concise"]["punctuation_tokens"] / measured["python"]["punctuation_tokens"],
                    "bytes_merlo_over_python": measured["concise"]["source_bytes"] / measured["python"]["source_bytes"],
                },
            }
        )
    summaries: dict[str, Any] = {"arms": {}, "ratios": {}}
    for arm in ARMS:
        summaries["arms"][arm] = {
            metric: _distribution(
                [(item["id"], item["record_sha256"], float(item["arms"][arm][metric])) for item in observations]
            )
            for metric in METRICS
        }
    for ratio in ("lexical_merlo_over_python", "punctuation_merlo_over_python", "bytes_merlo_over_python"):
        summaries["ratios"][ratio] = _distribution(
            [(item["id"], item["record_sha256"], float(item["ratios"][ratio])) for item in observations]
        )
    category_counts = {
        category: sum(item["category"] == category for item in observations)
        for category in REQUIRED_CATEGORIES
    }
    concise_metrics = [item["arms"]["concise"] for item in observations]
    gates = {
        "paired_programs_at_least_40": len(observations) >= 40,
        "required_category_distribution": all(count >= CASES_PER_CATEGORY for count in category_counts.values()),
        "lexical_median_at_most_0_80": summaries["ratios"]["lexical_merlo_over_python"]["median"] <= 0.80,
        "punctuation_median_at_most_0_80": summaries["ratios"]["punctuation_merlo_over_python"]["median"] <= 0.80,
        "ordinary_lifetime_annotations_zero": all(item["lifetime_annotations"] == 0 for item in concise_metrics),
        "ordinary_manual_memory_zero": all(item["manual_memory_operations"] == 0 for item in concise_metrics),
        "dynamic_any_zero": all(item["dynamic_any"] == 0 for item in concise_metrics),
        "independent_expected_results": all(item["expected"]["returncode"] == 0 for item in observations),
    }
    return {
        "schema": SCHEMA_VERSION,
        "corpus_schema": CORPUS_SCHEMA,
        "corpus_sha256": corpus_digest(records),
        "count": len(observations),
        "category_counts": category_counts,
        "summaries": summaries,
        "gates": gates,
        "passed": all(gates.values()),
        "observations": observations,
    }


def validate_report(report: Mapping[str, Any], records: tuple[AlphaRecord, ...] | None = None) -> bool:
    """Reject stale, forged, reordered, or source-mutated reports."""
    expected = build_report(records)
    if dict(report) != expected:
        raise CorpusError("report does not match the current content-addressed corpus")
    return True


def validate_observation_digest(report: Mapping[str, Any], records: tuple[AlphaRecord, ...] | None = None) -> bool:
    """Check a preregistered report against a freshly observed corpus digest."""
    records = load_corpus() if records is None else tuple(records)
    if report.get("corpus_sha256") != corpus_digest(records):
        raise CorpusError("post-observation corpus mutation detected")
    return True
