"""Preregistered Stage 0.4E protocol loading and integrity checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

_ARCHIVE_ROOT = Path(__file__).resolve().parents[1]
from typing import Any, Mapping

from .stage04e_freeze import assert_stage04_frozen


STAGE04E_PROTOCOL_SCHEMA_VERSION = 2
STAGE04E_PROTOCOL_FILENAME = "meldra_stage04e_protocol.json"
STAGE04E_PROTOCOL_LOCK_FILENAME = "meldra_stage04e_protocol_lock.json"
STAGE04E_PROTOCOL_V1_FILENAME = "meldra_stage04e_protocol_v1.json"
STAGE04E_PROTOCOL_V1_LOCK_FILENAME = "meldra_stage04e_protocol_v1_lock.json"
STAGE04E_PROTOCOL_V2_FILENAME = "meldra_stage04e_protocol_v2.json"
STAGE04E_PROTOCOL_V2_LOCK_FILENAME = "meldra_stage04e_protocol_v2_lock.json"

_REQUIRED_HYPOTHESES = frozenset(
    (
        "H-RUNTIME-SOUNDNESS",
        "H-STRICT-BASELINE",
        "H-INTERFACE-LOCALITY",
        "H-EFFECT-CONTEXT",
        "H-CAPABILITY-SAFETY",
        "H-AGENT-VALUE",
        "H-EXPRESSIVENESS",
    )
)
_REQUIRED_ARMS = frozenset(
    ("current-python-sidecar", "maximal-python-profile", "meldra-closed")
)


@dataclass(frozen=True)
class ProtocolMismatch:
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
class ProtocolVerification:
    protocol_id: str
    protocol_sha256: str
    mismatches: tuple[ProtocolMismatch, ...]

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "protocol_sha256": self.protocol_sha256,
            "ok": self.ok,
            "mismatches": [item.to_dict() for item in self.mismatches],
        }


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path.name} must contain an object")
    return dict(value)


def load_stage04e_protocol(root: str | Path = ".") -> dict[str, Any]:
    root_path = (Path(root).resolve() if str(root) != "." else _ARCHIVE_ROOT)
    protocol = _load_object(
        root_path / "benchmarks" / STAGE04E_PROTOCOL_FILENAME
    )
    if protocol.get("schema_version") != STAGE04E_PROTOCOL_SCHEMA_VERSION:
        raise ValueError("unsupported Stage 0.4E protocol schema version")
    if protocol.get("status") != "PREREGISTERED_BEFORE_EXTERNAL_EXECUTION":
        raise ValueError("Stage 0.4E protocol is not preregistered")
    return protocol


def load_stage04e_protocol_version(
    version: int, root: str | Path = "."
) -> dict[str, Any]:
    if version not in {1, 2}:
        raise ValueError("Stage 0.4E protocol version must be 1 or 2")
    root_path = (Path(root).resolve() if str(root) != "." else _ARCHIVE_ROOT)
    protocol = _load_object(
        root_path
        / "benchmarks"
        / (
            STAGE04E_PROTOCOL_V1_FILENAME
            if version == 1
            else STAGE04E_PROTOCOL_V2_FILENAME
        )
    )
    if protocol.get("schema_version") != version:
        raise ValueError(f"Stage 0.4E protocol v{version} schema mismatch")
    return protocol


def verify_stage04e_protocol(root: str | Path = ".") -> ProtocolVerification:
    root_path = (Path(root).resolve() if str(root) != "." else _ARCHIVE_ROOT)
    frozen = assert_stage04_frozen(root_path)
    protocol_path = root_path / "benchmarks" / STAGE04E_PROTOCOL_FILENAME
    protocol = load_stage04e_protocol(root_path)
    lock = _load_object(
        root_path / "benchmarks" / STAGE04E_PROTOCOL_LOCK_FILENAME
    )
    observed_sha256 = hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    mismatches: list[ProtocolMismatch] = []

    def expect(subject: str, expected: Any, observed: Any) -> None:
        if expected != observed:
            mismatches.append(
                ProtocolMismatch(subject, str(expected), str(observed))
            )

    expect(
        "lock.status",
        "LOCKED_BEFORE_EXTERNAL_EXECUTION",
        lock.get("status"),
    )
    expect("lock.protocol_sha256", lock.get("protocol_sha256"), observed_sha256)
    expect("parent_freeze_id", frozen.freeze_id, protocol.get("parent_freeze_id"))
    expect(
        "lock.parent_freeze_id",
        frozen.freeze_id,
        lock.get("parent_freeze_id"),
    )
    expect("lock.protocol_id", protocol.get("protocol_id"), lock.get("protocol_id"))

    v1_path = root_path / "benchmarks" / STAGE04E_PROTOCOL_V1_FILENAME
    v1_lock = _load_object(
        root_path / "benchmarks" / STAGE04E_PROTOCOL_V1_LOCK_FILENAME
    )
    v1_sha256 = hashlib.sha256(v1_path.read_bytes()).hexdigest()
    expect("v1.lock.protocol_sha256", v1_lock.get("protocol_sha256"), v1_sha256)
    expect(
        "v1.lock.protocol_id",
        "meldra-stage04e-external-semantic-differential-v1",
        v1_lock.get("protocol_id"),
    )
    v2_path = root_path / "benchmarks" / STAGE04E_PROTOCOL_V2_FILENAME
    v2_lock = _load_object(
        root_path / "benchmarks" / STAGE04E_PROTOCOL_V2_LOCK_FILENAME
    )
    v2_sha256 = hashlib.sha256(v2_path.read_bytes()).hexdigest()
    expect("v2.copy.sha256", observed_sha256, v2_sha256)
    expect("v2.lock.protocol_sha256", v2_lock.get("protocol_sha256"), v2_sha256)
    expect("v2.lock.protocol_id", protocol.get("protocol_id"), v2_lock.get("protocol_id"))

    hypotheses = {
        str(item.get("id"))
        for item in protocol.get("hypotheses", [])
        if isinstance(item, Mapping)
    }
    expect("hypotheses", sorted(_REQUIRED_HYPOTHESES), sorted(hypotheses))
    arms = {
        str(item.get("id"))
        for item in protocol.get("arms", [])
        if isinstance(item, Mapping)
    }
    expect("arms", sorted(_REQUIRED_ARMS), sorted(arms))

    corpus = protocol.get("corpus", {})
    selection = corpus.get("selection", {}) if isinstance(corpus, Mapping) else {}
    minimums = {
        "program_count_min": 30,
        "program_count_max": 50,
        "paired_change_count_min": 200,
        "adversarial_negative_count_min": 300,
        "runtime_observation_count_min": 300,
        "interface_change_count_min": 100,
        "capability_attack_count_min": 100,
        "external_safe_trials_each_operation_min": 30,
        "runtime_callsite_category_min": 23,
    }
    for key, value in minimums.items():
        expect(f"corpus.selection.{key}", value, selection.get(key))

    expect(
        "runtime_binding_categories",
        23,
        len(protocol.get("runtime_binding_categories", [])),
    )
    expect(
        "interface_change_categories",
        9,
        len(protocol.get("interface_change_categories", [])),
    )
    expect(
        "capability_attack_categories",
        5,
        len(protocol.get("capability_attack_categories", [])),
    )

    statistical = protocol.get("statistical_policy", {})
    if not isinstance(statistical, Mapping):
        statistical = {}
    expect("statistical.confidence_level", 0.95, statistical.get("confidence_level"))
    expect("statistical.bootstrap_replicates", 10000, statistical.get("bootstrap_replicates"))
    expect("statistical.bootstrap_seed", 20260810, statistical.get("bootstrap_seed"))

    alpha_go = protocol.get("language_alpha_go", {})
    if not isinstance(alpha_go, Mapping):
        alpha_go = {}
    expect("language_alpha_go.all_required", True, alpha_go.get("all_required"))
    agent = alpha_go.get("agent_value", {})
    if not isinstance(agent, Mapping):
        agent = {}
    expect("agent.same_model_required", True, agent.get("same_model_required"))

    stop_conditions = protocol.get("stop_conditions", [])
    if not isinstance(stop_conditions, list) or not stop_conditions:
        mismatches.append(
            ProtocolMismatch("stop_conditions", "non-empty list", repr(stop_conditions))
        )
    forbidden = protocol.get("feature_freeze", {}).get(
        "forbidden_during_stage04e", []
    )
    for feature in ("new syntax", "flow", "machine", "package registry"):
        if feature not in forbidden:
            mismatches.append(
                ProtocolMismatch(
                    f"feature_freeze.{feature}", "forbidden", "missing"
                )
            )

    return ProtocolVerification(
        str(protocol.get("protocol_id", "")),
        observed_sha256,
        tuple(mismatches),
    )


def assert_stage04e_protocol(root: str | Path = ".") -> ProtocolVerification:
    verification = verify_stage04e_protocol(root)
    if not verification.ok:
        details = "; ".join(
            f"{item.subject}: expected {item.expected}, observed {item.observed}"
            for item in verification.mismatches
        )
        raise RuntimeError(f"Stage 0.4E protocol verification failed: {details}")
    return verification


__all__ = [
    "STAGE04E_PROTOCOL_FILENAME",
    "STAGE04E_PROTOCOL_LOCK_FILENAME",
    "STAGE04E_PROTOCOL_V1_FILENAME",
    "STAGE04E_PROTOCOL_V1_LOCK_FILENAME",
    "STAGE04E_PROTOCOL_V2_FILENAME",
    "STAGE04E_PROTOCOL_V2_LOCK_FILENAME",
    "STAGE04E_PROTOCOL_SCHEMA_VERSION",
    "ProtocolMismatch",
    "ProtocolVerification",
    "assert_stage04e_protocol",
    "load_stage04e_protocol",
    "load_stage04e_protocol_version",
    "verify_stage04e_protocol",
]
