# Meldra Language Research

Дата: 2026-08-10  
Статус: Stage 0.4 Closed Semantic Frontend реализован и прошёл generated gates; Meldra Language Alpha не разрешён

## 1. Вывод

Идея языка жизнеспособна только в более узкой и строгой формулировке:

> Meldra — язык с закрытым семантическим миром, где имена, вызовы, эффекты, изменения состояния и эволюция программы представлены типизированными сущностями, а исходный текст является одной из проекций программы.

Необычность не в коротком синтаксисе. Главная ставка — убрать угадывание из разработки:

- каждая ссылка либо разрешена точно, либо явно проходит через динамическую границу;
- внешний эффект требует и типа эффекта, и capability с конкретной областью полномочий;
- долгий процесс представлен как типизированный граф `flow`;
- изменяемая идентичность существует только в `machine`;
- логическая сущность и конкретная ревизия сущности имеют разные идентификаторы;
- изменение программы — значение `Change`, а не неограниченный текстовый patch;
- проверки и доказательства привязаны к тем ревизиям, для которых они были получены;
- ИИ получает минимальный семантический контекст и право на конкретные операции, а не весь репозиторий и shell.

Stage 0.2 подтверждает, что такой sidecar можно построить поверх Python, но одновременно показывает предел этого подхода. На 20 внешних Python-проектах точная привязка покрыла только 90 882 из 391 227 ссылок (23,23%), а пригодная для консервативных Rename/Move привязка — 92 975 (23,77%). Неопределённость создаёт прежде всего не reflection: 70,23% неопределённых ссылок относятся к обычным именам, 14,64% — к атрибутам, 11,14% — к импортам. Значит, главный выигрыш нового языка должен прийти от закрытого name binding и типизированных package interfaces, а не от одного запрета `eval`.

## 2. Что результаты меняют в исходной идее

### Оставить

1. **Semantic Program Graph.** Компилятор должен знать сущности, ссылки, вызовы, типы, эффекты, потоки данных и публичные границы.
2. **ChangeIR.** Rename, Move и ChangeSignature должны быть операциями над идентичностями.
3. **Task Capsule.** Контекст для человека и ИИ должен собираться из графа задачи.
4. **Obligation Graph.** Изменение порождает явные обязательства, которые можно закрыть или доказанно блокировать.
5. **Revision-bound Evidence.** Результат теста или анализа действителен только для конкретных зависимостей и окружения.
6. **Uncertainty as data.** `Unknown`, `Dynamic`, `Observed` и `Ambiguous` нельзя маскировать под `Exact`.
7. **Транзакционное применение.** Сначала preview и валидация, затем атомарная materialization.

### Переделать

1. **Identity recovery.** Эвристическое восстановление идентичности по похожести непригодно для trusted path. В ручной проверке реального commit Pluggy четыре перенесённые сущности были распознаны правильно, но существующий `list_plugin_distinfo` был ошибочно отождествлён с новым `list_plugin_distributions`. Логическая идентичность должна сохраняться явной операцией, а не догадкой.
2. **Entity key.** Qualified name недостаточен: Python overloads, property accessors и повторные декларации дали 28 исключённых benchmark-задач из-за неоднозначных ID. У каждой декларации нужен собственный `SymbolId`; overload set и getter/setter — отдельные группы и роли.
3. **Incrementality.** Консервативное транзитивное замыкание оказалось почти полным пересканированием: изменение одной сущности затронуло 72/73 файлов, 887/1162 сущностей и 100% ссылок/вызовов в пилоте. Нужны мелкозернистые факты и инкрементальные запросы, а не один широкий dependency graph.
4. **Runtime evidence.** Наблюдение двух runtime targets не доказывает отсутствие третьего. `Observed` может сузить приоритет проверки, но становится `Exact` только при доказанной закрытости множества.
5. **Source preservation.** AST regeneration неприемлема. Комментарии, layout, quoting и BOM должны сохраняться lossless syntax tree либо точечными source edits.

### Отложить до измерений

- автономный multi-agent scheduler;
- database-only codebase без обычной source projection;
- compile-time вызовы ИИ;
- web/UI/GPU/mobile facets;
- автоматический выбор реализации по недоказанным performance claims.

## 3. Семантическое ядро

Поверхностный язык имеет семь основных деклараций:

| Конструкция | Назначение | Семантика |
|---|---|---|
| `record`, `enum` | значения | неизменяемые данные и алгебраические типы |
| `fn` | вычисление | чистая функция без внешних эффектов |
| `task` | действие | типизированное вычисление с effect row и capabilities |
| `flow` | процесс | сохраняемый типизированный граф шагов |
| `machine` | идентичность во времени | конечный автомат с проверяемыми transitions |
| `spec` | намерение | контракт, который могут реализовать несколько `impl` |
| `facet` | внешняя среда | явный адаптер к web, DB, OS, GPU или foreign code |

Минимальный core calculus не обязан иметь все эти формы. Поверхность может elaboration-компилироваться в:

```text
Value       immutable data
Computation return Value | perform Effect Operation Value | bind Computation
Flow        typed graph<Node, Port, Edge, Policy>
Machine     (State, Event) -> (State, List<Command>)
Revision    CoreTerm + dependency revisions + compiler semantics version
```

`flow` и `machine` должны оставаться различимыми. Flow отвечает на вопрос «какие шаги и зависимости образуют процесс?». Machine отвечает на вопрос «какие состояния и переходы допустимы для одной долгоживущей идентичности?».

## 4. Типы и эффекты

### 4.1 Значения

- immutable по умолчанию;
- `Option<T>` вместо `null`;
- `Result<T, E>` либо объявленный error row вместо скрытых исключений;
- исчерпывающий `match`;
- номинальная идентичность для публичных и сохраняемых типов;
- локальные anonymous records могут быть структурными;
- generic types и traits/interfaces без неявного глобального monkey patching.

Публичные сигнатуры пишутся явно. Внутри функции типы локальных значений выводятся. Это сохраняет простоту, но делает API и semantic graph стабильными.

### 4.2 Effect row и capability — разные вещи

Effect row отвечает: **что может произойти?**  
Capability отвечает: **к какому ресурсу и с какой властью разрешено обратиться?**

```meldra
capability OrdersDb:
  read(order: OrderId) -> Order
  write(order: Order) -> Unit

capability PaymentGateway:
  charge(token: CardToken, amount: Money, key: IdempotencyKey)
    -> Receipt ! [Declined, Timeout]

task checkout(order_id: OrderId, card: CardToken)
  uses db: OrdersDb[tenant = current_tenant]
  uses pay: PaymentGateway[merchant = shop]
  -> Receipt ! [NotFound, Declined, Timeout]:
  let order <- db.read(order_id)
  let receipt <- pay.charge(card, order.total, key: order.id)
  db.write(order with status = Paid(receipt.id))
  receipt
```

Тип функции содержит эффекты; capability value ограничивает authority. Некоторые ресурсы могут быть affine/linear — например transaction handle или secret lease, — но линейность не должна распространяться на все значения.

Handlers подменяют реализацию эффекта в тесте или симуляции без глобальных mock-объектов. Эта часть опирается на проверенную модель algebraic effects и row-polymorphic effects, а не на новый ad-hoc механизм.

### 4.3 Динамика

В native Meldra-коде не существует неявного `Unknown`. Динамическая операция выглядит как явная capability:

```meldra
task call_plugin(name: PluginName, input: Bytes)
  uses plugins: DynamicPlugins[allow = lock.plugins]
  -> Bytes ! [PluginMissing, ForeignFailure]
```

Она остаётся `Dynamic`, пока lock/interface не задаёт закрытое множество реализаций. Runtime trace добавляет `Observed`, но не превращает открытый мир в `Exact`.

## 5. Flow

`flow` — не функция с красивым синтаксисом и не библиотечный workflow. Это сохраняемый typed DAG, видимый type checker, runtime и ChangeIR.

```meldra
flow Fulfill(order: Order) -> Shipment:
  validate = check(order)
  payment  = charge(validate.payment) after validate
  stock    = reserve(validate.items) after validate
  label    = make_label(order.address) after [payment, stock]
  shipment = dispatch(label) after label

  policy payment:
    retry exponential(max: 3) when Timeout
    idempotency order.id

  compensate stock with release(stock.reservation)
  return shipment
```

Инварианты:

- каждый edge соединяет совместимые typed ports;
- цикл возможен только через явный `loop` с bound или progress invariant;
- retry допустим лишь при объявленной idempotency/retry policy;
- compensation — отдельный эффект с собственной возможностью отказа;
- compiler не обещает «exactly once» в распределённой системе. Он может доказать локальный exactly-once transition или сгенерировать deduplication protocol при наличии durable idempotency key;
- версия графа и состояние экземпляра versioned отдельно; миграция активного flow — явная ChangeIR-операция.

## 6. Machine

`machine` — единственное место общей изменяемой идентичности. Transition остаётся чистым: он вычисляет новое состояние и команды; эффекты выполняются task runtime, а результат возвращается событием.

```meldra
machine OrderLife(order_id: OrderId):
  state Pending(cart: Cart)
  state Charging(cart: Cart, attempt: PaymentAttempt)
  state Paid(receipt: Receipt)
  state Cancelled(reason: Text)

  event Submit(card: CardToken)
  event Charged(receipt: Receipt)
  event ChargeFailed(reason: PaymentError)
  event Cancel(reason: Text)

  transition Pending(cart) on Submit(card):
    become Charging(cart, PaymentAttempt.new())
    emit Charge(order_id, cart.total, card)

  transition Charging(_, _) on Charged(receipt):
    become Paid(receipt)

  transition Pending(_) on Cancel(reason):
    become Cancelled(reason)
```

Компилятор проверяет:

- исчерпывающие либо явно запрещённые event/state pairs;
- недостижимые состояния;
- invariants переходов;
- совместимость snapshot schema при evolution;
- отсутствие прямой мутации state за пределами transition;
- causal identity команд и событий для replay/deduplication.

## 7. Spec, impl и typed holes

`spec` отделяет намерение от реализации, но не вызывает ИИ внутри доверенной сборки.

```meldra
spec nearest_neighbors(points: Vector, count: Int) -> List<Item>:
  requires count > 0
  ensures result.length <= count
  quality recall >= 0.95
  budget p95 < 20.ms

impl exact_cpu for nearest_neighbors:
  target native
  proof exact_result

impl hnsw_local for nearest_neighbors:
  target native
  evidence benchmark("ann-2026-08")
```

Выбранная реализация фиксируется lock-файлом вместе с revision hash и evidence. Поиск или synthesis кандидата выполняется отдельной командой. Обычная сборка детерминирована и не зависит от модели или сети.

Typed hole — нормальное состояние редактора, но не production artifact:

```meldra
let thumbnail: Image = ?resize
```

Hole имеет ожидаемый тип, эффекты, obligations и editable scope. Профиль `preview` может подключить явно объявленный mock facet. Профиль `production` отвергает reachable holes.

## 8. Идентичность и ревизии

Нужны четыре разных ключа:

```text
SymbolId       логическая сущность, сохраняемая явной evolution operation
RevisionId     hash конкретного typed Core IR и revision зависимостей
DeclarationId  конкретная декларация: overload/accessor/impl/transition
WorldRevision  Merkle root всего согласованного semantic world
```

Правила:

1. Rename/Move не меняет `SymbolId`, но всегда создаёт новый `RevisionId`.
2. Изменение тела при прежнем имени сохраняет `SymbolId`, если tracked declaration anchor однозначен.
3. Новый похожий symbol не наследует старый ID по similarity.
4. Heuristic candidate никогда не materialize автоматически: только `Ambiguous` и ручное/протокольное подтверждение.
5. Getter, setter, overload signatures и generated implementation получают разные `DeclarationId`.
6. Recursive groups hash-ируются канонически как группа, но логические IDs членов сохраняются evolution log.
7. Plain-text import без истории сохраняет identity только для неизменённых content-addressed definitions; остальное получает новые IDs либо требует explicit mapping.

Так совмещаются сильные стороны content-addressed code и эволюционной идентичности. Hash — это версия, а не вечная личность изменяемой сущности.

## 9. Semantic codebase и source projection

Для 0.1 source остаётся переносимым canonical input, а semantic database — детерминированный derived artifact:

```text
lossless source
  -> parser + binder + type/effect checker
  -> typed Core IR
  -> semantic facts / graph indexes
  -> ChangeIR preview
  -> minimal source edits
  -> reparse + equality checks
```

Позже content-addressed store может стать canonical, но только после доказательства:

- round-trip исходников без потерь;
- обычного Git export/import;
- crash-safe journal и process locking;
- incremental rebuild;
- миграции schema хранилища;
- восстановления без специального сервера.

Stage 0.2 уже показал, почему нельзя начинать с «AST — истина»: комментарии, строки, форматирование и file-level bytes являются пользовательскими данными.

## 10. Facets и package system

Многогранность достигается не добавлением всех платформ в core, а typed facets:

```text
Core Language
  + facet web
  + facet cli
  + facet data
  + facet agent
  + target native | wasm
```

Facet определяет notation, typed interfaces, effect model, verifier, simulator и target adapter. Он не может менять правила identity, effects или evidence.

Package публикует:

- typed semantic exports;
- declared capabilities/effects;
- `SymbolId` namespace и immutable revisions;
- public contracts;
- target support;
- migration relations;
- reproducible implementation/evidence hashes.

Foreign package доступен только через adapter. Именно package interfaces должны закрыть большую долю нынешних `import`, `name` и `attribute` uncertainties.

## 11. AI protocol

ИИ не получает право «редактировать проект». Он получает:

```text
Goal
TaskCapsule L0-L3
Allowed ChangeIR operations
Editable SymbolIds / files
Node and dependency budget
Required obligations
Required evidence classes
```

Минимальный protocol:

```text
search(query)
inspect(symbol_id, revision?)
references(symbol_id, certainty?)
impact(change)
request_context(level | reason)
preview(change_set)
apply(change_set, expected_world_revision)
run_evidence(experiment_ids)
submit_evidence(artifacts)
```

Каждый ответ содержит schema version, world revision, certainty и provenance. `apply` использует optimistic concurrency: stale world отвергается. Коммутативность определяется ChangeIR над IDs; порядок некоммутативных изменений сериализуется.

## 12. Что считать простотой

Поверхностный синтаксис может быть коротким:

```meldra
fn normalize(name: Text) -> Text:
  name.trim().lower()

let names = users
  |> map(_.name)
  |> map(normalize)
  |> unique
```

Но язык не должен скрывать то, что меняет контракт:

- effect boundary;
- authority scope;
- failure modes;
- shared state;
- durability/retry;
- public API;
- dynamic dispatch;
- production hole;
- evidence level.

Формула: **easy by default, explicit at semantic boundaries**.

## 13. Наблюдаемый Core Semantics Lab 0.3

Реализован не пользовательский язык, а минимальная executable модель [`core_semantics.py`](core_semantics.py):

- `Package → Module → Symbol` с explicit imports/exports и versioned package interface;
- отдельные стабильный `SymbolId` и content-addressed `RevisionId`;
- `value`, `function`, `task`; mutable shared state отсутствует;
- внутренние ссылки обязаны связываться ровно с одним local/imported symbol;
- foreign imports явны и не входят во внутренний closed world;
- effects и scoped capabilities разделены;
- capability escalation блокирует change до materialization;
- Rename, Move, ChangeSignature, implementation edit и effect restriction применяются транзакционно;
- `flow`, `machine`, parser, runtime, scheduler, package registry и target backends намеренно отсутствуют.

Benchmark [`../benchmarks/meldra_core_benchmark.json`](../benchmarks/meldra_core_benchmark.json) сравнивает три пары эквивалентных Python/Core programs с одинаковыми declared public contracts:

| Метрика | Python sidecar | CoreIR |
|---|---:|---:|
| Internal Exact | 4/5 | 3/3 |
| Unknown internal | 0 | 0 |
| Explicit foreign | 3/8 total refs | 3/6 total refs |
| Safe Rename/Move/ChangeSignature | 6/9 | 9/9 |
| Identity continuity | 6/9 | 9/9 |
| Serialized context | 9 359 bytes | 4 436 bytes |
| Private implementation affected packages | 3/6 | 3/6 |
| Private interface changes | 0 | 0 |

Два static-control fixtures дали одинаковые 3/3 applied changes и 3/3 identity continuity в обоих arms. Преимущество Core появилось только на fixed-string `getattr`: Python sidecar консервативно заблокировал 3/3 changes, Core со структурной ссылкой применил 3/3. Это подтверждает ценность closed binding в малом синтетическом мире, но не доказывает новый язык. Agent success, tokens и tool calls остались `UNMEASURED`.

## 14. Stage 0.4: frontend lab, не большой runtime

Следующий разрешённый vertical slice:

1. lossless source parser для `package`, `module`, explicit `import`/`export`, `record`, `enum`, `fn`, `task`, `let`, `match`;
2. type checker с nominal types, exhaustive enum match и запретом unresolved internal names;
3. effect rows и capability values без ambient effects;
4. deterministic lowering в существующий CoreIR;
5. один простой deterministic execution target для тех же benchmark programs — reference interpreter или один backend, не native + WebAssembly одновременно;
6. source-preserving Rename/Move/ChangeSignature round trip;
7. CLI `check`, `run`, `inspect`, `change`, `preview`, `apply`;
8. declared-contract equivalence и reproducible output на тех же Python/Core fixtures.

`flow` и `machine` добавляются только после этого slice и отдельной формальной модели. UI/GPU/mobile, package manager, distributed runtime и autonomous agent scheduler не входят в Stage 0.4.

## 15. Наблюдаемый Stage 0.4 Closed Semantic Frontend

Реализован рабочий frontend path:

```text
.meldra source
  → lossless UTF-8 lexer/CST
  → closed binder
  → typed HIR
  → effect/capability checker
  → canonical CoreIR v1
  → reference evaluator
```

Canonical schema заморожена в [`core_ir_schema_v1.json`](core_ir_schema_v1.json), а правила resolution, visibility, package interfaces, types, effects, capabilities, hashing и identity — в [`FRONTEND_KERNEL.md`](FRONTEND_KERNEL.md). CLI предоставляет `frontend-check`, `frontend-ir`, `frontend-run` и `frontend-bench`.

Полный preregistered generated run на 40 paired programs дал:

| Проверка | Результат |
|---|---:|
| Lossless source roundtrip | 160/160 |
| Meldra exact logical references | 1 920/1 920 |
| Unknown internal binding rejection | 40/40 |
| Positive + negative nominal typing | 400/400 |
| Pure/undeclared effect rejection | 80/80 |
| Capability escalation blocked | 80/80 |
| Private interface stability | 40/40 |
| Public exact invalidation | 40/40 |
| ChangeIR identity + collision guards | 240/240 |
| External edit receives new identity | 40/40 |
| Deterministic CoreIR lowering | 1/1 |
| Expected values/effect traces | 80/80 |

Benchmark применил 200/200 semantic changes и получил ожидаемый diagnostic code на 360/360 negative programs. Полный artifact: [`../benchmarks/meldra_stage04_frontend_benchmark.json`](../benchmarks/meldra_stage04_frontend_benchmark.json).

Ключевой контроль не подтвердил языковое преимущество: strong structural type-aware Python binder и Meldra closed binder оба разрешили **1 920/1 920** одинаковых logical references. Current Python sidecar разрешил 1 240/1 920, но этот разрыв показывает ограничение текущего analyzer, а не преимущество нового языка. Поэтому результат frontend kernel — положительный инженерный результат, но не доказательство Meldra Language Alpha.

Ограничения остаются load-bearing:

- corpus generated и не является independently adjudicated human ground truth;
- same-model agent A/B, tokens, tool calls и accepted-task success не измерены;
- source-preserving materialization ChangeIR обратно в `.meldra` CST не реализована;
- нет incremental compiler, package registry, production runtime, `flow` и `machine`;
- reference evaluator проверяет семантику fixtures, а не performance или production execution.

Решение: **NO-GO для Meldra Language Alpha**. Разрешён только следующий шаг `EXTERNAL_STAGE04_VALIDATION`: независимый held-out source-product corpus, human labels и acceptance tests на одинаковых задачах и знаменателях. Расширение surface/runtime до получения этих данных запрещено.


## 16. Meldra 0.5 evidence gate

Stage 0.4 заменяет прежний provisional verdict: frontend kernel gates пройдены, но полный язык остаётся **NO-GO**; разрешён только независимый `EXTERNAL_STAGE04_VALIDATION`.

| Гипотеза | Требование | Текущее состояние |
|---|---|---|
| Executable frontend | parser + type checker + deterministic compiler выполняют Core fixtures с contract equivalence | SUPPORTED на generated corpus: 160/160 roundtrip, 400/400 typing, 80/80 execution |
| Closed binding | 100% native internal refs `Exact`, `Unknown = 0` на preregistered held-out corpus | PROVISIONAL: 1 920/1 920 generated; external held-out отсутствует |
| Identity | changed-only precision >99,5%, recall >95%; минимум 600 independently adjudicated changed links | OPEN: 240/240 generated checks, 0/600 independently adjudicated |
| Safety | false-safe 0 на independent human-labelled source-product corpus; infrastructure входит в denominator | PARTIAL: 3/3 Pluggy patches сохранили 141 tests |
| Automation | false-block <3% отдельно для Rename, Move и ChangeSignature | OPEN: planner-ready 12/12 non-human cases; Typer selection timeout |
| Context | same-model A/B success не хуже более чем на 2 п.п.; median input ниже минимум на 50% | PARTIAL: bytes 47,40%; success не измерен |
| Runtime evidence | `Observed` никогда не повышается до `Exact` без closed-world proof | mechanism pass, broad evidence absent |
| Incrementality | small edit p95 update <10% full rebuild без stale facts | OPEN |
| Transactions | kill-at-every-write-point recovery, process locking, deterministic replay | OPEN |
| Composition | реальные concurrent changes с conflict precision/recall | simulation-only |
| Language value | Meldra против Python/TypeScript: same model, tasks, tools, budgets, acceptance tests | BLOCKED: provider отсутствует |

Для односторонней 95% нижней границы precision >99,5% недостаточно нескольких красивых commits: при нуле ошибок требуется минимум 598 changed predictions. Для двустороннего 95% интервала — 736. Поэтому общая точность на десятках тысяч неизменённых symbols не является доказательством identity evolution.

## 17. Исследовательские основания

- Source Code Algebra формализует операции, composition, nullipotency и commutativity; опубликованный SCAS probe предварительный и не заменяет широкий benchmark: https://arxiv.org/abs/2607.18742
- Koka показывает практический row-polymorphic effect system и handlers: https://arxiv.org/abs/1406.2061
- Hazel показывает статическую и динамическую семантику incomplete programs с typed holes: https://arxiv.org/abs/1805.00155
- CodeQL подтверждает практичность представления code-as-data и Datalog-подобных semantic queries: https://codeql.github.com/docs/codeql-overview/about-codeql/
- Unison отделяет имена от content-addressed definitions; Meldra дополнительно нуждается в явной identity through evolution: https://www.unison-lang.org/docs/the-big-idea/
- Differential Dataflow даёт основание для инкрементального поддержания рекурсивных graph queries: https://www.microsoft.com/en-us/research/publication/differential-dataflow/

Ни одна из этих работ по отдельности не доказывает проект Meldra. Новая ставка — их совместная организация вокруг проверяемой эволюции программы и ограниченного AI protocol.
