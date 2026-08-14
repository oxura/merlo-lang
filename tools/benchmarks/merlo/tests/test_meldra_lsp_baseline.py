from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tools.benchmarks.merlo.lsp_baseline import (
    ERROR,
    OK,
    UNMEASURED,
    LspConfig,
    RenameTask,
    WorkspaceEditError,
    apply_workspace_edit,
    parse_workspace_edit,
    run_rename_task,
)


_FAKE_SERVER = r'''
import json
import sys


def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        name, value = line.decode("ascii").split(":", 1)
        headers[name.lower()] = value.strip()
    remaining = int(headers["content-length"])
    chunks = []
    while remaining:
        chunk = sys.stdin.buffer.read(remaining)
        if not chunk:
            raise EOFError
        chunks.append(chunk)
        remaining -= len(chunk)
    return json.loads(b"".join(chunks).decode("utf-8"))


def send(value):
    body = json.dumps(value, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def respond(request, result):
    send({"jsonrpc": "2.0", "id": request["id"], "result": result})


mode = sys.argv[1]
if mode == "malformed":
    read_message()
    sys.stdout.buffer.write(b"Content-Length: 4\r\n\r\n{bad")
    sys.stdout.buffer.flush()
    raise SystemExit(0)

opened_uri = None
while True:
    request = read_message()
    if request is None:
        break
    method = request.get("method")
    if method == "initialize":
        respond(request, {"capabilities": {"renameProvider": {"prepareProvider": True}}})
    elif method == "textDocument/didOpen":
        opened_uri = request["params"]["textDocument"]["uri"]
        send({
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": opened_uri, "diagnostics": []},
        })
        send({
            "jsonrpc": "2.0",
            "method": "window/logMessage",
            "params": {"type": 3, "message": "fake server ready"},
        })
    elif method == "textDocument/prepareRename":
        respond(request, {
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 3},
            },
            "placeholder": "foo",
        })
    elif method == "textDocument/rename":
        new_name = request["params"]["newName"]
        respond(request, {
            "documentChanges": [{
                "textDocument": {"uri": opened_uri, "version": 1},
                "edits": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 3},
                        },
                        "newText": new_name,
                    },
                    {
                        "range": {
                            "start": {"line": 1, "character": 6},
                            "end": {"line": 1, "character": 9},
                        },
                        "newText": new_name,
                    },
                ],
            }],
        })
    elif method == "shutdown":
        respond(request, None)
    elif method == "exit":
        break
'''


def _server(tmp_path: Path) -> Path:
    server = tmp_path / "fake_lsp.py"
    server.write_text(_FAKE_SERVER, encoding="utf-8")
    return server


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "sample.py"
    source.write_text("foo = 1\nprint(foo)\n", encoding="utf-8", newline="")
    return source


def test_rename_preview_is_immutable_and_edit_can_be_applied(tmp_path: Path) -> None:
    server = _server(tmp_path)
    source = _source(tmp_path)
    before = source.read_bytes()
    config = LspConfig((sys.executable, str(server), "ok"), tmp_path, "python")

    report = run_rename_task(config, RenameTask("sample.py", 0, 1, "bar"))

    assert report.status == OK
    assert report.edit_count == 2
    assert report.edit_files == ("sample.py",)
    assert report.workspace_edit is not None
    assert report.edit is not None
    assert len(report.diagnostics) == 1
    assert report.messages[0]["message"] == "fake server ready"
    assert source.read_bytes() == before
    assert report.to_json() == report.to_json()
    assert list(json.loads(report.to_json())) == sorted(json.loads(report.to_json()))

    applied = apply_workspace_edit(
        report.edit, tmp_path, expected_versions={source.as_uri(): 1}
    )
    assert applied.files == ("sample.py",)
    assert applied.edit_count == 2
    assert source.read_bytes() == b"bar = 1\nprint(bar)\n"


def test_missing_server_is_unmeasured(tmp_path: Path) -> None:
    _source(tmp_path)
    config = LspConfig((str(tmp_path / "missing-language-server"),), tmp_path, "python")

    report = run_rename_task(config, RenameTask("sample.py", 0, 1, "bar"))

    assert report.status == UNMEASURED
    assert report.edit_count == 0
    assert "unavailable" in (report.error or "")


def test_malformed_response_is_error(tmp_path: Path) -> None:
    server = _server(tmp_path)
    _source(tmp_path)
    config = LspConfig(
        (sys.executable, str(server), "malformed"),
        tmp_path,
        "python",
        timeout_seconds=1,
    )

    report = run_rename_task(config, RenameTask("sample.py", 0, 1, "bar"))

    assert report.status == ERROR
    assert "malformed JSON-RPC response" in (report.error or "")


def test_apply_rejects_traversal_without_mutating_files(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "inside.py"
    inside.write_text("inside\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    edit = parse_workspace_edit({
        "changes": {
            outside.as_uri(): [{
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 7},
                },
                "newText": "changed",
            }]
        }
    })

    with pytest.raises(WorkspaceEditError, match="escapes root"):
        apply_workspace_edit(edit, root)

    assert inside.read_text(encoding="utf-8") == "inside\n"
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_apply_rejects_stale_and_overlapping_edits(tmp_path: Path) -> None:
    source = _source(tmp_path)
    stale = parse_workspace_edit({
        "documentChanges": [{
            "textDocument": {"uri": source.as_uri(), "version": 3},
            "edits": [{
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 3},
                },
                "newText": "bar",
            }],
        }]
    }, tmp_path)
    with pytest.raises(WorkspaceEditError, match="stale document version"):
        apply_workspace_edit(stale, tmp_path, expected_versions={source.as_uri(): 2})

    overlapping = parse_workspace_edit({
        "changes": {
            source.as_uri(): [
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 3},
                    },
                    "newText": "bar",
                },
                {
                    "range": {
                        "start": {"line": 0, "character": 2},
                        "end": {"line": 0, "character": 5},
                    },
                    "newText": "z",
                },
            ]
        }
    }, tmp_path)
    with pytest.raises(WorkspaceEditError, match="overlapping"):
        apply_workspace_edit(overlapping, tmp_path)

    assert source.read_bytes() == b"foo = 1\nprint(foo)\n"
