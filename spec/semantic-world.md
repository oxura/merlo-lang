# SemanticWorld contract

A `SemanticWorld` is a deterministic index built from a compiled project. It
records modules, imports, symbols, signatures, interface/implementation
revisions, dependencies, diagnostics, and a digest. It may persist as
`.merlo/world.json` and must not silently combine stale lockfiles or compiler
compatibility records.

Queries include symbol inspection, references, callers, callees, dependencies,
impact, compile context, diagnostic explanations, and text/DOT/JSON maps.
Responses can be serialized as deterministic JSON. The world indexes the one
language semantic core; it is not a second interpretation or a bypass around
compiler checks.
