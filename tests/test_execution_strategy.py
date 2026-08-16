from __future__ import annotations

import hashlib
import dataclasses
import json

import pytest

from merlo.execution_strategy import (
    BackendCapabilities,
    ExecutionStrategy,
    WorkloadEstimate,
    compare_against_scalar_baseline,
    select_execution_strategy,
)


def workload(**overrides: object) -> WorkloadEstimate:
    values: dict[str, object] = {
        "item_count": 16,
        "element_width": 8,
        "pure": True,
        "associative": True,
        "transfer_bytes": 128,
        "operation_count": 16,
    }
    values.update(overrides)
    return WorkloadEstimate(**values)


def test_tiny_workload_stays_scalar() -> None:
    decision = select_execution_strategy(workload(), BackendCapabilities(vector=True, multicore=True, gpu=True, hvm=True))
    assert decision.strategy is ExecutionStrategy.SCALAR
    assert any(item.strategy is ExecutionStrategy.VECTOR and not item.accepted for item in decision.alternatives)


def test_vector_threshold_is_explicit() -> None:
    decision = select_execution_strategy(workload(item_count=256, operation_count=256), BackendCapabilities(vector=True))
    assert decision.strategy is ExecutionStrategy.VECTOR
    vector = next(item for item in decision.alternatives if item.strategy is ExecutionStrategy.VECTOR)
    assert vector.accepted


def test_multicore_threshold_is_explicit() -> None:
    decision = select_execution_strategy(workload(item_count=8_192, operation_count=100_000), BackendCapabilities(multicore=True))
    assert decision.strategy is ExecutionStrategy.MULTICORE
    assert "multicore_min_items" not in decision.alternatives[0].reason


def test_gpu_transfer_must_be_amortized() -> None:
    low_transfer = select_execution_strategy(
        workload(item_count=100_000, operation_count=10_000_000, transfer_bytes=1_000),
        BackendCapabilities(gpu=True),
    )
    high_transfer = select_execution_strategy(
        workload(item_count=100_000, operation_count=10_000_000, transfer_bytes=10_000_000_000),
        BackendCapabilities(gpu=True),
    )
    assert low_transfer.strategy is ExecutionStrategy.GPU
    assert high_transfer.strategy is ExecutionStrategy.SCALAR
    gpu = next(item for item in high_transfer.alternatives if item.strategy is ExecutionStrategy.GPU)
    assert "transfer" in gpu.reason.lower()


def test_hvm_selection_uses_operation_threshold() -> None:
    decision = select_execution_strategy(
        workload(item_count=100_000, operation_count=2_000_000, transfer_bytes=0),
        BackendCapabilities(hvm=True),
    )
    assert decision.strategy is ExecutionStrategy.HVM


def test_effectful_and_order_sensitive_work_never_parallelizes() -> None:
    for values in ({"pure": False}, {"order_sensitive": True}, {"associative": False}):
        decision = select_execution_strategy(
            workload(item_count=1_000_000, operation_count=10_000_000, transfer_bytes=0, **values),
            BackendCapabilities(vector=True, multicore=True, gpu=True, hvm=True),
        )
        assert decision.strategy is ExecutionStrategy.SCALAR
        assert all(not item.accepted for item in decision.alternatives if item.strategy is not ExecutionStrategy.SCALAR)


def test_decision_digest_and_json_round_trip_are_deterministic() -> None:
    capabilities = BackendCapabilities(vector=True, multicore=True)
    first = select_execution_strategy(workload(item_count=256, operation_count=256), capabilities)
    second = select_execution_strategy(workload(item_count=256, operation_count=256), capabilities)
    assert first.digest == second.digest
    assert first.to_json() == second.to_json()
    restored = first.from_json(first.to_json())
    assert dataclasses.asdict(restored) == dataclasses.asdict(first)
    assert json.loads(first.to_json())["digest"] == first.digest


def test_disabled_scalar_is_never_selected_and_payloads_are_strict() -> None:
    decision = select_execution_strategy(
        workload(item_count=256, operation_count=256),
        BackendCapabilities(scalar=False, vector=True),
    )
    assert decision.strategy is ExecutionStrategy.VECTOR
    scalar = next(item for item in decision.alternatives if item.strategy is ExecutionStrategy.SCALAR)
    assert not scalar.accepted
    assert scalar.estimated_time_ns is None

    payload = decision.to_dict()
    payload["digest"] = ""
    with pytest.raises(ValueError, match="ExecutionStrategyDigestMismatch"):
        type(decision).from_dict(payload)

    payload = decision.to_dict()
    payload["alternatives"][0]["accepted"] = "yes"
    payload_without_digest = {key: value for key, value in payload.items() if key != "digest"}
    payload["digest"] = hashlib.sha256(
        json.dumps(payload_without_digest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="AlternativeAcceptedInvalid"):
        type(decision).from_dict(payload)


def test_scalar_baseline_digest_mismatch_is_rejected() -> None:
    digest = "a" * 64
    assert compare_against_scalar_baseline(digest, digest).equivalent
    with pytest.raises(ValueError, match="ExecutionStrategyScalarBaselineDigestMismatch"):
        compare_against_scalar_baseline(digest, "b" * 64)
