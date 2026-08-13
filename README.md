<div align="center">

# Merlo

**A concise, statically typed native programming language for humans and coding agents.**

[![CI](https://github.com/oxura/merlo-lang/actions/workflows/ci.yml/badge.svg)](https://github.com/oxura/merlo-lang/actions/workflows/ci.yml)
[![License: MIT OR Apache-2.0](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-4c566a.svg)](LICENSE-MIT)
[![Status: Research Alpha](https://img.shields.io/badge/status-research%20alpha-8f6f3e.svg)](CHANGELOG.md)

</div>

Merlo is an experimental language and compiler built around static types,
ownership, inferred effects, capabilities, and stable semantic representations.
The `0.1.0-alpha.1` toolchain now includes the experimental Surface 0.2
frontend, a Python 3.11+ bootstrap compiler, a C11 native backend, project
tooling, an LSP server, a standard library, and executable examples.
Research and development began privately on 2026-03-19. The first public
research alpha is released on 2026-08-14.

```merlo
User:
    name: Text
    nickname: Text?
    active: Bool

display_name(user) = user.nickname or user.name
active_names(users) = users.where(.active).map(display_name)
```

## What is implemented

- static inference with no `Any` escape hatch or truthiness coercion
- records, payload enums, `Option`, `Result`, and strict option fallback
- whole-function `let`/`var` inference and tail results
- transitive effect, capability, and typed-error inference for private calls
- immutable values, ownership checks, borrowing, moves, and generated drop glue
- modules, projects, lockfiles, packages, and deterministic semantic identities
- `Text`, `Bytes`, `Vec`, `Map`, arrays, slices, boxes, and streaming file input
- HIR, Representation IR, MIR, C11 lowering, and native executable generation
- CLI commands for `new`, `check`, `fmt`, `test`, `build`, `run`, `map`, and
  `inspect`
- an LSP server, editor grammar, FFI boundary, standard library, and eight
  runnable example projects

The supported alpha target is Linux x86-64 with a C11-capable Clang or GCC
toolchain. I/O is synchronous. Capturing closures, `async`, a package registry,
macros, traits, cycle collection, self-hosting, and production stability
guarantees are outside this release. See
[Known limitations](docs/limitations.md) for the exact boundary.

## Compiler

```text
.mlo source
    │
    ├── concise elaboration and type checking
    ▼
structured HIR
    ▼
Representation IR
    ▼
performance MIR
    ▼
C11 source
    ▼
native executable
```

The compiler keeps one semantic core. Human-facing source, canonical source,
the LSP, semantic inspection, and native lowering are projections of that core
rather than independent interpretations.

## Install and run

Use Python 3.11 or newer on Linux x86-64:

```console
python -m pip install -e '.[test]'
merlo --help
```

Create and exercise a project:

```console
merlo new hello --name hello
merlo check hello
merlo fmt hello --check
merlo test hello
merlo build hello
merlo run hello
```

`new` writes `merlo.toml`, `merlo.lock`, and `src/main.mlo`. The other commands
accept either a project directory or a source path. Diagnostics go to stderr;
commands with a machine-readable mode return deterministic JSON through
`--json`.

## Repository map

| Path | Contents |
|---|---|
| [`merlo/`](merlo/) | Bootstrap compiler, semantic model, native backend, CLI, and LSP |
| [`stdlib/`](stdlib/) | Merlo standard-library modules |
| [`examples/`](examples/) | Executable CLI, data, package, network, and FFI projects |
| [`tests/`](tests/) | Language, compiler, runtime, determinism, and release regressions |
| [`benchmarks/`](benchmarks/) | Reproducible corpora, fixtures, protocols, and checked evidence |
| [`spec/`](spec/) | Normative alpha contracts |
| [`docs/`](docs/) | Installation, language guide, architecture, tooling, and limitations |
| [`research/`](research/) | Research questions, methods, results, limitations, and artifacts |
| [`rfcs/`](rfcs/) | Process for proposed language and runtime changes |

## Documentation

- [Installation](docs/installation.md) and [language tour](docs/tour.md)
- [Types and values](docs/types.md), [ownership](docs/ownership.md), and
  [errors](docs/errors.md)
- [Effects, capabilities, and resources](docs/effects.md)
- [Modules, projects, and packages](docs/projects.md)
- [Compiler architecture](docs/architecture.md) and
  [project history](docs/project-history.md)
- [FFI](docs/ffi.md), [SemanticWorld](docs/semantic-world.md), and
  [AlphaProtocol](docs/alpha-protocol.md)
- [CLI and LSP tooling](docs/tooling.md)
- [Research index](research/README.md)

Historical research commands remain available under `merlo historical ...`.
They are not production routes.

## Contributing and license

Merlo is research software, but changes to public behavior still require a
focused regression and an executable check of the affected path. See
[CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the
[RFC process](rfcs/README.md).

Merlo is dual-licensed under your choice of
[Apache License 2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT).
