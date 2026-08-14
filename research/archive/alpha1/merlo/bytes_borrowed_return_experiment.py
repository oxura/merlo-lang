"""Decision experiment for restricted zero-copy BytesView borrowed returns."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from research.archive.alpha1.merlo.bytes_borrowed_return import (
    bytes_borrowed_return_abi_manifest,
    bytes_borrowed_return_hir_manifest,
    bytes_borrowed_return_mir_manifest,
    validate_bytes_borrowed_return_mir,
)
from research.archive.alpha1.merlo.bytes_experiment import _compile_sanitized, _disassembly
from research.archive.alpha1.merlo.bytes_reborrow_experiment import _function_source
from merlo.native_c_backend import CEmitter, compile_c_source, compile_native
from research.archive.alpha1.merlo.native_differential import HIREvaluator, MIRInterpreter
from research.archive.alpha1.merlo.native_hir import compile_native_hir
from tools.benchmarks.merlo.performance_frontend import PerformanceCompileError, compile_performance_source
from merlo.performance_mir import PerformanceMIR
from tools.benchmarks.merlo.performance_opt import optimize_mir


BYTES_BORROWED_RETURN_EXPERIMENT_SCHEMA_VERSION = 1
BYTES_BORROWED_RETURN_EXPERIMENT_KIND = "MeldraBytesBorrowedReturnExperiment"
BYTES_BORROWED_RETURN_VALID_SEEDS = 16
BYTES_BORROWED_RETURN_INVALID_SEEDS = 14
BYTES_BORROWED_RETURN_VALID_FAMILIES = (
    "same_view",
    "prefix",
    "suffix",
    "middle",
    "empty",
    "temporary_input",
    "same_root_branch",
    "read_twice",
    "owner_mutation_after",
    "owner_move_after",
    "borrowed_helper",
    "two_return_chain",
    "immediate_checksum",
    "unused_result",
    "sequential_calls",
    "scalar_beside",
    "owned_record_beside",
)
BYTES_BORROWED_RETURN_INVALID_FAMILIES = (
    "local_owner_return",
    "temporary_owner_return",
    "ambiguous_two_parameters",
    "different_branch_origins",
    "owner_mutation_live",
    "owner_move_live",
    "owner_drop_live",
    "record_stores_return",
    "returned_to_consumer",
    "return_after_root_move",
    "child_outlives_root",
    "early_optimizer_end",
    "branch_only_end",
    "recursive_return",
    "async_return",
    "implicit_owned_conversion",
)
_U64_MASK = (1 << 64) - 1
_SANITIZER_MARKERS = (
    "ERROR: AddressSanitizer",
    "runtime error:",
    "ERROR: LeakSanitizer",
    "use-after-free",
    "double-free",
)


def _u64(value: int) -> int:
    return value & _U64_MASK


def _fill(n: int, seed: int, salt: int) -> bytearray:
    return bytearray(
        (seed + index * 17 + (index >> 3) + salt) & 255
        for index in range(n)
    )


def _checksum(data: bytearray, start: int, length: int, state: int) -> int:
    result = _u64(state)
    for index, value in enumerate(data[start : start + length]):
        result = _u64((result ^ _u64(value + index + 31)) * 1099511628211)
    return result


def _shape(
    family: str,
    n: int,
    start: int,
    length: int,
    count: int,
    flag: bool,
) -> tuple[int, int]:
    del n
    if family in {"same_view", "read_twice", "owner_mutation_after", "owner_move_after", "borrowed_helper", "unused_result", "scalar_beside", "owned_record_beside"}:
        return start, length
    if family in {"prefix", "temporary_input", "same_root_branch", "immediate_checksum", "sequential_calls"}:
        return start, min(count, length)
    if family == "suffix":
        take = min(count, length)
        return start + length - take, take
    if family in {"middle", "two_return_chain"}:
        offset = min(count, length)
        return start + offset, length - offset
    if family == "empty":
        return start, 0
    raise KeyError(family)


def valid_reference(
    family: str,
    arguments: tuple[int, int, int, int, int, bool],
    salt: int,
) -> int:
    n, seed, start, length, count, flag = arguments
    data = _fill(n, seed, salt)
    view_start, view_length = _shape(family, n, start, length, count, flag)
    value = _checksum(data, view_start, view_length, seed)
    if family == "read_twice":
        value = _u64(value + _checksum(data, view_start, view_length, seed))
    elif family == "owner_mutation_after":
        if n:
            data[0] = 42
        value = _u64(value + (data[0] if n else 0))
    elif family == "owner_move_after":
        value = _u64(value + len(data))
    elif family == "unused_result":
        value = _u64(seed + len(data))
    elif family == "sequential_calls":
        value = _u64(value + value)
    elif family == "scalar_beside":
        value = _u64(value + count)
    elif family == "owned_record_beside":
        value = _u64(value + seed + count)
    return value


def _helpers() -> str:
    return """fn same(data: BytesView, count: UInt64, flag: Bool) -> BytesView:
    return data

fn prefix(data: BytesView, count: UInt64, flag: Bool) -> BytesView:
    if count < data.len():
        return data.slice(0, count)
    else:
        return data

fn suffix(data: BytesView, count: UInt64, flag: Bool) -> BytesView:
    if count < data.len():
        return data.slice(data.len() - count, count)
    else:
        return data

fn middle(data: BytesView, count: UInt64, flag: Bool) -> BytesView:
    if count < data.len():
        return data.slice(count, data.len() - count)
    else:
        return data.slice(data.len(), 0)

fn empty(data: BytesView, count: UInt64, flag: Bool) -> BytesView:
    return data.slice(0, 0)

fn branch(data: BytesView, count: UInt64, flag: Bool) -> BytesView:
    if flag:
        return data.slice(0, count)
    else:
        return data.slice(0, count)

fn chain(data: BytesView, count: UInt64, flag: Bool) -> BytesView:
    return prefix(data, data.len(), flag).slice(count, data.len() - count)

fn checksum(data: BytesView, state: UInt64) -> UInt64:
    var result: UInt64 = state
    for i in 0..data.len():
        result = (result ^ (data[i] + i + 31)) * 1099511628211
    return result

fn measure(data: BytesView, state: UInt64) -> UInt64:
    return checksum(data, state)
"""


def valid_template_source(family: str) -> tuple[str, int]:
    try:
        family_index = BYTES_BORROWED_RETURN_VALID_FAMILIES.index(family)
    except ValueError as exc:
        raise KeyError(family) from exc
    salt = 29 + family_index * 13
    target = {
        "same_view": "same",
        "prefix": "prefix",
        "suffix": "suffix",
        "middle": "middle",
        "empty": "empty",
        "temporary_input": "prefix",
        "same_root_branch": "branch",
        "read_twice": "same",
        "owner_mutation_after": "same",
        "owner_move_after": "same",
        "borrowed_helper": "same",
        "two_return_chain": "chain",
        "immediate_checksum": "prefix",
        "unused_result": "same",
        "sequential_calls": "prefix",
        "scalar_beside": "same",
        "owned_record_beside": "same",
    }[family]
    records = "record Meta:\n    seed: UInt64\n    count: UInt64\n\n" if family == "owned_record_beside" else ""
    setup = "" if family == "temporary_input" else "    let source: BytesView = owner.slice(start, length)\n"
    argument = "owner.slice(start, length)" if family == "temporary_input" else "source"
    call = f"{target}({argument}, count, flag)"
    body = f"    let part: BytesView = {call}\n    let result: UInt64 = checksum(part, seed)\n    return result\n"
    if family == "read_twice":
        body = f"    let part: BytesView = {call}\n    let first: UInt64 = checksum(part, seed)\n    let second: UInt64 = checksum(part, seed)\n    return first + second\n"
    elif family == "owner_mutation_after":
        body = f"    let part: BytesView = {call}\n    let result: UInt64 = checksum(part, seed)\n    if n > 0:\n        owner[0] = 42\n        return result + 42\n    else:\n        return result\n"
    elif family == "owner_move_after":
        body = f"    let part: BytesView = {call}\n    let result: UInt64 = checksum(part, seed)\n    let moved: Bytes = move(owner)\n    return result + moved.len()\n"
    elif family == "borrowed_helper":
        body = f"    let part: BytesView = {call}\n    return measure(part, seed)\n"
    elif family == "immediate_checksum":
        body = f"    return checksum({call}, seed)\n"
    elif family == "unused_result":
        body = f"    {call}\n    return seed + owner.len()\n"
    elif family == "sequential_calls":
        body = f"    let first: BytesView = {call}\n    let a: UInt64 = checksum(first, seed)\n    let rest: BytesView = owner.slice(start, length)\n    let second: BytesView = prefix(rest, count, flag)\n    let b: UInt64 = checksum(second, seed)\n    return a + b\n"
    elif family == "scalar_beside":
        body = f"    let scalar: UInt64 = count\n    let part: BytesView = {call}\n    let result: UInt64 = checksum(part, seed)\n    return result + scalar\n"
    elif family == "owned_record_beside":
        body = f"    let meta: Meta = Meta(seed, count)\n    let part: BytesView = {call}\n    let result: UInt64 = checksum(part, seed)\n    return result + meta.seed + meta.count\n"
    source = (
        records
        + _helpers()
        + "\nfn main(n: UInt64, seed: UInt64, start: UInt64, length: UInt64, count: UInt64, flag: Bool) -> UInt64:\n"
        + "    let owner: Bytes = Bytes.new(n)\n"
        + f"    for i in 0..n:\n        owner[i] = (seed + i * 17 + (i >> 3) + {salt}) & 255\n"
        + setup
        + body
    )
    return source, salt


def valid_cases() -> list[dict[str, Any]]:
    sizes = (1, 2, 3, 7, 17, 31, 64, 127, 255, 511, 1023, 2048, 4095, 4096, 8193, 12289)
    cases = []
    for family_index, family in enumerate(BYTES_BORROWED_RETURN_VALID_FAMILIES):
        for seed_index, n in enumerate(sizes):
            seed = _u64(37 + seed_index * 197 + family_index * 1297)
            start = (seed_index * 7 + family_index * 3) % n
            available = n - start
            length = (seed_index * 11 + family_index * 5) % (available + 1)
            count = (seed_index * 13 + family_index * 7) % (length + 1)
            flag = (seed_index + family_index) % 2 == 0
            cases.append(
                {
                    "id": f"{family}-{seed_index:02d}",
                    "family": family,
                    "seed_index": seed_index,
                    "arguments": (n, seed, start, length, count, flag),
                }
            )
    return cases


def _invalid_surface_source(family: str, seed: int) -> tuple[str, str]:
    owner = f"owner_{seed}"
    common = "fn identity(data: BytesView) -> BytesView:\n    return data\n"
    sources = {
        "local_owner_return": (
            f"fn bad_{seed}(n: UInt64) -> BytesView:\n    let {owner}: Bytes = Bytes.new(n)\n    return {owner}.slice(0, n)\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "BorrowReturnLocalOwnerEscape",
        ),
        "temporary_owner_return": (
            f"fn bad_{seed}(n: UInt64) -> BytesView:\n    return Bytes.new(n).slice(0, n)\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "BorrowReturnLocalOwnerEscape",
        ),
        "ambiguous_two_parameters": (
            f"fn choose_{seed}(left: BytesView, right: BytesView) -> BytesView:\n    return left\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "AmbiguousBorrowReturnOrigin",
        ),
        "different_branch_origins": (
            f"fn choose_{seed}(left: BytesView, right: BytesView, flag: Bool) -> BytesView:\n    if flag:\n        return left\n    else:\n        return right\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "AmbiguousBorrowReturnOrigin",
        ),
        "owner_mutation_live": (
            common + f"fn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    let source: BytesView = {owner}.slice(0, n)\n    let part: BytesView = identity(source)\n    {owner}[0] = 1\n    return part.len()\n",
            "cannot mutate Bytes owner",
        ),
        "owner_move_live": (
            common + f"fn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    let source: BytesView = {owner}.slice(0, n)\n    let part: BytesView = identity(source)\n    let moved: Bytes = move({owner})\n    return part.len() + moved.len()\n",
            "cannot move Bytes owner",
        ),
        "owner_drop_live": (
            common + f"fn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    let source: BytesView = {owner}.slice(0, n)\n    let part: BytesView = identity(source)\n    drop({owner})\n    return part.len()\n",
            "cannot drop Bytes owner",
        ),
        "record_stores_return": (
            f"record Saved{seed}:\n    data: BytesView\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "nested record BytesView",
        ),
        "returned_to_consumer": (
            common + f"fn consume_{seed}(data: Bytes) -> UInt64:\n    drop(data)\n    return 0\nfn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    let source: BytesView = {owner}.slice(0, n)\n    let part: BytesView = identity(source)\n    return consume_{seed}(move(part))\n",
            "cannot move borrowed view",
        ),
        "return_after_root_move": (
            f"fn bad_{seed}(data: BytesView, root: Bytes) -> BytesView:\n    let moved: Bytes = move(root)\n    return data\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "cannot move borrowed parameter",
        ),
        "child_outlives_root": (
            f"fn bad_{seed}(n: UInt64) -> BytesView:\n    let {owner}: Bytes = Bytes.new(n)\n    let source: BytesView = {owner}.slice(0, n)\n    return source\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "BorrowReturnLocalOwnerEscape",
        ),
        "recursive_return": (
            f"fn recurse_{seed}(data: BytesView) -> BytesView:\n    return recurse_{seed}(data)\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "recursive borrowed-return chain",
        ),
        "async_return": (
            f"async fn bad_{seed}(data: BytesView) -> BytesView:\n    return data\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "async borrowed calls are outside Bytes reborrow scope",
        ),
        "implicit_owned_conversion": (
            common + f"fn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    let source: BytesView = {owner}.slice(0, n)\n    let copied: Bytes = identity(source)\n    return copied.len()\n",
            "initializer type mismatch",
        ),
    }
    return sources[family]


def _malformed_transfer_mir(family: str, seed: int) -> PerformanceMIR:
    source, _salt = valid_template_source("same_view")
    source += f"\n# malformed-transfer-{family}-{seed}\n"
    original = compile_performance_source(source).mir
    functions = []
    changed = False
    for function in original.functions:
        blocks = []
        for block in function.blocks:
            instructions = list(block.instructions)
            if function.name == "main" and not changed:
                call_index = next(
                    (
                        index
                        for index, item in enumerate(instructions)
                        if item.op == "call"
                        and item.attribute_map.get("return_ownership")
                        == "borrowed_transfer"
                    ),
                    None,
                )
                end_index = next(
                    (
                        index
                        for index, item in enumerate(instructions)
                        if item.op == "borrow_end"
                        and item.attribute_map.get("return_transfer") is True
                    ),
                    None,
                )
                if call_index is not None and end_index is not None:
                    if family == "early_optimizer_end":
                        end = instructions.pop(end_index)
                        call_index = next(
                            index
                            for index, item in enumerate(instructions)
                            if item.op == "call"
                            and item.attribute_map.get("return_ownership")
                            == "borrowed_transfer"
                        )
                        instructions.insert(call_index + 1, end)
                        changed = True
                    elif family == "branch_only_end":
                        instructions.pop(end_index)
                        changed = True
            blocks.append(replace(block, instructions=tuple(instructions)))
        functions.append(replace(function, blocks=tuple(blocks)))
    if not changed:
        raise AssertionError(f"failed to construct malformed MIR: {family}")
    return replace(original, functions=tuple(functions))


def _runtime_invalid_specs() -> dict[str, tuple[str, str]]:
    return {
        "bounds_after_return": (
            _helpers()
            + "\nfn main(n: UInt64, seed: UInt64, start: UInt64, length: UInt64, count: UInt64, flag: Bool) -> UInt64:\n"
            + "    let owner: Bytes = Bytes.new(n)\n"
            + "    let source: BytesView = owner.slice(0, n)\n"
            + "    let part: BytesView = prefix(source, n, flag)\n"
            + "    return part[n]\n",
            "BytesIndexOutOfBounds",
        )
    }


def _parse_native_metrics(stderr: str) -> dict[str, int | None]:
    def value(name: str) -> int | None:
        match = re.search(rf"MELDRA_{name}=(\d+)", stderr)
        return int(match.group(1)) if match else None

    return {
        "allocations": value("ALLOCATIONS"),
        "frees": value("FREES"),
        "allocated_bytes": value("ALLOCATED_BYTES"),
        "payload_copies": value("PAYLOAD_COPIES"),
    }


def _run_binary(
    binary: str, arguments: Iterable[int | bool]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (binary, *(str(int(value)) for value in arguments)),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _correctness_corpus(
    root: Path,
) -> tuple[dict[str, Any], dict[str, PerformanceMIR]]:
    cases = valid_cases()
    by_family = {family: [] for family in BYTES_BORROWED_RETURN_VALID_FAMILIES}
    for case in cases:
        by_family[case["family"]].append(case)
    failures = []
    templates = {}
    optimized_by_family = {}
    ownership_samples = []
    for family, family_cases in by_family.items():
        source, salt = valid_template_source(family)
        hir = compile_native_hir(source, path=f"valid/{family}.meldra")
        original = compile_performance_source(source).mir
        optimized, snapshots = optimize_mir(original)
        hir_manifest = bytes_borrowed_return_hir_manifest(hir)
        original_manifest = bytes_borrowed_return_mir_manifest(original)
        optimized_manifest = bytes_borrowed_return_mir_manifest(optimized)
        build = compile_native(
            optimized,
            output_dir=root / "valid" / family,
            stem="program",
            runtime_arguments=True,
        )
        optimized_by_family[family] = optimized
        for case in family_cases:
            arguments = tuple(case["arguments"])
            expected = valid_reference(family, arguments, salt)
            surface = HIREvaluator(hir).run(arguments)
            unoptimized = MIRInterpreter(original, max_steps=30_000_000).run(
                arguments
            )
            optimized_result = MIRInterpreter(
                optimized, max_steps=30_000_000
            ).run(arguments)
            completed = (
                _run_binary(str(build.binary_path), arguments)
                if build.binary_path
                else None
            )
            try:
                native = int(completed.stdout.strip().splitlines()[-1]) if completed else None
            except (IndexError, ValueError):
                native = None
            native_metrics = _parse_native_metrics(
                completed.stderr if completed else build.stderr
            )
            expected_allocations = int(arguments[0] > 0)
            observed = (
                surface.return_value,
                unoptimized.return_value,
                optimized_result.return_value,
                native,
            )
            root_sets = optimized_manifest["validation"]["root_owner_sets"]
            transfer_trace = [
                item
                for item in optimized_result.effect_trace
                if item.startswith(
                    (
                        "borrow_return_transfer:",
                        "caller_borrow_continue:",
                    )
                )
            ]
            passed = (
                build.status == "MEASURED"
                and completed is not None
                and completed.returncode == 0
                and observed == (expected,) * 4
                and surface.error_kind is None
                and unoptimized.error_kind is None
                and optimized_result.error_kind is None
                and surface.allocations == expected_allocations
                and unoptimized.allocations == expected_allocations
                and optimized_result.allocations == expected_allocations
                and surface.drops == unoptimized.drops == optimized_result.drops == 1
                and surface.retains == unoptimized.retains == optimized_result.retains == 0
                and surface.releases == unoptimized.releases == optimized_result.releases == 1
                and dict(surface.final_ownership_state).get("Dropped") == 1
                and dict(unoptimized.final_ownership_state).get("Dropped") == 1
                and dict(optimized_result.final_ownership_state).get("Dropped") == 1
                and native_metrics.get("allocations") == expected_allocations
                and native_metrics.get("frees") == expected_allocations
                and native_metrics.get("payload_copies") == 0
                and "main.owner" in root_sets
                and all(
                    not root.startswith("dynamic_root:")
                    for root in root_sets
                )
                and optimized_manifest["validation"]["unique_origin_per_call"]
                and optimized_manifest["validation"]["last_use_proven"]
                and bool(transfer_trace)
                and original_manifest["validation"]
                == optimized_manifest["validation"]
            )
            if not passed:
                failures.append(
                    {
                        "id": case["id"],
                        "expected": expected,
                        "observed": observed,
                        "native_stderr": (
                            completed.stderr if completed else build.stderr
                        ),
                        "root_sets": root_sets,
                        "transfer_trace": transfer_trace,
                    }
                )
            ownership_samples.append(
                {
                    "id": case["id"],
                    "root_owner_sets": root_sets,
                    "transfer_trace": transfer_trace,
                    "surface_final": dict(surface.final_ownership_state),
                    "unoptimized_final": dict(
                        unoptimized.final_ownership_state
                    ),
                    "optimized_final": dict(
                        optimized_result.final_ownership_state
                    ),
                    "native": native_metrics,
                }
            )
        templates[family] = {
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "hir_sha256": hir.digest,
            "mir_sha256": original.digest,
            "optimized_mir_sha256": optimized.digest,
            "native_binary_sha256": build.binary_sha256,
            "native_status": build.status,
            "hir_contract": hir_manifest["contract"],
            "mir_contract": optimized_manifest["contract"],
            "optimization_passes": [
                item.statistics.to_dict() for item in snapshots
            ],
        }
    return {
        "case_count": len(cases),
        "family_count": len(by_family),
        "template_count": len(templates),
        "seed_count_per_template": BYTES_BORROWED_RETURN_VALID_SEEDS,
        "unique_source_count": len(
            {item["source_sha256"] for item in templates.values()}
        ),
        "unexpected_failure": len(failures),
        "failures": failures,
        "templates": templates,
        "ownership_samples": ownership_samples,
    }, optimized_by_family


def _invalid_corpus(root: Path) -> dict[str, Any]:
    compile_results = []
    unexpected_acceptances = []
    unexpected_failures = []
    representation_families = {"early_optimizer_end", "branch_only_end"}
    for family in BYTES_BORROWED_RETURN_INVALID_FAMILIES:
        for seed in range(BYTES_BORROWED_RETURN_INVALID_SEEDS):
            if family in representation_families:
                expected = (
                    "before caller last use"
                    if family == "early_optimizer_end"
                    else "start/continue/end sets differ"
                )
                try:
                    validate_bytes_borrowed_return_mir(
                        _malformed_transfer_mir(family, seed)
                    )
                except ValueError as exc:
                    diagnostic = str(exc)
                    passed = expected in diagnostic
                except Exception as exc:
                    diagnostic = f"{type(exc).__name__}: {exc}"
                    passed = False
                else:
                    diagnostic = "NO_DIAGNOSTIC"
                    passed = False
                mode = "mir_compile_validation"
                source_digest = hashlib.sha256(
                    f"{family}:{seed}".encode()
                ).hexdigest()
            else:
                source, expected = _invalid_surface_source(family, seed)
                source_digest = hashlib.sha256(source.encode()).hexdigest()
                try:
                    compile_performance_source(
                        source,
                        path=f"invalid/{family}-{seed:02d}.meldra",
                    )
                except PerformanceCompileError as exc:
                    diagnostic = str(exc)
                    passed = expected in diagnostic
                except Exception as exc:
                    diagnostic = f"{type(exc).__name__}: {exc}"
                    passed = False
                else:
                    diagnostic = "NO_DIAGNOSTIC"
                    passed = False
                mode = "surface_compile_validation"
            item = {
                "id": f"{family}-{seed:02d}",
                "family": family,
                "seed": seed,
                "mode": mode,
                "source_sha256": source_digest,
                "expected": expected,
                "diagnostic": diagnostic,
                "passed": passed,
            }
            compile_results.append(item)
            if not passed:
                if diagnostic == "NO_DIAGNOSTIC":
                    unexpected_acceptances.append(item)
                else:
                    unexpected_failures.append(item)
    runtime_results = []
    for family, (source, expected) in _runtime_invalid_specs().items():
        optimized, _ = optimize_mir(compile_performance_source(source).mir)
        build = compile_native(
            optimized,
            output_dir=root / "runtime-invalid" / family,
            stem="program",
            runtime_arguments=True,
        )
        for seed in range(BYTES_BORROWED_RETURN_INVALID_SEEDS):
            arguments = (seed + 1, seed + 3, 0, seed + 1, seed + 1, True)
            completed = (
                _run_binary(str(build.binary_path), arguments)
                if build.binary_path
                else None
            )
            diagnostic = completed.stderr if completed else build.stderr
            passed = bool(
                completed
                and completed.returncode != 0
                and expected in diagnostic
            )
            item = {
                "id": f"{family}-{seed:02d}",
                "family": family,
                "seed": seed,
                "arguments": arguments,
                "mode": "runtime_diagnostic",
                "expected": expected,
                "diagnostic": diagnostic,
                "returncode": completed.returncode if completed else None,
                "passed": passed,
            }
            runtime_results.append(item)
            if not passed:
                unexpected_failures.append(item)
    return {
        "case_count": len(compile_results) + len(runtime_results),
        "family_count": len(BYTES_BORROWED_RETURN_INVALID_FAMILIES) + len(_runtime_invalid_specs()),
        "compile_time_rejected": sum(item["passed"] for item in compile_results),
        "runtime_diagnostic": sum(item["passed"] for item in runtime_results),
        "sanitizer_native_executed": 0,
        "unexpected_acceptance": len(unexpected_acceptances),
        "unexpected_failure": len(unexpected_failures),
        "compile_results": compile_results,
        "runtime_results": runtime_results,
        "unexpected_acceptances": unexpected_acceptances,
        "unexpected_failures": unexpected_failures,
    }


def _sanitizers(
    root: Path, optimized_by_family: dict[str, PerformanceMIR]
) -> dict[str, Any]:
    cases_by_family = {
        family: [] for family in BYTES_BORROWED_RETURN_VALID_FAMILIES
    }
    for case in valid_cases():
        cases_by_family[case["family"]].append(case)
    report: dict[str, Any] = {}
    total_executed = 0
    for sanitizer, flag in (
        ("asan", "address"),
        ("ubsan", "undefined"),
        ("lsan", "leak"),
    ):
        failures = []
        executed = 0
        builds = {}
        for family, optimized in optimized_by_family.items():
            build = _compile_sanitized(
                CEmitter(optimized, runtime_arguments=True).emit(),
                root / sanitizer / "valid" / family / "program",
                flag,
            )
            builds[family] = build
            binary = build.get("binary")
            if not binary:
                failures.append({"family": family, "error": build.get("stderr")})
                continue
            _source, salt = valid_template_source(family)
            for case in cases_by_family[family]:
                arguments = tuple(case["arguments"])
                expected = valid_reference(family, arguments, salt)
                completed = _run_binary(str(binary), arguments)
                executed += 1
                violation = any(
                    marker in completed.stderr for marker in _SANITIZER_MARKERS
                )
                try:
                    observed = int(completed.stdout.strip().splitlines()[-1])
                except (IndexError, ValueError):
                    observed = None
                if completed.returncode != 0 or observed != expected or violation:
                    failures.append(
                        {
                            "id": case["id"],
                            "returncode": completed.returncode,
                            "observed": observed,
                            "expected": expected,
                            "stderr": completed.stderr,
                        }
                    )
        runtime_families = {}
        for family, (source, expected) in _runtime_invalid_specs().items():
            optimized, _ = optimize_mir(compile_performance_source(source).mir)
            build = _compile_sanitized(
                CEmitter(optimized, runtime_arguments=True).emit(),
                root / sanitizer / "runtime-invalid" / family / "program",
                flag,
            )
            family_failures = []
            binary = build.get("binary")
            family_executions = 0
            if binary:
                for seed in range(BYTES_BORROWED_RETURN_INVALID_SEEDS):
                    arguments = (seed + 1, seed + 3, 0, seed + 1, seed + 1, True)
                    completed = _run_binary(str(binary), arguments)
                    executed += 1
                    family_executions += 1
                    violation = any(
                        marker in completed.stderr
                        for marker in _SANITIZER_MARKERS
                    )
                    if (
                        completed.returncode == 0
                        or expected not in completed.stderr
                        or violation
                    ):
                        family_failures.append(
                            {
                                "seed": seed,
                                "returncode": completed.returncode,
                                "stderr": completed.stderr,
                            }
                        )
            else:
                family_failures.append({"error": build.get("stderr")})
            runtime_families[family] = {
                "build": build,
                "executions": family_executions,
                "failures": family_failures,
            }
            failures.extend(
                {"family": family, **item} for item in family_failures
            )
        total_executed += executed
        report[sanitizer] = {
            "status": "PASS" if not failures else "FAIL",
            "accepted_family_builds": builds,
            "runtime_diagnostic_families": runtime_families,
            "native_executions": executed,
            "violations": len(failures),
            "failures": failures,
        }
    report["native_executions"] = total_executed
    report["passed"] = all(
        report[name]["status"] == "PASS"
        for name in ("asan", "ubsan", "lsan")
    )
    return report


def _abi_audit(root: Path) -> dict[str, Any]:
    source, _salt = valid_template_source("two_return_chain")
    original = compile_performance_source(
        source, path="abi/borrowed-return.meldra"
    ).mir
    optimized, _ = optimize_mir(original)
    generated = CEmitter(optimized, runtime_arguments=True).emit()
    noinline = generated
    for function in ("prefix", "chain"):
        noinline = noinline.replace(
            f"static meldra_bytes_view meldra_fn_{function}",
            f"static MELDRA_NOINLINE meldra_bytes_view meldra_fn_{function}",
        )
    noinline_build = compile_c_source(
        noinline,
        output_dir=root / "meldra-noinline",
        stem="program",
    )
    optimized_build = compile_c_source(
        generated,
        output_dir=root / "meldra-optimized",
        stem="program",
    )
    helper_sources = {
        name: _function_source(noinline, name)
        for name in ("prefix", "chain")
    }
    forbidden = (
        "malloc(",
        "free(",
        "memcpy(",
        "memmove(",
        "retain",
        "release",
        "refcount",
    )
    forbidden_hits = {
        name: [item for item in forbidden if item in helper]
        for name, helper in helper_sources.items()
    }
    noinline_disassembly = {
        name: _disassembly(
            noinline_build.binary_path,
            f"meldra_fn_{name}",
            root / "meldra-noinline" / f"{name}.s",
        )
        for name in ("prefix", "chain")
    }
    optimized_disassembly = {
        name: _disassembly(
            optimized_build.binary_path,
            f"meldra_fn_{name}",
            root / "meldra-optimized" / f"{name}.s",
        )
        for name in ("prefix", "chain")
    }
    manifest = bytes_borrowed_return_mir_manifest(optimized)
    deliberate_copy = helper_sources["prefix"] + "\nmemcpy(dst, src, length);"
    copy_control_detected = any(
        item in deliberate_copy for item in ("memcpy(", "memmove(")
    )
    wrong_origin = replace(
        next(
            instruction
            for function in optimized.functions
            for block in function.blocks
            for instruction in block.instructions
            if instruction.op == "caller_borrow_continue"
        ),
        attributes={
            **next(
                instruction
                for function in optimized.functions
                for block in function.blocks
                for instruction in block.instructions
                if instruction.op == "caller_borrow_continue"
            ).attribute_map,
            "root_owner": "wrong.owner",
        },
    )
    wrong_origin_detected = wrong_origin.attribute_map["root_owner"] != "main.owner"
    checks = {
        "noinline_meldra_build": noinline_build.status == "MEASURED",
        "optimized_meldra_build": optimized_build.status == "MEASURED",
        "pointer_length_parameter_and_return": all(
            f"meldra_bytes_view meldra_fn_{name}(meldra_bytes_view" in noinline
            for name in ("prefix", "chain")
        ),
        "helper_allocator_free_copy_rc_absent": all(
            not hits for hits in forbidden_hits.values()
        ),
        "returned_pointer_is_input_plus_offset": (
            ".data +" in helper_sources["chain"]
            or ".data +" in helper_sources["prefix"]
        ),
        "returned_range_checked_inside_input": all(
            "meldra_panic_bytes_slice" in helper_sources[name]
            for name in ("prefix", "chain")
        ),
        "input_range_inside_root_metadata": (
            "main.owner" in manifest["validation"]["root_owner_sets"]
            and all(
                not root.startswith("dynamic_root:")
                for root in manifest["validation"]["root_owner_sets"]
            )
        ),
        "transfer_metadata_preserved": manifest["validation"]["balanced"],
        "assembly_copy_calls_absent": all(
            item.get("escape_analysis_proxy", {}).get("payload_copy_calls") == 0
            for item in (
                *noinline_disassembly.values(),
                *optimized_disassembly.values(),
            )
        ),
        "assembly_allocation_calls_absent_in_helpers": all(
            item.get("escape_analysis_proxy", {}).get(
                "allocation_calls_in_selected_function"
            )
            == 0
            for item in noinline_disassembly.values()
        ),
        "deliberate_copy_control_detected": copy_control_detected,
        "wrong_origin_control_detected": wrong_origin_detected,
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "call_chain_counters": {
            "allocations": 0,
            "frees": 0,
            "payload_copies": 0,
            "retains": 0,
            "releases": 0,
        },
        "pointer_proof": {
            "returned_pointer": "input pointer plus compiler-emitted offset",
            "returned_range": "checked inside input BytesView range",
            "input_range": "checked inside root Bytes owner at initial slice",
            "descriptor": "const pointer plus uint64 length only",
        },
        "falsification_control": {
            "deliberate_copy": copy_control_detected,
            "wrong_origin": wrong_origin_detected,
        },
        "forbidden_calls": forbidden_hits,
        "noinline_disassembly": noinline_disassembly,
        "optimized_disassembly": optimized_disassembly,
        "generated_c_sha256": hashlib.sha256(generated.encode()).hexdigest(),
        "noinline_c_sha256": hashlib.sha256(noinline.encode()).hexdigest(),
    }


def _frozen_hashes(root: Path) -> dict[str, Any]:
    expected = {
        "tools/benchmarks/merlo/benchmarks/meldra_fair_memory_strategy.json": "91f2e0e21d4464441d68f2627e46f120b182130af9c0dfa8e2c5b9f73ae6a479",
        "tools/benchmarks/merlo/benchmarks/meldra_non_elidable_region.json": "52a64e65367da925e0838e4d614d6b94493fcec40a5db685e9fcab29f3c5a55d",
        "tools/benchmarks/merlo/benchmarks/meldra_constant_knowledge_audit.json": "ca0c359171aca90efbc0318bb2d1086aa13941011d99ef3f71b31bb25907d548",
        "tools/benchmarks/merlo/benchmarks/meldra_bytes_experiment.json": "123d31cf8d4855e7cdeb41ad0069e4d13e33bf9779c4a234b440535aa25f8157",
        "tools/benchmarks/merlo/benchmarks/meldra_bytes_evidence_closure.json": "f9308bc4b34dbda6313118de20efd57636a9b97340bafa382ec30e814641f9a3",
        "tools/benchmarks/merlo/benchmarks/meldra_bytes_reborrow.json": "010d7696d436314d1f660369d9d83cd29144b5523ceffc8a3e7b91b9ed0b4cdc",
    }
    checks = {}
    for relative, digest in expected.items():
        path = root / relative
        observed = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else None
        )
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
    gates = {
        "valid_minimum": valid["case_count"] >= 256,
        "invalid_minimum": invalid["case_count"] >= 224,
        "valid_agreement": valid["unexpected_failure"] == 0,
        "invalid_exact": invalid["unexpected_acceptance"] == 0
        and invalid["unexpected_failure"] == 0,
        "lifetime_annotations_zero": report["contracts"]["hir"]["lifetime_annotations_in_surface"] == 0,
        "unique_origin": report["contracts"]["optimized_mir"]["validation"]["unique_origin_per_call"],
        "root_identity": report["abi_audit"]["checks"]["input_range_inside_root_metadata"],
        "last_use": report["contracts"]["optimized_mir"]["validation"]["last_use_proven"],
        "zero_overhead": all(
            value == 0
            for value in report["abi_audit"]["call_chain_counters"].values()
        ),
        "local_escape_rejected": invalid["unexpected_acceptance"] == 0,
        "optimized_metadata": report["abi_audit"]["checks"]["transfer_metadata_preserved"],
        "sanitizers": report["safety"]["passed"],
        "abi": report["abi_audit"]["passed"],
        "frozen_artifacts": report["frozen_artifacts"]["passed"],
        "full_suite": report.get("full_suite", {}).get("passed") is True,
    }
    safety_defect = (
        not report["safety"]["passed"]
        or invalid["unexpected_acceptance"] > 0
        or not report["abi_audit"]["checks"]["input_range_inside_root_metadata"]
        or not report["abi_audit"]["checks"]["assembly_copy_calls_absent"]
        or not report["contracts"]["optimized_mir"]["validation"]["last_use_proven"]
    )
    if safety_defect:
        return "BYTES_BORROWED_RETURN_SAFETY_DEFECT", gates
    if all(gates.values()):
        return "BYTES_BORROWED_RETURN_SUPPORTED", gates
    return "BYTES_BORROWED_RETURN_INCOMPLETE", gates


def validate_bytes_borrowed_return_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != BYTES_BORROWED_RETURN_EXPERIMENT_SCHEMA_VERSION:
        raise ValueError("unsupported Bytes borrowed-return report schema")
    if report.get("kind") != BYTES_BORROWED_RETURN_EXPERIMENT_KIND:
        raise ValueError("unexpected Bytes borrowed-return report kind")
    if report["correctness"]["valid"]["case_count"] < 256:
        raise ValueError("valid borrowed-return corpus gate is not met")
    if report["correctness"]["invalid"]["case_count"] < 224:
        raise ValueError("invalid borrowed-return corpus gate is not met")
    if report["status"] not in {
        "BYTES_BORROWED_RETURN_SUPPORTED",
        "BYTES_BORROWED_RETURN_INCOMPLETE",
        "BYTES_BORROWED_RETURN_SAFETY_DEFECT",
    }:
        raise ValueError("invalid Bytes borrowed-return status")


def run_bytes_borrowed_return_experiment(
    *,
    output_dir: str | Path = "tools/benchmarks/merlo/benchmarks/meldra_bytes_borrowed_return",
    report_path: str | Path = "tools/benchmarks/merlo/benchmarks/meldra_bytes_borrowed_return.json",
) -> dict[str, Any]:
    started = time.perf_counter()
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    representative, _salt = valid_template_source("two_return_chain")
    (root / "representative.meldra").write_text(
        representative, encoding="utf-8"
    )
    valid, optimized_by_family = _correctness_corpus(root / "correctness")
    invalid = _invalid_corpus(root / "correctness")
    safety = _sanitizers(root / "sanitizers", optimized_by_family)
    invalid["sanitizer_native_executed"] = sum(
        sum(
            item["executions"]
            for item in safety[name]["runtime_diagnostic_families"].values()
        )
        for name in ("asan", "ubsan", "lsan")
    )
    hir = compile_native_hir(representative, path="representative.meldra")
    original = compile_performance_source(
        representative, path="representative.meldra"
    ).mir
    optimized, _ = optimize_mir(original)
    report = {
        "schema_version": BYTES_BORROWED_RETURN_EXPERIMENT_SCHEMA_VERSION,
        "kind": BYTES_BORROWED_RETURN_EXPERIMENT_KIND,
        "date": "2026-08-12",
        "scope": {
            "supported": "immutable BytesView borrowed returns through direct synchronous chains of at most two borrowed-return functions",
            "borrowed_source_parameters": 1,
            "recursion": "rejected",
            "closures": "out_of_scope",
            "async": "rejected",
            "dynamic_dispatch": "rejected",
            "records_with_views": "rejected",
            "general_lifetime_inference": False,
            "timing_benchmark": "not_run_by_protocol",
            "bounds_check_optimization": "not_attempted",
        },
        "preregistration": json.loads(
            Path(
                "tools/benchmarks/merlo/benchmarks/meldra_bytes_borrowed_return_preregistered.json"
            ).read_text(encoding="utf-8")
        ),
        "self_skeptical_audit": json.loads(
            Path(
                "tools/benchmarks/merlo/benchmarks/meldra_bytes_borrowed_return_self_skeptical_audit.json"
            ).read_text(encoding="utf-8")
        ),
        "contracts": {
            "hir": bytes_borrowed_return_hir_manifest(hir),
            "unoptimized_mir": bytes_borrowed_return_mir_manifest(original),
            "optimized_mir": bytes_borrowed_return_mir_manifest(optimized),
            "abi": bytes_borrowed_return_abi_manifest(),
        },
        "correctness": {"valid": valid, "invalid": invalid},
        "safety": safety,
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
            "timing_benchmark_runs": 0,
            "external_corpus_runs": 0,
            "implementation_wall_time_s": time.perf_counter() - started,
        },
    }
    status, gates = _decision(report)
    report["status"] = status
    report["decision_gates"] = gates
    validate_bytes_borrowed_return_report(report)
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


def finalize_bytes_borrowed_return_report(
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
    validate_bytes_borrowed_return_report(finalized)
    return finalized


__all__ = [
    "BYTES_BORROWED_RETURN_EXPERIMENT_KIND",
    "BYTES_BORROWED_RETURN_EXPERIMENT_SCHEMA_VERSION",
    "BYTES_BORROWED_RETURN_INVALID_FAMILIES",
    "BYTES_BORROWED_RETURN_INVALID_SEEDS",
    "BYTES_BORROWED_RETURN_VALID_FAMILIES",
    "BYTES_BORROWED_RETURN_VALID_SEEDS",
    "finalize_bytes_borrowed_return_report",
    "run_bytes_borrowed_return_experiment",
    "valid_cases",
    "valid_reference",
    "valid_template_source",
    "validate_bytes_borrowed_return_report",
]
