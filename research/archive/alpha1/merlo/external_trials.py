"""Stage 0.4E external 30x3 apply-and-test trials."""

from __future__ import annotations

import os
import hashlib
import json
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from research.archive.alpha1.merlo.external_bench import (
    ExternalAcceptanceProbe,
    ExternalProject,
    ExternalTaskSpec,
    generate_pilot_manifest,
    run_apply_and_test_validation,
)
from research.archive.historical_protocol.merlo.stage04e_protocol import assert_stage04e_protocol
from research.archive.historical_protocol.merlo.world import SoftwareWorld


EXTERNAL_TRIAL_SCHEMA_VERSION = 1
EXTERNAL_TRIAL_SEED = 20260810
EXTERNAL_TRIAL_MANIFEST_FILENAME = "meldra_external_trials_manifest.json"
_EXTERNAL_PROJECTS = (
    (
        "pluggy",
        "/tmp/meldra-external-corpus/pluggy",
        "plugin-architecture",
        42,
        "https://github.com/pytest-dev/pluggy.git",
        "f632a4d1b69e32e210063a91a71ae32fb3ac150c",
    ),
    (
        "click",
        "/tmp/meldra-external-corpus/click",
        "cli-library",
        6,
        "https://github.com/pallets/click.git",
        "9c4dfdaebe0e6b2aabc566eb81f6f10eb5cd6ea1",
    ),
    (
        "boltons",
        "/tmp/meldra-external-corpus/boltons",
        "utility-library",
        42,
        "https://github.com/mahmoud/boltons.git",
        "580a9c2d12755d472e534ca3e277e8f7e3ada49e",
    ),
)


@dataclass(frozen=True)
class TrialTarget:
    task: ExternalTaskSpec
    old_locator: str
    new_locator: str
    public: bool
    expected_world_revision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.to_dict(),
            "old_locator": self.old_locator,
            "new_locator": self.new_locator,
            "public": self.public,
            "expected_world_revision": self.expected_world_revision,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TrialTarget":
        return cls(
            ExternalTaskSpec.from_dict(dict(value["task"])),
            str(value["old_locator"]),
            str(value["new_locator"]),
            bool(value["public"]),
            str(value["expected_world_revision"]),
        )


@dataclass(frozen=True)
class ExternalTrialObservation:
    task_id: str
    project: str
    operation: str
    old_locator: str
    new_locator: str
    target_public: bool
    plan_ready: bool | None
    apply_succeeded: bool | None
    collection_guard: bool
    passed_count_guard: bool
    public_api_guard: bool
    acceptance_errors: tuple[str, ...]
    restoration_succeeded: bool
    source_unchanged: bool | None
    infrastructure_errors: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    changed_files: tuple[str, ...]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "project": self.project,
            "operation": self.operation,
            "old_locator": self.old_locator,
            "new_locator": self.new_locator,
            "target_public": self.target_public,
            "plan_ready": self.plan_ready,
            "apply_succeeded": self.apply_succeeded,
            "collection_guard": self.collection_guard,
            "passed_count_guard": self.passed_count_guard,
            "public_api_guard": self.public_api_guard,
            "restoration_succeeded": self.restoration_succeeded,
            "acceptance_errors": list(self.acceptance_errors),
            "source_unchanged": self.source_unchanged,
            "infrastructure_errors": list(self.infrastructure_errors),
            "blocked_reasons": list(self.blocked_reasons),
            "changed_files": list(self.changed_files),
            "status": self.status,
        }


@dataclass(frozen=True)
class ExternalTrialsReport:
    observations: tuple[ExternalTrialObservation, ...]
    manifest_sha256: str
    protocol_sha256: str
    workers: int
    schema_version: int = EXTERNAL_TRIAL_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        operations: dict[str, dict[str, int | float]] = {}
        for operation in ("rename", "move", "change_signature"):
            values = tuple(
                item for item in self.observations if item.operation == operation
            )
            passed = sum(item.status == "PASSED" for item in values)
            operations[operation] = {
                "trials": len(values),
                "passed": passed,
                "success_rate": round(passed / len(values), 6) if values else 0.0,
                "collection_guard_passed": sum(
                    item.collection_guard for item in values
                ),
                "passed_count_guard_passed": sum(
                    item.passed_count_guard for item in values
                ),
                "public_api_guard_passed": sum(
                    item.public_api_guard for item in values
                ),
                "unique_targets": len(
                    {
                        (item.project, item.old_locator)
                        for item in values
                    }
                ),
                "restoration_passed": sum(
                    item.restoration_succeeded for item in values
                ),
            }
        return {
            "schema_version": self.schema_version,
            "manifest_sha256": self.manifest_sha256,
            "protocol_sha256": self.protocol_sha256,
            "execution": {
                "workers": self.workers,
                "mode": "process_pool" if self.workers > 1 else "serial",
                "isolated_workspace_per_trial": True,
            },
            "statistical_units": {
                "external_trials": len(self.observations),
                "projects": len({item.project for item in self.observations}),
                "operations": 3,
                "trials_per_operation": 30,
                "unique_targets": len(
                    {
                        (item.project, item.operation, item.old_locator)
                        for item in self.observations
                    }
                ),
                "human_authors": 0,
            },
            "operations": operations,
            "infrastructure_failures": sum(
                bool(item.infrastructure_errors) for item in self.observations
            ),
            "source_restoration_failures": sum(
                not item.restoration_succeeded for item in self.observations
            ),
            "observations": [item.to_dict() for item in self.observations],
            "evidence_level": "EXTERNAL_SOURCE_PRODUCT_GENERATED_CHANGES",
            "decision": "NO_GO_LANGUAGE_ALPHA",
            "limitations": [
                "Candidates are deterministic planner-ready transformations, not independently authored change requests.",
                "Pluggy, Click, and Boltons are represented; repeated trials within a project are correlated.",
                "Move trials may reuse a source target with distinct destination modules when conservative preflight leaves too few unique move targets.",
                "The public API guard excludes the intentionally renamed, moved, or signature-changed target and requires every other public entity snapshot to remain equal.",
            ],
        }


def _new_locator(task: ExternalTaskSpec, entity: Any) -> str:
    if task.operation == "rename":
        parts = entity.qualname.split(".")
        parts[-1] = task.payload
        qualname = ".".join(parts)
        return f"{entity.module}.{qualname}"
    if task.operation == "move":
        return f"{task.payload}.{entity.qualname}"
    return entity.fqname


def build_external_trial_manifest(
    root: str | Path = Path(__file__).resolve().parents[1],
) -> dict[str, Any]:
    root_path = Path(root)
    assert_stage04e_protocol(root_path)
    targets = []
    projects = []
    for (
        project_id,
        project_root,
        category,
        safe_quota,
        url,
        revision,
    ) in _EXTERNAL_PROJECTS:
        project = ExternalProject(
            project_id,
            project_root,
            category,
            metadata=(("url", url), ("revision", revision)),
        )
        generated = generate_pilot_manifest(
            (project,),
            seed=EXTERNAL_TRIAL_SEED,
            safe_per_project=safe_quota,
            unsafe_per_project=0,
            balanced_safe=True,
            allow_repeated_move_targets=True,
        )
        with tempfile.TemporaryDirectory(prefix="meldra-external-trial-world-") as temporary:
            world = SoftwareWorld.scan(
                project.root, state_path=Path(temporary) / "world.json"
            )
            for task in generated.tasks:
                entity = world.program.entity(task.target)
                targets.append(
                    TrialTarget(
                        task,
                        entity.fqname,
                        _new_locator(task, entity),
                        entity.public,
                        world.program.world_revision,
                    )
                )
        projects.append(project.to_dict())
    counts = Counter(item.task.operation for item in targets)
    if counts != {
        "rename": 30,
        "move": 30,
        "change_signature": 30,
    }:
        raise RuntimeError(f"external trial operation denominator drift: {counts}")
    return {
        "schema_version": EXTERNAL_TRIAL_SCHEMA_VERSION,
        "kind": "MeldraExternal30x3TrialManifest",
        "seed": EXTERNAL_TRIAL_SEED,
        "projects": projects,
        "targets": [item.to_dict() for item in targets],
        "operation_counts": dict(sorted(counts.items())),
        "selection": (
            "Planner-ready module/source candidates excluding tests, docs, demos, "
            "examples, samples, and tutorials; 14 per operation from Pluggy, "
            "2 from Click, and 14 from Boltons."
        ),
        "label_source": "generated_planner_preflight_not_human",
    }


def _load_manifest(root: Path) -> tuple[dict[str, ExternalProject], tuple[TrialTarget, ...], str]:
    path = root / "tools" / "benchmarks" / "merlo" / "benchmarks" / EXTERNAL_TRIAL_MANIFEST_FILENAME
    raw = path.read_bytes()
    payload = json.loads(raw)
    projects = {
        item["id"]: ExternalProject.from_dict(item)
        for item in payload["projects"]
    }
    targets = tuple(TrialTarget.from_dict(item) for item in payload["targets"])
    return projects, targets, hashlib.sha256(raw).hexdigest()


def _public_api_probe(
    root: Path, target: TrialTarget
) -> ExternalAcceptanceProbe:
    excluded = tuple(sorted({target.old_locator, target.new_locator}))
    target_names = tuple(
        sorted({target.old_locator.rsplit(".", 1)[-1], target.new_locator.rsplit(".", 1)[-1]})
    )
    script = (
        "import functools,json,sys;"
        f"sys.path.insert(0,{str(root.resolve())!r});"
        "from research.archive.historical_protocol.merlo.analyzer import scan_python;"
        "p=scan_python('.');"
        f"excluded=set({excluded!r});"
        f"target_names={target_names!r};"
        "value=sorted((e.fqname,e.kind,functools.reduce("
        "lambda value,name:value.replace(name,'<TARGET>'),target_names,e.signature)) "
        "for e in p.entities if e.public and not any("
        "e.fqname == prefix or e.fqname.startswith(prefix + '.') "
        "for prefix in excluded));"
        "print(json.dumps(value,separators=(',',':')))"
    )
    return ExternalAcceptanceProbe.create(
        "public_api_except_target",
        (sys.executable, "-I", "-c", script),
        kind="json",
    )


def _pytest_probe(name: str, *arguments: str, kind: str) -> ExternalAcceptanceProbe:
    return ExternalAcceptanceProbe.create(
        name,
        (
            "env",
            "PYTHONPATH=src",
            sys.executable,
            "-m",
            "pytest",
            *arguments,
        ),
        kind=kind,
    )


def _observe_trial(
    root: Path,
    project: ExternalProject,
    target: TrialTarget,
    *,
    timeout: float,
) -> ExternalTrialObservation:
    probes = (
        _pytest_probe(
            "pytest_collection", "--collect-only", "-q", kind="pytest_collection"
        ),
        _pytest_probe("pytest_passed", "-q", kind="pytest_passed_count"),
        _public_api_probe(root, target),
    )
    result = run_apply_and_test_validation(
        project,
        target.task,
        (),
        expected_world_revision=target.expected_world_revision,
        timeout=timeout,
        acceptance_probes=probes,
    )
    acceptance = {item.probe.name: item for item in result.acceptance}
    collection_guard = acceptance.get("pytest_collection") is not None and (
        acceptance["pytest_collection"].matched is True
    )
    passed_count_guard = acceptance.get("pytest_passed") is not None and (
        acceptance["pytest_passed"].matched is True
    )
    public_api_guard = acceptance.get("public_api_except_target") is not None and (
        acceptance["public_api_except_target"].matched is True
    )
    infrastructure = tuple(
        f"{item.stage}:{item.kind}:{item.message}"
        for item in result.infrastructure_errors
        if item.stage != "acceptance_changed"
    )
    acceptance_errors = tuple(
        (
            f"{item.probe.name}:{item.error or 'mismatch'}:"
            f"stdout={((item.changed.stdout if item.changed else '')[-1000:])!r}:"
            f"stderr={((item.changed.stderr if item.changed else '')[-1000:])!r}"
        )
        for item in result.acceptance
        if item.matched is not True
    )
    passed = (
        result.plan_ready is True
        and result.apply_succeeded is True
        and collection_guard
        and passed_count_guard
        and public_api_guard
        and result.restoration_succeeded
        and result.source_unchanged is True
        and not infrastructure
    )
    return ExternalTrialObservation(
        target.task.id,
        project.id,
        target.task.operation,
        target.old_locator,
        target.new_locator,
        target.public,
        result.plan_ready,
        result.apply_succeeded,
        collection_guard,
        passed_count_guard,
        public_api_guard,
        acceptance_errors,
        result.restoration_succeeded,
        result.source_unchanged,
        infrastructure,
        result.blocked_reasons,
        result.changed_files,
        "PASSED" if passed else "FAILED",
    )


def _observe_trial_job(
    value: tuple[Path, ExternalProject, TrialTarget, float],
) -> ExternalTrialObservation:
    root, project, target, timeout = value
    return _observe_trial(root, project, target, timeout=timeout)


def run_external_trials(
    root: str | Path = Path(__file__).resolve().parents[1],
    *,
    timeout: float = 90.0,
    workers: int | None = None,
) -> ExternalTrialsReport:
    root_path = Path(root).resolve()
    protocol = assert_stage04e_protocol(root_path)
    projects, targets, manifest_sha256 = _load_manifest(root_path)
    resolved_workers = (
        min(8, os.cpu_count() or 1)
        if workers is None
        else workers
    )
    if resolved_workers < 1:
        raise ValueError("workers must be positive")
    jobs = tuple(
        (
            root_path,
            projects[target.task.project],
            target,
            timeout,
        )
        for target in targets
    )
    if resolved_workers == 1:
        observations = tuple(_observe_trial_job(job) for job in jobs)
    else:
        with ProcessPoolExecutor(max_workers=resolved_workers) as executor:
            observations = tuple(
                executor.map(_observe_trial_job, jobs, chunksize=1)
            )
    return ExternalTrialsReport(
        observations,
        manifest_sha256,
        protocol.protocol_sha256,
        resolved_workers,
    )


__all__ = [
    "EXTERNAL_TRIAL_MANIFEST_FILENAME",
    "EXTERNAL_TRIAL_SCHEMA_VERSION",
    "EXTERNAL_TRIAL_SEED",
    "ExternalTrialObservation",
    "ExternalTrialsReport",
    "TrialTarget",
    "build_external_trial_manifest",
    "run_external_trials",
]
