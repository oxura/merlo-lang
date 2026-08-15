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
    rir: int
    mir: int
    runtime_abi: int
    semantic_world: int
    manifest: int
    lockfile: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


VERSIONS = CompilerVersions(
    release="0.1.0-alpha.3-dev",
    language="0.3",
    frontend=7,
    canonical=5,
    hir=5,
    obligation_ir=1,
    rir=2,
    mir=2,
    runtime_abi=2,
    semantic_world=5,
    manifest=1,
    lockfile=1,
)


__version__ = VERSIONS.release


__all__ = ["CompilerVersions", "VERSIONS", "__version__"]
