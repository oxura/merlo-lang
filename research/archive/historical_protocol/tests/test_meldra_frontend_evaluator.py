from __future__ import annotations

import pytest

from research.archive.historical_protocol.merlo.frontend_evaluator import (
    EnumValue,
    EvaluationError,
    NewtypeValue,
    RecordValue,
    ReferenceEvaluator,
)
from research.archive.historical_protocol.merlo.frontend_semantics import compile_frontend

from test_meldra_frontend_semantics import CHECKOUT, MODEL, PAYMENTS


def _compilation():
    return compile_frontend(
        {
            "model.meldra": MODEL,
            "payments.meldra": PAYMENTS,
            "checkout.meldra": CHECKOUT,
        }
    )


def _user(compilation, *, active: bool, score: int = 4):
    user = compilation.hir.symbol("shop.model.User")
    user_id = compilation.hir.symbol("shop.model.UserId")
    status = compilation.hir.symbol("shop.model.Status")
    return RecordValue(
        user.symbol_id,
        (
            ("id", NewtypeValue(user_id.symbol_id, 7)),
            ("score", score),
            ("name", "Ada"),
            (
                "status",
                EnumValue(
                    status.symbol_id,
                    "Active" if active else "Disabled",
                ),
            ),
        ),
    )


def test_reference_evaluator_executes_values_functions_match_and_branches():
    compilation = _compilation()
    evaluator = ReferenceEvaluator(compilation)

    active = evaluator.evaluate("shop.checkout.total", (_user(compilation, active=True),))
    disabled = evaluator.evaluate(
        "shop.checkout.total", (_user(compilation, active=False),)
    )
    status = compilation.hir.symbol("shop.model.Status")
    label = evaluator.evaluate(
        "shop.model.label", (EnumValue(status.symbol_id, "Disabled"),)
    )

    assert active.value == 5
    assert disabled.value == 0
    assert label.value == "disabled"
    assert active.effect_trace == disabled.effect_trace == label.effect_trace == ()
    assert active.executed_symbol_ids == (
        compilation.hir.symbol("shop.checkout.total").symbol_id,
    )


def test_task_evaluation_requires_capability_and_records_exact_effect_trace():
    compilation = _compilation()
    evaluator = ReferenceEvaluator(
        compilation,
        handlers={"payments.charge": lambda amount: amount * 10},
    )
    payments = evaluator.capability("shop.payments.Payments")

    result = evaluator.evaluate(
        "shop.checkout.checkout",
        {
            "user": _user(compilation, active=True, score=2),
            "payments": payments,
        },
    )
    receipt = compilation.hir.symbol("shop.payments.Receipt")

    assert result.value == NewtypeValue(receipt.symbol_id, 30)
    assert len(result.effect_trace) == 1
    event = result.effect_trace[0]
    assert event.index == 0
    assert event.effect == "payments.charge"
    assert event.arguments == (3,)
    assert event.result == result.value
    assert result.executed_symbol_ids == (
        compilation.hir.symbol("shop.checkout.checkout").symbol_id,
        compilation.hir.symbol("shop.checkout.total").symbol_id,
        compilation.hir.symbol("shop.payments.Payments$charge").symbol_id,
    )
    assert result.to_json() == evaluator.evaluate(
        "shop.checkout.checkout",
        {
            "user": _user(compilation, active=True, score=2),
            "payments": payments,
        },
    ).to_json()


def test_evaluator_has_no_ambient_effect_fallbacks():
    compilation = _compilation()
    no_handler = ReferenceEvaluator(compilation)
    payments = no_handler.capability("shop.payments.Payments")

    with pytest.raises(EvaluationError, match="no evaluator handler"):
        no_handler.evaluate(
            "shop.checkout.checkout",
            {
                "user": _user(compilation, active=True),
                "payments": payments,
            },
        )
    with pytest.raises(EvaluationError, match="requires CapabilityValue"):
        ReferenceEvaluator(
            compilation, handlers={"payments.charge": lambda amount: amount}
        ).evaluate(
            "shop.checkout.checkout",
            {
                "user": _user(compilation, active=True),
                "payments": object(),
            },
        )


def test_evaluator_rejects_non_executable_symbols_and_noncanonical_results():
    compilation = _compilation()
    evaluator = ReferenceEvaluator(
        compilation,
        handlers={"payments.charge": lambda amount: object()},
    )

    with pytest.raises(EvaluationError, match="not executable"):
        evaluator.evaluate("shop.model.User")
    with pytest.raises(EvaluationError, match="non-canonical"):
        evaluator.evaluate(
            "shop.checkout.checkout",
            {
                "user": _user(compilation, active=True),
                "payments": evaluator.capability("shop.payments.Payments"),
            },
        )
