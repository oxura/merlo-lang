"""C11 backend for the General Representation Core.

Domain control flow comes exclusively from Structured HIR source. This emitter
contains only generic syntax lowering, type-directed layouts/moves/drops, Map,
Vec, Box, Bytes/Text primitives, and the permitted host I/O shim.
"""

from __future__ import annotations

import hashlib
import ast as _python_ast
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from merlo.ffi import pointer_type
from merlo.representation_ir import RepresentationProgram
from merlo.intrinsics import (
    CONTRACT_GRAPH,
    TypeConstructorId,
    INTRINSIC_SIGNATURES,
    intrinsic_signature,
)
from merlo.representation_mir import (
    GeneralMIRBlock,
    GeneralMIRFunction,
    GeneralMIRInstruction,
    GeneralPerformanceMIR,
)
from merlo.structured_hir_v2 import (
    HIRFunction,
    StructuredHIRProgram,
)
from merlo.version import VERSIONS
from merlo.type_parser import generic_arguments
from merlo.representation_c_types import (
    _callback_parts,
    _c_name,
    _identifier,
    _is_owner,
    _result_types,
)
from merlo.representation_c_runtime import RuntimeEmissionMixin


C_BACKEND_SCHEMA_VERSION = 2
C_BACKEND_CONTRACT = "merlo.general-representation-c11.v2"
RUNTIME_ABI_VERSION = VERSIONS.runtime_abi
RUNTIME_ABI_CONTRACT = "merlo.runtime-abi.v2"


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



class GeneralCEmitter(RuntimeEmissionMixin):
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
        self.contract_graph = CONTRACT_GRAPH.bind(hir.type_context)
        self.representation = representation
        self.mir = mir
        self.descriptors = {item.name: item for item in representation.descriptors}
        self._descriptor_aliases = self._build_descriptor_aliases()
        self.functions = {item.name: item for item in hir.functions}
        self.used_effects = frozenset(
            effect for function in hir.functions for effect in function.effects
        )
        self.ffi_program = hir.ffi_program
        self.extern_functions = {
            item.name: item
            for item in self.ffi_program.extern_functions
        }
        self.closure_nodes = tuple(
            node
            for function in hir.functions
            for node in function.walk()
            if node.kind == "ClosureCreate"
        )

    def _descriptor_key(self, type_name: str) -> str:
        descriptor = self.descriptors[type_name]
        if descriptor.source_type_identity is not None:
            return f"identity:{descriptor.source_type_identity}"
        try:
            arguments = generic_arguments(type_name)
        except Exception:
            return type_name
        constructor = type_name.split("[", 1)[0]
        return (
            f"{constructor}["
            + ",".join(self._descriptor_key_for_argument(item) for item in arguments)

            + "]"
        )

    def _descriptor_key_for_argument(self, type_name: str) -> str:
        descriptor = self.descriptors.get(type_name)
        if descriptor is not None:
            if descriptor.source_type_identity is not None:
                return f"identity:{descriptor.source_type_identity}"
            return self._descriptor_key(type_name)
        try:
            arguments = generic_arguments(type_name)
        except Exception:
            return type_name
        constructor = type_name.split("[", 1)[0]
        return (
            f"{constructor}["
            + ",".join(self._descriptor_key_for_argument(item) for item in arguments)
            + "]"
        )

    def _build_descriptor_aliases(self) -> dict[str, str]:
        primary_by_key: dict[str, str] = {}
        aliases: dict[str, str] = {}
        for descriptor in self.representation.descriptors:
            key = self._descriptor_key(descriptor.name)
            primary = primary_by_key.setdefault(key, descriptor.name)
            aliases[descriptor.name] = primary
        return aliases

    def _is_descriptor_alias(self, type_name: str) -> bool:
        return self._descriptor_aliases.get(type_name, type_name) != type_name


    def emit(self) -> GeneratedC:
        sections = [
            self._headers(),
            self._primitive_types(),
            self._forward_declarations(),
            self._vec_box_types(),
            self._nominal_types(),
            self._late_array_types(),
            self._closure_types(),
            self._function_prototypes(),
            self._primitive_runtime(),
            self._effect_runtime(),
            self._file_runtime(),
            self._move_drop_glue(),
            self._closure_runtime(),
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
static void merlo_contract_trap(
    const char *kind,
    const char *function_name,
    uint64_t line
) {
    fprintf(
        stderr,
        "MerloContractViolation:%s:%s:%" PRIu64 "\\n",
        kind,
        function_name,
        line
    );
    abort();
}
static void merlo_division_by_zero_trap(const char *type_name) {
    fprintf(stderr, "MerloDivisionByZero:%s\\n", type_name);
    abort();
}

static void merlo_invalid_shift_trap(const char *type_name) {
    fprintf(stderr, "MerloInvalidShift:%s\\n", type_name);
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
static uint8_t merlo_checked_byte_div(uint8_t left, uint8_t right) {
    if (right == 0) merlo_division_by_zero_trap("Byte");
    return (uint8_t)(left / right);
}

static uint8_t merlo_checked_byte_mod(uint8_t left, uint8_t right) {
    if (right == 0) merlo_division_by_zero_trap("Byte");
    return (uint8_t)(left % right);
}

static uint64_t merlo_checked_uint64_div(uint64_t left, uint64_t right) {
    if (right == 0) merlo_division_by_zero_trap("UInt64");
    return left / right;
}

static uint64_t merlo_checked_uint64_mod(uint64_t left, uint64_t right) {
    if (right == 0) merlo_division_by_zero_trap("UInt64");
    return left % right;
}

static int64_t merlo_checked_int64_div(int64_t left, int64_t right) {
    if (right == 0) merlo_division_by_zero_trap("Int64");
    if (left == INT64_MIN && right == -1) {
        merlo_overflow_trap("Int64Div");
    }
    return left / right;
}

static int64_t merlo_checked_int64_floor_div(
    int64_t left,
    int64_t right
) {
    int64_t quotient = merlo_checked_int64_div(left, right);
    int64_t remainder = left % right;
    if (remainder != 0 && ((remainder < 0) != (right < 0))) {
        --quotient;
    }
    return quotient;
}

static int64_t merlo_checked_int64_mod(int64_t left, int64_t right) {
    (void)merlo_checked_int64_div(left, right);
    int64_t remainder = left % right;
    if (remainder != 0 && ((remainder < 0) != (right < 0))) {
        remainder += right;
    }
    return remainder;
}

static uint8_t merlo_checked_byte_lshift(uint8_t value, uint8_t shift) {
    if (shift >= 8) merlo_invalid_shift_trap("Byte");
    if (value > (uint8_t)(UINT8_MAX >> shift)) {
        merlo_overflow_trap("ByteShift");
    }
    return (uint8_t)(value << shift);
}

static uint8_t merlo_checked_byte_rshift(uint8_t value, uint8_t shift) {
    if (shift >= 8) merlo_invalid_shift_trap("Byte");
    return (uint8_t)(value >> shift);
}

static uint64_t merlo_checked_uint64_lshift(
    uint64_t value,
    uint64_t shift
) {
    if (shift >= 64) merlo_invalid_shift_trap("UInt64");
    if (value > (UINT64_MAX >> shift)) {
        merlo_overflow_trap("UInt64Shift");
    }
    return value << shift;
}

static uint64_t merlo_checked_uint64_rshift(
    uint64_t value,
    uint64_t shift
) {
    if (shift >= 64) merlo_invalid_shift_trap("UInt64");
    return value >> shift;
}

static int64_t merlo_checked_int64_lshift(int64_t value, int64_t shift) {
    if (shift < 0 || shift >= 64) merlo_invalid_shift_trap("Int64");
    if (shift == 0) return value;
    if (shift == 63) {
        if (value == 0) return 0;
        if (value == -1) return INT64_MIN;
        merlo_overflow_trap("Int64Shift");
    }
    int64_t factor = (int64_t)(UINT64_C(1) << (uint64_t)shift);
    if (
        (value > 0 && value > INT64_MAX / factor) ||
        (value < 0 && value < INT64_MIN / factor)
    ) {
        merlo_overflow_trap("Int64Shift");
    }
    return value * factor;
}

static int64_t merlo_checked_int64_rshift(int64_t value, int64_t shift) {
    if (shift < 0 || shift >= 64) merlo_invalid_shift_trap("Int64");
    if (shift == 0) return value;
    if (shift == 63) return value < 0 ? -1 : 0;
    int64_t factor = (int64_t)(UINT64_C(1) << (uint64_t)shift);
    int64_t quotient = value / factor;
    if (value % factor < 0) --quotient;
    return quotient;
}

static int64_t merlo_checked_int64_neg(int64_t value) {
    if (value == INT64_MIN) merlo_overflow_trap("Int64Neg");
    return -value;
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
            if self._is_descriptor_alias(descriptor.name):
                continue
            if descriptor.kind == "enum" and all(
                payload is None for _, payload, _ in descriptor.variants
            ):
                lines.append(f"typedef uint32_t {_c_name(descriptor.name)};")
            elif descriptor.kind in {"record", "enum"} and descriptor.name not in {
                "Text",
                "TextBuilder",
            }:
                lines.append(
                    f"typedef struct {_c_name(descriptor.name)} "
                    f"{_c_name(descriptor.name)};"
                )
        for descriptor in self.representation.descriptors:
            primary = self._descriptor_aliases.get(descriptor.name, descriptor.name)
            if (
                primary != descriptor.name
                and descriptor.kind in {"record", "enum"}
                and descriptor.name not in {"Text", "TextBuilder"}
            ):
                lines.append(
                    f"typedef {_c_name(primary)} {_c_name(descriptor.name)};"
                )
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
typedef struct {
    FILE *stream;
    uint64_t generation;
} MerloFileWriter;
typedef struct { MerloFileReader *owner; uint64_t generation; } MerloFileLines;

static MerloTextView merlo_text_as_view(const MerloText *value) {
    MerloTextView result = { value->data, value->length };
    return result;
}

static MerloBytesView merlo_bytes_as_view(const MerloBytes *value) {
    MerloBytesView result = { value->data, value->length };
    return result;
}"""

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
                if self.descriptors[descriptor.element_type].kind in {
                    "record",
                    "enum",
                }:
                    continue
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
            elif descriptor.kind in {"callback", "closure"}:
                callback = _callback_parts(descriptor.name)
                assert callback is not None
                parameter_types, return_type = callback
                if any(
                    _is_owner(self.descriptors[item])
                    for item in parameter_types
                ):
                    raise RepresentationCBackendError(
                        "owning callback parameters require explicit ownership"
                    )
                parameters = ", ".join(
                    _c_name(item) for item in parameter_types
                )
                name = _c_name(descriptor.name)
                signature = "void *" + (f", {parameters}" if parameters else "")
                lines.append(
                    f"typedef {_c_name(return_type)} (*{name}Call)"
                    f"({signature});"
                )
                lines.append(
                    f"typedef struct {{ {name}Call call; void *environment; "
                    f"void (*retain)(void *); void (*release)(void *); }} {name};"
                )
        return "\n".join(lines)
    def _late_array_types(self) -> str:
        lines = []
        for descriptor in self.representation.descriptors:
            if (
                descriptor.kind == "array"
                and descriptor.element_type is not None
                and self.descriptors[descriptor.element_type].kind
                in {"record", "enum"}
            ):
                assert descriptor.length is not None
                lines.append(
                    f"typedef struct {{ {_c_name(descriptor.element_type)} "
                    f"data[{max(1, descriptor.length)}]; }} "
                    f"{_c_name(descriptor.name)};"
                )
        return "\n".join(lines)


    def _closure_types(self) -> str:
        lines: list[str] = []
        for node in self.closure_nodes:
            closure_id = node.attribute_map.get("closure_id")
            captures = node.attribute_map.get("captures", ())
            if (
                not isinstance(closure_id, str)
                or not isinstance(captures, (list, tuple))
            ):
                raise RepresentationCBackendError(
                    "typed closure metadata is malformed"
                )
            fields = " ".join(
                f"{_c_name(type_name)} {name};"
                for name, type_name, _ownership in captures
            )
            lines.append(
                f"typedef struct {{ uint64_t references; {fields} }} "
                f"MerloClosureEnv_{closure_id};"
            )
        return "\n".join(lines)

    def _nominal_types(self) -> str:
        lines: list[str] = []
        pending = {
            item.name: item
            for item in self.representation.descriptors
            if item.kind in {"record", "enum"}
            and item.name != "TextBuilder"
            and not self._is_descriptor_alias(item.name)
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
        for descriptor in self.representation.descriptors:
            if not self._is_descriptor_alias(descriptor.name):
                continue
            primary = self._descriptor_aliases[descriptor.name]
            lines.append(
                f"typedef {_c_name(primary)} {_c_name(descriptor.name)};"
            )
            if descriptor.kind != "enum":
                continue
            if all(payload is None for _, payload, _ in descriptor.variants):
                for variant, _, _ in descriptor.variants:
                    lines.append(
                        f"static const {_c_name(descriptor.name)} "
                        f"MERLO_{_identifier(descriptor.name)}_{variant} = "
                        f"MERLO_{_identifier(primary)}_{variant};"
                    )
            else:
                for variant, _, _ in descriptor.variants:
                    lines.append(
                        f"#define MERLO_{_identifier(descriptor.name)}_{variant}_TAG "
                        f"MERLO_{_identifier(primary)}_{variant}_TAG"
                    )
                lines.append(
                    f"#define MERLO_{_identifier(descriptor.name)}_MOVED_TAG "
                    f"MERLO_{_identifier(primary)}_MOVED_TAG"
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
        return parameter.ownership in {"borrow_mut", "contained_borrow"} or (
            parameter.ownership == "borrow"
            and _is_owner(descriptor)
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

    def _constructors(self) -> str:
        lines = []
        for descriptor in self.representation.descriptors:
            if descriptor.kind == "record" and descriptor.name != "TextBuilder":
                parameters = ", ".join(f"{_c_name(type_name)} {name}" for name, type_name, _ in descriptor.fields) or "void"
                lines.append(f"static {_c_name(descriptor.name)} merlo_make_{_identifier(descriptor.name)}({parameters}) {{")
                invariant_arguments = ", ".join(
                    (
                        f"&{field_name}"
                        if _is_owner(self.descriptors[field_type])
                        else field_name
                    )
                    for field_name, field_type, _ in descriptor.fields
                )
                for function_name, line in descriptor.invariants:
                    lines.append(
                        "    if (!merlo_fn_"
                        f"{function_name}({invariant_arguments})) "
                        'merlo_contract_trap("invariant", '
                        f'"{descriptor.name}", UINT64_C({line}));'
                    )
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
            assert descriptor.value_type is not None
            ctype = _c_name(descriptor.name)
            value_ctype = _c_name(descriptor.value_type)
            value_descriptor = self.descriptors[descriptor.value_type]
            missing_value = (
                f"merlo_zero_{_identifier(descriptor.value_type)}()"
                if _is_owner(value_descriptor)
                else f"({value_ctype})0"
            )
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
                '    if (map->active_views != 0) merlo_ownership_trap("MapMutationDuringView");',
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
                        '    if (map->active_views != 0) merlo_ownership_trap("MapMutationDuringView");',
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
                f"    return found ? map->entries[map->buckets[slot] - 1].value : {missing_value};",
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
    def _mir_plain_value(self, type_name: str, active: frozenset[str] = frozenset()) -> bool:
        if type_name in active:
            return False
        descriptor = self.descriptors.get(type_name)
        if descriptor is None:
            return False
        if descriptor.kind == "scalar":
            return True
        if descriptor.kind != "record":
            return False
        next_active = active | {type_name}
        return all(
            self._mir_plain_value(field_type, next_active)
            for _, field_type, _ in descriptor.fields
        )

    def _mir_array_value(self, type_name: str) -> bool:
        descriptor = self.descriptors.get(type_name)
        return (
            descriptor is not None
            and descriptor.kind == "array"
            and descriptor.element_type is not None
            and self._mir_plain_value(descriptor.element_type)
        )

    def _mir_call_safe_value(self, type_name: str) -> bool:
        return (
            self._mir_plain_value(type_name)
            or self._mir_array_value(type_name)
            or type_name in {"BytesView", "TextView"}
        )

    def _mir_cfg_eligible(
        self,
        function: HIRFunction,
        mir_function: GeneralMIRFunction,
        *,
        calls_collections: bool = False,
    ) -> bool:
        if function.requirements or function.ensures:
            return False
        if (
            not (
                self._mir_call_safe_value(function.return_type)
                if calls_collections
                else self._mir_plain_value(function.return_type)
            )
        ):
            return False
        allowed = {
            "const",
            "load_local",
            "store_local",
            "binary",
            "boolean",
            "compare",
            "unary",
            "numeric_intrinsic",
            "scalar_cast",
            "construct_record",
            "load_field",
            "store_field",
        }
        if calls_collections:
            allowed.update(
                {
                    "call",
                    "primitive_call",
                    "array_literal",
                    "bounds_checked_index",
                    "collection_operation",
                    "fused_collection_pipeline",
                    "implicit_callable",
                }
            )
        local_types: dict[str, str] = {}
        for block in mir_function.blocks:
            for instruction in block.instructions:
                if instruction.op not in allowed:
                    return False
                if instruction.type_name is not None and not (
                    self._mir_call_safe_value(instruction.type_name)
                    if calls_collections
                    else self._mir_plain_value(instruction.type_name)
                ):
                    return False
                if any(
                    not (
                        self._mir_call_safe_value(self.hir.type_context.render(type_id))
                        if calls_collections
                        else self._mir_plain_value(self.hir.type_context.render(type_id))
                    )
                    for type_id in instruction.operand_type_ids
                ):
                    return False
                if calls_collections:
                    if instruction.op == "call":
                        attrs = instruction.attribute_map
                        callee = attrs.get("callee")
                        moves = attrs.get("move_arguments", ())
                        if not isinstance(callee, str) or moves:
                            return False
                        target = next(
                            (item for item in self.hir.functions if item.name == callee),
                            None,
                        )
                        if target is None:
                            if not callee.endswith(".len"):
                                return False
                        elif not self._mir_call_safe_value(target.return_type):
                            return False
                    elif instruction.op == "primitive_call":
                        attrs = instruction.attribute_map
                        callee = attrs.get("callee")
                        contract_symbol = attrs.get("contract_symbol")
                        if not (
                            isinstance(callee, str)
                            and isinstance(contract_symbol, str)
                            and contract_symbol in {
                                "BytesView.len",
                                "TextView.len",
                                "BytesView.byte",
                                "TextView.byte",
                            }
                        ):
                            return False
                    elif instruction.op == "array_literal":
                        if (
                            instruction.type_name is None
                            or not self._mir_array_value(instruction.type_name)
                        ):
                            return False
                    elif instruction.op == "bounds_checked_index":
                        if (
                            len(instruction.operand_type_ids) != 2
                            or not self._mir_array_value(
                                self.hir.type_context.render(
                                    instruction.operand_type_ids[0]
                                )
                            )
                        ):
                            return False

                    elif instruction.op == "collection_operation":
                        attrs = instruction.attribute_map
                        source_collection_type = str(
                            attrs.get("source_collection_type", "")
                        )
                        collection_kind = attrs.get("collection_kind")
                        descriptor = self.descriptors.get(source_collection_type)
                        if (
                            attrs.get("collection_operation") != "count"
                            or descriptor is None
                            or descriptor.element_type is None
                            or collection_kind
                            not in {"array", "vec"}
                            or (
                                collection_kind == "array"
                                and (
                                    descriptor.kind != "array"
                                    or descriptor.length is None
                                )
                            )
                            or (
                                collection_kind == "vec"
                                and descriptor.kind != "vec"
                            )
                            or not self._mir_plain_value(descriptor.element_type)
                        ):
                            return False
                    elif instruction.op == "implicit_callable":
                        if (
                            not isinstance(
                                instruction.attribute_map.get("expression"),
                                str,
                            )
                            or instruction.operands
                        ):
                            return False
                if instruction.op == "store_local":
                    target = instruction.attribute_map.get(
                        "name",
                        instruction.attribute_map.get("target"),
                    )
                    if (
                        not isinstance(target, str)
                        or instruction.type_name is None
                        or not (
                            self._mir_call_safe_value(instruction.type_name)
                            if calls_collections
                            else self._mir_plain_value(instruction.type_name)
                        )
                    ):
                        return False
                    local_types.setdefault(target.split(".", 1)[0], instruction.type_name)
                if instruction.op == "store_field":
                    target = instruction.attribute_map.get("target")
                    if not isinstance(target, str) or "." not in target:
                        return False
                    if target.split(".", 1)[0] not in local_types:
                        return False
            terminator = block.terminator
            if terminator.kind == "branch":
                if len(terminator.targets) != 2 or terminator.value is None:
                    return False
            elif terminator.kind == "jump":
                if len(terminator.targets) != 1:
                    return False
            elif terminator.kind == "return":
                pass
            else:
                return False
        return bool(mir_function.blocks)
    def _mir_calls_collections_eligible(
        self,
        function: HIRFunction,
        mir_function: GeneralMIRFunction,
    ) -> bool:
        return self._mir_cfg_eligible(
            function,
            mir_function,
            calls_collections=True,
        )

    def _mir_ownership_ffi_eligible(
        self,
        function: HIRFunction,
        mir_function: GeneralMIRFunction,
    ) -> bool:
        allowed = {
            "allocate",
            "allocate_deferred",
            "open_file_reader",
            "file_open_read",
            "bounds_check",
            "borrow_key",
            "checked_growth",
            "checked_uint64_add",
            "copy_key_if_vacant",
            "pass",
            "drop_value",
            "return",
            "borrow_lines",
            "invalidate_line_borrow",
            "file_line_next",
            "file_lines",
            "break",
            "continue",
            "const",
            "load_local",
            "store_local",
            "aug_assign",
            "binary",
            "boolean",
            "compare",
            "unary",
            "numeric_intrinsic",
            "scalar_cast",
            "construct_enum",
            "construct_record",
            "load_field",
            "store_field",
            "result_branch",
            "vec_view",
            "vec_operation",
            "vec_new",
            "vec_push",
            "vec_get",
            "vec_get_mut",
            "vec_len",
            "box_new",
            "box_get",
            "map_new",
            "map_insert",
            "map_get",
            "map_increment",
            "map_entries",
            "entries_len",
            "entries_get",
            "move_value",
            "byte_load",
            "collection_operation",
            "fused_collection_pipeline",
            "implicit_callable",
            "call",
            "primitive_call",
            "array_literal",
            "bounds_checked_index",
            "callback_call",
            "closure_create",
            "load_enum_tag",
            "typed_error",
        }
        result_instructions = {
            instruction.result: instruction
            for block in mir_function.blocks
            for instruction in block.instructions
            if instruction.result is not None
        }
        def method_receiver_type(receiver: str) -> str | None:
            parts = receiver.split(".")
            if not parts or not parts[0]:
                return None
            source_type = next(
                (
                    item.type_name
                    for candidate in mir_function.blocks
                    for item in candidate.instructions
                    if (
                        item.op == "store_local"
                        and item.attribute_map.get(
                            "name", item.attribute_map.get("target")
                        )
                        == parts[0]
                    )
                ),
                next(
                    (
                        parameter.type_name
                        for parameter in function.parameters
                        if parameter.name == parts[0]
                    ),
                    None,
                ),
            )
            for field in parts[1:]:
                descriptor = self.descriptors.get(source_type or "")
                if descriptor is None or descriptor.kind != "record":
                    return None
                source_type = next(
                    (
                        field_type
                        for field_name, field_type, _ownership in descriptor.fields
                        if field_name == field
                    ),
                    None,
                )
                if source_type is None:
                    return None
            return source_type
        for block in mir_function.blocks:
            for instruction in block.instructions:
                if instruction.op not in allowed:
                    return False
                attrs = instruction.attribute_map
                if instruction.op == "call":
                    callee = attrs.get("callee")
                    if not isinstance(callee, str):
                        return False
                    target = next(
                        (
                            item
                            for item in self.hir.functions
                            if item.name == callee
                        ),
                        None,
                    )
                    if target is not None:
                        pass
                    elif callee.startswith("fs."):
                        if callee not in {
                            "fs.open_read",
                            "fs.open_write",
                            "fs.read",
                            "fs.read_text",
                            "fs.read_chunk",
                            "fs.write",
                            "fs.write_text",
                            "fs.write_chunk",
                            "fs.close_read",
                            "fs.close_write",
                        }:
                            return False
                    elif callee in {
                        "console.write",
                        "console.read",
                        "console.read_line",
                        "console.read_all",
                        "env.read",
                        "env.get",
                        "clock.now",
                        "random.read",
                        "network.http_request",
                        "network.tcp_connect",
                        "network.tcp_send",
                        "network.tcp_receive",
                        "network.tcp_close",
                        "process.args",
                        "process.arg",
                    }:
                        pass
                    elif callee == "Unit":
                        if instruction.operands:
                            return False
                    elif callee == "Path":
                        if len(instruction.operands) != 1:
                            return False
                    elif attrs.get("representation_lowering") in {
                        "option_is_none",
                        "option_is_some",
                        "result_is_err",
                        "result_is_ok",
                    }:
                        if len(instruction.operands) != 1:
                            return False
                        option_type = self.hir.type_context.render(
                            instruction.operand_type_ids[0]
                        )
                        option_descriptor = self.descriptors.get(option_type)
                        if option_descriptor is None or option_descriptor.kind != "enum":
                            return False
                        required_variants = (
                            {"NoneValue", "Some"}
                            if attrs["representation_lowering"].startswith("option_")
                            else {"Ok", "Err"}
                        )
                        if (
                            {
                                name
                                for name, _payload, _tag in option_descriptor.variants
                            }
                            < required_variants
                        ):
                            return False
                    elif attrs.get("representation_lowering") in {
                        "option_unwrap_clone",
                        "result_unwrap_clone",
                        "result_unwrap_err_clone",
                    }:
                        if len(instruction.operands) != 1:
                            return False
                        source_type = self.hir.type_context.render(
                            instruction.operand_type_ids[0]
                        )
                        source_descriptor = self.descriptors.get(source_type)
                        if source_descriptor is None or source_descriptor.kind != "enum":
                            return False
                        variants = {
                            name: payload
                            for name, payload, _tag in source_descriptor.variants
                        }
                        variant = {
                            "option_unwrap_clone": "Some",
                            "result_unwrap_clone": "Ok",
                            "result_unwrap_err_clone": "Err",
                        }[attrs["representation_lowering"]]
                        if variant not in variants:
                            return False
                    elif callee.endswith(".clone"):
                        if len(instruction.operands) == 1:
                            source_type = self.hir.type_context.render(
                                instruction.operand_type_ids[0]
                            )
                        elif not instruction.operands:
                            source_name = callee.rsplit(".clone", 1)[0]
                            source_type = method_receiver_type(source_name)
                        else:
                            return False
                        source_descriptor = self.descriptors.get(source_type or "")
                        if (
                            source_descriptor is None
                            or not _is_owner(source_descriptor)
                        ):
                            return False
                    elif callee.endswith(".len"):
                        source_name = callee.rsplit(".", 1)[0]
                        source_type = next(
                            (
                                item.type_name
                                for candidate in mir_function.blocks
                                for item in candidate.instructions
                                if (
                                    item.op == "store_local"
                                    and item.attribute_map.get("name")
                                    == source_name
                                )
                            ),
                            None,
                        )
                        descriptor = self.descriptors.get(source_type or "")
                        if (
                            descriptor is None
                            or descriptor.kind != "array"
                            or descriptor.length is None
                        ):
                            return False
                    elif not (
                        instruction.type_name is not None
                        and (
                            descriptor := self.descriptors.get(
                                instruction.type_name
                            )
                        ) is not None
                        and descriptor.kind == "enum"
                        and any(
                            variant == callee
                            for variant, _payload, _tag in descriptor.variants
                        )
                    ):
                        return False
                elif instruction.op == "primitive_call":
                    callee = attrs.get("callee")
                    if not isinstance(callee, str):
                        return False
                    if not (
                        callee in {"Text.from_bytes", "TextBuilder.new", "len", "to_text"}
                        or callee.endswith(".append_byte")
                        or callee.endswith(".append_scalar")
                        or callee.endswith(".append_uint64")
                        or callee.endswith(".append_text")
                        or callee.endswith(".finish")
                        or callee == "clone"
                        or callee.endswith(".ends_with")
                        or callee.endswith(".len")
                        or callee.endswith(".clone")
                        or callee.endswith(".view")
                        or callee.endswith(".as_view")
                        or callee in {"view", "as_view"}
                        or callee.endswith(".slice")
                        or callee == "slice_bytes"
                        or callee.endswith(".slice_bytes")
                        or callee.endswith(".to_text")
                        or callee.endswith(".contains")
                        or callee.endswith(".contains_ascii_case_insensitive")
                    ):
                        return False
                elif instruction.op == "collection_operation":
                    source_collection_type = str(
                        attrs.get("source_collection_type", "")
                    )
                    collection_kind = attrs.get("collection_kind")
                    collection_operation = attrs.get("collection_operation")
                    if (
                        collection_operation not in {"count", "where", "map"}
                        or collection_kind
                        not in {
                            "array",
                            "vec",
                            "slice",
                            "bytes",
                            "bytes_view",
                            "text",
                            "text_view",
                        }
                    ):
                        return False
                    if collection_kind in {"array", "vec", "slice"}:
                        descriptor_type = source_collection_type
                        if (
                            descriptor_type.startswith("Borrow[")
                            and descriptor_type.endswith("]")
                        ):
                            descriptor_type = descriptor_type[7:-1]
                        descriptor = self.descriptors.get(descriptor_type)
                        if (
                            descriptor is None
                            or descriptor.kind != collection_kind
                            or descriptor.element_type is None
                            or (
                                collection_kind == "array"
                                and descriptor.length is None
                            )
                        ):
                            return False
                    elif attrs.get("element_type") != "Byte":
                        return False
                elif instruction.op == "implicit_callable":
                    if (
                        not isinstance(
                            attrs.get("expression"),
                            str,
                        )
                        or instruction.operands
                    ):
                        return False
                elif instruction.op == "closure_create":
                    closure_id = attrs.get("closure_id")
                    captures = attrs.get("captures", ())
                    if (
                        not isinstance(closure_id, str)
                        or not isinstance(captures, (list, tuple))
                        or len(captures) != len(instruction.operands)
                        or not any(
                            node.attribute_map.get("closure_id") == closure_id
                            for node in self.closure_nodes
                        )
                    ):
                        return False
                elif instruction.op == "construct_enum":
                    callee = attrs.get("callee")
                    descriptor = self.descriptors.get(instruction.type_name or "")
                    if (
                        descriptor is None
                        or descriptor.kind != "enum"
                        or not isinstance(callee, str)
                        or "." not in callee
                        or not any(
                            name == callee.rsplit(".", 1)[1]
                            for name, _payload, _tag in descriptor.variants
                        )
                    ):
                        return False
                elif instruction.op == "bounds_checked_index":
                    if len(instruction.operand_type_ids) != 2:
                        return False
                    source_type = self.hir.type_context.render(
                        instruction.operand_type_ids[0]
                    )
                    descriptor = self.descriptors.get(source_type)
                    if source_type in {
                        "Bytes",
                        "BytesView",
                        "Text",
                        "TextView",
                    }:
                        pass
                    elif descriptor is None:
                        return False
                    elif descriptor.kind not in {"array", "vec", "slice"}:
                        return False
                    elif (
                        descriptor.kind == "array"
                        and descriptor.length is None
                    ):
                        return False
                elif instruction.op == "result_branch":
                    source_type = (
                        self.hir.type_context.render(instruction.operand_type_ids[0])
                        if instruction.operand_type_ids
                        else None
                    )
                    descriptor = self.descriptors.get(source_type or "")
                    if (
                        descriptor is None
                        or descriptor.kind != "enum"
                        or not any(
                            variant == "Ok"
                            for variant, _payload, _tag in descriptor.variants
                        )
                    ):
                        return False
            terminator = block.terminator
            if terminator.kind == "branch":
                if len(terminator.targets) != 2 or terminator.value is None:
                    return False
            elif terminator.kind == "jump":
                if len(terminator.targets) != 1:
                    return False
            elif terminator.kind == "switch":
                if terminator.value is None or not terminator.cases:
                    return False
                source = result_instructions.get(terminator.value)
                descriptor = self.descriptors.get(
                    source.type_name if source is not None else ""
                )
                if descriptor is None or descriptor.kind != "enum":
                    return False
                if any(
                    variant != "_"
                    and variant.rsplit(".", 1)[-1]
                    not in {
                        name for name, _payload, _tag in descriptor.variants
                    }
                    for variant, _target in terminator.cases
                ):
                    return False
            elif terminator.kind == "return":
                pass
            else:
                return False
        return bool(mir_function.blocks)

    def _mir_scalar_eligible(
        self,
        function: HIRFunction,
        mir_function: GeneralMIRFunction,
    ) -> bool:
        return (
            self._mir_cfg_eligible(function, mir_function)
            and len(mir_function.blocks) == 1
            and mir_function.blocks[0].terminator.kind == "return"
        )

    def _mir_scalar_temp(self, value: str) -> str:
        return f"__merlo_mir_{value}"

    def _mir_literal(
        self,
        type_name: str,
        value: object,
        *,
        owned: bool = False,
    ) -> str:
        if type_name == "Bool":
            return "true" if value else "false"
        if type_name == "Byte":
            return f"UINT8_C({int(value)})"
        if type_name == "UInt64":
            return f"UINT64_C({int(value)})"
        if type_name == "Int64":
            return f"INT64_C({int(value)})"
        if type_name in {"Float32", "Float64"}:
            return repr(float(value))
        if type_name == "Unit":
            return "0"
        if value is None:
            descriptor = self.descriptors.get(type_name)
            if descriptor is not None and descriptor.kind == "enum":
                variant = next(
                    (
                        name
                        for name, payload, _tag in descriptor.variants
                        if name == "NoneValue" and payload is None
                    ),
                    None,
                )
                if variant is not None:
                    if all(
                        payload is None
                        for _name, payload, _tag in descriptor.variants
                    ):
                        return (
                            f"MERLO_{_identifier(type_name)}"
                            f"_{variant}"
                        )
                    return (
                        f"merlo_make_{_identifier(type_name)}"
                        f"_{variant}()"
                    )
        if type_name == "Text" and isinstance(value, str):
            if owned:
                payload = value.encode("utf-8")
                values = (
                    ", ".join(f"UINT8_C({byte})" for byte in payload)
                    or "UINT8_C(0)"
                )
                return (
                    "merlo_text_literal("
                    f"(const uint8_t[]){{{values}}}, "
                    f"UINT64_C({len(payload)}))"
                )
            return self._borrowed_text_literal(value)
        if type_name == "Bytes" and isinstance(value, (bytes, bytearray, list, tuple)):
            payload = bytes(value)
            values = (
                ", ".join(f"UINT8_C({byte})" for byte in payload)
                or "UINT8_C(0)"
            )
            return (
                "merlo_bytes_literal("
                f"(const uint8_t[]){{{values}}}, "
                f"UINT64_C({len(payload)}))"
            )
        raise RepresentationCBackendError(
            f"MIR scalar literal has unsupported type: {type_name}"
        )

    def _closure_expression(
        self,
        node: Any,
        local_types: dict[str, str],
    ) -> str:
        kind = getattr(node, "kind", "")
        attrs = getattr(node, "attribute_map", {})
        children = tuple(getattr(node, "children", ()))
        if kind == "Name":
            name = attrs.get("name")
            if not isinstance(name, str) or name not in local_types:
                raise RepresentationCBackendError(
                    "closure name is not in the typed environment"
                )
            return (
                f"(*{name})"
                if local_types[name].startswith("Borrow[")
                else name
            )
        if kind == "Literal":
            type_name = getattr(node, "type_name", None)
            if not isinstance(type_name, str):
                raise RepresentationCBackendError(
                    "closure literal has no type"
                )
            descriptor = self.descriptors.get(type_name)
            return self._mir_literal(
                type_name,
                attrs.get("value"),
                owned=descriptor is not None and _is_owner(descriptor),
            )
        if kind in {"Compare", "Binary"}:
            if len(children) != 2:
                raise RepresentationCBackendError(
                    "closure comparison is malformed"
                )
            operator = attrs.get("operators", attrs.get("operator"))
            if isinstance(operator, (list, tuple)):
                operator = operator[0] if operator else None
            symbol = {
                "Eq": "==",
                "NotEq": "!=",
                "Lt": "<",
                "LtE": "<=",
                "Gt": ">",
                "GtE": ">=",
                "Add": "+",
                "Sub": "-",
                "Mul": "*",
                "Div": "/",
            }.get(str(operator))
            if symbol is None:
                raise RepresentationCBackendError(
                    f"unsupported closure operator: {operator}"
                )
            left = self._closure_expression(children[0], local_types)
            right = self._closure_expression(children[1], local_types)
            return f"(({left}) {symbol} ({right}))"
        if kind == "Boolean":
            if len(children) != 2:
                raise RepresentationCBackendError(
                    "closure boolean is malformed"
                )
            operator = attrs.get("operators", attrs.get("operator"))
            if isinstance(operator, (list, tuple)):
                operator = operator[0] if operator else None
            symbol = {"And": "&&", "Or": "||"}.get(str(operator))
            if symbol is None:
                raise RepresentationCBackendError(
                    f"unsupported closure boolean: {operator}"
                )
            return " ".join(
                (
                    f"({self._closure_expression(children[0], local_types)})",
                    symbol,
                    f"({self._closure_expression(children[1], local_types)})",
                )
            )
        if kind == "Unary":
            if len(children) != 1:
                raise RepresentationCBackendError(
                    "closure unary expression is malformed"
                )
            operator = attrs.get("operator")
            symbol = {"Not": "!", "USub": "-", "UAdd": "+"}.get(str(operator))
            if symbol is None:
                raise RepresentationCBackendError(
                    f"unsupported closure unary operator: {operator}"
                )
            return f"({symbol}{self._closure_expression(children[0], local_types)})"
        if kind == "BytesTextOperation":
            if len(children) != 1:
                raise RepresentationCBackendError(
                    "closure bytes/text operation is malformed"
                )
            callee = attrs.get("callee")
            if not isinstance(callee, str) or not callee.endswith(".len"):
                raise RepresentationCBackendError(
                    f"unsupported closure bytes/text operation: {callee}"
                )
            receiver = self._closure_expression(children[0], local_types)
            return f"({receiver}).length"
        if kind == "FieldAccess":
            if len(children) != 1 or not isinstance(attrs.get("field"), str):
                raise RepresentationCBackendError(
                    "closure field access is malformed"
                )
            receiver = self._closure_expression(children[0], local_types)
            return f"({receiver}).{attrs['field']}"
        if kind in {"DirectCall", "CallbackCall"}:
            callee = attrs.get("callee")
            if not isinstance(callee, str):
                raise RepresentationCBackendError(
                    "closure call has no callee"
                )
            arguments = ", ".join(
                self._closure_expression(child, local_types)
                for child in children
            )
            if kind == "CallbackCall":
                callee_type = local_types.get(callee)
                if callee_type is None:
                    raise RepresentationCBackendError(
                        f"closure callback target is unknown: {callee}"
                    )
                pointer = (
                    callee
                    if callee_type.startswith("Borrow[")
                    else f"&{callee}"
                )
                return (
                    f"({pointer})->call(({pointer})->environment"
                    f"{', ' if arguments else ''}{arguments})"
                )
            return f"merlo_fn_{_identifier(callee)}({arguments})"
        raise RepresentationCBackendError(
            f"unsupported typed closure expression: {kind}"
        )

    def _mir_contract_expression(
        self,
        node: Any,
        *,
        result_expression: str | None = None,
    ) -> str:
        kind = getattr(node, "kind", "")
        attrs = getattr(node, "attribute_map", {})
        children = tuple(getattr(node, "children", ()))
        if kind == "Name":
            name = attrs.get("name")
            if not isinstance(name, str):
                raise RepresentationCBackendError("MIR contract name is malformed")
            if name == "result":
                if result_expression is None:
                    raise RepresentationCBackendError(
                        "MIR contract result is unavailable"
                    )
                return f"({result_expression})"
            return name
        if kind == "Literal":
            return self._mir_literal(
                getattr(node, "type_name", None) or "Unit",
                attrs.get("value"),
            )
        if kind == "Compare":
            operators = tuple(attrs.get("operators", ()))
            if len(operators) != len(children) - 1:
                raise RepresentationCBackendError("MIR contract compare is malformed")
            symbols = {
                "Eq": "==",
                "NotEq": "!=",
                "Lt": "<",
                "LtE": "<=",
                "Gt": ">",
                "GtE": ">=",
            }
            try:
                return " && ".join(
                    f"({self._mir_contract_expression(children[index], result_expression=result_expression)} "
                    f"{symbols[str(operator)]} "
                    f"{self._mir_contract_expression(children[index + 1], result_expression=result_expression)})"
                    for index, operator in enumerate(operators)
                )
            except KeyError as exc:
                raise RepresentationCBackendError(
                    "MIR contract compare operator is unsupported"
                ) from exc
        if kind in {"BoolOp", "Boolean"}:
            if not children:
                raise RepresentationCBackendError("MIR contract boolean is malformed")
            symbol = "&&" if attrs.get("operator") in {"And", "and"} else "||"
            return f" {symbol} ".join(
                f"({self._mir_contract_expression(child, result_expression=result_expression)})"
                for child in children
            )
        if kind in {"BinOp", "Binary"}:
            if len(children) != 2:
                raise RepresentationCBackendError("MIR contract binary is malformed")
            symbols = {
                "Add": "+",
                "Sub": "-",
                "Mult": "*",
                "Div": "/",
                "Mod": "%",
            }
            operator = attrs.get("operator")
            if operator not in symbols:
                raise RepresentationCBackendError(
                    "MIR contract binary operator is unsupported"
                )
            return (
                f"({self._mir_contract_expression(children[0], result_expression=result_expression)} "
                f"{symbols[operator]} "
                f"{self._mir_contract_expression(children[1], result_expression=result_expression)})"
            )
        if kind == "Unary":
            if len(children) != 1:
                raise RepresentationCBackendError("MIR contract unary is malformed")
            symbol = {
                "Not": "!",
                "USub": "-",
                "UAdd": "+",
            }.get(attrs.get("operator"))
            if symbol is None:
                raise RepresentationCBackendError(
                    "MIR contract unary operator is unsupported"
                )
            return f"{symbol}({self._mir_contract_expression(children[0], result_expression=result_expression)})"
        if kind == "ScalarCast":
            if len(children) != 1:
                raise RepresentationCBackendError("MIR contract scalar cast is malformed")
            target_type = getattr(node, "type_name", None)
            if not isinstance(target_type, str):
                raise RepresentationCBackendError("MIR contract scalar cast type is missing")
            return (
                f"({_c_name(target_type)})"
                f"({self._mir_contract_expression(children[0], result_expression=result_expression)})"
            )
        if kind in {"Attribute", "Field"}:
            if len(children) != 1:
                raise RepresentationCBackendError("MIR contract field is malformed")
            field = attrs.get("attribute", attrs.get("field", attrs.get("name")))
            if not isinstance(field, str):
                raise RepresentationCBackendError("MIR contract field is unnamed")
            return f"({self._mir_contract_expression(children[0], result_expression=result_expression)}).{field}"
        raise RepresentationCBackendError(
            f"unsupported MIR contract expression: {kind or type(node).__name__}"
        )

    def _mir_contract_checks(
        self,
        function: HIRFunction,
        contracts: tuple[Any, ...],
        kind: str,
        *,
        result_expression: str | None = None,
        indent: str = "    ",
    ) -> list[str]:
        lines: list[str] = []
        for contract in contracts:
            condition = self._mir_contract_expression(
                contract.condition,
                result_expression=result_expression,
            )
            line = getattr(getattr(contract, "condition", None), "source", None)
            line_number = getattr(line, "line", 0)
            lines.append(
                f'{indent}if (!({condition})) merlo_contract_trap('
                f'"{kind}", "{function.name}", UINT64_C({line_number}));'
            )
        return lines
    def _mir_scalar_binary(

        self,
        type_name: str,
        operator: str,
        left: str,
        right: str,
        overflow: str | None,
    ) -> str:
        checked = {
            "Byte": {
                "Add": "merlo_checked_byte_add",
                "Sub": "merlo_checked_byte_sub",
                "Mult": "merlo_checked_byte_mult",
                "Div": "merlo_checked_byte_div",
                "FloorDiv": "merlo_checked_byte_div",
                "Mod": "merlo_checked_byte_mod",
                "LShift": "merlo_checked_byte_lshift",
                "RShift": "merlo_checked_byte_rshift",
            },
            "UInt64": {
                "Add": "merlo_checked_uint64_add",
                "Sub": "merlo_checked_uint64_sub",
                "Mult": "merlo_checked_uint64_mult",
                "Div": "merlo_checked_uint64_div",
                "FloorDiv": "merlo_checked_uint64_div",
                "Mod": "merlo_checked_uint64_mod",
                "LShift": "merlo_checked_uint64_lshift",
                "RShift": "merlo_checked_uint64_rshift",
            },
            "Int64": {
                "Add": "merlo_checked_int64_add",
                "Sub": "merlo_checked_int64_sub",
                "Mult": "merlo_checked_int64_mult",
                "Div": "merlo_checked_int64_div",
                "FloorDiv": "merlo_checked_int64_floor_div",
                "Mod": "merlo_checked_int64_mod",
                "LShift": "merlo_checked_int64_lshift",
                "RShift": "merlo_checked_int64_rshift",
            },
        }
        if overflow == "checked":
            helper = checked.get(type_name, {}).get(operator)
            if helper is not None:
                return f"{helper}({left}, {right})"
        operators = {
            "Add": "+",
            "Sub": "-",
            "Mult": "*",
            "Div": "/",
            "FloorDiv": "/",
            "Mod": "%",
            "BitOr": "|",
            "BitAnd": "&",
            "BitXor": "^",
            "LShift": "<<",
            "RShift": ">>",
        }
        symbol = operators.get(operator)
        if symbol is None:
            raise RepresentationCBackendError(
                f"MIR scalar binary operator unsupported: {operator}"
            )
        if overflow == "wrapping" and type_name == "Int64" and operator in {
            "Add",
            "Sub",
            "Mult",
        }:
            return (
                f"((int64_t)((uint64_t)({left}) {symbol} "
                f"(uint64_t)({right})))"
            )
        return f"(({left}) {symbol} ({right}))"
    def _mir_scalar_intrinsic(
        self,
        instruction: GeneralMIRInstruction,
        operands: tuple[str, ...],
    ) -> str:
        callee = str(instruction.attribute_map.get("callee", ""))
        numeric_type = str(
            instruction.attribute_map.get("numeric_type", instruction.type_name)
        )
        if len(operands) != 2:
            raise RepresentationCBackendError(
                f"MIR numeric intrinsic arity mismatch: {callee}"
            )
        overflow = instruction.attribute_map.get("overflow")
        if overflow is None:
            overflow = "checked" if callee.startswith("checked_") else "wrapping"
        return self._mir_scalar_binary(
            numeric_type,
            {
                "wrapping_add": "Add",
                "wrapping_sub": "Sub",
                "wrapping_mul": "Mult",
                "checked_add": "Add",
                "checked_sub": "Sub",
                "checked_mul": "Mult",
            }.get(callee, ""),
            operands[0],
            operands[1],
            str(overflow),
        )

    def _mir_scalar_cast(
        self,
        instruction: GeneralMIRInstruction,
        operand: str,
    ) -> str:
        target = instruction.type_name
        if target is None or len(instruction.operand_type_ids) != 1:
            raise RepresentationCBackendError("MIR scalar cast is malformed")
        source = self.hir.type_context.render(instruction.operand_type_ids[0])
        if target == "Byte" and source in {"UInt64", "Int64", "Byte"}:
            return f"merlo_cast_byte((uint64_t)({operand}))"
        if target == "Byte" and source in {"Float32", "Float64"}:
            return f"merlo_cast_byte_from_float64((double)({operand}))"
        if target == "Int64" and source in {"Float32", "Float64"}:
            return f"merlo_cast_int64_from_float64((double)({operand}))"
        if target == "Int64" and source in {"Byte", "UInt64"}:
            return f"merlo_cast_int64((uint64_t)({operand}))"
        if target == "UInt64" and source in {"Float32", "Float64"}:
            return f"merlo_cast_uint64_from_float64((double)({operand}))"
        if target == "UInt64" and source == "Int64":
            return f"merlo_cast_uint64((int64_t)({operand}))"
        return f"(({_c_name(target)})({operand}))"

    def _mir_callable_c_expression(
        self,
        expression: str,
        parameter: str,
    ) -> str:
        try:
            node = getattr(_python_ast, "parse")(expression, mode="eval").body
        except SyntaxError as exc:
            raise RepresentationCBackendError(
                f"invalid MIR collection callable: {expression}"
            ) from exc
        binary = {
            _python_ast.Add: "+",
            _python_ast.Sub: "-",
            _python_ast.Mult: "*",
            _python_ast.Div: "/",
            _python_ast.FloorDiv: "/",
            _python_ast.Mod: "%",
            _python_ast.BitOr: "|",
            _python_ast.BitAnd: "&",
            _python_ast.BitXor: "^",
            _python_ast.LShift: "<<",
            _python_ast.RShift: ">>",
        }
        compare = {
            _python_ast.Eq: "==",
            _python_ast.NotEq: "!=",
            _python_ast.Lt: "<",
            _python_ast.LtE: "<=",
            _python_ast.Gt: ">",
            _python_ast.GtE: ">=",
        }

        def render(current: _python_ast.expr) -> str:
            if isinstance(current, _python_ast.Name):
                if current.id in {"__item", parameter}:
                    return parameter
                raise RepresentationCBackendError(
                    f"MIR collection callable captures name: {current.id}"
                )
            if isinstance(current, _python_ast.Constant):
                if isinstance(current.value, bool):
                    return "true" if current.value else "false"
                if isinstance(current.value, int):
                    return (
                        f"INT64_C({current.value})"
                        if current.value < 0
                        else f"UINT64_C({current.value})"
                    )
                if isinstance(current.value, float):
                    return repr(current.value)
                raise RepresentationCBackendError(
                    "MIR collection callable literal is unsupported"
                )
            if isinstance(current, _python_ast.Attribute):
                return f"({render(current.value)}).{current.attr}"
            if isinstance(current, _python_ast.BinOp):
                operator = binary.get(type(current.op))
                if operator is None:
                    raise RepresentationCBackendError(
                        "MIR collection callable binary operator is unsupported"
                    )
                return f"({render(current.left)} {operator} {render(current.right)})"
            if isinstance(current, _python_ast.BoolOp):
                operator = "&&" if isinstance(current.op, _python_ast.And) else "||"
                return f" {operator} ".join(
                    f"({render(item)})" for item in current.values
                )
            if isinstance(current, _python_ast.UnaryOp):
                if isinstance(current.op, _python_ast.Not):
                    return f"!({render(current.operand)})"
                if isinstance(current.op, _python_ast.USub):
                    return f"-({render(current.operand)})"
                if isinstance(current.op, _python_ast.UAdd):
                    return f"+({render(current.operand)})"
                raise RepresentationCBackendError(
                    "MIR collection callable unary operator is unsupported"
                )
            if isinstance(current, _python_ast.Compare):
                if len(current.ops) != len(current.comparators):
                    raise RepresentationCBackendError(
                        "MIR collection callable comparison is malformed"
                    )
                parts = []
                left = current.left
                for operator_node, right in zip(
                    current.ops,
                    current.comparators,
                    strict=True,
                ):
                    operator = compare.get(type(operator_node))
                    if operator is None:
                        raise RepresentationCBackendError(
                            "MIR collection callable comparison is unsupported"
                        )
                    parts.append(f"({render(left)} {operator} {render(right)})")
                    left = right
                return " && ".join(parts)
            if isinstance(current, _python_ast.Call):
                if (
                    not isinstance(current.func, _python_ast.Name)
                    or len(current.args) != 1
                ):
                    raise RepresentationCBackendError(
                        "MIR collection callable function is unsupported"
                    )
                callee = next(
                    (
                        item
                        for item in self.hir.functions
                        if item.name == current.func.id
                    ),
                    None,
                )
                if callee is None:
                    raise RepresentationCBackendError(
                        f"MIR collection callable target is unknown: {current.func.id}"
                    )
                return (
                    f"merlo_fn_{_identifier(callee.name)}"
                    f"({render(current.args[0])})"
                )
            raise RepresentationCBackendError(
                f"MIR collection callable expression is unsupported: {expression}"
            )

        return render(node)

    def _emit_mir_scalar_function(
        self,
        function: HIRFunction,
        mir_function: GeneralMIRFunction,
    ) -> str:
        block = mir_function.blocks[0]
        local_types: dict[str, str] = {}
        parameter_names = {item.name for item in function.parameters}
        for instruction in block.instructions:
            if instruction.op == "store_local":
                name = instruction.attribute_map.get("name")
                if isinstance(name, str) and name not in parameter_names:
                    if instruction.type_name is None:
                        raise RepresentationCBackendError(
                            f"MIR local has no type: {name}"
                        )
                    local_types[name] = instruction.type_name
        declarations = [
            f"    {_c_name(type_name)} {name} = {{0}};"
            for name, type_name in local_types.items()
        ]
        values: dict[str, str] = {}
        lines = [self._function_signature(function) + " {"]
        lines.extend(declarations)

        def value_of(value: str) -> str:
            try:
                return values[value]
            except KeyError as exc:
                raise RepresentationCBackendError(
                    f"MIR scalar uses undefined value: {value}"
                ) from exc

        def define(instruction: GeneralMIRInstruction, expression: str) -> None:
            if instruction.result is None:
                return
            if instruction.type_name is None:
                raise RepresentationCBackendError(
                    f"MIR scalar result has no type: {instruction.op}"
                )
            temporary = self._mir_scalar_temp(instruction.result)
            lines.append(
                f"    {_c_name(instruction.type_name)} {temporary} = {expression};"
            )
            values[instruction.result] = temporary

        for instruction in block.instructions:
            operands = tuple(value_of(item) for item in instruction.operands)
            attrs = instruction.attribute_map
            if instruction.op == "const":
                define(
                    instruction,
                    self._mir_literal(instruction.type_name or "Unit", attrs.get("value")),
                )
            elif instruction.op == "load_local":
                name = attrs.get("name")
                if not isinstance(name, str):
                    raise RepresentationCBackendError("MIR load_local has no name")
                define(instruction, name)
            elif instruction.op == "store_local":
                name = attrs.get("name")
                if not isinstance(name, str) or len(operands) != 1:
                    raise RepresentationCBackendError("MIR store_local is malformed")
                lines.append(f"    {name} = {operands[0]};")
            elif instruction.op == "binary":
                if len(operands) != 2:
                    raise RepresentationCBackendError("MIR binary is malformed")
                overflow = attrs.get("overflow", attrs.get("signed_overflow"))
                if (
                    attrs.get("division_by_zero") == "trap"
                    or attrs.get("shift_range") == "checked"
                ):
                    overflow = "checked"
                define(
                    instruction,
                    self._mir_scalar_binary(
                        instruction.type_name or "",
                        str(attrs.get("operator", "")),
                        operands[0],
                        operands[1],
                        str(overflow) if overflow is not None else None,
                    ),
                )
            elif instruction.op == "boolean":
                if not operands:
                    raise RepresentationCBackendError("MIR boolean has no operands")
                symbol = "&&" if attrs.get("operator") == "And" else "||"
                define(instruction, f" {symbol} ".join(f"({item})" for item in operands))
            elif instruction.op == "compare":
                operators = tuple(attrs.get("operators", ()))
                if len(operators) != len(operands) - 1:
                    raise RepresentationCBackendError("MIR compare is malformed")
                symbols = {
                    "Eq": "==",
                    "NotEq": "!=",
                    "Lt": "<",
                    "LtE": "<=",
                    "Gt": ">",
                    "GtE": ">=",
                }
                comparisons: list[str] = []
                for index, operator in enumerate(operators):
                    left_type = self.hir.type_context.render(
                        instruction.operand_type_ids[index]
                    )
                    right_type = self.hir.type_context.render(
                        instruction.operand_type_ids[index + 1]
                    )
                    if left_type == right_type == "Text" and operator in {"Eq", "NotEq"}:
                        equal = (
                            f"merlo_text_equal_values({operands[index]}, "
                            f"{operands[index + 1]})"
                        )
                        comparisons.append(
                            equal if operator == "Eq" else f"!({equal})"
                        )
                    else:
                        comparisons.append(
                            f"({operands[index]} {symbols[str(operator)]} "
                            f"{operands[index + 1]})"
                        )
                define(instruction, " && ".join(comparisons))
            elif instruction.op == "unary":
                if len(operands) != 1:
                    raise RepresentationCBackendError("MIR unary is malformed")
                operator = str(attrs.get("operator", ""))
                if operator == "Not":
                    expression = f"!({operands[0]})"
                elif operator == "USub" and instruction.type_name == "Int64":
                    expression = f"merlo_checked_int64_neg({operands[0]})"
                elif operator == "USub":
                    expression = f"-({operands[0]})"
                elif operator == "UAdd":
                    expression = f"+({operands[0]})"
                elif operator == "Invert":
                    expression = f"~({operands[0]})"
                else:
                    raise RepresentationCBackendError(
                        f"MIR unary operator unsupported: {operator}"
                    )
                define(instruction, expression)
            elif instruction.op == "numeric_intrinsic":
                define(instruction, self._mir_scalar_intrinsic(instruction, operands))
            elif instruction.op == "scalar_cast":
                if len(operands) != 1:
                    raise RepresentationCBackendError("MIR scalar cast is malformed")
                define(instruction, self._mir_scalar_cast(instruction, operands[0]))
            elif instruction.op == "construct_record":
                descriptor = self.descriptors.get(instruction.type_name or "")
                if descriptor is None or len(operands) != len(descriptor.fields):
                    raise RepresentationCBackendError("MIR record construction is malformed")
                define(
                    instruction,
                    f"merlo_make_{_identifier(instruction.type_name)}"
                    f"({', '.join(operands)})",
                )
            elif instruction.op == "load_field":
                field = attrs.get("field")
                if len(operands) != 1 or not isinstance(field, str):
                    raise RepresentationCBackendError("MIR load_field is malformed")
                define(instruction, f"({operands[0]}).{field}")
            elif instruction.op == "store_field":
                target = attrs.get("target")
                if len(operands) != 1 or not isinstance(target, str):
                    raise RepresentationCBackendError("MIR store_field is malformed")
                lines.append(f"    {target} = {operands[0]};")
            else:
                raise RepresentationCBackendError(
                    f"unsupported MIR scalar operation: {instruction.op}"
                )
        if block.terminator.value is None:
            lines.append("    return;")
        else:
            lines.append(f"    return {value_of(block.terminator.value)};")
        lines.append("}")
        return "\n".join(lines)

    def _emit_mir_cfg_function(
        self,
        function: HIRFunction,
        mir_function: GeneralMIRFunction,
    ) -> str:
        parameter_names = {item.name for item in function.parameters}
        pointer_values: set[str] = set()
        moved_values: set[str] = set()
        borrowed_pointer_values: set[str] = set()
        inline_call_results: set[str] = set()
        inline_construct_results: set[str] = set()
        call_operand_ids: set[str] = set()
        pointer_call_operand_ids: set[str] = set()
        result_branch_inputs: set[str] = set()
        replacement_names: dict[str, tuple[str, str]] = {}
        replacement_index = 0
        seen_call_results: set[str] = set()
        seen_construct_results: set[str] = set()
        local_types: dict[str, str] = {}
        result_types: dict[str, str] = {}
        result_names: dict[str, str] = {}
        owned_temporaries: dict[str, tuple[str, str]] = {}
        owned_temp_index = 0
        result_instructions = {
            instruction.result: instruction
            for block in mir_function.blocks
            for instruction in block.instructions
            if instruction.result is not None
        }
        block_by_id = {block.id: block for block in mir_function.blocks}
        block_index = {
            block.id: index for index, block in enumerate(mir_function.blocks)
        }
        successors = {
            block.id: tuple(block.terminator.targets)
            for block in mir_function.blocks
        }

        def reachable(start: str, goal: str) -> bool:
            pending = [start]
            seen: set[str] = set()
            while pending:
                current = pending.pop()
                if current == goal:
                    return True
                if current in seen:
                    continue
                seen.add(current)
                pending.extend(successors.get(current, ()))
            return False
        def case_reachable(start: str, goal: str) -> bool:
            pending = [start]
            seen: set[str] = set()
            while pending:
                current = pending.pop()
                if current == goal:
                    return True
                if current in seen or "_match_join" in current:
                    continue
                seen.add(current)
                pending.extend(successors.get(current, ()))
            return False

        loop_pairs = [
            (
                block.id,
                block.terminator.targets[0],
                block.terminator.targets[1],
            )
            for block in mir_function.blocks
            if (
                any(
                    marker in block.id
                    for marker in (
                        "_while_condition",
                        "_for_condition",
                        "_file_lines_condition",
                    )
                )
                and block.terminator.kind == "branch"
                and len(block.terminator.targets) == 2
            )
        ]
        loop_source_positions = {
            block.id: (
                str(block.instructions[0].source.path),
                block.instructions[0].source.line,
                block.instructions[0].source.column,
            )
            for block in mir_function.blocks
            if any(
                marker in block.id
                for marker in (
                    "_while_condition",
                    "_for_condition",
                    "_file_lines_condition",
                )
            )
            and block.instructions
        }
        control_targets: dict[tuple[str, str], str] = {}
        for block in mir_function.blocks:
            for instruction in block.instructions:
                if instruction.op not in {"break", "continue"}:
                    continue
                break_source = instruction.source
                break_position = (
                    (
                        str(break_source.path),
                        break_source.line,
                        break_source.column,
                    )
                    if break_source is not None
                    else None
                )
                candidates = [
                    pair
                    for pair in loop_pairs
                    if reachable(pair[1], block.id)
                    and (
                        break_position is None
                        or pair[0] not in loop_source_positions
                        or loop_source_positions[pair[0]] <= break_position
                    )
                ]
                if not candidates:
                    continue
                condition, _body, exit_block = max(
                    candidates,
                    key=lambda pair: (
                        loop_source_positions.get(
                            pair[0],
                            ("", -1, -1),
                        ),
                        block_index[pair[0]],
                    ),
                )
                control_targets[(block.id, instruction.op)] = (
                    exit_block if instruction.op == "break" else condition
                )
        stored_local_names = {
            str(instruction.attribute_map["name"])
            for block in mir_function.blocks
            for instruction in block.instructions
            if instruction.op == "store_local"
            and isinstance(instruction.attribute_map.get("name"), str)
        }
        match_payload_bindings: dict[
            str, dict[str, tuple[str, str, str]]
        ] = {}
        file_line_bindings: dict[str, str] = {}
        match_payload_names: set[str] = set()
        match_cases: list[tuple[str, str, str, str]] = []
        direct_case_bindings: dict[
            str, dict[str, tuple[str, str, str]]
        ] = {}
        for block in mir_function.blocks:
            terminator = block.terminator
            if terminator.kind != "switch" or terminator.value is None:
                continue
            switch_type = next(
                (
                    instruction.type_name
                    for candidate_block in mir_function.blocks
                    for instruction in candidate_block.instructions
                    if instruction.result == terminator.value
                ),
                None,
            )
            switch_descriptor = self.descriptors.get(switch_type or "")
            if switch_descriptor is None or switch_descriptor.kind != "enum":
                continue
            for variant, target in terminator.cases:
                variant_name = variant.rsplit(".", 1)[-1]
                payload = next(
                    (
                        payload
                        for name, payload, _tag in switch_descriptor.variants
                        if name == variant_name
                    ),
                    None,
                )
                if payload is None or payload == "Unit":
                    continue
                match_cases.append(
                    (terminator.value, variant_name, payload, target)
                )
                target_block = block_by_id.get(target)
                if target_block is None:
                    continue
                candidates: dict[str, tuple[str, str, str]] = {}
                for instruction in target_block.instructions:
                    name = instruction.attribute_map.get("name")
                    if (
                        instruction.op == "load_local"
                        and isinstance(name, str)
                        and name not in parameter_names
                        and name not in stored_local_names
                    ):
                        candidates[name] = (
                            terminator.value,
                            variant_name,
                            payload,
                        )
                direct_case_bindings[target] = candidates
                match_payload_names.update(candidates)
        for _source_id, _variant, _payload, target in match_cases:
            candidates = direct_case_bindings.get(target, {})
            bindings: dict[str, tuple[str, str, str]] = {}
            for name, binding in candidates.items():
                shadowed = any(
                    other_target != target
                    and block_index.get(other_target, len(block_index))
                    < block_index.get(target, len(block_index))
                    and name in direct_case_bindings.get(other_target, {})
                    and case_reachable(other_target, target)
                    for (
                        _other_source,
                        _other_variant,
                        _other_payload,
                        other_target,
                    ) in match_cases
                )
                if not shadowed:
                    bindings[name] = binding
            match_payload_bindings[target] = bindings
        direct_match_payload_names = set(match_payload_names)
        for source_id, variant, payload, target in match_cases:
            pending = [target]
            seen: set[str] = set()
            case_bindings: dict[str, tuple[str, str, str]] = {}
            while pending:
                block_id = pending.pop()
                if block_id in seen or "_match_join" in block_id:
                    continue
                seen.add(block_id)
                block = block_by_id.get(block_id)
                if block is None:
                    continue
                for instruction in block.instructions:
                    name = instruction.attribute_map.get("name")
                    if (
                        instruction.op == "load_local"
                        and isinstance(name, str)
                        and name not in parameter_names
                        and name not in stored_local_names
                        and name not in direct_match_payload_names
                    ):
                        case_bindings[name] = (source_id, variant, payload)
                pending.extend(successors.get(block_id, ()))
            for block_id in seen:
                block = block_by_id.get(block_id)
                if block is None:
                    continue
                bindings = match_payload_bindings.setdefault(block_id, {})
                for instruction in block.instructions:
                    name = instruction.attribute_map.get("name")
                    if (
                        instruction.op == "load_local"
                        and isinstance(name, str)
                        and name in case_bindings
                    ):
                        bindings[name] = case_bindings[name]
            match_payload_names.update(case_bindings)
        for source_id, variant, payload, target in match_cases:
            inherited = match_payload_bindings.get(target, {})
            pending = [target]
            seen: set[str] = set()
            while pending:
                block_id = pending.pop()
                if block_id in seen or "_match_join" in block_id:
                    continue
                seen.add(block_id)
                existing = match_payload_bindings.get(block_id, {})
                merged = dict(existing)
                merged.update(inherited)
                match_payload_bindings[block_id] = merged
                pending.extend(successors.get(block_id, ()))
        file_line_names = {
            str(instruction.attribute_map["target"])
            for block in mir_function.blocks
            for instruction in block.instructions
            if instruction.op == "file_line_next"
            and isinstance(instruction.attribute_map.get("target"), str)
        }
        for block in mir_function.blocks:
            for instruction in block.instructions:
                if instruction.op != "call":
                    continue
                callee = instruction.attribute_map.get("callee")
                target = next(
                    (
                        item
                        for item in self.hir.functions
                        if item.name == callee
                    ),
                    None,
                )
                if target is None:
                    continue
                for index, operand_id in enumerate(instruction.operands):
                    if (
                        index < len(target.parameters)
                        and self._parameter_is_pointer(target.parameters[index])
                    ):
                        pointer_call_operand_ids.add(operand_id)
        for block in mir_function.blocks:
            for instruction in block.instructions:
                if instruction.op == "call":
                    call_operand_ids.update(instruction.operands)
                if instruction.op == "call" and instruction.result is not None:
                    seen_call_results.add(instruction.result)
                if (
                    instruction.op == "store_local"
                    and instruction.operands
                    and instruction.operands[0] in seen_call_results
                ):
                    inline_call_results.add(instruction.operands[0])
                if instruction.op == "result_branch":
                    result_branch_inputs.update(instruction.operands)
                if (
                    instruction.op == "construct_record"
                    and instruction.result is not None
                ):
                    seen_construct_results.add(instruction.result)
                if (
                    instruction.op == "store_local"
                    and instruction.operands
                    and instruction.operands[0] in seen_construct_results
                ):
                    inline_construct_results.add(instruction.operands[0])
                attrs = instruction.attribute_map
                if instruction.op == "move_value":
                    moved_values.update(instruction.operands)
                if instruction.op == "store_local":
                    target = attrs.get("name", attrs.get("target"))
                    if (
                        isinstance(target, str)
                        and "." not in target
                        and target not in parameter_names
                        and instruction.type_name is not None
                    ):
                        local_types.setdefault(target, instruction.type_name)
                    reassignment_target = attrs.get("target")
                    reassignment_type = self.descriptors.get(
                        instruction.type_name
                    )
                    if (
                        isinstance(reassignment_target, str)
                        and reassignment_type is not None
                        and _is_owner(reassignment_type)
                    ):
                        replacement_index += 1
                        replacement = f"__merlo_replacement_{replacement_index}"
                        replacement_names[instruction.operands[0]] = (
                            replacement,
                            instruction.type_name,
                        )
                        local_types[replacement] = instruction.type_name
                if instruction.result is None or instruction.type_name is None:
                    continue
                descriptor = self.descriptors.get(instruction.type_name)
                local_name = attrs.get("name")
                local_parameter = next(
                    (
                        parameter
                        for parameter in function.parameters
                        if parameter.name == local_name
                    ),
                    None,
                )
                target_function = next(
                    (
                        item
                        for item in self.hir.functions
                        if item.name == local_name
                    ),
                    None,
                )
                pointer_result = (
                    pointer_type(instruction.type_name) is not None
                    or (
                        instruction.type_id is not None
                        and self.hir.type_context.resolve(
                            instruction.type_id
                        ).constructor
                        == "Borrow"
                    )
                    or (
                        instruction.result in pointer_call_operand_ids
                        and instruction.op == "load_local"
                        and not (
                            target_function is not None
                            and descriptor is not None
                            and descriptor.kind in {"callback", "closure"}
                        )
                    )
                    or (
                        instruction.op == "load_local"
                        and local_parameter is not None
                        and self._parameter_is_pointer(local_parameter)
                    )
                    or (
                        descriptor is not None
                        and (
                            _is_owner(descriptor)
                            or instruction.op == "file_line_next"
                            or (
                                instruction.op == "load_local"
                                and local_name in file_line_names
                            )
                        )
                        and (
                            attrs.get("result_ownership")
                            in {"borrow", "borrow_mut"}
                            or (
                                instruction.op == "load_field"
                                and instruction.operands
                                and descriptor.kind
                                in {
                                    "text",
                                    "vec",
                                    "box",
                                    "map",
                                    "record",
                                    "enum",
                                }
                            )
                            or instruction.op == "file_line_next"
                            or instruction.op == "bounds_checked_index"
                            or (
                                instruction.op == "const"
                                and descriptor.kind == "text"
                            )
                            or (
                                instruction.op == "load_local"
                                and local_name in match_payload_names
                            )
                            or (
                                instruction.op == "load_local"
                                and not (
                                    target_function is not None
                                    and descriptor.kind in {"callback", "closure"}
                                )
                            )
                        )
                    )
                )
                if pointer_result:
                    pointer_values.add(instruction.result)
                    if (
                        attrs.get("result_ownership") in {"borrow", "borrow_mut"}
                        or instruction.op in {"load_field", "const", "bounds_checked_index"}
                        or (
                            instruction.op == "load_local"
                            and local_name in match_payload_names
                        )
                        or (
                            instruction.op == "load_local"
                            and local_parameter is not None
                            and local_parameter.ownership != "owned"
                        )
                    ):
                        borrowed_pointer_values.add(instruction.result)
                else:
                    result_types[instruction.result] = instruction.type_name
        owned_call_results = {
            instruction.result
            for block in mir_function.blocks
            for instruction in block.instructions
            if (
                instruction.op == "call"
                and instruction.result is not None
                and instruction.result in result_types
                and self.descriptors.get(instruction.type_name or "") is not None
                and _is_owner(self.descriptors[instruction.type_name or ""])
            )
        }
        for value, type_name in result_types.items():
            descriptor = self.descriptors.get(type_name)
            if (
                value in call_operand_ids
                and value not in result_names
                and descriptor is not None
                and _is_owner(descriptor)
            ):
                owned_temp_index += 1
                temporary = f"__merlo_owned_temp_{owned_temp_index}"
                result_names[value] = temporary
                owned_temporaries[value] = (temporary, type_name)
        returning_locals: set[str] = set()
        for block in mir_function.blocks:
            if block.terminator.kind != "return" or block.terminator.value is None:
                continue
            for instruction in block.instructions:
                if (
                    instruction.op == "load_local"
                    and instruction.result == block.terminator.value
                    and isinstance(instruction.attribute_map.get("name"), str)
                ):
                    returning_locals.add(str(instruction.attribute_map["name"]))
        explicit_drop_local_blocks: dict[str, set[str]] = {}
        for block in mir_function.blocks:
            for instruction in block.instructions:
                if (
                    instruction.op == "drop_value"
                    and isinstance(instruction.attribute_map.get("local"), str)
                ):
                    local = str(instruction.attribute_map["local"])
                    explicit_drop_local_blocks.setdefault(local, set()).add(block.id)
        instruction_by_result = {
            instruction.result: instruction
            for block in mir_function.blocks
            for instruction in block.instructions
            if instruction.result is not None
        }

        def return_roots(block: GeneralMIRBlock) -> set[str]:
            value_id = block.terminator.value
            if value_id is None:
                return set()

            def owner(type_name: str | None) -> bool:
                descriptor = self.descriptors.get(type_name or "")
                return descriptor is not None and _is_owner(descriptor)

            def owned_operands(
                instruction: GeneralMIRInstruction,
            ) -> tuple[str, ...]:
                attrs = instruction.attribute_map
                if instruction.op == "move_value":
                    return instruction.operands
                if instruction.op == "store_local":
                    return instruction.operands if owner(instruction.type_name) else ()
                if instruction.op == "result_branch":
                    return instruction.operands
                if instruction.op == "construct_record":
                    descriptor = self.descriptors.get(instruction.type_name or "")
                    if descriptor is None:
                        return ()
                    return tuple(
                        operand
                        for operand, (_name, type_name, _ownership) in zip(
                            instruction.operands,
                            descriptor.fields,
                        )
                        if owner(type_name)
                    )
                if instruction.op in {"construct_enum", "call"}:
                    callee = attrs.get("callee")
                    if not isinstance(callee, str):
                        return ()
                    target = next(
                        (item for item in self.hir.functions if item.name == callee),
                        None,
                    )
                    if target is not None:
                        return tuple(
                            operand
                            for operand, parameter in zip(
                                instruction.operands,
                                target.parameters,
                            )
                            if parameter.ownership in {"owned", "consuming"}
                        )
                    descriptor = self.descriptors.get(instruction.type_name or "")
                    if descriptor is not None and descriptor.kind == "enum":
                        variant = callee.rsplit(".", 1)[-1]
                        payload = next(
                            (
                                payload
                                for name, payload, _tag in descriptor.variants
                                if name == variant
                            ),
                            None,
                        )
                        if payload is not None and payload != "Unit" and owner(payload):
                            return instruction.operands[:1]
                if instruction.op == "primitive_call":
                    callee = attrs.get("callee")
                    if isinstance(callee, str) and callee.endswith(".finish"):
                        return instruction.operands[:1]
                return ()

            pending = [value_id]
            seen: set[str] = set()
            roots: set[str] = set()
            bindings = match_payload_bindings.get(block.id, {})
            while pending:
                current = pending.pop()
                if current in seen:
                    continue
                seen.add(current)
                instruction = instruction_by_result.get(current)
                if instruction is None:
                    continue
                if instruction.op == "load_local":
                    name = instruction.attribute_map.get("name")
                    if isinstance(name, str):
                        binding = bindings.get(name)
                        if binding is not None:
                            pending.append(binding[0])
                        else:
                            roots.add(name)
                pending.extend(owned_operands(instruction))
            return roots

        protected_return_locals = {
            block.id: return_roots(block)
            for block in mir_function.blocks
            if block.terminator.kind == "return"
        }
        host_temporary_types: dict[str, str] = {}
        for block in mir_function.blocks:
            for instruction in block.instructions:
                callee = instruction.attribute_map.get("callee")
                if (
                    instruction.result is None
                    or not (
                        instruction.op == "file_open_read"
                        or (
                            instruction.op == "call"
                            and isinstance(callee, str)
                            and (
                                callee.startswith("fs.")
                                or callee.startswith("network.")
                                or callee.startswith("env.")
                                or callee.startswith("clock.")
                                or callee.startswith("random.")
                                or callee.startswith("process.")
                            )
                        )
                    )
                    or instruction.type_name is None
                ):
                    continue
                result_parts = self._result_parts(instruction.type_name)
                if result_parts is not None and result_parts[0] != "Unit":
                    host_temporary_types[instruction.result] = result_parts[0]
        lines = [self._function_signature(function) + " {"]
        lines.extend(
            f"    {_c_name(type_name)} {name} = {{0}};"
            for name, type_name in local_types.items()
        )
        lines.extend(
            f"    {_c_name(type_name)} "
            f"{result_names.get(value, self._mir_scalar_temp(value))} = {{0}};"
            for value, type_name in result_types.items()
        )
        lines.extend(
            f"    {_c_name(type_name)} __merlo_host_value_{value} = {{0}};"
            for value, type_name in host_temporary_types.items()
        )
        lines.extend(
            self._mir_contract_checks(
                function,
                tuple(function.requirements),
                "require",
            )
        )
        values: dict[str, str] = {}
        callables: dict[str, GeneralMIRInstruction] = {}
        pending_drops: dict[str, list[tuple[str, str]]] = {}
        local_value_names: dict[str, str] = {}
        # A moved owner is zeroed, but branch-local cleanup must still run on
        # every path; explicit close only suppresses its same-block drop.
        consumed_locals: set[str] = set()
        consumed_local_blocks: dict[str, set[str]] = {}
        closed_local_blocks: dict[str, set[str]] = {}
        consumed_values: set[str] = set()
        pending_receiver_drops: dict[str, list[tuple[str, str]]] = {}
        active_match_bindings: dict[str, tuple[str, str, str]] = {}

        def value_of(value: str) -> str:
            try:
                return values[value]
            except KeyError as exc:
                raise RepresentationCBackendError(
                    f"MIR CFG uses undefined value: {value}"
                ) from exc

        def define(instruction: GeneralMIRInstruction, expression: str) -> None:
            if instruction.result is None:
                return
            if instruction.result in pointer_values:
                values[instruction.result] = expression
                return
            if instruction.result not in result_types:
                raise RepresentationCBackendError(
                    f"MIR CFG result has no type: {instruction.op}"
                )
            if (
                (
                    instruction.op == "call"
                    and instruction.result in inline_call_results
                )
                or (
                    instruction.op == "construct_record"
                    and instruction.result in inline_construct_results
                )
            ):
                values[instruction.result] = expression
                return
            temporary = result_names.get(
                instruction.result,
                self._mir_scalar_temp(instruction.result),
            )
            lines.append(f"    {temporary} = {expression};")
            values[instruction.result] = temporary
        def mark_consumed_local(local: str, *, closed: bool = False) -> None:
            consumed_locals.add(local)
            consumed_local_blocks.setdefault(local, set()).add(active_block_id)
            if closed:
                closed_local_blocks.setdefault(local, set()).add(active_block_id)

        def consume(
            operand_id: str,
            operand: str,
            type_name: str | None,
            *,
            force_move: bool = False,
        ) -> str:
            descriptor = self.descriptors.get(type_name or "")
            if descriptor is None or not _is_owner(descriptor):
                return operand
            address = operand if operand_id in pointer_values else f"&({operand})"
            if operand_id in borrowed_pointer_values:
                return f"merlo_clone_{_identifier(type_name)}({address})"
            if force_move or (
                operand_id not in pointer_values
                or operand_id in moved_values
            ):
                consumed_values.add(operand_id)
                local = local_value_names.get(operand_id)
                if local is not None:
                    mark_consumed_local(local)
                return f"merlo_move_{_identifier(type_name)}({address})"
            return f"merlo_clone_{_identifier(type_name)}({address})"

        def local_pointer(name: str) -> str:
            parameter = next(
                (item for item in function.parameters if item.name == name),
                None,
            )
            if parameter is not None and self._parameter_is_pointer(parameter):
                return name
            return f"&{name}"
        def method_receiver_pointer(receiver: str) -> str:
            parts = receiver.split(".")
            expression = local_pointer(parts[0])
            for field in parts[1:]:
                expression = f"&(({expression})->{field})"
            return expression

        def operand_type(
            item: GeneralMIRInstruction,
            index: int,
        ) -> str | None:
            if index >= len(item.operand_type_ids):
                return None
            return self.hir.type_context.render(item.operand_type_ids[index])

        def borrow(operand_id: str, operand: str, type_name: str | None) -> str:
            descriptor = self.descriptors.get(type_name or "")
            if descriptor is None or not _is_owner(descriptor):
                return operand
            address = operand if operand_id in pointer_values else f"&({operand})"
            return address

        def call_argument(
            parameter: Any,
            operand_id: str,
            operand: str,
            source_type: str | None,
        ) -> str:
            source_pointer = (
                operand
                if operand_id in pointer_values
                else f"&({operand})"
            )
            if parameter.type_name == "TextView" and source_type == "Text":
                return (
                    f"(MerloTextView){{ ({source_pointer})->data, "
                    f"({source_pointer})->length }}"
                )
            if parameter.type_name == "BytesView" and source_type == "Bytes":
                return (
                    f"(MerloBytesView){{ ({source_pointer})->data, "
                    f"({source_pointer})->length }}"
                )
            if parameter.type_name in {"TextView", "BytesView"} and (
                parameter.type_name == source_type
            ):
                return (
                    f"*({operand})"
                    if operand_id in pointer_values
                    else operand
                )
            if self._parameter_is_pointer(parameter):
                return (
                    operand
                    if operand_id in pointer_values
                    else f"&({operand})"
                )
            if parameter.ownership in {"consuming", "owned"} and source_type is not None:
                descriptor = self.descriptors.get(source_type)
                if (
                    descriptor is not None
                    and _is_owner(descriptor)
                    and operand_id in borrowed_pointer_values
                    and descriptor.kind == "map"
                ):
                    raise RepresentationCBackendError(
                        "cannot clone borrowed owner Map"
                    )
                if descriptor is not None and _is_owner(descriptor):
                    return consume(
                        operand_id,
                        operand,
                        source_type,
                        force_move=parameter.ownership in {"consuming", "owned"},
                    )
            return operand
        def host_address(operand_id: str, operand: str) -> str:
            return operand if operand_id in pointer_values else f"&({operand})"

        def host_result(
            instruction: GeneralMIRInstruction,
            expression: str,
            failure_condition: str,
            *,
            error_code: str = "merlo_file_error",
            text_payload: str | None = None,
        ) -> None:
            result_type = instruction.type_name
            if instruction.result is None or result_type is None:
                lines.append(f"    {expression};")
                return
            parts = self._result_parts(result_type)
            if parts is None:
                define(instruction, expression)
                return
            ok_type, error_type = parts
            result_name = result_names.get(
                instruction.result,
                self._mir_scalar_temp(instruction.result),
            )
            error = self._error_value(
                error_type,
                error_code,
                text_payload=text_payload,
            )
            if ok_type == "Unit":
                lines.append(f"    (void)({expression});")
                success = f"merlo_make_{_identifier(result_type)}_Ok()"
            else:
                raw_name = f"__merlo_host_value_{instruction.result}"
                lines.append(
                    f"    __merlo_host_value_{instruction.result} = {expression};"
                )
                success = (
                    f"merlo_make_{_identifier(result_type)}_Ok({raw_name})"
                )
            failure = f"merlo_make_{_identifier(result_type)}_Err({error})"
            lines.append(
                f"    {result_name} = ({failure_condition} ? "
                f"{failure} : {success});"
            )
            values[instruction.result] = result_name

        def host_call(
            callee: str,
            instruction: GeneralMIRInstruction,
            operands: tuple[str, ...],
        ) -> tuple[str, str] | None:
            if callee in {"fs.open_read", "fs.open_write"}:
                if len(operands) != 1:
                    raise RepresentationCBackendError(
                        f"MIR {callee} arity is invalid"
                    )
                helper = (
                    "merlo_file_open_read"
                    if callee == "fs.open_read"
                    else "merlo_file_open_write"
                )
                return (
                    f"{helper}({host_address(instruction.operands[0], operands[0])})",
                    "merlo_file_error != 0",
                )
            if callee in {"fs.read", "fs.read_text"}:
                if len(operands) != 1:
                    raise RepresentationCBackendError(
                        f"MIR {callee} arity is invalid"
                    )
                helper = (
                    "merlo_file_read_all"
                    if callee == "fs.read"
                    else "merlo_file_read_text"
                )
                return (
                    f"{helper}({host_address(instruction.operands[0], operands[0])})",
                    "merlo_file_error != 0",
                )
            if callee == "fs.read_chunk":
                if len(operands) != 2:
                    raise RepresentationCBackendError(
                        "MIR fs.read_chunk arity is invalid"
                    )
                return (
                    f"merlo_file_read_chunk("
                    f"{host_address(instruction.operands[0], operands[0])}, "
                    f"{operands[1]})",
                    "merlo_file_error != 0",
                )
            if callee in {"fs.write", "fs.write_text", "fs.write_chunk"}:
                if len(operands) != 2:
                    raise RepresentationCBackendError(
                        f"MIR {callee} arity is invalid"
                    )
                if callee == "fs.write_chunk":
                    helper = "merlo_file_write_chunk"
                    data = host_address(instruction.operands[1], operands[1])
                else:
                    helper = (
                        "merlo_file_write_text"
                        if callee == "fs.write_text"
                        else "merlo_file_write_all"
                    )
                    data = host_address(instruction.operands[1], operands[1])
                    if callee == "fs.write_text" and operand_type(instruction, 1) == "Text":
                        data = (
                            f"&((MerloTextView){{ ({data})->data, "
                            f"({data})->length }})"
                        )
                return (
                    f"{helper}("
                    f"{host_address(instruction.operands[0], operands[0])}, "
                    f"{data})",
                    (
                        "merlo_file_error != 0 || merlo_file_write_error != 0"
                        if callee != "fs.write_chunk"
                        else "merlo_file_error != 0"
                    ),
                )
            if callee in {"fs.close_read", "fs.close_write"}:
                if len(operands) != 1:
                    raise RepresentationCBackendError(
                        f"MIR {callee} arity is invalid"
                    )
                helper = (
                    "merlo_file_close"
                    if callee == "fs.close_read"
                    else "merlo_file_close_writer"
                )
                address = host_address(instruction.operands[0], operands[0])
                if instruction.operands[0] in pointer_values and address.startswith("&"):
                    address = f"&({address[1:]})"
                return f"{helper}({address})", "merlo_file_error != 0"
            if callee == "console.read":
                if operands:
                    raise RepresentationCBackendError(
                        "MIR console.read arity is invalid"
                    )
                return "merlo_console_read()", "false"
            if callee == "console.read_line":
                if operands:
                    raise RepresentationCBackendError(
                        "MIR console.read_line arity is invalid"
                    )
                return "merlo_console_read_line()", "false"
            if callee == "console.read_all":
                if operands:
                    raise RepresentationCBackendError(
                        "MIR console.read_all arity is invalid"
                    )
                return "merlo_console_read_all()", "false"
            if callee in {"env.read", "env.get"}:
                if len(operands) != 1:
                    raise RepresentationCBackendError(
                        f"MIR {callee} arity is invalid"
                    )
                return (
                    f"merlo_env_read({host_address(instruction.operands[0], operands[0])})",
                    "false",
                )
            if callee == "clock.now":
                if operands:
                    raise RepresentationCBackendError(
                        "MIR clock.now arity is invalid"
                    )
                return "merlo_clock_now()", "false"
            if callee == "random.read":
                if len(operands) != 1:
                    raise RepresentationCBackendError(
                        "MIR random.read arity is invalid"
                    )
                return f"merlo_random_read({operands[0]})", "false"
            if callee == "process.args":
                if operands:
                    raise RepresentationCBackendError(
                        "MIR process.args arity is invalid"
                    )
                return "merlo_process_args_count()", "false"
            if callee == "process.arg":
                if len(operands) != 1:
                    raise RepresentationCBackendError(
                        "MIR process.arg arity is invalid"
                    )
                return f"merlo_process_arg({operands[0]})", "false"
            if callee == "network.http_request":
                if len(operands) != 1:
                    raise RepresentationCBackendError(
                        "MIR network.http_request arity is invalid"
                    )
                return (
                    "merlo_network_http_request("
                    f"{host_address(instruction.operands[0], operands[0])})",
                    "merlo_network_error != 0",
                )
            if callee == "network.tcp_connect":
                if len(operands) != 2:
                    raise RepresentationCBackendError(
                        "MIR network.tcp_connect arity is invalid"
                    )
                return (
                    "merlo_network_tcp_connect("
                    f"{host_address(instruction.operands[0], operands[0])}, "
                    f"{operands[1]})",
                    "merlo_network_error != 0",
                )
            if callee == "network.tcp_send":
                if len(operands) != 2:
                    raise RepresentationCBackendError(
                        "MIR network.tcp_send arity is invalid"
                    )
                data = (
                    operands[1]
                    if instruction.operands[1] in pointer_values
                    else f"&({operands[1]})"
                )
                return (
                    f"merlo_network_tcp_send({operands[0]}, {data})",
                    "merlo_network_error != 0",
                )
            if callee == "network.tcp_receive":
                if len(operands) != 2:
                    raise RepresentationCBackendError(
                        "MIR network.tcp_receive arity is invalid"
                    )
                return (
                    f"merlo_network_tcp_receive({operands[0]}, {operands[1]})",
                    "merlo_network_error != 0",
                )
            if callee == "network.tcp_close":
                if len(operands) != 1:
                    raise RepresentationCBackendError(
                        "MIR network.tcp_close arity is invalid"
                    )
                return (
                    f"merlo_network_tcp_close({operands[0]})",
                    "merlo_network_error != 0",
                )
            return None
        def collection_layout(
            instruction: GeneralMIRInstruction,
            source: str,
        ) -> tuple[str, str, str, Any | None]:
            source_type = str(
                instruction.attribute_map.get("source_collection_type", "")
            )
            collection_kind = instruction.attribute_map.get("collection_kind")
            if instruction.op == "fused_collection_pipeline":
                source_instruction = instruction_by_result.get(
                    instruction.operands[0]
                )
                if (
                    source_instruction is not None
                    and source_instruction.type_name is not None
                ):
                    source_type = source_instruction.type_name
                    source_descriptor = self.descriptors.get(source_type)
                    if source_descriptor is not None:
                        collection_kind = source_descriptor.kind
            descriptor_type = source_type
            if (
                descriptor_type.startswith("Borrow[")
                and descriptor_type.endswith("]")
            ):
                descriptor_type = descriptor_type[7:-1]
            descriptor = self.descriptors.get(descriptor_type)
            if collection_kind in {"array", "vec", "slice"}:
                if (
                    descriptor is None
                    or descriptor.kind != collection_kind
                    or descriptor.element_type is None
                    or (
                        collection_kind == "array"
                        and descriptor.length is None
                    )
                ):
                    raise RepresentationCBackendError(
                        "MIR collection source is malformed"
                    )
                element_type = descriptor.element_type
            elif collection_kind in {
                "bytes",
                "bytes_view",
                "text",
                "text_view",
            }:
                if instruction.attribute_map.get("element_type") != "Byte":
                    raise RepresentationCBackendError(
                        "MIR byte collection element is malformed"
                    )
                element_type = "Byte"
            else:
                raise RepresentationCBackendError(
                    "MIR collection kind is unsupported"
                )
            source_is_pointer = (
                instruction.operands[0] in pointer_values
                or source_type.startswith("Borrow[")
            )
            source_pointer = (
                source
                if source_is_pointer
                else f"&({source})"
            )
            data = f"({source_pointer})->data"
            length = (
                f"UINT64_C({descriptor.length})"
                if collection_kind == "array"
                and descriptor is not None
                and descriptor.length is not None
                else f"({source_pointer})->length"
            )
            return element_type, data, length, descriptor

        def collection_owned_value(
            expression: str,
            expression_source: str,
            type_name: str,
        ) -> str:
            descriptor = self.descriptors.get(type_name)
            if descriptor is None or not _is_owner(descriptor):
                return expression
            try:
                parsed = getattr(_python_ast, "parse")(
                    expression_source,
                    mode="eval",
                ).body
            except SyntaxError as exc:
                raise RepresentationCBackendError(
                    f"invalid MIR collection callable: {expression_source}"
                ) from exc
            if isinstance(parsed, _python_ast.Call):
                return expression
            return (
                f"merlo_clone_{_identifier(type_name)}"
                f"(&({expression}))"
            )



        active_block_id = ""
        control_flow_condition_values = {
            block.terminator.value
            for block in mir_function.blocks
            if block.terminator.kind == "branch"
            and block.terminator.value is not None
        }
        def emit_instruction(instruction: GeneralMIRInstruction) -> None:
            attrs = instruction.attribute_map
            if instruction.op == "return":
                if len(instruction.operands) > 1:
                    raise RepresentationCBackendError(
                        "MIR return instruction is malformed"
                    )
                emit_return(
                    instruction.operands[0]
                    if instruction.operands
                    else None
                )
                return
            if instruction.op == "file_line_next":
                if len(instruction.operands) != 1:
                    raise RepresentationCBackendError(
                        "MIR file_line_next is malformed"
                    )
                source_name = local_value_names.get(instruction.operands[0])
                if source_name is not None:
                    receiver = local_pointer(source_name)
                else:
                    source = value_of(instruction.operands[0])
                    receiver = (
                        source
                        if instruction.operands[0] in pointer_values
                        else f"&({source})"
                    )
                target = attrs.get("target")
                if isinstance(target, str) and instruction.result is not None:
                    file_line_bindings[target] = instruction.result
                if instruction.result is None or instruction.type_name is None:
                    raise RepresentationCBackendError(
                        "MIR file_line_next has no result type"
                    )
                temporary = self._mir_scalar_temp(instruction.result)
                lines.append(
                    f"    {_c_name(instruction.type_name)} *{temporary} = "
                    f"merlo_file_next({receiver});"
                )
                values[instruction.result] = temporary
                return
            if instruction.op == "drop_value":
                local = attrs.get("local")
                type_name = attrs.get("type", instruction.type_name)
                if not isinstance(local, str) or not isinstance(type_name, str):
                    raise RepresentationCBackendError(
                        "MIR drop_value is malformed"
                    )
                if local in returning_locals:
                    return
                if active_block_id in consumed_local_blocks.get(local, set()):
                    return
                if local in protected_return_locals.get(active_block_id, set()):
                    return
                lines.append(
                    f"    merlo_drop_{_identifier(type_name)}"
                    f"({local_pointer(local)});"
                )
                return
            if instruction.op in {"break", "continue"}:
                target = control_targets.get((active_block_id, instruction.op))
                if target is None:
                    raise RepresentationCBackendError(
                        f"MIR {instruction.op} has no loop target"
                    )
                lines.append(
                    f"    /* {instruction.op}; */"
                )
                lines.append(f"    goto {labels[target]};")
                return
            if instruction.op in {
                "allocate",
                "allocate_deferred",
                "open_file_reader",
                "bounds_check",
                "borrow_key",
                "checked_growth",
                "checked_uint64_add",
                "copy_key_if_vacant",
                "move_value",
                "borrow_lines",
                "invalidate_line_borrow",
            }:
                return
            if instruction.op == "implicit_callable":
                if instruction.result is None:
                    raise RepresentationCBackendError(
                        "MIR implicit callable has no identity"
                    )
                callables[instruction.result] = instruction
                return
            if instruction.op == "fused_collection_pipeline":
                pipeline = tuple(attrs.get("pipeline_operations", ()))
                if pipeline not in {
                    ("where", "map"),
                    ("where", "map", "count"),
                }:
                    raise RepresentationCBackendError(
                        "MIR fused collection pipeline stages are unsupported"
                    )
                if (
                    instruction.result is None
                    or len(instruction.operands) != len(pipeline) + 1
                ):
                    raise RepresentationCBackendError(
                        "MIR fused collection pipeline is malformed"
                    )
                callable_nodes = [
                    callables.get(operand)
                    for operand in instruction.operands[1:]
                ]
                if any(node is None for node in callable_nodes):
                    raise RepresentationCBackendError(
                        "MIR fused collection callable identity is missing"
                    )
                source = value_of(instruction.operands[0])
                element_type, data, length, _source_descriptor = (
                    collection_layout(instruction, source)
                )
                where_node = callable_nodes[0]
                map_node = callable_nodes[1]
                parameter = "__merlo_collection_item"
                where_source = str(where_node.attribute_map["expression"])
                map_source = str(map_node.attribute_map["expression"])
                where_callback = self._mir_callable_c_expression(
                    where_source,
                    parameter,
                )
                map_callback = self._mir_callable_c_expression(
                    map_source,
                    parameter,
                )
                map_type = str(map_node.type_name or "")
                index = f"__merlo_collection_index_{instruction.result}"
                lines.append(
                    f"    /* __merlo_fused_collection_{instruction.result}; */"
                )
                if pipeline == ("where", "map"):
                    result_type = instruction.type_name
                    result_descriptor = self.descriptors.get(
                        result_type or ""
                    )
                    if (
                        result_descriptor is None
                        or result_descriptor.kind != "vec"
                        or result_descriptor.element_type != map_type
                    ):
                        raise RepresentationCBackendError(
                            "MIR fused collection result is malformed"
                        )
                    temporary = (
                        f"__merlo_fused_collection_result_{instruction.result}"
                    )
                    push_expression = collection_owned_value(
                        map_callback,
                        map_source,
                        map_type,
                    )
                    lines.append(
                        f"    {_c_name(result_type)} {temporary} = "
                        f"merlo_{_identifier(result_type)}_new();"
                    )
                    lines.append(
                        f"    for (uint64_t {index} = UINT64_C(0); "
                        f"{index} < {length}; ++{index}) {{"
                    )
                    lines.append(
                        f"        {_c_name(element_type)} {parameter} = "
                        f"{data}[{index}];"
                    )
                    lines.append(f"        if ({where_callback}) {{")
                    lines.append(
                        f"            merlo_{_identifier(result_type)}_push"
                        f"(&{temporary}, {push_expression});"
                    )
                    lines.append("        }")
                    lines.append("    }")
                    values[instruction.result] = temporary
                    return
                count_node = callable_nodes[2]
                map_type = str(map_node.type_name or "")
                if not self._mir_plain_value(map_type):
                    raise RepresentationCBackendError(
                        "MIR fused collection map result is not scalar"
                    )
                mapped_parameter = (
                    f"__merlo_collection_mapped_{instruction.result}"
                )
                count_callback = self._mir_callable_c_expression(
                    str(count_node.attribute_map["expression"]),
                    mapped_parameter,
                )
                temporary = self._mir_scalar_temp(instruction.result)
                lines.append(f"    {temporary} = UINT64_C(0);")
                lines.append(
                    f"    for (uint64_t {index} = UINT64_C(0); "
                    f"{index} < {length}; ++{index}) {{"
                )
                lines.append(
                    f"        {_c_name(element_type)} {parameter} = "
                    f"{data}[{index}];"
                )
                lines.append(f"        if ({where_callback}) {{")
                lines.append(
                    f"            {_c_name(map_type)} {mapped_parameter} = "
                    f"{map_callback};"
                )
                lines.append(
                    f"            if ({count_callback}) ++{temporary};"
                )
                lines.append("        }")
                lines.append("    }")
                values[instruction.result] = temporary
                return
            if instruction.op == "collection_operation":
                if instruction.result is None or len(instruction.operands) != 2:
                    raise RepresentationCBackendError(
                        "MIR collection operation is malformed"
                    )
                source = value_of(instruction.operands[0])
                callable_node = callables.get(instruction.operands[1])
                if callable_node is None:
                    raise RepresentationCBackendError(
                        "MIR collection callable identity is missing"
                    )
                element_type, data, length, _source_descriptor = (
                    collection_layout(instruction, source)
                )
                operation = attrs.get("collection_operation")
                parameter = "__merlo_collection_item"
                expression_source = str(
                    callable_node.attribute_map["expression"]
                )
                callback = self._mir_callable_c_expression(
                    expression_source,
                    parameter,
                )
                index = f"__merlo_collection_index_{instruction.result}"
                if operation == "count":
                    temporary = self._mir_scalar_temp(instruction.result)
                    lines.append(f"    {temporary} = UINT64_C(0);")
                    lines.append(
                        f"    for (uint64_t {index} = UINT64_C(0); "
                        f"{index} < {length}; ++{index}) {{"
                    )
                    lines.append(
                        f"        {_c_name(element_type)} {parameter} = "
                        f"{data}[{index}];"
                    )
                    lines.append(
                        f"        if ({callback}) ++{temporary};"
                    )
                    lines.append("    }")
                    values[instruction.result] = temporary
                    return
                result_type = instruction.type_name
                result_descriptor = self.descriptors.get(result_type or "")
                result_element_type = (
                    result_descriptor.element_type
                    if result_descriptor is not None
                    else None
                )
                if (
                    result_descriptor is None
                    or result_descriptor.kind != "vec"
                    or result_element_type is None
                ):
                    raise RepresentationCBackendError(
                        "MIR collection result is not a Vec"
                    )
                if operation == "where":
                    if (callable_node.type_name or "") != "Bool":
                        raise RepresentationCBackendError(
                            "MIR collection where callback is not Bool"
                        )
                    push_expression = collection_owned_value(
                        f"{data}[{index}]",
                        "__item",
                        element_type,
                    )
                elif operation == "map":
                    callback_type = str(
                        callable_node.attribute_map.get(
                            "callable_return_type"
                        )
                        or callable_node.type_name
                        or ""
                    )
                    if callback_type != result_element_type:
                        raise RepresentationCBackendError(
                            "MIR collection map result type is malformed"
                        )
                    push_expression = collection_owned_value(
                        callback,
                        expression_source,
                        callback_type,
                    )
                else:
                    raise RepresentationCBackendError(
                        "MIR collection operation is unsupported"
                    )
                temporary = (
                    f"__merlo_collection_result_{instruction.result}"
                )
                lines.append(
                    f"    {_c_name(result_type)} {temporary} = "
                    f"merlo_{_identifier(result_type)}_new();"
                )
                lines.append(
                    f"    for (uint64_t {index} = UINT64_C(0); "
                    f"{index} < {length}; ++{index}) {{"
                )
                lines.append(
                    f"        {_c_name(element_type)} {parameter} = "
                    f"{data}[{index}];"
                )
                if operation == "where":
                    lines.append(f"        if ({callback}) {{")
                    lines.append(
                        f"            merlo_{_identifier(result_type)}_push"
                        f"(&{temporary}, {push_expression});"
                    )
                    lines.append("        }")
                else:
                    lines.append(
                        f"        merlo_{_identifier(result_type)}_push"
                        f"(&{temporary}, {push_expression});"
                    )
                lines.append("    }")
                values[instruction.result] = temporary
                return
            operands = tuple(value_of(item) for item in instruction.operands)
            if instruction.op == "file_open_read":
                callee = attrs.get("callee")
                if not isinstance(callee, str):
                    raise RepresentationCBackendError(
                        "MIR file_open_read has no intrinsic"
                    )
                host = host_call(callee, instruction, operands)
                if host is None:
                    raise RepresentationCBackendError(
                        f"MIR file operation is unsupported: {callee}"
                    )
                expression, failure = host
                host_result(
                    instruction,
                    expression,
                    failure,
                    text_payload=(
                        host_address(instruction.operands[0], operands[0])
                        if callee in {"fs.open_read", "fs.read", "fs.read_text"}
                        and operands
                        else None
                    ),
                )
                return
            if instruction.op == "result_branch":
                if len(operands) != 1:
                    raise RepresentationCBackendError(
                        "MIR result_branch is malformed"
                    )
                result_type = operand_type(instruction, 0)
                result_descriptor = self.descriptors.get(result_type or "")
                if result_descriptor is None or result_descriptor.kind != "enum":
                    raise RepresentationCBackendError(
                        "MIR result_branch source is not an enum"
                    )
                ok_payload = next(
                    (
                        payload
                        for variant, payload, _tag in result_descriptor.variants
                        if variant == "Ok"
                    ),
                    None,
                )
                ok_tag = next(
                    (
                        tag
                        for variant, _payload, tag in result_descriptor.variants
                        if variant == "Ok"
                    ),
                    None,
                )
                if ok_tag is None or ok_payload is None:
                    raise RepresentationCBackendError(
                        "MIR result_branch source has no Ok variant"
                    )
                result_value = operands[0]
                drops = pending_drops.pop(instruction.operands[0], [])
                lines.append(
                    f"    if ({result_value}.tag != "
                    f"MERLO_{_identifier(result_type)}_Ok_TAG) {{"
                )
                for temporary, type_name in drops:
                    lines.append(
                        f"        merlo_drop_{_identifier(type_name)}"
                        f"(&{temporary});"
                    )
                target_parts = self._result_parts(function.return_type)
                source_error_type = next(
                    payload
                    for variant, payload, _tag in result_descriptor.variants
                    if variant == "Err"
                )
                compatible_error_type = (
                    target_parts is not None
                    and self._descriptor_aliases.get(
                        target_parts[1],
                        target_parts[1],
                    )
                    == self._descriptor_aliases.get(
                        source_error_type,
                        source_error_type,
                    )
                )
                if not compatible_error_type:
                    raise RepresentationCBackendError(
                        "MIR result propagation error type is incompatible"
                    )
                error_payload = f"{result_value}.payload.Err"
                lines.append(
                    f"        return merlo_make_{_identifier(function.return_type)}"
                    f"_Err({error_payload});"
                )
                lines.append("    }")
                payload = f"{result_value}.payload.Ok"
                payload_descriptor = self.descriptors.get(ok_payload)
                define(
                    instruction,
                    (
                        f"merlo_move_{_identifier(ok_payload)}"
                        f"(&{payload})"
                        if payload_descriptor is not None
                        and _is_owner(payload_descriptor)
                        else payload
                    ),
                )
                lines.append(
                    f"    {result_value}.tag = "
                    f"MERLO_{_identifier(result_type)}_MOVED_TAG;"
                )
                for temporary, type_name in drops:
                    lines.append(
                        f"    merlo_drop_{_identifier(type_name)}"
                        f"(&{temporary});"
                    )
                return
            if instruction.op == "file_lines":
                if len(operands) != 1:
                    raise RepresentationCBackendError(
                        "MIR file_lines is malformed"
                    )
                receiver = (
                    operands[0]
                    if instruction.operands[0] in pointer_values
                    else f"&({operands[0]})"
                )
                define(instruction, f"merlo_file_lines({receiver})")
                return
            if instruction.op == "vec_view":
                if len(operands) != 1:
                    raise RepresentationCBackendError(
                        "MIR Vec.view is malformed"
                    )
                if instruction.operands[0] in owned_call_results:
                    raise RepresentationCBackendError(
                        "borrowed result escapes"
                    )
                receiver = (
                    operands[0]
                    if instruction.operands[0] in pointer_values
                    else f"&({operands[0]})"
                )
                define(instruction, receiver)
                return
            if instruction.op == "vec_operation":
                callee = attrs.get("callee")
                if (
                    not isinstance(callee, str)
                    or not callee.endswith(".clone")
                    or len(operands) != 1
                ):
                    raise RepresentationCBackendError(
                        "MIR Vec operation is unsupported"
                    )
                source_type = operand_type(instruction, 0)
                descriptor = self.descriptors.get(source_type or "")
                if descriptor is None or descriptor.kind != "vec":
                    raise RepresentationCBackendError(
                        "MIR Vec clone source is invalid"
                    )
                source = (
                    operands[0]
                    if instruction.operands[0] in pointer_values
                    else f"&({operands[0]})"
                )
                define(
                    instruction,
                    f"merlo_clone_{_identifier(source_type)}({source})",
                )
                return
            if instruction.op == "vec_new":
                type_name = instruction.type_name
                descriptor = self.descriptors.get(type_name or "")
                if descriptor is None or descriptor.kind != "vec":
                    raise RepresentationCBackendError("MIR Vec.new type is invalid")
                define(
                    instruction,
                    f"merlo_{_identifier(type_name)}_new()",
                )
                return
            if instruction.op == "vec_push":
                callee = attrs.get("callee")
                if not isinstance(callee, str) or len(operands) not in {1, 2}:
                    raise RepresentationCBackendError("MIR Vec.push is malformed")
                receiver_name = callee.split(".", 1)[0]
                if len(operands) == 2:
                    receiver_id, receiver = instruction.operands[0], operands[0]
                    item_id, item = instruction.operands[1], operands[1]
                    receiver_type = operand_type(instruction, 0)
                    item_type = operand_type(instruction, 1)
                    receiver_pointer = (
                        receiver
                        if receiver_id in pointer_values
                        else f"&({receiver})"
                    )
                else:
                    receiver_id, receiver = receiver_name, local_pointer(receiver_name)
                    item_id, item = instruction.operands[0], operands[0]
                    receiver_type = local_types.get(receiver_name)
                    if receiver_type is None:
                        receiver_type = next(
                            (
                                parameter.type_name
                                for parameter in function.parameters
                                if parameter.name == receiver_name
                            ),
                            None,
                        )
                    item_type = operand_type(instruction, 0)
                    receiver_pointer = receiver
                descriptor = self.descriptors.get(str(receiver_type or ""))
                if descriptor is None or descriptor.kind != "vec":
                    raise RepresentationCBackendError("MIR Vec.push receiver is invalid")
                value = consume(item_id, item, item_type)
                lines.append(
                    f"    merlo_{_identifier(descriptor.name)}_push"
                    f"({receiver_pointer}, {value});"
                )
                return
            if instruction.op in {"vec_get", "vec_get_mut"}:
                callee = attrs.get("callee")
                if not isinstance(callee, str) or len(operands) not in {1, 2}:
                    raise RepresentationCBackendError("MIR Vec.get is malformed")
                receiver_name = callee.split(".", 1)[0]
                if (
                    instruction.op == "vec_get_mut"
                    and (
                        (
                            len(operands) == 1
                            and receiver_name not in local_types
                            and not any(
                                parameter.name == receiver_name
                                for parameter in function.parameters
                            )
                        )
                        or (
                            len(operands) == 2
                            and instruction.operands[0] in owned_call_results
                        )
                    )
                ):
                    raise RepresentationCBackendError("borrowed result escapes")
                receiver_temp: tuple[str, str] | None = None
                if len(operands) == 2:
                    receiver_id, receiver = instruction.operands[0], operands[0]
                    index = operands[1]
                    receiver_type = operand_type(instruction, 0)
                    receiver_pointer = (
                        receiver
                        if receiver_id in pointer_values
                        else f"&({receiver})"
                    )
                else:
                    index = operands[0]
                    receiver_type = local_types.get(receiver_name)
                    if receiver_type is None:
                        receiver_type = next(
                            (
                                parameter.type_name
                                for parameter in function.parameters
                                if parameter.name == receiver_name
                            ),
                            None,
                        )
                    if receiver_type is None:
                        contract = str(attrs.get("contract_symbol", ""))
                        receiver_type = contract.rsplit(".", 1)[0] or None
                        producer = next(
                            (
                                item
                                for item in self.hir.functions
                                if item.return_type == receiver_type
                                and not item.parameters
                            ),
                            None,
                        )
                        if producer is None:
                            raise RepresentationCBackendError(
                                "MIR Vec.get receiver is unknown"
                            )
                        receiver_name = f"__merlo_vec_receiver_{instruction.result}"
                        lines.append(
                            f"    {_c_name(receiver_type)} {receiver_name} = "
                            f"merlo_zero_{_identifier(receiver_type)}();"
                        )
                        lines.append(
                            f"    {receiver_name} = merlo_fn_{producer.name}();"
                        )
                        receiver_temp = (receiver_name, receiver_type)
                        receiver_pointer = f"&{receiver_name}"
                    else:
                        receiver_pointer = local_pointer(receiver_name)
                descriptor_type = str(receiver_type or "")
                if (
                    descriptor_type.startswith("Borrow[")
                    and descriptor_type.endswith("]")
                ):
                    descriptor_type = descriptor_type[7:-1]
                descriptor = self.descriptors.get(descriptor_type)
                if descriptor is None or descriptor.kind != "vec":
                    raise RepresentationCBackendError("MIR Vec.get receiver is invalid")
                expression = (
                    f"merlo_{_identifier(descriptor.name)}_get"
                    f"({receiver_pointer}, {index})"
                )
                if instruction.result not in pointer_values:
                    expression = f"*({expression})"
                define(instruction, expression)
                if receiver_temp is not None and instruction.result is not None:
                    pending_receiver_drops[instruction.result] = [receiver_temp]
                return
            if instruction.op == "vec_len":
                callee = attrs.get("callee")
                if not isinstance(callee, str):
                    raise RepresentationCBackendError("MIR Vec.len is malformed")
                if len(operands) == 1:
                    receiver_id = instruction.operands[0]
                    receiver = operands[0]
                    receiver_type = operand_type(instruction, 0)
                    receiver_name = callee.split(".", 1)[0]
                    receiver_pointer = (
                        receiver
                        if receiver_id in pointer_values
                        else f"&({receiver})"
                    )
                elif not operands:
                    receiver_name = callee.split(".", 1)[0]
                    receiver_type = next(
                        (
                            item.type_name
                            for item in function.parameters
                            if item.name == receiver_name
                        ),
                        None,
                    )
                    if receiver_type is None:
                        receiver_type = next(
                            (
                                item.type_name
                                for item in mir_function.blocks[0].instructions
                                if item.op == "store_local"
                                and item.attribute_map.get("name") == receiver_name
                            ),
                            None,
                        )
                    receiver_pointer = local_pointer(receiver_name)
                else:
                    raise RepresentationCBackendError("MIR Vec.len is malformed")
                descriptor_type = str(receiver_type or "")
                if (
                    descriptor_type.startswith("Borrow[")
                    and descriptor_type.endswith("]")
                ):
                    descriptor_type = descriptor_type[7:-1]
                descriptor = self.descriptors.get(descriptor_type)
                if descriptor is None or descriptor.kind != "vec":
                    raise RepresentationCBackendError("MIR Vec.len receiver is invalid")
                define(
                    instruction,
                    f"merlo_{_identifier(descriptor.name)}_len"
                    f"({receiver_pointer})",
                )
                return
            if instruction.op == "box_new":
                type_name = instruction.type_name
                descriptor = self.descriptors.get(type_name or "")
                if (
                    descriptor is None
                    or descriptor.kind != "box"
                    or descriptor.payload_type is None
                    or len(operands) != 1
                ):
                    raise RepresentationCBackendError("MIR Box.new is malformed")
                value = consume(
                    instruction.operands[0],
                    operands[0],
                    operand_type(instruction, 0),
                )
                define(
                    instruction,
                    f"merlo_{_identifier(type_name)}_new({value})",
                )
                return
            if instruction.op == "box_get":
                callee = attrs.get("callee")
                if not isinstance(callee, str):
                    raise RepresentationCBackendError("MIR Box.get is malformed")
                receiver_name = callee.split(".", 1)[0]
                receiver_temp: tuple[str, str] | None = None
                if len(operands) == 1:
                    receiver_id, receiver = instruction.operands[0], operands[0]
                    receiver_type = operand_type(instruction, 0)
                    receiver_pointer = (
                        receiver
                        if receiver_id in pointer_values
                        else f"&({receiver})"
                    )
                elif not operands:
                    receiver_type = next(
                        (
                            parameter.type_name
                            for parameter in function.parameters
                            if parameter.name == receiver_name
                        ),
                        None,
                    )
                    if receiver_type is None:
                        contract = str(attrs.get("contract_symbol", ""))
                        receiver_type = contract.rsplit(".", 1)[0] or None
                    producer = next(
                        (
                            item
                            for item in self.hir.functions
                            if item.return_type == receiver_type
                            and not item.parameters
                        ),
                        None,
                    )
                    if receiver_type is None or producer is None:
                        raise RepresentationCBackendError(
                            "MIR Box.get receiver is unknown"
                        )
                    receiver_name = f"__merlo_box_receiver_{instruction.result}"
                    lines.append(
                        f"    {_c_name(receiver_type)} {receiver_name} = "
                        f"merlo_zero_{_identifier(receiver_type)}();"
                    )
                    lines.append(
                        f"    {receiver_name} = merlo_fn_{producer.name}();"
                    )
                    receiver_temp = (receiver_name, receiver_type)
                    receiver_pointer = f"&{receiver_name}"
                else:
                    raise RepresentationCBackendError("MIR Box.get is malformed")
                descriptor = self.descriptors.get(str(receiver_type or ""))
                if (
                    descriptor is None
                    or descriptor.kind != "box"
                    or descriptor.payload_type is None
                ):
                    raise RepresentationCBackendError("MIR Box.get receiver is invalid")
                expression = (
                    f"merlo_{_identifier(descriptor.name)}_get"
                    f"({receiver_pointer})"
                )
                if instruction.result not in pointer_values:
                    expression = f"*({expression})"
                define(instruction, expression)
                if receiver_temp is not None and instruction.result is not None:
                    pending_receiver_drops[instruction.result] = [receiver_temp]
                return
            if instruction.op == "map_entries":
                if len(operands) != 1:
                    raise RepresentationCBackendError(
                        "MIR Map.entries is malformed"
                    )
                source_pointer = (
                    operands[0]
                    if instruction.operands[0] in pointer_values
                    else f"&({operands[0]})"
                )
                define(instruction, source_pointer)
                return
            if instruction.op == "entries_len":
                if len(operands) != 1:
                    raise RepresentationCBackendError(
                        "MIR Map.entries length is malformed"
                    )
                source_type = operand_type(instruction, 0)
                source_pointer = (
                    operands[0]
                    if instruction.operands[0] in pointer_values
                    or (source_type or "").startswith("Borrow[Map[")
                    else f"&({operands[0]})"
                )
                define(instruction, f"({source_pointer})->length")
                return
            if instruction.op == "entries_get":
                if len(operands) != 2:
                    raise RepresentationCBackendError(
                        "MIR Map entry access is malformed"
                    )
                source_type = operand_type(instruction, 0)
                source_pointer = (
                    operands[0]
                    if instruction.operands[0] in pointer_values
                    or (source_type or "").startswith("Borrow[Map[")
                    else f"&({operands[0]})"
                )
                access = (
                    f"({source_pointer})->entries[{operands[1]}]"
                )
                define(instruction, access)
                return
            if instruction.op == "map_new":
                type_name = instruction.type_name
                descriptor = self.descriptors.get(type_name or "")
                if descriptor is None or descriptor.kind != "map":
                    raise RepresentationCBackendError("MIR Map.new type is invalid")
                define(
                    instruction,
                    f"merlo_{_identifier(type_name)}_new()",
                )
                return
            if instruction.op == "map_insert":
                if len(operands) != 3:
                    raise RepresentationCBackendError("MIR Map.insert is malformed")
                map_type = operand_type(instruction, 0)
                descriptor = self.descriptors.get(map_type or "")
                if descriptor is None or descriptor.kind != "map" or descriptor.value_type is None:
                    raise RepresentationCBackendError("MIR Map.insert type is invalid")
                key = borrow(
                    instruction.operands[1],
                    operands[1],
                    operand_type(instruction, 1),
                )
                value = consume(
                    instruction.operands[2],
                    operands[2],
                    operand_type(instruction, 2),
                )
                lines.append(
                    f"    merlo_{_identifier(descriptor.name)}_insert"
                    f"({borrow(instruction.operands[0], operands[0], map_type)}, "
                    f"{key}, {value});"
                )
                return
            if instruction.op == "map_get":
                if len(operands) != 2:
                    raise RepresentationCBackendError("MIR Map.get is malformed")
                map_type = operand_type(instruction, 0)
                descriptor = self.descriptors.get(map_type or "")
                if descriptor is None or descriptor.kind != "map":
                    raise RepresentationCBackendError("MIR Map.get type is invalid")
                key = borrow(
                    instruction.operands[1],
                    operands[1],
                    operand_type(instruction, 1),
                )
                expression = (
                    f"merlo_{_identifier(descriptor.name)}_get"
                    f"({borrow(instruction.operands[0], operands[0], map_type)}, {key})"
                )
                define(instruction, expression)
                return
            if instruction.op == "map_increment":
                if len(operands) != 3:
                    raise RepresentationCBackendError("MIR Map.increment is malformed")
                map_type = operand_type(instruction, 0)
                descriptor = self.descriptors.get(map_type or "")
                if descriptor is None or descriptor.kind != "map":
                    raise RepresentationCBackendError("MIR Map.increment type is invalid")
                key = borrow(
                    instruction.operands[1],
                    operands[1],
                    operand_type(instruction, 1),
                )
                expression = (
                    f"merlo_{_identifier(descriptor.name)}_increment"
                    f"({borrow(instruction.operands[0], operands[0], map_type)}, "
                    f"{key}, {operands[2]})"
                )
                if instruction.result is None:
                    lines.append(f"    {expression};")
                else:
                    define(instruction, expression)
                return
            if instruction.op == "closure_create":
                closure_id = attrs.get("closure_id")
                captures = attrs.get("captures", ())
                if (
                    not isinstance(closure_id, str)
                    or not isinstance(captures, (list, tuple))
                    or len(captures) != len(operands)
                ):
                    raise RepresentationCBackendError(
                        "MIR closure construction is malformed"
                    )
                arguments = []
                for index, (_name, type_name, ownership) in enumerate(captures):
                    operand_id = instruction.operands[index]
                    operand = operands[index]
                    if ownership == "owned":
                        argument = (
                            operand
                            if operand_id in pointer_values
                            else f"&({operand})"
                        )
                    else:
                        argument = operand
                    arguments.append(argument)
                define(
                    instruction,
                    f"merlo_closure_make_{closure_id}"
                    f"({', '.join(arguments)})",
                )
                return
            if instruction.op == "typed_error":
                emit_instruction(replace(instruction, op="call"))
                return
            if instruction.op == "load_enum_tag":
                callee = attrs.get("callee")
                if (
                    not isinstance(callee, str)
                    or not callee.endswith(".tag")
                    or instruction.operands
                    or instruction.result is None
                ):
                    raise RepresentationCBackendError(
                        "MIR enum tag load is malformed"
                    )
                receiver_name = callee.rsplit(".", 1)[0]
                parts = receiver_name.split(".")
                receiver_type = next(
                    (
                        item.type_name
                        for item in function.parameters
                        if item.name == parts[0]
                    ),
                    local_types.get(parts[0]),
                )
                receiver = method_receiver_pointer(receiver_name)
                for field in parts[1:]:
                    descriptor = self.descriptors.get(receiver_type or "")
                    if descriptor is None or descriptor.kind != "record":
                        raise RepresentationCBackendError(
                            "MIR enum tag receiver is not a record field"
                        )
                    receiver_type = next(
                        (
                            field_type
                            for field_name, field_type, _ownership in descriptor.fields
                            if field_name == field
                        ),
                        None,
                    )
                descriptor = self.descriptors.get(receiver_type or "")
                if descriptor is None or descriptor.kind != "enum":
                    raise RepresentationCBackendError(
                        "MIR enum tag receiver is not an enum"
                    )
                tag = (
                    f"*({receiver})"
                    if all(
                        payload is None
                        for _name, payload, _tag in descriptor.variants
                    )
                    else f"({receiver})->tag"
                )
                define(instruction, tag)
                return
            if instruction.op == "call":
                callee = attrs.get("callee")
                if not isinstance(callee, str):
                    raise RepresentationCBackendError("MIR call has no callee")
                target = next(
                    (item for item in self.hir.functions if item.name == callee),
                    None,
                )
                if (
                    instruction.result in control_flow_condition_values
                    and any(
                        (
                            (
                                source_instruction := instruction_by_result.get(
                                    operand
                                )
                            )
                            is not None
                            and source_instruction.type_name is not None
                            and (
                                source_descriptor := self.descriptors.get(
                                    source_instruction.type_name
                                )
                            )
                            is not None
                            and _is_owner(source_descriptor)
                            and source_instruction.attribute_map.get(
                                "result_ownership"
                            )
                            == "owned"
                        )
                        for operand in instruction.operands
                    )
                ):
                    raise RepresentationCBackendError(
                        "control-flow expression cannot consume owned temporary"
                    )
                host = host_call(callee, instruction, operands)
                if host is not None:
                    if (
                        callee in {"fs.close_read", "fs.close_write"}
                        and instruction.operands
                    ):
                        local = local_value_names.get(instruction.operands[0])
                        if local is not None:
                            mark_consumed_local(local, closed=True)
                    expression, failure = host
                    if callee == "network.tcp_connect":
                        result_parts = self._result_parts(instruction.type_name or "")
                        if result_parts is not None:
                            ok_type, _error_type = result_parts
                            ok_descriptor = self.descriptors.get(ok_type)
                            if (
                                ok_descriptor is not None
                                and ok_descriptor.kind == "record"
                                and tuple(
                                    field_type
                                    for _field_name, field_type, _ownership
                                    in ok_descriptor.fields
                                )
                                == ("UInt64",)
                            ):
                                expression = (
                                    f"merlo_make_{_identifier(ok_type)}"
                                    f"({expression})"
                                )
                    host_result(
                        instruction,
                        expression,
                        failure,
                        error_code=(
                            "merlo_network_error"
                            if callee.startswith("network.")
                            else "merlo_file_error"
                        ),
                        text_payload=(
                            host_address(
                                instruction.operands[0],
                                operands[0],
                            )
                            if callee in {
                                "fs.open_read",
                                "fs.read",
                                "fs.read_text",
                            }
                            and operands
                            else None
                        ),
                    )
                    return
                if callee == "Path":
                    if len(operands) != 1:
                        raise RepresentationCBackendError(
                            "MIR Path constructor arity is invalid"
                        )
                    call = consume(
                        instruction.operands[0],
                        operands[0],
                        operand_type(instruction, 0),
                    )
                elif attrs.get("representation_lowering") in {
                    "option_is_none",
                    "option_is_some",
                    "result_is_err",
                    "result_is_ok",
                }:
                    if len(operands) != 1:
                        raise RepresentationCBackendError(
                            "MIR enum predicate arity is invalid"
                        )
                    enum_type = operand_type(instruction, 0)
                    descriptor = self.descriptors.get(enum_type or "")
                    if descriptor is None or descriptor.kind != "enum":
                        raise RepresentationCBackendError(
                            "MIR enum predicate source is not an enum"
                        )
                    lowering = attrs["representation_lowering"]
                    variant = {
                        "option_is_none": "NoneValue",
                        "option_is_some": "Some",
                        "result_is_err": "Err",
                        "result_is_ok": "Ok",
                    }[lowering]
                    if not any(
                        name == variant
                        for name, _payload, _tag in descriptor.variants
                    ):
                        raise RepresentationCBackendError(
                            "MIR enum predicate variant is missing"
                        )
                    value = operands[0]
                    access = (
                        f"({value})->tag"
                        if instruction.operands[0] in pointer_values
                        else f"({value}).tag"
                    )
                    call = (
                        f"({access} == "
                        f"MERLO_{_identifier(enum_type)}_{variant}_TAG)"
                    )
                elif attrs.get("representation_lowering") in {
                    "option_unwrap_clone",
                    "result_unwrap_clone",
                    "result_unwrap_err_clone",
                }:
                    if len(operands) != 1:
                        raise RepresentationCBackendError(
                            "MIR enum unwrap arity is invalid"
                        )
                    source_type = operand_type(instruction, 0)
                    descriptor = self.descriptors.get(source_type or "")
                    if descriptor is None or descriptor.kind != "enum":
                        raise RepresentationCBackendError(
                            "MIR enum unwrap source is not an enum"
                        )
                    lowering = attrs["representation_lowering"]
                    variant = {
                        "option_unwrap_clone": "Some",
                        "result_unwrap_clone": "Ok",
                        "result_unwrap_err_clone": "Err",
                    }[lowering]
                    payload_type = next(
                        (
                            payload
                            for name, payload, _tag in descriptor.variants
                            if name == variant
                        ),
                        None,
                    )
                    receiver = (
                        operands[0]
                        if instruction.operands[0] in pointer_values
                        else f"&({operands[0]})"
                    )
                    tag_access = f"({receiver})->tag"
                    suffix = _identifier(source_type)
                    diagnostic = {
                        "option_unwrap_clone": "OptionUnwrapWrongVariant",
                        "result_unwrap_clone": "ResultUnwrapWrongVariant",
                        "result_unwrap_err_clone": "ResultUnwrapErrWrongVariant",
                    }[lowering]
                    if payload_type in {None, "Unit"}:
                        payload = self._zero_expression(payload_type or "Unit")
                        failure = payload
                    else:
                        payload_access = f"({receiver})->payload.{variant}"
                        payload_descriptor = self.descriptors.get(payload_type)
                        if (
                            payload_descriptor is not None
                            and _is_owner(payload_descriptor)
                        ):
                            payload = (
                                f"merlo_clone_{_identifier(payload_type)}"
                                f"(&{payload_access})"
                            )
                        else:
                            payload = payload_access
                        failure = self._zero_expression(payload_type)
                    call = (
                        f"(({tag_access} == MERLO_{suffix}_{variant}_TAG) "
                        f"? ({payload}) : "
                        f"(merlo_ownership_trap(\"{diagnostic}\"), "
                        f"{failure}))"
                    )
                elif callee == "console.write":
                    if len(operands) != 1:
                        raise RepresentationCBackendError(
                            "MIR console.write arity is invalid"
                        )
                    value = operands[0]
                    pointer = (
                        value
                        if instruction.operands[0] in pointer_values
                        else f"&({value})"
                    )
                    call = (
                        "merlo_console_write_view((MerloTextView){ "
                        f"({pointer})->data, ({pointer})->length }})"
                    )
                elif callee == "Unit":
                    if operands:
                        raise RepresentationCBackendError(
                            "MIR Unit constructor arity is invalid"
                        )
                    call = "0"
                elif target is not None:
                    call_operands = []
                    for index, (operand_id, operand) in enumerate(
                        zip(instruction.operands, operands)
                    ):
                        parameter = (
                            target.parameters[index]
                            if index < len(target.parameters)
                            else None
                        )
                        if parameter is None:
                            raise RepresentationCBackendError(
                                f"MIR call arity exceeds target: {callee}"
                            )
                        call_operands.append(
                            call_argument(
                                parameter,
                                operand_id,
                                operand,
                                operand_type(instruction, index),
                            )
                        )
                    call = (
                        f"merlo_fn_{_identifier(callee)}"
                        f"({', '.join(call_operands)})"
                    )
                elif (
                    instruction.type_name is not None
                    and (
                        result_descriptor := self.descriptors.get(
                            instruction.type_name
                        )
                    ) is not None
                    and result_descriptor.kind == "enum"
                    and any(
                        variant == callee
                        for variant, _payload, _tag in result_descriptor.variants
                    )
                ):
                    variant = next(
                        variant
                        for variant, _payload, _tag in result_descriptor.variants
                        if variant == callee
                    )
                    payload = next(
                        payload
                        for variant_name, payload, _tag in result_descriptor.variants
                        if variant_name == variant
                    )
                    if payload is None:
                        if operands:
                            raise RepresentationCBackendError(
                                "MIR enum constructor arity is invalid"
                            )
                        if all(
                            item_payload is None
                            for _name, item_payload, _tag in result_descriptor.variants
                        ):
                            call = (
                                f"MERLO_{_identifier(instruction.type_name)}"
                                f"_{variant}"
                            )
                        else:
                            call = (
                                f"merlo_make_{_identifier(instruction.type_name)}"
                                f"_{variant}()"
                            )
                    elif payload == "Unit":
                        if operands:
                            raise RepresentationCBackendError(
                                "MIR enum constructor arity is invalid"
                            )
                        call = (
                            f"merlo_make_{_identifier(instruction.type_name)}"
                            f"_{variant}()"
                        )
                    elif len(operands) == 1:
                        call = (
                            f"merlo_make_{_identifier(instruction.type_name)}"
                            f"_{variant}({consume(instruction.operands[0], operands[0], payload, force_move=True)})"
                        )
                    else:
                        raise RepresentationCBackendError(
                            f"MIR enum constructor arity is invalid: {callee}"
                        )
                elif callee.endswith(".clone"):
                    source_name = callee.rsplit(".clone", 1)[0]
                    if len(operands) == 1:
                        source = (
                            operands[0]
                            if instruction.operands[0] in pointer_values
                            else f"&({operands[0]})"
                        )
                        source_type = operand_type(instruction, 0)
                    elif not operands:
                        source = method_receiver_pointer(source_name)
                        source_type = instruction.type_name
                    else:
                        raise RepresentationCBackendError(
                            f"MIR clone arity is invalid: {callee}"
                        )
                    if source_type is None:
                        raise RepresentationCBackendError(
                            "MIR clone source type is missing"
                        )
                    call = (
                        f"merlo_clone_{_identifier(source_type)}"
                        f"({source})"
                    )
                elif callee.endswith(".len"):
                    source_name = callee.rsplit(".", 1)[0]
                    source_type = local_types.get(source_name)
                    descriptor = self.descriptors.get(source_type or "")
                    if (
                        descriptor is None
                        or descriptor.kind != "array"
                        or descriptor.length is None
                    ):
                        raise RepresentationCBackendError(
                            f"MIR array length source is unknown: {callee}"
                        )
                    call = f"UINT64_C({descriptor.length})"
                else:
                    raise RepresentationCBackendError(
                        f"MIR call target is unsupported: {callee}"
                    )
                if instruction.result is None:
                    lines.append(f"    {call};")
                else:
                    define(instruction, call)
                drops = [
                    (temporary, type_name)
                    for operand_id, (temporary, type_name) in owned_temporaries.items()
                    if operand_id in instruction.operands
                ]
                if (
                    instruction.result in inline_call_results
                    or instruction.result in result_branch_inputs
                ):
                    pending_drops[instruction.result] = drops
                else:
                    for temporary, type_name in drops:
                        lines.append(
                            f"    merlo_drop_{_identifier(type_name)}"
                            f"(&{temporary});"
                        )
            elif instruction.op == "callback_call":
                callee = attrs.get("callee")
                if not isinstance(callee, str) or not operands:
                    raise RepresentationCBackendError(
                        "MIR callback call is malformed"
                    )
                callback_type = next(
                    (
                        parameter.type_name
                        for parameter in function.parameters
                        if parameter.name == callee
                    ),
                    local_types.get(callee),
                )
                descriptor = self.descriptors.get(callback_type or "")
                if descriptor is None or descriptor.kind not in {"callback", "closure"}:
                    raise RepresentationCBackendError(
                        f"MIR callback target is unknown: {callee}"
                    )
                callback_parameter = next(
                    (
                        parameter
                        for parameter in function.parameters
                        if parameter.name == callee
                    ),
                    None,
                )
                callback = (
                    callee
                    if callback_parameter is not None
                    and self._parameter_is_pointer(callback_parameter)
                    else f"&{callee}"
                )
                call = (
                    f"({callback})->call(({callback})->environment"
                    f"{', ' if operands else ''}{', '.join(operands)})"
                )
                if instruction.result is None:
                    lines.append(f"    {call};")
                else:
                    define(instruction, call)
            elif instruction.op == "primitive_call":
                callee = attrs.get("callee")
                if not isinstance(callee, str):
                    raise RepresentationCBackendError(
                        "MIR primitive call has no callee"
                    )
                if callee == "Text.from_bytes" and len(operands) == 3:
                    source = host_address(instruction.operands[0], operands[0])
                    source_type = operand_type(instruction, 0)
                    if source_type == "Bytes":
                        view = (
                            f"&(MerloBytesView){{ ({source})->data, "
                            f"({source})->length }}"
                        )
                    elif source_type == "BytesView":
                        view = source
                    else:
                        raise RepresentationCBackendError(
                            "MIR Text.from_bytes source is not byte-like"
                        )
                    define(
                        instruction,
                        f"merlo_text_from_bytes({view}, "
                        f"{operands[1]}, {operands[2]})",
                    )
                elif callee == "TextBuilder.new" and not operands:
                    define(instruction, "merlo_text_builder_new()")
                elif callee.endswith(".append_byte") and len(operands) == 2:
                    receiver = (
                        operands[0]
                        if instruction.operands[0] in pointer_values
                        else f"&({operands[0]})"
                    )
                    lines.append(
                        f"    merlo_text_builder_append_byte({receiver}, "
                        f"{operands[1]});"
                    )
                elif callee.endswith(".append_scalar") and len(operands) == 2:
                    receiver = (
                        operands[0]
                        if instruction.operands[0] in pointer_values
                        else f"&({operands[0]})"
                    )
                    lines.append(
                        f"    merlo_text_builder_append_scalar({receiver}, "
                        f"{operands[1]});"
                    )
                elif callee.endswith(".append_uint64") and len(operands) == 2:
                    receiver = (
                        operands[0]
                        if instruction.operands[0] in pointer_values
                        else f"&({operands[0]})"
                    )
                    lines.append(
                        f"    merlo_text_builder_append_uint64({receiver}, "
                        f"{operands[1]});"
                    )
                elif (
                    (
                        callee.endswith(".contains")
                        or callee.endswith(".contains_ascii_case_insensitive")
                    )
                    and len(operands) == 2
                ):
                    receiver = (
                        operands[0]
                        if instruction.operands[0] in pointer_values
                        else f"&({operands[0]})"
                    )
                    source_type = operand_type(instruction, 0)
                    if source_type == "Text":
                        receiver = (
                            f"&(MerloTextView){{ ({receiver})->data, "
                            f"({receiver})->length }}"
                        )
                    needle = (
                        operands[1]
                        if instruction.operands[1] in pointer_values
                        else f"&({operands[1]})"
                    )
                    ignore_case = (
                        "true"
                        if callee.endswith(".contains_ascii_case_insensitive")
                        else "false"
                    )
                    define(
                        instruction,
                        f"merlo_text_view_contains({receiver}, {needle}, "
                        f"{ignore_case})",
                    )
                elif (
                    (
                        callee.endswith(".starts_with")
                        or callee.endswith(".ends_with")
                    )
                    and len(operands) == 2
                ):
                    receiver = (
                        operands[0]
                        if instruction.operands[0] in pointer_values
                        else f"&({operands[0]})"
                    )
                    source_type = operand_type(instruction, 0)
                    if source_type == "Text":
                        receiver = (
                            f"&(MerloTextView){{ ({receiver})->data, "
                            f"({receiver})->length }}"
                        )
                    needle = (
                        operands[1]
                        if instruction.operands[1] in pointer_values
                        else f"&({operands[1]})"
                    )
                    suffix = (
                        "true"
                        if callee.endswith(".ends_with")
                        else "false"
                    )
                    define(
                        instruction,
                        f"merlo_text_view_prefix_suffix({receiver}, "
                        f"{needle}, {suffix})",
                    )
                elif callee.endswith(".append_text") and len(operands) == 2:
                    receiver = (
                        operands[0]
                        if instruction.operands[0] in pointer_values
                        else f"&({operands[0]})"
                    )
                    text = (
                        operands[1]
                        if instruction.operands[1] in pointer_values
                        else f"&({operands[1]})"
                    )
                    lines.append(
                        f"    merlo_text_builder_append_text({receiver}, "
                        f"{text});"
                    )
                elif callee.endswith(".finish") and len(operands) == 1:
                    receiver = (
                        operands[0]
                        if instruction.operands[0] in pointer_values
                        else f"&({operands[0]})"
                    )
                    define(
                        instruction,
                        f"merlo_text_builder_finish({receiver})",
                    )
                elif (
                    callee in {"view", "as_view"}
                    or callee.endswith(".view")
                    or callee.endswith(".as_view")
                ):
                    if len(operands) != 1:
                        raise RepresentationCBackendError(
                            f"MIR view operation arity is invalid: {callee}"
                        )
                    source = host_address(instruction.operands[0], operands[0])
                    source_type = operand_type(instruction, 0)
                    view_type = (
                        "MerloBytesView"
                        if source_type == "Bytes"
                        else "MerloTextView"
                    )
                    define(
                        instruction,
                        f"({view_type}){{ ({source})->data, "
                        f"({source})->length }}",
                    )
                elif callee.endswith(".slice") and len(operands) == 3:
                    source = host_address(instruction.operands[0], operands[0])
                    define(
                        instruction,
                        "(MerloBytesView){ "
                        f"({source})->data + ({operands[1]}), "
                        f"({operands[2]}) }}",
                    )
                elif (callee == "slice_bytes" or callee.endswith(".slice_bytes")) and len(operands) == 3:
                    source = host_address(instruction.operands[0], operands[0])
                    define(
                        instruction,
                        "merlo_text_view_slice_bytes("
                        f"{source}, {operands[1]}, {operands[2]})",
                    )
                elif (callee == "to_text" or callee.endswith(".to_text")) and len(operands) == 1:
                    source = host_address(instruction.operands[0], operands[0])
                    source_type = operand_type(instruction, 0)
                    if source_type == "Bytes":
                        expression = (
                            "merlo_text_from_bytes("
                            f"&(MerloBytesView){{ ({source})->data, "
                            f"({source})->length }}, UINT64_C(0), "
                            f"({source})->length)"
                        )
                    elif source_type == "TextView":
                        expression = f"merlo_text_from_view({source})"
                    else:
                        descriptor = self.descriptors.get(source_type or "")
                        if descriptor is None or descriptor.kind != "text":
                            raise RepresentationCBackendError(
                                "MIR to_text source is not text-like"
                            )
                        expression = f"merlo_text_clone({source})"
                    define(instruction, expression)
                elif (callee == "clone" or callee.endswith(".clone")) and len(operands) == 1:
                    source = (
                        operands[0]
                        if instruction.operands[0] in pointer_values
                        else f"&({operands[0]})"
                    )
                    source_type = operand_type(instruction, 0)
                    if source_type is None:
                        raise RepresentationCBackendError(
                            "MIR clone source type is missing"
                        )
                    define(
                        instruction,
                        f"merlo_clone_{_identifier(source_type)}({source})",
                    )
                elif (callee.endswith(".len") or callee == "len") and len(operands) == 1:
                    receiver = (
                        f"({operands[0]})->length"
                        if instruction.operands[0] in pointer_values
                        else f"({operands[0]}).length"
                    )
                    define(instruction, receiver)
                elif callee.endswith(".byte") and len(operands) == 2:
                    source_type = (
                        self.hir.type_context.render(instruction.operand_type_ids[0])
                        if instruction.operand_type_ids
                        else None
                    )
                    if source_type == "Bytes":
                        source = operands[0]
                        if instruction.operands[0] in pointer_values:
                            receiver = (
                                f"&(({_c_name('BytesView')})"
                                f"{{({source})->data, ({source})->length}})"
                            )
                        else:
                            receiver = (
                                f"&(({_c_name('BytesView')})"
                                f"{{({source}).data, ({source}).length}})"
                            )
                    else:
                        receiver = (
                            operands[0]
                            if instruction.operands[0] in pointer_values
                            else f"&({operands[0]})"
                        )
                    contract = str(attrs.get("contract_symbol", callee))
                    if contract.startswith("TextView."):
                        helper = "merlo_text_view_load"
                    elif contract.startswith("Text."):
                        helper = "merlo_text_load"
                    else:
                        helper = "merlo_bytes_load"
                    define(instruction, f"{helper}({receiver}, {operands[1]})")
                else:
                    raise RepresentationCBackendError(
                        f"MIR primitive call is unsupported: {callee}"
                    )
            elif instruction.op == "array_literal":
                descriptor = self.descriptors.get(instruction.type_name or "")
                if (
                    descriptor is None
                    or descriptor.kind != "array"
                    or descriptor.length is None
                    or descriptor.element_type is None
                    or len(operands) != descriptor.length
                ):
                    raise RepresentationCBackendError(
                        "MIR array literal is malformed"
                    )
                elements = [
                    consume(instruction.operands[index], value, descriptor.element_type)
                    if _is_owner(self.descriptors[descriptor.element_type])
                    else value
                    for index, value in enumerate(operands)
                ]
                define(
                    instruction,
                    f"({_c_name(instruction.type_name)}){{ .data = "
                    f"{{{', '.join(elements)}}} }}",
                )
            elif instruction.op == "bounds_checked_index":
                if len(operands) != 2 or len(instruction.operand_type_ids) != 2:
                    raise RepresentationCBackendError(
                        "MIR collection index is malformed"
                    )
                source_type = self.hir.type_context.render(
                    instruction.operand_type_ids[0]
                )
                descriptor = self.descriptors.get(source_type)
                if (
                    source_type not in {
                        "Bytes",
                        "BytesView",
                        "Text",
                        "TextView",
                    }
                    and (
                        descriptor is None
                        or descriptor.kind not in {
                            "array",
                            "vec",
                            "slice",
                        }
                    )
                ):
                    raise RepresentationCBackendError(
                        "MIR collection index source is malformed"
                    )
                source_pointer = (
                    operands[0]
                    if instruction.operands[0] in pointer_values
                    else f"&({operands[0]})"
                )
                if (
                    descriptor is not None
                    and descriptor.kind == "array"
                    and descriptor.length is not None
                ):
                    length = f"UINT64_C({descriptor.length})"
                else:
                    length = f"({source_pointer})->length"
                lines.append(
                    f"    if ({operands[1]} >= {length}) "
                    f"merlo_bounds_trap({operands[1]}, {length});"
                )
                access = (
                    f"({source_pointer})->data[{operands[1]}]"
                )
                define(
                    instruction,
                    f"&({access})"
                    if instruction.result in pointer_values
                    else access,
                )
            elif instruction.op == "byte_load":
                if len(operands) != 2:
                    raise RepresentationCBackendError("MIR byte_load is malformed")
                callee = attrs.get("callee")
                if not isinstance(callee, str):
                    raise RepresentationCBackendError("MIR byte_load has no callee")
                source_type = (
                    self.hir.type_context.render(instruction.operand_type_ids[0])
                    if instruction.operand_type_ids
                    else None
                )
                if source_type == "Bytes":
                    source = operands[0]
                    if instruction.operands[0] in pointer_values:
                        receiver = (
                            f"&(({_c_name('BytesView')})"
                            f"{{({source})->data, ({source})->length}})"
                        )
                    else:
                        receiver = (
                            f"&(({_c_name('BytesView')})"
                            f"{{({source}).data, ({source}).length}})"
                        )
                else:
                    receiver = (
                        operands[0]
                        if instruction.operands[0] in pointer_values
                        else f"&({operands[0]})"
                    )
                contract = str(attrs.get("contract_symbol", callee))
                if contract.startswith("TextView."):
                    helper = "merlo_text_view_load"
                elif contract.startswith("Text."):
                    helper = "merlo_text_load"
                else:
                    helper = "merlo_bytes_load"
                define(instruction, f"{helper}({receiver}, {operands[1]})")
            elif instruction.op == "const":
                literal = self._mir_literal(
                    instruction.type_name or "Unit",
                    attrs.get("value"),
                    owned=instruction.result not in pointer_values,
                )
                if instruction.result in pointer_values:
                    literal = f"&({literal})"
                define(instruction, literal)
            elif instruction.op == "load_local":
                name = attrs.get("name")
                if not isinstance(name, str):
                    raise RepresentationCBackendError("MIR CFG load_local has no name")
                line_binding = file_line_bindings.get(name)
                local_value_names[instruction.result or ""] = name
                if line_binding is not None:
                    define(instruction, value_of(line_binding))
                    return
                binding = active_match_bindings.get(name)
                if binding is not None:
                    source_id, variant, _payload_type = binding
                    source = value_of(source_id)
                    access = (
                        f"({source})->payload.{variant}"
                        if source_id in pointer_values
                        else f"({source}).payload.{variant}"
                    )
                    expression = (
                        f"&({access})"
                        if instruction.result in pointer_values
                        else access
                    )
                    define(instruction, expression)
                    return
                target = next(
                    (item for item in self.hir.functions if item.name == name),
                    None,
                )
                descriptor = self.descriptors.get(instruction.type_name or "")
                if (
                    target is not None
                    and descriptor is not None
                    and descriptor.kind in {"callback", "closure"}
                ):
                    define(
                        instruction,
                        f"({_c_name(instruction.type_name)}){{ "
                        f"merlo_closure_adapter_{name}, NULL, NULL, NULL }}",
                    )
                else:
                    parameter_pointer = any(
                        parameter.name == name
                        and self._parameter_is_pointer(parameter)
                        for parameter in function.parameters
                    )
                    expression = (
                        name
                        if parameter_pointer or instruction.result not in pointer_values
                        else f"&{name}"
                    )
                    define(instruction, expression)
                    if parameter_pointer:
                        pointer_values.add(instruction.result or "")
            elif instruction.op == "store_local":
                target = attrs.get("name", attrs.get("target"))
                if not isinstance(target, str) or len(operands) != 1:
                    raise RepresentationCBackendError("MIR CFG store_local is malformed")
                replacement = replacement_names.get(instruction.operands[0])
                if "target" in attrs and replacement is not None:
                    replacement_name, replacement_type = replacement
                    lines.append(
                        f"    {replacement_name} = {operands[0]};"
                    )
                    lines.append(
                        f"    merlo_drop_{_identifier(replacement_type)}"
                        f"(&{target});"
                    )
                    lines.append(
                        f"    {target} = merlo_move_{_identifier(replacement_type)}"
                        f"(&{replacement_name});"
                    )
                else:
                    store_value = operands[0]
                    descriptor = self.descriptors.get(instruction.type_name or "")
                    if (
                        instruction.operands[0] in pointer_values
                        and descriptor is not None
                        and _is_owner(descriptor)
                    ):
                        store_value = (
                            f"merlo_clone_{_identifier(instruction.type_name or '')}"
                            f"({store_value})"
                        )
                    elif (
                        instruction.operands[0] in result_types
                        and instruction.operands[0] not in inline_call_results
                        and instruction.operands[0] not in inline_construct_results
                        and descriptor is not None
                        and _is_owner(descriptor)
                    ):
                        store_value = (
                            f"merlo_move_{_identifier(instruction.type_name or '')}"
                            f"(&({store_value}))"
                        )
                    lines.append(f"    {target} = {store_value};")
                for temporary, type_name in pending_drops.pop(
                    instruction.operands[0],
                    [],
                ):
                    lines.append(
                        f"    merlo_drop_{_identifier(type_name)}"
                        f"(&{temporary});"
                    )
                for temporary, type_name in pending_receiver_drops.pop(
                    instruction.operands[0],
                    [],
                ):
                    lines.append(
                        f"    merlo_drop_{_identifier(type_name)}"
                        f"(&{temporary});"
                    )
            elif instruction.op == "binary":
                if len(operands) != 2:
                    raise RepresentationCBackendError("MIR CFG binary is malformed")
                overflow = attrs.get("overflow", attrs.get("signed_overflow"))
                if (
                    attrs.get("division_by_zero") == "trap"
                    or attrs.get("shift_range") == "checked"
                ):
                    overflow = "checked"
                define(
                    instruction,
                    self._mir_scalar_binary(
                        instruction.type_name or "",
                        str(attrs.get("operator", "")),
                        operands[0],
                        operands[1],
                        str(overflow) if overflow is not None else None,
                    ),
                )
            elif instruction.op == "aug_assign":
                if len(operands) != 2:
                    raise RepresentationCBackendError(
                        "MIR CFG augmented assignment is malformed"
                    )
                overflow = attrs.get("overflow", attrs.get("signed_overflow"))
                if (
                    attrs.get("division_by_zero") == "trap"
                    or attrs.get("shift_range") == "checked"
                ):
                    overflow = "checked"
                if overflow is None and instruction.type_name in {
                    "Byte",
                    "UInt64",
                    "Int64",
                }:
                    overflow = "checked"
                define(
                    instruction,
                    self._mir_scalar_binary(
                        instruction.type_name or "",
                        str(attrs.get("operator", "")),
                        operands[0],
                        operands[1],
                        str(overflow) if overflow is not None else None,
                    ),
                )
                target = attrs.get("target")
                if not isinstance(target, str):
                    raise RepresentationCBackendError(
                        "MIR CFG augmented assignment has no target"
                    )
                lines.append(f"    {target} = {value_of(instruction.result or '')};")
            elif instruction.op == "boolean":
                if not operands:
                    raise RepresentationCBackendError("MIR CFG boolean has no operands")
                symbol = "&&" if attrs.get("operator") == "And" else "||"
                define(instruction, f" {symbol} ".join(f"({item})" for item in operands))
            elif instruction.op == "compare":
                operators = tuple(attrs.get("operators", ()))
                if len(operators) != len(operands) - 1:
                    raise RepresentationCBackendError("MIR CFG compare is malformed")
                symbols = {
                    "Eq": "==",
                    "NotEq": "!=",
                    "Lt": "<",
                    "LtE": "<=",
                    "Gt": ">",
                    "GtE": ">=",
                }
                try:
                    comparisons: list[str] = []
                    for index, operator in enumerate(operators):
                        left_type = operand_type(instruction, index)
                        right_type = operand_type(instruction, index + 1)
                        descriptor = self.descriptors.get(left_type or "")
                        if (
                            left_type == right_type == "Text"
                            and operator in {"Eq", "NotEq"}
                        ):
                            left = (
                                f"*({operands[index]})"
                                if instruction.operands[index] in pointer_values
                                else operands[index]
                            )
                            right = (
                                f"*({operands[index + 1]})"
                                if instruction.operands[index + 1] in pointer_values
                                else operands[index + 1]
                            )
                            equal = (
                                f"merlo_text_equal_values({left}, {right})"
                            )
                            comparisons.append(
                                equal if operator == "Eq" else f"!({equal})"
                            )
                        elif (
                            left_type == right_type
                            and descriptor is not None
                            and descriptor.kind == "enum"
                            and operator in {"Eq", "NotEq"}
                        ):
                            pure_enum = all(
                                payload is None
                                for _name, payload, _tag in descriptor.variants
                            )

                            def unit_constructor(value_id: str) -> bool:
                                producer = result_instructions.get(value_id)
                                if producer is None:
                                    return False
                                if producer.op == "load_field":
                                    field = producer.attribute_map.get("field")
                                    variant = field if isinstance(field, str) else ""
                                elif producer.op in {"call", "construct_enum"}:
                                    callee = producer.attribute_map.get("callee")
                                    if not isinstance(callee, str):
                                        return False
                                    variant = callee.rsplit(".", 1)[-1]
                                else:
                                    return False
                                return any(
                                    name == variant
                                    and payload in {None, "Unit"}
                                    for name, payload, _tag in descriptor.variants
                                )

                            if not pure_enum and not any(
                                unit_constructor(value_id)
                                for value_id in instruction.operands[
                                    index : index + 2
                                ]
                            ):
                                raise RepresentationCBackendError(
                                    "MIR payload enum equality requires "
                                    "a unit variant"
                                )

                            def enum_tag(value_id: str, value: str) -> str:
                                if pure_enum:
                                    return (
                                        f"*({value})"
                                        if value_id in pointer_values
                                        else value
                                    )
                                return (
                                    f"({value})->tag"
                                    if value_id in pointer_values
                                    else f"({value}).tag"
                                )

                            left = enum_tag(
                                instruction.operands[index],
                                operands[index],
                            )
                            right = enum_tag(
                                instruction.operands[index + 1],
                                operands[index + 1],
                            )
                            equal = f"({left} == {right})"
                            comparisons.append(
                                equal if operator == "Eq" else f"!{equal}"
                            )
                        else:
                            comparisons.append(
                                f"({operands[index]} {symbols[str(operator)]} "
                                f"{operands[index + 1]})"
                            )
                except KeyError as exc:
                    raise RepresentationCBackendError(
                        "MIR CFG compare operator is unsupported"
                    ) from exc
                define(instruction, " && ".join(comparisons))
            elif instruction.op == "unary":
                if len(operands) != 1:
                    raise RepresentationCBackendError("MIR CFG unary is malformed")
                operator = str(attrs.get("operator", ""))
                if operator == "Not":
                    expression = f"!({operands[0]})"
                elif operator == "USub" and instruction.type_name == "Int64":
                    expression = f"merlo_checked_int64_neg({operands[0]})"
                elif operator == "USub":
                    expression = f"-({operands[0]})"
                elif operator == "UAdd":
                    expression = f"+({operands[0]})"
                elif operator == "Invert":
                    expression = f"~({operands[0]})"
                else:
                    raise RepresentationCBackendError(
                        f"MIR CFG unary operator unsupported: {operator}"
                    )
                define(instruction, expression)
            elif instruction.op == "numeric_intrinsic":
                define(instruction, self._mir_scalar_intrinsic(instruction, operands))
            elif instruction.op == "scalar_cast":
                if len(operands) != 1:
                    raise RepresentationCBackendError("MIR CFG scalar cast is malformed")
                define(instruction, self._mir_scalar_cast(instruction, operands[0]))
            elif instruction.op == "construct_enum":
                descriptor = self.descriptors.get(instruction.type_name or "")
                callee = attrs.get("callee")
                if (
                    descriptor is None
                    or descriptor.kind != "enum"
                    or not isinstance(callee, str)
                    or "." not in callee
                ):
                    raise RepresentationCBackendError(
                        "MIR enum construction is malformed"
                    )
                variant = callee.rsplit(".", 1)[1]
                payload = next(
                    (
                        payload
                        for name, payload, _tag in descriptor.variants
                        if name == variant
                    ),
                    None,
                )
                if payload is None:
                    if operands:
                        raise RepresentationCBackendError(
                            "MIR enum construction arity is invalid"
                        )
                    if all(
                        item_payload is None
                        for _name, item_payload, _tag in descriptor.variants
                    ):
                        expression = (
                            f"MERLO_{_identifier(instruction.type_name)}"
                            f"_{variant}"
                        )
                    else:
                        expression = (
                            f"merlo_make_{_identifier(instruction.type_name)}"
                            f"_{variant}()"
                        )
                elif payload == "Unit":
                    if operands:
                        raise RepresentationCBackendError(
                            "MIR enum construction arity is invalid"
                        )
                    expression = (
                        f"merlo_make_{_identifier(instruction.type_name)}"
                        f"_{variant}()"
                    )
                elif len(operands) == 1:
                    expression = (
                        f"merlo_make_{_identifier(instruction.type_name)}"
                        f"_{variant}("
                        f"{consume(instruction.operands[0], operands[0], payload, force_move=True)})"
                    )
                else:
                    raise RepresentationCBackendError(
                        "MIR enum construction arity is invalid"
                    )
                define(instruction, expression)
            elif instruction.op == "construct_record":
                descriptor = self.descriptors.get(instruction.type_name or "")
                if descriptor is None or len(operands) > len(descriptor.fields):
                    raise RepresentationCBackendError(
                        "MIR CFG record construction is malformed"
                    )
                fields = []
                for index, (_field_name, field_type, _ownership) in enumerate(
                    descriptor.fields
                ):
                    if index >= len(operands):
                        fields.append(self._zero_expression(field_type))
                        continue
                    value = operands[index]
                    operand_id = instruction.operands[index]
                    field_descriptor = self.descriptors[field_type]
                    fields.append(
                        consume(operand_id, value, field_type)
                        if _is_owner(field_descriptor)
                        else value
                    )
                define(
                    instruction,
                    f"merlo_make_{_identifier(instruction.type_name)}"
                    f"({', '.join(fields)})",
                )
            elif instruction.op == "load_field":
                field = attrs.get("field")
                if not isinstance(field, str):
                    raise RepresentationCBackendError("MIR CFG load_field is malformed")
                if len(operands) == 0:
                    descriptor = self.descriptors.get(instruction.type_name or "")
                    variant_payload = next(
                        (
                            payload
                            for variant, payload, _tag in (
                                descriptor.variants
                                if descriptor is not None
                                and descriptor.kind == "enum"
                                else ()
                            )
                            if variant == field
                        ),
                        None,
                    )
                    if (
                        descriptor is not None
                        and descriptor.kind == "enum"
                        and any(
                            variant == field
                            for variant, _payload, _tag in descriptor.variants
                        )
                    ):
                        if variant_payload is None and all(
                            payload is None
                            for _name, payload, _tag in descriptor.variants
                        ):
                            expression = (
                                f"MERLO_{_identifier(instruction.type_name)}"
                                f"_{field}"
                            )
                        else:
                            expression = (
                                f"merlo_make_{_identifier(instruction.type_name)}"
                                f"_{field}()"
                            )
                    else:
                        expression = (
                            f"MERLO_{_identifier(instruction.type_name)}_{field}"
                        )
                    define(instruction, expression)
                    return
                if len(operands) != 1:
                    raise RepresentationCBackendError(
                        "MIR CFG load_field is malformed"
                    )
                access = (
                    f"({operands[0]})->{field}"
                    if instruction.operands[0] in pointer_values
                    else f"({operands[0]}).{field}"
                )
                define(
                    instruction,
                    f"&({access})"
                    if instruction.result in pointer_values
                    else access,
                )
            elif instruction.op == "store_field":
                target = attrs.get("target")
                if len(operands) != 1 or not isinstance(target, str):
                    raise RepresentationCBackendError(
                        "MIR CFG store_field is malformed"
                    )
                base, separator, field = target.partition(".")
                if not separator or not field:
                    raise RepresentationCBackendError(
                        "MIR CFG store_field target is malformed"
                    )
                base_parameter = next(
                    (
                        parameter
                        for parameter in function.parameters
                        if parameter.name == base
                    ),
                    None,
                )
                access = (
                    f"{base}->{field}"
                    if base in pointer_values
                    or (
                        base_parameter is not None
                        and self._parameter_is_pointer(base_parameter)
                    )
                    else f"{base}.{field}"
                )
                store_value = operands[0]
                descriptor = self.descriptors.get(instruction.type_name or "")
                if descriptor is not None and _is_owner(descriptor):
                    store_value = consume(
                        instruction.operands[0],
                        operands[0],
                        instruction.type_name,
                        force_move=True,
                    )
                lines.append(f"    {access} = {store_value};")
            elif instruction.op == "pass":
                if instruction.operands or instruction.result is not None:
                    raise RepresentationCBackendError(
                        "MIR pass instruction is malformed"
                    )
            else:
                raise RepresentationCBackendError(
                    f"unsupported MIR CFG operation: {instruction.op}"
                )
        return_ordinal = 0

        def emit_return(value_id: str | None) -> None:
            nonlocal return_ordinal
            value: str | None = None
            if value_id is not None:
                value = value_of(value_id)
                descriptor = self.descriptors.get(function.return_type)
                if descriptor is not None and _is_owner(descriptor):
                    if value_id in borrowed_pointer_values:
                        value = (
                            f"merlo_clone_{_identifier(function.return_type)}"
                            f"({value})"
                        )
                    else:
                        address = (
                            value
                            if value_id in pointer_values
                            else f"&({value})"
                        )
                        value = (
                            f"merlo_move_{_identifier(function.return_type)}"
                            f"({address})"
                        )
                if function.return_type != "Unit":
                    return_ordinal += 1
                    return_name = f"__merlo_return_{return_ordinal}"
                    lines.append(
                        f"    {_c_name(function.return_type)} "
                        f"{return_name} = {value};"
                    )
                    value = return_name
                lines.extend(
                    self._mir_contract_checks(
                        function,
                        tuple(function.ensures),
                        "ensure",
                        result_expression=value,
                    )
                )
            elif function.return_type == "Unit":
                lines.extend(
                    self._mir_contract_checks(
                        function,
                        tuple(function.ensures),
                        "ensure",
                        result_expression="0",
                    )
                )

            for name, type_name in local_types.items():
                descriptor = self.descriptors.get(type_name)
                if (
                    descriptor is not None
                    and _is_owner(descriptor)
                    and (
                        active_block_id
                        not in explicit_drop_local_blocks.get(name, set())
                        or name in protected_return_locals.get(
                            active_block_id,
                            set(),
                        )
                        or (
                            active_block_id
                            in consumed_local_blocks.get(name, set())
                            and active_block_id
                            not in closed_local_blocks.get(name, set())
                        )
                    )
                ):
                    lines.append(
                        f"    merlo_drop_{_identifier(type_name)}"
                        f"({local_pointer(name)});"
                    )
            for result_id, type_name in result_types.items():
                descriptor = self.descriptors.get(type_name)
                temporary = result_names.get(
                    result_id,
                    self._mir_scalar_temp(result_id),
                )
                if (
                    descriptor is not None
                    and _is_owner(descriptor)
                    and result_id != value_id
                    and result_id not in consumed_values
                    and result_id not in moved_values
                    and result_id not in owned_temporaries
                ):
                    lines.append(
                        f"    merlo_drop_{_identifier(type_name)}"
                        f"(&{temporary});"
                    )
            if value_id is None:
                if function.return_type == "Unit":
                    lines.append("    return;")
                else:
                    lines.append('    merlo_ownership_trap("Unreachable");')
                    lines.append(
                        f"    return {self._zero_expression(function.return_type)};"
                    )
                return
            if function.return_type == "Unit":
                lines.append("    return;")
                return
            assert value is not None
            lines.append(f"    return {value};")

        labels = {
            block.id: (
                f"__merlo_loop_exit_{block.id}"
                if "_while_exit" in block.id
                else (
                    f"__merlo_loop_condition_{block.id}"
                    if "_while_condition" in block.id
                    else f"__merlo_{block.id}"
                )
            )
            for block in mir_function.blocks
        }
        for block in mir_function.blocks:
            active_block_id = block.id
            active_match_bindings = match_payload_bindings.get(block.id, {})
            lines.append(f"{labels[block.id]}:;")
            for instruction in block.instructions:
                emit_instruction(instruction)
            terminator = block.terminator
            if terminator.kind == "branch":
                assert terminator.value is not None
                lines.append(
                    f"    if ({value_of(terminator.value)}) "
                    f"goto {labels[terminator.targets[0]]};"
                )
                lines.append(f"    goto {labels[terminator.targets[1]]};")
            elif terminator.kind == "jump":
                lines.append(f"    goto {labels[terminator.targets[0]]};")
            elif terminator.kind == "switch":
                if terminator.value is None or not terminator.cases:
                    raise RepresentationCBackendError(
                        "MIR switch terminator is malformed"
                    )
                switch_type = next(
                    (
                        instruction.type_name
                        for candidate_block in mir_function.blocks
                        for instruction in candidate_block.instructions
                        if instruction.result == terminator.value
                    ),
                    None,
                )
                descriptor = self.descriptors.get(switch_type or "")
                if descriptor is None or descriptor.kind != "enum":
                    raise RepresentationCBackendError(
                        "MIR switch source is not an enum"
                    )
                switch_value = value_of(terminator.value)
                payloadless_enum = all(
                    payload is None
                    for _name, payload, _tag in descriptor.variants
                )
                switch_access = (
                    switch_value
                    if payloadless_enum
                    else (
                        f"({switch_value})->tag"
                        if terminator.value in pointer_values
                        else f"({switch_value}).tag"
                    )
                )
                lines.append(f"    switch ({switch_access}) {{")
                wildcard_target: str | None = None
                for variant, target in terminator.cases:
                    if variant == "_":
                        wildcard_target = target
                        continue
                    variant_name = variant.rsplit(".", 1)[-1]
                    tag = next(
                        (
                            tag
                            for name, _payload, tag in descriptor.variants
                            if name == variant_name
                        ),
                        None,
                    )
                    if tag is None:
                        raise RepresentationCBackendError(
                            f"MIR switch variant is unknown: {variant}"
                        )
                    case_name = (
                        f"MERLO_{_identifier(descriptor.name)}_{variant_name}"
                        if payloadless_enum
                        else (
                            f"MERLO_{_identifier(descriptor.name)}"
                            f"_{variant_name}_TAG"
                        )
                    )
                    lines.append(
                        f"    case {case_name}: goto {labels[target]};"
                    )
                lines.append("    default:")
                if wildcard_target is not None:
                    lines.append(f"        goto {labels[wildcard_target]};")
                else:
                    lines.append(
                        '        merlo_ownership_trap("InvalidEnumTag");'
                    )
                    lines.append(f"        goto {labels[terminator.targets[0]]};")
                lines.append("    }")
            elif terminator.kind == "return":
                emit_return(terminator.value)
            else:
                raise RepresentationCBackendError(
                    f"unsupported MIR CFG terminator: {terminator.kind}"
                )
        lines.append("}")
        return "\n".join(lines)

    def _functions(self) -> str:
        mir_functions = {item.name: item for item in self.mir.functions}
        emitted: list[str] = []
        for function in self.hir.functions:
            mir_function = mir_functions.get(function.name)
            if (
                mir_function is not None
                and self._mir_scalar_eligible(function, mir_function)
            ):
                emitted.append(self._emit_mir_scalar_function(function, mir_function))
                continue
            if (
                mir_function is not None
                and self._mir_cfg_eligible(function, mir_function)
            ):
                emitted.append(self._emit_mir_cfg_function(function, mir_function))
                continue
            if (
                mir_function is not None
                and self._mir_calls_collections_eligible(function, mir_function)
            ):
                emitted.append(self._emit_mir_cfg_function(function, mir_function))
                continue
            if (
                mir_function is not None
                and self._mir_ownership_ffi_eligible(function, mir_function)
            ):
                emitted.append(self._emit_mir_cfg_function(function, mir_function))
                continue
            raise RepresentationCBackendError(
                f"function is not supported by structured MIR emitter: {function.name}"
            )
        return "\n\n".join(emitted)

    def _canonical_nominal(self, type_name: str) -> str:
        descriptor = self.descriptors.get(type_name)
        if descriptor is not None:
            identity = descriptor.source_type_identity
            if identity is not None:
                aliases = [
                    name
                    for name, candidate in self.descriptors.items()
                    if candidate.source_type_identity == identity
                ]
                if aliases:
                    return min(aliases, key=lambda name: (len(name), name))
            return type_name
        matches = [
            name for name in self.descriptors
            if name.rsplit("__", 1)[-1].rsplit(".", 1)[-1] == type_name
        ]
        return matches[0] if len(matches) == 1 else type_name

    def _result_parts(self, type_name: str | None) -> tuple[str, str] | None:
        parts = _result_types(type_name)
        if parts is not None:
            return tuple(self._canonical_nominal(item) for item in parts)
        descriptor = self.descriptors.get(type_name or "")
        if descriptor is None or descriptor.kind != "enum":
            return None
        variants = {name: payload for name, payload, _ in descriptor.variants}
        ok_type = variants.get("Ok")
        error_type = variants.get("Err")
        if ok_type is None or error_type is None:
            return None
        return ok_type, error_type
    def _error_value(
        self,
        error_type: str,
        code: str = "merlo_file_error",
        *,
        text_payload: str | None = None,
        integer_payload: str = "merlo_file_error_line",
    ) -> str:
        descriptor = self.descriptors.get(error_type)
        if descriptor is None or descriptor.kind != "enum":
            raise RepresentationCBackendError(f"Result error type is not an enum: {error_type}")
        variants = tuple(descriptor.variants)
        if not variants:
            raise RepresentationCBackendError(f"Result error enum has no variants: {error_type}")
        names = {name for name, _payload, _ in variants}
        by_code = (
            (1, ("NotFound", "FileOpen", "ReadFailure", "ConnectionRefused")),
            (2, ("System", "ReadFailure", "IoFailure")),
            (3, ("InvalidUtf8", "InvalidData")),
            (4, ("InvalidPath", "PermissionDenied", "CapabilityDenied")),
            (5, ("Closed", "System")),
        )
        selected: dict[int, str] = {}
        for number, candidates in by_code:
            selected[number] = next((name for name in candidates if name in names), variants[0][0])
        def constructor(name: str, payload: str | None) -> str:
            if payload is not None:
                payload_descriptor = self.descriptors.get(payload)
                if payload_descriptor is not None and payload_descriptor.kind == "text":
                    if text_payload is None:
                        raise RepresentationCBackendError(
                            f"{error_type}.{name} requires a text error payload"
                        )
                    argument = (
                        f"merlo_text_clone((const MerloText *){text_payload})"
                    )
                elif payload == "UInt64":
                    argument = integer_payload
                else:
                    raise RepresentationCBackendError(
                        f"{error_type}.{name} error payload lowering is unsupported"
                    )
                return f"merlo_make_{_identifier(error_type)}_{name}({argument})"
            if all(item[1] is None for item in variants):
                return f"MERLO_{_identifier(error_type)}_{name}"
            return f"merlo_make_{_identifier(error_type)}_{name}()"
        values = {
            number: constructor(name, next(payload for variant, payload, _ in variants if variant == name))
            for number, name in selected.items()
        }
        fallback = constructor(variants[0][0], variants[0][1])
        result = fallback
        for number in sorted(values, reverse=True):
            result = f"(({code}) == UINT32_C({number}) ? {values[number]} : {result})"
        return result

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

    def _static_contract_receiver(self, receiver: str) -> TypeConstructorId | None:
        if not receiver.isidentifier():
            return None
        return TypeConstructorId(receiver)

    def _zero_expression(self, type_name: str) -> str:
        descriptor = self.descriptors[type_name]
        if _is_owner(descriptor):
            return f"merlo_zero_{_identifier(type_name)}()"
        return f"({_c_name(type_name)}){{0}}"

    def _text_host(self, function: HIRFunction) -> str:
        if (
            tuple(parameter.type_name for parameter in function.parameters)
            != ("Text",)
            or function.return_type != "Text"
        ):
            raise RepresentationCBackendError(
                "Text host entry must accept and return exactly one Text"
            )
        parameter = function.parameters[0]
        argument = "&input" if self._parameter_is_pointer(parameter) else "input"
        release_input = (
            "    free(input.data);\n"
            if parameter.ownership in {"borrow", "borrow_mut"}
            else ""
        )
        return f'''static uint8_t *merlo_host_read_text(uint64_t *length) {{
    uint8_t *data = NULL;
    size_t used = 0;
    size_t capacity = 0;
    uint8_t chunk[4096];
    while (!feof(stdin)) {{
        size_t count = fread(chunk, 1, sizeof(chunk), stdin);
        if (ferror(stdin)) {{
            free(data);
            return NULL;
        }}
        if (count == 0) break;
        if (used > SIZE_MAX - count) {{
            free(data);
            return NULL;
        }}
        size_t required = used + count;
        if (required > capacity) {{
            size_t next = capacity == 0 ? 4096 : capacity;
            while (next < required) {{
                if (next > SIZE_MAX / 2) {{
                    next = required;
                    break;
                }}
                next *= 2;
            }}
            uint8_t *grown = (uint8_t *)realloc(data, next);
            if (grown == NULL) {{
                free(data);
                return NULL;
            }}
            data = grown;
            capacity = next;
        }}
        memcpy(data + used, chunk, count);
        used += count;
    }}
    *length = (uint64_t)used;
    return data;
}}

int main(int argc, char **argv) {{
{self._capability_initialization(function)}
    uint64_t length = 0;
    uint8_t *data = merlo_host_read_text(&length);
    if (data == NULL && length != 0) return 74;
    if (!merlo_valid_utf8(data, length)) {{
        free(data);
        fputs("InvalidUtf8\\n", stderr);
        return 74;
    }}
    MerloText input = {{ data, length }};
    if (data != NULL) {{
        ++merlo_allocations;
        ++merlo_text_allocations;
    }}
    MerloText result = merlo_fn_{function.name}({argument});
    if (result.length != 0) {{
        fwrite(result.data, 1, (size_t)result.length, stdout);
    }}
    merlo_drop_Text(&result);
{release_input}    return ferror(stdout) ? 74 : 0;
}}'''


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
            file_error_guard = '''    if (result.tag == UINT32_C(0) && merlo_file_error != 0) {
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
            lines.append("    merlo_runtime_argv = argv;")
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
        if (
            tuple(parameter.type_name for parameter in function.parameters)
            == ("Text",)
            and function.return_type == "Text"
        ):
            return self._text_host(function)
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
}""".replace(
            "__MERLO_CAPS__",
            self._capability_initialization(function),
        )

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
            ("network.http", "fn(...) -> Unit", "scoped host request", "network.http", False, False, True, "O(1)", "merlo_network_http_request"),
            ("process.args", "fn() -> UInt64", "returns argument count", "process.args", False, False, True, "O(1)", "merlo_process_args_count"),
            ("Text.from_bytes", "fn(Borrow[Bytes], start: UInt64, end: UInt64) -> Text", "returns unique Text", "memory", True, True, True, "O(n)", "merlo_text_from_bytes"),
            ("TextBuilder.append", "fn(BorrowMut[TextBuilder], scalar: UInt64) -> Unit", "unique mutable borrow", "memory", True, True, True, "amortized O(1)", "merlo_text_builder_append_*"),
            ("TextBuilder.finish", "fn(TextBuilder) -> Text", "consumes builder; transfers buffer", "memory", False, False, False, "O(1)", "merlo_text_builder_finish"),
        ]
        normalized_entries = []
        for entry in entries:
            signature = intrinsic_signature(entry[0])
            receiver, separator, method = entry[0].partition(".")
            method_signature = (
                self.contract_graph.static_method(
                    self._static_contract_receiver(receiver),
                    method,
                )
                if separator
                else None
            )
            if signature is None and method_signature is None:
                normalized_entries.append(entry)
                continue
            contract = signature or method_signature
            assert contract is not None
            parameter_text = ", ".join(contract.parameters)
            if signature is not None:
                normalized_entries.append(
                    (
                        entry[0],
                        f"fn({parameter_text}) -> {signature.result_type}",
                        entry[2],
                        signature.effect,
                        entry[4],
                        entry[5],
                        entry[6],
                        entry[7],
                        entry[8],
                    )
                )
                continue
            assert method_signature is not None
            normalized_entries.append(
                (
                    entry[0],
                    f"fn({parameter_text}) -> {method_signature.result_type}",
                    (
                        f"parameters={method_signature.parameter_ownership}; "
                        f"result={method_signature.result_ownership}"
                    ),
                    entry[3],
                    method_signature.result_ownership == "owned",
                    method_signature.result_ownership == "owned"
                    or any(
                        item in {"Text", "TextView", "Bytes", "BytesView"}
                        for item in method_signature.parameters
                    ),
                    "may_fail" in method_signature.effects,
                    entry[7],
                    CONTRACT_GRAPH.abi_lowering(entry[0]) or entry[8],
                )
            )
        entries = normalized_entries
        implementations = {
            name: lowering
            for name in INTRINSIC_SIGNATURES
            if (lowering := CONTRACT_GRAPH.abi_lowering(name)) is not None
        }
        implementations.update(
            {
                f"{receiver}.{method}": (
                    signature.abi_lowering
                    or signature.representation_lowering
                )
                for (receiver, method), signature in CONTRACT_GRAPH.methods.items()
                if signature.static
                and (
                    signature.abi_lowering is not None
                    or signature.representation_lowering is not None
                )
            }
        )
        present = {entry[0] for entry in normalized_entries}
        for name, intrinsic in INTRINSIC_SIGNATURES.items():
            if name in present:
                continue
            parameter_text = ", ".join(intrinsic.parameters)
            linear = (
                intrinsic.result_ownership == "owned"
                or any(
                    item in {"Text", "TextView", "Bytes", "BytesView"}
                    for item in intrinsic.parameters
                )
            )
            normalized_entries.append(
                (
                    name,
                    f"fn({parameter_text}) -> {intrinsic.result_type}",
                    (
                        f"parameters={intrinsic.parameter_ownership}; "
                        f"result={intrinsic.result_ownership}"
                    ),
                    intrinsic.effect,
                    intrinsic.result_ownership == "owned",
                    linear,
                    intrinsic.result_type.startswith("Result["),
                    "O(n)" if linear else "O(1)",
                    implementations[name],
                )
            )
        present = {entry[0] for entry in normalized_entries}
        for (receiver, method), contract in CONTRACT_GRAPH.methods.items():
            name = f"{receiver}.{method}"
            if not contract.static or name in present:
                continue
            parameter_text = ", ".join(contract.parameters)
            linear = (
                contract.result_ownership == "owned"
                or any(
                    item in {"Text", "TextView", "Bytes", "BytesView"}
                    for item in contract.parameters
                )
            )
            normalized_entries.append(
                (
                    name,
                    f"fn({parameter_text}) -> {contract.result_type}",
                    (
                        f"parameters={contract.parameter_ownership}; "
                        f"result={contract.result_ownership}"
                    ),
                    "memory",
                    "allocate" in contract.effects,
                    "copy" in contract.effects,
                    "may_fail" in contract.effects,
                    "O(n)" if "copy" in contract.effects else "O(1)",
                    implementations[name],
                )
            )
        entries = normalized_entries
        effect_primitives = frozenset(signature.effect for signature in INTRINSIC_SIGNATURES.values())
        lines = source.splitlines()
        result = []
        for name, signature, ownership, effect, allocates, copies, may_fail, complexity, implementation in entries:
            if effect in effect_primitives and effect not in self.used_effects:
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
    holes = tuple(
        node
        for function in hir.functions
        for node in function.walk()
        if node.kind == "TypedHole"
    )
    if holes:
        lines = [
            (
                '#error "TypedHoleNotExecutable:'
                f"{node.attribute_map.get('hole_id')}:"
                f'{node.type_name}"'
            )
            for node in holes
        ]
        source = "\n".join(lines) + "\n"
        return GeneratedC(
            source,
            hashlib.sha256(source.encode()).hexdigest(),
            (),
            len(lines),
            0,
            (),
        )
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
