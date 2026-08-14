from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import pytest

from research.archive.alpha1.merlo.frontend_bench import (
    generate_negative_cases,
    generate_paired_corpus,
    run_binding_comparison,
)


ROOT = Path(__file__).parents[1]


def test_paired_corpus_has_30_programs_and_thousands_of_equal_logical_links():
    corpus = generate_paired_corpus(30)
    repeated = generate_paired_corpus(30)

    assert corpus == repeated
    assert corpus.program_count == 30
    assert len(corpus.meldra_sources) == 120
    assert len(corpus.python_sources) == 90
    assert len(corpus.references) == 1440
    assert len({item.id for item in corpus.references}) == 1440
    assert {item.kind for item in corpus.references} >= {
        "parameter_type",
        "record_field",
        "typed_member",
        "import_alias_call",
        "enum_variant",
        "same_module_call",
        "cross_package_call",
    }
    assert all(item.python_target and item.meldra_target for item in corpus.references)


def test_negative_corpus_has_more_than_300_preregistered_typed_cases():
    cases = generate_negative_cases(40)
    counts = Counter(item.expected_code for item in cases)

    assert len(cases) == 360
    assert counts == {
        "UnknownBinding": 40,
        "UnknownType": 40,
        "ArgumentTypeMismatch": 40,
        "ReturnTypeMismatch": 40,
        "NonExhaustiveMatch": 40,
        "EffectInPureFunction": 40,
        "CapabilityEscalation": 40,
        "EffectNotDeclared": 40,
        "PackageCycle": 40,
    }
    assert len({item.id for item in cases}) == len(cases)


def test_equal_denominator_comparison_separates_analyzer_from_language():
    corpus, current, strong, meldra = run_binding_comparison(30)

    assert current.denominator == strong.denominator == meldra.denominator == len(
        corpus.references
    )
    assert strong.exact == strong.denominator
    assert meldra.exact == meldra.denominator
    assert strong.wrong_target == strong.uncertain == strong.missing == 0
    assert meldra.wrong_target == meldra.uncertain == meldra.missing == 0
    assert current.exact < strong.exact
    assert current.missing > 0


def test_support_profile_is_frozen_and_not_mutated_by_corpus_generation():
    path = ROOT / "tools" / "benchmarks" / "merlo" / "benchmarks" / "meldra_stage04_support_profile.json"
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    generate_paired_corpus(30)
    generate_negative_cases(30)

    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("count", (0, 29, 51, 100))
def test_corpus_size_outside_preregistered_range_is_rejected(count: int):
    with pytest.raises(ValueError, match="30-50"):
        generate_paired_corpus(count)
