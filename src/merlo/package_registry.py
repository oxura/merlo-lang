"""Deterministic, offline-first package registry primitives.

The registry deliberately separates metadata/index resolution from transport.  A
resolver never performs I/O unless ``fetch`` is explicitly requested and an
injected transport is supplied.
"""
from __future__ import annotations

from collections import deque
import io
import hashlib
import json
import os
import posixpath
import re
import stat
import shutil
import zipfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

SCHEMA_VERSION = 1
MAX_ARCHIVE_ENTRIES = 10_000
MAX_EXTRACTED_BYTES = 1 << 30


class RegistryError(ValueError):
    def __init__(self, message: str, code: str = "RegistryError") -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class RegistryTransport(Protocol):
    def fetch(self, url: str) -> bytes: ...


@dataclass(frozen=True, order=True)
class PackageMetadata:
    name: str
    version: str
    sha256: str
    archive: str = ""
    dependencies: Mapping[str, str] = field(default_factory=dict)
    size: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not self.name
            or not isinstance(self.version, str)
            or _parse_version(self.version) is None
            or not isinstance(self.sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
        ):
            raise RegistryError("invalid package metadata", "InvalidMetadata")
        if self.size is not None and (type(self.size) is not int or self.size < 0):
            raise RegistryError("archive size must be non-negative", "InvalidMetadata")
        if not isinstance(self.dependencies, Mapping):
            raise RegistryError("dependencies must be an object", "InvalidMetadata")
        dependencies = {
            str(key): str(value)
            for key, value in self.dependencies.items()
            if isinstance(key, str) and key and isinstance(value, str) and value
        }
        if len(dependencies) != len(self.dependencies):
            raise RegistryError("invalid dependency constraint", "InvalidMetadata")
        object.__setattr__(self, "dependencies", MappingProxyType(dict(sorted(dependencies.items()))))

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"name": self.name, "version": self.version, "sha256": self.sha256}
        if self.archive:
            value["archive"] = self.archive
        if self.dependencies:
            value["dependencies"] = dict(self.dependencies)
        if self.size is not None:
            value["size"] = self.size
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PackageMetadata":
        raw_sha = value.get("sha256", value.get("archive_sha256", value.get("digest")))
        if not {"name", "version"}.issubset(value) or raw_sha is None:
            raise RegistryError("package metadata misses required fields", "InvalidMetadata")
        deps = value.get("dependencies", {})
        if not isinstance(deps, Mapping):
            raise RegistryError("dependencies must be an object", "InvalidMetadata")
        return cls(str(value["name"]), str(value["version"]), str(raw_sha).lower(), str(value.get("archive", value.get("url", ""))), deps, value.get("size"))


@dataclass(frozen=True)
class RegistryIndex:
    packages: tuple[PackageMetadata, ...]
    schema: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema != SCHEMA_VERSION:
            raise RegistryError("unsupported index schema", "SchemaMismatch")
        ordered = tuple(sorted(self.packages, key=lambda p: (p.name, _version_key(p.version))))
        if len({(p.name, p.version) for p in ordered}) != len(ordered):
            raise RegistryError("duplicate package version", "DuplicateMetadata")
        object.__setattr__(self, "packages", ordered)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": self.schema, "packages": [item.to_dict() for item in self.packages]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RegistryIndex":
        if raw.get("schema", raw.get("index_schema", raw.get("registry", SCHEMA_VERSION))) != SCHEMA_VERSION:
            raise RegistryError("unsupported index schema", "SchemaMismatch")
        rows = raw.get("packages", [])
        if isinstance(rows, Mapping):
            expanded: list[dict[str, Any]] = []
            for name, values in rows.items():
                entries = values if isinstance(values, list) else [values]
                if any(not isinstance(item, Mapping) for item in entries):
                    raise RegistryError("package entries must be objects", "InvalidIndex")
                expanded.extend(dict(item, name=name) for item in entries)
            rows = expanded
        if not isinstance(rows, list) or any(not isinstance(item, Mapping) for item in rows):
            raise RegistryError("packages must be a list", "InvalidIndex")
        return cls(tuple(PackageMetadata.from_dict(item) for item in rows))

    @classmethod
    def from_json(cls, text: str | bytes) -> "RegistryIndex":
        try:
            raw = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise RegistryError("invalid index JSON", "InvalidIndex") from exc
        if not isinstance(raw, Mapping):
            raise RegistryError("index must be an object", "InvalidIndex")
        return cls.from_dict(raw)

    def find(self, name: str, constraint: str = "*") -> PackageMetadata:
        candidates = [item for item in self.packages if item.name == name and satisfies(item.version, constraint)]
        if not candidates:
            raise RegistryError(f"no package satisfies {name} {constraint}", "UnsatisfiedConstraint")
        return max(candidates, key=lambda item: _version_key(item.version))


@dataclass(frozen=True)
class ResolvedLock:
    packages: tuple[PackageMetadata, ...]
    roots: tuple[tuple[str, str], ...]
    schema: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema != SCHEMA_VERSION:
            raise RegistryError("unsupported lock schema", "SchemaMismatch")
        if any(not isinstance(item, PackageMetadata) for item in self.packages):
            raise RegistryError("invalid lock packages", "InvalidLock")
        packages = tuple(sorted(self.packages, key=lambda item: (item.name, _version_key(item.version))))
        if len({item.name for item in packages}) != len(packages):
            raise RegistryError("duplicate locked package", "InvalidLock")
        try:
            roots = tuple(sorted((str(name), str(constraint)) for name, constraint in self.roots))
        except (TypeError, ValueError) as exc:
            raise RegistryError("invalid lock roots", "InvalidLock") from exc
        if any(not name or not constraint for name, constraint in roots) or len({name for name, _ in roots}) != len(roots):
            raise RegistryError("invalid lock roots", "InvalidLock")
        object.__setattr__(self, "packages", packages)
        object.__setattr__(self, "roots", roots)

    def to_dict(self) -> dict[str, Any]:
        return {"lockfile": self.schema, "packages": [p.to_dict() for p in self.packages], "roots": [dict(name=n, constraint=c) for n, c in self.roots]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ResolvedLock":
        if not isinstance(raw, Mapping) or set(raw) != {"lockfile", "packages", "roots"}:
            raise RegistryError("invalid lock object", "InvalidLock")
        if raw["lockfile"] != SCHEMA_VERSION:
            raise RegistryError("unsupported lock schema", "SchemaMismatch")
        rows = raw["packages"]
        roots = raw["roots"]
        if not isinstance(rows, list) or any(not isinstance(item, Mapping) for item in rows):
            raise RegistryError("invalid lock packages", "InvalidLock")
        if not isinstance(roots, list) or any(
            not isinstance(item, Mapping) or set(item) != {"name", "constraint"}
            for item in roots
        ):
            raise RegistryError("invalid lock roots", "InvalidLock")
        return cls(
            tuple(PackageMetadata.from_dict(item) for item in rows),
            tuple((item["name"], item["constraint"]) for item in roots),
            raw["lockfile"],
        )

    @classmethod
    def from_json(cls, text: str | bytes) -> "ResolvedLock":
        try:
            raw = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise RegistryError("invalid lock JSON", "InvalidLock") from exc
        if not isinstance(raw, Mapping):
            raise RegistryError("lock must be an object", "InvalidLock")
        return cls.from_dict(raw)


def _parse_version(value: str) -> tuple[int, int, int, tuple[tuple[int, int | str], ...] | None] | None:
    match = re.fullmatch(
        r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?",
        value.strip(),
    )
    if not match:
        return None
    prerelease = match.group(4)
    identifiers = None
    if prerelease is not None:
        identifiers = tuple(
            (0, int(item)) if item.isdigit() else (1, item)
            for item in prerelease.split(".")
        )
    return int(match.group(1)), int(match.group(2) or 0), int(match.group(3) or 0), identifiers


def _version_key(value: str) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
    parsed = _parse_version(value)
    if parsed is None:
        return (0, 0, 0, 0, ())
    major, minor, patch, prerelease = parsed
    return major, minor, patch, 1 if prerelease is None else 0, prerelease or ()


def satisfies(version: str, constraint: str) -> bool:
    """Evaluate the small deterministic constraint language used by indexes."""
    if not constraint or constraint.strip() in {"*", "any"}:
        return True
    parsed = _parse_version(version)
    if parsed is None:
        return False
    if parsed[3] is not None and "-" not in constraint:
        return False
    v = parsed[:3]
    for term in re.split(r"\s*,\s*|\s+", constraint.strip()):
        if not term:
            continue
        op, raw = "==", term
        for candidate in (">=", "<=", "!=", "^", "~", ">", "<", "="):
            if term.startswith(candidate):
                op, raw = candidate, term[len(candidate):]
                break
        parsed_constraint = _parse_version(raw)
        w = parsed_constraint[:3] if parsed_constraint is not None else (0, 0, 0)
        if op == "^":
            upper = (w[0] + 1, 0, 0) if w[0] else ((0, w[1] + 1, 0) if w[1] else (0, 0, w[2] + 1))
            if not (v >= w and v < upper): return False
        elif op == "~" and not (v >= w and v[:2] == w[:2]): return False
        elif op in {"==", "="}:
            if "x" in raw.lower() or "*" in raw:
                parts = raw.replace("*", "x").lower().split(".")
                if any(str(v[i]) != p for i, p in enumerate(parts) if p != "x"): return False
            elif parsed_constraint is None or _version_key(version) != _version_key(raw): return False
        elif parsed_constraint is None:
            raise RegistryError(raw, "InvalidConstraint")
        elif op == ">=" and not _version_key(version) >= _version_key(raw): return False
        elif op == "<=" and not _version_key(version) <= _version_key(raw): return False
        elif op == ">" and not _version_key(version) > _version_key(raw): return False
        elif op == "<" and not _version_key(version) < _version_key(raw): return False
        elif op == "!=" and _version_key(version) == _version_key(raw): return False
    return True


class PackageResolver:
    def __init__(self, index: RegistryIndex, *, transport: RegistryTransport | Callable[[str], bytes] | None = None, cache_dir: str | Path | None = None) -> None:
        self.index = index
        self.transport = transport
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None

    def resolve(self, requirements: Mapping[str, str] | None = None, *, lock: ResolvedLock | Mapping[str, Any] | None = None, offline: bool = False) -> ResolvedLock:
        if lock is not None:
            if isinstance(lock, Mapping):
                lock = ResolvedLock.from_dict(lock)
            if not isinstance(lock, ResolvedLock):
                raise RegistryError("invalid lock", "InvalidLock")
            if requirements is not None:
                supplied_roots = tuple(sorted((str(name), str(constraint)) for name, constraint in requirements.items()))
                if supplied_roots != lock.roots:
                    raise RegistryError("lock roots do not match requirements", "LockTampered")
            expected = self.resolve(dict(lock.roots))
            if [item.to_dict() for item in expected.packages] != [item.to_dict() for item in lock.packages]:
                raise RegistryError("lock dependency closure mismatch", "LockTampered")
            if offline:
                for package in lock.packages:
                    self._verify_file(package, self._cache_path(package))
            return lock
        if requirements is not None and not isinstance(requirements, Mapping):
            raise RegistryError("requirements must be an object", "InvalidRequirements")
        roots = tuple(sorted((str(name), str(constraint)) for name, constraint in (requirements or {}).items()))
        if any(not name or not constraint for name, constraint in roots):
            raise RegistryError("invalid requirement", "InvalidRequirements")
        selected: dict[str, PackageMetadata] = {}
        pending = deque(roots)
        while pending:
            name, constraint = pending.popleft()
            item = self.index.find(name, constraint)
            previous = selected.get(name)
            if previous is not None:
                if not satisfies(previous.version, constraint):
                    raise RegistryError(name, "ConflictingConstraint")
                continue
            selected[name] = item
            pending.extend(sorted(item.dependencies.items()))
        result = ResolvedLock(tuple(selected[name] for name in sorted(selected)), roots)
        if offline:
            for item in result.packages:
                if self.cache_dir is None or not self._cache_path(item).is_file():
                    raise RegistryError(item.name, "OfflineArtifactMissing")
                self._verify_file(item, self._cache_path(item))
        return result

    def fetch(self, package: PackageMetadata, *, url: str | None = None) -> Path:
        if self.transport is None:
            raise RegistryError("explicit transport is required for fetch", "TransportRequired")
        target = self._cache_path(package)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            self._verify_file(package, target)
            return target
        source = url or package.archive
        if not source:
            raise RegistryError(package.name, "ArchiveUnavailable")
        if isinstance(self.transport, Mapping):
            payload = self.transport.get(source)
        else:
            payload = (
                self.transport.fetch(source)
                if hasattr(self.transport, "fetch")
                else self.transport(source)
            )
        if not isinstance(payload, bytes):
            raise RegistryError("transport must return bytes", "TransportError")
        if package.size is not None and len(payload) != package.size:
            raise RegistryError(package.name, "ArchiveSizeMismatch")
        self.verify_archive(payload, package.sha256)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=".merlo-fetch-",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self._verify_file(package, target)
        return target

    def _cache_path(self, package: PackageMetadata) -> Path:
        if self.cache_dir is None:
            raise RegistryError("cache directory is required", "CacheRequired")
        return self.cache_dir / "sha256" / package.sha256[:2] / package.sha256[2:]

    @staticmethod
    def verify_archive(payload: bytes, expected_sha256: str) -> None:
        if not isinstance(payload, bytes) or hashlib.sha256(payload).hexdigest() != expected_sha256.lower():
            raise RegistryError("archive digest mismatch", "ArchiveTampered")
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_ARCHIVE_ENTRIES:
                    raise RegistryError("too many archive entries", "ArchiveLimitExceeded")
                total_size = 0
                names: set[str] = set()
                for info in infos:
                    name = info.filename.replace("\\", "/")
                    normalized = posixpath.normpath(name)
                    if (
                        not name
                        or name.startswith("/")
                        or re.match(r"^[A-Za-z]:", name)
                        or "\x00" in name
                        or normalized in {".", ".."}
                        or normalized.startswith("../")
                        or "/../" in f"/{name}"
                    ):
                        raise RegistryError(name, "ArchivePathEscape")
                    if normalized in names:
                        raise RegistryError(name, "ArchiveDuplicatePath")
                    names.add(normalized)
                    total_size += info.file_size
                    if total_size > MAX_EXTRACTED_BYTES:
                        raise RegistryError("archive expands beyond limit", "ArchiveLimitExceeded")
                    mode = (info.external_attr >> 16) & 0xFFFF
                    if stat.S_ISLNK(mode):
                        raise RegistryError(name, "ArchiveSymlink")
        except zipfile.BadZipFile as exc:
            raise RegistryError("invalid zip archive", "ArchiveInvalid") from exc

    @staticmethod
    def _read_file(path: Path) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise RegistryError(str(path), "CacheTampered") from exc
        with os.fdopen(descriptor, "rb") as stream:
            return stream.read()

    def _verify_file(self, package: PackageMetadata, path: Path) -> None:
        payload = self._read_file(path)
        self.verify_archive(payload, package.sha256)
        if package.size is not None and len(payload) != package.size:
            raise RegistryError(package.name, "ArchiveSizeMismatch")

    def extract(self, package: PackageMetadata, destination: str | Path) -> Path:
        """Atomically extract a verified cached archive into a new destination."""
        archive_path = self._cache_path(package)
        payload = self._read_file(archive_path)
        self.verify_archive(payload, package.sha256)
        if package.size is not None and len(payload) != package.size:
            raise RegistryError(package.name, "ArchiveSizeMismatch")
        root = Path(destination).absolute()
        root.parent.mkdir(parents=True, exist_ok=True)
        if root.exists() or root.is_symlink():
            raise RegistryError(str(root), "DestinationExists")
        temporary = Path(tempfile.mkdtemp(prefix=f".{root.name}.merlo-", dir=root.parent))
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                for info in archive.infolist():
                    name = posixpath.normpath(info.filename.replace("\\", "/"))
                    target = temporary.joinpath(*name.split("/"))
                    if info.is_dir() or info.filename.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    flags = (
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0)
                    )
                    descriptor = os.open(target, flags, 0o644)
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(archive.read(info))
            if root.exists() or root.is_symlink():
                raise RegistryError(str(root), "DestinationExists")
            os.replace(temporary, root)
            temporary = root
            return root
        finally:
            if temporary != root:
                shutil.rmtree(temporary, ignore_errors=True)
Resolver = PackageResolver
PackageIndex = RegistryIndex
PackageRegistry = PackageResolver
Registry = PackageResolver
PackageRecord = PackageMetadata
RegistryMetadata = PackageMetadata
canonical_lock = lambda lock: lock.to_json()



__all__ = ["PackageMetadata", "RegistryIndex", "ResolvedLock", "PackageResolver", "Resolver", "PackageIndex", "PackageRegistry", "Registry", "PackageRecord", "RegistryMetadata", "RegistryError", "RegistryTransport", "satisfies", "canonical_lock"]

