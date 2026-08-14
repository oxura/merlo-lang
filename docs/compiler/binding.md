# Binding contract

## Inputs and outputs

- **Current module input:** `ModuleGraph.load(entry)` in
  [`merlo/modules.py`](../../merlo/modules.py) reads the entry module and
  reachable `use` modules. Each `Module` records its qualified name, path,
  imports, source, and `ModuleSymbol` declarations.
- **Current symbol output:** `ModuleSymbol` carries `SymbolId`, `RevisionId`,
  `InterfaceRevisionId`, kind, signature, export status, and line. The graph's
  `to_json()` is the `modules` artifact recorded by `compile_project()`.
- **Current project binding:** `elaborate_concise_application()` and its
  `_load_modules()`/`_interfaces()` helpers in
  [`merlo/concise_application.py`](../../merlo/concise_application.py) assemble
  `PublicInterface` and `TaskBoundary` values for canonical lowering.

The current code has no production `BoundProgram`; that name belongs to the
accepted RFC 0001 contract and is explicitly planned.

## Invariants

`ModuleGraph.load()` resolves each declaration once per module, requires a
module header matching the expected name, rejects duplicate names, and checks
imports for missing modules and cycles. `Module.symbol(name)` requires exactly
one match. Current module/interface revisions are deterministic digests of the
module identity, declaration kind/name, and top-level signature data. The
current `_interfaces()` representation for exported records and enums does not
include their field/variant shape, so a public shape edit is not guaranteed to
change the published interface revision.

RFC 0001 (planned) makes these facts explicit in immutable `BoundProgram`,
`BoundReference`, and `BoundCall` records and requires deterministic public
signatures. Including exported member/type shape in interface revisions is an
additional stabilization requirement, not current behavior. Missing and
ambiguous names must fail before type or effect inference; aliases resolve to
one target `SymbolId`, not a second host identity.

## Failure modes

`ModuleError` reports an empty module, invalid/mismatched module header,
unknown import, import cycle, duplicate declaration, or ambiguous symbol.
`ConciseApplicationError` reports a module-binding failure when
`compile_project()` catches `ModuleError`. An invalid or stale
`.merlo-interface.json` is rejected by the concise elaboration path when the
interface lock is required, but its current snapshot cannot detect every
exported record/enum shape change.

## Identity and provenance

`modules.py::_digest()` derives stable IDs from module identity, declaration
kind/name, signatures, and revision payloads. `ModuleSymbol` exposes the IDs
rather than an incidental Python object identity. Current canonical inference
records `SourceOrigin(canonical_line, path, source_line)` separately; it does
not yet attach every call to a bound symbol. RFC 0001 requires every bound call
and reference to carry its owner, target/callee `SymbolId`, and exact
`SourceSpan`.

## Current-alpha limitations

- Declaration discovery in `modules.py` uses regular expressions and the
  concise route has additional text-oriented discovery; it is not the
  RFC 0001 lexer/parser/binder pipeline.
- Public interface locking is real, but current exported record/enum shape is
  not included in the concise `PublicInterface` payload or top-level module
  interface digest.
- The accepted RFC 0001 clean cutover explicitly removes
  `concise_application.py` after all callers migrate; no compatibility import
  is promised.
