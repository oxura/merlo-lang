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
    frontend=6,
    canonical=4,
    hir=4,
    rir=1,
    mir=1,
    runtime_abi=2,
    semantic_world=3,
    manifest=1,
    lockfile=1,
)


__version__ = VERSIONS.release


__all__ = ["CompilerVersions", "VERSIONS", "__version__"]
