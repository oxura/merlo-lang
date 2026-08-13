from __future__ import annotations

from merlo.benchmark_integrity import (
    CLUSTER_BOOTSTRAP_REPETITIONS,
    CLUSTER_BOOTSTRAP_SEED,
    cluster_bootstrap_interval,
    measure_expressiveness_and_burden,
    run_benchmark_integrity_report,
    zero_failure_upper_bound,
)


def test_cluster_bootstrap_is_seeded_and_clusters_before_resampling():
    clusters = {
        "program-a": (1, 2),
        "program-b": (2, 2),
        "program-c": (0, 2),
    }

    first = cluster_bootstrap_interval("success", "arm", clusters)
    second = cluster_bootstrap_interval("success", "arm", clusters)

    assert first == second
    assert first.estimate == 0.5
    assert first.clusters == 3
    assert first.numerator == 3
    assert first.denominator == 6
    assert first.repetitions == CLUSTER_BOOTSTRAP_REPETITIONS == 10_000
    assert first.seed == CLUSTER_BOOTSTRAP_SEED == 20260810
    assert first.lower_95 <= first.estimate <= first.upper_95


def test_zero_failure_bound_is_exact_one_sided_rule():
    assert zero_failure_upper_bound(300) == 0.009936
    assert zero_failure_upper_bound(460) == 0.006491


def test_legacy_evolution_and_language_semantics_are_partitioned():
    payload = run_benchmark_integrity_report().to_dict()
    partition = payload["provenance_partition"]
    legacy = partition["legacy_python_evolution"]
    language = partition["new_language_semantics"]

    assert legacy["external_projects"] == 20
    assert legacy["external_references"] == 391227
    assert legacy["language_semantics_claim"] == "PROHIBITED"
    assert len(legacy["artifacts"]) == 4
    assert len(language["artifacts"]) == 6
    assert language["external_meldra_programs"] == 0
    assert language["external_author_count"] == 0
    assert language["primary_external_gate_status"] == "UNMEASURED"
    assert not ({item["path"] for item in legacy["artifacts"]} & {
        item["path"] for item in language["artifacts"]
    })


def test_integrity_report_groups_every_required_analysis_level():
    payload = run_benchmark_integrity_report().to_dict()
    grouping = payload["grouping"]

    assert len(grouping["per_program"]) == 91
    assert len(grouping["per_construct_family"]) == 5
    assert len(grouping["per_template"]) == 129
    assert len(grouping["per_external_author"]) == 15
    assert all(
        item["meldra_programs"] == 0
        and item["meldra_claim_status"] == "UNMEASURED"
        for item in grouping["per_external_author"]
    )
    assert {item["construct_family"] for item in grouping["per_construct_family"]} == {
        "python-semantic-evolution",
        "runtime-binding",
        "interfaces-and-revisions",
        "typed-effects-and-context",
        "scoped-capabilities",
    }


def test_clustered_intervals_use_program_or_template_denominators():
    intervals = run_benchmark_integrity_report().to_dict()[
        "clustered_confidence_intervals"
    ]
    by_key = {(item["metric"], item["arm"]): item for item in intervals}

    assert len(intervals) == 13
    assert by_key[("locality_exact_rate", "meldra-closed")]["clusters"] == 12
    assert by_key[("locality_exact_rate", "current-python-sidecar")][
        "estimate"
    ] == 0.666667
    assert by_key[("capability_detection_recall", "meldra-closed")][
        "clusters"
    ] == 24
    assert by_key[("capability_detection_recall", "meldra-closed")][
        "estimate"
    ] == 0.8
    assert by_key[("runtime_non_unsound_static_claim_rate", "meldra-closed")][
        "clusters"
    ] == 23
    assert by_key[("legacy_python_usable_reference_rate", "current-python-sidecar")][
        "clusters"
    ] == 20


def test_generated_expressiveness_and_burden_are_measured_not_overclaimed():
    result = measure_expressiveness_and_burden(40)

    assert result["programs"] == 40
    assert result["fully_expressible_programs"] == 40
    assert result["fully_expressible_program_rate"] == 1.0
    assert result["foreign_escape_programs"] == 0
    assert result["foreign_escape_frequency"] == 0.0
    assert result["meldra_source_bytes"] == 78710
    assert result["python_source_bytes"] == 70470
    assert result["meldra_source_overhead_ratio"] == 0.116929
    assert result["meldra_token_overhead_ratio"] == 0.133028
    assert result["meldra_annotation_sites"] == 1920
    assert result["python_annotation_sites"] == 1880
    assert result["strict_python_sidecar_manifest_burden"] == (
        "UNMEASURED_ON_THIS_CORPUS"
    )
    assert result["evidence_level"] == "GENERATED_SUPPORT_PROFILE_PILOT"
    assert result["primary_external_gate_status"] == "UNMEASURED"


def test_integrity_report_keeps_language_alpha_closed():
    payload = run_benchmark_integrity_report().to_dict()

    assert payload["decision"] == "NO_GO_LANGUAGE_ALPHA"
    assert any(
        "not evidence about Meldra" in item for item in payload["limitations"]
    )
    assert any(
        "cannot satisfy the external expressiveness gate" in item
        for item in payload["limitations"]
    )
