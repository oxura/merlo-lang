from tools.benchmarks.merlo.productive_external_fixtures import verify_productive_external_fixtures


def test_two_provenance_verified_external_fixtures_exist_per_format():
    report = verify_productive_external_fixtures()

    assert report["passed"] is True
    assert report["counts"] == {"csv": 2, "ndjson": 2, "text": 2}
    assert report["unmeasured"] == []
    assert all(
        item["provenance"]
        in {"pinned_external", "derived_from_pinned_external"}
        for item in report["fixtures"]
    )
    assert all(
        item["source_url"].startswith("https://raw.githubusercontent.com/")
        for item in report["fixtures"]
    )
