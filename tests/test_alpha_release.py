from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from merlo.alpha_release import (
    ALPHA_RELEASE_INCOMPLETE,
    ALPHA_RELEASE_SUPPORTED,
    REQUIRED_DOCS,
    REQUIRED_EXAMPLES,
    REQUIRED_LICENSES,
    REQUIRED_METADATA,
    REQUIRED_SPECS,
    REQUIRED_STDLIB,
    EvidenceRecord,
    GateInput,
    ReleaseInputs,
    ReleaseProvenance,
    ReleaseValidationError,
    assemble_release,
    manifest_payload_sha256,
    manifest_sha256,
    validate_release,
    verify_manifest,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path, *, complete: bool = True) -> ReleaseInputs:
    root = tmp_path / "root"
    root.mkdir(parents=True)
    source = root / "merlo" / "compiler.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"compiler")
    (root / "merlo.lock").write_bytes(b"lock")
    for relative in (*REQUIRED_LICENSES, *REQUIRED_METADATA):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode())
    for relative in (*REQUIRED_DOCS, *REQUIRED_SPECS, *REQUIRED_STDLIB, *REQUIRED_EXAMPLES):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode())
    binary = root / "examples" / "hello"
    binary.write_bytes(b"native")
    binary.chmod(0o755)
    evidence_path = root / "evidence.json"
    evidence_path.write_text("{}", encoding="utf-8")
    for filename, content in (
        ("merlo-0.1.0a1.tar.gz", b"sdist"),
        ("merlo-0.1.0a1-py3-none-any.whl", b"wheel"),
        ("merlo-0.1.0-alpha.1-evidence.zip", b"evidence bundle"),
    ):
        (root / filename).write_bytes(content)
    source_hashes = {"merlo/compiler.py": _sha(source)}
    evidence = tuple(
        EvidenceRecord(
            id=name,
            kind=name,
            gate=name,
            executed=True,
            supported=True,
            status="PASSED",
            payload={"observed": True},
            raw_paths=(evidence_path,),
            raw_hashes={"evidence.json": _sha(evidence_path)},
            source_hashes=source_hashes,
            compiler_sha256=_sha(source),
            lock_sha256=_sha(root / "merlo.lock"),
            artifact_hashes={"merlo/compiler.py": _sha(source)},
        )
        for name in ("clean_demo", "examples", "full_tests", "corpus", "sanitizers", "simplicity", "performance", "lsp", "packaging", "reproducibility")
    )
    gates = tuple(GateInput(name=item.gate, evidence_ids=(item.id,)) for item in evidence)
    provenance = ReleaseProvenance(
        source_commit="0123456789abcdef0123456789abcdef01234567",
        tag="v0.1.0-alpha.1",
        versions={"package": "0.1.0-alpha.1", "compiler": "0.1.0-alpha.1", "displayed": "Merlo alpha.1"},
        platform="Linux",
        architecture="x86_64",
        python={"implementation": "CPython", "version": "3.11.9"},
        c_compiler={"name": "clang", "version": "18.1.8", "target": "x86_64-linux-gnu"},
        build_frontend={"name": "build", "version": "1.2.2"},
        source_date_epoch=1_755_158_400,
        assets={
            "merlo-0.1.0a1.tar.gz": _sha(root / "merlo-0.1.0a1.tar.gz"),
            "merlo-0.1.0a1-py3-none-any.whl": _sha(root / "merlo-0.1.0a1-py3-none-any.whl"),
            "merlo-0.1.0-alpha.1-evidence.zip": _sha(root / "merlo-0.1.0-alpha.1-evidence.zip"),
        },
    )
    return ReleaseInputs(
        root=root,
        compiler_path=Path("merlo/compiler.py"),
        lockfile_path=Path("merlo.lock"),
        source_hashes=source_hashes,
        compiler_sha256=_sha(source),
        lock_sha256=_sha(root / "merlo.lock"),
        gates=gates if complete else gates[:-1],
        evidence=evidence,
        docs=tuple(REQUIRED_DOCS),
        specs=tuple(REQUIRED_SPECS),
        stdlib=tuple(REQUIRED_STDLIB),
        examples=tuple(REQUIRED_EXAMPLES),
        binaries=(Path("examples/hello"),),
        provenance=provenance,
    )


def test_missing_gate_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ReleaseValidationError):
        validate_release(_fixture(tmp_path, complete=False))


def test_complete_inputs_derive_supported_without_emitting_files(tmp_path: Path) -> None:
    result = validate_release(_fixture(tmp_path))
    assert result.status == ALPHA_RELEASE_SUPPORTED


def test_missing_artifact_and_forged_status_are_rejected(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    (inputs.root / "stdlib/std/core.mlo").unlink()
    with pytest.raises(ReleaseValidationError, match="missing required stdlib files: stdlib/std/core.mlo"):
        validate_release(inputs)
    inputs = _fixture(tmp_path / "second")
    with pytest.raises(ReleaseValidationError):
        validate_release(replace(inputs, docs=()))
    with pytest.raises(ReleaseValidationError):
        validate_release(replace(inputs, claimed_status=ALPHA_RELEASE_INCOMPLETE))


def test_manifest_covers_payload_files_and_second_different_emission_is_refused(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    result = assemble_release(inputs, tmp_path / "dist" / "merlo-0.1.0-alpha.1")
    manifest = json.loads((result.path / "manifest.json").read_text(encoding="utf-8"))
    checksums = (result.path / "checksums.sha256").read_text(encoding="utf-8").splitlines()
    assert set(manifest["files"]) <= {line.split("  ", 1)[1] for line in checksums}
    assert set(inputs.provenance.assets) == set(manifest["assets"])
    assert all(len(digest) == 64 for digest in manifest["assets"].values())
    assert any(line.endswith("manifest.json") for line in checksums)
    (inputs.root / "merlo-0.1.0a1.tar.gz").write_bytes(b"changed sdist")
    with pytest.raises(ReleaseValidationError):
        assemble_release(inputs, result.path)


def test_existing_payload_and_checksum_tampering_are_rejected(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    result = assemble_release(inputs, tmp_path / "dist" / "merlo-0.1.0-alpha.1")
    (result.path / "docs" / "README.md").write_bytes(b"tampered")
    with pytest.raises(ReleaseValidationError, match="assembly payload modified"):
        assemble_release(inputs, result.path)
    result = assemble_release(inputs, tmp_path / "other" / "merlo-0.1.0-alpha.1")
    checksum_path = result.path / "checksums.sha256"
    checksum_path.write_text(checksum_path.read_text(encoding="utf-8").replace("  docs/README.md", "  docs/README.changed"), encoding="utf-8")
    with pytest.raises(ReleaseValidationError, match="assembly checksum"):
        assemble_release(inputs, result.path)


def test_asset_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    bad = dict(inputs.provenance.assets)
    bad["merlo-0.1.0a1.tar.gz"] = "0" * 64
    with pytest.raises(ReleaseValidationError, match="asset digest mismatch"):
        validate_release(replace(inputs, provenance=replace(inputs.provenance, assets=bad)))



def test_manifest_v2_schema_rejects_invalid_top_level_and_nested_fields(tmp_path: Path) -> None:
    result = assemble_release(_fixture(tmp_path), tmp_path / "dist" / "merlo-0.1.0-alpha.1")
    manifest = json.loads((result.path / "manifest.json").read_text(encoding="utf-8"))
    invalid = dict(manifest)
    invalid["unexpected"] = True
    invalid["payload_sha256"] = manifest_payload_sha256(invalid)
    invalid["manifest_sha256"] = manifest_sha256(invalid)
    with pytest.raises(ReleaseValidationError, match="top-level keys"):
        verify_manifest(invalid)
    invalid = dict(manifest)
    invalid["status"] = "NOT_A_STATUS"
    invalid["payload_sha256"] = manifest_payload_sha256(invalid)
    invalid["manifest_sha256"] = manifest_sha256(invalid)
    with pytest.raises(ReleaseValidationError, match="invalid status"):
        verify_manifest(invalid)
    invalid = dict(manifest)
    invalid["required"] = dict(manifest["required"])
    invalid["required"].pop("sources")
    invalid["payload_sha256"] = manifest_payload_sha256(invalid)
    invalid["manifest_sha256"] = manifest_sha256(invalid)
    with pytest.raises(ReleaseValidationError, match="required inventory"):
        verify_manifest(invalid)


def test_validation_report_carries_provenance_package_release(tmp_path: Path) -> None:
    result = validate_release(_fixture(tmp_path))
    assert result.to_dict()["release"] == "0.1.0-alpha.1"


def test_reassembly_rejects_reordered_sha256sums(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    destination = tmp_path / "dist" / "merlo-0.1.0-alpha.1"
    assemble_release(inputs, destination)
    checksum_path = destination / "SHA256SUMS"
    checksum_path.write_text("".join(reversed(checksum_path.read_text(encoding="utf-8").splitlines(True))), encoding="utf-8")
    with pytest.raises(ReleaseValidationError, match="different content"):
        assemble_release(inputs, destination)

def test_rehashed_manifest_cannot_forge_cross_field_integrity(tmp_path: Path) -> None:
    result = assemble_release(_fixture(tmp_path), tmp_path / "dist" / "merlo-0.1.0-alpha.1")
    manifest = json.loads((result.path / "manifest.json").read_text(encoding="utf-8"))
    invalid = dict(manifest)
    invalid["files"] = dict(manifest["files"])
    invalid["files"]["assets/merlo-0.1.0a1.tar.gz"] = "0" * 64
    invalid["payload_sha256"] = manifest_payload_sha256(invalid)
    invalid["manifest_sha256"] = manifest_sha256(invalid)
    with pytest.raises(ReleaseValidationError, match="asset digest mismatch"):
        verify_manifest(invalid)
    invalid = dict(manifest)
    invalid["failed_gates"] = ["examples"]
    invalid["payload_sha256"] = manifest_payload_sha256(invalid)
    invalid["manifest_sha256"] = manifest_sha256(invalid)
    with pytest.raises(ReleaseValidationError, match="failure set"):
        verify_manifest(invalid)


def test_asset_roles_are_distinct_and_asset_keys_canonical(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    overlap = inputs.root / "merlo-0.1.0a1-evidence.whl"
    overlap.write_bytes(b"overlap")
    assets = dict(inputs.provenance.assets)
    assets.pop("merlo-0.1.0a1-py3-none-any.whl")
    assets[overlap.name] = _sha(overlap)
    with pytest.raises(ReleaseValidationError, match="distinct role"):
        validate_release(replace(inputs, provenance=replace(inputs.provenance, assets=assets)))
    assets = dict(inputs.provenance.assets)
    digest = assets.pop("merlo-0.1.0a1.tar.gz")
    assets["staging/../merlo-0.1.0a1.tar.gz"] = digest
    with pytest.raises(ReleaseValidationError, match="noncanonical"):
        validate_release(replace(inputs, provenance=replace(inputs.provenance, assets=assets)))


def test_asset_sources_accept_canonical_dist_paths(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    dist = inputs.root / "dist"
    dist.mkdir()
    assets = {}
    for filename, digest in inputs.provenance.assets.items():
        source = inputs.root / filename
        (dist / filename).write_bytes(source.read_bytes())
        source.unlink()
        assets[f"dist/{filename}"] = digest
    inputs = replace(inputs, provenance=replace(inputs.provenance, assets=assets))
    result = assemble_release(inputs, tmp_path / "dist-output" / "merlo-0.1.0-alpha.1")
    assert set(result.manifest["assets"]) == set(filename.split("/")[-1] for filename in assets)


def test_asset_names_reject_version_substrings_and_invalid_alpha_versions(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    bad = dict(inputs.provenance.assets)
    digest = bad.pop("merlo-0.1.0a1.tar.gz")
    bad["merlo-1.0.0a1.tar.gz"] = digest
    with pytest.raises(ReleaseValidationError, match="filenames disagree"):
        validate_release(replace(inputs, provenance=replace(inputs.provenance, assets=bad)))
    versions = dict(inputs.provenance.versions)
    versions["package"] = "1"
    with pytest.raises(ReleaseValidationError, match="package version"):
        validate_release(replace(inputs, provenance=replace(inputs.provenance, versions=versions)))