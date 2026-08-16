from __future__ import annotations

from dataclasses import replace

from types import SimpleNamespace

import pytest

import merlo.smt_backend as smt_backend
from merlo.obligation_ir import build_obligation_ir
from merlo.smt_backend import SMTStatus, verify_smt
from merlo.structured_hir_v2 import compile_canonical_hir
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface


def _program(source: str):
    canonical = elaborate_surface(parse_surface(source)).canonical
    hir = compile_canonical_hir(canonical)
    return hir, build_obligation_ir(hir)


_SOURCE = (
    "fn identity(value: Byte) -> Byte:\n"
    "    ensure result == value\n"
    "    value\n\n"
    "fn main(input: BytesView) -> Byte:\n"
    "    Byte(0)\n"
)


class _Value:
    def __init__(self, value):
        self.value = value

    def as_long(self) -> int:
        return int(self.value)


class _Model:
    def __init__(self, values):
        self.values = values

    def eval(self, symbol, model_completion=True):
        assert model_completion
        return _Value(self.values[symbol])


class _Solver:
    def __init__(self, outcome, values):
        self.outcome = outcome
        self.values = values
        self.options = None
        self.query = None

    def set(self, **options) -> None:
        self.options = options

    def from_string(self, query: str) -> None:
        self.query = query

    def check(self):
        return self.outcome

    def model(self):
        return _Model(self.values)

    def reason_unknown(self) -> str:
        return "timeout"


def _z3(outcome: str, values=None):
    created = []

    def solver():
        value = _Solver(outcome, values or {})
        created.append(value)
        return value

    module = SimpleNamespace(
        Solver=solver,
        sat="sat",
        unsat="unsat",
        Bool=lambda name: name,
        Int=lambda name: name,
        is_true=lambda value: bool(value.value),
        get_version_string=lambda: "test-z3",
    )
    return module, created


def test_disabled_backend_emits_deterministic_query_without_solving() -> None:
    hir, obligations = _program(_SOURCE)

    first = verify_smt(hir, obligations)
    second = verify_smt(hir, obligations)

    assert first.to_json() == second.to_json()
    result = first.results[0]
    assert result.status == SMTStatus.DISABLED
    assert result.query_smt2 is not None
    assert "(declare-const p0 Int)" in result.query_smt2
    assert "(<= 0 p0)" in result.query_smt2
    assert result.query_sha256 is not None


def test_z3_unsat_proves_negated_postcondition_query() -> None:
    hir, obligations = _program(_SOURCE)
    z3, created = _z3("unsat")

    report = verify_smt(
        hir,
        obligations,
        backend="z3",
        timeout_ms=250,
        z3_module=z3,
    )

    assert report.backend_version == "test-z3"
    assert report.results[0].status == SMTStatus.PROVEN
    assert created[0].options == {"timeout": 250, "random_seed": 0}
    assert created[0].query == report.results[0].query_smt2


def test_z3_sat_returns_typed_counterexample_inputs() -> None:
    hir, obligations = _program(_SOURCE)
    z3, _created = _z3("sat", {"p0": 7})

    report = verify_smt(
        hir,
        obligations,
        backend="z3",
        z3_module=z3,
    )

    result = report.results[0]
    assert result.status == SMTStatus.REFUTED
    assert result.counterexample is not None
    assert result.counterexample.inputs == (("value", 7),)


def test_missing_z3_is_explicitly_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hir, obligations = _program(_SOURCE)
    monkeypatch.setattr(smt_backend, "_load_z3", lambda: None)

    report = verify_smt(hir, obligations, backend="z3")

    assert report.results[0].status == SMTStatus.UNAVAILABLE
    assert report.results[0].reason == "z3-solver is not installed"


def test_calls_are_unsupported_instead_of_assumed_pure() -> None:
    hir, obligations = _program(
        "fn helper(value: Byte) -> Byte:\n"
        "    value\n\n"
        "fn caller(value: Byte) -> Byte:\n"
        "    ensure result == value\n"
        "    helper(value)\n\n"
        "fn main(input: BytesView) -> Byte:\n"
        "    Byte(0)\n"
    )

    report = verify_smt(hir, obligations)

    assert report.results[0].status == SMTStatus.UNSUPPORTED
    assert report.results[0].query_smt2 is None
    assert report.results[0].reason is not None
    assert "DirectCall" in report.results[0].reason


def test_unknown_solver_result_never_proves() -> None:
    hir, obligations = _program(_SOURCE)
    z3, _created = _z3("unknown")

    report = verify_smt(
        hir,
        obligations,
        backend="z3",
        z3_module=z3,
    )

    assert report.results[0].status == SMTStatus.UNKNOWN
    assert report.results[0].reason == "timeout"


def test_division_query_excludes_trapping_zero_divisors() -> None:
    hir, obligations = _program(
        "fn divide(value: Byte, divisor: Byte) -> Byte:\n"
        "    ensure result <= value\n"
        "    value // divisor\n\n"
        "fn main(input: BytesView) -> Byte:\n"
        "    Byte(0)\n"
    )

    report = verify_smt(hir, obligations)

    assert report.results[0].query_smt2 is not None
    assert "(distinct p1 0)" in report.results[0].query_smt2


def test_signed_division_is_unsupported_without_exact_semantics() -> None:
    hir, obligations = _program(
        "fn divide(value: Int64, divisor: Int64) -> Int64:\n"
        "    ensure result <= value\n"
        "    value // divisor\n\n"
        "fn main(input: BytesView) -> Int64:\n"
        "    Int64(0)\n"
    )

    report = verify_smt(hir, obligations)

    assert report.results[0].status == SMTStatus.UNSUPPORTED
    assert report.results[0].reason == (
        "UnsupportedSignedSMTDivision: FloorDiv"
    )


def test_trapping_postcondition_expression_is_unsupported() -> None:
    hir, obligations = _program(
        "fn identity(value: Byte) -> Byte:\n"
        "    ensure result + Byte(1) > result\n"
        "    value\n\n"
        "fn main(input: BytesView) -> Byte:\n"
        "    Byte(0)\n"
    )

    report = verify_smt(hir, obligations)
    result = report.results[0]

    assert result.status == SMTStatus.UNSUPPORTED
    assert result.query_smt2 is None
    assert result.reason == "PostconditionMayTrap"

def test_smt_report_rejects_noncanonical_result_order() -> None:
    hir, obligations = _program(
        "fn identity(value: Byte) -> Byte:\n"
        "    ensure result == value\n"
        "    ensure result >= Byte(0)\n"
        "    value\n\n"
        "fn main(input: BytesView) -> Byte:\n"
        "    Byte(0)\n"
    )
    report = verify_smt(hir, obligations)

    with pytest.raises(ValueError, match="SMTResultsNotCanonical"):
        replace(report, results=tuple(reversed(report.results)))


def test_smt_path_limit_degrades_to_unsupported() -> None:
    hir, obligations = _program(
        "fn choose(flag: Bool) -> Byte:\n"
        "    ensure result >= Byte(0)\n"
        "    if flag:\n"
        "        return Byte(1)\n"
        "    else:\n"
        "        return Byte(0)\n\n"
        "fn main(input: BytesView) -> Byte:\n"
        "    Byte(0)\n"
    )

    report = verify_smt(hir, obligations, max_paths=1)

    assert report.results[0].status == SMTStatus.UNSUPPORTED
    assert report.results[0].reason == "SMTPathLimitExceeded: 1"


def test_invalid_backend_and_timeout_are_rejected() -> None:
    hir, obligations = _program(_SOURCE)

    with pytest.raises(ValueError, match="UnknownSMTBackend"):
        verify_smt(hir, obligations, backend="cvc5")
    with pytest.raises(ValueError, match="InvalidSMTTimeout"):
        verify_smt(hir, obligations, timeout_ms=0)
    with pytest.raises(ValueError, match="InvalidSMTPathLimit"):
        verify_smt(hir, obligations, max_paths=0)
