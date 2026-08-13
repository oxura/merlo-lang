from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from merlo.alpha_release import (
    ALPHA_RELEASE_INCOMPLETE,
    ALPHA_RELEASE_SUPPORTED,
    EvidenceRecord,
    GateInput,
    ReleaseInputs,
    ReleaseValidationError,
    assemble_release,
    validate_release,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path, *, complete: bool = True) -> ReleaseInputs:
    root = tmp_path / "root"
    root.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "compiler.py").write_bytes(b"compiler")
    (root / "merlo.lock").write_bytes(b"lock")
    for directory, name in (("docs", "guide.md"), ("spec", "alpha.md"), ("stdlib", "core.mlo")):
        (root / directory).mkdir()
        (root / directory / name).write_bytes(name.encode())
    example = root / "examples" / "hello"
    example.mkdir(parents=True)
    (example / "hello").write_bytes(b"native")
    (example / "hello").chmod(0o755)
    evidence_path = root / "evidence.json"
    evidence_path.write_text("{}", encoding="utf-8")
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
            source_hashes={"src/compiler.py": _sha(root / "src" / "compiler.py")},
            compiler_sha256=_sha(root / "src" / "compiler.py"),
            lock_sha256=_sha(root / "merlo.lock"),
            artifact_hashes={"src/compiler.py": _sha(root / "src" / "compiler.py")},
        )
        for name in ("clean_demo", "examples", "full_tests", "corpus", "sanitizers", "simplicity", "performance", "lsp", "packaging", "reproducibility")
    )
    gates = tuple(GateInput(name=item.gate, evidence_ids=(item.id,)) for item in evidence)
    return ReleaseInputs(
        root=root,
        compiler_path=Path("src/compiler.py"),
        lockfile_path=Path("merlo.lock"),
        source_hashes={"src/compiler.py": _sha(root / "src" / "compiler.py")},
        compiler_sha256=_sha(root / "src" / "compiler.py"),
        lock_sha256=_sha(root / "merlo.lock"),
        gates=gates if complete else gates[:-1],
        evidence=evidence,
        docs=(Path("docs/guide.md"),),
        specs=(Path("spec/alpha.md"),),
        stdlib=(Path("stdlib/core.mlo"),),
        examples=(Path("examples/hello"),),
        binaries=(Path("examples/hello/hello"),),
    )


def test_missing_gate_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ReleaseValidationError):
        validate_release(_fixture(tmp_path, complete=False))


def test_complete_inputs_derive_supported_without_emitting_files(tmp_path: Path) -> None:
    result = validate_release(_fixture(tmp_path))
    assert result.status == ALPHA_RELEASE_SUPPORTED
    assert not (tmp_path / "dist").exists()


def test_assembly_is_atomic_and_repeatable(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    first = assemble_release(inputs, tmp_path / "dist" / "merlo-0.1.0-alpha.1")
    first_bytes = (first.path / "manifest.json").read_bytes()
    second = assemble_release(inputs, tmp_path / "dist" / "merlo-0.1.0-alpha.1")
    assert second.path == first.path
    assert (second.path / "manifest.json").read_bytes() == first_bytes
    manifest = json.loads(first_bytes)
    assert manifest["payload_sha256"]
    assert manifest["files"]

def test_duplicate_ids_and_unexecuted_evidence_are_rejected(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    duplicate = replace(inputs, evidence=inputs.evidence[:-1] + (inputs.evidence[0],))
    with pytest.raises(ReleaseValidationError):
        validate_release(duplicate)
    unexecuted_record = replace(inputs.evidence[0], executed=False, content_sha256=None)
    unexecuted = replace(inputs, evidence=(unexecuted_record,) + inputs.evidence[1:])
    with pytest.raises(ReleaseValidationError):
        validate_release(unexecuted)


def test_stale_hashes_and_raw_tampering_are_rejected(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    (inputs.root / "src" / "compiler.py").write_bytes(b"changed")
    with pytest.raises(ReleaseValidationError):
        validate_release(inputs)
    inputs = _fixture(tmp_path / "second")
    (inputs.root / "evidence.json").write_bytes(b"tampered")
    with pytest.raises(ReleaseValidationError):
        validate_release(inputs)


def test_missing_artifact_and_forged_status_are_rejected(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    with pytest.raises(ReleaseValidationError):
        validate_release(replace(inputs, docs=()))
    with pytest.raises(ReleaseValidationError):
        validate_release(replace(inputs, claimed_status=ALPHA_RELEASE_INCOMPLETE))


def test_manifest_covers_payload_files_and_second_different_emission_is_refused(tmp_path: Path) -> None:
    inputs = _fixture(tmp_path)
    result = assemble_release(inputs, tmp_path / "dist" / "merlo-0.1.0-alpha.1")
    manifest = json.loads((result.path / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["files"]) == {
        line.split("  ", 1)[1]
        for line in (result.path / "checksums.sha256").read_text(encoding="utf-8").splitlines()
    }
    (inputs.root / "docs" / "guide.md").write_bytes(b"changed docs")
    with pytest.raises(ReleaseValidationError):
        assemble_release(inputs, result.path)
