from __future__ import annotations

from pathlib import Path

from research.archive.historical_protocol.merlo import EditCapability, SoftwareWorld


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _move_workspace(root: Path) -> None:
    _write(root, "pkg/__init__.py", "from .source import helper\n")
    _write(
        root,
        "pkg/source.py",
        (
            "# module comment stays\n\n"
            "def helper( value ):\n"
            "    # body comment and quote style must survive\n"
            "    return 'value:' + value\n\n"
            "def local_caller():\n"
            "    return helper('local')\n"
        ),
    )
    _write(root, "pkg/target.py", "TARGET = 'kept'\n")
    _write(
        root,
        "consumer.py",
        (
            "from pkg.source import (\n"
            "    helper as stable_helper,\n"
            ")\n\n"
            "def run():\n"
            "    return stable_helper('external')\n"
        ),
    )


def _capability(target_id: str, **kwargs):
    return EditCapability.move(
        target_id,
        allow_new_dependencies=True,
        allow_public_api_break=True,
        **kwargs,
    )


def test_move_updates_relative_reexport_alias_and_local_caller(tmp_path: Path):
    _move_workspace(tmp_path)
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.source.helper")
    before_revision = target.revision_hash
    capability = _capability(target.id)

    plan = world.plan_move(target.id, "pkg.target", capability)

    assert plan.ready, [item.to_dict() for item in plan.obligations]
    assert plan.change.operation == "move_symbol"
    assert set(plan.affected_files) == {
        "consumer.py",
        "pkg/__init__.py",
        "pkg/source.py",
        "pkg/target.py",
    }
    world.apply(plan, capability)

    moved = world.program.entity(target.id)
    assert moved.fqname == "pkg.target.helper"
    assert moved.identity_status == "Exact"
    assert moved.revision_hash != before_revision
    target_source = (tmp_path / "pkg" / "target.py").read_text(encoding="utf-8")
    source = (tmp_path / "pkg" / "source.py").read_text(encoding="utf-8")
    consumer = (tmp_path / "consumer.py").read_text(encoding="utf-8")
    package = (tmp_path / "pkg" / "__init__.py").read_text(encoding="utf-8")
    assert "TARGET = 'kept'" in target_source
    assert "def helper( value ):" in target_source
    assert "# body comment and quote style must survive" in target_source
    assert "return 'value:' + value" in target_source
    assert "def helper" not in source
    assert "from pkg.target import helper" in source
    assert "from pkg.target import (" in consumer
    assert "helper as stable_helper" in consumer
    assert "from pkg.target import helper" in package


def test_move_can_create_module_in_existing_package(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/source.py", "def helper():\n    return 1\n")
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.source.helper")
    capability = _capability(target.id)

    plan = world.plan_move(target.id, "pkg.created", capability)
    assert plan.ready
    assert any(item.allow_create for item in plan.edits)

    world.apply(plan, capability)

    assert (tmp_path / "pkg" / "created.py").exists()
    assert world.program.entity(target.id).module == "pkg.created"


def test_move_target_collision_is_blocking(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/source.py", "def helper():\n    return 1\n")
    _write(tmp_path, "pkg/target.py", "def helper():\n    return 2\n")
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.source.helper")

    plan = world.plan_move(target.id, "pkg.target", _capability(target.id))

    assert not plan.ready
    assert "TargetCollision" in {item.kind for item in plan.obligations}



def test_move_blocks_dependency_name_collision_in_target_module(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/deps.py", "def calculate(value):\n    return value + 1\n")
    _write(
        tmp_path,
        "pkg/source.py",
        (
            "from pkg.deps import calculate\n\n"
            "def work(value):\n"
            "    return calculate(value)\n"
        ),
    )
    _write(
        tmp_path,
        "pkg/target.py",
        "def calculate(value):\n    return value - 1\n",
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.source.work")

    plan = world.plan_move(target.id, "pkg.target", _capability(target.id))

    assert not plan.ready
    assert "MoveDependencyCollision" in {
        item.kind for item in plan.obligations
    }

def test_move_blocks_wildcard_and_string_reference(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/source.py", "def helper():\n    return 1\n")
    _write(tmp_path, "pkg/target.py", "")
    _write(
        tmp_path,
        "consumer.py",
        "from pkg.source import *\n__all__ = ['helper']\n",
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.source.helper")

    plan = world.plan_move(target.id, "pkg.target", _capability(target.id))

    assert not plan.ready
    kinds = {item.kind for item in plan.obligations}
    assert "WildcardReference" in kinds
    assert "StringReference" in kinds


def test_move_detects_cycle_created_by_old_module_bridge(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/source.py",
        (
            "def helper():\n    return 1\n\n"
            "def caller():\n    return helper()\n"
        ),
    )
    _write(
        tmp_path,
        "pkg/target.py",
        "from pkg.source import caller\n",
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.source.helper")

    plan = world.plan_move(target.id, "pkg.target", _capability(target.id))

    assert not plan.ready
    assert "CyclicDependency" in {item.kind for item in plan.obligations}


def test_move_detects_cycle_created_by_migrated_import(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py", "from .consumer import caller\n")
    _write(tmp_path, "pkg/source.py", "def helper():\n    return 1\n")
    _write(
        tmp_path,
        "pkg/consumer.py",
        "from .source import helper\n\ndef caller():\n    return helper()\n",
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.source.helper")

    plan = world.plan_move(target.id, "pkg", _capability(target.id))

    assert not plan.ready
    assert "CyclicDependency" in {item.kind for item in plan.obligations}


def test_move_blocks_unconditional_destination_import_exit(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/source.py", "def helper():\n    return 1\n")
    _write(
        tmp_path,
        "pkg/platform_only.py",
        "import sys\nassert sys.platform == 'imaginary'\n",
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.source.helper")

    plan = world.plan_move(
        target.id,
        "pkg.platform_only",
        _capability(target.id),
    )

    assert not plan.ready
    assert "MoveDestinationImportHazard" in {
        obligation.kind for obligation in plan.obligations
    }


def test_move_blocks_ambiguous_dependency_without_crashing(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/source.py",
        (
            "def dependency():\n    return 1\n\n"
            "def dependency():\n    return 2\n\n"
            "def helper():\n    return dependency()\n"
        ),
    )
    _write(tmp_path, "pkg/target.py", "")
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.source.helper")

    plan = world.plan_move(target.id, "pkg.target", _capability(target.id))

    assert not plan.ready
    assert "MoveDependencyAmbiguous" in {
        obligation.kind for obligation in plan.obligations
    }


def test_move_blocks_import_from_destination_itself(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/source.py", "def helper():\n    return 1\n")
    _write(
        tmp_path,
        "pkg/target.py",
        "from .source import helper\n\ndef run():\n    return helper()\n",
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.source.helper")

    plan = world.plan_move(target.id, "pkg.target", _capability(target.id))

    assert not plan.ready
    assert "MoveDestinationSelfImport" in {
        obligation.kind for obligation in plan.obligations
    }


def test_move_preview_never_mutates_source(tmp_path: Path):
    _move_workspace(tmp_path)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*.py")
    }
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.source.helper")

    world.plan_move(target.id, "pkg.target", _capability(target.id))

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*.py")
    }
    assert after == before



def test_move_carries_class_header_dependencies(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/source.py",
        (
            "from framework import BaseModel, configure, validate\n\n"
            "@configure()\n"
            "class Model(BaseModel):\n"
            "    @configure()\n"
            "    def run(self):\n"
            "        return validate()\n"
        ),
    )
    _write(tmp_path, "pkg/target.py", "")
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.source.Model")

    plan = world.plan_move(target.id, "pkg.target", _capability(target.id))

    assert plan.ready
    destination = next(
        edit.replacement
        for edit in plan.edits
        if edit.file == "pkg/target.py"
    )
    assert "from framework import BaseModel" in destination
    assert "from framework import configure" in destination
    assert "from framework import validate" in destination