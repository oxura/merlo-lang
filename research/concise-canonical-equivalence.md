# Concise and canonical source equivalence

## Question

Can a short human-facing surface and an explicit compiler-facing form share one semantics rather than drift into two languages?

## Hypothesis

If concise source elaborates into a typed canonical program before representation lowering, both forms can be compiled independently and compared at the native artifact boundary.

## Method

Productive workloads are compiled from concise projects and from materialized canonical expansions. The registry records source and generated-artifact hashes, runs both binaries with the same fixtures and arguments, and checks output digests and native artifact identity.

## Result

The checked alpha workloads satisfy the concise/canonical equivalence gates recorded by the performance and simplicity reports. The canonical path is a separately materialized compiler input, not a Python reference substituted for Merlo.

## Limitations

The result covers the frozen workloads and implemented language subset. It is not a proof for every syntactically valid future program. Any change to source, elaboration, compiler, or workload invalidates the corresponding evidence lock.

## Artifacts

- `src/merlo/concise_application.py`
- `tools/benchmarks/merlo/productive_performance.py`
- `tools/benchmarks/merlo/tests/test_concise_application_alpha.py`
- `tools/benchmarks/merlo/tests/test_productive_performance.py`
- `research/archive/alpha1/benchmarks/merlo_alpha_performance.json`
