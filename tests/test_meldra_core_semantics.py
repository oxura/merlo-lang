from __future__ import annotations

import json

import pytest

from merlo import (
    CoreBindingError,
    CoreChange,
    CoreError,
    CoreProgram,
    apply_core_change,
    compile_core,
)


def _program() -> CoreProgram:
    return CoreProgram.from_dict(
        {
            "packages": [
                {
                    "id": "pkg-api",
                    "name": "api",
                    "modules": [
                        {
                            "name": "types",
                            "imports": [],
                            "exports": ["User"],
                            "declarations": [
                                {
                                    "id": "sym-user",
                                    "name": "User",
                                    "kind": "interface",
                                    "members": {"name": "String"},
                                },
                                {
                                    "id": "sym-helper",
                                    "name": "normalize",
                                    "kind": "function",
                                    "signature": {
                                        "parameters": [{"name": "raw", "type": "String"}],
                                        "returns": "String",
                                    },
                                    "implementation": "trim-and-casefold",
                                },
                            ],
                        },
                        {
                            "name": "tasks",
                            "imports": [
                                {
                                    "package": "api",
                                    "module": "types",
                                    "name": "User",
                                }
                            ],
                            "exports": ["load_user"],
                            "declarations": [
                                {
                                    "id": "sym-net",
                                    "name": "Network",
                                    "kind": "capability",
                                    "effects": ["network"],
                                },
                                {
                                    "id": "sym-load",
                                    "name": "load_user",
                                    "kind": "task",
                                    "signature": {
                                        "parameters": [{"name": "id", "type": "String"}],
                                        "returns": "User",
                                    },
                                    "effects": ["network"],
                                    "capabilities": ["Network"],
                                    "implementation": "fetch-user",
                                    "refs": [
                                        "User",
                                        {
                                            "foreign": "python:ssl.SSLContext",
                                            "usage": "Type",
                                        },
                                    ],
                                },
                            ],
                        },
                    ],
                },
                {
                    "id": "pkg-app",
                    "name": "app",
                    "modules": [
                        {
                            "name": "main",
                            "imports": [
                                {
                                    "package": "api",
                                    "module": "tasks",
                                    "name": "load_user",
                                    "alias": "load",
                                }
                            ],
                            "exports": ["run"],
                            "declarations": [
                                {
                                    "id": "sym-run",
                                    "name": "run",
                                    "kind": "task",
                                    "signature": {
                                        "parameters": [],
                                        "returns": "Unit",
                                    },
                                    "implementation": "invoke-load",
                                    "refs": ["load"],
                                }
                            ],
                        }
                    ],
                },
            ]
        }
    )


def test_internal_binding_is_entirely_exact_and_foreign_is_not_unknown():
    world = compile_core(_program())

    assert world.exact_reference_count == 3
    assert world.foreign_reference_count == 1
    assert world.unknown_reference_count == 0
    assert {reference.status for reference in world.references} == {"Exact", "Foreign"}
    foreign = next(item for item in world.references if item.status == "Foreign")
    assert foreign.target_id is None
    assert foreign.foreign_target == "python:ssl.SSLContext"


def test_unresolved_hidden_and_ambiguous_bindings_are_rejected():
    hidden = _program().to_dict()
    hidden["packages"][1]["modules"][0]["imports"][0]["name"] = "normalize"
    hidden["packages"][1]["modules"][0]["declarations"][0]["refs"] = [
        "normalize"
    ]
    with pytest.raises(CoreBindingError, match="hidden or missing export"):
        compile_core(CoreProgram.from_dict(hidden))

    ambiguous = _program().to_dict()
    ambiguous["packages"][1]["modules"][0]["imports"].append(
        {
            "package": "api",
            "module": "types",
            "name": "User",
            "alias": "load",
        }
    )
    with pytest.raises(CoreBindingError, match="ambiguous import aliases"):
        compile_core(CoreProgram.from_dict(ambiguous))

    unresolved = _program().to_dict()
    unresolved["packages"][1]["modules"][0]["declarations"][0]["refs"] = [
        "not_imported"
    ]
    with pytest.raises(CoreBindingError, match="unresolved reference"):
        compile_core(CoreProgram.from_dict(unresolved))

    unresolved_type = _program().to_dict()
    unresolved_type["packages"][0]["modules"][1]["declarations"][1][
        "signature"
    ]["returns"] = "MissingType"
    with pytest.raises(CoreBindingError, match="unresolved reference"):
        compile_core(CoreProgram.from_dict(unresolved_type))


def test_duplicate_ids_names_and_invalid_exports_are_rejected():
    duplicate_id = _program().to_dict()
    duplicate_id["packages"][0]["modules"][0]["declarations"][1]["id"] = (
        "sym-user"
    )
    with pytest.raises(CoreError, match="duplicate symbol id"):
        compile_core(CoreProgram.from_dict(duplicate_id))

    duplicate_name = _program().to_dict()
    duplicate_name["packages"][0]["modules"][0]["declarations"][1]["name"] = (
        "User"
    )
    with pytest.raises(CoreError, match="duplicate declaration name"):
        compile_core(CoreProgram.from_dict(duplicate_name))

    invalid_export = _program().to_dict()
    invalid_export["packages"][0]["modules"][0]["exports"].append("Missing")
    with pytest.raises(CoreError, match="invalid exports"):
        compile_core(CoreProgram.from_dict(invalid_export))


def test_private_implementation_change_stops_at_package_interface():
    world = compile_core(_program())
    before_api = world.package("api")
    before_app = world.package("app")

    result = apply_core_change(
        world,
        CoreChange.change_implementation("sym-helper", "new-private-algorithm"),
    )

    after_api = result.world.package("api")
    after_app = result.world.package("app")
    assert after_api.interface_revision == before_api.interface_revision
    assert after_api.implementation_revision != before_api.implementation_revision
    assert after_app == before_app
    assert result.affected_symbols == ("sym-helper",)
    assert result.affected_packages == ("pkg-api",)
    assert result.interface_changed_packages == ()


def test_public_signature_change_propagates_only_to_exact_dependents():
    world = compile_core(_program())
    changed = apply_core_change(
        world,
        CoreChange.change_signature(
            "sym-load",
            {
                "parameters": [
                    {"name": "id", "type": "String"},
                    {"name": "fresh", "type": "Bool"},
                ],
                "returns": "User",
            },
        ),
    )

    assert changed.world.symbol("sym-load").revision_id != world.symbol(
        "sym-load"
    ).revision_id
    assert changed.world.package("api").interface_revision != world.package(
        "api"
    ).interface_revision
    assert changed.world.package("app").interface_revision == world.package(
        "app"
    ).interface_revision
    assert set(changed.affected_symbols) == {"sym-load", "sym-run"}
    assert set(changed.affected_packages) == {"pkg-api", "pkg-app"}
    assert changed.interface_changed_packages == ("pkg-api",)


def test_symbol_identity_survives_rename_and_move_while_revision_changes():
    world = compile_core(_program())
    old = world.symbol("sym-helper")

    renamed = apply_core_change(world, CoreChange.rename(old.id, "canonicalize"))
    renamed_symbol = renamed.world.symbol(old.id)
    assert renamed_symbol.id == old.id
    assert renamed_symbol.name == "canonicalize"
    assert renamed_symbol.revision_id != old.revision_id

    moved = apply_core_change(
        renamed.world, CoreChange.move(old.id, "tasks", target_package="api")
    )
    moved_symbol = moved.world.symbol(old.id)
    assert moved_symbol.id == old.id
    assert moved_symbol.module == "tasks"
    assert moved_symbol.revision_id != renamed_symbol.revision_id
    assert moved.world.unknown_reference_count == 0


def test_effects_are_task_only_and_tasks_need_covering_capabilities():
    effectful_function = _program().to_dict()
    effectful_function["packages"][0]["modules"][0]["declarations"][1][
        "effects"
    ] = ["network"]
    with pytest.raises(CoreError, match="only valid on task"):
        compile_core(CoreProgram.from_dict(effectful_function))

    uncovered_task = _program().to_dict()
    uncovered_task["packages"][0]["modules"][1]["declarations"][1][
        "capabilities"
    ] = []
    with pytest.raises(CoreError, match="effects without capabilities"):
        compile_core(CoreProgram.from_dict(uncovered_task))

    missing_capability = _program().to_dict()
    missing_capability["packages"][0]["modules"][1]["declarations"][1][
        "capabilities"
    ] = ["Database"]
    with pytest.raises(CoreError, match="missing capability"):
        compile_core(CoreProgram.from_dict(missing_capability))


def test_capability_escalation_is_blocked_until_materialized():
    source = _program().to_dict()
    source["packages"][0]["modules"][1]["declarations"].insert(
        1,
        {
            "id": "sym-db",
            "name": "Database",
            "kind": "capability",
            "effects": ["database"],
        },
    )
    world = compile_core(CoreProgram.from_dict(source))
    proposed = CoreChange.change_implementation(
        "sym-load",
        "fetch-and-cache",
        effects=("network", "database"),
        capabilities=("Network", "Database"),
    )

    blocked = apply_core_change(world, proposed)
    assert blocked.blocked
    assert not blocked.applied
    assert blocked.world is world
    assert blocked.affected_symbols == ()
    assert blocked.capability_violation is not None
    assert set(blocked.capability_violation.capabilities) == {"database", "Database"}

    materialized = apply_core_change(
        world,
        CoreChange.change_implementation(
            "sym-load",
            "fetch-and-cache",
            effects=("network", "database"),
            capabilities=("Network", "Database"),
            materialized_capabilities=("database", "Database"),
        ),
    )
    assert materialized.applied
    assert materialized.world.symbol("sym-load").effects == (
        "database",
        "network",
    )

    restricted = apply_core_change(
        materialized.world,
        CoreChange.restrict_effect("sym-load", ("network",)),
    )
    assert restricted.world.symbol("sym-load").effects == ("network",)
    with pytest.raises(CoreError, match="cannot add effects"):
        apply_core_change(
            restricted.world,
            CoreChange.restrict_effect("sym-load", ("network", "filesystem")),
        )


def test_program_world_context_and_change_json_are_stable():
    first_program = _program()
    second_program = CoreProgram.from_dict(_program().to_dict())
    assert first_program.to_json() == second_program.to_json()
    assert first_program.to_json() == json.dumps(
        first_program.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    first_world = compile_core(first_program)
    second_world = compile_core(second_program)
    assert first_world.to_dict() == second_world.to_dict()
    assert first_world.to_json() == second_world.to_json()
    assert first_world.to_json() == json.dumps(
        first_world.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    context = first_world.context_for("sym-load")
    assert context["symbol"]["id"] == "sym-load"
    assert context["package"]["id"] == "pkg-api"
    assert context["inbound_references"][0]["owner_id"] == "sym-run"
