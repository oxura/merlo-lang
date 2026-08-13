from __future__ import annotations

import json
from pathlib import Path


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "app" / "main.mlo"
    path.parent.mkdir(parents=True)
    path.write_text(
        "module app.main\n\n"
        "export enum AppError:\n"
        "    Failed\n\n"
        "fn helper(value: UInt64) -> Text:\n"
        "    return \"ok\"\n\n"
        "export task main(path: Path) -> Result[Text,AppError]:\n"
        "    uses console.write\n"
        "    console.write(\"main\")\n"
        "    return Ok(helper(1))\n",
        encoding="utf-8",
    )
    return path


def test_lsp_transcript_uses_semantic_world_and_incremental_snapshots(tmp_path: Path) -> None:
    from merlo.lsp import MerloLanguageServer
    from merlo.semantic_world import SemanticWorld

    source = _source(tmp_path)
    world = SemanticWorld.build(source, require_interface_lock=False)
    server = MerloLanguageServer(source, world=world)
    uri = source.as_uri()

    initialize = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert initialize[0]["result"]["capabilities"]["hoverProvider"] is True
    assert server.handle_message({"jsonrpc": "2.0", "method": "initialized", "params": []}) == []
    opened = server.handle_message({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": uri, "languageId": "merlo", "version": 1, "text": source.read_text(encoding="utf-8")}}})
    assert any(item.get("method") == "textDocument/publishDiagnostics" for item in opened)

    hover = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "textDocument/hover", "params": {"textDocument": {"uri": uri}, "position": {"line": 11, "character": 15}}})
    assert "helper" in hover[0]["result"]["contents"]["value"]
    definition = server.handle_message({"jsonrpc": "2.0", "id": 3, "method": "textDocument/definition", "params": {"textDocument": {"uri": uri}, "position": {"line": 11, "character": 15}}})
    assert definition[0]["result"][0]["uri"] == uri
    references = server.handle_message({"jsonrpc": "2.0", "id": 4, "method": "textDocument/references", "params": {"textDocument": {"uri": uri}, "position": {"line": 11, "character": 15}, "context": {"includeDeclaration": True}}})
    assert references[0]["result"]
    renamed = server.handle_message({"jsonrpc": "2.0", "id": 5, "method": "textDocument/rename", "params": {"textDocument": {"uri": uri}, "position": {"line": 11, "character": 15}, "newName": "assist"}})
    assert renamed[0]["result"]["changes"][uri]
    symbols = server.handle_message({"jsonrpc": "2.0", "id": 6, "method": "workspace/symbol", "params": {"query": "helper"}})
    assert symbols[0]["result"][0]["name"] == "helper"
    formatted = server.handle_message({"jsonrpc": "2.0", "id": 7, "method": "textDocument/formatting", "params": {"textDocument": {"uri": uri}, "options": {}}})
    assert formatted[0]["result"][0]["newText"].endswith("\n")

    stale = server.handle_message({"jsonrpc": "2.0", "method": "textDocument/didChange", "params": {"textDocument": {"uri": uri, "version": 1}, "contentChanges": [{"text": source.read_text(encoding="utf-8").replace("helper(1)", "missing(1)")}]}})
    stale_diagnostics = [item for item in stale if item.get("method") == "textDocument/publishDiagnostics"]
    assert stale_diagnostics and stale_diagnostics[0]["params"]["diagnostics"][0]["code"] == "StaleSnapshot"

    changed = server.handle_message({"jsonrpc": "2.0", "method": "textDocument/didChange", "params": {"textDocument": {"uri": uri, "version": 2}, "contentChanges": [{"text": source.read_text(encoding="utf-8").replace("helper(1)", "missing(1)")}]}})
    diagnostics = [item for item in changed if item.get("method") == "textDocument/publishDiagnostics"]
    assert diagnostics and any("UnknownSymbol" in item["params"]["diagnostics"][0]["message"] for item in diagnostics)
    assert server.handle_message({"jsonrpc": "2.0", "id": 8, "method": "shutdown", "params": None})[0]["result"] is None
    assert server.handle_message({"jsonrpc": "2.0", "method": "exit", "params": None}) == []
    assert server.exited is True


def test_lsp_stdio_framing_and_editor_metadata() -> None:
    from merlo.lsp import decode_message, encode_message

    payload = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    framed = encode_message(payload)
    assert framed.startswith(b"Content-Length:")
    assert decode_message(framed) == payload
    package = json.loads(Path("editors/vscode/package.json").read_text(encoding="utf-8"))
    grammar = json.loads(Path("editors/vscode/syntaxes/merlo.tmLanguage.json").read_text(encoding="utf-8"))
    assert package["contributes"]["grammars"][0]["scopeName"] == grammar["scopeName"]
    text = json.dumps(grammar)
    for scope in ("keyword.control.merlo", "storage.type.function.merlo", "constant.numeric.merlo", "comment.line.merlo"):
        assert scope in text

def test_lsp_utf16_columns_are_converted_for_non_bmp_prefix() -> None:
    from merlo.lsp import _codepoint_column, _utf16_column

    source = "😀 helper\n"
    assert _codepoint_column(source, 0, 3) == 2
    assert _utf16_column(source, 0, 2) == 3
