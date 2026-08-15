# Merlo alpha language specification

This specification covers the public `0.1.0-alpha.2` toolchain and the
experimental Surface 0.2 frontend. It is not a promise for unimplemented
features.

## Modules and declarations

A module begins with `module qualified.name`. `use qualified.name` imports must
precede declarations. Human Surface declarations are capitalized record forms,
explicit `enum`, inferred `name(args) = expression` / `name(args):` functions,
and explicit canonical `fn` or `task` declarations. `export` marks public
declarations. Duplicate names and unresolved imports or symbols are errors.

## Types and control flow

The scalar set is `Unit`, `Bool`, `Byte`, `UInt64`, `Int64`, `Float32`, and
`Float64`. The alpha also defines `Text`, `Bytes`, `Path`, views, records, enums,
`Option`, `Result`, arrays, slices, vectors, maps, and boxes. `T?` abbreviates
`Option[T]`. `Option[T] or T` is strict fallback; only `Bool or Bool` is boolean
OR and no other truthiness exists. `match` over sum values is exhaustive.

The Surface elaborator is the only inference engine. It infers private
parameters, returns, binding types and mutability, effects, capabilities, and
closed error rows. Tail expressions become explicit canonical returns.
Unresolved or conflicting constraints are errors. Implicit `.field` callables
exist only in typed `where`, `map`, and `count` argument positions.

`require Bool` and `ensure Bool` clauses form a contiguous prefix of a statement
function. They are pure. `require` may use parameters; `ensure` may additionally
use the reserved `result` value. The runtime checks requirements on entry and
ensures on every return. A false clause terminates native execution with
`MerloContractViolation:<kind>:<function>:<line>`. Contracts remain typed
canonical and HIR data rather than comments.

Record fields may be followed by one or more `invariant Bool` clauses.
Invariants may read the record fields and call pure functions. They are checked
by every record constructor; a false clause terminates native execution with
`MerloContractViolation:invariant:<record>:<line>`. Records have no mutable field
update syntax, so successful construction establishes the invariant for the
value's lifetime. Typed machines and transition invariants remain outside this
alpha contract.

A bare `?` is a contextual typed hole. Its surrounding return, annotation,
argument, or constructor field must determine one exact expected type;
otherwise elaboration reports `UnconstrainedTypedHole`. Canonical and HIR data
retain the hole identity, source, visible typed bindings, callables, effects,
and capabilities. `check` and SemanticWorld may inspect incomplete programs,
but native `build` rejects them with `TypedHoleNotExecutable`; no default or
mock value is generated. Postfix `value?` remains typed `Result` propagation.

The compiler derives `merlo.typed-obligation-ir.v1` from typed HIR. Categories
cover function preconditions/postconditions, data invariants, typed holes,
type/effect/capability/ownership/control-flow/arithmetic safety, and
termination. Each obligation has a stable identity, content revision, owner,
source, typed context, dependencies, and one typed disposition: unresolved,
statically proven/refuted, runtime guarded, or explicitly deferred.

The compiler also emits deterministic
`merlo.constant-range-analysis.v1` facts. Preconditions and comparisons refine
integer intervals within their branch. Checked arithmetic and narrowing casts
create arithmetic- or type-safety obligations: wholly safe ranges are proven,
wholly impossible ranges are refuted, and partial/unknown ranges remain
unresolved. Wrapping intrinsics retain modular semantics and are not reported
as checked-overflow proofs.

`merlo.bounded-symbolic.v1` evaluates pure, finite HIR function domains for
postconditions under fixed limits. It distinguishes proven, refuted,
inconclusive, and unsupported results. Refutations include concrete typed
inputs and the returned value; a sampled or truncated domain is never promoted
to a proof.

SMT solving is opt-in through `merlo check --smt z3`; it is never part of a
default build. Supported pure integer/Boolean paths are translated to canonical
SMT-LIB and checked by negating each postcondition. `unsat` proves that
obligation; `sat` records model inputs; unavailable, unsupported, timeout, and
`unknown` results never become proofs.

Canonical source is a deterministic diagnostic projection of the typed tree.
Its semantic hash includes types, authority, errors, and desugared operations;
formatting whitespace and source spans do not change that hash.

## Alpha boundary

There is one semantic core and no future facets in alpha. The supported target is
Linux x86-64 with C11 Clang/GCC bootstrap and synchronous I/O. Capturing
closures, `async`, registry, macros, traits, cycle collection, and self-hosting
are absent.
