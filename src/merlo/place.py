"""Versioned structural places for ownership and borrow analysis.

A place identifies a semantic storage location without using source spelling as
its identity. Roots use stable symbol identities; projections use stable field
and variant identities or a deliberately conservative index class. The module
is intentionally independent of the HIR so the same contract can be used by
borrow summaries, HIR diagnostics, and downstream semantic tooling.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any, Mapping


PLACE_SCHEMA_VERSION = 1
PLACE_CONTRACT = "merlo.place.v1"
MAX_U64 = (1 << 64) - 1


class PlaceError(ValueError):
    """Base error for malformed or unsupported semantic places."""


class UnsupportedProjectionError(PlaceError):
    """A projection is outside the closed Place v1 vocabulary."""


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PlaceError(f"{label} must be a non-empty semantic identity")
    return value


def _keys(value: object, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PlaceError(f"invalid {label}")
    return value


class OverlapRelation(str, Enum):
    """Conservative relation between two structural places."""

    EQUAL = "Equal"
    ANCESTOR = "Ancestor"
    DESCENDANT = "Descendant"
    DISJOINT = "Disjoint"
    MAY_OVERLAP = "MayOverlap"

    def __str__(self) -> str:
        return self.value


PlaceRelation = OverlapRelation


@dataclass(frozen=True, order=True)
class IndexClass:
    """The v1 index abstraction: one constant or an unknown dynamic index."""

    kind: str
    value: int | None = None

    CONSTANT = "Constant"
    DYNAMIC = "Dynamic"

    def __post_init__(self) -> None:
        if self.kind == self.CONSTANT:
            if type(self.value) is not int or not 0 <= self.value <= MAX_U64:
                raise PlaceError("constant index requires a UInt64 value")
        elif self.kind == self.DYNAMIC:
            if self.value is not None:
                raise PlaceError("dynamic index cannot carry a value")
        else:
            raise UnsupportedProjectionError(f"unsupported index class: {self.kind}")

    @classmethod
    def constant(cls, value: int) -> "IndexClass":
        return cls(cls.CONSTANT, value)

    @classmethod
    def const(cls, value: int) -> "IndexClass":
        return cls.constant(value)

    @classmethod
    def dynamic(cls) -> "IndexClass":
        return cls(cls.DYNAMIC)

    @property
    def is_constant(self) -> bool:
        return self.kind == self.CONSTANT

    @property
    def is_dynamic(self) -> bool:
        return self.kind == self.DYNAMIC

    def to_dict(self) -> dict[str, Any]:
        if self.is_constant:
            return {"kind": self.CONSTANT, "value": self.value}
        return {"kind": self.DYNAMIC}

    @classmethod
    def from_dict(cls, value: object) -> "IndexClass":
        if not isinstance(value, Mapping):
            raise PlaceError("invalid index class")
        kind = value.get("kind")
        if kind == cls.CONSTANT:
            if set(value) != {"kind", "value"}:
                raise PlaceError("invalid constant index class")
            return cls.constant(value.get("value"))  # type: ignore[arg-type]
        if kind == cls.DYNAMIC:
            if set(value) != {"kind"}:
                raise PlaceError("invalid dynamic index class")
            return cls.dynamic()
        raise UnsupportedProjectionError(f"unsupported index class: {kind}")


@dataclass(frozen=True, order=True)
class PlaceRoot:
    """A semantic place root identified by a compilation-local symbol identity.

    ``symbol_id`` is an identity for the current compilation, not a source
    spelling or a globally stable package identifier. Local, parameter, and
    receiver roots remain distinct even when their names happen to match.
    """

    kind: str
    symbol_id: str

    LOCAL = "Local"
    PARAM = "Param"
    SELF = "Self"

    def __post_init__(self) -> None:
        if self.kind not in {self.LOCAL, self.PARAM, self.SELF}:
            raise UnsupportedProjectionError(f"unsupported place root: {self.kind}")
        _id(self.symbol_id, "place root symbol_id")

    @classmethod
    def local(cls, symbol_id: str) -> "PlaceRoot":
        return cls(cls.LOCAL, _id(symbol_id, "local symbol_id"))

    @classmethod
    def param(cls, symbol_id: str) -> "PlaceRoot":
        return cls(cls.PARAM, _id(symbol_id, "parameter symbol_id"))

    @classmethod
    def parameter(cls, symbol_id: str) -> "PlaceRoot":
        return cls.param(symbol_id)

    @classmethod
    def self(cls, symbol_id: str) -> "PlaceRoot":
        return cls(cls.SELF, _id(symbol_id, "self symbol_id"))

    @classmethod
    def self_root(cls, symbol_id: str) -> "PlaceRoot":
        return cls.self(symbol_id)

    @property
    def is_local(self) -> bool:
        return self.kind == self.LOCAL

    @property
    def is_param(self) -> bool:
        return self.kind == self.PARAM

    @property
    def is_self(self) -> bool:
        return self.kind == self.SELF

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "symbol_id": self.symbol_id}

    @classmethod
    def from_dict(cls, value: object) -> "PlaceRoot":
        raw = _keys(value, {"kind", "symbol_id"}, "place root")
        return cls(str(raw["kind"]), _id(raw["symbol_id"], "place root symbol_id"))


@dataclass(frozen=True, order=True)
class PlaceStep:
    """One closed structural projection in Place v1."""

    kind: str
    value: str | IndexClass | None = None

    FIELD = "Field"
    VARIANT_PAYLOAD = "VariantPayload"
    INDEX = "Index"
    DEREFERENCE = "Dereference"

    def __post_init__(self) -> None:
        if self.kind == self.FIELD:
            _id(self.value, "field_id")
        elif self.kind == self.VARIANT_PAYLOAD:
            _id(self.value, "variant_id")
        elif self.kind == self.INDEX:
            if not isinstance(self.value, IndexClass):
                raise PlaceError("index projection requires IndexClass")
        elif self.kind == self.DEREFERENCE:
            if self.value is not None:
                raise PlaceError("dereference projection cannot carry a value")
        else:
            raise UnsupportedProjectionError(f"unsupported place step: {self.kind}")

    @classmethod
    def field(cls, field_id: str) -> "PlaceStep":
        return cls(cls.FIELD, _id(field_id, "field_id"))

    @classmethod
    def variant_payload(cls, variant_id: str) -> "PlaceStep":
        return cls(cls.VARIANT_PAYLOAD, _id(variant_id, "variant_id"))

    @classmethod
    def index(cls, index_class: IndexClass) -> "PlaceStep":
        return cls(cls.INDEX, index_class)

    @classmethod
    def dereference(cls) -> "PlaceStep":
        return cls(cls.DEREFERENCE)

    @property
    def field_id(self) -> str | None:
        return self.value if self.kind == self.FIELD else None  # type: ignore[return-value]

    @property
    def variant_id(self) -> str | None:
        return self.value if self.kind == self.VARIANT_PAYLOAD else None  # type: ignore[return-value]

    @property
    def index_class(self) -> IndexClass | None:
        return self.value if self.kind == self.INDEX else None  # type: ignore[return-value]

    def to_dict(self) -> dict[str, Any]:
        if self.kind == self.FIELD:
            return {"kind": self.FIELD, "field_id": self.field_id}
        if self.kind == self.VARIANT_PAYLOAD:
            return {"kind": self.VARIANT_PAYLOAD, "variant_id": self.variant_id}
        if self.kind == self.INDEX:
            return {"kind": self.INDEX, "index_class": self.index_class.to_dict()}  # type: ignore[union-attr]
        return {"kind": self.DEREFERENCE}

    @classmethod
    def from_dict(cls, value: object) -> "PlaceStep":
        if not isinstance(value, Mapping):
            raise PlaceError("invalid place step")
        kind = value.get("kind")
        if kind == cls.FIELD:
            raw = _keys(value, {"kind", "field_id"}, "field projection")
            return cls.field(_id(raw["field_id"], "field_id"))
        if kind == cls.VARIANT_PAYLOAD:
            raw = _keys(value, {"kind", "variant_id"}, "variant payload projection")
            return cls.variant_payload(_id(raw["variant_id"], "variant_id"))
        if kind == cls.INDEX:
            raw = _keys(value, {"kind", "index_class"}, "index projection")
            return cls.index(IndexClass.from_dict(raw["index_class"]))
        if kind == cls.DEREFERENCE:
            _keys(value, {"kind"}, "dereference projection")
            return cls.dereference()
        raise UnsupportedProjectionError(f"unsupported place step: {kind}")


@dataclass(frozen=True, order=True)
class Place:
    """A root plus a finite sequence of structural projections."""

    root: PlaceRoot
    steps: tuple[PlaceStep, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.root, PlaceRoot):
            raise PlaceError("place root must be PlaceRoot")
        steps = tuple(self.steps)
        if any(not isinstance(step, PlaceStep) for step in steps):
            raise PlaceError("place steps must be PlaceStep values")
        object.__setattr__(self, "steps", steps)

    @classmethod
    def from_root(cls, root: PlaceRoot) -> "Place":
        return cls(root)

    def project(self, step: PlaceStep) -> "Place":
        if not isinstance(step, PlaceStep):
            raise UnsupportedProjectionError("unsupported place projection")
        return Place(self.root, (*self.steps, step))

    def append(self, *steps: PlaceStep) -> "Place":
        result = self
        for step in steps:
            result = result.project(step)
        return result

    @property
    def semantic_key(self) -> tuple[object, ...]:
        return (self.root, self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": PLACE_CONTRACT,
            "schema_version": PLACE_SCHEMA_VERSION,
            "root": self.root.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ) + "\n"

    @classmethod
    def from_dict(cls, value: object) -> "Place":
        raw = _keys(
            value,
            {"contract", "schema_version", "root", "steps"},
            "place",
        )
        if raw["contract"] != PLACE_CONTRACT or raw["schema_version"] != PLACE_SCHEMA_VERSION:
            raise PlaceError("place contract or schema version mismatch")
        steps = raw["steps"]
        if not isinstance(steps, list):
            raise PlaceError("place steps must be a list")
        return cls(
            PlaceRoot.from_dict(raw["root"]),
            tuple(PlaceStep.from_dict(step) for step in steps),
        )

    @classmethod
    def from_json(cls, value: str) -> "Place":
        try:
            raw = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise PlaceError("invalid place JSON") from exc
        return cls.from_dict(raw)

    def overlap(self, other: "Place") -> OverlapRelation:
        return overlap_relation(self, other)


def _step_relation(left: PlaceStep, right: PlaceStep) -> OverlapRelation | None:
    if left == right:
        if (
            left.kind == PlaceStep.INDEX
            and left.index_class is not None
            and left.index_class.is_dynamic
        ):
            return OverlapRelation.MAY_OVERLAP
        return None
    if left.kind == right.kind == PlaceStep.FIELD:
        return OverlapRelation.DISJOINT
    if left.kind == right.kind == PlaceStep.VARIANT_PAYLOAD:
        return OverlapRelation.DISJOINT
    if left.kind == right.kind == PlaceStep.INDEX:
        left_index = left.index_class
        right_index = right.index_class
        if left_index.is_constant and right_index.is_constant:
            return OverlapRelation.DISJOINT
        return OverlapRelation.MAY_OVERLAP
    # Different projection kinds are not proven disjoint without the type
    # environment. Returning MayOverlap is the fail-closed choice.
    return OverlapRelation.MAY_OVERLAP


def overlap_relation(left: Place, right: Place) -> OverlapRelation:
    """Classify two places with a fail-closed structural relation."""

    if not isinstance(left, Place) or not isinstance(right, Place):
        raise PlaceError("overlap requires two Place values")
    if left.root != right.root:
        return OverlapRelation.DISJOINT
    common = min(len(left.steps), len(right.steps))
    for index in range(common):
        relation = _step_relation(left.steps[index], right.steps[index])
        if relation is not None:
            return relation
    if len(left.steps) == len(right.steps):
        return OverlapRelation.EQUAL
    if len(left.steps) < len(right.steps):
        return OverlapRelation.ANCESTOR
    return OverlapRelation.DESCENDANT


classify_overlap = overlap_relation
place_overlap = overlap_relation


__all__ = [
    "IndexClass",
    "MAX_U64",
    "OverlapRelation",
    "PLACE_CONTRACT",
    "PLACE_SCHEMA_VERSION",
    "Place",
    "PlaceError",
    "PlaceRelation",
    "PlaceRoot",
    "PlaceStep",
    "UnsupportedProjectionError",
    "classify_overlap",
    "overlap_relation",
    "place_overlap",
]
