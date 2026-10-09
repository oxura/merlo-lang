# Ownership, borrowing, and errors

Merlo tracks ownership at typed boundaries. Text, bytes, builders, and vector
values are owned values; `TextView` and `BytesView` are borrowed views. A value
may move into a constructor or return as an owned value. A borrow may be passed
or returned only while the source remains valid. Mutable borrows exclude a
conflicting mutation; a mutation during a live shared borrow is rejected.

The alpha does not require ordinary lifetime annotations or manual memory
operations. Composite records are dropped fieldwise, tagged enums drop by their
active variant, and boxed indirection permits recursive layouts. The current
runtime has no cycle collector, so programs must not rely on collecting cycles.
Capturing closures may retain immutable scalar values and owned values in a
typed environment. Borrowed captures may not escape, mutable captures and host
resources are rejected, and arbitrary shared closure environments are not part
of the alpha contract.

Compiler-managed owning bindings may be refreshed inside `while` and `for`
loops. Before replacing a value, the native backend evaluates the replacement
while the old owner is still valid, then releases the old owner and moves in the
new one. A borrowed owning collection element is cloned rather than transferred
out of its collection. Re-executed owning expression temporaries follow the same
replacement rule. Vacant enum and nested aggregate slots use typed moved/empty
states, and final cleanup applies to the actual return path, including owned
parameters. These rules do not allow moving an outer owner across a loop
backedge, mutating a live borrowed backing value, or escaping an iteration borrow.

Use typed errors at operation boundaries:

```merlo
enum AppError:
    Missing: Text

fn load(path: Path) -> Result[Text, AppError]:
    uses fs.read
    let bytes = fs.read(path)?
    return Ok(bytes.to_text())
```

`Result` is explicit and exhaustive. Diagnostics use named codes such as
`MissingCapability`, `CapabilityScopeEscape`, `MutationDuringBorrow`, and
`MerloOverflow:*` where the corresponding checked path emits them. Exact text
can include source locations and host details, so consumers should branch on
codes or JSON fields rather than prose.
