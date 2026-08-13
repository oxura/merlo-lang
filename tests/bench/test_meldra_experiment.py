from merlo.experiment import run_hypothesis_experiment


def test_reproducible_hypothesis_experiment_is_honest():
    report = run_hypothesis_experiment()

    assert report.tasks >= 9
    assert report.baseline_false_safe_rate == 1.0
    assert report.meldra_false_safe_rate == 0.0
    assert report.meldra_edit_precision >= report.baseline_edit_precision
    assert report.unsafe_cases == 4
    assert report.baseline_false_safe_cases == 4
    assert report.meldra_false_safe_cases == 0
    assert report.baseline_predicted_edits > report.expected_edits
    assert report.meldra_matched_edits == report.meldra_predicted_edits
    assert report.meldra_predicted_edits == report.expected_edits
    assert report.full_source_bytes > 0
    assert report.task_capsule_bytes > 0
    assert any("unmeasured" in finding for finding in report.findings)
