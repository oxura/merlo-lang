# Type Arena v1

Merlo currently accepts source-facing type spellings such as:

```text
Text
Vec[Text]
Result[Option[Box[app.model.User]],app.Error]
Array[UInt64,8]
```

Several compiler stages still carry those values as strings. That was useful
during the research prototype, but it is not a suitable long-term contract for
ownership, layout, MIR, LLVM, parallel lowering, or semantic tooling.

`merlo.type-arena.v1` introduces a small content-addressed foundation for the
migration.

## Boundary

The source boundary remains textual:

```text
source spelling
    ↓ parse + alias normalization
structural TypeRef graph
    ↓ deterministic interning
TypeId
```

After a stage adopts the arena, semantic identity should use `TypeId`. Human
diagnostics may continue to render the canonical spelling.

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

A qualified name such as `app.Int` is not rewritten.

## Serialization

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

Unresolved `?` types are rejected by default. A frontend-only arena may opt in
explicitly with `allow_unresolved=True`; closed compiler artifacts must not.

## Initial scope

Type Arena v1 is a foundation, not a whole-compiler cutover. This first step:

- adds `TypeId`, `TypeRef`, and `TypeArena`;
- provides strict JSON roundtrip and tamper detection;
- centralizes structural constructor arity validation;
- makes `TypePropertyResolver` the first resolver-local production consumer;
- consistently classifies `Int`, `UInt`, and `Float` aliases by their canonical
  scalar types instead of treating them as unknown owner types;
- does not change valid source grammar or HIR/RIR/MIR artifact schemas, and does
  not yet replace their `type_name: str` fields with `TypeId`;
- rejects malformed generic arities and unknown generic constructors earlier.

The migration should proceed in narrow PRs:

1. Add `TypeId` beside existing HIR parameters, fields, variants, and nodes;
   HIR is the first artifact migration.
2. Migrate ownership and ContractGraph consumers, then remove duplicate parsing.
3. Migrate representation IR descriptors.
4. Migrate executable MIR.
5. Make C11 and future LLVM/GPU backends consume structural identities directly.

No backend should grow a second type parser during this migration.
