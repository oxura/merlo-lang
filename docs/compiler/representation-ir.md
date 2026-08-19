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
`merlo.representation-ir.v3`, schema version `3`. It contains `TypeDescriptor`
values, type-directed `DropPlan` values, and `RIRFunction` trees of
`RIROperation` nodes. Descriptors record size, alignment, ABI class,
copy/move/drop classes, dependencies, contained-borrow/resource properties,
the exact contained borrow/resource leaf types, and `source_type_identity`. Operations
retain type, source span, symbol/revision identity, ownership provenance,
effects, attributes, and children. Each function also carries the digest-bound
`merlo.borrow-summary.v2` provenance contract lowered from HIR, so downstream
inspection can retain interprocedural direct and contained-borrow origins.

## Invariants

Schema drift, duplicate descriptors/functions, a missing entry function, and
domain-level JSON intrinsics are rejected. Inline layout cycles fail; owning
indirection cycles are allowed. Drop plans account for active enum payloads,
fields, arrays, buffers, boxes, and closeable file resources. The serialized
invariants preserve source identity and ownership provenance.

## Failure modes

`RepresentationCompileError` covers layout, representation, and ownership
failures. Unknown generic types or fields, negative/malformed array lengths,
inline cycles, unsupported operations, predecessor mismatches, and schema drift
fail before a `RepresentationProgram` is returned. Zero-length arrays are
currently accepted and produce a zero-size descriptor.

## Trusted boundary

`source_hir_digest` binds RIR to the exact serialized HIR predecessor.
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
