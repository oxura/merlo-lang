from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import merlo.transaction as txmod
from merlo.refactor import (
    ChangeIR,
    ChangeTarget,
    RefactorEdit,
)
from merlo.transaction import (
    ChangeTransaction,
    TransactionError,
    TransactionFile,
    commit,
    load_transaction,
    prepare_transaction,
    replay,
    rollback,
    save_transaction,
)


def _change(root: Path) -> ChangeIR:
    target = ChangeTarget(
        "target",
        "revision",
        "interface",
        "implementation",
    )
    edits = tuple(
        RefactorEdit(
            path=str((root / name).resolve()),
            start=0,
            end=len(name),
            replacement=name.upper(),
            symbol_id=target.symbol_id,
            kind="definition",
            syntax_id=f"syntax-{name}",
            token_id=f"token-{name}",
            token_ordinal=0,
        )
        for name in ("one.mlo", "two.mlo")
    )
    return ChangeIR(
        operation="rename",
        status="ready",
        target=target,
        expected_world_digest=hashlib.sha256(
            b"world"
        ).hexdigest(),
        metadata={
            "old_name": "one",
            "new_name": "ONE",
        },
        edits=edits,
    )


def _snapshots(root: Path) -> tuple[dict[Path, str], dict[Path, str]]:
    before = {root / "one.mlo": "one\n", root / "two.mlo": "two\n"}
    after = {root / "one.mlo": "ONE\n", root / "two.mlo": "TWO\n"}
    return before, after


def _transaction(tmp_path: Path) -> tuple[ChangeTransaction, dict[Path, str], dict[Path, str]]:
    before, after = _snapshots(tmp_path)
    for path, content in before.items():
        path.write_text(content, encoding="utf-8")
    return prepare_transaction(
        _change(tmp_path),
        tmp_path,
        before,
        after,
    ), before, after


def test_manifest_is_frozen_digest_bound_and_roundtrips(tmp_path: Path) -> None:
    transaction, _, _ = _transaction(tmp_path)
    assert transaction.contract == "merlo.change-transaction.v1"
    assert transaction.world_digest == (
        _change(tmp_path).expected_world_digest
    )
    assert transaction.digest
    assert transaction.transaction_id
    assert transaction.to_json() == ChangeTransaction.from_json(transaction.to_json()).to_json()
    assert transaction.files == tuple(sorted(transaction.files, key=lambda item: item.path))
    with pytest.raises(Exception):
        transaction.files = ()  # type: ignore[misc]


def test_save_load_is_deterministic_and_atomic(tmp_path: Path) -> None:
    transaction, _, _ = _transaction(tmp_path)
    journal = save_transaction(transaction)
    assert journal == tmp_path / ".merlo" / "transactions" / f"{transaction.transaction_id}.json"
    assert load_transaction(tmp_path, transaction.transaction_id).to_json() == transaction.to_json()
    assert journal.read_text(encoding="utf-8").endswith("\n")


def test_commit_rollback_replay_and_idempotency(tmp_path: Path) -> None:
    transaction, before, after = _transaction(tmp_path)
    committed = commit(transaction)
    assert committed.action == "commit"
    assert committed.changed is True
    assert committed.files == ("one.mlo", "two.mlo")
    assert all(path.read_text(encoding="utf-8") == after[path] for path in after)
    assert commit(transaction).changed is False
    rolled_back = rollback(transaction)
    assert rolled_back.action == "rollback"
    assert rolled_back.changed is True
    assert all(path.read_text(encoding="utf-8") == before[path] for path in before)
    assert rollback(transaction).changed is False
    assert replay(transaction).action == "replay"
    assert replay(transaction).changed is False


def test_journal_exists_before_first_source_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transaction, _, _ = _transaction(tmp_path)
    observed: list[bool] = []
    original = txmod._atomic_replace

    def check_journal(path: Path, content: bytes) -> None:
        observed.append(transaction.journal_path.exists())
        original(path, content)

    monkeypatch.setattr(txmod, "_atomic_replace", check_journal)
    commit(transaction)
    assert observed == [True, True]


def test_mixed_or_stale_sources_reject_without_partial_edit(tmp_path: Path) -> None:
    transaction, before, after = _transaction(tmp_path)
    (tmp_path / "one.mlo").write_text(after[tmp_path / "one.mlo"], encoding="utf-8")
    with pytest.raises(TransactionError, match="MixedState"):
        commit(transaction)
    assert (tmp_path / "two.mlo").read_text(encoding="utf-8") == before[tmp_path / "two.mlo"]
    (tmp_path / "one.mlo").write_text("stale\n", encoding="utf-8")
    with pytest.raises(TransactionError, match="StaleSource"):
        commit(transaction)
    assert (tmp_path / "two.mlo").read_text(encoding="utf-8") == before[tmp_path / "two.mlo"]


def test_rollback_and_replay_recover_mixed_crash_states(
    tmp_path: Path,
) -> None:
    transaction, before, after = _transaction(
        tmp_path
    )
    first = tmp_path / "one.mlo"
    first.write_text(
        after[first],
        encoding="utf-8",
    )

    recovered = rollback(transaction)
    assert recovered.changed is True
    assert all(
        path.read_text(encoding="utf-8")
        == content
        for path, content in before.items()
    )

    first.write_text(
        after[first],
        encoding="utf-8",
    )
    replayed = replay(transaction)
    assert replayed.changed is True
    assert all(
        path.read_text(encoding="utf-8")
        == content
        for path, content in after.items()
    )


def test_path_escape_and_symlink_escape_are_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    with pytest.raises(TransactionError):
        prepare_transaction(
            _change(tmp_path),
            tmp_path,
            {"../escape": "x"},
            {"../escape": "y"},
        )
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(TransactionError, match="PathEscape"):
        prepare_transaction(
            _change(tmp_path),
            tmp_path,
            {link / "x": "x"},
            {link / "x": "y"},
        )


def test_tampered_schema_digest_hash_and_content_reject(tmp_path: Path) -> None:
    transaction, _, _ = _transaction(tmp_path)
    raw = transaction.to_dict()
    raw["extra"] = True
    with pytest.raises(TransactionError, match="Schema"):
        ChangeTransaction.from_dict(raw)
    raw = transaction.to_dict()
    raw["digest"] = "0" * 64
    with pytest.raises(TransactionError, match="Digest"):
        ChangeTransaction.from_dict(raw)
    file_raw = transaction.files[0].to_dict()
    file_raw["before_sha256"] = "0" * 64
    with pytest.raises(TransactionError, match="BeforeHash"):
        TransactionFile.from_dict(file_raw)
    raw = transaction.to_dict()
    raw["files"][0]["after_content"] = "tampered\n"
    raw["digest"] = hashlib.sha256(
        json.dumps({key: raw[key] for key in raw if key not in {"digest", "transaction_id"}}, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    raw["transaction_id"] = hashlib.sha256((raw["digest"] + raw["change_digest"]).encode("ascii")).hexdigest()
    with pytest.raises(TransactionError, match="AfterHash"):
        ChangeTransaction.from_dict(raw)


def test_second_write_failure_restores_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transaction, before, _ = _transaction(tmp_path)
    original = txmod._atomic_replace
    calls = 0

    def fail_second(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second write failure")
        original(path, content)


    monkeypatch.setattr(txmod, "_atomic_replace", fail_second)
    with pytest.raises(TransactionError, match="WriteFailed"):
        commit(transaction)
    assert all(path.read_text(encoding="utf-8") == content for path, content in before.items())

def test_silent_write_failure_is_verified_and_restored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transaction, before, _ = _transaction(tmp_path)

    monkeypatch.setattr(
        txmod,
        "_atomic_replace",
        lambda path, content: None,
    )
    with pytest.raises(
        TransactionError,
        match="WriteFailed",
    ):
        commit(transaction)
    assert all(
        path.read_text(encoding="utf-8")
        == content
        for path, content in before.items()
    )


def test_journal_symlink_escape_is_rejected(
    tmp_path: Path,
) -> None:
    transaction, before, _ = _transaction(tmp_path)
    outside = tmp_path.parent / (
        tmp_path.name + "-journal-outside"
    )
    outside.mkdir()
    (tmp_path / ".merlo").symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(
        TransactionError,
        match="JournalPathEscape",
    ):
        commit(transaction)
    assert all(
        path.read_text(encoding="utf-8")
        == content
        for path, content in before.items()
    )
