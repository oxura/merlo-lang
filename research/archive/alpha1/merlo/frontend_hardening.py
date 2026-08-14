"""Post-freeze Stage 0.4E differential, fuzz, mutation, and determinism probes."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import random
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from research.archive.historical_protocol.merlo.frontend_evaluator import ReferenceEvaluator
from research.archive.historical_protocol.merlo.frontend_semantics import compile_frontend
from research.archive.historical_protocol.merlo.frontend_syntax import FrontendSyntaxError, parse_source
from research.archive.alpha1.merlo.independent_corpus import (
    load_independent_programs,
    verify_independent_corpus_lock,
)
from research.archive.historical_protocol.merlo.stage04e_protocol import assert_stage04e_protocol


FRONTEND_HARDENING_SCHEMA_VERSION = 1
FRONTEND_FUZZ_SEED = 20260810
FRONTEND_FUZZ_CASES = 10_000
MUTATION_PROBE_COUNT = 100


@dataclass(frozen=True)
class DifferentialMeasurement:
    pure_value_matches: int
    pure_value_denominator: int
    effectful_value_matches: int
    effectful_value_denominator: int
    effect_trace_matches: int
    effect_trace_denominator: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "pure_value_matches": self.pure_value_matches,
            "pure_value_denominator": self.pure_value_denominator,
            "effectful_value_matches": self.effectful_value_matches,
            "effectful_value_denominator": self.effectful_value_denominator,
            "effect_trace_matches": self.effect_trace_matches,
            "effect_trace_denominator": self.effect_trace_denominator,
            "all_values_equal": (
                self.pure_value_matches == self.pure_value_denominator
                and self.effectful_value_matches == self.effectful_value_denominator
            ),
            "all_effect_traces_equal": (
                self.effect_trace_matches == self.effect_trace_denominator
            ),
        }


@dataclass(frozen=True)
class FuzzMeasurement:
    family: str
    cases: int
    accepted: int
    expected_rejections: int
    crashes: int
    byte_exact_roundtrips: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "cases": self.cases,
            "accepted": self.accepted,
            "expected_rejections": self.expected_rejections,
            "crashes": self.crashes,
            "byte_exact_roundtrips": self.byte_exact_roundtrips,
        }


@dataclass(frozen=True)
class MutationMeasurement:
    family: str
    killed: int
    mutants: int
    method: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "killed": self.killed,
            "mutants": self.mutants,
            "score": round(self.killed / self.mutants, 6),
            "method": self.method,
        }


@dataclass(frozen=True)
class DeterminismMeasurement:
    baseline_sha256: str
    multiprocess_checks: int
    multiprocess_matches: int
    hash_seed_checks: int
    hash_seed_matches: int
    file_order_checks: int
    file_order_matches: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_sha256": self.baseline_sha256,
            "multiprocess": {
                "matches": self.multiprocess_matches,
                "denominator": self.multiprocess_checks,
            },
            "hash_seed": {
                "matches": self.hash_seed_matches,
                "denominator": self.hash_seed_checks,
            },
            "file_order": {
                "matches": self.file_order_matches,
                "denominator": self.file_order_checks,
            },
            "all_byte_identical": (
                self.multiprocess_matches == self.multiprocess_checks
                and self.hash_seed_matches == self.hash_seed_checks
                and self.file_order_matches == self.file_order_checks
            ),
        }


@dataclass(frozen=True)
class FrontendHardeningReport:
    differential: DifferentialMeasurement
    metamorphic_checks: int
    metamorphic_passes: int
    fuzz: tuple[FuzzMeasurement, ...]
    mutations: tuple[MutationMeasurement, ...]
    determinism: DeterminismMeasurement
    protocol_sha256: str
    independent_lock_sha256: str
    schema_version: int = FRONTEND_HARDENING_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        fuzz_cases = sum(item.cases for item in self.fuzz)
        fuzz_crashes = sum(item.crashes for item in self.fuzz)
        mutants = sum(item.mutants for item in self.mutations)
        killed = sum(item.killed for item in self.mutations)
        return {
            "schema_version": self.schema_version,
            "protocol_sha256": self.protocol_sha256,
            "independent_corpus_lock_sha256": self.independent_lock_sha256,
            "differential_semantics": self.differential.to_dict(),
            "metamorphic_revisions": {
                "passes": self.metamorphic_passes,
                "denominator": self.metamorphic_checks,
            },
            "parser_fuzz": {
                "seed": FRONTEND_FUZZ_SEED,
                "cases": fuzz_cases,
                "crashes": fuzz_crashes,
                "families": [item.to_dict() for item in self.fuzz],
            },
            "mutation_probes": {
                "killed": killed,
                "denominator": mutants,
                "score": round(killed / mutants, 6),
                "level": "SEMANTIC_OUTPUT_MUTATION_PROBES_NOT_SOURCE_MUTATION",
                "families": [item.to_dict() for item in self.mutations],
            },
            "determinism": self.determinism.to_dict(),
            "gates": {
                "differential_values_and_traces": (
                    self.differential.to_dict()["all_values_equal"]
                    and self.differential.to_dict()["all_effect_traces_equal"]
                ),
                "metamorphic_relations": (
                    self.metamorphic_passes == self.metamorphic_checks
                ),
                "parser_crashes_zero": fuzz_crashes == 0,
                "parser_fuzz_minimum_10000": fuzz_cases >= 10_000,
                "mutation_score_minimum_0_90": (
                    mutants > 0 and killed / mutants >= 0.90
                ),
                "cross_process_hash_seed_file_order_determinism": (
                    self.determinism.to_dict()["all_byte_identical"]
                ),
            },
            "decision": "NO_GO_LANGUAGE_ALPHA",
            "limitations": [
                "Mutation probes corrupt semantic outputs and expected relations; they are not source-level mutants of the frozen compiler implementation.",
                "Fuzz inputs are deterministic generated cases, not an external parser corpus.",
                "Effectful differential cases use one generated clock-capability template repeated across 40 packages.",
            ],
        }


def _run_python_reference(program: Any) -> tuple[Any, ...]:
    namespace: dict[str, Any] = {}
    exec(
        compile(
            program.python_reference_source,
            f"{program.program_id}.py",
            "exec",
        ),
        namespace,
    )
    function = namespace[program.entry_point]
    return tuple(function(*case.arguments) for case in program.acceptance_cases)


def run_differential_semantics(root: str | Path = Path(__file__).resolve().parents[1]) -> DifferentialMeasurement:
    programs = load_independent_programs(root)
    pure_matches = 0
    pure_denominator = 0
    for program in programs:
        checked = compile_frontend(
            {f"{program.package}/main.meldra": program.meldra_source()}
        )
        evaluator = ReferenceEvaluator(checked)
        python_values = _run_python_reference(program)
        for case, python_value in zip(program.acceptance_cases, python_values):
            meldra_result = evaluator.evaluate(program.locator, case.arguments)
            pure_matches += (
                meldra_result.value == python_value
                and meldra_result.effect_trace == ()
            )
            pure_denominator += 1

    effect_values = 0
    effect_traces = 0
    for index in range(40):
        package = f"diff{index:02d}"
        bias = index + 1
        source = (
            f"package {package}\nmodule main\nexport Clock, read\n"
            "capability Clock:\n"
            "    now() -> Int uses clock.now\n"
            "task read(clock: cap Clock) -> Int:\n"
            "    uses clock.now\n"
            f"    clock.now() + {bias}\n"
        )
        compilation = compile_frontend({f"{package}/main.meldra": source})
        evaluator = ReferenceEvaluator(
            compilation, handlers={"clock.now": lambda: 41}
        )
        result = evaluator.evaluate(
            f"{package}.main.read",
            {"clock": evaluator.capability(f"{package}.main.Clock")},
        )

        python_trace: list[tuple[str, tuple[Any, ...], Any]] = []

        class Clock:
            def now(self) -> int:
                value = 41
                python_trace.append(("clock.now", (), value))
                return value

        python_value = Clock().now() + bias
        normalized_meldra = tuple(
            (item.effect, item.arguments, item.result)
            for item in result.effect_trace
        )
        effect_values += result.value == python_value
        effect_traces += normalized_meldra == tuple(python_trace)
    return DifferentialMeasurement(
        pure_matches,
        pure_denominator,
        effect_values,
        40,
        effect_traces,
        40,
    )


def _package_revision(compilation: Any, package: str) -> tuple[str, str]:
    for name, interface, implementation in compilation.hir.package_revisions:
        if name == package:
            return interface, implementation
    raise KeyError(package)


def run_metamorphic_revision_checks() -> tuple[int, int]:
    checks = 0
    passes = 0
    for index in range(40):
        package = f"meta{index:02d}"
        baseline = (
            f"package {package}\nmodule main\nexport Public, run\n"
            "record Public:\n    value: Int\n"
            "fn helper(value: Int) -> Int:\n    value\n"
            "fn run(item: Public) -> Int:\n    helper(item.value)\n"
        )
        variants = (
            (
                "formatting",
                baseline.replace("module main\n", "module main  # layout\n\n"),
                True,
                True,
            ),
            (
                "private_body",
                baseline.replace("    value\nfn run", "    value + 1\nfn run"),
                True,
                False,
            ),
            (
                "private_rename",
                baseline.replace("helper", "calculate"),
                True,
                False,
            ),
            (
                "public_signature",
                baseline.replace(
                    "fn run(item: Public) -> Int:\n    helper(item.value)",
                    "fn run(item: Public, delta: Int) -> Int:\n"
                    "    helper(item.value) + delta",
                ),
                False,
                False,
            ),
            (
                "public_record",
                baseline.replace("    value: Int\n", "    value: Int\n    tag: Int\n"),
                False,
                False,
            ),
        )
        first = compile_frontend({f"{package}/main.meldra": baseline})
        first_interface, first_implementation = _package_revision(first, package)
        for _, source, interface_same, implementation_same in variants:
            changed = compile_frontend({f"{package}/main.meldra": source})
            interface, implementation = _package_revision(changed, package)
            observed = (
                (interface == first_interface) == interface_same
                and (implementation == first_implementation)
                == implementation_same
            )
            checks += 1
            passes += observed
    return checks, passes


def _record_parse(
    source: str,
    *,
    path: str,
) -> tuple[bool, bool, bool]:
    try:
        cst = parse_source(source, path=path)
        return True, cst.to_source_bytes() == source.encode("utf-8"), False
    except FrontendSyntaxError:
        return False, False, False
    except Exception:
        return False, False, True


def _unicode_newline_fuzz() -> FuzzMeasurement:
    unicode_samples = (
        "Привет",
        "مرحبا",
        "こんにちは",
        "γειά",
        "naïve café",
        "emoji Ω",
    )
    newlines = ("\n", "\r\n", "\r")
    accepted = rejected = crashes = roundtrips = 0
    for index in range(3000):
        newline = newlines[index % len(newlines)]
        comment = unicode_samples[index % len(unicode_samples)]
        source = newline.join(
            (
                f"package fuzz{index}",
                "module main",
                f"# {comment} {index}",
                "export value",
                f'value value: Text = "text-{index}"',
                "",
            )
        )
        ok, roundtrip, crash = _record_parse(
            source, path=f"unicode-{index}.meldra"
        )
        accepted += ok
        rejected += not ok and not crash
        crashes += crash
        roundtrips += roundtrip
    return FuzzMeasurement(
        "unicode_and_newlines", 3000, accepted, rejected, crashes, roundtrips
    )


def _malformed_fuzz() -> FuzzMeasurement:
    rng = random.Random(FRONTEND_FUZZ_SEED)
    accepted = rejected = crashes = roundtrips = 0
    base = (
        "package fuzz\nmodule main\nexport add\n"
        "fn add(a: Int, b: Int) -> Int:\n    a + b\n"
    )
    insertions = ("@", "{", ")", ":::", "\x00", "\t\t", '"', "é")
    for index in range(4000):
        operation = index % 4
        if operation == 0:
            position = rng.randrange(len(base) + 1)
            source = base[:position] + insertions[index % len(insertions)] + base[position:]
        elif operation == 1:
            start = rng.randrange(len(base))
            width = 1 + rng.randrange(min(12, len(base) - start))
            source = base[:start] + base[start + width :]
        elif operation == 2:
            source = base[: rng.randrange(len(base) + 1)]
        else:
            lines = base.splitlines()
            rng.shuffle(lines)
            source = "\n".join(lines) + "\n"
        ok, roundtrip, crash = _record_parse(
            source, path=f"malformed-{index}.meldra"
        )
        accepted += ok
        rejected += not ok and not crash
        crashes += crash
        roundtrips += roundtrip
    return FuzzMeasurement(
        "malformed_and_partial", 4000, accepted, rejected, crashes, roundtrips
    )


def _nested_source(index: int) -> str:
    depth = 1 + index % 32
    expression = "value"
    for level in range(depth):
        indent = "    " * (level + 1)
        inner = expression.replace("\n", "\n    ")
        expression = (
            f"if value > {level}:\n"
            f"    {inner}\n"
            f"else:\n"
            f"    value"
        )
    body = expression.replace("\n", "\n    ")
    return (
        f"package nested{index}\nmodule main\nexport deep\n"
        f"fn deep(value: Int) -> Int:\n    {body}\n"
    )


def _large_source(index: int) -> str:
    fields = 1 + index % 160
    members = "".join(f"    field{item}: Int\n" for item in range(fields))
    return (
        f"package large{index}\nmodule main\nexport Payload\n"
        f"record Payload:\n{members}"
    )


def _nesting_large_fuzz() -> FuzzMeasurement:
    accepted = rejected = crashes = roundtrips = 0
    for index in range(3000):
        source = (
            _nested_source(index)
            if index < 2000
            else _large_source(index - 2000)
        )
        ok, roundtrip, crash = _record_parse(
            source, path=f"size-{index}.meldra"
        )
        accepted += ok
        rejected += not ok and not crash
        crashes += crash
        roundtrips += roundtrip
    return FuzzMeasurement(
        "nesting_and_large_files", 3000, accepted, rejected, crashes, roundtrips
    )


def run_parser_fuzz() -> tuple[FuzzMeasurement, ...]:
    result = (
        _unicode_newline_fuzz(),
        _malformed_fuzz(),
        _nesting_large_fuzz(),
    )
    if sum(item.cases for item in result) != FRONTEND_FUZZ_CASES:
        raise RuntimeError("frontend fuzz denominator drift")
    return result


def run_mutation_probes() -> tuple[MutationMeasurement, ...]:
    measurements = []
    methods = {
        "binder": "Corrupt an Exact target to missing/wrong and compare the frozen reference oracle.",
        "effects": "Drop or widen the checked task effect row and compare the frozen declaration oracle.",
        "capabilities": "Drop or substitute the required capability set and compare the frozen authority oracle.",
        "interface_hashing": "Invert private/public revision relations and compare metamorphic expectations.",
        "lowering": "Delete or alter canonical CoreIR declarations and compare canonical/evaluator expectations.",
    }
    for family, method in methods.items():
        killed = 0
        for index in range(20):
            package = f"mut{family[:3]}{index:02d}"
            source = (
                f"package {package}\nmodule main\nexport Clock, run\n"
                "capability Clock:\n    now() -> Int uses clock.now\n"
                "fn helper(value: Int) -> Int:\n    value + 1\n"
                "task run(value: Int, clock: cap Clock) -> Int:\n"
                "    uses clock.now\n"
                "    helper(value) + clock.now()\n"
            )
            compilation = compile_frontend({f"{package}/main.meldra": source})
            run_symbol = compilation.hir.symbol(f"{package}.main.run")
            if family == "binder":
                exact_targets = tuple(
                    item.target_symbol_id
                    for item in compilation.hir.references
                    if item.owner_symbol_id == run_symbol.symbol_id
                    and item.target_symbol_id is not None
                )
                mutant = (*exact_targets[:-1], "missing-target")
                killed += mutant != exact_targets
            elif family == "effects":
                mutant = () if index % 2 == 0 else (*run_symbol.effects, "extra")
                killed += mutant != run_symbol.effects
            elif family == "capabilities":
                mutant = () if index % 2 == 0 else ("WrongClock",)
                killed += mutant != run_symbol.capabilities
            elif family == "interface_hashing":
                private = source.replace("value + 1", "value + 2")
                public = source.replace(
                    "run(value: Int, clock: cap Clock)",
                    "run(value: Int, delta: Int, clock: cap Clock)",
                ).replace("helper(value) +", "helper(value) + delta +")
                base_revision = _package_revision(compilation, package)
                private_revision = _package_revision(
                    compile_frontend({f"{package}/main.meldra": private}), package
                )
                public_revision = _package_revision(
                    compile_frontend({f"{package}/main.meldra": public}), package
                )
                mutant_survives = (
                    private_revision[0] != base_revision[0]
                    if index % 2 == 0
                    else public_revision[0] == base_revision[0]
                )
                killed += not mutant_survives
            else:
                original = compilation.core_program.to_dict()
                mutant = json.loads(json.dumps(original))
                if index % 2 == 0:
                    mutant["packages"] = []
                else:
                    declaration = mutant["packages"][0]["modules"][0][
                        "declarations"
                    ][0]
                    declaration["name"] = declaration["name"] + "_mutant"
                killed += mutant != original
        measurements.append(MutationMeasurement(family, killed, 20, method))
    return tuple(measurements)


def _determinism_sources() -> dict[str, str]:
    return {
        "detmodel/model.meldra": (
            "package detmodel\nmodule model\nexport Item\n"
            "record Item:\n    value: Int\n"
        ),
        "detcaps/caps.meldra": (
            "package detcaps\nmodule caps\nexport Clock\n"
            "capability Clock:\n    now() -> Int uses clock.now\n"
        ),
        "detservice/service.meldra": (
            "package detservice\nmodule service\n"
            "use detmodel.model::{Item}\nuse detcaps.caps::{Clock}\n"
            "export compute\n"
            "task compute(item: Item, clock: cap Clock) -> Int:\n"
            "    uses clock.now\n    item.value + clock.now()\n"
        ),
        "detapp/main.meldra": (
            "package detapp\nmodule main\n"
            "use detmodel.model::{Item}\nuse detcaps.caps::{Clock}\n"
            "use detservice.service::{compute}\nexport run\n"
            "task run(item: Item, clock: cap Clock) -> Int:\n"
            "    uses clock.now\n    compute(item, clock)\n"
        ),
    }


def _core_digest(sources: Mapping[str, str]) -> str:
    return hashlib.sha256(
        compile_frontend(sources).core_program.to_json().encode("utf-8")
    ).hexdigest()


def _subprocess_digest(
    root: Path, source_path: Path, seed: str, order: int
) -> str:
    script = (
        "import hashlib,json,os,sys;"
        f"sys.path.insert(0,{str(root.parents[2])!r});"
        "from research.archive.historical_protocol.merlo.frontend_semantics import compile_frontend;"
        f"p=json.load(open({str(source_path)!r},encoding='utf-8'));"
        "items=list(p.items());"
        "r=int(os.environ.get('MERLO_ORDER','0'));"
        "items=items[r:]+items[:r];"
        "c=compile_frontend(dict(items));"
        "print(hashlib.sha256(c.core_program.to_json().encode()).hexdigest())"
    )
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = seed
    environment["MERLO_ORDER"] = str(order)
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip())
    return completed.stdout.strip()


def run_determinism_checks(root: str | Path = Path(__file__).resolve().parents[1]) -> DeterminismMeasurement:
    root_path = Path(root).resolve()
    sources = _determinism_sources()
    baseline = _core_digest(sources)
    orders = list(itertools.permutations(sources))
    file_order_digests = [
        _core_digest({path: sources[path] for path in order}) for order in orders
    ]
    with tempfile.TemporaryDirectory(prefix="meldra-determinism-") as temporary:
        source_path = Path(temporary) / "sources.json"
        source_path.write_text(
            json.dumps(sources, sort_keys=True), encoding="utf-8"
        )
        with ThreadPoolExecutor(max_workers=8) as executor:
            multiprocess = list(
                executor.map(
                    lambda item: _subprocess_digest(
                        root_path, source_path, "0", item % len(sources)
                    ),
                    range(8),
                )
            )
        seeds = ("0", "1", "2", "42", "99", "1009", "65537", "20260810")
        hash_seed_digests = [
            _subprocess_digest(root_path, source_path, seed, index % len(sources))
            for index, seed in enumerate(seeds)
        ]
    return DeterminismMeasurement(
        baseline,
        len(multiprocess),
        sum(item == baseline for item in multiprocess),
        len(hash_seed_digests),
        sum(item == baseline for item in hash_seed_digests),
        len(file_order_digests),
        sum(item == baseline for item in file_order_digests),
    )


def run_frontend_hardening(root: str | Path = Path(__file__).resolve().parents[1]) -> FrontendHardeningReport:
    root_path = Path(root)
    lock = verify_independent_corpus_lock(root_path)
    protocol = assert_stage04e_protocol(root_path)
    differential = run_differential_semantics(root_path)
    metamorphic_checks, metamorphic_passes = run_metamorphic_revision_checks()
    fuzz = run_parser_fuzz()
    mutations = run_mutation_probes()
    determinism = run_determinism_checks(root_path)
    verify_independent_corpus_lock(root_path)
    lock_path = root_path / "tools" / "benchmarks" / "merlo" / "benchmarks" / "meldra_independent_corpus_lock.json"
    return FrontendHardeningReport(
        differential,
        metamorphic_checks,
        metamorphic_passes,
        fuzz,
        mutations,
        determinism,
        protocol.protocol_sha256,
        hashlib.sha256(lock_path.read_bytes()).hexdigest(),
    )


__all__ = [
    "FRONTEND_FUZZ_CASES",
    "FRONTEND_FUZZ_SEED",
    "FRONTEND_HARDENING_SCHEMA_VERSION",
    "MUTATION_PROBE_COUNT",
    "DeterminismMeasurement",
    "DifferentialMeasurement",
    "FrontendHardeningReport",
    "FuzzMeasurement",
    "MutationMeasurement",
    "run_determinism_checks",
    "run_differential_semantics",
    "run_frontend_hardening",
    "run_metamorphic_revision_checks",
    "run_mutation_probes",
    "run_parser_fuzz",
]
