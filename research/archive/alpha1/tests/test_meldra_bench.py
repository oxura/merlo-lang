from research.archive.alpha1.merlo.bench import run_stage02_bench


def test_stage02_semantic_benchmark_gate():
    report = run_stage02_bench()

    assert report.evolution_cases >= 16
    assert report.identity_cases >= 5
    assert report.false_safe_rate == 0.0
    assert report.transaction_safety == 1.0
    assert report.edit_precision >= 0.95
    assert report.edit_recall >= 0.95
    assert report.obligation_precision >= 0.95
    assert report.obligation_recall >= 0.95
    assert report.identity_precision == 1.0
    assert report.identity_recall == 1.0
    assert report.unsafe_cases == 8
    assert report.false_safe_cases == 0
    assert report.matched_edits == report.predicted_edits == report.expected_edits
    assert (
        report.matched_obligations
        == report.predicted_obligations
        == report.expected_obligations
    )
    assert report.transaction_safe_cases == report.evolution_cases
