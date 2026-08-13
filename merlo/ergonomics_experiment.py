"""Static ergonomics evidence with explicit unmeasured human/model trials."""

from __future__ import annotations

import json
import os
import re
import statistics
from pathlib import Path
from typing import Any


ERGONOMICS_SCHEMA_VERSION = 1
_LANGUAGES = ("meldra", "c", "rust", "go", "csharp", "python")
_SUFFIX = {
    "meldra": "main.meldra",
    "c": "main.c",
    "rust": "main.rs",
    "go": "main.go",
    "csharp": "Program.cs",
    "python": "main.py",
}
_DEFECT_SOURCES = {
    "mutable_array_alias_update": """fn main(n: UInt64) -> UInt64:
    var values: Array[UInt64, 4] = [1, 2, 3, 4]
    let view: Array[UInt64, 4] = borrow_mut(values)
    values[0] = view[0] + n
    values[0]
""",
    "duplicate_drop_prevention": """fn main(n: UInt64) -> UInt64:
    let values: Shared[Array[UInt64, 2]] = [n, 2]
    let result: UInt64 = values[0] + values[1]
    drop(values)
    result
""",
    "ownership_elision": """fn make_values(i: UInt64) -> Shared[Array[UInt64, 2]]:
    [i, i + 1]
fn main(n: UInt64) -> UInt64:
    let values: Shared[Array[UInt64, 2]] = make_values(n)
    let result: UInt64 = values[0] + values[1]
    drop(values)
    result
""",
}


def _metrics(source: str, root: Path) -> dict[str, Any]:
    lines = [line for line in source.splitlines() if line.strip()]
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\sA-Za-z0-9_]", source)
    return {
        "source_bytes": len(source.encode()),
        "nonblank_lines": len(lines),
        "lexical_tokens": len(tokens),
        "explicit_type_markers": len(re.findall(r"(?::\s*[A-Za-z_]|\b(?:uint64_t|u64|ulong)\b)", source)),
        "lifetime_annotations": len(re.findall(r"'[A-Za-z_]", source)),
        "unsafe_markers": len(re.findall(r"\bunsafe\b", source)),
        "manual_ownership_markers": len(
            re.findall(r"\b(?:malloc|free|retain|release|Rc|Arc|drop)\b", source)
        ),
        "build_config_files": sum(
            (root / name).is_file()
            for name in ("Cargo.toml", "go.mod", "bench.csproj", "Makefile", "CMakeLists.txt")
        ),
    }


def run_ergonomics_experiment(
    *,
    benchmark_dir: str | Path = "benchmarks/stage06p_benchmark/corpus",
    extended_dir: str | Path = "benchmarks/stage06p_extended/corpus",
    output_path: str | Path = "benchmarks/meldra_stage06p_ergonomics.json",
) -> dict[str, Any]:
    benchmark_root = Path(benchmark_dir)
    extended_root = Path(extended_dir)
    primitive = (
        "arithmetic_lcg",
        "fixed_array_scan",
        "record_values",
        "bubble_sort_8",
        "startup",
    )
    normal = (
        "map_filter_fold",
        "shared_allocations",
        "text_bytes_utf8",
        "recursive_values",
        "interface_dispatch",
    )
    maintenance = tuple(_DEFECT_SOURCES)
    tasks = [
        {"id": task, "category": "primitive"} for task in primitive
    ] + [
        {"id": task, "category": "normal-program"} for task in normal
    ] + [
        {"id": task, "category": "realistic-maintenance"} for task in maintenance
    ]
    tasks.extend(
        (
            {"id": "bounds_check_hardening", "category": "realistic-maintenance"},
            {"id": "helper_refactor", "category": "realistic-maintenance"},
        )
    )

    sources = []
    for task in primitive + normal[:2]:
        for language in _LANGUAGES:
            root = benchmark_root / task / language
            path = root / _SUFFIX[language]
            if path.is_file():
                source = path.read_text(encoding="utf-8")
                sources.append(
                    {
                        "task": task,
                        "category": "primitive" if task in primitive else "normal-program",
                        "language": language,
                        "status": "MEASURED",
                        **_metrics(source, root),
                    }
                )
    for task in normal[2:]:
        for language in _LANGUAGES:
            root = extended_root / task / language
            path = root / _SUFFIX[language]
            if path.is_file():
                source = path.read_text(encoding="utf-8")
                sources.append(
                    {
                        "task": task,
                        "category": "normal-program",
                        "language": language,
                        "status": "MEASURED",
                        **_metrics(source, root),
                    }
                )
            elif language == "meldra":
                sources.append(
                    {
                        "task": task,
                        "category": "normal-program",
                        "language": language,
                        "status": "UNSUPPORTED_STAGE06P_SUBSET",
                    }
                )
    for task, source in _DEFECT_SOURCES.items():
        sources.append(
            {
                "task": task,
                "category": "realistic-maintenance",
                "language": "meldra",
                "status": "MEASURED_REGRESSION_FIXTURE",
                **_metrics(source, Path("tests")),
            }
        )
    for task in ("bounds_check_hardening", "helper_refactor"):
        sources.append(
            {
                "task": task,
                "category": "realistic-maintenance",
                "language": "meldra",
                "status": "UNMEASURED_NO_PAIRED_REFERENCE_FIXTURE",
            }
        )

    aggregates = {}
    for language in _LANGUAGES:
        values = [
            item
            for item in sources
            if item["language"] == language and item["status"].startswith("MEASURED")
        ]
        aggregates[language] = {
            "measured_sources": len(values),
            "median_nonblank_lines": statistics.median(
                item["nonblank_lines"] for item in values
            ) if values else None,
            "median_lexical_tokens": statistics.median(
                item["lexical_tokens"] for item in values
            ) if values else None,
            "median_explicit_type_markers": statistics.median(
                item["explicit_type_markers"] for item in values
            ) if values else None,
            "median_lifetime_annotations": statistics.median(
                item["lifetime_annotations"] for item in values
            ) if values else None,
            "median_manual_ownership_markers": statistics.median(
                item["manual_ownership_markers"] for item in values
            ) if values else None,
        }

    api_key_available = bool(os.environ.get("FIREWORKS_API_KEY"))
    report = {
        "schema_version": ERGONOMICS_SCHEMA_VERSION,
        "kind": "MeldraStage06PErgonomics",
        "task_manifest": tasks,
        "task_count": len(tasks),
        "category_count": len({item["category"] for item in tasks}),
        "static_source_metrics": sources,
        "aggregates": aggregates,
        "human_trial": {
            "status": "UNMEASURED_WITHOUT_USERS",
            "measured_users": 0,
            "reason": "No recruited users or observed human sessions were available.",
        },
        "ai_trial": {
            "status": "UNMEASURED_PROVIDER_UNAVAILABLE",
            "measured_tasks": 0,
            "api_key_available": api_key_available,
            "reason": "FIREWORKS_API_KEY was unavailable; no fake/replay score substituted.",
        },
        "decision_gate": {
            "at_least_15_tasks": len(tasks) >= 15,
            "at_least_3_categories": len({item["category"] for item in tasks}) >= 3,
            "human_productivity_measured": False,
            "ai_productivity_measured": False,
            "passed": False,
        },
        "limitations": [
            "Static source metrics are descriptive and are not human usability or AI productivity measurements.",
            "Only twelve tasks have executable cross-language source; three maintenance tasks are Meldra regression fixtures or explicitly unmeasured.",
            "Build configuration and library ecosystem burden are not inferred from single-file benchmark arms.",
        ],
    }
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


__all__ = ["ERGONOMICS_SCHEMA_VERSION", "run_ergonomics_experiment"]
