from __future__ import annotations

from pathlib import Path
import subprocess

import pytest


ENTRY = Path("merlo/programs/productive_grep/app/main.mlo")


def test_alpha_versions_are_independent_and_frozen() -> None:
    from merlo.version import VERSIONS

    assert VERSIONS.to_dict() == {
        "release": "0.1.0-alpha.1",
        "language": "0.2",
        "frontend": 3,
        "canonical": 2,
        "hir": 2,
        "rir": 1,
        "mir": 1,
        "runtime_abi": 1,
        "semantic_world": 1,
        "manifest": 1,
        "lockfile": 1,
    }


def test_active_stage_contracts_match_version_matrix() -> None:
    from merlo.concise_application import CONCISE_APPLICATION_SCHEMA_VERSION
    from merlo.representation_c_backend import RUNTIME_ABI_VERSION
    from merlo.representation_ir import REPRESENTATION_IR_SCHEMA_VERSION
    from merlo.representation_mir import GENERAL_MIR_SCHEMA_VERSION
    from merlo.structured_hir_v2 import STRUCTURED_HIR_SCHEMA_VERSION
    from merlo.version import VERSIONS

    assert CONCISE_APPLICATION_SCHEMA_VERSION == VERSIONS.frontend
    assert STRUCTURED_HIR_SCHEMA_VERSION == VERSIONS.hir
    assert REPRESENTATION_IR_SCHEMA_VERSION == VERSIONS.rir
    assert GENERAL_MIR_SCHEMA_VERSION == VERSIONS.mir
    assert RUNTIME_ABI_VERSION == VERSIONS.runtime_abi


def test_compile_project_records_one_complete_parent_digest_chain() -> None:
    from merlo.compiler import compile_project

    compilation = compile_project(ENTRY, require_interface_lock=False)

    assert tuple(compilation.artifacts) == (
        "modules",
        "concise",
        "canonical",
        "hir",
        "rir",
        "mir",
        "optimized_mir",
        "c11",
    )
    previous = None
    for artifact in compilation.artifacts.values():
        assert artifact.parent_digest == previous
        assert len(artifact.digest) == 64
        previous = artifact.digest
    assert compilation.artifacts["canonical"].content == compilation.elaborated.canonical_source
    assert compilation.hir.source == compilation.elaborated.canonical_source
    assert compilation.generated.domain_opaque_calls == ()


def test_compile_project_rejects_interface_drift_when_lock_is_required(tmp_path: Path) -> None:
    from merlo.compiler import compile_project
    from merlo.concise_application import ConciseApplicationError

    source = tmp_path / "app" / "main.mlo"
    source.parent.mkdir()
    source.write_text(
        "module app.main\n\n"
        "export task main(path: Path) -> Result[Text, AppError]:\n"
        "    uses fs.read, console.write\n"
        "    file = fs.open_read(path)?\n"
        "    result = file.lines().count_text()\n"
        "    console.write(result)\n"
        "    return Ok(result)\n",
        encoding="utf-8",
    )

    try:
        compile_project(source, require_interface_lock=True)
    except ConciseApplicationError as exc:
        assert "PublicInterfaceRevisionMismatch" in str(exc)
    else:
        raise AssertionError("public interface drift was accepted")


def test_compile_project_emits_a_standalone_native_binary(tmp_path: Path) -> None:
    from merlo.compiler import compile_project

    output = tmp_path / "grep"
    compilation = compile_project(
        ENTRY,
        emit_native=True,
        release=True,
        output=output,
        require_interface_lock=False,
    )

    assert compilation.native is not None
    assert compilation.native.status == "MEASURED"
    assert compilation.native.binary_path == str(output)
    assert output.is_file()


@pytest.mark.parametrize(
    "application",
    ("concise_json", "productive_ndjson", "productive_csv", "productive_grep"),
)
def test_all_existing_applications_use_direct_canonical_lowering(application: str) -> None:
    from merlo.compiler import compile_project

    entry = Path("merlo/programs") / application / "app" / "main.mlo"
    compilation = compile_project(entry, require_interface_lock=False)

    assert compilation.hir.source == compilation.elaborated.canonical_source
    assert compilation.artifacts["hir"].parent_digest == compilation.artifacts["canonical"].digest


def test_direct_json_pipeline_executes_recursive_json(tmp_path: Path) -> None:
    from merlo.compiler import compile_project

    payload = tmp_path / "input.json"
    payload.write_text('{"a":[1,true,null]}', encoding="utf-8")
    output = tmp_path / "json"
    compilation = compile_project(
        Path("merlo/programs/concise_json/app/main.mlo"),
        emit_native=True,
        output=output,
        require_interface_lock=False,
    )
    completed = subprocess.run(
        [str(compilation.native.binary_path), str(payload)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == (
        "OK checksum=15459945301471017088 nodes=5 "
        "arrays=1 objects=1 fields=1\n"
    )
