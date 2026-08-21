# RFC 0004: Type Arena and Structural Type Identity v1

- Status: Implemented
- Accepted: 2026-08-21
- Accepted by: `@oxura`
- Acceptance evidence: https://github.com/oxura/merlo-lang/issues/88
- Implemented: 2026-08-21
- Implementation: PR #81, merge `e2c31b57b32be5befd5d25a213b13102d2d671c7`
- Target: `0.1.0-alpha.3-dev`

## Problem

Merlo compiler stages still exchange type spellings as strings. Equal semantic types
can therefore arrive with aliases such as `Int` and `Int64`, and each consumer can
silently invent its own parsing, arity, normalization, or fallback rules. Textual
identity is also a weak boundary for cached artifacts and AI-authored semantic edits:
a malformed or tampered type graph may be detected only after another stage has
already consumed it.

Concrete motivating cases:

```text
Int
Array[Text, 4]
Result[Vec[Text],app.Error]
```

`Int` and `Int64` must have one semantic identity. `Array[Text]` must fail before
interning because its length argument is absent. A serialized node whose claimed
identity does not match its canonical content must fail closed.

## Proposed semantics

### Structural references

A `TypeRef` contains one normalized constructor and zero or more argument `TypeId`
values. Its semantic payload is versioned by `merlo.type-ref.v1`.

The v1 aliases normalize at every standalone `TypeRef` boundary:

```text
Int   -> Int64
UInt  -> UInt64
Float -> Float64
```

Qualified nominal names remain distinct and are not suffix-normalized.

### Content identity

`TypeId` is the full lowercase SHA-256 of a canonical JSON envelope containing
the `merlo.type-id.v1` contract and the `TypeRef` semantic payload. Identity
depends on normalized structure, not arena insertion order or source formatting.

### Constructor arity

`type_parser.py` is the single v1 authority for structural constructor arity.
Fixed constructors reject missing or extra arguments. `Array` requires element type
and length. Function-like constructors require at least one parameter and a return
type. Unknown nominal leaves remain valid for user declarations; unknown generic
constructors fail.

### Arena behavior

`TypeArena` interns validated references and is versioned by `merlo.type-arena.v1`.
All child identities must already exist before a node is added. Failed validation
must leave the arena byte-identical.

Closed arenas reject unresolved `?`. A frontend-only arena may opt in explicitly.
Serialization is canonical and sorted by `TypeId`. Deserialization rejects unknown
children, cycles, duplicate identities, noncanonical aliases, contract or schema
drift, and content/hash mismatch.

Standalone `TypeRef.from_dict` canonicalizes aliases. `TypeArena.from_dict` is
stricter and rejects alias spellings in snapshots, preserving one byte
representation.

## Diagnostics

Direct structural parsing raises `GenericTypeSyntaxError`. `TypeArena.intern_text`
translates malformed textual input to `TypeArenaError`; closed-schema violations
raise `TypeArenaSchemaError`; absent identities raise `UnknownTypeIdError`;
unresolved closed types raise `UnresolvedTypeError`.

Diagnostics must identify the violated constructor, contract, identity, or reference
without accepting a partial mutation.

## Ownership, effects, and compatibility

`TypePropertyResolver` is the first production consumer and owns a resolver-local
arena. `Int`, `UInt`, and `Float` now receive their canonical scalar properties
instead of the conservative unknown-owner fallback. Unknown nominal leaves remain
conservatively move-only.

This RFC does not change valid source grammar, effect semantics, or capability
semantics. It does not migrate HIR/RIR/MIR artifact fields, which remain textual
in v1. The three new contracts are additive, but direct standalone `TypeRef`
alias payloads now canonicalize, and malformed generic arities or unknown generic
constructors fail earlier.

## Migration

1. Land the isolated arena, arity authority, resolver-local consumer, and negative
   tests without changing HIR/RIR/MIR schemas.
2. Add `TypeId` beside existing HIR spellings at a versioned boundary; HIR is the
   first artifact migration.
3. Migrate ownership and ContractGraph consumers, then remove duplicate parsing.
4. Migrate RIR descriptors.
5. Migrate executable MIR.
6. Make C11 and future backends consume structural identities directly.

Each step requires a clean cutover for its callers; no permanent aliases or parallel
type authorities are allowed.

## Rollback

Before a schema cutover, revert the arena consumer and its additive modules as one
commit; existing textual artifacts remain readable. After a future artifact-schema
cutover, rollback requires restoring the preceding schema version and its reader,
not silently interpreting `TypeId` as a spelling.

## Rejected alternatives

- Hash source spellings: aliases and formatting would create false identities.
- Truncated hashes: avoidable collision risk at a compiler contract boundary.
- A process-global arena: leaks state across compilations and tests.
- Let every stage parse strings: preserves the current split authority.
- Silently canonicalize arena snapshots: hides noncanonical persisted artifacts and
  weakens deterministic byte identity.

## Tests and evidence required for acceptance

- identical structures intern to identical full `TypeId` values across arenas;
- insertion order does not change JSON or digest;
- alias construction, decoding, and interning converge on fixed canonical IDs;
- fixed/variadic arity failures and unknown generic constructors fail before
  mutation;
- missing references, cycles, aliases, schema drift, and hash tampering fail closed;
- unresolved types require explicit opt-in;
- `TypePropertyResolver` alias behavior is covered at a production consumer;
- the complete production suite and pyflakes pass on the exact reviewed head.

## Follow-up decisions

- The first HIR schema carrying `TypeId` beside retained source/rendered spelling
  is a new versioned boundary owned by issue #83.
- Ownership and ContractGraph migrate only after that HIR boundary under issue
  #84; RIR and executable MIR follow under issue #85.
- User-defined generic declarations, declaration-scoped arity, and package/source
  nominal provenance require a v2 identity contract under issue #86. V1 remains
  fail-closed for unknown generic constructors and does not claim cross-package
  user-declaration identity.
- Diagnostic source spelling remains alongside structural identity during the
  staged migration; issue #83 owns the first serialized field and reader contract.

Historical implementation note: PR #81 introduced `TypeArena` without
replacing textual HIR fields. Issue #83 completed that follow-up in compiler
schema 10; `merlo.structured-typed-hir.v10` is now the authoritative HIR
serialization boundary, while issues #84 and #85 remain open for later
consumer and layout scope.

## Acceptance record

The repository owner accepted the v1 identity envelope, alias normalization,
constructor authority, atomic failure behavior, snapshot strictness, diagnostics,
resolver compatibility, migration order, and rollback consequences on 2026-08-21
after the RFC 0003 prerequisite merged and all review findings were resolved.
Issue #88 records the review boundary; issues #83-#86 are explicitly future
migrations or v2 work and do not broaden this v1 contract.

Historical status note: the acceptance record above describes the RFC 0004
boundary at acceptance time. Issue #83 has since completed the HIR boundary in
compiler schema 10; `merlo.structured-typed-hir.v10` is authoritative for HIR
serialization, while #84 and #85 remain open for later consumers and layout.

At RFC0004 acceptance, this was not an independent human approval and did not
claim that the arena had replaced textual HIR/RIR/MIR fields. PR #81 merged the exact reviewed Type
Arena behavior to `main` as `e2c31b57b32be5befd5d25a213b13102d2d671c7`
after production, pyflakes, native, and repository policy gates passed.
