"""Canonical, conservative evidence for release-gate measurements.

This module is intentionally an evidence *boundary*, not a benchmark.  A caller
must inject observations (or use the explicit collection helpers); absent inputs
are represented as ``unavailable`` and are never turned into zeroes or passes.
Every record is canonical JSON and digest-bound so that changing an input after
capture is detectable.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import platform
import resource
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from merlo.parallel_backends import BackendCapabilities, discover_capabilities
from merlo.performance_evidence import PerformanceEvidenceManifest, summarize_samples
from merlo.verification_metrics import VerificationMetricsReport
from merlo.work_stealing import WorkStealingResult

SCHEMA_VERSION = 2
CONTRACT = "merlo.frozen-evidence.v2"
MINIMUM_APPLICATION_BUILDS = 20
MINIMUM_SEMANTIC_EDIT_AUDITS = 300
METRIC_APPLICATION_BUILDS = "application_builds"
METRIC_SINGLE_CORE_NATIVE_RATIO = "single_core_native_ratio"
METRIC_MULTICORE_SCALING = "multicore_scaling"
METRIC_SUPPORTED_GPU_RATIO = "supported_gpu_ratio"
METRIC_AUTOMATIC_PROOF_CLOSURE = "automatic_proof_closure"
METRIC_MEMORY_SAFETY_CORPUS = "memory_safety_corpus"
METRIC_UNRELATED_SEMANTIC_EDIT_AUDIT = "unrelated_semantic_edit_audit"
METRIC_DETERMINISTIC_REPEATED_BUILDS = "deterministic_repeated_builds"
METRIC_REPAIR_ITERATION_REDUCTION = "repair_iteration_reduction"
METRIC_AI_CONTEXT_REDUCTION = "ai_context_reduction"
METRIC_BEND_COMPARISON = "bend_comparison"
BEND_PUBLIC_REPOSITORY = "https://github.com/HigherOrderCO/Bend.git"
BEND_PUBLIC_REVISION = "814453670d0e0d6777c1313c972764dba0491b7f"
FROZEN_APPLICATION_COHORT = (
    "automation",
    "json-cli",
    "ndjson",
    "csv",
    "grep",
    "network",
    "ffi",
    "capacity-ledger",
    "packages",
    "invoice-report",
    "access-log",
    "byte-stats",
    "inventory",
    "task-board",
    "tree-walk",
    "expense-report",
    "sensor-window",
    "shipping-batch",
    "access-policy",
    "invoice-summary",
)
 
 
REQUIRED_METRICS = (
    METRIC_APPLICATION_BUILDS,
    METRIC_SINGLE_CORE_NATIVE_RATIO,
    METRIC_MULTICORE_SCALING,
    METRIC_SUPPORTED_GPU_RATIO,
    METRIC_AUTOMATIC_PROOF_CLOSURE,
    METRIC_MEMORY_SAFETY_CORPUS,
    METRIC_UNRELATED_SEMANTIC_EDIT_AUDIT,
    METRIC_DETERMINISTIC_REPEATED_BUILDS,
    METRIC_REPAIR_ITERATION_REDUCTION,
    METRIC_AI_CONTEXT_REDUCTION,
    METRIC_BEND_COMPARISON,
)
_DIGEST_LENGTH = 64


class EvidenceStatus(str, Enum):
    MEASURED = "measured"
    UNAVAILABLE = "unavailable"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest_value(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("digest_text expects str")
    return digest_bytes(value.encode("utf-8"))


def digest_file(path: str | Path) -> str:
    return digest_bytes(Path(path).read_bytes())


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == _DIGEST_LENGTH and all(c in "0123456789abcdef" for c in value)


def _pairs(value: Mapping[str, str], code: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        raise ValueError(code)
    items = tuple(sorted((key, item) for key, item in value.items()))
    if any(not isinstance(key, str) or not key or not _is_digest(item) for key, item in items):
        raise ValueError(code)
    if len({key for key, _ in items}) != len(items):
        raise ValueError(code)
    return items


def _pair_dict(value: tuple[tuple[str, str], ...]) -> dict[str, str]:
    return dict(value)


def _samples(value: Iterable[int], code: str) -> tuple[int, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise ValueError(code)
    try:
        result = tuple(value)
    except TypeError as exc:
        raise ValueError(code) from exc
    if any(type(item) is not int or item < 0 for item in result):
        raise ValueError(code)
    return result


def _command(value: Iterable[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        raise ValueError("InvalidCommand")
    result = tuple(value)
    if not result or any(type(item) is not str or not item for item in result):
        raise ValueError("InvalidCommand")
    return result



_GPU_PROBE_KEYS = frozenset({"available", "reason", "provider", "version", "device", "runtime", "metadata"})
_GPU_PROVIDERS = frozenset({"cupy", "torch", "numba"})


def _gpu_unavailable(reason: str, *, provider: str | None = None) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "provider": provider,
        "version": None,
        "device": None,
        "runtime": None,
        "metadata": {},
    }


def _probe_gpu_provider(provider: str) -> dict[str, Any]:
    """Probe a real device, rather than treating an importable package as one."""
    if provider not in _GPU_PROVIDERS:
        return _gpu_unavailable(f"GPUUnavailable:GPUBackendUnsupported:{provider or 'unknown'}", provider=provider or None)
    try:
        module = importlib.import_module(provider)
        if provider == "torch":
            cuda = getattr(module, "cuda", None)
            if cuda is None or not bool(cuda.is_available()):
                return _gpu_unavailable("GPUUnavailable:GPUDeviceUnavailable:torch.cuda.is_available=false", provider=provider)
            count = int(cuda.device_count())
            if count < 1:
                return _gpu_unavailable("GPUUnavailable:GPUDeviceUnavailable:torch.device_count=0", provider=provider)
            device = str(cuda.get_device_name(0))
            version_obj = getattr(getattr(module, "version", None), "cuda", None) or getattr(getattr(module, "version", None), "hip", None)
            version = str(version_obj) if version_obj else "unknown"
            runtime = f"torch-cuda:{version}"
        elif provider == "cupy":
            runtime_api = module.cuda.runtime
            count = int(runtime_api.getDeviceCount())
            if count < 1:
                return _gpu_unavailable("GPUUnavailable:GPUDeviceUnavailable:cupy.device_count=0", provider=provider)
            properties = runtime_api.getDeviceProperties(0)
            raw_name = properties.get("name", b"unknown") if isinstance(properties, Mapping) else "unknown"
            device = raw_name.decode(errors="replace") if isinstance(raw_name, bytes) else str(raw_name)
            version = str(runtime_api.runtimeGetVersion())
            runtime = f"cupy-cuda:{version}"
        else:
            cuda = module.cuda
            if not bool(cuda.is_available()):
                return _gpu_unavailable("GPUUnavailable:GPUDeviceUnavailable:numba.cuda.is_available=false", provider=provider)
            device_obj = cuda.get_current_device()
            device = str(getattr(device_obj, "name", "unknown"))
            version = str(getattr(module, "__version__", "unknown"))
            runtime = f"numba-cuda:{version}"
            count = 1
        return {
            "available": True,
            "reason": None,
            "provider": provider,
            "version": version,
            "device": device,
            "runtime": runtime,
            "metadata": {"host": platform.platform(), "device_count": count},
        }
    except (ImportError, ModuleNotFoundError):
        return _gpu_unavailable(f"GPUUnavailable:GPUBackendImportUnavailable:{provider}", provider=provider)
    except Exception as exc:
        return _gpu_unavailable(f"GPUUnavailable:GPUProbeError:{provider}:{type(exc).__name__}", provider=provider)


def detect_gpu_backend(
    capabilities: BackendCapabilities | Mapping[str, Any] | None = None,
    *,
    backend_probe: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an explicit device/runtime snapshot for the GPU metric.

    Host discovery is conservative: a package import alone is not a supported
    device.  ``backend_probe`` is a deterministic fixture seam for supported
    adapters and is never consulted when the capability says unavailable.
    """
    snapshot = discover_capabilities(capabilities)
    capability = snapshot.for_target("gpu")
    if not capability.available:
        return _gpu_unavailable(f"GPUUnavailable:{capability.reason or 'CapabilityUnavailable'}", provider=capability.provider)
    if backend_probe is not None:
        if not isinstance(backend_probe, Mapping) or set(backend_probe) - _GPU_PROBE_KEYS:
            raise ValueError("GPUProbeSchemaMismatch")
        if type(backend_probe.get("available", True)) is not bool:
            raise ValueError("GPUProbeSchemaMismatch")
        if not backend_probe.get("available", True):
            reason = backend_probe.get("reason") or "CapabilityUnavailable"
            return _gpu_unavailable(f"GPUUnavailable:{reason}", provider=backend_probe.get("provider") or capability.provider)
        metadata = backend_probe.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("GPUProbeSchemaMismatch")
        return {
            "available": True,
            "reason": None,
            "provider": backend_probe.get("provider") or capability.provider or "fixture",
            "version": backend_probe.get("version") or capability.version or "unknown",
            "device": backend_probe.get("device") or capability.metadata.get("device", "unknown"),
            "runtime": backend_probe.get("runtime") or capability.metadata.get("runtime", "unknown"),
            "metadata": dict(metadata),
        }
    if capabilities is None:
        return _probe_gpu_provider(capability.provider or "")
    metadata = capability.metadata
    return {
        "available": True,
        "reason": None,
        "provider": capability.provider or "explicit",
        "version": capability.version or "unknown",
        "device": metadata.get("device", "unknown"),
        "runtime": metadata.get("runtime", "unknown"),
        "metadata": dict(metadata),
    }
@dataclass(frozen=True, slots=True)
class MetricMeasurement:
    """One digest-bound metric observation.

    ``samples`` are the untouched integer observations supplied by the caller.
    Ratio fields preserve an exact numerator/denominator in addition to those
    samples; no floating point value is stored or inferred.
    """

    metric_id: str
    status: EvidenceStatus | str
    reason: str | None
    command: tuple[str, ...]
    command_digest: str
    config_digest: str
    config_json: str
    source_digests: tuple[tuple[str, str], ...]
    environment_digest: str
    environment: tuple[tuple[str, str], ...]
    artifact_digests: tuple[tuple[str, str], ...]
    samples: tuple[int, ...]
    ratio_numerator: int | None = None
    ratio_denominator: int | None = None
    schema_version: int = SCHEMA_VERSION
    contract: str = CONTRACT
    record_digest: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.contract != CONTRACT:
            raise ValueError("FrozenEvidenceSchemaMismatch")
        if not isinstance(self.metric_id, str) or not self.metric_id:
            raise ValueError("InvalidMetricId")
        try:
            status = EvidenceStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError("InvalidEvidenceStatus") from exc
        object.__setattr__(self, "status", status)
        command = _command(self.command)
        object.__setattr__(self, "command", command)
        if self.command_digest != digest_value(list(command)):
            raise ValueError("CommandDigestMismatch")
        try:
            config = json.loads(self.config_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("InvalidConfig") from exc
        if _canonical(config) != self.config_json or self.config_digest != digest_value(config):
            raise ValueError("ConfigDigestMismatch")
        if not _is_digest(self.environment_digest):
            raise ValueError("InvalidEnvironmentDigest")
        environment = tuple(self.environment)
        if (
            environment != tuple(sorted(environment))
            or len({key for key, _ in environment}) != len(environment)
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in environment)
        ):
            raise ValueError("InvalidEnvironment")
        if self.environment_digest != digest_value(dict(environment)):
            raise ValueError("EnvironmentDigestMismatch")
        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "source_digests", _pairs(dict(self.source_digests), "InvalidSourceDigests"))
        object.__setattr__(self, "artifact_digests", _pairs(dict(self.artifact_digests), "InvalidArtifactDigests"))
        values = _samples(self.samples, "InvalidSamples")
        object.__setattr__(self, "samples", values)
        if status is EvidenceStatus.MEASURED and not values:
            raise ValueError("MeasuredEvidenceNeedsSamples")
        if status is EvidenceStatus.UNAVAILABLE:
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ValueError("UnavailableEvidenceNeedsReason")
            if values:
                raise ValueError("UnavailableEvidenceCannotHaveSamples")
        elif self.reason is not None and (not isinstance(self.reason, str) or not self.reason.strip()):
            raise ValueError("InvalidEvidenceReason")
        if (self.ratio_numerator is None) != (self.ratio_denominator is None):
            raise ValueError("RatioPairRequired")
        if self.ratio_numerator is not None:
            if type(self.ratio_numerator) is not int or (
                self.ratio_numerator < 0
                and self.metric_id != METRIC_REPAIR_ITERATION_REDUCTION
            ):
                raise ValueError("InvalidRatioNumerator")
            if type(self.ratio_denominator) is not int or self.ratio_denominator < 1:
                raise ValueError("InvalidRatioDenominator")
        expected = digest_value(self._payload())
        if self.record_digest and self.record_digest != expected:
            raise ValueError("MetricDigestMismatch")
        object.__setattr__(self, "record_digest", expected)

    @classmethod
    def create(
        cls,
        metric_id: str,
        *,
        status: EvidenceStatus | str,
        command: Sequence[str],
        config: Any,
        source_digests: Mapping[str, str] | None = None,
        environment: Mapping[str, str] | None = None,
        artifact_digests: Mapping[str, str] | None = None,
        samples: Iterable[int] = (),
        reason: str | None = None,
        ratio: tuple[int, int] | None = None,
    ) -> "MetricMeasurement":
        command_tuple = _command(command)
        if environment is None:
            environment = {}
        if not isinstance(environment, Mapping) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items()):
            raise ValueError("InvalidEnvironment")
        config_json = _canonical(config)
        environment_items = tuple(sorted(environment.items()))
        return cls(
            metric_id,
            status,
            reason,
            command_tuple,
            digest_value(list(command_tuple)),
            digest_value(config),
            config_json,
            tuple(sorted((source_digests or {}).items())),
            digest_value(dict(environment_items)),
            environment_items,
            tuple(sorted((artifact_digests or {}).items())),
            tuple(samples),
            *(ratio or (None, None)),
        )

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "metric_id": self.metric_id,
            "status": self.status.value,
            "reason": self.reason,
            "command": list(self.command),
            "command_digest": self.command_digest,
            "config_digest": self.config_digest,
            "config": json.loads(self.config_json),
            "source_digests": _pair_dict(self.source_digests),
            "environment_digest": self.environment_digest,
            "environment": dict(self.environment),
            "artifact_digests": _pair_dict(self.artifact_digests),
            "samples": list(self.samples),
            "ratio_numerator": self.ratio_numerator,
            "ratio_denominator": self.ratio_denominator,
        }

    @property
    def digest(self) -> str:
        return self.record_digest

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "record_digest": self.record_digest}

    def to_json(self) -> str:
        return _canonical(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MetricMeasurement":
        if not isinstance(value, Mapping):
            raise ValueError("InvalidMetricMeasurement")
        expected = set(
            cls.create(
                "x",
                status=EvidenceStatus.UNAVAILABLE,
                reason="x",
                command=("x",),
                config={},
            )._payload()
        )
        if set(value) != expected | {"record_digest"}:
            raise ValueError("MetricMeasurementSchemaMismatch")
        try:
            return cls(
                value["metric_id"], value["status"], value["reason"], tuple(value["command"]), value["command_digest"],
                value["config_digest"], _canonical(value["config"]), tuple(sorted(value["source_digests"].items())),
                value["environment_digest"], tuple(sorted(value["environment"].items())),
                tuple(sorted(value["artifact_digests"].items())), tuple(value["samples"]), value["ratio_numerator"],
                value["ratio_denominator"], value["schema_version"], value["contract"], value["record_digest"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ValueError):
                raise
            raise ValueError("MetricMeasurementSchemaMismatch") from exc

    @classmethod
    def from_json(cls, value: str) -> "MetricMeasurement":
        try:
            parsed = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("InvalidMetricMeasurementJSON") from exc
        return cls.from_dict(parsed)


@dataclass(frozen=True, slots=True)
class ApplicationBuildObservation:
    application_id: str
    command: tuple[str, ...]
    source_digest: str
    artifact_digest: str
    sample: int = 1

    def __post_init__(self) -> None:
        if not self.application_id or not _is_digest(self.source_digest) or not _is_digest(self.artifact_digest):
            raise ValueError("InvalidApplicationBuildObservation")
        object.__setattr__(self, "command", _command(self.command))
        if type(self.sample) is not int or self.sample < 0:
            raise ValueError("InvalidApplicationBuildSample")

@dataclass(frozen=True, slots=True)
class SemanticEditAuditObservation:
    edit_id: str
    operation_digest: str
    before_digest: str
    after_digest: str
    allowed_identities: tuple[str, ...]
    changed_identities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.edit_id, str) or not self.edit_id:
            raise ValueError("InvalidSemanticEditId")
        for digest in (self.operation_digest, self.before_digest, self.after_digest):
            if not _is_digest(digest):
                raise ValueError("InvalidSemanticEditDigest")
        allowed = tuple(self.allowed_identities)
        changed = tuple(self.changed_identities)
        if any(not isinstance(item, str) or not item for item in (*allowed, *changed)):
            raise ValueError("InvalidSemanticEditIdentity")
        if len(set(allowed)) != len(allowed) or len(set(changed)) != len(changed):
            raise ValueError("DuplicateSemanticEditIdentity")
        object.__setattr__(self, "allowed_identities", tuple(sorted(allowed)))
        object.__setattr__(self, "changed_identities", tuple(sorted(changed)))

    @property
    def unrelated_identities(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.changed_identities) - set(self.allowed_identities)))

    @classmethod
    def from_value(
        cls,
        value: "SemanticEditAuditObservation | Mapping[str, Any]",
    ) -> "SemanticEditAuditObservation":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("SemanticEditArtifactsRequired")
        try:
            return cls(
                value["edit_id"],
                value["operation_digest"],
                value["before_digest"],
                value["after_digest"],
                tuple(value["allowed_identities"]),
                tuple(value["changed_identities"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("InvalidSemanticEditObservation") from exc


@dataclass(frozen=True, slots=True)
class FrozenEvidenceReport:
    metrics: tuple[MetricMeasurement, ...]
    required_application_builds: int = MINIMUM_APPLICATION_BUILDS
    schema_version: int = SCHEMA_VERSION
    contract: str = CONTRACT
    record_digest: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.contract != CONTRACT:
            raise ValueError("FrozenEvidenceSchemaMismatch")
        if type(self.required_application_builds) is not int or self.required_application_builds < 1:
            raise ValueError("InvalidApplicationBuildMinimum")
        metrics = tuple(self.metrics)
        if any(not isinstance(item, MetricMeasurement) for item in metrics):
            raise ValueError("MetricTypeMismatch")
        ids = tuple(item.metric_id for item in metrics)
        if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
            raise ValueError("MetricsNotCanonical")
        if ids != tuple(sorted(REQUIRED_METRICS)):
            raise ValueError("RequiredMetricsIncomplete")
        app = next(item for item in metrics if item.metric_id == METRIC_APPLICATION_BUILDS)
        if app.status is EvidenceStatus.MEASURED and len(app.samples) < self.required_application_builds:
            raise ValueError("ApplicationBuildMinimumNotMet")
        gpu = next(item for item in metrics if item.metric_id == METRIC_SUPPORTED_GPU_RATIO)
        if gpu.status is EvidenceStatus.UNAVAILABLE and gpu.samples:
            raise ValueError("UnavailableGPUEvidenceHasSamples")
        expected = digest_value(self._payload())
        if self.record_digest and self.record_digest != expected:
            raise ValueError("FrozenEvidenceDigestMismatch")
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(self, "record_digest", expected)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "required_application_builds": self.required_application_builds,
            "metrics": [item.to_dict() for item in self.metrics],
        }

    @property
    def digest(self) -> str:
        return self.record_digest

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "record_digest": self.record_digest}

    def to_json(self) -> str:
        return _canonical(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FrozenEvidenceReport":
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "contract", "required_application_builds", "metrics", "record_digest"}:
            raise ValueError("FrozenEvidenceSchemaMismatch")
        if type(value["metrics"]) is not list:
            raise ValueError("FrozenEvidenceSchemaMismatch")
        metrics = tuple(MetricMeasurement.from_dict(item) for item in value["metrics"])
        return cls(metrics, value["required_application_builds"], value["schema_version"], value["contract"], value["record_digest"])

    @classmethod
    def from_json(cls, value: str) -> "FrozenEvidenceReport":
        try:
            parsed = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("InvalidFrozenEvidenceJSON") from exc
        return cls.from_dict(parsed)

    def validate_current(
        self,
        *,
        source_digests: Mapping[str, str] | None = None,
        artifact_digests: Mapping[str, str] | None = None,
        config_digests: Mapping[str, str] | None = None,
        environment_digest: str | None = None,
    ) -> None:
        """Reject evidence whose captured inputs no longer match current inputs."""
        for item in self.metrics:
            if source_digests is not None and _pair_dict(item.source_digests) != dict(sorted(source_digests.items())):
                raise ValueError(f"StaleEvidenceSource:{item.metric_id}")
            if artifact_digests is not None and _pair_dict(item.artifact_digests) != dict(sorted(artifact_digests.items())):
                raise ValueError(f"StaleEvidenceArtifact:{item.metric_id}")
            if config_digests is not None and item.metric_id in config_digests and item.config_digest != config_digests[item.metric_id]:
                raise ValueError(f"StaleEvidenceConfig:{item.metric_id}")
            if environment_digest is not None and item.environment_digest != environment_digest:
                raise ValueError(f"StaleEvidenceEnvironment:{item.metric_id}")


_SANITIZER_FLAGS = {"asan": "address", "ubsan": "undefined", "lsan": "leak"}
_SANITIZER_MARKERS = (
    "addresssanitizer",
    "undefinedbehaviorsanitizer",
    "leaksanitizer",
    "runtime error:",
)


@dataclass(frozen=True, slots=True)
class MemorySafetyCorpusCase:
    """A declared native source fixture and its observable exit contract."""

    case_id: str
    source: Path
    arguments: tuple[str, ...] = ()
    expected_exit_code: int = 0
    expected_stdout: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("InvalidMemorySafetyCaseId")
        object.__setattr__(self, "source", Path(self.source))
        object.__setattr__(self, "arguments", _command(self.arguments) if self.arguments else ())
        if type(self.expected_exit_code) is not int:
            raise ValueError("InvalidExpectedExitCode")

    @classmethod
    def from_value(cls, value: "MemorySafetyCorpusCase | Mapping[str, Any]") -> "MemorySafetyCorpusCase":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("InvalidMemorySafetyCase")
        source = value.get("source", value.get("path"))
        case_id = value.get("case_id", value.get("id"))
        if source is None or case_id is None:
            raise ValueError("InvalidMemorySafetyCase")
        return cls(
            str(case_id), Path(source),
            tuple(value.get("arguments", value.get("argv", ()))),
            int(value.get("expected_exit_code", value.get("returncode", 0))),
            value.get("expected_stdout"),
        )


@dataclass(frozen=True, slots=True)
class MemorySafetyRun:
    case_id: str
    sanitizer: str
    toolchain: str | None
    status: EvidenceStatus
    compile_command: tuple[str, ...]
    run_command: tuple[str, ...]
    config: Mapping[str, Any]
    environment: tuple[tuple[str, str], ...]
    source_digests: tuple[tuple[str, str], ...]
    artifact_digests: tuple[tuple[str, str], ...]
    stdout: bytes = b""
    stderr: bytes = b""
    exit_code: int | None = None
    elapsed_ns: int | None = None
    timed_out: bool = False
    failure: str | None = None
    reason: str | None = None

    @property
    def clean(self) -> bool:
        return self.status is EvidenceStatus.MEASURED and self.failure is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id, "sanitizer": self.sanitizer,
            "toolchain": self.toolchain, "status": self.status.value,
            "compile_command": list(self.compile_command),
            "run_command": list(self.run_command), "config": dict(self.config),
            "environment": dict(self.environment),
            "source_digests": dict(self.source_digests),
            "artifact_digests": dict(self.artifact_digests),
            "stdout_b64": base64.b64encode(self.stdout).decode("ascii"),
            "stderr_b64": base64.b64encode(self.stderr).decode("ascii"),
            "stdout_digest": digest_bytes(self.stdout),
            "stderr_digest": digest_bytes(self.stderr),
            "exit_code": self.exit_code, "elapsed_ns": self.elapsed_ns,
            "timed_out": self.timed_out, "failure": self.failure,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class MemorySafetyCorpusResult:
    runs: tuple[MemorySafetyRun, ...]
    status: EvidenceStatus
    reason: str | None
    config: Mapping[str, Any]
    environment: tuple[tuple[str, str], ...]
    source_digests: tuple[tuple[str, str], ...]
    artifact_digests: tuple[tuple[str, str], ...]
    record_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_digest", digest_value(self._payload()))

    @property
    def samples(self) -> tuple[int, ...]:
        return tuple(1 for item in self.runs if item.status is EvidenceStatus.MEASURED)

    @property
    def failures(self) -> tuple[MemorySafetyRun, ...]:
        return tuple(item for item in self.runs if item.status is EvidenceStatus.MEASURED and not item.clean)

    def _payload(self) -> dict[str, Any]:
        return {
            "runs": [item.to_dict() for item in self.runs],
            "status": self.status.value, "reason": self.reason,
            "config": dict(self.config), "environment": dict(self.environment),
            "source_digests": dict(self.source_digests),
            "artifact_digests": dict(self.artifact_digests),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "record_digest": self.record_digest}

    def to_json(self) -> str:
        return _canonical(self.to_dict())


def _child_limits(limits: Mapping[str, int]) -> None:
    """Apply limits in the child only; parent process limits remain unchanged."""
    table = {
        "cpu_seconds": resource.RLIMIT_CPU,
        "address_space_bytes": resource.RLIMIT_AS,
        "file_size_bytes": resource.RLIMIT_FSIZE,
        "processes": resource.RLIMIT_NPROC,
    }
    for key, resource_kind in table.items():
        value = limits.get(key)
        if value is not None:
            value = int(value)
            if value < 1:
                raise ValueError("InvalidResourceLimit")
            resource.setrlimit(resource_kind, (value, value))


def _run_limited(
    command: Sequence[str], *, environment: Mapping[str, str], timeout: float,
    limits: Mapping[str, int],
) -> tuple[subprocess.CompletedProcess[bytes] | None, bool, int | None]:
    started = time.monotonic_ns()
    try:
        result = subprocess.run(
            list(command), capture_output=True, check=False, shell=False,
            env=dict(environment), timeout=timeout,
            preexec_fn=lambda: _child_limits(limits),
        )
        return result, False, time.monotonic_ns() - started
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, bytes) else (exc.stdout or "").encode()
        stderr = exc.stderr if isinstance(exc.stderr, bytes) else (exc.stderr or "").encode()
        return subprocess.CompletedProcess(list(command), None, stdout, stderr), True, time.monotonic_ns() - started
    except (OSError, ValueError):
        return None, False, time.monotonic_ns() - started


def run_memory_safety_corpus(
    corpus: Sequence[MemorySafetyCorpusCase | Mapping[str, Any]] | str | Path,
    *,
    root: str | Path = ".",
    sanitizers: Sequence[str] = ("asan", "ubsan", "lsan"),
    toolchains: Mapping[str, str] | str | None = None,
    timeout_seconds: float = 30.0,
    resource_limits: Mapping[str, int] | None = None,
    config: Any = None,
    environment: Mapping[str, str] | None = None,
    output_dir: str | Path | None = None,
) -> MemorySafetyCorpusResult:
    """Run the declared C corpus deterministically under supported sanitizers."""
    base = Path(root).resolve()
    env = dict(os.environ if environment is None else environment)
    env.setdefault("LC_ALL", "C")
    env.setdefault("TZ", "UTC")
    names = tuple(dict.fromkeys(str(item) for item in sanitizers))
    if isinstance(corpus, (str, Path)):
        directory = Path(corpus)
        directory = directory if directory.is_absolute() else base / directory
        cases = (
            tuple(
                MemorySafetyCorpusCase(
                    path.relative_to(directory).with_suffix("").as_posix(),
                    path,
                )
                for path in sorted(directory.rglob("*.c"))
            )
            if directory.is_dir()
            else ()
        )
    else:
        cases = tuple(sorted((MemorySafetyCorpusCase.from_value(item) for item in corpus), key=lambda item: item.case_id))
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("DuplicateMemorySafetyCaseId")
    sources = {}
    for case in cases:
        path = case.source if case.source.is_absolute() else base / case.source
        if path.is_file():
            sources[case.case_id] = digest_file(path)
    limits = dict(resource_limits or {"cpu_seconds": max(1, int(timeout_seconds)), "address_space_bytes": 2 * 1024 * 1024 * 1024, "file_size_bytes": 64 * 1024 * 1024, "processes": 64})
    compiler_limits = {
        key: value
        for key, value in limits.items()
        if key in {"cpu_seconds", "file_size_bytes"}
    }
    sanitizer_limits = {
        key: value
        for key, value in limits.items()
        if key not in {"address_space_bytes", "processes"}
    }
    run_config = {
        **(dict(config) if isinstance(config, Mapping) else {"runner_config": config}),
        "sanitizers": list(names), "timeout_seconds": timeout_seconds,
        "resource_limits": limits, "case_ids": [case.case_id for case in cases],
        "compiler_resource_limits": compiler_limits,
        "sanitizer_resource_limits": sanitizer_limits,
    }
    if timeout_seconds <= 0:
        raise ValueError("InvalidMemorySafetyTimeout")
    if not names:
        return MemorySafetyCorpusResult((), EvidenceStatus.UNAVAILABLE, "NoSanitizersDeclared", run_config, tuple(sorted(env.items())), tuple(sorted(sources.items())), ())
    if not cases:
        return MemorySafetyCorpusResult((), EvidenceStatus.UNAVAILABLE, "MemorySafetyCorpusEmpty", run_config, tuple(sorted(env.items())), (), ())
    managed = tempfile.TemporaryDirectory(prefix="merlo-memory-safety-") if output_dir is None else None
    work = Path(managed.name) if managed is not None else Path(output_dir)
    work.mkdir(parents=True, exist_ok=True)
    runs: list[MemorySafetyRun] = []
    artifacts: dict[str, str] = {}
    unavailable_sanitizers: dict[str, str] = {}
    try:
        for sanitizer in names:
            flag = _SANITIZER_FLAGS.get(sanitizer)
            selected = toolchains.get(sanitizer, toolchains.get("default")) if isinstance(toolchains, Mapping) else toolchains
            compiler = shutil.which(selected) if selected else (shutil.which(env.get("CC", "")) if env.get("CC") else None)
            compiler = compiler or (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc"))
            if flag is None:
                unavailable_sanitizers[sanitizer] = "UnsupportedSanitizer"
                continue
            if compiler is None:
                unavailable_sanitizers[sanitizer] = "ToolchainUnavailable"
                continue
            probe_source = work / f".probe-{sanitizer}.c"
            probe_binary = work / f".probe-{sanitizer}"
            probe_source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            probe_command = (compiler, "-std=c11", "-O1", f"-fsanitize={flag}", str(probe_source), "-o", str(probe_binary))
            probe, probe_timeout, _ = _run_limited(probe_command, environment=env, timeout=min(timeout_seconds, 30.0), limits=compiler_limits)
            if probe is None:
                unavailable_sanitizers[sanitizer] = "SanitizerProbeLaunchFailed"
                continue
            if probe_timeout:
                unavailable_sanitizers[sanitizer] = "SanitizerProbeTimedOut"
                continue
            if probe.returncode != 0:
                unavailable_sanitizers[sanitizer] = "SanitizerUnsupportedByToolchain"
                continue
            for case in cases:
                source = case.source if case.source.is_absolute() else base / case.source
                destination = work / sanitizer / case.case_id
                destination.parent.mkdir(parents=True, exist_ok=True)
                compile_command = (compiler, "-std=c11", "-O1", "-g", "-fno-omit-frame-pointer", "-fno-sanitize-recover=all", f"-fsanitize={flag}", str(source), "-o", str(destination))
                run_command = (str(destination), *case.arguments)
                run_env = dict(env)
                run_env.update({"ASAN_OPTIONS": "halt_on_error=1:detect_leaks=1", "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1", "LSAN_OPTIONS": "exitcode=23:report_objects=0"})
                compile_result, compile_timeout, compile_elapsed = _run_limited(compile_command, environment=run_env, timeout=timeout_seconds, limits=compiler_limits)
                compile_stdout = compile_result.stdout if compile_result else b""
                compile_stderr = compile_result.stderr if compile_result else b""
                key = f"{sanitizer}:{case.case_id}"
                case_sources = {key + ":source": sources[case.case_id]} if case.case_id in sources else {}
                case_artifacts = {key + ":compile-stdout": digest_bytes(compile_stdout), key + ":compile-stderr": digest_bytes(compile_stderr)}
                if destination.is_file():
                    case_artifacts[key + ":binary"] = digest_file(destination)
                if compile_result is None or compile_timeout or compile_result.returncode != 0:
                    failure = "compile_timeout" if compile_timeout else ("compile_failed" if compile_result else "compile_launch_failed")
                    runs.append(MemorySafetyRun(case.case_id, sanitizer, compiler, EvidenceStatus.MEASURED, compile_command, run_command, run_config, tuple(sorted(run_env.items())), tuple(sorted(case_sources.items())), tuple(sorted(case_artifacts.items())), compile_stdout, compile_stderr, compile_result.returncode if compile_result else None, compile_elapsed, compile_timeout, failure))
                    artifacts.update(case_artifacts)
                    continue
                result, timed_out, elapsed = _run_limited(run_command, environment=run_env, timeout=timeout_seconds, limits=sanitizer_limits)
                stdout = result.stdout if result else b""
                stderr = result.stderr if result else b""
                case_artifacts.update({key + ":stdout": digest_bytes(stdout), key + ":stderr": digest_bytes(stderr)})
                artifacts.update(case_artifacts)
                error_text = stderr.decode("utf-8", errors="replace").lower()
                failure = "launch_failed" if result is None else ("timeout" if timed_out else None)
                if failure is None and result.returncode != case.expected_exit_code:
                    failure = "exit_code_mismatch"
                if failure is None and any(marker in error_text for marker in _SANITIZER_MARKERS):
                    failure = "sanitizer_violation"
                if failure is None and case.expected_stdout is not None and stdout.decode("utf-8", errors="replace") != case.expected_stdout:
                    failure = "stdout_mismatch"
                runs.append(MemorySafetyRun(case.case_id, sanitizer, compiler, EvidenceStatus.MEASURED, compile_command, run_command, run_config, tuple(sorted(run_env.items())), tuple(sorted(case_sources.items())), tuple(sorted(case_artifacts.items())), stdout, stderr, result.returncode if result else None, elapsed, timed_out, failure))
    finally:
        if managed is not None:
            managed.cleanup()
    if unavailable_sanitizers:
        run_config["unavailable_sanitizers"] = dict(sorted(unavailable_sanitizers.items()))
        return MemorySafetyCorpusResult(
            tuple(runs),
            EvidenceStatus.UNAVAILABLE,
            f"MemorySafetySanitizersUnavailable:{_canonical(unavailable_sanitizers)}",
            run_config,
            tuple(sorted(env.items())),
            tuple(sorted(sources.items())),
            tuple(sorted(artifacts.items())),
        )
    if not runs:
        return MemorySafetyCorpusResult((), EvidenceStatus.UNAVAILABLE, "SanitizerOrToolchainUnavailable", run_config, tuple(sorted(env.items())), tuple(sorted(sources.items())), tuple(sorted(artifacts.items())))
    return MemorySafetyCorpusResult(tuple(runs), EvidenceStatus.MEASURED, None, run_config, tuple(sorted(env.items())), tuple(sorted(sources.items())), tuple(sorted(artifacts.items())))


def derive_repair_iteration_metric(
    histories: Any,
    *,
    command: Sequence[str] = ("merlo", "frozen-evidence", "repair-iterations"),
    config: Any = None,
    environment: Mapping[str, str] | None = None,
) -> MetricMeasurement:
    """Derive exact repair reduction from paired, observed provider histories.

    A history is admissible only when it is a digest-validated ``AIRawTaskRecord``
    from the same provider/model/revision and deterministic oracle.  Exactly one
    ``semantic`` and one ``text`` record must exist for each task, with matching
    task/prompt/dataset hashes.  ``repair_iterations`` must be present in both the
    serialized raw response and the record.  Failed histories remain in the raw
    artifact digest and their explicit iteration counts remain in ``samples``;
    they do not enter the accepted-result aggregate.  Only pairs where both
    oracle results are successful contribute.  Missing, stale, duplicate, or
    unpaired histories are rejected rather than interpreted as zero.
    """
    from merlo.ai_evidence import AIRawTaskRecord, AIEvidenceReport

    if isinstance(histories, AIEvidenceReport):
        histories = AIEvidenceReport.from_dict(histories.to_dict())
        records = histories.records
    elif isinstance(histories, Mapping) and "records" in histories:
        records = AIEvidenceReport.from_dict(histories).records
    else:
        try:
            records = tuple(histories)
        except TypeError as exc:
            raise ValueError("RepairHistorySchemaMismatch") from exc
    if not records:
        return MetricMeasurement.create(
            metric_id=METRIC_REPAIR_ITERATION_REDUCTION,
            status=EvidenceStatus.UNAVAILABLE,
            reason="NoRepairHistories",
            command=command,
            config=config if config is not None else {},
            environment=environment or {},
        )
    normalized: list[AIRawTaskRecord] = []
    for item in records:
        try:
            normalized.append(
                item
                if isinstance(item, AIRawTaskRecord)
                else AIRawTaskRecord.from_dict(item)
            )
            # Reconstruct even an object to catch mutation after capture.
            normalized[-1] = AIRawTaskRecord.from_dict(normalized[-1].to_dict())
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError("RepairHistoryInvalidRecord") from exc
    if any(item.status != "measured" for item in normalized):
        raise ValueError("RepairHistoryNotMeasured")
    if len({item.run_id for item in normalized}) != len(normalized):
        raise ValueError("RepairHistoryDuplicateRun")
    by_task: dict[str, dict[str, AIRawTaskRecord]] = {}
    for item in normalized:
        if item.arm not in ("semantic", "text"):
            raise ValueError("RepairHistoryInvalidArm")
        raw = dict(item.raw)
        if type(raw.get("repair_iterations")) is not int or raw["repair_iterations"] != item.repair_iterations:
            raise ValueError("RepairHistoryMissingRawIterations")
        oracle_result = raw.get("oracle_passed", raw.get("success"))
        if type(oracle_result) is not bool or oracle_result != item.success:
            raise ValueError("RepairHistoryOracleMismatch")
        task = by_task.setdefault(item.task_id, {})
        if item.arm in task:
            raise ValueError("RepairHistoryDuplicateArm")
        task[item.arm] = item
    if any(set(arms) != {"semantic", "text"} for arms in by_task.values()):
        raise ValueError("RepairHistoryUnpaired")
    first = normalized[0]
    identity = (first.provider, first.model, first.revision)
    oracle = first.success_oracle
    for pair in by_task.values():
        for item in pair.values():
            if (item.provider, item.model, item.revision) != identity:
                raise ValueError("RepairHistoryProviderMismatch")
            if item.success_oracle != oracle:
                raise ValueError("RepairHistoryOracleMismatch")
        semantic, text = pair["semantic"], pair["text"]
        frozen = (semantic.prompt_hash, semantic.task_hash, semantic.dataset_hash)
        if (text.prompt_hash, text.task_hash, text.dataset_hash) != frozen:
            raise ValueError("RepairHistoryStaleTask")
    ordered = tuple(
        (task_id, by_task[task_id]["semantic"], by_task[task_id]["text"])
        for task_id in sorted(by_task)
    )
    samples = tuple(
        count
        for _, semantic, text in ordered
        for count in (semantic.repair_iterations, text.repair_iterations)
    )
    eligible = tuple(
        (semantic, text)
        for _, semantic, text in ordered
        if semantic.success and text.success
    )
    raw_payload = [item.to_dict() for _, semantic, text in ordered for item in (semantic, text)]
    artifacts = {
        f"{task_id}:{arm}": item.digest
        for task_id, semantic, text in ordered
        for arm, item in (("semantic", semantic), ("text", text))
    }
    artifacts["raw-histories"] = digest_value(raw_payload)
    sources = {
        f"{task_id}:{column}": value
        for task_id, semantic, _ in ordered
        for column, value in (
            ("prompt", semantic.prompt_hash),
            ("task", semantic.task_hash),
            ("dataset", semantic.dataset_hash),
        )
    }
    failures = {
        f"{task_id}:{arm}": (
            str(item.raw.get("failure") or item.raw.get("error") or "oracle_rejected")
        )
        for task_id, semantic, text in ordered
        for arm, item in (("semantic", semantic), ("text", text))
        if not item.success
    }
    rule_config = {
        "oracle": oracle,
        "arms": ["semantic", "text"],
        "accepted_pair_rule": "both_oracle_success",
        "failed_history_rule": "raw_only",
        "paired_tasks": len(ordered),
        "eligible_tasks": len(eligible),
        "failed_tasks": len(ordered) - len(eligible),
        "failures": failures,
        "caller_config": config if config is not None else {},
    }
    if not eligible:
        return MetricMeasurement.create(
            metric_id=METRIC_REPAIR_ITERATION_REDUCTION,
            status=EvidenceStatus.UNAVAILABLE,
            reason="NoPairedSuccessfulHistories",
            command=command,
            config=rule_config,
            source_digests=sources,
            environment=environment or {},
            artifact_digests=artifacts,
        )
    semantic_total = sum(item.repair_iterations for item, _ in eligible)
    text_total = sum(item.repair_iterations for _, item in eligible)
    if semantic_total == 0 or text_total == 0:
        raise ValueError("RepairHistoryAcceptedIterationMissing")
    return MetricMeasurement.create(
        metric_id=METRIC_REPAIR_ITERATION_REDUCTION,
        status=EvidenceStatus.MEASURED,
        command=command,
        config=rule_config,
        source_digests=sources,
        environment=environment or {},
        artifact_digests=artifacts,
        samples=samples,
        ratio=(text_total - semantic_total, text_total),
    )


class FrozenEvidenceRunner:
    """Collect available measurements while preserving unavailable gates honestly."""

    def __init__(self, root: str | Path = ".", *, applications: Sequence[str | Path] | None = None, command: Sequence[str] = ("merlo", "frozen-evidence"), config: Any = None, environment: Mapping[str, str] | None = None) -> None:
        self.root = Path(root).resolve()
        self.applications = tuple(Path(item).resolve() for item in applications) if applications is not None else self._discover_applications()
        self.command = _command(command)
        self.config = {} if config is None else config
        self.environment = dict(environment if environment is not None else {key: value for key, value in os.environ.items() if isinstance(value, str)})

    def _discover_applications(self) -> tuple[Path, ...]:
        examples = self.root / "examples"
        return tuple(examples / name for name in FROZEN_APPLICATION_COHORT)

    def _base_kwargs(self, metric_id: str, *, source_digests: Mapping[str, str] | None = None, artifact_digests: Mapping[str, str] | None = None) -> dict[str, Any]:
        return {"metric_id": metric_id, "command": self.command, "config": self.config, "source_digests": source_digests or {}, "environment": self.environment, "artifact_digests": artifact_digests or {}}

    def unavailable(self, metric_id: str, reason: str) -> MetricMeasurement:
        return MetricMeasurement.create(**self._base_kwargs(metric_id), status=EvidenceStatus.UNAVAILABLE, reason=reason)

    def ratio_metric(self, metric_id: str, samples: Iterable[int] | None, ratio: tuple[int, int] | None, *, reason: str, source_digests: Mapping[str, str] | None = None, artifact_digests: Mapping[str, str] | None = None) -> MetricMeasurement:
        values = tuple(samples or ())
        if not values or ratio is None:
            return MetricMeasurement.create(**self._base_kwargs(metric_id, source_digests=source_digests, artifact_digests=artifact_digests), status=EvidenceStatus.UNAVAILABLE, reason=reason)
        return MetricMeasurement.create(**self._base_kwargs(metric_id, source_digests=source_digests, artifact_digests=artifact_digests), status=EvidenceStatus.MEASURED, samples=values, ratio=ratio)

    def _manifest_ratio(self, metric_id: str, candidate: PerformanceEvidenceManifest | None, baseline: PerformanceEvidenceManifest | None) -> MetricMeasurement:
        if candidate is None or baseline is None:
            return self.unavailable(metric_id, "PerformanceEvidenceUnavailable")
        candidate_median = summarize_samples(candidate.samples_ns).median_ns
        baseline_median = summarize_samples(baseline.samples_ns).median_ns
        return self.ratio_metric(metric_id, tuple(candidate.samples_ns) + tuple(baseline.samples_ns), (candidate_median, baseline_median), reason="PerformanceSamplesUnavailable", source_digests={"candidate": candidate.workload_digest, "baseline": baseline.workload_digest}, artifact_digests={"candidate": candidate.artifact_digest, "baseline": baseline.artifact_digest})

    def single_core_native_ratio(self, candidate: PerformanceEvidenceManifest | None, baseline: PerformanceEvidenceManifest | None) -> MetricMeasurement:
        return self._manifest_ratio(METRIC_SINGLE_CORE_NATIVE_RATIO, candidate, baseline)

    def multicore_scaling(
        self,
        candidate: PerformanceEvidenceManifest | None,
        baseline: PerformanceEvidenceManifest | None,
    ) -> MetricMeasurement:
        """Reject legacy unpaired manifests for the frozen scaling gate."""
        return self.unavailable(
            METRIC_MULTICORE_SCALING,
            "RuntimeMeasurementUnavailable:paired one-core/configured-core runs required",
        )

    def multicore_scaling_repeated(
        self,
        one_core_samples: Iterable[int],
        configured_core_samples: Iterable[int],
        configured_core_count: int,
        *,
        affinity: Iterable[int] | None = None,
        repetitions: int | None = None,
        source_digests: Mapping[str, str] | None = None,
        artifact_digests: Mapping[str, str] | None = None,
        config: Any = None,
    ) -> MetricMeasurement:
        """Record paired one-core/configured-core timing samples.

        Samples are interleaved in the retained raw sequence
        ``(one_0, configured_0, one_1, configured_1, ...)``.  Scaling
        efficiency is the exact integer ratio
        ``sum(one_core) / (configured_core_count * sum(configured_core))``.
        No result is measured unless both arms have the same positive sample
        count and the configured count is greater than one.
        """
        try:
            one = tuple(one_core_samples)
            configured = tuple(configured_core_samples)
        except TypeError:
            one = configured = ()
        if (
            type(configured_core_count) is not int
            or configured_core_count < 2
            or len(one) != len(configured)
            or not one
            or any(type(value) is not int or value < 1 for value in (*one, *configured))
            or (repetitions is not None and (type(repetitions) is not int or repetitions != len(one)))
        ):
            return self.unavailable(
                METRIC_MULTICORE_SCALING,
                "RuntimeMeasurementInvalid:paired positive samples and configured core count are required",
            )
        if affinity is None:
            try:
                available = tuple(sorted(os.sched_getaffinity(0)))
            except (AttributeError, OSError):
                available = ()
        else:
            try:
                available = tuple(affinity)
            except TypeError:
                available = ()
        if (
            not available
            or any(type(cpu) is not int or cpu < 0 for cpu in available)
            or len(set(available)) != len(available)
            or len(available) < configured_core_count
        ):
            return self.unavailable(
                METRIC_MULTICORE_SCALING,
                "HardwareRuntimeUnavailable:configured core count exceeds usable affinity",
            )
        ordered_affinity = tuple(sorted(available))
        selected_affinity = ordered_affinity[:configured_core_count]
        payload = {
            "base_config": self.config if config is None else config,
            "metric": METRIC_MULTICORE_SCALING,
            "one_core_count": 1,
            "configured_core_count": configured_core_count,
            "one_core_affinity": [ordered_affinity[0]],
            "configured_core_affinity": list(selected_affinity),
            "repetitions": len(one),
            "sample_layout": "paired_interleaved",
        }
        raw = tuple(value for pair in zip(one, configured) for value in pair)
        numerator = sum(one)
        denominator = configured_core_count * sum(configured)
        metric_kwargs = self._base_kwargs(
            METRIC_MULTICORE_SCALING,
            source_digests=source_digests,
            artifact_digests=artifact_digests,
        )
        metric_kwargs["config"] = payload
        return MetricMeasurement.create(
            **metric_kwargs,
            status=EvidenceStatus.MEASURED,
            samples=raw,
            ratio=(numerator, denominator),
        )

    def collect_multicore_scaling(
        self,
        measure: Any,
        *,
        configured_core_count: int | None = None,
        repetitions: int = 5,
        warmups: int = 1,
        affinity: Iterable[int] | None = None,
        source_digests: Mapping[str, str] | None = None,
        artifact_digests: Mapping[str, str] | None = None,
        config: Any = None,
    ) -> MetricMeasurement:
        """Collect deterministic paired timings using an injected runtime.

        ``measure(core_count, affinity)`` must return one positive integer
        duration in nanoseconds.  The callback is invoked in fixed order
        (one-core then configured-core) for every warmup and measured pair;
        warmups are deliberately not retained as evidence.
        """
        try:
            usable = tuple(sorted(os.sched_getaffinity(0) if affinity is None else affinity))
        except (AttributeError, OSError, TypeError):
            return self.unavailable(METRIC_MULTICORE_SCALING, "HardwareRuntimeUnavailable:CPU affinity discovery unavailable")
        if (
            not usable
            or any(type(cpu) is not int or cpu < 0 for cpu in usable)
            or len(set(usable)) != len(usable)
        ):
            return self.unavailable(METRIC_MULTICORE_SCALING, "HardwareRuntimeUnavailable:no usable CPU affinity")
        if configured_core_count is None:
            configured_core_count = len(usable)
        if type(configured_core_count) is not int or configured_core_count < 2:
            return self.unavailable(METRIC_MULTICORE_SCALING, "HardwareRuntimeUnavailable:multiple usable cores are required")
        if configured_core_count > len(usable):
            return self.unavailable(METRIC_MULTICORE_SCALING, "HardwareRuntimeUnavailable:configured core count exceeds usable affinity")
        if type(repetitions) is not int or repetitions < 1 or type(warmups) is not int or warmups < 0:
            return self.unavailable(METRIC_MULTICORE_SCALING, "RuntimeMeasurementInvalid:repetitions and warmups must be non-negative integers")
        if not callable(measure):
            return self.unavailable(METRIC_MULTICORE_SCALING, "RuntimeMeasurementInvalid:measurement callback is not callable")
        one_affinity = (usable[0],)
        configured_affinity = usable[:configured_core_count]

        def invoke(core_count: int, cpus: tuple[int, ...]) -> int:
            try:
                value = measure(core_count, cpus)
            except TypeError as first:
                try:
                    value = measure(core_count)
                except TypeError:
                    raise first
            if type(value) is not int or value < 1:
                raise ValueError("RuntimeMeasurementInvalid:callback must return a positive integer duration")
            return value

        try:
            for _ in range(warmups):
                invoke(1, one_affinity)
                invoke(configured_core_count, configured_affinity)
            one_samples = []
            configured_samples = []
            for _ in range(repetitions):
                one_samples.append(invoke(1, one_affinity))
                configured_samples.append(invoke(configured_core_count, configured_affinity))
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            return self.unavailable(METRIC_MULTICORE_SCALING, f"RuntimeMeasurementUnavailable:{type(exc).__name__}:{exc}")
        return self.multicore_scaling_repeated(
            one_samples,
            configured_samples,
            configured_core_count,
            affinity=usable,
            repetitions=repetitions,
            source_digests=source_digests,
            artifact_digests=artifact_digests,
            config=config,
        )


    def multicore_scaling_from_work_stealing(
        self,
        result: WorkStealingResult | None,
        samples: Iterable[int] | None,
        ratio: tuple[int, int] | None,
    ) -> MetricMeasurement:
        if result is None:
            return self.unavailable(METRIC_MULTICORE_SCALING, "WorkStealingUnavailable")
        artifact_digest = digest_text(result.to_json())
        return MetricMeasurement.create(
            **self._base_kwargs(
                METRIC_MULTICORE_SCALING,
                artifact_digests={"work-stealing-result": artifact_digest},
            ),
            status=EvidenceStatus.UNAVAILABLE,
            reason="RuntimeMeasurementUnavailable:paired one-core/configured-core runs required",
        )
    def memory_safety_metric(
        self,
        samples: Iterable[int] | None = None,
        *,
        reason: str = "MemorySafetyCorpusUnavailable",
        result: MemorySafetyCorpusResult | None = None,
    ) -> MetricMeasurement:
        if result is not None:
            command = result.runs[0].compile_command if result.runs else self.command
            kwargs = {
                "metric_id": METRIC_MEMORY_SAFETY_CORPUS,
                "command": command,
                "config": result.config,
                "environment": dict(result.environment),
                "source_digests": dict(result.source_digests),
                "artifact_digests": dict(result.artifact_digests),
            }
            if result.status is EvidenceStatus.UNAVAILABLE:
                return MetricMeasurement.create(**kwargs, status=EvidenceStatus.UNAVAILABLE, reason=result.reason or reason)
            failures = len(result.failures)
            return MetricMeasurement.create(
                **kwargs,
                status=EvidenceStatus.MEASURED,
                samples=result.samples,
                reason=f"MemorySafetyFailures:{failures}" if failures else None,
            )
        values = tuple(samples or ())
        if not values:
            return self.unavailable(METRIC_MEMORY_SAFETY_CORPUS, reason)
        return MetricMeasurement.create(**self._base_kwargs(METRIC_MEMORY_SAFETY_CORPUS), status=EvidenceStatus.MEASURED, samples=values)

    def collect_memory_safety_corpus(self, corpus: Sequence[MemorySafetyCorpusCase | Mapping[str, Any]] | str | Path, **kwargs: Any) -> MemorySafetyCorpusResult:
        return run_memory_safety_corpus(corpus, root=self.root, config=self.config, environment=self.environment, **kwargs)


    def unrelated_semantic_edit_metric(
        self,
        observations: Iterable[SemanticEditAuditObservation | Mapping[str, Any]] | None = None,
        *,
        seed: int | None = None,
    ) -> MetricMeasurement:
        if observations is None:
            return self.unavailable(
                METRIC_UNRELATED_SEMANTIC_EDIT_AUDIT,
                "UnrelatedSemanticEditAuditUnavailable",
            )
        try:
            items = tuple(
                sorted(
                    (SemanticEditAuditObservation.from_value(item) for item in observations),
                    key=lambda item: item.edit_id,
                )
            )
        except (TypeError, ValueError):
            return self.unavailable(
                METRIC_UNRELATED_SEMANTIC_EDIT_AUDIT,
                "SemanticEditArtifactsRequired",
            )
        ids = tuple(item.edit_id for item in items)
        if len(set(ids)) != len(ids):
            raise ValueError("DuplicateSemanticEditAuditId")
        sources = {
            f"{item.edit_id}:operation": item.operation_digest
            for item in items
        }
        artifacts = {
            key: digest
            for item in items
            for key, digest in (
                (f"{item.edit_id}:before", item.before_digest),
                (f"{item.edit_id}:after", item.after_digest),
            )
        }
        config = {
            "runner": self.config,
            "seed": seed,
            "required_edits": MINIMUM_SEMANTIC_EDIT_AUDITS,
            "observed_edits": len(items),
            "edits": [
                {
                    "edit_id": item.edit_id,
                    "allowed_identities": list(item.allowed_identities),
                    "changed_identities": list(item.changed_identities),
                    "unrelated_identities": list(item.unrelated_identities),
                }
                for item in items
            ],
        }
        if len(items) < MINIMUM_SEMANTIC_EDIT_AUDITS:
            return MetricMeasurement.create(
                metric_id=METRIC_UNRELATED_SEMANTIC_EDIT_AUDIT,
                status=EvidenceStatus.UNAVAILABLE,
                reason=(
                    f"SemanticEditAuditMinimumNotMet: required "
                    f"{MINIMUM_SEMANTIC_EDIT_AUDITS}, observed {len(items)}"
                ),
                command=self.command,
                config=config,
                source_digests=sources,
                environment=self.environment,
                artifact_digests=artifacts,
            )
        samples = tuple(len(item.unrelated_identities) for item in items)
        unrelated = sum(samples)
        return MetricMeasurement.create(
            metric_id=METRIC_UNRELATED_SEMANTIC_EDIT_AUDIT,
            status=EvidenceStatus.MEASURED,
            reason=f"UnrelatedSemanticChangesDetected:{unrelated}" if unrelated else None,
            command=self.command,
            config=config,
            source_digests=sources,
            environment=self.environment,
            artifact_digests=artifacts,
            samples=samples,
        )
    def repair_iteration_metric(self, histories: Any | None = None) -> MetricMeasurement:
        if histories is None:
            return self.unavailable(
                METRIC_REPAIR_ITERATION_REDUCTION,
                "RepairHistoriesUnavailable",
            )
        return derive_repair_iteration_metric(
            histories,
            command=(*self.command, "repair-iterations"),
            config=self.config,
            environment=self.environment,
        )


    def application_build_metric(
        self,
        observations: Sequence[ApplicationBuildObservation],
        *,
        cohort: Sequence[str] | None = None,
        failures: Mapping[str, str] | None = None,
    ) -> MetricMeasurement:
        ordered = tuple(sorted(observations, key=lambda item: item.application_id))
        sources = {item.application_id: item.source_digest for item in ordered}
        artifacts = {item.application_id: item.artifact_digest for item in ordered}
        ids = tuple(item.application_id for item in ordered)
        unique_ids = set(ids)
        seen_ids: set[str] = set()
        duplicates_set: set[str] = set()
        for application_id in ids:
            if application_id in seen_ids:
                duplicates_set.add(application_id)
            seen_ids.add(application_id)
        duplicates = tuple(sorted(duplicates_set))
        expected = tuple(cohort or ())
        missing = tuple(sorted(set(expected) - unique_ids))
        unexpected = tuple(sorted(unique_ids - set(expected))) if expected else ()
        collection_failures = dict(sorted((failures or {}).items()))
        complete = (
            len(ordered) >= MINIMUM_APPLICATION_BUILDS
            and len(unique_ids) == len(ordered)
            and not collection_failures
            and not missing
            and not unexpected
        )
        config = {
            "runner": self.config,
            "build": {"emit_native": True, "require_interface_lock": False},
            "cohort": list(expected),
            "duplicates": list(duplicates),
            "missing": list(missing),
            "unexpected": list(unexpected),
            "failures": collection_failures,
        }
        if not complete:
            reason = (
                f"ApplicationBuildMinimumNotMet: required {MINIMUM_APPLICATION_BUILDS}, "
                f"observed {len(unique_ids)} unique successful builds"
            )
            return MetricMeasurement.create(
                metric_id=METRIC_APPLICATION_BUILDS,
                status=EvidenceStatus.UNAVAILABLE,
                reason=reason,
                command=self.command,
                config=config,
                source_digests=sources,
                environment=self.environment,
                artifact_digests=artifacts,
            )
        return MetricMeasurement.create(
            metric_id=METRIC_APPLICATION_BUILDS,
            status=EvidenceStatus.MEASURED,
            command=self.command,
            config=config,
            source_digests=sources,
            environment=self.environment,
            artifact_digests=artifacts,
            samples=tuple(item.sample for item in ordered),
        )

    def proof_closure_metric(self, report: VerificationMetricsReport | None) -> MetricMeasurement:
        if report is None:
            return self.unavailable(METRIC_AUTOMATIC_PROOF_CLOSURE, "VerificationMetricsUnavailable")
        if report.total_obligations < 1:
            return self.unavailable(METRIC_AUTOMATIC_PROOF_CLOSURE, "NoVerificationObligations")
        return MetricMeasurement.create(**self._base_kwargs(METRIC_AUTOMATIC_PROOF_CLOSURE), status=EvidenceStatus.MEASURED, samples=(report.automatically_closed, report.total_obligations), ratio=(report.automatically_closed, report.total_obligations))
    @staticmethod
    def _proof_closure_compilation_items(
        compilations: Mapping[str, Any] | Sequence[tuple[str, Any]],
    ) -> tuple[tuple[str, Any], ...]:
        if isinstance(compilations, Mapping):
            items = tuple(compilations.items())
        else:
            try:
                items = tuple(compilations)
            except TypeError as exc:
                raise ValueError("InvalidProofClosureCohort") from exc
        if not items:
            return ()
        normalized: list[tuple[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("InvalidProofClosureApplication")
            application_id, compilation = item
            if not isinstance(application_id, str) or not application_id.strip():
                raise ValueError("InvalidProofClosureApplication")
            if application_id in seen:
                raise ValueError(f"DuplicateProofClosureApplication:{application_id}")
            seen.add(application_id)
            normalized.append((application_id, compilation))
        return tuple(sorted(normalized, key=lambda item: item[0]))

    @staticmethod
    def _proof_closure_application(
        application_id: str,
        compilation: Any,
    ) -> tuple[int, int, str, dict[str, str], dict[str, str]]:
        """Extract and validate closure from a real ProjectCompilation.

        The aggregate verification report is deliberately not trusted for the
        counts.  Obligation and verification-metrics stage artifacts must both
        be present, digest-bound, and linked to the same HIR revision before
        states are counted.
        """
        obligations = getattr(compilation, "obligations", None)
        metrics = getattr(compilation, "verification_metrics", None)
        artifacts = getattr(compilation, "artifacts", None)
        if obligations is None or metrics is None or not isinstance(artifacts, Mapping):
            raise ValueError(f"MissingProofClosureEvidence:{application_id}")
        try:
            obligation_json = obligations.to_json()
            metrics_json = metrics.to_json()
            obligation_digest = obligations.digest
            obligation_items = tuple(obligations.obligations)
            metric_items = tuple(metrics.obligations)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"InvalidProofClosureEvidence:{application_id}") from exc
        if not obligation_items:
            raise ValueError(f"EmptyObligationEvidence:{application_id}")
        obligation_artifact = artifacts.get("obligations")
        metric_artifact = artifacts.get("verification-metrics")
        hir_artifact = artifacts.get("hir")
        if obligation_artifact is None or metric_artifact is None or hir_artifact is None:
            raise ValueError(f"MissingProofClosureArtifact:{application_id}")

        def _artifact_digest(artifact: Any, expected: str, name: str) -> None:
            if getattr(artifact, "content", None) != expected:
                raise ValueError(f"StaleProofClosureArtifact:{application_id}:{name}")
            if getattr(artifact, "digest", None) != digest_text(expected):
                raise ValueError(f"StaleProofClosureArtifact:{application_id}:{name}")

        _artifact_digest(obligation_artifact, obligation_json, "obligations")
        _artifact_digest(metric_artifact, metrics_json, "verification-metrics")
        hir_digest = getattr(obligations, "hir_digest", None)
        if not _is_digest(hir_digest) or getattr(metrics, "hir_digest", None) != hir_digest:
            raise ValueError(f"StaleProofClosureRevision:{application_id}")
        if getattr(hir_artifact, "digest", None) != hir_digest:
            raise ValueError(f"StaleProofClosureRevision:{application_id}")
        if getattr(obligation_artifact, "parent_digest", None) != hir_digest:
            raise ValueError(f"StaleProofClosureArtifact:{application_id}:obligations-parent")
        if getattr(metric_artifact, "parent_digest", None) != getattr(obligation_artifact, "digest", None):
            raise ValueError(f"StaleProofClosureArtifact:{application_id}:metrics-parent")
        if getattr(metrics, "obligation_digest", None) != obligation_digest:
            raise ValueError(f"StaleProofClosureRevision:{application_id}")

        obligation_ids = tuple(item.obligation_id for item in obligation_items)
        metric_ids = tuple(item.obligation_id for item in metric_items)
        if metric_ids != obligation_ids:
            raise ValueError(f"StaleProofClosureEvidence:{application_id}")
        closed = sum(
            getattr(item.state, "value", item.state) == "automatically_closed"
            for item in metric_items
        )
        total = len(obligation_items)
        if (
            getattr(metrics, "total_obligations", None) != total
            or getattr(metrics, "automatically_closed", None) != closed
            or getattr(metrics, "closed_rate_numerator", None) != closed
            or getattr(metrics, "closed_rate_denominator", None) != total
            or getattr(metrics, "closed_rate_basis_points", None) != (closed * 10000 // total)
        ):
            raise ValueError(f"StaleProofClosureCounts:{application_id}")
        source_digest = getattr(getattr(compilation, "hir", None), "source_sha256", None)
        if not _is_digest(source_digest):
            raise ValueError(f"MissingProofClosureSource:{application_id}")
        source_digests = {application_id: source_digest}
        artifact_digests = {
            f"{application_id}:hir": hir_digest,
            f"{application_id}:obligations": obligation_artifact.digest,
            f"{application_id}:verification-metrics": metric_artifact.digest,
        }
        return closed, total, source_digest, source_digests, artifact_digests

    def proof_closure_metric_from_compilations(
        self,
        compilations: Mapping[str, Any] | Sequence[tuple[str, Any]],
    ) -> MetricMeasurement:
        """Measure exact closure over a frozen cohort of compiled applications."""
        items = self._proof_closure_compilation_items(compilations)
        if not items:
            return self.unavailable(
                METRIC_AUTOMATIC_PROOF_CLOSURE,
                "ProofClosureCohortUnavailable",
            )
        observations = tuple(
            (application_id, self._proof_closure_application(application_id, compilation))
            for application_id, compilation in items
        )
        samples = tuple(
            value
            for _, (closed, total, _, _, _) in observations
            for value in (closed, total)
        )
        total_closed = sum(item[1][0] for item in observations)
        total_obligations = sum(item[1][1] for item in observations)
        if total_obligations < 1:
            return self.unavailable(
                METRIC_AUTOMATIC_PROOF_CLOSURE,
                "NoVerificationObligations",
            )
        source_digests = {
            key: value
            for _, (_, _, _, sources, _) in observations
            for key, value in sources.items()
        }
        artifact_digests = {
            key: value
            for _, (_, _, _, _, artifacts) in observations
            for key, value in artifacts.items()
        }
        application_counts = [
            {"application_id": app_id, "closed": closed, "total": total}
            for app_id, (closed, total, _, _, _) in observations
        ]
        config = {
            "cohort": application_counts,
            "runner_config": self.config,
        }
        metric_kwargs = self._base_kwargs(
            METRIC_AUTOMATIC_PROOF_CLOSURE,
            source_digests=source_digests,
            artifact_digests=artifact_digests,
        )
        metric_kwargs["config"] = config
        return MetricMeasurement.create(
            **metric_kwargs,
            status=EvidenceStatus.MEASURED,
            samples=samples,
            ratio=(total_closed, total_obligations),
        )

    def proof_closure_metric_from_artifacts(
        self,
        compilations: Mapping[str, Any] | Sequence[tuple[str, Any]],
    ) -> MetricMeasurement:
        """Explicit alias emphasizing that inputs are compiler artifacts."""
        return self.proof_closure_metric_from_compilations(compilations)

    def gpu_metric(
        self,
        samples: Iterable[int] | None = None,
        *,
        capabilities: BackendCapabilities | Mapping[str, Any] | None = None,
        ratio: tuple[int, int] | None = None,
        paired_samples: Iterable[tuple[int, int]] | None = None,
        backend_probe: Mapping[str, Any] | None = None,
        source_digests: Mapping[str, str] | None = None,
        artifact_digests: Mapping[str, str] | None = None,
    ) -> MetricMeasurement:
        """Record a supported GPU/candidate-vs-baseline measurement.

        ``paired_samples`` is flattened in candidate, baseline order so the
        existing raw-sample schema retains every observation without inventing
        a generic metadata field.  The ratio is always derived from the paired
        medians; caller-supplied ratios remain supported only for the legacy
        ``samples`` API.
        """
        backend = detect_gpu_backend(capabilities, backend_probe=backend_probe)
        config = {
            "runner_config": self.config,
            "gpu_backend": backend,
            "pairing": {"layout": "candidate_then_baseline", "raw_unit": "ns"},
        }
        environment = dict(self.environment)
        for key in ("provider", "version", "device", "runtime"):
            value = backend.get(key)
            if value is not None:
                environment[f"gpu_{key}"] = str(value)
        base = self._base_kwargs(
            METRIC_SUPPORTED_GPU_RATIO,
            source_digests=source_digests,
            artifact_digests=artifact_digests,
        )
        base.update(config=config, environment=environment)
        if not backend["available"]:
            return MetricMeasurement.create(**base, status=EvidenceStatus.UNAVAILABLE, reason=backend["reason"])
        if paired_samples is not None and samples is not None:
            return MetricMeasurement.create(
                **base,
                status=EvidenceStatus.UNAVAILABLE,
                reason="GPUMeasurementInputConflict",
            )
        if paired_samples is not None:
            pairs = tuple(paired_samples)
            if not pairs or any(
                not isinstance(pair, (tuple, list)) or len(pair) != 2
                or any(type(value) is not int or value < 1 for value in pair)
                for pair in pairs
            ):
                return MetricMeasurement.create(
                    **base,
                    status=EvidenceStatus.UNAVAILABLE,
                    reason="GPUMeasurementInvalid:PairedSamples",
                )
            candidate_values = tuple(pair[0] for pair in pairs)
            baseline_values = tuple(pair[1] for pair in pairs)
            values = tuple(value for pair in pairs for value in pair)
            measured_ratio = (
                summarize_samples(candidate_values).median_ns,
                summarize_samples(baseline_values).median_ns,
            )
            return MetricMeasurement.create(
                **base,
                status=EvidenceStatus.MEASURED,
                samples=values,
                ratio=measured_ratio,
            )
        values = tuple(samples or ())
        if not values or any(type(value) is not int or value < 1 for value in values) or ratio is None:
            return MetricMeasurement.create(
                **base,
                status=EvidenceStatus.UNAVAILABLE,
                reason="GPUMeasurementUnavailable",
            )
        return MetricMeasurement.create(
            **base,
            status=EvidenceStatus.MEASURED,
            samples=values,
            ratio=ratio,
        )

    def collect_gpu_ratio(
        self,
        candidate: Callable[[], Any],
        baseline: Callable[[], Any],
        *,
        capabilities: BackendCapabilities | Mapping[str, Any] | None = None,
        backend_probe: Mapping[str, Any] | None = None,
        repetitions: int = 3,
        warmups: int = 1,
        source_digests: Mapping[str, str] | None = None,
        artifact_digests: Mapping[str, str] | None = None,
    ) -> MetricMeasurement:
        """Run paired candidate/baseline callbacks and preserve raw timings."""
        if type(repetitions) is not int or repetitions < 1 or type(warmups) is not int or warmups < 0:
            return self.gpu_metric(
                capabilities=capabilities,
                backend_probe=backend_probe,
                source_digests=source_digests,
                artifact_digests=artifact_digests,
            )
        backend = detect_gpu_backend(capabilities, backend_probe=backend_probe)
        if not backend["available"]:
            return self.gpu_metric(
                capabilities=capabilities,
                backend_probe=backend_probe,
                source_digests=source_digests,
                artifact_digests=artifact_digests,
            )
        try:
            for _ in range(warmups):
                candidate()
                baseline()
            pairs: list[tuple[int, int]] = []
            for _ in range(repetitions):
                started = time.perf_counter_ns()
                candidate()
                candidate_ns = time.perf_counter_ns() - started
                started = time.perf_counter_ns()
                baseline()
                baseline_ns = time.perf_counter_ns() - started
                pairs.append((candidate_ns, baseline_ns))
        except Exception as exc:
            environment = dict(self.environment)
            for key in ("provider", "version", "device", "runtime"):
                value = backend.get(key)
                if value is not None:
                    environment[f"gpu_{key}"] = str(value)
            base = self._base_kwargs(
                METRIC_SUPPORTED_GPU_RATIO,
                source_digests=source_digests,
                artifact_digests=artifact_digests,
            )
            base.update(
                config={
                    "runner_config": self.config,
                    "gpu_backend": backend,
                    "pairing": {"layout": "candidate_then_baseline", "raw_unit": "ns"},
                },
                environment=environment,
            )
            return MetricMeasurement.create(
                **base,
                status=EvidenceStatus.UNAVAILABLE,
                reason=f"GPUMeasurementError:{type(exc).__name__}",
            )
        return self.gpu_metric(
            capabilities=capabilities,
            backend_probe=backend_probe,
            paired_samples=pairs,
            source_digests=source_digests,
            artifact_digests=artifact_digests,
        )

    def deterministic_build_metric(
        self,
        artifact_digests: Sequence[str] = (),
        *,
        repeated_builds: Sequence[Mapping[str, Any]] | None = None,
    ) -> MetricMeasurement:
        """Record repeated-build identity without conflating semantic equality.

        ``artifact_digests`` remains the original compact API.  The richer
        ``repeated_builds`` records are produced by
        :meth:`collect_repeated_builds`; their source map contains semantic
        stage digests while their artifact map contains raw paths and hashes.
        Thus equal generated semantics do not hide a byte-identity failure.
        """
        if repeated_builds is None:
            values = tuple(artifact_digests)
            if not values or any(not _is_digest(item) for item in values):
                return self.unavailable(METRIC_DETERMINISTIC_REPEATED_BUILDS, "RepeatedBuildArtifactsUnavailable")
            artifacts = {f"repeat-{index}": item for index, item in enumerate(values)}
            if len(set(values)) != 1:
                return MetricMeasurement.create(
                    **self._base_kwargs(METRIC_DETERMINISTIC_REPEATED_BUILDS, artifact_digests=artifacts),
                    status=EvidenceStatus.MEASURED,
                    samples=tuple(1 for _ in values),
                    reason="ArtifactDigestMismatch",
                )
            return MetricMeasurement.create(
                **self._base_kwargs(METRIC_DETERMINISTIC_REPEATED_BUILDS, artifact_digests=artifacts),
                status=EvidenceStatus.MEASURED,
                samples=tuple(1 for _ in values),
            )

        records = tuple(repeated_builds)
        if not records:
            return self.unavailable(METRIC_DETERMINISTIC_REPEATED_BUILDS, "RepeatedBuildArtifactsUnavailable")
        usable = tuple(item for item in records if item.get("status") == EvidenceStatus.MEASURED.value)
        source_digests: dict[str, str] = {}
        artifact_map: dict[str, str] = {}
        for index, item in enumerate(records):
            for key, value in dict(item.get("semantic_digests") or {}).items():
                if _is_digest(value):
                    source_digests[f"repeat-{index}:semantic:{key}"] = value
            input_path = item.get("input_path")
            input_digest = item.get("input_sha256")
            if isinstance(input_path, str) and _is_digest(input_digest):
                source_digests[f"repeat-{index}:input:{input_path}"] = input_digest
            for path, value in dict(item.get("artifact_digests") or {}).items():
                if isinstance(path, str) and _is_digest(value):
                    artifact_map[path] = value
        if len(usable) != len(records):
            reasons = tuple(
                str(item.get("reason") or "BuildUnavailable")
                for item in records
                if item.get("status") != EvidenceStatus.MEASURED.value
            )
            return MetricMeasurement.create(
                metric_id=METRIC_DETERMINISTIC_REPEATED_BUILDS,
                status=EvidenceStatus.UNAVAILABLE,
                reason="RepeatedBuildUnavailable:" + ";".join(sorted(set(reasons))),
                command=self.command,
                config={"runner": self.config, "repeated_builds": list(records)},
                source_digests=source_digests,
                environment=self.environment,
                artifact_digests=artifact_map,
            )
        by_application: dict[str, list[Mapping[str, Any]]] = {}
        for item in usable:
            application = str(item.get("application_id") or "")
            by_application.setdefault(application, []).append(item)
        semantic_converged = True
        byte_identical = True
        pair_count = 0
        matching_pairs = 0
        pair_samples: list[int] = []
        for application, app_records in sorted(by_application.items()):
            if len(app_records) < 2:
                semantic_converged = False
                byte_identical = False
                continue
            first = app_records[0]
            first_semantic = dict(first.get("semantic_digests") or {})
            first_roles = dict(first.get("artifact_roles") or {})
            for current in app_records[1:]:
                pair_count += 1
                current_semantic = dict(current.get("semantic_digests") or {})
                current_roles = dict(current.get("artifact_roles") or {})
                semantic_equal = first_semantic == current_semantic
                bytes_equal = bool(first_roles) and first_roles.keys() == current_roles.keys() and all(
                    first.get("artifact_digests", {}).get(first_roles[role])
                    == current.get("artifact_digests", {}).get(current_roles[role])
                    for role in first_roles
                )
                semantic_converged = semantic_converged and semantic_equal
                byte_identical = byte_identical and bytes_equal
                matching_pairs += int(bytes_equal)
                pair_samples.append(int(bytes_equal))
        reasons: list[str] = []
        if not semantic_converged:
            reasons.append("SemanticConvergenceMismatch")
        if not byte_identical:
            reasons.append("ByteIdentityMismatch")
        reason = ";".join(reasons) or None
        samples = tuple(pair_samples) or tuple(
            int(item.get("status") == EvidenceStatus.MEASURED.value)
            for item in records
        )
        config = {
            "runner": self.config,
            "repeated_builds": [
                {
                    key: value
                    for key, value in item.items()
                    if key in {"application_id", "repeat", "command", "compiler", "compiler_version", "config", "environment", "artifact_roles", "status", "reason"}
                }
                for item in records
            ],
            "semantic_converged": semantic_converged,
            "byte_identical": byte_identical,
        }
        environment = dict(self.environment)
        environment.update({"SOURCE_DATE_EPOCH": "0", "LC_ALL": "C", "TZ": "UTC"})
        return MetricMeasurement.create(
            metric_id=METRIC_DETERMINISTIC_REPEATED_BUILDS,
            status=EvidenceStatus.MEASURED,
            reason=reason,
            command=self.command,
            config=config,
            source_digests=source_digests,
            environment=environment,
            artifact_digests=artifact_map,
            samples=samples,
            ratio=(matching_pairs, pair_count) if pair_count else None,
        )

    def collect_repeated_builds(
        self,
        *,
        repetitions: int = 2,
        output_dir: str | Path | None = None,
        applications: Sequence[str | Path] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Build each application in clean, isolated directories.

        Raw generated artifacts, compiler source, binary, and a provenance
        manifest are retained on disk.  The returned records contain paths and
        hashes for every retained file, plus stage-artifact digests used to
        distinguish semantic convergence from byte identity.
        """
        if type(repetitions) is not int or repetitions < 2:
            raise ValueError("RepeatedBuildsNeedAtLeastTwoRepetitions")
        from merlo.compiler import compile_project

        selected = (
            self.applications
            if applications is None
            else tuple(Path(item).resolve() for item in applications)
        )
        if not selected:
            return ()
        base = Path(output_dir).resolve() if output_dir is not None else self.root / ".merlo" / "frozen-evidence"
        base.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        for application in selected:
            for repetition in range(repetitions):
                run_dir = Path(tempfile.mkdtemp(prefix=f"{application.name}-repeat-{repetition}-", dir=base))
                record: dict[str, Any] = {
                    "application_id": application.name,
                    "repeat": repetition,
                    "output_dir": str(run_dir),
                    "status": EvidenceStatus.UNAVAILABLE.value,
                    "artifact_digests": {},
                    "artifact_roles": {},
                    "semantic_digests": {},
                }
                try:
                    compilation = compile_project(
                        application,
                        emit_native=True,
                        require_interface_lock=False,
                        output=run_dir / "native" / application.name,
                    )
                    native = compilation.native
                    if native is None or native.binary_path is None or native.binary_sha256 is None:
                        record["reason"] = "NativeArtifactUnavailable"
                        records.append(record)
                        continue
                    raw_artifacts: dict[str, str] = {}
                    artifact_roles: dict[str, str] = {}
                    for artifact in compilation.artifacts.values():
                        artifact_path = run_dir / "generated" / f"{artifact.name}.artifact"
                        artifact_path.parent.mkdir(parents=True, exist_ok=True)
                        artifact_path.write_text(artifact.content, encoding="utf-8")
                        raw_artifacts[str(artifact_path)] = digest_file(artifact_path)
                        artifact_roles[f"generated:{artifact.name}"] = str(artifact_path)
                    source_path = Path(native.source_path)
                    binary_path = Path(native.binary_path)
                    raw_artifacts[str(source_path)] = digest_file(source_path)
                    raw_artifacts[str(binary_path)] = digest_file(binary_path)
                    artifact_roles["compiler-source"] = str(source_path)
                    artifact_roles["binary"] = str(binary_path)
                    input_path = Path(compilation.entry_path)
                    semantic_digests = {
                        name: artifact.digest
                        for name, artifact in sorted(compilation.artifacts.items())
                    }
                    effective_environment = dict(self.environment)
                    effective_environment.update({"SOURCE_DATE_EPOCH": "0", "LC_ALL": "C", "TZ": "UTC"})
                    command = tuple(native.command)
                    config = {
                        "runner": self.config,
                        "compiler": native.compiler,
                        "compiler_version": native.compiler_version,
                        "release": False,
                        "emit_native": True,
                    }
                    provenance = {
                        "application_id": application.name,
                        "repeat": repetition,
                        "command": list(command),
                        "config": config,
                        "environment": effective_environment,
                        "input_path": str(input_path),
                        "input_sha256": digest_file(input_path),
                        "source_sha256": digest_file(source_path),
                        "binary_sha256": native.binary_sha256,
                        "semantic_digests": semantic_digests,
                        "artifact_roles": artifact_roles,
                        "artifact_digests": raw_artifacts,
                    }
                    provenance_path = run_dir / "provenance.json"
                    provenance_path.write_text(_canonical(provenance) + "\n", encoding="utf-8")
                    raw_artifacts[str(provenance_path)] = digest_file(provenance_path)
                    record.update(
                        {
                            "status": EvidenceStatus.MEASURED.value,
                            "command": list(command),
                            "compiler": native.compiler,
                            "compiler_version": native.compiler_version,
                            "config": config,
                            "environment": effective_environment,
                            "input_path": str(input_path),
                            "input_sha256": digest_file(input_path),
                            "source_sha256": digest_file(source_path),
                            "binary_sha256": native.binary_sha256,
                            "semantic_digests": semantic_digests,
                            "artifact_roles": artifact_roles,
                            "artifact_digests": raw_artifacts,
                        }
                    )
                except Exception as exc:
                    record["reason"] = f"{type(exc).__name__}:{exc}"
                records.append(record)
        return tuple(records)

    def ai_context_reduction_metric(
        self,
        report: Any | None = None,
        *,
        records: Iterable[Any] | None = None,
        tokenizer_contract: Mapping[str, Any] | None = None,
    ) -> MetricMeasurement:
        """Measure context-token reduction from one real, paired AI run.

        The provider's ``text`` arm is the baseline and ``semantic`` is the
        Merlo arm.  A record is admissible only when every task has exactly one
        record per arm, all records identify the same provider/model revision,
        and both arms carry the same explicit tokenizer/accounting contract.
        Counts stay as task-ordered baseline/Merlo pairs in ``samples``; the
        ratio is the exact ``(baseline - merlo) / baseline`` fraction.
        """
        try:
            from merlo.ai_evidence import AIRawTaskRecord, validate_evidence
        except ImportError as exc:
            raise ValueError("AIContextReductionEvidenceUnavailable") from exc

        if report is not None and records is not None:
            raise ValueError("AIContextReductionDuplicateInputs")
        if report is not None:
            validated = validate_evidence(report)
            values = tuple(validated.records)
        elif records is not None:
            normalized_records = []
            for item in records:
                if isinstance(item, AIRawTaskRecord):
                    normalized_records.append(item)
                    continue
                if isinstance(item, Mapping):
                    payload = dict(item)
                    payload["arm"] = {
                        "baseline": "text",
                        "text_baseline": "text",
                        "merlo": "semantic",
                        "semantic_protocol": "semantic",
                    }.get(payload.get("arm"), payload.get("arm"))
                    normalized_records.append(AIRawTaskRecord.from_dict(payload))
                    continue
                normalized_records.append(AIRawTaskRecord.from_dict(item))
            values = tuple(normalized_records)
        else:
            return self.unavailable(METRIC_AI_CONTEXT_REDUCTION, "AIPairedRecordsUnavailable")
        if not values:
            return self.unavailable(METRIC_AI_CONTEXT_REDUCTION, "AIPairedRecordsUnavailable")

        def _contract(record: Any) -> Mapping[str, Any] | None:
            raw = record.raw
            if not isinstance(raw, Mapping):
                return None
            for key in ("token_accounting", "tokenizer_contract", "token_contract"):
                candidate = raw.get(key)
                if isinstance(candidate, Mapping):
                    value = dict(candidate)
                    tokenizer = value.get("tokenizer") or value.get("name")
                    accounting = value.get("accounting_contract") or value.get("accounting")
                    if tokenizer is not None and accounting is not None:
                        return value
            tokenizer = raw.get("tokenizer", raw.get("tokenizer_name"))
            accounting = raw.get(
                "accounting_contract",
                raw.get("token_accounting_contract", raw.get("accounting")),
            )
            if tokenizer is not None and accounting is not None:
                result: dict[str, Any] = {
                    "tokenizer": tokenizer,
                    "accounting_contract": accounting,
                }
                for key in ("tokenizer_revision", "tokenizer_version", "accounting_revision"):
                    if key in raw:
                        result[key] = raw[key]
                return result
            return None

        run_ids = [item.run_id for item in values]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("AIContextReductionDuplicateRun")
        identities = {(item.provider, item.model, item.revision) for item in values}
        if len(identities) != 1:
            raise ValueError("AIContextReductionModelMismatch")
        contracts = tuple(_contract(item) for item in values)
        if any(item is None for item in contracts):
            return self.unavailable(METRIC_AI_CONTEXT_REDUCTION, "AITokenizerAccountingContractUnavailable")
        first_contract = dict(contracts[0] or {})
        if any(_canonical(dict(item or {})) != _canonical(first_contract) for item in contracts[1:]):
            raise ValueError("AIContextReductionTokenizerContractMismatch")
        if tokenizer_contract is not None and _canonical(dict(tokenizer_contract)) != _canonical(first_contract):
            raise ValueError("AIContextReductionTokenizerContractMismatch")

        grouped: dict[str, dict[str, Any]] = {}
        for item in values:
            if item.arm not in ("semantic", "text"):
                raise ValueError("AIContextReductionInvalidArm")
            arms = grouped.setdefault(item.task_id, {})
            if item.arm in arms:
                raise ValueError("AIContextReductionDuplicateArm")
            arms[item.arm] = item
        if not grouped or any(set(arms) != {"semantic", "text"} for arms in grouped.values()):
            raise ValueError("AIContextReductionUnpairedRecords")

        ordered = tuple(sorted(grouped.items()))
        for task_id, arms in ordered:
            semantic = arms["semantic"]
            text = arms["text"]
            if (
                (semantic.prompt_hash, semantic.task_hash, semantic.dataset_hash, semantic.success_oracle)
                != (text.prompt_hash, text.task_hash, text.dataset_hash, text.success_oracle)
            ):
                raise ValueError(f"AIContextReductionStaleRecord:{task_id}")
        baseline_total = sum(arms["text"].context_tokens for _, arms in ordered)
        merlo_total = sum(arms["semantic"].context_tokens for _, arms in ordered)
        if baseline_total < 1:
            return self.unavailable(METRIC_AI_CONTEXT_REDUCTION, "AIContextReductionZeroBaseline")
        if merlo_total > baseline_total:
            return self.unavailable(METRIC_AI_CONTEXT_REDUCTION, "AIContextReductionNegative")

        provider, model, revision = next(iter(identities))
        samples = tuple(
            value
            for _, arms in ordered
            for value in (arms["text"].context_tokens, arms["semantic"].context_tokens)
        )
        source_digests = {
            task_id: digest_value({
                "task_id": task_id,
                "prompt_hash": arms["text"].prompt_hash,
                "task_hash": arms["text"].task_hash,
                "dataset_hash": arms["text"].dataset_hash,
            })
            for task_id, arms in ordered
        }
        artifact_digests = {
            f"{task_id}:baseline": arms["text"].digest
            for task_id, arms in ordered
        }
        artifact_digests.update({
            f"{task_id}:merlo": arms["semantic"].digest
            for task_id, arms in ordered
        })
        environment = dict(self.environment)
        environment.update({
            "ai_provider": provider,
            "ai_model": model,
            "ai_revision": revision,
            "ai_tokenizer_contract": _canonical(first_contract),
        })
        config = {
            "runner": self.config,
            "metric": METRIC_AI_CONTEXT_REDUCTION,
            "token_field": "context_tokens",
            "baseline_arm": "text",
            "merlo_arm": "semantic",
            "provider": provider,
            "model": model,
            "revision": revision,
            "tokenizer_contract": first_contract,
            "task_ids": [task_id for task_id, _ in ordered],
        }
        return MetricMeasurement.create(
            METRIC_AI_CONTEXT_REDUCTION,
            status=EvidenceStatus.MEASURED,
            command=self.command,
            config=config,
            source_digests=source_digests,
            environment=environment,
            artifact_digests=artifact_digests,
            samples=samples,
            ratio=(baseline_total - merlo_total, baseline_total),
        )


    def _collect_application_builds(
        self,
    ) -> tuple[tuple[ApplicationBuildObservation, ...], dict[str, str]]:
        """Build the frozen cohort without replacing or hiding failed members."""
        from merlo.compiler import compile_project

        observations: list[ApplicationBuildObservation] = []
        failures: dict[str, str] = {}
        with tempfile.TemporaryDirectory(prefix="merlo-frozen-") as output:
            for application in self.applications:
                application_id = application.name
                if not application.is_dir():
                    failures[application_id] = "ApplicationDirectoryMissing"
                    continue
                source_files = tuple(sorted(application.rglob("*.mlo")))
                if not source_files:
                    failures[application_id] = "ApplicationSourcesMissing"
                    continue
                source_inventory = {
                    str(path.relative_to(application)): digest_file(path)
                    for path in source_files
                }
                try:
                    compilation = compile_project(
                        application,
                        emit_native=True,
                        require_interface_lock=False,
                        output=Path(output) / application_id,
                    )
                    native = compilation.native
                    if native is None or native.binary_sha256 is None:
                        failures[application_id] = "NativeArtifactMissing"
                        continue
                    observations.append(
                        ApplicationBuildObservation(
                            application_id,
                            tuple(native.command),
                            digest_value(source_inventory),
                            native.binary_sha256,
                        )
                    )
                except Exception as exc:
                    failures[application_id] = f"{type(exc).__name__}:{exc}"
        return tuple(observations), failures

    def collect_application_builds(self) -> tuple[ApplicationBuildObservation, ...]:
        """Build every frozen application or raise with the complete failure set."""
        observations, failures = self._collect_application_builds()
        if failures:
            raise ValueError(f"ApplicationBuildFailures:{_canonical(failures)}")
        return observations

    def collect_application_build_metric(self) -> MetricMeasurement:
        observations, failures = self._collect_application_builds()
        return self.application_build_metric(
            observations,
            cohort=tuple(path.name for path in self.applications),
            failures=failures,
        )
    def collect_single_core_native_ratio(
        self,
        candidate_command: Sequence[str] | None,
        baseline_command: Sequence[str] | None,
        *,
        repetitions: int = 30,
        warmups: int = 5,
        timeout_seconds: float = 30.0,
        input_data: bytes | str | None = None,
        cwd: str | Path | None = None,
        source_digests: Mapping[str, str] | None = None,
        artifact_digests: Mapping[str, str] | None = None,
        affinity_cpu: int | None = None,
        pinned_comparison: PerformanceEvidenceManifest | None = None,
    ) -> MetricMeasurement:
        """Measure two pinned native commands in fresh processes.

        The candidate median is the ratio numerator and the pinned baseline
        median is the denominator.  ``samples`` retains every measured timing
        (candidate samples first, then baseline samples); warmups are not
        observations.  A command failure, timeout, output mismatch, missing
        executable, or absent comparison produces ``UNAVAILABLE`` instead of a
        fabricated timing.
        """
        metric_id = METRIC_SINGLE_CORE_NATIVE_RATIO
        if candidate_command is None or baseline_command is None:
            return self.unavailable(metric_id, "SingleCoreComparisonUnavailable:both commands are required")
        try:
            candidate = _command(candidate_command)
            baseline = _command(baseline_command)
        except (TypeError, ValueError) as exc:
            return self.unavailable(metric_id, f"SingleCoreComparisonUnavailable:invalid command ({exc})")
        if type(repetitions) is not int or repetitions < 1:
            return self.unavailable(metric_id, "SingleCoreConfigurationUnavailable:repetitions must be positive")
        if type(warmups) is not int or warmups < 0:
            return self.unavailable(metric_id, "SingleCoreConfigurationUnavailable:warmups must be non-negative")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0 or timeout_seconds != timeout_seconds:
            return self.unavailable(metric_id, "SingleCoreConfigurationUnavailable:timeout must be positive")
        if input_data is None:
            payload = None
        elif isinstance(input_data, bytes):
            payload = input_data
        elif isinstance(input_data, str):
            payload = input_data.encode("utf-8")
        else:
            return self.unavailable(metric_id, "SingleCoreConfigurationUnavailable:input_data must be bytes or text")

        try:
            available_cpus = tuple(sorted(os.sched_getaffinity(0)))
        except (AttributeError, OSError):
            return self.unavailable(metric_id, "SingleCoreAffinityUnavailable:OS affinity API is unavailable")
        if not available_cpus:
            return self.unavailable(metric_id, "SingleCoreAffinityUnavailable:no CPU is available")
        if affinity_cpu is None:
            selected_cpu = available_cpus[0]
        elif type(affinity_cpu) is int and affinity_cpu in available_cpus:
            selected_cpu = affinity_cpu
        else:
            return self.unavailable(metric_id, f"SingleCoreAffinityUnavailable:CPU {affinity_cpu!r} is not available")

        commands = (
            f"candidate={_canonical(list(candidate))}",
            f"baseline={_canonical(list(baseline))}",
        )
        config: dict[str, Any] = {
            "runner": self.config,
            "repetitions": repetitions,
            "warmups": warmups,
            "core_count": 1,
            "affinity": [selected_cpu],
            "timeout_seconds": timeout_seconds,
            "cwd": str(Path(cwd).resolve()) if cwd is not None else str(self.root),
            "input_digest": digest_bytes(payload) if payload is not None else None,
            "pinned_comparison_digest": pinned_comparison.digest if pinned_comparison is not None else None,
        }
        sources = dict(source_digests or {})
        sources.setdefault("candidate-command", digest_value(list(candidate)))
        sources.setdefault("baseline-command", digest_value(list(baseline)))
        if payload is not None:
            sources.setdefault("input", digest_bytes(payload))
        artifacts = dict(artifact_digests or {})

        def resolve(command: tuple[str, ...], label: str) -> Path | None:
            executable = shutil.which(command[0])
            if executable is None:
                candidate_path = Path(command[0])
                if candidate_path.is_file():
                    executable = str(candidate_path)
            if executable is None:
                raise RuntimeError(f"SingleCoreToolchainUnavailable:{label}:{command[0]}")
            path = Path(executable).resolve()
            if not path.is_file():
                raise RuntimeError(f"SingleCoreToolchainUnavailable:{label}:{command[0]}")
            return path

        try:
            candidate_executable = resolve(candidate, "candidate")
            baseline_executable = resolve(baseline, "baseline")
            artifacts.setdefault("candidate-executable", digest_file(candidate_executable))
            artifacts.setdefault("baseline-executable", digest_file(baseline_executable))
        except (OSError, RuntimeError) as exc:
            return MetricMeasurement.create(
                metric_id, status=EvidenceStatus.UNAVAILABLE, reason=str(exc),
                command=commands, config=config, source_digests=sources,
                environment=self.environment, artifact_digests=artifacts,
            )

        run_environment = dict(self.environment)
        run_cwd = Path(cwd).resolve() if cwd is not None else self.root
        if not run_cwd.is_dir():
            return MetricMeasurement.create(
                metric_id, status=EvidenceStatus.UNAVAILABLE,
                reason=f"SingleCoreComparisonUnavailable:cwd does not exist: {run_cwd}",
                command=commands, config=config, source_digests=sources,
                environment=self.environment, artifact_digests=artifacts,
            )

        def pin_single_core() -> None:
            os.sched_setaffinity(0, {selected_cpu})

        def run_once(
            command: tuple[str, ...],
        ) -> tuple[int | None, bytes, bytes, int, str | None]:
            started = time.perf_counter_ns()
            try:
                completed = subprocess.run(
                    list(command), input=payload, capture_output=True, check=False,
                    shell=False, cwd=run_cwd, env=run_environment,
                    timeout=float(timeout_seconds), preexec_fn=pin_single_core,
                )
            except subprocess.TimeoutExpired:
                return None, b"", b"", time.perf_counter_ns() - started, "timeout"
            except (OSError, ValueError) as exc:
                return None, b"", b"", time.perf_counter_ns() - started, str(exc)
            return (
                completed.returncode,
                completed.stdout,
                completed.stderr,
                time.perf_counter_ns() - started,
                None,
            )

        for _ in range(warmups):
            for label, command in (("candidate", candidate), ("baseline", baseline)):
                code, _stdout, _stderr, _elapsed, error = run_once(command)
                if error == "timeout":
                    reason = f"SingleCoreTimeout:{label}:warmup"
                elif error is not None:
                    reason = f"SingleCoreExecutionUnavailable:{label}:{error}"
                elif code != 0:
                    reason = f"SingleCoreCommandFailed:{label}:exit={code}"
                else:
                    continue
                return MetricMeasurement.create(
                    metric_id, status=EvidenceStatus.UNAVAILABLE, reason=reason,
                    command=commands, config=config, source_digests=sources,
                    environment=self.environment, artifact_digests=artifacts,
                )

        candidate_samples: list[int] = []
        baseline_samples: list[int] = []
        output_digests: dict[str, str] = {}
        for _ in range(repetitions):
            observations: list[tuple[str, tuple[str, ...], list[int]]] = [
                ("candidate", candidate, candidate_samples),
                ("baseline", baseline, baseline_samples),
            ]
            for label, command, samples in observations:
                code, stdout, stderr, elapsed, error = run_once(command)
                if error == "timeout":
                    reason = f"SingleCoreTimeout:{label}:sample"
                elif error is not None:
                    reason = f"SingleCoreExecutionUnavailable:{label}:{error}"
                elif code != 0:
                    reason = f"SingleCoreCommandFailed:{label}:exit={code}"
                else:
                    current = {
                        f"{label}-stdout": digest_bytes(stdout),
                        f"{label}-stderr": digest_bytes(stderr),
                    }
                    changed = any(
                        key in output_digests and output_digests[key] != digest
                        for key, digest in current.items()
                    )
                    if changed:
                        artifacts.update(output_digests)
                        return MetricMeasurement.create(
                            metric_id, status=EvidenceStatus.UNAVAILABLE,
                            reason=f"SingleCoreOutputChanged:{label}:sample output is not stable",
                            command=commands, config=config, source_digests=sources,
                            environment=self.environment, artifact_digests=artifacts,
                        )
                    output_digests.update(current)
                    samples.append(elapsed)
                    continue
                return MetricMeasurement.create(
                    metric_id, status=EvidenceStatus.UNAVAILABLE, reason=reason,
                    command=commands, config=config, source_digests=sources,
                    environment=self.environment, artifact_digests=artifacts,
                )

        if output_digests["candidate-stdout"] != output_digests["baseline-stdout"]:
            artifacts.update(output_digests)
            return MetricMeasurement.create(
                metric_id, status=EvidenceStatus.UNAVAILABLE,
                reason="SingleCoreOutputMismatch:pinned comparison outputs differ",
                command=commands, config=config, source_digests=sources,
                environment=self.environment, artifact_digests=artifacts,
            )
        artifacts.update(output_digests)
        candidate_median = summarize_samples(candidate_samples).median_ns
        baseline_median = summarize_samples(baseline_samples).median_ns
        return MetricMeasurement.create(
            metric_id, status=EvidenceStatus.MEASURED,
            command=commands, config=config, source_digests=sources,
            environment=self.environment, artifact_digests=artifacts,
            samples=tuple(candidate_samples + baseline_samples),
            ratio=(candidate_median, baseline_median),
        )


    def bend_comparison_metric(
        self,
        samples: Iterable[int] | None = None,
        ratio: tuple[int, int] | None = None,
        *,
        repository: str = BEND_PUBLIC_REPOSITORY,
        revision: str = BEND_PUBLIC_REVISION,
        remote_result: str | None = None,
    ) -> MetricMeasurement:
        """Record a Bend comparison only when its public revision is pinned.

        ``remote_result`` is an injected ``git ls-remote`` result (or an exact
        commit line) so normal evidence collection never performs network I/O.
        """
        if not repository or not revision:
            return MetricMeasurement.create(
                METRIC_BEND_COMPARISON,
                status=EvidenceStatus.UNAVAILABLE,
                reason="BendPublicRevisionNotConfigured",
                command=self.command,
                config={"runner": self.config, "repository": repository, "revision": revision},
                environment=self.environment,
            )
        try:
            validate_bend_public_revision(repository, revision, remote_result=remote_result)
        except ValueError as exc:
            return MetricMeasurement.create(
                METRIC_BEND_COMPARISON,
                status=EvidenceStatus.UNAVAILABLE,
                reason=f"BendPublicRevisionRejected:{exc}",
                command=("git", "ls-remote", repository, "HEAD"),
                config={"runner": self.config, "repository": repository, "revision": revision},
                environment=self.environment,
            )
        values = tuple(samples or ())
        if not values or ratio is None:
            return MetricMeasurement.create(
                METRIC_BEND_COMPARISON,
                status=EvidenceStatus.UNAVAILABLE,
                reason="BendComparisonSamplesUnavailable",
                command=("git", "ls-remote", repository, "HEAD"),
                config={"runner": self.config, "repository": repository, "revision": revision},
                environment=self.environment,
                source_digests={f"{repository}@{revision}": digest_text(revision)},
            )
        return MetricMeasurement.create(
            METRIC_BEND_COMPARISON,
            status=EvidenceStatus.MEASURED,
            command=("git", "ls-remote", repository, "HEAD"),
            config={"runner": self.config, "repository": repository, "revision": revision},
            environment=self.environment,
            source_digests={f"{repository}@{revision}": digest_text(revision)},
            samples=values,
            ratio=ratio,
        )

    def run(
        self,
        *,
        measurements: Mapping[str, MetricMeasurement] | None = None,
        application_builds: Sequence[ApplicationBuildObservation] | None = None,
        verification_compilations: Mapping[str, Any] | Sequence[tuple[str, Any]] | None = None,
        verification_metrics: VerificationMetricsReport | None = None,
        capabilities: BackendCapabilities | Mapping[str, Any] | None = None,
        collect_applications: bool = False,
        single_core_candidate: PerformanceEvidenceManifest | None = None,
        single_core_baseline: PerformanceEvidenceManifest | None = None,
        single_core_candidate_command: Sequence[str] | None = None,
        single_core_baseline_command: Sequence[str] | None = None,
        single_core_repetitions: int = 30,
        single_core_warmups: int = 5,
        single_core_timeout_seconds: float = 30.0,
        single_core_input_data: bytes | str | None = None,
        single_core_cwd: str | Path | None = None,
        single_core_affinity_cpu: int | None = None,
        multicore_candidate: PerformanceEvidenceManifest | None = None,
        multicore_baseline: PerformanceEvidenceManifest | None = None,
        multicore_measure: Any | None = None,
        multicore_core_count: int | None = None,
        multicore_repetitions: int = 5,
        multicore_warmups: int = 1,
        multicore_affinity: Iterable[int] | None = None,
        memory_safety_samples: Iterable[int] | None = None,
        unrelated_edit_observations: Iterable[SemanticEditAuditObservation | Mapping[str, Any]] | None = None,
        unrelated_edit_seed: int | None = None,
        memory_safety_corpus: Sequence[MemorySafetyCorpusCase | Mapping[str, Any]] | str | Path | None = None,
        memory_safety_sanitizers: Sequence[str] = ("asan", "ubsan", "lsan"),
        memory_safety_toolchains: Mapping[str, str] | str | None = None,
        memory_safety_timeout_seconds: float = 30.0,
        memory_safety_resource_limits: Mapping[str, int] | None = None,
        repeated_artifact_digests: Sequence[str] | None = None,
        repeated_builds: Sequence[Mapping[str, Any]] | None = None,
        collect_repeated_builds: bool = False,
        repair_histories: Any | None = None,
        ai_evidence: Any | None = None,
        ai_records: Iterable[Any] | None = None,
        ai_tokenizer_contract: Mapping[str, Any] | None = None,
        gpu_samples: Iterable[int] | None = None,
        gpu_ratio: tuple[int, int] | None = None,
        gpu_paired_samples: Iterable[tuple[int, int]] | None = None,
        gpu_backend_probe: Mapping[str, Any] | None = None,
        bend_samples: Iterable[int] | None = None,
        bend_ratio: tuple[int, int] | None = None,
        bend_repository: str = BEND_PUBLIC_REPOSITORY,
        bend_revision: str = BEND_PUBLIC_REVISION,
        bend_remote_result: str | None = None,
    ) -> FrozenEvidenceReport:
        supplied = dict(measurements or {})
        if METRIC_APPLICATION_BUILDS not in supplied:
            if collect_applications and application_builds is None:
                supplied[METRIC_APPLICATION_BUILDS] = self.collect_application_build_metric()
            else:
                supplied[METRIC_APPLICATION_BUILDS] = self.application_build_metric(
                    tuple(application_builds or ())
                )
        if METRIC_AUTOMATIC_PROOF_CLOSURE not in supplied:
            if verification_compilations is not None:
                supplied[METRIC_AUTOMATIC_PROOF_CLOSURE] = (
                    self.proof_closure_metric_from_compilations(verification_compilations)
                )
            elif verification_metrics is not None:
                supplied[METRIC_AUTOMATIC_PROOF_CLOSURE] = self.unavailable(
                    METRIC_AUTOMATIC_PROOF_CLOSURE,
                    "ProofClosureArtifactsRequired",
                )
            else:
                supplied[METRIC_AUTOMATIC_PROOF_CLOSURE] = self.proof_closure_metric(None)
        if METRIC_SUPPORTED_GPU_RATIO not in supplied:
            supplied[METRIC_SUPPORTED_GPU_RATIO] = self.gpu_metric(
                gpu_samples,
                capabilities=capabilities,
                ratio=gpu_ratio,
                paired_samples=gpu_paired_samples,
                backend_probe=gpu_backend_probe,
            )
        if METRIC_SINGLE_CORE_NATIVE_RATIO not in supplied:
            if single_core_candidate_command is not None or single_core_baseline_command is not None:
                supplied[METRIC_SINGLE_CORE_NATIVE_RATIO] = self.collect_single_core_native_ratio(
                    single_core_candidate_command,
                    single_core_baseline_command,
                    repetitions=single_core_repetitions,
                    warmups=single_core_warmups,
                    timeout_seconds=single_core_timeout_seconds,
                    input_data=single_core_input_data,
                    cwd=single_core_cwd,
                    affinity_cpu=single_core_affinity_cpu,
                )
            else:
                supplied[METRIC_SINGLE_CORE_NATIVE_RATIO] = self.single_core_native_ratio(
                    single_core_candidate,
                    single_core_baseline,
                )
        if METRIC_MULTICORE_SCALING not in supplied:
            supplied[METRIC_MULTICORE_SCALING] = (
                self.collect_multicore_scaling(
                    multicore_measure,
                    configured_core_count=multicore_core_count,
                    repetitions=multicore_repetitions,
                    warmups=multicore_warmups,
                    affinity=multicore_affinity,
                )
                if multicore_measure is not None
                else self.multicore_scaling(multicore_candidate, multicore_baseline)
            )
        if METRIC_MEMORY_SAFETY_CORPUS not in supplied:
            memory_result = (
                self.collect_memory_safety_corpus(
                    memory_safety_corpus,
                    sanitizers=memory_safety_sanitizers,
                    toolchains=memory_safety_toolchains,
                    timeout_seconds=memory_safety_timeout_seconds,
                    resource_limits=memory_safety_resource_limits,
                )
                if memory_safety_corpus is not None
                else None
            )
            supplied[METRIC_MEMORY_SAFETY_CORPUS] = self.memory_safety_metric(memory_safety_samples, result=memory_result)
        if METRIC_UNRELATED_SEMANTIC_EDIT_AUDIT not in supplied:
            supplied[METRIC_UNRELATED_SEMANTIC_EDIT_AUDIT] = self.unrelated_semantic_edit_metric(
                unrelated_edit_observations,
                seed=unrelated_edit_seed,
            )
        if METRIC_DETERMINISTIC_REPEATED_BUILDS not in supplied:
            records = (
                self.collect_repeated_builds()
                if collect_repeated_builds and repeated_builds is None
                else repeated_builds
            )
            supplied[METRIC_DETERMINISTIC_REPEATED_BUILDS] = self.deterministic_build_metric(
                repeated_artifact_digests or (),
                repeated_builds=records,
            )
        if METRIC_REPAIR_ITERATION_REDUCTION not in supplied:
            supplied[METRIC_REPAIR_ITERATION_REDUCTION] = self.repair_iteration_metric(repair_histories)
        if METRIC_AI_CONTEXT_REDUCTION not in supplied:
            supplied[METRIC_AI_CONTEXT_REDUCTION] = self.ai_context_reduction_metric(
                ai_evidence,
                records=ai_records,
                tokenizer_contract=ai_tokenizer_contract,
            )
        if METRIC_BEND_COMPARISON not in supplied:
            supplied[METRIC_BEND_COMPARISON] = self.bend_comparison_metric(
                bend_samples,
                bend_ratio,
                repository=bend_repository,
                revision=bend_revision,
                remote_result=bend_remote_result,
            )
        for metric_id in REQUIRED_METRICS:
            supplied.setdefault(metric_id, self.unavailable(metric_id, "MeasurementNotInjected"))
        return FrozenEvidenceReport(tuple(supplied[metric_id] for metric_id in sorted(REQUIRED_METRICS)))


def validate_bend_public_revision(
    repository: str,
    revision: str,
    *,
    remote_result: str | None = None,
    resolver: Any | None = None,
) -> str:
    """Validate a public Bend repository and immutable full commit identity.

    A resolver or captured ``git ls-remote`` result is required deliberately:
    ordinary evidence collection must not turn an unavailable network into a
    fabricated benchmark. Branches, tags, abbreviated hashes, and a remote
    result that resolves to another commit are rejected.
    """
    if (
        not isinstance(repository, str)
        or not repository.startswith("https://")
        or len(repository) <= len("https://")
        or any(char in repository for char in "\r\n")
    ):
        raise ValueError("PublicRepositoryMustBeHTTPS")
    if (
        not isinstance(revision, str)
        or len(revision) != 40
        or revision != revision.lower()
        or any(char not in "0123456789abcdef" for char in revision)
    ):
        raise ValueError("RevisionMustBeExact40Hex")
    if resolver is not None:
        try:
            remote_result = resolver(repository, revision)
        except Exception as exc:
            raise ValueError(f"RevisionResolverUnavailable:{exc}") from exc
    if not isinstance(remote_result, str) or not remote_result.strip():
        raise ValueError("RemoteRevisionVerificationUnavailable")
    matches: list[str] = []
    for line in remote_result.splitlines():
        fields = line.split()
        if fields and len(fields[0]) == 40 and all(char in "0123456789abcdefABCDEF" for char in fields[0]):
            matches.append(fields[0].lower())
    if not matches:
        raise ValueError("RemoteRevisionNotFound")
    if revision not in matches:
        raise ValueError("RemoteRevisionMismatch")
    return revision


def validate_frozen_evidence(value: FrozenEvidenceReport | Mapping[str, Any] | str) -> FrozenEvidenceReport:
    if isinstance(value, FrozenEvidenceReport):
        return FrozenEvidenceReport.from_json(value.to_json())
    if isinstance(value, str):
        return FrozenEvidenceReport.from_json(value)
    return FrozenEvidenceReport.from_dict(value)


def validate_staleness(report: FrozenEvidenceReport, **kwargs: Any) -> None:
    report.validate_current(**kwargs)


# Descriptive aliases used by callers that prefer "record" or "manifest" language.
FrozenEvidence = FrozenEvidenceReport
EvidenceMetric = MetricMeasurement
FrozenEvidenceManifest = FrozenEvidenceReport

__all__ = [
    "CONTRACT", "SCHEMA_VERSION", "MINIMUM_APPLICATION_BUILDS", "MINIMUM_SEMANTIC_EDIT_AUDITS",
    "FROZEN_APPLICATION_COHORT", "REQUIRED_METRICS",
    "METRIC_APPLICATION_BUILDS", "METRIC_SINGLE_CORE_NATIVE_RATIO", "METRIC_MULTICORE_SCALING",
    "METRIC_SUPPORTED_GPU_RATIO", "METRIC_AUTOMATIC_PROOF_CLOSURE", "METRIC_MEMORY_SAFETY_CORPUS",
    "METRIC_UNRELATED_SEMANTIC_EDIT_AUDIT", "METRIC_DETERMINISTIC_REPEATED_BUILDS",
    "METRIC_REPAIR_ITERATION_REDUCTION", "METRIC_AI_CONTEXT_REDUCTION", "METRIC_BEND_COMPARISON",
    "BEND_PUBLIC_REPOSITORY", "BEND_PUBLIC_REVISION",
    "EvidenceStatus", "MetricMeasurement", "EvidenceMetric", "ApplicationBuildObservation",
    "SemanticEditAuditObservation", "MemorySafetyCorpusCase", "MemorySafetyRun", "MemorySafetyCorpusResult",
    "FrozenEvidenceReport", "FrozenEvidence", "FrozenEvidenceManifest", "FrozenEvidenceRunner",
    "digest_value", "digest_bytes", "digest_text", "digest_file", "detect_gpu_backend",
    "run_memory_safety_corpus", "derive_repair_iteration_metric", "validate_bend_public_revision",
    "validate_frozen_evidence", "validate_staleness",
]
