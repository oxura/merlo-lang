from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Mapping
from urllib.parse import unquote, urlparse

from merlo.alpha_protocol import AlphaProtocol
from merlo.refactor import ChangeIR
from merlo.compiler import compile_project
from merlo.formatter import format_application_source
from merlo.project import Project
from merlo.semantic_world import SemanticWorld, StaleWorldError, WorldError


JSON = dict[str, Any]
_JSONRPC_ERROR = -32001


@dataclass
class _Document:
    uri: str
    path: Path
    text: str
    version: int | None


def encode_message(message: Mapping[str, Any]) -> bytes:
    """Encode one JSON-RPC message using byte-accurate LSP framing."""
    payload = json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return b"Content-Length: " + str(len(payload)).encode("ascii") + b"\r\n\r\n" + payload


def decode_message(frame: bytes) -> JSON:
    """Decode a complete LSP frame (headers and UTF-8 JSON body)."""
    marker = frame.find(b"\r\n\r\n")
    if marker < 0:
        raise ValueError("InvalidLSPFrame: missing header terminator")
    headers = frame[:marker].decode("ascii", errors="strict").split("\r\n")
    values: dict[str, str] = {}
    for header in headers:
        if ":" not in header:
            raise ValueError("InvalidLSPFrame: malformed header")
        key, value = header.split(":", 1)
        values[key.casefold()] = value.strip()
    try:
        length = int(values["content-length"])
    except (KeyError, ValueError) as exc:
        raise ValueError("InvalidLSPFrame: Content-Length is required") from exc
    body = frame[marker + 4 : marker + 4 + length]
    if len(body) != length:
        raise ValueError("InvalidLSPFrame: truncated body")
    value = json.loads(body.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("InvalidLSPFrame: JSON-RPC message must be an object")
    return value


def _uri(path: Path) -> str:
    return path.resolve().as_uri()


def _path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme and parsed.scheme != "file":
        raise ValueError(f"UnsupportedDocumentUri: {uri}")
    return Path(unquote(parsed.path)).resolve()


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _position(text: str, offset: int) -> dict[str, int]:
    offset = max(0, min(len(text), offset))
    offsets = _line_offsets(text)
    line = 0
    for index, start in enumerate(offsets):
        if start > offset:
            break
        line = index
    line_start = offsets[line]
    return {"line": line, "character": _utf16_length(text[line_start:offset])}


def _offset(text: str, line: int, character: int) -> int:
    lines = text.splitlines(keepends=True)
    if not lines:
        return 0
    line = max(0, min(line, len(lines) - 1))
    segment = lines[line]
    units = 0
    index = 0
    for index, char in enumerate(segment):
        width = _utf16_length(char)
        if units + width > character:
            break
        units += width
    else:
        index = len(segment)
    return sum(len(item) for item in lines[:line]) + index


def _codepoint_column(text: str, line: int, character: int) -> int:
    """Convert an incoming UTF-16 LSP column to a Python string column."""
    if not text:
        return 0
    lines = text.splitlines(keepends=True)
    if not lines:
        return 0
    line = max(0, min(line, len(lines) - 1))
    segment = lines[line]
    units = 0
    for index, char in enumerate(segment):
        width = _utf16_length(char)
        if units + width > character:
            return index
        units += width
    return len(segment)


def _utf16_column(text: str, line: int, column: int) -> int:
    if not text:
        return column
    lines = text.splitlines(keepends=True)
    if not lines:
        return column
    line = max(0, min(line, len(lines) - 1))
    return _utf16_length(lines[line][:max(0, column)])


def _span_range(span: Mapping[str, Any], source: str = "") -> dict[str, Any]:
    line = max(0, int(span.get("line", 1)) - 1)
    end_line = max(line, int(span.get("end_line", span.get("line", 1))) - 1)
    column = max(0, int(span.get("column", 0)))
    end_column = max(column, int(span.get("end_column", column)))
    return {
        "start": {"line": line, "character": _utf16_column(source, line, column)},
        "end": {"line": end_line, "character": _utf16_column(source, end_line, end_column)},
    }


def _kind(kind: str) -> int:
    return {"module": 2, "fn": 12, "function": 12, "task": 12, "record": 23, "enum": 10, "const": 13, "type": 23}.get(str(kind).casefold(), 13)


def _diagnostic_code(message: str) -> str:
    lowered = message.casefold()
    if "unresolvedname" in lowered or "unknown symbol" in lowered or "unknown enum" in lowered:
        return "UnknownSymbol"
    if "stale" in lowered and "snapshot" in lowered:
        return "StaleSnapshot"
    known = ("UnknownSymbol", "AmbiguousSymbol", "StaleWorld", "StaleSnapshot", "UnsupportedMigration", "InvalidSymbolName", "MissingTarget", "CompileError")
    for code in known:
        if code.casefold() in lowered:
            return code
    match = re.search(r"\b([A-Z][A-Za-z0-9]*(?:Error|Symbol|Type|Effect|Capability|Migration|Snapshot|World|Lockfile|Manifest)?)[ :]", message)
    return match.group(1) if match else "CompileError"


def _compile_location(message: str, fallback: Path) -> tuple[Path, int, int, str]:
    match = re.match(r"^(.*?):(\d+)(?::(\d+))?:\s*(.*)$", message, re.S)
    if match:
        return Path(match.group(1)).resolve(), int(match.group(2)), int(match.group(3) or 0), match.group(4)
    return fallback.resolve(), 1, 0, message


class MerloLanguageServer:
    """Small deterministic LSP facade over Project, compiler, world, and protocol."""

    def __init__(self, path: str | Path = ".", *, world: SemanticWorld | None = None) -> None:
        candidate = Path(path).resolve()
        if candidate.is_dir():
            try:
                project = Project.load(candidate)
                candidate = project.root / "src" / "main.mlo"
            except Exception:
                candidate = candidate / "main.mlo"
        self.source_path = candidate
        try:
            self.project: Project | None = Project.load(candidate)
        except Exception:
            self.project = None
        self.root = self.project.root if self.project is not None else candidate.parent
        self.documents: dict[Path, _Document] = {}
        self.world = world
        self.protocol = AlphaProtocol(world) if world is not None else None
        self._shadow_root: Path | None = None
        self.initialized = False
        self.shutdown_requested = False
        self.exited = False

    @property
    def world_revision(self) -> str | None:
        return self.world.world_revision if self.world is not None else None

    def _public_path(self, path: Path) -> Path:
        path = path.resolve()
        if self._shadow_root is not None:
            try:
                return (self.root / path.relative_to(self._shadow_root)).resolve()
            except ValueError:
                pass
        return path

    def _shadow_path(self, path: Path, shadow: Path) -> Path:
        try:
            relative = path.resolve().relative_to(self.root)
        except ValueError:
            relative = Path(path.name)
        result = shadow / relative
        result.parent.mkdir(parents=True, exist_ok=True)
        return result

    def _snapshot_overrides(self) -> dict[Path, _Document]:
        changed: dict[Path, _Document] = {}
        for path, document in self.documents.items():
            try:
                disk = path.read_text(encoding="utf-8")
            except OSError:
                disk = None
            if disk != document.text:
                changed[path] = document
        return changed

    def _refresh(self) -> list[dict[str, Any]]:
        if self._shadow_root is not None:
            shutil.rmtree(self._shadow_root, ignore_errors=True)
            self._shadow_root = None
        overrides = self._snapshot_overrides()
        target: Path = self.source_path
        if self.project is not None:
            target = self.root
        if overrides:
            self._shadow_root = Path(tempfile.mkdtemp(prefix="merlo-lsp-"))
            if self.root.is_dir():
                shadow_root = self._shadow_root / self.root.name
                shutil.copytree(self.root, shadow_root)
            else:
                shadow_root = self._shadow_root
            for path, document in overrides.items():
                destination = self._shadow_path(path, shadow_root)
                destination.write_text(document.text, encoding="utf-8")
            self._shadow_root = shadow_root
            target = shadow_root if self.project is not None else self._shadow_path(self.source_path, shadow_root)
        try:
            compilation = compile_project(target, require_interface_lock=False)
            built = SemanticWorld.build(compilation, previous=self.world, require_interface_lock=False)
        except Exception as exc:
            self.world = None
            self.protocol = None
            fallback = next(iter(overrides), self.source_path)
            path, line, column, detail = _compile_location(str(exc), fallback)
            code = _diagnostic_code(str(exc))
            return [{"path": self._public_path(path), "line": line, "column": column, "message": f"{code}: {detail}", "code": code, "severity": 1}]
        data = built.to_dict()
        if self._shadow_root is not None:
            shadow = str(self._shadow_root)
            original_root = str(self.root)
            def remap(value: Any) -> Any:
                if isinstance(value, str) and value.startswith(shadow):
                    return original_root + value[len(shadow):]
                if isinstance(value, list):
                    return [remap(item) for item in value]
                if isinstance(value, dict):
                    return {key: remap(item) for key, item in value.items()}
                return value
            data = remap(data)
            data["root"] = original_root
        self.world = SemanticWorld(self.root, self.root / ".merlo" / "world.json", data)
        self.protocol = AlphaProtocol(self.world)
        return []

    def _diagnostics(self, document: _Document | None, raw: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        diagnostics = []
        for item in raw:
            path = Path(item.get("path", document.path if document else self.source_path)).resolve()
            if document is not None and path != document.path:
                continue
            source = document.text if document is not None and path == document.path else self._source_text(path)
            line = max(0, int(item.get("line", 1)) - 1)
            column = max(0, int(item.get("column", 0)))
            range_value = {"start": {"line": line, "character": _utf16_column(source, line, column)}, "end": {"line": line, "character": _utf16_column(source, line, column + 1)}}
            diagnostics.append({
                "range": range_value,
                "code": item.get("code", "CompileError"),
                "source": "merlo",
                "message": str(item.get("message", "compile error")),
            })
        diagnostics.sort(key=lambda item: (item["range"]["start"]["line"], item["range"]["start"]["character"], item["code"], item["message"]))
        return {"uri": document.uri if document else _uri(self.source_path), "version": document.version if document else None, "diagnostics": diagnostics}

    def _ensure_world(self) -> None:
        if self.world is None:
            raw = self._refresh()
            if raw:
                raise WorldError(f"{raw[0]['code']}: {raw[0]['message']}")
            return
        try:
            self.world.require_fresh()
        except StaleWorldError:
            raw = self._refresh()
            if raw:
                raise StaleWorldError(f"StaleWorld: {raw[0]['message']}")

    def _document(self, uri: str) -> _Document:
        path = _path(uri)
        document = self.documents.get(path)
        if document is not None:
            return document
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise WorldError(f"UnknownDocument: {uri}") from exc
        return _Document(uri, path, text, None)

    def _resolve(self, params: Mapping[str, Any]) -> dict[str, Any]:
        if self.world is None:
            raise WorldError("UnknownSymbol: no semantic world")
        target = params.get("target", params.get("symbol", params.get("id")))
        if isinstance(target, str) and target:
            return self.world.resolve(target)
        document = self._document(str(params.get("textDocument", {}).get("uri", "")))
        position = params.get("position", {})
        line_index = int(position.get("line", 0))
        line = line_index + 1
        character = _codepoint_column(document.text, line_index, int(position.get("character", 0)))
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        for symbol in self.world.data.get("symbols", ()):
            span = symbol.get("source", {})
            if Path(span.get("path", "")).resolve() != document.path or int(span.get("line", 0)) != line:
                continue
            start = int(span.get("column", 0))
            end = int(span.get("end_column", start + len(symbol.get("name", ""))))
            if start <= character <= max(start + 1, end):
                candidates.append((max(0, end - start), symbol["symbol_id"], symbol))
        for reference in self.world.data.get("references", ()):
            span = reference.get("source", {})
            if Path(span.get("path", "")).resolve() != document.path or int(span.get("line", 0)) != line:
                continue
            start = int(span.get("column", 0)); end = int(span.get("end_column", start + 1))
            if start <= character <= max(start + 1, end):
                try:
                    symbol = self.world.resolve(reference["target_id"])
                except WorldError:
                    continue
                candidates.append((max(0, end - start), symbol["symbol_id"], symbol))
        if not candidates:
            raise WorldError("UnknownSymbol: no exact symbol at position")
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    def _source_text(self, path: Path) -> str:
        public = self._public_path(path)
        document = self.documents.get(public)
        if document is not None:
            return document.text
        try:
            return public.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _location(self, span: Mapping[str, Any]) -> dict[str, Any]:
        path = self._public_path(Path(str(span.get("path", self.source_path))))
        return {"uri": _uri(path), "range": _span_range(span, self._source_text(path))}

    def _error(self, request_id: Any, exc: Exception) -> dict[str, Any]:
        text = str(exc)
        code = _diagnostic_code(text)
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": _JSONRPC_ERROR, "message": text, "data": {"code": code, "worldRevision": self.world_revision}}}

    def _response(self, request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _publish(self, document: _Document, raw: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": self._diagnostics(document, raw)}

    def _capabilities(self) -> dict[str, Any]:
        return {"positionEncoding": "utf-16", "textDocumentSync": {"openClose": True, "change": 1}, "hoverProvider": True, "definitionProvider": True, "referencesProvider": True, "renameProvider": True, "documentSymbolProvider": True, "workspaceSymbolProvider": True, "documentFormattingProvider": True}

    def handle_message(self, message: Mapping[str, Any]) -> list[dict[str, Any]]:
        if self.exited:
            return []
        request_id = message.get("id")
        method = message.get("method")
        if not isinstance(method, str):
            return [self._error(request_id, ValueError("InvalidRequest: method is required"))] if request_id is not None else []
        params = message.get("params") or {}
        try:
            if method == "initialize":
                self.initialized = True
                return [self._response(request_id, {"serverInfo": {"name": "merlo-alpha", "version": "1"}, "capabilities": self._capabilities()})] if request_id is not None else []
            if method == "initialized":
                return []
            if method == "shutdown":
                self.shutdown_requested = True
                return [self._response(request_id, None)] if request_id is not None else []
            if method == "exit":
                self.exited = True
                return []
            if method == "textDocument/didOpen":
                item = params.get("textDocument", {})
                document = _Document(str(item["uri"]), _path(str(item["uri"])), str(item.get("text", "")), item.get("version"))
                self.documents[document.path] = document
                return [self._publish(document, self._refresh())]
            if method == "textDocument/didChange":
                item = params.get("textDocument", {})
                document = self._document(str(item["uri"]))
                incoming_version = item.get("version")
                if document.version is not None and (
                    not isinstance(incoming_version, int)
                    or not isinstance(document.version, int)
                    or incoming_version <= document.version
                ):
                    return [self._publish(document, [{
                        "path": document.path,
                        "line": 1,
                        "column": 0,
                        "message": f"stale document version {incoming_version!r}; current version is {document.version}",
                        "code": "StaleSnapshot",
                        "severity": 2,
                    }])]
                changes = params.get("contentChanges", [])
                text = document.text
                for change in changes:
                    if "range" not in change:
                        text = str(change.get("text", ""))
                    else:
                        start = change["range"]["start"]; end = change["range"]["end"]
                        begin_offset = _offset(text, int(start.get("line", 0)), int(start.get("character", 0)))
                        end_offset = _offset(text, int(end.get("line", 0)), int(end.get("character", 0)))
                        text = text[:begin_offset] + str(change.get("text", "")) + text[end_offset:]
                document = _Document(document.uri, document.path, text, incoming_version)
                self.documents[document.path] = document
                return [self._publish(document, self._refresh())]
            if method == "textDocument/didClose":
                document = self._document(str(params.get("textDocument", {}).get("uri", "")))
                self.documents.pop(document.path, None)
                return [self._publish(document, self._refresh())]
            if method in {"textDocument/hover", "textDocument/definition", "textDocument/references", "textDocument/rename"}:
                self._ensure_world()
                symbol = self._resolve(params)
                if method == "textDocument/hover":
                    value = f"**{symbol['qualified_name']}**\n\n`{symbol['signature']}`\n\nkind: `{symbol['kind']}`"
                    if symbol.get("effects"):
                        value += "\n\neffects: " + ", ".join(symbol["effects"])
                    return [self._response(request_id, {"contents": {"kind": "markdown", "value": value}, "range": _span_range(symbol["source"], self._source_text(Path(symbol["source"]["path"])))})]
                if method == "textDocument/definition":
                    return [self._response(request_id, [self._location(symbol["source"])])]
                if method == "textDocument/references":
                    locations = [self._location(item["source"]) for item in self.world.references(symbol["symbol_id"])]
                    if bool(params.get("context", {}).get("includeDeclaration", False)):
                        locations.append(self._location(symbol["source"]))
                    locations.sort(key=lambda item: (item["uri"], item["range"]["start"]["line"], item["range"]["start"]["character"]))
                    return [self._response(request_id, locations)]
                rename = str(params.get("newName", ""))
                result = self.protocol.call("refactor.rename", {"target": symbol["symbol_id"], "new_name": rename, "mode": "preview"})
                change = ChangeIR.from_dict(result, world=self.world)
                if change.status != "ready":
                    raise WorldError("UnsupportedMigration: rename preview is not ready")
                changes: dict[str, list[dict[str, Any]]] = {}
                for edit in change.edits:
                    edit_path = self._public_path(Path(edit.path))
                    try:
                        text = self.documents.get(edit_path, _Document("", edit_path, edit_path.read_text(encoding="utf-8"), None)).text
                    except OSError:
                        text = ""
                    changes.setdefault(_uri(edit_path), []).append({"range": {"start": _position(text, edit.start), "end": _position(text, edit.end)}, "newText": edit.replacement})
                for edits in changes.values():
                    edits.sort(key=lambda item: (item["range"]["start"]["line"], item["range"]["start"]["character"]))
                return [self._response(request_id, {"changes": dict(sorted(changes.items()))})]
            if method == "textDocument/documentSymbol":
                self._ensure_world()
                document = self._document(str(params.get("textDocument", {}).get("uri", "")))
                result = []
                for symbol in sorted(self.world.data.get("symbols", ()), key=lambda item: (item["source"].get("line", 0), item["name"], item["symbol_id"])):
                    if Path(symbol["source"].get("path", "")).resolve() != document.path:
                        continue
                    symbol_source = self._source_text(Path(symbol["source"]["path"]))
                    result.append({"name": symbol["name"], "detail": symbol["signature"], "kind": _kind(symbol["kind"]), "range": _span_range(symbol["source"], symbol_source), "selectionRange": _span_range(symbol["source"], symbol_source), "data": {"symbolId": symbol["symbol_id"], "revisionId": symbol["revision_id"]}})
                return [self._response(request_id, result)]
            if method == "workspace/symbol":
                self._ensure_world()
                query = str(params.get("query", ""))
                result = []
                for symbol in self.protocol.search(query):
                    result.append({"name": symbol["name"], "kind": _kind(symbol["kind"]), "containerName": symbol["module"], "location": self._location(symbol["source"]), "data": {"symbolId": symbol["symbol_id"], "revisionId": symbol["revision_id"]}})
                result.sort(key=lambda item: (item["name"], item["containerName"], item["location"]["uri"]))
                return [self._response(request_id, result)]
            if method == "textDocument/formatting":
                document = self._document(str(params.get("textDocument", {}).get("uri", "")))
                formatted = format_application_source(document.text, path=str(document.path))
                end = _position(document.text, len(document.text))
                return [self._response(request_id, [{"range": {"start": {"line": 0, "character": 0}, "end": end}, "newText": formatted}])]
            raise ValueError(f"MethodNotFound: {method}")
        except Exception as exc:
            return [self._error(request_id, exc)] if request_id is not None else []

    def handle(self, message: Mapping[str, Any]) -> list[dict[str, Any]]:
        return self.handle_message(message)


def read_message(stream: BinaryIO) -> JSON | None:
    headers = bytearray()
    while True:
        line = stream.readline()
        if not line:
            return None
        headers.extend(line)
        if headers.endswith(b"\r\n\r\n"):
            break
    header_text = bytes(headers[:-4]).decode("ascii")
    values = {}
    for line in header_text.split("\r\n"):
        key, value = line.split(":", 1)
        values[key.casefold()] = value.strip()
    length = int(values["content-length"])
    body = stream.read(length)
    if len(body) != length:
        raise ValueError("InvalidLSPFrame: truncated body")
    value = json.loads(body.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("InvalidLSPFrame: message must be an object")
    return value



def run_stdio(server: MerloLanguageServer | None = None, *, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> int:
    server = server or MerloLanguageServer(".")
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    while not server.exited:
        message = read_message(stdin)
        if message is None:
            break
        for response in server.handle_message(message):
            stdout.write(encode_message(response))
            stdout.flush()
    return 0


def main() -> int:
    return run_stdio()


LSPServer = MerloLanguageServer
LanguageServer = MerloLanguageServer
serve = run_stdio
__all__ = ["LSPServer", "LanguageServer", "MerloLanguageServer", "decode_message", "encode_message", "main", "read_message", "run_stdio", "serve"]


if __name__ == "__main__":
    raise SystemExit(main())
