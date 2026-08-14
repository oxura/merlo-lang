from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

PACKAGE_SCHEMA_VERSION = 1
_FULL_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")


class PackageError(ValueError):
    """Stable error raised while resolving a Merlo package."""

    code = "PackageError"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        self.code = code or self.code
        super().__init__(f"{self.code}: {message}")


class DependencySpecificationError(PackageError):
    code = "InvalidDependency"


class GitDependencyError(PackageError):
    code = "GitDependencyUnavailable"


class LockfileError(PackageError):
    code = "LockfileError"


@dataclass(frozen=True)
class Dependency:
    """A dependency as written in a project manifest."""

    name: str
    path: str | None = None
    git: str | None = None
    rev: str | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        if bool(self.path) == bool(self.git):
            raise DependencySpecificationError(
                f"{self.name}: exactly one of path or git is required"
            )
        if self.git is not None and (
            not self.rev or not _FULL_COMMIT.fullmatch(self.rev)
        ):
            raise DependencySpecificationError(
                f"{self.name}: git dependencies require a full 40-character rev"
            )

    @classmethod
    def from_value(cls, name: str, value: str | Mapping[str, Any]) -> "Dependency":
        if isinstance(value, str):
            return cls(name=name, path=value)
        if not isinstance(value, Mapping):
            raise DependencySpecificationError(
                f"{name}: dependency must be a string or table"
            )
        path = value.get("path")
        git = value.get("git")
        rev = value.get("rev")
        version = value.get("version")
        if path is not None and not isinstance(path, str):
            raise DependencySpecificationError(f"{name}: path must be text")
        if git is not None and not isinstance(git, str):
            raise DependencySpecificationError(f"{name}: git must be text")
        if rev is not None and not isinstance(rev, str):
            raise DependencySpecificationError(f"{name}: rev must be text")
        return cls(name=name, path=path, git=git, rev=rev, version=version)

    @property
    def kind(self) -> str:
        return "path" if self.path is not None else "git"

    def to_dict(self) -> dict[str, str]:
        result: dict[str, str] = {"name": self.name, "kind": self.kind}
        if self.path is not None:
            result["path"] = self.path.replace("\\", "/")
        else:
            assert self.git is not None and self.rev is not None
            result["git"] = self.git
            result["rev"] = self.rev.lower()
        if self.version is not None:
            result["version"] = self.version
        return result

    @property
    def canonical_source(self) -> dict[str, str]:
        if self.path is not None:
            return {"kind": "path", "path": self.path.replace("\\", "/")}
        assert self.git is not None and self.rev is not None
        return {"kind": "git", "git": self.git, "rev": self.rev.lower()}


@dataclass(frozen=True)
class Package:
    name: str
    root: Path
    version: str = "0.1.0"
    dependencies: Mapping[str, Dependency] = field(default_factory=dict)
    source_kind: str = "path"
    source: Mapping[str, str] = field(default_factory=dict)
    source_hash: str = ""

    def canonical_files(self) -> tuple[tuple[str, str], ...]:
        """Return package source and manifest hashes in stable path order."""
        if not self.root.is_dir():
            raise PackageError(str(self.root), code="PackageNotFound")
        ignored = {".git", ".merlo", "__pycache__", "target", "build"}
        rows: list[tuple[str, str]] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or any(part in ignored for part in path.parts):
                continue
            relative = path.relative_to(self.root).as_posix()
            if relative != "merlo.toml" and path.suffix != ".mlo":
                continue
            rows.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
        return tuple(rows)

    def content_hash(self) -> str:
        payload = {
            "package_schema": PACKAGE_SCHEMA_VERSION,
            "name": self.name,
            "version": self.version,
            "files": self.canonical_files(),
            "dependencies": tuple(
                dep.to_dict() for _, dep in sorted(self.dependencies.items())
            ),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def to_lock_record(self, *, dependency_names: Iterable[str] = ()) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "source": dict(sorted(self.source.items())),
            "source_hash": self.source_hash or self.content_hash(),
            "dependencies": sorted(set(dependency_names)),
        }


def _git_root(url: str) -> Path | None:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return Path(parsed.path)
    if parsed.scheme:
        return None
    candidate = Path(url).expanduser()
    return candidate if candidate.exists() else None


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args],
            stderr=subprocess.PIPE,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GitDependencyError(str(repo), code="GitCommitUnavailable") from exc


def verify_git_commit(dep: Dependency) -> Path:
    if dep.git is None or dep.rev is None:
        raise GitDependencyError(f"{dep.name}: git source is incomplete")
    root = _git_root(dep.git)
    if root is None or not root.is_dir():
        raise GitDependencyError(
            f"{dep.name}: network resolution is disabled; use a local repository"
        )
    resolved = _git(root, "rev-parse", "--verify", f"{dep.rev}^{{commit}}")
    if resolved.lower() != dep.rev.lower():
        raise GitDependencyError(
            f"{dep.name}: rev {dep.rev} is not the exact repository commit"
        )
    return root


def package_from_root(root: str | Path, *, name: str | None = None) -> Package:
    """Load a package without importing a registry or contacting the network."""
    root_path = Path(root).resolve()
    manifest_path = root_path / "merlo.toml"
    if not manifest_path.is_file():
        raise PackageError(str(manifest_path), code="ManifestNotFound")
    try:
        import tomllib

        raw = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackageError(str(manifest_path), code="InvalidManifest") from exc
    project = raw.get("project", raw.get("package", {}))
    if not isinstance(project, Mapping):
        raise PackageError("project table must be a table", code="InvalidManifest")
    package_name = name or project.get("name")
    if not isinstance(package_name, str) or not package_name:
        raise PackageError("project.name is required", code="InvalidManifest")
    version = project.get("version", "0.1.0")
    raw_dependencies = raw.get("dependencies", {})
    if not isinstance(raw_dependencies, Mapping):
        raise PackageError("dependencies must be a table", code="InvalidManifest")
    dependencies = {
        dep_name: Dependency.from_value(dep_name, value)
        for dep_name, value in sorted(raw_dependencies.items())
    }
    package = Package(
        name=package_name,
        root=root_path,
        version=str(version),
        dependencies=dependencies,
        source_kind="path",
        source={"kind": "path", "path": root_path.as_posix()},
    )
    return Package(
        name=package.name,
        root=package.root,
        version=package.version,
        dependencies=package.dependencies,
        source_kind=package.source_kind,
        source=package.source,
        source_hash=package.content_hash(),
    )


def resolve_git_package(dep: Dependency) -> Package:
    root = verify_git_commit(dep)
    package = package_from_root(root, name=dep.name)
    source = {"kind": "git", "git": dep.git or "", "rev": (dep.rev or "").lower()}
    return Package(
        name=package.name,
        root=package.root,
        version=package.version,
        dependencies=package.dependencies,
        source_kind="git",
        source=source,
        source_hash=package.source_hash,
    )


__all__ = [
    "PACKAGE_SCHEMA_VERSION",
    "Dependency",
    "DependencySpecificationError",
    "GitDependencyError",
    "LockfileError",
    "Package",
    "PackageError",
    "package_from_root",
    "resolve_git_package",
    "verify_git_commit",
]
