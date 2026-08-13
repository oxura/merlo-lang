from __future__ import annotations

from merlo.recursive_json_intrinsic_audit import (
    AUDIT_STATUS,
    run_recursive_json_intrinsic_audit,
    validate_recursive_json_intrinsic_audit,
)


def test_recursive_json_audit_detects_opaque_intrinsic_boundary(tmp_path) -> None:
    report = run_recursive_json_intrinsic_audit(tmp_path / "audit.json")
    validate_recursive_json_intrinsic_audit(report)
    assert report["status"] == AUDIT_STATUS
    assert report["decision"] == {
        "current_final_path_acceptable": False,
        "json_semantics_in_ordinary_surface": False,
        "json_semantics_in_hir": False,
        "json_semantics_in_mir_cfg": False,
        "handwritten_c_contains_json_semantics": True,
        "opaque_runtime_json_calls": 1,
        "byte_scanner_decomposition_possible_without_new_types": True,
        "owning_recursive_ast_possible_in_current_type_ir": False,
        "frontend_hir_mir_redesign_required": True,
        "selected_status": AUDIT_STATUS,
    }
    assert report["tokenizer_path"]["mir"]["instruction_count"] == 1
    assert report["tokenizer_path"]["mir"]["instructions"][0]["op"] == (
        "json_token_checksum"
    )
    assert all(
        item["opaque_runtime_call"] and item["handwritten_c_logic"]
        for item in report["tokenizer_path"]["components"]
    )
    assert all(
        not item["accepted"]
        for item in report["recursive_representation_probes"].values()
    )
