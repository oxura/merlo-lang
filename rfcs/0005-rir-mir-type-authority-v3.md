# RFC 0005: RIR and Executable MIR Type Authority v3

- Status: Implemented
- Acceptance evidence: https://github.com/oxura/merlo-lang/issues/85
- Implementation: PR #98
- Target: `0.1.0-alpha.3-dev`

## Problem

Representation IR and executable Performance MIR retained semantic type spellings after
HIR became TypeArena-backed. Each downstream consumer could therefore parse, normalize,
or infer a different type identity. A spelling-only boundary also made serialized
artifacts unable to prove that their operands and physical layouts belonged to the same
closed HIR authority.

## Contract

RIR consumes one frozen HIR `TypeContext` and carries the corresponding closed
`FrozenTypeArena` plus its digest. Every descriptor and typed operation carries a
validated `TypeId`; every physical descriptor carries a target-bound `LayoutId`.
Descriptor fields, variants, generic children, and drop plans retain their structural
identities and are verified against the arena before an artifact is accepted.

Executable MIR schema v3 carries the same arena predecessor and target metadata. Its
construction, deserialization, optimization boundaries, and evaluation are guarded by
`MIRVerificationError` and reject unknown values, mismatched types, invalid layouts,
missing CFG predecessors, unresolved drops, stale predecessors, and schema drift.

The predecessor chain is explicit:

```text
Structured HIR + frozen TypeArena
  -> Representation IR v6 + LayoutId v1
  -> executable Performance MIR v3
```

Each artifact also records source/predecessor digests, target specification digests,
ownership/drop/effect/source provenance, and canonical JSON identities.

## Authority boundary

Production RIR/MIR code does not call `parse_type`, `generic_parts`, or `intern_text`.
Diagnostic text is rendered from existing `TypeId` values only. Layout validation receives
the frozen HIR `TypeContext`; it never rebuilds a semantic arena from retained spelling
text. Qualified nominal identities remain distinct even when their physical layouts are
equal. Descriptor aliases with one source identity must have compatible physical payloads
before C emission; incompatible aliases fail closed.

## Compatibility and failure policy

RIR v5 and MIR v2 artifacts are not read by the v6/v3 readers. Callers must use an
explicit legacy reader or migrate the artifact. Tampered child identities, layout IDs,
target specifications, predecessor digests, and drop plans are rejected before lowering,
optimization, or code generation.

This RFC does not remove the native-syntax/C-backend authority. That cutover remains the
separate owner of Issue #72. LLVM, async, Task IR, Parallel IR, GPU, partial moves, and
other optimizer redesigns remain outside this migration.
