from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any


SCRIPT_ARGUMENT_TYPES = frozenset(
    {"Text", "UInt64", "Int64", "Float64", "Bool", "Path"}
)


class ScriptArgumentError(ValueError):
    pass


@dataclass(frozen=True)
class ScriptArgumentBoundary:
    index: int
    name: str
    type_name: str

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("script argument index must be non-negative")
        if not re.fullmatch(r"[A-Za-z_]\w*", self.name):
            raise ValueError("script argument name must be an identifier")
        if self.type_name not in SCRIPT_ARGUMENT_TYPES:
            raise ScriptArgumentError(
                f"UnsupportedArgumentType {self.type_name!r}"
            )

    @property
    def canonical_source(self) -> str:
        return (
            f"let {self.name}: {self.type_name} = "
            f"args.parse<{self.type_name}>({self.index})?"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "type": self.type_name,
            "checked": True,
            "failure": "typed ArgumentError",
            "canonical_source": self.canonical_source,
        }


def _fail(index: int, type_name: str, raw: str) -> ScriptArgumentError:
    return ScriptArgumentError(
        f"ArgumentParseError: index={index} expected={type_name} value={raw!r}"
    )


def parse_script_argument(
    type_name: str,
    raw: str,
    *,
    index: int = 0,
) -> str | int | float | bool | PurePath:
    if type_name not in SCRIPT_ARGUMENT_TYPES:
        raise ScriptArgumentError(
            f"UnsupportedArgumentType {type_name!r}"
        )
    if type_name == "Text":
        try:
            raw.encode("utf-8", "strict")
        except UnicodeError as exc:
            raise _fail(index, type_name, raw) from exc
        return raw
    if type_name == "Path":
        if not raw or "\x00" in raw:
            raise _fail(index, type_name, raw)
        return PurePath(raw)
    if type_name == "Bool":
        if raw in {"true", "1"}:
            return True
        if raw in {"false", "0"}:
            return False
        raise _fail(index, type_name, raw)
    if type_name == "UInt64":
        if re.fullmatch(r"[0-9]+", raw) is None:
            raise _fail(index, type_name, raw)
        value = int(raw, 10)
        if value > (1 << 64) - 1:
            raise _fail(index, type_name, raw)
        return value
    if type_name == "Int64":
        if re.fullmatch(r"[+-]?[0-9]+", raw) is None:
            raise _fail(index, type_name, raw)
        value = int(raw, 10)
        if not -(1 << 63) <= value <= (1 << 63) - 1:
            raise _fail(index, type_name, raw)
        return value
    if re.fullmatch(
        r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?",
        raw,
    ) is None:
        raise _fail(index, type_name, raw)
    value = float(raw)
    if not math.isfinite(value):
        raise _fail(index, type_name, raw)
    return value


__all__ = [
    "SCRIPT_ARGUMENT_TYPES",
    "ScriptArgumentBoundary",
    "ScriptArgumentError",
    "parse_script_argument",
]
