"""Clang assembly and optimization-record evidence for representative native kernels."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from research.archive.alpha1.merlo.native_bench import WORKLOADS, _c_source, _meldra_source
from merlo.native_c_backend import CEmitter, compiler_version, find_c_compiler
from tools.benchmarks.merlo.performance_frontend import compile_performance_source
from tools.benchmarks.merlo.performance_opt import optimize_mir

CODEGEN_EVIDENCE_SCHEMA_VERSION = 1


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assembly_metrics(assembly: str) -> dict[str, Any]:
    lines = assembly.splitlines()
    instruction_lines = [
        line
        for line in lines
        if re.match(r"^\s+[a-zA-Z][a-zA-Z0-9.]*\s", line)
    ]
    vector_registers = re.findall(r"%(?:xmm|ymm|zmm)\d+", assembly)
    vector_instructions = [
        line.strip()
        for line in instruction_lines
        if re.search(r"%(?:xmm|ymm|zmm)\d+", line)
    ]
    direct_calls = re.findall(r"(?m)^\s*callq?\s+(?!\*)([^\s#]+)", assembly)
    indirect_calls = re.findall(r"(?m)^\s*callq?\s+\*([^\s#]+)", assembly)
    conditional_branches = re.findall(
        r"(?m)^\s*j(?!mp\b)[a-z]+\s+([^\s#]+)", assembly
    )
    stack_adjustments = [
        line.strip()
        for line in instruction_lines
        if re.search(r"\b(?:sub|add)q?\s+\$[0-9]+,\s*%rsp", line)
    ]
    return {
        "assembly_lines": len(lines),
        "instruction_lines": len(instruction_lines),
        "vector_register_mentions": len(vector_registers),
        "vector_registers": sorted(set(vector_registers)),
        "vector_instruction_count": len(vector_instructions),
        "vector_instruction_examples": vector_instructions[:20],
        "direct_call_count": len(direct_calls),
        "direct_call_targets": sorted(set(direct_calls)),
        "indirect_call_count": len(indirect_calls),
        "conditional_branch_count": len(conditional_branches),
        "malloc_mentions": len(re.findall(r"\bmalloc\b", assembly)),
        "free_mentions": len(re.findall(r"\bfree\b", assembly)),
        "stack_adjustments": stack_adjustments[:20],
    }


def _compile_assembly(
    compiler: str,
    source: Path,
    assembly: Path,
    optimization_record: Path,
) -> dict[str, Any]:
    is_clang = "clang" in Path(compiler).name
    command = [
        compiler,
        "-std=c11",
        "-O3",
        "-fwrapv",
        "-fno-delete-null-pointer-checks",
        "-ffp-contract=off",
        "-fno-ident",
        "-S",
        str(source),
        "-o",
        str(assembly),
    ]
    if is_clang:
        command[8:8] = [
            "-fsave-optimization-record",
            f"-foptimization-record-file={optimization_record}",
            "-Rpass=loop-vectorize",
            "-Rpass-missed=loop-vectorize",
        ]
    else:
        command[8:8] = [f"-fopt-info-vec-all={optimization_record}"]
    environment = dict(os.environ, LC_ALL="C", TZ="UTC", SOURCE_DATE_EPOCH="0")
    started = time.perf_counter_ns()
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
        env=environment,
    )
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    if completed.returncode != 0 or not assembly.is_file():
        return {
            "status": "FAILED",
            "command": command,
            "compile_time_ms": elapsed_ms,
            "returncode": completed.returncode,
            "stderr": completed.stderr,
        }
    text = assembly.read_text(encoding="utf-8")
    record_exists = optimization_record.is_file()
    return {
        "status": "MEASURED",
        "command": command,
        "compile_time_ms": elapsed_ms,
        "returncode": completed.returncode,
        "source_sha256": _sha256(source),
        "assembly_sha256": _sha256(assembly),
        "optimization_record_sha256": (
            _sha256(optimization_record) if record_exists else None
        ),
        "optimization_record_present": record_exists,
        "optimization_remark_lines": [
            line
            for line in completed.stderr.splitlines()
            if "vectorized loop" in line.lower()
            or "loop not vectorized" in line.lower()
        ],
        "metrics": _assembly_metrics(text),
    }


def run_codegen_evidence(
    *,
    output_dir: str | Path = "tools/benchmarks/merlo/benchmarks/stage06p_codegen",
) -> dict[str, Any]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    compiler = find_c_compiler()
    if compiler is None:
        return {
            "schema_version": CODEGEN_EVIDENCE_SCHEMA_VERSION,
            "kind": "MeldraStage06PCodegenEvidence",
            "status": "UNMEASURED_COMPILER_UNAVAILABLE",
            "compiler": None,
            "observations": [],
            "failures": ["No Clang, GCC, or cc executable was found."],
        }
    observations = []
    failures = []
    for workload in WORKLOADS:
        workload_root = root / workload.id
        workload_root.mkdir(parents=True, exist_ok=True)
        sources = {"c": _c_source(workload)}
        arms = []
        if workload.meldra_supported:
            initial = compile_performance_source(
                _meldra_source(workload),
                path=f"codegen/{workload.id}.meldra",
            ).mir
            optimized, _ = optimize_mir(initial)
            sources["meldra"] = CEmitter(
                optimized,
                executable=True,
                runtime_arguments=True,
            ).emit()
        else:
            arms.append(
                {
                    "language": "meldra",
                    "status": "UNSUPPORTED_DECLARED",
                    "reason": workload.limitation,
                }
            )
        for language, source_text in sources.items():
            source_path = workload_root / f"{language}.c"
            assembly_path = workload_root / f"{language}.s"
            record_path = workload_root / f"{language}.opt.yaml"
            source_path.write_text(source_text, encoding="utf-8")
            result = _compile_assembly(
                compiler,
                source_path,
                assembly_path,
                record_path,
            )
            result.update(
                {
                    "language": language,
                    "source_path": str(source_path),
                    "assembly_path": str(assembly_path),
                    "optimization_record_path": str(record_path),
                }
            )
            arms.append(result)
            if result["status"] != "MEASURED":
                failures.append(
                    {
                        "workload": workload.id,
                        "language": language,
                        "status": result["status"],
                    }
                )
        observations.append(
            {
                "workload": workload.id,
                "category": workload.category,
                "arms": arms,
            }
        )
    report = {
        "schema_version": CODEGEN_EVIDENCE_SCHEMA_VERSION,
        "kind": "MeldraStage06PCodegenEvidence",
        "status": "PASS" if not failures else "FAIL",
        "compiler": compiler,
        "compiler_version": compiler_version(compiler),
        "workload_count": len(observations),
        "observations": observations,
        "failures": failures,
        "claim_boundary": (
            "Assembly counters and compiler remarks are descriptive evidence; "
            "runtime superiority is decided only by the repeated benchmark artifact."
        ),
    }
    (root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def write_codegen_evidence(
    destination: str | Path = "tools/benchmarks/merlo/benchmarks/meldra_stage06p_codegen.json",
) -> dict[str, Any]:
    report = run_codegen_evidence()
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


__all__ = [
    "CODEGEN_EVIDENCE_SCHEMA_VERSION",
    "run_codegen_evidence",
    "write_codegen_evidence",
]
