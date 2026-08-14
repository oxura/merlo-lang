# Compiler contracts

These pages describe the checked alpha artifacts and their trust boundaries.
They distinguish current symbols from RFC 0001 work; a planned interface is not
a public API. The production coordinator is
[`src/merlo/compiler.py`](../../src/merlo/compiler.py), and frontend result
records are in [`src/merlo/frontend_model.py`](../../src/merlo/frontend_model.py).

- [Frontend](frontend.md) — surface parsing, elaboration, and provenance
- [Binding](binding.md) — module graph, symbols, and interface revisions
- [Inference](inference.md) — types, mutability, errors, and decisions
- [Effects](effects.md) — effect declarations and capability limits
- [Structured HIR](hir.md) — typed semantic tree before physical layout
- [Representation IR](representation-ir.md) — descriptors, ownership, drops
- [Performance MIR](mir.md) — CFG, explicit operations, optimization
- [Ownership](ownership.md) — copy/move/borrow/drop across stages
- [Code generation](codegen.md) — RIR/MIR predecessors to C11/native output
- [Source maps](source-maps.md) — spans, origins, and diagnostic projection

Compilation records stage artifacts for modules, concise/canonical source, HIR,
RIR, MIR, optimized MIR, and C11. Each artifact has a contract, schema,
SHA-256 digest, and parent digest; optional native output is reported separately
by `ProjectCompilation.native`. These links describe observed alpha behavior,
not a promise of a future package layout.
