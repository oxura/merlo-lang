# AI protocol

Merlo's public machine-facing protocol is `AlphaProtocol`, backed by
`SemanticWorld`. The compiler and tooling are converging on shared semantic
facts; this page does not claim that every current path is already one
semantic core. The protocol exposes deterministic queries and explicit
refactor plans rather than a second language evaluator. See
[AlphaProtocol](alpha-protocol.md) and its [public specification](../spec/alpha-protocol.md).

A client should inspect a target, review references/impact, preview a rename,
move, or signature plan, and apply only a ready plan. Stale worlds, unsupported
migrations, missing targets, and insufficient capabilities are diagnostics.
Protocol output is advisory structured data; it does not bypass compiler,
ownership, effect, capability, or FFI checks.

Merlo is designed around structured semantics for coding agents, but any
productivity advantage remains unvalidated.
