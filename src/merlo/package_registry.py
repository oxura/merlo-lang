"""Deterministic, offline-first package registry primitives.

The registry deliberately separates metadata/index resolution from transport.  A
resolver never performs I/O unless ``fetch`` is explicitly requested and an
injected transport is supplied.
"""
from __future__ import annotations

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
        for constraint in dependencies.values():
            _validate_constraint(constraint)
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
            roots = tuple(sorted((name, constraint) for name, constraint in self.roots))
        except (TypeError, ValueError) as exc:
            raise RegistryError("invalid lock roots", "InvalidLock") from exc
        if (
            any(
                not isinstance(name, str)
                or not isinstance(constraint, str)
                or not name
                or not constraint
                for name, constraint in roots
            )
            or len({name for name, _ in roots}) != len(roots)
        ):
            raise RegistryError("invalid lock roots", "InvalidLock")
        for _, constraint in roots:
            try:
                _validate_constraint(constraint)
            except RegistryError as exc:
                raise RegistryError(str(exc), "InvalidLock") from exc
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


_CONSTRAINT_OPERATORS = (">=", "<=", "!=", "^", "~", ">", "<", "=")


def _constraint_terms(constraint: str) -> tuple[tuple[str, str, tuple[int, int, int, tuple[tuple[int, int | str], ...] | None] | None, tuple[str, ...] | None], ...]:
    """Parse and validate every term before any tuple indexing occurs."""
    if not isinstance(constraint, str):
        raise RegistryError("constraint must be a string", "InvalidConstraint")
    value = constraint.strip()
    if value in {"*", "any"}:
        return (("any", "", None, None),)
    if not value:
        raise RegistryError("empty constraint", "InvalidConstraint")
    raw_terms = re.split(r"\s*,\s*|\s+", value)
    if any(not term for term in raw_terms):
        raise RegistryError(value, "InvalidConstraint")
    parsed: list[tuple[str, str, tuple[int, int, int, tuple[tuple[int, int | str], ...] | None] | None, tuple[str, ...] | None]] = []
    for term in raw_terms:
        op, raw = "==", term
        for candidate in _CONSTRAINT_OPERATORS:
            if term.startswith(candidate):
                op, raw = candidate, term[len(candidate):]
                break
        if not raw:
            raise RegistryError(term, "InvalidConstraint")
        lowered = raw.lower()
        wildcard_parts: tuple[str, ...] | None = None
        if "*" in raw or any(part == "x" for part in lowered.split(".")):
            if op not in {"==", "="}:
                raise RegistryError(term, "InvalidConstraint")
            normalized = lowered.replace("*", "x")
            parts = tuple(normalized.split("."))
            if not 1 <= len(parts) <= 3 or any(part == "" for part in parts):
                raise RegistryError(term, "InvalidConstraint")
            if parts.count("x") > 1:
                raise RegistryError(term, "InvalidConstraint")
            wildcard_seen = False
            for part in parts:
                if part == "x":
                    wildcard_seen = True
                elif wildcard_seen or not part.isdigit():
                    raise RegistryError(term, "InvalidConstraint")
            wildcard_parts = parts
            parsed.append((op, raw, None, wildcard_parts))
            continue
        parsed_version = _parse_version(raw)
        if parsed_version is None:
            raise RegistryError(term, "InvalidConstraint")
        parsed.append((op, raw, parsed_version, None))
    return tuple(parsed)


def _validate_constraint(constraint: str) -> None:
    _constraint_terms(constraint)


def satisfies(version: str, constraint: str) -> bool:
    """Evaluate the deterministic constraint language used by indexes."""
    terms = _constraint_terms(constraint)
    parsed = _parse_version(version)
    if parsed is None:
        return False
    if parsed[3] is not None and not any("-" in raw for _, raw, _, _ in terms):
        return False
    version_key = _version_key(version)
    release = parsed[:3]
    for op, raw, parsed_constraint, wildcard_parts in terms:
        if op == "any":
            continue
        if wildcard_parts is not None:
            if any(part != "x" and int(part) != release[index] for index, part in enumerate(wildcard_parts)):
                return False
            continue
        assert parsed_constraint is not None
        bound = parsed_constraint[:3]
        if op == "^":
            upper = (bound[0] + 1, 0, 0) if bound[0] else (
                (0, bound[1] + 1, 0) if bound[1] else (0, 0, bound[2] + 1)
            )
            if not (version_key >= _version_key(raw) and release < upper):
                return False
        elif op == "~":
            upper = (bound[0] + 1, 0, 0) if raw.count(".") == 0 else (bound[0], bound[1] + 1, 0)
            if not (version_key >= _version_key(raw) and release < upper):
                return False
        elif op in {"==", "="} and version_key != _version_key(raw):
            return False
        elif op == ">=" and version_key < _version_key(raw):
            return False
        elif op == "<=" and version_key > _version_key(raw):
            return False
        elif op == ">" and version_key <= _version_key(raw):
            return False
        elif op == "<" and version_key >= _version_key(raw):
            return False
        elif op == "!=" and version_key == _version_key(raw):
            return False
    return True


def _constraints_conflict(constraints: tuple[str, ...]) -> bool:
    """Check conjunction satisfiability using normalized interval bounds."""
    version_key = tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]
    parsed_constraints = tuple(_constraint_terms(constraint) for constraint in constraints)
    terms = tuple(term for parsed in parsed_constraints for term in parsed)
    allow_prerelease = all(
        any("-" in raw for _, raw, _, _ in parsed)
        for parsed in parsed_constraints
    )
    lower: tuple[version_key, bool] | None = (
        ((0, 0, 0, 0, ((0, 0),)) if allow_prerelease else (0, 0, 0, 1, ()), True)
    )
    upper: tuple[version_key, bool] | None = None
    exact: version_key | None = None
    exact_raw: str | None = None
    excluded: set[version_key] = set()

    def key(parsed: tuple[int, int, int, tuple[tuple[int, int | str], ...] | None]) -> version_key:
        return parsed[:3] + (1 if parsed[3] is None else 0, parsed[3] or ())

    def prerelease_floor(release: tuple[int, int, int]) -> version_key:
        return release + (0, ((0, 0),))

    def stable(release: tuple[int, int, int]) -> version_key:
        return release + (1, ())

    def tighten_lower(candidate: version_key, inclusive: bool) -> None:
        nonlocal lower
        if lower is None or candidate > lower[0] or (candidate == lower[0] and not inclusive and lower[1]):
            lower = (candidate, inclusive)

    def tighten_upper(candidate: version_key, inclusive: bool) -> None:
        nonlocal upper
        if upper is None or candidate < upper[0] or (candidate == upper[0] and not inclusive and upper[1]):
            upper = (candidate, inclusive)

    for operator, raw, parsed, wildcard_parts in terms:
        if operator == "any":
            continue
        if wildcard_parts is not None:
            release = [0, 0, 0]
            wildcard_index = len(wildcard_parts) - 1
            for index, part in enumerate(wildcard_parts):
                if part == "x":
                    wildcard_index = index
                    break
                release[index] = int(part)
            if wildcard_index == 0:
                continue
            release_tuple = (release[0], release[1], release[2])
            tighten_lower(
                prerelease_floor(release_tuple)
                if allow_prerelease
                else stable(release_tuple),
                True,
            )
            if wildcard_index == 1:
                next_release = (release[0] + 1, 0, 0)
            else:
                next_release = (release[0], release[1] + 1, 0)
            tighten_upper(
                prerelease_floor(next_release) if allow_prerelease else stable(next_release),
                False,
            )
            continue
        assert parsed is not None
        bound = key(parsed)
        release = parsed[:3]
        if operator in {"==", "="}:
            if exact is not None and exact != bound:
                return True
            exact = bound
            exact_raw = raw
        elif operator == "!=":
            excluded.add(bound)
        elif operator == ">=":
            tighten_lower(bound, True)
        elif operator == ">":
            tighten_lower(bound, False)
        elif operator == "<=":
            tighten_upper(bound, True)
        elif operator == "<":
            tighten_upper(bound, False)
        else:
            tighten_lower(bound, True)
            if operator == "^":
                next_release = (release[0] + 1, 0, 0) if release[0] else (
                    (0, release[1] + 1, 0) if release[1] else (0, 0, release[2] + 1)
                )
            else:
                next_release = (
                    (release[0] + 1, 0, 0)
                    if raw.count(".") == 0
                    else (release[0], release[1] + 1, 0)
                )
            tighten_upper(
                prerelease_floor(next_release) if allow_prerelease else stable(next_release),
                False,
            )

    if lower is not None and upper is not None:
        if lower[0] > upper[0] or (lower[0] == upper[0] and not (lower[1] and upper[1])):
            return True
    if exact is not None:
        if exact_raw is None or not all(satisfies(exact_raw, constraint) for constraint in constraints):
            return True
        return False
    stable_candidate = (0, 0, 0)
    if lower is not None:
        release = lower[0][:3]
        if lower[0][3] == 1 and not lower[1]:
            release = (release[0], release[1], release[2] + 1)
        stable_candidate = release
    for _ in range(len(excluded) + 1):
        candidate_key = stable(stable_candidate)
        if candidate_key not in excluded:
            break
        stable_candidate = (stable_candidate[0], stable_candidate[1], stable_candidate[2] + 1)
    else:
        candidate_key = stable(stable_candidate)
    if upper is None or candidate_key < upper[0] or (candidate_key == upper[0] and upper[1]):
        return False
    return not allow_prerelease




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
                if not isinstance(requirements, Mapping):
                    raise RegistryError("requirements must be an object", "InvalidRequirements")
                if any(not isinstance(name, str) or not isinstance(constraint, str) for name, constraint in requirements.items()):
                    raise RegistryError("invalid requirement", "InvalidRequirements")
                for constraint in requirements.values():
                    _validate_constraint(constraint)
                supplied_roots = tuple(sorted(requirements.items()))
                if supplied_roots != lock.roots:
                    raise RegistryError("lock roots do not match requirements", "LockTampered")
            if not offline:
                self._validate_lock_index(lock)
            self._validate_lock_closure(lock)
            if offline:
                for package in lock.packages:
                    self._verify_file(package, self._cache_path(package))
            return lock
        if requirements is not None and not isinstance(requirements, Mapping):
            raise RegistryError("requirements must be an object", "InvalidRequirements")
        raw_requirements = requirements or {}
        if any(not isinstance(name, str) or not name for name in raw_requirements):
            raise RegistryError("invalid requirement", "InvalidRequirements")
        for constraint in raw_requirements.values():
            _validate_constraint(constraint)
        roots = tuple(sorted(raw_requirements.items()))
        root_names = {name for name, _ in roots}
        if any(not constraint for _, constraint in roots):
            raise RegistryError("invalid requirement", "InvalidRequirements")

        constraints: dict[str, list[str]] = {name: [constraint] for name, constraint in roots}
        selected: dict[str, PackageMetadata] = {}
        # Each frame represents one selected package and the untried candidates
        # for that package.  It is the explicit equivalent of recursive DFS.
        frames: list[dict[str, Any]] = []
        selected_result: dict[str, PackageMetadata] | None = None
        missing_seen = False
        conflict_seen = False
        cycle_seen = False

        def apply(frame: dict[str, Any], package: PackageMetadata) -> None:
            name = frame["name"]
            selected[name] = package
            frame["package"] = package
            for dependency, dependency_constraint in package.dependencies.items():
                constraints.setdefault(dependency, []).append(dependency_constraint)

        def undo(frame: dict[str, Any]) -> None:
            name = frame["name"]
            package = frame["package"]
            selected.pop(name, None)
            for dependency in package.dependencies:
                terms = constraints[dependency]
                terms.pop()
                if not terms:
                    del constraints[dependency]

        def backtrack() -> bool:
            while frames:
                frame = frames[-1]
                undo(frame)
                candidates = frame["candidates"]
                next_candidate = frame["next"]
                if next_candidate < len(candidates):
                    frame["next"] = next_candidate + 1
                    apply(frame, candidates[next_candidate])
                    return True
                frames.pop()
            return False

        while True:
            invalid = next(
                (
                    name
                    for name, package in selected.items()
                    if not all(satisfies(package.version, term) for term in constraints[name])
                ),
                None,
            )
            if invalid is not None:
                invalid_terms = tuple(constraints[invalid])
                if _constraints_conflict(invalid_terms):
                    conflict_seen = True
                else:
                    missing_seen = True
                if not backtrack():
                    break
                continue

            unresolved = sorted(
                (name for name in constraints if name not in selected),
                key=lambda name: (name not in root_names, name),
            )
            if not unresolved:
                candidate_lock = ResolvedLock(tuple(selected[name] for name in sorted(selected)), roots)
                try:
                    self._validate_lock_closure(candidate_lock)
                except RegistryError as exc:
                    if exc.code != "DependencyCycle":
                        raise
                    cycle_seen = True
                    if not backtrack():
                        break
                    continue
                selected_result = selected
                break

            name = unresolved[0]
            terms = tuple(constraints[name])
            available = tuple(package for package in self.index.packages if package.name == name)
            if not available:
                missing_seen = True
                if not backtrack():
                    break
                continue
            candidates = tuple(
                sorted(
                    (
                        package
                        for package in available
                        if all(satisfies(package.version, term) for term in terms)
                    ),
                    key=lambda package: _version_key(package.version),
                    reverse=True,
                )
            )
            if not candidates:
                if _constraints_conflict(terms):
                    conflict_seen = True
                else:
                    missing_seen = True
                if not backtrack():
                    break
                continue
            frame = {"name": name, "candidates": candidates, "next": 1}
            frames.append(frame)
            apply(frame, candidates[0])

        if selected_result is None:
            if cycle_seen:
                raise RegistryError("dependency graph contains a cycle", "DependencyCycle")
            if conflict_seen:
                raise RegistryError("package constraints conflict", "ConflictingConstraint")
            if missing_seen:
                raise RegistryError("dependency graph has no compatible solution", "UnsatisfiedConstraint")
            raise RegistryError("dependency graph has no compatible solution", "UnsatisfiedConstraint")
        result = ResolvedLock(tuple(selected_result[name] for name in sorted(selected_result)), roots)
        self._validate_lock_closure(result)
        if offline:
            for item in result.packages:
                if self.cache_dir is None or not self._cache_path(item).is_file():
                    raise RegistryError(item.name, "OfflineArtifactMissing")
                self._verify_file(item, self._cache_path(item))
        return result

    def _validate_lock_index(self, lock: ResolvedLock) -> None:
        current = {(package.name, package.version): package for package in self.index.packages}
        for package in lock.packages:
            if current.get((package.name, package.version)) != package:
                raise RegistryError(package.name, "LockTampered")

    @staticmethod
    def _validate_lock_closure(lock: ResolvedLock) -> None:
        packages = {package.name: package for package in lock.packages}
        constraints: dict[str, list[str]] = {name: [constraint] for name, constraint in lock.roots}
        for package in lock.packages:
            for name, constraint in package.dependencies.items():
                constraints.setdefault(name, []).append(constraint)
        if set(packages) != set(constraints):
            missing = sorted(set(constraints) - set(packages))
            raise RegistryError(",".join(missing), "LockTampered")
        for name, package in packages.items():
            if not all(satisfies(package.version, constraint) for constraint in constraints[name]):
                raise RegistryError(name, "LockTampered")

        state: dict[str, int] = {}
        for start in sorted(packages):
            if state.get(start, 0) == 2:
                continue
            stack: list[tuple[str, bool]] = [(start, False)]
            while stack:
                name, exiting = stack.pop()
                if exiting:
                    state[name] = 2
                    continue
                current_state = state.get(name, 0)
                if current_state == 1:
                    raise RegistryError(name, "DependencyCycle")
                if current_state == 2:
                    continue
                state[name] = 1
                stack.append((name, True))
                for dependency in reversed(sorted(packages[name].dependencies)):
                    dependency_state = state.get(dependency, 0)
                    if dependency_state == 1:
                        raise RegistryError(dependency, "DependencyCycle")
                    if dependency_state == 0:
                        stack.append((dependency, False))

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

