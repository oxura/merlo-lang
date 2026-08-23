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

The alpha targets one C11 host ABI and one native platform. The emitter
consumes typed RIR/MIR descriptors, operations, and provenance; HIR is used
only for predecessor identity and typed metadata validation. It does not
claim to interpret every MIR instruction as a direct instruction-by-
instruction lowering proof. Planned `backend/c11.py` and
`backend/toolchain.py` paths are not current imports.

## Native core measurement

Run the checked three-arm study with:

```console
python3 -m tools.benchmarks.merlo.native_core_benchmark \
  --output tools/benchmarks/merlo/benchmarks/merlo_native_core.json
```

The study compiles seven frozen core workloads as generated Merlo C, independent
C, and independent Rust. It verifies each frozen checksum, pins each invocation
to one CPU, performs three warmups and fifteen randomized sequential
measurements per arm, and retains every timing sample and source hash.

On the checked workstation, the geometric-mean Merlo/best-native ratio was
1.091. Six workloads were within 3% of their faster C/Rust arm; the
shared-allocation workload was the outlier at 1.821. All relative MAD values
were below 8%. These are observations of this frozen corpus and host, not a
general C/Rust performance or language-superiority claim. Rust is compiled from
the recorded `rust:1.88-slim` image; unavailable toolchains are errors rather
than silently omitted arms.

## Verification commands

```console
merlo build PROJECT --json
merlo run PROJECT --json
```

Native output requires a C11-capable Clang or GCC on Linux x86-64.
