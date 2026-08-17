## Change

Describe the observable behavior and the reason for changing it.

## Evidence

List the focused tests and executable commands run. Attach checked benchmark or sanitizer evidence only when the change depends on it.

## Compatibility

State changes to syntax, types, ownership, effects, capabilities, manifests, lockfiles, public IR, diagnostics, or native output. Write `None` when there is no compatibility effect.

## Review boundary

State the affected owners: frontend, type system, ownership, runtime, native
backend, FFI/unsafe, verification, packages/release, or tooling/docs. Report
reviewed code lines separately from generated evidence and fixtures. Link the
RFC and split plan when the ordinary 800-1,200 line boundary is exceeded.

## Checklist

- [ ] The change has one clear responsibility.
- [ ] New observable behavior has a regression test.
- [ ] The affected CLI or native path was exercised directly.
- [ ] Documentation and normative specs agree with the implementation.
- [ ] Required CI is current; new commits have not made approvals stale.
- [ ] Safety-critical paths have the required independent reviewers and evidence.
- [ ] Generated files, local world state, credentials, and benchmark scratch data are excluded.
- [ ] A required RFC is linked, or the change does not require one.
