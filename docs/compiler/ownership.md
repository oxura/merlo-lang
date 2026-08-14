# Ownership contract

## Inputs and outputs

Ownership enters HIR through `HIRParameter.ownership` and `HIRNode.ownership`
in [`merlo/structured_hir_v2.py`](../../merlo/structured_hir_v2.py). RIR turns
those labels into `StoragePolicy`, `TypeDescriptor` copy/move/drop classes, and
`DropPlan` values in [`merlo/representation_ir.py`](../../merlo/representation_ir.py).
MIR then materializes ownership operations and `drop_value` instructions in
[`merlo/representation_mir.py`](../../merlo/representation_mir.py); the C
backend consumes the resulting plans.

## Invariants

Trivial/scalar descriptors may copy; owning descriptors have `copy_class`
`forbidden` and explicit move/invalidation behavior; borrow descriptors are
trivial-copy shared views. `StoragePolicy` records storage, copy, move, drop,
partial initialization, and shared ownership. Drop plans are type-directed:
record fields, active enum payloads, arrays, vectors, maps, boxes, builders, and
file readers use distinct cleanup actions. A moved owner cannot be silently
reused; the operation/provenance stream must record the transfer and eventual
invalidation/drop.

The HIR/RIR/MIR serialized fields `ownership`, `ownership_provenance`, and drop
metadata are the cross-stage contract. They are semantic data, not comments for
the backend to reinterpret.

## Failure modes

`RepresentationCompileError` rejects unsupported ownership/layout combinations,
unknown descriptors, illegal inline recursion, and invalid partial
initialization. `RepresentationProgram` rejects missing entry functions or
schema drift. `GeneralPerformanceMIR` rejects missing type-directed drop glue
when required. Backend predecessor mismatches fail before C emission.

## Identity and provenance

`TypeDescriptor.source_type_identity` binds physical layout and cleanup to the
source type identity. RIR operations preserve HIR symbol/revision IDs and emit
`ownership_provenance`; MIR instructions derive new revision IDs while retaining
that provenance and source span. Drop actions are selected from descriptors and
plans, never from generated C names or incidental allocation order.

## Current-alpha limitations

- Current alpha ownership is represented in the lowering stack but is not a
  complete source-level borrow checker: the structured HIR contract explicitly
  excludes low-level CFG/drop flags, and the production frontend remains
  transitional.
- Shared ownership is represented for selected descriptor policies; a general
  cycle collector, async borrowing, and multi-threaded memory model are absent.
- RFC 0001 is accepted, but its future bound-program ownership facts and clean
  frontend cutover are planned rather than current APIs.
