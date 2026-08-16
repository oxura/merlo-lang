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
    program = parse_surface(SOURCE, path="machine-flow.mlo")
    assert isinstance(program.declarations[0], SurfaceFlow)
    assert isinstance(program.declarations[1], SurfaceMachine)
    assert isinstance(program.declarations[0].body[1], SurfaceParallel)
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
