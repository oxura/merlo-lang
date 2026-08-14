# Changelog

## 0.1.0-alpha.2 — unreleased

This release is reserved for repository and frontend stabilization under
[RFC 0001](rfcs/0001-repository-and-frontend-stabilization.md). It will separate
production and research code, remove text-based semantic inference, strengthen
CI and release manifests, and provide a clean wheel-first installation path.

No alpha.2 artifact has been published yet.

## 0.1.0-alpha.1 — 2026-08-14

This is the first public packaged Merlo alpha release. Private research and
development began on 2026-03-19.

> **Known release defects:** the alpha.1 public source archive omitted
> `stdlib/std/core.mlo`; its wheel predates later Python and Clang portability
> repairs. Both packages remain historical evidence and are not the recommended
> installation path while alpha.2 is being prepared.

- Adds a Python 3.11+ bootstrap package and the `merlo` console script.
- Publishes project creation, checking, native build/run, tests, formatting,
  generated documentation, semantic-world queries, and protocol refactors.
- Publishes the alpha standard-library `.mlo` sources, VS Code grammar, examples,
  public documentation, and public specifications.
- Documents the checked ownership, effects/capabilities, resource, package,
  SemanticWorld, AlphaProtocol, LSP, and C FFI surfaces.
- Records the supported platform and the intentionally absent alpha features.
- Rejects noncanonical streamed UTF-8, propagates output close failures, and
  binds Python performance evidence to its implementation source.
- Releases the project under the dual MIT OR Apache-2.0 license.


### Surface 0.2 development snapshot

- Adds the preferred inferred function, binding, effect, error, optional, and
  collection-pipeline syntax while preserving explicit canonical expansion.
- Migrates 47 shipped human-facing `.mlo` sources to the Surface 0.2 parser.
- The locked 100-case external-source challenge remains unsupported: the
  current compiler cannot represent all required string, collection, record,
  parser, exception, file/stream, callable, and Python object-protocol
  semantics. `benchmarks/merlo_surface_0_2.json` records all 100 blocked case
  IDs; no compression or general superiority claim is made from that corpus.

## Before alpha.1

The repository contained executable compiler, native backend, CLI, language
server, SemanticWorld, protocol, standard-library, and example work. Those
historical artifacts remain available for research but are not production CLI
routes unless explicitly listed in the public tooling documentation.
