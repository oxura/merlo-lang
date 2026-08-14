# Source maps and provenance contract

## Inputs and outputs

Current source mapping is assembled by `ConciseApplicationElaboration.origins`
and [`ProjectCompilation.diagnostic_source_map`](../../src/merlo/compiler.py).
`compile_project()` builds the elaboration, HIR, RIR, MIR, optimized MIR, and
C11 artifacts; HIR nodes, RIR operations, and MIR instructions carry source
spans and semantic fields where their dataclasses define them. MIR blocks and
terminators do not carry those fields. `diagnostic_source_map` walks every HIR
function/node and returns records with `node_id`, canonical coordinates, and
concise coordinates; it does not emit a complete map for RIR/MIR operations or
C tokens.

`SourceOrigin` in [`src/merlo/concise_application.py`](../../src/merlo/concise_application.py)
records `(canonical_line, path, source_line)`. If no concise origin matches a
HIR source line, the map falls back to the canonical span; otherwise it
projects the canonical end-line delta onto the concise source line. The
canonical `column` and `end_column` are reused for concise coordinates and are
best-effort after text rewrites, not exact concise columns.

## Invariants

A map entry is emitted for every traversed HIR node; it does not depend on the
node being executable. Paths, one-based line/column spans, node IDs, and
canonical source coordinates are preserved for those HIR entries. Artifact
provenance is a separate parent-digest chain: `StageArtifact.parent_digest`
links module, concise, canonical, HIR, RIR, MIR, optimized MIR, and C11 content,
while `ProjectCompilation.generated_c_sha256` hashes generated C directly.

The accepted RFC 0001 contract (planned) requires canonicalization to return a
*total* canonical-node-to-`SourceSpan` map and forbids reparsing generated
canonical text. Diagnostics, SemanticWorld, LSP, formatter expansion, and
native lowering should consume that shared exact-span map.

## Failure modes

Missing source origins do not fabricate concise locations: current projection
uses the canonical span with best-effort columns. Invalid predecessor digests
are rejected by RIR/MIR/C emitters, while malformed source and lowering
failures are reported at the coordinator as `ConciseApplicationError`. A
source map is metadata and cannot make an otherwise failed compile succeed.

## Identity and provenance

`HIRNode.id`, `symbol_id`, and `revision_id` identify the semantic node where
present; source spans identify its textual provenance. RIR operations and MIR
instructions carry their own derived semantic revisions, but CFG blocks,
terminators, and generated C do not inherit a complete node map. `StageArtifact.digest`
is content identity and `parent_digest` is lineage, not a replacement for
source identity. `ProjectCompilation.digest` is the C11 artifact digest.
Optional native output has independent compiler/binary metadata and is
intentionally outside the IR lineage.

## Current-alpha limitations

- Current projection is HIR-node based and line-oriented; canonical columns are
  best-effort for concise rewrites, and no RIR/MIR/C-token source map exists.
- `SourceOrigin` is produced by the concise text elaborator, so its accuracy is
  bounded by transitional source rewriting and canonical line alignment.
- The RFC 0001 standalone `frontend/source_map.py` and shared bound-node map
  are planned, not current imports. Do not document them as shipped APIs.
