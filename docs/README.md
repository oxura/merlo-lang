# Merlo documentation

Merlo `0.1.0-alpha.1` is a Linux x86-64 alpha with a Python 3.11+ bootstrap
package, a C11 Clang/GCC native path, and synchronous I/O.

- [Architecture](architecture.md) and [project history](project-history.md)
- [Compiler contracts](compiler/index.md) — frontend through C11 and source maps
- [Installation](installation.md) — package setup and clean project demo
- [Tour](tour.md) — a small typed task
- [Types](types.md) — scalars, values, records, enums, and containers
- [Ownership](ownership.md) and [errors](errors.md)
- [Effects](effects.md), [capabilities](capabilities.md), and [resources](resources.md)
- [Modules](modules.md) and [projects/packages/lockfiles](projects.md)
- [FFI](ffi.md) — explicit C ABI and unsafe boundary
- [SemanticWorld](semantic-world.md), [AlphaProtocol](alpha-protocol.md), and
  [AI protocol](ai-protocol.md)
- [Public AI evaluation](ai-evaluation.md) — preregistered, currently
  `PREREGISTERED_UNRUN` Merlo/Python same-model A/B
- [CLI](tooling.md) and [LSP](lsp.md)
- [Examples](examples.md)
- [Limitations](limitations.md)
- [Research notes](../research/README.md)

The normative, intentionally smaller alpha contracts are in `spec/`.
Historical artifacts remain readable, but they are not production CLI routes.
The compiler pages distinguish current symbols from **planned (RFC 0001)**
interfaces so transitional behavior is not mistaken for a stable API.
