# Ownership contract

## Purpose

Ownership metadata follows values from typed HIR through representation and MIR
to type-directed cleanup. It records copy, move, borrow, invalidation, and drop
obligations without pretending that metadata alone proves runtime safety.

## Inputs

HIR carries ownership labels on `HIRParameter` and `HIRNode` in
[`src/merlo/structured_hir_v2.py`](../../src/merlo/structured_hir_v2.py).
Each HIR function also carries a versioned `BorrowSummary`. A summary entry
binds a returned direct or contained borrow to a formal parameter index,
source/result paths, borrow type, and one of the ownership modes `value`,
`borrow`, `borrow_mut`, `owned`, `contained_borrow`, or
`owned_contained_borrow`. Summaries are computed to a deterministic fixed
point over local calls and are part of the HIR digest.
RIR consumes those labels through `TypeDescriptor` and `DropPlan` in
[`src/merlo/representation_ir.py`](../../src/merlo/representation_ir.py).
MIR materializes ownership operations and `drop_value` instructions in
[`src/merlo/representation_mir.py`](../../src/merlo/representation_mir.py).

## Outputs

Trivial/scalar descriptors may copy. Owning descriptors have a forbidden copy
class and explicit move/invalidation behavior; borrowed views are trivial-copy
views. Drop plans distinguish record fields, active enum payloads, arrays,
vectors, maps, boxes, builders, and file readers. HIR/RIR/MIR serialize
`ownership`, `ownership_provenance`, and cleanup metadata.

## Invariants

A moved owner cannot be reused; a live borrow cannot overlap a conflicting
mutation. Drop actions are selected from type descriptors and plans, not from
generated C names or allocation order. RIR does not contain `StoragePolicy`
values; the `storage_policy_matrix()` helper is not a production lowering
stage. The backend checks predecessor identity before emission.
Call sites substitute summary formal origins into actual places, including
owning actuals that do not themselves contain a borrow. Conditional and
transitive origins are unioned. Missing or opaque summaries fail closed;
temporary owners are rejected at the entry boundary or by the native artifact
gate rather than treated as independent storage.

Recursive records and enums must cross an owning `Box[T]` or `Vec[T]`
indirection; inline layout cycles are rejected with their minimal cycle path.
Generated drop glue follows active enum tags, initialized vector elements, and
boxed payloads recursively before releasing their storage. Mutual recursion
uses forward C declarations and the same finite, type-directed drop plans.

Recursive records and enums must cross an owning `Box[T]` or `Vec[T]`
indirection; inline layout cycles are rejected with their minimal cycle path.
Generated drop glue follows active enum tags, initialized vector elements, and
boxed payloads recursively before releasing their storage. Mutual recursion
uses forward C declarations and the same finite, type-directed drop plans.

## Failure modes

`RepresentationCompileError` rejects unsupported ownership/layout combinations,
unknown descriptors, illegal inline recursion, and invalid partial
initialization. RIR rejects schema or entry-function failures. MIR rejects
missing type-directed drop glue when cleanup is required. Predecessor mismatch
fails before C emission.

## Trusted boundary

Descriptor copy/move/drop classes and the HIR/RIR/MIR provenance fields are the
cross-stage ownership contract. Generated C is an implementation of that
contract, not the source of ownership truth.

## Experimental boundary

The alpha is not a complete source-level borrow checker. Shared ownership is
not a production descriptor class, and `StoragePolicy.shared_ownership` is not
set by current lowering. Closure environments support restricted immutable and
owned captures, but not escaping borrowed, mutable, resource, or general shared
captures. Cycle collection, ordinary lifetime annotations, and manual memory
operations are outside the alpha surface.

## Verification commands

```console
merlo check PROJECT
merlo build PROJECT --json
merlo run PROJECT --json
```
