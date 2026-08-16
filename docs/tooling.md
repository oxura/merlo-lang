# CLI, formatter, documentation, and LSP

The production console script is `merlo`. Its project commands are:

- `new`, `check`, `verify`, `build`, `run`, and `test`;
- `obligations`, `holes`, `explain-hole`, and `synthesize`;
- `fmt`, `expand`, `explain`, `doc`, and `map`;
- `inspect`, `refs`, `callers`, `callees`, `deps`, `impact`, `why`, and `context`;
- `refactor rename|move|signature`, `evolve rename|apply`, and
  `add --path|--git`.

Use `merlo --help` for parser-level details. `--json` is available on the
commands that emit structured payloads. `doc` writes generated API markdown to
`docs/API.md` by default; use `-o` for another destination. `map` supports
`text`, `dot`, and `json` projections. `fmt --stdout` prints formatted source;
`fmt --check` reports drift without writing.

`merlo verify` classifies the current typed obligations as automatically
closed, runtime guarded, refuted, or unresolved. It succeeds only when none are
refuted or unresolved. `merlo obligations` exposes each obligation together
with bounded-symbolic and optional SMT evidence. `merlo holes` lists incomplete
typed expressions, while `merlo explain-hole HOLE_ID` reports the expected
type, visible bindings and callables, allowed effects, and capabilities. All
four commands have deterministic `--json` output; `verify` and `obligations`
also accept the same optional `--smt z3` settings as `check`.

`merlo synthesize TARGET PROJECT` fills one typed hole with deterministic,
offline candidates from bounded enumeration, contract-guided symbolic search,
and the local package graph. Preview is read-only and ranks only candidates
that compile in an isolated project, remove the selected hole, and do not add a
refuted obligation. Use `--hole ID` when the target contains more than one hole,
`--goal TEXT` to bind the semantic capsule to an intent, `--report-out FILE` to
persist the digest-bound report, and `--apply` for an explicit journaled write.
Application repeats verification against the fresh SemanticWorld and rolls the
exact transaction back if its evidence or resulting source hashes differ.
`merlo build` never invokes synthesis or an LLM.

`merlo refactor signature TARGET SIGNATURE PROJECT` replaces only the anchored
explicit signature span. The preview becomes `ready` only if an isolated full
project compile proves that existing bodies and callers still type-check; use
`--apply` for the journaled edit. The command deliberately does not synthesize
caller arguments. Cross-module `refactor move` remains fail-closed until the
protocol carries old/new SymbolId lineage.

`merlo evolve rename TARGET NEW_NAME PROJECT` creates a digest-bound plan with
ChangeIR, a target SemanticCapsule, and a transitive impact report. Use
`--plan-out FILE` to persist the exact plan for review, then apply that same
artifact with `merlo evolve apply FILE PROJECT`. The shorter `--apply` route is
also available. A successful result includes preservation findings, before and
after world identities, exact file hashes, a journaled transaction, and patch
evidence. Any post-commit verification failure rolls the complete transaction
back before the command returns a diagnostic status.

`merlo fmt` preserves the human Surface AST. It does not insert canonical
keywords. `merlo expand` projects the typed Canonical AST with complete
parameter, return, binding, task, effect, capability, and error facts.
`merlo explain` reports those decisions and their evidence without requiring
inspection of generated source. Neither command is a textual normalizer.

## Language server

`merlo.lsp` provides a small JSON-RPC/LSP facade over the project compiler,
SemanticWorld, and AlphaProtocol. It frames messages with byte-accurate
`Content-Length` headers, understands file URIs, tracks document versions, and
returns diagnostics, symbols, references, definitions, formatting, and semantic
refactor responses supported by the alpha implementation. It is a Python API
surface in this release; no separate `merlo lsp` production subcommand is
claimed.

## Explicit non-production suites

The production contract suite is isolated from research tooling:

```text
python -m pytest tests/
python -m pytest tools/benchmarks/merlo/tests/
python -m pytest tools/release/merlo/tests/
python -m pytest research/archive/historical_protocol/tests/
python -m pytest research/archive/alpha1/tests/
```
