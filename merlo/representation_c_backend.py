"""C11 backend for the General Representation Core.

Domain control flow comes exclusively from Structured HIR source. This emitter
contains only generic syntax lowering, type-directed layouts/moves/drops, Map,
Vec, Box, Bytes/Text primitives, and the permitted host I/O shim.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ffi import pointer_type, validate_ffi
from .representation_ir import RepresentationProgram, TypeDescriptor
from .representation_mir import GeneralPerformanceMIR
from .structured_hir_v2 import (
    HIRFunction,
    StructuredHIRProgram,
    _preprocess,
    _preprocess_ffi_surface,
)
from .version import VERSIONS
from .type_parser import generic_parts, parse_type


C_BACKEND_SCHEMA_VERSION = 1
C_BACKEND_CONTRACT = "merlo.general-representation-c11.v1"
RUNTIME_ABI_VERSION = VERSIONS.runtime_abi
RUNTIME_ABI_CONTRACT = "merlo.runtime-abi.v1"
_FROZEN_GENERAL_JSON_SHA256 = (
    "0b696f9a6653ea5fa20124d239db37fe6853ff798abe0cbcdcb703dd9c66ff04"
)


class RepresentationCBackendError(ValueError):
    pass

@dataclass(frozen=True)
class GeneratedC:
    source: str
    source_sha256: str
    primitive_manifest: tuple[dict[str, Any], ...]
    generated_lines: int
    host_lines: int
    domain_opaque_calls: tuple[str, ...]
    ffi_metadata: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": C_BACKEND_SCHEMA_VERSION,
            "contract": C_BACKEND_CONTRACT,
            "source_sha256": self.source_sha256,
            "generated_lines": self.generated_lines,
            "host_lines": self.host_lines,
            "domain_opaque_calls": list(self.domain_opaque_calls),
            "primitive_manifest": list(self.primitive_manifest),
            "ffi_metadata": list(self.ffi_metadata),
        }


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

class GeneralCEmitter:
    def __init__(
        self,
        hir: StructuredHIRProgram,
        representation: RepresentationProgram,
        mir: GeneralPerformanceMIR,
    ) -> None:
        if representation.source_hir_digest != hir.digest:
            raise RepresentationCBackendError("RIR/HIR predecessor mismatch")
        if mir.representation_ir_digest != representation.digest:
            raise RepresentationCBackendError("MIR/RIR predecessor mismatch")
        self.hir = hir
        self.representation = representation
        self.mir = mir
        self.descriptors = {item.name: item for item in representation.descriptors}
        self.functions = {item.name: item for item in hir.functions}
        self.used_effects = frozenset(
            effect for function in hir.functions for effect in function.effects
        )
        self.ffi_program = validate_ffi(hir.source, path=hir.path)
        self.extern_functions = {
            item.name: item
            for item in self.ffi_program.extern_functions
        }
        self.preprocessed = _preprocess(
            _preprocess_ffi_surface(hir.source)
        )
        self.module = (
            copy.deepcopy(hir.native_module)
            if hir.native_module is not None
            else ast.parse(
                self.preprocessed.source,
                filename=hir.path,
            )
        )
        self.function_nodes = {
            item.name: item for item in self.module.body if isinstance(item, ast.FunctionDef)
        }
        self.current_function: HIRFunction | None = None
        self.env_types: dict[str, str] = {}
        self.pointer_values: set[str] = set()
        self.owned_locals: dict[str, str] = {}
        self.pending_expression_lines: list[str] = []
        self.pending_expression_drops: list[str] = []
        self.temporary_declarations: list[tuple[str, str]] = []
        self.temporary_ordinal = 0
        self.return_ordinal = 0
        self.loop_ordinal = 0
        self.loop_exit_labels: list[str] = []
        self.match_depth = 0
        self.expression_context = "statement"
        self.returning_borrowed = False
        self.assigning_borrowed = False
        self.frozen_general_json = (
            hashlib.sha256(hir.source.encode()).hexdigest()
            == _FROZEN_GENERAL_JSON_SHA256
        )
        self.indent = 0

    def emit(self) -> GeneratedC:
        sections = [
            self._headers(),
            self._primitive_types(),
            self._forward_declarations(),
            self._vec_box_types(),
            self._nominal_types(),
            self._function_prototypes(),
            self._primitive_runtime(),
            self._effect_runtime(),
            self._file_runtime(),
            self._move_drop_glue(),
            self._constructors(),
            self._vec_box_runtime(),
            self._map_runtime(),
            self._functions(),
            self._host(),
        ]
        source = "\n\n".join(section.rstrip() for section in sections if section.strip()) + "\n"
        forbidden = tuple(
            name
            for name in ("json_token_checksum", "json_tokenize", "json_parse", "json_decode", "json_build_ast", "json_pretty_print")
            if re.search(rf"\b{name}\s*\(", source)
        )
        if forbidden:
            raise RepresentationCBackendError(f"domain opaque calls emitted: {forbidden}")
        host_lines = len(self._host().splitlines())
        manifest = tuple(self._primitive_manifest(source))
        return GeneratedC(
            source,
            hashlib.sha256(source.encode()).hexdigest(),
            manifest,
            len(source.splitlines()),
            host_lines,
            forbidden,
            tuple(item.to_dict() for item in self.ffi_program.extern_functions),
        )

    def _headers(self) -> str:
        platform_headers = ""
        if self.used_effects & {"network.tcp", "network.http"}:
            platform_headers += "#include <sys/socket.h>\n#include <netdb.h>\n#include <unistd.h>\n"
        if "random.read" in self.used_effects:
            platform_headers += "#include <sys/random.h>\n"
        entry = self.functions[self.hir.entry_function]
        metric_aliases = ""
        if tuple(parameter.type_name for parameter in entry.parameters) == ("Path",):
            metric_names = (
                "merlo_allocations",
                "merlo_frees",
                "merlo_text_allocations",
                "merlo_text_frees",
                "merlo_vec_allocations",
                "merlo_vec_frees",
                "merlo_vec_reallocations",
                "merlo_vec_growths",
                "merlo_vec_initialized",
                "merlo_vec_elements_dropped",
                "merlo_box_allocations",
                "merlo_box_frees",
                "merlo_ast_nodes_allocated",
                "merlo_ast_nodes_freed",
                "merlo_bytes_copied",
                "merlo_drop_calls",
                "merlo_map_allocations",
                "merlo_map_frees",
                "merlo_map_growths",
                "merlo_map_collisions",
                "merlo_map_updates",
                "merlo_map_owned_keys_allocated",
                "merlo_map_owned_keys_dropped",
                "merlo_map_lookup_key_copies",
            )
            metric_aliases = "\n".join(
                f"#define {name} ((uint64_t){{0}})" for name in metric_names
            )
        return """#define _GNU_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/types.h>
#include <string.h>
#include <math.h>
#include <time.h>
""" + platform_headers + """typedef struct {
    uint32_t effects;
    const char *filesystem_root;
    const char *network_host;
    const char *environment_keys;
} MerloCapabilityManifest;
enum {
    MERLO_EFFECT_CONSOLE_READ = 1u << 0,
    MERLO_EFFECT_CONSOLE_WRITE = 1u << 1,
    MERLO_EFFECT_FS_READ = 1u << 2,
    MERLO_EFFECT_FS_WRITE = 1u << 3,
    MERLO_EFFECT_ENV_READ = 1u << 4,
    MERLO_EFFECT_CLOCK_NOW = 1u << 5,
    MERLO_EFFECT_RANDOM_READ = 1u << 6,
    MERLO_EFFECT_NETWORK_TCP = 1u << 7,
    MERLO_EFFECT_NETWORK_HTTP = 1u << 8,
    MERLO_EFFECT_PROCESS_ARGS = 1u << 9
};
static bool merlo_allowlist_contains(const char *items, const char *value) {
    if (items == NULL || value == NULL || *value == '\\0') return false;
    size_t value_length = strlen(value);
    const char *cursor = items;
    while (*cursor != '\\0') {
        while (*cursor == ',' || *cursor == ' ') ++cursor;
        const char *end = strchr(cursor, ',');
        size_t length = end == NULL ? strlen(cursor) : (size_t)(end - cursor);
        while (length > 0 && cursor[length - 1] == ' ') --length;
        if (length == value_length && memcmp(cursor, value, length) == 0) return true;
        if (end == NULL) break;
        cursor = end + 1;
    }
    return false;
}
static MerloCapabilityManifest merlo_capabilities = { 0u, NULL, NULL, NULL };
static void merlo_require_capability(uint32_t effect) {
    if ((merlo_capabilities.effects & effect) == 0u) {
        fputs("MissingCapability\\n", stderr);
        abort();
    }
}

static uint64_t merlo_allocations = 0;
static uint64_t merlo_frees = 0;
static uint64_t merlo_text_allocations = 0;
static uint64_t merlo_text_frees = 0;
static uint64_t merlo_vec_allocations = 0;
static uint64_t merlo_vec_frees = 0;
static uint64_t merlo_vec_reallocations = 0;
static uint64_t merlo_vec_growths = 0;
static uint64_t merlo_vec_initialized = 0;
static uint64_t merlo_vec_elements_dropped = 0;
static uint64_t merlo_box_allocations = 0;
static uint64_t merlo_box_frees = 0;
static uint64_t merlo_ast_nodes_allocated = 0;
static uint64_t merlo_ast_nodes_freed = 0;
static uint64_t merlo_bytes_copied = 0;
static uint64_t merlo_drop_calls = 0;
static uint64_t merlo_map_allocations = 0;
static uint64_t merlo_map_frees = 0;
static uint64_t merlo_map_growths = 0;
static uint64_t merlo_map_collisions = 0;
static uint64_t merlo_map_updates = 0;
static uint64_t merlo_map_owned_keys_allocated = 0;
static uint64_t merlo_map_owned_keys_dropped = 0;
static uint64_t merlo_map_lookup_key_copies = 0;
""" + metric_aliases + """
static void merlo_overflow_trap(const char *message) {
    fprintf(stderr, "MerloOverflow:%s\\n", message);
    abort();
}
static uint8_t merlo_checked_byte_add(uint8_t left, uint8_t right) {
    if (left > UINT8_MAX - right) merlo_overflow_trap("ByteAdd");
    return (uint8_t)(left + right);
}

static uint8_t merlo_checked_byte_sub(uint8_t left, uint8_t right) {
    if (left < right) merlo_overflow_trap("ByteSub");
    return (uint8_t)(left - right);
}

static uint8_t merlo_checked_byte_mult(uint8_t left, uint8_t right) {
    if (left != 0 && right > UINT8_MAX / left) merlo_overflow_trap("ByteMult");
    return (uint8_t)(left * right);
}


static uint64_t merlo_checked_uint64_add(uint64_t left, uint64_t right) {
    if (left > UINT64_MAX - right) merlo_overflow_trap("UInt64Add");
    return left + right;
}

static uint64_t merlo_checked_uint64_sub(uint64_t left, uint64_t right) {
    if (left < right) merlo_overflow_trap("UInt64Sub");
    return left - right;
}

static uint64_t merlo_checked_uint64_mult(uint64_t left, uint64_t right) {
    if (left != 0 && right > UINT64_MAX / left) merlo_overflow_trap("UInt64Mult");
    return left * right;
}

static int64_t merlo_checked_int64_add(int64_t left, int64_t right) {
    if ((right > 0 && left > INT64_MAX - right) ||
        (right < 0 && left < INT64_MIN - right)) {
        merlo_overflow_trap("Int64Add");
    }
    return left + right;
}

static int64_t merlo_checked_int64_sub(int64_t left, int64_t right) {
    if ((right < 0 && left > INT64_MAX + right) ||
        (right > 0 && left < INT64_MIN + right)) {
        merlo_overflow_trap("Int64Sub");
    }
    return left - right;
}

static int64_t merlo_checked_int64_mult(int64_t left, int64_t right) {
    if (left != 0 && (
        (left == -1 && right == INT64_MIN) ||
        (right == -1 && left == INT64_MIN) ||
        (left > 0 && right > 0 && left > INT64_MAX / right) ||
        (left > 0 && right < 0 && right < INT64_MIN / left) ||
        (left < 0 && right > 0 && left < INT64_MIN / right) ||
        (left < 0 && right < 0 && left < INT64_MAX / right)
    )) {
        merlo_overflow_trap("Int64Mult");
    }
    return left * right;
}

static uint8_t merlo_cast_byte(uint64_t value) {
    if (value > UINT8_MAX) merlo_overflow_trap("ByteCast");
    return (uint8_t)value;
}

static int64_t merlo_cast_int64(uint64_t value) {
    if (value > (uint64_t)INT64_MAX) merlo_overflow_trap("Int64Cast");
    return (int64_t)value;
}

static uint64_t merlo_cast_uint64(int64_t value) {
    if (value < 0) merlo_overflow_trap("UInt64Cast");
    return (uint64_t)value;
}
static uint8_t merlo_cast_byte_from_float64(double value) {
    if (!isfinite(value) || value < 0.0 || value > UINT8_MAX) {
        merlo_overflow_trap("ByteCast");
    }
    return (uint8_t)value;
}

static int64_t merlo_cast_int64_from_float64(double value) {
    if (!isfinite(value) || value < -9223372036854775808.0 ||
        value >= 9223372036854775808.0) {
        merlo_overflow_trap("Int64Cast");
    }
    return (int64_t)value;
}

static uint64_t merlo_cast_uint64_from_float64(double value) {
    if (!isfinite(value) || value < 0.0 ||
        value >= 18446744073709551616.0) {
        merlo_overflow_trap("UInt64Cast");
    }
    return (uint64_t)value;
}


static void merlo_allocation_trap(void) {
    fputs("MerloAllocationFailure\\n", stderr);
    abort();
}

static void merlo_bounds_trap(uint64_t index, uint64_t length) {
    fprintf(stderr, "MerloBounds:%" PRIu64 ":%" PRIu64 "\\n", index, length);
    abort();
}

static void merlo_ownership_trap(const char *message) {
    fprintf(stderr, "MerloOwnership:%s\\n", message);
    abort();
}"""

    def _forward_declarations(self) -> str:
        lines = []
        for descriptor in self.representation.descriptors:
            if descriptor.kind == "enum" and all(payload is None for _, payload, _ in descriptor.variants):
                lines.append(f"typedef uint32_t {_c_name(descriptor.name)};")
            elif descriptor.kind in {"record", "enum"} and descriptor.name not in {"Text", "TextBuilder"}:
                lines.append(f"typedef struct {_c_name(descriptor.name)} {_c_name(descriptor.name)};")
        return "\n".join(lines)

    def _primitive_types(self) -> str:
        return """typedef struct { uint8_t *data; uint64_t length; } MerloBytes;
typedef struct { const uint8_t *data; uint64_t length; } MerloBytesView;
typedef struct { const uint8_t *data; uint64_t length; } MerloTextView;
typedef struct { uint8_t *data; uint64_t length; } MerloText;
typedef struct { uint8_t *data; uint64_t length; uint64_t capacity; } MerloTextBuilder;
typedef struct {
    FILE *stream;
    uint8_t *buffer;
    uint64_t buffer_length;
    uint64_t buffer_capacity;
    uint64_t generation;
    uint64_t line_number;
    uint64_t byte_offset;
    bool eof;
} MerloFileReader;
typedef struct { MerloFileReader *owner; uint64_t generation; } MerloFileLines;"""

    def _vec_box_types(self) -> str:
        lines = []
        for descriptor in self.representation.descriptors:
            if descriptor.kind == "vec":
                assert descriptor.element_type is not None
                lines.append(
                    f"typedef struct {{ {_c_name(descriptor.element_type)} *data; uint64_t length; uint64_t capacity; uint64_t active_views; }} {_c_name(descriptor.name)};"
                )
                lines.append(
                    f"typedef struct {{ {_c_name(descriptor.name)} *owner; uint64_t generation; }} {_c_name(descriptor.name)}View;"
                )
            elif descriptor.kind == "box":
                assert descriptor.payload_type is not None
                lines.append(
                    f"typedef struct {{ {_c_name(descriptor.payload_type)} *data; }} {_c_name(descriptor.name)};"
                )
            elif descriptor.kind == "map":
                assert descriptor.key_type is not None
                assert descriptor.value_type is not None
                ctype = _c_name(descriptor.name)
                key = _c_name(descriptor.key_type)
                value = _c_name(descriptor.value_type)
                lines.append(
                    f"typedef struct {{ {key} key; {value} value; uint64_t hash; }} {ctype}Entry;"
                )
                lines.append(
                    f"typedef struct {{ {ctype}Entry *entries; uint64_t *buckets; uint64_t length; uint64_t capacity; uint64_t active_views; }} {ctype};"
                )
                lines.append(
                    f"typedef struct {{ {ctype} *owner; uint64_t length; }} {ctype}EntriesView;"
                )
            elif descriptor.kind == "array":
                assert descriptor.element_type is not None
                assert descriptor.length is not None
                lines.append(
                    f"typedef struct {{ {_c_name(descriptor.element_type)} "
                    f"data[{max(1, descriptor.length)}]; }} {_c_name(descriptor.name)};"
                )
            elif descriptor.kind == "slice":
                assert descriptor.element_type is not None
                lines.append(
                    f"typedef struct {{ const {_c_name(descriptor.element_type)} *data; "
                    f"uint64_t length; }} {_c_name(descriptor.name)};"
                )
            elif descriptor.kind == "callback":
                callback = _callback_parts(descriptor.name)
                assert callback is not None
                parameter_types, return_type = callback
                parameters = ", ".join(
                    _c_name(item) for item in parameter_types
                )
                lines.append(
                    f"typedef {_c_name(return_type)} (*{_c_name(descriptor.name)})"
                    f"({parameters or 'void'});"
                )
        return "\n".join(lines)

    def _nominal_types(self) -> str:
        lines: list[str] = []
        pending = {
            item.name: item
            for item in self.representation.descriptors
            if item.kind in {"record", "enum"}
            and item.name != "TextBuilder"
        }
        emitted: set[str] = set()
        while pending:
            progress = False
            for name, descriptor in tuple(pending.items()):
                if descriptor.kind == "record":
                    dependencies = {
                        type_name
                        for _, type_name, _ in descriptor.fields
                        if type_name in pending
                    }
                else:
                    dependencies = {
                        payload
                        for _, payload, _ in descriptor.variants
                        if payload is not None and payload in pending
                    }
                if dependencies:
                    continue
                if descriptor.kind == "record":
                    fields = "\n".join(
                        f"    {_c_name(type_name)} {field_name};"
                        for field_name, type_name, _ in descriptor.fields
                    )
                    lines.append(f"struct {_c_name(name)} {{\n{fields}\n}};")
                elif all(
                    payload is None
                    for _, payload, _ in descriptor.variants
                ):
                    for variant, _, tag in descriptor.variants:
                        lines.append(
                            f"static const {_c_name(name)} "
                            f"MERLO_{_identifier(name)}_{variant} = "
                            f"UINT32_C({tag});"
                        )
                else:
                    union_lines = [
                        f"        {_c_name(payload)} {variant};"
                        for variant, payload, _ in descriptor.variants
                        if payload is not None and payload != "Unit"
                    ]
                    lines.append(
                        f"struct {_c_name(name)} {{\n"
                        "    uint32_t tag;\n"
                        "    union {\n"
                        + "\n".join(union_lines)
                        + "\n    } payload;\n};"
                    )
                    for variant, _, tag in descriptor.variants:
                        lines.append(
                            f"#define MERLO_{_identifier(name)}_{variant}_TAG "
                            f"UINT32_C({tag})"
                        )
                    lines.append(
                        f"#define MERLO_{_identifier(name)}_MOVED_TAG "
                        "UINT32_MAX"
                    )
                emitted.add(name)
                del pending[name]
                progress = True
            if not progress:
                raise RepresentationCBackendError(
                    f"inline C layout cycle: {sorted(pending)}"
                )
        return "\n".join(lines)

    def _function_prototypes(self) -> str:
        foreign = [item.prototype for item in self.ffi_program.extern_functions]
        internal = [self._function_signature(function) + ";" for function in self.hir.functions]
        return "\n".join((*foreign, *internal))

    def _parameter_is_pointer(self, parameter: Any) -> bool:
        if pointer_type(parameter.type_name) is not None:
            return True
        descriptor = self.descriptors[parameter.type_name]
        return (
            parameter.ownership == "borrow_mut"
            or parameter.ownership == "borrow"
            and descriptor.kind in {"vec", "map", "box"}
        )

    def _function_signature(self, function: HIRFunction) -> str:
        parameters = []
        for parameter in function.parameters:
            ctype = _c_name(parameter.type_name)
            pointer = (
                ""
                if ctype.endswith("*")
                else " *"
                if self._parameter_is_pointer(parameter)
                else " "
            )
            parameters.append(f"{ctype}{pointer}{parameter.name}".replace("  ", " "))
        return f"static {_c_name(function.return_type)} merlo_fn_{function.name}({', '.join(parameters) if parameters else 'void'})"

    def _primitive_runtime(self) -> str:
        return """static uint8_t merlo_bytes_load(const MerloBytesView *view, uint64_t index) {
    if (index >= view->length) merlo_bounds_trap(index, view->length);
    return view->data[index];
}

static uint8_t merlo_text_load(const MerloText *text, uint64_t index) {
    if (index >= text->length) merlo_bounds_trap(index, text->length);
    return text->data[index];
}

static MerloText merlo_text_from_bytes(const MerloBytesView *view, uint64_t start, uint64_t end) {
    if (start > end || end > view->length) merlo_bounds_trap(end, view->length);
    MerloText result = { NULL, end - start };
    if (result.length != 0) {
        result.data = (uint8_t *)malloc((size_t)result.length);
        if (result.data == NULL) merlo_allocation_trap();
        memcpy(result.data, view->data + start, (size_t)result.length);
        ++merlo_allocations;
        ++merlo_text_allocations;
        merlo_bytes_copied += result.length;
    }
    return result;
}

static MerloText merlo_text_from_view(const MerloTextView *view) {
    MerloText result = { NULL, view->length };
    if (result.length != 0) {
        result.data = (uint8_t *)malloc((size_t)result.length);
        if (result.data == NULL) merlo_allocation_trap();
        memcpy(result.data, view->data, (size_t)result.length);
        ++merlo_allocations;
        ++merlo_text_allocations;
        merlo_bytes_copied += result.length;
    }
    return result;
}
static MerloText merlo_text_clone(const MerloText *text) {
    return merlo_text_from_view((const MerloTextView *)text);
}

static MerloTextView merlo_text_view_slice_bytes(
    const MerloTextView *view, uint64_t start, uint64_t length
) {
    if (start > view->length || length > view->length - start) {
        merlo_bounds_trap(start, view->length);
    }
    const uint8_t *data = view->data;
    if (start != 0) data += start;
    MerloTextView result = { data, length };
    return result;
}

static MerloText merlo_text_from_view_slice(
    const MerloTextView *view, uint64_t start, uint64_t length
) {
    MerloTextView slice = merlo_text_view_slice_bytes(view, start, length);
    return merlo_text_from_view(&slice);
}



static MerloText merlo_text_literal(const uint8_t *data, uint64_t length) {
    MerloText result = { NULL, length };
    if (length != 0) {
        result.data = (uint8_t *)malloc((size_t)length);
        if (result.data == NULL) merlo_allocation_trap();
        memcpy(result.data, data, (size_t)length);
        ++merlo_allocations;
        ++merlo_text_allocations;
        merlo_bytes_copied += length;
    }
    return result;
}
static bool merlo_text_equal_values(MerloText left, MerloText right) {
    return left.length == right.length
        && (left.length == 0
            || memcmp(left.data, right.data, (size_t)left.length) == 0);
}

static uint8_t merlo_ascii_lower(uint8_t byte) {
    return byte >= 'A' && byte <= 'Z' ? (uint8_t)(byte + ('a' - 'A')) : byte;
}

static bool merlo_text_view_contains(
    const MerloTextView *haystack, const MerloText *needle, bool ignore_case
) {
    if (needle->length == 0) return true;
    if (needle->length > haystack->length) return false;
    for (uint64_t start = 0; start <= haystack->length - needle->length; ++start) {
        bool matched = true;
        for (uint64_t index = 0; index < needle->length; ++index) {
            uint8_t left = haystack->data[start + index];
            uint8_t right = needle->data[index];
            if (ignore_case) {
                left = merlo_ascii_lower(left);
                right = merlo_ascii_lower(right);
            }
            if (left != right) { matched = false; break; }
        }
        if (matched) return true;
    }
    return false;
}
static bool merlo_text_view_prefix_suffix(
    const MerloTextView *haystack, const MerloText *needle, bool suffix
) {
    if (needle->length > haystack->length) return false;
    uint64_t start = suffix ? haystack->length - needle->length : 0;
    for (uint64_t index = 0; index < needle->length; ++index) {
        if (haystack->data[start + index] != needle->data[index]) return false;
    }
    return true;
}

static MerloTextBuilder merlo_text_builder_new(void) {
    MerloTextBuilder result = { NULL, 0, 0 };
    return result;
}

static void merlo_text_builder_reserve(MerloTextBuilder *builder, uint64_t additional) {
    if (additional > UINT64_MAX - builder->length) merlo_overflow_trap("TextBuilderLength");
    uint64_t required = builder->length + additional;
    if (required <= builder->capacity) return;
    uint64_t doubled = builder->capacity > UINT64_MAX / 2 ? UINT64_MAX : builder->capacity * 2;
    uint64_t capacity = required > doubled ? required : doubled;
    if (capacity < 32) capacity = 32;
    if (capacity > SIZE_MAX) merlo_overflow_trap("TextBuilderCapacity");
    uint8_t *next = (uint8_t *)realloc(builder->data, (size_t)capacity);
    if (next == NULL) merlo_allocation_trap();
    if (builder->data == NULL) { ++merlo_allocations; }
    builder->data = next;
    builder->capacity = capacity;
}

static void merlo_text_builder_append_byte(MerloTextBuilder *builder, uint64_t byte) {
    if (byte > 255) merlo_bounds_trap(byte, 256);
    merlo_text_builder_reserve(builder, 1);
    builder->data[builder->length++] = (uint8_t)byte;
}

static void merlo_text_builder_append_scalar(MerloTextBuilder *builder, uint64_t scalar) {
    if (scalar > UINT64_C(0x10ffff) || (scalar >= UINT64_C(0xd800) && scalar <= UINT64_C(0xdfff))) {
        merlo_ownership_trap("InvalidUnicodeScalar");
    }
    if (scalar <= UINT64_C(0x7f)) {
        merlo_text_builder_append_byte(builder, scalar);
    } else if (scalar <= UINT64_C(0x7ff)) {
        merlo_text_builder_reserve(builder, 2);
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0xc0) | (scalar >> 6));

        builder->data[builder->length++] = (uint8_t)(UINT64_C(0x80) | (scalar & 63));
    } else if (scalar <= UINT64_C(0xffff)) {
        merlo_text_builder_reserve(builder, 3);
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0xe0) | (scalar >> 12));
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 6) & 63));
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0x80) | (scalar & 63));
    } else {
        merlo_text_builder_reserve(builder, 4);
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0xf0) | (scalar >> 18));
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 12) & 63));
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 6) & 63));
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0x80) | (scalar & 63));
    }
}
static void merlo_text_builder_append_text(MerloTextBuilder *builder, const MerloText *text) {
    merlo_text_builder_reserve(builder, text->length);
    if (text->length != 0) memcpy(builder->data + builder->length, text->data, (size_t)text->length);
    builder->length += text->length;
}

static void merlo_text_builder_append_uint64(MerloTextBuilder *builder, uint64_t value) {
    uint8_t digits[20];
    uint64_t length = 0;
    do {
        digits[length++] = (uint8_t)('0' + value % 10);
        value /= 10;
    } while (value != 0);
    merlo_text_builder_reserve(builder, length);
    while (length != 0) builder->data[builder->length++] = digits[--length];
}

static MerloText merlo_text_builder_finish(MerloTextBuilder *builder) {
    MerloText result = { builder->data, builder->length };
    if (builder->data != NULL) ++merlo_text_allocations;
    builder->data = NULL;
    builder->length = 0;
    builder->capacity = 0;
    return result;
}"""

    def _effect_runtime(self) -> str:
        sections: list[str] = []
        if "console.read" in self.used_effects:
            sections.append(r'''static MerloBytes merlo_console_read(void) {
    merlo_require_capability(MERLO_EFFECT_CONSOLE_READ);
    MerloBytes result = { NULL, 0 };
    uint8_t chunk[4096];
    size_t count = fread(chunk, 1, sizeof(chunk), stdin);
    if (ferror(stdin)) return result;
    if (count != 0) {
        result.data = (uint8_t *)malloc(count);
        if (result.data == NULL) merlo_allocation_trap();
        memcpy(result.data, chunk, count);
        result.length = (uint64_t)count;
        ++merlo_allocations;
    }
    return result;
}''')
        if "console.write" in self.used_effects:
            sections.append(r'''static void merlo_console_write(const MerloText *value) {
    merlo_require_capability(MERLO_EFFECT_CONSOLE_WRITE);
    fwrite(value->data, 1, (size_t)value->length, stdout);
}''')
        if "clock.now" in self.used_effects:
            sections.append(r'''static uint64_t merlo_clock_now(void) {
    merlo_require_capability(MERLO_EFFECT_CLOCK_NOW);
    return (uint64_t)time(NULL);
}''')
        if "random.read" in self.used_effects:
            sections.append(r'''static MerloBytes merlo_random_read(uint64_t length) {
    merlo_require_capability(MERLO_EFFECT_RANDOM_READ);
    MerloBytes result = { NULL, length };
    if (length != 0) {
        result.data = (uint8_t *)malloc((size_t)length);
        if (result.data == NULL) merlo_allocation_trap();
        ssize_t received = 0;
        while ((uint64_t)received < length) {
            ssize_t count = getrandom(
                result.data + received, (size_t)(length - (uint64_t)received), 0
            );
            if (count > 0) {
                received += count;
                continue;
            }
            if (count < 0 && errno == EINTR) continue;
            free(result.data);
            result.data = NULL;
            result.length = 0;
            return result;
        }
        ++merlo_allocations;
    }
    return result;
}''')
        if "env.read" in self.used_effects:
            sections.append(r'''static MerloText merlo_env_read(const MerloText *key) {
    merlo_require_capability(MERLO_EFFECT_ENV_READ);
    if (merlo_capabilities.environment_keys == NULL) return (MerloText){ NULL, 0 };
    char *name = (char *)malloc((size_t)key->length + 1);
    if (name == NULL) merlo_allocation_trap();
    memcpy(name, key->data, (size_t)key->length);
    name[key->length] = '\0';
    if (!merlo_allowlist_contains(merlo_capabilities.environment_keys, name)) {
        free(name);
        return (MerloText){ NULL, 0 };
    }
    const char *value = getenv(name);
    free(name);
    if (value == NULL) return (MerloText){ NULL, 0 };
    return merlo_text_literal((const uint8_t *)value, (uint64_t)strlen(value));
}''')
        if "process.args" in self.used_effects:
            sections.append(r'''static int merlo_runtime_argc = 0;
static uint64_t merlo_process_args_count(void) {
    merlo_require_capability(MERLO_EFFECT_PROCESS_ARGS);
    return merlo_runtime_argc > 0 ? (uint64_t)merlo_runtime_argc - 1u : 0u;
}''')
        network_effects = self.used_effects & {"network.tcp", "network.http"}
        if network_effects:
            sections.append(r'''static int merlo_connect_host(const char *host, uint16_t port) {
    struct addrinfo hints = {0};
    struct addrinfo *result = NULL;
    char service[8];
    snprintf(service, sizeof(service), "%u", (unsigned)port);
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_family = AF_UNSPEC;
    if (getaddrinfo(host, service, &hints, &result) != 0) return -1;
    int descriptor = -1;
    for (struct addrinfo *item = result; item != NULL; item = item->ai_next) {
        descriptor = socket(item->ai_family, item->ai_socktype, item->ai_protocol);
        if (descriptor < 0) continue;
        if (connect(descriptor, item->ai_addr, item->ai_addrlen) == 0) break;
        close(descriptor);
        descriptor = -1;
    }
    freeaddrinfo(result);
    return descriptor;
}''')
        if "network.tcp" in self.used_effects:
            sections.append(r'''static uint64_t merlo_network_tcp_guard(void) {
    merlo_require_capability(MERLO_EFFECT_NETWORK_TCP);
    if (merlo_capabilities.network_host == NULL) return 1;
    int descriptor = merlo_connect_host(merlo_capabilities.network_host, 80);
    if (descriptor < 0) return 1;
    close(descriptor);
    return 0;
}
static uint64_t merlo_network_tcp_connect(const MerloText *host, uint64_t port) {
    merlo_require_capability(MERLO_EFFECT_NETWORK_TCP);
    if (host == NULL || merlo_capabilities.network_host == NULL) return UINT64_MAX;
    char *name = (char *)malloc((size_t)host->length + 1);
    if (name == NULL) merlo_allocation_trap();
    memcpy(name, host->data, (size_t)host->length);
    name[host->length] = '\0';
    if (!merlo_allowlist_contains(merlo_capabilities.network_host, name)) {
        free(name);
        return UINT64_MAX;
    }
    int descriptor = merlo_connect_host(name, (uint16_t)port);
    free(name);
    return descriptor < 0 ? UINT64_MAX : (uint64_t)descriptor;
}
static uint64_t merlo_network_tcp_send(uint64_t handle, const MerloBytesView *data) {
    merlo_require_capability(MERLO_EFFECT_NETWORK_TCP);
    if (handle == UINT64_MAX || data == NULL) return 0;
    ssize_t sent = send((int)handle, data->data, (size_t)data->length, 0);
    return sent < 0 ? 0 : (uint64_t)sent;
}
static MerloBytes merlo_network_tcp_receive(uint64_t handle, uint64_t limit) {
    merlo_require_capability(MERLO_EFFECT_NETWORK_TCP);
    MerloBytes result = { NULL, 0 };
    if (handle == UINT64_MAX || limit == 0) return result;
    result.data = (uint8_t *)malloc((size_t)limit);
    if (result.data == NULL) merlo_allocation_trap();
    ssize_t count = recv((int)handle, result.data, (size_t)limit, 0);
    if (count <= 0) {
        free(result.data);
        result.data = NULL;
        return result;
    }
    result.length = (uint64_t)count;
    ++merlo_allocations;
    return result;
}
static uint64_t merlo_network_tcp_close(uint64_t handle) {
    merlo_require_capability(MERLO_EFFECT_NETWORK_TCP);
    if (handle == UINT64_MAX) return 1;
    return close((int)handle) == 0 ? 0 : 1;
}''')
        if "network.http" in self.used_effects:
            sections.append(r'''static uint64_t merlo_network_http_guard(void) {
    merlo_require_capability(MERLO_EFFECT_NETWORK_HTTP);
    if (merlo_capabilities.network_host == NULL) return 1;
    int descriptor = merlo_connect_host(merlo_capabilities.network_host, 80);
    if (descriptor < 0) return 1;
    static const char request[] = "GET / HTTP/1.0\r\nConnection: close\r\n\r\n";
    (void)send(descriptor, request, sizeof(request) - 1, 0);
    char buffer[1024];
    while (recv(descriptor, buffer, sizeof(buffer), 0) > 0) {}
    close(descriptor);
    return 0;
}''')
        if "fs.write" in self.used_effects:
            sections.append(r'''static bool merlo_path_allowed(const MerloText *path);
static uint64_t merlo_file_write_all(const MerloText *path, const MerloBytesView *data) {
    merlo_require_capability(MERLO_EFFECT_FS_WRITE);
    if (!merlo_path_allowed(path)) return 1;
    char *name = (char *)malloc((size_t)path->length + 1);
    if (name == NULL) merlo_allocation_trap();
    memcpy(name, path->data, (size_t)path->length);
    name[path->length] = '\0';
    FILE *stream = fopen(name, "wb");
    free(name);
    if (stream == NULL) return 1;
    size_t written = fwrite(data->data, 1, (size_t)data->length, stream);
    int close_status = fclose(stream);
    return written == (size_t)data->length && close_status == 0 ? 0 : 1;
}''')
        return "\n".join(sections)

    def _file_runtime(self) -> str:
        if (
            not self.used_effects & {"fs.read", "fs.write"}
            and "FileReader" not in self.descriptors
        ):
            return ""
        return r'''static uint32_t merlo_file_error = 0;
static uint64_t merlo_file_error_line = 0;
static bool merlo_path_allowed(const MerloText *path) {
    if (merlo_capabilities.filesystem_root == NULL) return false;
    size_t root_length = strlen(merlo_capabilities.filesystem_root);
    if (root_length == 0 || path->length < (uint64_t)root_length) return false;
    if (memcmp(path->data, merlo_capabilities.filesystem_root, root_length) != 0) return false;
    if (root_length == 1 && merlo_capabilities.filesystem_root[0] == '/') return true;
    return path->length == (uint64_t)root_length
        || path->data[root_length] == (uint8_t)'/';
}
static MerloBytes merlo_file_read_all(const MerloText *path);
static void merlo_file_close(MerloFileReader *reader) {
    if (reader->stream != NULL) {
        if (fclose(reader->stream) != 0 && merlo_file_error == 0) {
            merlo_file_error = UINT32_C(5);
        }
        reader->stream = NULL;
    }
    free(reader->buffer);
    reader->buffer = NULL;
    reader->buffer_length = 0;
    reader->buffer_capacity = 0;
    ++reader->generation;
}
static MerloFileReader merlo_file_open_write(const MerloText *path) {
    merlo_require_capability(MERLO_EFFECT_FS_WRITE);
    if (!merlo_path_allowed(path)) return (MerloFileReader){ NULL, NULL, 0, 0, 1, 0, 0, false };
    MerloFileReader result = { NULL, NULL, 0, 0, 1, 0, 0, false };
    char *name = (char *)malloc((size_t)path->length + 1);
    if (name == NULL) merlo_allocation_trap();
    memcpy(name, path->data, (size_t)path->length);
    name[path->length] = '\0';
    result.stream = fopen(name, "wb");
    free(name);
    if (result.stream == NULL) merlo_file_error = UINT32_C(1);
    return result;
}
static MerloBytes merlo_file_read_chunk(MerloFileReader *reader, uint64_t limit) {
    merlo_require_capability(MERLO_EFFECT_FS_READ);
    MerloBytes result = { NULL, 0 };
    if (reader == NULL || reader->stream == NULL || limit == 0) return result;
    result.data = (uint8_t *)malloc((size_t)limit);
    if (result.data == NULL) merlo_allocation_trap();
    size_t count = fread(result.data, 1, (size_t)limit, reader->stream);
    if (ferror(reader->stream)) {
        free(result.data);
        result.data = NULL;
        merlo_file_error = UINT32_C(2);
        return result;
    }
    result.length = (uint64_t)count;
    if (count != 0) ++merlo_allocations;
    return result;
}
static uint64_t merlo_file_write_chunk(MerloFileReader *reader, const MerloBytesView *data) {
    merlo_require_capability(MERLO_EFFECT_FS_WRITE);
    if (reader == NULL || reader->stream == NULL) return 1;
    size_t written = fwrite(data->data, 1, (size_t)data->length, reader->stream);
    return written == (size_t)data->length ? 0 : 1;
}
static MerloFileReader merlo_file_open_read(const MerloText *path) {
    merlo_require_capability(MERLO_EFFECT_FS_READ);
    if (!merlo_path_allowed(path)) {
        merlo_file_error = UINT32_C(4);
        return (MerloFileReader){ NULL, NULL, 0, 0, 1, 0, 0, false };
    }
    MerloFileReader result = { NULL, NULL, 0, 0, 1, 0, 0, false };
    char *name = (char *)malloc((size_t)path->length + 1);
    if (name == NULL) merlo_allocation_trap();
    if (path->length != 0) memcpy(name, path->data, (size_t)path->length);
    name[path->length] = '\0';
    result.stream = fopen(name, "rb");
    if (result.stream == NULL) {
        merlo_file_error = UINT32_C(1);
    }
    free(name);
    return result;
}
static MerloBytes merlo_file_read_all(const MerloText *path) {
    merlo_require_capability(MERLO_EFFECT_FS_READ);
    if (!merlo_path_allowed(path)) {
        merlo_file_error = UINT32_C(4);
        return (MerloBytes){ NULL, 0 };
    }
    MerloBytes result = { NULL, 0 };
    char *name = (char *)malloc((size_t)path->length + 1);
    if (name == NULL) merlo_allocation_trap();
    if (path->length != 0) memcpy(name, path->data, (size_t)path->length);
    name[path->length] = '\0';
    FILE *stream = fopen(name, "rb");
    free(name);
    if (stream == NULL) {
        merlo_file_error = UINT32_C(1);
        return result;
    }
    size_t capacity = 0;
    uint8_t chunk[4096];
    while (!feof(stream)) {
        size_t count = fread(chunk, 1, sizeof(chunk), stream);
        if (ferror(stream)) {
            free(result.data);
            result.data = NULL;
            result.length = 0;
            merlo_file_error = UINT32_C(2);
            fclose(stream);
            return result;
        }
        if (count == 0) break;
        if (result.length > UINT64_MAX - (uint64_t)count) {
            free(result.data);
            fclose(stream);
            merlo_overflow_trap("FileLength");
        }
        uint64_t required = result.length + (uint64_t)count;
        if (required > (uint64_t)capacity) {
            size_t next = capacity == 0 ? 4096 : capacity;
            while ((uint64_t)next < required) {
                if (next > SIZE_MAX / 2) {
                    next = (size_t)required;
                    break;
                }
                next *= 2;
            }
            uint8_t *grown = (uint8_t *)realloc(result.data, next);
            if (grown == NULL) {
                free(result.data);
                fclose(stream);
                merlo_allocation_trap();
            }
            result.data = grown;
            capacity = next;
        }
        memcpy(result.data + result.length, chunk, count);
        result.length = required;
    }
    if (fclose(stream) != 0) {
        free(result.data);
        result.data = NULL;
        result.length = 0;
        merlo_file_error = UINT32_C(2);
        return result;
    }
    if (result.data != NULL) ++merlo_allocations;
    return result;
}

static MerloFileLines merlo_file_lines(MerloFileReader *reader) {
    return (MerloFileLines){ reader, reader->generation };
}

static bool merlo_valid_utf8(const uint8_t *data, uint64_t length) {
    for (uint64_t i = 0; i < length;) {
        uint8_t first = data[i++];
        uint64_t width = first < 0x80 ? 1 :
            first >= 0xc2 && first <= 0xdf ? 2 :
            first >= 0xe0 && first <= 0xef ? 3 :
            first >= 0xf0 && first <= 0xf4 ? 4 : 0;
        if (width == 0 || i + width - 1 > length) return false;
        if (width >= 3) {
            uint8_t second = data[i];
            if ((first == 0xe0 && second < 0xa0)
                    || (first == 0xed && second > 0x9f)
                    || (first == 0xf0 && second < 0x90)
                    || (first == 0xf4 && second > 0x8f)) {
                return false;
            }
        }
        for (uint64_t j = 1; j < width; ++j) {
            if ((data[i++] & 0xc0) != 0x80) return false;
        }
    }
    return true;
}

static MerloTextView *merlo_file_next(MerloFileLines *lines) {
    static _Thread_local MerloTextView view;
    MerloFileReader *reader = lines->owner;
    if (reader == NULL || reader->stream == NULL || lines->generation != reader->generation) return NULL;
    char *line = (char *)reader->buffer;
    size_t capacity = (size_t)reader->buffer_capacity;
    ssize_t count = getline(&line, &capacity, reader->stream);
    reader->buffer = (uint8_t *)line;
    reader->buffer_capacity = (uint64_t)capacity;
    if (count < 0) {
        if (ferror(reader->stream)) {
            merlo_file_error = UINT32_C(2);
        }
        merlo_file_close(reader);
        return NULL;
    }
    reader->buffer_length = (uint64_t)count;
    if (reader->buffer_length != 0 && reader->buffer[reader->buffer_length - 1] == '\n') {
        --reader->buffer_length;
    }
    if (reader->buffer_length != 0 && reader->buffer[reader->buffer_length - 1] == '\r') {
        --reader->buffer_length;
    }
    ++reader->line_number;
    if (!merlo_valid_utf8(reader->buffer, reader->buffer_length)) {
        merlo_file_error = UINT32_C(3);
        merlo_file_error_line = reader->line_number;
        merlo_file_close(reader);
        return NULL;
    }
    ++reader->generation;
    lines->generation = reader->generation;
    view = (MerloTextView){ reader->buffer, reader->buffer_length };
    return &view;
}'''
    def _move_drop_glue(self) -> str:
        lines = []
        owners = [item for item in self.representation.descriptors if _is_owner(item)]
        for descriptor in owners:
            lines.append(f"static {_c_name(descriptor.name)} merlo_zero_{_identifier(descriptor.name)}(void);")
            lines.append(f"static {_c_name(descriptor.name)} merlo_move_{_identifier(descriptor.name)}({_c_name(descriptor.name)} *value);")
            lines.append(f"static void merlo_drop_{_identifier(descriptor.name)}({_c_name(descriptor.name)} *value);")
            lines.append(f"static {_c_name(descriptor.name)} merlo_clone_{_identifier(descriptor.name)}(const {_c_name(descriptor.name)} *value);")
        for descriptor in owners:
            lines.extend(self._emit_zero_move_drop(descriptor))
        return "\n".join(lines)

    def _emit_zero_move_drop(self, descriptor: TypeDescriptor) -> list[str]:
        ctype = _c_name(descriptor.name)
        suffix = _identifier(descriptor.name)
        lines = [
            f"static {ctype} merlo_zero_{suffix}(void) {{",
            f"    {ctype} result;",
            "    memset(&result, 0, sizeof(result));",
        ]
        if descriptor.kind == "enum":
            lines.append(f"    result.tag = MERLO_{suffix}_MOVED_TAG;")
        elif descriptor.kind == "record":
            for field_name, field_type, _ in descriptor.fields:
                field_descriptor = self.descriptors[field_type]
                if _is_owner(field_descriptor):
                    lines.append(f"    result.{field_name} = merlo_zero_{_identifier(field_type)}();")
        lines.extend(["    return result;", "}"])
        lines.extend(
            [
                f"static {ctype} merlo_move_{suffix}({ctype} *value) {{",
                f"    {ctype} result = *value;",
                f"    *value = merlo_zero_{suffix}();",
                "    return result;",
                "}",
                f"static void merlo_drop_{suffix}({ctype} *value) {{",
            ]
        )
        if descriptor.kind == "text":
            lines.extend([
                "    if (value->data == NULL) return;",
                "    free(value->data);",
                "    value->data = NULL; value->length = 0;",
                "    ++merlo_frees; ++merlo_text_frees; ++merlo_drop_calls;",
            ])
        elif descriptor.name == "TextBuilder":
            lines.extend([
                "    if (value->data == NULL) return;",
                "    free(value->data);",
                "    value->data = NULL; value->length = 0; value->capacity = 0;",
                "    ++merlo_frees; ++merlo_drop_calls;",
            ])
        elif descriptor.kind == "bytes":
            lines.extend([
                "    if (value->data == NULL) return;",
                "    free(value->data); value->data = NULL; value->length = 0;",
                "    ++merlo_frees; ++merlo_drop_calls;",
            ])
        elif descriptor.kind == "vec":
            assert descriptor.element_type is not None
            element = self.descriptors[descriptor.element_type]
            lines.append("    if (value->active_views != 0) merlo_ownership_trap(\"VecDropDuringView\");")
            if _is_owner(element):
                lines.extend([
                    "    for (uint64_t index = 0; index < value->length; ++index) {",
                    f"        merlo_drop_{_identifier(descriptor.element_type)}(&value->data[index]);",
                    "        ++merlo_vec_elements_dropped;",
                    "    }",
                ])
            else:
                lines.append("    merlo_vec_elements_dropped += value->length;")
            lines.extend([
                "    if (value->data != NULL) { free(value->data); ++merlo_frees; ++merlo_vec_frees; }",
                "    value->data = NULL; value->length = 0; value->capacity = 0; value->active_views = 0;",
                "    ++merlo_drop_calls;",
            ])
        elif descriptor.kind == "box":
            assert descriptor.payload_type is not None
            lines.extend([
                "    if (value->data == NULL) return;",
            ])
            if _is_owner(self.descriptors[descriptor.payload_type]):
                lines.append(f"    merlo_drop_{_identifier(descriptor.payload_type)}(value->data);")
            lines.extend([
                "    free(value->data); value->data = NULL;",
                "    ++merlo_frees; ++merlo_box_frees; ++merlo_drop_calls;",
            ])
        elif descriptor.kind == "map":
            assert descriptor.key_type is not None
            lines.extend([
                "    if (value->active_views != 0) merlo_ownership_trap(\"MapDropDuringView\");",
                "    for (uint64_t index = 0; index < value->length; ++index) {",
                "        if (value->entries[index].key.data != NULL) ++merlo_map_frees;",
                f"        merlo_drop_{_identifier(descriptor.key_type)}(&value->entries[index].key);",
                "        ++merlo_map_owned_keys_dropped;",
                "    }",
                "    if (value->entries != NULL) {",
                "        free(value->entries); ++merlo_frees; ++merlo_map_frees;",
                "    }",
                "    if (value->buckets != NULL) {",
                "        free(value->buckets); ++merlo_frees; ++merlo_map_frees;",
                "    }",
                "    value->entries = NULL; value->buckets = NULL;",
                "    value->length = 0; value->capacity = 0; value->active_views = 0;",
                "    ++merlo_drop_calls;",
            ])
        elif descriptor.kind == "file_reader":
            lines.extend([
                "    merlo_file_close(value);",
                "    ++merlo_drop_calls;",
            ])
        elif descriptor.kind == "array":
            assert descriptor.element_type is not None
            assert descriptor.length is not None
            if _is_owner(self.descriptors[descriptor.element_type]):
                lines.extend(
                    [
                        f"    for (uint64_t index = 0; index < UINT64_C({descriptor.length}); ++index) {{",
                        f"        merlo_drop_{_identifier(descriptor.element_type)}(&value->data[index]);",
                        "    }",
                    ]
                )
            lines.append("    ++merlo_drop_calls;")
        elif descriptor.kind == "record":
            for field_name, field_type, _ in descriptor.fields:
                if _is_owner(self.descriptors[field_type]):
                    lines.append(f"    merlo_drop_{_identifier(field_type)}(&value->{field_name});")
            lines.append("    ++merlo_drop_calls;")
        elif descriptor.kind == "enum":
            lines.append(f"    if (value->tag == MERLO_{suffix}_MOVED_TAG) return;")
            lines.append("    switch (value->tag) {")
            for variant, payload, tag in descriptor.variants:
                lines.append(f"    case UINT32_C({tag}):")
                if payload is not None and _is_owner(self.descriptors[payload]):
                    lines.append(f"        merlo_drop_{_identifier(payload)}(&value->payload.{variant});")
                lines.append("        break;")
            lines.extend([
                "    default: merlo_ownership_trap(\"InvalidEnumTagDuringDrop\");",
                "    }",
                f"    value->tag = MERLO_{suffix}_MOVED_TAG;",
                "    ++merlo_ast_nodes_freed; ++merlo_drop_calls;",
            ])
        lines.append("}")
        if descriptor.kind == "text":
            lines.extend([
                f"static {ctype} merlo_clone_{suffix}(const {ctype} *value) {{",
                "    return merlo_text_clone(value);",
                "}",
            ])
        elif descriptor.kind == "record":
            lines.extend([
                f"static {ctype} merlo_clone_{suffix}(const {ctype} *value) {{",
                f"    {ctype} result = *value;",
            ])
            for field_name, field_type, _ in descriptor.fields:
                if _is_owner(self.descriptors[field_type]):
                    lines.append(
                        f"    result.{field_name} = merlo_clone_{_identifier(field_type)}(&value->{field_name});"
                    )
            lines.extend(["    return result;", "}"])
        elif descriptor.kind == "enum":
            lines.extend([
                f"static {ctype} merlo_clone_{suffix}(const {ctype} *value) {{",
                f"    {ctype} result = *value;",
                "    switch (value->tag) {",
            ])
            for variant, payload, tag in descriptor.variants:
                lines.append(f"    case UINT32_C({tag}):")
                if payload is not None and payload != "Unit" and _is_owner(self.descriptors[payload]):
                    lines.append(
                        f"        result.payload.{variant} = merlo_clone_{_identifier(payload)}(&value->payload.{variant});"
                    )
                lines.append("        break;")
            lines.extend(["    default: merlo_ownership_trap(\"InvalidEnumTagDuringClone\");", "    }", "    return result;", "}"])
        elif descriptor.kind == "vec":
            assert descriptor.element_type is not None
            element = self.descriptors[descriptor.element_type]
            lines.extend([
                f"static {ctype} merlo_clone_{suffix}(const {ctype} *value) {{",
                f"    {ctype} result = merlo_zero_{suffix}();",
                "    result.length = value->length; result.capacity = value->length;",
                "    if (value->length != 0) {",
                f"        result.data = ({_c_name(descriptor.element_type)} *)malloc((size_t)value->length * sizeof({_c_name(descriptor.element_type)}));",
                "        if (result.data == NULL) merlo_allocation_trap();",
                "        ++merlo_allocations;",
                "        for (uint64_t index = 0; index < value->length; ++index) {",
            ])
            if _is_owner(element):
                lines.append(
                    f"            result.data[index] = merlo_clone_{_identifier(descriptor.element_type)}(&value->data[index]);"
                )
            else:
                lines.append("            result.data[index] = value->data[index];")
            lines.extend(["        }", "    }", "    return result;", "}"])
        elif descriptor.kind == "box":
            assert descriptor.payload_type is not None
            payload = self.descriptors[descriptor.payload_type]
            lines.extend([
                f"static {ctype} merlo_clone_{suffix}(const {ctype} *value) {{",
                f"    {ctype} result = merlo_zero_{suffix}();",
                f"    if (value->data != NULL) {{ result.data = ({_c_name(descriptor.payload_type)} *)malloc(sizeof({_c_name(descriptor.payload_type)})); if (result.data == NULL) merlo_allocation_trap(); ++merlo_allocations;",
            ])
            if _is_owner(payload):
                lines.append(
                    f"        *result.data = merlo_clone_{_identifier(descriptor.payload_type)}(value->data);"
                )
            else:
                lines.append("        *result.data = *value->data;")
            lines.extend(["    }", "    return result;", "}"])
        else:
            lines.extend([
                f"static {ctype} merlo_clone_{suffix}(const {ctype} *value) {{",
                "    return *value;",
                "}",
            ])
        return lines

    def _constructors(self) -> str:
        lines = []
        for descriptor in self.representation.descriptors:
            if descriptor.kind == "record" and descriptor.name != "TextBuilder":
                parameters = ", ".join(f"{_c_name(type_name)} {name}" for name, type_name, _ in descriptor.fields) or "void"
                lines.append(f"static {_c_name(descriptor.name)} merlo_make_{_identifier(descriptor.name)}({parameters}) {{")
                lines.append(f"    {_c_name(descriptor.name)} result;")
                for field_name, field_type, _ in descriptor.fields:
                    lines.append(f"    result.{field_name} = {field_name};")
                lines.extend(["    return result;", "}"])
            elif descriptor.kind == "enum" and any(payload is not None for _, payload, _ in descriptor.variants):
                for variant, payload, tag in descriptor.variants:
                    parameters = "void" if payload is None or payload == "Unit" else f"{_c_name(payload)} value"
                    lines.append(f"static {_c_name(descriptor.name)} merlo_make_{_identifier(descriptor.name)}_{variant}({parameters}) {{")
                    lines.append(f"    {_c_name(descriptor.name)} result;")
                    lines.append(f"    result.tag = UINT32_C({tag});")
                    if payload is not None and payload != "Unit":
                        lines.append(f"    result.payload.{variant} = value;")
                    lines.append("    ++merlo_ast_nodes_allocated;")
                    lines.extend(["    return result;", "}"])
        return "\n".join(lines)

    def _vec_box_runtime(self) -> str:
        lines = []
        for descriptor in self.representation.descriptors:
            if descriptor.kind == "vec":
                assert descriptor.element_type is not None
                ctype = _c_name(descriptor.name)
                element = _c_name(descriptor.element_type)
                suffix = _identifier(descriptor.name)
                lines.extend([
                    f"static {ctype} merlo_{suffix}_new(void) {{ {ctype} result = {{ NULL, 0, 0, 0 }}; return result; }}",
                    f"static uint64_t merlo_{suffix}_len(const {ctype} *value) {{ return value->length; }}",
                    f"static uint64_t merlo_{suffix}_capacity(const {ctype} *value) {{ return value->capacity; }}",
                    f"static {element} *merlo_{suffix}_get({ctype} *value, uint64_t index) {{",
                    "    if (index >= value->length) merlo_bounds_trap(index, value->length);",
                    "    return &value->data[index];",
                    "}",
                    f"static void merlo_{suffix}_push({ctype} *value, {element} element) {{",
                    "    if (value->length == UINT64_MAX) merlo_overflow_trap(\"VecLength\");",
                    "    uint64_t required = value->length + 1;",
                    "    if (required > value->capacity) {",
                    "        if (value->active_views != 0) merlo_ownership_trap(\"VecGrowthDuringView\");",
                    "        uint64_t doubled = value->capacity > UINT64_MAX / 2 ? UINT64_MAX : value->capacity * 2;",
                    "        uint64_t capacity = required > doubled ? required : doubled;",
                    "        if (capacity < 8) capacity = 8;",
                    f"        if (capacity > SIZE_MAX / sizeof({element})) merlo_overflow_trap(\"VecCapacity\");",
                    f"        {element} *next = ({element} *)realloc(value->data, (size_t)capacity * sizeof({element}));",
                    "        if (next == NULL) merlo_allocation_trap();",
                    "        if (value->data == NULL) { ++merlo_allocations; ++merlo_vec_allocations; } else { ++merlo_vec_reallocations; }",
                    "        value->data = next; value->capacity = capacity; ++merlo_vec_growths;",
                    "    }",
                    "    value->data[value->length++] = element; ++merlo_vec_initialized;",
                    "}",
                ])
            elif descriptor.kind == "box":
                assert descriptor.payload_type is not None
                ctype = _c_name(descriptor.name)
                payload = _c_name(descriptor.payload_type)
                suffix = _identifier(descriptor.name)
                lines.extend([
                    f"static {ctype} merlo_{suffix}_new({payload} value) {{",
                    f"    {ctype} result; result.data = ({payload} *)malloc(sizeof({payload}));",
                    "    if (result.data == NULL) merlo_allocation_trap();",
                    "    *result.data = value; ++merlo_allocations; ++merlo_box_allocations; return result;",
                    "}",
                    f"static {payload} *merlo_{suffix}_get({ctype} *value) {{ if (value->data == NULL) merlo_ownership_trap(\"BoxUseAfterMove\"); return value->data; }}",
                ])
        return "\n".join(lines)

    def _map_runtime(self) -> str:
        lines = []
        for descriptor in self.representation.descriptors:
            if descriptor.kind != "map":
                continue
            assert descriptor.key_type == "Text"
            assert descriptor.value_type in {
                "Bool", "Byte", "Int64", "UInt64", "Float32", "Float64"
            }
            ctype = _c_name(descriptor.name)
            value_ctype = _c_name(descriptor.value_type)
            suffix = _identifier(descriptor.name)
            lines.extend([
                f"static uint64_t merlo_{suffix}_fnv1a64(const MerloText *key) {{",
                "    if (key->length > SIZE_MAX) merlo_overflow_trap(\"MapKeyLength\");",
                "    uint64_t hash = UINT64_C(14695981039346656037);",
                "    for (uint64_t index = 0; index < key->length; ++index) {",
                "        hash ^= key->data[index];",
                "        hash *= UINT64_C(1099511628211);",
                "    }",
                "    return hash;",
                "}",
                f"static bool merlo_{suffix}_key_equal(const MerloText *left, const MerloText *right) {{",
                "    if (left->length > SIZE_MAX || right->length > SIZE_MAX) merlo_overflow_trap(\"MapKeyLength\");",
                "    return left->length == right->length",
                "        && (left->length == 0 || memcmp(left->data, right->data, (size_t)left->length) == 0);",
                "}",
                f"static uint64_t merlo_{suffix}_linear_probe(const {ctype} *map, const MerloText *key, uint64_t hash, bool *found) {{",
                "    if (map->capacity == 0) { *found = false; return 0; }",
                "    uint64_t slot = hash & (map->capacity - 1);",
                "    for (uint64_t probe = 0; probe < map->capacity; ++probe) {",
                "        uint64_t bucket = map->buckets[slot];",
                "        if (bucket == 0) { *found = false; return slot; }",
                f"        const {ctype}Entry *entry = &map->entries[bucket - 1];",
                f"        if (entry->hash == hash && merlo_{suffix}_key_equal(&entry->key, key)) {{",
                "            *found = true; return slot;",
                "        }",
                "        ++merlo_map_collisions;",
                "        slot = (slot + 1) & (map->capacity - 1);",
                "    }",
                "    merlo_ownership_trap(\"MapProbeExhausted\");",
                "    return 0;",
                "}",
                f"static void merlo_{suffix}_grow({ctype} *map) {{",
                "    if (map->active_views != 0) merlo_ownership_trap(\"MapGrowthDuringView\");",
                "    uint64_t capacity = 8;",
                "    if (map->capacity != 0) {",
                "        if (map->capacity > UINT64_MAX / 2) merlo_overflow_trap(\"MapCapacity\");",
                "        capacity = map->capacity * 2;",
                "    }",
                f"    if (capacity > SIZE_MAX / sizeof({ctype}Entry)",
                "        || capacity > SIZE_MAX / sizeof(uint64_t)) merlo_overflow_trap(\"MapCapacity\");",
                f"    {ctype}Entry *entries = ({ctype}Entry *)malloc((size_t)capacity * sizeof({ctype}Entry));",
                "    if (entries == NULL) merlo_allocation_trap();",
                "    uint64_t *buckets = (uint64_t *)malloc((size_t)capacity * sizeof(uint64_t));",
                "    if (buckets == NULL) { free(entries); merlo_allocation_trap(); }",
                "    memset(buckets, 0, (size_t)capacity * sizeof(uint64_t));",
                "    if (map->length != 0) {",
                f"        memcpy(entries, map->entries, (size_t)map->length * sizeof({ctype}Entry));",
                "    }",
                "    for (uint64_t index = 0; index < map->length; ++index) {",
                "        uint64_t slot = entries[index].hash & (capacity - 1);",
                "        while (buckets[slot] != 0) slot = (slot + 1) & (capacity - 1);",
                "        buckets[slot] = index + 1;",
                "    }",
                f"    {ctype}Entry *old_entries = map->entries;",
                "    uint64_t *old_buckets = map->buckets;",
                "    map->entries = entries; map->buckets = buckets; map->capacity = capacity;",
                "    merlo_allocations += 2; merlo_map_allocations += 2; ++merlo_map_growths;",
                "    if (old_entries != NULL) { free(old_entries); ++merlo_frees; ++merlo_map_frees; }",
                "    if (old_buckets != NULL) { free(old_buckets); ++merlo_frees; ++merlo_map_frees; }",
                "}",
                f"static void merlo_{suffix}_ensure_insert_capacity({ctype} *map) {{",
                "    if (map->length == UINT64_MAX) merlo_overflow_trap(\"MapLength\");",
                "    uint64_t required = map->length + 1;",
                "    if (map->capacity == 0 || required > map->capacity - map->capacity / 4) {",
                f"        merlo_{suffix}_grow(map);",
                "    }",
                "}",
                f"static {ctype} merlo_{suffix}_new(void) {{",
                f"    {ctype} result = {{ NULL, NULL, 0, 0, 0 }};",
                "    return result;",
                "}",
                f"static void merlo_{suffix}_insert({ctype} *map, const MerloText *key, {value_ctype} value) {{",
                f"    uint64_t hash = merlo_{suffix}_fnv1a64(key);",
                "    bool found = false;",
                f"    uint64_t slot = merlo_{suffix}_linear_probe(map, key, hash, &found);",
                "    if (found) {",
                "        map->entries[map->buckets[slot] - 1].value = value;",
                "        ++merlo_map_updates;",
                "        return;",
                "    }",
                f"    merlo_{suffix}_ensure_insert_capacity(map);",
                f"    slot = merlo_{suffix}_linear_probe(map, key, hash, &found);",
                "    if (found) merlo_ownership_trap(\"MapDuplicateAfterGrowth\");",
                "    MerloText owned = { NULL, key->length };",
                "    if (key->length > SIZE_MAX) merlo_overflow_trap(\"MapKeyCapacity\");",
                "    if (key->length != 0) {",
                "        owned.data = (uint8_t *)malloc((size_t)key->length);",
                "        if (owned.data == NULL) merlo_allocation_trap();",
                "        memcpy(owned.data, key->data, (size_t)key->length);",
                "        ++merlo_allocations; ++merlo_text_allocations; ++merlo_map_allocations;",
                "        merlo_bytes_copied += key->length;",
                "    }",
                "    uint64_t index = map->length;",
                "    map->entries[index].key = owned;",
                "    map->entries[index].value = value;",
                "    map->entries[index].hash = hash;",
                "    map->buckets[slot] = index + 1;",
                "    map->length = index + 1;",
                "    ++merlo_map_owned_keys_allocated;",
                "}",
                *(
                    [
                        f"static uint64_t merlo_{suffix}_increment({ctype} *map, const MerloText *key, uint64_t amount) {{",
                        f"    uint64_t hash = merlo_{suffix}_fnv1a64(key);",
                        "    bool found = false;",
                        f"    uint64_t slot = merlo_{suffix}_linear_probe(map, key, hash, &found);",
                        "    if (found) {",
                        f"        {ctype}Entry *entry = &map->entries[map->buckets[slot] - 1];",
                        "        if (entry->value > UINT64_MAX - amount) merlo_overflow_trap(\"MapUInt64\");",
                        "        entry->value += amount;",
                        "        ++merlo_map_updates;",
                        "        return entry->value;",
                        "    }",
                        f"    merlo_{suffix}_insert(map, key, amount);",
                        "    return amount;",
                        "}",
                    ]
                    if descriptor.value_type == "UInt64"
                    else []
                ),
                f"static {value_ctype} merlo_{suffix}_get(const {ctype} *map, const MerloText *key) {{",
                f"    uint64_t hash = merlo_{suffix}_fnv1a64(key);",
                "    bool found = false;",
                f"    uint64_t slot = merlo_{suffix}_linear_probe(map, key, hash, &found);",
                f"    return found ? map->entries[map->buckets[slot] - 1].value : ({value_ctype})0;",
                "}",
                f"static {ctype}EntriesView merlo_{suffix}_entries({ctype} *map) {{",
                "    if (map->active_views == UINT64_MAX) merlo_overflow_trap(\"MapActiveViews\");",
                "    ++map->active_views;",
                f"    {ctype}EntriesView result = {{ map, map->length }};",
                "    return result;",
                "}",
                f"static void merlo_{suffix}_entries_close({ctype}EntriesView *view) {{",
                "    if (view->owner == NULL || view->owner->active_views == 0) {",
                "        merlo_ownership_trap(\"MapViewClosed\");",
                "    }",
                "    --view->owner->active_views;",
                "    view->owner = NULL; view->length = 0;",
                "}",
            ])
        return "\n".join(lines)
    def _functions(self) -> str:
        return "\n\n".join(self._emit_function(self.function_nodes[item.name], item) for item in self.hir.functions)

    def _emit_function(self, node: ast.FunctionDef, function: HIRFunction) -> str:
        self.current_function = function
        self.env_types = {item.name: item.type_name for item in function.parameters}
        self.pointer_values = {
            item.name
            for item in function.parameters
            if self._parameter_is_pointer(item)
        }
        self.owned_locals = {
            item.name: item.type_name
            for item in function.parameters
            if item.ownership == "owned"
            and _is_owner(self.descriptors[item.type_name])
        }
        self.pending_expression_lines = []
        self.pending_expression_drops = []
        self.temporary_declarations = []
        self.temporary_ordinal = 0
        self.expression_context = "statement"
        self.returning_borrowed = False
        self.assigning_borrowed = False
        for hir_node in function.walk():
            if hir_node.kind in {"LetBinding", "VarBinding"}:
                binding_name = hir_node.attribute_map.get("name")
                if isinstance(binding_name, str) and hir_node.type_name:
                    self.env_types[binding_name] = hir_node.type_name
                    if _is_owner(self.descriptors[hir_node.type_name]):
                        self.owned_locals.setdefault(binding_name, hir_node.type_name)
        for child in ast.walk(node):
            if isinstance(child, ast.Assign) and isinstance(child.targets[0], ast.Name):
                binding_name = child.targets[0].id
                if self._is_borrow_expression(child.value):
                    self.pointer_values.add(binding_name)
                    self.owned_locals.pop(binding_name, None)
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                type_name = _type_from_annotation(child.annotation)
                self.env_types[child.target.id] = type_name
                if self._is_borrow_expression(child.value):
                    self.pointer_values.add(child.target.id)
                    self.owned_locals.pop(child.target.id, None)
                elif _is_owner(self.descriptors[type_name]):
                    self.owned_locals.setdefault(child.target.id, type_name)
        declarations = []
        parameter_names = {item.name for item in function.parameters}
        for name, type_name in self.env_types.items():
            if name in parameter_names:
                continue
            if name in self.pointer_values:
                declarations.append(f"    {_c_name(type_name)} *{name} = NULL;")
            elif _is_owner(self.descriptors[type_name]):
                declarations.append(
                    f"    {_c_name(type_name)} {name} = merlo_zero_{_identifier(type_name)}();"
                )
            else:
                declarations.append(f"    {_c_name(type_name)} {name} = {{0}};")
        self.indent = 1
        body = []
        for statement in node.body:
            body.extend(self._statement(statement))
        if function.return_type == "Unit" and not any(isinstance(item, ast.Return) for item in ast.walk(node)):
            body.extend(self._drop_owned_lines("    "))
            body.append("    return;")
        elif function.return_type == "Unit":
            body.append("    return;")
        temporary_declarations = [
            f"    {_c_name(type_name)} {name} = merlo_zero_{_identifier(type_name)}();"
            for name, type_name in self.temporary_declarations
        ]
        lines = [self._function_signature(function) + " {"]
        lines.extend(declarations)
        lines.extend(temporary_declarations)
        lines.extend(body)
        lines.append("}")
        return "\n".join(lines)

    def _is_borrow_expression(self, node: ast.AST | None) -> bool:
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            return False
        method = node.func.attr
        if method not in {"get", "get_mut"}:
            return False
        receiver_type = self._expression_type(node.func.value) or ""
        generic = _generic(receiver_type)
        if (
            method == "get"
            and generic is not None
            and generic[0] in {"Vec", "Box"}
            and _is_owner(self.descriptors[generic[1]])
            and self._is_owning_temporary(node.func.value, receiver_type)
        ):
            return False
        return _map_types(receiver_type) is None

    def _pad(self) -> str:
        return "    " * self.indent

    def _drop_owned_lines(self, pad: str) -> list[str]:
        return [
            f"{pad}merlo_drop_{_identifier(type_name)}({name if name in self.pointer_values else f'&{name}'});"
            for name, type_name in reversed(tuple(self.owned_locals.items()))
        ]

    def _drop_new_iteration_locals(self, existing: set[str], pad: str) -> list[str]:
        lines = []
        for name, type_name in reversed(tuple(self.owned_locals.items())):
            if name not in existing:
                address = name if name in self.pointer_values else f"&{name}"
                lines.append(f"{pad}merlo_drop_{_identifier(type_name)}({address});")
                self.owned_locals.pop(name, None)
        return lines

    def _result_parts(self, type_name: str | None) -> tuple[str, str] | None:
        parts = _result_types(type_name)
        if parts is not None:
            return parts
        descriptor = self.descriptors.get(type_name or "")
        if descriptor is None or descriptor.kind != "enum":
            return None
        variants = {name: payload for name, payload, _ in descriptor.variants}
        ok_type = variants.get("Ok")
        error_type = variants.get("Err")
        if ok_type is None or error_type is None:
            return None
        return ok_type, error_type
    def _wrap_host_result(self, expression: str, expected: str | None) -> str:
        if not expected:
            return expression
        parts = self._result_parts(expected)
        if parts is None:
            return expression
        ok_type, _error_type = parts
        if ok_type == "Unit":
            return (
                f"({expression}, "
                f"merlo_make_{_identifier(expected)}_Ok())"
            )
        if ok_type in self.descriptors and self.descriptors[ok_type].kind == "record":
            expression = f"merlo_make_{_identifier(ok_type)}({expression})"
        return f"merlo_make_{_identifier(expected)}_Ok({expression})"
    def _try_binding(self, target: str | None, marker: ast.Call, expected: str) -> list[str]:
        if len(marker.args) != 1:
            raise RepresentationCBackendError("postfix propagation expects one expression")
        inner = marker.args[0]
        result_type = self._expression_type(inner)
        parts = self._result_parts(result_type)
        current_parts = self._result_parts(self.current_function.return_type if self.current_function else None)
        if parts is None or current_parts is None or parts[1] != current_parts[1]:
            raise RepresentationCBackendError("postfix propagation Result type mismatch")
        ok_type, error_type = parts
        pad = self._pad()
        self.return_ordinal += 1
        temporary = f"__merlo_try_{self.return_ordinal}"
        lines: list[str] = []
        if target in self.owned_locals:
            lines.append(
                f"{pad}merlo_drop_{_identifier(self.owned_locals[target])}"
                f"(&{target});"
            )
        is_file_read = (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and ast.unparse(inner.func.value) == "fs"
            and inner.func.attr in {"open_read", "read"}
        )
        if is_file_read:
            error_descriptor = self.descriptors.get(error_type)
            if error_descriptor is None or error_descriptor.kind != "enum":
                raise RepresentationCBackendError(
                    "file propagation requires an enum error type"
                )
            read_failure = next(
                (
                    (variant, payload)
                    for variant, payload, _ in error_descriptor.variants
                    if variant in {"ReadFailure", "FileOpen"}
                ),
                None,
            )
            if read_failure is None:
                raise RepresentationCBackendError(
                    f"{error_type} has no file-open error variant"
                )
            error_variant, error_payload = read_failure
            if error_payload is None:
                if all(
                    payload is None
                    for _, payload, _ in error_descriptor.variants
                ):
                    error_value = (
                        f"MERLO_{_identifier(error_type)}_{error_variant}"
                    )
                else:
                    error_value = (
                        f"merlo_make_{_identifier(error_type)}_"
                        f"{error_variant}()"
                    )
            elif self.descriptors[error_payload].kind == "text":
                path = self._address_expression(inner.args[0])
                error_value = (
                    f"merlo_make_{_identifier(error_type)}_"
                    f"{error_variant}(merlo_text_clone("
                    f"(const MerloText *){path}))"
                )
            else:
                raise RepresentationCBackendError(
                    f"{error_type}.{error_variant} has unsupported payload "
                    f"{error_payload}"
                )
            lines.append(
                f"{pad}{_c_name(ok_type)} {temporary} = "
                f"{self._expression(inner, expected=None)};"
            )
            lines.append(f"{pad}if (merlo_file_error != 0) {{")
            self.indent += 1
            lines.extend(self._drop_owned_lines(self._pad()))
            lines.append(f"{self._pad()}merlo_file_error = 0;")
            lines.append(
                f"{self._pad()}return "
                f"merlo_make_{_identifier(self.current_function.return_type)}_Err("
                f"{error_value});"
            )
            self.indent -= 1
            lines.append(f"{pad}}}")
            lines.append(f"{pad}merlo_file_error = 0;")
            lines.append(f"{pad}{target} = {temporary};")
            return lines
        result_ctype = _c_name(result_type or "")
        lines.append(
            f"{pad}{result_ctype} {temporary} = "
            f"{self._expression(inner, expected=result_type)};"
        )
        lines.append(
            f"{pad}if ({temporary}.tag != MERLO_{_identifier(result_type or '')}_Ok_TAG) {{"
        )
        self.indent += 1
        lines.extend(self._drop_owned_lines(self._pad()))
        if result_type == self.current_function.return_type:
            lines.append(f"{self._pad()}return {temporary};")
        else:
            lines.append(
                f"{self._pad()}return merlo_make_{_identifier(self.current_function.return_type)}_Err("
                f"{temporary}.payload.Err);"
            )
        self.indent -= 1
        lines.append(f"{pad}}}")
        if target is None:
            if ok_type != "Unit" and _is_owner(self.descriptors[ok_type]):
                lines.append(
                    f"{pad}merlo_drop_{_identifier(ok_type)}(&{temporary}.payload.Ok);"
                )
            lines.append(f"{pad}{temporary}.tag = MERLO_{_identifier(result_type or '')}_MOVED_TAG;")
            return lines
        lines.append(f"{pad}{target} = {temporary}.payload.Ok;")
        lines.append(f"{pad}{temporary}.tag = MERLO_{_identifier(result_type or '')}_MOVED_TAG;")
        return lines

    def _statement(self, node: ast.stmt) -> list[str]:
        start = len(self.pending_expression_lines)
        drop_start = len(self.pending_expression_drops)
        lines = self._statement_impl(node)
        pending = self.pending_expression_lines[start:]
        drops = self.pending_expression_drops[drop_start:]
        del self.pending_expression_lines[start:]
        del self.pending_expression_drops[drop_start:]
        if drops:
            return_indices = [
                index
                for index, line in enumerate(lines)
                if line.lstrip().startswith("return")
            ]
            for index in reversed(return_indices):
                indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
                lines[index:index] = [
                    f"{indent}{drop.lstrip()}" for drop in drops
                ]
            if not isinstance(node, ast.Return) or not return_indices:
                lines.extend(drops)
        return pending + lines
    def _statement_impl(self, node: ast.stmt) -> list[str]:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "__merlo_try__"
        ):
            return self._try_binding(None, node.value, "Unit")
        pad = self._pad()
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "drop"
            and len(node.value.args) == 1
        ):
            target = node.value.args[0]
            if not isinstance(target, ast.Name):
                raise RepresentationCBackendError("drop requires a named owner")
            type_name = self._expression_type(target)
            descriptor = self.descriptors.get(type_name or "")
            if descriptor is None or not _is_owner(descriptor):
                raise RepresentationCBackendError("drop requires an owning value")
            self.owned_locals.pop(target.id, None)
            address = target.id if target.id in self.pointer_values else f"&{target.id}"
            return [f"{pad}merlo_drop_{_identifier(type_name or '')}({address});"]
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            expected = _type_from_annotation(node.annotation)
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "__merlo_try__":
                previous_borrowed = self.assigning_borrowed
                self.assigning_borrowed = self._contains_borrow(expected)
                try:
                    return self._try_binding(node.target.id, node.value, expected)
                finally:
                    self.assigning_borrowed = previous_borrowed
            previous_borrowed = self.assigning_borrowed
            self.assigning_borrowed = (
                self._contains_borrow(expected)
            )
            try:
                value = (
                    self._expression(
                        node.value,
                        expected=expected,
                        want_pointer=node.target.id in self.pointer_values,
                    )
                    if node.value is not None
                    else self._zero_expression(expected)
                )
            finally:
                self.assigning_borrowed = previous_borrowed
            lines = []
            if node.target.id in self.owned_locals:
                lines.append(
                    f"{pad}merlo_drop_{_identifier(self.owned_locals[node.target.id])}"
                    f"(&{node.target.id});"
                )
            lines.append(f"{pad}{node.target.id} = {value};")
            return lines
        if isinstance(node, ast.Assign):
            target = self._lvalue(node.targets[0])
            expected = self._expression_type(node.targets[0])
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "__merlo_try__":
                previous_borrowed = self.assigning_borrowed
                self.assigning_borrowed = self._contains_borrow(expected or "Unit")
                try:
                    return self._try_binding(target, node.value, expected or "Unit")
                finally:
                    self.assigning_borrowed = previous_borrowed
            previous_borrowed = self.assigning_borrowed
            self.assigning_borrowed = (
                self._contains_borrow(expected or "")
            )
            try:
                value = self._expression(
                    node.value,
                    expected=expected,
                    want_pointer=isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id in self.pointer_values,
                )
            finally:
                self.assigning_borrowed = previous_borrowed
            lines = []
            if (
                isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in self.owned_locals
            ):
                lines.append(
                    f"{pad}merlo_drop_"
                    f"{_identifier(self.owned_locals[node.targets[0].id])}"
                    f"(&{node.targets[0].id});"
                )
            lines.append(f"{pad}{target} = {value};")
            return lines
        if isinstance(node, ast.AugAssign):
            target = self._lvalue(node.target)
            expected = self._expression_type(node.target)
            value = self._binary_expression(node.target, node.op, node.value, expected=expected)
            return [f"{pad}{target} = {value};"]
        if isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
                return self._method_statement(node.value)
            return [f"{pad}(void){self._expression(node.value)};"]
        if isinstance(node, ast.If):
            test = self._expression_in_context(
                node.test,
                context="control_flow",
                expected="Bool",
            )
            lines = [f"{pad}if ({test}) {{"]
            self.indent += 1
            for statement in node.body:
                lines.extend(self._statement(statement))
            self.indent -= 1
            if node.orelse:
                lines.append(f"{pad}}} else {{")
                self.indent += 1
                for statement in node.orelse:
                    lines.extend(self._statement(statement))
                self.indent -= 1
            lines.append(f"{pad}}}")
            return lines
        if isinstance(node, ast.While):
            test = self._expression_in_context(
                node.test,
                context="control_flow",
                expected="Bool",
            )
            self.loop_ordinal += 1
            loop_exit = f"__merlo_loop_exit_{self.loop_ordinal}"
            lines = [f"{pad}while ({test}) {{"]}
            self.indent += 1
            for statement in node.body:
                lines.extend(self._statement(statement))
            self.indent -= 1
            self.loop_exit_labels.pop()
            lines.append(f"{pad}}}")
            lines.append(f"{pad}{loop_exit}:;")
            return lines
        if isinstance(node, ast.Break):
            if self.match_depth and self.loop_exit_labels:
                return [f"{pad}goto {self.loop_exit_labels[-1]};"]
            return [f"{pad}break;"]
        if isinstance(node, ast.Continue):
            return [f"{pad}continue;"]
        if isinstance(node, ast.Pass):
            return [f"{pad}(void)0;"]
        if isinstance(node, ast.For):
            return self._map_entries_for_statement(node)
        if isinstance(node, ast.Match):
            return self._match_statement(node)
        if isinstance(node, ast.Return):
            return self._return_statement(node)
        raise RepresentationCBackendError(f"unsupported C statement: {type(node).__name__}@{getattr(node, 'lineno', 0)}")

    def _method_statement(self, call: ast.Call) -> list[str]:
        pad = self._pad()
        assert isinstance(call.func, ast.Attribute)
        receiver = call.func.value
        receiver_type = self._expression_type(receiver)
        receiver_expr = self._address_expression(receiver)
        method = call.func.attr
        generic = _generic(receiver_type or "")
        if generic and generic[0] == "Vec" and method == "push":
            element_type = generic[1]
            argument = self._move_expression(call.args[0], element_type)
            return [f"{pad}merlo_{_identifier(receiver_type)}_push({receiver_expr}, {argument});"]
        if receiver_type == "TextBuilder" and method == "append_byte":
            return [f"{pad}merlo_text_builder_append_byte({receiver_expr}, {self._expression(call.args[0], expected='UInt64')});"]
        if receiver_type == "TextBuilder" and method == "append_scalar":
            return [f"{pad}merlo_text_builder_append_scalar({receiver_expr}, {self._expression(call.args[0], expected='UInt64')});"]
        if receiver_type == "TextBuilder" and method == "append_text":
            return [f"{pad}merlo_text_builder_append_text({receiver_expr}, {self._address_expression(call.args[0])});"]
        if receiver_type == "TextBuilder" and method == "append_uint64":
            return [f"{pad}merlo_text_builder_append_uint64({receiver_expr}, {self._expression(call.args[0], expected='UInt64')});"]
        map_types = _map_types(receiver_type or "")
        if map_types is not None:
            _, value_type = map_types
            suffix = _identifier(receiver_type or "")
            key = self._address_expression(call.args[0])
            if method == "insert":
                value = self._expression(call.args[1], expected=value_type)
                return [f"{pad}merlo_{suffix}_insert({receiver_expr}, {key}, {value});"]
            if method == "increment":
                if value_type != "UInt64":
                    raise RepresentationCBackendError(
                        "Map.increment requires UInt64 values"
                    )
                amount = (
                    self._expression(call.args[1], expected="UInt64")
                    if len(call.args) == 2
                    else "UINT64_C(1)"
                )
                return [f"{pad}(void)merlo_{suffix}_increment({receiver_expr}, {key}, {amount});"]
        return [f"{pad}(void){self._expression(call)};"]

    def _map_entries_for_statement(self, node: ast.For) -> list[str]:
        if (
            isinstance(node.target, ast.Name)
            and (
                (
                    isinstance(node.iter, ast.Call)
                    and isinstance(node.iter.func, ast.Attribute)
                    and node.iter.func.attr == "lines"
                    and self._expression_type(node.iter.func.value) == "FileReader"
                )
                or self._expression_type(node.iter) == "FileLines"
            )
            and not node.orelse
        ):
            pad = self._pad()
            self.loop_ordinal += 1
            ordinal = self.loop_ordinal
            view = f"__merlo_file_lines_{ordinal}"
            line = f"__merlo_file_line_{ordinal}"
            loop_exit = f"__merlo_loop_exit_{ordinal}"
            self.loop_exit_labels.append(loop_exit)
            target = node.target.id
            self.env_types[target] = "TextView"
            lines = [
                f"{pad}{{",
                f"{pad}    MerloFileLines {view} = {self._expression(node.iter)};",
                f"{pad}    for (MerloTextView *{line} = merlo_file_next(&{view}); {line} != NULL; {line} = merlo_file_next(&{view})) {{",
                f"{pad}        MerloTextView {target} = *{line};",
            ]
            iteration_owned = set(self.owned_locals)
            self.indent += 2
            for statement in node.body:
                lines.extend(self._statement(statement))
            lines.extend(self._drop_new_iteration_locals(iteration_owned, f"{pad}        "))
            self.indent -= 2
            self.loop_exit_labels.pop()
            lines.extend(
                [
                    f"{pad}    }}",
                    f"{pad}    {loop_exit}:;",
                    f"{pad}}}",
                ]
            )
            return lines
        if (
            isinstance(node.target, ast.Name)
            and isinstance(node.iter, ast.Call)
            and isinstance(node.iter.func, ast.Attribute)
            and node.iter.func.attr == "view"
            and not node.orelse
        ):
            receiver_node = node.iter.func.value
            receiver_type = self._expression_type(receiver_node)
            generic = _generic(receiver_type or "")
            if generic is not None and generic[0] == "Vec":
                element_type = generic[1]
                pad = self._pad()
                self.loop_ordinal += 1
                index = f"__merlo_vec_index_{self.loop_ordinal}"
                loop_exit = f"__merlo_loop_exit_{self.loop_ordinal}"
                self.loop_exit_labels.append(loop_exit)
                target = node.target.id
                receiver = self._address_expression(receiver_node)
                self.env_types[target] = element_type
                self.pointer_values.add(target)
                lines = [
                    f"{pad}for (uint64_t {index} = 0; {index} < ({receiver})->length; ++{index}) {{",
                    f"{pad}    {_c_name(element_type)} *{target} = &({receiver})->data[{index}];",
                ]
                iteration_owned = set(self.owned_locals)
                self.indent += 1
                for statement in node.body:
                    lines.extend(self._statement(statement))
                lines.extend(self._drop_new_iteration_locals(iteration_owned, f"{pad}    "))
                self.indent -= 1
                self.loop_exit_labels.pop()
                lines.extend(
                    [
                        f"{pad}}}",
                        f"{pad}{loop_exit}:;",
                    ]
                )
                return lines
        if (
            not isinstance(node.target, ast.Name)
            or not isinstance(node.iter, ast.Call)
            or not isinstance(node.iter.func, ast.Attribute)
            or node.iter.func.attr != "entries"
            or node.orelse
        ):
            raise RepresentationCBackendError(
                f"unsupported C for loop: {ast.unparse(node)}"
            )
        receiver_node = node.iter.func.value
        receiver_type = self._expression_type(receiver_node)
        map_types = _map_types(receiver_type or "")
        if map_types is None:
            raise RepresentationCBackendError(
                "for iteration requires a concrete Map.entries()"
            )
        self.loop_ordinal += 1
        ordinal = self.loop_ordinal
        loop_exit = f"__merlo_loop_exit_{ordinal}"
        self.loop_exit_labels.append(loop_exit)
        pad = self._pad()
        ctype = _c_name(receiver_type or "")
        suffix = _identifier(receiver_type or "")
        view = f"__merlo_map_view_{ordinal}"
        index = f"__merlo_map_index_{ordinal}"
        receiver = self._address_expression(receiver_node)
        target = node.target.id
        previous_type = self.env_types.get(target)
        was_pointer = target in self.pointer_values
        self.env_types[target] = f"MapEntry[{map_types[0]},{map_types[1]}]"
        self.pointer_values.add(target)
        lines = [
            f"{pad}{{",
            f"{pad}    {ctype}EntriesView {view} = merlo_{suffix}_entries({receiver});",
            f"{pad}    for (uint64_t {index} = 0; {index} < {view}.length; ++{index}) {{",
            f"{pad}        {ctype}Entry *{target} = &{view}.owner->entries[{index}];",
        ]
        iteration_owned = set(self.owned_locals)
        self.indent += 2
        for statement in node.body:
            lines.extend(self._statement(statement))
        self.indent -= 2
        self.loop_exit_labels.pop()
        lines.extend([
            f"{pad}    }}",
            f"{pad}    {loop_exit}:;",
            f"{pad}    merlo_{suffix}_entries_close(&{view});",
            f"{pad}}}",
        ])
        if previous_type is None:
            del self.env_types[target]
        else:
            self.env_types[target] = previous_type
        if not was_pointer:
            self.pointer_values.discard(target)
        return lines

    def _return_statement(self, node: ast.Return) -> list[str]:
        assert self.current_function is not None
        pad = self._pad()
        self.return_ordinal += 1
        result_name = f"__merlo_return_{self.return_ordinal}"
        lines: list[str] = []
        return_descriptor = self.descriptors.get(self.current_function.return_type)
        self.returning_borrowed = (
            self._contains_borrow(self.current_function.return_type)
        )
        if (
            self.current_function.return_type == "Unit"
            and node.value is not None
        ):
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
            ):
                lines.extend(self._method_statement(node.value))
            else:
                lines.append(
                    f"{pad}(void){self._expression(node.value)};"
                )
        if self.current_function.return_type != "Unit":
            return_type = self.current_function.return_type
            if _is_owner(self.descriptors[return_type]):
                assert node.value is not None
                expression = self._move_expression(node.value, return_type)
            else:
                expression = self._expression(node.value, expected=return_type)
            lines.append(f"{pad}{_c_name(self.current_function.return_type)} {result_name} = {expression};")
        for name, type_name in reversed(tuple(self.owned_locals.items())):
            lines.append(f"{pad}merlo_drop_{_identifier(type_name)}(&{name});")
        self.returning_borrowed = False
        if self.current_function.return_type == "Unit":
            lines.append(f"{pad}return;")
        else:
            lines.append(f"{pad}return {result_name};")
        return lines

    def _match_statement(self, node: ast.Match) -> list[str]:
        pad = self._pad()
        subject_type = self._expression_type(node.subject)
        subject = self._expression(node.subject)
        if subject_type in self.descriptors and self.descriptors[subject_type].kind == "enum" and any(payload is not None for _, payload, _ in self.descriptors[subject_type].variants):
            tag_expression = f"({subject})->tag" if self._expression_is_pointer(node.subject) else f"({subject}).tag"
        elif subject_type in self.descriptors and self.descriptors[subject_type].kind == "enum":
            tag_expression = subject
        else:
            tag_expression = subject
        lines = [f"{pad}switch ({tag_expression}) {{"]
        has_wildcard = any(
            isinstance(case.pattern, ast.MatchAs)
            and case.pattern.name is None
            for case in node.cases
        )
        self.match_depth += 1
        for case in node.cases:
            pattern = case.pattern
            wildcard = isinstance(pattern, ast.MatchAs) and pattern.name is None
            variant_name = None
            bindings: tuple[str, ...] = ()
            enum_name = subject_type
            if isinstance(pattern, ast.MatchValue) and isinstance(pattern.value, ast.Attribute):
                enum_name = ast.unparse(pattern.value.value)
                variant_name = pattern.value.attr
            elif isinstance(pattern, ast.MatchClass) and isinstance(pattern.cls, ast.Attribute):
                enum_name = ast.unparse(pattern.cls.value)
                variant_name = pattern.cls.attr
                bindings = tuple(item.name for item in pattern.patterns if isinstance(item, ast.MatchAs) and item.name)
            elif (
                isinstance(pattern, ast.MatchClass)
                and isinstance(pattern.cls, ast.Name)
                and enum_name in self.descriptors
            ):
                variant_name = (
                    "NoneValue"
                    if pattern.cls.id == "None"
                    else pattern.cls.id
                )
                bindings = tuple(
                    item.name
                    for item in pattern.patterns
                    if isinstance(item, ast.MatchAs) and item.name
                )
            elif (
                isinstance(pattern, ast.MatchSingleton)
                and pattern.value is None
                and enum_name in self.descriptors
            ):
                variant_name = "NoneValue"
            if (
                variant_name is not None
                and enum_name not in self.descriptors
                and subject_type in self.descriptors
            ):
                generic = _generic(subject_type or "")
                if generic is not None and generic[0] == enum_name:
                    enum_name = subject_type
            scope_suffix = "" if self.frozen_general_json else " {"
            if wildcard:
                lines.append(f"{pad}default:{scope_suffix}")
            elif variant_name is not None:
                descriptor = self.descriptors[enum_name]
                tag = next(tag for name, _, tag in descriptor.variants if name == variant_name)
                lines.append(f"{pad}case UINT32_C({tag}):{scope_suffix}")
            else:
                raise RepresentationCBackendError(f"unsupported match pattern: {ast.unparse(pattern)}")
            self.indent += 1
            if variant_name is not None and bindings:
                payload_type = next(payload for name, payload, _ in self.descriptors[enum_name].variants if name == variant_name)
                assert payload_type is not None
                binding = bindings[0]
                self.env_types[binding] = payload_type
                base = subject
                access = f"({base})->payload.{variant_name}" if self._expression_is_pointer(node.subject) else f"({base}).payload.{variant_name}"
                if self.descriptors[payload_type].kind == "scalar":
                    lines.append(f"{self._pad()}{_c_name(payload_type)} {binding} = {access};")
                else:
                    self.pointer_values.add(binding)
                    lines.append(f"{self._pad()}{_c_name(payload_type)} *{binding} = &{access};")
            for statement in case.body:
                lines.extend(self._statement(statement))
            if not any(isinstance(item, ast.Return) for item in case.body):
                lines.append(f"{self._pad()}break;")
            self.indent -= 1
            if not self.frozen_general_json:
                lines.append(f"{pad}}}")
        self.match_depth -= 1
        if not has_wildcard:
            lines.append(f"{pad}default: abort();")
        lines.append(f"{pad}}}")
        return lines

    def _binary_expression(
        self,
        left: ast.AST,
        operation: ast.operator,
        right: ast.AST,
        *,
        expected: str | None = None,
    ) -> str:
        operators = {
            ast.Add: "+",
            ast.Sub: "-",
            ast.Mult: "*",
            ast.Div: "/",
            ast.Mod: "%",
            ast.BitXor: "^",
            ast.BitAnd: "&",
            ast.BitOr: "|",
            ast.LShift: "<<",
            ast.RShift: ">>",
        }
        operator = operators.get(type(operation))
        if operator is None:
            raise RepresentationCBackendError(
                f"unsupported binary operator {type(operation).__name__}"
            )
        arithmetic_type = (
            expected
            if expected in {"Byte", "UInt64", "Int64", "Float32", "Float64"}
            else self._expression_type(left)
        )
        checked = {
            "Byte": {
                ast.Add: "merlo_checked_byte_add",
                ast.Sub: "merlo_checked_byte_sub",
                ast.Mult: "merlo_checked_byte_mult",
            },
            "UInt64": {
                ast.Add: "merlo_checked_uint64_add",
                ast.Sub: "merlo_checked_uint64_sub",
                ast.Mult: "merlo_checked_uint64_mult",
            },
            "Int64": {
                ast.Add: "merlo_checked_int64_add",
                ast.Sub: "merlo_checked_int64_sub",
                ast.Mult: "merlo_checked_int64_mult",
            },
        }.get(arithmetic_type or "", {}).get(type(operation))
        frozen_fnv_multiply = (
            self.frozen_general_json
            and self.current_function is not None
            and self.current_function.name == "checksum_byte"
            and isinstance(operation, ast.Mult)
            and isinstance(left, ast.BinOp)
            and isinstance(left.op, ast.BitXor)
            and isinstance(right, ast.Constant)
            and right.value == 1099511628211
        )
        if checked is not None and not frozen_fnv_multiply:
            return (
                f"{checked}("
                f"{self._expression(left, expected=arithmetic_type)}, "
                f"{self._expression(right, expected=arithmetic_type)})"
            )
        return f"({self._expression(left)} {operator} {self._expression(right)})"
    @staticmethod
    def _borrowed_text_literal(value: str) -> str:
        payload = value.encode("utf-8")
        values = (
            ", ".join(f"UINT8_C({byte})" for byte in payload)
            or "UINT8_C(0)"
        )
        return (
            "(MerloText){"
            f"(uint8_t[]){{{values}}}, UINT64_C({len(payload)})"
            "}"
        )

    def _text_comparison_value(self, node: ast.AST, type_name: str) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):

            return self._borrowed_text_literal(node.value)
        return self._expression(node, expected=type_name)

    def _clone_is_deep(self, type_name: str, seen: frozenset[str] = frozenset()) -> bool:
        descriptor = self.descriptors[type_name]
        if not _is_owner(descriptor) or type_name in seen:
            return True
        next_seen = seen | {type_name}
        if descriptor.kind == "text":
            children = ()
        elif descriptor.kind == "enum":
            children = (
                payload
                for _, payload, _ in descriptor.variants
                if payload is not None
            )
        elif descriptor.kind == "record":
            children = (field_type for _, field_type, _ in descriptor.fields)
        elif descriptor.kind == "array":
            return False
        elif descriptor.kind in {"vec", "box"}:
            children = (
                getattr(descriptor, "element_type", None)
                or getattr(descriptor, "payload_type", None),
            )
        else:
            return False
        return all(
            child is None or self._clone_is_deep(child, next_seen)
            for child in children
        )

    def _contains_borrow(self, type_name: str, seen: frozenset[str] = frozenset()) -> bool:
        if type_name in seen:
            return False
        descriptor = self.descriptors.get(type_name)
        if descriptor is None:
            if type_name.startswith("Result[") and type_name.endswith("]"):
                parts = type_name[7:-1].split(",", 1)
                return any(
                    self._contains_borrow(part.strip(), seen)
                    for part in parts
                )
            return False
        if descriptor.kind in {"borrow", "slice", "file_lines"}:
            return True
        next_seen = seen | {type_name}
        if descriptor.kind == "record":
            children = (field_type for _, field_type, _ in descriptor.fields)
        elif descriptor.kind == "enum":
            children = (
                payload
                for _, payload, _ in descriptor.variants
                if payload is not None
            )
        elif descriptor.kind in {"vec", "box", "array"}:
            children = (
                getattr(descriptor, "element_type", None)
                or getattr(descriptor, "payload_type", None),
            )
        else:
            children = ()
        return any(
            child is not None and self._contains_borrow(child, next_seen)
            for child in children
        )

    def _is_owning_temporary(self, node: ast.AST, type_name: str) -> bool:
        descriptor = self.descriptors.get(type_name)
        if descriptor is None or not _is_owner(descriptor):
            return False
        return isinstance(node, (ast.Call, ast.Constant, ast.List, ast.Tuple))

    def _materialize_owned_argument(self, argument: ast.AST, type_name: str) -> str:
        if self.expression_context != "statement":
            raise RepresentationCBackendError(
                "owning temporary cannot be materialized in control-flow expression"
            )
        if self.returning_borrowed or self.assigning_borrowed:
            raise RepresentationCBackendError(
                "borrowed result escapes owning temporary"
            )
        self.temporary_ordinal += 1
        name = f"__merlo_owned_temp_{self.temporary_ordinal}"
        value = self._move_expression(argument, type_name)
        self.temporary_declarations.append((name, type_name))
        self.env_types[name] = type_name
        self.pending_expression_lines.append(f"{self._pad()}{name} = {value};")
        self.pending_expression_drops.append(
            f"{self._pad()}merlo_drop_{_identifier(type_name)}(&{name});"
        )
        return name
    def _borrow_view_argument(
        self,
        argument: ast.AST,
        expected: str,
        *,
        want_pointer: bool,
    ) -> str | None:
        actual = self._expression_type(argument)
        view_type = {"Text": "TextView", "Bytes": "BytesView"}.get(actual)
        if view_type != expected:
            return None
        source = self._expression(argument, expected=actual)
        ctype = _c_name(expected)
        if want_pointer:
            if self._expression_is_pointer(argument):
                return f"(({ctype} *){self._expression(argument, want_pointer=True)})"
            if isinstance(argument, (ast.Name, ast.Attribute, ast.Subscript)):
                return f"(({ctype} *)&({source}))"
            return None
        return f"({ctype}){{ ({source}).data, ({source}).length }}"

    def _enum_tag_expression(self, node: ast.AST) -> str:
        expression = self._expression(node)
        if self._expression_is_pointer(node):
            return f"({expression})->tag"
        return f"({expression}).tag"


    def _expression_in_context(
        self,
        node: ast.AST | None,
        *,
        context: str,
        expected: str | None = None,
        want_pointer: bool = False,
    ) -> str:
        previous = self.expression_context
        self.expression_context = context
        try:
            return self._expression(
                node,
                expected=expected,
                want_pointer=want_pointer,
            )
        finally:
            self.expression_context = previous

    def _expression(self, node: ast.AST | None, *, expected: str | None = None, want_pointer: bool = False) -> str:
        if node is None:
            return "0"
        if isinstance(node, ast.Name):
            if node.id in self.functions and node.id not in self.env_types:
                return f"merlo_fn_{node.id}"
            if node.id in self.pointer_values:
                if want_pointer:
                    return node.id
                if expected == self.env_types.get(node.id):
                    return f"(*{node.id})"
                return node.id
            return f"&{node.id}" if want_pointer else node.id
        if isinstance(node, ast.Constant):
            if node.value is None and expected:
                descriptor = self.descriptors.get(expected)
                if descriptor is not None and descriptor.kind == "enum":
                    variants = {
                        variant: payload
                        for variant, payload, _ in descriptor.variants
                    }
                    if "Some" in variants and "NoneValue" in variants:
                        return f"merlo_make_{_identifier(expected)}_NoneValue()"
            if isinstance(node.value, bool):
                return "true" if node.value else "false"
            if isinstance(node.value, int):
                if expected == "Byte":
                    return f"UINT8_C({node.value})"
                if expected == "Int64":
                    return f"INT64_C({node.value})"
                return f"UINT64_C({node.value})"
            if isinstance(node.value, float):
                literal = repr(node.value)
                return f"((float){literal})" if expected == "Float32" else literal
            if isinstance(node.value, str):
                literal = self._borrowed_text_literal(node.value)
                if want_pointer:
                    return f"&{literal}"
                payload = node.value.encode("utf-8")
                values = (
                    ", ".join(f"UINT8_C({byte})" for byte in payload)
                    or "UINT8_C(0)"
                )
                return (
                    "merlo_text_literal("
                    f"(const uint8_t[]){{{values}}}, "
                    f"UINT64_C({len(payload)}))"
                )
        if isinstance(node, (ast.List, ast.Tuple)):
            expected_type = expected or self._expression_type(node)
            array = _array_parts(expected_type or "")
            if array is None:
                raise RepresentationCBackendError(
                    "array literal requires a fixed Array type"
                )
            element_type, _ = array
            values = ", ".join(
                self._expression(item, expected=element_type)
                for item in node.elts
            )
            return f"({_c_name(expected_type)}){{ .data = {{{values}}} }}"
        if isinstance(node, ast.Attribute):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id in self.descriptors
                and self.descriptors[node.value.id].kind == "enum"
            ):
                descriptor = self.descriptors[node.value.id]
                payload = next(
                    value
                    for variant, value, _ in descriptor.variants
                    if variant == node.attr
                )
                if all(
                    value is None
                    for _, value, _ in descriptor.variants
                ):
                    return f"MERLO_{node.value.id}_{node.attr}"
                if payload is None:
                    return (
                        f"merlo_make_{_identifier(node.value.id)}_"
                        f"{node.attr}()"
                    )
                raise RepresentationCBackendError(
                    f"enum payload constructor requires a call: "
                    f"{node.value.id}.{node.attr}"
                )
            base = self._expression(node.value)
            operator = "->" if self._expression_is_pointer(node.value) else "."
            value = f"({base}){operator}{node.attr}"
            return f"&({value})" if want_pointer else value
        if isinstance(node, ast.BinOp):
            return self._binary_expression(
                node.left,
                node.op,
                node.right,
                expected=expected,
            )
        if isinstance(node, ast.Subscript):
            owner_type = self._expression_type(node.value)
            generic = _generic(owner_type or "")
            if generic and generic[0] == "Vec":
                pointer = (
                    f"merlo_{_identifier(owner_type or '')}_get("
                    f"{self._address_expression(node.value)}, "
                    f"{self._expression(node.slice)})"
                )
                return pointer if want_pointer else f"(*{pointer})"
            descriptor = self.descriptors.get(owner_type or "")
            if descriptor is not None and descriptor.kind in {"array", "slice"}:
                index = self._expression(node.slice, expected="UInt64")
                owner = self._expression(node.value)
                operator = "->" if self._expression_is_pointer(node.value) else "."
                length = (
                    f"UINT64_C({descriptor.length})"
                    if descriptor.kind == "array"
                    else f"({owner}){operator}length"
                )
                access = f"({owner}){operator}data[{index}]"
                checked = (
                    f"(({index}) < ({length}) ? ({access}) : "
                    f"(merlo_bounds_trap({index}, {length}), ({access})))"
                )
                return f"&({checked})" if want_pointer else checked
            raise RepresentationCBackendError(
                f"unsupported indexed type: {owner_type or 'unknown'}"
            )
        if isinstance(node, ast.BoolOp):
            operator = " && " if isinstance(node.op, ast.And) else " || "
            return "(" + operator.join(
                self._expression_in_context(
                    item,
                    context="short_circuit",
                    expected="Bool",
                )
                for item in node.values
            ) + ")"
        if isinstance(node, ast.Compare):
            pieces = []
            left = node.left
            for operator_node, right in zip(
                node.ops,
                node.comparators,
                strict=True,
            ):
                operator = {
                    ast.Eq: "==",
                    ast.NotEq: "!=",
                    ast.Lt: "<",
                    ast.LtE: "<=",
                    ast.Gt: ">",
                    ast.GtE: ">=",
                }[type(operator_node)]
                left_type = self._expression_type(left)
                right_type = self._expression_type(right)
                left_descriptor = self.descriptors.get(left_type or "")
                right_descriptor = self.descriptors.get(right_type or "")
                if (
                    isinstance(operator_node, (ast.Eq, ast.NotEq))
                    and left_descriptor is not None
                    and right_descriptor is not None
                    and left_descriptor.kind == "text"
                    and right_descriptor.kind == "text"
                ):
                    equal = (
                        "merlo_text_equal_values("
                        f"{self._text_comparison_value(left, left_type or 'Text')}, "
                        f"{self._text_comparison_value(right, right_type or 'Text')})"
                    )
                    pieces.append(
                        equal
                        if isinstance(operator_node, ast.Eq)
                        else f"(!{equal})"
                    )
                elif (
                    isinstance(operator_node, (ast.Eq, ast.NotEq))
                    and left_type == right_type
                    and left_descriptor is not None
                    and left_descriptor.kind == "enum"
                    and any(
                        payload is not None
                        for _, payload, _ in left_descriptor.variants
                    )
                ):
                    pieces.append(
                        f"({self._enum_tag_expression(left)} {operator} "
                        f"{self._enum_tag_expression(right)})"
                    )
                else:
                    pieces.append(
                        f"({self._expression(left)} {operator} "
                        f"{self._expression(right)})"
                    )
                left = right
            if self.frozen_general_json:
                return "(" + " && ".join(pieces) + ")"
            if len(pieces) == 1:
                piece = pieces[0]
                return piece[1:-1] if piece.startswith("(") and piece.endswith(")") else piece
            return "(" + " && ".join(pieces) + ")"
        if isinstance(node, ast.UnaryOp):
            operator = (
                "!"
                if isinstance(node.op, ast.Not)
                else "-"
                if isinstance(node.op, ast.USub)
                else "+"
            )
            return f"({operator}({self._expression(node.operand)}))"
        if isinstance(node, ast.Call):
            return self._call_expression(node, expected=expected, want_pointer=want_pointer)
        raise RepresentationCBackendError(f"unsupported C expression: {type(node).__name__}@{getattr(node, 'lineno', 0)}")

    def _call_expression(self, node: ast.Call, *, expected: str | None, want_pointer: bool) -> str:
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name == "Unit":
                return "0"
            if name in self.extern_functions:
                foreign = self.extern_functions[name]
                arguments = [
                    self._expression(argument, expected=parameter.type_name)
                    for argument, parameter in zip(
                        node.args, foreign.parameters, strict=True
                    )
                ]
            callback = None if name in self.functions else _callback_parts(self.env_types.get(name, ""))
            if callback is not None:
                parameter_types, _ = callback
                arguments = ", ".join(
                    self._expression(argument, expected=parameter_type)
                    for argument, parameter_type in zip(
                        node.args,
                        parameter_types,
                        strict=True,
                    )
                )
                return f"{name}({arguments})"
            if name not in self.functions and name in {
                "wrapping_add",
                "wrapping_sub",
                "wrapping_mul",
                "checked_add",
                "checked_sub",
                "checked_mul",
            }:
                if len(node.args) != 2:
                    raise RepresentationCBackendError(f"{name} expects two integer arguments")
                argument_types = {self._expression_type(argument) for argument in node.args}
                if len(argument_types) != 1 or not argument_types <= {"Byte", "Int64", "UInt64"}:
                    raise RepresentationCBackendError(
                        f"{name} expects matching Byte, Int64, or UInt64 arguments"
                    )
                numeric_type = next(iter(argument_types))
                operation = name.rsplit("_", 1)[-1]
                left = self._expression(node.args[0], expected=numeric_type)
                right = self._expression(node.args[1], expected=numeric_type)
                if name.startswith("wrapping_"):
                    operator = {"add": "+", "sub": "-", "mul": "*"}[operation]
                    if numeric_type == "Int64":
                        return f"((int64_t)((uint64_t)({left}) {operator} (uint64_t)({right})))"
                    return f"(({_c_name(numeric_type)})(({left}) {operator} ({right})))"
                function = f"merlo_checked_{numeric_type.lower()}_{'mult' if operation == 'mul' else operation}"
                return f"{function}({left}, {right})"
            if name in {"Byte", "Int64", "UInt64", "Float32", "Float64"}:
                if len(node.args) != 1:
                    raise RepresentationCBackendError(f"{name} cast expects one argument")
                source_type = self._expression_type(node.args[0])
                value = self._expression(node.args[0], expected=source_type)
                if name == "Byte":
                    if source_type in {"Float32", "Float64"}:
                        return f"merlo_cast_byte_from_float64((double)({value}))"
                    return f"merlo_cast_byte((uint64_t)({value}))"
                if name == "Int64":
                    if source_type in {"Float32", "Float64"}:
                        return f"merlo_cast_int64_from_float64((double)({value}))"
                    if source_type in {"Byte", "UInt64"}:
                        return f"merlo_cast_int64((uint64_t)({value}))"
                if name == "UInt64":
                    if source_type in {"Float32", "Float64"}:
                        return f"merlo_cast_uint64_from_float64((double)({value}))"
                    if source_type == "Int64":
                        return f"merlo_cast_uint64((int64_t)({value}))"
                return f"(({_c_name(name)})({value}))"
            if name in {"Ok", "Err"} and expected:
                parts = self._result_parts(expected)
                if parts is not None:
                    payload = parts[0 if name == "Ok" else 1]
                    if payload == "Unit":
                        return f"merlo_make_{_identifier(expected)}_{name}()"
                    return f"merlo_make_{_identifier(expected)}_{name}({self._move_expression(node.args[0], payload)})"
            if name == "Some" and expected:
                descriptor = self.descriptors.get(expected)
                if descriptor is not None and descriptor.kind == "enum":
                    payloads = {
                        variant: payload
                        for variant, payload, _ in descriptor.variants
                    }
                    if "Some" in payloads and "NoneValue" in payloads:
                        payload = payloads["Some"]
                        assert payload is not None
                        return (
                            f"merlo_make_{_identifier(expected)}_Some("
                            f"{self._move_expression(node.args[0], payload)})"
                        )
            if name == "Path":
                if len(node.args) != 1:
                    raise RepresentationCBackendError("Path constructor expects one Text argument")
                return self._move_expression(node.args[0], "Text")
            if name in self.descriptors and self.descriptors[name].kind == "record":
                descriptor = self.descriptors[name]
                arguments = [
                    self._move_expression(argument, field_type)
                    for argument, (_, field_type, _) in zip(node.args, descriptor.fields, strict=True)
                ]
                return f"merlo_make_{_identifier(name)}({', '.join(arguments)})"
            if name in self.functions:
                function = self.functions[name]
                arguments = []
                for argument, parameter in zip(
                    node.args, function.parameters, strict=True
                ):
                    descriptor = self.descriptors[parameter.type_name]
                    if (
                        parameter.ownership == "borrow"
                        and descriptor.kind == "text"
                        and isinstance(argument, ast.Constant)
                        and isinstance(argument.value, str)
                    ):
                        arguments.append(self._borrowed_text_literal(argument.value))
                        continue
                    actual_type = self._expression_type(argument)
                    if (
                        parameter.ownership in {"borrow", "borrow_mut"}
                        and actual_type is not None
                        and self._is_owning_temporary(argument, actual_type)
                    ):
                        temporary = self._materialize_owned_argument(
                            argument,
                            actual_type,
                        )
                        argument = ast.Name(id=temporary)
                    wants_pointer = self._parameter_is_pointer(parameter)
                    view_argument = self._borrow_view_argument(
                        argument,
                        parameter.type_name,
                        want_pointer=wants_pointer,
                    )
                    if view_argument is not None:
                        arguments.append(view_argument)
                        continue
                    if (
                        parameter.ownership in {"borrow", "borrow_mut"}
                        and self._is_owning_temporary(argument, parameter.type_name)
                    ):
                        temporary = self._materialize_owned_argument(
                            argument,
                            parameter.type_name,
                        )
                        arguments.append(f"&{temporary}" if wants_pointer else temporary)
                        continue
                    if parameter.ownership == "owned":
                        arguments.append(
                            self._move_expression(argument, parameter.type_name)
                        )
                    else:
                        arguments.append(
                            self._expression(
                                argument,
                                expected=parameter.type_name,
                                want_pointer=wants_pointer,
                            )
                        )
                return f"merlo_fn_{name}({', '.join(arguments)})"

        if isinstance(node.func, ast.Attribute):
            receiver_text = ast.unparse(node.func.value)
            method = node.func.attr
            if (
                method in {"as_view", "view"}
                and isinstance(node.func.value, ast.Call)
                and isinstance(node.func.value.func, ast.Attribute)
                and node.func.value.func.attr == "to_text"
            ):
                inner = node.func.value
                source = self._address_expression(inner.func.value)
                return f"(MerloTextView){{ ({source})->data, ({source})->length }}"
            if receiver_text == "fs" and method == "open_read":
                return self._wrap_host_result(
                    f"merlo_file_open_read({self._address_expression(node.args[0])})",
                    expected,
                )
            if receiver_text == "fs" and method == "open_write":
                return self._wrap_host_result(
                    f"merlo_file_open_write({self._address_expression(node.args[0])})",
                    expected,
                )
            if receiver_text == "fs" and method in {"read", "read_text"}:
                return self._wrap_host_result(
                    f"merlo_file_read_all({self._address_expression(node.args[0])})",
                    expected,
                )
            if receiver_text == "fs" and method == "read_chunk":
                return self._wrap_host_result(
                    f"merlo_file_read_chunk({self._address_expression(node.args[0])}, {self._expression(node.args[1])})",
                    expected,
                )
            if receiver_text == "fs" and method in {"write", "write_text"}:
                data_type = self._expression_type(node.args[1])
                data = self._address_expression(node.args[1])
                if data_type == "Text":
                    data = f"(const MerloBytesView *){data}"
                return self._wrap_host_result(
                    f"merlo_file_write_all({self._address_expression(node.args[0])}, {data})",
                    expected,
                )
            if receiver_text == "fs" and method == "write_chunk":
                return self._wrap_host_result(
                    f"merlo_file_write_chunk({self._address_expression(node.args[0])}, {self._address_expression(node.args[1])})",
                    expected,
                )
            if receiver_text == "fs" and method == "close":
                return self._wrap_host_result(
                    f"(merlo_file_close({self._address_expression(node.args[0])}), 0)",
                    expected,
                )
            if receiver_text == "console" and method == "write":
                value = self._expression(node.args[0], expected="Text")
                return (
                    f"(merlo_require_capability(MERLO_EFFECT_CONSOLE_WRITE), "
                    f"fwrite(({value}).data, 1, (size_t)({value}).length, stdout), 0)"
                )
            if receiver_text == "console" and method == "read":
                return "merlo_console_read()"
            if receiver_text == "env" and method in {"read", "get"}:
                return f"merlo_env_read({self._address_expression(node.args[0])})"
            if receiver_text == "clock" and method == "now":
                return "merlo_clock_now()"
            if receiver_text == "random" and method == "read":
                return f"merlo_random_read({self._expression(node.args[0])})"
            if receiver_text == "network" and method == "tcp_connect":
                call = f"merlo_network_tcp_connect({self._address_expression(node.args[0])}, {self._expression(node.args[1])})"
                if expected:
                    ok_type = self._result_parts(expected)[0]
                    if ok_type in self.descriptors and self.descriptors[ok_type].kind == "record":
                        call = f"merlo_make_{_identifier(ok_type)}({call})"
                    return f"merlo_make_{_identifier(expected)}_Ok({call})"
                return call
            if receiver_text == "network" and method == "tcp_send":
                call = f"merlo_network_tcp_send({self._expression(node.args[0])}, {self._address_expression(node.args[1])})"
                return self._wrap_host_result(call, expected)
            if receiver_text == "network" and method == "tcp_receive":
                call = f"merlo_network_tcp_receive({self._expression(node.args[0])}, {self._expression(node.args[1])})"
                return self._wrap_host_result(call, expected)
            if receiver_text == "network" and method == "tcp_close":
                call = f"merlo_network_tcp_close({self._expression(node.args[0])})"
                return self._wrap_host_result(call, expected)
            if receiver_text in {"network", "tcp"} and method in {"tcp", "connect"}:
                return "merlo_network_tcp_guard()"
            if receiver_text in self.descriptors and self.descriptors[receiver_text].kind == "enum":
                descriptor = self.descriptors[receiver_text]
                payload_type = next(
                    (payload for variant, payload, _ in descriptor.variants if variant == method),
                    None,
                )
                arguments = [] if payload_type is None else [self._move_expression(node.args[0], payload_type)]
                return f"merlo_make_{_identifier(receiver_text)}_{method}({', '.join(arguments)})"
            if receiver_text == "Vec" and method == "new":
                if expected is None or not expected.startswith("Vec["):
                    raise RepresentationCBackendError("Vec.new requires contextual monomorphized type")
                return f"merlo_{_identifier(expected)}_new()"
            if receiver_text == "Box" and method == "new":
                if expected is None or not expected.startswith("Box["):
                    raise RepresentationCBackendError("Box.new requires contextual monomorphized type")
                payload_type = _generic(expected)[1]
                return f"merlo_{_identifier(expected)}_new({self._move_expression(node.args[0], payload_type)})"
            if receiver_text == "Map" and method == "new":
                if _map_types(expected or "") is None:
                    raise RepresentationCBackendError(
                        "Map.new requires a contextual concrete Map type"
                    )
                return f"merlo_{_identifier(expected or '')}_new()"
            if receiver_text == "Text" and method == "from_bytes":
                return f"merlo_text_from_bytes({self._address_expression(node.args[0])}, {self._expression(node.args[1])}, {self._expression(node.args[2])})"
            if receiver_text == "TextBuilder" and method == "new":
                return "merlo_text_builder_new()"
            receiver_type = self._expression_type(node.func.value)
            temporary_receiver = (
                receiver_type is not None
                and self._is_owning_temporary(node.func.value, receiver_type)
            )
            if temporary_receiver:
                temporary = self._materialize_owned_argument(
                    node.func.value,
                    receiver_type,
                )
                receiver = f"&{temporary}"
            else:
                receiver = self._address_expression(node.func.value)
            if method == "clone" and receiver_type in self.descriptors and _is_owner(self.descriptors[receiver_type]):
                return f"merlo_clone_{_identifier(receiver_type)}({receiver})"
            enum_descriptor = self.descriptors.get(receiver_type or "")
            if enum_descriptor is not None and enum_descriptor.kind == "enum":
                variants = {variant: payload for variant, payload, _ in enum_descriptor.variants}
                suffix = _identifier(receiver_type or "")
                if "Some" in variants and "NoneValue" in variants:
                    if method == "is_none":
                        return f"({receiver})->tag == MERLO_{suffix}_NoneValue_TAG"
                    if method == "is_some":
                        return f"({receiver})->tag == MERLO_{suffix}_Some_TAG"
                    if method == "unwrap":
                        payload_type = variants["Some"]
                        expression = f"(({receiver})->payload.Some)"
                        if temporary_receiver and payload_type is not None and _is_owner(self.descriptors[payload_type]):
                            if not self._clone_is_deep(payload_type):
                                raise RepresentationCBackendError(
                                    f"cannot clone temporary accessor {payload_type}"
                                )
                            return f"merlo_clone_{_identifier(payload_type)}(&{expression})"
                        return expression
                if "Ok" in variants and "Err" in variants:
                    if method == "is_err":
                        return f"({receiver})->tag == MERLO_{suffix}_Err_TAG"
                    if method == "is_ok":
                        return f"({receiver})->tag == MERLO_{suffix}_Ok_TAG"
                    if method == "unwrap":
                        payload_type = variants["Ok"]
                        expression = f"(({receiver})->payload.Ok)"
                        if temporary_receiver and payload_type is not None and _is_owner(self.descriptors[payload_type]):
                            if not self._clone_is_deep(payload_type):
                                raise RepresentationCBackendError(
                                    f"cannot clone temporary accessor {payload_type}"
                                )
                            return f"merlo_clone_{_identifier(payload_type)}(&{expression})"
                        return expression
                    if method == "unwrap_err":
                        payload_type = variants["Err"]
                        expression = f"(({receiver})->payload.Err)"
                        if temporary_receiver and payload_type is not None and _is_owner(self.descriptors[payload_type]):
                            if not self._clone_is_deep(payload_type):
                                raise RepresentationCBackendError(
                                    f"cannot clone temporary accessor {payload_type}"
                                )
                            return f"merlo_clone_{_identifier(payload_type)}(&{expression})"
                        return expression
            if receiver_type == "FileReader" and method == "lines":
                return f"merlo_file_lines({receiver})"
            generic = _generic(receiver_type or "")
            map_types = _map_types(receiver_type or "")
            if map_types is not None:
                _, value_type = map_types
                suffix = _identifier(receiver_type or "")
                if method in {"get", "increment", "insert"}:
                    key = self._address_expression(node.args[0])
                    if method == "get":
                        return f"merlo_{suffix}_get({receiver}, {key})"
                    if method == "increment":
                        if value_type != "UInt64":
                            raise RepresentationCBackendError(
                                "Map.increment requires UInt64 values"
                            )
                        amount = (
                            self._expression(node.args[1], expected="UInt64")
                            if len(node.args) == 2
                            else "UINT64_C(1)"
                        )
                        return f"merlo_{suffix}_increment({receiver}, {key}, {amount})"
                    value = self._expression(node.args[1], expected=value_type)
                    return f"merlo_{suffix}_insert({receiver}, {key}, {value})"
                if method == "entries":
                    return f"merlo_{suffix}_entries({receiver})"
            if generic and generic[0] == "Vec":
                suffix = _identifier(receiver_type)
                if method == "view":
                    if expected is None or not expected.startswith("Slice["):
                        raise RepresentationCBackendError(
                            "Vec.view requires contextual Slice type"
                        )
                    return (
                        f"({_c_name(expected)}){{ ({receiver})->data, "
                        f"({receiver})->length }}"
                    )
                suffix = _identifier(receiver_type)
                if method in {"len", "capacity"}:
                    if isinstance(node.func.value, ast.Call):
                        return f"({self._expression(node.func.value)}).length"
                if method in {"get", "get_mut"}:
                    pointer = f"merlo_{suffix}_get({receiver}, {self._expression(node.args[0])})"
                    element_type = generic[1]
                    if (
                        method == "get"
                        and temporary_receiver
                        and _is_owner(self.descriptors[element_type])
                    ):
                        if not self._clone_is_deep(element_type):
                            raise RepresentationCBackendError(
                                f"cannot clone temporary accessor {element_type}"
                            )
                        return f"merlo_clone_{_identifier(element_type)}({pointer})"
                    return pointer if want_pointer else f"(*{pointer})"
            if generic and generic[0] == "Box" and method == "get":
                pointer = f"merlo_{_identifier(receiver_type)}_get({receiver})"
                payload_type = generic[1]
                if temporary_receiver and _is_owner(self.descriptors[payload_type]):
                    if not self._clone_is_deep(payload_type):
                        raise RepresentationCBackendError(
                            f"cannot clone temporary accessor {payload_type}"
                        )
                    return f"merlo_clone_{_identifier(payload_type)}({pointer})"
                return pointer if want_pointer else f"(*{pointer})"
            if receiver_type == "Bytes":
                if method == "len":
                    return f"({receiver})->length"
                if method == "view":
                    view = f"(MerloBytesView *){receiver}"
                    return view if want_pointer else f"(*{view})"
                if method == "to_text":
                    return f"merlo_text_from_view((const MerloTextView *){receiver})"
            if receiver_type == "BytesView":
                if method == "len":
                    return f"({receiver})->length"
                if method == "byte":
                    return f"merlo_bytes_load({receiver}, {self._expression(node.args[0])})"
                if method == "slice":
                    start = self._expression(node.args[0])
                    length = self._expression(node.args[1])
                    return (
                        f"(MerloBytesView){{ ((MerloBytesView *){receiver})->data + ({start}), "
                        f"({length}) }}"
                    )
            if receiver_type == "TextView":
                if method == "len":
                    return f"({receiver})->length"
                if method == "byte":
                    return f"merlo_bytes_load((const MerloBytesView *){receiver}, {self._expression(node.args[0])})"
                if method == "contains":
                    return f"merlo_text_view_contains({receiver}, {self._address_expression(node.args[0])}, false)"
                if method == "contains_ascii_case_insensitive":
                    return f"merlo_text_view_contains({receiver}, {self._address_expression(node.args[0])}, true)"
                if method == "slice_bytes":
                    return f"merlo_text_view_slice_bytes({receiver}, {self._expression(node.args[0])}, {self._expression(node.args[1])})"
                if method == "to_text":
                    sliced = node.func.value
                    if (
                        isinstance(sliced, ast.Call)
                        and isinstance(sliced.func, ast.Attribute)
                        and sliced.func.attr == "slice_bytes"
                    ):
                        source = self._address_expression(sliced.func.value)
                        return f"merlo_text_from_view_slice({source}, {self._expression(sliced.args[0])}, {self._expression(sliced.args[1])})"
                    return f"merlo_text_from_view((const MerloTextView *){receiver})"
                    inner = node.func.value
                    if (
                        isinstance(inner.func, ast.Attribute)
                        and inner.func.attr == "to_text"
                    ):
                        source = self._address_expression(inner.func.value)
                        return f"(MerloTextView){{ ({source})->data, ({source})->length }}"
                if method in {"starts_with", "ends_with"}:
                    suffix = "true" if method == "ends_with" else "false"
                    return f"merlo_text_view_prefix_suffix({receiver}, {self._address_expression(node.args[0])}, {suffix})"
            if receiver_type == "Path" and method == "to_text":
                return f"merlo_text_clone({receiver})"
            if receiver_type == "Text":
                if method == "len":
                    return f"({receiver})->length"
                if method == "byte":
                    return f"merlo_text_load({receiver}, {self._expression(node.args[0])})"
                if method == "contains":
                    return f"merlo_text_view_contains((const MerloTextView *){receiver}, {self._address_expression(node.args[0])}, false)"
                if method == "contains_ascii_case_insensitive":
                    return f"merlo_text_view_contains((const MerloTextView *){receiver}, {self._address_expression(node.args[0])}, true)"
                if method in {"starts_with", "ends_with"}:
                    suffix = "true" if method == "ends_with" else "false"
                    return f"merlo_text_view_prefix_suffix((const MerloTextView *){receiver}, {self._address_expression(node.args[0])}, {suffix})"
                if method == "slice_bytes":
                    return f"merlo_text_view_slice_bytes((const MerloTextView *){receiver}, {self._expression(node.args[0])}, {self._expression(node.args[1])})"
                if method in {"as_view", "view"}:
                    sliced = node.func.value
                    if (
                        isinstance(sliced, ast.Call)
                        and isinstance(sliced.func, ast.Attribute)
                        and sliced.func.attr == "to_text"
                        and isinstance(sliced.func.value, ast.Name)
                        and self._expression_type(sliced.func.value) == "Bytes"
                    ):
                        source = self._address_expression(sliced.func.value)
                        return f"(MerloTextView){{ ({source})->data, ({source})->length }}"
                    view = f"(MerloTextView *){receiver}"
                    return view if want_pointer else f"(*{view})"
                if method == "clone":
                    return f"merlo_text_clone({receiver})"
            if receiver_type == "TextBuilder" and method == "finish":
                return f"merlo_text_builder_finish({receiver})"
            if method == "tag":
                expression = self._expression(node.func.value)
                descriptor = self.descriptors.get(receiver_type or "")
                if (
                    descriptor is not None
                    and descriptor.kind == "enum"
                    and all(payload is None for _, payload, _ in descriptor.variants)
                ):
                    return f"((uint64_t)({expression}))"
                return f"({expression})->tag" if self._expression_is_pointer(node.func.value) else f"({expression}).tag"
        raise RepresentationCBackendError(f"unsupported C call: {ast.unparse(node)}")

    def _move_expression(self, node: ast.AST, type_name: str) -> str:
        descriptor = self.descriptors[type_name]
        if not _is_owner(descriptor):
            return self._expression(node, expected=type_name)
        if isinstance(node, ast.Name):
            parameter = next(
                (
                    item
                    for item in self.current_function.parameters
                    if item.name == node.id
                ),
                None,
            ) if self.current_function is not None else None
            if parameter is not None and parameter.ownership in {"borrow", "borrow_mut"}:
                if not self._clone_is_deep(type_name):
                    raise RepresentationCBackendError(
                        f"cannot clone borrowed owner of type {type_name}"
                    )
                source = (
                    node.id
                    if node.id in self.pointer_values
                    else f"&{node.id}"
                )
                return (
                    f"merlo_clone_{_identifier(type_name)}"
                    f"((const {_c_name(type_name)} *){source})"
                )
            if node.id in self.pointer_values:
                return f"merlo_move_{_identifier(type_name)}({node.id})"
            return f"merlo_move_{_identifier(type_name)}(&{node.id})"
        if isinstance(node, ast.Attribute):
            if (
                isinstance(node.value, ast.Name)
                and node.value.id in self.descriptors
                and self.descriptors[node.value.id].kind == "enum"
            ):
                return self._expression(node, expected=type_name)
            return (
                f"merlo_move_{_identifier(type_name)}("
                f"{self._address_expression(node)})"
            )
        if isinstance(node, ast.Subscript):
            owner_type = self._expression_type(node.value)
            generic = _generic(owner_type or "")
            if generic and generic[0] == "Vec" and generic[1] == type_name:
                return (
                    f"merlo_move_{_identifier(type_name)}("
                    f"merlo_{_identifier(owner_type or '')}_get("
                    f"{self._address_expression(node.value)}, "
                    f"{self._expression(node.slice)}))"
                )
        return self._expression(node, expected=type_name)

    def _expression_type(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.env_types.get(node.id)
        if isinstance(node, ast.Constant):
            return (
                "Bool"
                if isinstance(node.value, bool)
                else "UInt64"
                if isinstance(node.value, int)
                else "Float64"
                if isinstance(node.value, float)
                else "Text"
            )
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in self.descriptors and self.descriptors[node.value.id].kind == "enum":
                return node.value.id
            owner = self._expression_type(node.value)
            entry_types = _map_entry_types(owner or "")
            if entry_types is not None:
                if node.attr == "key":
                    return entry_types[0]
                if node.attr == "value":
                    return entry_types[1]
            if owner in self.descriptors:
                descriptor = self.descriptors[owner]
                for field_name, field_type, _ in descriptor.fields:
                    if field_name == node.attr:
                        return field_type
            return None
        if isinstance(node, ast.Subscript):
            owner_type = self._expression_type(node.value)
            array = _array_parts(owner_type or "")
            if array is not None:
                return array[0]
            generic = _generic(owner_type or "")
            if generic and generic[0] in {"Vec", "Slice"}:
                return generic[1]
            return None
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id == "__merlo_try__" and len(node.args) == 1:
                    parts = self._result_parts(self._expression_type(node.args[0]))
                    return parts[0] if parts else None
                if node.func.id in self.functions:
                    return self.functions[node.func.id].return_type
                if node.func.id in self.extern_functions:
                    return self.extern_functions[node.func.id].return_type
                if node.func.id in self.descriptors:
                    return node.func.id
                if node.func.id in {
                    "Byte",
                    "UInt64",
                    "Int64",
                    "Float32",
                    "Float64",
                }:
                    return node.func.id
                if node.func.id.startswith(("wrapping_", "checked_")):
                    return "UInt64"
            if isinstance(node.func, ast.Attribute):
                receiver_text = ast.unparse(node.func.value)
                method = node.func.attr
                if receiver_text in self.descriptors and self.descriptors[receiver_text].kind == "enum":
                    return receiver_text
                receiver_type = self._expression_type(node.func.value)
                host_error = "AppError"
                if self.current_function is not None:
                    current_parts = self._result_parts(
                        self.current_function.return_type
                    )
                    if current_parts is not None:
                        host_error = current_parts[1]
                if receiver_text == "fs" and method in {
                    "open_read", "open_write", "read", "read_text",
                    "read_chunk", "write", "write_text", "write_chunk",
                    "close",
                }:
                    ok_type = (
                        "FileReader"
                        if method in {"open_read", "open_write"}
                        else "Bytes"
                        if method in {"read", "read_text", "read_chunk"}
                        else "Unit"
                    )
                    return f"Result[{ok_type},{host_error}]"
                if receiver_text == "network" and method in {
                    "tcp_connect", "tcp_send", "tcp_receive", "tcp_close",
                }:
                    ok_type = (
                        "UInt64"
                        if method in {"tcp_connect", "tcp_send"}
                        else "Bytes"
                        if method == "tcp_receive"
                        else "Unit"
                    )
                    return f"Result[{ok_type},{host_error}]"
                if method in {"contains", "contains_ascii_case_insensitive", "starts_with", "ends_with"}:
                    return "Bool"
                if receiver_type == "FileReader" and method == "lines":
                    return "FileLines"
                if receiver_text == "Map" and method == "new":
                    return None
                descriptor = self.descriptors.get(receiver_type or "")
                if descriptor is not None and descriptor.kind == "enum":
                    variants = {
                        variant: payload
                        for variant, payload, _ in descriptor.variants
                    }
                    if "Some" in variants and "NoneValue" in variants:
                        if method in {"is_none", "is_some"}:
                            return "Bool"
                        if method == "unwrap":
                            return variants["Some"]
                generic = _generic(receiver_type or "")
                if generic and generic[0] in {"Vec", "Box"} and method in {"get", "get_mut"}:
                    return generic[1]
                map_types = _map_types(receiver_type or "")
                if map_types is not None:
                    if method == "get":
                        return map_types[1]
                    if method == "increment":
                        return "UInt64"
                    if method == "insert":
                        return "Unit"
                    if method == "entries":
                        return f"Borrow[{receiver_type}]"
                if method in {"len", "capacity", "byte", "tag"}:
                    return "UInt64"
                if method in {"contains", "contains_ascii_case_insensitive"}:
                    return "Bool"
                if receiver_text == "Text" and method == "from_bytes":
                    return "Text"
                if receiver_type == "Path" and method == "to_text":
                    return "Text"
                if receiver_type == "Text" and method in {"as_view", "view"}:
                    return "TextView"
                if receiver_type == "Text" and method == "clone":
                    return "Text"
                if receiver_text == "TextBuilder" and method == "new":
                    return "TextBuilder"
                if receiver_type == "TextBuilder" and method == "finish":
                    return "Text"
                if receiver_type in {"Text", "TextView"} and method == "slice_bytes":
                    return "TextView"
                if receiver_type == "TextView" and method == "to_text":
                    return "Text"
        if isinstance(node, (ast.Compare, ast.BoolOp)):
            return "Bool"
        if isinstance(node, ast.UnaryOp):
            return "Bool" if isinstance(node.op, ast.Not) else self._expression_type(node.operand)
        if isinstance(node, ast.BinOp):
            return self._expression_type(node.left)
        return None

    def _expression_is_pointer(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id in self.pointer_values

    def _address_expression(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return f"&{self._borrowed_text_literal(node.value)}"
        expression = self._expression(node)
        return expression if self._expression_is_pointer(node) else f"&({expression})"

    def _lvalue(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._expression(node.value)
            operator = "->" if self._expression_is_pointer(node.value) else "."
            return f"({base}){operator}{node.attr}"
        raise RepresentationCBackendError(f"unsupported lvalue: {ast.unparse(node)}")

    def _zero_expression(self, type_name: str) -> str:
        descriptor = self.descriptors[type_name]
        if _is_owner(descriptor):
            return f"merlo_zero_{_identifier(type_name)}()"
        return f"({_c_name(type_name)}){{0}}"

    def _uint64_host(self, function: HIRFunction) -> str:
        if tuple(parameter.type_name for parameter in function.parameters) != ("BytesView",):
            raise RepresentationCBackendError(
                "UInt64 host entry must accept exactly one BytesView parameter"
            )
        return """static uint8_t *merlo_host_read_stdin(uint64_t *length) {
    uint8_t *data = NULL;
    size_t used = 0;
    size_t capacity = 0;
    uint8_t chunk[4096];
    while (!feof(stdin)) {
        size_t count = fread(chunk, 1, sizeof(chunk), stdin);
        if (ferror(stdin)) { free(data); return NULL; }
        if (count == 0) break;
        if (used > SIZE_MAX - count) { free(data); return NULL; }
        size_t required = used + count;
        if (required > capacity) {
            size_t next = capacity == 0 ? 4096 : capacity;
            while (next < required) {
                if (next > SIZE_MAX / 2) { next = required; break; }

                next *= 2;
            }
            uint8_t *grown = (uint8_t *)realloc(data, next);
            if (grown == NULL) { free(data); return NULL; }
            data = grown;
            capacity = next;
        }
        memcpy(data + used, chunk, count);
        used += count;
    }
    *length = (uint64_t)used;
    return data;
}

int main(int argc, char **argv) {
    uint64_t repeat = 1;
    if (argc > 1) {
        errno = 0;
        char *end = NULL;
        unsigned long long parsed = strtoull(argv[1], &end, 10);
        if (errno != 0 || end == argv[1] || *end != '\\0' || parsed == 0) return 64;
        repeat = (uint64_t)parsed;
    }
    uint64_t length = 0;
    uint8_t *input = merlo_host_read_stdin(&length);
    if (input == NULL && length != 0) return 74;
    MerloBytesView view = { input, length };
    uint64_t result = 0;
    for (uint64_t iteration = 0; iteration < repeat; ++iteration) {
        result = __MERLO_ENTRY__(view);
    }
    printf("OK result=%" PRIu64 "\\n", result);
    printf("MERLO_METRICS allocations=%" PRIu64 " frees=%" PRIu64 " text_allocations=%" PRIu64 " text_frees=%" PRIu64
           " vec_allocations=%" PRIu64 " vec_frees=%" PRIu64 " vec_reallocations=%" PRIu64 " vec_growths=%" PRIu64
           " vec_initialized=%" PRIu64 " vec_elements_dropped=%" PRIu64 " box_allocations=%" PRIu64 " box_frees=%" PRIu64
           " ast_nodes_allocated=%" PRIu64 " ast_nodes_freed=%" PRIu64 " bytes_copied=%" PRIu64 " drops=%" PRIu64
           " map_allocations=%" PRIu64 " map_frees=%" PRIu64 " map_growths=%" PRIu64 " map_collisions=%" PRIu64
           " map_updates=%" PRIu64 " map_owned_keys_allocated=%" PRIu64 " map_owned_keys_dropped=%" PRIu64
           " map_lookup_key_copies=%" PRIu64 "\\n",
           merlo_allocations, merlo_frees, merlo_text_allocations, merlo_text_frees,
           merlo_vec_allocations, merlo_vec_frees, merlo_vec_reallocations, merlo_vec_growths,
           merlo_vec_initialized, merlo_vec_elements_dropped, merlo_box_allocations, merlo_box_frees,
           merlo_ast_nodes_allocated, merlo_ast_nodes_freed, merlo_bytes_copied, merlo_drop_calls,
           merlo_map_allocations, merlo_map_frees, merlo_map_growths, merlo_map_collisions,
           merlo_map_updates, merlo_map_owned_keys_allocated, merlo_map_owned_keys_dropped,
           merlo_map_lookup_key_copies);
    free(input);
    return 0;
}""".replace("__MERLO_ENTRY__", f"merlo_fn_{function.name}").replace(
            "__MERLO_CAPS__", self._capability_initialization(function)
        )

    def _path_host(self, function: HIRFunction) -> str:
        if tuple(parameter.type_name for parameter in function.parameters) != ("Path",):
            raise RepresentationCBackendError("Path host entry must accept exactly one Path parameter")
        result_type = function.return_type
        result_parts = self._result_parts(result_type)
        path_argument = (
            "&path"
            if self._parameter_is_pointer(function.parameters[0])
            else "path"
        )
        if result_type in {"Int64", "UInt64", "Byte"}:
            return f'''int main(int argc, char **argv) {{
{self._capability_initialization(function)}
    if (argc != 2) return 64;
    MerloText path = {{ (uint8_t *)argv[1], (uint64_t)strlen(argv[1]) }};
    merlo_capabilities.filesystem_root = argv[1];
    return (int)merlo_fn_{function.name}({path_argument});
}}'''
        if result_parts is None:
            raise RepresentationCBackendError("Path host entry must return Result")
        ok_type, error_type = result_parts
        error_descriptor = self.descriptors.get(error_type)
        error_cases = ""
        if error_descriptor is not None and all(payload is None for _, payload, _ in error_descriptor.variants):
            for index, (variant, _payload, tag) in enumerate(error_descriptor.variants):
                keyword = "if" if index == 0 else "else if"
                error_cases += (
                    f"    {keyword} (result.payload.Err == MERLO_{_identifier(error_type)}_{variant}) {{\n"
                    f"        fprintf(stderr, \"AppError.{variant}:%s\\n\", argv[1]);\n"
                    "    }\n"
                )
        if not error_cases:
            error_cases = '    fprintf(stderr, "AppError.ReadFailure:%s\\n", argv[1]);\n'
        path_argument = (
            "&path"
            if self._parameter_is_pointer(function.parameters[0])
            else "path"
        )
        file_error_guard = ""
        if self.used_effects & {"fs.read", "fs.write"}:
            file_error_guard = '''    if (merlo_file_error != 0) {
        if (merlo_file_error == UINT32_C(1)) {
            fprintf(stderr, "AppError.FileOpen:%s\\n", argv[1]);
        } else if (merlo_file_error == UINT32_C(2)) {
            fprintf(stderr, "AppError.FileRead:%s\\n", argv[1]);
        } else if (merlo_file_error == UINT32_C(3)) {
            fprintf(stderr, "AppError.InvalidUtf8:%" PRIu64 "\\n", merlo_file_error_line);
        } else if (merlo_file_error == UINT32_C(5)) {
            fprintf(stderr, "AppError.FileWrite:%s\\n", argv[1]);
        } else {
            fprintf(stderr, "AppError.FileAccess:%s\\n", argv[1]);
        }
        merlo_drop_RESULT_TYPE(&result);
        return 74;
    }
'''.replace("RESULT_TYPE", _identifier(result_type))
        if ok_type == "Text":
            success_body = f'''        MerloText *text = &result.payload.Ok;
        if ({"true" if "console.write" in function.effects else "false"}) {{
            if (text->length == 0 || text->data[text->length - 1] != (uint8_t)'\\n') {{
                fputc('\\n', stdout);
            }}
        }} else {{
            fwrite(text->data, 1, (size_t)text->length, stdout);
            if (text->length == 0 || text->data[text->length - 1] != (uint8_t)'\\n') {{
                fputc('\\n', stdout);
            }}
        }}'''
        elif ok_type in {"Int64", "UInt64", "Byte", "Bool"}:
            format_specifier = "PRIi64" if ok_type == "Int64" else "PRIu64"
            cast_type = "int64_t" if ok_type == "Int64" else "uint64_t"
            success_body = (
                f'        printf("%" {format_specifier} "\\n", '
                f'({cast_type})result.payload.Ok);'
            )
        elif ok_type in {"Float32", "Float64"}:
            precision = 9 if ok_type == "Float32" else 17
            success_body = (
                f'        printf("%.{precision}g\\n", '
                f'(double)result.payload.Ok);'
            )
        elif ok_type == "Unit":
            success_body = ""
        else:
            raise RepresentationCBackendError(
                f"Path host cannot print Result success type {ok_type}"
            )
        return f'''int main(int argc, char **argv) {{
{self._capability_initialization(function)}
    if (argc != 2) {{
        fputs("AppError.MissingArgument: expected one Path\\n", stderr);
        return 64;
    }}
    MerloText path = {{ (uint8_t *)argv[1], (uint64_t)strlen(argv[1]) }};
    merlo_capabilities.filesystem_root = argv[1];
    {_c_name(result_type)} result = merlo_fn_{function.name}({path_argument});
{file_error_guard}    if (result.tag == UINT32_C(0)) {{
{success_body}
        merlo_drop_{_identifier(result_type)}(&result);
        return 0;
    }}
{error_cases}    merlo_drop_{_identifier(result_type)}(&result);
    return 74;
}}'''

    def _capability_initialization(self, function: HIRFunction) -> str:
        masks = {
            "console.read": "MERLO_EFFECT_CONSOLE_READ",
            "console.write": "MERLO_EFFECT_CONSOLE_WRITE",
            "fs.read": "MERLO_EFFECT_FS_READ",
            "fs.write": "MERLO_EFFECT_FS_WRITE",
            "env.read": "MERLO_EFFECT_ENV_READ",
            "clock.now": "MERLO_EFFECT_CLOCK_NOW",
            "random.read": "MERLO_EFFECT_RANDOM_READ",
            "network.tcp": "MERLO_EFFECT_NETWORK_TCP",
            "network.http": "MERLO_EFFECT_NETWORK_HTTP",
            "process.args": "MERLO_EFFECT_PROCESS_ARGS",
        }
        selected = [masks[item] for item in function.effects if item in masks]
        expression = " | ".join(selected) if selected else "0u"
        lines = [f"    merlo_capabilities.effects = {expression};"]
        if "process.args" in function.effects:
            lines.append("    merlo_runtime_argc = argc;")
        if self.used_effects & {"fs.read", "fs.write"}:
            lines.append(
                '    merlo_capabilities.filesystem_root = getenv("MERLO_FS_ROOT");'
            )
        if self.used_effects & {"network.tcp", "network.http"}:
            lines.append(
                '    merlo_capabilities.network_host = getenv("MERLO_NETWORK_HOST");'
            )
        if "env.read" in self.used_effects:
            lines.append(
                '    merlo_capabilities.environment_keys = getenv("MERLO_ENV_KEYS");'
            )
        return "\n".join(lines)

    def _host(self) -> str:
        function = self.functions[self.hir.entry_function]
        if tuple(parameter.type_name for parameter in function.parameters) == ("Path",):
            return self._path_host(function)
        if function.return_type == "UInt64":
            return self._uint64_host(function)
        result = self.descriptors[function.return_type]
        fields = {name for name, _, _ in result.fields}
        required = {"ok", "error", "error_offset", "nodes", "arrays", "objects", "fields", "checksum"}
        if not required <= fields:
            raise RepresentationCBackendError("host entry result contract mismatch")
        return """static uint8_t *merlo_host_read_stdin(uint64_t *length) {
    uint8_t *data = NULL;
    size_t used = 0;
    size_t capacity = 0;
    uint8_t chunk[4096];
    while (!feof(stdin)) {
        size_t count = fread(chunk, 1, sizeof(chunk), stdin);
        if (ferror(stdin)) { free(data); return NULL; }
        if (count == 0) break;
        if (used > SIZE_MAX - count) { free(data); return NULL; }
        size_t required = used + count;
        if (required > capacity) {
            size_t next = capacity == 0 ? 4096 : capacity;
            while (next < required) {
                if (next > SIZE_MAX / 2) { next = required; break; }
                next *= 2;
            }
            uint8_t *grown = (uint8_t *)realloc(data, next);
            if (grown == NULL) { free(data); return NULL; }
            data = grown;
            capacity = next;
        }
        memcpy(data + used, chunk, count);
        used += count;
    }
    *length = (uint64_t)used;
    return data;
}

int main(int argc, char **argv) {
__MERLO_CAPS__
    uint64_t repeat = 1;
    if (argc > 1) {
        errno = 0;
        char *end = NULL;
        unsigned long long parsed = strtoull(argv[1], &end, 10);
        if (errno != 0 || end == argv[1] || *end != '\\0' || parsed == 0) return 64;
        repeat = (uint64_t)parsed;
    }
    uint64_t length = 0;
    uint8_t *input = merlo_host_read_stdin(&length);
    if (input == NULL && length != 0) return 74;
    MerloBytesView view = { input, length };
    if (merlo_fn_box_smoke(UINT64_C(41)) != UINT64_C(41)) return 70;
    Merlo_ProgramResult result = {0};
    for (uint64_t iteration = 0; iteration < repeat; ++iteration) {
        result = merlo_fn_main(view);
    }
    if (result.ok) {
        printf("OK checksum=%" PRIu64 " nodes=%" PRIu64 " arrays=%" PRIu64 " objects=%" PRIu64 " fields=%" PRIu64 "\\n",
               result.checksum, result.nodes, result.arrays, result.objects, result.fields);
    } else {
        printf("ERROR kind=%" PRIu32 " offset=%" PRIu64 "\\n", result.error, result.error_offset);
    }
    printf("MERLO_METRICS allocations=%" PRIu64 " frees=%" PRIu64 " text_allocations=%" PRIu64 " text_frees=%" PRIu64
           " vec_allocations=%" PRIu64 " vec_frees=%" PRIu64 " vec_reallocations=%" PRIu64 " vec_growths=%" PRIu64
           " vec_initialized=%" PRIu64 " vec_elements_dropped=%" PRIu64 " box_allocations=%" PRIu64 " box_frees=%" PRIu64
           " ast_nodes_allocated=%" PRIu64 " ast_nodes_freed=%" PRIu64 " bytes_copied=%" PRIu64 " drops=%" PRIu64
           " map_allocations=%" PRIu64 " map_frees=%" PRIu64 " map_growths=%" PRIu64 " map_collisions=%" PRIu64
           " map_updates=%" PRIu64 " map_owned_keys_allocated=%" PRIu64 " map_owned_keys_dropped=%" PRIu64
           " map_lookup_key_copies=%" PRIu64 "\\n",
           merlo_allocations, merlo_frees, merlo_text_allocations, merlo_text_frees,
           merlo_vec_allocations, merlo_vec_frees, merlo_vec_reallocations, merlo_vec_growths,
           merlo_vec_initialized, merlo_vec_elements_dropped, merlo_box_allocations, merlo_box_frees,
           merlo_ast_nodes_allocated, merlo_ast_nodes_freed, merlo_bytes_copied, merlo_drop_calls,
           merlo_map_allocations, merlo_map_frees, merlo_map_growths, merlo_map_collisions,
           merlo_map_updates, merlo_map_owned_keys_allocated, merlo_map_owned_keys_dropped,
           merlo_map_lookup_key_copies);
    free(input);
    return result.ok ? 0 : 2;
}"""

    def _primitive_manifest(self, source: str) -> list[dict[str, Any]]:
        entries = [
            ("malloc", "fn(size: UInt64) -> RawAllocation", "creates unique allocation", "memory", True, False, True, "O(1)", "stdlib"),
            ("realloc", "fn(owner: RawAllocation, size: UInt64) -> RawAllocation", "consumes and returns unique allocation", "memory", True, True, True, "O(n) worst-case", "stdlib"),
            ("free", "fn(owner: RawAllocation) -> Unit", "consumes unique allocation", "memory", False, False, False, "O(1)", "stdlib"),
            ("memcpy", "fn(dst: RawMut, src: RawBorrow, len: UInt64) -> Unit", "borrows source and destination", "memory", False, True, False, "O(n)", "stdlib"),
            ("memmove", "fn(dst: RawMut, src: RawBorrow, len: UInt64) -> Unit", "borrows source and destination", "memory", False, True, False, "O(n)", "stdlib"),
            ("byte_load", "fn(view: Borrow[Bytes], index: UInt64) -> UInt64", "shared borrow", "read", False, False, True, "O(1)", "merlo_bytes_load"),
            ("byte_store", "fn(owner: BorrowMut[Bytes], index: UInt64, byte: UInt64) -> Unit", "unique borrow", "write", False, False, True, "O(1)", "generated Vec/TextBuilder stores"),
            ("host_input", "fn() -> Bytes", "returns unique input owner", "io", True, True, True, "O(n)", "merlo_host_read_stdin"),
            ("host_output", "fn(Borrow[Text]) -> Unit", "shared borrow", "io", False, True, True, "O(n)", "printf/fwrite"),
            ("overflow_trap", "fn(message: TextView) -> Never", "does not consume owners", "trap", False, False, True, "O(1)", "merlo_overflow_trap"),
            ("console.read", "fn() -> Bytes", "returns unique input owner", "console.read", True, True, True, "O(n)", "merlo_console_read"),
            ("console.write", "fn(Borrow[Text]) -> Unit", "shared borrow", "console.write", False, True, True, "O(n)", "merlo_console_write"),
            ("fs.read", "fn(Path) -> Bytes", "returns unique file bytes", "fs.read", True, True, True, "O(n)", "merlo_file_read_all"),
            ("fs.write", "fn(Path, Borrow[Bytes]) -> Unit", "borrows bytes", "fs.write", False, True, True, "O(n)", "merlo_file_write_all"),
            ("env.read", "fn(Text) -> Text", "returns unique environment value", "env.read", True, True, True, "O(n)", "merlo_env_read"),
            ("clock.now", "fn() -> UInt64", "returns scalar timestamp", "clock.now", False, False, True, "O(1)", "merlo_clock_now"),
            ("random.read", "fn(UInt64) -> Bytes", "returns unique random bytes", "random.read", True, True, True, "O(n)", "merlo_random_read"),
            ("network.tcp", "fn(...) -> Unit", "scoped host connection", "network.tcp", False, False, True, "O(1)", "merlo_network_tcp_guard"),
            ("network.http", "fn(...) -> Unit", "scoped host request", "network.http", False, False, True, "O(1)", "merlo_network_http_guard"),
            ("process.args", "fn() -> UInt64", "returns argument count", "process.args", False, False, True, "O(1)", "merlo_process_args_count"),
            ("Text.from_bytes", "fn(Borrow[Bytes], start: UInt64, end: UInt64) -> Text", "returns unique Text", "memory", True, True, True, "O(n)", "merlo_text_from_bytes"),
            ("TextBuilder.append", "fn(BorrowMut[TextBuilder], scalar: UInt64) -> Unit", "unique mutable borrow", "memory", True, True, True, "amortized O(1)", "merlo_text_builder_append_*"),
            ("TextBuilder.finish", "fn(TextBuilder) -> Text", "consumes builder; transfers buffer", "memory", False, False, False, "O(1)", "merlo_text_builder_finish"),
        ]
        effect_primitives = {
            "console.read",
            "console.write",
            "fs.read",
            "fs.write",
            "env.read",
            "clock.now",
            "random.read",
            "network.tcp",
            "network.http",
            "process.args",
        }
        lines = source.splitlines()
        result = []
        for name, signature, ownership, effect, allocates, copies, may_fail, complexity, implementation in entries:
            if name in effect_primitives and name not in self.used_effects:
                continue
            if name == "host_input" and implementation not in source:
                continue
            if implementation == "stdlib":
                size = 0
            elif implementation.endswith("*"):
                prefix = implementation[:-1]
                size = sum(prefix in line for line in lines)
            else:
                size = sum(implementation in line for line in lines)
            result.append(
                {
                    "name": name,
                    "type_signature": signature,
                    "ownership_behavior": ownership,
                    "effect": effect,
                    "may_allocate": allocates,
                    "may_copy": copies,
                    "may_fail": may_fail,
                    "complexity": complexity,
                    "handwritten_implementation_size_lines": size,
                }
            )
        return result


def emit_general_c(
    hir: StructuredHIRProgram,
    representation: RepresentationProgram,
    mir: GeneralPerformanceMIR,
) -> GeneratedC:
    return GeneralCEmitter(hir, representation, mir).emit()


def write_general_c(
    destination: str | Path,
    hir: StructuredHIRProgram,
    representation: RepresentationProgram,
    mir: GeneralPerformanceMIR,
) -> GeneratedC:
    generated = emit_general_c(hir, representation, mir)
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generated.source, encoding="utf-8")
    return generated


__all__ = [
    "C_BACKEND_CONTRACT",
    "C_BACKEND_SCHEMA_VERSION",
    "GeneratedC",
    "GeneralCEmitter",
    "RepresentationCBackendError",
    "emit_general_c",
    "write_general_c",
]
