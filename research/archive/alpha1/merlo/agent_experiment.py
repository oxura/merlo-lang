"""Frozen same-model agent A/B manifest and explicit measurement record."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from research.archive.alpha1.merlo.agent_trial import (
    UNMEASURED,
    AgentTrialHarness,
    AgentTrialReport,
    OpenAICompatibleProvider,
    TaskManifest,
    TrialBudget,
)
from research.archive.historical_protocol.merlo.stage04e_protocol import assert_stage04e_protocol


_ALPHA1_ROOT = Path(__file__).resolve().parents[1]


AGENT_EXPERIMENT_SCHEMA_VERSION = 1
AGENT_EXPERIMENT_MANIFEST_FILENAME = "meldra_agent_experiment_manifest.json"
AGENT_EXPERIMENT_REPORT_FILENAME = "meldra_agent_experiment.json"
AGENT_EXPERIMENT_MODEL = "accounts/fireworks/models/glm-5p2"
AGENT_EXPERIMENT_ENDPOINT = (
    "https://api.fireworks.ai/inference/v1/chat/completions"
)
AGENT_EXPERIMENT_ABLATIONS = (
    "meldra_without_task_capsule",
    "meldra_without_effects",
    "meldra_without_changeir",
    "meldra_without_capability_restrictions",
)
AGENT_EXPERIMENT_BUDGET = TrialBudget(
    wall_time_seconds=300.0,
    input_tokens=100_000,
    output_tokens=25_000,
    tool_calls=100,
    iterations=25,
)


@dataclass(frozen=True)
class AgentExperimentReport:
    paired: AgentTrialReport
    manifest_sha256: str
    protocol_sha256: str
    ablations: tuple[Mapping[str, Any], ...]
    environment: Mapping[str, Any]
    schema_version: int = AGENT_EXPERIMENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        paired_payload = self.paired.to_dict()
        measured = (
            self.paired.baseline.measured_tasks
            + self.paired.meldra.measured_tasks
        )
        return {
            "schema_version": self.schema_version,
            "kind": "MeldraSameModelAgentExperiment",
            "manifest_sha256": self.manifest_sha256,
            "protocol_sha256": self.protocol_sha256,
            "provider": self.paired.provider.to_dict(),
            "constraints": {
                "same_model": True,
                "equal_budget": True,
                "equal_hardware": True,
                "budget": AGENT_EXPERIMENT_BUDGET.to_dict(),
            },
            "environment": dict(self.environment),
            "paired": paired_payload,
            "ablations": [dict(item) for item in self.ablations],
            "evidence_level": (
                "SAME_MODEL_PAIRED_MEASURED"
                if measured
                else "UNMEASURED_PROVIDER_UNAVAILABLE"
            ),
            "decision": (
                "MEASURED_REQUIRES_GATE_EVALUATION"
                if measured
                else "UNMEASURED"
            ),
            "limitations": [
                "An unavailable provider produces zero measured denominators; no replay, fake provider, or generated score substitutes for a real model run.",
                "Ablation arms remain UNMEASURED until the same provider identity can execute every frozen arm under the same budget and hardware.",
                "Tasks are planner-generated source-product transformations and are not independent human-authored requests.",
            ],
        }


def _task_prompt(target: Mapping[str, Any]) -> str:
    task = target["task"]
    operation = str(task["operation"])
    old_locator = str(target["old_locator"])
    new_locator = str(target["new_locator"])
    if operation == "change_signature":
        instruction = (
            f"Change the signature of {old_locator} to {task['payload']} and "
            "migrate every statically known caller."
        )
    elif operation == "move":
        instruction = (
            f"Move {old_locator} to {new_locator} and migrate imports and callers."
        )
    else:
        instruction = (
            f"Rename {old_locator} to {new_locator} and migrate references."
        )
    return (
        instruction
        + " Preserve behavior and every unrelated public API. Run the declared "
        "test suite. Final contracts must include tests-pass and public-api-preserved."
    )


def build_agent_experiment_manifest(
    root: str | Path = Path(__file__).resolve().parents[1],
) -> dict[str, Any]:
    root_path = _ALPHA1_ROOT if str(root) == "." else Path(root)
    protocol = assert_stage04e_protocol(root_path)
    external_manifest_path = (
        root_path / "tools" / "benchmarks" / "merlo" / "benchmarks" / "meldra_external_trials_manifest.json"
    )
    external_report_path = (
        root_path / "tools" / "benchmarks" / "merlo" / "benchmarks" / "meldra_external_trials.json"
    )
    external_raw = external_manifest_path.read_bytes()
    external = json.loads(external_raw)
    external_report = json.loads(external_report_path.read_text(encoding="utf-8"))
    changed_files = {
        item["task_id"]: tuple(item["changed_files"])
        for item in external_report["observations"]
    }
    projects = {item["id"]: item for item in external["projects"]}
    tasks = []
    for target in external["targets"]:
        task = target["task"]
        project = projects[task["project"]]
        manifest = TaskManifest(
            task_id=str(task["id"]),
            repo=str(project["metadata"].get("url", project["id"])),
            root=str(project["root"]),
            prompt=_task_prompt(target),
            expected_files=changed_files.get(str(task["id"]), ()),
            expected_contracts=("public-api-preserved", "tests-pass"),
            test_argv=(
                "env",
                "PYTHONPATH=src",
                sys.executable,
                "-m",
                "pytest",
                "-q",
            ),
            budget=AGENT_EXPERIMENT_BUDGET,
        )
        tasks.append(manifest.to_dict())
    return {
        "schema_version": AGENT_EXPERIMENT_SCHEMA_VERSION,
        "kind": "MeldraSameModelAgentManifest",
        "protocol_sha256": protocol.protocol_sha256,
        "external_trial_manifest_sha256": hashlib.sha256(external_raw).hexdigest(),
        "provider": {
            "endpoint": AGENT_EXPERIMENT_ENDPOINT,
            "model": AGENT_EXPERIMENT_MODEL,
            "same_model_required": True,
        },
        "budget": AGENT_EXPERIMENT_BUDGET.to_dict(),
        "arms": ["baseline", "meldra"],
        "ablation_arms": list(AGENT_EXPERIMENT_ABLATIONS),
        "tasks": tasks,
        "task_count": len(tasks),
        "selection": "All frozen 30x3 external source-product transformations.",
    }


def load_agent_experiment_manifest(
    root: str | Path = Path(__file__).resolve().parents[1],
) -> tuple[tuple[TaskManifest, ...], str]:
    root_path = _ALPHA1_ROOT if str(root) == "." else Path(root)
    path = root_path / "tools" / "benchmarks" / "merlo" / "benchmarks" / AGENT_EXPERIMENT_MANIFEST_FILENAME
    raw = path.read_bytes()
    payload = json.loads(raw)
    tasks = tuple(TaskManifest.from_dict(item) for item in payload["tasks"])
    return tasks, hashlib.sha256(raw).hexdigest()


def _environment() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "process_isolation": "fresh copied workspace per arm and task sequence",
    }


def run_agent_experiment(
    root: str | Path = Path(__file__).resolve().parents[1],
    *,
    api_key: str | None = None,
) -> AgentExperimentReport:
    root_path = _ALPHA1_ROOT if str(root) == "." else Path(root)
    protocol = assert_stage04e_protocol(root_path)
    tasks, manifest_sha256 = load_agent_experiment_manifest(root_path)
    provider = OpenAICompatibleProvider(
        endpoint=AGENT_EXPERIMENT_ENDPOINT,
        model=AGENT_EXPERIMENT_MODEL,
        api_key=api_key,
        timeout_seconds=AGENT_EXPERIMENT_BUDGET.wall_time_seconds,
        provider_name="fireworks",
    )
    paired = AgentTrialHarness(provider).run(tasks)
    unavailable = provider.unavailable_reason
    ablations = tuple(
        {
            "arm": arm,
            "status": UNMEASURED,
            "measured_tasks": 0,
            "reason": unavailable or "ablation execution is not enabled in this harness revision",
        }
        for arm in AGENT_EXPERIMENT_ABLATIONS
    )
    return AgentExperimentReport(
        paired,
        manifest_sha256,
        protocol.protocol_sha256,
        ablations,
        _environment(),
    )


__all__ = [
    "AGENT_EXPERIMENT_ABLATIONS",
    "AGENT_EXPERIMENT_BUDGET",
    "AGENT_EXPERIMENT_ENDPOINT",
    "AGENT_EXPERIMENT_MANIFEST_FILENAME",
    "AGENT_EXPERIMENT_MODEL",
    "AGENT_EXPERIMENT_REPORT_FILENAME",
    "AGENT_EXPERIMENT_SCHEMA_VERSION",
    "AgentExperimentReport",
    "build_agent_experiment_manifest",
    "load_agent_experiment_manifest",
    "run_agent_experiment",
]
