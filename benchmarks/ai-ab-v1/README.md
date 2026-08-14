# Merlo/Python same-model A/B v1

**Status: `PREREGISTERED_UNRUN`.** This directory is a public, immutable
preregistration for a later controlled workflow experiment. It contains 30
language-neutral task specifications, expanded Merlo and Python mirrors, and
black-box oracles. No provider request has been made and no result is implied.

## Locked design

- 30 tasks: 10 deterministic CLI/data transformations, 10 multi-module API
  migrations, and 10 regression repairs.
- Three fixed replicates per task produce 90 paired observations and 180 arm
  attempts. `tasks.json` contains the complete seeded schedule.
- Each pair contains an editable `main.mlo` Merlo source or `main.py` Python
  source, its public input data, and an independent oracle. The oracle executes
  that exact arm source through the locked toolchain command against three
  nontrivial embedded cases; there is no shared runner or hidden solution.
- Every arm starts with an intentionally incomplete implementation. A
  whitespace-only edit cannot satisfy the oracle. Correct implementations are
  supplied only by later isolated test doubles, never in committed fixtures.
- The per-arm allowlist permits only that arm's source (`main.mlo` or
  `main.py`); input, oracle cases, oracle, and schedule locks are immutable.

The fixture and oracle SHA-256 values are fully materialized in the manifest.

The preregistration root is:

```text
protocol_sha256 = a4d8468f8e7b42f1b8849c9da10a868597d2c848481717604d5f977b2ffbea21
tasks_sha256    = e996fd273644ef8f369b6e7c5f45f5656b281f01a9569704d512bcb90a211e79
combined_root   = f051064cd214e4447658ee86d904c3a7fb04079818302c69017b8194ff2272d1
```

`combined_root` is the SHA-256 of canonical JSON containing the two named
hashes. The validator pins both component hashes independently; the public Git
merge commit is the external publication anchor. Changing either document and
recomputing its self-hash therefore remains a protocol deviation.

The runner validates `protocol.json`, `tasks.json`, every fixture, every oracle,
the schedule, and all denominators before any provider call. A missing locked
provider revision or key fingerprint is always
`UNMEASURED_PROVIDER_IDENTITY_INCOMPLETE`; it is never inferred and never
converted into a score. This branch intentionally does not contact a provider.

## Claims and stopping rules

The only potentially eligible later statement is restricted to the exact model
revision, public task set, container, and protocol, and requires all 90 pairs to
be measured and valid, a paired success difference of at least +10 percentage
points with a positive 95% paired-bootstrap lower bound, and an upper 95% CI
below 0.85 for at least one of median total-token or wall-time ratios, with no
regressions or out-of-scope edits in successful attempts. Otherwise the result
is `MEASURED_INCONCLUSIVE` or `MEASURED_NO_ADVANTAGE`, not a superiority claim.

A provider/model/container/task/oracle/prompt lock change stops the study with
`INVALID_PROTOCOL_DEVIATION`; an amendment requires a new v2 preregistration.
Retries are allowed only for independently logged infrastructure failures
before any model output or token, and both paired arms must be rerun. The
provider must document a training cutoff predating publication of these
fixtures and network retrieval remains disabled. That reduces but cannot prove
absence of memorization or provider contamination.

## Later execution boundary

After a locked provider revision, key fingerprint, and execution image are
published in a new immutable preregistration, the exact future command is:

```console
python3 -m merlo.ai_ab --root benchmarks/ai-ab-v1 --provider-config LOCKED_PROVIDER_CONFIG --output ai-ab-v1-report.json
```

This v1 branch has no provider execution command enabled; running the validator
alone is not an experiment and cannot establish an advantage claim.
