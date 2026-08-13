from __future__ import annotations

from pathlib import Path

from merlo import EditCapability, SoftwareWorld


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _base(root: Path, consumers: str = "") -> None:
    _write(
        root,
        "api.py",
        (
            "def compute(tokens, complexity):\n"
            "    return tokens + complexity\n"
        ),
    )
    if consumers:
        _write(root, "consumer.py", consumers)


def _capability(target_id: str, *, public_break: bool = True):
    return EditCapability.change_signature(
        target_id,
        allow_public_api_break=public_break,
    )


def test_optional_keyword_only_parameter_is_backward_compatible(tmp_path: Path):
    _base(
        tmp_path,
        "from api import compute\nresult = compute(1, 2)\n",
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("api.compute")
    capability = _capability(target.id, public_break=False)

    plan = world.plan_change_signature(
        target.id,
        "(tokens, complexity, *, model=None)",
        capability,
    )

    assert plan.ready, [item.to_dict() for item in plan.obligations]
    assert [item.reason for item in plan.edits] == ["change_signature"]
    world.apply(plan, capability)
    assert "def compute(tokens, complexity, *, model=None):" in (
        tmp_path / "api.py"
    ).read_text(encoding="utf-8")
    assert "compute(1, 2)" in (tmp_path / "consumer.py").read_text(
        encoding="utf-8"
    )


def test_required_parameter_migrates_positional_keyword_and_multiline_calls(
    tmp_path: Path,
):
    _base(
        tmp_path,
        (
            "from api import compute\n"
            "a = compute(1, 2)\n"
            "b = compute(tokens=1, complexity=2)\n"
            "c = compute(\n"
            "    1,\n"
            "    2,\n"
            ")\n"
        ),
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("api.compute")
    capability = _capability(target.id)

    plan = world.plan_change_signature(
        target.id,
        "(tokens, complexity, *, model)",
        capability,
        argument_values={"model": "'fast'"},
    )

    assert plan.ready, [item.to_dict() for item in plan.obligations]
    assert sum(item.reason == "migrate_direct_call" for item in plan.edits) == 3
    world.apply(plan, capability)
    consumer = (tmp_path / "consumer.py").read_text(encoding="utf-8")
    assert "compute(1, 2, model='fast')" in consumer
    assert "compute(tokens=1, complexity=2, model='fast')" in consumer
    assert "model='fast'" in consumer
    assert world.program.entity(target.id).identity_status == "Exact"


def test_missing_required_argument_strategy_is_blocking(tmp_path: Path):
    _base(tmp_path, "from api import compute\nvalue = compute(1, 2)\n")
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("api.compute")

    plan = world.plan_change_signature(
        target.id,
        "(tokens, complexity, model)",
        _capability(target.id),
    )

    assert not plan.ready
    assert "MissingArgumentMigration" in {item.kind for item in plan.obligations}


def test_variadic_calls_create_specific_obligations(tmp_path: Path):
    _base(
        tmp_path,
        (
            "from api import compute\n"
            "def wrapper(*args, **kwargs):\n"
            "    return compute(*args, **kwargs)\n"
        ),
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("api.compute")

    plan = world.plan_change_signature(
        target.id,
        "(tokens, complexity, *, model)",
        _capability(target.id),
        argument_values={"model": "'fast'"},
    )

    assert not plan.ready
    assert "VariadicCallCompatibility" in {
        item.kind for item in plan.obligations
    }


def test_indirect_function_uses_are_never_silently_migrated(tmp_path: Path):
    _base(
        tmp_path,
        (
            "from functools import partial\n"
            "from api import compute\n\n"
            "stored = compute\n"
            "part = partial(compute, 1)\n"
            "callback_register(compute)\n"
            "decorated = decorator(compute)\n\n"
            "@compute\n"
            "def wrapped():\n"
            "    return 1\n"
        ),
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("api.compute")

    plan = world.plan_change_signature(
        target.id,
        "(tokens, complexity, *, model)",
        _capability(target.id),
        argument_values={"model": "'fast'"},
    )

    kinds = {item.kind for item in plan.obligations}
    assert not plan.ready
    assert "StoredFunctionCompatibility" in kinds
    assert "PartialCompatibility" in kinds
    assert "CallbackCompatibility" in kinds
    assert "DecoratorCompatibility" in kinds


def test_getattr_call_creates_dynamic_call_obligation(tmp_path: Path):
    _base(
        tmp_path,
        (
            "import api\n\n"
            "def run():\n"
            "    fn = getattr(api, 'compute')\n"
            "    return fn(1, 2)\n"
        ),
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("api.compute")

    plan = world.plan_change_signature(
        target.id,
        "(tokens, complexity, *, model)",
        _capability(target.id),
        argument_values={"model": "'fast'"},
    )

    assert not plan.ready
    assert "DynamicCallCompatibility" in {
        item.kind for item in plan.obligations
    }


def test_public_required_change_needs_explicit_capability(tmp_path: Path):
    _base(tmp_path, "from api import compute\nvalue = compute(1, 2)\n")
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("api.compute")

    plan = world.plan_change_signature(
        target.id,
        "(tokens, complexity, *, model)",
        _capability(target.id, public_break=False),
        argument_values={"model": "'fast'"},
    )

    assert not plan.ready
    assert "PublicApiCompatibility" in {item.kind for item in plan.obligations}


def test_existing_parameter_default_change_is_not_treated_as_additive(
    tmp_path: Path,
):
    _write(
        tmp_path,
        "api.py",
        "def compute(tokens=1):\n    return tokens\n",
    )
    _write(tmp_path, "consumer.py", "from api import compute\nvalue = compute()\n")
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("api.compute")

    plan = world.plan_change_signature(
        target.id,
        "(tokens)",
        _capability(target.id),
    )

    assert not plan.ready
    assert "UnsupportedSignatureMigration" in {
        item.kind for item in plan.obligations
    }


def test_signature_preview_does_not_mutate_source(tmp_path: Path):
    _base(tmp_path, "from api import compute\nvalue = compute(1, 2)\n")
    before = {path: path.read_bytes() for path in tmp_path.glob("*.py")}
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("api.compute")

    world.plan_change_signature(
        target.id,
        "(tokens, complexity, *, model=None)",
        _capability(target.id, public_break=False),
    )

    assert {path: path.read_bytes() for path in tmp_path.glob("*.py")} == before
