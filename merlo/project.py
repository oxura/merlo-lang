from __future__ import annotations

import hashlib
import json
import os
import posixpath
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .package import (
    Dependency,
    GitDependencyError,
    LockfileError,
    Package,
    PackageError,
    package_from_root,
    resolve_git_package,
)
from .version import VERSIONS, CompilerVersions

MANIFEST_SCHEMA_VERSION = 1
LOCKFILE_SCHEMA_VERSION = 1


def _string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _manifest_hash(manifest: "ProjectManifest") -> str:
    return hashlib.sha256(_canonical_json(manifest.to_dict()).encode()).hexdigest()


def _relative_source(path: Path, root: Path) -> str:
    return posixpath.normpath(os.path.relpath(path, root).replace(os.sep, "/"))


def _toml_manifest(manifest: "ProjectManifest") -> str:
    lines = [f"manifest = {manifest.schema_version}", "", "[project]"]
    lines.append(f"name = {_string(manifest.name)}")
    lines.append(f"version = {_string(manifest.version)}")
    if manifest.edition:
        lines.append(f"edition = {_string(manifest.edition)}")
    if manifest.dependencies:
        lines.extend(["", "[dependencies]"])
        for name, dependency in sorted(manifest.dependencies.items()):
            if dependency.kind == "path":
                assert dependency.path is not None
                if dependency.version is None:
                    lines.append(f"{name} = {_string(dependency.path.replace(chr(92), '/'))}")
                else:
                    lines.append(
                        f"{name} = {{ path = {_string(dependency.path.replace(chr(92), '/'))}, "
                        f"version = {_string(dependency.version)} }}"
                    )
            else:
                assert dependency.git is not None and dependency.rev is not None
                fields = [f"git = {_string(dependency.git)}", f"rev = {_string(dependency.rev.lower())}"]
                if dependency.version is not None:
                    fields.append(f"version = {_string(dependency.version)}")
                lines.append(f"{name} = {{ {', '.join(fields)} }}")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ProjectManifest:
    name: str
    version: str = "0.1.0"
    edition: str = "alpha.1"
    dependencies: Mapping[str, Dependency] = field(default_factory=dict)
    schema_version: int = MANIFEST_SCHEMA_VERSION

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProjectManifest":
        schema = raw.get("manifest", raw.get("manifest_schema", MANIFEST_SCHEMA_VERSION))
        if schema != MANIFEST_SCHEMA_VERSION:
            raise LockfileError(
                f"manifest schema {schema} is unsupported",
                code="ManifestCompatibilityMismatch",
            )
        project = raw.get("project", raw.get("package", {}))
        if not isinstance(project, Mapping):
            raise PackageError("project must be a table", code="InvalidManifest")
        name = project.get("name")
        if not isinstance(name, str) or not name:
            raise PackageError("project.name is required", code="InvalidManifest")
        version = project.get("version", "0.1.0")
        edition = project.get("edition", "alpha.1")
        dependencies_raw = raw.get("dependencies", {})
        if not isinstance(dependencies_raw, Mapping):
            raise PackageError("dependencies must be a table", code="InvalidManifest")
        dependencies = {
            dep_name: Dependency.from_value(dep_name, value)
            for dep_name, value in sorted(dependencies_raw.items())
        }
        return cls(
            name=name,
            version=str(version),
            edition=str(edition),
            dependencies=dependencies,
            schema_version=int(schema),
        )

    def to_dict(self) -> dict[str, Any]:
        dependencies: dict[str, Any] = {}
        for name, dependency in sorted(self.dependencies.items()):
            data = dependency.to_dict()
            data.pop("name", None)
            data.pop("kind", None)
            dependencies[name] = data
        return {
            "manifest": self.schema_version,
            "project": {
                "name": self.name,
                "version": self.version,
                "edition": self.edition,
            },
            "dependencies": dependencies,
        }

    def to_toml(self) -> str:
        return _toml_manifest(self)

    def digest(self) -> str:
        return _manifest_hash(self)


@dataclass(frozen=True)
class MerloLock:
    packages: tuple[Mapping[str, Any], ...]
    graph: Mapping[str, tuple[str, ...]]
    manifest_hash: str
    compatibility: Mapping[str, Any] = field(default_factory=lambda: VERSIONS.to_dict())
    schema_version: int = LOCKFILE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "lockfile": self.schema_version,
            "compatibility": dict(sorted(self.compatibility.items())),
            "manifest_hash": self.manifest_hash,
            "packages": [dict(sorted(package.items())) for package in self.packages],
            "graph": {
                name: list(self.graph[name]) for name in sorted(self.graph)
            },
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict()) + "\n"

    def to_toml(self) -> str:
        # TOML is supplied for tooling that prefers it; JSON remains the
        # canonical on-disk representation because nested graph records are
        # unambiguous and byte-stable with the standard library.
        return _toml_lock(self.to_dict())

    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MerloLock":
        schema = raw.get("lockfile", raw.get("schema_version"))
        if schema != LOCKFILE_SCHEMA_VERSION:
            raise LockfileError(
                f"lockfile schema {schema} is unsupported",
                code="LockCompatibilityMismatch",
            )
        compatibility = raw.get("compatibility")
        if not isinstance(compatibility, Mapping):
            raise LockfileError("compatibility contract is missing", code="LockCompatibilityMismatch")
        expected = VERSIONS.to_dict()
        for key in ("language", "frontend", "canonical", "hir", "rir", "mir", "runtime_abi", "manifest", "lockfile"):
            if compatibility.get(key) != expected[key]:
                raise LockfileError(
                    f"compatibility.{key} does not match this compiler",
                    code="LockCompatibilityMismatch",
                )
        packages_raw = raw.get("packages", [])
        graph_raw = raw.get("graph", {})
        if not isinstance(packages_raw, list) or not isinstance(graph_raw, Mapping):
            raise LockfileError("packages and graph must be deterministic collections", code="InvalidLockfile")
        packages = tuple(dict(item) for item in packages_raw if isinstance(item, Mapping))
        graph = {
            str(name): tuple(sorted(str(dep) for dep in deps))
            for name, deps in graph_raw.items()
            if isinstance(deps, (list, tuple))
        }
        manifest_hash = raw.get("manifest_hash")
        if not isinstance(manifest_hash, str):
            raise LockfileError("manifest_hash is missing", code="InvalidLockfile")
        return cls(
            packages=packages,
            graph=graph,
            manifest_hash=manifest_hash,
            compatibility=dict(compatibility),
            schema_version=int(schema),
        )

    @classmethod
    def read(cls, path: str | Path) -> "MerloLock":
        lock_path = Path(path)
        try:
            text = lock_path.read_text(encoding="utf-8")
            if text.lstrip().startswith("{"):
                raw = json.loads(text)
            else:
                raw = tomllib.loads(text)
        except (OSError, ValueError) as exc:
            raise LockfileError(str(lock_path), code="InvalidLockfile") from exc
        if not isinstance(raw, Mapping):
            raise LockfileError(str(lock_path), code="InvalidLockfile")
        return cls.from_dict(raw)

    def write(self, path: str | Path, *, format: str | None = None) -> None:
        lock_path = Path(path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        selected = format or "json"
        if selected == "json":
            text = self.to_json()
        elif selected == "toml":
            text = self.to_toml()
        else:
            raise LockfileError(f"unknown lock format {selected}", code="InvalidLockfile")
        lock_path.write_text(text, encoding="utf-8")


def _toml_lock(raw: Mapping[str, Any]) -> str:
    lines = [f"lockfile = {raw['lockfile']}", f"manifest_hash = {_string(raw['manifest_hash'])}", ""]
    lines.append("[compatibility]")
    for key, value in raw["compatibility"].items():
        lines.append(f"{key} = {_string(value) if isinstance(value, str) else value}")
    lines.extend(["", "[graph]"])
    for name, dependencies in raw["graph"].items():
        lines.append(f"{name} = {json.dumps(dependencies, ensure_ascii=False)}")
    for package in raw["packages"]:
        lines.extend(["", "[[packages]]"])
        for key, value in package.items():
            if isinstance(value, Mapping):
                fields = ", ".join(f"{k} = {_string(v)}" for k, v in value.items())
                lines.append(f"{key} = {{ {fields} }}")
            elif isinstance(value, list):
                lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
            else:
                lines.append(f"{key} = {_string(value) if isinstance(value, str) else value}")
    return "\n".join(lines) + "\n"


def _resolve_package_graph(
    root: Path,
    *,
    root_source_hash: str,
) -> tuple[tuple[Mapping[str, Any], ...], dict[str, tuple[str, ...]]]:
    packages: dict[str, Package] = {}
    graph: dict[str, tuple[str, ...]] = {}
    visiting: set[str] = set()

    def visit(package: Package) -> None:
        if package.name in visiting:
            raise PackageError(package.name, code="DependencyCycle")
        if package.name in packages:
            if packages[package.name].source_hash != package.source_hash:
                raise PackageError(package.name, code="DuplicatePackage")
            return
        visiting.add(package.name)
        child_names: list[str] = []
        for dep_name, dep in sorted(package.dependencies.items()):
            if dep.path is not None:
                child_root = (package.root / dep.path).resolve()
                child = package_from_root(child_root, name=dep_name)
                source = {"kind": "path", "path": _relative_source(child_root, root)}
                child = Package(
                    name=child.name,
                    root=child.root,
                    version=child.version,
                    dependencies=child.dependencies,
                    source_kind="path",
                    source=source,
                    source_hash=child.source_hash,
                )
            else:
                child = resolve_git_package(dep)
            child_names.append(child.name)
            visit(child)
        visiting.remove(package.name)
        packages[package.name] = package
        graph[package.name] = tuple(sorted(child_names))

    root_package = package_from_root(root)
    root_package = Package(
        name=root_package.name,
        root=root_package.root,
        version=root_package.version,
        dependencies=root_package.dependencies,
        source_kind="path",
        source={"kind": "path", "path": "."},
        source_hash=root_source_hash,
    )
    visit(root_package)
    records = tuple(
        packages[name].to_lock_record(dependency_names=graph[name])
        for name in sorted(packages)
    )
    return records, {name: graph[name] for name in sorted(graph)}


def resolve_dependencies(project: "Project | str | Path", *, write: bool = True) -> MerloLock:
    instance = project if isinstance(project, Project) else Project.load(project)
    manifest_hash = instance.manifest.digest()
    packages, graph = _resolve_package_graph(
        instance.root,
        root_source_hash=manifest_hash,
    )
    lock = MerloLock(
        packages=packages,
        graph=graph,
        manifest_hash=manifest_hash,
    )
    if write:
        lock.write(instance.lock_path)
    return lock


@dataclass(frozen=True)
class Project:
    root: Path
    manifest: ProjectManifest

    @property
    def manifest_path(self) -> Path:
        return self.root / "merlo.toml"

    @property
    def lock_path(self) -> Path:
        return self.root / "merlo.lock"

    @property
    def source_dir(self) -> Path:
        return self.root / "src"

    @property
    def tests_dir(self) -> Path:
        return self.root / "tests"

    @classmethod
    def create(
        cls,
        root: str | Path,
        *,
        name: str | None = None,
        version: str = "0.1.0",
    ) -> "Project":
        root_path = Path(root).resolve()
        project_name = name or root_path.name.replace("-", "_") or "app"
        manifest = ProjectManifest(name=project_name, version=version)
        root_path.mkdir(parents=True, exist_ok=True)
        (root_path / "src").mkdir(exist_ok=True)
        (root_path / "tests").mkdir(exist_ok=True)
        (root_path / "src" / "main.mlo").write_text(
            "module main\n\n"
            "export enum AppError:\n"
            "    Failed\n\n"
            "export main(path: Path) -> Result[Text, AppError]:\n"
            "    console.write(\"ok\")\n"
            "    Ok(\"ok\")\n",
            encoding="utf-8",
        )
        (root_path / "tests" / ".gitkeep").write_text("", encoding="utf-8")
        instance = cls(root=root_path, manifest=manifest)
        instance.write_manifest()
        resolve_dependencies(instance)
        return instance

    @classmethod
    def discover(cls, path: str | Path = ".") -> "Project | None":
        candidate = Path(path).resolve()
        if candidate.is_file():
            candidate = candidate.parent
        while candidate != candidate.parent and not (candidate / "merlo.toml").is_file():
            candidate = candidate.parent
        manifest_path = candidate / "merlo.toml"
        if not manifest_path.is_file():
            return None
        try:
            raw = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PackageError(str(manifest_path), code="InvalidManifest") from exc
        return cls(root=candidate, manifest=ProjectManifest.from_dict(raw))

    @classmethod
    def load(cls, path: str | Path = ".") -> "Project":
        project = cls.discover(path)
        if project is not None:
            return project
        candidate = Path(path).resolve()
        if candidate.is_file():
            candidate = candidate.parent
        while candidate != candidate.parent:
            candidate = candidate.parent
        raise PackageError(str(candidate / "merlo.toml"), code="ManifestNotFound")

    def write_manifest(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(self.manifest.to_toml(), encoding="utf-8")

    def add_dependency(
        self,
        name: str,
        *,
        path: str | None = None,
        git: str | None = None,
        rev: str | None = None,
        version: str | None = None,
    ) -> Dependency:
        dependency = Dependency(name=name, path=path, git=git, rev=rev, version=version)
        dependencies = dict(self.manifest.dependencies)
        dependencies[name] = dependency
        object.__setattr__(self, "manifest", ProjectManifest(
            name=self.manifest.name,
            version=self.manifest.version,
            edition=self.manifest.edition,
            dependencies=dependencies,
        ))
        self.write_manifest()
        resolve_dependencies(self)
        return dependency

    def add_path(self, name: str, path: str, *, version: str | None = None) -> Dependency:
        return self.add_dependency(name, path=path, version=version)

    def add_git(self, name: str, git: str, rev: str, *, version: str | None = None) -> Dependency:
        return self.add_dependency(name, git=git, rev=rev, version=version)

    def lock(self, *, require_fresh: bool = True) -> MerloLock:
        if not self.lock_path.is_file():
            raise LockfileError(str(self.lock_path), code="LockfileMissing")
        lock = MerloLock.read(self.lock_path)
        if require_fresh:
            if lock.manifest_hash != self.manifest.digest():
                raise LockfileError(str(self.lock_path), code="StaleLockfile")
            expected = resolve_dependencies(self, write=False)
            if expected.to_json() != lock.to_json():
                raise LockfileError(str(self.lock_path), code="StaleLockfile")
        return lock


__all__ = [
    "LOCKFILE_SCHEMA_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "MerloLock",
    "Project",
    "ProjectManifest",
    "resolve_dependencies",
]
