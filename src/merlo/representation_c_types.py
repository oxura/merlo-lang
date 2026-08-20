"""Type spelling and C layout helpers shared by the representation backend."""

from __future__ import annotations

import re

from merlo import native_syntax as ast
from merlo.ffi import pointer_type
from merlo.representation_ir import TypeDescriptor
from merlo.type_parser import generic_parts, parse_type


def _identifier(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def _generic(type_name: str) -> tuple[str, str] | None:
    try:
        parsed = parse_type(type_name)
    except ValueError:
        return None
    if not parsed.args:
        return None
    return parsed.name, ",".join(item.canonical for item in parsed.args)


def _array_parts(type_name: str) -> tuple[str, int] | None:
    parts = generic_parts(type_name, "Array", arity=2)
    if parts is None:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


def _callback_parts(type_name: str) -> tuple[tuple[str, ...], str] | None:
    parts = generic_parts(type_name, "Fn")
    if parts is None or len(parts) < 2:
        return None
    return parts[:-1], parts[-1]


def _type_from_annotation(node: ast.AST | None) -> str:
    """Normalize an AST annotation to the canonical Merlo type spelling."""
    if node is None:
        return "Unit"
    type_name = ast.unparse(node).replace(" ", "")
    for alias, canonical in {
        "Int": "Int64",
        "UInt": "UInt64",
        "Float": "Float64",
    }.items():
        type_name = re.sub(rf"\b{alias}\b", canonical, type_name)
    return type_name


def _result_types(type_name: str | None) -> tuple[str, str] | None:
    parts = generic_parts(type_name, "Result", arity=2)
    return parts if parts is not None else None  # type: ignore[return-value]


def _map_types(type_name: str) -> tuple[str, str] | None:
    parts = generic_parts(type_name, "Map", arity=2)
    return parts if parts is not None else None  # type: ignore[return-value]


def _map_entry_types(type_name: str) -> tuple[str, str] | None:
    parts = generic_parts(type_name, "MapEntry", arity=2)
    return parts if parts is not None else None  # type: ignore[return-value]


def _c_name(type_name: str) -> str:
    pointer = pointer_type(type_name)
    if pointer is not None:
        return f"{_c_name(pointer.pointee)} *"
    borrowed = generic_parts(type_name, "Borrow", arity=1)
    if borrowed is not None:
        return f"{_c_name(borrowed[0])} *"
    aliases = {
        "Unit": "void",
        "Bool": "bool",
        "Byte": "uint8_t",
        "UInt8": "uint8_t",
        "Int8": "int8_t",
        "UInt16": "uint16_t",
        "Int16": "int16_t",
        "UInt32": "uint32_t",
        "Int32": "int32_t",
        "UInt64": "uint64_t",
        "Int64": "int64_t",
        "Float32": "float",
        "Float64": "double",
        "BytesView": "MerloBytesView",
        "TextView": "MerloTextView",
        "Text": "MerloText",
        "Path": "MerloText",
        "TextBuilder": "MerloTextBuilder",
        "Bytes": "MerloBytes",
        "FileReader": "MerloFileReader",
        "FileWriter": "MerloFileWriter",
        "FileLines": "MerloFileLines",
    }
    if type_name in aliases:
        return aliases[type_name]
    generic = _generic(type_name)
    if generic:
        base, argument = generic
        return f"Merlo{base}_{_identifier(argument)}"
    return f"Merlo_{_identifier(type_name)}"


def _is_owner(descriptor: TypeDescriptor) -> bool:
    """Return whether a descriptor requires type-directed cleanup."""
    return descriptor.drop_class != "trivial"
