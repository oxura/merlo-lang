"""Stage 0.4E benchmark provenance, clustering, and burden accounting."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import random
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .frontend_bench import generate_paired_corpus
from .frontend_semantics import check_frontend
from .stage04e_protocol import assert_stage04e_protocol


BENCHMARK_INTEGRITY_SCHEMA_VERSION = 1
CLUSTER_BOOTSTRAP_SEED = 20260810
CLUSTER_BOOTSTRAP_REPETITIONS = 10_000
_LEGACY_ARTIFACTS = (
    "benchmarks/meldra_external_coverage.json",
    "benchmarks/meldra_external_results.json",
    "benchmarks/meldra_external_validation.json",
    "benchmarks/meldra_core_benchmark.json",
)
_NEW_LANGUAGE_ARTIFACTS = (
    "benchmarks/meldra_stage04_frontend_benchmark.json",
    "benchmarks/meldra_maximal_python.json",
    "benchmarks/meldra_runtime_soundness.json",
    "benchmarks/meldra_interface_locality.json",
    "benchmarks/meldra_effect_context.json",
    "benchmarks/meldra_capability_experiment.json",
)
_TOKEN_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|==|!=|<=|>=|->|::|\|>|[^\s]"
)


@dataclass(frozen=True)
class ClusterInterval:
    metric: str
    arm: str
    estimate: float | None
    lower_95: float | None
    upper_95: float | None
    clusters: int
    numerator: int
    denominator: int
    repetitions: int = CLUSTER_BOOTSTRAP_REPETITIONS
    seed: int = CLUSTER_BOOTSTRAP_SEED
    status: str = "OBSERVED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "arm": self.arm,
            "estimate": self.estimate,
            "lower_95": self.lower_95,
            "upper_95": self.upper_95,
            "clusters": self.clusters,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "repetitions": self.repetitions,
            "seed": self.seed,
            "status": self.status,
        }


@dataclass(frozen=True)
class BenchmarkIntegrityReport:
    legacy_evolution: Mapping[str, Any]
    new_language_semantics: Mapping[str, Any]
    program_groups: tuple[Mapping[str, Any], ...]
    construct_groups: tuple[Mapping[str, Any], ...]
    template_groups: tuple[Mapping[str, Any], ...]
    external_author_groups: tuple[Mapping[str, Any], ...]
    confidence_intervals: tuple[ClusterInterval, ...]
    expressiveness: Mapping[str, Any]
    protocol_sha256: str
    schema_version: int = BENCHMARK_INTEGRITY_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "protocol_sha256": self.protocol_sha256,
            "provenance_partition": {
                "legacy_python_evolution": dict(self.legacy_evolution),
                "new_language_semantics": dict(self.new_language_semantics),
            },
            "grouping": {
                "per_program": [dict(item) for item in self.program_groups],
                "per_construct_family": [
                    dict(item) for item in self.construct_groups
                ],
                "per_template": [dict(item) for item in self.template_groups],
                "per_external_author": [
                    dict(item) for item in self.external_author_groups
                ],
            },
            "clustered_confidence_intervals": [
                item.to_dict() for item in self.confidence_intervals
            ],
            "expressiveness_and_burden": dict(self.expressiveness),
            "decision": "NO_GO_LANGUAGE_ALPHA",
            "limitations": [
                "Generated variants are clustered by originating program/template and are never counted as independent authors.",
                "Legacy Python repository coverage is not evidence about Meldra language semantics.",
                "The 40-program expressiveness measurement is generated inside the frozen support profile and cannot satisfy the external expressiveness gate.",
                "No external author contributed a Meldra implementation in this report.",
            ],
        }


def _load(root: Path, relative: str) -> dict[str, Any]:
    value = json.loads((root / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"benchmark artifact must be an object: {relative}")
    return value


def _artifact_entry(root: Path, relative: str) -> dict[str, Any]:
    raw = (root / relative).read_bytes()
    payload = json.loads(raw)
    return {
        "path": relative,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "schema_version": payload.get("schema_version", payload.get("schema")),
        "decision": payload.get("decision"),
        "evidence_level": payload.get("evidence_level"),
    }


def cluster_bootstrap_interval(
    metric: str,
    arm: str,
    clusters: Mapping[str, tuple[int, int]],
    *,
    repetitions: int = CLUSTER_BOOTSTRAP_REPETITIONS,
    seed: int = CLUSTER_BOOTSTRAP_SEED,
) -> ClusterInterval:
    values = tuple(sorted(clusters.items()))
    numerator = sum(value[0] for _, value in values)
    denominator = sum(value[1] for _, value in values)
    if not values or denominator == 0:
        return ClusterInterval(
            metric,
            arm,
            None,
            None,
            None,
            len(values),
            numerator,
            denominator,
            repetitions,
            seed,
            "NOT_ESTIMABLE",
        )
    rng = random.Random(seed)
    samples = []
    cluster_values = tuple(value for _, value in values)
    for _ in range(repetitions):
        selected = [rng.choice(cluster_values) for _ in cluster_values]
        sample_numerator = sum(item[0] for item in selected)
        sample_denominator = sum(item[1] for item in selected)
        samples.append(sample_numerator / sample_denominator)
    samples.sort()
    lower_index = max(0, math.floor(0.025 * (repetitions - 1)))
    upper_index = min(
        repetitions - 1, math.ceil(0.975 * (repetitions - 1))
    )
    return ClusterInterval(
        metric,
        arm,
        round(numerator / denominator, 6),
        round(samples[lower_index], 6),
        round(samples[upper_index], 6),
        len(values),
        numerator,
        denominator,
        repetitions,
        seed,
    )


def zero_failure_upper_bound(denominator: int, confidence: float = 0.95) -> float:
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between zero and one")
    return round(1.0 - (1.0 - confidence) ** (1.0 / denominator), 6)


def _clusters_from_observations(
    observations: Sequence[Mapping[str, Any]],
    *,
    cluster_part: int,
    success_key: str,
) -> dict[str, tuple[int, int]]:
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for item in observations:
        case_id = str(item["case_id"])
        parts = case_id.split(":")
        cluster = ":".join(parts[: cluster_part + 1])
        totals[cluster][0] += int(bool(item[success_key]))
        totals[cluster][1] += 1
    return {key: (value[0], value[1]) for key, value in totals.items()}


def _runtime_safe_claim_clusters(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[int, int]]:
    result = {}
    for item in observations:
        prediction = item["prediction"]
        classification = prediction["classification"]
        predicted = prediction.get("predicted_target")
        observed = set(item.get("observed_target_set", ()))
        safe = classification != "Exact" or predicted in observed
        count = int(item["observation_count"])
        result[str(item["category"])] = (count if safe else 0, count)
    return result


def _program_and_template_groups(
    runtime: Mapping[str, Any],
    locality: Mapping[str, Any],
    context: Mapping[str, Any],
    capability: Mapping[str, Any],
    external: Mapping[str, Any],
    projects: Mapping[str, Any],
) -> tuple[
    tuple[Mapping[str, Any], ...],
    tuple[Mapping[str, Any], ...],
    tuple[Mapping[str, Any], ...],
]:
    program_groups: list[Mapping[str, Any]] = []
    template_groups: list[Mapping[str, Any]] = []

    for study, payload, per_program in (
        ("interface_locality", locality, 9),
        ("effect_context", context, 6),
        ("capability_safety", capability, 5),
    ):
        programs: dict[str, set[str]] = defaultdict(set)
        for case in payload["cases"]:
            parts = str(case["id"]).split(":")
            program = ":".join(parts[:2])
            programs[program].add(str(case["category"]))
        for program, templates in sorted(programs.items()):
            program_groups.append(
                {
                    "study": study,
                    "program": program,
                    "cases": per_program,
                    "templates": len(templates),
                    "external_author": None,
                    "independent": False,
                }
            )
        for arm_name, arm in sorted(payload["arms"].items()):
            observations_key = (
                "attack_observations"
                if study == "capability_safety"
                else "observations"
            )
            success_key = "detected" if study == "capability_safety" else (
                "verified" if study == "effect_context" else "exact"
            )
            grouped: dict[str, list[int]] = defaultdict(lambda: [0, 0])
            for observation in arm[observations_key]:
                category = str(observation["category"])
                grouped[category][0] += int(bool(observation[success_key]))
                grouped[category][1] += 1
            for category, (successes, denominator) in sorted(grouped.items()):
                template_groups.append(
                    {
                        "study": study,
                        "template": category,
                        "arm": arm_name,
                        "successes": successes,
                        "denominator": denominator,
                        "rate": round(successes / denominator, 6),
                    }
                )

    for fixture in runtime["fixtures"]:
        program_groups.append(
            {
                "study": "runtime_binding",
                "program": fixture["id"],
                "cases": int(
                    runtime["statistical_units"]["runtime_observations"]
                    // runtime["statistical_units"]["generated_callsites"]
                ),
                "templates": 1,
                "external_author": None,
                "independent": False,
            }
        )
    for arm_name, arm in sorted(runtime["arms"].items()):
        for observation in arm["observations"]:
            prediction = observation["prediction"]
            safe = prediction["classification"] != "Exact" or prediction.get(
                "predicted_target"
            ) in set(observation.get("observed_target_set", ()))
            template_groups.append(
                {
                    "study": "runtime_binding",
                    "template": observation["category"],
                    "arm": arm_name,
                    "successes": int(observation["observation_count"]) if safe else 0,
                    "denominator": int(observation["observation_count"]),
                    "rate": 1.0 if safe else 0.0,
                }
            )

    project_metadata = {item["name"]: item for item in projects["projects"]}
    for project in external["projects"]:
        metadata = project_metadata[str(project["project"])]
        owner = metadata["url"].split("github.com/", 1)[1].split("/", 1)[0]
        program_groups.append(
            {
                "study": "legacy_external_python_coverage",
                "program": project["project"],
                "cases": int(project["references"]),
                "templates": 1,
                "external_author": owner,
                "independent": True,
            }
        )
    return (
        tuple(program_groups),
        tuple(template_groups),
        _construct_groups(runtime, locality, context, capability, external),
    )


def _construct_groups(
    runtime: Mapping[str, Any],
    locality: Mapping[str, Any],
    context: Mapping[str, Any],
    capability: Mapping[str, Any],
    external: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    return (
        {
            "construct_family": "python-semantic-evolution",
            "study": "legacy_external_python_coverage",
            "primary_units": int(external["aggregate"]["references"]),
            "independent_programs": int(external["project_count"]),
        },
        {
            "construct_family": "runtime-binding",
            "study": "runtime_binding",
            "primary_units": int(runtime["statistical_units"]["runtime_observations"]),
            "independent_programs": 0,
        },
        {
            "construct_family": "interfaces-and-revisions",
            "study": "interface_locality",
            "primary_units": len(locality["cases"]),
            "independent_programs": 0,
        },
        {
            "construct_family": "typed-effects-and-context",
            "study": "effect_context",
            "primary_units": len(context["cases"]),
            "independent_programs": 0,
        },
        {
            "construct_family": "scoped-capabilities",
            "study": "capability_safety",
            "primary_units": len(capability["cases"]),
            "independent_programs": 0,
        },
    )


def _external_author_groups(
    external: Mapping[str, Any], projects: Mapping[str, Any]
) -> tuple[Mapping[str, Any], ...]:
    metadata = {item["name"]: item for item in projects["projects"]}
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"projects": [], "usable": 0, "references": 0}
    )
    for project in external["projects"]:
        project_name = str(project["project"])
        url = str(metadata[project_name]["url"])
        owner = url.split("github.com/", 1)[1].split("/", 1)[0]
        grouped[owner]["projects"].append(project_name)
        grouped[owner]["usable"] += int(project["usable"]["count"])
        grouped[owner]["references"] += int(project["references"])
    result = []
    for owner, values in sorted(grouped.items()):
        denominator = int(values["references"])
        numerator = int(values["usable"])
        result.append(
            {
                "external_author": owner,
                "projects": sorted(values["projects"]),
                "project_count": len(values["projects"]),
                "legacy_python_usable_references": numerator,
                "legacy_python_references": denominator,
                "legacy_python_usable_rate": round(numerator / denominator, 6),
                "meldra_programs": 0,
                "meldra_claim_status": "UNMEASURED",
            }
        )
    return tuple(result)


def _confidence_intervals(
    runtime: Mapping[str, Any],
    locality: Mapping[str, Any],
    context: Mapping[str, Any],
    capability: Mapping[str, Any],
    external: Mapping[str, Any],
) -> tuple[ClusterInterval, ...]:
    intervals = []
    for arm_name, arm in sorted(locality["arms"].items()):
        intervals.append(
            cluster_bootstrap_interval(
                "locality_exact_rate",
                arm_name,
                _clusters_from_observations(
                    arm["observations"], cluster_part=1, success_key="exact"
                ),
            )
        )
    for arm_name, arm in sorted(context["arms"].items()):
        intervals.append(
            cluster_bootstrap_interval(
                "effect_context_verified_rate",
                arm_name,
                _clusters_from_observations(
                    arm["observations"], cluster_part=1, success_key="verified"
                ),
            )
        )
    for arm_name, arm in sorted(capability["arms"].items()):
        intervals.append(
            cluster_bootstrap_interval(
                "capability_detection_recall",
                arm_name,
                _clusters_from_observations(
                    arm["attack_observations"],
                    cluster_part=1,
                    success_key="detected",
                ),
            )
        )
    for arm_name, arm in sorted(runtime["arms"].items()):
        intervals.append(
            cluster_bootstrap_interval(
                "runtime_non_unsound_static_claim_rate",
                arm_name,
                _runtime_safe_claim_clusters(arm["observations"]),
            )
        )
    external_clusters = {
        str(item["project"]): (
            int(item["usable"]["count"]),
            int(item["references"]),
        )
        for item in external["projects"]
    }
    intervals.append(
        cluster_bootstrap_interval(
            "legacy_python_usable_reference_rate",
            "current-python-sidecar",
            external_clusters,
        )
    )
    return tuple(intervals)


def _python_annotation_sites(source: str) -> int:
    tree = ast.parse(source)
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            count += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            count += node.returns is not None
        elif isinstance(node, ast.AnnAssign):
            count += 1
    return count


def _meldra_annotation_sites(source: str) -> int:
    parameter_or_field = len(
        re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\s*:\s*(?:cap\s+)?[A-Z][A-Za-z0-9_.]*", source)
    )
    returns = len(re.findall(r"->\s*[A-Z][A-Za-z0-9_.]*", source))
    return parameter_or_field + returns


def _burden_sites(source: str, *, language: str) -> int:
    if language == "meldra":
        return len(
            re.findall(r"(?m)^\s*(?:package|module|use|export|uses)\b", source)
        ) + _meldra_annotation_sites(source)
    return len(re.findall(r"(?m)^\s*(?:from|import)\b", source)) + _python_annotation_sites(source)


def measure_expressiveness_and_burden(
    program_count: int = 40,
) -> dict[str, Any]:
    corpus = generate_paired_corpus(program_count)
    meldra_sources = dict(corpus.meldra_sources)
    python_sources = dict(corpus.python_sources)
    programs = []
    for index, fixture in enumerate(corpus.fixture_ids):
        app = f"app{index:02d}"
        meldra_program = {
            path: source
            for path, source in meldra_sources.items()
            if path.startswith(f"{fixture}/") or path.startswith(f"{app}/")
        }
        python_program = {
            path: source
            for path, source in python_sources.items()
            if path.startswith(f"{fixture}/") or path.startswith(f"{app}/")
        }
        frontend = check_frontend(meldra_program)
        python_ok = True
        try:
            for path, source in python_program.items():
                compile(source, path, "exec")
        except SyntaxError:
            python_ok = False
        meldra_bytes = sum(len(item.encode("utf-8")) for item in meldra_program.values())
        python_bytes = sum(len(item.encode("utf-8")) for item in python_program.values())
        meldra_tokens = sum(len(_TOKEN_RE.findall(item)) for item in meldra_program.values())
        python_tokens = sum(len(_TOKEN_RE.findall(item)) for item in python_program.values())
        meldra_annotations = sum(
            _meldra_annotation_sites(item) for item in meldra_program.values()
        )
        python_annotations = sum(
            _python_annotation_sites(item) for item in python_program.values()
        )
        meldra_burden = sum(
            _burden_sites(item, language="meldra")
            for item in meldra_program.values()
        )
        python_burden = sum(
            _burden_sites(item, language="python")
            for item in python_program.values()
        )
        programs.append(
            {
                "program": fixture,
                "fully_expressible": frontend.ok and python_ok,
                "meldra_bytes": meldra_bytes,
                "python_bytes": python_bytes,
                "meldra_tokens": meldra_tokens,
                "python_tokens": python_tokens,
                "meldra_annotation_sites": meldra_annotations,
                "python_annotation_sites": python_annotations,
                "meldra_declaration_burden_sites": meldra_burden,
                "python_declaration_burden_sites": python_burden,
                "foreign_escape_sites": 0,
            }
        )
    expressible = sum(bool(item["fully_expressible"]) for item in programs)
    meldra_bytes = sum(int(item["meldra_bytes"]) for item in programs)
    python_bytes = sum(int(item["python_bytes"]) for item in programs)
    meldra_tokens = sum(int(item["meldra_tokens"]) for item in programs)
    python_tokens = sum(int(item["python_tokens"]) for item in programs)
    return {
        "evidence_level": "GENERATED_SUPPORT_PROFILE_PILOT",
        "programs": len(programs),
        "fully_expressible_programs": expressible,
        "fully_expressible_program_rate": round(expressible / len(programs), 6),
        "foreign_escape_programs": 0,
        "foreign_escape_frequency": 0.0,
        "meldra_source_bytes": meldra_bytes,
        "python_source_bytes": python_bytes,
        "meldra_source_overhead_ratio": round(meldra_bytes / python_bytes - 1.0, 6),
        "meldra_tokens": meldra_tokens,
        "python_tokens": python_tokens,
        "meldra_token_overhead_ratio": round(meldra_tokens / python_tokens - 1.0, 6),
        "meldra_annotation_sites": sum(
            int(item["meldra_annotation_sites"]) for item in programs
        ),
        "python_annotation_sites": sum(
            int(item["python_annotation_sites"]) for item in programs
        ),
        "meldra_declaration_burden_sites": sum(
            int(item["meldra_declaration_burden_sites"]) for item in programs
        ),
        "python_declaration_burden_sites": sum(
            int(item["python_declaration_burden_sites"]) for item in programs
        ),
        "median_meldra_source_overhead_ratio": round(
            statistics.median(
                int(item["meldra_bytes"]) / int(item["python_bytes"]) - 1.0
                for item in programs
            ),
            6,
        ),
        "burden_definition": (
            "Annotation sites plus explicit package/module/use/export/uses lines "
            "for Meldra; annotation sites plus import/from lines for Python."
        ),
        "strict_python_sidecar_manifest_burden": "UNMEASURED_ON_THIS_CORPUS",
        "primary_external_gate_status": "UNMEASURED",
        "per_program": programs,
    }


def run_benchmark_integrity_report(root: str | Path = ".") -> BenchmarkIntegrityReport:
    root_path = Path(root)
    protocol = assert_stage04e_protocol(root_path)
    runtime = _load(root_path, "benchmarks/meldra_runtime_soundness.json")
    locality = _load(root_path, "benchmarks/meldra_interface_locality.json")
    context = _load(root_path, "benchmarks/meldra_effect_context.json")
    capability = _load(root_path, "benchmarks/meldra_capability_experiment.json")
    external = _load(root_path, "benchmarks/meldra_external_coverage.json")
    projects = _load(root_path, "benchmarks/meldra_external_projects.json")
    program_groups, template_groups, construct_groups = _program_and_template_groups(
        runtime, locality, context, capability, external, projects
    )
    legacy = {
        "scope": "Python semantic-evolution sidecar and repository coverage",
        "artifacts": [_artifact_entry(root_path, item) for item in _LEGACY_ARTIFACTS],
        "external_projects": int(external["project_count"]),
        "external_references": int(external["aggregate"]["references"]),
        "language_semantics_claim": "PROHIBITED",
    }
    new = {
        "scope": "Frozen Stage 0.4 frontend and Stage 0.4E differential studies",
        "artifacts": [
            _artifact_entry(root_path, item) for item in _NEW_LANGUAGE_ARTIFACTS
        ],
        "generated_program_templates": 1,
        "external_meldra_programs": 0,
        "external_author_count": 0,
        "primary_external_gate_status": "UNMEASURED",
    }
    return BenchmarkIntegrityReport(
        legacy,
        new,
        program_groups,
        construct_groups,
        template_groups,
        _external_author_groups(external, projects),
        _confidence_intervals(runtime, locality, context, capability, external),
        measure_expressiveness_and_burden(40),
        protocol.protocol_sha256,
    )


__all__ = [
    "BENCHMARK_INTEGRITY_SCHEMA_VERSION",
    "CLUSTER_BOOTSTRAP_REPETITIONS",
    "CLUSTER_BOOTSTRAP_SEED",
    "BenchmarkIntegrityReport",
    "ClusterInterval",
    "cluster_bootstrap_interval",
    "measure_expressiveness_and_burden",
    "run_benchmark_integrity_report",
    "zero_failure_upper_bound",
]
