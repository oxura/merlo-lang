# Merlo alpha specification

The files in this directory define normative behavior for the active
`0.1.0-alpha.3-dev` line. The published prerelease remains alpha.2. User guides
in `docs/` may explain behavior more broadly; when wording differs, the
specification is authoritative for the implemented alpha subset.

- [Language core](language.md)
- [Ownership and borrowing](ownership.md)
- [Memory Model v1](memory-model.md)
- [Effects and capabilities](effects.md)
- [Packages and lockfiles](packages.md)
- [FFI boundary](ffi.md)
- [SemanticWorld](semantic-world.md)
- [AlphaProtocol](alpha-protocol.md)

The specification covers only implemented behavior. Proposed syntax or runtime
features belong in `rfcs/` and do not become normative until implemented,
tested, and incorporated here. Concurrency Model v1 therefore remains
[RFC 0002](../rfcs/0002-concurrency-model-v1.md).
