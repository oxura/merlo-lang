"""Frozen Productive Core performance protocol.

The default report never invents measurements.  An explicit runner registry
executes independent Merlo application artifacts, the Python references, and
compiled native C applications.  Merlo build failures stay explicit in build
evidence; no arm is silently replaced by a Python sidecar.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import platform
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from merlo.compiler import compile_project
from merlo.native_c_backend import compile_c_source
from tools.benchmarks.merlo.productive_applications import (
    GrepOptions,
    aggregate_csv,
    analyze_ndjson,
    search_text,
)

SCHEMA_VERSION = 1
DEFAULT_WARMUPS = 5
DEFAULT_MEASURED_RUNS = 30
BOOTSTRAP_REPLICATES = 2_000
BOOTSTRAP_SEED = 2_026_081_3
NATIVE_RATIO_LIMIT = 1.5
MAD_RATIO_LIMIT = 0.05
APPLICATIONS = ("ndjson", "csv", "grep")
ARMS = ("concise_merlo", "canonical_merlo", "python", "native")
PYTHON_ENTRYPOINTS = {
    "ndjson": "analyze_ndjson",
    "csv": "aggregate_csv",
    "grep": "search_text",
}


@dataclass(frozen=True)
class FrozenWorkload:
    application: str
    fixture: str
    expected_bytes: int
    expected_sha256: str
    operation: str


@dataclass(frozen=True)
class ArmMeasurement:
    """One result from an explicitly supplied in-process arm runner."""

    elapsed_ns: float
    result_digest: str


Runner = Callable[[FrozenWorkload, bytes], ArmMeasurement]


# Deterministically generated, application-valid large fixtures. Paths, sizes,
# and hashes form the immutable workload lock.
FROZEN_WORKLOADS: tuple[FrozenWorkload, ...] = (
    FrozenWorkload(
        "ndjson",
        "tools/benchmarks/merlo/benchmarks/productive_performance/ndjson/workload.ndjson",
        548_337,
        "ca9aca6d54d49e5f9203b985c763ae0fc91600027cd56fa043b7474e81c23bb1",
        "Map[Text,UInt64]",
    ),
    FrozenWorkload(
        "csv",
        "tools/benchmarks/merlo/benchmarks/productive_performance/csv/workload.csv",
        182_104,
        "d2f8f12ac2f5c59ab613b716b958d63e31ae87ae9f7458756c7e8d711c2a7599",
        "Map[Text,UInt64]",
    ),
    FrozenWorkload(
        "grep",
        "tools/benchmarks/merlo/benchmarks/productive_performance/grep/workload.txt",
        286_389,
        "7293083cbfcfe616ea3750158c02e717d85008eee9ce30a2010925fdad631a31",
        "Map[Text,UInt64]",
    ),
)

# Four arm descriptions are immutable.  In particular, the native arm is not
# replaced by a Python implementation when its executable is unavailable.
ALGORITHM_DESCRIPTORS: tuple[tuple[str, str], ...] = (
    ("concise_merlo", "frozen concise Merlo source artifact"),
    ("canonical_merlo", "frozen canonical Merlo source artifact"),
    ("python", "pinned external Python artifact, if present"),
    ("native", "native executable artifact, if present"),
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _workload_payload() -> list[dict[str, object]]:
    return [
        {
            "application": item.application,
            "fixture": item.fixture,
            "expected_bytes": item.expected_bytes,
            "expected_sha256": item.expected_sha256,
            "operation": item.operation,
        }
        for item in FROZEN_WORKLOADS
    ]


def workload_digest() -> str:
    return hashlib.sha256(_canonical(_workload_payload())).hexdigest()


def algorithm_digest() -> str:
    return hashlib.sha256(_canonical(ALGORITHM_DESCRIPTORS)).hexdigest()


def mutation_lock_digest() -> str:
    return hashlib.sha256(_canonical({"workloads": _workload_payload(), "algorithms": ALGORITHM_DESCRIPTORS})).hexdigest()


WORKLOAD_DIGEST = workload_digest()
ALGORITHM_DIGEST = algorithm_digest()
MUTATION_LOCK = mutation_lock_digest()


def median_absolute_deviation(samples: Sequence[float]) -> float | None:
    """Return the population MAD around the sample median."""
    if not samples:
        return None
    center = statistics.median(samples)
    return statistics.median(abs(value - center) for value in samples)


def bootstrap_median_ci(
    samples: Sequence[float],
    *,
    seed: int = BOOTSTRAP_SEED,
    replicates: int = BOOTSTRAP_REPLICATES,
) -> tuple[float, float] | None:
    """Return a deterministic percentile CI for the sample median."""
    if not samples:
        return None
    if replicates < 1:
        raise ValueError("bootstrap replicates must be positive")
    values = tuple(float(value) for value in samples)
    rng = random.Random(seed)
    medians = [statistics.median(rng.choices(values, k=len(values))) for _ in range(replicates)]
    medians.sort()
    lower = medians[int(0.025 * (replicates - 1))]
    upper = medians[int(0.975 * (replicates - 1))]
    return (lower, upper)


def summarize_samples(
    samples: Sequence[float],
    *,
    seed: int = BOOTSTRAP_SEED,
    replicates: int = BOOTSTRAP_REPLICATES,
) -> dict[str, object]:
    """Summarize real observations without inventing values for empty arms."""
    values = [float(value) for value in samples]
    median = statistics.median(values) if values else None
    mad = median_absolute_deviation(values)
    return {
        "samples": values,
        "sample_count": len(values),
        "median": median,
        "mad": mad,
        "relative_mad": (mad / median if mad is not None and median else None),
        "bootstrap_median_95_ci": bootstrap_median_ci(values, seed=seed, replicates=replicates),
    }


def _fixture_observation(root: Path, workload: FrozenWorkload) -> dict[str, object]:
    path = root / workload.fixture
    if not path.is_file():
        return {
            "status": "UNMEASURED",
            "reason": "FIXTURE_NOT_FOUND",
            "path": workload.fixture,
            "expected_bytes": workload.expected_bytes,
            "expected_sha256": workload.expected_sha256,
            "observed_bytes": None,
            "observed_sha256": None,
        }
    payload = path.read_bytes()
    observed = hashlib.sha256(payload).hexdigest()
    return {
        "status": "READY" if len(payload) == workload.expected_bytes and observed == workload.expected_sha256 else "INVALID",
        "reason": None if len(payload) == workload.expected_bytes and observed == workload.expected_sha256 else "FIXTURE_DIGEST_MISMATCH",
        "path": workload.fixture,
        "expected_bytes": workload.expected_bytes,
        "expected_sha256": workload.expected_sha256,
        "observed_bytes": len(payload),
        "observed_sha256": observed,
    }


def _arm_artifact(root: Path, application: str, arm: str) -> list[str]:
    if arm == "concise_merlo":
        base = root / "tools" / "benchmarks" / "merlo" / "programs" / f"productive_{application}" / "app"
        return sorted(str(path.relative_to(root)) for path in base.glob("*.mlo") if path.is_file())
    if arm == "canonical_merlo":
        base = root / "tools" / "benchmarks" / "merlo" / "programs" / f"productive_{application}" / "canonical"
        return sorted(str(path.relative_to(root)) for path in base.glob("*.mlo") if path.is_file())
    if arm == "python":
        path = root / "tools" / "benchmarks" / "merlo" / "benchmarks" / "productive_simplicity" / application / "reference.py"
        return [str(path.relative_to(root))] if path.is_file() else []
    if arm == "native":
        source = root / "tools" / "benchmarks" / "merlo" / "benchmarks" / "productive_simplicity" / application / "reference.c"
        base = root / "tools" / "benchmarks" / "merlo" / "benchmarks" / "productive_performance" / application
        paths = ([source] if source.is_file() else []) + [
            path for path in base.glob("native*") if path.is_file()
        ]
        return sorted(str(path.relative_to(root)) for path in paths)
    raise ValueError(f"unknown performance arm: {arm}")


def _empty_arm(root: Path, application: str, arm: str, *, reason: str | None = None) -> dict[str, object]:
    artifacts = _arm_artifact(root, application, arm)
    if reason is None:
        reason = "NATIVE_EXECUTABLE_NOT_PROVIDED" if arm == "native" else (
            "NO_MEASUREMENT_RUNNER" if artifacts else "SOURCE_ARTIFACT_NOT_FOUND"
        )
    return {
        "status": "UNMEASURED",
        "reason": reason,
        "artifacts": artifacts,
        **summarize_samples(()),
    }

def _python_application_output(application: str, path: Path) -> bytes:
    if application == "ndjson":
        return analyze_ndjson(path).report.encode("utf-8")
    if application == "csv":
        return aggregate_csv(path).report.encode("utf-8")
    if application == "grep":
        return search_text(path, GrepOptions(contains="needle")).output.encode("utf-8")
    raise ValueError(f"unknown productive application: {application}")


def _source_digest(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _build_record(
    base: Path,
    source_paths: Sequence[Path],
    build: object,
    *,
    adapter: str | None = None,
) -> dict[str, object]:
    native = getattr(build, "native", build)
    binary_path = getattr(native, "binary_path", None)
    source_path = getattr(native, "source_path", None)
    record: dict[str, object] = {
        "status": getattr(native, "status", "FAILED"),
        "source": [
            str(path.relative_to(base))
            if path.resolve().is_relative_to(base)
            else str(path.resolve())
            for path in source_paths
        ],
        "source_sha256": _source_digest(source_paths),
        "binary": str(binary_path) if binary_path is not None else None,
        "binary_sha256": getattr(native, "binary_sha256", None),
        "binary_bytes": (
            Path(binary_path).stat().st_size
            if binary_path is not None and Path(binary_path).is_file()
            else None
        ),
        "generated_source": str(source_path) if source_path is not None else None,
        "generated_source_sha256": getattr(native, "source_sha256", None),
        "compiler": getattr(native, "compiler", None),
        "compiler_version": getattr(native, "compiler_version", None),
        "command": list(getattr(native, "command", ())),
        "stderr": getattr(native, "stderr", ""),
    }
    if adapter is not None:
        record["adapter"] = adapter
    return record


def _binary_runner(
    base: Path,
    application: str,
    binary: str,
    *,
    include_grep_options: bool = False,
) -> Runner:
    def runner(
        workload: FrozenWorkload,
        payload: bytes,
        *,
        application: str = application,
        binary: str = binary,
    ) -> ArmMeasurement:
        if hashlib.sha256(payload).hexdigest() != workload.expected_sha256:
            raise ValueError("runner payload does not match frozen workload")
        command = [binary, str(base / workload.fixture)]
        if application == "grep" and include_grep_options:
            command.extend(("--contains", "needle"))
        started = time.perf_counter_ns()
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=60,
        )
        elapsed = time.perf_counter_ns() - started
        if completed.returncode != 0:
            raise RuntimeError(
                f"{application} Merlo/native arm failed: "
                f"{completed.stderr.decode(errors='replace')}"
            )
        return ArmMeasurement(
            float(elapsed),
            hashlib.sha256(completed.stdout).hexdigest(),
        )

    return runner

def _canonical_roundtrip_project(
    application: str,
    canonical_source: str,
    effects: Sequence[str],
    build_root: Path,
) -> Path:
    """Materialize the compiler's canonical expansion as an independent input."""
    marker = "task main("
    if canonical_source.count(marker) != 1:
        raise ValueError(f"{application}: canonical expansion has no unique main task")
    source = "module app.main\n\n" + canonical_source.replace(
        marker, "export task main(", 1
    )
    header_start = source.index("\nexport task main(") + 1
    header_end = source.index("\n", header_start)
    body_start = header_end + 1
    if not source.startswith("    uses ", body_start):
        source = (
            source[:body_start]
            + f"    uses {', '.join(effects)}\n"
            + source[body_start:]
        )
    entry = build_root / application / "canonical_roundtrip" / "app" / "main.mlo"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(source, encoding="utf-8")
    return entry


def build_productive_runner_registry(
    root: str | Path = ".",
) -> tuple[dict[tuple[str, str], Runner], dict[str, dict[str, object]]]:
    """Build independent Merlo, Python, and native runners for each application."""
    base = Path(root).resolve()
    registry: dict[tuple[str, str], Runner] = {}
    builds: dict[str, dict[str, object]] = {}
    # Binaries are retained in a runtime-only directory because runners outlive
    # this function and generated benchmark artifacts must not enter the tree.
    build_root = Path(tempfile.mkdtemp(prefix="productive-performance-"))
    python_source_path = base / "tools" / "benchmarks" / "merlo" / "productive_applications.py"
    python_source_sha256 = _source_digest((python_source_path,))

    for application in APPLICATIONS:
        def python_runner(
            workload: FrozenWorkload,
            payload: bytes,
            *,
            application: str = application,
        ) -> ArmMeasurement:
            if hashlib.sha256(payload).hexdigest() != workload.expected_sha256:
                raise ValueError("runner payload does not match frozen workload")
            started = time.perf_counter_ns()
            output = _python_application_output(application, base / workload.fixture)
            elapsed = time.perf_counter_ns() - started
            return ArmMeasurement(float(elapsed), hashlib.sha256(output).hexdigest())

        registry[(application, "python")] = python_runner
        builds[f"{application}:python"] = {
            "status": "MEASURED",
            "source": [str(python_source_path.relative_to(base))],
            "source_sha256": python_source_sha256,
            "binary": None,
            "binary_sha256": None,
            "binary_bytes": None,
            "generated_source": None,
            "generated_source_sha256": None,
            "compiler": platform.python_implementation(),
            "compiler_version": platform.python_version(),
            "command": [
                "python-in-process",
                f"merlo.productive_applications.{PYTHON_ENTRYPOINTS[application]}",
            ],
            "stderr": "",
        }

        native_source_path = (
            base / "tools" / "benchmarks" / "merlo" / "benchmarks" / "productive_simplicity" / application / "reference.c"
        )
        native_source = native_source_path.read_text(encoding="utf-8")
        native_source_sha256 = hashlib.sha256(native_source.encode("utf-8")).hexdigest()
        native_build = compile_c_source(
            native_source,
            output_dir=build_root / application / "native",
            stem=f"native_{native_source_sha256[:16]}",
        )
        native_key = f"{application}:native"
        builds[native_key] = _build_record(base, (native_source_path,), native_build)
        if (
            getattr(native_build, "status", None) == "MEASURED"
            and getattr(native_build, "binary_path", None) is not None
        ):
            registry[(application, "native")] = _binary_runner(
                base,
                application,
                str(native_build.binary_path),
                include_grep_options=True,
            )

        source_dir = base / "tools" / "benchmarks" / "merlo" / "programs" / f"productive_{application}" / "app"
        source_paths = tuple(
            sorted(path for path in source_dir.glob("*.mlo") if path.is_file())
        )
        concise_entry = next(
            (path for path in source_paths if path.name == "main.mlo"),
            source_paths[0] if source_paths else source_dir / "main.mlo",
        )
        concise_build: object | None = None
        canonical_source: str | None = None

        for arm in ("concise_merlo", "canonical_merlo"):
            key = f"{application}:{arm}"
            if not source_paths:
                builds[key] = {
                    "status": "UNMEASURED",
                    "reason": "SOURCE_ARTIFACT_NOT_FOUND",
                    "source": [],
                    "source_sha256": None,
                    "binary": None,
                    "binary_sha256": None,
                }
                continue
            entry = concise_entry
            adapter = "concise_main"
            canonical_entry: Path | None = None
            if arm == "canonical_merlo":
                if concise_build is None or canonical_source is None:
                    builds[key] = {
                        "status": "FAILED",
                        "reason": "CANONICAL_EXPANSION_UNAVAILABLE",
                        "source": [str(path.relative_to(base)) for path in source_paths],
                        "source_sha256": _source_digest(source_paths),
                        "binary": None,
                        "binary_sha256": None,
                        "adapter": "canonical_expansion_roundtrip",
                    }
                    continue
                tasks = getattr(getattr(concise_build, "elaborated"), "tasks")
                effects = next(item.effects for item in tasks if item.name == "main")
                canonical_entry = _canonical_roundtrip_project(
                    application,
                    canonical_source,
                    effects,
                    build_root,
                )
                entry = canonical_entry
                adapter = "canonical_expansion_roundtrip"
            output = build_root / application / arm / "app"
            try:
                built = compile_project(
                    entry,
                    emit_native=True,
                    output=output,
                    require_interface_lock=False,
                )
            except Exception as exc:
                builds[key] = {
                    "status": "FAILED",
                    "reason": "MERLO_BUILD_FAILED",
                    "error": f"{type(exc).__name__}: {exc}",
                    "source": [str(path.relative_to(base)) for path in source_paths],
                    "source_sha256": _source_digest(source_paths),
                    "binary": None,
                    "binary_sha256": None,
                    "adapter": adapter,
                }
                continue
            if arm == "concise_merlo":
                concise_build = built
                canonical_source = getattr(getattr(built, "elaborated"), "canonical_source")
            record_source_paths = (
                (canonical_entry,)
                if canonical_entry is not None
                else tuple(source_paths)
            )
            builds[key] = _build_record(
                base,
                record_source_paths,
                built,
                adapter=adapter,
            )
            if canonical_entry is not None:
                builds[key]["adapter_source"] = [
                    str(canonical_entry.resolve())
                ]
                builds[key]["canonical_source_sha256"] = _source_digest(
                    (canonical_entry,)
                )
            binary_path = getattr(getattr(built, "native", built), "binary_path", None)
            if (
                getattr(getattr(built, "native", built), "status", None) == "MEASURED"
                and binary_path is not None
            ):
                registry[(application, arm)] = _binary_runner(
                    base, application, str(binary_path)
                )

    return registry, builds


def _runner_for(
    registry: Mapping[tuple[str, str], Runner] | None,
    application: str,
    arm: str,
) -> Runner | None:
    if registry is None:
        return None
    runner = registry.get((application, arm))
    if runner is not None and not callable(runner):
        raise TypeError(f"runner for {application}/{arm} is not callable")
    return runner


def _measure_registered_arms(
    workload: FrozenWorkload,
    payload: bytes,
    arms: dict[str, dict[str, object]],
    registry: Mapping[tuple[str, str], Runner] | None,
    *,
    warmups: int,
    measured_runs: int,
    seed: int,
    schedule: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    runners = {
        arm: _runner_for(registry, workload.application, arm)
        for arm in ARMS
    }
    available = [arm for arm in ARMS if runners[arm] is not None]
    if not available:
        return {"status": "UNMEASURED", "digest": None, "reason": "NO_MEASUREMENT_RUNNERS"}
    preflight: dict[str, ArmMeasurement] = {}
    for arm in available:
        runner = runners[arm]
        assert runner is not None
        observation = runner(workload, payload)
        if not isinstance(observation, ArmMeasurement) or not observation.result_digest:
            raise ValueError(f"invalid preflight result for {workload.application}/{arm}")
        if observation.elapsed_ns < 0:
            raise ValueError(f"negative preflight duration for {workload.application}/{arm}")
        preflight[arm] = observation
    samples = {arm: [] for arm in available}
    expected_digest = preflight[available[0]].result_digest
    if any(observation.result_digest != expected_digest for observation in preflight.values()):
        for measured_arm in available:
            arms[measured_arm]["reason"] = "RESULT_EQUIVALENCE_FAILED"
            arms[measured_arm]["status"] = "INVALID"
            arms[measured_arm].update(summarize_samples(()))
        return {"status": "INVALID", "digest": None, "reason": "RESULT_EQUIVALENCE_FAILED"}
    for round_info in schedule:
        if round_info.get("phase") == "warmup":
            for arm in round_info["arms"]:
                if arm in runners and runners[arm] is not None:
                    runner = runners[arm]
                    assert runner is not None
                    observation = runner(workload, payload)
                    if observation.result_digest != expected_digest:
                        for measured_arm in available:
                            arms[measured_arm]["reason"] = "RESULT_EQUIVALENCE_FAILED"
                            arms[measured_arm]["status"] = "INVALID"
                            arms[measured_arm].update(summarize_samples(()))
                        return {"status": "INVALID", "digest": None, "reason": "RESULT_EQUIVALENCE_FAILED"}
        else:
            for arm in round_info["arms"]:
                if arm in samples:
                    runner = runners[arm]
                    assert runner is not None
                    observation = runner(workload, payload)
                    if observation.result_digest != expected_digest:
                        for measured_arm in available:
                            arms[measured_arm]["reason"] = "RESULT_EQUIVALENCE_FAILED"
                            arms[measured_arm]["status"] = "INVALID"
                            arms[measured_arm].update(summarize_samples(()))
                        return {"status": "INVALID", "digest": None, "reason": "RESULT_EQUIVALENCE_FAILED"}
                    samples[arm].append(float(observation.elapsed_ns))
    for arm in available:
        arms[arm].update(summarize_samples(samples[arm], seed=seed))
        arms[arm]["status"] = "MEASURED"
        arms[arm]["reason"] = None
    return {"status": "MEASURED", "digest": expected_digest, "reason": None}


def _schedule(*, seed: int, warmups: int, measured_runs: int) -> list[dict[str, object]]:
    rng = random.Random(seed)
    rounds: list[dict[str, object]] = []
    for phase, count in (("warmup", warmups), ("measured", measured_runs)):
        for iteration in range(count):
            arms = list(ARMS)
            rng.shuffle(arms)
            rounds.append({"phase": phase, "iteration": iteration, "arms": arms})
    return rounds


def _pin_affinity() -> tuple[dict[str, object], set[int] | None]:
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


def _restore_affinity(affinity: dict[str, object], original: set[int] | None) -> None:
    if original is not None and affinity.get("applied") and hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, original)
        affinity["restored"] = True


def _samples_for_arm(arm: Mapping[str, object]) -> list[float]:
    samples = arm.get("samples")
    if not isinstance(samples, list):
        return []
    return [float(value) for value in samples if isinstance(value, (int, float))]


def _native_gate(app: str, arms: Mapping[str, Mapping[str, object]], *, limit: float = NATIVE_RATIO_LIMIT) -> bool:
    del app
    native_samples = _samples_for_arm(arms["native"])
    merlo_samples = _samples_for_arm(arms["concise_merlo"])
    native_median = statistics.median(native_samples) if native_samples else None
    merlo_median = statistics.median(merlo_samples) if merlo_samples else None
    return (
        isinstance(native_median, (int, float))
        and native_median > 0
        and isinstance(merlo_median, (int, float))
        and merlo_median / native_median <= limit
    )


def _mad_gate(
    applications: Sequence[Mapping[str, object]],
    *,
    limit: float = MAD_RATIO_LIMIT,
) -> bool:
    measured = 0
    for item in applications:
        arms = item["arms"]
        if not isinstance(arms, Mapping):
            return False
        for arm in arms.values():
            if not isinstance(arm, Mapping):
                return False
            samples = _samples_for_arm(arm)
            if samples:
                measured += 1
                median = statistics.median(samples)
                mad = median_absolute_deviation(samples)
                relative = mad / median if mad is not None and median else None
                if relative is None or relative > limit:
                    return False
    return measured > 0


def run_productive_performance(
    root: str | Path = ".",
    *,
    warmups: int = DEFAULT_WARMUPS,
    measured_runs: int = DEFAULT_MEASURED_RUNS,
    seed: int = BOOTSTRAP_SEED,
    runner_registry: Mapping[tuple[str, str], Runner] | None = None,
    runners: Mapping[tuple[str, str], Runner] | None = None,
) -> dict[str, object]:
    """Build a frozen, sequential performance report from available artifacts."""
    if warmups < 0 or measured_runs < 1:
        raise ValueError("warmups must be non-negative and measured_runs must be positive")
    if runner_registry is not None and runners is not None:
        raise ValueError("pass only one runner registry")
    registry = runner_registry if runner_registry is not None else runners
    root_path = Path(root).resolve()
    rounds = _schedule(seed=seed, warmups=warmups, measured_runs=measured_runs)
    affinity, original = _pin_affinity()
    try:
        applications: list[dict[str, object]] = []
        for workload in FROZEN_WORKLOADS:
            arms = {arm: _empty_arm(root_path, workload.application, arm) for arm in ARMS}
            observation = _fixture_observation(root_path, workload)
            equivalence: dict[str, object] = {
                "status": "UNMEASURED",
                "digest": None,
                "reason": "NO_MEASUREMENT_RUNNERS",
            }
            if observation["status"] == "READY" and registry is not None:
                payload = (root_path / workload.fixture).read_bytes()
                app_rounds = [item for item in rounds if item["phase"] in {"warmup", "measured"}]
                equivalence = _measure_registered_arms(
                    workload,
                    payload,
                    arms,
                    registry,
                    warmups=warmups,
                    measured_runs=measured_runs,
                    seed=seed,
                    schedule=app_rounds,
                )
            applications.append(
                {
                    "application": workload.application,
                    "workload": {
                        "fixture": workload.fixture,
                        "operation": workload.operation,
                        "digest": workload.expected_sha256,
                        "bytes": workload.expected_bytes,
                        "observation": observation,
                    },
                    "result_equivalence": equivalence,
                    "arms": arms,
                }
            )
        gates: dict[str, bool] = {}
        for item in applications:
            arms = item["arms"]
            assert isinstance(arms, Mapping)
            gates[f"{item['application']}_within_native_1_5"] = _native_gate(
                item["application"],
                arms,
                limit=NATIVE_RATIO_LIMIT,
            )
        gates["all_four_arms_measured"] = all(
            all(
                isinstance(arm, Mapping)
                and arm.get("status") == "MEASURED"
                and isinstance(arm.get("samples"), list)
                and len(arm["samples"]) == measured_runs
                for arm in item["arms"].values()
            )
            for item in applications
        )
        gates["artifact_result_equivalence"] = all(
            isinstance(item.get("result_equivalence"), Mapping)
            and item["result_equivalence"].get("status") == "MEASURED"
            for item in applications
        )
        gates["identical_input_semantics"] = all(
            item["workload"]["operation"] == workload.operation
            for item, workload in zip(applications, FROZEN_WORKLOADS)
        )
        gates["all_measured_mad_at_most_0_05"] = _mad_gate(applications, limit=MAD_RATIO_LIMIT)
        gates["algorithms_frozen"] = algorithm_digest() == ALGORITHM_DIGEST
        gates["mutation_lock"] = mutation_lock_digest() == MUTATION_LOCK
        invalid_equivalence = any(
            isinstance(item.get("result_equivalence"), Mapping)
            and item["result_equivalence"].get("status") == "INVALID"
            for item in applications
        )
        status = (
            "INVALID"
            if invalid_equivalence
            else "MEASURED"
            if any(
                isinstance(item["arms"], Mapping)
                and any(
                    isinstance(arm, Mapping)
                    and isinstance(arm.get("samples"), list)
                    and bool(arm["samples"])
                    for arm in item["arms"].values()
                )
                for item in applications
            )
            else "UNMEASURED"
        )
        report: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "protocol": {
                "warmups": warmups,
                "measured_runs": measured_runs,
                "bootstrap_replicates": BOOTSTRAP_REPLICATES,
                "bootstrap_seed": seed,
                "arm_order": "deterministically_randomized_sequential",
                "native_ratio_limit": NATIVE_RATIO_LIMIT,
                "mad_ratio_limit": MAD_RATIO_LIMIT,
            },
            "frozen_workloads": {
                "digest": WORKLOAD_DIGEST,
                "items": _workload_payload(),
            },
            "algorithms": {
                "digest": ALGORITHM_DIGEST,
                "arms": [name for name, _ in ALGORITHM_DESCRIPTORS],
            },
            "randomized_schedule": {"seed": seed, "rounds": rounds},
            "affinity": affinity,
            "applications": applications,
            "gates": gates,
            "mutation_lock": {"digest": MUTATION_LOCK, "verified": gates["mutation_lock"]},
            "passed": all(gates.values()),
        }
        validate_productive_performance_report(report)
        return report
    finally:
        _restore_affinity(affinity, original)


def validate_productive_performance_report(report: Mapping[str, object]) -> None:
    """Raise ``ValueError`` when a report is malformed or its locks are stale."""
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported productive performance schema")
    if report.get("status") not in {"MEASURED", "UNMEASURED", "INVALID"}:
        raise ValueError("invalid productive performance status")
    protocol = report.get("protocol")
    if (
        not isinstance(protocol, Mapping)
        or not isinstance(protocol.get("warmups"), int)
        or isinstance(protocol.get("warmups"), bool)
        or protocol.get("warmups") < 0
        or not isinstance(protocol.get("measured_runs"), int)
        or isinstance(protocol.get("measured_runs"), bool)
        or protocol.get("measured_runs") < 1
        or protocol.get("bootstrap_replicates") != BOOTSTRAP_REPLICATES
        or not isinstance(protocol.get("bootstrap_seed"), int)
        or isinstance(protocol.get("bootstrap_seed"), bool)
        or protocol.get("arm_order") != "deterministically_randomized_sequential"
        or protocol.get("native_ratio_limit") != NATIVE_RATIO_LIMIT
        or protocol.get("mad_ratio_limit") != MAD_RATIO_LIMIT
    ):
        raise ValueError("invalid performance run protocol")
    measured_run_count = protocol["measured_runs"]
    frozen = report.get("frozen_workloads")
    if (
        not isinstance(frozen, Mapping)
        or frozen.get("digest") != WORKLOAD_DIGEST
        or frozen.get("items") != _workload_payload()
    ):
        raise ValueError("frozen workload digest mismatch")
    algorithms = report.get("algorithms")
    if (
        not isinstance(algorithms, Mapping)
        or algorithms.get("digest") != ALGORITHM_DIGEST
        or tuple(algorithms.get("arms", ())) != ARMS
    ):
        raise ValueError("algorithm lock mismatch")
    mutation = report.get("mutation_lock")
    if (
        not isinstance(mutation, Mapping)
        or mutation.get("digest") != MUTATION_LOCK
        or type(mutation.get("verified")) is not bool
        or mutation.get("verified") is not True
    ):
        raise ValueError("mutation lock mismatch")
    schedule = report.get("randomized_schedule")
    if (
        not isinstance(schedule, Mapping)
        or not isinstance(schedule.get("seed"), int)
        or isinstance(schedule.get("seed"), bool)
        or schedule.get("seed") != protocol["bootstrap_seed"]
        or not isinstance(schedule.get("rounds"), list)
    ):
        raise ValueError("randomized schedule missing")
    rounds = schedule["rounds"]
    if len(rounds) != protocol["warmups"] + protocol["measured_runs"]:
        raise ValueError("randomized schedule count mismatch")
    for round_info in rounds:
        if (
            not isinstance(round_info, Mapping)
            or round_info.get("phase") not in {"warmup", "measured"}
            or tuple(sorted(round_info.get("arms", ()))) != tuple(sorted(ARMS))
        ):
            raise ValueError("randomized schedule arm order mismatch")

    applications = report.get("applications")
    if not isinstance(applications, Sequence) or isinstance(applications, (str, bytes)):
        raise ValueError("application set mismatch")
    names: list[object] = []
    for item in applications:
        if not isinstance(item, Mapping):
            raise ValueError("application arm schema mismatch")
        names.append(item.get("application"))
    if names != list(APPLICATIONS):
        raise ValueError("application set mismatch")

    for item in applications:
        workload = item.get("workload")
        equivalence = item.get("result_equivalence")
        arms = item.get("arms")
        if (
            not isinstance(workload, Mapping)
            or not isinstance(arms, Mapping)
            or not isinstance(equivalence, Mapping)
        ):
            raise ValueError("application arm schema mismatch")
        equivalence_status = equivalence.get("status")
        if equivalence_status not in {"MEASURED", "UNMEASURED", "INVALID"}:
            raise ValueError("result equivalence schema mismatch")
        if set(arms) != set(ARMS):
            raise ValueError("four-arm schema mismatch")
        arm_statuses: list[object] = []
        for arm in arms.values():
            if not isinstance(arm, Mapping):
                raise ValueError("arm schema mismatch")
            samples = arm.get("samples")
            if not isinstance(samples, list) or any(
                not isinstance(value, (int, float)) or isinstance(value, bool)
                for value in samples
            ):
                raise ValueError("arm samples must be numeric")
            if arm.get("sample_count") != len(samples):
                raise ValueError("arm sample count mismatch")
            arm_status = arm.get("status")
            if arm_status not in {"MEASURED", "UNMEASURED", "INVALID"}:
                raise ValueError("arm status schema mismatch")
            if arm_status == "MEASURED" and len(samples) != measured_run_count:
                raise ValueError("measured arm sample count mismatch")
            if arm_status != "MEASURED" and samples:
                raise ValueError("unmeasured arm has samples")
            arm_statuses.append(arm_status)
            expected_summary = summarize_samples(
                samples,
                seed=protocol["bootstrap_seed"],
                replicates=protocol["bootstrap_replicates"],
            )
            for key, expected in expected_summary.items():
                if arm.get(key) != expected:
                    raise ValueError("arm summary mismatch")
        expected_equivalence_status = (
            "INVALID"
            if "INVALID" in arm_statuses
            else "MEASURED"
            if "MEASURED" in arm_statuses
            else "UNMEASURED"
        )
        if equivalence_status != expected_equivalence_status:
            raise ValueError("result equivalence mismatch")

    expected_gates: dict[str, bool] = {}
    for item in applications:
        item_arms = item["arms"]
        assert isinstance(item_arms, Mapping)
        expected_gates[f"{item['application']}_within_native_1_5"] = _native_gate(
            item["application"],
            item_arms,
            limit=protocol["native_ratio_limit"],
        )
    expected_gates["all_four_arms_measured"] = all(
        all(
            isinstance(arm, Mapping)
            and arm.get("status") == "MEASURED"
            and len(arm["samples"]) == measured_run_count
            for arm in item["arms"].values()
        )
        for item in applications
    )
    expected_gates["artifact_result_equivalence"] = all(
        item["result_equivalence"]["status"] == "MEASURED"
        for item in applications
    )
    expected_gates["identical_input_semantics"] = all(
        item["workload"].get("operation") == workload.operation
        for item, workload in zip(applications, FROZEN_WORKLOADS)
    )
    expected_gates["all_measured_mad_at_most_0_05"] = _mad_gate(
        applications,
        limit=protocol["mad_ratio_limit"],
    )
    expected_gates["algorithms_frozen"] = (
        algorithms.get("digest") == ALGORITHM_DIGEST
        and tuple(algorithms.get("arms", ())) == ARMS
    )
    expected_gates["mutation_lock"] = mutation.get("digest") == MUTATION_LOCK

    gates = report.get("gates")
    if not isinstance(gates, Mapping) or set(gates) != set(expected_gates):
        raise ValueError("gate mapping mismatch")
    if any(type(value) is not bool for value in gates.values()):
        raise ValueError("gate values must be boolean")
    if dict(gates) != expected_gates:
        raise ValueError("gate value mismatch")

    expected_passed = all(expected_gates.values())
    if type(report.get("passed")) is not bool or report.get("passed") != expected_passed:
        raise ValueError("passed value mismatch")

    invalid_equivalence = any(
        item["result_equivalence"]["status"] == "INVALID"
        for item in applications
    )
    has_samples = any(
        bool(arm["samples"])
        for item in applications
        for arm in item["arms"].values()
    )
    expected_status = (
        "INVALID"
        if invalid_equivalence
        else "MEASURED"
        if has_samples
        else "UNMEASURED"
    )
    if report["status"] != expected_status:
        raise ValueError("status value mismatch")


__all__ = [
    "ALGORITHM_DIGEST",
    "APPLICATIONS",
    "ARMS",
    "ArmMeasurement",
    "Runner",
    "BOOTSTRAP_REPLICATES",
    "DEFAULT_MEASURED_RUNS",
    "DEFAULT_WARMUPS",
    "FROZEN_WORKLOADS",
    "MUTATION_LOCK",
    "NATIVE_RATIO_LIMIT",
    "SCHEMA_VERSION",
    "algorithm_digest",
    "bootstrap_median_ci",
    "build_productive_runner_registry",
    "median_absolute_deviation",
    "mutation_lock_digest",
    "run_productive_performance",
    "summarize_samples",
    "validate_productive_performance_report",
    "workload_digest",
]
