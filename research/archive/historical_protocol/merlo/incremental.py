from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from research.archive.historical_protocol.merlo.evidence import invalidated_evidence_ids
from research.archive.historical_protocol.merlo.model import Evidence, ProgramIR, Resolution


@dataclass(frozen=True)
class AffectedRatio:
    """An affected count with its raw denominator.

    The ratio is descriptive only: it is not a measured speedup.
    """

    affected: int
    total: int

    @property
    def ratio(self) -> float:
        return self.affected / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, int | float]:
        return {
            "affected": self.affected,
            "total": self.total,
            "ratio": self.ratio,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AffectedRatio":
        return cls(affected=int(value["affected"]), total=int(value["total"]))


@dataclass(frozen=True)
class ScanTiming:
    """Timing supplied by the caller for a real full scan, if one was measured."""

    full_scan_seconds: float | None
    comparison_seconds: float
    full_scan_measured: bool
    speedup_claimed: bool = False

    def to_dict(self) -> dict[str, float | bool | None]:
        return {
            "full_scan_seconds": self.full_scan_seconds,
            "comparison_seconds": self.comparison_seconds,
            "full_scan_measured": self.full_scan_measured,
            "speedup_claimed": False,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ScanTiming":
        return cls(
            full_scan_seconds=(
                float(value["full_scan_seconds"])
                if value.get("full_scan_seconds") is not None
                else None
            ),
            comparison_seconds=float(value["comparison_seconds"]),
            full_scan_measured=bool(value["full_scan_measured"]),
            speedup_claimed=False,
        )


@dataclass(frozen=True)
class IncrementalProfile:
    old_world_revision: str
    new_world_revision: str
    changed_files: tuple[str, ...]
    changed_entity_ids: tuple[str, ...]
    binding_closure_entity_ids: tuple[str, ...]
    call_closure_entity_ids: tuple[str, ...]
    reference_closure_entity_ids: tuple[str, ...]
    affected_file_paths: tuple[str, ...]
    affected_entity_ids: tuple[str, ...]
    affected_reference_ids: tuple[str, ...]
    affected_call_ids: tuple[str, ...]
    uncertain_reference_ids: tuple[str, ...]
    uncertain_call_ids: tuple[str, ...]
    invalidated_evidence_ids: tuple[str, ...]
    file_ratio: AffectedRatio
    entity_ratio: AffectedRatio
    reference_ratio: AffectedRatio
    call_ratio: AffectedRatio
    theoretical_work_set: tuple[str, ...]
    timing: ScanTiming

    def to_dict(self) -> dict[str, Any]:
        return {
            "old_world_revision": self.old_world_revision,
            "new_world_revision": self.new_world_revision,
            "changed_files": list(self.changed_files),
            "changed_entity_ids": list(self.changed_entity_ids),
            "binding_closure_entity_ids": list(self.binding_closure_entity_ids),
            "call_closure_entity_ids": list(self.call_closure_entity_ids),
            "reference_closure_entity_ids": list(self.reference_closure_entity_ids),
            "affected_file_paths": list(self.affected_file_paths),
            "affected_entity_ids": list(self.affected_entity_ids),
            "affected_reference_ids": list(self.affected_reference_ids),
            "affected_call_ids": list(self.affected_call_ids),
            "uncertain_reference_ids": list(self.uncertain_reference_ids),
            "uncertain_call_ids": list(self.uncertain_call_ids),
            "invalidated_evidence_ids": list(self.invalidated_evidence_ids),
            "file_ratio": self.file_ratio.to_dict(),
            "entity_ratio": self.entity_ratio.to_dict(),
            "reference_ratio": self.reference_ratio.to_dict(),
            "call_ratio": self.call_ratio.to_dict(),
            "theoretical_work_set": list(self.theoretical_work_set),
            "timing": self.timing.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IncrementalProfile":
        tuple_fields = (
            "changed_files",
            "changed_entity_ids",
            "binding_closure_entity_ids",
            "call_closure_entity_ids",
            "reference_closure_entity_ids",
            "affected_file_paths",
            "affected_entity_ids",
            "affected_reference_ids",
            "affected_call_ids",
            "uncertain_reference_ids",
            "uncertain_call_ids",
            "invalidated_evidence_ids",
            "theoretical_work_set",
        )
        values = {name: tuple(value.get(name, ())) for name in tuple_fields}
        return cls(
            old_world_revision=str(value["old_world_revision"]),
            new_world_revision=str(value["new_world_revision"]),
            **values,
            file_ratio=AffectedRatio.from_dict(value["file_ratio"]),
            entity_ratio=AffectedRatio.from_dict(value["entity_ratio"]),
            reference_ratio=AffectedRatio.from_dict(value["reference_ratio"]),
            call_ratio=AffectedRatio.from_dict(value["call_ratio"]),
            timing=ScanTiming.from_dict(value["timing"]),
        )


class IncrementalProfiler:
    """Compute a conservative semantic invalidation closure between two IRs."""

    def compare(
        self,
        old: ProgramIR,
        new: ProgramIR,
        *,
        evidence: Iterable[Evidence] = (),
        full_scan_seconds: float | None = None,
    ) -> IncrementalProfile:
        return profile_incremental(
            old,
            new,
            evidence=evidence,
            full_scan_seconds=full_scan_seconds,
        )


def profile_incremental(
    old: ProgramIR,
    new: ProgramIR,
    *,
    evidence: Iterable[Evidence] = (),
    full_scan_seconds: float | None = None,
) -> IncrementalProfile:
    """Compare complete scans and describe theoretical incremental work.

    ``full_scan_seconds`` must be a caller measurement. The function deliberately
    does not derive or report a speedup from the theoretical work set.
    """

    started = time.perf_counter()
    if full_scan_seconds is not None and full_scan_seconds < 0:
        raise ValueError("full_scan_seconds must be non-negative")

    old_files = {item.path: item for item in old.files}
    new_files = {item.path: item for item in new.files}
    all_files = set(old_files) | set(new_files)
    changed_files = {
        path
        for path in all_files
        if path not in old_files
        or path not in new_files
        or old_files[path].digest != new_files[path].digest
    }

    old_entities = {item.id: item for item in old.entities}
    new_entities = {item.id: item for item in new.entities}
    all_entities = set(old_entities) | set(new_entities)
    changed_entities = {
        entity_id
        for entity_id in all_entities
        if entity_id not in old_entities
        or entity_id not in new_entities
        or old_entities[entity_id].revision_hash
        != new_entities[entity_id].revision_hash
        or old_entities[entity_id].source_hash != new_entities[entity_id].source_hash
        or old_entities[entity_id].file != new_entities[entity_id].file
        or old_entities[entity_id].module != new_entities[entity_id].module
        or old_entities[entity_id].qualname != new_entities[entity_id].qualname
        or old_entities[entity_id].public != new_entities[entity_id].public
    }
    # Parsing is the indivisible unit of the current analyzer, so all entities in
    # a changed file belong to the theoretical work set even if their revisions
    # remained stable.
    file_work_entities = {
        entity.id
        for entity in (*old.entities, *new.entities)
        if entity.file in changed_files
    }

    affected_entities = set(changed_entities) | file_work_entities
    affected_files = set(changed_files)
    reference_entities: set[str] = set()
    call_entities: set[str] = set()
    binding_entities: set[str] = set()
    affected_references: set[str] = set()
    affected_calls: set[str] = set()
    uncertain_references: set[str] = set()
    uncertain_calls: set[str] = set()

    old_reference_map = {_reference_key(item): item for item in old.references}
    new_reference_map = {_reference_key(item): item for item in new.references}
    changed_reference_keys = _changed_record_keys(
        old_reference_map, new_reference_map
    )
    for key in changed_reference_keys:
        for reference in (
            old_reference_map.get(key),
            new_reference_map.get(key),
        ):
            if reference is None:
                continue
            affected_files.add(reference.file)
            if (
                reference.owner_id is not None
                and reference.owner_id in all_entities
            ):
                affected_entities.add(reference.owner_id)
            if (
                reference.target_id is not None
                and reference.target_id in all_entities
            ):
                affected_entities.add(reference.target_id)
            affected_entities.update(
                set(reference.possible_target_ids) & all_entities
            )
            affected_references.add(key)

    old_call_map = {_call_key(item): item for item in old.calls}
    new_call_map = {_call_key(item): item for item in new.calls}
    changed_call_keys = _changed_record_keys(old_call_map, new_call_map)
    for key in changed_call_keys:
        for call in (old_call_map.get(key), new_call_map.get(key)):
            if call is None:
                continue
            affected_files.add(call.file)
            if call.source_id is not None and call.source_id in all_entities:
                affected_entities.add(call.source_id)
            if call.target_id is not None and call.target_id in all_entities:
                affected_entities.add(call.target_id)
            affected_entities.update(set(call.possible_target_ids) & all_entities)
            affected_calls.add(key)

    changed_public = {
        entity_id
        for entity_id in changed_entities
        if (new_entities.get(entity_id) or old_entities.get(entity_id)).public
    }

    references = _union_records(old.references, new.references, _reference_key)
    calls = _union_records(old.calls, new.calls, _call_key)

    # A public surface can be reached by unresolved dynamic binding. Treat such
    # owners as affected rather than pretending the binding is absent.
    public_uncertainty = bool(changed_public)
    changed = True
    while changed:
        before = (len(affected_entities), len(affected_files))
        for entity in (*old.entities, *new.entities):
            if entity.id in affected_entities:
                affected_files.add(entity.file)

        for reference in references:
            key = _reference_key(reference)
            targets = set(reference.possible_target_ids)
            if reference.target_id is not None:
                targets.add(reference.target_id)
            uncertain = reference.resolution in {
                Resolution.CONDITIONAL,
                Resolution.DYNAMIC,
                Resolution.UNKNOWN,
            }
            touches = bool(targets & affected_entities)
            owner_touches = reference.owner_id in affected_entities
            file_touches = reference.file in affected_files
            unknown_public_binding = uncertain and not targets and public_uncertainty
            if not (touches or owner_touches or file_touches or unknown_public_binding):
                continue
            affected_references.add(key)
            if uncertain:
                uncertain_references.add(key)
            if (
                reference.owner_id is not None
                and reference.owner_id in all_entities
            ):
                affected_entities.add(reference.owner_id)
                reference_entities.add(reference.owner_id)
                binding_entities.add(reference.owner_id)
            if owner_touches or file_touches:
                internal_targets = targets & all_entities
                affected_entities.update(internal_targets)
                reference_entities.update(internal_targets)
                binding_entities.update(internal_targets)
            affected_files.add(reference.file)

        for call in calls:
            key = _call_key(call)
            targets = set(call.possible_target_ids)
            if call.target_id is not None:
                targets.add(call.target_id)
            uncertain = call.resolution in {
                Resolution.CONDITIONAL,
                Resolution.DYNAMIC,
                Resolution.UNKNOWN,
            }
            touches = bool(targets & affected_entities)
            source_touches = call.source_id in affected_entities
            file_touches = call.file in affected_files
            unknown_public_call = uncertain and not targets and public_uncertainty
            if not (touches or source_touches or file_touches or unknown_public_call):
                continue
            affected_calls.add(key)
            if uncertain:
                uncertain_calls.add(key)
            if call.source_id is not None and call.source_id in all_entities:
                affected_entities.add(call.source_id)
                call_entities.add(call.source_id)
            if source_touches or file_touches:
                internal_targets = targets & all_entities
                affected_entities.update(internal_targets)
                call_entities.update(internal_targets)
            affected_files.add(call.file)

        # A changed public definition invalidates the module binding surface,
        # including import-star and reflective users the analyzer cannot resolve.
        public_modules = {
            (new_entities.get(item) or old_entities.get(item)).module
            for item in changed_public
        }
        if public_modules:
            for entity in (*old.entities, *new.entities):
                if entity.module in public_modules:
                    affected_entities.add(entity.id)
                    binding_entities.add(entity.id)

        changed = before != (len(affected_entities), len(affected_files))

    elapsed = time.perf_counter() - started
    entity_total = len(all_entities)
    reference_total = len(set(old_reference_map) | set(new_reference_map))
    call_total = len(set(old_call_map) | set(new_call_map))
    theoretical_work = tuple(
        sorted(
            [f"file:{path}" for path in affected_files]
            + [f"entity:{item}" for item in affected_entities]
            + [f"reference:{item}" for item in affected_references]
            + [f"call:{item}" for item in affected_calls]
        )
    )
    return IncrementalProfile(
        old_world_revision=old.world_revision,
        new_world_revision=new.world_revision,
        changed_files=tuple(sorted(changed_files)),
        changed_entity_ids=tuple(sorted(changed_entities)),
        binding_closure_entity_ids=tuple(sorted(binding_entities)),
        call_closure_entity_ids=tuple(sorted(call_entities)),
        reference_closure_entity_ids=tuple(sorted(reference_entities)),
        affected_file_paths=tuple(sorted(affected_files)),
        affected_entity_ids=tuple(sorted(affected_entities)),
        affected_reference_ids=tuple(sorted(affected_references)),
        affected_call_ids=tuple(sorted(affected_calls)),
        uncertain_reference_ids=tuple(sorted(uncertain_references)),
        uncertain_call_ids=tuple(sorted(uncertain_calls)),
        invalidated_evidence_ids=invalidated_evidence_ids(evidence, new),
        file_ratio=AffectedRatio(len(affected_files), len(all_files)),
        entity_ratio=AffectedRatio(len(affected_entities), entity_total),
        reference_ratio=AffectedRatio(len(affected_references), reference_total),
        call_ratio=AffectedRatio(len(affected_calls), call_total),
        theoretical_work_set=theoretical_work,
        timing=ScanTiming(full_scan_seconds, elapsed, full_scan_seconds is not None),
    )


def compare_programs(
    old: ProgramIR,
    new: ProgramIR,
    *,
    evidence: Iterable[Evidence] = (),
    full_scan_seconds: float | None = None,
) -> IncrementalProfile:
    """Descriptive alias for callers that do not need a profiler instance."""

    return profile_incremental(
        old,
        new,
        evidence=evidence,
        full_scan_seconds=full_scan_seconds,
    )


def _union_records(old: Iterable[Any], new: Iterable[Any], key: Any) -> tuple[Any, ...]:
    old_records = {key(item): item for item in old}
    new_records = {key(item): item for item in new}
    records: list[Any] = []
    for identifier in sorted(set(old_records) | set(new_records)):
        old_item = old_records.get(identifier)
        new_item = new_records.get(identifier)
        if old_item is not None and new_item is not None:
            if old_item.to_dict() != new_item.to_dict():
                records.append(old_item)
            records.append(new_item)
        else:
            records.append(old_item if old_item is not None else new_item)
    return tuple(records)


def _changed_record_keys(
    old: Mapping[str, Any], new: Mapping[str, Any]
) -> set[str]:
    return {
        key
        for key in set(old) | set(new)
        if key not in old
        or key not in new
        or old[key].to_dict() != new[key].to_dict()
    }


def _reference_key(reference: Any) -> str:
    if reference.id:
        return reference.id
    return (
        f"{reference.file}:{reference.span.start.line}:"
        f"{reference.span.start.column}:{reference.kind}"
    )


def _call_key(call: Any) -> str:
    if call.id:
        return call.id
    return f"{call.file}:{call.line}:{call.column}:{call.source_id}:{call.target_id}"
