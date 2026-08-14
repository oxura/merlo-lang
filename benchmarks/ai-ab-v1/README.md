# Merlo/Python same-model A/B v1

**Status: `DRAFT_UNRUN`.** This directory is the reviewable draft for a later
public preregistration. It contains 30 language-neutral task specifications,
expanded Merlo and Python mirrors, and black-box oracles. No provider request
has been made and no result is implied.

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
protocol_sha256 = 83e816d818341775d5ff6a1986af7723a21afd66388a1265944a24298cf5d3c0
tasks_sha256    = b339bdefd269564c82b0fabe559314a6157e6bcc1b667daa0a3d2e237342d75c
combined_root   = a0a606d5bf1c0fadff04700e6c16d34313bc02af57c5621fb7826eeef5cac7cb
```

`combined_root` is the SHA-256 of canonical JSON containing the two named
hashes. This is a draft-review fingerprint, not the external publication
anchor. The final root will be published only after the alpha2 commit, grammar,
compiler, container, provider revision, and calibration manifests are locked.

The runner validates `protocol.json`, `tasks.json`, every fixture, every oracle,
the schedule, and all denominators before any provider call. A missing locked
provider revision or key fingerprint is always
`UNMEASURED_PROVIDER_IDENTITY_INCOMPLETE`; it is never inferred and never
converted into a score. This branch intentionally does not contact a provider.

Both arms receive only the same text tools: shell, read, search, edit, and test.
This first experiment compares the languages under equal tooling; it does not
test SemanticWorld, HIR inspection, ChangeIR, or an AI-native tool advantage.
The agent sees only its arm workspace. Task manifests, oracle programs, and
acceptance cases are mounted only into the trusted runner after the agent stops.
Workspace digests include each relative path, file or symlink kind, exact
content hash, executable bit, and symlink target while excluding VCS, caches,
build output, and temporary files. The runner derives `changed_paths` and all
oracle aggregate counts from normalized evidence.

Credential evidence uses HMAC-SHA256 of a local key alias with a private random
salt. Neither a direct API-key hash nor the salt is published.

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

This draft has no provider execution command enabled; running the validator
alone is not an experiment and cannot establish an advantage claim.
