# Meldra Stage 0.4E — External Semantic Differential Validation

Дата фиксации: 2026-08-10  
Статус: preregistered до external/hidden execution  
Parent freeze: `stage04_cdabcc45ff1c8d377517c8861967f577aba61a08c5491d5f228cde745c0066e1`

## Исследовательский вопрос

Даёт ли исполнимо гарантированная семантика Meldra преимущество по runtime soundness, policy safety, interface locality или стоимости AI-разработки относительно максимально строгого практического Python profile?

Точное статическое разрешение имён само по себе больше не считается преимуществом языка.

## Сравниваемые arms

1. **Python unrestricted** — strong structural/type-aware binder и обычная семантика CPython.
2. **Python strict** — тот же binder плюс frozen profile: типы, explicit effects, injected capabilities, export manifests, запрет ambient imports, sidecar identity/revisions, interface hashes и ChangeIR policy.
3. **Meldra closed** — frozen Stage 0.4 grammar, binder, type/effect/capability rules, CoreIR и reference semantics.

Все arms используют одинаковые language-neutral tasks, acceptance tests, logical denominators и failure accounting.

## Гипотезы

- `H-RUNTIME-SOUNDNESS`: Meldra Exact остаётся runtime target; unrestricted Python сохраняет измеримый Unsound Exact risk.
- `H-STRICT-BASELINE`: Meldra даёт гарантии, которые maximal strict Python не повторяет при сопоставимой цене.
- `H-INTERFACE-LOCALITY`: отдельные interface/implementation revisions обеспечивают точную dependency, evidence и context invalidation.
- `H-EFFECT-CONTEXT`: typed effects увеличивают verified changes на 1 000 context tokens без потери task success.
- `H-CAPABILITY-SAFETY`: scoped capabilities ловят held-out escalation до materialization при false-block не выше 3%.
- `H-AGENT-VALUE`: same-model paired A/B показывает выигрыш по success, cost или long-horizon regressions.
- `H-EXPRESSIVENESS`: гарантии не требуют pervasive foreign/dynamic escape hatches.

## Независимый corpus

Минимальные размеры:

- 30–50 небольших программ;
- не менее 200 behavior-level paired changes;
- не менее 300 adversarial negative cases;
- не менее 300 runtime target observations;
- не менее 100 interface-locality changes;
- не менее 100 held-out capability attacks;
- не менее 30 external apply/test trials отдельно для Rename, Move и ChangeSignature.

Домены: CLI, pricing, authorization, inventory, small workflow, data transformation, event processing, plugin-like dispatch, configuration validation, notification service.

Порядок независимости:

1. language-neutral specification;
2. frozen acceptance tests;
3. независимые Python и Meldra implementations;
4. behavior-level change requests;
5. hidden set недоступен compiler maintainers;
6. author/project/template provenance сохраняется.

Generated translations Stage 0.4 не являются external evidence.

## Runtime Binding Soundness

Категории: module/instance/class monkey patching, replacement decorators, descriptor, property, `__getattr__`, `__getattribute__`, metaclass injection, import hook, dynamic re-export, open-world subclass override, dependency injection, `functools.partial`, `singledispatch`.

Для каждого callsite фиксируются:

- StaticExact;
- RuntimeObservedTargets;
- StaticTargetWasObserved;
- UnexpectedRuntimeTarget;
- UnsoundExact.

Primary metric: `Unsound Exact Rate`.

## Interface locality

Категории: private body edit, private rename, private type replacement, private dependency replacement, public signature/return/effect/capability change, public enum addition.

Метрики: invalidation precision/recall, unnecessary/missed invalidations, context closure size, evidence recalculation size.

## Effects и context

Сравниваются effect-blind и effect-aware closures. Маленький context засчитывается только при сохранённом acceptance-test success.

Primary metric: `Verified Changes per 1 000 Context Tokens`.

## Capability safety

Held-out attacks: forbidden database scope, forbidden network escalation, arbitrary host, secret-to-AI flow, effect inside pure function.

Метрики: detection recall, false-safe, false-block, time to detection, pre-materialization detection, runtime escapes.

Capability annotation без физического enforcement не считается предотвращением.

## Statistical policy

Primary unit зависит от эксперимента: runtime scenario, semantic change, held-out attack, paired behavior task или program. Reference-level значения descriptive only.

Results группируются per program, construct family, template, external author и external project. 95% intervals считаются deterministic cluster bootstrap с 10 000 replicates и seed `20260810`. Для zero-failure rate используется one-sided exact Clopper–Pearson bound. Infrastructure failures имеют отдельный denominator и не удаляются.

## Frozen GO gate

Language Alpha получает GO только при одновременном выполнении всех условий:

1. не менее 300 runtime observations, Meldra Unsound Exact = 0, one-sided 95% upper bound ≤1%, strict Python сохраняет residual risk либо требует material burden;
2. не менее 100 capability attacks, recall ≥99%, false-safe = 0, false-block ≤3%, pre-materialization = 100%, runtime escapes = 0;
3. не менее 100 locality changes, precision/recall ≥99%, missed invalidations = 0, median context reduction ≥30%;
4. same-model Agent A/B: либо +10 п.п. task success, либо success не хуже более чем на 2 п.п. и tokens/iterations ниже минимум на 30%, либо long-horizon regressions ниже минимум на 30%;
5. fully expressible programs ≥90%, foreign escape ≤10%, median source overhead ≤25%;
6. external acceptance, fuzz, mutation и cross-process determinism gates пройдены.

Ни один обязательный `UNMEASURED` metric не может дать GO.

## Frozen NO-GO gate

Если maximal strict Python находится в пределах 2 п.п. Meldra по task success, policy recall и locality, а преимущество Meldra по burden не превышает 25%, новый язык получает NO-GO. Результат переводится в strict semantic platform или cross-language semantic layer.

## Stop conditions

Run инвалидируется, если после доступа к hidden set меняется frozen Stage 0.4 digest, acceptance tests, support profile, denominators или gates; если infrastructure failures удаляются; если generated repetitions выдаются за independent samples; если нарушены same-model/equal-budget constraints.

## Feature freeze

В Stage 0.4E запрещены новый синтаксис, `flow`, `machine`, advanced generics, traits, macros, async runtime, scheduler, package registry, WASM/LLVM backend, UI/GPU/mobile и собственная модель. Разрешены только benchmark harnesses, external adapters, проверки и исправления дефектов, найденных до hidden execution.
