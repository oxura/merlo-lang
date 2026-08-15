# Type and error inference contract

## Purpose

Inference fills private parameter, return, local, mutability, effect, and typed
error facts when constraints are sufficient. It preserves explicit public
contracts and rejects ambiguity instead of choosing a fallback.

## Inputs

The frontend elaboration supplies canonical nodes and the source origins used
for evidence. The current result model is in
[`src/merlo/frontend_model.py`](../../src/merlo/frontend_model.py):
`InferenceDecision` records owner, name, kind, type, mutability, location, and
evidence; `TaskBoundary` records parameters, return type, effects,
capabilities, visibility, and source location.

## Outputs

`ConciseApplicationElaboration` contains the canonical program, canonical and
machine source, semantic digests, inference decisions, task boundaries, public
interfaces, and source origins. `semantic_ast_equal` and
`canonical_reference_equal` expose the comparisons made by elaboration. HIR
construction consumes the completed `CanonicalProgram`; inference is not a
runtime type test.

## Invariants

Conflicting or unresolved constraints fail. `InferenceDecision.mutable` is a
binding mutability fact only; it is not an ownership, borrow, move, or drop
proof. Explicit task parameter and return types are preserved. Public interface
revisions include their published signature and effect/capability fields.
`Result` propagation contributes its declared typed error rather than an
untyped exception escape.

## Failure modes

The elaborator rejects unsupported annotations, dynamic `Any`-like content,
invalid calls, inconsistent assignments, bad returns, missing declarations, and
interface-lock mismatches. Canonical/HIR lowering failures are surfaced by the
project coordinator as production-lowering diagnostics. Inference never
silently inserts a dynamic type or truthiness conversion.

## Trusted boundary

Canonical nodes, source spans, semantic digests, and the immutable decision
records are the trusted inference output. Ownership is checked later by HIR/RIR
and cannot be inferred from a mutable flag alone.

## Experimental boundary

The alpha inference pass consumes the native Surface AST. It is not a
standalone public `infer()` API. Subtyping, implicit conversions, overloads,
and the RFC 0001 bound-node typed-error model remain outside the published
alpha contract.

## Corpus measurement

`python3 -m tools.benchmarks.merlo.private_annotations` parses every production
source in the fifteen checked-in examples and counts explicit parameter and
return types only on private functions. Public contracts are excluded because
their annotations are mandatory.

The checked corpus has 6 explicit annotations across 19 private boundary
slots, a rate of 31.58%. The report includes the ordered source digest and fails
its gate above one third. `tests/test_private_annotations.py` locks the corpus,
measurement definition, and threshold.

`python3 -m tools.benchmarks.merlo.alpha_simplicity` recomputes the independent
paired-source measurement over 48 frozen programs in eight categories. Its
median concise-Merlo/Python lexical-token ratio is 0.5455 (54.55%). This is a
source-surface measurement, not a general productivity or readability claim.
The corpus digest, per-arm hashes, output parity, distribution, and 0.80 gate
are validated by `tools/benchmarks/merlo/tests/test_alpha_simplicity.py`.

## Verification commands

```console
merlo check PROJECT
merlo expand PROJECT
merlo explain PROJECT
```

Use diagnostic codes and JSON fields for automation; diagnostic wording and
source locations may contain host-specific detail.
