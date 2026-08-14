# Known alpha limitations

This release is intentionally narrow and should be evaluated as such:

- **Platform:** Linux x86-64 is the supported target. Native output expects a
  C11-capable Clang or GCC bootstrap compiler; other hosts/toolchains are not
  promised.
- **Execution:** host I/O and the runtime contract are synchronous. `async` is
  absent.
- **Language:** arbitrary capturing closures, a registry, macros, traits, and
  self-hosting are absent. There is no cycle collector. Surface 0.2 implicit
  callables are restricted to `where`, `map`, and `count`; ordinary lifetime
  annotations and manual memory operations are not part of the human surface.
- **FFI:** only explicit C ABI declarations with fixed-width types are accepted;
  FFI and unsafe operations remain review obligations.
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
