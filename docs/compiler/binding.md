# Binding contract

## Purpose

Binding resolves a project module graph, imports, declarations, and public
interfaces before type or effect analysis. It gives tooling and compilation the
same module identity and revision inputs.

## Inputs

- `ModuleGraph.load(entry)` in
  [`src/merlo/modules.py`](../../src/merlo/modules.py) reads the entry module
  and reachable `use` modules.
- Project assembly also uses `_load_modules()` and `_read_module()` in
  [`src/merlo/module_loader.py`](../../src/merlo/module_loader.py), which
  validate module headers, paths, and imports.
- The frontend model in
  [`src/merlo/frontend_model.py`](../../src/merlo/frontend_model.py) carries
  `PublicInterface` and `TaskBoundary` records to the canonical lowering path.

## Outputs

A module has a qualified name, path, imports, source, and declarations.
`ModuleSymbol` carries `SymbolId`, `RevisionId`, `InterfaceRevisionId`, kind,
signature, export status, and source line. Public interfaces and task
boundaries serialize deterministic signature, effect, capability, and revision
fields. The world/lock artifacts record the resulting module graph.

## Invariants

A module header must match its qualified path. Duplicate declarations, missing
imports, import cycles, and ambiguous symbol lookups fail before inference.
Digest inputs include module identity, declaration kind/name, and signature
payload. An alias resolves to one target identity; it is not a second host
symbol.

The current concise interface payload does not include every exported record or
enum member shape. A shape edit is therefore not promised to change the current
interface revision unless the published signature payload changes.

## Failure modes

`ModuleError` reports an empty or malformed module, mismatched header, unknown
import, cycle, duplicate declaration, or ambiguous symbol. Project assembly
surfaces the failure as a frontend diagnostic. Invalid or stale interface and
lock files are rejected rather than silently mixed with another revision.

## Trusted boundary

Module loading, symbol digesting, and interface-lock checks are the binding
boundary. HIR and SemanticWorld may consume their identities, but neither may
re-resolve a name by text matching after binding.

## Experimental boundary

The alpha still retains regular-expression declaration discovery in the flat
module loader, and exported member-shape hashing is incomplete. RFC 0001's
immutable `BoundProgram`, `BoundReference`, and `BoundCall` records are not
published alpha APIs; callers should use the documented production commands
and current flat-module symbols instead.

## Verification commands

```console
merlo check PROJECT
merlo map PROJECT --projection json
merlo inspect TARGET PROJECT --json
```

The JSON forms expose current graph/identity data; exact diagnostic prose is
not a compatibility contract.
