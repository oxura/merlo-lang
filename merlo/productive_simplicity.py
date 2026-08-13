from __future__ import annotations

import re
from pathlib import Path
from statistics import median


APPLICATIONS = ("ndjson", "csv", "grep")
ARMS = ("concise_merlo", "canonical_merlo", "python", "c")

_CONSTRUCT_WORDS = frozenset(
    {
        "fn",
        "def",
        "return",
        "if",
        "else",
        "elif",
        "for",
        "while",
        "match",
        "case",
        "struct",
        "class",
        "enum",
        "import",
        "from",
        "const",
        "var",
        "try",
        "except",
        "catch",
        "raise",
        "throw",
    }
)
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")
_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.ASCII)
_TYPE_ANNOTATION_RE = re.compile(
    r"(?:->|:)[ \t]*(?:&[ \t]*)?(?:'[A-Za-z_][A-Za-z0-9_]*[ \t]*)?[A-Za-z_][A-Za-z0-9_]*(?:[ \t]*<[^>\n]+>)?"
)
_LIFETIME_RE = re.compile(r"'[A-Za-z_][A-Za-z0-9_]*")
_OWNERSHIP_RE = re.compile(r"\b(?:move|copy|clone|borrow|take|release)\s*\(")
_RESOURCE_RE = re.compile(r"\b(?:malloc|calloc|realloc|free|fopen|fclose)\s*\(")
_ERROR_RE = re.compile(r"\b(?:try|except|raise|catch|throw)\b")
_ANY_RE = re.compile(r"\bAny\b")


def _nesting_depth(source: str) -> int:
    bracket_depth = 0
    maximum = 0
    for character in source:
        if character in "([{":
            bracket_depth += 1
            maximum = max(maximum, bracket_depth)
        elif character in ")]}":
            bracket_depth = max(0, bracket_depth - 1)
    for line in source.splitlines():
        if not line.strip():
            continue
        leading = len(line) - len(line.lstrip(" \t"))
        if leading:
            maximum = max(maximum, leading // 4 if "\t" not in line[:leading] else leading)
    return maximum


def extract_source_metrics(source: str) -> dict[str, object]:
    """Extract deterministic, lexical source-surface measurements."""
    if not isinstance(source, str):
        raise TypeError("source must be text")
    lexical_tokens = _TOKEN_RE.findall(source)
    constructs: list[str] = []
    for token in lexical_tokens:
        if token in _CONSTRUCT_WORDS and token not in constructs:
            constructs.append(token)
    return {
        "lexical_tokens": len(lexical_tokens),
        "punctuation_tokens": len(_PUNCTUATION_RE.findall(source)),
        "lines": len(source.splitlines()),
        "source_bytes": len(source.encode("utf-8")),
        "explicit_type_annotations": len(_TYPE_ANNOTATION_RE.findall(source)),
        "lifetime_annotations": len(_LIFETIME_RE.findall(source)),
        "ownership_operations": len(_OWNERSHIP_RE.findall(source)),
        "manual_resource_operations": len(_RESOURCE_RE.findall(source)),
        "error_handling_boilerplate": len(_ERROR_RE.findall(source)),
        "dynamic_any": len(_ANY_RE.findall(source)),
        "distinct_constructs": len(constructs),
        "constructs": constructs,
        "nesting_depth": _nesting_depth(source),
    }


def _source_paths(root: Path, application: str, arm: str) -> tuple[Path, ...]:
    if arm in {"concise_merlo", "canonical_merlo"}:
        surface = "app" if arm == "concise_merlo" else "canonical"
        directory = root / "merlo" / "programs" / f"productive_{application}" / surface
        return tuple(sorted(directory.glob("*.mlo")))
    suffix = ".py" if arm == "python" else ".c"
    path = root / "benchmarks" / "productive_simplicity" / application / f"reference{suffix}"
    return (path,) if path.is_file() else ()


def _unmeasured() -> dict[str, object]:
    return {
        "status": "UNMEASURED",
        "reason": "SOURCE_ARTIFACT_NOT_FOUND",
        "source_files": [],
        "metrics": None,
    }


def _measured(root: Path, paths: tuple[Path, ...]) -> dict[str, object]:
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    return {
        "status": "MEASURED",
        "source_files": [path.relative_to(root).as_posix() for path in paths],
        "metrics": extract_source_metrics(source),
    }


def _ratio_record(arms: dict[str, dict[str, object]]) -> dict[str, float]:
    concise = arms["concise_merlo"]
    python = arms["python"]
    if concise["status"] != "MEASURED" or python["status"] != "MEASURED":
        return {}
    concise_metrics = concise["metrics"]
    python_metrics = python["metrics"]
    if not isinstance(concise_metrics, dict) or not isinstance(python_metrics, dict):
        return {}
    if concise_metrics["source_bytes"] == 0 or python_metrics["source_bytes"] == 0:
        return {}
    result: dict[str, float] = {}
    for metric, key in (
        ("lexical_tokens", "lexical_tokens_merlo_over_python"),
        ("punctuation_tokens", "punctuation_tokens_merlo_over_python"),
    ):
        denominator = python_metrics[metric]
        if isinstance(denominator, int) and denominator > 0:
            numerator = concise_metrics[metric]
            if isinstance(numerator, int):
                result[key] = numerator / denominator
    return result


def _median(values: list[float]) -> float | None:
    return float(median(values)) if values else None


def audit_productive_simplicity(root: str | Path) -> dict[str, object]:
    base = Path(root)
    applications: list[dict[str, object]] = []
    for application in APPLICATIONS:
        arms: dict[str, dict[str, object]] = {}
        for arm in ARMS:
            paths = _source_paths(base, application, arm)
            arms[arm] = _measured(base, paths) if paths else _unmeasured()
        ratios = _ratio_record(arms)
        applications.append(
            {
                "application": application,
                "arms": arms,
                "ratios": ratios,
            }
        )

    token_ratios = [
        float(item["ratios"]["lexical_tokens_merlo_over_python"])
        for item in applications
        if "lexical_tokens_merlo_over_python" in item["ratios"]
    ]
    punctuation_ratios = [
        float(item["ratios"]["punctuation_tokens_merlo_over_python"])
        for item in applications
        if "punctuation_tokens_merlo_over_python" in item["ratios"]
    ]
    concise_records = [
        item["arms"]["concise_merlo"]
        for item in applications
        if item["arms"]["concise_merlo"]["status"] == "MEASURED"
    ]
    concise_sources_complete = len(concise_records) == len(APPLICATIONS) and all(
        isinstance(record["metrics"], dict) and record["metrics"]["source_bytes"] > 0
        for record in concise_records
    )
    token_median = _median(token_ratios)
    punctuation_median = _median(punctuation_ratios)
    gates = {
        "median_merlo_python_tokens_at_most_0_80": token_median is not None and token_median <= 0.80,
        "median_merlo_python_punctuation_at_most_0_80": punctuation_median is not None
        and punctuation_median <= 0.80,
        "concise_lifetime_annotations_zero": concise_sources_complete
        and all(record["metrics"]["lifetime_annotations"] == 0 for record in concise_records),
        "concise_manual_resource_operations_zero": concise_sources_complete
        and all(record["metrics"]["manual_resource_operations"] == 0 for record in concise_records),
        "concise_dynamic_any_zero": concise_sources_complete
        and all(record["metrics"]["dynamic_any"] == 0 for record in concise_records),
    }
    return {
        "applications": applications,
        "ratio_medians": {
            "measured_application_count": len(token_ratios),
            "lexical_tokens_merlo_over_python": token_median,
            "punctuation_tokens_merlo_over_python": punctuation_median,
        },
        "gates": gates,
        "passed": all(gates.values()),
    }
