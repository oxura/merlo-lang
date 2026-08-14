# CLI, formatter, documentation, and LSP

The production console script is `merlo`. Its project commands are:

- `new`, `check`, `build`, `run`, and `test`;
- `fmt`, `expand`, `explain`, `doc`, and `map`;
- `inspect`, `refs`, `callers`, `callees`, `deps`, `impact`, `why`, and `context`;
- `refactor rename|move|signature` and `add --path|--git`.

Use `merlo --help` for parser-level details. `--json` is available on the
commands that emit structured payloads. `doc` writes generated API markdown to
`docs/API.md` by default; use `-o` for another destination. `map` supports
`text`, `dot`, and `json` projections. `fmt --stdout` prints formatted source;
`fmt --check` reports drift without writing.

`merlo fmt` preserves the human Surface AST. It does not insert canonical
keywords. `merlo expand` projects the typed Canonical AST with complete
parameter, return, binding, task, effect, capability, and error facts.
`merlo explain` reports those decisions and their evidence without requiring
inspection of generated source. Neither command is a textual normalizer.

## Language server

`merlo.lsp` provides a small JSON-RPC/LSP facade over the project compiler,
SemanticWorld, and AlphaProtocol. It frames messages with byte-accurate
`Content-Length` headers, understands file URIs, tracks document versions, and
returns diagnostics, symbols, references, definitions, formatting, and semantic
refactor responses supported by the alpha implementation. It is a Python API
surface in this release; no separate `merlo lsp` production subcommand is
claimed.

## Explicit non-production suites

The production contract suite is isolated from research tooling:

```text
python -m pytest tests/
python -m pytest tools/benchmarks/merlo/tests/
python -m pytest tools/release/merlo/tests/
python -m pytest research/archive/historical_protocol/tests/
python -m pytest research/archive/alpha1/tests/
```

