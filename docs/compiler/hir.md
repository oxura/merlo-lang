# Structured HIR contract

## Purpose

Structured HIR is the typed semantic tree between canonical elaboration and
physical representation. It preserves source and semantic identity while
leaving layout and low-level control flow to later stages.

## Inputs

`compile_canonical_hir(program, entry_function="main")` in
[`src/merlo/structured_hir_v2.py`](../../src/merlo/structured_hir_v2.py)
consumes the typed `SurfaceProgram` retained by an in-memory
`CanonicalProgram`. A canonical JSON/source projection without that Surface
tree is rejected; serialized projections are not compiler input.

## Outputs

The function returns `StructuredHIRProgram`, contract
`merlo.structured-typed-hir.v5`, schema version `5`. It contains source
text/digest, `HIRTypeDecl` values with typed record invariants,
`HIRField`/`HIRVariant` values, `HIRFunction` records, typed parameters,
function contracts, contextual `TypedHole` nodes, an entry function, and
tree-shaped `HIRNode` bodies. `HIRNode.walk()` traverses contract conditions
and executable nodes for source-map projection.

## Invariants

Each node has an ID, kind, span, scope, optional type, ownership, effects,
optional symbol/revision IDs, attributes, and children. Names, node IDs, and the
entry function are unique. HIR is a tree: CFG blocks, gotos, allocation, raw
pointers, and drop flags are rejected here. Stable JSON includes source and
semantic identity for every function and node.
Requirements and ensures are Boolean, source-spanned HIR expressions. Their
revision IDs contribute to the enclosing function revision.
Typed holes retain their expected type, stable source identity, visible typed
bindings and callables, and allowed effects/capabilities. RIR/MIR preserve an
explicit non-executable hole operation. C emission produces a
`TypedHoleNotExecutable` compile-time blocker and never a value.

The sibling `obligations` compiler artifact derives typed verification work
from HIR without changing executable semantics. Function contracts and data
invariants begin as runtime-guarded obligations; contextual holes begin
unresolved. Stable obligation IDs are separate from predicate revisions so
stored verification results can be invalidated precisely.

The sibling `ranges` artifact, contract
`merlo.constant-range-analysis.v1`, propagates closed integer intervals through
constants, bindings, checked arithmetic, casts, preconditions, and conditional
branches. It records branch-local facts and unreachable branch IDs. Checked
arithmetic and narrowing casts contribute typed safety obligations; only
intervals wholly inside or outside the target domain are proven or refuted.
Overlapping or otherwise unknown domains remain unresolved.

The `bounded-symbolic` artifact exhaustively enumerates finite primitive input
domains up to explicit case/value limits and checks typed postconditions against
the HIR executor. Complete domains may be proven; concrete failures retain
deterministic input/result counterexamples. Truncated domains are inconclusive,
and unsupported operations or checked traps are reported without a proof.

Optional `merlo check --smt z3` translates supported pure postcondition paths
to canonical SMT-LIB, asks Z3 for a counterexample to each postcondition, and
records solver version, timeout, query digest/text, and model inputs. The
default report is disabled and does not import a solver. Missing packages,
unsupported HIR, timeout, and solver `unknown` remain explicit non-proofs.
Path expansion is bounded by `--smt-max-paths`; exceeding it is unsupported.

## Failure modes

A missing retained Surface tree, unsupported expressions, invalid map
specializations, duplicate declarations, a missing `main`, duplicate node IDs,
forbidden low-level kinds, and schema drift raise `StructuredHIRCompileError`
or `ValueError`. The coordinator surfaces construction failures as a production
lowering diagnostic.

## Trusted boundary

The retained Surface tree, canonical program identity, and HIR spans/IDs form
the semantic handoff to RIR. HIR carries ownership and effect facts for later
checking but is not itself a complete borrow proof or physical-layout
specification.

## Experimental boundary

The HIR builder and representation backend share Merlo-owned structured syntax
nodes emitted directly from the typed Surface tree. The node vocabulary keeps
the old bootstrap shape where that preserves audited lowering rules, but the
objects, traversal, source locations, and rendering are owned by Merlo. The
legacy direct-source helper may parse canonical snippets through Python before
converting them; production project compilation does not. HIR intentionally
does not model low-level CFG or drop flags.

## Verification commands

```console
merlo check PROJECT
merlo expand PROJECT
merlo inspect main PROJECT --json
```
