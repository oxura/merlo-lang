# Migrating from alpha.2 to alpha.3

Alpha.3 intentionally changes the filesystem and verification models. The
compiler reports language `0.3`, frontend `7`, canonical `5`, HIR `5`,
Obligation IR `1`, range analysis `1`, bounded symbolic execution `1`, RIR/MIR
`2`, runtime ABI `2`, and SemanticWorld `7`; earlier lockfiles must be
regenerated with the alpha.3 compiler.

SemanticWorld now includes the canonical constant-range analysis payload.
Compiler results expose the same payload and merge its checked-arithmetic and
cast-safety obligations into Obligation IR.
Compiler and SemanticWorld payloads also include bounded postcondition results.
Consumers must preserve the distinction between exhaustive proofs,
counterexamples, incomplete bounds, and unsupported HIR.


## File handles

Read and write handles are now different nominal resource types:

```text
fs.open_read(path)?  -> FileReader
fs.open_write(path)? -> FileWriter
```

Replace the shared close operation according to the handle mode:

```text
fs.close(reader) -> fs.close_read(reader)
fs.close(writer) -> fs.close_write(writer)
```

`fs.read_chunk` accepts only `FileReader`; `fs.write_chunk` accepts only
`FileWriter`. `close_read` requires `fs.read`, while `close_write` requires
`fs.write`.

## Literal escapes

Byte literals decode `\xNN` and octal escapes into one byte. Unicode escapes
inside byte literals are errors. Text literals accept valid Unicode scalar
escapes and reject surrogates, values above `U+10FFFF`, malformed escapes, and
unknown escapes.

## Function contracts

Move preconditions and postconditions to the leading `require` and `ensure`
clauses of a statement function. Contract expressions must be pure and Boolean;
postconditions use `result` for the returned value. Canonical, HIR, interface,
and SemanticWorld revisions now include these clauses.

## Record invariants

Place pure Boolean `invariant` clauses after a record's fields. Every record
constructor checks every clause. Record invariants participate in canonical,
HIR, descriptor, and SemanticWorld revisions.

## Contextual typed holes

Bare `?` now retains a typed completion obligation when an exact type is
available from context. Use postfix `value?` only for `Result` propagation.
Incomplete programs can be checked and indexed, but native builds fail with
`TypedHoleNotExecutable`.

## Typed obligation artifact

Compiler and SemanticWorld payloads now include the deterministic
`merlo.typed-obligation-ir.v1` artifact. Consumers must distinguish obligation
identity from revision and handle every typed disposition explicitly.
