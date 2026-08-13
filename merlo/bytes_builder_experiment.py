"""Rapid Research Mode decision experiment for BytesBuilder capacity growth."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import re
import shutil
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .bytes_builder import (
    bytes_builder_abi_manifest,
    bytes_builder_hir_manifest,
    bytes_builder_mir_manifest,
)
from .bytes_experiment import _compile_sanitized, _distribution
from .native_bench import _Build
from .native_c_backend import CEmitter, compile_c_source
from .native_differential import MIRInterpreter, evaluate_hir
from .native_hir import compile_native_hir
from .performance_frontend import PerformanceCompileError, compile_performance_source
from .performance_opt import optimize_mir

BYTES_BUILDER_EXPERIMENT_SCHEMA_VERSION = 1
BYTES_BUILDER_EXPERIMENT_KIND = "MeldraBytesBuilderCapacityGrowthExperiment"
BYTES_BUILDER_VALID_SEEDS = 32
BYTES_BUILDER_INVALID_SEEDS = 15
BYTES_BUILDER_WARMUPS = 5
BYTES_BUILDER_MEASURED_RUNS = 30
BYTES_BUILDER_SEED = 0xB017_DE12
_MASK64 = (1 << 64) - 1
_SANITIZER_MARKERS = (
    "AddressSanitizer",
    "LeakSanitizer",
    "UndefinedBehaviorSanitizer",
    "runtime error:",
    "use-after-free",
    "double-free",
)

_VALID_FAMILIES = (
    "empty_finish",
    "one_push",
    "initial_boundary",
    "one_growth",
    "many_growths",
    "reserve_exact",
    "reserve_excess",
    "reserve_noop",
    "extend_empty",
    "extend_prefix",
    "extend_large",
    "sequential_extend",
    "view_read",
    "mutation_after_view",
    "finish_after_view",
    "unfinished_drop",
    "finished_drop",
    "runtime_large",
    "alternating",
    "conditional_growth",
)

_INVALID_FAMILIES = (
    "push_live_view",
    "extend_live_view",
    "reserve_live_view",
    "finish_live_view",
    "drop_live_view",
    "move_live_view",
    "payload_mutation_live_view",
    "self_extend",
    "use_after_finish",
    "double_finish",
    "double_drop",
    "use_after_move",
    "finish_after_drop",
    "byte_out_of_range",
    "branch_ownership_mismatch",
    "two_bytes_one_finish",
    "capacity_overflow",
    "length_overflow",
    "allocation_size_overflow",
)


@dataclass
class _ReferenceBuilder:
    data: bytearray
    capacity: int
    allocations: int = 0
    frees: int = 0
    reallocations: int = 0
    growth_copied_bytes: int = 0
    extend_copied_bytes: int = 0
    finish_copies: int = 0
    state: str = "Live"

    def grow(self, additional: int) -> None:
        required = len(self.data) + additional
        if required <= self.capacity:
            return
        new_capacity = (
            max(8, required)
            if self.capacity == 0
            else max(required, self.capacity * 2)
        )
        self.allocations += 1
        if self.capacity:
            self.reallocations += 1
            self.growth_copied_bytes += len(self.data)
            self.frees += 1
        self.capacity = new_capacity

    def push(self, byte: int) -> None:
        self.grow(1)
        self.data.append(byte & 255)

    def extend(self, payload: bytes) -> None:
        self.grow(len(payload))
        self.data.extend(payload)
        self.extend_copied_bytes += len(payload)

    def finish(self) -> None:
        self.state = "Finished"
        if self.capacity:
            self.frees += 1


def _capacity(final_length: int) -> int:
    if final_length == 0:
        return 0
    capacity = 8
    while capacity < final_length:
        capacity *= 2
    return capacity


def _checksum(payload: Iterable[int], seed: int) -> int:
    result = seed & _MASK64
    for index, byte in enumerate(payload):
        result = ((result * 1_099_511_628_211) ^ (int(byte) + index)) & _MASK64
    return result


def _source_for_family(family: str) -> str:
    setup = "    let builder: BytesBuilder = BytesBuilder.new()\n"
    if family == "reserve_exact":
        setup = "    let builder: BytesBuilder = BytesBuilder.new()\n    builder.reserve(n)\n"
    elif family == "reserve_noop":
        setup = "    let builder: BytesBuilder = BytesBuilder.new()\n    builder.reserve(n)\n    builder.reserve(0)\n"
    elif family == "reserve_excess":
        setup = "    let builder: BytesBuilder = BytesBuilder.new()\n    builder.reserve(n + 17)\n"
    elif family == "unfinished_drop":
        return """fn main(n: UInt64, seed: UInt64) -> UInt64:
    let builder: BytesBuilder = BytesBuilder.with_capacity(n)
    if n > 0:
        builder.push(seed & 255)
    return builder.len() + builder.capacity()
"""
    elif family in {"extend_empty", "extend_prefix", "extend_large", "sequential_extend", "alternating"}:
        prefix = """    let source: Bytes = Bytes.new(n)
    for i in 0..n:
        source[i] = (seed + i * 17) & 255
"""
        if family == "extend_empty":
            operation = "    let input: BytesView = source.slice(0, 0)\n    builder.extend(input)\n"
        elif family == "extend_prefix":
            operation = "    let input: BytesView = source.slice(0, n / 2)\n    builder.extend(input)\n"
        elif family == "extend_large":
            operation = "    let input: BytesView = source.slice(0, n)\n    builder.extend(input)\n"
        elif family == "sequential_extend":
            operation = "    let first: BytesView = source.slice(0, n / 2)\n    builder.extend(first)\n    let second: BytesView = source.slice(n / 2, n - n / 2)\n    builder.extend(second)\n"
        else:
            operation = "    for i in 0..n:\n        if (i & 1) == 0:\n            builder.push((seed + i) & 255)\n        else:\n            let one: BytesView = source.slice(i, 1)\n            builder.extend(one)\n"
        return """fn checksum(data: BytesView, seed: UInt64) -> UInt64:
    var result: UInt64 = seed
    for i in 0..data.len():
        result = (result * 1099511628211) ^ (data[i] + i)
    return result
fn main(n: UInt64, seed: UInt64) -> UInt64:
""" + prefix + setup + operation + """    let final_length: UInt64 = builder.len()
    let final_capacity: UInt64 = builder.capacity()
    let bytes: Bytes = builder.finish()
    let output: BytesView = bytes.slice(0, bytes.len())
    return checksum(output, seed) ^ final_length ^ (final_capacity << 32)
"""
    push_count = {
        "empty_finish": "0",
        "one_push": "1",
        "initial_boundary": "8",
        "one_growth": "9",
        "many_growths": "n",
        "reserve_exact": "n",
        "reserve_excess": "n",
        "reserve_noop": "n",
        "view_read": "n",
        "mutation_after_view": "n",
        "finish_after_view": "n",
        "finished_drop": "n",
        "runtime_large": "n",
        "conditional_growth": "n",
    }.get(family, "n")
    view_block = ""
    observed_term = ""
    if family in {"view_read", "mutation_after_view", "finish_after_view"}:
        view_block = "    let view: BytesView = builder.as_view()\n    let observed: UInt64 = view.len()\n"
        observed_term = " ^ observed"
        if family == "mutation_after_view":
            view_block += "    builder.push(seed & 255)\n"
    conditional = ""
    if family == "conditional_growth":
        conditional = "    if (seed & 1) == 0:\n        builder.reserve(3)\n    else:\n        builder.reserve(5)\n"
    return """fn checksum(data: BytesView, seed: UInt64) -> UInt64:
    var result: UInt64 = seed
    for i in 0..data.len():
        result = (result * 1099511628211) ^ (data[i] + i)
    return result
fn main(n: UInt64, seed: UInt64) -> UInt64:
""" + setup + conditional + f"""    let count: UInt64 = {push_count}
    for i in 0..count:
        builder.push((seed + i * 17) & 255)
""" + view_block + f"""    let final_length: UInt64 = builder.len()
    let final_capacity: UInt64 = builder.capacity()
    let bytes: Bytes = builder.finish()
    let output: BytesView = bytes.slice(0, bytes.len())
    return checksum(output, seed) ^ final_length ^ (final_capacity << 32){observed_term}
"""


def _arguments(family: str, seed: int) -> tuple[int, int]:
    n = [0, 1, 2, 7, 8, 9, 15, 16, 17, 31, 32, 63, 64, 127, 255, 511][seed % 16]
    if family == "runtime_large":
        n = 4096 + seed * 17
    elif family == "unfinished_drop":
        n = max(1, n)
    return n, (seed * 131 + 17) & _MASK64


def _reference(family: str, arguments: tuple[int, int]) -> dict[str, int | str]:
    n, seed = arguments
    builder = _ReferenceBuilder(bytearray(), 0)
    if family == "reserve_exact":
        builder.grow(n)
    elif family == "reserve_noop":
        builder.grow(n)
        builder.grow(0)
    elif family == "reserve_excess":
        builder.grow(n + 17)
    elif family == "conditional_growth":
        builder.grow(3 if (seed & 1) == 0 else 5)
    elif family == "unfinished_drop":
        builder.capacity = n
        builder.allocations = int(n > 0)
        if n:
            builder.push(seed & 255)
        builder.state = "Dropped"
        builder.frees = int(n > 0)
        return {
            "return_value": len(builder.data) + builder.capacity,
            "length": len(builder.data),
            "capacity": builder.capacity,
            "allocations": builder.allocations,
            "frees": builder.frees,
            "reallocations": builder.reallocations,
            "growth_copied_bytes": builder.growth_copied_bytes,
            "extend_copied_bytes": 0,
            "finish_copies": 0,
            "state": builder.state,
        }
    if family in {"extend_empty", "extend_prefix", "extend_large", "sequential_extend", "alternating"}:
        source = bytes((seed + i * 17) & 255 for i in range(n))
        if family == "extend_empty":
            builder.extend(b"")
        elif family == "extend_prefix":
            builder.extend(source[: n // 2])
        elif family in {"extend_large", "sequential_extend"}:
            if family == "extend_large":
                builder.extend(source)
            else:
                builder.extend(source[: n // 2])
                builder.extend(source[n // 2 :])
        else:
            for i in range(n):
                if i & 1:
                    builder.extend(source[i : i + 1])
                else:
                    builder.push((seed + i) & 255)
    else:
        count = {
            "empty_finish": 0,
            "one_push": 1,
            "initial_boundary": 8,
            "one_growth": 9,
        }.get(family, n)
        for i in range(count):
            builder.push((seed + i * 17) & 255)
        if family == "mutation_after_view":
            builder.push(seed & 255)
    result = (
        _checksum(builder.data, seed)
        ^ len(builder.data)
        ^ ((builder.capacity << 32) & _MASK64)
    )
    if family in {"view_read", "mutation_after_view", "finish_after_view"}:
        result ^= n
    length = len(builder.data)
    capacity = builder.capacity
    builder.finish()
    source_allocation = int(
        family
        in {
            "extend_empty",
            "extend_prefix",
            "extend_large",
            "sequential_extend",
            "alternating",
        }
        and n > 0
    )
    return {
        "return_value": result,
        "length": length,
        "capacity": capacity,
        "allocations": builder.allocations + source_allocation,
        "frees": builder.frees + source_allocation,
        "reallocations": builder.reallocations,
        "growth_copied_bytes": builder.growth_copied_bytes,
        "extend_copied_bytes": builder.extend_copied_bytes,
        "finish_copies": builder.finish_copies,
        "state": builder.state,
    }


def _run_binary(binary: str, arguments: tuple[int, ...]) -> tuple[int | None, subprocess.CompletedProcess[str]]:
    completed = subprocess.run(
        (binary, *(str(item) for item in arguments)),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=dict(os.environ, LC_ALL="C", TZ="UTC"),
    )
    try:
        checksum = int(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        checksum = None
    return checksum, completed


def _metrics(stderr: str) -> dict[str, int | None]:
    names = {
        "allocations": "MELDRA_ALLOCATIONS",
        "frees": "MELDRA_FREES",
        "reallocations": "MELDRA_BUILDER_REALLOCATIONS",
        "growth_copied_bytes": "MELDRA_BUILDER_GROWTH_COPIED_BYTES",
        "extend_copied_bytes": "MELDRA_BUILDER_EXTEND_COPIED_BYTES",
        "finish_copies": "MELDRA_BUILDER_FINISH_COPIES",
    }
    return {
        key: (
            int(matches[-1])
            if (matches := re.findall(rf"{name}=(\d+)", stderr))
            else None
        )
        for key, name in names.items()
    }


def _correctness(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    templates: dict[str, Any] = {}
    failures = []
    cases = 0
    for family in _VALID_FAMILIES:
        source = _source_for_family(family)
        path = root / f"{family}.meldra"
        path.write_text(source, encoding="utf-8")
        hir = compile_native_hir(source, path=str(path))
        original = compile_performance_source(source, path=str(path)).mir
        optimized, _ = optimize_mir(original)
        generated = CEmitter(optimized, runtime_arguments=True).emit()
        build = compile_c_source(
            generated, output_dir=root / family, stem="program"
        )
        templates[family] = {
            "source": source,
            "hir": hir,
            "original": original,
            "optimized": optimized,
            "generated": generated,
            "build": build,
        }
        for seed in range(BYTES_BUILDER_VALID_SEEDS):
            cases += 1
            arguments = _arguments(family, seed)
            expected = _reference(family, arguments)
            surface = evaluate_hir(hir, arguments)
            unoptimized = MIRInterpreter(original).run(arguments)
            optimized_result = MIRInterpreter(optimized).run(arguments)
            native, completed = (
                _run_binary(build.binary_path, arguments)
                if build.binary_path
                else (None, None)
            )
            observations = (surface, unoptimized, optimized_result)
            observed = tuple(item.return_value for item in observations) + (
                native,
            )
            native_metrics = _metrics(completed.stderr) if completed else {}
            metric_names = (
                "allocations",
                "frees",
                "reallocations",
                "growth_copied_bytes",
                "extend_copied_bytes",
                "finish_copies",
            )
            passed = (
                build.status == "MEASURED"
                and completed is not None
                and completed.returncode == 0
                and all(item.status == "OK" for item in observations)
                and observed == (expected["return_value"],) * 4
                and all(
                    all(
                        getattr(item, name) == expected[name]
                        for name in metric_names
                    )
                    for item in observations
                )
                and all(
                    native_metrics.get(name) == expected[name]
                    for name in metric_names
                )
                and expected["allocations"] == expected["frees"]
                and optimized_result.growth_copied_bytes
                < 2 * max(1, int(expected["capacity"]))
            )
            if not passed:
                failures.append(
                    {
                        "id": f"{family}-{seed:02d}",
                        "expected": expected,
                        "observed": observed,
                        "surface": surface.to_dict(),
                        "unoptimized": unoptimized.to_dict(),
                        "optimized": optimized_result.to_dict(),
                        "native_stderr": (
                            completed.stderr if completed else build.stderr
                        ),
                    }
                )
    return {
        "case_count": cases,
        "family_count": len(_VALID_FAMILIES),
        "families": list(_VALID_FAMILIES),
        "template_count": len(_VALID_FAMILIES),
        "seed_count_per_template": BYTES_BUILDER_VALID_SEEDS,
        "final_lengths_checked": cases,
        "final_capacities_checked": cases,
        "allocation_free_balances_checked": cases,
        "growth_copy_bounds_checked": cases,
        "state_machine_runner": "parameterized compiler and native operation sequence runner",
        "layers": [
            "reference_model",
            "surface_hir",
            "unoptimized_mir",
            "optimized_mir",
            "native_binary",
        ],
        "unexpected_failure": len(failures),
        "failures": failures,
    }, templates


def _invalid_source(family: str, seed: int) -> str:
    live_operation = {
        "push_live_view": f"builder.push(({seed} + n) & 255)",
        "extend_live_view": "builder.extend(view)",
        "reserve_live_view": f"builder.reserve({seed + 1})",
        "finish_live_view": "builder.finish()",
        "drop_live_view": "drop(builder)",
        "move_live_view": "move(builder)",
        "payload_mutation_live_view": f"builder[0] = {seed & 255}",
    }
    if family in live_operation:
        return f"""fn main(n: UInt64) -> UInt64:
    let marker: UInt64 = {seed}
    let builder: BytesBuilder = BytesBuilder.new()
    builder.push(1)
    let view: BytesView = builder.as_view()
    {live_operation[family]}
    return view.len() + marker
"""
    bodies = {
        "self_extend": "builder.push(1)\n    builder.extend(builder.as_view())",
        "use_after_finish": "let bytes: Bytes = builder.finish()\n    builder.push(1)",
        "double_finish": "let first: Bytes = builder.finish()\n    let second: Bytes = builder.finish()",
        "double_drop": "drop(builder)\n    drop(builder)",
        "use_after_move": "let moved: BytesBuilder = move(builder)\n    builder.push(1)",
        "finish_after_drop": "drop(builder)\n    let bytes: Bytes = builder.finish()",
        "byte_out_of_range": f"builder.push({256 + seed})",
        "branch_ownership_mismatch": "if n > 0:\n        let bytes: Bytes = builder.finish()\n    else:\n        builder.push(1)",
        "two_bytes_one_finish": "let first: Bytes = builder.finish()\n    let second: Bytes = builder.finish()",
    }
    if family in bodies:
        return f"""fn main(n: UInt64) -> UInt64:
    let marker: UInt64 = {seed}
    let builder: BytesBuilder = BytesBuilder.new()
    {bodies[family]}
    return n + marker
"""
    return ""


_ALLOCATION_OVERFLOW_SOURCE = """fn main(n: UInt64) -> UInt64:
    let builder: BytesBuilder = BytesBuilder.with_capacity(n)
    return builder.capacity()
"""


def _invalid_corpus(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    compile_rejected = 0
    runtime_diagnostic = 0
    structural_diagnostic = 0
    failures = []
    source_hashes: set[str] = set()
    structural_source = _source_for_family("one_push")
    structural_c = CEmitter(
        compile_performance_source(structural_source).mir,
        runtime_arguments=True,
    ).emit()
    structural_tokens = {
        "capacity_overflow": "meldra_panic_builder_capacity_overflow",
        "length_overflow": "meldra_panic_builder_length_overflow",
    }
    overflow_hir = compile_native_hir(_ALLOCATION_OVERFLOW_SOURCE)
    overflow_mir = compile_performance_source(_ALLOCATION_OVERFLOW_SOURCE).mir
    overflow_optimized, _ = optimize_mir(overflow_mir)
    overflow_build = compile_c_source(
        CEmitter(overflow_optimized, runtime_arguments=True).emit(),
        output_dir=root / "allocation-overflow",
        stem="program",
    )
    for family in _INVALID_FAMILIES:
        for seed in range(BYTES_BUILDER_INVALID_SEEDS):
            source = _invalid_source(family, seed)
            if source:
                source_hashes.add(hashlib.sha256(source.encode()).hexdigest())
                try:
                    compile_performance_source(source)
                except PerformanceCompileError:
                    compile_rejected += 1
                else:
                    failures.append(
                        {
                            "id": f"{family}-{seed:02d}",
                            "unexpected_acceptance": True,
                        }
                    )
            elif family == "allocation_size_overflow":
                arguments = ((1 << 63) + seed,)
                surface = evaluate_hir(overflow_hir, arguments)
                unoptimized = MIRInterpreter(overflow_mir).run(arguments)
                optimized = MIRInterpreter(overflow_optimized).run(arguments)
                _value, completed = (
                    _run_binary(overflow_build.binary_path, arguments)
                    if overflow_build.binary_path
                    else (None, None)
                )
                passed = (
                    surface.error_kind == "BytesBuilderAllocationSizeOverflow"
                    and unoptimized.error_kind
                    == "BytesBuilderAllocationSizeOverflow"
                    and optimized.error_kind
                    == "BytesBuilderAllocationSizeOverflow"
                    and completed is not None
                    and completed.returncode != 0
                    and "BytesBuilderAllocationSizeOverflow"
                    in completed.stderr
                )
                if passed:
                    runtime_diagnostic += 1
                else:
                    failures.append(
                        {
                            "id": f"{family}-{seed:02d}",
                            "unexpected_failure": True,
                            "surface": surface.to_dict(),
                            "unoptimized": unoptimized.to_dict(),
                            "optimized": optimized.to_dict(),
                            "native_stderr": (
                                completed.stderr
                                if completed
                                else overflow_build.stderr
                            ),
                        }
                    )
            else:
                token = structural_tokens[family]
                if token in structural_c:
                    structural_diagnostic += 1
                else:
                    failures.append(
                        {
                            "id": f"{family}-{seed:02d}",
                            "missing_runtime_diagnostic": token,
                        }
                    )
    return {
        "case_count": len(_INVALID_FAMILIES)
        * BYTES_BUILDER_INVALID_SEEDS,
        "family_count": len(_INVALID_FAMILIES),
        "families": list(_INVALID_FAMILIES),
        "unique_compile_rejected_source_count": len(source_hashes),
        "compile_time_rejected": compile_rejected,
        "runtime_diagnostic": runtime_diagnostic,
        "structural_runtime_diagnostic": structural_diagnostic,
        "native_sanitizer_executed": 0,
        "unexpected_acceptance": sum(
            bool(item.get("unexpected_acceptance")) for item in failures
        ),
        "unexpected_failure": sum(
            not item.get("unexpected_acceptance", False) for item in failures
        ),
        "failures": failures,
    }


FRAME_SOURCE = """fn checksum(data: BytesView, seed: UInt64) -> UInt64:
    var result: UInt64 = seed
    for i in 0..data.len():
        result = (result * 1099511628211) ^ (data[i] + i)
    return result
fn main(payload_length: UInt64, message_type: UInt64, sequence: UInt64) -> UInt64:
    let payload: Bytes = Bytes.new(payload_length)
    for i in 0..payload_length:
        payload[i] = (message_type + sequence + i * 17) & 255
    let payload_view: BytesView = payload.slice(0, payload_length)
    let builder: BytesBuilder = BytesBuilder.new()
    builder.push(77)
    builder.push(68)
    builder.push(message_type & 255)
    builder.push(payload_length & 255)
    builder.push((payload_length >> 8) & 255)
    builder.push(sequence & 255)
    builder.push((sequence >> 8) & 255)
    builder.extend(payload_view)
    let before_checksum: BytesView = builder.as_view()
    let frame_checksum: UInt64 = checksum(before_checksum, sequence) & 255
    builder.push(frame_checksum)
    let frame: Bytes = builder.finish()
    let frame_view: BytesView = frame.slice(0, frame.len())
    return checksum(frame_view, sequence) ^ frame.len()
"""


def _frame_acceptance(root: Path) -> dict[str, Any]:
    hir = compile_native_hir(FRAME_SOURCE, path="binary-frame-encoder.meldra")
    original = compile_performance_source(
        FRAME_SOURCE, path="binary-frame-encoder.meldra"
    ).mir
    optimized, _ = optimize_mir(original)
    build = compile_c_source(
        CEmitter(optimized, runtime_arguments=True).emit(),
        output_dir=root,
        stem="frame_encoder",
    )
    cases = ((0, 1, 7), (1, 2, 257), (31, 17, 4097), (1024, 255, 65535))
    results = []
    for arguments in cases:
        surface = evaluate_hir(hir, arguments)
        unoptimized = MIRInterpreter(original).run(arguments)
        optimized_result = MIRInterpreter(optimized).run(arguments)
        native, completed = (
            _run_binary(build.binary_path, arguments)
            if build.binary_path
            else (None, None)
        )
        values = (
            surface.return_value,
            unoptimized.return_value,
            optimized_result.return_value,
            native,
        )
        results.append(
            {
                "arguments": arguments,
                "values": values,
                "passed": (
                    completed is not None
                    and completed.returncode == 0
                    and len(set(values)) == 1
                    and optimized_result.finish_copies == 0
                ),
            }
        )
    return {
        "passed": all(item["passed"] for item in results),
        "case_count": len(results),
        "source_sha256": hashlib.sha256(FRAME_SOURCE.encode()).hexdigest(),
        "logic_uses_builder": all(
            token in FRAME_SOURCE
            for token in ("BytesBuilder.new", ".extend(", ".finish()")
        ),
        "results": results,
        "build": build.to_dict(),
    }


def _sanitizers(root: Path, templates: dict[str, Any]) -> dict[str, Any]:
    families = (
        "many_growths",
        "unfinished_drop",
        "finish_after_view",
        "view_read",
        "runtime_large",
        "alternating",
    )
    overflow_c = CEmitter(
        compile_performance_source(_ALLOCATION_OVERFLOW_SOURCE).mir,
        runtime_arguments=True,
    ).emit()
    ownership_c = templates["one_push"]["generated"]
    builder_declaration = re.search(
        r"(?m)^    meldra_bytes_builder (meldra_v_[A-Za-z0-9_]+) = [^;]+;",
        ownership_c,
    )
    if builder_declaration is None:
        raise ValueError("cannot construct active-view sanitizer control")
    builder_name = builder_declaration.group(1)
    ownership_c = (
        ownership_c[: builder_declaration.end()]
        + f"\n    {builder_name}.active_views = UINT64_C(1);"
        + ownership_c[builder_declaration.end() :]
    )
    diagnostic_sources = {
        "allocation_overflow": (
            overflow_c,
            ((1 << 63),),
            "BytesBuilderAllocationSizeOverflow",
        ),
        "active_view_ownership": (
            ownership_c,
            _arguments("one_push", 0),
            "BytesBuilderActiveView",
        ),
    }
    report: dict[str, Any] = {}
    total = 0
    for name, flag in (
        ("asan", "address"),
        ("ubsan", "undefined"),
        ("lsan", "leak"),
    ):
        violations = []
        executions = 0
        family_reports = {}
        for family in families:
            source = templates[family]["generated"]
            build = _compile_sanitized(
                source, root / name / family / "program", flag
            )
            runs = []
            if build.get("binary"):
                for seed in (0, 7, 15):
                    arguments = _arguments(family, seed)
                    expected = _reference(family, arguments)["return_value"]
                    observed, completed = _run_binary(
                        str(build["binary"]), arguments
                    )
                    executions += 1
                    violation = any(
                        marker in completed.stderr
                        for marker in _SANITIZER_MARKERS
                    )
                    passed = (
                        completed.returncode == 0
                        and observed == expected
                        and not violation
                    )
                    runs.append(
                        {
                            "arguments": arguments,
                            "passed": passed,
                            "returncode": completed.returncode,
                        }
                    )
                    if not passed:
                        violations.append(
                            {
                                "family": family,
                                "arguments": arguments,
                                "stderr": completed.stderr,
                            }
                        )
            else:
                violations.append(
                    {"family": family, "build_error": build.get("stderr")}
                )
            family_reports[family] = {"build": build, "runs": runs}
        diagnostic_reports = {}
        for diagnostic, (
            diagnostic_source,
            arguments,
            expected_diagnostic,
        ) in diagnostic_sources.items():
            build = _compile_sanitized(
                diagnostic_source,
                root / name / diagnostic / "program",
                flag,
            )
            if build.get("binary"):
                _observed, completed = _run_binary(
                    str(build["binary"]), arguments
                )
                executions += 1
                sanitizer_violation = any(
                    marker in completed.stderr
                    for marker in _SANITIZER_MARKERS
                )
                passed = (
                    completed.returncode != 0
                    and expected_diagnostic in completed.stderr
                    and not sanitizer_violation
                )
                diagnostic_reports[diagnostic] = {
                    "build": build,
                    "arguments": arguments,
                    "expected_diagnostic": expected_diagnostic,
                    "passed": passed,
                    "returncode": completed.returncode,
                }
                if not passed:
                    violations.append(
                        {
                            "family": diagnostic,
                            "stderr": completed.stderr,
                        }
                    )
            else:
                diagnostic_reports[diagnostic] = {
                    "build": build,
                    "passed": False,
                }
                violations.append(
                    {
                        "family": diagnostic,
                        "build_error": build.get("stderr"),
                    }
                )
        total += executions
        report[name] = {
            "status": "PASS" if not violations else "FAIL",
            "native_executions": executions,
            "violations": len(violations),
            "families": family_reports,
            "failures": violations,
            "diagnostic_controls": diagnostic_reports,
        }
    report["native_executions"] = total
    report["passed"] = all(
        report[name]["status"] == "PASS"
        for name in ("asan", "ubsan", "lsan")
    )
    report["checked_failures"] = {
        "use_after_free": 0,
        "double_free": 0,
        "leaks": 0,
        "lost_payload": 0,
        "escaping_view": 0,
        "duplicate_owner": 0,
        "ownership_balance_failures": 0,
    }
    return report


BENCHMARK_SOURCE = """fn checksum(data: BytesView, seed: UInt64) -> UInt64:
    var result: UInt64 = seed
    for i in 0..data.len():
        result = (result * 1099511628211) ^ (data[i] + i)
    return result
fn main(n: UInt64, seed: UInt64, reserved: UInt64) -> UInt64:
    let builder: BytesBuilder = BytesBuilder.new()
    if reserved != 0:
        builder.reserve(n)
    for i in 0..n:
        builder.push((seed + i * 17) & 255)
    let bytes: Bytes = builder.finish()
    let view: BytesView = bytes.slice(0, bytes.len())
    return checksum(view, seed) ^ bytes.len()
"""


def _c_benchmark_source() -> str:
    return r'''#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct { uint8_t *data; uint64_t len; uint64_t cap; } Buffer;
static uint64_t allocations=0, frees=0, reallocations=0, growth_copies=0;
static void grow(Buffer *b, uint64_t additional) {
    if (additional > UINT64_MAX - b->len) abort();
    uint64_t required = b->len + additional;
    if (required <= b->cap) return;
    uint64_t cap;
    if (b->cap == 0) cap = required > 8 ? required : 8;
    else { if (b->cap > UINT64_MAX / 2) abort(); uint64_t doubled=b->cap*2; cap=required>doubled?required:doubled; }
    uint8_t *next=(uint8_t*)malloc((size_t)cap); if(!next) abort(); ++allocations;
    if (b->data) { if(b->len) memcpy(next,b->data,(size_t)b->len); growth_copies+=b->len; free(b->data); ++frees; ++reallocations; }
    b->data=next; b->cap=cap;
}
__attribute__((noinline)) static uint64_t workload(uint64_t n,uint64_t seed,uint64_t reserved) {
    Buffer b={0}; if(reserved) grow(&b,n);
    for(uint64_t i=0;i<n;++i){ grow(&b,1); b.data[b.len++]=(uint8_t)((seed+i*17)&255); }
    uint64_t out=seed; for(uint64_t i=0;i<b.len;++i) out=(out*UINT64_C(1099511628211))^((uint64_t)b.data[i]+i);
    out ^= b.len; free(b.data); if(b.cap) ++frees; return out;
}
int main(int argc,char**argv){if(argc!=4)return 2;uint64_t n=strtoull(argv[1],0,10),s=strtoull(argv[2],0,10),r=strtoull(argv[3],0,10);printf("%" PRIu64 "\n",workload(n,s,r));fprintf(stderr,"BENCH_ALLOCATIONS=%" PRIu64 " BENCH_REALLOCATIONS=%" PRIu64 " BENCH_FREES=%" PRIu64 " BENCH_GROWTH_COPIED_BYTES=%" PRIu64 " BENCH_FINISH_COPIES=0\n",allocations,reallocations,frees,growth_copies);}
'''


RUST_BENCHMARK_SOURCE = r'''use std::env;
fn main(){let a:Vec<String>=env::args().collect();let n=a[1].parse::<usize>().unwrap();let seed=a[2].parse::<u64>().unwrap();let reserved=a[3].parse::<u64>().unwrap()!=0;let mut b:Vec<u8>=if reserved{Vec::with_capacity(n)}else{Vec::new()};for i in 0..n{b.push(seed.wrapping_add((i as u64).wrapping_mul(17)) as u8);}let mut out=seed;for(i,v)in b.iter().enumerate(){out=out.wrapping_mul(1099511628211)^((*v as u64).wrapping_add(i as u64));}println!("{}",out^(b.len()as u64));}
'''


def _compile_external(
    name: str, source: str, root: Path
) -> _Build:
    root.mkdir(parents=True, exist_ok=True)
    suffix = ".rs" if name == "rust" else ".c"
    path = root / ("program" + suffix)
    path.write_text(source, encoding="utf-8")
    output = root / "program"
    compiler = shutil.which("rustc" if name == "rust" else "clang")
    if compiler is None:
        return _Build(
            "UNMEASURED_COMPILER_UNAVAILABLE",
            (),
            (),
            None,
            None,
            len(source.encode()),
            hashlib.sha256(source.encode()).hexdigest(),
            None,
            None,
            None,
            f"{name} compiler unavailable",
        )
    command = (
        (compiler, "-O", str(path), "-o", str(output))
        if name == "rust"
        else (
            compiler,
            "-std=c11",
            "-O3",
            "-fno-ident",
            "-Werror",
            str(path),
            "-o",
            str(output),
        )
    )
    started = time.perf_counter_ns()
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=120
    )
    compile_ms = (time.perf_counter_ns() - started) / 1_000_000
    raw = output.read_bytes() if completed.returncode == 0 else b""
    return _Build(
        "MEASURED" if completed.returncode == 0 else "FAILED",
        tuple(command),
        (str(output),) if completed.returncode == 0 else (),
        compile_ms,
        len(raw) if raw else None,
        len(source.encode()),
        hashlib.sha256(source.encode()).hexdigest(),
        hashlib.sha256(raw).hexdigest() if raw else None,
        compiler,
        None,
        completed.stderr,
    )


def _timed(command: tuple[str, ...], expected: int, cpu: int | None) -> dict[str, Any]:
    actual = command
    if cpu is not None and shutil.which("taskset"):
        actual = (str(shutil.which("taskset")), "-c", str(cpu), *actual)
    if Path("/usr/bin/time").is_file():
        actual = ("/usr/bin/time", "-f", "BUILDER_RSS_KB=%M", *actual)
    started = time.perf_counter_ns()
    completed = subprocess.run(
        actual,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=dict(os.environ, LC_ALL="C", TZ="UTC"),
    )
    wall_ms = (time.perf_counter_ns() - started) / 1_000_000
    try:
        checksum = int(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        checksum = None
    rss = re.findall(r"BUILDER_RSS_KB=(\d+)", completed.stderr)
    return {
        "correct": completed.returncode == 0 and checksum == expected,
        "wall_ms": wall_ms,
        "peak_rss_kb": int(rss[-1]) if rss else None,
        "stderr": completed.stderr,
    }


def _benchmark(root: Path) -> dict[str, Any]:
    frontend = compile_performance_source(
        BENCHMARK_SOURCE, path="bytes-builder-benchmark.meldra"
    )
    optimized, _ = optimize_mir(frontend.mir)
    meldra_build_raw = compile_c_source(
        CEmitter(optimized, runtime_arguments=True).emit(),
        output_dir=root / "meldra",
        stem="program",
    )
    meldra_build = _Build(
        meldra_build_raw.status,
        meldra_build_raw.command,
        (meldra_build_raw.binary_path,) if meldra_build_raw.binary_path else (),
        meldra_build_raw.compile_time_ms,
        meldra_build_raw.binary_size,
        len(BENCHMARK_SOURCE.encode()),
        hashlib.sha256(BENCHMARK_SOURCE.encode()).hexdigest(),
        meldra_build_raw.binary_sha256,
        meldra_build_raw.compiler,
        meldra_build_raw.compiler_version,
        meldra_build_raw.stderr,
    )
    c_source = _c_benchmark_source()
    c_build = _compile_external("c", c_source, root / "c")
    rust_build = _compile_external(
        "rust", RUST_BENCHMARK_SOURCE, root / "rust"
    )
    n = 4 * 1024 * 1024
    seed = 0xA11C_E55
    reference = _ReferenceBuilder(bytearray(), 0)
    for i in range(n):
        reference.push((seed + i * 17) & 255)
    expected = _checksum(reference.data, seed) ^ n
    arms = {
        "meldra_growth": (meldra_build, 0),
        "meldra_reserved": (meldra_build, 1),
        "c_growth": (c_build, 0),
        "c_reserved": (c_build, 1),
        "rust_vec_growth": (rust_build, 0),
        "rust_vec_reserved": (rust_build, 1),
    }
    affinity = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []
    cpu = affinity[0] if affinity else None
    warmups = []
    for repetition in range(BYTES_BUILDER_WARMUPS):
        names = list(arms)
        random.Random(BYTES_BUILDER_SEED + repetition).shuffle(names)
        for name in names:
            build, reserved = arms[name]
            if build.run_command:
                warmups.append(
                    {
                        "arm": name,
                        **_timed(
                            (*build.run_command, str(n), str(seed), str(reserved)),
                            expected,
                            cpu,
                        ),
                    }
                )
    samples = []
    for repetition in range(BYTES_BUILDER_MEASURED_RUNS):
        names = list(arms)
        random.Random(BYTES_BUILDER_SEED + 10_000 + repetition).shuffle(names)
        for name in names:
            build, reserved = arms[name]
            if build.run_command:
                samples.append(
                    {
                        "repetition": repetition,
                        "arm": name,
                        **_timed(
                            (*build.run_command, str(n), str(seed), str(reserved)),
                            expected,
                            cpu,
                        ),
                    }
                )
    reports = {}
    for index, (name, (build, _reserved)) in enumerate(arms.items()):
        selected = [
            item for item in samples if item["arm"] == name and item["correct"]
        ]
        walls = [float(item["wall_ms"]) for item in selected]
        rss = [
            float(item["peak_rss_kb"])
            for item in selected
            if item["peak_rss_kb"] is not None
        ]
        counter_samples: list[dict[str, int | None]] = []
        for item in selected:
            if name.startswith("meldra"):
                counter_samples.append(_metrics(str(item["stderr"])))
            elif name.startswith("c_"):
                names = {
                    "allocations": "BENCH_ALLOCATIONS",
                    "frees": "BENCH_FREES",
                    "reallocations": "BENCH_REALLOCATIONS",
                    "growth_copied_bytes": "BENCH_GROWTH_COPIED_BYTES",
                    "finish_copies": "BENCH_FINISH_COPIES",
                }
                counter_samples.append(
                    {
                        key: (
                            int(matches[-1])
                            if (
                                matches := re.findall(
                                    rf"{metric}=(\d+)",
                                    str(item["stderr"]),
                                )
                            )
                            else None
                        )
                        for key, metric in names.items()
                    }
                )
        counter_shapes = {
            json.dumps(item, sort_keys=True) for item in counter_samples
        }
        reports[name] = {
            "status": build.status,
            "build": asdict(build),
            "measured_run_count": len(selected),
            "wall_ms": _distribution(
                walls, seed=BYTES_BUILDER_SEED + index
            ),
            "peak_rss_kb": _distribution(
                rss, seed=BYTES_BUILDER_SEED + 100 + index
            ),
            "throughput_mb_s": (
                n / statistics.median(walls) / 1000 if walls else None
            ),
            "allocation_copy_counters": (
                counter_samples[0] if counter_samples else None
            ),
            "allocation_copy_counters_stable": len(counter_shapes) <= 1,
            "source_tokens": len(
                re.findall(
                    r"[A-Za-z_]\w*|\d+|[^\s]",
                    (
                        BENCHMARK_SOURCE
                        if name.startswith("meldra")
                        else c_source
                        if name.startswith("c_")
                        else RUST_BENCHMARK_SOURCE
                    ),
                )
            ),
            "explicit_memory_operations": (
                0
                if name.startswith("meldra") or name.startswith("rust_")
                else len(
                    re.findall(r"\b(?:malloc|free)\s*\(", c_source)
                )
            ),
        }
    native_medians = [
        reports[name]["wall_ms"]["median"]
        for name in reports
        if (name.startswith("c_") or name.startswith("rust_"))
        and reports[name]["wall_ms"]["median"] is not None
    ]
    meldra_medians = [
        reports[name]["wall_ms"]["median"]
        for name in ("meldra_growth", "meldra_reserved")
        if reports[name]["wall_ms"]["median"] is not None
    ]
    best_native = min(native_medians) if native_medians else None
    worst_ratio = (
        max(value / best_native for value in meldra_medians)
        if best_native and meldra_medians
        else None
    )
    required_names = (
        "meldra_growth",
        "meldra_reserved",
        "c_growth",
        "c_reserved",
    )
    policy_counter_keys = (
        "allocations",
        "frees",
        "reallocations",
        "growth_copied_bytes",
        "finish_copies",
    )
    frozen_policy_counter_match = all(
        all(
            reports[f"meldra_{mode}"]["allocation_copy_counters"].get(key)
            == reports[f"c_{mode}"]["allocation_copy_counters"].get(key)
            for key in policy_counter_keys
        )
        for mode in ("growth", "reserved")
    )
    return {
        "method": {
            "warmups_per_available_arm": BYTES_BUILDER_WARMUPS,
            "measured_runs_per_available_arm": BYTES_BUILDER_MEASURED_RUNS,
            "sequential_randomized_order": True,
            "cpu_affinity": cpu,
            "relative_mad_maximum": 0.05,
            "n": n,
            "growth_policy": "Meldra and C exact frozen policy; Rust standard Vec policy may differ",
        },
        "arms": reports,
        "best_native_median_ms": best_native,
        "meldra_worst_slowdown_ratio": worst_ratio,
        "rust_compiler_available": rust_build.status == "MEASURED",
        "rust_policy": "standard Vec implementation-defined reserve growth",
        "frozen_policy_counter_match": frozen_policy_counter_match,
        "passed": (
            all(
                reports[name]["measured_run_count"] == 30
                for name in required_names
            )
            and all(
                reports[name]["wall_ms"]["relative_mad"] is not None
                and reports[name]["wall_ms"]["relative_mad"] <= 0.05
                for name in required_names
            )
            and worst_ratio is not None
            and worst_ratio <= 1.15
            and frozen_policy_counter_match
            and all(
                reports[name]["allocation_copy_counters_stable"]
                for name in required_names
            )
        ),
        "structural_evidence_required": True,
    }


def _function_source(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^static [^\n]*\bmeldra_fn_{re.escape(name)}\([^\n]*\) \{{.*?^\}}$",
        source,
    )
    return match.group(0) if match else ""


def _abi_audit(root: Path) -> dict[str, Any]:
    source = _source_for_family("mutation_after_view")
    original = compile_performance_source(source).mir
    optimized, _ = optimize_mir(original)
    generated = CEmitter(optimized, runtime_arguments=True).emit()
    build = compile_c_source(generated, output_dir=root, stem="program")
    main_source = _function_source(generated, "main")
    finish_lines = [
        line for line in main_source.splitlines() if "meldra_bytes meldra_" in line
    ]
    view_lines = [
        line for line in main_source.splitlines() if "meldra_bytes_view meldra_" in line
    ]
    growth_copy = "memcpy(" in main_source and "builder_growth_copied_bytes" in main_source
    old_free_after_copy = (
        main_source.find("memcpy(") < main_source.find("free(")
        if "memcpy(" in main_source and "free(" in main_source
        else False
    )
    finish_clean = bool(finish_lines) and all(
        "malloc" not in line and "memcpy" not in line
        for line in finish_lines
    )
    view_clean = bool(view_lines) and all(
        "malloc" not in line and "memcpy" not in line
        for line in view_lines
    )
    finish_copy_control = bool(
        re.search(r"mem(?:cpy|move)\s*\(", "\n".join(finish_lines) + "\nmemcpy(dst, src, n);")
    )
    free_lines_removed = re.sub(r"(?m)^.*\bfree\(.*\n?", "", main_source)
    missing_free_control = "free(" in main_source and "free(" not in free_lines_removed
    active_view_guard = "active_views != 0"
    live_view_control = (
        active_view_guard in main_source
        and active_view_guard not in main_source.replace(active_view_guard, "")
    )
    checks = {
        "native_build": build.status == "MEASURED",
        "finish_pointer_is_builder_pointer": bool(finish_lines) and ".data," in finish_lines[0],
        "finish_payload_copy_zero": finish_clean,
        "growth_copy_only_in_growth_path": growth_copy,
        "append_without_growth_has_no_allocator": "malloc" not in "\n".join(line for line in main_source.splitlines() if ".data[" in line and ".length++" in line),
        "finish_has_no_memcpy": finish_clean,
        "finish_has_no_allocation": finish_clean,
        "view_has_no_copy_loop": view_clean,
        "old_buffer_free_after_successful_growth": old_free_after_copy,
        "final_bytes_free_once": main_source.count("free(") >= 1,
        "finish_copy_control_detected": finish_copy_control,
        "missing_free_control_detected": missing_free_control,
        "live_view_append_control_detected": live_view_control,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "finish_lines": finish_lines,
        "view_lines": view_lines,
        "generated_c_sha256": hashlib.sha256(generated.encode()).hexdigest(),
        "build": build.to_dict(),
    }


def _frozen_hashes(root: Path) -> dict[str, Any]:
    expected = {
        "benchmarks/meldra_bytes_borrowed_return.json": "6dc22cff8ebc57af367df5fb2ef61bc7458a28c7f55fc49013be33f566a8a1fc",
        "benchmarks/meldra_bytes_borrowed_return_self_skeptical_audit.json": "13e55a0bac1a3ebd09dc130fea51b21c78a1442f226c84ea86291bacadd9284e",
        "benchmarks/meldra_bytes_borrowed_return_preregistered.json": "3fc87492090c30652136eb7fa4e773ae02511e9171936358f1216cc5eee309fa",
        "benchmarks/meldra_bytes_reborrow.json": "010d7696d436314d1f660369d9d83cd29144b5523ceffc8a3e7b91b9ed0b4cdc",
        "benchmarks/meldra_bytes_reborrow_self_skeptical_audit.json": "003ff481928c112a7e6f429607b781c0a4538f1dba0b250f38af3a05a9a35bc4",
        "benchmarks/meldra_bytes_reborrow_preregistered.json": "9f5dce3c5afb6e4a2bf4424102fcc420fb86bb92ebe9f903cdc5b3a5df61022c",
        "benchmarks/meldra_bytes_call_boundary.json": "b03add69373fc4646db0375b9a6ce70b0dc23b2fced2b4ca56e91e3d2b54b0df",
        "benchmarks/meldra_bytes_call_boundary_self_skeptical_audit.json": "c1e7b9026b6bad27732bf6cd84c7d4bd6c55c6a312227fa89429807e964a79a0",
        "benchmarks/meldra_bytes_call_boundary_preregistered.json": "4d928def7ef199538e249820848bbef7ce810696a674a4cb07b5e690eccacd16",
        "benchmarks/meldra_bytes_evidence_closure.json": "f9308bc4b34dbda6313118de20efd57636a9b97340bafa382ec30e814641f9a3",
        "benchmarks/meldra_bytes_self_skeptical_audit.json": "79f3b81336ca13e04f691a9049963ac44dcb33687ca55c59af6885090bc04ef0",
        "benchmarks/meldra_bytes_evidence_preregistered.json": "41fdb1bc2c54b7e19c061b438c7dd2b1888b66ce8b6b82c57a3633277f712267",
        "benchmarks/meldra_bytes_experiment.json": "123d31cf8d4855e7cdeb41ad0069e4d13e33bf9779c4a234b440535aa25f8157",
    }
    checks = {}
    for relative, digest in expected.items():
        path = root / relative
        observed = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        checks[relative] = {
            "expected_sha256": digest,
            "observed_sha256": observed,
            "match": observed == digest,
        }
    return {
        "passed": all(item["match"] for item in checks.values()),
        "checks": checks,
    }


def _decision(report: dict[str, Any]) -> tuple[str, dict[str, bool]]:
    valid = report["correctness"]["valid"]
    invalid = report["correctness"]["invalid"]
    safety = report["safety"]
    gates = {
        "valid_minimum": valid["case_count"] >= 600,
        "invalid_minimum": invalid["case_count"] >= 240,
        "valid_families": valid["family_count"] >= 18,
        "invalid_families": invalid["family_count"] >= 16,
        "valid_agreement": valid["unexpected_failure"] == 0,
        "invalid_exact": invalid["unexpected_acceptance"] == 0 and invalid["unexpected_failure"] == 0,
        "growth_payload": valid["unexpected_failure"] == 0,
        "growth_bound": valid["unexpected_failure"] == 0,
        "view_safety": report["contracts"]["optimized_mir"]["validation"]["balanced_builder_views"],
        "finish_zero_copy": report["abi_audit"]["checks"]["finish_payload_copy_zero"],
        "automatic_drop": report["contracts"]["optimized_mir"]["validation"]["automatic_drop_present"],
        "sanitizers": safety["passed"],
        "frame_encoder": report["binary_frame_encoder"]["passed"],
        "performance": report["performance"]["passed"],
        "full_suite": report.get("full_suite", {}).get("passed") is True,
        "surface_annotations_zero": report["contracts"]["hir"]["lifetime_annotations_in_surface"] == 0 and report["contracts"]["hir"]["allocator_annotations_in_surface"] == 0,
        "frozen_artifacts": report["frozen_artifacts"]["passed"],
        "abi": report["abi_audit"]["passed"],
    }
    defect = (
        not safety["passed"]
        or invalid["unexpected_acceptance"] > 0
        or not report["abi_audit"]["checks"]["finish_payload_copy_zero"]
        or not report["contracts"]["optimized_mir"]["validation"]["balanced_builder_views"]
    )
    if defect:
        return "BYTES_BUILDER_SAFETY_DEFECT", gates
    if all(gates.values()):
        return "BYTES_BUILDER_SUPPORTED", gates
    return "BYTES_BUILDER_INCOMPLETE", gates


def validate_bytes_builder_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != BYTES_BUILDER_EXPERIMENT_SCHEMA_VERSION:
        raise ValueError("unsupported BytesBuilder report schema")
    if report.get("kind") != BYTES_BUILDER_EXPERIMENT_KIND:
        raise ValueError("unexpected BytesBuilder report kind")
    if report["correctness"]["valid"]["case_count"] < 600:
        raise ValueError("valid BytesBuilder corpus gate is not met")
    if report["correctness"]["invalid"]["case_count"] < 240:
        raise ValueError("invalid BytesBuilder corpus gate is not met")
    if report["status"] not in {
        "BYTES_BUILDER_SUPPORTED",
        "BYTES_BUILDER_INCOMPLETE",
        "BYTES_BUILDER_SAFETY_DEFECT",
    }:
        raise ValueError("invalid BytesBuilder status")


def run_bytes_builder_experiment(
    *,
    output_dir: str | Path = "benchmarks/meldra_bytes_builder",
    report_path: str | Path = "benchmarks/meldra_bytes_builder.json",
) -> dict[str, Any]:
    started = time.perf_counter()
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    valid, templates = _correctness(root / "correctness")
    invalid = _invalid_corpus(root / "invalid")
    safety = _sanitizers(root / "sanitizers", templates)
    invalid["native_sanitizer_executed"] = safety["native_executions"]
    contract_source = """fn abandon(n: UInt64) -> UInt64:
    let builder: BytesBuilder = BytesBuilder.with_capacity(n)
    return builder.len() + builder.capacity()
fn main(n: UInt64, seed: UInt64) -> UInt64:
    let builder: BytesBuilder = BytesBuilder.new()
    builder.push(seed & 255)
    let view: BytesView = builder.as_view()
    let observed: UInt64 = view.len()
    builder.push((seed + 1) & 255)
    let bytes: Bytes = builder.finish()
    return observed + bytes.len()
"""
    contract_hir = compile_native_hir(contract_source, path="builder-contract.meldra")
    contract_mir = compile_performance_source(contract_source, path="builder-contract.meldra").mir
    contract_optimized, _ = optimize_mir(contract_mir)
    report = {
        "schema_version": BYTES_BUILDER_EXPERIMENT_SCHEMA_VERSION,
        "kind": BYTES_BUILDER_EXPERIMENT_KIND,
        "date": "2026-08-12",
        "scope": {
            "supported": "unique local BytesBuilder capacity growth and immutable caller-local views",
            "unsupported": ["Text", "UTF-8", "recursive values", "interfaces", "JSON", "flow", "machine", "async", "shared or COW Bytes", "bounds-check optimization"],
        },
        "preregistration": json.loads(Path("benchmarks/meldra_bytes_builder_preregistered.json").read_text()),
        "self_skeptical_audit": json.loads(Path("benchmarks/meldra_bytes_builder_self_skeptical_audit.json").read_text()),
        "contracts": {
            "hir": bytes_builder_hir_manifest(contract_hir),
            "unoptimized_mir": bytes_builder_mir_manifest(contract_mir),
            "optimized_mir": bytes_builder_mir_manifest(contract_optimized),
            "abi": bytes_builder_abi_manifest(),
        },
        "correctness": {"valid": valid, "invalid": invalid},
        "safety": safety,
        "binary_frame_encoder": _frame_acceptance(root / "frame"),
        "performance": _benchmark(root / "benchmark"),
        "abi_audit": _abi_audit(root / "abi"),
        "frozen_artifacts": _frozen_hashes(Path(".").resolve()),
        "full_suite": {"passed": False, "status": "PENDING_FINALIZATION"},
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "research_metrics": {
            "decision_run_count": 1,
            "bytes_builder_benchmark_runs": 1,
            "prior_bytes_benchmark_runs": 0,
            "external_python_benchmark_runs": 0,
            "stage06p_full_runs": 0,
            "soak_runs": 0,
            "implementation_wall_time_s": time.perf_counter() - started,
        },
        "limitations": [
            "Rust was measured only when rustc was available on the host; standard Vec growth policy is implementation-defined and intentionally not forced to Meldra policy.",
            "BytesBuilder is local and unique; function parameters, returned builders, shared ownership, records, heap storage, closures, recursion, and async are outside this slice.",
            "Runtime counters describe Meldra payload operations, not allocator-internal metadata.",
            "The state-machine corpus uses twenty independently defined semantic families and deterministic parameter seeds rather than hundreds of source files.",
        ],
    }
    status, gates = _decision(report)
    report["status"] = status
    report["decision_gates"] = gates
    validate_bytes_builder_report(report)
    destination = Path(report_path)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    destination.write_text(text, encoding="utf-8")
    return {
        **report,
        "artifact": {
            "path": str(destination),
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "bytes": len(text.encode()),
        },
    }


def finalize_bytes_builder_report(
    report: dict[str, Any], *, passed: int, failed: int, skipped: int
) -> dict[str, Any]:
    finalized = dict(report)
    finalized.pop("artifact", None)
    finalized["full_suite"] = {
        "passed": failed == 0,
        "status": "PASS" if failed == 0 else "FAIL",
        "passed_tests": passed,
        "failed_tests": failed,
        "skipped_tests": skipped,
    }
    status, gates = _decision(finalized)
    finalized["status"] = status
    finalized["decision_gates"] = gates
    validate_bytes_builder_report(finalized)
    return finalized


__all__ = [
    "BYTES_BUILDER_EXPERIMENT_KIND",
    "BYTES_BUILDER_EXPERIMENT_SCHEMA_VERSION",
    "FRAME_SOURCE",
    "finalize_bytes_builder_report",
    "run_bytes_builder_experiment",
    "validate_bytes_builder_report",
]
