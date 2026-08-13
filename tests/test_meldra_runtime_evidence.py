from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

from merlo.analyzer import scan_python
from merlo.model import Evidence, EvidenceDependency, Resolution
from merlo.evidence_experiment import (
    CommandExperimentResult,
    CommandExperimentSpec,
    ExperimentStatus,
    run_command_experiment,
    simulate_evidence_invalidation,
)
from merlo.runtime_experiment import (
    ObservationStore,
    ObservedReference,
    UNSEEN_TARGET_WARNING,
    observe_runtime,
)


def _write_program(
    root: Path,
    body: str = "return value + 1",
    *,
    previous=None,
):
    (root / "sample.py").write_text(
        "def target(value):\n"
        f"    {body}\n\n"
        "def caller(value):\n"
        "    return target(value)\n",
        encoding="utf-8",
    )
    return scan_python(root, previous=previous)


def _call_reference(program):
    target = program.entity("sample.target")
    reference = next(
        item
        for item in program.references
        if item.target_id == target.id and item.owner_id is not None
    )
    return target, reference


def test_observation_merge_preserves_counts_and_provenance(tmp_path: Path):
    program = _write_program(tmp_path)
    target, reference = _call_reference(program)
    first = ObservedReference.capture(
        program,
        reference.id,
        {target.id: 2},
        callsite_id="trace-call-1",
        environments=("cpython-3.14",),
        trace_hash="trace-a",
        artifact_hash="artifact-a",
        observed_at="2026-08-09T10:00:00Z",
    )
    second = ObservedReference.capture(
        program,
        reference.id,
        {target.id: 3, "external:alternate": 1},
        callsite_id="trace-call-1",
        environments=("pypy-3.11",),
        trace_hash="trace-b",
        artifact_hash="artifact-b",
        observed_at="2026-08-10T10:00:00Z",
    )

    store = ObservationStore((first,)).merge(second)
    merged = store.query(reference_id=reference.id)[0]

    assert merged.count_for(target.id) == 5
    assert merged.count_for("external:alternate") == 1
    assert merged.call_count == 6
    assert merged.environments == ("cpython-3.14", "pypy-3.11")
    assert merged.trace_hashes == ("trace-a", "trace-b")
    assert merged.artifact_hashes == ("artifact-a", "artifact-b")
    assert merged.coverage == "observational"
    assert merged.resolution == "Observed"
    assert merged.unseen_targets_possible is True
    assert merged.unseen_target_warning == UNSEEN_TARGET_WARNING
    assert ObservationStore.from_json(store.to_json()) == store


def test_timestamp_is_excluded_from_observation_identity(tmp_path: Path):
    program = _write_program(tmp_path)
    target, reference = _call_reference(program)
    before = ObservedReference.capture(
        program,
        reference.id,
        {target.id: 1},
        trace_hash="same-trace",
        observed_at="2026-08-09T10:00:00Z",
    )
    after = replace(before, observed_at=("2026-08-10T10:00:00Z",))

    assert before.deterministic_id == after.deterministic_id
    assert before.to_dict()["observed_target_count"] == 1
    assert before.to_dict()["total_call_count"] == 1


def test_observation_becomes_stale_when_bound_revision_changes(tmp_path: Path):
    before = _write_program(tmp_path)
    target, reference = _call_reference(before)
    observation = ObservedReference.capture(
        before,
        reference.id,
        {target.id: 4},
        trace_hash="trace",
    )

    after = _write_program(tmp_path, body="return value + 2", previous=before)

    assert observation.is_stale(before) is False
    assert observation.is_stale(after) is True
    reasons = observation.stale_reasons(after)
    assert any("missing entity:" in reason for reason in reasons)
    assert any("changed reference:" in reason for reason in reasons)
    assert ObservationStore((observation,)).stale(after) == (observation,)
    assert ObservationStore((observation,)).query(
        program=after, include_stale=False
    ) == ()


def test_observation_does_not_upgrade_uncertain_static_resolution(tmp_path: Path):
    program = _write_program(tmp_path)
    target, reference = _call_reference(program)
    uncertain = replace(reference, target_id=None, resolution=Resolution.UNKNOWN)
    program = replace(
        program,
        references=tuple(
            uncertain if item.id == reference.id else item
            for item in program.references
        ),
    ).with_world_revision()

    observation = ObservedReference.capture(
        program, uncertain.id, {target.id: 9}, trace_hash="trace"
    )

    assert observation.resolution == "Observed"
    assert program.references_to(target.id) == ()
    assert next(
        item for item in program.references if item.id == uncertain.id
    ).resolution == Resolution.UNKNOWN
    assert observation.unseen_targets_possible is True


def test_real_profiler_observes_two_dynamic_getattr_targets(tmp_path: Path):
    source = (
        "class Handler:\n"
        "    def one(self):\n"
        "        return 1\n"
        "    def two(self):\n"
        "        return 2\n\n"
        "def invoke(name):\n"
        "    return getattr(Handler(), name)()\n"
    )
    path = tmp_path / "dynamic_fixture.py"
    path.write_text(source, encoding="utf-8")
    program = scan_python(tmp_path)
    spec = importlib.util.spec_from_file_location("dynamic_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with observe_runtime(program, environment="test-python") as observer:
        assert module.invoke("one") == 1
        assert module.invoke("two") == 2
        assert module.invoke("one") == 1

    one = program.entity("dynamic_fixture.Handler.one")
    two = program.entity("dynamic_fixture.Handler.two")
    matching = [
        item
        for item in observer.observations
        if {one.id, two.id}.issubset(item.observed_target_ids)
    ]

    assert len(matching) == 1
    assert matching[0].count_for(one.id) == 2
    assert matching[0].count_for(two.id) == 1
    assert matching[0].environments == ("test-python",)
    assert matching[0].coverage == "observational"
    assert matching[0].unseen_targets_possible is True
    assert matching[0].trace_hashes
    assert matching[0].artifact_hashes


def _experiment(
    tmp_path: Path,
    experiment_id: str,
    kind: str,
    code: str,
    dependencies=(),
):
    return CommandExperimentSpec.create(
        experiment_id,
        kind,
        (sys.executable, "-c", code),
        cwd=tmp_path,
        timeout=5,
        environment={"MELDRA_TEST_ENV": "stable"},
        dependencies=dependencies,
    )


def test_command_experiment_captures_deterministic_success_and_failure(
    tmp_path: Path,
):
    program = _write_program(tmp_path)
    target, _ = _call_reference(program)
    dependency = EvidenceDependency("entity", target.id, target.revision_hash)
    successful = _experiment(
        tmp_path,
        "unit-tests",
        "pytest",
        "print('stable output')",
        (dependency,),
    )
    first = run_command_experiment(
        successful, observed_at="2026-08-09T10:00:00Z"
    )
    second = run_command_experiment(
        successful, observed_at="2026-08-10T10:00:00Z"
    )

    assert first.status == ExperimentStatus.PASSED
    assert first.successful is True
    assert first.exit_code == 0
    assert first.stdout_bytes > 0
    assert first.artifact_hash == second.artifact_hash
    assert CommandExperimentResult.from_dict(first.to_dict()) == first
    assert successful.to_dict()["dependency_count"] == 1

    failed = run_command_experiment(
        _experiment(
            tmp_path,
            "type-check",
            "typecheck",
            "import sys; print('bad'); sys.exit(3)",
        )
    )

    assert failed.status == ExperimentStatus.FAILED
    assert failed.successful is False
    assert failed.exit_code == 3
    assert failed.infrastructure_error is None


def test_command_infrastructure_failure_is_data_not_success(tmp_path: Path):
    spec = CommandExperimentSpec.create(
        "benchmark",
        "benchmark",
        (str(tmp_path / "command-that-does-not-exist"),),
        cwd=tmp_path,
        timeout=1,
    )
    result = run_command_experiment(spec)

    assert result.status == ExperimentStatus.ERROR
    assert result.successful is False
    assert result.exit_code is None
    assert result.infrastructure_error


def test_invalidation_simulation_is_minimal_and_never_preserves_rerun_evidence(
    tmp_path: Path,
):
    program = _write_program(tmp_path)
    target, _ = _call_reference(program)
    entity_dependency = EvidenceDependency(
        "entity", target.id, target.revision_hash
    )
    relation_dependency = EvidenceDependency(
        "relation_set", target.id, program.reference_set_hash(target.id)
    )
    world_dependency = EvidenceDependency(
        "world", "ProgramIR", program.world_revision
    )
    experiments = (
        _experiment(tmp_path, "bench", "benchmark", "pass", (world_dependency,)),
        _experiment(tmp_path, "types", "typecheck", "pass", (relation_dependency,)),
        _experiment(tmp_path, "tests", "pytest", "pass", (entity_dependency,)),
    )
    provider_owned = Evidence(
        id="e-provider",
        kind="pytest",
        level="observed",
        statement="old result",
        produced_by="tests",
        dependencies=(),
    )
    dependency_owned = Evidence(
        id="e-dependency",
        kind="manual",
        level="observed",
        statement="old result",
        dependencies=(entity_dependency,),
    )
    unrelated = Evidence(
        id="e-unrelated",
        kind="manual",
        level="observed",
        statement="unrelated",
        dependencies=(EvidenceDependency("entity", "other", "revision"),),
    )

    simulation = simulate_evidence_invalidation(
        experiments,
        (unrelated, provider_owned, dependency_owned),
        changed_dependencies=(("entity", target.id),),
    )

    assert simulation.rerun_experiment_ids == ("tests",)
    assert simulation.invalidated_evidence_ids == ("e-dependency", "e-provider")
    assert simulation.preserved_evidence_ids == ("e-unrelated",)
    report = simulation.to_dict()
    assert report["rerun_experiment_count"] == 1
    assert report["invalidated_evidence_count"] == 2
    assert report["preserved_evidence_count"] == 1
    assert report["input_evidence_count"] == 3
