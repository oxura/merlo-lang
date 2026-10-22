# Modules and the standard library

A source file declares `module qualified.name`; its path mirrors the module
name (`src/foo/bar.mlo` declares `foo.bar`). `use` imports precede declarations.
Top-level `fn`, `task`, `record`, `enum`, and `const` declarations can be
exported. Duplicate symbols, misplaced imports, unknown modules, and unresolved
names are diagnostics rather than implicit imports.

The package ships `.mlo` standard-library sources in two locations used by the
bootstrap:

- `std.core`, `std.option`, `std.result`, `std.text`, `std.bytes`,
  `std.collections`, `std.io`, `std.fs`, `std.cli`, `std.time`, `std.random`,
  `std.json`, `std.net`, and `std.http` under `stdlib/std/`;
  `src/merlo/stdlib/`.

The standard library is alpha source data, not a separate binary distribution.
Its host operations still require declared effects and capabilities.

`std.json.parse` decodes `\uXXXX` escapes as UTF-16 code units: a high surrogate
must be followed immediately by a low-surrogate escape, and the pair becomes
one Unicode scalar. Lone, reversed, malformed, and truncated surrogate escapes
return `JsonError.InvalidEscape`; unescaped control characters return
`JsonError.InvalidString`. `std.json.format` escapes every U+0000–U+001F control
character, quotation mark, and backslash in string values and object keys.
Other Unicode text is retained as UTF-8.
