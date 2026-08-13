"""Falsifiable Semantic Compression Surface experiment."""
from __future__ import annotations

import hashlib
import json
import random
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .semantic_surface import (
    SemanticSurfaceError,
    build_semantic_surface,
    compile_semantic_surface,
)


SEMANTIC_SURFACE_EXPERIMENT_SCHEMA_VERSION = 1
SEMANTIC_SURFACE_EXPERIMENT_CONTRACT = "merlo.semantic-surface-experiment.v1"
MASK = (1 << 64) - 1


@dataclass(frozen=True)
class SurfaceCase:
    id: str
    category: str
    concise: str
    python: str
    arguments: tuple[int, ...]
    expected: int


_CASES = (
    SurfaceCase(
        "automation_checksum",
        "script",
        """n = args[0]
value = 1
checksum = 0
for i in 0..n:
    value = value * 1664525 + 1013904223
    checksum = checksum ^ (value + i)
checksum
""",
        """import sys
MASK = (1 << 64) - 1
n = int(sys.argv[1])
value = 1
checksum = 0
for i in range(n):
    value = (value * 1664525 + 1013904223) & MASK
    checksum = (checksum ^ (value + i)) & MASK
print(checksum)
""",
        (750_000,),
        0,
    ),
    SurfaceCase(
        "research_recurrence",
        "research",
        """samples = args[0]
seed = args[1]
state = seed
score = 0
for step in 0..samples:
    state = state * 6364136223846793005 + 1442695040888963407
    score = score + ((state ^ step) & 65535)
score
""",
        """import sys
MASK = (1 << 64) - 1
samples = int(sys.argv[1])
state = int(sys.argv[2])
score = 0
for step in range(samples):
    state = (state * 6364136223846793005 + 1442695040888963407) & MASK
    score = (score + ((state ^ step) & 65535)) & MASK
print(score)
""",
        (650_000, 17),
        0,
    ),
    SurfaceCase(
        "billing_compound",
        "business",
        """fn compound(amount, rate, periods):
    total = amount
    for period in 0..periods:
        total = total + total * rate / 10000
    total

principal = args[0]
rate = args[1]
periods = args[2]
compound(principal, rate, periods)
""",
        """import sys
MASK = (1 << 64) - 1
def compound(amount, rate, periods):
    total = amount
    for _ in range(periods):
        total = (total + total * rate // 10000) & MASK
    return total
print(compound(int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])))
""",
        (125_000_00, 375, 48),
        0,
    ),
    SurfaceCase(
        "systems_mixer",
        "systems",
        """n = args[0]
word = args[1]
for round in 0..n:
    word = word ^ (word << 13)
    word = word ^ (word >> 7)
    word = word ^ (word << 17)
word
""",
        """import sys
MASK = (1 << 64) - 1
n = int(sys.argv[1])
word = int(sys.argv[2])
for _ in range(n):
    word = (word ^ (word << 13)) & MASK
    word = (word ^ (word >> 7)) & MASK
    word = (word ^ (word << 17)) & MASK
print(word)
""",
        (900_000, 88172645463325252),
        0,
    ),
)


def _expected(case: SurfaceCase) -> int:
    if case.id == "automation_checksum":
        n = case.arguments[0]
        value = 1
        checksum = 0
        for index in range(n):
            value = (value * 1664525 + 1013904223) & MASK
            checksum = (checksum ^ (value + index)) & MASK
        return checksum
    if case.id == "research_recurrence":
        samples, state = case.arguments
        score = 0
        for step in range(samples):
            state = (state * 6364136223846793005 + 1442695040888963407) & MASK
            score = (score + ((state ^ step) & 65535)) & MASK
        return score
    if case.id == "billing_compound":
        total, rate, periods = case.arguments
        for _ in range(periods):
            total = (total + total * rate // 10000) & MASK
        return total
    n, word = case.arguments
    for _ in range(n):
        word = (word ^ (word << 13)) & MASK
        word = (word ^ (word >> 7)) & MASK
        word = (word ^ (word << 17)) & MASK
    return word


def _tokens(source: str) -> int:
    return len(re.findall(r"[A-Za-z_]\w*|\d+|==|!=|<=|>=|<<|>>|\.\.|\S", source))


def _run(command: tuple[str, ...]) -> tuple[int, float]:
    started = time.perf_counter_ns()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or f"exit {completed.returncode}")
    return int(completed.stdout.strip().splitlines()[-1]), elapsed


def run_semantic_surface_experiment(
    *,
    output_dir: str | Path = "benchmarks/merlo_semantic_surface",
    report_path: str | Path = "benchmarks/merlo_semantic_surface.json",
    repetitions: int = 9,
    warmups: int = 2,
) -> dict[str, Any]:
    if repetitions < 5 or warmups < 1:
        raise ValueError("semantic surface experiment needs >=5 runs and warmup")
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    observations: list[dict[str, Any]] = []
    for case in _CASES:
        expected = _expected(case)
        case_root = root / case.id
        case_root.mkdir(parents=True, exist_ok=True)
        build = build_semantic_surface(
            case.concise,
            output_dir=case_root / "merlo",
            path=f"{case.id}.mlo",
            stem="program",
        )
        python_path = case_root / "reference.py"
        python_path.write_text(case.python, encoding="utf-8")
        native_command = (
            str(build.native.binary_path),
            *(str(item) for item in case.arguments),
        )
        python_command = (
            sys.executable,
            str(python_path),
            *(str(item) for item in case.arguments),
        )
        schedule = ["merlo", "python"] * (warmups + repetitions)
        random.Random(0x5EED + len(observations)).shuffle(schedule)
        samples = {"merlo": [], "python": []}
        counts = {"merlo": 0, "python": 0}
        correct = True
        for arm in schedule:
            result, elapsed = _run(
                native_command if arm == "merlo" else python_command
            )
            correct = correct and result == expected
            counts[arm] += 1
            if counts[arm] > warmups:
                samples[arm].append(elapsed)
        merlo_median = statistics.median(samples["merlo"])
        python_median = statistics.median(samples["python"])
        source_ratio = _tokens(case.concise) / _tokens(case.python)
        observations.append(
            {
                "id": case.id,
                "category": case.category,
                "arguments": list(case.arguments),
                "expected": expected,
                "correct": correct,
                "surface": {
                    "source_bytes": len(case.concise.encode()),
                    "tokens": _tokens(case.concise),
                    "lines": len(case.concise.splitlines()),
                    "python_source_bytes": len(case.python.encode()),
                    "python_tokens": _tokens(case.python),
                    "python_lines": len(case.python.splitlines()),
                    "token_ratio_vs_python": source_ratio,
                    "not_longer_than_python": source_ratio <= 1.0,
                    "canonical_source": build.compilation.elaborated.canonical_source,
                    "inferred_annotations": build.compilation.elaborated.inferred_annotation_count,
                    "inferred_mutability": build.compilation.elaborated.inferred_mutability_count,
                },
                "semantic_projection": {
                    "hir_digest": build.compilation.hir.digest,
                    "mir_digest": build.compilation.mir.digest,
                    "optimized_mir_digest": build.compilation.optimized_mir.digest,
                    "generated_c_sha256": build.compilation.generated_c_sha256,
                    "unknown_references": sum(
                        item.status != "Exact"
                        and item.spelling
                        not in {"Bool", "UInt64", "meldra_range"}
                        for item in build.compilation.hir.references
                    ),
                },
                "performance": {
                    "runs": repetitions,
                    "warmups": warmups,
                    "merlo_median_ms": merlo_median,
                    "python_median_ms": python_median,
                    "merlo_over_python": merlo_median / python_median,
                    "native_faster_than_python": merlo_median < python_median,
                },
            }
        )
    negative_sources = {
        "ambiguous_parameter": "value = args[0]\nfn identity(item):\n    item\n\nidentity(value)\n",
        "type_conflict": "flag = args[0]\nif flag:\n    flag = flag + 1\nflag\n",
        "unresolved_name": "n = args[0]\nmissing + n\n",
    }
    negative = []
    for name, source in negative_sources.items():
        try:
            compile_semantic_surface(source, path=f"negative/{name}.mlo")
        except SemanticSurfaceError as exc:
            negative.append({"id": name, "rejected": True, "diagnostic": str(exc)})
        else:
            negative.append({"id": name, "rejected": False, "diagnostic": None})
    gates = {
        "four_categories": {item["category"] for item in observations}
        == {"script", "research", "business", "systems"},
        "native_correct": all(item["correct"] for item in observations),
        "surface_not_longer_than_python": all(
            item["surface"]["not_longer_than_python"] for item in observations
        ),
        "native_faster_than_python": all(
            item["performance"]["native_faster_than_python"]
            for item in observations
        ),
        "semantic_references_exact": all(
            item["semantic_projection"]["unknown_references"] == 0
            for item in observations
        ),
        "ambiguity_rejected": all(item["rejected"] for item in negative),
    }
    report = {
        "schema_version": SEMANTIC_SURFACE_EXPERIMENT_SCHEMA_VERSION,
        "contract": SEMANTIC_SURFACE_EXPERIMENT_CONTRACT,
        "status": (
            "SEMANTIC_COMPRESSION_SUPPORTED"
            if all(gates.values())
            else "SEMANTIC_COMPRESSION_INCOMPLETE"
        ),
        "hypothesis": (
            "A concise target-independent surface can be no longer than Python "
            "while elaborating deterministically into typed Merlo HIR/MIR and "
            "retaining native execution."
        ),
        "scope": {
            "supported": [
                "top-level scripts with positional native inputs",
                "Bool and UInt64 local inference",
                "inferred mutability",
                "functions, calls, conditions, and numeric loops",
                "canonical typed Merlo projection",
                "native C backend",
            ],
            "future_targets": [
                "WebAssembly",
                "browser DOM/UI facet",
                "server rendering",
            ],
            "unsupported": [
                "Text and collections inference",
                "I/O and effects",
                "records and enums in concise syntax",
                "REPL persistence",
                "frontend renderer or DOM runtime",
            ],
        },
        "gates": gates,
        "observations": observations,
        "negative_controls": negative,
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    destination = Path(report_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


__all__ = [
    "SEMANTIC_SURFACE_EXPERIMENT_CONTRACT",
    "SEMANTIC_SURFACE_EXPERIMENT_SCHEMA_VERSION",
    "run_semantic_surface_experiment",
]
