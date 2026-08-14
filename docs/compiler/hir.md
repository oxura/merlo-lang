# Structured HIR contract

## Purpose

Structured HIR is the typed semantic tree between canonical elaboration and
physical representation. It preserves source and semantic identity while
leaving layout and low-level control flow to later stages.

## Inputs

`compile_canonical_hir(program, entry_function="main")` in
[`src/merlo/structured_hir_v2.py`](../../src/merlo/structured_hir_v2.py)
consumes a `CanonicalProgram`. The production coordinator supplies the same
canonical predecessor used for semantic digests and interface checks.

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

Empty or malformed canonical source, unsupported expressions, invalid map
specializations, duplicate declarations, a missing `main`, duplicate node IDs,
forbidden low-level kinds, and schema drift raise `StructuredHIRCompileError`
or `ValueError`. The coordinator surfaces construction failures as a production
lowering diagnostic.

## Trusted boundary

Canonical program identity and HIR spans/IDs are the semantic handoff to RIR.
HIR carries ownership and effect facts for later checking but is not itself a
complete borrow proof or physical-layout specification.

## Experimental boundary

Some alpha paths still use copied CPython-compatible AST nodes while building
HIR. HIR schema v2 is current and consumed by RIR, but a fully shared bound-node
frontend remains RFC 0001 work. HIR intentionally does not model low-level CFG
or drop flags.

## Verification commands

```console
merlo check PROJECT
merlo expand PROJECT
merlo inspect main PROJECT --json
```
