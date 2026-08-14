"""Public native benchmark v1 wrapper and report validator.

The alpha-performance module owns the measurement schedule.  This module owns
only the public lock, provenance, report contract, and failure persistence.
"""
from __future__ import annotations

import contextlib
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
CONTROLLED_BUILD_ENVIRONMENT = {
    "LC_ALL": "C",
    "TZ": "UTC",
    "SOURCE_DATE_EPOCH": "0",
    "PATH": "/usr/bin:/bin",
}

@contextlib.contextmanager
def _controlled_environment() -> Any:
    previous = {key: os.environ.get(key) for key in CONTROLLED_BUILD_ENVIRONMENT}
    os.environ.update(CONTROLLED_BUILD_ENVIRONMENT)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

PUBLIC_CORPUS_POLICY = "all_three_checked_in_workloads_required; no_filters_or_fallback_sources"
PUBLIC_PROTOCOL = {
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
PUBLIC_LOCK_KEYS = frozenset({
    "schema", "claim_id", "arms", "required_arms", "optional_arms",
    "class_ratio_limits", "protocol", "corpus_policy", "workloads",
})

class PublicBenchmarkError(ValueError):
    """Raised when a public benchmark lock/report is invalid."""


class PublicBenchmarkOutputError(OSError):
    """Raised when a report cannot be created without overwriting evidence."""

def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


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
            env=dict(CONTROLLED_BUILD_ENVIRONMENT),
        )
        output = (completed.stdout or b"") + (completed.stderr or b"")
        record["version_sha256"] = _sha256_bytes(output)
        record["version"] = output.decode("utf-8", errors="replace").splitlines()[0] if output else None
        record["version_returncode"] = completed.returncode
    except (OSError, subprocess.SubprocessError):
        record["version_returncode"] = None
    return record

def _validate_toolchain_record(record: Mapping[str, Any]) -> None:
    path = record.get("path")
    binary_hash = record.get("binary_sha256")
    version_hash = record.get("version_sha256")
    if not isinstance(path, str) or not isinstance(binary_hash, str) or not _hex(binary_hash) or not isinstance(version_hash, str) or not _hex(version_hash):
        raise PublicBenchmarkError("complete toolchain identity is required")
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file():
        raise PublicBenchmarkError("toolchain executable path is invalid")
    if _executable_hash(candidate) != binary_hash:
        raise PublicBenchmarkError("toolchain executable hash mismatch")
    try:
        completed = subprocess.run((str(candidate), "--version"), capture_output=True, check=False, timeout=5, env=dict(CONTROLLED_BUILD_ENVIRONMENT))
    except (OSError, subprocess.SubprocessError) as exc:
        raise PublicBenchmarkError("toolchain version query failed") from exc
    output = (completed.stdout or b"") + (completed.stderr or b"")
    if _sha256_bytes(output) != version_hash:
        raise PublicBenchmarkError("toolchain version hash mismatch")

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
    base = Path(root).resolve()
    lock, payload, lock_sha256 = _read_lock(base)
    if frozenset(lock) != PUBLIC_LOCK_KEYS:
        raise PublicBenchmarkError("workload lock keys are not authoritative")
    if (
        lock.get("schema") != alpha.WORKLOAD_SCHEMA_VERSION
        or lock.get("claim_id") != CLAIM_ID
        or tuple(lock.get("arms", ())) != alpha.ARMS
        or tuple(lock.get("required_arms", ())) != alpha.REQUIRED_ARMS
        or tuple(lock.get("optional_arms", ())) != alpha.OPTIONAL_ARMS
        or lock.get("class_ratio_limits") != dict(alpha.CLASS_RATIO_LIMITS)
        or lock.get("protocol") != PUBLIC_PROTOCOL
        or lock.get("corpus_policy") != PUBLIC_CORPUS_POLICY
    ):
        raise PublicBenchmarkError("workload lock static protocol mismatch")
    items = lock.get("workloads")
    if not isinstance(items, list):
        raise PublicBenchmarkError("workload lock workloads must be a list")
    try:
        parsed = tuple(alpha._workload_from_dict(item) for item in items)
    except (TypeError, KeyError, ValueError) as exc:
        raise PublicBenchmarkError("invalid workload lock item") from exc
    if tuple(item.to_dict() for item in parsed) != tuple(item.to_dict() for item in alpha.FROZEN_WORKLOADS):
        raise PublicBenchmarkError("authoritative workload lock diverges from runner lock")
    if len(parsed) != 3 or len({item.workload_class for item in parsed}) != 3:
        raise PublicBenchmarkError("public v1 requires exactly three workload classes")
    return parsed, {
        "path": LOCK_PATH,
        "sha256": lock_sha256,
        "bytes": len(payload),
        "schema": lock["schema"],
        "claim_id": lock["claim_id"],
        "corpus_policy": lock["corpus_policy"],
        "protocol": dict(lock["protocol"]),
        "class_ratio_limits": dict(lock["class_ratio_limits"]),
        "digest": alpha.workload_digest(parsed),
        "items": [item.to_dict() for item in parsed],
    }

def _compiler_provenance(root: Path, builds: Mapping[str, Any], *, runner_sha256: str, source_tree_sha256: str | None = None) -> dict[str, Any]:
    python = _version_record(sys.executable)
    c_records = [record for key, record in builds.items() if key.endswith(":native") or key.endswith(":c")]
    selected: Mapping[str, Any] = next((record for record in c_records if isinstance(record, Mapping) and record.get("compiler")), {})
    compiler = selected.get("compiler") if isinstance(selected, Mapping) else None
    c = _version_record(compiler if isinstance(compiler, str) else shutil.which("cc") or shutil.which("gcc"))
    return {
        "source_tree_sha256": source_tree_sha256 or compiler_input_tree_sha256(root),
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
                "source": list(record.get("source", [])),
                "source_sha256": record.get("source_sha256"),
                "generated_source": record.get("generated_source"),
                "generated_source_sha256": record.get("generated_source_sha256"),
                "optimized_artifact_sha256": record.get("optimized_artifact_sha256"),
                "binary": record.get("binary"),
                "binary_sha256": record.get("binary_sha256", record.get("optimized_artifact_sha256")),
                "binary_bytes": record.get("binary_bytes", record.get("optimized_artifact_bytes")),
            }
    return result
def _materialize_artifacts(root: Path, artifacts: Mapping[str, Any]) -> dict[str, Any]:
    destination_root = root / ".merlo" / "public-benchmark-v1" / "artifacts"
    materialized = json.loads(json.dumps(artifacts))
    for workload_id, records in materialized.items():
        if not isinstance(records, Mapping):
            continue
        for arm, record in records.items():
            if not isinstance(record, dict):
                continue
            target_dir = destination_root / str(workload_id) / str(arm)
            for key in ("binary", "generated_source"):
                value = record.get(key)
                if not value:
                    continue
                source = Path(value)
                if not source.is_file():
                    continue
                try:
                    record[key] = source.resolve().relative_to(root).as_posix()
                    continue
                except ValueError:
                    pass
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / source.name
                shutil.copy2(source, target)
                record[key] = target.relative_to(root).as_posix()
            source_paths = record.get("source")
            if isinstance(source_paths, list):
                record["source"] = [
                    Path(value).resolve().relative_to(root).as_posix()
                    if Path(value).is_absolute() and Path(value).resolve().is_relative_to(root)
                    else str(value)
                    for value in source_paths
                ]
    return materialized

def _checked_artifact_path(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PublicBenchmarkError("artifact path is invalid")
    candidate = Path(value)
    if candidate.is_absolute() or any(part in {".", ".."} for part in candidate.parts):
        raise PublicBenchmarkError("artifact path must be normalized relative")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PublicBenchmarkError("artifact path escapes root") from exc
    if candidate.is_symlink() or resolved.is_symlink() or not resolved.is_file():
        raise PublicBenchmarkError("artifact path is missing or symlinked")
    return resolved


def _environment() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "uname": {"system": platform.system(), "machine": platform.machine()},
        "controlled_build": dict(CONTROLLED_BUILD_ENVIRONMENT),
    }

def _failure_report(root: Path, *, status: str, code: str, message: str, lock: Mapping[str, Any] | None = None, lock_sha256: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "claim_id": CLAIM_ID,
        "status": status,
        "passed": False,
        "workload_lock": {"path": LOCK_PATH, "sha256": lock_sha256},
        "compiler_provenance": {"source_tree_sha256": None, "runner_sha256": _sha256_bytes(Path(__file__).read_bytes())},
        "toolchains": {"python": _version_record(sys.executable), "c": _version_record(None)},
        "protocol": {**PUBLIC_PROTOCOL, "bootstrap_seed": alpha.DEFAULT_SEED, "timer": "time.perf_counter_ns"},
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
        source_tree_before = compiler_input_tree_sha256(base)
        try:
            builds: Mapping[str, Any] = {}
            registry = runner_registry
            with _controlled_environment():
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
            if compiler_input_tree_sha256(base) != source_tree_before:
                raise PublicBenchmarkError("compiler inputs changed during benchmark")
            report = dict(report)
            report["schema_version"] = SCHEMA_VERSION
            report["claim_id"] = CLAIM_ID
            report["workload_lock"] = {"path": LOCK_PATH, "sha256": lock_sha256}
            report["compiler_provenance"] = _compiler_provenance(base, builds, runner_sha256=_sha256_bytes(Path(__file__).read_bytes()), source_tree_sha256=source_tree_before)
            report["toolchains"] = {
                "python": report["compiler_provenance"]["python"],
                "c": report["compiler_provenance"]["c"],
            }
            report["environment"] = dict(report.get("environment", {}))
            report["artifacts"] = _materialize_artifacts(base, _artifact_provenance(report.get("artifacts", {})))
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
    environment = report.get("environment")
    if not isinstance(environment, Mapping):
        raise PublicBenchmarkError("failure environment missing")
    uname = environment.get("uname")
    system = uname.get("system") if isinstance(uname, Mapping) else environment.get("system")
    machine = uname.get("machine") if isinstance(uname, Mapping) else environment.get("machine")
    if "raw_samples" in report and report.get("raw_samples") != []:
        raise PublicBenchmarkError("non-measured report cannot retain timing samples")
    if system != "Linux" or str(machine).lower() not in {"x86_64", "amd64"}:
        raise PublicBenchmarkError("public benchmark requires Linux x86-64")


def validate_public_report(report: Mapping[str, Any], *, root: str | Path | None = None) -> None:
    if not isinstance(report, Mapping) or report.get("schema_version") != SCHEMA_VERSION or report.get("claim_id") != CLAIM_ID:
        raise PublicBenchmarkError("unsupported public benchmark schema")
    status = report.get("status")
    passed = report.get("passed")
    if status not in {"MEASURED", "UNMEASURED", "FAILED", "INVALID"} or type(passed) is not bool:
        raise PublicBenchmarkError("invalid public benchmark decision")
    if passed and status != "MEASURED":
        raise PublicBenchmarkError("non-measured report cannot pass")
    if status in {"FAILED", "INVALID"}:
        _validate_failure(report)
        return
    if status == "UNMEASURED" and "failure" in report:
        _validate_failure(report)
    required = ("workload_lock", "compiler_provenance", "toolchains", "protocol", "workloads", "artifacts", "randomized_schedule", "raw_samples", "gates", "material_gaps")
    if any(key not in report for key in required):
        raise PublicBenchmarkError("public benchmark report is incomplete")
    lock = report["workload_lock"]
    if not isinstance(lock, Mapping) or lock.get("path") != LOCK_PATH or not _hex(lock.get("sha256")):
        raise PublicBenchmarkError("workload lock provenance missing")
    protocol = report["protocol"]
    expected_report_protocol = {**PUBLIC_PROTOCOL, "bootstrap_seed": alpha.DEFAULT_SEED, "timer": "time.perf_counter_ns"}
    if not isinstance(protocol, Mapping) or dict(protocol) != expected_report_protocol:
        raise PublicBenchmarkError("public protocol mismatch")
    workloads = report["workloads"]
    expected_items = [item.to_dict() for item in alpha.FROZEN_WORKLOADS]
    if (
        not isinstance(workloads, Mapping)
        or workloads.get("schema") != alpha.WORKLOAD_SCHEMA_VERSION
        or workloads.get("claim_id") != CLAIM_ID
        or workloads.get("corpus_policy") != PUBLIC_CORPUS_POLICY
        or workloads.get("class_ratio_limits") != dict(alpha.CLASS_RATIO_LIMITS)
        or workloads.get("protocol") != PUBLIC_PROTOCOL
        or workloads.get("digest") != alpha.workload_digest(alpha.FROZEN_WORKLOADS)
        or workloads.get("items") != expected_items
    ):
        raise PublicBenchmarkError("public workload corpus/protocol mismatch")
    environment = report.get("environment")
    if not isinstance(environment, Mapping):
        raise PublicBenchmarkError("benchmark environment missing")
    uname = environment.get("uname")
    system = uname.get("system") if isinstance(uname, Mapping) else environment.get("system")
    machine = uname.get("machine") if isinstance(uname, Mapping) else environment.get("machine")
    if system != "Linux" or str(machine).lower() not in {"x86_64", "amd64"}:
        raise PublicBenchmarkError("public benchmark requires Linux x86-64")
    affinity = report.get("affinity")
    if status == "MEASURED" and (not isinstance(affinity, Mapping) or affinity.get("applied") is not True):
        raise PublicBenchmarkError("CPU affinity was not successfully applied")
    if root is not None:
        _, authoritative = load_authoritative_workload_lock(root)
        if lock.get("sha256") != authoritative["sha256"]:
            raise PublicBenchmarkError("workload lock hash mismatch")
    algorithms = {"arms": list(alpha.ARMS), "required_arms": list(alpha.REQUIRED_ARMS), "optional_arms": list(alpha.OPTIONAL_ARMS)}
    if report.get("algorithms", algorithms) != algorithms and report.get("algorithms", {}).get("arms") != algorithms["arms"]:
        raise PublicBenchmarkError("public arm protocol mismatch")
    provenance = report["compiler_provenance"]
    if not isinstance(provenance, Mapping) or not _hex(provenance.get("source_tree_sha256")) or provenance.get("runner_sha256") != _sha256_bytes(Path(__file__).read_bytes()):
        raise PublicBenchmarkError("compiler provenance missing or runner source tampered")
    toolchains = report.get("toolchains")
    if not isinstance(toolchains, Mapping) or not isinstance(toolchains.get("python"), Mapping) or not isinstance(toolchains.get("c"), Mapping):
        raise PublicBenchmarkError("toolchain provenance missing")
    if status == "MEASURED":
        _validate_toolchain_record(toolchains["python"])
        _validate_toolchain_record(toolchains["c"])
    artifacts = report["artifacts"]
    if not isinstance(artifacts, Mapping) or set(artifacts) != {item.id for item in alpha.FROZEN_WORKLOADS}:
        raise PublicBenchmarkError("public artifact provenance is incomplete")
    artifact_root = Path(root or ".").resolve()
    measured_arms = {
        app.get("id"): {arm for arm, value in app.get("arms", {}).items() if isinstance(value, Mapping) and value.get("status") == "MEASURED"}
        for app in report.get("applications", [])
        if isinstance(app, Mapping)
    }
    schedule = report.get("randomized_schedule")
    available = {
        item.id: [arm for arm in alpha.ARMS if arm in measured_arms.get(item.id, set())]
        for item in alpha.FROZEN_WORKLOADS
    }
    if not isinstance(schedule, Mapping) or schedule.get("seed") != alpha.DEFAULT_SEED:
        raise PublicBenchmarkError("randomized schedule seed mismatch")
    if schedule.get("rounds") != alpha._schedule(alpha.FROZEN_WORKLOADS, available, seed=alpha.DEFAULT_SEED, warmups=alpha.DEFAULT_WARMUPS, measured_runs=alpha.DEFAULT_MEASURED_RUNS):
        raise PublicBenchmarkError("randomized schedule is not authoritative")
    for workload in alpha.FROZEN_WORKLOADS:
        records = artifacts.get(workload.id)
        if not isinstance(records, Mapping) or set(records) != set(alpha.ARMS):
            raise PublicBenchmarkError("public artifact arm denominator is incomplete")
        for arm in alpha.ARMS:
            record = records[arm]
            if not isinstance(record, Mapping) or record.get("source_sha256") != workload.source_sha256[arm]:
                raise PublicBenchmarkError("public artifact source lock mismatch")
            optimized = record.get("optimized_artifact_sha256")
            if arm not in measured_arms.get(workload.id, set()):
                continue
            binary_hash = record.get("binary_sha256")
            if optimized is not None and binary_hash is not None and optimized != binary_hash:
                raise PublicBenchmarkError("optimized executable hash mismatch")
            for key in ("generated_source_sha256", "binary_sha256"):
                value = record.get(key)
                if value is not None and not _hex(value):
                    raise PublicBenchmarkError("public artifact hash malformed")
            source_paths = record.get("source")
            if not isinstance(source_paths, list) or not source_paths or any(not isinstance(item, str) for item in source_paths):
                raise PublicBenchmarkError("public source artifact path missing")
            source_digest = hashlib.sha256()
            for source_path in source_paths:
                checked = _checked_artifact_path(artifact_root, source_path)
                source_digest.update(checked.read_bytes())
                source_digest.update(b"\0")
            if source_digest.hexdigest() != record.get("source_sha256"):
                raise PublicBenchmarkError("public source artifact hash mismatch")
            if report.get("status") == "MEASURED" and arm != "python":
                binary_path = _checked_artifact_path(artifact_root, record.get("binary"))
                generated_path = _checked_artifact_path(artifact_root, record.get("generated_source"))
                if _sha256_bytes(binary_path.read_bytes()) != record.get("binary_sha256"):
                    raise PublicBenchmarkError("compiled executable hash mismatch")
                if _sha256_bytes(generated_path.read_bytes()) != record.get("generated_source_sha256"):
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
        for app in report.get("applications", []):
            if not isinstance(app, Mapping):
                raise PublicBenchmarkError("application evidence malformed")
            arms = app.get("arms", {})
            c_median = arms.get("c", {}).get("median") if isinstance(arms.get("c"), Mapping) else None
            merlo = [arms.get(name, {}).get("median") for name in ("merlo_concise", "merlo_canonical") if isinstance(arms.get(name), Mapping)]
            if not isinstance(c_median, (int, float)) or c_median <= 0 or any(not isinstance(value, (int, float)) or value <= 0 for value in merlo):
                raise PublicBenchmarkError("C baseline is required")
            limit = alpha.CLASS_RATIO_LIMITS[app["class"]]
            if max(merlo) / c_median > limit:
                raise PublicBenchmarkError("C baseline ratio gate failed")


__all__ = [
    "CLAIM_ID", "CONTROLLED_BUILD_ENVIRONMENT", "LOCK_PATH", "PublicBenchmarkError",
    "PublicBenchmarkOutputError", "SCHEMA_VERSION", "canonical_report_bytes",
    "compiler_input_tree_sha256", "load_authoritative_workload_lock", "run_public_benchmark",
    "validate_public_report", "write_public_report",
]
