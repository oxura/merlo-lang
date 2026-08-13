# Effects, capabilities, and resources

Private Surface 0.2 functions do not repeat effects that the compiler can
derive:

```merlo
load(path: Path):
    data = fs.read_text(path)?
    print data
    data
```

This elaborates to a `task` with `fs.read` and `console.write` capabilities and
a closed `FileError` row. Effects, capabilities, and errors propagate through
private calls. Explicit canonical/project interfaces still display the complete
contract; an explicit interface-lock update is required before a public
contract may change.

The alpha effect set is closed:

`console.read`, `console.write`, `fs.read`, `fs.write`, `env.read`, `clock.now`,
`random.read`, `network.tcp`, `network.http`, and `process.args`.

Effects describe the operation. A capability manifest supplies the authority:
filesystem roots, network hosts, allowed environment keys, and process-argument
access are explicit. An operation without its effect is rejected before the host
operation. A child scope can only narrow effects and resource ranges; it cannot
escape to a parent or unrelated path.

`ResourceScope` owns closeable host resources and closes them in reverse order
on every exit. A close failure is surfaced as `ResourceCloseFailure`. Runtime
operations are synchronous in alpha; there is no async effect scheduler.

Capability checks are scoped host controls, not a complete security sandbox.
Review manifests, inputs, native output, and FFI code together.
