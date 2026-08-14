# Installation and first project

Merlo requires Python 3.11 or newer. The current prerelease supports Linux
x86-64. The alpha.1 source archive is incomplete, and its wheel predates later
compiler portability repairs. Install the GitHub prerelease wheel (no PyPI
availability is claimed):

```console
python -m pip install https://github.com/oxura/merlo-lang/releases/download/v0.1.0-alpha.2/merlo-0.1.0a2-py3-none-any.whl
merlo --help
```

Or install from a source checkout instead:

```console
git clone https://github.com/oxura/merlo-lang.git
cd merlo-lang
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
merlo --help
```

Native commands also require a C11-capable Clang or GCC. The bootstrap compiler
requires Python 3.11 or newer and has no third-party Python runtime
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

The generated program writes:

```text
ok
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
