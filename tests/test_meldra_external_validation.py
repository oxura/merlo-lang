from __future__ import annotations

import json
import sys
from dataclasses import replace

from merlo.coverage import SemanticCoverageReport
from merlo.external_bench import (
    ExternalApplyTestResult,
    ExternalAcceptanceProbe,
    ExternalBenchmarkManifest,
    ExternalBenchmarkReport,
    ExternalProject,
    ExternalTaskSpec,
    generate_pilot_manifest,
    load_manifest,
    run_apply_and_test_validation,
    run_external_benchmark,
    save_manifest,
)
from merlo.model import Resolution
from merlo.world import SoftwareWorld


def _external_projects(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "api.py").write_text(
        "def target(value=1):\n    return value\n\ndef other():\n    return target()\n",
        encoding="utf-8",
    )
    (second / "core.py").write_text(
        "def target(value=1):\n    return value\n\ndef caller():\n    return target()\n",
        encoding="utf-8",
    )
    return first, second


def _task(identifier, project, target, expected_safe, *, strict=False):
    return ExternalTaskSpec(
        id=identifier,
        project=project,
        operation="rename",
        target=target,
        payload=f"renamed_{identifier.replace('-', '_')}",
        expected_safe=expected_safe,
        label_source="policy" if strict else "fixture",
        oracle="policy_strict_public_rename" if strict else "fixture_declared",
        allow_public_api_break=not strict,
    )


def test_semantic_coverage_has_raw_resolution_and_operation_denominators(tmp_path):
    first, _ = _external_projects(tmp_path)
    program = SoftwareWorld.scan(first).program
    reference = program.references[0]
    call = program.calls[0]
    resolutions = (
        Resolution.EXACT,
        Resolution.DERIVED,
        Resolution.CONDITIONAL,
        "Observed",
        Resolution.DYNAMIC,
        Resolution.UNKNOWN,
    )
    measured = replace(
        program,
        references=tuple(
            replace(
                reference,
                id=str(index),
                resolution=value,
                usage="CallCallee",
            )
            for index, value in enumerate(resolutions)
        ),
        calls=tuple(replace(call, id=str(index), resolution=value) for index, value in enumerate(resolutions)),
    )

    report = SemanticCoverageReport.from_program(measured)
    payload = report.to_dict()

    assert payload["resolution_counts"] == dict(zip(resolutions, (1, 1, 1, 1, 1, 1)))
    assert payload["exact"] == {"count": 1, "denominator": 6, "ratio": 0.166667}
    assert payload["usable"] == {"count": 3, "denominator": 6, "ratio": 0.5}
    assert report.for_operation("rename").denominator == 6
    assert report.for_operation("rename").numerator == 2
    assert report.for_operation("move").denominator == 6
    assert report.for_operation("change_signature").denominator == 6
    assert SemanticCoverageReport.from_dict(payload).to_dict() == payload


def test_external_benchmark_confusion_breakdowns_and_stable_json(tmp_path):
    first, second = _external_projects(tmp_path)
    projects = (
        ExternalProject("alpha", str(first), "library", held_out_count=1),
        ExternalProject("beta", str(second), "service", held_out_count=1),
    )
    tasks = (
        _task("a-tp", "alpha", "api.target", True),
        _task("a-fp", "alpha", "api.target", False),
        _task("a-fn", "alpha", "api.target", True, strict=True),
        _task("a-tn", "alpha", "api.target", False, strict=True),
        _task("a-error", "alpha", "api.missing", True),
        _task("b-tp", "beta", "core.target", True),
        _task("b-fp", "beta", "core.target", False),
        _task("b-fn", "beta", "core.target", True, strict=True),
        _task("b-tn", "beta", "core.target", False, strict=True),
    )
    manifest = ExternalBenchmarkManifest(projects, tasks, seed=41)
    manifest_path = tmp_path / "manifest.json"
    save_manifest(manifest, manifest_path)

    loaded = load_manifest(manifest_path)
    report = run_external_benchmark(loaded)
    matrix = report.metrics.to_dict()

    assert loaded == manifest
    assert matrix["confusion_matrix"] == {
        "true_positive": 2,
        "false_positive": 2,
        "false_negative": 2,
        "true_negative": 2,
    }
    assert matrix["total_tasks"] == 9
    assert matrix["evaluated_tasks"] == 8
    assert matrix["infrastructure_errors"] == 1
    assert matrix["expected_safe_tasks"] == 5
    assert matrix["false_safe"] == {"numerator": 2, "denominator": 4, "rate": 0.5}
    assert matrix["false_block"] == {"numerator": 2, "denominator": 4, "rate": 0.5}
    assert matrix["safe_automation"] == {"numerator": 2, "denominator": 4, "coverage": 0.5}
    assert report.for_category("library").to_dict()["confusion_matrix"] == {
        "true_positive": 1,
        "false_positive": 1,
        "false_negative": 1,
        "true_negative": 1,
    }
    assert report.for_category("service").total_tasks == 4
    assert report.for_operation("rename").total_tasks == 9
    assert report.for_label_source("policy").total_tasks == 4
    assert len(report.held_out_entities) == 2
    assert report.blocked_reason_frequency == (("PublicApiCompatibility", 4),)
    failed = next(item for item in report.results if item.task_id == "a-error")
    assert failed.outcome == "infrastructure_error"
    assert failed.planner_allowed is None
    assert failed.error_kind == "KeyError"

    encoded = json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":"))
    restored = ExternalBenchmarkReport.from_dict(json.loads(encoded))
    assert json.dumps(restored.to_dict(), sort_keys=True, separators=(",", ":")) == encoded
    save_manifest(loaded, tmp_path / "manifest-copy.json")
    assert (tmp_path / "manifest-copy.json").read_bytes() == manifest_path.read_bytes()


def test_pilot_generator_enforces_policy_quotas_without_claiming_human_labels(tmp_path):
    first, second = _external_projects(tmp_path)
    projects = (
        ExternalProject("alpha", str(first), "library"),
        ExternalProject("beta", str(second), "service"),
    )

    manifest = generate_pilot_manifest(projects, seed=73)
    repeated = generate_pilot_manifest(projects, seed=73)

    assert manifest.to_dict() == repeated.to_dict()
    assert len(manifest.tasks) == 50
    for project in projects:
        selected = [task for task in manifest.tasks if task.project == project.id]
        assert sum(task.expected_safe for task in selected) == 10
        assert sum(not task.expected_safe for task in selected) == 15
        assert {task.label_source for task in selected} == {"policy"}
        assert all("human" not in task.oracle.lower() for task in selected)



def test_balanced_safe_generator_preflights_all_operations(tmp_path):
    root = tmp_path / "balanced"
    root.mkdir()
    (root / "source.py").write_text(
        "from typing import overload\n\n"
        "@overload\n"
        "def overloaded(value: int) -> int: ...\n\n"
        "@overload\n"
        "def overloaded(value: str) -> str: ...\n\n"
        "def overloaded(value):\n"
        "    return value\n\n"
        "def movable(value=1):\n"
        "    return value\n\n"
        "def test_not_candidate():\n"
        "    return 3\n\n"
        "class Container:\n"
        "    def unsupported_method(self):\n"
        "        return 1\n",
        encoding="utf-8",
    )
    (root / "target.py").write_text(
        "def destination_helper():\n"
        "    return 2\n",
        encoding="utf-8",
    )
    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_fixture.py").write_text(
        "def test_from_path():\n"
        "    return 4\n",
        encoding="utf-8",
    )
    (root / "testing.py").write_text(
        "def fixture_helper():\n"
        "    return 5\n",
        encoding="utf-8",
    )
    (root / "conftest.py").write_text(
        "def configure_fixture():\n"
        "    return 6\n",
        encoding="utf-8",
    )
    (root / "service_tests.py").write_text(
        "def production_lookalike():\n"
        "    return 7\n",
        encoding="utf-8",
    )
    docs_dir = root / "docs"
    docs_dir.mkdir()
    (docs_dir / "sample.py").write_text(
        "def documented_example():\n"
        "    return 8\n",
        encoding="utf-8",
    )
    examples_dir = root / "examples"
    examples_dir.mkdir()
    (examples_dir / "sample.py").write_text(
        "def runnable_example():\n"
        "    return 9\n",
        encoding="utf-8",
    )
    tutorial_dir = root / "docs_src" / "tutorial"
    tutorial_dir.mkdir(parents=True)
    (tutorial_dir / "guide.py").write_text(
        "def tutorial_example():\n"
        "    return 10\n",
        encoding="utf-8",
    )
    project = ExternalProject("balanced", str(root), "fixture")

    manifest = generate_pilot_manifest(
        (project,),
        seed=19,
        safe_per_project=6,
        unsafe_per_project=0,
        balanced_safe=True,
    )
    report = run_external_benchmark(manifest)
    scanned = SoftwareWorld.scan(
        root, state_path=tmp_path / "balanced-inspection.json"
    )
    unsupported = scanned.program.entity(
        "source.Container.unsupported_method"
    ).id
    test_entities = {
        scanned.program.entity("source.test_not_candidate").id,
        scanned.program.entity("tests.test_fixture.test_from_path").id,
        scanned.program.entity("testing.fixture_helper").id,
        scanned.program.entity("conftest.configure_fixture").id,
        scanned.program.entity("service_tests.production_lookalike").id,
        scanned.program.entity("docs.sample.documented_example").id,
        scanned.program.entity("examples.sample.runnable_example").id,
        scanned.program.entity(
            "docs_src.tutorial.guide.tutorial_example"
        ).id,
    }
    overloads = tuple(
        item
        for item in scanned.program.entities
        if item.fqname == "source.overloaded"
    )
    assert len(overloads) == 3
    assert len({item.id for item in overloads}) == 1
    ambiguous_id = overloads[0].id

    assert {
        operation: sum(task.operation == operation for task in manifest.tasks)
        for operation in ("rename", "move", "change_signature")
    } == {"rename": 2, "move": 2, "change_signature": 2}
    assert all(task.expected_safe for task in manifest.tasks)
    assert {
        task.label_source for task in manifest.tasks
    } == {"validated_fixture"}
    assert {
        task.oracle for task in manifest.tasks
    } == {"planner_preflight_candidate"}
    assert unsupported not in {
        task.target for task in manifest.tasks if task.operation == "rename"
    }
    assert test_entities.isdisjoint({task.target for task in manifest.tasks})
    assert ambiguous_id not in {task.target for task in manifest.tasks}
    assert all(result.planner_allowed is True for result in report.results)
    assert all(
        task.payload in {"source", "target"}
        for task in manifest.tasks
        if task.operation == "move"
    )
    assert all(
        "meldra_optional_" in task.payload and "=None" in task.payload
        for task in manifest.tasks
        if task.operation == "change_signature"
    )


def test_apply_and_test_validation_isolated_success_and_round_trip(tmp_path):
    root = tmp_path / "apply-success"
    root.mkdir()
    source = (
        "def target(value=1):\n"
        "    return value\n\n"
        "def caller():\n"
        "    return target()\n"
    )
    (root / "api.py").write_text(source, encoding="utf-8")
    world = SoftwareWorld.scan(
        root, state_path=tmp_path / "apply-success-world.json"
    )
    project = ExternalProject("apply-success", str(root), "fixture")
    task = ExternalTaskSpec.safe(
        id="apply-success:rename",
        project=project.id,
        operation="rename",
        target="api.target",
        payload="renamed_target",
        label_source="validated_fixture",
        oracle="fixture_assertion",
    )

    result = run_apply_and_test_validation(
        project,
        task,
        (
            (
                sys.executable,
                "-c",
                "from api import renamed_target; assert renamed_target() == 1",
            ),
        ),
        expected_world_revision=world.program.world_revision,
        timeout=2,
    )

    assert result.plan_ready is True
    assert result.apply_attempted is True
    assert result.materialized is True
    assert result.world_revision_after != world.program.world_revision
    assert result.tests_successful is True
    assert result.commands[0].returncode == 0
    assert result.source_unchanged is True
    assert result.restoration_succeeded is True
    assert result.temporary_workspace_removed is True
    assert (root / "api.py").read_text(encoding="utf-8") == source
    payload = result.to_dict()
    assert ExternalApplyTestResult.from_dict(payload).to_dict() == payload


def test_apply_acceptance_probes_preserve_collection_api_behavior_and_counts(
    tmp_path,
):
    root = tmp_path / "guarded-apply"
    root.mkdir()
    source = (
        "def _target(value=1):\n"
        "    return value\n\n"
        "def caller():\n"
        "    return _target()\n"
    )
    (root / "api.py").write_text(source, encoding="utf-8")
    world = SoftwareWorld.scan(
        root, state_path=tmp_path / "guarded-world.json"
    )
    project = ExternalProject("guarded", str(root), "fixture")
    task = ExternalTaskSpec.safe(
        id="guarded:rename",
        project=project.id,
        operation="rename",
        target="api._target",
        payload="_renamed_target",
        label_source="human_review",
        oracle="declared_private_contract",
    )
    probes = (
        ExternalAcceptanceProbe.create(
            "collected_node_ids",
            (
                sys.executable,
                "-c",
                "print('tests/test_api.py::test_behavior')",
            ),
            kind="pytest_collection",
        ),
        ExternalAcceptanceProbe.create(
            "passed_count",
            (sys.executable, "-c", "print('1 passed in 0.01s')"),
            kind="pytest_passed_count",
        ),
        ExternalAcceptanceProbe.create(
            "public_api",
            (
                sys.executable,
                "-c",
                "import ast,json; tree=ast.parse(open('api.py').read()); "
                "print(json.dumps(sorted(n.name for n in tree.body "
                "if isinstance(n,(ast.FunctionDef,ast.ClassDef)) "
                "and not n.name.startswith('_'))))",
            ),
            kind="json",
        ),
        ExternalAcceptanceProbe.create(
            "selected_behavior",
            (
                sys.executable,
                "-c",
                "import api,json; print(json.dumps({'value':api.caller()},sort_keys=True))",
            ),
            kind="json",
        ),
    )

    result = run_apply_and_test_validation(
        project,
        task,
        ((sys.executable, "-c", "import api; assert api.caller() == 1"),),
        expected_world_revision=world.program.world_revision,
        acceptance_probes=probes,
        timeout=2,
    )

    assert result.materialized is True
    assert result.acceptance_successful is True
    assert result.behaviorally_accepted is True
    assert [item.matched for item in result.acceptance] == [
        True,
        True,
        True,
        True,
    ]
    assert result.acceptance[0].baseline_value == {
        "count": 1,
        "node_ids": ["tests/test_api.py::test_behavior"],
    }
    assert result.acceptance[1].baseline_value == 1
    assert ExternalApplyTestResult.from_dict(result.to_dict()) == result


def test_acceptance_probe_detects_zero_exit_contract_regression(tmp_path):
    root = tmp_path / "guard-regression"
    root.mkdir()
    (root / "api.py").write_text(
        "def target():\n    return 1\n", encoding="utf-8"
    )
    world = SoftwareWorld.scan(
        root, state_path=tmp_path / "guard-regression-world.json"
    )
    project = ExternalProject("guard-regression", str(root), "fixture")
    task = ExternalTaskSpec.safe(
        id="guard-regression:rename",
        project=project.id,
        operation="rename",
        target="api.target",
        payload="renamed_target",
        label_source="fixture",
        oracle="intentional_regression",
    )
    public_api = ExternalAcceptanceProbe.create(
        "public_api",
        (
            sys.executable,
            "-c",
            "import ast,json; print(json.dumps([n.name for n in "
            "ast.parse(open('api.py').read()).body "
            "if isinstance(n,ast.FunctionDef)]))",
        ),
        kind="json",
    )

    result = run_apply_and_test_validation(
        project,
        task,
        ((sys.executable, "-c", "raise SystemExit(0)"),),
        expected_world_revision=world.program.world_revision,
        acceptance_probes=(public_api,),
        timeout=2,
    )

    assert result.tests_successful is True
    assert result.acceptance_successful is False
    assert result.behaviorally_accepted is False
    assert result.acceptance[0].baseline_value == ["target"]
    assert result.acceptance[0].changed_value == ["renamed_target"]
    assert result.acceptance[0].matched is False


def test_apply_validation_never_materializes_blocked_or_unsafe_tasks(tmp_path):
    root = tmp_path / "non-application"
    root.mkdir()
    source_path = root / "api.py"
    source_path.write_text(
        "def target(value=1):\n    return value\n", encoding="utf-8"
    )
    world = SoftwareWorld.scan(
        root, state_path=tmp_path / "non-application-world.json"
    )
    project = ExternalProject("non-application", str(root), "fixture")
    blocked = ExternalTaskSpec.safe(
        id="blocked",
        project=project.id,
        operation="rename",
        target="api.target",
        payload="renamed",
        label_source="validated_fixture",
        oracle="fixture_assertion",
        allow_public_api_break=False,
    )
    unsafe = ExternalTaskSpec.unsafe(
        id="unsafe",
        project=project.id,
        operation="rename",
        target="api.target",
        payload="renamed",
        label_source="validated_fixture",
        oracle="fixture_assertion",
    )
    marker_command = (
        sys.executable,
        "-c",
        "from pathlib import Path; Path('should-not-exist').touch()",
    )

    blocked_result = run_apply_and_test_validation(
        project,
        blocked,
        (marker_command,),
        expected_world_revision=world.program.world_revision,
    )
    unsafe_result = run_apply_and_test_validation(
        project,
        unsafe,
        (marker_command,),
        expected_world_revision=world.program.world_revision,
    )
    stale_result = run_apply_and_test_validation(
        project,
        blocked,
        (marker_command,),
        expected_world_revision="stale-world-revision",
    )

    assert blocked_result.plan_ready is False
    assert blocked_result.materialization_eligible is False
    assert blocked_result.apply_attempted is False
    assert blocked_result.commands == ()
    assert unsafe_result.plan_ready is True
    assert unsafe_result.materialization_eligible is False
    assert unsafe_result.apply_attempted is False
    assert unsafe_result.commands == ()
    assert stale_result.plan_ready is None
    assert stale_result.apply_attempted is False
    assert stale_result.apply_error_kind == "WorldRevisionMismatch"
    assert stale_result.commands == ()
    assert source_path.read_text(encoding="utf-8").startswith("def target")
    assert blocked_result.to_dict()["tests"]["timeouts"]["rate"] is None


def test_apply_validation_reports_timeout_and_infrastructure_denominators(
    tmp_path,
):
    root = tmp_path / "command-errors"
    root.mkdir()
    (root / "api.py").write_text(
        "def target():\n    return 1\n", encoding="utf-8"
    )
    world = SoftwareWorld.scan(
        root, state_path=tmp_path / "command-errors-world.json"
    )
    project = ExternalProject("command-errors", str(root), "fixture")
    task = ExternalTaskSpec.safe(
        id="command-errors:rename",
        project=project.id,
        operation="rename",
        target="api.target",
        payload="renamed",
        label_source="validated_fixture",
        oracle="fixture_assertion",
    )

    result = run_apply_and_test_validation(
        project,
        task,
        (
            (sys.executable, "-c", "import time; time.sleep(1)"),
            (str(tmp_path / "missing-executable"),),
        ),
        expected_world_revision=world.program.world_revision,
        timeout=0.01,
    )
    tests = result.to_dict()["tests"]

    assert result.materialized is True
    assert result.tests_successful is False
    assert tests["timeouts"] == {
        "numerator": 1,
        "denominator": 2,
        "rate": 0.5,
    }
    assert tests["infrastructure_errors"] == {
        "numerator": 2,
        "denominator": 2,
        "rate": 1.0,
    }
    assert len(result.infrastructure_errors) == 2
    assert result.source_unchanged is True