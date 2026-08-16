# Merlo roadmap

## 0.1.0-alpha.3-dev — active development

Alpha.3 is the compiler-foundation milestone. It advances the language
contract to `0.3`, the frontend contract to `4`, and the runtime ABI to `2`.
Its release gates are strict-aliasing-safe native lowering, locked literal and
resource lifetimes, a full source-preserving lexer/CST, direct typed Surface
lowering, descriptor-based ownership, and one generated builtin contract graph.

The published prerelease remains alpha.2 until every alpha.3 gate is green.

## 0.1.0-alpha.2 — current prerelease

The current prerelease packages the Python 3.11+ bootstrap compiler and `merlo`
CLI after the production `src/` compiler, archive, benchmark, and release-tool
boundaries were made explicit. The checked release surface includes:

- typed source checking and canonical semantic elaboration;
- Linux x86-64 native output through a C11 Clang/GCC bootstrap;
- synchronous closed effects, scoped capabilities, and resource cleanup;
- modules, path/Git dependencies, deterministic lockfiles, and project tests;
- SemanticWorld queries, AlphaProtocol refactor previews/apply, generated docs,
  and the JSON-RPC/LSP facade;
- explicit C FFI declarations and unsafe-operation validation;
- standard-library `.mlo` sources, examples, and the VS Code grammar;
- centralized intrinsic contracts and module-qualified identity;
- corrected native/public benchmark paths and the capacity-ledger example;
- a hardened preregistered same-model AI A/B draft; its outcome remains
  unmeasured and makes no productivity claim.

The alpha retains one semantic core. There are no future facets or alternate
semantic meanings in this release.

## Explicit alpha limits

The supported target is Linux x86-64. Native compilation expects C11 Clang or
GCC and production host I/O is synchronous. Restricted immutable and owned
captures are supported; arbitrary closure capture, language-level `async`,
macros, cycle collection, and a hosted public registry are absent. The staged
self-host subset remains an experiment and the production compiler still uses
the Python bootstrap. These are limitations, not promises about a future
release.

## Next investigations

Future work may investigate the absent features and broader hosts, but each
change must preserve one semantic core and acquire its own checked evidence.
The roadmap intentionally does not promise dates, performance thresholds, or
security properties that are not established by the release test suite.

## Historical material

Earlier benchmark and research artifacts remain readable in the repository.
They are not production routes. The production CLI is the command set listed
in [docs/tooling.md](docs/tooling.md).
