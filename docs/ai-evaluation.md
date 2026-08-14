# Controlled AI evaluation

The public AI productivity study is separate from Merlo's executable compiler
benchmark. `tools/benchmarks/merlo/benchmarks/ai-ab-v1` is **`DRAFT_UNRUN`**: its 30 paired
language-neutral tasks, expanded fixtures, oracles, prompt parity lock, seeded
90-pair schedule, budgets, six disjoint calibration tasks, and claim gates are
bound to the alpha2 baseline and published external root
`bc69b019938c380fc80e579e410a3c260d0a20fc` but remain unexecuted pending
immutable container and provider locks. No model call has occurred.
Legacy agent reports are not evidence for this study.

## Protocol

Each task has a Merlo mirror and a Python 3.12 + pytest mirror. Ten tasks are
in each of deterministic CLI/data transformation, multi-module API migration,
and regression repair. Every pair is run three times in a fresh locked
workspace. The common tool menu is shell, read, search, edit, and test, with the
same model snapshot, decoding controls, token/time/iteration/tool limits,
container image, result clipping, and denied network. No semantic tools are
available to either arm: this study compares languages, not platforms. Prompt
differences are only `LANGUAGE`, `WORKSPACE`, and `TEST_COMMAND`; normalized
prompt hashes are identical.

The agent container mounts only its arm workspace. The task manifest, oracle
program, and acceptance cases stay in a separate trusted-runner mount and are
executed only after the agent stops. Every attempt records normalized pre/post
workspace maps containing relative path, kind, exact content hash, executable
bit, and symlink target. VCS state, caches, build output, and temporary files
are excluded. `changed_paths` is derived from the map delta.

The validator checks protocol and task roots, fixture/oracle hashes, prompt
parity, schedule and budgets, normalized workspace evidence, and complete
denominators. The trusted runner derives case count, pass/fail counts, and task
success from case IDs, expected values, actual values, and outcomes; a supplied
aggregate is never trusted independently.

A missing immutable provider revision or credential alias HMAC produces
`UNMEASURED_PROVIDER_IDENTITY_INCOMPLETE`; no provider identity or outcome is
synthesized. The HMAC uses a private random salt and local alias, never the API
key itself. A lock change stops the study as `INVALID_PROTOCOL_DEVIATION`.
Training-cutoff and no-network controls reduce but cannot prove absence of
memorization or provider contamination.

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
