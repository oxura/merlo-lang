from __future__ import annotations

import dataclasses

import pytest

from merlo.parallel_backends import (
    BACKEND_TARGETS,
    BackendAdapter,
    BackendCapabilities,
    BackendResult,
    BackendTarget,
    HVMBackend,
    ParallelBackendArtifact,
    TARGET_PRECEDENCE,
    discover_capabilities,
    lower_gpu,
    lower_hvm,
    select_backend,
)
from merlo.parallel_ir import ParallelIR, ParallelOperation, ParallelValueType


def fixture(*enabled: str, **named: str) -> dict[str, bool]:
    available = set(enabled) | set(named) | {"scalar_cpu"}
    return {target: target in available for target in BACKEND_TARGETS}


def test_precedence_is_stable_and_gpu_wins() -> None:
    capabilities = discover_capabilities(fixture(gpu="yes", hvm="yes", multicore_cpu="yes"))
    first = select_backend(capabilities)
    second = select_backend(capabilities.to_dict())
    assert TARGET_PRECEDENCE == ("gpu", "hvm", "multicore_cpu", "vector_cpu", "scalar_cpu")
    assert first.selected_target == "gpu"
    assert first == second
    assert first.capabilities_digest == capabilities.digest


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        ((), "scalar_cpu"),
        (("vector_cpu",), "vector_cpu"),
        (("multicore_cpu",), "multicore_cpu"),
        (("gpu",), "gpu"),
        (("hvm",), "scalar_cpu"),
    ],
)
def test_each_backend_branch(enabled: tuple[str, ...], expected: str) -> None:
    result = select_backend(capability_data=fixture(*enabled))
    assert result.selected_target == expected
    assert result.fallback is (expected == "scalar_cpu")


def test_explicit_request_rejects_unavailable_target() -> None:
    capabilities = discover_capabilities(fixture(multicore_cpu="yes"))
    with pytest.raises(ValueError, match="RequestedBackendUnavailable:gpu"):
        select_backend(capabilities, requested_target=BackendTarget.GPU)
    with pytest.raises(ValueError, match="UnknownBackendTarget"):
        select_backend(capabilities, requested_target="quantum")


def test_no_installed_optional_backend_uses_scalar_fallback() -> None:
    capabilities = discover_capabilities(
        platform_probe={
            "machine": "unknown-machine",
            "cpu_count": 1,
            "gpu_modules": False,
            "hvm_modules": False,
        }
    )
    assert not capabilities.for_target("gpu").available
    assert capabilities.for_target("gpu").reason == "GPUIntegrationMissing"
    assert not capabilities.for_target("hvm").available
    assert capabilities.for_target("hvm").reason == "HVMIntegrationMissing"
    result = select_backend(capabilities)
    assert result.selected_target == "scalar_cpu"
    assert result.fallback
    assert result.reason == "ScalarFallback"


def test_capability_snapshot_is_immutable_and_tamper_evident() -> None:
    source = fixture(vector_cpu="yes")
    capabilities = discover_capabilities(source)
    source["gpu"] = True
    assert capabilities.for_target("gpu").available is False
    assert dataclasses.is_dataclass(capabilities)
    with pytest.raises(TypeError):
        capabilities.capabilities[0].metadata["x"] = 1

    payload = capabilities.to_dict()
    payload["capabilities"][0]["available"] = False
    with pytest.raises(ValueError, match="BackendCapabilitiesDigestMismatch"):
        BackendCapabilities.from_dict(payload)


def test_selection_and_result_schemas_reject_tampering() -> None:
    selection = select_backend(capability_data=fixture(gpu="yes"))
    payload = selection.to_dict()
    payload["selected_target"] = "scalar_cpu"
    with pytest.raises(ValueError, match="BackendSelectionDigestMismatch"):
        type(selection).from_dict(payload)

    result = BackendResult("hvm", "ok", output={"value": [1, 2]})
    assert result.to_dict()["digest"] == result.digest
    assert BackendResult.from_dict(result.to_dict()) == result
    malformed = result.to_dict()
    malformed["unexpected"] = 1
    with pytest.raises(ValueError, match="BackendResultSchemaMismatch"):
        BackendResult.from_dict(malformed)

def test_backend_results_reject_contradictory_payloads_and_non_string_keys() -> None:
    with pytest.raises(ValueError, match="SuccessfulBackendHasError"):
        BackendResult("scalar_cpu", "ok", error="bad")
    with pytest.raises(ValueError, match="FailedBackendHasOutput"):
        BackendResult("scalar_cpu", "error", output=1, error="bad")
    with pytest.raises(ValueError, match="FailedBackendNeedsReason"):
        BackendResult("scalar_cpu", "error")
    with pytest.raises(ValueError, match="InvalidBackendMetadataKey"):
        BackendResult("scalar_cpu", "ok", metadata={1: "bad"})



def test_adapter_protocol_is_provider_neutral() -> None:
    class Adapter:
        target = BackendTarget.SCALAR_CPU

        def execute(self, request):
            return BackendResult("scalar_cpu", "ok", output=request)

    assert isinstance(Adapter(), BackendAdapter)
    assert Adapter().execute({"x": 1}).target == "scalar_cpu"
def _vector_ir(kind: str = "map", *, effects: tuple[str, ...] = (), attributes: dict[str, object] | None = None) -> ParallelIR:
    operation = ParallelOperation(
        "op0",
        kind,
        inputs=("input",),
        result="output",
        result_type=ParallelValueType.vector("i32", 4),
        effects=effects,
        attributes=attributes if attributes is not None else {"independent": True, "execution": "vector"},
    )
    return ParallelIR("a" * 64, (operation,))


def test_hvm_is_not_auto_selected_but_is_explicitly_selectable() -> None:
    capabilities = discover_capabilities(fixture(hvm="yes"))
    assert select_backend(capabilities).selected_target == "scalar_cpu"
    assert select_backend(capabilities, requested_target="hvm").selected_target == "hvm"


@pytest.mark.parametrize("kind", ("map", "zip", "reduce", "scan", "filter", "gather", "scatter", "fused_map_zip"))
def test_gpu_lowering_accepts_only_proven_vector_subset(kind: str) -> None:
    artifact = lower_gpu(_vector_ir(kind), fixture(gpu="yes"))
    assert artifact.target == "gpu"
    assert artifact.selected_adapter == "builtin"
    assert artifact.source_digest == _vector_ir(kind).digest
    assert artifact.content_digest == artifact.to_dict()["content_digest"]
    assert ParallelBackendArtifact.from_json(artifact.to_json()) == artifact


def test_gpu_lowering_rejects_effectful_scalar_and_unproven_ir() -> None:
    with pytest.raises(ValueError, match="GPUEffectfulOperation"):
        lower_gpu(
            _vector_ir(effects=("io",), attributes={"independent": False, "execution": "scalar"}),
            fixture(gpu="yes"),
        )
    scalar = ParallelOperation("op0", "map", result="output", result_type=ParallelValueType.scalar("i32"))
    with pytest.raises(ValueError, match="GPUScalarOperation"):
        lower_gpu(ParallelIR("a" * 64, (scalar,)), fixture(gpu="yes"))
    with pytest.raises(ValueError, match="GPUIndependenceProofRequired"):
        lower_gpu(_vector_ir(attributes={"execution": "vector"}), fixture(gpu="yes"))
    with pytest.raises(ValueError, match="GPUVectorSafetyProofRequired"):
        lower_gpu(_vector_ir(attributes={"independent": True}), fixture(gpu="yes"))


def test_gpu_rejects_tampered_ir_and_reports_missing_dependency() -> None:
    tampered = _vector_ir().to_dict()
    tampered["operations"][0]["attributes"]["independent"] = False
    with pytest.raises(ValueError, match="ParallelIRDigestMismatch"):
        lower_gpu(tampered, fixture(gpu="yes"))
    with pytest.raises(ValueError, match="GPUUnavailable: GPUIntegrationMissing"):
        lower_gpu(_vector_ir(), fixture())


def test_hvm_requires_opt_in_dependency_and_marks_artifact_experimental() -> None:
    with pytest.raises(ValueError, match="HVMOptInRequired"):
        lower_hvm(_vector_ir(), fixture(hvm="yes"))
    with pytest.raises(ValueError, match="HVMUnavailable: HVMIntegrationMissing"):
        lower_hvm(_vector_ir(), fixture(), opt_in=True)
    artifact = HVMBackend(opt_in=True).lower(_vector_ir(), fixture(hvm="yes"))
    assert artifact.experimental
    assert artifact.format == "canonical-hvm-net-ir"
    assert ParallelBackendArtifact.from_dict(artifact.to_dict()) == artifact
