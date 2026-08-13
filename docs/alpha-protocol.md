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

The CLI checks the affected semantic world and returns a diagnostic when a
migration is unsupported, a target is missing, or an edit capability is not
sufficient. Applying a plan changes source files only after the plan is ready;
review the preview and generated diff before committing. The protocol does not
promise compatibility with historical `merlo historical rename|move|signature`
providers. Those commands remain readable research routes, not production
routes.
