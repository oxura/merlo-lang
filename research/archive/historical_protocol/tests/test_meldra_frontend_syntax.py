from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import pytest

from research.archive.historical_protocol.merlo.core_semantics import (
    CORE_SCHEMA_ID,
    CORE_SCHEMA_SHA256,
    CORE_SCHEMA_VERSION,
    CoreError,
    CoreProgram,
)
from research.archive.historical_protocol.merlo.frontend_syntax import (
    FRONTEND_SYNTAX_SCHEMA_VERSION,
    FrontendSyntaxError,
    lex_source,
    parse_source,
)


ROOT = Path(__file__).parents[4]


SAMPLE = b"""package shop.checkout\n\nuse shop.catalog::{Cart, Product}\nexport Order, total\n\nnewtype OrderId = Int\n\nrecord Order:\n    id: OrderId\n    total: Int\n\nenum Status:\n    Pending\n    Paid\n\nvalue multiplier: Int = 2\n\nfn total(value: Int) -> Int:\n    let scaled = value * multiplier\n    if scaled > 0:\n        scaled\n    else:\n        0\n"""


def test_core_ir_v1_schema_is_frozen_and_rejects_other_versions():
    path = ROOT / "merlo" / "core_ir_schema_v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["$id"] == CORE_SCHEMA_ID
    assert payload["properties"]["schema_version"]["const"] == CORE_SCHEMA_VERSION == 1
    assert hashlib.sha256(path.read_bytes()).hexdigest() == CORE_SCHEMA_SHA256
    with pytest.raises(CoreError, match="unsupported CoreIR schema version"):
        CoreProgram.from_dict({"schema_version": 2, "packages": []})
    with pytest.raises(CoreError, match="unsupported CoreIR schema version"):
        CoreProgram.from_dict({"schema_version": True, "packages": []})


def test_lossless_parser_round_trips_every_byte_and_is_deterministic():
    first = parse_source(SAMPLE, path="shop/checkout.meldra")
    second = parse_source(SAMPLE, path="shop/checkout.meldra")

    assert first.schema_version == FRONTEND_SYNTAX_SCHEMA_VERSION == 1
    assert first.to_source_bytes() == SAMPLE
    assert first == second
    assert first.to_dict() == second.to_dict()
    assert first.module.package_name == "shop"
    assert first.module.module_name == "checkout"
    assert first.module.exports == ("Order", "total")
    assert [item.kind for item in first.module.declarations] == [
        "newtype",
        "record",
        "enum",
        "value",
        "fn",
    ]
    assert b"".join(
        token.text.encode("utf-8")
        for token in first.tokens
        if not token.synthetic and token.kind != "EOF"
    ) == SAMPLE


def test_parser_accepts_multiline_task_capability_calls_and_match():
    source = b"""package shop.checkout\nmodule workflow\nuse shop.payments::{Payments, Receipt}\nexport checkout\n\ncapability Clock:\n    now() -> Int uses clock.now\n\nenum ResultCode:\n    Accepted(Int)\n    Rejected\n\ntask checkout(\n    amount: Int,\n    payments: cap Payments,\n) -> Receipt:\n    uses payments.charge\n    let receipt = payments.charge(amount)\n    match ResultCode.Accepted(amount):\n        Accepted(value): receipt\n        Rejected: receipt\n"""

    cst = parse_source(source, path="workflow.meldra")
    task = cst.module.declarations[-1]

    assert cst.to_source_bytes() == source
    assert cst.module.package_name == "shop.checkout"
    assert cst.module.module_name == "workflow"
    assert task.kind == "task"
    assert task.parameters[1].capability is True
    assert task.body[0].kind == "uses"
    assert task.body[-1].kind == "match"
    assert [arm.variant for arm in task.body[-1].arms] == ["Accepted", "Rejected"]


def test_lossless_roundtrip_property_over_formatting_and_comments():
    randomizer = random.Random(20260810)
    for index in range(200):
        blank = "\n" * randomizer.randint(1, 3)
        indent = " " * randomizer.choice((2, 4, 6, 8))
        comment = f" # deterministic-{index}" if index % 2 else ""
        source = (
            f"package corpus.case{index}{blank}"
            f"export calculate\n{blank}"
            f"fn calculate(value: Int) -> Int:{comment}\n"
            f"{indent}let shadow = value + {index}\n"
            f"{indent}shadow\n"
        ).encode("utf-8")
        cst = parse_source(source, path=f"case_{index}.meldra")
        assert cst.to_source_bytes() == source
        assert lex_source(source, path=f"case_{index}.meldra") == cst.tokens


def test_formatting_changes_syntax_identity_but_not_declaration_shape():
    compact = b"package p.m\nexport f\nfn f(x: Int) -> Int:\n  x\n"
    spaced = b"package p.m\n\n# trivia\nexport f\nfn f(x: Int) -> Int:\n    x  # keep\n"

    first = parse_source(compact, path="m.meldra")
    second = parse_source(spaced, path="m.meldra")

    assert first.module.syntax_id != second.module.syntax_id
    assert first.module.declarations[0].syntax_id != second.module.declarations[0].syntax_id
    assert first.module.declarations[0].name == second.module.declarations[0].name == "f"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (b"package p.m\nfn f() -> Int:\n\t1\n", "tabs are not allowed"),
        (b"package p.m\nfn f() -> Int:\n   1\n  2\n", "indentation does not match"),
        (b"package p.m\nfn f() -> Int:\n    @\n", "unexpected character"),
        (b"package p.m\nfn f() -> Int:\n    \"open\n", "unterminated string"),
    ],
)
def test_invalid_lexical_structure_is_rejected(source: bytes, message: str):
    with pytest.raises(FrontendSyntaxError, match=message):
        parse_source(source, path="invalid.meldra")
