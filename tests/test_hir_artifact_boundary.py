from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path


from merlo.native_c_backend import compile_c_source
from merlo.representation_c_backend import emit_general_c
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import (
    lower_rir_to_performance_mir,
    optimize_general_mir,
)
from merlo.structured_hir_v2 import (
    StructuredHIRProgram,
    compile_canonical_hir,
    compile_structured_hir,
)
from merlo.surface_elaborator import elaborate_surface
from merlo.surface_parser import parse_surface


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    "main(input: BytesView) -> UInt64:\n"
    "    value: UInt64 = 41\n"
    "    value + 1\n"
)


def _hir(source: str = SOURCE) -> StructuredHIRProgram:
    canonical = elaborate_surface(
        parse_surface(source, path="artifact-boundary.mlo")
    ).canonical
    return compile_canonical_hir(canonical)


def _generated(hir: StructuredHIRProgram) -> str:
    representation = lower_structured_hir_to_rir(hir)
    mir = optimize_general_mir(
        lower_rir_to_performance_mir(hir, representation)
    )
    return emit_general_c(hir, representation, mir).source

def _typed_nodes(value: object) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    if isinstance(value, dict):
        if isinstance(value.get("kind"), str) and isinstance(
            value.get("children"), list
        ):
            result.append(value)
        for item in value.values():
            result.extend(_typed_nodes(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_typed_nodes(item))
    return result




def test_hir_json_roundtrip_closes_backend_artifact() -> None:
    original = _hir()
    restored = StructuredHIRProgram.from_json(original.to_json())

    assert restored.to_dict() == original.to_dict()
    assert restored.to_json() == original.to_json()
    assert restored.digest == original.digest
    assert _generated(restored) == _generated(original)


def test_hir_roundtrip_in_new_process_preserves_digest_and_c() -> None:
    original = _hir()
    source = _generated(original)
    script = """
import hashlib
import json
import sys
from merlo.structured_hir_v2 import StructuredHIRProgram
from merlo.representation_ir import lower_structured_hir_to_rir
from merlo.representation_mir import lower_rir_to_performance_mir, optimize_general_mir
from merlo.representation_c_backend import emit_general_c
hir = StructuredHIRProgram.from_json(sys.stdin.read())
rir = lower_structured_hir_to_rir(hir)
mir = optimize_general_mir(lower_rir_to_performance_mir(hir, rir))
generated = emit_general_c(hir, rir, mir).source
print(json.dumps({"hir": hir.digest, "c": hashlib.sha256(generated.encode()).hexdigest()}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        input=original.to_json(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    evidence = json.loads(completed.stdout)
    assert evidence == {
        "hir": original.digest,
        "c": hashlib.sha256(source.encode()).hexdigest(),
    }


def test_original_and_roundtripped_hir_produce_identical_binaries(
    tmp_path: Path,
) -> None:
    original = _hir()
    restored = StructuredHIRProgram.from_json(original.to_json())
    original_c = _generated(original)
    restored_c = _generated(restored)
    assert original_c == restored_c

    first = compile_c_source(
        original_c,
        output_dir=tmp_path / "original",
        stem="artifact-closure",
    )
    second = compile_c_source(
        restored_c,
        output_dir=tmp_path / "restored",
        stem="artifact-closure",
    )
    assert first.status == second.status == "MEASURED"
    assert first.binary_path is not None and second.binary_path is not None
    assert hashlib.sha256(Path(first.binary_path).read_bytes()).hexdigest() == (
        hashlib.sha256(Path(second.binary_path).read_bytes()).hexdigest()
    )


def test_typed_hir_changes_are_digest_and_codegen_bound() -> None:
    original = _hir()
    payload = copy.deepcopy(original.to_dict())
    literal = payload["functions"][0]["body"][0]["children"][0]
    assert isinstance(literal, dict)
    attributes = literal["attributes"]
    assert isinstance(attributes, dict)
    attributes["value"] = 42
    changed = StructuredHIRProgram.from_dict(payload)

    assert changed.digest != original.digest
    assert _generated(changed) != _generated(original)


def test_every_serialized_typed_node_is_digest_bound() -> None:
    original = _hir()
    nodes = _typed_nodes(original.to_dict()["functions"])
    assert nodes
    for index in range(len(nodes)):
        payload = copy.deepcopy(original.to_dict())
        changed_node = _typed_nodes(payload["functions"])[index]
        source = changed_node["source"]
        assert isinstance(source, dict)
        source["path"] = f"digest-probe-{index}.mlo"
        changed = StructuredHIRProgram.from_dict(payload)
        assert changed.digest != original.digest


def test_backend_never_reads_legacy_artifacts_or_raw_source() -> None:
    hir = _hir()
    assert not hasattr(hir, "native_module")
    assert not hasattr(hir, "native_syntax_json")
    assert "merlo_fn_main" in _generated(hir)
    backend_source = (
        ROOT / "src" / "merlo" / "representation_c_backend.py"
    ).read_text(encoding="utf-8")
    assert "ast.parse(" not in backend_source
    assert "native_syntax" not in backend_source
    assert "native_module" not in backend_source
    assert "hir.source" not in backend_source
    assert "validate_ffi(hir.source" not in backend_source




def test_typed_ffi_artifact_roundtrips_builds_and_runs(tmp_path: Path) -> None:
    hir = compile_structured_hir(
        'extern "C" fn abs(value: Int32) -> Int32\n\n'
        "fn main(input: BytesView) -> UInt64:\n"
        "    return 7\n",
        path="ffi-artifact.mlo",
    )
    restored = StructuredHIRProgram.from_json(hir.to_json())
    assert restored.ffi_program == hir.ffi_program
    assert restored.ffi_program.extern_functions[0].prototype == (
        "extern int32_t abs(int32_t value);"
    )
    generated = _generated(restored)
    assert "extern int32_t abs(int32_t value);" in generated

    build = compile_c_source(
        generated,
        output_dir=tmp_path,
        stem="ffi-artifact",
    )
    assert build.status == "MEASURED", build.stderr
    assert build.binary_path is not None
    completed = subprocess.run(
        [build.binary_path],
        input=b"",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert b"OK result=7" in completed.stdout


def test_ffi_backend_visible_change_changes_hir_digest() -> None:
    hir = compile_structured_hir(
        'extern "C" fn abs(value: Int32) -> Int32\n\n'
        "fn main(input: BytesView) -> UInt64:\n"
        "    return 7\n"
    )
    payload = copy.deepcopy(hir.to_dict())
    payload["ffi"]["extern_functions"][0]["safe_wrapper"] = True
    changed = StructuredHIRProgram.from_dict(payload)
    assert changed.digest != hir.digest
