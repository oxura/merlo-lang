from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from merlo.refactor import (
    ChangeIR,
    preview_change_signature,
    preview_move,
    preview_rename,
)
from merlo.semantic_world import SemanticWorld, StaleWorldError, WorldError


SYNTHESIS_SCHEMA_VERSION = 1
SYNTHESIS_REQUEST_CONTRACT = "merlo.synthesis-request.v1"
SYNTHESIS_CANDIDATE_CONTRACT = "merlo.synthesis-candidate.v1"
CANDIDATE_PRODUCERS = ("rewrite", "enumerative", "symbolic", "package", "llm")
CANDIDATE_STATUSES = ("proposed", "blocked")
_OPERATIONS = ("rename", "move", "change_signature", "fill_hole")


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    try:
        encoded = _json(value).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise WorldError("SynthesisNonCanonical") from exc
    return hashlib.sha256(encoded).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if isinstance(value, float) and not math.isfinite(value):
        raise WorldError("SynthesisNonFiniteNumber")
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _text(value: Any, error: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value):
        raise WorldError(error)
    return value
def _validate_arguments(operation: str, arguments: Any) -> Mapping[str, Any]:
    if type(operation) is not str or not operation:
        raise WorldError("SynthesisRequestInvalidOperation")
    if not isinstance(arguments, Mapping):
        raise WorldError("SynthesisInvalidArguments")
    if operation not in _OPERATIONS:
        # Requests are producer-neutral.  The rewrite producer rejects unknown
        # operations; other producers may bind a later ChangeIR operation.
        if any(type(key) is not str or not key for key in arguments):
            raise WorldError("SynthesisInvalidArguments")
        return arguments
    if operation == "fill_hole":
        allowed = ({"hole_id"}, {"hole_id", "max_candidates"})
        if set(arguments) not in allowed:
            raise WorldError("SynthesisInvalidArguments")
        _text(arguments["hole_id"], "SynthesisInvalidArguments:hole_id")
        if "max_candidates" in arguments and (
            type(arguments["max_candidates"]) is not int
            or arguments["max_candidates"] <= 0
        ):
            raise WorldError("SynthesisInvalidArguments:max_candidates")
        return arguments
    expected = {
        "rename": {"new_name"},
        "move": {"module"},
        "change_signature": {"signature"},
    }[operation]
    if set(arguments) != expected:
        raise WorldError("SynthesisInvalidArguments")
    key = next(iter(expected))
    _text(arguments[key], f"SynthesisInvalidArguments:{key}")
    return arguments


@dataclass(frozen=True)
class SynthesisRequest:
    """The producer-neutral, content-addressed synthesis request."""

    world_digest: str
    target: str
    operation: str
    arguments: Mapping[str, Any]
    goal: str = ""
    schema_version: int = SYNTHESIS_SCHEMA_VERSION
    contract: str = SYNTHESIS_REQUEST_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SYNTHESIS_SCHEMA_VERSION:
            raise WorldError("SynthesisRequestVersionMismatch")
        if self.contract != SYNTHESIS_REQUEST_CONTRACT:
            raise WorldError("SynthesisRequestContractMismatch")
        _text(self.world_digest, "SynthesisRequestInvalidWorldDigest")
        _text(self.target, "SynthesisRequestInvalidTarget")
        _text(self.operation, "SynthesisRequestInvalidOperation")
        if type(self.goal) is not str:
            raise WorldError("SynthesisRequestInvalidGoal")
        arguments = _validate_arguments(self.operation, self.arguments)
        object.__setattr__(self, "arguments", _freeze(arguments))
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise WorldError("SynthesisRequestDigestMismatch")
        object.__setattr__(self, "digest", expected)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "world_digest": self.world_digest,
            "target": self.target,
            "operation": self.operation,
            "arguments": _thaw(self.arguments),
            "goal": self.goal,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SynthesisRequest":
        if not isinstance(value, Mapping):
            raise WorldError("SynthesisRequestSchemaMismatch")
        required = {
            "schema_version",
            "contract",
            "world_digest",
            "target",
            "operation",
            "arguments",
            "goal",
            "digest",
        }
        if set(value) != required or not isinstance(value.get("arguments"), Mapping) or type(value.get("digest")) is not str:
            raise WorldError("SynthesisRequestSchemaMismatch")
        payload = {key: value[key] for key in required if key != "digest"}
        if value["digest"] != _digest(payload):
            raise WorldError("SynthesisRequestDigestMismatch")
        return cls(
            world_digest=value["world_digest"],
            target=value["target"],
            operation=value["operation"],
            arguments=value["arguments"],
            goal=value["goal"],
            schema_version=value["schema_version"],
            contract=value["contract"],
            digest=value["digest"],
        )

    @classmethod
    def from_json(cls, value: str) -> "SynthesisRequest":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise WorldError("SynthesisRequestSchemaMismatch") from exc
        return cls.from_dict(payload)


@dataclass(frozen=True)
class CandidateRank:
    priority: int
    cost: int
    tie_breaker: str

    def __post_init__(self) -> None:
        if type(self.priority) is not int or self.priority < 0:
            raise WorldError("CandidateRankInvalidPriority")
        if type(self.cost) is not int or self.cost < 0:
            raise WorldError("CandidateRankInvalidCost")
        _text(self.tie_breaker, "CandidateRankInvalidTieBreaker")

    def to_dict(self) -> dict[str, Any]:
        return {
            "priority": self.priority,
            "cost": self.cost,
            "tie_breaker": self.tie_breaker,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateRank":
        if not isinstance(value, Mapping) or set(value) != {"priority", "cost", "tie_breaker"}:
            raise WorldError("CandidateRankSchemaMismatch")
        return cls(value["priority"], value["cost"], value["tie_breaker"])


_CANDIDATE_FIELDS = {
    "schema_version",
    "contract",
    "producer",
    "producer_revision",
    "base_world_digest",
    "target_symbol_id",
    "change",
    "capsule_digest",
    "impact_digest",
    "rank",
    "provenance",
    "status",
    "diagnostic",
    "digest",
}


@dataclass(frozen=True)
class SynthesisCandidate:
    """A proposed or blocked rewrite; acceptance and verification are separate."""

    producer: str
    producer_revision: str
    base_world_digest: str
    target_symbol_id: str
    change: Mapping[str, Any]
    capsule_digest: str
    impact_digest: str
    rank: CandidateRank
    provenance: Mapping[str, Any]
    status: str
    diagnostic: Mapping[str, Any] | None = None
    schema_version: int = SYNTHESIS_SCHEMA_VERSION
    contract: str = SYNTHESIS_CANDIDATE_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SYNTHESIS_SCHEMA_VERSION:
            raise WorldError("SynthesisCandidateVersionMismatch")
        if self.contract != SYNTHESIS_CANDIDATE_CONTRACT:
            raise WorldError("SynthesisCandidateContractMismatch")
        if type(self.producer) is not str or self.producer not in CANDIDATE_PRODUCERS:
            raise WorldError("SynthesisCandidateInvalidProducer")
        _text(self.producer_revision, "SynthesisCandidateInvalidProducerRevision")
        _text(self.base_world_digest, "SynthesisCandidateInvalidWorldDigest")
        _text(self.target_symbol_id, "SynthesisCandidateInvalidTarget")
        _text(self.capsule_digest, "SynthesisCandidateInvalidCapsuleDigest")
        _text(self.impact_digest, "SynthesisCandidateInvalidImpactDigest")
        if type(self.status) is not str or self.status not in CANDIDATE_STATUSES:
            raise WorldError("SynthesisCandidateInvalidStatus")
        rank = self.rank if isinstance(self.rank, CandidateRank) else CandidateRank.from_dict(self.rank)
        object.__setattr__(self, "rank", rank)
        if not isinstance(self.change, Mapping):
            raise WorldError("SynthesisCandidateInvalidChange")
        change = _freeze(self.change)
        # Parsing validates the complete ChangeIR envelope and its own digest.
        change_ir = ChangeIR.from_dict(_thaw(change))
        if change_ir.expected_world_digest != self.base_world_digest:
            raise WorldError("SynthesisCandidateChangeWorldMismatch")
        if change_ir.target.symbol_id != self.target_symbol_id:
            raise WorldError("SynthesisCandidateChangeTargetMismatch")
        object.__setattr__(self, "change", change)
        if not isinstance(self.provenance, Mapping):
            raise WorldError("SynthesisCandidateInvalidProvenance")
        object.__setattr__(self, "provenance", _freeze(self.provenance))
        diagnostic = self.diagnostic
        if diagnostic is not None:
            if not isinstance(diagnostic, Mapping) or set(diagnostic) != {"code", "message", "details"}:
                raise WorldError("SynthesisCandidateInvalidDiagnostic")
            _text(diagnostic["code"], "SynthesisCandidateInvalidDiagnostic")
            _text(diagnostic["message"], "SynthesisCandidateInvalidDiagnostic")
            if not isinstance(diagnostic["details"], Mapping):
                raise WorldError("SynthesisCandidateInvalidDiagnostic")
            diagnostic = _freeze(diagnostic)
            object.__setattr__(self, "diagnostic", diagnostic)
        if self.status == "proposed":
            if change_ir.status != "ready" or diagnostic is not None:
                raise WorldError("SynthesisCandidateProposedInvariant")
        else:
            if change_ir.status != "unsupported" or diagnostic is None:
                raise WorldError("SynthesisCandidateBlockedInvariant")
            if _thaw(diagnostic) != change_ir.diagnostic.to_dict():
                raise WorldError("SynthesisCandidateDiagnosticMismatch")
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise WorldError("SynthesisCandidateDigestMismatch")
        object.__setattr__(self, "digest", expected)

    @property
    def change_ir(self) -> ChangeIR:
        return ChangeIR.from_dict(_thaw(self.change))

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "producer": self.producer,
            "producer_revision": self.producer_revision,
            "base_world_digest": self.base_world_digest,
            "target_symbol_id": self.target_symbol_id,
            "change": _thaw(self.change),
            "capsule_digest": self.capsule_digest,
            "impact_digest": self.impact_digest,
            "rank": self.rank.to_dict(),
            "provenance": _thaw(self.provenance),
            "status": self.status,
            "diagnostic": _thaw(self.diagnostic) if self.diagnostic is not None else None,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SynthesisCandidate":
        if not isinstance(value, Mapping) or set(value) != _CANDIDATE_FIELDS:
            raise WorldError("SynthesisCandidateSchemaMismatch")
        if not isinstance(value.get("change"), Mapping) or not isinstance(value.get("rank"), Mapping) or not isinstance(value.get("provenance"), Mapping):
            raise WorldError("SynthesisCandidateSchemaMismatch")
        if value.get("diagnostic") is not None and not isinstance(value.get("diagnostic"), Mapping):
            raise WorldError("SynthesisCandidateSchemaMismatch")
        if type(value.get("digest")) is not str or value["digest"] != _digest({key: value[key] for key in _CANDIDATE_FIELDS if key != "digest"}):
            raise WorldError("SynthesisCandidateDigestMismatch")
        return cls(
            producer=value["producer"],
            producer_revision=value["producer_revision"],
            base_world_digest=value["base_world_digest"],
            target_symbol_id=value["target_symbol_id"],
            change=value["change"],
            capsule_digest=value["capsule_digest"],
            impact_digest=value["impact_digest"],
            rank=CandidateRank.from_dict(value["rank"]),
            provenance=value["provenance"],
            status=value["status"],
            diagnostic=value["diagnostic"],
            schema_version=value["schema_version"],
            contract=value["contract"],
            digest=value["digest"],
        )

    @classmethod
    def from_json(cls, value: str) -> "SynthesisCandidate":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise WorldError("SynthesisCandidateSchemaMismatch") from exc
        return cls.from_dict(payload)


def _request(value: SynthesisRequest | Mapping[str, Any]) -> SynthesisRequest:
    if isinstance(value, SynthesisRequest):
        return value
    if isinstance(value, Mapping):
        return SynthesisRequest.from_dict(value)
    raise WorldError("SynthesisRequestSchemaMismatch")


def build_synthesis_candidate(
    world: SemanticWorld,
    request: SynthesisRequest | Mapping[str, Any],
    change: ChangeIR,
    *,
    producer: str,
    producer_revision: str,
    rank: CandidateRank,
    provenance: Mapping[str, Any],
) -> SynthesisCandidate:
    """Validate and bind one producer's canonical ChangeIR candidate."""

    if not isinstance(world, SemanticWorld):
        raise WorldError("SynthesisWorldMismatch")
    active = _request(request)
    if not isinstance(change, ChangeIR):
        raise WorldError("SynthesisChangeBindingMismatch")
    world.require_fresh()
    if active.world_digest != world.digest:
        raise StaleWorldError("StaleWorld: synthesis request belongs to another world")
    symbol = world.resolve(active.target)
    for key in (
        "symbol_id",
        "revision_id",
        "interface_revision_id",
        "implementation_revision_id",
    ):
        if str(symbol.get(key, "")) != str(getattr(change.target, key)):
            raise StaleWorldError(f"StaleWorld: target identity changed ({key})")
    if change.expected_world_digest != world.digest:
        raise StaleWorldError("StaleWorld: ChangeIR belongs to another world")
    if change.target.symbol_id != symbol["symbol_id"]:
        raise WorldError("SynthesisChangeTargetMismatch")
    if change.operation != active.operation:
        raise WorldError("SynthesisChangeOperationMismatch")
    if active.operation == "rename":
        if (
            change.metadata.get("old_name") != symbol.get("name")
            or change.metadata.get("new_name") != active.arguments.get("new_name")
        ):
            raise WorldError("SynthesisChangeArgumentsMismatch")
    elif active.operation == "move":
        if change.metadata.get("module") != active.arguments.get("module"):
            raise WorldError("SynthesisChangeArgumentsMismatch")
    elif active.operation == "change_signature":
        if change.metadata.get("signature") != active.arguments.get("signature"):
            raise WorldError("SynthesisChangeArgumentsMismatch")
    elif active.operation == "fill_hole":
        if (
            set(active.arguments)
            not in ({"hole_id"}, {"hole_id", "max_candidates"})
            or type(active.arguments.get("hole_id")) is not str
            or not active.arguments["hole_id"]
            or (
                "max_candidates" in active.arguments
                and (
                    type(active.arguments["max_candidates"]) is not int
                    or active.arguments["max_candidates"] <= 0
                )
            )
            or change.metadata.get("hole_id") != active.arguments["hole_id"]
        ):
            raise WorldError("SynthesisChangeArgumentsMismatch")
    capsule = world.compile_context(active.target, goal=active.goal)
    impact = world.change_impact(change)
    if (
        capsule.world_digest != world.digest
        or capsule.target_revision_id != symbol["revision_id"]
    ):
        raise WorldError("SynthesisCapsuleBindingMismatch")
    if (
        impact.world_digest != world.digest
        or impact.change_digest != change.digest
        or impact.target_symbol_id != symbol["symbol_id"]
    ):
        raise WorldError("SynthesisImpactBindingMismatch")
    blocked = change.status == "unsupported"
    diagnostic = (
        change.diagnostic.to_dict()
        if blocked and change.diagnostic is not None
        else None
    )
    return SynthesisCandidate(
        producer=producer,
        producer_revision=producer_revision,
        base_world_digest=world.digest,
        target_symbol_id=symbol["symbol_id"],
        change=change.to_dict(),
        capsule_digest=capsule.digest,
        impact_digest=impact.digest,
        rank=rank,
        provenance=provenance,
        status="blocked" if blocked else "proposed",
        diagnostic=diagnostic,
    )


def synthesize_rewrites(
    world: SemanticWorld,
    request: SynthesisRequest | Mapping[str, Any],
) -> tuple[SynthesisCandidate, ...]:
    """Generate one deterministic candidate without applying it or using I/O services."""

    active = _request(request)
    if not isinstance(world, SemanticWorld):
        raise WorldError("SynthesisWorldMismatch")
    world.require_fresh()
    if active.world_digest != world.digest:
        raise StaleWorldError("StaleWorld: synthesis request belongs to another world")
    if active.operation == "rename":
        change = preview_rename(world, active.target, active.arguments["new_name"])
    elif active.operation == "move":
        change = preview_move(world, active.target, active.arguments["module"])
    elif active.operation == "change_signature":
        change = preview_change_signature(
            world,
            active.target,
            active.arguments["signature"],
        )
    else:  # SynthesisRequest validates this; retain a defensive source-level guard.
        raise WorldError(f"SynthesisUnknownOperation: {active.operation}")
    return (
        build_synthesis_candidate(
            world,
            active,
            change,
            producer="rewrite",
            producer_revision="v1",
            rank=CandidateRank(0, len(change.edits), change.digest),
            provenance={"request_digest": active.digest},
        ),
    )


__all__ = [
    "CANDIDATE_PRODUCERS",
    "CANDIDATE_STATUSES",
    "CandidateRank",
    "SYNTHESIS_CANDIDATE_CONTRACT",
    "SYNTHESIS_REQUEST_CONTRACT",
    "SYNTHESIS_SCHEMA_VERSION",
    "SynthesisCandidate",
    "SynthesisRequest",
    "build_synthesis_candidate",
    "synthesize_rewrites",
]
