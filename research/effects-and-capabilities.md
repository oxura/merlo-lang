# Effects and capability boundaries

## Question

Can external operations remain visible in types and be narrowed at runtime without turning normal application code into dependency-injection plumbing?

## Hypothesis

Tasks that declare effects, combined with a capability manifest enforced by generated host guards, make ambient authority harder to introduce accidentally and keep tests deterministic.

## Method

The compiler distinguishes pure functions from tasks, propagates effect requirements through calls, rejects undeclared effects, and emits guards for filesystem, network, environment, clock, random, console, and FFI operations. Tests exercise compile-time rejection and runtime narrowing.

## Result

Within the alpha subset, pure functions cannot invoke host effects, tasks expose their effect set, missing capabilities fail before the host operation, filesystem and network access are narrowed to configured roots or destinations, and test handlers can replace supported services.

## Limitations

Capabilities do not make native code a sandbox. A compiler defect, unsafe FFI function, compromised C toolchain, or process escape remains outside the language-level boundary. Policies are local to one process and do not yet include distributed delegation.

## Artifacts

- `merlo/concise_application.py`
- `merlo/capability_experiment.py`
- `merlo/representation_c_backend.py`
- `tests/test_alpha_effects.py`
- `tests/test_meldra_capability_experiment.py`
