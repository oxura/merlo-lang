# Type Arena v1

Merlo accepts source-facing type spellings such as:

```text
Text
Vec[Text]
Result[Option[Box[app.model.User]],app.Error]
Array[UInt64,8]
```

Several compiler stages still carry those values as strings. That was useful
during the research prototype, but it is not a suitable long-term contract
for ownership, layout, MIR, LLVM, parallel lowering, or semantic tooling.
`merlo.type-arena.v1` is the existing authority for the first structured HIR
type-identity migration. It does not itself authorize a whole-compiler
ownership, RIR, MIR, or backend cutover.

## Boundary and lifecycle

The source boundary remains textual:

```text
source spelling
    ↓ parse + alias normalization
structural TypeRef graph
    ↓ deterministic interning
TypeId
```

The HIR builder creates one local arena per `StructuredHIRProgram`. It uses
that arena, and its cached interning operations, for declarations,
parameters, fields, variants, functions, nodes, contracts, flows, machines,
machine state fields, and the HIR-only FFI JSON projection. The arena is
completed before the program is serialized and no process-global arena is
consulted. A closed compiler artifact never reopens the arena or reparses a
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

HIR v10 stores the snapshot plus `type_arena_digest` in
`StructuredHIRProgram`. The snapshot is closed (`allow_unresolved=false`).
When reading HIR, the outer object’s exact keys, contract, schema version,
and envelope invariants are checked first; HIR v9 is strictly rejected. The
arena is then restored, its closedness is required, and the digest is
recomputed and compared. HIR-only FFI annotations are validated next, then
native syntax and typed HIR records are restored. Each present `TypeId` must
exist in the arena, and its canonical spelling must equal the retained
canonical HIR spelling. Finally, `StructuredHIRProgram` validates source
digest, uniqueness, cross-record identities, and canonical roundtrip
invariants. Every spelling/ID pair is present or absent together. There is
no fallback parser or compatibility reconstruction.

Unresolved `?` types are rejected by default. A frontend-only arena may opt
in explicitly with `allow_unresolved=True`; closed compiler artifacts must
not.

## HIR migration surface

HIR v10 carries `TypeId` beside retained spelling at every HIR-visible type
position. The serialized pairs are exact: `type` + `type_id`,
`payload_type` + `payload_type_id`, and `return_type` + `return_type_id`.
This includes declaration types, parameters, returns, record fields, enum
payloads, flow results, local `Let`/`Var`/`Assign` nodes, and contract
condition result types. `HIRTypeDecl` has its nominal `type_id` beside
`symbol_id` and `revision_id`. `HIRMachineStateField` is
`{name,type,type_id}`. HIR-only FFI JSON annotates extern parameters,
extern return/error fields, and `repr(C)` fields with `type_name`/`type_id`;
the underlying FFI classes and contract remain unchanged.

Aliases are already canonicalized before HIR stores them, so readers compare
the arena's canonical spelling with retained canonical HIR spelling rather
than reparsing source syntax. Qualified nominal names remain qualified and
distinct. Downstream consumers continue reading retained spelling in this
migration; issues #84 and #85 cover later consumer/layout scope and remain
open.

## Initial scope

Type Arena v1 is a foundation, not a whole-compiler cutover. This migration:

- keeps `TypeId`, `TypeRef`, and `TypeArena` as the single arena authority;
- gives structured HIR schema 10 one closed arena snapshot and digest;
- provides strict JSON roundtrip, closed-snapshot validation, and tamper
  detection;
- centralizes structural constructor arity validation;
- makes `TypePropertyResolver` the first resolver-local production consumer;
- consistently classifies `Int`, `UInt`, and `Float` aliases by their
  canonical scalar types instead of treating them as unknown owner types;
- rejects malformed generic arities and unknown generic constructors earlier;
- preserves existing ownership, ContractGraph, RIR, MIR, backend, and
  generated-C semantics rather than claiming their migration.

The migration proceeds in narrow PRs:

1. Add `TypeId` beside existing HIR positions (the HIR v10 migration).
2. Migrate ownership and ContractGraph consumers, then remove duplicate
   parsing (issue #84 scope).
3. Migrate representation IR descriptors and later physical layout identity
   (issue #85 scope).
4. Migrate executable MIR only under a separately reviewed contract.
5. Make C11 and future LLVM/GPU backends consume structural identities only
   after those boundaries are explicitly reviewed.

No backend should grow a second type parser during this migration.
