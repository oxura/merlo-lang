# Merlo validation and benchmark artifacts

This directory contains reproducible fixtures, corpus manifests, runners, raw reports, and checked evidence used during the alpha research cycle.

The main public evidence sets are:

- `alpha_performance/workloads.json` and `merlo_alpha_performance.json`
- `merlo_alpha_corpus.json` and `merlo_alpha_corpus_execution.json`
- `merlo_alpha_sanitizers.json`
- `merlo_alpha_simplicity.json`
- `merlo_alpha_examples.json`
- `merlo_alpha_release_validation.json`

A report is meaningful only with its schema version, source hashes, fixture hashes, command, toolchain, schedule, and raw samples. `UNMEASURED` and failed gates remain visible; runners must not substitute a different implementation for a missing arm.

Generated native binaries, sanitizer scratch directories, local build caches, and temporary corpora are release artifacts rather than source and are excluded from the public repository. Release binaries are attached to the corresponding GitHub Release.
