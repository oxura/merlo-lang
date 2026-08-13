# Security policy

Merlo `0.1.0-alpha.1` is an early compiler and tooling release. Treat generated
native binaries, FFI declarations, project dependencies, and capability
manifests as code that requires review. The capability checks narrow the
operations declared by a task; they are not a substitute for host OS isolation,
input validation, or a sandbox.

## Reporting

Do not publish an exploitable report before maintainers have had a chance to
triage it. Send a concise report to the repository maintainers through the
project's private security channel, if one has been configured, or open a
non-sensitive issue asking for a private contact. Include the release, host
platform, minimal source/project, command, observed diagnostic or native
behavior, and whether an FFI declaration or external package is involved.
Remove secrets and personal data from reports.

## Scope notes

The alpha target is Linux x86-64, C11 Clang/GCC bootstrap, and synchronous I/O.
There is no cycle collector, capturing-closure runtime, `async` runtime,
registry, macros, traits, or self-hosting implementation. Historical research
commands are not production security surfaces. FFI is an explicit unsafe
boundary and must be wrapped with fixed-width ABI types, declared pointer
ownership, effects, and checked error handling.

This policy describes coordinated reporting; it does not change the project's
MIT OR Apache-2.0 license terms.
