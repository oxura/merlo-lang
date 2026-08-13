# C FFI contract

Alpha FFI accepts only ABI `C`. Foreign functions use fixed-width scalar types,
`Unit`, supported results, and explicit pointer declarations. Every pointer
specifies access (`read`, `write`, or `store`) and ownership (`borrowed` or
`owned`). Owned pointers require a destructor; borrowed pointers forbid one;
write/store requires mutable pointer metadata.

`repr(C) record` computes deterministic offsets, size, and alignment. Unknown or
variable-width ABI types are rejected. Raw pointer arithmetic, allocation,
conversion, and reads/writes are unsafe operations and require an unsafe block.
Foreign effects and error types remain explicit at the declaration boundary.

A parsed declaration is not a safety proof. Callers must validate external
memory, lengths, nullability, ownership, and errors in a checked wrapper.
