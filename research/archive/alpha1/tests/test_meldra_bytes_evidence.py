from __future__ import annotations

from collections import Counter

from research.archive.alpha1.merlo.bytes_evidence_closure import (
    _PROGRAM_SOURCES,
    _collect_automatic_drop,
    _collect_zero_copy,
    _expected_valid,
    _invalid_cases,
    _valid_cases,
)
from research.archive.alpha1.merlo.native_differential import evaluate_surface


def test_closure_corpora_meet_preregistered_family_and_uniqueness_gates():
    valid = _valid_cases()
    invalid = _invalid_cases()

    assert len(valid) == 540
    assert len(invalid) == 312
    assert len(Counter(item.family for item in valid)) == 15
    assert len(Counter(item.family for item in invalid)) == 13
    assert set(Counter(item.family for item in valid)) == {
        "zero_length",
        "one_byte",
        "small_buffer",
        "page_boundary_sizes",
        "large_runtime_sized_buffer",
        "full_view",
        "empty_view",
        "prefix_view",
        "suffix_view",
        "middle_view",
        "sequential_views",
        "owner_mutation_after_view",
        "owner_move_after_borrow",
        "automatic_scope_drop",
        "runtime_valid_boundaries",
    }
    assert set(Counter(item.family for item in invalid)) == {
        "use_after_move",
        "double_drop",
        "owner_mutation_live_view",
        "owner_move_live_view",
        "owner_drop_live_view",
        "mutation_immutable_view",
        "escaping_view",
        "view_outlives_owner",
        "index_oob",
        "slice_start_oob",
        "slice_length_oob",
        "start_length_overflow",
        "allocation_size_overflow",
    }
    assert len({(item.family, item.seed, item.arguments) for item in valid}) == len(valid)
    assert len(
        {
            (item.family, item.seed, item.arguments, item.source)
            for item in invalid
        }
    ) == len(invalid)


def test_each_valid_program_template_has_surface_reference_agreement():
    representative = {}
    for case in _valid_cases():
        representative.setdefault(case.template, case)

    assert set(representative) == set(_PROGRAM_SOURCES)
    for template, case in representative.items():
        observed = evaluate_surface(
            _PROGRAM_SOURCES[template],
            case.arguments,
            path=f"closure-smoke/{template}.meldra",
        )
        assert observed.status == "OK"
        assert observed.return_value == _expected_valid(case)
        assert observed.final_ownership_state == (("Dropped", 1),)


def test_zero_copy_probe_and_deliberate_copy_control(tmp_path):
    evidence, _compile_ms = _collect_zero_copy(tmp_path)

    assert evidence["passed"] is True
    assert evidence["runtime_relation"]["address_delta"] == 7
    assert evidence["runtime_relation"]["alloc_before"] == evidence["runtime_relation"]["alloc_after"] == 1
    assert evidence["runtime_relation"]["free_before"] == evidence["runtime_relation"]["free_after"] == 0
    assert evidence["native_counters"]["payload_copies"] == 0
    assert evidence["assembly"]["copy_loop_absent"] is True
    assert evidence["falsification_control"]["detected"] is True


def test_automatic_and_explicit_drop_are_distinguished(tmp_path):
    evidence, _compile_ms = _collect_automatic_drop(tmp_path)

    assert evidence["passed"] is True
    assert evidence["compiler_inserted"]["surface_contains_drop_call"] is False
    assert evidence["explicit_early_drop"]["surface_contains_drop_call"] is True
    assert evidence["compiler_inserted"]["native_counters"]["allocations"] == 1
    assert evidence["compiler_inserted"]["native_counters"]["frees"] == 1
    assert evidence["explicit_early_drop"]["native_counters"]["allocations"] == 1
    assert evidence["explicit_early_drop"]["native_counters"]["frees"] == 1
    assert evidence["automatic_lsan"]["returncode"] == 0
