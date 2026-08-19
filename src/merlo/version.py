from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CompilerVersions:
    release: str
    language: str
    frontend: int
    canonical: int
    hir: int
    obligation_ir: int
    range_analysis: int
    bounded_symbolic: int
    smt: int
    property_evidence: int
    verification_metrics: int
    change_ir: int
    semantic_capsule: int
    semantic_impact: int
    patch_evidence: int
    preservation: int
    transaction: int
    rir: int
    mir: int
    parallel_ir: int
    wasm_backend: int
    runtime_abi: int
    semantic_world: int
    manifest: int
    lockfile: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


VERSIONS = CompilerVersions(
    release="0.1.0-alpha.3-dev",
    language="0.3",
    frontend=8,
    canonical=6,
    hir=8,
    obligation_ir=1,
    range_analysis=1,
    bounded_symbolic=1,
    smt=1,
    property_evidence=1,
    verification_metrics=1,
    change_ir=1,
    semantic_capsule=1,
    semantic_impact=1,
    patch_evidence=1,
    preservation=1,
    transaction=1,
    rir=3,
    mir=2,
    parallel_ir=1,
    wasm_backend=1,
    runtime_abi=2,
    semantic_world=16,
    manifest=1,
    lockfile=1,
)


__version__ = VERSIONS.release


__all__ = ["CompilerVersions", "VERSIONS", "__version__"]
