# Source maps and provenance contract

## Inputs and outputs

Current source mapping is assembled by `ConciseApplicationElaboration.origins`
and [`ProjectCompilation.diagnostic_source_map`](../../merlo/compiler.py).
`compile_project()` builds the elaboration, HIR, RIR, MIR, optimized MIR, and
C11 artifacts; each IR node carries its own `SourceSpan` (HIR/RIR/MIR) and
semantic IDs. `diagnostic_source_map` walks every HIR function/node and returns
records with `node_id`, canonical coordinates, and concise coordinates.

`SourceOrigin` in [`merlo/concise_application.py`](../../merlo/concise_application.py)
records `(canonical_line, path, source_line)`. If no concise origin matches a
HIR source line, the map falls back to the canonical span; otherwise it
projects the canonical end-line delta onto the concise source line.

## Invariants

A map entry is emitted for every traversed HIR node; it does not depend on the
node being executable. Paths, one-based line/column spans, node IDs, and
canonical source coordinates are preserved. Artifact provenance is a separate
parent-digest chain: `StageArtifact.parent_digest` links module, concise,
canonical, HIR, RIR, MIR, optimized MIR, and C11 content, while
`ProjectCompilation.generated_c_sha256` hashes generated C directly.

The accepted RFC 0001 contract (planned) requires canonicalization to return a
*total* canonical-node-to-`SourceSpan` map and forbids reparsing generated
canonical text. Diagnostics, SemanticWorld, LSP, formatter expansion, and
native lowering should consume that shared map.

## Failure modes

Missing source origins do not fabricate concise locations: current projection
uses the canonical span. Invalid predecessor digests are rejected by RIR/MIR/
C emitters, while malformed source and lowering failures are reported at the
coordinator as `ConciseApplicationError`. A source map is metadata and cannot
make an otherwise failed compile succeed.

## Identity and provenance

`HIRNode.id`, `symbol_id`, and `revision_id` identify the semantic node; source
spans identify its textual provenance. `StageArtifact.digest` is content
identity and `parent_digest` is lineage, not a replacement for source identity.
`ProjectCompilation.digest` is the C11 artifact digest. Optional native output
has independent compiler/binary metadata and is intentionally outside the IR
lineage.

## Current-alpha limitations

- Current projection is HIR-node based and line-oriented; it does not yet
  provide a canonical map for every intermediate operation or generated C
  token.
- `SourceOrigin` is produced by the concise text elaborator, so its accuracy is
  bounded by transitional source rewriting and canonical line alignment.
- The RFC 0001 standalone `frontend/source_map.py` and shared bound-node map
  are planned, not current imports. Do not document them as shipped APIs.
