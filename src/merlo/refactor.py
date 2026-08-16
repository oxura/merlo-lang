from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from merlo.frontend.file_syntax import FileToken, SyntaxNode, parse_file_cst
from merlo.semantic_world import SemanticWorld, StaleWorldError, UnsupportedMigration, WorldError


CHANGE_IR_SCHEMA_VERSION = 1
CHANGE_IR_CONTRACT = "merlo.change-ir.v1"


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if isinstance(value, float) and not math.isfinite(value):
        raise WorldError(
            "ChangeIRInvalidMetadata: non-finite numbers are forbidden"
        )
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


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

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise WorldError("ChangeIRInvalidEdit: path is required")
        if type(self.start) is not int or type(self.end) is not int or self.start < 0 or self.end < self.start:
            raise WorldError("ChangeIRInvalidEdit: edit span must be ordered")
        if type(self.token_ordinal) is not int or self.token_ordinal < 0:
            raise WorldError("ChangeIRInvalidEdit: token ordinal must be non-negative")

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

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RefactorEdit":
        if not isinstance(value, Mapping):
            raise WorldError("ChangeIRInvalidEdit: expected object")
        required = {"path", "start", "end", "replacement", "symbol_id", "kind", "syntax_id", "token_id", "token_ordinal"}
        if set(value) != required:
            raise WorldError("ChangeIRInvalidEdit: edit fields do not match schema")
        return cls(**{key: value[key] for key in required})


@dataclass(frozen=True)
class ChangeTarget:
    symbol_id: str
    revision_id: str
    interface_revision_id: str
    implementation_revision_id: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value for value in (self.symbol_id, self.revision_id, self.interface_revision_id, self.implementation_revision_id)):
            raise WorldError("ChangeIRInvalidTarget: all target identities are required")

    def to_dict(self) -> dict[str, str]:
        return {
            "symbol_id": self.symbol_id,
            "revision_id": self.revision_id,
            "interface_revision_id": self.interface_revision_id,
            "implementation_revision_id": self.implementation_revision_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChangeTarget":
        if not isinstance(value, Mapping) or set(value) != {"symbol_id", "revision_id", "interface_revision_id", "implementation_revision_id"}:
            raise WorldError("ChangeIRInvalidTarget: target fields do not match schema")
        return cls(**{key: value[key] for key in value})


@dataclass(frozen=True)
class ChangeDiagnostic:
    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code or not isinstance(self.message, str) or not self.message:
            raise WorldError("ChangeIRInvalidDiagnostic: code and message are required")
        if not isinstance(self.details, Mapping):
            raise WorldError("ChangeIRInvalidDiagnostic: details must be an object")
        object.__setattr__(self, "details", _freeze(self.details))

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": _thaw(self.details)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChangeDiagnostic":
        if not isinstance(value, Mapping) or set(value) != {"code", "message", "details"}:
            raise WorldError("ChangeIRInvalidDiagnostic: diagnostic fields do not match schema")
        return cls(value["code"], value["message"], value["details"])


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
    match = matches[0]
    syntax_id, token_ordinal = _syntax_identity(cst.declarations, match)
    return RefactorEdit(
        path=str(path.resolve()),
        start=match.start,
        end=match.end,
        replacement=new,
        symbol_id=symbol_id,
        kind=kind,
        syntax_id=syntax_id,
        token_id=match.token_id,
        token_ordinal=token_ordinal,
    )


def _signature_edit(
    symbol: Mapping[str, Any],
    signature: str,
) -> tuple[RefactorEdit, str]:
    path = Path(symbol["source"]["path"]).resolve()
    source = path.read_text(encoding="utf-8")
    cst = parse_file_cst(source, path=str(path))
    if cst.diagnostics:
        codes = ",".join(item.code for item in cst.diagnostics)
        raise UnsupportedMigration(
            "UnsupportedMigration: cannot change a signature in recovered "
            f"syntax in {path}: {codes}"
        )
    line = int(symbol["source"].get("line", 0))
    candidates = tuple(
        declaration
        for declaration in cst.declarations
        if any(
            token.line == line
            and token.kind == "identifier"
            and token.text == symbol["name"]
            for token in declaration.tokens
        )
    )
    if len(candidates) != 1:
        raise UnsupportedMigration(
            "UnsupportedMigration: function declaration identity is ambiguous"
        )
    declaration = candidates[0]
    significant = tuple(
        token
        for token in declaration.tokens
        if token.kind
        not in {
            "whitespace",
            "comment",
            "newline",
            "indent",
            "dedent",
            "eof",
        }
    )
    name_index = next(
        (
            index
            for index, token in enumerate(significant)
            if token.line == line
            and token.kind == "identifier"
            and token.text == symbol["name"]
        ),
        None,
    )
    if name_index is None:
        raise UnsupportedMigration(
            "UnsupportedMigration: function name token is missing"
        )
    open_index = next(
        (
            index
            for index in range(name_index + 1, len(significant))
            if significant[index].text == "("
        ),
        None,
    )
    if open_index is None:
        raise UnsupportedMigration(
            "UnsupportedMigration: inferred short-form signatures cannot yet "
            "be migrated"
        )
    depth = 0
    header_colon = None
    for token in significant[open_index:]:
        if token.text in {"(", "[", "{"}:
            depth += 1
        elif token.text in {")", "]", "}"}:
            depth -= 1
        elif token.text == ":" and depth == 0:
            header_colon = token
            break
    if header_colon is None:
        raise UnsupportedMigration(
            "UnsupportedMigration: function signature terminator is missing"
        )
    anchor = significant[open_index]
    old_signature = source[anchor.start:header_colon.start]
    if old_signature == signature:
        raise WorldError("ChangeSignatureNoOp")
    return (
        RefactorEdit(
            path=str(path),
            start=anchor.start,
            end=header_colon.start,
            replacement=signature,
            symbol_id=str(symbol["symbol_id"]),
            kind="signature",
            syntax_id=declaration.syntax_id,
            token_id=anchor.token_id,
            token_ordinal=open_index,
        ),
        old_signature,
    )


def _validate_signature(signature: str) -> None:
    if (
        type(signature) is not str
        or signature != signature.strip()
        or not signature.startswith("(")
        or "\n" in signature
        or "\r" in signature
        or not re.search(r"\)\s*->\s*[^:]+$", signature)
    ):
        raise WorldError("ChangeSignatureInvalidSyntax")
    from merlo.surface_parser import SurfaceSyntaxError, parse_surface

    try:
        parse_surface(
            f"fn __merlo_signature_probe__{signature}:\n    ?\n",
            path="<change-signature>",
        )
    except (SurfaceSyntaxError, ValueError) as exc:
        raise WorldError("ChangeSignatureInvalidSyntax") from exc


def _signature_compiles_in_isolation(
    world: SemanticWorld,
    edit: RefactorEdit,
) -> tuple[bool, str]:
    from merlo.compiler import compile_project

    try:
        relative = Path(edit.path).resolve().relative_to(world.root.resolve())
    except ValueError as exc:
        raise WorldError("ChangeIRInvalidPath: edit escapes project root") from exc
    with tempfile.TemporaryDirectory(prefix="merlo-change-signature-") as directory:
        isolated_root = Path(directory) / "project"
        shutil.copytree(world.root, isolated_root, symlinks=False)
        isolated_path = isolated_root / relative
        source = isolated_path.read_text(encoding="utf-8")
        isolated_path.write_text(
            source[:edit.start] + edit.replacement + source[edit.end:],
            encoding="utf-8",
        )
        entry = Path(world.data["entry_path"]).resolve()
        isolated_entry = isolated_root / entry.relative_to(world.root.resolve())
        try:
            compile_project(isolated_entry, require_interface_lock=False)
        except Exception as exc:
            message = (str(exc) or type(exc).__name__).replace(
                str(isolated_root),
                "<isolated>",
            )
            return False, message
    return True, ""


def _hole_payload(
    symbol: Mapping[str, Any],
    hole_id: str,
) -> Mapping[str, Any]:
    holes = symbol.get("holes")
    if not isinstance(holes, (list, tuple)):
        raise WorldError("FillHoleMalformedTarget")
    matches = tuple(
        item
        for item in holes
        if isinstance(item, Mapping)
        and item.get("hole_id") == hole_id
    )
    if len(matches) != 1:
        raise WorldError("FillHoleNotOwned")
    hole = matches[0]
    required = {
        "hole_id",
        "expected_type",
        "source",
        "node_id",
        "context",
        "callables",
        "effects",
        "capabilities",
    }
    if (
        set(hole) != required
        or type(hole["expected_type"]) is not str
        or not hole["expected_type"]
        or not isinstance(hole["source"], Mapping)
    ):
        raise WorldError("FillHoleMalformedTarget")
    return hole


def _hole_edit(
    symbol: Mapping[str, Any],
    hole: Mapping[str, Any],
    replacement: str,
) -> RefactorEdit:
    source_span = hole["source"]
    if set(source_span) != {
        "path",
        "line",
        "column",
        "end_line",
        "end_column",
    }:
        raise WorldError("FillHoleMalformedSource")
    path = Path(source_span["path"]).resolve()
    source = path.read_text(encoding="utf-8")
    cst = parse_file_cst(source, path=str(path))
    if cst.diagnostics:
        codes = ",".join(
            item.code
            for item in cst.diagnostics
        )
        raise UnsupportedMigration(
            "UnsupportedMigration: cannot fill "
            f"recovered syntax in {path}: {codes}"
        )
    offsets = _line_offsets(source)
    line = source_span["line"]
    end_line = source_span["end_line"]
    column = source_span["column"]
    end_column = source_span["end_column"]
    if (
        any(
            type(value) is not int
            for value in (
                line,
                end_line,
                column,
                end_column,
            )
        )
        or line < 1
        or end_line != line
        or column < 1
        or end_column <= column
        or line >= len(offsets)
    ):
        raise WorldError("FillHoleMalformedSource")
    start = offsets[line - 1] + column - 1
    end = offsets[end_line - 1] + end_column - 1
    matches = tuple(
        token
        for token in cst.tokens
        if token.text == "?"
        and start <= token.start
        and token.end <= end
    )
    if len(matches) != 1:
        raise UnsupportedMigration(
            "UnsupportedMigration: typed hole "
            "source identity is ambiguous"
        )
    token = matches[0]
    syntax_id, ordinal = _syntax_identity(
        cst.declarations,
        token,
    )
    return RefactorEdit(
        path=str(path),
        start=token.start,
        end=token.end,
        replacement=replacement,
        symbol_id=str(symbol["symbol_id"]),
        kind="hole",
        syntax_id=syntax_id,
        token_id=token.token_id,
        token_ordinal=ordinal,
    )


def _validate_hole_replacement(
    replacement: str,
    expected_type: str,
) -> None:
    from merlo.surface_parser import (
        SurfaceSyntaxError,
        parse_surface,
    )

    source = (
        "fn __merlo_fill__() -> "
        f"{expected_type}:\n"
        f"    {replacement}\n"
    )
    try:
        parse_surface(
            source,
            path="<fill-hole>",
        )
    except (SurfaceSyntaxError, ValueError) as exc:
        raise WorldError(
            "FillHoleInvalidReplacement"
        ) from exc


def preview_fill_hole(
    world: SemanticWorld,
    target: str,
    hole_id: str,
    replacement: str,
) -> "ChangeIR":
    if not isinstance(world, SemanticWorld):
        raise WorldError("FillHoleWorldRequired")
    world.require_fresh()
    if (
        type(target) is not str
        or not target
        or type(hole_id) is not str
        or not hole_id
        or type(replacement) is not str
        or not replacement.strip()
        or replacement != replacement.strip()
        or "\n" in replacement
        or "\r" in replacement
        or replacement == "?"
    ):
        raise WorldError("FillHoleInvalidArguments")
    symbol = world.resolve(target)
    hole = _hole_payload(symbol, hole_id)
    _validate_hole_replacement(
        replacement,
        hole["expected_type"],
    )
    edit = _hole_edit(
        symbol,
        hole,
        replacement,
    )
    return ChangeIR(
        operation="fill_hole",
        status="ready",
        target=_target(symbol),
        expected_world_digest=world.digest,
        edits=(edit,),
        metadata={
            "hole_id": hole_id,
            "expected_type": hole[
                "expected_type"
            ],
            "replacement": replacement,
        },
        world=world,
    )


def _target(symbol: Mapping[str, Any]) -> ChangeTarget:
    return ChangeTarget(
        symbol_id=str(symbol["symbol_id"]),
        revision_id=str(symbol["revision_id"]),
        interface_revision_id=str(symbol["interface_revision_id"]),
        implementation_revision_id=str(symbol["implementation_revision_id"]),
    )


def preview_rename(world: SemanticWorld, target: str, new_name: str) -> "ChangeIR":
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", new_name):
        raise WorldError("InvalidSymbolName: rename requires an identifier")
    symbol = world.resolve(target)
    edits: list[RefactorEdit] = []
    definition_path = Path(symbol["source"]["path"]).resolve()
    edits.append(_identifier_edit(definition_path, symbol["source"], symbol["name"], new_name, symbol["symbol_id"], "definition"))
    for reference in world.references(symbol["symbol_id"]):
        path = Path(reference["source"]["path"]).resolve()
        edits.append(_identifier_edit(path, reference["source"], symbol["name"], new_name, symbol["symbol_id"], "reference"))
    unique: dict[tuple[str, int, int], RefactorEdit] = {}
    for edit in edits:
        unique[(edit.path, edit.start, edit.end)] = edit
    ordered = tuple(sorted(unique.values(), key=lambda item: (item.path, item.start, item.end)))
    return ChangeIR(
        operation="rename",
        status="ready",
        target=_target(symbol),
        expected_world_digest=world.digest,
        edits=ordered,
        metadata={"old_name": symbol["name"], "new_name": new_name},
        world=world,
    )


@dataclass(frozen=True)
class ChangeIR:
    operation: str
    status: str
    target: ChangeTarget
    expected_world_digest: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    edits: tuple[RefactorEdit, ...] = ()
    diagnostic: ChangeDiagnostic | None = None
    schema_version: int = CHANGE_IR_SCHEMA_VERSION
    contract: str = CHANGE_IR_CONTRACT
    digest: str = ""
    world: SemanticWorld | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != CHANGE_IR_SCHEMA_VERSION or self.contract != CHANGE_IR_CONTRACT:
            raise WorldError("ChangeIRVersionMismatch")
        if not isinstance(self.operation, str) or not isinstance(self.status, str) or self.status not in {"ready", "unsupported"}:
            raise WorldError("ChangeIRInvalidStatus")
        allowed_statuses = {
            "rename": {"ready"},
            "move": {"unsupported"},
            "change_signature": {"ready", "unsupported"},
            "fill_hole": {"ready"},
        }.get(self.operation)
        if allowed_statuses is None or self.status not in allowed_statuses:
            raise WorldError("ChangeIRInvalidOperation")
        if not isinstance(self.expected_world_digest, str) or not self.operation or not self.expected_world_digest:
            raise WorldError("ChangeIRInvalidEnvelope")
        if not isinstance(self.target, ChangeTarget):
            object.__setattr__(self, "target", ChangeTarget.from_dict(self.target))
        if not isinstance(self.metadata, Mapping):
            raise WorldError("ChangeIRInvalidEnvelope: metadata must be an object")
        object.__setattr__(self, "metadata", _freeze(self.metadata))
        object.__setattr__(self, "edits", tuple(item if isinstance(item, RefactorEdit) else RefactorEdit.from_dict(item) for item in self.edits))
        if self.diagnostic is not None and not isinstance(
            self.diagnostic,
            ChangeDiagnostic,
        ):
            object.__setattr__(
                self,
                "diagnostic",
                ChangeDiagnostic.from_dict(self.diagnostic),
            )
        if self.world is not None:
            root = Path(self.world.root).resolve()
            for edit in self.edits:
                try:
                    Path(edit.path).resolve().relative_to(root)
                except ValueError as exc:
                    raise WorldError("ChangeIRInvalidPath: edit escapes project root") from exc
        if self.status == "ready" and self.diagnostic is not None:
            raise WorldError("ChangeIRInvalidEnvelope: ready change cannot carry a diagnostic")
        if self.status == "unsupported" and self.edits:
            raise WorldError("ChangeIRInvalidEnvelope: unsupported change cannot carry edits")
        if self.status == "ready" and not self.edits:
            raise WorldError(
                "ChangeIRInvalidEnvelope: ready change requires edits"
            )
        if self.status == "unsupported" and self.diagnostic is None:
            raise WorldError(
                "ChangeIRInvalidEnvelope: unsupported change requires "
                "a diagnostic"
            )
        self._validate_edits()
        expected = _digest(self._payload())
        if self.digest and self.digest != expected:
            raise WorldError("ChangeIRDigestMismatch")
        object.__setattr__(self, "digest", expected)

    def _validate_edits(self) -> None:
        previous: RefactorEdit | None = None
        for item in self.edits:
            if item.symbol_id != self.target.symbol_id:
                raise WorldError("ChangeIRInvalidEdits: edit target does not match envelope target")
            if not item.kind or not item.syntax_id or not item.token_id or not isinstance(item.replacement, str):
                raise WorldError("ChangeIRInvalidEdits: edit identity fields are required")
            path = Path(item.path)
            if not path.is_absolute() or str(path) != str(path.resolve()):
                raise WorldError("ChangeIRInvalidPath: edit paths must be normalized absolute paths")
            if previous is not None:
                if (item.path, item.start, item.end) <= (previous.path, previous.start, previous.end):
                    raise WorldError("ChangeIRInvalidEdits: edits must be sorted and unique")
                if item.path == previous.path and item.start < previous.end:
                    raise WorldError("ChangeIRInvalidEdits: overlapping edits are forbidden")
            previous = item

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract": self.contract,
            "operation": self.operation,
            "status": self.status,
            "target": self.target.to_dict(),
            "expected_world_digest": self.expected_world_digest,
            "metadata": _thaw(self.metadata),
            "edits": [item.to_dict() for item in self.edits],
            "diagnostic": self.diagnostic.to_dict() if self.diagnostic is not None else None,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "digest": self.digest}

    def to_json(self) -> str:
        return _json(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, world: SemanticWorld | None = None) -> "ChangeIR":
        if not isinstance(value, Mapping):
            raise WorldError("ChangeIRSchemaMismatch")
        required = {"schema_version", "contract", "operation", "status", "target", "expected_world_digest", "metadata", "edits", "diagnostic", "digest"}
        if (
            set(value) != required
            or not isinstance(value.get("target"), Mapping)
            or not isinstance(value.get("metadata"), Mapping)
            or not isinstance(value.get("edits"), list)
            or (
                value.get("diagnostic") is not None
                and not isinstance(
                    value.get("diagnostic"),
                    Mapping,
                )
            )
            or not isinstance(value.get("digest"), str)
        ):
            raise WorldError("ChangeIRSchemaMismatch")
        if value.get("schema_version") != CHANGE_IR_SCHEMA_VERSION or value.get("contract") != CHANGE_IR_CONTRACT:
            raise WorldError("ChangeIRVersionMismatch")
        payload = {key: value[key] for key in required if key != "digest"}
        try:
            expected_digest = _digest(payload)
        except (TypeError, ValueError) as exc:
            raise WorldError(
                "ChangeIRSchemaMismatch"
            ) from exc
        if value.get("digest") != expected_digest:
            raise WorldError("ChangeIRDigestMismatch")
        return cls(
            operation=value["operation"],
            status=value["status"],
            target=ChangeTarget.from_dict(value["target"]),
            expected_world_digest=value["expected_world_digest"],
            metadata=value["metadata"],
            edits=tuple(RefactorEdit.from_dict(item) for item in value["edits"]),
            diagnostic=ChangeDiagnostic.from_dict(value["diagnostic"]) if value["diagnostic"] is not None else None,
            schema_version=value["schema_version"],
            contract=value["contract"],
            digest=value["digest"],
            world=world,
        )

    @classmethod
    def from_json(cls, value: str, *, world: SemanticWorld | None = None) -> "ChangeIR":
        try:
            payload = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise WorldError("ChangeIRSchemaMismatch") from exc
        return cls.from_dict(payload, world=world)

    def _source_key(self, path: Path, world: SemanticWorld) -> str:
        try:
            return str(path.relative_to(world.root))
        except ValueError:
            return str(path)

    def _validate_for_apply(self, world: SemanticWorld) -> None:
        if self.status != "ready":
            message = self.diagnostic.message if self.diagnostic is not None else "change is not ready"
            raise UnsupportedMigration(f"UnsupportedMigration: {message}")
        if world.digest != self.expected_world_digest:
            raise StaleWorldError("StaleWorld: refactor preview belongs to another world")
        world.require_fresh()
        current = world.resolve(self.target.symbol_id)
        for key in ("symbol_id", "revision_id", "interface_revision_id", "implementation_revision_id"):
            if str(current.get(key, "")) != str(getattr(self.target, key)):
                raise StaleWorldError(f"StaleWorld: target identity changed ({key})")
        if self.operation == "rename":
            old_name = self.metadata.get("old_name")
            new_name = self.metadata.get("new_name")
            if (
                old_name != current.get("name")
                or not isinstance(new_name, str)
                or not re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_]*",
                    new_name,
                )
            ):
                raise WorldError(
                    "ChangeIRInvalidRenameMetadata"
                )
            semantic_edits = [
                _identifier_edit(
                    Path(current["source"]["path"]).resolve(),
                    current["source"],
                    old_name,
                    new_name,
                    current["symbol_id"],
                    "definition",
                )
            ]
            for reference in world.references(current["symbol_id"]):
                semantic_edits.append(
                    _identifier_edit(
                        Path(reference["source"]["path"]).resolve(),
                        reference["source"],
                        old_name,
                        new_name,
                        current["symbol_id"],
                        "reference",
                    )
                )
            expected_edits = tuple(
                sorted(
                    {
                        (item.path, item.start, item.end): item
                        for item in semantic_edits
                    }.values(),
                    key=lambda item: (
                        item.path,
                        item.start,
                        item.end,
                    ),
                )
            )
            if self.edits != expected_edits:
                raise WorldError(
                    "ChangeIRSemanticEditMismatch"
                )
        elif self.operation == "change_signature":
            if set(self.metadata) != {
                "old_signature",
                "signature",
            }:
                raise WorldError(
                    "ChangeIRInvalidChangeSignatureMetadata"
                )
            signature = self.metadata["signature"]
            _validate_signature(signature)
            expected_edit, old_signature = _signature_edit(
                current,
                signature,
            )
            if (
                self.metadata["old_signature"] != old_signature
                or self.edits != (expected_edit,)
            ):
                raise WorldError("ChangeIRSemanticEditMismatch")
            compatible, diagnostic = _signature_compiles_in_isolation(
                world,
                expected_edit,
            )
            if not compatible:
                raise UnsupportedMigration(
                    "UnsupportedMigration: change-signature no longer "
                    "type-checks with its body and callers: " + diagnostic
                )
        elif self.operation == "fill_hole":
            if set(self.metadata) != {
                "hole_id",
                "expected_type",
                "replacement",
            }:
                raise WorldError(
                    "ChangeIRInvalidFillHoleMetadata"
                )
            hole_id = self.metadata["hole_id"]
            replacement = self.metadata[
                "replacement"
            ]
            if (
                type(hole_id) is not str
                or not hole_id
                or type(replacement) is not str
                or not replacement.strip()
                or replacement != replacement.strip()
                or "\n" in replacement
                or "\r" in replacement
                or replacement == "?"
            ):
                raise WorldError(
                    "ChangeIRInvalidFillHoleMetadata"
                )
            hole = _hole_payload(current, hole_id)
            if (
                self.metadata["expected_type"]
                != hole["expected_type"]
                or self.edits
                != (
                    _hole_edit(
                        current,
                        hole,
                        replacement,
                    ),
                )
            ):
                raise WorldError(
                    "ChangeIRSemanticEditMismatch"
                )
        root = world.root.resolve()
        source_hashes = world.data.get("source_hashes", {})
        for edit in self.edits:
            path = Path(edit.path).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise WorldError("ChangeIRInvalidPath: edit escapes project root") from exc
            key = self._source_key(path, world)
            if key not in source_hashes and str(path) not in source_hashes:
                raise StaleWorldError(f"StaleWorld: edit source is not in the expected world ({path})")
            if not path.is_file():
                raise StaleWorldError(f"StaleWorld: missing source {path}")

    def apply(
        self,
        world: SemanticWorld | None = None,
    ) -> dict[str, Any]:
        from merlo.transaction import (
            prepare_transaction,
        )

        active_world = world or self.world
        if active_world is None:
            raise WorldError(
                "ChangeIRApplyRequiresWorld"
            )
        self._validate_for_apply(active_world)
        originals: dict[str, bytes] = {}
        updated: dict[str, bytes] = {}
        by_path: dict[str, list[RefactorEdit]] = {}
        for edit in self.edits:
            by_path.setdefault(
                edit.path,
                [],
            ).append(edit)
        for path_name, edits in by_path.items():
            path = Path(path_name)
            original = path.read_bytes()
            originals[path_name] = original
            text = original.decode("utf-8")
            cst = parse_file_cst(
                text,
                path=path_name,
            )
            syntax = {
                item.syntax_id: item
                for item in cst.declarations
            }
            for edit in sorted(
                edits,
                key=lambda item: item.start,
                reverse=True,
            ):
                declaration = syntax.get(
                    edit.syntax_id
                )
                significant = (
                    tuple(
                        item
                        for item in declaration.tokens
                        if item.kind
                        not in {
                            "whitespace",
                            "comment",
                            "newline",
                            "indent",
                            "dedent",
                            "eof",
                        }
                    )
                    if declaration is not None
                    else ()
                )
                token = (
                    significant[edit.token_ordinal]
                    if edit.token_ordinal
                    < len(significant)
                    else None
                )
                if (
                    token is None
                    or token.token_id != edit.token_id
                    or token.start != edit.start
                    or (
                        edit.kind != "signature"
                        and token.end != edit.end
                    )
                ):
                    raise UnsupportedMigration(
                        "UnsupportedMigration: "
                        "syntax identity changed at "
                        f"{path}:{edit.start}"
                    )
                expected_source = (
                    self.metadata["old_name"]
                    if self.operation == "rename"
                    else self.metadata["old_signature"]
                    if self.operation == "change_signature"
                    else "?"
                    if self.operation == "fill_hole"
                    else text[
                        edit.start:edit.end
                    ]
                )
                if (
                    text[edit.start:edit.end]
                    != expected_source
                ):
                    raise UnsupportedMigration(
                        "UnsupportedMigration: "
                        "source changed at "
                        f"{path}:{edit.start}"
                    )
                text = (
                    text[:edit.start]
                    + edit.replacement
                    + text[edit.end:]
                )
            updated[path_name] = text.encode("utf-8")
        transaction = prepare_transaction(
            self,
            active_world.root,
            originals,
            updated,
        )
        transaction_result = transaction.commit()
        return {
            "committed": True,
            "operation": self.operation,
            "target_id": self.target.symbol_id,
            "files": sorted(updated),
            "edits": [
                item.to_dict()
                for item in self.edits
            ],
            "transaction": (
                transaction_result.to_dict()
            ),
        }


def _unsupported(world: SemanticWorld, target: str, operation: str, message: str, metadata: Mapping[str, Any]) -> ChangeIR:
    symbol = world.resolve(target)
    return ChangeIR(
        operation=operation,
        status="unsupported",
        target=_target(symbol),
        expected_world_digest=world.digest,
        metadata=metadata,
        diagnostic=ChangeDiagnostic("UnsupportedMigration", message),
        world=world,
    )


def preview_move(world: SemanticWorld, target: str, module: str) -> ChangeIR:
    return _unsupported(
        world,
        target,
        "move",
        "Move requires import, visibility, and module declaration migration not available in alpha.",
        {"module": module},
    )


def preview_change_signature(world: SemanticWorld, target: str, signature: str) -> ChangeIR:
    if not isinstance(world, SemanticWorld):
        raise WorldError("ChangeSignatureWorldRequired")
    world.require_fresh()
    _validate_signature(signature)
    symbol = world.resolve(target)
    if symbol["kind"] not in {"fn", "task"}:
        return _unsupported(
            world,
            target,
            "change_signature",
            "Change-signature is limited to functions and tasks.",
            {"signature": signature},
        )
    edit, old_signature = _signature_edit(symbol, signature)
    compatible, diagnostic = _signature_compiles_in_isolation(world, edit)
    if not compatible:
        return _unsupported(
            world,
            target,
            "change_signature",
            "Change-signature requires a caller/body migration: " + diagnostic,
            {"signature": signature},
        )
    return ChangeIR(
        operation="change_signature",
        status="ready",
        target=_target(symbol),
        expected_world_digest=world.digest,
        metadata={
            "old_signature": old_signature,
            "signature": signature,
        },
        edits=(edit,),
        world=world,
    )


__all__ = [
    "preview_fill_hole",
    "CHANGE_IR_CONTRACT",
    "CHANGE_IR_SCHEMA_VERSION",
    "ChangeDiagnostic",
    "ChangeIR",
    "ChangeTarget",
    "RefactorEdit",
    "preview_change_signature",
    "preview_move",
    "preview_rename",
]
