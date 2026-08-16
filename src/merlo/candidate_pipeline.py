from __future__ import annotations

"""Deterministic, source-preserving verification and benchmarking of synthesis candidates.

The producer-facing synthesis schemas deliberately remain separate from this module.  A
producer can claim that a candidate is ready, but this module only accepts a candidate
whose exact ChangeIR can be applied to a temporary project and rebuilt by the production
compiler.
"""
import hashlib
import json
import math
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from merlo.compiler import compile_project
from merlo.refactor import ChangeIR, RefactorEdit, preview_fill_hole, preview_rename
from merlo.semantic_world import SemanticWorld, StaleWorldError, WorldError
from merlo.synthesis import SynthesisCandidate


SCHEMA_VERSION = 1
CANDIDATE_VERIFICATION_CONTRACT = "merlo.candidate-verification.v1"
CANDIDATE_BENCHMARK_CONTRACT = "merlo.candidate-benchmark.v1"
CANDIDATE_SELECTION_CONTRACT = "merlo.candidate-selection.v1"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorldError("CandidatePipelineNonFiniteNumber")
        raise WorldError("CandidatePipelineFloatForbidden")
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _required(value: Any, fields: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise WorldError(code)
    return value


def _text(value: Any, code: str, *, empty: bool = False) -> str:
    if type(value) is not str or (not empty and not value):
        raise WorldError(code)
    return value


def _diagnostic(code: str, message: str, details: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    return _freeze({"code": str(code), "message": str(message), "details": dict(details or {})})


def _diagnostic_from_exception(exc: BaseException, root: Path | None = None) -> Mapping[str, Any]:
    message = str(exc)
    if root is not None:
        message = message.replace(str(root), "<isolated>")
    return _diagnostic(type(exc).__name__, message or type(exc).__name__, {"exception": type(exc).__name__})


def _candidate_digest(value: Any) -> str:
    try:
        if isinstance(value, SynthesisCandidate):
            return value.digest
        if isinstance(value, Mapping) and type(value.get("digest")) is str and value["digest"]:
            return value["digest"]
        return _digest(value)
    except Exception:
        return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def _hole(symbol: Mapping[str, Any], hole_id: str) -> Mapping[str, Any] | None:
    holes = symbol.get("holes", ())
    if not isinstance(holes, (list, tuple)):
        return None
    matches = tuple(item for item in holes if isinstance(item, Mapping) and item.get("hole_id") == hole_id)
    return matches[0] if len(matches) == 1 else None


def _digest_sequence(value: Any) -> str:
    return _digest(value)


def _symbol_snapshot(symbol: Mapping[str, Any]) -> dict[str, Any]:
    holes = tuple(_thaw(item) for item in symbol.get("holes", ()))
    holes = tuple(sorted(holes, key=lambda item: str(item.get("hole_id", "")) if isinstance(item, Mapping) else repr(item)))
    return {
        "type_digest": _digest_sequence({"signature": symbol.get("signature", ""), "types": tuple(sorted(symbol.get("types", ())))}),
        "effect_digest": _digest_sequence(tuple(sorted(str(item) for item in symbol.get("effects", ())))),
        "capability_digest": _digest_sequence(tuple(sorted(str(item) for item in symbol.get("capabilities", ())))),
        "hole_digest": _digest_sequence(holes),
    }


def _relative(root: Path, value: str | Path) -> str:
    path = Path(value).resolve()
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise WorldError("CandidateEditEscapesProject") from exc


def _world_files(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def _map_hole(original: Mapping[str, Any], isolated: Mapping[str, Any], hole_id: str) -> str:
    source = _hole(original, hole_id)
    if source is None:
        raise WorldError("CandidateHoleIdentityMismatch")
    candidates = []
    for item in isolated.get("holes", ()):
        if not isinstance(item, Mapping):
            continue
        if item.get("expected_type") != source.get("expected_type"):
            continue
        if item.get("context") != source.get("context") or item.get("callables") != source.get("callables"):
            continue
        left = source.get("source", {})
        right = item.get("source", {})
        if all(left.get(key) == right.get(key) for key in ("line", "column", "end_line", "end_column")):
            candidates.append(item)
    if len(candidates) != 1 or not isinstance(candidates[0].get("hole_id"), str):
        raise WorldError("CandidateHoleIdentityNotMapped")
    return str(candidates[0]["hole_id"])


def _clone_change(change: ChangeIR, original: SemanticWorld, isolated: SemanticWorld, target: Mapping[str, Any], hole_id: str | None = None) -> ChangeIR:
    edits: list[RefactorEdit] = []
    for edit in change.edits:
        relative = _relative(original.root, edit.path)
        mapped = isolated.root / relative
        edits.append(RefactorEdit(**{**edit.to_dict(), "path": str(mapped.resolve()), "symbol_id": str(target["symbol_id"])}))
    metadata = _thaw(change.metadata)
    if hole_id is not None:
        metadata["hole_id"] = hole_id
    return ChangeIR(
        operation=change.operation,
        status=change.status,
        target={
            "symbol_id": str(target["symbol_id"]),
            "revision_id": str(target["revision_id"]),
            "interface_revision_id": str(target["interface_revision_id"]),
            "implementation_revision_id": str(target["implementation_revision_id"]),
        },
        expected_world_digest=isolated.digest,
        metadata=metadata,
        edits=tuple(edits),
        diagnostic=None,
        world=isolated,
    )


def _map_target(world: SemanticWorld, isolated: SemanticWorld, symbol_id: str) -> Mapping[str, Any]:
    source = world.resolve(symbol_id)
    relative = _relative(world.root, source["source"]["path"])
    matches = tuple(
        item for item in isolated.data.get("symbols", ())
        if item.get("symbol_id") == source.get("symbol_id")
        and item.get("qualified_name") == source.get("qualified_name")
        and item.get("name") == source.get("name")
        and item.get("kind") == source.get("kind")
        and _relative(isolated.root, item.get("source", {}).get("path", "")) == relative
    )
    if len(matches) != 1:
        raise WorldError("CandidateTargetIdentityNotMapped")
    target = matches[0]
    for key in ("symbol_id", "revision_id", "interface_revision_id", "implementation_revision_id"):
        if str(target.get(key, "")) != str(source.get(key, "")):
            raise StaleWorldError(f"CandidateTargetIdentityChanged:{key}")
    return target


def _semantic_diff(before: Mapping[str, Any], after: Mapping[str, Any], change: ChangeIR, root: Path) -> dict[str, Any]:
    node: dict[str, Any] = {"kind": change.operation}
    if change.operation == "fill_hole":
        node.update({"kind": "TypedHole", "node_id": str(change.metadata.get("hole_id", ""))})
    elif change.edits:
        node.update({"node_id": change.edits[0].syntax_id, "kind": change.edits[0].kind})
    path = before.get("source", {}).get("path")
    if isinstance(path, str):
        node["path"] = _relative(root, path)
    return {
        "target": {
            "symbol_id": str(before.get("symbol_id", "")),
            "revision_id": str(before.get("revision_id", "")),
            "qualified_name": str(before.get("qualified_name", "")),
        },
        "operation": change.operation,
        "changed_node": node,
        "before": _symbol_snapshot(before),
        "after": _symbol_snapshot(after),
    }


def _artifact(candidate: SynthesisCandidate, compilation: Any, world: SemanticWorld, diff: Mapping[str, Any], root: Path) -> dict[str, Any]:
    changed = tuple(sorted(_relative(root, edit.path) for edit in candidate.change_ir.edits))
    return {
        "candidate_digest": candidate.digest,
        "operation": candidate.change_ir.operation,
        "target_symbol_id": candidate.target_symbol_id,
        "output_digest": compilation.digest,
        "semantic_digest": _digest(diff),
        "changed_files": changed,
        "source_digests": dict(sorted((str(k), str(v)) for k, v in world.data.get("source_hashes", {}).items())),
        "priority": candidate.rank.priority,
        "cost": candidate.rank.cost,
        "tie_breaker": candidate.rank.tie_breaker,
        "verification_summary": verification_summary(
            compilation.verification_metrics
        ),
    }


def verification_summary(report: Any) -> dict[str, Any]:
    """Return path- and identity-independent verification evidence."""
    return {
        "total_obligations": report.total_obligations,
        "automatically_closed": report.automatically_closed,
        "refuted": report.refuted,
        "runtime_guarded": report.runtime_guarded,
        "unresolved": report.unresolved,
        "closed_rate_basis_points": report.closed_rate_basis_points,
        "categories": [
            item.to_dict() for item in report.categories
        ],
        "states": sorted(
            (
                {
                    "category": item.category.value,
                    "state": item.state.value,
                    "statuses": list(item.statuses),
                }
                for item in report.obligations
            ),
            key=_json,
        ),
    }
@dataclass(frozen=True)
class CandidateVerification:
    candidate_digest: str
    status: str
    diagnostic: Mapping[str, Any] | None = None
    artifact: Mapping[str, Any] | None = None
    semantic_diff: Mapping[str, Any] | None = None
    schema_version: int = SCHEMA_VERSION
    contract: str = CANDIDATE_VERIFICATION_CONTRACT
    digest: str = ""
    def __post_init__(self) -> None:
        _text(self.candidate_digest, "CandidateVerificationInvalidDigest")
        if type(self.status) is not str or self.status not in {"verified", "rejected"}:
            raise WorldError("CandidateVerificationInvalidStatus")
        if self.schema_version != SCHEMA_VERSION or self.contract != CANDIDATE_VERIFICATION_CONTRACT:
            raise WorldError("CandidateVerificationVersionMismatch")
        for name in ("diagnostic", "artifact", "semantic_diff"):
            value = getattr(self, name)
            if value is not None:
                _freeze(value)
                object.__setattr__(self, name, _freeze(value))
        if self.status == "verified" and (self.artifact is None or self.semantic_diff is None or self.diagnostic is not None):
            raise WorldError("CandidateVerificationInvariant")
        if self.status == "rejected" and self.diagnostic is None:
            raise WorldError("CandidateVerificationInvariant")
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise WorldError("CandidateVerificationDigestMismatch")
        object.__setattr__(self, "digest", expected)

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    def _payload(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "contract": self.contract, "candidate_digest": self.candidate_digest, "status": self.status, "diagnostic": _thaw(self.diagnostic), "artifact": _thaw(self.artifact), "semantic_diff": _thaw(self.semantic_diff)}

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateVerification":
        fields = {"schema_version", "contract", "candidate_digest", "status", "diagnostic", "artifact", "semantic_diff", "digest"}
        _required(value, fields, "CandidateVerificationSchemaMismatch")
        if type(value["digest"]) is not str or value["digest"] != _digest({key: value[key] for key in fields if key != "digest"}):
            raise WorldError("CandidateVerificationDigestMismatch")
        return cls(**{key: value[key] for key in fields})

    @classmethod
    def from_json(cls, value: str) -> "CandidateVerification":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise WorldError("CandidateVerificationSchemaMismatch") from exc
        return cls.from_dict(payload)


@dataclass(frozen=True)
class CandidateBenchmark:
    candidate_digest: str
    status: str
    rank: Mapping[str, Any]
    measurements: Mapping[str, int]
    output_digest: str | None = None
    semantic_digest: str | None = None
    diagnostic: Mapping[str, Any] | None = None
    semantic_diff: Mapping[str, Any] | None = None
    schema_version: int = SCHEMA_VERSION
    contract: str = CANDIDATE_BENCHMARK_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        _text(self.candidate_digest, "CandidateBenchmarkInvalidDigest")
        if self.status not in {"verified", "rejected"}:
            raise WorldError("CandidateBenchmarkInvalidStatus")
        if self.schema_version != SCHEMA_VERSION or self.contract != CANDIDATE_BENCHMARK_CONTRACT:
            raise WorldError("CandidateBenchmarkVersionMismatch")
        _required(self.rank, {"priority", "cost", "tie_breaker"}, "CandidateBenchmarkRankSchemaMismatch")
        if type(self.rank["priority"]) is not int or type(self.rank["cost"]) is not int or self.rank["priority"] < 0 or self.rank["cost"] < 0 or type(self.rank["tie_breaker"]) is not str:
            raise WorldError("CandidateBenchmarkInvalidRank")
        if not isinstance(self.measurements, Mapping) or any(type(k) is not str or type(v) is not int or isinstance(v, bool) or v < 0 for k, v in self.measurements.items()):
            raise WorldError("CandidateBenchmarkInvalidMeasurements")
        object.__setattr__(self, "rank", _freeze(self.rank))
        object.__setattr__(self, "measurements", _freeze(self.measurements))
        for name in ("diagnostic", "semantic_diff"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _freeze(value))
        if self.status == "verified" and (not self.output_digest or not self.semantic_digest or self.diagnostic is not None):
            raise WorldError("CandidateBenchmarkInvariant")
        if self.status == "rejected" and self.diagnostic is None:
            raise WorldError("CandidateBenchmarkInvariant")
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise WorldError("CandidateBenchmarkDigestMismatch")
        object.__setattr__(self, "digest", expected)

    def _payload(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "contract": self.contract, "candidate_digest": self.candidate_digest, "status": self.status, "rank": _thaw(self.rank), "measurements": _thaw(self.measurements), "output_digest": self.output_digest, "semantic_digest": self.semantic_digest, "diagnostic": _thaw(self.diagnostic), "semantic_diff": _thaw(self.semantic_diff)}

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateBenchmark":
        fields = {"schema_version", "contract", "candidate_digest", "status", "rank", "measurements", "output_digest", "semantic_digest", "diagnostic", "semantic_diff", "digest"}
        _required(value, fields, "CandidateBenchmarkSchemaMismatch")
        if type(value["digest"]) is not str or value["digest"] != _digest({key: value[key] for key in fields if key != "digest"}):
            raise WorldError("CandidateBenchmarkDigestMismatch")
        return cls(**{key: value[key] for key in fields})

    @classmethod
    def from_json(cls, value: str) -> "CandidateBenchmark":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise WorldError("CandidateBenchmarkSchemaMismatch") from exc
        return cls.from_dict(payload)


@dataclass(frozen=True)
class CandidateSelection:
    benchmarks: tuple[CandidateBenchmark, ...]
    selected_candidate_digest: str | None
    schema_version: int = SCHEMA_VERSION
    contract: str = CANDIDATE_SELECTION_CONTRACT
    digest: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.contract != CANDIDATE_SELECTION_CONTRACT:
            raise WorldError("CandidateSelectionVersionMismatch")
        items = tuple(self.benchmarks)
        if any(not isinstance(item, CandidateBenchmark) for item in items) or items != tuple(sorted(items, key=lambda item: item.candidate_digest)):
            raise WorldError("CandidateSelectionNonCanonical")
        object.__setattr__(self, "benchmarks", items)
        if self.selected_candidate_digest is not None and self.selected_candidate_digest not in {item.candidate_digest for item in items}:
            raise WorldError("CandidateSelectionUnknownCandidate")
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise WorldError("CandidateSelectionDigestMismatch")
        object.__setattr__(self, "digest", expected)

    @property
    def selected(self) -> CandidateBenchmark | None:
        return next((item for item in self.benchmarks if item.candidate_digest == self.selected_candidate_digest), None)

    def __iter__(self):
        return iter(self.benchmarks)

    def __len__(self) -> int:
        return len(self.benchmarks)

    def __getitem__(self, index: int) -> CandidateBenchmark:
        return self.benchmarks[index]

    def _payload(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "contract": self.contract, "benchmarks": [item.to_dict() for item in self.benchmarks], "selected_candidate_digest": self.selected_candidate_digest}

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CandidateSelection":
        fields = {"schema_version", "contract", "benchmarks", "selected_candidate_digest", "digest"}
        _required(value, fields, "CandidateSelectionSchemaMismatch")
        if not isinstance(value["benchmarks"], list) or type(value["digest"]) is not str or value["digest"] != _digest({key: value[key] for key in fields if key != "digest"}):
            raise WorldError("CandidateSelectionDigestMismatch")
        return cls(tuple(CandidateBenchmark.from_dict(item) for item in value["benchmarks"]), value["selected_candidate_digest"], value["schema_version"], value["contract"], value["digest"])

    @classmethod
    def from_json(cls, value: str) -> "CandidateSelection":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise WorldError("CandidateSelectionSchemaMismatch") from exc
        return cls.from_dict(payload)


def _reject(candidate_digest: str, code: str, message: str, details: Mapping[str, Any] | None = None) -> CandidateVerification:
    return CandidateVerification(candidate_digest, "rejected", diagnostic=_diagnostic(code, message, details))


def _verify_one(world: SemanticWorld, raw: Any, original_files: Mapping[str, bytes]) -> CandidateVerification:
    digest = _candidate_digest(raw)
    try:
        candidate = raw if isinstance(raw, SynthesisCandidate) else SynthesisCandidate.from_dict(raw)
        # Reparse even instances, so a producer cannot smuggle mutable/tampered state.
        candidate = SynthesisCandidate.from_dict(candidate.to_dict())
        world.require_fresh()
        if candidate.base_world_digest != world.digest or candidate.change_ir.expected_world_digest != world.digest:
            raise StaleWorldError("StaleWorld: candidate belongs to another world")
        if candidate.status != "proposed" or candidate.change_ir.status != "ready":
            raise WorldError("CandidateNotReady")
        symbol = world.resolve(candidate.target_symbol_id)
        change = candidate.change_ir
        if change.target.to_dict() != {key: symbol[key] for key in ("symbol_id", "revision_id", "interface_revision_id", "implementation_revision_id")}:
            raise StaleWorldError("CandidateTargetIdentityMismatch")
        request_goal = candidate.provenance.get("request_goal", "")
        if type(request_goal) is not str:
            raise WorldError("CandidateRequestGoalMismatch")
        capsule = world.compile_context(
            symbol["symbol_id"],
            goal=request_goal,
        )
        if candidate.capsule_digest != capsule.digest:
            raise WorldError("CandidateCapsuleDigestMismatch")
        impact = world.change_impact(change)
        if candidate.impact_digest != impact.digest or impact.target_symbol_id != symbol["symbol_id"] or impact.change_digest != change.digest:
            raise WorldError("CandidateImpactDigestMismatch")
        for edit in change.edits:
            relative = _relative(world.root, edit.path)
            if relative not in original_files:
                raise StaleWorldError("CandidateEditSourceMissing")
        with tempfile.TemporaryDirectory(prefix="merlo-candidate-") as directory:
            isolated_root = Path(directory) / "project"
            shutil.copytree(world.root, isolated_root, symlinks=False)
            target_entry = Path(world.data.get("entry_path", ""))
            entry = isolated_root / _relative(world.root, target_entry)
            lock_path = world.data.get("lockfile_path")
            clone_lock = isolated_root / _relative(world.root, lock_path) if isinstance(lock_path, str) and Path(lock_path).is_file() and Path(lock_path).resolve().is_relative_to(world.root.resolve()) else None
            compilation_before = compile_project(entry, require_interface_lock=False)
            isolated_world = SemanticWorld.build(compilation_before, state_path=isolated_root / ".merlo" / "world.json", lockfile=clone_lock, require_interface_lock=False)
            isolated_target = _map_target(world, isolated_world, candidate.target_symbol_id)
            hole_id = change.metadata.get("hole_id") if change.operation == "fill_hole" else None
            mapped_hole_id = None
            if hole_id is not None:
                if _hole(symbol, str(hole_id)) is None:
                    raise WorldError("CandidateHoleIdentityMismatch")
                mapped_hole_id = _map_hole(symbol, isolated_target, str(hole_id))
            mapped_candidate = _clone_change(change, world, isolated_world, isolated_target, mapped_hole_id)
            if change.operation == "fill_hole":
                isolated_change = preview_fill_hole(isolated_world, str(isolated_target["symbol_id"]), str(mapped_hole_id), str(change.metadata["replacement"]))
            elif change.operation == "rename":
                isolated_change = preview_rename(isolated_world, str(isolated_target["symbol_id"]), str(change.metadata["new_name"]))
            else:
                raise WorldError("CandidateOperationUnsupported")
            if len(mapped_candidate.edits) != len(isolated_change.edits) or any(
                (left.start, left.end, left.replacement, left.kind, left.token_id, left.token_ordinal)
                != (right.start, right.end, right.replacement, right.kind, right.token_id, right.token_ordinal)
                for left, right in zip(mapped_candidate.edits, isolated_change.edits)
            ):
                raise WorldError("CandidateSemanticEditMismatch")
            isolated_change.apply(isolated_world)
            compilation_after = compile_project(entry, require_interface_lock=False)
            before_metrics = compilation_before.verification_metrics
            after_metrics = compilation_after.verification_metrics
            if after_metrics.refuted > before_metrics.refuted:
                raise WorldError("CandidateIntroducedRefutedObligation")
            if (
                change.operation == "fill_hole"
                and after_metrics.unresolved >= before_metrics.unresolved
            ):
                raise WorldError("CandidateDidNotCloseTypedHoleObligation")
            after_world = SemanticWorld.build(compilation_after, state_path=isolated_root / ".merlo" / "world-after.json", lockfile=clone_lock, require_interface_lock=False)
            after_target = after_world.resolve(candidate.target_symbol_id)
            if mapped_hole_id is not None and _hole(after_target, mapped_hole_id) is not None:
                raise WorldError("CandidateHoleNotRemoved")
            diff = _semantic_diff(symbol, after_target, change, world.root)
            artifact = _artifact(candidate, compilation_after, after_world, diff, world.root)
            return CandidateVerification(candidate.digest, "verified", artifact=artifact, semantic_diff=diff)
    except Exception as exc:
        return _reject(digest, type(exc).__name__, str(exc) or type(exc).__name__)


def verify_candidates(world: SemanticWorld, candidates: Iterable[Any]) -> tuple[CandidateVerification, ...]:
    if not isinstance(world, SemanticWorld):
        raise WorldError("CandidateWorldRequired")
    values = tuple(candidates)
    try:
        before = _world_files(world.root)
    except Exception as exc:
        raise WorldError("CandidateWorldUnreadable") from exc
    reports = tuple(_verify_one(world, item, before) for item in values)
    # Verification is required to be observational: never silently accept an accidental
    # write by a compiler/plugin into the caller's project.
    if _world_files(world.root) != before:
        raise WorldError("CandidateVerificationModifiedOriginal")
    return reports


def _benchmark_diagnostic(code: str, message: str) -> Mapping[str, Any]:
    return _diagnostic(code, message)


def _measurement_tuple(values: Mapping[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((str(k), int(v)) for k, v in values.items()))


def _call_evaluator(evaluator: Callable[[Mapping[str, Any]], Mapping[str, Any]], artifact: Mapping[str, Any]) -> tuple[str, str, Mapping[str, int]]:
    result = evaluator(_thaw(artifact))
    _required(result, {"output_digest", "semantic_digest", "measurements"}, "CandidateEvaluatorSchemaMismatch")
    _text(result["output_digest"], "CandidateEvaluatorOutputDigest")
    _text(result["semantic_digest"], "CandidateEvaluatorSemanticDigest")
    measurements = result["measurements"]
    if not isinstance(measurements, Mapping) or any(type(k) is not str or not k or type(v) is not int or isinstance(v, bool) or v < 0 for k, v in measurements.items()):
        raise WorldError("CandidateEvaluatorMeasurementsMismatch")
    return result["output_digest"], result["semantic_digest"], dict(sorted(measurements.items()))


def benchmark_candidates(world: SemanticWorld, verifications: Iterable[Any], evaluator: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None) -> CandidateSelection:
    if not isinstance(world, SemanticWorld):
        raise WorldError("CandidateWorldRequired")
    values = tuple(item if isinstance(item, CandidateVerification) else CandidateVerification.from_dict(item) for item in verifications)
    world.require_fresh()
    reports: list[CandidateBenchmark] = []
    for verification in values:
        artifact = verification.artifact
        rank = {"priority": 2**31 - 1, "cost": 2**31 - 1, "tie_breaker": verification.candidate_digest}
        if artifact is not None:
            # The rank is not repeated in verification; candidate rank is carried by the
            # artifact only when a producer supplies it.  The stable digest remains the
            # final tie breaker in all cases.
            rank = {"priority": int(artifact.get("priority", rank["priority"])), "cost": int(artifact.get("cost", rank["cost"])), "tie_breaker": verification.candidate_digest}
        if not verification.verified:
            reports.append(CandidateBenchmark(verification.candidate_digest, "rejected", rank, {}, diagnostic=verification.diagnostic, semantic_diff=verification.semantic_diff))
            continue
        try:
            if evaluator is None:
                output_digest = str(artifact["output_digest"])
                semantic_digest = str(artifact["semantic_digest"])
                measurements: Mapping[str, int] = {}
            else:
                if not callable(evaluator):
                    raise WorldError("CandidateEvaluatorRequired")
                output_digest, semantic_digest, measurements = _call_evaluator(evaluator, artifact)
                if output_digest != artifact["output_digest"] or semantic_digest != artifact["semantic_digest"]:
                    raise WorldError("CandidateEvaluatorDigestMismatch")
            reports.append(CandidateBenchmark(verification.candidate_digest, "verified", rank, measurements, output_digest, semantic_digest, semantic_diff=verification.semantic_diff))
        except Exception as exc:
            reports.append(CandidateBenchmark(verification.candidate_digest, "rejected", rank, {}, diagnostic=_benchmark_diagnostic(type(exc).__name__, str(exc) or type(exc).__name__), semantic_diff=verification.semantic_diff))
    # CandidateSelection canonicalizes by candidate digest, while ranking is exposed by
    # the order of the benchmark records only through the selected key.  Keep deterministic
    # ranking independent of hash-map insertion order.
    def key(item: CandidateBenchmark) -> tuple[Any, ...]:
        return (0 if item.status == "verified" else 1, int(item.rank["priority"]), int(item.rank["cost"]), _measurement_tuple(item.measurements), item.candidate_digest)
    ranked = tuple(sorted(reports, key=key))
    selected = next((item.candidate_digest for item in ranked if item.status == "verified"), None)
    return CandidateSelection(tuple(sorted(ranked, key=lambda item: item.candidate_digest)), selected)


__all__ = [
    "CANDIDATE_BENCHMARK_CONTRACT",
    "CANDIDATE_SELECTION_CONTRACT",
    "CANDIDATE_VERIFICATION_CONTRACT",
    "CandidateBenchmark",
    "CandidateSelection",
    "CandidateVerification",
    "benchmark_candidates",
    "verify_candidates",
    "verification_summary",
]
