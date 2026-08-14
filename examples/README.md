# Merlo examples

Every subdirectory is a complete project with `merlo.toml`, a deterministic `merlo.lock`, source, tests, input, and expected output.

| Example | Focus |
|---|---|
| `automation` | Multi-module text processing and reporting |
| `packages` | Vendored package and interface lock |
| `network` | Capability-restricted TCP operations |
| `ndjson` | Streaming NDJSON aggregation |
| `json-cli` | JSON parsing and typed result handling |
| `grep` | Streaming text search |
| `csv` | Typed CSV aggregation |
| `capacity-ledger` | Checked multi-lane capacity aggregation |
| `ffi` | Explicit C ABI boundary |

Run an example with the production CLI:

```console
merlo check examples/automation --json
merlo test examples/automation --json
merlo build examples/automation --json
merlo run examples/automation --json
```

Run the capacity ledger demonstration:

```console
merlo run examples/capacity-ledger -- examples/capacity-ledger/input.txt
```

The `.merlo/` directory is generated locally and is not source material.
