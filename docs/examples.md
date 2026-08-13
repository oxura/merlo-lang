# Examples

The packaged `examples/` corpus contains small projects with manifests,
lockfiles, source, tests, and expected/input data:

- `automation` — module composition and console output;
- `packages` — a local package and vendored path dependency;
- `network` — declared network effects;
- `ndjson` — line-oriented data processing;
- `json-cli` — JSON input and command arguments;
- `grep` — filesystem input and filtering;
- `csv` — CSV processing;
- `ffi` — an explicit C FFI boundary.

Run an example from its directory with the production commands:

```console
merlo check examples/automation
merlo test examples/automation
merlo build examples/automation
merlo run examples/automation
```

The expected files document the corpus contracts; they are not claims about
performance or portability. `.merlo/world.json` and native build output are
local generated state and are not production routes.
