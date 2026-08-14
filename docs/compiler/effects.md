# Effects and capabilities contract

## Inputs and outputs

- **Current effect vocabulary:** `_EFFECT_CALL_PATTERNS` in
  [`merlo/concise_application.py`](../../merlo/concise_application.py) maps
  console, filesystem, environment, clock, randomness, network, and process
  spellings to effect names such as `fs.read` and `network.http`.
- **Current analysis:** `_direct_effects(source)` scans a task body;
  `_resolve_task_effects(modules, tasks)` recursively visits same-named task
  calls, adds transitive effects, and returns revised `TaskBoundary` values.
- **Current public result:** `TaskBoundary.effects` and
  `TaskBoundary.capabilities` are serialized into public interfaces and task
  revision IDs. `AlphaProtocol.effects()` and `.capabilities()` expose the
  separate SemanticWorld/research protocol, not a replacement for compiler
  checking.

The accepted RFC 0001 contract (planned) defines bound `HostOperation` values
and `infer_effects(program, hosts)` over `BoundCall.callee`.

## Invariants

A current task's resolved set must equal its declared `uses` set: missing or
extra effects raise `EffectDeclarationMismatch`. Effect recursion is detected
by a visiting set and raises `EffectCycle`; successful output is sorted and
stored as immutable tuples. `fn`/`task` restrictions and capability syntax are
validated by concise elaboration. Capabilities describe authority separately
from the effect name and both participate in a public `TaskBoundary` revision.

RFC 0001 requires a fixed point over bound calls, transitive wrapper/alias
propagation, and rejection when a pure function has inferred effects. Runtime
capabilities remain semantic guards, not host isolation.

## Failure modes

Unrecognized host spellings simply contribute no current effect, which is a
known limitation rather than proof of purity. A declared effect mismatch,
recursive task call, malformed task declaration, or invalid capability/effect
syntax is a `ConciseApplicationError` with source path and line where
available. The current regex scan can also misclassify text-shaped calls; the
compiler must not treat that as a security boundary.

## Identity and provenance

Current effects are attached to `TaskBoundary.name` and source location, then
included in `revision_id` and `PublicInterface.revision_id`. They are not yet
keyed to a resolved `SymbolId`; same-named calls can therefore affect analysis
when text matches. RFC 0001's `HostOperation.symbol`, effect IDs, capability IDs,
and bound call spans are the required provenance model.

## Current-alpha limitations

- Effect discovery is text/regex-based and only follows task names found by the
  concise loader; comments, strings, aliases, and same-named methods are not
  proven harmless by this implementation.
- The current alpha has no standalone effect/capability checker and no runtime
  host-isolation guarantee. `AlphaProtocol` is a SemanticWorld API, not proof
  that native operations are sandboxed.
- The accepted RFC 0001 bound-call fixed point and clean frontend cutover are
  planned; callers must not rely on aspirational `infer_effects` APIs.
