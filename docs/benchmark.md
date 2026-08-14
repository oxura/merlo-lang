# Public native benchmark v1

The public benchmark is source-checkout tooling, not a production `merlo`
command and not part of the wheel workflow. From a clean Linux x86-64 clone:

```console
git clone https://github.com/oxura/merlo-lang.git
cd merlo-lang
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python3 -c 'from tools.benchmarks.merlo.public_benchmark import run_public_benchmark; run_public_benchmark(".", output="./merlo-benchmark-v1.json")'
```

The callable above is the active entry point under
`tools/benchmarks/merlo/public_benchmark.py`; it accepts the repository root
and an optional report path. Do not replace it with a `merlo benchmark`
subcommand: no such production route is published.

The runner has one report destination option at the Python API boundary:
`output`. The repository lock supplies all three checked-in workloads and all
four required arms (concise Merlo, canonical Merlo, C, and Python). It does not
accept workload filters, alternate corpora, seeds, timing counts, compiler
flags, baseline substitutions, prior reports, or Rust as a replacement for C.

A successful report has `status: "MEASURED"` and `passed: true`. The runner
writes canonical JSON for measured failures, missing tools, invalid fixtures,
failed builds, and invalid observations as well; those outcomes are evidence
of an unmeasured or invalid run, not a pass. Existing output is never replaced
by a different report.


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
