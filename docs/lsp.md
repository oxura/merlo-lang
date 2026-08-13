# LSP

The Python `merlo.lsp` module implements the alpha JSON-RPC/LSP facade. It
uses byte-accurate `Content-Length` framing, file URIs, document versions, and
UTF-16 positions. The facade connects diagnostics, symbols, references,
formatting, and supported semantic refactors to the compiler and SemanticWorld.

This alpha does not claim a separate `merlo lsp` console route or editor server
binary. Use the Python API from an editor integration, and treat stale-world or
compile diagnostics as actionable rather than silently serving old data. See
[tooling](tooling.md) for the production CLI routes.
