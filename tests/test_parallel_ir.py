from __future__ import annotations

import json
from pathlib import Path

import pytest
from merlo.compiler import compile_project
from merlo.representation_mir import (
    GeneralMIRBlock,
    GeneralMIRFunction,
    GeneralMIRInstruction,
    GeneralMIRTerminator,
    GeneralPerformanceMIR,
)
from merlo.structured_hir_v2 import SourceSpan
from merlo.type_arena import TypeContextBuilder

from merlo.parallel_ir import (
    ParallelIR,
    ParallelOperation,
    ParallelValueType,
    fuse_parallel_ir,
    lower_performance_mir,
)


_ROOT = Path(__file__).resolve().parents[1]


_DIGEST = "1" * 64


def _operation(
    operation_id: str,
    kind: str,
    *,
    inputs: tuple[str, ...] = (),
    result: str | None = None,
    dependencies: tuple[str, ...] = (),
    effects: tuple[str, ...] = (),
) -> ParallelOperation:
    return ParallelOperation(
        operation_id=operation_id,
        kind=kind,
        inputs=inputs,
        result=result,
        result_type=ParallelValueType.vector(
            "UInt64",
            4,
        ),
        dependencies=dependencies,
        ownership_mode="owned",
        effects=effects,
        attributes=(
            ("execution", "vector"),
            ("independent", True),
        ),
        provenance=(operation_id,),
        function="work",
        block="entry",
    )


def test_compile_project_emits_parallel_ir_artifact() -> None:
    compilation = compile_project(
        _ROOT / "examples" / "automation",
        require_interface_lock=False,
    )
    artifact = compilation.artifacts["parallel_ir"]

    assert artifact.parent_digest == compilation.artifacts["optimized_mir"].digest
    assert artifact.content == compilation.parallel_ir.to_json()
    assert artifact.contract == compilation.parallel_ir.contract


def test_parallel_ir_roundtrip_and_pure_fusion() -> None:
    first = _operation(
        "first",
        "map",
        inputs=("input",),
        result="mapped",
    )
    second = _operation(
        "second",
        "zip",
        inputs=("mapped", "other"),
        result="result",
        dependencies=("first",),
    )
    original = ParallelIR(
        _DIGEST,
        (first, second),
    )

    fused = fuse_parallel_ir(original)

    assert len(fused.operations) == 1
    operation = fused.operations[0]
    assert operation.kind == "fused_map_zip"
    assert operation.inputs == ("input", "other")
    assert operation.provenance == (
        "first",
        "second",
    )
    assert ParallelIR.from_json(
        fused.to_json()
    ) == fused
    assert fused.to_json() == fuse_parallel_ir(
        original
    ).to_json()
def test_attributes_are_deeply_immutable_and_large_graph_is_iterative() -> None:
    operation = ParallelOperation(
        "root",
        "scalar",
        attributes=(("proof", {"lanes": [1, 2]}),),
    )
    with pytest.raises(TypeError):
        operation.attributes[0][1]["lanes"] = ()
    projected = operation.attribute_map
    projected["proof"]["lanes"].append(3)
    assert operation.to_dict()["attributes"]["proof"]["lanes"] == [1, 2]

    operations = tuple(
        ParallelOperation(
            f"node-{index}",
            "scalar",
            dependencies=(() if index == 0 else (f"node-{index - 1}",)),
        )
        for index in range(2_000)
    )
    assert len(ParallelIR(_DIGEST, operations).operations) == 2_000




def test_effects_cycles_and_tampering_reject() -> None:
    with pytest.raises(
        ValueError,
        match="EffectfulVectorization",
    ):
        _operation(
            "effectful",
            "map",
            effects=("console.write",),
        )

    cyclic_a = _operation(
        "a",
        "map",
        dependencies=("b",),
    )
    cyclic_b = _operation(
        "b",
        "zip",
        dependencies=("a",),
    )
    with pytest.raises(
        ValueError,
        match="DependencyCycle",
    ):
        ParallelIR(
            _DIGEST,
            (cyclic_a, cyclic_b),
        )

    valid = ParallelIR(
        _DIGEST,
        (
            _operation(
                "only",
                "map",
                result="result",
            ),
        ),
    )
    payload = json.loads(valid.to_json())
    payload["operations"][0]["kind"] = "scatter"
    with pytest.raises(
        ValueError,
        match="DigestMismatch",
    ):
        ParallelIR.from_dict(payload)

def test_mir_lowering_proves_or_falls_back() -> None:
    span = SourceSpan(
        "<parallel-test>",
        1,
        1,
        1,
        2,
    )
    builder = TypeContextBuilder()
    uint64_id = builder.intern_text("UInt64")
    type_arena = builder.freeze().arena

    def instruction(
        identifier: str,
        operation: str,
        operand: str,
        result: str,
        *,
        effects: tuple[str, ...] = (),
    ) -> GeneralMIRInstruction:
        return GeneralMIRInstruction(
            identifier,
            operation,
            "UInt64",
            (operand,),
            result,
            span,
            "symbol",
            "revision",
            "owned",
            effects,
            (
                (
                    "collection_operation",
                    operation,
                ),
                ("independent", True),
                ("vector", True),
                ("lanes", 4),
            ),
            type_id=uint64_id,
            operand_type_ids=(uint64_id,),
            result_type_id=uint64_id,
        )

    def mir(
        instructions: tuple[
            GeneralMIRInstruction,
            ...,
        ],
        *,
        effects: tuple[str, ...] = (),
    ) -> GeneralPerformanceMIR:
        function = GeneralMIRFunction(
            "main",
            "symbol",
            "revision",
            (),
            "UInt64",
            effects,
            (
                GeneralMIRBlock(
                    "entry",
                    instructions,
                    GeneralMIRTerminator(
                        "return",
                        value=instructions[-1].result,
                    ),
                ),
            ),
            span,
            return_type_id=uint64_id,
        )
        return GeneralPerformanceMIR(
            _DIGEST,
            "2" * 64,
            "3" * 64,
            "4" * 64,
            "5" * 64,
            "main",
            (function,),
            type_arena=type_arena,
            type_arena_digest=type_arena.digest,
        )

    pure = lower_performance_mir(
        mir(
            (
                instruction(
                    "map",
                    "map",
                    "input",
                    "mapped",
                ),
                instruction(
                    "zip",
                    "zip",
                    "mapped",
                    "result",
                ),
            )
        )
    )
    assert [item.kind for item in pure.operations] == [
        "fused_map_zip"
    ]

    effectful = lower_performance_mir(
        mir(
            (
                instruction(
                    "map",
                    "map",
                    "input",
                    "result",
                    effects=("console.write",),
                ),
            ),
            effects=("console.write",),
        )
    )
    assert effectful.operations[0].kind == "scalar"
    assert (
        effectful.operations[0].attribute_map[
            "fallback_reason"
        ]
        == "effectful_or_unproven_independence"
    )


def test_scalar_operation_does_not_require_vector_shape() -> None:
    operation = ParallelOperation(
        operation_id="scalar",
        kind="scalar",
        result="value",
        result_type=ParallelValueType.scalar(
            "UInt64"
        ),
        attributes=(("execution", "scalar"),),
    )
    ir = ParallelIR(_DIGEST, (operation,))
    assert ir.operations[0].vectorizable is False
