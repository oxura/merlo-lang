"""Structured asynchronous execution and capability scopes for Merlo.

The module deliberately builds on :mod:`asyncio` rather than keeping a second
scheduler.  Tasks created by :class:`StructuredTaskGroup` are always joined
before the scope exits, including cancellation and error paths.
"""
from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass
from typing import Any, Awaitable, Coroutine, Iterable, TypeVar

from .runtime_contract import (
    CapabilityHostDeniedError,
    CapabilityManifest,
    MissingCapabilityError,
)

T = TypeVar("T")


@dataclass(frozen=True)
class CapabilityContext:
    """The authority visible to one async scope.

    ``capabilities`` may contain application capabilities (for example
    ``database``) in addition to the closed runtime effects in a manifest.
    Network hosts are always checked separately from the effect capability.
    """

    capabilities: frozenset[str] = frozenset()
    network_hosts: frozenset[str] = frozenset()

    def __init__(
        self,
        capabilities: Iterable[str] = (),
        *,
        network_hosts: Iterable[str] = (),
        manifest: CapabilityManifest | None = None,
    ) -> None:
        effects = set(str(item) for item in capabilities)
        hosts = set(str(item).lower() for item in network_hosts)
        if manifest is not None:
            effects.update(manifest.effects)
            hosts.update(host.lower() for host in manifest.network_hosts)
        object.__setattr__(self, "capabilities", frozenset(effects))
        object.__setattr__(self, "network_hosts", frozenset(hosts))

    @classmethod
    def from_manifest(cls, manifest: CapabilityManifest) -> "CapabilityContext":
        return cls(manifest=manifest)

    def require(self, capability: str, host: str | None = None) -> None:
        if capability not in self.capabilities:
            raise MissingCapabilityError(f"MissingCapability:{capability}")
        if host is not None and host.lower() not in self.network_hosts:
            raise CapabilityHostDeniedError(f"CapabilityHostDenied:{host}")


_CAPABILITIES: contextvars.ContextVar[CapabilityContext | None] = contextvars.ContextVar(
    "merlo_capability_context", default=None
)


class capability_scope:
    """Temporarily install a capability context for sync or async ``with``."""

    def __init__(self, context: CapabilityContext | CapabilityManifest) -> None:
        self.context = (
            CapabilityContext.from_manifest(context)
            if isinstance(context, CapabilityManifest)
            else context
        )
        self._token: contextvars.Token[CapabilityContext | None] | None = None

    def __enter__(self) -> CapabilityContext:
        self._token = _CAPABILITIES.set(self.context)
        return self.context

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._token is not None:
            _CAPABILITIES.reset(self._token)
            self._token = None

    async def __aenter__(self) -> CapabilityContext:
        return self.__enter__()

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.__exit__(exc_type, exc, tb)


def current_capabilities() -> CapabilityContext:
    context = _CAPABILITIES.get()
    if context is None:
        raise MissingCapabilityError("MissingCapability:scope")
    return context


def require_capability(capability: str, *, host: str | None = None) -> CapabilityContext:
    context = current_capabilities()
    context.require(capability, host)
    return context


class StructuredTaskGroup:
    """A bounded, fail-fast task group with deterministic joining."""

    def __init__(self, *, max_tasks: int = 32, name: str = "task-group") -> None:
        if type(max_tasks) is not int or max_tasks < 1:
            raise ValueError("max_tasks must be positive")
        if not isinstance(name, str) or not name:
            raise ValueError("task group name must be non-empty")
        self.max_tasks = max_tasks
        self.name = name
        self._tasks: set[asyncio.Task[Any]] = set()
        self._entered = False
        self._cancel_requested = False

    async def __aenter__(self) -> "StructuredTaskGroup":
        self._entered = True
        return self

    def create_task(
        self,
        awaitable: Coroutine[Any, Any, T] | Awaitable[T],
        *,
        name: str | None = None,
    ) -> asyncio.Task[T]:
        if not self._entered:
            raise RuntimeError("task group is not active")
        if len(self._tasks) >= self.max_tasks:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise RuntimeError(f"TaskGroupLimit:{self.max_tasks}")
        task = asyncio.create_task(awaitable, name=name or self.name)
        self._tasks.add(task)
        return task  # type: ignore[return-value]

    def cancel(self) -> None:
        self._cancel_requested = True
        for task in tuple(self._tasks):
            if not task.done():
                task.cancel()

    async def _join(self, cancel: bool = False) -> list[BaseException]:
        if cancel:
            self.cancel()
        pending = set(self._tasks)
        failures: list[BaseException] = []
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_EXCEPTION)
            child_failed = False
            for task in done:
                try:
                    task.result()
                except asyncio.CancelledError:
                    continue
                except BaseException as error:
                    failures.append(error)
                    child_failed = True
            if child_failed:
                for task in pending:
                    task.cancel()
        # FIRST_EXCEPTION does not necessarily observe a cancellation until
        # all tasks are done; gathering here consumes every task exception.
        for task in self._tasks:
            if task.done():
                try:
                    task.result()
                except (asyncio.CancelledError, BaseException) as error:
                    if not isinstance(error, asyncio.CancelledError) and error not in failures:
                        failures.append(error)
        return failures

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        body_error = exc if isinstance(exc, BaseException) else None
        failures = await self._join(cancel=body_error is not None or self._cancel_requested)
        self._entered = False
        if body_error is not None:
            if failures:
                raise ExceptionGroup(self.name, [body_error, *failures])
            return False
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise ExceptionGroup(self.name, failures)
        return False


# Familiar spelling for callers migrating from asyncio.TaskGroup.
AsyncTaskGroup = StructuredTaskGroup


class AsyncRuntime:
    """Small facade for bounded groups and scoped timeouts."""

    def __init__(self, *, max_tasks: int = 32) -> None:
        if type(max_tasks) is not int or max_tasks < 1:
            raise ValueError("max_tasks must be positive")
        self.max_tasks = max_tasks

    def task_group(self, *, max_tasks: int | None = None, name: str = "task-group") -> StructuredTaskGroup:
        return StructuredTaskGroup(
            max_tasks=self.max_tasks if max_tasks is None else max_tasks,
            name=name,
        )

    async def run(self, awaitable: Awaitable[T], *, timeout: float | None = None) -> T:
        return await run_with_timeout(awaitable, timeout)


async def run_with_timeout(awaitable: Awaitable[T], timeout: float | None) -> T:
    """Await ``awaitable`` with cancellation-safe timeout semantics."""
    if timeout is None:
        return await awaitable
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
        raise ValueError("timeout must be a non-negative number")
    try:
        return await asyncio.wait_for(awaitable, timeout)
    except asyncio.TimeoutError as error:
        raise TimeoutError(f"operation timed out after {timeout:g}s") from error


__all__ = [
    "AsyncRuntime", "AsyncTaskGroup", "CapabilityContext", "StructuredTaskGroup",
    "capability_scope", "current_capabilities", "require_capability", "run_with_timeout",
]
