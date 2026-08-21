# Type Arena v1

Merlo accepts source-facing type spellings such as:

```text
Text
Vec[Text]
Result[Option[Box[app.model.User]],app.Error]
Array[UInt64,8]
```

Several compiler stages historically carried those values as strings. The
TypeArena/TypeContext boundary is now the semantic authority for structured HIR,
ownership, Place lookup, and borrow provenance. It does not itself authorize an
RIR, MIR, or backend representation-identity cutover.

## Boundary and lifecycle

The source boundary remains textual:

```text
source spelling
    ↓ parse + alias normalization
structural TypeRef graph
    ↓ deterministic interning
TypeId
```

The HIR builder creates one local mutable `TypeContextBuilder` per compilation.
It uses that builder, and its cached interning operations, for declarations,
parameters, fields, variants, functions, nodes, contracts, flows, machines,
machine state fields, and the HIR-only FFI JSON projection. The builder remains
open while HIR construction and the ownership/borrow analyses consume the same
context; it is frozen exactly once when the final `StructuredHIRProgram` is
assembled. A closed compiler artifact never reopens the arena or reparses a
retained spelling after load.

After a stage adopts the arena, semantic identity should use `TypeId`. Human
diagnostics may continue to render the retained canonical spelling. The
spelling is a diagnostic projection, not a second identity authority.

In memory the value is a `TypeId`; JSON uses `TypeId.to_dict()`. A serialized
string is not a substitute for a validated ID object.

## Identity

A `TypeRef` contains:

```text
constructor
argument TypeIds
```

Its `TypeId` is the SHA-256 of a canonical envelope containing the
`merlo.type-id.v1` contract and the versioned `TypeRef` semantic payload.
Consequences:

- the same normalized structure has the same identity in separate arenas;
- insertion order does not affect identities or serialized snapshots;
- qualified names remain distinct;
- aliases normalize in source parsing, direct `TypeRef` construction, and
  standalone `TypeRef.from_dict`;
- malformed, missing, cyclic, or tampered arena snapshots fail closed.

The v1 aliases are:

```text
Int   -> Int64
UInt  -> UInt64
Float -> Float64
```

A qualified name such as `app.Int` is not rewritten to `Int`. Nominal
declaration `TypeId`s are stable by qualified nominal name; declaration
semantics changing is represented by a new `RevisionId`, not by changing the
nominal type identity. `TypeId` is structural/nominal type identity,
`SymbolId` is declaration identity, and `RevisionId` is a semantic
declaration revision. A future `LayoutId`, if introduced, will describe a
physical layout and is not interchangeable with any of these IDs.

## Snapshot and digest

The arena snapshot contains:

```text
contract
schema_version
allow_unresolved
entries
```

Entries are sorted by `TypeId`, so byte serialization and the arena digest are
independent of insertion order. Every referenced argument must exist in the
same snapshot, and every identity is recomputed during deserialization.
Standalone `TypeRef.from_dict` canonicalizes aliases. Arena snapshots are
stricter: `TypeArena.from_dict` rejects a serialized alias before identity
validation, so snapshots have one canonical byte representation.

HIR v12 stores the snapshot plus `type_arena_digest` in
`StructuredHIRProgram`. The snapshot is closed (`allow_unresolved=false`).
The reader checks the outer contract, schema version, and envelope invariants
first; HIR v11 is strictly rejected. The arena is then restored, its
closedness is required, and the digest is recomputed and compared. HIR-only
FFI annotations are validated next, then
native syntax and typed HIR records are restored. Each present `TypeId` must
exist in the arena, and its canonical spelling must equal the retained
canonical HIR spelling. Finally, `StructuredHIRProgram` validates source
digest, uniqueness, cross-record identities, and canonical roundtrip
invariants. Every spelling/ID pair is present or absent together. There is
no fallback parser or compatibility reconstruction.

Unresolved `?` types are rejected by default. A frontend-only arena may opt
in explicitly with `allow_unresolved=True`; closed compiler artifacts must
not.

## HIR and consumer migration surface

HIR v12 carries `TypeId` beside retained spelling at every HIR-visible type
position. Semantic attribute spellings (closures, holes, result/error,
intrinsics, casts, foreign calls, collection operations, and map
specializations) carry paired identities; machine state identities remain
outside the arena. The serialized pairs are exact: `type` + `type_id`,
`payload_type` + `payload_type_id`, and `return_type` + `return_type_id`.
This includes declaration types, parameters, returns, record fields, enum
payloads, flow results, local `Let`/`Var`/`Assign` nodes, and contract-condition
result types. `HIRTypeDecl` has its nominal `type_id` beside `symbol_id` and
`revision_id`. `HIRMachineStateField` is `{name,type,type_id}`. The bound
in-memory FFI model stores IDs for extern parameters, extern return/error
types, `repr(C)` fields, and raw-pointer pointees. HIR JSON writes those stored
IDs directly and records the bound lifecycle explicitly, including
identity-empty FFI programs and empty `repr(C)` records. Cached C prototypes
are serialization data, not a spelling-reparse path. Access, ownership,
destructor, mutability, and nullability remain separate pointer policy fields
and are cross-checked against the parameter's structural pointer TypeId.

Aliases are already canonicalized before HIR stores them, so readers compare
the arena's canonical spelling with retained canonical HIR spelling rather than
reparsing source syntax. Qualified nominal names remain qualified and distinct.
Ownership, Place lookup, borrow provenance, and ContractGraph scheme resolution
now consume `TypeId` from the same mutable-then-frozen `TypeContext`; retained
spelling remains diagnostic or an out-of-scope backend projection only.
ContractGraph binds explicit variables, concrete identities, applied
constructors, and const arguments, then matches and instantiates through
`TypeRef` without reparsing actual types. Issue #85 covers
representation/layout identity and issue #86 covers later executable-IR scope;
both remain open.

## Initial scope

Type Arena v1 is a foundation with a complete HIR/ownership/borrow authority
boundary, not a whole-compiler cutover. This migration:

- keeps `TypeId`, `TypeRef`, and `TypeArena` as the single arena authority;
- gives structured HIR schema 12 one closed arena snapshot and digest;
- centralizes structural constructor arity validation;
- makes `TypePropertyResolver` the first resolver-local production consumer;
- consistently classifies `Int`, `UInt`, and `Float` aliases by their
  canonical scalar types instead of treating them as unknown owner types;
- rejects malformed generic arities and unknown generic constructors earlier;
- preserves existing RIR, MIR, backend, and generated-C semantics while
  ownership, Place lookup, borrow provenance, and ContractGraph use TypeId
  authority.

The migration proceeds in narrow commits:

1. Add `TypeId` beside existing HIR positions (the HIR v12 migration).
2. Keep machine state labels in their separate deterministic identity domain;
3. Migrate ownership, Place lookup, borrow provenance, and ContractGraph
   consumers and remove duplicate parsing (issue #84 scope, now landed).
4. Migrate representation IR descriptors and later physical layout identity
   (issue #85 scope).
5. Make C11 and future LLVM/GPU backends consume structural identities only
   after those boundaries are explicitly reviewed.
