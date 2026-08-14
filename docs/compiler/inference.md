# Type and error inference contract

## Inputs and outputs

- **Current input:** `elaborate_concise_application()` in
  [`merlo/concise_application.py`](../../merlo/concise_application.py) reads
  project source, normalizes declared type spellings, and runs its internal
  `_Inference` state over the assembled core. The structural route accepts a
  `SurfaceProgram` through `elaborate_surface()` in
  [`merlo/surface_elaborator.py`](../../merlo/surface_elaborator.py).
- **Current output:** `ConciseApplicationElaboration` contains
  `canonical_program`, canonical and machine source, semantic digests,
  `InferenceDecision` records, `TaskBoundary` signatures, and source origins.
  `InferenceDecision` records owner, name, kind, inferred type, mutability,
  location, and textual evidence.
- **Lowering consumer:** `compile_project()` passes the resulting
  `CanonicalProgram` to `compile_canonical_hir()`; inference is complete before
  HIR construction.

## Invariants

Canonical semantic output is deterministic: `semantic_ast_equal` compares the
concise and canonical semantic digests, while `canonical_reference_equal`
records the reference comparison used by elaboration. Declared task parameter
and return types are preserved in `TaskBoundary`; public interface revisions
include signature, effects, and capabilities. `InferenceDecision.mutable` is a
binding mutability fact only; it is not an ownership, move, borrow, or drop
proof. Ownership is assigned later by `_OwnershipChecker` and `_HIRBuilder` in
[`merlo/structured_hir_v2.py`](../../merlo/structured_hir_v2.py).

The RFC 0001 contract (planned) changes the input boundary to bound nodes and
records local types, return types, mutability, typed errors, and evidence spans;
it must never inspect raw source or CPython AST nodes.

## Failure modes

`ConciseApplicationError` rejects untyped or ambiguous expressions rather than
choosing a fallback. The current elaborator rejects unsupported annotations,
dynamic `Any`-like content, invalid calls, inconsistent assignments, bad
returns, missing declarations, and interface-lock mismatches. The project
coordinator turns `TypeError` and `ValueError` from canonical/HIR lowering into
`production lowering failed` diagnostics.

## Identity and provenance

Each `InferenceDecision` carries the source path and line where evidence was
observed. Canonical nodes retain spans, and `SourceOrigin` maps canonical lines
back to concise source. Semantic digests hash normalized canonical payloads,
not Python object identity. Under RFC 0001, inference facts will be keyed by
bound `SymbolId` and exact spans; aliases cannot duplicate identity.

## Current-alpha limitations

- The production `_Inference` implementation is embedded in the 2,775-line
  `concise_application.py` coordinator and works over preprocessed source/
  CPython AST structures rather than a standalone bound IR.
- Type and error inference are not exposed as a stable `infer()` module API;
  `InferenceDecision` is an elaboration result, not a complete typed-tree
  contract.
- Generics, subtyping, implicit conversions, traits, overloads, and the RFC
  0001 typed-error inference model are not current alpha guarantees. RFC 0001
  is accepted and marks the bound-node implementation as planned.
