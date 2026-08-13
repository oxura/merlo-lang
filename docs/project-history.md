# Project history

Merlo's research and development began privately on 2026-03-19 under the
working name Meldra. The public repository starts with an honest import of that
existing work; it does not manufacture earlier public commits.

## 2026-03-19 — Research begins

The initial phase investigated the language model, semantic identities,
ownership without user-written lifetime annotations, explicit effects, and a
compiler representation that could serve both developer tools and native
lowering. This work remained private through the pre-alpha period.

## 2026-08 — Typed native core

The first experiments established typed HIR, explicit ownership operations, deterministic MIR, and a C11 backend. Early benchmark and differential-test modules retain the `meldra` name where renaming them would erase the provenance of an experiment.

## 2026-08 — Representation and memory work

The compiler added concrete representation descriptors for `Text`, `Bytes`, recursive enums, `Vec`, `Map`, `Box`, borrows, and resource handles. Generated move and drop operations became part of the checked lowering path rather than handwritten runtime conventions.

## 2026-08 — Merlo surface and projects

The project adopted the Merlo name and added the concise `.mlo` surface, modules, manifests, lockfiles, package resolution, effects, capabilities, standard-library modules, executable examples, CLI commands, and LSP support.

## 2026-08-14 — First public research alpha

The first public baseline packages the supported Linux x86-64 compiler, source, tests, specifications, reproducible evidence, and release process as `0.1.0-alpha.1`. The import commits group existing compiler, runtime, tests, and documentation by responsibility. Their timestamps describe publication, not the start of development.

Future history will be recorded as ordinary changes: one reviewable behavior per commit, with the focused test or executable evidence that supports it.
