from __future__ import annotations

from merlo.productive_falsification import (
    PRODUCTIVE_FALSIFICATION_CONTRACT,
    PRODUCTIVE_MUTANTS,
    run_productive_falsification,
)


def test_productive_falsification_detects_exactly_the_named_mutants():
    report = run_productive_falsification()

    assert report["contract"] == PRODUCTIVE_FALSIFICATION_CONTRACT
    assert report["check_count"] == 12
    assert tuple(report["checks"]) == PRODUCTIVE_MUTANTS
    assert all(
        set(check) == {"detected", "evidence"}
        and check["detected"] is True
        and check["evidence"]
        for check in report["checks"].values()
    )
    assert report["passed"] is True

    evidence = {
        name: check["evidence"] for name, check in report["checks"].items()
    }
    assert evidence["map_growth_loses_entry"]["actual_entries"] == evidence[
        "map_growth_loses_entry"
    ]["expected_entries"]
    assert evidence["collision_updates_wrong_key"]["actual_entries"] == evidence[
        "collision_updates_wrong_key"
    ]["expected_entries"]
    assert evidence["map_drop_skips_text_key"] == {
        "owned_before_close": 3,
        "owned_after_close": 0,
        "released_after_close": 3,
    }
    assert evidence["file_close_omitted_on_error"] == {
        "active_descriptors": 0,
        "close_count": 1,
        "error": "FileUtf8Error",
        "open_count": 1,
    }
    assert evidence["stale_borrowed_line_accepted"]["stale_access_error"] == (
        "BorrowedLineExpiredError"
    )
    assert evidence["pure_file_read_accepted"]["diagnostic"] == (
        "EffectInPureFunction"
    )
    assert evidence["missing_capability_accepted"]["diagnostic"] == (
        "MissingCapability"
    )
    assert evidence["invalid_ndjson_counted_valid"] == {
        "invalid": 1,
        "total": 2,
        "valid": 1,
    }
    assert evidence["csv_revenue_overflow_ignored"]["diagnostic"] == (
        "RevenueOverflow"
    )
    assert evidence["grep_final_unterminated_line_dropped"] == {
        "matches": [[2, "final match"]],
        "matching_lines": 1,
        "output": "2:final match\n",
        "total_lines": 2,
    }
    assert evidence["body_driven_public_signature_drift"]["diagnostic"] == (
        "PublicInterfaceRevisionMismatch"
    )
    assert evidence["opaque_c_parser_replacement"]["actual_domain_ops"] == []
    assert evidence["opaque_c_parser_replacement"]["mutant_domain_ops"] == [
        "json_parse"
    ]
    assert evidence["opaque_c_parser_replacement"]["primitive_count"] > 0


def test_productive_falsification_report_is_deterministic():
    assert run_productive_falsification() == run_productive_falsification()
