import pytest

import merlo.surface_parser as surface_parser_module
from merlo.surface_ast import SurfaceFlow, SurfaceMachine, SurfaceParallel
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface
from merlo.structured_hir_v2 import (
    compile_canonical_hir,
)


SOURCE = """durable flow ingest(input: Text) -> Result[Text,Err]:
    fetch = read(input) timeout 1s retry 2 on Timeout idempotent by input compensate rollback(input)
    parallel:
        left = read(input)
        right = read(input)
machine Job(id: UInt64):
    state Idle
    state Running(value: Text)
    initial Idle
    invariant id >= 0
    transition start from Idle -> Running:
        uses io
        value = read(id)
"""


def test_flow_and_machine_parse_and_elaborate_to_canonical_nodes() -> None:
    assert not hasattr(surface_parser_module._Parser, "_parameters")
    program = parse_surface(SOURCE, path="machine-flow.mlo")
    assert isinstance(program.declarations[0], SurfaceFlow)
    assert isinstance(program.declarations[1], SurfaceMachine)
    assert isinstance(program.declarations[0].body[1], SurfaceParallel)
    assert program.declarations[0].parameters[0].type_name == "Text"
    assert program.declarations[0].return_type == "Result[Text,Err]"
    assert program.declarations[1].parameters[0].type_name == "UInt64"
    first = elaborate_surface(program).canonical
    second = elaborate_surface(parse_surface(SOURCE, path="machine-flow.mlo")).canonical
    assert len(first.flows) == 1
    assert len(first.machines) == 1
    assert first.semantic_hash == second.semantic_hash
    assert first.flows[0].durable is True
    assert {item.kind for item in first.flows[0].body[0].policies} == {
        "timeout", "retry", "idempotent", "compensate"
    }
    assert first.machines[0].states[1].fields == (("value", "Text"),)
    hir = compile_canonical_hir(first)
    assert len(hir.flows) == 1
    assert len(hir.machines) == 1
    assert {
        node.kind
        for node in hir.flows[0].walk()
    } == {"FlowStep", "Parallel"}
    assert {
        node.kind
        for node in hir.machines[0].walk()
    } == {"Transition"}

    changed = elaborate_surface(
        parse_surface(
            SOURCE.replace(
                "timeout 1s",
                "timeout 2s",
            ),
            path="machine-flow.mlo",
        )
    ).canonical
    changed_hir = compile_canonical_hir(changed)
    assert (
        changed_hir.flows[0].revision_id
        != hir.flows[0].revision_id
    )


def test_retry_requires_idempotency() -> None:
    source = SOURCE.replace(" idempotent by input", "")
    try:
        elaborate_surface(parse_surface(source, path="bad-flow.mlo"))
    except ValueError as exc:
        assert "RetryRequiresIdempotency" in str(exc)
    else:
        raise AssertionError("retry without idempotency was accepted")


def test_flow_step_and_policy_expressions_consume_cst_regions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        surface_parser_module,
        "lex_expression",
        lambda _source: (_ for _ in ()).throw(AssertionError("re-lexed")),
    )

    flow = parse_surface(SOURCE, path="machine-flow.mlo").declarations[0]
    assert flow.body[0].value is not None
    idempotent = next(
        policy for policy in flow.body[0].policies if policy.kind == "idempotent"
    )
    assert idempotent.expression is not None


def test_machine_members_are_decoded_from_retained_tokens() -> None:
    machine = parse_surface(
        "machine Job():\n"
        "    state Idle\n"
        "    state Running(value: Text)\n"
        "    initial Idle\n"
        "    invariant true\n"
        "    transition start from Idle | Running -> Running:\n"
        "        pass\n",
        path="retained-machine.mlo",
    ).declarations[0]

    assert isinstance(machine, SurfaceMachine)
    assert tuple(state.name for state in machine.states) == ("Idle", "Running")
    assert machine.initial == "Idle"
    assert machine.transitions[0].sources == ("Idle", "Running")
    assert machine.transitions[0].target == "Running"


def test_machine_transition_rejects_malformed_retained_source_list() -> None:
    with pytest.raises(surface_parser_module.SurfaceSyntaxError, match="InvalidTransitionSource"):
        parse_surface(
            "machine Job():\n"
            "    state Idle\n"
            "    transition start from Idle | -> Idle:\n"
            "        pass\n",
            path="bad-transition.mlo",
        )
