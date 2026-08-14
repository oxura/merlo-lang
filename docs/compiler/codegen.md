# Code generation contract

## Inputs and outputs

`emit_general_c(hir, representation, mir)` in
[`src/merlo/representation_c_backend.py`](../../src/merlo/representation_c_backend.py)
consumes the exact HIR, RIR, and MIR predecessors and returns `GeneratedC`.
The result contains C11 `source`, its SHA-256, primitive and FFI manifests,
generated/host line counts, and domain-opaque call metadata. `compile_project()`
records the source as the terminal `c11` `StageArtifact` with contract
`merlo.c11.runtime-abi` and the configured runtime ABI version.

The current emitter validates HIR/RIR and RIR/MIR digests, then generates by
walking the copied HIR/CPython AST plus representation descriptors. It stores
MIR but does not currently read MIR instructions or `representation.drop_plans`
to drive C emission. This is a predecessor check, not a claim that C is
MIR-generated.

When `emit_native=True`, `compile_project()` passes that C source to
`native_c_backend.compile_c_source()` using Clang or GCC when discovered. If
neither is discovered, `compile_c_source()` can fall back to `cc`; after a
specific compiler is selected, a failed compilation is not retried with
another compiler. The resulting `NativeBuildResult` is separate from the C11
artifact chain.

## Invariants

`GeneralCEmitter.__init__` requires `representation.source_hir_digest ==
hir.digest` and `mir.representation_ir_digest == representation.digest`. Type
names are lowered through `_c_name()` and descriptors, not guessed from source
spelling. FFI declarations are parsed and validated before emission; an FFI
call is an explicit unsafe boundary. `GeneratedC.source_sha256` must match its
source. The C11 artifact's `parent_digest` is the optimized MIR digest; only
optional native output is outside that lineage.

## Failure modes

RIR/HIR or MIR/RIR predecessor mismatch, invalid FFI declarations, unsupported
types/operations, missing descriptors, and backend failures raise
`RepresentationCBackendError` before a `GeneratedC` result is returned. A
requested native build fails with `ConciseApplicationError` unless the native
result is `MEASURED` and has a binary path. Toolchain selection does not promise
retry after a named compiler fails, and no fallback compiler silently changes
the selected result.

## Identity and provenance

C11 source is content-addressed by SHA-256 and linked to optimized MIR through
its `StageArtifact.parent_digest`. `GeneratedC` itself contains source/hash,
manifest, line-count, and opaque-call metadata only; it does not carry HIR/RIR/
MIR source spans, symbol IDs, revision IDs, ownership provenance, or a token-
level `#line` map. Those facts remain available in predecessor IR artifacts,
not in generated-C diagnostics.

## Current-alpha limitations

- The backend targets C11 and one native host ABI; it is not a portable
  multi-target code generator or a proof-producing C verifier.
- The compiler selection observable today is `clang`/`gcc` discovery followed
  by `cc` fallback when no named compiler is found; a selected compiler failure
  is not retried.
- RFC 0001 names the planned backend boundaries `backend/c11.py` and
  `backend/toolchain.py`. The current public symbols remain
  `emit_general_c()` and `native_c_backend.compile_c_source()` in the flat
  `merlo` package.
