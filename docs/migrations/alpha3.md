# Migrating from alpha.2 to alpha.3

Alpha.3 intentionally changes the filesystem resource contract. The compiler
reports language `0.3`, frontend `4`, and runtime ABI `2`; alpha.2 lockfiles must
be regenerated with the alpha.3 compiler.

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
