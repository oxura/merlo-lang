# Ownership without lifetime syntax

## Question

Can a concise language reject use-after-move and unsafe borrowed returns while generating deterministic native cleanup without exposing lifetime annotations in ordinary source?

## Hypothesis

A restricted ownership model with immutable values, explicit borrow-producing operations, lexical resource lifetimes, and representation-directed drop glue can cover the alpha's CLI and data workloads without a general lifetime language.

## Method

The compiler checks moves and borrows in typed HIR, lowers ownership operations into Representation IR and MIR, emits type-specific C cleanup, and executes native regression, sanitizer, and differential corpora.

## Result

The supported subset covers owned text and bytes, recursive values, vectors, maps, boxes, result payloads, file resources, reborrows, and call boundaries. The compiler rejects the covered use-after-move, escaping-borrow, double-drop, and incompatible representation cases.

## Limitations

The model is not a proof of memory safety for arbitrary C FFI, capturing closures, concurrency, cycles, or future language features. FFI remains an explicit unsafe boundary. The current target is Linux x86-64.

## Artifacts

- `src/merlo/representation_ir.py`
- `src/merlo/representation_c_backend.py`
- `tests/test_alpha_ownership.py`
- `research/archive/historical_protocol/tests/test_meldra_move.py`
- `tools/benchmarks/merlo/tests/test_meldra_bytes_reborrow.py`
- `research/archive/alpha1/benchmarks/merlo_alpha_sanitizers.json`
