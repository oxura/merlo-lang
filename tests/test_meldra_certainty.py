from __future__ import annotations

from pathlib import Path

from merlo import EditCapability, SoftwareWorld, scan_python


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _base(root: Path) -> None:
    _write(root, "pkg/__init__.py", "")
    _write(
        root,
        "pkg/service.py",
        "def greet(name):\n    return f'hello {name}'\n",
    )


def test_alias_shadowing_and_local_import_have_explicit_provenance(tmp_path: Path):
    _base(tmp_path)
    _write(
        tmp_path,
        "pkg/consumer.py",
        (
            "from pkg.service import greet as stable_greet\n\n"
            "def run():\n"
            "    from pkg.service import greet\n"
            "    return greet('A') + stable_greet('B')\n\n"
            "def shadowed(greet):\n"
            "    return greet('local')\n"
        ),
    )

    program = scan_python(tmp_path)
    target = program.entity("pkg.service.greet")
    references = program.references_to(target.id)

    assert any(item.provenance == "Alias" for item in references)
    assert any(item.usage == "Import" and item.owner_id for item in references)
    assert all(item.resolution == "Exact" for item in references)
    shadowed = program.entity("pkg.consumer.shadowed")
    assert not any(item.owner_id == shadowed.id for item in references)


def test_dynamic_constructs_become_uncertain_references(tmp_path: Path):
    _base(tmp_path)
    _write(
        tmp_path,
        "pkg/consumer.py",
        (
            "import importlib\n"
            "import pkg.service as service\n"
            "from pkg.service import *\n\n"
            "def dynamic(name):\n"
            "    first = getattr(service, 'greet')\n"
            "    second = globals()['greet']\n"
            "    module = importlib.import_module('pkg.service')\n"
            "    return first(name), second(name), module\n"
        ),
    )

    program = scan_python(tmp_path)
    target = program.entity("pkg.service.greet")
    uncertain = program.uncertain_references_to(target.id)

    assert {item.resolution for item in uncertain} >= {"Dynamic", "Unknown"}
    assert {item.provenance for item in uncertain} >= {"Reflection", "Wildcard"}
    assert all(target.id in item.possible_target_ids for item in uncertain)
    assert "dynamic_import" in {item.kind for item in program.hazards}


def test_all_and_monkey_patch_are_conditional_not_exact(tmp_path: Path):
    _base(tmp_path)
    _write(
        tmp_path,
        "pkg/consumer.py",
        (
            "import pkg.service as service\n"
            "from pkg.service import greet\n"
            "__all__ = ['greet']\n"
            "service.greet = lambda name: name\n"
        ),
    )

    program = scan_python(tmp_path)
    target = program.entity("pkg.service.greet")
    uncertain = program.uncertain_references_to(target.id)

    assert any(
        item.provenance == "StringLiteral"
        and item.resolution == "Conditional"
        for item in uncertain
    )
    assert any(item.usage == "MonkeyPatch" for item in uncertain)


def test_module_getattr_makes_public_names_dynamically_uncertain(tmp_path: Path):
    _write(
        tmp_path,
        "module.py",
        (
            "def visible():\n    return 1\n\n"
            "def __getattr__(name):\n    return globals()[name]\n"
        ),
    )

    program = scan_python(tmp_path)
    visible = program.entity("module.visible")

    assert any(
        item.kind == "module_getattr"
        and visible.id in item.possible_target_ids
        for item in program.references
    )


def test_reexport_chain_is_resolved_and_renamed_end_to_end(tmp_path: Path):
    _base(tmp_path)
    _write(tmp_path, "pkg/__init__.py", "from .service import greet\n")
    _write(
        tmp_path,
        "consumer.py",
        "from pkg import greet\n\ndef run():\n    return greet('Ada')\n",
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.service.greet")
    references = world.program.references_to(target.id)

    assert any(item.file == "consumer.py" for item in references)
    capability = EditCapability.rename(
        target.id,
        allow_public_api_break=True,
    )
    plan = world.plan_rename(target.id, "welcome", capability)
    assert plan.ready

    world.apply(plan, capability)

    assert "from .service import welcome" in (
        tmp_path / "pkg" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "from pkg import welcome" in (tmp_path / "consumer.py").read_text(
        encoding="utf-8"
    )


def test_explicit_reexport_alias_preserves_public_alias(tmp_path: Path):
    _base(tmp_path)
    _write(
        tmp_path,
        "pkg/__init__.py",
        "from .service import greet as greet\n",
    )
    _write(
        tmp_path,
        "consumer.py",
        "from pkg import greet\n\ndef run():\n    return greet('Ada')\n",
    )
    _write(
        tmp_path,
        "attribute_consumer.py",
        "import pkg\n\ndef run():\n    return pkg.greet('Ada')\n",
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.service.greet")
    capability = EditCapability.rename(
        target.id,
        allow_public_api_break=True,
    )

    plan = world.plan_rename(target.id, "welcome", capability)
    world.apply(plan, capability)

    assert "from .service import welcome as greet" in (
        tmp_path / "pkg" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "from pkg import greet" in (tmp_path / "consumer.py").read_text(
        encoding="utf-8"
    )
    assert "pkg.greet('Ada')" in (
        tmp_path / "attribute_consumer.py"
    ).read_text(encoding="utf-8")



def test_rename_blocks_runtime_protocol_names(tmp_path: Path):
    _write(
        tmp_path,
        "module.py",
        "def __getattr__(name):\n    raise AttributeError(name)\n",
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("module.__getattr__")
    capability = EditCapability.rename(
        target.id,
        allow_public_api_break=True,
    )

    plan = world.plan_rename(target.id, "lookup", capability)

    assert not plan.ready
    assert "SemanticProtocolName" in {
        obligation.kind for obligation in plan.obligations
    }


def test_rename_blocks_symbols_in_dynamic_module_namespace(
    tmp_path: Path,
):
    _write(
        tmp_path,
        "module.py",
        (
            "def _helper():\n    return 1\n\n"
            "def __getattr__(name):\n"
            "    return globals()[f'_{name}']\n"
        ),
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("module._helper")
    capability = EditCapability.rename(
        target.id,
        allow_public_api_break=True,
    )

    plan = world.plan_rename(target.id, "_renamed", capability)

    assert not plan.ready
    assert "DynamicModuleNamespace" in {
        obligation.kind for obligation in plan.obligations
    }


def test_relative_submodule_import_resolves_attribute_reference(tmp_path: Path):
    _base(tmp_path)
    _write(
        tmp_path,
        "pkg/consumer.py",
        "from . import service\n\ndef run():\n    return service.greet('Ada')\n",
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("pkg.service.greet")
    capability = EditCapability.rename(
        target.id,
        allow_public_api_break=True,
    )

    plan = world.plan_rename(target.id, "welcome", capability)
    world.apply(plan, capability)

    assert "service.welcome('Ada')" in (
        tmp_path / "pkg" / "consumer.py"
    ).read_text(encoding="utf-8")


def test_src_layout_uses_importable_module_names_and_resolves_tests(
    tmp_path: Path,
):
    _write(tmp_path, "src/pkg/__init__.py", "")
    _write(tmp_path, "src/pkg/service.py", "def greet():\n    return 'hello'\n")
    _write(
        tmp_path,
        "tests/test_service.py",
        "from pkg.service import greet\n\ndef test_greet():\n    assert greet() == 'hello'\n",
    )

    program = scan_python(tmp_path)
    target = program.entity("pkg.service.greet")

    assert program.file_for_module("pkg.service") == "src/pkg/service.py"
    assert any(
        reference.file == "tests/test_service.py"
        and reference.usage == "Import"
        for reference in program.references_to(target.id)
    )