"""Bounded HTTP/1.1 and RFC6455 networking over asyncio streams.

No socket is opened until both the effect and exact host authority checks have
succeeded.  Stream factories are injectable so protocol tests do not need a
network and callers can supply their own transport policy.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import ssl
import struct
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from .async_runtime import CapabilityContext, current_capabilities, run_with_timeout
from .runtime_contract import CapabilityManifest

StreamFactory = Callable[..., Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


class NetworkProtocolError(ValueError):
    """Malformed, unsupported, or resource-exhausting wire data."""


class HTTPProtocolError(NetworkProtocolError):
    pass


class WebSocketProtocolError(NetworkProtocolError):
    pass


@dataclass(frozen=True)
class HTTPRequest:
    method: str
    target: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    reason: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""


async def _read_until(reader: asyncio.StreamReader, marker: bytes, limit: int) -> bytes:
    try:
        value = await reader.readuntil(marker)
    except (asyncio.LimitOverrunError, asyncio.IncompleteReadError) as error:
        raise HTTPProtocolError("header or line exceeds bound") from error
    if len(value) > limit:
        raise HTTPProtocolError("wire data exceeds bound")
    return value


def _headers(raw: bytes) -> dict[str, str]:
    lines = raw.split(b"\r\n")
    if not lines or lines[-1] != b"":
        raise HTTPProtocolError("header terminator missing")
    result: dict[str, str] = {}
    for line in lines[:-1]:
        if not line or b":" not in line:
            raise HTTPProtocolError("malformed header")
        name, value = line.split(b":", 1)
        try:
            key = name.decode("ascii").strip().lower()
            val = value.decode("iso-8859-1").strip()
        except UnicodeDecodeError as error:
            raise HTTPProtocolError("invalid header encoding") from error
        if not key or any(ch not in "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyz" for ch in key):
            raise HTTPProtocolError("invalid header name")
        if key in result:
            result[key] = result[key] + ", " + val
        else:
            result[key] = val
    return result


def _outgoing_headers(headers: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        raise HTTPProtocolError("headers must be a mapping")
    result: dict[str, str] = {}
    token = "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyz"
    for raw_name, raw_value in headers.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise HTTPProtocolError("header names and values must be strings")
        name = raw_name.lower()
        if not name or any(character not in token for character in name):
            raise HTTPProtocolError("invalid header name")
        if any(ord(character) < 32 or ord(character) == 127 for character in raw_value):
            raise HTTPProtocolError("header injection")
        result[name] = raw_value
    return result


def _content_length(headers: Mapping[str, str]) -> int | None:
    value = headers.get("content-length")
    if value is None:
        return None
    try:
        if not value.isascii() or not value.strip().isdigit():
            raise ValueError
        length = int(value.strip(), 10)
    except ValueError as error:
        raise HTTPProtocolError("invalid content-length") from error
    if length < 0:
        raise HTTPProtocolError("negative content-length")
    return length


async def _read_body(
    reader: asyncio.StreamReader,
    headers: Mapping[str, str],
    *,
    max_size: int,
    read_to_eof: bool = False,
) -> bytes:
    transfer = headers.get("transfer-encoding", "").lower()
    if transfer:
        codings = [item.strip() for item in transfer.split(",") if item.strip()]
        if codings != ["chunked"]:
            raise HTTPProtocolError("unsupported transfer-encoding")
        chunks: list[bytes] = []
        total = 0
        while True:
            line = await _read_until(reader, b"\r\n", 8192)
            text = line[:-2].decode("ascii", "strict")
            size_text = text.split(";", 1)[0].strip()
            try:
                size = int(size_text, 16)
            except ValueError as error:
                raise HTTPProtocolError("invalid chunk length") from error
            if size < 0 or total + size > max_size:
                raise HTTPProtocolError("response body exceeds bound")
            if size == 0:
                # Trailer fields are legal; consume through the empty line.
                while True:
                    trailer = await _read_until(reader, b"\r\n", 8192)
                    if trailer == b"\r\n":
                        return b"".join(chunks)
                    if b":" not in trailer:
                        raise HTTPProtocolError("malformed chunk trailer")
            chunk = await reader.readexactly(size)
            ending = await reader.readexactly(2)
            if ending != b"\r\n":
                raise HTTPProtocolError("chunk terminator missing")
            chunks.append(chunk)
            total += size
    length = _content_length(headers)
    if length is not None:
        if length > max_size:
            raise HTTPProtocolError("response body exceeds bound")
        try:
            return await reader.readexactly(length)
        except asyncio.IncompleteReadError as error:
            raise HTTPProtocolError("truncated response body") from error
    if not read_to_eof:
        return b""
    chunks = []
    total = 0
    while True:
        chunk = await reader.read(min(65536, max_size - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_size:
            raise HTTPProtocolError("response body exceeds bound")
        chunks.append(chunk)


async def _read_http_response(
    reader: asyncio.StreamReader,
    *,
    max_size: int,
    max_header_size: int,
) -> HTTPResponse:
    header = await _read_until(reader, b"\r\n\r\n", max_header_size)
    lines = header[:-4].split(b"\r\n")
    if not lines:
        raise HTTPProtocolError("missing status line")
    try:
        version, status_text, reason = lines[0].decode("ascii").split(" ", 2)
        status = int(status_text)
    except (UnicodeDecodeError, ValueError) as error:
        raise HTTPProtocolError("malformed status line") from error
    if version not in {"HTTP/1.0", "HTTP/1.1"} or not 100 <= status <= 999:
        raise HTTPProtocolError("unsupported HTTP status line")
    headers = _headers(b"\r\n".join(lines[1:]) + b"\r\n")
    body = b"" if status in {204, 304} or 100 <= status < 200 else await _read_body(
        reader, headers, max_size=max_size, read_to_eof=True
    )
    return HTTPResponse(status, reason, headers, body)


def _context_for(context: CapabilityContext | CapabilityManifest | None) -> CapabilityContext:
    if isinstance(context, CapabilityManifest):
        return CapabilityContext.from_manifest(context)
    if isinstance(context, CapabilityContext):
        return context
    return current_capabilities()


class AsyncHTTPClient:
    def __init__(
        self,
        *,
        manifest: CapabilityManifest | None = None,
        capabilities: CapabilityContext | None = None,
        open_connection: StreamFactory = asyncio.open_connection,
        timeout: float = 30.0,
        max_response_size: int = 8 * 1024 * 1024,
        max_request_size: int = 8 * 1024 * 1024,
        max_header_size: int = 65536,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
            or type(max_response_size) is not int
            or max_response_size < 0
            or type(max_request_size) is not int
            or max_request_size < 0
            or type(max_header_size) is not int
            or max_header_size < 1024
        ):
            raise ValueError("invalid HTTP bounds")
        self.context = capabilities or (CapabilityContext.from_manifest(manifest) if manifest else None)
        self.open_connection = open_connection
        self.timeout = float(timeout)
        self.max_response_size = max_response_size
        self.max_request_size = max_request_size
        self.max_header_size = max_header_size
        self.ssl_context = ssl_context

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
        timeout: float | None = None,
    ) -> HTTPResponse:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise HTTPProtocolError("URL must use http or https and include a host")
        if parsed.username is not None or parsed.password is not None:
            raise HTTPProtocolError("userinfo is not allowed")
        host = parsed.hostname.lower()
        context = _context_for(self.context)
        context.require("network.http", host)
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise HTTPProtocolError("invalid port") from exc
        if not 1 <= port <= 65535:
            raise HTTPProtocolError("invalid port")
        if not isinstance(method, str) or not method or any(
            character.lower() not in "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyz"
            for character in method
        ):
            raise HTTPProtocolError("invalid method")
        if not isinstance(body, (bytes, bytearray, memoryview)):
            raise HTTPProtocolError("request body must be bytes")
        if len(body) > self.max_request_size:
            raise HTTPProtocolError("request body exceeds bound")
        request_headers = _outgoing_headers(headers or {})
        request_headers.setdefault("host", parsed.netloc)
        request_headers.setdefault("connection", "close")
        request_headers.setdefault("content-length", str(len(body)))
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        if any(ord(character) < 32 or ord(character) == 127 for character in target):
            raise HTTPProtocolError("invalid request target")
        header_wire = (
            f"{method.upper()} {target} HTTP/1.1\r\n"
            + "".join(f"{key}: {value}\r\n" for key, value in request_headers.items())
            + "\r\n"
        ).encode("iso-8859-1")
        if len(header_wire) > self.max_header_size:
            raise HTTPProtocolError("request headers exceed bound")
        wire = header_wire + bytes(body)
        ssl_arg: ssl.SSLContext | None = None
        if parsed.scheme == "https":
            ssl_arg = self.ssl_context or ssl.create_default_context()
        kwargs: dict[str, Any] = {"ssl": ssl_arg}
        if ssl_arg is not None:
            kwargs["server_hostname"] = host
        effective_timeout = self.timeout if timeout is None else timeout
        if (
            isinstance(effective_timeout, bool)
            or not isinstance(effective_timeout, (int, float))
            or effective_timeout < 0
        ):
            raise ValueError("timeout must be a non-negative number")
        reader: asyncio.StreamReader
        writer: asyncio.StreamWriter
        try:
            reader, writer = await run_with_timeout(
                self.open_connection(host, port, **kwargs),
                effective_timeout,
            )
        except TypeError:
            if "server_hostname" not in kwargs:
                raise
            kwargs.pop("server_hostname")
            reader, writer = await run_with_timeout(
                self.open_connection(host, port, **kwargs),
                effective_timeout,
            )
        try:
            writer.write(wire)
            await run_with_timeout(writer.drain(), effective_timeout)
            return await run_with_timeout(
                _read_http_response(
                    reader,
                    max_size=self.max_response_size,
                    max_header_size=self.max_header_size,
                ),
                effective_timeout,
            )
        finally:
            writer.close()
            waiter = getattr(writer, "wait_closed", None)
            if waiter is not None:
                try:
                    await waiter()
                except (OSError, asyncio.CancelledError):
                    pass

    async def get(self, url: str, **kwargs: Any) -> HTTPResponse:
        return await self.request("GET", url, **kwargs)


@dataclass(frozen=True)
class HTTPServerConfig:
    max_connections: int = 64
    max_request_size: int = 1024 * 1024
    timeout: float = 30.0

    def __post_init__(self) -> None:
        if (
            type(self.max_connections) is not int
            or self.max_connections < 1
            or type(self.max_request_size) is not int
            or self.max_request_size < 0
            or isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or self.timeout <= 0
        ):
            raise ValueError("invalid HTTP server bounds")


class BoundedHTTPServer:
    """HTTP/1.1 server whose connection tasks are bounded and joined on close."""

    def __init__(self, handler: Callable[[HTTPRequest], Awaitable[HTTPResponse]], *, config: HTTPServerConfig = HTTPServerConfig()) -> None:
        if config.max_connections < 1:
            raise ValueError("max_connections must be positive")
        self.handler = handler
        self.config = config
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[asyncio.Task[Any]] = set()
        self._limit = asyncio.Semaphore(config.max_connections)

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> asyncio.AbstractServer:
        self._server = await asyncio.start_server(self._accept, host, port)
        return self._server

    @property
    def sockets(self) -> Sequence[Any]:
        return tuple(self._server.sockets or ()) if self._server else ()

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self._connections) >= self.config.max_connections:
            writer.close()
            waiter = getattr(writer, "wait_closed", None)
            if waiter is not None:
                await waiter()
            return
        async def run() -> None:
            async with self._limit:
                try:
                    header = await asyncio.wait_for(_read_until(reader, b"\r\n\r\n", self.config.max_request_size), self.config.timeout)
                    lines = header[:-4].split(b"\r\n")
                    try:
                        method, target, version = lines[0].decode("ascii").split(" ", 2)
                    except (UnicodeDecodeError, ValueError) as error:
                        raise HTTPProtocolError("malformed request line") from error
                    if version not in {"HTTP/1.0", "HTTP/1.1"}:
                        raise HTTPProtocolError("unsupported HTTP version")
                    request_headers = _headers(b"\r\n".join(lines[1:]) + b"\r\n")
                    body = await _read_body(reader, request_headers, max_size=self.config.max_request_size)
                    response = await asyncio.wait_for(self.handler(HTTPRequest(method, target, request_headers, body)), self.config.timeout)
                    if not isinstance(response, HTTPResponse):
                        raise TypeError("HTTP handler must return HTTPResponse")
                    if len(response.body) > self.config.max_request_size:
                        raise HTTPProtocolError("response body exceeds bound")
                    response_headers = _outgoing_headers(response.headers)
                    response_headers.setdefault("content-length", str(len(response.body)))
                    response_headers.setdefault("connection", "close")
                    if (
                        type(response.status) is not int
                        or not 100 <= response.status <= 999
                        or not isinstance(response.reason, str)
                        or any(ord(character) < 32 or ord(character) == 127 for character in response.reason)
                    ):
                        raise HTTPProtocolError("invalid response status")
                    wire = f"HTTP/1.1 {response.status} {response.reason}\r\n".encode("ascii")
                    wire += (
                        "".join(f"{key}: {value}\r\n" for key, value in response_headers.items()).encode("iso-8859-1")
                        + b"\r\n"
                        + response.body
                    )
                    writer.write(wire)
                    await asyncio.wait_for(writer.drain(), self.config.timeout)
                except (HTTPProtocolError, asyncio.TimeoutError):
                    writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    try:
                        await writer.drain()
                    except OSError:
                        pass
                finally:
                    writer.close()
                    waiter = getattr(writer, "wait_closed", None)
                    if waiter:
                        await waiter()
        task = asyncio.create_task(run())
        self._connections.add(task)
        task.add_done_callback(self._connections.discard)

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        tasks = tuple(self._connections)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def __aenter__(self) -> "BoundedHTTPServer":
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()


@dataclass(frozen=True)
class WebSocketFrame:
    opcode: int
    data: bytes
    fin: bool = True


def _ws_accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")


class WebSocketConnection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, is_client: bool, max_frame_size: int = 8 * 1024 * 1024) -> None:
        if type(max_frame_size) is not int or max_frame_size < 0:
            raise ValueError("invalid WebSocket frame bound")
        self.reader, self.writer, self.is_client, self.max_frame_size = reader, writer, is_client, max_frame_size
        self.closed = False

    async def _send(self, opcode: int, data: bytes = b"", *, fin: bool = True) -> None:
        if self.closed and opcode != 0x8:
            raise WebSocketProtocolError("websocket is closed")
        if opcode not in {0x0, 0x1, 0x2, 0x8, 0x9, 0xA}:
            raise WebSocketProtocolError("reserved websocket opcode")
        if opcode >= 0x8 and (not fin or len(data) > 125):
            raise WebSocketProtocolError("invalid control frame")
        if len(data) > self.max_frame_size:
            raise WebSocketProtocolError("frame exceeds bound")
        first = (0x80 if fin else 0) | (opcode & 0x0F)
        mask = self.is_client
        length = len(data)
        if length < 126:
            header = bytes([first, (0x80 if mask else 0) | length])
        elif length <= 0xFFFF:
            header = bytes([first, (0x80 if mask else 0) | 126]) + struct.pack("!H", length)
        else:
            header = bytes([first, (0x80 if mask else 0) | 127]) + struct.pack("!Q", length)
        if mask:
            key = os.urandom(4)
            data = bytes(value ^ key[index % 4] for index, value in enumerate(data))
            header += key
        self.writer.write(header + data)
        await self.writer.drain()

    async def send_text(self, text: str) -> None:
        await self._send(0x1, text.encode("utf-8"))

    async def send_binary(self, data: bytes) -> None:
        await self._send(0x2, bytes(data))

    async def ping(self, data: bytes = b"") -> None:
        await self._send(0x9, data)

    async def pong(self, data: bytes = b"") -> None:
        await self._send(0xA, data)

    async def recv(self) -> WebSocketFrame:
        first_two = await self.reader.readexactly(2)
        first, second = first_two
        fin, rsv, opcode = bool(first & 0x80), first & 0x70, first & 0x0F
        if rsv or opcode not in {0x0, 0x1, 0x2, 0x8, 0x9, 0xA}:
            raise WebSocketProtocolError("invalid control or reserved bits")
        if not fin:
            raise WebSocketProtocolError("fragmented frames are unsupported")
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", await self.reader.readexactly(2))[0]
        elif length == 127:
            raw = struct.unpack("!Q", await self.reader.readexactly(8))[0]
            if raw & (1 << 63):
                raise WebSocketProtocolError("invalid 64-bit frame length")
            length = raw
        if length > self.max_frame_size or (opcode >= 0x8 and length > 125):
            raise WebSocketProtocolError("frame exceeds bound")
        if masked != (not self.is_client):
            raise WebSocketProtocolError("invalid masking direction")
        key = await self.reader.readexactly(4) if masked else b""
        data = await self.reader.readexactly(length)
        if masked:
            data = bytes(value ^ key[index % 4] for index, value in enumerate(data))
        frame = WebSocketFrame(opcode, data, fin)
        if opcode == 0x9:
            await self.pong(data)
        elif opcode == 0x8:
            self.closed = True
        return frame

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed:
            return
        encoded = reason.encode("utf-8")
        if len(encoded) > 123 or not 1000 <= code <= 4999:
            raise WebSocketProtocolError("invalid close payload")
        self.closed = True
        try:
            await self._send(0x8, struct.pack("!H", code) + encoded)
        finally:
            self.writer.close()
            waiter = getattr(self.writer, "wait_closed", None)
            if waiter:
                await waiter()


class WebSocketClient:
    def __init__(
        self,
        *,
        manifest: CapabilityManifest | None = None,
        capabilities: CapabilityContext | None = None,
        open_connection: StreamFactory = asyncio.open_connection,
        timeout: float = 30.0,
        max_frame_size: int = 8 * 1024 * 1024,
        max_header_size: int = 65536,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
            or type(max_frame_size) is not int
            or max_frame_size < 0
            or type(max_header_size) is not int
            or max_header_size < 1024
        ):
            raise ValueError("invalid WebSocket bounds")
        self.context = capabilities or (CapabilityContext.from_manifest(manifest) if manifest else None)
        self.open_connection = open_connection
        self.timeout = float(timeout)
        self.max_frame_size = max_frame_size
        self.max_header_size = max_header_size
        self.ssl_context = ssl_context

    async def connect(self, url: str, *, headers: Mapping[str, str] | None = None) -> WebSocketConnection:
        parsed = urlsplit(url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise WebSocketProtocolError("URL must use ws or wss and include a host")
        if parsed.username is not None or parsed.password is not None:
            raise WebSocketProtocolError("userinfo is not allowed")
        host = parsed.hostname.lower()
        context = _context_for(self.context)
        context.require("network.http", host)
        provided_headers = _outgoing_headers(headers or {})
        try:
            port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        except ValueError as exc:
            raise WebSocketProtocolError("invalid port") from exc
        if not 1 <= port <= 65535:
            raise WebSocketProtocolError("invalid port")
        ssl_arg = self.ssl_context or ssl.create_default_context() if parsed.scheme == "wss" else None
        kwargs: dict[str, Any] = {"ssl": ssl_arg}
        if ssl_arg is not None:
            kwargs["server_hostname"] = host
        try:
            reader, writer = await run_with_timeout(
                self.open_connection(host, port, **kwargs),
                self.timeout,
            )
        except TypeError:
            kwargs.pop("server_hostname", None)
            reader, writer = await run_with_timeout(
                self.open_connection(host, port, **kwargs),
                self.timeout,
            )
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            if any(ord(character) < 32 or ord(character) == 127 for character in target):
                raise WebSocketProtocolError("invalid request target")
            req_headers = dict(provided_headers)
            req_headers.update({
                "host": parsed.netloc,
                "upgrade": "websocket",
                "connection": "Upgrade",
                "sec-websocket-key": key,
                "sec-websocket-version": "13",
            })
            wire = (
                f"GET {target} HTTP/1.1\r\n"
                + "".join(f"{name}: {value}\r\n" for name, value in req_headers.items())
                + "\r\n"
            ).encode("iso-8859-1")
            if len(wire) > self.max_header_size:
                raise WebSocketProtocolError("handshake headers exceed bound")
            writer.write(wire)
            await run_with_timeout(writer.drain(), self.timeout)
            header = await run_with_timeout(
                _read_until(reader, b"\r\n\r\n", self.max_header_size),
                self.timeout,
            )
            lines = header[:-4].split(b"\r\n")
            if not lines or not lines[0].startswith(b"HTTP/1.1 101 "):
                raise WebSocketProtocolError("websocket upgrade rejected")
            response_headers = _headers(b"\r\n".join(lines[1:]) + b"\r\n")
            connection_tokens = {
                item.strip().lower()
                for item in response_headers.get("connection", "").split(",")
            }
            if (
                response_headers.get("upgrade", "").lower() != "websocket"
                or "upgrade" not in connection_tokens
                or response_headers.get("sec-websocket-accept") != _ws_accept(key)
            ):
                raise WebSocketProtocolError("invalid websocket handshake")
            return WebSocketConnection(
                reader,
                writer,
                is_client=True,
                max_frame_size=self.max_frame_size,
            )
        except BaseException:
            writer.close()
            waiter = getattr(writer, "wait_closed", None)
            if waiter is not None:
                try:
                    await waiter()
                except (OSError, asyncio.CancelledError):
                    pass
            raise


async def websocket_accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, max_frame_size: int = 8 * 1024 * 1024) -> WebSocketConnection:
    """Validate a server-side RFC6455 handshake and write the response."""
    header = await _read_until(reader, b"\r\n\r\n", 65536)
    lines = header[:-4].split(b"\r\n")
    if not lines:
        raise WebSocketProtocolError("missing handshake")
    try:
        method, _target, version = lines[0].decode("ascii").split(" ", 2)
    except (UnicodeDecodeError, ValueError) as error:
        raise WebSocketProtocolError("malformed handshake") from error
    hs = _headers(b"\r\n".join(lines[1:]) + b"\r\n")
    key = hs.get("sec-websocket-key", "")
    connection_tokens = {item.strip().lower() for item in hs.get("connection", "").split(",")}
    if (
        method != "GET"
        or version != "HTTP/1.1"
        or hs.get("upgrade", "").lower() != "websocket"
        or "upgrade" not in connection_tokens
        or hs.get("sec-websocket-version") != "13"
        or not key
    ):
        raise WebSocketProtocolError("invalid websocket handshake")
    try:
        decoded_key = base64.b64decode(key, validate=True)
    except Exception as error:
        raise WebSocketProtocolError("invalid websocket key") from error
    if len(decoded_key) != 16:
        raise WebSocketProtocolError("invalid websocket key")
    if type(max_frame_size) is not int or max_frame_size < 0:
        raise ValueError("invalid WebSocket frame bound")
    writer.write(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {_ws_accept(key)}\r\n\r\n"
        ).encode("ascii")
    )
    await writer.drain()
    return WebSocketConnection(reader, writer, is_client=False, max_frame_size=max_frame_size)


# Public aliases used by integrations.
HTTPClient = AsyncHTTPClient
HTTPServer = BoundedHTTPServer
WebSocket = WebSocketConnection

__all__ = [
    "AsyncHTTPClient", "HTTPClient", "HTTPProtocolError", "HTTPRequest", "HTTPResponse", "HTTPServer", "HTTPServerConfig", "BoundedHTTPServer", "NetworkProtocolError", "WebSocket", "WebSocketClient", "WebSocketConnection", "WebSocketFrame", "WebSocketProtocolError", "websocket_accept",
]
