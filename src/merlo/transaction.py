from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from merlo.refactor import ChangeIR

TRANSACTION_SCHEMA_VERSION = 1
TRANSACTION_CONTRACT = "merlo.change-transaction.v1"
_HASH_LENGTH = 64


class TransactionError(ValueError):
    """Raised when a transaction is invalid, stale, or cannot be materialized."""


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _valid_hash(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != _HASH_LENGTH:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _source_bytes(value: Any, field: str) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, bytes):
        try:
            value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TransactionError(f"TransactionInvalid{field}: source is not UTF-8") from exc
        return value
    raise TransactionError(f"TransactionInvalid{field}: source must be str or UTF-8 bytes")


def _freeze_map(value: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType(dict(sorted(value.items())))


def _unfreeze(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value[key] for key in value}


def _normal_relative(path: Any) -> str:
    if not isinstance(path, str) or not path or "\x00" in path:
        raise TransactionError("TransactionInvalidPath")
    candidate = Path(path)
    if candidate.is_absolute():
        raise TransactionError("TransactionInvalidPath: manifest paths must be relative")
    # Pure lexical checks make the persisted representation canonical even when
    # the path does not exist yet. Backslashes are separators on Windows and
    # are forbidden here as a cross-platform ambiguity.
    if "\\" in path:
        raise TransactionError("TransactionInvalidPath: backslashes are forbidden")
    parts = candidate.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise TransactionError("TransactionInvalidPath: path is not normalized")
    normalized = candidate.as_posix()
    if normalized != path or normalized.startswith("../") or normalized == "..":
        raise TransactionError("TransactionInvalidPath: path is not normalized")
    return normalized


def _resolved_root(root: Any) -> Path:
    try:
        value = Path(root).resolve(strict=True)
    except (OSError, TypeError) as exc:
        raise TransactionError("TransactionInvalidRoot") from exc
    if not value.is_dir():
        raise TransactionError("TransactionInvalidRoot")
    return value


def _relative_to_root(path: Any, root: Path) -> str:
    try:
        raw = Path(path)
    except TypeError as exc:
        raise TransactionError("TransactionInvalidPath") from exc
    if not str(raw) or "\x00" in str(raw):
        raise TransactionError("TransactionInvalidPath")
    candidate = raw if raw.is_absolute() else root / raw
    try:
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise TransactionError("TransactionPathEscape") from exc
    if relative == Path(".") or not relative.parts:
        raise TransactionError("TransactionInvalidPath")
    return _normal_relative(relative.as_posix())

def _journal_path(
    root: Path,
    transaction_id: str,
) -> Path:
    path = (
        root
        / ".merlo"
        / "transactions"
        / f"{transaction_id}.json"
    )
    try:
        resolved = path.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise TransactionError(
            "TransactionJournalPathEscape"
        ) from exc
    if resolved != path:
        raise TransactionError(
            "TransactionJournalSymlinkPath"
        )
    return path



def _verified_change(
    change: Any,
) -> ChangeIR:
    if not isinstance(change, ChangeIR):
        raise TransactionError(
            "TransactionExpectedChangeIR"
        )
    payload = change.to_dict()
    supplied = payload.pop("digest", None)
    if (
        supplied != _digest(payload)
        or change.status != "ready"
        or not change.edits
    ):
        raise TransactionError(
            "TransactionInvalidChangeIR"
        )
    return change


def _change_paths(
    change: ChangeIR,
    root: Path,
) -> set[str]:
    return {
        _relative_to_root(edit.path, root)
        for edit in change.edits
    }


@dataclass(frozen=True, slots=True)
class TransactionFile:
    path: str
    before_sha256: str
    after_sha256: str
    before_content: str
    after_content: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _normal_relative(self.path))
        if not _valid_hash(self.before_sha256) or not _valid_hash(self.after_sha256):
            raise TransactionError("TransactionInvalidFileHash")
        before = _source_bytes(self.before_content, "BeforeContent")
        after = _source_bytes(self.after_content, "AfterContent")
        if _hash_bytes(before) != self.before_sha256:
            raise TransactionError("TransactionBeforeHashMismatch")
        if _hash_bytes(after) != self.after_sha256:
            raise TransactionError("TransactionAfterHashMismatch")
        if self.before_sha256 == self.after_sha256:
            raise TransactionError(
                "TransactionFileUnchanged"
            )
        # Persisted snapshots are text, not an alternate binary channel.
        if not isinstance(self.before_content, str) or not isinstance(self.after_content, str):
            raise TransactionError("TransactionInvalidContent")

    def _payload(self) -> dict[str, str]:
        return {
            "path": self.path,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "before_content": self.before_content,
            "after_content": self.after_content,
        }

    def to_dict(self) -> dict[str, str]:
        return self._payload()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TransactionFile":
        fields = {"path", "before_sha256", "after_sha256", "before_content", "after_content"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise TransactionError("TransactionFileSchemaMismatch")
        return cls(
            path=value["path"],
            before_sha256=value["before_sha256"],
            after_sha256=value["after_sha256"],
            before_content=value["before_content"],
            after_content=value["after_content"],
        )


@dataclass(frozen=True, slots=True)
class ChangeTransaction:
    root: str
    change_digest: str
    files: tuple[TransactionFile, ...]
    world_digest: str
    schema_version: int = TRANSACTION_SCHEMA_VERSION
    contract: str = TRANSACTION_CONTRACT
    digest: str = ""
    transaction_id: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != TRANSACTION_SCHEMA_VERSION:
            raise TransactionError("TransactionVersionMismatch")
        if self.contract != TRANSACTION_CONTRACT:
            raise TransactionError("TransactionContractMismatch")
        try:
            root = str(Path(self.root).resolve(strict=False))
        except (OSError, TypeError) as exc:
            raise TransactionError("TransactionInvalidRoot") from exc
        if not Path(root).is_absolute() or not root:
            raise TransactionError("TransactionInvalidRoot")
        object.__setattr__(self, "root", root)
        if not _valid_hash(self.change_digest):
            raise TransactionError("TransactionInvalidChangeDigest")
        if not _valid_hash(self.world_digest):
            raise TransactionError("TransactionInvalidWorldDigest")
        files = tuple(item if isinstance(item, TransactionFile) else TransactionFile.from_dict(item) for item in self.files)
        if not files:
            raise TransactionError("TransactionEmpty")
        if tuple(item.path for item in files) != tuple(sorted(item.path for item in files)):
            raise TransactionError("TransactionFilesNotSorted")
        if len({item.path for item in files}) != len(files):
            raise TransactionError("TransactionDuplicatePath")
        object.__setattr__(self, "files", files)
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise TransactionError("TransactionDigestMismatch")
        object.__setattr__(self, "digest", expected)
        expected_id = hashlib.sha256((expected + self.change_digest).encode("ascii")).hexdigest()
        if self.transaction_id and self.transaction_id != expected_id:
            raise TransactionError("TransactionIdMismatch")
        object.__setattr__(self, "transaction_id", expected_id)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "root": self.root,
            "change_digest": self.change_digest,
            "world_digest": self.world_digest,
            "files": [item.to_dict() for item in self.files],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest, "transaction_id": self.transaction_id}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChangeTransaction":
        fields = {"schema_version", "contract", "root", "change_digest", "world_digest", "files", "digest", "transaction_id"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise TransactionError("TransactionSchemaMismatch")
        payload = {key: value[key] for key in fields if key not in {"digest", "transaction_id"}}
        if value.get("digest") != _digest(payload):
            raise TransactionError("TransactionDigestMismatch")
        expected_id = hashlib.sha256((value["digest"] + value["change_digest"]).encode("ascii")).hexdigest()
        if value.get("transaction_id") != expected_id:
            raise TransactionError("TransactionIdMismatch")
        if not isinstance(value["files"], list):
            raise TransactionError("TransactionSchemaMismatch")
        return cls(
            root=value["root"],
            change_digest=value["change_digest"],
            files=tuple(TransactionFile.from_dict(item) for item in value["files"]),
            world_digest=value["world_digest"],
            schema_version=value["schema_version"],
            contract=value["contract"],
            digest=value["digest"],
            transaction_id=value["transaction_id"],
        )

    @classmethod
    def from_json(cls, value: str) -> "ChangeTransaction":
        try:
            data = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise TransactionError("TransactionSchemaMismatch") from exc
        return cls.from_dict(data)

    @property
    def journal_path(self) -> Path:
        return _journal_path(
            Path(self.root),
            self.transaction_id,
        )

    def save(self, destination: str | os.PathLike[str] | None = None) -> Path:
        return save_transaction(self, destination)

    def commit(self, root: str | os.PathLike[str] | None = None) -> "TransactionResult":
        return commit(self, root)

    def rollback(self, root: str | os.PathLike[str] | None = None) -> "TransactionResult":
        return rollback(self, root)

    def replay(self, root: str | os.PathLike[str] | None = None) -> "TransactionResult":
        return replay(self, root)


@dataclass(frozen=True, slots=True)
class TransactionResult:
    transaction_id: str
    transaction_digest: str
    action: str
    changed: bool
    files: tuple[str, ...]
    resulting_hashes: Mapping[str, str]

    def __post_init__(self) -> None:
        if self.action not in {"commit", "rollback", "replay"}:
            raise TransactionError("TransactionInvalidResultAction")
        if not _valid_hash(self.transaction_digest) or not self.transaction_id:
            raise TransactionError("TransactionInvalidResult")
        names = tuple(self.files)
        if names != tuple(sorted(names)) or len(set(names)) != len(names):
            raise TransactionError("TransactionInvalidResultFiles")
        hashes = dict(self.resulting_hashes)
        if set(hashes) != set(names) or any(not _valid_hash(item) for item in hashes.values()):
            raise TransactionError("TransactionInvalidResultHashes")
        object.__setattr__(self, "files", names)
        object.__setattr__(self, "resulting_hashes", _freeze_map(hashes))

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "transaction_digest": self.transaction_digest,
            "action": self.action,
            "changed": self.changed,
            "files": list(self.files),
            "resulting_hashes": _unfreeze(self.resulting_hashes),
        }


def prepare_transaction(
    change: ChangeIR,
    root: str | os.PathLike[str],
    originals: Mapping[Any, Any],
    updated: Mapping[Any, Any],
) -> ChangeTransaction:
    resolved = _resolved_root(root)
    verified_change = _verified_change(change)
    change_digest = verified_change.digest
    world_digest = (
        verified_change.expected_world_digest
    )
    if not isinstance(originals, Mapping) or not isinstance(updated, Mapping):
        raise TransactionError("TransactionSnapshotsMustBeMappings")
    before: dict[str, str] = {}
    after: dict[str, str] = {}
    for source, destination in ((originals, before), (updated, after)):
        for path, content in source.items():
            relative = _relative_to_root(path, resolved)
            if relative in destination:
                raise TransactionError("TransactionDuplicatePath")
            raw = _source_bytes(content, "Content")
            destination[relative] = raw.decode("utf-8")
    if set(before) != set(after) or not before:
        raise TransactionError("TransactionSnapshotPathsMismatch")
    expected_paths = _change_paths(
        verified_change,
        resolved,
    )
    if expected_paths != set(before):
        raise TransactionError(
            "TransactionChangeFilesMismatch"
        )
    files = tuple(
        TransactionFile(
            path=path,
            before_content=before[path],
            after_content=after[path],
            before_sha256=_hash_bytes(before[path].encode("utf-8")),
            after_sha256=_hash_bytes(after[path].encode("utf-8")),
        )
        for path in sorted(before)
    )
    return ChangeTransaction(root=str(resolved), change_digest=change_digest, files=files, world_digest=world_digest)


def _atomic_replace(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.merlo-", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def save_transaction(transaction: ChangeTransaction, destination: str | os.PathLike[str] | None = None) -> Path:
    if not isinstance(transaction, ChangeTransaction):
        raise TransactionError("TransactionExpected")
    if destination is None:
        journal = transaction.journal_path
    else:
        supplied = Path(destination).resolve(
            strict=False
        )
        expected = transaction.journal_path
        if supplied != expected:
            raise TransactionError(
                "TransactionJournalPathMismatch"
            )
        journal = expected
    journal.parent.mkdir(parents=True, exist_ok=True)
    encoded = (transaction.to_json() + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{journal.name}.", dir=str(journal.parent))
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, journal)
        directory_fd = os.open(journal.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
    return journal


def load_transaction(
    source: str | os.PathLike[str],
    transaction_id: str | None = None,
) -> ChangeTransaction:
    path = Path(source)
    if transaction_id is not None:
        root = _resolved_root(path)
        if not _valid_hash(transaction_id):
            raise TransactionError(
                "TransactionInvalidId"
            )
        path = _journal_path(
            root,
            transaction_id,
        )
    elif path.is_dir():
        raise TransactionError(
            "TransactionJournalRequired"
        )
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TransactionError(
            "TransactionJournalUnavailable"
        ) from exc
    transaction = ChangeTransaction.from_json(
        value
    )
    if (
        path.resolve(strict=False)
        != transaction.journal_path
    ):
        raise TransactionError(
            "TransactionJournalPathMismatch"
        )
    if (
        transaction_id is not None
        and transaction.transaction_id
        != transaction_id
    ):
        raise TransactionError(
            "TransactionIdMismatch"
        )
    return transaction


def _ensure_journal(transaction: ChangeTransaction) -> None:
    journal = transaction.journal_path
    if journal.exists():
        loaded = load_transaction(journal)
        if loaded.to_json() != transaction.to_json():
            raise TransactionError("TransactionJournalMismatch")
    else:
        save_transaction(transaction)


def _safe_path(
    transaction: ChangeTransaction,
    item: TransactionFile,
    root: Path,
) -> Path:
    path = root / item.path
    try:
        resolved = path.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise TransactionError(
            "TransactionPathEscape"
        ) from exc
    if resolved != path:
        raise TransactionError(
            "TransactionSymlinkPath"
        )
    return path


def _validate_state(
    transaction: ChangeTransaction,
    root: Path,
    destination: str,
    *,
    allow_mixed: bool = False,
) -> tuple[
    list[tuple[Path, TransactionFile, bytes]],
    bool,
]:
    if root != Path(transaction.root):
        raise TransactionError(
            "TransactionRootMismatch"
        )
    states: list[bool] = []
    rows: list[
        tuple[Path, TransactionFile, bytes]
    ] = []
    for item in transaction.files:
        path = _safe_path(
            transaction,
            item,
            root,
        )
        try:
            current = path.read_bytes()
        except (OSError, UnicodeError) as exc:
            raise TransactionError(
                "TransactionSourceUnavailable"
            ) from exc
        current_hash = _hash_bytes(current)
        before_bytes = item.before_content.encode(
            "utf-8"
        )
        after_bytes = item.after_content.encode(
            "utf-8"
        )
        if current_hash == item.before_sha256:
            if current != before_bytes:
                raise TransactionError(
                    "TransactionContentMismatch"
                )
            in_destination = (
                destination == "before"
            )
        elif current_hash == item.after_sha256:
            if current != after_bytes:
                raise TransactionError(
                    "TransactionContentMismatch"
                )
            in_destination = (
                destination == "after"
            )
        else:
            raise TransactionError(
                "TransactionStaleSource"
            )
        states.append(in_destination)
        rows.append((path, item, current))
    if (
        any(states)
        and not all(states)
        and not allow_mixed
    ):
        raise TransactionError(
            "TransactionMixedState"
        )
    return rows, not all(states)


def _transition(transaction: ChangeTransaction, root: str | os.PathLike[str] | None, action: str, destination: str) -> TransactionResult:
    resolved = _resolved_root(transaction.root if root is None else root)
    _ensure_journal(transaction)
    rows, changed = _validate_state(
        transaction,
        resolved,
        destination,
        allow_mixed=action
        in {"rollback", "replay"},
    )
    if changed:
        touched: list[tuple[Path, bytes]] = []
        try:
            for path, item, current in rows:
                target = item.after_content if destination == "after" else item.before_content
                target_bytes = target.encode("utf-8")
                if current == target_bytes:
                    continue
                if path.read_bytes() != current:
                    raise TransactionError(
                        "TransactionConcurrentModification"
                    )
                touched.append((path, current))
                _atomic_replace(path, target_bytes)
            for path, item, _ in rows:
                expected = (
                    item.after_content
                    if destination == "after"
                    else item.before_content
                ).encode("utf-8")
                if path.read_bytes() != expected:
                    raise TransactionError(
                        "TransactionWriteVerificationFailed"
                    )
        except Exception as exc:
            restore_errors: list[Exception] = []
            # Include the attempted write: an fsync or replace wrapper can
            # report failure after the target has already been installed.
            for path, original in reversed(touched):
                try:
                    if path.read_bytes() != original:
                        _atomic_replace(path, original)
                except Exception as restore_exc:  # pragma: no cover - defensive I/O path
                    restore_errors.append(restore_exc)
            if restore_errors:
                raise TransactionError("TransactionRollbackFailed") from restore_errors[0]
            raise TransactionError("TransactionWriteFailed") from exc
    resulting: dict[str, str] = {}
    for path, item, _ in rows:
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise TransactionError(
                "TransactionResultUnavailable"
            ) from exc
        expected = (
            item.after_sha256
            if destination == "after"
            else item.before_sha256
        )
        observed = _hash_bytes(content)
        if observed != expected:
            raise TransactionError(
                "TransactionResultMismatch"
            )
        resulting[item.path] = observed
    return TransactionResult(
        transaction_id=transaction.transaction_id,
        transaction_digest=transaction.digest,
        action=action,
        changed=changed,
        files=tuple(item.path for item in transaction.files),
        resulting_hashes=resulting,
    )


def commit(transaction: ChangeTransaction, root: str | os.PathLike[str] | None = None) -> TransactionResult:
    if not isinstance(transaction, ChangeTransaction):
        raise TransactionError("TransactionExpected")
    return _transition(transaction, root, "commit", "after")


def rollback(transaction: ChangeTransaction, root: str | os.PathLike[str] | None = None) -> TransactionResult:
    if not isinstance(transaction, ChangeTransaction):
        raise TransactionError("TransactionExpected")
    return _transition(transaction, root, "rollback", "before")


def replay(transaction: ChangeTransaction, root: str | os.PathLike[str] | None = None) -> TransactionResult:
    if not isinstance(transaction, ChangeTransaction):
        raise TransactionError("TransactionExpected")
    return _transition(transaction, root, "replay", "after")


__all__ = [
    "TRANSACTION_SCHEMA_VERSION",
    "TRANSACTION_CONTRACT",
    "TransactionError",
    "TransactionFile",
    "ChangeTransaction",
    "TransactionResult",
    "prepare_transaction",
    "save_transaction",
    "load_transaction",
    "commit",
    "rollback",
    "replay",
]
