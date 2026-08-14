# Meldra Stage 0.6P — Native Integrity and Realistic Memory

Status: **ACTIVE; MINIMAL NATIVE TEXT CORE SUPPORTED (2026-08-12); OTHER EXPANSION GAPS REMAIN**  
Protocol schema: 1  
Bootstrap backend: portable C11 only

## Goal

Stage 0.6P attempts to falsify three claims:

1. Aggressive Performance MIR optimization preserves language semantics.
2. Compiler-inferred ownership, borrows, regions, and uniqueness can remove most avoidable reference-count traffic without lifetime syntax in ordinary source.
3. Performance near a strong native baseline survives Text, Bytes, recursive values, acyclic sharing, and interface dispatch.

The stage does not attempt to prove that Meldra is universally fast. Miscompilation, undefined behavior, leaks, hidden copies, retain/release storms, missed vectorization, and workloads that lose badly are first-class results.

## Frozen predecessor

`tools/benchmarks/merlo/benchmarks/meldra_stage05p_freeze_v2.json` freezes Stage 0.5P before Stage 0.6P changes. Stage 0.5P and the Python sidecar remain `CRITICAL_FIXES_ONLY`. A Stage 0.5P defect requires a failing regression, a version increment, and preserved before/after evidence.

## Architecture contract

```text
Meldra source
    -> existing lossless CST / normalized performance parse
    -> Native Typed HIR v1
       |-> semantic adapter: symbols, revisions, references, effects, capabilities
       `-> Performance MIR: CFG, ownership, layouts, allocations, drops, checks
```

Native Typed HIR is the common versioned contract. The semantic adapter does not acquire low-level drops or CFG blocks. Performance MIR does not duplicate evolution metadata. Both branches preserve source mappings; Native HIR preserves stable `SymbolId`/`RevisionId`. For the common Stage 0.4/Stage 0.5P subset, types, references, values, effects, and capabilities must agree. The frozen Stage 0.4 parser is not modified.

Supported compiler core for this stage: `Bool`, `Int64`, `UInt64`, `Float32`, `Float64`; direct typed calls; flat scalar records; fixed `Array[T, N]`; dynamic `Slice[T]`; `Bytes`, `BytesBuilder`, borrowed `BytesView`; owned UTF-8 `Text`, borrowed `TextView`, and exhaustive `Utf8Decode`; branches, `match`, `while`, range `for`; checked indexing; unique move/drop; scoped shared/mutable borrow; and explicit `Shared[Array|Slice]` retain/release. Remaining declared expansion gaps: recursive value layouts, cyclic `SharedRc`, generic interfaces, dynamic dispatch, exceptions, threads, async, and FFI.

## Correctness protocol

Registered corpus:

- 5,000 valid generated programs;
- 2,000 invalid generated programs;
- fixed seeds declared in the correctness artifact;
- nested branches and loops, records, arrays, slices, moves, inferred borrows, direct calls, bounds checks, early returns, and shared values.

For supported programs:

```text
surface evaluator
== Native HIR evaluator
== unoptimized MIR interpreter
== MIR after each pass
== optimized MIR interpreter
== generated C native binary
```

Compared semantic observations: status, result, printed checksum, and error kind. Effect traces and ownership counters (allocations, drops, retains/releases, final state) are captured and checked against pass-specific invariants; they are not required to remain numerically equal when an optimization intentionally removes work. Invalid programs must produce their preregistered diagnostic class. A checksum from a few handwritten workloads is not compiler-correctness evidence.

## Integer and C semantics

- `UInt64`: modulo $2^{64}$.
- `Int64`: two-complement modulo $2^{64}$ reinterpreted as signed.
- `Float32` and `Float64`: IEEE-754 host operations; NaN payload identity is not promised.
- Generated C must not rely on signed-overflow UB, invalid aliasing, invalid alignment, unchecked pointer arithmetic, uninitialized reads, or out-of-bounds access.
- Generated C uses explicit unsigned helpers for wrapping signed arithmetic, `memcpy` bit reinterpretation, masked shifts, explicit division edge handling, and compiler flags `-fwrapv -fno-delete-null-pointer-checks -ffp-contract=off`.

Required modes: release, debug assertions, explicit bounds checks, ASan, UBSan, and LeakSanitizer when supported.

## Ownership model tournament

Internal states:

- `Unique`;
- `BorrowedShared`;
- `BorrowedMutable`;
- `RegionOwned`;
- `SharedRc`.

Ordinary unique-value code contains no lifetime annotation or retain/release. Stage 0.6P shared values require explicit `drop`/`release`; `retain` is available only for deliberate `Shared` aliases. Experimental `borrow` operations are scope-bound and cannot escape a function.

Measured lowerings:

- current reference counting;
- optimized reference counting;
- borrow-heavy;
- region-owned where lifetime bounds are proven;
- C manual ownership;
- C reference counting;
- Rust `Rc`/`Arc`, Go GC, and C# GC when reproducible toolchains are available.

Cycles are not collected. A statically visible `SharedRc` cycle is rejected with `SharedCycleUnsupported`. Foreign cycles are outside the guarantee and must be declared at a boundary.

## Text and Bytes implementation boundary

The minimal native `Text` core is supported by [`../tools/benchmarks/merlo/benchmarks/meldra_text_core_sprint.json`](../tools/benchmarks/merlo/benchmarks/meldra_text_core_sprint.json): owned valid RFC 3629 UTF-8, borrowed zero-copy `TextView`, byte-boundary slicing, scalar iteration/counting, `Bytes -> Text -> Bytes` ownership transfer, exhaustive `Valid(Text) | Invalid(error_offset)` handling, and automatic drops. The prerequisite direct-call builder gate passed 96 valid and 108 invalid cases. The Text decision corpus contains 768 valid and 640 invalid inputs, including fixed-seed fuzz families; 1,408 native executions matched the HIR, unoptimized MIR, optimized MIR, and Python strict-decoder oracle. ASan/UBSan/LSan covered all 1,408 corpus inputs plus transfer and large-text paths (4,230 executions) without violations. The two measured native workloads were `1.1143x` and `1.0377x` the equivalent checked C implementations, below the preregistered `1.20x` gate. Rust was unavailable, so the artifact makes no Rust performance claim.

This is deliberately not a general text library. Normalization, collation, grapheme segmentation, formatting, regex, line/word processing, immutable concatenation, and higher-level parsing remain unsupported until separate measured experiments justify their semantics and allocation policy. The predecessor Bytes artifacts are reused byte-for-byte; their cache hashes are recorded in the Text artifact.

## Recursive-values expansion boundary

Unique trees, linked lists, recursive enums, and shared acyclic DAGs are registered as deterministic external-reference scenarios. Native recursive layouts and indirection are not implemented in Stage 0.6P. Recursive compiler inputs are rejected; the scenarios are reported `UNSUPPORTED_DECLARED`. Indexed-array traversal remains a baseline only and is never reported as recursive-pointer evidence.

## Closed-interface expansion boundary

One minimal shape is registered: `Operation.apply(UInt64) -> UInt64`, implemented by `Square` and `Increment`, across monomorphic, sealed finite, and genuinely dynamic reference implementations. Stage 0.6P has no interface or trait lowering, so these scenarios are `UNSUPPORTED_DECLARED`; monomorphic direct calls remain part of the supported core. No general trait system is introduced.

## Optimizer evidence

Every pass requires positive and negative cases, before/after IR, semantic equivalence, counters, and missed-reason reporting. Stage 0.6P does not call Stage 0.5P collection specialization “full generics monomorphization”; it is recorded as collection-operation specialization until true type parameters exist.

Clang optimization records and representative assembly are preserved where supported. Generated C is compared with handwritten C by loops, vector instructions, direct/indirect calls, allocations, bounds branches, retain/releases, and stack frame.

## Compiler-phase evidence

Thirty repeated samples are retained for surface preprocessing/parse, scope and borrow validation, the combined typecheck-plus-MIR frontend, Native HIR construction, the HIR-to-MIR contract handoff, MIR optimization, and C source emission. The current frontend does not expose type checking independently from MIR lowering; the experiment reports that combined boundary instead of subtracting overlapping timers. External C object compilation and linking are timed separately for thirty runs.

## Benchmark method

Steady-state calibrated target: 200–500 ms per measured process. Frozen cross-language inputs are not enlarged when that would make an interpreter arm impractical. A below-target supported Meldra/C arm is accepted as timing-stable only when both have relative MAD $\le 5\%$; it remains labeled below-target. Declared unsupported Meldra workloads are excluded from the Meldra/C calibration gate and remain visible in the report. Each measured arm uses at least five warmups and thirty randomized-order measured runs. Inputs come from runtime arguments; independent reference checksums are printed. Raw samples, median, mean, minimum, p95, standard deviation, MAD, deterministic-bootstrap 95% interval, run count, CPU affinity result, governor, turbo state, load average, source metrics, and phase-specific build times are retained.

Corrupted-run exclusion rule, fixed before measurement: exclude only a non-zero exit, checksum mismatch, timeout, sanitizer failure, or explicit affinity-launch failure. Timing outliers remain in the sample. If intervals overlap, differences inside the overlap are not called wins.

Startup reports process launch separately from runtime initialization and workload execution when the runtime exposes those components.

## Toolchains

Host toolchains are preferred. Reproducible pinned containers are allowed when the host lacks a toolchain. Emulation is forbidden. Current container candidates are Rust 1.88, Go 1.24, and .NET SDK 8.0; exact image digests and versions must be frozen. C, Rust, and Go share the same target CPU policy. The available C# arm is a fresh-process, single-file JIT build; NativeAOT and in-process JIT steady-state are not measured and receive no claim. CPython is always shown.

## Registered corpus families

Arithmetic; vector/array compute; map/filter/fold; records; sorting; bounds-heavy loop; Text/UTF-8 scan; TextBuilder; word count; integer parser; Bytes transform; recursive tree; linked list; shared acyclic DAG; interface dispatch; allocation churn; small CSV parser; startup.

Each workload has a language-neutral specification, deterministic runtime input, expected checksum, algorithm, data representation, comparable arms, and classification (`microbenchmark`, `kernel`, `small_macrobenchmark`, `startup`, or `memory`). Categories are reported separately; no single all-workload geometric mean is a decision gate.

## Decision gates

Exactly one final status is permitted:

- `GO_NATIVE_CORE_EXPANSION`
- `CONTINUE_PERFORMANCE_RESEARCH`
- `GO_PYTHON_PLATFORM_ONLY`
- `NO_GO_NATIVE_LANGUAGE`

Thresholds are frozen before results.

### GO_NATIVE_CORE_EXPANSION

All must hold:

- 0 differential semantic mismatches on the registered corpus;
- 0 sanitizer violations;
- 0 unexplained checksum mismatches;
- every optimizer pass preserves observations;
- generated C does not depend on UB;
- unique numeric/array kernel geometric mean $\le 1.10\times$ C;
- realistic Text/Bytes families have no systematic gap $>1.25\times$ the strongest measured native baseline;
- recursive unique families have no systematic gap $>1.25\times$;
- shared workloads improve materially over the Stage 0.5P $1.698\times$ C result;
- RC, borrow, and region results are separate;
- results include repeated runs and confidence intervals;
- ordinary source has no lifetime syntax;
- borrow inference removes retain/releases;
- no hidden double-free or leak;
- unsupported cycles are diagnosed;
- Native Typed HIR is the common versioned contract;
- interfaces and devirtualization are measured;
- deterministic MIR, C, and binary remain stable;
- C, Rust, and Go are measured for a full GO.

If a key native toolchain remains unavailable, status is capped at `CONTINUE_PERFORMANCE_RESEARCH`.

### CONTINUE_PERFORMANCE_RESEARCH

Use when architecture and performance remain promising but correctness evidence, baselines, shared memory, Text/recursive maturity, or corpus breadth is incomplete.

### GO_PYTHON_PLATFORM_ONLY

Use when native efficiency does not justify compiler and memory-model complexity and Maximal Python is the more practical route.

### NO_GO_NATIVE_LANGUAGE

Use when realistic workloads broadly lose, safety imposes a large runtime tax, costs are unpredictable, correctness cannot be secured, or simple source requires user-visible ownership complexity.

## Non-goals

No flow, machine, scheduler, async runtime, networking, database layer, package registry/downloader, UI, web framework, mobile, GPU, LLVM, Cranelift, JIT, custom assembler, cyclic GC, complex generics, macros, inheritance, distributed runtime, new LLM, or IDE. C11 remains the bootstrap backend.

## Human and AI evidence

Static surface metrics are measured, but human usability remains `UNMEASURED_WITH_USERS`. Fewer lines are not usability proof. AI productivity remains `UNMEASURED` without a real model API and is not a performance gate.
