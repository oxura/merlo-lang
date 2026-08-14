# Controlled AI evaluation

The public AI productivity study is separate from Merlo's executable compiler
benchmark. `benchmarks/ai-ab-v1` is **`PREREGISTERED_UNRUN`**: its 30 paired
language-neutral tasks, expanded fixtures, oracles, prompt parity lock, seeded
90-pair schedule, budgets, and claim gates are published before any model call.
The legacy agent reports are historical artifacts and are not evidence for this
study.

## Protocol

Each task has a Merlo mirror and a Python 3.12 + pytest mirror. Ten tasks are
in each of deterministic CLI/data transformation, multi-module API migration,
and regression repair. Every pair is run three times in a fresh locked
workspace. The common tool menu is shell, read, search, edit, and test, with the
same model snapshot, decoding controls, token/time/iteration/tool limits,
container image, result clipping, and denied network. Prompt differences are
only `LANGUAGE`, `WORKSPACE`, and `TEST_COMMAND`; normalized prompt hashes are
identical.

The validator checks the protocol and task self-digests, fixture/oracle hashes,
duplicate and missing mirrors, allowlists, prompt parity, schedule and budget
locks, and complete denominators before execution. Every later attempt must
retain redacted request/response/tool-event transcript JSONL, stdout/stderr,
pre/post digest maps, raw tokens/time/iterations/tool calls, oracle output,
regressions, irrelevant edits, terminal reason, and hashes. Independent oracle
and full-regression results—not model declarations—determine success.

A missing immutable provider revision or key fingerprint produces
`UNMEASURED_PROVIDER_IDENTITY_INCOMPLETE`; no provider identity or outcome is
synthesized. A lock change stops the study as `INVALID_PROTOCOL_DEVIATION` and
requires a new v2 preregistration. The training-cutoff and no-network policy
reduces but cannot prove absence of memorization or provider contamination.

## Restricted result gate

Only when all 90 pairs are measured and artifact-valid may the paired metrics be
reported: success-rate difference in percentage points, fixed-seed 10,000-draw
paired bootstrap CIs stratified by task, median token and wall-time ratios,
median iterations, irrelevant edits, regressions, and every unmeasured
 denominator. A restricted conditional advantage statement is eligible only if
success difference is at least +10 percentage points, its 95% paired-bootstrap
lower bound is greater than zero, at least one median token or wall-time ratio
has an upper 95% CI below 0.85, and successful attempts contain no regression or
out-of-scope edit. Otherwise report `MEASURED_INCONCLUSIVE` or
`MEASURED_NO_ADVANTAGE`; do not claim language or general productivity
superiority.
