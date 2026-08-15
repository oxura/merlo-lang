# AlphaProtocol and semantic refactors

`AlphaProtocol` is the structured query/refactor facade over `SemanticWorld`.
Production operations include inspection, references, callers/callees,
dependencies, impact, compile context, and diagnostic explanations.

Refactors are explicit and can be previewed before applying:

```console
merlo refactor rename app.main.helper assist PROJECT
merlo refactor rename app.main.helper assist PROJECT --apply
merlo refactor move app.main.helper app.support PROJECT
merlo refactor signature app.main.helper "(value: UInt64) -> Text" PROJECT
```

In alpha.2, exact rename plans are the only refactors that can become ready and
be applied. Every preview is a canonical `merlo.change-ir.v1` envelope with a
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
