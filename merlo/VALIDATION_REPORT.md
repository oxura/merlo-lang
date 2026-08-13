# Meldra Stage 0.2–0.4 — внешний validation report

Дата: 2026-08-10  
Режим: hypothesis-first; Stage 0.4 Closed Semantic Frontend реализован, Language Alpha и product runtime стадий 0.5–0.7 не разрешены

## 1. Итоговый вердикт

Stage 0.2 дал проверяемый semantic evolution sidecar для Python, Stage 0.3 — синтетический Core Semantics Lab, Stage 0.4 — исполняемый закрытый frontend. Все 13 generated frontend gates прошли, но ни один этап **не доказал**, что архитектура уже готова стать новым языком или production-платформой.

Главные факты:

- 20 внешних Python-проектов, 18 категорий, 5 654 tracked Python-файла в acquisition manifest;
- 81 905 semantic entities, 391 227 references и 37 060 calls в coverage scan;
- точное разрешение ссылок: 90 882 / 391 227 = **23,23%**;
- usable разрешение для консервативных Rename/Move: 92 975 / 391 227 = **23,77%**;
- 500 policy/fixture задач: 447 измерены, 53 infrastructure failures;
- false-safe: 0 / 267 на измеренных unsafe-задачах;
- false-block: 83 / 180 = **46,11%**, целевой уровень `<3%` не достигнут;
- safe automation coverage: 97 / 180 = **53,89%**;
- в исходном 500-task policy corpus все safe-примеры были Rename; corrected source-product pilot добавил по 4 planner-ready Rename, Move и ChangeSignature в 4 завершившихся проектах, но labels остаются non-human;
- false-block Pareto: 82/83 исходных false-block вызваны `UnsupportedBinding`, ещё 1/83 — `EntityBudgetExceeded`; среди 98 задач поддерживаемого module-level scope post-hoc false-block = 1/98 = **1,02%**;
- isolated Pluggy apply-and-test: Rename, Move и ChangeSignature материализованы 3/3, return code 0 в 3/3, baseline **141 passed** сохранён в 3/3, исходные bytes восстановлены в 3/3;
- первая версия apply pilot была честно инвалидирована: Rename тестовой функции вернул 0, но уменьшил pytest discovery с 141 до 140; после этого test/docs/example candidates исключены;
- changed-only identity claim `>99,5%` не подтверждён: targeted manual audit нашёл 1 ложную identity assignment среди 5 предсказанных changed links;
- independent review workflow создан, но подтверждённых labels пока 0/6 в очереди при policy target 600;
- Core Lab на 3 эквивалентных synthetic program pairs: internal binding 3/3 Exact, Unknown 0, safe changes 9/9 против Python sidecar 6/9, identity continuity 9/9 против 6/9;
- сериализованный Core context = 4 436 bytes против 9 359 bytes Python Task Capsule, то есть **47,40%**, но equal-success agent A/B отсутствует;
- corrected source-product selection завершился для 4/5 проектов; Typer не нашёл кандидатов за 180 s и учтён как selection timeout;
- current incremental closure почти полный; реального incremental scanner нет;
- composition/parallel throughput измерены только как offline simulation;
- 10-step persistent-world smoke прошёл, но crash journal и process locking отсутствуют.
- Stage 0.4: 40 paired programs, 160 lossless Meldra sources, 1 920 logical references, 360 negative cases и 200 semantic changes;
- Meldra closed binder: 1 920/1 920 Exact; strong structural Python binder: те же 1 920/1 920; current sidecar: 1 240/1 920;
- 13/13 generated gates `SUPPORTED`, включая 400/400 typing checks, 80/80 effect checks, 80/80 capability checks, 240/240 ChangeIR provenance/collision checks и 80/80 evaluator results;
- разница с current Python sidecar не является языковым преимуществом: сильный Python baseline полностью сравнялся с Meldra;

Решение: **NO-GO для Meldra Language Alpha**. Stage 0.4 frontend kernel принят только как инженерный lab result. Разрешён следующий эксперимент `EXTERNAL_STAGE04_VALIDATION`: независимый held-out source-product corpus, human adjudication и одинаковые acceptance tests. `flow`, `machine`, UI/GPU/mobile, package ecosystem и большой runtime остаются за gate.

## 2. Статус гипотез

| Гипотеза | Статус | Наблюдение |
|---|---|---|
| Semantic ChangeIR безопаснее raw text edit | Частично подтверждена | internal regression не дал false-safe; внешний policy corpus дал 0/267; три source-product Pluggy changes сохранили 141-test baseline, но это один repository |
| False Safe Rate → 0 | Наблюдалось, не доказано | 0/267 policy unsafe; labels не независимы; односторонняя 95% верхняя граница для population rate всё ещё 1,12% |
| False Block Rate `<3%` | Не подтверждена globally; поддерживаемый scope прошёл post-hoc | raw 83/180 = 46,11%; 82/83 — неподдерживаемые method-level bindings; post-hoc module-level scope 1/98 = 1,02%, не preregistered |
| Changed identity precision `>99,5%` | Не подтверждена | unchanged links маскируют denominator; manual changed audit = 4/5; independent queue = 0/6 labelled |
| Semantic coverage достаточна для automation | Не подтверждена | usable Rename/Move coverage 23,77%; conservative signature coverage 16,76% |
| Закрытый frontend устраняет internal uncertainty | Подтверждена только на generated corpus | Meldra 1 920/1 920 Exact и Unknown 0; strong Python binder также 1 920/1 920; external held-out corpus отсутствует |
| Executable frontend детерминирован и проверяем | Подтверждена только в lab | 160/160 byte roundtrip, 400/400 positive/negative typing, 1/1 canonical lowering, 80/80 values/effect traces |
| Runtime evidence уменьшает uncertainty | Частично, слабая форма | targets/counts восстановлены; static set не сузился, `Observed` не стал `Exact` |
| Task Capsule экономит контекст без потери success | Bytes-only | Core/Python = 4 436/9 359 = 47,40%; task success, tokens и iterations не измерены |
| Evidence invalidation сокращает проверки | Mechanism pass, completeness unknown | выбрана 1 из 4 declared suites; пропущенные regressions не измерены |
| Incremental semantic world масштабируется | Не подтверждена | один edit дал closure 72/73 файлов и 100% references/calls; speedup не заявлен |
| ChangeIR хорошо композируется | Simulation-only | 5 680/6 000 пар commute по текущим правилам; реального concurrent исполнения не было |
| Долгая история сохраняет IDs/revisions/evidence | Smoke pass | 10 изменений, 11 entity/world revisions, 0 failures |
| Crash-safe transactions готовы | Не подтверждена | byte rollback есть; journal/recovery/process lock отсутствуют |
| Meldra улучшает работу реального coding agent | Не измерена | provider key отсутствовал; нулевые denominators сохранены как `UNMEASURED` |
| Meldra лучше LSP refactoring | Не измерена | `pyright-langserver` отсутствовал; слабая замена не использовалась |
| Flow/Machine оправданы экспериментом | Не измерена | Core Lab намеренно ограничен package/module/symbol/value/function/task/effect/capability |

## 3. Методика и denominators

### 3.1 Внешний корпус

Acquisition manifest: [`../benchmarks/meldra_external_projects.json`](../benchmarks/meldra_external_projects.json).

В нём зафиксированы URL, branch, exact HEAD revision и число tracked Python-файлов для 20 публичных проектов:

`aiohttp`, `black`, `celery`, `click`, `dbt-core`, `fastapi`, `flask`, `httpx`, `kedro`, `locust`, `marshmallow`, `pluggy`, `python-telegram-bot`, `pytest`, `requests`, `rich`, `scrapy`, `sqlalchemy`, `starlette`, `typer`.

Корпус покрывает backend, CLI, async, plugins, data, tooling, bots, HTTP, serialization, distributed tasks и terminal UI. Репозитории клонировались в `/tmp/meldra-external-corpus` и не изменялись.

Оговорка source scope: Black измерен на `src/black`, а Kedro — на `kedro/pipeline`; full-root Kedro scan завершался `AnalysisError` на cookiecutter templates. Metadata сохраняет URL, revision, source slice и известный failure. Число 5 654 описывает acquisition manifest, а не утверждает, что каждый tracked файл успешно вошёл в semantic scan.

Task manifest: [`../benchmarks/meldra_external_validation.json`](../benchmarks/meldra_external_validation.json).

- seed `20260810`;
- 25 задач на проект;
- 10 expected-safe и 15 expected-unsafe на проект;
- 500 задач всего;
- labels: deterministic policy/fixture oracle;
- held-out target revision записан в task metadata.

Это policy regression corpus, а не независимая human-labeled выборка. Он проверяет, что известные запреты срабатывают, но не доказывает отсутствие неизвестных unsafe paths.

### 3.2 Метрики безопасности

Только задачи с `planner_allowed != null` входят в confusion matrix:

```text
False Safe Rate  = FP / (FP + TN)
False Block Rate = FN / (TP + FN)
Safe Coverage    = TP / (TP + FN)
```

Infrastructure failures не превращаются ни в safe, ни в blocked. Они имеют отдельный reliability denominator. Такое разделение не позволяет улучшить safety score падением scanner/planner.

### 3.3 Метрика идентичности

Две выборки разделены:

1. **Preserved:** locator не изменился. Это regression check стабильности snapshot, но не доказательство Rename/Move recovery.
2. **Changed-only:** locator изменился и есть rename/move hypothesis. Только этот denominator относится к заявлению `Identity Precision >99,5%`.

Общий score, смешивающий десятки тысяч unchanged links с единицами changed links, считается описательной статистикой и не используется как identity proof.

### 3.4 Semantic coverage

```text
Exact  = Exact / all references
Usable = (Exact + Derived) / all references
```

Rename/Move coverage требует `Exact|Derived`. ChangeSignature denominator включает все `CallCallee`, `Callback`, `Partial`, `StoredValue`, `Decorator`; safe numerator содержит только точно/производно разрешённые прямые `CallCallee`. Это консервативнее прежнего смещённого denominator только из уже построенных call edges.

## 4. External 500-task benchmark

Raw result: [`../benchmarks/meldra_external_results.json`](../benchmarks/meldra_external_results.json).

| Поле | Результат |
|---|---:|
| Requested tasks | 500 |
| Evaluated tasks | 447 |
| Infrastructure failures | 53 |
| Reliability coverage | 89,4% |
| Expected safe | 200 |
| Expected unsafe | 300 |
| TP | 97 |
| FN | 83 |
| FP | 0 |
| TN | 267 |
| False Safe | 0/267 = 0% |
| False Block | 83/180 = 46,11% |
| Safe automation | 97/180 = 53,89% |

Safe coverage Wilson 95% CI: 46,60–61,01%. False-block Wilson 95% CI: 38,99–53,40%.

### 4.1 Ограничение operation coverage

| Operation | Total | Evaluated | Expected-safe denominator | Вывод |
|---|---:|---:|---:|---|
| Rename | 300 | 269 | 180 | единственная операция с measured safe automation |
| Move | 100 | 92 | 0 | измерены только запреты; safe Move не проверен |
| ChangeSignature | 100 | 86 | 0 | измерены только запреты; safe migration не проверена |

Следовательно, общий safe automation score нельзя переносить на MoveSymbol и ChangeSignature.

### 4.2 Infrastructure failures

- SQLAlchemy: 25/25 задач помечены `ProjectScanTimeout`; scan не вошёл в первую задачу за 1 279,59 s.
- HTTPX: 10 `KeyError` из-за неоднозначной semantic entity.
- Click: 9 таких ошибок.
- Pluggy: 6 таких ошибок.
- aiohttp: 3 таких ошибки.

Всего 28 task failures показали collision logical IDs на overload/property/repeated declarations. Это не «грязные данные», а defect текущей identity schema. Новый язык должен иметь отдельные DeclarationId и role/group identity.

### 4.3 Блокировки

Наиболее частые причины во всех task records:

| Причина | Count |
|---|---:|
| `UnsupportedBinding` | 194 |
| `PublicApiCompatibility` | 89 |
| `UnsupportedSignatureMigration` | 73 |
| `SyntaxInvalid` | 32 |
| `infrastructure:project_scan_timeout` | 25 |
| `TargetCollision` | 16 |
| `CyclicDependency` | 14 |
| `MissingArgumentMigration` | 13 |
| `MoveDependencyCollision` | 9 |

False-block достигал 80% в `distributed-task-processing`, `http-library`, `load-testing`, `serialization-library`; 70% в bot/crawler/test-framework. Текущая стратегия действительно conservative, но слишком часто отказывается от нормальной автоматизации.

## 5. Semantic coverage на 20 проектах

Per-project rows и raw resolution counts находятся в [`../benchmarks/meldra_hypothesis_pilot.json`](../benchmarks/meldra_hypothesis_pilot.json).

| Метрика | Numerator | Denominator | Ratio |
|---|---:|---:|---:|
| Exact references | 90 882 | 391 227 | 23,23% |
| Usable references | 92 975 | 391 227 | 23,77% |
| Rename safe binding coverage | 92 975 | 391 227 | 23,77% |
| Move safe binding coverage | 92 975 | 391 227 | 23,77% |
| ChangeSignature conservative coverage | 39 962 | 238 426 | 16,76% |

Дополнительно:

- entities: 81 905;
- calls: 37 060;
- semantic hazards: 4 449;
- uncertain references: 298 252.

Распределение uncertainty:

| Kind | Count | Доля uncertainty |
|---|---:|---:|
| `name` | 209 477 | 70,23% |
| `attribute` | 43 667 | 14,64% |
| `import` | 33 237 | 11,14% |
| `unknown_name` | 7 495 | 2,51% |
| `string_export` | 2 679 | 0,90% |
| `dynamic` | 1 623 | 0,54% |

Вывод для языка: closed name binding, declared package exports и typed member interfaces важнее, чем один запрет reflection. Language experiment классифицирует 298 252/298 252 uncertain references как потенциально адресуемые свойствами закрытого языка. Это **theoretical upper bound** при сильных assumptions; implementation и measured gain отсутствуют.

## 6. Git identity validation

Compact raw summary: [`../benchmarks/meldra_git_identity_summary.json`](../benchmarks/meldra_git_identity_summary.json).  
Manual adjudication: [`../benchmarks/meldra_identity_manual_audit.json`](../benchmarks/meldra_identity_manual_audit.json).

### 6.1 Последняя first-parent пара в 20 repos

- requested: 20;
- completed: 15;
- пять repos не дали pair result;
- infrastructure error records: 8;
- preserved-dominated links: TP 73 995, FP 0, predicted 73 995;
- all-link precision 100%; recall 73 995/73 999 = 99,9946%;
- changed-only predictions: 0;
- changed-only proxy ground truth: 1;
- changed-only precision denominator: 0.

Пять repos выпали из-за unsafe archive-member rejection или Python syntax/fixture parsing errors. Общий высокий score описывает unchanged addresses и не отвечает на вопрос о rename/move.

### 6.2 Deep sample

40 последних first-parent пар в Flask, HTTPX, Marshmallow и Pluggy:

- completed 40/40;
- all links: TP 34 291, FP 5, predicted 34 296;
- all-link precision 99,9854%, recall 99,9883%;
- changed-only Git proxy: TP 0, FP 5, FN 1.

Но ручная проверка выявила ошибки самого proxy oracle:

- Git proxy пропустил перенос `DistFacade` и трёх методов из `pluggy._manager` в `pluggy._compat`;
- четыре из пяти Meldra changed predictions в этом commit были реальными moves;
- пятая prediction ошибочно связала существующий `list_plugin_distinfo` с новым `list_plugin_distributions`; старый метод остался на прежнем locator;
- Flask proxy назвал `_reset_os_environ -> _standard_os_environ` чистым Rename, хотя `_standard_os_environ` уже существовал в old revision, а новый fixture объединил/переписал поведение.

Targeted manual precision: 4/5 = 80%. Manual recall не заявляется: audit не был полным ground-truth corpus.

Отдельный 10-pair Click run оборвался в `measure_split_merge_hypotheses` с `ValueError: split hypotheses require 1:N endpoints`; результат не был превращён в частичный успех.

Вердикт: `>99,5% changed identity precision` не подтверждён. Для односторонней 95% нижней границы выше 99,5% при нуле ошибок нужно минимум 598 подтверждённых changed predictions; для двустороннего 95% интервала — 736. Нужен manually adjudicated corpus, а не Git-diff heuristic как единственная истина.

После этого результата policy ужесточена: heuristic `Probable` больше не наследует старый Entity ID автоматически. Новый snapshot получает свежий ID, а relation остаётся review-only; прежний ID возвращается только explicit confirmation. Это исключает перенос evidence/trust по одной structural similarity.

## 7. Context L0–L3 и Task Capsule

Пять реальных targets текущего проекта:

| Level | Median bytes | Mean bytes | Range |
|---|---:|---:|---:|
| L0 | 6 130 | 18 632 | 4 248–39 303 |
| L1 | 12 539 | 33 120 | 5 707–78 721 |
| L2 | 17 423 | 40 719 | 6 208–95 446 |
| L3 | 17 492 | 40 788 | 6 277–95 515 |

Для `core.cognitive_budget.compute_budget` полный Task Capsule имел 76 093 bytes против 870 583 bytes Python-исходников в fresh scan: 8,74%. Оценка по правилу `UTF-8 bytes / 4` — 19 024 против 217 646 tokens. Это byte proxy, а не tokenizer output.

L3 почти не вырос относительно L2, потому что в выбранных задачах мало attached evidence/policy. Это не доказывает достаточность уровня. Same-model baseline/Meldra A/B сохранил `UNMEASURED`: `FIREWORKS_API_KEY` отсутствовал, input/output tokens и task-success denominators равны нулю.

## 8. Runtime evidence

Реальный instrumented Python fixture с variable `getattr`:

- 3 вызова;
- 2 observed targets;
- environment и trace/artifact hashes записаны;
- `unseen_targets_possible = true`;
- static resolution не повышен до `Exact`.

Отдельный representable dynamic case имел 1 static `Dynamic` reference и 1 observed target на 2 calls. Runtime observation доказало достижимость наблюдённой цели, но не недостижимость ненаблюдённых. Дополнительно обнаружен frontend gap: variable-`getattr` fixture не получил static uncertain reference, хотя profiler увидел targets.

Гипотеза «runtime постепенно заменит Unknown на Exact» в таком виде неверна. Правильный lattice: `Unknown/Dynamic -> Observed`, а `Exact` требует closed-world guarantee.

## 9. Evidence invalidation

Симуляция dependency-bound evidence:

- declared experiments: 4;
- изменение одной entity выбрало 1 experiment;
- selected fraction: 25%;
- invalidated evidence: 1;
- preserved evidence: 3;
- выбранная suite реально прошла 26 tests;
- полный suite отдельно наблюдал 259 tests.

Это показывает механизм экономии, но dependency completeness не доказана. `missed_regressions = null`: нельзя заявлять безопасное сокращение CI, пока полный suite не используется как oracle на большом наборе изменений.

## 10. Incrementality

Пилот изменил одну entity в `core/cognitive_budget.py` и сравнил два полных scans:

- measured full scan: 7,966 s;
- comparison: 0,063 s;
- affected files: 72/73 = 98,63%;
- affected entities: 887/1162 = 76,33%;
- affected references: 5157/5157 = 100%;
- affected calls: 1596/1596 = 100%;
- speedup claim: false.

Это theoretical affected closure, не incremental scanner. Текущий граф слишком груб для масштабирования. Языку нужны compiler-owned fine-grained binding/type/effect facts и инкрементальная поддержка запросов.

## 11. Composition и параллелизм

500 task descriptors сгруппированы по 20 проектам и проверены текущими `changes_commute/changes_conflict`:

- pairs: 6 000;
- commuting: 5 680 = 94,67%;
- conflicting: 124 = 2,07%;
- non-commuting: 320;
- theoretical waves: 71;
- theoretical changes/wave: 7,04.

Эксперимент явно offline и deterministic. Он не планировал и не применял changes, не запускал agents/subprocesses и не измерял wall time. Это поддерживает исследование Change Algebra, но не доказывает parallel-agent throughput или crash-safe scheduling.

## 12. Persistence, transactions и source preservation

Long-horizon smoke:

- 10 последовательных changes;
- один стабильный `SymbolId`;
- 11 distinct entity revisions;
- 11 distinct world revisions;
- 10 evolution-log entries;
- 4 valid и 35 stale evidence records;
- failures: 0.

Internal benchmark также сохранил transaction safety 16/16 и корректный stale-world refusal. Unit/property tests покрывают identity ambiguity, source preservation, deterministic materialization, signature default migration, dependency collision и rollback.

Ограничение: World хранится atomic JSON snapshot. Нет append-only crash journal, fsync protocol, inter-process lock и kill-point recovery. Поэтому «production transaction system» не заявляется.

## 13. Что реализовано как validation infrastructure

Изолированные measurement и lab modules:

- `coverage.py` — raw semantic/operation denominators;
- `external_bench.py` — policy-labelled external tasks, balanced source-product selection и isolated apply-and-test;
- `validation_taxonomy.py` — false-block Pareto и infrastructure taxonomy;
- `identity_ground_truth.py` — independent adjudication queue, import/export и agreement metrics;
- `git_identity.py` — Git pair extraction, proxy hints и changed-only metrics;
- `agent_trial.py` — provider-neutral same-model A/B schema;
- `runtime_experiment.py` / `evidence_experiment.py`;
- `context_experiment.py` / `incremental.py`;
- `composition_experiment.py`;
- `language_experiment.py`;
- `lsp_baseline.py`;
- `core_semantics.py` — deterministic CoreIR binder, identities, package interfaces, effects/capabilities и transactional ChangeIR;
- `core_bench.py` — эквивалентные Python/Core fixtures и измерения закрытого binding, blast radius, context, automation и identity.

Не добавлены production `flow`, `machine`, agent scheduler, package runtime, parser, type checker, compiler backend или autonomous orchestration. Core Semantics Lab остаётся in-memory research model, а не пользовательским языком.

## 14. Что делать дальше по данным

### Выполненное hardening

1. Создан независимый identity review workflow с immutable dataset digest и отдельным reviewer namespace; labels ещё не собраны.
2. Добавлены balanced-safe Move/ChangeSignature/Rename candidates. Test, docs, examples, tutorial и неоднозначные Entity ID исключаются до planner.
3. Добавлен isolated apply-and-test harness с revision guard, temporary copy, cleanup и source digest. На Pluggy 3/3 changes сохранили 141-test baseline.
4. False-block и infrastructure failures разложены по причинам. Raw 46,11% нельзя выдавать за supported-scope rate.
5. LSP и same-model agent prerequisites проверяются воспроизводимо и остаются `UNMEASURED`, а не заменяются proxy scores.

### P0 — незакрытые blockers

1. Получить минимум 600 independently adjudicated changed-identity predictions; текущий результат 0.
2. Заменить planner-preflight safe labels независимым oracle и расширить behavioral apply-and-test за пределы одного repository.
3. Устранить sidecar DeclarationId collisions в модели, а не только фильтровать их из benchmark selection.
4. Добавить bounded candidate search: Typer source-product selection не завершился за 180 s.
5. Выполнить same-model A/B и LSP baseline только после появления реальных prerequisites.

### Stage 0.4 — generated experiment завершён

Lossless parser/CST, closed binder, nominal checker, effects/capabilities, deterministic CoreIR lowering и reference evaluator реализованы для малого surface. Generated gates пройдены, но source-preserving ChangeIR materialization обратно в `.meldra`, incremental build и production runtime отсутствуют.

### External Stage 0.4 validation — разрешённый следующий эксперимент

Собрать независимый held-out source-product corpus, зафиксировать human labels до запуска, сравнить Meldra и сильный Python baseline на одинаковых logical references и acceptance tests. Same-model agent A/B обязан использовать одинаковые задачи, tools и budgets. Generated fixtures нельзя выдавать за external evidence.

### Meldra 0.5 gate

Текущий вердикт — `NO_GO_LANGUAGE_ALPHA`. Executable frontend gate закрыт только generated evidence; остаются held-out closed-binding corpus, независимые behavioral labels, 600 identity adjudications, same-model equal-task A/B и context comparison при равном success. Точные прежние denominators и stop conditions: [`../benchmarks/meldra_0_5_gate.json`](../benchmarks/meldra_0_5_gate.json).

## 15. Артефакты

- [`STAGE_0_2_REPORT.md`](STAGE_0_2_REPORT.md) — отчёт внутреннего vertical slice;
- [`LANGUAGE_RESEARCH.md`](LANGUAGE_RESEARCH.md) — скорректированная архитектура языка;
- [`../benchmarks/meldra_external_projects.json`](../benchmarks/meldra_external_projects.json) — exact corpus provenance;
- [`../benchmarks/meldra_external_validation.json`](../benchmarks/meldra_external_validation.json) — 500 task specs;
- [`../benchmarks/meldra_external_results.json`](../benchmarks/meldra_external_results.json) — 500 raw task outcomes и breakdowns;
- [`../benchmarks/meldra_external_coverage.json`](../benchmarks/meldra_external_coverage.json) — per-project semantic coverage и исправленные operation denominators;
- [`../benchmarks/meldra_hypothesis_pilot.json`](../benchmarks/meldra_hypothesis_pilot.json) — coverage/context/runtime/evidence/incremental/composition results;
- [`../benchmarks/meldra_git_identity_summary.json`](../benchmarks/meldra_git_identity_summary.json) — latest/deep Git metrics;
- [`../benchmarks/meldra_identity_manual_audit.json`](../benchmarks/meldra_identity_manual_audit.json) — ручная проверка спорных changed links;
- [`HASHING.md`](HASHING.md) — byte/revision/identity invariants.
- [`../benchmarks/meldra_identity_review_queue.json`](../benchmarks/meldra_identity_review_queue.json) — immutable independent-review queue;
- [`../benchmarks/meldra_validation_taxonomy.json`](../benchmarks/meldra_validation_taxonomy.json) — false-block Pareto и infrastructure breakdown;
- [`../benchmarks/meldra_balanced_safe_pilot.json`](../benchmarks/meldra_balanced_safe_pilot.json) — corrected source-product balanced planner pilot;
- [`../benchmarks/meldra_external_apply_test_pilot.json`](../benchmarks/meldra_external_apply_test_pilot.json) — isolated Pluggy apply/test/restore evidence;
- [`../benchmarks/meldra_core_fixtures.json`](../benchmarks/meldra_core_fixtures.json) — эквивалентные Python/Core programs;
- [`../benchmarks/meldra_core_benchmark.json`](../benchmarks/meldra_core_benchmark.json) — Core Semantics Lab measurements;
- [`../benchmarks/meldra_0_5_gate.json`](../benchmarks/meldra_0_5_gate.json) — решение и preregistered evidence gate.
- [`FRONTEND_KERNEL.md`](FRONTEND_KERNEL.md) — frozen Stage 0.4 semantic contract и observed decision;
- [`core_ir_schema_v1.json`](core_ir_schema_v1.json) — frozen canonical CoreIR v1 schema;
- [`../benchmarks/meldra_stage04_support_profile.json`](../benchmarks/meldra_stage04_support_profile.json) — preregistered Python P0 scope;
- [`../benchmarks/meldra_stage04_frontend_benchmark.json`](../benchmarks/meldra_stage04_frontend_benchmark.json) — полный 40-program, 13-gate frontend result;

## 16. Воспроизводимость и ограничения

Observed checks в этой рабочей копии:

```text
python3 -m pytest tests/ -q
python3 -m py_compile meldra/*.py selected tests
python3 -m meldra bench --compact
python3 -m meldra core-bench --compact
python3 -m meldra experiment --compact
python3 -m meldra scan --state <tmp> --json
python3 -m meldra rename --state <tmp> ...
python3 -m meldra move --state <tmp> ...
python3 -m meldra signature --state <tmp> ...
python3 -m meldra frontend-check <meldra-root> --compact
python3 -m meldra frontend-ir <meldra-root> --compact
python3 -m meldra frontend-run <meldra-root> <symbol> ...
run_frontend_benchmark(40)
```

Финальная проверка этой рабочей копии: `python3 -m pytest tests/ -q` → **338 passed**; `python3 -m py_compile meldra/*.py tests/test_meldra*.py` → exit 0 без вывода. End-to-end CLI smoke дал return code 0 для `frontend-check`, `frontend-ir` и двух `frontend-run`: pure value `5`, handled effect value `41`. Полный `run_frontend_benchmark(40)` сохранил 13/13 `SUPPORTED` gates. Pluggy test suite ранее реально запускался в isolated copies: baseline и каждый из трёх patches дали **141 passed**. Это observed evidence, а не proof всей платформы. `pyflakes` не запущен: модуль отсутствует в окружении (`No module named pyflakes`). Same-model AI calls, воспроизводимый LSP baseline, independent Stage 0.4 corpus, production runtime/crash recovery и real parallel-agent execution не измерены.
