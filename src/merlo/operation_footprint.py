"""Canonical immutable operation-footprint contracts.

Operation footprints are the one source of truth for the places an operation
can inspect, mutate, borrow, relocate, allocate, or free.  Frontend and IR
consumers use this small contract rather than maintaining operation-specific
metadata tables.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping
import re


OPERATION_FOOTPRINT_SCHEMA_VERSION = 1
OPERATION_FOOTPRINT_CONTRACT = "merlo.operation-footprint.v1"

_ROOTS = frozenset({"receiver", "parameter"})
_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[[^\[\]]+\])?\.[A-Za-z_][A-Za-z0-9_]*$")
_PATTERN_RE = re.compile(r"^(?:receiver(?:\[\*\]|\.deref)?|parameter\[[0-9]+\](?:\.state)?)$")
_GENERIC_RECEIVER_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\[[^\[\]]+\]$")


def _canonical_pattern(root: str, projections: tuple[str, ...]) -> str:
    if root == "receiver":
        if projections == ():
            return "receiver"
        if projections == ("*",):
            return "receiver[*]"
        if projections == ("deref",):
            return "receiver.deref"
    elif root == "parameter":
        if len(projections) == 1 and projections[0].isdigit() and (
            projections[0] == "0" or not projections[0].startswith("0")
        ):
            return f"parameter[{projections[0]}]"
        if len(projections) == 2 and projections[0].isdigit() and (
            projections[0] == "0" or not projections[0].startswith("0")
        ) and projections[1] == "state":
            return f"parameter[{projections[0]}].state"
    raise ValueError("non-canonical operation place pattern")


@dataclass(frozen=True, order=True, slots=True)
class PlacePattern:
    """A strict finite pattern rooted at the receiver or a call parameter.

    ``projections`` uses ``"*"`` for an indexed element, ``"deref"`` for a
    dereference, and ``"state"`` for resource parameter state.  Parameter
    indices are represented by a decimal projection as in
    ``PlacePattern("parameter", ("0", "state"))``.
    """

    root: str
    projections: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.root, str) or self.root not in _ROOTS:
            raise ValueError("operation place pattern root must be receiver or parameter")
        projections = tuple(self.projections)
        if any(not isinstance(item, str) for item in projections):
            raise ValueError("operation place pattern projections must be text")
        _canonical_pattern(self.root, projections)
        object.__setattr__(self, "projections", projections)

    @classmethod
    def parse(cls, value: str) -> "PlacePattern":
        if not isinstance(value, str) or _PATTERN_RE.fullmatch(value) is None:
            raise ValueError("invalid operation place pattern")
        if value == "receiver":
            return cls("receiver")
        if value == "receiver[*]":
            return cls("receiver", ("*",))
        if value == "receiver.deref":
            return cls("receiver", ("deref",))
        match = re.fullmatch(r"parameter\[([0-9]+)\](?:\.state)?", value)
        assert match is not None
        projections = (match.group(1), "state") if value.endswith(".state") else (match.group(1),)
        return cls("parameter", projections)

    @property
    def syntax(self) -> str:
        return _canonical_pattern(self.root, self.projections)

    def render(self) -> str:
        return self.syntax

    def to_dict(self) -> str:
        """Return the canonical scalar representation used in footprint maps."""
        return self.syntax

    @classmethod
    def from_dict(cls, value: Any) -> "PlacePattern":
        # The serialized form is deliberately scalar; accepting only strings
        # keeps malformed mappings from being silently interpreted.
        return cls.parse(value)


def _patterns(value: Any, field_name: str) -> tuple[PlacePattern, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise ValueError(f"{field_name} must be a sequence of place patterns")
    result = tuple(item if isinstance(item, PlacePattern) else PlacePattern.from_dict(item) for item in value)
    if tuple(sorted(result)) != result or len(set(result)) != len(result):
        raise ValueError(f"{field_name} must be sorted and duplicate-free")
    return result


@dataclass(frozen=True, slots=True)
class OperationFootprint:
    """Immutable operation effects over canonical abstract places."""

    read_places: tuple[PlacePattern, ...] = ()
    write_places: tuple[PlacePattern, ...] = ()
    borrow_places: tuple[PlacePattern, ...] = ()
    invalidated_borrows: tuple[PlacePattern, ...] = ()
    may_relocate: bool = False
    allocates: bool = False
    frees: bool = False
    atomicity: str = "none"
    blocking: bool = False
    device_compatibility: tuple[str, ...] = ("cpu",)
    schema_version: int = OPERATION_FOOTPRINT_SCHEMA_VERSION
    contract: str = OPERATION_FOOTPRINT_CONTRACT

    def __post_init__(self) -> None:
        for name in ("read_places", "write_places", "borrow_places", "invalidated_borrows"):
            object.__setattr__(self, name, _patterns(getattr(self, name), name))
        if isinstance(self.device_compatibility, (str, bytes)) or not isinstance(
            self.device_compatibility, (tuple, list)
        ):
            raise ValueError("device compatibility must be a sequence")
        devices = tuple(self.device_compatibility)
        if (
            not devices
            or any(
                not isinstance(item, str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]*", item)
                for item in devices
            )
            or tuple(sorted(set(devices))) != devices
        ):
            raise ValueError("device compatibility must be sorted and duplicate-free")
        object.__setattr__(self, "device_compatibility", devices)
        for name in ("may_relocate", "allocates", "frees", "blocking"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if not isinstance(self.atomicity, str) or self.atomicity not in {
            "none",
            "operation",
        }:
            raise ValueError("atomicity must be none or operation")
        if type(self.schema_version) is not int or self.schema_version != OPERATION_FOOTPRINT_SCHEMA_VERSION:
            raise ValueError("operation footprint schema version mismatch")
        if self.contract != OPERATION_FOOTPRINT_CONTRACT:
            raise ValueError("operation footprint contract mismatch")

    @property
    def borrow_invalidated_places(self) -> tuple[PlacePattern, ...]:
        return self.invalidated_borrows

    @property
    def reads(self) -> tuple[PlacePattern, ...]:
        return self.read_places

    @property
    def writes(self) -> tuple[PlacePattern, ...]:
        return self.write_places

    @property
    def borrows(self) -> tuple[PlacePattern, ...]:
        return self.borrow_places

    def to_dict(self) -> dict[str, Any]:
        # Tuples are intentional: generated IR attributes retain deep
        # immutability while remaining directly JSON-compatible after the
        # ordinary JSON encoder's tuple-as-array conversion.
        return {
            "read_places": tuple(item.to_dict() for item in self.read_places),
            "write_places": tuple(item.to_dict() for item in self.write_places),
            "borrow_places": tuple(item.to_dict() for item in self.borrow_places),
            "invalidated_borrows": tuple(item.to_dict() for item in self.invalidated_borrows),
            "may_relocate": self.may_relocate,
            "allocates": self.allocates,
            "frees": self.frees,
            "atomicity": self.atomicity,
            "blocking": self.blocking,
            "device_compatibility": self.device_compatibility,
            "schema_version": self.schema_version,
            "contract": self.contract,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "OperationFootprint":
        expected = {
            "read_places", "write_places", "borrow_places", "invalidated_borrows",
            "may_relocate", "allocates", "frees", "atomicity", "blocking",
            "device_compatibility", "schema_version", "contract",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("operation footprint schema mismatch")
        return cls(
            read_places=_patterns(value["read_places"], "read_places"),
            write_places=_patterns(value["write_places"], "write_places"),
            borrow_places=_patterns(value["borrow_places"], "borrow_places"),
            invalidated_borrows=_patterns(value["invalidated_borrows"], "invalidated_borrows"),
            may_relocate=value["may_relocate"],
            allocates=value["allocates"],
            frees=value["frees"],
            atomicity=value["atomicity"],
            blocking=value["blocking"],
            device_compatibility=value["device_compatibility"],
            schema_version=value["schema_version"],
            contract=value["contract"],
        )


def _p(root: str, *projections: str) -> PlacePattern:
    return PlacePattern(root, tuple(projections))


def _footprint(**kwargs: Any) -> OperationFootprint:
    return OperationFootprint(**kwargs)


_CATALOG: dict[str, OperationFootprint] = {
    "Vec.get": _footprint(
        read_places=(_p("receiver", "*"),),
        borrow_places=(_p("receiver", "*"),),
    ),
    "Vec.get_mut": _footprint(
        read_places=(_p("receiver", "*"),),
        write_places=(_p("receiver", "*"),),
        borrow_places=(_p("receiver", "*"),),
    ),
    "Vec.set": _footprint(
        write_places=(_p("receiver", "*"),),
        invalidated_borrows=(_p("receiver", "*"),),
        atomicity="operation",
    ),
    "Vec.push": _footprint(
        read_places=(_p("receiver"),),
        write_places=(_p("receiver"),),
        may_relocate=True,
        invalidated_borrows=(_p("receiver", "*"),),
        allocates=True,
        atomicity="operation",
    ),
    "Vec.len": _footprint(read_places=(_p("receiver"),)),
    "Vec.capacity": _footprint(read_places=(_p("receiver"),)),
    "Vec.view": _footprint(
        read_places=(_p("receiver"),),
        borrow_places=(_p("receiver"),),
    ),
    "Vec.new": _footprint(allocates=True),
    "Map.get": _footprint(read_places=(_p("receiver"),)),
    "Map.insert": _footprint(
        read_places=(_p("receiver"),),
        write_places=(_p("receiver"),),
        may_relocate=True,
        invalidated_borrows=(_p("receiver"),),
        allocates=True,
        atomicity="operation",
    ),
    "Map.increment": _footprint(
        read_places=(_p("receiver"),),
        write_places=(_p("receiver"),),
        may_relocate=True,
        invalidated_borrows=(_p("receiver"),),
        allocates=True,
        atomicity="operation",
    ),
    "Map.entries": _footprint(
        read_places=(_p("receiver"),),
        borrow_places=(_p("receiver"),),
    ),
    "Map.new": _footprint(allocates=True),
    "Box.get": _footprint(
        read_places=(_p("receiver", "deref"),),
        borrow_places=(_p("receiver", "deref"),),
    ),
    "Box.new": _footprint(allocates=True),
    "fs.read_chunk": _footprint(
        read_places=(_p("parameter", "0", "state"),),
        write_places=(_p("parameter", "0", "state"),),
        allocates=True,
        blocking=True,
    ),
    "fs.close_read": _footprint(
        read_places=(_p("parameter", "0", "state"),),
        write_places=(_p("parameter", "0", "state"),),
        invalidated_borrows=(_p("parameter", "0"),),
        frees=True,
        atomicity="operation",
    ),
    "fs.close_write": _footprint(
        read_places=(_p("parameter", "0", "state"),),
        write_places=(_p("parameter", "0", "state"),),
        invalidated_borrows=(_p("parameter", "0"),),
        frees=True,
        atomicity="operation",
    ),
}

OPERATION_FOOTPRINT_CATALOG = MappingProxyType(_CATALOG)
OPERATION_FOOTPRINTS = OPERATION_FOOTPRINT_CATALOG


def canonical_operation_symbol(symbol: str) -> str:
    """Normalize a concrete generic receiver to its constructor."""
    if not isinstance(symbol, str) or not symbol:
        return symbol
    receiver, separator, name = symbol.rpartition(".")
    if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return symbol
    base_match = re.match(r"^[A-Za-z_][A-Za-z0-9_]*", receiver)
    if base_match is None:
        return symbol
    base = base_match.group(0)
    suffix = receiver[len(base):]
    if not suffix:
        return f"{base}.{name}"
    if not suffix.startswith("[") or not suffix.endswith("]"):
        return symbol
    depth = 0
    for character in suffix:
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth < 0:
                return symbol
    if depth != 0 or len(suffix) <= 2:
        return symbol
    return f"{base}.{name}"


canonical_symbol = canonical_operation_symbol


def operation_footprint(symbol: str) -> OperationFootprint | None:
    if not isinstance(symbol, str):
        return None
    return OPERATION_FOOTPRINT_CATALOG.get(canonical_operation_symbol(symbol))


def validate_footprint_attributes(
    attributes: Mapping[str, Any],
    *,
    label: str = "operation",
) -> None:
    """Validate serialized footprint metadata against the canonical catalog."""
    if not isinstance(attributes, Mapping):
        raise ValueError(f"{label} attributes must be a mapping")
    symbol = attributes.get("contract_symbol") or attributes.get("callee")
    actual = attributes.get("operation_footprint")
    expected = operation_footprint(symbol) if isinstance(symbol, str) else None
    if expected is not None:
        if actual is None:
            raise ValueError(f"{label} operation footprint mismatch")
        try:
            decoded = OperationFootprint.from_dict(actual)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} operation footprint schema mismatch") from exc
        if decoded != expected:
            raise ValueError(f"{label} operation footprint mismatch")
        return
    if isinstance(actual, Mapping) and actual.get("contract") == OPERATION_FOOTPRINT_CONTRACT:
        raise ValueError(f"{label} unknown operation footprint contract")


def footprint_attributes(footprint: OperationFootprint | None) -> dict[str, Any]:
    if footprint is None:
        return {}
    if not isinstance(footprint, OperationFootprint):
        raise TypeError("footprint must be an OperationFootprint or None")
    return {"operation_footprint": footprint.to_dict()}


__all__ = [
    "OPERATION_FOOTPRINT_CONTRACT",
    "OPERATION_FOOTPRINT_SCHEMA_VERSION",
    "OPERATION_FOOTPRINT_CATALOG",
    "OPERATION_FOOTPRINTS",
    "OperationFootprint",
    "PlacePattern",
    "canonical_operation_symbol",
    "canonical_symbol",
    "footprint_attributes",
    "operation_footprint",
    "validate_footprint_attributes",
]


