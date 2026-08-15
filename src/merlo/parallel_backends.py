"""Deterministic capability discovery and target selection for parallel backends.

The module deliberately has no backend implementation dependencies. A caller may
provide a complete capability fixture (recommended for reproducible builds), or
omit it to use two documented local probes: ``platform.machine`` and
``os.cpu_count`` for CPU capabilities, and ``importlib.util.find_spec`` for
optional GPU/HVM integrations. Optional packages are never imported.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import platform
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

BACKEND_SCHEMA_VERSION = 1
BACKEND_CONTRACT = "merlo.parallel-backends.v1"


class BackendTarget(str, Enum):
    SCALAR_CPU = "scalar_cpu"
    VECTOR_CPU = "vector_cpu"
    MULTICORE_CPU = "multicore_cpu"
    GPU = "gpu"
    HVM = "hvm"


BACKEND_TARGETS = tuple(item.value for item in BackendTarget)
# This order is part of the public deterministic selection contract.
TARGET_PRECEDENCE = (
    BackendTarget.GPU.value,
    BackendTarget.HVM.value,
    BackendTarget.MULTICORE_CPU.value,
    BackendTarget.VECTOR_CPU.value,
    BackendTarget.SCALAR_CPU.value,
)
GPU_INTEGRATION_MODULES = ("cupy", "torch", "numba")
HVM_INTEGRATION_MODULES = ("hvm", "llvmlite", "wasmtime")

_CAPABILITY_KEYS = {"target", "available", "reason", "provider", "version", "metadata"}
_SNAPSHOT_KEYS = {"schema_version", "contract", "capabilities", "digest"}
_SELECTION_KEYS = {
    "schema_version", "contract", "selected_target", "requested_target",
    "capabilities_digest", "fallback", "reason", "digest",
}
_RESULT_KEYS = {
    "schema_version", "contract", "target", "status", "output", "error",
    "metadata", "digest",
}


def _json(value: Any) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("InvalidBackendJSON") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _target_name(value: Any) -> str:
    if isinstance(value, BackendTarget):
        return value.value
    if type(value) is str:
        return value
    raise ValueError("InvalidBackendTarget")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("InvalidBackendMetadataKey")
        return MappingProxyType({key: _freeze(value[key]) for key in sorted(value)})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NonFiniteBackendNumber")
    if value is None or type(value) in {str, int, bool, float}:
        return value
    raise ValueError("InvalidBackendMetadata")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _validate_digest(value: Any, code: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(code)
    return value


@dataclass(frozen=True)
class BackendCapability:
    """Immutable availability claim for one backend target."""

    target: str
    available: bool
    reason: str | None = None
    provider: str | None = None
    version: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        target = _target_name(self.target)
        if target not in BACKEND_TARGETS:
            raise ValueError("UnknownBackendTarget")
        object.__setattr__(self, "target", target)
        if type(self.available) is not bool:
            raise ValueError("InvalidBackendAvailability")
        for name in ("reason", "provider", "version"):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or not value):
                raise ValueError(f"InvalidBackend{name.title()}")
        if not self.available and self.reason is None:
            raise ValueError("UnavailableBackendNeedsReason")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("InvalidBackendMetadata")
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "available": self.available,
            "reason": self.reason,
            "provider": self.provider,
            "version": self.version,
            "metadata": _thaw(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BackendCapability":
        if not isinstance(value, Mapping) or set(value) != _CAPABILITY_KEYS:
            raise ValueError("BackendCapabilitySchemaMismatch")
        return cls(
            value["target"], value["available"], value["reason"], value["provider"],
            value["version"], value["metadata"],
        )


@dataclass(frozen=True)
class BackendCapabilities:
    """Canonical, digest-bound capability snapshot."""

    capabilities: tuple[BackendCapability, ...]
    schema_version: int = BACKEND_SCHEMA_VERSION
    contract: str = BACKEND_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != BACKEND_SCHEMA_VERSION:
            raise ValueError("BackendCapabilitiesVersionMismatch")
        if self.contract != BACKEND_CONTRACT:
            raise ValueError("BackendCapabilitiesContractMismatch")
        if not isinstance(self.capabilities, tuple):
            raise ValueError("BackendCapabilitiesNotImmutable")
        if any(not isinstance(item, BackendCapability) for item in self.capabilities):
            raise ValueError("BackendCapabilityTypeMismatch")
        names = tuple(item.target for item in self.capabilities)
        if len(set(names)) != len(names) or set(names) != set(BACKEND_TARGETS):
            raise ValueError("BackendCapabilitiesTargetSetMismatch")
        if names != tuple(sorted(names, key=BACKEND_TARGETS.index)):
            raise ValueError("BackendCapabilitiesOrderMismatch")
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise ValueError("BackendCapabilitiesDigestMismatch")
        object.__setattr__(self, "digest", expected)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "capabilities": [item.to_dict() for item in self.capabilities],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BackendCapabilities":
        if not isinstance(value, Mapping) or set(value) != _SNAPSHOT_KEYS:
            raise ValueError("BackendCapabilitiesSchemaMismatch")
        if type(value.get("capabilities")) is not list:
            raise ValueError("BackendCapabilitiesSchemaMismatch")
        payload = {key: value[key] for key in _SNAPSHOT_KEYS if key != "digest"}
        if _validate_digest(value.get("digest"), "BackendCapabilitiesDigestMalformed") != _digest(payload):
            raise ValueError("BackendCapabilitiesDigestMismatch")
        try:
            capabilities = tuple(BackendCapability.from_dict(item) for item in value["capabilities"])
            return cls(capabilities, value["schema_version"], value["contract"], value["digest"])
        except (TypeError, KeyError) as exc:
            raise ValueError("BackendCapabilitiesSchemaMismatch") from exc

    @classmethod
    def from_json(cls, value: str) -> "BackendCapabilities":
        try:
            raw = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("BackendCapabilitiesSchemaMismatch") from exc
        return cls.from_dict(raw)

    def for_target(self, target: str | BackendTarget) -> BackendCapability:
        name = _target_name(target)
        if name not in BACKEND_TARGETS:
            raise ValueError("UnknownBackendTarget")
        return next(item for item in self.capabilities if item.target == name)


# Descriptive alias used by callers that call a report a snapshot.
CapabilitySnapshot = BackendCapabilities


def _capability_from_value(target: str, value: Any) -> BackendCapability:
    if type(value) is bool:
        return BackendCapability(target, value, None if value else "CapabilityUnavailable", "builtin" if value else None)
    if not isinstance(value, Mapping):
        raise ValueError("BackendCapabilitySchemaMismatch")
    allowed = {"available", "reason", "provider", "version", "metadata"}
    if set(value) - allowed or "available" not in value:
        raise ValueError("BackendCapabilitySchemaMismatch")
    available = value["available"]
    reason = value.get("reason")
    if type(available) is not bool:
        raise ValueError("InvalidBackendAvailability")
    if not available and reason is None:
        reason = "CapabilityUnavailable"
    return BackendCapability(
        target, available, reason, value.get("provider"), value.get("version"), value.get("metadata", {})
    )


def _explicit_capabilities(data: Mapping[str, Any]) -> BackendCapabilities:
    if not isinstance(data, Mapping) or not data:
        raise ValueError("BackendCapabilityDataSchemaMismatch")
    if set(data) - set(BACKEND_TARGETS):
        raise ValueError("UnknownBackendTarget")
    values: list[BackendCapability] = []
    for target in BACKEND_TARGETS:
        if target == BackendTarget.SCALAR_CPU.value and target not in data:
            values.append(BackendCapability(target, True, provider="builtin"))
        elif target not in data:
            values.append(BackendCapability(target, False, "CapabilityNotSupplied"))
        else:
            values.append(_capability_from_value(target, data[target]))
    if not values[0].available:
        raise ValueError("ScalarCPUUnavailable")
    return BackendCapabilities(tuple(values))


def _module_present(module_names: tuple[str, ...]) -> str | None:
    for name in module_names:
        try:
            if importlib.util.find_spec(name) is not None:
                return name
        except (ImportError, ModuleNotFoundError, ValueError):
            continue
    return None


def _probe_local_platform(platform_probe: Mapping[str, Any] | None = None) -> BackendCapabilities:
    """Probe only documented host facts; ``platform_probe`` is test injection."""
    probe = platform_probe or {}
    if not isinstance(probe, Mapping):
        raise ValueError("PlatformProbeSchemaMismatch")
    allowed = {"machine", "cpu_count", "gpu_modules", "hvm_modules"}
    if set(probe) - allowed:
        raise ValueError("PlatformProbeSchemaMismatch")
    machine = probe.get("machine", platform.machine())
    cpu_count = probe.get("cpu_count", os.cpu_count())
    if type(machine) is not str or not machine:
        raise ValueError("PlatformProbeSchemaMismatch")
    if cpu_count is not None and (type(cpu_count) is not int or cpu_count < 1):
        raise ValueError("PlatformProbeSchemaMismatch")
    normalized = machine.lower().replace("-", "_")
    vector_architectures = {"x86_64", "amd64", "aarch64", "arm64", "ppc64le", "s390x"}
    vector = normalized in vector_architectures
    multicore = (cpu_count or 1) > 1
    gpu_module = probe.get("gpu_modules")
    hvm_module = probe.get("hvm_modules")
    if gpu_module is None:
        gpu_module = _module_present(GPU_INTEGRATION_MODULES)
    elif type(gpu_module) is not str and gpu_module is not False:
        raise ValueError("PlatformProbeSchemaMismatch")
    if hvm_module is None:
        hvm_module = _module_present(HVM_INTEGRATION_MODULES)
    elif type(hvm_module) is not str and hvm_module is not False:
        raise ValueError("PlatformProbeSchemaMismatch")
    return BackendCapabilities(
        (
            BackendCapability("scalar_cpu", True, provider="builtin", metadata={"machine": normalized}),
            BackendCapability(
                "vector_cpu", vector, None if vector else "VectorInstructionsUnavailable", provider="builtin" if vector else None,
                metadata={"machine": normalized},
            ),
            BackendCapability(
                "multicore_cpu", multicore, None if multicore else "SingleCPUCore", provider="builtin" if multicore else None,
                metadata={"cpu_count": cpu_count or 1},
            ),
            BackendCapability(
                "gpu", bool(gpu_module), None if gpu_module else "GPUIntegrationMissing", provider=gpu_module or None,
                metadata={"probed_modules": list(GPU_INTEGRATION_MODULES)},
            ),
            BackendCapability(
                "hvm", bool(hvm_module), None if hvm_module else "HVMIntegrationMissing", provider=hvm_module or None,
                metadata={"probed_modules": list(HVM_INTEGRATION_MODULES)},
            ),
        )
    )


def discover_capabilities(
    capability_data: Mapping[str, Any] | BackendCapabilities | None = None,
    *,
    platform_probe: Mapping[str, Any] | None = None,
) -> BackendCapabilities:
    """Return an immutable snapshot from explicit data or documented probes.

    Supplying ``capability_data`` disables all host inspection. Supplying only
    ``platform_probe`` injects host facts while retaining the documented probe
    shape, making selection tests independent of installed packages.
    """
    if isinstance(capability_data, Mapping) and set(capability_data) == _SNAPSHOT_KEYS:
        return BackendCapabilities.from_dict(capability_data)
    if isinstance(capability_data, BackendCapabilities):
        return capability_data
    if capability_data is not None:
        if platform_probe is not None:
            raise ValueError("ConflictingBackendCapabilitySources")
        return _explicit_capabilities(capability_data)
    return _probe_local_platform(platform_probe)


@dataclass(frozen=True)
class BackendSelection:
    selected_target: str
    requested_target: str | None
    capabilities_digest: str
    fallback: bool
    reason: str
    schema_version: int = BACKEND_SCHEMA_VERSION
    contract: str = BACKEND_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        if self.selected_target not in BACKEND_TARGETS:
            raise ValueError("UnknownBackendTarget")
        if self.requested_target is not None and self.requested_target not in BACKEND_TARGETS:
            raise ValueError("UnknownBackendTarget")
        _validate_digest(self.capabilities_digest, "InvalidCapabilitiesDigest")
        if type(self.fallback) is not bool or type(self.reason) is not str or not self.reason:
            raise ValueError("InvalidBackendSelection")
        if self.schema_version != BACKEND_SCHEMA_VERSION or self.contract != BACKEND_CONTRACT:
            raise ValueError("BackendSelectionContractMismatch")
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise ValueError("BackendSelectionDigestMismatch")
        object.__setattr__(self, "digest", expected)

    @property
    def target(self) -> str:
        return self.selected_target

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "selected_target": self.selected_target,
            "requested_target": self.requested_target,
            "capabilities_digest": self.capabilities_digest,
            "fallback": self.fallback,
            "reason": self.reason,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BackendSelection":
        if not isinstance(value, Mapping) or set(value) != _SELECTION_KEYS:
            raise ValueError("BackendSelectionSchemaMismatch")
        payload = {key: value[key] for key in _SELECTION_KEYS if key != "digest"}
        if _validate_digest(value.get("digest"), "BackendSelectionDigestMalformed") != _digest(payload):
            raise ValueError("BackendSelectionDigestMismatch")
        return cls(
            value["selected_target"], value["requested_target"], value["capabilities_digest"], value["fallback"],
            value["reason"], value["schema_version"], value["contract"], value["digest"],
        )


def select_backend(
    capabilities: BackendCapabilities | Mapping[str, Any] | None = None,
    *,
    requested_target: str | BackendTarget | None = None,
    requested: str | BackendTarget | None = None,
    capability_data: Mapping[str, Any] | None = None,
) -> BackendSelection:
    """Select the highest-precedence available target, or scalar CPU."""
    if capabilities is not None and capability_data is not None:
        raise ValueError("ConflictingBackendCapabilitySources")
    source = capability_data if capability_data is not None else capabilities
    report = discover_capabilities(source)
    if requested_target is not None and requested is not None and _target_name(requested_target) != _target_name(requested):
        raise ValueError("ConflictingRequestedTargets")
    requested_value = requested_target if requested_target is not None else requested
    requested_name = None if requested_value is None else _target_name(requested_value)
    if requested_name is not None and requested_name not in BACKEND_TARGETS:
        raise ValueError("UnknownBackendTarget")
    if requested_name is not None:
        capability = report.for_target(requested_name)
        if not capability.available:
            raise ValueError(f"RequestedBackendUnavailable:{requested_name}")
        selected, fallback, reason = requested_name, False, "RequestedTarget"
    else:
        # HVM is experimental and may only be selected by an explicit request.
        selected = next(
            (
                target for target in TARGET_PRECEDENCE
                if target != BackendTarget.HVM.value and report.for_target(target).available
            ),
            "scalar_cpu",
        )
        if not report.for_target(selected).available:
            raise ValueError("ScalarCPUUnavailable")
        fallback, reason = selected == "scalar_cpu", (
            "ScalarFallback" if selected == "scalar_cpu" else "HighestPrecedenceAvailable"
        )
    if not report.for_target(selected).available:
        raise ValueError("SelectedBackendUnavailable")
    return BackendSelection(selected, requested_name, report.digest, fallback, reason)


@dataclass(frozen=True)
class BackendResult:
    """Digest-bound immutable result returned by a backend adapter."""

    target: str
    status: str
    output: Any = None
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = BACKEND_SCHEMA_VERSION
    contract: str = BACKEND_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        target = _target_name(self.target)
        if target not in BACKEND_TARGETS:
            raise ValueError("UnknownBackendTarget")
        object.__setattr__(self, "target", target)
        if self.status not in {"ok", "error", "unavailable"}:
            raise ValueError("InvalidBackendResultStatus")
        if self.status == "ok" and self.error is not None:
            raise ValueError("SuccessfulBackendHasError")
        if self.status in {"error", "unavailable"}:
            if self.output is not None:
                raise ValueError("FailedBackendHasOutput")
            if type(self.error) is not str or not self.error:
                raise ValueError("FailedBackendNeedsReason")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("InvalidBackendMetadata")
        object.__setattr__(self, "output", _freeze(self.output))
        object.__setattr__(self, "metadata", _freeze(self.metadata))
        if self.schema_version != BACKEND_SCHEMA_VERSION or self.contract != BACKEND_CONTRACT:
            raise ValueError("BackendResultContractMismatch")
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise ValueError("BackendResultDigestMismatch")
        object.__setattr__(self, "digest", expected)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "target": self.target,
            "status": self.status,
            "output": _thaw(self.output),
            "error": self.error,
            "metadata": _thaw(self.metadata),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BackendResult":
        if not isinstance(value, Mapping) or set(value) != _RESULT_KEYS:
            raise ValueError("BackendResultSchemaMismatch")
        payload = {key: value[key] for key in _RESULT_KEYS if key != "digest"}
        if _validate_digest(value.get("digest"), "BackendResultDigestMalformed") != _digest(payload):
            raise ValueError("BackendResultDigestMismatch")
        return cls(
            value["target"], value["status"], value["output"], value["error"], value["metadata"],
            value["schema_version"], value["contract"], value["digest"],
        )


BackendAdapterResult = BackendResult


@runtime_checkable
class BackendAdapter(Protocol):
    """Provider-neutral adapter contract; implementations live outside discovery."""

    target: BackendTarget

    def execute(self, request: Mapping[str, Any]) -> BackendResult:
        """Execute an immutable request and return a digest-bound result."""
        ...


PARALLEL_BACKEND_ARTIFACT_SCHEMA_VERSION = 1
GPU_ARTIFACT_CONTRACT = "merlo.parallel-gpu-artifact.v1"
HVM_ARTIFACT_CONTRACT = "merlo.parallel-hvm-artifact.v1"
GPU_SUPPORTED_OPERATIONS = frozenset(
    {"map", "zip", "reduce", "scan", "filter", "gather", "scatter", "fused_map_zip"}
)
# HVM deliberately has the same small, pure collection subset as GPU.  Keeping
# this set explicit prevents a future scalar/effectful operation from silently
# becoming HVM IR.
HVM_SUPPORTED_OPERATIONS = GPU_SUPPORTED_OPERATIONS
_ARTIFACT_KEYS = {
    "schema_version", "contract", "target", "format", "source_digest",
    "content", "content_digest", "selected_adapter", "required_capabilities",
    "experimental", "artifact_digest",
}


@dataclass(frozen=True)
class BackendLoweringDiagnostic:
    """Stable machine-readable reason for rejecting a lowering request."""

    code: str
    target: str
    operation_id: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "target": self.target,
            "operation_id": self.operation_id,
            "detail": self.detail,
        }


class BackendLoweringError(ValueError):
    """A deterministic lowering rejection, never an optional-library error."""

    def __init__(
        self,
        code: str,
        target: str,
        operation_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.diagnostic = BackendLoweringDiagnostic(code, target, operation_id, detail)
        suffix = f": {detail}" if detail else ""
        operation = f" [{operation_id}]" if operation_id else ""
        super().__init__(f"{code}{operation}{suffix}")


def _artifact_digest(value: Mapping[str, Any]) -> str:
    return _digest(value)


@dataclass(frozen=True)
class ParallelBackendArtifact:
    """Canonical, digest-bound textual artifact emitted by a pure lowering."""

    target: str
    format: str
    source_digest: str
    content: str
    selected_adapter: str
    required_capabilities: tuple[str, ...]
    experimental: bool = False
    schema_version: int = PARALLEL_BACKEND_ARTIFACT_SCHEMA_VERSION
    contract: str = GPU_ARTIFACT_CONTRACT
    content_digest: str = ""
    artifact_digest: str = ""

    def __post_init__(self) -> None:
        target = _target_name(self.target)
        if target not in {BackendTarget.GPU.value, BackendTarget.HVM.value}:
            raise ValueError("BackendArtifactTargetMismatch")
        object.__setattr__(self, "target", target)
        expected_contract = (
            GPU_ARTIFACT_CONTRACT if self.target == BackendTarget.GPU.value else HVM_ARTIFACT_CONTRACT
        )
        if self.schema_version != PARALLEL_BACKEND_ARTIFACT_SCHEMA_VERSION:
            raise ValueError("BackendArtifactSchemaMismatch")
        if self.contract != expected_contract:
            raise ValueError("BackendArtifactContractMismatch")
        if type(self.format) is not str or not self.format:
            raise ValueError("BackendArtifactFormatMismatch")
        _validate_digest(self.source_digest, "BackendArtifactSourceDigestMalformed")
        if type(self.content) is not str:
            raise ValueError("BackendArtifactContentMismatch")
        if type(self.selected_adapter) is not str or not self.selected_adapter:
            raise ValueError("BackendArtifactAdapterMismatch")
        if not isinstance(self.required_capabilities, tuple):
            object.__setattr__(self, "required_capabilities", tuple(self.required_capabilities))
        if (
            any(type(item) is not str or not item for item in self.required_capabilities)
            or tuple(sorted(set(self.required_capabilities))) != self.required_capabilities
        ):
            raise ValueError("BackendArtifactCapabilitiesMismatch")
        if type(self.experimental) is not bool:
            raise ValueError("BackendArtifactExperimentalMismatch")
        if self.experimental is not (self.target == BackendTarget.HVM.value):
            raise ValueError("BackendArtifactExperimentalMismatch")
        content_digest = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_digest and self.content_digest != content_digest:
            raise ValueError("BackendArtifactContentDigestMismatch")
        object.__setattr__(self, "content_digest", content_digest)
        expected = _artifact_digest(self._payload())
        if self.artifact_digest and self.artifact_digest != expected:
            raise ValueError("BackendArtifactDigestMismatch")
        object.__setattr__(self, "artifact_digest", expected)

    @property
    def schema(self) -> int:
        return self.schema_version

    @property
    def gpu_adapter(self) -> str | None:
        return self.selected_adapter if self.target == BackendTarget.GPU.value else None

    @property
    def backend(self) -> str:
        return self.target

    @property
    def adapter(self) -> str:
        return self.selected_adapter

    @property
    def digest(self) -> str:
        return self.artifact_digest

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "target": self.target,
            "format": self.format,
            "source_digest": self.source_digest,
            "content": self.content,
            "content_digest": self.content_digest,
            "selected_adapter": self.selected_adapter,
            "required_capabilities": list(self.required_capabilities),
            "experimental": self.experimental,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "artifact_digest": self.artifact_digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ParallelBackendArtifact":
        if not isinstance(value, Mapping) or set(value) != _ARTIFACT_KEYS:
            raise ValueError("BackendArtifactSchemaMismatch")
        if type(value.get("required_capabilities")) is not list:
            raise ValueError("BackendArtifactSchemaMismatch")
        payload = {key: value[key] for key in _ARTIFACT_KEYS if key != "artifact_digest"}
        if _validate_digest(value.get("artifact_digest"), "BackendArtifactDigestMalformed") != _artifact_digest(payload):
            raise ValueError("BackendArtifactDigestMismatch")
        return cls(
            value["target"],
            value["format"],
            value["source_digest"],
            value["content"],
            value["selected_adapter"],
            tuple(value["required_capabilities"]),
            value["experimental"],
            value["schema_version"],
            value["contract"],
            value["content_digest"],
            value["artifact_digest"],
        )

    @classmethod
    def from_json(cls, value: str) -> "ParallelBackendArtifact":
        try:
            raw = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("BackendArtifactSchemaMismatch") from exc
        return cls.from_dict(raw)


GPUArtifact = ParallelBackendArtifact
HVMArtifact = ParallelBackendArtifact

def _coerce_parallel_ir(value: Any) -> Any:
    """Load and revalidate IR so mappings/JSON cannot bypass digest checks."""
    from merlo.parallel_ir import ParallelIR

    if isinstance(value, ParallelIR):
        # Round-tripping also catches mutable mappings hidden inside a caller's
        # hand-built operation before any backend lowering is attempted.
        return ParallelIR.from_dict(value.to_dict())
    if isinstance(value, Mapping):
        return ParallelIR.from_dict(value)
    if isinstance(value, str):
        return ParallelIR.from_json(value)
    raise BackendLoweringError("ParallelIRSchemaMismatch", "parallel")


def _proof_for_operation(operation: Any, target: str) -> None:
    operation_id = operation.operation_id
    if operation.kind == "scalar":
        raise BackendLoweringError(f"{target.upper()}ScalarOperation", target, operation_id)
    supported = GPU_SUPPORTED_OPERATIONS if target == BackendTarget.GPU.value else HVM_SUPPORTED_OPERATIONS
    if operation.kind not in supported:
        raise BackendLoweringError(f"{target.upper()}UnsupportedOperation", target, operation_id, operation.kind)
    if operation.effects:
        raise BackendLoweringError(f"{target.upper()}EffectfulOperation", target, operation_id)
    result_type = operation.result_type
    attrs = operation.attribute_map
    if result_type is None or result_type.shape != "vector" or attrs.get("parallel_shape") == "scalar":
        raise BackendLoweringError(f"{target.upper()}ScalarOperation", target, operation_id)
    if attrs.get("independent") is not True:
        raise BackendLoweringError(f"{target.upper()}IndependenceProofRequired", target, operation_id)
    vector_safe = (
        attrs.get("vector_safe") is True
        or attrs.get("parallel_safe") is True
        or attrs.get("vector") is True
        or attrs.get("execution") == "vector"
    )
    if not vector_safe:
        raise BackendLoweringError(f"{target.upper()}VectorSafetyProofRequired", target, operation_id)


def _lower_operations(ir: Any, target: str) -> tuple[dict[str, Any], ...]:
    for operation in ir.operations:
        _proof_for_operation(operation, target)
    return tuple(operation.to_dict() for operation in ir.operations)


def _capability_for_lowering(
    capabilities: BackendCapabilities | Mapping[str, Any] | None,
    target: str,
) -> BackendCapability:
    report = discover_capabilities(capabilities)
    capability = report.for_target(target)
    if not capability.available:
        code = "GPUUnavailable" if target == BackendTarget.GPU.value else "HVMUnavailable"
        default_detail = "GPUIntegrationMissing" if target == BackendTarget.GPU.value else "HVMIntegrationMissing"
        detail = capability.reason if capability.reason not in {None, "", "CapabilityUnavailable"} else default_detail
        raise BackendLoweringError(code, target, detail=detail)
    return capability


def _adapter_name(capability: BackendCapability, adapter: str | None, target: str) -> str:
    if adapter is not None and (type(adapter) is not str or not adapter):
        raise BackendLoweringError(f"{target.upper()}AdapterInvalid", target)
    return adapter or capability.provider or ("generic-gpu" if target == "gpu" else "hvm")

def lower_gpu(
    ir: Any,
    capabilities: BackendCapabilities | Mapping[str, Any] | None = None,
    *,
    capability_data: Mapping[str, Any] | None = None,
    adapter: str | None = None,
    gpu_adapter: str | None = None,
) -> ParallelBackendArtifact:
    """Lower the proven pure vector subset without importing GPU libraries."""
    if capabilities is not None and capability_data is not None:
        raise BackendLoweringError("GPUCapabilityConflict", "gpu")
    if capability_data is not None:
        capabilities = capability_data
    if adapter is not None and gpu_adapter is not None and adapter != gpu_adapter:
        raise BackendLoweringError("GPUAdapterConflict", "gpu")
    normalized = _coerce_parallel_ir(ir)
    capability = _capability_for_lowering(capabilities, BackendTarget.GPU.value)
    selected = _adapter_name(capability, adapter if adapter is not None else gpu_adapter, "gpu")
    operations = _lower_operations(normalized, BackendTarget.GPU.value)
    required = tuple(sorted({"gpu", "pure-vector", f"gpu-adapter:{selected}"}))
    content = _json(
        {
            "contract": GPU_ARTIFACT_CONTRACT,
            "source_digest": normalized.digest,
            "selected_adapter": selected,
            "required_capabilities": list(required),
            "operations": list(operations),
        }
    )
    return ParallelBackendArtifact(
        "gpu", "canonical-gpu-ir", normalized.digest, content, selected, required, False,
        contract=GPU_ARTIFACT_CONTRACT,
    )


def lower_hvm(
    ir: Any,
    capabilities: BackendCapabilities | Mapping[str, Any] | None = None,
    *,
    capability_data: Mapping[str, Any] | None = None,
    opt_in: bool = False,
    requested: bool | None = None,
    adapter: str | None = None,
) -> ParallelBackendArtifact:
    """Lower the experimental pure subset only after explicit opt-in."""
    if capabilities is not None and capability_data is not None:
        raise BackendLoweringError("HVMCapabilityConflict", "hvm")
    if capability_data is not None:
        capabilities = capability_data
    if requested is not None:
        if type(requested) is not bool:
            raise BackendLoweringError("HVMOptInInvalid", "hvm")
        opt_in = requested
    if opt_in is not True:
        raise BackendLoweringError("HVMOptInRequired", "hvm")
    normalized = _coerce_parallel_ir(ir)
    capability = _capability_for_lowering(capabilities, BackendTarget.HVM.value)
    selected = _adapter_name(capability, adapter, "hvm")
    operations = _lower_operations(normalized, BackendTarget.HVM.value)
    required = tuple(sorted({"hvm", "pure-vector", f"hvm-adapter:{selected}"}))
    # JSON is intentional: it is textual net IR, canonical, and does not
    # depend on the optional HVM package's in-process object model.
    content = _json(
        {
            "contract": HVM_ARTIFACT_CONTRACT,
            "source_digest": normalized.digest,
            "selected_adapter": selected,
            "required_capabilities": list(required),
            "operations": list(operations),
        }
    )
    return ParallelBackendArtifact(
        "hvm", "canonical-hvm-net-ir", normalized.digest, content, selected, required, True,
        contract=HVM_ARTIFACT_CONTRACT,
    )


lower_gpu_ir = lower_gpu
lower_hvm_ir = lower_hvm
lower_gpu_backend = lower_gpu
lower_hvm_backend = lower_hvm
lower_parallel_ir_to_gpu = lower_gpu
lower_parallel_ir_to_hvm = lower_hvm


class GPUBackend:
    target = BackendTarget.GPU

    def lower(self, ir: Any, capabilities: BackendCapabilities | Mapping[str, Any] | None = None, **kwargs: Any) -> ParallelBackendArtifact:
        return lower_gpu(ir, capabilities, **kwargs)

    compile = lower


class HVMBackend:
    target = BackendTarget.HVM

    def __init__(self, *, opt_in: bool = False) -> None:
        self.opt_in = opt_in

    def lower(self, ir: Any, capabilities: BackendCapabilities | Mapping[str, Any] | None = None, **kwargs: Any) -> ParallelBackendArtifact:
        kwargs.setdefault("opt_in", self.opt_in)
        return lower_hvm(ir, capabilities, **kwargs)

    compile = lower


__all__ = [
    "BACKEND_CONTRACT", "BACKEND_SCHEMA_VERSION", "BACKEND_TARGETS",
    "lower_gpu_backend", "lower_hvm_backend", "lower_parallel_ir_to_gpu",
    "lower_parallel_ir_to_hvm",
    "PARALLEL_BACKEND_ARTIFACT_SCHEMA_VERSION", "GPU_ARTIFACT_CONTRACT",
    "HVM_ARTIFACT_CONTRACT", "GPU_SUPPORTED_OPERATIONS", "HVM_SUPPORTED_OPERATIONS",
    "BackendAdapter", "BackendAdapterResult", "BackendCapability", "BackendCapabilities",
    "BackendLoweringDiagnostic", "BackendLoweringError", "BackendResult", "BackendSelection",
    "BackendTarget", "CapabilitySnapshot", "ParallelBackendArtifact", "GPUArtifact", "HVMArtifact",
    "GPUBackend", "HVMBackend", "discover_capabilities", "select_backend",
    "lower_gpu", "lower_hvm", "lower_gpu_ir", "lower_hvm_ir",
]
