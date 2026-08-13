# Safe FFI boundary

Merlo FFI is an explicit C ABI declaration surface. It is not intrinsically
safe: it makes the boundary reviewable and rejects incomplete contracts.
Foreign declarations use fixed-width ABI types, for example:

```merlo
extern "C" fn write(fd: Int32, buf: RawPointer[UInt8] {read, borrowed}, count: UInt64) -> Int64 effects [console.write]
```

A raw pointer must declare access (`read`, `write`, or `store`) and ownership
(`borrowed` or `owned`). Owned foreign pointers require a named destructor;
borrowed pointers cannot declare one. Writes require a mutable pointer. Foreign
results may carry a declared error type and effects.

`repr(C) record` computes deterministic field offsets, size, and alignment. A
non-fixed-width ABI type is rejected with `FixedWidthABIRequired`; only C ABI is
accepted in alpha. Raw pointer operations such as pointer arithmetic,
allocation, and raw reads/writes require an explicit unsafe block and propagate
their obligation.

Keep unsafe declarations behind a small checked wrapper that validates lengths,
nullability, ownership, error conversion, and the effect/capability contract.
Do not infer safety from a successful parse or from a generated C prototype.
