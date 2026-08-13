from __future__ import annotations

import json
import sys
from pathlib import Path

from merlo.agent_trial import (
    MEASURED,
    UNMEASURED,
    AgentTrialHarness,
    AgentTrialReport,
    FakeProvider,
    OpenAICompatibleProvider,
    ProviderIdentity,
    ProviderRequest,
    ProviderResponse,
    ReplayProvider,
    TaskManifest,
    ToolCall,
    TrialBudget,
    TrialResult,
    aggregate_results,
)


def _manifest(
    root: Path,
    *,
    task_id: str = "change-value",
    expected_files: tuple[str, ...] = ("app.py",),
    sequence_id: str | None = None,
    sequence_index: int = 0,
    expected_value: int = 1,
) -> TaskManifest:
    return TaskManifest(
        task_id=task_id,
        repo="example/repo",
        root=str(root),
        prompt=f"Change value to {expected_value}",
        expected_files=expected_files,
        expected_contracts=("value-updated",),
        test_argv=(
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                f"assert Path('app.py').read_text() == 'value = {expected_value}\\n'"
            ),
        ),
        budget=TrialBudget(
            wall_time_seconds=10,
            input_tokens=100,
            output_tokens=50,
            tool_calls=10,
            iterations=5,
        ),
        sequence_id=sequence_id,
        sequence_index=sequence_index,
    )


def _successful_script(request: ProviderRequest) -> ProviderResponse:
    expected = int(request.manifest.prompt.rsplit(" ", 1)[-1])
    previous = expected - 1
    if request.iteration == 1:
        edit = {"path": "app.py", "old": f"value = {previous}\n", "new": f"value = {expected}\n"}
        if request.manifest.task_id == "with-unintended":
            if request.arm == "baseline":
                return ProviderResponse(
                    tool_calls=(
                        ToolCall("edit", edit),
                        ToolCall("edit", {"path": "notes.txt", "old": "", "new": "extra\n"}),
                    ),
                    input_tokens=7,
                    output_tokens=3,
                    wall_time_ms=2,
                )
            return ProviderResponse(
                tool_calls=(ToolCall("change", {"edits": [edit, {"path": "notes.txt", "old": "", "new": "extra\n"}]}),),
                input_tokens=7,
                output_tokens=3,
                wall_time_ms=2,
            )
        tool = ToolCall("edit", edit) if request.arm == "baseline" else ToolCall("change", {"edits": [edit]})
        return ProviderResponse(
            tool_calls=(tool,), input_tokens=7, output_tokens=3, wall_time_ms=2
        )
    verification = ToolCall("test") if request.arm == "baseline" else ToolCall("evidence", {"run_tests": True})
    return ProviderResponse(
        tool_calls=(verification,),
        final={
            "success": True,
            "safe": True,
            "contracts": {"value-updated": True},
            "human_interventions": 0,
        },
        input_tokens=5,
        output_tokens=2,
        wall_time_ms=3,
    )


def test_same_provider_identity_and_budgets_are_used_for_both_arms(tmp_path: Path):
    (tmp_path / "app.py").write_text("value = 0\n", encoding="utf-8")
    manifest = _manifest(tmp_path)
    provider = FakeProvider(_successful_script, model="one-model", revision="fixed")

    report = AgentTrialHarness(provider).run((manifest,))

    assert len(report.results) == 2
    assert {result.provider for result in report.results} == {provider.identity}
    assert {result.budget for result in report.results} == {manifest.budget}
    assert {call.identity for call in provider.calls} == {provider.identity}
    assert {call.budget for call in provider.calls} == {manifest.budget}
    assert {call.arm for call in provider.calls} == {"baseline", "meldra"}
    assert all(result.task_success for result in report.results)
    assert all(result.iterations == 2 for result in report.results)
    assert all(result.first_pass for result in report.results)


def test_aggregation_has_raw_denominators_medians_and_round_trips(tmp_path: Path):
    identity = ProviderIdentity("fake", "same")
    budget = TrialBudget()
    results = (
        TrialResult(
            "a",
            "baseline",
            identity,
            budget,
            MEASURED,
            True,
            True,
            2,
            10,
            4,
            1,
            (),
            False,
            False,
            0,
            20,
            None,
            0,
            (),
        ),
        TrialResult(
            "b",
            "baseline",
            identity,
            budget,
            MEASURED,
            False,
            False,
            4,
            30,
            8,
            3,
            ("extra.py",),
            True,
            False,
            2,
            40,
            None,
            0,
            (),
        ),
        TrialResult(
            "c",
            "baseline",
            identity,
            budget,
            UNMEASURED,
            False,
            False,
            0,
            0,
            0,
            0,
            (),
            False,
            False,
            0,
            0,
            None,
            0,
            (),
        ),
    )

    aggregate = aggregate_results(results, "baseline")

    assert aggregate.total_tasks == 3
    assert aggregate.measured_tasks == 2
    assert aggregate.unmeasured_tasks == 1
    assert aggregate.successful_tasks == 1
    assert aggregate.task_success_denominator == 2
    assert aggregate.task_success_rate == 0.5
    assert aggregate.first_pass_tasks == 1
    assert aggregate.first_pass_denominator == 2
    assert aggregate.false_safe_tasks == 1
    assert aggregate.false_safe_denominator == 2
    assert aggregate.median_tool_calls == 3
    assert aggregate.median_input_tokens == 20
    assert aggregate.median_iterations == 2
    assert aggregate.median_unintended_edits == 0.5

    empty_other = aggregate_results((), "meldra")
    report = AgentTrialReport(identity, results, aggregate, empty_other)
    assert AgentTrialReport.from_dict(json.loads(json.dumps(report.to_dict()))) == report


def test_first_pass_is_false_after_failed_verification_and_iterations_are_counted(
    tmp_path: Path,
):
    (tmp_path / "app.py").write_text("value = 0\n", encoding="utf-8")
    manifest = _manifest(tmp_path)

    def script(request: ProviderRequest) -> ProviderResponse:
        verify = ToolCall("test") if request.arm == "baseline" else ToolCall("evidence", {"run_tests": True})
        if request.iteration == 1:
            return ProviderResponse(tool_calls=(verify,))
        if request.iteration == 2:
            edit = {"path": "app.py", "old": "value = 0\n", "new": "value = 1\n"}
            tool = ToolCall("edit", edit) if request.arm == "baseline" else ToolCall("change", {"edits": [edit]})
            return ProviderResponse(tool_calls=(tool,))
        return ProviderResponse(
            tool_calls=(verify,),
            final={
                "success": True,
                "safe": True,
                "contracts": {"value-updated": True},
            },
        )

    report = AgentTrialHarness(FakeProvider(script)).run((manifest,))

    assert all(result.task_success for result in report.results)
    assert all(result.iterations == 3 for result in report.results)
    assert all(not result.first_pass for result in report.results)
    assert report.baseline.first_pass_tasks == 0
    assert report.meldra.first_pass_tasks == 0


def test_unintended_files_are_reported_not_hidden(tmp_path: Path):
    (tmp_path / "app.py").write_text("value = 0\n", encoding="utf-8")
    manifest = _manifest(tmp_path, task_id="with-unintended")

    report = AgentTrialHarness(FakeProvider(_successful_script)).run((manifest,))

    assert all(result.task_success for result in report.results)
    assert all(result.unintended_files == ("notes.txt",) for result in report.results)
    assert report.baseline.median_unintended_edits == 1
    assert report.meldra.median_unintended_edits == 1


def test_missing_openai_key_is_unmeasured_without_synthetic_score(tmp_path: Path):
    (tmp_path / "app.py").write_text("value = 0\n", encoding="utf-8")
    provider = OpenAICompatibleProvider(
        endpoint="https://example.invalid/v1/chat/completions",
        model="accounts/fireworks/models/example",
        api_key=None,
    )

    report = AgentTrialHarness(provider).run((_manifest(tmp_path),))

    assert all(result.status == UNMEASURED for result in report.results)
    assert all(not result.task_success for result in report.results)
    assert all(result.iterations == 0 for result in report.results)
    assert report.baseline.task_success_denominator == 0
    assert report.baseline.task_success_rate is None
    assert report.meldra.task_success_denominator == 0
    assert report.meldra.task_success_rate is None


def test_replay_provider_is_deterministic_and_persistent(tmp_path: Path):
    manifest = _manifest(tmp_path)
    response = ProviderResponse(
        tool_calls=(ToolCall("source", {"path": "app.py"}),),
        input_tokens=11,
        output_tokens=2,
        wall_time_ms=7,
    )
    provider = ReplayProvider(
        {"baseline": (response,), "meldra": (response,)},
        identity=ProviderIdentity("fireworks", "fixed-model", revision="r1"),
    )
    request = ProviderRequest(
        manifest,
        "baseline",
        1,
        provider.identity,
        manifest.budget,
        ("source",),
        (),
    )

    assert provider.complete(request) == provider.complete(request)
    restored = ReplayProvider.from_dict(json.loads(json.dumps(provider.to_dict())))
    assert restored.identity == provider.identity
    assert restored.complete(request) == response
    assert json.dumps(restored.to_dict(), sort_keys=True) == json.dumps(
        provider.to_dict(), sort_keys=True
    )


def test_long_horizon_sequence_succeeds_only_when_every_step_succeeds(tmp_path: Path):
    (tmp_path / "app.py").write_text("value = 0\n", encoding="utf-8")
    first = _manifest(
        tmp_path,
        task_id="step-1",
        sequence_id="migration",
        sequence_index=0,
        expected_value=1,
    )
    second = _manifest(
        tmp_path,
        task_id="step-2",
        sequence_id="migration",
        sequence_index=1,
        expected_value=2,
    )

    report = AgentTrialHarness(FakeProvider(_successful_script)).run((second, first))

    assert report.baseline.long_horizon_sequences == 1
    assert report.baseline.successful_long_horizon_sequences == 1
    assert report.baseline.long_horizon_denominator == 1
    assert report.baseline.long_horizon_success_rate == 1.0
    assert report.meldra.long_horizon_sequences == 1
    assert report.meldra.successful_long_horizon_sequences == 1
    assert report.meldra.long_horizon_success_rate == 1.0
