"""Stage 0.5P boundary: freeze Stage 0.4E and the Python sidecar."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .legacy_evidence import frozen_sha256, resolve_frozen_path


STAGE05P_FREEZE_SCHEMA_VERSION = 1
STAGE05P_FREEZE_FILENAME = "meldra_stage05p_freeze.json"
STAGE05P_FROZEN_PATHS = (
    "meldra/analyzer.py",
    "meldra/evolution.py",
    "meldra/world.py",
    "meldra/maximal_python.py",
    "meldra/python_binder.py",
    "meldra/frontend_syntax.py",
    "meldra/frontend_semantics.py",
    "meldra/frontend_evaluator.py",
    "meldra/core_ir_schema_v1.json",
    "benchmarks/meldra_stage04e_decision.json",
    "benchmarks/meldra_stage04e_protocol.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class Stage05PFreezeVerification:
    ok: bool
    mismatches: tuple[tuple[str, str, str], ...]
    manifest_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mismatches": [
                {"path": path, "expected": expected, "observed": observed}
                for path, expected, observed in self.mismatches
            ],
            "manifest_sha256": self.manifest_sha256,
        }


def build_stage05p_freeze(root: str | Path = Path(__file__).resolve().parents[1]) -> dict[str, Any]:
    root_path = Path(root)
    files = {
        relative: frozen_sha256(root_path, relative)
        for relative in STAGE05P_FROZEN_PATHS
    }
    decision = json.loads(
        (root_path / "benchmarks" / "meldra_stage04e_decision.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "schema_version": STAGE05P_FREEZE_SCHEMA_VERSION,
        "kind": "MeldraStage05PFreeze",
        "stage04e_decision": decision["decision"],
        "python_sidecar_policy": "CRITICAL_FIXES_ONLY",
        "native_research_is_separate": True,
        "files": files,
        "non_go_interpretation": (
            "NO_GO_LANGUAGE_ALPHA closes Stage 0.4E only; it does not measure or "
            "reject native performance, memory-model, compiler-quality, human-"
            "simplicity, or same-model AI-productivity hypotheses."
        ),
    }


def verify_stage05p_freeze(root: str | Path = Path(__file__).resolve().parents[1]) -> Stage05PFreezeVerification:
    root_path = Path(root)
    path = root_path / "benchmarks" / STAGE05P_FREEZE_FILENAME
    raw = path.read_bytes()
    payload = json.loads(raw)
    mismatches = []
    for relative, expected in payload["files"].items():
        target = resolve_frozen_path(root_path, relative)
        observed = frozen_sha256(root_path, relative) if target.is_file() else "MISSING"
        if observed != expected:
            mismatches.append((relative, expected, observed))
    return Stage05PFreezeVerification(
        not mismatches,
        tuple(sorted(mismatches)),
        hashlib.sha256(raw).hexdigest(),
    )


def assert_stage05p_frozen(root: str | Path = Path(__file__).resolve().parents[1]) -> Stage05PFreezeVerification:
    result = verify_stage05p_freeze(root)
    if not result.ok:
        details = ", ".join(path for path, _expected, _observed in result.mismatches)
        raise AssertionError(f"Stage 0.4E freeze mismatch: {details}")
    return result


__all__ = [
    "STAGE05P_FREEZE_FILENAME",
    "STAGE05P_FREEZE_SCHEMA_VERSION",
    "STAGE05P_FROZEN_PATHS",
    "Stage05PFreezeVerification",
    "assert_stage05p_frozen",
    "build_stage05p_freeze",
    "verify_stage05p_freeze",
]
