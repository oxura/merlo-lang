from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .semantic_world import SemanticWorld, StaleWorldError, UnsupportedMigration, WorldError


@dataclass(frozen=True)
class RefactorEdit:
    path: str
    start: int
    end: int
    replacement: str
    symbol_id: str
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "start": self.start, "end": self.end, "replacement": self.replacement, "symbol_id": self.symbol_id, "kind": self.kind}


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _identifier_edit(path: Path, span: dict[str, Any], old: str, new: str, symbol_id: str, kind: str) -> RefactorEdit:
    source = path.read_text(encoding="utf-8")
    offsets = _line_offsets(source)
    line = int(span.get("line", 1))
    start_line = max(0, line - 1)
    column = int(span.get("column", 0))
    start = offsets[start_line] + column
    end_line = int(span.get("end_line", line))
    end = offsets[min(end_line, len(offsets) - 1)] + int(span.get("end_column", 0))
    end = max(start, min(len(source), end))
    segment = source[start:end]
    matches = list(re.finditer(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", segment))
    if len(matches) != 1:
        # A call span can start at a receiver or include a nested expression; narrow
        # only to the exact identifier and reject if the semantic span is ambiguous.
        matches = list(
            re.finditer(
                rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])",
                source,
            )
        )
        if kind == "reference" and matches:
            match = matches[-1]
            return RefactorEdit(
                path=str(path),
                start=match.start(),
                end=match.end(),
                replacement=new,
                symbol_id=symbol_id,
                kind=kind,
            )
        if kind == "definition" and matches:
            match = matches[0]
            return RefactorEdit(
                path=str(path),
                start=match.start(),
                end=match.end(),
                replacement=new,
                symbol_id=symbol_id,
                kind=kind,
            )
        if len(matches) != 1:
            raise UnsupportedMigration(f"UnsupportedMigration: cannot locate exact {kind} for {symbol_id}")
        match = matches[0]
        return RefactorEdit(path=str(path), start=offsets[start_line] + match.start(), end=offsets[start_line] + match.end(), replacement=new, symbol_id=symbol_id, kind=kind)
    match = matches[0]
    return RefactorEdit(path=str(path), start=start + match.start(), end=start + match.end(), replacement=new, symbol_id=symbol_id, kind=kind)


def preview_rename(world: SemanticWorld, target: str, new_name: str) -> "RefactorTransaction":
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", new_name):
        raise WorldError("InvalidSymbolName: rename requires an identifier")
    symbol = world.resolve(target)
    if not symbol["exported"] and symbol["module"] != Path(world.data["entry_path"]).stem:
        # Private symbols are still safe to rename within their defining module;
        # callers outside that module would already be non-exact and are rejected.
        pass
    edits: list[RefactorEdit] = []
    definition_path = Path(symbol["source"]["path"])
    edits.append(_identifier_edit(definition_path, symbol["source"], symbol["name"], new_name, symbol["symbol_id"], "definition"))
    for reference in world.references(symbol["symbol_id"]):
        path = Path(reference["source"]["path"])
        edits.append(_identifier_edit(path, reference["source"], symbol["name"], new_name, symbol["symbol_id"], "reference"))
    unique: dict[tuple[str, int, int], RefactorEdit] = {}
    for edit in edits:
        unique[(edit.path, edit.start, edit.end)] = edit
    ordered = tuple(sorted(unique.values(), key=lambda item: (item.path, item.start, item.end)))
    return RefactorTransaction(world=world, operation="rename", target_id=symbol["symbol_id"], expected_digest=world.digest, edits=ordered, metadata={"old_name": symbol["name"], "new_name": new_name})


@dataclass
class RefactorTransaction:
    world: SemanticWorld
    operation: str
    target_id: str
    expected_digest: str
    edits: tuple[RefactorEdit, ...]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"operation": self.operation, "target_id": self.target_id, "expected_world": self.expected_digest, "edits": [item.to_dict() for item in self.edits], **self.metadata}

    def apply(self) -> dict[str, Any]:
        if self.world.digest != self.expected_digest:
            raise StaleWorldError("StaleWorld: refactor preview belongs to another world")
        self.world.require_fresh()
        originals: dict[str, bytes] = {}
        updated: dict[str, bytes] = {}
        try:
            by_path: dict[str, list[RefactorEdit]] = {}
            for edit in self.edits:
                by_path.setdefault(edit.path, []).append(edit)
            for path_name, edits in by_path.items():
                path = Path(path_name)
                original = path.read_bytes()
                originals[path_name] = original
                text = original.decode("utf-8")
                for edit in sorted(edits, key=lambda item: item.start, reverse=True):
                    if text[edit.start:edit.end] != self.metadata.get("old_name", text[edit.start:edit.end]):
                        raise UnsupportedMigration(f"UnsupportedMigration: source changed at {path}:{edit.start}")
                    text = text[:edit.start] + edit.replacement + text[edit.end:]
                updated[path_name] = text.encode("utf-8")
            for path_name, content in updated.items():
                path = Path(path_name)
                temporary: Path | None = None
                try:
                    with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as handle:
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                        temporary = Path(handle.name)
                    os.replace(temporary, path)
                finally:
                    if temporary is not None and temporary.exists():
                        temporary.unlink()
        except Exception:
            for path_name, content in originals.items():
                Path(path_name).write_bytes(content)
            raise
        return {"committed": True, "operation": self.operation, "target_id": self.target_id, "files": sorted(updated), "edits": [item.to_dict() for item in self.edits]}


def preview_move(world: SemanticWorld, target: str, module: str) -> dict[str, Any]:
    world.resolve(target)
    return {"operation": "move", "diagnostic": {"code": "UnsupportedMigration", "message": "Move requires import, visibility, and module declaration migration not available in alpha."}, "target": target, "module": module}


def preview_change_signature(world: SemanticWorld, target: str, signature: str) -> dict[str, Any]:
    symbol = world.resolve(target)
    if world.callers(symbol["symbol_id"]):
        return {"operation": "change_signature", "diagnostic": {"code": "UnsupportedMigration", "message": "Change-signature requires typed argument migration for every caller."}, "target": symbol["symbol_id"], "signature": signature}
    return {"operation": "change_signature", "diagnostic": {"code": "UnsupportedMigration", "message": "Change-signature migration is unsupported for this source form."}, "target": symbol["symbol_id"], "signature": signature}


__all__ = ["RefactorEdit", "RefactorTransaction", "preview_change_signature", "preview_move", "preview_rename"]
