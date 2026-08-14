# Contributing to Merlo

Merlo alpha work should move compilation and tooling toward the shared semantic
contracts documented in `spec/`. Prefer a small change at the source of a
problem over a compatibility alias or a second interpretation of the same
program.

## Development setup

Use Python 3.11 or newer and a Linux x86-64 host. A C11-capable Clang or GCC is
needed for native compiler paths. Create a virtual environment outside the
repository if desired:

```console
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
```

The bootstrap compiler requires Python 3.11 or newer and has no third-party
Python runtime dependencies. The test extra is for development only. The
project manifest and lockfile formats are described in
[docs/projects.md](docs/projects.md).

## Checks before a change is proposed

Run the focused tests for the subsystem, then the full test suite. For a user
visible language or CLI change, exercise a real project with `merlo check` and,
when native behavior changes, `merlo build`/`merlo run` on Linux. Keep generated
build output and `.merlo/world.json` out of production examples. Do not report
performance, safety, or portability results unless the corresponding checked
evidence is included.

For distribution work, the repeatable manual hooks are:

```console
rm -rf dist build *.egg-info
python -m build --wheel --outdir dist
python -m venv /tmp/merlo-alpha-venv
/tmp/merlo-alpha-venv/bin/python -m pip install --no-deps dist/*.whl
/tmp/merlo-alpha-venv/bin/merlo --help
```

These commands are review hooks, not a claim that every contributor's machine
has the same compiler or packaging tools.

## Documentation and specs

Public behavior belongs in `docs/`; normative alpha contracts belong in
`spec/`. State limitations plainly. Keep examples executable or label a snippet
as illustrative. Historical commands and artifacts can be documented for
readability but must not be presented as production routes.

## Pull requests

Describe the observable behavior, files changed, and evidence used. Include
regressions for new public contracts where the existing suite does not already
cover them. Avoid unrelated formatting changes. Unless explicitly stated
otherwise, contributions are accepted under the repository's dual
MIT OR Apache-2.0 license.
