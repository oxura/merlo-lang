from __future__ import annotations

from pathlib import Path

import pytest

from merlo import EditCapability, SoftwareWorld, WorldError, scan_python


def _replace_workspace(root: Path, files: dict[str, str]) -> None:
    for path in root.rglob("*.py"):
        path.unlink()
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _initial(root: Path):
    _replace_workspace(
        root,
        {"a.py": "def alpha(x):\n    return x + 1\n"},
    )
    program = scan_python(root)
    return program, program.entity("a.alpha")


@pytest.mark.parametrize(
    ("name", "files", "locator"),
    [
        (
            "body edit",
            {"a.py": "def alpha(x):\n    return x + 2\n"},
            "a.alpha",
        ),
        (
            "rename",
            {"a.py": "def beta(x):\n    return x + 1\n"},
            "a.beta",
        ),
        (
            "move",
            {"b.py": "def alpha(x):\n    return x + 1\n"},
            "b.alpha",
        ),
        (
            "rename and body edit",
            {"a.py": "def beta(x):\n    return x + 2\n"},
            "a.beta",
        ),
        (
            "move and body edit",
            {"b.py": "def alpha(x):\n    return x + 2\n"},
            "b.alpha",
        ),
        (
            "rename and move",
            {"b.py": "def beta(x):\n    return x + 1\n"},
            "b.beta",
        ),
        (
            "rename move and body edit",
            {"b.py": "def beta(x):\n    return x + 2\n"},
            "b.beta",
        ),
    ],
)
def test_external_edits_offer_probable_identity_without_inheritance(
    tmp_path: Path,
    name: str,
    files: dict[str, str],
    locator: str,
):
    old, entity = _initial(tmp_path)
    _replace_workspace(tmp_path, files)

    new = scan_python(tmp_path, previous=old)
    recovered = new.entity(locator)

    assert recovered.id != entity.id, name
    assert recovered.identity_status == "Probable"
    assert recovered.identity_score >= 0.84
    assert recovered.revision_hash != entity.revision_hash
    assert any(
        relation.status == "Probable"
        and relation.old_id == entity.id
        and relation.new_id == recovered.id
        for relation in new.identity_relations
    )


def test_delete_then_create_similar_function_is_not_false_exact(tmp_path: Path):
    old, entity = _initial(tmp_path)
    _replace_workspace(
        tmp_path,
        {"a.py": 'def alpha(x):\n    return "unrelated"\n'},
    )

    new = scan_python(tmp_path, previous=old)
    replacement = new.entity("a.alpha")

    assert replacement.id != entity.id
    assert replacement.identity_status == "Ambiguous"
    assert any(
        relation.status == "Ambiguous" and relation.old_id == entity.id
        for relation in new.identity_relations
    )


def test_two_identical_candidates_never_receive_old_id(tmp_path: Path):
    old, entity = _initial(tmp_path)
    _replace_workspace(
        tmp_path,
        {
            "b.py": (
                "def beta(x):\n    return x + 1\n\n"
                "def gamma(x):\n    return x + 1\n"
            )
        },
    )

    new = scan_python(tmp_path, previous=old)

    assert entity.id not in {item.id for item in new.entities}
    ambiguous = [
        relation
        for relation in new.identity_relations
        if relation.status == "Ambiguous" and relation.old_id == entity.id
    ]
    assert len(ambiguous) == 1
    assert {item.locator for item in ambiguous[0].candidates} == {
        "b.beta",
        "b.gamma",
    }


def test_copy_then_modify_original_is_ambiguous(tmp_path: Path):
    old, entity = _initial(tmp_path)
    _replace_workspace(
        tmp_path,
        {
            "a.py": (
                "def alpha(x):\n    return x + 2\n\n"
                "def copy_alpha(x):\n    return x + 1\n"
            )
        },
    )

    new = scan_python(tmp_path, previous=old)

    assert entity.id not in {item.id for item in new.entities}
    assert all(item.identity_status == "Ambiguous" for item in new.entities)


def test_swapped_names_are_ambiguous_without_provenance(tmp_path: Path):
    _replace_workspace(
        tmp_path,
        {
            "a.py": (
                "def alpha(x):\n    return x + 1\n\n"
                "def beta(x):\n    return x * 2\n"
            )
        },
    )
    old = scan_python(tmp_path)
    old_ids = {item.id for item in old.entities}
    _replace_workspace(
        tmp_path,
        {
            "a.py": (
                "def alpha(x):\n    return x * 2\n\n"
                "def beta(x):\n    return x + 1\n"
            )
        },
    )

    new = scan_python(tmp_path, previous=old)

    assert not old_ids & {item.id for item in new.entities}
    assert all(item.identity_status == "Ambiguous" for item in new.entities)


def test_changeir_provenance_keeps_identity_exact(tmp_path: Path):
    _replace_workspace(
        tmp_path,
        {"module.py": "def alpha(x):\n    return x + 1\n"},
    )
    world = SoftwareWorld.scan(tmp_path)
    target = world.program.entity("module.alpha")
    capability = EditCapability.rename(
        target.id,
        allow_public_api_break=True,
    )
    plan = world.plan_rename(target.id, "beta", capability)

    world.apply(plan, capability)
    renamed = world.program.entity(target.id)

    assert renamed.fqname == "module.beta"
    assert renamed.identity_status == "Exact"
    assert "explicit ChangeIR provenance" in renamed.identity_reason


def test_probable_identity_blocks_later_change_until_confirmed(tmp_path: Path):
    state = tmp_path / ".meldra" / "world.json"
    _replace_workspace(
        tmp_path,
        {"module.py": "def alpha(x):\n    return x + 1\n"},
    )
    world = SoftwareWorld.scan(tmp_path, state)
    world.save()
    target = world.program.entity("module.alpha")
    _replace_workspace(
        tmp_path,
        {"module.py": "def alpha(x):\n    return x + 2\n"},
    )
    changed = SoftwareWorld.scan(tmp_path, state)
    recovered = changed.program.entity("module.alpha")
    assert recovered.id != target.id
    capability = EditCapability.rename(
        recovered.id,
        allow_public_api_break=True,
    )

    plan = changed.plan_rename(recovered.id, "beta", capability)

    assert not plan.ready
    assert "ProbableIdentity" in {item.kind for item in plan.obligations}

    confirmed = changed.confirm_identity(recovered.id, target.id)
    confirmed_capability = EditCapability.rename(
        confirmed.id,
        allow_public_api_break=True,
    )
    confirmed_plan = changed.plan_rename(
        confirmed.id, "beta", confirmed_capability
    )
    assert confirmed.id == target.id
    assert confirmed.identity_status == "Exact"
    assert confirmed_plan.ready



def test_formatting_and_comments_change_source_not_semantic_hash(tmp_path: Path):
    _replace_workspace(
        tmp_path,
        {"module.py": "def alpha(x):\n    return 'value'\n"},
    )
    first = scan_python(tmp_path)
    before = first.entity("module.alpha")
    _replace_workspace(
        tmp_path,
        {
            "module.py": (
                "def alpha( x ):\n"
                "    # retained human explanation\n"
                "    return \"value\"\n"
            )
        },
    )

    second = scan_python(tmp_path, previous=first)
    after = second.entity("module.alpha")

    assert after.source_hash != before.source_hash
    assert after.revision_hash == before.revision_hash
    assert after.identity_status == "Exact"


@pytest.mark.parametrize(
    "changed_source",
    [
        'def alpha(x):\n    \"new docs\"\n    return x + 1\n',
        '@staticmethod\ndef alpha(x):\n    return x + 1\n',
        'def alpha(x: int):\n    return x + 1\n',
    ],
)
def test_docstrings_decorators_and_annotations_change_semantic_hash(
    tmp_path: Path, changed_source: str
):
    old, before = _initial(tmp_path)
    _replace_workspace(tmp_path, {"a.py": changed_source})

    new = scan_python(tmp_path, previous=old)
    after = new.entity("a.alpha")

    assert after.revision_hash != before.revision_hash


def test_ambiguous_identity_can_be_confirmed_explicitly(tmp_path: Path):
    state = tmp_path / ".meldra" / "world.json"
    _replace_workspace(
        tmp_path,
        {"a.py": "def alpha(x):\n    return x + 1\n"},
    )
    old_world = SoftwareWorld.scan(tmp_path, state)
    old_id = old_world.program.entity("a.alpha").id
    old_world.save()
    _replace_workspace(
        tmp_path,
        {"a.py": "def alpha(x):\n    return 'unrelated'\n"},
    )
    ambiguous = SoftwareWorld.scan(tmp_path, state)
    assert ambiguous.program.entity("a.alpha").identity_status == "Ambiguous"
    identity_obligations = tuple(
        item
        for item in ambiguous.obligations.values()
        if item.kind == "AmbiguousIdentity"
    )
    assert identity_obligations
    with pytest.raises(WorldError, match="not a reviewable predecessor"):
        ambiguous.confirm_identity("a.alpha", "ent_not_a_candidate")


    confirmed = ambiguous.confirm_identity("a.alpha", old_id)

    assert confirmed.id == old_id
    assert confirmed.identity_status == "Exact"
    assert "manual_identity_confirmation" in confirmed.identity_reason
    assert all(
        ambiguous.obligations[item.id].status == "resolved"
        for item in identity_obligations
    )