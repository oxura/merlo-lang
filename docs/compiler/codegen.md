# Code generation contract

## Inputs and outputs

`emit_general_c(hir, representation, mir)` in
[`merlo/representation_c_backend.py`](../../merlo/representation_c_backend.py)
consumes the exact HIR, RIR, and MIR predecessors and returns `GeneratedC`.
The result contains C11 `source`, its SHA-256, primitive and FFI manifests,
generated/host line counts, and domain-opaque call metadata. `compile_project()`
records the source as the `c11` `StageArtifact` with contract
`merlo.c11.runtime-abi` and the configured runtime ABI version.

When `emit_native=True`, `compile_project()` passes that C source to
`native_c_backend.compile_c_source()` using Clang or GCC and stores a separate
`NativeBuildResult`; the binary is not a parent of the C11 artifact chain.

## Invariants

`GeneralCEmitter.__init__` requires `representation.source_hir_digest ==
hir.digest` and `mir.representation_ir_digest == representation.digest`. Type
names are lowered through `_c_name()` and descriptors, not guessed from source
spelling. Generated code includes type-directed cleanup for non-trivial
owners, explicit checks required by MIR, and only the host/FFI operations
represented in the manifests. `GeneratedC.source_sha256` must match its source.

The runtime ABI is identified by `RUNTIME_ABI_VERSION` and
`RUNTIME_ABI_CONTRACT = "merlo.runtime-abi.v1"`. FFI declarations are parsed
and validated before emission; an FFI call is an explicit unsafe boundary.

## Failure modes

RIR/HIR or MIR/RIR predecessor mismatch, invalid FFI declarations, unsupported
types/operations, missing descriptors, and backend ownership failures raise
`RepresentationCBackendError` before a `GeneratedC` result is returned. A
requested native build fails with `ConciseApplicationError` unless the native
result is `MEASURED` and has a binary path. No fallback compiler silently
changes the target.

## Identity and provenance

C11 source is content-addressed by SHA-256 and linked to the optimized MIR
parent digest through `StageArtifact`. Generated operations retain source
spans, symbols, revisions, and ownership provenance while lowering to C names.
`GeneratedC.primitive_manifest`, `ffi_metadata`, and `domain_opaque_calls`
expose what the emitter did without claiming that a binary has semantic
identity equal to an IR node.

## Current-alpha limitations

- The backend targets C11 and one native host ABI; it is not a portable
  multi-target code generator or a proof-producing C verifier.
- Generated C and optional native metadata are separate from the intermediate
  artifact provenance chain; native compilation is optional.
- RFC 0001's final `backend/native_c.py` package split is accepted/planned. The
  current public symbol remains `emit_general_c()` in the flat `merlo` package.
