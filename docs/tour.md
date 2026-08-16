# Language tour

A Merlo module starts with a qualified module name. Imports come before
declarations. Surface 0.2 omits facts that the elaborator can prove:

```merlo
module app.users

User:
    name: Text
    nickname: Text?
    active: Bool

display_name(user) = user.nickname or user.name
active_names(users) = users.where(.active).map(display_name)
```

A capitalized declaration with typed fields is a record. `name(args) = expr`
is an expression-bodied function; `name(args):` starts a statement body. The
last expression is the result. Local bindings omit `let`, `var`, and their type:
the compiler counts assignments across the whole function and materializes the
canonical form.

Function contracts are pure Boolean clauses at the start of a statement body:

```merlo
withdraw(balance: UInt64, amount: UInt64) -> UInt64:
    require amount <= balance
    ensure result <= balance
    balance - amount
```

`require` is checked on entry. `ensure` is checked on every return and may refer
to the returned value as `result`. Clauses cannot be nested, follow executable
statements, or perform effects. A failed native check terminates with a
`MerloContractViolation` diagnostic.

Records use the same pure Boolean model for data invariants:

```merlo
Positive:
    value: UInt64
    invariant value > 0
```

Every `Positive(...)` construction checks the invariant. Ordinary records are
immutable; typed machines and transition invariants are not part of this alpha.

Use a bare `?` to retain an intentionally incomplete, exactly typed expression:

```merlo
let discount: UInt64 = ?
```

The semantic model records the expected type and visible scope. `merlo check`
can expose that context; `merlo build` rejects the hole instead of inventing a
value. This is distinct from postfix `operation()?` result propagation.

`or` is boolean OR only for `Bool`; for `Option[T] or T` it is a strict typed
fallback. Other truthiness is rejected. `.field` is an implicit callable only
inside `where`, `map`, and `count`; it cannot capture locals or appear at an
arbitrary call site.

An explicitly typed closure may capture a checked immutable or owned value:

```merlo
fn greater_than(limit: UInt64) -> Fn[UInt64,Bool]:
    value => value > limit
```

The compiler materializes and drops its typed environment. Escaping borrowed,
mutable, resource, and arbitrary shared captures are rejected.

Host calls make an inferred declaration a `task`. Effects and capabilities
propagate through private calls to a fixed point, while `?` adds its typed error
to a closed error row. Ambiguous inference is rejected rather than guessed.
`merlo expand` exposes the explicit canonical facts.
