from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Sequence
from urllib.parse import unquote, urlsplit


OK = "OK"
UNMEASURED = "UNMEASURED"
ERROR = "ERROR"


class LspBaselineError(RuntimeError):
    """Base error for the external LSP baseline."""


class LspProtocolError(LspBaselineError):
    """The server violated JSON-RPC or LSP stdio framing."""


class WorkspaceEditError(LspBaselineError):
    """A WorkspaceEdit is malformed or unsafe to apply."""


@dataclass(frozen=True)
class LspConfig:
    argv: tuple[str, ...]
    root: Path
    language_id: str
    timeout_seconds: float = 10.0

    def __init__(
        self,
        argv: Sequence[str],
        root: str | Path,
        language_id: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        if isinstance(argv, (str, bytes)):
            raise ValueError("argv must be a sequence, not a shell command")
        object.__setattr__(self, "argv", tuple(argv))
        object.__setattr__(self, "root", Path(root).resolve())
        object.__setattr__(self, "language_id", language_id)
        object.__setattr__(self, "timeout_seconds", float(timeout_seconds))
        if not self.argv or any(not isinstance(item, str) or not item for item in self.argv):
            raise ValueError("argv must contain non-empty strings")
        if not self.language_id:
            raise ValueError("language_id is required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True)
class RenameTask:
    path: str | Path
    line: int
    character: int
    new_name: str
    version: int = 1
    source: str | None = None

    def __post_init__(self) -> None:
        if self.line < 0 or self.character < 0:
            raise ValueError("line and character must be non-negative")
        if not self.new_name:
            raise ValueError("new_name is required")
        if self.version < 0:
            raise ValueError("version must be non-negative")


@dataclass(frozen=True)
class LspTextEdit:
    start_line: int
    start_character: int
    end_line: int
    end_character: int
    new_text: str
    annotation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "range": {
                "start": {"line": self.start_line, "character": self.start_character},
                "end": {"line": self.end_line, "character": self.end_character},
            },
            "newText": self.new_text,
        }
        if self.annotation_id is not None:
            value["annotationId"] = self.annotation_id
        return value


@dataclass(frozen=True)
class WorkspaceDocumentEdit:
    uri: str
    edits: tuple[LspTextEdit, ...]
    version: int | None = None
    source_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "version": self.version,
            "edits": [edit.to_dict() for edit in self.edits],
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True)
class WorkspaceEdit:
    documents: tuple[WorkspaceDocumentEdit, ...]
    raw: Mapping[str, Any] = field(repr=False)

    @property
    def edit_count(self) -> int:
        return sum(len(document.edits) for document in self.documents)

    @property
    def uris(self) -> tuple[str, ...]:
        return tuple(document.uri for document in self.documents)

    def to_dict(self) -> dict[str, Any]:
        return {
            "documents": [document.to_dict() for document in self.documents],
            "raw": _json_copy(self.raw),
        }


@dataclass(frozen=True)
class ApplyResult:
    files: tuple[str, ...]
    edit_count: int

    def to_dict(self) -> dict[str, Any]:
        return {"files": list(self.files), "edit_count": self.edit_count}


@dataclass(frozen=True)
class LspRenameReport:
    status: str
    workspace_edit: Mapping[str, Any] | None
    edit_files: tuple[str, ...]
    edit_count: int
    diagnostics: tuple[Mapping[str, Any], ...]
    messages: tuple[Mapping[str, Any], ...]
    elapsed_ms: int
    stderr: str
    error: str | None = None
    prepare_result: Any = None
    edit: WorkspaceEdit | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.status not in (OK, UNMEASURED, ERROR):
            raise ValueError(f"unknown LSP baseline status: {self.status}")
        if self.edit_count < 0 or self.elapsed_ms < 0:
            raise ValueError("counts and timings must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace_edit": (
                _json_copy(self.workspace_edit) if self.workspace_edit is not None else None
            ),
            "edit_files": list(self.edit_files),
            "edit_count": self.edit_count,
            "diagnostics": [_json_copy(item) for item in self.diagnostics],
            "messages": [_json_copy(item) for item in self.messages],
            "elapsed_ms": self.elapsed_ms,
            "stderr": self.stderr,
            "error": self.error,
            "prepare_result": _json_copy(self.prepare_result),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _as_object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WorkspaceEditError(f"{name} must be an object")
    return value


def _as_nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkspaceEditError(f"{name} must be a non-negative integer")
    return value


def _parse_text_edit(value: Any) -> LspTextEdit:
    item = _as_object(value, "text edit")
    range_value = _as_object(item.get("range"), "text edit range")
    start = _as_object(range_value.get("start"), "range start")
    end = _as_object(range_value.get("end"), "range end")
    new_text = item.get("newText")
    if not isinstance(new_text, str):
        raise WorkspaceEditError("text edit newText must be a string")
    annotation_id = item.get("annotationId")
    if annotation_id is not None and not isinstance(annotation_id, str):
        raise WorkspaceEditError("annotationId must be a string")
    return LspTextEdit(
        _as_nonnegative_int(start.get("line"), "start line"),
        _as_nonnegative_int(start.get("character"), "start character"),
        _as_nonnegative_int(end.get("line"), "end line"),
        _as_nonnegative_int(end.get("character"), "end character"),
        new_text,
        annotation_id,
    )


def _uri_to_path(uri: str, root: Path) -> Path:
    parsed = urlsplit(uri)
    if parsed.scheme != "file" or parsed.query or parsed.fragment:
        raise WorkspaceEditError(f"only plain file URIs are allowed: {uri!r}")
    if parsed.netloc not in ("", "localhost"):
        raise WorkspaceEditError(f"remote file URI is not allowed: {uri!r}")
    raw_path = unquote(parsed.path)
    if os.name == "nt" and len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
        raw_path = raw_path[1:]
    path = Path(raw_path).resolve()
    root = root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise WorkspaceEditError(f"workspace edit escapes root: {uri!r}") from exc
    if not path.is_file():
        raise WorkspaceEditError(f"workspace edit target is not a file: {uri!r}")
    return path


def parse_workspace_edit(
    value: Mapping[str, Any], root: str | Path | None = None
) -> WorkspaceEdit:
    """Parse text-only LSP WorkspaceEdit data, optionally snapshotting its files."""
    raw = _as_object(value, "WorkspaceEdit")
    documents: list[tuple[str, int | None, tuple[LspTextEdit, ...]]] = []

    changes = raw.get("changes")
    if changes is not None:
        changes_object = _as_object(changes, "WorkspaceEdit.changes")
        for uri, edits in changes_object.items():
            if not isinstance(uri, str) or not isinstance(edits, list):
                raise WorkspaceEditError("WorkspaceEdit.changes has invalid entries")
            documents.append((uri, None, tuple(_parse_text_edit(edit) for edit in edits)))

    document_changes = raw.get("documentChanges")
    if document_changes is not None:
        if not isinstance(document_changes, list):
            raise WorkspaceEditError("WorkspaceEdit.documentChanges must be an array")
        for value_item in document_changes:
            item = _as_object(value_item, "document change")
            if "kind" in item:
                raise WorkspaceEditError("resource operations are not supported")
            text_document = _as_object(item.get("textDocument"), "textDocument")
            uri = text_document.get("uri")
            version = text_document.get("version")
            edits = item.get("edits")
            if not isinstance(uri, str) or not isinstance(edits, list):
                raise WorkspaceEditError("text document edit is malformed")
            if version is not None:
                version = _as_nonnegative_int(version, "document version")
            documents.append(
                (uri, version, tuple(_parse_text_edit(edit) for edit in edits))
            )

    if changes is None and document_changes is None:
        return WorkspaceEdit((), _json_copy(raw))

    root_path = Path(root).resolve() if root is not None else None
    seen: set[Path | str] = set()
    parsed_documents: list[WorkspaceDocumentEdit] = []
    for uri, version, edits in documents:
        if root_path is None:
            identity: Path | str = uri
            digest = None
        else:
            path = _uri_to_path(uri, root_path)
            identity = path
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if identity in seen:
            raise WorkspaceEditError(f"duplicate document in WorkspaceEdit: {uri!r}")
        seen.add(identity)
        parsed_documents.append(WorkspaceDocumentEdit(uri, edits, version, digest))
    parsed_documents.sort(key=lambda item: item.uri)
    return WorkspaceEdit(tuple(parsed_documents), _json_copy(raw))


def _position_to_offset(text: str, line: int, character: int) -> int:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    if line >= len(starts):
        raise WorkspaceEditError(f"line {line} is outside the document")
    start = starts[line]
    line_end = text.find("\n", start)
    if line_end < 0:
        line_end = len(text)
    if line_end > start and text[line_end - 1] == "\r":
        line_end -= 1
    current_units = 0
    for offset in range(start, line_end):
        if current_units == character:
            return offset
        current_units += 2 if ord(text[offset]) > 0xFFFF else 1
        if current_units > character:
            raise WorkspaceEditError("position splits a UTF-16 surrogate pair")
    if current_units == character:
        return line_end
    raise WorkspaceEditError(f"character {character} is outside line {line}")


def _expected_version(
    expected_versions: Mapping[str | Path, int], uri: str, path: Path, root: Path
) -> int | None:
    candidates: tuple[str | Path, ...] = (
        uri,
        path,
        str(path),
        path.relative_to(root),
        str(path.relative_to(root)),
    )
    for candidate in candidates:
        if candidate in expected_versions:
            return expected_versions[candidate]
    return None


def _render_document(text: str, edits: tuple[LspTextEdit, ...]) -> str:
    spans: list[tuple[int, int, str]] = []
    for edit in edits:
        start = _position_to_offset(text, edit.start_line, edit.start_character)
        end = _position_to_offset(text, edit.end_line, edit.end_character)
        if end < start:
            raise WorkspaceEditError("text edit range ends before it starts")
        spans.append((start, end, edit.new_text))
    spans.sort(key=lambda item: (item[0], item[1]))
    previous_end = -1
    previous_span: tuple[int, int, str] | None = None
    for span in spans:
        if span[0] < previous_end or (
            previous_span is not None
            and span[0] == span[1] == previous_span[0] == previous_span[1]
        ):
            raise WorkspaceEditError("WorkspaceEdit contains overlapping edits")
        previous_end = max(previous_end, span[1])
        previous_span = span
    rendered = text
    for start, end, replacement in reversed(spans):
        rendered = rendered[:start] + replacement + rendered[end:]
    return rendered


def _write_atomic(path: Path, data: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def apply_workspace_edit(
    workspace_edit: WorkspaceEdit | Mapping[str, Any],
    root: str | Path,
    *,
    expected_versions: Mapping[str | Path, int] | None = None,
) -> ApplyResult:
    """Validate and atomically apply a text-only edit, rolling back partial writes."""
    root_path = Path(root).resolve()
    parsed = (
        workspace_edit
        if isinstance(workspace_edit, WorkspaceEdit)
        else parse_workspace_edit(workspace_edit, root_path)
    )
    originals: dict[Path, bytes] = {}
    replacements: dict[Path, bytes] = {}
    modes: dict[Path, int] = {}

    for document in parsed.documents:
        path = _uri_to_path(document.uri, root_path)
        if path in originals:
            raise WorkspaceEditError(f"duplicate document target: {path}")
        original = path.read_bytes()
        if document.source_sha256 is not None:
            actual_digest = hashlib.sha256(original).hexdigest()
            if actual_digest != document.source_sha256:
                raise WorkspaceEditError(f"stale workspace edit for {path}")
        if document.version is not None and expected_versions is not None:
            actual_version = _expected_version(
                expected_versions, document.uri, path, root_path
            )
            if actual_version != document.version:
                raise WorkspaceEditError(
                    f"stale document version for {path}: expected "
                    f"{document.version}, got {actual_version}"
                )
        try:
            text = original.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceEditError(f"document is not UTF-8: {path}") from exc
        replacements[path] = _render_document(text, document.edits).encode("utf-8")
        originals[path] = original
        modes[path] = path.stat().st_mode

    changed: list[Path] = []
    try:
        for path in sorted(replacements, key=lambda item: str(item)):
            if replacements[path] == originals[path]:
                continue
            if path.read_bytes() != originals[path]:
                raise WorkspaceEditError(f"document changed during apply: {path}")
            _write_atomic(path, replacements[path], modes[path])
            changed.append(path)
    except BaseException as exc:
        rollback_errors: list[str] = []
        for path in reversed(changed):
            try:
                _write_atomic(path, originals[path], modes[path])
            except BaseException as rollback_exc:
                rollback_errors.append(f"{path}: {rollback_exc}")
        if rollback_errors:
            raise WorkspaceEditError(
                f"apply failed ({exc}); rollback failed: {'; '.join(rollback_errors)}"
            ) from exc
        raise WorkspaceEditError(f"apply failed and was rolled back: {exc}") from exc

    relative_files = tuple(
        str(path.relative_to(root_path)) for path in sorted(originals, key=lambda item: str(item))
    )
    return ApplyResult(relative_files, parsed.edit_count)


class _LspStdioClient:
    def __init__(self, config: LspConfig) -> None:
        self.config = config
        self.process: subprocess.Popen[bytes] | None = None
        self._incoming: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stderr_chunks: list[bytes] = []
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._request_id = 0
        self._initialized = False
        self.diagnostics: list[Mapping[str, Any]] = []
        self.messages: list[Mapping[str, Any]] = []

    @property
    def stderr(self) -> str:
        return b"".join(self._stderr_chunks).decode("utf-8", errors="replace")

    def start(self) -> None:
        self.process = subprocess.Popen(
            self.config.argv,
            cwd=self.config.root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            bufsize=0,
        )
        assert self.process.stdout is not None and self.process.stderr is not None
        self._reader_thread = threading.Thread(
            target=self._read_loop, args=(self.process.stdout,), daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop, args=(self.process.stderr,), daemon=True
        )
        self._reader_thread.start()
        self._stderr_thread.start()

    def _stderr_loop(self, stream: BinaryIO) -> None:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            self._stderr_chunks.append(chunk)

    @staticmethod
    def _read_framed_message(stream: BinaryIO) -> Mapping[str, Any] | None:
        headers: dict[str, str] = {}
        while True:
            line = stream.readline(65_537)
            if not line:
                if not headers:
                    return None
                raise LspProtocolError("unexpected EOF in LSP headers")
            if len(line) > 65_536:
                raise LspProtocolError("LSP header line is too long")
            if line in (b"\r\n", b"\n"):
                break
            try:
                name, raw_value = line.decode("ascii").split(":", 1)
            except (UnicodeDecodeError, ValueError) as exc:
                raise LspProtocolError("malformed LSP header") from exc
            name = name.strip().lower()
            if name in headers:
                raise LspProtocolError(f"duplicate LSP header: {name}")
            headers[name] = raw_value.strip()
        try:
            content_length = int(headers["content-length"])
        except (KeyError, ValueError) as exc:
            raise LspProtocolError("invalid Content-Length header") from exc
        if content_length < 0 or content_length > 64 * 1024 * 1024:
            raise LspProtocolError("invalid LSP message length")
        chunks: list[bytes] = []
        remaining = content_length
        while remaining:
            chunk = stream.read(remaining)
            if not chunk:
                raise LspProtocolError("unexpected EOF in LSP message body")
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
        try:
            message = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LspProtocolError("malformed JSON-RPC response") from exc
        if not isinstance(message, Mapping):
            raise LspProtocolError("JSON-RPC message must be an object")
        if message.get("jsonrpc") != "2.0":
            raise LspProtocolError("JSON-RPC version must be 2.0")
        return message

    def _read_loop(self, stream: BinaryIO) -> None:
        try:
            while True:
                message = self._read_framed_message(stream)
                if message is None:
                    self._incoming.put(("eof", None))
                    return
                self._incoming.put(("message", message))
        except BaseException as exc:
            self._incoming.put(("error", exc))

    def _send(self, message: Mapping[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise LspProtocolError("LSP server is not running")
        body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        try:
            self.process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
            self.process.stdin.write(body)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise LspProtocolError("failed to write to LSP server") from exc

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def _record_notification(self, message: Mapping[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params", {})
        if not isinstance(params, Mapping):
            raise LspProtocolError(f"notification {method!r} params must be an object")
        if method == "textDocument/publishDiagnostics":
            self.diagnostics.append(_json_copy(params))
        elif method in ("window/showMessage", "window/logMessage"):
            saved = dict(params)
            saved["method"] = method
            self.messages.append(_json_copy(saved))

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        self._request_id += 1
        request_id = self._request_id
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self._send(message)
        deadline = time.monotonic() + self.config.timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LspProtocolError(f"timeout waiting for {method}")
            try:
                kind, value = self._incoming.get(timeout=remaining)
            except queue.Empty as exc:
                raise LspProtocolError(f"timeout waiting for {method}") from exc
            if kind == "error":
                if isinstance(value, BaseException):
                    raise LspProtocolError(str(value)) from value
                raise LspProtocolError(str(value))
            if kind == "eof":
                raise LspProtocolError(f"LSP server exited while waiting for {method}")
            response = value
            if "method" in response:
                self._record_notification(response)
                continue
            if response.get("id") != request_id:
                raise LspProtocolError(
                    f"unexpected JSON-RPC response id {response.get('id')!r}"
                )
            if "error" in response:
                raise LspProtocolError(
                    f"{method} failed: {json.dumps(response['error'], sort_keys=True)}"
                )
            if "result" not in response:
                raise LspProtocolError(f"{method} response has no result")
            return response["result"]

    def initialize(self) -> None:
        result = self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "clientInfo": {"name": "meldra-lsp-baseline"},
                "rootUri": self.config.root.as_uri(),
                "capabilities": {
                    "general": {"positionEncodings": ["utf-16"]},
                    "workspace": {"applyEdit": False, "workspaceEdit": {"documentChanges": True}},
                    "textDocument": {"rename": {"prepareSupport": True}},
                },
            },
        )
        if not isinstance(result, Mapping):
            raise LspProtocolError("initialize result must be an object")
        self.notify("initialized", {})
        self._initialized = True

    def preview_rename(
        self, uri: str, source: str, task: RenameTask
    ) -> tuple[Mapping[str, Any] | None, Any]:
        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": self.config.language_id,
                    "version": task.version,
                    "text": source,
                }
            },
        )
        position = {"line": task.line, "character": task.character}
        prepare_result = self.request(
            "textDocument/prepareRename",
            {"textDocument": {"uri": uri}, "position": position},
        )
        if prepare_result is None:
            return None, None
        result = self.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": uri},
                "position": position,
                "newName": task.new_name,
            },
        )
        if result is not None and not isinstance(result, Mapping):
            raise LspProtocolError("rename result must be a WorkspaceEdit or null")
        return result, prepare_result

    def shutdown(self) -> None:
        if self.process is None:
            return
        if self._initialized and self.process.poll() is None:
            self.request("shutdown")
            self.notify("exit")
            self._initialized = False
        self._finish_process()

    def abort(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        self._finish_process()

    def _finish_process(self) -> None:
        if self.process is None:
            return
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=1.0)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1.0)
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)


def _task_path(config: LspConfig, task: RenameTask) -> Path:
    path = Path(task.path)
    if not path.is_absolute():
        path = config.root / path
    path = path.resolve()
    try:
        path.relative_to(config.root)
    except ValueError as exc:
        raise WorkspaceEditError(f"rename task path escapes root: {task.path!s}") from exc
    if not path.is_file():
        raise WorkspaceEditError(f"rename task path is not a file: {task.path!s}")
    return path


def run_rename_task(config: LspConfig, task: RenameTask) -> LspRenameReport:
    """Run one non-mutating external-LSP rename task for Meldra comparison."""
    started = time.perf_counter()
    client = _LspStdioClient(config)
    workspace_value: Mapping[str, Any] | None = None
    parsed: WorkspaceEdit | None = None
    prepare_result: Any = None
    try:
        path = _task_path(config, task)
        source = task.source if task.source is not None else path.read_text(encoding="utf-8")
        client.start()
        client.initialize()
        workspace_value, prepare_result = client.preview_rename(path.as_uri(), source, task)
        if workspace_value is not None:
            parsed = parse_workspace_edit(workspace_value, config.root)
        client.shutdown()
        edit_files = (
            tuple(
                str(_uri_to_path(document.uri, config.root).relative_to(config.root))
                for document in parsed.documents
            )
            if parsed is not None
            else ()
        )
        return LspRenameReport(
            status=OK,
            workspace_edit=(
                _json_copy(workspace_value) if workspace_value is not None else None
            ),
            edit_files=edit_files,
            edit_count=parsed.edit_count if parsed is not None else 0,
            diagnostics=tuple(client.diagnostics),
            messages=tuple(client.messages),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            stderr=client.stderr,
            prepare_result=prepare_result,
            edit=parsed,
        )
    except FileNotFoundError as exc:
        client.abort()
        return LspRenameReport(
            status=UNMEASURED,
            workspace_edit=None,
            edit_files=(),
            edit_count=0,
            diagnostics=tuple(client.diagnostics),
            messages=tuple(client.messages),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            stderr=client.stderr,
            error=f"language server unavailable: {exc}",
        )
    except BaseException as exc:
        client.abort()
        return LspRenameReport(
            status=ERROR,
            workspace_edit=(
                _json_copy(workspace_value) if workspace_value is not None else None
            ),
            edit_files=(),
            edit_count=0,
            diagnostics=tuple(client.diagnostics),
            messages=tuple(client.messages),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            stderr=client.stderr,
            error=f"{type(exc).__name__}: {exc}",
            prepare_result=prepare_result,
        )
