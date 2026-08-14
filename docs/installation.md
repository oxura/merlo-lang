# Installation and first project

Merlo is distributed as a Python package for Python 3.11 or newer. The release
target is Linux x86-64. Until alpha.2 is published, install the verified
alpha.1 wheel rather than its incomplete source archive:

```console
python3 -m venv .venv
. .venv/bin/activate
python -m pip install https://github.com/oxura/merlo-lang/releases/download/v0.1.0-alpha.1/merlo-0.1.0a1-py3-none-any.whl
merlo --help
```

Native commands also require a C11-capable Clang or GCC. The bootstrap compiler
requires the Python interpreter but has no third-party Python runtime
dependencies.

## Clean demo

```console
merlo new hello --name hello
merlo check hello
merlo fmt hello --check
merlo test hello
merlo build hello
merlo run hello
```

`merlo new` creates a manifest, lockfile, and `src/main.mlo`. The generated
program is the starting point; edit it before treating `run` output as your own
program's result. `check` builds the typed project and updates its semantic
world state. `build` emits a native executable under `.merlo/build` by default;
`run` builds and executes it. `test` discovers project tests. `fmt --check`
reports whether source formatting would change.

Use `--json` on commands that expose it when a script needs deterministic
machine-readable output. A failed command returns a diagnostic exit status;
this is not a promise that every host diagnostic has one stable wording.
