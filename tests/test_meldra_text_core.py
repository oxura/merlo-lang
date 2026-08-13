import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from merlo.native_c_backend import CEmitter, compile_c_source
from merlo.native_differential import evaluate_hir, evaluate_mir
from merlo.native_hir import compile_native_hir
from merlo.performance_frontend import (
    PerformanceCompileError,
    compile_performance_source,
)
from merlo.performance_opt import optimize_mir
from merlo.text_core import (
    text_abi_manifest,
    text_hir_manifest,
    text_mir_manifest,
)
from merlo.text_builder_core import (
    text_builder_abi_manifest,
    text_builder_hir_manifest,
    text_builder_mir_manifest,
)
from merlo.text_builder_experiment import (
    DIRECT_CALL_SOURCE,
    JSON_ENCODER_SOURCE,
    STATE_MACHINE_SOURCE,
    TEXT_BUILDER_INVALID_CASES,
    TEXT_BUILDER_VALID_CASES,
    _json_expected,
    _reference_state,
    _valid_cases as _text_builder_valid_cases,
    validate_text_builder_report,
)
from merlo.text_core_experiment import (
    BOUNDARY_SOURCE,
    ROUNDTRIP_SOURCE,
    TEXT_INVALID_CASES,
    TEXT_INVALID_FAMILIES,
    TEXT_VALID_CASES,
    TEXT_VALID_FAMILIES,
    UTF8_INSPECTOR_SOURCE,
    _invalid_corpus,
    _pack,
    _shrink_bytes,
    _valid_corpus,
    validate_text_report,
)


def _evaluate_all(source: str, arguments: tuple[int, ...]):
    hir = compile_native_hir(source, path="text-test.meldra")
    mir = compile_performance_source(
        source, path="text-test.meldra"
    ).mir
    optimized, _ = optimize_mir(mir)
    return (
        hir,
        mir,
        optimized,
        evaluate_hir(hir, arguments),
        evaluate_mir(mir, arguments),
        evaluate_mir(optimized, arguments),
    )




def test_utf8_inspector_valid_and_invalid_surfaces_agree():
    valid = "AЖ😀".encode("utf-8")
    invalid = b"AB\xe2(\xa1"
    for data, expected in (
        (valid, (1 << 63) | (3 << 32) | len(valid)),
        (invalid, 2),
    ):
        results = _evaluate_all(
            UTF8_INSPECTOR_SOURCE,
            (_pack(data), len(data), 1),
        )[3:]
        assert [result.status for result in results] == ["OK"] * 3
        assert [result.return_value for result in results] == [
            expected
        ] * 3
        assert all(result.frees == 1 for result in results)
        assert all(result.finish_copies == 0 for result in results)


def test_utf8_inspector_native_binary_reports_same_checksum(tmp_path: Path):
    data = "A😀".encode("utf-8")
    expected = (1 << 63) | (2 << 32) | len(data)
    mir = compile_performance_source(
        UTF8_INSPECTOR_SOURCE, path="native-text-test.meldra"
    ).mir
    optimized, _ = optimize_mir(mir)
    build = compile_c_source(
        CEmitter(optimized, runtime_arguments=True).emit(),
        output_dir=tmp_path,
        stem="program",
    )
    if build.binary_path is None:
        pytest.skip(build.stderr)
    completed = subprocess.run(
        [build.binary_path, str(_pack(data)), str(len(data)), "1"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0
    assert int(completed.stdout.strip()) == expected
    assert "MELDRA_ALLOCATIONS=1" in completed.stderr
    assert "MELDRA_FREES=1" in completed.stderr
    assert "MELDRA_PAYLOAD_COPIES=0" in completed.stderr


def test_text_bytes_roundtrip_has_one_allocation_and_no_payload_copy():
    _hir, _mir, optimized, hir_result, mir_result, optimized_result = (
        _evaluate_all(ROUNDTRIP_SOURCE, (0x1F600,))
    )
    for result in (hir_result, mir_result, optimized_result):
        assert result.status == "OK"
        assert result.return_value == 8
        assert result.allocations == 1
        assert result.frees == 1
        assert result.finish_copies == 0
    operations = [
        instruction.op
        for function in optimized.functions
        for block in function.blocks
        for instruction in block.instructions
    ]
    assert "text_to_bytes_transfer" in operations
    assert "payload_copy" not in operations


def test_loop_local_text_drops_every_iteration(tmp_path: Path):
    source = """fn main(scalar: UInt64, repetitions: UInt64) -> UInt64:
 var checksum: UInt64 = 0
 var iteration: UInt64 = 0
 while iteration < repetitions:
  let text: Text = Text.from_scalar(scalar)
  let view: TextView = text.as_view()
  checksum = checksum + view.len_bytes()
  iteration = iteration + 1
 return checksum
"""
    _hir, _mir, optimized, hir_result, mir_result, optimized_result = (
        _evaluate_all(source, (0x1F600, 10))
    )
    for result in (hir_result, mir_result, optimized_result):
        assert result.status == "OK"
        assert result.return_value == 40
        assert result.allocations == 10
        assert result.frees == 10
    build = compile_c_source(
        CEmitter(optimized, runtime_arguments=True).emit(),
        output_dir=tmp_path,
        stem="loop-text",
    )
    if build.binary_path is None:
        pytest.skip(build.stderr)
    completed = subprocess.run(
        [build.binary_path, str(0x1F600), "10"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "40"
    assert "MELDRA_ALLOCATIONS=10" in completed.stderr
    assert "MELDRA_FREES=10" in completed.stderr


def test_textview_rejects_non_boundary_slice():
    _hir, _mir, _optimized, hir_result, mir_result, optimized_result = (
        _evaluate_all(BOUNDARY_SOURCE, (0x1F600,))
    )
    assert hir_result.error_kind == "TextSliceNotOnUtf8Boundary"
    assert mir_result.error_kind == "TextSliceNotOnUtf8Boundary"
    assert optimized_result.error_kind == "TextSliceNotOnUtf8Boundary"


def test_decode_and_live_view_obligations_are_compile_time_errors():
    unmatched = """fn main(n: UInt64) -> UInt64:
 let data: Bytes = Bytes.new(n)
 let decoded: Utf8Decode = Text.from_utf8(move(data))
 return n
"""
    with pytest.raises(
        PerformanceCompileError,
        match="must be handled by an exhaustive",
    ):
        compile_performance_source(unmatched)

    live_view = """fn main(scalar: UInt64) -> UInt64:
 let text: Text = Text.from_scalar(scalar)
 let view: TextView = text.as_view()
 let bytes: Bytes = text.into_bytes()
 return view.len_bytes() + bytes.len()
"""
    with pytest.raises(
        PerformanceCompileError,
        match="while TextView view is live",
    ):
        compile_performance_source(live_view)


def test_owned_text_and_borrowed_textview_cross_direct_calls():
    source = """fn identity(value: Text) -> Text:
 return value

fn whole(value: TextView) -> TextView:
 return value.slice_bytes(0, value.len_bytes())

fn main(scalar: UInt64) -> UInt64:
 let first: Text = Text.from_scalar(scalar)
 let second: Text = identity(move(first))
 let view: TextView = second.as_view()
 let returned: TextView = whole(view)
 let width: UInt64 = returned.scalar_width_at(0)
 let bytes: Bytes = second.into_bytes()
 return width + bytes.len()
"""
    results = _evaluate_all(source, (0x1F600,))[3:]
    assert [result.return_value for result in results] == [8, 8, 8]
    assert all(result.status == "OK" for result in results)


def test_text_contracts_and_corpus_shape_are_frozen():
    hir = compile_native_hir(
        UTF8_INSPECTOR_SOURCE, path="contract-text-test.meldra"
    )
    mir = compile_performance_source(
        UTF8_INSPECTOR_SOURCE, path="contract-text-test.meldra"
    ).mir
    optimized, _ = optimize_mir(mir)
    hir_manifest = text_hir_manifest(hir)
    mir_manifest = text_mir_manifest(optimized)
    abi = text_abi_manifest()
    assert hir_manifest["contract"] == "meldra.text-hir.v1"
    assert mir_manifest["validation"]["typed_decode_match_present"] is True
    assert mir_manifest["validation"]["scalar_iteration_present"] is True
    assert abi["bytes_to_text"]["payload_copies"] == 0
    assert abi["text_to_bytes"]["payload_copies"] == 0
    valid = _valid_corpus()
    invalid = _invalid_corpus()
    assert len(valid) == TEXT_VALID_CASES == 768
    assert len({case["family"] for case in valid}) == TEXT_VALID_FAMILIES == 21
    assert len(invalid) == TEXT_INVALID_CASES == 640
    assert len({case["family"] for case in invalid}) == TEXT_INVALID_FAMILIES == 21
    assert all(case["valid"] is True for case in valid)
    assert all(case["valid"] is False for case in invalid)


def test_text_failure_shrinker_is_deterministic():
    assert _shrink_bytes(
        b"abc", lambda candidate: b"b" in candidate
    ) == b"b"


def test_text_decision_artifact_is_supported_and_integral():
    report = json.loads(
        Path(
            "benchmarks/meldra_text_core_sprint.json"
        ).read_text(encoding="utf-8")
    )
    assert report["status"] == "TEXT_CORE_SUPPORTED"
    assert report["decision"]["gate_failures"] == []
    assert validate_text_report(report) == []
    expected_hash = report.pop("artifact_payload_sha256")
    payload = json.dumps(
        report, sort_keys=True, separators=(",", ":")
    )
    assert hashlib.sha256(payload.encode()).hexdigest() == expected_hash


def test_text_builder_state_machine_agrees_across_all_surfaces(
    tmp_path: Path,
):
    arguments = (65, 0x7FF, 0x800, 0x1F600, 31, 12)
    expected = _reference_state(arguments)
    hir, mir, optimized, hir_result, mir_result, optimized_result = (
        _evaluate_all(STATE_MACHINE_SOURCE, arguments)
    )
    for result in (hir_result, mir_result, optimized_result):
        assert result.status == "OK"
        assert result.return_value == expected["return_value"]
        assert (
            result.required_append_bytes
            == expected["required_append_bytes"]
        )
        assert result.finish_copies == 0
    build = compile_c_source(
        CEmitter(optimized, runtime_arguments=True).emit(),
        output_dir=tmp_path,
        stem="text-builder-state",
    )
    if build.binary_path is None:
        pytest.skip(build.stderr)
    completed = subprocess.run(
        [build.binary_path, *(str(value) for value in arguments)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 0
    assert int(completed.stdout.strip()) == expected["return_value"]
    assert "MELDRA_BUILDER_FINISH_COPIES=0" in completed.stderr
    assert (
        "MELDRA_TEXT_BUILDER_REQUIRED_APPEND_BYTES="
        f"{expected['required_append_bytes']}"
    ) in completed.stderr
    assert text_builder_hir_manifest(hir)[
        "representation"
    ]["finish"] == "ownership transfer directly to Text"
    assert text_builder_mir_manifest(optimized)[
        "validation"
    ]["finish_zero_copy"] is True
    assert text_builder_abi_manifest()["finish"][
        "validation_passes"
    ] == 0


def test_text_builder_direct_owned_calls_and_json_encoder():
    direct = _evaluate_all(DIRECT_CALL_SOURCE, (0x1F600,))[3:]
    assert [result.return_value for result in direct] == [5, 5, 5]
    arguments = (34, 92, 10, 0x1F600, 4)
    _expected_bytes, expected = _json_expected(arguments)
    encoded = _evaluate_all(JSON_ENCODER_SOURCE, arguments)[3:]
    assert all(result.status == "OK" for result in encoded)
    assert [result.return_value for result in encoded] == [
        expected,
        expected,
        expected,
    ]
    assert all(result.finish_copies == 0 for result in encoded)


def test_text_builder_invalid_diagnostics_and_corpus_shape():
    invalid_sources = (
        """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 let view: TextView = builder.as_view()
 builder.push_scalar(value)
 return view.len_bytes()
""",
        """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 builder.extend(builder.as_view())
 return value
""",
        """fn main(value: UInt64) -> UInt64:
 let bytes: Bytes = Bytes.new(value)
 let raw: BytesView = bytes.slice(0, value)
 let builder: TextBuilder = TextBuilder.new()
 builder.extend(raw)
 return builder.len_bytes()
""",
    )
    for source in invalid_sources:
        with pytest.raises(PerformanceCompileError):
            compile_performance_source(source)
    runtime = """fn main(value: UInt64) -> UInt64:
 let builder: TextBuilder = TextBuilder.new()
 builder.push_scalar(value)
 let text: Text = builder.finish()
 return text.len_bytes()
"""
    result = evaluate_mir(
        compile_performance_source(runtime).mir, (0xD800,)
    )
    assert result.status == "ERROR"
    assert result.error_kind == "TextBuilderInvalidUnicodeScalar"
    cases = _text_builder_valid_cases()
    assert len(cases) == TEXT_BUILDER_VALID_CASES == 640
    assert len({case["family"] for case in cases}) == 32
    assert TEXT_BUILDER_INVALID_CASES == 320


def test_text_builder_decision_artifact_is_integral():
    report = json.loads(
        Path("benchmarks/meldra_text_builder.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["status"] in {
        "TEXT_BUILDER_SUPPORTED",
        "TEXT_BUILDER_INCOMPLETE",
        "TEXT_BUILDER_SAFETY_DEFECT",
    }
    assert validate_text_builder_report(report) == report["decision"][
        "gate_failures"
    ]
    expected_hash = report.pop("artifact_payload_sha256")
    payload = json.dumps(
        report, sort_keys=True, separators=(",", ":")
    )
    assert hashlib.sha256(payload.encode()).hexdigest() == expected_hash
