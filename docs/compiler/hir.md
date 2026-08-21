# Structured HIR contract

## Purpose and boundary

Structured HIR is the typed semantic tree between canonical elaboration and
physical representation. It preserves source and semantic identity while
leaving layout and low-level control flow to later stages. The v11 change
gives HIR and the ownership/borrow analyses one compiler-local TypeId
authority; it does not migrate RIR, MIR, or backend representation identity.

`compile_canonical_hir(program, entry_function="main")` in
[`src/merlo/structured_hir_v2.py`](../../src/merlo/structured_hir_v2.py)
consumes the typed `SurfaceProgram` retained by an in-memory
`CanonicalProgram`. A canonical JSON/source projection without that Surface
tree is rejected; serialized projections are not compiler input.

## Version and contents

The function returns `StructuredHIRProgram`, contract
`merlo.structured-typed-hir.v11`, schema version `11`. It contains source
text/digest, one complete `TypeArena` snapshot and its digest, `HIRTypeDecl`
values with typed record invariants, `HIRField`/`HIRVariant` values,
`HIRFunction` records, typed parameters, function contracts, contextual
`TypedHole` nodes, an entry function, and tree-shaped `HIRNode` bodies. It
also contains the complete canonical Merlo native-syntax tree used by the C
adapter and validated typed FFI declarations.

Each function carries a versioned `merlo.borrow-summary.v4` contract. Its
semantic `BorrowRelation` values bind direct or contained returned borrows to
formal parameter indices, finite structural source/result places, a
TypeId-authoritative borrow type, and ownership vocabulary. The retained
canonical `borrow_type` spelling is diagnostic only and is not a second
identity authority. Relations contribute to the function revision and HIR
semantic digest; serialized bounded `witness_path` diagnostics do not.
Recursive SCCs use one canonical structural `RecursiveTail` and choose the
shortest, lexicographically smallest diagnostic witness after convergence.
`HIRNode.walk()` traverses contract conditions and executable nodes for
source-map projection.

## Type identity in HIR

Every HIR-visible type position is either absent as a pair or carries both
the retained diagnostic spelling and its validated `TypeId`. The serialized
field pairs are exact:

| HIR position | Spelling field | Identity field |
| --- | --- | --- |
| ordinary type positions (including fields, parameters, variants, and HIRNode result type) | `type` | `type_id` |
| enum variant payload | `payload_type` | `payload_type_id` |
| function and flow result | `return_type` | `return_type_id` |

`HIRTypeDecl` additionally carries the nominal declaration's `type_id`
alongside its existing declaration `symbol_id` and `revision_id`.
`HIRMachineStateField` serializes exactly `{name, type, type_id}`. HIR-only
FFI JSON annotates extern parameters with `type_id`, extern return/error
fields with `return_type`/`return_type_id` and `error_type`/`error_type_id`,
and `repr(C)` fields with `type_name`/`type_id`; these annotations do not
change the underlying `FFIProgram` classes or FFI ownership contract.

This covers formerly string-only positions in declarations, parameters,
returns, flow results, machine state fields, record fields, enum payloads,
local `Let`/`Var`/`Assign` nodes, contract-condition result types, and the
typed FFI projection. A `HIRNode.type_id` is present for those local and
condition result positions when a type is present. Retained spelling remains
the diagnostic/source-facing projection. `TypeId` is the semantic authority:
consumers must not infer identity by reparsing that spelling.

In memory these identities are `TypeId` values. JSON represents them through
`TypeId.to_dict()`; consumers should not substitute a raw spelling or an
unvalidated string for that representation.

`TypeId`, `SymbolId`, and `RevisionId` have different jobs. A `TypeId`
identifies a structural type in the arena; a `SymbolId` identifies a named
declaration; and a `RevisionId` identifies one semantic revision of that
declaration. A nominal declaration's `type_id` is stable by nominal name,
while its `revision_id` changes when declaration semantics change. A future
`LayoutId` would identify a physical layout and is not interchangeable with
any of these identities.

## Arena lifecycle and validation

The HIR builder creates one local mutable `TypeContextBuilder` and uses its
cached interning operations for declarations, functions, parameters, nodes,
contracts, flows, machines, machine state fields, and the HIR-only FFI
projection. The same builder remains open while the HIR, borrow-summary, and
ownership analyses run; it is frozen exactly once when the final
`StructuredHIRProgram` is assembled. The completed immutable context is
attached to that program; no process-global arena and no post-load fallback
parser participate in the contract.

Serialization stores the arena snapshot and `type_arena_digest` before the
typed nodes. The snapshot is closed (`allow_unresolved=false`). Entries are
canonical and deterministic; the digest covers the canonical snapshot. A
A reader validates in this order:

1. validate the outer object’s exact keys, contract, schema version, and
   envelope invariants, rejecting HIR v10 rather than accepting it as a
   compatibility path;
2. restore the `TypeArena` snapshot, require a closed arena, recompute and
   compare `type_arena_digest`, and reject malformed, missing, cyclic, or
   tampered entries;
3. validate HIR-only FFI annotations, requiring every supplied `TypeId` to
   exist in that arena and its canonical spelling to match the retained
   spelling;
4. restore native syntax and typed HIR records, requiring each supplied
   `TypeId` to exist in the arena and requiring
   `arena.canonical(type_id)` to equal the retained canonical HIR spelling;
5. let `StructuredHIRProgram` validate source digest, uniqueness,
   cross-record identities, and canonical roundtrip invariants.

The reader never reparses retained spelling during typed-node restoration.
Every spelling/ID pair is both present or both absent. This ordering ensures
that node validation cannot consult a different or implicitly reopened arena.

Aliases are normalized before interning, so the supported scalar aliases
`Int -> Int64`, `UInt -> UInt64`, and `Float -> Float64` share the canonical
identity of their targets. Qualified nominal names remain distinct:
`app.Int` is not rewritten to `Int`, and a qualified declaration's nominal
`TypeId` is stable by that qualified nominal name. Diagnostic output may
retain the canonical normalized spelling, but spelling is not an alternate
identity channel.

## Other invariants

Each node has an ID, kind, span, scope, optional type, ownership, effects,
optional symbol/revision IDs, attributes, and children. Names, node IDs, and
the entry function are unique. HIR is a tree: CFG blocks, gotos, allocation,
raw pointers, and drop flags are rejected here. Stable JSON includes source
and semantic identity for every function and node.

Requirements and ensures are Boolean, source-spanned HIR expressions. Their
revision IDs contribute to the enclosing function revision. Typed holes
retain their expected type, stable source identity, visible typed bindings
and callables, and allowed effects/capabilities. RIR/MIR preserve an explicit
non-executable hole operation. C emission produces a
`TypedHoleNotExecutable` compile-time blocker and never a value.

The sibling `obligations` compiler artifact derives typed verification work
from HIR without changing executable semantics. Function contracts and data
invariants begin as runtime-guarded obligations; contextual holes begin
unresolved. Stable obligation IDs are separate from predicate revisions so
stored verification results can be invalidated precisely.

The sibling `ranges` artifact, contract
`merlo.constant-range-analysis.v1`, propagates closed integer intervals
through constants, bindings, checked arithmetic, casts, preconditions, and
conditional branches. It records branch-local facts and unreachable branch
IDs. Checked arithmetic and narrowing casts contribute typed safety
obligations; only intervals wholly inside or outside the target domain are
proven or refuted. Overlapping or otherwise unknown domains remain
unresolved.

The `bounded-symbolic` artifact exhaustively enumerates finite primitive input
domains up to explicit case/value limits and checks typed postconditions
against the HIR executor. Complete domains may be proven; concrete failures
retain deterministic input/result counterexamples. Truncated domains are
inconclusive, and unsupported operations or checked traps are reported
without a proof.

Optional `merlo check --smt z3` translates supported pure postcondition paths
to canonical SMT-LIB, asks Z3 for a counterexample to each postcondition, and
records solver version, timeout, query digest/text, and model inputs. The
default report is disabled and does not import a solver. Missing packages,
unsupported HIR, timeout, and solver `unknown` remain explicit non-proofs.
Path expansion is bounded by `--smt-max-paths`; exceeding it is unsupported.

The `property-evidence` artifact derives replayable parameter domains and
Cartesian cases for each typed postcondition. Exhaustive finite domains are
marked separately from bounded boundary samples. Preconditions remain
attached to each property, and concrete bounded/SMT refutations are
normalized with typed inputs and engine provenance.

The `verification-metrics` artifact measures automatic closure without
double-counting obligations proven by multiple engines. Static, exhaustive
bounded, and SMT proofs count as closed; refutations take precedence over any
conflicting proof. Counts, per-category/per-engine provenance, and an exact
integer basis-points rate are serialized canonically.

## Failure modes

A missing retained Surface tree, unsupported expressions, invalid map
specializations, duplicate declarations, a missing `main`, duplicate node
IDs, forbidden low-level kinds, an open or tampered arena, a missing or
mismatched `TypeId`, and schema drift raise `StructuredHIRCompileError` or
`ValueError`. HIR v10 is not readable by the v11 reader. The coordinator
surfaces construction failures as a production lowering diagnostic.

## Trusted and experimental boundaries

The canonical program identity, digest-bound native syntax and FFI artifacts,
and HIR spans/IDs form the semantic handoff to later stages. HIR JSON has a
strict validated deserializer. A retained in-memory native module may witness
construction consistency, but code generation always restores the serialized
tree and rejects a mismatching witness. HIR carries ownership and effect
facts for later checking but is not itself a complete borrow proof or
physical-layout specification.

This migration makes ownership, `Place` lookup, and borrow provenance
TypeId-authoritative through the shared compiler-local `TypeContext`.
Retained spellings remain diagnostic projections only. It does not claim an
RIR, MIR, LLVM, GPU, or backend TypeId cutover: RIR remains v5 and MIR remains
v2, with generated-C semantics preserved. Issue #84 is the authority
migration recorded here; issues #85 and #86 remain the separately tracked
representation/layout and later executable-IR scope.

The HIR builder and representation backend share Merlo-owned structured
syntax nodes emitted directly from the typed Surface tree. The node
vocabulary keeps the old bootstrap shape where that preserves audited
lowering rules, but the objects, traversal, source locations, and rendering
are owned by Merlo. The legacy direct-source helper may parse canonical
snippets through Python before converting them; production project compilation
does not. HIR intentionally does not model low-level CFG or drop flags.

## Verification commands

```console
merlo check PROJECT
merlo expand PROJECT
merlo inspect main PROJECT --json
```
