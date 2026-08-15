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

Canonical source is a deterministic diagnostic projection of the typed tree.
Its semantic hash includes types, authority, errors, and desugared operations;
formatting whitespace and source spans do not change that hash.

## Alpha boundary

There is one semantic core and no future facets in alpha. The supported target is
Linux x86-64 with C11 Clang/GCC bootstrap and synchronous I/O. Capturing
closures, `async`, registry, macros, traits, cycle collection, and self-hosting
are absent.
