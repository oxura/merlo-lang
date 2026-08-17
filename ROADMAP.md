# Merlo roadmap

This roadmap describes checked repository state, not marketing promises. A
module existing under `src/merlo` does not by itself make a feature part of the
production language path. Alpha keeps one semantic core and no future facets;
preview and research components cannot reinterpret production source.

Status labels:

- **Production**: exercised by the ordinary compiler or CLI and required CI.
- **Preview**: executable, versioned, and tested, but deliberately narrow or
  not yet a compatibility commitment.
- **Research**: an experimental model or artifact, not a supported user path.
- **Absent**: no production implementation.

## Feature maturity on main

| Feature | Status | Current boundary |
| --- | --- | --- |
| Language contract `0.3` | Production | Alpha compatibility may still change before release. |
| Frontend contract `8` | Production | Lossless CST anchors declarations, statements, and statement expressions; the transitional line parser still owns remaining grammar. |
| Canonical contract `6`, HIR contract `7` | Production | Typed lowering is Merlo-owned; the digest-bound AST-shaped native-syntax artifact remains as a backend adapter. |
| C11 native backend, runtime ABI `2` | Production | Linux x86-64 through Clang or GCC. |
| Ownership, moves, borrows, recursive drop | Production | Safe subset is checked and [Memory Model v1](spec/memory-model.md) is normative for that subset. |
| Capturing closures | Production | Checked immutable scalar and owned captures only. |
| Generic functions, interfaces, collections | Production | Monomorphized core subset with checked collection fusion. |
| Verify and Obligation IR | Production | Deterministic CLI and bounded engines; Z3 is opt-in. |
| Evolve and ChangeIR | Preview | Rename, explicit ChangeSignature, and private top-level MoveSymbol use fail-closed verified subsets. |
| Offline typed-hole synthesis | Preview | Bounded deterministic candidates; default builds never invoke an LLM. |
| SemanticWorld and semantic capsules | Production | Deterministic project index and structured context APIs. |
| Staged self-host | Preview | Three-stage source-level subset convergence; production stage 0 remains the Python bootstrap and every native stage still uses C11. |
| Synchronous filesystem/network I/O | Production | Capability-checked host operations only. |
| Async, machine, and durable flow | Research | Not the production native execution path; concurrency remains [RFC 0002](rfcs/0002-concurrency-model-v1.md). |
| CPU Parallel IR and work stealing | Research | Models and isolated tests exist; normal programs do not select this path. |
| GPU, WASM, hosted registry | Research | No supported hosted ecosystem or production lowering claim. |
| M:N task runtime, LLVM backend | Absent | Planned only after the Deep Core gate. |
| macOS, Windows, Linux ARM64 | Absent | Linux x86-64 is the only supported target. |

The published prerelease remains `0.1.0-alpha.2`. Main is the
`0.1.0-alpha.3-dev` line with language `0.3`, frontend `8`, canonical `6`, HIR `7`,
RIR/MIR `2`, runtime ABI `2`, and SemanticWorld `14`.

## Milestone 1: Alpha.3 Deep Core Gate

No broad runtime or GPU subsystem becomes production before this gate closes.

Required work:

1. Make lossless CST the only source boundary for declarations, statements,
   expressions, types, patterns, generics, and contracts.
2. Remove production fragment re-lexing, `source.find()` span reconstruction,
   regex semantic boundaries, and the transitional line parser.
3. Preserve exact token spans end to end, add typed delimiter recovery, and
   support incremental reparse by changed subtree with byte-perfect round trip.
4. Complete one ContractGraph for core types and operations: `Vec`, `Array`,
   `Slice`, `Map`, `Text`, `Bytes`, `Box`, `Arc`, `Option`, `Result`, iterators,
   files, sockets, channels, tasks, and atomics. Frontend, verifier, HIR, MIR,
   backend, ABI manifest, stdlib declarations, and generated docs must consume
   it rather than repeat rules.
5. Replace type-spelling ownership decisions with typed descriptors and lock
   Memory Model v1.
6. Build large positive and negative conformance corpora plus grammar, type,
   ownership, effect, and capability fuzzers with crash reduction.
7. Record independent clean-clone installation, first-program usability, and
   compiler-frontend review evidence.
8. Build and run one real application of at least 10,000 Merlo source lines.
9. Preserve sanitizer gates, deterministic artifacts, and three-stage
   self-host convergence.

Definition of done:

- protected `main`, required CI, review and stale-approval rules enabled;
- no double parsing or duplicated builtin semantics in production;
- zero sanitizer failures and zero unresolved crashes in the frozen fuzz run;
- independent frontend review and first application recorded;
- the 10k LOC application builds and runs from a clean checkout.

## Milestone 2: Merlo 0.2 Native Scale

This milestone separates service concurrency from data parallelism.

Task IR and runtime:

- lightweight tasks on an M:N work-stealing scheduler;
- structured scopes, spawn/await, task groups, cancellation and deadlines;
- channels, select, supervision, timer wheel, blocking pool, and backpressure;
- Linux epoll or io_uring networking and asynchronous filesystem operations;
- compiler-checked task transfer, sharing, mutation, and resource scope escape;
- scheduler fairness, bounded task memory, race-safety, p99/p999 latency, and
  network scaling gates against a frozen Go baseline.

Scalar backend:

- keep C11 as portable bootstrap and differential oracle;
- add MIR to LLVM IR for optimized release builds;
- use range and obligation proofs for bounds-check and checked-arithmetic
  elimination;
- add escape analysis, stack allocation, scalar replacement, specialization,
  inlining, devirtualization, fusion, copy elision, vectorization, PGO, and LTO;
- measure kernels and real programs without excluding losing workloads.

CPU Parallel IR:

- pure regions, dependencies, reductions, determinism and data partitions;
- scalar fusion, SIMD, multicore work stealing, adaptive chunking, and NUMA;
- end-to-end comparison against C/OpenMP, Rust/Rayon, Go, and Bend/HVM where a
  reproducible public version is available.

## Milestone 3: Merlo 0.3 Heterogeneous Compute

GPU follows a working CPU path. Parallel IR lowers to a GPU Kernel IR with
PTX/CUDA first, then possible AMDGPU, SPIR-V, and Metal targets. The runtime
must account for transfer cost, residency, launch overhead, fusion, pinned
buffers, tiling, streams, kernel cache, determinism, and device capabilities.
Benchmarks report end-to-end time including transfers, not kernel-only time.

## Product proof tracks

Verify expands to at least 1,000 adversarial properties across arithmetic,
contracts, invariants, ownership, resources, effects, capabilities, states, and
concurrency. A false proof is never acceptable; unsupported reasoning remains
explicitly unresolved with a reason or a concrete counterexample.

Evolve expands from narrow structural edits to public moves, caller-updating
signature changes, module split/merge, record and enum migrations, error and
capability changes, async conversion, API versions, schemas, and representation
changes. Evaluation uses real edits and reports success, unrelated changes,
context, repair iterations, regressions, rollback, and human review time.

The frozen AI experiment uses three arms:

1. another language with text tools;
2. Merlo with text tools;
3. Merlo with SemanticWorld, ChangeIR, and Verify.

No AI productivity claim is made until the preregistered same-model run is
completed on a frozen provider/model revision.

## General-purpose and self-host gates

Language and stdlib work is driven by five substantial programs: a production
HTTP service, a concurrent broker, a parallel data engine, a Merlo package
manager, and production compiler modules written in Merlo. The standard
library grows from those applications, using mature external TLS and
cryptographic implementations through reviewed bindings rather than new crypto.

Self-hosting progressively ports the actual lexer, CST, parser, binding,
inference, ownership/effects, HIR/RIR/MIR, optimizer, build driver, and package
resolver. The end state is released compiler to stage 1, stage 1 to stage 2,
and stage 2 to stage 3 with semantic and byte convergence. Python then leaves
the production distribution; C11 may remain as a portable bootstrap until a
separately reviewed direct object backend is justified.

Cross-platform order after the core/runtime stabilizes: Linux ARM64, macOS
ARM64/x86-64, Windows x86-64, then WASM.

## Change discipline

Ordinary pull requests stay within one responsibility and approximately
800-1,200 changed code lines. Larger migrations require an RFC and a review
plan split by semantic boundary. Runtime, ownership, unsafe, FFI, and backend
changes require stronger review than ordinary tooling changes. Claims about
performance, safety, portability, or AI productivity require checked evidence.
