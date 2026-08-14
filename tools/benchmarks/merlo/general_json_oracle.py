"""Independent Python JSON oracle preserving order, duplicates, and number lexemes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
MASK_U64 = (1 << 64) - 1


class NumberLexeme(str):
    pass


class ObjectPairs(list[tuple[str, Any]]):
    pass


@dataclass(frozen=True)
class OracleResult:
    ok: bool
    error_family: str | None
    error_offset: int | None
    nodes: int
    arrays: int
    objects: int
    fields: int
    checksum: int
    maximum_depth: int

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-JSON constant: {value}")


def _update(checksum: int, byte: int) -> int:
    return ((checksum ^ byte) * FNV_PRIME) & MASK_U64


def _update_bytes(checksum: int, payload: bytes) -> int:
    for byte in payload:
        checksum = _update(checksum, byte)
    return checksum


def _visit(value: Any, checksum: int, depth: int = 0) -> tuple[int, int, int, int, int, int]:
    nodes = 1
    arrays = objects = fields = 0
    maximum_depth = depth
    if value is None:
        checksum = _update(checksum, 0)
    elif isinstance(value, bool):
        checksum = _update(checksum, 1)
        checksum = _update(checksum, 1 if value else 0)
    elif isinstance(value, NumberLexeme):
        checksum = _update(checksum, 2)
        checksum = _update_bytes(checksum, value.encode("utf-8"))
    elif isinstance(value, str):
        checksum = _update(checksum, 3)
        checksum = _update_bytes(checksum, value.encode("utf-8"))
    elif isinstance(value, ObjectPairs):
        checksum = _update(checksum, 5)
        objects = 1
        fields = len(value)
        for key, child in value:
            checksum = _update_bytes(checksum, key.encode("utf-8"))
            child_values = _visit(child, checksum, depth + 1)
            checksum = child_values[0]
            nodes += child_values[1]
            arrays += child_values[2]
            objects += child_values[3]
            fields += child_values[4]
            maximum_depth = max(maximum_depth, child_values[5])
    elif isinstance(value, list):
        checksum = _update(checksum, 4)
        arrays = 1
        for child in value:
            child_values = _visit(child, checksum, depth + 1)
            checksum = child_values[0]
            nodes += child_values[1]
            arrays += child_values[2]
            objects += child_values[3]
            fields += child_values[4]
            maximum_depth = max(maximum_depth, child_values[5])
    else:
        raise TypeError(type(value).__name__)
    return checksum, nodes, arrays, objects, fields, maximum_depth


def evaluate_python_oracle(payload: bytes, *, maximum_depth: int = 128) -> OracleResult:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        return OracleResult(False, "InvalidUtf8", exc.start, 0, 0, 0, 0, FNV_OFFSET, 0)
    try:
        value = json.loads(
            text,
            object_pairs_hook=ObjectPairs,
            parse_int=NumberLexeme,
            parse_float=NumberLexeme,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        byte_offset = len(text[: exc.pos].encode("utf-8"))
        return OracleResult(False, "JsonSyntax", byte_offset, 0, 0, 0, 0, FNV_OFFSET, 0)
    except ValueError:
        return OracleResult(False, "InvalidNumber", 0, 0, 0, 0, 0, FNV_OFFSET, 0)
    try:
        checksum, nodes, arrays, objects, fields, depth = _visit(value, FNV_OFFSET)
    except UnicodeEncodeError:
        return OracleResult(False, "InvalidString", 0, 0, 0, 0, 0, FNV_OFFSET, 0)
    if depth > maximum_depth:
        return OracleResult(False, "DepthExceeded", 0, 0, 0, 0, 0, FNV_OFFSET, depth)
    return OracleResult(True, None, None, nodes, arrays, objects, fields, checksum, depth)


__all__ = [
    "FNV_OFFSET",
    "FNV_PRIME",
    "MASK_U64",
    "NumberLexeme",
    "ObjectPairs",
    "OracleResult",
    "evaluate_python_oracle",
]
