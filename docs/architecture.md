# Compiler architecture

Merlo `0.1.0-alpha.2` uses a Python bootstrap compiler and emits C11.
Production code lives under `src/merlo`; benchmark, release, and frozen
research code lives in explicit tool or archive namespaces. The production
wheel contains the compiler/runtime/tooling closure.

The stage-by-stage contract index is [Compiler contracts](compiler/index.md).
Those pages cite the current Python symbols and label RFC 0001 replacement APIs
as planned; they are the precise companion to this overview.

## Compilation path

The production coordinator currently records this checked artifact chain:

1. module graph and concise project elaboration;
2. canonical typed program;
3. structured HIR;
4. Representation IR;
5. performance MIR and deterministic optimization;
6. generated C11, followed optionally by a native executable.

The recorded intermediate artifacts through generated C11 carry a contract,
schema version, digest, and parent digest. The optional native executable has
separate compiler and binary metadata but is not part of that provenance
chain. `compiler.py` coordinates compilation; the CLI, LSP, and SemanticWorld
consume its results.

Production expression parsing uses a standalone Merlo token stream, immutable
Surface AST, bound symbols, and structural inference. Module declarations,
indentation, and type declarations still use the transitional file parser;
Merlo does not yet claim a unified lossless file lexer or CST. Elaboration constraints, call binding,
diagnostics, inference state, and the CPython compatibility adapter have
separate owners under `merlo/elaboration`; the token stream lives under
`merlo/frontend`. Tooling can request a lossless trivia view for expressions
while production expression parsing consumes semantic tokens only.

The current HIR and C backend still consume the isolated CPython AST adapter.
It is a compatibility boundary, not a second source parser, but direct typed
Surface-to-HIR lowering is not complete and must not be claimed yet.

## Ownership and runtime

Ordinary values are immutable. Owning values move unless an operation explicitly
borrows or clones them. Representation descriptors determine copy, move,
invalidation, and drop behavior. The C backend emits type-specific drop glue
for text, bytes, collections, boxes, enum payloads, and file resources.

Capabilities narrow checked filesystem, network, environment, time, randomness,
and FFI operations. They are program semantics, not an OS sandbox; untrusted
native binaries still require host isolation.

## Evidence boundary

The normal suite covers parsing, semantics, ownership, lowering, runtime,
determinism, CLI projects, LSP behavior, packaging, and release assembly.
Larger corpora, sanitizer runs, and performance reports are evidence only when
their source, fixture, toolchain, and protocol locks match.

## Deliberate constraints

The alpha has one native target. It does not include async, a cycle collector,
self-hosting, a registry, macros, traits, or capturing closures. Stabilization
takes priority over adding any of them.
