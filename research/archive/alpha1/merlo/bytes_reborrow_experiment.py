"""Decision experiment for compositional BytesView reborrows."""

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

from research.archive.alpha1.merlo.bytes_experiment import _compile_sanitized, _disassembly
from research.archive.alpha1.merlo.bytes_reborrow import (
    bytes_reborrow_abi_manifest,
    bytes_reborrow_hir_manifest,
    bytes_reborrow_mir_manifest,
    validate_bytes_reborrow_mir,
)
from merlo.native_c_backend import CEmitter, compile_c_source, compile_native
from research.archive.alpha1.merlo.native_differential import HIREvaluator, MIRInterpreter
from research.archive.alpha1.merlo.native_hir import compile_native_hir
from tools.benchmarks.merlo.performance_frontend import PerformanceCompileError, compile_performance_source
from merlo.performance_mir import PerformanceMIR
from tools.benchmarks.merlo.performance_opt import optimize_mir


BYTES_REBORROW_EXPERIMENT_SCHEMA_VERSION = 1
BYTES_REBORROW_EXPERIMENT_KIND = "MeldraBytesCompositionalReborrowExperiment"
BYTES_REBORROW_VALID_SEEDS = 16
BYTES_REBORROW_INVALID_SEEDS = 12
BYTES_REBORROW_VALID_FAMILIES = (
    "depth1_named",
    "depth2_helper",
    "depth3_chain",
    "temporary_depth3",
    "sequential_depth2",
    "scalar_inner_depth3",
    "record_return",
    "owner_mutation_after",
    "owner_move_after",
    "conditional_balanced",
    "empty_depth3",
    "one_byte_depth2",
    "prefix_depth3",
    "suffix_depth3",
    "middle_depth3",
    "distinct_sequential_depth3",
)
BYTES_REBORROW_INVALID_FAMILIES = (
    "inner_returns_view",
    "record_stores_view",
    "child_escapes_parent",
    "outer_returns_child_view",
    "view_to_owned_parameter",
    "drop_reborrowed_view",
    "move_reborrowed_view",
    "retain_reborrowed_view",
    "release_reborrowed_view",
    "owner_mutation_during_chain",
    "owner_move_during_chain",
    "branch_unbalanced_reborrow",
    "recursive_borrowed_call",
    "async_borrowed_call",
    "depth_four_chain",
    "parent_ends_before_child",
    "child_outlives_parent",
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


def _scan(
    data: bytearray,
    start: int,
    length: int,
    state: int,
    salt: int,
) -> int:
    checksum = state
    for index, value in enumerate(data[start : start + length]):
        checksum = _u64(
            (checksum ^ _u64(value + index + salt)) * 1099511628211
        )
    return checksum


def _family_depth(family: str) -> int:
    if family in {"depth1_named", "record_return"}:
        return 1
    if family in {
        "depth2_helper",
        "sequential_depth2",
        "owner_mutation_after",
        "owner_move_after",
        "conditional_balanced",
        "one_byte_depth2",
    }:
        return 2
    return 3


def _helper_source(depth: int, salt: int, *, conditional: bool = False) -> str:
    leaf = f"""fn leaf(data: BytesView, state: UInt64) -> UInt64:
    var checksum: UInt64 = state
    for i in 0..data.len():
        checksum = (checksum ^ (data[i] + i + {salt})) * 1099511628211
    return checksum

"""
    if conditional:
        return leaf + f"""fn outer(data: BytesView, state: UInt64, flag: Bool) -> UInt64:
    if flag:
        return leaf(data, state)
    else:
        return leaf(data, state + {salt})

"""
    if depth == 1:
        return leaf
    if depth == 2:
        return leaf + """fn outer(data: BytesView, state: UInt64) -> UInt64:
    return leaf(data, state)

"""
    return leaf + """fn middle(data: BytesView, state: UInt64) -> UInt64:
    return leaf(data, state)

fn outer(data: BytesView, state: UInt64) -> UInt64:
    return middle(data, state)

"""


def valid_template_source(family: str) -> tuple[str, int, int]:
    try:
        family_index = BYTES_REBORROW_VALID_FAMILIES.index(family)
    except ValueError as exc:
        raise KeyError(family) from exc
    salt = 19 + family_index * 2
    depth = _family_depth(family)
    if family == "record_return":
        helpers = f"""record Stats:
    checksum: UInt64
    length: UInt64

fn inspect(data: BytesView, state: UInt64) -> Stats:
    var checksum: UInt64 = state
    for i in 0..data.len():
        checksum = (checksum ^ (data[i] + i + {salt})) * 1099511628211
    return Stats(checksum=checksum, length=data.len())

"""
    else:
        helpers = _helper_source(
            depth,
            salt,
            conditional=family == "conditional_balanced",
        )
    helpers += """fn transfer(data: Bytes) -> Bytes:
    return data

"""
    main = f"""fn main(n: UInt64, seed: UInt64, start: UInt64, length: UInt64, flag: Bool) -> UInt64:
    let owner: Bytes = Bytes.new(n)
    for i in 0..n:
        owner[i] = (seed + i * 17 + (i >> 3) + {salt}) & 255
"""
    target = "leaf" if depth == 1 else "outer"
    selected_call = f"{target}(owner.slice(start, length), seed)"
    if family == "depth1_named":
        body = (
            "    let view: BytesView = owner.slice(start, length)\n"
            "    let checksum: UInt64 = leaf(view, seed)\n"
            "    return checksum + owner.len()\n"
        )
    elif family == "record_return":
        body = (
            "    let stats: Stats = inspect(owner.slice(start, length), seed)\n"
            "    return stats.checksum + stats.length + owner.len()\n"
        )
    elif family in {"sequential_depth2", "distinct_sequential_depth3"}:
        second = (
            "outer(owner.slice(0, n), first)"
            if family == "distinct_sequential_depth3"
            else "outer(view, first)"
        )
        body = (
            "    let view: BytesView = owner.slice(start, length)\n"
            "    let first: UInt64 = outer(view, seed)\n"
            f"    let checksum: UInt64 = {second}\n"
            "    return checksum + owner.len()\n"
        )
    elif family == "owner_mutation_after":
        body = (
            "    let view: BytesView = owner.slice(start, length)\n"
            "    let checksum: UInt64 = outer(view, seed)\n"
            "    var tail: UInt64 = 0\n"
            "    if n > 0:\n"
            "        owner[0] = (owner[0] + checksum) & 255\n"
            "        tail = owner[0]\n"
            "    return checksum + owner.len() + tail\n"
        )
    elif family == "owner_move_after":
        body = (
            "    let view: BytesView = owner.slice(start, length)\n"
            "    let checksum: UInt64 = outer(view, seed)\n"
            "    let moved: Bytes = transfer(move(owner))\n"
            "    return checksum + moved.len()\n"
        )
    elif family == "conditional_balanced":
        body = (
            "    let checksum: UInt64 = outer(owner.slice(start, length), seed, flag)\n"
            "    return checksum + owner.len()\n"
        )
    else:
        body = (
            f"    let checksum: UInt64 = {selected_call}\n"
            "    return checksum + owner.len()\n"
        )
    return helpers + main + body, salt, depth


def valid_reference(
    family: str,
    arguments: tuple[int, int, int, int, int],
    salt: int,
) -> int:
    n, seed, start, length, flag = arguments
    data = _fill(n, seed, salt)
    state = seed
    if family == "conditional_balanced" and not flag:
        state = _u64(state + salt)
    checksum = _scan(data, start, length, state, salt)
    if family in {"sequential_depth2", "distinct_sequential_depth3"}:
        if family == "distinct_sequential_depth3":
            checksum = _scan(data, 0, n, checksum, salt)
        else:
            checksum = _scan(data, start, length, checksum, salt)
    if family == "record_return":
        return _u64(checksum + length + n)
    if family == "owner_mutation_after" and n > 0:
        data[0] = (data[0] + checksum) & 255
        return _u64(checksum + n + data[0])
    return _u64(checksum + n)


def valid_cases() -> list[dict[str, Any]]:
    sizes = (0, 1, 2, 3, 7, 17, 31, 64, 127, 255, 511, 1023, 2048, 4095, 4096, 8193)
    cases = []
    for family_index, family in enumerate(BYTES_REBORROW_VALID_FAMILIES):
        for seed_index, n in enumerate(sizes):
            seed = _u64(31 + seed_index * 193 + family_index * 1223)
            if n == 0:
                start = length = 0
            else:
                length = (seed_index * 13 + family_index * 3) % (n + 1)
                start = (seed * 5 + family_index) % (n - length + 1)
            if family == "empty_depth3":
                start = length = 0
            elif family == "one_byte_depth2" and n:
                length = 1
                start = seed % n
            elif family == "prefix_depth3":
                start = 0
            elif family == "suffix_depth3":
                start = n - length
            elif family == "middle_depth3" and n:
                length = min(length, max(1, n // 2))
                start = (n - length) // 2
            arguments = (n, seed, start, length, seed_index & 1)
            cases.append(
                {
                    "id": f"{family}-{seed_index:02d}",
                    "family": family,
                    "template": family_index,
                    "seed": seed_index,
                    "arguments": arguments,
                }
            )
    return cases


def _invalid_surface_source(family: str, seed: int) -> tuple[str, str]:
    owner = f"owner_{seed}"
    sources = {
        "inner_returns_view": (
            f"fn inner_{seed}(left: BytesView, right: BytesView) -> BytesView:\n    return left\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "AmbiguousBorrowReturnOrigin",
        ),
        "record_stores_view": (
            f"record Saved{seed}:\n    data: BytesView\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "nested record BytesView",
        ),
        "child_escapes_parent": (
            f"fn child_{seed}(n: UInt64) -> BytesView:\n    let local_{seed}: Bytes = Bytes.new(n)\n    return local_{seed}.slice(0, n)\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "BorrowReturnLocalOwnerEscape",
        ),
        "outer_returns_child_view": (
            f"fn level1_{seed}(data: BytesView) -> BytesView:\n    return data\nfn level2_{seed}(data: BytesView) -> BytesView:\n    return level1_{seed}(data)\nfn outer_{seed}(data: BytesView) -> BytesView:\n    return level2_{seed}(data)\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "borrowed-return chain exceeds 2",
        ),
        "view_to_owned_parameter": (
            f"fn consume_{seed}(data: Bytes) -> UInt64:\n    drop(data)\n    return 0\nfn outer_{seed}(view: BytesView) -> UInt64:\n    return consume_{seed}(move(view))\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "cannot move borrowed view",
        ),
        "drop_reborrowed_view": (
            f"fn outer_{seed}(data: BytesView) -> UInt64:\n    drop(data)\n    return 0\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "cannot drop borrowed view",
        ),
        "move_reborrowed_view": (
            f"fn outer_{seed}(data: BytesView) -> UInt64:\n    move(data)\n    return 0\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "cannot move borrowed view",
        ),
        "retain_reborrowed_view": (
            f"fn outer_{seed}(data: BytesView) -> UInt64:\n    let kept_{seed}: Shared[Slice[UInt64]] = retain(data)\n    drop(kept_{seed})\n    return 0\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "cannot retain borrowed view",
        ),
        "release_reborrowed_view": (
            f"fn outer_{seed}(data: BytesView) -> UInt64:\n    release(data)\n    return 0\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "cannot release borrowed view",
        ),
        "owner_mutation_during_chain": (
            f"fn overlap_{seed}(view: BytesView, data: Bytes) -> UInt64:\n    let result: UInt64 = view.len()\n    data[0] = 1\n    drop(data)\n    return result\nfn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    return overlap_{seed}({owner}.slice(0, n), move({owner}))\n",
            "cannot move or mutate root owner",
        ),
        "owner_move_during_chain": (
            f"fn overlap_{seed}(view: BytesView, data: Bytes) -> UInt64:\n    let result: UInt64 = view.len()\n    drop(data)\n    return result\nfn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    let view_{seed}: BytesView = {owner}.slice(0, n)\n    return overlap_{seed}(view_{seed}, move({owner}))\n",
            "cannot move or mutate root owner",
        ),
        "branch_unbalanced_reborrow": (
            f"fn leaf_{seed}(data: BytesView) -> UInt64:\n    return data.len()\nfn outer_{seed}(data: BytesView, flag: Bool) -> UInt64:\n    if flag:\n        return leaf_{seed}(data)\n    else:\n        return 0\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "must end on every conditional branch",
        ),
        "recursive_borrowed_call": (
            f"fn recurse_{seed}(data: BytesView) -> UInt64:\n    return recurse_{seed}(data)\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "recursive borrowed call chain",
        ),
        "async_borrowed_call": (
            f"async fn outer_{seed}(data: BytesView) -> UInt64:\n    return data.len()\nfn main(n: UInt64) -> UInt64:\n    return n\n",
            "async borrowed calls are outside Bytes reborrow scope",
        ),
        "depth_four_chain": (
            f"fn leaf_{seed}(data: BytesView) -> UInt64:\n    return data.len()\nfn level3_{seed}(data: BytesView) -> UInt64:\n    return leaf_{seed}(data)\nfn level2_{seed}(data: BytesView) -> UInt64:\n    return level3_{seed}(data)\nfn level1_{seed}(data: BytesView) -> UInt64:\n    return level2_{seed}(data)\nfn main(n: UInt64) -> UInt64:\n    let {owner}: Bytes = Bytes.new(n)\n    return level1_{seed}({owner}.slice(0, n))\n",
            "reborrow depth exceeds 3",
        ),
    }
    return sources[family]


def _runtime_invalid_specs() -> dict[str, tuple[str, str]]:
    return {
        "index_out_of_bounds_after_chain": (
            "fn leaf(data: BytesView) -> UInt64:\n"
            "    return data.len()\n"
            "fn outer(data: BytesView) -> UInt64:\n"
            "    return leaf(data)\n"
            "fn main(n: UInt64) -> UInt64:\n"
            "    let owner: Bytes = Bytes.new(n)\n"
            "    let checked: UInt64 = outer(owner.slice(0, n))\n"
            "    return checked + owner[n]\n",
            "BytesIndexOutOfBounds",
        ),
        "slice_out_of_bounds_before_chain": (
            "fn leaf(data: BytesView) -> UInt64:\n"
            "    return data.len()\n"
            "fn main(n: UInt64) -> UInt64:\n"
            "    let owner: Bytes = Bytes.new(n)\n"
            "    return leaf(owner.slice(n, 1))\n",
            "BytesSliceOutOfBounds",
        ),
    }


def _malformed_reborrow_mir(family: str, seed: int) -> PerformanceMIR:
    source, _salt, _depth = valid_template_source("depth3_chain")
    source += f"\n# malformed-seed-{seed}\n"
    original = compile_performance_source(source).mir
    functions = []
    changed = False
    for function in original.functions:
        blocks = []
        for block in function.blocks:
            instructions = list(block.instructions)
            if family == "parent_ends_before_child" and not changed:
                starts = [index for index, item in enumerate(instructions) if item.op == "borrow_argument"]
                calls = [index for index, item in enumerate(instructions) if item.op == "call"]
                ends = [index for index, item in enumerate(instructions) if item.op == "borrow_end"]
                if starts and calls and ends:
                    end = instructions.pop(ends[0])
                    call_index = next(index for index, item in enumerate(instructions) if item.op == "call")
                    instructions.insert(call_index, end)
                    changed = True
            elif family == "child_outlives_parent" and not changed:
                end_index = next(
                    (index for index, item in enumerate(instructions) if item.op == "reborrow_end"),
                    None,
                )
                if end_index is not None:
                    instructions.pop(end_index)
                    changed = True
            blocks.append(replace(block, instructions=tuple(instructions)))
        functions.append(replace(function, blocks=tuple(blocks)))
    if not changed:
        raise AssertionError(f"failed to construct malformed MIR: {family}")
    return replace(original, functions=tuple(functions))


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


def _parse_reborrow_audit(stderr: str) -> dict[str, int | None]:
    def value(name: str) -> int | None:
        match = re.search(rf"MELDRA_REBORROW_{name}=(\d+)", stderr)
        return int(match.group(1)) if match else None

    return {
        "chain_allocations": value("CHAIN_ALLOCATIONS"),
        "chain_frees": value("CHAIN_FREES"),
        "chain_payload_copies": value("CHAIN_PAYLOAD_COPIES"),
        "same_pointer": value("SAME_POINTER"),
        "same_length": value("SAME_LENGTH"),
        "child_inside_root": value("CHILD_INSIDE_ROOT"),
    }


def _run_binary(binary: str, arguments: Iterable[int]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (binary, *(str(value) for value in arguments)),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _borrow_trace_valid(effect_trace: tuple[str, ...], maximum_depth: int) -> bool:
    stack = []
    observed_depth = 0
    root_names = set()
    for event in effect_trace:
        if event.startswith(("borrow_argument:", "reborrow_argument:")):
            depth_match = re.search(r":depth=(\d+):", event)
            root_match = re.search(r":root=([^:]+(?:\.[^:]+)?):same_payload=true$", event)
            if depth_match is None or root_match is None:
                return False
            depth = int(depth_match.group(1))
            if depth != len(stack) + 1:
                return False
            stack.append(event.split(":depth=", 1)[0])
            observed_depth = max(observed_depth, depth)
            root_names.add(root_match.group(1))
        elif event.startswith(("borrow_end:", "reborrow_end:")):
            remaining = re.search(r":remaining=(\d+)$", event)
            if not stack or remaining is None:
                return False
            stack.pop()
            if int(remaining.group(1)) != len(stack):
                return False
    return not stack and observed_depth == maximum_depth and len(root_names) == 1


def _correctness_corpus(root: Path) -> tuple[dict[str, Any], dict[str, PerformanceMIR]]:
    cases = valid_cases()
    by_family = {family: [] for family in BYTES_REBORROW_VALID_FAMILIES}
    for case in cases:
        by_family[case["family"]].append(case)
    failures = []
    templates = {}
    optimized_by_family = {}
    ownership_samples = []
    for family, family_cases in by_family.items():
        source, salt, depth = valid_template_source(family)
        path = f"valid/{family}.meldra"
        hir = compile_native_hir(source, path=path)
        original = compile_performance_source(source, path=path).mir
        optimized, snapshots = optimize_mir(original, artifact_dir=root / "mir" / family)
        optimized_by_family[family] = optimized
        hir_manifest = bytes_reborrow_hir_manifest(hir)
        original_manifest = bytes_reborrow_mir_manifest(original)
        optimized_manifest = bytes_reborrow_mir_manifest(optimized)
        build = compile_native(
            optimized,
            output_dir=root / "native" / family,
            stem="program",
            runtime_arguments=True,
        )
        template_failures = 0
        for case in family_cases:
            arguments = tuple(case["arguments"])
            expected = valid_reference(family, arguments, salt)
            surface = HIREvaluator(hir, max_steps=10_000_000).run(arguments)
            unoptimized = MIRInterpreter(original, max_steps=10_000_000).run(arguments)
            optimized_result = MIRInterpreter(optimized, max_steps=10_000_000).run(arguments)
            completed = _run_binary(str(build.binary_path), arguments) if build.binary_path else None
            try:
                native_checksum = int(completed.stdout.strip().splitlines()[-1]) if completed else None
            except (IndexError, ValueError):
                native_checksum = None
            native_metrics = _parse_native_metrics(completed.stderr) if completed else {}
            expected_allocations = int(arguments[0] > 0)
            observed = (
                surface.return_value,
                unoptimized.return_value,
                optimized_result.return_value,
                native_checksum,
            )
            trace_ok = _borrow_trace_valid(optimized_result.effect_trace, depth)
            root_sets = optimized_manifest["validation"]["root_owner_sets"]
            root_identity_ok = bool(root_sets) and all(len(values) == 1 for values in root_sets.values())
            optimized_metadata_preserved = (
                original_manifest["validation"]["start_count"]
                == optimized_manifest["validation"]["start_count"]
                and original_manifest["validation"]["end_count"]
                == optimized_manifest["validation"]["end_count"]
                and original_manifest["validation"]["maximum_depth"]
                == optimized_manifest["validation"]["maximum_depth"]
            )
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
                and surface.drops == 1
                and unoptimized.drops == 1
                and optimized_result.drops == 1
                and surface.retains == unoptimized.retains == optimized_result.retains == 0
                and surface.releases == unoptimized.releases == optimized_result.releases == 1
                and dict(surface.final_ownership_state).get("Dropped") == 1
                and dict(unoptimized.final_ownership_state).get("Dropped") == 1
                and dict(optimized_result.final_ownership_state).get("Dropped") == 1
                and native_metrics.get("allocations") == expected_allocations
                and native_metrics.get("frees") == expected_allocations
                and native_metrics.get("payload_copies") == 0
                and trace_ok
                and root_identity_ok
                and optimized_metadata_preserved
            )
            if not passed:
                template_failures += 1
                failures.append(
                    {
                        "id": case["id"],
                        "expected": expected,
                        "observed": observed,
                        "trace_ok": trace_ok,
                        "root_identity_ok": root_identity_ok,
                        "optimized_metadata_preserved": optimized_metadata_preserved,
                        "native_returncode": completed.returncode if completed else None,
                        "native_stderr": completed.stderr if completed else build.stderr,
                    }
                )
            ownership_samples.append(
                {
                    "id": case["id"],
                    "root_owner_sets": root_sets,
                    "borrow_depth": optimized_manifest["validation"]["maximum_depth"],
                    "borrow_trace": list(optimized_result.effect_trace),
                    "surface_final": dict(surface.final_ownership_state),
                    "unoptimized_final": dict(unoptimized.final_ownership_state),
                    "optimized_final": dict(optimized_result.final_ownership_state),
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
            "expected_depth": depth,
            "hir_contract": hir_manifest["contract"],
            "mir_contract": optimized_manifest["contract"],
            "optimization_passes": [item.statistics.to_dict() for item in snapshots],
            "case_count": len(family_cases),
            "failures": template_failures,
        }
    return {
        "case_count": len(cases),
        "family_count": len(by_family),
        "template_count": len(templates),
        "seed_count_per_template": BYTES_REBORROW_VALID_SEEDS,
        "unique_source_count": len({item["source_sha256"] for item in templates.values()}),
        "unexpected_failure": len(failures),
        "failures": failures,
        "templates": templates,
        "ownership_samples": ownership_samples,
    }, optimized_by_family


def _invalid_corpus(root: Path) -> dict[str, Any]:
    compile_results = []
    unexpected_acceptances = []
    unexpected_failures = []
    representation_families = {"parent_ends_before_child", "child_outlives_parent"}
    for family in BYTES_REBORROW_INVALID_FAMILIES:
        for seed in range(BYTES_REBORROW_INVALID_SEEDS):
            if family in representation_families:
                expected = (
                    "parent ends before child"
                    if family == "parent_ends_before_child"
                    else "start/end sets differ"
                )
                try:
                    validate_bytes_reborrow_mir(_malformed_reborrow_mir(family, seed))
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
                source_digest = hashlib.sha256(f"{family}:{seed}".encode()).hexdigest()
            else:
                source, expected = _invalid_surface_source(family, seed)
                source_digest = hashlib.sha256(source.encode()).hexdigest()
                try:
                    compile_performance_source(source, path=f"invalid/{family}-{seed:02d}.meldra")
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
        for seed in range(BYTES_REBORROW_INVALID_SEEDS):
            arguments = (seed + 1,)
            completed = _run_binary(str(build.binary_path), arguments) if build.binary_path else None
            diagnostic = completed.stderr if completed else build.stderr
            passed = bool(completed and completed.returncode != 0 and expected in diagnostic)
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
        "family_count": len(BYTES_REBORROW_INVALID_FAMILIES) + len(_runtime_invalid_specs()),
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


def _sanitizers(root: Path, optimized_by_family: dict[str, PerformanceMIR]) -> dict[str, Any]:
    cases_by_family = {family: [] for family in BYTES_REBORROW_VALID_FAMILIES}
    for case in valid_cases():
        cases_by_family[case["family"]].append(case)
    report: dict[str, Any] = {}
    total_executed = 0
    for sanitizer, flag in (("asan", "address"), ("ubsan", "undefined"), ("lsan", "leak")):
        failures = []
        executed = 0
        builds = {}
        for family, optimized in optimized_by_family.items():
            source = CEmitter(optimized, runtime_arguments=True).emit()
            build = _compile_sanitized(source, root / sanitizer / "valid" / family / "program", flag)
            builds[family] = build
            binary = build.get("binary")
            if not binary:
                failures.append({"family": family, "error": build.get("stderr")})
                continue
            _template, salt, _depth = valid_template_source(family)
            for case in cases_by_family[family]:
                arguments = tuple(case["arguments"])
                expected = valid_reference(family, arguments, salt)
                completed = _run_binary(str(binary), arguments)
                executed += 1
                violation = any(marker in completed.stderr for marker in _SANITIZER_MARKERS)
                try:
                    checksum = int(completed.stdout.strip().splitlines()[-1])
                except (IndexError, ValueError):
                    checksum = None
                if completed.returncode != 0 or checksum != expected or violation:
                    failures.append(
                        {
                            "id": case["id"],
                            "returncode": completed.returncode,
                            "checksum": checksum,
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
                for seed in range(BYTES_REBORROW_INVALID_SEEDS):
                    completed = _run_binary(str(binary), (seed + 1,))
                    executed += 1
                    family_executions += 1
                    violation = any(marker in completed.stderr for marker in _SANITIZER_MARKERS)
                    if completed.returncode == 0 or expected not in completed.stderr or violation:
                        family_failures.append(
                            {"seed": seed, "returncode": completed.returncode, "stderr": completed.stderr}
                        )
            else:
                family_failures.append({"error": build.get("stderr")})
            runtime_families[family] = {
                "build": build,
                "executions": family_executions,
                "failures": family_failures,
            }
            failures.extend({"family": family, **item} for item in family_failures)
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
    report["passed"] = all(report[name]["status"] == "PASS" for name in ("asan", "ubsan", "lsan"))
    report["scope"] = {
        "accepted_native_families": list(
            BYTES_REBORROW_VALID_FAMILIES
        ),
        "runtime_diagnostic_families": list(
            _runtime_invalid_specs()
        ),
        "compile_time_rejects_sanitizer_tested": False,
    }
    return report


def _function_source(source: str, function: str) -> str:
    pattern = re.compile(
        rf"^static (?:MELDRA_NOINLINE )?[^\n]+ meldra_fn_{re.escape(function)}\([^;\n]*\) \{{",
        re.MULTILINE,
    )
    match = pattern.search(source)
    if match is None:
        raise ValueError(f"generated function not found: {function}")
    opening = source.find("{", match.start())
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    raise ValueError(f"unterminated generated function: {function}")


def _helper_overhead_scan(source: str) -> dict[str, int]:
    return {
        "allocation_calls": len(
            re.findall(r"\b(?:malloc|calloc|realloc)\s*\(", source)
        ),
        "free_calls": len(re.findall(r"\bfree\s*\(", source)),
        "payload_copy_calls": len(
            re.findall(r"\b(?:memcpy|memmove)\s*\(", source)
        ),
        "payload_store_operations": len(
            re.findall(r"\.data\s*\[[^\]]+\]\s*=", source)
        ),
        "reference_count_calls": len(
            re.findall(
                r"\b(?:retain|release|refcount_inc|refcount_dec)\s*\(",
                source,
            )
        ),
    }


def _instrument_noinline_audit(source: str) -> str:
    declarations = """static uint64_t meldra_reborrow_before_allocations = 0;
static uint64_t meldra_reborrow_before_frees = 0;
static uint64_t meldra_reborrow_before_payload_copies = 0;
static uint64_t meldra_reborrow_after_allocations = 0;
static uint64_t meldra_reborrow_after_frees = 0;
static uint64_t meldra_reborrow_after_payload_copies = 0;
static const uint8_t *meldra_reborrow_owner_data = NULL;
static uint64_t meldra_reborrow_owner_length = 0;
static const uint8_t *meldra_reborrow_root_data = NULL;
static uint64_t meldra_reborrow_root_length = 0;
static const uint8_t *meldra_reborrow_leaf_data = NULL;
static uint64_t meldra_reborrow_leaf_length = 0;
static const uint8_t *meldra_reborrow_middle_data = NULL;
static uint64_t meldra_reborrow_middle_length = 0;
static const uint8_t *meldra_reborrow_outer_data = NULL;
static uint64_t meldra_reborrow_outer_length = 0;"""
    marker = "static uint64_t meldra_payload_copies = 0;"
    if marker not in source:
        raise ValueError("generated payload-copy counter not found")
    instrumented = source.replace(
        marker,
        marker + "\n" + declarations,
        1,
    )
    for function in ("leaf", "middle", "outer"):
        pattern = re.compile(
            rf"(static MELDRA_NOINLINE uint64_t meldra_fn_{function}"
            r"\(meldra_bytes_view meldra_data_1, "
            r"uint64_t meldra_state_2\) \{\n)"
        )
        replacement = (
            rf"\1    meldra_reborrow_{function}_data = "
            "meldra_data_1.data;\n"
            f"    meldra_reborrow_{function}_length = "
            "meldra_data_1.length;\n"
        )
        instrumented, count = pattern.subn(
            replacement,
            instrumented,
            count=1,
        )
        if count != 1:
            raise ValueError(
                f"generated no-inline {function} definition not found"
            )
    slice_pattern = re.compile(
        r"(?m)^(?P<indent>\s*)meldra_bytes_view (?P<view>\w+) = "
        r"\{ (?P<owner>\w+)\.data == NULL \? NULL : "
        r"(?P=owner)\.data \+ [^,]+, [^}]+ \};$"
    )
    slice_match = slice_pattern.search(instrumented)
    if slice_match is None:
        raise ValueError("generated root BytesView construction not found")
    indent = slice_match.group("indent")
    view = slice_match.group("view")
    owner = slice_match.group("owner")
    root_capture = (
        slice_match.group(0)
        + f"\n{indent}meldra_reborrow_owner_data = {owner}.data;"
        + f"\n{indent}meldra_reborrow_owner_length = {owner}.length;"
        + f"\n{indent}meldra_reborrow_root_data = {view}.data;"
        + f"\n{indent}meldra_reborrow_root_length = {view}.length;"
    )
    instrumented = (
        instrumented[: slice_match.start()]
        + root_capture
        + instrumented[slice_match.end() :]
    )
    call_pattern = re.compile(
        r"(?m)^(?P<indent>\s*)uint64_t (?P<result>\w+) = "
        r"meldra_fn_outer\((?P<view>\w+), (?P<rest>[^;]+)\);$"
    )
    call_match = call_pattern.search(instrumented)
    if call_match is None or call_match.group("view") != view:
        raise ValueError("generated root reborrow call not found")
    indent = call_match.group("indent")
    measured_call = (
        f"{indent}meldra_reborrow_before_allocations = "
        "meldra_heap_allocations;\n"
        f"{indent}meldra_reborrow_before_frees = meldra_heap_frees;\n"
        f"{indent}meldra_reborrow_before_payload_copies = "
        "meldra_payload_copies;\n"
        + call_match.group(0)
        + f"\n{indent}meldra_reborrow_after_allocations = "
        "meldra_heap_allocations;"
        + f"\n{indent}meldra_reborrow_after_frees = meldra_heap_frees;"
        + f"\n{indent}meldra_reborrow_after_payload_copies = "
        "meldra_payload_copies;"
    )
    instrumented = (
        instrumented[: call_match.start()]
        + measured_call
        + instrumented[call_match.end() :]
    )
    driver_marker = (
        '    fprintf(stderr, "MELDRA_ALLOCATIONS=%" PRIu64 "\\n", '
        "meldra_heap_allocations);"
    )
    if driver_marker not in instrumented:
        raise ValueError("generated runtime metric driver not found")
    driver_audit = """    fprintf(stderr, "MELDRA_REBORROW_CHAIN_ALLOCATIONS=%" PRIu64 " MELDRA_REBORROW_CHAIN_FREES=%" PRIu64 " MELDRA_REBORROW_CHAIN_PAYLOAD_COPIES=%" PRIu64 "\\n", meldra_reborrow_after_allocations - meldra_reborrow_before_allocations, meldra_reborrow_after_frees - meldra_reborrow_before_frees, meldra_reborrow_after_payload_copies - meldra_reborrow_before_payload_copies);
    bool meldra_reborrow_same_pointer = meldra_reborrow_root_data == meldra_reborrow_outer_data && meldra_reborrow_root_data == meldra_reborrow_middle_data && meldra_reborrow_root_data == meldra_reborrow_leaf_data;
    bool meldra_reborrow_same_length = meldra_reborrow_root_length == meldra_reborrow_outer_length && meldra_reborrow_root_length == meldra_reborrow_middle_length && meldra_reborrow_root_length == meldra_reborrow_leaf_length;
    uintptr_t meldra_reborrow_owner_begin = (uintptr_t)meldra_reborrow_owner_data;
    uintptr_t meldra_reborrow_root_begin = (uintptr_t)meldra_reborrow_root_data;
    bool meldra_reborrow_child_inside_root = meldra_reborrow_owner_data != NULL && meldra_reborrow_root_begin >= meldra_reborrow_owner_begin && meldra_reborrow_root_begin - meldra_reborrow_owner_begin <= meldra_reborrow_owner_length && meldra_reborrow_root_length <= meldra_reborrow_owner_length - (meldra_reborrow_root_begin - meldra_reborrow_owner_begin);
    fprintf(stderr, "MELDRA_REBORROW_SAME_POINTER=%u MELDRA_REBORROW_SAME_LENGTH=%u MELDRA_REBORROW_CHILD_INSIDE_ROOT=%u\\n", (unsigned)meldra_reborrow_same_pointer, (unsigned)meldra_reborrow_same_length, (unsigned)meldra_reborrow_child_inside_root);
"""
    return instrumented.replace(
        driver_marker,
        driver_audit + driver_marker,
        1,
    )


def _metadata_loss_control(mir: PerformanceMIR) -> dict[str, Any]:
    functions = []
    changed = False
    for function in mir.functions:
        blocks = []
        for block in function.blocks:
            instructions = []
            for instruction in block.instructions:
                if not changed and instruction.op == "reborrow_end":
                    changed = True
                    continue
                instructions.append(instruction)
            blocks.append(replace(block, instructions=tuple(instructions)))
        functions.append(replace(function, blocks=tuple(blocks)))
    if not changed:
        return {
            "mutation": "remove_reborrow_end",
            "detected": False,
            "diagnostic": "NO_REBORROW_END",
        }
    try:
        validate_bytes_reborrow_mir(
            replace(mir, functions=tuple(functions))
        )
    except ValueError as exc:
        return {
            "mutation": "remove_reborrow_end",
            "detected": True,
            "diagnostic": str(exc),
        }
    return {
        "mutation": "remove_reborrow_end",
        "detected": False,
        "diagnostic": "NO_DIAGNOSTIC",
    }


def _c_control_source() -> str:
    return """#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef struct { const uint8_t *data; uint64_t length; } bytes_view;
__attribute__((noinline)) static uint64_t leaf(bytes_view data, uint64_t state) {
    for (uint64_t i = 0; i < data.length; ++i) state = (state ^ ((uint64_t)data.data[i] + i + 23)) * UINT64_C(1099511628211);
    return state;
}
__attribute__((noinline)) static uint64_t middle(bytes_view data, uint64_t state) { return leaf(data, state); }
__attribute__((noinline)) static uint64_t outer(bytes_view data, uint64_t state) { return middle(data, state); }
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    uint64_t n = strtoull(argv[1], NULL, 10);
    uint8_t *owner = n ? malloc((size_t)n) : NULL;
    if (n && owner == NULL) return 3;
    for (uint64_t i = 0; i < n; ++i) owner[i] = (uint8_t)i;
    bytes_view root = { owner, n };
    uint64_t result = outer(root, 7);
    free(owner);
    printf("%" PRIu64 "\\n", result);
    return 0;
}
"""


def _abi_audit(root: Path) -> dict[str, Any]:
    source, salt, _depth = valid_template_source("depth3_chain")
    original = compile_performance_source(
        source,
        path="abi/reborrow.meldra",
    ).mir
    optimized, _ = optimize_mir(original)
    generated = CEmitter(optimized, runtime_arguments=True).emit()
    noinline_base = generated
    for function in ("leaf", "middle", "outer"):
        noinline_base = noinline_base.replace(
            f"static uint64_t meldra_fn_{function}",
            f"static MELDRA_NOINLINE uint64_t meldra_fn_{function}",
        )
    noinline = _instrument_noinline_audit(noinline_base)
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
    control = _c_control_source()
    control_build = compile_c_source(
        control,
        output_dir=root / "c-control",
        stem="program",
    )
    helper_sources = {
        name: _function_source(noinline_base, name)
        for name in ("leaf", "middle", "outer")
    }
    helper_scans = {
        name: _helper_overhead_scan(helper)
        for name, helper in helper_sources.items()
    }
    noinline_disassembly = {
        name: _disassembly(
            noinline_build.binary_path,
            f"meldra_fn_{name}",
            root / "meldra-noinline" / f"{name}.s",
        )
        for name in ("leaf", "middle", "outer")
    }
    optimized_disassembly = {
        name: _disassembly(
            optimized_build.binary_path,
            f"meldra_fn_{name}",
            root / "meldra-optimized" / f"{name}.s",
        )
        for name in ("leaf", "middle", "outer")
    }
    control_disassembly = {
        name: _disassembly(
            control_build.binary_path,
            name,
            root / "c-control" / f"{name}.s",
        )
        for name in ("leaf", "middle", "outer")
    }
    audit_arguments = (257, 43, 11, 193, 1)
    expected_checksum = valid_reference(
        "depth3_chain",
        audit_arguments,
        salt,
    )
    noinline_run = (
        _run_binary(str(noinline_build.binary_path), audit_arguments)
        if noinline_build.binary_path
        else None
    )
    optimized_run = (
        _run_binary(str(optimized_build.binary_path), audit_arguments)
        if optimized_build.binary_path
        else None
    )
    noinline_metrics = (
        _parse_reborrow_audit(noinline_run.stderr)
        if noinline_run is not None
        else {}
    )
    optimized_metrics = (
        _parse_native_metrics(optimized_run.stderr)
        if optimized_run is not None
        else {}
    )

    def checksum(completed: subprocess.CompletedProcess[str] | None) -> int | None:
        if completed is None:
            return None
        try:
            return int(completed.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            return None

    def wrappers_same_descriptor(sources: dict[str, str]) -> bool:
        return all(
            "meldra_data_1" in sources[name]
            and re.search(
                rf"meldra_fn_"
                rf"{'leaf' if name == 'middle' else 'middle'}"
                r"\(meldra_data_1,",
                sources[name],
            )
            is not None
            for name in ("middle", "outer")
        )

    wrapper_same_descriptor = wrappers_same_descriptor(helper_sources)
    original_manifest = bytes_reborrow_mir_manifest(original)
    mir_manifest = bytes_reborrow_mir_manifest(optimized)
    metadata_exact = (
        original_manifest["events"] == mir_manifest["events"]
        and original_manifest["calls"] == mir_manifest["calls"]
        and original_manifest["validation"] == mir_manifest["validation"]
    )
    source_mutants = {
        "allocation": helper_sources["middle"] + "\nmalloc(1);",
        "free": helper_sources["middle"] + "\nfree(ptr);",
        "payload_copy": (
            helper_sources["middle"] + "\nmemcpy(dst, src, length);"
        ),
        "copy_loop": (
            helper_sources["middle"]
            + "\nfor (uint64_t i = 0; i < src.length; ++i) "
            "dst.data[i] = src.data[i];"
        ),
        "reference_count": (
            helper_sources["middle"] + "\nretain(meldra_data_1);"
        ),
    }
    scan_fields = {
        "allocation": "allocation_calls",
        "free": "free_calls",
        "payload_copy": "payload_copy_calls",
        "copy_loop": "payload_store_operations",
        "reference_count": "reference_count_calls",
    }
    source_controls = {
        name: {
            "mutation": name,
            "detected": (
                _helper_overhead_scan(mutant)[scan_fields[name]] > 0
            ),
        }
        for name, mutant in source_mutants.items()
    }
    descriptor_mutant = dict(helper_sources)
    descriptor_mutant["middle"] = descriptor_mutant["middle"].replace(
        "meldra_fn_leaf(meldra_data_1,",
        "meldra_fn_leaf((meldra_bytes_view){NULL, 0},",
        1,
    )
    source_controls["pointer_identity"] = {
        "mutation": "replace_child_descriptor",
        "detected": not wrappers_same_descriptor(descriptor_mutant),
    }
    source_controls["optimized_metadata"] = _metadata_loss_control(
        optimized
    )
    copy_assembly_sets = (
        *noinline_disassembly.values(),
        *optimized_disassembly.values(),
        *control_disassembly.values(),
    )
    allocation_assembly_sets = (
        *noinline_disassembly.values(),
        *control_disassembly.values(),
    )
    call_chain_counters = {
        "allocations": noinline_metrics.get("chain_allocations"),
        "frees": noinline_metrics.get("chain_frees"),
        "payload_copies": noinline_metrics.get(
            "chain_payload_copies"
        ),
        "retains": sum(
            item["reference_count_calls"]
            for item in helper_scans.values()
        ),
        "releases": sum(
            item["reference_count_calls"]
            for item in helper_scans.values()
        ),
    }
    pointer_proof = {
        "root_slice": "measured child range inside generated owner range",
        "reborrow": (
            "instrumented generated no-inline C observed the same "
            "pointer-length descriptor at outer, middle, and leaf"
        ),
        "child_inside_root": (
            noinline_metrics.get("child_inside_root") == 1
        ),
        "same_payload_pointer": noinline_metrics.get("same_pointer") == 1,
        "same_length": noinline_metrics.get("same_length") == 1,
    }
    checks = {
        "noinline_meldra_build": noinline_build.status == "MEASURED",
        "optimized_meldra_build": optimized_build.status == "MEASURED",
        "noinline_c_control_build": control_build.status == "MEASURED",
        "noinline_execution": (
            noinline_run is not None
            and noinline_run.returncode == 0
            and checksum(noinline_run) == expected_checksum
        ),
        "optimized_execution": (
            optimized_run is not None
            and optimized_run.returncode == 0
            and checksum(optimized_run) == expected_checksum
            and optimized_metrics.get("payload_copies") == 0
        ),
        "optimized_owner_lifetime_counts": (
            optimized_metrics.get("allocations") == 1
            and optimized_metrics.get("frees") == 1
        ),
        "pointer_length_parameter_every_level": all(
            f"meldra_fn_{name}(meldra_bytes_view" in noinline_base
            for name in ("leaf", "middle", "outer")
        ),
        "helper_allocator_free_copy_rc_absent": all(
            all(value == 0 for value in scan.values())
            for scan in helper_scans.values()
        ),
        "helper_copy_loop_absent": all(
            scan["payload_store_operations"] == 0
            for scan in helper_scans.values()
        ),
        "same_descriptor_passed_through_wrappers": (
            wrapper_same_descriptor
        ),
        "runtime_same_pointer": pointer_proof["same_payload_pointer"],
        "runtime_same_length": pointer_proof["same_length"],
        "runtime_child_inside_root": pointer_proof["child_inside_root"],
        "call_chain_counters_zero": all(
            value == 0 for value in call_chain_counters.values()
        ),
        "root_owner_metadata_identical": all(
            values == ["main.owner"]
            for values in mir_manifest["validation"][
                "root_owner_sets"
            ].values()
        ),
        "maximum_depth_three": (
            mir_manifest["validation"]["maximum_depth"] == 3
        ),
        "optimized_metadata_preserved": metadata_exact,
        "assembly_copy_calls_absent": all(
            item.get("escape_analysis_proxy", {}).get(
                "payload_copy_calls"
            )
            == 0
            for item in copy_assembly_sets
        ),
        "assembly_allocation_calls_absent_in_helpers": all(
            item.get("escape_analysis_proxy", {}).get(
                "allocation_calls_in_selected_function"
            )
            == 0
            for item in allocation_assembly_sets
        ),
        "falsification_controls_detected": all(
            item["detected"] for item in source_controls.values()
        ),
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "call_chain_counters": call_chain_counters,
        "counter_sources": {
            "allocations": "generated_noinline_before_after_snapshot",
            "frees": "generated_noinline_before_after_snapshot",
            "payload_copies": (
                "generated_noinline_before_after_snapshot"
            ),
            "retains": "generated_helper_reference_count_call_scan",
            "releases": "generated_helper_reference_count_call_scan",
        },
        "pointer_proof": pointer_proof,
        "falsification_control": {
            "kind": "deliberate_payload_copy_injection",
            "detected": source_controls["payload_copy"]["detected"],
        },
        "falsification_controls": source_controls,
        "mir": mir_manifest,
        "meldra_noinline": {
            "build": noinline_build.to_dict(),
            "arguments": list(audit_arguments),
            "expected_checksum": expected_checksum,
            "observed_checksum": checksum(noinline_run),
            "returncode": (
                noinline_run.returncode
                if noinline_run is not None
                else None
            ),
            "metrics": noinline_metrics,
            "helpers": {
                name: {
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "overhead_scan": helper_scans[name],
                }
                for name, text in helper_sources.items()
            },
            "disassembly": noinline_disassembly,
        },
        "meldra_optimized": {
            "build": optimized_build.to_dict(),
            "arguments": list(audit_arguments),
            "expected_checksum": expected_checksum,
            "observed_checksum": checksum(optimized_run),
            "returncode": (
                optimized_run.returncode
                if optimized_run is not None
                else None
            ),
            "metrics": optimized_metrics,
            "disassembly": optimized_disassembly,
        },
        "c_control": {
            "build": control_build.to_dict(),
            "disassembly": control_disassembly,
        },
    }


def _frozen_hashes(root: Path) -> dict[str, Any]:
    expected = {
        "tools/benchmarks/merlo/benchmarks/meldra_fair_memory_strategy.json": "91f2e0e21d4464441d68f2627e46f120b182130af9c0dfa8e2c5b9f73ae6a479",
        "tools/benchmarks/merlo/benchmarks/meldra_non_elidable_region.json": "52a64e65367da925e0838e4d614d6b94493fcec40a5db685e9fcab29f3c5a55d",
        "tools/benchmarks/merlo/benchmarks/meldra_constant_knowledge_audit.json": "ca0c359171aca90efbc0318bb2d1086aa13941011d99ef3f71b31bb25907d548",
        "tools/benchmarks/merlo/benchmarks/meldra_bytes_experiment.json": "123d31cf8d4855e7cdeb41ad0069e4d13e33bf9779c4a234b440535aa25f8157",
        "tools/benchmarks/merlo/benchmarks/meldra_bytes_evidence_closure.json": "f9308bc4b34dbda6313118de20efd57636a9b97340bafa382ec30e814641f9a3",
        "tools/benchmarks/merlo/benchmarks/meldra_bytes_call_boundary.json": "b03add69373fc4646db0375b9a6ce70b0dc23b2fced2b4ca56e91e3d2b54b0df",
    }
    checks = {}
    for relative, digest in expected.items():
        path = root / relative
        observed = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        checks[relative] = {"expected_sha256": digest, "observed_sha256": observed, "match": observed == digest}
    return {"passed": all(item["match"] for item in checks.values()), "checks": checks}


def _artifact_payload_hash(report: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in report.items()
        if key != "artifact_payload_sha256"
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _falsification_controls(
    report: dict[str, Any],
) -> dict[str, Any]:
    invalid = report["correctness"]["invalid"]
    valid = report["correctness"]["valid"]
    abi = report["abi_audit"]
    compile_results = invalid["compile_results"]

    def rejected(family: str) -> bool:
        matching = [
            item for item in compile_results if item["family"] == family
        ]
        return (
            len(matching) == BYTES_REBORROW_INVALID_SEEDS
            and all(item["passed"] for item in matching)
        )

    def valid_family(family: str) -> bool:
        return valid["templates"][family]["failures"] == 0

    unoptimized = report["contracts"]["unoptimized_mir"]
    optimized = report["contracts"]["optimized_mir"]
    metadata_exact = (
        unoptimized["events"] == optimized["events"]
        and unoptimized["calls"] == optimized["calls"]
        and unoptimized["validation"] == optimized["validation"]
    )
    abi_controls = abi["falsification_controls"]
    checks = {
        "lifetime_syntax": {
            "passed": (
                report["contracts"]["hir"][
                    "lifetime_annotations_in_surface"
                ]
                == 0
            ),
            "evidence": "HIR surface lifetime annotation count",
        },
        "new_owner": {
            "passed": (
                report["contracts"]["abi"]["descriptor"][
                    "ownership_fields"
                ]
                == 0
                and abi["call_chain_counters"]["allocations"] == 0
                and abi_controls["allocation"]["detected"]
            ),
            "evidence": "ABI descriptor plus allocation mutation control",
        },
        "allocation_or_free": {
            "passed": (
                abi["call_chain_counters"]["allocations"] == 0
                and abi["call_chain_counters"]["frees"] == 0
                and abi_controls["allocation"]["detected"]
                and abi_controls["free"]["detected"]
            ),
            "evidence": "generated no-inline before/after counters",
        },
        "payload_copy": {
            "passed": (
                abi["call_chain_counters"]["payload_copies"] == 0
                and abi["checks"]["assembly_copy_calls_absent"]
                and abi_controls["payload_copy"]["detected"]
                and abi_controls["copy_loop"]["detected"]
            ),
            "evidence": "native counter, C scan, assembly, mutations",
        },
        "retain_release": {
            "passed": (
                abi["call_chain_counters"]["retains"] == 0
                and abi["call_chain_counters"]["releases"] == 0
                and abi_controls["reference_count"]["detected"]
            ),
            "evidence": "generated helper RC-call scan",
        },
        "returned_view": {
            "passed": (
                rejected("inner_returns_view")
                and rejected("outer_returns_child_view")
            ),
            "evidence": "exact compile-time reject families",
        },
        "stored_view": {
            "passed": rejected("record_stores_view"),
            "evidence": "exact record escape reject family",
        },
        "early_root_owner_availability": {
            "passed": (
                rejected("owner_mutation_during_chain")
                and rejected("owner_move_during_chain")
                and valid_family("owner_mutation_after")
                and valid_family("owner_move_after")
            ),
            "evidence": "during-chain rejects and after-chain accepts",
        },
        "child_outlives_parent": {
            "passed": (
                rejected("parent_ends_before_child")
                and rejected("child_outlives_parent")
            ),
            "evidence": "malformed MIR ancestry controls",
        },
        "optimized_metadata": {
            "passed": (
                metadata_exact
                and abi_controls["optimized_metadata"]["detected"]
            ),
            "evidence": "exact before/after metadata plus removal mutant",
        },
        "conditional_end_balance": {
            "passed": (
                rejected("branch_unbalanced_reborrow")
                and valid_family("conditional_balanced")
            ),
            "evidence": "balanced accept and unbalanced reject",
        },
        "pointer_identity": {
            "passed": (
                abi["pointer_proof"]["same_payload_pointer"]
                and abi["pointer_proof"]["child_inside_root"]
                and abi_controls["pointer_identity"]["detected"]
            ),
            "evidence": "instrumented no-inline pointer chain",
        },
    }
    return {
        "passed": all(item["passed"] for item in checks.values()),
        "checks": checks,
    }


def _decision(report: dict[str, Any]) -> tuple[str, dict[str, bool]]:
    valid = report["correctness"]["valid"]
    invalid = report["correctness"]["invalid"]
    abi = report["abi_audit"]
    counters = abi["call_chain_counters"]
    depths = {
        valid["templates"][family]["expected_depth"]
        for family in BYTES_REBORROW_VALID_FAMILIES
    }
    gates = {
        "valid_minimum": valid["case_count"] >= 256,
        "invalid_minimum": invalid["case_count"] >= 192,
        "valid_agreement": valid["unexpected_failure"] == 0,
        "invalid_exact": (
            invalid["unexpected_acceptance"] == 0
            and invalid["unexpected_failure"] == 0
        ),
        "depths_one_to_three": depths == {1, 2, 3},
        "lifetime_annotations_zero": (
            report["contracts"]["hir"][
                "lifetime_annotations_in_surface"
            ]
            == 0
        ),
        "zero_overhead": (
            all(value == 0 for value in counters.values())
            and abi["checks"]["helper_allocator_free_copy_rc_absent"]
            and abi["checks"]["helper_copy_loop_absent"]
        ),
        "non_escaping": (
            invalid["unexpected_acceptance"] == 0
            and report["falsification_controls"]["checks"][
                "returned_view"
            ]["passed"]
            and report["falsification_controls"]["checks"][
                "stored_view"
            ]["passed"]
            and report["falsification_controls"]["checks"][
                "child_outlives_parent"
            ]["passed"]
        ),
        "sanitizers": (
            report["safety"]["passed"]
            and all(
                report["safety"][name]["violations"] == 0
                for name in ("asan", "ubsan", "lsan")
            )
        ),
        "optimized_metadata": (
            abi["checks"]["optimized_metadata_preserved"]
        ),
        "falsification_controls": (
            report["falsification_controls"]["passed"]
        ),
        "abi": abi["passed"],
        "frozen_artifacts": report["frozen_artifacts"]["passed"],
        "full_suite": (
            report.get("full_suite", {}).get("passed") is True
        ),
    }
    observed_overhead = any(
        isinstance(value, int) and value != 0
        for value in counters.values()
    )
    pointer_defect = (
        abi["pointer_proof"].get("same_payload_pointer") is False
        or abi["pointer_proof"].get("child_inside_root") is False
    )
    safety_defect = (
        not report["safety"]["passed"]
        or invalid["unexpected_acceptance"] > 0
        or observed_overhead
        or pointer_defect
        or not abi["checks"]["root_owner_metadata_identical"]
        or not abi["checks"]["optimized_metadata_preserved"]
        or not abi["checks"]["assembly_copy_calls_absent"]
    )
    if safety_defect:
        return "BYTES_REBORROW_SAFETY_DEFECT", gates
    if all(gates.values()):
        return "BYTES_REBORROW_SUPPORTED", gates
    return "BYTES_REBORROW_INCOMPLETE", gates


def validate_bytes_reborrow_report(report: dict[str, Any]) -> None:
    if (
        report.get("schema_version")
        != BYTES_REBORROW_EXPERIMENT_SCHEMA_VERSION
    ):
        raise ValueError("unsupported Bytes reborrow report schema")
    if report.get("kind") != BYTES_REBORROW_EXPERIMENT_KIND:
        raise ValueError("unexpected Bytes reborrow report kind")
    valid = report["correctness"]["valid"]
    invalid = report["correctness"]["invalid"]
    if valid["case_count"] < 256:
        raise ValueError("valid reborrow corpus gate is not met")
    if invalid["case_count"] < 192:
        raise ValueError("invalid reborrow corpus gate is not met")
    if valid["family_count"] != len(BYTES_REBORROW_VALID_FAMILIES):
        raise ValueError("valid reborrow family count is inconsistent")
    if set(valid["templates"]) != set(BYTES_REBORROW_VALID_FAMILIES):
        raise ValueError("valid reborrow templates are incomplete")
    if valid["case_count"] != sum(
        item["case_count"] for item in valid["templates"].values()
    ):
        raise ValueError("valid reborrow case count is inconsistent")
    if valid["seed_count_per_template"] != BYTES_REBORROW_VALID_SEEDS:
        raise ValueError("valid reborrow seed count is inconsistent")
    if invalid["case_count"] != (
        len(invalid["compile_results"])
        + len(invalid["runtime_results"])
    ):
        raise ValueError("invalid reborrow case count is inconsistent")
    if invalid["compile_time_rejects_sanitizer_tested"] is not False:
        raise ValueError("compile-time rejects cannot be sanitizer-tested")
    if report["status"] not in {
        "BYTES_REBORROW_SUPPORTED",
        "BYTES_REBORROW_INCOMPLETE",
        "BYTES_REBORROW_SAFETY_DEFECT",
    }:
        raise ValueError("invalid Bytes reborrow status")
    expected_status, expected_gates = _decision(report)
    if report["status"] != expected_status:
        raise ValueError("Bytes reborrow status disagrees with gates")
    if report["decision_gates"] != expected_gates:
        raise ValueError("Bytes reborrow decision gates are stale")
    frozen = report["frozen_artifacts"]
    if frozen["passed"] != all(
        item["match"] for item in frozen["checks"].values()
    ):
        raise ValueError("frozen artifact summary is inconsistent")
    expected_hash = report.get("artifact_payload_sha256")
    if (
        expected_hash is not None
        and expected_hash != _artifact_payload_hash(report)
    ):
        raise ValueError("Bytes reborrow artifact payload hash mismatch")


def run_bytes_reborrow_experiment(
    *,
    output_dir: str | Path = "tools/benchmarks/merlo/benchmarks/meldra_bytes_reborrow",
    report_path: str | Path = "tools/benchmarks/merlo/benchmarks/meldra_bytes_reborrow.json",
) -> dict[str, Any]:
    started = time.perf_counter()
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    representative, _salt, _depth = valid_template_source("depth3_chain")
    (root / "representative.meldra").write_text(representative, encoding="utf-8")
    valid, optimized_by_family = _correctness_corpus(root / "correctness")
    invalid = _invalid_corpus(root / "correctness")
    safety = _sanitizers(root / "sanitizers", optimized_by_family)
    invalid["sanitizer_native_executed"] = sum(
        sum(item["executions"] for item in safety[name]["runtime_diagnostic_families"].values())
        for name in ("asan", "ubsan", "lsan")
    )
    invalid["compile_time_rejects_sanitizer_tested"] = False
    invalid["sanitizer_scope"] = (
        "accepted native and runtime-diagnostic families only"
    )
    representative_hir = compile_native_hir(representative, path="representative.meldra")
    representative_mir = compile_performance_source(representative, path="representative.meldra").mir
    representative_optimized, _ = optimize_mir(representative_mir)
    report = {
        "schema_version": BYTES_REBORROW_EXPERIMENT_SCHEMA_VERSION,
        "kind": BYTES_REBORROW_EXPERIMENT_KIND,
        "date": "2026-08-12",
        "scope": {
            "supported": "immutable BytesView compositional reborrow through direct synchronous depth-1-to-3 chains",
            "recursion": "rejected",
            "closures": "out_of_scope",
            "async": "rejected",
            "dynamic_dispatch": "rejected",
            "returned_borrowed_values": "rejected",
            "general_lifetime_inference": False,
            "timing_benchmark": "not_run_by_protocol",
            "bounds_check_optimization": "not_attempted",
        },
        "preregistration": json.loads(Path("tools/benchmarks/merlo/benchmarks/meldra_bytes_reborrow_preregistered.json").read_text(encoding="utf-8")),
        "self_skeptical_audit": json.loads(Path("tools/benchmarks/merlo/benchmarks/meldra_bytes_reborrow_self_skeptical_audit.json").read_text(encoding="utf-8")),
        "contracts": {
            "hir": bytes_reborrow_hir_manifest(representative_hir),
            "unoptimized_mir": bytes_reborrow_mir_manifest(representative_mir),
            "optimized_mir": bytes_reborrow_mir_manifest(representative_optimized),
            "abi": bytes_reborrow_abi_manifest(),
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
            "implementation_wall_time_s": time.perf_counter() - started,
        },
    }
    report["falsification_controls"] = _falsification_controls(report)
    status, gates = _decision(report)
    report["status"] = status
    report["decision_gates"] = gates
    validate_bytes_reborrow_report(report)
    report["artifact_payload_sha256"] = _artifact_payload_hash(report)
    validate_bytes_reborrow_report(report)
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


def finalize_bytes_reborrow_report(
    report: dict[str, Any], *, passed: int, failed: int, skipped: int
) -> dict[str, Any]:
    finalized = dict(report)
    finalized.pop("artifact", None)
    finalized.pop("artifact_payload_sha256", None)
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
    validate_bytes_reborrow_report(finalized)
    finalized["artifact_payload_sha256"] = _artifact_payload_hash(
        finalized
    )
    validate_bytes_reborrow_report(finalized)
    return finalized


__all__ = [
    "BYTES_REBORROW_EXPERIMENT_KIND",
    "BYTES_REBORROW_EXPERIMENT_SCHEMA_VERSION",
    "BYTES_REBORROW_INVALID_FAMILIES",
    "BYTES_REBORROW_INVALID_SEEDS",
    "BYTES_REBORROW_VALID_FAMILIES",
    "BYTES_REBORROW_VALID_SEEDS",
    "finalize_bytes_reborrow_report",
    "run_bytes_reborrow_experiment",
    "valid_cases",
    "valid_reference",
    "valid_template_source",
    "validate_bytes_reborrow_report",
]
