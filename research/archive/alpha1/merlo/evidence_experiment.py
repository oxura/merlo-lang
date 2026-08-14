from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from research.archive.historical_protocol.merlo.model import Evidence, EvidenceDependency


class ExperimentStatus:
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _as_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="replace")


@dataclass(frozen=True)
class CommandExperimentSpec:
    """A validation-only command description; it does not produce Meldra evidence."""

    experiment_id: str
    kind: str
    argv: tuple[str, ...]
    cwd: str
    timeout: float
    environment: tuple[tuple[str, str], ...] = ()
    dependencies: tuple[EvidenceDependency, ...] = ()

    def __post_init__(self) -> None:
        if not self.experiment_id or not self.kind:
            raise ValueError("experiment_id and kind are required")
        if not self.argv:
            raise ValueError("experiment argv cannot be empty")
        if self.timeout <= 0:
            raise ValueError("experiment timeout must be positive")
        object.__setattr__(self, "argv", tuple(str(item) for item in self.argv))
        object.__setattr__(
            self,
            "environment",
            tuple(sorted((str(key), str(value)) for key, value in self.environment)),
        )
        object.__setattr__(
            self,
            "dependencies",
            tuple(
                sorted(
                    set(self.dependencies),
                    key=lambda item: (item.kind, item.key, item.revision),
                )
            ),
        )

    @classmethod
    def create(
        cls,
        experiment_id: str,
        kind: str,
        argv: Sequence[str],
        *,
        cwd: str | os.PathLike[str],
        timeout: float,
        environment: Mapping[str, str] | Iterable[tuple[str, str]] = (),
        dependencies: Iterable[EvidenceDependency] = (),
    ) -> "CommandExperimentSpec":
        values = environment.items() if isinstance(environment, Mapping) else environment
        return cls(
            experiment_id=experiment_id,
            kind=kind,
            argv=tuple(argv),
            cwd=str(cwd),
            timeout=timeout,
            environment=tuple(values),
            dependencies=tuple(dependencies),
        )

    @property
    def dependency_keys(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted({(item.kind, item.key) for item in self.dependencies}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "kind": self.kind,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "timeout": self.timeout,
            "environment": [
                {"name": key, "value": value} for key, value in self.environment
            ],
            "dependencies": [item.to_dict() for item in self.dependencies],
            "dependency_count": len(self.dependencies),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CommandExperimentSpec":
        return cls(
            experiment_id=str(value["experiment_id"]),
            kind=str(value["kind"]),
            argv=tuple(str(item) for item in value["argv"]),
            cwd=str(value["cwd"]),
            timeout=float(value["timeout"]),
            environment=tuple(
                (str(item["name"]), str(item["value"]))
                if isinstance(item, Mapping)
                else (str(item[0]), str(item[1]))
                for item in value.get("environment", ())
            ),
            dependencies=tuple(
                EvidenceDependency.from_dict(dict(item))
                for item in value.get("dependencies", ())
            ),
        )


@dataclass(frozen=True)
class CommandExperimentResult:
    spec: CommandExperimentSpec
    status: str
    exit_code: int | None
    stdout_hash: str
    stderr_hash: str
    stdout_bytes: int
    stderr_bytes: int
    artifact_hash: str
    environment: tuple[tuple[str, str], ...]
    infrastructure_error: str | None = None
    timed_out: bool = False
    observed_at: str | None = None

    @property
    def successful(self) -> bool:
        return self.status == ExperimentStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "status": self.status,
            "successful": self.successful,
            "exit_code": self.exit_code,
            "stdout_hash": self.stdout_hash,
            "stderr_hash": self.stderr_hash,
            "stdout_bytes": self.stdout_bytes,
            "stderr_bytes": self.stderr_bytes,
            "artifact_hash": self.artifact_hash,
            "environment": [
                {"name": key, "value": value} for key, value in self.environment
            ],
            "infrastructure_error": self.infrastructure_error,
            "timed_out": self.timed_out,
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CommandExperimentResult":
        return cls(
            spec=CommandExperimentSpec.from_dict(value["spec"]),
            status=str(value["status"]),
            exit_code=(
                int(value["exit_code"])
                if value.get("exit_code") is not None
                else None
            ),
            stdout_hash=str(value["stdout_hash"]),
            stderr_hash=str(value["stderr_hash"]),
            stdout_bytes=int(value["stdout_bytes"]),
            stderr_bytes=int(value["stderr_bytes"]),
            artifact_hash=str(value["artifact_hash"]),
            environment=tuple(
                (str(item["name"]), str(item["value"]))
                if isinstance(item, Mapping)
                else (str(item[0]), str(item[1]))
                for item in value.get("environment", ())
            ),
            infrastructure_error=value.get("infrastructure_error"),
            timed_out=bool(value.get("timed_out", False)),
            observed_at=value.get("observed_at"),
        )


def _captured_environment(
    spec: CommandExperimentSpec, inherited: Mapping[str, str]
) -> tuple[tuple[str, str], ...]:
    values = {
        "inherited_environment_sha256": _stable_hash(tuple(sorted(inherited.items()))),
        "machine": platform.machine(),
        "platform": platform.system(),
        "platform_release": platform.release(),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
    }
    values.update({f"override:{key}": value for key, value in spec.environment})
    return tuple(sorted(values.items()))


def run_command_experiment(
    spec: CommandExperimentSpec,
    *,
    observed_at: str | None = None,
) -> CommandExperimentResult:
    """Run an isolated validation command without changing a ProgramIR or World."""

    inherited = dict(os.environ)
    command_environment = dict(inherited)
    command_environment.update(dict(spec.environment))
    captured_environment = _captured_environment(spec, inherited)
    exit_code: int | None = None
    stdout = b""
    stderr = b""
    infrastructure_error: str | None = None
    timed_out = False
    try:
        completed = subprocess.run(
            list(spec.argv),
            cwd=spec.cwd,
            timeout=spec.timeout,
            env=command_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
        )
        exit_code = completed.returncode
        stdout = _as_bytes(completed.stdout)
        stderr = _as_bytes(completed.stderr)
        status = (
            ExperimentStatus.PASSED
            if completed.returncode == 0
            else ExperimentStatus.FAILED
        )
    except subprocess.TimeoutExpired as error:
        stdout = _as_bytes(error.stdout)
        stderr = _as_bytes(error.stderr)
        status = ExperimentStatus.ERROR
        timed_out = True
        infrastructure_error = f"timeout after {spec.timeout:g} seconds"
    except OSError as error:
        status = ExperimentStatus.ERROR
        infrastructure_error = f"{type(error).__name__}: {error}"

    stdout_hash = hashlib.sha256(stdout).hexdigest()
    stderr_hash = hashlib.sha256(stderr).hexdigest()
    artifact_hash = _stable_hash(
        {
            "experiment_id": spec.experiment_id,
            "kind": spec.kind,
            "argv": spec.argv,
            "cwd": spec.cwd,
            "timeout": spec.timeout,
            "status": status,
            "exit_code": exit_code,
            "stdout_hash": stdout_hash,
            "stderr_hash": stderr_hash,
            "stdout_bytes": len(stdout),
            "stderr_bytes": len(stderr),
            "environment": captured_environment,
            "dependencies": tuple(
                (item.kind, item.key, item.revision) for item in spec.dependencies
            ),
            "infrastructure_error": infrastructure_error,
            "timed_out": timed_out,
        }
    )
    return CommandExperimentResult(
        spec=spec,
        status=status,
        exit_code=exit_code,
        stdout_hash=stdout_hash,
        stderr_hash=stderr_hash,
        stdout_bytes=len(stdout),
        stderr_bytes=len(stderr),
        artifact_hash=artifact_hash,
        environment=captured_environment,
        infrastructure_error=infrastructure_error,
        timed_out=timed_out,
        observed_at=observed_at,
    )


@dataclass(frozen=True)
class InvalidationSimulation:
    rerun_experiment_ids: tuple[str, ...]
    invalidated_evidence_ids: tuple[str, ...]
    preserved_evidence_ids: tuple[str, ...]
    changed_dependencies: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rerun_experiment_ids": list(self.rerun_experiment_ids),
            "rerun_experiment_count": len(self.rerun_experiment_ids),
            "invalidated_evidence_ids": list(self.invalidated_evidence_ids),
            "invalidated_evidence_count": len(self.invalidated_evidence_ids),
            "preserved_evidence_ids": list(self.preserved_evidence_ids),
            "preserved_evidence_count": len(self.preserved_evidence_ids),
            "input_evidence_count": (
                len(self.invalidated_evidence_ids) + len(self.preserved_evidence_ids)
            ),
            "changed_dependencies": [
                {"kind": kind, "key": key}
                for kind, key in self.changed_dependencies
            ],
            "changed_dependency_count": len(self.changed_dependencies),
        }


def _dependency_key(
    value: EvidenceDependency | tuple[str, str] | str,
) -> tuple[str, str]:
    if isinstance(value, EvidenceDependency):
        return value.kind, value.key
    if isinstance(value, str):
        kind, separator, key = value.partition(":")
        if not separator or not kind or not key:
            raise ValueError("string dependencies must use 'kind:key'")
        return kind, key
    kind, key = value
    return str(kind), str(key)


def simulate_evidence_invalidation(
    experiments: Iterable[CommandExperimentSpec],
    evidence_items: Iterable[Evidence],
    *,
    changed_dependencies: Iterable[
        EvidenceDependency | tuple[str, str] | str
    ],
) -> InvalidationSimulation:
    """Pure simulation: report what would rerun or become stale, mutate nothing."""

    changed = {_dependency_key(item) for item in changed_dependencies}
    rerun = {
        spec.experiment_id
        for spec in experiments
        if changed.intersection(spec.dependency_keys)
    }
    invalidated: set[str] = set()
    preserved: set[str] = set()
    for evidence in evidence_items:
        evidence_keys = {
            (dependency.kind, dependency.key)
            for dependency in evidence.dependencies
        }
        if changed.intersection(evidence_keys) or evidence.produced_by in rerun:
            invalidated.add(evidence.id)
        else:
            preserved.add(evidence.id)
    # Evidence IDs are semantic identities. If malformed input repeats one with
    # conflicting dependency metadata, conservatively invalidate it.
    preserved.difference_update(invalidated)
    return InvalidationSimulation(
        rerun_experiment_ids=tuple(sorted(rerun)),
        invalidated_evidence_ids=tuple(sorted(invalidated)),
        preserved_evidence_ids=tuple(sorted(preserved)),
        changed_dependencies=tuple(sorted(changed)),
    )
