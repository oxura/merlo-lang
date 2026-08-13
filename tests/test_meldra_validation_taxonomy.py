from __future__ import annotations

import json

from merlo.external_bench import (
    BenchmarkBreakdown,
    ExternalBenchmarkReport,
    ExternalPlanResult,
    ValidationMetrics,
)
from merlo.validation_taxonomy import ValidationFailureAnalysis, analyze_validation_failures


def _result(
    task_id: str,
    *,
    allowed: bool | None,
    expected_safe: bool = True,
    reasons: tuple[str, ...] = (),
    operation: str = "rename",
    category: str = "library",
    error_kind: str | None = None,
    error_message: str | None = None,
) -> ExternalPlanResult:
    return ExternalPlanResult(
        task_id=task_id,
        project="fixture",
        category=category,
        operation=operation,
        target="fixture.target",
        expected_safe=expected_safe,
        label_source="generated_policy",
        oracle="fixture_declared",
        planner_allowed=allowed,
        outcome="infrastructure_error" if allowed is None else ("allowed" if allowed else "blocked"),
        blocked_reasons=reasons,
        error_kind=error_kind,
        error_message=error_message,
    )


def _report(results: tuple[ExternalPlanResult, ...]) -> ExternalBenchmarkReport:
    def breakdown(attribute: str) -> tuple[BenchmarkBreakdown, ...]:
        keys = sorted({getattr(item, attribute) for item in results})
        return tuple(
            BenchmarkBreakdown(
                key,
                ValidationMetrics.from_results(item for item in results if getattr(item, attribute) == key),
            )
            for key in keys
        )

    metrics = ValidationMetrics.from_results(results)
    return ExternalBenchmarkReport(
        seed=17,
        projects=(),
        metrics=metrics,
        results=tuple(sorted(results, key=lambda item: item.task_id)),
        held_out_entities=(),
        blocked_reason_frequency=(),
        category_breakdown=breakdown("category"),
        operation_breakdown=breakdown("operation"),
        label_source_breakdown=breakdown("label_source"),
    )


def _category_counts(analysis: ValidationFailureAnalysis) -> dict[str, int]:
    return {item.category: item.count for item in analysis.infrastructure.categories}


def test_multi_cause_tasks_are_counted_raw_but_once_in_primary_pareto() -> None:
    false_blocks = tuple(
        _result(
            f"unsupported-{index}",
            allowed=False,
            reasons=("PublicApiCompatibility", "UnsupportedBinding") if index == 0 else ("UnsupportedBinding",),
            operation="rename" if index < 6 else "move",
            category="library" if index < 5 else "service",
        )
        for index in range(8)
    ) + tuple(
        _result(
            f"public-{index}",
            allowed=False,
            reasons=("PublicApiCompatibility",),
            operation="move",
            category="service",
        )
        for index in range(2)
    )
    true_positives = (
        _result("rename-allowed", allowed=True, operation="rename", category="library"),
        _result("move-allowed", allowed=True, operation="move", category="service"),
    )

    analysis = analyze_validation_failures(_report(false_blocks + true_positives))

    assert [(item.cause, item.count) for item in analysis.raw_multi_cause_counts] == [
        ("UnsupportedBinding", 8),
        ("PublicApiCompatibility", 3),
    ]
    assert [(item.cause, item.count) for item in analysis.pareto] == [
        ("UnsupportedBinding", 8),
        ("PublicApiCompatibility", 2),
    ]
    assert sum(item.count for item in analysis.pareto) == analysis.false_block_numerator == 10
    assert analysis.pareto[0].cumulative_count == 8
    assert analysis.pareto[0].cumulative_ratio == 0.8
    assert analysis.pareto[1].cumulative_count == 10
    assert analysis.pareto[1].cumulative_ratio == 1.0
    assert analysis.false_block_denominator == 12
    assert analysis.false_block_ratio == 0.833333

    operations = {item.key: item for item in analysis.operation_breakdown}
    assert (operations["rename"].numerator, operations["rename"].denominator) == (6, 7)
    assert (operations["move"].numerator, operations["move"].denominator) == (4, 5)
    categories = {item.key: item for item in analysis.category_breakdown}
    assert (categories["library"].numerator, categories["library"].denominator) == (5, 6)
    assert (categories["service"].numerator, categories["service"].denominator) == (5, 6)


def test_known_53_failure_messages_classify_without_entering_confusion_denominators() -> None:
    timeout_message = (
        "SoftwareWorld.scan did not complete for sqlalchemy before cancellation; "
        "no task began after 1279.590s."
    )
    collision_shapes = (
        (9, '"ambiguous semantic entity \'ent_1c25070be8474271\': src.click._termui_impl.Editor.edit, src.click._termui_impl.Editor.edit, src.click._termui_impl.Editor.edit"'),
        (6, '"ambiguous semantic entity \'ent_925240c426832fea\': httpx._client.BaseClient.auth, httpx._client.BaseClient.auth"'),
        (6, '"ambiguous semantic entity \'ent_dd08092d9adc2c66\': src.pluggy._hooks.HookspecMarker.__call__, src.pluggy._hooks.HookspecMarker.__call__, src.pluggy._hooks.HookspecMarker.__call__"'),
        (4, '"ambiguous semantic entity \'ent_5818c6bac5fcf1a7\': httpx._client.BaseClient.params, httpx._client.BaseClient.params"'),
        (3, '"ambiguous semantic entity \'ent_52aa1a4d83ebde68\': aiohttp.tracing.TraceConfig.__init__, aiohttp.tracing.TraceConfig.__init__, aiohttp.tracing.TraceConfig.__init__"'),
    )
    infrastructure = tuple(
        _result(
            f"timeout-{index}",
            allowed=None,
            category="infrastructure",
            error_kind="ProjectScanTimeout",
            error_message=timeout_message,
        )
        for index in range(25)
    )
    infrastructure += tuple(
        _result(
            f"collision-{shape_index}-{index}",
            allowed=None,
            category="infrastructure",
            error_kind="KeyError",
            error_message=message,
        )
        for shape_index, (count, message) in enumerate(collision_shapes)
        for index in range(count)
    )
    evaluated = (
        _result("tp", allowed=True),
        _result("fn", allowed=False, reasons=("UnsupportedBinding",)),
        _result("tn", allowed=False, expected_safe=False, reasons=("PublicApiCompatibility",)),
        _result("fp", allowed=True, expected_safe=False),
    )
    report = _report(evaluated + infrastructure)
    before = report.metrics.to_dict()

    analysis = analyze_validation_failures(report)

    assert len(infrastructure) == 53
    assert _category_counts(analysis) == {
        "timeout": 25,
        "identity_collision": 28,
        "parser_frontend": 0,
        "checkout_archive": 0,
        "missing_dependency": 0,
        "test_harness": 0,
        "encoding": 0,
        "unknown": 0,
    }
    assert analysis.infrastructure.source_count == 53
    assert analysis.infrastructure.classified_count == 53
    assert analysis.metrics.to_dict() == before
    assert analysis.false_block_numerator == 1
    assert analysis.false_block_denominator == 2
    assert analysis.to_dict()["validation_metrics"]["confusion_matrix"] == before["confusion_matrix"]
    infrastructure_row = next(item for item in analysis.category_breakdown if item.key == "infrastructure")
    assert infrastructure_row.numerator == infrastructure_row.denominator == 0
    assert infrastructure_row.ratio is None


def test_infrastructure_categories_use_only_structured_error_fields_and_fall_back_to_unknown() -> None:
    failures = (
        _result("timeout", allowed=None, error_kind="TimeoutError", error_message="operation timed out"),
        _result("identity", allowed=None, error_kind="EntityCollision", error_message="duplicate declaration identity"),
        _result("parser", allowed=None, error_kind="SyntaxError", error_message="invalid syntax"),
        _result("checkout", allowed=None, error_kind="FileNotFoundError", error_message="project root is not a directory: /checkout"),
        _result("dependency", allowed=None, error_kind="ModuleNotFoundError", error_message="No module named 'optional'"),
        _result("harness", allowed=None, error_kind="CalledProcessError", error_message="pytest test command failed"),
        _result("encoding", allowed=None, error_kind="UnicodeDecodeError", error_message="utf-8 codec can't decode byte"),
        _result(
            "unknown",
            allowed=None,
            reasons=("ProjectScanTimeout",),
            error_kind="RuntimeError",
            error_message="opaque failure",
        ),
    )

    analysis = analyze_validation_failures(_report(failures))

    assert _category_counts(analysis) == {
        "timeout": 1,
        "identity_collision": 1,
        "parser_frontend": 1,
        "checkout_archive": 1,
        "missing_dependency": 1,
        "test_harness": 1,
        "encoding": 1,
        "unknown": 1,
    }
    assert analysis.false_block_denominator == 0
    assert analysis.false_block_ratio is None
    assert analysis.raw_multi_cause_counts == ()
    assert analysis.pareto == ()


def test_analysis_has_stable_sorted_json_round_trip() -> None:
    report = _report(
        (
            _result("z", allowed=False, reasons=("ZetaCause",)),
            _result("a", allowed=False, reasons=("AlphaCause",)),
            _result("infra", allowed=None, error_kind="RuntimeError", error_message="unclassified"),
        )
    )

    analysis = analyze_validation_failures(report)
    encoded = json.dumps(analysis.to_dict(), sort_keys=True, separators=(",", ":"))
    restored = ValidationFailureAnalysis.from_dict(json.loads(encoded))

    assert json.dumps(restored.to_dict(), sort_keys=True, separators=(",", ":")) == encoded
    assert [item.cause for item in analysis.pareto] == ["AlphaCause", "ZetaCause"]
