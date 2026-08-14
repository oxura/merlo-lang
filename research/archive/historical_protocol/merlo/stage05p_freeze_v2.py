"""Second immutable Stage 0.5P freeze recorded before Stage 0.6P changes."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from research.archive.historical_protocol.merlo.legacy_evidence import frozen_sha256
from research.archive.alpha1.merlo.native_bench import WORKLOADS, competitor_source, reference_checksum
from merlo.native_c_backend import NATIVE_BACKEND_SCHEMA_VERSION, compiler_version, find_c_compiler
from tools.benchmarks.merlo.performance_frontend import PERFORMANCE_FRONTEND_SCHEMA_VERSION
from merlo.performance_mir import PERFORMANCE_MIR_SCHEMA_VERSION
from tools.benchmarks.merlo.performance_opt import OPTIMIZATION_PIPELINE


STAGE05P_FREEZE_V2_SCHEMA_VERSION = 2
STAGE05P_FREEZE_V2_FILENAME = "meldra_stage05p_freeze_v2.json"
PERFORMANCE_SYNTAX_VERSION = 1
OPTIMIZER_PASS_VERSION = 1
MEMORY_MODEL_VERSION = 1
C_BACKEND_VERSION = 1

STAGE05P_V2_FROZEN_PATHS = (
    "meldra/performance_frontend.py",
    "meldra/performance_mir.py",
    "meldra/performance_mir_schema_v1.json",
    "meldra/performance_opt.py",
    "meldra/native_c_backend.py",
    "meldra/native_bench.py",
    "meldra/native_hypotheses.py",
    "meldra/stage05p_freeze.py",
    "meldra/stage05p_protocol.py",
    "meldra/stage05p_decision.py",
    "tools/benchmarks/merlo/tests/test_meldra_stage05p.py",
    "tools/benchmarks/merlo/benchmarks/meldra_stage05p_freeze.json",
    "tools/benchmarks/merlo/benchmarks/meldra_stage05p_protocol.json",
    "tools/benchmarks/merlo/benchmarks/meldra_stage05p_hypotheses.json",
    "tools/benchmarks/merlo/benchmarks/meldra_stage05p_decision.json",
    "tools/benchmarks/merlo/benchmarks/meldra_stage06p_stage05p_audit.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_files(root: Path) -> tuple[Path, ...]:
    corpus = root / "tools" / "benchmarks" / "merlo" / "benchmarks" / "stage05p_audit" / "run_1" / "corpus"
    suffixes = {".meldra", ".c", ".rs", ".go", ".cs", ".py"}
    return tuple(
        sorted(
            path
            for path in corpus.glob("**/*")
            if path.is_file() and path.suffix in suffixes and "__pycache__" not in path.parts
        )
    )


def _digest_paths(root: Path, paths: Iterable[Path]) -> str:
    payload = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256(path),
        }
        for path in paths
    ]
    return _canonical_digest(payload)


def _git_revision(root: Path) -> dict[str, Any]:
    command = ("git", "rev-parse", "HEAD")
    completed = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return {
        "command": list(command),
        "returncode": completed.returncode,
        "revision": completed.stdout.strip() if completed.returncode == 0 else None,
        "stderr": completed.stderr.strip(),
    }


def build_stage05p_freeze_v2(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root).resolve()
    frozen_files = {
        relative: frozen_sha256(root_path, relative)
        for relative in STAGE05P_V2_FROZEN_PATHS
    }
    workspace_digest = _canonical_digest(frozen_files)
    source_files = _source_files(root_path)
    inputs = {
        workload.id: {
            "input": workload.input,
            "input_sha256": _canonical_digest(
                {
                    "workload": workload.id,
                    "input": workload.input,
                    "algorithm": workload.algorithm,
                }
            ),
            "expected_checksum": reference_checksum(workload),
            "expected_checksum_sha256": _canonical_digest(
                {
                    "workload": workload.id,
                    "checksum": reference_checksum(workload),
                }
            ),
            "arm_source_sha256": {
                language: hashlib.sha256(
                    competitor_source(language, workload).encode("utf-8")
                ).hexdigest()
                for language in ("c", "rust", "go", "csharp", "python")
            },
        }
        for workload in WORKLOADS
    }
    compiler = find_c_compiler()
    flags = [
        "-std=c11",
        "-O3",
        "-fwrapv",
        "-fno-ident",
        "-Werror",
        "-Wl,--build-id=none",
    ]
    return {
        "schema_version": STAGE05P_FREEZE_V2_SCHEMA_VERSION,
        "kind": "MeldraStage05PFreezeV2",
        "source_revision": _git_revision(root_path),
        "workspace_content_sha256": workspace_digest,
        "versions": {
            "performance_syntax": PERFORMANCE_SYNTAX_VERSION,
            "performance_frontend_schema": PERFORMANCE_FRONTEND_SCHEMA_VERSION,
            "performance_mir_schema": PERFORMANCE_MIR_SCHEMA_VERSION,
            "optimizer_pass": OPTIMIZER_PASS_VERSION,
            "c_backend": C_BACKEND_VERSION,
            "c_backend_schema": NATIVE_BACKEND_SCHEMA_VERSION,
            "memory_model": MEMORY_MODEL_VERSION,
        },
        "performance_mir_schema": {
            "path": "meldra/performance_mir_schema_v1.json",
            "sha256": frozen_sha256(
                root_path, "meldra/performance_mir_schema_v1.json"
            ),
        },
        "optimizer_passes": {
            item.__name__: {
                "version": OPTIMIZER_PASS_VERSION,
                "implementation_sha256": frozen_files["meldra/performance_opt.py"],
            }
            for item in OPTIMIZATION_PIPELINE
        },
        "backend": {
            "kind": "portable_C11",
            "version": C_BACKEND_VERSION,
            "compiler": compiler,
            "compiler_version": compiler_version(compiler) if compiler else None,
            "flags": flags,
            "integer_overflow": "Int64 and UInt64 use two-complement wrapping; -fwrapv freezes signed C lowering",
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "libc": list(platform.libc_ver()),
        },
        "benchmark": {
            "corpus_source_count": len(source_files),
            "corpus_sha256": _digest_paths(root_path, source_files),
            "input_sha256": _canonical_digest(inputs),
            "inputs": inputs,
            "audit_reports": {
                f"run_{index}": _sha256(
                    root_path
                    / "benchmarks"
                    / "stage05p_audit"
                    / f"run_{index}"
                    / "report.json"
                )
                for index in range(1, 4)
            },
        },
        "frozen_files": frozen_files,
        "change_policy": {
            "stage05p": "FROZEN_CRITICAL_FIXES_ONLY",
            "python_sidecar": "CRITICAL_FIXES_ONLY",
            "defect_workflow": [
                "add failing regression test",
                "capture before result",
                "fix root cause",
                "increment relevant version",
                "capture after result",
                "regenerate a new versioned artifact without rewriting old evidence",
            ],
            "silent_recalculation_forbidden": True,
        },
    }


def write_stage05p_freeze_v2(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root)
    payload = build_stage05p_freeze_v2(root_path)
    (root_path / "tools" / "benchmarks" / "merlo" / "benchmarks" / STAGE05P_FREEZE_V2_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


__all__ = [
    "C_BACKEND_VERSION",
    "MEMORY_MODEL_VERSION",
    "OPTIMIZER_PASS_VERSION",
    "PERFORMANCE_SYNTAX_VERSION",
    "STAGE05P_FREEZE_V2_FILENAME",
    "STAGE05P_FREEZE_V2_SCHEMA_VERSION",
    "STAGE05P_V2_FROZEN_PATHS",
    "build_stage05p_freeze_v2",
    "write_stage05p_freeze_v2",
]
