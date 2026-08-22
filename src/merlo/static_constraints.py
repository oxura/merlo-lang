"""Closed, compile-time type constraints used by generic specialization."""

from __future__ import annotations

from collections.abc import Mapping

from merlo.surface_ast import SurfaceEnum, SurfaceRecord
from merlo.collection_protocol import collection_shape
from merlo.type_parser import TypeExpr, parse_type
from merlo.type_arena import TypeArenaError, TypeContextBuilder

SUPPORTED_CONSTRAINTS = frozenset(
    {"Comparable", "Hashable", "Iterable", "Display", "Encode"}
)

_NUMERIC = frozenset(
    {
        "Byte",
        "Int8",
        "Int16",
        "Int32",
        "Int64",
        "Int",
        "UInt8",
        "UInt16",
        "UInt32",
        "UInt64",
        "UInt",
        "Float32",
        "Float64",
    }
)
_TEXTUAL = frozenset({"Text", "TextView", "Bytes", "BytesView", "Path"})
_SCALAR = _NUMERIC | _TEXTUAL | {"Bool"}


def _all_arguments(
    constraint: str,
    expression: TypeExpr,
    records: Mapping[str, SurfaceRecord],
    enums: Mapping[str, SurfaceEnum],
    visiting: frozenset[tuple[str, str]],
    authority: TypeContextBuilder | None,
) -> bool:
    return all(
        _satisfies(constraint, item, records, enums, visiting, authority)
        for item in expression.args
    )


def _satisfies(
    constraint: str,
    expression: TypeExpr,
    records: Mapping[str, SurfaceRecord],
    enums: Mapping[str, SurfaceEnum],
    visiting: frozenset[tuple[str, str]],
    authority: TypeContextBuilder | None,
) -> bool:
    key = (constraint, expression.canonical)
    if key in visiting:
        return True
    nested_visiting = visiting | {key}

    if constraint == "Comparable":
        return not expression.args and expression.name in _SCALAR
    if constraint == "Hashable":
        return not expression.args and expression.name in (
            (_NUMERIC - {"Float32", "Float64"}) | _TEXTUAL | {"Bool"}
        )
    if constraint == "Iterable":
        try:
            type_id = authority.intern_expr(expression) if authority is not None else None
        except TypeArenaError:
            return False
        return (
            collection_shape(type_id, authority) is not None
            if authority is not None and type_id is not None
            else collection_shape(expression.canonical) is not None
        )
    if constraint == "Display":
        if not expression.args and expression.name in _SCALAR | {"Unit"}:
            return True
        if expression.name in {"Option", "Result", "Vec", "Array"}:
            return _all_arguments(
                constraint, expression, records, enums, nested_visiting, authority
            )
        return False
    if constraint != "Encode":
        return False
    if not expression.args and expression.name in _SCALAR | {"Unit"}:
        return True
    if expression.name in {"Option", "Result", "Vec", "Array", "Map", "Box"}:
        return _all_arguments(
            constraint, expression, records, enums, nested_visiting, authority
        )
    record = records.get(expression.name)
    if record is not None and not expression.args:
        return all(
            _satisfies(
                constraint,
                parse_type(field.type_name),
                records,
                enums,
                nested_visiting,
                authority,
            )
            for field in record.fields
        )
    enum = enums.get(expression.name)
    if enum is not None and not expression.args:
        return all(
            variant.payload_type is None
            or _satisfies(
                constraint,
                parse_type(variant.payload_type),
                records,
                enums,
                nested_visiting,
                authority,
            )
            for variant in enum.variants
        )
    return False


def satisfies_constraint(
    constraint: str,
    type_name: str,
    *,
    records: Mapping[str, SurfaceRecord] | None = None,
    enums: Mapping[str, SurfaceEnum] | None = None,
    type_context: TypeContextBuilder | None = None,
) -> bool:
    """Return whether a concrete type has the named built-in constraint instance."""

    if constraint not in SUPPORTED_CONSTRAINTS:
        return False
    return _satisfies(
        constraint,
        parse_type(type_name),
        records or {},
        enums or {},
        frozenset(),
        type_context,
    )


__all__ = ["SUPPORTED_CONSTRAINTS", "satisfies_constraint"]
