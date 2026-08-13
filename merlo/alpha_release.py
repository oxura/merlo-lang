"""Fresh-evidence validator and atomic assembler for Merlo alpha.1.

This module is intentionally a consumer of evidence.  It never runs a compiler,
benchmark, sanitizer, test suite, or package builder, and it never invents a
report.  The controller supplies observed, content-addressed inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

RELEASE_VERSION = "0.1.0-alpha.1"
SCHEMA_VERSION = 1
ALPHA_RELEASE_SUPPORTED = "MERLO_ALPHA_RELEASE_SUPPORTED"
ALPHA_RELEASE_INCOMPLETE = "MERLO_ALPHA_RELEASE_INCOMPLETE"
ALPHA_RELEASE_SAFETY_DEFECT = "MERLO_ALPHA_RELEASE_SAFETY_DEFECT"
ALPHA_RELEASE_REPRODUCIBILITY_DEFECT = "MERLO_ALPHA_RELEASE_REPRODUCIBILITY_DEFECT"
ALLOWED_STATUSES = frozenset({
    ALPHA_RELEASE_SUPPORTED,
    ALPHA_RELEASE_INCOMPLETE,
    ALPHA_RELEASE_SAFETY_DEFECT,
    ALPHA_RELEASE_REPRODUCIBILITY_DEFECT,
})
REQUIRED_GATES = (
    "clean_demo", "examples", "full_tests", "corpus", "sanitizers",
    "simplicity", "performance", "lsp", "packaging", "reproducibility",
)


class ReleaseValidationError(ValueError):
    """Raised when supplied release inputs are malformed or stale."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: Any) -> str:
    return _digest_bytes(value if isinstance(value, bytes) else _canonical(value).encode("utf-8"))


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ReleaseValidationError(f"path escapes release root: {path}") from exc


@dataclass(frozen=True)
class EvidenceRecord:
    """One observed gate record and its content-addressed provenance."""

    id: str
    kind: str
    gate: str
    status: str
    executed: bool
    supported: bool
    payload: Mapping[str, Any] = field(default_factory=dict)
    raw_paths: tuple[Path | str, ...] = ()
    source_hashes: Mapping[str, str] = field(default_factory=dict)
    compiler_sha256: str | None = None
    lock_sha256: str | None = None
    raw_hashes: Mapping[str, str] = field(default_factory=dict)
    artifact_hashes: Mapping[str, str] = field(default_factory=dict)
    content_sha256: str | None = None
    def __post_init__(self) -> None:
        if self.content_sha256 is None:
            object.__setattr__(self, "content_sha256", self.digest())


    def unsigned_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "gate": self.gate,
            "status": self.status,
            "executed": self.executed,
            "supported": self.supported,
            "payload": dict(self.payload),
            "raw_hashes": dict(sorted(self.raw_hashes.items())),
            "source_hashes": dict(sorted(self.source_hashes.items())),
            "compiler_sha256": self.compiler_sha256,
            "lock_sha256": self.lock_sha256,
            "artifact_hashes": dict(sorted(self.artifact_hashes.items())),
        }

    def digest(self) -> str:
        return _digest(self.unsigned_payload())


@dataclass(frozen=True)
class GateInput:
    """A required gate linked to the evidence IDs that establish it."""

    name: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseInputs:
    """Controller-supplied source paths, gates, and fresh observed evidence."""

    root: Path
    gates: tuple[GateInput, ...]
    evidence: tuple[EvidenceRecord, ...]
    source_hashes: Mapping[str, str]
    compiler_path: Path
    compiler_sha256: str
    lockfile_path: Path
    lock_sha256: str
    docs: tuple[Path | str, ...]
    specs: tuple[Path | str, ...]
    stdlib: tuple[Path | str, ...]
    examples: tuple[Path | str, ...]
    binaries: tuple[Path | str, ...]
    claimed_status: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))
        object.__setattr__(self, "compiler_path", Path(self.compiler_path))
        object.__setattr__(self, "lockfile_path", Path(self.lockfile_path))
        object.__setattr__(self, "gates", tuple(self.gates))
        object.__setattr__(self, "evidence", tuple(self.evidence))


@dataclass(frozen=True)
class ValidationResult:
    status: str
    gates: Mapping[str, bool]
    failed_gates: tuple[str, ...]
    failed_evidence_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.status == ALPHA_RELEASE_SUPPORTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "release": RELEASE_VERSION,
            "status": self.status,
            "gates": dict(sorted(self.gates.items())),
            "failed_gates": list(self.failed_gates),
            "failed_evidence_ids": list(self.failed_evidence_ids),
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True)
class AssemblyResult:
    path: Path
    manifest: Mapping[str, Any]
    validation: ValidationResult


def _as_path(root: Path, value: Path | str) -> Path:
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ReleaseValidationError(f"path escapes release root: {value}") from exc
    return resolved

def _file_hash(path: Path) -> str:
    try:
        return _digest_bytes(path.read_bytes())
    except OSError as exc:
        raise ReleaseValidationError(f"cannot hash artifact {path}: {exc}") from exc


def _validate_paths(inputs: ReleaseInputs) -> None:
    root = inputs.root.resolve()
    if not inputs.source_hashes:
        raise ReleaseValidationError("source hashes are required")
    for relative, expected in inputs.source_hashes.items():
        path = _as_path(root, relative)
        if not path.is_file() or _file_hash(path) != expected:
            raise ReleaseValidationError(f"stale source hash: {relative}")
    compiler = _as_path(root, inputs.compiler_path)
    if not compiler.is_file() or _file_hash(compiler) != inputs.compiler_sha256:
        raise ReleaseValidationError("stale compiler hash")
    lockfile = _as_path(root, inputs.lockfile_path)
    if not lockfile.is_file() or _file_hash(lockfile) != inputs.lock_sha256:
        raise ReleaseValidationError("stale lock hash")
    for label, values in (("docs", inputs.docs), ("specs", inputs.specs), ("stdlib", inputs.stdlib), ("examples", inputs.examples), ("binaries", inputs.binaries)):
        if not values:
            raise ReleaseValidationError(f"missing required {label}")
        for value in values:
            path = _as_path(root, value)
            if not path.exists() or (label == "binaries" and not path.is_file()):
                raise ReleaseValidationError(f"missing {label} artifact: {value}")
    for value in inputs.binaries:
        if not os.access(_as_path(root, value), os.X_OK):
            raise ReleaseValidationError(f"sample binary is not executable: {value}")


def _validate_evidence(inputs: ReleaseInputs) -> tuple[dict[str, EvidenceRecord], set[str]]:
    root = inputs.root.resolve()
    records: dict[str, EvidenceRecord] = {}
    safety_failures: set[str] = set()
    for record in inputs.evidence:
        if not record.id or record.id in records:
            raise ReleaseValidationError(f"duplicate evidence ID: {record.id}")
        if record.gate not in REQUIRED_GATES:
            raise ReleaseValidationError(f"unsupported evidence gate: {record.gate}")
        if record.status not in {"PASSED", "FAILED"}:
            raise ReleaseValidationError(f"evidence is not an observed pass/fail: {record.id}")
        if record.executed is not True or record.supported is not True:
            raise ReleaseValidationError(f"evidence was not executed on a supported toolchain: {record.id}")
        if record.digest() != record.content_sha256:
            raise ReleaseValidationError(f"evidence content hash mismatch: {record.id}")
        if dict(record.source_hashes) != dict(inputs.source_hashes):
            raise ReleaseValidationError(f"stale evidence source hash: {record.id}")
        if record.compiler_sha256 != inputs.compiler_sha256:
            raise ReleaseValidationError(f"stale evidence compiler hash: {record.id}")
        if record.lock_sha256 != inputs.lock_sha256:
            raise ReleaseValidationError(f"stale evidence lock hash: {record.id}")
        payload_status = record.payload.get("status")
        if payload_status is not None and payload_status != record.status:
            raise ReleaseValidationError(f"forged evidence status: {record.id}")
        payload_passed = record.payload.get("passed")
        if payload_passed is not None and payload_passed is not (record.status == "PASSED"):
            raise ReleaseValidationError(f"forged evidence pass decision: {record.id}")
        payload_executed = record.payload.get("executed")
        if payload_executed is not None and payload_executed is not record.executed:
            raise ReleaseValidationError(f"forged evidence execution decision: {record.id}")
        if record.gate == "reproducibility" and not record.artifact_hashes:
            raise ReleaseValidationError(f"reproducibility evidence has no artifact hashes: {record.id}")
        for raw in record.raw_paths:
            path = _as_path(root, raw)
            if not path.is_file():
                raise ReleaseValidationError(f"missing raw evidence: {raw}")
            expected = record.raw_hashes.get(str(raw), record.raw_hashes.get(_relative(path, root)))
            if expected is None or _file_hash(path) != expected:
                raise ReleaseValidationError(f"tampered raw evidence: {raw}")
        for raw, expected in record.artifact_hashes.items():
            path = _as_path(root, raw)
            if not path.is_file() or _file_hash(path) != expected:
                raise ReleaseValidationError(f"tampered evidence artifact: {raw}")
        records[record.id] = record
        if record.gate == "sanitizers" and record.status == "FAILED":
            safety_failures.add(record.id)
    return records, safety_failures


def validate_release(inputs: ReleaseInputs) -> ValidationResult:
    """Validate fresh inputs and derive one status; emit no files."""
    if not isinstance(inputs, ReleaseInputs):
        raise ReleaseValidationError("release inputs must be typed ReleaseInputs")
    _validate_paths(inputs)
    records, safety_failures = _validate_evidence(inputs)
    gates_by_name: dict[str, GateInput] = {}
    for gate in inputs.gates:
        if gate.name in gates_by_name:
            raise ReleaseValidationError(f"duplicate gate: {gate.name}")
        if gate.name not in REQUIRED_GATES:
            raise ReleaseValidationError(f"unsupported gate: {gate.name}")
        gates_by_name[gate.name] = gate
    missing = [name for name in REQUIRED_GATES if name not in gates_by_name]
    if missing:
        raise ReleaseValidationError(f"missing required gates: {', '.join(missing)}")
    gate_values: dict[str, bool] = {}
    failed_ids: set[str] = set(safety_failures)
    for name in REQUIRED_GATES:
        gate = gates_by_name[name]
        if not gate.evidence_ids or len(set(gate.evidence_ids)) != len(gate.evidence_ids):
            raise ReleaseValidationError(f"duplicate or missing evidence IDs for gate: {name}")
        selected: list[EvidenceRecord] = []
        for evidence_id in gate.evidence_ids:
            if evidence_id not in records:
                raise ReleaseValidationError(f"missing evidence ID: {evidence_id}")
            record = records[evidence_id]
            if record.gate != name:
                raise ReleaseValidationError(f"evidence linked to wrong gate: {evidence_id}")
            selected.append(record)
        gate_values[name] = all(item.status == "PASSED" for item in selected)
        failed_ids.update(item.id for item in selected if item.status != "PASSED")
    linked = {item for gate in gates_by_name.values() for item in gate.evidence_ids}
    if linked != set(records):
        raise ReleaseValidationError("evidence is omitted from or duplicated across gate declarations")
    reproducibility_failed = not gate_values["reproducibility"]
    if safety_failures:
        status = ALPHA_RELEASE_SAFETY_DEFECT
    elif reproducibility_failed:
        status = ALPHA_RELEASE_REPRODUCIBILITY_DEFECT
    elif all(gate_values.values()):
        status = ALPHA_RELEASE_SUPPORTED
    else:
        status = ALPHA_RELEASE_INCOMPLETE
    if inputs.claimed_status is not None and inputs.claimed_status != status:
        raise ReleaseValidationError("forged release status")
    return ValidationResult(status, gate_values, tuple(name for name in REQUIRED_GATES if not gate_values[name]), tuple(sorted(failed_ids)), tuple(sorted(records)))


def _copy_tree(root: Path, source: Path | str, target: Path, label: str) -> list[str]:
    path = _as_path(root, source)
    if path.is_file():
        destination = target / label / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        return [destination.relative_to(target).as_posix()]
    if path.is_dir():
        destination = target / label / path.name
        shutil.copytree(path, destination)
        return [item.relative_to(target).as_posix() for item in sorted(destination.rglob("*")) if item.is_file()]
    raise ReleaseValidationError(f"missing assembly source: {source}")


def _manifest_payload(inputs: ReleaseInputs, validation: ValidationResult, files: Mapping[str, str], evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "release": RELEASE_VERSION,
        "status": validation.status,
        "gates": dict(sorted(validation.gates.items())),
        "failed_gates": list(validation.failed_gates),
        "failed_evidence_ids": list(validation.failed_evidence_ids),
        "evidence": list(evidence),
        "files": dict(sorted(files.items())),
        "required": {
            "docs": len(inputs.docs),
            "specs": len(inputs.specs),
            "stdlib": len(inputs.stdlib),
            "examples": len(inputs.examples),
            "binaries": len(inputs.binaries),
        },
    }
def manifest_payload_sha256(manifest: Mapping[str, Any]) -> str:
    payload = dict(manifest)
    payload.pop("payload_sha256", None)
    payload.pop("manifest_sha256", None)
    return _digest(payload)
def manifest_sha256(manifest: Mapping[str, Any]) -> str:
    payload = dict(manifest)
    payload.pop("manifest_sha256", None)
    return _digest(payload)


def verify_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("payload_sha256") != manifest_payload_sha256(manifest):
        raise ReleaseValidationError("manifest payload self-hash mismatch")
    if manifest.get("manifest_sha256") != manifest_sha256(manifest):
        raise ReleaseValidationError("manifest self-hash mismatch")



def _write_manifest(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    payload_hash = _digest(payload)
    unsigned = {**payload, "payload_sha256": payload_hash}
    manifest_hash = _digest(unsigned)
    manifest = {**unsigned, "manifest_sha256": manifest_hash}
    path.write_text(_canonical(manifest) + "\n", encoding="utf-8")
    return manifest


def _verify_manifest(manifest: Mapping[str, Any]) -> None:
    payload = dict(manifest)
    manifest_hash = payload.pop("manifest_sha256", None)
    payload_hash = payload.pop("payload_sha256", None)
    if payload_hash != _digest(payload):
        raise ReleaseValidationError("manifest payload self-hash mismatch")
    if manifest_hash != _digest({**payload, "payload_sha256": payload_hash}):
        raise ReleaseValidationError("manifest self-hash mismatch")


def assemble_release(inputs: ReleaseInputs, destination: Path | str) -> AssemblyResult:
    """Validate then atomically assemble a deterministic release directory."""
    validation = validate_release(inputs)
    if validation.status != ALPHA_RELEASE_SUPPORTED:
        raise ReleaseValidationError(f"release cannot be assembled with status {validation.status}")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{destination.name}-", dir=str(destination.parent)) as temporary:
        work = Path(temporary)
        copied: list[str] = []
        for value in inputs.docs:
            copied.extend(_copy_tree(inputs.root, value, work, "docs"))
        for value in inputs.specs:
            copied.extend(_copy_tree(inputs.root, value, work, "spec"))
        for value in inputs.stdlib:
            copied.extend(_copy_tree(inputs.root, value, work, "stdlib"))
        for value in inputs.examples:
            copied.extend(_copy_tree(inputs.root, value, work, "examples"))
        for value in inputs.binaries:
            copied.extend(_copy_tree(inputs.root, value, work, "bin"))
        evidence_entries: list[dict[str, Any]] = []
        for record in sorted(inputs.evidence, key=lambda item: item.id):
            raw_refs: list[str] = []
            for raw in record.raw_paths:
                source = _as_path(inputs.root, raw)
                target = work / "evidence" / "raw" / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and target.read_bytes() != source.read_bytes():
                    raise ReleaseValidationError(f"raw evidence name collision: {source.name}")
                shutil.copyfile(source, target)
                ref = target.relative_to(work).as_posix()
                copied.append(ref)
                raw_refs.append(ref)
            evidence_entries.append({"id": record.id, "kind": record.kind, "gate": record.gate, "content_sha256": record.content_sha256, "raw_paths": raw_refs})
        files = {relative: _file_hash(work / relative) for relative in sorted(set(copied))}
        payload = _manifest_payload(inputs, validation, files, evidence_entries)
        manifest = _write_manifest(work / "manifest.json", payload)
        checksums = "".join(f"{digest}  {relative}\n" for relative, digest in sorted(files.items()))
        (work / "checksums.sha256").write_text(checksums, encoding="utf-8")
        _verify_manifest(manifest)
        if destination.exists():
            old = destination / "manifest.json"
            if not old.is_file() or old.read_bytes() != (work / "manifest.json").read_bytes():
                raise ReleaseValidationError("refusing second release emission with different content")
            return AssemblyResult(destination, manifest, validation)
        os.replace(work, destination)
    return AssemblyResult(destination, manifest, validation)


def write_validation_report_once(path: Path | str, validation: ValidationResult) -> Path:
    """Write the immutable machine report, refusing different content."""
    destination = Path(path)
    unsigned = validation.to_dict()
    payload_hash = _digest(unsigned)
    report_value = {**unsigned, "payload_sha256": payload_hash}
    report = {**report_value, "report_sha256": _digest(report_value)}
    payload = _canonical(report) + "\n"
    if destination.exists():
        if destination.read_text(encoding="utf-8") != payload:
            raise ReleaseValidationError("refusing to overwrite validation report")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, destination)
    return destination


__all__ = [
    "ALLOWED_STATUSES", "ALPHA_RELEASE_INCOMPLETE", "ALPHA_RELEASE_REPRODUCIBILITY_DEFECT",
    "ALPHA_RELEASE_SAFETY_DEFECT", "ALPHA_RELEASE_SUPPORTED", "AssemblyResult", "EvidenceRecord",
    "GateInput", "RELEASE_VERSION", "REQUIRED_GATES", "ReleaseInputs", "ReleaseValidationError",
    "ValidationResult", "assemble_release", "manifest_payload_sha256", "manifest_sha256",
    "validate_release", "verify_manifest", "write_validation_report_once",
]
