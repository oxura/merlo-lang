# Merlo validation and benchmark artifacts

This directory contains reproducible fixtures, corpus manifests, runners, raw reports, and checked evidence used during the alpha research cycle.

The active benchmark evidence sets are:

- `stage05p_*`, `stage06p_*`, and `meldra_*` manifests and fixtures
- productive corpus, performance, safety, and simplicity reports

Frozen Alpha1 evidence is retained under
`research/archive/alpha1/benchmarks/` and is not part of this active tool tree.

A report is meaningful only with its schema version, source hashes, fixture hashes, command, toolchain, schedule, and raw samples. `UNMEASURED` and failed gates remain visible; runners must not substitute a different implementation for a missing arm.

Generated native binaries, sanitizer scratch directories, local build caches, and temporary corpora are release artifacts rather than source and are excluded from the public repository. Release binaries are attached to the corresponding GitHub Release.
