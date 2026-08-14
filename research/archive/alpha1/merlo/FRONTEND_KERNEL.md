# Meldra Stage 0.4 — Closed Semantic Frontend

Дата: 2026-08-10  
Статус: frozen research contract реализован; 13/13 generated gates `SUPPORTED`; не Language Alpha

## 1. Проверяемая гипотеза

Frontend Kernel проверяет, даёт ли закрытый binder вместе с package interfaces, typed effects, scoped capabilities и ChangeIR более точный и локальный программный мир, чем сильный binder существующего Python-кода.

Два обязательных arm:

1. Meldra source → lossless CST → closed binder → typed HIR → effect/capability checker → canonical CoreIR → reference evaluator.
2. Те же логические программы в Python → current Meldra analyzer и отдельный structural type-aware Python binder.

Все arm сравниваются на одном manifest логических ссылок. Разные знаменатели запрещены.

## 2. Источник истины

Stage 0.4 использует hybrid policy:

- UTF-8 `.meldra` text является переносимым build input;
- Semantic World хранит persistent identity, revisions, history и evidence;
- компиляция корректности не зависит от исторического `SymbolId`;
- ChangeIR сохраняет `SymbolId` точно;
- unchanged declaration может быть привязана к прежнему ID только по exact persistent anchor;
- внешний rename, move или body edit не наследует ID молча;
- probable и ambiguous соответствия получают новый `SymbolId` и review obligation.

Скрытые ID в source не добавляются.

## 3. Идентичности

Frontend различает пять ключей:

- `SyntaxNodeId`: content-addressed узел внутри одного lossless CST; включает source digest и byte span.
- `BindingId`: определение или local binding в конкретной компиляции.
- `SymbolId`: историческая identity semantic symbol; сохраняется только через explicit provenance или exact persistent anchor.
- `RevisionId`: hash полного семантического состояния symbol, без trivia и source position.
- `InterfaceRevisionId`: hash экспортируемого package contract.

Package также имеет `ImplementationRevisionId`: hash всех symbols и exact references package.

## 4. Scope rules

1. Один source file задаёт один module.
2. `package a.b` без отдельного `module` означает package `a`, module `b`.
3. `package a` + `module b.c` означает package `a`, module `b.c`.
4. Module scope содержит top-level declarations и explicit imports.
5. Function/task scope содержит parameters и последовательные `let` bindings.
6. `if` branches и match arms создают вложенные scopes.
7. Inner local binding может shadow outer local/import/global binding.
8. Две декларации или два local bindings в одном scope запрещены.
9. Local binding видим только после своей декларации.
10. Module declarations не зависят от порядка в файле.

## 5. Name resolution

Binder обязан получить ровно один target для каждой валидной внутренней ссылки.

Порядок поиска value name:

1. inner local scopes;
2. parameters;
3. module declarations;
4. explicit import aliases.

Type name ищется только среди builtin types, `record`, `enum`, `newtype` и imported type declarations. Capability type ищется только среди `capability` declarations.

Field access разрешается по nominal receiver type. Enum variant, record field, newtype constructor и capability member имеют отдельные `BindingId` и `SymbolId`.

Результаты binder:

- `Exact`: один внутренний target;
- `Foreign`: только явно объявленная foreign/effect boundary;
- compile error: target отсутствует, скрыт или неоднозначен.

`Unknown internal` в успешном HIR не существует. Wildcard imports, implicit globals, generated members, reflection и runtime member injection не входят в kernel.

## 6. Visibility

1. Declaration private по умолчанию.
2. Только имена из `export` доступны другому module/package.
3. Members экспортированного record/enum/capability входят в его interface contract и получают semantic member IDs.
4. Private symbol нельзя импортировать.
5. Private symbol нельзя переместить через package boundary без явного public contract change.
6. Одинаковые имена в разных modules допустимы; implicit cross-module lookup запрещён.

## 7. Package interfaces и hashing

`InterfaceRevisionId` вычисляется из отсортированного canonical списка экспортов:

- module и public name;
- declaration kind;
- nominal type contract;
- public members;
- task effect set;
- required capability types;
- revisions экспортированных type dependencies.

Implementation body, trivia, file path, source span, local names и private symbols в interface hash не входят.

`ImplementationRevisionId` включает canonical semantic state всех symbols и exact references package.

Обязательные переходы:

- private/body edit: implementation меняется, interface не меняется, downstream invalidation = 0;
- private rename: impact остаётся package-local;
- public signature/member/effect widening: interface меняется, инвалидируются exact consumers;
- formatting/comment edit: source digest меняется, semantic symbol revisions и package revisions не меняются.

## 8. Nominal type rules

Kernel types:

- builtin: `Int`, `Text`, `Bool`, `Unit`;
- nominal: `record`, `enum`, `newtype`;
- capability parameter: `cap Name`;
- function/task parameter и return types обязаны быть явными.

Generics, subtyping, implicit conversions, traits, overloads и type inference между declarations отсутствуют.

Rules:

- literal types фиксированы;
- `let` без annotation получает тип expression;
- `let` с annotation требует exact type equality;
- function/task call проверяет arity, named arguments и exact argument types;
- record construction требует все и только объявленные fields;
- field access разрешён только на соответствующем record;
- newtype не совместим с underlying type без `.new`;
- `if` condition имеет `Bool`, обе ветви возвращают один тип;
- `match` по enum обязан быть exhaustive, без duplicate/unknown variants;
- последний executable expression body задаёт return value; его тип равен declared return type.

## 9. Effects

Effect — закрытое qualified имя действия, например `payments.charge`.

- `fn` всегда имеет пустой effect set;
- `fn` не вызывает `task` или capability member;
- `task` перечисляет allowed effects через `uses`;
- фактические effects вызванных tasks/capability members обязаны удовлетворять `effects(callee) ⊆ declared_effects(caller)`;
- widening public task effects меняет package interface;
- dynamic lookup возможен только как явно объявленный effect boundary и не входит в Stage 0.4 syntax.

Algebraic handlers и row polymorphism не реализуются.

## 10. Capabilities

Capability отвечает за authority, а effect — за поведение.

Kernel использует declaration:

```text
capability Payments:
    charge(amount: Int) -> Int uses payments.charge
```

Parameter `payments: cap Payments` материализует authority только для members `Payments`.

Проверки:

- каждый declared/required effect task покрыт capability environment;
- вызов capability member без соответствующего `cap` parameter запрещён;
- добавление нового effect/capability через ChangeIR блокируется до explicit materialization;
- capability sets сравниваются как закрытые множества;
- escalation является compile/change error до evaluator и tests.

## 11. Surface syntax

Поддерживаются:

```text
package  module  use  export
record   enum    newtype  capability
value    fn      task
let      if      else     match
call     field access
```

Синтаксис indentation-based. Lexer сохраняет каждую byte-последовательность whitespace, comment и newline. CST roundtrip не форматирует source.

Не поддерживаются:

```text
generics traits inheritance async exceptions macros reflection
overloading operator customization flow machine scheduler package registry
JIT LLVM WASM UI GPU mobile production runtime
```

## 12. Canonical lowering

Frontend HIR schema имеет отдельную версию. Lowering выдаёт frozen CoreIR schema v1:

- record/enum/newtype → Core `interface` с canonical member contract;
- capability → Core `capability`;
- value/fn/task → соответствующие Core declarations;
- semantic members получают generated Core declarations и stable IDs;
- local bindings не становятся global Core symbols;
- source spans, SyntaxNodeId, BindingId и trivia не попадают в Core semantic hashes;
- все internal Core references содержат direct `target_id`;
- packages, modules, declarations, imports, exports и references сортируются canonical.

Одинаковый typed program обязан давать byte-identical `CoreProgram.to_json()`.

## 13. Reference evaluator

Evaluator нужен только как semantic oracle benchmark-программ.

Поддерживаются literals, locals, values, calls, record/newtype construction, field access, arithmetic/comparison, `if`, exhaustive `match`, function/task calls и capability handlers.

Результат содержит:

- canonical value;
- ordered effect trace;
- executed SymbolIds.

Evaluator не предоставляет filesystem, network, scheduler, async, retries или deployment runtime.

## 14. Support Profile P0

Профиль фиксируется до Stage 0.4 benchmark.

Included Python baseline constructs:

- module-level classes/functions/constants;
- explicit absolute imports и aliases;
- unique declaration IDs;
- lexical locals, parameters и nested scopes;
- explicit nominal annotations;
- annotated class fields и enum-style members;
- statically typed field access;
- source-product files.

Excluded:

- wildcard imports/exports;
- dynamic decorators и metaclasses;
- monkey patching;
- `getattr` с runtime name;
- generated/runtime members;
- tests, examples, docs, tutorials, demos;
- ambiguous duplicate declarations;
- external stubs, plugins и native extension internals.

Профиль не меняется после чтения результатов текущего benchmark run. Выход за профиль считается отдельным denominator, а не false-block внутри P0.

## 15. Stage gates

1. Parser: 100% byte-exact roundtrip поддерживаемого corpus.
2. Binder: 100% valid internal logical references = `Exact`.
3. Binder safety: unknown/ambiguous internal references не компилируются.
4. Types: positive corpus проходит, negative corpus даёт expected diagnostic code.
5. Effects: `fn` effect set всегда пуст; effect subset проверяется.
6. Capabilities: 0 разрешённых escalation в negative corpus.
7. Interfaces: private body edit не меняет interface hash/downstream.
8. Public changes: меняют interface hash и инвалидируют ровно exact consumers.
9. ChangeIR: explicit operations сохраняют SymbolId.
10. External text edit: no silent identity inheritance.
11. Lowering: byte-identical canonical CoreIR.
12. Execution: expected values и effect traces.
13. Comparison: current Python analyzer, strong structural Python binder и Meldra binder используют один logical-reference denominator.

Прохождение generated corpus разрешает только следующий external experiment. Оно само по себе не разрешает Meldra Language Alpha.

## 16. Observed run и решение

Artifact [`../tools/benchmarks/merlo/benchmarks/meldra_stage04_frontend_benchmark.json`](../tools/benchmarks/merlo/benchmarks/meldra_stage04_frontend_benchmark.json) получен полным run на 40 paired programs:

| Gate | Результат |
|---|---:|
| Parser byte-exact roundtrip | 160/160 |
| Closed internal binding | 1 920/1 920 |
| Unknown internal rejection | 40/40 |
| Positive/negative nominal typing | 400/400 |
| Pure/undeclared effect rejection | 80/80 |
| Capability escalation blocking | 80/80 |
| Private interface stability | 40/40 |
| Public exact invalidation | 40/40 |
| ChangeIR provenance/collision guard | 240/240 |
| External edit identity noninheritance | 40/40 |
| Deterministic lowering | 1/1 |
| Reference evaluation | 80/80 |
| Equal baseline denominator | 1 920/1 920 |

Дополнительно: 200/200 semantic changes applied; 360/360 negative cases дали preregistered diagnostic; support profile остался byte-identical.

Сравнение не показало преимущества языка над сильным resolver: structural type-aware Python binder и Meldra binder оба дали 1 920/1 920 `Exact`. Результат current Python analyzer 1 240/1 920 не используется как языковой baseline.

Decision: `NO_GO_LANGUAGE_ALPHA`. Единственный разрешённый следующий этап: `EXTERNAL_STAGE04_VALIDATION` на независимом held-out corpus с human adjudication и одинаковыми acceptance tests. До него запрещены расширение surface, `flow`, `machine`, большой runtime и package ecosystem.
