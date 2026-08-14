# Effects and capabilities contract

## Purpose

Effect analysis records host operations on task boundaries and checks declared
authority before native lowering. Effects describe operations; capabilities
constrain the corresponding checked authority.

## Inputs

The closed alpha vocabulary is `console.read`, `console.write`, `fs.read`,
`fs.write`, `env.read`, `clock.now`, `random.read`, `network.tcp`,
`network.http`, and `process.args`. Frontend task boundaries in
[`src/merlo/frontend_model.py`](../../src/merlo/frontend_model.py) carry effect
and capability tuples. The runtime vocabulary is defined by
[`src/merlo/runtime_contract.py`](../../src/merlo/runtime_contract.py).

## Outputs

`TaskBoundary.effects` and `TaskBoundary.capabilities` are sorted, serialized
in public interfaces, and included in task/interface revision IDs. In this
alpha the capability tuple mirrors the effect tuple; it is not a separate
scoped-authority object. A task must declare the effects required by the
current analysis before the coordinator accepts it.

## Invariants

Unknown effect names are rejected. A task with an inferred effect must expose a
corresponding declaration; extra declarations are currently accepted. Effects
propagate through the current private-call closure, and resolved tuples are
stored deterministically. Capability checks constrain recognized Merlo host
operations but do not isolate arbitrary native code.

## Failure modes

Missing effect declarations and unknown effect names raise a frontend/project
diagnostic with a source path and line when available. A host spelling that the
transitional analyzer does not recognize contributes no effect; that is a known
limitation, not proof that the function is pure. Same-named methods, aliases,
and comments are not guaranteed to be distinguished by the current source-based
analysis.

## Trusted boundary

The closed runtime vocabulary, task/interface revision payload, and declared
manifest authority are the checked boundary. An operation without authority is
rejected before the checked host operation. FFI and generated native code remain
explicit review boundaries.

## Experimental boundary

The current alpha effect discovery is source/AST based and does not expose a
stable public `infer_effects()` API. It does not provide a separate capability
syntax, a typed `EffectCycle` diagnostic, or a proof that native binaries are
sandboxed. RFC 0001's bound-call fixed point and distinct effect/capability IDs
remain future work.

## Verification commands

```console
merlo check PROJECT
merlo inspect TASK PROJECT --json
merlo explain PROJECT
```

These commands expose checked contract data; they do not turn capability
manifests into an operating-system security boundary.
