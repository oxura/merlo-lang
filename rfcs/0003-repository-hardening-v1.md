# RFC 0003: Repository Hardening and Deterministic Package Resolution v1

- Status: Implemented
- Accepted: 2026-08-21
- Accepted by: `@oxura`
- Acceptance evidence: https://github.com/oxura/merlo-lang/issues/87
- Implemented: 2026-08-21
- Implementation: PR #82, merge `4e1e175a1da7ee837657e550fd96fd14f4cf34c0`
- Target: `0.1.0-alpha.3-dev`

## Problem

The production compiler is passing its current tests, but several repository
boundaries are weaker than the claims made around them:

- dependency resolution selects versions before all transitive constraints are
  known and can reject a compatible graph;
- supplied online locks can diverge from the trusted registry index;
- ownership analysis and C emission each live in a multi-thousand-line module,
  making focused review and rollback unnecessarily expensive;
- pull-request checks, review policy, action provenance, release-tag provenance,
  and branch protection are documented but not all mechanically enforced;
- tracked probe files and frozen Python fixtures leave the declared pyflakes
  contour noisy.

These issues make a release harder to review even when language behavior is not
being extended.

Concrete failure cases motivate the slices:

- `root` depends on `left` and `right`; `left` requires `shared >=1`, `right`
  requires `shared <2`, and the index contains `shared` 2.0 and 1.5, so choosing
  the local maximum first must backtrack to the compatible lower version;
- an online lock whose package version still exists but whose archive hash or
  dependency metadata differs from the current registry entry;
- a pull request approved before its last push, or a release tag moved after
  publication, while repository documentation still claims those states are
  rejected mechanically.

## Scope and review plan

The proposed implementation is organized as five independently reviewable and
revertible slices.
They are ordered for integration, but no slice hides another feature front.

1. **Package resolver correctness.** Aggregate complete dependency constraints,
   resolve deterministically with explicit backtracking, validate semver input,
   preserve offline lock replay, and revalidate online locks against the current
   index.
2. **Ownership extraction.** Move ownership/place/borrow/loop analysis from
   `structured_hir_v2.py` into `ownership.py` without changing HIR schema,
   diagnostics, or ownership results.
3. **C backend extraction.** Move type spelling/layout helpers and runtime/effect/
   file/ownership/closure emission into focused modules while preserving public
   backend APIs and byte-identical generated C.
4. **Repository hygiene.** Delete tracked probe residue and remove only the
   pyflakes-diagnosed unused imports and static f-string prefixes in frozen Python
   fixtures.
5. **Governance enforcement.** Pin Actions by commit SHA, test exact pull-request
   heads, enforce review/size/RFC/conversation gates through active rulesets,
   verify signed immutable release tags on protected `main` history, and prevent
   pull-request workflows from receiving write tokens.

Each slice has its own commit and can be reverted in reverse order. Mechanical
moves and fixture cleanup are reported separately from reviewed behavioral logic.

## Package resolution semantics

Requirements are a conjunction of all root and transitive constraints for each
package name. The resolver chooses the highest compatible version and uses a
deterministic explicit backtracking stack when a later constraint invalidates an
earlier choice. Root requirements are accumulated before dependency choices.

Constraint syntax is validated before resolution. Supported v1 terms are `any`/
`*`, wildcard prefixes, equality/inequality, comparisons, caret, and tilde ranges.
Malformed extra segments, misplaced wildcards, and empty terms fail with
`InvalidConstraint`. Prerelease candidates remain excluded unless the individual
constraint string names a prerelease.

A package name absent from the index reports `UnsatisfiedConstraint`. When the name
exists but the normalized conjunction is provably empty, resolution reports
`ConflictingConstraint`. Conservative interval cases may report Unsatisfied rather
than falsely claiming a conflict; they must never change the selected compatible
version.

The search and lock-closure cycle validation use explicit stacks rather than Python
recursion, so valid deep dependency chains do not fail at the interpreter recursion
limit.

## Lock trust and offline behavior

A supplied online lock is checked against the current `RegistryIndex`; name,
version, archive hash, dependencies, and metadata must match. A supplied offline
lock is validated from its own canonical closure and cached artifact hashes without
requiring the current index. Root constraints, dependency closure, package-name
uniqueness, cycles, cache paths, and hashes fail closed.

The lockfile schema remains v1 and canonical JSON shape does not change.

## Ownership and backend boundaries

`merlo.structured-typed-hir.v9` remains the HIR contract. The ownership checker is
moved, not redesigned. Production construction supplies the same error, type-name,
stable-ID, borrowed-type, and qualified-name callbacks. Existing internal call
sites cut over in one change; no parallel ownership authority remains.

Historical note: this RFC's v9 statement records its accepted RFC 0003 merge
state. RFC 0004 and issue #83 later supersede that statement only for HIR
serialization: `merlo.structured-typed-hir.v10` is now authoritative. The
ownership move described above remains unchanged.

The C backend keeps its seven public exports. `GeneralCEmitter` gains one runtime
emission mixin with no overlapping methods. Emission section order and all moved C
string literals remain unchanged. New modules are covered by the existing
`merlo*` package discovery.

## Repository and release governance

The active `main-hardening` ruleset has no bypass actors. In explicit
solo-maintainer mode it requires zero approvals, conversation resolution, and the
`Required CI gates` and `Pull request policy gate` from the GitHub Actions
integration. Large changes additionally require an Accepted RFC from the exact
pull-request head. Two independent approvals become mandatory under issue #89
when eligible maintainers exist.
The GitHub API readback on 2026-08-21 reported this ruleset active as
`21111414`; this is remote repository state, not a claim derived only from the
checked-in configurator.

Two tag rulesets cover `refs/tags/v*-alpha.*`: only repository administrators may
create a matching tag, and a separate no-bypass ruleset prevents update,
deletion, or force movement after creation. Temporary solo-maintainer alpha mode
also applies the no-bypass `stable-release-freeze` ruleset to
`refs/tags/v*`, excluding `refs/tags/v*-alpha.*`; it blocks stable tag creation,
update, deletion, and non-fast-forward movement. The alpha path therefore
remains administrator-create-only and immutable, while stable tags are frozen
until the repository can authorize a stable release.
The release workflow accepts annotated tags only,
requires GitHub verification with reason `valid`, requires the tag target to equal
the event commit, and proves that commit is an ancestor of protected `main`. The
write-capable release checkout does not persist credentials.
The authenticated readback reported the tag-creation and tag-immutability
rulesets active as `21131166` and `21131167`. The post-implementation solo-mode
hardening apply reported `stable-release-freeze` active as `21136517`, with no
bypass actors and the four configured blocking rules. The checked-in payloads
remain the reproducible source used to detect later drift.

GitHub tag verification does not expose a trusted signer fingerprint or configurable
signer allowlist. Administrator-only creation, immutability, protected-main ancestry,
and exact-target verification are the v1 authorization boundary; this limitation
must remain explicit.

## Diagnostics and compatibility

Package error classes and lock schema remain public. The resolver intentionally
adds stricter malformed-constraint rejection, online lock/index consistency, true
aggregate resolution, and deterministic error separation for missing versus
provably contradictory constraints.

Ownership extraction, backend extraction, hygiene, and governance do not change
Merlo syntax, effect rules, capability rules, HIR/RIR/MIR schemas, or supported
targets. They do change repository merge and release authorization policy.

## Migration

- Existing valid manifests and canonical locks require no rewrite.
- A malformed constraint previously accepted accidentally must be corrected.
- An online lock that no longer matches its index must be regenerated from that
  index; an offline lock remains replayable with verified cached artifacts.
- Maintainers apply the four named rulesets with
  `.github/configure_ruleset.py`; the operation is idempotent and excludes inherited
  parent rulesets from its lookup. The dry-run output and post-application API
  readback are the evidence for all four payloads; `stable-release-freeze` is
  recorded as live ruleset `21136517`.
- Existing open pull requests must pass current exact-head checks, conversation
  resolution, and the applicable Accepted-RFC gate before merge.

## Rollback

Revert in reverse slice order:

1. governance workflow/ruleset source (and explicitly disable the remote rulesets,
   including `stable-release-freeze`, before claiming the old policy);
2. hygiene cleanup;
3. C backend extraction;
4. ownership extraction;
5. package resolver behavior.

Remote rulesets are repository state, not removed by reverting Git. Rollback must
record and verify their explicit disablement. Lock schema v1 and textual compiler
artifacts make the code slices independently reversible.

## Rejected alternatives

- Keep the greedy resolver and document ordering: compatible graphs still fail.
- Raise Python's recursion limit: hides an avoidable algorithmic limit.
- Trust online lock metadata without its index: breaks the trust boundary.
- Split ownership/backend with compatibility facades: creates duplicate authority.
- Rewrite moved code while extracting it: prevents byte/diagnostic equivalence
  review.
- Reference Actions by mutable tags: leaves build provenance mutable.
- Put tag creation and immutability in one admin-bypass ruleset: administrators
  could then move or delete published tags.
- Use `pull_request_target` for policy code: risks running pull-request-controlled
  code with elevated permissions.

## Tests and evidence required for acceptance

- compatible transitive constraints resolve to the deterministic maximal version;
- contradictory, missing, malformed, prerelease, deep-chain, online-lock, offline-
  lock, cycle, cache, and archive cases are covered;
- the complete production, tooling, and archive suites pass;
- ownership/place/borrow/loop regressions and native execution pass;
- `tests/fixtures/c_backend/capacity-ledger.json` records the `examples/capacity-ledger`
  generated-C SHA-256 and full pre-extraction baseline commit; the behavioral test
  recompiles that fixture and checks exact bytes, with intentional backend changes
  updating both fields with rationale in the same reviewed PR;
- pyflakes is clean across production, tests, benchmark tooling, and release tooling;
- all workflow actions are immutable SHA references and pull-request workflows have
  no write permission;
- ruleset dry-run and live API state match all four local payloads after
  normalizing GitHub's documented server-added defaults; the stable-freeze
  readback is active ruleset `21136517`;
- release verification rejects lightweight, unsigned, invalid, moved, and
  non-`main` tags;
- GitHub CI passes on the exact reviewed heads.

## Follow-up decisions

- A richer machine-readable resolver explanation graph is deferred to issue #90
  and does not change v1 selection or error semantics.
- External signer-fingerprint verification is deferred to issue #92; the v1
  authorization limitation remains explicit.
- Independent CODEOWNERS and two-human approval activation are deferred to issue
  #89 and require actual eligible maintainers, never fabricated approvals.
- A deterministic resolver graph-size policy is deferred to issue #91; resource
  exhaustion must not be classified as a semantic conflict.

## Acceptance record

The repository owner accepted the package semantics, migration, governance, and
rollback consequences on 2026-08-21 after all independent review findings were
resolved and the required production, tooling, archive, native, sanitizer,
generated-C, release-policy, and live-ruleset evidence passed. Issue #87 records
the candidate revision and review boundary.

This is semantic RFC acceptance, not a claim that two independent human reviewers
exist. The repository has one eligible human maintainer, so its explicit
solo-maintainer policy forbids fake approvals and compensates with no-bypass
rulesets, exact-head CI, conversation resolution, and exact-revision Accepted-RFC
enforcement. PR #82 merged the reviewed behavior to `main` as
`4e1e175a1da7ee837657e550fd96fd14f4cf34c0`; the required CI and policy gates
passed on its exact head.
