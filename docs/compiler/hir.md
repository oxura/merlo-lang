# Structured HIR contract

## Inputs and outputs

`compile_canonical_hir(program, entry_function="main")` in
[`merlo/structured_hir_v2.py`](../../merlo/structured_hir_v2.py) consumes a
`CanonicalProgram` and returns `StructuredHIRProgram`. When the canonical
program has no native module it parses `program.to_source()` through the
structured builder; otherwise it copies and validates the native module before
constructing the same typed tree. `compile_project()` is the production caller.

`StructuredHIRProgram` contains source text/digest, `HIRTypeDecl` and
`HIRFunction` tuples, an entry function, schema version `2`, and contract
`merlo.structured-typed-hir.v2`. Functions contain typed `HIRParameter` values
and tree-shaped `HIRNode` bodies.

## Invariants

`HIRNode` records an ID, kind, `SourceSpan`, scope, optional type, ownership,
effects, optional `symbol_id`, revision ID, attributes, and children. HIR is a
tree: CFG blocks, gotos, allocation, raw pointers, and drop flags are rejected
in `StructuredHIRProgram.__post_init__`. Type and function names, node IDs, and
the entry function must be unique/present. The JSON representation includes
source and semantic identity fields for every function and node.

## Failure modes

Empty or malformed canonical source, unsupported expressions, invalid map
specializations, duplicate declarations, missing `main`, duplicate node IDs,
forbidden low-level kinds, and schema drift raise `StructuredHIRCompileError`
or `ValueError`. `compile_project()` surfaces construction failures as
`ConciseApplicationError("production lowering failed: ...")`.

## Identity and provenance

`HIRTypeDecl`, `HIRField`, `HIRVariant`, `HIRParameter`, `HIRFunction`, and
`HIRNode` preserve `symbol_id`, `revision_id`, and source spans where the
canonical input provides them. `StructuredHIRProgram.digest` hashes its stable
JSON. `HIRNode.walk()` is the traversal consumed by compiler source-map
projection. HIR deliberately carries semantics and source provenance but not
physical layout decisions.

## Current-alpha limitations

- The production path still preprocesses canonical source and may use copied
  CPython AST nodes; this is not the RFC 0001 bound-node-only frontend.
- HIR schema v2 exists and is consumed by RIR, but the accepted RFC 0001
  `BoundProgram` handoff and fully shared frontend object have not landed.
- HIR is intentionally not a complete ownership proof or control-flow IR;
  those obligations belong to RIR/MIR and the backend.
