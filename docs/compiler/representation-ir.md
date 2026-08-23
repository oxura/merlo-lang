# Representation IR contract

## Purpose

Representation IR assigns physical descriptors, ABI classes, ownership classes,
and type-directed cleanup plans to typed HIR without changing its meaning.

## Inputs

`lower_structured_hir_to_rir(hir)` in
[`src/merlo/representation_ir.py`](../../src/merlo/representation_ir.py)
consumes one `StructuredHIRProgram` predecessor. It does not rebuild RIR from
unrelated source text.

## Outputs

The function returns `RepresentationProgram`, contract
`merlo.representation-ir.v6`, schema version `6`. It contains `TypeDescriptor`
values, type-directed `DropPlan` values, and `RIRFunction` trees of
`RIROperation` nodes. Descriptors record size, alignment, ABI class,
copy/move/drop classes, dependencies, contained-borrow/resource properties,
the exact contained borrow/resource leaf types, `TypeId` authority, and
`LayoutId` physical-layout identity. Operations retain type and `TypeId`,
source span, symbol/revision identity, ownership provenance, effects,
attributes, and children. Each function also carries the digest-bound
`merlo.borrow-summary.v4` relation and diagnostic witness contract lowered
from HIR, so downstream inspection retains interprocedural direct and
contained-borrow origins without making witnesses semantic. RIR v6 requires
the closed predecessor `FrozenTypeArena`; retained spellings are diagnostic
projections only.

## LayoutId hash domain

`LayoutId` is the opaque SHA-256 identity of a physical descriptor. Its
canonical payload uses contract `merlo.layout-id.v1`, schema `1`, the target
specification contract and digest, target triple, endianness, pointer width,
address space, ABI policy, ABI/preferred alignment, size, representation kind,
packing, ABI class, field offsets plus child `LayoutId`s, enum tag encoding
plus payload `LayoutId`s, payload offsets, niche policy, collection length,
and element/payload/key/value child `LayoutId`s. The payload is serialized as
UTF-8 JSON with sorted keys, no insignificant whitespace, and SHA-256 is
applied to those bytes.

Semantic `TypeId`, declaration names, source spans, drop plans, and diagnostic
borrow witnesses are not part of this domain. Changing physical target policy
or any physical child layout therefore changes the `LayoutId`; changing only
semantic spelling or diagnostic provenance does not.

The RIR digest canonicalizes those diagnostic witness arrays to empty values;
changing only a witness therefore preserves both predecessor and RIR hashes.

## Invariants

Schema drift, duplicate descriptors/functions, a missing entry function, and
domain-level JSON intrinsics are rejected. Inline layout cycles fail; owning
indirection cycles are allowed. Drop plans account for active enum payloads,
fields, arrays, buffers, boxes, and closeable file resources. The serialized
invariants preserve source identity and ownership provenance.

## Recursive layout dependency rules

Recursive layout validation runs before descriptor construction. It traverses
the closed HIR `TypeContext`/`TypeId` graph, walks every nested type argument,
and records the nominal target plus the source path. `BorrowSummary` relations
and their diagnostic witnesses are not inputs to this analysis.

| Dependency path | Layout mode | Examples |
| --- | --- | --- |
| nominal field or variant payload; `Option.payload`; `Result.ok` / `Result.error`; `Array.element` | inline | `Node`, `Option[Node]`, `Result[Node,Error]`, `Array[Node,4]` |
| pointer pointee; `Box.payload`; `Vec.element`; `Map.key` / `Map.value`; `Borrow.payload`; `Slice.element`; `Fn` or `Closure` parameters and return | indirect | `Box[Node]`, `Vec[Node]`, `Map[Text,Box[Node]]`, `Fn[Node,Node]` |

Inline edges form the layout graph. A cycle is rejected with the shortest
cycle; ties are resolved lexicographically by nominal path and then by
structural branch path. Indirect edges do not participate in that cycle graph,
so ownership wrappers may cross recursive boundaries. Descriptor dependency
lists use the same traversal and are sorted for stable serialization.

The regression was a nominal dependency loss, not a borrow-analysis failure:
the old generic helper flattened arguments with `",".join(...)` and returned
only one generic slot. `Result[Leaf,Error]` therefore became the non-nominal
string `Leaf,Error`; neither `Leaf` nor `Error` was recorded as a dependency.
The structural visitor preserves each parsed argument and is covered by a
round-trip regression test.

Accepted recursive layouts include `Option[Box[Node]]`, `Vec[Result[Node,Error]]`,
`Map[Text,Box[Graph]]`, and `Fn[Node,Node]`. Rejected layouts include direct
`Node` self-fields, `Option[Node]` self-fields, `Result[Node,Error]` self-fields,
and `Array[Option[Node],4]` self-fields. A map with a non-`Text` key or a
non-scalar value outside a nominal layout field remains rejected by the alpha
front end. Owner-valued map fields are layout-only; runtime Map operations
remain scalar-only.

## Failure modes

`RepresentationCompileError` covers layout, representation, and ownership
failures. Unknown generic types or fields, negative/malformed array lengths,
inline cycles, unsupported operations, predecessor mismatches, and schema drift
fail before a `RepresentationProgram` is returned. Zero-length arrays are
currently accepted and produce a zero-size descriptor.

## Trusted boundary

`source_hir_digest` binds RIR to the semantic HIR predecessor; diagnostic-only
witness changes intentionally preserve that digest.
Type-directed descriptors and drop plans are the physical-layout and cleanup
boundary; they are not inferred from generated C names or allocation order.
Generic descriptors obtain contained-borrow and contained-resource properties
recursively from every type argument rather than from constructor spelling.

## Experimental boundary

RIR supplies layout and drop metadata but is not a standalone borrow checker and
does not establish runtime memory safety. Shared ownership is not a current
production descriptor class. The final package split proposed by RFC 0001 is
not a published import path.

## Verification commands

```console
merlo build PROJECT --json
merlo inspect main PROJECT --json
```
