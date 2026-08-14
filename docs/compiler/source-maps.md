# Source maps and provenance contract

## Purpose

Source mapping projects checked semantic nodes back to concise source for
human diagnostics and tooling. It is separate from artifact digest lineage:
a map cannot make a failed compile succeed.

## Inputs

`SourceOrigin` in [`src/merlo/frontend_model.py`](../../src/merlo/frontend_model.py)
records `(canonical_line, path, source_line)`. `ProjectCompilation` in
[`src/merlo/compiler.py`](../../src/merlo/compiler.py) exposes
`diagnostic_source_map`; current projection walks HIR nodes and their spans.
HIR nodes, RIR operations, and MIR instructions carry source spans where their
dataclasses define them; MIR blocks/terminators and generated C tokens do not
carry a complete map.

## Outputs

`diagnostic_source_map` returns entries with node ID, canonical coordinates, and
concise coordinates. If no concise origin matches a HIR source line, projection
falls back to the canonical span. Artifact `StageArtifact.parent_digest`
links module, concise, canonical, HIR, RIR, MIR, optimized-MIR, and C11
artifacts; optional native output has independent metadata.

## Invariants

Map entries retain one-based path, line, column, and end coordinates for every
traversed HIR node, including non-executable nodes. Canonical semantic identity
and artifact digest identity are not substituted for source identity. Best-effort
column projection never fabricates a concise source line when no origin exists.

## Failure modes

Missing origins use canonical spans with best-effort columns. Invalid predecessor
digests are rejected by RIR/MIR/C emitters. Malformed source and lowering
failures remain coordinator diagnostics. A source map is metadata only.

## Trusted boundary

The parser/elaborator spans, `SourceOrigin` records, HIR node IDs, and stage
parent digests are the current provenance boundary. Consumers must preserve
those records rather than infer locations from generated C text.

## Experimental boundary

The alpha map is HIR-node based and line-oriented. It has no complete RIR/MIR
operation or C-token map; canonical columns after transitional rewriting are
best effort. The RFC 0001 standalone source-map module and total bound-node map
are not current imports.

## Verification commands

```console
merlo map PROJECT --projection json
merlo inspect TARGET PROJECT --json
merlo explain PROJECT
```
