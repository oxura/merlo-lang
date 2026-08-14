from __future__ import annotations

import json

from research.archive.alpha1.merlo.core_bench import (
    NOT_SUPPORTED,
    SUPPORTED,
    UNMEASURED,
    CoreBenchmarkReport,
    core_benchmark_fixtures,
    run_core_benchmark,
)
from merlo.cli import main


def _fixture(report: CoreBenchmarkReport, fixture_id: str):
    return next(item for item in report.fixtures if item.fixture_id == fixture_id)


def _fixture_hypothesis(report: CoreBenchmarkReport, fixture_id: str, name: str):
    return next(
        item
        for item in _fixture(report, fixture_id).hypotheses
        if item.hypothesis == name
    )


def test_sidecar_and_core_fixtures_have_equivalent_declared_public_contracts():
    fixtures = core_benchmark_fixtures()
    report = run_core_benchmark(fixtures)

    assert len(fixtures) == len(report.fixtures) == 3
    for result in report.fixtures:
        assert result.python.arm == "python_sidecar"
        assert result.python.public_contracts == result.declared_public_contracts
        assert result.core.public_contracts == result.declared_public_contracts
        assert result.provenance.label_source == "benchmark_policy"
        assert result.provenance.oracle == "declared_public_contract"
        assert result.provenance.human_ground_truth is False
        assert (
            next(
                item
                for item in result.hypotheses
                if item.hypothesis == "declared_public_contract_equivalence"
            ).status
            == SUPPORTED
        )


def test_core_closes_internal_bindings_and_fixed_getattr_remains_dynamic_in_sidecar():
    report = run_core_benchmark()
    dynamic = _fixture(report, "text")
    controls = (_fixture(report, "price"), _fixture(report, "route"))

    for result in report.fixtures:
        assert (
            result.core.internal_exact_numerator
            == result.core.internal_reference_denominator
            == 1
        )
        assert result.core.unknown_reference_count == 0
        assert result.core.dynamic_reference_count == 0
        assert result.core.foreign_reference_count == 1
        assert result.core.reference_denominator == 2

    assert dynamic.provenance.source.endswith(":fixed_getattr")
    assert dynamic.python.dynamic_reference_count == 1
    assert dynamic.python.internal_exact_numerator == 0
    assert dynamic.python.internal_reference_denominator == 1
    assert dynamic.python.unknown_reference_count == 0
    assert dynamic.python.foreign_reference_count == 1
    assert dynamic.python.reference_denominator == 2
    assert all(item.provenance.source.endswith(":static_control") for item in controls)
    assert all(item.python.dynamic_reference_count == 0 for item in controls)
    assert all(item.python.internal_exact_numerator == 2 for item in controls)
    assert all(item.python.internal_reference_denominator == 2 for item in controls)
    assert all(item.python.unknown_reference_count == 0 for item in controls)
    assert all(item.python.foreign_reference_count == 1 for item in controls)
    assert all(item.python.reference_denominator == 3 for item in controls)

    assert report.core.internal_exact_numerator == 3
    assert report.core.internal_reference_denominator == 3
    assert report.core.unknown_reference_count == 0
    assert report.core.foreign_reference_count == 3
    assert report.core.reference_denominator == 6
    assert report.python.internal_exact_numerator == 4
    assert report.python.internal_reference_denominator == 5
    assert report.python.unknown_reference_count == 0
    assert report.python.dynamic_reference_count == 1
    assert report.python.foreign_reference_count == 3
    assert report.python.reference_denominator == 8
    assert report.hypothesis("core_internal_resolution").status == SUPPORTED


def test_private_impact_and_public_interface_propagation_keep_raw_package_bounds():
    report = run_core_benchmark()

    for result in report.fixtures:
        assert (
            result.python.private_change_affected_symbols,
            result.python.private_change_symbol_denominator,
        ) == (1, 3)
        assert (
            result.core.private_change_affected_symbols,
            result.core.private_change_symbol_denominator,
        ) == (1, 3)
        assert result.python.private_change_affected_packages == 1
        assert result.core.private_change_affected_packages == 1
        assert result.python.private_change_package_denominator == 2
        assert result.core.private_change_package_denominator == 2
        assert result.python.private_change_interface_changed_packages == 0
        assert result.core.private_change_interface_changed_packages == 0
        assert result.core.interface_change_changed_packages == 1
        assert result.core.interface_change_package_denominator == 2

    assert report.core.private_change_affected_packages == 3
    assert report.core.private_change_package_denominator == 6
    assert report.core.private_change_interface_changed_packages == 0
    assert report.core.interface_change_changed_packages == 3
    assert report.core.interface_change_package_denominator == 6
    assert report.hypothesis("private_change_is_interface_bounded").status == SUPPORTED
    assert report.hypothesis("interface_revision_propagates").status == SUPPORTED


def test_real_isolated_changeir_controls_tie_core_and_dynamic_lookup_blocks_sidecar():
    report = run_core_benchmark()
    comparison = "core_safe_changes_exceed_python_sidecar"
    identity = "core_identity_continuity_exceeds_python_sidecar"

    for fixture_id in ("price", "route"):
        result = _fixture(report, fixture_id)
        assert result.python.safe_rename_count == 1
        assert result.python.safe_move_count == 1
        assert result.python.safe_change_signature_count == 1
        assert result.python.identity_continuity_count == 3
        assert result.core.safe_change_count == result.python.safe_change_count == 3
        assert result.core.identity_continuity_count == 3
        assert _fixture_hypothesis(report, fixture_id, comparison).status == NOT_SUPPORTED
        assert _fixture_hypothesis(report, fixture_id, identity).status == NOT_SUPPORTED

    dynamic = _fixture(report, "text")
    assert dynamic.python.safe_rename_count == 0
    assert dynamic.python.safe_move_count == 0
    assert dynamic.python.safe_change_signature_count == 0
    assert dynamic.python.identity_continuity_count == 0
    assert dynamic.core.safe_change_count == 3
    assert dynamic.core.identity_continuity_count == 3
    assert _fixture_hypothesis(report, "text", comparison).status == SUPPORTED
    assert _fixture_hypothesis(report, "text", identity).status == SUPPORTED

    assert report.python.safe_rename_count == 2
    assert report.python.safe_move_count == 2
    assert report.python.safe_change_signature_count == 2
    assert report.python.safe_change_denominator == 9
    assert report.python.identity_continuity_count == 6
    assert report.python.identity_change_denominator == 9
    assert report.core.safe_change_count == report.core.safe_change_denominator == 9
    assert report.core.identity_continuity_count == report.core.identity_change_denominator == 9
    assert report.hypothesis(comparison).status == SUPPORTED
    assert report.hypothesis(identity).status == SUPPORTED


def test_serialized_context_bytes_are_measured_without_token_estimates():
    report = run_core_benchmark()

    assert report.python.serialized_context_bytes > 0
    assert report.core.serialized_context_bytes > 0
    context_hypothesis = report.hypothesis("core_context_is_smaller")
    assert context_hypothesis.status in (SUPPORTED, NOT_SUPPORTED)
    assert context_hypothesis.numerator in range(4)
    assert context_hypothesis.denominator == 3
    assert all(
        result.python.serialized_context_bytes > 0
        and result.core.serialized_context_bytes > 0
        for result in report.fixtures
    )


def test_agent_success_token_and_tool_metrics_remain_null_without_a_provider():
    report = run_core_benchmark()
    payload = report.to_dict()

    assert report.agent.status == UNMEASURED
    assert report.agent.provider is None
    assert report.agent.successful_tasks is None
    assert report.agent.task_success_denominator is None
    assert report.agent.input_tokens is None
    assert report.agent.output_tokens is None
    assert report.agent.tool_calls is None
    assert payload["agent"] == {
        "status": "UNMEASURED",
        "provider": None,
        "successful_tasks": None,
        "task_success_denominator": None,
        "input_tokens": None,
        "output_tokens": None,
        "tool_calls": None,
    }
    agent_hypothesis = report.hypothesis("agent_assisted_task_success")
    assert agent_hypothesis.status == UNMEASURED
    assert agent_hypothesis.numerator is None
    assert agent_hypothesis.denominator is None


def test_report_is_deterministic_stable_json_and_round_trips():
    first = run_core_benchmark()
    second = run_core_benchmark()

    assert first.to_dict() == second.to_dict()
    restored = CoreBenchmarkReport.from_dict(first.to_dict())
    assert restored == first
    assert restored.to_dict() == first.to_dict()
    assert json.dumps(
        restored.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ) == json.dumps(
        first.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def test_core_benchmark_is_runnable_from_cli(capsys):
    assert main(["historical", "core-bench", "--compact"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert (
        payload["core"]["safe_rename_count"]
        + payload["core"]["safe_move_count"]
        + payload["core"]["safe_change_signature_count"]
    ) == 9
    assert (
        payload["python"]["safe_rename_count"]
        + payload["python"]["safe_move_count"]
        + payload["python"]["safe_change_signature_count"]
    ) == 6
    assert payload["agent"]["status"] == UNMEASURED
