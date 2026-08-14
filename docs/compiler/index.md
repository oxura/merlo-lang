# Compiler contracts

These pages describe the artifacts that the alpha actually emits and the
identity/provenance obligations accepted by [RFC 0001](../../rfcs/0001-repository-and-frontend-stabilization.md).
A statement marked **planned (RFC 0001)** is a target contract, not a current
API. Current coordination is in [`src/merlo/compiler.py`](../../src/merlo/compiler.py).

- [Frontend](frontend.md) — surface parsing and the transitional project entry
- [Binding](binding.md) — module graph, symbols, and interface revisions
- [Inference](inference.md) — types, mutability, errors, and decisions
- [Effects](effects.md) — effect discovery, task declarations, capabilities
- [Structured HIR](hir.md) — typed semantic tree before physical layout
- [Representation IR](representation-ir.md) — descriptors, ownership, drops
- [Performance MIR](mir.md) — CFG, explicit operations, optimization
- [Ownership](ownership.md) — copy/move/borrow/drop contracts across stages
- [Code generation](codegen.md) — RIR/MIR to C11 and runtime ABI
- [Source maps](source-maps.md) — spans, origins, and diagnostic projection

The coordinator records `StageArtifact` entries for modules, concise source,
canonical source, HIR, RIR, MIR, optimized MIR, and C11. Each artifact has a
contract, version, SHA-256 digest, and parent digest; the optional native binary
is reported separately by `ProjectCompilation.native`.
