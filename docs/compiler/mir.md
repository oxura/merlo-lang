# Performance MIR contract

## Inputs and outputs

`lower_rir_to_performance_mir(hir, representation)` in
[`merlo/representation_mir.py`](../../merlo/representation_mir.py) consumes
both the predecessor `StructuredHIRProgram` and `RepresentationProgram` and
returns `GeneralPerformanceMIR` (`merlo.performance-mir.general-representation.v1`).
`optimize_general_mir(mir)` returns a new optimized value with
`optimized=True` and named optimization passes. `compile_project()` emits both
`mir` and `optimized_mir` artifacts.

A `GeneralPerformanceMIR` contains `GeneralMIRFunction` CFGs, each made of
`GeneralMIRBlock` instructions and a `GeneralMIRTerminator`. Instructions carry
operands/results, type, source span, symbol/revision IDs, ownership provenance,
effects, and attributes.

## Invariants

MIR makes basic blocks, branches, calls, loads/stores, allocations, enum tags,
moves, drops, and bounds checks explicit in its serialized invariants. Domain
JSON operations are rejected. `entry_function` must exist; schema version must
remain `1`. If `requires_drop_glue` is true, at least one `drop_value`
instruction must exist. Predecessor digests (`source_hir_digest`,
`representation_ir_digest`, descriptor and drop-plan digests) are retained.

Optimization is deterministic and must preserve the source identity,
ownership provenance, effects, and predecessor relationship while changing only
instruction selection/shape authorized by the existing passes.

## Failure modes

Missing entry functions, schema drift, domain intrinsics, missing required drop
glue, malformed CFG construction, and predecessor mismatch are hard
`ValueError`/`RepresentationCBackendError` failures, not fallback paths. The
coordinator wraps type/value failures during lowering in
`ConciseApplicationError`.

## Identity and provenance

`GeneralMIRInstruction` copies source spans, operation symbols, effects, and
ownership provenance from RIR. Its revision ID is derived from the RIR
revision, MIR operation, and instruction ordinal. The MIR digest is the SHA-256
of stable sorted JSON, and the optimized artifact has its own digest and parent
link rather than overwriting the unoptimized proof object.

## Current-alpha limitations

- The current MIR is a backend-oriented performance IR, not the planned
  `ir/mir` package boundary in RFC 0001.
- Optimization passes are intentionally narrow; a MIR digest proves the
  recorded artifact chain, not equivalence to arbitrary native code.
- There is one native target and no async scheduler, register allocator, or
  multi-target ABI contract in this alpha.
