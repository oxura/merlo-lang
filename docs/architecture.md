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

Production parsing starts with a lossless full-file Merlo token stream and CST,
then builds an immutable Surface AST with bound symbols and structural
inference. The semantic declaration parser remains transitional, but every
production file first passes the same indentation, trivia, token, and recovery
boundary. The CST now has hierarchical declaration, header, block, statement,
type-region, and expression-region nodes with explicit recovery nodes and
structurally stable identities. It remains a conservative syntax hierarchy,
not yet a complete replacement for the semantic Surface parser. Top-level
Surface declaration boundaries and declaration-kind dispatch already consume
the CST anchors and fail closed on disagreement; statement parsing is the next
migration boundary. Elaboration constraints, call binding, diagnostics,
inference state, and native lowering have separate owners under
`merlo/elaboration` and `merlo/frontend`.

Typed Surface nodes lower directly into Merlo-owned native syntax nodes. HIR and
the C backend share that representation without constructing or compiling
CPython AST objects. Python parsing survives only behind the legacy
`compile_structured_hir(source)` test boundary; project compilation never uses
it.

Builtin semantics are progressively owned by one immutable ContractGraph.
Host intrinsics and the centralized static `Text.from_bytes` and
`TextBuilder.new` paths carry parameter/result types, ownership, effects, and
ABI lowering from Surface elaboration through HIR, MIR, and the backend
manifest. The graph also resolves generic receiver patterns for the pure
`Option[T].is_none/is_some` and `Result[T,E].is_ok/is_err` predicates and owns
their inline representation lowering. `unwrap/unwrap_err` use the same generic
contracts with borrow-and-clone semantics: copy payloads are read by value,
owning payloads are deep-cloned, and a tag mismatch traps before inactive union
storage is read. Generic collection operations still have type- and
ownership-directed rules outside that graph; this alpha does not claim that
every builtin is generated from one declaration yet.

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

The alpha has one supported native target. Production I/O remains synchronous,
there is no cycle collector or macro system, and the public package registry is
not a stable service. Restricted capturing closures and a staged self-host
subset exist. Machine, flow, async, parallel, synthesis, registry, and WASM
modules remain experimental until their source semantics reach the supported
native pipeline and release gates. Stabilization takes priority over widening
those claims.
