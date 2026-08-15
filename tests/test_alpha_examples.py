from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from merlo import concise_services
from merlo.compiler import compile_project
from merlo.project import Project
from merlo.semantic_world import SemanticWorld
from merlo.test_runner import run_project_tests

ROOT = Path(__file__).parents[1]
EXAMPLES = ROOT / "examples"
EXAMPLE_NAMES = (
    "automation",
    "json-cli",
    "ndjson",
    "csv",
    "grep",
    "network",
    "ffi",
    "capacity-ledger",
    "packages",
    "invoice-report",
    "access-log",
    "byte-stats",
    "inventory",
    "task-board",
    "tree-walk",
)

_INPUTS = {
    "automation": "input.txt",
    "json-cli": "input.json",
    "ndjson": "input.ndjson",
    "csv": "input.csv",
    "grep": "input.txt",
    "network": "input.txt",
    "ffi": "input.txt",
    "packages": "input.txt",
    "capacity-ledger": "input.txt",
    "invoice-report": "input.txt",
    "access-log": "input.log",
    "byte-stats": "input.bin",
    "inventory": "input.txt",
    "task-board": "input.txt",
    "tree-walk": "input.txt",
}


def _project(name: str) -> Path:
    return EXAMPLES / name


def _sources(project: Path) -> tuple[Path, ...]:
    return tuple(sorted(project.rglob("*.mlo")))


def test_exactly_fifteen_independent_projects_have_complete_contract() -> None:
    names = tuple(sorted(path.name for path in EXAMPLES.iterdir() if path.is_dir()))
    assert names == tuple(sorted(EXAMPLE_NAMES))
    for name in EXAMPLE_NAMES:
        project = _project(name)
        assert (project / "merlo.toml").is_file()
        assert (project / "merlo.lock").is_file()
        assert (project / "src" / "main.mlo").is_file()
        assert (project / "tests").is_dir()
        assert tuple(project.glob("expected.*"))
        assert _sources(project)


def test_manifests_locks_imports_and_business_source_audit() -> None:
    forbidden = re.compile(r"(?:malloc|free|fclose|system|popen|subprocess|os\.system)")
    opaque = re.compile(r"\b(?:ndjson|csv|grep)_(?:parse|analyze|aggregate|search)\b")
    for name in EXAMPLE_NAMES:
        project = _project(name)
        manifest = (project / "merlo.toml").read_text(encoding="utf-8")
        lock = (project / "merlo.lock").read_text(encoding="utf-8")
        assert "manifest = 1" in manifest
        assert 'edition = "alpha.1"' in manifest
        assert '"compatibility"' in lock
        assert '"lockfile":1' in lock
        source = "\n".join(path.read_text(encoding="utf-8") for path in _sources(project))
        assert not forbidden.search(source)
        assert not opaque.search(source)
        assert "python" not in source.casefold()
    assert 'use app.json' in (_project("json-cli") / "src/main.mlo").read_text(encoding="utf-8")
    assert 'use report' in (_project("ndjson") / "src/main.mlo").read_text(encoding="utf-8")
    assert 'use sales' in (_project("csv") / "src/main.mlo").read_text(encoding="utf-8")
    assert 'use search' in (_project("grep") / "src/main.mlo").read_text(encoding="utf-8")
    assert 'use std.net' in (_project("network") / "src/main.mlo").read_text(encoding="utf-8")
    assert 'use std.fs' in (_project("ffi") / "src/main.mlo").read_text(encoding="utf-8")
    packages_manifest = (_project("packages") / "merlo.toml").read_text(encoding="utf-8")
    assert 'greeting = { path = "vendor/greeting"' in packages_manifest
    assert 'use greeting' in (_project("packages") / "src/main.mlo").read_text(encoding="utf-8")
    assert 'use billing' in (_project("invoice-report") / "src/main.mlo").read_text(encoding="utf-8")
    assert 'use analytics' in (_project("access-log") / "src/main.mlo").read_text(encoding="utf-8")
    assert 'use metrics' in (_project("byte-stats") / "src/main.mlo").read_text(encoding="utf-8")
    assert 'use catalog' in (_project("inventory") / "src/main.mlo").read_text(encoding="utf-8")
    assert 'use board' in (_project("task-board") / "src/main.mlo").read_text(encoding="utf-8")
    assert 'use tree' in (_project("tree-walk") / "src/main.mlo").read_text(encoding="utf-8")


def test_project_compile_reuses_the_validated_module_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def duplicate_loader(_entry: Path) -> tuple[object, ...]:
        raise AssertionError("compile_project traversed the module graph twice")

    monkeypatch.setattr(concise_services, "_load_modules", duplicate_loader)
    compilation = compile_project(
        _project("automation"),
        require_interface_lock=False,
    )
    assert compilation.module_graph.modules


@pytest.mark.parametrize("name", EXAMPLE_NAMES)
def test_each_example_uses_production_compile_world_index_and_inspect(name: str, tmp_path: Path) -> None:
    project = Project.load(_project(name))
    compilation = compile_project(project.root, require_interface_lock=False)
    assert compilation.native is None
    world = SemanticWorld.build(
        compilation,
        state_path=project.root / ".merlo" / "world.json",
        lockfile=project.lock_path,
        require_interface_lock=False,
    )
    world.save()
    assert world.map("json")["modules"]
    inspected = world.inspect("main.main")
    assert inspected["symbol"]["qualified_name"] == "main.main"
    assert world.search("main")
    assert world.dependencies("main.main")
    assert world.map("dot").startswith("digraph semantic_world")
    native = compile_project(
        project.root,
        emit_native=True,
        release=True,
        output=tmp_path / name,
        require_interface_lock=False,
    )
    assert native.native is not None
    assert native.native.binary_path is not None
    assert Path(native.native.binary_path).is_file()


def test_project_tests_execute_through_native_test_runner() -> None:
    for name in EXAMPLE_NAMES:
        report = run_project_tests(Project.load(_project(name)))
        assert report.failed == 0, report.to_dict()
        assert report.tests


def test_expected_outputs_and_python_free_native_rerun(tmp_path: Path) -> None:
    clean_entries = []
    for item in os.environ.get("PATH", "").split(os.pathsep):
        if not item:
            continue
        if any(shutil.which(candidate, path=item) for candidate in ("python", "python3", "python3.14")):
            continue
        clean_entries.append(item)
    clean_path = os.pathsep.join(clean_entries)
    assert all(shutil.which(candidate, path=clean_path) is None for candidate in ("python", "python3", "python3.14"))
    for name in EXAMPLE_NAMES:
        project = Project.load(_project(name))
        build = compile_project(
            project.root,
            emit_native=True,
            release=True,
            output=tmp_path / name,
            require_interface_lock=False,
        )
        assert build.native is not None and build.native.binary_path is not None
        expected = (project.root / "expected.txt").read_text(encoding="utf-8")
        arguments = [str(project.root / _INPUTS[name])]
        completed = subprocess.run(
            [build.native.binary_path, *arguments],
            check=False,
            capture_output=True,
            text=True,
            env={"PATH": clean_path},
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout == expected
        assert completed.stderr == ""


def test_capacity_ledger_rejects_invalid_records_natively(tmp_path: Path) -> None:
    clean_entries = []
    for item in os.environ.get("PATH", "").split(os.pathsep):
        if not item:
            continue
        if any(shutil.which(candidate, path=item) for candidate in ("python", "python3", "python3.14")):
            continue
        clean_entries.append(item)
    clean_path = os.pathsep.join(clean_entries)
    project = Project.load(_project("capacity-ledger"))
    build = compile_project(
        project.root,
        emit_native=True,
        release=True,
        output=tmp_path / "capacity-ledger",
        require_interface_lock=False,
    )
    assert build.native is not None and build.native.binary_path is not None
    fixtures = (
        "malformed_record.txt",
        "invalid_lane.txt",
        "invalid_minutes.txt",
        "total_overflow.txt",
    )
    for fixture in fixtures:
        completed = subprocess.run(
            [build.native.binary_path, str(project.root / "tests" / "fixtures" / fixture)],
            check=False,
            capture_output=True,
            text=True,
            env={"PATH": clean_path},
        )
        assert completed.returncode == 74
        assert completed.stdout == ""
