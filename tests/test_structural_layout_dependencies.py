from __future__ import annotations

from dataclasses import replace
import json

import pytest

from merlo.representation_ir import (
    RepresentationCompileError,
    build_drop_plans,
    build_type_descriptors,
    lower_structured_hir_to_rir,
    validate_recursive_layouts,
)
from merlo.structured_hir_v2 import (
    StructuredHIRCompileError,
    StructuredHIRProgram,
    compile_structured_hir,
)


_MAIN = "fn main(input: BytesView) -> UInt64:\n    return 0\n"


@pytest.mark.parametrize(
    ("declarations", "cycle", "branch"),
    [
        ("record Node:\n    next: Node\n", ("Node", "Node"), "field[next]"),
        (
            "record Node:\n    next: Option[Node]\n",
            ("Node", "Node"),
            "field[next]/Option.payload",
        ),
        (
            "record Error:\n    code: UInt64\n"
            "record Node:\n    next: Result[Node,Error]\n",
            ("Node", "Node"),
            "field[next]/Result.ok",
        ),
        (
            "record Error:\n    code: UInt64\n"
            "record A:\n    value: Option[Result[B,Error]]\n"
            "record B:\n    value: A\n",
            ("A", "B", "A"),
            "field[value]/Option.payload/Result.ok",
        ),
        (
            "record Error:\n    code: UInt64\n"
            "enum Tree:\n    Branch: Result[Tree,Error]\n",
            ("Tree", "Tree"),
            "variant[Branch]/Result.ok",
        ),
        (
            "record Node:\n    next: Array[Option[Node],4]\n",
            ("Node", "Node"),
            "field[next]/Array.element/Option.payload",
        ),
    ],
)
def test_nested_inline_recursion_is_rejected_before_descriptors(
    declarations: str,
    cycle: tuple[str, ...],
    branch: str,
) -> None:
    hir = compile_structured_hir(declarations + _MAIN)
    validation = validate_recursive_layouts(hir.types)
    assert validation.accepted is False
    assert validation.minimal_cycle_path == cycle
    assert branch in (validation.diagnostic or "")
    with pytest.raises(RepresentationCompileError, match="InlineRecursiveLayout"):
        build_type_descriptors(hir)


@pytest.mark.parametrize(
    "declarations",
    [
        "record Node:\n    next: Option[Box[Node]]\n",
        "record Tree:\n    children: Vec[Tree]\n",
        "record Graph:\n    nodes: Map[Text,Box[Graph]]\n",
        "enum List:\n    Cons: Box[List]\n    Nil\n",
        (
            "record Error:\n    code: UInt64\n"
            "record Node:\n    children: Vec[Result[Node,Error]]\n"
        ),
    ],
)
def test_owning_indirection_recursion_is_accepted(declarations: str) -> None:
    hir = compile_structured_hir(declarations + _MAIN)
    validation = validate_recursive_layouts(hir.types)
    assert validation.accepted is True
    descriptors = build_type_descriptors(hir)
    declared = {item.name for item in hir.types}
    assert declared <= {item.name for item in descriptors}


def test_result_arguments_become_separate_nominal_dependencies() -> None:
    hir = compile_structured_hir(
        "record Error:\n    code: UInt64\n"
        "record Leaf:\n    value: UInt64\n"
        "record Envelope:\n    payload: Option[Result[Leaf,Error]]\n"
        + _MAIN
    )
    descriptors = {item.name: item for item in build_type_descriptors(hir)}
    envelope = descriptors["Envelope"]
    assert envelope.inline_dependencies == ("Error", "Leaf")
    assert envelope.indirect_dependencies == ()
    assert envelope.size > descriptors["Leaf"].size


def test_nested_indirection_marks_every_nominal_below_boundary_indirect() -> None:
    hir = compile_structured_hir(
        "record Error:\n    code: UInt64\n"
        "record Node:\n    children: Vec[Result[Node,Error]]\n"
        + _MAIN
    )
    node = next(item for item in build_type_descriptors(hir) if item.name == "Node")
    assert node.inline_dependencies == ()
    assert node.indirect_dependencies == ("Error", "Node")


def test_owner_map_layout_and_drop_plan_are_structural() -> None:
    hir = compile_structured_hir(
        "record Graph:\n    nodes: Map[Text,Box[Graph]]\n" + _MAIN
    )
    representation = lower_structured_hir_to_rir(hir)
    descriptors = {item.name: item for item in representation.descriptors}
    graph = descriptors["Graph"]
    map_descriptor = descriptors["Map[Text,Box[Graph]]"]
    assert graph.indirect_dependencies == ("Graph",)
    assert map_descriptor.indirect_dependencies == ("Text", "Box[Graph]")
    map_plan = next(
        item for item in representation.drop_plans
        if item.type_name == "Map[Text,Box[Graph]]"
    )
    assert map_plan.action == "map_owned_entries_then_buffers"
    assert [(item.field_name, item.type_name, item.action) for item in map_plan.children] == [
        ("key", "Text", "owner_free"),
        ("value", "Box[Graph]", "box_payload_then_free"),
    ]


def test_owner_map_operations_fail_during_hir_validation() -> None:
    source = (
        "record Graph:\n    nodes: Map[Text,Box[Graph]]\n"
        "fn update(graph: Graph, key: Text) -> Unit:\n"
        "    graph.nodes.get(key)\n"
    )
    with pytest.raises(StructuredHIRCompileError, match="MapOperationsRequireScalarValues"):
        compile_structured_hir(source, entry_function="update")


def test_layout_parse_failures_are_rejected_before_descriptor_build() -> None:
    hir = compile_structured_hir("record Outer:\n    value: UInt64\n" + _MAIN)
    field = hir.types[0].fields[0]
    malformed_type = replace(field, type_name="Option[UInt64")
    malformed_decl = replace(hir.types[0], fields=(malformed_type,))
    validation = validate_recursive_layouts((malformed_decl,))
    assert validation.accepted is False
    assert validation.minimal_cycle_path == ()
    assert validation.diagnostic is not None
    assert validation.diagnostic.startswith("MalformedLayoutType: Option[UInt64")
    with pytest.raises(ValueError, match="field type identity does not match spelling"):
        replace(hir, types=(malformed_decl,))


def test_qualified_nominal_names_remain_distinct_layout_dependencies() -> None:
    source = (
        "record Merlo_left__Item:\n    value: UInt64\n"
        "record Merlo_right__Item:\n    value: UInt64\n"
        "record Pair:\n"
        "    left: Merlo_left__Item\n"
        "    right: Merlo_right__Item\n"
        + _MAIN
    )
    representation = lower_structured_hir_to_rir(compile_structured_hir(source))
    descriptors = {item.name: item for item in representation.descriptors}

    assert descriptors["Pair"].inline_dependencies == (
        "Merlo_left__Item",
        "Merlo_right__Item",
    )
    assert (
        descriptors["Merlo_left__Item"].source_type_identity
        != descriptors["Merlo_right__Item"].source_type_identity
    )

def test_shortest_lexicographic_structural_cycle_is_invariant() -> None:
    declarations = (
        "record A:\n    zed: B\n    alpha: C\n"
        "record B:\n    back: A\n"
        "record C:\n    back: A\n"
    )
    reordered = (
        "record C:\n    back: A\n"
        "record B:\n    back: A\n"
        "record A:\n    zed: B\n    alpha: C\n"
    )
    unrelated = "record Unrelated:\n    value: UInt64\n"
    expected = (
        "InlineRecursiveLayout: A --field[zed]--> B "
        "--field[back]--> A; add Box or Vec indirection"
    )
    results = [
        validate_recursive_layouts(compile_structured_hir(source + _MAIN).types)
        for source in (declarations, reordered, unrelated + declarations)
    ]
    assert all(item.minimal_cycle_path == ("A", "B", "A") for item in results)
    assert all(item.diagnostic == expected for item in results)
    graphs = [dict(item.inline_graph) for item in results]
    assert graphs[0] == graphs[1]
    assert {name: graphs[2][name] for name in graphs[0]} == graphs[0]


def test_nested_layout_hir_rir_json_and_drop_plans_are_deterministic() -> None:
    source = (
        "record Error:\n    code: UInt64\n"
        "record Node:\n    next: Option[Box[Node]]\n"
        "record Envelope:\n    payload: Result[Node,Error]\n"
        + _MAIN
    )
    first_hir = compile_structured_hir(source)
    restored_hir = StructuredHIRProgram.from_json(first_hir.to_json())
    assert restored_hir.digest == first_hir.digest
    first_rir = lower_structured_hir_to_rir(first_hir)
    second_rir = lower_structured_hir_to_rir(restored_hir)
    assert first_rir.to_json() == second_rir.to_json()
    assert json.loads(first_rir.to_json()) == first_rir.to_dict()
    assert build_type_descriptors(first_hir) == build_type_descriptors(restored_hir)
    first_plans = build_drop_plans(first_rir.descriptors)
    second_plans = build_drop_plans(second_rir.descriptors)
    assert first_plans == second_plans
