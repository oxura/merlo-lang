from __future__ import annotations

import hashlib
import json
import platform
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any, Callable, Iterable, Mapping

from .evidence import dependency_revision
from .model import EvidenceDependency, ProgramIR


OBSERVATIONAL_COVERAGE = "observational"
UNSEEN_TARGET_WARNING = (
    "Observed targets are not exhaustive; targets unseen in this trace remain possible."
)


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reference_revision(program: ProgramIR, reference_id: str) -> str | None:
    for reference in program.references:
        if reference.id == reference_id:
            return _stable_hash(reference.to_dict())
    return None


def _dependency_revision(
    program: ProgramIR, dependency: EvidenceDependency
) -> str | None:
    if dependency.kind == "reference":
        return _reference_revision(program, dependency.key)
    return dependency_revision(program, dependency.kind, dependency.key)


def _normalise_counts(
    counts: Mapping[str, int] | Iterable[tuple[str, int]],
) -> tuple[tuple[str, int], ...]:
    source = counts.items() if isinstance(counts, Mapping) else counts
    merged: dict[str, int] = {}
    for target_id, count in source:
        count = int(count)
        if count < 0:
            raise ValueError("observed call counts cannot be negative")
        if count:
            merged[str(target_id)] = merged.get(str(target_id), 0) + count
    return tuple(sorted(merged.items()))


def observation_dependencies(
    program: ProgramIR,
    reference_id: str,
    target_ids: Iterable[str] = (),
) -> tuple[EvidenceDependency, ...]:
    """Capture only revisions on which this observation's interpretation depends."""

    dependencies: list[EvidenceDependency] = []
    reference_revision = _reference_revision(program, reference_id)
    if reference_revision is not None:
        dependencies.append(
            EvidenceDependency("reference", reference_id, reference_revision)
        )
        reference = next(ref for ref in program.references if ref.id == reference_id)
        if reference.owner_id is not None:
            try:
                owner = program.entity(reference.owner_id)
            except KeyError:
                pass
            else:
                dependencies.append(
                    EvidenceDependency("entity", owner.id, owner.revision_hash)
                )
    for target_id in sorted(set(target_ids)):
        try:
            target = program.entity(target_id)
        except KeyError:
            continue
        dependencies.append(
            EvidenceDependency("entity", target.id, target.revision_hash)
        )
    unique = {
        (item.kind, item.key, item.revision): item for item in dependencies
    }
    return tuple(unique[key] for key in sorted(unique))


@dataclass(frozen=True)
class ObservedReference:
    """Runtime evidence for one static reference/callsite.

    This is positive, observational evidence only.  It deliberately cannot express
    exhaustive coverage, so an unobserved target is never ruled out and a static
    resolution is never promoted to ``Exact``.
    """

    reference_id: str
    callsite_id: str = ""
    target_counts: tuple[tuple[str, int], ...] = ()
    environments: tuple[str, ...] = ()
    trace_hashes: tuple[str, ...] = ()
    artifact_hashes: tuple[str, ...] = ()
    observed_at: tuple[str, ...] = ()
    dependencies: tuple[EvidenceDependency, ...] = ()
    coverage: str = field(default=OBSERVATIONAL_COVERAGE, init=False)
    unseen_targets_possible: bool = field(default=True, init=False)
    resolution: str = field(default="Observed", init=False)

    def __post_init__(self) -> None:
        if not self.reference_id:
            raise ValueError("reference_id is required")
        object.__setattr__(self, "callsite_id", self.callsite_id or self.reference_id)
        object.__setattr__(self, "target_counts", _normalise_counts(self.target_counts))
        object.__setattr__(self, "environments", tuple(sorted(set(self.environments))))
        object.__setattr__(self, "trace_hashes", tuple(sorted(set(self.trace_hashes))))
        object.__setattr__(self, "artifact_hashes", tuple(sorted(set(self.artifact_hashes))))
        object.__setattr__(self, "observed_at", tuple(sorted(set(self.observed_at))))
        object.__setattr__(
            self,
            "dependencies",
            tuple(
                sorted(
                    set(self.dependencies),
                    key=lambda item: (item.kind, item.key, item.revision),
                )
            ),
        )

    @classmethod
    def capture(
        cls,
        program: ProgramIR,
        reference_id: str,
        target_counts: Mapping[str, int] | Iterable[tuple[str, int]],
        *,
        callsite_id: str = "",
        environments: Iterable[str] = (),
        trace_hash: str = "",
        artifact_hash: str = "",
        observed_at: str | Iterable[str] | None = None,
    ) -> "ObservedReference":
        counts = _normalise_counts(target_counts)
        if isinstance(observed_at, str):
            timestamps = (observed_at,)
        else:
            timestamps = tuple(observed_at or ())
        return cls(
            reference_id=reference_id,
            callsite_id=callsite_id,
            target_counts=counts,
            environments=tuple(environments),
            trace_hashes=(trace_hash,) if trace_hash else (),
            artifact_hashes=(artifact_hash,) if artifact_hash else (),
            observed_at=timestamps,
            dependencies=observation_dependencies(
                program, reference_id, (target_id for target_id, _ in counts)
            ),
        )

    @property
    def observed_target_ids(self) -> tuple[str, ...]:
        return tuple(target_id for target_id, _ in self.target_counts)

    @property
    def call_count(self) -> int:
        return sum(count for _, count in self.target_counts)

    @property
    def unseen_target_warning(self) -> str:
        return UNSEEN_TARGET_WARNING

    @property
    def deterministic_id(self) -> str:
        # Timestamps are provenance metadata, not semantic identity.
        payload = {
            "reference_id": self.reference_id,
            "callsite_id": self.callsite_id,
            "target_counts": self.target_counts,
            "environments": self.environments,
            "trace_hashes": self.trace_hashes,
            "artifact_hashes": self.artifact_hashes,
            "dependencies": tuple(
                (item.kind, item.key, item.revision) for item in self.dependencies
            ),
            "coverage": self.coverage,
            "resolution": self.resolution,
            "unseen_targets_possible": True,
        }
        return "obs_" + _stable_hash(payload)[:20]

    def count_for(self, target_id: str) -> int:
        return dict(self.target_counts).get(target_id, 0)

    def merge(self, *others: "ObservedReference") -> "ObservedReference":
        counts = dict(self.target_counts)
        environments = set(self.environments)
        trace_hashes = set(self.trace_hashes)
        artifact_hashes = set(self.artifact_hashes)
        observed_at = set(self.observed_at)
        dependencies = set(self.dependencies)
        for other in others:
            if (other.reference_id, other.callsite_id) != (
                self.reference_id,
                self.callsite_id,
            ):
                raise ValueError("only observations for the same callsite can be merged")
            for target_id, count in other.target_counts:
                counts[target_id] = counts.get(target_id, 0) + count
            environments.update(other.environments)
            trace_hashes.update(other.trace_hashes)
            artifact_hashes.update(other.artifact_hashes)
            observed_at.update(other.observed_at)
            dependencies.update(other.dependencies)
        return ObservedReference(
            reference_id=self.reference_id,
            callsite_id=self.callsite_id,
            target_counts=tuple(counts.items()),
            environments=tuple(environments),
            trace_hashes=tuple(trace_hashes),
            artifact_hashes=tuple(artifact_hashes),
            observed_at=tuple(observed_at),
            dependencies=tuple(dependencies),
        )

    def stale_reasons(self, program: ProgramIR) -> tuple[str, ...]:
        reasons: list[str] = []
        for dependency in self.dependencies:
            current = _dependency_revision(program, dependency)
            if current is None:
                reasons.append(f"missing {dependency.kind}:{dependency.key}")
            elif current != dependency.revision:
                reasons.append(
                    f"changed {dependency.kind}:{dependency.key} "
                    f"({dependency.revision[:12]} -> {current[:12]})"
                )
        return tuple(sorted(reasons))

    def is_stale(self, program: ProgramIR) -> bool:
        return bool(self.stale_reasons(program))

    def rebind(self, program: ProgramIR) -> "ObservedReference":
        """Rebind provenance after an actual replay, never as validation itself."""

        return replace(
            self,
            dependencies=observation_dependencies(
                program, self.reference_id, self.observed_target_ids
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.deterministic_id,
            "reference_id": self.reference_id,
            "callsite_id": self.callsite_id,
            "target_counts": [
                {"target_id": target_id, "call_count": count}
                for target_id, count in self.target_counts
            ],
            "observed_target_count": len(self.target_counts),
            "total_call_count": self.call_count,
            "environments": list(self.environments),
            "trace_hashes": list(self.trace_hashes),
            "artifact_hashes": list(self.artifact_hashes),
            "observed_at": list(self.observed_at),
            "dependencies": [item.to_dict() for item in self.dependencies],
            "coverage": self.coverage,
            "resolution": self.resolution,
            "unseen_targets_possible": True,
            "unseen_target_warning": UNSEEN_TARGET_WARNING,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservedReference":
        raw_counts = value.get("target_counts", ())
        if isinstance(raw_counts, Mapping):
            counts = tuple((str(key), int(count)) for key, count in raw_counts.items())
        else:
            counts = tuple(
                (str(item["target_id"]), int(item["call_count"]))
                if isinstance(item, Mapping)
                else (str(item[0]), int(item[1]))
                for item in raw_counts
            )
        raw_timestamps = value.get("observed_at", ())
        if isinstance(raw_timestamps, str):
            raw_timestamps = (raw_timestamps,)
        return cls(
            reference_id=str(value["reference_id"]),
            callsite_id=str(value.get("callsite_id", "")),
            target_counts=counts,
            environments=tuple(str(item) for item in value.get("environments", ())),
            trace_hashes=tuple(str(item) for item in value.get("trace_hashes", ())),
            artifact_hashes=tuple(
                str(item) for item in value.get("artifact_hashes", ())
            ),
            observed_at=tuple(str(item) for item in raw_timestamps),
            dependencies=tuple(
                EvidenceDependency.from_dict(dict(item))
                for item in value.get("dependencies", ())
            ),
        )


@dataclass(frozen=True)
class ObservationStore:
    observations: tuple[ObservedReference, ...] = ()
    schema: int = 1

    def __post_init__(self) -> None:
        merged: dict[tuple[str, str], ObservedReference] = {}
        for observation in self.observations:
            key = (observation.reference_id, observation.callsite_id)
            merged[key] = (
                merged[key].merge(observation) if key in merged else observation
            )
        object.__setattr__(
            self,
            "observations",
            tuple(merged[key] for key in sorted(merged)),
        )

    def merge(
        self, *items: "ObservationStore | ObservedReference"
    ) -> "ObservationStore":
        observations = list(self.observations)
        for item in items:
            if isinstance(item, ObservationStore):
                observations.extend(item.observations)
            else:
                observations.append(item)
        return ObservationStore(tuple(observations), schema=self.schema)

    def query(
        self,
        *,
        reference_id: str | None = None,
        callsite_id: str | None = None,
        target_id: str | None = None,
        program: ProgramIR | None = None,
        include_stale: bool = True,
    ) -> tuple[ObservedReference, ...]:
        matches = (
            observation
            for observation in self.observations
            if (reference_id is None or observation.reference_id == reference_id)
            and (callsite_id is None or observation.callsite_id == callsite_id)
            and (target_id is None or observation.count_for(target_id) > 0)
            and (
                include_stale
                or program is None
                or not observation.is_stale(program)
            )
        )
        return tuple(matches)

    def stale(self, program: ProgramIR) -> tuple[ObservedReference, ...]:
        return tuple(
            observation
            for observation in self.observations
            if observation.is_stale(program)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "observation_count": len(self.observations),
            "observations": [item.to_dict() for item in self.observations],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ObservationStore":
        schema = int(value.get("schema", 1))
        if schema != 1:
            raise ValueError(f"unsupported ObservationStore schema {schema}")
        return cls(
            observations=tuple(
                ObservedReference.from_dict(item)
                for item in value.get("observations", ())
            ),
            schema=schema,
        )

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    @classmethod
    def from_json(cls, payload: str) -> "ObservationStore":
        return cls.from_dict(json.loads(payload))


class RuntimeObserver:
    """Minimal project-local call profiler producing conservative observations."""

    def __init__(
        self,
        program: ProgramIR,
        *,
        environment: str | None = None,
        observed_at: str | None = None,
    ) -> None:
        self.program = program
        self.environment = environment or (
            f"{platform.python_implementation()}-{platform.python_version()}"
            f"|{platform.system()}-{platform.machine()}"
        )
        self.observed_at = observed_at
        self._root = Path(program.root).resolve()
        self._entity_by_code = self._build_entity_index()
        self._calls_by_location = self._build_callsite_index()
        self._counts: dict[tuple[str, str, str], int] = {}
        self._events: list[tuple[str, str, str]] = []
        self._previous_profile: Callable[..., Any] | None = None
        self._active = False
        self._store: ObservationStore | None = None

    def _normalise_file(self, filename: str) -> str | None:
        try:
            path = Path(filename).resolve()
            relative = path.relative_to(self._root)
        except (OSError, ValueError):
            return None
        return relative.as_posix()

    def _build_entity_index(self) -> dict[tuple[str, str], str]:
        index: dict[tuple[str, str], str] = {}
        for entity in self.program.entities:
            relative = self._normalise_file(str(self._root / entity.file))
            if relative is not None:
                index[(relative, entity.qualname)] = entity.id
        return index

    def _build_callsite_index(self) -> dict[tuple[str, int], tuple[Any, ...]]:
        calls: dict[tuple[str, int], list[Any]] = {}
        for call in self.program.calls:
            relative = self._normalise_file(str(self._root / call.file))
            if relative is None:
                continue
            calls.setdefault((relative, call.line), []).append(call)
        return {
            key: tuple(
                sorted(
                    values,
                    key=lambda call: (
                        call.target_id is not None,
                        not call.uncertain,
                        call.column,
                        call.id,
                    ),
                )
            )
            for key, values in calls.items()
        }

    def _entity_for_frame(self, frame: FrameType) -> str | None:
        relative = self._normalise_file(frame.f_code.co_filename)
        if relative is None:
            return None
        qualname = getattr(frame.f_code, "co_qualname", frame.f_code.co_name)
        return self._entity_by_code.get((relative, qualname))

    def _callsite_for_frame(self, frame: FrameType) -> tuple[str, str]:
        relative = self._normalise_file(frame.f_code.co_filename)
        if relative is None:
            return "", ""
        candidates = self._calls_by_location.get((relative, frame.f_lineno), ())
        if candidates:
            call = candidates[0]
            callsite_id = call.id or (
                f"call:{relative}:{call.line}:{call.column}"
            )
            return call.reference_id or callsite_id, callsite_id
        synthetic = f"runtime-call:{relative}:{frame.f_lineno}"
        return synthetic, synthetic

    def _profile(
        self,
        frame: FrameType,
        event: str,
        arg: Any,
    ) -> None:
        if self._previous_profile is not None:
            self._previous_profile(frame, event, arg)
        if event != "call":
            return
        target_id = self._entity_for_frame(frame)
        caller = frame.f_back
        if target_id is None or caller is None:
            return
        reference_id, callsite_id = self._callsite_for_frame(caller)
        if not reference_id:
            return
        key = (reference_id, callsite_id, target_id)
        self._counts[key] = self._counts.get(key, 0) + 1
        self._events.append(key)

    def __enter__(self) -> "RuntimeObserver":
        if self._active:
            raise RuntimeError("RuntimeObserver is already active")
        self._active = True
        self._store = None
        self._previous_profile = sys.getprofile()
        sys.setprofile(self._profile)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        sys.setprofile(self._previous_profile)
        self._active = False
        self._store = self._build_store()

    def _build_store(self) -> ObservationStore:
        trace_hash = _stable_hash(self._events)
        grouped: dict[tuple[str, str], dict[str, int]] = {}
        for (reference_id, callsite_id, target_id), count in self._counts.items():
            counts = grouped.setdefault((reference_id, callsite_id), {})
            counts[target_id] = counts.get(target_id, 0) + count
        observations: list[ObservedReference] = []
        for (reference_id, callsite_id), counts in sorted(grouped.items()):
            artifact_hash = _stable_hash(
                {
                    "reference_id": reference_id,
                    "callsite_id": callsite_id,
                    "target_counts": tuple(sorted(counts.items())),
                    "environment": self.environment,
                    "trace_hash": trace_hash,
                }
            )
            observations.append(
                ObservedReference.capture(
                    self.program,
                    reference_id,
                    counts,
                    callsite_id=callsite_id,
                    environments=(self.environment,),
                    trace_hash=trace_hash,
                    artifact_hash=artifact_hash,
                    observed_at=self.observed_at,
                )
            )
        return ObservationStore(tuple(observations))

    @property
    def store(self) -> ObservationStore:
        if self._active:
            raise RuntimeError("observations are available after leaving the context")
        if self._store is None:
            self._store = self._build_store()
        return self._store

    @property
    def observations(self) -> tuple[ObservedReference, ...]:
        return self.store.observations


def observe_runtime(
    program: ProgramIR,
    *,
    environment: str | None = None,
    observed_at: str | None = None,
) -> RuntimeObserver:
    return RuntimeObserver(
        program, environment=environment, observed_at=observed_at
    )
