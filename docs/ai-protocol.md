# AI protocol

Merlo's public machine-facing protocol is `AlphaProtocol`, backed by the one
`SemanticWorld` semantic core. It exposes deterministic queries and explicit
refactor plans rather than a second language evaluator. See
[AlphaProtocol](alpha-protocol.md) and its [public specification](../spec/alpha-protocol.md).

A client should inspect a target, review references/impact, preview a rename,
move, or signature plan, and apply only a ready plan. Stale worlds, unsupported
migrations, missing targets, and insufficient capabilities are diagnostics.
Protocol output is advisory structured data; it does not bypass compiler,
ownership, effect, capability, or FFI checks.
