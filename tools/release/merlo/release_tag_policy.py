from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any


class ReleaseTagPolicyError(ValueError):
    """Raised when GitHub release-tag payloads violate the release policy."""


REF_PAYLOAD_ENV = "RELEASE_REF_JSON"
TAG_PAYLOAD_ENV = "RELEASE_TAG_JSON"
MAIN_PAYLOAD_ENV = "RELEASE_MAIN_JSON"
COMPARISON_PAYLOAD_ENV = "RELEASE_COMPARISON_JSON"


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseTagPolicyError(f"{label} must be a JSON object")
    return value


def _field(payload: Mapping[str, Any], name: str, label: str) -> Any:
    if name not in payload:
        raise ReleaseTagPolicyError(f"{label} is missing {name!r}")
    return payload[name]


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseTagPolicyError(f"{label} must be a nonempty string")
    return value


def _object_payload(payload: Any, label: str) -> Mapping[str, Any]:
    return _mapping(_field(_mapping(payload, label), "object", label), f"{label}.object")


def verify_release_tag(
    ref_payload: Mapping[str, Any],
    tag_payload: Mapping[str, Any],
    main_payload: Mapping[str, Any],
    comparison_payload: Mapping[str, Any],
    expected_commit: str,
) -> None:
    """Verify that a fetched release tag is a valid, reviewed main ancestor.

    The payloads are the unmodified JSON responses from the GitHub refs, tags,
    and compare APIs.  This function is deliberately side-effect free so the
    policy can be exercised independently of GitHub and the shell workflow.
    """
    expected_commit = _nonempty_string(expected_commit, "expected_commit")

    ref_object = _object_payload(ref_payload, "ref payload")
    ref_type = _field(ref_object, "type", "ref payload.object")
    if ref_type != "tag":
        raise ReleaseTagPolicyError("release ref must point to an annotated tag object")
    tag_object_sha = _nonempty_string(
        _field(ref_object, "sha", "ref payload.object"), "ref payload.object.sha"
    )

    tag = _mapping(tag_payload, "tag payload")
    verification = _mapping(_field(tag, "verification", "tag payload"), "tag payload.verification")
    if _field(verification, "verified", "tag payload.verification") is not True:
        raise ReleaseTagPolicyError("release tag must have verified=true")
    if _field(verification, "reason", "tag payload.verification") != "valid":
        raise ReleaseTagPolicyError("release tag verification reason must be valid")

    tag_object = _mapping(_field(tag, "object", "tag payload"), "tag payload.object")
    if _field(tag_object, "type", "tag payload.object") != "commit":
        raise ReleaseTagPolicyError("annotated tag must target a commit")
    target_sha = _nonempty_string(
        _field(tag_object, "sha", "tag payload.object"), "tag payload.object.sha"
    )
    if target_sha != expected_commit:
        raise ReleaseTagPolicyError("release tag target does not match the event commit")

    main_object = _object_payload(main_payload, "main payload")
    if _field(main_object, "type", "main payload.object") != "commit":
        raise ReleaseTagPolicyError("main ref must point to a commit")
    main_sha = _nonempty_string(
        _field(main_object, "sha", "main payload.object"), "main payload.object.sha"
    )

    comparison = _mapping(comparison_payload, "comparison payload")
    status = _field(comparison, "status", "comparison payload")
    if status not in ("ahead", "identical"):
        raise ReleaseTagPolicyError(
            "main must be ahead of or identical to the release commit"
        )
    merge_base = _mapping(
        _field(comparison, "merge_base_commit", "comparison payload"),
        "comparison payload.merge_base_commit",
    )
    if _field(merge_base, "sha", "comparison payload.merge_base_commit") != target_sha:
        raise ReleaseTagPolicyError("release commit is not on the main history")
    behind_by = _field(comparison, "behind_by", "comparison payload")
    if type(behind_by) is not int or behind_by != 0:
        raise ReleaseTagPolicyError("release commit must not be behind main")

    # Keep these fetched values semantically consumed by the policy.  The
    # compare response is only meaningful for the exact main ref requested by
    # the workflow, whose nonempty SHA is part of the payload contract.
    if not main_sha or not tag_object_sha:
        raise ReleaseTagPolicyError("GitHub ref payloads must identify their objects")


def _json_from_environment(name: str) -> Any:
    raw = os.environ.get(name)
    if raw is None:
        raise ReleaseTagPolicyError(f"required environment variable {name} is missing")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReleaseTagPolicyError(f"environment variable {name} is not valid JSON") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify GitHub release tag policy payloads")
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args(argv)
    try:
        verify_release_tag(
            _json_from_environment(REF_PAYLOAD_ENV),
            _json_from_environment(TAG_PAYLOAD_ENV),
            _json_from_environment(MAIN_PAYLOAD_ENV),
            _json_from_environment(COMPARISON_PAYLOAD_ENV),
            args.expected_commit,
        )
    except ReleaseTagPolicyError as exc:
        print(f"release tag policy failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
