from __future__ import annotations

import asyncio
import base64
import hashlib
import ssl

import pytest

from merlo.async_runtime import (
    AsyncRuntime,
    CapabilityContext,
    StructuredTaskGroup,
    run_with_timeout,
)
from merlo.database_runtime import DatabaseCapabilityError, DatabaseResourceManager
from merlo.network_runtime import (
    AsyncHTTPClient,
    BoundedHTTPServer,
    HTTPServerConfig,
    HTTPProtocolError,
    HTTPResponse,
    WebSocketClient,
    WebSocketProtocolError,
    WebSocketConnection,
    websocket_accept,
)


def run(coro):
    return asyncio.run(coro)


def test_structured_group_cancels_siblings_and_joins() -> None:
    async def scenario() -> None:
        cancelled = asyncio.Event()

        async def sibling() -> None:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async with pytest.raises(ValueError):
            async with StructuredTaskGroup(max_tasks=2) as group:
                group.create_task(sibling())
                async def fail() -> None:
                    await asyncio.sleep(0)
                    raise ValueError("boom")
                group.create_task(fail())
        assert cancelled.is_set()
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]

    # pytest.raises is a synchronous context manager and cannot wrap async
    # code; keep the assertion explicit to make the test Python-version stable.
    async def fixed() -> None:
        cancelled = asyncio.Event()
        async def sibling() -> None:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise
        with pytest.raises(ValueError, match="boom"):
            async with StructuredTaskGroup(max_tasks=2) as group:
                group.create_task(sibling())
                async def fail() -> None:
                    await asyncio.sleep(0)
                    raise ValueError("boom")
                group.create_task(fail())
        assert cancelled.is_set()
    run(fixed())


def test_timeout_cancels_coroutine() -> None:
    async def scenario() -> None:
        finished = False
        async def work() -> None:
            nonlocal finished
            try:
                await asyncio.sleep(10)
            finally:
                finished = True
        with pytest.raises(TimeoutError):
            await run_with_timeout(work(), 0.001)
        assert finished
    run(scenario())


def test_http_real_local_server_content_length_and_host_denial() -> None:
    async def scenario() -> None:
        seen = asyncio.Event()
        async def server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            seen.set()
            request = await reader.readuntil(b"\r\n\r\n")
            assert request.startswith(b"GET /ok HTTP/1.1")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello")
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        listener = await asyncio.start_server(server, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        context = CapabilityContext({"network.http"}, network_hosts={"127.0.0.1"})
        client = AsyncHTTPClient(capabilities=context)
        response = await client.get(f"http://127.0.0.1:{port}/ok")
        assert response == HTTPResponse(200, "OK", {"content-length": "5", "connection": "close"}, b"hello")
        assert seen.is_set()
        denied = AsyncHTTPClient(capabilities=CapabilityContext({"network.http"}, network_hosts={"other.example"}))
        with pytest.raises(PermissionError):
            await denied.get(f"http://127.0.0.1:{port}/denied")
        listener.close()
        await listener.wait_closed()
    run(scenario())

def test_bounded_http_server_serves_real_local_client() -> None:
    async def scenario() -> None:
        async def handler(request):
            assert request.method == "GET"
            assert request.target == "/health"
            return HTTPResponse(200, "OK", {"content-type": "text/plain"}, b"healthy")

        server = BoundedHTTPServer(
            handler,
            config=HTTPServerConfig(max_connections=2, max_request_size=1024, timeout=1),
        )
        await server.start("127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            response = await AsyncHTTPClient(
                capabilities=CapabilityContext({"network.http"}, network_hosts={"127.0.0.1"}),
            ).get(f"http://127.0.0.1:{port}/health")
            assert response.status == 200
            assert response.body == b"healthy"
        finally:
            await server.close()

    run(scenario())



def test_http_chunked_and_oversize_rejection() -> None:
    async def scenario() -> None:
        async def factory(host, port, **kwargs):
            reader = asyncio.StreamReader()
            reader.feed_data(
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                b"3\r\nabc\r\n0\r\n\r\n"
            )
            reader.feed_eof()
            return reader, _MemoryWriter()
        response = await AsyncHTTPClient(
            capabilities=CapabilityContext({"network.http"}, network_hosts={"example.test"}),
            open_connection=factory,
        ).get("http://example.test/")
        assert response.body == b"abc"

        async def oversized(host, port, **kwargs):
            reader = asyncio.StreamReader()
            reader.feed_data(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nabcd")
            reader.feed_eof()
            return reader, _MemoryWriter()
        with pytest.raises(HTTPProtocolError, match="exceeds"):
            await AsyncHTTPClient(
                capabilities=CapabilityContext({"network.http"}, network_hosts={"example.test"}),
                open_connection=oversized,
                max_response_size=3,
            ).get("http://example.test/")
    run(scenario())


def test_https_uses_caller_ssl_context_before_connect() -> None:
    async def scenario() -> None:
        supplied = ssl.create_default_context()
        observed = {}
        async def factory(host, port, **kwargs):
            observed.update(kwargs)
            reader = asyncio.StreamReader()
            reader.feed_data(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            reader.feed_eof()
            return reader, _MemoryWriter()
        await AsyncHTTPClient(
            capabilities=CapabilityContext({"network.http"}, network_hosts={"example.test"}),
            open_connection=factory,
            ssl_context=supplied,
        ).get("https://example.test/")
        assert observed["ssl"] is supplied
        assert observed["server_hostname"] == "example.test"
    run(scenario())


def test_websocket_handshake_masking_and_close() -> None:
    async def scenario() -> None:


        reader = asyncio.StreamReader()
        writer = _HandshakeWriter(reader)
        async def factory(host, port, **kwargs):
            return reader, writer
        ws = await WebSocketClient(
            capabilities=CapabilityContext({"network.http"}, network_hosts={"example.test"}),
            open_connection=factory,
        ).connect("ws://example.test/chat")
        assert writer.data.startswith(b"GET /chat HTTP/1.1")
        # Server frames are unmasked; client sends masked frames.
        reader.feed_data(b"\x81\x02hi")
        frame = await ws.recv()
        assert frame.opcode == 1 and frame.data == b"hi"
        before = len(writer.data)
        await ws.send_text("ok")
        sent = writer.data[before:]
        assert sent[1] & 0x80
        mask = sent[2:6]
        assert bytes(value ^ mask[index % 4] for index, value in enumerate(sent[6:])) == b"ok"
        with pytest.raises(WebSocketProtocolError):
            reader.feed_data(b"\x81\x81a")  # server is not allowed to mask
            await ws.recv()
        await ws.close()
    run(scenario())
def test_websocket_real_local_echo() -> None:
    async def scenario() -> None:
        finished = asyncio.Event()

        async def handler(reader, writer) -> None:
            connection = await websocket_accept(reader, writer, max_frame_size=1024)
            frame = await connection.recv()
            await connection.send_text("echo:" + frame.data.decode())
            await connection.close()
            finished.set()

        listener = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        try:
            client = await WebSocketClient(
                capabilities=CapabilityContext({"network.http"}, network_hosts={"127.0.0.1"}),
                max_frame_size=1024,
            ).connect(f"ws://127.0.0.1:{port}/echo")
            await client.send_text("hello")
            response = await client.recv()
            assert response.data == b"echo:hello"
            await client.close()
            await asyncio.wait_for(finished.wait(), 1)
        finally:
            listener.close()
            await listener.wait_closed()

    run(scenario())


def test_runtime_http_and_websocket_bounds_are_enforced() -> None:
    with pytest.raises(ValueError, match="max_tasks"):
        AsyncRuntime().task_group(max_tasks=0)

    async def scenario() -> None:
        opened = 0

        async def unused_factory(host, port, **kwargs):
            nonlocal opened
            opened += 1
            return asyncio.StreamReader(), _MemoryWriter()

        client = AsyncHTTPClient(
            capabilities=CapabilityContext({"network.http"}, network_hosts={"example.test"}),
            open_connection=unused_factory,
            max_request_size=1,
        )
        with pytest.raises(HTTPProtocolError, match="request body exceeds"):
            await client.request("POST", "http://example.test/", body=b"xx")
        assert opened == 0

        async def oversized_header(host, port, **kwargs):
            reader = asyncio.StreamReader()
            reader.feed_data(b"HTTP/1.1 200 OK\r\nX-Large: " + b"a" * 1100 + b"\r\n\r\n")
            reader.feed_eof()
            return reader, _MemoryWriter()

        with pytest.raises(HTTPProtocolError, match="wire data exceeds"):
            await AsyncHTTPClient(
                capabilities=CapabilityContext({"network.http"}, network_hosts={"example.test"}),
                open_connection=oversized_header,
                max_header_size=1024,
            ).get("http://example.test/")

        with pytest.raises(TimeoutError):
            await AsyncHTTPClient(
                capabilities=CapabilityContext({"network.http"}, network_hosts={"example.test"}),
                open_connection=unused_factory,
            ).get("http://example.test/", timeout=0)

        reader = asyncio.StreamReader()
        writer = _MemoryWriter()

        async def ws_factory(host, port, **kwargs):
            return reader, writer

        with pytest.raises(HTTPProtocolError, match="header injection"):
            await WebSocketClient(
                capabilities=CapabilityContext({"network.http"}, network_hosts={"example.test"}),
                open_connection=ws_factory,
            ).connect("ws://example.test/", headers={"x-test": "ok\r\ninjected: yes"})
        assert writer.data == b""

        rejected_reader = asyncio.StreamReader()
        rejected_reader.feed_data(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
        rejected_writer = _MemoryWriter()

        async def rejected_factory(host, port, **kwargs):
            return rejected_reader, rejected_writer

        with pytest.raises(WebSocketProtocolError, match="upgrade rejected"):
            await WebSocketClient(
                capabilities=CapabilityContext({"network.http"}, network_hosts={"example.test"}),
                open_connection=rejected_factory,
            ).connect("ws://example.test/")
        assert rejected_writer.closed

        frame_reader = asyncio.StreamReader()
        frame_reader.feed_data(b"\x83\x00")
        connection = WebSocketConnection(frame_reader, _MemoryWriter(), is_client=True)
        with pytest.raises(WebSocketProtocolError, match="reserved"):
            await connection.recv()

        handshake_reader = asyncio.StreamReader()
        handshake_reader.feed_data(
            b"GET / HTTP/1.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: YQ==\r\n\r\n"
        )
        with pytest.raises(WebSocketProtocolError, match="websocket key"):
            await websocket_accept(handshake_reader, _MemoryWriter())

    run(scenario())


def test_database_close_failure_clears_active_transaction() -> None:
    class Tx:
        async def commit(self): pass
        async def rollback(self): pass
        async def close(self): raise RuntimeError("close failed")

    class Conn:
        async def begin(self): return Tx()
        async def close(self): pass

    class Driver:
        async def connect(self): return Conn()

    manager = DatabaseResourceManager(Driver(), capabilities=CapabilityContext({"database"}))

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="close failed"):
            async with manager:
                pass
        assert manager._active_transaction is None

    run(scenario())


def test_database_commit_rollback_and_close_once() -> None:
    class Tx:
        def __init__(self): self.calls = []
        async def commit(self): self.calls.append("commit")
        async def rollback(self): self.calls.append("rollback")
        async def close(self): self.calls.append("tx-close")
    class Conn:
        def __init__(self): self.tx, self.closed = Tx(), 0
        async def begin(self): return self.tx
        async def close(self): self.closed += 1
    class Driver:
        def __init__(self): self.conn = Conn()
        async def connect(self): return self.conn
    driver = Driver()
    async def scenario() -> None:
        async with DatabaseResourceManager(driver, capabilities=CapabilityContext({"database"})):
            pass
        assert driver.conn.tx.calls == ["commit", "tx-close"]
        assert driver.conn.closed == 1
        driver2 = Driver()
        with pytest.raises(RuntimeError):
            async with DatabaseResourceManager(driver2, capabilities=CapabilityContext({"database"})):
                raise RuntimeError("fail")
        assert driver2.conn.tx.calls == ["rollback", "tx-close"]
        assert driver2.conn.closed == 1
        with pytest.raises(DatabaseCapabilityError):
            async with DatabaseResourceManager(driver2, capabilities=CapabilityContext()):
                pass
    run(scenario())


class _MemoryWriter:
    def __init__(self):
        self.data = b""
        self.response = b""
        self.closed = False
    def write(self, data):
        self.data += bytes(data)
        if self.response:
            # The response is fed by the matching reader in tests that use it.
            pass
    async def drain(self):
        pass
    def close(self): self.closed = True
    async def wait_closed(self): pass


class _HandshakeWriter(_MemoryWriter):
    def __init__(self, reader):
        super().__init__()
        self.reader = reader
    def write(self, data):
        super().write(data)
        if data.startswith(b"GET "):
            key = next(line.split(b":", 1)[1].strip().decode() for line in data.split(b"\r\n") if line.lower().startswith(b"sec-websocket-key:"))
            accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            self.reader.feed_data(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + "\r\n\r\n").encode())
