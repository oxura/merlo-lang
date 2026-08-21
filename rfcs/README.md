# Merlo RFC process

RFCs are required for changes that alter syntax, type rules, ownership, effects, capabilities, module or package semantics, public IR contracts, target support, or compatibility policy.

## Proposal

Create `rfcs/NNNN-short-name.md` with:

- problem and concrete motivating programs
- proposed semantics and diagnostics
- ownership, effect, and compatibility consequences
- rejected alternatives
- migration and rollback plan
- tests and evidence required for acceptance
- unresolved questions

Use the next unused four-digit number. A proposal begins in `Draft`, becomes `Accepted` only after its semantic and migration consequences are resolved, and moves to `Implemented` when the shipped behavior and tests match it. Rejected or withdrawn RFCs remain in the repository as decision history.

Small bug fixes, diagnostics that do not change behavior, internal refactors, documentation corrections, and additional tests do not need an RFC.

## Proposals

- [RFC 0001: repository and frontend stabilization](0001-repository-and-frontend-stabilization.md)
- [RFC 0002: Concurrency Model v1](0002-concurrency-model-v1.md), proposed and
  explicitly outside the production alpha contract
- [RFC 0003: Repository Hardening and Deterministic Package Resolution v1](0003-repository-hardening-v1.md),
  Accepted 2026-08-21; implementation is PR #82
- [RFC 0004: Type Arena and Structural Type Identity v1](0004-type-arena-v1.md),
  Accepted 2026-08-21; implementation is PR #81
