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
