# Reproducible multi-arm performance evidence

## Question

How can an early compiler report performance without silently substituting implementations, averaging away unstable runs, or detaching measurements from source?

## Hypothesis

Frozen fixtures, independent runner arms, source and binary locks, sequential randomized scheduling, raw samples, robust dispersion gates, and explicit unavailable states make performance claims inspectable.

## Method

The alpha protocol runs concise Merlo, canonical Merlo, C, and Python arms with identical workload inputs. Rust is optional and stays unavailable when its toolchain is absent. Each measured sample preserves elapsed time, output digest, startup time, and memory observation. Per-round aggregation uses the minimum replicate to reduce scheduler interference; reports retain every replicate.

## Result

The checked report identifies the exact implementation source for every required arm, preserves raw samples, validates output equivalence, and reports gate failures instead of replacing missing measurements. It is evidence for the recorded machine and workloads, not a universal language ranking.

## Limitations

The corpus is small, Linux x86-64 only, and sensitive to compiler versions, CPU state, thermal behavior, and background load. Native C is a workload reference rather than a complete language comparison. New workloads and independent reproduction remain necessary.

## Artifacts

- `tools/benchmarks/merlo/alpha_performance.py`
- `tools/benchmarks/merlo/productive_performance.py`
- `research/archive/alpha1/benchmarks/alpha_performance/workloads.json`
- `research/archive/alpha1/benchmarks/merlo_alpha_performance.json`
- `tools/benchmarks/merlo/tests/test_alpha_performance.py`
- `tools/benchmarks/merlo/tests/test_productive_performance.py`
