# SemanticWorld

`SemanticWorld` is the current project's deterministic semantic index. It is
built from a compiled project and can be persisted at `.merlo/world.json`.
The world records modules, exported symbols, signatures, imports, interface and
implementation revisions, dependency information, diagnostics, and a world
digest.

The production CLI exposes these queries:

```console
merlo inspect TARGET PROJECT
merlo refs TARGET PROJECT
merlo callers TARGET PROJECT
merlo callees TARGET PROJECT
merlo deps TARGET PROJECT
merlo impact TARGET PROJECT
merlo context TARGET PROJECT --goal "..."
merlo map PROJECT --projection text
```

`--json` emits deterministic JSON for commands that accept it. A stale or
incompatible world/lockfile is a diagnostic; callers should rebuild rather than
silently mixing revisions. The world is an index over compiler state; Merlo is
converging on a shared semantic core for compilation and tooling, not claiming
that every current transitional path is already unified.
