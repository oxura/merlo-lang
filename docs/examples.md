# Examples

The packaged `examples/` corpus contains fifteen projects with manifests,
lockfiles, source, native tests, and expected/input data:

- `automation` — module composition and console output;
- `packages` — a local package and vendored path dependency;
- `network` — declared network effects;
- `ndjson` — line-oriented data processing;
- `json-cli` — JSON input and command arguments;
- `grep` — filesystem input and filtering;
- `csv` — CSV processing;
- `capacity-ledger` — checked multi-lane capacity aggregation;
- `ffi` — an explicit C FFI boundary;
- `invoice-report` — checked record and fixed-array accounting;
- `access-log` — streaming request and failure analysis;
- `byte-stats` — binary file iteration and checksums;
- `inventory` — vectors, predicates, and stock valuation;
- `task-board` — enum state aggregation;
- `tree-walk` — recursive boxed values and traversal.

Run an example from its directory with the production commands:

```console
merlo check examples/automation
merlo test examples/automation
merlo build examples/automation
merlo run examples/automation
```

For the two data-processing examples, the checked-in fixtures support:

```console
merlo run examples/capacity-ledger -- examples/capacity-ledger/input.txt
capacity-ledger
entries=4
alpha_minutes=40
beta_minutes=50

merlo run examples/json-cli -- examples/json-cli/input.json
json-bytes=33 root=object fields=2
```

These are fixture outputs, not claims about throughput, latency, portability,
or AI productivity.

The expected files document the corpus contracts; they are not claims about
performance or portability. `.merlo/world.json` and native build output are
local generated state and are not production routes.
