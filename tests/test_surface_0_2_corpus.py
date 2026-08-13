from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from merlo.surface_challenge import (
    CATEGORIES,
    SurfaceChallengeError,
    load_locked_corpus,
    quantile,
    significant_tokens,
    validate_locked_corpus,
)


def test_surface_corpus_is_preregistered_balanced_and_python_only() -> None:
    corpus = load_locked_corpus()

    assert len(corpus.cases) == 100
    assert {case.category for case in corpus.cases} == set(CATEGORIES)
    assert {
        category: sum(case.category == category for case in corpus.cases)
        for category in CATEGORIES
    } == {category: 10 for category in CATEGORIES}
    assert {case.repository for case in corpus.cases} == {
        "boltons",
        "click",
        "flask",
        "httpx",
        "pluggy",
    }
    assert all(case.python_source and case.merlo_source is None for case in corpus.cases)
    assert all(len(case.commit) == 40 for case in corpus.cases)
    assert all(case.source_sha256 == hashlib.sha256(case.python_source.encode()).hexdigest() for case in corpus.cases)
    assert len(corpus.corpus_sha256) == len(corpus.protocol_sha256) == 64


def test_neutral_tokenizer_ignores_comments_docstrings_and_layout() -> None:
    python = 'def add(a, b):\n    """docs"""\n    return a + b  # note\n'
    merlo = "add(a, b) =\n    a + b\n"

    assert significant_tokens(python, language="python") == (
        "def",
        "add",
        "(",
        "a",
        ",",
        "b",
        ")",
        ":",
        "return",
        "a",
        "+",
        "b",
    )
    assert significant_tokens(merlo, language="merlo") == (
        "add",
        "(",
        "a",
        ",",
        "b",
        ")",
        "=",
        "a",
        "+",
        "b",
    )


def test_quantiles_use_preregistered_nearest_rank() -> None:
    values = list(range(1, 101))

    assert quantile(values, 0.50) == 50.0
    assert quantile(values, 0.75) == 75.0
    with pytest.raises(ValueError, match="requires samples"):
        quantile([], 0.5)


def test_corpus_validation_rejects_source_and_protocol_tampering() -> None:
    corpus = load_locked_corpus()
    first = corpus.cases[0]

    with pytest.raises(SurfaceChallengeError, match="source hash"):
        validate_locked_corpus(
            replace(corpus, cases=(replace(first, python_source=first.python_source + "\n"), *corpus.cases[1:]))
        )
    with pytest.raises(SurfaceChallengeError, match="protocol hash"):
        validate_locked_corpus(replace(corpus, protocol_sha256="0" * 64))


def test_locked_json_contains_no_translation_fields() -> None:
    payload = json.loads(Path("benchmarks/surface_0_2/corpus.json").read_text(encoding="utf-8"))

    assert all("merlo_source" not in case and "canonical_source" not in case for case in payload["cases"])
