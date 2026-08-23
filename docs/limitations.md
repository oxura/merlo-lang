# Known alpha limitations

This release is intentionally narrow and should be evaluated as such:

- **Platform:** Linux x86-64 is the supported target. Native output expects a
  C11-capable Clang or GCC bootstrap compiler; other hosts/toolchains are not
  promised.
- **Execution:** the supported native host I/O contract is synchronous.
  Experimental async, machine, and durable-flow runtimes are not yet the
  production native execution path.
- **Language:** closure capture is restricted to checked immutable scalar and
  owned environments. Escaping borrowed, mutable, resource, and arbitrary
  shared captures are rejected. There is no macro system or cycle collector;
  ordinary lifetime annotations and manual memory operations are not part of
  the human surface.
- **Bootstrap:** a staged self-host subset is exercised for semantic
  convergence, but stage 0 and the production compiler remain Python-based and
  every native stage still requires a C11 compiler.
- **Ecosystem:** registry, synthesis, parallel, WASM, web, machine, and flow
  modules are experimental research surfaces rather than a stable hosted
  ecosystem or a promise that every construct lowers to native code.
- **FFI:** only explicit C ABI declarations with fixed-width types are accepted;
  FFI and unsafe operations remain review obligations.

## Issue #72 cutover boundaries

The production native cutover is deliberately limited to the typed
Structured HIR -> Representation IR -> executable Performance MIR -> C11
pipeline. It does not widen the language or redesign the runtime:

- Whole-value ownership moves and the existing type-directed drop plans remain
  supported. General partial-move semantics and runtime/conditional drop-flag
  lowering are outside this cutover.
- C11 remains the native bootstrap backend. LLVM lowering and optimizer
  redesign are outside this cutover.
- Execution remains synchronous. Async, Task IR, and Parallel IR scheduling are
  outside this cutover; GPU lowering and WASM targets remain research surfaces.
- Removing the native-syntax backend authority does not add grammar. New
  surface syntax and transitional parser replacement remain outside this
  cutover.
- User-defined generic declaration/package-provenance `TypeId` v2 remains
  outside this cutover and is tracked by Issue #86. Unknown generic
  constructors continue to fail closed under the accepted v1 contract.

- **Tooling:** LSP is a Python JSON-RPC facade, not a separately installed
  daemon command. Historical commands and artifacts remain readable but are not
  production routes.
- **Distribution:** source is dual-licensed under MIT or Apache-2.0.

There are no alpha guarantees here about throughput, latency, memory use,
security isolation, cross-platform behavior, or API stability beyond the
specific checked contracts and diagnostics documented in `spec/`.

- **Capabilities:** checked capabilities constrain compiler-recognized program
  behavior; they are not an operating-system sandbox. Untrusted native binaries
  require normal host isolation.
- **Coding agents:** structured semantics are a design goal. No independent
  productivity advantage has been established.

Surface 0.2 currently covers parsing, typed elaboration, deterministic
expansion, explanation, strict option fallback, and typed collection shorthand.
The locked 100-function external corpus is a falsification gate: unsupported
cases remain failures and are not removed or replaced.
