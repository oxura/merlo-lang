# Merlo Memory Model v1

Status: normative for the implemented `0.1.0-alpha.3-dev` safe subset. Sections
marked **future** are requirements for later concurrency work, not current
language behavior.

## Safety boundary

Safe Merlo source has no operation for arbitrary pointer arithmetic, manual
free, unchecked union access, or fabricating a borrow. The compiler, generated
drop glue, runtime, and declared host contracts jointly enforce this model.
`extern` and future `unsafe` blocks are outside the safe boundary and remain
review obligations.

The v1 safety goal is:

> An accepted safe program cannot cause use-after-free or double-free through
> Merlo operations. Once concurrent execution becomes production, the same
> boundary must also prevent data races.

This is a semantic contract backed by checking, sanitizers, and conformance
tests. It is not a claim of a mechanized proof of the compiler implementation.

## Value properties

Every typed value has semantic properties independent of how its type name is
spelled:

- **copy**: duplication creates an independent value and needs no drop;
- **owned**: exactly one live logical owner is responsible for destruction;
- **borrowed**: a non-owning view whose validity is bounded by its source;
- **resource**: an owned host value with a typed close operation;
- **shared**: reference-counted immutable ownership where the declared type
  supports it.

Primitive integers, floats, booleans, and `Unit` are copy values. `Text`,
`Bytes`, `Vec`, `Box`, owning records/enums, closure environments with owned
captures, and file handles are owning values. `TextView`, `BytesView`, slices,
and declared borrowed parameters do not own their backing storage.

Composite properties are structural and recursive. Arrays, records, enum
payloads, options, results, `Vec`, `Box`, `Future`, and `Shared` inherit
contained-borrow and contained-resource properties from every type argument;
`Map` analyzes both its key and value. A composite needs drop when its own
storage or any live contained value needs drop. The compiler may represent a
copy value with the same machine bits as an owned value only when their
semantic operations remain distinct.

## Moves and copies

Passing or assigning an owning value to a consuming position moves it. A move
transfers the destruction obligation and immediately invalidates the source
place. Reading, moving, borrowing, mutating, closing, or dropping the invalid
source is an error.

Copy values may be duplicated implicitly. Owning values may only be duplicated
through an operation whose contract returns an independent owner, such as a
typed clone. A native structure assignment is not by itself a semantic clone.

Projection from an owning composite follows the operation contract. Borrowed
projection keeps the parent live. An owned extraction must either move the
payload while marking the parent slot inactive, or produce an independent
owner. The current `Option.unwrap`, `Result.unwrap`, and `Result.unwrap_err`
contracts use borrow-and-clone for owning payloads and inspect a union payload
only after validating its active tag.

## Aliasing and borrows

Two places alias when operations through either may observe the same storage.
During a shared borrow, the borrowed storage may be read but not mutated,
moved, closed, or dropped. During a mutable borrow, no other overlapping read
or write borrow may be used. A mutable borrow itself does not become an owner.

A borrow cannot outlive its source, escape through an owning return, be stored
in a longer-lived value, or cross a consuming operation on the source. Views
passed to host calls are real view descriptors; an owner pointer must not be
reinterpreted as a pointer to a different C structure type.

An owning container may hold a borrow while its backing owner is live in the
same scope. The container retains the borrow provenance recursively. Returning
or asynchronously transferring that container, capturing it in an escaping
closure, storing it in a longer-lived owner, or moving/dropping the backing
owner first is rejected. Diagnostics identify the container, contained borrow
type, backing owner, and escape path.

Interprocedural returns use the digest-bound `merlo.borrow-summary.v2`
contract. A function summary records each direct or contained result origin as
`(source_parameter_index, source_path, borrow_type, result_path, kind,
ownership)`, plus a bounded diagnostic witness path for transitive diagnostics.
The witness is not part of summary relation identity; recursive cycles use a
special marker. The fixed point is a finite worklist over deterministic SCCs of
the local call graph. A call substitutes the formal
origin into the actual place even when the actual is an owning `Text` or
`Bytes`; dropping or moving that actual while the returned view is live is
invalid. An absent or opaque summary is not evidence of safety, and a borrow
from a materialized temporary is rejected rather than assigned an invented
lifetime.

The checker may shorten a borrow to its last semantic use. It may not use a
native optimizer's accidental behavior as evidence that an invalid alias is
safe.

## Initialization and destruction

A place is in exactly one relevant state: uninitialized, initialized and live,
moved, or destroyed. Only initialized live values may be used. Construction
must establish every field before the composite becomes live. Failure during
construction destroys only fields that were initialized.

Records drop initialized fields. Enums inspect the active tag and drop only the
active payload. Collections drop initialized elements, not unused capacity.
Recursive `Box` payloads drop recursively. A scope destroys still-live locals
on every supported exit path. Explicit consuming close invalidates a resource,
so later scope cleanup cannot close it again.

Reference-counted shared values decrement exactly once per live owner. Cycles
are not collected in v1 and programs must not rely on cycle reclamation.

## Closures

A closure environment owns each owned capture and copies each copy capture.
Destroying the final closure owner destroys its environment. Borrowed captures
may not escape their source scope. Mutable, host-resource, and arbitrary shared
captures are rejected by the current production subset.

## Resources and failures

`FileReader` and `FileWriter` are distinct resource types. Read, write, and
close operations carry their own effects and capabilities. Resource scopes
close still-live handles in reverse acquisition order on normal return, early
return, and result propagation. Explicit close is consuming. A close failure is
reported through the declared resource error contract.

Production native failures and contract traps terminate execution. Merlo v1
does not specify stack unwinding through arbitrary C frames. Drop correctness
must therefore be established for language control-flow exits; process abort
is not observable cleanup.

## FFI and representation

An FFI declaration must state parameter/result types and ownership direction.
The caller and callee must agree on exactly one owner for transferred storage.
Borrowed pointers are valid only for the declared call extent unless a longer
lifetime is explicitly represented by a safe wrapper. C code may not retain a
temporary view or call Merlo drop glue twice.

Layout, alignment, tag representation, and ABI lowering belong to typed
representation descriptors. Frontend spelling does not authorize a backend to
guess ownership. Strict-aliasing optimization is part of the native test gate.

## Concurrency boundary

Production alpha execution is synchronous. Experimental task and parallel
modules do not widen this memory contract. Before concurrent safe code becomes
production, every transferable type must have descriptor properties equivalent
to safe transfer and safe sharing, shared mutation must use typed synchronization,
and atomics must define memory order. Those requirements are specified as a
proposal in `rfcs/0002-concurrency-model-v1.md`.

## Required diagnostics and evidence

Invalid safe programs fail closed with stable diagnostic codes for use after
move, conflicting borrow, mutation during borrow, escaping borrow, invalid
resource use, and unsupported capture/transfer. Exact prose may evolve.

Every memory-model change requires positive and negative tests, generated-C or
IR assertions where representation matters, GCC and Clang native execution,
ASan/UBSan/LSan coverage, and unchanged deterministic artifact gates.
