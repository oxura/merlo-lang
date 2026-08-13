# Meldra Stage 0.2 — технический отчёт

Дата: 2026-08-10

## Вердикт

Stage 0.2 реализован как **semantic evolution sidecar для существующего Python-кода**. Это не новый язык, не runtime и не попытка преждевременно реализовать `flow`, `machine` или собственную систему эффектов.

Рабочий вертикальный срез теперь выглядит так:

```text
Python source
  -> deterministic ProgramIR
  -> semantic identity/certainty graph
  -> Task Capsule + semantic impact
  -> typed ChangeIR
  -> obligation DAG + revision-bound evidence
  -> capability check
  -> source-exact transactional materialization
  -> rescan + identity verification + Evolution Log
```

Результат достаточен для исследовательского Stage 0.2: система выполняет ограниченные semantic-first изменения, умеет честно блокировать непроверенные случаи и прошла end-to-end проверку на копии текущего проекта. Результат **недостаточен** для заявления, что semantic-first разработка уже доказанно превосходит обычные coding agents на реальных репозиториях.

## Что реализовано

| Подсистема | Файлы | Реализованный контракт |
|---|---|---|
| Универсальный IR | `model.py` | `ProgramIR`, стабильный `Entity ID`, `Semantic Revision Hash`, `Source Hash`, типизированные references/calls/hazards, `ChangePlan`, `SemanticImpact` |
| Python semantic frontend | `analyzer.py` | AST-анализ модулей, функций и классов; межфайловые и локальные imports; aliases/re-exports; shadowing; calls; `__all__`; wildcard, string и dynamic hazards; UTF-8 BOM |
| Identity recovery | `identity.py` | `Exact` только для явного ChangeIR provenance либо неизменного semantic address + revision; `Probable` и `Ambiguous` получают новые IDs и остаются review-only; старый ID возвращается только явным подтверждением |
| Hash model | `HASHING.md` | Разделены file digest, exact entity source hash, conservative semantic revision hash и identity content fingerprint |
| Change algebra | `changes.py` | Детерминированные descriptors, ordering, composition и конфликтность минимального набора операций |
| Evolution engine | `evolution.py` | `RenameSymbol`, module-level `MoveSymbol`, консервативный append-only `ChangeSignature`; exact source edits; preview; inverse metadata; stale-world preconditions; syntax preflight; rollback |
| Obligations | `obligations.py` | Типизированный DAG с root cause, `depends_on`, affected entities/files, required evidence и possible resolutions |
| Evidence | `evidence.py` | Revision/world/relation dependencies, deterministic evidence IDs, validation/rebinding и incremental invalidation |
| Impact | `impact.py` | Direct references, direct/transitive callers, public boundaries, uncertain references, affected files, expected edits и invalidated evidence |
| Context compiler | `context.py` | Детерминированный `TaskCapsule`: цель, target/source, semantic callers/dependencies/boundaries, obligations/evidence, impact и capability scope; recovery fingerprints не дублируются в capsule |
| AI-facing API | `protocol.py` | Provider-neutral Python API: search/inspect/references/callers/dependencies/source/context/preview/validate/apply/obligations/evidence/impact |
| Persistent world | `world.py` | Schema v2, v1 migration, atomic JSON replace, plans/obligations/evidence/identity confirmations, committed/rolled-back Evolution Log |
| CLI | `cli.py`, `__main__.py` | `scan`, `ir`, `inspect`, `identities`, `rename`, `move`, `signature`, `impact`, `context`, `obligations`, `obligation`, `evidence`, `bench`, `experiment` |
| Measurement | `bench.py`, `experiment.py`, `long_horizon.py` | Deterministic Stage 0.2 gate, honest text-baseline proxy and ten-step evolution scenario |

## Изменённые архитектурные решения

1. **Sidecar вместо Core Language.** Главная гипотеза проверяется на живом Python-коде; новый parser/runtime пока добавил бы риск и не дал бы доказательства semantic-first подхода.
2. **Identity отделена от revision.** Сущность сохраняет логическую личность через разрешённую эволюцию, а каждое семантическое состояние имеет отдельный hash. Rename больше не выглядит как delete + create, но изменение не скрывается.
3. **Syntax certainty отделена от semantic certainty.** Успешный `ast.parse` не означает, что bindings, callers или runtime behavior известны. Unknown/ambiguous состояния хранятся в IR и порождают obligations.
4. **ChangeIR вместо прямого patch API.** Намерение, scope, ожидаемые source fragments, impact, inverse и evidence известны до materialization; preview и apply используют один plan.
5. **Source preservation вместо AST regeneration.** AST служит анализу, но edits применяются к точным spans. Это сохраняет комментарии, quoting, layout и BOM и уменьшает unrelated diff.
6. **Capability как semantic policy.** Проверяется не только путь файла: scope включает entities, related callers, categories, dependencies, public API и бюджеты изменения.
7. **Persistent Software World вместо одноразового index.** Identity, obligations, evidence и Evolution Log переживают процесс и пересчитываются относительно revision dependencies.
8. **Conservative refusal вместо optimistic fallback.** Непроверяемый dynamic/ambiguous/collision case блокируется с типизированной причиной; скрытого текстового fallback нет.


## Основные гарантии текущего среза

### Identity

- Rename/Move/ChangeSignature, выполненные через ChangeIR, сохраняют один `Entity ID` через явный provenance hint.
- Внешние изменения без provenance не получают `Exact` по одной эвристике.
- Неоднозначные кандидаты получают новый ID и blocking `AmbiguousIdentity` obligation.
- Ручное подтверждение разрешено только для реально зафиксированной ambiguous predecessor relation; произвольный ID внедрить нельзя.
- Formatting/comments не меняют semantic revision, но меняют exact source/file hashes. Docstrings, decorators, annotations, defaults, signatures и body меняют semantic revision.

### Изменения

- `RenameSymbol` мигрирует определение, exact calls/imports/re-exports и локальные bindings; aliases сохраняются, если их публичное имя не должно меняться.
- `MoveSymbol` работает для module-level symbols, обновляет imports/re-exports/local callers, переносит необходимые dependencies, проверяет cycles и блокирует dependency-name collisions в target module.
- `ChangeSignature` автоматически поддерживает только консервативное добавление параметров. Required parameters требуют явных migration expressions. Variadic, stored function, callback, decorator, dynamic и public-external cases превращаются в obligations. Изменение существующего default/annotation не маскируется под additive change.
- Preview ничего не пишет. Apply проверяет file digests, повторно парсит все изменённые модули, выполняет multi-file transaction, rescans ProgramIR и проверяет сохранение identity.
- Ошибка materialization/rescan откатывает каждый затронутый файл; Evolution Log фиксирует `rolled_back` при возможности сохранить World.

### Uncertainty и capabilities

- Dynamic/wildcard/string/unknown references являются данными IR, а не предупреждениями в строке.
- Blocking obligations дают false-safe предпочтение: неизвестное не называется безопасным.
- `EditCapability` ограничивает target IDs, operation classes, files, related entities, количество edits/files/entities, новые dependencies и public API breaks.
- Scope expansion видим в preview и может быть запрещён независимо от синтаксической корректности patch.

## Измерения

### Детерминированный Meldra Bench

Команда:

```bash
python3 -m meldra bench --compact
```

Корпус: 16 evolution cases + 5 identity cases.

| Метрика | Результат | Сырые значения |
|---|---:|---:|
| Edit Precision | 1.000 | 27 / 27 predicted edits |
| Edit Recall | 1.000 | 27 / 27 expected edits |
| Obligation Precision | 1.000 | 9 / 9 predicted obligations |
| Obligation Recall | 1.000 | 9 / 9 expected obligations |
| Identity Precision | 1.000 | 4 / 4 predicted links |
| Identity Recall | 1.000 | 4 / 4 expected links |
| False Safe Rate | 0.000 | 0 / 8 unsafe cases |
| Unintended Edit Count | 0 | 0 extra edits |
| Transaction Safety | 1.000 | 16 / 16 cases |

Это regression gate, а не внешняя научная валидация. Кейсы написаны вместе с реализацией, размеры выборок малы, а нулевой false-safe результат на восьми unsafe cases не даёт production-уровня статистической уверенности.

### Проверка фундаментальной гипотезы

Команда:

```bash
python3 -m meldra experiment --compact
```

Метод: детерминированный proxy на одних и тех же девяти rename tasks; coding model не вызывался. Baseline намеренно простой и оптимистичный: workspace-wide textual identifier replacement, где число правильных совпадений ограничивается ожидаемым числом edits.

| Метрика | Text baseline | Meldra |
|---|---:|---:|
| Matched / predicted edits | 16 / 21 | 16 / 16 |
| Edit Precision | 0.761905 | 1.000000 |
| Edit Recall | 1.000000 | 1.000000 |
| False-safe cases | 4 / 4 | 0 / 4 |
| False Safe Rate | 1.000000 | 0.000000 |

Подтверждено только на этих fixtures:

- semantic binding не меняет shadowed aliases и посторонние textual occurrences;
- typed uncertainty блокирует четыре unsafe изменения, которые текстовый baseline применяет;
- Meldra создаёт точный edit set на данном наборе.

Не подтверждено:

- успех реального coding model;
- число tool calls и итераций;
- wall-clock/cost/token advantage модели;
- преимущество над LSP/IDE refactoring engines;
- переносимость результатов на чужие репозитории.

### Context economy

Результат намеренно не приукрашен.

- На крошечных experiment fixtures суммарный Task Capsule больше полного source: `21,011` против `612` bytes. На таком масштабе metadata overhead доминирует; экономия контекста не доказана.
- Одноразовое измерение на текущем проекте для `core.cognitive_budget.compute_budget`: `76,093` bytes Task Capsule против `558,760` bytes всего Python source, отношение `13.62%`.
- Token proxy в experiment — только `ceil(UTF-8 bytes / 4)`, не tokenizer output.

Следовательно, selective context уже уменьшает реальный repository payload для одного крупного target, но качество capsule и влияние на агента остаются unmeasured.

### Long-horizon scenario

Команда запускает десять последовательных Rename/Move/ChangeSignature над одной сущностью.

Результат:

- 10 / 10 steps;
- один стабильный `Entity ID`: `ent_b8aa3a10a5398e9d`;
- 11 distinct entity revisions;
- 11 distinct world revisions;
- 10 Evolution Log entries;
- final locator: `app.billing.final_price`;
- 0 failures;
- 4 valid и 35 stale evidence items после последовательной invalidation.

Это подтверждает revision tracking и identity continuity на одном управляемом сценарии. Это не доказывает устойчивость к сотням внешних, конфликтующих или параллельных изменений.

### Реальный проект

Финальный scan текущего репозитория:

- 700 entities;
- 3,092 references, из них 1,251 uncertain;
- 984 call edges;
- 58 semantic hazards;
- 0 scan-time open obligations.

Для `core.cognitive_budget.compute_budget`:

- Rename preview: ready, 5 существующих файлов;
- Move preview в новый `core.budgeting`: ready, 5 существующих файлов + 1 новый;
- additive ChangeSignature preview: ready, только файл определения;
- тот же public rename без `allow_public_api_break`: blocked с `PublicApiCompatibility`.

На изолированной полной копии проекта Rename был реально применён к пяти файлам. После изменения 26 целевых tests прошли. Обратный semantic rename восстановил все Python-файлы byte-for-byte; Evolution Log содержит две committed transactions. Рабочая копия проекта этим smoke test не изменялась.

## Verification

- Full project suite: `216 passed`.
- Meldra-focused suite входит в общий suite и покрывает identity matrices, certainty categories, Rename/Move/ChangeSignature, rollback, source preservation, DAG/evidence/impact, protocol/context, storage migration, benchmark, hypothesis experiment и long horizon.
- End-to-end algebra property: два disjoint semantic renames дают одинаковый World и exact source независимо от порядка.
- Python compilation: `python3 -m compileall -q ...` завершился без ошибок.
- Внутренний dependency graph: 16 Meldra modules, 41 internal edges, 0 cycles.
- Существовавшие Python 3.14 router failures были вызваны тестовым `asyncio.get_event_loop()` вне event loop; тесты переведены на тот же wall-clock источник, который использует cache implementation.

## Ограничения

1. **Python only.** IR задуман универсальным, но доказан только Python frontend.
2. **Нет type checker/runtime proof.** AST syntax и static bindings не доказывают поведение, типы, import-time side effects или внешний API.
3. **Dynamic Python остаётся границей.** Reflection, monkey patching, runtime imports, generated code и opaque frameworks часто дают obligations или остаются вне точного графа.
4. **Identity corpus мал.** Эвристики консервативны, но не проверены на большом ground-truth наборе реальных историй Git.
5. **Операции намеренно узкие.** Move — только module-level; ChangeSignature — append-only; полный executable `Remove/Replace/Wrap/Split/Merge` не реализован.
6. **Нет межпроцессной транзакционной блокировки.** File digests ловят stale snapshot, а rollback восстанавливает локальную transaction, но concurrent writers требуют journal/locking protocol.
7. **World storage пока snapshot JSON.** Schema/versioning, atomic replace и content hashes готовы как переходный слой, но incremental content-addressed object store ещё не реализован.
8. **Evidence adapters ограничены.** Revision invalidation реализована, но test/type/security/benchmark artifacts пока не подключаются автоматически от внешних CI systems.
9. **Task Capsule ещё велик.** Он компактнее полного текущего репозитория для измеренного target, но больше tiny fixtures и не оптимизирован retrieval/model feedback loop.
10. **Benchmark internal.** Нужны независимые held-out repos, реальные изменения и baseline уровня LSP/coding agent.

## Следующий Stage

До Core Language следующий этап должен усиливать именно semantic evolution engine.

1. **Внешний benchmark:** минимум несколько сотен held-out transformations на 20+ Python repos; отдельно минимум 300 unsafe cases, чтобы false-safe измерялся на содержательном знаменателе.
2. **Identity ground truth:** реальные rename/move/body-edit histories, swapped/copy/delete-create cases, precision-first gate и ручная ambiguity review.
3. **Baseline A/B:** сравнить Meldra не только с text replace, но с LSP rename/refactor и одинаковым coding model без semantic sidecar.
4. **Controlled model trial:** только при доступной реальной модели измерить task success, tool calls, iterations, context tokens, wall time, unintended edits и human interventions.
5. **Evidence adapters:** pytest/type-checker/import smoke/security/benchmark evidence с artifact hashes и dependency-specific invalidation.
6. **Transactional hardening:** filesystem journal, inter-process locking, crash recovery и deterministic replay.
7. **Incremental storage:** content-addressed objects/revisions, delta indexes и обновление только затронутых graph partitions.
8. **Context compiler feedback loop:** измерять не размер сам по себе, а достаточность capsule для успешного изменения; удалять поля только по экспериментальным данным.
9. **Расширять ChangeIR по данным benchmark:** следующая executable операция выбирается по частоте реальных blocked tasks, а не по красоте алгебры.

## Решение по Core Language

Core Language по-прежнему запрещён. К нему имеет смысл возвращаться только если внешний эксперимент покажет одновременно:

- устойчиво низкий False Safe Rate на большом unsafe corpus;
- высокие edit/obligation recall без чрезмерного числа блокировок;
- точное identity recovery на реальных histories;
- статистически заметное улучшение model task success или снижение cost/iterations относительно сильного baseline;
- устойчивость long-horizon World к внешним и параллельным изменениям.

До этих доказательств Meldra остаётся тем, чем должна быть сейчас: небольшим, детерминированным и проверяемым слоем semantic evolution поверх существующего языка.
