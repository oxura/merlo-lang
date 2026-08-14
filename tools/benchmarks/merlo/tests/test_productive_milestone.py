from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.benchmarks.merlo.productive_milestone import (
    PRODUCTIVE_CORE_INCOMPLETE,
    PRODUCTIVE_CORE_SAFETY_DEFECT,
    PRODUCTIVE_CORE_SUPPORTED,
    build_productive_report,
    validate_productive_report,
    write_productive_report_once,
)


class ProductiveMilestoneTests(unittest.TestCase):
    def _evidence(self) -> dict[str, object]:
        return {
            "corpus": {
                "plan": {"kind": "productive-corpus-plan"},
                "execution": {"status": "MEASURED", "passed": True},
            },
            "external_fixtures": {"passed": True, "fixtures": []},
            "map_evidence": {"passed": True, "status": "MEASURED"},
            "resources": {"passed": True, "status": "MEASURED"},
            "cli": {"passed": True, "status": "MEASURED"},
            "applications": {"passed": True, "status": "MEASURED"},
            "safety": {
                "passed": True,
                "records": [{"executed": True, "sanitizer": "none", "status": "PASSED"}],
            },
            "falsification": {"passed": True},
            "performance": {
                "passed": True,
                "status": "MEASURED",
                "old_microbenchmarks": 0,
                "python_sidecar_benchmarks": 0,
            },
            "simplicity": {"passed": True, "status": "MEASURED"},
            "ai_change_corpus": {
                "trial_execution_status": "NOT_EXECUTED",
                "tasks": [{"id": index} for index in range(18)],
            },
            "full_suite": {"passed": True, "status": "PASS"},
        }

    def _build(self, root: Path, **changes: object) -> dict[str, object]:
        evidence = self._evidence()
        evidence.update(changes)
        return build_productive_report(root=root, **evidence)

    def test_supported_requires_all_phase_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools" / "benchmarks" / "merlo" / "benchmarks").mkdir(parents=True)
            (root / "research/archive/alpha1/benchmarks/merlo_concise_application_alpha.json").parent.mkdir(parents=True, exist_ok=True)
            (root / "research/archive/alpha1/benchmarks/merlo_concise_application_alpha.json").write_text("alpha", encoding="utf-8")
            (root / "tools/benchmarks/merlo/benchmarks/merlo_general_representation_core.json").write_text("general", encoding="utf-8")
            report = self._build(root)
            self.assertEqual(report["status"], PRODUCTIVE_CORE_SUPPORTED)
            validate_productive_report(report, root=root)

    def test_nested_false_performance_gate_rejects_supported_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            performance = dict(self._evidence()["performance"])
            performance["gates"] = {"exact_measurement": False}
            report = self._build(root, performance=performance)
            self.assertEqual(report["status"], PRODUCTIVE_CORE_INCOMPLETE)

    def test_validated_safety_schema_without_top_level_passed_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools" / "benchmarks" / "merlo" / "benchmarks").mkdir(parents=True)
            (root / "research/archive/alpha1/benchmarks/merlo_concise_application_alpha.json").parent.mkdir(parents=True, exist_ok=True)
            (root / "research/archive/alpha1/benchmarks/merlo_concise_application_alpha.json").write_text("alpha", encoding="utf-8")
            (root / "tools/benchmarks/merlo/benchmarks/merlo_general_representation_core.json").write_text("general", encoding="utf-8")
            report = self._build(
                root,
                safety={
                    "aggregate_proofs": {"all_relevant_executable_checks": True},
                    "invariants": {
                        "map_cleanup": "PASSED",
                        "resource_cleanup": "PASSED",
                    },
                    "records": [
                        {
                            "command": ["/tmp/map-check"],
                            "exit_code": 0,
                            "sanitizer": "asan",
                            "status": "PASSED",
                        }
                    ],
                    "status": "PASSED",
                },
            )
            self.assertEqual(report["status"], PRODUCTIVE_CORE_SUPPORTED)
            validate_productive_report(report, root=root)

    def test_failed_sanitizer_record_selects_safety_defect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._build(
                root,
                safety={
                    "records": [
                        {
                            "command": ["/tmp/map-check"],
                            "exit_code": 1,
                            "sanitizer": "asan",
                            "status": "FAILED",
                        }
                    ]
                },
            )
            self.assertEqual(report["status"], PRODUCTIVE_CORE_SAFETY_DEFECT)


    def test_incomplete_execution_or_unsupported_record_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._build(
                root,
                corpus={"plan": {"kind": "plan-only"}, "execution": {"status": "UNSUPPORTED", "passed": False}},
            )
            self.assertEqual(report["status"], PRODUCTIVE_CORE_INCOMPLETE)

    def test_safety_defect_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._build(
                root,
                safety={
                    "passed": False,
                    "records": [
                        {
                            "executed": True,
                            "sanitizer": "asan",
                            "status": "FAILED",
                            "defect": "heap-use-after-free",
                        }
                    ],
                },
            )
            self.assertEqual(report["status"], PRODUCTIVE_CORE_SAFETY_DEFECT)

    def test_validator_rejects_stale_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools" / "benchmarks" / "merlo" / "benchmarks").mkdir(parents=True)
            alpha = root / "research/archive/alpha1/benchmarks/merlo_concise_application_alpha.json"
            general = root / "tools/benchmarks/merlo/benchmarks/merlo_general_representation_core.json"
            alpha.write_text("alpha", encoding="utf-8")
            general.write_text("general", encoding="utf-8")
            report = self._build(root)
            alpha.write_text("changed", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_productive_report(report, root=root)

    def test_validator_rejects_missing_section_and_hash_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._build(root)
            missing = dict(report)
            missing.pop("performance")
            with self.assertRaises(ValueError):
                validate_productive_report(missing, root=root)
            tampered = json.loads(json.dumps(report))
            tampered["full_suite"]["passed"] = False
            with self.assertRaises(ValueError):
                validate_productive_report(tampered, root=root)

    def test_write_refuses_different_existing_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = self._build(root)
            destination = root / "report.json"
            write_productive_report_once(report, destination)
            write_productive_report_once(report, destination)
            changed = self._build(
                root,
                full_suite={"passed": True, "status": "PASS", "marker": "different"},
            )
            with self.assertRaises(FileExistsError):
                write_productive_report_once(changed, destination)


if __name__ == "__main__":
    unittest.main()
