"""Deterministic adaptive execution strategy selection.

The selector is deliberately a policy decision, not a semantic transformation. A
workload and backend capability set are immutable, validated values; selection is
therefore reproducible and can be recorded as a canonical JSON digest.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


EXECUTION_STRATEGY_SCHEMA_VERSION = 1
EXECUTION_POLICY_VERSION = "adaptive-v1"


class ExecutionStrategy(str, Enum):
    SCALAR = "scalar"
    VECTOR = "vector"
    MULTICORE = "multicore"
    GPU = "gpu"
    HVM = "hvm"


_STRATEGY_ORDER = (
    ExecutionStrategy.SCALAR,
    ExecutionStrategy.VECTOR,
    ExecutionStrategy.MULTICORE,
    ExecutionStrategy.GPU,
    ExecutionStrategy.HVM,
)
_HEX = frozenset("0123456789abcdef")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _strict_int(value: Any, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"ExecutionStrategyInvalid{name[0].upper() + name[1:]}")
    return value


def _strict_bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"ExecutionStrategyInvalid{name[0].upper() + name[1:]}")
    return value


def _digest_value(value: Any, code: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(code)
    return value


@dataclass(frozen=True)
class WorkloadEstimate:
    """Typed, semantic-neutral estimate used by the selector."""

    item_count: int
    element_width: int
    pure: bool
    associative: bool
    transfer_bytes: int
    operation_count: int
    order_sensitive: bool = False

    def __post_init__(self) -> None:
        _strict_int(self.item_count, "item_count")
        _strict_int(self.element_width, "element_width", minimum=1)
        _strict_bool(self.pure, "pure")
        _strict_bool(self.associative, "associative")
        _strict_int(self.transfer_bytes, "transfer_bytes")
        _strict_int(self.operation_count, "operation_count")
        _strict_bool(self.order_sensitive, "order_sensitive")

    @property
    def effectful(self) -> bool:
        return not self.pure

    @property
    def parallel_safe(self) -> bool:
        return self.pure and self.associative and not self.order_sensitive

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_count": self.item_count,
            "element_width": self.element_width,
            "pure": self.pure,
            "associative": self.associative,
            "transfer_bytes": self.transfer_bytes,
            "operation_count": self.operation_count,
            "order_sensitive": self.order_sensitive,
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WorkloadEstimate":
        expected = {"item_count", "element_width", "pure", "associative", "transfer_bytes", "operation_count", "order_sensitive"}
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise ValueError("ExecutionStrategyWorkloadSchemaMismatch")
        return cls(**dict(payload))

    @classmethod
    def from_json(cls, text: str) -> "WorkloadEstimate":
        try:
            payload = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("ExecutionStrategyInvalidJSON") from exc
        return cls.from_dict(payload)


@dataclass(frozen=True)
class BackendCapabilities:
    """Available execution backends and their explicit model parameters."""

    scalar: bool = True
    vector: bool = False
    multicore: bool = False
    gpu: bool = False
    hvm: bool = False
    vector_lanes: int = 8
    multicore_workers: int = 8
    gpu_transfer_bandwidth_bytes_per_second: int = 12_000_000_000
    gpu_launch_overhead_ns: int = 50_000
    gpu_operations_per_second: int = 100_000_000_000
    hvm_operations_per_second: int = 40_000_000_000

    def __post_init__(self) -> None:
        for name in ("scalar", "vector", "multicore", "gpu", "hvm"):
            _strict_bool(getattr(self, name), name)
        _strict_int(self.vector_lanes, "vector_lanes", minimum=1)
        _strict_int(self.multicore_workers, "multicore_workers", minimum=1)
        _strict_int(self.gpu_transfer_bandwidth_bytes_per_second, "gpu_transfer_bandwidth_bytes_per_second", minimum=1)
        _strict_int(self.gpu_launch_overhead_ns, "gpu_launch_overhead_ns")
        _strict_int(self.gpu_operations_per_second, "gpu_operations_per_second", minimum=1)
        _strict_int(self.hvm_operations_per_second, "hvm_operations_per_second", minimum=1)

    @property
    def supports_scalar(self) -> bool:
        return self.scalar

    @property
    def supports_vector(self) -> bool:
        return self.vector

    @property
    def supports_multicore(self) -> bool:
        return self.multicore

    @property
    def supports_gpu(self) -> bool:
        return self.gpu

    @property
    def supports_hvm(self) -> bool:
        return self.hvm

    def to_dict(self) -> dict[str, Any]:
        return {
            "scalar": self.scalar,
            "vector": self.vector,
            "multicore": self.multicore,
            "gpu": self.gpu,
            "hvm": self.hvm,
            "vector_lanes": self.vector_lanes,
            "multicore_workers": self.multicore_workers,
            "gpu_transfer_bandwidth_bytes_per_second": self.gpu_transfer_bandwidth_bytes_per_second,
            "gpu_launch_overhead_ns": self.gpu_launch_overhead_ns,
            "gpu_operations_per_second": self.gpu_operations_per_second,
            "hvm_operations_per_second": self.hvm_operations_per_second,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BackendCapabilities":
        expected = {"scalar", "vector", "multicore", "gpu", "hvm", "vector_lanes", "multicore_workers", "gpu_transfer_bandwidth_bytes_per_second", "gpu_launch_overhead_ns", "gpu_operations_per_second", "hvm_operations_per_second"}
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise ValueError("ExecutionStrategyCapabilitiesSchemaMismatch")
        return cls(**dict(payload))


@dataclass(frozen=True)
class ExecutionPolicy:
    """Named and versioned thresholds; changing one is a policy change."""

    version: str = EXECUTION_POLICY_VERSION
    vector_min_items: int = 256
    multicore_min_items: int = 8_192
    gpu_min_items: int = 65_536
    hvm_min_operations: int = 1_000_000
    hvm_min_items: int = 4_096
    gpu_min_operations_per_byte: int = 2
    gpu_min_compute_to_transfer_ratio: int = 4
    vector_fixed_overhead_ns: int = 100
    multicore_fixed_overhead_ns: int = 2_000
    hvm_fixed_overhead_ns: int = 10_000

    def __post_init__(self) -> None:
        if self.version != EXECUTION_POLICY_VERSION:
            raise ValueError("ExecutionStrategyPolicyVersionMismatch")
        for name in (
            "vector_min_items", "multicore_min_items", "gpu_min_items", "hvm_min_operations", "hvm_min_items",
            "gpu_min_operations_per_byte", "gpu_min_compute_to_transfer_ratio", "vector_fixed_overhead_ns",
            "multicore_fixed_overhead_ns", "hvm_fixed_overhead_ns",
        ):
            _strict_int(getattr(self, name), name, minimum=0)
        if not (self.vector_min_items <= self.multicore_min_items):
            raise ValueError("ExecutionStrategyThresholdOrderInvalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "vector_min_items": self.vector_min_items,
            "multicore_min_items": self.multicore_min_items,
            "gpu_min_items": self.gpu_min_items,
            "hvm_min_operations": self.hvm_min_operations,
            "hvm_min_items": self.hvm_min_items,
            "gpu_min_operations_per_byte": self.gpu_min_operations_per_byte,
            "gpu_min_compute_to_transfer_ratio": self.gpu_min_compute_to_transfer_ratio,
            "vector_fixed_overhead_ns": self.vector_fixed_overhead_ns,
            "multicore_fixed_overhead_ns": self.multicore_fixed_overhead_ns,
            "hvm_fixed_overhead_ns": self.hvm_fixed_overhead_ns,
        }


DEFAULT_EXECUTION_POLICY = ExecutionPolicy()


@dataclass(frozen=True)
class StrategyAlternative:
    strategy: ExecutionStrategy
    accepted: bool
    reason: str
    estimated_time_ns: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.strategy, ExecutionStrategy):
            raise ValueError("ExecutionStrategyInvalidStrategy")
        if type(self.accepted) is not bool:
            raise ValueError("ExecutionStrategyAlternativeAcceptedInvalid")
        if not isinstance(self.reason, str) or not self.reason:
            raise ValueError("ExecutionStrategyAlternativeReasonInvalid")
        if self.estimated_time_ns is not None and (
            type(self.estimated_time_ns) is not int or self.estimated_time_ns < 0
        ):
            raise ValueError("ExecutionStrategyAlternativeEstimateInvalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy.value,
            "accepted": self.accepted,
            "reason": self.reason,
            "estimated_time_ns": self.estimated_time_ns,
        }


@dataclass(frozen=True)
class ExecutionDecision:
    strategy: ExecutionStrategy
    policy_version: str
    workload_digest: str
    alternatives: tuple[StrategyAlternative, ...]
    explanation: str
    schema_version: int = EXECUTION_STRATEGY_SCHEMA_VERSION
    digest: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != EXECUTION_STRATEGY_SCHEMA_VERSION:
            raise ValueError("ExecutionStrategySchemaVersionMismatch")
        _digest_value(self.workload_digest, "ExecutionStrategyWorkloadDigestInvalid")
        if self.policy_version != EXECUTION_POLICY_VERSION:
            raise ValueError("ExecutionStrategyPolicyVersionMismatch")
        if not isinstance(self.strategy, ExecutionStrategy):
            try:
                object.__setattr__(self, "strategy", ExecutionStrategy(self.strategy))
            except (TypeError, ValueError) as exc:
                raise ValueError("ExecutionStrategyInvalidStrategy") from exc
        if any(not isinstance(item, StrategyAlternative) for item in self.alternatives):
            raise ValueError("ExecutionStrategyAlternativeSchemaMismatch")
        expected = tuple(_STRATEGY_ORDER)
        actual = tuple(item.strategy for item in self.alternatives)
        if actual != expected:
            raise ValueError("ExecutionStrategyAlternativesNotCanonical")
        if not self.explanation:
            raise ValueError("ExecutionStrategyExplanationMissing")
        expected_digest = _digest(self._payload())
        if self.digest and self.digest != expected_digest:
            raise ValueError("ExecutionStrategyDigestMismatch")
        object.__setattr__(self, "digest", expected_digest)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "strategy": self.strategy.value,
            "policy_version": self.policy_version,
            "workload_digest": self.workload_digest,
            "alternatives": [item.to_dict() for item in self.alternatives],
            "explanation": self.explanation,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload()
        payload["digest"] = self.digest
        return payload

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExecutionDecision":
        expected = {"schema_version", "strategy", "policy_version", "workload_digest", "alternatives", "explanation", "digest"}
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise ValueError("ExecutionStrategyDecisionSchemaMismatch")
        digest = payload["digest"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or digest != _digest({key: payload[key] for key in expected if key != "digest"})
        ):
            raise ValueError("ExecutionStrategyDigestMismatch")
        alternatives: list[StrategyAlternative] = []
        if not isinstance(payload["alternatives"], list):
            raise ValueError("ExecutionStrategyAlternativesSchemaMismatch")
        for item in payload["alternatives"]:
            if not isinstance(item, Mapping) or set(item) != {"strategy", "accepted", "reason", "estimated_time_ns"}:
                raise ValueError("ExecutionStrategyAlternativeSchemaMismatch")
            try:
                strategy = ExecutionStrategy(item["strategy"])
            except (TypeError, ValueError) as exc:
                raise ValueError("ExecutionStrategyInvalidStrategy") from exc
            alternatives.append(StrategyAlternative(strategy, item["accepted"], item["reason"], item["estimated_time_ns"]))
        try:
            selected = ExecutionStrategy(payload["strategy"])
        except (TypeError, ValueError) as exc:
            raise ValueError("ExecutionStrategyInvalidStrategy") from exc
        return cls(selected, payload["policy_version"], payload["workload_digest"], tuple(alternatives), payload["explanation"], payload["schema_version"], payload["digest"])

    @classmethod
    def from_json(cls, text: str) -> "ExecutionDecision":
        try:
            payload = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("ExecutionStrategyInvalidJSON") from exc
        return cls.from_dict(payload)


@dataclass(frozen=True)
class BaselineComparison:
    optimized_digest: str
    scalar_baseline_digest: str
    equivalent: bool = True

    def __post_init__(self) -> None:
        _digest_value(self.optimized_digest, "ExecutionStrategyOptimizedDigestInvalid")
        _digest_value(self.scalar_baseline_digest, "ExecutionStrategyScalarBaselineDigestInvalid")
        if self.optimized_digest != self.scalar_baseline_digest:
            raise ValueError("ExecutionStrategyScalarBaselineDigestMismatch")

    def to_dict(self) -> dict[str, Any]:
        return {"optimized_digest": self.optimized_digest, "scalar_baseline_digest": self.scalar_baseline_digest, "equivalent": self.equivalent}


def _estimate_times(workload: WorkloadEstimate, capabilities: BackendCapabilities, policy: ExecutionPolicy) -> dict[ExecutionStrategy, int]:
    operations = max(workload.operation_count, workload.item_count)
    times: dict[ExecutionStrategy, int] = {}
    if capabilities.scalar:
        times[ExecutionStrategy.SCALAR] = max(1, operations)
    if capabilities.vector:
        times[ExecutionStrategy.VECTOR] = policy.vector_fixed_overhead_ns + math.ceil(operations / (capabilities.vector_lanes * 2))
    if capabilities.multicore:
        times[ExecutionStrategy.MULTICORE] = policy.multicore_fixed_overhead_ns + math.ceil(operations / (capabilities.multicore_workers * 2))
    if capabilities.gpu:
        transfer_ns = math.ceil(workload.transfer_bytes * 1_000_000_000 / capabilities.gpu_transfer_bandwidth_bytes_per_second)
        compute_ns = math.ceil(operations * 1_000_000_000 / capabilities.gpu_operations_per_second)
        times[ExecutionStrategy.GPU] = capabilities.gpu_launch_overhead_ns + transfer_ns + compute_ns
    if capabilities.hvm:
        times[ExecutionStrategy.HVM] = policy.hvm_fixed_overhead_ns + math.ceil(operations * 1_000_000_000 / capabilities.hvm_operations_per_second)
    return times


def select_execution_strategy(
    workload: WorkloadEstimate,
    capabilities: BackendCapabilities,
    policy: ExecutionPolicy = DEFAULT_EXECUTION_POLICY,
) -> ExecutionDecision:
    """Select one backend, explaining every accepted or rejected alternative."""
    if not isinstance(workload, WorkloadEstimate):
        raise ValueError("ExecutionStrategyWorkloadTypeMismatch")
    if not isinstance(capabilities, BackendCapabilities):
        raise ValueError("ExecutionStrategyCapabilitiesTypeMismatch")
    if not isinstance(policy, ExecutionPolicy):
        raise ValueError("ExecutionStrategyPolicyTypeMismatch")
    if not capabilities.scalar and not any((capabilities.vector, capabilities.multicore, capabilities.gpu, capabilities.hvm)):
        raise ValueError("ExecutionStrategyNoBackendAvailable")

    times = _estimate_times(workload, capabilities, policy)
    reasons: dict[ExecutionStrategy, str] = {}
    eligible: list[ExecutionStrategy] = []
    for strategy in _STRATEGY_ORDER:
        if strategy not in times:
            reasons[strategy] = "backend unavailable"
            continue
        if strategy in {ExecutionStrategy.VECTOR, ExecutionStrategy.MULTICORE, ExecutionStrategy.GPU, ExecutionStrategy.HVM} and not workload.parallel_safe:
            reasons[strategy] = "parallel execution rejected: workload is effectful or order-sensitive"
            continue
        if strategy is ExecutionStrategy.VECTOR and workload.item_count < policy.vector_min_items:
            reasons[strategy] = f"below vector_min_items={policy.vector_min_items}"
            continue
        if strategy is ExecutionStrategy.MULTICORE and workload.item_count < policy.multicore_min_items:
            reasons[strategy] = f"below multicore_min_items={policy.multicore_min_items}"
            continue
        if strategy is ExecutionStrategy.GPU:
            if workload.item_count < policy.gpu_min_items:
                reasons[strategy] = f"below gpu_min_items={policy.gpu_min_items}"
                continue
            if workload.transfer_bytes and workload.operation_count // workload.transfer_bytes < policy.gpu_min_operations_per_byte:
                reasons[strategy] = "GPU transfer overhead is not amortized"
                continue
        if strategy is ExecutionStrategy.HVM and (workload.item_count < policy.hvm_min_items or workload.operation_count < policy.hvm_min_operations):
            reasons[strategy] = f"below hvm_min_items={policy.hvm_min_items} or hvm_min_operations={policy.hvm_min_operations}"
            continue
        eligible.append(strategy)
        reasons[strategy] = "eligible"

    if capabilities.scalar:
        eligible.append(ExecutionStrategy.SCALAR)
        reasons[ExecutionStrategy.SCALAR] = "baseline" if workload.parallel_safe else "required for effectful or order-sensitive work"
    if not eligible:
        raise ValueError("ExecutionStrategyNoEligibleBackend")
    selected = min(eligible, key=lambda item: (times[item], _STRATEGY_ORDER.index(item)))
    alternatives = tuple(StrategyAlternative(item, item in eligible, reasons[item], times.get(item)) for item in _STRATEGY_ORDER)
    explanation = f"selected {selected.value}; policy {policy.version}; deterministic cost ordering with transfer overhead"
    return ExecutionDecision(selected, policy.version, workload.digest, alternatives, explanation)


def compare_against_scalar_baseline(optimized_digest: str, scalar_baseline_digest: str) -> BaselineComparison:
    """Require exact digest equality; no semantic-equivalence widening is performed."""
    return BaselineComparison(optimized_digest, scalar_baseline_digest)


compare_scalar_baseline = compare_against_scalar_baseline


__all__ = [
    "BaselineComparison",
    "BackendCapabilities",
    "DEFAULT_EXECUTION_POLICY",
    "EXECUTION_POLICY_VERSION",
    "EXECUTION_STRATEGY_SCHEMA_VERSION",
    "ExecutionDecision",
    "ExecutionPolicy",
    "ExecutionStrategy",
    "StrategyAlternative",
    "WorkloadEstimate",
    "compare_against_scalar_baseline",
    "compare_scalar_baseline",
    "select_execution_strategy",
]
