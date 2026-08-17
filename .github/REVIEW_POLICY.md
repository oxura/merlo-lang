# Review and branch policy

The target policy for `main` is:

- changes arrive through pull requests;
- `Required CI gates` is a required status check and must be current;
- at least one approval comes from someone other than the author;
- runtime, ownership, unsafe, FFI, and backend changes require two independent
  approvals through a path-scoped ruleset;
- code-owner review is required after independent maintainers are added;
- new commits dismiss stale approvals;
- force pushes and branch deletion are disabled;
- administrators do not bypass the release and safety rules;
- release tags are signed and verified before publishing.

`CODEOWNERS` only routes review. It cannot make a self-approval independent and
does not by itself implement the two-review critical-path rule. Repository
rulesets must enforce these settings. Until another maintainer or review team
exists, the independent-review gates are intentionally not satisfiable and a
release requiring them must remain blocked rather than silently weakening the
rule.

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
