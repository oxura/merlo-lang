# Compiler architecture

Merlo 0.1 uses a Python bootstrap compiler and emits C11. The current package
contains both production compilation and historical research paths. It is being
split under [RFC 0001](../rfcs/0001-repository-and-frontend-stabilization.md);
the flat package is not the accepted long-term architecture.

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

The project frontend still contains transitional regex and text-rewrite logic.
Merlo is therefore converging on, rather than already possessing, one clean
semantic core. RFC 0001 replaces that path with a Merlo lexer, immutable
Surface AST, bound symbols, structural inference, and direct canonical
lowering.

## Ownership and runtime

Ordinary values are immutable. Owning values move unless an operation
explicitly borrows or clones them. Representation descriptors determine copy,
move, invalidation, and drop behavior. The C backend emits type-specific drop
glue for text, bytes, collections, boxes, enum payloads, and file resources.

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
