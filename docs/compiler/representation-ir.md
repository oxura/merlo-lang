# Representation IR contract

## Inputs and outputs

`lower_structured_hir_to_rir(hir)` in
[`merlo/representation_ir.py`](../../merlo/representation_ir.py) consumes a
`StructuredHIRProgram`. It builds `TypeDescriptor` values, type-directed
`DropPlan` values, and `RIRFunction` trees of `RIROperation` nodes, returning a
`RepresentationProgram` with contract `merlo.representation-ir.v1` and schema
version `1`.

`TypeDescriptor` records size, alignment, ABI class, copy/move/drop classes,
inline and indirect dependencies, and `source_type_identity`. Operations retain
type, source, `symbol_id`, a derived `revision_id`, `ownership_provenance`,
effects, attributes, and child operations.

## Invariants

`RepresentationProgram.__post_init__` rejects schema drift, duplicate
Descriptors/functions, a missing entry function, and domain-level JSON
intrinsics. Inline layout cycles are rejected by the descriptor builder;
owning indirection cycles are permitted. Drop plans are type-directed and
include active enum payloads, fields, array elements, buffers, boxes, and file
close actions where needed. The serialized `invariants` object records source
identity and ownership-provenance preservation.

## Failure modes

`RepresentationCompileError` covers representation, layout, and ownership
failure. Unknown/invalid generic types, unknown fields, negative or malformed
array lengths, inline cycles, and unsupported operations fail before a
`RepresentationProgram` is returned. `Array[T,0]` is currently accepted and
produces a zero-size descriptor. A schema or predecessor mismatch is a hard
error; RIR is never implicitly rebuilt from unrelated source text.

## Identity and provenance

`source_hir_digest` binds the artifact to the exact serialized HIR predecessor.
The similarly named `source_sha256` is copied from HIR; on the production
native-module path HIR assigns `CanonicalProgram.semantic_hash`, so this field
is not always a hash of original source bytes. `_lower_operation()` derives a
new RIR operation revision from HIR revision, operation kind, and ownership
provenance rather than copying the HIR revision. `symbol_id` and source spans
remain attached where the HIR operation provides them.

## Current-alpha limitations

- The representation implementation is current and consumed by
  `compile_project()`, but its source types originate in the transitional
  concise/CPython-AST frontend described in [frontend](frontend.md).
- RIR supplies layout/drop metadata; it is not a standalone borrow checker and
  does not establish runtime memory safety by itself.
- RFC 0001's bound frontend and final package split are accepted/planned, so
  callers must use `lower_structured_hir_to_rir()` rather than any planned
  `ir/representation` package path.
