"""Immutable Stage 0.4 freeze verification for Stage 0.4E experiments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from research.archive.historical_protocol.merlo.legacy_evidence import frozen_sha256, resolve_frozen_path


STAGE04_FREEZE_SCHEMA_VERSION = 1
STAGE04_FREEZE_FILENAME = "meldra_stage04_freeze.json"
STAGE04_CANONICAL_FREEZE_FILENAME = "STAGE_0_4_FREEZE.json"
STAGE04_FREEZE_LOCK_FILENAME = "meldra_stage04_freeze_lock.json"

_IMPLEMENTATION_PATHS = {
    "core_semantics": "meldra/core_semantics.py",
    "frontend_benchmark": "meldra/frontend_bench.py",
    "frontend_evaluator": "meldra/frontend_evaluator.py",
    "frontend_semantics": "meldra/frontend_semantics.py",
    "frontend_syntax": "meldra/frontend_syntax.py",
    "python_binder": "meldra/python_binder.py",
}


@dataclass(frozen=True)
class FreezeMismatch:
    subject: str
    expected: str
    observed: str

    def to_dict(self) -> dict[str, str]:
        return {
            "subject": self.subject,
            "expected": self.expected,
            "observed": self.observed,
        }


@dataclass(frozen=True)
class FreezeVerification:
    freeze_id: str
    mismatches: tuple[FreezeMismatch, ...]

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def to_dict(self) -> dict[str, Any]:
        return {
            "freeze_id": self.freeze_id,
            "ok": self.ok,
            "mismatches": [item.to_dict() for item in self.mismatches],
        }


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def load_stage04_freeze(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root).resolve()
    path = root_path / "tools" / "benchmarks" / "merlo" / "benchmarks" / STAGE04_FREEZE_FILENAME
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("Stage 0.4 freeze manifest must be an object")
    if value.get("schema_version") != STAGE04_FREEZE_SCHEMA_VERSION:
        raise ValueError("unsupported Stage 0.4 freeze schema version")
    if value.get("status") != "FROZEN_BEFORE_STAGE04E_HELD_OUT_EXECUTION":
        raise ValueError("Stage 0.4 freeze status is not immutable")
    return dict(value)


def verify_stage04_freeze(root: str | Path = ".") -> FreezeVerification:
    root_path = Path(root).resolve()
    manifest = load_stage04_freeze(root_path)
    mismatches: list[FreezeMismatch] = []

    freeze_id = str(manifest.get("freeze_id", ""))
    identity_payload = dict(manifest)
    identity_payload.pop("freeze_id", None)
    observed_freeze_id = "stage04_" + _sha256_bytes(_canonical(identity_payload))
    if freeze_id != observed_freeze_id:
        mismatches.append(
            FreezeMismatch("freeze_id", freeze_id, observed_freeze_id)
        )
    canonical_path = (
        resolve_frozen_path(root_path, f"meldra/{STAGE04_CANONICAL_FREEZE_FILENAME}")
    )
    lock_path = (
        root_path / "tools" / "benchmarks" / "merlo" / "benchmarks" / STAGE04_FREEZE_LOCK_FILENAME
    )
    if not canonical_path.is_file() or not lock_path.is_file():
        mismatches.append(
            FreezeMismatch(
                "canonical_manifest",
                "present with lock",
                "MISSING",
            )
        )
    else:
        canonical_manifest = json.loads(
            canonical_path.read_text(encoding="utf-8")
        )
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        canonical_sha256 = _sha256_file(canonical_path)
        if lock.get("status") != "LOCKED_BEFORE_HELD_OUT_STAGE04E":
            mismatches.append(
                FreezeMismatch(
                    "canonical_lock.status",
                    "LOCKED_BEFORE_HELD_OUT_STAGE04E",
                    str(lock.get("status")),
                )
            )
        if lock.get("canonical_manifest_sha256") != canonical_sha256:
            mismatches.append(
                FreezeMismatch(
                    "canonical_lock.sha256",
                    str(lock.get("canonical_manifest_sha256")),
                    canonical_sha256,
                )
            )
        if canonical_manifest.get("freeze_id") != freeze_id:
            mismatches.append(
                FreezeMismatch(
                    "canonical_manifest.freeze_id",
                    freeze_id,
                    str(canonical_manifest.get("freeze_id")),
                )
            )
        if lock.get("freeze_id") != freeze_id:
            mismatches.append(
                FreezeMismatch(
                    "canonical_lock.freeze_id",
                    freeze_id,
                    str(lock.get("freeze_id")),
                )
            )

    semantics = manifest.get("semantics", {})
    if not isinstance(semantics, Mapping):
        semantics = {}
    for name, contract in sorted(semantics.items()):
        if not isinstance(contract, Mapping):
            mismatches.append(
                FreezeMismatch(f"semantics.{name}", "object", type(contract).__name__)
            )
            continue
        expected = str(contract.get("sha256", ""))
        observed = _sha256_bytes(_canonical(contract.get("rules", [])))
        if expected != observed:
            mismatches.append(
                FreezeMismatch(f"semantics.{name}.sha256", expected, observed)
            )

    implementation = manifest.get("implementation_sha256", {})
    if not isinstance(implementation, Mapping):
        implementation = {}
    for name, relative_path in sorted(_IMPLEMENTATION_PATHS.items()):
        expected = str(implementation.get(name, ""))
        path = resolve_frozen_path(root_path, relative_path)
        observed = frozen_sha256(root_path, relative_path) if path.is_file() else "MISSING"
        if expected != observed:
            mismatches.append(
                FreezeMismatch(f"implementation.{name}", expected, observed)
            )

    core_ir = manifest.get("core_ir", {})
    if not isinstance(core_ir, Mapping):
        core_ir = {}
    expected_schema = str(core_ir.get("schema_sha256", ""))
    schema_recorded_path = "meldra/core_ir_schema_v1.json"
    schema_path = resolve_frozen_path(root_path, schema_recorded_path)
    observed_schema = frozen_sha256(root_path, schema_recorded_path) if schema_path.is_file() else "MISSING"
    if expected_schema != observed_schema:
        mismatches.append(
            FreezeMismatch("core_ir.schema_sha256", expected_schema, observed_schema)
        )

    benchmark = manifest.get("benchmark", {})
    if not isinstance(benchmark, Mapping):
        benchmark = {}
    artifacts = {
        "support_profile_sha256": "tools/benchmarks/merlo/benchmarks/meldra_stage04_support_profile.json",
        "observed_result_sha256": "tools/benchmarks/merlo/benchmarks/meldra_stage04_frontend_benchmark.json",
    }
    for key, relative_path in artifacts.items():
        expected = str(benchmark.get(key, ""))
        path = root_path / relative_path
        observed = _sha256_file(path) if path.is_file() else "MISSING"
        if expected != observed:
            mismatches.append(FreezeMismatch(f"benchmark.{key}", expected, observed))

    return FreezeVerification(freeze_id, tuple(mismatches))


def assert_stage04_frozen(root: str | Path = ".") -> FreezeVerification:
    verification = verify_stage04_freeze(root)
    if not verification.ok:
        details = "; ".join(
            f"{item.subject}: expected {item.expected}, observed {item.observed}"
            for item in verification.mismatches
        )
        raise RuntimeError(f"Stage 0.4 freeze verification failed: {details}")
    return verification


__all__ = [
    "STAGE04_CANONICAL_FREEZE_FILENAME",
    "STAGE04_FREEZE_LOCK_FILENAME",
    "STAGE04_FREEZE_FILENAME",
    "STAGE04_FREEZE_SCHEMA_VERSION",
    "FreezeMismatch",
    "FreezeVerification",
    "assert_stage04_frozen",
    "load_stage04_freeze",
    "verify_stage04_freeze",
]
