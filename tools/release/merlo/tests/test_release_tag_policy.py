from __future__ import annotations

import json
from typing import Any

import pytest

from tools.release.merlo.release_tag_policy import (
    COMPARISON_PAYLOAD_ENV,
    MAIN_PAYLOAD_ENV,
    REF_PAYLOAD_ENV,
    TAG_PAYLOAD_ENV,
    ReleaseTagPolicyError,
    main,
    verify_release_tag,
)


EXPECTED_COMMIT = "a" * 40


def _payloads() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        {"object": {"type": "tag", "sha": "b" * 40}},
        {
            "verification": {"verified": True, "reason": "valid"},
            "object": {"type": "commit", "sha": EXPECTED_COMMIT},
        },
        {"object": {"type": "commit", "sha": "c" * 40}},
        {
            "status": "ahead",
            "merge_base_commit": {"sha": EXPECTED_COMMIT},
            "behind_by": 0,
        },
    )


def test_valid_annotated_tag_is_a_main_ancestor() -> None:
    assert verify_release_tag(*_payloads(), EXPECTED_COMMIT) is None


@pytest.mark.parametrize(
    ("case", "expected_message"),
    (
        ("lightweight", "annotated tag"),
        ("unsigned", "verified=true"),
        ("invalid-reason", "reason must be valid"),
        ("moved-target", "event commit"),
        ("non-main", "main history"),
        ("diverged", "ahead of or identical"),
        ("invalid-main-ref", "main ref must point to a commit"),
    ),
)
def test_release_tag_policy_rejects_invalid_payloads(case: str, expected_message: str) -> None:
    payloads = list(_payloads())
    if case == "lightweight":
        payloads[0]["object"]["type"] = "commit"
    elif case == "unsigned":
        payloads[1]["verification"]["verified"] = False
    elif case == "invalid-reason":
        payloads[1]["verification"]["reason"] = "expired"
    elif case == "moved-target":
        payloads[1]["object"]["sha"] = "d" * 40
    elif case == "non-main":
        payloads[3]["merge_base_commit"]["sha"] = "e" * 40
    elif case == "diverged":
        payloads[3]["status"] = "diverged"
    elif case == "invalid-main-ref":
        payloads[2]["object"]["type"] = "tag"
    else:  # pragma: no cover - the parameter list is exhaustive.
        raise AssertionError(case)

    with pytest.raises(ReleaseTagPolicyError, match=expected_message):
        verify_release_tag(*payloads, EXPECTED_COMMIT)


@pytest.mark.parametrize("verified", (1, "true"))
def test_verified_flag_requires_json_boolean_true(verified: object) -> None:
    payloads = list(_payloads())
    payloads[1]["verification"]["verified"] = verified
    with pytest.raises(ReleaseTagPolicyError, match="verified=true"):
        verify_release_tag(*payloads, EXPECTED_COMMIT)


@pytest.mark.parametrize("behind_by", (True, 0.0, "0", -1))
def test_behind_by_requires_integer_zero(behind_by: object) -> None:
    payloads = list(_payloads())
    payloads[3]["behind_by"] = behind_by
    with pytest.raises(ReleaseTagPolicyError, match="must not be behind"):
        verify_release_tag(*payloads, EXPECTED_COMMIT)


@pytest.mark.parametrize(
    "malformed",
    (
        (None, *_payloads()[1:]),
        ({}, *_payloads()[1:]),
        (_payloads()[0], None, *_payloads()[2:]),
        (_payloads()[0], _payloads()[1], {"object": {}}, _payloads()[3]),
        (_payloads()[0], _payloads()[1], _payloads()[2], {"status": "ahead"}),
    ),
)
def test_malformed_payloads_raise_policy_error_not_key_error(malformed: tuple[Any, ...]) -> None:
    with pytest.raises(ReleaseTagPolicyError):
        verify_release_tag(*malformed, EXPECTED_COMMIT)


def test_cli_reads_json_payloads_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = _payloads()
    for name, payload in zip(
        (REF_PAYLOAD_ENV, TAG_PAYLOAD_ENV, MAIN_PAYLOAD_ENV, COMPARISON_PAYLOAD_ENV),
        payloads,
    ):
        monkeypatch.setenv(name, json.dumps(payload))

    assert main(["--expected-commit", EXPECTED_COMMIT]) == 0


@pytest.mark.parametrize("value", (None, "{not-json"))
def test_cli_rejects_missing_or_malformed_json_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str | None,
) -> None:
    for name, payload in zip(
        (REF_PAYLOAD_ENV, TAG_PAYLOAD_ENV, MAIN_PAYLOAD_ENV, COMPARISON_PAYLOAD_ENV),
        _payloads(),
    ):
        monkeypatch.setenv(name, json.dumps(payload))
    if value is None:
        monkeypatch.delenv(TAG_PAYLOAD_ENV)
    else:
        monkeypatch.setenv(TAG_PAYLOAD_ENV, value)

    assert main(["--expected-commit", EXPECTED_COMMIT]) == 1
    error = capsys.readouterr().err
    assert "release tag policy failed:" in error
    assert "Traceback" not in error
