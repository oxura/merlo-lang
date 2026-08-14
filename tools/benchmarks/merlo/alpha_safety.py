"""Executable ASan/UBSan/LSan evidence for the Merlo alpha corpus.

This module records only observations from a real production compilation and
native process.  Unsupported toolchains remain UNSUPPORTED; they are never
converted into a passing claim.  The full runner is explicit because it can
compile thousands of projects.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.benchmarks.merlo.alpha_corpus import (
    CORPUS_NAME,
    FEATURE_FAMILIES,
    generate_alpha_corpus,
    load_alpha_corpus,
    validate_alpha_corpus,
)
from merlo.compiler import compile_project
from tools.benchmarks.merlo.productive_safety import run_productive_safety, validate_productive_safety_report

SCHEMA_VERSION = 1
SAFETY_NAME = "merlo-alpha-safety"
SANITIZERS = ("asan", "ubsan", "lsan")
STATUSES = ("PASSED", "FAILED", "UNSUPPORTED")
_SANITIZER_FLAGS = {
    "asan": ("-fsanitize=address", "ASAN_OPTIONS", "halt_on_error=1:detect_leaks=1"),
    "ubsan": ("-fsanitize=undefined", "UBSAN_OPTIONS", "halt_on_error=1:print_stacktrace=1"),
    "lsan": ("-fsanitize=leak", "LSAN_OPTIONS", "exitcode=23:report_objects=0"),
}
_SANITIZER_MARKERS = ("AddressSanitizer", "UndefinedBehaviorSanitizer", "LeakSanitizer", "runtime error:")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _compiler() -> str | None:
    return shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")


def _environment(sanitizer: str) -> dict[str, str]:
    env = dict(os.environ)
    if sanitizer in _SANITIZER_FLAGS:
        env[_SANITIZER_FLAGS[sanitizer][1]] = _SANITIZER_FLAGS[sanitizer][2]
    env.update({"LC_ALL": "C", "TZ": "UTC", "SOURCE_DATE_EPOCH": "0"})
    return env


def _record(
    *, case_id: str, sanitizer: str, command: Sequence[str], toolchain: str | None,
    stdout: bytes, stderr: bytes, return_code: int | None, status: str, expectation: str,
    executed: bool, reason: str,
) -> dict[str, Any]:
    record = {
        "case_id": case_id,
        "sanitizer": sanitizer,
        "command": list(command),
        "toolchain": toolchain,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "stdout_sha256": _sha256(stdout),
        "stderr_sha256": _sha256(stderr),
        "return_code": return_code,
        "status": status,
        "expectation": expectation,
        "executed": executed,
        "reason": reason,
    }
    digest = hashlib.sha256(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()
    return {**record, "content_sha256": digest}


def _materialize_case(case: Mapping[str, Any], root: Path) -> Path:
    project = root / str(case["id"])
    (project / "src").mkdir(parents=True, exist_ok=True)
    (project / "tests").mkdir(parents=True, exist_ok=True)
    (project / "merlo.toml").write_text(str(case["manifest"]), encoding="utf-8")
    (project / "merlo.lock").write_text(str(case["lock"]), encoding="utf-8")
    (project / "src" / "main.mlo").write_text(str(case["source"]), encoding="utf-8")
    (project / "tests" / "main.mlo").write_text(str(case["test_source"]), encoding="utf-8")
    return project


def _compile_sanitized(
    source: str,
    *,
    compiler: str,
    sanitizer: str,
    directory: Path,
    stem: str,
    arguments: Sequence[str] = (),
) -> tuple[list[str], subprocess.CompletedProcess[bytes] | None]:
    directory.mkdir(parents=True, exist_ok=True)
    source_path = directory / f"{stem}.c"
    binary_path = directory / stem
    source_path.write_text(source, encoding="utf-8")
    command = [compiler, "-std=c11", "-O1", "-fno-omit-frame-pointer", _SANITIZER_FLAGS[sanitizer][0], str(source_path), "-o", str(binary_path)]
    try:
        built = subprocess.run(command, capture_output=True, check=False, shell=False, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return command, None
    if built.returncode != 0:
        return command, built
    run_command = [str(binary_path), *arguments]
    try:
        return run_command, subprocess.run(run_command, capture_output=True, check=False, shell=False, timeout=30, env=_environment(sanitizer))
    except (OSError, subprocess.TimeoutExpired):
        return run_command, None


def _run_case(case: Mapping[str, Any], sanitizer: str, compiler: str | None, directory: Path) -> dict[str, Any]:
    expectation = "accepted" if case["validity"] else str(case["expected"]["kind"])
    if compiler is None:
        return _record(case_id=str(case["id"]), sanitizer=sanitizer, command=["<compiler>", _SANITIZER_FLAGS[sanitizer][0]], toolchain=None, stdout=b"", stderr=b"", return_code=None, status="UNSUPPORTED", expectation=expectation, executed=False, reason="no C compiler observed")
    if not case["validity"]:
        # Compile-invalid cases are checked by the corpus's exact diagnostic
        # contract, not by a memory sanitizer. Runtime-invalid cases are
        # executable and are deliberately included below.
        if case["expected"]["kind"] != "runtime-invalid":
            return _record(case_id=str(case["id"]), sanitizer=sanitizer, command=[compiler, "<compile-invalid-case>"], toolchain=compiler, stdout=b"", stderr=b"", return_code=None, status="UNSUPPORTED", expectation=expectation, executed=False, reason="compile-time rejection is not a sanitizer execution")
    project = _materialize_case(case, directory / "projects")
    try:
        compilation = compile_project(project, require_interface_lock=False)
    except Exception as exc:
        data = str(exc).encode("utf-8")
        return _record(case_id=str(case["id"]), sanitizer=sanitizer, command=[compiler, "<production-compile>"], toolchain=compiler, stdout=b"", stderr=data, return_code=1, status="FAILED", expectation=expectation, executed=True, reason="production compiler rejected an executable safety case")
    command, completed = _compile_sanitized(
        compilation.generated_c,
        compiler=compiler,
        sanitizer=sanitizer,
        directory=directory / "native",
        stem=str(case["id"]),
        arguments=(str(project),),
    )
    if completed is None:
        return _record(case_id=str(case["id"]), sanitizer=sanitizer, command=command, toolchain=compiler, stdout=b"", stderr=b"", return_code=None, status="UNSUPPORTED", expectation=expectation, executed=False, reason="sanitized native process could not be exercised")
    violation = any(marker.lower() in completed.stderr.decode("utf-8", errors="replace").lower() for marker in _SANITIZER_MARKERS)
    if case["validity"]:
        expected_value = int(case["expected"]["observable"]["return_value"])
        try:
            observed = int(completed.stdout.decode("utf-8").strip().splitlines()[-1])
        except (IndexError, ValueError):
            observed = None
        passed = completed.returncode == 0 and observed == expected_value and not violation
    else:
        passed = completed.returncode != 0 and not violation
    return _record(case_id=str(case["id"]), sanitizer=sanitizer, command=command, toolchain=compiler, stdout=completed.stdout, stderr=completed.stderr, return_code=completed.returncode, status="PASSED" if passed else "FAILED", expectation=expectation, executed=True, reason="native observation matched the declared case contract" if passed else "native result or sanitizer cleanliness did not match the declared case contract")


def _status(records: Sequence[Mapping[str, Any]]) -> str:
    if not records:
        return "UNSUPPORTED"
    statuses = {str(item["status"]) for item in records}
    return "FAILED" if "FAILED" in statuses else "UNSUPPORTED" if "UNSUPPORTED" in statuses else "PASSED"


def run_alpha_safety(
    root: str | Path = ".", *, representative: bool = True, limit_per_family: int = 1,
    include_productive: bool = True, output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run real sanitizer evidence for a representative or full alpha scope."""
    corpus_path = Path(root) / "research" / "archive" / "alpha1" / "benchmarks" / "merlo_alpha_corpus.json"
    corpus = load_alpha_corpus(corpus_path) if corpus_path.exists() else generate_alpha_corpus()
    validate_alpha_corpus(corpus)
    selected: list[Mapping[str, Any]] = []
    for validity in (True, False):
        for family in FEATURE_FAMILIES:
            selected.extend(list(item for item in corpus["cases"] if item["validity"] is validity and item["family"] == family and (validity or item["expected"]["kind"] == "runtime-invalid"))[: (limit_per_family if representative else None)])
    compiler = _compiler()
    records: list[dict[str, Any]] = []
    destination = Path(output_dir).resolve() if output_dir is not None else Path(root).resolve() / ".merlo" / "alpha-safety"
    temporary_parent = destination.parent if destination.parent.exists() else None
    with tempfile.TemporaryDirectory(
        prefix="alpha-safety-", dir=temporary_parent
    ) as temporary:
        work = Path(temporary)
        for sanitizer in SANITIZERS:
            for case in selected:
                records.append(_run_case(case, sanitizer, compiler, work / sanitizer))
    productive = None
    if include_productive:
        productive = run_productive_safety(root)
        validate_productive_safety_report(productive)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "name": SAFETY_NAME,
        "corpus": CORPUS_NAME,
        "scope": "representative" if representative else "full",
        "selected_case_ids": [str(item["id"]) for item in selected],
        "records": records,
        "productive_safety": productive,
        "status": _status(records),
    }
    validate_alpha_safety_report(report)
    return report


def run_representative_alpha_safety(root: str | Path = ".", **kwargs: Any) -> dict[str, Any]:
    kwargs["representative"] = True
    return run_alpha_safety(root, **kwargs)


def run_full_alpha_safety(root: str | Path = ".", **kwargs: Any) -> dict[str, Any]:
    kwargs["representative"] = False
    kwargs["limit_per_family"] = 1
    return run_alpha_safety(root, **kwargs)


def validate_alpha_safety_report(report: Mapping[str, Any]) -> None:
    required = {"schema_version", "name", "corpus", "scope", "selected_case_ids", "records", "productive_safety", "status"}
    if not isinstance(report, Mapping) or set(report) != required:
        raise ValueError("alpha safety report fields are missing or forged")
    if report.get("schema_version") != SCHEMA_VERSION or report.get("name") != SAFETY_NAME or report.get("corpus") != CORPUS_NAME:
        raise ValueError("unsupported alpha safety report")
    selected = report.get("selected_case_ids")
    records = report.get("records")
    if not isinstance(selected, list) or len(selected) != len(set(selected)) or not isinstance(records, list) or not records:
        raise ValueError("alpha safety scope is empty or duplicated")
    expected_keys = {"case_id", "sanitizer", "command", "toolchain", "stdout", "stderr", "stdout_sha256", "stderr_sha256", "return_code", "status", "expectation", "executed", "reason", "content_sha256"}
    expected_pairs = {(str(case_id), sanitizer) for case_id in selected for sanitizer in SANITIZERS}
    observed_pairs: set[tuple[str, str]] = set()
    for record in records:
        if not isinstance(record, Mapping) or set(record) != expected_keys:
            raise ValueError("alpha safety record fields are missing or forged")
        pair = (str(record["case_id"]), str(record["sanitizer"]))
        if pair in observed_pairs:
            raise ValueError("alpha safety records contain duplicate case/sanitizer pairs")
        observed_pairs.add(pair)
        if pair not in expected_pairs or record["status"] not in STATUSES:
            raise ValueError("alpha safety record scope is invalid")
        if not isinstance(record["command"], list) or not record["command"] or not all(isinstance(item, str) and item for item in record["command"]):
            raise ValueError("alpha safety command is not an argv record")
        stdout = str(record["stdout"]).encode("utf-8")
        stderr = str(record["stderr"]).encode("utf-8")
        if record["stdout_sha256"] != _sha256(stdout) or record["stderr_sha256"] != _sha256(stderr):
            raise ValueError("alpha safety output digest does not match captured output")
        unsigned = dict(record)
        content_digest = unsigned.pop("content_sha256", None)
        if content_digest != hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest():
            raise ValueError("alpha safety content address does not match record")
        if record["status"] == "PASSED" and (record["executed"] is not True or record["return_code"] is None):
            raise ValueError("alpha safety cannot claim pass before execution")
        if record["status"] == "FAILED" and record["executed"] is not True:
            raise ValueError("failed alpha safety record lacks execution evidence")
        if record["status"] == "UNSUPPORTED" and record["executed"] is True and record["return_code"] is not None:
            raise ValueError("unsupported alpha safety record has an executed return status")
        if record["status"] == "PASSED" and any(marker.lower() in str(record["stderr"]).lower() for marker in _SANITIZER_MARKERS):
            raise ValueError("alpha safety pass contains a sanitizer violation")
    if observed_pairs != expected_pairs:
        raise ValueError("alpha safety records omit or add case/sanitizer pairs")
    if report.get("status") != _status(records):
        raise ValueError("alpha safety status is forged")
    productive = report.get("productive_safety")
    if productive is not None:
        validate_productive_safety_report(productive)


__all__ = [
    "SANITIZERS", "SAFETY_NAME", "SCHEMA_VERSION", "run_alpha_safety", "run_full_alpha_safety",
    "run_representative_alpha_safety", "validate_alpha_safety_report",
]
