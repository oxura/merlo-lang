from __future__ import annotations

from itertools import permutations

from research.archive.historical_protocol.merlo.changes import ChangeDescriptor
from research.archive.alpha1.merlo.composition_experiment import (
    CompositionExperimentReport,
    measure_composition_set,
    run_composition_experiment,
)


def _change(kind: str, target: str, **payload: object) -> ChangeDescriptor:
    return ChangeDescriptor.create(kind, target, **payload)


def test_disjoint_changes_measure_one_theoretical_parallel_wave():
    measurement = measure_composition_set(
        (
            _change("Rename", "entity:a", new_name="renamed_a"),
            _change("Move", "entity:b", target_module="destination"),
        )
    )

    assert measurement.change_count == 2
    assert measurement.pair_count == 1
    assert measurement.commuting_pair_count == 1
    assert measurement.conflicting_pair_count == 0
    assert measurement.theoretical_wave_widths == (2,)
    assert measurement.theoretical_throughput == 2.0


def test_conflicting_changes_measure_serial_theoretical_waves():
    measurement = measure_composition_set(
        (
            _change("Rename", "entity:shared", new_name="first"),
            _change("Rename", "entity:shared", new_name="second"),
        )
    )

    assert measurement.pair_count == 1
    assert measurement.commuting_pair_count == 0
    assert measurement.conflicting_pair_count == 1
    assert measurement.non_commuting_pair_count == 1
    assert measurement.theoretical_wave_widths == (1, 1)


def test_measurement_is_deterministic_under_input_permutation():
    changes = (
        _change("Rename", "entity:a", new_name="new_a"),
        _change("Move", "entity:a", target_module="destination"),
        _change("Refine", "entity:b", new_signature="(value)"),
    )

    measurements = [
        measure_composition_set(candidate).to_dict()
        for candidate in permutations(changes)
    ]

    assert all(item == measurements[0] for item in measurements[1:])


def test_report_exposes_raw_rate_denominators_and_measured_blocked_reasons():
    report = run_composition_experiment(
        (
            (
                _change("Rename", "entity:a", new_name="new_a"),
                _change("Move", "entity:b", target_module="destination"),
            ),
            (
                _change("Rename", "entity:shared", new_name="first"),
                _change("Rename", "entity:shared", new_name="second"),
            ),
        ),
        blocked_reason_frequency={
            "dynamic_reference": 7,
            "public_boundary": 2,
            "unobserved": 0,
        },
    )

    payload = report.to_dict()
    assert payload["simulation"] is True
    assert payload["execution_performed"] is False
    assert "theoretical" in payload["method"]
    assert payload["commute_rate"] == {
        "numerator_commuting_pairs": 1,
        "denominator_pairs": 2,
        "value": 0.5,
    }
    assert payload["conflict_rate"] == {
        "numerator_conflicting_pairs": 1,
        "denominator_pairs": 2,
        "value": 0.5,
    }
    assert payload["theoretical_throughput"] == {
        "numerator_changes": 4,
        "denominator_waves": 3,
        "value": 1.333333,
    }
    assert payload["counts"]["blocked_reasons"] == 9
    assert payload["blocked_reason_frequency"] == [
        {"blocked_reason": "dynamic_reference", "count": 7},
        {"blocked_reason": "public_boundary", "count": 2},
    ]
    assert CompositionExperimentReport.from_dict(payload).to_dict() == payload


def test_blocked_reason_observations_can_be_supplied_as_raw_events():
    report = run_composition_experiment(
        (),
        blocked_reason_frequency=("uncertain", "uncertain", "external"),
    )

    assert report.blocked_reason_frequency == (("external", 1), ("uncertain", 2))
    assert report.to_dict()["theoretical_throughput"] == {
        "numerator_changes": 0,
        "denominator_waves": 0,
        "value": 0.0,
    }
