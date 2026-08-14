"""Sequential Merlo alpha performance evidence protocol.

The study is deliberately boring: frozen inputs are checked before any runner is
called, one process is timed at a time, and every observation is retained in the
JSON report.  A missing optional Rust toolchain is visible rather than replaced
with a Python implementation.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .productive_performance import ArmMeasurement, FROZEN_WORKLOADS as PRODUCTIVE_WORKLOADS
from .productive_performance import build_productive_runner_registry

SCHEMA_VERSION = "merlo.alpha-performance.report.v2"
WORKLOAD_SCHEMA_VERSION = "merlo.alpha-performance.workloads.v2"
DEFAULT_WARMUPS = 5
DEFAULT_MEASURED_RUNS = 30
DEFAULT_SAMPLE_REPLICATES = 31
BOOTSTRAP_REPLICATES = 2_000
DEFAULT_SEED = 2_026_081_3
MAD_LIMIT = 0.05
ARMS = ("merlo_concise", "merlo_canonical", "c", "python", "rust")
REQUIRED_ARMS = ("merlo_concise", "merlo_canonical", "c", "python")
OPTIONAL_ARMS = ("rust",)
WORKLOAD_CLASSES = ("numeric_array", "text_bytes_collections", "cli_data")
CLASS_RATIO_LIMITS = {
    "numeric_array": 1.15,
    "text_bytes_collections": 1.25,
    "cli_data": 1.50,
}


class PerformanceEvidenceError(ValueError):
    """Raised when frozen or observed evidence is incomplete or tampered."""


@dataclass(frozen=True)
class FrozenWorkload:
    id: str
    workload_class: str
    fixture: str
    expected_bytes: int
    input_sha256: str
    expected_checksum: str
    algorithm: str
    source_sha256: Mapping[str, str]
    source_application: str

    @property
    def class_name(self) -> str:
        return self.workload_class

    @property
    def expected_sha256(self) -> str:
        return self.input_sha256

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "class": self.workload_class,
            "fixture": self.fixture,
            "expected_bytes": self.expected_bytes,
            "input_sha256": self.input_sha256,
            "expected_checksum": self.expected_checksum,
            "algorithm": self.algorithm,
            "source_sha256": dict(self.source_sha256),
            "source_application": self.source_application,
        }


@dataclass(frozen=True)
class Measurement:
    elapsed_ns: float
    checksum: str
    startup_ns: float | None = None
    peak_rss_kb: int | None = None
    metadata: Mapping[str, Any] | None = None


# Friendly aliases used by small runner fixtures.
Sample = Measurement
Runner = Callable[[FrozenWorkload, bytes], Measurement]


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: object) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = _canonical(value)
    return hashlib.sha256(payload).hexdigest()


# These records mirror the checked-in workload lock.  The input hashes and
# source hashes are copied from already frozen productive evidence; they are not
# computed from files after timing.
FROZEN_WORKLOADS: tuple[FrozenWorkload, ...] = (
    FrozenWorkload(
        "numeric_array_sum", "numeric_array",
        "benchmarks/productive_performance/csv/workload.csv", 182104,
        "d2f8f12ac2f5c59ab613b716b958d63e31ae87ae9f7458756c7e8d711c2a7599",
        "d070c570a17d06e284948b409662c5270a407540db55f55108343fcc18083e8b",
        "parse numeric columns and sum each row into one deterministic digest",
        {
            "merlo_concise": "c180e2e8f56862a4e2e7516fb91f15fc70416779eff310111a9b8f3a2db00b00",
            "merlo_canonical": "1b2b7bb242b263158f05a5c82b8f43320dec3767d7aa19bcfbc4456d551eeff7",
            "c": "2c3fd15da202b4d9856388947bafbdce7eaf1198df3c6e448cab5d27d469c683",
            "python": "3c8e982d2c0ef05f5af4217727048bebb3b554db1ebf1ca9687fb995af9f5290",
            "rust": "d2f8f12ac2f5c59ab613b716b958d63e31ae87ae9f7458756c7e8d711c2a7599",
        },
        "csv",
    ),
    FrozenWorkload(
        "text_bytes_collections", "text_bytes_collections",
        "benchmarks/productive_performance/grep/workload.txt", 286389,
        "7293083cbfcfe616ea3750158c02e717d85008eee9ce30a2010925fdad631a31",
        "96cd04e7dbc91d288171226cf0b33876a07e55ed291d6b6961db17b80cb43ee5",
        "scan UTF-8 Text and Bytes while aggregating collection counts",
        {
            "merlo_concise": "f65ae25a24372f307b133eec0d03908696b022c994471068aabe520e79ec5b56",
            "merlo_canonical": "377aaccc72c25f343c3ee3d600b4acc69f605f915806b3239967bbd9c793a4ea",
            "c": "7cb11f5803c6834c8dff223316aee86c419b98a405cbcb82eb2d5cd7e8d89ea2",
            "python": "3c8e982d2c0ef05f5af4217727048bebb3b554db1ebf1ca9687fb995af9f5290",
            "rust": "7293083cbfcfe616ea3750158c02e717d85008eee9ce30a2010925fdad631a31",
        },
        "grep",
    ),
    FrozenWorkload(
        "cli_ndjson_report", "cli_data",
        "benchmarks/productive_performance/ndjson/workload.ndjson", 548337,
        "ca9aca6d54d49e5f9203b985c763ae0fc91600027cd56fa043b7474e81c23bb1",
        "eca3053bda42dfc900af171698e6b6d9b9f4da0815b91d3b41401d0cf7bc4d12",
        "real CLI NDJSON report with one process and identical arguments",
        {
            "merlo_concise": "8a274344bc7e41d2f1f661c86d45af5f0dd6a147b5ef7cd542ba10e69effcff6",
            "merlo_canonical": "d5566bd26707b6fd5890ed0e0ae0ecc8ee3b498107c80474ac12768f198b3162",
            "c": "c5d86160aac3f27c85e5d3a8c8ff228ea96f5a76d9145aa3168fe38e9ad0d220",
            "python": "3c8e982d2c0ef05f5af4217727048bebb3b554db1ebf1ca9687fb995af9f5290",
            "rust": "ca9aca6d54d49e5f9203b985c763ae0fc91600027cd56fa043b7474e81c23bb1",
        },
        "ndjson",
    ),
)


def workload_digest(workloads: Sequence[FrozenWorkload] = FROZEN_WORKLOADS) -> str:
    return _sha256([item.to_dict() for item in workloads])


WORKLOAD_DIGEST = workload_digest()


def algorithm_digest() -> str:
    return _sha256({"arms": ARMS, "required": REQUIRED_ARMS, "optional": OPTIONAL_ARMS})


ALGORITHM_DIGEST = algorithm_digest()


def median_absolute_deviation(samples: Sequence[float]) -> float | None:
    if not samples:
        return None
    center = statistics.median(samples)
    return statistics.median(abs(float(item) - center) for item in samples)


def bootstrap_median_ci(
    samples: Sequence[float], *, seed: int = DEFAULT_SEED,
    replicates: int = BOOTSTRAP_REPLICATES,
) -> tuple[float, float] | None:
    if not samples:
        return None
    if replicates < 1:
        raise ValueError("bootstrap replicates must be positive")
    values = tuple(float(item) for item in samples)
    rng = random.Random(seed)
    medians = [statistics.median(rng.choices(values, k=len(values))) for _ in range(replicates)]
    medians.sort()
    return (medians[int(0.025 * (replicates - 1))], medians[int(0.975 * (replicates - 1))])


def summarize_samples(
    samples: Sequence[float], *, seed: int = DEFAULT_SEED,
    replicates: int = BOOTSTRAP_REPLICATES,
) -> dict[str, Any]:
    values = [float(item) for item in samples]
    median = statistics.median(values) if values else None
    mad = median_absolute_deviation(values)
    return {
        "samples": values,
        "sample_count": len(values),
        "median": median,
        "mad": mad,
        "relative_mad": mad / median if mad is not None and median else None,
        "bootstrap_median_95_ci": bootstrap_median_ci(values, seed=seed, replicates=replicates),
    }


def freeze_workloads(
    root: str | Path = ".", workloads: Sequence[FrozenWorkload] = FROZEN_WORKLOADS,
) -> list[dict[str, Any]]:
    """Verify every input lock before a runner can be timed."""
    base = Path(root).resolve()
    seen: set[str] = set()
    frozen: list[dict[str, Any]] = []
    for workload in workloads:
        if workload.id in seen:
            raise PerformanceEvidenceError(f"duplicate workload: {workload.id}")
        seen.add(workload.id)
        if workload.workload_class not in WORKLOAD_CLASSES:
            raise PerformanceEvidenceError(f"unsupported workload class: {workload.workload_class}")
        path = base / workload.fixture
        if not path.is_file():
            raise PerformanceEvidenceError(f"missing frozen fixture: {workload.fixture}")
        payload = path.read_bytes()
        observed = hashlib.sha256(payload).hexdigest()
        if len(payload) != workload.expected_bytes or observed != workload.input_sha256:
            raise PerformanceEvidenceError(f"frozen fixture mismatch: {workload.id}")
        frozen.append({
            "id": workload.id,
            "fixture": workload.fixture,
            "bytes": len(payload),
            "input_sha256": observed,
            "expected_sha256": workload.input_sha256,
            "expected_checksum": workload.expected_checksum,
            "class": workload.workload_class,
        })
    return frozen


def _environment_snapshot() -> dict[str, Any]:
    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
    load = os.getloadavg() if hasattr(os, "getloadavg") else None
    return {
        "argv": [Path(sys.argv[0]).name, *sys.argv[1:]],
        "python": sys.version,
        "python_executable": Path(sys.executable).name,
        "platform": platform.platform(),
        "uname": dict(
            zip(
                ("system", "node", "release", "version", "machine", "processor"),
                platform.uname(),
            )
        ),
        "cpu_count": os.cpu_count(),
        "load_average": list(load) if load is not None else None,
        "affinity_before": affinity,
        "monotonic_clock": time.get_clock_info("perf_counter").implementation,
    }


def _toolchain_metadata() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("gcc", "clang", "rustc", "python"):
        executable = sys.executable if name == "python" else shutil.which(name)
        if executable is None:
            result[name] = {"available": False, "path": None, "version": None}
            continue
        version = None
        try:
            completed = subprocess.run((executable, "--version"), capture_output=True, text=True, check=False, timeout=5)
            version = (completed.stdout or completed.stderr).splitlines()[0] if completed.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            version = None
        result[name] = {"available": True, "path": executable, "version": version}
    return result


def _pin_affinity() -> tuple[dict[str, Any], set[int] | None]:
    if not hasattr(os, "sched_getaffinity") or not hasattr(os, "sched_setaffinity"):
        return {"requested_cpu": None, "available_cpus": [], "applied": False, "restored": False}, None
    original = set(os.sched_getaffinity(0))
    available = sorted(original)
    if not available:
        return {"requested_cpu": None, "available_cpus": [], "applied": False, "restored": False}, original
    cpu = available[0]
    try:
        os.sched_setaffinity(0, {cpu})
    except OSError:
        return {"requested_cpu": cpu, "available_cpus": available, "applied": False, "restored": False}, original
    return {"requested_cpu": cpu, "available_cpus": available, "applied": True, "restored": False}, original


def _restore_affinity(record: dict[str, Any], original: set[int] | None) -> None:
    if original is not None and record.get("applied") and hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, original)
        record["restored"] = True


def _schedule(workloads: Sequence[FrozenWorkload], available: Mapping[str, Sequence[str]], *, seed: int, warmups: int, measured_runs: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    rounds: list[dict[str, Any]] = []
    for workload in workloads:
        arms = list(available[workload.id])
        for phase, count in (("warmup", warmups), ("measured", measured_runs)):
            for iteration in range(count):
                order = list(arms)
                rng.shuffle(order)
                rounds.append({"workload_id": workload.id, "phase": phase, "iteration": iteration, "arms": order})
    return rounds


def _coerce_measurement(value: object) -> Measurement:
    if isinstance(value, Measurement):
        return value
    if isinstance(value, ArmMeasurement):
        return Measurement(value.elapsed_ns, value.result_digest)
    if isinstance(value, Mapping):
        elapsed = value.get("elapsed_ns", value.get("elapsed", value.get("wall_ns")))
        checksum = value.get("checksum", value.get("result_checksum", value.get("result_digest")))
        if elapsed is None or checksum is None:
            raise PerformanceEvidenceError("runner measurement lacks elapsed_ns/checksum")
        rss = value.get("peak_rss_kb")
        return Measurement(float(elapsed), str(checksum), None if value.get("startup_ns") is None else float(value["startup_ns"]), None if rss is None else int(rss), value.get("metadata") if isinstance(value.get("metadata"), Mapping) else None)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        raise PerformanceEvidenceError("runner measurement needs an expected checksum")
    raise PerformanceEvidenceError("unsupported runner measurement")


def _artifact_identity(metadata: Mapping[str, Any], workload_id: str) -> tuple[bool, str | None]:
    arms = metadata.get(workload_id, {})
    if not isinstance(arms, Mapping):
        return False, "artifact metadata missing workload"
    concise = arms.get("merlo_concise")
    canonical = arms.get("merlo_canonical")
    if not isinstance(concise, Mapping) or not isinstance(canonical, Mapping):
        return False, "optimized concise/canonical artifacts missing"
    left = concise.get("optimized_artifact_sha256")
    right = canonical.get("optimized_artifact_sha256")
    if not isinstance(left, str) or not isinstance(right, str) or len(left) != 64 or len(right) != 64:
        return False, "optimized artifact digest missing"
    if left != right:
        return False, "optimized concise/canonical artifacts differ"
    left_bytes = concise.get("optimized_artifact_bytes")
    right_bytes = canonical.get("optimized_artifact_bytes")
    if left_bytes is not None and right_bytes is not None and left_bytes != right_bytes:
        return False, "optimized concise/canonical artifact sizes differ"
    return True, None


def _ratio_gate(workload_class: str, arms: Mapping[str, Mapping[str, Any]]) -> tuple[bool, str | None]:
    limit = CLASS_RATIO_LIMITS[workload_class]
    merlo_values = [arms[arm].get("median") for arm in ("merlo_concise", "merlo_canonical") if arms.get(arm, {}).get("status") == "MEASURED"]
    baselines = [arms[arm].get("median") for arm in ("c", "rust") if arms.get(arm, {}).get("status") == "MEASURED"]
    if not merlo_values or not baselines or any(not isinstance(value, (int, float)) or value <= 0 for value in (*merlo_values, *baselines)):
        return False, "no comparable native baseline"
    baseline = min(float(value) for value in baselines)
    ratio = max(float(value) for value in merlo_values) / baseline
    if ratio <= limit:
        return True, None
    return False, f"{workload_class} Merlo/native ratio {ratio:.3f} exceeds frozen {limit:.2f}x limit"


def _summary(values: Sequence[float], *, seed: int, replicates: int) -> dict[str, Any]:
    return summarize_samples(values, seed=seed, replicates=replicates)


def run_alpha_performance(
    root: str | Path = ".",
    *,
    workloads: Sequence[FrozenWorkload] = FROZEN_WORKLOADS,
    runner_registry: Mapping[tuple[str, str], Runner] | None = None,
    artifact_metadata: Mapping[str, Any] | None = None,
    warmups: int = DEFAULT_WARMUPS,
    measured_runs: int = DEFAULT_MEASURED_RUNS,
    seed: int = DEFAULT_SEED,
    raw_report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run all available arms sequentially and return a validated raw report."""
    if warmups < 0 or measured_runs < 1:
        raise ValueError("warmups must be non-negative and measured_runs must be positive")
    selected = tuple(workloads)
    frozen = freeze_workloads(root, selected)
    base = Path(root).resolve()
    registry = runner_registry
    builds: Mapping[str, Any] = {}
    if registry is None:
        registry, builds = build_alpha_runner_registry(base)
    metadata = artifact_metadata if artifact_metadata is not None else _metadata_by_builds(selected, builds)
    environment = _environment_snapshot()
    affinity, original = _pin_affinity()
    try:
        available: dict[str, list[str]] = {}
        for workload in selected:
            names = [arm for arm in REQUIRED_ARMS if callable(registry.get((workload.id, arm)))]
            if callable(registry.get((workload.id, "rust"))):
                names.append("rust")
            available[workload.id] = names
        rounds = _schedule(selected, available, seed=seed, warmups=warmups, measured_runs=measured_runs)
        apps: list[dict[str, Any]] = []
        raw_samples: list[dict[str, Any]] = []
        for workload, frozen_item in zip(selected, frozen):
            payload = (base / workload.fixture).read_bytes()
            arm_records: dict[str, dict[str, Any]] = {}
            for arm in ARMS:
                if arm not in available[workload.id]:
                    reason = "OPTIONAL_TOOLCHAIN_UNAVAILABLE" if arm in OPTIONAL_ARMS else "REQUIRED_RUNNER_UNAVAILABLE"
                    arm_records[arm] = {"status": "UNAVAILABLE", "reason": reason, **summarize_samples((), seed=seed, replicates=BOOTSTRAP_REPLICATES), "startup_samples_ns": [], "peak_rss_kb_samples": [], "raw_sample_count": 0}
                else:
                    arm_records[arm] = {"status": "MEASURED", "reason": None, **summarize_samples((), seed=seed, replicates=BOOTSTRAP_REPLICATES), "startup_samples_ns": [], "peak_rss_kb_samples": [], "raw_sample_count": 0}
            measured_values = {arm: [] for arm in available[workload.id]}
            startup_values = {arm: [] for arm in available[workload.id]}
            rss_values = {arm: [] for arm in available[workload.id]}
            for round_info in rounds:
                if round_info["workload_id"] != workload.id:
                    continue
                phase = round_info["phase"]
                for arm in round_info["arms"]:
                    runner = registry[(workload.id, arm)]
                    replicate_count = (
                        DEFAULT_SAMPLE_REPLICATES if phase == "measured" else 1
                    )
                    observations = [
                        _coerce_measurement(runner(workload, payload))
                        for _ in range(replicate_count)
                    ]
                    for current in observations:
                        if (
                            not math.isfinite(float(current.elapsed_ns))
                            or current.elapsed_ns < 0
                        ):
                            raise PerformanceEvidenceError(
                                f"invalid elapsed time for {workload.id}/{arm}"
                            )
                        if current.checksum != workload.expected_checksum:
                            raise PerformanceEvidenceError(
                                f"checksum mismatch for {workload.id}/{arm}"
                            )
                        if current.startup_ns is not None and (
                            not math.isfinite(current.startup_ns)
                            or current.startup_ns < 0
                        ):
                            raise PerformanceEvidenceError(
                                f"invalid startup time for {workload.id}/{arm}"
                            )
                        if (
                            current.peak_rss_kb is not None
                            and current.peak_rss_kb < 0
                        ):
                            raise PerformanceEvidenceError(
                                f"invalid RSS for {workload.id}/{arm}"
                            )
                    observation = min(
                        observations, key=lambda item: float(item.elapsed_ns)
                    )
                    replicate_elapsed = [
                        float(item.elapsed_ns) for item in observations
                    ]
                    sample = {
                        "workload_id": workload.id,
                        "arm": arm,
                        "phase": phase,
                        "iteration": round_info["iteration"],
                        "elapsed_ns": float(observation.elapsed_ns),
                        "replicate_elapsed_ns": replicate_elapsed,
                        "startup_ns": observation.startup_ns,
                        "peak_rss_kb": observation.peak_rss_kb,
                        "checksum": observation.checksum,
                        "environment": environment,
                    }
                    if observation.metadata is not None:
                        sample["metadata"] = dict(observation.metadata)
                    raw_samples.append(sample)
                    arm_records[arm]["raw_sample_count"] += 1
                    if phase == "measured":
                        measured_values[arm].append(float(observation.elapsed_ns))
                        if observation.startup_ns is not None:
                            startup_values[arm].append(float(observation.startup_ns))
                        if observation.peak_rss_kb is not None:
                            rss_values[arm].append(int(observation.peak_rss_kb))
            for arm in available[workload.id]:
                arm_records[arm].update(_summary(measured_values[arm], seed=seed, replicates=BOOTSTRAP_REPLICATES))
                arm_records[arm]["startup_samples_ns"] = startup_values[arm]
                arm_records[arm]["peak_rss_kb_samples"] = rss_values[arm]
                arm_records[arm]["startup"] = _summary(startup_values[arm], seed=seed ^ 0x5354415254, replicates=BOOTSTRAP_REPLICATES)
                arm_records[arm]["peak_rss_kb"] = _summary(rss_values[arm], seed=seed ^ 0x525353, replicates=BOOTSTRAP_REPLICATES)
            apps.append({"id": workload.id, "class": workload.workload_class, "workload": frozen_item, "arms": arm_records})
        gaps: list[dict[str, Any]] = []
        gates: dict[str, bool] = {}
        for app in apps:
            ratio_ok, reason = _ratio_gate(app["class"], app["arms"])
            key = f"{app['id']}_within_{app['class']}_ratio"
            gates[key] = ratio_ok
            if reason:
                gaps.append({"workload_id": app["id"], "class": app["class"], "reason": reason})
        for workload_class in WORKLOAD_CLASSES:
            class_apps = [app for app in apps if app["class"] == workload_class]
            gates[f"{workload_class}_ratio_within_limit"] = bool(class_apps) and all(
                gates[f"{app['id']}_within_{workload_class}_ratio"] for app in class_apps
            )
        gates["all_required_arms_measured"] = all(
            app["arms"][arm]["status"] == "MEASURED" and app["arms"][arm]["sample_count"] == measured_runs
            for app in apps for arm in REQUIRED_ARMS
        )
        gates["all_measured_mad_at_most_0_05"] = all(
            app["arms"][arm]["relative_mad"] is not None and app["arms"][arm]["relative_mad"] <= MAD_LIMIT
            for app in apps for arm in available[app["id"]]
        ) and bool(raw_samples)
        identity: dict[str, bool] = {}
        for app in apps:
            ok, reason = _artifact_identity(metadata, app["id"])
            identity[app["id"]] = ok
            if reason:
                gaps.append({"workload_id": app["id"], "class": app["class"], "reason": reason})
        gates["concise_canonical_optimized_artifacts_identical"] = bool(identity) and all(identity.values())
        gates["frozen_workloads"] = len(frozen) == len(selected) and all(
            item["input_sha256"] == item["expected_sha256"]
            and item["bytes"] == next(workload.expected_bytes for workload in selected if workload.id == item["id"])
            for item in frozen
        )
        gates["sequential_schedule"] = True
        gates["raw_samples_complete"] = all(
            app["arms"][arm]["raw_sample_count"] == warmups + measured_runs
            for app in apps for arm in available[app["id"]]
        )
        gates["rust_optional"] = all("rust" in available[app["id"]] or app["arms"]["rust"]["reason"] == "OPTIONAL_TOOLCHAIN_UNAVAILABLE" for app in apps)
        report: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "status": "MEASURED" if gates["all_required_arms_measured"] else "UNMEASURED",
            "protocol": {
                "warmups": warmups,
                "measured_runs": measured_runs,
                "sample_replicates": DEFAULT_SAMPLE_REPLICATES,
                "sample_aggregation": "minimum",
                "bootstrap_replicates": BOOTSTRAP_REPLICATES,
                "bootstrap_seed": seed,
                "mad_limit": MAD_LIMIT,
                "ratio_limits": dict(CLASS_RATIO_LIMITS),
                "schedule": "seeded_randomized_strictly_sequential",
                "timer": "time.perf_counter_ns",
            },
            "workloads": {"schema": WORKLOAD_SCHEMA_VERSION, "digest": workload_digest(selected), "items": [item.to_dict() for item in selected]},
            "algorithms": {"digest": ALGORITHM_DIGEST, "arms": list(ARMS), "required_arms": list(REQUIRED_ARMS), "optional_arms": list(OPTIONAL_ARMS)},
            "randomized_schedule": {"seed": seed, "rounds": rounds},
            "environment": environment,
            "toolchains": _toolchain_metadata(),
            "affinity": affinity,
            "applications": apps,
            "raw_samples": raw_samples,
            "artifacts": metadata,
            "gates": gates,
            "material_gaps": gaps,
            "passed": all(gates.values()),
        }
        validate_alpha_performance_report(report)
        if raw_report_path is not None:
            write_raw_report(report, raw_report_path)
        return report
    finally:
        _restore_affinity(affinity, original)


def _metadata_by_builds(workloads: Sequence[FrozenWorkload], builds: Mapping[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    arm_map = {"merlo_concise": "concise_merlo", "merlo_canonical": "canonical_merlo", "c": "native", "python": "python", "rust": "rust"}
    for workload in workloads:
        app = workload.source_application
        metadata[workload.id] = {}
        for arm, build_arm in arm_map.items():
            record = builds.get(f"{app}:{build_arm}")
            if isinstance(record, Mapping):
                source_sha256 = (
                    record.get("canonical_source_sha256")
                    if arm == "merlo_canonical"
                    else record.get("source_sha256")
                )
                metadata[workload.id][arm] = {
                    "status": record.get("status"),
                    "source_sha256": source_sha256,
                    "optimized_artifact_sha256": record.get("binary_sha256"),
                    "optimized_artifact_bytes": record.get("binary_bytes"),
                    "binary": record.get("binary"),
                    "binary_sha256": record.get("binary_sha256"),
                    "generated_source": record.get("generated_source"),
                    "generated_source_sha256": record.get("generated_source_sha256"),
                    "compiler": record.get("compiler"),
                    "compiler_version": record.get("compiler_version"),
                    "command": record.get("command", []),
                }
            else:
                metadata[workload.id][arm] = {"status": "UNAVAILABLE", "source_sha256": workload.source_sha256.get(arm)}
    return metadata


def build_alpha_runner_registry(
    root: str | Path = ".",
) -> tuple[dict[tuple[str, str], Runner], dict[str, Any]]:
    """Reuse productive Merlo/C/Python builds for the frozen alpha workloads."""
    productive, builds = build_productive_runner_registry(root)
    source_workloads = {item.application: item for item in PRODUCTIVE_WORKLOADS}
    registry: dict[tuple[str, str], Runner] = {}
    for workload in FROZEN_WORKLOADS:
        source = source_workloads[workload.source_application]
        for alpha_arm, productive_arm in (
            ("merlo_concise", "concise_merlo"),
            ("merlo_canonical", "canonical_merlo"),
            ("c", "native"),
            ("python", "python"),
        ):
            runner = productive.get((workload.source_application, productive_arm))
            if runner is None:
                continue

            def adapted(
                current: FrozenWorkload,
                payload: bytes,
                *,
                runner: Callable[..., ArmMeasurement] = runner,
                source: Any = source,
            ) -> Measurement:
                observed = runner(source, payload)
                return Measurement(observed.elapsed_ns, observed.result_digest)

            registry[(workload.id, alpha_arm)] = adapted
    return registry, builds


def write_raw_report(report: Mapping[str, Any], path: str | Path) -> None:
    validate_alpha_performance_report(report)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def load_raw_report(path: str | Path) -> dict[str, Any]:
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PerformanceEvidenceError(f"cannot read raw report: {exc}") from exc
    if not isinstance(report, dict):
        raise PerformanceEvidenceError("raw report root must be an object")
    validate_alpha_performance_report(report)
    return report


def validate_alpha_performance_report(report: Mapping[str, Any]) -> None:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise PerformanceEvidenceError("unsupported alpha performance schema")
    protocol = report.get("protocol")
    if (
        not isinstance(protocol, Mapping)
        or not isinstance(protocol.get("warmups"), int)
        or isinstance(protocol.get("warmups"), bool)
        or protocol["warmups"] < 0
        or not isinstance(protocol.get("measured_runs"), int)
        or isinstance(protocol.get("measured_runs"), bool)
        or protocol["measured_runs"] < 1
        or protocol.get("sample_replicates") != DEFAULT_SAMPLE_REPLICATES
        or protocol.get("sample_aggregation") != "minimum"
        or not isinstance(protocol.get("bootstrap_seed"), int)
        or isinstance(protocol.get("bootstrap_seed"), bool)
        or protocol.get("schedule") != "seeded_randomized_strictly_sequential"
    ):
        raise PerformanceEvidenceError("invalid alpha performance protocol")
    if protocol.get("bootstrap_replicates") != BOOTSTRAP_REPLICATES or protocol.get("mad_limit") != MAD_LIMIT or protocol.get("ratio_limits") != CLASS_RATIO_LIMITS:
        raise PerformanceEvidenceError("alpha protocol constants changed")
    workloads = report.get("workloads")
    if not isinstance(workloads, Mapping) or workloads.get("schema") != WORKLOAD_SCHEMA_VERSION or not isinstance(workloads.get("items"), list):
        raise PerformanceEvidenceError("missing workload lock")
    try:
        parsed = tuple(_workload_from_dict(item) for item in workloads["items"])
    except (TypeError, KeyError, ValueError) as exc:
        raise PerformanceEvidenceError("invalid workload lock") from exc
    if len({item.id for item in parsed}) != len(parsed) or workloads.get("digest") != workload_digest(parsed):
        raise PerformanceEvidenceError("workload lock digest mismatch")
    algorithms = report.get("algorithms")
    if not isinstance(algorithms, Mapping) or algorithms.get("digest") != ALGORITHM_DIGEST or tuple(algorithms.get("arms", ())) != ARMS or tuple(algorithms.get("required_arms", ())) != REQUIRED_ARMS or tuple(algorithms.get("optional_arms", ())) != OPTIONAL_ARMS:
        raise PerformanceEvidenceError("algorithm lock mismatch")
    environment = report.get("environment")
    required_environment = {
        "argv",
        "python",
        "python_executable",
        "platform",
        "uname",
        "cpu_count",
        "load_average",
        "affinity_before",
        "monotonic_clock",
    }
    if (
        not isinstance(environment, Mapping)
        or set(environment) != required_environment
        or not isinstance(environment.get("uname"), Mapping)
    ):
        raise PerformanceEvidenceError("reproducible environment metadata is missing")
    apps = report.get("applications")
    if not isinstance(apps, list) or [item.get("id") for item in apps if isinstance(item, Mapping)] != [item.id for item in parsed]:
        raise PerformanceEvidenceError("application identity mismatch")
    available: dict[str, list[str]] = {}
    for app, workload in zip(apps, parsed):
        if (
            not isinstance(app, Mapping)
            or app.get("class") != workload.workload_class
            or not isinstance(app.get("workload"), Mapping)
            or not isinstance(app.get("arms"), Mapping)
            or set(app["arms"]) != set(ARMS)
        ):
            raise PerformanceEvidenceError("arm schema mismatch")
        locked_app_workload = app["workload"]
        if (
            locked_app_workload.get("id") != workload.id
            or locked_app_workload.get("fixture") != workload.fixture
            or locked_app_workload.get("bytes") != workload.expected_bytes
            or locked_app_workload.get("input_sha256") != workload.input_sha256
            or locked_app_workload.get("expected_checksum") != workload.expected_checksum
        ):
            raise PerformanceEvidenceError("application workload lock mismatch")
        available[workload.id] = []
        for arm in ARMS:
            item = app["arms"][arm]
            if not isinstance(item, Mapping) or item.get("status") not in {"MEASURED", "UNAVAILABLE"}:
                raise PerformanceEvidenceError("invalid arm status")
            samples = item.get("samples")
            if not isinstance(samples, list) or any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in samples):
                raise PerformanceEvidenceError("raw timing samples must be numeric")
            if item.get("sample_count") != len(samples):
                raise PerformanceEvidenceError("sample count mismatch")
            expected = summarize_samples(samples, seed=protocol["bootstrap_seed"], replicates=BOOTSTRAP_REPLICATES)
            for key, value in expected.items():
                observed = item.get(key)
                if key == "bootstrap_median_95_ci" and isinstance(observed, list):
                    observed = tuple(observed)
                if observed != value:
                    raise PerformanceEvidenceError("timing summary mismatch")
            if item["status"] == "MEASURED":
                available[workload.id].append(arm)
                if len(samples) != protocol["measured_runs"]:
                    raise PerformanceEvidenceError("measured sample count mismatch")
            elif samples or item.get("raw_sample_count") != 0:
                raise PerformanceEvidenceError("unavailable arm has evidence")
    schedule = report.get("randomized_schedule")
    if not isinstance(schedule, Mapping) or schedule.get("seed") != protocol.get("bootstrap_seed") or not isinstance(schedule.get("rounds"), list):
        raise PerformanceEvidenceError("missing randomized schedule")
    expected_rounds = protocol["warmups"] + protocol["measured_runs"]
    if len(schedule["rounds"]) != len(parsed) * expected_rounds:
        raise PerformanceEvidenceError("schedule count mismatch")
    for round_info in schedule["rounds"]:
        if not isinstance(round_info, Mapping) or round_info.get("workload_id") not in available or round_info.get("phase") not in {"warmup", "measured"} or not isinstance(round_info.get("arms"), list) or len(set(round_info["arms"])) != len(round_info["arms"]) or set(round_info["arms"]) != set(available[round_info["workload_id"]]):
            raise PerformanceEvidenceError("schedule is not sequential and complete")
    raw = report.get("raw_samples")
    if not isinstance(raw, list):
        raise PerformanceEvidenceError("raw samples missing")
    counts: dict[tuple[str, str], int] = {}
    measured_counts: dict[tuple[str, str], int] = {}
    raw_elapsed: dict[tuple[str, str], list[float]] = {}
    raw_startup: dict[tuple[str, str], list[float]] = {}
    raw_rss: dict[tuple[str, str], list[int]] = {}
    raw_keys: set[tuple[str, str, str, int]] = set()
    for sample in raw:
        if (
            not isinstance(sample, Mapping)
            or any(key not in sample for key in ("workload_id", "arm", "phase", "iteration", "elapsed_ns", "replicate_elapsed_ns", "startup_ns", "peak_rss_kb", "checksum", "environment"))
            or sample.get("workload_id") not in available
            or sample.get("arm") not in available[sample["workload_id"]]
            or sample.get("phase") not in {"warmup", "measured"}
            or sample.get("environment") != environment
            or sample.get("checksum") != next(item.expected_checksum for item in parsed if item.id == sample["workload_id"])
            or not isinstance(sample.get("iteration"), int)
            or not isinstance(sample.get("elapsed_ns"), (int, float))
            or isinstance(sample.get("elapsed_ns"), bool)
            or sample.get("elapsed_ns") < 0
        ):
            raise PerformanceEvidenceError("tampered raw sample")
        replicate_elapsed = sample["replicate_elapsed_ns"]
        expected_replicates = (
            DEFAULT_SAMPLE_REPLICATES if sample["phase"] == "measured" else 1
        )
        if (
            not isinstance(replicate_elapsed, list)
            or len(replicate_elapsed) != expected_replicates
            or any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or value < 0
                for value in replicate_elapsed
            )
            or float(min(replicate_elapsed)) != float(sample["elapsed_ns"])
        ):
            raise PerformanceEvidenceError("tampered replicated sample")
        raw_key = (sample["workload_id"], sample["arm"], sample["phase"], sample["iteration"])
        if raw_key in raw_keys:
            raise PerformanceEvidenceError("duplicate raw sample")
        raw_keys.add(raw_key)
        key = (sample["workload_id"], sample["arm"])
        counts[key] = counts.get(key, 0) + 1
        raw_elapsed.setdefault(key, []).append(float(sample["elapsed_ns"]))
        if sample["phase"] == "measured":
            measured_counts[key] = measured_counts.get(key, 0) + 1
            startup = sample.get("startup_ns")
            rss = sample.get("peak_rss_kb")
            if startup is not None:
                if not isinstance(startup, (int, float)) or isinstance(startup, bool) or startup < 0:
                    raise PerformanceEvidenceError("invalid raw startup sample")
                raw_startup.setdefault(key, []).append(float(startup))
            if rss is not None:
                if not isinstance(rss, int) or isinstance(rss, bool) or rss < 0:
                    raise PerformanceEvidenceError("invalid raw RSS sample")
                raw_rss.setdefault(key, []).append(int(rss))
    for workload in parsed:
        for arm in available[workload.id]:
            key = (workload.id, arm)
            app = next(item for item in apps if item["id"] == workload.id)
            arm_record = app["arms"][arm]
            if counts.get(key) != protocol["warmups"] + protocol["measured_runs"] or measured_counts.get(key) != protocol["measured_runs"]:
                raise PerformanceEvidenceError("raw sample omission or duplication")
            if raw_elapsed.get(key, [])[protocol["warmups"]:] != [float(value) for value in arm_record["samples"]]:
                raise PerformanceEvidenceError("raw timing differs from arm summary")
            if raw_startup.get(key, []) != [float(value) for value in arm_record.get("startup_samples_ns", [])]:
                raise PerformanceEvidenceError("raw startup differs from arm summary")
            if raw_rss.get(key, []) != [int(value) for value in arm_record.get("peak_rss_kb_samples", [])]:
                raise PerformanceEvidenceError("raw RSS differs from arm summary")
            if arm_record.get("raw_sample_count") != counts[key]:
                raise PerformanceEvidenceError("arm raw sample count mismatch")
    expected_schedule_keys = {
        (round_info["workload_id"], arm, round_info["phase"], round_info["iteration"])
        for round_info in schedule["rounds"]
        for arm in round_info["arms"]
    }
    if raw_keys != expected_schedule_keys:
        raise PerformanceEvidenceError("raw sample schedule mismatch")
    for workload in parsed:
        for arm in available[workload.id]:
            key = (workload.id, arm)
            if counts.get(key) != protocol["warmups"] + protocol["measured_runs"] or measured_counts.get(key) != protocol["measured_runs"]:
                raise PerformanceEvidenceError("raw sample omission or duplication")
    gates = report.get("gates")
    if not isinstance(gates, Mapping):
        raise PerformanceEvidenceError("gate decisions missing")
    expected_gates: dict[str, bool] = {}
    for app in apps:
        ok, _ = _ratio_gate(app["class"], app["arms"])
        expected_gates[f"{app['id']}_within_{app['class']}_ratio"] = ok
    for workload_class in WORKLOAD_CLASSES:
        class_apps = [app for app in apps if app["class"] == workload_class]
        expected_gates[f"{workload_class}_ratio_within_limit"] = bool(class_apps) and all(
            expected_gates[f"{app['id']}_within_{workload_class}_ratio"] for app in class_apps
        )
    expected_gates["all_required_arms_measured"] = all(app["arms"][arm]["status"] == "MEASURED" and app["arms"][arm]["sample_count"] == protocol["measured_runs"] for app in apps for arm in REQUIRED_ARMS)
    expected_gates["all_measured_mad_at_most_0_05"] = all(app["arms"][arm].get("relative_mad") is not None and app["arms"][arm]["relative_mad"] <= MAD_LIMIT for app in apps for arm in available[app["id"]]) and bool(raw)
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise PerformanceEvidenceError("artifact evidence missing")
    for workload in parsed:
        records = artifacts.get(workload.id)
        if not isinstance(records, Mapping):
            raise PerformanceEvidenceError("artifact source lock mismatch")
        for arm in ARMS:
            record = records.get(arm)
            if (
                not isinstance(record, Mapping)
                or record.get("source_sha256") != workload.source_sha256[arm]
            ):
                raise PerformanceEvidenceError("artifact source lock mismatch")
    expected_gates["concise_canonical_optimized_artifacts_identical"] = all(_artifact_identity(artifacts, item.id)[0] for item in parsed)
    expected_gates["frozen_workloads"] = all(
        app["workload"].get("input_sha256") == app["workload"].get("expected_sha256")
        and app["workload"].get("bytes") == workload.expected_bytes
        for app, workload in zip(apps, parsed)
    )
    expected_gates["sequential_schedule"] = all(
        len(round_info["arms"]) == len(set(round_info["arms"]))
        for round_info in schedule["rounds"]
    ) and bool(schedule["rounds"])
    expected_gates["raw_samples_complete"] = all(counts.get((item.id, arm), 0) == protocol["warmups"] + protocol["measured_runs"] for item in parsed for arm in available[item.id])
    expected_gates["rust_optional"] = all("rust" in available[item.id] or report["applications"][index]["arms"]["rust"].get("reason") == "OPTIONAL_TOOLCHAIN_UNAVAILABLE" for index, item in enumerate(parsed))
    if dict(gates) != expected_gates or any(type(value) is not bool for value in gates.values()):
        raise PerformanceEvidenceError("gate decision mismatch")
    expected_status = "MEASURED" if expected_gates["all_required_arms_measured"] else "UNMEASURED"
    if report.get("status") != expected_status or report.get("passed") is not all(expected_gates.values()):
        raise PerformanceEvidenceError("status or passed decision mismatch")


def _workload_from_dict(item: Mapping[str, Any]) -> FrozenWorkload:
    source = item["source_sha256"]
    if (
        not isinstance(source, Mapping)
        or set(source) != set(ARMS)
        or item["class"] not in WORKLOAD_CLASSES
        or not isinstance(item["id"], str)
        or not isinstance(item["fixture"], str)
        or not isinstance(item["expected_bytes"], int)
        or isinstance(item["expected_bytes"], bool)
        or item["expected_bytes"] < 1
        or not isinstance(item["input_sha256"], str)
        or len(item["input_sha256"]) != 64
        or not isinstance(item["expected_checksum"], str)
        or len(item["expected_checksum"]) != 64
        or any(not isinstance(value, str) or len(value) != 64 for value in source.values())
    ):
        raise ValueError("workload lock fields are invalid")
    return FrozenWorkload(
        item["id"], item["class"], item["fixture"], item["expected_bytes"],
        item["input_sha256"], item["expected_checksum"], str(item["algorithm"]),
        {str(key): str(value) for key, value in source.items()}, str(item["source_application"]),
    )
# Short aliases make the harness convenient for release scripts and fixtures.
run_study = run_alpha_performance
validate_report = validate_alpha_performance_report

__all__ = [
    "ALGORITHM_DIGEST", "ARMS", "BOOTSTRAP_REPLICATES", "CLASS_RATIO_LIMITS",
    "DEFAULT_MEASURED_RUNS", "DEFAULT_SAMPLE_REPLICATES", "DEFAULT_SEED", "DEFAULT_WARMUPS", "FROZEN_WORKLOADS",
    "FrozenWorkload", "MAD_LIMIT", "Measurement", "OPTIONAL_ARMS", "PerformanceEvidenceError",
    "REQUIRED_ARMS", "Runner", "Sample", "SCHEMA_VERSION", "WORKLOAD_CLASSES",
    "WORKLOAD_DIGEST", "WORKLOAD_SCHEMA_VERSION", "algorithm_digest", "bootstrap_median_ci",
    "build_alpha_runner_registry", "freeze_workloads", "load_raw_report",
    "median_absolute_deviation", "run_alpha_performance", "run_study", "summarize_samples",
    "validate_alpha_performance_report", "validate_report", "workload_digest", "write_raw_report",
]
