# Public native benchmark v1

The public benchmark is a fixed, executable observation—not a general
performance claim. From a clean clone on Linux x86-64:

```console
git clone https://github.com/oxura/merlo-lang.git
cd merlo-lang
python3 -m merlo benchmark --output ./merlo-benchmark-v1.json
```

The command has exactly one functional option: `--output PATH`. The repository
lock supplies all three checked-in workloads and all four required arms
(concise Merlo, canonical Merlo, C, and Python). It does not accept workload
filters, alternate corpora, seeds, timing counts, compiler flags, baseline
substitutions, or prior reports. Rust is optional and cannot replace C.

A successful command exits zero only for `status: "MEASURED"` and
`passed: true`. It writes a canonical JSON report for measured failures,
missing tools, invalid fixtures, failed builds, and invalid observations as
well; those outcomes exit one. Invalid command usage or an unwritable output
path exits two. Existing output is never replaced by a different report.

## Exact claim

> On these three checked-in, fixed Linux x86-64 workloads, with the exact compiler and C toolchain recorded in the report, both concise and canonical Merlo native executables produced the locked output checksum and their worst median end-to-end time was within the predeclared class-specific multiple of the C reference.

The report retains the lock, compiler-input tree, runner, Python and C
executable/version hashes, generated-C and executable hashes, controlled build
environment, schedule, and every raw observation. All three workloads are
always denominators: a missing sample, failed checksum, unavailable required
arm, fixture/source tamper, failed affinity application, or failed dispersion
or ratio gate cannot become a pass. Reports with different static source or
toolchain hashes are separate observations, not replications or regressions.

This is not a claim of general native performance, a ranking across languages,
a Rust result, or evidence outside these three fixtures. It is not an AI
A/B experiment; the future same-model study remains separately preregistered
and unrun.
