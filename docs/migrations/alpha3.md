# Migrating from alpha.2 to alpha.3
> **Current-contract note (issue #84, HIR v12):** Alpha.3 originally shipped
> the HIR v10 transition described below. The current development compiler uses
> HIR v12, `merlo.borrow-summary.v4`, and SemanticWorld v19. Ownership, `Place`
> lookup, borrow provenance, ContractGraph, semantic HIR attributes, and bound
> FFI metadata are now TypeId-authoritative. Machine-state IDs remain a separate
> identity domain. Issues #85 and #86 remain open for representation/layout and
> executable-IR scope.

Alpha.3 intentionally changes the filesystem and verification models. The
compiler reports language `0.3`, frontend `8`, canonical `6`, HIR `10`,
Obligation IR `1`, range analysis `1`, bounded symbolic execution `1`, optional
SMT `1`, property evidence `1`, verification metrics `1`, ChangeIR `1`,
semantic capsule `1`, semantic impact `1`, patch evidence `1`, preservation
report `1`, change transaction `1`, RIR `5`, MIR `2`, runtime ABI `2`, and
SemanticWorld `17`. Earlier lockfiles must be regenerated with the alpha.3
compiler; the old HIR v9 artifact is not readable by the HIR v10 reader.

## Structured HIR v12 and TypeArena

Structured HIR is now serialized as the strict
`merlo.structured-typed-hir.v12` contract (schema `12`). Every HIR-visible
type position carries retained canonical diagnostic spelling together with a
validated `TypeId`, or carries neither. Semantic type attributes carry paired
IDs as well. Machine state labels use deterministic state IDs outside the
TypeArena; Transition nodes have no type identity and all source/target IDs
must belong to the machine.

In memory IDs are `TypeId` values and JSON uses `TypeId.to_dict()`; a raw
spelling or unvalidated string is not an alternate representation.

Each `StructuredHIRProgram` owns one completed local `TypeArena` snapshot and
`type_arena_digest`. The snapshot is closed (`allow_unresolved=false`) and is
the sole interning authority for declarations, functions, nodes, contracts,
flows, machines, and bound FFI identities. The reader validates outer exact
keys/contract/schema/invariants, restores the closed arena and checks its
digest, restores the typed FFI model directly, restores native and typed
records, then checks source digest, uniqueness, cross-record identities, and
canonical roundtrip invariants. Each `TypeId` must exist in the arena and
`arena.canonical(type_id)` must equal the retained canonical HIR spelling. The
reader never reparses spelling and has no post-load fallback parser. HIR v11
artifacts are rejected rather than upgraded through a compatibility shim.

Aliases are normalized before interning (`Int`/`UInt`/`Float` map to
`Int64`/`UInt64`/`Float64`); qualified names such as `app.Int` remain
distinct. A nominal `TypeId` is stable by nominal name even when declaration
semantics change and its `RevisionId` changes. `TypeId` identifies a type,
`SymbolId` a declaration, `RevisionId` a declaration revision, and a future
`LayoutId` (if introduced) a physical layout; these identities are not
interchangeable. Retained spelling remains for diagnostics, while `TypeId`
is authoritative for semantic validation.

This completes the issue #84 HIR, ownership, borrow, ContractGraph, and FFI
authority cutover. It does not claim an RIR, MIR, LLVM, GPU, or backend TypeId
cutover and does not change generated-C semantics. Issues #85 and #86 remain
open for representation/layout and executable-IR scope.

## Regenerating generated lockfiles

There is no dedicated `merlo lock` CLI command. The supported resolver API is
the public `resolve_dependencies` operation:

```console
python -c 'from merlo.project import resolve_dependencies; resolve_dependencies("PROJECT")'
```

Run that command once per project root containing `merlo.toml` (for example,
`examples/access-log`), after installing the alpha.3 compiler. It rewrites
that root's canonical `merlo.lock`; repeat for nested package roots such as
`examples/packages/vendor/greeting` when regenerating the complete example
corpus. Do not hand-edit compatibility fields. The checked-in 22 lockfiles
(20 top-level example roots, the nested
`examples/packages/vendor/greeting`, and `selfhost`) have been regenerated to
`compatibility.hir: 12` and `compatibility.semantic_world: 19`. Downstream
lockfiles with older compatibility values must run the supported resolver
command above.

SemanticWorld now includes the canonical constant-range analysis payload.
Compiler results expose the same payload and merge its checked-arithmetic and
cast-safety obligations into Obligation IR.
Compiler and SemanticWorld payloads also include bounded postcondition results.
Consumers must preserve the distinction between exhaustive proofs,
counterexamples, incomplete bounds, and unsupported HIR.
Z3 remains optional (`pip install merlo[verify]`) and is loaded only for
`merlo check --smt z3`. Normal checks and builds remain dependency-free and
emit a deterministic disabled SMT report.
Compiler and SemanticWorld payloads now include generated property cases and
normalized typed counterexamples. Consumers must honor each property's
`exhaustive` flag and requirements before replay.
Closure-rate consumers should read the exact closed/total counts or integer
`closed_rate_basis_points`; refutations and incomplete evidence are never
included in the numerator.
Semantic refactor previews now use the versioned `merlo.change-ir.v1` envelope
with target revisions, world binding, immutable metadata, canonical edit
identities, and a digest. Unsupported operations use the same envelope and
cannot be applied.
The old ad hoc `TaskCapsule` mapping is removed. `context.compile` now returns
the digest-bound `merlo.semantic-capsule.v1` contract with target-scoped
verification evidence.
Change-bound impact analysis is serialized as `merlo.semantic-impact.v1`.
Consumers should call `impact.change` with the full ChangeIR envelope instead
of inferring affected symbols from edited paths.
ChangeIR apply receipts now include a journaled change transaction. Consumers
may load the transaction by ID to perform exact rollback or replay; ad hoc
source restoration is no longer part of ChangeIR.

Patch evidence and preservation are distinct v1 contracts. Patch evidence
proves the observed structural apply/rebuild chain. Preservation reports check
that the renamed program retained behavior-facing contracts, authority, and
verification evidence.


## Borrow summary v3

`merlo.borrow-summary.v3` replaces string source/result paths and semantic
`call_path` values with a `BorrowRelation` plus a finite structural
`BorrowPlacePath`. Consumers must read relation fields below `relation` and
treat `witness_path` as diagnostic-only. The allowed path steps are
`Parameter`, `Field`, `Element`, `VariantPayload`, `Deref`, and
`RecursiveTail`; arbitrary strings are rejected. Recursive summaries now
converge by SCC relation widening and fail closed with
`BorrowSummaryNonMonotone` if an established relation disappears.

The HIR, RIR, and SemanticWorld schema bumps are mandatory. Regenerate lockfiles
and stored HIR/RIR/SemanticWorld artifacts; there is no compatibility shim for
v2 summary entries.

## File handles

Read and write handles are now different nominal resource types:

```text
fs.open_read(path)?  -> FileReader
fs.open_write(path)? -> FileWriter
```

Replace the shared close operation according to the handle mode:

```text
fs.close(reader) -> fs.close_read(reader)
fs.close(writer) -> fs.close_write(writer)
```

`fs.read_chunk` accepts only `FileReader`; `fs.write_chunk` accepts only
`FileWriter`. `close_read` requires `fs.read`, while `close_write` requires
`fs.write`.

## Literal escapes

Byte literals decode `\xNN` and octal escapes into one byte. Unicode escapes
inside byte literals are errors. Text literals accept valid Unicode scalar
escapes and reject surrogates, values above `U+10FFFF`, malformed escapes, and
unknown escapes.

## Function contracts

Move preconditions and postconditions to the leading `require` and `ensure`
clauses of a statement function. Contract expressions must be pure and Boolean;
postconditions use `result` for the returned value. Canonical, HIR, interface,
and SemanticWorld revisions now include these clauses.

## Record invariants

Place pure Boolean `invariant` clauses after a record's fields. Every record
constructor checks every clause. Record invariants participate in canonical,
HIR, descriptor, and SemanticWorld revisions.

## Contextual typed holes

Bare `?` now retains a typed completion obligation when an exact type is
available from context. Use postfix `value?` only for `Result` propagation.
Incomplete programs can be checked and indexed, but native builds fail with
`TypedHoleNotExecutable`.

## Typed obligation artifact

Compiler and SemanticWorld payloads now include the deterministic
`merlo.typed-obligation-ir.v1` artifact. Consumers must distinguish obligation
identity from revision and handle every typed disposition explicitly.
