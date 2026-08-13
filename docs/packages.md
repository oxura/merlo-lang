# Packages and lockfiles

See [Projects, packages, and lockfiles](projects.md) for the complete workflow.
A package is a manifest-backed source tree with a name, version, dependencies,
and deterministic source hash. Path dependencies resolve from the owning
package; Git dependencies require an explicit revision.

The alpha manifest and lockfile schemas are both version `1`. Canonical
lockfiles are JSON and include compiler compatibility, the manifest hash,
package hashes, and a sorted dependency graph. Dependency cycles, duplicate
names, and compatibility mismatches are diagnostics.
