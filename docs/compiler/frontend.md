# Frontend contract

This page describes the alpha path that exists today, then marks the accepted
RFC 0001 replacement as planned. It is not a claim that the replacement has
landed.

## Inputs and outputs

- **Current input:** `parse_surface(source, path=...)` in
  [`merlo/surface_parser.py`](../../merlo/surface_parser.py) consumes non-empty
  UTF-8 text and produces an immutable `SurfaceProgram` from
  [`merlo/surface_ast.py`](../../merlo/surface_ast.py). `SourceSpan` is attached
  to every `SurfaceNode`.
- **Current output:** `_Parser.parse()` emits declarations, statements, and
  expressions such as `SurfaceFunction`, `SurfaceRecord`, `SurfaceEnum`,
  `SurfaceCall`, `SurfaceMatch`, and `SurfaceTry`. `elaborate_surface()` in
  [`merlo/surface_elaborator.py`](../../merlo/surface_elaborator.py) then returns
  `SurfaceElaboration(canonical, decisions)`.
- **Project entry today:** `compile_project()` in
  [`merlo/compiler.py`](../../merlo/compiler.py) instead enters through
  `elaborate_concise_application()` in
  [`merlo/concise_application.py`](../../merlo/concise_application.py), which
  assembles a `CanonicalProgram` before calling `compile_canonical_hir()`.

## Invariants

`SurfaceProgram` and its nodes are frozen dataclasses; source spans retain path,
line, column, and end coordinates. Empty source fails with
`SurfaceSyntaxError("EmptySource", ...)`. Unsupported expression forms fail
with a source-located `SurfaceSyntaxError`; elaboration failures use
`SurfaceElaborationError`. The frontend must not manufacture a declaration or
reference from comments or string contents.

## Failure modes

Malformed Python-compatible expression syntax, unsupported AST node kinds, bad
implicit receivers, duplicate declarations, and unsupported type forms are
reported as `SurfaceSyntaxError` or `SurfaceElaborationError`, rather than
silently guessed. At the project boundary, `compile_project()` wraps module
binding and production-lowering `TypeError`/`ValueError` as
`ConciseApplicationError`.

## Identity and provenance

Every current surface node carries a `SourceSpan`; canonical nodes retain
spans used by `ConciseApplicationElaboration.origins` and the compiler's
`diagnostic_source_map`. Surface parsing does not assign stable symbol IDs.
The accepted RFC 0001 contract (planned) requires a Merlo lexer and parser to
emit immutable tokens and `SurfaceProgram` nodes without CPython AST or text
rewrites, while preserving exact spans into binding and canonicalization.

## Current-alpha limitations

- `surface_parser.py` rewrites literals and postfix `?` and parses expressions
  through CPython `ast`; this is transitional, not the RFC 0001 semantic core.
- `compile_project()` still uses the text-oriented concise application route;
  `concise_application.py` owns project assembly and canonical generation.
- The planned `load_project`/lexer/parser split and direct typed-bound lowering
  are not current APIs. RFC 0001 is accepted, but its replacement is work in
  progress.
