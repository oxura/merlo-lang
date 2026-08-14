"""Targeted prerequisite gate for direct BytesBuilder call ownership transfer."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

from research.archive.alpha1.merlo.builder_call_boundary import (
    builder_call_abi_manifest,
    builder_call_hir_manifest,
    builder_call_mir_manifest,
)
from merlo.native_c_backend import CEmitter, compile_c_source
from research.archive.alpha1.merlo.native_differential import MIRInterpreter, evaluate_hir
from research.archive.alpha1.merlo.native_hir import compile_native_hir
from tools.benchmarks.merlo.performance_frontend import PerformanceCompileError, compile_performance_source
from tools.benchmarks.merlo.performance_opt import optimize_mir

BUILDER_CALL_VALID_FAMILIES = (
    "return_identity",
    "return_push",
    "return_reserve",
    "return_extend",
    "finish_identity",
    "finish_extend",
    "balanced_conditional",
    "nested_two_calls",
)
BUILDER_CALL_INVALID_FAMILIES = (
    "caller_use_after_move",
    "double_consume",
    "active_view_transfer",
    "branch_loss_mismatch",
    "finish_one_branch",
    "two_return_aliases",
    "unconsumed_parameter",
    "return_after_drop",
    "nested_three_calls",
)
BUILDER_CALL_FULL_SEEDS = 12
_MASK64 = (1 << 64) - 1

_CHECKSUM = """fn checksum(data: BytesView, seed: UInt64) -> UInt64:
    var result: UInt64 = seed
    for i in 0..data.len():
        result = (result * 1099511628211) ^ (data[i] + i)
    return result
"""


def _valid_source(family: str) -> str:
    if family == "return_identity":
        helper = """fn transform(builder: BytesBuilder, kind: UInt64, flag: Bool) -> BytesBuilder:
    return builder
"""
        operation = "    let returned: BytesBuilder = transform(move(builder), seed, flag)\n    let frame: Bytes = returned.finish()\n"
    elif family == "return_push":
        helper = """fn transform(builder: BytesBuilder, kind: UInt64, flag: Bool) -> BytesBuilder:
    builder.push(kind & 255)
    return builder
"""
        operation = "    let returned: BytesBuilder = transform(move(builder), seed, flag)\n    let frame: Bytes = returned.finish()\n"
    elif family == "return_reserve":
        helper = """fn transform(builder: BytesBuilder, kind: UInt64, flag: Bool) -> BytesBuilder:
    builder.reserve(kind & 15)
    return builder
"""
        operation = "    let returned: BytesBuilder = transform(move(builder), seed, flag)\n    let frame: Bytes = returned.finish()\n"
    elif family == "return_extend":
        helper = """fn transform(builder: BytesBuilder, payload: BytesView) -> BytesBuilder:
    builder.extend(payload)
    return builder
"""
        operation = "    let returned: BytesBuilder = transform(move(builder), payload_view)\n    let frame: Bytes = returned.finish()\n"
    elif family == "finish_identity":
        helper = """fn transform(builder: BytesBuilder, kind: UInt64, flag: Bool) -> Bytes:
    return builder.finish()
"""
        operation = "    let frame: Bytes = transform(move(builder), seed, flag)\n"
    elif family == "finish_extend":
        helper = """fn transform(builder: BytesBuilder, payload: BytesView) -> Bytes:
    builder.extend(payload)
    return builder.finish()
"""
        operation = "    let frame: Bytes = transform(move(builder), payload_view)\n"
    elif family == "balanced_conditional":
        helper = """fn transform(builder: BytesBuilder, kind: UInt64, flag: Bool) -> BytesBuilder:
    if flag:
        builder.push(kind & 255)
    else:
        builder.push((kind + 1) & 255)
    return builder
"""
        operation = "    let returned: BytesBuilder = transform(move(builder), seed, flag)\n    let frame: Bytes = returned.finish()\n"
    elif family == "nested_two_calls":
        helper = """fn inner(builder: BytesBuilder, kind: UInt64) -> BytesBuilder:
    builder.push(kind & 255)
    return builder
fn transform(builder: BytesBuilder, kind: UInt64, flag: Bool) -> BytesBuilder:
    let returned: BytesBuilder = inner(move(builder), kind)
    returned.push((kind + 1) & 255)
    return returned
"""
        operation = "    let returned: BytesBuilder = transform(move(builder), seed, flag)\n    let frame: Bytes = returned.finish()\n"
    else:
        raise KeyError(family)
    return _CHECKSUM + helper + """fn main(n: UInt64, seed: UInt64, flag: Bool) -> UInt64:
    let payload: Bytes = Bytes.new(n)
    for i in 0..n:
        payload[i] = (seed + i * 17) & 255
    let payload_view: BytesView = payload.slice(0, n)
    let builder: BytesBuilder = BytesBuilder.new()
    for i in 0..n:
        builder.push((seed + i * 17) & 255)
""" + operation + """    let frame_view: BytesView = frame.slice(0, frame.len())
    return checksum(frame_view, seed) ^ frame.len()
"""


def _payload(family: str, n: int, seed: int, flag: bool) -> bytes:
    initial = bytearray((seed + index * 17) & 255 for index in range(n))
    extra = bytes(initial)
    if family == "return_push":
        initial.append(seed & 255)
    elif family in {"return_extend", "finish_extend"}:
        initial.extend(extra)
    elif family == "balanced_conditional":
        initial.append((seed if flag else seed + 1) & 255)
    elif family == "nested_two_calls":
        initial.extend((seed & 255, (seed + 1) & 255))
    return bytes(initial)


def _checksum(payload: bytes, seed: int) -> int:
    result = seed & _MASK64
    for index, byte in enumerate(payload):
        result = ((result * 1_099_511_628_211) ^ (byte + index)) & _MASK64
    return result ^ len(payload)


def _arguments(seed_index: int) -> tuple[int, int, bool]:
    lengths = (0, 1, 2, 7, 8, 9, 15, 16, 17, 31, 63, 127)
    return (
        lengths[seed_index % len(lengths)],
        (seed_index * 131 + 19) & _MASK64,
        bool(seed_index & 1),
    )


def _invalid_source(family: str, seed: int) -> str:
    marker = f"    let marker: UInt64 = {seed}\n"
    if family == "caller_use_after_move":
        return """fn pass_builder(builder: BytesBuilder) -> BytesBuilder:
    return builder
fn main(n: UInt64) -> UInt64:
""" + marker + """    let builder: BytesBuilder = BytesBuilder.new()
    let returned: BytesBuilder = pass_builder(move(builder))
    builder.push(marker & 255)
    return returned.len()
"""
    if family == "double_consume":
        return """fn pass_builder(builder: BytesBuilder) -> BytesBuilder:
    return builder
fn main(n: UInt64) -> UInt64:
""" + marker + """    let builder: BytesBuilder = BytesBuilder.new()
    let first: BytesBuilder = pass_builder(move(builder))
    let second: BytesBuilder = pass_builder(move(builder))
    return first.len() + second.len()
"""
    if family == "active_view_transfer":
        return """fn pass_builder(builder: BytesBuilder) -> BytesBuilder:
    return builder
fn main(n: UInt64) -> UInt64:
""" + marker + """    let builder: BytesBuilder = BytesBuilder.new()
    builder.push(marker & 255)
    let view: BytesView = builder.as_view()
    let returned: BytesBuilder = pass_builder(move(builder))
    return returned.len() + view.len()
"""
    if family == "branch_loss_mismatch":
        return """fn broken(builder: BytesBuilder, flag: Bool) -> BytesBuilder:
    if flag:
        drop(builder)
    else:
        builder.push(1)
    return builder
fn main(n: UInt64) -> UInt64:
""" + marker + "    return marker + n\n"
    if family == "finish_one_branch":
        return """fn broken(builder: BytesBuilder, flag: Bool) -> UInt64:
    if flag:
        let bytes: Bytes = builder.finish()
    else:
        builder.push(1)
    return 0
fn main(n: UInt64) -> UInt64:
""" + marker + "    return marker + n\n"
    if family == "two_return_aliases":
        return """fn broken(builder: BytesBuilder) -> BytesBuilder:
    let first: BytesBuilder = move(builder)
    let second: BytesBuilder = move(first)
    return first
fn main(n: UInt64) -> UInt64:
""" + marker + "    return marker + n\n"
    if family == "unconsumed_parameter":
        return """fn broken(builder: BytesBuilder) -> UInt64:
    return builder.len()
fn main(n: UInt64) -> UInt64:
""" + marker + "    return marker + n\n"
    if family == "return_after_drop":
        return """fn broken(builder: BytesBuilder) -> BytesBuilder:
    drop(builder)
    return builder
fn main(n: UInt64) -> UInt64:
""" + marker + "    return marker + n\n"
    if family == "nested_three_calls":
        return """fn first(builder: BytesBuilder) -> BytesBuilder:
    return builder
fn second(builder: BytesBuilder) -> BytesBuilder:
    let result: BytesBuilder = first(move(builder))
    return result
fn third(builder: BytesBuilder) -> BytesBuilder:
    let result: BytesBuilder = second(move(builder))
    return result
fn main(n: UInt64) -> UInt64:
""" + marker + """    let builder: BytesBuilder = BytesBuilder.new()
    let result: BytesBuilder = third(move(builder))
    return result.len() + marker + n
"""
    raise KeyError(family)


def _run_binary(
    binary: str, arguments: Iterable[int | bool]
) -> tuple[int | None, subprocess.CompletedProcess[str]]:
    completed = subprocess.run(
        (binary, *(str(int(item)) for item in arguments)),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env=dict(os.environ, LC_ALL="C", TZ="UTC"),
    )
    try:
        result = int(completed.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        result = None
    return result, completed


def _valid_corpus(root: Path, seeds: int) -> tuple[dict[str, Any], dict[str, str]]:
    root.mkdir(parents=True, exist_ok=True)
    failures = []
    generated_by_family: dict[str, str] = {}
    for family in BUILDER_CALL_VALID_FAMILIES:
        source = _valid_source(family)
        hir = compile_native_hir(
            source, path=f"builder-call/{family}.meldra"
        )
        original = compile_performance_source(
            source, path=f"builder-call/{family}.meldra"
        ).mir
        optimized, _snapshots = optimize_mir(original)
        generated = CEmitter(optimized, runtime_arguments=True).emit()
        generated_by_family[family] = generated
        build = compile_c_source(
            generated, output_dir=root / family, stem="program"
        )
        for seed_index in range(seeds):
            arguments = _arguments(seed_index)
            expected = _checksum(
                _payload(family, arguments[0], arguments[1], arguments[2]),
                arguments[1],
            )
            surface = evaluate_hir(hir, arguments)
            unoptimized = MIRInterpreter(original).run(arguments)
            optimized_result = MIRInterpreter(optimized).run(arguments)
            native, completed = (
                _run_binary(build.binary_path, arguments)
                if build.binary_path
                else (None, None)
            )
            observations = (surface, unoptimized, optimized_result)
            values = tuple(item.return_value for item in observations) + (
                native,
            )
            balanced = all(
                item.allocations == item.frees
                and item.finish_copies == 0
                and dict(item.final_ownership_state).get("Live", 0) == 0
                for item in observations
            )
            passed = (
                build.status == "MEASURED"
                and completed is not None
                and completed.returncode == 0
                and all(item.status == "OK" for item in observations)
                and values == (expected,) * 4
                and balanced
            )
            if not passed:
                failures.append(
                    {
                        "id": f"{family}-{seed_index:02d}",
                        "expected": expected,
                        "values": values,
                        "surface": surface.to_dict(),
                        "unoptimized": unoptimized.to_dict(),
                        "optimized": optimized_result.to_dict(),
                        "native_stderr": (
                            completed.stderr if completed else build.stderr
                        ),
                    }
                )
    cases = len(BUILDER_CALL_VALID_FAMILIES) * seeds
    return {
        "case_count": cases,
        "family_count": len(BUILDER_CALL_VALID_FAMILIES),
        "families": list(BUILDER_CALL_VALID_FAMILIES),
        "seeds_per_family": seeds,
        "layers": [
            "reference_model",
            "surface_hir",
            "unoptimized_mir",
            "optimized_mir",
            "native_binary",
        ],
        "ownership_balances_checked": cases,
        "unexpected_failure": len(failures),
        "failures": failures,
    }, generated_by_family


def _invalid_corpus(seeds: int) -> dict[str, Any]:
    failures = []
    diagnostics: dict[str, set[str]] = {
        family: set() for family in BUILDER_CALL_INVALID_FAMILIES
    }
    for family in BUILDER_CALL_INVALID_FAMILIES:
        for seed in range(seeds):
            source = _invalid_source(family, seed)
            try:
                compile_performance_source(source)
            except PerformanceCompileError as exc:
                diagnostics[family].add(str(exc))
            else:
                failures.append(
                    {
                        "id": f"{family}-{seed:02d}",
                        "unexpected_acceptance": True,
                    }
                )
    return {
        "case_count": len(BUILDER_CALL_INVALID_FAMILIES) * seeds,
        "family_count": len(BUILDER_CALL_INVALID_FAMILIES),
        "families": list(BUILDER_CALL_INVALID_FAMILIES),
        "compile_time_rejected": (
            len(BUILDER_CALL_INVALID_FAMILIES) * seeds - len(failures)
        ),
        "runtime_diagnostic": 0,
        "native_sanitizer_executed": 0,
        "diagnostic_families": {
            family: sorted(items) for family, items in diagnostics.items()
        },
        "unexpected_acceptance": len(failures),
        "unexpected_failure": 0,
        "failures": failures,
    }


_ABI_SOURCE = """fn return_builder(builder: BytesBuilder) -> BytesBuilder:
    return builder
fn finish_builder(builder: BytesBuilder) -> Bytes:
    return builder.finish()
fn main(n: UInt64) -> UInt64:
    let first: BytesBuilder = BytesBuilder.with_capacity(n + 1)
    first.push(7)
    let returned: BytesBuilder = return_builder(move(first))
    returned.push(8)
    let first_bytes: Bytes = returned.finish()
    let second: BytesBuilder = BytesBuilder.with_capacity(n + 1)
    second.push(9)
    let second_bytes: Bytes = finish_builder(move(second))
    return first_bytes.len() + second_bytes.len()
"""


def _function_source(source: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^static [^\n]*\bmeldra_fn_{re.escape(name)}\([^\n]*\) \{{.*?^\}}$",
        source,
    )
    return match.group(0) if match else ""


def _abi_audit(root: Path) -> dict[str, Any]:
    hir = compile_native_hir(_ABI_SOURCE, path="builder-call/abi.meldra")
    original = compile_performance_source(
        _ABI_SOURCE, path="builder-call/abi.meldra"
    ).mir
    optimized, _snapshots = optimize_mir(original)
    generated = CEmitter(optimized, runtime_arguments=True).emit()
    noinline = generated
    for ctype, function in (
        ("meldra_bytes_builder", "return_builder"),
        ("meldra_bytes", "finish_builder"),
    ):
        noinline = noinline.replace(
            f"static {ctype} meldra_fn_{function}",
            f"static MELDRA_NOINLINE {ctype} meldra_fn_{function}",
        )
    build = compile_c_source(
        noinline, output_dir=root / "noinline", stem="program"
    )
    returned = _function_source(noinline, "return_builder")
    finished = _function_source(noinline, "finish_builder")
    finish_descriptor = re.search(
        r"meldra_bytes\s+\w+\s*=\s*\{\s*"
        r"(\w+)\.data,\s*\1\.length,\s*\1\.capacity,\s*true\s*\}",
        finished,
    )
    forbidden = ("malloc(", "memcpy(", "memmove(", "realloc(", "refcount")
    checks = {
        "noinline_build": build.status == "MEASURED",
        "builder_descriptor_parameter": (
            "meldra_fn_return_builder(meldra_bytes_builder" in noinline
        ),
        "builder_descriptor_return": (
            "meldra_bytes_builder meldra_fn_return_builder" in noinline
        ),
        "builder_return_direct": bool(
            re.search(r"return\s+meldra_builder_\d+;", returned)
        ),
        "builder_return_pointer_identity": bool(returned),
        "builder_return_payload_copies_zero": "memcpy(" not in returned,
        "builder_return_allocations_zero": "malloc(" not in returned,
        "finish_descriptor_return": (
            "meldra_bytes meldra_fn_finish_builder" in noinline
        ),
        "finish_pointer_identity": finish_descriptor is not None,
        "finish_payload_copies_zero": "memcpy(" not in finished,
        "finish_allocations_zero": "malloc(" not in finished,
        "reference_counting_absent": all(
            token not in returned + finished for token in forbidden[4:]
        ),
        "helper_allocator_copy_rc_absent": all(
            token not in returned + finished for token in forbidden
        ),
        "single_finished_bytes_descriptor": len(
            re.findall(
                r"(?m)^\s+meldra_bytes\s+meldra_v_\d+\s*=",
                finished,
            )
        )
        == 1,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "hir": builder_call_hir_manifest(hir),
        "mir": builder_call_mir_manifest(optimized),
        "abi": builder_call_abi_manifest(),
        "return_helper": returned,
        "finish_helper": finished,
        "build": build.to_dict(),
        "generated_c_sha256": hashlib.sha256(generated.encode()).hexdigest(),
        "noinline_c_sha256": hashlib.sha256(noinline.encode()).hexdigest(),
    }


def _compile_sanitized(source: str, output: Path) -> dict[str, Any]:
    compiler = shutil.which("clang")
    output.parent.mkdir(parents=True, exist_ok=True)
    source_path = output.with_suffix(".c")
    source_path.write_text(source, encoding="utf-8")
    if compiler is None:
        return {
            "status": "UNMEASURED_COMPILER_UNAVAILABLE",
            "binary": None,
            "stderr": "clang unavailable",
        }
    command = (
        compiler,
        "-std=c11",
        "-O1",
        "-g",
        "-fno-omit-frame-pointer",
        "-fsanitize=address",
        "-fno-sanitize-recover=all",
        str(source_path),
        "-o",
        str(output),
    )
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=120
    )
    return {
        "status": "MEASURED" if completed.returncode == 0 else "FAILED",
        "binary": str(output) if completed.returncode == 0 else None,
        "command": list(command),
        "stderr": completed.stderr,
    }


def _asan(
    root: Path,
    generated_by_family: dict[str, str],
    families: Iterable[str],
) -> dict[str, Any]:
    failures = []
    executions = 0
    builds = {}
    for family in families:
        build = _compile_sanitized(
            generated_by_family[family], root / family / "program"
        )
        builds[family] = build
        if build.get("binary") is None:
            failures.append(
                {"family": family, "build_error": build.get("stderr")}
            )
            continue
        expected = _checksum(
            _payload(family, *_arguments(7)), _arguments(7)[1]
        )
        observed, completed = _run_binary(
            str(build["binary"]), _arguments(7)
        )
        executions += 1
        sanitizer_marker = any(
            marker in completed.stderr
            for marker in (
                "AddressSanitizer",
                "LeakSanitizer",
                "use-after-free",
                "double-free",
            )
        )
        if (
            completed.returncode != 0
            or observed != expected
            or sanitizer_marker
        ):
            failures.append(
                {
                    "family": family,
                    "observed": observed,
                    "expected": expected,
                    "stderr": completed.stderr,
                }
            )
    return {
        "passed": not failures,
        "native_executions": executions,
        "violations": len(failures),
        "failures": failures,
        "builds": builds,
    }


def run_builder_call_boundary_quick(
    *, output_dir: str | Path
) -> dict[str, Any]:
    root = Path(output_dir)
    valid, generated = _valid_corpus(root / "correctness", 2)
    invalid = _invalid_corpus(2)
    abi = _abi_audit(root / "abi")
    asan = _asan(
        root / "asan", generated, ("return_push", "finish_extend")
    )
    passed = (
        valid["case_count"] == 16
        and valid["unexpected_failure"] == 0
        and invalid["case_count"] == 18
        and invalid["unexpected_acceptance"] == 0
        and abi["passed"]
        and asan["passed"]
    )
    return {
        "passed": passed,
        "status": (
            "BUILDER_CALL_BOUNDARY_SUPPORTED"
            if passed
            else "BUILDER_CALL_BOUNDARY_SAFETY_DEFECT"
        ),
        "valid": valid,
        "invalid": invalid,
        "abi": abi,
        "asan": asan,
    }


def run_builder_call_boundary_gate(
    *, output_dir: str | Path
) -> dict[str, Any]:
    root = Path(output_dir)
    valid, generated = _valid_corpus(
        root / "correctness", BUILDER_CALL_FULL_SEEDS
    )
    invalid = _invalid_corpus(BUILDER_CALL_FULL_SEEDS)
    abi = _abi_audit(root / "abi")
    asan = _asan(root / "asan", generated, BUILDER_CALL_VALID_FAMILIES)
    invalid["native_sanitizer_executed"] = asan["native_executions"]
    gates = {
        "valid_minimum": valid["case_count"] >= 96,
        "invalid_minimum": invalid["case_count"] >= 96,
        "valid_families": valid["family_count"] >= 8,
        "invalid_families": invalid["family_count"] >= 8,
        "valid_agreement": valid["unexpected_failure"] == 0,
        "invalid_rejection": invalid["unexpected_acceptance"] == 0,
        "abi": abi["passed"],
        "asan": asan["passed"],
        "pointer_identity": (
            abi["checks"]["builder_return_pointer_identity"]
            and abi["checks"]["finish_pointer_identity"]
        ),
        "payload_copies_zero": (
            abi["checks"]["builder_return_payload_copies_zero"]
            and abi["checks"]["finish_payload_copies_zero"]
        ),
        "return_allocations_zero": abi["checks"][
            "builder_return_allocations_zero"
        ],
        "one_final_bytes_owner": abi["checks"][
            "single_finished_bytes_descriptor"
        ],
        "lifetime_annotations_zero": (
            abi["hir"]["lifetime_annotations_in_surface"] == 0
        ),
    }
    passed = all(gates.values())
    return {
        "passed": passed,
        "status": (
            "BUILDER_CALL_BOUNDARY_SUPPORTED"
            if passed
            else "BUILDER_CALL_BOUNDARY_SAFETY_DEFECT"
        ),
        "valid": valid,
        "invalid": invalid,
        "abi": abi,
        "asan": asan,
        "decision_gates": gates,
        "performance_runs": 0,
    }


__all__ = [
    "BUILDER_CALL_INVALID_FAMILIES",
    "BUILDER_CALL_VALID_FAMILIES",
    "run_builder_call_boundary_gate",
    "run_builder_call_boundary_quick",
]
