# Compiler architecture

Merlo 0.1 uses a Python bootstrap compiler and emits C11. The repository keeps the bootstrap modules flat under `merlo/` so the import graph remains explicit; directories are split by shipped artifact (`stdlib/`, `examples/`, `spec/`) rather than by decorative layers.

## Compilation path

1. `concise_lexer.py`, `concise_parser.py`, and `concise_application.py` parse and elaborate `.mlo` projects.
2. `structured_hir_v2.py` produces typed structured HIR with effects, ownership, resources, and result propagation made explicit.
3. `representation_ir.py` assigns concrete representation descriptors for values, borrows, collections, recursive types, and resources.
4. `representation_mir.py` lowers representation operations into control-flow-oriented MIR.
5. `performance_opt.py` applies deterministic optimizations whose preconditions are checked against typed operations.
6. `representation_c_backend.py` emits the supported general C11 backend; `native_c_backend.py` invokes Clang or GCC and records the toolchain and artifact hashes.

`compiler.py` coordinates project loading, module resolution, interface locks, and native output. `cli.py` exposes the production commands. `lsp.py` projects the same semantic model into editor diagnostics and navigation.

## Semantic state

`semantic_world.py` stores content-addressed facts and stable identities. `alpha_protocol.py` defines the typed inspection protocol used by CLI and tooling. Source text, canonical text, symbol inspection, and native lowering are derived views; they do not own separate type systems.

A project build records its module graph and public interfaces in `merlo.lock` and `.merlo/world.json`. Generated state is reproducible and excluded from source control.

## Ownership and runtime

Ordinary values are immutable. Owning values move unless an operation explicitly borrows or clones them. Representation descriptors determine copy, move, invalidation, and drop behavior. The C backend emits type-specific drop glue for text, bytes, collections, boxes, enum payloads, and file resources.

Host operations are divided by declared effects. Capabilities narrow filesystem roots, network destinations, environment access, time, randomness, and FFI at runtime. These checks are part of program semantics, not an OS sandbox; untrusted native binaries still require host isolation.

## Evidence boundary

The normal test suite covers parsing, semantics, ownership, lowering, runtime behavior, determinism, CLI projects, LSP behavior, packaging, and release assembly. Larger corpora, sanitizer runs, and performance reports live under `benchmarks/` as content-addressed evidence. A generated report is not treated as current when its source, fixture, toolchain, or protocol lock differs.

## Deliberate constraints

The alpha has one native target and one semantic core. It does not include an async runtime, cycle collector, self-hosted compiler, package registry, macros, traits, or capturing closures. Keeping these boundaries explicit is preferred to publishing placeholder subsystems.
