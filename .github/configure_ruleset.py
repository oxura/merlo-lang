"""Configure Merlo repository rulesets through the GitHub API.

Run this script as a repository administrator with ``GITHUB_TOKEN`` and
``GITHUB_REPOSITORY=owner/name``. ``--dry-run`` prints the desired payloads
without making a request. The update is idempotent by ruleset name.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen


RULESET_NAME = "main-hardening"
MAIN_RULESET_PAYLOAD = {
    "name": RULESET_NAME,
    "target": "branch",
    "enforcement": "active",
    "conditions": {
        "ref_name": {"include": ["refs/heads/main"], "exclude": []},
    },
    "bypass_actors": [],
    "rules": [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "do_not_enforce_on_create": False,
                "required_status_checks": [
                    {"context": "Required CI gates", "integration_id": 15368},
                    {"context": "Pull request policy gate", "integration_id": 15368},
                ],
            },
        },
        {
            "type": "pull_request",
            "parameters": {
                "dismiss_stale_reviews_on_push": True,
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_approving_review_count": 0,
                "required_review_thread_resolution": True,
                "required_reviewers": [],
                "allowed_merge_methods": ["merge", "squash", "rebase"],
            },
        },
    ],
}

RELEASE_TAG_CREATION_PAYLOAD = {
    "name": "release-tag-creation",
    "target": "tag",
    "enforcement": "active",
    "conditions": {
        "ref_name": {"include": ["refs/tags/v*-alpha.*"], "exclude": []},
    },
    "bypass_actors": [
        {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"},
    ],
    "rules": [{"type": "creation"}],
}

RELEASE_TAG_IMMUTABILITY_PAYLOAD = {
    "name": "release-tag-immutability",
    "target": "tag",
    "enforcement": "active",
    "conditions": {
        "ref_name": {"include": ["refs/tags/v*-alpha.*"], "exclude": []},
    },
    "bypass_actors": [],
    "rules": [
        {"type": "update"},
        {"type": "deletion"},
        {"type": "non_fast_forward"},
    ],
}

# Stable semver tags remain impossible to create, update, delete, or force-move
# while temporary solo-maintainer alpha mode is active.
STABLE_RELEASE_FREEZE_PAYLOAD = {
    "name": "stable-release-freeze",
    "target": "tag",
    "enforcement": "active",
    "conditions": {
        "ref_name": {
            "include": ["refs/tags/v*"],
            "exclude": ["refs/tags/v*-alpha.*"],
        },
    },
    "bypass_actors": [],
    "rules": [
        {"type": "creation"},
        {"type": "update"},
        {"type": "deletion"},
        {"type": "non_fast_forward"},
    ],
}

# Keep the original single-payload names available to callers while applying
# all four named rulesets in one idempotent configuration operation.
RULESET_PAYLOAD = MAIN_RULESET_PAYLOAD
RULESET_PAYLOADS = (
    MAIN_RULESET_PAYLOAD,
    RELEASE_TAG_CREATION_PAYLOAD,
    RELEASE_TAG_IMMUTABILITY_PAYLOAD,
    STABLE_RELEASE_FREEZE_PAYLOAD,
)


def _request(method: str, url: str, token: str, payload: object | None = None) -> object:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "merlo-ruleset-configurator",
        },
    )
    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {url} failed ({error.code}): {detail}") from error


def configure(*, repository: str, token: str, api_base: str = "https://api.github.com") -> dict[str, object]:
    if repository.count("/") != 1 or not all(repository.split("/")):
        raise ValueError("repository must be owner/name")
    endpoint = f"{api_base.rstrip('/')}/repos/{repository}/rulesets"
    rulesets = _request(
        "GET",
        f"{endpoint}?per_page=100&includes_parents=false",
        token,
    )
    if not isinstance(rulesets, list):
        raise RuntimeError("GitHub returned an invalid ruleset list")

    actions: list[dict[str, object]] = []
    for payload in RULESET_PAYLOADS:
        name = payload["name"]
        existing = next(
            (
                item
                for item in rulesets
                if isinstance(item, dict) and item.get("name") == name
            ),
            None,
        )
        if existing is None:
            response = _request("POST", endpoint, token, payload)
            action = "created"
        else:
            ruleset_id = existing.get("id")
            if not isinstance(ruleset_id, int):
                raise RuntimeError(f"matching ruleset {name!r} has no numeric id")
            response = _request("PUT", f"{endpoint}/{ruleset_id}", token, payload)
            action = "updated"
        actions.append({"name": name, "action": action, "ruleset": response})
    return {"actions": actions}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_run:
        print(json.dumps(RULESET_PAYLOADS, indent=2, sort_keys=True))
        return 0
    token = os.environ.get("GITHUB_TOKEN")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repository:
        print("GITHUB_TOKEN and GITHUB_REPOSITORY are required", file=sys.stderr)
        return 2
    try:
        print(json.dumps(configure(repository=repository, token=token), sort_keys=True))
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
