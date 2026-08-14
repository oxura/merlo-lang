"""Adversarial Stage 0.4 corpus and equal-denominator frontend benchmark."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from research.archive.historical_protocol.merlo.analyzer import scan_python
from research.archive.historical_protocol.merlo.core_semantics import CoreChange, CoreError, apply_core_change
from research.archive.historical_protocol.merlo.frontend_evaluator import (
    EnumValue,
    RecordValue,
    ReferenceEvaluator,
)
from research.archive.historical_protocol.merlo.frontend_semantics import FrontendCompilation, check_frontend, compile_frontend
from research.archive.historical_protocol.merlo.python_binder import PythonBindingReport, bind_python_sources


FRONTEND_BENCHMARK_SCHEMA_VERSION = 1
DEFAULT_PROGRAM_COUNT = 40
SUPPORTED = "SUPPORTED"
NOT_SUPPORTED = "NOT_SUPPORTED"
UNMEASURED = "UNMEASURED"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LogicalReference:
    id: str
    fixture_id: str
    kind: str
    python_path: str
    python_line: int
    python_column: int
    python_spelling: str
    python_usage: str
    python_target: str
    meldra_path: str
    meldra_line: int
    meldra_column: int
    meldra_spelling: str
    meldra_usage: str
    meldra_target: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fixture_id": self.fixture_id,
            "kind": self.kind,
            "python": {
                "path": self.python_path,
                "line": self.python_line,
                "column": self.python_column,
                "spelling": self.python_spelling,
                "usage": self.python_usage,
                "target": self.python_target,
            },
            "meldra": {
                "path": self.meldra_path,
                "line": self.meldra_line,
                "column": self.meldra_column,
                "spelling": self.meldra_spelling,
                "usage": self.meldra_usage,
                "target": self.meldra_target,
            },
        }


@dataclass(frozen=True)
class PairedCorpus:
    fixture_ids: tuple[str, ...]
    meldra_sources: tuple[tuple[str, str], ...]
    python_sources: tuple[tuple[str, str], ...]
    references: tuple[LogicalReference, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fixture_ids", tuple(sorted(self.fixture_ids)))
        object.__setattr__(self, "meldra_sources", tuple(sorted(self.meldra_sources)))
        object.__setattr__(self, "python_sources", tuple(sorted(self.python_sources)))
        object.__setattr__(self, "references", tuple(sorted(self.references, key=lambda item: item.id)))

    @property
    def program_count(self) -> int:
        return len(self.fixture_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "program_count": self.program_count,
            "fixture_ids": list(self.fixture_ids),
            "meldra_sources": [
                {
                    "path": path,
                    "bytes": len(source.encode("utf-8")),
                    "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                }
                for path, source in self.meldra_sources
            ],
            "python_sources": [
                {
                    "path": path,
                    "bytes": len(source.encode("utf-8")),
                    "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                }
                for path, source in self.python_sources
            ],
            "logical_references": [item.to_dict() for item in self.references],
        }


@dataclass(frozen=True)
class NegativeCase:
    id: str
    expected_code: str
    sources: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class BindingArmMeasurement:
    arm: str
    exact: int
    wrong_target: int
    uncertain: int
    missing: int
    denominator: int

    @property
    def exact_ratio(self) -> float:
        return self.exact / self.denominator if self.denominator else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "exact": self.exact,
            "wrong_target": self.wrong_target,
            "uncertain": self.uncertain,
            "missing": self.missing,
            "denominator": self.denominator,
            "exact_ratio": round(self.exact_ratio, 6),
        }


@dataclass(frozen=True)
class GateMeasurement:
    gate: str
    status: str
    numerator: int | None
    denominator: int | None
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "status": self.status,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class FrontendBenchmarkReport:
    corpus: PairedCorpus
    current_python: BindingArmMeasurement
    strong_python: BindingArmMeasurement
    meldra: BindingArmMeasurement
    parser_roundtrips: int
    parser_denominator: int
    negative_expected: int
    negative_denominator: int
    negative_breakdown: tuple[tuple[str, int, int], ...]
    semantic_changes_applied: int
    semantic_change_denominator: int
    semantic_change_breakdown: tuple[tuple[str, int, int], ...]
    identity_continuity: int
    external_edits_new_identity: int
    interface_private_passes: int
    interface_public_passes: int
    capability_escalations_blocked: int
    target_collisions_blocked: int
    execution_passes: int
    execution_denominator: int
    deterministic_lowering_passes: int
    deterministic_lowering_denominator: int
    support_profile_sha256: str
    gates: tuple[GateMeasurement, ...]
    decision: str
    authorized_next_stage: str
    schema_version: int = FRONTEND_BENCHMARK_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "corpus": self.corpus.to_dict(),
            "binding": {
                "shared_logical_denominator": len(self.corpus.references),
                "arms": {
                    self.current_python.arm: self.current_python.to_dict(),
                    self.strong_python.arm: self.strong_python.to_dict(),
                    self.meldra.arm: self.meldra.to_dict(),
                },
            },
            "parser": {
                "roundtrips": self.parser_roundtrips,
                "denominator": self.parser_denominator,
            },
            "negative_cases": {
                "expected_diagnostic": self.negative_expected,
                "denominator": self.negative_denominator,
                "breakdown": {
                    name: {"numerator": numerator, "denominator": denominator}
                    for name, numerator, denominator in self.negative_breakdown
                },
            },
            "semantic_changes": {
                "applied": self.semantic_changes_applied,
                "denominator": self.semantic_change_denominator,
                "identity_continuity": self.identity_continuity,
                "external_text_edits_new_identity": self.external_edits_new_identity,
                "breakdown": {
                    name: {"numerator": numerator, "denominator": denominator}
                    for name, numerator, denominator in self.semantic_change_breakdown
                },
                "target_collisions_blocked": self.target_collisions_blocked,
            },
            "interfaces": {
                "private_edit_passes": self.interface_private_passes,
                "public_change_passes": self.interface_public_passes,
                "denominator_each": self.corpus.program_count,
            },
            "capabilities": {
                "escalations_blocked": self.capability_escalations_blocked,
                "denominator": self.corpus.program_count,
            },
            "execution": {
                "passes": self.execution_passes,
                "denominator": self.execution_denominator,
            },
            "deterministic_lowering": {
                "passes": self.deterministic_lowering_passes,
                "denominator": self.deterministic_lowering_denominator,
            },
            "support_profile": {
                "id": "meldra-stage04-python-binding-p0",
                "sha256": self.support_profile_sha256,
            },
            "gates": [item.to_dict() for item in self.gates],
            "decision": self.decision,
            "authorized_next_stage": self.authorized_next_stage,
            "limitations": [
                "The paired corpus is generated and is not independent human ground truth.",
                "The strong Python arm is a repository-local structural type-aware binder, not Pyright, CodeQL, mypy, or an LSP.",
                "Passing the kernel corpus does not establish ergonomics, performance, external task success, or Language Alpha value.",
                "The independent external 30x3 review queue remains incomplete.",
            ],
        }

    def to_json(self) -> str:
        return _canonical(self.to_dict())


class _Builder:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, line: str = "") -> int:
        self.lines.append(line)
        return len(self.lines)

    def column(self, line: int, spelling: str, occurrence: int = 0) -> int:
        text = self.lines[line - 1]
        start = -1
        offset = 0
        for _ in range(occurrence + 1):
            start = text.index(spelling, offset)
            offset = start + len(spelling)
        return start

    def source(self) -> str:
        return "\n".join(self.lines) + "\n"


def generate_paired_corpus(
    program_count: int = DEFAULT_PROGRAM_COUNT,
) -> PairedCorpus:
    if not 30 <= program_count <= 50:
        raise ValueError("Stage 0.4 paired corpus requires 30-50 programs")
    meldra_sources: dict[str, str] = {}
    python_sources: dict[str, str] = {}
    references: list[LogicalReference] = []
    fixture_ids: list[str] = []

    for index in range(program_count):
        fixture = f"case{index:02d}"
        app = f"app{index:02d}"
        fixture_ids.append(fixture)
        m_model_path = f"{fixture}/model.meldra"
        m_service_path = f"{fixture}/service.meldra"
        m_moved_path = f"{fixture}/moved.meldra"
        m_app_path = f"{app}/main.meldra"
        p_model_path = f"{fixture}/model.py"
        p_service_path = f"{fixture}/service.py"
        p_app_path = f"{app}/main.py"

        mm = _Builder()
        pm = _Builder()
        mm.add(f"package {fixture}.model")
        mm.add(
            "export User, Status, normalize, Clock, timestamp, cached_time"
        )
        mm.add("record User:")
        mm.add("    name: Text")
        mm.add("    score: Int")
        mm.add("    status: Status")
        mm.add("enum Status:")
        mm.add("    Active")
        mm.add("    Disabled")
        mm.add("capability Clock:")
        mm.add("    now() -> Int uses clock.now")
        mm.add("fn normalize(value: Text) -> Text:")
        mm.add("    value")
        mm.add("fn constant() -> Int:")
        mm.add(f"    {index}")
        m_timestamp_header = mm.add("task timestamp(clock: cap Clock) -> Int:")
        mm.add("    uses clock.now")
        m_timestamp_call = mm.add("    clock.now()")
        m_cached_header = mm.add("task cached_time(clock: cap Clock) -> Int:")
        mm.add("    uses clock.now")
        mm.add("    0")

        pm.add("class User:")
        pm.add("    name: str")
        pm.add("    score: int")
        pm.add("    status: Status")
        pm.add("")
        pm.add("class Status:")
        pm.add('    Active = "active"')
        pm.add('    Disabled = "disabled"')
        pm.add("")
        pm.add("class Clock:")
        pm.add("    def now(self) -> int:")
        pm.add("        return 0")
        pm.add("")
        pm.add("def normalize(value: str) -> str:")
        pm.add("    return value")
        pm.add("")
        pm.add("def constant() -> int:")
        pm.add(f"    return {index}")
        pm.add("")
        p_timestamp_header = pm.add("def timestamp(clock: Clock) -> int:")
        p_timestamp_call = pm.add("    return clock.now()")
        pm.add("")
        p_cached_header = pm.add("def cached_time(clock: Clock) -> int:")
        pm.add("    return 0")

        ms = _Builder()
        ps = _Builder()
        ms.add(f"package {fixture}.service")
        ms.add(
            f"use {fixture}.model::{{User, Status, normalize as clean}}"
        )
        export_names = [
            *(f"render{number}" for number in range(8)),
            *(f"score{number}" for number in range(4)),
            "active",
            "choose",
            "shadow",
            "aggregate",
        ]
        ms.add("export " + ", ".join(export_names))
        ms.add("fn helper(value: Text) -> Text:")
        ms.add("    value")

        ps.add(f"from {fixture}.model import User, Status, normalize as clean")
        ps.add("")
        ps.add("def helper(value: str) -> str:")
        ps.add("    return value")

        link_counter = 0

        def add_link(
            kind: str,
            python_path: str,
            python_builder: _Builder,
            python_line: int,
            python_spelling: str,
            python_usage: str,
            python_target: str,
            meldra_path: str,
            meldra_builder: _Builder,
            meldra_line: int,
            meldra_spelling: str,
            meldra_usage: str,
            meldra_target: str,
            *,
            python_occurrence: int = 0,
            meldra_occurrence: int = 0,
        ) -> None:
            nonlocal link_counter
            link_id = f"{fixture}:ref:{link_counter:03d}"
            link_counter += 1
            references.append(
                LogicalReference(
                    link_id,
                    fixture,
                    kind,
                    python_path,
                    python_line,
                    python_builder.column(
                        python_line, python_spelling, python_occurrence
                    ),
                    python_spelling,
                    python_usage,
                    python_target,
                    meldra_path,
                    meldra_line,
                    meldra_builder.column(
                        meldra_line, meldra_spelling, meldra_occurrence
                    ),
                    meldra_spelling,
                    meldra_usage,
                    meldra_target,
                )
            )

        add_link(
            "parameter_type",
            p_model_path,
            pm,
            p_timestamp_header,
            "Clock",
            "Type",
            f"{fixture}.model.Clock",
            m_model_path,
            mm,
            m_timestamp_header,
            "Clock",
            "Capability",
            f"{fixture}.model.Clock",
        )
        add_link(
            "typed_member",
            p_model_path,
            pm,
            p_timestamp_call,
            "now",
            "Field",
            f"{fixture}.model.Clock.now",
            m_model_path,
            mm,
            m_timestamp_call,
            "now",
            "Field",
            f"{fixture}.model.Clock$now",
        )
        add_link(
            "parameter_type",
            p_model_path,
            pm,
            p_cached_header,
            "Clock",
            "Type",
            f"{fixture}.model.Clock",
            m_model_path,
            mm,
            m_cached_header,
            "Clock",
            "Capability",
            f"{fixture}.model.Clock",
        )

        for number in range(8):
            p_header = ps.add(f"def render{number}(user: User) -> str:")
            p_field = ps.add("    local = user.name")
            p_call = ps.add("    return clean(local)")
            m_header = ms.add(f"fn render{number}(user: User) -> Text:")
            m_field = ms.add("    let local = user.name")
            m_call = ms.add("    clean(local)")
            add_link(
                "parameter_type",
                p_service_path,
                ps,
                p_header,
                "User",
                "Type",
                f"{fixture}.model.User",
                m_service_path,
                ms,
                m_header,
                "User",
                "Type",
                f"{fixture}.model.User",
            )
            add_link(
                "record_field",
                p_service_path,
                ps,
                p_field,
                "name",
                "Field",
                f"{fixture}.model.User.name",
                m_service_path,
                ms,
                m_field,
                "name",
                "Field",
                f"{fixture}.model.User$name",
            )
            add_link(
                "import_alias_call",
                p_service_path,
                ps,
                p_call,
                "clean",
                "Call",
                f"{fixture}.model.normalize",
                m_service_path,
                ms,
                m_call,
                "clean",
                "Value",
                f"{fixture}.model.normalize",
            )

        for number in range(4):
            p_header = ps.add(f"def score{number}(user: User) -> int:")
            p_field = ps.add(f"    return user.score + {number}")
            m_header = ms.add(f"fn score{number}(user: User) -> Int:")
            m_field = ms.add(f"    user.score + {number}")
            add_link(
                "parameter_type",
                p_service_path,
                ps,
                p_header,
                "User",
                "Type",
                f"{fixture}.model.User",
                m_service_path,
                ms,
                m_header,
                "User",
                "Type",
                f"{fixture}.model.User",
            )
            add_link(
                "record_field",
                p_service_path,
                ps,
                p_field,
                "score",
                "Field",
                f"{fixture}.model.User.score",
                m_service_path,
                ms,
                m_field,
                "score",
                "Field",
                f"{fixture}.model.User$score",
            )

        p_active_header = ps.add("def active(user: User) -> bool:")
        p_active_body = ps.add("    return user.status == Status.Active")
        m_active_header = ms.add("fn active(user: User) -> Bool:")
        m_active_body = ms.add("    user.status == Status.Active")
        for kind, spelling, usage, python_target, meldra_target in (
            (
                "parameter_type",
                "User",
                "Type",
                f"{fixture}.model.User",
                f"{fixture}.model.User",
            ),
            (
                "record_field",
                "status",
                "Field",
                f"{fixture}.model.User.status",
                f"{fixture}.model.User$status",
            ),
            (
                "enum_type",
                "Status",
                "Value",
                f"{fixture}.model.Status",
                f"{fixture}.model.Status",
            ),
            (
                "enum_variant",
                "Active",
                "Field",
                f"{fixture}.model.Status.Active",
                f"{fixture}.model.Status$Active",
            ),
        ):
            p_line = p_active_header if spelling == "User" else p_active_body
            m_line = m_active_header if spelling == "User" else m_active_body
            add_link(
                kind,
                p_service_path,
                ps,
                p_line,
                spelling,
                usage,
                python_target,
                m_service_path,
                ms,
                m_line,
                spelling,
                "Type" if spelling == "User" else ("Value" if spelling == "Status" else "Field"),
                meldra_target,
            )

        p_choose_header = ps.add("def choose(status: Status) -> str:")
        p_choose_body = ps.add(
            '    return "active" if status == Status.Active else "disabled"'
        )
        m_choose_header = ms.add("fn choose(status: Status) -> Text:")
        m_choose_body = ms.add("    if status == Status.Active:")
        ms.add('        "active"')
        ms.add("    else:")
        ms.add('        "disabled"')
        add_link(
            "parameter_type",
            p_service_path,
            ps,
            p_choose_header,
            "Status",
            "Type",
            f"{fixture}.model.Status",
            m_service_path,
            ms,
            m_choose_header,
            "Status",
            "Type",
            f"{fixture}.model.Status",
        )
        add_link(
            "enum_type",
            p_service_path,
            ps,
            p_choose_body,
            "Status",
            "Value",
            f"{fixture}.model.Status",
            m_service_path,
            ms,
            m_choose_body,
            "Status",
            "Value",
            f"{fixture}.model.Status",
        )
        add_link(
            "enum_variant",
            p_service_path,
            ps,
            p_choose_body,
            "Active",
            "Field",
            f"{fixture}.model.Status.Active",
            m_service_path,
            ms,
            m_choose_body,
            "Active",
            "Field",
            f"{fixture}.model.Status$Active",
        )

        p_shadow_header = ps.add("def shadow(user: User) -> str:")
        p_shadow_field = ps.add("    clean = user.name")
        ps.add("    return clean")
        m_shadow_header = ms.add("fn shadow(user: User) -> Text:")
        m_shadow_field = ms.add("    let clean = user.name")
        ms.add("    clean")
        add_link(
            "parameter_type",
            p_service_path,
            ps,
            p_shadow_header,
            "User",
            "Type",
            f"{fixture}.model.User",
            m_service_path,
            ms,
            m_shadow_header,
            "User",
            "Type",
            f"{fixture}.model.User",
        )
        add_link(
            "record_field",
            p_service_path,
            ps,
            p_shadow_field,
            "name",
            "Field",
            f"{fixture}.model.User.name",
            m_service_path,
            ms,
            m_shadow_field,
            "name",
            "Field",
            f"{fixture}.model.User$name",
        )

        p_aggregate_header = ps.add("def aggregate(user: User) -> str:")
        p_aggregate_call = ps.add("    return render0(user)")
        m_aggregate_header = ms.add("fn aggregate(user: User) -> Text:")
        m_aggregate_call = ms.add("    render0(user)")
        add_link(
            "parameter_type",
            p_service_path,
            ps,
            p_aggregate_header,
            "User",
            "Type",
            f"{fixture}.model.User",
            m_service_path,
            ms,
            m_aggregate_header,
            "User",
            "Type",
            f"{fixture}.model.User",
        )
        add_link(
            "same_module_call",
            p_service_path,
            ps,
            p_aggregate_call,
            "render0",
            "Call",
            f"{fixture}.service.render0",
            m_service_path,
            ms,
            m_aggregate_call,
            "render0",
            "Value",
            f"{fixture}.service.render0",
        )

        ma = _Builder()
        pa = _Builder()
        ma.add(f"package {app}")
        ma.add("module main")
        ma.add(f"use {fixture}.model::{{User}}")
        ma.add(f"use {fixture}.service::{{aggregate}}")
        ma.add("export run")
        m_app_header = ma.add("fn run(user: User) -> Text:")
        m_app_call = ma.add("    aggregate(user)")
        pa.add(f"from {fixture}.model import User")
        pa.add(f"from {fixture}.service import aggregate")
        p_app_header = pa.add("def run(user: User) -> str:")
        p_app_call = pa.add("    return aggregate(user)")
        add_link(
            "cross_package_type",
            p_app_path,
            pa,
            p_app_header,
            "User",
            "Type",
            f"{fixture}.model.User",
            m_app_path,
            ma,
            m_app_header,
            "User",
            "Type",
            f"{fixture}.model.User",
        )
        add_link(
            "cross_package_call",
            p_app_path,
            pa,
            p_app_call,
            "aggregate",
            "Call",
            f"{fixture}.service.aggregate",
            m_app_path,
            ma,
            m_app_call,
            "aggregate",
            "Value",
            f"{fixture}.service.aggregate",
        )

        moved = _Builder()
        moved.add(f"package {fixture}.moved")
        meldra_sources[m_model_path] = mm.source()
        meldra_sources[m_service_path] = ms.source()
        meldra_sources[m_moved_path] = moved.source()
        meldra_sources[m_app_path] = ma.source()
        python_sources[p_model_path] = pm.source()
        python_sources[p_service_path] = ps.source()
        python_sources[p_app_path] = pa.source()

    return PairedCorpus(
        tuple(fixture_ids),
        tuple(meldra_sources.items()),
        tuple(python_sources.items()),
        tuple(references),
    )


def generate_negative_cases(
    program_count: int = DEFAULT_PROGRAM_COUNT,
) -> tuple[NegativeCase, ...]:
    if not 30 <= program_count <= 50:
        raise ValueError("negative corpus follows the 30-50 program corpus")
    cases: list[NegativeCase] = []
    for index in range(program_count):
        package = f"negative{index:02d}"
        templates: tuple[tuple[str, Mapping[str, str]], ...] = (
            (
                "UnknownBinding",
                {"main.meldra": f"package {package}.main\nexport bad\nfn bad() -> Int:\n    missing()\n"},
            ),
            (
                "UnknownType",
                {"main.meldra": f"package {package}.main\nexport bad\nfn bad(value: Missing) -> Int:\n    1\n"},
            ),
            (
                "ArgumentTypeMismatch",
                {"main.meldra": f"package {package}.main\nexport bad\nfn take(value: Int) -> Int:\n    value\nfn bad() -> Int:\n    take(\"wrong\")\n"},
            ),
            (
                "ReturnTypeMismatch",
                {"main.meldra": f"package {package}.main\nexport bad\nfn bad() -> Int:\n    \"wrong\"\n"},
            ),
            (
                "NonExhaustiveMatch",
                {"main.meldra": f"package {package}.main\nexport Status, bad\nenum Status:\n    Ready\n    Failed\nfn bad(status: Status) -> Int:\n    match status:\n        Ready: 1\n"},
            ),
            (
                "EffectInPureFunction",
                {"main.meldra": f"package {package}.main\nexport Net, bad\ncapability Net:\n    post(value: Int) -> Int uses network.post\nfn bad(net: cap Net) -> Int:\n    uses network.post\n    net.post(1)\n"},
            ),
            (
                "CapabilityEscalation",
                {"main.meldra": f"package {package}.main\nexport bad\ntask bad() -> Int:\n    uses network.post\n    1\n"},
            ),
            (
                "EffectNotDeclared",
                {"main.meldra": f"package {package}.main\nexport Net, bad\ncapability Net:\n    post(value: Int) -> Int uses network.post\ntask bad(net: cap Net) -> Int:\n    net.post(1)\n"},
            ),
            (
                "PackageCycle",
                {
                    "a.meldra": f"package {package}a\nmodule main\nuse {package}b.main::{{b}}\nexport a\nfn a() -> Int:\n    b()\n",
                    "b.meldra": f"package {package}b\nmodule main\nuse {package}a.main::{{a}}\nexport b\nfn b() -> Int:\n    a()\n",
                },
            ),
        )
        for offset, (code, sources) in enumerate(templates):
            cases.append(
                NegativeCase(
                    f"{package}:{offset:02d}:{code}",
                    code,
                    tuple(sorted(sources.items())),
                )
            )
    return tuple(cases)


def _current_python_world(sources: Mapping[str, str]):
    with tempfile.TemporaryDirectory(prefix="meldra-stage04-python-") as temporary:
        root = Path(temporary)
        for path, source in sources.items():
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(source, encoding="utf-8")
        return scan_python(root)


def _measure_current_python(
    corpus: PairedCorpus,
    program: Any,
) -> BindingArmMeasurement:
    exact = wrong = uncertain = missing = 0
    for link in corpus.references:
        candidates = tuple(
            reference
            for reference in program.references
            if reference.file == link.python_path
            and reference.span.start.line == link.python_line
            and reference.expected in {link.python_spelling, link.python_target.rsplit(".", 1)[-1]}
        )
        if not candidates:
            missing += 1
            continue
        resolved = tuple(item for item in candidates if item.target_id is not None)
        if not resolved:
            uncertain += 1
            continue
        matching = tuple(
            item
            for item in resolved
            if program.entity(item.target_id).fqname == link.python_target
        )
        if len(matching) == 1 and matching[0].resolution == "Exact":
            exact += 1
        elif matching:
            uncertain += 1
        else:
            wrong += 1
    return BindingArmMeasurement(
        "current_python_analyzer",
        exact,
        wrong,
        uncertain,
        missing,
        len(corpus.references),
    )


def _measure_strong_python(
    corpus: PairedCorpus,
    report: PythonBindingReport,
) -> BindingArmMeasurement:
    exact = wrong = uncertain = missing = 0
    for link in corpus.references:
        candidates = report.references_at(
            link.python_path,
            link.python_line,
            link.python_column,
            link.python_spelling,
        )
        candidates = tuple(
            item for item in candidates if item.usage == link.python_usage
        )
        if not candidates:
            missing += 1
            continue
        matching = tuple(
            item
            for item in candidates
            if item.target_symbol_id is not None
            and report.symbol(item.target_symbol_id).locator == link.python_target
        )
        if len(matching) == 1 and matching[0].status == "Exact":
            exact += 1
        elif matching:
            uncertain += 1
        elif any(item.status == "Unknown" for item in candidates):
            uncertain += 1
        else:
            wrong += 1
    return BindingArmMeasurement(
        "strong_structural_python_binder",
        exact,
        wrong,
        uncertain,
        missing,
        len(corpus.references),
    )


def _measure_meldra(
    corpus: PairedCorpus,
    compilation: FrontendCompilation,
) -> BindingArmMeasurement:
    exact = wrong = uncertain = missing = 0
    for link in corpus.references:
        candidates = tuple(
            reference
            for reference in compilation.hir.references
            if reference.path == link.meldra_path
            and reference.span.line == link.meldra_line
            and reference.spelling == link.meldra_spelling
            and reference.usage == link.meldra_usage
        )
        if not candidates:
            missing += 1
            continue
        matching = tuple(
            item
            for item in candidates
            if item.target_symbol_id is not None
            and compilation.hir.symbol(item.target_symbol_id).locator
            == link.meldra_target
        )
        if len(matching) == 1 and matching[0].status == "Exact":
            exact += 1
        elif matching:
            uncertain += 1
        else:
            wrong += 1
    return BindingArmMeasurement(
        "meldra_closed_binder",
        exact,
        wrong,
        uncertain,
        missing,
        len(corpus.references),
    )


def _measure_changes(
    corpus: PairedCorpus,
    compilation: FrontendCompilation,
) -> dict[str, Any]:
    attempts = Counter()
    applied = Counter()
    identity = 0
    private_interface = 0
    public_interface = 0
    escalations = 0
    collisions = 0
    for fixture in corpus.fixture_ids:
        constant = compilation.hir.symbol(f"{fixture}.model.constant")
        render = compilation.hir.symbol(f"{fixture}.service.render0")
        cached = compilation.hir.symbol(f"{fixture}.model.cached_time")
        timestamp = compilation.hir.symbol(f"{fixture}.model.timestamp")
        operations = (
            ("Rename", CoreChange.rename(constant.symbol_id, f"constant_{fixture}")),
            ("Move", CoreChange.move(constant.symbol_id, "moved")),
            (
                "ChangeSignature",
                CoreChange.change_signature(
                    render.symbol_id,
                    {
                        "args": [
                            {"name": "user", "type": "User"},
                            {"name": "mode", "type": "Int"},
                        ],
                        "returns": "String",
                    },
                ),
            ),
            (
                "ImplementationEdit",
                CoreChange.change_implementation(
                    constant.symbol_id,
                    {"kind": "literal", "value": 999},
                ),
            ),
            (
                "EffectRestriction",
                CoreChange.restrict_effect(cached.symbol_id, ()),
            ),
        )
        for name, change in operations:
            attempts[name] += 1
            result = apply_core_change(compilation.world, change)
            if result.applied:
                applied[name] += 1
                new_symbol = result.world.symbol(change.symbol_id)
                if (
                    new_symbol.id == change.symbol_id
                    and new_symbol.revision_id
                    != compilation.world.symbol(change.symbol_id).revision_id
                ):
                    identity += 1
        old_package = compilation.world.package(fixture)
        private = apply_core_change(
            compilation.world,
            CoreChange.change_implementation(
                constant.symbol_id, {"kind": "literal", "value": 1000}
            ),
        )
        if (
            private.interface_changed_packages == ()
            and private.world.package(fixture).interface_revision_id
            == old_package.interface_revision_id
            and private.world.package(fixture).implementation_revision_id
            != old_package.implementation_revision_id
        ):
            private_interface += 1
        public = apply_core_change(
            compilation.world,
            CoreChange.change_signature(
                render.symbol_id,
                {
                    "args": [
                        {"name": "user", "type": "User"},
                        {"name": "mode", "type": "Int"},
                    ],
                    "returns": "String",
                },
            ),
        )
        aggregate = compilation.hir.symbol(f"{fixture}.service.aggregate")
        app = "app" + fixture.removeprefix("case")
        run = compilation.hir.symbol(f"{app}.main.run")
        if (
            public.world.package(fixture).interface_revision_id
            != old_package.interface_revision_id
            and aggregate.symbol_id in public.affected_symbols
            and run.symbol_id in public.affected_symbols
            and set(public.affected_packages)
            == {
                compilation.world.package(fixture).id,
                compilation.world.package(app).id,
            }
        ):
            public_interface += 1
        escalation = apply_core_change(
            compilation.world,
            CoreChange.change_implementation(
                timestamp.symbol_id,
                timestamp.contract,
                effects=("clock.now", "network.post"),
                capabilities=("Clock", "Network"),
            ),
        )
        if escalation.blocked and escalation.world is compilation.world:
            escalations += 1
        try:
            apply_core_change(
                compilation.world,
                CoreChange.rename(constant.symbol_id, "normalize"),
            )
        except CoreError:
            collisions += 1
    return {
        "attempts": attempts,
        "applied": applied,
        "identity": identity,
        "private_interface": private_interface,
        "public_interface": public_interface,
        "escalations": escalations,
        "collisions": collisions,
    }


def _measure_execution(
    corpus: PairedCorpus,
    compilation: FrontendCompilation,
) -> tuple[int, int]:
    passes = 0
    denominator = 0
    for fixture in corpus.fixture_ids:
        app = "app" + fixture.removeprefix("case")
        user = compilation.hir.symbol(f"{fixture}.model.User")
        status = compilation.hir.symbol(f"{fixture}.model.Status")
        value = RecordValue(
            user.symbol_id,
            (
                ("name", f"user-{fixture}"),
                ("score", 7),
                ("status", EnumValue(status.symbol_id, "Active")),
            ),
        )
        evaluator = ReferenceEvaluator(
            compilation, handlers={"clock.now": lambda: 41}
        )
        run = evaluator.evaluate(f"{app}.main.run", (value,))
        denominator += 1
        if run.value == f"user-{fixture}" and not run.effect_trace:
            passes += 1
        clock = evaluator.capability(f"{fixture}.model.Clock")
        timestamp = evaluator.evaluate(
            f"{fixture}.model.timestamp", (clock,)
        )
        denominator += 1
        if (
            timestamp.value == 41
            and len(timestamp.effect_trace) == 1
            and timestamp.effect_trace[0].effect == "clock.now"
        ):
            passes += 1
    return passes, denominator


def _formatted_sources(sources: Mapping[str, str]) -> dict[str, str]:
    result = {}
    for path, source in sources.items():
        lines = source.splitlines()
        lines.insert(1, "")
        lines.insert(2, "# formatting-only Stage 0.4 variant")
        result[path] = "\n".join(lines) + "\n"
    return result


def _externally_edited_sources(sources: Mapping[str, str]) -> dict[str, str]:
    result = {}
    for path, source in sources.items():
        lines = source.splitlines()
        for index, line in enumerate(lines[:-1]):
            if line == "fn constant() -> Int:":
                value = int(lines[index + 1].strip())
                lines[index + 1] = f"    {value + 1000}"
        result[path] = "\n".join(lines) + "\n"
    return result


def _binding_context(
    program_count: int,
) -> tuple[
    PairedCorpus,
    FrontendCompilation,
    BindingArmMeasurement,
    BindingArmMeasurement,
    BindingArmMeasurement,
]:
    corpus = generate_paired_corpus(program_count)
    compilation = compile_frontend(dict(corpus.meldra_sources))
    python_sources = dict(corpus.python_sources)
    strong_report = bind_python_sources(python_sources)
    current_program = _current_python_world(python_sources)
    return (
        corpus,
        compilation,
        _measure_current_python(corpus, current_program),
        _measure_strong_python(corpus, strong_report),
        _measure_meldra(corpus, compilation),
    )


def run_binding_comparison(
    program_count: int = DEFAULT_PROGRAM_COUNT,
) -> tuple[
    PairedCorpus,
    BindingArmMeasurement,
    BindingArmMeasurement,
    BindingArmMeasurement,
]:
    corpus, _, current, strong, meldra = _binding_context(program_count)
    return corpus, current, strong, meldra


def run_frontend_benchmark(
    program_count: int = DEFAULT_PROGRAM_COUNT,
) -> FrontendBenchmarkReport:
    corpus, compilation, current, strong, meldra = _binding_context(
        program_count
    )
    meldra_sources = dict(corpus.meldra_sources)

    parser_roundtrips = sum(
        cst.to_source_bytes() == meldra_sources[cst.path].encode("utf-8")
        for cst in compilation.csts
    )
    parser_denominator = len(compilation.csts)

    negatives = generate_negative_cases(program_count)
    negative_counts = Counter()
    negative_passes = Counter()
    for case in negatives:
        negative_counts[case.expected_code] += 1
        result = check_frontend(dict(case.sources))
        codes = {item.code for item in result.diagnostics}
        if result.compilation is None and case.expected_code in codes:
            negative_passes[case.expected_code] += 1

    changes = _measure_changes(corpus, compilation)
    change_denominator = sum(changes["attempts"].values())
    change_applied = sum(changes["applied"].values())
    execution_passes, execution_denominator = _measure_execution(
        corpus, compilation
    )
    formatted = compile_frontend(_formatted_sources(meldra_sources))
    deterministic_passes = int(
        formatted.core_program.to_json() == compilation.core_program.to_json()
        and formatted.hir.package_revisions == compilation.hir.package_revisions
    )
    externally_edited = compile_frontend(
        _externally_edited_sources(meldra_sources)
    )
    original_symbols = {
        item.locator: item.symbol_id for item in compilation.hir.symbols
    }
    externally_edited_symbols = {
        item.locator: item.symbol_id for item in externally_edited.hir.symbols
    }
    external_edits_new_identity = sum(
        original_symbols[f"{fixture}.model.constant"]
        != externally_edited_symbols[f"{fixture}.model.constant"]
        for fixture in corpus.fixture_ids
    )
    deterministic_denominator = 1

    support_profile = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "benchmarks"
        / "merlo"
        / "benchmarks"
        / "meldra_stage04_support_profile.json"
    )
    support_profile_sha256 = hashlib.sha256(support_profile.read_bytes()).hexdigest()

    negative_total = sum(negative_passes.values())
    negative_denominator = len(negatives)
    gates = (
        GateMeasurement(
            "parser_byte_exact_roundtrip",
            SUPPORTED if parser_roundtrips == parser_denominator else NOT_SUPPORTED,
            parser_roundtrips,
            parser_denominator,
            "Every supported source must round-trip byte-for-byte through the CST.",
        ),
        GateMeasurement(
            "closed_internal_binding",
            SUPPORTED if meldra.exact == meldra.denominator else NOT_SUPPORTED,
            meldra.exact,
            meldra.denominator,
            "Every preregistered valid logical reference must bind to its declared target.",
        ),
        GateMeasurement(
            "unknown_internal_binding_rejection",
            (
                SUPPORTED
                if negative_passes["UnknownBinding"]
                == negative_counts["UnknownBinding"]
                else NOT_SUPPORTED
            ),
            negative_passes["UnknownBinding"],
            negative_counts["UnknownBinding"],
            "An unresolved internal name must be a compile error, never a dynamic edge.",
        ),
        GateMeasurement(
            "positive_and_negative_nominal_typing",
            (
                SUPPORTED
                if negative_total == negative_denominator
                else NOT_SUPPORTED
            ),
            program_count + negative_total,
            program_count + negative_denominator,
            "All valid paired programs compile and every negative case emits its preregistered diagnostic.",
        ),
        GateMeasurement(
            "pure_function_effect_rejection",
            (
                SUPPORTED
                if (
                    negative_passes["EffectInPureFunction"]
                    + negative_passes["EffectNotDeclared"]
                    == negative_counts["EffectInPureFunction"]
                    + negative_counts["EffectNotDeclared"]
                )
                else NOT_SUPPORTED
            ),
            (
                negative_passes["EffectInPureFunction"]
                + negative_passes["EffectNotDeclared"]
            ),
            (
                negative_counts["EffectInPureFunction"]
                + negative_counts["EffectNotDeclared"]
            ),
            "Pure functions cannot call effects and tasks cannot hide undeclared effects.",
        ),
        GateMeasurement(
            "capability_escalation_blocking",
            (
                SUPPORTED
                if (
                    changes["escalations"]
                    + negative_passes["CapabilityEscalation"]
                    == program_count + negative_counts["CapabilityEscalation"]
                )
                else NOT_SUPPORTED
            ),
            (
                changes["escalations"]
                + negative_passes["CapabilityEscalation"]
            ),
            program_count + negative_counts["CapabilityEscalation"],
            "Capability escalation is rejected both during checking and before change materialization.",
        ),
        GateMeasurement(
            "private_edit_interface_hash_stability",
            (
                SUPPORTED
                if changes["private_interface"] == program_count
                else NOT_SUPPORTED
            ),
            changes["private_interface"],
            program_count,
            "A private implementation edit must preserve its package interface revision.",
        ),
        GateMeasurement(
            "public_change_exact_invalidation",
            (
                SUPPORTED
                if changes["public_interface"] == program_count
                else NOT_SUPPORTED
            ),
            changes["public_interface"],
            program_count,
            "A public contract change must invalidate exactly its importing package.",
        ),
        GateMeasurement(
            "changeir_symbol_id_provenance",
            (
                SUPPORTED
                if (
                    changes["identity"] == change_denominator
                    and changes["collisions"] == program_count
                )
                else NOT_SUPPORTED
            ),
            changes["identity"] + changes["collisions"],
            change_denominator + program_count,
            "ChangeIR preserves target identity and rejects accidental identity collisions.",
        ),
        GateMeasurement(
            "external_edit_identity_noninheritance",
            (
                SUPPORTED
                if external_edits_new_identity == program_count
                else NOT_SUPPORTED
            ),
            external_edits_new_identity,
            program_count,
            "Equivalent external text edits receive new identity without explicit ChangeIR provenance.",
        ),
        GateMeasurement(
            "deterministic_lowering",
            SUPPORTED if deterministic_passes else NOT_SUPPORTED,
            deterministic_passes,
            deterministic_denominator,
            "Formatting-only source changes must produce byte-identical canonical CoreIR.",
        ),
        GateMeasurement(
            "reference_evaluator",
            SUPPORTED if execution_passes == execution_denominator else NOT_SUPPORTED,
            execution_passes,
            execution_denominator,
            "Pure values and explicit effect traces must match the fixture oracle.",
        ),
        GateMeasurement(
            "strong_python_baseline_comparison",
            (
                SUPPORTED
                if strong.denominator == meldra.denominator == len(corpus.references)
                else NOT_SUPPORTED
            ),
            strong.denominator,
            meldra.denominator,
            "Strong Python and Meldra binders use the same preregistered logical-reference denominator.",
        ),
    )
    all_kernel_gates = all(item.status == SUPPORTED for item in gates)
    decision = "NO_GO_LANGUAGE_ALPHA"
    authorized = (
        "EXTERNAL_STAGE04_VALIDATION"
        if all_kernel_gates
        else "FIX_FRONTEND_KERNEL"
    )
    return FrontendBenchmarkReport(
        corpus=corpus,
        current_python=current,
        strong_python=strong,
        meldra=meldra,
        parser_roundtrips=parser_roundtrips,
        parser_denominator=parser_denominator,
        negative_expected=negative_total,
        negative_denominator=negative_denominator,
        negative_breakdown=tuple(
            (code, negative_passes[code], negative_counts[code])
            for code in sorted(negative_counts)
        ),
        semantic_changes_applied=change_applied,
        semantic_change_denominator=change_denominator,
        semantic_change_breakdown=tuple(
            (name, changes["applied"][name], changes["attempts"][name])
            for name in sorted(changes["attempts"])
        ),
        identity_continuity=changes["identity"],
        external_edits_new_identity=external_edits_new_identity,
        interface_private_passes=changes["private_interface"],
        interface_public_passes=changes["public_interface"],
        capability_escalations_blocked=changes["escalations"],
        target_collisions_blocked=changes["collisions"],
        execution_passes=execution_passes,
        execution_denominator=execution_denominator,
        deterministic_lowering_passes=deterministic_passes,
        deterministic_lowering_denominator=deterministic_denominator,
        support_profile_sha256=support_profile_sha256,
        gates=gates,
        decision=decision,
        authorized_next_stage=authorized,
    )


__all__ = [
    "DEFAULT_PROGRAM_COUNT",
    "FRONTEND_BENCHMARK_SCHEMA_VERSION",
    "NOT_SUPPORTED",
    "SUPPORTED",
    "UNMEASURED",
    "BindingArmMeasurement",
    "FrontendBenchmarkReport",
    "GateMeasurement",
    "LogicalReference",
    "NegativeCase",
    "PairedCorpus",
    "generate_negative_cases",
    "generate_paired_corpus",
    "run_binding_comparison",
    "run_frontend_benchmark",
]
