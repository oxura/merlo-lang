"""Differential contract for the Stage 0.4 / Stage 0.6P overlap subset."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from .frontend_evaluator import ReferenceEvaluator
from .frontend_semantics import compile_frontend
from .native_differential import evaluate_hir
from .native_hir import compile_native_hir


STAGE04_SOURCE = """package overlap.main
export helper, compute
fn helper(value: Int) -> Int:
    value + 1
fn compute(value: Int) -> Int:
    helper(value) * 2
"""

STAGE06P_SOURCE = """fn helper(value: Int64) -> Int64:
    value + 1
fn compute(value: Int64) -> Int64:
    helper(value) * 2
"""

_TYPE_EQUIVALENCE = {"Int": "Int64", "Bool": "Bool"}
_NATIVE_BUILTINS = frozenset({"Int64", "UInt64", "Float32", "Float64", "Bool"})


def _stage04_contracts(compilation: Any) -> dict[str, dict[str, Any]]:
    contracts = {}
    for symbol in compilation.hir.symbols:
        if symbol.kind != "fn":
            continue
        contracts[symbol.name] = {
            "parameters": [
                _TYPE_EQUIVALENCE.get(item["type"], item["type"])
                for item in symbol.contract["args"]
            ],
            "return_type": _TYPE_EQUIVALENCE.get(
                symbol.contract["returns"], symbol.contract["returns"]
            ),
            "effects": list(symbol.effects),
            "capabilities": list(symbol.capabilities),
        }
    return contracts


def _native_contracts(hir: Any) -> dict[str, dict[str, Any]]:
    return {
        symbol.name: {
            "parameters": list(symbol.parameter_types),
            "return_type": symbol.return_type,
            "effects": list(symbol.effects),
            "capabilities": list(symbol.capabilities),
        }
        for symbol in hir.symbols
        if symbol.kind == "function"
    }


def _stage04_references(compilation: Any) -> Counter[tuple[str, str]]:
    return Counter(
        (
            reference.spelling,
            "Call" if reference.target_symbol_id is not None else "Read",
        )
        for reference in compilation.hir.references
    )


def _native_references(hir: Any) -> Counter[tuple[str, str]]:
    return Counter(
        (reference.spelling, reference.usage)
        for reference in hir.references
        if reference.spelling not in _NATIVE_BUILTINS
    )


def compare_stage04_overlap(
    inputs: Iterable[int] = (0, 1, 20, -7),
) -> dict[str, Any]:
    """Compare the old and native frontends without sharing parser logic."""

    arguments = tuple(inputs)
    old = compile_frontend({"overlap.meldra": STAGE04_SOURCE})
    native = compile_native_hir(
        STAGE06P_SOURCE,
        path="overlap_native.meldra",
        entry_function="compute",
    )
    old_contracts = _stage04_contracts(old)
    native_contracts = _native_contracts(native)
    old_references = _stage04_references(old)
    native_references = _native_references(native)

    values = []
    old_evaluator = ReferenceEvaluator(old)
    for argument in arguments:
        old_result = old_evaluator.evaluate("overlap.main.compute", (argument,))
        native_result = evaluate_hir(native, (argument,))
        values.append(
            {
                "argument": argument,
                "stage04": old_result.value,
                "native": native_result.return_value,
                "stage04_effect_trace": [event.to_dict() for event in old_result.effect_trace],
                "native_effect_trace": list(native_result.effect_trace),
                "equal": (
                    old_result.value == native_result.return_value
                    and not old_result.effect_trace
                    and not native_result.effect_trace
                ),
            }
        )

    contracts_equal = old_contracts == native_contracts
    references_equal = old_references == native_references
    return {
        "schema_version": 1,
        "kind": "MeldraStage04NativeOverlap",
        "independent_frontends": True,
        "contracts_equal": contracts_equal,
        "references_equal": references_equal,
        "values_equal": all(item["equal"] for item in values),
        "ok": contracts_equal and references_equal and all(item["equal"] for item in values),
        "stage04_contracts": old_contracts,
        "native_contracts": native_contracts,
        "stage04_references": [list(item) + [count] for item, count in sorted(old_references.items())],
        "native_references": [list(item) + [count] for item, count in sorted(native_references.items())],
        "values": values,
    }


__all__ = [
    "STAGE04_SOURCE",
    "STAGE06P_SOURCE",
    "compare_stage04_overlap",
]
