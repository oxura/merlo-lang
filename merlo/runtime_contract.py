"""Closed effects and scoped host authority for Merlo alpha runtime.

The compiler describes *what* a task may do with effect names. This module
supplies the host-side *authority* required to perform those effects. No
ambient authority is used: every operation checks the active manifest before
it touches the host.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

RUNTIME_CONTRACT = "merlo.runtime-contract.v1"
RUNTIME_ABI_VERSION = 1
CLOSED_EFFECTS: tuple[str, ...] = (
    "console.read", "console.write", "fs.read", "fs.write", "env.read",
    "clock.now", "random.read", "network.tcp", "network.http", "process.args",
)
ALPHA_EFFECTS = frozenset(CLOSED_EFFECTS)

@dataclass(frozen=True)
class RuntimeContract:
    abi_version: int = RUNTIME_ABI_VERSION
    effects: tuple[str, ...] = CLOSED_EFFECTS
    synchronous: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "contract": RUNTIME_CONTRACT,
            "abi_version": self.abi_version,
            "effects": list(self.effects),
            "synchronous": self.synchronous,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


RUNTIME = RuntimeContract()
EFFECTS = CLOSED_EFFECTS


class RuntimeContractError(PermissionError):
    diagnostic = "RuntimeCapabilityViolation"


class MissingCapabilityError(RuntimeContractError):
    diagnostic = "MissingCapability"


class CapabilityScopeEscapeError(RuntimeContractError):
    diagnostic = "CapabilityScopeEscape"


class CapabilityHostDeniedError(RuntimeContractError):
    diagnostic = "CapabilityHostDenied"


class ResourceCloseError(RuntimeError):
    diagnostic = "ResourceCloseFailure"


@dataclass(frozen=True)
class CapabilityManifest:
    """Immutable, deterministic authority for one task scope."""

    effects: tuple[str, ...] = ()
    filesystem_roots: tuple[str, ...] = ()
    network_hosts: tuple[str, ...] = ()
    environment_keys: tuple[str, ...] = ()
    process_arguments: bool = False

    def __post_init__(self) -> None:
        effects = tuple(sorted(set(self.effects)))
        unknown = set(effects) - ALPHA_EFFECTS
        if unknown:
            raise ValueError(f"UnsupportedEffect:{sorted(unknown)}")
        object.__setattr__(self, "effects", effects)
        object.__setattr__(self, "filesystem_roots", tuple(sorted(set(map(str, self.filesystem_roots)))))
        object.__setattr__(self, "network_hosts", tuple(sorted(set(map(str, self.network_hosts)))))
        object.__setattr__(self, "environment_keys", tuple(sorted(set(map(str, self.environment_keys)))))
        if self.process_arguments and "process.args" not in effects:
            raise ValueError("ProcessArgumentsCapabilityRequired")

    @classmethod
    def empty(cls) -> "CapabilityManifest":
        return cls()

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CapabilityManifest":
        return cls(
            tuple(str(item) for item in payload.get("effects", ())),
            tuple(str(item) for item in payload.get("filesystem_roots", ())),
            tuple(str(item) for item in payload.get("network_hosts", ())),
            tuple(str(item) for item in payload.get("environment_keys", ())),
            bool(payload.get("process_arguments", False)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1, "contract": RUNTIME_CONTRACT,
            "effects": list(self.effects),
            "filesystem_roots": list(self.filesystem_roots),
            "network_hosts": list(self.network_hosts),
            "environment_keys": list(self.environment_keys),
            "process_arguments": self.process_arguments,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def allows(self, effect: str) -> bool:
        return effect in self.effects

    def narrowed(
        self, *, effects: Iterable[str] | None = None,
        filesystem_roots: Iterable[str] | None = None,
        network_hosts: Iterable[str] | None = None,
        environment_keys: Iterable[str] | None = None,
        process_arguments: bool | None = None,
    ) -> "CapabilityManifest":
        selected_effects = tuple(self.effects if effects is None else effects)
        selected_roots = tuple(self.filesystem_roots if filesystem_roots is None else filesystem_roots)
        selected_hosts = tuple(self.network_hosts if network_hosts is None else network_hosts)
        selected_keys = tuple(self.environment_keys if environment_keys is None else environment_keys)
        selected_args = self.process_arguments if process_arguments is None else process_arguments
        if not set(selected_effects) <= set(self.effects):
            raise CapabilityScopeEscapeError("CapabilityScopeEscape: effects may only narrow")
        parent_roots = tuple(Path(root).resolve() for root in self.filesystem_roots)
        if any(
            not any(candidate == parent or parent in candidate.parents for parent in parent_roots)
            for candidate in (Path(root).resolve() for root in selected_roots)
        ):
            raise CapabilityScopeEscapeError("CapabilityScopeEscape: filesystem roots may only narrow")
        if not set(selected_hosts) <= set(self.network_hosts):
            raise CapabilityScopeEscapeError("CapabilityScopeEscape: network hosts may only narrow")
        if not set(selected_keys) <= set(self.environment_keys):
            raise CapabilityScopeEscapeError("CapabilityScopeEscape: environment keys may only narrow")
        if selected_args and not self.process_arguments:
            raise CapabilityScopeEscapeError("CapabilityScopeEscape: process arguments may only narrow")
        return CapabilityManifest(selected_effects, selected_roots, selected_hosts, selected_keys, selected_args)


RuntimeCapabilityManifest = CapabilityManifest
ScopedCapabilityManifest = CapabilityManifest


class ResourceScope:
    """Tracks owned resources and closes every resource on every exit path."""

    def __init__(self) -> None:
        self._resources: list[object] = []
        self._closed = False

    def own(self, resource: object) -> object:
        if self._closed:
            raise ResourceCloseError("ResourceScopeClosed")
        if not callable(getattr(resource, "close", None)):
            raise TypeError("resource must provide close()")
        self._resources.append(resource)
        return resource

    def close_all(self) -> None:
        if self._closed:
            return
        self._closed = True
        failures: list[BaseException] = []
        for resource in reversed(self._resources):
            try:
                resource.close()  # type: ignore[attr-defined]
            except BaseException as exc:
                failures.append(exc)
        self._resources.clear()
        if failures:
            raise ResourceCloseError(f"ResourceCloseFailure:{len(failures)}") from failures[0]

    def __enter__(self) -> "ResourceScope":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close_all()


class GuardedHostRuntime:
    """Synchronous low-level host primitives guarded by a manifest."""

    def __init__(self, manifest: CapabilityManifest, *, argv: Sequence[str] = ()) -> None:
        self.manifest = manifest
        self.argv = tuple(argv)

    def require(self, effect: str) -> None:
        if effect not in ALPHA_EFFECTS:
            raise ValueError(f"UnsupportedEffect:{effect}")
        if not self.manifest.allows(effect):
            raise MissingCapabilityError(f"MissingCapability:{effect}")

    def scope(self, manifest: CapabilityManifest) -> "GuardedHostRuntime":
        narrowed = self.manifest.narrowed(
            effects=manifest.effects, filesystem_roots=manifest.filesystem_roots,
            network_hosts=manifest.network_hosts, environment_keys=manifest.environment_keys,
            process_arguments=manifest.process_arguments,
        )
        return GuardedHostRuntime(narrowed, argv=self.argv)

    def console_read(self) -> bytes:
        self.require("console.read")
        return sys.stdin.buffer.readline()

    def console_write(self, value: bytes | str) -> None:
        self.require("console.write")
        data = value.encode() if isinstance(value, str) else bytes(value)
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    def _path(self, value: str | os.PathLike[str], *, write: bool) -> Path:
        effect = "fs.write" if write else "fs.read"
        self.require(effect)
        candidate = Path(value).resolve()
        roots = tuple(Path(root).resolve() for root in self.manifest.filesystem_roots)
        if not roots or not any(candidate == root or root in candidate.parents for root in roots):
            raise CapabilityScopeEscapeError(f"CapabilityScopeEscape:{candidate}")
        return candidate

    def fs_read(self, path: str | os.PathLike[str]) -> bytes:
        return self._path(path, write=False).read_bytes()

    def fs_write(self, path: str | os.PathLike[str], data: bytes | bytearray | memoryview) -> None:
        self._path(path, write=True).write_bytes(bytes(data))

    def env_read(self, key: str) -> str | None:
        self.require("env.read")
        if key not in self.manifest.environment_keys:
            raise CapabilityScopeEscapeError(f"CapabilityScopeEscape:environment:{key}")
        return os.environ.get(key)

    def clock_now(self) -> int:
        self.require("clock.now")
        return time.time_ns()

    def random_read(self, size: int) -> bytes:
        self.require("random.read")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError("random size must be a non-negative integer")
        return os.urandom(size)

    def tcp_connect(self, host: str, port: int, *, timeout: float | None = None) -> socket.socket:
        self.require("network.tcp")
        if host not in self.manifest.network_hosts:
            raise CapabilityHostDeniedError(f"CapabilityHostDenied:{host}")
        return socket.create_connection((host, port), timeout=timeout)

    def http_request(self, url: str, *, method: str = "GET", body: bytes | None = None) -> bytes:
        self.require("network.http")
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        if host not in self.manifest.network_hosts:
            raise CapabilityHostDeniedError(f"CapabilityHostDenied:{host}")
        request = urllib.request.Request(url, data=body, method=method)
        with urllib.request.urlopen(request) as response:
            return response.read()

    def process_args(self) -> tuple[str, ...]:
        self.require("process.args")
        return self.argv


HostRuntime = GuardedHostRuntime


def capability_manifest_for(effects: Iterable[str], **kwargs: object) -> CapabilityManifest:
    return CapabilityManifest(tuple(effects), **kwargs)  # type: ignore[arg-type]


__all__ = [
    "ALPHA_EFFECTS", "CLOSED_EFFECTS", "CapabilityHostDeniedError",
    "CapabilityManifest", "CapabilityScopeEscapeError", "EFFECTS",
    "GuardedHostRuntime", "HostRuntime", "MissingCapabilityError",
    "ResourceCloseError", "ResourceScope", "RuntimeCapabilityManifest",
    "RuntimeContract", "RuntimeContractError", "RUNTIME", "RUNTIME_ABI_VERSION",
    "RUNTIME_CONTRACT", "ScopedCapabilityManifest", "capability_manifest_for",
]
