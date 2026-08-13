from __future__ import annotations

import json

import pytest

from merlo.json_streaming import (
    JSON_STREAMING_LIMITATIONS,
    JsonTokenError,
    json_streaming_mir_manifest,
    tokenize_json,
)
from merlo.native_c_backend import find_c_compiler
from merlo.native_differential import run_differential
from merlo.native_hir import compile_native_hir, lower_native_hir_to_performance
from merlo.performance_opt import optimize_mir


SOURCE = """fn main(data: BytesView) -> UInt64:
    return json_token_checksum(data)
"""


@pytest.mark.parametrize(
    ("payload", "checksum"),
    [
        (b"null", 1883120304953510569),
        (b'{"a":[1,true,"x\\n"]}', 12238494697268198425),
        (b'"a\\u20ac\\ud83d\\ude00"', 1253031874760682182),
    ],
)
def test_token_checksum_is_frozen(payload: bytes, checksum: int) -> None:
    assert json.loads(payload) is not None or payload == b"null"
    assert tokenize_json(payload).checksum == checksum


def test_unescaped_strings_are_zero_allocation_views() -> None:
    result = tokenize_json(b'{"key":"plain UTF-8: \xe2\x82\xac"}')
    assert result.stats.unescaped_strings == 2
    assert result.stats.escaped_strings == 0
    assert result.stats.text_builder_allocations == 0
    assert result.stats.text_builder_reallocations == 0
    assert result.stats.text_builder_finish_copies == 0


def test_escaped_strings_use_bounded_builder_growth_and_zero_copy_finish() -> None:
    result = tokenize_json(b'"prefix-\\n-\\u20ac-\\ud83d\\ude00-suffix"')
    assert result.stats.escaped_strings == 1
    assert result.stats.text_builder_allocations == 1
    assert result.stats.text_builder_reallocations >= 1
    assert result.stats.text_builder_growth_copied_bytes > 0
    assert result.stats.text_builder_semantic_bytes == len("prefix-\n-€-😀-suffix".encode())
    assert result.stats.text_builder_finish_copies == 0
    assert result.stats.text_builder_frees == 1 + result.stats.text_builder_reallocations


@pytest.mark.parametrize(
    ("payload", "kind", "offset"),
    [
        (b"", "JsonTruncatedInput", 0),
        (b'{"a" 1}', "JsonExpectedColon", 5),
        (b"[1 2]", "JsonExpectedComma", 3),
        (b'"a\\q"', "JsonInvalidEscape", 2),
        (b'"\\ud800"', "JsonInvalidUnicodeEscape", 1),
        (b"01", "JsonMalformedNumber", 0),
        (b"[1}", "JsonDelimiterMismatch", 2),
        (b"\xff", "JsonInvalidUtf8", 0),
    ],
)
def test_invalid_json_has_typed_byte_offset(
    payload: bytes, kind: str, offset: int
) -> None:
    with pytest.raises(JsonTokenError) as raised:
        tokenize_json(payload)
    assert (raised.value.kind, raised.value.offset) == (kind, offset)


def test_nesting_limit_is_explicit_and_deterministic() -> None:
    accepted = b"[" * 64 + b"0" + b"]" * 64
    assert tokenize_json(accepted).stats.token_count == 129
    with pytest.raises(JsonTokenError) as raised:
        tokenize_json(b"[" * 65 + b"0" + b"]" * 65)
    assert (raised.value.kind, raised.value.offset) == (
        "JsonNestingDepthExceeded",
        64,
    )
    assert JSON_STREAMING_LIMITATIONS["maximum_nesting"] == 64


def test_frontend_records_streaming_mir_contract_and_hir_identity() -> None:
    hir = compile_native_hir(SOURCE, path="streaming_contract.meldra")
    mir = lower_native_hir_to_performance(hir)
    manifest = json_streaming_mir_manifest(mir)
    assert all(manifest["validation"].values())
    event = manifest["events"][0]
    assert event["attributes"] == {
        "constructs_ast": False,
        "consumer": "deterministic_fnv1a64_v1",
        "receiver_kind": "BytesView",
        "streaming": True,
    }
    assert event["source"]["path"] == "streaming_contract.meldra"
    assert all(symbol.symbol_id and symbol.revision_id for symbol in hir.symbols)
    text_view_source = """fn main(data: TextView) -> UInt64:
    return json_token_checksum(data)
"""
    text_view_mir = lower_native_hir_to_performance(
        compile_native_hir(
            text_view_source, path="streaming_text_view_contract.meldra"
        )
    )
    text_view_event = json_streaming_mir_manifest(text_view_mir)["events"][0]
    assert text_view_event["attributes"]["receiver_kind"] == "TextView"


def test_optimizer_preserves_streaming_operation_metadata() -> None:
    mir = lower_native_hir_to_performance(
        compile_native_hir(SOURCE, path="streaming_opt.meldra")
    )
    optimized, _ = optimize_mir(mir)
    assert json_streaming_mir_manifest(optimized)["events"] == (
        json_streaming_mir_manifest(mir)["events"]
    )


def test_native_valid_and_invalid_paths_match_every_compiler_level(tmp_path) -> None:
    if find_c_compiler() is None:
        pytest.skip("C compiler unavailable")
    valid = run_differential(
        SOURCE,
        (b'{"borrowed":"x\\n","n":1.5e2}',),
        path="streaming_native_valid.meldra",
        artifact_dir=tmp_path / "valid",
    )
    invalid = run_differential(
        SOURCE,
        (b'{"borrowed" 1}',),
        path="streaming_native_invalid.meldra",
        artifact_dir=tmp_path / "invalid",
    )
    assert valid.ok
    assert invalid.ok
    assert {
        (item.status, item.error_kind, item.error_offset)
        for _level, item in invalid.observations
    } == {("ERROR", "JsonExpectedColon", 12)}
