# Errors and diagnostics

Merlo uses typed `Result[T, E]` values for expected application failures. Define
an `enum` error type, return `Err(...)`, and use `?` only where the enclosing
return type can carry that error. `match` over error variants is exhaustive.

Compiler, world, capability, and native paths also return named diagnostics.
Examples include `UnknownSymbol`, `MissingCapability`, `CapabilityScopeEscape`,
`StaleWorld`, `LockCompatibilityMismatch`, `FixedWidthABIRequired`, and
`MerloOverflow:*`. JSON output contains a diagnostic code and message where the
command supports `--json`; text may add source locations. Branch on the code,
not on incidental prose.

A diagnostic is not a license to continue with guessed semantics. Fix the source
or manifest and rerun the relevant production command.
