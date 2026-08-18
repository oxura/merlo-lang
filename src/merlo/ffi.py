"""Explicit C ABI declarations and unsafe-boundary validation for Merlo alpha.

The module is intentionally independent of code generation. It parses the small
foreign-declaration surface, computes deterministic ``repr(C)`` layouts, and
checks the ownership/unsafe obligations consumed by the compiler stages.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Iterable
from merlo.type_parser import generic_parts, parse_type

FFI_ABI = "C"
FIXED_WIDTH_TYPES = frozenset({"Int8", "UInt8", "Int16", "UInt16", "Int32", "UInt32", "Int64", "UInt64", "Float32", "Float64", "Bool", "Byte"})
_POINTER_BASES = frozenset({"RawPointer", "Ptr", "ConstPointer", "MutPointer"})
_UNSAFE_NAMES = frozenset({"ptr_from_int", "ptr_to_int", "ptr_add", "ptr_read", "ptr_write", "unsafe_alloc", "unsafe_free", "raw_alloc", "raw_free", "size_of", "align_of", "read_raw", "write_raw"})


class FFICompileError(ValueError):
    """Stable, named rejection from the FFI/unsafe boundary."""

    def __init__(self, diagnostic: str, detail: str = "") -> None:
        self.diagnostic = diagnostic
        self.detail = detail
        super().__init__(f"{diagnostic}: {detail}" if detail else diagnostic)


@dataclass(frozen=True)
class RawPointerType:
    pointee: str
    mutable: bool = False
    nullable: bool = False

    @property
    def type_name(self) -> str:
        return f"RawPointer[{self.pointee}]"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type_name, "pointee": self.pointee, "mutable": self.mutable, "nullable": self.nullable}


@dataclass(frozen=True)
class ForeignPointerPolicy:
    parameter: str
    access: str = "read"
    ownership: str = "borrowed"
    destructor: str | None = None
    nullable: bool = False

    def __post_init__(self) -> None:
        if self.access not in {"read", "write", "store"}:
            raise FFICompileError("ForeignPointerAccessInvalid", self.access)
        if self.ownership not in {"borrowed", "owned"}:
            raise FFICompileError("ForeignPointerOwnershipInvalid", self.ownership)
        if self.ownership == "owned" and not self.destructor:
            raise FFICompileError("ForeignOwnershipUndeclared", self.parameter)
        if self.ownership == "borrowed" and self.destructor is not None:
            raise FFICompileError("BorrowedDestructorForbidden", self.parameter)

    def to_dict(self) -> dict[str, Any]:
        return {"parameter": self.parameter, "access": self.access, "ownership": self.ownership, "destructor": self.destructor, "nullable": self.nullable}


@dataclass(frozen=True)
class ExternParameter:
    name: str
    type_name: str
    pointer: RawPointerType | None = None
    policy: ForeignPointerPolicy | None = None

    def __post_init__(self) -> None:
        if self.pointer is not None and self.policy is None:
            raise FFICompileError("ForeignOwnershipUndeclared", self.name)
        if self.pointer is None and self.policy is not None:
            raise FFICompileError("ForeignPointerPolicyNonPointer", self.name)
        if self.pointer is not None and self.policy is not None and self.policy.access in {"write", "store"} and not self.pointer.mutable:
            raise FFICompileError("ForeignPointerWriteRequiresMutable", self.name)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type_name, "pointer": self.pointer.to_dict() if self.pointer else None, "policy": self.policy.to_dict() if self.policy else None}


@dataclass(frozen=True)
class ExternFunction:
    name: str
    parameters: tuple[ExternParameter, ...]
    return_type: str = "Unit"
    effects: tuple[str, ...] = ()
    error_type: str | None = None
    abi: str = FFI_ABI
    safe_wrapper: bool = False
    source: tuple[str, int] | None = None
    def __post_init__(self) -> None:
        if self.abi != FFI_ABI:
            raise FFICompileError("UnsupportedCABI", self.abi)
        _validate_abi_type(self.return_type, allow_result=True)
        if self.error_type is not None and not self.error_type:
            raise FFICompileError("ForeignErrorTypeMissing", self.name)
        if tuple(sorted(set(self.effects))) != self.effects:
            raise FFICompileError("NondeterministicForeignEffects", self.name)

    @property
    def prototype(self) -> str:
        def parameter_type(item: ExternParameter) -> str:
            pointer = item.pointer
            if pointer is not None and item.policy is not None and item.policy.access == "read":
                pointer = RawPointerType(pointer.pointee, mutable=False, nullable=pointer.nullable)
            return _c_abi_type(item.type_name, pointer)

        parameters = ", ".join(parameter_type(item) + " " + item.name for item in self.parameters) or "void"
        return f"extern {_c_abi_type(self.return_type)} {self.name}({parameters});"

    def call(self, arguments: Iterable[str]) -> str:
        return f"{self.name}({', '.join(arguments)})"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "abi": self.abi, "parameters": [item.to_dict() for item in self.parameters], "return_type": self.return_type, "effects": list(self.effects), "error_type": self.error_type, "safe_wrapper": self.safe_wrapper, "source": list(self.source) if self.source else None, "prototype": self.prototype}


@dataclass(frozen=True)
class ReprCField:
    name: str
    type_name: str
    offset: int
    size: int
    alignment: int

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class ReprCRecord:
    name: str
    fields: tuple[ReprCField, ...]
    size: int
    alignment: int
    abi: str = FFI_ABI

    def __post_init__(self) -> None:
        if self.abi != FFI_ABI:
            raise FFICompileError("UnsupportedCABI", self.abi)
        if self.size < 0 or self.alignment <= 0:
            raise FFICompileError("InvalidCLayout", self.name)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "abi": self.abi, "fields": [item.to_dict() for item in self.fields], "size": self.size, "alignment": self.alignment}


@dataclass(frozen=True)
class UnsafeOperation:
    operation: str
    source_line: int
    in_unsafe_block: bool
    propagates: bool = False

    def __post_init__(self) -> None:
        if self.operation not in _UNSAFE_NAMES:
            raise FFICompileError("UnknownUnsafeOperation", self.operation)
        if not self.in_unsafe_block:
            raise FFICompileError("UnsafeOperationRequiresBlock", self.operation)

    def to_dict(self) -> dict[str, Any]:
        return {"operation": self.operation, "source_line": self.source_line, "in_unsafe_block": self.in_unsafe_block, "propagates": self.propagates}

@dataclass(frozen=True)
class FFIProgram:
    extern_functions: tuple[ExternFunction, ...] = ()
    repr_c_records: tuple[ReprCRecord, ...] = ()
    unsafe_operations: tuple[UnsafeOperation, ...] = ()

    def __post_init__(self) -> None:
        names = [item.name for item in self.extern_functions]
        records = [item.name for item in self.repr_c_records]
        if len(names) != len(set(names)):
            raise FFICompileError("DuplicateExternFunction", names[0])
        if len(records) != len(set(records)):
            raise FFICompileError("DuplicateReprCRecord", records[0])

    def to_dict(self) -> dict[str, Any]:
        return {"abi": FFI_ABI, "extern_functions": [item.to_dict() for item in self.extern_functions], "repr_c_records": [item.to_dict() for item in self.repr_c_records], "unsafe_operations": [item.to_dict() for item in self.unsafe_operations]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FFIProgram":
        """Validate and restore the typed FFI artifact without source parsing."""
        _ffi_keys(
            value,
            {"abi", "extern_functions", "repr_c_records", "unsafe_operations"},
            "FFI program",
        )
        if value["abi"] != FFI_ABI:
            raise FFICompileError("UnsupportedCABI", str(value["abi"]))
        extern_values = _ffi_list(value["extern_functions"], "extern_functions")
        record_values = _ffi_list(value["repr_c_records"], "repr_c_records")
        unsafe_values = _ffi_list(value["unsafe_operations"], "unsafe_operations")
        program = cls(
            tuple(_extern_from_dict(item) for item in extern_values),
            tuple(_record_from_dict(item) for item in record_values),
            tuple(_unsafe_from_dict(item) for item in unsafe_values),
        )
        if program.to_dict() != dict(value):
            raise FFICompileError("NonCanonicalFFIArtifact")
        return program

    @classmethod
    def from_json(cls, payload: str) -> "FFIProgram":
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise FFICompileError("InvalidFFIArtifactJSON") from exc
        if not isinstance(value, Mapping):
            raise FFICompileError("InvalidFFIArtifactRoot")
        program = cls.from_dict(value)
        if program.to_json() != payload:
            raise FFICompileError("NonCanonicalFFIArtifactJSON")
        return program


def _ffi_keys(value: object, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise FFICompileError("InvalidFFIArtifact", label)
    if set(value) != expected:
        raise FFICompileError("InvalidFFIArtifactKeys", label)
    return value


def _ffi_list(value: object, label: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise FFICompileError("InvalidFFIArtifactList", label)
    return [
        _ffi_keys(item, set(item) if isinstance(item, Mapping) else set(), label)
        for item in value
    ]


def _pointer_from_dict(value: object) -> RawPointerType | None:
    if value is None:
        return None
    raw = _ffi_keys(value, {"type", "pointee", "mutable", "nullable"}, "pointer")
    if not isinstance(raw["pointee"], str):
        raise FFICompileError("InvalidFFIArtifact", "pointer pointee")
    pointer = RawPointerType(
        raw["pointee"],
        _ffi_bool(raw["mutable"], "pointer mutable"),
        _ffi_bool(raw["nullable"], "pointer nullable"),
    )
    if raw["type"] != pointer.type_name:
        raise FFICompileError("InvalidFFIArtifact", "pointer type")
    return pointer


def _policy_from_dict(value: object) -> ForeignPointerPolicy | None:
    if value is None:
        return None
    raw = _ffi_keys(
        value,
        {"parameter", "access", "ownership", "destructor", "nullable"},
        "pointer policy",
    )
    for name in ("parameter", "access", "ownership"):
        if not isinstance(raw[name], str):
            raise FFICompileError("InvalidFFIArtifact", f"policy {name}")
    destructor = raw["destructor"]
    if destructor is not None and not isinstance(destructor, str):
        raise FFICompileError("InvalidFFIArtifact", "policy destructor")
    return ForeignPointerPolicy(
        raw["parameter"],
        raw["access"],
        raw["ownership"],
        destructor,
        _ffi_bool(raw["nullable"], "policy nullable"),
    )


def _parameter_from_dict(value: object) -> ExternParameter:
    raw = _ffi_keys(value, {"name", "type", "pointer", "policy"}, "parameter")
    if not isinstance(raw["name"], str) or not isinstance(raw["type"], str):
        raise FFICompileError("InvalidFFIArtifact", "parameter identity")
    return ExternParameter(
        raw["name"],
        raw["type"],
        _pointer_from_dict(raw["pointer"]),
        _policy_from_dict(raw["policy"]),
    )


def _extern_from_dict(value: object) -> ExternFunction:
    raw = _ffi_keys(
        value,
        {
            "name", "abi", "parameters", "return_type", "effects", "error_type",
            "safe_wrapper", "source", "prototype",
        },
        "extern function",
    )
    for name in ("name", "abi", "return_type", "prototype"):
        if not isinstance(raw[name], str):
            raise FFICompileError("InvalidFFIArtifact", f"extern {name}")
    parameters = _ffi_list(raw["parameters"], "parameters")
    effects = raw["effects"]
    if not isinstance(effects, list) or not all(
        isinstance(item, str) for item in effects
    ):
        raise FFICompileError("InvalidFFIArtifact", "extern effects")
    error_type = raw["error_type"]
    if error_type is not None and not isinstance(error_type, str):
        raise FFICompileError("InvalidFFIArtifact", "extern error_type")
    source = raw["source"]
    if source is not None and not (
        isinstance(source, list)
        and len(source) == 2
        and isinstance(source[0], str)
        and isinstance(source[1], int)
        and not isinstance(source[1], bool)
    ):
        raise FFICompileError("InvalidFFIArtifact", "extern source")
    result = ExternFunction(
        raw["name"],
        tuple(_parameter_from_dict(item) for item in parameters),
        raw["return_type"],
        tuple(effects),
        error_type,
        raw["abi"],
        _ffi_bool(raw["safe_wrapper"], "extern safe_wrapper"),
        tuple(source) if source is not None else None,
    )
    if raw["prototype"] != result.prototype:
        raise FFICompileError("InvalidFFIArtifact", "extern prototype")
    return result


def _field_from_dict(value: object) -> ReprCField:
    raw = _ffi_keys(
        value,
        {"name", "type_name", "offset", "size", "alignment"},
        "repr(C) field",
    )
    if not isinstance(raw["name"], str) or not isinstance(raw["type_name"], str):
        raise FFICompileError("InvalidFFIArtifact", "repr(C) field identity")
    return ReprCField(
        raw["name"],
        raw["type_name"],
        _ffi_int(raw["offset"], "field offset"),
        _ffi_int(raw["size"], "field size"),
        _ffi_int(raw["alignment"], "field alignment"),
    )


def _record_from_dict(value: object) -> ReprCRecord:
    raw = _ffi_keys(
        value,
        {"name", "abi", "fields", "size", "alignment"},
        "repr(C) record",
    )
    if not isinstance(raw["name"], str) or not isinstance(raw["abi"], str):
        raise FFICompileError("InvalidFFIArtifact", "repr(C) record identity")
    fields = _ffi_list(raw["fields"], "repr(C) fields")
    return ReprCRecord(
        raw["name"],
        tuple(_field_from_dict(item) for item in fields),
        _ffi_int(raw["size"], "record size"),
        _ffi_int(raw["alignment"], "record alignment"),
        raw["abi"],
    )


def _unsafe_from_dict(value: object) -> UnsafeOperation:
    raw = _ffi_keys(
        value,
        {"operation", "source_line", "in_unsafe_block", "propagates"},
        "unsafe operation",
    )
    if not isinstance(raw["operation"], str):
        raise FFICompileError("InvalidFFIArtifact", "unsafe operation name")
    return UnsafeOperation(
        raw["operation"],
        _ffi_int(raw["source_line"], "unsafe source line"),
        _ffi_bool(raw["in_unsafe_block"], "unsafe block marker"),
        _ffi_bool(raw["propagates"], "unsafe propagation marker"),
    )


def _ffi_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise FFICompileError("InvalidFFIArtifact", label)
    return value


def _ffi_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise FFICompileError("InvalidFFIArtifact", label)
    return value

_PARAM_RE = re.compile(r"^(?P<name>[A-Za-z_]\w*)\s*:\s*(?P<type>[^({]+?)(?:\s*(?:\{|\()\s*(?P<meta>[^)}]+?)\s*[})])?\s*$")

_EXTERN_RE = re.compile(r'^extern\s*(?:"(?P<quote>[^"]+)"|(?P<bare>C))?\s*(?:fn\s+)?(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^)]*)\)\s*(?:->\s*(?P<return>[^\s]+))?\s*(?:effects?\s*[:=]?\s*\[(?P<effects>[^]]*)\])?\s*$')

def _align(value: int, alignment: int) -> int:
    return value if value % alignment == 0 else value + alignment - value % alignment

def _type_parts(type_name: str) -> tuple[str, str | None]:
    try:
        parsed = parse_type(type_name.strip())
    except ValueError:
        return (type_name.strip(), None)
    if not parsed.args:
        return parsed.name, None
    return parsed.name, ",".join(item.canonical for item in parsed.args)


def pointer_type(type_name: str) -> RawPointerType | None:
    base, argument = _type_parts(type_name)
    if base not in _POINTER_BASES or not argument:
        return None
    return RawPointerType(argument.strip(), mutable=base in {"RawPointer", "Ptr", "MutPointer"})


def _validate_abi_type(type_name: str, *, allow_result: bool = False) -> None:
    type_name = type_name.strip()
    pointer = pointer_type(type_name)
    if pointer is not None:
        _validate_abi_type(pointer.pointee)
        return
    result_parts = generic_parts(type_name, "Result", arity=2)
    if result_parts is not None and allow_result:
        _validate_abi_type(result_parts[0])
        if not result_parts[1]:
            raise FFICompileError("ForeignErrorTypeMissing", type_name)
        return
    base, _ = _type_parts(type_name)
    if type_name not in FIXED_WIDTH_TYPES and base not in {"Unit", "void"}:
        raise FFICompileError("FixedWidthABIRequired", type_name)


def _split_parameters(text: str) -> tuple[str, ...]:
    parts: list[str] = []
    start = 0
    square = curly = paren = 0
    for index, character in enumerate(text):
        if character == "[":
            square += 1
        elif character == "]":
            square -= 1
        elif character == "{":
            curly += 1
        elif character == "}":
            curly -= 1
        elif character == "(":
            paren += 1
        elif character == ")":
            paren -= 1
        elif character == "," and square == curly == paren == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return tuple(item for item in parts if item)


def _abi_size_alignment(type_name: str) -> tuple[int, int]:
    if pointer_type(type_name) is not None:
        return 8, 8
    scalar = {"Unit": (0, 1), "void": (0, 1), "Bool": (1, 1), "Byte": (1, 1), "Int8": (1, 1), "UInt8": (1, 1), "Int16": (2, 2), "UInt16": (2, 2), "Int32": (4, 4), "UInt32": (4, 4), "Int64": (8, 8), "UInt64": (8, 8), "Float32": (4, 4), "Float64": (8, 8)}
    if type_name in scalar:
        return scalar[type_name]
    raise FFICompileError("FixedWidthABIRequired", type_name)


def _c_abi_type(type_name: str, pointer: RawPointerType | None = None) -> str:
    if pointer is not None:
        pointee = _c_abi_type(pointer.pointee)
        return ("" if pointer.mutable else "const ") + pointee + " *"
    aliases = {"Unit": "void", "void": "void", "Bool": "bool", "Byte": "uint8_t", "Int8": "int8_t", "UInt8": "uint8_t", "Int16": "int16_t", "UInt16": "uint16_t", "Int32": "int32_t", "UInt32": "uint32_t", "Int64": "int64_t", "UInt64": "uint64_t", "Float32": "float", "Float64": "double"}
    return aliases.get(type_name, "Merlo_" + re.sub(r"[^A-Za-z0-9_]", "_", type_name))


def _parse_pointer_policy(name: str, type_name: str, metadata: str | None) -> tuple[RawPointerType | None, ForeignPointerPolicy | None]:
    pointer = pointer_type(type_name)
    if pointer is None:
        return None, None
    tokens = [item.strip().lower() for item in re.split(r"[,\s]+", metadata or "") if item.strip()]
    access = next((item for item in tokens if item in {"read", "write", "store"}), "read")
    ownership = next((item for item in tokens if item in {"borrowed", "owned"}), None)
    destructor = next((item.split("=", 1)[1] for item in tokens if item.startswith("destructor=") and "=" in item), None)
    if ownership is None:
        raise FFICompileError("ForeignOwnershipUndeclared", name)
    return pointer, ForeignPointerPolicy(name, access, ownership, destructor)


def _parse_params(text: str, line_no: int) -> tuple[ExternParameter, ...]:
    if not text.strip():
        return ()
    result = []
    for raw in _split_parameters(text):
        match = _PARAM_RE.match(raw.strip())
        if match is None:
            raise FFICompileError("InvalidExternParameter", f"line {line_no}: {raw.strip()}")
        name, type_name, metadata = match.group("name"), match.group("type").strip(), match.group("meta")
        pointer, policy = _parse_pointer_policy(name, type_name, metadata)
        _validate_abi_type(type_name)
        result.append(ExternParameter(name, type_name, pointer, policy))
    return tuple(result)


def _parse_extern_line(line: str, line_no: int) -> ExternFunction | None:
    match = _EXTERN_RE.match(line.strip())
    if match is None:
        return None
    abi = match.group("quote") or match.group("bare") or "C"
    if abi != "C":
        raise FFICompileError("UnsupportedCABI", abi)
    effects = tuple(sorted(item.strip() for item in (match.group("effects") or "").split(",") if item.strip()))
    return ExternFunction(match.group("name"), _parse_params(match.group("params"), line_no), (match.group("return") or "Unit").strip(), effects, source=("<source>", line_no))


def _repr_record(lines: list[tuple[int, str]], start: int, name: str) -> tuple[ReprCRecord, int]:
    fields: list[ReprCField] = []
    cursor = start
    while cursor < len(lines):
        line_no, line = lines[cursor]
        if not line.strip():
            cursor += 1
            continue
        if len(line) - len(line.lstrip()) == 0:
            break
        match = re.match(r"^\s*(?P<name>[A-Za-z_]\w*)\s*:\s*(?P<type>[^\s#]+)", line)
        if match is None:
            raise FFICompileError("InvalidReprCField", f"line {line_no}")
        type_name = match.group("type").strip()
        _validate_abi_type(type_name)
        size, alignment = _abi_size_alignment(type_name)
        offset = _align(fields[-1].offset + fields[-1].size, alignment) if fields else 0
        fields.append(ReprCField(match.group("name"), type_name, offset, size, alignment))
        cursor += 1
    alignment = max((item.alignment for item in fields), default=1)
    size = _align((fields[-1].offset + fields[-1].size) if fields else 0, alignment)
    return ReprCRecord(name, tuple(fields), size, alignment), cursor


def parse_ffi_declarations(source: str, *, path: str = "main.mlo") -> FFIProgram:
    """Parse extern C/repr(C) declarations embedded in concise source."""
    lines = [(index, line) for index, line in enumerate(source.splitlines(), 1)]
    externs: list[ExternFunction] = []
    records: list[ReprCRecord] = []
    in_extern_block = False
    for position, (index, line) in enumerate(lines):
        stripped = line.strip()
        if re.match(r'^extern\s*(?:"C"|C)\s*[:{]?\s*$', stripped):
            in_extern_block = True
            continue
        if in_extern_block and stripped in {"}", "};"}:
            in_extern_block = False
            continue
        candidate = stripped[:-1].strip() if in_extern_block and stripped.endswith(";") else stripped
        if in_extern_block:
            candidate = "extern C " + candidate if not candidate.startswith("extern") else candidate
        declaration = _parse_extern_line(candidate, index)
        if declaration is not None:
            externs.append(ExternFunction(declaration.name, declaration.parameters, declaration.return_type, declaration.effects, declaration.error_type, declaration.abi, declaration.safe_wrapper, (path, index)))
            continue
        repr_match = re.match(r"^(?:@repr\(C\)\s*)?repr\(C\)\s+record\s+([A-Za-z_]\w*)\s*:\s*$", stripped)
        if repr_match is None:
            repr_match = re.match(r"^@repr\(C\)\s+record\s+([A-Za-z_]\w*)\s*:\s*$", stripped)
        if repr_match:
            record, _ = _repr_record(lines, position + 1, repr_match.group(1))
            records.append(record)
    operations = validate_unsafe_source(source)
    return FFIProgram(tuple(externs), tuple(records), operations)


def validate_unsafe_source(source: str) -> tuple[UnsafeOperation, ...]:
    """Reject raw pointer/memory operations outside explicit ``unsafe:`` blocks."""
    operations: list[UnsafeOperation] = []
    unsafe_indents: list[int] = []
    extern_block = False
    repr_indent: int | None = None
    for line_no, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if repr_indent is not None:
            if stripped and indent <= repr_indent:
                repr_indent = None
            else:
                continue
        if re.match(r"^(?:@?repr\(C\)\s+record)\b", stripped):
            repr_indent = indent
            continue
        if re.match(r'^extern\s*(?:"C"|C)\s*[{:]?\s*$', stripped):
            extern_block = True
            continue
        if extern_block:
            if stripped in {"}", "};"}:
                extern_block = False
            continue
        if stripped.startswith("extern ") or stripped.startswith("extern\""):
            continue
        indent = len(line) - len(line.lstrip())
        while unsafe_indents and indent <= unsafe_indents[-1] and stripped != "unsafe:":
            unsafe_indents.pop()
        if stripped == "unsafe:":
            unsafe_indents.append(indent)
            continue
        names = [name for name in _UNSAFE_NAMES if re.search(rf"\b{re.escape(name)}\s*\(", stripped)]
        if re.search(r"\*\s*[A-Za-z_]\w*", stripped) and not re.search(r"\w+\s*\*\s*\w+", stripped):
            names.append("ptr_read")
        for name in sorted(set(names)):
            if not unsafe_indents:
                raise FFICompileError("UnsafeOperationRequiresBlock", f"{name} at line {line_no}")
            operations.append(UnsafeOperation(name, line_no, True, propagates=False))
    return tuple(operations)


def size_of(type_name: str, *, records: Iterable[ReprCRecord] = ()) -> int:
    """Return the fixed C ABI size for a scalar, raw pointer, or repr(C) record."""
    record_map = {item.name: item for item in records}
    if type_name in record_map:
        return record_map[type_name].size
    return _abi_size_alignment(type_name)[0]


def align_of(type_name: str, *, records: Iterable[ReprCRecord] = ()) -> int:
    """Return the fixed C ABI alignment for a scalar, raw pointer, or repr(C) record."""
    record_map = {item.name: item for item in records}
    if type_name in record_map:
        return record_map[type_name].alignment
    return _abi_size_alignment(type_name)[1]


def validate_ffi(source: str, *, path: str = "main.mlo") -> FFIProgram:
    """Parse declarations and apply every fixed ABI/ownership/unsafe check."""
    return parse_ffi_declarations(source, path=path)


__all__ = ["ExternFunction", "ExternParameter", "FFICompileError", "FFIProgram", "ForeignPointerPolicy", "RawPointerType", "ReprCField", "ReprCRecord", "UnsafeOperation", "align_of", "parse_ffi_declarations", "pointer_type", "size_of", "validate_ffi", "validate_unsafe_source"]
