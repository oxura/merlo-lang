"""External-spec Stage 0.4E paired corpus and frozen acceptance harness."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from research.archive.historical_protocol.merlo.legacy_evidence import frozen_sha256
from research.archive.alpha1.merlo.frontend_bench import NegativeCase, generate_negative_cases
from research.archive.historical_protocol.merlo.frontend_evaluator import ReferenceEvaluator
from research.archive.historical_protocol.merlo.frontend_semantics import check_frontend
from research.archive.alpha1.merlo.maximal_python import (
    MaximalPythonManifest,
    MaximalPythonPackageManifest,
    analyze_maximal_python,
)
from research.archive.historical_protocol.merlo.stage04e_freeze import assert_stage04_frozen
from research.archive.historical_protocol.merlo.stage04e_protocol import assert_stage04e_protocol


INDEPENDENT_CORPUS_SCHEMA_VERSION = 1
INDEPENDENT_CORPUS_FILENAME = "meldra_independent_mbpp_subset.json"
INDEPENDENT_CORPUS_LOCK_FILENAME = "meldra_independent_corpus_lock.json"
BEHAVIOR_MUTATIONS_PER_PROGRAM = 5
ADVERSARIAL_NEGATIVE_COUNT = 300


@dataclass(frozen=True)
class AcceptanceCase:
    arguments: tuple[int, ...]
    expected: int | bool

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AcceptanceCase":
        arguments = tuple(value.get("arguments", ()))
        if any(isinstance(item, bool) or not isinstance(item, int) for item in arguments):
            raise ValueError("independent corpus arguments must be Int values")
        expected = value.get("expected")
        if not isinstance(expected, (int, bool)):
            raise ValueError("independent corpus expected value must be Int or Bool")
        return cls(arguments, expected)

    def to_dict(self) -> dict[str, Any]:
        return {"arguments": list(self.arguments), "expected": self.expected}


@dataclass(frozen=True)
class IndependentProgram:
    program_id: str
    task_id: int
    domain_adapter: str
    source_file: str
    prompt: str
    entry_point: str
    parameters: tuple[str, ...]
    return_type: str
    acceptance_cases: tuple[AcceptanceCase, ...]
    python_reference_source: str
    meldra_declarations: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IndependentProgram":
        result = cls(
            str(value["program_id"]),
            int(value["task_id"]),
            str(value["domain_adapter"]),
            str(value["source_file"]),
            str(value["prompt"]),
            str(value["entry_point"]),
            tuple(str(item) for item in value["parameters"]),
            str(value["return_type"]),
            tuple(
                AcceptanceCase.from_dict(item)
                for item in value["acceptance_cases"]
            ),
            str(value["python_reference_source"]),
            str(value["meldra_declarations"]),
        )
        if result.return_type not in {"Int", "Bool"}:
            raise ValueError(f"unsupported return type: {result.return_type}")
        if len(result.acceptance_cases) < 3:
            raise ValueError("every external program requires at least three tests")
        return result

    @property
    def package(self) -> str:
        return self.program_id.replace("-", "")

    @property
    def locator(self) -> str:
        return f"{self.package}.main.{self.entry_point}"

    def meldra_source(self) -> str:
        return (
            f"package {self.package}\nmodule main\n"
            f"export {self.entry_point}\n"
            f"{self.meldra_declarations}"
        )

    def to_dict(self, *, include_sources: bool = False) -> dict[str, Any]:
        result = {
            "program_id": self.program_id,
            "task_id": self.task_id,
            "domain_adapter": self.domain_adapter,
            "source_file": self.source_file,
            "prompt": self.prompt,
            "entry_point": self.entry_point,
            "parameters": list(self.parameters),
            "return_type": self.return_type,
            "acceptance_cases": [
                item.to_dict() for item in self.acceptance_cases
            ],
            "python_reference_sha256": hashlib.sha256(
                self.python_reference_source.encode("utf-8")
            ).hexdigest(),
            "meldra_source_sha256": hashlib.sha256(
                self.meldra_source().encode("utf-8")
            ).hexdigest(),
        }
        if include_sources:
            result["python_reference_source"] = self.python_reference_source
            result["meldra_source"] = self.meldra_source()
        return result


@dataclass(frozen=True)
class BehaviorChangeResult:
    change_id: str
    program_id: str
    mutation: str
    python_acceptance_rejected: bool
    meldra_acceptance_rejected: bool
    meldra_interface_preserved: bool
    meldra_implementation_changed: bool
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "program_id": self.program_id,
            "mutation": self.mutation,
            "python_acceptance_rejected": self.python_acceptance_rejected,
            "meldra_acceptance_rejected": self.meldra_acceptance_rejected,
            "meldra_interface_preserved": self.meldra_interface_preserved,
            "meldra_implementation_changed": self.meldra_implementation_changed,
            "status": self.status,
        }


@dataclass(frozen=True)
class NegativeResult:
    case_id: str
    expected_code: str
    observed_codes: tuple[str, ...]
    detected: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "expected_code": self.expected_code,
            "observed_codes": list(self.observed_codes),
            "detected": self.detected,
        }


@dataclass(frozen=True)
class IndependentCorpusReport:
    programs: tuple[IndependentProgram, ...]
    behavior_changes: tuple[BehaviorChangeResult, ...]
    negatives: tuple[NegativeResult, ...]
    current_python_program_passes: int
    current_python_assertion_passes: int
    maximal_python_admitted_programs: int
    meldra_program_passes: int
    meldra_assertion_passes: int
    strict_diagnostic_counts: tuple[tuple[str, int], ...]
    corpus_sha256: str
    acceptance_sha256: str
    protocol_sha256: str
    lock_sha256: str
    schema_version: int = INDEPENDENT_CORPUS_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        assertion_count = sum(len(item.acceptance_cases) for item in self.programs)
        domains = sorted({item.domain_adapter for item in self.programs})
        source_groups = sorted({item.source_file for item in self.programs})
        changes_passed = sum(
            item.status == "DETECTED_BY_BOTH" for item in self.behavior_changes
        )
        negatives_passed = sum(item.detected for item in self.negatives)
        return {
            "schema_version": self.schema_version,
            "source": {
                "dataset": "Mostly Basic Python Problems sanitized subset",
                "external_specs_and_tests": True,
                "python_references_from_external_dataset": True,
                "meldra_implementations_external": False,
                "human_adjudicated_meldra_translations": False,
                "source_groups": source_groups,
                "source_group_count": len(source_groups),
            },
            "freeze": {
                "corpus_sha256": self.corpus_sha256,
                "acceptance_sha256": self.acceptance_sha256,
                "protocol_sha256": self.protocol_sha256,
                "lock_sha256": self.lock_sha256,
                "locked_before_frontend_hardening": True,
            },
            "statistical_units": {
                "paired_programs": len(self.programs),
                "domains": len(domains),
                "acceptance_assertions": assertion_count,
                "behavior_changes": len(self.behavior_changes),
                "adversarial_negatives": len(self.negatives),
                "external_spec_source_groups": len(source_groups),
                "independent_meldra_implementation_authors": 0,
            },
            "domains": domains,
            "programs": [item.to_dict() for item in self.programs],
            "baseline": {
                "current-python-sidecar": {
                    "program_passes": self.current_python_program_passes,
                    "programs": len(self.programs),
                    "assertion_passes": self.current_python_assertion_passes,
                    "assertions": assertion_count,
                    "role": "external executable reference; sidecar not a runtime sandbox",
                },
                "maximal-python-profile": {
                    "admitted_programs": self.maximal_python_admitted_programs,
                    "programs": len(self.programs),
                    "diagnostic_counts": dict(self.strict_diagnostic_counts),
                    "role": "unmodified external Python references tested for strict-profile admission",
                },
                "meldra-closed": {
                    "program_passes": self.meldra_program_passes,
                    "programs": len(self.programs),
                    "assertion_passes": self.meldra_assertion_passes,
                    "assertions": assertion_count,
                    "role": "repository-authored translation checked against external tests",
                },
            },
            "behavior_changes": {
                "passed": changes_passed,
                "denominator": len(self.behavior_changes),
                "results": [item.to_dict() for item in self.behavior_changes],
            },
            "adversarial_negatives": {
                "passed": negatives_passed,
                "denominator": len(self.negatives),
                "results": [item.to_dict() for item in self.negatives],
            },
            "evidence_level": "EXTERNAL_SPECS_TESTS_INTERNAL_MELDRA_TRANSLATIONS",
            "primary_external_gate_status": "PARTIAL",
            "decision": "NO_GO_LANGUAGE_ALPHA",
            "limitations": [
                "MBPP prompts, Python references, and acceptance tests are external and hand-verified by the source dataset; Meldra translations are not independently authored.",
                "The ten application-domain labels are deterministic adapters, not labels supplied by MBPP authors.",
                "The 200 behavior changes and 300 negatives are generated mutation/template checks, not independent author samples.",
                "Acceptance tests are digest-locked against compiler changes but are repository-visible, not secret held-out tests.",
            ],
        }


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def _paths(root: Path) -> tuple[Path, Path]:
    return (
        root / "tools" / "benchmarks" / "merlo" / "benchmarks" / INDEPENDENT_CORPUS_FILENAME,
        root / "tools" / "benchmarks" / "merlo" / "benchmarks" / INDEPENDENT_CORPUS_LOCK_FILENAME,
    )


def load_independent_programs(root: str | Path = ".") -> tuple[IndependentProgram, ...]:
    root_path = Path(root)
    corpus_path, _ = _paths(root_path)
    payload = _load_object(corpus_path)
    programs = tuple(
        IndependentProgram.from_dict(item) for item in payload.get("records", ())
    )
    if len(programs) != 40:
        raise ValueError(f"independent corpus requires 40 programs, got {len(programs)}")
    if len({item.program_id for item in programs}) != len(programs):
        raise ValueError("independent corpus program IDs must be unique")
    return programs


def acceptance_digest(programs: tuple[IndependentProgram, ...]) -> str:
    return _sha256_bytes(
        _canonical(
            [
                {
                    "program_id": item.program_id,
                    "prompt": item.prompt,
                    "entry_point": item.entry_point,
                    "parameters": item.parameters,
                    "return_type": item.return_type,
                    "acceptance_cases": [
                        case.to_dict() for case in item.acceptance_cases
                    ],
                }
                for item in programs
            ]
        ).encode("utf-8")
    )


def verify_independent_corpus_lock(root: str | Path = ".") -> dict[str, Any]:
    root_path = Path(root)
    corpus_path, lock_path = _paths(root_path)
    lock = _load_object(lock_path)
    programs = load_independent_programs(root_path)
    observed = {
        "corpus_sha256": _sha256_bytes(corpus_path.read_bytes()),
        "acceptance_sha256": acceptance_digest(programs),
        "harness_sha256": frozen_sha256(
            root_path, "meldra/independent_corpus.py"
        ),
    }
    mismatches = {
        key: {"expected": lock.get(key), "observed": value}
        for key, value in observed.items()
        if lock.get(key) != value
    }
    assert_stage04_frozen(root_path)
    protocol = assert_stage04e_protocol(root_path)
    if lock.get("protocol_sha256") != protocol.protocol_sha256:
        mismatches["protocol_sha256"] = {
            "expected": lock.get("protocol_sha256"),
            "observed": protocol.protocol_sha256,
        }
    if mismatches:
        raise RuntimeError(
            "independent corpus lock verification failed: " + _canonical(mismatches)
        )
    return lock


def _python_results(program: IndependentProgram, source: str) -> tuple[Any, ...]:
    namespace: dict[str, Any] = {}
    exec(compile(source, f"{program.program_id}.py", "exec"), namespace)
    function = namespace[program.entry_point]
    return tuple(function(*case.arguments) for case in program.acceptance_cases)


def _meldra_compilation(program: IndependentProgram, source: str):
    path = f"{program.package}/main.meldra"
    result = check_frontend({path: source})
    if not result.ok or result.compilation is None:
        codes = sorted({item.code for item in result.diagnostics})
        raise RuntimeError(f"{program.program_id} failed Meldra frontend: {codes}")
    return result.compilation


def _meldra_results(program: IndependentProgram, source: str) -> tuple[Any, ...]:
    compilation = _meldra_compilation(program, source)
    evaluator = ReferenceEvaluator(compilation)
    return tuple(
        evaluator.evaluate(program.locator, case.arguments).value
        for case in program.acceptance_cases
    )


class _RenameEntry(ast.NodeTransformer):
    def __init__(self, old: str, new: str) -> None:
        self.old = old
        self.new = new

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        if node.name == self.old:
            node.name = self.new
        return self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id == self.old:
            node.id = self.new
        return node


def _python_mutation_source(program: IndependentProgram, mutation: int) -> str:
    reference_name = f"_reference_{program.entry_point}"
    tree = _RenameEntry(program.entry_point, reference_name).visit(
        ast.parse(program.python_reference_source)
    )
    ast.fix_missing_locations(tree)
    reference = ast.unparse(tree).rstrip() + "\n\n"
    parameters = ", ".join(program.parameters)
    arguments = ", ".join(program.parameters)
    first = program.parameters[0]
    last = program.parameters[-1]
    if program.return_type == "Int":
        expressions = ("result + 1", "result - 1", "result * 2", "0", last)
        body = (
            f"    result = {reference_name}({arguments})\n"
            f"    return {expressions[mutation]}\n"
        )
    else:
        bodies = (
            "    result = {call}\n    return not result\n",
            "    return True\n",
            "    return False\n",
            f"    return {first} == 0\n",
            f"    return {first} > 0\n",
        )
        body = bodies[mutation].format(call=f"{reference_name}({arguments})")
    return reference + f"def {program.entry_point}({parameters}):\n" + body


def _meldra_mutation_source(program: IndependentProgram, mutation: int) -> str:
    reference_name = f"_reference_{program.entry_point}"
    renamed = re.sub(
        rf"\b{re.escape(program.entry_point)}\b",
        reference_name,
        program.meldra_declarations,
    )
    parameters = ", ".join(f"{name}: Int" for name in program.parameters)
    arguments = ", ".join(program.parameters)
    first = program.parameters[0]
    last = program.parameters[-1]
    if program.return_type == "Int":
        expressions = ("result + 1", "result - 1", "result * 2", "0", last)
        body = (
            f"    let result = {reference_name}({arguments})\n"
            f"    {expressions[mutation]}\n"
        )
    else:
        bodies = (
            f"    let result = {reference_name}({arguments})\n"
            "    if result:\n        0 == 1\n    else:\n        0 == 0\n",
            "    0 == 0\n",
            "    0 == 1\n",
            f"    {first} == 0\n",
            f"    {first} > 0\n",
        )
        body = bodies[mutation]
    return (
        f"package {program.package}\nmodule main\n"
        f"export {program.entry_point}\n"
        + renamed
        + f"fn {program.entry_point}({parameters}) -> {program.return_type}:\n"
        + body
    )


def _package_revision(
    compilation: Any, package_name: str
) -> tuple[str, str]:
    for package, interface, implementation in compilation.hir.package_revisions:
        if package == package_name:
            return interface, implementation
    raise KeyError(f"unknown package revision: {package_name}")


def run_behavior_changes(
    programs: tuple[IndependentProgram, ...]
) -> tuple[BehaviorChangeResult, ...]:
    results = []
    for program in programs:
        baseline = _meldra_compilation(program, program.meldra_source())
        baseline_interface, baseline_implementation = _package_revision(
            baseline, program.package
        )
        expected = tuple(case.expected for case in program.acceptance_cases)
        for mutation in range(BEHAVIOR_MUTATIONS_PER_PROGRAM):
            python_source = _python_mutation_source(program, mutation)
            meldra_source = _meldra_mutation_source(program, mutation)
            python_rejected = _python_results(program, python_source) != expected
            meldra_values = _meldra_results(program, meldra_source)
            meldra_rejected = meldra_values != expected
            changed = _meldra_compilation(program, meldra_source)
            changed_interface, changed_implementation = _package_revision(
                changed, program.package
            )
            interface_preserved = baseline_interface == changed_interface
            implementation_changed = (
                baseline_implementation != changed_implementation
            )
            passed = (
                python_rejected
                and meldra_rejected
                and interface_preserved
                and implementation_changed
            )
            results.append(
                BehaviorChangeResult(
                    f"{program.program_id}:mutation-{mutation}",
                    program.program_id,
                    f"output_mutation_{mutation}",
                    python_rejected,
                    meldra_rejected,
                    interface_preserved,
                    implementation_changed,
                    "DETECTED_BY_BOTH" if passed else "FAILED_ORACLE",
                )
            )
    return tuple(results)


def _duplicate_negative_cases() -> tuple[NegativeCase, ...]:
    cases = []
    for index in range(30):
        package = f"duplicate{index:02d}"
        source = (
            f"package {package}\nmodule main\nexport bad\n"
            "fn bad(value: Int) -> Int:\n    value\n"
            "fn bad(value: Int) -> Int:\n    value + 1\n"
        )
        cases.append(
            NegativeCase(
                f"{package}:09:DuplicateDeclaration",
                "DuplicateDeclaration",
                ((f"{package}/main.meldra", source),),
            )
        )
    return tuple(cases)


def run_adversarial_negatives() -> tuple[NegativeResult, ...]:
    cases = (*generate_negative_cases(30), *_duplicate_negative_cases())
    if len(cases) != ADVERSARIAL_NEGATIVE_COUNT:
        raise RuntimeError(f"expected 300 negatives, got {len(cases)}")
    results = []
    for case in cases:
        checked = check_frontend(dict(case.sources))
        observed = tuple(sorted({item.code for item in checked.diagnostics}))
        results.append(
            NegativeResult(
                case.id,
                case.expected_code,
                observed,
                not checked.ok and case.expected_code in observed,
            )
        )
    return tuple(results)


def _strict_admission(program: IndependentProgram) -> tuple[bool, tuple[str, ...]]:
    path = f"{program.package}/main.py"
    manifest = MaximalPythonManifest(
        (
            MaximalPythonPackageManifest(
                program.package,
                program.package,
                (program.locator,),
                allowed_ambient_imports=("math",),
            ),
        )
    )
    report = analyze_maximal_python({path: program.python_reference_source}, manifest)
    return report.ok, tuple(
        sorted({item.code for item in report.blocking_diagnostics})
    )


def run_independent_corpus(root: str | Path = ".") -> IndependentCorpusReport:
    root_path = Path(root)
    lock = verify_independent_corpus_lock(root_path)
    protocol = assert_stage04e_protocol(root_path)
    programs = load_independent_programs(root_path)
    python_program_passes = 0
    python_assertion_passes = 0
    meldra_program_passes = 0
    meldra_assertion_passes = 0
    strict_admitted = 0
    strict_diagnostics: dict[str, int] = {}
    for program in programs:
        expected = tuple(case.expected for case in program.acceptance_cases)
        python_values = _python_results(program, program.python_reference_source)
        meldra_values = _meldra_results(program, program.meldra_source())
        python_matches = tuple(
            actual == target for actual, target in zip(python_values, expected)
        )
        meldra_matches = tuple(
            actual == target for actual, target in zip(meldra_values, expected)
        )
        python_assertion_passes += sum(python_matches)
        meldra_assertion_passes += sum(meldra_matches)
        python_program_passes += all(python_matches)
        meldra_program_passes += all(meldra_matches)
        admitted, diagnostics = _strict_admission(program)
        strict_admitted += admitted
        for diagnostic in diagnostics:
            strict_diagnostics[diagnostic] = strict_diagnostics.get(diagnostic, 0) + 1
    corpus_path, lock_path = _paths(root_path)
    return IndependentCorpusReport(
        programs,
        run_behavior_changes(programs),
        run_adversarial_negatives(),
        python_program_passes,
        python_assertion_passes,
        strict_admitted,
        meldra_program_passes,
        meldra_assertion_passes,
        tuple(sorted(strict_diagnostics.items())),
        _sha256_bytes(corpus_path.read_bytes()),
        acceptance_digest(programs),
        protocol.protocol_sha256,
        _sha256_bytes(lock_path.read_bytes()),
    )


__all__ = [
    "ADVERSARIAL_NEGATIVE_COUNT",
    "BEHAVIOR_MUTATIONS_PER_PROGRAM",
    "INDEPENDENT_CORPUS_FILENAME",
    "INDEPENDENT_CORPUS_LOCK_FILENAME",
    "INDEPENDENT_CORPUS_SCHEMA_VERSION",
    "AcceptanceCase",
    "BehaviorChangeResult",
    "IndependentCorpusReport",
    "IndependentProgram",
    "NegativeResult",
    "acceptance_digest",
    "load_independent_programs",
    "run_adversarial_negatives",
    "run_behavior_changes",
    "run_independent_corpus",
    "verify_independent_corpus_lock",
]
