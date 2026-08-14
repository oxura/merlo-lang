# Effects and capabilities contract

## Inputs and outputs

- **Current effect vocabulary:** `_EFFECT_CALL_PATTERNS` in
  [`merlo/concise_application.py`](../../merlo/concise_application.py) maps
  console, filesystem, environment, clock, randomness, network, and process
  spellings to effect names such as `fs.read` and `network.http`.
- **Current production analysis:** `_Inference._infer_effects()` computes direct
  effects from CPython-AST-unparsed function bodies and iterates a bounded
  same-name call closure. `_validate_declared_task_effects()` requires every
  task to declare `uses`, rejects unknown names, and rejects only
  `inferred - declared` (missing effects).
- **Current public result:** `TaskBoundary.effects` and
  `TaskBoundary.capabilities` are serialized into public interfaces and task
  revision IDs. In current concise lowering, capabilities mirror the effect
  tuple; they are not separate scoped authority objects.

The accepted RFC 0001 contract (planned) defines bound `HostOperation` values
and `infer_effects(program, hosts)` over `BoundCall.callee`, with distinct
EffectId and CapabilityId sets.

## Invariants

Current tasks must declare a non-empty `uses` list and every inferred effect
must occur in that list. Extra declared effects are currently accepted. The
current bounded closure does not reject an annotated recursive call graph with
an `EffectCycle`; it can stop after its iteration bound. Unknown effect names
are rejected, and resolved tuples are sorted before being stored in
`TaskBoundary`.

RFC 0001 (planned) requires a fixed point over bound calls, transitive
wrapper/alias propagation, a pure-function effect rejection, and a separate
capability authority check. Runtime capabilities remain semantic guards, not
host isolation.

## Failure modes

Unrecognized host spellings simply contribute no current effect, which is a
known limitation rather than proof of purity. Missing `uses` declarations,
unknown effect names, and missing inferred declarations raise
`ConciseApplicationError` with source path and line where available. Current
text/AST matching does not guarantee a typed cycle diagnostic or distinguish
same-spelled methods from resolved calls.

## Identity and provenance

Current effects are attached to `TaskBoundary.name` and source location, then
included in `revision_id` and `PublicInterface.revision_id`. They are not yet
keyed to a resolved `SymbolId`; same-named calls can therefore affect analysis
when text matches. Current capability fields have the same provenance because
they mirror effects. RFC 0001's `HostOperation.symbol`, effect IDs, capability
IDs, and bound call spans are the required future provenance model.

## Current-alpha limitations

- Effect discovery is text/CPython-AST based and only follows function names
  visible to the concise inference pass; comments, strings, aliases, and
  same-named methods are not proven harmless by this implementation.
- The current alpha has no separate capability syntax/checker: the published
  `TaskBoundary.capabilities` tuple mirrors `effects`. `AlphaProtocol` is a
  SemanticWorld API, not proof that native operations are sandboxed.
- The accepted RFC 0001 bound-call fixed point, `EffectCycle` diagnostics, and
  authority/effect separation are planned; callers must not rely on
  aspirational `infer_effects` APIs.
