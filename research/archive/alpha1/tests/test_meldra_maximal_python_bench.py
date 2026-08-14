from __future__ import annotations

from research.archive.alpha1.merlo.maximal_python_bench import run_maximal_python_benchmark


def test_maximal_python_generated_pilot_is_three_arm_and_honest_about_rejection_cost():
    report = run_maximal_python_benchmark(40)
    payload = report.to_dict()

    assert payload["binding"]["current-python-sidecar"]["numerator"] == 1240
    assert payload["binding"]["maximal-python-profile"]["numerator"] == 1920
    assert payload["binding"]["meldra-closed"]["numerator"] == 1920
    assert {
        item["denominator"] for item in payload["binding"].values()
    } == {1920}

    runtime = payload["runtime_soundness"]
    assert runtime["current-python-sidecar"]["unsound_exact_count"] == 140
    assert runtime["maximal-python-profile"]["rejected_callsites"] == 23
    assert runtime["maximal-python-profile"]["static_exact_callsites"] == 0
    assert runtime["meldra-closed"]["unsound_exact_count"] == 0
    assert payload["dynamic_profile_rejections"]["rate"] == 1.0

    assert {item["status"] for item in payload["bypass_detection"]} == {
        "BLOCKED"
    }
    assert payload["runtime_audit_status"] == "RUNTIME_POLICY_BLOCK"
    assert payload["interfaces"] == {
        "private_body_preserved_interface": True,
        "public_signature_changed_interface": True,
    }
    assert all(
        item["applied"] and item["identity_continuity"]
        for item in payload["changeir"].values()
    )
    assert payload["lsp_status"] == "UNMEASURED_NO_LANGUAGE_SERVER"
    assert payload["evidence_level"] == "GENERATED_PILOT_NOT_EXTERNAL_EVIDENCE"
    assert payload["decision"] == "NO_GO_LANGUAGE_ALPHA"
