# Review and branch policy

The target policy for `main` is:

- changes arrive through pull requests;
- `Required CI gates` is a required status check and must be current;
- when eligible independent maintainers exist, at least one approval comes from
  someone other than the author;
- when eligible independent maintainers exist, runtime, ownership, unsafe, FFI,
  and backend changes require two independent approvals through a path-scoped
  ruleset;
- code-owner review is required after independent maintainers are added;
- new commits dismiss stale approvals;
- force pushes and branch deletion are disabled;
- administrators do not bypass the release and safety rules;
- release tags are signed and verified before publishing.

`CODEOWNERS` only routes review. It cannot make a self-approval independent and
does not by itself implement the two-review critical-path rule. Repository
rulesets must enforce these settings once the repository has enough eligible
maintainers.

## Solo-maintainer research-alpha exception

While Merlo has fewer than two eligible independent reviewers, the repository
owner may merge an alpha-development pull request without pretending that a
self-review is independent. Every such override must:

- be recorded in the pull request conversation before merge;
- identify the exact reviewed head commit;
- have the complete required CI workflow succeed for that head;
- have no unresolved review thread, known safety regression, or unexplained
  sanitizer failure;
- report the affected compiler boundary, negative regressions, local test
  commands, remaining limitations, and any environment-blocked evidence;
- preserve the pull-request history rather than pushing directly to `main`.

An override cannot waive failed or missing required CI and cannot be used for a
stable release. Independent review becomes mandatory as soon as eligible
maintainers exist, and all stable-release changes require the normal
independent-review gates regardless of project staffing.

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
