# Meldra Stage 0.2 hashing model

Meldra stores three deliberately different hashes. None of them proves behavioral equivalence.

## File digest

`FileSnapshot.digest` is SHA-256 over the exact file bytes. It includes UTF-8 BOM, comments, whitespace, line endings, quoting, and every other byte. Transactions compare this digest immediately before materialization to reject a stale Semantic World.

## Entity source hash

`Entity.source_hash` is SHA-256 over the exact decoded source segment from the first decorator (or declaration keyword) through the AST end position. It includes formatting, comments inside that span, docstrings, decorator spelling, and quote style. It excludes leading comments not owned by the AST node and excludes a file-level UTF-8 BOM; those remain covered by the file digest.

The source hash answers: “Is this exact entity representation unchanged?”

## Semantic Revision Hash

`Entity.revision_hash` is the conservative Python Semantic Revision Hash. Its payload contains:

- frontend language (`python`);
- entity kind;
- module and qualified semantic address;
- `ast.dump(..., include_attributes=False)` for the complete declaration.

It includes function/class names through their semantic address, signatures, annotations, defaults, decorators, docstrings, statements, constants, and expressions. It excludes source locations, ordinary comments, whitespace, line endings, and quote style because CPython does not retain those in its AST.

Consequences:

- formatting-only or ordinary-comment edits change source/file hashes but not the semantic revision;
- rename and move operations create a new semantic revision;
- docstring, decorator, annotation, default, signature, or body edits create a new semantic revision.

This hash models the parsed Python AST, not every tool-visible behavior. Comments can affect external tooling, decorators can execute arbitrary code, and dynamic Python behavior can escape static analysis. Meldra therefore calls equality “same parsed semantic revision,” never “behaviorally equivalent.”

## Identity content fingerprint

`identity_features.content_hash` is name- and location-neutral. It is used only as one signal by `IdentityResolver` when source changed outside Meldra. A unique match is classified `Probable`, not `Exact`. Both `Probable` and `Ambiguous` candidates receive fresh Entity IDs; neither inherits the predecessor ID until explicit confirmation.

Only explicit ChangeIR provenance or an unchanged semantic address plus unchanged revision is `Exact`.
