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

`frontend_model.py` defines the serialized project result:
`ConciseApplicationElaboration`, `InferenceDecision`, `TaskBoundary`,
`PublicInterface`, and `SourceOrigin`. The in-memory `CanonicalProgram` retains
the typed `SurfaceProgram` consumed by HIR; canonical source is a deterministic
projection for inspection and hashing, not compiler input. `to_dict()` exposes
deterministic JSON data and is not a second evaluator.

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

The full-file lexer/CST owns lossless tokens, indentation, trivia, and recovery.
Its immutable hierarchy is `file -> declaration -> header/block -> statement`,
with conservative parameter, type, and expression regions under headers.
`SyntaxNode.walk()` exposes the tree, recovered regions are explicit `error`
nodes, and IDs are derived from semantic headers plus their structural parent
rather than absolute offsets. This makes an existing node identity survive
trivia edits and unrelated sibling insertions. The Surface parser still owns
the complete semantic grammar for declarations, types, statements, and
expressions; the CST is not yet the sole parser. Its top-level declaration
cursor and kind dispatch do consume CST anchors, including source line offsets,
and reject any CST/parser disagreement instead of silently reparsing a
different boundary. Statement blocks also require a same-line, same-kind CST
node for every executable statement; comment-only lines remain lossless trivia,
while nested `else` and `case` headers are checked as structural anchors.
Statement expression regions reuse their retained CST tokens, including across
physical lines inside open delimiters. The file lexer validates typed delimiter
pairs, so `([)]`, stray closers, and unclosed openers fail with distinct,
source-located diagnostics. Expression parsing rebases the retained absolute
token offsets directly and validates each token against the original file; it
does not search repeated token text to reconstruct positions or silently
re-lex the fragment. Inline function declarations retain separate parameter,
return-type, and body-expression regions. Both inline and indented
expression-bodied functions consume those anchored expression tokens instead
of invoking the expression lexer a second time. Function parameter lists own
structural parameter children and per-parameter type regions. Surface function
signatures validate and consume those nodes plus the retained return-type
region instead of locating parameter fragments in the line text.
Module binding transforms Surface nodes structurally,
and HIR lowering projects the retained Surface tree into Merlo-owned native
syntax nodes, so no canonical or module source is reparsed and no CPython AST
is created on the production frontend path.

## Verification commands

From a checked-out project, the supported smoke paths are:

```console
merlo check PROJECT
merlo verify PROJECT
merlo obligations PROJECT --json
merlo holes PROJECT --json
merlo expand PROJECT
merlo explain PROJECT
```

These commands exercise the production frontend and retain token-derived source
coordinates through module binding and HIR lowering.
