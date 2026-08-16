# AlphaProtocol and semantic refactors

`AlphaProtocol` is the structured query/refactor facade over `SemanticWorld`.
Production operations include inspection, references, callers/callees,
dependencies, impact, compile context, and diagnostic explanations.

`context.compile` returns a canonical `merlo.semantic-capsule.v1` bound to the
world and target revision. It contains only the target's source, dependencies,
authority, contracts, holes, obligations, public tests, and filtered
verification/property evidence; unrelated global evidence is excluded.
`impact.change` accepts a canonical ChangeIR and returns a
`merlo.semantic-impact.v1` report. The report partitions directly edited and
transitively affected symbols and records callers, references, dependencies,
files, interfaces, and relevant tests without changing source.

Applied ChangeIR plans are materialized through a journaled
`merlo.change-transaction.v1` transaction. The apply receipt exposes its
transaction ID and digest; the persisted manifest supports exact rollback and
replay only while every source is in a complete before or after state.

Post-change `merlo.patch-evidence.v1` bundles bind the transaction receipt,
before/after worlds and capsules, exact file hashes, target lineage, and
carried verification provenance. `merlo.preservation-report.v1` separately
checks behavioral contracts, effects, capabilities, obligations, and proof
evidence; an authorized rename does not authorize any behavioral delta.


Refactors are explicit and can be previewed before applying:

```console
merlo refactor rename app.main.helper assist PROJECT
merlo refactor rename app.main.helper assist PROJECT --apply
merlo refactor move app.main.helper app.support PROJECT
merlo refactor signature app.main.helper "(value: UInt64) -> Text" PROJECT
```

For the verified path, serialize and review the whole evolution plan before
applying it:

```console
merlo evolve rename app.main.helper assist PROJECT \
  --goal "rename without changing behavior" \
  --plan-out .merlo/helper-rename.json
merlo evolve apply .merlo/helper-rename.json PROJECT
```

Typed-hole synthesis uses the same world, capsule, impact, ChangeIR, and
transaction identities:

```console
merlo synthesize app.main.parse_port PROJECT \
  --goal "return a valid Port or ParseError" \
  --report-out .merlo/parse-port-synthesis.json
merlo synthesize app.main.parse_port PROJECT \
  --goal "return a valid Port or ParseError" --apply
```

Preview generation is read-only. Bounded enumeration, symbolic contract
projection, and local package candidates are compiled in isolated project
copies; a candidate is rejected if it leaves the selected hole unresolved or
introduces a refuted obligation. Apply verifies the chosen candidate again
against the fresh world and compares stable evidence plus exact source hashes
before committing.

The apply command reconstructs the plan against a fresh SemanticWorld, rejects
tampered or stale artifacts before writing, performs the exact structural
edits, rebuilds the world, checks preservation, emits patch evidence, and saves
the new world. If any post-commit step fails, the journal restores every edited
file and the previous world.

In the current alpha.3 development contract, exact rename plans are the only
refactors that can become ready and be applied. Every preview is a canonical
`merlo.change-ir.v1` envelope with a
schema version, deterministic digest, world/target revisions, immutable
metadata, and source-anchored edits. `move` and `signature` are reserved
protocol operations that return the same envelope with `status: unsupported`;
they do not edit source and cannot be applied.

The CLI checks the affected semantic world and returns a diagnostic when a
migration is unsupported, a target is missing, or an edit capability is not
sufficient. Applying a plan changes source files only after the plan is ready;
review the preview and generated diff before committing. Historical protocol
commands are archived under `research/archive/historical_protocol/` and are not
part of the production CLI.
