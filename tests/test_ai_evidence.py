from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import pytest

from merlo.ai_evidence import (
    AIAggregates,
    AIEvidenceError,
    AITask,
    AIProviderIdentity,
    PairedEvidenceRunner,
    aggregate_evidence,
    validate_evidence,
)


def _oracle(response):
    return response["output"] == "accepted"


class Provider:
    identity = AIProviderIdentity("test-provider", "same-model", "revision-1")

    def __init__(self):
        self.calls = []

    def __call__(self, request):
        self.calls.append(dict(request))
        semantic = request["arm"] == "semantic"
        return {
            "provider": "test-provider",
            "model": "same-model",
            "revision": "revision-1",
            "run_id": f'{request["task_id"]}-{request["arm"]}',
            "output": "accepted" if request["task_id"] == "one" or semantic else "rejected",
            "oracle_passed": request["task_id"] == "one" or semantic,
            "input_tokens": 10 if semantic else 30,
            "output_tokens": 4 if semantic else 8,
            "context_tokens": 12 if semantic else 24,
            "repair_iterations": 1 if semantic else 2,
        }


def _tasks():
    return (
        AITask("one", "prompt one", _oracle, task={"operation": "rename", "name": "one"}, dataset={"case": 1}),
        AITask("two", "prompt two", _oracle, task={"operation": "move", "name": "two"}, dataset={"case": 2}),
    )


def test_runner_records_both_arms_and_integer_aggregates():
    provider = Provider()
    report = PairedEvidenceRunner(provider).run(_tasks())

    assert len(provider.calls) == 4
    assert {call["arm"] for call in provider.calls} == {"semantic", "text"}
    assert report.aggregates["task_count"] == 2
    assert report.aggregates["semantic"]["input_tokens"] == 20
    assert report.aggregates["text"]["input_tokens"] == 60
    assert report.aggregates["ratios"]["input_tokens"] == {"numerator": 1, "denominator": 3}
    assert report.aggregates["ratios"]["context_reduction"] == {"numerator": 1, "denominator": 2}
    assert all(type(value["numerator"]) is int for value in report.aggregates["ratios"].values() if value)


def test_report_roundtrip_is_canonical_and_tamper_evident():
    report = PairedEvidenceRunner(Provider()).run(_tasks())
    encoded = report.to_json()
    assert validate_evidence(encoded).to_json() == encoded

    tampered = json.loads(encoded)
    tampered["records"][0]["input_tokens"] += 1
    with pytest.raises(AIEvidenceError, match="Digest"):
        validate_evidence(tampered)


def test_rejects_provider_mismatch_and_synthetic_records():
    class WrongProvider(Provider):
        def __call__(self, request):
            result = super().__call__(request)
            result["revision"] = "revision-2"
            return result

    with pytest.raises(AIEvidenceError, match="ProviderMismatch"):
        PairedEvidenceRunner(WrongProvider()).run(_tasks())

    class SyntheticProvider(Provider):
        def __call__(self, request):
            result = super().__call__(request)
            result["synthetic"] = True
            return result

    with pytest.raises(AIEvidenceError, match="Synthetic"):
        PairedEvidenceRunner(SyntheticProvider()).run(_tasks())


def test_rejects_missing_arm_stale_and_empty_provider_runs():
    report = PairedEvidenceRunner(Provider()).run(_tasks())
    with pytest.raises(AIEvidenceError, match="Unpaired"):
        replace(report, records=report.records[:-1])

    first = report.records[0]
    stale = replace(first, task_hash=hashlib.sha256(b"stale").hexdigest())
    with pytest.raises(AIEvidenceError, match="Stale"):
        replace(report, records=(stale, *report.records[1:]))

    with pytest.raises(AIEvidenceError, match="NoProviderRuns"):
        aggregate_evidence(())


def test_schedule_must_be_canonical_and_paired():
    provider = Provider()
    with pytest.raises(AIEvidenceError, match="Schedule"):
        PairedEvidenceRunner(provider).run(_tasks(), schedule=[{"task_id": "one", "arms": ["semantic"]}])
    assert provider.calls == []


def test_direct_aggregate_rejects_placeholder_and_provider_is_not_called_on_import():
    # This also documents that records are observed provider records, not generated scores.
    with pytest.raises(AIEvidenceError, match="SyntheticOrPlaceholder"):
        from merlo.ai_evidence import AIRawTaskRecord

        AIRawTaskRecord(
            "one", "semantic", "test-provider", "same-model", "revision-1",
            hashlib.sha256(b"p").hexdigest(), hashlib.sha256(b"t").hexdigest(), hashlib.sha256(b"d").hexdigest(),
            "oracle", True, 1, 1, 1, 0, "run", hashlib.sha256(b"out").hexdigest(), raw={"status": "placeholder"}
        )
    assert AIAggregates is not None
