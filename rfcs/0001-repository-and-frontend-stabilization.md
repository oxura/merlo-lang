# RFC 0001: Repository and frontend stabilization

- Status: Accepted
- Accepted: 2026-08-14
- Target: `0.1.0-alpha.2`

## Summary

Merlo will stop adding language features until the existing alpha compiler is separated into a maintainable production package and a reproducible research workspace. Production code moves to `src/merlo`; research, benchmark, evidence, and release runners move to top-level `tools`; historical reports and protocols move to `research`. Only the compiler, runtime support, tooling, project services, and standard library ship in the wheel.

Project compilation will use one semantic path:

```text
source bytes
  -> Merlo lexer
  -> Surface AST
  -> module and name binding
  -> BoundProgram
  -> type, error, effect, and capability inference
  -> CanonicalProgram
  -> HIR -> Representation IR -> MIR -> C11
```

Semantic decisions must not depend on regex searches, text rewriting, CPython AST nodes, comments, strings, or formatting. Calls bind to stable `SymbolId` values; effects and capabilities are inferred over the bound call graph.

One clean `v0.1.0-alpha.2` will be published only after the package cutover, frontend cutover, CI expansion, documentation correction, release-manifest verification, and clean-wheel smoke pass. Alpha.1 remains historical but is marked superseded because its public source package was incomplete.

## Current evidence

The alpha.1 wheel contains 161 Python modules. At least 64 modules, 39.8 percent, are visibly experiments, benchmarks, evidence generators, corpora, stage tooling, or research runners.

`src/merlo/concise_application.py` is 2,775 lines. It owns or coordinates module loading, source mapping, declaration discovery, inference, task analysis, effects, capabilities, interfaces, canonical generation, sum-type rewriting, and diagnostic projection. The compiler, CLI, formatter, LSP, SemanticWorld, project test runner, and research runners depend on it.

The project path recognizes module headers, imports, tasks, calls, effects, and parts of `Option` and `Result` lowering through regex and string replacement. `surface_parser.py` and `surface_elaborator.py` already provide a useful structural path for individual sources, but expressions still pass through CPython AST and project compilation still enters through the text-oriented elaborator.

The public `main` branch reports alpha.1 after the alpha.1 tag and after a CI repair. The alpha.1 release also has a corrective source addendum. This is transparent but not a clean release boundary.

## Goals

1. Ship a small compiler package rather than the research laboratory.
2. Give every production module one responsibility and one-way dependencies.
3. Delete the text-oriented project frontend after migrating every caller.
4. Bind calls and infer effects and capabilities from resolved symbols.
5. Make compilation and tooling consume the same bound and typed objects.
6. Install alpha.2 from a wheel without editable mode or test dependencies.
7. Turn Linux, Python, Clang, GCC, sanitizer, determinism, packaging, and manifest claims into CI gates.
8. Correct README statements that exceed demonstrated behavior.
9. Record at least fifteen professional, substantive public commits without rewriting prior history.
10. Preserve research reproducibility without installing research code.

## Non-goals

This stabilization does not add web support, `async`, a registry, self-hosting, a cycle collector, new targets, new facets, a marketing site, performance superiority claims, or claims that Merlo is the best language for coding agents.

Capabilities do not become an OS sandbox. Trademark research is not legal clearance. External reviews, gradual history, and benchmark wins must not be fabricated.

## Target repository layout

```text
src/merlo/
    __init__.py
    __main__.py
    version.py
    compiler.py
    frontend/
        ast.py
        lexer.py
        parser.py
        module_loader.py
        binding.py
        inference.py
        effects.py
        interfaces.py
        source_map.py
        canonicalize.py
    semantics/
        types.py
        diagnostics.py
        world.py
        protocol.py
    ir/
        hir/
        representation/
        mir/
    backend/
        c11.py
        toolchain.py
    runtime/
        contracts.py
        resources.py
    tooling/
        cli/
        lsp/
    project/
        manifest.py
        lockfile.py
        packages.py
        testing.py
    stdlib/
        *.mlo

tools/
    research/
    tools/benchmarks/merlo/benchmarks/
    release/

research/
    protocols/
    reports/
    archive/

tools/benchmarks/merlo/benchmarks/
examples/
spec/
docs/
rfcs/
tests/
```

The implementation may use fewer focused files when adjacent behavior has one owner. Empty scaffolding is forbidden.

### Dependency direction

```text
tooling/project
    -> compiler
    -> frontend
    -> semantics
    -> IR
    -> backend/runtime
```

IR models never import CLI, LSP, project management, benchmarks, release code, or research code. `src/merlo` never imports `tools`, `benchmarks`, or `research`. Repository tools may import the installed production package.

`merlo.__init__` exports only the release version, `compile_project`, documented project types, and public diagnostics. Historical semantic-evolution and benchmark types cease to be package-root exports. Historical CLI commands become repository commands under `python -m tools...`; no compatibility aliases remain.

## Frontend contracts

### Lexer and parser

The lexer consumes UTF-8 and emits immutable tokens with exact `SourceSpan` values. Comments and whitespace may affect formatting and spans but cannot create declarations, references, effects, or capabilities.

The parser consumes tokens and emits immutable `SurfaceProgram` objects. Module declarations and imports are nodes, not regex matches. Expressions use a Merlo Pratt or precedence-climbing parser; CPython AST is not part of the semantic path.

`Option[T]`, `Result[T, E]`, optional postfix syntax, matches, tail expressions, pipelines, and postfix propagation are represented as AST nodes. They are not introduced through `str.replace` or regex substitution.

### Module loading

`load_project(entry: Path) -> LoadedProject` resolves the root, manifest, stdlib, imports, dependency order, and cycles. A loaded module contains its path, source digest, parsed `SurfaceProgram`, and module identity. Read errors and dependency cycles are source-located diagnostics.

### Binding

The binder consumes `LoadedProject` and emits `BoundProgram`:

```python
@dataclass(frozen=True, order=True)
class SymbolId:
    value: str

@dataclass(frozen=True)
class BoundReference:
    owner: SymbolId
    target: SymbolId
    span: SourceSpan

@dataclass(frozen=True)
class BoundCall:
    owner: SymbolId
    callee: SymbolId
    arguments: tuple[BoundExpression, ...]
    span: SourceSpan

@dataclass(frozen=True)
class BoundProgram:
    modules: tuple[BoundModule, ...]
    symbols: tuple[BoundSymbol, ...]
    references: tuple[BoundReference, ...]
    calls: tuple[BoundCall, ...]
```

Declaration identities reuse the existing rule: digest of module identity, declaration kind, and name. Revision IDs include semantic contract and body. An alias resolves to its target `SymbolId`; it does not create a second host identity. Missing and ambiguous names fail before inference.

### Type and error inference

Inference operates on bound nodes and records local types, return types, mutability, typed errors, and evidence spans. It never inspects raw source. Public signatures remain deterministic and participate in interface-lock validation.

### Effects and capabilities

Host operations are keyed by bound identity:

```python
@dataclass(frozen=True)
class HostOperation:
    symbol: SymbolId
    parameter_types: tuple[TypeId, ...]
    return_type: TypeId
    effects: frozenset[EffectId]
    capabilities: frozenset[CapabilityId]
```

`infer_effects(program, hosts)` computes a deterministic fixed point over `BoundCall.callee`. Direct host calls contribute registered effects and capabilities. User functions contribute their callees' closure, so aliases and wrappers propagate effects automatically.

A task's `uses` set must contain inferred capabilities. A pure function with inferred effects is rejected. Same-spelled methods, comments, strings, formatting, and unbound names cannot add or hide effects. Regressions cover aliases, imports, wrappers, comments, string literals, same-named methods, renamed imports, and private transitive calls.

Runtime capabilities remain semantic guards, not host isolation.

### Canonicalization and source maps

Canonicalization consumes typed bound nodes and returns `CanonicalProgram` plus a total canonical-node-to-`SourceSpan` map. It does not reparse generated canonical text. Compiler diagnostics, SemanticWorld, LSP, formatter expansion, and native lowering use this result.

`compile_project` is the only production coordinator. It records the parent-digest chain for module graph, Surface AST, bound program, canonical program, HIR, Representation IR, MIR, optimized MIR, C11, and optional binary.

`concise_application.py` is deleted after all callers move. No deprecated import remains.

## Production and research migration

1. Adopt `src/merlo` packaging and move the smallest stable API.
2. Move compiler stages while preserving stage contracts and digests.
3. Move CLI, LSP, project, runtime, and stdlib code.
4. Move research, benchmark, evidence, corpus, stage, and release modules outside the package.
5. Replace the project frontend with parsed modules and bound symbols.
6. Migrate every production caller and test.
7. Delete old modules, frontend routes, root experiment exports, and historical CLI commands.
8. Build the wheel and prove no research module is included.

Research reports retain content hashes and provenance. Path changes are recorded. Generated products remain ignored.

## Packaging and quickstart

Setuptools uses `package-dir = {"" = "src"}` and discovers `src/merlo*` only. Standard-library `.mlo` files live under `src/merlo/stdlib` as package data. The wheel does not install repository docs, examples, reports, research tools, protocols, corpus generators, release assemblers, or editor packages.

The alpha.2 user path is:

```console
python -m pip install https://github.com/oxura/merlo-lang/releases/download/v0.1.0-alpha.2/merlo-0.1.0a2-py3-none-any.whl
merlo new hello --name hello
merlo run hello
```

Editable `.[test]` installation stays in `CONTRIBUTING.md`. The bootstrap compiler requires Python 3.11+ but has no third-party Python runtime dependencies.

## Required CI

Every pull request and `main` push runs:

1. `lint`: `pyflakes` on production, tools, and tests.
2. Python 3.11, 3.12, 3.13, and 3.14 compiler tests.
3. Explicit Clang native example checks.
4. Explicit GCC native example checks.
5. Compact ownership/resource corpus under supported ASan, UBSan, and LSan modes.
6. Deterministic wheel and source archive builds.
7. Clean wheel install, `merlo --help`, project creation, check, build, and run.
8. Two clean builds comparing module, bound, canonical, HIR, RIR, MIR, optimized MIR, C11, and binary hashes.
9. Release-manifest verification of every required stdlib, spec, example, license, metadata, and package file.

Branch protection requires stable job names before merge. Each job invokes a repository command reproducible locally.

## Alpha.2 release

1. Set project, compiler, manifest, changelog, and displayed versions to `0.1.0-alpha.2` / `0.1.0a2`.
2. Mark alpha.1 superseded because its source omitted `stdlib/std/core.mlo` and required portability repairs.
3. Remove the alpha.1 corrective addendum. Do not rewrite the original tag or primary assets.
4. Build source and wheel from a clean checkout with fixed `SOURCE_DATE_EPOCH`.
5. Build each twice and require byte-identical hashes.
6. Generate a manifest with commit, versions, platform, toolchains, required paths, file and artifact digests, test evidence, and status.
7. Install the wheel cleanly and execute the README quickstart.
8. Tag the verified commit `v0.1.0-alpha.2` and upload only final source, wheel, manifest, checksums, and evidence.
9. Download public assets and repeat digest and smoke verification.

A failed candidate is rebuilt before publication. Published tags and primary assets are never patched or force-updated.

## README and compiler documentation

README will say Merlo is converging on one semantic core until the cutover is complete. It will state that capabilities are not an OS sandbox, coding-agent productivity is not independently validated, the bootstrap compiler needs Python, and no general performance, simplicity, or AI superiority is claimed.

README shows direct wheel installation, one-minute `new`/`run`, the concise introduction, and a real JSON CLI excerpt with observed command output.

Compiler contracts live in:

- `docs/compiler/frontend.md`
- `docs/compiler/binding.md`
- `docs/compiler/inference.md`
- `docs/compiler/effects.md`
- `docs/compiler/hir.md`
- `docs/compiler/representation-ir.md`
- `docs/compiler/mir.md`
- `docs/compiler/ownership.md`
- `docs/compiler/codegen.md`
- `docs/compiler/source-maps.md`

Each states inputs, outputs, invariants, failure modes, trusted assumptions, and experimental boundaries.

## Professional public history

The stabilization must produce at least fifteen substantive public commits. This is a professionalism requirement, not an attempt to imitate a human timeline. Each commit is independently understandable, testable, and revertible.

The planned commit boundaries are:

1. `docs: accept repository stabilization RFC`
2. `docs: correct alpha claims and user quickstart`
3. `build: adopt src package and strict wheel boundary`
4. `refactor: isolate project and module services`
5. `refactor: group HIR representation and MIR stages`
6. `refactor: separate native backend and runtime`
7. `refactor: isolate CLI and LSP tooling`
8. `refactor: move research and release tools`
9. `refactor: parse expressions without CPython AST`
10. `refactor: bind project calls to stable symbols`
11. `refactor: infer effects from bound calls`
12. `refactor: delete the text-oriented frontend`
13. `ci: expand Python native and sanitizer coverage`
14. `release: add manifest determinism and wheel gates`
15. `docs: publish compiler subsystem contracts`
16. `examples: add a substantial native showcase`
17. `research: publish reproducible benchmark command`
18. `research: publish controlled coding-agent comparison`
19. `research: document Merlo name risk screen`
20. `release: prepare clean alpha2 artifacts`

If one boundary proves inseparable, it may be combined only when another equally substantive boundary replaces it. Tiny formatting-only or split-for-count commits do not satisfy the minimum.

Ten issues cover package separation, frontend decomposition, bound effects, alpha.2 engineering, Python coverage, Clang/GCC, sanitizers, determinism, release manifests, and AI validation. Changes use public branches and pull requests. PRs state behavior, contracts, focused tests, and evidence; required CI passes before merge.

## External validation and research gates

Three gates require people who did not implement the change:

1. clean clone and documented installation;
2. first small `.mlo` program without private guidance;
3. compiler engineer review of binding and effect inference.

The project publishes exact review checklists and requests. Gates remain open until real external evidence arrives; the author or automation cannot self-certify independence.

One showcase must exercise modules, typed errors, effects, capabilities, collections, native compilation, and real I/O without hidden Python execution.

One benchmark must have one reproduction command, locked inputs, environment metadata, raw samples, and a corpus-limited claim.

One same-model AI A/B uses equal model, prompts, budgets, tasks, environment, and review rules. It reports success, tokens, iterations, irrelevant edits, and regressions. Missing, underpowered, or inconclusive evidence produces no productivity claim.

## Trademark risk screen

Before investment in a domain, logo, foundation, merchandise, or company, the project searches official and reputable trademark sources for `Merlo` in relevant software and technology classes and intended jurisdictions. The report records query dates, databases, classes, potentially conflicting marks, company/SEO collision, domain/package availability, and questions for counsel.

The report is not legal clearance and recommends professional counsel before commercial branding.

## Acceptance evidence

Alpha.2 requires:

- all public tests pass on Python 3.11 through 3.14;
- Clang and GCC smoke pass;
- supported ASan, UBSan, and LSan modes pass;
- no semantic effect decision reads raw source;
- aliases and wrappers preserve effect closure;
- comments, strings, formatting, and same-named methods cannot create or hide capabilities;
- every production caller uses the new frontend;
- `concise_application.py` and duplicate frontend paths are absent;
- the wheel contains production modules and stdlib only;
- two clean builds produce identical semantic and binary hashes;
- manifest validation rejects a missing `stdlib/std/core.mlo` mutation;
- clean wheel installation completes the README quickstart;
- README claims match evidence;
- tag, package version, manifest, changelog, and assets agree;
- at least fifteen substantive commits are visible in public history.

External review, showcase, AI A/B, reproducible benchmark, and trademark screen complete before broad promotion. An inconclusive result blocks its claim, not the compiler release.

## Migration and rollback

Each PR keeps affected tests green. File moves use symbol-aware refactoring when available; otherwise callers are enumerated before the move and verified after it. Before alpha.2, a failed migration is reverted by reverting its PR. No compatibility aliases remain after cutover. Alpha.1 stays downloadable as superseded history.

## Rejected alternatives

- **Release hygiene now, architecture in alpha.3:** rejected because alpha.2 would preserve the source of the trust problem.
- **Flat repository plus wheel allowlist:** rejected because it hides rather than fixes architecture.
- **Split the god object but keep regex semantics:** rejected because navigation improves while correctness does not.
- **Compatibility imports and historical CLI aliases:** rejected because alpha has no stability guarantee and clean cutover is cheaper.
- **Rewrite private history:** rejected because fabricated evolution is worse than an honest import followed by professional public work.

## Unresolved questions

None. The project owner selected one clean alpha.2 after the full stabilization boundary and required at least fifteen substantive public commits.
