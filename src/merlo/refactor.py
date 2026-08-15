from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from merlo.frontend.file_syntax import FileToken, SyntaxNode, parse_file_cst
from merlo.semantic_world import SemanticWorld, StaleWorldError, UnsupportedMigration, WorldError


@dataclass(frozen=True)
class RefactorEdit:
    path: str
    start: int
    end: int
    replacement: str
    symbol_id: str
    kind: str
    syntax_id: str
    token_id: str
    token_ordinal: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "start": self.start,
            "end": self.end,
            "replacement": self.replacement,
            "symbol_id": self.symbol_id,
            "kind": self.kind,
            "syntax_id": self.syntax_id,
            "token_id": self.token_id,
            "token_ordinal": self.token_ordinal,
        }


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _syntax_identity(
    declarations: tuple[SyntaxNode, ...],
    token: FileToken,
) -> tuple[str, int]:
    for declaration in declarations:
        if declaration.start <= token.start and token.end <= declaration.end:
            significant = tuple(
                item
                for item in declaration.tokens
                if item.kind not in {"whitespace", "comment", "newline", "indent", "dedent", "eof"}
            )
            for ordinal, item in enumerate(significant):
                if item.token_id == token.token_id:
                    return declaration.syntax_id, ordinal
    raise UnsupportedMigration(
        f"UnsupportedMigration: token {token.token_id} has no stable syntax owner"
    )


def _identifier_edit(path: Path, span: dict[str, Any], old: str, new: str, symbol_id: str, kind: str) -> RefactorEdit:
    source = path.read_text(encoding="utf-8")
    cst = parse_file_cst(source, path=str(path))
    if cst.diagnostics:
        codes = ",".join(item.code for item in cst.diagnostics)
        raise UnsupportedMigration(
            f"UnsupportedMigration: cannot refactor recovered syntax in {path}: {codes}"
        )
    offsets = _line_offsets(source)
    line = int(span.get("line", 1))
    start_line = max(0, line - 1)
    raw_column = int(span.get("column", 0))
    column = raw_column - 1 if raw_column > 0 else 0
    start = offsets[start_line] + column
    end_line = int(span.get("end_line", line))
    raw_end_column = int(span.get("end_column", 0))
    end_column = raw_end_column - 1 if raw_end_column > 0 else 0
    end_line_index = max(0, end_line - 1)
    end = offsets[min(end_line_index, len(offsets) - 1)] + end_column
    end = max(start, min(len(source), end))
    if kind == "definition":
        start = offsets[start_line]
        end = offsets[min(start_line + 1, len(offsets) - 1)]
    matches = [
        token
        for token in cst.tokens
        if token.kind == "identifier"
        and token.text == old
        and start <= token.start
        and token.end <= end
    ]
    if not matches:
        raise UnsupportedMigration(
            f"UnsupportedMigration: semantic span does not contain exact {kind} for {symbol_id}"
        )
    if kind == "definition" and len(matches) != 1:
        raise UnsupportedMigration(
            f"UnsupportedMigration: declaration header is ambiguous for {symbol_id}"
        )
    # Call spans start at the callee. For a nested call such as f(f(x)), the
    # outer reference is therefore the first exact identifier in its span; the
    # inner call has its own narrower span. Never fall back to another location
    # in the file, because that can silently edit a different symbol.
    match = matches[0]
    syntax_id, token_ordinal = _syntax_identity(cst.declarations, match)
    return RefactorEdit(
        path=str(path),
        start=match.start,
        end=match.end,
        replacement=new,
        symbol_id=symbol_id,
        kind=kind,
        syntax_id=syntax_id,
        token_id=match.token_id,
        token_ordinal=token_ordinal,
    )


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
                cst = parse_file_cst(text, path=path_name)
                syntax = {item.syntax_id: item for item in cst.declarations}
                for edit in sorted(edits, key=lambda item: item.start, reverse=True):
                    declaration = syntax.get(edit.syntax_id)
                    significant = tuple(
                        item
                        for item in declaration.tokens
                        if item.kind not in {"whitespace", "comment", "newline", "indent", "dedent", "eof"}
                    ) if declaration is not None else ()
                    token = (
                        significant[edit.token_ordinal]
                        if edit.token_ordinal < len(significant)
                        else None
                    )
                    if (
                        token is None
                        or token.token_id != edit.token_id
                        or token.start != edit.start
                        or token.end != edit.end
                    ):
                        raise UnsupportedMigration(
                            f"UnsupportedMigration: syntax identity changed at {path}:{edit.start}"
                        )
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
