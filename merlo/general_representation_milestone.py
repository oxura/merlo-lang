"""One-shot evidence runner for the General Representation Core milestone."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from .general_json_oracle import evaluate_python_oracle
from .general_representation_corpus import (
    invalid_json_cases,
    layout_sources,
    valid_json_cases,
)
from .general_representation_falsification import run_falsification_controls
from .representation_c_backend import write_general_c
from .representation_ir import (
    RepresentationCompileError,
    build_type_descriptors,
    lower_structured_hir_to_rir,
    validate_recursive_layouts,
)
from .representation_mir import (
    evaluate_general_mir,
    evaluate_representation_ir,
    lower_rir_to_performance_mir,
    optimize_general_mir,
)
from .representation_runtime import evaluate_structured_hir, exercise_vec_box_runtime
from .structured_hir_v2 import compile_structured_hir, compile_structured_hir_file


SUPPORTED = "GENERAL_REPRESENTATION_CORE_SUPPORTED"
INCOMPLETE = "GENERAL_REPRESENTATION_CORE_INCOMPLETE"
SAFETY_DEFECT = "GENERAL_REPRESENTATION_CORE_SAFETY_DEFECT"
ARCHITECTURE_RETHINK = "ARCHITECTURE_RETHINK_REQUIRED"
_ALLOWED_STATUSES = {SUPPORTED, INCOMPLETE, SAFETY_DEFECT, ARCHITECTURE_RETHINK}
_FORBIDDEN_DOMAIN_OPS = {
    "json_token_checksum",
    "json_tokenize",
    "json_parse",
    "json_decode",
    "json_build_ast",
    "json_pretty_print",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _compile_native(root: Path, source: Path, output: Path, sanitizers: bool = False) -> None:
    clang = shutil.which("clang")
    if clang is None:
        raise RuntimeError("clang is required for the milestone")
    flags = ["-std=c11", "-Wall", "-Wextra"]
    if sanitizers:
        flags.extend(
            [
                "-O1",
                "-g",
                "-fno-omit-frame-pointer",
                "-fsanitize=address,undefined,leak",
            ]
        )
    else:
        flags.extend(["-O2", "-DNDEBUG"])
    completed = subprocess.run(
        [clang, *flags, str(source), "-o", str(output)],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"C compilation failed: {completed.stderr}")


def _native_observation(binary: Path, payload: bytes, *, root: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [str(binary)],
        cwd=root,
        input=payload,
        capture_output=True,
        check=False,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    lines = stdout.splitlines()
    if not lines:
        return {
            "status": "PROCESS_FAILURE",
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    ok = re.fullmatch(
        r"OK checksum=(\d+) nodes=(\d+) arrays=(\d+) objects=(\d+) fields=(\d+)",
        lines[0],
    )
    error = re.fullmatch(r"ERROR kind=(\d+) offset=(\d+)", lines[0])
    metrics = {}
    if len(lines) >= 2 and lines[1].startswith("MERLO_METRICS "):
        metrics = {
            name: int(value)
            for name, value in re.findall(r"([a-z_]+)=(\d+)", lines[1])
        }
    if ok is not None:
        result = {
            name: int(value)
            for name, value in zip(
                ("checksum", "nodes", "arrays", "objects", "fields"),
                ok.groups(),
                strict=True,
            )
        }
        status = "OK"
    elif error is not None:
        result = {"error": int(error.group(1)), "error_offset": int(error.group(2))}
        status = "ERROR"
    else:
        result = None
        status = "PROCESS_FAILURE"
    return {
        "status": status,
        "result": result,
        "metrics": metrics,
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def _balanced_native_metrics(metrics: dict[str, int]) -> bool:
    return (
        metrics.get("allocations") == metrics.get("frees")
        and metrics.get("text_allocations") == metrics.get("text_frees")
        and metrics.get("vec_allocations") == metrics.get("vec_frees")
        and metrics.get("box_allocations") == metrics.get("box_frees")
        and metrics.get("ast_nodes_allocated") == metrics.get("ast_nodes_freed")
        and metrics.get("vec_initialized") == metrics.get("vec_elements_dropped")
    )


def _error_names(hir: Any) -> dict[int, str]:
    return {item.tag: item.name for item in hir.type_decl("ErrorKind").variants}


def _run_json_corpus(
    hir: Any,
    representation: Any,
    mir: Any,
    optimized: Any,
    native_binary: Path,
    *,
    root: Path,
) -> dict[str, Any]:
    valid = valid_json_cases()
    invalid = invalid_json_cases()
    mismatches: list[dict[str, Any]] = []
    layer_counts = Counter()
    valid_families = Counter(item.family for item in valid)
    invalid_families = Counter(item.family for item in invalid)
    error_families = Counter()
    oracle_error_families = Counter()
    balanced_cases = 0
    native_balanced_cases = 0
    allocated = Counter()
    names = _error_names(hir)

    for case in [*valid, *invalid]:
        oracle = evaluate_python_oracle(case.payload)
        layer_results = {
            "hir": evaluate_structured_hir(hir, representation, case.payload),
            "rir": evaluate_representation_ir(hir, representation, case.payload),
            "mir": evaluate_general_mir(hir, representation, mir, case.payload),
            "optimized_mir": evaluate_general_mir(
                hir, representation, optimized, case.payload
            ),
        }
        native = _native_observation(native_binary, case.payload, root=root)
        for name, result in layer_results.items():
            layer_counts[name] += 1
            if result.ownership_balanced:
                balanced_cases += 1
        if _balanced_native_metrics(native.get("metrics", {})):
            native_balanced_cases += 1
        for name, value in layer_results["hir"].metrics.items():
            allocated[name] += value

        first = layer_results["hir"]
        layer_payloads = [item.to_dict() for item in layer_results.values()]
        layers_equal = all(item == layer_payloads[0] for item in layer_payloads[1:])
        issue: list[str] = []
        if not layers_equal:
            issue.append("layer_disagreement")
        if case.valid:
            expected = {
                "nodes": oracle.nodes,
                "arrays": oracle.arrays,
                "objects": oracle.objects,
                "fields": oracle.fields,
                "checksum": oracle.checksum,
            }
            if not oracle.ok or first.status != "OK":
                issue.append("valid_rejected")
            elif any(first.result[name] != value for name, value in expected.items()):
                issue.append("oracle_value_mismatch")
            if native.get("status") != "OK" or native.get("result") != expected:
                issue.append("native_value_mismatch")
        else:
            if oracle.ok or first.status != "ERROR":
                issue.append("invalid_accepted")
            if native.get("status") != "ERROR":
                issue.append("native_invalid_accepted")
            elif native["result"] != {
                "error": first.result["error"],
                "error_offset": first.result["error_offset"],
            }:
                issue.append("native_diagnostic_mismatch")
            error_families[names[first.result["error"]]] += 1
            oracle_error_families[str(oracle.error_family)] += 1
        if not all(item.ownership_balanced for item in layer_results.values()):
            issue.append("runtime_ownership_imbalance")
        if not _balanced_native_metrics(native.get("metrics", {})):
            issue.append("native_ownership_imbalance")
        if issue:
            mismatches.append(
                {
                    "case_id": case.case_id,
                    "family": case.family,
                    "issues": issue,
                    "oracle": oracle.to_dict(),
                    "hir": first.to_dict(),
                    "native": native,
                }
            )

    total = len(valid) + len(invalid)
    return {
        "valid_cases": len(valid),
        "invalid_cases": len(invalid),
        "total_cases": total,
        "valid_family_counts": dict(sorted(valid_families.items())),
        "invalid_family_counts": dict(sorted(invalid_families.items())),
        "partitions": dict(
            sorted(Counter(item.partition for item in [*valid, *invalid]).items())
        ),
        "layer_evaluations": dict(sorted(layer_counts.items())),
        "python_oracle_evaluations": total,
        "native_evaluations": total,
        "balanced_layer_evaluations": balanced_cases,
        "balanced_native_evaluations": native_balanced_cases,
        "diagnostic_family_counts": dict(sorted(error_families.items())),
        "python_oracle_error_family_counts": dict(
            sorted(oracle_error_families.items())
        ),
        "aggregate_runtime_metrics": dict(sorted(allocated.items())),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "passed": len(mismatches) == 0,
    }


def _run_layout_corpus() -> dict[str, Any]:
    valid, invalid = layout_sources()
    mismatches = []
    valid_families = Counter(name.rsplit("_", 1)[0] for name, _ in valid)
    invalid_families = Counter(name.rsplit("_", 1)[0] for name, _ in invalid)
    typed_diagnostics = Counter()
    for name, source in valid:
        hir = compile_structured_hir(source, path=f"layout/{name}.mlo")
        validation = validate_recursive_layouts(hir.types)
        try:
            build_type_descriptors(hir)
            descriptors_built = True
        except RepresentationCompileError:
            descriptors_built = False
        if not validation.accepted or not descriptors_built:
            mismatches.append(
                {
                    "case_id": name,
                    "expected": "accepted",
                    "validation": validation.to_dict(),
                    "descriptors_built": descriptors_built,
                }
            )
    for name, source in invalid:
        hir = compile_structured_hir(source, path=f"layout/{name}.mlo")
        validation = validate_recursive_layouts(hir.types)
        rejected_by_builder = False
        try:
            build_type_descriptors(hir)
        except RepresentationCompileError as exc:
            rejected_by_builder = True
            typed_diagnostics[str(exc).split(":", 1)[0]] += 1
        if validation.accepted or not rejected_by_builder:
            mismatches.append(
                {
                    "case_id": name,
                    "expected": "rejected",
                    "validation": validation.to_dict(),
                    "rejected_by_builder": rejected_by_builder,
                }
            )
    return {
        "valid_cases": len(valid),
        "invalid_cases": len(invalid),
        "valid_family_counts": dict(sorted(valid_families.items())),
        "invalid_family_counts": dict(sorted(invalid_families.items())),
        "typed_diagnostic_counts": dict(sorted(typed_diagnostics.items())),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "passed": len(mismatches) == 0,
    }


def _safety_payloads() -> list[tuple[str, bytes, bool]]:
    return [
        ("empty_scalar_ast", b"null", True),
        ("deep_array_128", b"[" * 128 + b"null" + b"]" * 128, True),
        ("deep_object_128", b'{"a":' * 128 + b"null" + b"}" * 128, True),
        ("mixed_recursive", b'{"a":[null,{"b":[true,false,"x"]}],"c":3}', True),
        ("large_vec_growth", json.dumps(list(range(4096)), separators=(",", ":")).encode(), True),
        ("partially_initialized_vec", b"[1,2,3,", False),
        ("parse_failure_cleanup", b'{"a":[{"b":"owned"},', False),
        ("recursive_drop", b'{"a":[[[{"b":[1,2,3]}]]]}', True),
        ("depth_limit_failure", b"[" * 129 + b"null" + b"]" * 129, False),
        ("invalid_utf8", b'"\x80"', False),
    ]


def _run_sanitizers(root: Path, source: Path, artifact: Path) -> dict[str, Any]:
    binary = artifact / "merlo_json_sanitized"
    _compile_native(root, source, binary, sanitizers=True)
    environment = dict(os.environ)
    environment["ASAN_OPTIONS"] = "detect_leaks=1:halt_on_error=1"
    environment["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
    cases = []
    violations = []
    for name, payload, valid in _safety_payloads():
        completed = subprocess.run(
            [str(binary)],
            cwd=root,
            input=payload,
            capture_output=True,
            env=environment,
            check=False,
        )
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        expected_returncode = 0 if valid else 2
        sanitizer_failure = any(
            marker in stderr
            for marker in (
                "ERROR: AddressSanitizer",
                "ERROR: LeakSanitizer",
                "runtime error:",
                "UndefinedBehaviorSanitizer",
            )
        )
        observation = _native_observation(binary, payload, root=root)
        passed = (
            completed.returncode == expected_returncode
            and not sanitizer_failure
            and _balanced_native_metrics(observation.get("metrics", {}))
        )
        cases.append(
            {
                "name": name,
                "valid": valid,
                "returncode": completed.returncode,
                "sanitizer_failure": sanitizer_failure,
                "ownership_balanced": _balanced_native_metrics(
                    observation.get("metrics", {})
                ),
                "passed": passed,
            }
        )
        if not passed:
            violations.append(
                {
                    "name": name,
                    "returncode": completed.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "observation": observation,
                }
            )
    return {
        "address_sanitizer": True,
        "undefined_behavior_sanitizer": True,
        "leak_sanitizer": True,
        "cases": cases,
        "case_count": len(cases),
        "violation_count": len(violations),
        "violations": violations,
        "zero_leaks": not any("LeakSanitizer" in item.get("stderr", "") for item in violations),
        "zero_use_after_free": not any("use-after-free" in item.get("stderr", "") for item in violations),
        "zero_double_free": not any("double-free" in item.get("stderr", "") for item in violations),
        "zero_out_of_bounds": not any("buffer-overflow" in item.get("stderr", "") for item in violations),
        "passed": len(violations) == 0,
    }


def _validate_correction(root: Path) -> dict[str, Any]:
    path = root / "benchmarks" / "merlo_evidence_correction_v1.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for item in manifest["frozen_predecessors"]:
        predecessor = root / item["path"]
        actual = _sha256(predecessor)
        entries.append(
            {
                "path": item["path"],
                "expected_sha256": item["sha256"],
                "actual_sha256": actual,
                "valid": actual == item["sha256"],
            }
        )
    component_statuses = {
        item["component"]: item["status"] for item in manifest["entries"]
    }
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "contract": manifest["contract"],
        "does_not_modify_predecessors": manifest["does_not_modify_predecessors"],
        "frozen_predecessors": entries,
        "component_statuses": component_statuses,
        "passed": manifest["does_not_modify_predecessors"]
        and component_statuses.get("JSON tokenizer as ordinary Merlo program") == "SUPPORTED"
        and component_statuses.get("JSON tokenizer through opaque C intrinsic") == "HISTORICAL_PROTOTYPE_ONLY"
        and all(item["valid"] for item in entries),
    }


def validate_general_representation_report(report: dict[str, Any]) -> None:
    if report.get("status") not in _ALLOWED_STATUSES:
        raise ValueError("invalid or missing final milestone status")
    if report.get("contract") != "merlo.general-representation-core.v1":
        raise ValueError("invalid milestone contract")
    required = (
        "architecture",
        "primitive_policy",
        "json_corpus",
        "layout_corpus",
        "safety",
        "falsification",
        "performance",
        "surface",
        "correction",
        "limitations",
    )
    if any(name not in report for name in required):
        raise ValueError("incomplete milestone artifact")
    expected_supported = all(
        (
            report["json_corpus"]["passed"],
            report["layout_corpus"]["passed"],
            report["safety"]["passed"],
            report["falsification"]["passed"],
            report["performance"]["performance_gate_passed"],
            all(report["surface"]["gate"].values()),
            report["primitive_policy"]["passed"],
            report["correction"]["passed"],
        )
    )
    if (report["status"] == SUPPORTED) != expected_supported:
        raise ValueError("final status does not match evidence gates")


def run_general_representation_milestone(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root).resolve()
    artifact = root_path / "benchmarks" / "general_representation"
    artifact.mkdir(parents=True, exist_ok=True)
    source_path = root_path / "merlo" / "programs" / "general_json.mlo"
    hir = compile_structured_hir_file(source_path)
    representation = lower_structured_hir_to_rir(hir)
    mir = lower_rir_to_performance_mir(hir, representation)
    optimized = optimize_general_mir(mir)
    generated = write_general_c(
        artifact / "generated_json.c", hir, representation, optimized
    )
    _write_json(artifact / "structured_hir_v2.json", hir.to_dict())
    _write_json(artifact / "representation_ir_v1.json", representation.to_dict())
    _write_json(artifact / "performance_mir.json", mir.to_dict())
    _write_json(artifact / "performance_mir_optimized.json", optimized.to_dict())
    native_binary = artifact / "merlo_json"
    _compile_native(root_path, artifact / "generated_json.c", native_binary)

    json_corpus = _run_json_corpus(
        hir, representation, mir, optimized, native_binary, root=root_path
    )
    layout_corpus = _run_layout_corpus()
    safety = _run_sanitizers(root_path, artifact / "generated_json.c", artifact)
    parse_error = evaluate_structured_hir(
        hir, representation, b'{"a":[1,]}'
    )
    falsification = run_falsification_controls(
        hir,
        representation,
        mir,
        optimized,
        generated.source,
        parse_error.metrics,
    )
    primitive_manifest = list(generated.primitive_manifest)
    primitive_policy = {
        "allowed_primitives": primitive_manifest,
        "forbidden_domain_intrinsics": sorted(_FORBIDDEN_DOMAIN_OPS),
        "domain_opaque_calls": list(generated.domain_opaque_calls),
        "all_signatures_recorded": all(item["type_signature"] for item in primitive_manifest),
        "all_ownership_recorded": all(item["ownership_behavior"] for item in primitive_manifest),
        "all_effects_recorded": all(item["effect"] for item in primitive_manifest),
        "all_allocation_copy_failure_recorded": all(
            all(key in item for key in ("may_allocate", "may_copy", "may_fail"))
            for item in primitive_manifest
        ),
        "all_complexity_recorded": all(item["complexity"] for item in primitive_manifest),
        "all_implementation_sizes_recorded": all(
            item["handwritten_implementation_size_lines"] >= 0
            for item in primitive_manifest
        ),
        "passed": not generated.domain_opaque_calls
        and all(name not in generated.source for name in _FORBIDDEN_DOMAIN_OPS),
    }
    benchmark_path = (
        root_path / "benchmarks" / "merlo_general_representation_benchmark.json"
    )
    performance = json.loads(benchmark_path.read_text(encoding="utf-8"))
    correction = _validate_correction(root_path)
    runtime_smoke = exercise_vec_box_runtime(representation)
    hir_kinds = sorted(
        {node.kind for function in hir.functions for node in function.walk()}
    )
    rir_ops = sorted(
        {item.op for function in representation.functions for item in function.walk()}
    )
    mir_ops = sorted(
        {
            item.op
            for function in mir.functions
            for block in function.blocks
            for item in block.instructions
        }
    )
    architecture = {
        "source_path": source_path.relative_to(root_path).as_posix(),
        "source_extension": source_path.suffix,
        "structured_hir": {
            "contract": hir.contract,
            "digest": hir.digest,
            "types": len(hir.types),
            "functions": len(hir.functions),
            "node_kinds": hir_kinds,
        },
        "representation_ir": {
            "contract": representation.contract,
            "digest": representation.digest,
            "descriptors": len(representation.descriptors),
            "drop_plans": len(representation.drop_plans),
            "operation_kinds": rir_ops,
        },
        "performance_mir": {
            "contract": mir.contract,
            "digest": mir.digest,
            "optimized_digest": optimized.digest,
            "instruction_count": mir.instruction_count,
            "optimized_instruction_count": optimized.instruction_count,
            "optimization_passes": list(optimized.optimization_passes),
            "operation_kinds": mir_ops,
        },
        "c_backend": {
            **generated.to_dict(),
            "generated_source_path": "benchmarks/general_representation/generated_json.c",
            "generated_source_sha256": _sha256(
                artifact / "generated_json.c"
            ),
            "native_binary_path": "benchmarks/general_representation/merlo_json",
            "host_scope": "stdin/stdout, repeat parsing, and process exit only",
        },
        "representative_forms": {
            "structured_hir": "benchmarks/general_representation/structured_hir_v2.json",
            "representation_ir": "benchmarks/general_representation/representation_ir_v1.json",
            "performance_mir": "benchmarks/general_representation/performance_mir.json",
            "optimized_mir": "benchmarks/general_representation/performance_mir_optimized.json",
        },
        "runtime_vec_box_smoke": runtime_smoke,
    }
    surface = {
        "metrics": performance["surface"],
        "gate": performance["surface_gate"],
        "comparison": "Descriptive source-surface counts only; no claim of human simplicity.",
    }
    gates = {
        "json_corpus": json_corpus["passed"],
        "layout_corpus": layout_corpus["passed"],
        "safety": safety["passed"],
        "falsification": falsification["passed"],
        "performance": performance["performance_gate_passed"],
        "surface": all(surface["gate"].values()),
        "primitive_policy": primitive_policy["passed"],
        "correction": correction["passed"],
    }
    if not safety["passed"]:
        status = SAFETY_DEFECT
    elif not primitive_policy["passed"]:
        status = ARCHITECTURE_RETHINK
    elif all(gates.values()):
        status = SUPPORTED
    else:
        status = INCOMPLETE
    report = {
        "schema_version": 1,
        "contract": "merlo.general-representation-core.v1",
        "status": status,
        "gates": gates,
        "architecture": architecture,
        "primitive_policy": primitive_policy,
        "json_corpus": json_corpus,
        "layout_corpus": layout_corpus,
        "safety": safety,
        "falsification": falsification,
        "performance": performance,
        "surface": surface,
        "correction": correction,
        "limitations": [
            "The JSON and layout corpora are deterministic internal generated corpora, not external author implementations.",
            "The Rust performance arm was unavailable because rustc was absent.",
            "The first benchmark run persisted medians and relative MAD but not raw samples or bootstrap intervals.",
            "The C benchmark arm is independent and behavior-equivalent on the frozen benchmark input; the full 1,000-case correctness corpus compares the Merlo binary against the Python oracle.",
            "Recursive drop is bounded by the program depth limit of 128 rather than unbounded host-stack recursion.",
            "This milestone covers the representation core and JSON tool, not flow, machine, async, network, package management, macros, UI, GPU, or a general ecosystem.",
        ],
        "next_action": "Extend only with another evidence-gated real program; do not broaden the runtime before preserving these gates.",
    }
    validate_general_representation_report(report)
    output = root_path / "benchmarks" / "merlo_general_representation_core.json"
    _write_json(output, report)
    return report


__all__ = [
    "ARCHITECTURE_RETHINK",
    "INCOMPLETE",
    "SAFETY_DEFECT",
    "SUPPORTED",
    "run_general_representation_milestone",
    "validate_general_representation_report",
]
