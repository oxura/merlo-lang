# AlphaProtocol contract

`AlphaProtocol` provides structured SemanticWorld queries and named refactor
operations. Production refactors are `rename`, `move`, and `signature`; each
can be previewed and supported rename operations may be applied explicitly.

A refactor plan must identify its target, affected source, capability, and
migration diagnostics. Missing targets, invalid names, stale worlds, and
unsupported migrations are diagnostics. Applying a plan is not implicit in a
preview.

The historical protocol/provider remains readable under `merlo historical` but
is not the production AlphaProtocol route. Protocol output does not grant
permission to bypass type, ownership, effect, or capability checks.
