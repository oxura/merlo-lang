from __future__ import annotations

import base64
import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from merlo.productive_corpus import (
    CORPUS_PATH,
    REQUIRED_FAMILIES,
    corpus_sha256,
    generate_productive_corpus,
    load_productive_corpus,
    serialize_productive_corpus,
    validate_productive_corpus,
)


ROOT = Path(__file__).parents[1]
EXPECTED_COUNTS = {
    ("ndjson", True): 200,
    ("ndjson", False): 120,
    ("csv", True): 200,
    ("csv", False): 120,
    ("grep", True): 200,
    ("grep", False): 120,
    ("merlo", False): 400,
}

EXPECTED_FAMILIES = {
    ("ndjson", True): {
        "empty",
        "one-line",
        "large",
        "unicode",
        "map-collision",
        "map-growth",
        "map-duplicate",
    },
    ("ndjson", False): {
        "invalid-utf8",
        "early-parse-error",
        "late-parse-error",
        "cli-failure",
    },
    ("csv", True): {
        "empty",
        "one-line",
        "large",
        "unicode",
        "map-collision",
        "map-growth",
        "map-duplicate",
    },
    ("csv", False): {
        "invalid-utf8",
        "early-parse-error",
        "late-parse-error",
        "cli-failure",
    },
    ("grep", True): {
        "empty",
        "one-line",
        "large",
        "unicode",
        "map-collision",
        "map-growth",
        "map-duplicate",
    },
    ("grep", False): {"invalid-utf8", "cli-failure"},
    ("merlo", False): {
        "missing-capability",
        "pure-effect-violation",
        "close-every-exit",
        "stale-line-view",
        "private-module-access",
        "cyclic-import",
        "public-interface-drift",
    },
}


def test_committed_corpus_has_exact_counts_and_validates_its_digest() -> None:
    corpus = load_productive_corpus(ROOT / CORPUS_PATH)

    observed = Counter(
        (case["kind"], case["validity"])
        for case in corpus["cases"]
    )
    assert observed == EXPECTED_COUNTS
    assert len(corpus["cases"]) == 1_360
    assert corpus["sha256"] == corpus_sha256(corpus)
    assert corpus["sha256"] == "a565dcfa6a64f22279e845c90d1f9200a5c3dcf71ee53044e026ae904cacc5da"


def test_regeneration_is_byte_for_byte_deterministic() -> None:
    committed_text = (ROOT / CORPUS_PATH).read_text(encoding="utf-8")
    committed = json.loads(committed_text)

    first = generate_productive_corpus()
    second = generate_productive_corpus()

    assert first == second == committed
    assert serialize_productive_corpus(first) == committed_text


def test_cases_are_unique_and_cover_every_required_family() -> None:
    corpus = load_productive_corpus(ROOT / CORPUS_PATH)
    cases = corpus["cases"]
    observed_families = {
        key: {
            case["family"]
            for case in cases
            if (case["kind"], case["validity"]) == key
        }
        for key in EXPECTED_FAMILIES
    }

    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["seed"] for case in cases}) == len(cases)
    assert observed_families == EXPECTED_FAMILIES
    assert set().union(*observed_families.values()) == REQUIRED_FAMILIES
    assert all(case["template"] for case in cases)
    assert {
        (item["id"], item["family"], item["kind"], item["validity"])
        for item in corpus["generator"]["templates"]
    } == {
        (case["template"], case["family"], case["kind"], case["validity"])
        for case in cases
    }

    common_fields = {
        "id",
        "kind",
        "family",
        "template",
        "seed",
        "validity",
        "provenance",
        "expected",
    }
    for case in cases:
        content_field = "merlo_source" if case["kind"] == "merlo" else "payload_b64"
        assert set(case) == common_fields | {content_field}
        assert case["expected"]["outcome"]
        assert case["expected"]["summary"]


def test_invalid_utf8_payloads_preserve_the_original_bytes() -> None:
    corpus = load_productive_corpus(ROOT / CORPUS_PATH)
    cases = [case for case in corpus["cases"] if case["family"] == "invalid-utf8"]

    assert {case["kind"] for case in cases} == {"ndjson", "csv", "grep"}
    assert cases
    for case in cases:
        payload = base64.b64decode(case["payload_b64"], validate=True)
        assert base64.b64encode(payload).decode("ascii") == case["payload_b64"]
        with pytest.raises(UnicodeDecodeError):
            payload.decode("utf-8")


def test_every_case_is_generated_and_no_external_provenance_is_claimed() -> None:
    corpus = load_productive_corpus(ROOT / CORPUS_PATH)

    assert corpus["provenance"] == "generated"
    assert all(case["provenance"] == "generated" for case in corpus["cases"])
    assert "external" not in json.dumps(corpus, sort_keys=True).lower()


def test_validation_rejects_tampering_without_rewriting_the_digest() -> None:
    corpus = generate_productive_corpus()
    tampered = copy.deepcopy(corpus)
    tampered["cases"][0]["expected"]["summary"] = "tampered"

    with pytest.raises(ValueError, match="sha256"):
        validate_productive_corpus(tampered)


def _resign(corpus: dict[str, object]) -> None:
    corpus["sha256"] = corpus_sha256(corpus)


def test_validation_rejects_incomplete_case_plan_with_matching_digest() -> None:
    incomplete = copy.deepcopy(generate_productive_corpus())
    incomplete["cases"].pop()
    _resign(incomplete)

    with pytest.raises(ValueError, match="case order"):
        validate_productive_corpus(incomplete)


def test_validation_rejects_duplicate_ids_with_matching_digest() -> None:
    duplicate = copy.deepcopy(generate_productive_corpus())
    duplicate["cases"][1]["id"] = duplicate["cases"][0]["id"]
    _resign(duplicate)

    with pytest.raises(ValueError, match="ids must be unique"):
        validate_productive_corpus(duplicate)


def test_validation_rejects_family_drift_with_matching_digest() -> None:
    drifted = copy.deepcopy(generate_productive_corpus())
    drifted["cases"][0]["family"] = "one-line"
    _resign(drifted)

    with pytest.raises(ValueError, match="deterministic template"):
        validate_productive_corpus(drifted)


def test_validation_rejects_valid_utf8_in_invalid_family_with_matching_digest() -> None:
    repaired = copy.deepcopy(generate_productive_corpus())
    case = next(item for item in repaired["cases"] if item["family"] == "invalid-utf8")
    case["payload_b64"] = base64.b64encode(b"valid UTF-8\n").decode("ascii")
    _resign(repaired)

    with pytest.raises(ValueError, match="unexpectedly decodes"):
        validate_productive_corpus(repaired)


def test_validation_rejects_outside_provenance_with_matching_digest() -> None:
    outside = copy.deepcopy(generate_productive_corpus())
    outside["cases"][0]["provenance"] = "external"
    _resign(outside)

    with pytest.raises(ValueError, match="provenance"):
        validate_productive_corpus(outside)
