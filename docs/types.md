# Types, scalars, and values

The alpha has these scalar descriptors:

| Type | Role |
| --- | --- |
| `Unit` | no value |
| `Bool` | boolean |
| `Byte` | one byte |
| `UInt64` / `Int64` | unsigned/signed 64-bit integer |
| `Float32` / `Float64` | IEEE floating-point values |

`Int`, `UInt`, and `Float` are accepted aliases for `Int64`, `UInt64`, and
`Float64` in the concise surface. Narrowing and arithmetic checks are explicit;
checked overflow produces a diagnostic rather than silently changing the
source contract. Wrapping operations are distinct operations.

Structured values include:

- `Text`, `Bytes`, and `Path` values;
- borrowed `TextView` and `BytesView` views;
- `Array[T, N]`, `Slice[T]`, `Vec[T]`, and `Map[Text, UInt64]`;
- user `record` and `enum` types;
- `Option[T]` (`None`/`Some`) and `Result[T, E]` (`Ok`/`Err`);
- `Box[T]` for indirection where recursive layout needs it.

Fixed arrays carry their length. `match` over sum values must be exhaustive.

`Vec[T]`, `Array[T, N]`, `Slice[T]`, `Borrow[Vec[T]]`, `Bytes`,
`BytesView`, `Text`, and `TextView` share the General sequential collection
protocol. They support bounds-checked indexing, `for` iteration, and the
`where`, `map`, and `count` operations. `where` and `map` return `Vec`; `count`
returns `UInt64`. Text collections expose their UTF-8 storage as `Byte`
elements. Fixed array lengths remain compile-time constants; other lengths
come from the collection view.
Eligible direct `where`/`map`/`count` chains over copy scalars fuse into one
native loop. A terminal `count` allocates no intermediate vectors; a terminal
`where` or `map` materializes only its final `Vec`.

Casts, indexing, and result propagation remain visible in the checked semantic
pipeline. The alpha does not provide a dynamic `Any` escape hatch in concise
application elaboration.

Surface 0.2 infers private parameters, returns, and locals from literals,
operators, record fields, constructors, calls, collection elements, branches,
patterns, and tail expressions. Conflicting constraints are errors; unresolved
constraints are errors. Recursive call groups require at least one explicit
boundary. Exported interfaces become explicit, content-addressed contracts once
locked.

`T?` is the human spelling of `Option[T]`. `option or fallback` is accepted only
when the left side is `Option[T]` and the right side is `T`; `Bool or Bool`
remains boolean. Text, integers, records, and collections do not have implicit
truthiness.
