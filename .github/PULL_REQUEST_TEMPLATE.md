## Change

Describe the observable behavior and the reason for changing it.

## Evidence

List the focused tests and executable commands run. Attach checked benchmark or sanitizer evidence only when the change depends on it.

## Compatibility

State changes to syntax, types, ownership, effects, capabilities, manifests, lockfiles, public IR, diagnostics, or native output. Write `None` when there is no compatibility effect.

## Review boundary

RFC: <!-- Required for changes over 1,200 lines; use the accepted RFC URL. -->

Review plan: <!-- Required for changes over 1,200 lines; name independent slices. -->

State the affected owners: frontend, type system, ownership, runtime, native
backend, FFI/unsafe, verification, packages/release, or tooling/docs. Report
reviewed code lines separately from generated evidence and fixtures. Link the
RFC and split plan when the ordinary 800-1,200 line boundary is exceeded.

The native `main` ruleset runs in explicit solo-maintainer mode: zero fabricated
approvals, no bypass actors, exact-head CI, conversation resolution, and an
Accepted RFC from the exact pull-request head for changes over 1,200 lines.

## Checklist

- [ ] The change has one clear responsibility.
- [ ] New observable behavior has a regression test.
- [ ] The affected CLI or native path was exercised directly.
- [ ] Documentation and normative specs agree with the implementation.
- [ ] Required CI is current; new commits have not made approvals stale.
- [ ] Safety-critical paths have the required independent reviewers and evidence.
- [ ] Generated files, local world state, credentials, and benchmark scratch data are excluded.
- [ ] `Required CI gates` passed after checking out this exact PR head.
- [ ] `Pull request policy` passed its size/Accepted-RFC check; native
      conversation resolution is clear.
- [ ] Changes over 1,200 lines link an accepted RFC and include a review plan.
- [ ] Solo-maintainer mode is still necessary, or issue #89 has restored two
      current, distinct, non-author approvals.
- [ ] Release-tag changes preserve administrator-only creation and no-bypass
      immutability; GitHub tag verification has no signer-key allowlist.
