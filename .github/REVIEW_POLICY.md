# Review and branch policy

The target policy for `main` is:

- changes arrive through pull requests;
- `Required CI gates` is a required status check and must be current;
- the repository currently has one eligible human maintainer, so the native
  ruleset requires zero approvals and does not pretend self-approval is
  independent;
- changes over 1,200 lines require an Accepted RFC stored in the exact pull
  request head plus a substantive review plan;
- code-owner review and two current, distinct, non-author approvals become
  mandatory when independent maintainers are onboarded under issue #89;
- force pushes and branch deletion are disabled;
- only repository administrators may create matching `v*-alpha.*` release tags;
- after creation, release tags are immutable: no actor, including administrators,
  may update, delete, or force-move them;
- release tags are annotated, GitHub-verified, and point into protected `main`
  history before publishing;
- GitHub tag verification has no signer-key allowlist, so this policy cannot
  restrict a valid GitHub verification to a configured signer identity.

`CODEOWNERS` only routes review. It cannot make a self-approval independent.
Native code-owner enforcement is intentionally disabled while `@oxura` is the
only eligible human maintainer.

Solo-maintainer mode is explicit, not a hidden bypass: pull requests remain
subject to exact-head CI, the Accepted-RFC gate for large changes, conversation
resolution, force-push/deletion protection, and the no-bypass ruleset. Creating
fake, bot, or nominal reviewers to manufacture human approvals is prohibited.


## Temporary solo-maintainer alpha mode

This mode is limited to alpha development and cannot authorize a stable release.
Self-review, bot/app reviews, generated comments, and alternate accounts do not
count as independent human review.

Before a stable release, or once at least two eligible non-author human reviewers
are available for every pull request, the main ruleset must restore two approvals
and last-push approval. The authenticated API readback and reviewer eligibility
must be recorded in issue #89. Required code-owner review remains a separate
transition: it is enabled only when every critical path has an independent owner
and escalation route.

## Automated merge gates

The `Pull request policy` workflow is a required status check alongside
`Required CI gates`. The active ruleset binds both contexts to the GitHub Actions
integration, has no bypass actors, and requires conversation resolution.
The native approval count is zero and last-push approval is disabled only because
the repository has one eligible human maintainer.

For pull requests over 1,200 changed lines, the policy workflow requires an RFC
URL at the exact pull-request head, verifies that the referenced repository RFC
has status `Accepted` or `Implemented`, and requires a non-placeholder review
plan. This is the machine-enforced solo-maintainer acceptance record.

Every CI checkout uses `github.event.pull_request.head.sha` for pull requests.
The `Exact pull-request head checkout` job asserts that revision, and
`Required CI gates` depends on it and every production/tooling/native gate. No
workflow using `pull_request` grants a write token, and no workflow uses
`pull_request_target`; pull-request code is never executed with release
permissions.

An administrator applies all three repository rulesets with
`GITHUB_TOKEN=... GITHUB_REPOSITORY=owner/name python .github/configure_ruleset.py`.
The script updates each named ruleset instead of creating duplicates; use
`--dry-run` to inspect all payloads first. It requires the GitHub administration
permission and is intentionally not run from a pull-request workflow.
The main ruleset has no bypass actors. The release-tag creation ruleset allows
only the RepositoryRole administrator (`actor_id: 5`) to create matching tags;
the separate release-tag immutability ruleset has no bypass actors.

## Change size

An ordinary pull request has one responsibility, one migration boundary, and
approximately 800-1,200 changed code lines or fewer. Generated evidence and
mechanical fixture updates are reported separately from reviewed code. A larger
change requires an accepted RFC plus a review plan that names independently
mergeable semantic slices.

## Risk labels

Pull requests identify each affected boundary: frontend, type system,
ownership, runtime, native backend, FFI/unsafe, verification, packages/release,
or tooling/docs. Safety-critical changes include a negative regression,
generated IR/C assertions where applicable, both GCC and Clang execution, and
sanitizer evidence.
