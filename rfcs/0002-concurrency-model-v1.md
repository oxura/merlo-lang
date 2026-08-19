# RFC 0002: Concurrency Model v1

Status: proposed. This RFC does not make `async`, channels, or M:N scheduling a
production Merlo feature.

## Goal

Merlo should support simple service concurrency without merging it with data
parallel execution. Task IR schedules many independent operations and I/O.
Parallel IR schedules pure data work across scalar, SIMD, CPU, or GPU lanes.
They share types, ownership, effects, capabilities, diagnostics, profiling, and
cost evidence, but have different semantics and runtimes.

## Structured task model

Every spawned task belongs to a lexical task group. A group cannot finish while
an un-detached child is live. Leaving the group waits for successful children
or cancels and joins the remaining children after failure. Cancellation is
cooperative, typed, and observed at declared suspension points.

Proposed surface:

```merlo
serve(request):
    scope:
        user = spawn load_user(request.user_id)
        orders = spawn load_orders(request.user_id)
        profile = await user
        history = await orders
        Response(profile, history)
```

`spawn` transfers its arguments into the child unless a type is explicitly safe
to share. The parent cannot use a moved argument. A child result is owned by the
awaiting task. Borrowed stack views, mutable aliases, and unscoped resources
cannot cross the task boundary.

## Transfer and sharing

Type descriptors gain two independent properties:

- transferable: ownership may move to another task;
- shareable: immutable aliases may be observed concurrently.

These are compiler properties, not required everyday annotations. Mutable
sharing requires a typed synchronization wrapper. Plain shared mutable records
are rejected. Resources may cross a task boundary only when their contract
declares transfer and the destination scope becomes responsible for close.

Safe concurrent Merlo must not permit a data race. This rule covers language
operations, runtime collections, generated code, and safe host contracts. FFI
and unsafe code remain outside the guarantee.

## Runtime

The production target is an M:N scheduler with multiple worker threads,
work-stealing deques, bounded task metadata, cooperative suspension, a blocking
pool, timer wheel, and platform I/O reactor. Linux uses epoll or io_uring first;
macOS and Windows may later use kqueue and IOCP.

Scheduling order is not deterministic unless a deterministic mode is selected.
Program results must not depend on scheduling order except through operations
whose contract explicitly permits nondeterminism. Cancellation, deadlines,
channel close, and select tie-breaking require specified observable behavior.

## Channels, select, and backpressure

Channels are typed and bounded by default. Sending transfers the value into the
channel. Receiving transfers it out. Closing is idempotent only when the type
contract says so; otherwise double close is rejected or traps with a stable
diagnostic. A blocked sender or receiver is cancellation-aware.

`select` operates on typed channel, timer, and cancellation events. Fairness and
tie-breaking must be testable. Unbounded queues require an explicit spelling
and profiler visibility. Network and stream APIs expose backpressure rather
than silently accumulating unbounded buffers.

## Effects and capabilities

Spawning is an effect distinct from the child operation's effects. A child may
receive only capabilities narrowed from its parent scope. No spawn, channel, or
callback can widen filesystem roots, hosts, environment keys, process access,
or future device authority. Cancellation cleanup uses only authority already
owned by that scope.

## Failure and cleanup

Task groups define supervision policy. The default is fail-fast: the first
unhandled child failure cancels siblings, joins them, runs resource cleanup,
and returns a typed error. Detached tasks require an explicit supervisor whose
lifetime and authority are visible. There are no silent orphan tasks.

Resource cleanup runs after children are joined and in reverse acquisition
order within each scope. An explicit close consumes the handle. Cancellation
cannot turn a borrowed resource into an owner or skip mandatory close evidence.

## Atomics

Atomics are not part of the initial user milestone. When introduced, each
operation must state a memory order. The minimum set is relaxed, acquire,
release, acquire-release, and sequentially consistent. Non-atomic conflicting
access remains a data race and is forbidden. Compiler optimizations must
preserve the chosen order.

## Separation from Parallel IR

Task IR does not automatically map ordinary request handlers to GPU lanes.
Parallel IR accepts pure regions with explicit dependency, reduction,
associativity, commutativity, data layout, transfer, and determinism facts.
Bridging the two requires a typed awaitable parallel job with explicit device
capability and cancellation semantics.

## Acceptance gates

The RFC may become normative only after the production path demonstrates:

- one million sleeping tasks without failure and bounded per-task memory;
- sustained high-connection networking with explicit backpressure;
- predictable cancellation, cleanup, fairness, and p99/p999 latency;
- zero safe-code data races in the frozen concurrency corpus;
- scaling and memory comparisons against a frozen Go baseline;
- sanitizer and race-detector coverage plus deterministic scheduler replay for
  reduced failures;
- real HTTP service and broker applications, not only scheduler fixtures.

Exact numerical thresholds beyond the memory/task gate are fixed with the
benchmark hardware and Go baseline before measurement, not after results.
