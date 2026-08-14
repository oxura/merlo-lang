# Code generation contract

## Purpose

The C11 backend turns the checked IR chain into source suitable for the alpha's
native toolchain. It validates predecessor identity and emits an optional
native executable without making portability or safety claims beyond the
recorded artifact.

## Inputs

`emit_general_c(hir, representation, mir)` in
[`src/merlo/representation_c_backend.py`](../../src/merlo/representation_c_backend.py)
consumes exact HIR, RIR, and MIR predecessors. `compile_project()` records the
resulting C11 source as the terminal `merlo.c11.runtime-abi` artifact. With
`emit_native=True`, [`src/merlo/native_c_backend.py`](../../src/merlo/native_c_backend.py)
invokes the selected Clang/GCC/`cc` toolchain.

## Outputs

`GeneratedC` contains C11 source, its SHA-256, primitive and FFI manifests,
generated/host line counts, and domain-opaque call metadata. The C11 stage has a
contract, schema/version, digest, and optimized-MIR parent digest. Optional
`NativeBuildResult` is separate metadata containing compiler and binary
information.

## Invariants

The emitter requires `representation.source_hir_digest == hir.digest` and
`mir.representation_ir_digest == representation.digest`. C names come from
validated descriptors rather than source spelling. FFI declarations are parsed
and validated before emission; an FFI call remains an explicit unsafe boundary.
`GeneratedC.source_sha256` matches its source. A selected compiler failure is
not silently retried with another compiler.

## Failure modes

Predecessor mismatch, invalid FFI declarations, unsupported types/operations,
missing descriptors, and backend failures raise `RepresentationCBackendError`.
A requested native build fails unless a measured native result with a binary
path is returned. Toolchain discovery does not promise cross-platform support.

## Trusted boundary

Content-addressed C11 source and its parent digest are the trusted codegen
artifact. Native output is trusted only as the result of the recorded selected
toolchain; it is outside the IR lineage and is not a proof-producing C verifier.

## Experimental boundary

The alpha targets one C11 host ABI and one native platform. The emitter currently
walks typed HIR/representation data and validates MIR provenance; the C backend
does not claim to interpret every MIR instruction as a direct instruction-by-
instruction lowering proof. Planned `backend/c11.py` and `backend/toolchain.py`
paths are not current imports.

## Verification commands

```console
merlo build PROJECT --json
merlo run PROJECT --json
```

Native output requires a C11-capable Clang or GCC on Linux x86-64.
