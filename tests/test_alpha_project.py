from __future__ import annotations

import json
from pathlib import Path

import pytest

from merlo.cache import ContentCache
from merlo.package import DependencySpecificationError, GitDependencyError
from merlo.project import LockfileError, MerloLock, Project, resolve_dependencies
from merlo.version import VERSIONS
from merlo.surface_ast import SurfaceExpressionStatement, SurfaceFunction
from merlo.surface_parser import parse_surface


def test_clean_project_has_deterministic_manifest_and_lock(tmp_path: Path) -> None:
    first = Project.create(tmp_path / "app", name="app")
    manifest_once = first.manifest_path.read_bytes()
    lock_once = first.lock_path.read_bytes()
    second = Project.load(first.root)
    resolve_dependencies(second)

    assert manifest_once == first.manifest_path.read_bytes()
    assert lock_once == first.lock_path.read_bytes()
    assert first.manifest.name == "app"
    assert first.lock().compatibility["lockfile"] == VERSIONS.lockfile
    assert (first.source_dir / "main.mlo").is_file()


def test_new_project_scaffold_uses_surface_zero_two(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "app", name="app")
    program = parse_surface(
        (project.source_dir / "main.mlo").read_text(encoding="utf-8"),
        path=str(project.source_dir / "main.mlo"),
    )
    main = next(
        declaration
        for declaration in program.declarations
        if isinstance(declaration, SurfaceFunction) and declaration.name == "main"
    )

    assert main.declared_kind is None
    assert main.return_type == "Result[Text,AppError]"
    assert isinstance(main.body, tuple)
    assert isinstance(main.body[-1], SurfaceExpressionStatement)


def test_local_path_dependency_is_locked_by_source_hash_and_graph(tmp_path: Path) -> None:
    library = Project.create(tmp_path / "library", name="library")
    app = Project.create(tmp_path / "app", name="app")
    app.add_path("library", "../library")

    lock = app.lock()
    records = {record["name"]: record for record in lock.packages}
    assert lock.graph["app"] == ("library",)
    assert records["library"]["source"]["path"] == "../library"
    assert len(records["library"]["source_hash"]) == 64
    assert library.manifest.digest() != app.manifest.digest()


def test_git_dependencies_require_exact_commits_and_never_network_fallback(tmp_path: Path) -> None:
    with pytest.raises(DependencySpecificationError, match="full 40-character rev"):
        Project.create(tmp_path / "app", name="app").add_git(
            "library", "https://example.invalid/library", "main"
        )

    app = Project.create(tmp_path / "app2", name="app2")
    with pytest.raises(GitDependencyError, match="network resolution is disabled"):
        app.add_git("library", "https://example.invalid/library", "0" * 40)


def test_lock_rejects_stale_manifest_and_incompatible_schema(tmp_path: Path) -> None:
    app = Project.create(tmp_path / "app", name="app")
    app.manifest_path.write_text(
        app.manifest_path.read_text(encoding="utf-8").replace('version = "0.1.0"', 'version = "0.1.1"'),
        encoding="utf-8",
    )
    stale = Project.load(app.root)
    with pytest.raises(LockfileError, match="StaleLockfile"):
        stale.lock()

    raw = json.loads(app.lock_path.read_text(encoding="utf-8"))
    raw["lockfile"] = 99
    app.lock_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(LockfileError, match="LockCompatibilityMismatch"):
        MerloLock.read(app.lock_path)


def test_cache_keys_are_independent_and_targeted_invalidation_preserves_unrelated_entries(tmp_path: Path) -> None:
    cache = ContentCache(tmp_path / "cache")
    function = cache.put("mir", "function-a", source_hashes=("source-a",), functions=("f",), modules=("m",))
    module = cache.put("hir", "module-a", source_hashes=("source-a",), modules=("m",))
    unrelated = cache.put("hir", "module-b", source_hashes=("source-b",), modules=("other",))
    world = cache.put("semantic_world", b"world", source_hashes=("source-a",), modules=("m",))

    removed = cache.invalidate_function("f")
    assert function.key in removed
    assert cache.get("mir", function.key) is None
    assert cache.get("hir", module.key) is not None
    assert cache.get("hir", unrelated.key) is not None
    assert cache.get("semantic_world", world.key) is not None

    removed_world = cache.invalidate_semantic_world(source_hashes=("source-a",))
    assert world.key in removed_world
    assert cache.get("semantic_world", world.key) is None


def test_cache_timing_report_has_required_edit_and_link_metrics(tmp_path: Path) -> None:
    cache = ContentCache(tmp_path / "cache")
    for metric in ("cold", "warm", "single_function_edit", "module_edit", "link", "semantic_map"):
        cache.record_timing(metric, 0.001)
    report = cache.timing_report()
    assert report.metrics == (
        "cold",
        "warm",
        "single_function_edit",
        "module_edit",
        "link",
        "semantic_map",
    )


def test_compile_project_rejects_stale_project_lock(tmp_path: Path) -> None:
    from merlo.compiler import compile_project
    from merlo.concise_application import ConciseApplicationError

    project = Project.create(tmp_path / "app", name="app")
    project.manifest_path.write_text(
        project.manifest_path.read_text(encoding="utf-8").replace(
            'version = "0.1.0"', 'version = "0.1.1"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConciseApplicationError, match="StaleLockfile"):
        compile_project(project.root, require_interface_lock=False)


def test_src_project_imports_resolve_below_src(tmp_path: Path) -> None:
    from merlo.modules import ModuleGraph

    root = tmp_path / "app"
    (root / "src" / "foo").mkdir(parents=True)
    (root / "src" / "main.mlo").write_text(
        "module app.main\n\nuse foo.bar\n\n"
        "export task main() -> Int:\n    return 0\n",
        encoding="utf-8",
    )
    (root / "src" / "foo" / "bar.mlo").write_text(
        "module foo.bar\n\nexport fn value() -> Int:\n    return 1\n",
        encoding="utf-8",
    )

    graph = ModuleGraph.load(root / "src" / "main.mlo")
    assert tuple(module.name for module in graph.modules) == ("foo.bar", "app.main")
