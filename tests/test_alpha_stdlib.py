from __future__ import annotations

from pathlib import Path

from merlo.modules import ModuleGraph, STDLIB_MODULES


STANDARD_NAMES = (
    "std.core",
    "std.option",
    "std.result",
    "std.text",
    "std.bytes",
    "std.collections",
    "std.io",
    "std.fs",
    "std.cli",
    "std.time",
    "std.random",
    "std.json",
    "std.net",
    "std.http",
)


def test_all_standard_modules_load_through_project_graph(tmp_path: Path) -> None:
    imports = "\n".join(f"use {name}" for name in STANDARD_NAMES)
    entry = tmp_path / "main.mlo"
    entry.write_text(
        "module app.main\n\n"
        + imports
        + "\n\nexport fn main() -> Unit:\n    return Unit\n",
        encoding="utf-8",
    )

    graph = ModuleGraph.load(entry)

    assert {module.name for module in graph.modules} == {*STANDARD_NAMES, "app.main"}
    assert all(name in STDLIB_MODULES for name in STANDARD_NAMES)
    assert all(Path(STDLIB_MODULES[name]).is_file() for name in STANDARD_NAMES)


def test_json_path_builder_and_streaming_apis_are_source_symbols() -> None:
    expected = {
        "std.json": {"parse", "format", "required", "optional"},
        "std.fs": {"path_join", "path_normalize", "fs_read_chunk", "fs_write_chunk"},
        "std.text": {"text_builder_new", "text_builder_append", "parse_uint"},
        "std.bytes": {"bytes_builder_new", "bytes_builder_append", "bytes_hex"},
        "std.net": {"tcp_connect", "tcp_send_all", "tcp_receive"},
        "std.http": {"http_encode_request", "http_parse_response", "http_request_once"},
    }

    for name, symbols in expected.items():
        module = ModuleGraph.load(STDLIB_MODULES[name]).module(name)
        assert symbols <= {symbol.name for symbol in module.symbols if symbol.exported}


def test_source_stdlib_has_no_domain_runtime_intrinsics() -> None:
    forbidden = ("merlo_json_", "merlo_csv_", "merlo_http_", "json_parse")
    for name in STANDARD_NAMES:
        source = Path(STDLIB_MODULES[name]).read_text(encoding="utf-8")
        assert not any(token in source for token in forbidden)
