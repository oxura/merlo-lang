# Ownership contract

Owned values may move into a consuming operation or return as owned values.
Borrowed views may be passed while their source remains valid. Shared borrows
exclude conflicting mutation; mutable borrows exclude other conflicting access.
The checker rejects use after move and mutation during a live borrow.

The alpha uses fieldwise record drop, active-variant enum drop, and boxed
indirection for recursive layouts. It does not require ordinary lifetime
annotations or expose manual memory operations in concise application source.
There is no cycle collector and no capturing-closure runtime.

Ownership is part of the semantic contract and must not be inferred from a
successful native build alone.

The complete terminology, move/alias rules, initialization states, composite
drop behavior, resource cleanup, FFI boundary, and concurrency boundary are
defined by [Memory Model v1](memory-model.md).
