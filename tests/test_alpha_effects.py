from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from merlo.frontend_model import ConciseApplicationError
from merlo.concise_services import elaborate_concise_application
from merlo.runtime_contract import (
    CLOSED_EFFECTS,
    CapabilityHostDeniedError,
    CapabilityManifest,
    CapabilityScopeEscapeError,
    GuardedHostRuntime,
    MissingCapabilityError,
    ResourceScope,
)


def _app(tmp_path: Path, source: str):
    root = tmp_path / "app"
    root.mkdir()
    path = root / "main.mlo"
    if not source.lstrip().startswith("module "):
        source = "module app.main\n" + source
    path.write_text(source, encoding="utf-8")
    return elaborate_concise_application(path, require_interface_lock=False)


def test_closed_effect_set_is_stable() -> None:
    assert CLOSED_EFFECTS == (
        "console.read", "console.write", "fs.read", "fs.write", "env.read",
        "clock.now", "random.read", "network.tcp", "network.http", "process.args",
    )


def test_transitive_task_effects_are_in_public_boundary(tmp_path: Path) -> None:
    app = _app(tmp_path, """\
fn pure() -> Text:
    "ok"

task emit() -> Unit:
    uses console.write
    console.write("ok")

export task main(path: Path) -> Text:
    uses console.write
    emit()
    pure()
    return "ok"
""")
    main = next(item for item in app.tasks if item.name == "main")
    assert main.effects == ("console.write",)
    assert app.interfaces[-1].effects == ("console.write",)



def test_effect_names_in_comments_and_strings_are_not_calls(tmp_path: Path) -> None:
    app = _app(tmp_path, """\
export fn describe() -> Text:
    # fs.read(path) is documentation, not a call.
    return "fs.read("

export task main(path: Path) -> Text:
    uses console.write
    console.write(describe())
    return describe()
""")

    assert app.effects == ("console.write",)

def test_pure_function_cannot_call_task(tmp_path: Path) -> None:
    with pytest.raises(ConciseApplicationError, match="EffectInPureFunction"):
        _app(tmp_path, """\
fn bad() -> Unit:
    emit()

task emit() -> Unit:
    uses console.write
    console.write("bad")

export task main(path: Path) -> Unit:
    uses console.write
    emit()
""")


def test_missing_capability_is_rejected_before_operation(tmp_path: Path) -> None:
    with pytest.raises(ConciseApplicationError, match="MissingCapability"):
        _app(tmp_path, """\
export task main(path: Path) -> Unit:
    uses fs.read
    console.write("bad")
""")


def test_runtime_scope_only_narrows_and_guards_host(tmp_path: Path) -> None:
    manifest = CapabilityManifest(("fs.read",), filesystem_roots=(str(tmp_path),))
    runtime = GuardedHostRuntime(manifest)
    with pytest.raises(MissingCapabilityError, match="MissingCapability"):
        runtime.console_write("no")
    with pytest.raises(CapabilityScopeEscapeError, match="CapabilityScopeEscape"):
        runtime.fs_read(tmp_path.parent / "outside")


def test_resource_scope_closes_all_resources_on_error() -> None:
    closed: list[str] = []

    class Resource:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            closed.append(self.name)

    with pytest.raises(RuntimeError, match="boom"):
        with ResourceScope() as scope:
            scope.own(Resource("first"))
            scope.own(Resource("second"))
            raise RuntimeError("boom")
    assert closed == ["second", "first"]

def test_runtime_manifest_allows_safe_env_and_rejects_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAFE", "ok")
    monkeypatch.setenv("SECRET", "no")
    runtime = GuardedHostRuntime(
        CapabilityManifest(("env.read",), environment_keys=("SAFE",))
    )
    assert runtime.env_read("SAFE") == "ok"
    with pytest.raises(CapabilityScopeEscapeError, match="CapabilityScopeEscape"):
        runtime.env_read("SECRET")


def test_runtime_filesystem_and_network_allowlists_are_exact(tmp_path: Path) -> None:
    child = tmp_path / "child.txt"
    child.write_bytes(b"ok")
    runtime = GuardedHostRuntime(
        CapabilityManifest(
            ("fs.read", "network.tcp"),
            filesystem_roots=(str(tmp_path),),
            network_hosts=("localhost",),
        )
    )
    assert runtime.fs_read(child) == b"ok"
    with pytest.raises(CapabilityScopeEscapeError, match="CapabilityScopeEscape"):
        runtime.fs_read(tmp_path.parent / f"{tmp_path.name}2" / "sibling")
    with pytest.raises(CapabilityHostDeniedError, match="CapabilityHostDenied"):
        runtime.tcp_connect("127.0.0.1", 1)


def test_runtime_random_uses_requested_length() -> None:
    data = GuardedHostRuntime(CapabilityManifest(("random.read",))).random_read(32)
    assert len(data) == 32



def test_runtime_preserves_line_all_and_argument_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = io.TextIOWrapper(io.BytesIO(b"first\nsecond"))
    monkeypatch.setattr(sys, "stdin", stream)
    console = GuardedHostRuntime(CapabilityManifest(("console.read",)))
    assert console.console_read_line() == "first\n"
    assert console.console_read_all() == "second"
    invalid = io.TextIOWrapper(io.BytesIO(b"\xff"))
    monkeypatch.setattr(sys, "stdin", invalid)
    with pytest.raises(UnicodeDecodeError):
        console.console_read_all()

    process = GuardedHostRuntime(
        CapabilityManifest(("process.args",), process_arguments=True),
        argv=("app", "--name", "Merlo"),
    )
    assert process.process_args() == 2
    assert process.process_arg(0) == "--name"
    assert process.process_arg(1) == "Merlo"
    assert process.process_arg(2) == ""


def test_tcp_handle_is_owned_and_shared_with_narrowed_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []

    class Connection:
        def fileno(self) -> int:
            return 41

        def send(self, data: bytes) -> int:
            return len(data)

        def recv(self, limit: int) -> bytes:
            return b"ok"[:limit]

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(
        "merlo.runtime_contract.socket.create_connection",
        lambda address, timeout=None: Connection(),
    )
    manifest = CapabilityManifest(
        ("network.tcp",),
        network_hosts=("localhost",),
    )
    runtime = GuardedHostRuntime(manifest)
    narrowed = runtime.scope(manifest)
    with ResourceScope() as resources:
        handle = resources.own(runtime.tcp_connect("localhost", 80))
        assert narrowed.tcp_send(handle, b"data") == 4
        assert narrowed.tcp_receive(handle, 2) == b"ok"
    assert closed == [True]
