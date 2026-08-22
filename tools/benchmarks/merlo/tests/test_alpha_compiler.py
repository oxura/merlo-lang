from __future__ import annotations

from pathlib import Path
import subprocess

import pytest


ENTRY = Path("tools/benchmarks/merlo/programs/productive_grep/app/main.mlo")


def test_alpha_versions_are_independent_and_frozen() -> None:
    from merlo.version import VERSIONS

    assert VERSIONS.to_dict() == {
        "release": "0.1.0-alpha.3-dev",
        "language": "0.3",
        "frontend": 8,
        "canonical": 6,
        "hir": 12,
        "obligation_ir": 1,
        "range_analysis": 1,
        "bounded_symbolic": 1,
        "smt": 1,
        "property_evidence": 1,
        "verification_metrics": 1,
        "change_ir": 1,
        "semantic_capsule": 1,
        "semantic_impact": 1,
        "patch_evidence": 1,
        "preservation": 1,
        "transaction": 1,
        "rir": 6,
        "mir": 3,
        "parallel_ir": 1,
        "wasm_backend": 1,
        "runtime_abi": 2,
        "semantic_world": 19,
        "manifest": 1,
        "lockfile": 1,
    }


def test_active_stage_contracts_match_version_matrix() -> None:
    from merlo.frontend_model import CONCISE_APPLICATION_SCHEMA_VERSION
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
        "obligations",
        "ranges",
        "bounded-symbolic",
        "smt",
        "property-evidence",
        "verification-metrics",
        "rir",
        "mir",
        "optimized_mir",
        "parallel_ir",
        "c11",
    )
    for artifact in compilation.artifacts.values():
        assert len(artifact.digest) == 64
    parents = {
        name: next(
            (
                parent_name
                for parent_name, parent in compilation.artifacts.items()
                if parent.digest == artifact.parent_digest
            ),
            None,
        )
        for name, artifact in compilation.artifacts.items()
    }
    assert parents == {
        "modules": None,
        "concise": "modules",
        "canonical": "concise",
        "hir": "canonical",
        "obligations": "hir",
        "ranges": "hir",
        "bounded-symbolic": "obligations",
        "smt": "obligations",
        "property-evidence": "obligations",
        "verification-metrics": "obligations",
        "rir": "hir",
        "mir": "rir",
        "optimized_mir": "mir",
        "parallel_ir": "optimized_mir",
        "c11": "optimized_mir",
    }
    assert compilation.artifacts["canonical"].content == compilation.elaborated.canonical_source
    assert compilation.hir.source == compilation.elaborated.canonical_source
    assert compilation.generated.domain_opaque_calls == ()


def test_compile_project_rejects_interface_drift_when_lock_is_required(tmp_path: Path) -> None:
    from merlo.compiler import compile_project
    from merlo.frontend_model import ConciseApplicationError

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


def test_concise_text_entry_reads_stdin_and_writes_returned_text(tmp_path: Path) -> None:
    from merlo.compiler import compile_project

    entry = tmp_path / "app" / "main.mlo"
    entry.parent.mkdir()
    entry.write_text(
        "module app.main\n\n"
        "fn echo(input: TextView) -> Text:\n"
        "    input.to_text()\n\n"
        "export task main(input: Text) -> Text:\n"
        "    uses console.read, console.write\n"
        "    return echo(input.view())\n",
        encoding="utf-8",
    )
    compilation = compile_project(
        entry,
        emit_native=True,
        output=tmp_path / "text-entry",
        require_interface_lock=False,
    )

    assert compilation.native is not None
    completed = subprocess.run(
        [compilation.native.binary_path],
        input="hello\nworld",
        capture_output=True,
        text=True,
        check=False,
    )
    assert (completed.returncode, completed.stdout, completed.stderr) == (
        0,
        "hello\nworld",
        "",
    )


def test_native_compile_rejects_non_unit_fallthrough_before_c_lowering(
    tmp_path: Path,
) -> None:
    from merlo.compiler import compile_project
    from merlo.frontend_model import ConciseApplicationError

    entry = tmp_path / "app" / "main.mlo"
    entry.parent.mkdir()
    entry.write_text(
        "module app.main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn read(flag: Bool) -> UInt64:\n"
        "    if flag:\n"
        "        return 1\n"
        "\n"
        "export task main(path: Path) -> Result[UInt64, AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"control\")\n"
        "    return Ok(read(true))\n",
        encoding="utf-8",
    )

    with pytest.raises(ConciseApplicationError, match="MissingReturn"):
        compile_project(
            entry,
            emit_native=True,
            output=tmp_path / "main",
            require_interface_lock=False,
        )


@pytest.mark.parametrize(
    "application",
    ("concise_json", "productive_ndjson", "productive_csv", "productive_grep"),
)
def test_all_existing_applications_use_direct_canonical_lowering(application: str) -> None:
    from merlo.compiler import compile_project

    base = Path("src/merlo/programs") if application == "concise_json" else Path("tools/benchmarks/merlo/programs")
    entry = base / application / "app" / "main.mlo"
    compilation = compile_project(entry, require_interface_lock=False)

    assert compilation.hir.source == compilation.elaborated.canonical_source
    assert compilation.artifacts["hir"].parent_digest == compilation.artifacts["canonical"].digest


def test_direct_json_pipeline_executes_recursive_json(tmp_path: Path) -> None:
    from merlo.compiler import compile_project

    payload = tmp_path / "input.json"
    payload.write_text('{"a":[1,true,null]}', encoding="utf-8")
    output = tmp_path / "json"
    compilation = compile_project(
        Path("src/merlo/programs/concise_json/app/main.mlo"),
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
