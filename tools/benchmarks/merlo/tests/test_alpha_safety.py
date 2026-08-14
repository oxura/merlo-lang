from __future__ import annotations

import copy
import hashlib
import json
import subprocess

import pytest

from tools.benchmarks.merlo.alpha_corpus import CORPUS_NAME
from tools.benchmarks.merlo import alpha_safety
from tools.benchmarks.merlo.alpha_safety import SAFETY_NAME, SANITIZERS, SCHEMA_VERSION, validate_alpha_safety_report


def _unsupported_report() -> dict[str, object]:
    records = []
    for sanitizer in SANITIZERS:
        record = {
            "case_id": "alpha-valid-0000",
            "sanitizer": sanitizer,
            "command": ["<compiler>", f"-fsanitize={sanitizer}"],
            "toolchain": None,
            "stdout": "",
            "stderr": "",
            "stdout_sha256": hashlib.sha256(b"").hexdigest(),
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
            "return_code": None,
            "status": "UNSUPPORTED",
            "expectation": "accepted",
            "executed": False,
            "reason": "no C compiler observed",
        }
        record["content_sha256"] = hashlib.sha256(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
        records.append(record)
    return {
        "schema_version": SCHEMA_VERSION,
        "name": SAFETY_NAME,
        "corpus": CORPUS_NAME,
        "scope": "representative",
        "selected_case_ids": ["alpha-valid-0000"],
        "records": records,
        "productive_safety": None,
        "status": "UNSUPPORTED",
    }


def test_alpha_safety_accepts_only_an_explicit_unsupported_observation() -> None:
    validate_alpha_safety_report(_unsupported_report())


def test_alpha_safety_rejects_pass_without_execution() -> None:
    forged = copy.deepcopy(_unsupported_report())
    record = forged["records"][0]
    record["status"] = "PASSED"
    record["reason"] = "native observation matched the declared case contract"
    forged["status"] = "FAILED"
    with pytest.raises(ValueError, match="before execution|content address"):
        validate_alpha_safety_report(forged)


def test_alpha_safety_rejects_output_digest_mismatch() -> None:
    forged = copy.deepcopy(_unsupported_report())
    forged["records"][0]["stdout"] = "forged"
    with pytest.raises(ValueError, match="output digest|content address"):
        validate_alpha_safety_report(forged)


def test_sanitizer_runner_passes_entrypoint_arguments(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    commands: list[list[str]] = []

    def completed(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", completed)
    command, _ = alpha_safety._compile_sanitized(
        "int main(void) { return 0; }",
        compiler="cc",
        sanitizer="asan",
        directory=tmp_path,
        stem="case",
        arguments=("/tmp/input",),
    )

    assert command == [str(tmp_path / "case"), "/tmp/input"]
    assert commands[-1] == command
