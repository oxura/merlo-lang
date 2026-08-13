# Project and package contract

A project has `merlo.toml`, `merlo.lock`, `src/`, and optional `tests/`.
Manifest schema `1` contains `[project]` (`name`, `version`, `edition`) and
`[dependencies]`. Path dependencies are relative to their package. Git
dependencies require a URL and explicit revision.

Lockfile schema `1` records compiler compatibility, manifest hash, package
source hashes, and a deterministic dependency graph. JSON is canonical on disk;
TOML is available for tooling. Cycles, duplicate names, incompatible revisions,
and source/manifest mismatches are errors.

The package distribution includes the alpha `.mlo` standard library, examples,
public docs/specs, and editor grammar as data. Historical build/world artifacts
are readable but are not production routes.
