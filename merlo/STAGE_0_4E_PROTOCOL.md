# Meldra Stage 0.4E — External Semantic Differential Validation v2

Дата фиксации: 2026-08-10  
Статус: preregistered и locked до external hidden-set execution  
Supersedes: v1, сохранённый без изменений  
Причина новой версии: требования расширены до 23 runtime categories и явных Arm A/B/C до запуска независимого external corpus.

## Исследовательский вопрос

Даёт ли исполнимо гарантированная семантика Meldra измеримое преимущество над максимально сильным честным Python baseline по runtime soundness, safety, package locality, AI efficiency и долгой semantic evolution?

Static binding completeness не является достаточным доказательством.

## Три arms

### Arm A — Current Python Sidecar

Текущий Python analyzer, существующий semantic evolution sidecar и обычная семантика CPython. Его `Exact` — утверждение анализатора, а не runtime guarantee.

### Arm B — Maximal Python Semantic Profile

Strong structural/type-aware binder, explicit exports и package manifests, SymbolId/RevisionId sidecar, effect/capability declarations, ambient import policy, interface hashes, source-preserving ChangeIR, static/import audit, restricted runtime harness, tests и LSP при наличии.

Любой обход через `socket`, `globals()`, monkey patching, import hook или иной Python-механизм считается runtime escape. Аннотация сама по себе не считается enforcement.

### Arm C — Meldra Closed Frontend

Замороженные Stage 0.4 grammar, binder, nominal checker, interfaces, effects, capabilities, identity policy, CoreIR v1 и evaluator executed-SymbolId trace. Dynamic/foreign boundary не может считаться Exact.

## Runtime Binding Soundness

23 обязательные категории:

1. module monkey patch;
2. instance monkey patch;
3. class monkey patch;
4. replacement decorator;
5. wrapper decorator;
6. property;
7. descriptor;
8. `__getattr__`;
9. `__getattribute__`;
10. metaclass injection;
11. subclass override;
12. open-world dispatch;
13. `singledispatch`;
14. `functools.partial`;
15. callback variable;
16. dependency injection;
17. runtime re-export;
18. import hook;
19. mutable module namespace;
20. replacement after import;
21. callable object;
22. proxy object;
23. dynamic method registration.

Для каждого callsite сохраняются static classification, predicted SymbolId/target, runtime code-object identity, observation count, observed target set, unexpected target, provenance, environment и soundness.

`Unsound Exact` означает: reference был выдан как `Exact`, но хотя бы один root runtime target отличается от predicted target. Диагностическое сообщение `out-of-profile` не переклассифицирует уже выданный `Exact` задним числом.

Repeated calls проверяют стабильность harness, но не увеличивают independent sample size.

## Corpus и статистика

External gate требует:

- 30–50 независимых программ;
- не менее 200 paired behavior changes;
- не менее 300 adversarial negatives;
- не менее 300 runtime observations;
- все 23 runtime categories;
- не менее 100 interface changes;
- не менее 100 capability attacks;
- по 30 external safe Rename, Move и ChangeSignature trials.

Primary units: runtime scenario, semantic change, held-out attack, paired agent task или program. Reference counts descriptive only. Результаты кластеризуются per program, construct family, template, author и project. 95% intervals: deterministic cluster bootstrap, 10 000 replicates, seed `20260810`; zero-failure upper bound — exact one-sided Clopper–Pearson.

## Определения outcomes

- **Infrastructure failure**: tool, checkout, interpreter, timeout, provider, LSP или sandbox не дали semantic outcome. Случай остаётся в отдельном denominator.
- **Semantic failure**: arm выполнил или принял поведение, нарушающее frozen specification, contract, identity, effect, capability, interface или acceptance oracle.
- **False safe**: unsafe/policy-violating change разрешён к materialization или прошёл все pre-materialization guards.
- **False block**: safe in-profile change заблокирован при доступном полном required context.
- **Successful change**: поведение реализовано, acceptance tests и collection guard проходят, public API snapshot соответствует ожиданию, policy checks проходят, unintended files не изменены, restoration успешен.

## Frozen GO

Language Alpha получает GO только при одновременном выполнении:

1. Meldra Unsound Exact = 0 на ≥300 external observations и one-sided 95% upper bound ≤1%; maximal Python сохраняет residual risk либо требует material burden.
2. Capability attacks ≥100, recall ≥99%, false-safe 0, false-block ≤3%, pre-materialization 100%, runtime escapes 0.
3. Locality changes ≥100, precision/recall ≥99%, missed invalidations 0, median context reduction ≥30%.
4. Same-model A/B: +10 п.п. success, либо noninferiority в пределах 2 п.п. и ≥30% tokens/iterations reduction, либо ≥30% long-horizon regression reduction.
5. Fully expressible rate ≥90%, foreign escape ≤10%, median source overhead ≤25%.
6. External acceptance, fuzz, mutation и cross-process/hash-seed/file-order determinism gates пройдены.

Любой обязательный `UNMEASURED` запрещает GO.

## Frozen NO-GO

Если Maximal Python находится в пределах 2 п.п. Meldra по task success, policy recall и locality, а преимущество Meldra по burden не превышает 25%, новый язык получает NO-GO. Результат переводится в strict Python semantic platform или cross-language semantic layer.

## Result invalidation

Run инвалидируется при изменении frozen Stage 0.4 digest после hidden access, изменении acceptance tests/gates/support profile, разных logical denominators, удалении infrastructure failures, выдаче generated repetitions за independent samples или нарушении same-model/equal-budget constraints.

## Feature freeze

Запрещены новый syntax, `flow`, `machine`, advanced generics, traits, macros, async runtime, scheduler, package registry, WASM/LLVM, UI/GPU/mobile и custom model. Разрешены harnesses, tests, adapters и objective defect fixes по правилу failing regression test first, с version bump и сохранением pre/post-fix results.
