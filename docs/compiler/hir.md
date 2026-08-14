# Structured HIR contract

## Purpose

Structured HIR is the typed semantic tree between canonical elaboration and
physical representation. It preserves source and semantic identity while
leaving layout and low-level control flow to later stages.

## Inputs

`compile_canonical_hir(program, entry_function="main")` in
[`src/merlo/structured_hir_v2.py`](../../src/merlo/structured_hir_v2.py)
consumes the typed `SurfaceProgram` retained by an in-memory
`CanonicalProgram`. A canonical JSON/source projection without that Surface
tree is rejected; serialized projections are not compiler input.

## Outputs

The function returns `StructuredHIRProgram`, contract
`merlo.structured-typed-hir.v2`, schema version `2`. It contains source
text/digest, `HIRTypeDecl`/`HIRField`/`HIRVariant` values, `HIRFunction`
records, typed parameters, an entry function, and tree-shaped `HIRNode`
bodies. `HIRNode.walk()` is the traversal used by current source-map
projection.

## Invariants

Each node has an ID, kind, span, scope, optional type, ownership, effects,
optional symbol/revision IDs, attributes, and children. Names, node IDs, and the
entry function are unique. HIR is a tree: CFG blocks, gotos, allocation, raw
pointers, and drop flags are rejected here. Stable JSON includes source and
semantic identity for every function and node.

## Failure modes

A missing retained Surface tree, unsupported expressions, invalid map
specializations, duplicate declarations, a missing `main`, duplicate node IDs,
forbidden low-level kinds, and schema drift raise `StructuredHIRCompileError`
or `ValueError`. The coordinator surfaces construction failures as a production
lowering diagnostic.

## Trusted boundary

The retained Surface tree, canonical program identity, and HIR spans/IDs form
the semantic handoff to RIR. HIR carries ownership and effect facts for later
checking but is not itself a complete borrow proof or physical-layout
specification.

## Experimental boundary

The current HIR builder internally adapts typed Surface nodes to
CPython-compatible AST objects so the established ownership and representation
lowerers remain shared. This adapter is implementation-local: Python AST is not
the frontend stage contract, and no projected source is reparsed. HIR
intentionally does not model low-level CFG or drop flags.

## Verification commands

```console
merlo check PROJECT
merlo expand PROJECT
merlo inspect main PROJECT --json
```
