# RFC 0006: Executable MIR Ownership SSA v1

- Status: Draft
- Tracking issue: https://github.com/oxura/merlo-lang/issues/101
- Depends on: Type Arena v1 (PR #81), RIR/MIR Type Authority v3 (PR #98), MIR-only C backend (PR #99)
- Target: `0.1.0-alpha.3-dev`

## Problem

Executable MIR makes some transfers and drops visible, but its verifier does not prove ownership balance over the control-flow graph. A malformed or optimizer-corrupted artifact can duplicate a consume, remove a destroy, end a borrow early, destroy a borrow base, or change ownership at a join while retaining valid TypeIds and layouts.

The frontend ownership checker cannot close this boundary. Serialized MIR is independently loadable, optimizers rewrite it, and native code generation trusts it. Ownership therefore needs an executable, versioned MIR contract rather than another source-level analysis result.

A representative invalid MIR fragment is:

```text
%text = call make_text() : Text
move_value %text -> call consume
move_value %text -> return
```

Both instructions are individually well typed. The ownership verifier must reject the second consume without consulting source text.

## Contract

MIR schema v4 carries `merlo.mir-ownership.v1` as a required ownership SSA program. The program is deterministic, keyed by function symbol, basic block, MIR instruction identity, ValueId, place, and TypeId. It is part of canonical MIR JSON and therefore part of the MIR digest.

The ownership program is derived from executable MIR and its existing TypeArena/drop-plan authority. Deserialization recomputes the expected program and rejects missing, stale, reordered, or altered ownership metadata. The verifier runs after MIR construction, after deserialization, and after each optimizer transformation.

Only MIR changes schema in this RFC:

```text
HIR v13 -> RIR v6 -> executable MIR v4 + ownership SSA v1
```

HIR, RIR, LayoutId, Type Arena, and runtime ABI versions do not change.

## Ownership kinds

Every tracked SSA value and place has one of four kinds:

- `Trivial`: freely copyable and has no destruction obligation.
- `Owned`: exactly one live logical owner must be moved or destroyed on every reachable path.
- `Guaranteed`: a non-owning value with exactly one declared base owner. Its lifetime is bounded by `begin_borrow` and `end_borrow`.
- `Unowned`: a raw or foreign non-owning value without a lifetime guarantee. It may be passed only through an explicit non-owning contract and may not implicitly initialize owned storage.

A TypeId with a bound DropPlanId is owning. Borrow/view constructors and borrowed function parameters are guaranteed. Raw pointer constructors are unowned. Remaining types are trivial. Per-value provenance may narrow an owning storage type to a guaranteed borrow but may not silently widen a guaranteed or unowned value to owned.

## Operation vocabulary

Ownership SSA v1 has a closed operation set:

- `move_value`: transfer one live owned SSA value to a consuming target;
- `copy_value`: duplicate or observe a trivial, guaranteed, or unowned value; applying it to `Owned` is an implicit-clone error;
- `destroy_value`: discharge one live owned value or initialized owned place;
- `begin_borrow`: create one guaranteed value and bind it to one live base owner;
- `end_borrow`: end that exact guaranteed value;
- `load_copy`: load a trivial, guaranteed, or unowned place without consuming it;
- `load_take`: move an owned place into a new SSA value and leave the place uninitialized;
- `store_init`: initialize live uninitialized storage;
- `store_assign`: replace initialized storage, discharging its previous value before transfer;
- `storage_live`: introduce storage in the uninitialized state;
- `storage_dead`: end storage after all ownership and borrow obligations are discharged.

Operations are ordered within their MIR block. Each operation carries its TypeId, ownership kind, source instruction identity when applicable, and exact value/place/base references. Unknown operations or extraneous fields are schema errors.

## Verification

The verifier executes ownership operations over the MIR CFG with a monotone worklist. There is no arbitrary iteration cap. A block is revisited only when its incoming state changes.

The state tracks:

- live or consumed owned SSA values;
- dead, uninitialized, or initialized places;
- live or ended guaranteed values and their bases;
- ownership kinds for block-visible values.

The verifier requires:

1. Every owned SSA value is consumed exactly once on every reachable exit path.
2. Every initialized owned place is moved, assigned with destruction, or destroyed before `storage_dead`.
3. A consume or destroy cannot occur twice.
4. A base owner remains live until every dependent `end_borrow`.
5. A guaranteed value has exactly one base and cannot escape through an owning return or store.
6. An unowned value cannot implicitly initialize or replace owned storage.
7. Trivial values are excluded from consume obligations.
8. Incoming ownership states at a CFG join are identical for the same ValueId/place; MIR v1 has no implicit ownership phi conversion.
9. Forwarding and return operations preserve ownership kind.
10. An owned value is never cloned implicitly.
11. Foreign calls with nontrivial operands or results declare explicit argument and result ownership; an absent or unknown foreign contract fails closed.
12. Every ownership operation references an existing MIR block, instruction, value/place, and TypeId of the same function.

Stable diagnostic codes identify at least double consume, owned leak, missing base, base destroyed during borrow, borrow escape, unowned-to-owned transfer, join mismatch, implicit clone, missing FFI ownership, and ownership metadata mismatch.

## Borrow endpoints

`begin_borrow` is emitted at the MIR operation that produces the guaranteed value. `end_borrow` is placed at the last semantic use on every CFG path. A borrow unused after creation ends immediately. Borrowed parameters use a caller-owned boundary base and end at each function exit.

Optimizer liveness may shorten a borrow only by rebuilding and re-verifying ownership SSA. It may not preserve a stale ownership program after changing uses or control flow.

## Compatibility and migration

MIR v3 artifacts are rejected by the v4 reader. Producers must rebuild from RIR v6. There is no compatibility shim and no mixed v3/v4 runtime path.

The clean rollback is a git revert of the implementation commits, restoring MIR v3 production and its reader. Persisted v4 artifacts must then be rebuilt; no down-converter is provided.

## Rejected alternatives

### Keep ownership only in the frontend

Rejected because deserialized and optimized MIR would remain outside the proof boundary.

### Infer ownership only in the C backend

Rejected because correctness would depend on one target backend and mutations could survive until native execution.

### Store only one ownership string per instruction

Rejected because strings do not model storage state, borrow endpoints, path balance, or join compatibility.

### Treat guaranteed and unowned as one kind

Rejected because a guaranteed value has a base-liveness proof while an unowned pointer does not. Conflating them would either reject valid borrows or authorize unsafe foreign retention.

### Retain MIR v3 and add optional fields

Rejected because ownership metadata changes the serialized validity contract. Optional ownership would preserve an unsafe compatibility path.

## Evidence required for implementation

Acceptance requires:

- positive whole-value move/copy/borrow/store/drop CFG cases;
- mutations removing a destroy, duplicating a consume, ending a borrow early, destroying a base, changing join ownership, changing a borrowed argument, and erasing ownership metadata;
- construction, canonical JSON round-trip, optimizer-boundary, and deserialization rejection tests;
- existing production and tooling suites;
- GCC and Clang native execution plus ASan/UBSan/LSan ownership corpus coverage;
- deterministic HIR/RIR/MIR/C/binary artifacts;
- compile-time and peak-RSS comparison against the MIR v3 baseline.

## Deferred work

Field-sensitive partial moves, abstract drop classification, operation footprints, transfer/share properties, the standalone checker command, async execution, Task IR, Parallel IR, GPU, WASM, LLVM, arbitrary unwind, cycle collection, user-generic TypeId v2, and new surface syntax remain separate milestones under issue #101.
