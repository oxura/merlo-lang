from __future__ import annotations

import json
import math
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bench import EvolutionCase, stage02_evolution_cases
from .context import compile_context
from .model import EditCapability
from .world import SoftwareWorld


@dataclass(frozen=True)
class HypothesisReport:
    tasks: int
    expected_edits: int
    unsafe_cases: int
    baseline_matched_edits: int
    baseline_predicted_edits: int
    baseline_false_safe_cases: int
    meldra_matched_edits: int
    meldra_predicted_edits: int
    meldra_false_safe_cases: int
    baseline_edit_precision: float
    baseline_edit_recall: float
    baseline_false_safe_rate: float
    meldra_edit_precision: float
    meldra_edit_recall: float
    meldra_false_safe_rate: float
    full_source_bytes: int
    task_capsule_bytes: int
    estimated_full_source_tokens: int
    estimated_task_capsule_tokens: int
    findings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": (
                "deterministic proxy experiment; no coding model was invoked"
            ),
            "tasks": self.tasks,
            "baseline": {
                "strategy": (
                    "optimistic workspace-wide textual identifier replacement; "
                    "matches are capped by the expected edit count"
                ),
                "edit_precision": round(self.baseline_edit_precision, 6),
                "edit_recall": round(self.baseline_edit_recall, 6),
                "false_safe_rate": round(self.baseline_false_safe_rate, 6),
                "counts": {
                    "matched_edits": self.baseline_matched_edits,
                    "predicted_edits": self.baseline_predicted_edits,
                    "expected_edits": self.expected_edits,
                    "false_safe_cases": self.baseline_false_safe_cases,
                    "unsafe_cases": self.unsafe_cases,
                },
            },
            "meldra": {
                "strategy": "ProgramIR + TaskCapsule + ChangeIR + obligations",
                "edit_precision": round(self.meldra_edit_precision, 6),
                "edit_recall": round(self.meldra_edit_recall, 6),
                "false_safe_rate": round(self.meldra_false_safe_rate, 6),
                "counts": {
                    "matched_edits": self.meldra_matched_edits,
                    "predicted_edits": self.meldra_predicted_edits,
                    "expected_edits": self.expected_edits,
                    "false_safe_cases": self.meldra_false_safe_cases,
                    "unsafe_cases": self.unsafe_cases,
                },
            },
            "context": {
                "full_source_bytes": self.full_source_bytes,
                "task_capsule_bytes": self.task_capsule_bytes,
                "estimated_full_source_tokens": self.estimated_full_source_tokens,
                "estimated_task_capsule_tokens": self.estimated_task_capsule_tokens,
                "token_estimate": "ceil(UTF-8 bytes / 4), not tokenizer output",
            },
            "findings": list(self.findings),
        }


def run_hypothesis_experiment() -> HypothesisReport:
    cases = tuple(
        case for case in stage02_evolution_cases() if case.operation == "rename"
    )
    baseline_tp = baseline_predicted = baseline_expected = 0
    baseline_false_safe = baseline_unsafe = 0
    meldra_tp = meldra_predicted = meldra_expected = 0
    meldra_false_safe = meldra_unsafe = 0
    full_source_bytes = capsule_bytes = 0
    for case in cases:
        old_name = case.target.rsplit(".", 1)[-1]
        expected = sum(count for _file, _reason, count in case.expected_edits)
        predicted = sum(source.count(old_name) for source in case.files.values())
        baseline_tp += min(predicted, expected)
        baseline_predicted += predicted
        baseline_expected += expected
        if not case.expected_ready:
            baseline_unsafe += 1
            baseline_false_safe += 1
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_case(root, case)
            world = SoftwareWorld.scan(root)
            entity = world.program.entity(case.target)
            capsule = compile_context(
                world.program,
                entity.id,
                goal=f"Benchmark {case.name}",
            ).to_dict()
            full_source_bytes += sum(
                len(source.encode("utf-8")) for source in case.files.values()
            )
            capsule_bytes += len(
                json.dumps(
                    capsule,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            capability = EditCapability.rename(
                entity.id,
                allow_public_api_break=True,
                allow_new_dependencies=True,
            )
            plan = world.plan_rename(entity.id, case.payload, capability)
            actual_edits = Counter((item.file, item.reason) for item in plan.edits)
            expected_edits = Counter(
                {
                    (file, reason): count
                    for file, reason, count in case.expected_edits
                }
            )
            meldra_tp += sum((actual_edits & expected_edits).values())
            meldra_predicted += sum(actual_edits.values())
            meldra_expected += sum(expected_edits.values())
            if not case.expected_ready:
                meldra_unsafe += 1
                meldra_false_safe += int(plan.ready)

    baseline_precision = _ratio(baseline_tp, baseline_predicted)
    baseline_fsr = _ratio(baseline_false_safe, baseline_unsafe)
    meldra_precision = _ratio(meldra_tp, meldra_predicted)
    meldra_recall = _ratio(meldra_tp, meldra_expected)
    meldra_fsr = _ratio(meldra_false_safe, meldra_unsafe)
    findings: list[str] = []
    if meldra_fsr < baseline_fsr:
        findings.append(
            "typed uncertainty prevented unsafe automatic changes missed by the text baseline"
        )
    if meldra_precision > baseline_precision:
        findings.append(
            "semantic binding avoided shadowed aliases and unrelated textual occurrences"
        )
    if capsule_bytes < full_source_bytes:
        findings.append("Task Capsules reduced deterministic context bytes")
    else:
        findings.append(
            "Task Capsules were larger than full source on these tiny fixtures; context economy is not yet demonstrated"
        )
    findings.append(
        "model task success, tool calls, and iteration count remain unmeasured until a controlled model runner is available"
    )
    return HypothesisReport(
        tasks=len(cases),
        expected_edits=baseline_expected,
        unsafe_cases=baseline_unsafe,
        baseline_matched_edits=baseline_tp,
        baseline_predicted_edits=baseline_predicted,
        baseline_false_safe_cases=baseline_false_safe,
        meldra_matched_edits=meldra_tp,
        meldra_predicted_edits=meldra_predicted,
        meldra_false_safe_cases=meldra_false_safe,
        baseline_edit_precision=baseline_precision,
        baseline_edit_recall=_ratio(baseline_tp, baseline_expected),
        baseline_false_safe_rate=baseline_fsr,
        meldra_edit_precision=meldra_precision,
        meldra_edit_recall=meldra_recall,
        meldra_false_safe_rate=meldra_fsr,
        full_source_bytes=full_source_bytes,
        task_capsule_bytes=capsule_bytes,
        estimated_full_source_tokens=math.ceil(full_source_bytes / 4),
        estimated_task_capsule_tokens=math.ceil(capsule_bytes / 4),
        findings=tuple(findings),
    )


def _write_case(root: Path, case: EvolutionCase) -> None:
    for relative, source in case.files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0
