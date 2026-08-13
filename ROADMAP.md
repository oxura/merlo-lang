# Merlo roadmap

## 0.1.0-alpha.1 — current

The alpha packages the Python 3.11+ bootstrap compiler and `merlo` CLI. The
checked release surface includes:

- typed source checking and canonical semantic elaboration;
- Linux x86-64 native output through a C11 Clang/GCC bootstrap;
- synchronous closed effects, scoped capabilities, and resource cleanup;
- modules, path/Git dependencies, deterministic lockfiles, and project tests;
- SemanticWorld queries, AlphaProtocol refactor previews/apply, generated docs,
  and the JSON-RPC/LSP facade;
- explicit C FFI declarations and unsafe-operation validation;
- standard-library `.mlo` sources, examples, and the VS Code grammar.

The alpha has one semantic core. There are no future facets or alternate
semantic meanings in this release.

## Explicit alpha limits

The target is Linux x86-64 only. Native compilation expects C11 Clang or GCC.
I/O is synchronous. Capturing closures, `async`, a registry, macros, and traits
are absent. There is no cycle collector and no self-hosting implementation.
These are limitations, not promises about a future release.

## Next investigations

Future work may investigate the absent features and broader hosts, but each
change must preserve one semantic core and acquire its own checked evidence.
The roadmap intentionally does not promise dates, performance thresholds, or
security properties that are not established by the release test suite.

## Historical material

Earlier benchmark and research artifacts remain readable in the repository.
They are not production routes. The production CLI is the command set listed
in [docs/tooling.md](docs/tooling.md).
