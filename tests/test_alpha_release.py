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
    validate_release,
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
        ("merlo-0.1.0-alpha.1.tar.gz", b"sdist"),
        ("merlo-0.1.0-alpha.1-py3-none-any.whl", b"wheel"),
        ("merlo-alpha.1-evidence.json", b"evidence bundle"),
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
            "merlo-0.1.0-alpha.1.tar.gz": _sha(root / "merlo-0.1.0-alpha.1.tar.gz"),
            "merlo-0.1.0-alpha.1-py3-none-any.whl": _sha(root / "merlo-0.1.0-alpha.1-py3-none-any.whl"),
            "merlo-alpha.1-evidence.json": _sha(root / "merlo-alpha.1-evidence.json"),
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
    (inputs.root / "merlo-0.1.0-alpha.1.tar.gz").write_bytes(b"changed sdist")
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
    bad["merlo-0.1.0-alpha.1.tar.gz"] = "0" * 64
    with pytest.raises(ReleaseValidationError, match="asset digest mismatch"):
        validate_release(replace(inputs, provenance=replace(inputs.provenance, assets=bad)))
