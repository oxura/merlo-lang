# Ownership contract

## Inputs and outputs

Ownership enters HIR through `HIRParameter.ownership` and `HIRNode.ownership`
in [`merlo/structured_hir_v2.py`](../../merlo/structured_hir_v2.py). RIR turns
those labels into `TypeDescriptor` copy/move/drop classes and type-directed
`DropPlan` values in [`merlo/representation_ir.py`](../../merlo/representation_ir.py).
`RepresentationProgram` serializes descriptors and drop plans; it does **not**
contain `StoragePolicy` values. `storage_policy_matrix()` exists as a helper
but is not called by `lower_structured_hir_to_rir()`.

MIR materializes ownership operations and `drop_value` instructions in
[`merlo/representation_mir.py`](../../merlo/representation_mir.py); the C
backend validates the predecessor chain but currently does not consume MIR
instructions or RIR drop plans to drive emission.

## Invariants

Trivial/scalar descriptors may copy; owning descriptors have `copy_class`
`forbidden` and explicit move/invalidation behavior; borrow descriptors are
trivial-copy views. Descriptor and drop-plan metadata records storage-relevant
copy, move, and drop actions. Drop plans are type-directed: record fields,
active enum payloads, arrays, vectors, maps, boxes, builders, and file readers
use distinct cleanup actions. The HIR/RIR/MIR serialized fields
`ownership`, `ownership_provenance`, and drop metadata are the cross-stage
contract; they are semantic data, not comments for a future backend to
reinterpret.

## Failure modes

`RepresentationCompileError` rejects unsupported ownership/layout combinations,
unknown descriptors, illegal inline recursion, and invalid partial
initialization. `RepresentationProgram` rejects missing entry functions or
schema drift. `GeneralPerformanceMIR` rejects missing type-directed drop glue
when required. Backend predecessor mismatches fail before C emission.

## Identity and provenance

`TypeDescriptor.source_type_identity` binds physical layout and cleanup to the
source type identity. RIR operations preserve HIR symbol IDs and source spans
where present, but derive each operation `revision_id` from the HIR revision,
operation kind, and ownership provenance. MIR instructions derive further
revision IDs while retaining that provenance and source span. Drop actions are
selected from descriptors and plans, never from generated C names or incidental
allocation order.

## Current-alpha limitations

- Current alpha ownership is represented in the lowering stack but is not a
  complete source-level borrow checker: the structured HIR contract explicitly
  excludes low-level CFG/drop flags, and the production frontend remains
  transitional.
- Borrowed views are supported, but `_DescriptorBuilder.get()` rejects every
  `Shared[...]` type with `SharedOwnershipUnsupported`; no current descriptor
  enables shared ownership and `StoragePolicy.shared_ownership` is never true
  in production lowering.
- The accepted RFC 0001 bound-program ownership facts and clean frontend cutover
  are planned rather than current APIs.
