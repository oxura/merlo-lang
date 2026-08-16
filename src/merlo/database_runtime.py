"""Database driver protocols and lifetime-safe transaction management.

This module intentionally does not implement SQL or provide a test backend.
Drivers own transport and query semantics; the manager only enforces authority
and the commit/rollback/close lifetime contract.
"""
from __future__ import annotations

import inspect
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, AsyncIterator, Protocol, TypeVar, runtime_checkable

from .async_runtime import CapabilityContext, current_capabilities
from .runtime_contract import CapabilityManifest

T = TypeVar("T")


@runtime_checkable
class Transaction(Protocol):
    async def commit(self) -> Any: ...
    async def rollback(self) -> Any: ...
    async def close(self) -> Any: ...


@runtime_checkable
class Connection(Protocol):
    async def begin(self) -> Transaction: ...
    async def close(self) -> Any: ...


@runtime_checkable
class Driver(Protocol):
    async def connect(self) -> Connection: ...


class DatabaseCapabilityError(PermissionError):
    diagnostic = "DatabaseCapabilityDenied"


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class TransactionScope(AbstractAsyncContextManager[Transaction]):
    """A transaction scope around a manager-owned connection."""

    def __init__(self, manager: "DatabaseResourceManager") -> None:
        self.manager = manager
        self.transaction: Transaction | None = None
        self._closed = False

    async def __aenter__(self) -> Transaction:
        if self.manager.connection is None:
            raise RuntimeError("database manager is not active")
        if self.manager._active_transaction is not None:
            raise RuntimeError("a transaction is already active")
        self.transaction = await _maybe_await(self.manager.connection.begin())
        self.manager._active_transaction = self.transaction
        return self.transaction

    async def _close_once(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.transaction is not None:
                await _maybe_await(self.transaction.close())
        finally:
            self.manager._active_transaction = None

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        transaction = self.transaction
        if transaction is None:
            return False
        error = exc if isinstance(exc, BaseException) else None
        operation_error: BaseException | None = None
        try:
            if error is None:
                await _maybe_await(transaction.commit())
            else:
                await _maybe_await(transaction.rollback())
        except BaseException as operation:
            operation_error = operation
            if error is None:
                try:
                    await _maybe_await(transaction.rollback())
                except BaseException as rollback_error:
                    operation_error = ExceptionGroup("transaction commit/rollback", [operation, rollback_error])
        finally:
            try:
                await self._close_once()
            except BaseException as close_error:
                if operation_error is None:
                    operation_error = close_error
                else:
                    operation_error = ExceptionGroup("transaction close", [operation_error, close_error])
        if operation_error is not None:
            if error is not None:
                raise ExceptionGroup("transaction scope", [error, operation_error])
            raise operation_error
        return False


class DatabaseResourceManager(AbstractAsyncContextManager["DatabaseResourceManager"]):
    """Acquire one connection and implicit transaction, closing each once."""

    def __init__(
        self,
        driver: Driver,
        *,
        capabilities: CapabilityContext | CapabilityManifest | None = None,
        capability: str = "database",
    ) -> None:
        self.driver = driver
        self.capability = capability
        if isinstance(capabilities, CapabilityManifest):
            self.context = CapabilityContext.from_manifest(capabilities)
        else:
            self.context = capabilities
        self.connection: Connection | None = None
        self._active_transaction: Transaction | None = None
        self._transaction_scope: TransactionScope | None = None
        self._connection_closed = False
        self._entered = False

    def _require(self) -> None:
        try:
            context = self.context or current_capabilities()
        except PermissionError as error:
            raise DatabaseCapabilityError(f"DatabaseCapabilityDenied:{self.capability}") from error
        if self.capability not in context.capabilities:
            raise DatabaseCapabilityError(f"DatabaseCapabilityDenied:{self.capability}")

    async def __aenter__(self) -> "DatabaseResourceManager":
        if self._entered:
            raise RuntimeError("database manager is already active")
        self._require()
        self.connection = await _maybe_await(self.driver.connect())
        self._entered = True
        self._connection_closed = False
        self._transaction_scope = TransactionScope(self)
        try:
            await self._transaction_scope.__aenter__()
        except BaseException:
            await self._close_connection_once()
            self._entered = False
            raise
        return self

    async def _close_connection_once(self) -> None:
        if self.connection is not None and not self._connection_closed:
            self._connection_closed = True
            await _maybe_await(self.connection.close())

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if not self._entered:
            return False
        operation_error: BaseException | None = None
        try:
            if self._transaction_scope is not None:
                await self._transaction_scope.__aexit__(exc_type, exc, tb)
        except BaseException as error:
            operation_error = error
        finally:
            try:
                await self._close_connection_once()
            except BaseException as close_error:
                operation_error = close_error if operation_error is None else ExceptionGroup("database close", [operation_error, close_error])
            self._entered = False
            self.connection = None
        if operation_error is not None:
            if isinstance(exc, BaseException):
                raise ExceptionGroup("database scope", [exc, operation_error])
            raise operation_error
        return False

    def transaction(self) -> TransactionScope:
        """Create an explicit transaction scope on the acquired connection."""
        return TransactionScope(self)


@asynccontextmanager
async def managed_transaction(
    driver: Driver,
    *,
    capabilities: CapabilityContext | CapabilityManifest | None = None,
    capability: str = "database",
) -> AsyncIterator[Transaction]:
    manager = DatabaseResourceManager(driver, capabilities=capabilities, capability=capability)
    async with manager:
        assert manager._active_transaction is not None
        yield manager._active_transaction


DatabaseManager = DatabaseResourceManager

__all__ = [
    "Connection", "DatabaseCapabilityError", "DatabaseManager", "DatabaseResourceManager", "Driver", "Transaction", "TransactionScope", "managed_transaction",
]
