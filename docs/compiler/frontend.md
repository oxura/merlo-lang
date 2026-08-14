# Frontend contract

## Purpose

The frontend turns `.mlo` source into a structured surface program and a
canonical typed program without inventing declarations from comments or string
contents. It is the boundary shared by project compilation and tooling.

## Inputs

- `parse_surface(source, path=...)` in
  [`src/merlo/surface_parser.py`](../../src/merlo/surface_parser.py) consumes
  non-empty UTF-8 source and returns an immutable `SurfaceProgram` from
  [`src/merlo/surface_ast.py`](../../src/merlo/surface_ast.py).
- `elaborate_surface()` in
  [`src/merlo/surface_elaborator.py`](../../src/merlo/surface_elaborator.py)
  converts a `SurfaceProgram` to `SurfaceElaboration(canonical, decisions)`.
- Project assembly uses `_load_modules()` in
  [`src/merlo/module_loader.py`](../../src/merlo/module_loader.py) and the
  immutable result records in [`src/merlo/frontend_model.py`](../../src/merlo/frontend_model.py).

## Outputs

`frontend_model.py` defines the current serialized handoff:
`ConciseApplicationElaboration`, `InferenceDecision`, `TaskBoundary`,
`PublicInterface`, and `SourceOrigin`. The elaboration carries the canonical
program, canonical and machine source, source and semantic digests, interface
revisions, effects/capabilities, inference decisions, and concise origins.
`to_dict()` is deterministic JSON data; it is not a second evaluator.

## Invariants

Surface nodes are frozen records with path, line, column, and end coordinates.
Empty input fails with `EmptySource`. Unsupported syntax, duplicate
declarations, bad implicit receivers, and unsupported type forms fail with a
source-located frontend diagnostic. Canonical and concise semantic digests are
compared; an ambiguous constraint is rejected rather than guessed.

## Failure modes

Malformed or unsupported syntax raises `SurfaceSyntaxError` or
`SurfaceElaborationError`. Project assembly reports a
`ConciseApplicationError` when a module cannot be read, has the wrong header,
or cannot be elaborated. A failed frontend never emits a partial canonical
program for lowering.

## Trusted boundary

The parser, surface AST, elaborator, and canonical digest are the trusted
semantic handoff. Downstream HIR/RIR/MIR and diagnostics must consume those
records and their spans, not rediscover declarations with regular expressions.

## Experimental boundary

The alpha still contains CPython-compatible expression parsing and transitional
literal/postfix preprocessing in the surface path. `frontend_model.py` is the
stable data model for this release, but a fully bound-node-only frontend and a
Merlo-native lexer remain RFC 0001 work. No planned `BoundProgram` API is
published here.

## Verification commands

From a checked-out project, the supported smoke paths are:

```console
merlo check PROJECT
merlo expand PROJECT
merlo explain PROJECT
```

These commands exercise the production frontend; they do not promise that
transitional source coordinates are token-perfect after canonical rewriting.
