from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from merlo.patch_evidence import (
    PATCH_EVIDENCE_CONTRACT,
    PatchClaimStatus,
    PatchEvidenceBundle,
    emit_patch_evidence,
)
from merlo.refactor import preview_rename
from merlo.semantic_capsule import extract_semantic_capsule
from merlo.semantic_world import SemanticWorld


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "app" / "main.mlo"
    path.parent.mkdir(parents=True)
    path.write_text(
        "module app.main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "export task helper(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"helper\")\n"
        "    return Ok(\"helper\")\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"main\")\n"
        "    return helper(path)\n",
        encoding="utf-8",
    )
    return path


def test_emit_rename_carries_structural_evidence(tmp_path: Path) -> None:
    source = _source(tmp_path)
    before = SemanticWorld.build(source, require_interface_lock=False)
    before_capsule = extract_semantic_capsule(before, "app.main.helper")
    change = preview_rename(before, "app.main.helper", "assist")
    receipt = change.apply()
    after = SemanticWorld.build(source, require_interface_lock=False)
    after_capsule = extract_semantic_capsule(after, "app.main.assist")

    bundle = emit_patch_evidence(change, before, after, receipt, before_capsule, after_capsule)
    assert bundle.contract == PATCH_EVIDENCE_CONTRACT
    assert bundle.target.before_qualified_name == "app.main.helper"
    assert bundle.target.after_qualified_name == "app.main.assist"
    assert bundle.files[0].path == str(source.resolve())
    assert bundle.files[0].before_sha256 != bundle.files[0].after_sha256
    assert {claim.name for claim in bundle.claims} == {
        "authorized_edits",
        "atomic_apply_receipt_observed",
        "world_rebuilt",
        "target_rebound",
        "evidence_carried",
    }
    assert all(claim.status is PatchClaimStatus.PROVEN for claim in bundle.claims)
    assert not any("test" in claim.name or "contract" in claim.name or "capabilit" in claim.name for claim in bundle.claims)


def test_evidence_handles_multiple_variable_length_edits(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    before = SemanticWorld.build(
        source,
        require_interface_lock=False,
    )
    change = preview_rename(
        before,
        "app.main.helper",
        "assistance",
    )
    before_capsule = extract_semantic_capsule(
        before,
        "app.main.helper",
    )
    receipt = change.apply()
    after = SemanticWorld.build(
        source,
        require_interface_lock=False,
    )
    after_capsule = extract_semantic_capsule(
        after,
        "app.main.assistance",
    )

    bundle = emit_patch_evidence(
        change,
        before,
        after,
        receipt,
        before_capsule,
        after_capsule,
    )

    assert len(bundle.files[0].edits) == 2
    assert "task assistance" in source.read_text(
        encoding="utf-8"
    )


def test_patch_evidence_roundtrip_and_digest_tamper_rejection(tmp_path: Path) -> None:
    source = _source(tmp_path)
    before = SemanticWorld.build(source, require_interface_lock=False)
    change = preview_rename(before, "app.main.helper", "assist")
    before_capsule = extract_semantic_capsule(before, "app.main.helper")
    receipt = change.apply()
    after = SemanticWorld.build(source, require_interface_lock=False)
    after_capsule = extract_semantic_capsule(after, "app.main.assist")
    bundle = emit_patch_evidence(change, before, after, receipt, before_capsule, after_capsule)

    assert PatchEvidenceBundle.from_json(bundle.to_json()).to_dict() == bundle.to_dict()
    tampered = json.loads(bundle.to_json())
    tampered["target"]["after_qualified_name"] = "app.main.other"
    with pytest.raises(ValueError, match="DigestMismatch"):
        PatchEvidenceBundle.from_dict(tampered)
    schema = json.loads(bundle.to_json())
    schema["contract"] = "merlo.patch-evidence.v0"
    with pytest.raises(ValueError, match="ContractMismatch"):
        PatchEvidenceBundle.from_dict(schema)


def test_emit_rejects_uncommitted_or_mismatched_receipts(tmp_path: Path) -> None:
    source = _source(tmp_path)
    before = SemanticWorld.build(source, require_interface_lock=False)
    change = preview_rename(before, "app.main.helper", "assist")
    capsule = extract_semantic_capsule(before, "app.main.helper")
    with pytest.raises(ValueError, match="ApplyNotCommitted"):
        emit_patch_evidence(change, before, before, {"committed": False}, capsule, capsule)
    receipt = change.apply()
    after = SemanticWorld.build(source, require_interface_lock=False)
    after_capsule = extract_semantic_capsule(after, "app.main.assist")
    bad = dict(receipt)
    bad["files"] = []
    with pytest.raises(ValueError, match="ChangedFilesMismatch"):
        emit_patch_evidence(change, before, after, bad, capsule, after_capsule)

    forged = dict(receipt)
    forged_transaction = dict(
        forged["transaction"]
    )
    forged_transaction["files"] = [
        *forged_transaction["files"],
        "unrelated.mlo",
    ]
    forged_hashes = dict(
        forged_transaction["resulting_hashes"]
    )
    forged_hashes["unrelated.mlo"] = "0" * 64
    forged_transaction[
        "resulting_hashes"
    ] = forged_hashes
    forged["transaction"] = forged_transaction
    with pytest.raises(
        ValueError,
        match="TransactionHashMismatch",
    ):
        emit_patch_evidence(
            change,
            before,
            after,
            forged,
            capsule,
            after_capsule,
        )

    tampered_capsule = replace(
        after_capsule,
        effects=("network.http",),
    )
    with pytest.raises(
        ValueError,
        match="CapsuleExtractionMismatch",
    ):
        emit_patch_evidence(
            change,
            before,
            after,
            receipt,
            capsule,
            tampered_capsule,
        )
