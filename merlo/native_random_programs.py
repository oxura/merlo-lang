"""Deterministic well-typed and invalid program corpus for Stage 0.6P."""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .frontend_semantics import check_frontend
from .native_c_backend import CEmitter, compile_c_source
from .native_differential import evaluate_hir, evaluate_mir, evaluate_surface
from .native_hir import compile_native_hir, lower_native_hir_to_performance
from .performance_frontend import PerformanceCompileError
from .performance_opt import OPTIMIZATION_PIPELINE


NATIVE_RANDOM_SCHEMA_VERSION = 2
VALID_PROGRAM_COUNT = 5000
INVALID_PROGRAM_COUNT = 2000
VALID_SEEDS = (0x600D0001, 0x600D0002, 0x600D0003, 0x600D0004, 0x600D0005)
INVALID_SEEDS = (0xBAD00001, 0xBAD00002)
_MASK64 = (1 << 64) - 1


@dataclass(frozen=True)
class GeneratedProgram:
    id: str
    seed: int
    family: str
    source: str
    argument: int
    expected: int

    def to_dict(self, *, include_source: bool = False) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "seed": self.seed,
            "family": self.family,
            "argument": self.argument,
            "expected": self.expected,
            "source_sha256": hashlib.sha256(self.source.encode("utf-8")).hexdigest(),
        }
        if include_source:
            payload["source"] = self.source
        return payload


@dataclass(frozen=True)
class InvalidProgram:
    id: str
    seed: int
    family: str
    source: str
    expected_error: str
    adapter: str = "native"

    def to_dict(self, *, include_source: bool = False) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "seed": self.seed,
            "family": self.family,
            "expected_error": self.expected_error,
            "adapter": self.adapter,
            "source_sha256": hashlib.sha256(self.source.encode("utf-8")).hexdigest(),
        }
        if include_source:
            payload["source"] = self.source
        return payload


def _u64(value: int) -> int:
    return value & _MASK64


def _constants(rng: random.Random) -> tuple[int, int, int, int]:
    return tuple(rng.randrange(1, 1 << 16) for _ in range(4))


def _valid_source(index: int, seed: int, family_index: int) -> tuple[str, str, int, int]:
    rng = random.Random(seed ^ (index * 0x9E3779B1))
    a, b, c, d = _constants(rng)
    argument = rng.getrandbits(32)
    family = (
        "arithmetic",
        "branch",
        "while_loop",
        "for_loop",
        "nested_control",
        "direct_call",
        "record",
        "array",
        "move",
        "early_return",
        "bounds_safe",
        "shared_value",
        "slice",
        "borrow",
        "match",
        "mutable_borrow",
    )[family_index % 16]
    if family == "arithmetic":
        expected = _u64(_u64(argument * a + b) ^ c)
        source = f"""fn main(n: UInt64) -> UInt64:
    (n * {a} + {b}) ^ {c}
"""
    elif family == "branch":
        expected = _u64(argument + a) if argument & 1 == 0 else _u64(argument ^ b)
        source = f"""fn main(n: UInt64) -> UInt64:
    var result: UInt64 = n
    if n & 1 == 0:
        result = n + {a}
    else:
        result = n ^ {b}
    result
"""
    elif family == "while_loop":
        expected = c
        for value in range(argument & 15):
            expected = _u64(expected * (a | 1) + value)
        source = f"""fn main(n: UInt64) -> UInt64:
    var i: UInt64 = 0
    var result: UInt64 = {c}
    while i < (n & 15):
        result = result * {a | 1} + i
        i = i + 1
    result
"""
    elif family == "for_loop":
        expected = b
        for value in range(argument & 31):
            expected = _u64(expected + (value ^ a))
        source = f"""fn main(n: UInt64) -> UInt64:
    var result: UInt64 = {b}
    for i in 0..(n & 31):
        result = result + (i ^ {a})
    result
"""
    elif family == "nested_control":
        expected = d
        for outer in range(argument & 7):
            for inner in range(a & 7):
                if (outer + inner) & 1:
                    expected = _u64(expected + b)
                else:
                    expected = _u64(expected ^ c)
        source = f"""fn main(n: UInt64) -> UInt64:
    var result: UInt64 = {d}
    for outer in 0..(n & 7):
        for inner in 0..({a} & 7):
            if (outer + inner) & 1 == 1:
                result = result + {b}
            else:
                result = result ^ {c}
    result
"""
    elif family == "direct_call":
        expected = _u64(_u64(argument + a) * b)
        source = f"""fn helper_{index}(value: UInt64) -> UInt64:
    (value + {a}) * {b}

fn main(n: UInt64) -> UInt64:
    helper_{index}(n)
"""
    elif family == "record":
        expected = _u64(_u64(argument + a) ^ _u64(argument * b + c))
        source = f"""record Pair_{index}:
    left: UInt64
    right: UInt64

fn main(n: UInt64) -> UInt64:
    let pair: Pair_{index} = Pair_{index}(left=n + {a}, right=n * {b} + {c})
    pair.left ^ pair.right
"""
    elif family == "array":
        values = (a, b, c, d)
        expected = _u64(values[argument & 3] + argument)
        source = f"""fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 4] = [{a}, {b}, {c}, {d}]
    values[n & 3] + n
"""
    elif family == "move":
        values = (a, b, c, d)
        expected = _u64(values[argument & 3] ^ argument)
        source = f"""fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 4] = [{a}, {b}, {c}, {d}]
    let moved: Array[UInt64, 4] = move(values)
    moved[n & 3] ^ n
"""
    elif family == "early_return":
        expected = _u64(argument + a) if argument & 1 == 0 else _u64(argument ^ b)
        source = f"""fn main(n: UInt64) -> UInt64:
    if n & 1 == 0:
        return n + {a}
    n ^ {b}
"""
    elif family == "bounds_safe":
        values = (a, b, c, d)
        expected = _u64(values[argument % 4] * c)
        source = f"""fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 4] = [{a}, {b}, {c}, {d}]
    var result: UInt64 = 0
    for i in 0..len(values):
        if i == n % 4:
            result = values[i] * {c}
    result
"""
    elif family == "shared_value":
        expected = _u64(argument * 3 + a + d)
        source = f"""fn main(n: UInt64) -> UInt64:
    let values: Shared[Array[UInt64, 4]] = [n + {a}, {b}, {c}, n * 2 + {d}]
    let result: UInt64 = values[0] + values[3]
    drop(values)
    result
"""
    elif family == "slice":
        expected = _u64(a + b + c + d + argument)
        source = f"""fn sum_{index}(values: Slice[UInt64]) -> UInt64:
    var result: UInt64 = 0
    for i in 0..len(values):
        result = result + values[i]
    result

fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 4] = [{a}, {b}, {c}, {d}]
    sum_{index}(values) + n
"""
    elif family == "borrow":
        expected = _u64(b + argument)
        source = f"""fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 4] = [{a}, {b}, {c}, {d}]
    let view: Array[UInt64, 4] = borrow(values)
    view[1] + n
"""
    elif family == "match":
        expected = _u64(argument + a) if argument & 1 == 0 else _u64(argument + b)
        source = f"""fn main(n: UInt64) -> UInt64:
    var result: UInt64 = n
    match n & 1:
        case 0:
            result = n + {a}
        case _:
            result = n + {b}
    result
"""
    else:
        expected = _u64(a + argument)
        source = f"""fn main(n: UInt64) -> UInt64:
    var values: Array[UInt64, 4] = [{a}, {b}, {c}, {d}]
    let view: Array[UInt64, 4] = borrow_mut(values)
    view[0] = view[0] + n
    values[0]
"""
    return family, source, argument, expected


def generate_valid_programs(count: int = VALID_PROGRAM_COUNT) -> tuple[GeneratedProgram, ...]:
    if count < 1:
        raise ValueError("valid generated program count must be positive")
    programs = []
    per_seed = (count + len(VALID_SEEDS) - 1) // len(VALID_SEEDS)
    for index in range(count):
        seed = VALID_SEEDS[min(index // per_seed, len(VALID_SEEDS) - 1)]
        family, source, argument, expected = _valid_source(index, seed, index)
        programs.append(
            GeneratedProgram(
                f"valid-{index:05d}", seed, family, source, argument, expected
            )
        )
    return tuple(programs)


def _invalid_source(index: int, seed: int, family_index: int) -> InvalidProgram:
    family = (
        "use_after_move",
        "double_move",
        "invalid_borrow",
        "out_of_scope",
        "type_mismatch",
        "invalid_mutation",
        "missing_drop_path",
        "shared_cycle",
        "capability_effect_mismatch",
    )[family_index % 9]
    if family == "use_after_move":
        source = """fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 2] = [1, 2]
    let moved: Array[UInt64, 2] = move(values)
    values[0] + moved[0] + n
"""
        expected = "UseAfterMove"
        adapter = "native"
    elif family == "double_move":
        source = """fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 2] = [1, 2]
    let first: Array[UInt64, 2] = move(values)
    let second: Array[UInt64, 2] = move(values)
    first[0] + second[0] + n
"""
        expected = "DoubleMove"
        adapter = "native"
    elif family == "invalid_borrow":
        source = """fn main(n: UInt64) -> UInt64:
    let values: Array[UInt64, 2] = [1, 2]
    let view: Array[UInt64, 2] = borrow_mut(values)
    view[0] + n
"""
        expected = "InvalidBorrow"
        adapter = "native"
    elif family == "out_of_scope":
        source = """fn main(n: UInt64) -> UInt64:
    if n > 0:
        let local: UInt64 = n
    local
"""
        expected = "OutOfScope"
        adapter = "native"
    elif family == "type_mismatch":
        source = """fn main(n: UInt64) -> UInt64:
    let value: Bool = n
    n
"""
        expected = "TypeMismatch"
        adapter = "native"
    elif family == "invalid_mutation":
        source = """fn main(n: UInt64) -> UInt64:
    let value: UInt64 = n
    value = 1
    value
"""
        expected = "InvalidMutation"
        adapter = "native"
    elif family == "missing_drop_path":
        source = """# ownership: explicit
fn main(n: UInt64) -> UInt64:
    let values: Shared[Array[UInt64, 2]] = [n, 2]
    values[0]
"""
        expected = "MissingDropPath"
        adapter = "native"
    elif family == "shared_cycle":
        source = """record Node:
    value: UInt64
    next: Node

fn main(n: UInt64) -> UInt64:
    n
"""
        expected = "SharedCycleUnsupported"
        adapter = "native"
    else:
        source = f"""package generated.case{index}
export bad
fn bad(value: Int) -> Int:
    uses network.read
    value
"""
        expected = "CapabilityEffectMismatch"
        adapter = "stage04"
    return InvalidProgram(
        f"invalid-{index:05d}", seed, family, source, expected, adapter
    )


def generate_invalid_programs(count: int = INVALID_PROGRAM_COUNT) -> tuple[InvalidProgram, ...]:
    if count < 1:
        raise ValueError("invalid generated program count must be positive")
    programs = []
    per_seed = (count + len(INVALID_SEEDS) - 1) // len(INVALID_SEEDS)
    for index in range(count):
        seed = INVALID_SEEDS[min(index // per_seed, len(INVALID_SEEDS) - 1)]
        programs.append(_invalid_source(index, seed, index))
    return tuple(programs)


def _diagnostic_kind(message: str) -> str:
    lowered = message.lower()
    if "use after move" in lowered:
        return "UseAfterMove"
    if "invalid mutable borrow" in lowered:
        return "InvalidBorrow"
    if "out-of-scope" in lowered or "unknown value" in lowered:
        return "OutOfScope"
    if "type mismatch" in lowered:
        return "TypeMismatch"
    if "immutable let" in lowered or "invalid mutation" in lowered:
        return "InvalidMutation"
    if "missing drop path" in lowered:
        return "MissingDropPath"
    if "sharedcycleunsupported" in lowered:
        return "SharedCycleUnsupported"
    return "UnknownDiagnostic"


def validate_invalid_program(program: InvalidProgram) -> tuple[bool, str, str]:
    if program.adapter == "stage04":
        result = check_frontend({f"{program.id}.meldra": program.source})
        codes = [item.code for item in result.diagnostics]
        observed = "CapabilityEffectMismatch" if codes else "Accepted"
        return observed == program.expected_error, observed, ",".join(codes)
    try:
        compile_native_hir(program.source)
    except PerformanceCompileError as exc:
        message = str(exc)
        observed = _diagnostic_kind(message)
        if program.family == "double_move" and observed == "UseAfterMove":
            observed = "DoubleMove"
        return observed == program.expected_error, observed, message
    return False, "Accepted", "program unexpectedly compiled"


def _rename_entry(source: str, name: str) -> str:
    return source.replace("fn main(", f"fn {name}(", 1)


def _batch_source(programs: Iterable[GeneratedProgram]) -> tuple[str, tuple[GeneratedProgram, ...]]:
    values = tuple(programs)
    declarations = [
        _rename_entry(item.source, f"case_{index}")
        for index, item in enumerate(values)
    ]
    declarations.append(
        "fn main(n: UInt64) -> UInt64:\n    case_0(n)\n"
    )
    return "\n".join(declarations), values


def run_valid_corpus(
    programs: Iterable[GeneratedProgram],
    *,
    artifact_dir: str | Path,
    native_batch_size: int = 500,
) -> dict[str, Any]:
    values = tuple(programs)
    destination = Path(artifact_dir)
    destination.mkdir(parents=True, exist_ok=True)
    mismatches = []
    per_pass_mismatches = []
    observation_mismatches = []
    ownership_balance_failures = []
    execution_level_totals: dict[str, dict[str, Any]] = {}

    def record_level(name: str, observation: Any) -> None:
        totals = execution_level_totals.setdefault(
            name,
            {
                "observations": 0,
                "errors": 0,
                "allocations": 0,
                "drops": 0,
                "retains": 0,
                "releases": 0,
                "steps": 0,
                "final_ownership_states": {},
            },
        )
        totals["observations"] += 1
        totals["errors"] += observation.status != "OK"
        totals["allocations"] += observation.allocations
        totals["drops"] += observation.drops
        totals["retains"] += observation.retains
        totals["releases"] += observation.releases
        totals["steps"] += observation.steps
        states = totals["final_ownership_states"]
        for state, count in observation.final_ownership_state:
            states[state] = states.get(state, 0) + count
    family_counts: dict[str, int] = {}
    native_results = 0
    native_mismatches = 0
    started = time.perf_counter_ns()
    for index, program in enumerate(values):
        family_counts[program.family] = family_counts.get(program.family, 0) + 1
        hir = compile_native_hir(program.source, path=f"generated/{program.id}.meldra")
        surface_result = evaluate_surface(
            program.source,
            (program.argument,),
            path=f"generated/{program.id}.meldra",
        )
        hir_result = evaluate_hir(hir, (program.argument,))
        mir = lower_native_hir_to_performance(hir)
        mir_result = evaluate_mir(mir, (program.argument,))
        record_level("surface", surface_result)
        record_level("hir", hir_result)
        record_level("mir_unoptimized", mir_result)
        if (
            surface_result.return_value != program.expected
            or hir_result.return_value != program.expected
            or mir_result.return_value != program.expected
        ):
            mismatches.append(
                {
                    "id": program.id,
                    "family": program.family,
                    "expected": program.expected,
                    "hir": hir_result.to_dict(),
                    "mir": mir_result.to_dict(),
                }
            )
        if surface_result.semantic_key() != hir_result.semantic_key():
            observation_mismatches.append(
                {
                    "id": program.id,
                    "levels": ["surface", "hir"],
                    "surface": surface_result.to_dict(),
                    "hir": hir_result.to_dict(),
                }
            )
        if hir_result.semantic_key() != mir_result.semantic_key():
            observation_mismatches.append(
                {
                    "id": program.id,
                    "levels": ["hir", "mir_unoptimized"],
                    "hir": hir_result.to_dict(),
                    "mir": mir_result.to_dict(),
                }
            )
        current = mir
        for pass_function in OPTIMIZATION_PIPELINE:
            before_result = evaluate_mir(current, (program.argument,))
            after, _statistics = pass_function(current)
            after_result = evaluate_mir(after, (program.argument,))
            record_level(f"after_{pass_function.__name__}", after_result)
            semantic_fields = (
                "status",
                "return_value",
                "printed_checksum",
                "error_kind",
                "effect_trace",
            )
            if any(
                getattr(before_result, field) != getattr(after_result, field)
                for field in semantic_fields
            ):
                per_pass_mismatches.append(
                    {
                        "id": program.id,
                        "pass": pass_function.__name__,
                        "before": before_result.to_dict(),
                        "after": after_result.to_dict(),
                    }
                )
            current = after
        final_result = evaluate_mir(current, (program.argument,))
        record_level("mir_optimized", final_result)
        if (
            program.family == "shared_value"
            and dict(final_result.final_ownership_state).get("Dropped", 0) < 1
        ):
            ownership_balance_failures.append(
                {
                    "id": program.id,
                    "family": program.family,
                    "observation": final_result.to_dict(),
                }
            )
    interpreter_elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000

    batch_artifacts = []
    for batch_index, offset in enumerate(range(0, len(values), native_batch_size)):
        batch = values[offset : offset + native_batch_size]
        combined_source, batch_values = _batch_source(batch)
        hir = compile_native_hir(
            combined_source,
            path=f"generated/batch_{batch_index}.meldra",
        )
        mir = lower_native_hir_to_performance(hir)
        current = mir
        for pass_function in OPTIMIZATION_PIPELINE:
            current, _statistics = pass_function(current)
        c_source = CEmitter(current, executable=False).emit()
        wrapper = [
            "int main(int argc, char **argv) {",
            "    if (argc != 2) return 2;",
            "    uint64_t base = strtoull(argv[1], NULL, 10);",
        ]
        for local_index, program in enumerate(batch_values):
            derived = program.argument
            wrapper.append(
                f'    printf("%" PRIu64 "\\n", (uint64_t)meldra_fn_case_{local_index}(UINT64_C({derived})));'
            )
        wrapper.extend(("    return 0;", "}"))
        c_source += "\n".join(wrapper) + "\n"
        batch_dir = destination / f"batch_{batch_index:02d}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        (batch_dir / "programs.meldra").write_text(combined_source, encoding="utf-8")
        build = compile_c_source(c_source, output_dir=batch_dir, stem="program")
        if build.status != "MEASURED" or build.binary_path is None:
            native_mismatches += len(batch_values)
            batch_artifacts.append(
                {
                    "batch": batch_index,
                    "status": build.status,
                    "stderr": build.stderr,
                    "count": len(batch_values),
                }
            )
            continue
        completed = subprocess.run(
            (build.binary_path, "0"),
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        output = completed.stdout.strip().splitlines()
        if completed.returncode != 0 or len(output) != len(batch_values):
            native_mismatches += len(batch_values)
        else:
            for program, raw in zip(batch_values, output, strict=True):
                native_results += 1
                if int(raw) != program.expected:
                    native_mismatches += 1
                    mismatches.append(
                        {
                            "id": program.id,
                            "family": program.family,
                            "expected": program.expected,
                            "native": int(raw),
                        }
                    )
        batch_artifacts.append(
            {
                "batch": batch_index,
                "status": "MEASURED" if completed.returncode == 0 else "FAILED",
                "count": len(batch_values),
                "source_sha256": build.source_sha256,
                "binary_sha256": build.binary_sha256,
                "compile_time_ms": build.compile_time_ms,
                "binary_size": build.binary_size,
            }
        )
    return {
        "schema_version": NATIVE_RANDOM_SCHEMA_VERSION,
        "valid_programs": len(values),
        "families": dict(sorted(family_counts.items())),
        "seeds": list(VALID_SEEDS),
        "interpreter_mismatches": mismatches,
        "interpreter_mismatch_count": len(mismatches),
        "observation_mismatches": observation_mismatches,
        "observation_mismatch_count": len(observation_mismatches),
        "per_pass_mismatches": per_pass_mismatches,
        "per_pass_mismatch_count": len(per_pass_mismatches),
        "execution_level_totals": {
            name: {
                **totals,
                "final_ownership_states": dict(
                    sorted(totals["final_ownership_states"].items())
                ),
            }
            for name, totals in sorted(execution_level_totals.items())
        },
        "ownership_balance_failures": ownership_balance_failures,
        "ownership_balance_failure_count": len(ownership_balance_failures),
        "native_results": native_results,
        "native_mismatches": native_mismatches,
        "interpreter_elapsed_ms": interpreter_elapsed_ms,
        "native_batches": batch_artifacts,
    }


def run_invalid_corpus(programs: Iterable[InvalidProgram]) -> dict[str, Any]:
    values = tuple(programs)
    family_counts: dict[str, int] = {}
    failures = []
    observed_counts: dict[str, int] = {}
    for program in values:
        family_counts[program.family] = family_counts.get(program.family, 0) + 1
        ok, observed, detail = validate_invalid_program(program)
        observed_counts[observed] = observed_counts.get(observed, 0) + 1
        if not ok:
            failures.append(
                {
                    "id": program.id,
                    "family": program.family,
                    "expected": program.expected_error,
                    "observed": observed,
                    "detail": detail,
                }
            )
    return {
        "schema_version": NATIVE_RANDOM_SCHEMA_VERSION,
        "invalid_programs": len(values),
        "families": dict(sorted(family_counts.items())),
        "seeds": list(INVALID_SEEDS),
        "observed_diagnostics": dict(sorted(observed_counts.items())),
        "failures": failures,
        "failure_count": len(failures),
    }


def write_program_manifests(
    valid: Iterable[GeneratedProgram],
    invalid: Iterable[InvalidProgram],
    *,
    destination: str | Path,
) -> dict[str, Any]:
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    valid_values = tuple(valid)
    invalid_values = tuple(invalid)
    valid_manifest = [item.to_dict() for item in valid_values]
    invalid_manifest = [item.to_dict() for item in invalid_values]
    (root / "valid_manifest.json").write_text(
        json.dumps(valid_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "invalid_manifest.json").write_text(
        json.dumps(invalid_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "valid_count": len(valid_values),
        "invalid_count": len(invalid_values),
        "valid_manifest_sha256": hashlib.sha256(
            (root / "valid_manifest.json").read_bytes()
        ).hexdigest(),
        "invalid_manifest_sha256": hashlib.sha256(
            (root / "invalid_manifest.json").read_bytes()
        ).hexdigest(),
    }


__all__ = [
    "INVALID_PROGRAM_COUNT",
    "INVALID_SEEDS",
    "NATIVE_RANDOM_SCHEMA_VERSION",
    "VALID_PROGRAM_COUNT",
    "VALID_SEEDS",
    "GeneratedProgram",
    "InvalidProgram",
    "generate_invalid_programs",
    "generate_valid_programs",
    "run_invalid_corpus",
    "run_valid_corpus",
    "validate_invalid_program",
    "write_program_manifests",
]
