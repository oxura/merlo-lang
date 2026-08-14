from __future__ import annotations

from research.archive.alpha1.merlo.runtime_soundness import (
    RUNTIME_SOUNDNESS_CATEGORIES,
    RUNTIME_SOUNDNESS_REPETITIONS,
    RuntimeCallsiteObservation,
    StaticPrediction,
    generate_runtime_soundness_fixtures,
    run_runtime_soundness_benchmark,
)


def _runtime(events):
    return tuple(
        (("events", tuple((tuple(sorted(event.items())) for event in group))),)
        for group in events
    )


def test_runtime_soundness_corpus_covers_every_preregistered_category():
    fixtures = generate_runtime_soundness_fixtures()

    assert len(fixtures) == 23
    assert tuple(item.category for item in fixtures) == RUNTIME_SOUNDNESS_CATEGORIES
    assert len({item.id for item in fixtures}) == len(fixtures)
    assert all(item.repetitions == RUNTIME_SOUNDNESS_REPETITIONS for item in fixtures)
    assert all(item.call_line > 0 and item.call_spelling for item in fixtures)


def test_unsound_exact_requires_an_exact_prediction_and_unexpected_target():
    unknown = RuntimeCallsiteObservation(
        "unknown",
        "adversarial",
        StaticPrediction("arm", "Unknown", None, None, "static"),
        "pkg.original",
        _runtime((( {"target": "pkg.replacement"},),)),
        (),
        "test",
    )
    sound = RuntimeCallsiteObservation(
        "sound",
        "adversarial",
        StaticPrediction("arm", "Exact", "pkg.original", "sym", "static"),
        "pkg.original",
        _runtime((( {"target": "pkg.original"},),)),
        (),
        "test",
    )
    unsound = RuntimeCallsiteObservation(
        "unsound",
        "adversarial",
        StaticPrediction("arm", "Exact", "pkg.original", "sym", "static"),
        "pkg.original",
        _runtime((( {"target": "pkg.replacement"},),)),
        (),
        "test",
    )

    assert unknown.sound_exact is None
    assert unknown.unsound_exact_count == 0
    assert sound.sound_exact is True
    assert sound.unsound_exact_count == 0
    assert unsound.sound_exact is False
    assert unsound.unsound_exact_count == 1


def test_generated_runtime_pilot_observes_real_code_targets_without_claiming_external_evidence():
    report = run_runtime_soundness_benchmark()
    payload = report.to_dict()

    assert report.current_python.static_exact_callsites == 7
    assert report.current_python.unsound_exact_count == 140
    assert report.strong_python_diagnostic.static_exact_callsites == 23
    assert report.strong_python_diagnostic.unsound_exact_count == 460
    assert report.maximal_python.static_exact_callsites == 0
    assert report.maximal_python.unsound_exact_count == 0
    assert report.maximal_python.to_dict()["rejected_callsites"] == 23
    assert report.meldra.static_exact_callsites == 23
    assert report.meldra.unsound_exact_count == 0
    assert payload["statistical_units"] == {
        "generated_callsites": 23,
        "runtime_observations": 460,
        "independent_programs": 0,
        "independent_authors": 0,
        "template_families": 23,
        "primary_external_gate_status": "UNMEASURED",
        "note": "Repeated runtime calls exercise the harness but are not independent samples.",
    }
    assert payload["evidence_level"] == "GENERATED_PILOT_NOT_EXTERNAL_EVIDENCE"
    assert payload["decision"] == "NO_GO_LANGUAGE_ALPHA"
    assert set(payload["arms"]) == {
        "current-python-sidecar",
        "maximal-python-profile",
        "meldra-closed",
    }
    assert set(payload["diagnostic_baselines"]) == {
        "strong-python-binder"
    }

    first = report.strong_python_diagnostic.observations[0]
    event = dict(dict(first.runtime_observations[0])["events"][0])
    assert event["target"] == "main.replacement"
    assert event["qualname"] == "replacement"
    assert len(event["code_sha256"]) == 64
    assert dict(first.environment)["isolated_mode"] is True
