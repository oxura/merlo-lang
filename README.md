<div align="center">

# Merlo

**A concise, statically typed native programming language for humans and coding agents.**

[![CI](https://github.com/oxura/merlo-lang/actions/workflows/ci.yml/badge.svg)](https://github.com/oxura/merlo-lang/actions/workflows/ci.yml)
[![License: MIT OR Apache-2.0](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-4c566a.svg)](LICENSE-MIT)
[![Status: Research Alpha](https://img.shields.io/badge/status-research%20alpha-8f6f3e.svg)](CHANGELOG.md)

</div>

Merlo is an experimental language and compiler built around static types,
ownership, inferred effects, capabilities, and stable semantic representations.
The current public toolchain is `0.1.0-alpha.1`: a Python 3.11+ bootstrap
compiler with a C11 native backend, project tooling, an LSP server, a standard
library, and executable examples. Its source release is known to be incomplete;
the repository is being stabilized for one clean `0.1.0-alpha.2`.
Research and development began privately on 2026-03-19. The first public
research alpha was released on 2026-08-14.

```merlo
User:
    name: Text
    nickname: Text?
    active: Bool

display_name(user) = user.nickname or user.name
active_names(users) = users.where(.active).map(display_name)
```

The short form is the design target. A real program is deliberately more
explicit about host failures and effects:

```merlo
export main(path: Path) -> Result[Text, AppError]:
    data = fs.read(path)?
    result = summarize(data)
    console.write(result)
    Ok(result)
```

```console
$ merlo run examples/json-cli -- examples/json-cli/input.json
json-bytes=33 root=object fields=2
```

## What is implemented

- static inference with no `Any` escape hatch or truthiness coercion
- records, payload enums, `Option`, `Result`, and strict option fallback
- whole-function `let`/`var` inference and tail results
- experimental effect, capability, and typed-error inference
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

> **Security note:** Merlo capabilities constrain checked program behavior, but
> the current native runtime is not an operating-system security sandbox. Run
> untrusted binaries inside normal host isolation.

Merlo is designed around structured semantics for coding agents. The
productivity advantage has not yet been independently validated. No general
native-performance, simplicity, or AI-productivity superiority is claimed.

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

Merlo is converging on one semantic core shared by compilation and tooling.
The current project frontend still contains transitional text-oriented
elaboration that is being replaced under [RFC 0001](rfcs/0001-repository-and-frontend-stabilization.md).

## Install and run

Use Python 3.11 or newer on Linux x86-64. Until alpha.2 is published, install
the verified alpha.1 wheel directly; do not use its incomplete source archive:

```console
python -m pip install https://github.com/oxura/merlo-lang/releases/download/v0.1.0-alpha.1/merlo-0.1.0a1-py3-none-any.whl
merlo new hello --name hello
merlo run hello
```

The observed generated project output is:

```text
ok
```

For a full project check:

```console
merlo check hello
merlo fmt hello --check
merlo test hello
merlo build hello
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

Historical research remains reproducible from repository sources. It is not
part of the supported user workflow and will be removed from the production CLI.

## Contributing and license

Merlo is research software, but changes to public behavior still require a
focused regression and an executable check of the affected path. See
[CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the
[RFC process](rfcs/README.md).

Merlo is dual-licensed under your choice of
[Apache License 2.0](LICENSE-APACHE) or [MIT](LICENSE-MIT).
