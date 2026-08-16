# Changelog

## Unreleased

- Starts the `0.1.0-alpha.3-dev` compatibility line with language contract
  `0.3`, frontend `7`, canonical `5`, HIR `5`, Obligation IR `1`, RIR/MIR `2`,
  runtime ABI `2`, and SemanticWorld `5`. Alpha.2 lockfiles must be regenerated;
  see the migration guide.
- Replaces owner/view pointer punning with real view descriptors and locks the
  generated C under GCC and Clang strict-aliasing optimization.
- Defines byte and text escape semantics, rejecting Unicode escapes in bytes,
  invalid Unicode scalar values, malformed escapes, and unknown escapes.
- Carries bytes literals through deterministic HIR/RIR artifacts and emits
  owned native byte storage, including file writer/reader round trips.
- Adds the lossless full-file token/CST boundary and replaces the production
  CPython AST compatibility adapter with Merlo-owned native syntax nodes shared
  by HIR and the C backend.
- Replaces `Vec`-specific transform lowering with one General collection
  protocol for `Vec`, fixed `Array`, `Slice`, borrowed vectors, bytes, and text;
  indexing, iteration, `where`, `map`, and `count` now share typed HIR and
  native C semantics.
- Fuses eligible copy-scalar collection chains into one native loop, removing
  every intermediate vector allocation while preserving ordered callback and
  bounds semantics.
- Locks recursive record and enum values through `Box`/`Vec` indirection,
  including mutually recursive native layouts and active-payload,
  initialized-element, and boxed-payload drop glue.
- Defines complete core numeric behavior across Surface, typed HIR, and C:
  checked integer division, floor division, remainder, shifts, unary negation,
  literal ranges and casts, with explicit zero, shift, and overflow traps.
- Expands the native example corpus from nine to fifteen complete projects,
  adding invoicing, access-log analysis, byte statistics, inventory, task-board,
  and recursive-tree workloads with checked fixtures.
- Measures private function boundaries structurally across all fifteen
  applications: 6 of 19 parameter and return slots remain explicit (31.58%),
  below the locked one-third gate.
- Recomputes the frozen 48-program paired-source corpus at a median
  concise-Merlo/Python lexical-token ratio of 0.5455, with a module CLI and
  explicit source-surface-only claim boundary.
- Adds a checked seven-workload Merlo/C/Rust native study with raw samples,
  frozen checksums and source hashes; the measured geometric-mean ratio to each
  workload's faster native baseline is 1.091, with shared allocation called out
  as the 1.821 outlier.
- Adds pure, typed `require` and `ensure` function contracts, preserves them in
  canonical and Structured HIR identities, and emits entry/all-return native
  checks with source-located `MerloContractViolation` diagnostics.
- Adds pure, typed record invariants, preserves them through canonical, HIR,
  representation descriptors, and SemanticWorld, and enforces them at every
  native record constructor.
- Adds contextual bare `?` holes with exact expected types and completion
  contexts in canonical, HIR, and SemanticWorld data. RIR/MIR retain explicit
  non-executable hole operations; native builds fail without a fallback value.
- Adds deterministic typed Obligation IR categories and dispositions for
  contracts, invariants, holes, and future safety proofs, with separate stable
  identity and content revisions exposed through compiler and SemanticWorld
  artifacts.
- Adds deterministic constant and branch-local integer range refinement,
  unreachable-branch facts, and checked arithmetic/cast safety obligations to
  compiler artifacts and SemanticWorld.
- Adds deterministic bounded symbolic execution for pure postconditions over
  finite primitive domains, with explicit proof, counterexample, inconclusive,
  and unsupported results.
- Adds an opt-in Z3 backend with canonical SMT-LIB queries, deterministic
  solver settings, model counterexamples, timeout/unknown handling, and no
  default-build dependency.
- Generates canonical postcondition property domains/cases and normalizes real
  bounded/SMT counterexamples with typed inputs and replay provenance.
- Measures automatically closed obligations with deduplicated engine
  provenance, conservative refutation precedence, per-category counts, and an
  exact integer closure rate.
- Replaces unversioned refactor transactions and ad hoc unsupported responses
  with canonical, digest-bound `ChangeIR`, revision/world validation, and one
  preview/apply envelope across protocol, CLI, and LSP.
- Replaces ad hoc task context maps with immutable, revision-bound semantic
  capsules containing only target-relevant code, authority, obligations,
  tests, and filtered verification evidence.
- Adds canonical ChangeIR-bound semantic impact reports covering direct and
  transitive symbols, reference/call/dependency edges, interfaces, files, and
  tests before a change is applied.
- Adds a verified cross-module `MoveSymbol` subset for private functions, with
  structural CST edits, direct-import migration, isolated project compilation,
  transactional apply, and digest-bound old/new SymbolId revision lineage.
- Adds digest-bound patch evidence and preservation reports, keeping structural
  apply proof separate from contract, effect, capability, obligation, and
  verification preservation.
- Routes ChangeIR application through durable source-snapshot transactions
  with atomic commit, exact rollback/replay, stale-state rejection, and
  content-addressed journals.
- Adds deterministic offline typed-hole synthesis through bounded enumeration,
  contract-guided symbolic search, and local package candidates. Preview is
  read-only; explicit apply repeats isolated compilation and obligation checks,
  binds evidence to exact source hashes, and rolls back on any mismatch.
- Enables structural `refactor signature` for explicit function/task
  signatures when the exact edit compiles with every existing body and caller
  in an isolated project. Incompatible migrations remain read-only unsupported
  plans, and apply revalidates the anchored CST edit against a fresh world.
- Moves the static `Text.from_bytes` and `TextBuilder.new` contracts into the
  immutable ContractGraph, including parameters, ownership, effects, static
  dispatch, and ABI lowering. Surface elaboration, HIR, MIR allocation, and the
  backend primitive manifest now consume that shared contract.
- Adds generic ContractGraph receiver matching for the pure
  `Option.is_none/is_some` and `Result.is_ok/is_err` predicates. Surface type
  checking, typed HIR metadata, and native enum-tag lowering now consume the
  same immutable contracts.
- Centralizes `Option.unwrap`, `Result.unwrap`, and `Result.unwrap_err` with
  borrow-and-clone payload ownership. Owning payloads are deep-cloned instead
  of aliasing the enum storage, fixing a reproducible native double-free;
  incorrect variants now trap before reading inactive C union members.
- Replaces the flat declaration-only CST projection with a lossless hierarchy
  of declarations, headers, blocks, statements, and conservative type and
  expression regions. Recovery is represented by explicit error nodes, while
  structural IDs survive trivia changes and unrelated sibling insertions.
- Routes top-level Surface declaration boundaries and kind dispatch through
  those CST anchors. The semantic parser now fails closed if its line cursor
  and CST disagree, including for module-body line offsets; statement and
  expression grammar migration remains incremental.
- Requires every executable statement parsed through a Surface block to
  consume the CST node at the same source line and with the same structural kind. Nested
  control-flow bodies, `else` branches, `match` cases, transition bodies, and
  module-body offsets fail closed on missing or inconsistent anchors.
- Feeds executable statement expressions from their lossless CST token regions
  into the Surface expression parser instead of lexing those fragments again.
  Delimited multiline expressions now remain one CST region and do not emit
  indentation layout while `()`, `[]`, or `{}` are open.
- Replaces delimiter depth counters with typed delimiter stacks and reports
  mismatched, unexpected, and unclosed delimiters at retained token spans.
  Statement expressions now rebase exact CST offsets directly, removing the
  repeated-text `source.find()` reconstruction path.
- Retains inline function-body expressions and return-type regions under
  declaration headers. Inline and indented expression-bodied functions now
  consume their CST expression regions and fail closed when an anchor is absent.
- Adds structural parameter nodes with retained per-parameter type regions.
  Function parameter and return types now come from validated CST offsets;
  missing or inconsistent regions fail with `CSTTypeMismatch`.
- Adds retained generic-parameter nodes under function headers. Generic names,
  interface constraints, and exact spans now come from the same CST token
  stream; the old function-signature `source.find()` path has been removed.

- Replaces production module and expression text rewrites with a typed Surface
  AST, structural module binding, and a retained Surface-to-HIR handoff.
- Rejects serialized canonical projections as compiler input while preserving
  exact module source spans through HIR and SemanticWorld indexing.
- Fixes owned `Array` projection cleanup that could double-free nested owner
  values, and makes empty text writes valid under GCC's strict diagnostics.
- Adds full production-suite Clang/GCC gates and a focused ASan/UBSan ownership
  regression, while honoring `MERLO_C_COMPILER` in native test paths.
- Derives binder names and monomorphic method signatures from the immutable
  builtin contract registry. Files now use distinct `FileReader` and
  `FileWriter` resources with mode-correct close effects.
- Reuses the validated module graph instead of reading and traversing modules
  a second time during application elaboration.
- Splits elaboration constraints, call binding, diagnostics, state, and native
  lowering into owned modules.
- Makes AlphaProtocol rename edits span-exact, including nested calls, and
  documents unsupported move and signature migrations.
- Restores the wheel boundary promised by RFC 0001: only the compiler/runtime
  package and standard library ship in the wheel; source-only assets stay in
  the source archive.
- Updates official GitHub Actions to Node 24-compatible major versions.

## 0.1.0-alpha.2 — 2026-08-14

This GitHub prerelease updates the packaged product boundary while retaining
the `alpha.1` language edition and the `0.2` language contract. No PyPI
availability is claimed.

- Packages the production compiler from `src/` and separates production code
  from archive, benchmark, and release-tool namespaces.
- Centralizes intrinsic contracts and gives declarations module-qualified
  identities.
- Corrects native and public benchmark paths and their claim boundaries.
- Adds the checked multi-lane `capacity-ledger` example and its generated lock.
- Hardens the preregistered same-model AI A/B draft with frozen inputs,
  equal-model/budget constraints, and explicit unmeasured outcomes when a
  provider is unavailable. It reports no measured AI advantage.

## 0.1.0-alpha.1 — 2026-08-14

This is the first public packaged Merlo alpha release. Private research and
development began on 2026-03-19.

> **Known release defects:** the alpha.1 public source archive omitted
> `stdlib/std/core.mlo`; its wheel predates later Python and Clang portability
> repairs. Both packages remain historical evidence and are not the recommended
> installation path for the alpha.2 prerelease.

- Adds a Python 3.11+ bootstrap package and the `merlo` console script.
- Publishes project creation, checking, native build/run, tests, formatting,
  generated documentation, semantic-world queries, and protocol refactors.
- Publishes the alpha standard-library `.mlo` sources, VS Code grammar, examples,
  public documentation, and public specifications.
- Documents the checked ownership, effects/capabilities, resource, package,
  SemanticWorld, AlphaProtocol, LSP, and C FFI surfaces.
- Records the supported platform and the intentionally absent alpha features.
- Rejects noncanonical streamed UTF-8, propagates output close failures, and
  binds Python performance evidence to its implementation source.
- Releases the project under the dual MIT OR Apache-2.0 license.


### Surface 0.2 development snapshot

- Adds the preferred inferred function, binding, effect, error, optional, and
  collection-pipeline syntax while preserving explicit canonical expansion.
- Migrates 47 shipped human-facing `.mlo` sources to the Surface 0.2 parser.
- The locked 100-case external-source challenge remains unsupported: the
  current compiler cannot represent all required string, collection, record,
  parser, exception, file/stream, callable, and Python object-protocol
  semantics. `tools/benchmarks/merlo/benchmarks/merlo_surface_0_2.json` records all 100 blocked case
  IDs; no compression or general superiority claim is made from that corpus.

## Before alpha.1

The repository contained executable compiler, native backend, CLI, language
server, SemanticWorld, protocol, standard-library, and example work. Those
historical artifacts remain available for research but are not production CLI
routes unless explicitly listed in the public tooling documentation.
