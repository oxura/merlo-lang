from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from merlo.candidate_pipeline import (
    CandidateSelection,
    CandidateVerification,
    benchmark_candidates,
    verification_summary,
    verify_candidates,
)
from merlo.compiler import compile_project
from merlo.enumerative_search import enumerate_candidates
from merlo.package_search import search_package_candidates
from merlo.semantic_world import SemanticWorld, WorldError
from merlo.symbolic_search import search_symbolic_candidates
from merlo.synthesis import SynthesisCandidate, SynthesisRequest
from merlo.transaction import load_transaction


SYNTHESIS_RUN_SCHEMA_VERSION = 1
SYNTHESIS_RUN_CONTRACT = "merlo.synthesis-run.v1"


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _plain(value: Any) -> Any:
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class SynthesisRun:
    request: SynthesisRequest
    candidates: tuple[SynthesisCandidate, ...]
    verifications: tuple[CandidateVerification, ...]
    selection: CandidateSelection
    schema_version: int = SYNTHESIS_RUN_SCHEMA_VERSION
    contract: str = SYNTHESIS_RUN_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            self.schema_version != SYNTHESIS_RUN_SCHEMA_VERSION
            or self.contract != SYNTHESIS_RUN_CONTRACT
        ):
            raise WorldError("SynthesisRunVersionMismatch")
        if not isinstance(self.request, SynthesisRequest):
            raise WorldError("SynthesisRunRequestMismatch")
        candidate_ids = tuple(item.digest for item in self.candidates)
        if candidate_ids != tuple(sorted(set(candidate_ids))):
            raise WorldError("SynthesisRunCandidatesNotCanonical")
        verification_ids = tuple(
            item.candidate_digest for item in self.verifications
        )
        if verification_ids != candidate_ids:
            raise WorldError("SynthesisRunVerificationMismatch")
        if {
            item.candidate_digest for item in self.selection.benchmarks
        } != set(candidate_ids):
            raise WorldError("SynthesisRunSelectionMismatch")
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise WorldError("SynthesisRunDigestMismatch")
        object.__setattr__(self, "digest", expected)

    @property
    def selected_candidate(self) -> SynthesisCandidate | None:
        selected = self.selection.selected_candidate_digest
        return next(
            (item for item in self.candidates if item.digest == selected),
            None,
        )

    @property
    def selected_verification(self) -> CandidateVerification | None:
        selected = self.selection.selected_candidate_digest
        return next(
            (
                item
                for item in self.verifications
                if item.candidate_digest == selected
            ),
            None,
        )

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "request": self.request.to_dict(),
            "candidates": [item.to_dict() for item in self.candidates],
            "verifications": [
                item.to_dict() for item in self.verifications
            ],
            "selection": self.selection.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SynthesisRun":
        fields = {
            "schema_version",
            "contract",
            "request",
            "candidates",
            "verifications",
            "selection",
            "digest",
        }
        if not isinstance(value, Mapping) or set(value) != fields:
            raise WorldError("SynthesisRunSchemaMismatch")
        if not all(
            isinstance(value.get(name), expected)
            for name, expected in (
                ("request", Mapping),
                ("candidates", list),
                ("verifications", list),
                ("selection", Mapping),
            )
        ):
            raise WorldError("SynthesisRunSchemaMismatch")
        payload = {key: value[key] for key in fields if key != "digest"}
        if type(value.get("digest")) is not str or value["digest"] != _digest(
            payload
        ):
            raise WorldError("SynthesisRunDigestMismatch")
        return cls(
            request=SynthesisRequest.from_dict(value["request"]),
            candidates=tuple(
                SynthesisCandidate.from_dict(item)
                for item in value["candidates"]
            ),
            verifications=tuple(
                CandidateVerification.from_dict(item)
                for item in value["verifications"]
            ),
            selection=CandidateSelection.from_dict(value["selection"]),
            schema_version=value["schema_version"],
            contract=value["contract"],
            digest=value["digest"],
        )

    @classmethod
    def from_json(cls, value: str) -> "SynthesisRun":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise WorldError("SynthesisRunSchemaMismatch") from exc
        return cls.from_dict(payload)

    def apply(self, world: SemanticWorld) -> dict[str, Any]:
        if not isinstance(world, SemanticWorld):
            raise WorldError("SynthesisRunWorldRequired")
        world.require_fresh()
        if world.digest != self.request.world_digest:
            raise WorldError("StaleWorld: synthesis run belongs to another world")
        candidate = self.selected_candidate
        verification = self.selected_verification
        if candidate is None or verification is None or not verification.verified:
            raise WorldError("SynthesisRunHasNoVerifiedSelection")
        repeated = verify_candidates(world, (candidate,))[0]
        if repeated.to_json() != verification.to_json():
            raise WorldError("SynthesisRunVerificationChanged")
        receipt = candidate.change_ir.apply(world)
        transaction = load_transaction(
            world.root,
            receipt["transaction"]["transaction_id"],
        )
        try:
            entry = world.data.get("entry_path", world.root / "src" / "main.mlo")
            compilation = compile_project(entry, require_interface_lock=False)
            artifact = verification.artifact
            after_world = SemanticWorld.build(
                compilation,
                state_path=world.state_path,
                lockfile=world.data.get("lockfile_path"),
                require_interface_lock=False,
            )
            actual_sources = dict(
                sorted(after_world.data.get("source_hashes", {}).items())
            )
            mismatches = []
            if artifact is None:
                mismatches.append("missing_artifact")
            else:
                if verification_summary(compilation.verification_metrics) != (
                    _plain(artifact.get("verification_summary"))
                ):
                    mismatches.append("verification")
                if actual_sources != _plain(artifact.get("source_digests")):
                    mismatches.append("sources")
            if mismatches:
                raise WorldError(
                    "SynthesisApplyArtifactMismatch:"
                    + ",".join(mismatches)
                )
            target = after_world.resolve(candidate.target_symbol_id)
            hole_id = str(candidate.change_ir.metadata.get("hole_id", ""))
            if any(
                item.get("hole_id") == hole_id
                for item in target.get("holes", ())
            ):
                raise WorldError("SynthesisApplyHoleNotRemoved")
            after_world.save(world.state_path)
            return {
                "status": "committed",
                "run_digest": self.digest,
                "candidate_digest": candidate.digest,
                "before_world_digest": world.digest,
                "after_world_digest": after_world.digest,
                "transaction": receipt["transaction"],
                "verification_metrics": (
                    compilation.verification_metrics.to_dict()
                ),
            }
        except Exception as exc:
            rollback = transaction.rollback().to_dict()
            restored = SemanticWorld.build(
                world.data.get("entry_path", world.root / "src" / "main.mlo"),
                state_path=world.state_path,
                lockfile=world.data.get("lockfile_path"),
                require_interface_lock=False,
            )
            if restored.digest != world.digest:
                raise WorldError("SynthesisRollbackWorldDigestMismatch") from exc
            restored.save(world.state_path)
            return {
                "status": "rolled_back",
                "run_digest": self.digest,
                "candidate_digest": candidate.digest,
                "before_world_digest": world.digest,
                "after_world_digest": None,
                "transaction": receipt["transaction"],
                "rollback": rollback,
                "diagnostic": {
                    "code": type(exc).__name__,
                    "message": str(exc) or type(exc).__name__,
                },
            }


def synthesize_typed_hole(
    world: SemanticWorld,
    target: str,
    hole_id: str,
    *,
    goal: str = "",
    max_candidates: int = 16,
) -> SynthesisRun:
    if not isinstance(world, SemanticWorld):
        raise WorldError("SynthesisWorldMismatch")
    world.require_fresh()
    bounded = SynthesisRequest(
        world.digest,
        target,
        "fill_hole",
        {"hole_id": hole_id, "max_candidates": max_candidates},
        goal,
    )
    symbolic = SynthesisRequest(
        world.digest,
        target,
        "fill_hole",
        {"hole_id": hole_id},
        goal,
    )
    generated = (
        *enumerate_candidates(world, bounded),
        *search_symbolic_candidates(world, symbolic),
        *search_package_candidates(world, bounded),
    )
    candidates = tuple(sorted(generated, key=lambda item: item.digest))
    verifications = tuple(
        sorted(
            verify_candidates(world, candidates),
            key=lambda item: item.candidate_digest,
        )
    )
    selection = benchmark_candidates(world, verifications)
    return SynthesisRun(bounded, candidates, verifications, selection)


__all__ = [
    "SYNTHESIS_RUN_CONTRACT",
    "SYNTHESIS_RUN_SCHEMA_VERSION",
    "SynthesisRun",
    "synthesize_typed_hole",
]
