from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTION_USE = re.compile(r"^\s*(?:-\s+)?uses:\s+([^@\s]+)@([^\s#]+)(?:\s+#\s*(\S.*))?$", re.MULTILINE)


def test_workflow_actions_are_immutable_and_document_the_release_tag() -> None:
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        for match in ACTION_USE.finditer(text):
            ref = match.group(2)
            comment = match.group(3) or ""
            assert re.fullmatch(r"[0-9a-f]{40}", ref), (workflow, match.group(0))
            assert re.search(r"\bv\d", comment), (workflow, match.group(0))


def test_pull_request_workflows_never_grant_write_tokens_or_run_target_code() -> None:
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        assert "pull_request_target" not in text
        if re.search(r"^\s*pull_request:", text, re.MULTILINE):
            assert not re.search(r"^\s+[\w-]+:\s+write\s*$", text, re.MULTILINE)


def test_ci_and_release_lint_production_and_tooling_trees() -> None:
    command = (
        "python -m pyflakes src/merlo tests "
        "tools/benchmarks/merlo tools/release/merlo"
    )
    for name in ("ci.yml", "release.yml"):
        assert command in (WORKFLOWS / name).read_text(encoding="utf-8")


def test_ci_required_gate_checks_the_exact_pull_request_head() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "name: Exact pull-request head checkout" in text
    assert "github.event.pull_request.head.sha || github.sha" in text
    assert "test \"$(git rev-parse HEAD)\" = \"$EXPECTED_HEAD\"" in text
    required = text.split("  required-gates:", 1)[1]
    assert "      - exact-head" in required


def test_release_verifies_signed_annotated_tag_before_build() -> None:
    text = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    verify = text.split("  verify-tag:", 1)[1].split("  production:", 1)[0]
    assert "ref: main" in verify
    assert "persist-credentials: false" in verify
    assert 'git/ref/tags/${TAG_NAME}' in verify
    assert 'git/tags/${tag_sha}' in verify
    assert 'git/ref/heads/${MAIN_BRANCH}' in verify
    assert 'compare/${target_sha}...${main_sha}' in verify
    assert "export RELEASE_REF_JSON=" in verify
    assert "export RELEASE_TAG_JSON=" in verify
    assert "export RELEASE_MAIN_JSON=" in verify
    assert "export RELEASE_COMPARISON_JSON=" in verify
    assert "python tools/release/merlo/release_tag_policy.py --expected-commit" in verify
    artifacts = text.split("  artifacts:", 1)[1].split("  release:", 1)[0]
    assert "needs: [verify-tag]" in artifacts
    release = text.split("  release:", 1)[1]
    assert "contents: write" in release
    assert "persist-credentials: false" in release


def test_pull_request_policy_retriggers_for_size_inputs() -> None:
    text = (WORKFLOWS / "policy.yml").read_text(encoding="utf-8")
    assert "edited" in text and "synchronize" in text
    assert "pull_request_review:" not in text
    assert "reviewThreads" not in text
    assert "listReviews" not in text
    assert "changedLines > 1200" in text
    assert "rfcMatch" in text and "hasPlan" in text
    assert "pull.head.sha" in text
    assert "github.rest.repos.getContent" in text
    assert "Accepted|Implemented" in text


def test_pull_request_template_exposes_non_placeholder_policy_fields() -> None:
    policy = (WORKFLOWS / "policy.yml").read_text(encoding="utf-8")
    template = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(
        encoding="utf-8"
    )
    assert "(?!<!--)" in policy
    assert "[ \\t]+" in policy
    assert "RFC: <!--" in template
    assert "Review plan: <!--" in template


def test_ruleset_requires_real_branch_protection_gates() -> None:
    path = ROOT / ".github" / "configure_ruleset.py"
    assert "includes_parents=false" in path.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("merlo_ruleset", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = module.RULESET_PAYLOAD
    assert payload["name"] == "main-hardening"
    rules = {rule["type"]: rule for rule in payload["rules"]}
    assert {"deletion", "non_fast_forward", "pull_request", "required_status_checks"} <= rules.keys()
    review_parameters = rules["pull_request"]["parameters"]
    assert review_parameters["required_review_thread_resolution"] is True
    assert review_parameters["require_last_push_approval"] is False
    assert review_parameters["require_code_owner_review"] is False
    assert review_parameters["required_approving_review_count"] == 0
    assert review_parameters["required_reviewers"] == []
    assert review_parameters["allowed_merge_methods"] == ["merge", "squash", "rebase"]
    status_parameters = rules["required_status_checks"]["parameters"]
    assert status_parameters["strict_required_status_checks_policy"] is True
    assert {check["context"] for check in status_parameters["required_status_checks"]} == {
        "Required CI gates",
        "Pull request policy gate",
    }
    assert {
        check["integration_id"]
        for check in status_parameters["required_status_checks"]
    } == {15368}
    payloads = {item["name"]: item for item in module.RULESET_PAYLOADS}
    creation = payloads["release-tag-creation"]
    assert creation["target"] == "tag"
    assert creation["conditions"]["ref_name"] == {
        "include": ["refs/tags/v*-alpha.*"],
        "exclude": [],
    }
    assert creation["bypass_actors"] == [
        {
            "actor_id": 5,
            "actor_type": "RepositoryRole",
            "bypass_mode": "always",
        }
    ]
    assert creation["rules"] == [{"type": "creation"}]

    immutability = payloads["release-tag-immutability"]
    assert immutability["target"] == "tag"
    assert immutability["conditions"]["ref_name"] == {
        "include": ["refs/tags/v*-alpha.*"],
        "exclude": [],
    }
    assert immutability["bypass_actors"] == []
    assert {rule["type"] for rule in immutability["rules"]} == {
        "update",
        "deletion",
        "non_fast_forward",
    }

    stable_freeze = payloads["stable-release-freeze"]
    assert stable_freeze["target"] == "tag"
    assert stable_freeze["enforcement"] == "active"
    assert stable_freeze["conditions"]["ref_name"] == {
        "include": ["refs/tags/v*"],
        "exclude": ["refs/tags/v*-alpha.*"],
    }
    assert stable_freeze["bypass_actors"] == []
    assert {rule["type"] for rule in stable_freeze["rules"]} == {
        "creation",
        "update",
        "deletion",
        "non_fast_forward",
    }


def test_ruleset_dry_run_is_executable(capsys) -> None:
    path = ROOT / ".github" / "configure_ruleset.py"
    spec = importlib.util.spec_from_file_location("merlo_ruleset_dry_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["--dry-run"]) == 0
    payloads = json.loads(capsys.readouterr().out)
    assert {payload["name"] for payload in payloads} == {
        "main-hardening",
        "release-tag-creation",
        "release-tag-immutability",
        "stable-release-freeze",
    }
