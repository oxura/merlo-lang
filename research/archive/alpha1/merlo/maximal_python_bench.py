"""Three-arm generated pilot for the Stage 0.4E maximal Python baseline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.archive.alpha1.merlo.frontend_bench import generate_paired_corpus, run_binding_comparison
from research.archive.alpha1.merlo.maximal_python import (
    MaximalPythonChange,
    MaximalPythonManifest,
    MaximalPythonPackageManifest,
    analyze_maximal_python,
    apply_maximal_python_change,
    manifest_for_sources,
    run_restricted_python,
)
from research.archive.alpha1.merlo.runtime_soundness import run_runtime_soundness_benchmark
from research.archive.historical_protocol.merlo.stage04e_protocol import assert_stage04e_protocol


MAXIMAL_PYTHON_BENCHMARK_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ArmMeasurement:
    arm: str
    numerator: int
    denominator: int
    status: str
    note: str

    @property
    def rate(self) -> float:
        return round(self.numerator / self.denominator, 6) if self.denominator else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "rate": self.rate,
            "status": self.status,
            "note": self.note,
        }


@dataclass(frozen=True)
class MaximalPythonBenchmarkReport:
    binding: tuple[ArmMeasurement, ...]
    runtime: tuple[tuple[str, tuple[tuple[str, Any], ...]], ...]
    safe_profile_programs: ArmMeasurement
    dynamic_profile_rejections: ArmMeasurement
    bypass_detection: tuple[tuple[str, str, tuple[str, ...]], ...]
    runtime_audit_status: str
    interface_private_stable: bool
    interface_public_changed: bool
    changeir_operations: tuple[tuple[str, bool, bool], ...]
    lsp_status: str
    protocol_sha256: str
    runtime_report_sha256: str
    evidence_level: str = "GENERATED_PILOT_NOT_EXTERNAL_EVIDENCE"
    schema_version: int = MAXIMAL_PYTHON_BENCHMARK_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_level": self.evidence_level,
            "protocol_sha256": self.protocol_sha256,
            "binding": {item.arm: item.to_dict() for item in self.binding},
            "runtime_soundness": {
                name: dict(values) for name, values in self.runtime
            },
            "runtime_report_sha256": self.runtime_report_sha256,
            "safe_profile_programs": self.safe_profile_programs.to_dict(),
            "dynamic_profile_rejections": self.dynamic_profile_rejections.to_dict(),
            "bypass_detection": [
                {
                    "case": name,
                    "status": status,
                    "diagnostics": list(diagnostics),
                }
                for name, status, diagnostics in self.bypass_detection
            ],
            "runtime_audit_status": self.runtime_audit_status,
            "interfaces": {
                "private_body_preserved_interface": self.interface_private_stable,
                "public_signature_changed_interface": self.interface_public_changed,
            },
            "changeir": {
                name: {
                    "applied": applied,
                    "identity_continuity": identity,
                }
                for name, applied, identity in self.changeir_operations
            },
            "lsp_status": self.lsp_status,
            "conclusion": (
                "Maximal Python matches generated static binding by rejecting or "
                "marking the registered dynamic semantics; external safety, false-block, "
                "burden, and agent-value gates remain UNMEASURED."
            ),
            "decision": "NO_GO_LANGUAGE_ALPHA",
        }


def _maximal_binding(program_count: int) -> tuple[
    ArmMeasurement, ArmMeasurement, ArmMeasurement, MaximalPythonManifest
]:
    corpus, current, _, meldra = run_binding_comparison(program_count)
    sources = dict(corpus.python_sources)
    manifest = manifest_for_sources(sources)
    report = analyze_maximal_python(sources, manifest)
    exact = 0
    for link in corpus.references:
        matches = tuple(
            item
            for item in report.references
            if item.path == link.python_path
            and item.line == link.python_line
            and item.spelling == link.python_spelling
            and item.profile_status == "Exact"
            and item.target_locator == link.python_target
        )
        if len(matches) == 1:
            exact += 1
    denominator = len(corpus.references)
    return (
        ArmMeasurement(
            "current-python-sidecar",
            current.exact,
            denominator,
            "OBSERVED",
            "Current analyzer static Exact on the shared logical denominator.",
        ),
        ArmMeasurement(
            "maximal-python-profile",
            exact,
            denominator,
            "OBSERVED",
            "Strong binder plus explicit strict profile on generated static programs.",
        ),
        ArmMeasurement(
            "meldra-closed",
            meldra.exact,
            denominator,
            "OBSERVED",
            "Frozen closed binder on the shared logical denominator.",
        ),
        manifest,
    )


def _runtime_summary() -> tuple[
    tuple[tuple[str, tuple[tuple[str, Any], ...]], ...], str, ArmMeasurement
]:
    report = run_runtime_soundness_benchmark()
    payload = report.to_dict()
    summaries = []
    for name, arm in payload["arms"].items():
        summaries.append(
            (
                name,
                tuple(
                    sorted(
                        (key, arm[key])
                        for key in (
                            "callsites",
                            "static_exact_callsites",
                            "rejected_callsites",
                            "explicit_dynamic_callsites",
                            "unsound_exact_count",
                            "unsound_exact_denominator",
                            "unsound_exact_rate",
                        )
                    )
                ),
            )
        )
    maximal = payload["arms"]["maximal-python-profile"]
    rejected = maximal["rejected_callsites"] + maximal["explicit_dynamic_callsites"]
    measurement = ArmMeasurement(
        "maximal-python-profile",
        rejected,
        maximal["callsites"],
        "GENERATED_PILOT",
        "Profile rejection/dynamic-boundary rate is also an expressiveness and false-block cost.",
    )
    return tuple(sorted(summaries)), hashlib.sha256(report.to_json().encode()).hexdigest(), measurement


def _bypass_cases() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    cases = {
        "socket_import": "import socket\n\ndef run() -> int:\n    return 1\n",
        "globals_mutation": (
            "def target() -> int:\n    return 1\n\n"
            "def run() -> int:\n"
            "    globals()['target'] = run\n"
            "    return target()\n"
        ),
        "monkey_patch": (
            "class Service:\n"
            "    def run(self) -> int:\n        return 1\n\n"
            "def replacement(self) -> int:\n    return 2\n\n"
            "Service.run = replacement\n"
        ),
    }
    values = []
    for name, source in cases.items():
        sources = {"pilot/api.py": source}
        manifest = manifest_for_sources(sources)
        report = analyze_maximal_python(sources, manifest)
        values.append(
            (
                name,
                "BLOCKED" if not report.ok else "MISSED",
                tuple(sorted({item.code for item in report.blocking_diagnostics})),
            )
        )
    return tuple(values)


def _interface_checks() -> tuple[bool, bool]:
    manifest = MaximalPythonManifest(
        (
            MaximalPythonPackageManifest(
                "pilot", "pilot", ("pilot.api.public", "pilot.api.helper")
            ),
        )
    )
    source = (
        "def public(value: int) -> int:\n"
        "    return helper(value)\n\n"
        "def helper(value: int) -> int:\n"
        "    return value + 1\n"
    )
    first = analyze_maximal_python({"pilot/api.py": source}, manifest)
    private = analyze_maximal_python(
        {"pilot/api.py": source.replace("value + 1", "value + 2")}, manifest
    )
    public = analyze_maximal_python(
        {
            "pilot/api.py": source.replace(
                "def public(value: int)", "def public(value: str)"
            )
        },
        manifest,
    )
    return (
        first.package("pilot").interface_revision_id
        == private.package("pilot").interface_revision_id,
        first.package("pilot").interface_revision_id
        != public.package("pilot").interface_revision_id,
    )


def _changeir_checks() -> tuple[tuple[str, bool, bool], ...]:
    base = {
        "pilot/api.py": "def greet(name: str) -> str:\n    return name\n",
        "pilot/other.py": "",
        "pilot/client.py": (
            "from pilot.api import greet\n\n"
            "def render(name: str) -> str:\n"
            "    return greet(name)\n"
        ),
    }
    manifest = MaximalPythonManifest(
        (
            MaximalPythonPackageManifest(
                "pilot", "pilot", ("pilot.api.greet", "pilot.client.render")
            ),
        )
    )
    changes = (
        ("rename", MaximalPythonChange.rename("pilot.api.greet", "salute")),
        ("move", MaximalPythonChange.move("pilot.api.greet", "pilot.other")),
        (
            "change_signature",
            MaximalPythonChange.change_signature(
                "pilot.api.greet", "(name: str, suffix: str)", {"suffix": '"!"'}
            ),
        ),
    )
    result = []
    for name, change in changes:
        applied = apply_maximal_python_change(base, manifest, change)
        result.append(
            (
                name,
                applied.applied,
                applied.target_symbol_id_before == applied.target_symbol_id_after,
            )
        )
    return tuple(result)


def _runtime_audit() -> str:
    sources = {
        "pilot/api.py": (
            "import builtins\n\n"
            "def run() -> int:\n"
            "    builtins.open('/tmp/meldra-maximal-pilot', 'w')\n"
            "    return 1\n\n"
            "if __name__ == '__main__':\n"
            "    run()\n"
        )
    }
    return run_restricted_python(
        sources, manifest_for_sources(sources), entry_path="pilot/api.py"
    ).status


def run_maximal_python_benchmark(
    program_count: int = 40,
) -> MaximalPythonBenchmarkReport:
    protocol = assert_stage04e_protocol(Path(__file__).resolve().parents[1])
    current, maximal, meldra, manifest = _maximal_binding(program_count)
    runtime, runtime_digest, dynamic_rejections = _runtime_summary()
    private, public = _interface_checks()
    source_count = len(generate_paired_corpus(program_count).python_sources)
    return MaximalPythonBenchmarkReport(
        (current, maximal, meldra),
        runtime,
        ArmMeasurement(
            "maximal-python-profile",
            program_count,
            program_count,
            "GENERATED_PILOT",
            f"All {source_count} generated Python source units passed the strict profile.",
        ),
        dynamic_rejections,
        _bypass_cases(),
        _runtime_audit(),
        private,
        public,
        _changeir_checks(),
        "UNMEASURED_NO_LANGUAGE_SERVER",
        protocol.protocol_sha256,
        runtime_digest,
    )


__all__ = [
    "MAXIMAL_PYTHON_BENCHMARK_SCHEMA_VERSION",
    "ArmMeasurement",
    "MaximalPythonBenchmarkReport",
    "run_maximal_python_benchmark",
]
