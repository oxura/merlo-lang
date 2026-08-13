# Capabilities

Capabilities are immutable authority records attached to a task scope. A
manifest may list only names from the closed alpha effect set and may constrain:

- `filesystem_roots` for `fs.read` and `fs.write`;
- `network_hosts` for TCP/HTTP access;
- `environment_keys` for `env.read`;
- `process_arguments` together with the `process.args` effect.

Child scopes use a narrowed manifest. Effects, roots, hosts, keys, and argument
access can only become smaller. Attempts to widen a scope raise
`CapabilityScopeEscape`. Missing effect authority raises `MissingCapability`;
unsupported effect names are rejected while constructing the manifest.

The capability model is intentionally explicit but should not be over-read. It
checks the Merlo host operations that use it. It does not make an arbitrary
native binary, C library, or operating-system process safe, and it is not a
replacement for OS isolation.
