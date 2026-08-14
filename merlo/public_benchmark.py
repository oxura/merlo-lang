"""Public native benchmark v1 wrapper and report validator.

The alpha-performance module owns the measurement schedule.  This module owns
only the public lock, provenance, report contract, and failure persistence.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import alpha_performance as alpha

SCHEMA_VERSION = "merlo.public-benchmark.v1"
CLAIM_ID = "public-native-three-workload-v1"
LOCK_PATH = "benchmarks/alpha_performance/workloads.json"
CONTROLLED_BUILD_ENVIRONMENT = {
    "LC_ALL": "C",
    "TZ": "UTC",
    "SOURCE_DATE_EPOCH": "0",
}


class PublicBenchmarkError(ValueError):
    """Raised when a public benchmark lock/report is invalid."""


class PublicBenchmarkOutputError(OSError):
    """Raised when a report cannot be created without overwriting evidence."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object) -> str:
    return _sha256_bytes(value if isinstance(value, bytes) else _canonical(value))


def _hex(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _path(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else root / candidate


def _length_delimited_digest(files: Sequence[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for relative, payload in sorted(files, key=lambda item: item[0]):
        path_bytes = relative.encode("utf-8")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def compiler_input_tree_sha256(root: str | Path = ".") -> str:
    """Hash compiler Python and stdlib inputs with unambiguous framing."""
    base = Path(root).resolve()
    files: list[tuple[str, bytes]] = []
    merlo = base / "merlo"
    if merlo.is_dir():
        files.extend(
            (path.relative_to(base).as_posix(), path.read_bytes())
            for path in merlo.rglob("*.py")
            if path.is_file()
        )
    stdlib = base / "stdlib"
    if stdlib.is_dir():
        files.extend(
            (path.relative_to(base).as_posix(), path.read_bytes())
            for path in stdlib.rglob("*")
            if path.is_file()
        )
    return _length_delimited_digest(files)


def _executable_hash(path: str | Path | None) -> str | None:
    if path is None:
        return None
    try:
        candidate = Path(path).resolve()
        return _sha256_bytes(candidate.read_bytes()) if candidate.is_file() else None
    except OSError:
        return None


def _version_record(executable: str | Path | None) -> dict[str, Any]:
    if executable is None:
        return {"path": None, "binary_sha256": None, "version_sha256": None, "version": None}
    resolved = shutil.which(str(executable)) or str(executable)
    candidate = Path(resolved).resolve()
    record: dict[str, Any] = {
        "path": str(candidate) if candidate.is_file() else None,
        "binary_sha256": _executable_hash(candidate),
        "version_sha256": None,
        "version": None,
    }
    try:
        completed = subprocess.run(
            (str(candidate), "--version"),
            capture_output=True,
            check=False,
            timeout=5,
            env={**CONTROLLED_BUILD_ENVIRONMENT, "PATH": os.environ.get("PATH", "")},
        )
        output = (completed.stdout or b"") + (completed.stderr or b"")
        record["version_sha256"] = _sha256_bytes(output)
        record["version"] = output.decode("utf-8", errors="replace").splitlines()[0] if output else None
        record["version_returncode"] = completed.returncode
    except (OSError, subprocess.SubprocessError):
        record["version_returncode"] = None
    return record


def _read_lock(root: Path) -> tuple[dict[str, Any], bytes, str]:
    path = root / LOCK_PATH
    try:
        payload = path.read_bytes()
        lock = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicBenchmarkError(f"cannot read workload lock: {exc}") from exc
    if not isinstance(lock, dict):
        raise PublicBenchmarkError("workload lock root must be an object")
    return lock, payload, _sha256_bytes(payload)


def load_authoritative_workload_lock(root: str | Path = ".") -> tuple[tuple[alpha.FrozenWorkload, ...], dict[str, Any]]:
    """Load the checked-in lock and reject divergence from alpha's frozen records."""
    base = Path(root).resolve()
    lock, payload, lock_sha256 = _read_lock(base)
    if lock.get("schema") != alpha.WORKLOAD_SCHEMA_VERSION:
        raise PublicBenchmarkError("unsupported workload lock schema")
    if tuple(lock.get("arms", ())) != alpha.ARMS or tuple(lock.get("required_arms", ())) != alpha.REQUIRED_ARMS or tuple(lock.get("optional_arms", ())) != alpha.OPTIONAL_ARMS:
        raise PublicBenchmarkError("workload lock arm protocol mismatch")
    protocol = lock.get("protocol", {})
    if protocol and protocol != {
        "seed": alpha.DEFAULT_SEED,
        "warmups": alpha.DEFAULT_WARMUPS,
        "measured_runs": alpha.DEFAULT_MEASURED_RUNS,
        "sample_replicates": alpha.DEFAULT_SAMPLE_REPLICATES,
        "bootstrap_replicates": alpha.BOOTSTRAP_REPLICATES,
        "sample_aggregation": "minimum",
        "schedule": "seeded_randomized_strictly_sequential",
        "mad_limit": alpha.MAD_LIMIT,
        "ratio_limits": dict(alpha.CLASS_RATIO_LIMITS),
    }:
        raise PublicBenchmarkError("workload lock protocol mismatch")
    items = lock.get("workloads")
    if not isinstance(items, list):
        raise PublicBenchmarkError("workload lock workloads must be a list")
    try:
        parsed = tuple(alpha._workload_from_dict(item) for item in items)
    except (TypeError, KeyError, ValueError) as exc:
        raise PublicBenchmarkError("invalid workload lock item") from exc
    if tuple(item.id for item in parsed) != tuple(item.id for item in alpha.FROZEN_WORKLOADS):
        raise PublicBenchmarkError("workload lock must contain all frozen workloads in order")
    if any(item.to_dict() != frozen.to_dict() for item, frozen in zip(parsed, alpha.FROZEN_WORKLOADS)):
        raise PublicBenchmarkError("authoritative workload lock diverges from runner lock")
    if len(parsed) != 3 or len({item.workload_class for item in parsed}) != 3:
        raise PublicBenchmarkError("public v1 requires exactly three workload classes")
    if lock.get("class_ratio_limits", dict(alpha.CLASS_RATIO_LIMITS)) != dict(alpha.CLASS_RATIO_LIMITS):
        raise PublicBenchmarkError("workload class ratio limits mismatch")
    return parsed, {
        "path": LOCK_PATH,
        "sha256": lock_sha256,
        "bytes": len(payload),
        "schema": lock.get("schema"),
        "digest": alpha.workload_digest(parsed),
        "items": [item.to_dict() for item in parsed],
    }


def _compiler_provenance(root: Path, builds: Mapping[str, Any], *, runner_sha256: str) -> dict[str, Any]:
    python = _version_record(sys.executable)
    c_records = [record for key, record in builds.items() if key.endswith(":native") or key.endswith(":c")]
    selected: Mapping[str, Any] = next((record for record in c_records if isinstance(record, Mapping) and record.get("compiler")), {})
    compiler = selected.get("compiler") if isinstance(selected, Mapping) else None
    c = _version_record(compiler if isinstance(compiler, str) else shutil.which("cc") or shutil.which("gcc"))
    return {
        "source_tree_sha256": compiler_input_tree_sha256(root),
        "runner_sha256": runner_sha256,
        "python": python,
        "c": c,
        "build_environment": dict(CONTROLLED_BUILD_ENVIRONMENT),
    }


def _artifact_provenance(artifacts: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for workload_id, records in artifacts.items():
        if not isinstance(records, Mapping):
            continue
        result[workload_id] = {}
        for arm, record in records.items():
            if not isinstance(record, Mapping):
                continue
            result[workload_id][arm] = {
                "status": record.get("status"),
                "source_sha256": record.get("source_sha256"),
                "generated_source": record.get("generated_source"),
                "generated_source_sha256": record.get("generated_source_sha256"),
                "optimized_artifact_sha256": record.get("optimized_artifact_sha256"),
                "binary": record.get("binary"),
                "binary_sha256": record.get("binary_sha256", record.get("optimized_artifact_sha256")),
                "binary_bytes": record.get("binary_bytes", record.get("optimized_artifact_bytes")),
            }
    return result


def _environment() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "controlled_build": dict(CONTROLLED_BUILD_ENVIRONMENT),
    }


def _failure_report(root: Path, *, status: str, code: str, message: str, lock: Mapping[str, Any] | None = None, lock_sha256: str | None = None) -> dict[str, Any]:
    protocol = {
        "seed": alpha.DEFAULT_SEED,
        "warmups": alpha.DEFAULT_WARMUPS,
        "measured_runs": alpha.DEFAULT_MEASURED_RUNS,
        "sample_replicates": alpha.DEFAULT_SAMPLE_REPLICATES,
        "bootstrap_replicates": alpha.BOOTSTRAP_REPLICATES,
        "sample_aggregation": "minimum",
        "schedule": "seeded_randomized_strictly_sequential",
        "mad_limit": alpha.MAD_LIMIT,
        "ratio_limits": dict(alpha.CLASS_RATIO_LIMITS),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "claim_id": CLAIM_ID,
        "status": status,
        "passed": False,
        "workload_lock": {"path": LOCK_PATH, "sha256": lock_sha256},
        "compiler_provenance": {"source_tree_sha256": None, "runner_sha256": _sha256_bytes(Path(__file__).read_bytes())},
        "toolchains": {"python": _version_record(sys.executable), "c": _version_record(None)},
        "protocol": protocol,
        "workloads": dict(lock) if lock is not None else {"digest": None, "items": []},
        "artifacts": {},
        "randomized_schedule": {"seed": alpha.DEFAULT_SEED, "rounds": []},
        "raw_samples": [],
        "gates": {},
        "material_gaps": [{"code": code, "message": message}],
        "environment": _environment(),
        "applications": [],
        "failure": {"code": code, "message": message},
    }


def canonical_report_bytes(report: Mapping[str, Any]) -> bytes:
    return _canonical(report) + b"\n"


def write_public_report(report: Mapping[str, Any], path: str | Path) -> None:
    """Atomically create a report, refusing to replace differing evidence."""
    validate_public_report(report)
    destination = Path(path)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = canonical_report_bytes(report)
        if destination.exists():
            try:
                if destination.read_bytes() == content:
                    return
            except OSError as exc:
                raise PublicBenchmarkOutputError(str(exc)) from exc
            raise PublicBenchmarkOutputError(f"refusing to overwrite differing report: {destination}")
        fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=str(destination.parent))
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.read_bytes() != content:
                    raise PublicBenchmarkOutputError(f"refusing to overwrite differing report: {destination}")
            finally:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        except OSError as exc:
            raise PublicBenchmarkOutputError(f"cannot write benchmark report: {exc}") from exc
    except PublicBenchmarkOutputError:
        raise
    except OSError as exc:
        raise PublicBenchmarkOutputError(f"cannot write benchmark report: {exc}") from exc


def run_public_benchmark(
    root: str | Path = ".",
    *,
    output: str | Path | None = None,
    runner_registry: Mapping[tuple[str, str], Any] | None = None,
    artifact_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the fixed alpha schedule and emit the public v1 report."""
    base = Path(root).resolve()
    lock: dict[str, Any] | None = None
    lock_sha256: str | None = None
    try:
        workloads, lock_info = load_authoritative_workload_lock(base)
        lock = lock_info
        lock_sha256 = str(lock_info["sha256"])
        try:
            builds: Mapping[str, Any] = {}
            registry = runner_registry
            if registry is None:
                registry, builds = alpha.build_alpha_runner_registry(base)
            metadata = artifact_metadata if artifact_metadata is not None else alpha._metadata_by_builds(workloads, builds)
            report = alpha.run_alpha_performance(
                base,
                workloads=workloads,
                runner_registry=registry,
                artifact_metadata=metadata,
                warmups=alpha.DEFAULT_WARMUPS,
                measured_runs=alpha.DEFAULT_MEASURED_RUNS,
                seed=alpha.DEFAULT_SEED,
            )
        except alpha.PerformanceEvidenceError as exc:
            report = _failure_report(base, status="INVALID", code="INVALID_OBSERVATION", message=str(exc), lock=lock_info, lock_sha256=lock_sha256)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            report = _failure_report(base, status="FAILED", code="RUNNER_FAILURE", message=f"{type(exc).__name__}: {exc}", lock=lock_info, lock_sha256=lock_sha256)
        except Exception as exc:
            report = _failure_report(base, status="FAILED", code="RUNNER_FAILURE", message=f"{type(exc).__name__}: {exc}", lock=lock_info, lock_sha256=lock_sha256)
        else:
            report = dict(report)
            report["schema_version"] = SCHEMA_VERSION
            report["claim_id"] = CLAIM_ID
            report["workload_lock"] = {"path": LOCK_PATH, "sha256": lock_sha256}
            report["compiler_provenance"] = _compiler_provenance(base, builds, runner_sha256=_sha256_bytes(Path(__file__).read_bytes()))
            report["toolchains"] = {
                "python": report["compiler_provenance"]["python"],
                "c": report["compiler_provenance"]["c"],
            }
            report["environment"] = dict(report.get("environment", {}))
            report["artifacts"] = _artifact_provenance(report.get("artifacts", {}))
            report["workloads"] = lock_info
            report["protocol"] = {**dict(report["protocol"]), "seed": report["protocol"].get("bootstrap_seed")}
            report["status"] = "MEASURED" if report.get("status") == "MEASURED" else "UNMEASURED"
        validate_public_report(report, root=base)
    except PublicBenchmarkError as exc:
        report = _failure_report(base, status="INVALID", code="INVALID_LOCK", message=str(exc), lock=lock, lock_sha256=lock_sha256)
        validate_public_report(report)
    if output is not None:
        write_public_report(report, output)
    return report


def _validate_failure(report: Mapping[str, Any]) -> None:
    status = report.get("status")
    if status not in {"FAILED", "INVALID", "UNMEASURED"} or report.get("passed") is not False:
        raise PublicBenchmarkError("invalid failure report status")
    failure = report.get("failure")
    if status in {"FAILED", "INVALID"} and (not isinstance(failure, Mapping) or not isinstance(failure.get("code"), str) or not isinstance(failure.get("message"), str)):
        raise PublicBenchmarkError("failure envelope missing code/message")


def validate_public_report(report: Mapping[str, Any], *, root: str | Path | None = None) -> None:
    if not isinstance(report, Mapping) or report.get("schema_version") != SCHEMA_VERSION or report.get("claim_id") != CLAIM_ID:
        raise PublicBenchmarkError("unsupported public benchmark schema")
    if report.get("status") in {"FAILED", "INVALID"} and "failure" in report:
        _validate_failure(report)
        return
    required = ("status", "passed", "workload_lock", "compiler_provenance", "toolchains", "protocol", "workloads", "artifacts", "randomized_schedule", "raw_samples", "gates", "material_gaps")
    if any(key not in report for key in required):
        raise PublicBenchmarkError("public benchmark report is incomplete")
    if report.get("status") not in {"MEASURED", "UNMEASURED"} or type(report.get("passed")) is not bool:
        raise PublicBenchmarkError("invalid public benchmark decision")
    lock = report["workload_lock"]
    if not isinstance(lock, Mapping) or lock.get("path") != LOCK_PATH or not _hex(lock.get("sha256")):
        raise PublicBenchmarkError("workload lock provenance missing")
    protocol = report["protocol"]
    expected = {
        "seed": alpha.DEFAULT_SEED,
        "warmups": alpha.DEFAULT_WARMUPS,
        "measured_runs": alpha.DEFAULT_MEASURED_RUNS,
        "sample_replicates": alpha.DEFAULT_SAMPLE_REPLICATES,
    }
    if not isinstance(protocol, Mapping) or any(protocol.get(key) != value for key, value in expected.items()):
        raise PublicBenchmarkError("public protocol mismatch")
    workloads = report["workloads"]
    if not isinstance(workloads, Mapping) or workloads.get("digest") is None or not isinstance(workloads.get("items"), list) or len(workloads["items"]) != 3:
        raise PublicBenchmarkError("public workload denominator is incomplete")
    try:
        parsed = tuple(alpha._workload_from_dict(item) for item in workloads["items"])
    except (TypeError, KeyError, ValueError) as exc:
        raise PublicBenchmarkError("invalid public workload records") from exc
    if alpha.workload_digest(parsed) != workloads.get("digest"):
        raise PublicBenchmarkError("public workload digest mismatch")
    algorithms = {"arms": list(alpha.ARMS), "required_arms": list(alpha.REQUIRED_ARMS), "optional_arms": list(alpha.OPTIONAL_ARMS)}
    if report.get("algorithms", algorithms) != algorithms and report.get("algorithms", {}).get("arms") != algorithms["arms"]:
        raise PublicBenchmarkError("public arm protocol mismatch")
    provenance = report["compiler_provenance"]
    if not isinstance(provenance, Mapping) or not _hex(provenance.get("source_tree_sha256")) or provenance.get("runner_sha256") != _sha256_bytes(Path(__file__).read_bytes()):
        raise PublicBenchmarkError("compiler provenance missing or runner source tampered")
    if root is not None and provenance.get("source_tree_sha256") != compiler_input_tree_sha256(root):
        raise PublicBenchmarkError("compiler input tree hash mismatch")
    toolchains = report["toolchains"]
    if not isinstance(toolchains, Mapping) or not isinstance(toolchains.get("python"), Mapping) or not isinstance(toolchains.get("c"), Mapping):
        raise PublicBenchmarkError("toolchain provenance missing")
    for key in ("python", "c"):
        if toolchains[key].get("binary_sha256") is not None and not _hex(toolchains[key].get("binary_sha256")):
            raise PublicBenchmarkError("toolchain executable hash malformed")
        if toolchains[key].get("version_sha256") is not None and not _hex(toolchains[key].get("version_sha256")):
            raise PublicBenchmarkError("toolchain version hash malformed")
        if root is not None and toolchains[key].get("path") and toolchains[key].get("binary_sha256"):
            if _executable_hash(toolchains[key]["path"]) != toolchains[key]["binary_sha256"]:
                raise PublicBenchmarkError("toolchain executable hash mismatch")
    if not isinstance(report["raw_samples"], list) or not isinstance(report["gates"], Mapping) or not isinstance(report["material_gaps"], list):
        raise PublicBenchmarkError("public observations are malformed")
    artifacts = report["artifacts"]
    if not isinstance(artifacts, Mapping):
        raise PublicBenchmarkError("public artifact provenance missing")
    for records in artifacts.values():
        if not isinstance(records, Mapping):
            raise PublicBenchmarkError("public artifact provenance malformed")
        for record in records.values():
            if not isinstance(record, Mapping):
                raise PublicBenchmarkError("public artifact record malformed")
            for key in ("source_sha256", "generated_source_sha256", "optimized_artifact_sha256", "binary_sha256"):
                value = record.get(key)
                if value is not None and not _hex(value):
                    raise PublicBenchmarkError("public artifact hash malformed")
            optimized = record.get("optimized_artifact_sha256")
            binary_hash = record.get("binary_sha256")
            if optimized is not None and binary_hash is not None and optimized != binary_hash:
                raise PublicBenchmarkError("optimized executable hash mismatch")
            if root is not None and record.get("binary") and binary_hash:
                if _executable_hash(record["binary"]) != binary_hash:
                    raise PublicBenchmarkError("compiled executable hash mismatch")
            if root is not None and record.get("generated_source") and record.get("generated_source_sha256"):
                try:
                    generated_hash = _sha256_bytes(_path(Path(root).resolve(), record["generated_source"]).read_bytes())
                except OSError as exc:
                    raise PublicBenchmarkError("generated C artifact is unavailable") from exc
                if generated_hash != record["generated_source_sha256"]:
                    raise PublicBenchmarkError("generated C hash mismatch")
    if report.get("status") == "MEASURED":
        # Delegate the detailed raw sample, summary, schedule, lock, and gate checks.
        alpha_report = dict(report)
        alpha_report["schema_version"] = alpha.SCHEMA_VERSION
        alpha_report["workloads"] = {
            "schema": alpha.WORKLOAD_SCHEMA_VERSION,
            "digest": workloads["digest"],
            "items": workloads["items"],
        }
        try:
            alpha.validate_alpha_performance_report(alpha_report)
        except alpha.PerformanceEvidenceError as exc:
            raise PublicBenchmarkError(
                f"invalid alpha performance evidence: {exc}"
            ) from exc
        if report.get("passed") and not all(report["gates"].values()):
            raise PublicBenchmarkError("public pass decision is not supported by gates")


__all__ = [
    "CLAIM_ID", "CONTROLLED_BUILD_ENVIRONMENT", "LOCK_PATH", "PublicBenchmarkError",
    "PublicBenchmarkOutputError", "SCHEMA_VERSION", "canonical_report_bytes",
    "compiler_input_tree_sha256", "load_authoritative_workload_lock", "run_public_benchmark",
    "validate_public_report", "write_public_report",
]
