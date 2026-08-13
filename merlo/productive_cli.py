from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath
from typing import Literal


ProductiveOptionType = Literal["Text", "UInt64", "Byte", "Bool"]
_UINT64_MAX_TEXT = "18446744073709551615"


class ProductiveCliErrorFamily(str, Enum):
    UNKNOWN = "unknown"
    DUPLICATE = "duplicate"
    MISSING = "missing"
    MALFORMED = "malformed"
    OVERFLOW = "overflow"
    INVALID_TEXT = "invalid-text"
    EXTRA_POSITIONAL = "extra-positional"


class ProductiveCliError(ValueError):
    def __init__(
        self,
        index: int,
        name: str,
        family: ProductiveCliErrorFamily,
    ) -> None:
        self.index = index
        self.name = name
        self.family = family
        super().__init__(
            f"ProductiveCliError: family={family.value} index={index} name={name!r}"
        )


@dataclass(frozen=True, slots=True)
class ProductiveOptionSpec:
    name: str
    type_name: ProductiveOptionType


PRODUCTIVE_OPTION_SCHEMA = (
    ProductiveOptionSpec("level", "Text"),
    ProductiveOptionSpec("service", "Text"),
    ProductiveOptionSpec("contains", "Text"),
    ProductiveOptionSpec("column", "Text"),
    ProductiveOptionSpec("minimum-duration", "UInt64"),
    ProductiveOptionSpec("delimiter", "Byte"),
    ProductiveOptionSpec("ignore-case", "Bool"),
    ProductiveOptionSpec("count", "Bool"),
)
_OPTION_BY_NAME = {option.name: option for option in PRODUCTIVE_OPTION_SCHEMA}


@dataclass(frozen=True, slots=True)
class ProductiveCliOptions:
    path: PurePath
    level: str | None = None
    service: str | None = None
    contains: str | None = None
    column: str | None = None
    minimum_duration: int | None = None
    delimiter: int | None = None
    ignore_case: bool = False
    count: bool = False


def _raise(
    index: int,
    name: str,
    family: ProductiveCliErrorFamily,
) -> None:
    raise ProductiveCliError(index, name, family)


def _check_text(raw: str, index: int, name: str) -> str:
    try:
        raw.encode("utf-8", "strict")
    except UnicodeError:
        _raise(index, name, ProductiveCliErrorFamily.INVALID_TEXT)
    return raw


def _parse_path(raw: str, index: int) -> PurePath:
    _check_text(raw, index, "path")
    if not raw or "\x00" in raw:
        _raise(index, "path", ProductiveCliErrorFamily.MALFORMED)
    return PurePath(raw)


def _parse_uint64(raw: str, index: int, name: str) -> int:
    _check_text(raw, index, name)
    if not raw or any(character < "0" or character > "9" for character in raw):
        _raise(index, name, ProductiveCliErrorFamily.MALFORMED)

    first_significant = 0
    while first_significant < len(raw) - 1 and raw[first_significant] == "0":
        first_significant += 1
    significant = raw[first_significant:]
    if len(significant) > len(_UINT64_MAX_TEXT) or (
        len(significant) == len(_UINT64_MAX_TEXT)
        and significant > _UINT64_MAX_TEXT
    ):
        _raise(index, name, ProductiveCliErrorFamily.OVERFLOW)
    return int(significant, 10)


def _parse_byte(raw: str, index: int, name: str) -> int:
    _check_text(raw, index, name)
    encoded = raw.encode("utf-8")
    if len(encoded) != 1:
        _raise(index, name, ProductiveCliErrorFamily.MALFORMED)
    return encoded[0]


def _option_value(
    arguments: Sequence[str],
    option_index: int,
    name: str,
) -> tuple[str, int]:
    value_index = option_index + 1
    if value_index >= len(arguments) or arguments[value_index].startswith("--"):
        _raise(option_index, name, ProductiveCliErrorFamily.MISSING)
    return arguments[value_index], value_index


def parse_productive_cli(arguments: Sequence[str]) -> ProductiveCliOptions:
    path: PurePath | None = None
    level: str | None = None
    service: str | None = None
    contains: str | None = None
    column: str | None = None
    minimum_duration: int | None = None
    delimiter: int | None = None
    ignore_case = False
    count = False
    seen: set[str] = set()

    index = 0
    while index < len(arguments):
        raw = arguments[index]
        if not raw.startswith("--"):
            if path is not None:
                _check_text(raw, index, "path")
                _raise(index, "path", ProductiveCliErrorFamily.EXTRA_POSITIONAL)
            path = _parse_path(raw, index)
            index += 1
            continue

        _check_text(raw, index, "argument")
        name = raw[2:]
        specification = _OPTION_BY_NAME.get(name)
        if specification is None:
            _raise(index, name, ProductiveCliErrorFamily.UNKNOWN)
        if name in seen:
            _raise(index, name, ProductiveCliErrorFamily.DUPLICATE)
        seen.add(name)

        if specification.type_name == "Bool":
            next_index = index + 1
            parsed_bool = True
            if next_index < len(arguments):
                candidate = arguments[next_index]
                if candidate in {"true", "false"}:
                    parsed_bool = candidate == "true"
                    index += 2
                elif path is not None and not candidate.startswith("--"):
                    _check_text(candidate, next_index, name)
                    _raise(
                        next_index,
                        name,
                        ProductiveCliErrorFamily.MALFORMED,
                    )
                else:
                    index += 1
            else:
                index += 1
            if name == "ignore-case":
                ignore_case = parsed_bool
            else:
                count = parsed_bool
            continue

        raw_value, value_index = _option_value(arguments, index, name)
        if specification.type_name == "Text":
            value = _check_text(raw_value, value_index, name)
            if name == "level":
                level = value
            elif name == "service":
                service = value
            elif name == "contains":
                contains = value
            else:
                column = value
        elif specification.type_name == "UInt64":
            minimum_duration = _parse_uint64(raw_value, value_index, name)
        else:
            delimiter = _parse_byte(raw_value, value_index, name)
        index += 2

    if path is None:
        _raise(len(arguments), "path", ProductiveCliErrorFamily.MISSING)
    return ProductiveCliOptions(
        path=path,
        level=level,
        service=service,
        contains=contains,
        column=column,
        minimum_duration=minimum_duration,
        delimiter=delimiter,
        ignore_case=ignore_case,
        count=count,
    )


__all__ = [
    "PRODUCTIVE_OPTION_SCHEMA",
    "ProductiveCliError",
    "ProductiveCliErrorFamily",
    "ProductiveCliOptions",
    "ProductiveOptionSpec",
    "ProductiveOptionType",
    "parse_productive_cli",
]
