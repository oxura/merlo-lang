"""Deterministic Merlo release validation and assembly."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import io
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

RELEASE_VERSION = "0.1.0-alpha.2"
SCHEMA_VERSION = 2
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
REQUIRED_LICENSES = ("LICENSE-APACHE", "LICENSE-MIT")
REQUIRED_METADATA = ("CHANGELOG.md", "README.md", "pyproject.toml")
REQUIRED_DOCS = (
    "docs/README.md", "docs/architecture.md", "docs/project-history.md",
    "docs/installation.md", "docs/tour.md", "docs/types.md", "docs/ownership.md",
    "docs/errors.md", "docs/effects.md", "docs/capabilities.md", "docs/resources.md",
    "docs/modules.md", "docs/projects.md", "docs/packages.md", "docs/ffi.md",
    "docs/semantic-world.md", "docs/alpha-protocol.md", "docs/ai-protocol.md",
    "docs/tooling.md", "docs/lsp.md", "docs/examples.md", "docs/limitations.md",
)
REQUIRED_SPECS = (
    "spec/README.md", "spec/language.md", "spec/ownership.md", "spec/effects.md",
    "spec/packages.md", "spec/ffi.md", "spec/semantic-world.md", "spec/alpha-protocol.md",
)
REQUIRED_STDLIB = (
    "stdlib/README.md", "stdlib/std/core.mlo", "stdlib/std/option.mlo",
    "stdlib/std/result.mlo", "stdlib/std/text.mlo", "stdlib/std/bytes.mlo",
    "stdlib/std/collections.mlo", "stdlib/std/io.mlo", "stdlib/std/fs.mlo",
    "stdlib/std/cli.mlo", "stdlib/std/time.mlo", "stdlib/std/random.mlo",
    "stdlib/std/json.mlo", "stdlib/std/net.mlo", "stdlib/std/http.mlo",
    "src/merlo/stdlib/json.mlo", "src/merlo/stdlib/csv.mlo",
)
REQUIRED_EXAMPLES = (
    "examples/README.md", "examples/automation/merlo.toml", "examples/automation/merlo.lock",
    "examples/automation/input.txt", "examples/automation/expected.txt",
    "examples/automation/src/main.mlo", "examples/automation/src/report.mlo",
    "examples/automation/tests/automation.mlo", "examples/packages/merlo.toml",
    "examples/packages/merlo.lock", "examples/packages/input.txt", "examples/packages/expected.txt",
    "examples/packages/src/main.mlo", "examples/packages/src/greeting.mlo",
    "examples/packages/tests/packages.mlo", "examples/packages/vendor/greeting/merlo.toml",
    "examples/packages/vendor/greeting/merlo.lock", "examples/packages/vendor/greeting/src/main.mlo",
    "examples/network/merlo.toml", "examples/network/merlo.lock", "examples/network/input.txt",
    "examples/network/expected.txt", "examples/network/src/main.mlo", "examples/network/tests/network.mlo",
    "examples/ndjson/merlo.toml", "examples/ndjson/merlo.lock", "examples/ndjson/expected.txt",
    "examples/ndjson/input.ndjson", "examples/ndjson/src/main.mlo", "examples/ndjson/src/report.mlo",
    "examples/ndjson/tests/ndjson.mlo", "examples/json-cli/merlo.toml", "examples/json-cli/merlo.lock",
    "examples/json-cli/input.json", "examples/json-cli/expected.txt", "examples/json-cli/src/main.mlo",
    "examples/json-cli/tests/json_cli.mlo", "examples/grep/merlo.toml", "examples/grep/merlo.lock",
    "examples/grep/input.txt", "examples/grep/expected.txt", "examples/grep/src/main.mlo",
    "examples/grep/src/search.mlo", "examples/grep/tests/grep.mlo", "examples/csv/merlo.toml",
    "examples/csv/merlo.lock", "examples/csv/input.csv", "examples/csv/expected.txt",
    "examples/csv/src/main.mlo", "examples/csv/src/sales.mlo", "examples/csv/tests/csv.mlo",
    "examples/ffi/merlo.toml", "examples/ffi/merlo.lock", "examples/ffi/input.txt",
    "examples/ffi/expected.txt", "examples/ffi/src/main.mlo", "examples/ffi/tests/ffi.mlo",
)
_HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


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
            "id": self.id, "kind": self.kind, "gate": self.gate, "status": self.status,
            "executed": self.executed, "supported": self.supported, "payload": dict(self.payload),
            "raw_hashes": dict(sorted(self.raw_hashes.items())),
            "source_hashes": dict(sorted(self.source_hashes.items())),
            "compiler_sha256": self.compiler_sha256, "lock_sha256": self.lock_sha256,
            "artifact_hashes": dict(sorted(self.artifact_hashes.items())),
        }

    def digest(self) -> str:
        return _digest(self.unsigned_payload())


@dataclass(frozen=True)
class GateInput:
    name: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseProvenance:
    """Controller-provided immutable release identity and external asset digests."""

    source_commit: str
    tag: str
    versions: Mapping[str, str]
    platform: str
    architecture: str
    python: Mapping[str, str]
    c_compiler: Mapping[str, str]
    build_frontend: Mapping[str, str]
    source_date_epoch: int
    assets: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "versions", dict(self.versions))
        object.__setattr__(self, "python", dict(self.python))
        object.__setattr__(self, "c_compiler", dict(self.c_compiler))
        object.__setattr__(self, "build_frontend", dict(self.build_frontend))
        object.__setattr__(self, "assets", dict(self.assets))


@dataclass(frozen=True)
class ReleaseInputs:
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
    provenance: ReleaseProvenance
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
    release: str = RELEASE_VERSION

    @property
    def passed(self) -> bool:
        return self.status == ALPHA_RELEASE_SUPPORTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION, "release": self.release, "status": self.status,
            "gates": dict(sorted(self.gates.items())), "failed_gates": list(self.failed_gates),
            "failed_evidence_ids": list(self.failed_evidence_ids), "evidence_ids": list(self.evidence_ids),
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

_ALPHA_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-alpha\.([1-9][0-9]*)$")


def _distribution_filenames(package_version: str) -> tuple[str, str, str]:
    match = _ALPHA_VERSION.fullmatch(package_version)
    if match is None:
        raise ReleaseValidationError("invalid provenance package version")
    major, minor, patch, alpha = match.groups()
    normalized = f"{major}.{minor}.{patch}a{alpha}"
    return (
        f"merlo-{normalized}.tar.gz",
        f"merlo-{normalized}-py3-none-any.whl",
        f"merlo-{package_version}-evidence.zip",
    )


def _asset_roles(assets: Mapping[str, str]) -> dict[str, str]:
    roles: dict[str, str] = {}
    basenames: set[str] = set()
    for filename in assets:
        if not isinstance(filename, str) or not filename or "\\" in filename:
            raise ReleaseValidationError("invalid provenance asset filename")
        path = Path(filename)
        if path.is_absolute() or path.as_posix() != filename or any(part in {".", ".."} for part in path.parts):
            raise ReleaseValidationError(f"noncanonical provenance asset filename: {filename}")
        basename = path.name
        if basename in basenames:
            raise ReleaseValidationError("provenance asset basenames must be unique")
        basenames.add(basename)
        matches = []
        if basename.endswith((".tar.gz", ".tar")):
            matches.append("sdist")
        if basename.endswith(".whl"):
            matches.append("wheel")
        if "evidence" in basename.lower():
            matches.append("evidence")
        if len(matches) != 1 or matches[0] in roles:
            raise ReleaseValidationError("provenance assets must have exactly one distinct role")
        roles[matches[0]] = basename
    if set(roles) != {"sdist", "wheel", "evidence"}:
        raise ReleaseValidationError("provenance assets must contain sdist, wheel, and evidence")
    return roles


def _validate_provenance(provenance: ReleaseProvenance, root: Path) -> None:
    if not isinstance(provenance, ReleaseProvenance):
        raise ReleaseValidationError("provenance must be a ReleaseProvenance")
    if not isinstance(provenance.source_commit, str) or not _HEX40.fullmatch(provenance.source_commit):
        raise ReleaseValidationError("invalid provenance source_commit")
    if not isinstance(provenance.tag, str) or not provenance.tag:
        raise ReleaseValidationError("invalid provenance tag")
    if set(provenance.versions) != {"package", "compiler", "displayed"} or not all(
        isinstance(value, str) and value for value in provenance.versions.values()
    ):
        raise ReleaseValidationError("invalid provenance versions")
    package_version = provenance.versions["package"]
    compiler_version = provenance.versions["compiler"]
    displayed_version = provenance.versions["displayed"]
    alpha_match = _ALPHA_VERSION.fullmatch(package_version)
    if compiler_version != package_version:
        raise ReleaseValidationError("compiler version disagrees with package version")
    if alpha_match is None:
        raise ReleaseValidationError("invalid provenance package version")
    expected_displayed = f"Merlo alpha.{alpha_match.group(4)}"
    if displayed_version != expected_displayed:
        raise ReleaseValidationError("displayed version disagrees with package version")
    if provenance.tag != f"v{package_version}":
        raise ReleaseValidationError("provenance tag disagrees with package version")
    sdist_name, wheel_name, evidence_name = _distribution_filenames(package_version)
    if not isinstance(provenance.platform, str) or not provenance.platform:
        raise ReleaseValidationError("invalid provenance platform or architecture")
    if not isinstance(provenance.architecture, str) or not provenance.architecture:
        raise ReleaseValidationError("invalid provenance platform or architecture")
    if set(provenance.python) != {"implementation", "version"} or not all(
        isinstance(value, str) and value for value in provenance.python.values()
    ):
        raise ReleaseValidationError("invalid provenance python")
    if set(provenance.c_compiler) != {"name", "version", "target"} or not all(
        isinstance(value, str) and value for value in provenance.c_compiler.values()
    ):
        raise ReleaseValidationError("invalid provenance c_compiler")
    if set(provenance.build_frontend) != {"name", "version"} or not all(
        isinstance(value, str) and value for value in provenance.build_frontend.values()
    ):
        raise ReleaseValidationError("invalid provenance build_frontend")
    if isinstance(provenance.source_date_epoch, bool) or not isinstance(provenance.source_date_epoch, int) or provenance.source_date_epoch < 0:
        raise ReleaseValidationError("invalid provenance source_date_epoch")
    if len(provenance.assets) != 3 or not all(
        isinstance(value, str) and _HEX64.fullmatch(value) for value in provenance.assets.values()
    ):
        raise ReleaseValidationError("invalid provenance assets")
    roles = _asset_roles(provenance.assets)
    if {Path(name).name for name in provenance.assets} != {sdist_name, wheel_name, evidence_name}:
        raise ReleaseValidationError("provenance asset filenames disagree with package version")
    if roles != {"sdist": sdist_name, "wheel": wheel_name, "evidence": evidence_name}:
        raise ReleaseValidationError("provenance asset roles disagree with package version")
    for filename, expected in sorted(provenance.assets.items()):
        path = _as_path(root, filename)
        if not path.is_file():
            raise ReleaseValidationError(f"missing release asset: {filename}")
        if _file_hash(path) != expected:
            raise ReleaseValidationError(f"asset digest mismatch: {filename}")


def _inventory_paths(root: Path, values: Sequence[Path | str], label: str, expected: Sequence[str]) -> None:
    actual: set[str] = set()
    for value in values:
        path = _as_path(root, value)
        relative = _relative(path, root)
        if relative in actual:
            raise ReleaseValidationError(f"duplicate {label} file: {relative}")
        if not path.is_file():
            if relative in expected:
                continue
            raise ReleaseValidationError(f"missing {label} artifact: {relative}")
        actual.add(relative)
    missing = sorted(set(expected) - actual)
    extra = sorted(actual - set(expected))
    if missing:
        raise ReleaseValidationError(f"missing required {label} files: {', '.join(missing)}")
    if extra:
        raise ReleaseValidationError(f"unexpected {label} files: {', '.join(extra)}")


def _production_sources(root: Path, source_hashes: Mapping[str, str]) -> tuple[str, ...]:
    package = root / "src" / "merlo"
    if package.is_dir():
        return tuple(sorted(_relative(path, root) for path in package.rglob("*.py") if path.is_file()))
    return ()


def _validate_paths(inputs: ReleaseInputs) -> None:
    root = inputs.root.resolve()
    for label, expected in (("licenses", REQUIRED_LICENSES), ("metadata", REQUIRED_METADATA)):
        for relative in expected:
            path = _as_path(root, relative)
            if not path.is_file():
                raise ReleaseValidationError(f"missing required {label} files: {relative}")
    _inventory_paths(root, inputs.docs, "docs", REQUIRED_DOCS)
    _inventory_paths(root, inputs.specs, "specs", REQUIRED_SPECS)
    _inventory_paths(root, inputs.stdlib, "stdlib", REQUIRED_STDLIB)
    _inventory_paths(root, inputs.examples, "examples", REQUIRED_EXAMPLES)
    expected_sources = set(_production_sources(root, inputs.source_hashes))
    actual_sources = set(inputs.source_hashes)
    missing = sorted(expected_sources - actual_sources)
    extra = sorted(actual_sources - expected_sources)
    if missing:
        raise ReleaseValidationError(f"missing required production sources: {', '.join(missing)}")
    if extra:
        raise ReleaseValidationError(f"unexpected production sources: {', '.join(extra)}")
    if not actual_sources:
        raise ReleaseValidationError("production source set is required")
    for relative, expected in sorted(inputs.source_hashes.items()):
        path = _as_path(root, relative)
        if not path.is_file() or _file_hash(path) != expected:
            raise ReleaseValidationError(f"stale source hash: {relative}")
    compiler = _as_path(root, inputs.compiler_path)
    if not compiler.is_file() or _file_hash(compiler) != inputs.compiler_sha256:
        raise ReleaseValidationError("stale compiler hash")
    lockfile = _as_path(root, inputs.lockfile_path)
    if not lockfile.is_file() or _file_hash(lockfile) != inputs.lock_sha256:
        raise ReleaseValidationError("stale lock hash")
    if not inputs.binaries:
        raise ReleaseValidationError("missing required binaries")
    for value in inputs.binaries:
        path = _as_path(root, value)
        if not path.is_file():
            raise ReleaseValidationError(f"missing binaries artifact: {value}")
        if not os.access(path, os.X_OK):
            raise ReleaseValidationError(f"sample binary is not executable: {value}")
    _validate_provenance(inputs.provenance, root)


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
        if record.payload.get("status") is not None and record.payload["status"] != record.status:
            raise ReleaseValidationError(f"forged evidence status: {record.id}")
        if record.payload.get("passed") is not None and record.payload["passed"] is not (record.status == "PASSED"):
            raise ReleaseValidationError(f"forged evidence pass decision: {record.id}")
        if record.payload.get("executed") is not None and record.payload["executed"] is not record.executed:
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
    if safety_failures:
        status = ALPHA_RELEASE_SAFETY_DEFECT
    elif not gate_values["reproducibility"]:
        status = ALPHA_RELEASE_REPRODUCIBILITY_DEFECT
    elif all(gate_values.values()):
        status = ALPHA_RELEASE_SUPPORTED
    else:
        status = ALPHA_RELEASE_INCOMPLETE
    if inputs.claimed_status is not None and inputs.claimed_status != status:
        raise ReleaseValidationError("forged release status")
    return ValidationResult(status, gate_values, tuple(name for name in REQUIRED_GATES if not gate_values[name]), tuple(sorted(failed_ids)), tuple(sorted(records)), inputs.provenance.versions["package"])


def _copy_file(root: Path, source: Path | str, target: Path, destination: str) -> str:
    path = _as_path(root, source)
    if not path.is_file():
        raise ReleaseValidationError(f"missing assembly source: {source}")
    output = target / destination
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, output)
    return destination


def _manifest_payload(inputs: ReleaseInputs, validation: ValidationResult, files: Mapping[str, str], evidence: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    provenance = inputs.provenance
    return {
        "schema_version": SCHEMA_VERSION, "release": inputs.provenance.versions["package"], "status": validation.status,
        "gates": dict(sorted(validation.gates.items())), "failed_gates": list(validation.failed_gates),
        "failed_evidence_ids": list(validation.failed_evidence_ids), "evidence": list(evidence),
        "provenance": {
            "source_commit": provenance.source_commit, "tag": provenance.tag,
            "versions": dict(sorted(provenance.versions.items())), "platform": provenance.platform,
            "architecture": provenance.architecture, "python": dict(sorted(provenance.python.items())),
            "c_compiler": dict(sorted(provenance.c_compiler.items())),
            "build_frontend": dict(sorted(provenance.build_frontend.items())),
            "source_date_epoch": provenance.source_date_epoch,
        },
        "assets": dict(sorted((Path(name).name, digest) for name, digest in provenance.assets.items())),
        "files": dict(sorted(files.items())),
        "required": {
            "licenses": list(REQUIRED_LICENSES), "metadata": list(REQUIRED_METADATA),
            "docs": list(REQUIRED_DOCS), "specs": list(REQUIRED_SPECS), "stdlib": list(REQUIRED_STDLIB),
            "examples": list(REQUIRED_EXAMPLES), "sources": sorted(inputs.source_hashes),
            "binaries": sorted(_relative(_as_path(inputs.root, item), inputs.root) for item in inputs.binaries),
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


def _manifest_strings(value: Any, field_name: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or (nonempty and not item) for item in value):
        raise ReleaseValidationError(f"manifest {field_name} has invalid strings")
    if len(set(value)) != len(value):
        raise ReleaseValidationError(f"manifest {field_name} has duplicates")
    return value


def _manifest_path(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ReleaseValidationError(f"manifest {field_name} has invalid path")
    path = Path(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {".", ".."} for part in path.parts):
        raise ReleaseValidationError(f"manifest {field_name} has noncanonical path")


def _validate_manifest_shape(manifest: Mapping[str, Any]) -> None:
    top_level = {
        "schema_version", "release", "status", "gates", "failed_gates", "failed_evidence_ids",
        "evidence", "provenance", "assets", "files", "required", "payload_sha256", "manifest_sha256",
    }
    if not isinstance(manifest, Mapping) or set(manifest) != top_level:
        raise ReleaseValidationError("manifest has invalid top-level keys")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != SCHEMA_VERSION:
        raise ReleaseValidationError("unsupported manifest schema")
    if not isinstance(manifest["status"], str) or manifest["status"] not in ALLOWED_STATUSES:
        raise ReleaseValidationError("manifest has invalid status")
    gates = manifest["gates"]
    if not isinstance(gates, Mapping) or set(gates) != set(REQUIRED_GATES) or any(type(value) is not bool for value in gates.values()):
        raise ReleaseValidationError("manifest has invalid gates")
    failed_gates = _manifest_strings(manifest["failed_gates"], "failed_gates")
    failed_evidence_ids = _manifest_strings(manifest["failed_evidence_ids"], "failed_evidence_ids")
    evidence = manifest["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise ReleaseValidationError("manifest validation evidence missing")
    evidence_ids: list[str] = []
    for item in evidence:
        if not isinstance(item, Mapping) or set(item) != {"id", "kind", "gate", "content_sha256", "raw_paths"}:
            raise ReleaseValidationError("manifest validation evidence malformed")
        if not isinstance(item["id"], str) or not item["id"] or not isinstance(item["kind"], str) or not item["kind"]:
            raise ReleaseValidationError("manifest validation evidence malformed")
        if not isinstance(item["gate"], str) or item["gate"] not in REQUIRED_GATES:
            raise ReleaseValidationError("manifest validation evidence malformed")
        if not isinstance(item["content_sha256"], str) or not _HEX64.fullmatch(item["content_sha256"]):
            raise ReleaseValidationError("manifest validation evidence malformed")
        _manifest_strings(item["raw_paths"], "evidence.raw_paths")
        evidence_ids.append(item["id"])
    evidence_gates = dict(zip(evidence_ids, (item["gate"] for item in evidence)))
    if len(evidence_gates) != len(evidence_ids):
        raise ReleaseValidationError("manifest validation evidence has duplicates")
    if set(failed_evidence_ids) - set(evidence_gates):
        raise ReleaseValidationError("manifest validation evidence IDs mismatch")
    expected_failed = {name for name, passed in gates.items() if not passed}
    if set(failed_gates) != expected_failed:
        raise ReleaseValidationError("manifest gate failure set mismatch")
    failed_evidence_gates = {evidence_gates[item] for item in failed_evidence_ids}
    if failed_evidence_gates != expected_failed:
        raise ReleaseValidationError("manifest failed evidence coverage mismatch")
    derived_status = (
        ALPHA_RELEASE_SAFETY_DEFECT if not gates["sanitizers"]
        else ALPHA_RELEASE_REPRODUCIBILITY_DEFECT if not gates["reproducibility"]
        else ALPHA_RELEASE_INCOMPLETE if expected_failed
        else ALPHA_RELEASE_SUPPORTED
    )
    if manifest["status"] != derived_status:
        raise ReleaseValidationError("manifest status derivation mismatch")
    provenance = manifest["provenance"]
    provenance_keys = {
        "source_commit", "tag", "versions", "platform", "architecture", "python",
        "c_compiler", "build_frontend", "source_date_epoch",
    }
    if not isinstance(provenance, Mapping) or set(provenance) != provenance_keys:
        raise ReleaseValidationError("manifest provenance missing")
    if not isinstance(provenance["source_commit"], str) or not _HEX40.fullmatch(provenance["source_commit"]):
        raise ReleaseValidationError("manifest provenance source_commit invalid")
    if not isinstance(provenance["tag"], str) or not provenance["tag"]:
        raise ReleaseValidationError("manifest provenance tag invalid")
    versions = provenance["versions"]
    if not isinstance(versions, Mapping) or set(versions) != {"package", "compiler", "displayed"} or any(
        not isinstance(value, str) or not value for value in versions.values()
    ):
        raise ReleaseValidationError("manifest provenance versions invalid")
    package_version = versions["package"]
    alpha_match = _ALPHA_VERSION.fullmatch(package_version)
    if provenance["tag"] != f"v{package_version}" or manifest["release"] != package_version:
        raise ReleaseValidationError("manifest release identity mismatch")
    if versions["compiler"] != package_version or alpha_match is None or versions["displayed"] != f"Merlo alpha.{alpha_match.group(4)}":
        raise ReleaseValidationError("manifest provenance version mismatch")
    for name in ("platform", "architecture"):
        if not isinstance(provenance[name], str) or not provenance[name]:
            raise ReleaseValidationError(f"manifest provenance {name} invalid")
    for name, keys in (("python", {"implementation", "version"}), ("c_compiler", {"name", "version", "target"}), ("build_frontend", {"name", "version"})):
        value = provenance[name]
        if not isinstance(value, Mapping) or set(value) != keys or any(not isinstance(item, str) or not item for item in value.values()):
            raise ReleaseValidationError(f"manifest provenance {name} invalid")
    if isinstance(provenance["source_date_epoch"], bool) or not isinstance(provenance["source_date_epoch"], int) or provenance["source_date_epoch"] < 0:
        raise ReleaseValidationError("manifest provenance source_date_epoch invalid")
    assets = manifest["assets"]
    if not isinstance(assets, Mapping) or len(assets) != 3 or any(not isinstance(value, str) or not _HEX64.fullmatch(value) for value in assets.values()):
        raise ReleaseValidationError("manifest assets invalid")
    expected_assets = set(_distribution_filenames(versions["package"]))
    if set(assets) != expected_assets:
        raise ReleaseValidationError("manifest asset filenames disagree with package version")
    roles = _asset_roles(assets)
    if roles != {"sdist": next(name for name in expected_assets if name.endswith(".tar.gz")), "wheel": next(name for name in expected_assets if name.endswith(".whl")), "evidence": next(name for name in expected_assets if name.endswith(".zip"))}:
        raise ReleaseValidationError("manifest asset roles disagree with package version")
    files = manifest["files"]
    if not isinstance(files, Mapping) or not files:
        raise ReleaseValidationError("manifest files missing")
    for relative, digest in files.items():
        _manifest_path(relative, "files")
        if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
            raise ReleaseValidationError("manifest files contain invalid SHA-256")
    required = manifest["required"]
    required_keys = {"licenses", "metadata", "docs", "specs", "stdlib", "examples", "sources", "binaries"}
    if not isinstance(required, Mapping) or set(required) != required_keys:
        raise ReleaseValidationError("manifest required inventory missing")
    for name, expected in (
        ("licenses", REQUIRED_LICENSES), ("metadata", REQUIRED_METADATA), ("docs", REQUIRED_DOCS),
        ("specs", REQUIRED_SPECS), ("stdlib", REQUIRED_STDLIB), ("examples", REQUIRED_EXAMPLES),
    ):
        if tuple(_manifest_strings(required[name], f"required.{name}")) != tuple(expected):
            raise ReleaseValidationError(f"manifest required inventory mismatch: {name}")
    sources = _manifest_strings(required["sources"], "required.sources")
    binaries = _manifest_strings(required["binaries"], "required.binaries")
    if not sources or not binaries:
        raise ReleaseValidationError("manifest required sources or binaries missing")
    for filename, digest in assets.items():
        asset_path = f"assets/{filename}"
        if files.get(asset_path) != digest:
            raise ReleaseValidationError(f"manifest asset digest mismatch: {filename}")
    inventory_files = (*REQUIRED_LICENSES, *REQUIRED_METADATA, *REQUIRED_DOCS, *REQUIRED_SPECS, *REQUIRED_STDLIB, *REQUIRED_EXAMPLES)
    for relative in inventory_files:
        if relative not in files or not isinstance(files[relative], str) or not _HEX64.fullmatch(files[relative]):
            raise ReleaseValidationError(f"manifest inventory file missing: {relative}")
    for relative in sources:
        if files.get(f"source/{relative}") is None:
            raise ReleaseValidationError(f"manifest source missing from files: {relative}")
    for relative in binaries:
        if files.get(f"bin/{Path(relative).name}") is None:
            raise ReleaseValidationError(f"manifest binary missing from files: {relative}")
    for field_name in ("payload_sha256", "manifest_sha256"):
        if not isinstance(manifest[field_name], str) or not _HEX64.fullmatch(manifest[field_name]):
            raise ReleaseValidationError(f"manifest {field_name} invalid")


def verify_manifest(manifest: Mapping[str, Any]) -> None:
    _validate_manifest_shape(manifest)
    if manifest.get("payload_sha256") != manifest_payload_sha256(manifest):
        raise ReleaseValidationError("manifest payload self-hash mismatch")
    if manifest.get("manifest_sha256") != manifest_sha256(manifest):
        raise ReleaseValidationError("manifest self-hash mismatch")


def _write_manifest(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    payload_hash = _digest(payload)
    unsigned = {**payload, "payload_sha256": payload_hash}
    manifest = {**unsigned, "manifest_sha256": _digest(unsigned)}
    path.write_text(_canonical(manifest) + "\n", encoding="utf-8")
    return manifest


def _checksum_entries(directory: Path, files: Mapping[str, str]) -> dict[str, str]:
    entries = dict(files)
    entries["manifest.json"] = _file_hash(directory / "manifest.json")
    return dict(sorted(entries.items()))


def _write_checksums(directory: Path, entries: Mapping[str, str]) -> str:
    payload = "".join(f"{digest}  {relative}\n" for relative, digest in sorted(entries.items()))
    (directory / "checksums.sha256").write_text(payload, encoding="utf-8")
    (directory / "SHA256SUMS").write_text(payload, encoding="utf-8")
    return payload


def _read_checksum_file(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReleaseValidationError(f"assembly checksum missing: {path.name}") from exc
    values: dict[str, str] = {}
    for line in lines:
        parts = line.split("  ", 1)
        if len(parts) != 2 or not _HEX64.fullmatch(parts[0]) or not parts[1] or parts[1] in values:
            raise ReleaseValidationError(f"assembly checksum malformed: {path.name}")
        values[parts[1]] = parts[0]
    return values


def _verify_existing(directory: Path, expected_manifest: Mapping[str, Any], expected_checksums: str) -> None:
    manifest_path = directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseValidationError("assembly manifest missing or invalid") from exc
    verify_manifest(manifest)
    expected_files = dict(manifest.get("files", {}))
    allowed = set(expected_files) | {"manifest.json", "checksums.sha256", "SHA256SUMS"}
    actual = {path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()}
    extra = sorted(actual - allowed)
    if extra:
        raise ReleaseValidationError(f"unexpected assembly files: {', '.join(extra)}")
    for relative, digest in sorted(expected_files.items()):
        path = directory / relative
        if not path.is_file():
            raise ReleaseValidationError(f"assembly payload missing: {relative}")
        if _file_hash(path) != digest:
            raise ReleaseValidationError(f"assembly payload modified: {relative}")
    expected_entries = _checksum_entries(directory, expected_files)
    for filename in ("checksums.sha256", "SHA256SUMS"):
        values = _read_checksum_file(directory / filename)
        if values != expected_entries:
            raise ReleaseValidationError(f"assembly checksum mismatch: {filename}")
        for relative, digest in values.items():
            path = directory / relative
            if not path.is_file() or _file_hash(path) != digest:
                raise ReleaseValidationError(f"assembly checksum mismatch: {relative}")
    if manifest != expected_manifest or any(
        (directory / filename).read_text(encoding="utf-8") != expected_checksums
        for filename in ("checksums.sha256", "SHA256SUMS")
    ):
        raise ReleaseValidationError("refusing second release emission with different content")


def assemble_release(inputs: ReleaseInputs, destination: Path | str) -> AssemblyResult:
    validation = validate_release(inputs)
    if validation.status != ALPHA_RELEASE_SUPPORTED:
        raise ReleaseValidationError(f"release cannot be assembled with status {validation.status}")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{destination.name}-", dir=str(destination.parent)) as temporary:
        work = Path(temporary)
        copied: set[str] = set()
        for relative in (*REQUIRED_LICENSES, *REQUIRED_METADATA):
            copied.add(_copy_file(inputs.root, relative, work, relative))
        for values in (inputs.docs, inputs.specs, inputs.stdlib, inputs.examples):
            for value in values:
                relative = _relative(_as_path(inputs.root, value), inputs.root)
                copied.add(_copy_file(inputs.root, value, work, relative))
        for value in inputs.binaries:
            source = _as_path(inputs.root, value)
            copied.add(_copy_file(inputs.root, value, work, f"bin/{source.name}"))
        for relative in sorted(inputs.source_hashes):
            copied.add(_copy_file(inputs.root, relative, work, f"source/{relative}"))
        for filename in sorted(inputs.provenance.assets):
            copied.add(_copy_file(inputs.root, filename, work, f"assets/{Path(filename).name}"))
        evidence_entries: list[dict[str, Any]] = []
        for record in sorted(inputs.evidence, key=lambda item: item.id):
            raw_refs: list[str] = []
            for raw in record.raw_paths:
                source = _as_path(inputs.root, raw)
                ref = f"evidence/raw/{source.name}"
                if ref in copied and _file_hash(work / ref) != _file_hash(source):
                    raise ReleaseValidationError(f"raw evidence name collision: {source.name}")
                copied.add(_copy_file(inputs.root, raw, work, ref))
                raw_refs.append(ref)
            evidence_entries.append({"id": record.id, "kind": record.kind, "gate": record.gate, "content_sha256": record.content_sha256, "raw_paths": raw_refs})
        files = {relative: _file_hash(work / relative) for relative in sorted(copied)}
        payload = _manifest_payload(inputs, validation, files, evidence_entries)
        manifest = _write_manifest(work / "manifest.json", payload)
        entries = _checksum_entries(work, files)
        checksums = _write_checksums(work, entries)
        verify_manifest(manifest)
        if destination.exists():
            _verify_existing(destination, manifest, checksums)
            return AssemblyResult(destination, manifest, validation)
        os.replace(work, destination)
    return AssemblyResult(destination, manifest, validation)


def write_validation_report_once(path: Path | str, validation: ValidationResult) -> Path:
    destination = Path(path)
    unsigned = validation.to_dict()
    report_value = {**unsigned, "payload_sha256": _digest(unsigned)}
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
CI_ATTESTATION_SCHEMA_VERSION = 1
ACTIVE_WORKLOAD_LOCK_PATH = "tools/benchmarks/merlo/benchmarks/alpha_performance/workloads.json"
_JOB_RESPONSIBILITIES = {
    "production": frozenset({"full_tests", "lsp", "examples"}),
    "tooling": frozenset({"corpus", "sanitizers", "simplicity", "performance"}),
    "artifacts": frozenset({"clean_demo", "packaging", "reproducibility"}),
    "archive": frozenset(),
}


@dataclass(frozen=True)
class ReleaseBuildResult:
    """Paths and typed assembly produced by :func:`build_alpha_release`."""

    assembly: AssemblyResult
    evidence_zip: Path
    manifest: Path
    checksums: Path
    sha256sums: Path

    @property
    def path(self) -> Path:
        return self.assembly.path

    @property
    def validation(self) -> ValidationResult:
        return self.assembly.validation


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseValidationError(f"invalid CI attestation JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ReleaseValidationError("CI attestation must be a JSON object")
    return value


def _validate_ci_attestation(attestation: Mapping[str, Any], *, tag: str, commit: str) -> dict[str, Mapping[str, Any]]:
    if attestation.get("schema_version") != CI_ATTESTATION_SCHEMA_VERSION:
        raise ReleaseValidationError("unsupported CI attestation schema")
    if attestation.get("commit") != commit or attestation.get("tag") != tag:
        raise ReleaseValidationError("CI attestation identity mismatch")
    repository = attestation.get("repository")
    run_id = attestation.get("run_id")
    run_url = attestation.get("run_url")
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ReleaseValidationError("CI attestation repository is invalid")
    if isinstance(run_id, bool) or not isinstance(run_id, (int, str)) or not str(run_id).isdigit() or int(run_id) <= 0:
        raise ReleaseValidationError("CI attestation run identity is invalid")
    expected_url = f"https://github.com/{repository}/actions/runs/{int(run_id)}"
    if run_url != expected_url:
        raise ReleaseValidationError("CI attestation run URL is invalid")
    jobs = attestation.get("jobs")
    if not isinstance(jobs, Mapping) or set(jobs) != set(_JOB_RESPONSIBILITIES):
        raise ReleaseValidationError("CI attestation jobs are incomplete")
    validated: dict[str, Mapping[str, Any]] = {}
    for job_name, responsibilities in _JOB_RESPONSIBILITIES.items():
        job = jobs[job_name]
        if not isinstance(job, Mapping) or job.get("result") != "success":
            raise ReleaseValidationError(f"CI job did not succeed: {job_name}")
        observed = job.get("responsibilities")
        if not isinstance(observed, list) or any(not isinstance(item, str) for item in observed):
            raise ReleaseValidationError(f"CI job responsibilities are invalid: {job_name}")
        if set(observed) != set(responsibilities) or len(observed) != len(set(observed)):
            raise ReleaseValidationError(f"CI job responsibilities mismatch: {job_name}")
        validated[job_name] = job
    return validated


def _zip_timestamp(epoch: int) -> tuple[int, int, int, int, int, int]:
    import datetime

    value = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    # ZIP timestamps cannot represent dates before 1980.
    value = max(value, datetime.datetime(1980, 1, 1, tzinfo=datetime.timezone.utc))
    return value.timetuple()[:6]


def _write_deterministic_evidence(
    destination: Path,
    *,
    attestation: Mapping[str, Any],
    tag: str,
    commit: str,
    source_date_epoch: int,
) -> str:
    attestation_bytes = (_canonical(attestation) + "\n").encode("utf-8")
    attestation_hash = _digest_bytes(attestation_bytes)
    manifest_payload = {
        "schema_version": CI_ATTESTATION_SCHEMA_VERSION,
        "tag": tag,
        "commit": commit,
        "source_date_epoch": source_date_epoch,
        "files": {"attestation.json": attestation_hash},
    }
    payload_hash = _digest(manifest_payload)
    unsigned = {**manifest_payload, "payload_sha256": payload_hash}
    manifest = {**unsigned, "manifest_sha256": _digest(unsigned)}
    manifest_bytes = (_canonical(manifest) + "\n").encode("utf-8")
    checksums = (
        f"{attestation_hash}  attestation.json\n"
        f"{_digest_bytes(manifest_bytes)}  manifest.json\n"
    ).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    entries = {
        "attestation.json": attestation_bytes,
        "manifest.json": manifest_bytes,
        "checksums.sha256": checksums,
        "SHA256SUMS": checksums,
    }
    timestamp = _zip_timestamp(source_date_epoch)
    with tempfile.NamedTemporaryFile(prefix=f".{destination.name}-", dir=destination.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, content in entries.items():
                info = zipfile.ZipInfo(name, timestamp)
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return attestation_hash

def canonicalize_sdist(path: Path | str, source_date_epoch: int) -> Path:
    """Rewrite an sdist with canonical tar and gzip metadata."""
    source = Path(path)
    if isinstance(source_date_epoch, bool) or not isinstance(source_date_epoch, int) or source_date_epoch < 0:
        raise ReleaseValidationError("invalid source date epoch")
    try:
        with tarfile.open(source, "r:*") as archive:
            members = archive.getmembers()
            entries: list[tuple[tarfile.TarInfo, bytes]] = []
            names: set[str] = set()
            for member in members:
                name = member.name
                candidate = Path(name)
                if (
                    not name
                    or "\\" in name
                    or candidate.is_absolute()
                    or any(part in {"", ".", ".."} for part in candidate.parts)
                    or name in names
                ):
                    raise ReleaseValidationError("sdist contains unsafe or duplicate member path")
                if member.issym() or member.islnk() or not (member.isdir() or member.isreg()):
                    raise ReleaseValidationError("sdist contains unsupported link or special member")
                content = b""
                if member.isreg():
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise ReleaseValidationError("sdist regular member has no content")
                    content = extracted.read()
                normalized = tarfile.TarInfo(name)
                normalized.type = tarfile.DIRTYPE if member.isdir() else tarfile.REGTYPE
                normalized.mode = member.mode & 0o777
                normalized.mtime = source_date_epoch
                normalized.uid = 0
                normalized.gid = 0
                normalized.uname = ""
                normalized.gname = ""
                normalized.size = len(content)
                entries.append((normalized, content))
                names.add(name)
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseValidationError("cannot read sdist") from exc
    entries.sort(key=lambda item: item[0].name)
    source.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{source.name}-", dir=source.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with temporary_path.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=source_date_epoch, compresslevel=9) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as output:
                    for member, content in entries:
                        output.addfile(member, io.BytesIO(content) if member.isreg() else None)
        os.replace(temporary_path, source)
    finally:
        temporary_path.unlink(missing_ok=True)
    return source


def _release_toolchain_identity() -> tuple[dict[str, str], dict[str, str]]:
    try:
        from merlo.native_c_backend import compiler_version, find_c_compiler
        compiler = find_c_compiler()
        if compiler is None:
            raise ReleaseValidationError("release C compiler is unavailable")
        version = compiler_version(compiler)
    except (ImportError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise ReleaseValidationError("release C compiler identity is unavailable") from exc
    if not version:
        raise ReleaseValidationError("release C compiler version is unavailable")
    try:
        frontend_version = importlib.metadata.version("build")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ReleaseValidationError("release build frontend identity is unavailable") from exc
    return (
        {"name": Path(compiler).name, "version": version, "target": platform.machine() or "unknown"},
        {"name": "build", "version": frontend_version},
    )


def _asset_source(root: Path, dist: Path, path: Path, filename: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        staging = root / ".merlo-release-assets"
        staging.mkdir(parents=True, exist_ok=True)
        target = staging / filename
        shutil.copyfile(path, target)
        return _relative(target, root)


def build_alpha_release(
    root: Path | str,
    dist: Path | str,
    output: Path | str,
    tag: str,
    commit: str,
    source_date_epoch: int,
    *,
    sample_binary: Path | str,
    attestation: Path | str,
) -> ReleaseBuildResult:
    """Validate CI evidence and assemble the three alpha distribution assets."""
    base = Path(root).resolve()
    dist_path = Path(dist).resolve()
    if not base.is_dir() or not dist_path.is_dir():
        raise ReleaseValidationError("release root and dist must be directories")
    if not _HEX40.fullmatch(commit):
        raise ReleaseValidationError("release commit must be a 40-hex SHA")
    if tag != f"v{RELEASE_VERSION}":
        raise ReleaseValidationError("release tag disagrees with package version")
    if isinstance(source_date_epoch, bool) or not isinstance(source_date_epoch, int) or source_date_epoch < 0:
        raise ReleaseValidationError("invalid source date epoch")
    binary = _as_path(base, sample_binary)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ReleaseValidationError("sample binary is not executable")
    attestation_path = _as_path(base, attestation)
    if not attestation_path.is_file():
        raise ReleaseValidationError("missing CI attestation")
    attestation_value = _read_json_object(attestation_path)
    jobs = _validate_ci_attestation(attestation_value, tag=tag, commit=commit)
    sdist_name, wheel_name, evidence_name = _distribution_filenames(RELEASE_VERSION)
    wheel = dist_path / wheel_name
    sdist = dist_path / sdist_name
    if not wheel.is_file() or not sdist.is_file():
        raise ReleaseValidationError("dist is missing the expected wheel or sdist")
    evidence_zip = dist_path / evidence_name
    attestation_hash = _write_deterministic_evidence(
        evidence_zip,
        attestation=attestation_value,
        tag=tag,
        commit=commit,
        source_date_epoch=source_date_epoch,
    )
    raw_relative = _relative(attestation_path, base)
    raw_hashes = {raw_relative: _file_hash(attestation_path)}
    source_hashes = {
        _relative(path, base): _file_hash(path)
        for path in sorted((base / "src" / "merlo").rglob("*.py"))
        if path.is_file()
    }
    if not source_hashes:
        raise ReleaseValidationError("production source set is required")
    compiler_path = base / "src" / "merlo" / "compiler.py"
    lockfile_path = base / ACTIVE_WORKLOAD_LOCK_PATH
    if not compiler_path.is_file() or not lockfile_path.is_file():
        raise ReleaseValidationError("release compiler or workload lock is missing")
    compiler_sha256 = _file_hash(compiler_path)
    lock_sha256 = _file_hash(lockfile_path)
    artifact_hashes = {
        _asset_source(base, dist_path, wheel, wheel_name): _file_hash(wheel),
        _asset_source(base, dist_path, sdist, sdist_name): _file_hash(sdist),
    }
    records = []
    for gate in REQUIRED_GATES:
        owner = next(name for name, values in _JOB_RESPONSIBILITIES.items() if gate in values)
        record = EvidenceRecord(
            id=f"ci.{owner}.{gate}",
            kind="github-ci",
            gate=gate,
            status="PASSED",
            executed=True,
            supported=True,
            payload={
                "repository": attestation_value["repository"],
                "run_id": int(attestation_value["run_id"]),
                "run_url": attestation_value["run_url"],
                "job": owner,
                "result": jobs[owner]["result"],
                "responsibility": gate,
                "attestation_sha256": attestation_hash,
            },
            raw_paths=(raw_relative,),
            source_hashes=source_hashes,
            compiler_sha256=compiler_sha256,
            lock_sha256=lock_sha256,
            raw_hashes=raw_hashes,
            artifact_hashes=artifact_hashes if gate == "reproducibility" else {},
        )
        records.append(record)
    asset_paths = {
        "wheel": _asset_source(base, dist_path, wheel, wheel_name),
        "sdist": _asset_source(base, dist_path, sdist, sdist_name),
        "evidence": _asset_source(base, dist_path, evidence_zip, evidence_name),
    }
    c_compiler, build_frontend = _release_toolchain_identity()
    provenance = ReleaseProvenance(
        source_commit=commit,
        tag=tag,
        versions={"package": RELEASE_VERSION, "compiler": RELEASE_VERSION, "displayed": "Merlo alpha.2"},
        platform=platform.system() or "unknown",
        architecture=platform.machine() or "unknown",
        python={"implementation": platform.python_implementation(), "version": platform.python_version()},
        c_compiler=c_compiler,
        build_frontend=build_frontend,
        source_date_epoch=source_date_epoch,
        assets={
            asset_paths["wheel"]: _file_hash(wheel),
            asset_paths["sdist"]: _file_hash(sdist),
            asset_paths["evidence"]: _file_hash(evidence_zip),
        },
    )
    inputs = ReleaseInputs(
        root=base,
        gates=tuple(GateInput(name=gate, evidence_ids=(record.id,)) for gate, record in zip(REQUIRED_GATES, records)),
        evidence=tuple(records),
        source_hashes=source_hashes,
        compiler_path=Path("src/merlo/compiler.py"),
        compiler_sha256=compiler_sha256,
        lockfile_path=Path(ACTIVE_WORKLOAD_LOCK_PATH),
        lock_sha256=lock_sha256,
        docs=tuple(REQUIRED_DOCS),
        specs=tuple(REQUIRED_SPECS),
        stdlib=tuple(REQUIRED_STDLIB),
        examples=tuple(REQUIRED_EXAMPLES),
        binaries=(binary.relative_to(base),),
        provenance=provenance,
    )
    assembly = assemble_release(inputs, output)
    verify_manifest(assembly.manifest)
    return ReleaseBuildResult(
        assembly=assembly,
        evidence_zip=evidence_zip,
        manifest=assembly.path / "manifest.json",
        checksums=assembly.path / "checksums.sha256",
        sha256sums=assembly.path / "SHA256SUMS",
    )


build_release = build_alpha_release


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble a Merlo alpha release from typed CI evidence")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--binary", "--sample-binary", dest="sample_binary", type=Path, required=True)
    parser.add_argument("--attestation", "--ci-attestation", dest="attestation", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build_alpha_release(
            args.root, args.dist, args.output, args.tag, args.commit, args.source_date_epoch,
            sample_binary=args.sample_binary, attestation=args.attestation,
        )
    except ReleaseValidationError as exc:
        parser.error(str(exc))
    print(json.dumps({
        "status": result.validation.status,
        "output": str(result.path),
        "evidence": str(result.evidence_zip),
        "manifest": str(result.manifest),
        "checksums": str(result.checksums),
        "SHA256SUMS": str(result.sha256sums),
    }, sort_keys=True))
    return 0




def public_benchmark_evidence(
    report: Mapping[str, Any],
    report_path: Path | str,
    *,
    root: Path | str = ".",
    source_hashes: Mapping[str, str] | None = None,
    compiler_sha256: str | None = None,
    lock_sha256: str | None = None,
) -> EvidenceRecord:
    """Adapt one canonical retained public report into release evidence."""
    from tools.benchmarks.merlo.public_benchmark import (
        PublicBenchmarkError,
        canonical_report_bytes,
        validate_public_report,
    )

    base = Path(root).resolve()
    candidate = Path(report_path)
    if "\x00" in str(candidate) or any(part in {".", ".."} for part in candidate.parts):
        raise ReleaseValidationError("public benchmark report path is not normalized")
    unresolved = candidate if candidate.is_absolute() else base / candidate
    try:
        relative_path = unresolved.relative_to(base)
    except ValueError as exc:
        raise ReleaseValidationError(
            f"public benchmark report escapes release root: {report_path}"
        ) from exc
    cursor = base
    for part in relative_path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ReleaseValidationError("public benchmark report cannot be symlinked")
    resolved = unresolved.resolve()
    if not resolved.is_file():
        raise ReleaseValidationError(f"missing public benchmark report: {report_path}")
    raw_bytes = resolved.read_bytes()
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseValidationError("public benchmark report is not valid JSON") from exc
    if parsed != dict(report) or canonical_report_bytes(parsed) != raw_bytes:
        raise ReleaseValidationError("public benchmark report path disagrees with supplied report")
    try:
        validate_public_report(parsed, root=base)
    except PublicBenchmarkError as exc:
        raise ReleaseValidationError(f"invalid public benchmark report: {exc}") from exc
    relative = resolved.relative_to(base).as_posix()
    provenance = parsed.get("compiler_provenance")
    lock = parsed.get("workload_lock")
    toolchains = parsed.get("toolchains")
    if not isinstance(provenance, Mapping) or not isinstance(lock, Mapping) or not isinstance(toolchains, Mapping):
        raise ReleaseValidationError("public benchmark provenance is incomplete")
    c_toolchain = toolchains.get("c")
    if compiler_sha256 is None or not isinstance(c_toolchain, Mapping) or c_toolchain.get("binary_sha256") != compiler_sha256:
        raise ReleaseValidationError("release compiler hash is not bound to C toolchain")
    report_hash = _digest_bytes(raw_bytes)
    selected_sources = dict(source_hashes or {
        "compiler_input_tree_sha256": str(provenance.get("source_tree_sha256")),
        "runner_sha256": str(provenance.get("runner_sha256")),
    })
    report_lock = str(lock.get("sha256"))
    if lock_sha256 is not None and lock_sha256 != report_lock:
        raise ReleaseValidationError("public benchmark lock override disagrees with report")
    selected_lock = report_lock
    payload = {
        "schema_version": parsed.get("schema_version"),
        "claim_id": parsed.get("claim_id"),
        "status": parsed.get("status"),
        "passed": parsed.get("passed"),
        "report_sha256": report_hash,
        "workload_lock_sha256": selected_lock,
        "compiler_tree_sha256": provenance.get("source_tree_sha256"),
        "runner_sha256": provenance.get("runner_sha256"),
    }
    return EvidenceRecord(
        id="performance.public-native-three-workload-v1",
        kind="public-benchmark",
        gate="performance",
        status="PASSED" if parsed.get("status") == "MEASURED" and parsed.get("passed") is True else "FAILED",
        executed=True,
        supported=True,
        payload=payload,
        raw_paths=(relative,),
        source_hashes=selected_sources,
        compiler_sha256=compiler_sha256,
        lock_sha256=selected_lock,
        raw_hashes={relative: report_hash},
        artifact_hashes={},
    )


__all__ = [
    "ACTIVE_WORKLOAD_LOCK_PATH", "ALLOWED_STATUSES", "ALPHA_RELEASE_INCOMPLETE",
    "ALPHA_RELEASE_REPRODUCIBILITY_DEFECT", "ALPHA_RELEASE_SAFETY_DEFECT",
    "ALPHA_RELEASE_SUPPORTED", "AssemblyResult", "CI_ATTESTATION_SCHEMA_VERSION",
    "EvidenceRecord", "GateInput", "RELEASE_VERSION", "REQUIRED_DOCS",
    "REQUIRED_EXAMPLES", "REQUIRED_GATES", "REQUIRED_LICENSES", "REQUIRED_METADATA",
    "REQUIRED_SPECS", "REQUIRED_STDLIB", "ReleaseBuildResult", "ReleaseInputs",
    "ReleaseProvenance", "ReleaseValidationError", "SCHEMA_VERSION", "ValidationResult",
    "assemble_release", "build_alpha_release", "build_release", "canonicalize_sdist", "main",
    "manifest_payload_sha256", "manifest_sha256", "validate_release",
    "public_benchmark_evidence",
]


if __name__ == "__main__":
    raise SystemExit(main())
