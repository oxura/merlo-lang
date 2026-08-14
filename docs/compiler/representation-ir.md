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
type, source, `symbol_id`, `revision_id`, `ownership_provenance`, effects,
attributes, and child operations.

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
failure. Invalid generic types, unknown fields/types, non-positive arrays,
inline cycles, and unsupported operations fail before a `RepresentationProgram`
is returned. A schema or predecessor mismatch is a hard error; RIR is never
implicitly rebuilt from unrelated source text.

## Identity and provenance

Descriptor identities are stable hashes derived from builtin/type declaration
identity and dependencies. `_lower_operation()` derives operation and revision
IDs from HIR node identity, operation kind, and provenance. `source_hir_digest`
and `source_sha256` bind the whole artifact to its predecessor and source.
`symbol_id`, `revision_id`, source spans, and `ownership_provenance` are copied
into each operation and function for later MIR/backend diagnostics.

## Current-alpha limitations

- The representation implementation is current and consumed by
  `compile_project()`, but its source types originate in the transitional
  concise/CPython-AST frontend described in [frontend](frontend.md).
- RIR supplies layout/drop metadata; it is not a standalone borrow checker and
  does not establish runtime memory safety by itself.
- RFC 0001's bound frontend and final package split are accepted/planned, so
  callers must use `lower_structured_hir_to_rir()` rather than any planned
  `ir/representation` package path.
