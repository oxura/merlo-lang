from __future__ import annotations

import pytest
from pathlib import Path

from merlo.modules import ModuleError, ModuleGraph
from merlo.compiler import compile_project


LIBRARY = (
    "module shop.math\n\n"
    "export fn add(left: UInt64, right: UInt64) -> UInt64:\n"
    "    return left + right\n\n"
    "fn secret() -> UInt64:\n"
    "    return 41\n"
)
APPLICATION = (
    "module app.main\n"
    "use shop.math\n\n"
    "export fn main(value: UInt64) -> UInt64:\n"
    "    return math.add(value, 1)\n"
)


def test_module_graph_binds_imports_and_private_visibility_exactly() -> None:
    graph = ModuleGraph.from_sources(
        {"shop.math": LIBRARY, "app.main": APPLICATION}
    )

    exported = graph.resolve("shop.math", "add", requester="app.main")
    local = graph.resolve("shop.math", "secret", requester="shop.math")

    assert exported.exported is True
    assert local.exported is False
    with pytest.raises(ModuleError, match="PrivateSymbol"):
        graph.resolve("shop.math", "secret", requester="app.main")
    with pytest.raises(ModuleError, match="unresolved imports"):
        ModuleGraph.from_sources({"app.main": APPLICATION})


def test_symbol_identity_is_path_independent() -> None:
    first = ModuleGraph.from_sources(
        {"shop.math": LIBRARY},
        paths={"shop.math": "/first/root/math.mlo"},
    )
    second = ModuleGraph.from_sources(
        {"shop.math": LIBRARY},
        paths={"shop.math": "/moved/root/math.mlo"},
    )

    first_symbol = first.module("shop.math").symbol("add")
    second_symbol = second.module("shop.math").symbol("add")

    assert first_symbol.symbol_id == second_symbol.symbol_id
    assert first_symbol.revision_id == second_symbol.revision_id
    assert first.interface_revision_id == second.interface_revision_id
    assert first.implementation_revision_id == second.implementation_revision_id
    assert first.to_json() == second.to_json()


def test_interface_and_implementation_revisions_are_separate() -> None:
    changed_body = LIBRARY.replace("return left + right", "return right + left")
    changed_interface = LIBRARY.replace(
        "right: UInt64) -> UInt64",
        "right: Int64) -> UInt64",
    )
    original = ModuleGraph.from_sources({"shop.math": LIBRARY})
    body = ModuleGraph.from_sources({"shop.math": changed_body})
    interface = ModuleGraph.from_sources({"shop.math": changed_interface})

    original_symbol = original.module("shop.math").symbol("add")
    body_symbol = body.module("shop.math").symbol("add")
    interface_symbol = interface.module("shop.math").symbol("add")

    assert original_symbol.symbol_id == body_symbol.symbol_id == interface_symbol.symbol_id
    assert original_symbol.interface_revision_id == body_symbol.interface_revision_id
    assert original_symbol.revision_id != body_symbol.revision_id
    assert original.interface_revision_id == body.interface_revision_id
    assert original.implementation_revision_id != body.implementation_revision_id
    assert original_symbol.interface_revision_id != interface_symbol.interface_revision_id
    assert original.interface_revision_id != interface.interface_revision_id


def test_cyclic_imports_are_rejected_with_exact_cycle() -> None:
    sources = {
        "a": "module a\nuse b\n\nexport fn one() -> UInt64:\n    return 1\n",
        "b": "module b\nuse c\n\nexport fn two() -> UInt64:\n    return 2\n",
        "c": "module c\nuse a\n\nexport fn three() -> UInt64:\n    return 3\n",
    }

    with pytest.raises(ModuleError, match=r"CyclicModuleImport: a -> b -> c -> a"):
        ModuleGraph.from_sources(sources)


def test_duplicate_symbols_and_late_imports_are_rejected() -> None:
    with pytest.raises(ModuleError, match="duplicate symbol"):
        ModuleGraph.from_sources(
            {
                "app.main": (
                    "module app.main\n\n"
                    "fn value() -> UInt64:\n    return 1\n\n"
                    "fn value() -> UInt64:\n    return 2\n"
                )
            }
        )

    with pytest.raises(ModuleError, match="imports must precede declarations"):
        ModuleGraph.from_sources(
            {
                "app.main": (
                    "module app.main\n\n"
                    "fn value() -> UInt64:\n    return 1\n"
                    "use shop.math\n"
                ),
                "shop.math": LIBRARY,
            }
        )


def test_private_types_cannot_cross_module_signatures() -> None:
    with pytest.raises(ModuleError, match=r"PrivateSymbol: shop\.model\.Secret"):
        ModuleGraph.from_sources(
            {
                "shop.model": (
                    "module shop.model\n\n"
                    "record Secret:\n"
                    "    value: UInt64\n"
                ),
                "app.main": (
                    "module app.main\n"
                    "use shop.model\n\n"
                    "export fn reveal(value: Secret) -> UInt64:\n"
                    "    return value.value\n"
                ),
            }
        )


def test_production_compilation_records_exact_module_graph_artifact() -> None:
    compilation = compile_project(
        Path("merlo/programs/productive_grep/app/main.mlo"),
        require_interface_lock=False,
    )

    assert {item.name for item in compilation.module_graph.modules} == {
        "app.main",
        "app.search",
    }
    artifact = compilation.artifacts["modules"]
    assert artifact.contract == "merlo.module-graph.v1"
    assert artifact.content == compilation.module_graph.to_json()
