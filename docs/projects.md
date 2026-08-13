# Projects, packages, and lockfiles

`merlo new` creates this layout:

```text
project/
  merlo.toml
  merlo.lock
  src/main.mlo
  tests/
```

The manifest has schema `manifest = 1` and a `[project]` table with `name`,
`version`, and `edition = "alpha.1"`. Dependencies use deterministic path
records or an explicit Git URL plus immutable revision:

```toml
[dependencies]
local = { path = "../local", version = "0.1.0" }
remote = { git = "https://example.invalid/remote.git", rev = "<full revision>" }
```

Use `merlo add --path NAME PATH PROJECT` or `merlo add --git NAME URL --rev
REV PROJECT`. A Git dependency requires `--rev`; a path dependency is resolved
relative to its owning package. Cycles, duplicate package names, source-hash
changes, and manifest/lock compatibility mismatches are errors.

`merlo.lock` is canonical JSON in the alpha. It records schema and compiler
compatibility, a manifest hash, package source hashes, and a dependency graph.
A TOML representation can be read/written by the Python API for tooling, but
JSON is the byte-stable on-disk form. Lockfiles should be reviewed with source
changes and are not a substitute for reviewing dependency code.
