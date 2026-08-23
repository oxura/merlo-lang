# Performance MIR contract

## Purpose

Performance MIR makes representation operations and control flow explicit for
deterministic backend optimization. It is the last IR before C11 emission.

## Inputs

`lower_rir_to_performance_mir(hir, representation)` in
[`src/merlo/representation_mir.py`](../../src/merlo/representation_mir.py)
consumes both the HIR predecessor and its `RepresentationProgram`.
`optimize_general_mir(mir)` consumes the resulting MIR and returns an optimized
value.

## Outputs

The lowerer returns `GeneralPerformanceMIR`, contract
`merlo.performance-mir.general-representation.v3`, schema version `3`. It
contains `GeneralMIRFunction` CFGs made of `GeneralMIRBlock` instructions and
`GeneralMIRTerminator` values. Instructions carry operands/results/places,
`TypeId`-typed value identities, source span, symbol/revision IDs, ownership
provenance, effects, and attributes. The program also binds every descriptor
`TypeId` to its `LayoutId` and requires the closed predecessor
`FrozenTypeArena`. The coordinator records both `mir` and `optimized_mir`
artifacts; the optimized value has its own digest and `optimized=True`.

## Invariants

Blocks, branches, calls, loads/stores, allocation, enum tags, moves, drops, and
bounds checks are explicit. Domain JSON operations are rejected; the entry
function must exist and schema version remains `3`. When cleanup is required,
at least one `drop_value` instruction is present. HIR, RIR, descriptor, and
drop-plan predecessor digests are retained. Optimization preserves source
identity, ownership provenance, effects, predecessor lineage, `TypeId` bindings,
and descriptor `LayoutId` bindings.

`verify_general_mir` runs at MIR construction, deserialization, and every
optimization boundary. It fails closed on a non-frozen or mismatched
`TypeArena`, predecessor or target-spec digest drift, unknown/duplicate
`TypeId`/`LayoutId`/`DropPlanId` bindings, parameter/return/operand/result/place
identity mismatches, duplicate values or instructions, missing CFG targets,
undefined terminator values, invalid nested attributes, missing drop plans,
and call-signature drift. The optional RIR predecessor is reverified and its
descriptor layouts and drop plans must equal the MIR bindings.

## Failure modes

Missing entry functions, schema drift, domain intrinsics, missing required drop
glue, malformed CFG construction, and predecessor mismatch are hard failures,
not fallback paths. The coordinator surfaces type/value failures as production
lowering diagnostics.

## Trusted boundary

The MIR digest and predecessor links bind optimization to the exact HIR/RIR
inputs. The recorded invariants and instruction provenance are the contract
consumed by the C11 backend; an optimized digest is not a proof of arbitrary
native-code equivalence.

## Experimental boundary

This alpha has one native target and narrow deterministic optimization passes.
MIR is backend-oriented and does not provide an async scheduler, register
allocator, or multi-target ABI contract. The RFC 0001 `ir/mir` package boundary
is not a current import.

## Verification commands

```console
merlo build PROJECT --json
merlo run PROJECT --json
```
