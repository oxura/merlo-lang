from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .context import compile_context
from .coverage import semantic_coverage

from .core_semantics import (
    CoreChange,
    CoreChangeResult,
    CoreProgram,
    CoreWorld,
    apply_core_change,
    compile_core,
)
from .evolution import ChangeBlocked
from .impact import analyze_impact
from .model import EditCapability, Provenance, Resolution
from .world import SoftwareWorld, WorldError


SUPPORTED = "SUPPORTED"
NOT_SUPPORTED = "NOT_SUPPORTED"
UNMEASURED = "UNMEASURED"
BENCHMARK_SCHEMA = 1

_STATUSES = frozenset((SUPPORTED, NOT_SUPPORTED, UNMEASURED))


def _stable_json_bytes(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


@dataclass(frozen=True)
class FixtureProvenance:
    fixture_id: str
    source: str = "generated:meldra.core_bench"
    label_source: str = "benchmark_policy"
    oracle: str = "declared_public_contract"
    human_ground_truth: bool = False

    def __post_init__(self) -> None:
        if not all((self.fixture_id, self.source, self.label_source, self.oracle)):
            raise ValueError("fixture provenance fields must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "source": self.source,
            "label_source": self.label_source,
            "oracle": self.oracle,
            "human_ground_truth": self.human_ground_truth,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixtureProvenance":
        return cls(
            fixture_id=str(value["fixture_id"]),
            source=str(value.get("source", "generated:meldra.core_bench")),
            label_source=str(value.get("label_source", "benchmark_policy")),
            oracle=str(value.get("oracle", "declared_public_contract")),
            human_ground_truth=bool(value.get("human_ground_truth", False)),
        )


@dataclass(frozen=True)
class ArmMeasurement:
    arm: str
    public_contracts: tuple[str, ...]
    internal_exact_numerator: int
    internal_reference_denominator: int
    unknown_reference_count: int
    foreign_reference_count: int
    dynamic_reference_count: int
    reference_denominator: int
    private_change_affected_symbols: int
    private_change_symbol_denominator: int
    private_change_affected_packages: int
    private_change_package_denominator: int
    private_change_interface_changed_packages: int
    interface_change_changed_packages: int
    interface_change_package_denominator: int
    serialized_context_bytes: int
    safe_rename_count: int
    rename_attempt_denominator: int
    safe_move_count: int
    move_attempt_denominator: int
    safe_change_signature_count: int
    change_signature_attempt_denominator: int
    identity_continuity_count: int
    identity_change_denominator: int

    def __post_init__(self) -> None:
        if not self.arm:
            raise ValueError("benchmark arm must be non-empty")
        object.__setattr__(self, "public_contracts", tuple(sorted(self.public_contracts)))
        values = (
            self.internal_exact_numerator,
            self.internal_reference_denominator,
            self.unknown_reference_count,
            self.foreign_reference_count,
            self.dynamic_reference_count,
            self.reference_denominator,
            self.private_change_affected_symbols,
            self.private_change_symbol_denominator,
            self.private_change_affected_packages,
            self.private_change_package_denominator,
            self.private_change_interface_changed_packages,
            self.interface_change_changed_packages,
            self.interface_change_package_denominator,
            self.serialized_context_bytes,
            self.safe_rename_count,
            self.rename_attempt_denominator,
            self.safe_move_count,
            self.move_attempt_denominator,
            self.safe_change_signature_count,
            self.change_signature_attempt_denominator,
            self.identity_continuity_count,
            self.identity_change_denominator,
        )
        if min(values, default=0) < 0:
            raise ValueError("benchmark counts must be non-negative")
        fractions = (
            (self.internal_exact_numerator, self.internal_reference_denominator),
            (self.unknown_reference_count, self.reference_denominator),
            (self.foreign_reference_count, self.reference_denominator),
            (self.dynamic_reference_count, self.reference_denominator),
            (self.private_change_affected_symbols, self.private_change_symbol_denominator),
            (self.private_change_affected_packages, self.private_change_package_denominator),
            (self.private_change_interface_changed_packages, self.private_change_package_denominator),
            (self.interface_change_changed_packages, self.interface_change_package_denominator),
            (self.safe_rename_count, self.rename_attempt_denominator),
            (self.safe_move_count, self.move_attempt_denominator),
            (self.safe_change_signature_count, self.change_signature_attempt_denominator),
            (self.identity_continuity_count, self.identity_change_denominator),
        )
        if any(numerator > denominator for numerator, denominator in fractions):
            raise ValueError("benchmark numerator cannot exceed its denominator")

    @property
    def safe_change_count(self) -> int:
        return (
            self.safe_rename_count
            + self.safe_move_count
            + self.safe_change_signature_count
        )

    @property
    def safe_change_denominator(self) -> int:
        return (
            self.rename_attempt_denominator
            + self.move_attempt_denominator
            + self.change_signature_attempt_denominator
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "public_contracts": list(self.public_contracts),
            "internal_exact_numerator": self.internal_exact_numerator,
            "internal_reference_denominator": self.internal_reference_denominator,
            "unknown_reference_count": self.unknown_reference_count,
            "foreign_reference_count": self.foreign_reference_count,
            "dynamic_reference_count": self.dynamic_reference_count,
            "reference_denominator": self.reference_denominator,
            "private_change_affected_symbols": self.private_change_affected_symbols,
            "private_change_symbol_denominator": self.private_change_symbol_denominator,
            "private_change_affected_packages": self.private_change_affected_packages,
            "private_change_package_denominator": self.private_change_package_denominator,
            "private_change_interface_changed_packages": self.private_change_interface_changed_packages,
            "interface_change_changed_packages": self.interface_change_changed_packages,
            "interface_change_package_denominator": self.interface_change_package_denominator,
            "serialized_context_bytes": self.serialized_context_bytes,
            "safe_rename_count": self.safe_rename_count,
            "rename_attempt_denominator": self.rename_attempt_denominator,
            "safe_move_count": self.safe_move_count,
            "move_attempt_denominator": self.move_attempt_denominator,
            "safe_change_signature_count": self.safe_change_signature_count,
            "change_signature_attempt_denominator": self.change_signature_attempt_denominator,
            "identity_continuity_count": self.identity_continuity_count,
            "identity_change_denominator": self.identity_change_denominator,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArmMeasurement":
        return cls(
            arm=str(value["arm"]),
            public_contracts=tuple(str(item) for item in value.get("public_contracts", ())),
            internal_exact_numerator=int(value["internal_exact_numerator"]),
            internal_reference_denominator=int(value["internal_reference_denominator"]),
            unknown_reference_count=int(value["unknown_reference_count"]),
            foreign_reference_count=int(value["foreign_reference_count"]),
            dynamic_reference_count=int(value["dynamic_reference_count"]),
            reference_denominator=int(value["reference_denominator"]),
            private_change_affected_symbols=int(value["private_change_affected_symbols"]),
            private_change_symbol_denominator=int(value["private_change_symbol_denominator"]),
            private_change_affected_packages=int(value["private_change_affected_packages"]),
            private_change_package_denominator=int(value["private_change_package_denominator"]),
            private_change_interface_changed_packages=int(value["private_change_interface_changed_packages"]),
            interface_change_changed_packages=int(value["interface_change_changed_packages"]),
            interface_change_package_denominator=int(value["interface_change_package_denominator"]),
            serialized_context_bytes=int(value["serialized_context_bytes"]),
            safe_rename_count=int(value["safe_rename_count"]),
            rename_attempt_denominator=int(value["rename_attempt_denominator"]),
            safe_move_count=int(value["safe_move_count"]),
            move_attempt_denominator=int(value["move_attempt_denominator"]),
            safe_change_signature_count=int(value["safe_change_signature_count"]),
            change_signature_attempt_denominator=int(value["change_signature_attempt_denominator"]),
            identity_continuity_count=int(value["identity_continuity_count"]),
            identity_change_denominator=int(value["identity_change_denominator"]),
        )


@dataclass(frozen=True)
class HypothesisResult:
    hypothesis: str
    status: str
    numerator: int | None
    denominator: int | None
    rationale: str

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError(f"unknown hypothesis status: {self.status}")
        if not self.hypothesis or not self.rationale:
            raise ValueError("hypothesis and rationale must be non-empty")
        if self.status == UNMEASURED:
            if self.numerator is not None or self.denominator is not None:
                raise ValueError("unmeasured hypotheses must keep counts null")
        elif self.numerator is None or self.denominator is None:
            raise ValueError("measured hypotheses require raw counts")
        elif min(self.numerator, self.denominator) < 0 or self.numerator > self.denominator:
            raise ValueError("invalid hypothesis raw counts")

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis,
            "status": self.status,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HypothesisResult":
        return cls(
            hypothesis=str(value["hypothesis"]),
            status=str(value["status"]),
            numerator=(int(value["numerator"]) if value.get("numerator") is not None else None),
            denominator=(int(value["denominator"]) if value.get("denominator") is not None else None),
            rationale=str(value["rationale"]),
        )


@dataclass(frozen=True)
class AgentMeasurement:
    status: str = UNMEASURED
    provider: str | None = None
    successful_tasks: int | None = None
    task_success_denominator: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: int | None = None

    def __post_init__(self) -> None:
        if self.status != UNMEASURED:
            raise ValueError("the Core benchmark has no agent provider arm")
        values = (
            self.provider,
            self.successful_tasks,
            self.task_success_denominator,
            self.input_tokens,
            self.output_tokens,
            self.tool_calls,
        )
        if any(value is not None for value in values):
            raise ValueError("unmeasured agent metrics must remain null")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "provider": self.provider,
            "successful_tasks": self.successful_tasks,
            "task_success_denominator": self.task_success_denominator,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tool_calls": self.tool_calls,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentMeasurement":
        return cls(
            status=str(value.get("status", UNMEASURED)),
            provider=(str(value["provider"]) if value.get("provider") is not None else None),
            successful_tasks=(int(value["successful_tasks"]) if value.get("successful_tasks") is not None else None),
            task_success_denominator=(int(value["task_success_denominator"]) if value.get("task_success_denominator") is not None else None),
            input_tokens=(int(value["input_tokens"]) if value.get("input_tokens") is not None else None),
            output_tokens=(int(value["output_tokens"]) if value.get("output_tokens") is not None else None),
            tool_calls=(int(value["tool_calls"]) if value.get("tool_calls") is not None else None),
        )


@dataclass(frozen=True)
class FixtureMeasurement:
    fixture_id: str
    provenance: FixtureProvenance
    declared_public_contracts: tuple[str, ...]
    python: ArmMeasurement
    core: ArmMeasurement
    hypotheses: tuple[HypothesisResult, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "declared_public_contracts", tuple(sorted(self.declared_public_contracts)))
        object.__setattr__(self, "hypotheses", tuple(sorted(self.hypotheses, key=lambda item: item.hypothesis)))
        if self.fixture_id != self.provenance.fixture_id:
            raise ValueError("fixture and provenance ids differ")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "provenance": self.provenance.to_dict(),
            "declared_public_contracts": list(self.declared_public_contracts),
            "python": self.python.to_dict(),
            "core": self.core.to_dict(),
            "hypotheses": [item.to_dict() for item in self.hypotheses],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FixtureMeasurement":
        return cls(
            fixture_id=str(value["fixture_id"]),
            provenance=FixtureProvenance.from_dict(value["provenance"]),
            declared_public_contracts=tuple(str(item) for item in value.get("declared_public_contracts", ())),
            python=ArmMeasurement.from_dict(value["python"]),
            core=ArmMeasurement.from_dict(value["core"]),
            hypotheses=tuple(HypothesisResult.from_dict(item) for item in value.get("hypotheses", ())),
        )


@dataclass(frozen=True)
class CoreBenchmarkReport:
    fixtures: tuple[FixtureMeasurement, ...]
    python: ArmMeasurement
    core: ArmMeasurement
    hypotheses: tuple[HypothesisResult, ...]
    agent: AgentMeasurement = AgentMeasurement()
    schema: int = BENCHMARK_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != BENCHMARK_SCHEMA:
            raise ValueError(f"unsupported Core benchmark schema: {self.schema}")
        object.__setattr__(self, "fixtures", tuple(sorted(self.fixtures, key=lambda item: item.fixture_id)))
        object.__setattr__(self, "hypotheses", tuple(sorted(self.hypotheses, key=lambda item: item.hypothesis)))

    def hypothesis(self, name: str) -> HypothesisResult:
        for item in self.hypotheses:
            if item.hypothesis == name:
                return item
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "fixtures": [item.to_dict() for item in self.fixtures],
            "python": self.python.to_dict(),
            "core": self.core.to_dict(),
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "agent": self.agent.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CoreBenchmarkReport":
        return cls(
            fixtures=tuple(FixtureMeasurement.from_dict(item) for item in value.get("fixtures", ())),
            python=ArmMeasurement.from_dict(value["python"]),
            core=ArmMeasurement.from_dict(value["core"]),
            hypotheses=tuple(HypothesisResult.from_dict(item) for item in value.get("hypotheses", ())),
            agent=AgentMeasurement.from_dict(value.get("agent", {})),
            schema=int(value.get("schema", BENCHMARK_SCHEMA)),
        )


@dataclass(frozen=True)
class CoreBenchmarkFixture:
    id: str
    python_files: tuple[tuple[str, str], ...]
    program: CoreProgram
    declared_public_contracts: tuple[str, ...]
    private_symbol_id: str
    public_symbol_id: str
    destination_module: str
    python_private_locator: str
    python_public_locator: str
    python_destination_module: str
    provenance: FixtureProvenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "python_files", tuple(sorted(self.python_files)))
        object.__setattr__(self, "declared_public_contracts", tuple(sorted(self.declared_public_contracts)))
        if self.id != self.provenance.fixture_id:
            raise ValueError("fixture and provenance ids differ")


@dataclass(frozen=True)
class _PythonOperation:
    ready: bool
    applied: bool
    identity_preserved: bool
    affected_packages: tuple[str, ...]


@dataclass(frozen=True)
class _PythonObservation:
    contracts: tuple[str, ...]
    exact_references: int
    internal_references: int
    total_references: int
    unknown_references: int
    foreign_references: int
    dynamic_references: int
    symbol_count: int
    package_count: int
    context_bytes: int
    private_affected_symbols: int
    private_affected_packages: int
    rename: _PythonOperation
    move: _PythonOperation
    change_signature: _PythonOperation


def _write_python_fixture(root: Path, fixture: CoreBenchmarkFixture) -> None:
    for relative, source in fixture.python_files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _python_packages_for_entities(
    world: SoftwareWorld,
    entity_ids: Iterable[str],
) -> tuple[str, ...]:
    packages: set[str] = set()
    for identifier in entity_ids:
        try:
            entity = world.program.entity(identifier)
        except KeyError:
            continue
        packages.add(entity.module.split(".", 1)[0])
    return tuple(sorted(packages))


def _python_capability(
    world: SoftwareWorld,
    target_id: str,
    operation: str,
) -> EditCapability:
    options = {
        "allowed_files": tuple(item.path for item in world.program.files),
        "related_entity_ids": tuple(item.id for item in world.program.entities),
        "max_files": max(20, len(world.program.files) + 5),
        "max_entities": max(50, len(world.program.entities) + 5),
        "max_edits": 200,
        "allow_new_dependencies": True,
        "allow_public_api_break": True,
    }
    if operation == "rename":
        return EditCapability.rename(target_id, **options)
    if operation == "move":
        return EditCapability.move(target_id, **options)
    if operation == "change_signature":
        return EditCapability.change_signature(target_id, **options)
    raise ValueError(f"unsupported Python benchmark operation: {operation}")


def _run_python_change(
    fixture: CoreBenchmarkFixture,
    operation: str,
) -> _PythonOperation:
    with tempfile.TemporaryDirectory(prefix=f"meldra-core-bench-{fixture.id}-") as temporary:
        root = Path(temporary)
        _write_python_fixture(root, fixture)
        world = SoftwareWorld.scan(root)
        target = world.program.entity(fixture.python_public_locator)
        capability = _python_capability(world, target.id, operation)
        if operation == "rename":
            plan = world.plan_rename(
                target.id,
                "renamed_public",
                capability,
            )
        elif operation == "move":
            plan = world.plan_move(
                target.id,
                fixture.python_destination_module,
                capability,
            )
        elif operation == "change_signature":
            plan = world.plan_change_signature(
                target.id,
                "(value: str, suffix: str = '')",
                capability,
            )
        else:
            raise ValueError(f"unsupported Python benchmark operation: {operation}")

        affected_packages = tuple(
            sorted(
                {
                    path.split("/", 1)[0]
                    for path in plan.affected_files
                    if "/" in path
                }
            )
        )
        if not plan.ready:
            return _PythonOperation(False, False, False, affected_packages)
        old_revision = target.revision_hash
        try:
            world.apply(plan, capability)
        except (ChangeBlocked, WorldError):
            return _PythonOperation(True, False, False, affected_packages)
        migrated = world.program.entity(target.id)
        return _PythonOperation(
            ready=True,
            applied=True,
            identity_preserved=(
                migrated.id == target.id and migrated.revision_hash != old_revision
            ),
            affected_packages=affected_packages,
        )


def _observe_python(fixture: CoreBenchmarkFixture) -> _PythonObservation:
    with tempfile.TemporaryDirectory(prefix=f"meldra-core-observe-{fixture.id}-") as temporary:
        root = Path(temporary)
        _write_python_fixture(root, fixture)
        world = SoftwareWorld.scan(root)
        coverage = semantic_coverage(world.program)
        public_target = world.program.entity(fixture.python_public_locator)
        private_target = world.program.entity(fixture.python_private_locator)
        capsule = compile_context(
            world.program,
            public_target.id,
            goal="Measure an exported semantic change",
        )
        private_impact = analyze_impact(world.program, private_target.id)
        affected_ids = {
            private_target.id,
            *private_impact.direct_callers,
            *private_impact.transitive_callers,
        }
        contracts = tuple(
            sorted(
                f"{entity.module}.{entity.qualname}{entity.signature}"
                for entity in world.program.entities
                if entity.public
            )
        )
        foreign_references = tuple(
            reference
            for reference in world.program.references
            if reference.provenance == Provenance.EXTERNAL_IMPORT
        )
        internal_references = tuple(
            reference
            for reference in world.program.references
            if reference.provenance != Provenance.EXTERNAL_IMPORT
        )
        exact_internal = sum(
            reference.resolution == Resolution.EXACT
            for reference in internal_references
        )
        unknown_internal = sum(
            reference.resolution == Resolution.UNKNOWN
            for reference in internal_references
        )
        dynamic_internal = sum(
            reference.resolution == Resolution.DYNAMIC
            for reference in internal_references
        )
        package_count = len(
            {entity.module.split(".", 1)[0] for entity in world.program.entities}
        )
        context_bytes = _stable_json_bytes(capsule.to_dict())
        symbol_count = len(world.program.entities)
        private_packages = len(_python_packages_for_entities(world, affected_ids))

    return _PythonObservation(
        contracts=contracts,
        exact_references=exact_internal,
        internal_references=len(internal_references),
        total_references=coverage.total_references,
        unknown_references=unknown_internal,
        foreign_references=len(foreign_references),
        dynamic_references=dynamic_internal,
        symbol_count=symbol_count,
        package_count=package_count,
        context_bytes=context_bytes,
        private_affected_symbols=len(affected_ids),
        private_affected_packages=private_packages,
        rename=_run_python_change(fixture, "rename"),
        move=_run_python_change(fixture, "move"),
        change_signature=_run_python_change(fixture, "change_signature"),
    )


def _core_program_contracts(program: CoreProgram) -> tuple[str, ...]:
    contracts: list[str] = []
    payload = program.to_dict()
    for package in payload.get("packages", ()):
        package_name = str(package["name"])
        for module in package.get("modules", ()):
            module_name = str(module["name"])
            exports = {str(item) for item in module.get("exports", ())}
            for declaration in module.get("declarations", ()):
                name = str(declaration["name"])
                if not bool(declaration.get("export", name in exports)):
                    continue
                signature = str(declaration.get("signature", declaration.get("type", "Any")))
                contracts.append(f"{package_name}.{module_name}.{name}{signature}")
    return tuple(sorted(contracts))


def _successful(result: CoreChangeResult) -> bool:
    return not result.blocked and result.capability_violation is None


def _identity_preserved(
    before: CoreWorld,
    result: CoreChangeResult,
    symbol_id: str,
) -> bool:
    if not _successful(result):
        return False
    try:
        old_symbol = before.symbol(symbol_id)
        new_symbol = result.world.symbol(symbol_id)
    except KeyError:
        return False
    return new_symbol.id == old_symbol.id and new_symbol.revision_id != old_symbol.revision_id


def _measure_fixture(fixture: CoreBenchmarkFixture) -> FixtureMeasurement:
    python_observation = _observe_python(fixture)
    core_contracts = _core_program_contracts(fixture.program)
    world = compile_core(fixture.program)

    private_result = apply_core_change(
        world,
        CoreChange.change_implementation(
            fixture.private_symbol_id,
            "return value.strip().casefold()",
        ),
    )
    interface_result = apply_core_change(
        world,
        CoreChange.rename(fixture.public_symbol_id, "renamed_public"),
    )
    rename_result = apply_core_change(
        world,
        CoreChange.rename(fixture.public_symbol_id, "renamed_public"),
    )
    move_result = apply_core_change(
        world,
        CoreChange.move(
            fixture.public_symbol_id,
            fixture.destination_module,
            target_package=world.symbol(fixture.public_symbol_id).package_id,
        ),
    )
    signature_result = apply_core_change(
        world,
        CoreChange.change_signature(
            fixture.public_symbol_id,
            "(value: str, suffix: str = '') -> str",
        ),
    )

    context = world.context_for(fixture.public_symbol_id)
    python_arm = ArmMeasurement(
        arm="python_sidecar",
        public_contracts=python_observation.contracts,
        internal_exact_numerator=python_observation.exact_references,
        internal_reference_denominator=python_observation.internal_references,
        unknown_reference_count=python_observation.unknown_references,
        foreign_reference_count=python_observation.foreign_references,
        dynamic_reference_count=python_observation.dynamic_references,
        reference_denominator=python_observation.total_references,
        private_change_affected_symbols=python_observation.private_affected_symbols,
        private_change_symbol_denominator=python_observation.symbol_count,
        private_change_affected_packages=python_observation.private_affected_packages,
        private_change_package_denominator=python_observation.package_count,
        private_change_interface_changed_packages=0,
        interface_change_changed_packages=len(
            python_observation.rename.affected_packages
            if python_observation.rename.applied
            else ()
        ),
        interface_change_package_denominator=python_observation.package_count,
        serialized_context_bytes=python_observation.context_bytes,
        safe_rename_count=int(python_observation.rename.applied),
        rename_attempt_denominator=1,
        safe_move_count=int(python_observation.move.applied),
        move_attempt_denominator=1,
        safe_change_signature_count=int(python_observation.change_signature.applied),
        change_signature_attempt_denominator=1,
        identity_continuity_count=sum(
            (
                python_observation.rename.identity_preserved,
                python_observation.move.identity_preserved,
                python_observation.change_signature.identity_preserved,
            )
        ),
        identity_change_denominator=3,
    )
    core_arm = ArmMeasurement(
        arm="core_ir",
        public_contracts=core_contracts,
        dynamic_reference_count=0,
        internal_exact_numerator=world.exact_reference_count,
        internal_reference_denominator=(world.exact_reference_count + world.unknown_reference_count),
        unknown_reference_count=world.unknown_reference_count,
        foreign_reference_count=world.foreign_reference_count,
        reference_denominator=(
            world.exact_reference_count
            + world.unknown_reference_count
            + world.foreign_reference_count
        ),
        private_change_affected_symbols=len(private_result.affected_symbols),
        private_change_symbol_denominator=len(world.symbols),
        private_change_affected_packages=len(private_result.affected_packages),
        private_change_package_denominator=len(world.packages),
        private_change_interface_changed_packages=len(private_result.interface_changed_packages),
        interface_change_changed_packages=len(interface_result.interface_changed_packages),
        interface_change_package_denominator=len(world.packages),
        serialized_context_bytes=_stable_json_bytes(context),
        safe_rename_count=int(_successful(rename_result)),
        rename_attempt_denominator=1,
        safe_move_count=int(_successful(move_result)),
        move_attempt_denominator=1,
        safe_change_signature_count=int(_successful(signature_result)),
        change_signature_attempt_denominator=1,
        identity_continuity_count=sum(
            (
                _identity_preserved(world, rename_result, fixture.public_symbol_id),
                _identity_preserved(world, move_result, fixture.public_symbol_id),
                _identity_preserved(world, signature_result, fixture.public_symbol_id),
            )
        ),
        identity_change_denominator=3,
    )
    hypotheses = _fixture_hypotheses(fixture, python_arm, core_arm)
    return FixtureMeasurement(
        fixture_id=fixture.id,
        provenance=fixture.provenance,
        declared_public_contracts=fixture.declared_public_contracts,
        python=python_arm,
        core=core_arm,
        hypotheses=hypotheses,
    )


def _measured_hypothesis(
    name: str,
    passed: bool,
    *,
    rationale: str,
) -> HypothesisResult:
    return HypothesisResult(
        hypothesis=name,
        status=SUPPORTED if passed else NOT_SUPPORTED,
        numerator=int(passed),
        denominator=1,
        rationale=rationale,
    )


def _fixture_hypotheses(
    fixture: CoreBenchmarkFixture,
    python: ArmMeasurement,
    core: ArmMeasurement,
) -> tuple[HypothesisResult, ...]:
    declared = fixture.declared_public_contracts
    return (
        _measured_hypothesis(
            "declared_public_contract_equivalence",
            python.public_contracts == core.public_contracts == declared,
            rationale="Both encodings are compared with the generated fixture contract, not human ground truth.",
        ),
        _measured_hypothesis(
            "core_internal_resolution",
            core.internal_exact_numerator == core.internal_reference_denominator
            and core.unknown_reference_count == 0,
            rationale="All Core internal references must be Exact and Unknown must remain zero.",
        ),
        _measured_hypothesis(
            "private_change_is_interface_bounded",
            core.private_change_affected_packages < core.private_change_package_denominator
            and core.private_change_interface_changed_packages == 0,
            rationale="A private implementation edit must not alter a package interface or invalidate every package.",
        ),
        _measured_hypothesis(
            "interface_revision_propagates",
            0 < core.interface_change_changed_packages <= core.interface_change_package_denominator,
            rationale="A public rename must produce an explicit, package-bounded interface revision change.",
        ),
        _measured_hypothesis(
            "core_context_is_smaller",
            core.serialized_context_bytes < python.serialized_context_bytes,
            rationale="Stable compact JSON byte counts are compared without estimating tokens.",
        ),
        _measured_hypothesis(
            "core_safe_changes_exceed_python_sidecar",
            core.safe_change_count > python.safe_change_count,
            rationale="Only successfully applied isolated semantic changes count in either arm.",
        ),
        _measured_hypothesis(
            "core_identity_continuity_exceeds_python_sidecar",
            core.identity_continuity_count > python.identity_continuity_count,
            rationale="Core SymbolId continuity is compared with ChangeIR-preserved Python entity identity.",
        ),
    )


def _sum_arms(items: Iterable[ArmMeasurement], arm: str) -> ArmMeasurement:
    values = tuple(items)
    return ArmMeasurement(
        arm=arm,
        public_contracts=tuple(
            contract for item in values for contract in item.public_contracts
        ),
        internal_exact_numerator=sum(item.internal_exact_numerator for item in values),
        internal_reference_denominator=sum(item.internal_reference_denominator for item in values),
        unknown_reference_count=sum(item.unknown_reference_count for item in values),
        foreign_reference_count=sum(item.foreign_reference_count for item in values),
        dynamic_reference_count=sum(item.dynamic_reference_count for item in values),
        reference_denominator=sum(item.reference_denominator for item in values),
        private_change_affected_symbols=sum(item.private_change_affected_symbols for item in values),
        private_change_symbol_denominator=sum(item.private_change_symbol_denominator for item in values),
        private_change_affected_packages=sum(item.private_change_affected_packages for item in values),
        private_change_package_denominator=sum(item.private_change_package_denominator for item in values),
        private_change_interface_changed_packages=sum(item.private_change_interface_changed_packages for item in values),
        interface_change_changed_packages=sum(item.interface_change_changed_packages for item in values),
        interface_change_package_denominator=sum(item.interface_change_package_denominator for item in values),
        serialized_context_bytes=sum(item.serialized_context_bytes for item in values),
        safe_rename_count=sum(item.safe_rename_count for item in values),
        rename_attempt_denominator=sum(item.rename_attempt_denominator for item in values),
        safe_move_count=sum(item.safe_move_count for item in values),
        move_attempt_denominator=sum(item.move_attempt_denominator for item in values),
        safe_change_signature_count=sum(item.safe_change_signature_count for item in values),
        change_signature_attempt_denominator=sum(item.change_signature_attempt_denominator for item in values),
        identity_continuity_count=sum(item.identity_continuity_count for item in values),
        identity_change_denominator=sum(item.identity_change_denominator for item in values),
    )


def _aggregate_hypotheses(
    fixtures: tuple[FixtureMeasurement, ...],
    python: ArmMeasurement,
    core: ArmMeasurement,
) -> tuple[HypothesisResult, ...]:
    names = sorted(
        {hypothesis.hypothesis for fixture in fixtures for hypothesis in fixture.hypotheses}
    )
    results: list[HypothesisResult] = []
    for name in names:
        selected = tuple(
            hypothesis
            for fixture in fixtures
            for hypothesis in fixture.hypotheses
            if hypothesis.hypothesis == name
        )
        if name == "core_safe_changes_exceed_python_sidecar":
            denominator = core.safe_change_denominator
            if denominator == 0:
                results.append(
                    HypothesisResult(name, UNMEASURED, None, None, selected[0].rationale)
                )
            else:
                results.append(
                    HypothesisResult(
                        name,
                        (
                            SUPPORTED
                            if core.safe_change_count > python.safe_change_count
                            else NOT_SUPPORTED
                        ),
                        core.safe_change_count,
                        denominator,
                        selected[0].rationale,
                    )
                )
            continue
        if name == "core_identity_continuity_exceeds_python_sidecar":
            denominator = core.identity_change_denominator
            if denominator == 0:
                results.append(
                    HypothesisResult(name, UNMEASURED, None, None, selected[0].rationale)
                )
            else:
                results.append(
                    HypothesisResult(
                        name,
                        (
                            SUPPORTED
                            if core.identity_continuity_count
                            > python.identity_continuity_count
                            else NOT_SUPPORTED
                        ),
                        core.identity_continuity_count,
                        denominator,
                        selected[0].rationale,
                    )
                )
            continue
        denominator = sum(item.denominator or 0 for item in selected)
        if denominator == 0:
            results.append(
                HypothesisResult(
                    name,
                    UNMEASURED,
                    None,
                    None,
                    "No measured fixture denominator was available.",
                )
            )
            continue
        numerator = sum(item.numerator or 0 for item in selected)
        results.append(
            HypothesisResult(
                name,
                SUPPORTED if numerator == denominator else NOT_SUPPORTED,
                numerator,
                denominator,
                selected[0].rationale,
            )
        )
    results.append(
        HypothesisResult(
            "agent_assisted_task_success",
            UNMEASURED,
            None,
            None,
            "No agent provider was configured; success, token, and tool metrics are null.",
        )
    )
    return tuple(results)


def run_core_benchmark(
    fixtures: Iterable[CoreBenchmarkFixture] | None = None,
) -> CoreBenchmarkReport:
    selected = tuple(fixtures) if fixtures is not None else core_benchmark_fixtures()
    if len(selected) < 3:
        raise ValueError("the Core benchmark requires at least three fixture systems")
    fixture_ids = [item.id for item in selected]
    if len(fixture_ids) != len(set(fixture_ids)):
        raise ValueError("duplicate Core benchmark fixture id")
    measurements = tuple(_measure_fixture(item) for item in sorted(selected, key=lambda item: item.id))
    python = _sum_arms((item.python for item in measurements), "python_sidecar")
    core = _sum_arms((item.core for item in measurements), "core_ir")
    return CoreBenchmarkReport(
        fixtures=measurements,
        python=python,
        core=core,
        hypotheses=_aggregate_hypotheses(measurements, python, core),
    )


def core_benchmark_fixtures() -> tuple[CoreBenchmarkFixture, ...]:
    return tuple(
        _make_fixture(*values)
        for values in (
            ("text", "textkit", "textapp", "format_title", "render_title", True),
            ("price", "pricekit", "shopapp", "format_price", "render_price", False),
            ("route", "routekit", "webapp", "format_route", "render_route", False),
        )
    )


def _make_fixture(
    fixture_id: str,
    library: str,
    client: str,
    public_name: str,
    client_public_name: str,
    dynamic_lookup: bool,
) -> CoreBenchmarkFixture:
    private_id = f"symbol:{library}:api:_normalize"
    public_id = f"symbol:{library}:api:{public_name}"
    client_id = f"symbol:{client}:api:{client_public_name}"
    library_id = f"package:{library}"
    client_package_id = f"package:{client}"
    signature = "(value: str) -> str"
    files = (
        (f"{library}/_moved.py", ""),
        (
            f"{library}/api.py",
            f"import re\n\n"
            f"def _normalize(value: str) -> str:\n"
            f"    return re.sub(r'\\s+', ' ', value)\n\n"
            f"def {public_name}(value: str) -> str:\n"
            f"    return value\n",
        ),
        (
            f"{client}/api.py",
            (
                f"import {library}.api as public_module\n\n"
                f"def {client_public_name}(value: str) -> str:\n"
                f"    return getattr(public_module, {public_name!r})(value)\n"
                if dynamic_lookup
                else f"from {library}.api import {public_name}\n\n"
                f"def {client_public_name}(value: str) -> str:\n"
                f"    return {public_name}(value)\n"
            ),
        ),
    )
    program = CoreProgram.from_dict(
        {
            "packages": [
                {
                    "id": library_id,
                    "name": library,
                    "modules": [
                        {
                            "name": "_moved",
                            "imports": [],
                            "declarations": [],
                            "exports": [],
                        },
                        {
                            "name": "api",
                            "imports": [
                                {
                                    "foreign": "python:re",
                                    "name": "re",
                                    "alias": "re",
                                }
                            ],
                            "declarations": [
                                {
                                    "id": private_id,
                                    "name": "_normalize",
                                    "kind": "function",
                                    "signature": signature,
                                    "refs": ["re"],
                                    "implementation": "return re.sub(r'\\s+', ' ', value)",
                                    "effects": [],
                                    "capabilities": [],
                                    "export": False,
                                },
                                {
                                    "id": public_id,
                                    "name": public_name,
                                    "kind": "function",
                                    "signature": signature,
                                    "refs": [],
                                    "implementation": "return value",
                                    "effects": [],
                                    "capabilities": [],
                                    "export": True,
                                }
                            ],
                            "exports": [public_name],
                        },
                    ],
                },
                {
                    "id": client_package_id,
                    "name": client,
                    "modules": [
                        {
                            "name": "api",
                            "imports": [
                                {
                                    "package": library,
                                    "module": "api",
                                    "name": public_name,
                                    "alias": public_name,
                                }
                            ],
                            "declarations": [
                                {
                                    "id": client_id,
                                    "name": client_public_name,
                                    "kind": "function",
                                    "signature": signature,
                                    "refs": [public_name],
                                    "implementation": f"return {public_name}(value)",
                                    "effects": [],
                                    "capabilities": [],
                                    "export": True,
                                }
                            ],
                            "exports": [client_public_name],
                        }
                    ],
                },
            ]
        }
    )
    contracts = (
        f"{library}.api.{public_name}{signature}",
        f"{client}.api.{client_public_name}{signature}",
    )
    return CoreBenchmarkFixture(
        id=fixture_id,
        python_files=files,
        program=program,
        declared_public_contracts=contracts,
        private_symbol_id=private_id,
        public_symbol_id=public_id,
        destination_module="_moved",
        python_private_locator=f"{library}.api._normalize",
        python_public_locator=f"{library}.api.{public_name}",
        python_destination_module=f"{library}._moved",
        provenance=FixtureProvenance(
            fixture_id=fixture_id,
            source=(
                "generated:meldra.core_bench:fixed_getattr"
                if dynamic_lookup
                else "generated:meldra.core_bench:static_control"
            ),
        ),
    )
