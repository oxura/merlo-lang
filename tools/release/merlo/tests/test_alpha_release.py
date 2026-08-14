from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from tools.release.merlo import alpha_release as release

from tools.release.merlo.alpha_release import (
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
    source = root / "src" / "merlo" / "compiler.py"
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
        ("merlo-0.1.0a2.tar.gz", b"sdist"),
        ("merlo-0.1.0a2-py3-none-any.whl", b"wheel"),
        ("merlo-0.1.0-alpha.2-evidence.zip", b"evidence bundle"),
    ):
        (root / filename).write_bytes(content)
    source_hashes = {"src/merlo/compiler.py": _sha(source)}
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
            artifact_hashes={"src/merlo/compiler.py": _sha(source)},
        )
        for name in ("clean_demo", "examples", "full_tests", "corpus", "sanitizers", "simplicity", "performance", "lsp", "packaging", "reproducibility")
    )
    gates = tuple(GateInput(name=item.gate, evidence_ids=(item.id,)) for item in evidence)
    provenance = ReleaseProvenance(
        source_commit="0123456789abcdef0123456789abcdef01234567",
        tag="v0.1.0-alpha.2",
        versions={"package": "0.1.0-alpha.2", "compiler": "0.1.0-alpha.2", "displayed": "Merlo alpha.2"},
        platform="Linux",
        architecture="x86_64",
        python={"implementation": "CPython", "version": "3.11.9"},
        c_compiler={"name": "clang", "version": "18.1.8", "target": "x86_64-linux-gnu"},
        build_frontend={"name": "build", "version": "1.2.2"},
        source_date_epoch=1_755_158_400,
        assets={
            "merlo-0.1.0a2.tar.gz": _sha(root / "merlo-0.1.0a2.tar.gz"),
            "merlo-0.1.0a2-py3-none-any.whl": _sha(root / "merlo-0.1.0a2-py3-none-any.whl"),
            "merlo-0.1.0-alpha.2-evidence.zip": _sha(root / "merlo-0.1.0-alpha.2-evidence.zip"),
        },
    )
    return ReleaseInputs(
        root=root,
        compiler_path=Path("src/merlo/compiler.py"),
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

def _builder_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    inputs = _fixture(tmp_path)
    root = inputs.root
    lock = root / release.ACTIVE_WORKLOAD_LOCK_PATH
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(b'{"active":"capacity-ledger"}\n')
    dist = root / "dist"
    dist.mkdir()
    for filename in ("merlo-0.1.0a2.tar.gz", "merlo-0.1.0a2-py3-none-any.whl"):
        shutil.copyfile(root / filename, dist / filename)
    binary = root / "examples" / "hello"
    attestation = root / "ci-attestation.json"
    payload = {
        "schema_version": 1,
        "repository": "oxura/merlo-lang",
        "run_id": 123,
        "run_url": "https://github.com/oxura/merlo-lang/actions/runs/123",
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "tag": "v0.1.0-alpha.2",
        "jobs": {
            "production": {"result": "success", "responsibilities": ["full_tests", "lsp", "examples"]},
            "tooling": {"result": "success", "responsibilities": ["corpus", "sanitizers", "simplicity", "performance"]},
            "archive": {"result": "success", "responsibilities": []},
            "artifacts": {"result": "success", "responsibilities": ["clean_demo", "packaging", "reproducibility"]},
        },
    }
    attestation.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return root, dist, binary, attestation, tmp_path / "release"


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
    result = assemble_release(inputs, tmp_path / "dist" / "merlo-0.1.0-alpha.2")
    manifest = json.loads((result.path / "manifest.json").read_text(encoding="utf-8"))
    checksums = (result.path / "checksums.sha256").read_text(encoding="utf-8").splitlines()
    assert set(manifest["files"]) <= {line.split("  ", 1)[1] for line in checksums}
    assert set(inputs.provenance.assets) == set(manifest["assets"])
    assert all(len(digest) == 64 for digest in manifest["assets"].values())
    assert any(line.endswith("manifest.json") for line in checksums)
    (inputs.root / "merlo-0.1.0a2.tar.gz").write_bytes(b"changed sdist")
    with pytest.raises(ReleaseValidationError):
        assemble_release(inputs, result.path)


def test_existing_payload_and_checksum_tampering_are_rejected(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    result = assemble_release(inputs, tmp_path / "dist" / "merlo-0.1.0-alpha.2")
    (result.path / "docs" / "README.md").write_bytes(b"tampered")
    with pytest.raises(ReleaseValidationError, match="assembly payload modified"):
        assemble_release(inputs, result.path)
    result = assemble_release(inputs, tmp_path / "other" / "merlo-0.1.0-alpha.2")
    checksum_path = result.path / "checksums.sha256"
    checksum_path.write_text(checksum_path.read_text(encoding="utf-8").replace("  docs/README.md", "  docs/README.changed"), encoding="utf-8")
    with pytest.raises(ReleaseValidationError, match="assembly checksum"):
        assemble_release(inputs, result.path)


def test_asset_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    bad = dict(inputs.provenance.assets)
    bad["merlo-0.1.0a2.tar.gz"] = "0" * 64
    with pytest.raises(ReleaseValidationError, match="asset digest mismatch"):
        validate_release(replace(inputs, provenance=replace(inputs.provenance, assets=bad)))



def test_manifest_v2_schema_rejects_invalid_top_level_and_nested_fields(tmp_path: Path) -> None:
    result = assemble_release(_fixture(tmp_path), tmp_path / "dist" / "merlo-0.1.0-alpha.2")
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
    invalid = dict(manifest)
    invalid["gates"] = dict(manifest["gates"])
    invalid["gates"].pop("examples")
    invalid["payload_sha256"] = manifest_payload_sha256(invalid)
    invalid["manifest_sha256"] = manifest_sha256(invalid)
    with pytest.raises(ReleaseValidationError, match="gates"):
        verify_manifest(invalid)
    invalid = dict(manifest)
    invalid["required"] = dict(manifest["required"])
    invalid["required"]["sources"] = []
    invalid["payload_sha256"] = manifest_payload_sha256(invalid)
    invalid["manifest_sha256"] = manifest_sha256(invalid)
    with pytest.raises(ReleaseValidationError, match="sources or binaries"):
        verify_manifest(invalid)


def test_validation_report_carries_provenance_package_release(tmp_path: Path) -> None:
    result = validate_release(_fixture(tmp_path))
    assert result.to_dict()["release"] == "0.1.0-alpha.2"


def test_reassembly_rejects_reordered_sha256sums(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    destination = tmp_path / "dist" / "merlo-0.1.0-alpha.2"
    assemble_release(inputs, destination)
    checksum_path = destination / "SHA256SUMS"
    checksum_path.write_text("".join(reversed(checksum_path.read_text(encoding="utf-8").splitlines(True))), encoding="utf-8")
    with pytest.raises(ReleaseValidationError, match="different content"):
        assemble_release(inputs, destination)

def test_rehashed_manifest_cannot_forge_cross_field_integrity(tmp_path: Path) -> None:
    result = assemble_release(_fixture(tmp_path), tmp_path / "dist" / "merlo-0.1.0-alpha.2")
    manifest = json.loads((result.path / "manifest.json").read_text(encoding="utf-8"))
    invalid = dict(manifest)
    invalid["files"] = dict(manifest["files"])
    invalid["files"]["assets/merlo-0.1.0a2.tar.gz"] = "0" * 64
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


def test_manifest_status_derivation_and_failed_evidence_coverage(tmp_path: Path) -> None:
    result = assemble_release(_fixture(tmp_path), tmp_path / "dist" / "merlo-0.1.0-alpha.2")
    manifest = json.loads((result.path / "manifest.json").read_text(encoding="utf-8"))
    repro = dict(manifest)
    repro["gates"] = dict(manifest["gates"])
    repro["gates"]["full_tests"] = False
    repro["gates"]["reproducibility"] = False
    repro["failed_gates"] = ["full_tests", "reproducibility"]
    repro["failed_evidence_ids"] = ["full_tests", "reproducibility"]
    repro["status"] = "MERLO_ALPHA_RELEASE_REPRODUCIBILITY_DEFECT"
    repro["payload_sha256"] = manifest_payload_sha256(repro)
    repro["manifest_sha256"] = manifest_sha256(repro)
    verify_manifest(repro)
    incomplete = dict(repro)
    incomplete["gates"] = dict(manifest["gates"])
    incomplete["gates"]["full_tests"] = False
    incomplete["gates"]["reproducibility"] = True
    incomplete["failed_gates"] = ["full_tests"]
    incomplete["failed_evidence_ids"] = []
    incomplete["status"] = "MERLO_ALPHA_RELEASE_INCOMPLETE"
    incomplete["payload_sha256"] = manifest_payload_sha256(incomplete)
    incomplete["manifest_sha256"] = manifest_sha256(incomplete)
    with pytest.raises(ReleaseValidationError, match="evidence coverage"):
        verify_manifest(incomplete)
    passing = dict(manifest)
    passing["failed_evidence_ids"] = ["clean_demo"]
    passing["payload_sha256"] = manifest_payload_sha256(passing)
    passing["manifest_sha256"] = manifest_sha256(passing)
    with pytest.raises(ReleaseValidationError, match="evidence coverage"):
        verify_manifest(passing)


def test_asset_roles_are_distinct_and_asset_keys_canonical(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    overlap = inputs.root / "merlo-0.1.0a2-evidence.whl"
    overlap.write_bytes(b"overlap")
    assets = dict(inputs.provenance.assets)
    assets.pop("merlo-0.1.0a2-py3-none-any.whl")
    assets[overlap.name] = _sha(overlap)
    with pytest.raises(ReleaseValidationError, match="distinct role"):
        validate_release(replace(inputs, provenance=replace(inputs.provenance, assets=assets)))
    assets = dict(inputs.provenance.assets)
    digest = assets.pop("merlo-0.1.0a2.tar.gz")
    assets["staging/../merlo-0.1.0a2.tar.gz"] = digest
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
    result = assemble_release(inputs, tmp_path / "dist-output" / "merlo-0.1.0-alpha.2")
    assert set(result.manifest["assets"]) == set(filename.split("/")[-1] for filename in assets)


def test_asset_names_reject_version_substrings_and_invalid_alpha_versions(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    bad = dict(inputs.provenance.assets)
    digest = bad.pop("merlo-0.1.0a2.tar.gz")
    bad["merlo-1.0.0a2.tar.gz"] = digest
    with pytest.raises(ReleaseValidationError, match="filenames disagree"):
        validate_release(replace(inputs, provenance=replace(inputs.provenance, assets=bad)))
    versions = dict(inputs.provenance.versions)
    versions["package"] = "1"
    with pytest.raises(ReleaseValidationError, match="package version"):
        validate_release(replace(inputs, provenance=replace(inputs.provenance, versions=versions)))


def test_provenance_rejects_compiler_and_display_version_mismatch(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    versions = dict(inputs.provenance.versions)
    versions["compiler"] = "0.1.0-alpha.1"
    with pytest.raises(ReleaseValidationError, match="compiler version"):
        validate_release(replace(inputs, provenance=replace(inputs.provenance, versions=versions)))
    versions = dict(inputs.provenance.versions)
    versions["displayed"] = "Merlo alpha.1"
    with pytest.raises(ReleaseValidationError, match="displayed version"):
        validate_release(replace(inputs, provenance=replace(inputs.provenance, versions=versions)))


def test_provenance_rejects_tag_mismatch(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    provenance = replace(inputs.provenance, tag="v0.1.0-alpha.1")
    with pytest.raises(ReleaseValidationError, match="tag disagrees"):
        validate_release(replace(inputs, provenance=provenance))


def _patch_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        release,
        "_release_toolchain_identity",
        lambda: (
            {"name": "clang", "version": "clang version test", "target": "x86_64"},
            {"name": "build", "version": "1.2.2"},
        ),
    )


def test_builder_rejects_attestation_identity_and_job_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, dist, binary, attestation, output = _builder_fixture(tmp_path)
    _patch_toolchain(monkeypatch)
    payload = json.loads(attestation.read_text(encoding="utf-8"))
    payload["commit"] = "f" * 40
    attestation.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReleaseValidationError, match="identity mismatch"):
        release.build_alpha_release(root, dist, output, "v0.1.0-alpha.2", "0" * 40, 1, sample_binary=binary, attestation=attestation)
    payload["commit"] = "0123456789abcdef0123456789abcdef01234567"
    payload["jobs"]["tooling"]["result"] = "failure"
    attestation.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReleaseValidationError, match="did not succeed"):
        release.build_alpha_release(root, dist, output, "v0.1.0-alpha.2", payload["commit"], 1, sample_binary=binary, attestation=attestation)


def test_builder_deterministic_evidence_and_three_asset_assembly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, dist, binary, attestation, output = _builder_fixture(tmp_path)
    _patch_toolchain(monkeypatch)
    commit = "0123456789abcdef0123456789abcdef01234567"
    first = release.build_alpha_release(root, dist, output, "v0.1.0-alpha.2", commit, 1, sample_binary=binary, attestation=attestation)
    evidence_bytes = first.evidence_zip.read_bytes()
    second = release.build_alpha_release(root, dist, tmp_path / "release-second", "v0.1.0-alpha.2", commit, 1, sample_binary=binary, attestation=attestation)
    assert second.evidence_zip.read_bytes() == evidence_bytes
    assert set(first.assembly.manifest["assets"]) == {
        "merlo-0.1.0a2.tar.gz",
        "merlo-0.1.0a2-py3-none-any.whl",
        "merlo-0.1.0-alpha.2-evidence.zip",
    }
    with zipfile.ZipFile(first.evidence_zip) as archive:
        assert archive.namelist() == ["attestation.json", "manifest.json", "checksums.sha256", "SHA256SUMS"]


def test_builder_invokes_typed_assembly_and_rejects_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, dist, binary, attestation, output = _builder_fixture(tmp_path)
    _patch_toolchain(monkeypatch)
    called = 0
    real_assemble = release.assemble_release

    def observed(inputs: ReleaseInputs, destination: Path | str):
        nonlocal called
        called += 1
        return real_assemble(inputs, destination)

    monkeypatch.setattr(release, "assemble_release", observed)
    commit = "0123456789abcdef0123456789abcdef01234567"
    result = release.build_alpha_release(root, dist, output, "v0.1.0-alpha.2", commit, 1, sample_binary=binary, attestation=attestation)
    assert called == 1
    (result.path / "docs" / "README.md").write_bytes(b"tampered")
    with pytest.raises(ReleaseValidationError, match="payload modified"):
        release.build_alpha_release(root, dist, output, "v0.1.0-alpha.2", commit, 1, sample_binary=binary, attestation=attestation)


def test_builder_cli_accepts_ci_attestation_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root, dist, binary, attestation, output = _builder_fixture(tmp_path)
    _patch_toolchain(monkeypatch)
    commit = "0123456789abcdef0123456789abcdef01234567"
    assert release.main([
        "--root", str(root), "--dist", str(dist), "--output", str(output),
        "--tag", "v0.1.0-alpha.2", "--commit", commit, "--source-date-epoch", "1",
        "--sample-binary", str(binary), "--ci-attestation", str(attestation),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == release.ALPHA_RELEASE_SUPPORTED


def test_canonicalize_sdist_normalizes_headers_and_preserves_pkg_info(tmp_path: Path) -> None:
    content = b"Metadata-Version: 2.4\nName: merlo\nVersion: 0.1.0a2\n"

    def write_archive(path: Path, member_epoch: int) -> None:
        with tarfile.open(path, "w:gz") as archive:
            directory = tarfile.TarInfo("merlo-0.1.0a2/")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            directory.mtime = member_epoch
            archive.addfile(directory)
            member = tarfile.TarInfo("merlo-0.1.0a2/PKG-INFO")
            member.mode = 0o644
            member.mtime = member_epoch
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))

    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    write_archive(first, 1)
    write_archive(second, 2)
    release.canonicalize_sdist(first, 123)
    release.canonicalize_sdist(second, 123)
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first, "r:gz") as archive:
        member = archive.getmember("merlo-0.1.0a2/PKG-INFO")
        assert member.mtime == 123
        assert member.uid == member.gid == 0
        assert archive.extractfile(member).read() == content


def test_toolchain_identity_requires_real_compiler_and_frontend(monkeypatch: pytest.MonkeyPatch) -> None:
    from merlo import native_c_backend

    monkeypatch.setattr(native_c_backend, "find_c_compiler", lambda: "/opt/clang")
    monkeypatch.setattr(native_c_backend, "compiler_version", lambda _: "clang version test")
    monkeypatch.setattr(release.importlib.metadata, "version", lambda _: "1.2.3")
    c_compiler, frontend = release._release_toolchain_identity()
    assert c_compiler["name"] == "clang"
    assert c_compiler["version"] == "clang version test"
    assert frontend == {"name": "build", "version": "1.2.3"}