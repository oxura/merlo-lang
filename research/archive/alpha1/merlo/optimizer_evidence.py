"""Positive/negative evidence for every Stage 0.6P optimizer pass."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from research.archive.alpha1.merlo.native_differential import ExecutionObservation, evaluate_mir
from research.archive.alpha1.merlo.native_hir import compile_native_hir, lower_native_hir_to_performance
from merlo.performance_mir import PerformanceMIR
from tools.benchmarks.merlo.performance_opt import (
    OPTIMIZATION_PIPELINE,
    OPTIMIZATION_PASS_VERSIONS,
    region_ownership_lowering,
)

OPTIMIZER_EVIDENCE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class _Case:
    source: str
    arguments: tuple[int | float | bool, ...]
    missed_reason: str


_SIMPLE = """fn main(n: UInt64) -> UInt64:
    n + 1
"""

_COLLECTION = """fn square(value: UInt64) -> UInt64:
    value * value

fn add(left: UInt64, right: UInt64) -> UInt64:
    left + right

fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 4] = [1, 2, 3, 4]
    let mapped: Slice[UInt64] = map(values, square)
    fold(mapped, n, add)
"""

_SHARED = """fn main(n: UInt64) -> UInt64:
    let values: Shared[Array[UInt64, 2]] = [n, 2]
    let result: UInt64 = values[0] + values[1]
    drop(values)
    result
"""

_CASES: dict[str, tuple[_Case, _Case]] = {
    "monomorphization": (
        _Case(_COLLECTION, (3,), "no generic collection operation exists"),
        _Case(_SIMPLE, (3,), "no generic collection operation exists"),
    ),
    "collection_fusion": (
        _Case(_COLLECTION, (3,), "no map/filter producer feeds a fold"),
        _Case(_SIMPLE, (3,), "no map/filter producer feeds a fold"),
    ),
    "inlining": (
        _Case(
            """fn helper(value: UInt64) -> UInt64:
    value * 3 + 1

fn main(n: UInt64) -> UInt64:
    helper(n)
""",
            (7,),
            "no eligible direct call exists",
        ),
        _Case(_SIMPLE, (7,), "no eligible direct call exists"),
    ),
    "owned_result_drop_insertion": (
        _Case(
            """fn square(value: UInt64) -> UInt64:
    value * value

fn make_values(n: UInt64) -> Slice[UInt64]:
    let values: Array[UInt64, 2] = [n, n + 1]
    map(values, square)

fn main(n: UInt64) -> UInt64:
    let mapped: Slice[UInt64] = make_values(n)
    mapped[0]
""",
            (6,),
            "no non-escaping owned collection result exists",
        ),
        _Case(_SIMPLE, (6,), "no non-escaping owned collection result exists"),
    ),
    "borrow_inference": (
        _Case(_SHARED, (40,), "shared allocation is escaping or lacks one safe drop"),
        _Case(_SIMPLE, (40,), "shared allocation is escaping or lacks one safe drop"),
    ),
    "constant_folding": (
        _Case(
            """fn main(n: UInt64) -> UInt64:
    let five: UInt64 = 2 + 3
    five * n
""",
            (8,),
            "no operation has compile-time constant operands",
        ),
        _Case(_SIMPLE, (8,), "no operation has compile-time constant operands"),
    ),
    "bounds_check_elimination": (
        _Case(
            """fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 4] = [1, 2, 3, 4]
    var result: UInt64 = n
    for i in 0..len(values):
        result = result + values[i]
    result
""",
            (5,),
            "index is not guarded by the same collection length",
        ),
        _Case(
            """fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 4] = [1, 2, 3, 4]
    values[n & 3]
""",
            (5,),
            "index is not guarded by the same collection length",
        ),
    ),
    "memory_model_lowering": (
        _Case(
            """fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 2] = [n, 2]
    values[0] + values[1]
""",
            (40,),
            "no abstract allocation remains",
        ),
        _Case(_SIMPLE, (40,), "no abstract allocation remains"),
    ),
    "dead_code_elimination": (
        _Case(
            """fn main(n: UInt64) -> UInt64:
    let unused: UInt64 = n * 9
    n + 1
""",
            (4,),
            "every pure result is live",
        ),
        _Case(_SIMPLE, (4,), "every pure result is live"),
    ),
    "region_ownership_lowering": (
        _Case(_SHARED, (40,), "no inferred-unique stack allocation exists"),
        _Case(_SIMPLE, (40,), "no inferred-unique stack allocation exists"),
    ),
}


def _semantic_key(observation: ExecutionObservation) -> tuple[Any, ...]:
    return (
        observation.status,
        observation.return_value,
        observation.printed_checksum,
        observation.error_kind,
        observation.effect_trace,
    )


def _prepare(
    mir: PerformanceMIR,
    target: str,
) -> tuple[PerformanceMIR, Callable[[PerformanceMIR], tuple[PerformanceMIR, Any]]]:
    current = mir
    if target == "region_ownership_lowering":
        for pass_function in OPTIMIZATION_PIPELINE:
            current, _ = pass_function(current)
        return current, region_ownership_lowering
    for pass_function in OPTIMIZATION_PIPELINE:
        if pass_function.__name__ == target:
            return current, pass_function
        current, _ = pass_function(current)
    raise KeyError(target)


def _run_case(
    pass_name: str,
    case: _Case,
    *,
    expected_to_fire: bool,
) -> dict[str, Any]:
    hir = compile_native_hir(case.source, path=f"optimizer/{pass_name}.meldra")
    initial = lower_native_hir_to_performance(hir)
    before, pass_function = _prepare(initial, pass_name)
    before_observation = evaluate_mir(before, case.arguments)
    after, statistics = pass_function(before)
    after_observation = evaluate_mir(after, case.arguments)
    changed = before.digest != after.digest
    semantic_equal = _semantic_key(before_observation) == _semantic_key(after_observation)
    before_instructions = {
        instruction.id: instruction.to_dict()
        for function in before.functions
        for block in function.blocks
        for instruction in block.instructions
    }
    after_instructions = {
        instruction.id: instruction.to_dict()
        for function in after.functions
        for block in function.blocks
        for instruction in block.instructions
    }
    changed_instruction_ids = sorted(
        instruction_id
        for instruction_id in before_instructions.keys() & after_instructions.keys()
        if before_instructions[instruction_id] != after_instructions[instruction_id]
    )
    removed_instruction_ids = sorted(
        before_instructions.keys() - after_instructions.keys()
    )
    added_instruction_ids = sorted(
        after_instructions.keys() - before_instructions.keys()
    )
    return {
        "expected_to_fire": expected_to_fire,
        "changed": changed,
        "expectation_met": changed is expected_to_fire,
        "semantic_equal": semantic_equal,
        "source_sha256": hashlib.sha256(case.source.encode("utf-8")).hexdigest(),
        "arguments": list(case.arguments),
        "before_digest": before.digest,
        "after_digest": after.digest,
        "before_observation": before_observation.to_dict(),
        "after_observation": after_observation.to_dict(),
        "statistics": statistics.to_dict(),
        "structural_delta": {
            "instructions_before": len(before_instructions),
            "instructions_after": len(after_instructions),
            "changed_instruction_ids": changed_instruction_ids,
            "removed_instruction_ids": removed_instruction_ids,
            "added_instruction_ids": added_instruction_ids,
        },
        "missed_reason": None if changed else case.missed_reason,
        "before_mir": before.to_dict(),
        "after_mir": after.to_dict(),
    }


def run_optimizer_evidence() -> dict[str, Any]:
    expected_names = {function.__name__ for function in OPTIMIZATION_PIPELINE} | {
        "region_ownership_lowering"
    }
    if set(_CASES) != expected_names:
        raise AssertionError(
            f"optimizer evidence cases drifted: cases={sorted(_CASES)}, passes={sorted(expected_names)}"
        )
    passes = []
    failures = []
    for pass_name in sorted(expected_names):
        positive_case, negative_case = _CASES[pass_name]
        positive = _run_case(pass_name, positive_case, expected_to_fire=True)
        negative = _run_case(pass_name, negative_case, expected_to_fire=False)
        passed = (
            positive["expectation_met"]
            and negative["expectation_met"]
            and positive["semantic_equal"]
            and negative["semantic_equal"]
        )
        observation = {
            "pass": pass_name,
            "version": OPTIMIZATION_PASS_VERSIONS[pass_name],
            "status": "PASS" if passed else "FAIL",
            "positive": positive,
            "negative": negative,
        }
        passes.append(observation)
        if not passed:
            failures.append(
                {
                    "pass": pass_name,
                    "positive_expectation_met": positive["expectation_met"],
                    "negative_expectation_met": negative["expectation_met"],
                    "positive_semantic_equal": positive["semantic_equal"],
                    "negative_semantic_equal": negative["semantic_equal"],
                }
            )
    return {
        "schema_version": OPTIMIZER_EVIDENCE_SCHEMA_VERSION,
        "kind": "MeldraStage06POptimizerEvidence",
        "status": "PASS" if not failures else "FAIL",
        "pass_count": len(passes),
        "passes": passes,
        "failures": failures,
    }


def write_optimizer_evidence(
    destination: str | Path = "tools/benchmarks/merlo/benchmarks/meldra_stage06p_optimizer.json",
) -> dict[str, Any]:
    report = run_optimizer_evidence()
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


__all__ = [
    "OPTIMIZER_EVIDENCE_SCHEMA_VERSION",
    "run_optimizer_evidence",
    "write_optimizer_evidence",
]
